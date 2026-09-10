"""Damage over time: bleed, blight, burn, stress DoTs and heal DoTs.

Split out of `combat_engine.py` on 2026-09-08, verbatim. Two blocks that
belong together and sat 3,400 lines apart: the burn ZONES near the top of the
class, and the end-of-turn TICKS near the bottom.

**Burn is a real DoT and copies neither of the other two.** Bleed and blight
tick a flat amount per round; burn carries a stack that decays by
`BURN_BASE_DECAY` and repaints the tile behind a moving actor, which is why
`_copy_burn_behind` exists and why burn needs a zone at all.

**`burn_zones` is one of the eight attributes `reset()` creates and
`__init__` does not.** A `CombatEngine` that has never been reset raises
`AttributeError` here rather than reading an empty list, and that is true of
this state whether it lives in this file or the other one -- the split did not
introduce it and does not hide it.

The ticks call outward freely (`_apply_heal`, `_apply_stress`,
`_check_deaths_door_dot`, `_check_win_loss`, `_reverse_buff_from_stats`).
That is what a mixin is for: attribute lookup still resolves on the instance,
so a DoT killing a hero still runs the whole death path.
"""

from __future__ import annotations
from data.types import SkillLevelData
from .combatant import BURN_BASE_DECAY, CombatantState, burn_next_stack


class DotMixin:
    def paint_burn_zone(self, rank: int, amount: int, duration: int) -> None:
        """Set a rank alight for `duration` rounds."""
        if amount <= 0 or duration <= 0:
            return
        self.burn_zones[int(rank)] = [int(amount), int(duration)]
        self.log.append(f"Rank {rank + 1} is set alight ({amount} burn, "
                        f"{duration} rounds)")

    def _tick_burn_zones(self) -> None:
        """Burn whoever is standing in a painted rank, then age the zone.

        Resolved at END OF ROUND against the board as it stands THEN, not when
        the zone was painted -- that ordering is the entire mechanic. A monster
        pulled into the fire this round burns this round; one shoved out of it
        does not.

        Applied directly rather than through `_apply_effect_to_target`, for the
        same reason `_copy_burn_behind` is: burn is irresistible by NOT DRAWING,
        and routing a zone tick through the effect path would consume RNG draws
        and desynchronise every trace comparison after it.
        """
        if not self.burn_zones:
            return
        for rank in sorted(self.burn_zones):
            amount, rounds_left = self.burn_zones[rank]
            victim = self._find_combatant_by_position(rank, enemy=True)
            if victim is not None and victim.is_alive():
                victim.burn_stacks += amount
                self.log.append(
                    f"{victim.name} stands in the fire (+{amount} burn "
                    f"=> {victim.burn_stacks})")
            self.burn_zones[rank][1] = rounds_left - 1
        # Expire after the tick, so a zone painted with duration 1 still burns
        # once. Rebuilt rather than mutated: deleting from a dict being walked
        # is the classic way to lose an entry.
        self.burn_zones = {r: v for r, v in self.burn_zones.items() if v[1] > 0}

    def _copy_burn_behind(self, target: CombatantState,
                          sd: SkillLevelData) -> None:
        """`.copy_burn_behind` -- spread the target's burn to the rank behind.

        Backdraft carries it and nothing else does. This is the half Fran
        described as the class's power: *"backdraft makes the burn damage from
        an enemy hit 2 enemies, essentially doubling burn damage, specially
        potent with 2"*.

        BEHIND means further from the party, i.e. the next rank up. A large
        monster answers to every rank it occupies, so the search starts past
        its BACK rank -- otherwise a size-2 target would copy the burn onto
        itself. Applied directly rather than through `_apply_effect_to_target`
        because burn is irresistible by NOT DRAWING (`.dot_irresistible`), and
        routing it through the effect path would move the RNG stream.
        """
        if not sd.copy_burn_behind or not target.burn_stacks:
            return
        occupied = getattr(target, "occupied_positions", None) or [target.position]
        behind = max(occupied) + 1
        other = self._find_combatant_by_position(behind, enemy=not target.is_hero)
        if other is None or not other.is_alive() or other is target:
            return
        # Additive, the way `_apply_effect_to_target` stacks burn -- a second
        # application adds rather than queueing an independent instance.
        other.burn_stacks += target.burn_stacks
        self.log.append(
            f"The fire spreads from {target.name} to {other.name} "
            f"({target.burn_stacks} burn)")

    def _is_burning_at(self, position: int) -> bool:
        """Is a LIVING combatant standing in this rank and burning?

        A corpse cannot burn in any sense that matters -- it never takes a turn,
        so burn never ticks on it -- and `requires_burning` is about a target
        whose burn Backdraft can amplify and spread.
        """
        target = self._find_combatant_by_position(position, enemy=True)
        return bool(target and target.is_alive() and target.burn_stacks > 0)

    def _tick_dots(self, combatant: CombatantState):
        # Resistance (bleed_resist/blight_resist) only gates whether the DoT
        # gets INFLICTED in the first place (the roll in _resolve_effect) —
        # once bleeding/blighted, the real game deals full damage every
        # round of the duration, no further resist check
        # (darkestdungeon.wiki.gg/wiki/Bleed_(Darkest_Dungeon)/Blight_
        # (Darkest_Dungeon)). This used to re-roll resistance on every tick
        # too, silently under-dealing DoT damage project-wide by a factor
        # related to the target's own resist stat.
        for dot_list in (combatant.bleeds, combatant.blights):
            remaining = []
            for dot in dot_list:
                combatant.hp -= dot.damage_per_tick
                self.log.append(f"{combatant.name} takes {dot.damage_per_tick} from {dot.effect_name}")
                dot.remaining_ticks -= 1
                if dot.remaining_ticks > 0:
                    remaining.append(dot)
            dot_list.clear()
            dot_list.extend(remaining)

    def _tick_burn(self, combatant: CombatantState):
        """Burn ticks at the END of the burning actor's turn.

        That is the one structural difference from bleed and blight, which
        tick at the START (see _advance_to_next_hero_turn, whose comment
        explains why that ordering is tactically load-bearing). Burn is the
        other way round: a burning enemy ALWAYS gets to act, even when the
        burn is lethal -- so "it is already dead, spend the turn elsewhere"
        does not buy the free turn against burn that it buys against blight.

        The stack then decays. It is a stack count, not a tick budget, so
        there is nothing to decrement and no expiry to check.
        """
        if combatant.burn_stacks <= 0:
            return
        damage = combatant.burn_stacks
        combatant.hp -= damage
        self.log.append(f"{combatant.name} takes {damage} from Burn")
        combatant.burn_stacks = burn_next_stack(
            combatant.burn_stacks, BURN_BASE_DECAY + combatant.burn_decay_bonus)
        self.vlog(f"    [BURN] {combatant.name} takes {damage}, "
                  f"{combatant.burn_stacks} left")

    def _end_of_turn_burn(self, combatant: CombatantState):
        """The burn tick plus the same bookkeeping the start-of-turn DoT tick
        does, so a burn kill and a bleed kill leave the board in the same
        state (Death's Door, win/loss)."""
        if combatant.burn_stacks <= 0:
            return
        self._tick_burn(combatant)
        self._check_deaths_door_dot(combatant)
        self._check_win_loss()

    def _tick_stress_dots(self, combatant: CombatantState):
        """Horror — routed through _apply_stress (not a direct .stress
        mutation) so stress_dmg_received_mult, affliction/virtue triggers,
        and heart attacks all fire correctly from a Horror tick exactly as
        they would from any other stress source."""
        remaining = []
        for dot in combatant.stress_dots:
            self.log.append(f"{combatant.name} takes {dot.damage_per_tick} stress from {dot.effect_name}")
            self._apply_stress(combatant, float(dot.damage_per_tick))
            dot.remaining_ticks -= 1
            if dot.remaining_ticks > 0:
                remaining.append(dot)
        combatant.stress_dots.clear()
        combatant.stress_dots.extend(remaining)

    def _tick_heal_dots(self, combatant: CombatantState):
        """Reclaim's real heal mechanism ("Flag Healdot N", the real
        .dotHpHeal field) — a heal-over-time/regeneration effect, distinct
        from bleed/blight DoTs in that it restores HP each tick. Routed
        through _apply_heal (not a direct .hp mutation) so max_hp clamping
        fires correctly, same as any other heal. check_recovery=False: this
        runs before that turn's bleed/blight tick (see _advance_to_next_
        hero_turn) — the caller checks Death's Door recovery once, after
        every HP-changing tick that turn has applied, not mid-sequence.

        crit_eligible=combatant.is_hero, not tied to a `performer` arg:
        DotInstance has no caster reference (heal_dots is confirmed
        Flagellant-only real data, always self-inflicted via Reclaim), so
        there's no performer to pass for produced_mult purposes -- but the
        wiki is explicit that "Restoration HoT can crit on each tick," and
        the receiving hero IS the caster for this specific real skill."""
        remaining = []
        for dot in combatant.heal_dots:
            applied = self._apply_heal(combatant, dot.damage_per_tick, check_recovery=False,
                                        crit_eligible=combatant.is_hero)
            self.log.append(f"{combatant.name} regenerates {applied} from {dot.effect_name}")
            dot.remaining_ticks -= 1
            if dot.remaining_ticks > 0:
                remaining.append(dot)
        combatant.heal_dots.clear()
        combatant.heal_dots.extend(remaining)

    def _tick_buff_durations(self):
        for c in self.heroes + self.enemies:
            remaining = []
            for buff in c.buffs:
                if buff.remaining_rounds is not None:
                    buff.remaining_rounds -= 1
                    if buff.remaining_rounds <= 0:
                        # Generic reversal for any real buff id (previously
                        # only handled resistance/stun specifically — any
                        # other duration-tracked buff, e.g. from generic
                        # .buff_ids resolution, was never actually undone).
                        # buff_id may be a synthetic "EFFECT_<name>" marker
                        # from the is_stat_buff bookkeeping in
                        # _resolve_effect, which has no store.buffs entry —
                        # store.buffs.get() returns None for those, so
                        # nothing to reverse, matches prior behavior.
                        bd = self.store.buffs.get(buff.buff_id)
                        if bd:
                            self._reverse_buff_from_stats(c, bd, applied_delta=buff.applied_delta)
                        continue
                remaining.append(buff)
            c.buffs = remaining
