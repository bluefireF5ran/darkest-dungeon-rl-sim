"""The map panel's cells, read against the art the game ships.

`panels/icons_map/` holds every glyph the dungeon map draws, and the two that
matter are sized: **a room is 64x64 and a hallway tile is 24x24**. That is a
fixed 8:3 ratio at every zoom level, because the panel scales one sprite sheet
rather than drawing two independent grids -- so a detected cell can be called a
room or a hall from its measured size alone, with no template matching and no
threshold tuned on a capture.

Why that matters. `mappanel.find_boxes` returns rectangles; the fit then has to
decide which rectangle is which tile, and until now its only evidence was where
the rectangles sat. A wrong transform that lands every box near *some* tile is
indistinguishable from a right one -- and the map failures banked on 2026-09-03
were exactly that, a fit that was confident and wrong. The save already knows
which of its areas are rooms (`Area.is_room`) and which are corridor, so
requiring the KINDS to agree as well as the positions is a second, independent
constraint on the same transform. It is cheap and it is labelled, which is the
combination this layer never had.

Two deliberate limits:

* **Size classifies; the icon images are for the SUBTYPE.** Telling a battle
  room from a treasure room needs the pixels, and at minimum zoom a 64x64
  sprite is drawn at ~17 px. `classify` reports a subtype only when the
  correlation clears `MATCH_FLOOR` and beats the runner-up; otherwise it says
  `room` and stops. A confident wrong label is worse here than no label.
* **`indicator.png` and `scoutingbanner.png` are not cells.** They are drawn
  over the grid, so their sizes (51x48, 366x63) would read as rooms. They are
  listed in `OVERLAY_ICONS` so a caller can exclude them rather than having to
  know the filenames.
"""
from __future__ import annotations

import functools
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

import numpy as np

#: Where the game keeps them. One directory, no DLC merge -- the map panel is
#: base-game UI and no shipped DLC or mod redraws it.
ICON_SUBDIR = Path("panels") / "icons_map"

#: The shipped sprite sizes, in the sheet's own pixels. THE RATIO IS THE
#: MECHANISM: 64/24 is 8:3, and the panel scales both by one zoom factor, so
#: `room_px / hall_px` is fixed at every zoom and on every window size.
ROOM_ART_PX = 64
HALL_ART_PX = 24
ROOM_OVER_HALL = ROOM_ART_PX / HALL_ART_PX

#: How much of its own 24x24 sprite a hall tile actually inks. Measured off
#: the shipped PNGs, not off a capture: `hall_clear` 21x20, `hall_dim` 20x20,
#: `hall_door` 24x23, so the tile leaves ~1.5 px of margin and a threshold
#: detector measures the ink rather than the sprite. The room sprites ink
#: their full 64 px width, which is why only this side needs the correction.
HALL_INK_PX = 21

#: **A HALL TILE'S SPRITE IS THE PITCH.** Adjacent tiles sit one map unit
#: apart and the panel draws a 24 px hall in that unit, so one map unit IS 24
#: sheet pixels and every other size follows from the sheet with nothing
#: fitted:
#:
#:     room = scale * 64/24 = 2.667 * scale
#:     hall = scale * 21/24 = 0.875 * scale
#:
#: Against the four banked frames -- scale 48.0, 15.3, 12.4 and 7.7 -- the
#: rooms measure 2.688, 2.680, 2.742 and 2.727 and the halls 0.812, 0.850 and
#: 0.968. Both predictions land inside the spread with no constant tuned on a
#: capture, which is the check that this is geometry and not a curve fit.
ROOM_UNITS = ROOM_ART_PX / HALL_ART_PX
HALL_UNITS = HALL_INK_PX / HALL_ART_PX

#: How far a measured cell may sit from its predicted size, as a FRACTION of
#: that size. Two numbers because the two sides are measured to very different
#: precision: the rooms above span 2.68-2.74 (about +-1.5% around 2.667) and
#: the halls 0.81-0.97 (about +-11% around 0.875), which is what a 3 px sprite
#: margin does at low zoom.
#:
#: Set well outside both spreads, and the GAP between the resulting bands is
#: the point. Room accepts 2.00-3.33 units and hall 0.57-1.18, so two hall
#: tiles the segmentation failed to separate -- one pitch plus a tile, about
#: 1.88 units -- land in neither and are reported UNKNOWN. A merge called a
#: room is a vote for a transform one cell out; a merge called UNKNOWN is
#: simply excluded, which is the safe direction here.
ROOM_TOLERANCE = 0.25
HALL_TOLERANCE = 0.35

#: A template correlation below this is not a subtype claim. Set high on
#: purpose: at minimum zoom a room sprite is ~17 px and the subtypes differ by
#: a small central glyph, so most of any correlation is the shared border.
MATCH_FLOOR = 0.80
#: ...and it must beat the runner-up by this much, or the two are the same
#: picture with noise between them.
MATCH_MARGIN = 0.03

#: Drawn OVER the grid, not part of it. `indicator` is the party token and
#: `scoutingbanner` the scouting flash; both are room-sized or larger and would
#: otherwise be classified as cells.
OVERLAY_ICONS = ("indicator", "scoutingbanner", "moving_room",
                 "marker_room_visited")

#: `Tile.content` -> the room sprite the panel draws for it. Keys are the
#: constants in `bridge/dungeon_map.py`; the values are file stems.
#:
#: `CONTENT_TRAP`, `CONTENT_OBSTACLE` and `CONTENT_HUNGER` have no room sprite
#: of their own -- they are corridor events and the panel draws them as a
#: `marker_*` on a hall tile, which is why they appear in `CONTENT_MARKER`
#: instead. A guarded curio or a guarded treasure draws the reward, not the
#: guard.
CONTENT_ROOM_ICON = {
    0: "room_empty",
    1: "room_battle",
    6: "room_curio",
    7: "room_curio",
    9: "room_treasure",
    10: "room_treasure",
}

#: `Tile.content` -> the small overlay drawn on a corridor tile.
CONTENT_MARKER = {
    1: "marker_battle",
    3: "marker_trap",
    4: "marker_obstacle",
    6: "marker_curio",
    7: "marker_curio",
    8: "marker_hunger",
    9: "marker_curio",
    10: "marker_curio",
}

ROOM = "room"
HALL = "hall"
UNKNOWN = "unknown"


@dataclass(frozen=True)
class Icon:
    """One shipped sprite, greyscale, in the sheet's own resolution."""
    name: str
    grey: np.ndarray

    @property
    def art_px(self) -> int:
        return max(self.grey.shape)

    @property
    def is_room(self) -> bool:
        return self.name.startswith("room_") or self.name.startswith("Locked")


@dataclass(frozen=True)
class CellKind:
    """What a detected box is. `subtype` is None when only size was usable."""
    kind: str
    subtype: Optional[str] = None
    confidence: float = 0.0

    @property
    def is_room(self) -> bool:
        return self.kind == ROOM

    def __str__(self) -> str:
        if self.subtype:
            return f"{self.subtype} ({self.confidence:.2f})"
        return self.kind


def icon_dir(game_dir) -> Path:
    return Path(game_dir) / ICON_SUBDIR


@functools.lru_cache(maxsize=4)
def load_icons(game_dir: str) -> dict:
    """Every sprite in `panels/icons_map/`, greyscale, keyed by file stem.

    Returns `{}` rather than raising when the directory or Pillow is missing:
    this module is an *extra* constraint on the fit, and a bridge that cannot
    read the art must degrade to the size rule rather than stop working.
    """
    try:
        from PIL import Image
    except ImportError:
        return {}
    root = icon_dir(game_dir)
    if not root.is_dir():
        return {}
    out = {}
    for path in sorted(root.glob("*.png")):
        try:
            with Image.open(path) as handle:
                grey = np.asarray(handle.convert("L"), dtype=np.float64)
        except (OSError, ValueError):
            continue
        out[path.stem] = Icon(name=path.stem, grey=grey)
    return out


def room_icons(game_dir: str) -> dict:
    return {k: v for k, v in load_icons(game_dir).items()
            if v.is_room and k not in OVERLAY_ICONS}


def hall_icons(game_dir: str) -> dict:
    return {k: v for k, v in load_icons(game_dir).items()
            if k.startswith("hall_")}


#: The size that separates a room from a hall, as a multiple of the hall's
#: drawn size. The geometric mean of the two, so the rule is symmetric in the
#: ratio rather than favouring whichever side an arithmetic mean lands on.
SPLIT_RATIO = (ROOM_UNITS / HALL_UNITS) ** 0.5


def room_px(scale: float) -> float:
    """A room's drawn size at this fit scale, in client pixels."""
    return scale * ROOM_UNITS


def hall_px(scale: float) -> float:
    """A hall tile's drawn size at this fit scale, in client pixels."""
    return scale * HALL_UNITS


def classify_size(box_px: float, scale: Optional[float] = None) -> CellKind:
    """Room or hall, from the drawn size alone.

    With a `scale` in hand the test is absolute -- a box near `hall_px(scale)`
    is a hall and one near `room_px(scale)` is a room, both predicted from the
    sprite sheet. Without one, the caller has no reference and this can only
    answer UNKNOWN; a ratio needs two sizes to compare, which is what
    `classify_boxes` supplies.
    """
    if scale is None or scale <= 0 or box_px <= 0:
        return CellKind(UNKNOWN)
    units = box_px / scale
    for want, tol, kind in ((HALL_UNITS, HALL_TOLERANCE, HALL),
                            (ROOM_UNITS, ROOM_TOLERANCE, ROOM)):
        slack = abs(units - want) / want
        if slack <= tol:
            return CellKind(kind, confidence=1.0 - slack)
    return CellKind(UNKNOWN)


def classify_boxes(sizes: Sequence[float]) -> list:
    """Room or hall for a whole panel, with no scale given.

    The panel draws exactly two cell sizes, so the SMALLEST cluster is the
    halls and anything near `ROOM_UNITS/HALL_UNITS` of it is a room -- a ratio
    test, so it needs no knowledge of the zoom. Uses the 25th percentile rather
    than the minimum because one clipped cell at the panel edge would otherwise
    set the unit for every other box on screen.

    **IT IS UNSOUND WHEN ONLY ONE CELL TYPE IS DRAWN, and that is not a corner
    case.** An unscouted generated dungeon shows its rooms and no corridor at
    all -- `captures/03_ruins_map_panel.png` is eight rooms and nothing else --
    and this then takes the rooms as the hall unit and calls all eight halls.
    Anything holding a scale must use `classify_size` instead; this is for a
    diagnostic printing a panel it has no save for.
    """
    usable = [s for s in sizes if s > 0]
    if not usable:
        return [CellKind(UNKNOWN) for _ in sizes]
    unit = float(np.percentile(sorted(usable), 25))
    if unit <= 0:
        return [CellKind(UNKNOWN) for _ in sizes]
    out = []
    for size in sizes:
        ratio = (size / unit) if size > 0 else 0.0
        if ratio <= 0:
            out.append(CellKind(UNKNOWN))
        elif ratio < SPLIT_RATIO:
            out.append(CellKind(HALL, confidence=min(1.0, ratio)))
        else:
            out.append(CellKind(ROOM,
                                confidence=min(1.0, ratio / ROOM_OVER_HALL)))
    return out


def _normalise(patch: np.ndarray) -> Optional[np.ndarray]:
    """Zero-mean, unit-norm. `None` when the patch is flat (no information)."""
    flat = np.asarray(patch, dtype=np.float64).ravel()
    flat = flat - flat.mean()
    norm = float(np.linalg.norm(flat))
    if norm < 1e-6:
        return None
    return flat / norm


def _resample(grey: np.ndarray, height: int, width: int) -> np.ndarray:
    """Nearest-neighbour resize.

    Deliberately not Pillow: the icons are loaded once and this runs per box
    per candidate, and nearest is what a comparison of two heavily-downscaled
    sprites deserves.
    """
    if height <= 0 or width <= 0:
        return np.zeros((1, 1))
    rows = np.clip((np.arange(height) * grey.shape[0]) // max(height, 1),
                   0, grey.shape[0] - 1)
    cols = np.clip((np.arange(width) * grey.shape[1]) // max(width, 1),
                   0, grey.shape[1] - 1)
    return grey[np.ix_(rows, cols)]


def _scored(patch: np.ndarray, icons: dict) -> list:
    """`[(correlation, stem), ...]`, best first.

    Normalised cross-correlation, which is invariant to the panel's brightness
    -- the same room is drawn dimmer when unvisited, and an absolute difference
    would call that a different sprite.
    """
    ref = _normalise(patch)
    if ref is None or not icons:
        return []
    height, width = patch.shape[:2]
    out = []
    for stem, icon in icons.items():
        cand = _normalise(_resample(icon.grey, height, width))
        if cand is not None:
            out.append((float(np.dot(ref, cand)), stem))
    out.sort(reverse=True)
    return out


def match(patch: np.ndarray, icons: dict) -> tuple:
    """Best `(stem, correlation)` for this patch, or `(None, 0.0)`."""
    scored = _scored(patch, icons)
    if not scored:
        return (None, 0.0)
    best, stem = scored[0]
    return (stem, best)


def classify(patch: np.ndarray, kind: CellKind, game_dir: str) -> CellKind:
    """Add a subtype to a size classification, when the pixels support one.

    Refuses in three cases, each of which has been a real wrong answer in this
    layer before: no icons loaded, a correlation under `MATCH_FLOOR`, and a
    winner that does not beat the runner-up by `MATCH_MARGIN`. A room whose
    subtype is refused is still a room -- the size rule stands on its own.
    """
    if kind.kind == UNKNOWN or patch is None or getattr(patch, "size", 0) == 0:
        return kind
    icons = room_icons(game_dir) if kind.is_room else hall_icons(game_dir)
    scored = _scored(np.asarray(patch, dtype=np.float64), icons)
    if not scored:
        return kind
    best, stem = scored[0]
    runner = scored[1][0] if len(scored) > 1 else -1.0
    if best < MATCH_FLOOR or (best - runner) < MATCH_MARGIN:
        return kind
    return CellKind(kind.kind, subtype=stem, confidence=best)


def expected_kinds(dungeon) -> dict:
    """`{area_id: "room"|"hall"}` from the SAVE, to check a fit against.

    This is the labelled half of the correspondence: the panel's cell sizes say
    room or hall, and so does `Area.is_room`, so a transform has to make the
    two agree as well as line the positions up.
    """
    out = {}
    for area_id, area in getattr(dungeon, "areas", {}).items():
        out[area_id] = ROOM if getattr(area, "is_room", False) else HALL
    return out


def kind_agreement(pairs: Sequence[tuple]) -> float:
    """Share of `(observed CellKind, expected kind)` pairs that agree.

    UNKNOWN observations are EXCLUDED rather than counted as wrong. A cell the
    size rule could not call is a cell this constraint has no opinion about,
    and scoring it as a disagreement would penalise a correct fit for the
    detector's blind spots.
    """
    usable = [(got, want) for got, want in pairs if got.kind != UNKNOWN]
    if not usable:
        return 1.0
    hits = sum(1 for got, want in usable if got.kind == want)
    return hits / len(usable)
