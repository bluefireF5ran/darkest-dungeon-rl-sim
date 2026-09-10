"""Where to click on the curio panel, found on screen rather than assumed.

    python bridge/curiopanel.py <frame.png>

## Why this exists

`W` at a curio was never broken. It **opens the curio panel** -- and nothing
clicked anything in it. The bridge pressed `W`, sent SPACE, saw the curio still
`active` and the pack unchanged, called it "did not respond", stepped forward
and pressed `W` again, up to six times, into a panel that was already open.
Four sessions and five falsified theories about where the party was standing,
and the party was standing in the right place every time.

The panel offers three kinds of thing, and the bridge has to pick one:

* the **hand** -- interact bare-handed, take whatever the curio gives
* an **item slot** -- use a provision on it first (`bridge/curios.py` already
  decides *whether* that is worth it, and which provision)
* the **red X** -- walk away

## Why the red X is the anchor

`layout.py` measured the panel's X at (1950, 618) of 2560x1440 and deliberately
refused to store it, because *"that panel flows around a description of whatever
length the curio has. Its x is right-anchored and stable; its y is not, and
inventing one would put a click on the description text."*

That is exactly right, and it is why this module finds the X instead of
assuming it. The X is the only **saturated red** thing inside the panel, which
is the same signature `LOOT_CLOSE` was found by. Locate it, and the rest of the
row follows: the buttons sit on one line at a fixed pitch, so every other slot
is an integer number of pitches away at the same height, whatever the
description did to the y.

## The row is a GRID, and the pitch is measured twice

Two live frames, two curios, two window sizes, and the pitch agrees to four
decimal places as a fraction of the client width:

    obstacle (Shipwreck, 1366x768)  X 1040  item 956  hand 882   pitch 79.0
    Traveler's Tent   (1280x720)    X  974  item  --  hand 826   pitch 74.0

    79.0 / 1366 = 0.05783        74.0 / 1280 = 0.05781

That is `SLOT_PITCH_FRAC`. The historical `HAND_FROM_X` (-0.11731) is exactly
two of them and is kept as a cross-check rather than as the geometry -- one
constant is better than two that have to agree.

## The ITEM SLOT IS ALWAYS DRAWN, which is why the hand sits at a fixed 2

The Shipwreck panel draws hand + shovel + X. The Tent draws hand + a **dimmed**
slot + X -- and `travellers_tent` has **zero** item interactions in the curio
library, so that slot can hold nothing at all and the game reserves it anyway.
The row is three wide whatever the curio is, and the hand is two pitches from
the X on both frames.

So `HAND_FROM_X` was right all along. What it lacked was any way to *know* it
was right on the frame in front of it -- and the curio library says eight of
sixty curios offer TWO usable provisions, which is the one shape that could
move the hand and which no capture here contains.

## The hand is CHECKED by its plate, and that is the panel-open test

The hand sits on a warm gold plate and no other slot does:

    slot                  warm-and-lit fraction    Shipwreck    Tent
    -1  item                                           0.004   0.000
    -2  hand                                           0.115   0.114
    -3  empty                                          0.000   0.004

0.115 against 0.004, and 0.115 against 0.114 across two curios at two window
sizes. A gold plate is a fixed sprite, so its warm-lit fraction is a property
of the sprite rather than of the scene -- which is what makes a narrow BAND the
right shape of test instead of a floor.

**And it is the panel-open test the module never had.** Over the 302 frames in
this repo the red-X rule alone fires on **62** -- town screens, the estate map,
the pause menu, the quest-complete page. Requiring the plate at slot -2 leaves
**exactly the two real panels**; the nearest miss reads 0.130, the nearest from
below 0.067.

A row wider than three would put an item icon at slot -2, fail the band and
report `hand=None`. That is the intended answer for the two-provision curios:
refuse and walk away, rather than spend the wrong provision.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# One slot to the next, as a fraction of the client width. Measured on two
# frames at two resolutions (see the module docstring); they agree to 1 part in
# 4000, which is far tighter than anything fitted here usually manages.
SLOT_PITCH_FRAC = 0.05782

# The hand relative to the red X, as fractions of the client area. Superseded
# as the geometry by `SLOT_PITCH_FRAC` -- it is exactly two pitches -- and kept
# because it is what live runs clicked successfully for weeks.
# `tests/bridge/test_curiopanel.py` pins that the two still agree inside one button.
HAND_FROM_X = (-0.11731, -0.00132)

# Saturated red: strong R, weak G and B. Same test `LOOT_CLOSE` was found by.
RED_MIN, RED_OVER = 110, 60

# The X is a small square button. The torch bar and the party's HP bars are
# also red, far larger and far more diffuse, and they sit in the same half of
# the screen -- so size is what separates the button from the furniture, not
# colour. Bounds as fractions of width, either side of the ~0.022 the real
# button measures.
#
# **THE FLOOR WAS 0.008 AND THAT WAS THREE TIMES TOO GENEROUS.** The X is
# drawn at a constant fraction -- 28 px of 1280 and 30 px of 1366, both 0.022
# -- while the two false positives banked from live play measure 11 px
# (0.0081) and 14 px (0.0102). The old floor admitted both, and the first of
# them reported "something red on screen after W" on every curio press of a
# raid.
#
# **THE 11 px ONE IS A HERO'S HP BAR AT DEATH'S DOOR** -- Fran named it off
# the scene ("the red thing is the man at arms that blinks red because it's at
# deaths door"), and the crop agrees: a solid red bar with the stress pips
# under it, at the feet of the rear hero. It matters that it BLINKS, because a
# pulsing thing is a different contaminant from a static one and a floor tuned
# to one frame of it would be tuned to noise. What makes the cut safe anyway
# is structural rather than statistical: the bar is about five pixels tall, so
# no square block of side 20 can be carved out of it whatever the pulse is
# doing, and `ASPECT_MIN`/`ASPECT_MAX` want a square.
#
# The 14 px one is the party's own marker inside the map panel, which
# `_in_map_panel` throws out independently.
#
# 0.015 sits between the two populations with a 32% margin below the signal
# and a 47% margin above the largest contaminant. MASS would have worked less
# well: 133 and 163 pixels against 50 and 95, so the nearest miss is only 29%
# clear where side length is 100% clear.
BUTTON_MIN_FRAC, BUTTON_MAX_FRAC = 0.015, 0.045
# The panel is right-anchored; nothing left of here is part of it.
PANEL_LEFT_FRAC = 0.5

# Colour and size alone are not enough, and the control frame proved it: a
# frame with NO panel open still yielded a "button" from red scenery near the
# corridor gate. The two are far apart on shape and mass, so both are required.
#
#   real X (1280x720)   28x28  aspect 1.00  133 px
#   scenery blob        17x22  aspect 0.77    8 px
#
# A button is square. Pixel count scales with AREA, so the floor is expressed
# against the frame's area -- 40 px at 1280x720, 160 at 2560x1440, against a
# real X that carries 133 and 532 respectively.
ASPECT_MIN, ASPECT_MAX = 0.8, 1.25
MIN_PX_AT_720P = 40

# --- the slot grid ---

# The row is three wide -- hand, item, X -- on every panel seen, including one
# for a curio with no item interaction at all. So these are positions, not a
# search: `ITEM_SLOT` is one pitch left of the X and `HAND_SLOT` is two.
ITEM_SLOT, HAND_SLOT = 1, 2
# A slot box, as a fraction of the pitch. Deliberately smaller than 1.0: the
# plates very nearly touch, and a box overlapping its neighbour would let a
# bright item leak into the hand test.
SLOT_BOX_FRAC = 0.35
SLOT_BAND_FRAC = 0.42

# The hand's gold plate. `warm` is R - B and `lit` is mean luma; the pair is
# what the plate has and an icon on a dark plate does not.
HAND_WARM_OVER, HAND_LIT_MIN = 25, 55
# A BAND, not a floor, and the two edges are set by different things.
#
# The plate reads 0.114 and 0.115 on the two real panels. Over the other 300
# frames in this repo the same box reads 0.067 at worst from below and 0.130 at
# worst from above -- the Hamlet's own furniture, which is also warm and lit.
# A floor at 0.05 admits 24 town screens; a floor at 0.09 admits the other 15.
# Only a band separates them, and a band is defensible here precisely because a
# fixed sprite has a fixed fill: each edge sits at the geometric mean of the
# signal and the nearest contaminant.
#
# THE FALSIFIER: a real panel at some resolution whose plate falls outside it.
# The failure is `hand=None`, so the bridge closes the panel and walks away
# rather than clicking blind -- and `bridge/act.py` says so in the log.
HAND_WARM_BAND = (0.086, 0.122)

# Is there an item in that slot, or is the slot drawn empty/greyed out?
# Measured: the Shipwreck's usable shovel reads p95 102.5, the Tent's dimmed
# item 57.0, an undrawn slot 58.0. So a dim slot is indistinguishable from no
# slot -- which is fine, because both mean "there is nothing here to click".
#
# THE FALSIFIER, and it is a cheap one: a frame where the party carries the
# provision a curio wants, whose slot reads below this. n=2 is what exists, so
# the failure direction matters more than the number -- `sole_item` returns
# None and the caller uses the hand or walks away.
ITEM_LIT_P95 = 80.0


@dataclass(frozen=True)
class CurioButtons:
    """Client-area FRACTIONS, ready for `win.to_screen`.

    `hand` is None when the row is not one this module has seen. The X is
    still reported, because walking away is always safe and is what a caller
    that cannot read the panel should do.
    """
    hand: Optional[tuple[float, float]]
    close: tuple[float, float]
    items: tuple[tuple[float, float], ...] = ()
    usable: tuple[bool, ...] = ()

    @property
    def readable(self) -> bool:
        """Is this a panel, rather than a red thing that passed the X test?"""
        return self.hand is not None

    def sole_item(self) -> Optional[tuple[float, float]]:
        """The one item slot, when exactly one is drawn and lit.

        `bridge/curios.py` already picks a single provision out of what the
        party carries, so the only question left is which slot holds it -- and
        with two lit slots there is nothing on screen that says which. Refusing
        is the point: clicking the wrong one spends the wrong provision.
        """
        lit = [p for p, ok in zip(self.items, self.usable) if ok]
        return lit[0] if len(lit) == 1 else None

    def line(self) -> str:
        hand = ("--" if self.hand is None
                else f"({self.hand[0]:.4f}, {self.hand[1]:.4f})")
        items = " ".join(f"({p[0]:.4f}{'*' if ok else ''})"
                         for p, ok in zip(self.items, self.usable)) or "none"
        return (f"hand {hand}  items {items}  "
                f"X ({self.close[0]:.4f}, {self.close[1]:.4f})")


def _red_blocks(img: np.ndarray) -> list[tuple[float, float, int, int, float]]:
    """`(cx, cy, width, count, aspect)` per red cluster, right half only."""
    h, w, _ = img.shape
    a = img.astype(np.int16)
    r, g, b = a[:, :, 0], a[:, :, 1], a[:, :, 2]
    mask = (r > RED_MIN) & (r - g > RED_OVER) & (r - b > RED_OVER)
    ys, xs = np.nonzero(mask)
    keep = xs > w * PANEL_LEFT_FRAC
    xs, ys = xs[keep], ys[keep]
    if xs.size == 0:
        return []
    order = np.argsort(xs)
    xs, ys = xs[order], ys[order]
    out, start = [], 0
    for i in range(1, xs.size + 1):
        if i == xs.size or xs[i] - xs[i - 1] > 20:
            out.extend(_split_by_row(xs[start:i], ys[start:i]))
            start = i
    return out


def _split_by_row(gx, gy) -> list[tuple[float, float, int, int, float]]:
    """One x-column of red pixels, cut again wherever it jumps in Y.

    **Clustering on X alone merges the curio panel's X with whatever red sits
    under it**, and something always does: the map panel's quest tokens and
    the raid pack's red trinkets are both right-half and both overlap the
    panel's x range. The merged cluster then spans y 295 to 700, its aspect
    collapses to nothing, the size filter throws it out, and `find` reports
    "no red X" at a panel that is plainly open. Measured 2026-09-07 on a frame
    Fran opened by hand: 144 red pixels in a 28x28 box at (974, 308), every
    filter satisfied, and no block offered.

    `_in_map_panel` was the earlier attempt at this and it cannot work from
    where it stands -- it judges the CENTROID of a cluster that has already
    been merged, so the answer it is given is about neither blob.

    A strict refinement: a group already tight in y comes back unchanged, and
    the gap is the same 20 px the x pass uses, because the row of three
    buttons is one row.
    """
    order = np.argsort(gy)
    gx, gy = gx[order], gy[order]
    out, start = [], 0
    for i in range(1, gy.size + 1):
        if i == gy.size or gy[i] - gy[i - 1] > 20:
            cx, cy = gx[start:i], gy[start:i]
            bw = int(cx.max() - cx.min() + 1)
            bh = int(cy.max() - cy.min() + 1)
            out.append((float(cx.mean()), float(cy.mean()), bw,
                        int(cx.size), bw / bh if bh else 0.0))
            start = i
    return out


def _slot_box(img: np.ndarray, cx: float, cy: float,
              pitch: float) -> Optional[np.ndarray]:
    """The pixels of one slot, or None when it falls off the frame."""
    h, w, _ = img.shape
    half, band = pitch * SLOT_BOX_FRAC, pitch * SLOT_BAND_FRAC
    x0, x1 = int(cx - half), int(cx + half)
    y0, y1 = int(cy - band), int(cy + band)
    if x0 < 0 or y0 < 0 or x1 > w or y1 > h or x1 - x0 < 4 or y1 - y0 < 4:
        return None
    return img[y0:y1, x0:x1, :].astype(float)


def _warm_lit_fraction(box: np.ndarray) -> float:
    """How much of the slot is the hand's gold plate."""
    lum = box.mean(axis=2)
    warm = box[:, :, 0] - box[:, :, 2]
    return float(((warm > HAND_WARM_OVER) & (lum > HAND_LIT_MIN)).mean())


def _lit_p95(box: np.ndarray) -> float:
    return float(np.percentile(box.mean(axis=2), 95))


def _in_map_panel(x: float, y: float, w: int, h: int) -> bool:
    """Is this red thing inside the MAP panel, where a curio X never is?

    **The party's own torch marker is warm, compact and about the right mass**,
    and no rule about colour or shape was ever going to separate it from an X:
    measured at (913.5, 623.5) on a plain Crypts corridor, which
    `bridge/mappanel.py` independently fits as the party's own room. It cost a
    live raid an "attempt N/10 ... something red on screen after W" on every
    single curio press, and it is the one candidate in the whole banked corpus
    that this rejects.

    Position is what separates them and it is not a coincidence: DD draws the
    curio and obstacle panels over the middle of the HUD, ABOVE the map box.
    Both real panels here sit at y 308 and 329 against a map panel starting at
    544, so the margin is 200 pixels rather than a tuned few.

    `panel_rect` is imported rather than restated -- the two modules are
    reading the same box, and a copy would drift the first time the panel is
    re-measured.
    """
    try:
        from bridge.mappanel import panel_rect               # noqa: PLC0415
        x0, x1, y0, y1 = panel_rect(w, h)
    except Exception:                                        # noqa: BLE001
        return False
    return x0 <= x <= x1 and y0 <= y <= y1


def find(img: np.ndarray) -> Optional[CurioButtons]:
    """The panel's buttons, or None when there is no red X at all.

    Returning None is a real answer and the common one: the panel is only up
    for the moment between `W` and a click. A caller that gets None must not
    click anything -- there is nothing on screen to click.

    A result whose `hand` is None is the *other* real answer: something red and
    button-shaped is on screen but the row around it is not a curio panel this
    module can read. Sixty of the sixty-two frames that pass the red-X test in
    this repo land there.
    """
    if img is None or img.ndim != 3:
        return None
    h, w, _ = img.shape
    if not (w and h):
        return None

    lo, hi = BUTTON_MIN_FRAC * w, BUTTON_MAX_FRAC * w
    min_px = MIN_PX_AT_720P * (w * h) / (1280 * 720)
    buttons = [blk for blk in _red_blocks(img)
               if lo <= blk[2] <= hi
               and ASPECT_MIN <= blk[4] <= ASPECT_MAX
               and blk[3] >= min_px
               and not _in_map_panel(blk[0], blk[1], w, h)]
    if not buttons:
        return None
    # The rightmost button-sized red thing. The panel is right-anchored, and a
    # red trinket icon in the pack would sit lower and further left.
    cx, cy = max(buttons, key=lambda blk: blk[0])[:2]
    close = (cx / w, cy / h)

    pitch = SLOT_PITCH_FRAC * w
    plate = _slot_box(img, cx - HAND_SLOT * pitch, cy, pitch)
    slot = _slot_box(img, cx - ITEM_SLOT * pitch, cy, pitch)
    lo, hi = HAND_WARM_BAND
    if plate is None or slot is None or not lo <= _warm_lit_fraction(plate) <= hi:
        # The X is real enough to click; the row around it is not a panel this
        # module has seen. Reporting the X anyway is what lets a caller close
        # something it cannot read.
        return CurioButtons(hand=None, close=close)
    return CurioButtons(hand=((cx - HAND_SLOT * pitch) / w, cy / h),
                        close=close,
                        items=(((cx - ITEM_SLOT * pitch) / w, cy / h),),
                        usable=(_lit_p95(slot) >= ITEM_LIT_P95,))


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    from PIL import Image
    img = np.asarray(Image.open(sys.argv[1]).convert("RGB"))
    found = find(img)
    h, w, _ = img.shape
    print(f"{sys.argv[1]}  {w}x{h}")
    if found is None:
        print("  no red X - no panel")
        return 1
    print(f"  {found.line()}")
    if not found.readable:
        print("  an X but no gold hand plate beside it - not a curio panel")
        return 1
    print(f"  in pixels: hand ({found.hand[0] * w:.0f}, {found.hand[1] * h:.0f})"
          f"  X ({found.close[0] * w:.0f}, {found.close[1] * h:.0f})")
    for pt, ok in zip(found.items, found.usable):
        state = "holds something usable" if ok else "empty or greyed out"
        print(f"    item slot ({pt[0] * w:.0f}, {pt[1] * h:.0f})  {state}")
    sole = found.sole_item()
    where = "none" if sole is None else f"({sole[0] * w:.0f}, {sole[1] * h:.0f})"
    print(f"  the one item to click: {where}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
