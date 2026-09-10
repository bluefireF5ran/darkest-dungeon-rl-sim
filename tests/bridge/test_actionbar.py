"""The drawn action bar, and what it says about where a hero is standing.

Two real frames from one live Crypts fight on 2026-09-05, both banked from the
moment the bridge could not make a turn land:

* `battle_bar_plague_doctor_rank1.png` -- her kit launches from 2, 3 and 4 and
  she is at rank 1, so the game greys **all four** skills. This is the frame
  the bridge froze on for six minutes, pressing a digit the game would not
  take.
* `battle_bar_shieldbreaker_rank1.png` -- three of his four launch from rank 1,
  `single_out` does not, so the game draws `###.`.

They are the positive and negative control for the same reader, and the second
one is what makes this a test of LEGALITY rather than of brightness: the four
drawn icons are predicted, one by one, from the shipped `.launch` lists.

Stored at half resolution. That is not a saving dressed up as a principle --
the verdict is identical at both sizes because a greyed icon is desaturated to
*exactly* zero, so interpolating grey with grey stays grey, and the test below
pins that rather than a threshold somebody chose.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from bridge import actionbar
from bridge.layout import load as load_layout
from bridge.overlay_slots import MOVE_SLOT, PASS_SLOT

CAPTURES = Path(__file__).resolve().parents[2] / "captures"
from data.paths import GAME_DIR
from data.paths import CSV_DIR

#: The two parties' equipped kits, in action-bar order, as the save lists them.
PLAGUE_DOCTOR = ("plague_grenade", "blinding_gas", "battlefield_medicine",
                 "noxious_blast")
SHIELDBREAKER = ("pierce", "adders_kiss", "expose", "single_out")


class _Resolved:
    """Only what `actionbar.read` touches: the fractional layout."""

    def __init__(self, frac):
        self.frac = frac


@pytest.fixture(scope="module")
def resolved():
    lay = load_layout()
    assert lay is not None, "bridge/overlay_layout.json did not migrate"
    return _Resolved(lay)


def _frame(name):
    pytest.importorskip("PIL")
    pytest.importorskip("numpy")
    import numpy as np
    from PIL import Image

    path = CAPTURES / name
    if not path.exists():
        pytest.skip(f"{name} is not banked")
    return np.array(Image.open(path).convert("RGB"))


def _launches(ids):
    """`.launch` per skill id, from the shipped data."""
    if not GAME_DIR.exists():
        pytest.skip("the game is not installed at GAME_DIR")
    from data.store_cache import load_store_cached

    table = load_store_cached(str(CSV_DIR), str(GAME_DIR)).skill_index()
    out = []
    for sid in ids:
        data = next((table.get((sid, lvl)) for lvl in range(6)
                     if table.get((sid, lvl)) is not None), None)
        out.append(tuple(data.launch) if data is not None and data.launch else None)
    return tuple(out)


# --- the reader ----------------------------------------------------------

def test_a_hero_who_cannot_act_shows_four_greyed_skills(resolved):
    bar = actionbar.read(_frame("battle_bar_plague_doctor_rank1.png"), resolved)
    assert str(bar) == "....##"
    assert bar.waiting, "MOVE and the X are lit -- the game IS asking her"
    assert not bar.any_skill


def test_a_hero_who_can_act_shows_the_skills_it_can_launch(resolved):
    bar = actionbar.read(_frame("battle_bar_shieldbreaker_rank1.png"), resolved)
    assert str(bar) == "###.##"
    assert bar.waiting and bar.any_skill
    assert bar.skill_pattern == (True, True, True, False)


def test_a_greyed_icon_is_desaturated_to_EXACTLY_zero(resolved):
    """The mechanism, not the threshold.

    Darkest Dungeon draws an unusable skill through a grayscale shader, so the
    signal is not "dimmer" -- it is a channel spread of zero, which no lit icon
    can reach by being dark. That is why `LIT_SAT` can sit an order of
    magnitude under the dimmest lit reading and still never be tuned.
    """
    bar = actionbar.read(_frame("battle_bar_plague_doctor_rank1.png"), resolved)
    assert bar.sat[:4] == (0.0, 0.0, 0.0, 0.0)
    assert min(bar.sat[MOVE_SLOT - 1], bar.sat[PASS_SLOT - 1]) > 4 * actionbar.LIT_SAT


def test_the_two_signals_agree_on_every_slot_of_both_frames(resolved):
    """`lit` takes saturation OR brightness, and the OR has never been used.

    Both are kept because each covers a way the other could be fooled -- a
    monochrome modded icon, a game that renders in browns -- but if they ever
    start disagreeing, the reader is looking at something new and the disagreeing
    slot is where to look.
    """
    for name in ("battle_bar_plague_doctor_rank1.png",
                 "battle_bar_shieldbreaker_rank1.png"):
        bar = actionbar.read(_frame(name), resolved)
        for i, lit in enumerate(bar.lit):
            by_sat = bar.sat[i] >= actionbar.LIT_SAT
            by_light = bar.bright[i] >= actionbar.LIT_BRIGHT
            assert by_sat == by_light == lit, (name, i, bar.sat[i], bar.bright[i])


def test_it_refuses_rather_than_guessing_when_it_cannot_see(resolved):
    """None is "no opinion", never "nothing is legal".

    Every caller treats it as the former, so a display that will not read costs
    the cross-check and never the turn. Same one-sided rule as `_panel_is_open`.
    """
    assert actionbar.read(None, resolved) is None
    assert actionbar.read(_frame("battle_bar_shieldbreaker_rank1.png"), None) is None
    assert actionbar.read([[1, 2], [3, 4]], resolved) is None
    assert actionbar.read(_frame("battle_bar_shieldbreaker_rank1.png"),
                          _Resolved(None)) is None


# --- the cross-check: the drawn bar IS the launch table ------------------

def test_the_drawn_bar_matches_what_launch_predicts(resolved):
    """The check that this reads legality and not brightness.

    A Shieldbreaker at rank 1: `pierce` (.launch 3,2,1), `adders_kiss` (1) and
    `expose` (3,2,1) are lit, `single_out` (3,2) is not. Four predictions from
    shipped data against four icons on a real frame, and nothing was fitted.
    """
    bar = actionbar.read(_frame("battle_bar_shieldbreaker_rank1.png"), resolved)
    want = actionbar.predicted_pattern(_launches(SHIELDBREAKER), 1)
    assert bar.skill_pattern == want == (True, True, True, False)


def test_the_all_greyed_frame_is_predicted_too(resolved):
    bar = actionbar.read(_frame("battle_bar_plague_doctor_rank1.png"), resolved)
    assert bar.skill_pattern == actionbar.predicted_pattern(
        _launches(PLAGUE_DOCTOR), 1) == (False, False, False, False)


# --- recovering the rank the save cannot tell us -------------------------

def test_it_recovers_the_rank_both_heroes_were_really_standing_in():
    """The save said `[shieldbreaker, plague_doctor]` and both were at rank 1.

    Not a contradiction -- they were at ranks 1 and 2 in that order when the
    fight began, the Plague Doctor moved to the front mid-fight, and
    `party.heroes` was never rewritten. The bar is the only thing that knows.
    """
    assert actionbar.rank_from_pattern(
        _launches(PLAGUE_DOCTOR), (False, False, False, False), 2) == 1
    assert actionbar.rank_from_pattern(
        _launches(SHIELDBREAKER), (True, True, True, False), 2) == 1


def test_an_ambiguous_pattern_returns_None_and_that_costs_nothing():
    """A kit that launches from everywhere lights all four everywhere.

    Refusing is right and it is free: such a hero can always act, so nothing
    downstream ever needs to know where it is standing.
    """
    everywhere = ((1, 2, 3, 4),) * 4
    assert actionbar.rank_from_pattern(everywhere, (True,) * 4, 4) is None
    # And a pattern no rank can produce is refused rather than approximated.
    assert actionbar.rank_from_pattern(((1,), (1,), (1,), (1,)),
                                       (True, False, True, False), 4) is None


def test_an_unreadable_skill_can_never_rule_a_rank_out():
    """`None` predicts lit, so a skill the store could not resolve is inert.

    The alternative -- predicting "greyed" -- would let a missing skill pose as
    evidence about where the hero stands, and modded classes are exactly where
    a lookup comes back empty.
    """
    assert actionbar.predicted_pattern((None, None), 3) == (True, True)
    # A Plague Doctor whose whole kit failed to resolve is unlocatable, not
    # located at some default.
    assert actionbar.rank_from_pattern((None,) * 4, (True,) * 4, 4) is None
