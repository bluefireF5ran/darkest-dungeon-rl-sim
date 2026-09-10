"""Read the game's OWN action bar: which slots is it willing to accept?

**THE SAVE DOES NOT RECORD THE IN-COMBAT FORMATION, and this module exists
because of that.** `persist.raid.json`'s `party.heroes` is the party's marching
order; a position change *inside* a fight -- a Move, an enemy push or pull, an
afflicted hero's `change_pos` act-out -- is **not written to it**. Proved live
on 2026-09-05: the file read `[37, 15]` before a swap, the swap was confirmed
by that same file (the mover's `turns_taken` advanced and an enemy died), and
it still read `[37, 15]` afterwards.

So the bridge's rank model is right at the start of a fight and can go stale at
any point during it, silently. What that costs is total: the advisor picks a
skill legal from the rank it *believes*, `run.py::advised_button` turns it into
a slot digit, the game refuses the digit because that skill is greyed out for
the rank the hero is *actually* standing in, the click then lands on an enemy
with nothing selected, and the save never moves. Every poll re-derives the
identical plan. A live Crypts raid froze that way for six minutes with a Plague
Doctor at rank 1 and all four of her skills unusable.

**The bar is the answer because it is the game's own statement**, and it needs
no model at all. Darkest Dungeon draws an unusable skill through a grayscale
shader, so the discriminator is not a threshold anybody had to choose:

    slot 1 pierce       .launch 3,2,1   LIT     sat 0.583   p95 187
    slot 2 adders_kiss  .launch 1       LIT     sat 0.162   p95 136
    slot 3 expose       .launch 3,2,1   LIT     sat 0.351   p95 139
    slot 4 single_out   .launch 3,2     GREY    sat 0.000   p95  56
    slot 5 MOVE                         LIT     sat 0.125   p95 120
    slot 6 PASS (the X)                 LIT     sat 0.302   p95 112

A greyed icon is desaturated to **exactly zero**, measured on a Shieldbreaker
standing at rank 1 -- and the four predictions from `.launch` match the four
drawn icons, which is the check that this reads legality and not brightness.
The same hero's Plague Doctor partner, at rank 1 with a kit that launches only
from 2, 3 and 4, drew **four** zeros.

Two independent signals with large margins, and they agreed on every slot of
every frame measured, so `lit` takes EITHER: saturation clears zero, or the
icon is simply bright. Requiring both would make a monochrome modded icon read
as unusable; requiring only saturation would trust one channel of a game that
renders in browns.

**When no hero is being asked to choose, the WHOLE bar is grey** -- MOVE and
the X included, at p95 48 and 49. That is what `waiting` reads, and it is a
better answer than the save can give: `enemies_first` is derived from the turn
queue and is a prediction, while this is the game saying it has stopped
animating and wants an input.

This module reads pixels and decides nothing. `bridge/act.py` owns what to do
about a greyed slot.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional, Sequence

from bridge.overlay_slots import MOVE_SLOT, PASS_SLOT

#: Slots on the bar: the four equipped skills, MOVE, and the narrow X.
BAR_SLOTS = PASS_SLOT

#: Fraction of `layout.skill_half` to sample around each slot centre. The icon
#: is inset in its frame and the frame has a lit border of its own, so a full
#: half-width box reads the CHROME rather than the art -- and the chrome is
#: bright on a greyed slot too.
BOX_FRAC = 0.62

#: A greyed icon measures exactly 0.000; the dimmest lit one measured 0.125,
#: and the dimmest lit SKILL 0.162. The cut sits an order of magnitude below
#: the signal and above a floating-point zero.
LIT_SAT = 0.03

#: Greyed icons top out at p95 56 and the "nobody is choosing" bar at 49; the
#: dimmest lit slot reads 112. Between them, and nowhere near either.
LIT_BRIGHT = 90.0


@dataclass(frozen=True)
class ActionBar:
    """What the game is drawing on the action bar, per 1-based slot."""

    lit: tuple[bool, ...]
    sat: tuple[float, ...]
    bright: tuple[float, ...]

    @property
    def waiting(self) -> bool:
        """Is a hero being asked to choose an action?

        Keyed on MOVE and PASS rather than on the skills, deliberately: a hero
        can be asked to choose with every SKILL greyed -- that is the whole
        situation this module exists for -- but the game always offers those
        two, so a bar with neither lit is a bar nobody is being shown.
        """
        return self.slot_lit(MOVE_SLOT) or self.slot_lit(PASS_SLOT)

    @property
    def any_skill(self) -> bool:
        """Can this hero perform ANY of its four skills from where it stands?"""
        return any(self.slot_lit(s) for s in range(1, MOVE_SLOT))

    def slot_lit(self, slot: int) -> bool:
        return 1 <= slot <= len(self.lit) and self.lit[slot - 1]

    @property
    def skill_pattern(self) -> tuple[bool, ...]:
        """The four skill slots alone -- the signature `rank_from_pattern` reads."""
        return tuple(self.slot_lit(s) for s in range(1, MOVE_SLOT))

    def __str__(self) -> str:
        return "".join("#" if self.slot_lit(s) else "."
                       for s in range(1, len(self.lit) + 1))


def read(img, resolved) -> Optional[ActionBar]:
    """Read the bar off one frame, or None when it cannot be read.

    None is a real answer and every caller must treat it as "no opinion", not
    as "nothing is legal": an uncalibrated layout, a numpy that will not
    import and a frame of the wrong shape all land here, and none of them is
    evidence about the game. A one-sided reader that refuses when it cannot
    see is the `_panel_is_open` rule, and this is the same rule.
    """
    try:
        import numpy as np                                    # noqa: PLC0415
    except Exception:                                         # noqa: BLE001
        return None
    if img is None or resolved is None:
        return None
    frac = getattr(getattr(resolved, "frac", None), "skills", ())
    half = float(getattr(getattr(resolved, "frac", None), "skill_half", 0.0) or 0.0)
    if not frac or half <= 0:
        return None
    arr = np.asarray(img)
    if arr.ndim != 3 or arr.shape[2] < 3:
        return None
    h, w = arr.shape[:2]
    box = max(2, int(half * w * BOX_FRAC))

    lit, sats, brights = [], [], []
    for fx, fy in frac:
        cx, cy = int(fx * w), int(fy * h)
        y0, y1 = max(0, cy - box), min(h, cy + box)
        x0, x1 = max(0, cx - box), min(w, cx + box)
        if y1 - y0 < 2 or x1 - x0 < 2:
            return None
        patch = arr[y0:y1, x0:x1, :3].astype("float32")
        hi = patch.max(axis=2)
        lo = patch.min(axis=2)
        sat = float(((hi - lo) / np.maximum(hi, 1.0)).mean())
        bright = float(np.percentile(patch.mean(axis=2), 95))
        sats.append(sat)
        brights.append(bright)
        lit.append(sat >= LIT_SAT or bright >= LIT_BRIGHT)
    return ActionBar(tuple(lit), tuple(sats), tuple(brights))


def predicted_pattern(launches: Sequence[Optional[Iterable[int]]],
                      rank: int) -> tuple[bool, ...]:
    """Which of four skills a hero could launch from `rank` (1-based).

    A slot whose launch ranks are unknown -- an unparsed skill, a modded one
    the store never loaded -- predicts **True**, so it can never be the reason
    a rank is ruled out. Guessing "unusable" there would let an unreadable
    skill masquerade as evidence about where the hero is standing.
    """
    out = []
    for lp in launches:
        out.append(True if lp is None else rank in set(lp))
    return tuple(out)


def rank_from_pattern(launches: Sequence[Optional[Iterable[int]]],
                      pattern: Sequence[bool],
                      party_size: int) -> Optional[int]:
    """Where is this hero really standing? 1-based rank, or None.

    The lit/greyed pattern of four skills is a 4-bit signature and `.launch`
    predicts it exactly, so a rank that disagrees with the screen is refuted.
    Returns a rank only when exactly ONE survives -- a hero whose kit launches
    from everywhere lights all four at every rank, and the honest answer there
    is "cannot tell", which costs nothing because such a hero can act anyway.

    This is the only way back to the truth once a fight reorders the party,
    because the save keeps the marching order and never updates it.
    """
    want = tuple(bool(b) for b in pattern)
    hits = [r for r in range(1, max(1, int(party_size)) + 1)
            if predicted_pattern(launches, r) == want]
    return hits[0] if len(hits) == 1 else None
