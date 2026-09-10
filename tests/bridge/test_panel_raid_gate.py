"""`panel()` answers "map" for a title screen; the SAVE is what settles it.

`bridge/screen.panel()` is a two-way discriminator between the inventory grid
and everything else, so `"map"` means "not an inventory" and nothing more. On
2026-09-03 that turned a game sitting at the front end into a router
complaining it could not fit the map of a dungeon that was not on screen.

**A positive pixel test was attempted and abandoned**, and the negative result
is pinned here so it is not attempted the same way again: over the 72 banked
captures, no single scale-free feature of the panel region separates known maps
from known non-maps, and a three-feature box tuned on 17 hand-labelled captures
then accepts 31 of the 72 including front-end and town screens.

The question was wrong rather than hard. A caller needs to know whether a RAID
is running, which is not a screen question: `persist.raid.json` exists only
during one. These tests pin the gate that follows from that, and above all that
it defaults to the old behaviour.
"""

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from bridge import screen as screen_mod                          # noqa: E402


def _inventory_like():
    """A frame whose panel region carries the inventory's 8x2 column grid."""
    rng = np.random.default_rng(0)
    img = (rng.random((1080, 1920, 3)) * 40).astype(np.uint8)
    h, w = img.shape[:2]
    from bridge.layout import INVENTORY_COLS, INVENTORY_X
    pitch = (INVENTORY_X[1] - INVENTORY_X[0]) * w / INVENTORY_COLS
    for k in range(INVENTORY_COLS + 1):
        x = int(round(INVENTORY_X[0] * w + pitch * k))
        img[:, max(0, x - 1):x + 2] = 255
    return img


def test_a_false_raid_flag_refuses_whatever_the_pixels_say():
    """The whole point: no frame may be called a map when no raid is running.

    Asserted on an INVENTORY frame as well, so the gate is shown to precede
    the pixel test rather than merely agreeing with it on easy input.
    """
    inv = _inventory_like()
    assert screen_mod.panel(inv) == "inventory", "fixture is not inventory-like"
    assert screen_mod.panel(inv, False) == ""
    assert screen_mod.panel(np.zeros((1080, 1920, 3), np.uint8), False) == ""


def test_None_is_the_old_behaviour_exactly():
    """The default must not change the live loop.

    Every existing caller passes nothing, so `None` has to be
    indistinguishable from the pre-gate function on every input.
    """
    for frame in (_inventory_like(),
                  np.zeros((1080, 1920, 3), np.uint8),
                  (np.random.default_rng(1).random((1080, 1920, 3)) * 255).astype(np.uint8)):
        assert screen_mod.panel(frame, None) == screen_mod.panel(frame)


def test_a_true_raid_flag_changes_nothing():
    """The gate may only ever REFUSE. Told a raid is running it must defer to
    the pixels, or it would be a second, weaker classifier."""
    for frame in (_inventory_like(), np.zeros((1080, 1920, 3), np.uint8)):
        assert screen_mod.panel(frame, True) == screen_mod.panel(frame)


def test_the_runner_reports_None_when_it_cannot_know():
    """`None`, never `True`, when there is no raid path.

    A caller that cannot establish the answer must keep the old behaviour
    rather than be told a raid is running -- the failure this whole gate exists
    to stop is something being believed on no evidence.
    """
    from bridge.act import ActRunner
    runner = ActRunner.__new__(ActRunner)
    runner.raid_path = None
    assert runner._raid_is_running() is None


def test_the_runner_reads_the_save_by_EXISTENCE(tmp_path):
    """`persist.raid.json` is written only during a raid, which is how
    `live_state` decides `in_raid`. One stat, no parse -- so a torn or
    half-written save cannot make this throw."""
    from bridge.act import ActRunner
    runner = ActRunner.__new__(ActRunner)
    raid = tmp_path / "persist.raid.json"
    runner.raid_path = raid
    assert runner._raid_is_running() is False
    raid.write_bytes(b"not even valid json")
    assert runner._raid_is_running() is True


if __name__ == "__main__":
    import tempfile
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            if "tmp_path" in _fn.__code__.co_varnames[:_fn.__code__.co_argcount]:
                with tempfile.TemporaryDirectory() as d:
                    _fn(Path(d))
            else:
                _fn()
            print(f"ok {_name}")
