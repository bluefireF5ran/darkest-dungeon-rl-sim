"""Tests for the heads-up display (`bridge/overlay.py`) and the structured seam
it renders from (`bridge.run.Update`, `bridge.run.advised_slot`).

An overlay fails differently from the rest of this repo. It cannot raise in a
way anyone notices mid-fight, and its whole job is to point at a spot on a
screen — so the failure mode is a marker that is confidently on the **wrong**
rank, which looks exactly like correct software. Three things therefore get
pinned here rather than left to a look:

* rank 1 is the front rank on both sides, and the layout is sliced 4/4/1 in the
  order the calibration prompts ask for it;
* an ally skill points at the hero row and an attack at the enemy row, since a
  side mix-up mirrors every marker and nothing downstream would object;
* nothing is ever drawn in the colour that is keyed out of the window, which
  would punch a hole where that element should be.

The rendering test opens a real Tk window and destroys it. That is deliberate:
the alternative is asserting against a mock of the toolkit, which passes just as
happily when the toolkit is the thing that broke.
"""

import json
import queue
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest

from bridge.overlay import (
    CALIBRATION_PROMPTS, CARD_H, CARD_W, CLOSE_KEY, COLOURS, RANKS,
    TRANSPARENT_KEY, Layout, Overlay, room_lines,
)
from bridge.overlay_slots import MOVE_SLOT, PASS_SLOT
from bridge.run import Update, advised_button, advised_slot
from engine import Action

HERO_POINTS = [(200, 600), (320, 600), (440, 600), (560, 600)]
ENEMY_POINTS = [(1300, 600), (1420, 600), (1540, 600), (1660, 600)]


SKILL_BAR = Layout.spread((743, 1058), (1149, 1058)) + [(1213, 1058)]


def layout():
    return Layout(hero=list(HERO_POINTS), enemy=list(ENEMY_POINTS),
                  skills=list(SKILL_BAR), card=(600, 80), screen=(1920, 1080))


class _Skill:
    def __init__(self, allies):
        self.targets_allies = allies


class _Actor:
    id = 0
    skill_tier = 4


class _Engine:
    def __init__(self, allies=False):
        self.heroes = [_Actor()]
        self._allies = allies

    def get_skill_data(self, skill_id, level=4):
        return _Skill(self._allies)


# --- which spot on screen ---

def test_an_attack_points_at_the_enemy_row():
    assert advised_slot(_Engine(allies=False), Action(0, "smite", 2)) == ("enemy", 2)


def test_an_ally_skill_points_at_the_hero_row():
    """A side mix-up mirrors every marker onto the opposite formation, and
    nothing downstream of here would object to it."""
    assert advised_slot(_Engine(allies=True), Action(0, "divine_grace", 3)) == ("hero", 3)


def test_a_move_points_at_the_destination_rank():
    assert advised_slot(_Engine(), Action(0, "__move__", 1)) == ("hero", 1)


def test_a_pass_points_at_nothing():
    assert advised_slot(_Engine(), Action(0, "__pass__", -1)) == ("", -1)


def test_a_provision_points_at_nothing():
    """It is used from the inventory, not by clicking a rank; a ring on rank 1
    would send the player to the wrong half of the screen."""
    assert advised_slot(_Engine(), Action(0, "__provision_bandage__", 0)) == ("", -1)


def test_a_selfless_target_points_at_nothing():
    assert advised_slot(_Engine(), Action(0, "bolster", -1)) == ("", -1)


def test_the_raw_target_rank_is_kept_for_a_large_monster():
    """A size-2 monster answers to both ranks it covers, so the click the advice
    implies is legal on either. Normalising to its front rank would slide the
    marker off the half of the sprite the advice actually pointed at."""
    assert advised_slot(_Engine(), Action(0, "smite", 3))[1] == 3


# --- which button, in the game's own numbering ---

class _Hero:
    def __init__(self, skills):
        self.selected_skills = list(skills)


class _State:
    def __init__(self, *heroes):
        self.heroes = list(heroes)


HIGHWAYMAN = _Hero(["pistol_shot", "point_blank_shot", "duelist_advance",
                    "opened_vein"])


def test_the_button_is_the_index_in_what_the_hero_actually_brought():
    """The action space indexes the full 7-skill movepool so slot k names the
    same skill whatever a comp equips; the screen shows only the four brought,
    in save order. Mapping through the action index would point at the wrong
    icon for any hero whose loadout is not the first four of their movepool."""
    state = _State(HIGHWAYMAN)
    assert advised_button(state, Action(0, "pistol_shot", 0)) == 1
    assert advised_button(state, Action(0, "opened_vein", 0)) == 4


def test_move_and_pass_are_the_two_fixed_buttons():
    state = _State(HIGHWAYMAN)
    assert advised_button(state, Action(0, "__move__", 2)) == MOVE_SLOT
    assert advised_button(state, Action(0, "__pass__", -1)) == PASS_SLOT


def test_a_provision_has_no_button():
    assert advised_button(_State(HIGHWAYMAN),
                          Action(0, "__provision_bandage__", 0)) == -1


def test_a_skill_the_hero_did_not_bring_points_at_nothing():
    """The Abomination is never skill-restricted, so the engine can offer a
    beast-form skill that is not in `selected_skills` at all. Pointing at a
    button that is not on screen is worse than pointing at nothing."""
    assert advised_button(_State(HIGHWAYMAN), Action(0, "rake", 0)) == -1


def test_a_duplicate_class_party_resolves_per_hero_not_per_class():
    """A real party fields two Highwaymen (guids 245 and 2 in the live save).
    Anything class-keyed collapses them onto whichever was inserted last."""
    other = _Hero(["duelist_advance", "pistol_shot", "opened_vein", "grapeshot"])
    state = _State(HIGHWAYMAN, other)
    assert advised_button(state, Action(0, "duelist_advance", 0)) == 3
    assert advised_button(state, Action(1, "duelist_advance", 0)) == 1


def test_a_hero_index_off_the_end_is_survivable():
    assert advised_button(_State(HIGHWAYMAN), Action(7, "pistol_shot", 0)) == -1


# --- the layout ---

def test_the_layout_survives_a_round_trip(tmp_path):
    path = tmp_path / "layout.json"
    layout().save(path)
    assert Layout.load(path) == layout()


def test_a_layout_is_rejected_unless_both_rows_are_complete(tmp_path):
    """Half a calibration is worse than none: it renders, and every marker on
    the missing row silently becomes a no-op."""
    path = tmp_path / "half.json"
    path.write_text(json.dumps({"hero": HERO_POINTS, "enemy": ENEMY_POINTS[:2],
                                "card": [0, 0]}), encoding="utf-8")
    assert Layout.load(path) is None


def test_a_missing_or_corrupt_layout_is_none_not_an_error(tmp_path):
    assert Layout.load(tmp_path / "nope.json") is None
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert Layout.load(bad) is None


def test_rank_one_is_the_first_point_on_each_side():
    """The whole system numbers the FRONT rank 1 — engine, advisor and the
    game's own tooltips. Reversing it here mirrors every marker."""
    assert layout().point("hero", 0) == HERO_POINTS[0]
    assert layout().point("enemy", 0) == ENEMY_POINTS[0]


def test_an_unknown_side_or_rank_resolves_to_nothing():
    assert layout().point("enemy", 9) is None
    assert layout().point("hero", -1) is None
    assert layout().point("", 0) is None


def test_the_calibration_prompts_match_how_the_points_are_sliced():
    """4 hero / 4 enemy / skill-1 / skill-5 / pass / card. If a prompt is added
    without moving the slice, every marker after it lands one place off."""
    assert len(CALIBRATION_PROMPTS) == RANKS * 2 + 4
    assert all("HERO" in p for p in CALIBRATION_PROMPTS[:RANKS])
    assert all("ENEMY" in p for p in CALIBRATION_PROMPTS[RANKS:RANKS * 2])
    assert "SKILL slot 1" in CALIBRATION_PROMPTS[RANKS * 2]
    assert "PASS" in CALIBRATION_PROMPTS[RANKS * 2 + 2]


# --- the action bar ---

def test_the_skill_slots_are_evenly_spread_from_two_clicks():
    """Slots 2-4 are interpolated: the bar is evenly spaced, so three more
    clicks would only be three more chances to be off."""
    spread = Layout.spread((743, 1058), (1149, 1058))
    assert len(spread) == 5
    assert spread[0] == (743, 1058) and spread[-1] == (1149, 1058)
    gaps = [spread[i + 1][0] - spread[i][0] for i in range(4)]
    assert max(gaps) - min(gaps) <= 1, gaps


def test_the_pass_box_is_not_on_the_skill_spacing():
    """The X sits about two thirds of a step past slot 5 and is a third as
    wide, which is why it is clicked rather than extrapolated."""
    layout_with_bar = layout()
    step = layout_with_bar.skills[1][0] - layout_with_bar.skills[0][0]
    to_pass = layout_with_bar.skills[5][0] - layout_with_bar.skills[4][0]
    assert to_pass < step
    assert layout_with_bar.pass_half < layout_with_bar.skill_half


def test_a_layout_calibrated_before_skill_marking_still_loads(tmp_path):
    """Refusing an older layout would throw away a good calibration over an
    addition; it just draws no square."""
    path = tmp_path / "old.json"
    path.write_text(json.dumps({"hero": HERO_POINTS, "enemy": ENEMY_POINTS,
                                "card": [600, 80]}), encoding="utf-8")
    old = Layout.load(path)
    assert old is not None and old.skill_point(1) is None


def test_an_unknown_slot_points_at_nothing():
    assert layout().skill_point(-1) is None
    assert layout().skill_point(9) is None


def test_the_advice_marks_which_button_to_press(overlay):
    overlay.render(Update(phase="battle", headline="x", side="enemy", rank=1,
                          skill_slot=3))
    squares = [i for i in overlay.canvas.find_all()
               if overlay.canvas.type(i) == "rectangle"
               and overlay.canvas.itemcget(i, "outline") == COLOURS["skill"]]
    assert squares, "no square drawn for the advised skill"


def test_a_provision_marks_no_button(overlay):
    """It is used from the inventory, not from the action bar."""
    overlay.render(Update(phase="battle", headline="x", skill_slot=-1))
    assert not [i for i in overlay.canvas.find_all()
                if overlay.canvas.type(i) == "rectangle"
                and overlay.canvas.itemcget(i, "outline") == COLOURS["skill"]]


def test_the_pass_square_is_narrower_than_a_skill_square(overlay):
    def width_of(slot):
        overlay.canvas.delete("all")
        overlay.render(Update(phase="battle", headline="x", skill_slot=slot))
        box = min((overlay.canvas.coords(i) for i in overlay.canvas.find_all()
                   if overlay.canvas.type(i) == "rectangle"
                   and overlay.canvas.itemcget(i, "outline") == COLOURS["skill"]),
                  key=lambda c: c[2] - c[0])
        return box[2] - box[0]
    assert width_of(PASS_SLOT) < width_of(1)


# --- the card text ---

def test_the_card_keeps_the_layers_that_are_not_the_headline():
    block = ("=== rooA ===\nROUTE   a > b\n        6 moves\n"
             "CURIO   shovel -> web\nTORCH   hold the light\nCAMP    push on")
    lines = room_lines(block)
    assert not any(line.startswith("ROUTE") for line in lines)
    assert lines[0].startswith("CURIO")


def test_the_card_never_grows_past_what_fits():
    block = "\n".join(f"CURIO   thing {i}" for i in range(20))
    assert len(room_lines(block)) == 3


def test_a_block_with_no_layer_headers_yields_nothing():
    assert room_lines("=== rooA ===\n  just a header") == []


# --- the window ---

def test_no_colour_is_the_one_keyed_out_of_the_window():
    """Anything drawn in the transparent key becomes a hole. It would look like
    the element simply failed to render."""
    assert TRANSPARENT_KEY not in COLOURS.values()


def test_every_verdict_has_a_colour():
    from bridge.run import VERDICTS
    assert all(v in COLOURS for v in VERDICTS)


def test_the_close_key_is_read_globally_and_is_not_the_game_s_own():
    """A click-through window cannot receive a keystroke — that is the point of
    it — so tkinter's Esc binding is dead exactly when the overlay works. The
    key is polled globally instead, which rules out Esc: that is DD's menu."""
    from bridge.overlay import VK_CLOSE
    assert VK_CLOSE == 0x79 and CLOSE_KEY == "F10"
    assert VK_CLOSE != 0x1B                       # VK_ESCAPE


@pytest.fixture(scope="module")
def _window():
    """One Tk root for the whole module.

    Tk tolerates a second root badly: creating and destroying one per test made
    this file skip a random one of its own cases roughly one run in two, which
    is exactly the kind of flake that later gets "fixed" by deleting the test.
    """
    try:
        made = Overlay(layout(), queue.Queue())
    except Exception as exc:                      # noqa: BLE001
        pytest.skip(f"no usable display: {exc}")
    yield made
    made.root.destroy()


@pytest.fixture
def overlay(_window):
    """The shared window, wound back to a blank slate."""
    _window.canvas.delete("all")
    _window.last_verdict = ""
    _window.tally = ()
    return _window


def test_a_battle_update_draws_a_ring_and_a_card(overlay):
    overlay.render(Update(phase="battle", headline="Vestal: JUDGEMENT on rank 2",
                          side="enemy", rank=1, confidence=0.61))
    ovals = [i for i in overlay.canvas.find_all()
             if overlay.canvas.type(i) == "oval"]
    assert ovals, "no ring drawn for a targeted recommendation"
    assert overlay.canvas.find_all()


def test_a_verdict_clears_the_ring(overlay):
    """A verdict closes the PREVIOUS turn. Leaving its ring up would point at a
    board that is already gone, which is worse than pointing at nothing."""
    overlay.render(Update(phase="battle", headline="x", side="enemy", rank=1))
    overlay.render(Update(phase="verdict", verdict="DIFFER", headline="played attack",
                          tally=(("MATCH", 1), ("DIFFER", 1), ("?", 0))))
    assert not [i for i in overlay.canvas.find_all()
                if overlay.canvas.type(i) == "oval"]
    assert overlay.last_verdict == "DIFFER"


def test_an_untargeted_recommendation_still_renders(overlay):
    """A pass and a provision point at nothing. The card must still appear —
    a blank screen reads as "the bridge died"."""
    overlay.render(Update(phase="battle", headline="Highwayman: PASS on self"))
    assert overlay.canvas.find_all()


# --- a preview must not read as an instruction ---
#
# Reported from live play: "the decision it recommends is not the one that's
# due". The turn pointer was not the cause — checked against all 97 usable
# captures, the queue never names a hero outside the party and `next_hero` never
# points outside it. The cause was here: `recommend` reads ahead past pending
# enemy turns so the screen is not blank through ~17 s of silence (13 of 77
# advisories in the capture set are that look-ahead), and the overlay published
# only the move, dropping the "after 2 enemy turns ->" lead the terminal prints.

def test_a_look_ahead_says_so_in_the_headline():
    """The lead is built in run.py so every front-end inherits it, rather than
    each one having to remember to add it."""
    from bridge.run import Update as U
    u = U(phase="battle", headline="2 ENEMY TURNS FIRST, then Vestal: JUDGEMENT",
          provisional=True, enemies_first=2)
    assert u.headline.startswith("2 ENEMY TURNS FIRST")


def test_a_look_ahead_is_not_drawn_in_a_side_colour(overlay):
    """Grey, not the enemy's red: it is not yet an instruction."""
    overlay.render(Update(phase="battle", headline="x", side="enemy", rank=1,
                          provisional=True, enemies_first=1))
    rings = [i for i in overlay.canvas.find_all()
             if overlay.canvas.type(i) == "oval"
             and overlay.canvas.itemcget(i, "outline") != "#000000"]
    assert rings, "a look-ahead with no repositioners should still point"
    assert overlay.canvas.itemcget(rings[0], "outline") == COLOURS["provisional"]
    assert overlay.canvas.itemcget(rings[0], "dash")


def test_no_ring_at_all_when_a_pending_enemy_can_reposition(overlay):
    """46 of 246 monsters can move somebody. If one does, the advised rank holds
    a different combatant by the time the hero acts — and a confident circle on
    the wrong enemy is worse than no circle."""
    overlay.render(Update(phase="battle", headline="x", side="enemy", rank=1,
                          provisional=True, enemies_first=1,
                          movers=("Brigand Bloodletter",)))
    assert not [i for i in overlay.canvas.find_all()
                if overlay.canvas.type(i) == "oval"]
    assert overlay.canvas.find_all()          # the card still explains why


def test_a_current_recommendation_keeps_its_side_colour(overlay):
    overlay.render(Update(phase="battle", headline="x", side="enemy", rank=1))
    rings = [i for i in overlay.canvas.find_all()
             if overlay.canvas.type(i) == "oval"
             and overlay.canvas.itemcget(i, "outline") != "#000000"]
    assert overlay.canvas.itemcget(rings[0], "outline") == COLOURS["enemy"]
    assert not overlay.canvas.itemcget(rings[0], "dash")


def test_a_room_update_renders_without_a_ring(overlay):
    overlay.render(Update(phase="room", headline="go to coAF",
                          detail="TORCH   hold the light\nCAMP    push on"))
    assert not [i for i in overlay.canvas.find_all()
                if overlay.canvas.type(i) == "oval"]
    assert overlay.canvas.find_all()


def test_a_rank_the_layout_does_not_have_is_survivable(overlay):
    overlay.render(Update(phase="battle", headline="x", side="enemy", rank=7))
    assert overlay.canvas.find_all()          # card still drawn


def test_the_tally_survives_an_update_that_carries_none(overlay):
    """Room blocks and verdicts both carry the tally; a battle block published
    before the first resolved turn does not. Blanking the badge each time would
    make the running count flicker."""
    overlay.render(Update(phase="verdict", verdict="MATCH",
                          tally=(("MATCH", 3), ("DIFFER", 1), ("?", 0))))
    overlay.render(Update(phase="battle", headline="x", side="enemy", rank=0))
    assert overlay.tally == (("MATCH", 3), ("DIFFER", 1), ("?", 0))


def test_the_card_fits_on_a_normal_screen():
    assert CARD_W + 600 < 1920 and CARD_H + 80 < 1080


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
