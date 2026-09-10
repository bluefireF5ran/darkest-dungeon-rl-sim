"""Reading the map panel against the art the game ships.

The map layer's whole difficulty is that a transform can be confident and
wrong: land every detected box near *some* tile and the fit scores well
whatever it actually named. Three raids were lost to exactly that, and the
repair is a second constraint that comes from a different place -- the panel
draws a room at 64x64 and a hallway tile at 24x24, so a cell's SIZE says which
it is, and `Area.is_room` in the save says the same thing independently.

These tests pin the two halves that would rot silently: the shipped sizes (if
the game repacks its icons, every ratio here is wrong rather than merely
stale), and the scoring rule that makes the constraint usable -- per BOX and
not per tile, which is the difference between 1.00 and 0.46 on the same
correct fit.
"""

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from bridge import mapicons                                     # noqa: E402
from env.gym_env import GAME_DIR                                # noqa: E402

# Deliberately NOT xdist_group("memory_heavy"), unlike `test_mappanel.py` next
# door. That file decodes 2560x1440 captures and spikes +92 MB each; this one
# loads 27 sprites of 64x64 and smaller. Measured peak for the whole file:
# 63 MB. Pinning it would cost every test in it a place in the serial queue
# and buy nothing -- `test_suite_memory.py` enforces that trade.
GAME = str(GAME_DIR)
ICONS = mapicons.icon_dir(GAME_DIR)

needs_game = pytest.mark.skipif(not ICONS.is_dir(),
                                reason="no game install")


# --- what the game ships ---

@needs_game
def test_the_shipped_sprites_are_64_and_24():
    """THE 8:3 RATIO IS THE MECHANISM, so it has to be measured, not assumed.

    The panel scales one sprite sheet by a single zoom factor, which is why
    `room_px / hall_px` is the same number at every zoom and on every window
    size. If this fails, `classify_size` is calibrated against a sheet the
    game no longer draws.
    """
    icons = mapicons.load_icons(GAME)
    rooms = mapicons.room_icons(GAME)
    halls = mapicons.hall_icons(GAME)
    assert rooms and halls, sorted(icons)
    assert {i.art_px for i in rooms.values()} == {mapicons.ROOM_ART_PX}
    assert {i.art_px for i in halls.values()} == {mapicons.HALL_ART_PX}
    assert mapicons.ROOM_OVER_HALL == pytest.approx(8 / 3)


@needs_game
def test_the_room_and_hall_sets_are_the_ones_the_panel_draws():
    """A misfiled sprite is a silent miscalibration rather than an error."""
    halls = set(mapicons.hall_icons(GAME))
    assert halls == {"hall_clear", "hall_dim", "hall_dark", "hall_door"}
    rooms = set(mapicons.room_icons(GAME))
    for stem in ("room_empty", "room_battle", "room_curio", "room_treasure",
                 "room_entrance", "room_boss", "room_unknown"):
        assert stem in rooms, sorted(rooms)


@needs_game
def test_the_party_token_is_not_a_cell():
    """`indicator.png` is 51x48 and drawn OVER the grid. Classified by size it
    would read as a room and vote for a transform that is one cell out."""
    icons = mapicons.load_icons(GAME)
    assert "indicator" in icons
    for stem in mapicons.OVERLAY_ICONS:
        assert stem not in mapicons.room_icons(GAME)
        assert stem not in mapicons.hall_icons(GAME)


def test_a_missing_install_degrades_rather_than_raising():
    """This module is an EXTRA constraint on the fit. A bridge that cannot
    read the art has to fall back on position alone, not stop."""
    assert mapicons.load_icons(str(Path(__file__).parent / "nope")) == {}
    assert mapicons.classify(np.zeros((8, 8)), mapicons.CellKind("room"),
                             "nowhere") == mapicons.CellKind("room")


# --- the size rule ---

@pytest.mark.parametrize("scale,room_px,hall_px", [
    (48.0, 129, 39),      # the tutorial capture
    (15.3, 41, 13),       # a mid-zoom live frame
    (12.4, 34, 12),       # captures/04_minzoom_map_panel.png
])
def test_the_measured_cells_of_real_frames_classify(scale, room_px, hall_px):
    """Every room/hall pair this project has measured, at three zooms.

    These are the numbers `SIZE_TOLERANCE` is set by, so the test is what says
    the band still covers them.
    """
    assert mapicons.classify_size(room_px, scale).kind == mapicons.ROOM
    assert mapicons.classify_size(hall_px, scale).kind == mapicons.HALL


def test_the_two_predictions_come_off_the_SPRITE_SHEET_not_a_curve_fit():
    """A hall sprite IS the tile pitch, so both sizes follow from 24 and 64.

    Against the four banked frames the rooms measure 2.688, 2.680, 2.742 and
    2.727 units and the halls 0.812, 0.850 and 0.968 -- and the sheet predicts
    64/24 and 21/24 with nothing tuned. That agreement is the evidence this
    module rests on, so it is a test rather than a comment.
    """
    assert mapicons.ROOM_UNITS == pytest.approx(64 / 24)
    assert mapicons.HALL_UNITS == pytest.approx(21 / 24)
    for scale, room, hall in ((48.0, 129, 39), (15.3, 41, 13), (12.4, 34, 12)):
        assert room / scale == pytest.approx(mapicons.ROOM_UNITS, rel=0.05)
        assert hall / scale == pytest.approx(mapicons.HALL_UNITS, rel=0.15)


def test_two_merged_hall_TILES_are_refused_rather_than_called_a_room():
    """THE GAP BETWEEN THE BANDS IS WHY THERE ARE TWO TOLERANCES.

    A projection cannot separate two cells that touch only at a corner, so the
    detector does hand out merges. One pitch plus a tile is ~1.88 units, and
    calling that a room is a vote for a transform one cell out; UNKNOWN is
    excluded from `kind_agreement` instead, which is the safe direction.
    """
    scale = 12.4
    merged = scale * (1.0 + mapicons.HALL_UNITS)
    assert mapicons.classify_size(merged, scale).kind == mapicons.UNKNOWN
    assert mapicons.classify_size(0, scale).kind == mapicons.UNKNOWN
    assert mapicons.classify_size(30, None).kind == mapicons.UNKNOWN


def test_the_absolute_rule_needs_no_second_cell_type_on_screen():
    """THE REASON `classify_boxes` IS NOT USED INSIDE THE FIT.

    An unscouted generated dungeon draws its rooms and no corridor at all --
    `captures/03_ruins_map_panel.png` is eight rooms and nothing else. The
    relative rule takes the smallest cluster as the hall size and calls all
    eight halls; the absolute rule has the scale and calls all eight rooms.
    """
    rooms_only = [22, 21, 21, 20, 21, 22, 20, 21]
    relative = mapicons.classify_boxes(rooms_only)
    assert all(k.kind == mapicons.HALL for k in relative)
    absolute = [mapicons.classify_size(s, 7.7) for s in rooms_only]
    assert all(k.kind == mapicons.ROOM for k in absolute), absolute


def test_the_relative_rule_works_when_both_types_are_drawn():
    """It is still the right answer with no scale in hand -- which is what a
    diagnostic printing a panel it has no save for has."""
    sizes = [34, 34, 12, 11, 13, 12, 34]
    kinds = mapicons.classify_boxes(sizes)
    assert [k.kind for k in kinds] == ["room", "room", "hall", "hall",
                                       "hall", "hall", "room"]


# --- the agreement score ---

def test_an_unknown_observation_is_excluded_not_counted_wrong():
    """A cell the size rule could not call is a cell this constraint has no
    opinion about. Scoring it as a disagreement would penalise a correct fit
    for the detector's blind spots -- and the detector has several."""
    pairs = [(mapicons.CellKind("room"), "room"),
             (mapicons.CellKind("unknown"), "hall"),
             (mapicons.CellKind("unknown"), "room")]
    assert mapicons.kind_agreement(pairs) == 1.0
    assert mapicons.kind_agreement([]) == 1.0


def test_disagreement_is_measured_not_forgiven():
    pairs = [(mapicons.CellKind("room"), "hall"),
             (mapicons.CellKind("hall"), "hall")]
    assert mapicons.kind_agreement(pairs) == 0.5


def test_expected_kinds_reads_the_SAVE_and_not_the_pixels():
    """The labelled half of the correspondence. `Area.is_room` is the save's
    own statement, so this needs no heuristic and cannot drift with zoom."""
    class _Area:
        def __init__(self, is_room):
            self.is_room = is_room

    class _Map:
        areas = {"rooA": _Area(True), "coAB": _Area(False)}

    assert mapicons.expected_kinds(_Map()) == {"rooA": "room", "coAB": "hall"}


# --- the subtype, which is allowed to refuse ---

@needs_game
def test_a_sprite_matches_ITSELF_at_full_size():
    """The floor case for the correlation. If a 64x64 room does not match its
    own file, nothing downscaled will."""
    icons = mapicons.room_icons(GAME)
    battle = icons["room_battle"].grey
    stem, score = mapicons.match(battle, icons)
    assert stem == "room_battle"
    assert score > 0.99


@needs_game
def test_a_subtype_is_refused_when_the_pixels_do_not_support_one():
    """`classify` may only ADD information. A room whose subtype cannot be
    read is still a room, and a confident wrong label is worse here than no
    label -- the subtype is what a caller would use to cross-check a tile's
    `content` against the save."""
    noise = np.zeros((17, 17), dtype=float)
    noise[::2, ::2] = 255.0
    got = mapicons.classify(noise, mapicons.CellKind(mapicons.ROOM), GAME)
    assert got.kind == mapicons.ROOM
    assert got.subtype is None


@needs_game
def test_a_flat_patch_carries_no_information():
    flat = np.full((16, 16), 42.0)
    assert mapicons.match(flat, mapicons.room_icons(GAME)) == (None, 0.0)


@needs_game
def test_every_content_the_save_can_carry_names_a_sprite_that_exists():
    """`CONTENT_ROOM_ICON` and `CONTENT_MARKER` are a binding from the save's
    numbers to file stems, and a stem that does not exist is a lookup that
    silently returns nothing."""
    icons = mapicons.load_icons(GAME)
    for table in (mapicons.CONTENT_ROOM_ICON, mapicons.CONTENT_MARKER):
        for content, stem in table.items():
            assert stem in icons, (content, stem)


def test_the_content_tables_cover_what_dungeon_map_defines():
    """Every content constant is either a room sprite or a corridor marker.

    Trap, obstacle and hunger are corridor events with no room art of their
    own, so they appear in exactly one of the two tables -- and that asymmetry
    is the thing a reader gets wrong.
    """
    from bridge import dungeon_map
    known = set(dungeon_map.CONTENT_KINDS)
    covered = set(mapicons.CONTENT_ROOM_ICON) | set(mapicons.CONTENT_MARKER)
    assert known == covered, known ^ covered
    corridor_only = set(mapicons.CONTENT_MARKER) - set(mapicons.CONTENT_ROOM_ICON)
    assert corridor_only == {dungeon_map.CONTENT_TRAP,
                             dungeon_map.CONTENT_OBSTACLE,
                             dungeon_map.CONTENT_HUNGER}


if __name__ == "__main__":
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            try:
                _fn()
            except TypeError:
                continue            # parametrised
            print(f"ok {_name}")
