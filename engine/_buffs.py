"""Trinkets, quirks, gear ranks, and every buff that is only sometimes on.

Split out of `combat_engine.py` on 2026-09-08, verbatim. Twelve methods that
call nothing outside themselves -- the second-most self-contained block in the
class after the formation.

**An unread `rule_type` is GRANTED, not skipped.** Anything not in
`_CONDITIONAL_RULE_TYPES` is treated as always-active, which matches the
behaviour from before conditional buffs existed. That default is a decision
about which way to be wrong, not a safe one: it means a rule type nobody has
implemented hands out a free stat rather than quietly dropping the buff, and
free stats are exactly what a comp search will take.

**`_CONDITIONAL_RULE_TYPES` is documentation that no code reads.**
`_is_conditional_rule_active` branches on the same fifteen names in an
if/elif chain and never consults the set. They were verified identical in both
directions on 2026-09-08 -- and `tests/engine/test_conditional_buffs.py` now
checks that, so the set cannot drift into being a lie about the chain beside
it. `tests/env/test_region_quirks.py` cites it in prose as the authority for
which rule types are conditional, which is only safe while the two agree.

**A `stat_type` with no branch is a SILENT no-op**, not an error -- the other
half of the same design, and the reason `_apply_buff_to_stats` and
`_reverse_buff_from_stats` have to stay exact mirrors of each other. A buff
that applies through one and does not reverse through the other leaks stats
for the rest of the fight.
"""

from __future__ import annotations
from data.types import BuffDefinition, SkillType
from typing import Optional
from .combatant import CombatantState
from ._constants import max_gear_rank_for_resolve


class BuffMixin:
    def apply_gear_ranks(self, hero: CombatantState, resolve_level: int,
                          weapon_rank: int, armour_rank: int) -> None:
        """Correct a hero already built by `reset()` to REAL weapon/armour ranks.

        `reset()` derives one gear tier from `hero_levels` (`tier_idx = level - 1`),
        a deliberate convention for sampled parties — it pairs a Veteran party with
        Veteran gear. A real roster does not work that way: resolve level and the
        two upgrade tracks are three independent numbers, and a save carries all
        three.

        Measured cost of ignoring it: a resolve-2 Vestal with rank-2 armour has 34
        max HP; the convention gave her tier 1 and **29**. `_apply_carry_over`
        clamps `hp = min(carry.hp, max_hp)`, so her real 34 HP silently truncated
        to 29/29 — a damaged hero reading as full. Every stat the tiers carry was
        off the same way, skill damage included, since `skill_tier` selects the
        skill's per-level row.

        Applied as a DELTA against the tier `reset()` used, so torchlight
        modifiers and anything else already added stay intact. Call it BEFORE
        trinkets and quirks, matching the engine's own base-stats-then-buffs
        order.
        """
        hd = self.store.heroes.get(hero.definition_id)
        if hd is None or not hd.armour_tiers or not hd.weapon_tiers:
            return
        # MUST match the tier `reset()` picked, or this delta is computed
        # against a baseline the hero was never built on. It read
        # `resolve_level - 1` because that was reset()'s old formula;
        # changing reset() to the real upgrade gating without changing this
        # silently inflated every real-gear hero by the difference (a
        # resolve-2 rank-2 Vestal went 34 -> 39 max HP).
        used = max(0, min(len(hd.armour_tiers) - 1,
                          max_gear_rank_for_resolve(resolve_level) - 1))
        arm = max(0, min(armour_rank, len(hd.armour_tiers) - 1))
        wpn = max(0, min(weapon_rank, len(hd.weapon_tiers) - 1))
        a_used, a_real = hd.armour_tiers[used], hd.armour_tiers[arm]
        w_used, w_real = hd.weapon_tiers[used], hd.weapon_tiers[wpn]

        hero.max_hp += a_real.hp - a_used.hp
        hero.dodge += a_real.dodge - a_used.dodge
        hero.prot += a_real.prot - a_used.prot
        hero.speed += (a_real.spd_bonus - a_used.spd_bonus) + (w_real.spd_bonus - w_used.spd_bonus)
        hero.crit += w_real.crit_bonus - w_used.crit_bonus
        hero.atk_bonus += w_real.atk_bonus - w_used.atk_bonus
        hero.dmg_min += w_real.dmg_min - w_used.dmg_min
        hero.dmg_max += w_real.dmg_max - w_used.dmg_max
        # Skill damage rows are selected by weapon rank, not by resolve.
        hero.skill_tier = wpn
        hero.max_hp = max(1, hero.max_hp)
        hero.hp = min(hero.hp, hero.max_hp)

    def apply_trinkets_and_quirks(self, hero: CombatantState,
                                   trinket_ids: list[str], quirk_ids: list[str]):
        hero.trinket_ids = list(trinket_ids)
        worn_sets: dict[str, int] = {}
        for tid in trinket_ids:
            td = self.store.trinkets.get(tid)
            if td:
                self._apply_buff_ids(hero, td.buffs)
                if td.set_id:
                    worn_sets[td.set_id] = worn_sets.get(td.set_id, 0) + 1
        # A SET BONUS needs two members of the same set on ONE hero. The
        # threshold is 2 because every shipped set has exactly two members --
        # asserted by tests/test_trinket_sets.py rather than assumed, so a mod
        # shipping a three-piece set fails loudly instead of paying out early.
        for set_id, worn in worn_sets.items():
            if worn >= 2:
                self._apply_buff_ids(hero, self.store.trinket_sets.get(set_id, []))
        for qid in quirk_ids:
            qd = self.store.quirks.get(qid)
            if qd:
                self._apply_buff_ids(hero, qd.buffs)
        # Conditions (rank/HP/round/guard) are already current at this
        # point in reset() -- evaluate once now so an already-true
        # condition (e.g. a rank-3 trinket on a hero placed at rank 3)
        # takes effect immediately rather than waiting for the first
        # _sync_all_conditional_buffs call in step().
        self._sync_conditional_buffs(hero)

    def _apply_buff_ids(self, hero: CombatantState, buff_ids: list[str]):
        for buff_id in buff_ids:
            bd = self.store.buffs.get(buff_id)
            if not bd:
                continue
            if bd.rule_type == "always":
                self._apply_buff_to_stats(hero, bd)
            else:
                # Real trinket/quirk buff whose activation depends on
                # in-combat state that can change (rank, HP, guard, Death's
                # Door, round, stress, melee/ranged, a target's status) --
                # deferred to _sync_conditional_buffs instead of baked in
                # once here. See CombatantState.conditional_buffs.
                hero.conditional_buffs.append(bd)

    # Rule types this evaluates (all real, verified against
    # shared/buffs/base.buffs.json): in_rank, meleeonly, rangedonly,
    # hpbelow, hpabove, at_deaths_door, is_guarded, firstroundonly,
    # stress_above, actorStatus/is_actor_status, lightbelow/lightabove.
    #
    # The two light rules were added last (2026-08-21) and are the reason to
    # distrust this list: torchlight has been modelled all along, so 234
    # torch-gated buffs -- 139 trinkets and 12 quirks, 1.36 equipped per real
    # 4-hero party -- were firing in broad daylight. An unevaluated rule type
    # is NOT inert; it is the buff granted unconditionally. Rank a candidate
    # by what an expedition actually equips, not by how many definitions
    # exist.
    #
    # Deliberately NOT implemented: afflicted/virtued. The state to evaluate
    # them now exists (`CombatantState.affliction_id`/`virtue_id`, added with
    # the trait work), so the old reason -- "this simulator has no personality
    # subsystem" -- is stale. The current reason is that these gate only the
    # ~36 personality buffs (afflictionFearfulBuff*, virtueStalwartBuff*),
    # which `data/trait_loader.py` attaches BECAUSE the hero broke that way,
    # so the gate is already satisfied by construction. Zero trinkets and zero
    # quirks carry one. Also not implemented: monster-matchup rules
    # (monsterType/monsterSize/
    # attacking_monster_type/monster_type_count_min -- real but narrow
    # "extra resist/damage vs a specific monster tag" trinkets, and
    # town/traversal rules (in_corridor/in_activity/skill(build_to_finale)) --
    # these describe where the party is, and inside a combat the answer is
    # unanswerable (in_activity is a town screen, 0 equipped).
    #
    # **`in_dungeon` IS evaluated now** (2026-08-27). It used to sit in that
    # list on the grounds that "0 equipped" carry it, which was true when the
    # note was written and stopped being true the moment `tools/comp_ga.py`
    # started choosing quirks freely: the 15 region quirks
    # (`<region>_adventurer/_explorer/_scrounger/_tactician/_phobe`, ruins,
    # cove and weald only -- the Warrens has none) all carry it, and while it
    # defaulted to always-active a Cove Scrounger paid out in full in the
    # Weald. Fran spotted it in the emitted comps: *"recommends quirks for
    # other regions ... they shouldn't work on other regions"*. Free stats are
    # exactly what a search will take.
    # `in_camp` is the exception and IS evaluated, to False -- see its branch.
    # Any rule_type not listed below is treated as always-active (matches
    # pre-fix behavior) rather than silently dropping the buff. That default
    # is a decision about which way to be wrong, not a safe one.
    _CONDITIONAL_RULE_TYPES = frozenset({
        "in_rank", "meleeonly", "rangedonly", "hpbelow", "hpabove",
        "at_deaths_door", "is_guarded", "firstroundonly", "stress_above",
        "actorStatus", "is_actor_status", "lightbelow", "lightabove",
        "in_camp", "in_dungeon",
    })

    def _actor_has_status(self, c: CombatantState, status: str) -> bool:
        if status == "tagged":
            return c.is_marked
        if status == "bleeding":
            return bool(c.bleeds)
        if status in ("poisoned", "blighted"):
            return bool(c.blights)
        if status == "burning":
            return c.burn_stacks > 0
        if status == "stunned":
            return c.is_stunned
        return False

    def _get_skill_type(self, skill_id: Optional[str]) -> Optional[SkillType]:
        if not skill_id:
            return None
        for hd in self.store.heroes.values():
            for sk in hd.skills:
                if sk.id == skill_id:
                    return sk.skill_type
        for ed in self.store.enemies.values():
            for sk in ed.skills:
                if sk.id == skill_id:
                    return sk.skill_type
        return None

    def _is_conditional_rule_active(self, c: CombatantState, bd: BuffDefinition,
                                      skill_id: Optional[str] = None) -> bool:
        rt = bd.rule_type
        if rt == "in_rank":
            result = c.position == int(bd.rule_data_float)
        elif rt == "meleeonly":
            result = self._get_skill_type(skill_id) == SkillType.MELEE
        elif rt == "rangedonly":
            result = self._get_skill_type(skill_id) == SkillType.RANGED
        elif rt == "in_dungeon":
            # Unknown zone means the caller never said where the fight is, and
            # a buff must not be silently withdrawn on that: `zone_id` is
            # optional on `reset` precisely so every existing call site keeps
            # its behaviour, so an unset zone grants the buff exactly as
            # before. Only a caller that HAS named a zone gets the gate.
            result = (not self.zone_id
                      or bd.rule_data_string.lower() == self.zone_id.lower())
        elif rt == "hpbelow":
            result = c.max_hp > 0 and (c.hp / c.max_hp) < bd.rule_data_float
        elif rt == "hpabove":
            result = c.max_hp > 0 and (c.hp / c.max_hp) > bd.rule_data_float
        elif rt == "at_deaths_door":
            result = c.is_deaths_door
        elif rt == "is_guarded":
            result = c.guard_target_id is not None
        elif rt == "firstroundonly":
            result = self.round == 0
        elif rt == "stress_above":
            result = c.stress > bd.rule_data_float
        elif rt in ("actorStatus", "is_actor_status"):
            result = self._actor_has_status(c, bd.rule_data_string)
        elif rt == "lightbelow":
            # BOTH light comparisons are STRICT, and the shipped thresholds
            # are what say so rather than a convention of ours. The darkness
            # bands exclude their lower bound (`lowest_excluded: true`, and
            # `_get_darkness_band` reads them as `lower < torch <= upper`), so the
            # band tops are 25 / 50 / 75. The two thresholds that dominate the
            # data are `lightbelow 26` (105 trinkets) and `lightabove 75` (56)
            # -- and those land exactly on a band edge ONLY under `<` and `>`:
            #   lightbelow 26  ->  torch <= 25  ->  precisely "black or pitch"
            #   lightabove 75  ->  torch  > 75  ->  precisely "radiant"
            # Read inclusively they would each spill one point into the
            # neighbouring band and name nothing. The light quirks confirm the
            # direction independently: `lygophobia` (fear of the dark) is
            # +20% stress taken at `lightbelow 26`, `phengophobia` (fear of
            # daylight) is +20% at `lightabove 75`, and `nocturnal`/`diurnal`
            # are -2 SPD in the light / in the dark respectively.
            # The shipped data is not perfectly tidy -- `dark_bracer` pairs
            # `lightbelow 51` with `lightabove 51`, so torch exactly 51 fires
            # neither. That one-point dead zone is in the game's own numbers,
            # not introduced here. Falsifier in ASSUMPTIONS.md.
            result = self.torch_level < bd.rule_data_float
        elif rt == "lightabove":
            result = self.torch_level > bd.rule_data_float
        elif rt == "in_camp":
            # Constantly False, and that is the whole rule: this class is
            # CombatEngine, camping is `engine/camping.py`, and camping does
            # not route through the buff machinery at all. So an `in_camp`
            # buff can never be active in anything this function sees.
            #
            # It was reaching the always-active fallback, which is the
            # opposite of inert: 25 instances are equipped across the real
            # comps (`cc_set_vest_salacious_diary` +33% stress healing,
            # `campers_helmet` +20% stress healing received), and
            # `stress_heal_percent`/`stress_heal_received_percent` are both
            # wired -- so the party was getting its campfire bonuses in the
            # middle of a fight. Unlike the light rules this one only ever
            # takes something away.
            result = False
        else:
            return True
        return result != bd.is_false_rule

    def _sync_conditional_buffs(self, c: CombatantState, skill_id: Optional[str] = None):
        for bd in c.conditional_buffs:
            active = self._is_conditional_rule_active(c, bd, skill_id)
            applied = bd.id in c.active_conditional_buffs
            if active and not applied:
                delta = self._apply_buff_to_stats(c, bd)
                c.active_conditional_buffs[bd.id] = delta
            elif not active and applied:
                delta = c.active_conditional_buffs.pop(bd.id)
                self._reverse_buff_from_stats(c, bd, applied_delta=delta)

    def _sync_all_conditional_buffs(self, performer: Optional[CombatantState] = None,
                                      skill_id: Optional[str] = None):
        """Re-evaluate every living combatant's conditional trinket/quirk
        buffs against current state (rank, HP, guard, round, stress, a
        target's status) -- called once per action so rank/HP changes from
        the previous action (a kill, a move, a heal) are reflected before
        the next attack resolves against either side. Only `performer` gets
        `skill_id`-dependent evaluation (meleeonly/rangedonly), since that's
        about which skill THEY are about to use this action, not a standing
        state of whoever else is on the field."""
        for c in self.heroes + self.enemies:
            if not c.is_alive():
                continue
            self._sync_conditional_buffs(c, skill_id if c is performer else None)

    def _apply_buff_to_stats(self, c: CombatantState, bd: BuffDefinition) -> Optional[int]:
        """Returns the exact integer delta applied, but only for
        combat_stat_multiply on an int-typed stat (max_hp/damage_low/
        damage_high) — callers that track this buff via ActiveBuff for
        later reversal (_resolve_effect) should store it on
        ActiveBuff.applied_delta so _reverse_buff_from_stats can subtract
        the precise recorded delta instead of re-deriving it by dividing
        back through the multiplier, which can drift by rounding on
        repeated apply/reverse cycles. All other stat types are exact
        (float add/subtract) and don't need this."""
        st = bd.stat_type
        sst = bd.stat_sub_type
        amt = bd.amount
        if st == "combat_stat_multiply":
            if sst == "max_hp":
                before = c.max_hp
                c.max_hp = max(1, int(c.max_hp * (1 + amt)))
                c.hp = min(c.hp, c.max_hp)
                return c.max_hp - before
            elif sst == "damage_low":
                before = c.dmg_min
                c.dmg_min = max(1, int(c.dmg_min * (1 + amt)))
                return c.dmg_min - before
            elif sst == "damage_high":
                before = c.dmg_max
                c.dmg_max = max(1, int(c.dmg_max * (1 + amt)))
                return c.dmg_max - before
        elif st == "combat_stat_add":
            if sst == "max_hp":
                c.max_hp = max(1, c.max_hp + int(amt))
                c.hp = min(c.hp, c.max_hp)
            elif sst == "damage_low":
                c.dmg_min = max(1, c.dmg_min + int(amt))
            elif sst == "damage_high":
                c.dmg_max = max(1, c.dmg_max + int(amt))
            elif sst == "attack_rating" or sst == "attack_rating_add":
                c.atk_bonus += amt
            elif sst == "crit_chance" or sst == "crit_chance_add":
                c.crit += amt
            # "defense_rating" is the game's own name for dodge, and it is what
            # 95 of base.buffs.json's entries actually say — the 7th most
            # common buff shape in the file. Only "defense"/"dodge" were
            # matched here, so every one of those 95 (trinket, quirk, skill and
            # trait sourced alike) silently did nothing. Same bug class as the
            # historic dmg_min/dmg_max and .dotStress key mismatches: a real
            # mechanic disabled by a name the parser never looked for.
            elif sst in ("defense", "dodge", "defense_rating"):
                c.dodge += amt
            elif sst == "protection_rating" or sst == "prot":
                c.prot = min(0.95, max(0, c.prot + amt))
            elif sst == "speed" or sst == "speed_rating":
                c.speed += int(amt)
        elif st == "resistance":
            if sst == "stun":
                c.stun_resist += amt
            elif sst == "poison" or sst == "blight":
                c.blight_resist += amt
            elif sst == "bleed":
                c.bleed_resist += amt
            elif sst == "debuff":
                c.debuff_resist += amt
            elif sst == "move":
                c.move_resist += amt
            elif sst == "disease":
                pass  # not tracked in combat
            elif sst == "death_blow":
                c.death_blow_resist += amt
        elif st == "party_surprise_chance":
            c.party_surprise_chance_mod += amt
        elif st == "monsters_surprise_chance":
            c.monsters_surprise_chance_mod += amt
        elif st == "stress_dmg_received_percent":
            c.stress_dmg_received_mult += amt
        elif st == "hp_heal_received_percent":
            # The sub type names WHICH healing. 'eat' is food only (the
            # Granary); everything else is healing generally.
            if sst == "eat":
                c.eat_heal_received_mult += amt
            else:
                c.hp_heal_received_mult += amt
        elif st == "hp_heal_percent":
            c.hp_heal_produced_mult += amt
        elif st == "stress_heal_received_percent":
            c.stress_heal_received_mult += amt
        elif st == "stress_heal_percent":
            c.stress_heal_produced_mult += amt
        elif st == "poison_chance":
            c.blight_chance_bonus += amt
        elif st == "bleed_chance":
            c.bleed_chance_bonus += amt
        elif st == "stun_chance":
            c.stun_chance_bonus += amt
        elif st == "move_chance":
            c.move_chance_bonus += amt
        elif st == "debuff_chance":
            c.debuff_chance_bonus += amt
        elif st == "resolve_check_percent":
            c.resolve_check_mod += amt
        elif st == "crit_received_chance":
            c.crit_received_bonus += amt
        elif st == "hp_dot_burn_decay_percent":
            c.burn_decay_bonus += amt
        elif st == "hp_dot_burn_amount_percent":
            c.burn_amount_bonus += amt
        elif st == "hp_dot_burn_amount_received_percent":
            c.burn_amount_received_bonus += amt
        elif st == "hp_dot_burn_amount_percent":
            c.burn_amount_bonus += amt
        elif st == "hp_dot_burn_amount_received_percent":
            c.burn_amount_received_bonus += amt

    def _reverse_buff_from_stats(self, c: CombatantState, bd: BuffDefinition,
                                   applied_delta: Optional[int] = None):
        """Inverse of _apply_buff_to_stats — used when a duration-tracked
        buff expires (_tick_buff_durations) or a death's door debuff needs
        undoing (recovery via heal, see _apply_heal). Only previously
        handled stun resistance specifically; generalized here so any buff
        applied through _apply_buff_to_stats can be cleanly undone.

        combat_stat_multiply on an int-typed stat (max_hp/damage_low/
        damage_high): when the caller has the exact delta _apply_buff_to_stats
        returned at apply time (ActiveBuff.applied_delta, tracked by
        _tick_buff_durations' reversal path), subtract that precise value —
        exact, no rounding. Falls back to dividing back through the same
        multiplier only when no delta was recorded (e.g. death's door debuff
        reversal via _reverse_deaths_door_buffs, which isn't ActiveBuff-
        tracked) — that fallback can drift by rounding on repeated
        apply/reverse cycles (e.g. int(int(3*0.75)/0.75) != 3 in some cases).
        combat_stat_add and resistance reversals are always exact either way.
        """
        st = bd.stat_type
        sst = bd.stat_sub_type
        amt = bd.amount
        if st == "combat_stat_multiply":
            if applied_delta is not None:
                if sst == "max_hp":
                    c.max_hp = max(1, c.max_hp - applied_delta)
                    c.hp = min(c.hp, c.max_hp)
                elif sst == "damage_low":
                    c.dmg_min = max(1, c.dmg_min - applied_delta)
                elif sst == "damage_high":
                    c.dmg_max = max(1, c.dmg_max - applied_delta)
                return
            divisor = 1 + amt
            if divisor == 0:
                return
            if sst == "max_hp":
                c.max_hp = max(1, int(c.max_hp / divisor))
                c.hp = min(c.hp, c.max_hp)
            elif sst == "damage_low":
                c.dmg_min = max(1, int(c.dmg_min / divisor))
            elif sst == "damage_high":
                c.dmg_max = max(1, int(c.dmg_max / divisor))
        elif st == "combat_stat_add":
            if sst == "max_hp":
                c.max_hp = max(1, c.max_hp - int(amt))
                c.hp = min(c.hp, c.max_hp)
            elif sst == "damage_low":
                c.dmg_min = max(1, c.dmg_min - int(amt))
            elif sst == "damage_high":
                c.dmg_max = max(1, c.dmg_max - int(amt))
            elif sst == "attack_rating" or sst == "attack_rating_add":
                c.atk_bonus -= amt
            elif sst == "crit_chance" or sst == "crit_chance_add":
                c.crit -= amt
            elif sst in ("defense", "dodge", "defense_rating"):
                c.dodge -= amt
            elif sst == "protection_rating" or sst == "prot":
                c.prot = min(0.95, max(0, c.prot - amt))
            elif sst == "speed" or sst == "speed_rating":
                c.speed -= int(amt)
        elif st == "resistance":
            if sst == "stun":
                c.stun_resist -= amt
            elif sst == "poison" or sst == "blight":
                c.blight_resist -= amt
            elif sst == "bleed":
                c.bleed_resist -= amt
            elif sst == "debuff":
                c.debuff_resist -= amt
            elif sst == "move":
                c.move_resist -= amt
            elif sst == "death_blow":
                c.death_blow_resist -= amt
        elif st == "stress_dmg_received_percent":
            c.stress_dmg_received_mult -= amt
        elif st == "hp_heal_received_percent":
            if sst == "eat":
                c.eat_heal_received_mult -= amt
            else:
                c.hp_heal_received_mult -= amt
        elif st == "hp_heal_percent":
            c.hp_heal_produced_mult -= amt
        elif st == "stress_heal_received_percent":
            c.stress_heal_received_mult -= amt
        elif st == "stress_heal_percent":
            c.stress_heal_produced_mult -= amt
        elif st == "poison_chance":
            c.blight_chance_bonus -= amt
        elif st == "bleed_chance":
            c.bleed_chance_bonus -= amt
        elif st == "stun_chance":
            c.stun_chance_bonus -= amt
        elif st == "move_chance":
            c.move_chance_bonus -= amt
        elif st == "debuff_chance":
            c.debuff_chance_bonus -= amt
        elif st == "resolve_check_percent":
            c.resolve_check_mod -= amt
        elif st == "crit_received_chance":
            c.crit_received_bonus -= amt
        elif st == "hp_dot_burn_decay_percent":
            c.burn_decay_bonus -= amt
        elif st == "hp_dot_burn_amount_percent":
            c.burn_amount_bonus -= amt
        elif st == "hp_dot_burn_amount_received_percent":
            c.burn_amount_received_bonus -= amt
        elif st == "hp_dot_burn_decay_percent":
            c.burn_decay_bonus -= amt
        elif st == "hp_dot_burn_amount_percent":
            c.burn_amount_bonus -= amt
        elif st == "hp_dot_burn_amount_received_percent":
            c.burn_amount_received_bonus -= amt

    def _compute_buff_stat(self, c: CombatantState, stat_type: str,
                            stat_sub_type: str, default: float = 0.0) -> float:
        total = default
        for buff in c.buffs:
            bd = self.store.buffs.get(buff.buff_id)
            if bd and bd.stat_type == stat_type and bd.stat_sub_type == stat_sub_type:
                total += bd.amount
        return total

    def _resolve_named_buff_amount(self, buff_id: str, stat_type: str, stat_sub_type: str) -> float:
        """Look up a buff by id (e.g. referenced via an effect's .buff_ids) and
        return its amount if it matches the given stat type, else 0.

        `stealthed_crit_chance_buff` (referenced by the "StealthedCrit" effect
        on attacks made from stealth) does not resolve here: it isn't present
        in shared/buffs/base.buffs.json under that id, and EffectLoader doesn't
        parse .buff_ids yet (only guard/riposte/stealth's own dedicated fields
        were added in this pass). Declared here rather than guessing a bonus
        value — see ASSUMPTIONS.md.
        """
        bd = self.store.buffs.get(buff_id)
        if bd and bd.stat_type == stat_type and bd.stat_sub_type == stat_sub_type:
            return bd.amount
        return 0.0
