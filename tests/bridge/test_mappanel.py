"""Turning an area id into a pixel on the drawn map.

The last thing between the bridge and an unattended run: corridors walk on
`A`/`D`, but a room is only ever left by clicking the map, and until this
module existed the bridge brought the map up and stopped.

**The ground truth is real.** `captures/02_starting_room_After_map_click.png`
is the Old Road's first room with the map panel open, and the tutorial's layout
is hand-authored -- `rooA` at map x -10.5, six corridor tiles at -9.5..-4.5,
`rooB` at -3.5, all on row -7 -- so a fit against that frame is checkable
against a layout that cannot drift. `tests/bridge/test_tutorial_map.py` pins the
layout itself; this pins what it looks like.

The load-bearing tests are the refusals. A wrong click here is not a no-op: the
map panel sits over the inventory and a miss into the world behind it moves the
party or interacts with a curio. So `locate` has to answer "I do not know"
when the panel is not a map, when the fit does not explain what is drawn, and
-- the one that is easiest to get wrong -- when the fit is perfect but the
destination is simply not on the map yet.
"""

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from bridge import mappanel                                     # noqa: E402
from bridge.dungeon_map import Area, DungeonMap, Tile           # noqa: E402

# xdist: this file's tests spike to +92 MB each while they decode frames.
# `--dist load` scatters them, so up to 16 spikes land at once and the suite's
# PEAK -- not its steady state -- is what crashes the machine. Grouped, at most
# one runs at a time. The four grouped files cost 12.9 s serially against a 74 s
# suite, so this buys memory without touching the critical path.
pytestmark = pytest.mark.xdist_group("memory_heavy")

CAPTURES = Path(__file__).resolve().parents[2] / "captures"
MAP_SHOT = CAPTURES / "02_starting_room_After_map_click.png"

pytestmark = pytest.mark.skipif(not MAP_SHOT.exists(),
                                reason="no map capture")


def _img(png):
    Image = pytest.importorskip("PIL.Image", reason="Pillow is dev-only")
    return np.asarray(Image.open(png).convert("RGB"), dtype=np.uint8)


@pytest.fixture(scope="module")
def frame():
    return _img(MAP_SHOT)


def _tile(index, pos):
    return Tile(index=index, content=0, curio_id=None, trap_id=None,
                mash_index=-1, knowledge=3, lit=True, mappos=pos)


def _area(aid, positions, knowledge=3):
    return Area(id=aid, is_room=aid.startswith("roo"), knowledge=knowledge,
                tiles=tuple(_tile(i, p) for i, p in enumerate(positions)))


@pytest.fixture
def tutorial():
    """The Old Road, from `scripts/starting_save/persist.map.json`.

    Written out rather than read, so this test says what it expects instead of
    agreeing with whatever the file happens to hold. `test_tutorial_map.py` is
    what checks the game still ships this.
    """
    return DungeonMap(
        entrance_id="rooA", final_room_id="rooB",
        areas={
            "rooA": _area("rooA", [(-10.5, -7.0)]),
            "coAB": _area("coAB", [(-9.5 + i, -7.0) for i in range(6)],
                          knowledge=1),
            "rooB": _area("rooB", [(-3.5, -7.0)], knowledge=1),
        })


# --- finding the drawn cells ---

def test_the_tutorial_panel_holds_exactly_two_rooms_and_four_tiles(frame):
    """Six cells, and the split is unmissable: rooms come out ~128 px across
    against 39 for a corridor tile. Only four of the corridor's six tiles are
    drawn -- the door tile at each end is swallowed by the room box next to
    it, which is why the fit scores itself on boxes and not on tiles."""
    boxes = mappanel.find_boxes(frame)
    assert len(boxes) == 6
    big = [b for b in boxes if min(b.size) > 60]
    small = [b for b in boxes if min(b.size) <= 60]
    assert len(big) == 2 and len(small) == 4
    assert all(35 <= min(b.size) <= 45 for b in small)


def test_a_cell_is_measured_against_its_own_rows_not_its_column(frame):
    """The second pass, and the reason it exists. Column runs alone give a
    vertical corridor the full width of whatever wide room shares its column,
    putting its centre tens of pixels sideways of where it is drawn -- and the
    centre IS the click. Every corridor tile here has to come back roughly
    square."""
    for box in mappanel.find_boxes(frame):
        w, h = box.size
        assert 0.7 < w / h < 1.5, box


def test_nothing_is_found_in_a_frame_that_is_not_a_map():
    assert mappanel.find_boxes(np.zeros((1440, 2560, 3), dtype=np.uint8)) == []
    assert mappanel.find_boxes(None) == []


# --- the fit ---

def test_the_transform_is_recovered_exactly(frame, tutorial):
    """48.0 px per map unit, and every drawn cell explained. The residual is
    12.3 px -- an unvisited room's box is drawn asymmetrically -- which is a
    quarter of one unit, so it cannot reach the neighbouring tile."""
    got = mappanel.fit(mappanel.find_boxes(frame), mappanel.tiles_of(tutorial))
    assert got is not None
    assert got.scale == pytest.approx(48.0, abs=0.5)
    assert got.matched == got.boxes == 6
    assert got.residual < mappanel.TOL_UNITS * got.scale


def test_the_recovered_grid_predicts_every_tile(frame, tutorial):
    """The check that the fit is the RIGHT one and not merely self-consistent:
    the four drawn corridor tiles are t1..t4, and the transform has to put
    them where they are, one unit apart, without ever having been told which
    box was which."""
    got = mappanel.fit(mappanel.find_boxes(frame), mappanel.tiles_of(tutorial))
    xs = [got.point((-9.5 + i, -7.0))[0] for i in range(6)]
    assert [round(x) for x in xs] == [1597, 1645, 1693, 1741, 1789, 1837]
    assert round(got.point((-10.5, -7.0))[0]) == 1549   # rooA
    assert round(got.point((-3.5, -7.0))[0]) == 1885    # rooB


def test_a_scale_bigger_than_the_closest_two_cells_is_impossible(frame,
                                                                 tutorial):
    """Tiles are one map unit apart and nothing is drawn closer, so no fit may
    claim a scale under which two of the cells it is fitting would overlap.
    That is a geometric impossibility, not a tuned threshold, and it is what
    rejects the spurious large-scale fits the vote otherwise finds."""
    boxes = mappanel.find_boxes(frame)
    closest = mappanel._closest_pair(boxes)
    assert closest == pytest.approx(48.0, abs=1.0)
    assert mappanel._score(boxes, mappanel.tiles_of(tutorial),
                           closest * 2) is None


def test_a_speck_is_not_a_cell_at_a_large_scale():
    """`_cells_for` resolves the circularity: how small is too small depends
    on the scale, so it is asked per candidate. A 3-pixel glyph cannot be a
    tile on a map drawn at 48 px per unit."""
    boxes = [mappanel.Box(0, 2, 0, 2), mappanel.Box(100, 140, 100, 140)]
    assert mappanel._cells_for(boxes, 48.0) == [boxes[1]]
    assert len(mappanel._cells_for(boxes, 5.0)) == 2


def test_a_frame_with_nothing_to_fit_returns_none(tutorial):
    assert mappanel.fit([], mappanel.tiles_of(tutorial)) is None
    assert mappanel.fit(mappanel.find_boxes(_img(MAP_SHOT)), []) is None


# --- the answer, and the refusals ---

def test_the_destination_room_resolves_to_its_own_box(frame, tutorial):
    """The whole point. `rooB` is the far room of the Old Road and the click
    that leaves `rooA`; it has to land inside the box the game drew for it."""
    point, why = mappanel.locate(frame, tutorial, "rooB")
    assert point is not None, why
    px, py = point[0] * frame.shape[1], point[1] * frame.shape[0]
    box = next(b for b in mappanel.find_boxes(frame) if b.x0 > 1800)
    assert box.contains(px, py)
    # The click is the fit's prediction clamped into the box since 2026-09-28
    # (a room merged with its corridor has its centre ON the corridor), so it
    # is near the box centre rather than on it.
    assert px == pytest.approx(1884, abs=15) and py == pytest.approx(1238, abs=15)


def test_the_answer_is_a_fraction_of_the_client_rect(frame, tutorial):
    """Every stored coordinate in `bridge/` is a fraction, because DD scales
    its 16:9 layout to fill the client area -- so a windowed game dragged
    mid-run still clicks the right place. A pixel here would be a pixel for one
    window size."""
    point, _ = mappanel.locate(frame, tutorial, "rooB")
    assert all(0.0 < v < 1.0 for v in point)
    half = frame[::2, ::2]
    shrunk, _ = mappanel.locate(half, tutorial, "rooB")
    if shrunk is not None:
        assert shrunk[0] == pytest.approx(point[0], abs=0.01)


def test_an_area_the_save_never_placed_is_refused(frame, tutorial):
    point, why = mappanel.locate(frame, tutorial, "rooZ")
    assert point is None and "no coordinates" in why


def test_a_room_that_is_not_drawn_yet_is_refused(frame, tutorial):
    """The refusal that is easiest to get wrong, and the most dangerous to
    miss. An unscouted room is simply not on the panel; the fit is still
    perfect, and the transform will happily predict a point for it. Clicking
    there puts the cursor on the panel's empty ground -- or worse, on the
    world showing behind it."""
    far = dict(tutorial.areas)
    far["rooQ"] = _area("rooQ", [(4.5, -7.0)], knowledge=1)
    grown = DungeonMap(entrance_id="rooA", final_room_id="rooB", areas=far)
    point, why = mappanel.locate(frame, grown, "rooQ")
    assert point is None
    assert "not drawn" in why and "predicted" in why


def test_an_inventory_frame_never_yields_a_click(tutorial):
    """`screen.panel()` is what decides map-versus-inventory and `act.py`
    checks it first, but this must not depend on that: the bag's 16 slots are a
    grid too, and a grid is exactly what the fit is looking for."""
    bag = CAPTURES / "trap.png"
    if not bag.exists():
        pytest.skip("no inventory capture")
    point, why = mappanel.locate(_img(bag), tutorial, "rooB")
    assert point is None, why


def test_a_black_frame_is_refused(tutorial):
    point, why = mappanel.locate(np.zeros((1440, 2560, 3), dtype=np.uint8),
                                 tutorial, "rooB")
    assert point is None and "nothing is drawn" in why


# --- what the save has to supply ---

def test_a_corridor_answers_with_its_middle_tile(tutorial):
    """Only diagnostics ask -- the game does not accept a corridor as a
    destination -- but answering with tile 0 would point at the doorway it
    shares with the room, which is the least distinguishable spot on it."""
    assert mappanel.area_point(tutorial, "coAB") == (-6.5, -7.0)
    assert mappanel.area_point(tutorial, "rooB") == (-3.5, -7.0)
    assert mappanel.area_point(tutorial, "nope") is None


def test_every_tile_of_every_captured_dungeon_has_a_coordinate():
    """`mappos` lives in `static_save`, a length-prefixed nested DSON buffer
    inside `persist.map.json` -- one decode further in than the reader used to
    go, which is why the bridge spent months believing the geometry was not in
    a live save at all. All three captured dungeons, every tile."""
    from tests._captures import decoded_maps
    seen = 0
    for path, dungeon in decoded_maps():
        tiles = [t for a in dungeon.areas.values() for t in a.tiles]
        if not tiles:
            continue
        seen += 1
        assert all(t.mappos is not None for t in tiles), path
    if not seen:
        pytest.skip("no captured maps")


def test_the_grid_is_contiguous_and_one_unit_per_tile():
    """What makes the whole fit possible: a corridor's tiles are exactly one
    unit apart, so the gap between two drawn cells IS the scale."""
    from tests._captures import decoded_maps
    checked = 0
    for _path, dungeon in decoded_maps():
        for area in dungeon.areas.values():
            pos = [t.mappos for t in area.tiles if t.mappos]
            if len(pos) < 2:
                continue
            checked += 1
            steps = {(round(b[0] - a[0], 1), round(b[1] - a[1], 1))
                     for a, b in zip(pos, pos[1:])}
            assert steps <= {(1.0, 0.0), (-1.0, 0.0), (0.0, 1.0), (0.0, -1.0)}, \
                (area.id, steps)
    if not checked:
        pytest.skip("no captured maps")


def test_a_coordinate_decodes_out_of_either_save_encoding():
    """DSON stores `mappos` as int32s that are float bit patterns; the plain
    text template stores real floats. The two are told apart by container, not
    by a magnitude heuristic -- `-1050673152` is `-14.0`."""
    from bridge.dungeon_map import _as_float
    assert _as_float(-1050673152) == pytest.approx(-14.0)
    assert _as_float(-14.0) == pytest.approx(-14.0)
    assert _as_float(None) is None


# ── a GENERATED dungeon, which refused for twelve days ──

RUINS_SHOT = CAPTURES / "03_ruins_map_panel.png"
RUINS_SAVE = (Path(__file__).resolve().parents[2]
              / "archive" / "traces" / "ruins_map_paired" / "persist.map.json")


@pytest.fixture(scope="module")
def ruins_frame():
    if not RUINS_SHOT.exists():
        pytest.skip("no paired Ruins capture")
    return _img(RUINS_SHOT)


@pytest.fixture(scope="module")
def ruins_map():
    """The save the frame was taken from, in the same second.

    This pairing is the whole point. `BRIDGE.md` recorded the module as "not
    yet validated on a full-size dungeon" precisely because the unpaired real
    captures could not be confirmed to belong to the raid whose save was being
    fitted -- so a bad fit and a mismatched pair were indistinguishable.
    """
    if not RUINS_SAVE.exists():
        pytest.skip("no paired Ruins save")
    from bridge.dungeon_map import read_dungeon_map                # noqa: PLC0415
    got = read_dungeon_map(RUINS_SAVE)
    if got is None:
        pytest.skip("Ruins save did not decode")
    return got


def test_a_generated_dungeon_fits(ruins_frame, ruins_map):
    """~7.7 px per unit, and nearly every drawn cell explained.

    A generated Ruins map is hub-and-spoke where the Old Road is a corridor:
    eight rooms at unit x in {1,8,15} and y in {1,8,15,22}, **seven units
    apart**, joined by six-tile corridors the panel draws too small for
    `find_boxes`. So every gap between two boxes is 7 units, and the scale is
    ~7.7 px/unit -- smaller than any previously recorded (48.0, 24.0, 15.3,
    10.7).

    Two things had to change before this could fit, and both were fixed
    numbers standing in for derived ones:

    * the divisors were a fixed `(1, 2, 3)`, so 7 was never offered and the
      true scale was never scored. They come off the save now -- a gap can
      only be a distance the map actually contains.
    * the candidate dedup was a flat 0.5 px/unit, which is 1% at 48 and
      **6.4%** here; it merged 7.43 and 7.71 and kept the first. Over the 22
      units from the entrance to the far room that is ~6 px of drift.
    """
    got = mappanel.fit(mappanel.find_boxes(ruins_frame),
                       mappanel.tiles_of(ruins_map))
    assert got is not None, "a generated dungeon must fit, not refuse"
    assert got.scale == pytest.approx(7.7, abs=0.3)
    assert got.boxes == 8
    assert got.matched >= 7
    assert got.explained >= mappanel.MIN_EXPLAINED
    assert got.residual < mappanel.TOL_UNITS * got.scale


def test_every_room_of_the_generated_dungeon_resolves(ruins_frame, ruins_map):
    """All eight rooms become a click, and inside the panel.

    `locate` refuses a destination that is not drawn, so this also says the
    eight boxes really are the eight rooms rather than a fit that merely
    scores well.
    """
    seen = {}
    for area_id in sorted(ruins_map.areas):
        if not area_id.startswith("roo"):
            continue
        point, note = mappanel.locate(ruins_frame, ruins_map, area_id)
        assert point is not None, f"{area_id} did not resolve: {note}"
        fx, fy = point
        assert 0.0 < fx < 1.0 and 0.0 < fy < 1.0
        # the panel is the right-hand third of the frame, low down
        assert fx > 0.5, f"{area_id} resolved outside the map panel"
        seen[area_id] = point
    assert len(seen) == 8
    assert len(set(seen.values())) == 8, "two rooms resolved to one point"


def test_the_generated_fit_preserves_the_save_s_own_geometry(ruins_frame,
                                                             ruins_map):
    """The check that the fit is RIGHT rather than self-consistent.

    The save says the rooms sit on a 7-unit grid. Whatever scale was
    recovered, two rooms 7 units apart in the save must land 7*scale apart on
    screen -- so this compares the drawing against the data instead of against
    the fit's own residual.
    """
    got = mappanel.fit(mappanel.find_boxes(ruins_frame),
                       mappanel.tiles_of(ruins_map))
    assert got is not None
    rooms = {a: ruins_map.areas[a].tiles[0].mappos
             for a in ruins_map.areas if a.startswith("roo")}
    # rooA (1,1) and rooB (8,1) are 7 apart in x and share a row
    (ax, ay), (bx, by) = rooms["rooA"], rooms["rooB"]
    assert (bx - ax, by - ay) == (7.0, 0.0)
    pa = got.point((ax, ay))
    pb = got.point((bx, by))
    assert pb[0] - pa[0] == pytest.approx(7.0 * got.scale, abs=2.0)
    assert pb[1] - pa[1] == pytest.approx(0.0, abs=2.0)


def test_the_party_token_mask_is_NOT_wired_in():
    """A retraction, kept as a test so the idea is not had twice.

    The party's orange token really can merge two cells --
    `captures/map_party_token_merge.png` shows `find_boxes` returning five
    boxes where six are drawn -- and masking it by hue really does recover the
    sixth. **Wiring that in made the route click the wrong box.** Across four
    live runs at one window origin:

        6/6 boxes explained -> clicked client (942, 618) = rooB    correct
        6/6 boxes explained -> clicked client (942, 618) = rooB    correct
        5/6 boxes explained -> clicked client (894, 624) = a cell  WRONG

    The third is with the mask. The detector was already explaining every box
    and aiming correctly; the mask split `rooA` into a room plus a phantom
    cell, one box then matched no tile, and the fit shifted by a cell.

    The banked frame is written **only on failure**, so it is a biased sample
    of one -- which is how "90 px is too wide for rooA" got believed off a
    brightened crop. A 90 px `rooA` is what that room measures.
    """
    import numpy as np
    from PIL import Image
    from bridge.mappanel import find_boxes, _without_party_token

    path = (Path(__file__).resolve().parents[2] / "captures"
            / "map_party_token_merge.png")
    if not path.exists():
        pytest.skip("the banked frame is not present")
    img = np.asarray(Image.open(path).convert("RGB"), dtype=np.uint8)

    # The shipped detector sees the merge, and that is DELIBERATE.
    assert len(find_boxes(img)) == 5

    # The mask still works; it is simply not applied.
    assert len(find_boxes(_without_party_token(img))) == 6


def test_masking_the_token_is_a_NO_OP_when_there_is_none():
    """Unchanged behaviour on a frame with no saturated orange, so the helper
    stays safe if anyone revisits the idea with better evidence."""
    import numpy as np
    from bridge.mappanel import _without_party_token

    grey = np.full((40, 40, 3), 90, dtype=np.uint8)
    assert np.array_equal(_without_party_token(grey), grey)
# --- picking WHICH scale, and refining it ---------------------------------
#
# Both of these come from one live failure: a Warrens frame with all seven
# rooms drawn was refused, the party sat in `rooC` for the whole play budget,
# and the router's complaint ("could not fit the map to the 23 cells drawn")
# named the symptom rather than either cause.


def _fit(scale, matched, boxes, residual):
    return mappanel.MapFit(scale=scale, ox=0.0, oy=0.0, matched=matched,
                           boxes=boxes, residual=residual)


def test_the_fraction_explained_outranks_a_smaller_residual():
    """Three candidates each matched 5 boxes on the live frame. The raw
    residual handed it to 6.0 px/unit matching 5 of 23 cells (residual 0.4)
    over 12.25 matching 5 of 7 (residual 4.0) -- and 12.25 is the correct
    reading, the one whose neighbourhood holds a 7-of-7 fit.

    The residual is in PIXELS and the tolerance is proportional to the scale,
    so a small scale gets small residuals for free and ranking on them prefers
    reading the map as tinier than it is.
    """
    tiny = _fit(scale=6.0, matched=5, boxes=23, residual=0.4)
    right = _fit(scale=12.25, matched=5, boxes=7, residual=4.0)
    assert mappanel._rank(right) > mappanel._rank(tiny)


def test_more_boxes_matched_still_wins_outright():
    """Explained is the tie-break, not the objective. A scale that accounts for
    more of the panel is better even at a worse fraction."""
    few = _fit(scale=12.0, matched=4, boxes=4, residual=0.1)
    many = _fit(scale=12.0, matched=7, boxes=9, residual=3.0)
    assert mappanel._rank(many) > mappanel._rank(few)


def _grid(scale, cells, size=None):
    """Boxes on an exact grid at `scale`, for the unit positions in `cells`."""
    half = (size or scale * 0.6) / 2.0
    out = []
    for ux, uy in cells:
        cx, cy = 100.0 + scale * ux, 200.0 + scale * uy
        out.append(mappanel.Box(int(cx - half), int(cx + half),
                                int(cy - half), int(cy + half)))
    return out


def test_the_refinement_finds_a_scale_BETWEEN_the_candidates():
    """`_candidate_scales` can only propose a scale some PAIR of drawn boxes
    implies, so the truth is offered only when two boxes sit an exact whole
    number of units apart and both are drawn. On the live frame the candidates
    were 11.50, 12.00 and 12.25 and the truth was 11.70-11.95: the best
    candidate matched 5 of 7 rooms, explained 0.71 against the 0.75 bar, and a
    frame with every room drawn was refused.
    """
    cells = [(0, 0), (2, 0), (4, 0), (0, 2), (2, 2), (4, 2), (2, 4)]
    truth = 11.83
    boxes = _grid(truth, cells)
    started = mappanel._score(boxes, cells, 12.25)
    assert started is not None
    got = mappanel._refine(boxes, cells, started)
    assert got.matched == len(cells), "the refinement bought no matches"
    assert abs(got.scale - truth) < 0.5


def test_the_refinement_never_walks_a_COMPLETE_fit():
    """It exists because the vote winner can MISS boxes; it has no business
    re-tuning one that already explains them all. Letting it shave the residual
    walked the tutorial's 48.0 px/unit to 50.4 -- a 5% error, a third of a cell
    over seven units, on a frame that was already fitted exactly."""
    cells = [(0, 0), (1, 0), (2, 0), (0, 1), (1, 1), (2, 1)]
    boxes = _grid(48.0, cells)
    exact = mappanel._score(boxes, cells, 48.0)
    assert exact is not None and exact.matched == len(cells)
    got = mappanel._refine(boxes, cells, exact)
    assert got.scale == exact.scale, "a complete fit was re-tuned"


def test_the_refinement_runs_BEFORE_the_acceptance_gates():
    """The whole point is that the vote winner can be a slightly-wrong scale
    that fails `MIN_EXPLAINED` while the true one passes. Refining afterwards
    would never run on the frames that need it."""
    import inspect
    src = inspect.getsource(mappanel.fit)
    assert src.index("_refine(") < src.index("MIN_EXPLAINED")


# --- minimum zoom: the view that shows the whole map, and could not be read ---

MINZOOM_SHOT = CAPTURES / "04_minzoom_map_panel.png"
MINZOOM_MAP = (Path(__file__).resolve().parents[2] / "archive" / "traces"
               / "minzoom_map_paired" / "persist.map.json")

minzoom = pytest.mark.skipif(not (MINZOOM_SHOT.exists() and MINZOOM_MAP.exists()),
                             reason="no minimum-zoom capture")


@pytest.fixture(scope="module")
def minzoom_frame():
    return _img(MINZOOM_SHOT)


@pytest.fixture(scope="module")
def minzoom_map():
    from bridge.dungeon_map import read_dungeon_map
    return read_dungeon_map(str(MINZOOM_MAP))


@minzoom
def test_the_whole_map_view_is_readable_at_last(minzoom_frame, minzoom_map):
    """THE FRAME THIS WORK EXISTS FOR, and it used to be REFUSED.

    Minimum zoom is the only view that shows the whole map, so it is where the
    router should stand -- and it was the one view `find_boxes` could not
    segment. Row runs were taken over a whole column span, so cells at
    different heights in different columns joined into one y-run and the panel
    came back as four full-height columns: 36x75, 11x75, 12x75 and 50x75,
    where a room here is 34 px and a hall is 12.

    Alternating the x and y tightening until nothing moves splits them, and the
    frame goes from a refusal to 12.4 px/unit with 16 of 19 cells explained.
    """
    boxes = mappanel.find_boxes(minzoom_frame)
    tall = sorted(b.size for b in boxes if b.size[1] >= 70)
    assert not tall, f"a full-height merge survived: {tall}"

    # ONE merge does survive, and it is the shape a projection provably cannot
    # undo: a room and a hall touching only at a corner come back as their
    # bounding box (50x63 here) whichever axis is cut. It is 5.1 units tall
    # against a room's 2.7, so `classify_size` calls it UNKNOWN and
    # `kind_agreement` excludes it -- the detector's blind spot costs a vote
    # rather than casting a wrong one.
    from bridge import mapicons
    corner = [b for b in boxes if b.size == (50, 63)]
    assert len(corner) == 1
    assert mapicons.classify_size(63, 12.4).kind == mapicons.UNKNOWN

    got = mappanel.fit(boxes, mappanel.tiles_of(minzoom_map),
                       mappanel.kinds_of(minzoom_map))
    assert got is not None, "the whole-map view must fit, not refuse"
    assert got.scale == pytest.approx(12.4, abs=0.6)
    assert got.explained >= mappanel.MIN_EXPLAINED
    assert got.agreement == 1.0, "every matched cell should be the right kind"


@minzoom
def test_every_room_of_the_live_map_resolves_at_minimum_zoom(minzoom_frame,
                                                             minzoom_map):
    """Seven rooms, seven distinct points, all inside the panel.

    `locate` refuses a destination that is not drawn, so this also says the
    boxes really are those rooms rather than a fit that merely scores well.
    """
    seen = {}
    for area_id, area in sorted(minzoom_map.areas.items()):
        if not area.is_room:
            continue
        point, note = mappanel.locate(minzoom_frame, minzoom_map, area_id)
        assert point is not None, f"{area_id} did not resolve: {note}"
        assert point[0] > 0.5, f"{area_id} resolved outside the map panel"
        seen[area_id] = point
    assert len(seen) == 7, sorted(seen)
    assert len(set(seen.values())) == 7, "two rooms resolved to one point"


@minzoom
def test_the_kind_constraint_refuses_a_map_that_is_not_this_one(minzoom_frame,
                                                                tutorial):
    """A transform is only as good as what it is fitted AGAINST.

    The tutorial's three areas can be laid over this frame's 19 cells in ways
    that score well on position alone -- that is the failure mode the whole
    layer suffers from. Requiring the drawn SIZES to agree with the save is a
    second, independent constraint, and it comes from `Area.is_room` rather
    than from anything measured off the picture.
    """
    boxes = mappanel.find_boxes(minzoom_frame)
    got = mappanel.fit(boxes, mappanel.tiles_of(tutorial),
                       mappanel.kinds_of(tutorial))
    assert got is None, f"the wrong map was accepted: {got and got.line()}"


@minzoom
def test_the_kind_constraint_is_OPT_IN(minzoom_frame, minzoom_map):
    """Omitting `tile_kinds` has to reproduce the old behaviour exactly, or
    every existing caller silently changes its answer."""
    boxes = mappanel.find_boxes(minzoom_frame)
    tiles = mappanel.tiles_of(minzoom_map)
    bare = mappanel.fit(boxes, tiles)
    assert bare is not None and bare.agreement == 1.0
    assert "kinds" not in bare.line()
    with_kinds = mappanel.fit(boxes, tiles, mappanel.kinds_of(minzoom_map))
    assert with_kinds is not None
    assert with_kinds.scale == pytest.approx(bare.scale)


@minzoom
def test_the_splitting_pass_cannot_MERGE_anything(minzoom_frame):
    """It refines what the three fixed passes returned and never coarsens it,
    which is what makes it safe to drop under the tutorial corpus: every old
    box is covered by one or more new ones."""
    from bridge import mappanel as mp
    original = mp._split
    try:
        mp._split = lambda lum, a, b, c, d, x0, y0, ink=None: [
            mp.Box(a + x0, b + x0, c + y0, d + y0)]
        before = mp.find_boxes(minzoom_frame)
    finally:
        mp._split = original
    after = mappanel.find_boxes(minzoom_frame)
    assert len(after) >= len(before)
    for box in after:
        assert any(o.x0 <= box.x0 and box.x1 <= o.x1
                   and o.y0 <= box.y0 and box.y1 <= o.y1 for o in before), box

def test_a_DIM_palette_still_fits():
    """A Cove map drawn with cell fill at 20-40 luminance: at INK 40 the
    rooms come out half-height, a corridor shreds into 4 px slabs and nothing
    fits. `locate` retries at INK_DIM, and the party's own room resolves onto
    the torch icon it is drawn under (client ~855, 587)."""
    from PIL import Image
    from bridge.dungeon_map import read_dungeon_map

    img = np.asarray(Image.open(CAPTURES / "map_dim_cove.png").convert("RGB"))
    dungeon = read_dungeon_map(str(CAPTURES / "map_dim_cove.map.json"))
    assert mappanel.fit(mappanel.find_boxes(img), mappanel.tiles_of(dungeon),
                        mappanel.kinds_of(dungeon)) is None
    point, why = mappanel.locate(img, dungeon, "rooL")
    assert point is not None, why
    assert abs(point[0] * 1280 - 855) < 8 and abs(point[1] * 720 - 587) < 8



def test_a_SPARSE_entrance_map_is_anchored_on_the_party_token():
    """Live 2026-09-25, a Ruins entrance: three rooms drawn, and the vote put
    the whole map 14 x 7 units off -- rooL, the party's own room, predicted at
    (1003, 660) with the token at (856, 587) -- so rooH, straight up, read as
    "not drawn" and the party sat in its first room for two iterations.
    Anchored on the token (the save says the party is in rooL) the same frame
    resolves rooH onto the clipped box above and rooK onto the room left."""
    from PIL import Image
    from bridge.dungeon_map import read_dungeon_map

    img = np.asarray(Image.open(CAPTURES / "map_anchor_entrance.png").convert("RGB"))
    dungeon = read_dungeon_map(str(CAPTURES / "map_anchor_entrance.map.json"))
    tok = mappanel.party_token(img)
    assert tok is not None and abs(tok[0] - 856) < 4 and abs(tok[1] - 586) < 4
    # the old reading: the fit is confidently wrong and rooH is "not drawn"
    assert mappanel.locate(img, dungeon, "rooH")[0] is None
    point, why = mappanel.locate(img, dungeon, "rooH", here="rooL")
    assert point is not None, why
    assert abs(point[0] * 1280 - 856) < 8 and abs(point[1] * 720 - 513) < 8
    point, why = mappanel.locate(img, dungeon, "rooK", here="rooL")
    assert point is not None and abs(point[0] * 1280 - 787) < 8, why


def test_an_unanchored_fit_that_misplaces_the_party_is_refused():
    """When the anchored fit cannot be made, the fallback must still put the
    party's room on the token -- otherwise it is clicking into a map it has
    placed wrong."""
    from bridge.dungeon_map import read_dungeon_map
    from PIL import Image
    img = np.asarray(Image.open(CAPTURES / "map_anchor_entrance.png").convert("RGB"))
    dungeon = read_dungeon_map(str(CAPTURES / "map_anchor_entrance.map.json"))
    # Force the anchored fit to fail and the vote to run.
    orig = mappanel.fit
    try:
        mappanel.fit = (lambda b, t, k=None, anchor=None, scale_hint=None:
                        None if anchor is not None else orig(b, t, k))
        point, why = mappanel.locate(img, dungeon, "rooB", here="rooL")
    finally:
        mappanel.fit = orig
    assert point is None and "party token" in why, why


def test_a_red_room_icon_is_not_the_party_token():
    """Live 2026-09-26: a red fight-room icon was read as the party's torch
    token, so the party-on-token check refused a CORRECT fit and the loop
    stood in a crypts entrance for three iterations. The torch is orange
    (green/red ~0.47), the icon red (~0.21)."""
    from PIL import Image
    from bridge.dungeon_map import read_dungeon_map

    img = np.asarray(Image.open(CAPTURES / "map_red_icon.png").convert("RGB"))
    dungeon = read_dungeon_map(str(CAPTURES / "map_red_icon.map.json"))
    tok = mappanel.party_token(img)
    assert tok is not None and abs(tok[0] - 855) < 6 and abs(tok[1] - 585) < 6
    point, why = mappanel.locate(img, dungeon, "rooB", here="rooA")
    assert point is not None, why
    assert abs(point[0] * 1280 - 929) < 8 and abs(point[1] * 720 - 584) < 8


def test_a_gold_treasure_icon_is_not_the_party_token():
    """Live 2026-09-26, the second decoy: a treasure room's gold icon is orange
    enough to pass the colour gate, and the densest-cluster rule picked it
    over the torch. The panel opens centred on the party, so the cluster
    nearest that home point is the token."""
    from PIL import Image
    from bridge.dungeon_map import read_dungeon_map

    img = np.asarray(Image.open(CAPTURES / "map_gold_icon.png").convert("RGB"))
    dungeon = read_dungeon_map(str(CAPTURES / "map_gold_icon.map.json"))
    tok = mappanel.party_token(img)
    assert tok is not None and abs(tok[0] - 856) < 6 and abs(tok[1] - 586) < 6
    point, why = mappanel.locate(img, dungeon, "rooJ", here="rooJ")
    assert point is not None, why


def test_a_room_clipped_by_the_panel_edge_is_clicked_where_it_shows(monkeypatch):
    """Live 2026-09-27 (Cove): rooI predicted at y 696, the panel ending at 695
    and the top of the room drawn above it; "not drawn" on every poll."""
    import numpy as np
    from types import SimpleNamespace
    from bridge import mappanel as mp
    img = np.zeros((720, 1280, 3), np.uint8)
    x0, x1, y0, y1 = mp.panel_rect(1280, 720)
    here = mp.Box(840, 874, 570, 604)                   # a full room
    clipped = mp.Box(840, 874, 680, y1)                 # its bottom cut off
    fake = SimpleNamespace(scale=14.0, point=lambda _t: (857.0, 696.0),
                           line=lambda: "fake fit")
    monkeypatch.setattr(mp, "find_boxes", lambda *a, **k: [here, clipped])
    monkeypatch.setattr(mp, "area_point", lambda d, a: (0.0, 0.0))
    monkeypatch.setattr(mp, "fit", lambda *a, **k: fake)
    monkeypatch.setattr(mp, "tiles_of", lambda d: [])
    monkeypatch.setattr(mp, "kinds_of", lambda d: [])
    point, why = mp.locate(img, object(), "rooI")
    assert point is not None, why
    assert abs(point[0] * 1280 - 857) < 2 and y0 < point[1] * 720 <= y1
    # ...and a prediction far off the panel is still refused.
    fake2 = SimpleNamespace(scale=14.0, point=lambda _t: (857.0, 760.0),
                            line=lambda: "fake fit")
    monkeypatch.setattr(mp, "fit", lambda *a, **k: fake2)
    assert mp.locate(img, object(), "rooI")[0] is None


def test_a_room_clipped_by_the_TOP_edge_is_found_in_the_grid_strip():
    """Live 2026-09-28: rooC predicted at y 499 while the panel region starts
    at 510 and the grid is drawn from 491; the raid stalled in rooH. The top
    strip holds rooC's box, split by its icon."""
    from PIL import Image
    from bridge.dungeon_map import read_dungeon_map

    img = np.asarray(Image.open(CAPTURES / "map_top_clipped.png").convert("RGB"))
    dungeon = read_dungeon_map(str(CAPTURES / "map_top_clipped.map.json"))
    point, why = mappanel.locate(img, dungeon, "rooC", here="rooH")
    assert point is not None, why
    assert "top edge" in why
    assert abs(point[0] * 1280 - 877) < 10 and point[1] * 720 < 510


def test_a_room_merged_with_its_corridor_is_clicked_on_the_room():
    """Live 2026-09-28: rooI and coFI came out as one tall box whose centre is
    on the corridor, and the raid stalled in rooF clicking it."""
    from PIL import Image
    from bridge.dungeon_map import read_dungeon_map

    img = np.asarray(Image.open(CAPTURES / "map_rooF_offered_miss.png").convert("RGB"))
    dungeon = read_dungeon_map(str(CAPTURES / "map_rooF_offered_miss.map.json"))
    point, why = mappanel.locate(img, dungeon, "rooI", here="rooF")
    assert point is not None, why
    assert abs(point[0] * 1280 - 857) < 6 and abs(point[1] * 720 - 656) < 6
