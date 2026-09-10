"""The town's geometry is DERIVED, and these are the checks that it is right.

`bridge/townlayout.py` reads Darkest Dungeon's own UI layout files instead of
measuring ten building panels off screenshots. That is only worth anything if
the reading is correct, and a derivation cannot check itself -- so this file
compares it against the coordinates in `bridge/layout.py` that were obtained
some other way entirely (a human clicking, or an edge/hue detector run over a
capture at 2560x1440).

**Three independent agreements, each testing a different thing:**

1. `MODAL_OK` -- a two-term composition inside one file, and the one that
   establishes that `confirm_dialog_layout.base_pos` is centre-anchored in x.
2. the inventory pitch -- a number two SEPARATE files state, and which a third
   source (the edge-detected raid pack) reproduces.
3. `TOWN_CLOSE_PANEL` -- a THREE-term composition, screen -> panel -> widget,
   which is the rule every accessor in the module depends on and which
   neither of the other two exercises.

If these ever stop agreeing, the module is reading the files wrongly and every
coordinate it produces is suspect -- which is why they are tests and not a
comment. The failure mode being guarded is not a crash: it is a click that
lands somewhere plausible and wrong.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest

from bridge import townlayout as TL
from bridge.layout import (INVENTORY_COLS, INVENTORY_X, INVENTORY_Y, MODAL_OK,
                           TOWN_CLOSE_PANEL, load as load_layout, play_rect)

from data.paths import GAME_DIR

pytestmark = pytest.mark.skipif(not GAME_DIR.exists(),
                                reason="needs the game install")


def _design(frac_pt):
    """A fraction pair back to 1920x1080 design px, for readable assertions."""
    return (frac_pt[0] * TL.DESIGN_W, frac_pt[1] * TL.DESIGN_H)


# -- the three cross-checks ----------------------------------------------

def test_the_derived_modal_answer_matches_the_measured_one():
    """`MODAL_OK` was edge-detected at (1262, 779) of 2560x1440. The dialog
    file composes to the same place from `base_pos + answers_offset +
    start_offset + half a button`."""
    derived = _design(TL.derived_modal_answer(0, GAME_DIR))
    measured = _design(MODAL_OK)
    assert derived == pytest.approx((948.0, 589.0))
    assert measured == pytest.approx((946.5, 584.2), abs=0.1)
    assert abs(derived[0] - measured[0]) <= 2.0
    assert abs(derived[1] - measured[1]) <= 6.0


def test_the_derived_column_pitch_is_the_measured_pack_pitch_exactly():
    """Three sources for one number.

    The raid pack's cell block was edge-detected at 1306.0..2159.3 of 2560
    across 8 columns -- 106.66 px, which is 80.0 in the design space. The game
    states the same 80 in `provision_store_grid_layout.offset`, and explains
    it in `inventory_item_layout.icon_size` (72 wide plus an 8 px gutter).
    """
    col_pitch, icon_h = TL.derived_item_pitch(GAME_DIR)
    x0, x1 = INVENTORY_X
    measured = (x1 - x0) * 2560 / INVENTORY_COLS * (TL.DESIGN_W / 2560)
    assert measured == pytest.approx(80.0, abs=0.05)
    assert col_pitch == pytest.approx(measured, abs=0.05)
    # The row pitch is the icon height plus its own gutter, and is a looser
    # agreement because the gutter is not stated anywhere.
    row_measured = (INVENTORY_Y[1] - INVENTORY_Y[0]) * 1440 * (TL.DESIGN_H / 1440)
    assert row_measured == pytest.approx(154.5, abs=0.5)
    assert 0 < row_measured - icon_h <= 12.0, "an implausible row gutter"


def test_the_derived_panel_close_matches_the_hue_measured_one():
    """The three-term composition, and the one that pinned the anchor.

    `TOWN_CLOSE_PANEL` is the ACTIVITY LOG's X, isolated by hue off
    `captures/screens/town.png`. Composing `town_screen_layout.activity_log_pos
    + activity_log_layout.close_pos + CLOSE_GLYPH_CENTRE` reproduces it -- and
    the same `close_pos` value appears in `building_base_layout`, which is why
    one offset serves every panel in the game.
    """
    derived = TL.derived_panel_close(GAME_DIR)
    assert derived[0] == pytest.approx(TOWN_CLOSE_PANEL[0], abs=0.002)
    assert derived[1] == pytest.approx(TOWN_CLOSE_PANEL[1], abs=0.002)


def test_a_building_panel_closes_at_the_same_point_as_the_activity_log():
    """Not a coincidence to be preserved by luck: DD draws every panel's X at
    one screen position, which is what makes `building_close()` one point for
    all ten buildings rather than ten measurements."""
    assert TL.building_close(GAME_DIR) == pytest.approx(
        TL.derived_panel_close(GAME_DIR))


# -- the navigation strip -------------------------------------------------

def test_all_ten_buildings_resolve_and_are_ordered_down_the_strip():
    pts = [TL.building_button(n, GAME_DIR) for n in TL.BUILDINGS]
    assert all(p is not None for p in pts), TL.BUILDINGS
    xs = {round(p[0], 6) for p in pts}
    assert len(xs) == 1, "the strip is one column"
    ys = [p[1] for p in pts]
    assert ys == sorted(ys), "button_index must run down the screen"
    gaps = [b - a for a, b in zip(ys, ys[1:])]
    assert max(gaps) - min(gaps) < 1e-9, "evenly spaced by button_spacing"


def test_an_unknown_building_yields_no_point():
    """A typo must produce nothing, not the first button. Same discipline as
    `--ban-class` validating against the real class list: a silent fallback
    would look like a working run that clicked the wrong shop."""
    assert TL.building_button("stagecoach", GAME_DIR) is None
    assert TL.building_button("", GAME_DIR) is None


# -- every accessor lands on screen --------------------------------------

def _all_points():
    out = {}
    for name in TL.BUILDINGS:
        out[f"building:{name}"] = TL.building_button(name, GAME_DIR)
    out["close"] = TL.building_close(GAME_DIR)
    for t in range(3):
        for n in range(2):
            out[f"upgrade:{t}.{n}"] = TL.upgrade_node(t, n, GAME_DIR)
    for i in range(4):
        out[f"recruit:{i}"] = TL.recruit_slot(i, GAME_DIR)
        out[f"party:{i}"] = TL.party_slot(i, GAME_DIR)
    for i in range(8):
        out[f"roster:{i}"] = TL.roster_slot(i, GAME_DIR)
    for i in range(14):
        out[f"shop:{i}"] = TL.shop_slot(i, GAME_DIR)
        out[f"pack:{i}"] = TL.party_pack_slot(i, GAME_DIR)
    for i in range(4):
        out[f"from:{i}"] = TL.exchange_from(i, GAME_DIR)
        out[f"to:{i}"] = TL.exchange_to(i, GAME_DIR)
        out[f"confirm:{i}"] = TL.exchange_confirm(i, GAME_DIR)
        out[f"up:{i}"] = TL.exchange_amount(i, True, GAME_DIR)
        out[f"down:{i}"] = TL.exchange_amount(i, False, GAME_DIR)
    return out


def test_every_derived_point_resolves():
    missing = [k for k, v in _all_points().items() if v is None]
    assert not missing, f"unresolved: {missing}"


def test_every_derived_point_is_on_screen():
    """A fraction outside [0,1] is off the window entirely -- the tell for a
    composition that used the wrong base."""
    bad = {k: v for k, v in _all_points().items()
           if not (0.0 <= v[0] <= 1.0 and 0.0 <= v[1] <= 1.0)}
    assert not bad, bad


@pytest.mark.parametrize("size", [(2560, 1440), (1920, 1080), (1280, 720),
                                  (3440, 1440), (2560, 1600)])
def test_every_point_lands_inside_the_play_rect_at_any_aspect(size):
    """The whole reason these are fractions: DD insets its picture on a
    non-16:9 client, and `play_rect` is the correction `bridge/layout.py`
    already applies. An ultrawide (3440x1440) and a 16:10 are both real
    monitors, and a point that only works at 16:9 is a point that will
    misclick on someone's desk.
    """
    ox, oy, w, h = play_rect(*size, 16 / 9)
    for name, pt in _all_points().items():
        x, y = ox + pt[0] * w, oy + pt[1] * h
        assert 0 <= x <= size[0], (name, size, x)
        assert 0 <= y <= size[1], (name, size, y)


# -- the grids ------------------------------------------------------------

def test_the_shop_grid_wraps_at_seven_and_the_pack_at_eight():
    """Row-major, and the two grids have DIFFERENT widths -- 7 columns of
    stock against 8 of pack. Reading one with the other's stride puts every
    purchase after the seventh on the wrong row."""
    shop = [TL.shop_slot(i, GAME_DIR) for i in range(16)]
    assert shop[0][1] == shop[6][1], "the first seven share a row"
    assert shop[7][1] > shop[6][1], "the eighth wraps"
    assert shop[7][0] == pytest.approx(shop[0][0])
    pack = [TL.party_pack_slot(i, GAME_DIR) for i in range(16)]
    assert pack[0][1] == pack[7][1], "the first eight share a row"
    assert pack[8][1] > pack[7][1]
    assert pack[8][0] == pytest.approx(pack[0][0])


def test_the_party_slots_run_front_to_back_left_to_right():
    """Order IS the formation. `embark.py` returns a ranked party and dropping
    it in any other order builds a different comp."""
    pts = [TL.party_slot(i, GAME_DIR) for i in range(4)]
    xs = [p[0] for p in pts]
    assert xs == sorted(xs)
    assert len({round(p[1], 6) for p in pts}) == 1, "one row"
    assert TL.party_slot(4, GAME_DIR) is None
    assert TL.party_slot(-1, GAME_DIR) is None


def test_the_roster_stops_at_the_panels_own_element_max():
    """`element_max` is 8 and the list scrolls. A ninth index is not on screen,
    and returning a point below the panel would drop a recruit onto the
    Hamlet."""
    assert TL.roster_slot(7, GAME_DIR) is not None
    assert TL.roster_slot(8, GAME_DIR) is None
    assert TL.roster_slot(-1, GAME_DIR) is None


# -- failing safe ---------------------------------------------------------

def test_a_missing_install_yields_no_points_rather_than_wrong_ones():
    """`_blocks` swallows the read and every accessor returns None. An absent
    coordinate refuses; a defaulted one clicks the top-left of the screen."""
    nowhere = Path("Z:/no/such/darkest/dungeon")
    assert TL.building_button("stage_coach", nowhere) is None
    assert TL.building_close(nowhere) is None
    assert TL.upgrade_node(0, 0, nowhere) is None
    assert TL.recruit_slot(0, nowhere) is None
    assert TL.party_slot(0, nowhere) is None
    assert TL.shop_slot(0, nowhere) is None
    assert TL.exchange_from(0, nowhere) is None
    assert TL.derived_item_pitch(nowhere) is None


@pytest.mark.parametrize("accessor", [
    TL.recruit_slot, TL.roster_slot, TL.party_slot, TL.shop_slot,
    TL.party_pack_slot, TL.exchange_from, TL.exchange_to,
    TL.exchange_confirm, TL.derived_modal_answer,
])
def test_a_NEGATIVE_index_is_refused_by_every_accessor(accessor):
    """The bug `tests/bridge/test_act_town.py` found, pinned at the source.

    Negative index arithmetic is perfectly valid arithmetic: `start_pos +
    spacing * -1` is a real point one row ABOVE the panel, and every accessor
    here returned it happily. `recruit_slot(-1)` therefore produced a plan
    instead of a refusal -- on screen, a drag beginning on whatever the Hamlet
    draws up there.
    """
    assert accessor(-1, GAME_DIR) is None
    assert accessor(-99, GAME_DIR) is None


def test_a_BOOL_index_is_refused():
    """`True` is an int and `slot(True)` is slot 1. That is the shape of a
    mis-ordered keyword argument, and it would otherwise silently work --
    `exchange_amount(index, up)` takes an int then a bool, which is exactly
    the pair to get the wrong way round."""
    assert TL.recruit_slot(True, GAME_DIR) is None
    assert TL.party_slot(False, GAME_DIR) is None
    assert TL.exchange_amount(True, True, GAME_DIR) is None


def test_exchange_amount_guards_its_index_too():
    """Not covered by the parametrised sweep above because it takes a second
    argument; it is the accessor most likely to be called with its two
    arguments swapped."""
    assert TL.exchange_amount(-1, True, GAME_DIR) is None
    assert TL.exchange_amount(0, True, GAME_DIR) is not None


def test_upgrade_node_guards_BOTH_of_its_indices():
    assert TL.upgrade_node(-1, 0, GAME_DIR) is None
    assert TL.upgrade_node(0, -1, GAME_DIR) is None
    assert TL.upgrade_node(0, 0, GAME_DIR) is not None


def test_add_refuses_a_partial_composition():
    """The rule that makes the above true. Treating a missing offset as (0,0)
    does not produce no point -- it produces a point in the wrong place."""
    assert TL._add((1.0, 2.0), (3.0, 4.0)) == (4.0, 6.0)
    assert TL._add((1.0, 2.0), None, (3.0, 4.0)) is None


def test_xy_reads_both_separators_and_drops_a_third_component():
    """`DarkestParser` hands multi-value numerics back as one string, and the
    town files use spaces AND tabs. A `.pos` with three components is
    x/y/depth; the depth is a render order, not a coordinate."""
    assert TL._xy("172 259") == (172.0, 259.0)
    assert TL._xy("0\t0") == (0.0, 0.0)
    assert TL._xy("315\t -5 -3") == (315.0, -5.0)
    assert TL._xy("550  10 -1") == (550.0, 10.0)
    assert TL._xy("128") is None
    assert TL._xy("easeInSine") is None
    assert TL._xy(7) is None


def test_nothing_in_townlayout_sends_anything():
    """Pure geometry, like `bridge/layout.py`. If this module ever grows a
    backend, the refusal discipline moves out of `act.py` -- which has tests
    for it -- and into a file that does not.

    Checked on the IMPORT GRAPH rather than on the text: the module's own
    docstring says the words "backend" and "no game state", so a substring
    grep fails on its own documentation. That is the same class of mistake as
    a `.effect ""` truthiness test, and the lesson is the same -- assert
    against structure, not against prose.
    """
    import ast
    mod = ast.parse((Path(__file__).resolve().parents[2] / "bridge"
                     / "townlayout.py").read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(mod):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert "ctypes" not in imported, "input injection needs ctypes"
    assert not (imported & {"win32api", "win32gui", "win32con", "pyautogui"})
    # `bridge.act` is where every send lives, and nothing may reach it from
    # here: geometry must stay callable with the game closed, which is what
    # makes `--replay` and this whole test file possible.
    assert "bridge" not in imported or "act" not in {
        n.module for n in ast.walk(mod)
        if isinstance(n, ast.ImportFrom) and n.module}


# -- the estate map's quest tokens ----------------------------------------
#
# A fourth independent agreement, and the strongest of the four: three
# dungeons out of two different layout files, matched against a live estate
# map with `tools/find_button.py` on the game's own quest-type icons.

QUEST_TOKENS_MEASURED = {          # design px, from a live 1280x720 frame
    "crypts": (948.1, 312.0),
    "darkestdungeon": (1267.6, 192.0),
    "courtyard": (578.3, 422.3),
}


@pytest.mark.parametrize("dungeon", sorted(QUEST_TOKENS_MEASURED))
def test_the_derived_quest_token_lands_on_the_measured_one(dungeon):
    point = TL.quest_button(dungeon)
    assert point is not None, dungeon
    got = (point[0] * TL.DESIGN_W, point[1] * TL.DESIGN_H)
    want = QUEST_TOKENS_MEASURED[dungeon]
    assert abs(got[0] - want[0]) < 1.0, (dungeon, got, want)
    assert abs(got[1] - want[1]) < 1.0, (dungeon, got, want)


def test_the_quest_button_y_is_the_shipped_number_verbatim():
    """Only the x carries a measured residual. If both had needed one, the
    composition would be a fit rather than a derivation."""
    assert TL.QUEST_BUTTON_OFFSET[1] == 92.0
    row = TL._field("quest_select", "town_quest_select_dungeon_layout",
                    "quest_button_start_offset")
    assert row is not None and row[1] == 92.0


def test_the_courtyard_comes_out_of_the_dlc_file():
    """One dungeon lives in `crimson_court.quest_select.layout.darkest`, under
    a different filename -- so a single-file reading would silently return no
    point for it, and `_add` would refuse rather than click. Base is read
    first, which is the DLC merge rule everywhere else here."""
    assert TL.quest_map_pos("courtyard") == (570.0, 330.0)
    assert TL.quest_map_pos("crypts") == (940.0, 220.0)
    assert TL.quest_map_pos("no_such_dungeon") is None


def test_every_simulable_dungeon_has_a_token():
    from bridge.town_state import SIMULABLE_DUNGEONS

    for dungeon in SIMULABLE_DUNGEONS:
        assert TL.quest_button(dungeon) is not None, dungeon


def test_a_second_quest_in_one_region_refuses_rather_than_guessing():
    """`quest_button_spacing` has never been observed -- every live map has
    held at most one quest per dungeon -- and two readings fit the single
    measured point equally well. A wrong point here selects a DIFFERENT quest,
    which is not something to discover by doing."""
    assert TL.QUEST_ROW_MEASURED is False
    assert TL.quest_button("crypts", 1) is None
    assert TL.quest_button("crypts", -1) is None


# -- the provisioner's items ----------------------------------------------

def test_the_shipped_store_order_reproduces_two_hand_clicked_constants():
    """`PROVISION_FOOD` and `PROVISION_TORCH` were clicked by a human. The
    store list nobody derived them from puts food at cell 0 and torch at cell
    8, and the derived grid puts those cells where the two constants are."""
    from bridge.layout import PROVISION_FOOD, PROVISION_TORCH

    for item, clicked in (("food", PROVISION_FOOD), ("torch", PROVISION_TORCH)):
        derived = TL.shop_item(item)
        assert derived is not None, item
        # They must land on the SAME CELL, not merely nearby: the grid pitch
        # is 80 design px across and 160 down, so half of each is the bound.
        assert abs(derived[0] - clicked[0]) * TL.DESIGN_W < 40, item
        assert abs(derived[1] - clicked[1]) * TL.DESIGN_H < 80, item


def test_the_item_index_comes_from_the_shipped_list():
    assert TL.shop_item_index("food") == 0
    assert TL.shop_item_index("torch") == 8
    assert TL.shop_item_index("firewood") is None


def test_the_icon_centre_is_inside_its_own_cell():
    """The offset is half an icon, so it must not reach the next column: the
    grid pitch is 80 design px in x and 160 in y."""
    assert 0 < TL.ICON_CENTRE_OFFSET[0] < 80
    assert 0 < TL.ICON_CENTRE_OFFSET[1] < 160


# -- the roster's scroll arrows -------------------------------------------

# Hue-detected on a live estate map, 2026-09-02, at 1280x720: the orange
# chevrons in the roster list's left gutter.
ROSTER_CHEVRONS_MEASURED = {"up": (1580.3, 119.8), "down": (1580.3, 919.7)}


@pytest.mark.parametrize("which", ["up", "down"])
def test_the_derived_scroll_chevron_lands_on_the_measured_one(which):
    """One shipped constant, applied outward from each edge of the row band.

    Anchoring to the list's FRAMES instead puts them at design y -88 and -22,
    above the top of the screen, which is how the first version was caught.
    """
    point = TL.roster_scroll(which == "down")
    assert point is not None
    got = (point[0] * TL.DESIGN_W, point[1] * TL.DESIGN_H)
    want = ROSTER_CHEVRONS_MEASURED[which]
    assert abs(got[0] - want[0]) < 1.0, (which, got, want)
    assert abs(got[1] - want[1]) < 1.0, (which, got, want)


def test_the_two_chevrons_share_an_x_and_bracket_the_rows():
    """Same widget mirrored, not two coincidences -- and the derivation is
    symmetric about the row band, so it has to come out that way."""
    up, down = TL.roster_scroll(False), TL.roster_scroll(True)
    assert up[0] == down[0]
    band = TL.roster_band()
    assert band == (132.0, 908.0)
    assert up[1] * TL.DESIGN_H < band[0] < band[1] < down[1] * TL.DESIGN_H
    top_row, last_row = TL.roster_portrait(0), TL.roster_portrait(7)
    assert 0 < up[1] < top_row[1] and last_row[1] < down[1] < 1.0


def test_the_scroll_derivation_uses_one_shipped_number():
    """The outward step is `scroll_down_button_offset`'s own magnitude, 12.
    If that stopped being the number, both chevrons would move together and
    the two measured points above would both fail -- which is the check."""
    step = TL._field("roster", "town_roster_list_layout",
                     "scroll_down_button_offset")
    band = TL.roster_band()
    assert abs(step[1]) == 12.0
    assert TL.roster_scroll(False)[1] * TL.DESIGN_H == band[0] - 12.0
    assert TL.roster_scroll(True)[1] * TL.DESIGN_H == band[1] + 12.0


# -- the guild and the blacksmith -----------------------------------------

def test_the_guild_draws_one_row_per_skill_and_a_hero_has_seven():
    rows = [TL.guild_skill_node(i) for i in range(TL.GUILD_SKILL_ROWS)]
    assert all(r is not None for r in rows)
    assert TL.guild_skill_node(TL.GUILD_SKILL_ROWS) is None
    ys = [r[1] * TL.DESIGN_H for r in rows]
    assert ys == sorted(ys)
    assert all(0 < y < TL.DESIGN_H for y in ys)
    # `skill_spacing 0 91` -- rows go down, never across.
    assert len({round(r[0], 6) for r in rows}) == 1


def test_a_rank_moves_across_a_row_and_a_row_moves_down_the_panel():
    """`requirement_spacing 75 0` in each building's own tree layout, which is
    the same shape `upgrade_node` composes for the coach."""
    first, second = TL.guild_skill_node(0, 0), TL.guild_skill_node(0, 1)
    assert second[0] > first[0] and second[1] == first[1]
    row0, row1 = TL.guild_skill_node(0), TL.guild_skill_node(1)
    assert row1[1] > row0[1] and row1[0] == row0[0]


def test_the_blacksmith_has_exactly_two_rows():
    assert TL.blacksmith_node(0) is not None
    assert TL.blacksmith_node(1) is not None
    assert TL.blacksmith_node(2) is None
    assert TL.blacksmith_node(0)[1] < TL.blacksmith_node(1)[1]


def test_the_guild_and_blacksmith_are_declared_unmeasured():
    """The stage coach is why: its derived tree points landed in a margin
    until a live frame moved them by (133.5, 35.3), and no tree was drawn at
    all until the building crest was clicked."""
    assert TL.GUILD_MEASURED is False


# --- the coach point, and the screen that swallows it (2026-09-05) -------

def test_the_coach_point_is_inside_the_box_it_was_MEASURED_in():
    """THE FIRST POINT HERE OPENED THE BUTCHER'S CIRCUS.

    `town.layout.darkest`'s `pos.x` is the building's BANNER, and it does
    cross-check to within two pixels -- but a click has to hit the WAGON,
    which sits right of it and lower, and the y was never derivable at all.
    The Circus marker is planted just left of and below the coach and its
    hotspot reached over the shipped point, so two unattended iterations
    walked straight into it.

    The replacement was measured by clicking and reading the screen that came
    up. This pins it inside that box so a later tidy-up cannot drift it back.
    """
    from bridge import townlayout as T

    (x0, x1), (y0, y1) = T.STAGE_COACH_ANSWERS
    fx, fy = T.BUILDING_SCENE_POINTS["stage_coach"]
    assert x0 < fx < x1, (fx, x0, x1)
    assert y0 < fy < y1, (fy, y0, y1)
    # ...and not hugging an edge: the y margin is thin enough that a point on
    # the boundary would be one rounding away from the Circus again.
    assert min(fx - x0, x1 - fx) > 0.2 * (x1 - x0)
    assert min(fy - y0, y1 - fy) > 0.2 * (y1 - y0)


def test_the_old_coach_point_is_OUTSIDE_that_box():
    """The retired value, kept as the thing the box has to exclude. Without
    this the test above passes trivially against any box drawn round the new
    point."""
    from bridge import townlayout as T

    (_x0, _x1), (y0, y1) = T.STAGE_COACH_ANSWERS
    retired_y = 788.0 / T.DESIGN_H
    assert not y0 < retired_y < y1


def test_the_circus_has_a_way_OUT_and_it_is_not_a_key():
    """ESC opens the pause menu, whose only exits are the main menu and the
    desktop -- both measured live -- and the top-left compass opens the Banner
    Designer. The way back is a coordinate, so it has to be stored."""
    from bridge import layout as L
    from bridge import town

    assert "butchers_circus" in town.LEAVE_AT
    assert town.LEAVE_AT["butchers_circus"] == "circus_exit"
    fx, fy = L.CIRCUS_EXIT
    assert 0.9 < fx < 1.0 and 0.7 < fy < 0.85
    # It must not be one of the shared controls, or the dispatch would have
    # needed no new branch and this whole category would be a mistake.
    assert L.CIRCUS_EXIT != L.TOWN_CLOSE_PANEL


def test_every_LEAVE_AT_screen_names_a_real_layout_point():
    """A typo here is a silent refusal ("no move known for ...") in the middle
    of an unattended night."""
    from bridge.act import OfflineWindow
    from bridge.layout import load
    from bridge import town

    resolved = load().resolve(OfflineWindow(size=(1366, 768), origin=(0, 0)))
    for name, attr in town.LEAVE_AT.items():
        point = getattr(resolved, attr, None)
        assert isinstance(point, tuple) and len(point) == 2, (name, attr)


def test_LEAVE_AT_and_the_other_tables_do_not_overlap():
    """A screen in two tables takes whichever branch comes first, which makes
    the dispatch order load-bearing by accident."""
    from bridge import town

    tables = {"ADVANCE": set(town.ADVANCE), "CLOSE_PANEL": set(town.CLOSE_PANEL),
              "PRESS": set(town.PRESS), "CONFIRM": set(town.CONFIRM),
              "LEAVE_AT": set(town.LEAVE_AT)}
    for a in tables:
        for b in tables:
            if a < b:
                assert not tables[a] & tables[b], (a, b, tables[a] & tables[b])
