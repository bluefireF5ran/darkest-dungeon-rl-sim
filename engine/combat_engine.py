from __future__ import annotations
import re
from dataclasses import dataclass, field, replace
from typing import Callable, Optional
from .rng import SeededRNG
from .combatant import (CombatantState, ActiveBuff, DotInstance, RiposteState,
                        CarryOverHeroState, BURN_BASE_DECAY, burn_next_stack)
from .action import Action
from .resolution import (resolve_attack, compute_hit_chance, resolve_to_hit,
                         PROT_DAMAGE_REDUCTION_CAP)
from .stress import apply_stress, resolve_check
from data.types import (
    DataStore, CombatRules, HeroDefinition, EnemyDefinition, SkillLevelData,
    EffectDefinition, BuffDefinition, TrinketDefinition, QuirkDefinition,
    SkillType, TraitDefinition, TraitActOut,
)


@dataclass
class StepResult:
    obs: dict = field(default_factory=dict)
    reward: float = 0.0
    done: bool = False
    info: dict = field(default_factory=dict)


# Maps an inline stat-modifier field on EffectDefinition (as opposed to a
# .buff_ids reference) to the (stat_type, stat_sub_type) pair
# _apply_buff_to_stats/_reverse_buff_from_stats understand — see the
# is_stat_buff handling in CombatEngine._resolve_effect.
# Re-exported, not defined here: `engine/_constants.py` owns these and the
# mixins import them from there, because importing them back out of this
# module would be a cycle. Every name below is imported FROM
# `engine.combat_engine` somewhere in the repo, so this list is a public
# surface rather than a convenience.
from ._constants import (INLINE_STAT_FIELD_MAP, DEATHBLOW_RESIST_CAP,
    HEAL_CRIT_CHANCE_SINGLE_TARGET, HEAL_CRIT_CHANCE_PER_TARGET,
    HEAL_CRIT_STRESS_REDUCTION, UNDERLEVELED_STRESS_PER_LEVEL,
    UNDERLEVELED_STRESS_DMG_MULT_PER_LEVEL, CORPSE_ALIVE_ROUND_LIMIT,
    BOARD_RANKS, VIRTUE_SELF_HEAL_MAX_HP_FRACTION, _crit_dot_rounds,
    IN_COMBAT_PROVISIONS_ALLOWED, IN_COMBAT_PROVISION_IDS,
    PASS_TURN_STRESS, HEART_ATTACK_STRESS_THRESHOLD,
    HEART_ATTACK_RESULTING_STRESS, ACCELERATED_STALL_SUMMON_THRESHOLD,
    MORTALITY_TIER_RANK, GEAR_RANK_RESOLVE_THRESHOLDS, _int_stat_snapshot,
    max_gear_rank_for_resolve)
from ._forecast import ForecastMixin
from ._formation import FormationMixin
from ._dots import DotMixin
from ._afflictions import AfflictionMixin
from ._effects import EffectsMixin
from ._buffs import BuffMixin
from ._monster_ai import MonsterAIMixin


class CombatEngine(MonsterAIMixin, BuffMixin, EffectsMixin, AfflictionMixin, DotMixin, FormationMixin, ForecastMixin):
    def __init__(self, store: DataStore, rng: SeededRNG):
        self.store = store
        self.rules = store.rules
        self.rng = rng
        self.heroes: list[CombatantState] = []
        self.enemies: list[CombatantState] = []
        self.round: int = 0
        #: Deathblow rolls over this engine's LIFE -- `reset()` leaves it, so
        #: a caller can diff it across a combat transition. Instrumentation
        #: and the `--dd-exposure-penalty` arm; nothing in combat reads it.
        self.deathblow_rolls: int = 0
        self.deaths_door_entries: int = 0
        self.deathblow_deaths: int = 0
        self.turn_order: list[CombatantState] = []
        self.current_turn_index: int = 0
        self.stall_counter: int = 0
        # Per-round count of hero actions using a skill with
        # is_stall_invalidating True (the default -- most support/heal/
        # utility skills explicitly set it False) -- the real "2+ damage
        # moves per round" condition that avoids a stall round. Reset each
        # round in _check_stall.
        self._stall_invalidating_actions_this_round: int = 0
        # Whether the PREVIOUS round was also an accelerated-only stall. The
        # game charges `stall_round_accelerated_only_increment` (1) for the
        # first such round and `_consecutive_increment` (5) for each one after
        # it, which is what makes reinforcements land on the 2nd accelerated
        # stall round rather than the 4th.
        self._accelerated_stall_streak: bool = False
        self.consecutive_misses: dict[int, int] = {}
        # Act-outs actually rolled, by kind. Deliberately NOT reset in reset():
        # it accumulates across a whole expedition so a diagnostic run can check
        # the observed distribution against the trait table (Fearful should bark
        # on ~10% of its turns, not the 100% the old flat model produced).
        self._act_out_counts: dict[str, int] = {}
        self.log: list[str] = []
        self.done: bool = False
        self.winner: str | None = None
        self._action_buffer: Action | None = None
        self._current_enemy_action: int = 0
        self.torch_level: float = 100.0
        # Set by a `.bonus_action_next_turn` effect during _execute_action and
        # read by step() the moment it returns -- see _apply_effect_to_target.
        self._bonus_action_pending: bool = False
        self._stress_chance_bonus: float = 0.0
        self._stress_damage_mult: float = 1.0
        self._monster_dmg_mult: float = 1.0
        self._first_enemy_acted_this_round: bool = False
        self._last_skill_used: str | None = None
        # Turn-order index whose START-of-turn DoT tick has already run,
        # so free actions re-entering the same turn don't re-tick it.
        self._dot_ticked_for_index: int = -1
        # Same guard for the overstress act-out roll. The game's own field name
        # is `combat_start_turn_act_outs` — ONE roll per turn — but a hero can
        # re-enter step() several times in the same turn via free actions
        # (provisions, Abomination's Transform). Without this, a Courageous
        # hero chaining provisions would farm party stress heals, and an
        # afflicted one would re-roll a burnt turn.
        self._act_out_rolled_for_index: int = -1
        # **Act-outs may not chain**, and the game says so in two clauses:
        # "cannot happen two times in a row for the same hero, and two
        # different heroes cannot have act-outs occur back-to-back".
        #
        # Without this an afflicted party can act out on consecutive turns and
        # again the round after, which is the difference between an affliction
        # being a serious problem and being a spiral -- exactly the thing Fran
        # rates "almost the same as a death". The old guard only stopped TWO
        # ROLLS IN ONE TURN; it said nothing about consecutive turns.
        #
        # `(round, turn index)` of the last act-out that fired, and per hero
        # the round of theirs, because a hero's own consecutive turns are a
        # round apart while two heroes' are adjacent indices.
        self._last_act_out_at: tuple = (-1, -1)
        self._last_act_out_round_by_hero: dict[int, int] = {}
        # Opt-in calculation tracing. OFF during training (millions of steps —
        # the string building alone would dominate runtime) and ON for
        # battle_report.py, where the point is to show every roll and formula
        # so a human can check the engine's arithmetic and the policy's
        # decisions against how they would actually play the board.
        self.verbose: bool = False

    def vlog(self, msg: str) -> None:
        if self.verbose:
            self.log.append(msg)

    def _vlog_attack(self, attacker, target, skill_id, skill, result) -> None:
        """Full arithmetic for one attack roll: the accuracy check, then every
        multiplier between the raw damage range and the number subtracted from
        the target's HP."""
        if not self.verbose:
            return
        self.log.append(
            f"    [ACC] {attacker.name} '{skill_id}' vs {target.name}(rank {target.position+1}): "
            f"skill {skill.atk:.0%} + bonus {attacker.atk_bonus:+.0%} + hidden 5% "
            f"= {result.acc_used:.0%}  -  DODGE {result.dodge_used:.0%}  "
            f"=> {result.miss_chance:.0%} to hit | roll {result.hit_roll:.3f} "
            f"=> {'HIT' if result.hit else 'MISS'}")
        if not result.hit:
            return
        parts = [f"roll {result.base_roll:.1f} in [{result.dmg_low}-{result.dmg_high}]"]
        if result.dmg_mod:
            parts.append(f"x skill dmg {1+result.dmg_mod:.2f}")
        if abs(result.damage_multiplier - 1.0) > 1e-9:
            parts.append(f"x conditional {result.damage_multiplier:.2f}")
        if result.excess_crit_bonus:
            parts.append(f"x excess-crit {1+result.excess_crit_bonus:.2f}")
        if result.is_crit:
            parts.append(f"x CRIT {result.crit_multiplier:.2f}")
        if result.prot_applied:
            parts.append(f"x (1-PROT {result.prot_applied:.0%})")
        self.log.append(
            f"    [CRIT] chance {result.crit_chance:.0%} => {'CRIT!' if result.is_crit else 'no'}")
        self.log.append(
            f"    [DMG] {' '.join(parts)} = {result.applied_damage} "
            f"| {target.name} HP {target.hp + result.applied_damage} -> {target.hp}")
        if result.blocked_by_aegis:
            self.log.append("    [AEGIS] block consumed — damage negated")

    # ── Combatant state helpers ──



    def _apply_mode_battle_start_effects(self):
        """`mode: .battle_start_effect_ids` -- what a STANCE puts on its hero
        the moment the fight opens.

        Two carriers ship, both stance heroes, so this is inert for the other
        eighteen classes. Applied here rather than in `_on_board_ready` for the
        same reason gear is applied there: this runs after the board exists and
        before the torch modifiers, the surprise roll and any turn, so nothing
        has acted against an un-equipped party.
        """
        for hero in self.heroes:
            hd = self.store.heroes.get(hero.definition_id)
            if hd is None:
                continue
            mode = next((m for m in hd.modes if m.id == hero.mode), None)
            if mode is None:
                continue
            for ename in mode.battle_start_effect_ids:
                eff = self.store.effects.get(ename)
                if eff is not None:
                    self._resolve_effect(hero, hero, eff, None)

    def _apply_trinket_attack_procs(self, performer: CombatantState,
                                      target: CombatantState,
                                      skill: SkillLevelData, is_crit: bool = False):
        """Effects a WORN TRINKET adds to its wearer's attack.

        Applied only on a landed hit against a living target, and only for
        heroes -- no monster carries a trinket. Resolved through the ordinary
        `_resolve_effect` path so the proc obeys the same `.chance` roll and
        the same resist subtraction any other effect does.
        """
        for tid in performer.trinket_ids:
            td = self.store.trinkets.get(tid)
            if not td or not td.attack_effects:
                continue
            for ename in td.attack_effects:
                eff = self.store.effects.get(ename)
                if eff is not None:
                    self._resolve_effect(target, performer, eff, skill, is_crit=is_crit)

    def _reaction_blocks(self, hero: CombatantState, kind: str) -> bool:
        """Does this hero's affliction refuse `kind` right now?

        `reaction_act_outs`, the sibling of the act-out table we already roll
        at the start of an afflicted hero's turn. This one fires when someone
        does something TO them -- four of the seven afflictions refuse a heal,
        a buff, an item or a retreat one time in three, and five refuse a move.

        Only afflictions reach here. A virtuous hero carries `block_effect` at
        chance 1.0, which cannot mean "immune to everything" and is not wired
        -- see ASSUMPTIONS.md. The roll is drawn ONLY when an affliction that
        actually carries this kind is present, so a party with no afflicted
        hero draws nothing and every existing RNG stream is untouched.
        """
        if not hero.is_hero or not hero.is_afflicted or not hero.affliction_id:
            return False
        trait = self.store.traits.get(hero.affliction_id)
        if trait is None:
            return False
        chance = trait.reactions.get(kind, 0.0)
        if chance <= 0.0:
            return False
        if self.rng.random() >= chance:
            return False
        self.log.append(f"{hero.name} refuses ({trait.id}: {kind})")
        return True












    # ── Reset ──

    def reset(self, hero_defs: list[HeroDefinition], enemy_defs: list[EnemyDefinition],
              hero_positions: Optional[list[int]] = None, torch_level: float = 100.0,
              hero_levels: Optional[list[int]] = None,
              hero_skill_ids: Optional[list[list[str]]] = None,
              provisions: Optional[dict[str, int]] = None,
              carry_over: Optional[list[Optional[CarryOverHeroState]]] = None,
              quest_difficulty_level: Optional[int] = None,
              auto_resolve_to_hero: bool = True,
              surprise: Optional[tuple[bool, bool]] = None,
              zone_id: str = "",
              on_board_ready: Optional[Callable[[], None]] = None):
        """`auto_resolve_to_hero=False` stops reset() short of walking the
        initiative queue, leaving the board built but no turn taken.

        Only `bridge/seed.py` passes it: when the state comes from a REAL game
        already several turns into a fight, letting the engine auto-resolve
        would have it invent enemy actions the real game has already played,
        overwriting the very state being seeded. Every other caller keeps the
        default, which is behaviourally identical to before this parameter
        existed — same convention as hero_levels/provisions/carry_over."""
        self.heroes = []
        self.enemies = []
        # Shared party inventory (bandage/antivenom/holy_water/laudanum) —
        # consumed by any hero via a free (turn-preserving) action. None
        # (default) = no provisions available, identical to before this
        # param existed, same convention as hero_levels/hero_skill_ids.
        self.provision_inventory: dict[str, int] = dict(provisions) if provisions else {}
        self.round = 0
        # Set by _roll_surprise below, read by _compute_turn_order and
        # get_valid_actions, and only ever consulted in the OPENING round
        # (self.round == 0 -- _end_of_round increments afterwards).
        self.party_surprised = False
        self.monsters_surprised = False
        self.stall_counter = 0
        self._stall_invalidating_actions_this_round = 0
        self._accelerated_stall_streak = False
        self.consecutive_misses = {}
        self.log = []
        self.done = False
        self.winner = None
        # Failed retreat attempts THIS combat. The real game pays
        # `combat_retreat_bonus_chance_per_attempt` for each one, so the odds
        # climb while the party keeps trying; per-combat, like every other
        # counter reset here.
        self.retreat_attempts = 0
        self._action_buffer = None
        self._current_enemy_action = 0
        self.torch_level = torch_level
        # Ranks painted by Controlled Burn: {rank: [amount, rounds_left]}.
        # Cleared here, so a zone can never survive into the next combat.
        self.burn_zones: dict[int, list[int]] = {}
        # Which region this fight is in, for `in_dungeon`-gated buffs. Empty
        # means "not stated", and an unstated zone grants those buffs exactly
        # as they were granted before the gate existed -- see the branch in
        # `_is_conditional_rule_active`.
        self.zone_id = zone_id or ""

        self._stress_chance_bonus = 0.0
        self._stress_damage_mult = 1.0
        self._monster_dmg_mult = 1.0
        self._first_enemy_acted_this_round = False
        self._last_skill_used = None
        # Real per-hero skill loadout restriction (4 of a hero's ~7 skills,
        # matching what a player would actually equip) — keyed by the
        # engine's own hero id (0..N-1), not definition_id, since two heroes
        # of the same class can have different loadouts in principle. Absent
        # entries (hero_skill_ids=None, or a shorter list than hero_defs)
        # mean "no restriction" — get_hero_skill_ids falls back to the
        # hero's full movepool, exactly like before this parameter existed.
        self._hero_skill_id_restrictions: dict[int, list[str]] = {}
        if hero_skill_ids:
            for i, ids in enumerate(hero_skill_ids):
                if ids:
                    self._hero_skill_id_restrictions[i] = ids

        positions = hero_positions or [0, 1, 2, 3]
        for i, hd in enumerate(hero_defs):
            pos = positions[i] if i < len(positions) else i
            # Resolve Level -> weapon/armour/skill tier index, from the
            # game's OWN upgrade gating rather than a convention: see
            # GEAR_RANK_RESOLVE_THRESHOLDS at the top of this file. A hero is
            # fielded at the best gear their resolve can buy, which is what
            # "a fair fight at this difficulty" means — the party the game
            # would actually let you take into that dungeon.
            #
            # This used to be `tier_idx = level - 1`, which agreed with the
            # data for veterans (resolve 3-4 -> rank 4) and champions
            # (resolve 5-6 -> rank 5) and was wrong by a full rank for
            # apprentices: resolve 2 buys rank 3, not rank 2. Since
            # TIER_TO_HERO_LEVEL fields mash tiers 1-2 at resolve 2, every
            # Apprentice-bracket combat ever trained was under-equipped.
            # No hero_levels given -> exact pre-existing behavior (max gear
            # tier, no resistance bonus), not an implicit "level 6" — those
            # are two different things and conflating them regressed every
            # existing default-composition combat (silently +50% resist).
            if hero_levels and i < len(hero_levels):
                level = hero_levels[i]
                tier_idx = max(0, min(len(hd.weapon_tiers) - 1,
                                      max_gear_rank_for_resolve(level) - 1))
                # Each resolve level adds +10% resist to stun/blight/bleed/
                # move/debuff. **`level`, NOT `level - 1`** -- corrected
                # 2026-09-02, and the old form was off by one for every hero
                # above resolve 0.
                #
                # The scale is the SAVE's, 0-6: `resolve_level_thresholds`
                # ships seven entries with the first at 0, `TIER_TO_HERO_LEVEL`
                # is written on it, and `RosterHero.resolve_level` carries it.
                # So `base_stats` describes a **resolve-0** hero, and the old
                # comment's own justification -- "base_stats already reflect an
                # unleveled hero" -- argues for this formula rather than for
                # the subtraction it sat above. Subtracting one gave resolve 0
                # and resolve 1 identical resistances.
                #
                # Fran, 2026-09-02, with the worked example that settles it:
                # "a level 0 leper has 60% but a level 6 leper has 60 base +
                # 10*6% extra so 120%". The Leper ships `.stun 60% .move 60%`,
                # so both ends of his example are the shipped numbers, and the
                # 120% is the reason **nothing clamps a resist**: it is
                # subtracted in `roll < chance - resist`, where above 1.0
                # simply means the payload cannot land.
                # https://darkestdungeon.wiki.gg/wiki/Trap
                #
                # THIS MOVED THE TRAINING DISTRIBUTION. Every hero at resolve 1
                # or above is more resistant than it was, so a comp score or an
                # eval from before this date was measured on a different ruler.
                # Checkpoints still load -- the observation is unchanged.
                resist_bonus = max(0, level) * 0.10
            else:
                level = 6
                tier_idx = len(hd.weapon_tiers) - 1
                resist_bonus = 0.0
            wt = hd.weapon_tiers[tier_idx]
            at = hd.armour_tiers[tier_idx]
            # Wiki-confirmed underleveled-hero penalty: "20 stress for every
            # level of difference" plus "+25% stress for every level of
            # difference" to incoming stress damage, when a hero's resolve
            # level falls short of the expedition's own difficulty level
            # (Apprentice/Veteran/Champion = quest difficulty 1/3/5, same
            # 1-6 scale as hero_levels/resolve level -- confirmed no
            # rules.json field backs this, same "hardcoded in the binary"
            # treatment as TIER_TO_HERO_LEVEL). Opt-in via
            # quest_difficulty_level=None (default) -> zero shortfall, zero
            # penalty, identical to before this parameter existed --
            # TIER_TO_HERO_LEVEL already deliberately pairs party level to
            # zone tier exactly everywhere this engine's own envs sample a
            # party, so this is currently unreachable through them by
            # design; it only fires for a caller that explicitly passes a
            # mismatched level, e.g. a manual CombatEngine.reset() call.
            level_shortfall = (max(0, quest_difficulty_level - level)
                                if quest_difficulty_level is not None else 0)
            hero = CombatantState(
                id=i, definition_id=hd.id, is_hero=True, name=hd.name,
                position=pos, hp=at.hp, max_hp=at.hp,
                dodge=at.dodge, prot=at.prot, speed=wt.spd_bonus,
                crit=wt.crit_bonus, atk_bonus=wt.atk_bonus,
                dmg_min=wt.dmg_min, dmg_max=wt.dmg_max,
                stun_resist=hd.base_stats.stun_resist + resist_bonus,
                blight_resist=hd.base_stats.blight_resist + resist_bonus,
                bleed_resist=hd.base_stats.bleed_resist + resist_bonus,
                debuff_resist=hd.base_stats.debuff_resist + resist_bonus,
                move_resist=hd.base_stats.move_resist + resist_bonus,
                death_blow_resist=hd.base_stats.death_blow_resist,
                mode=hd.modes[0].id if hd.modes else "human",
                level=level, skill_tier=tier_idx,
                stress=int(UNDERLEVELED_STRESS_PER_LEVEL * level_shortfall),
                stress_dmg_received_mult=1.0 + UNDERLEVELED_STRESS_DMG_MULT_PER_LEVEL * level_shortfall,
                level_shortfall=level_shortfall,
                overstress_override=self._overstress_override(hd),
            )
            self.heroes.append(hero)
            carry = carry_over[i] if carry_over and i < len(carry_over) else None
            if carry is not None:
                self._apply_carry_over(hero, hd, carry)

        # Position via a running slot cursor advanced by each enemy's own
        # .size (1 or 2), not a naive enumerate() index — a size-2 enemy
        # (verified common in real encounter data: skeleton_captain_A,
        # brigand_blood_A, etc., ~25% of real multi-enemy hall/room/boss/
        # stall comps combine a size-2 enemy with others) occupies 2 ranks,
        # so the next enemy must start one slot further in, or the two
        # would silently overlap onto the same position. Real comp data
        # never sums to more than 4 total size-units (verified across all
        # 1105 extracted comps), so this never overflows the 4-slot board.
        slot_cursor = 0
        for ed in enemy_defs:
            enemy = self._build_enemy_combatant(ed, slot_cursor)
            self.enemies.append(enemy)
            slot_cursor += ed.size

        self._reinforcements_spawned = False
        # PERSISTENT EQUIPMENT GOES HERE, and this hook exists because it used
        # to go after `reset()` returned -- by which time reset had already
        # rolled surprise, computed initiative AND let every faster enemy take
        # its opening turn. So the first blows of every combat landed on heroes
        # with no gear dodge, no trinket PROT and the base max-HP ceiling, a
        # +SPD trinket could not affect the round it was most wanted in, and
        # the two surprise-chance buff types were unreadable in principle.
        #
        # The board is fully built at this point and nothing has acted, which
        # is the only window where "what the hero is carrying" can be applied
        # without either rebuilding a state or retconning a turn.
        if on_board_ready is not None:
            on_board_ready()
        self._apply_mode_battle_start_effects()
        self._apply_torch_modifiers()
        # After the torch (the darkness band is an input to the chance) and
        # before the turn order (which surprise reorders). `surprise=` states
        # the outcome instead of rolling for it, and then does NOT scramble:
        # bridge/seed.py passes the flags a live `persist.raid.json` already
        # carries, and those ranks have already been scrambled by the game.
        if surprise is None:
            self._roll_surprise()
        else:
            self.party_surprised, self.monsters_surprised = surprise
        self._compute_turn_order()
        self._resolve_bonus_initiative(is_round_start=True)
        self._check_win_loss()
        if not self.done and auto_resolve_to_hero:
            self._advance_to_next_hero_turn()

    def _apply_carry_over(self, hero: CombatantState, hd: HeroDefinition, carry: CarryOverHeroState):
        """Seeds a freshly-built (full HP/0 stress) CombatantState with the
        state that should persist from a previous combat within the same
        expedition. HP/stress/Death's-Door/mortality-tier, the overstress
        personality and the DoTs carry; buffs/guard/riposte/stun/cooldowns/
        round are already correctly fresh from the normal reset() path above.

        The personality is restored by id and its trait buffs re-applied,
        rather than re-rolled — a hero does not get a fresh 25% virtue lottery
        ticket at every room. See CarryOverHeroState for the data evidence
        that affliction outlives a combat."""
        hero.hp = min(carry.hp, hero.max_hp)
        hero.stress = max(0, carry.stress)
        hero.mortality_tier = carry.mortality_tier
        self._restore_personality(hero, carry)
        buff_list = None
        if carry.mortality_tier == "mortality":
            buff_list = hd.recovery_buffs
        elif carry.mortality_tier == "heart_attack":
            buff_list = hd.recovery_heart_attack_buffs
        if buff_list:
            for buff_id in buff_list:
                bd = self.store.buffs.get(buff_id)
                if bd:
                    self._apply_buff_to_stats(hero, bd)
        # Camp buffs are the one buff class that crosses a combat boundary:
        # camping happens BETWEEN combats and a camp buff lasts until the end
        # of the next battle, so it has to survive this reset. Combat buffs
        # still do not carry.
        for buff_id in carry.camp_buff_ids:
            bd = self.store.buffs.get(buff_id)
            if bd:
                self._apply_buff_to_stats(hero, bd)
        # A DoT follows the party out of the room. `rules.json`'s
        # `dotHallwayStepRatio` is the evidence — see CarryOverHeroState.
        # COPIED, never aliased: the carry record outlives this reset and a
        # tick mutating `remaining_ticks` in place would rewrite history.
        hero.bleeds = [replace(d) for d in carry.bleeds]
        hero.blights = [replace(d) for d in carry.blights]
        hero.stress_dots = [replace(d) for d in carry.stress_dots]
        hero.burn_stacks = carry.burn_stacks
        if carry.is_deaths_door and hero.hp <= 0:
            hero.is_deaths_door = True
            self._apply_deaths_door_buffs(hero)

    def _restore_personality(self, hero: CombatantState, carry: CarryOverHeroState):
        """Re-establish a carried-over affliction or virtue on a freshly built
        CombatantState: the flag, the id, and the trait's stat buffs.

        Deliberately does NOT go through _apply_affliction_effects — that draws
        a NEW personality, and a hero keeps the one they broke with. It also
        skips _maybe_force_mode_revert, since reset() already starts every hero
        in their default mode."""
        trait_id = carry.virtue_id or carry.affliction_id
        if not trait_id:
            return
        # **A cure survives the combat boundary.** `carry.stress` is what the
        # party walked out of the last fight with, so restoring a trait on top
        # of a hero who reached 0 would undo the recovery every time the next
        # room started -- and camping, which is where most stress healing
        # actually happens, sits exactly on that boundary.
        if carry.stress <= 0:
            return
        trait = self.store.traits.get(trait_id)
        if trait is None:
            return
        hero.is_virtuous = trait.is_virtue
        hero.is_afflicted = not trait.is_virtue
        hero.virtue_id = trait_id if trait.is_virtue else None
        hero.affliction_id = None if trait.is_virtue else trait_id
        hero.overstress_stats = _int_stat_snapshot(hero)
        for buff_id in trait.buff_ids:
            bd = self.store.buffs.get(buff_id)
            if bd:
                self._apply_buff_to_stats(hero, bd)
                hero.buffs.append(ActiveBuff(buff_id=buff_id, remaining_rounds=None))

    def _build_enemy_combatant(self, ed: EnemyDefinition, position: int) -> CombatantState:
        """Extracted from reset()'s enemy-building loop so _spawn_reinforcements
        can build a new enemy mid-combat the same way, instead of duplicating
        this logic. id is derived from the current enemy count, which is
        always safe since dead/corpse enemies are never removed from
        self.enemies (only flagged), so ids never collide."""
        dmg_min, dmg_max = 1, 1
        if ed.skills and ed.skills[0].levels:
            lv = next(iter(ed.skills[0].levels.values()))
            # Truthy, not `is not None`: a first skill at `.dmg 0 0` now
            # loads as 0, and the opening band stays the 1-1 it always was.
            if lv.atk_dmg_min:
                dmg_min = lv.atk_dmg_min
            if lv.atk_dmg_max:
                dmg_max = lv.atk_dmg_max
        return CombatantState(
            id=100 + len(self.enemies), definition_id=ed.id, is_hero=False, name=ed.name,
            position=position, hp=ed.stats.hp, max_hp=ed.stats.hp,
            dodge=ed.stats.dodge, prot=ed.stats.prot, speed=ed.stats.speed,
            crit=0.0, atk_bonus=0.0,
            dmg_min=dmg_min, dmg_max=dmg_max,
            stun_resist=ed.stats.stun_resist,
            blight_resist=ed.stats.blight_resist,
            bleed_resist=ed.stats.bleed_resist,
            debuff_resist=ed.stats.debuff_resist,
            move_resist=ed.stats.move_resist,
            size=ed.size,
            death_blow_resist=0.0,
        )

    # ── Torchlight ──

    def _get_darkness_band(self):
        """rules.json's darkness.range_table bands EXCLUDE their lower bound.

        The file says so itself — `darkness.lowest_excluded: true` — and the
        real tiers it produces are the documented ones: 76-100 brightest,
        51-75 dim, 26-50 dark, 0-25 pitch black. So torch=75 is **dim**, not
        radiant.

        This used to read `band.lower <= torch < band.upper`, which contradicted
        the tier list in its own docstring and put 75 in the radiant band. Inert
        for training and every archived eval, which field torch=100 and are
        unaffected at any non-boundary value — but NOT inert for the bridge,
        which feeds `bridge/seed.py` the real torch out of the save, and a human
        deliberately parks it at exactly 75 (PLAYBOOK 2.3).
        """
        for band in self.rules.darkness_table:
            if band.lower < self.torch_level <= band.upper:
                return band
        return None

    def _apply_torch_modifiers(self):
        """Torch level is fixed for the whole encounter in this simulator
        (no mid-combat camping/consumables), so darkness modifiers are
        applied once here as permanent stat deltas — same pattern as
        apply_trinkets_and_quirks — rather than threaded through every
        attack/stress call. rules.json values are percentage points (e.g.
        4.0 = +4%); divided by 100 to match the 0-1 fraction scale used for
        crit/dodge/atk_bonus elsewhere in this engine. monster_damage_increase
        is treated as a multiplicative scale on dmg_min/dmg_max (consistent
        with how skill.dmg_mod and buff damage multipliers work elsewhere),
        the rest are additive.
        """
        band = self._get_darkness_band()
        if band is None:
            return
        for h in self.heroes:
            h.crit += band.player_crit_increase / 100.0
            h.dodge += band.player_def_increase / 100.0
        for e in self.enemies:
            e.atk_bonus += band.monster_attack_increase / 100.0
            e.crit += band.monster_crit_increase / 100.0
        # NOT applied directly to e.dmg_min/dmg_max here: _execute_enemy_turn
        # re-derives those from the skill's atk_dmg_min/atk_dmg_max every
        # turn, which would silently overwrite a one-time multiplier applied
        # at reset. Stored instead and applied at that point of use.
        self._monster_dmg_mult = 1.0 + band.monster_damage_increase / 100.0
        self._stress_chance_bonus = band.stress_chance_increase / 100.0
        self._stress_damage_mult = 1.0 + band.stress_damage_increase / 100.0

    # ── Skill / def lookup ──

    def get_hero_skill_ids(self, hero_id: int) -> list[str]:
        hd = self._get_hero_def(hero_id)
        if not hd:
            return []
        all_ids = [s.id for s in hd.skills]
        restriction = self._hero_skill_id_restrictions.get(hero_id)
        if restriction is None:
            return all_ids
        allowed = set(restriction)
        return [sid for sid in all_ids if sid in allowed]

    def get_hero_full_skill_ids(self, hero_id: int) -> list[str]:
        """The hero's COMPLETE movepool in HeroDefinition order, ignoring any
        per-comp 4-skill restriction. Distinct from get_hero_skill_ids (which
        filters to the restriction) and used as the stable slot vocabulary for
        the Gym action space: every real hero class has exactly 7 skills, so
        "skill slot k" names the same skill for a given class no matter which
        4 a particular comp happens to equip. Encoding the restricted list
        instead would make slot k mean different skills for two comps running
        the same class, which is exactly the kind of unstable action semantics
        the fixed action space exists to remove."""
        hd = self._get_hero_def(hero_id)
        return [s.id for s in hd.skills] if hd else []

    def _get_hero_def(self, hero_id: int) -> HeroDefinition | None:
        if hero_id >= len(self.heroes):
            return None
        return self.store.heroes.get(self.heroes[hero_id].definition_id)

    def _get_enemy_def(self, enemy_id: int) -> EnemyDefinition | None:
        t = self._find_combatant(enemy_id)
        if not t or t.is_hero:
            return None
        return self.store.enemies.get(t.definition_id)

    def _find_combatant(self, cid: int) -> CombatantState | None:
        for h in self.heroes:
            if h.id == cid:
                return h
        for e in self.enemies:
            if e.id == cid:
                return e
        return None

    def get_skill_data(self, skill_id: str, level: int = 4) -> SkillLevelData | None:
        """O(1) via `store.skill_index()`; this used to scan ~678 skill defs.

        Called ~15.6x per env step across step()/_execute_action()/
        get_valid_actions()/_skill_slot_vecs(). See DataStore.skill_index for
        why the index reproduces the old scan's precedence exactly.
        """
        return self.store.skill_index().get((skill_id, level))

    def _is_ally_skill(self, skill_id: str) -> bool:
        """True if this skill targets the performer's own side (heals,
        Guard, party buffs). Real signal: '@' in the raw .target notation
        (SkillLevelData.targets_allies) — NOT SkillType. SkillType.ALLY_TEAM
        and SkillType.SELF are never actually assigned by the loader:
        .type is absent entirely on every heal skill (defaults to
        SkillType.MELEE) and support skills like Man-at-Arms' defender/Guard
        carry .type "melee" too, despite targeting an ally. A SkillType-based
        check here previously made every heal/support skill resolve as if it
        targeted the enemy side — verified: it generated 0 valid actions, or
        actions pointed at enemy positions, for every heal in the game.

        O(1) via `store.ally_skill_index()` — hero skills only, exactly as the
        scan this replaced. An unknown id is False, which is why `.get` carries
        that default rather than raising.
        """
        return self.store.ally_skill_index().get(skill_id, False)

    # ── Turn order ──

    # -- Surprise --------------------------------------------------------

    def _surprise_chances(self) -> tuple[float, float]:
        """(party surprised, monsters surprised) for this encounter.

        `base + darkness + trinkets`, clamped to `[0, max]`. Every number is
        shipped: the bases and the two 0.65 caps are `rules.json` scalars, the
        darkness column is the band's own `heroes_surprised_increase` /
        `monsters_surprised_increase` (percentage points, so /100 like every
        other band column), and the modifiers are real buff stat_types.

        Two things this simulator does NOT model, and what each costs:

        * **Corridor vs room.** Nothing, at these values -- the shipped bases
          are 0.1 for all four unscouted combinations. They only diverge once
          a room is scouted.
        * **Scouting.** Real, and it is why the `known_` bases are read but
          never used here: a scouted room drops the party's chance to -1.0
          (a hard never) and RAISES the monsters' to 0.25. Modelling it needs
          a map layer the combat sim does not have, so every encounter is
          treated as unscouted -- the pessimistic side for the party.

        And one ordering limitation, stated rather than hidden: the buff terms
        only see equipment the heroes are ALREADY wearing when `reset()` runs,
        and both expedition envs equip gear and trinkets immediately AFTER it
        (`_apply_persistent_equipment_for_current_roster`). So a Book of
        Intuition does nothing to a sampled comp's surprise chance today. That
        ordering is a bug in its own right and a wider one than this -- the
        same window is what lets a fast enemy take its opening turn against a
        hero whose armour has not been applied yet -- so it is recorded as its
        own item in ASSUMPTIONS.md rather than worked around here. `bridge/
        seed.py` is unaffected: it states the outcome instead of rolling.
        """
        band = self._get_darkness_band()
        party = self.rules.surprise_room_party_base_chance
        monsters = self.rules.surprise_room_monsters_base_chance
        if band is not None:
            party += band.heroes_surprised_increase / 100.0
            monsters += band.monsters_surprised_increase / 100.0
        for h in self.heroes:
            if h.is_alive():
                party += h.party_surprise_chance_mod
                monsters += h.monsters_surprise_chance_mod
        return (
            max(0.0, min(self.rules.surprise_max_party_surprised_chance, party)),
            max(0.0, min(self.rules.surprise_max_monsters_surprised_chance, monsters)),
        )

    def _roll_surprise(self) -> None:
        """Decide, once, whether either side opened the fight flat-footed.

        Two INDEPENDENT draws, always both taken so the RNG stream does not
        depend on the first outcome. Independence is the save format's own
        reading -- `persist.raid.json` carries `party_surprised` and
        `monsters_surprised` as two separate booleans, and rules.json gives
        each its own base chance and its own 0.65 cap. Both true at once is
        therefore representable, and cancels out in the turn order, which is
        the sensible thing for it to do.

        What surprise costs, per the wiki's Surprise page:

        * the surprised side **acts last in the opening round** -- not a lost
          turn, a lost tempo;
        * a surprised **party is scrambled**, its ranks randomised, which is
          the expensive half: a back-rank Vestal in rank 1 has no legal heal;
        * a surprised party **cannot retreat** during that round.

        Unmodelled here and deliberately so: surprise is impossible in the
        Darkest Dungeon and the Farmstead (neither is a region this simulator
        fields), and forced by a Shambler/Fanatic/camping ambush (the two
        `surprise_ambush_*` keys rules.json ships for that are absent from the
        binary, so the game does not roll them either -- it just sets it).
        """
        p_party, p_monsters = self._surprise_chances()
        # Both draws are taken before the monster flags are consulted, so the
        # stream does not depend on which monsters turned up.
        roll_party = self.rng.random() < p_party
        roll_monsters = self.rng.random() < p_monsters
        self.party_surprised, self.monsters_surprised = self._apply_surprise_flags(
            roll_party, roll_monsters)
        if self.party_surprised:
            self._scramble_side(is_hero=True)
            self.log.append("The party is surprised!")
        if self.monsters_surprised:
            self.log.append("The monsters are surprised!")

    def _apply_surprise_flags(self, roll_party: bool,
                              roll_monsters: bool) -> tuple[bool, bool]:
        """Let the monsters on the board override the roll.

        Every one of the game's 383 monster files carries
        `battle_modifier: .can_surprise .can_be_surprised .always_surprise
        .always_be_surprised`, and nothing read them until 2026-08-21 -- so
        surprise, added the same day, was rolled against monsters the game says
        cannot be surprised and monsters it says ALWAYS surprise you.

        What the data says, and why it is trustworthy: `always_surprise` is
        carried by **exactly the Shambler line and the Fanatic**, which is the
        wiki's "unavoidable vs Shambler/Fanatic" list with nothing else in it.
        162 monsters -- the Ancestor forms, the Crimson Court bodies, the Baron
        -- have both `can_` flags False, which is how the game encodes
        "impossible in the Darkest Dungeon and the Farmstead": per monster,
        rather than per region.

        **ANY, not ALL**, in both directions: one ambusher is enough to catch a
        party out, and one Shambler forces it. The choice is nearly
        unobservable -- across all 1105 real encounter comps only **10** are
        mixed on either flag -- but it has to be made, so it is written down.
        """
        # Same lookup `_check_stall` uses for the other two `battle_modifier`
        # flags: CombatantState holds a definition_id, not the definition.
        defs = [d for d in (self.store.enemies.get(e.definition_id)
                            for e in self.enemies if e.is_alive()) if d]
        if not defs:
            return roll_party, roll_monsters
        party = (True if any(d.always_surprise for d in defs)
                 else roll_party and any(d.can_surprise for d in defs))
        monsters = (True if any(d.always_be_surprised for d in defs)
                    else roll_monsters and any(d.can_be_surprised for d in defs))
        return party, monsters

    def _scramble_side(self, is_hero: bool) -> None:
        """Randomise one side's ranks, keeping the SET of occupied ranks.

        Same invariant a push or a pull keeps (`_shift_to_position`): a
        formation is a permutation of the ranks it already fills, so a scramble
        can neither open a hole nor close one.

        Built on `_formation`, so on the monster side a CORPSE shuffles with
        everyone else -- it is an occupant, which is the whole reason it
        blocks. Heroes leave no corpse, so the party case is unchanged from
        when this only did heroes for surprise.

        Sizes are handled by rebuilding the line with a cursor rather than by
        permuting positions directly: a size-2 monster consumes two ranks, so
        assigning it a neighbour's old position would overlap. A permutation
        that cannot be laid out (the sizes do not fit the same slots in the new
        order) is abandoned rather than half-applied.
        """
        line = self._formation(is_hero)
        if len(line) < 2:
            return
        slots = [c.position for c in line]
        order = list(line)
        self.rng.shuffle(order)
        cursor = 0
        placed = []
        for c in order:
            placed.append((c, cursor))
            cursor += max(1, c.size)
        if cursor > BOARD_RANKS:
            return
        # Only rewrite once the whole line is known to fit.
        for c, pos in placed:
            c.position = pos
        del slots

    def _scramble_one(self, c: CombatantState) -> None:
        """Move a single combatant to a random rank of its own side, sliding
        whoever it passes -- a displacement, not a teleport, so it obeys the
        same rotation rule as a push or a pull."""
        line = self._formation(c.is_hero)
        if len(line) < 2:
            return
        options = [p for p in (x.position for x in line) if p != c.position]
        if not options:
            return
        self._shift_to_position(c, self.rng.choice(options))


    def _compute_turn_order(self):
        combatants = [c for c in self.heroes + self.enemies if c.is_alive()]
        rolls = {}
        def sort_key(c):
            spd = c.speed
            roll = self.rng.roll(8)
            rolls[id(c)] = (spd, roll)
            # Explicit tie-break on equal SPD+d8: heroes before enemies,
            # then lower rank first. Previously this fell out only by
            # accident, from Python's stable sort plus combatants being
            # built as `self.heroes + self.enemies` (each already in rank
            # order) — correct today, but silently dependent on both lists
            # staying build-ordered and on sort() staying stable. Made
            # explicit here so a future reorder or `sorted()` swap can't
            # break it. Matches the wiki-documented tie-break rule.
            # A surprised side acts LAST in the opening round -- ranked
            # ahead of the stun term only because the two cannot co-occur
            # (nothing is stunned before the first turn is taken), which
            # leaves the pre-surprise ordering untouched for every later
            # round, where both surprise flags are ignored.
            surprised = self.round == 0 and (
                self.party_surprised if c.is_hero else self.monsters_surprised)
            return (1 if surprised else 0, 1 if c.is_stunned else 0,
                    -(spd + roll), 0 if c.is_hero else 1, c.position)
        combatants.sort(key=sort_key)
        self.turn_order = combatants
        self.current_turn_index = 0
        self._dot_ticked_for_index = -1
        self._act_out_rolled_for_index = -1
        # NOT cleared per round: the no-chaining rule spans the round
        # boundary, and clearing here would let the first hero of a round act
        # out immediately after the last hero of the previous one.
        if self.verbose:
            self.log.append("  [INITIATIVE] SPD + d8:")
            for c in combatants:
                spd, roll = rolls.get(id(c), (c.speed, 0))
                side = "team" if c.is_hero else "foe "
                self.vlog(f"    {side} {c.name:22s} rank {c.position+1}  "
                          f"SPD {spd} + d8 {roll} = {spd + roll}"
                          + ("  [STUNNED — acts last]" if c.is_stunned else ""))
            order = " -> ".join(
                f"{i+1} {c.name}(rank {c.position+1},{'team' if c.is_hero else 'foe'})"
                for i, c in enumerate(combatants))
            self.vlog(f"    ORDER: {order}")

    def get_current_actor_id(self) -> int | None:
        """Id of the hero whose turn it currently is, or None if combat is
        done. Callers (Gym wrapper, tests) should query this instead of
        picking a hero on their own — the engine owns turn order."""
        if self.done or self.current_turn_index >= len(self.turn_order):
            return None
        combatant = self.turn_order[self.current_turn_index]
        return combatant.id if combatant.is_hero else None

    def _end_of_round(self):
        self._resolve_bonus_initiative(is_round_start=False)
        self._tick_buff_durations()
        self._tick_riposte_durations()
        self._tick_stealth_durations()
        self._tick_guard_durations()
        self._tick_mark_durations()
        self._tick_corpse_durations()
        self._tick_burn_zones()
        self._tick_enemy_skill_cooldowns()
        self._tick_mode_stress()
        self._check_stall()
        self.round += 1
        self._first_enemy_acted_this_round = False
        self._check_win_loss()

    def _advance_to_next_hero_turn(self):
        """Resolve turn_order entries — enemy actions, stun-recovery skips,
        dead/corpse skips — one at a time from current_turn_index, until a
        living hero's turn is reached or combat ends.

        One unified initiative queue per round (heroes and enemies
        interleaved by SPD+d8, matching the real game), consumed one entry
        per call rather than re-rolled on every hero action. Only recomputed
        once the whole queue has been consumed for the round.
        """
        while not self.done:
            if self.current_turn_index >= len(self.turn_order):
                self._end_of_round()
                if self.done:
                    return
                self._compute_turn_order()
                self._resolve_bonus_initiative(is_round_start=True)
                self._check_win_loss()
                if self.done:
                    return
                continue

            combatant = self.turn_order[self.current_turn_index]

            if combatant.is_dead or combatant.is_corpse:
                self.current_turn_index += 1
                continue

            # ── START of this combatant's turn: DoTs tick BEFORE it acts ──
            #
            # Real bug found and fixed: these ticks used to run AFTER the
            # action (and were skipped entirely on the stun path), so a unit
            # standing on lethal bleed/blight still got a full turn before
            # dying. In the real game the DoT lands as its turn comes up: an
            # enemy on 3 HP with 4 blight simply dies without acting, and a
            # hero in the same spot goes to Death's Door before acting.
            #
            # That difference is tactically load-bearing, not cosmetic — it's
            # what lets a player skip attacking a target that is already dead
            # to its DoT and spend the turn elsewhere, reaching the stall
            # phase a turn earlier. The damage is NOT double-counted: an
            # attack applies its own hit damage, and the DoT it inflicts is
            # separate and lands on the victim's own subsequent turns.
            #
            # Guarded by _dot_ticked_for_index because free actions
            # (provisions, Abomination's Transform) re-enter this loop on the
            # same turn index without the combatant having acted, which would
            # otherwise tick the same turn's DoT repeatedly.
            if self._dot_ticked_for_index != self.current_turn_index:
                self._dot_ticked_for_index = self.current_turn_index
                # Wiki-confirmed order (darkestdungeon.wiki.gg/wiki/Status_
                # Effects): Restoration (heal-over-time) resolves before
                # Bleed/Blight. Stress DoTs (Horror) don't interact with HP,
                # so their relative order here is arbitrary either way.
                #
                # Death's Door recovery is checked once, AFTER all three —
                # not inline inside _tick_heal_dots — so a heal_dot that
                # alone crosses above 0 HP can't prematurely flip a hero off
                # Death's Door only to have that turn's bleed/blight put them
                # straight back on it, which would skip the deathblow roll a
                # hero already on Death's Door owes for a net-negative turn
                # and double-fire enter/exit side effects.
                hp_before_ticks = combatant.hp
                self._tick_heal_dots(combatant)
                self._tick_dots(combatant)
                self._tick_stress_dots(combatant)
                if combatant.hp > 0 and combatant.is_deaths_door:
                    self._recover_from_deaths_door(combatant)
                # The game rolls a deathblow only on DAMAGE taken
                # (`Actor::vf50`: a hit at or under 0 damage survives), so
                # a hero resting on Death's Door with nothing ticking owes
                # no roll at its turn start. Unguarded, every such hero
                # rolled 33% each turn it took (changelog 2026-10-11).
                if combatant.hp < hp_before_ticks or not combatant.is_deaths_door:
                    self._check_deaths_door_dot(combatant)
                self._check_win_loss()
                if self.done:
                    return
                if not combatant.is_alive():
                    self.vlog(f"    [DOT-DEATH] {combatant.name} dies to damage-over-time "
                              f"before acting")
                    self.current_turn_index += 1
                    continue

            if combatant.is_stunned:
                # Stun prevents ACTING, not bleeding — the tick above already
                # ran, which is why stun+DoT compounds the way it does in the
                # real game (Plague Doctor stunning while Blight ticks).
                combatant.is_stunned = False
                self._apply_stun_recovery(combatant)
                # A stunned actor still had a turn, so its burn still ticks.
                self._end_of_turn_burn(combatant)
                if self.done:
                    return
                self.current_turn_index += 1
                continue

            if combatant.is_hero:
                return  # pause here — step() executes the submitted action next

            self._execute_enemy_turn(combatant)
            self._end_of_turn_burn(combatant)
            self.current_turn_index += 1
            self._check_win_loss()

    # ── Action validation ──

    @staticmethod
    def _named_effects(raw) -> list[str]:
        """Effect names from a `.effect` field, dropping the blanks.

        The `.darkest` parser returns `.effect ""` as `['']` and a single
        named effect as a bare `str`, so neither `if raw:` nor iteration alone
        is safe here."""
        if not raw:
            return []
        items = [raw] if isinstance(raw, str) else list(raw)
        return [e for e in items if isinstance(e, str) and e.strip()]

    def _performer_hp_allows(self, performer: CombatantState,
                             sd: SkillLevelData) -> bool:
        """`.required_performer_hp_range`, a fraction of max HP.

        Two skills in the game, both the Flagellant's: Redeem and Exsanguinate
        are `0 0.40`, unusable until he is under 40% HP. Parsed and unread
        until 2026-08-21, so the policy could open with either at full health
        -- a line the real game does not offer, and one that inverts the class:
        his payoff skills are supposed to be what he EARNS by bleeding.
        """
        rng_ = sd.required_performer_hp_range
        if rng_ is None or performer.max_hp <= 0:
            return True
        frac = performer.hp / performer.max_hp
        return rng_[0] <= frac <= rng_[1]

    def _is_wasted_heal(self, sd, positions: list[int]) -> bool:
        """True for a heal that cannot restore a single point of HP.

        A pure heal aimed only at heroes already at full HP is **strictly
        dominated**: it restores nothing, carries no other effect, and costs
        the turn. The real game permits it, so this is a deliberate narrowing
        of the legal set rather than a fidelity fix — the same argument as any
        other dominated action, and it removes a move no competent player ever
        makes.

        Measured cause: the trained policy spent 21.2% of its skill turns
        healing and **17.7% of those landed on a target already at full HP**.
        Seen from real play first — a Vestal healing herself at 34/34 while
        the Man-at-Arms stood at 16/27.

        Deliberately narrow, so nothing legitimate is removed:
        * only skills whose *sole* payload is healing — anything carrying an
          effect (a buff, a stress heal, a cure) stays legal even at full HP;
        * for an AoE heal, every position it would land on must be full;
        * a hero on Death's Door is never "full", whatever the HP reads."""
        if sd is None or sd.heal_min is None:
            return False
        # `.effect ""` parses to [''], NOT to an empty list — a plain truthiness
        # test reads that as "this skill has effects" and silently disables
        # this whole guard. Vestal's Divine Grace and Divine Comfort are both
        # written that way, which is to say the two skills it most exists for.
        if self._named_effects(sd.effects) or self._named_effects(sd.self_effects):
            return False
        for pos in positions:
            target = next((h for h in self.heroes
                           if h.position == pos and h.is_alive()), None)
            if target is None:
                continue
            if target.is_deaths_door or target.hp < target.max_hp:
                return False
        return True

    # -- Controlled Burn's rank ZONES (2026-08-27) --------------------------
    #
    # A persistent effect attached to a RANK rather than to a combatant, which
    # is machinery this engine had none of. It is what makes Controlled Burn
    # pair with pull and push: Fran, *"controlled burn works great with
    # pull/push comps ... making all the enemies that ocupy that rank gain burn
    # damage"*. Painting rank 2 and then dragging three different monsters
    # through it burns all three.
    #
    # Held as `{rank: [amount, rounds_left]}` on the ENEMY side only -- all five
    # shipped effects are the Runaway's and target enemies, and a hero-side zone
    # would need its own answer to "what happens when the party re-forms".
    # Re-painting the same rank REFRESHES it rather than stacking a second
    # zone, which is what a duration on a place can mean.





    @staticmethod
    def _can_damage_corpse(sd: SkillLevelData) -> bool:
        """Whether offering this skill against a corpse is worth an action.

        Deliberately narrower than the real game, on the same "strictly
        dominated" argument as _is_wasted_heal: everything a corpse resists is
        resisted at 200% (stun, debuff, move), it never takes a turn so a bleed
        or blight on it never ticks, and `crit: .is_valid_effects_target false`
        rules out the rest. So a skill with no damage payload (`.dmg -100%`,
        e.g. Plague Doctor's Blinding Gas) can do literally nothing to a corpse,
        and offering it would only widen the action space with dead moves."""
        return sd.heal_min is None and sd.dmg_mod > -1.0

    def get_valid_actions(self, hero_id: int) -> list[Action]:
        hero = next((h for h in self.heroes if h.id == hero_id and h.is_alive()), None)
        if not hero:
            return []
        skill_ids = self.get_hero_skill_ids(hero_id)
        skill_data_map = {}
        for sk_id in skill_ids:
            sd = self.get_skill_data(sk_id, level=hero.skill_tier)
            if sd:
                skill_data_map[sk_id] = sd

        # EVERY rank a monster stands on, not just its front one. A size-2+
        # monster is reachable from either of the ranks it covers in the real
        # game, and offering only the front one made a skill whose .target is
        # exactly that monster's BACK rank illegal against it — up to "no legal
        # target at all" for a rank-3-only skill facing a large enemy at
        # ranks 2-3. 61 of 246 loaded monsters are size 2+, so this is not an
        # edge case. Found from live play: the advisor named a rank-4 Brigand
        # Fusilier as rank 3 because a size-2 Bloodletter was holding ranks
        # 1-2 and every rank downstream of it was being counted one short.
        enemy_positions = sorted({p for e in self.enemies if e.is_alive()
                                  and self._can_be_damaged(e)
                                  for p in e.occupied_positions})
        # A corpse is a legal target for anything that deals damage. It was not
        # for the whole life of this project, because the pool above filters on
        # is_alive() and `is_alive() == not is_dead and not is_corpse` — so no
        # hero was ever OFFERED an attack on a corpse, while the real game gives
        # it a stat block (7 HP, `.can_be_missed False`) precisely so you can
        # destroy it. Raised from live play: with one enemy left behind a
        # corpse, breaking the corpse collapses the line and pulls the survivor
        # into reach, a play the simulator could not express.
        corpse_positions = sorted({p for e in self.enemies if e.is_corpse
                                   for p in e.occupied_positions})
        ally_positions = [h.position for h in self.heroes if h.is_alive() and h.id != hero_id]
        # **A stealthed enemy cannot be picked out by a single-target skill**
        # (Fran, 2026-09-28) -- the same rule the monster side has always had
        # for stealthed heroes (`_monster_ai`), which the hero side lacked:
        # a Brigand Fusilier, Crone or Fishman Shaman that stealthed itself
        # stayed a legal single target. AoE still hits it, a skill with
        # `ignore_stealth` (Tracking Shot) still picks it, and when EVERY
        # living enemy is stealthed the rule lifts, as the monster side's does.
        living = [e for e in self.enemies if e.is_alive()]
        stealthed_positions = {p for e in living if e.is_stealthed
                               for p in e.occupied_positions}
        if living and all(e.is_stealthed for e in living):
            stealthed_positions = set()
        valid = []
        for sk_id in skill_ids:
            sd = skill_data_map.get(sk_id)
            if not sd:
                continue
            if sd.launch and (hero.position + 1) not in sd.launch:
                continue
            if not self._is_valid_mode(hero, sd):
                # Real gap found and fixed alongside the action-masking
                # work: this was never checked here, only at execution
                # time (_check_valid_mode) -- so a mode-restricted skill
                # (Abomination's human-only/beast-only skills) was offered
                # as a legal action while in the wrong mode, then silently
                # rejected on selection, wasting the whole turn (logged as
                # "cannot use that skill in X mode") exactly like the
                # index-clamping pathology action_masks() fixes elsewhere.
                continue
            if not self._has_skill_uses_remaining(hero, sk_id, sd):
                # Same bug, same fix pattern, for per_battle_limit/
                # per_turn_limit -- a maxed-out skill was still offered
                # here, silently rejected at execution time. 13 hero
                # skills have a battle limit (see
                # _has_skill_uses_remaining); only Transform has a turn
                # limit.
                continue
            if not sd.target:
                valid.append(Action(hero_id, sk_id, -1))
                continue
            if not self._performer_hp_allows(hero, sd):
                continue
            if self._is_ally_skill(sk_id):
                # The performer's own rank is in the pool because every hero
                # ally skill's `.target` carries the `@` prefix -- but
                # `.self_target_valid false` then takes the performer back
                # out. Seven hero skills say so: the three guard skills, which
                # would otherwise guard their own caster, and the Flagellant's
                # Reclaim/Redeem/Endure/Suffer, each of which pairs an effect
                # on the ALLY with a self-effect on him.
                target_pool = ally_positions + (
                    [hero.position] if sd.self_target_valid else [])
            elif self._can_damage_corpse(sd):
                target_pool = enemy_positions + corpse_positions
            else:
                target_pool = enemy_positions
            if sd.requires_burning:
                # Backdraft only exists against something already on fire. The
                # gate goes in BOTH places -- here and in
                # `_resolve_action_targets` -- for the reason the corpse rule
                # documents: an action offered because a target qualified, then
                # re-resolved to nothing at execution, silently burns the turn.
                target_pool = [pos for pos in target_pool
                               if self._is_burning_at(pos)]
            if (stealthed_positions and not sd.is_aoe
                    and not self._is_ally_skill(sk_id)
                    and not getattr(sd, "ignore_stealth", False)):
                target_pool = [pos for pos in target_pool
                               if pos not in stealthed_positions]
            candidates = sorted(set(p - 1 for p in sd.target if (p - 1) in target_pool))
            if not candidates:
                continue
            if sd.is_aoe:
                # One action represents "use this AoE skill" — it hits every
                # occupied position in sd.target (re-resolved at execution
                # time by _resolve_action_targets), not just whichever
                # position target_id names. target_id is kept as the first
                # candidate purely so downstream code that expects a
                # non-negative target_id (e.g. action validity checks) still
                # sees one.
                if self._is_wasted_heal(sd, candidates):
                    continue
                valid.append(Action(hero_id, sk_id, candidates[0]))
            else:
                for t in candidates:
                    if self._is_wasted_heal(sd, [t]):
                        continue
                    valid.append(Action(hero_id, sk_id, t))

        # Voluntary reposition — real per-hero combat_move_skill data
        # (backward/forward distance), a genuine turn-costing action
        # alongside skills, not a free modifier.
        #
        # **A move is a SWAP with the ally in the destination rank**, which
        # makes an occupied rank the only legal destination rather than the
        # only illegal one. The game says so in its own words: the option
        # `menu_options_element_tooltip_hold_required_in_dungeon` is about
        # "swap hero positions". It also follows from the formation rule this
        # engine already enforces everywhere else — the party is contiguous
        # from rank 1 (`_collapse_hero_position_gap`), so there is no empty
        # rank to move INTO, and a move that left one would contradict it.
        #
        # This was inverted (`if p in occupied: continue`) and the inversion
        # broke the action in both directions at once:
        #   * a FULL party could not move at all — every rank is occupied, so
        #     every candidate was skipped. Move has been dead in every
        #     four-hero combat this project ever simulated.
        #   * an UNDER-STRENGTH party could only move into the ranks its dead
        #     used to hold. A live 2-hero tutorial party offered its
        #     Highwayman rank 3 and rank 4, the bridge clicked rank 4, and the
        #     game ignored it because nothing is there: "it tried to move to
        #     an empty position, as there is no way to move to a slot that
        #     exceeds your party size".
        # The party-size bound is not a second clamp — it falls out of
        # requiring an occupant, which is why the old `min(3, ...)` on `hi` is
        # gone with it.
        hd = self._get_hero_def(hero_id)
        if hd and hd.move_skill and (hero.position + 1) in hd.move_skill.launch:
            swappable = {h.position for h in self.heroes
                         if h.is_alive() and h.id != hero_id}
            # Real bug found and fixed: these two were inverted. position 0 is
            # rank 1 (the front), so moving FORWARD (toward the enemy) means
            # DECREASING the index and moving BACKWARD (retreating) means
            # increasing it — the old code had backward decrease and forward
            # increase, i.e. exactly backwards.
            #
            # Confirmed against two classes whose real .move data makes the
            # correct behaviour unambiguous, both matching a measured
            # pathology:
            #   * Hellion (.move 0 1 = 0 back / 1 forward) is a rank-1
            #     bruiser who should be able to advance but never retreat.
            #     Inverted, she could ONLY drift toward rank 4 and never come
            #     back — and she was observed passing (no legal action at all)
            #     on 18% of her turns, stranded in the back ranks.
            #   * Musketeer/Arbalest (.move 2 0 = 2 back / 0 forward) are
            #     snipers who should retreat 2 and never advance. Inverted, a
            #     Musketeer pushed to rank 1 computed lo=max(0,0-2)=0,
            #     hi=min(3,0+0)=0 — literally no legal move — and she was
            #     observed passing on 39% of her turns.
            lo = max(0, hero.position - hd.move_skill.forward)
            hi = hero.position + hd.move_skill.backward
            for p in range(lo, hi + 1):
                if p == hero.position or p not in swappable:
                    continue
                valid.append(Action(hero_id, "__move__", p))

        # Free (turn-preserving) provisions — self-targeted only in v1,
        # gated by whether the hero actually has the condition it treats
        # (bandage/antivenom; holy_water/laudanum have no precondition,
        # matching the real game's "proactive use" items).
        for pid in (IN_COMBAT_PROVISION_IDS if IN_COMBAT_PROVISIONS_ALLOWED
                    else ()):
            if self.provision_inventory.get(pid, 0) <= 0:
                continue
            if pid == "bandage" and not hero.bleeds:
                continue
            if pid == "antivenom" and not hero.blights:
                continue
            # Same "don't offer a cure for a condition you don't have" gate as
            # bandage/antivenom above, which laudanum and holy_water were
            # missing. Provisions are FREE actions (turn-preserving), so they
            # cost nothing at the moment of use while their real cost — a
            # finite inventory that has to last 8-18 combats — is deferred and
            # invisible. Measured on the trained policy: 82% of all provision
            # uses were wasted, 274 of 278 laudanum uses with no Horror to
            # cure at all, plus 68 holy_waters re-applied over an already
            # active buff. A player would not drink laudanum with no Horror.
            if pid == "laudanum" and not hero.stress_dots:
                continue
            if pid == "holy_water" and any(
                    b.buff_id.startswith("holy_water") for b in hero.buffs):
                continue
            valid.append(Action(hero_id, f"__provision_{pid}__", hero.position))

        # Break off the fight. Always legal on a hero's turn, which is what the
        # real game does — the retreat button is never greyed out in a normal
        # encounter. Offered ALONGSIDE the real options rather than as a
        # fallback, because unlike Pass it is a decision and not a last resort:
        # the whole point of modelling it is that a party which cannot leave a
        # losing fight overstates how dangerous the dungeon is.
        # ...unless the party was surprised, which locks the door for the
        # opening round. The wiki states it as a property of surprise rather
        # than of the retreat roll, so it is a legality gate here and not a
        # chance penalty in attempt_retreat.
        if not (self.party_surprised and self.round == 0):
            valid.append(Action(hero_id, "__retreat__", -1))

        # Last-resort fallback — only ever offered when nothing else is
        # legal (e.g. a boxed-in backline hero with no reachable skill and
        # no open Move rank), never alongside a real skill/Move option.
        if len(valid) == 1:
            valid.append(Action(hero_id, "__pass__", -1))

        return valid

    def submit_action(self, action: Action):
        self._action_buffer = action

    # ── Main step ──

    def step(self) -> StepResult:
        if self.done:
            return StepResult(done=True)

        if self._action_buffer is not None:
            action = self._action_buffer
            performer = self._find_combatant(action.hero_id)
            skill_data = None
            if performer and not action.skill_id.startswith("__"):
                skill_data = self.get_skill_data(action.skill_id, level=performer.skill_tier)
            # Provisions AND any skill with .is_continue_turn (real data:
            # only Abomination's Transform — "FREE ACTION, does not end
            # your turn" per the wiki, letting him switch Human/Beast and
            # still attack the same turn) preserve the hero's turn instead
            # of ending it. Previously is_continue_turn was parsed but
            # never consulted anywhere, so Transform silently cost a whole
            # turn like any other skill — a real behavioral gap for a
            # mechanic the game explicitly calls out as free.
            is_free_action = action.skill_id.startswith("__provision_") or (
                skill_data is not None and skill_data.is_continue_turn)
            if performer:
                self._sync_all_conditional_buffs(
                    performer=performer,
                    skill_id=action.skill_id if skill_data else None)
            # An afflicted hero may refuse the submitted action outright.
            # Checked before execution and before the free-action branch, so
            # an act-out always burns a real turn — otherwise a hero could
            # dodge the whole mechanic by chaining provisions/Transform.
            if performer is not None and self._maybe_act_out(performer):
                is_free_action = False
            else:
                self._bonus_action_pending = False
                self._execute_action(action)
                if self._bonus_action_pending:
                    # Duelist's Coup de Grace killed its target. Joins
                    # provisions and `.is_continue_turn` on the free-action
                    # path: the turn simply does not end.
                    is_free_action = True
                    self._bonus_action_pending = False
            self._action_buffer = None
            if not is_free_action:
                # Free (provision/continue-turn) actions deliberately skip
                # DoT-tick/deaths-door-check/turn-advance — the real game
                # lets you use a bandage (or Transform) without ending
                # your turn, so the same hero is re-offered
                # get_current_actor_id() immediately (bounded: finite
                # inventory / a 2-per-battle limit, no infinite-loop risk).
                # Running these on every free use too would double-tick
                # DoTs whenever a hero acts again in the same visit.
                # DoTs are NOT ticked here any more — they land at the
                # START of a combatant's turn (_advance_to_next_hero_turn),
                # so a hero standing on lethal bleed goes to Death's Door
                # before acting rather than after.
                # Burn ticks HERE and not with the other DoTs, because
                # it lands at the END of the turn -- see _tick_burn.
                if performer is not None:
                    self._end_of_turn_burn(performer)
                if self.current_turn_index < len(self.turn_order):
                    self.current_turn_index += 1
            self._check_win_loss()

        if not self.done:
            self._advance_to_next_hero_turn()

        return StepResult(
            obs=self._get_obs(),
            done=self.done,
            info={"winner": self.winner, "round": self.round, "log": self.log[-20:]},
        )

    # ── Action execution ──

    def _execute_action(self, action: Action):
        performer = self._find_combatant(action.hero_id)
        if not performer or not performer.is_alive():
            return
        if action.skill_id == "__move__":
            if self._reaction_blocks(performer, "block_move"):
                return
            # A swap, not a teleport. Writing the new position alone left the
            # vacated rank empty and the destination doubly occupied, which
            # the formation rules forbid everywhere else in this file. The
            # partner is re-looked-up here rather than carried on the Action
            # because the action was legalised at `get_valid_actions` time and
            # a DoT or a riposte can have killed the ally since.
            partner = next((h for h in self.heroes
                            if h.is_alive() and h.id != performer.id
                            and h.position == action.target_id), None)
            if partner is None:
                self.log.append(
                    f"{performer.name} cannot move to {action.target_id + 1} "
                    f"— nobody there to swap with")
                return
            self.log.append(f"{performer.name} swaps from "
                            f"{performer.position + 1} to {action.target_id + 1} "
                            f"with {partner.name}")
            performer.position, partner.position = partner.position, performer.position
            return
        if action.skill_id == "__pass__":
            self.log.append(f"{performer.name} passes the turn")
            self._apply_stress(performer, PASS_TURN_STRESS)
            return
        if action.skill_id == "__retreat__":
            if self._reaction_blocks(performer, "block_combat_retreat"):
                return
            self.attempt_retreat(performer)
            return
        if action.skill_id.startswith("__provision_"):
            pid = action.skill_id[len("__provision_"):-2]
            if self._reaction_blocks(performer, "block_item"):
                return
            self._use_provision(pid, performer)
            return
        skill_data = self.get_skill_data(action.skill_id, level=performer.skill_tier)
        if not skill_data:
            return

        if not self._check_skill_limits(performer, action.skill_id, skill_data):
            return

        if not self._check_valid_mode(performer, skill_data):
            return

        if action.target_id < 0:
            self._resolve_self_action(performer, action.skill_id, skill_data)
            return

        is_ally_skill = self._is_ally_skill(action.skill_id)
        targets = self._resolve_action_targets(action, skill_data, is_ally_skill)
        if not targets:
            return

        is_heal = skill_data.heal_min is not None and skill_data.heal_max is not None

        if is_heal:
            # Real bug found and fixed: a heal skill's own .effect list was
            # silently dropped entirely -- _resolve_heal only ever applied
            # the bare .heal amount, never the skill's named effects. Several
            # real heal skills carry their MAIN value there, not in .heal:
            # Crusader's inspiring_cry's actual stress-heal ("Crusader
            # HealStress N") and torch gain ("Crusader Light N") are both
            # named effects -- its bare .heal is a token 1-2 HP, so before
            # this fix Inspiring Cry only ever gave that trivial HP tick and
            # never relieved any stress at all, despite being the party's
            # primary stress healer in most comps that carry a Crusader.
            # Also affects Plague Doctor's battlefield_medicine ("Cure"/
            # "Cureself" -- clears bleed/blight), Antiquarian's
            # fortifying_vapours ("Fortify Resists N"), Arbalest's
            # battlefield_bandage ("Arb Stacking Heal N"), and Occultist's
            # wyrd_reconstruction ("Wyrd Bleed N" -- the real risky-heal
            # bleed chance). Fixed by applying effects/movement the same
            # way the ally-skill branch below already does.
            group_names, per_target_names = self._split_group_effect_names(skill_data)
            is_multi_target = len(targets) > 1
            for target in targets:
                if (target is not performer
                        and self._reaction_blocks(target, "block_heal")):
                    continue
                self._resolve_heal(performer, target, skill_data, is_multi_target=is_multi_target)
                self._apply_skill_effects(target, performer, skill_data, hit=True, effect_names=per_target_names)
                self._apply_movement(performer, target, skill_data)
            if group_names:
                self._apply_skill_effects(performer, performer, skill_data, hit=True, effect_names=group_names)
            self._apply_self_effects(performer, skill_data)
        elif is_ally_skill:
            # Pure ally support/buff skill -- heals via a named effect
            # instead of the skill's own bare .heal field, if it heals at
            # all (e.g. Flagellant's redeem/reclaim -> "Flag Super Heal"/
            # "Flag Healdot", Jester's battle_ballad -> "Embolden Team").
            # Real bug found and fixed: these used to fall through to the
            # enemy-attack path below, resolving a real to-hit roll and
            # the performer's own weapon damage against each ally target
            # (including the performer itself, for AoE/self-inclusive
            # skills) as an unintended side effect of what should be a
            # harmless buff -- confirmed via full-expedition replay:
            # "Jester hits Jester for 102" from battle_ballad (real .dmg
            # is 0%, which means NO damage reduction at all, not "-100%"),
            # and Flagellant's "suffer" landing real attack rolls against
            # itself ("Flagellant misses Flagellant"). No attack roll, no
            # Riposte, no Guard redirect here -- none of those make sense
            # against your own ally.
            group_names, per_target_names = self._split_group_effect_names(skill_data)
            for target in targets:
                if (target is not performer
                        and self._reaction_blocks(target, "block_buff")):
                    continue
                self._apply_skill_effects(target, performer, skill_data, hit=True, effect_names=per_target_names)
                self._apply_movement(performer, target, skill_data)
            if group_names:
                self._apply_skill_effects(performer, performer, skill_data, hit=True, effect_names=group_names)
            self._apply_self_effects(performer, skill_data)
        else:
            attacking_from_stealth = performer.is_stealthed
            crit_bonus = self._resolve_named_buff_amount(
                "stealthed_crit_chance_buff", "combat_stat_add", "crit_chance_add"
            ) if attacking_from_stealth else 0.0
            performer.crit += crit_bonus

            # Real bug found and fixed: a handful of AoE attack skills
            # against the enemy side carry one effect that redirects to
            # the whole ALLY side via its own .target ("performer_group_
            # other"/"performer_group") -- e.g. Arbalest/Musketeer's
            # Rallying Flare ("flare"), whose "FlareHealStress N" is a
            # small party stress heal bundled alongside its per-enemy
            # Destealth. Applying it inside this per-AoE-target loop meant
            # it fired once per enemy actually hit -- confirmed via replay
            # against 3 living enemies: a single Rallying Flare use healed
            # anywhere from 0 to 12 stress depending on hit RNG and
            # FlareHealStress's own chance roll, instead of the flat
            # "small Stress heal" the real skill is (HEROES/Heroes/
            # Arbalest doc). Split out and applied once per action instead
            # (gated on any_hit, matching "on hit" semantics), same
            # principle _apply_self_effects already uses below.
            group_names, per_target_names = self._split_group_effect_names(skill_data)
            any_hit = False
            # One to-hit roll for the whole skill use, compared against each
            # target's own chance -- see _shared_hit_roll.
            shared_hit_roll = self._shared_hit_roll(targets)
            # `self.fate` is the GAME's known draws for this hero action
            # (bridge/fate.py, DECOMP.md F15). None everywhere but the live
            # advisor's look-ahead, so the stream below is untouched by default.
            fate = getattr(self, "fate", None) if performer.is_hero else None
            if fate is not None:
                shared_hit_roll = fate.hit_roll
                self.fate = None                     # one action's worth only
            first_target = True
            for target in targets:
                if target.is_corpse:
                    # Damage and nothing else. A corpse cannot be guarded
                    # (`.can_be_guarded False`), cannot be missed, riposte
                    # nothing, relieves no stress on a crit or a killing blow
                    # (`.can_relieve_stress_from_crit/_killing_blow False`), and
                    # resists every applicable status at 200% while never taking
                    # a turn for a DoT to tick on. Routing it through the full
                    # per-target machinery below would invent all of that.
                    self._strike_corpse(performer, target, skill_data,
                                        action.skill_id, shared_hit_roll)
                    continue
                if not is_ally_skill and not skill_data.ignore_guard:
                    # `.ignore_guard` -- Jester `dirk_stab`, Runaway
                    # `backdraft`, Shieldbreaker `break_guard`. The attack
                    # lands on who it was aimed at even with a guardian
                    # standing in front, which for `break_guard` is the entire
                    # point of the skill.
                    target = self._resolve_guard_redirect(target, is_aoe=skill_data.is_aoe)
                damage_mult = self._compute_mark_damage_mult(performer, target, skill_data)
                acc_bonus = (self.consecutive_misses.get(performer.id, 0) *
                             self.rules.consecutive_miss_reduction_acc_buff)
                known = fate is not None and first_target
                first_target = False
                result = resolve_attack(performer, target, skill_data, self.rules, self.rng,
                                          damage_mult, acc_bonus, hit_roll=shared_hit_roll,
                                          crit_roll=fate.crit_roll if known else None,
                                          damage_u=fate.damage_u if known else None)
                self._vlog_attack(performer, target, action.skill_id, skill_data, result)

                if result.hit:
                    any_hit = True
                    if result.blocked_by_aegis:
                        self.log.append(f"{target.name}'s Aegis Block absorbs {performer.name}'s hit!")
                    self.log.append(f"{performer.name} hits {target.name} for {result.applied_damage}" +
                                  (" (crit!)" if result.is_crit else ""))
                    self._copy_burn_behind(target, skill_data)
                    self.consecutive_misses[performer.id] = 0
                    if result.is_crit:
                        if performer.is_hero:
                            self._stress_relief_performer(performer)
                            self._trigger_on_crit_passive(performer)
                        else:
                            self._apply_stress(target, self.rules.monster_crit_stress_damage)
                            self._apply_stress_to_allies(
                                target, self.rules.monster_crit_companion_stress_chance,
                                self.rules.monster_crit_companion_stress_damage, exclude=target,
                            )
                    if result.target_deaths_door:
                        self._handle_deaths_door(target, performer, is_crit=result.is_crit)
                else:
                    self.log.append(f"{performer.name} misses {target.name}")
                    self.consecutive_misses[performer.id] = self.consecutive_misses.get(performer.id, 0) + 1

                self._maybe_trigger_riposte(target, performer, result.hit)
                self._apply_skill_effects(target, performer, skill_data, result.hit,
                                            effect_names=per_target_names, is_crit=result.is_crit)
                if result.hit and target.is_alive():
                    self._apply_trinket_attack_procs(performer, target, skill_data,
                                                     is_crit=result.is_crit)
                self._apply_movement(performer, target, skill_data)

            if group_names:
                self._apply_skill_effects(performer, performer, skill_data, any_hit, effect_names=group_names)

            performer.crit -= crit_bonus

            if attacking_from_stealth:
                performer.is_stealthed = False
                performer.stealth_rounds_remaining = None
                self.log.append(f"{performer.name} breaks stealth")

            # Once per action regardless of how many targets an AoE hit —
            # self-buffs from using the skill aren't multiplied per target.
            self._apply_self_effects(performer, skill_data)

        self._track_skill_use(performer, action.skill_id, skill_data)

    def _strike_corpse(self, performer: CombatantState, corpse: CombatantState,
                        skill_data: SkillLevelData, skill_id: str,
                        shared_hit_roll: Optional[float]):
        """One damaging hit on a corpse. Always lands (see resolve_attack's
        is_corpse branch); destroys the corpse and collapses the enemy line the
        moment its 7 HP run out."""
        result = resolve_attack(performer, corpse, skill_data, self.rules, self.rng,
                                 hit_roll=shared_hit_roll)
        self._vlog_attack(performer, corpse, skill_id, skill_data, result)
        self.log.append(f"{performer.name} strikes {corpse.name}'s corpse for "
                        f"{result.applied_damage}")
        if corpse.hp <= 0:
            self._destroy_corpse(corpse)

    def _resolve_action_targets(self, action: Action, skill_data: SkillLevelData,
                                  is_ally_skill: bool) -> list[CombatantState]:
        """AoE skills (is_aoe) hit every currently-alive combatant in
        skill_data.target's ranks, re-checked at execution time (not the
        stale candidate list from when get_valid_actions built the action —
        the board can change between then and now). Single-target skills
        just resolve the one position the action specifies.
        """
        pool = self.heroes if is_ally_skill else self.enemies
        # Corpses are targets too, for anything that damages — and the AoE case
        # is the one that must stay in step with get_valid_actions, since an AoE
        # action is re-resolved here from scratch. Offering an AoE whose only
        # candidate rank held a corpse and then resolving it to zero targets
        # would silently burn the turn.
        hits_corpses = not is_ally_skill and self._can_damage_corpse(skill_data)

        def reachable(c: CombatantState) -> bool:
            if not is_ally_skill and not self._can_be_damaged(c):
                return False
            # `getattr`, because tests build stub skill objects here and a
            # stub predating this field must not crash target resolution.
            if getattr(skill_data, "requires_burning", False) and not (
                    c.is_alive() and c.burn_stacks > 0):
                # The same gate `get_valid_actions` applies. Both, always: the
                # board can change between the two -- a DoT can put the only
                # burning target down -- and an action that re-resolves to
                # nothing burns the turn in silence.
                return False
            return c.is_alive() or (hits_corpses and c.is_corpse)

        if skill_data.is_aoe:
            positions = {p - 1 for p in skill_data.target}
            # Matched against every rank the target covers: an AoE over ranks
            # 3-4 hits a size-2 monster standing on ranks 2-3. Still at most
            # once each — one CombatantState cannot appear twice in a list
            # comprehension over `pool`.
            return [c for c in pool if reachable(c)
                    and not positions.isdisjoint(c.occupied_positions)]
        target = self._find_combatant_by_position(action.target_id, enemy=not is_ally_skill,
                                                  include_corpses=hits_corpses)
        # The single-target path runs `reachable` too. Backdraft is
        # single-target, so gating only the AoE branch left `requires_burning`
        # unenforced exactly where it mattered.
        return [target] if target is not None and reachable(target) else []

    def _resolve_self_action(self, performer: CombatantState, skill_id: str, skill: SkillLevelData):
        if skill.heal_min is not None:
            heal_amt = self.rng.randint(skill.heal_min, skill.heal_max)
            applied = self._apply_heal(performer, heal_amt, performer, crit_eligible=performer.is_hero)
            self.log.append(f"{performer.name} heals self for {applied}")
        # Self-targeted skills (empty .target) list their effects under
        # `.effect` same as attack skills — the game format has no separate
        # "self effect" key. `self_effects` is always empty (hero_loader never
        # populates it), so without this call skills like Shadow Fade's
        # stealth_self would never apply.
        self._apply_skill_effects(performer, performer, skill, hit=True)
        self._apply_self_effects(performer, skill)
        # A stance change: apply whichever list matches the performer's
        # CURRENT mode. The Abomination's transform is the vanilla case --
        # human_effects includes "switch_mode_beast_self" (which flips
        # performer.mode via eff.set_mode) and beast_effects includes
        # "switch_mode_human_self" -- but the convention is one
        # `.<mode>_effects` per `.valid_modes` entry, and the Duelist's
        # Anticipation uses `.stance_defensive_effects` /
        # `.stance_aggressive_effects` the same way. Looking the mode up by
        # name covers both; the old `human if ... else beast` sent every
        # Duelist stance change down the beast branch and found nothing.
        # Distinct from the regular .effect list, which these skills don't use.
        mode_effects = skill.mode_effects.get(performer.mode, [])
        for ename in mode_effects:
            eff = self.store.effects.get(ename)
            if eff:
                self._resolve_effect(performer, performer, eff, skill)
        self._apply_movement(performer, performer, skill)
        self._track_skill_use(performer, skill_id, skill)

    _MARKED_DMG_CACHE: dict[str, float] = {}

    def _compute_mark_damage_mult(self, attacker: CombatantState, target: CombatantState,
                                    skill: SkillLevelData) -> float:
        # Real .keyStatus values found in base.effects.darkest: "tagged" (35,
        # e.g. Grave Robber's "GR Dagger Dmg Marked N"/Bounty Hunter's "BH
        # Dmg Marked"), "poisoned" (6, "PoisonKiller"/Grave Robber's "Poison
        # Killer N"), "bleeding" (1, "BleedKiller"), "stunned" (5, "Stun
        # Killer 1..5") -- all the same "performer" self-buff-damage-vs-a-
        # status shape, just keyed to a different target status. Previously
        # hardcoded to "tagged" only (an early `if not target.is_marked:
        # return 1.0` short-circuit skipped evaluating a skill's other
        # keyStatus-gated effects entirely) -- Grave Robber's Thrown Dagger
        # bundles a "GR Dagger Dmg Marked N" (tagged) AND a "Poison Killer N"
        # (poisoned) effect together, so the latter's bonus damage vs a
        # blighted target silently never applied even when correctly
        # implemented for Marked. Reuses _actor_has_status, the same status-
        # lookup helper the buff-level actorStatus rule_type uses.
        mult = 1.0
        for ename in skill.effects:
            eff = self.store.effects.get(ename)
            if not eff:
                continue
            # Real .monsterType field (25 occurrences: "man"/"beast"/
            # "unholy"/"eldritch") -- same "performer" conditional-damage-
            # bonus shape as key_status, but gated on the target's
            # PERMANENT monster-type tag (EnemyDefinition.enemy_types)
            # instead of a transient status. Bounty Hunter's "Man Killer N"
            # (collect_bounty's real "+DMG vs Human") was previously
            # completely inert -- .monsterType was parsed nowhere at all.
            matches_status = eff.key_status and self._actor_has_status(target, eff.key_status)
            matches_type = (eff.monster_type_condition and not target.is_hero
                             and (ed := self._get_enemy_def(target.id))
                             and eff.monster_type_condition in ed.enemy_types)
            if matches_status or matches_type:
                # Real bug found and fixed: .damage_low_multiply and
                # .damage_high_multiply are ONE percentage bonus applied to
                # the low and high ends of a damage range -- not two separate
                # multipliers to compound. Verified across the whole install:
                # all 192 effects carrying both fields have IDENTICAL values.
                # Multiplying them together squared every conditional damage
                # bonus, so Houndmaster's +90% vs Marked became 1.9 x 1.9 =
                # 3.61x (+261%), and Bounty Hunter's Collect Bounty likewise.
                # Caught in a verbose battle log reading
                # "[DMG] roll 10.8 in [6-11] x conditional 3.61 = 39".
                # This engine rolls one scalar from dmg_min..dmg_max and then
                # scales it, so the bonus applies exactly once.
                bonus = eff.damage_low_multiply
                if bonus is None:
                    bonus = eff.damage_high_multiply
                if bonus is not None:
                    mult *= (1.0 + bonus)
        return mult

    def _resolve_heal(self, performer: CombatantState, target: CombatantState, skill: SkillLevelData,
                       is_multi_target: bool = False):
        heal_amt = self.rng.randint(skill.heal_min, skill.heal_max)
        applied = self._apply_heal(target, heal_amt, performer,
                                    crit_eligible=performer.is_hero, is_multi_target=is_multi_target)
        self.log.append(f"{performer.name} heals {target.name} for {applied}")

    def _apply_heal(self, target: CombatantState, heal_amt: int,
                     performer: Optional[CombatantState] = None,
                     check_recovery: bool = True,
                     crit_eligible: bool = False,
                     is_multi_target: bool = False) -> int:
        # hp_heal_percent (performer, e.g. on_crit_healbuff +25%) and
        # hp_heal_received_percent (target, e.g. HPRECOVERY10/-10) are real
        # buff stat_types verified in base.buffs.json — both scale the final
        # applied amount, not just the roll.
        produced_mult = performer.hp_heal_produced_mult if performer is not None else 1.0
        heal_amt = max(0, round(heal_amt * produced_mult * target.hp_heal_received_mult))

        # Heal crit — real, hero-only mechanic found via the wiki extract
        # (see HEAL_CRIT_CHANCE_SINGLE_TARGET above); previously entirely
        # unmodeled, no heal ever crit regardless of how the wiki describes
        # it. crit_eligible is opt-in per call site (defaults False) rather
        # than inferred from `performer.is_hero` here, because performer
        # also drives produced_mult and some callers (_tick_heal_dots) must
        # NOT pass a performer — a heal_dot has no caster reference at tick
        # time (DotInstance doesn't track one) — while still wanting the
        # crit roll gated on the RECEIVING hero (Reclaim always self-heals).
        # `performer is None or performer.is_hero` is a belt-and-suspenders
        # re-check, not the primary gate — every real call site already
        # computes crit_eligible from a hero check of its own — so a
        # monster heal can never crit-heal even if some future caller
        # passes crit_eligible=True by mistake.
        if (crit_eligible and heal_amt > 0 and (performer is None or performer.is_hero)
                and self.rng.random() < (
                HEAL_CRIT_CHANCE_PER_TARGET if is_multi_target else HEAL_CRIT_CHANCE_SINGLE_TARGET)):
            heal_amt *= 2
            target.stress = max(0.0, target.stress - HEAL_CRIT_STRESS_REDUCTION)
            self._maybe_clear_overstress(target)
            self.log.append(f"{target.name}'s heal crits! (stress -{HEAL_CRIT_STRESS_REDUCTION:.0f})")

        target.hp = min(target.max_hp, target.hp + heal_amt)
        # check_recovery=False is used only by _tick_heal_dots: a heal_dot
        # ticks alongside bleed/blight in the same start-of-turn phase, so
        # checking recovery here (before that turn's bleed/blight has been
        # subtracted) could flip a hero off Death's Door on the heal alone,
        # then have _check_deaths_door_dot immediately re-enter it once the
        # bleed lands — skipping the deathblow roll a hero already on
        # Death's Door should face for net-negative HP that turn, and
        # double-firing enter/exit side effects (stress, buffs, Flagellant's
        # enter_effects). The caller instead runs _maybe_recover_from_deaths_
        # door once, after every HP-changing tick that turn has applied.
        if check_recovery and target.hp > 0 and target.is_deaths_door:
            self._recover_from_deaths_door(target)
        return heal_amt

    def _recover_from_deaths_door(self, target: CombatantState):
        # Clear the sticky flag: a healed hero who was previously downed
        # should get a fresh death's door survival roll if downed again,
        # not an instant deathblow from a flag that never reset. Also
        # reverses the stat debuffs from _apply_deaths_door_buffs, which
        # used to be permanent for the rest of combat even after
        # recovering (see _reverse_deaths_door_buffs).
        target.is_deaths_door = False
        self._reverse_deaths_door_buffs(target)
        self.log.append(f"{target.name} recovers from death's door")
        self._apply_recovery_tier(target)

    def _apply_recovery_tier(self, hero: CombatantState):
        """Applies the weaker "mortality" debuff tier (or the separate,
        not-strictly-worse "heart_attack" tier if this Death's Door visit
        was heart-attack-caused) for the rest of the expedition — sticky,
        never downgrades a hero already at a higher tier. Buffs applied
        directly and permanently (like apply_trinkets_and_quirks), not
        duration-tracked, since they're meant to outlive this combat."""
        new_tier = "heart_attack" if hero.next_recovery_is_heart_attack else "mortality"
        hero.next_recovery_is_heart_attack = False
        if MORTALITY_TIER_RANK[new_tier] <= MORTALITY_TIER_RANK[hero.mortality_tier]:
            return
        hd = self._get_hero_def(hero.id)
        if not hd:
            return
        buff_list = hd.recovery_heart_attack_buffs if new_tier == "heart_attack" else hd.recovery_buffs
        hero.mortality_deltas = {}
        for buff_id in buff_list:
            bd = self.store.buffs.get(buff_id)
            if bd:
                # Keep the exact delta: reversing a multiply on an int stat
                # drifts, and this tier can be cleared at a campfire.
                delta = self._apply_buff_to_stats(hero, bd)
                if delta is not None:
                    hero.mortality_deltas[buff_id] = delta
        hero.mortality_tier = new_tier
        self.log.append(f"{hero.name} bears the mark of {new_tier} ({len(buff_list)} debuffs)")

    # ── Effect application ──








    # ── Guard / Riposte ──

    def _shared_hit_roll(self, targets: list[CombatantState]) -> Optional[float]:
        """One to-hit roll for a multi-target skill use, else None.

        The real game rolls to-hit **once per skill use** and tests that single
        roll against each target's own hit chance. Confirmed from captured game
        debug output (`tools/dbwin_capture.py` -> `tools/game_trace.py --aoe`):
        11/11 multi-target attacks across 2- and 3-target skills shared one
        roll, with zero counterexamples, while the crit roll and the damage
        rolls stayed distinct per target. So share *only* the to-hit roll.

        This does not change the expected hit rate of any single target; it
        changes the correlation between targets. A real AoE tends to hit
        everything or miss everything, where independent per-target rolls
        average out — so it matters for how swingy AoE comps are, not for their
        mean damage.

        Returns None for a single target so `resolve_attack` draws its own roll
        exactly as before, keeping the RNG stream byte-identical for every
        single-target attack.
        """
        if len(targets) <= 1:
            return None
        return self.rng.random()

    def _resolve_guard_redirect(self, target: CombatantState, is_aoe: bool = False) -> CombatantState:
        """If target is guarded by a living ally, the attack hits the guardian
        instead. Wiki-confirmed (darkestdungeon.wiki.gg/wiki/Status_Effects):
        "AoE bypasses guard" -- an AoE attack lands on its real targets
        directly, never redirected onto a guardian. Real gap found and fixed:
        every AoE-attack call site here used to call this unconditionally, so
        a guarded hero standing in an AoE's target ranks was incorrectly
        protected from it."""
        if is_aoe or target.guard_source_id is None:
            return target
        guardian = self._find_combatant(target.guard_source_id)
        if guardian and guardian.is_alive():
            return guardian
        return target

    def _break_guard_if_guarding(self, combatant: CombatantState):
        """Clears combatant's own guard-on-someone-else, if any, and the
        reverse link on whoever they were guarding. Wiki-confirmed
        (darkestdungeon.wiki.gg/wiki/Status_Effects): "whenever a guarding
        unit is stunned, the guard effect will immediately disappear from
        the protected unit" -- stunning the GUARDIAN breaks the guard, not
        stunning the guarded unit. Real gap found and fixed: stun application
        (eff.stun_chance branch above) never touched guard_target_id/
        guard_source_id at all, so a stunned guardian kept redirecting
        attacks meant for their ward. Shared with the pre-existing
        is_clear_guarding effect branch, which clears the same link
        unconditionally (no stun involved)."""
        if combatant.guard_target_id is None:
            return
        guarded = self._find_combatant(combatant.guard_target_id)
        if guarded:
            guarded.guard_source_id = None
        combatant.guard_target_id = None
        combatant.guard_rounds_remaining = None

    def _maybe_trigger_riposte(self, defender: CombatantState, attacker: CombatantState, hit: bool):
        # Real gap found and fixed: a Stunned defender's Riposte never
        # triggers -- confirmed by two independent hero docs (Highwayman's
        # own: "Ripostes aren't activated when Stunned" / "Flashfire
        # Gunpowder... 20% Stun Resist is crippling for Highwayman, as
        # Ripostes require avoiding Stuns"). Riposte is purely reactive
        # (fires off being attacked, not off taking a turn), so nothing
        # else in the engine already gated this the way the stun-skip in
        # _advance_to_next_hero_turn gates a stunned combatant's own turn.
        riposte = defender.active_riposte
        if riposte is None or defender.is_stunned or not attacker.is_alive():
            return
        chance = riposte.on_hit_chance if hit else riposte.on_miss_chance
        if chance <= 0 or self.rng.random() >= chance:
            return
        if riposte.effect_name:
            eff = self.store.effects.get(riposte.effect_name)
            if eff:
                self.log.append(f"{defender.name} ripostes against {attacker.name}!")
                self._resolve_effect(attacker, defender, eff, None)
            return

        # Real bug found and fixed: the riposte counter-attack itself used
        # to land unconditionally once triggered -- no accuracy roll at
        # all, so every riposte was a guaranteed hit. The real riposte_
        # skill: record (Man-at-Arms 90% ACC/+0% crit, Bounty Hunter/
        # Highwayman 85%/+5% each) is a FIXED base that does NOT scale
        # with the setup skill's own level, but IS affected by ACC buffs/
        # trinkets -- confirmed verbatim by HEROES/Heroes/Man-at-Arms'
        # own doc. Falls back to the old guaranteed-hit behavior when the
        # defender has no riposte_acc data (e.g. the rare monster-side
        # riposte case, out of scope here).
        hd = self._get_hero_def(defender.id) if defender.is_hero else None
        riposte_crit_mod = 0.0
        if hd and hd.riposte_acc is not None:
            # Same to-hit rule as any other attack: the cutoffs bracket the
            # ROLL and the chance is unclamped (DECOMP.md F4). Inferred here by
            # consistency rather than separately validated -- the captured
            # corpus does not distinguish riposte rolls from ordinary ones --
            # but two different to-hit rules in one engine is the worse bet.
            riposte_hit_chance = hd.riposte_acc + defender.atk_bonus - attacker.dodge
            if not resolve_to_hit(self.rng.random(), riposte_hit_chance, self.rules):
                self.log.append(f"{defender.name}'s riposte misses {attacker.name}")
                return
            riposte_crit_mod = hd.riposte_crit_mod or 0.0

        dmg = self.rng.randint(defender.dmg_min, defender.dmg_max)
        dmg = max(1, int(dmg * (1 + riposte.dmg_mod)))
        is_crit = self.rng.random() < max(0.0, defender.crit + riposte.crit_mod
                                            + riposte_crit_mod + attacker.crit_received_bonus)
        if is_crit:
            dmg = int(dmg * self.rules.player_crit_multiplier)
            if defender.is_hero:
                # Same "landed a crit" event as a normal attack — on-crit
                # passives should proc here too, not just on primary attacks.
                self._trigger_on_crit_passive(defender)
        dmg = max(0, int(dmg * (1 - attacker.prot)))
        if dmg > 0 and attacker.damage_blocks > 0:
            attacker.damage_blocks -= 1
            self.log.append(f"{attacker.name}'s Aegis Block absorbs {defender.name}'s riposte!")
            dmg = 0
        attacker.hp -= dmg
        self.log.append(f"{defender.name} ripostes {attacker.name} for {dmg}" + (" (crit!)" if is_crit else ""))
        if attacker.hp <= 0:
            self._handle_deaths_door(attacker, defender, is_crit=is_crit)

    def _tick_riposte_durations(self):
        for c in self.heroes + self.enemies:
            if c.active_riposte is not None:
                c.active_riposte.remaining_rounds -= 1
                if c.active_riposte.remaining_rounds <= 0:
                    c.active_riposte = None

    def _tick_enemy_skill_cooldowns(self):
        for enemy in self.enemies:
            if not enemy.skill_cooldowns:
                continue
            enemy.skill_cooldowns = {
                sk_id: remaining - 1
                for sk_id, remaining in enemy.skill_cooldowns.items()
                if remaining - 1 > 0
            }

    def _tick_guard_durations(self):
        """Guard.duration is tracked on the guardian; None means permanent
        (matches trinket/quirk-style effects with no .duration field)."""
        for guardian in self.heroes + self.enemies:
            if guardian.guard_rounds_remaining is None:
                continue
            guardian.guard_rounds_remaining -= 1
            if guardian.guard_rounds_remaining <= 0:
                guarded = self._find_combatant(guardian.guard_target_id) if guardian.guard_target_id is not None else None
                if guarded:
                    guarded.guard_source_id = None
                guardian.guard_target_id = None
                guardian.guard_rounds_remaining = None

    def _tick_stealth_durations(self):
        for c in self.heroes + self.enemies:
            if c.is_stealthed and c.stealth_rounds_remaining is not None:
                c.stealth_rounds_remaining -= 1
                if c.stealth_rounds_remaining <= 0:
                    c.is_stealthed = False
                    c.stealth_rounds_remaining = None

    def _tick_mark_durations(self):
        for c in self.heroes + self.enemies:
            if c.is_marked and c.mark_rounds_remaining is not None:
                c.mark_rounds_remaining -= 1
                if c.mark_rounds_remaining <= 0:
                    c.is_marked = False
                    c.mark_rounds_remaining = None

    # ── Skill limits ──

    def _has_skill_uses_remaining(self, performer: CombatantState, skill_id: str, skill: SkillLevelData) -> bool:
        """Side-effect-free check, shared by get_valid_actions' filter and
        _check_skill_limits' execution-time check -- same split as
        _is_valid_mode/_check_valid_mode, for the same reason: a maxed-out
        skill was offered as a legal action after its uses ran out, silently
        rejected on selection and wasting the whole turn once selected.

        **13 hero skills carry `per_battle_limit`**, not one: take_aim (the
        Highwayman's, "Tracking Shot" on screen), barbaric_yawp, blinding_gas,
        bolster, bulwark_of_faith, emboldening_vapours, heroic_end, protect_me,
        revenge, solo, toxin_trickery, withstand and transform. Only
        Abomination's Transform also has `per_turn_limit` (2/battle, 1/turn).
        The old note here said Transform was the only one at all, and a reader
        who believed it would think this path was near-dead code -- it is not,
        and `bridge/seed.py::_sync_skill_uses` exists because the live bridge
        was starting every one of these counters at zero on every turn."""
        if skill.per_battle_limit is not None:
            if performer.skill_uses.get(skill_id, 0) >= skill.per_battle_limit:
                return False
        if skill.per_turn_limit is not None:
            if performer.skill_uses.get(f"{skill_id}_turn_{self.round}", 0) >= skill.per_turn_limit:
                return False
        return True

    def _check_skill_limits(self, performer: CombatantState, skill_id: str, skill: SkillLevelData) -> bool:
        if not self._has_skill_uses_remaining(performer, skill_id, skill):
            used = performer.skill_uses.get(skill_id, 0)
            if skill.per_battle_limit is not None and used >= skill.per_battle_limit:
                self.log.append(f"{performer.name}: {skill_id} used {used}/{skill.per_battle_limit}")
            return False
        return True

    def _track_skill_use(self, performer: CombatantState, skill_id: str, skill: SkillLevelData):
        performer.skill_uses[skill_id] = performer.skill_uses.get(skill_id, 0) + 1
        performer.skill_uses[f"{skill_id}_turn_{self.round}"] = \
            performer.skill_uses.get(f"{skill_id}_turn_{self.round}", 0) + 1
        self._last_skill_used = skill_id
        # Real stall condition: the PARTY needs >=2 "real" (is_stall_
        # invalidating) actions per round to avoid the round counting
        # toward a stall — see _check_stall. Hero-only (this is a
        # party-behavior check, not something enemy actions factor into).
        if performer.is_hero and self._invalidates_stall(skill_id, skill):
            self._stall_invalidating_actions_this_round += 1

    def _invalidates_stall(self, skill_id: str, skill: SkillLevelData) -> bool:
        """Does using this skill count toward the two that reset the stall?

        **The shipped flag alone gets three skills wrong, and it gets them
        wrong in the dangerous direction** -- they read as invalidating, so
        the simulated party could dodge a stall penalty with moves the real
        game does not accept.

        `.is_stall_invalidating` defaults to TRUE and is written out only
        where it is false, so a skill that is non-damaging AND lacks the tag
        inherits "invalidating" by omission. The wiki's Talk:Stalling page
        names the three the game overrides anyway -- Houndmaster's Guard Dog,
        Leper's Revenge, Plague Doctor's Emboldening Vapours -- and proposes
        the missing condition: **the skill must target an enemy.** Checked
        against the shipped files, that single test reproduces every
        documented row:

        | skill | flag | ally | target | active? |
        |---|---|---|---|---|
        | ordinary attack | True (default) | no | enemies | **yes** |
        | Jester `solo` (-100% dmg) | True (default) | no | enemies | **yes** |
        | MaA `bellow` (-100% dmg) | **False** | no | enemies | no |
        | Occultist `disruptive_curse` (-90%) | **False** | no | enemies | no |
        | HM `guard_dog` | True (default) | **yes** | allies | no |
        | PD `emboldening_vapours` | True (default) | **yes** | allies | no |
        | Leper `revenge` | True (default) | no | **none (self)** | no |

        Both of the wiki's two "well-known exceptions" survive untouched:
        Solo is the non-damaging skill that DOES invalidate (it targets the
        enemy side and carries no tag), and Vulnerability Hex is the damaging
        one that does NOT (its tag is explicitly false).

        The enemy test is two conditions because self-targeting and
        ally-targeting are different things in this loader:
        `_is_ally_skill` reads the `@` in the raw `.target` notation, and a
        pure self-buff like Revenge has an EMPTY target list instead.
        """
        if not skill.is_stall_invalidating:
            return False
        if self._is_ally_skill(skill_id):
            return False
        return bool(getattr(skill, "target", None))

    def _is_valid_mode(self, performer: CombatantState, skill: SkillLevelData) -> bool:
        if skill.valid_modes and "human" in skill.valid_modes and "beast" in skill.valid_modes:
            return True
        return performer.mode in skill.valid_modes

    def _check_valid_mode(self, performer: CombatantState, skill: SkillLevelData) -> bool:
        if self._is_valid_mode(performer, skill):
            return True
        self.log.append(f"{performer.name} cannot use that skill in {performer.mode} mode")
        return False

    # ── Enemy turn ──












    def _vlog_resist(self, target, kind: str, resist: float, roll: float, resisted: bool) -> None:
        self.vlog(f"      [{kind.upper()} RES] {target.name}: {resist:.0%} | roll {roll:.3f} "
                  f"=> {'RESISTED' if resisted else 'APPLIED'}")

    # Effect resist types, in priority order. Exactly ONE shipped effect in
    # the whole game carries two of these ("Baron Blight": blight + a stat
    # debuff), so "an effect has one resist type" is a property of the data
    # rather than a simplification -- and that one is Crimson Court, outside
    # anything this simulator fields. Order decides it: the DoT wins.
    EFFECT_RESIST_ORDER = ("bleed", "blight", "stun", "move", "debuff")

    def _effect_resist(self, eff: EffectDefinition, eff_target: CombatantState,
                       performer: CombatantState) -> tuple:
        """`(resist_type, effective_resist)` for this effect landing here.

        The attacker's `*_chance` buffs lower the target's resist rather than
        raising the effect's chance -- there is one roll, so there is one place
        to put them.

        **`debuff` is the only type that depends on who is applying it.** A
        stat modifier is a debuff when it crosses to the other side and a buff
        when it does not, and nothing in the effect record says which: the same
        `combat_stat_buff` machinery carries Bolster and Weakening Curse. So
        the side test IS the predicate, and it is the reason a self-inflicted
        debuff (Leper's Vulnerability, Hellion's Exhaust) is never resisted --
        which is also what the wiki says about them.
        """
        if eff.dot_irresistible:
            # The data's own "do not roll for this". Honoured by skipping the
            # subtraction rather than by subtracting a zero, so a trace
            # comparison is not thrown off by a draw that changes nothing.
            return None, 0.0
        if eff.bleed_amount is not None:
            kind, resist, bonus = "bleed", eff_target.bleed_resist, performer.bleed_chance_bonus
        elif eff.blight_amount is not None:
            kind, resist, bonus = "blight", eff_target.blight_resist, performer.blight_chance_bonus
        elif eff.stun_chance is not None:
            kind, resist, bonus = "stun", eff_target.stun_resist, performer.stun_chance_bonus
        elif ((eff.push_distance and eff.push_distance > 0)
              or (eff.pull_distance and eff.pull_distance > 0)):
            kind, resist, bonus = "move", eff_target.move_resist, performer.move_chance_bonus
        elif ((eff.buff_ids or eff.is_stat_buff or eff.inline_buff_type)
                and eff_target.is_hero != performer.is_hero):
            kind, resist, bonus = "debuff", eff_target.debuff_resist, performer.debuff_chance_bonus
        else:
            return None, 0.0
        return kind, max(0.0, resist - bonus)

    def _get_resistance(self, target: CombatantState, resistance_type_id: str) -> float:
        return {
            "stun": target.stun_resist,
            "poison": target.blight_resist,
            "blight": target.blight_resist,
            "bleed": target.bleed_resist,
            "debuff": target.debuff_resist,
            "move": target.move_resist,
            "death_blow": target.death_blow_resist,
        }.get(resistance_type_id, 0.0)








    # ── Death's door ──

    def _handle_deaths_door(self, target: CombatantState, attacker: CombatantState,
                            is_crit: bool = False):
        if not target.is_hero:
            target.is_dead = True
            target.is_corpse = self._leaves_corpse(target, is_crit)
            if target.is_corpse:
                target.corpse_rounds_remaining = CORPSE_ALIVE_ROUND_LIMIT
                target.corpse_spawn_round = self.round
                self._apply_corpse_stat_block(target)
            self.log.append(f"{target.name} is slain!"
                            + ("" if target.is_corpse else " (no corpse)"))
            self._collapse_enemy_position_gaps()
            self._resolve_life_links(target)
            self._relieve_stress_from_killing_blow(attacker)
            return

        if not target.is_deaths_door:
            self.deaths_door_entries += 1
            target.is_deaths_door = True
            target.hp = max(target.hp, 0)
            self.log.append(f"{target.name} is at Death's Door!")
            self._apply_deaths_door_buffs(target)
            self._maybe_trigger_death_door_enter(target)
            self._apply_stress(target, self.rules.deaths_door_self_stress_dmg,
                               self.rules.deaths_door_self_stress_chance)
            self._apply_stress_to_allies(
                target, self.rules.deaths_door_companion_stress_chance,
                self.rules.deaths_door_companion_stress_dmg, exclude=target,
            )
        else:
            # Wiki-confirmed cap: "The maximum resistance cap is 87%, even if
            # modifications would theoretically reach higher values."
            capped_resist = min(target.death_blow_resist, DEATHBLOW_RESIST_CAP)
            effective_chance = self.rules.deathblow_chance - capped_resist
            self.deathblow_rolls += 1
            db_roll = self.rng.random()
            self.vlog(f"      [DEATHBLOW] {target.name} at Death's Door: base "
                      f"{self.rules.deathblow_chance:.0%} - DEATHBLOW RES "
                      f"{capped_resist:.0%}(cap {DEATHBLOW_RESIST_CAP:.0%}) = {effective_chance:.0%} to die | "
                      f"roll {db_roll:.3f} => {'DIES' if db_roll < effective_chance else 'SURVIVES'}")
            if db_roll < effective_chance:
                self.deathblow_deaths += 1
                target.is_dead = True
                target.hp = 0
                self.log.append(f"{target.name} has died!")
                self._collapse_hero_position_gap(target.position)
                self._on_hero_died(target)
            else:
                self.log.append(f"{target.name} survives Death's Door!")

    def _can_be_damaged(self, c: CombatantState) -> bool:
        """`battle_modifier: .can_be_damaged_directly false` -- 12 monsters.

        The one that matters is the Hag's **empty cauldron**: 100 HP, size 2,
        standing on ranks 1-2 of her boss fight, and not a legal target at all.
        Before this the policy could pour a whole fight into an invulnerable
        pot, because the engine saw a 100 HP enemy in the front rank and
        offered it. `cauldron_full` -- the one with a captured hero in it --
        does NOT carry the flag, which is the design: you break the pot your
        friend is in, never the empty one.

        Applied to hero targeting AND to AoE resolution, because
        `get_valid_actions` and `_resolve_action_targets` have to move together
        or an AoE offered over the cauldron's rank re-resolves to nothing and
        silently burns the turn -- the same rule corpses already follow.

        The flag says "directly", and this reads it as "cannot be chosen or
        struck". Whether an indirect source (a DoT already on it) would still
        tick is unanswerable from shipped data and unreachable in play: nothing
        can land a DoT on something it cannot hit.
        """
        ed = self.store.enemies.get(c.definition_id)
        return ed is None or ed.can_be_damaged_directly

    def _resolve_life_links(self, dead: CombatantState) -> None:
        """A boss's adds die with the boss.

        `life_link: .base_class "X"` sits on 177 monsters across 19 parents:
        the Prophet's pews, the Hag's cauldron, the Crow's nest, and -- the
        half that matters most here -- every add the summoning bosses call in.
        Necromancer skeletons, Siren fishmen, the Drowned Captain's anchor, the
        Brigand Cannon's crew, the Collector's collected heroes. Parsed and
        unread until 2026-08-21, which meant **killing the boss did not end the
        fight**: the party stood there clearing summons that no longer had a
        summoner. That went from theoretical to live two commits earlier, when
        `summon_count` started working.

        **The link is inert unless the parent is actually on the board**, and
        that guard is the whole reading rather than a safety margin.
        `skeleton_arbalist_A` is linked to `necromancer` and fills ordinary
        Ruins corridors with no Necromancer anywhere: of the 1266 comp slots
        holding a linked add, **1239 have no parent present** and all 27 that do
        are boss or named comps. A rule without the guard would delete half the
        Ruins on the first skeleton kill.

        Matched on `base_class`, the monster's DIRECTORY, so a Veteran
        Necromancer clears Veteran skeletons and the `_A`/`_B`/`_C` tiers never
        cross.

        A linked add is REMOVED, not slain: no corpse (it is not a killing blow
        and `_leaves_corpse` has no say), no killing-blow stress relief, and no
        recursion -- an add is never itself a parent in the shipped data, and
        looping once means a chain could not run away even if one appeared.
        """
        parent = self.store.enemies.get(dead.definition_id)
        if parent is None or not parent.base_class:
            return
        for e in self.enemies:
            if e is dead or not e.is_alive():
                continue
            ed = self.store.enemies.get(e.definition_id)
            if ed is None or ed.life_link_base_class != parent.base_class:
                continue
            e.is_dead = True
            e.hp = 0
            e.is_corpse = False
            self.log.append(f"{e.name} collapses without {dead.name}!")
        self._collapse_enemy_position_gaps()

    def _leaves_corpse(self, enemy: CombatantState, is_crit: bool) -> bool:
        """Whether a slain monster leaves a rank-blocking corpse behind.

        Real, data-driven rule — the engine used to unconditionally set
        is_corpse=True on every enemy death:

        1. Only a monster with a `death_class:` record leaves one. That
           record's .monster_class_id was already parsed into
           EnemyDefinition.corpse_id but read by nobody. 125 of 254 real
           monster files have it; measured against the encounter groups we
           actually train on, 21.5% of enemy slots were getting a corpse
           the real game never gives them (rabid_dog, maggot, spider_webber,
           spider_spitter and gargoyle are the biggest offenders).
        2. A critical killing blow leaves none — every one of those 125
           records sets `.is_valid_on_crit False`. (The only three records
           with True are the Ancestor phase-transition chain, which is a
           different `.type` entirely and already out of scope.)

        DoT kills are handled separately in _check_deaths_door_dot, which
        matches the same data: `.is_valid_on_bleed_dot`/`_blight_dot` are
        absent from all 125 corpse records, i.e. default False.
        """
        ed = self._get_enemy_def(enemy.id)
        if ed is None or ed.corpse_id is None:
            return False
        return not is_crit

    def _apply_corpse_stat_block(self, enemy: CombatantState):
        """A corpse is its own monster class, not the husk of what died.

        Real data — `monsters/corpse/corpse_A/corpse_A.info.darkest`:
        `.hp 7 .def -20% .prot 0`, and decisively
        `battle_modifier: ... .can_be_missed False`. So it is a 7-HP object that
        cannot be dodged, not a full-health body: without this the corpse kept
        the slain monster's own HP and "attack the corpse" would have been a
        multi-turn job against, say, a 40-HP husk instead of the one-hit lane
        opener it is in the real game.

        The variant is named by the dead monster's own
        `death_class: .monster_class_id` (already parsed into
        EnemyDefinition.corpse_id), which is what makes the difficulty scale
        come out right: 7/10/14/14 HP for corpse_A..D, 15/21/29/30 for the
        size-2 corpse_large_A..D.

        Size is deliberately left as the slain monster's: the game pairs
        corpse_large with the size-2 monsters, so the two already agree, and
        rewriting it here would move a formation the death has just collapsed.
        """
        ed = self._get_enemy_def(enemy.id)
        cd = self.store.corpses.get(ed.corpse_id) if ed and ed.corpse_id else None
        if cd is None:
            return
        enemy.max_hp = cd.stats.hp
        enemy.hp = cd.stats.hp
        enemy.dodge = cd.stats.dodge
        enemy.prot = cd.stats.prot

    def _destroy_corpse(self, corpse: CombatantState):
        """Damage finished the corpse off — the rank it was holding frees up
        immediately, exactly as when it rots out or a clear-corpses skill
        removes it. This is the whole point of being able to attack one: the
        living enemy standing behind it advances into reach."""
        corpse.is_corpse = False
        corpse.corpse_rounds_remaining = None
        corpse.corpse_spawn_round = None
        corpse.hp = 0
        self.log.append(f"{corpse.name}'s corpse is destroyed")
        self._collapse_enemy_position_gaps()

    def _tick_corpse_durations(self):
        """Rot corpses away — real `life_time: .alive_round_limit 3` on the
        game's corpse monster class, counted from the round AFTER the one the
        corpse appeared in.

        That last clause is the whole subtlety, and it was wrong until
        2026-08-20. The data gives the number 3 but not the comparison, and
        the two readings differ by a full round. The game's own worked
        example (darkestdungeon.wiki.gg/wiki/Corpse) settles it: *"a creature
        killed in round 1 will have its corpse on the field until the end of
        round 4, when it will disappear and leave an unoccupied slot for
        round 5."* That is FOUR round-ends, not three, so the round of
        appearance is excluded and the limit counts full rounds the corpse
        survives intact.

        Fixed by skipping the spawn round rather than by changing the
        constant to 4: 3 is what the game file says, and overwriting a real
        data value with a number derived from prose would hide where the
        adjustment actually lives.

        Getting this short is not cosmetic. A corpse blocks the rank it
        holds, so an extra round of blocking is an extra round the enemy line
        does not collapse forward — which is exactly the pressure that
        `W_BLOCKING_CORPSE` scores and that melee/front-rank comps feel most.
        """
        rotted = False
        for enemy in self.enemies:
            if not enemy.is_corpse or enemy.corpse_rounds_remaining is None:
                continue
            if enemy.corpse_spawn_round == self.round:
                # It appeared during THIS round; its first full round has not
                # happened yet.
                continue
            enemy.corpse_rounds_remaining -= 1
            if enemy.corpse_rounds_remaining <= 0:
                enemy.is_corpse = False
                enemy.corpse_rounds_remaining = None
                enemy.corpse_spawn_round = None
                self.log.append(f"{enemy.name}'s corpse rots away")
                rotted = True
        if rotted:
            # The rank the corpse was holding is now free — everything
            # behind it advances, exactly as when it is cleared by a skill.
            self._collapse_enemy_position_gaps()





    def _trigger_heart_attack(self, hero: CombatantState):
        if hero.is_deaths_door:
            # Already on Death's Door when the heart attack lands: instant
            # death, bypassing the deathblow-resist roll entirely.
            hero.is_dead = True
            hero.hp = 0
            self.log.append(f"{hero.name}'s heart gives out!")
            self._collapse_hero_position_gap(hero.position)
            self._on_hero_died(hero)
            return
        hero.hp = 0
        hero.stress = HEART_ATTACK_RESULTING_STRESS
        hero.next_recovery_is_heart_attack = True
        hero.is_deaths_door = True
        self.log.append(f"{hero.name} suffers a heart attack! At Death's Door!")
        self._apply_deaths_door_buffs(hero)
        self._maybe_trigger_death_door_enter(hero)

    def _maybe_trigger_death_door_enter(self, hero: CombatantState):
        """Real `deaths_door: .enter_effects X .enter_effect_round_cooldown
        N` fields (Flagellant-only in practice: "flagellant_death_door_
        enter", a 10%-max-HP party heal) — a hero-specific reaction fired
        on ENTERING Death's Door, gated by a round-based cooldown so
        re-entering within that many rounds doesn't re-fire it. Distinct
        from the passive .buffs applied by _apply_deaths_door_buffs."""
        hd = self._get_hero_def(hero.id)
        if not hd or not hd.death_door_enter_effects:
            return
        last = hero.last_death_door_enter_round
        if last is not None and self.round - last < hd.death_door_enter_round_cooldown:
            return
        hero.last_death_door_enter_round = self.round
        for ename in hd.death_door_enter_effects:
            eff = self.store.effects.get(ename)
            if eff:
                self._resolve_effect(hero, hero, eff, None)

    def _on_hero_died(self, hero: CombatantState):
        """Everything the party owes when one of them actually dies.

        `death_party_stress_chance` (0.5) and `death_party_stress_damage` (12)
        are real `rules.json` scalars, read by the game and by nothing here
        until 2026-08-21: **a death cost the survivors no stress at all.**

        That is the largest single reason the death spiral -- the thing that
        actually ends a Darkest Dungeon run -- could not happen in this
        simulator. 12 stress at even odds across three survivors is ~18 points
        of party stress per death, landing exactly when the party can least
        afford it, and it is what turns one loss into an expedition-ending
        cascade of afflictions. Without it a death was priced purely as lost
        output.

        Also the single place death reactions fire. `is_kill_effect` did not
        call them at all before, so a Flagellant executed by an instant-kill
        effect skipped Last Gift and Last Insult.
        """
        self._apply_stress_to_allies(
            hero, self.rules.death_party_stress_chance,
            self.rules.death_party_stress_damage, exclude=hero)
        self._maybe_trigger_death_reactions(hero)

    def _maybe_trigger_death_reactions(self, hero: CombatantState):
        """Real `death_reaction: .target_allies bool .target_enemies bool
        .effects "X"` records (Flagellant-only in practice: "Flag Last
        Gift" heals the rest of the party 10 HP, "Flag Last Insult" stuns
        every enemy at 125% chance) — fired once when this hero actually
        dies. Each reaction's own target_allies/target_enemies flag picks
        the whole side to apply its effects to (the effect's own .target
        is "target", a single-target spec — the group semantics live on
        the death_reaction record, not the effect)."""
        hd = self._get_hero_def(hero.id)
        if not hd:
            return
        for reaction in hd.death_reactions:
            if reaction.target_allies:
                pool = [h for h in self.heroes if h.is_alive()]
            elif reaction.target_enemies:
                pool = [e for e in self.enemies if e.is_alive()]
            else:
                continue
            for member in pool:
                for ename in reaction.effects:
                    eff = self.store.effects.get(ename)
                    if eff:
                        self._resolve_effect(member, hero, eff, None)

    def clear_mortality_tier(self, hero: CombatantState) -> bool:
        """Undo a hero's mortality debuffs and reset the tier. Returns whether
        anything was cleared.

        The counterpart to the sticky tier applied on Death's Door recovery,
        and the ONLY thing that lowers `mortality_tier` — every other write
        raises it. Without this a hero who visited Death's Door once carried
        -2 ACC / -5% DMG / -1 SPD / -stress-resist for the whole rest of the
        expedition with no way back, because the tier is carried in
        `CarryOverHeroState` and `_apply_recovery_tier` guards with `<=`.

        The real game removes them at a campfire (`stand_tall`, `dark_ritual`),
        which is what calls this. Public rather than `_`-private for that
        reason: `engine/camping.py` has no engine reference and takes it as a
        callback, the same way it takes `apply_buff`.
        """
        if hero.mortality_tier in ("", "none"):
            return False
        hd = self._get_hero_def(hero.id)
        if not hd:
            return False
        buff_list = (hd.recovery_heart_attack_buffs
                     if hero.mortality_tier == "heart_attack" else hd.recovery_buffs)
        for buff_id in buff_list:
            bd = self.store.buffs.get(buff_id)
            if bd:
                self._reverse_buff_from_stats(
                    hero, bd, applied_delta=hero.mortality_deltas.get(buff_id))
        self.log.append(f"{hero.name} is freed of the mark of {hero.mortality_tier}")
        hero.mortality_tier = "none"
        hero.mortality_deltas = {}
        return True

    def _apply_deaths_door_buffs(self, hero: CombatantState):
        hd = self._get_hero_def(hero.id)
        if not hd:
            return
        for buff_id in hd.deaths_door_buffs:
            bd = self.store.buffs.get(buff_id)
            if bd:
                self._apply_buff_to_stats(hero, bd)
                self.log.append(f"{hero.name} gets death's door debuff: {buff_id}")

    def _reverse_deaths_door_buffs(self, hero: CombatantState):
        """Called from _apply_heal when a hero recovers from death's door —
        undoes the same buff list _apply_deaths_door_buffs applied. Previously
        these were permanent for the rest of combat even after recovering,
        since is_deaths_door itself never cleared on heal either (see
        _apply_heal); now that it does, its side effects should too."""
        hd = self._get_hero_def(hero.id)
        if not hd:
            return
        for buff_id in hd.deaths_door_buffs:
            bd = self.store.buffs.get(buff_id)
            if bd:
                self._reverse_buff_from_stats(hero, bd)
                self.log.append(f"{hero.name} recovers from death's door debuff: {buff_id}")

    def _maybe_clear_overstress(self, hero: CombatantState) -> bool:
        """An affliction (or virtue) ENDS when the hero reaches 0 stress.

        Fran, 2026-09-02: *"if a hero is afflicted, it'll remain afflicted for
        the whole dungeon unless his stress value reaches 0 again, as its on
        the game, so the team we'll need to carry on with all the affliction
        for the rest of the dungeon or even multiple afflictions on several
        heroes"*.

        **Persistence was already modelled** -- `CarryOverHeroState` carries the
        trait id between combats and restores it rather than re-rolling, so a
        broken hero stays broken across the raid, and any number of heroes can
        be broken at once. **The CURE was not.** Seven places reduce hero
        stress and not one of them looked at the flags, so a hero healed to 0
        kept the personality and its whole buff list for the rest of the run.
        The simulator was HARSHER than the game here, which is the rarer
        direction and the reason it went unnoticed: a fidelity gap that makes
        things worse does not look like a bug in a win rate.

        The buffs have to come off with it. `TraitDefinition.buff_ids` is the
        complete effect list -- the -15 resists and MAXHP-10 an affliction
        shares are entries INSIDE its own list -- so leaving them behind would
        keep a cured hero permanently weakened, which is the same defect one
        layer down.
        """
        if hero.stress > 0 or not (hero.is_afflicted or hero.is_virtuous):
            return False
        trait_id = hero.affliction_id or hero.virtue_id
        trait = self.store.traits.get(trait_id) if trait_id else None
        if trait is not None:
            for buff_id in trait.buff_ids:
                bd = self.store.buffs.get(buff_id)
                if bd:
                    self._reverse_buff_from_stats(hero, bd)
            hero.buffs = [b for b in hero.buffs if b.buff_id not in set(trait.buff_ids)]
        if hero.overstress_stats is not None:
            # Restored rather than reversed -- see `overstress_stats`. The HP
            # itself is left where it is and only re-capped, because recovering
            # from an affliction is not a heal.
            for name, value in hero.overstress_stats.items():
                setattr(hero, name, value)
            hero.hp = min(hero.hp, hero.max_hp)
            hero.overstress_stats = None
        was = "affliction" if hero.is_afflicted else "virtue"
        hero.is_afflicted = False
        hero.is_virtuous = False
        hero.affliction_id = None
        hero.virtue_id = None
        self.log.append(f"{hero.name} recovers from their {was} ({trait_id})")
        return True

    def _check_deaths_door_dot(self, combatant: CombatantState):
        if combatant.hp <= 0 and not combatant.is_dead:
            if combatant.is_hero:
                self._handle_deaths_door(combatant, combatant)
            else:
                combatant.is_dead = True
                # Wiki-confirmed: "A unit that dies from a damage over time
                # effect will not leave a Corpse." Matters tactically because
                # a corpse keeps blocking its rank until cleared, so finishing
                # something with bleed/blight opens the lane that a normal
                # kill would not — which only means anything now that
                # _collapse_enemy_position_gaps actually walks the survivors
                # through the opened lane.
                combatant.is_corpse = False
                combatant.corpse_rounds_remaining = None
                combatant.corpse_spawn_round = None
                self._collapse_enemy_position_gaps()
                # A boss bled to death is still a dead boss: its adds go too.
                self._resolve_life_links(combatant)

    # ── Stress ──







    # ── Act-out outcomes ──
    #
    # `retreat_from_combat` is deliberately absent: it carries weight 0 in
    # every trait the game ships, so it can never be rolled. `consume_item`
    # (Color of Madness only) is likewise out of scope — this simulator has no
    # inventory to raid beyond the provision list.

















    def _stress_relief_performer(self, performer: CombatantState):
        relief = self.rules.player_crit_stress_relief
        performer.stress = max(0, performer.stress - relief)
        self._maybe_clear_overstress(performer)
        self.log.append(f"{performer.name} stress -{relief} (crit)")
        self._apply_stress_to_allies(
            performer, self.rules.player_crit_companion_relief_chance,
            -self.rules.player_crit_companion_stress_relief, exclude=performer,
        )

    def _relieve_stress_from_killing_blow(self, killer: CombatantState):
        """Real rules.json mechanic that nothing in the engine consulted:
        killing_blow_relief_chance (0.5) / killing_blow_stress_relief (3.0)
        — landing the killing blow on a monster gives the killer a 50%
        chance of -3 stress. Both fields were loaded into CombatRules and
        never read anywhere, same parsed-but-unused shape as is_crit_valid
        and the stall flags.

        Only heroes have stress at all (_apply_stress no-ops for monsters),
        and only a real killer qualifies — a DoT death has no attacker and
        correctly passes killer=None from _check_deaths_door_dot."""
        if killer is None or not killer.is_hero or not killer.is_alive():
            return
        if self.rng.random() >= self.rules.killing_blow_relief_chance:
            return
        relief = self.rules.killing_blow_stress_relief
        killer.stress = max(0, killer.stress - int(relief))
        self._maybe_clear_overstress(killer)
        self.log.append(f"{killer.name} stress -{int(relief)} (killing blow)")

    def _use_provision(self, pid: str, hero: CombatantState):
        self.provision_inventory[pid] = max(0, self.provision_inventory.get(pid, 0) - 1)
        pd = self.store.provisions.get(pid)
        eff = self.store.effects.get(pd.effect_name) if pd else None
        if eff is None:
            self.log.append(f"{hero.name} uses {pid}")
            return
        self.log.append(f"{hero.name} uses {pid}")
        if eff.cures_bleed:
            hero.bleeds = []
        if eff.cures_blight:
            hero.blights = []
        if eff.cures_burn:
            # Its own key on purpose: a bandage (.cure_bleed) and an
            # antivenom (.cure_poison) must NOT put a fire out, and neither
            # must the bare `.cure`, which the binary scopes to bleed+blight.
            hero.burn_stacks = 0
        if eff.clears_stress_dot:
            hero.stress_dots = []
        if eff.buff_ids:
            self._resolve_effect(hero, hero, eff, None)

    def _trigger_on_crit_passive(self, performer: CombatantState):
        """Per-class passive that fires whenever this hero lands ANY crit,
        with ANY skill — real `crit: .effects "on_crit_X"` record, separate
        from a skill's own .effect list (e.g. Crusader's on_crit_prot:
        +15% PROT for 2 rounds). Monsters have no `crit:` record in any
        real .info.darkest (verified absent), so this is a no-op for them —
        deliberately hero-only, not a missing case."""
        hd = self._get_hero_def(performer.id)
        if not hd or not hd.crit_effect_id:
            return
        eff = self.store.effects.get(hd.crit_effect_id)
        if not eff:
            return
        self._resolve_effect(performer, performer, eff, None)

    def _apply_stress_to_allies(self, source: CombatantState, chance: float, amount: float,
                                 exclude: Optional[CombatantState] = None):
        """Each living ally of `source` (heroes if source.is_hero, else
        enemies) independently rolls `chance` to also gain/lose `amount`
        stress — e.g. real party-wide crit stress (rules.monster_crit_
        companion_stress_*/player_crit_companion_*) and Death's-Door
        companion stress (rules.deaths_door_companion_stress_*). Negative
        `amount` means relief (mirrors _apply_stress's own sign convention).
        _apply_stress already no-ops for non-hero targets, so this is safe
        to call unconditionally."""
        party = self.heroes if source.is_hero else self.enemies
        for ally in party:
            if ally is exclude or not ally.is_alive():
                continue
            self._apply_stress(ally, amount, chance)

    def _tick_mode_stress(self):
        for hero in self.heroes:
            if hero.is_alive() and not hero.is_afflicted and not hero.is_virtuous:
                hd = self._get_hero_def(hero.id)
                if hd:
                    for mode in hd.modes:
                        if mode.id == hero.mode and mode.stress_damage_per_turn > 0:
                            self._apply_stress(hero, float(mode.stress_damage_per_turn))

    # ── Stun recovery ──

    def _apply_stun_recovery(self, combatant: CombatantState):
        recovery_buff = ActiveBuff(buff_id=self.rules.stun_recovery_buff_id, remaining_rounds=2)
        combatant.buffs.append(recovery_buff)
        combatant.stun_resist += 0.5

    # ── DoT ticking ──






    # ── Buff duration tick ──


    # ── Stall ──

    def effective_enemy_stall_size(self) -> int:
        """The enemy side of the stall test, in SIZE rather than heads.

        Total rank occupancy plus `stall_extra_round_size_amount` for every
        monster at or above `stall_extra_round_size_threshold`. Shared with
        `bridge/advisor.py` so the number it shows the player is the number
        the engine actually tests.
        """
        living = [e for e in self.enemies if e.is_alive()]
        return int(sum(e.size for e in living)
                   + sum(self.rules.stall_extra_round_size_amount for e in living
                         if e.size >= self.rules.stall_extra_round_size_threshold))

    def _check_stall(self):
        # Real bug found and fixed: this used to increment stall_counter
        # whenever alive_enemies <= stall_enemy_size_threshold, full stop —
        # backwards from the real mechanic (wiki-confirmed, matching
        # HEROES/Heroes/Plague Doctor's own "How Stalling Works" section):
        # a round only counts toward a stall if the party ALSO failed to
        # land >= stall_number_of_invalidating_skills_threshold (2) "real"
        # (SkillLevelData.is_stall_invalidating) actions, and never counts
        # at all if the party is nearly wiped (alive_heroes <
        # stall_party_size_threshold). Both real rules.json fields were
        # already loaded... no, weren't loaded at all before this fix — see
        # ASSUMPTIONS.md. Previously, any comp that naturally grinds a
        # fight down to its last 1-2 enemies over several rounds (exactly
        # what a Stun/Blight-control comp is designed to do) kept
        # incrementing the counter regardless of how aggressively it was
        # actually playing, spawning bogus reinforcement waves and
        # prolonging fights well past what real play would ever see —
        # confirmed via replay: 1-4 reinforcement spawns within a single
        # 8-combat "Stun Control: Blight" episode.
        living_enemies = [e for e in self.enemies if e.is_alive()]
        alive_enemies = len(living_enemies)
        alive_heroes = sum(1 for h in self.heroes if h.is_alive())
        actions_taken = self._stall_invalidating_actions_this_round
        self._stall_invalidating_actions_this_round = 0

        living_enemy_defs = [d for d in (self.store.enemies.get(e.definition_id) for e in living_enemies) if d]

        # Wiki-confirmed (darkestdungeon.wiki.gg/wiki/Stalling): any living
        # enemy with the real .disable_stall_penalty flag (most boss/
        # Ancestor forms, brigand_blood at every tier, ~119 monsters total)
        # exempts the fight from stalling entirely — never found or
        # consulted before this fix.
        if any(d.disable_stall_penalty for d in living_enemy_defs):
            self.stall_counter = 0
            return

        # The enemy side of the test is measured in SIZE, not heads --
        # `stall_enemy_size_threshold` says so in its own name, and
        # `stall_extra_round_size_threshold`/`_amount` (2 / 1) are the
        # surcharge a large monster carries. All three were unread until
        # 2026-08-23, so this was a plain head count and one Swinetaur read as
        # "a single enemy left, the party must be stalling".
        #
        # DECLARED READING (falsifier in ASSUMPTIONS.md) -- the only one that
        # satisfies everything the wiki states at once:
        #   1-2 small enemies -> stalling possible      (1..2 <= 2)
        #   THREE small        -> not                    (3 > 2)
        #   ANY size-2         -> not                    (2 + 1 = 3 > 2)
        # A bare head count gets the third wrong; bare occupancy with no
        # surcharge also gets it wrong. Only occupancy PLUS surcharge fits.
        is_stalling = (
            alive_heroes >= self.rules.stall_party_size_threshold
            and self.effective_enemy_stall_size() <= self.rules.stall_enemy_size_threshold
            and actions_taken < self.rules.stall_number_of_invalidating_skills_threshold
        )
        accelerated = alive_enemies > 0 and all(d.accelerate_stall_penalty
                                                for d in living_enemy_defs)

        # Wiki-confirmed: using >=2 real actions in a round resets the stall
        # timer outright, not just a one-step decay.
        #
        # The increment is 1 normally. When EVERY living enemy accelerates,
        # the game charges `stall_round_accelerated_only_increment` (1) for
        # the first such round and `_consecutive_increment` (5) for each one
        # after -- so the counter reads 1, then 6, and crosses the ordinary
        # summon threshold of 4 on the SECOND accelerated stall round. That
        # reproduces the wiki's "reinforcements after 2 rounds instead of 4"
        # out of the shipped numbers, replacing a hardcoded 2 that this file
        # previously admitted was a guess.
        if is_stalling:
            if accelerated:
                increment = int(self.rules.stall_round_accelerated_only_consecutive_increment
                                if self._accelerated_stall_streak
                                else self.rules.stall_round_accelerated_only_increment)
                self._accelerated_stall_streak = True
            else:
                increment = 1
                self._accelerated_stall_streak = False
            self.stall_counter += increment
        else:
            self.stall_counter = 0
            self._accelerated_stall_streak = False

        # Wiki-confirmed accelerated stalling: when EVERY living enemy has
        # the real .accelerate_stall_penalty flag (Bone Arbalist/Courtier/
        # Rabble/Soldier, Carrion Eater, Crone, Fungal Artillery, Madman,
        # Pelagic Guardian/Shaman, Swine Drummer/Skiver, Webber, and
        # others — verified against HEROES/Heroes/Occultist's own "enemies
        # that accelerate reinforcements" list), reinforcements arrive
        # after 2 stall rounds instead of 4, and the round-3 stress
        # penalty doesn't apply at all ("the party does not suffer extra
        # stress with accelerated stall penalties"). ACCELERATED_STALL_
        # SUMMON_THRESHOLD is a simulator convention for the wiki-
        # confirmed "2" — rules.json's own stall_round_accelerated_only_
        # increment/_consecutive_increment fields clearly drive this
        # mechanic too, but their exact arithmetic couldn't be pinned down
        # with confidence, so the confirmed threshold is applied directly
        # instead of guessing at an increment formula.
        if accelerated:
            if (self.stall_counter >= self.rules.stall_round_summon_effect_threshold
                    and not self._reinforcements_spawned):
                self._spawn_reinforcements()
            self._maybe_reset_stall_counter()
            return

        if self.stall_counter >= self.rules.stall_round_stress_effect_threshold:
            for hero in self.heroes:
                if hero.is_alive():
                    self._apply_stress(hero, 5)

        if (self.stall_counter >= self.rules.stall_round_summon_effect_threshold
                and not self._reinforcements_spawned):
            self._spawn_reinforcements()

        self._maybe_reset_stall_counter()

    def _maybe_reset_stall_counter(self):
        """`rules.json stall_round_reset_threshold` (4) -- the last of the
        twelve stall scalars, and the last one nothing read.

        DECLARED READING, and the argument is the naming pattern rather than
        any statement in the data: the other two thresholds on this counter are
        `stall_round_stress_effect_threshold` (3) and
        `stall_round_summon_effect_threshold` (4), both of the form
        `stall_round_<what happens>_threshold` and both meaning "at this
        counter value, do this". Read the same way, the counter cycles --
        3 charges stress, 4 charges stress and summons, then the counter goes
        back to 0 and the cycle restarts.

        What that changes: stalling used to charge +5 stress to the whole party
        EVERY round from the third onward, without limit. It now charges it on
        two rounds in every four. That is a real softening, and it is the
        direction Fran's own account of the mechanic points -- a party that
        stalls correctly is not supposed to be bled dry for it, and the
        reinforcement wave (already one-shot here) is the actual punishment.

        THE OTHER READING, recorded because implementing the wrong one is a
        regression in the opposite direction: 4 could be the number of
        consecutive NON-stalling rounds required before the counter clears,
        where this engine clears it instantly. That would make stalling
        strictly HARSHER, not softer. It is the weaker reading -- the counter
        already has a documented instant reset (>=2 stall-invalidating actions
        in a round, wiki-confirmed), and a second, slower reset on the same
        counter would need a name that says so.

        Falsifier: stall a fight past the reinforcement wave in the real game
        with a party at known stress and count the +5 ticks over eight rounds.
        Six ticks means the old behaviour, four means this one.
        """
        if self.stall_counter >= self.rules.stall_round_reset_threshold:
            self.stall_counter = 0
            self._accelerated_stall_streak = False

    def _current_enemy_tier_suffix(self) -> str:
        """A/B/C tier suffix of the current fight's own enemies, by
        majority vote across definition_id's trailing _A/_B/_C (real
        reinforcements always match the fight's own difficulty — same
        cultist_brawler/cultist_witch pair regardless of zone, just at a
        matching tier). Defaults to 'A' if empty/mixed/unparseable."""
        counts: dict[str, int] = {}
        for e in self.enemies:
            suffix = e.definition_id.rsplit("_", 1)[-1]
            if suffix in ("A", "B", "C"):
                counts[suffix] = counts.get(suffix, 0) + 1
        if not counts:
            return "A"
        return max(counts, key=lambda k: counts[k])

    def _summon_from_effect(self, eff) -> None:
        """Spawn whatever an effect's `.summon_*` fields describe.

        The semantics are a **weighted pick repeated `summon_count` times**,
        not one roll per candidate:

            NecroSummon 2  .summon_count 1
                           .summon_monsters skeleton_common_B skeleton_militia_B
                                            skeleton_defender_B
                           .summon_chances  1.0 3.0 1.0

        `summon_count 1` with three candidates and weights summing to 5 is one
        skeleton, militia-weighted — not three skeletons. Reading the chances as
        independent probabilities spawns the whole list, because two of the
        three are above 1.0 and can never fail a `random() < chance` test.

        Board space is the hard cap: a summon with nowhere to stand does not
        Draws are WITH replacement unless `.summon_erase_data_on_roll` is set.
        That flag is carried by exactly three effects -- the `stall_summon_*`
        reinforcement waves -- and it reconciles two shapes that otherwise
        contradict each other: those spawn one of each candidate, while
        `SummonTentacle A` is count 2 over a SINGLE candidate and so must be
        able to draw the same monster twice.

        appear. `.summon_limits` caps how many of that monster may be alive at
        once, and `.summon_ranks` (1-indexed) names preferred ranks.

        Skill-driven summoning was **entirely absent** until 2026-08-21 --
        `summon_count` was parsed and never read, so 54 monster skills across
        35 monsters did nothing. Four of the eight region bosses are in that
        set (Necromancer, Drowned Captain, Brigand Cannon and the Siren) and
        summoning is the whole shape of those fights; ectoplasm splitting is in
        it too.
        """
        if not eff.summon_monster_ids:
            return
        count = eff.summon_count if eff.summon_count is not None else 1
        weights = list(eff.summon_chances) or [1.0] * len(eff.summon_monster_ids)
        limits = list(eff.summon_limits)
        used: set = set()
        for _ in range(max(0, int(count))):
            occupied = {p for e in self.enemies
                        if e.is_alive() or e.is_corpse
                        for p in e.occupied_positions}
            open_positions = sorted(p for p in range(BOARD_RANKS)
                                    if p not in occupied)
            if not open_positions:
                return
            pool, w, idxs = [], [], []
            for i, mid in enumerate(eff.summon_monster_ids):
                ed = self.store.enemies.get(mid)
                if ed is None or i in used:
                    continue
                if i < len(limits):
                    alive = sum(1 for e in self.enemies
                                if e.definition_id == mid and e.is_alive())
                    if alive >= limits[i]:
                        continue
                pool.append(ed)
                w.append(weights[i] if i < len(weights) else 1.0)
                idxs.append(i)
            if not pool:
                return
            ed = self.rng.weighted_choice(pool, w)
            if eff.summon_erase_on_roll:
                used.add(idxs[pool.index(ed)])
            # `.summon_ranks` is 1-indexed and only a preference: if the rank
            # it names is taken, the summon still turns up somewhere rather
            # than silently failing.
            preferred = [r - 1 for r in eff.summon_ranks if r - 1 in open_positions]
            pos = preferred[0] if preferred else (
                min(open_positions) if ed.prefers_front else open_positions[0])
            enemy = self._build_enemy_combatant(ed, pos)
            self.enemies.append(enemy)
            self.log.append(f"{enemy.name} is summoned!")

    def _refresh_wave_limited_skills(self):
        """`.refresh_after_each_wave` -- give back the uses of any per-battle
        limited skill that carries the flag, because a new wave has landed.

        Ten shipped skills carry it. It is meaningless without
        `per_battle_limit`, so the two are read together: a skill with no
        limit has nothing to refund.
        """
        for hero in self.heroes:
            if not hero.is_alive() or not hero.skill_uses:
                continue
            for skill_id in list(hero.skill_uses):
                sd = self.get_skill_data(skill_id, level=hero.skill_tier)
                if sd is not None and sd.refresh_after_each_wave and sd.per_battle_limit:
                    hero.skill_uses[skill_id] = 0
                    self.log.append(f"{hero.name}'s {skill_id} is ready again")

    def _spawn_reinforcements(self):
        """Real, fixed, zone-independent reinforcement (stall_summon_A/B/C:
        always 2 cultist_brawler + cultist_witch at a matching tier),
        triggered once when a fight stalls too long with too few enemies —
        guaranteed board space exists since the trigger condition requires
        alive_enemies <= stall_enemy_size_threshold sustained for several
        rounds."""
        self._reinforcements_spawned = True
        suffix = self._current_enemy_tier_suffix()
        eff = self.store.effects.get(f"stall_summon_{suffix}")
        if not eff:
            return
        # Same code path as a monster's own summon skill. This used to roll
        # each candidate independently and ignore `.summon_count` entirely,
        # which happens to agree for `stall_summon_*` (two candidates at equal
        # weight, count 2) and disagrees for every skill-driven summon.
        self._summon_from_effect(eff)
        self._refresh_wave_limited_skills()

    # ── Win/loss ──

    def _check_win_loss(self):
        heroes_alive = any(h.is_alive() for h in self.heroes)
        enemies_alive = any(e.is_alive() for e in self.enemies)
        if not heroes_alive:
            self.done = True
            self.winner = "enemies"
        if not enemies_alive:
            self.done = True
            self.winner = "heroes"

    # ── Retreat ──

    def retreat_chance(self) -> float:
        """The party's odds of getting out, right now.

        `combat_retreat_chance` + `combat_retreat_bonus_chance_per_attempt` per
        failed attempt this combat — 70% + 5%, straight off `rules.json`. Not a
        convention: New Game Plus ships the same two numbers and a *higher*
        `combat_retreat_stress` (25 vs 20), which is what confirms the stress is
        the price of leaving rather than an unrelated constant."""
        return min(1.0, self.rules.combat_retreat_chance
                   + self.rules.combat_retreat_bonus_chance_per_attempt
                   * self.retreat_attempts)

    def attempt_retreat(self, performer: CombatantState) -> bool:
        """Try to break off the fight. True if the party got out.

        On success every living hero takes `combat_retreat_stress`, the combat
        ends, and `winner` is **"retreat"** — deliberately a third value rather
        than folding into "enemies". A retreat is not a defeat: nobody died, the
        party keeps its HP and its expedition, and the callers that care about
        the difference (`ExpeditionSequenceEnv`, `eval_expedition`) must be able
        to see it. Anything checking `winner == "heroes"` is unaffected.

        On failure the turn is spent and the next attempt is 5 points better.

        The stress lands on the living only: a hero on Death's Door is alive and
        pays it, a dead one is not carried out. `min_hero_count_for_escape` (3)
        is loaded but **not** implemented — no shipped file states what it gates
        and every mode ships the same 3, so there is no contrast to read it
        from. Guessing would be a convention wearing game data's clothes."""
        self.retreat_attempts += 1
        roll = self.rng.random()
        chance = self.retreat_chance()
        # Drawn before the branch so the RNG stream advances identically either
        # way — the same discipline `resolve_attack` follows for a corpse.
        if roll >= chance:
            self.log.append(
                f"{performer.name} tries to retreat and fails "
                f"({chance:.0%}); the party is still in the fight")
            return False
        for hero in self.heroes:
            if hero.is_alive():
                self._apply_stress(hero, self.rules.combat_retreat_stress)
        self.log.append(f"The party retreats ({chance:.0%})")
        self.done = True
        self.winner = "retreat"
        return True

    # ── Helpers ──

    def _find_combatant_by_position(self, position: int, enemy: bool = True,
                                     include_corpses: bool = False) -> CombatantState | None:
        """Whoever is standing on `position`, including a size-2+ monster
        reached by its BACK rank — matching what get_valid_actions offers.

        `include_corpses` stays off by default: every other caller (monster AI
        targeting, push/pull resolution) means a living combatant, and a corpse
        is only a target for a damaging hero skill that explicitly asked."""
        pool = self.enemies if enemy else self.heroes
        for c in pool:
            if c.is_alive() and position in c.occupied_positions:
                return c
        if include_corpses:
            for c in pool:
                if c.is_corpse and position in c.occupied_positions:
                    return c
        return None

    def _get_obs(self) -> dict:
        return {
            "round": self.round,
            "heroes": [
                {"id": h.id, "name": h.name, "hp": h.hp, "max_hp": h.max_hp,
                 "position": h.position, "stress": h.stress,
                 "is_deaths_door": h.is_deaths_door, "is_dead": h.is_dead}
                for h in self.heroes
            ],
            "enemies": [
                {"id": e.id, "name": e.name, "hp": e.hp, "max_hp": e.max_hp,
                 "position": e.position, "is_dead": e.is_dead}
                for e in self.enemies
            ],
        }
