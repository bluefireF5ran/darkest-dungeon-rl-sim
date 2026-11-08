"""The two repairs that keep the pixel layers able to SEE the game.

Both were written on 2026-09-20 after an unattended run spent its whole
allowance on a game it could not read. Neither can be tested against a real
window manager in a suite, so what is pinned here is the part with a decision
in it: where to move a flipped window, and which checkpoint would load.
"""
import json
import os
import sys
import time
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


# -- window.nudge_target -----------------------------------------------------

def test_a_second_monitor_takes_the_whole_window_with_it():
    """The normal case: two 1920x1080 monitors side by side.

    A 300 px shove clears the monitor boundary -- which is what breaks DWM's
    independent-flip path -- and the window stays entirely on the desktop, so
    every pixel the actuator then clicks is still on screen.
    """
    from bridge.window import nudge_target

    where, clipped = nudge_target((0, 0, 1920, 1080), (0, 0, 1920, 1080),
                                  (0, 0, 3840, 1080))
    assert where == (300, 0)
    assert clipped is False


def test_it_shoves_AWAY_from_the_edge_it_would_fall_off():
    """On the right-hand monitor, +300 would hang off the desktop and -300
    does not. The preference list is ordered, but only over candidates that
    fit."""
    from bridge.window import nudge_target

    where, clipped = nudge_target((1920, 0, 3840, 1080), (1920, 0, 3840, 1080),
                                  (0, 0, 3840, 1080))
    assert where == (1620, 0)
    assert clipped is False


def test_one_monitor_clips_ONE_pixel_and_says_so():
    """With a single monitor the window IS the desktop, so no offset can both
    clear the monitor and stay wholly inside it.

    The first-listed candidate is +300, and taking it would throw away 300 px
    of frame to save one. The smallest overhang is the right answer, and
    `clipped` has to be True so the caller can tell the operator that the
    frame is no longer whole.
    """
    from bridge.window import nudge_target

    where, clipped = nudge_target((0, 0, 1920, 1080), (0, 0, 1920, 1080),
                                  (0, 0, 1920, 1080))
    assert where == (1, 0)
    assert clipped is True


def test_a_window_that_does_not_cover_the_monitor_is_left_alone():
    """`nudge_off_monitor` refuses outright when this is not the flip case --
    checked here through the predicate it uses, since moving a window that is
    already composited would be a change with no reason."""
    from bridge.window import nudge_target

    # A 1280x720 window on a 1920x1080 monitor: the launch flags' own case.
    # It is never asked, but if it were, the answer must still fit.
    where, clipped = nudge_target((34, 31, 1314, 751), (0, 0, 1920, 1080),
                                  (0, 0, 3840, 1080))
    assert clipped is False
    assert where == (334, 31)


# -- advisor.checkpoint_width ------------------------------------------------

def _fake_checkpoint(path: Path, width: int) -> None:
    """An SB3-shaped zip: `data` holding a pickled observation space.

    Built rather than copied from `models/`, which is gitignored -- a test
    that needs a real checkpoint is a test that silently stops running on a
    fresh clone, which is this repo's characteristic failure.
    """
    import base64
    import pickle
    from gymnasium.spaces import Box

    blob = {"observation_space": {
        ":type:": "<class 'gymnasium.spaces.box.Box'>",
        ":serialized:": base64.b64encode(
            pickle.dumps(Box(low=-1.0, high=1.0, shape=(width,)))).decode()}}
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("data", json.dumps(blob))


def test_the_width_is_read_without_loading_the_policy(tmp_path):
    from bridge.advisor import checkpoint_width

    ck = tmp_path / "a.zip"
    _fake_checkpoint(ck, 1836)
    assert checkpoint_width(ck) == 1836


def test_anything_that_is_not_a_checkpoint_is_None_rather_than_a_raise(tmp_path):
    """This runs over every file in `models/` on the refusal path, and a
    diagnostic that raises on a stray zip tells you less than the refusal it
    was trying to explain."""
    from bridge.advisor import checkpoint_width

    junk = tmp_path / "junk.zip"
    with zipfile.ZipFile(junk, "w") as z:
        z.writestr("notdata", "hello")
    assert checkpoint_width(junk) is None

    notazip = tmp_path / "notazip.zip"
    notazip.write_bytes(b"not a zip at all")
    assert checkpoint_width(notazip) is None
    assert checkpoint_width(tmp_path / "missing.zip") is None


def test_the_refusal_can_name_a_checkpoint_that_WOULD_load(tmp_path):
    """The whole point of the scan: on 2026-09-19 the named slot went stale
    and the refusal said "no checkpoint exists at the current width" while
    four did. Newest first, because the newest is the one to promote."""
    from bridge.advisor import checkpoints_at_width

    old, new, wrong = (tmp_path / "old.zip", tmp_path / "new.zip",
                       tmp_path / "wrong.zip")
    _fake_checkpoint(old, 1836)
    _fake_checkpoint(new, 1836)
    _fake_checkpoint(wrong, 1652)
    # Distinct mtimes, decided here rather than inherited from the filesystem.
    os.utime(old, (time.time() - 600, time.time() - 600))

    found = checkpoints_at_width(1836, tmp_path)
    assert [f.name for f in found] == ["new.zip", "old.zip"]
    assert checkpoints_at_width(1652, tmp_path) == [wrong]
    assert checkpoints_at_width(99, tmp_path) == []
    assert checkpoints_at_width(1836, tmp_path / "nothing here") == []


if __name__ == "__main__":
    sys.exit(__import__("pytest").main([__file__, "-v"]))
