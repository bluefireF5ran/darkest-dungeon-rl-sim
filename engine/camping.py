"""Camping — the between-combat recovery the simulator was missing.

Camping was listed as deliberately out of scope until the eval put the whole
remaining gap in the long band: 37.2% SUCCESS against short's 80.7%, with the
failure mode being wipes (45.5%) at mean stress 117.5 versus an affliction
threshold of 100. The real game grants firewood by raid length — **0 short,
1 medium, 2 long** (user-confirmed) — so the simulator's short band already
matched the real game while its long band was missing every recovery the real
game gives. That gradient is the reason this came into scope; see CHANGELOG.md.

**Auto-resolved, not a policy decision.** The agent does not choose camp
skills: `Discrete(37)` is documented as fixed semantics and the observation
would have to grow to carry camp state. A fixed, sensible heuristic buys the
fidelity correction on its own and leaves a clean baseline to measure a
policy-controlled version against later, if it ever earns one.

Everything numeric is real data: skills and costs from
`raid/camping/default.camping_skills.json` (64 of them), the point budget and
ambush chance from `shared/rules.json`.

**`camp_rest_point_threshold` (6) is parsed but deliberately unused.** No
shipped rule states how much HP a generic "rest" restores, and inventing one
would be a simulator convention masquerading as game data. All recovery here
comes from camp skills, which is where the real game's healing demonstrably
lives (`first_aid`, `bandage`, `wound_care`, `encourage`, ...).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional, Sequence

from data.types import CampingSkillDefinition, CampingSkillEffect, DataStore
from .combatant import CombatantState

# Effects this resolves. The rest of the real vocabulary is genuinely
# inapplicable here rather than skipped for convenience: `loot` and
# `remove_disease` have no simulator counterpart (no loot, no disease system),
# and `reduce_torch`/`camp_restore_torch` matter only once torch decay over
# travel exists.
#
# `remove_deaths_door_recovery_buffs` was in that list until 2026-08-23, on the
# grounds that "the engine's own mortality tiers handle it". They do not: every
# write to `mortality_tier` RAISES it (`_apply_recovery_tier` guards with
# `<=`), it is carried between combats in `CarryOverHeroState`, and nothing
# anywhere lowered it. So one Death's Door visit cost a hero -2 ACC / -5% DMG /
# -1 SPD for the remainder of the expedition, permanently. 94 equipped slots
# across `stand_tall` (60) and `dark_ritual` (34) were buying nothing.
HEAL_EFFECTS = frozenset({
    "stress_heal_amount",
    "stress_damage_amount",
    "health_heal_max_health_percent",
    "health_damage_max_health_percent",
    "remove_bleeding",
    "remove_poison",
    "remove_deaths_door_recovery_buffs",
})

# Scoring weights for the spend heuristic. Simulator convention — they order
# candidate skills, they are not game data.
#
# Recovery is scored as a FRACTION OF THE POOL IT PROTECTS, never in raw
# points. Stress runs 0-200 and HP runs ~20-45, so comparing them point for
# point is not close to neutral: it made the heuristic spend a whole campfire
# on stress and leave a Bounty Hunter at 8/30 HP untouched, one hit from
# Death's Door, while a first_aid sat unused in his own kit.
# Recovery is also URGENCY-weighted: topping up a hero at 90% HP is worth far
# less than the same heal on one at 18%, who is a single hit from Death's Door.
# Without this the flat version bought a 5-point stat buff and left a Bounty
# Hunter at 8/45 HP with an unused 2-point first_aid in his own kit.
STRESS_POOL = 100.0       # the affliction threshold — the stress that matters
W_STRESS = 1.0
W_HP = 2.0                # HP loss kills outright; stress kills at a threshold
URGENCY_MAX = 2.0         # multiplier at 0% of the pool, falling to 1.0 at full
W_DOT_CLEARED = 0.15      # per bleed/blight instance carried into the next fight
W_BUFF = 0.05             # a buff that will apply to the next combat
W_AMBUSH_REDUCTION = 0.60  # avoiding a whole extra combat is worth a lot
# Clearing a mortality tier. Priced between a DoT instance and a campfire's
# worth of healing: the debuff is small per combat (-2 ACC, -5% DMG, -1 SPD)
# but it is PERMANENT for the expedition, so on a long run it is paid 10+ more
# times. Convention, like every other weight here.
W_MORTALITY_CLEARED = 0.50


def _urgency(remaining_fraction: float) -> float:
    """1.0 when the pool is full, URGENCY_MAX when it is empty."""
    return 1.0 + (URGENCY_MAX - 1.0) * (1.0 - max(0.0, min(1.0, remaining_fraction)))


# **The camp MEAL is worth taking even at full HP**, because a feast is the
# only stress relief in the game that costs no respite points. That is what
# makes this a real decision rather than a top-up: 8 food buys -10 stress on
# every living hero AND 25% of their max HP, while the 12-point skill budget
# stays entirely free for everything else.
#
# The party still refuses to gorge when there is nothing to gain. A hero is
# "worth feeding" at under this fraction of max HP -- the same reading
# `EAT_TOPUP_FRACTION` makes for eating between rooms, and the same declared
# convention, because the real trigger is a player looking at four HP bars.
MEAL_TOPUP_FRACTION = 0.90
# ...or carrying at least this much stress, which no amount of HP can excuse
# leaving on the table when the relief is free of respite points.
MEAL_STRESS_FLOOR = 10.0


@dataclass
class CampResult:
    skills_used: list[tuple[int, str]] = field(default_factory=list)  # (hero index, skill id)
    points_spent: int = 0
    ambush: bool = False
    granted_buffs: dict[int, list[str]] = field(default_factory=dict)  # hero index -> buff ids
    log: list[str] = field(default_factory=list)
    meal: Optional[str] = None
    food_spent: int = 0


def _hero_tags(store: DataStore, hero: CombatantState) -> set[str]:
    hd = store.heroes.get(hero.definition_id)
    return set(hd.tags) if hd is not None else set()


def _requirements_met(effect: CampingSkillEffect, target: CombatantState,
                      store: DataStore) -> bool:
    """Only the four requirement strings the real data uses are understood:
    afflicted, religious, not_religious, has_deaths_door_recovery_buffs. An
    unrecognised requirement blocks the effect rather than firing it — a camp
    skill doing something it shouldn't is a worse failure than one doing
    nothing."""
    tags = _hero_tags(store, target)
    for req in effect.requirements:
        if req == "afflicted":
            if not target.is_afflicted:
                return False
        elif req == "religious":
            if "religious" not in tags:
                return False
        elif req == "not_religious":
            if "religious" in tags:
                return False
        elif req == "has_deaths_door_recovery_buffs":
            if target.mortality_tier in ("", "none"):
                return False
        else:
            return False
    return True


# Which side of the ledger a camp `buff` effect lands on, and whether the
# hero it lands on wants it for ATTACKING or for SURVIVING. Read off the buff's
# own `stat_type`, never guessed: `_effect_value` used to score every buff a
# flat +W_BUFF regardless of sign, so the Runaway's `play_with_fire`
# (+10% damage RECEIVED) and `cauterize` (-20% debuff resist) were scored as
# benefits and could be bought with points that had somewhere better to go.
#
# `_RECEIVED_IS_BAD` is the same rule the observation uses for its stat
# buckets: on a "received" axis a POSITIVE amount is worse for the receiver.
_RECEIVED_IS_BAD = frozenset({
    "stress_dmg_received_percent",
    "damage_received_percent",
})
# Stats a hero uses on its own turn. Everything else -- dodge, prot, the
# resistances, heal-received, tag blocks -- is survivability.
_OFFENSIVE_SUB_TYPES = frozenset({
    "attack_rating", "crit_chance", "damage_low", "damage_high", "speed_rating",
})


def _buff_nature(effect: CampingSkillEffect, store: DataStore) -> tuple[bool, bool]:
    """`(is_beneficial, is_offensive)` for a camp `buff` effect.

    Falls back to `(True, False)` for a buff id the store does not carry, which
    keeps an unresolvable buff behaving exactly as it did before this existed.
    """
    bd = store.buffs.get(effect.stat_sub_type)
    if bd is None:
        return True, False
    amount = float(getattr(bd, "amount", 0.0) or 0.0)
    beneficial = amount < 0.0 if bd.stat_type in _RECEIVED_IS_BAD else amount >= 0.0
    return beneficial, bd.stat_sub_type in _OFFENSIVE_SUB_TYPES


def _targets(effect: CampingSkillEffect, heroes: Sequence[CombatantState],
             caster_idx: int) -> list[int]:
    sel = effect.target
    if sel == "self":
        return [caster_idx]
    if sel == "party":
        return [i for i, h in enumerate(heroes) if h.is_alive()]
    if sel == "party_other":
        return [i for i, h in enumerate(heroes) if h.is_alive() and i != caster_idx]
    # "individual" is the player's pick in the real game; the heuristic picks
    # the neediest living hero at apply time (see _pick_individual).
    return [i for i, h in enumerate(heroes) if h.is_alive()]


def _pick_individual(effect: CampingSkillEffect, heroes: Sequence[CombatantState],
                     candidates: Sequence[int],
                     store: Optional[DataStore] = None) -> list[int]:
    if not candidates:
        return []
    if effect.stat_type == "buff" and store is not None:
        # Until 2026-08-23 this fell through to `candidates[0]`, the frontmost
        # living hero. That is defensible for a defensive buff and arbitrary
        # for an offensive one, and actively wrong for the handful of camp
        # "buffs" that are really debuffs.
        #
        # DECLARED CONVENTION, and only the ROUTING is invented -- which side
        # the buff falls on is read off its own `stat_type`:
        #   * a harmful one goes to whoever can best absorb it (highest HP
        #     fraction), mirroring the existing `stress_damage_amount` rule;
        #   * a survivability buff goes to the hero closest to dying;
        #   * an offensive buff goes to the biggest hitter, since a percentage
        #     of a larger number is worth more.
        # Falsifier: watch where a real player sends Bless or Tiger's Eye.
        beneficial, offensive = _buff_nature(effect, store)
        if not beneficial:
            return [max(candidates,
                        key=lambda i: heroes[i].hp / max(heroes[i].max_hp, 1))]
        if offensive:
            return [max(candidates, key=lambda i: heroes[i].dmg_max)]
        return [min(candidates,
                    key=lambda i: heroes[i].hp / max(heroes[i].max_hp, 1))]
    if effect.stat_type in ("stress_heal_amount",):
        return [max(candidates, key=lambda i: heroes[i].stress)]
    if effect.stat_type in ("health_heal_max_health_percent",):
        return [min(candidates, key=lambda i: heroes[i].hp / max(heroes[i].max_hp, 1))]
    if effect.stat_type == "remove_bleeding":
        return [max(candidates, key=lambda i: len(heroes[i].bleeds))]
    if effect.stat_type == "remove_poison":
        return [max(candidates, key=lambda i: len(heroes[i].blights))]
    if effect.stat_type == "remove_deaths_door_recovery_buffs":
        # Heavier tier first; a hero with no tier scores 0 in _effect_value and
        # the whole candidate is dropped there.
        rank = {"heart_attack": 2, "mortality": 1}
        return [max(candidates, key=lambda i: rank.get(heroes[i].mortality_tier, 0))]
    # Harmful "individual" effects land on whoever can best absorb them.
    if effect.stat_type == "stress_damage_amount":
        return [min(candidates, key=lambda i: heroes[i].stress)]
    return [candidates[0]]


def _effect_value(effect: CampingSkillEffect, heroes: Sequence[CombatantState],
                  target_idxs: Sequence[int],
                  store: Optional[DataStore] = None) -> float:
    """How much good this effect would actually do right now — measured in
    recovery that is not wasted. Healing 25 stress off a hero sitting at 4
    scores 4, not 25, which is what stops the heuristic burning its budget on
    a rested party."""
    total = 0.0
    for i in target_idxs:
        h = heroes[i]
        if effect.stat_type == "stress_heal_amount":
            # "Remaining" for stress is inverted: high stress IS the danger.
            urgency = _urgency(1.0 - min(1.0, h.stress / STRESS_POOL))
            total += (min(effect.amount, h.stress) / STRESS_POOL) * W_STRESS * urgency
        elif effect.stat_type == "stress_damage_amount":
            total -= (effect.amount / STRESS_POOL) * W_STRESS
        elif effect.stat_type == "health_heal_max_health_percent":
            pool = max(h.max_hp, 1)
            urgency = _urgency(h.hp / pool)
            total += (min(effect.amount * pool, pool - h.hp) / pool) * W_HP * urgency
        elif effect.stat_type == "health_damage_max_health_percent":
            total -= effect.amount * W_HP
        elif effect.stat_type == "remove_deaths_door_recovery_buffs":
            # Worthless on a hero who never went down, which is most of them,
            # so this is gated on the tier rather than granted flat.
            if h.mortality_tier not in ("", "none"):
                total += W_MORTALITY_CLEARED
        elif effect.stat_type == "remove_bleeding":
            total += len(h.bleeds) * W_DOT_CLEARED
        elif effect.stat_type == "remove_poison":
            total += len(h.blights) * W_DOT_CLEARED
        elif effect.stat_type == "buff":
            # SIGNED: a camp "buff" can be a debuff (the Runaway's two), and a
            # flat positive made those look worth buying.
            beneficial = True if store is None else _buff_nature(effect, store)[0]
            total += W_BUFF if beneficial else -W_BUFF
        elif effect.stat_type == "reduce_ambush_chance":
            total += W_AMBUSH_REDUCTION * effect.amount
    return total * effect.chance


def _apply_effect(effect: CampingSkillEffect, heroes: Sequence[CombatantState],
                  target_idxs: Sequence[int], store: DataStore,
                  apply_buff: Optional[Callable], result: CampResult,
                  clear_mortality: Optional[Callable] = None,
                  rng=None) -> None:
    """Apply one camp effect to its targets, rolling `.chance` per target.

    THE CHANCE IS ROLLED HERE, and until 2026-08-23 it was not — it reached
    `_effect_value` (which correctly scales the expected value by it) and
    nothing else, so every effect fired unconditionally. Ten shipped effects
    carry a chance below 1.0, across 131 equipped comp slots, and the error
    runs in BOTH directions:

    * `gallows_humor` is a gamble — 75% that an ally is relieved of 20 stress,
      25% that they gain 10. Both fired every time, turning a mostly-good
      skill into a guaranteed net -10 for each ally.
    * `abandon_hope` inflicted 10 AND 5 stress on every ally where its expected
      cost is 7.5.
    * `quarantine` healed 20 AND 15 where it should average 17.5.

    ONE ROLL PER TARGET, and the two rows of a skill roll INDEPENDENTLY. That
    is the literal reading of the data: nothing marks `gallows_humor`'s heal
    and its stress damage as exclusive, so a single ally can legitimately get
    both or neither. Declared rather than assumed — the alternative reading is
    one roll choosing between them, which would need a marker the files do not
    carry.

    NO DRAW IS TAKEN AT CHANCE >= 1.0, the `dot_irresistible` precedent: 185 of
    the 195 shipped camp effects are certainties, and rolling against them
    would shift the RNG stream for every party without changing one outcome.
    """
    for i in target_idxs:
        h = heroes[i]
        if effect.chance < 1.0:
            if rng is None or rng.random() >= effect.chance:
                continue
        if effect.stat_type == "stress_heal_amount":
            before = h.stress
            h.stress = max(0, h.stress - int(effect.amount))
            # Camp stress relief is the main way a raid un-breaks a hero, so
            # this is the site that matters most for the cure -- see
            # `CombatEngine._maybe_clear_overstress`. Done by flag here rather
            # than by calling the engine: `resolve_camp` takes combatants and a
            # store, not an engine, and the buff reversal happens when the next
            # combat rebuilds the party from `carry_over`.
            if h.stress <= 0 and (getattr(h, "is_afflicted", False)
                                  or getattr(h, "is_virtuous", False)):
                h.is_afflicted = False
                h.is_virtuous = False
                h.affliction_id = None
                h.virtue_id = None
            if before != h.stress:
                result.log.append(f"{h.name} relieved of {before - h.stress} stress")
        elif effect.stat_type == "stress_damage_amount":
            h.stress = h.stress + int(effect.amount)
            result.log.append(f"{h.name} gains {int(effect.amount)} stress at camp")
        elif effect.stat_type == "health_heal_max_health_percent":
            before = h.hp
            h.hp = min(h.max_hp, h.hp + int(round(effect.amount * h.max_hp)))
            if before != h.hp:
                result.log.append(f"{h.name} heals {h.hp - before}")
        elif effect.stat_type == "health_damage_max_health_percent":
            h.hp = max(1, h.hp - int(round(effect.amount * h.max_hp)))
        elif effect.stat_type == "remove_bleeding":
            if h.bleeds:
                h.bleeds.clear()
                result.log.append(f"{h.name}'s bleeding is treated")
        elif effect.stat_type == "remove_poison":
            if h.blights:
                h.blights.clear()
                result.log.append(f"{h.name}'s blight is treated")
        elif effect.stat_type == "remove_deaths_door_recovery_buffs":
            if clear_mortality is not None and clear_mortality(h):
                result.log.append(f"{h.name} shakes off the mark of mortality")
        elif effect.stat_type == "buff":
            buff_id = effect.stat_sub_type
            bd = store.buffs.get(buff_id)
            if bd is not None and apply_buff is not None:
                apply_buff(h, bd)
                result.granted_buffs.setdefault(i, []).append(buff_id)


def choose_meal(rules, heroes: Sequence[CombatantState], food: int):
    """Which meal the party takes, and what it costs. See `meals_table`.

    Returns `(type, food_cost, healing_fraction, stress_delta)`.

    **Never starve when anything at all is affordable.** `none` is -20% max HP
    and +15 stress on every hero -- four times the damage a `full` meal heals
    and more stress than a campfire's whole skill budget usually removes -- so
    the two food a `half` costs is never the worse trade.

    Above that the party takes the richest meal it can afford AND use:
    a feast on a party at full HP and no stress is six food spent on nothing,
    and food is the resource the hunger check comes back for.
    """
    table = getattr(rules, "meals_table", ()) or ()
    alive = [h for h in heroes if h.is_alive()]
    if not table or not alive:
        return None
    wants = any(h.hp < MEAL_TOPUP_FRACTION * h.max_hp for h in alive) or \
        any(h.stress >= MEAL_STRESS_FLOOR for h in alive)
    for meal_type, per_hero, healing, stress in table:      # richest first
        cost = int(round(per_hero * len(alive)))
        if cost > food:
            continue
        # A meal that heals or calms is only taken when somebody needs it;
        # `half` and `none` cost nothing to take and are the fallbacks.
        if (healing > 0 or stress < 0) and not wants:
            continue
        return (meal_type, cost, healing, stress)
    return None


def eat_at_camp(rules, heroes: Sequence[CombatantState], food: int,
                result: "CampResult") -> int:
    """Apply the camp meal. Returns the food spent.

    The FOOD phase happens before the skill phase in the real game -- the
    Shieldbreaker's nightmare bark fires *"after the party has eaten, but
    before Camping Skills have been used"* -- and the order matters here for
    the same reason it matters there: the skill phase's own value function
    reads HP and stress, so feeding first is what stops it spending respite
    points healing damage the meal was about to remove.
    """
    picked = choose_meal(rules, heroes, food)
    if picked is None:
        return 0
    meal_type, cost, healing, stress = picked
    for hero in heroes:
        if not hero.is_alive():
            continue
        if healing:
            hero.hp = max(1, min(hero.max_hp,
                                 hero.hp + int(round(healing * hero.max_hp))))
        if stress:
            hero.stress = max(0, hero.stress + int(stress))
    result.log.append(
        f"the party eats a {meal_type} meal ({cost} food)"
        + (f", healing {healing:+.0%}" if healing else "")
        + (f" and {abs(int(stress))} stress {'off' if stress < 0 else 'on'}"
           if stress else ""))
    result.meal = meal_type
    result.food_spent = cost
    return cost


def resolve_camp(rng, store: DataStore, heroes: Sequence[CombatantState],
                 camp_skill_ids: Sequence[Sequence[str]],
                 apply_buff: Optional[Callable] = None,
                 clear_mortality: Optional[Callable] = None,
                 extra_points: int = 0,
                 food: Optional[int] = None) -> CampResult:
    """Spend one campfire's point budget on the party's own equipped camp skills.

    `camp_skill_ids` is **positional** against `heroes` — never a hero_id-keyed
    dict. 37 of the real comps field duplicate classes with genuinely different
    loadouts ("African Vapors" has two Antiquarians whose camp skills differ),
    and a dict collapses them onto whichever was inserted last. That exact bug
    has been found twice in this repo already.

    Greedy by value-per-point: a skill is only worth its cost if the recovery
    it delivers is not wasted, so a healthy party leaves points unspent rather
    than dumping heals into full-HP heroes.
    """
    result = CampResult()
    rules = store.rules
    # 1. THE FOOD PHASE, before the skills -- see `eat_at_camp`.
    #
    # **`None` means "no food phase", and `0` means "out of rations".** They
    # are different outcomes and the difference is 20% of every hero's max HP
    # plus 15 stress, so the default has to be the one that changes nothing:
    # every caller that predates the phase -- `bridge/camp.py`, and every test
    # that camps a party directly -- passes nothing and gets the behaviour it
    # had. A caller that genuinely has an empty pack passes 0 and starves.
    if food is not None:
        eat_at_camp(rules, heroes, int(food), result)
    # `extra_points` is the Outsiders Bonfire district (+2). Defaults to 0, so
    # every existing caller -- bridge/camp.py included -- is unchanged.
    budget = int(rules.camp_start_camping_points) + int(extra_points)
    ambush_chance = float(rules.ambush_camping_base_chance)
    uses_left: dict[tuple[int, str], int] = {}

    while budget > 0:
        best = None  # (value_per_point, hero_idx, skill, plan)
        for idx, hero in enumerate(heroes):
            if not hero.is_alive() or idx >= len(camp_skill_ids):
                continue
            for skill_id in camp_skill_ids[idx]:
                skill: CampingSkillDefinition | None = store.camping_skills.get(skill_id)
                if skill is None or skill.cost > budget:
                    continue
                key = (idx, skill_id)
                if uses_left.get(key, skill.use_limit or 1) <= 0:
                    continue
                if skill.hero_classes and hero.definition_id not in skill.hero_classes:
                    continue

                plan: list[tuple[CampingSkillEffect, list[int]]] = []
                value = 0.0
                for effect in skill.effects:
                    if effect.stat_type not in HEAL_EFFECTS and effect.stat_type not in ("buff", "reduce_ambush_chance"):
                        continue
                    tgts = _targets(effect, heroes, idx)
                    if effect.target == "individual":
                        tgts = _pick_individual(effect, heroes, tgts, store)
                    tgts = [t for t in tgts if _requirements_met(effect, heroes[t], store)]
                    if not tgts:
                        continue
                    value += _effect_value(effect, heroes, tgts, store)
                    plan.append((effect, tgts))

                if value <= 0 or not plan:
                    continue
                score = value / max(skill.cost, 1)
                if best is None or score > best[0]:
                    best = (score, idx, skill, plan)

        if best is None:
            break

        _, idx, skill, plan = best
        for effect, tgts in plan:
            if effect.stat_type == "reduce_ambush_chance":
                ambush_chance = max(0.0, ambush_chance - effect.amount)
                continue
            _apply_effect(effect, heroes, tgts, store, apply_buff, result,
                          clear_mortality, rng)
        budget -= skill.cost
        result.points_spent += skill.cost
        result.skills_used.append((idx, skill.id))
        uses_left[(idx, skill.id)] = uses_left.get((idx, skill.id), skill.use_limit or 1) - 1

    result.ambush = ambush_chance > 0.0 and rng.random() < ambush_chance
    if result.ambush:
        result.log.append("The party is ambushed at camp!")
    return result
