"""Who stands where, and what happens to the gap when someone dies.

Split out of `combat_engine.py` on 2026-09-08, verbatim. Four methods that
call nothing outside themselves and touch three attributes -- the most
self-contained block in the class.

**Formations close ranks, and a gap only ever sits at the back.** Heroes via
`_collapse_hero_position_gap`, monsters via `_collapse_enemy_position_gaps`,
which is called from all five formation-changing sites: slain by an attack,
slain by a DoT, `is_kill_effect`, corpse rot, corpse cleared.

Enemies had none of this until 2026-08-08. Their formation was frozen for the
whole fight, which left a rank-4 survivor permanently untargetable by any hero
capping at rank 3 -- the Abomination in every form -- and was the real cause
of the "Stun Control" stall-outs. Do not reintroduce "enemies deliberately do
not advance"; `docs/CHANGELOG.md` carries the evidence.

A corpse is an OCCUPANT of the monster formation, which is what makes it
block, so it slides forward too rather than leaving a hole at the front.
"""

from __future__ import annotations
from .combatant import CombatantState
from ._constants import BOARD_RANKS


class FormationMixin:
    def _formation(self, is_hero: bool) -> list:
        """Everyone who OCCUPIES a rank on one side, front to back.

        Same rule `_collapse_enemy_position_gaps` uses: a corpse is an occupant
        (that is what makes it block), a dead hero is not (heroes leave no
        corpse).
        """
        side = self.heroes if is_hero else self.enemies
        return sorted((c for c in side
                       if c.is_alive() or (not is_hero and c.is_corpse)),
                      key=lambda c: c.position)

    def _shift_to_position(self, mover: CombatantState, new_pos: int) -> None:
        """Move `mover` toward `new_pos` by ROTATING it through the occupied
        ranks, sliding everyone it passes the other way.

        Fran's statement of the rule: *"if you pull the position 4 to position
        2, former-position 2 will go into 3 and former-position 3 will go into
        4, it never leaves a gap nor has 2 enemies on the same position"* —
        and, correcting a first attempt at this, *"a single enemy can't be
        pushed as there isn't enough positions to rotate, a displacement always
        keeps the gaps"*.

        So this is a **permutation of the occupants over the ranks they already
        hold**, not an arithmetic slide. Two consequences fall straight out of
        that and both are load-bearing:

        * the SET of occupied ranks never changes, so a displacement can
          neither open a hole nor close one — a formation holding a gap at
          rank 3 still holds it afterwards;
        * a lone combatant cannot be moved at all, because there is nothing to
          rotate with. Pushing the last living monster does nothing, which is
          the correct answer rather than an edge case to paper over.

        Before this, push/pull and skill self-movement wrote an ABSOLUTE
        position and displaced nobody, so a 2-rank push on rank 3 of a full
        party gave `[0, 0, 1, 3]` — two combatants stacked on one rank and a
        hole behind them.

        Gaps created by a DEATH are a different mechanic and stay where they
        are: `_collapse_enemy_position_gaps` closes those, front-first, at the
        moments it already runs. Doing it here as well would smuggle a second
        rule into every shove.

        Size > 1 is only approximated: the slots come from the board as it
        stands, so reordering occupants of different sizes can leave a large
        monster overlapping. That is narrower than the bug being fixed and is
        left alone rather than half-solved.
        """
        if new_pos == mover.position:
            return
        occupants = self._formation(mover.is_hero)
        if len(occupants) < 2:
            return
        slots = [c.position for c in occupants]
        others = [c for c in occupants if c is not mover]
        if new_pos > mover.position:
            idx = sum(1 for c in others if c.position <= new_pos)
        else:
            idx = sum(1 for c in others if c.position < new_pos)
        ordered = others[:idx] + [mover] + others[idx:]
        # Refuse a rotation that would hang a large monster off the back. The
        # slots come from the board as it stands, so swapping a size-3 with a
        # size-1 behind it would put the big one where it does not fit. Nothing
        # moving is the right answer there -- the real game has no room for the
        # swap either.
        if any(pos + max(1, c.size) > BOARD_RANKS
               for c, pos in zip(ordered, slots)):
            return
        for c, pos in zip(ordered, slots):
            c.position = pos

    def _collapse_hero_position_gap(self, dead_position: int):
        """Heroes never leave a corpse (unlike enemies, is_corpse/size-
        blocking) — real game rule: everyone behind a dead hero
        immediately shifts one rank toward the front to close the gap.
        Enemies get the equivalent in _collapse_enemy_position_gaps; a
        corpse participates in that formation as an occupant, which is
        what makes it block."""
        for h in self.heroes:
            if h.is_alive() and h.position > dead_position:
                h.position -= 1

    def _collapse_enemy_position_gaps(self):
        """Monsters (and their corpses) slide forward into unblocked ranks.

        Real rule, user-confirmed: monsters advance into an empty rank
        ahead of them, so the ONLY gaps a monster formation can hold are
        at the BACK. A corpse occupies a rank exactly like a living unit
        — that is precisely what stops the units behind it advancing, and
        why a DoT or crit kill (which leaves no corpse) "opens the lane"
        that a normal kill does not. Because a corpse is an occupant
        rather than a hole, it slides forward too when a rank ahead of it
        frees up; otherwise a cleared front rank would leave a permanent
        front gap, which the real game never shows.

        **This was missing entirely.** The only writes to a non-hero
        `.position` anywhere in the engine were the push/pull effects, so
        an enemy formation stayed frozen for the whole fight: verified by
        probe, killing the rank-1 monster with no corpse left ranks 2/3/4
        occupied and rank 1 permanently empty. The old
        _collapse_hero_position_gap docstring asserted enemies were
        excluded "deliberately", which conflated "a corpse blocks its
        rank" with "nothing ever advances".

        Consequence measured before the fix: no hero skill reaches enemy
        rank 4 from every position, and the Abomination cannot reach rank
        4 in ANY form (human manacles/vomit target ranks 1-3, beast
        rake/rage/slam target 1-3). A surviving back-rank monster was
        therefore permanently untargetable by him, leaving Absolution — a
        self-heal that is free, unlimited and is_stall_invalidating False
        — as his only legal action. That was 57% of every env step in
        "Stun Control: Beast" (228 env steps per combat against 18.6 for
        the best comp) and a 70% stall-out rate on the long band. The
        game's own stall penalty never fires on this because it requires
        alive_enemies <= 2, and the stalemate holds with 3-4 alive.

        Relative order is preserved and nothing ever moves backwards.
        Size-2 monsters consume two slots, matching reset()'s cursor.
        """
        board = sorted((e for e in self.enemies if e.is_alive() or e.is_corpse),
                       key=lambda e: e.position)
        cursor = 0
        for enemy in board:
            if enemy.position != cursor:
                what = "corpse" if enemy.is_corpse else "advances"
                self.log.append(
                    f"{enemy.name} {what} moves from rank {enemy.position + 1} "
                    f"to rank {cursor + 1}")
            enemy.position = cursor
            cursor += max(1, enemy.size)
