"""What a skill actually DOES to what it hits.

Split out of `combat_engine.py` on 2026-09-08, verbatim. The largest single
block in the class, and `_apply_effect_to_target` alone is 529 lines -- the
biggest method in the engine, because it is the one switch every status,
buff, DoT, move, stun, guard, mark, summon and heal passes through.

**A status effect is ONE roll, and the resist is subtracted.** Not a roll per
stack, not a roll per target -- `_effect_resist` prices it once. That rule is
worth knowing before editing anything here, because the RNG stream is
load-bearing: an extra draw on a resisted effect shifts every subsequent roll
in the fight, which is exactly what `tools/trajectory_oracle.py` is built to
catch.

**An unread `rule_type` is GRANTED, not skipped**, and a `stat_type` with no
branch is a silent no-op rather than an error. Both are deliberate and both
have bitten: see `docs/CHANGELOG.md`.

This block calls freely into the other mixins -- `_apply_stress` from the
afflictions, `paint_burn_zone` from the DoTs, `_shift_to_position` and the two
`_collapse_*` from the formation. That is the mixin design paying off: they
all resolve on the instance through the MRO, so an effect that moves a hero
still closes ranks and an effect that burns still paints the tile.
"""

from __future__ import annotations
from data.types import BuffDefinition, EffectDefinition, SkillLevelData
from typing import Optional
from .combatant import ActiveBuff, CombatantState, DotInstance, RiposteState
from ._constants import BOARD_RANKS, INLINE_STAT_FIELD_MAP, _crit_dot_rounds


class EffectsMixin:
    def _apply_skill_effects(self, target: CombatantState, performer: CombatantState,
                              skill: SkillLevelData, hit: bool,
                              effect_names: Optional[list[str]] = None,
                              is_crit: bool = False):
        for ename in (skill.effects if effect_names is None else effect_names):
            eff = self.store.effects.get(ename)
            if not eff:
                continue
            if hit and not eff.on_hit:
                continue
            if not hit and not eff.on_miss:
                continue
            self._resolve_effect(target, performer, eff, skill, is_crit=is_crit)

    def _split_group_effect_names(self, skill: SkillLevelData) -> tuple[list[str], list[str]]:
        """(once_per_action_effect_names, per_target_effect_names) -- two
        independent real signals both mean an effect must fire once per
        ACTION, not once per AoE target hit, on a multi-target skill (AoE
        attack/heal/ally-buff):

        1. `.target` is "performer_group_other"/"performer_group" -- the
           effect already resolves to a whole side by itself (see
           _resolve_effect's dispatcher). Real bug found and fixed:
           Arbalest/Musketeer's Rallying Flare ("flare") is an AoE attack
           against the enemy side whose "FlareHealStress N"/"FlareClear N"
           effects redirect to the ally side -- with 3 living enemies, a
           single use could heal 0, 3, 6, 9, or even 12 stress depending on
           how many of them were hit, instead of the flat "small Stress
           heal" the real skill is (HEROES/Heroes/Arbalest doc).
        2. The real `.apply_once` field (`EffectDefinition.is_apply_once`,
           parsed since an earlier session but never actually consulted
           anywhere until now) -- a second, independently confirmed real
           bug, much broader than the first: found 57 real cases of a
           `target: "performer"` effect nested in an AoE skill, and every
           single one already has `.apply_once true` in the real data.
           Concrete, high-impact examples: Hellion's own signature
           `barbaric_yawp` (her double-frontline-stun) and `breakthru`
           would double/triple-stack her "Hellion Exhaust" self-debuff if
           more than one enemy target was hit; Jester's `battle_ballad`/
           `harvest`/`solo` would over-charge her "Build to Finale"
           stacking counter; Occultist's `abyssal_artillery` would
           double-stack "Abyssal Killer"; several monster AoE skills
           (Shambler's tentacle summons, Necromancer's reinforcement
           summons) would spawn extra, unintended summons per AoE hit.
        """
        once_names = [n for n in skill.effects if (e := self.store.effects.get(n)) and
                       (e.target in ("performer_group_other", "performer_group", "global")
                        or e.is_apply_once)]
        if not once_names:
            return [], skill.effects
        per_target_names = [n for n in skill.effects if n not in once_names]
        return once_names, per_target_names

    def _apply_self_effects(self, performer: CombatantState, skill: SkillLevelData):
        for ename in skill.self_effects:
            eff = self.store.effects.get(ename)
            if eff:
                self._resolve_effect(performer, performer, eff, skill)

    def _resolve_effect(self, target: CombatantState, performer: CombatantState,
                         eff: EffectDefinition, skill: SkillLevelData,
                         is_crit: bool = False):
        if eff.target in ("performer_group_other", "performer_group"):
            # Real, high-impact bug found and fixed: _get_effect_target
            # only ever resolved this to ONE ally (whichever came first in
            # the side list), silently under-applying every party-wide
            # effect in the game to just 1 of typically 3 other members
            # instead of all of them. Confirmed to affect, among others,
            # Abomination's Transform (both directions -- "beast_stress_
            # party" +8 stress human->beast, "human_stress_heal_party_N"
            # -2 stress beast->human -- a mechanic this project's own
            # Abomination fidelity audit already touched this session
            # without catching this), Flagellant's death's-door-enter
            # party heal ("flagellant_death_door_enter"), and Arbalest's
            # Rallying Flare ("FlareHealStress N"/"FlareBuff N").
            side = self.heroes if performer.is_hero else self.enemies
            members = [c for c in side if c.is_alive() and
                       (eff.target == "performer_group" or c.id != performer.id)]
            for member in members:
                self._apply_effect_to_target(member, target, performer, eff, skill, is_crit)
            return
        eff_target = self._get_effect_target(target, performer, eff.target)
        self._apply_effect_to_target(eff_target, target, performer, eff, skill, is_crit)

    def _apply_effect_to_target(self, eff_target: CombatantState, target: CombatantState,
                                  performer: CombatantState, eff: EffectDefinition,
                                  skill: SkillLevelData, is_crit: bool = False):
        # Wiki-confirmed crit bonuses: "All status effects applied on a
        # critical hit have a 20% higher chance of applying, and Bleed and
        # Blight will last 50% longer, rounded up, or 2 additional turns for
        # most skills." Neither was modelled — a crit applied exactly the same
        # status, for the same duration, as a normal hit.
        # `-rules.effect_crit_roll_mod`, not a hardcoded 0.20. The wiki's "20%
        # higher chance of applying" was right, and `rules.json` has been
        # shipping the same number as `effect_crit_roll_mod: -0.2` the whole
        # time -- expressed as a modifier on the ROLL, which is why it is
        # negative. `roll + mod < chance` and `roll < chance - mod` are the
        # same predicate; nothing clamps either side, so unlike the accuracy
        # cutoffs (DECOMP F4) the two forms cannot diverge. Reading it from the
        # data means a patch or a mod moves it.
        chance = eff.chance - (self.rules.effect_crit_roll_mod if is_crit else 0.0)
        roll = self.rng.random()
        if roll >= chance:
            self.vlog(f"    [EFFECT] '{eff.name}' on {eff_target.name}: "
                      f"chance {chance:.0%} | roll {roll:.3f} => did not apply")
            return
        # ONE roll, and the resist is SUBTRACTED from the chance it is compared
        # against. This engine used to roll twice -- `.chance` here, then a
        # second roll against the resist inside each payload -- and that made
        # every chance above 100% mean exactly 100%. The shipped data says
        # otherwise: 572 of 1973 effects carry `.chance` above 1.0, laddered per
        # skill rank (1.0 / 1.2 / 1.4 / 1.6) and running as high as 5.0 on Guard
        # Break. Headroom over 100% is only worth writing down if something is
        # taken off it, so under the two-roll reading 29% of the game's effects
        # carried dead numbers and a rank-5 skill applied its status exactly as
        # often as a rank-1 one. `effect_crit_roll_mod` agrees: rules.json ships
        # the crit bonus as a modifier on the ROLL, which is the shape of a
        # single comparison. See ASSUMPTIONS.md for the falsifier.
        #
        # Two comparisons off one draw, not two draws: `roll < chance` gates
        # what cannot be resisted (stress, heals, guard, shuffle, and Mark --
        # the wiki is explicit that Mark cannot be resisted by anyone), and
        # `roll < chance - resist` gates the resistable payload. So Occultist's
        # Vulnerability Curse still marks when its stat debuff is resisted.
        resist_type, effective_resist = self._effect_resist(eff, eff_target, performer)
        resisted = roll >= chance - effective_resist
        self.vlog(f"    [EFFECT] '{eff.name}' on {eff_target.name}: "
                  f"chance {chance:.0%} | roll {roll:.3f} => applies"
                  + (f" | {resist_type.upper()} RES {effective_resist:.0%} => "
                     + ("RESISTED" if resisted else "not resisted")
                     if resist_type else ""))
        if resisted:
            self.log.append(f"{eff_target.name} resists {eff.name}")

        if eff.bleed_amount is not None:
            self.vlog(f"      [BLEED] {eff.bleed_amount}/rd x {eff.bleed_rounds} rds "
                      + ("RESISTED" if resisted else
                         f"APPLIED (total {eff.bleed_amount*(eff.bleed_rounds or 3)} over "
                         f"{eff.bleed_rounds} rds)"))
            if not resisted:
                eff_target.bleeds.append(DotInstance(
                    effect_name=eff.name,
                    damage_per_tick=eff.bleed_amount,
                    remaining_ticks=_crit_dot_rounds(eff.bleed_rounds or 3, is_crit),
                    chance=1.0,
                ))
                self.log.append(f"{eff_target.name} bleeding {eff.bleed_amount}/rd for {eff.bleed_rounds} rds")

        if eff.controlled_burn_amount is not None:
            # The effect names a TARGET, but what it paints is the target's
            # RANK -- see `paint_burn_zone`. Read off the rank the target
            # occupies at resolution time, which for a large monster is its
            # FRONT rank, matching where the skill was aimed.
            self.paint_burn_zone(eff_target.position,
                                 eff.controlled_burn_amount,
                                 eff.controlled_burn_duration or 3)

        if eff.burn_amount is not None:
            # No resist roll. Every shipped burn effect carries
            # `.dot_irresistible true`, and there is no `burn_resist` stat in
            # any shipped file to roll against even if one did not -- so the
            # flag is honoured by SKIPPING the roll rather than by rolling
            # against a zero, which would silently consume an RNG draw and
            # desynchronise every trace comparison after it.
            amount = eff.burn_amount * (1.0 + performer.burn_amount_bonus
                                        + eff_target.burn_amount_received_bonus)
            gained = max(0, int(round(amount)))
            # Burn STACKS additively onto what is already burning, unlike a
            # second bleed, which queues its own independent instance.
            eff_target.burn_stacks += gained
            self.vlog(f"      [BURN] +{gained} stacks"
                      + ("" if eff.dot_irresistible else " (no .dot_irresistible, "
                         "but no burn_resist stat exists either)")
                      + f" => {eff_target.burn_stacks} on {eff_target.name}")
            self.log.append(f"{eff_target.name} burning {eff_target.burn_stacks}")

        if eff.blight_amount is not None:
            self.vlog(f"      [BLIGHT] {eff.blight_amount}/rd x {eff.blight_rounds} rds "
                      + ("RESISTED" if resisted else
                         f"APPLIED (total {eff.blight_amount*(eff.blight_rounds or 3)} over "
                         f"{eff.blight_rounds} rds)"))
            if not resisted:
                eff_target.blights.append(DotInstance(
                    effect_name=eff.name,
                    damage_per_tick=eff.blight_amount,
                    remaining_ticks=_crit_dot_rounds(eff.blight_rounds or 3, is_crit),
                    chance=1.0,
                ))
                self.log.append(f"{eff_target.name} blighted {eff.blight_amount}/rd for {eff.blight_rounds} rds")

        if eff.stun_chance is not None:
            self.vlog(f"      [STUN] => {'RESISTED' if resisted else 'STUNNED'}")
            if not resisted:
                eff_target.is_stunned = True
                self.log.append(f"{eff_target.name} stunned!")
                self._break_guard_if_guarding(eff_target)

        if eff.stress_damage_per_round is not None and eff_target.is_hero:
            # Horror — no dedicated resist stat in the real game (unlike
            # bleed/blight), so infliction is gated only by the eff.chance
            # roll already applied above; stress-received modifiers
            # (stress_dmg_received_mult) apply per-tick via _apply_stress,
            # same as any other stress source.
            eff_target.stress_dots.append(DotInstance(
                effect_name=eff.name,
                damage_per_tick=eff.stress_damage_per_round,
                remaining_ticks=eff.stress_dot_rounds or 3,
                chance=1.0,
            ))
            self.log.append(f"{eff_target.name} horrified {eff.stress_damage_per_round}/rd for {eff.stress_dot_rounds} rds")

        # Real gap found and fixed: push/pull used to apply unconditionally.
        # move_resist was parsed, buffed and debuffed correctly, but its only
        # consumer in the whole engine was monster-brain target weighting —
        # nothing ever rolled against it, so every knockback and pull landed
        # regardless of the target's Move Resist. Man-at-Arms' Rampart,
        # Bounty Hunter's Come Hither, Shieldbreaker's Puncture and every
        # monster shuffle were all unresistable.
        # Shuffle, and it is NOT gated by MOVE RES: push and pull carry the
        # distance that the move-resist roll reads, and none of the 24 shuffle
        # effects carries anything to roll against. The `.chance` roll in
        # `_apply_effect_to_target` is the only gate -- which is why Shuffle
        # Party ships at 80-150%, an over-100% chance being a certainty rather
        # than something to resist.
        if eff.shuffle_party:
            self._scramble_side(eff_target.is_hero)
            side = "party" if eff_target.is_hero else "monsters"
            self.log.append(f"The {side} are shuffled!")
        elif eff.shuffle_target:
            was = eff_target.position
            self._scramble_one(eff_target)
            if eff_target.position != was:
                self.log.append(
                    f"{eff_target.name} is shuffled from {was+1} "
                    f"to {eff_target.position+1}")

        if (eff.push_distance and eff.push_distance > 0) or \
           (eff.pull_distance and eff.pull_distance > 0):
            self._vlog_resist(eff_target, "move", effective_resist, roll, resisted)
            if resisted:
                self.log.append(f"{eff_target.name} resists the move")
            else:
                # Direction, settled from the game's own data rather than
                # from the English words. Two skills prove it between them:
                #
                #   Daemon's Pull  .target 34  applies "Pull 2A"
                #   Rampart        .target 12  applies "Push 1A"
                #
                # A pull can only legally hit ranks 3-4, so it must move the
                # target FORWARD (toward rank 1); a push can only hit ranks
                # 1-2, so it must move it BACK. With the signs the other way
                # round each skill would be a no-op on half its legal targets.
                # This engine had push decreasing the rank index and pull
                # increasing it -- exactly inverted -- across 46 of 246
                # monsters and every hero skill that shoves or drags.
                rear = BOARD_RANKS - max(1, eff_target.size)
                if eff.push_distance and eff.push_distance > 0:
                    new_pos = eff_target.position + eff.push_distance
                    new_pos = max(0, min(new_pos, rear))
                    was = eff_target.position
                    self._shift_to_position(eff_target, new_pos)
                    self.log.append(
                        f"{eff_target.name} pushed from {was+1} to "
                        f"{eff_target.position+1}")

                if eff.pull_distance and eff.pull_distance > 0:
                    # Was clamped to rank 4 with no size awareness at all, so a
                    # pull could hang a size-2 monster's back half off the end
                    # of the board.
                    new_pos = eff_target.position - eff.pull_distance
                    new_pos = max(0, min(new_pos, rear))
                    was = eff_target.position
                    self._shift_to_position(eff_target, new_pos)
                    self.log.append(
                        f"{eff_target.name} pulled from {was+1} to "
                        f"{eff_target.position+1}")

        if eff.summon_monster_ids:
            # Every shipped summon effect carries `.apply_once true`, so an AoE
            # resolves it once per skill use rather than once per target -- see
            # _split_apply_once_effects, which already relies on that flag.
            self._summon_from_effect(eff)

        if eff.stress_damage is not None:
            self._apply_stress(eff_target, float(eff.stress_damage))

        if eff.stress_heal is not None:
            # stress_heal_percent (performer) / stress_heal_received_percent
            # (target) — same real-data-verified pattern as hp_heal_percent/
            # hp_heal_received_percent above, applied to relief instead of damage.
            healed = max(0, round(eff.stress_heal * performer.stress_heal_produced_mult
                                   * eff_target.stress_heal_received_mult))
            eff_target.stress = max(0, eff_target.stress - healed)
            self._maybe_clear_overstress(eff_target)

        if eff.heal_min is not None or eff.heal_percent is not None:
            # A named effect's own heal, previously never applied at all —
            # _resolve_effect had no heal handling whatsoever, only
            # _resolve_heal (a skill's bare .heal field) did. Real, high-
            # impact consequence: Flagellant's Redeem heals via "Flag Super
            # Heal"/"Flag HealSelf" (percent-of-max-HP, .heal_percent) with
            # no bare .heal on the skill itself — Redeem, "arguably
            # Flagellant's strongest skill," did nothing at all beyond its
            # self-debuff before this fix. Also fixes "Flag Last Gift" (his
            # on-death party heal, a flat .heal 10).
            if eff.heal_percent is not None:
                heal_amt = round(eff_target.max_hp * eff.heal_percent)
            else:
                heal_amt = self.rng.randint(eff.heal_min, eff.heal_max)
            # Single-target crit rate: _resolve_effect resolves one
            # eff_target per call even for a group-dispatched effect (the
            # per-member loop lives one level up, in the group-spec
            # dispatcher) -- confirmed narrow real-data scope (Flagellant/
            # DLC only) makes this an acceptable simplification rather than
            # plumbing group-size through to here.
            applied = self._apply_heal(eff_target, heal_amt, performer, crit_eligible=performer.is_hero)
            self.log.append(f"{performer.name} heals {eff_target.name} for {applied} ({eff.name})")

        if eff.heal_dot_amount is not None:
            # Heal-over-time / regeneration (Flagellant's Reclaim, "Flag
            # Healdot N") — same shape as bleed/blight DoTs, ticked via
            # _tick_heal_dots, but restoring HP instead of dealing damage.
            eff_target.heal_dots.append(DotInstance(
                effect_name=eff.name, damage_per_tick=eff.heal_dot_amount,
                remaining_ticks=eff.heal_dot_rounds or 3, chance=1.0,
            ))
            self.log.append(f"{eff_target.name} regenerating {eff.heal_dot_amount}/rd for {eff.heal_dot_rounds} rds")

        # Real bug found and fixed: this used to key off eff.key_status ==
        # "tagged" -- but key_status is a DIFFERENT, same-named-by-
        # coincidence mechanism (a conditional damage-bonus-vs-marked-target
        # check, see _compute_mark_damage_mult) that's always a "target"
        # (i.e. eff_target resolves to the PERFORMER for those effects, per
        # real data -- see EffectDefinition.is_mark_effect's comment). So
        # this line was flagging the ATTACKING hero as Marked every time
        # they landed a Dmg-Marked-synergy skill, while the real Mark-
        # applying effects ("Mark Target"/"Vulnerability Curse N"/"BH Mark
        # Target"/boss "X Mark" skills, real key .tag 1) never set is_marked
        # at all -- confirmed via grep: 0 references to is_mark_effect
        # anywhere before this fix, real Mark never actually applied.
        if eff.is_mark_effect and eff.target == "target":
            eff_target.is_marked = True
            eff_target.mark_rounds_remaining = eff.duration or 3
            self.log.append(f"{eff_target.name} marked!")

        if eff.remove_status == "tagged":
            # Dead against real data (verified: 0 real effect files anywhere
            # in the install use .removeStatus at all) -- kept for forward
            # compatibility, matches the pre-fix behavior for this field.
            eff_target.is_marked = False
            eff_target.mark_rounds_remaining = None
            self.log.append(f"{eff_target.name} mark removed")

        # --- 2026-08-23 audit. Five keys the shipped data carries on effects
        # this simulator FIELDS and that nothing read; see EffectDefinition for
        # the carrier counts. Ranked by what a real expedition equips, the same
        # method that put `stun_chance` at the top of the buff sweep.
        if eff.requires_kill_target and target.is_alive():
            # Gated on the skill having KILLED its primary target. Checked
            # AFTER the `.chance` roll on purpose, so wiring this cannot shift
            # the RNG stream of a skill that was already drawing. No shipped
            # effect pairs this key with any other payload -- Duelist's Coup de
            # Grace is the only carrier -- so its position here is not
            # observable, only its existence.
            return

        # TORCHLIGHT, mid-combat. `torch_level` was set once at reset() and
        # never moved again, while 9 hero skills raise it, 4 monsters drain it,
        # and 104 of the 141 comps in the pool equip one. Everything downstream
        # was already wired, so a Crusader's +24 was resolving the party's
        # light-gated buffs against a number that could not change.
        # No conditional-buff resync is forced: _sync_all_conditional_buffs
        # already re-evaluates every living combatant once per action, which is
        # the same schedule every other state change on this board settles on.
        if eff.torch_change:
            before = self.torch_level
            self.torch_level = max(0.0, min(100.0, self.torch_level + eff.torch_change))
            if self.torch_level != before:
                self.log.append(f"torch {before:.0f} -> {self.torch_level:.0f}")

        if eff.clears_mark and (eff_target.is_marked or
                                eff_target.mark_rounds_remaining is not None):
            eff_target.is_marked = False
            eff_target.mark_rounds_remaining = None
            self.log.append(f"{eff_target.name} is no longer marked")

        if eff.clears_stun and eff_target.is_stunned:
            eff_target.is_stunned = False
            self.log.append(f"{eff_target.name} is no longer stunned")
            # DECLARED, and the falsifier is cheap: no shipped key says whether
            # a FORCED unstun also grants the stun-resist buff that NATURAL
            # recovery grants. Granted here because _apply_stun_recovery is the
            # only stun-clearing path in this file and consistency beats a
            # second convention. To falsify: Flare a stunned ally in the real
            # game and re-stun them the same round -- if it lands at the base
            # rate, delete this call.
            self._apply_stun_recovery(eff_target)

        if eff.steal_dot_stat_type and eff_target is not performer:
            # Flagellant's Suffer. The whole point of the skill is pulling an
            # ally's DoTs onto himself (he profits from being hurt); without it
            # the skill was a mark-swap that left the bleed where it was.
            pool = {"hp_dot_bleed": "bleeds", "hp_dot_poison": "blights"}.get(
                eff.steal_dot_stat_type)
            taken = getattr(eff_target, pool) if pool else None
            if taken:
                getattr(performer, pool).extend(taken)
                self.log.append(f"{performer.name} takes {len(taken)} "
                                f"{pool[:-1]} from {eff_target.name}")
                taken.clear()

        if eff.grants_bonus_action:
            # Duelist's Coup de Grace. A kill does not end the turn, which is
            # the SAME mechanic as `.is_continue_turn`, so it reuses that path
            # in step() rather than inserting a second entry into the
            # initiative queue -- `.initiative_change 1` rides the same effect
            # and is subsumed by it. Bounded by the enemies left alive, since
            # the gate above needs a fresh kill each time.
            self._bonus_action_pending = True
            self.log.append(f"{performer.name} gains a bonus action")

        if eff.health_damage_blocks is not None:
            # Shieldbreaker's Serpent's Sway ("SB Aegis") -- real stacking
            # damage-negation charge, capped by rules.health_damage_
            # blocks_max. Consumed in engine/resolution.py's resolve_attack.
            eff_target.damage_blocks = min(
                int(self.rules.health_damage_blocks_max),
                eff_target.damage_blocks + eff.health_damage_blocks)
            self.log.append(f"{eff_target.name} gains an Aegis Block "
                             f"({eff_target.damage_blocks} total)")

        if eff.is_kill_effect:
            eff_target.is_dead = True
            eff_target.hp = 0
            self.log.append(f"{eff_target.name} killed by effect!")
            if eff_target.is_hero:
                self._collapse_hero_position_gap(eff_target.position)
                self._on_hero_died(eff_target)
            else:
                self._collapse_enemy_position_gaps()
                self._resolve_life_links(eff_target)

        if eff.is_stat_buff and eff.buff_stat_type and not resisted:
            # Inline stat fields (damage_low_multiply, attack_rating_add,
            # speed_add, ...) previously only got a decorative ActiveBuff
            # marker appended (buff_id f"EFFECT_{eff.name}") that never
            # resolved in store.buffs, so _apply_buff_to_stats was never
            # actually called for them — every inline combat_stat_buff
            # effect in the game (Abomination's beast_buff_N/xform_damage_N,
            # and any other skill using inline fields instead of .buff_ids)
            # was fully inert. Fixed by synthesizing a real BuffDefinition
            # per stat field (cached in store.buffs so repeat applications
            # reuse the same one) and applying it through the normal path.
            # eff_target already resolved once by the caller (_resolve_effect)
            # -- previously re-resolved independently here via a second
            # _get_effect_target call, harmless when it always agreed with
            # eff_target, but would have silently diverged from the group
            # loop in _resolve_effect for a group-targeted inline stat buff.
            applied = []
            for field_name, stat_type, stat_sub_type in INLINE_STAT_FIELD_MAP:
                amount = getattr(eff, field_name)
                if amount is None:
                    continue
                buff_id = f"__inline__{eff.name}__{stat_sub_type}"
                bd = self.store.buffs.get(buff_id)
                if bd is None:
                    bd = BuffDefinition(
                        id=buff_id, stat_type=stat_type, stat_sub_type=stat_sub_type,
                        amount=amount, duration=eff.duration, duration_type="rounds",
                        rule_type="always", is_false_rule=False,
                        remove_on_battle_complete=False, remove_if_not_active=False,
                    )
                    self.store.buffs[buff_id] = bd
                delta = self._apply_buff_to_stats(eff_target, bd)
                # Real bug found and fixed: buff_duration_type=="combat_end"
                # (e.g. Grave Robber's Toxin Trickery -- "GR Self Speed N"/
                # "Shadow Blood N", real .duration 1 but meant to last the
                # whole fight, confirmed by her own doc: "1 Use per Battle...
                # Buffs last for the whole battle") overrides the numeric
                # .duration entirely -- treated as permanent-for-combat
                # (remaining_rounds omitted), not a real 1-round countdown.
                if eff.duration is not None and eff.buff_duration_type != "combat_end":
                    eff_target.buffs.append(ActiveBuff(buff_id=buff_id, remaining_rounds=eff.duration,
                                                          applied_delta=delta))
                applied.append(stat_sub_type)
            if applied:
                self.log.append(f"{eff_target.name} buffed by {eff.name} ({', '.join(applied)})")

        if eff.inline_buff_type and not resisted:
            # Real .buff_type/.buff_amount pair (29 occurrences) -- a THIRD
            # inline-buff mechanism, distinct from both buff_ids and the
            # combat_stat_buff+specific-field pattern above. buff_type IS
            # the stat_type directly (verified real values:
            # hp_heal_received_percent -- e.g. Arbalest's "Arb Stacking
            # Heal N"; crit_received_chance -- e.g. Shieldbreaker's
            # "Expose Crit Debuff N", Arbalest/Musketeer's "Grapeshot
            # Vulnerability N"). Same synthesized-BuffDefinition approach
            # as the inline combat_stat_buff fields just above.
            buff_id = f"__buff_type__{eff.name}"
            bd = self.store.buffs.get(buff_id)
            if bd is None:
                bd = BuffDefinition(
                    id=buff_id, stat_type=eff.inline_buff_type, stat_sub_type="",
                    amount=eff.inline_buff_amount or 0.0, duration=eff.duration,
                    duration_type="rounds", rule_type="always", is_false_rule=False,
                    remove_on_battle_complete=False, remove_if_not_active=False,
                )
                self.store.buffs[buff_id] = bd
            delta = self._apply_buff_to_stats(eff_target, bd)
            if eff.duration is not None and eff.buff_duration_type != "combat_end":
                eff_target.buffs.append(ActiveBuff(buff_id=buff_id, remaining_rounds=eff.duration,
                                                      applied_delta=delta))
            self.log.append(f"{eff_target.name} gains {eff.inline_buff_type} from {eff.name}")

        if eff.set_mode:
            eff_target.mode = eff.set_mode
            self.log.append(f"{eff_target.name} transforms into {eff.set_mode} mode")

        if eff.is_clear_corpses and skill is not None and skill.target:
            # Real skills pairing this (Leper's Focus, Occultist's Daemon's
            # Pull, Plague Doctor's Disorienting Blast) all target the enemy
            # side and clear corpses within the skill's own target ranks —
            # not a separate "clear anywhere" sweep.
            pool = self.heroes if target.is_hero else self.enemies
            clear_positions = {p - 1 for p in skill.target}
            # A large monster's corpse blocks every rank it covered, so it
            # clears from any of them — same rule as targeting it alive.
            removed = [c for c in pool if c.is_corpse
                       and not clear_positions.isdisjoint(c.occupied_positions)]
            if removed:
                pool[:] = [c for c in pool if c not in removed]
                for c in removed:
                    self.log.append(f"{c.name}'s corpse is cleared")
                if pool is self.enemies:
                    # Clearing a corpse is only worth a turn because the
                    # rank it held opens up and the monsters behind it
                    # advance through. Before _collapse_enemy_position_gaps
                    # existed, corpse-clearing changed targeting for nobody.
                    self._collapse_enemy_position_gaps()

        if eff.cures_dots:
            eff_target.bleeds.clear()
            eff_target.blights.clear()
            self.log.append(f"{eff_target.name} cured of bleed/blight")

        if eff.is_guard:
            guardian, guarded = (eff_target, performer) if eff.swap_source_and_target else (performer, eff_target)
            guardian.guard_target_id = guarded.id
            guarded.guard_source_id = guardian.id
            # Real Guard effects carry a .duration (2-3 rounds) — verified
            # (MAA Guard 1=3, Antiq ProtectMe Guard=2, HM Guard 1=2).
            # Previously untracked entirely (persisted until explicitly
            # cleared by another effect); now ticked in _tick_guard_durations
            # the same way riposte/stealth durations already were.
            guardian.guard_rounds_remaining = eff.duration
            self.log.append(f"{guardian.name} guards {guarded.name}")

        if eff.is_clear_guarding:
            self._break_guard_if_guarding(eff_target)

        if eff.is_clear_guarded:
            prev = self._find_combatant(eff_target.guard_source_id) if eff_target.guard_source_id is not None else None
            if prev:
                prev.guard_target_id = None
                prev.guard_rounds_remaining = None
            eff_target.guard_source_id = None

        if eff.is_riposte:
            eff_target.active_riposte = RiposteState(
                remaining_rounds=eff.riposte_duration or 2,
                dmg_mod=eff.damage_low_multiply or 0.0,
                crit_mod=eff.crit_chance_add or 0.0,
                on_hit_chance=eff.riposte_on_hit_chance_add or 0.0,
                on_miss_chance=eff.riposte_on_miss_chance_add or 0.0,
                effect_name=eff.riposte_effect,
            )
            self.log.append(f"{eff_target.name} readies a riposte")

        if eff.is_clear_riposte:
            eff_target.active_riposte = None

        if eff.stealth_duration is not None:
            eff_target.is_stealthed = True
            eff_target.stealth_rounds_remaining = eff.stealth_duration
            self.log.append(f"{eff_target.name} is stealthed")

        if eff.is_unstealth:
            eff_target.is_stealthed = False
            eff_target.stealth_rounds_remaining = None

        for buff_id in (() if resisted else eff.buff_ids):
            bd = self.store.buffs.get(buff_id)
            if not bd:
                continue
            delta = self._apply_buff_to_stats(eff_target, bd)
            # Fall back to the buff definition's own duration when the
            # effect line doesn't specify one — real bug found verifying
            # on-crit passives: on_crit_prot/on_crit_acc/on_crit_def/
            # on_crit_speed's effect records have no .duration even though
            # their buffs do (e.g. on_crit_prot's buff has duration=2),
            # which meant this ActiveBuff tracking never got created and
            # the stat change applied permanently instead of expiring.
            effective_duration = eff.duration if eff.duration is not None else bd.duration
            # buff_duration_type=="combat_end" (e.g. Leper's "Leper
            # Vulnerability"/"Bolster Stress Buff N") overrides any numeric
            # duration -- permanent for the rest of combat, same as the
            # inline-stat-field branch above.
            if eff.buff_duration_type == "combat_end":
                effective_duration = None
            if effective_duration is not None:
                eff_target.buffs.append(ActiveBuff(buff_id=buff_id, remaining_rounds=effective_duration,
                                                     applied_delta=delta))
            self.log.append(f"{eff_target.name} gains buff {buff_id} from {eff.name}")

    def _get_effect_target(self, primary_target: CombatantState, performer: CombatantState,
                            target_spec: str) -> CombatantState:
        # "performer_group_other"/"performer_group" are handled one level up,
        # in _resolve_effect's dispatcher (they resolve to a whole list of
        # combatants, not one) -- never reach here.
        if target_spec == "target":
            return primary_target
        if target_spec == "performer":
            return performer
        if target_spec == "global":
            # The board itself -- the 24 shipped `global` effects are all
            # torchlight. Routed to the performer so the `.chance` roll and
            # the resist lookup have a live combatant to read, never so the
            # payload lands on them: `torch_change` does not touch eff_target.
            # 23 of the 24 also carry `.apply_once`; the odd one out is why
            # "global" was added to _split_group_effect_names' once list
            # rather than relying on that flag.
            return performer
        return primary_target

    def _apply_movement(self, performer: CombatantState, target: CombatantState,
                          skill: SkillLevelData):
        if skill.move_self != 0:
            new_pos = performer.position + skill.move_self
            # Rear-most rank a combatant of this size can stand on and still
            # fit the 4-rank board. Was written out for size 2 with everything
            # else falling through to 3, so a size-3 monster could park its
            # front rank at 3 and hang two ranks off the back of the board.
            # Rare (8 size-3 and 2 size-4 monsters, and only those with a
            # move_self skill) but wrong, and the same assumption that made
            # large monsters answer to only one rank.
            new_pos = max(0, min(new_pos, BOARD_RANKS - max(1, performer.size)))
            if new_pos != performer.position:
                # Displaces the allies it passes, exactly as a push or pull
                # does -- a Grave Robber lunging from rank 4 to rank 2 pushes
                # the heroes who were on 2 and 3 back one each, rather than
                # landing on top of one of them.
                was = performer.position
                self._shift_to_position(performer, new_pos)
                self.log.append(
                    f"{performer.name} moves from pos {was+1} "
                    f"to {performer.position+1}")
