"""Finding the curio panel's buttons on screen.

`W` at a curio was never broken -- it opens the panel, and nothing clicked
anything in it. So the bridge pressed `W`, saw the curio still active, called it
"did not respond", stepped forward and pressed `W` again into an already-open
panel, six times. This module is the missing half.

Three fixtures, all real frames:

* `curio_panel.png` -- the Traveler's Tent at 1280x720, panel open, its item
  slot drawn and **dimmed** (the Tent has no item interaction at all);
* `obstacle_panel_shovel.png` -- the Cove's Shipwreck at 1366x768, panel open,
  its shovel slot **lit** because the party is carrying four;
* `_curio_approach.png` -- the same corridor with no panel.

The third matters more than the first two: a detector that fires on scenery
sends a click into the world, and the first version did exactly that.
"""

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from bridge import curiopanel
from bridge.layout import LOOT_CLOSE

CAPTURES = Path(__file__).resolve().parents[2] / "captures"
PANEL = CAPTURES / "curio_panel.png"
ITEM_PANEL = CAPTURES / "obstacle_panel_shovel.png"
# **A TRACKED FRAME, not the banked failure shot.** `_curio_approach.png`
# is what `_bank_frame` overwrites on every missed curio, so a test
# pointed at it grades whatever the last live run happened to be looking
# at -- and on 2026-09-05 a run replaced it and this file went red
# without a line of source changing. This copy is a real corridor with
# the map panel up and no curio panel anywhere.
NO_PANEL = CAPTURES / "curio_none_corridor.png"

# xdist: this file's tests spike to +21 MB each while they decode frames.
# `--dist load` scatters them, so up to 16 spikes land at once and the suite's
# PEAK -- not its steady state -- is what crashes the machine. Grouped, at most
# one runs at a time. The four grouped files cost 12.9 s serially against a 74 s
# suite, so this buys memory without touching the critical path.
#
# BOTH MARKS IN ONE LIST. Assigning `pytestmark` twice silently drops the first
# -- it is a plain module global -- and this file did exactly that, so the
# grouping above was documented and not applied for as long as it existed.
pytestmark = [
    pytest.mark.xdist_group("memory_heavy"),
    pytest.mark.skipif(not PANEL.exists(),
                       reason="live curio-panel capture not present"),
]


def _load(path: Path) -> np.ndarray:
    from PIL import Image
    return np.asarray(Image.open(path).convert("RGB"))


def test_it_finds_the_hand_and_the_close_button():
    found = curiopanel.find(_load(PANEL))
    assert found is not None
    assert found.readable
    assert found.close == pytest.approx((0.7609, 0.4280), abs=0.004)
    assert found.hand == pytest.approx((0.6436, 0.4267), abs=0.004)
    # The hand is left of the X and on the same row -- that is the whole model.
    assert found.hand[0] < found.close[0]
    assert abs(found.hand[1] - found.close[1]) < 0.01


def test_the_close_button_agrees_with_the_independent_measurement():
    """`layout.py` measured this panel's X at (1950, 618) of 2560x1440 while
    documenting why it refused to store it. This frame is 1280x720, a different
    run and half the resolution, and lands within a thousandth of it. Two
    independent measurements agreeing is why the offset is trustworthy."""
    found = curiopanel.find(_load(PANEL))
    documented_x = 1950.0 / 2560
    assert abs(found.close[0] - documented_x) < 0.005
    # ...and it is NOT the loot window's X, which is further right.
    assert abs(found.close[0] - LOOT_CLOSE[0]) > 0.001


@pytest.mark.skipif(not NO_PANEL.exists(), reason="control frame not present")
def test_it_finds_nothing_when_no_panel_is_open():
    """The regression that matters. The first version returned a confident
    button here -- an 8-pixel smear of red scenery near the corridor gate --
    which would have put a click in the world. A real X is a 28x28 square
    carrying 133 pixels; the smear was 17x22 carrying 8. Shape and mass
    separate them, colour alone does not.

    **THIS FRAME PASSES THE COLOUR, SHAPE AND MASS TESTS**, and the thing it
    finds is the party's own TORCH MARKER on the map panel -- warm, compact,
    about the right size, measured at (913.5, 623.5) and independently fitted
    by `bridge/mappanel.py` as the room the party is standing in. It reported
    "something red on screen after W" on every curio press of a live raid.

    What separates them is POSITION, and not by luck: DD draws the curio and
    obstacle panels over the middle of the HUD, above the map box. The two real
    panels in this repo sit at y 308 and 329 against a map panel starting at
    544, so `_in_map_panel` throws this one out with two hundred pixels to
    spare and nothing else in the corpus moves.

    **AND IT RETURNS `hand=None` NOW, NOT `None`** (2026-09-07). Clustering
    used to merge every red pixel in the right half that shared an x range, so
    the torch marker and a second, higher blob came back as one thing whose
    centroid landed inside the map panel. `_red_blocks` cuts by row as well
    now -- it had to, because that same merge was hiding the curio panel's own
    X behind the map's quest tokens -- and the two separate: the marker is
    still thrown out by `_in_map_panel` and the other blob is offered.

    So the contract is stated where every caller actually reads it: nothing
    CLICKABLE. `_work_curio_panel` and `_dismiss_stray_curio_panel` both gate
    on `readable`, which needs the hand's gold plate two pitches to the left,
    and a corridor wall does not have one.
    """
    found = curiopanel.find(_load(NO_PANEL))
    assert found is None or not found.readable, found


def test_degenerate_input_is_not_a_panel():
    assert curiopanel.find(None) is None
    assert curiopanel.find(np.zeros((0, 0, 3), dtype=np.uint8)) is None
    assert curiopanel.find(np.zeros((720, 1280, 3), dtype=np.uint8)) is None


def test_a_wall_of_red_is_not_a_button():
    """Fail closed on the pathological frame: a red screen must not resolve to
    a click. Size is what rejects it -- the torch bar and HP bars are red and
    live in the same half of the screen."""
    img = np.zeros((720, 1280, 3), dtype=np.uint8)
    img[:, :, 0] = 255
    assert curiopanel.find(img) is None


# --- the slot grid ---

def test_the_pitch_and_the_old_hand_offset_still_agree():
    """TWO CONSTANTS THAT MUST NOT DRIFT APART.

    `HAND_FROM_X` was measured on one frame and clicked correctly for weeks;
    `SLOT_PITCH_FRAC` was measured on two and is what the item slot is derived
    from. They describe the same row, so the hand must be two pitches out --
    and "agree" here means inside one button (~0.021 of the width), because
    anything inside the button clicks it.
    """
    derived = 2 * curiopanel.SLOT_PITCH_FRAC
    assert abs(derived - abs(curiopanel.HAND_FROM_X[0])) < 0.02


@pytest.mark.skipif(not ITEM_PANEL.exists(), reason="obstacle capture absent")
def test_the_item_slot_is_read_off_the_panel_and_not_assumed():
    """THE LIVE BLOCKER THIS EXISTS FOR.

    The party stood in front of the Cove's Shipwreck with four shovels in the
    pack, and the bridge knew only how to click the X -- so the raid ended
    stuck in a corridor. The shovel is one pitch left of the X.
    """
    found = curiopanel.find(_load(ITEM_PANEL))
    assert found is not None and found.readable
    assert len(found.items) == 1
    assert found.usable == (True,)
    item = found.sole_item()
    assert item is not None
    # Between the hand and the X, on the same row.
    assert found.hand[0] < item[0] < found.close[0]
    assert abs(item[1] - found.close[1]) < 0.01


def test_a_dimmed_slot_is_not_something_to_click():
    """The Tent's slot is DRAWN and holds nothing -- `travellers_tent` has no
    item interaction at all. So "a slot exists" is not "an item is in it", and
    reading the first as the second would send a click that does nothing and
    leave the panel standing over the world."""
    found = curiopanel.find(_load(PANEL))
    assert found.items and found.usable == (False,)
    assert found.sole_item() is None


def test_a_second_lit_slot_would_be_refused_rather_than_guessed():
    """Eight of sixty curios offer TWO usable provisions and no capture here
    holds one. `bridge/curios.py` names a single provision, so with two lit
    slots nothing on screen says which is which -- and clicking the wrong one
    spends the wrong provision. `sole_item` refuses; it does not pick."""
    two = curiopanel.CurioButtons(hand=(0.5, 0.4), close=(0.7, 0.4),
                                  items=((0.58, 0.4), (0.64, 0.4)),
                                  usable=(True, True))
    assert two.sole_item() is None
    one = curiopanel.CurioButtons(hand=(0.5, 0.4), close=(0.7, 0.4),
                                  items=((0.58, 0.4), (0.64, 0.4)),
                                  usable=(False, True))
    assert one.sole_item() == (0.64, 0.4)


def test_an_unreadable_row_still_reports_the_X():
    """A red button with no gold plate beside it is not a panel -- but the X
    is still where it was found, and closing something you cannot read is the
    safe move. `readable` is what callers gate the hand and the item on."""
    buttons = curiopanel.CurioButtons(hand=None, close=(0.87, 0.42))
    assert not buttons.readable
    assert buttons.sole_item() is None
    assert "--" in buttons.line()


def test_the_plate_test_rejects_the_frames_the_red_X_rule_accepts():
    """THE MEASUREMENT THE BAND IS SET BY, as a test rather than a comment.

    Over the frames this repo holds, the red-X rule alone fires on the pause
    menu, the raid-results page and two dozen Hamlet screens. Every one of
    them has something warm and lit one or two pitches from that X. Only the
    band separates them, so these are the readings that must stay outside it.
    """
    lo, hi = curiopanel.HAND_WARM_BAND
    real = (0.114, 0.115)                      # Tent, Shipwreck
    contaminants = (0.067,                     # p1_after_provision, slot -2
                    0.130, 0.135,              # the Hamlet's own furniture
                    0.486, 0.510)              # pause menu / raid results
    for v in real:
        assert lo <= v <= hi, v
    for v in contaminants:
        assert not lo <= v <= hi, v


@pytest.mark.skipif(not ITEM_PANEL.exists(), reason="obstacle capture absent")
def test_both_real_panels_put_the_hand_at_the_same_place():
    """Two curios, two window sizes, one geometry. If the row were laid out
    per curio -- which is what a variable number of item slots would mean --
    these two would not land on the same fraction."""
    a = curiopanel.find(_load(PANEL))
    b = curiopanel.find(_load(ITEM_PANEL))
    assert abs(a.hand[0] - b.hand[0]) < 0.002
    assert abs(a.close[0] - b.close[0]) < 0.002
    assert abs(a.items[0][0] - b.items[0][0]) < 0.002


def test_an_x_merged_with_red_rubble_is_still_found():
    """Live 2026-09-27: a Rubble panel's X shared its rows with red rubble and
    joined a 347-px cluster; the party pressed W at an open panel for a whole
    iteration. The tight-gap fallback reads it."""
    frame = CAPTURES / "curio_rubble_merged_x.png"
    found = curiopanel.find(_load(frame))
    assert found is not None and found.readable
    x_px = found.close[0] * 1280
    assert abs(x_px - 974) < 4
    assert found.sole_item() is not None



if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))


def test_the_button_floor_sits_between_the_two_populations():
    """SIZE is what separates the X from red furniture, and by how much.

    The button is drawn at a constant fraction of width -- 28 px of 1280 and
    30 px of 1366 -- so the floor is a fraction too. What it has to clear is
    the two false positives banked from live play: an 11 px corridor
    decoration and the 14 px party marker on the map panel.

    Measured off the fixtures rather than restated, so a re-measurement of the
    panel moves this test with it.

    **Measured on the block `find` would KEEP, not on every fragment**
    (2026-09-07). Row-splitting broke the merged clusters into their parts,
    and a real panel frame legitimately contains small ones -- an 8 px chip of
    the same red. Requiring the smallest of those to clear the floor was only
    ever true because the merge had swallowed them, and it says nothing about
    the button.
    """
    def kept(img):
        h, w, _ = img.shape
        min_px = curiopanel.MIN_PX_AT_720P * (w * h) / (1280 * 720)
        return [blk[2] / w for blk in curiopanel._red_blocks(img)
                if curiopanel.ASPECT_MIN <= blk[4] <= curiopanel.ASPECT_MAX
                and blk[2] <= curiopanel.BUTTON_MAX_FRAC * w
                and blk[3] >= min_px
                and not curiopanel._in_map_panel(blk[0], blk[1], w, h)]

    real = []
    for path in (CAPTURES / "curio_panel.png",
                 CAPTURES / "obstacle_panel_shovel.png"):
        if not path.exists():
            continue
        found = curiopanel.find(_load(path))
        assert found is not None and found.readable, path
        widths = kept(_load(path))
        assert widths, path
        real.append(max(widths))
    assert real, "no banked panel to measure the button on"
    assert min(real) > curiopanel.BUTTON_MIN_FRAC, (
        f"the floor {curiopanel.BUTTON_MIN_FRAC} is above a REAL button at "
        f"{min(real):.4f}")


# -- the map panel was hiding the X (2026-09-07) --------------------------

DARK_PANEL = CAPTURES / "curio_panel_dark.png"


@pytest.mark.skipif(not DARK_PANEL.exists(), reason="frame not banked")
def test_a_panel_at_TORCH_ZERO_is_still_a_panel():
    """The frame Fran opened by hand, after a raid spent looking past it.

    Three attempts at `locked_strongbox` reported `no panel on screen after W`
    and the curio was given up on for the raid -- while the cleanup path,
    looking a minute later, found the very same panel and closed it. Two
    readers of one screen disagreeing is the tell.

    The cause was not the dark and not the detector's thresholds: 144 red
    pixels sat in a 28x28 box at (974, 308), clearing every filter. It was the
    CLUSTERING. `_red_blocks` grouped on x alone, so the X merged with the map
    panel's quest tokens four hundred pixels below it, and the merged block's
    aspect collapsed to nothing.
    """
    found = curiopanel.find(_load(DARK_PANEL))
    assert found is not None and found.readable, found
    h, w, _ = _load(DARK_PANEL).shape
    assert abs(found.close[0] * w - 974) < 4
    assert abs(found.close[1] * h - 308) < 4
    assert found.hand is not None and found.hand[0] < found.close[0]


@pytest.mark.skipif(not DARK_PANEL.exists(), reason="frame not banked")
def test_a_row_of_red_that_spans_the_HUD_is_not_one_button():
    """The refinement itself, stated so it cannot be undone by a tidy-up: two
    red things at the same x and a long way apart in y are two things."""
    import numpy as np

    img = _load(DARK_PANEL)
    blocks = curiopanel._red_blocks(img)
    tall = [blk for blk in blocks if blk[2] and blk[2] / blk[4] > 200]
    assert not tall, f"a block spans {max(b[2]/b[4] for b in tall):.0f} px of y"
    assert any(abs(blk[0] - 974) < 6 and abs(blk[1] - 308) < 6
               for blk in blocks), "the X is not offered as its own block"
    assert np is not None
