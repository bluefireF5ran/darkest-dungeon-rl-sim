"""Where an area sits on the drawn map, in pixels.

The last thing standing between the bridge and an unattended run. Corridors
walk on `A`/`D`, but **a room is only ever left by clicking the map** — the
game says so itself, in `str_help_raid_room_0`:

    [CLICK] on a room in the map to begin moving to that room.

and again in `tutorial_popup_map_nav_description`:

    You are currently in a room. To move forth, [CLICK] on another room on the
    map display. This will take you to the connecting hallway.

Two things follow from those strings, and both were assumptions the bridge had
wrong. **The click target is the destination ROOM, not the corridor** that
`route.py` names as the next step — the game routes you through the hallway
itself. And a corridor tile is never a valid target, so the small boxes on the
panel are landmarks for measuring, never things to click.

## Why this is measured per frame and not stored in `layout.py`

Every other coordinate in `bridge/` is a fixed fraction of the client rect,
because the HUD does not move. The map does. The game ships
`[MOUSEWHEEL]` zoom, `[RIGHT-CLICK]` + `[DRAG]` pan, a "Center map on party"
button and an `Auto-Center Dungeon Map` option — so the transform from map
units to pixels is **user state**, and a stored constant would be a constant
for one player's scroll wheel. Measured across three real captures the scale
came out 48.0, 15.3 and 10.7 px per unit: not a calibration error, three
different zoom levels.

So the transform is recovered from the frame itself, against the map the save
says is there. `Tile.mappos` is the grid — one unit per tile, contiguous
(`bridge/dungeon_map.py` decodes it out of the nested `static_save` blob), and
`map.bounds` is its exact extent.

## The fit, and why it is allowed to fail

`find_boxes` finds the drawn cells, `fit` recovers `(scale, origin)` such that
every box lands on a known tile, and `locate` returns the click point for one
area **only if a detected box is actually sitting there**. Three separate
refusals, because a wrong click here is not a no-op: the map's own panel sits
over the inventory, and a miss into the world behind it moves the party or
interacts with a curio.

Nothing in this module clicks, sleeps, or reads a file. It takes an image and a
`DungeonMap` and returns a point or `None`, which is what lets the whole thing
be tested against `captures/*.png` with the game closed.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# The right-hand panel, as fractions of the client rect. Shared with
# `bridge/screen.py`'s map-vs-inventory discriminator: the two must agree about
# where the panel is, or one names a screen the other cannot measure.
from bridge.screen import PANEL_X, PANEL_Y, LUMA

# A drawn cell against the panel's near-black ground. The panel's mean
# luminance is ~7; every real cell edge measured 40+.
INK = 40.0
# The fallback for a dim palette -- see `locate`.
INK_DIM = 25.0
# Below this a run is a stray glyph, not a cell. The smallest real tile
# measured is 6 px across (`generic curio.png`, the most zoomed-out capture).
MIN_RUN = 4

# How far a box centre may sit from its predicted tile, as a fraction of the
# scale. 0.35 is set by the widest real disagreement: an unvisited room's box
# is drawn asymmetrically and its centre came out 12 px (0.25 units) off the
# tile it belongs to, while the next tile over is a full 1.0 away -- so this
# has room to absorb the drawing and still cannot reach a neighbour.
TOL_UNITS = 0.35
# A fit has to explain most of what is on screen. Below this the boxes and the
# save are describing different maps and the answer is "I do not know".
MIN_EXPLAINED = 0.75
#: The same gate for a fit whose origin the party token pins (see `fit`).
MIN_EXPLAINED_ANCHORED = 0.5
MIN_MATCHED = 3

# A room's box is drawn several times the size of a corridor tile (measured
# 129x128 vs 39x40 at 48 px/unit, 41x41 vs 13x13 at 15.3). Only used to report
# the split; the fit itself does not care which is which.
ROOM_MIN_UNITS = 0.55
# How small a drawn cell can be, as a fraction of the scale. The tightest real
# ratio measured is a corridor tile at 13 px on a 15.5 px/unit map (0.84); the
# floor is well under that so a cell drawn with a gap in its border still
# counts, and well over the 3-5 px specks that are glyphs rather than cells.
MIN_CELL_UNITS = 0.45


@dataclass(frozen=True)
class Box:
    """A drawn cell, in client pixels."""
    x0: int
    x1: int
    y0: int
    y1: int

    @property
    def centre(self) -> tuple[float, float]:
        return ((self.x0 + self.x1) / 2.0, (self.y0 + self.y1) / 2.0)

    @property
    def size(self) -> tuple[int, int]:
        return (self.x1 - self.x0 + 1, self.y1 - self.y0 + 1)

    def contains(self, x: float, y: float) -> bool:
        return self.x0 <= x <= self.x1 and self.y0 <= y <= self.y1


@dataclass(frozen=True)
class MapFit:
    """map units -> client pixels. Uniform scale, no rotation: the map is
    drawn axis-aligned and the panel never rotates it."""
    scale: float
    ox: float
    oy: float
    matched: int
    boxes: int
    residual: float
    #: Share of matched boxes whose DRAWN SIZE agrees with what the save says
    #: that tile is -- a room-sized box on a room, a hall-sized one on
    #: corridor. 1.0 is also "nobody asked", so an old two-argument `fit` call
    #: is unaffected. See `agreement` for why this is worth carrying.
    agreement: float = 1.0

    def point(self, mappos: tuple[float, float]) -> tuple[float, float]:
        return (self.ox + self.scale * mappos[0],
                self.oy + self.scale * mappos[1])

    @property
    def explained(self) -> float:
        return self.matched / self.boxes if self.boxes else 0.0

    def line(self) -> str:
        kinds = "" if self.agreement >= 1.0 else f", kinds {self.agreement:.2f}"
        return (f"{self.scale:.1f} px/unit, {self.matched}/{self.boxes} boxes "
                f"explained, worst {self.residual:.1f} px{kinds}")


def _runs(profile: np.ndarray, threshold: float,
          min_len: int = MIN_RUN) -> list[tuple[int, int]]:
    """Inclusive [start, end] spans where the profile is above threshold."""
    on = profile > threshold
    out: list[tuple[int, int]] = []
    start: Optional[int] = None
    for i, v in enumerate(on):
        if v and start is None:
            start = i
        elif not v and start is not None:
            if i - start >= min_len:
                out.append((start, i - 1))
            start = None
    if start is not None and len(on) - start >= min_len:
        out.append((start, len(on) - 1))
    return out


#: Pixels kept between a click and its box's edge.
CLICK_MARGIN_PX = 3
#: How close to the panel's edge a box must reach to count as clipped by it.
EDGE_PX = 4
#: How far (map units) past the edge a prediction may sit and still be
#: pulled onto a clipped cell -- half a room.
CLIP_REACH_UNITS = 1.6


#: Where the map GRID starts, below the panel's frame line. `screen.PANEL_Y`
#: (y 510 at 720p) cuts the grid's top 19 px: measured 2026-09-28, frame line
#: at y 487-489 and grid from 491, and a room drawn there (rooC, predicted
#: y 497) was never seen, so the raid stalled in rooH until the loop gave up.
#: Used ONLY by the clipped-cell fallback: widening the fit's own region put a
#: wrong scale on four map fixtures.
MAP_GRID_TOP = 492.0 / 720


def panel_rect(width: int, height: int) -> tuple[int, int, int, int]:
    """The panel in pixels for a frame this size. `(x0, x1, y0, y1)`."""
    return (int(PANEL_X[0] * width), int(PANEL_X[1] * width),
            int(PANEL_Y[0] * height), int(PANEL_Y[1] * height))


# RETRACTED 2026-09-01. Kept, unused, with the evidence -- because the idea is
# plausible enough that someone will have it again.
#
# The theory: the party's orange torch token is drawn on top of the grid, and
# where it straddles the gap between two cells the segmentation merges them.
# `captures/map_party_token_merge.png` really does show `find_boxes` returning
# a 90 px `rooA` and five boxes where six are drawn, and masking the token by
# hue really does turn that into six boxes with a 66 px `rooA`.
#
# **And wiring it in made the route click the WRONG BOX.** The measurements
# that condemn it are the click targets across four live runs, all at the same
# window origin:
#
#     6/6 boxes explained -> clicked client (942, 618) = rooB    correct
#     6/6 boxes explained -> clicked client (942, 618) = rooB    correct
#     5/6 boxes explained -> clicked client (894, 624) = a cell  WRONG
#
# The third is with the mask. The detector was ALREADY explaining every box
# and aiming correctly; the mask split `rooA` into a 66 px room plus a phantom
# 23 px cell, one box then matched no tile, and the fit shifted by a cell.
#
# The reasoning error is worth more than the code: `_map_panel.png` is banked
# **only on failure**, so it is a biased sample of one, and "90 px is too wide"
# came from eyeballing a brightened crop rather than from a frame where the
# route had worked. A 90 px `rooA` is simply what that room measures.
#
# What remains true and unexplained is that a map click sometimes does not
# register even when aimed correctly -- twice tonight out of eight. That is a
# separate problem from box detection and must not be "fixed" here again.
TOKEN_MIN_RED = 0.45
TOKEN_RED_OVER_BLUE = 1.6
TOKEN_MAX_GREEN_RATIO = 0.95
TOKEN_MIN_SAT = 0.5


def _without_party_token(img: np.ndarray) -> np.ndarray:
    """`img` with saturated-orange pixels blanked. **NOT USED** -- see above."""
    a = np.asarray(img, dtype=np.float64) / 255.0
    red, green, blue = a[..., 0], a[..., 1], a[..., 2]
    top, bottom = a.max(2), a.min(2)
    sat = np.where(top > 0, (top - bottom) / np.maximum(top, 1e-6), 0.0)
    token = ((red > TOKEN_MIN_RED)
             & (red > blue * TOKEN_RED_OVER_BLUE)
             & (green < red * TOKEN_MAX_GREEN_RATIO)
             & (sat > TOKEN_MIN_SAT))
    if not token.any():
        return img
    out = np.array(img, copy=True)
    out[token] = 0
    return out


def find_boxes(img: np.ndarray, ink: float = INK,
               top: Optional[float] = None) -> list[Box]:
    """The drawn cells in the map panel.

    Column runs, then row runs *within* each column run, then column runs again
    *within* each of those. Two passes rather than one, and the second is what
    makes the coordinates usable: a vertical corridor sharing a column span
    with a wide room comes out of pass one with the room's full width, putting
    its centre tens of pixels sideways of where it is drawn. Re-tightening x
    against only its own rows fixes that, and splits the span when it really
    did hold two cells side by side.

    This is a segmentation, not a labelling. It is exact for an axis-aligned
    grid, which is what the panel draws, and costs a handful of profile passes
    where a flood fill would cost a walk over 300k pixels on every poll.
    """
    if img is None or getattr(img, "ndim", 0) != 3:
        return []
    height, width = img.shape[:2]
    x0, x1, y0, y1 = panel_rect(width, height)
    if top is not None:
        y0 = int(top * height)
    if x1 <= x0 or y1 <= y0:
        return []
    lum = np.asarray(img, dtype=np.float64)[y0:y1, x0:x1] @ LUMA

    out: list[Box] = []
    for a, b in _runs(lum.max(axis=0), ink):
        column = lum[:, a:b + 1]
        for c, d in _runs(column.max(axis=1), ink):
            band = lum[c:d + 1, a:b + 1]
            for e, f in _runs(band.max(axis=0), ink) or [(0, b - a)]:
                out.extend(_split(lum, a + e, a + f, c, d, x0, y0, ink=ink))
    return out


#: How many alternating x/y tightenings a single region may take before it is
#: accepted as drawn. Six is far above what any real panel needs -- the
#: measured worst case is three -- and it exists only so a pathological frame
#: cannot spin.
MAX_SPLIT_PASSES = 6


def _split(lum: np.ndarray, a: int, b: int, c: int, d: int,
           x0: int, y0: int, ink: float = INK) -> list[Box]:
    """Keep alternating x and y runs inside a region until it stops splitting.

    THE THREE FIXED PASSES ABOVE ARE NOT ENOUGH AT LOW ZOOM, and this is the
    specific reason minimum zoom -- the only view that shows the whole map --
    could not be fitted. Pass two takes row runs over the *whole* column span,
    so when different columns hold cells at different heights the bright rows
    join up and the entire map region comes back as one y-run. Pass three then
    splits it by x only, and the result is a set of full-height columns:
    measured on `captures/_map_panel.png`, four boxes of 36x75, 11x75, 12x75
    and 50x75 where a room is 34 px and a hall is 12.

    Alternating until nothing moves is what the first three passes were already
    reaching towards; it costs a handful more profile passes on a 472x197 crop
    and it turns those four boxes into two 34x34 rooms and their hall tiles.
    Measured on the same frame: 19 boxes -> 21, with the room sizes correct.

    It cannot MERGE anything, so it can only ever refine what the old code
    returned -- the tutorial corpus comes back byte-identical at 6 boxes.

    What it cannot do is separate two cells that touch only at a corner: a
    projection sees an L-shaped region as its bounding box whichever axis it
    cuts on. That is what `mapicons` is for -- a box far larger than the 8:3
    room size is a merge, and the size rule can say so even when the
    segmentation cannot undo it.
    """
    regions = [(a, b, c, d)]
    quiet = 0
    # y FIRST: the caller has just tightened x, so cutting on x again cannot
    # move anything and a naive "stop when nothing moved" would stop before the
    # first useful pass.
    for depth in range(MAX_SPLIT_PASSES):
        axis = (depth + 1) % 2
        nxt: list[tuple[int, int, int, int]] = []
        moved = False
        for ra, rb, rc, rd in regions:
            patch = lum[rc:rd + 1, ra:rb + 1]
            if axis == 0:
                runs = _runs(patch.max(axis=0), ink) or [(0, rb - ra)]
                pieces = [(ra + s, ra + e, rc, rd) for s, e in runs]
            else:
                runs = _runs(patch.max(axis=1), ink) or [(0, rd - rc)]
                pieces = [(ra, rb, rc + s, rc + e) for s, e in runs]
            if len(pieces) > 1 or pieces[0] != (ra, rb, rc, rd):
                moved = True
            nxt.extend(pieces)
        regions = nxt
        # One quiet pass proves nothing -- the axis that just ran was tightened
        # by the pass before it. It takes a quiet pass on EACH axis to say the
        # region is drawn as one cell.
        quiet = 0 if moved else quiet + 1
        if quiet >= 2:
            break
    return [Box(ra + x0, rb + x0, rc + y0, rd + y0)
            for ra, rb, rc, rd in regions]


def _candidate_scales(boxes: Sequence[Box],
                     tiles: Sequence[tuple[float, float]] = ()) -> list[float]:
    """Plausible px-per-unit values, best first.

    Adjacent tiles are exactly one unit apart, so the gap between two
    neighbouring boxes IS the scale -- when both are drawn. Rooms swallow the
    door tiles either side of them, so plenty of gaps are 2 or 3 units instead,
    which is why each observed gap is also offered divided by a set of
    plausible unit-distances rather than trusting the smallest.

    A median would be wrong here and it is worth saying why: the tutorial's six
    boxes give gaps 96, 48, 48, 48, 95.5, 95.5, whose median is 71.75 -- a
    scale that matches nothing. Every candidate is scored by the fit instead.

    **THE DIVISORS ARE DERIVED FROM THE SAVE, and a fixed (1,2,3) is why this
    refused every full-size dungeon.** In the Old Road the drawn cells really
    are 1-3 units apart, because its rooms and corridor tiles sit in one
    contiguous run. A generated Ruins map is hub-and-spoke: its eight rooms sit
    at unit x in {1,8,15} and y in {1,8,15,22}, **seven units apart**, with a
    six-tile corridor between them that the panel draws as dots too small for
    `find_boxes`. So the only gaps observable between two boxes were 7 units,
    7 was not in the divisor set, and the true scale (~7.8 px/unit -- smaller
    than any previously recorded) was never even offered to `_score`.

    Widening to a fixed larger set would be a guess. The pairwise distances
    that ACTUALLY occur are in the save, so they are read off it: a gap can
    only correspond to a distance the map really contains.
    """
    centres = [b.centre for b in boxes]
    gaps: list[float] = []
    for i, (ax, ay) in enumerate(centres):
        best = None
        for j, (bx, by) in enumerate(centres):
            if i == j:
                continue
            d = max(abs(ax - bx), abs(ay - by))
            if d > 1.0 and (best is None or d < best):
                best = d
        if best is not None:
            gaps.append(best)
    seen: list[float] = []
    for g in sorted(gaps):
        for divisor in _unit_distances(tiles):
            s = g / divisor
            if s < 3.0:
                continue
            # RELATIVE, not the flat 0.5 px/unit this used to be. That
            # tolerance is 1% at the tutorial's 48 px/unit and **6.4%** at a
            # generated dungeon's ~7.8, where it merged 7.43 and 7.71 and kept
            # the first -- and over the 22 units from the entrance to the far
            # room a 0.28 px/unit error is ~6 px of drift, which is enough to
            # miss the boxes it was meant to explain. The floor keeps the
            # behaviour identical at every scale previously recorded.
            if not any(abs(s - t) < max(0.05, 0.01 * s) for t in seen):
                seen.append(s)
    return seen


# A gap between two drawn cells has to be a distance the map actually contains,
# and beyond a point a "distance" is really two cells that are simply far
# apart rather than neighbours. 12 covers the widest room spacing seen in a
# generated dungeon (7) with room to spare, and keeps the candidate list short
# enough that `_score` is still the thing deciding.
MAX_NEIGHBOUR_UNITS = 12.0


def _unit_distances(tiles: Sequence[tuple[float, float]]) -> list[float]:
    """The small pairwise distances the map really contains, as divisors.

    Chebyshev, to match the metric `_candidate_scales` measures gaps with.
    Falls back to the historical (1, 2, 3) when no tiles are supplied, so an
    old call site keeps its exact behaviour.
    """
    if not tiles:
        return [1.0, 2.0, 3.0]
    found: set[float] = set()
    for i, (ax, ay) in enumerate(tiles):
        for bx, by in tiles[i + 1:]:
            d = max(abs(ax - bx), abs(ay - by))
            if 0.0 < d <= MAX_NEIGHBOUR_UNITS:
                found.add(round(d, 3))
    return sorted(found) or [1.0, 2.0, 3.0]


def _cells_for(boxes: Sequence[Box], scale: float) -> list[Box]:
    """The boxes big enough to be a cell at this scale.

    Resolves the one circularity in the fit. A cell drawn at `scale` px per
    unit cannot be much smaller than `scale`, so a 3-pixel speck is not a
    candidate tile at 48 px/unit however well it votes -- but "much smaller
    than what" is only answerable once a scale is proposed. So it is answered
    per candidate, which also means a wrong large scale is judged on the few
    boxes that could belong to it rather than on the whole panel.
    """
    floor = MIN_CELL_UNITS * scale
    return [b for b in boxes if min(b.size) >= floor]


def _closest_pair(boxes: Sequence[Box]) -> Optional[float]:
    """The smallest centre-to-centre distance between two drawn cells.

    Bounds the scale from above, and not by a tuned constant: tiles are one map
    unit apart and nothing is drawn closer, so no two cells can sit nearer than
    one scale. A candidate above this is claiming a zoom at which two of the
    boxes it is fitting would have to overlap.
    """
    best = None
    for i, a in enumerate(boxes):
        ax, ay = a.centre
        for b in boxes[i + 1:]:
            bx, by = b.centre
            d = max(abs(ax - bx), abs(ay - by))
            if d > 1.0 and (best is None or d < best):
                best = d
    return best


#: Below this share of matched boxes agreeing with the save about room-vs-hall,
#: the transform is describing a different map and is refused.
#:
#: 0.60 is set by the measured separation rather than by taste. On
#: `captures/_map_panel.png` against its own save the correct fit scores
#: **1.00 over 16 boxes**, and the same fit walked one cell in each of the four
#: directions scores 1.00/0.86/0.71/0.62 over 6 to 8 boxes. So the constraint
#: does NOT separate a shift on its own -- `matched` already does that, 16
#: against 6-8 -- and it is here as a veto on the case `matched` cannot see: a
#: transform that explains plenty of boxes by landing rooms on corridor.
MIN_KIND_AGREEMENT = 0.60


def _agreement(boxes: Sequence[Box], tile_kinds: dict, scale: float,
               ox: float, oy: float) -> float:
    """Share of matched boxes whose drawn size agrees with the save.

    **Scored per BOX, never per tile**, and that is the whole difference
    between a usable constraint and a useless one. A room's box is ~2.7 units
    across, so the corridor tiles beside it fall INSIDE it; asking "what is
    drawn where this tile is" therefore reports a room for several hall tiles
    and the correct fit scores 0.46. Asking "what is this box, and which tile
    is nearest its centre" scores 1.00 on the same frame.
    """
    if not tile_kinds:
        return 1.0
    from bridge import mapicons
    # The ABSOLUTE test, not `classify_boxes`. The relative one takes the
    # smallest cluster on screen as the hall size, and a frame showing eight
    # rooms and no drawn corridor -- an unscouted generated dungeon, which
    # `captures/03_ruins_map_panel.png` is -- then reads all eight as halls and
    # refuses a correct fit. The scale is in hand here, so there is no reason
    # to infer a unit that the fit already states.
    kinds = [mapicons.classify_size(max(b.size), scale) for b in boxes]
    tol = TOL_UNITS * scale
    pairs = []
    for box, kind in zip(boxes, kinds):
        cx, cy = box.centre
        best, near = None, None
        for pos, want in tile_kinds.items():
            dist = max(abs(cx - (ox + scale * pos[0])),
                       abs(cy - (oy + scale * pos[1])))
            if near is None or dist < near:
                best, near = want, dist
        if near is not None and near <= tol:
            pairs.append((kind, best))
    return mapicons.kind_agreement(pairs)


def _score(boxes: Sequence[Box], tiles: Sequence[tuple[float, float]],
           scale: float, tile_kinds: Optional[dict] = None,
           anchor=None) -> Optional[MapFit]:
    """Best translation for one scale, by voting.

    Every (box, tile) pair implies one origin. The true origin is implied by
    every correctly paired box at once, so it wins the vote by the number of
    boxes that are really on the map -- and a scale that is wrong scatters its
    votes and loses. Bins are a third of the tolerance so a near-miss still
    lands with its neighbours.
    """
    cells = _cells_for(boxes, scale)
    if len(cells) < MIN_MATCHED:
        return None
    closest = _closest_pair(cells)
    if closest is not None and scale > closest * (1.0 + TOL_UNITS):
        return None

    tol = TOL_UNITS * scale
    bin_size = max(tol / 3.0, 1.0)
    votes: dict[tuple[int, int], list[tuple[float, float]]] = {}
    for box in cells:
        cx, cy = box.centre
        for tx, ty in tiles:
            ox, oy = cx - scale * tx, cy - scale * ty
            key = (int(round(ox / bin_size)), int(round(oy / bin_size)))
            votes.setdefault(key, []).append((ox, oy))
    if not votes:
        return None

    best_key = max(votes, key=lambda k: len(votes[k]))
    picked = votes[best_key]
    ox = float(np.mean([p[0] for p in picked]))
    oy = float(np.mean([p[1] for p in picked]))
    if anchor is not None:
        # The party token IS the party's room: its pixel and the room's map
        # position fix the origin for this scale, and the vote -- which with
        # three drawn cells can pick another part of the map -- is not asked.
        (mx, my), (px, py) = anchor
        ox, oy = px - scale * mx, py - scale * my

    # Re-derive the match from the agreed origin rather than trusting the bin:
    # a box on a bin boundary votes in one bucket and matches from another.
    matched, worst = 0, 0.0
    for box in cells:
        cx, cy = box.centre
        near = min((max(abs(cx - (ox + scale * tx)), abs(cy - (oy + scale * ty)))
                    for tx, ty in tiles), default=None)
        if near is not None and near <= tol:
            matched += 1
            worst = max(worst, near)
    return MapFit(scale=scale, ox=ox, oy=oy, matched=matched,
                  boxes=len(cells), residual=worst,
                  agreement=_agreement(cells, tile_kinds or {}, scale, ox, oy))


# How far either side of the winning candidate to look, and how finely.
# `_candidate_scales` can only ever propose a scale that some PAIR of drawn
# boxes implies, so the truth is offered only when two boxes sit an exact whole
# number of units apart and both are drawn. Measured on a live Warrens map: the
# candidates were 11.50, 12.00 and 12.25, the truth was 11.70-11.95, and the
# best candidate matched 5 of 7 rooms -- explained 0.71 against the 0.75 bar,
# so a frame with EVERY room drawn was refused and the party sat in `rooC` for
# the whole play budget. +-12% in 0.5% steps costs ~50 more `_score` calls,
# which is nothing beside the box detection that already ran.
REFINE_SPAN = 0.12
REFINE_STEPS = 24


def _rank(got: MapFit) -> tuple:
    """How two candidate scales are compared.

    **The residual is in PIXELS and the tolerance is proportional to the
    scale**, so a raw residual is not comparable across scales: a small scale
    has a small tolerance and therefore small residuals, and ranking on it
    systematically prefers reading the map as tinier than it is. Measured on a
    live Warrens frame: three candidates each matched 5 boxes, and the raw
    residual handed it to **6.0 px/unit matching 5 of 23 cells (residual 0.4)**
    over **12.25 matching 5 of 7 (residual 4.0)** -- the correct reading, and
    the one whose neighbourhood holds a 7-of-7 fit.

    So: most boxes matched, then the largest FRACTION of drawn cells explained,
    and only then the residual.

    **The fraction is the load-bearing term and the residual is a last
    resort.** Normalising the residual by the scale was tried and reverted the
    same hour: it made the tutorial's 48.0 px/unit drift to 50.4, because
    minimising a scale-relative worst-deviation is a different objective from
    recovering the grid, and nothing pins it to the truth once every box
    already matches.
    """
    return (got.matched, got.explained, -got.residual)


def _refine(boxes: Sequence[Box], tiles: Sequence[tuple[float, float]],
            best: MapFit, tile_kinds: Optional[dict] = None,
            anchor=None) -> MapFit:
    """Walk a fine grid around the winning scale and keep the best.

    Same ranking as `fit` uses -- more boxes matched, then a smaller residual
    -- so this can only ever improve on the candidate it starts from. It is a
    refinement and not a search: a scale 12% away from the vote winner is not
    a different reading of the map, it is the same one measured better.
    """
    # **Only ever buy MATCHES, and judge every candidate against the ORIGINAL.**
    # The refinement exists because the vote winner can be a scale that misses
    # boxes; it has no business re-tuning one that already explains them all.
    # Allowing it to shave the residual walked the tutorial's 48.0 px/unit to
    # 50.4 -- a 5% error, a third of a cell over seven units, on a frame that
    # was already fitted exactly.
    #
    # Among the candidates that DO strictly improve, take the tightest.
    # `TOL_UNITS` is 0.35, so a whole band of scales reaches the same match
    # count and accepting the first walks to the edge of it: measured on a
    # synthetic grid whose true scale was 11.83, that gave 10.83 -- every box
    # matched and the transform 8% wrong, which mis-places a distant room even
    # though the nearby ones look right.
    improved: Optional[MapFit] = None
    for k in range(-REFINE_STEPS, REFINE_STEPS + 1):
        if k == 0:
            continue
        scale = best.scale * (1.0 + k * (REFINE_SPAN / REFINE_STEPS))
        if scale <= 0:
            continue
        got = _score(boxes, tiles, scale, tile_kinds, anchor)
        if got is None:
            continue
        if (got.matched, got.explained) <= (best.matched, best.explained):
            continue
        if improved is None or got.residual < improved.residual:
            improved = got
    return improved or best


def fit(boxes: Sequence[Box],
        tiles: Sequence[tuple[float, float]],
        tile_kinds: Optional[dict] = None,
        anchor=None, scale_hint: Optional[float] = None) -> Optional[MapFit]:
    """Recover map-units -> pixels, or None when the frame does not support one.

    None is a normal answer. The panel may be mid-pan, half the map may be
    undrawn, or the frame may not be a map at all -- and every one of those has
    to end in a refusal rather than a fit nobody checked.

    `tile_kinds` is `{mappos: "room"|"hall"}` from `kinds_of(dungeon)`. Given
    it, a fit must also agree with the save about which drawn cells are rooms
    -- see `MIN_KIND_AGREEMENT`. Omitting it reproduces the old behaviour
    exactly, which is what keeps every existing caller and test unmoved.
    """
    if len(boxes) < MIN_MATCHED or not tiles:
        return None
    best: Optional[MapFit] = None
    # With the party's own room box measured, the scale is KNOWN to within the
    # drawing's slack, and the pairwise candidates -- which on a sparse frame
    # offered 6.8 px/unit and let merged corridor slabs outvote the truth --
    # are not needed.
    scales = ([scale_hint * (1.0 + k * (REFINE_SPAN / REFINE_STEPS))
               for k in range(-REFINE_STEPS, REFINE_STEPS + 1)]
              if scale_hint else _candidate_scales(boxes, tiles))
    for scale in scales:
        got = _score(boxes, tiles, scale, tile_kinds, anchor)
        if got is None:
            continue
        if best is None or _rank(got) > _rank(best):
            best = got
    if best is None:
        return None
    # Refine BEFORE the acceptance gates, never after: the whole point is that
    # the vote winner can be a slightly-wrong scale that fails them while the
    # true one passes.
    best = _refine(boxes, tiles, best, tile_kinds, anchor)
    if best.matched < MIN_MATCHED:
        return None
    # An ANCHORED fit has the party token pinning its origin, which is worth
    # more than a vote share -- live 2026-09-25 a vertical corridor drawn as
    # merged slabs held a correct, anchored fit at 5/7 under the 0.75 bar.
    if best.explained < (MIN_EXPLAINED_ANCHORED if anchor is not None
                         else MIN_EXPLAINED):
        return None
    if best.agreement < MIN_KIND_AGREEMENT:
        return None
    return best


def tiles_of(dungeon) -> list[tuple[float, float]]:
    """Every tile the save places on the grid, as bare coordinates."""
    return [t.mappos for area in dungeon.areas.values()
            for t in area.tiles if t.mappos is not None]


def kinds_of(dungeon) -> dict:
    """`{mappos: "room"|"hall"}` -- the labelled half of the correspondence.

    A tile belongs to exactly one area and `Area.is_room` is what the save
    says, so this needs no heuristic. Feed it to `fit` to make the transform
    answer to the drawn cell SIZES as well as their positions.
    """
    from bridge import mapicons
    out = {}
    for area in dungeon.areas.values():
        kind = mapicons.ROOM if area.is_room else mapicons.HALL
        for tile in area.tiles:
            if tile.mappos is not None:
                out[tile.mappos] = kind
    return out


def area_point(dungeon, area_id: str) -> Optional[tuple[float, float]]:
    """The map coordinate to aim at for one area.

    A room has a single tile and that is the answer. A corridor is asked for
    only by diagnostics -- the game does not accept one as a destination -- and
    answers with its middle tile.
    """
    area = dungeon.areas.get(area_id)
    if area is None:
        return None
    placed = [t for t in area.tiles if t.mappos is not None]
    if not placed:
        return None
    return placed[len(placed) // 2].mappos


#: Token pixels needed before a centre is believed, and the cluster radius.
TOKEN_MIN_PIXELS = 20
#: The torch is ORANGE (green/red ~0.45-0.49 measured); a red room icon on
#: the map is ~0.21 and was taken for the token on 2026-09-26, which made the
#: party-on-token check refuse a CORRECT fit for three iterations.
TOKEN_MIN_GREEN_RATIO = 0.35
TOKEN_RADIUS = 14


def party_token(img: np.ndarray) -> Optional[tuple[float, float]]:
    """The party's torch token on the map, as client pixels, or None.

    Searched INSIDE the panel only -- the torch meter above the scene is the
    same orange -- and as the densest cluster rather than the mean, because
    a room icon nearby carries a few orange pixels of its own (measured: the
    plain mean sat 12 px left of the token). The same colour gates as
    `_without_party_token`, which stays retracted as a box-detection change;
    this only reads WHERE the token is."""
    height, width = img.shape[:2]
    x0, x1, y0, y1 = panel_rect(width, height)
    a = np.asarray(img[y0:y1, x0:x1], dtype=np.float64) / 255.0
    red, green, blue = a[..., 0], a[..., 1], a[..., 2]
    top, bottom = a.max(2), a.min(2)
    sat = np.where(top > 0, (top - bottom) / np.maximum(top, 1e-6), 0.0)
    mask = ((red > TOKEN_MIN_RED) & (red > blue * TOKEN_RED_OVER_BLUE)
            & (green < red * TOKEN_MAX_GREEN_RATIO) & (sat > TOKEN_MIN_SAT)
            & (green > red * TOKEN_MIN_GREEN_RATIO))
    ys, xs = np.nonzero(mask)
    if len(xs) < TOKEN_MIN_PIXELS:
        return None
    grid, xe, ye = np.histogram2d(
        xs, ys, bins=[max(1, (x1 - x0) // TOKEN_RADIUS),
                      max(1, (y1 - y0) // TOKEN_RADIUS)])
    # Every orange CLUSTER, then the one nearest where the map puts the party.
    # The densest was the first rule and it lost twice on 2026-09-26: a red
    # fight-room icon (gated out by colour) and a treasure room's gold icon,
    # which is orange enough to pass. The panel opens CENTRED on the party --
    # (855, 587) at 1280x720 in every frame banked -- so position decides.
    candidates = []
    taken = np.zeros(len(xs), dtype=bool)
    for flat in np.argsort(grid, axis=None)[::-1]:
        i, j = np.unravel_index(int(flat), grid.shape)
        if grid[i, j] < TOKEN_MIN_PIXELS / 4:
            break
        cx, cy = (xe[i] + xe[i + 1]) / 2.0, (ye[j] + ye[j + 1]) / 2.0
        near = ((np.abs(xs - cx) <= TOKEN_RADIUS * 1.5)
                & (np.abs(ys - cy) <= TOKEN_RADIUS * 1.5) & ~taken)
        if int(near.sum()) >= TOKEN_MIN_PIXELS:
            candidates.append((float(xs[near].mean() + x0),
                               float(ys[near].mean() + y0)))
            taken |= near
    if not candidates:
        return None
    hx, hy = TOKEN_HOME[0] * width, TOKEN_HOME[1] * height
    return min(candidates, key=lambda c: (c[0] - hx) ** 2 + (c[1] - hy) ** 2)


#: Where the map panel draws the party when it opens, as a client fraction.
TOKEN_HOME = (855.0 / 1280, 587.0 / 720)


def locate(img: np.ndarray, dungeon, area_id: str, here: str = ""
           ) -> tuple[Optional[tuple[float, float]], str]:
    """Where to click to travel to `area_id`, as a client-rect FRACTION.

    A fraction rather than a pixel because that is what every stored
    coordinate in `bridge/` is and what `GameWindow.to_screen` takes: the game
    scales its 16:9 layout to fill the client area, so a fraction survives a
    windowed game being dragged or resized mid-run and a pixel does not.

    Returns `(point, note)`. `point` is None on every refusal and `note` always
    names which one, because this runs unattended and "it did not click" with
    no reason is the failure mode that cost the most time on the layers below.

    The last check is the one that matters: the predicted point must land
    inside a box that was actually detected. The fit can be excellent and the
    target still undrawn -- an unscouted room simply is not on the map -- and
    clicking where it *would* be puts the cursor on the panel's empty ground.
    """
    target = area_point(dungeon, area_id)
    if target is None:
        return None, f"{area_id} has no coordinates in the save"
    boxes = find_boxes(img)
    if not boxes:
        return None, "nothing is drawn in the map panel"
    # **ANCHORED ON THE PARTY TOKEN when the party is in a room** (live
    # 2026-09-25): with three cells drawn around the entrance, the vote put the
    # whole map 14 x 7 units off -- rooL, the party's own room, predicted at
    # (1003, 660) with the token at (856, 587) -- and the party sat in its
    # first room for two iterations. The save says which room the party is
    # in and the token is drawn on it, so that pair pins the origin.
    anchor = None
    home = dungeon.areas.get(here) if here else None
    if home is not None and home.is_room:
        pos = area_point(dungeon, here)
        tok = party_token(img)
        if pos is not None and tok is not None:
            anchor = (pos, tok)
    got = None
    if anchor is not None:
        from bridge import mapicons                      # noqa: PLC0415
        tx, ty = anchor[1]
        under = [bx for bx in boxes if bx.contains(tx, ty)]
        hint = (max(max(under, key=lambda bx: max(bx.size)).size)
                / mapicons.ROOM_UNITS) if under else None
        got = fit(boxes, tiles_of(dungeon), kinds_of(dungeon), anchor=anchor,
                  scale_hint=hint)
    if got is None:
        got = fit(boxes, tiles_of(dungeon), kinds_of(dungeon))
        if got is not None and anchor is not None:
            hx, hy = got.point(anchor[0])
            if max(abs(hx - anchor[1][0]),
                   abs(hy - anchor[1][1])) > TOL_UNITS * got.scale * 2:
                return None, (f"the map fit puts {here} at {hx:.0f},{hy:.0f} "
                              f"but the party token is at "
                              f"{anchor[1][0]:.0f},{anchor[1][1]:.0f} - not "
                              f"clicking a wrong room")
    if got is None:
        # **A dim palette draws its cells under INK.** Measured 2026-09-23
        # on a Cove map: fill at 20-40 luminance with only broken edges
        # above 40, so rooms came out half-height and a corridor split into
        # 4 px slabs -- 10 boxes, no fit, a raid stalled in its room. At 25
        # the same frame fits 15/15. Tried only after the normal threshold
        # fails, so every map that already worked is left exactly as it was.
        dim = find_boxes(img, ink=INK_DIM)
        dim_fit = fit(dim, tiles_of(dungeon), kinds_of(dungeon)) if dim else None
        if dim_fit is not None:
            boxes, got = dim, dim_fit
    if got is None:
        return None, f"could not fit the map to the {len(boxes)} cells drawn"
    px, py = got.point(target)
    # Only cells the fit itself accepted are clickable. Searching every box
    # would let a 3-pixel glyph that happens to straddle the prediction stand
    # in for the room, and the click would land on a label.
    hits = [b for b in _cells_for(boxes, got.scale) if b.contains(px, py)]
    if not hits:
        # **A cell CLIPPED by the panel's edge is still the cell.** Live
        # 2026-09-27 (Cove): rooI predicted at y 696 with the panel ending at
        # 695 -- its centre off the panel, the top of it drawn -- so no box
        # could contain the prediction, "rooI is not drawn" on every poll,
        # and the raid stalled. Panning is not an option -- a dragged view
        # loses the map (Fran). So the prediction is pulled inside the panel
        # (never further than half a cell), and a box there that touches the
        # edge is taken and clicked in the middle of what is visible.
        height, width = img.shape[:2]
        x0, x1, y0, y1 = panel_rect(width, height)
        floor = MIN_CELL_UNITS * got.scale
        reach = got.scale * CLIP_REACH_UNITS
        qx = min(max(px, x0 + EDGE_PX), x1 - EDGE_PX)
        qy = min(max(py, y0 + EDGE_PX), y1 - EDGE_PX)
        near = abs(qx - px) <= reach and abs(qy - py) <= reach
        clipped = [b for b in boxes if near and b.contains(qx, qy)
                   and max(b.size) >= floor
                   and (b.y1 >= y1 - EDGE_PX or b.y0 <= y0 + EDGE_PX
                        or b.x1 >= x1 - EDGE_PX or b.x0 <= x0 + EDGE_PX)]
        if not clipped and y0 - reach <= py < y0:
            # Above the panel's top the grid is still drawn for 19 px; look
            # there for a box under the prediction's x.
            # Nearest by x within reach, not "contains": a room's icon splits
            # its box in two and the prediction can sit in the 2 px between
            # (live rooC: boxes 866-873 and 876-885, predicted x 875).
            top_strip = [b for b in find_boxes(img, top=MAP_GRID_TOP)
                         if b.y0 < y0 and abs(b.centre[0] - px) <= reach
                         and abs(b.centre[1] - py) <= reach
                         and max(b.size) >= floor]
            if top_strip:
                box = min(top_strip, key=lambda b: abs(b.centre[0] - px))
                cx, cy = box.centre
                return ((cx / width, cy / height),
                        got.line() + " (clipped by the top edge)")
        if clipped:
            box = max(clipped, key=lambda b: b.size[0] * b.size[1])
            cx, cy = box.centre
            return (cx / width, cy / height), got.line() + " (clipped by the edge)"
    if not hits:
        return None, (f"{area_id} is not drawn on the map "
                      f"({got.line()}; predicted {px:.0f},{py:.0f})")
    box = min(hits, key=lambda b: abs(b.centre[0] - px) + abs(b.centre[1] - py))
    height, width = img.shape[:2]
    # **The prediction, kept inside the box -- not the box's centre.** A room
    # and the corridor running into it can come out of `find_boxes` as ONE
    # tall box, whose centre is on the corridor (live 2026-09-28: rooI and
    # coFI both resolved to (850, 638), the room drawn at ~(857, 657), and the
    # raid stalled in rooF). The fit's own point is good to a few pixels.
    m = CLICK_MARGIN_PX
    cx = min(max(px, box.x0 + m), box.x1 - m) if box.x1 - box.x0 > 2 * m else box.centre[0]
    cy = min(max(py, box.y0 + m), box.y1 - m) if box.y1 - box.y0 > 2 * m else box.centre[1]
    return (cx / width, cy / height), got.line()


def main() -> None:
    """Fit one frame against one save and print what came out.

    The tool for the next time a room will not open: it says whether the cells
    were found, whether the transform was recovered, and which of the three
    refusals fired -- rather than leaving "it did not click" as the whole
    report. `bridge/act.py` banks the frame as `captures/_map_panel.png` every
    time it declines, so there is usually one to point this at.
    """
    import argparse

    from bridge.dungeon_map import read_dungeon_map

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("image", help="a PNG of the game, map panel showing")
    ap.add_argument("map_file", help="the matching persist.map.json")
    ap.add_argument("--area", default="", help="resolve this area to a click")
    args = ap.parse_args()

    from PIL import Image                          # noqa: PLC0415  (dev-only)
    img = np.asarray(Image.open(args.image).convert("RGB"), dtype=np.uint8)
    dungeon = read_dungeon_map(args.map_file)
    if dungeon is None:
        raise SystemExit(f"no map in {args.map_file}")

    boxes = find_boxes(img)
    print(f"{len(boxes)} cells drawn in the panel")
    got = fit(boxes, tiles_of(dungeon))
    if got is None:
        print("no fit — the cells and the save do not describe one map")
    else:
        print(f"fit: {got.line()}")
        for b in sorted(boxes, key=lambda b: (b.y0, b.x0)):
            print(f"   {b.size[0]:3d}x{b.size[1]:<3d} at "
                  f"{b.centre[0]:7.1f},{b.centre[1]:7.1f}")

    for area_id in ([args.area] if args.area
                    else sorted(a for a in dungeon.areas if a.startswith("roo"))):
        point, why = locate(img, dungeon, area_id)
        where = (f"click ({point[0]:.4f}, {point[1]:.4f})"
                 if point else "REFUSED")
        print(f"  {area_id:<6} {where:<28} {why}")


if __name__ == "__main__":                          # pragma: no cover
    main()
