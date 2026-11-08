"""The roster list arrives SCROLLED, and the save does not say by how much.

Found live on 2026-09-02: the list was at **offset 4** on a 12-hero campaign
before anything had touched it, so `roster_portrait(save_index)` was pointing
four rows off. A wrong row here does not miss -- it drags a different hero, and
the party bar confirms it exactly as it would the right one, which is why the
offset has to be measured rather than assumed and why an unreadable one has to
refuse.

These drive the matcher against SYNTHETIC frames built from the game's own
portrait art, so they need the install but not a running game. The live
walk-down (4 -> 3 -> 2 -> 1 -> 0, every step read back) is in `CHANGELOG.md`.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest

from bridge import town
from bridge import townlayout as TL

from data.paths import GAME_DIR

pytestmark = pytest.mark.skipif(not GAME_DIR.exists(),
                                reason="needs the game install")

GD = str(GAME_DIR)
W, H = 1280, 720

ROSTER = ["crusader", "highwayman", "plague_doctor", "vestal", "leper",
          "antiquarian", "antiquarian", "jester", "occultist", "duelist",
          "man_at_arms", "abomination"]


class _Hero:
    def __init__(self, hero_class, in_party=False):
        self.hero_class = hero_class
        self.in_party = in_party


class _Town:
    def __init__(self, classes):
        self.heroes = [_Hero(c) for c in classes]


def _frame(classes, offset):
    """A fake 1280x720 grab with the game's own portraits pasted into the rows
    the list would draw at `offset`."""
    import numpy as np

    refs = town._roster_portrait_refs(GD)
    img = np.zeros((H, W, 3), dtype=np.uint8)
    side = 2 * town.ROSTER_PATCH
    for row in range(town.ROSTER_ROWS_VISIBLE):
        hero_class = classes[offset + row]
        point = TL.roster_portrait(row)
        cx, cy = int(round(point[0] * W)), int(round(point[1] * H))
        art = refs[hero_class][0]
        img[cy - town.ROSTER_PATCH:cy + town.ROSTER_PATCH,
            cx - town.ROSTER_PATCH:cx + town.ROSTER_PATCH] = art.astype("uint8")
    return img


@pytest.fixture
def fake_grab(monkeypatch):
    def install(img):
        import bridge.screen as screen_mod
        monkeypatch.setattr(screen_mod, "grab", lambda _win: img)
    return install


def test_the_game_ships_a_roster_portrait_for_every_class():
    """The matcher is only as good as its reference set, and a class with no
    art scores 999 -- which would silently push every alignment containing it
    to the bottom."""
    refs = town._roster_portrait_refs(GD)
    for hero_class in set(ROSTER) | {"musketeer", "flagellant", "shieldbreaker",
                                     "runaway", "hellion", "houndmaster"}:
        assert refs.get(hero_class), hero_class


@pytest.mark.parametrize("offset", [0, 1, 2, 3, 4])
def test_the_offset_is_recovered_from_the_drawn_portraits(offset, fake_grab):
    fake_grab(_frame(ROSTER, offset))
    assert town._roster_offset(None, _Town(ROSTER), GAME_DIR) == offset


def test_a_roster_that_cannot_scroll_is_offset_zero_without_looking():
    """Eight or fewer heroes fill the list, so there is nothing to measure --
    and this is why `week_skip` and `recruit_all` were correct for months
    while passing a save index straight in."""
    assert town._roster_offset(None, _Town(ROSTER[:8]), GAME_DIR) == 0
    assert town._roster_offset(None, _Town(ROSTER[:3]), GAME_DIR) == 0


def test_an_unreadable_frame_refuses_rather_than_guessing_zero(fake_grab):
    """Returning 0 on a frame it cannot read is the dangerous failure: 0 is a
    plausible offset, so every caller would seat heroes off by however far the
    list really was."""
    fake_grab(None)
    assert town._roster_offset(None, _Town(ROSTER), GAME_DIR) is None


def test_an_ambiguous_alignment_refuses(fake_grab):
    """A roster of one repeated class aligns equally well at every offset.
    There is no right answer, so there must be no answer."""
    same = ["crusader"] * 12
    fake_grab(_frame(same, 0))
    assert town._roster_offset(None, _Town(same), GAME_DIR) is None


def test_the_margin_is_what_makes_it_a_measurement(fake_grab):
    """Live margins were 5.6-6.9 and the threshold is 2.0. Raising it above
    the real separation must make the reading refuse, not silently pass --
    that is the difference between a gate and a decoration."""
    fake_grab(_frame(ROSTER, 3))
    assert town._roster_offset(None, _Town(ROSTER), GAME_DIR) == 3
    import bridge.town as town_mod
    old = town_mod.ROSTER_MATCH_MARGIN
    try:
        town_mod.ROSTER_MATCH_MARGIN = 10_000.0
        assert town._roster_offset(None, _Town(ROSTER), GAME_DIR) is None
    finally:
        town_mod.ROSTER_MATCH_MARGIN = old


def test_a_screen_row_is_the_save_index_minus_the_offset():
    """The whole arithmetic, stated once. `seat_hero` takes the left-hand
    side; `plan.party` carries heroes identified by guid, which resolve to the
    right-hand side."""
    for offset in range(5):
        for index in range(offset, offset + town.ROSTER_ROWS_VISIBLE):
            row = index - offset
            assert 0 <= row < town.ROSTER_ROWS_VISIBLE
            assert TL.roster_portrait(row) is not None


def test_the_park_point_is_outside_the_recogniser_s_signature_box():
    """A parked cursor draws a tooltip and a tooltip covers `SIG_BOX`, which
    stalled a live walk on the provision screen with the Embark button lit.
    The park has to be somewhere the recogniser does not look."""
    from bridge import screen

    x0, y0, x1, y1 = screen.SIG_BOX
    px, py = town.CURSOR_PARK
    assert not (x0 <= px <= x1 and y0 <= py <= y1)
    assert 0.0 < px < 1.0 and 0.0 < py < 1.0

# -- the list draws the roster MINUS the party bar --------------------------

def test_a_hero_in_the_PARTY_is_not_in_the_list():
    """**DD moves a seated hero out of the roster list**, so a roster of 11
    with 3 seated draws exactly 8 -- which is also `ROSTER_ROWS_VISIBLE`, so
    nothing can scroll and the chevron is not drawn at all.

    Measured live 2026-09-22 on Fran's campaign: save rows 3, 6 and 9 were in
    the party, the list drew the other eight with save row 0 in its TOP row,
    and matching the 11-long save sequence against 8 drawn portraits reported
    offset 2. The seat for save row 0 was refused as "not on screen", the
    scroll could not move a list already at its top, and the night looped.
    """
    town_obj = _Town(ROSTER[:11])
    for i in (3, 6, 9):
        town_obj.heroes[i].in_party = True
    drawn = town.drawn_roster(town_obj)
    assert len(drawn) == 8
    assert [h.hero_class for h in drawn] == [
        c for i, c in enumerate(ROSTER[:11]) if i not in (3, 6, 9)]


def test_eight_drawn_rows_cannot_scroll_whatever_the_save_holds():
    """The guard that failed: `len(town.heroes) <= ROSTER_ROWS_VISIBLE` asked
    the save, and the save is not what is drawn."""
    town_obj = _Town(ROSTER[:11])
    for i in (3, 6, 9):
        town_obj.heroes[i].in_party = True
    assert town._roster_offset(None, town_obj) == 0


def test_nobody_seated_leaves_the_list_exactly_as_it_was():
    """The fix may not change the ordinary case."""
    town_obj = _Town(ROSTER)
    assert [h.hero_class for h in town.drawn_roster(town_obj)] == ROSTER

