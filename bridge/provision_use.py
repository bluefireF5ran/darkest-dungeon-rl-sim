"""When to USE a provision -- each one is a trigger, never a whim.

Fran, 2026-09-25: *"we shouldn't use any provision randomly, each is a trigger
of something"*. The triggers are his; the numbers they rest on are shipped:

  food        a hero under FOOD_HP_TRIGGER (30%) of max HP eats until back
              over it or FULL. `rules.json`: `provision_hp_heal` 0.05 (5% of
              max HP a portion) and `max_provisions_before_full` 4.
  bandage     a bleeding hero.        (`.cure_bleed`)
  antivenom   a blighted hero.        (`.cure_poison`)
  laudanum    a hero under Horror.    (`.clearDotStress`)
  holy_water  in a fight, on a hero an enemy that can REACH their rank threatens
              with heavy bleed / blight / disease. `holy_water` is +33% resist
              to blight, bleed, disease and debuff for 3 rounds
              (`base.buffs.json`).
  dog_treats  in a fight, on the Houndmaster, when +50% damage for 3 rounds
              (`base.effects.darkest`) is what turns the hit into a kill.

**The middle path between curios and combat is a RESERVE.** `lootplan`
already assigns the pack across the curios still on the map; what it has
earmarked is `reserve`, and a combat or corridor use only ever spends the
surplus above it. Food keeps a reserve for the hunger checks ahead too.

Pure decisions over plain records, so it is tested without a game. The
reader (`heroes_from_engine`) and the hands live elsewhere.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Mapping, Optional, Sequence

#: A hero below this fraction of max HP is fed (Fran).
FOOD_HP_TRIGGER = 0.30
#: `rules.json` `provision_hp_heal` and `max_provisions_before_full`; the
#: reader passes the parsed values, these are the shipped ones.
FOOD_HEAL_FRACTION = 0.05
MAX_FOOD_BEFORE_FULL = 4
#: Expected DoT damage (plus disease, weighted) an enemy line can put on a
#: hero's rank in one round before holy water is worth a flask. A declared
#: convention: one strong bleed (3 x 3 at 100%) is 9, a Weak Blight 1 is 3.
HOLY_WATER_THREAT = 6.0
#: What a 33% disease chance counts as in that sum -- a disease outlives the
#: raid, so it is priced like a full-strength DoT.
DISEASE_WEIGHT = 9.0
DOG_TREAT_DAMAGE = 1.5


@dataclass(frozen=True)
class HeroNeed:
    guid: int
    hero_class: str
    hp: float
    max_hp: float
    rank: int = 1                   # 1 = front
    bleeding: bool = False
    blighted: bool = False
    horror: bool = False
    eaten: int = 0                  # portions since the last reset of "full"
    holy_water_up: bool = False     # already carrying the resist buffs


@dataclass(frozen=True)
class Use:
    guid: int
    hero_class: str
    item: str
    why: str

    def line(self) -> str:
        return f"USE     {self.item} on {self.hero_class} - {self.why}"


def _spare(pack: Mapping[str, int], reserve: Mapping[str, int], item: str,
           spent: Mapping[str, int]) -> int:
    return max(0, int(pack.get(item, 0)) - int(reserve.get(item, 0))
               - int(spent.get(item, 0)))


def between_fights(heroes: Iterable[HeroNeed], pack: Mapping[str, int],
                   reserve: Optional[Mapping[str, int]] = None, *,
                   heal_fraction: float = FOOD_HEAL_FRACTION,
                   max_full: int = MAX_FOOD_BEFORE_FULL) -> list[Use]:
    """Cures and meals for the corridor. Every use names its trigger."""
    reserve = reserve or {}
    spent: dict[str, int] = {}
    out: list[Use] = []

    def take(h: HeroNeed, item: str, why: str) -> bool:
        if _spare(pack, reserve, item, spent) <= 0:
            return False
        spent[item] = spent.get(item, 0) + 1
        out.append(Use(h.guid, h.hero_class, item, why))
        return True

    for h in heroes:
        if h.hp <= 0:
            continue
        if h.bleeding:
            take(h, "bandage", "bleeding")
        if h.blighted:
            take(h, "antivenom", "blighted")
        if h.horror:
            take(h, "laudanum", "under Horror")
        if h.max_hp > 0 and h.hp < FOOD_HP_TRIGGER * h.max_hp:
            short = FOOD_HP_TRIGGER * h.max_hp - h.hp
            want = math.ceil(short / max(1e-9, heal_fraction * h.max_hp))
            room = max(0, max_full - h.eaten)
            for _ in range(min(want, room)):
                if not take(h, "food", f"{h.hp:.0f}/{h.max_hp:.0f} HP, under "
                                        f"{FOOD_HP_TRIGGER:.0%}"):
                    break
    return out


def dot_threat(rank: int, enemies: Sequence, store) -> float:
    """Expected bleed + blight damage (and weighted disease) the living enemy
    line can land on a hero standing at `rank` with one skill each.

    `enemies` are engine combatants (`definition_id`, `position` 0-based,
    `is_alive()`); a skill counts when the enemy can launch it from where it
    stands and it can target `rank`. Per enemy the WORST such skill counts,
    since each acts once a round."""
    total = 0.0
    for e in enemies:
        alive = getattr(e, "is_alive", lambda: True)
        if not (alive() if callable(alive) else alive):
            continue
        definition = store.enemies.get(getattr(e, "definition_id", ""))
        if definition is None:
            continue
        pos = int(getattr(e, "position", 0)) + 1
        worst = 0.0
        for skill in definition.skills or []:
            lv = skill.levels.get(0) if getattr(skill, "levels", None) else None
            if lv is None or rank not in (lv.target or []) \
                    or (lv.launch and pos not in lv.launch):
                continue
            score = 0.0
            for name in list(lv.effects or []):
                eff = store.effects.get(name)
                if eff is None:
                    continue
                chance = float(eff.chance or 0.0)
                if eff.bleed_amount:
                    score += chance * eff.bleed_amount * (eff.bleed_rounds or 3)
                if eff.blight_amount:
                    score += chance * eff.blight_amount * (eff.blight_rounds or 3)
                if "disease" in name.lower():
                    score += chance * DISEASE_WEIGHT
            worst = max(worst, score)
        total += worst
    return total


def holy_water_now(hero: HeroNeed, threat: float, pack: Mapping[str, int],
                   reserve: Optional[Mapping[str, int]] = None) -> Optional[Use]:
    """Flask a hero the enemy line can badly DoT or disease, from the surplus."""
    if hero.holy_water_up or hero.hp <= 0 or threat < HOLY_WATER_THREAT:
        return None
    if _spare(pack, reserve or {}, "holy_water", {}) <= 0:
        return None
    return Use(hero.guid, hero.hero_class, "holy_water",
               f"enemy DoT/disease threat {threat:.1f} on rank {hero.rank}")


def dog_treat_now(hero: HeroNeed, dmg_min: float, dmg_max: float,
                  target_hp: float, pack: Mapping[str, int],
                  reserve: Optional[Mapping[str, int]] = None) -> Optional[Use]:
    """A treat when +50% damage is what makes the planned hit a kill: the
    hit would NOT reliably kill as it stands, and WOULD with the treat."""
    if hero.hero_class != "houndmaster" or target_hp <= 0:
        return None
    if _spare(pack, reserve or {}, "dog_treats", {}) <= 0:
        return None
    mean = (dmg_min + dmg_max) / 2.0
    if mean >= target_hp or mean * DOG_TREAT_DAMAGE < target_hp:
        return None
    return Use(hero.guid, hero.hero_class, "dog_treats",
               f"{mean:.1f} average damage becomes {mean * DOG_TREAT_DAMAGE:.1f} "
               f"against {target_hp:.0f} HP - the treat makes the kill")


def needs_from_engine(state, engine, eaten: Optional[Mapping[int, int]] = None
                      ) -> list[HeroNeed]:
    """`HeroNeed`s for the live party, from the engine `seed.py` builds off the
    save (max HP after gear and trinkets, DoTs from `buff_group`) paired with
    the live heroes for their roster guids. Paired in party order and CHECKED
    by class: a mismatch returns [] rather than feeding the wrong hero.

    Horror (`stress_dots`) is only as good as the save mapping behind it, and
    its `stat_type` is not yet mapped in `seed.py` -- so today it reads False,
    and laudanum is never spent on a guess."""
    eaten = eaten or {}
    live = list(getattr(state, "heroes", []) or [])
    sims = list(getattr(engine, "heroes", []) or [])
    if len(live) != len(sims) or any(
            getattr(lh, "hero_class", "") != getattr(sh, "definition_id", "")
            for lh, sh in zip(live, sims)):
        return []
    out = []
    for lh, sh in zip(live, sims):
        buffs = [getattr(b, "buff_id", "") for b in getattr(sh, "buffs", []) or []]
        out.append(HeroNeed(
            guid=int(lh.guid), hero_class=lh.hero_class,
            hp=float(sh.hp), max_hp=float(sh.max_hp),
            rank=int(getattr(sh, "position", 0)) + 1,
            # Out of combat `build_party_from_live` syncs no buffs, so the
            # DoTs are read off the live `buff_group` as well (seed.py's
            # SAVE_STAT_BLEED 5 / SAVE_STAT_BLIGHT 4).
            bleeding=bool(getattr(sh, "bleeds", None)) or 5 in live_dots(lh),
            blighted=bool(getattr(sh, "blights", None)) or 4 in live_dots(lh),
            horror=bool(getattr(sh, "stress_dots", None)),
            eaten=int(eaten.get(int(lh.guid), 0)),
            holy_water_up=any(b.startswith("holy_water") for b in buffs)))
    return out


def live_dots(hero) -> set:
    """The DoT stat_types on a live hero's buff_group with ticks left."""
    return {int(getattr(b, "stat_type", -1)) for b in getattr(hero, "buffs", []) or []
            if int(getattr(b, "duration", 0) or 0) > 0 and float(getattr(b, "amount", 0) or 0) > 0}


def room_reserve(loot, party_size: int, band: Optional[str],
                 remaining_fights: Optional[int]) -> dict:
    """What the corridor may NOT spend: every item `lootplan` earmarked for a
    curio still ahead, and food for the hunger checks the rest of the map
    holds (`hunger_per_fight` from the shipped map budgets)."""
    reserve: dict = {}
    for stop in getattr(loot, "stops", ()) or ():
        if getattr(stop, "action", "") in ("take", "defer") and getattr(stop, "item", ""):
            reserve[stop.item] = reserve.get(stop.item, 0) + 1
    if band and remaining_fights:
        try:
            from data.map_generator import hunger_per_fight   # noqa: PLC0415
            checks = math.ceil(hunger_per_fight(band) * int(remaining_fights))
            reserve["food"] = reserve.get("food", 0) + party_size * checks
        except Exception:                                     # noqa: BLE001
            pass
    return reserve


def plan_room_provisions(state, store, loot=None, band: Optional[str] = None,
                         remaining_fights: Optional[int] = None,
                         eaten: Optional[Mapping[int, int]] = None) -> list[tuple]:
    """`[(Use, rank)]` for the party standing out of combat. [] on anything
    unreadable -- a provision is never spent on a guess."""
    if getattr(state, "in_battle", False) or not getattr(state, "heroes", None):
        return []
    from bridge.seed import build_party_from_live             # noqa: PLC0415
    engine = build_party_from_live(state, store)
    needs = needs_from_engine(state, engine, eaten)
    if not needs:
        return []
    # The save names food "provision" with no id; every other supply by id.
    pack = {("food" if k in ("", "provision") else k): int(v)
            for k, v in (getattr(state, "provisions", None) or {}).items()}
    reserve = room_reserve(loot, len(needs), band, remaining_fights)
    rank = {n.guid: n.rank for n in needs}
    return [(u, rank.get(u.guid, 1)) for u in between_fights(needs, pack, reserve)]


def battle_item(state, engine, action, store,
                reserve: Optional[Mapping[str, int]] = None) -> Optional[Use]:
    """One provision for the ACTING hero before their skill, or None.

    Measured live 2026-09-26: an item right-clicked on a hero's turn is used
    on that hero and does NOT end the turn (holy water 2 -> 1, the resist
    icons up, the Plague Doctor still to act). So an item is a free action
    ahead of the policy's skill -- the skill is untouched, which keeps the
    combat layer the policy's.

    Holy water first (a DoT/disease-heavy line that can reach this rank),
    then dog treats (only the Houndmaster, only when +50% makes the planned
    hit a kill). Both spend only the surplus above `reserve`."""
    if action is None or engine is None or state is None:
        return None
    if str(getattr(action, "skill_id", "")).startswith("__"):
        return None
    actor = next((h for h in engine.heroes
                  if h.id == getattr(action, "hero_id", -1)), None)
    heroes = list(getattr(state, "heroes", []) or [])
    idx = int(getattr(action, "hero_id", -1))
    if actor is None or not (0 <= idx < len(heroes)):
        return None
    live = heroes[idx]
    if getattr(live, "hero_class", "") != getattr(actor, "definition_id", ""):
        return None
    pack = {("food" if k in ("", "provision") else k): int(v)
            for k, v in (getattr(state, "provisions", None) or {}).items()}
    buffs = [getattr(b, "buff_id", "") for b in getattr(actor, "buffs", []) or []]
    need = HeroNeed(guid=int(live.guid), hero_class=live.hero_class,
                    hp=float(actor.hp), max_hp=float(actor.max_hp),
                    rank=int(getattr(actor, "position", 0)) + 1,
                    holy_water_up=any(str(b).startswith("holy_water") for b in buffs))
    use = holy_water_now(need, dot_threat(need.rank, engine.enemies, store),
                         pack, reserve)
    if use is not None:
        return use
    if need.hero_class != "houndmaster":
        return None
    try:
        sd = engine.get_skill_data(action.skill_id)
    except Exception:                                         # noqa: BLE001
        sd = None
    if sd is None or getattr(sd, "heal_min", None) is not None:
        return None
    target = next((e for e in engine.enemies
                   if getattr(e, "position", -1) == getattr(action, "target_id", -2)
                   and (e.is_alive() if callable(getattr(e, "is_alive", None)) else True)),
                  None)
    if target is None:
        return None
    mod = 1.0 + float(getattr(sd, "dmg_mod", 0.0) or 0.0)
    return dog_treat_now(need, actor.dmg_min * mod, actor.dmg_max * mod,
                         float(target.hp), pack, reserve)
