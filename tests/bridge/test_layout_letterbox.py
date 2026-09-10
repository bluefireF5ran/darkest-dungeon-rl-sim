"""Mapping a layout fraction onto a client that is not 16:9.

DD renders at one aspect and pads rather than stretching, so on any other
aspect the picture is inset in the client rect. `play_rect` computes that inset
and `ResolvedLayout._pt` maps every point through it.

**The model is assumed, not measured.** It says DD pads the entire UI
symmetrically around one 16:9 picture. Plenty of games letterbox the world and
anchor HUD furniture to the true screen edges instead — and this HUD has
corner-anchored furniture (`PARTY_ORDER` at x=0.863, `LOOT_CLOSE` at x=0.763).
If DD does that, the correction fixes the board and breaks the HUD.

`test_the_model_is_recorded_as_unverified` is the marker for that: it exists so
the assumption cannot quietly become folklore. Settling it needs one frame at a
non-16:9 size — set the game to 1920x1200 or a 4:3 window, grab it with
`bridge/screen.py`, and check whether a known HUD element sits where this
mapping predicts or where a raw client fraction does.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from bridge.layout import (CALIBRATED_ASPECT, FractionalLayout, ResolvedLayout,
                           PARTY_ORDER, play_rect)


class _Win:
    """The two things `_pt` asks a window for."""

    def __init__(self, w, h, origin=(0, 0)):
        self.client_size = (w, h)
        self.client_origin = origin

    def to_screen(self, fx, fy):
        left, top = self.client_origin
        w, h = self.client_size
        return left + int(round(fx * w)), top + int(round(fy * h))


def _resolved(w, h, origin=(0, 0)):
    return ResolvedLayout(frac=FractionalLayout(), win=_Win(w, h, origin))


# ── the inset itself ──

@pytest.mark.parametrize("w,h", [(2560, 1440), (1920, 1080), (1280, 720),
                                 (3840, 2160)])
def test_a_16_9_client_has_no_inset(w, h):
    """The property that matters most: every setup this has ever run on is
    16:9, and the correction must not move a single pixel on any of them."""
    assert play_rect(w, h) == (0, 0, w, h)


def test_a_wider_client_gets_bars_at_the_sides():
    """21:9 ultrawide. The picture keeps full height and is centred."""
    x, y, w, h = play_rect(3440, 1440)
    assert (w, h) == (2560, 1440)          # 1440 * 16/9
    assert (x, y) == ((3440 - 2560) // 2, 0)


def test_a_taller_client_gets_bars_top_and_bottom():
    """16:10, the common laptop case."""
    x, y, w, h = play_rect(1920, 1200)
    assert (w, h) == (1920, 1080)          # 1920 * 9/16
    assert (x, y) == (0, (1200 - 1080) // 2)


def test_a_four_three_client_is_letterboxed_hard():
    x, y, w, h = play_rect(1600, 1200)
    assert (w, h) == (1600, 900)
    assert (x, y) == (0, 150)


def test_a_degenerate_client_does_not_explode():
    """A zero-sized client happens while a window is being created."""
    assert play_rect(0, 0) == (0, 0, 0, 0)
    assert play_rect(1920, 1080, aspect=0) == (0, 0, 1920, 1080)


# ── the mapping through it ──

@pytest.mark.parametrize("w,h", [(2560, 1440), (1920, 1080), (1280, 720)])
def test_at_16_9_the_mapping_is_exactly_the_plain_one(w, h):
    """`_pt` must be byte-identical to `win.to_screen` at the calibrated
    aspect. If this drifts, the correction has broken the working case to fix
    the broken one."""
    res = _resolved(w, h, origin=(37, 11))
    for frac in (PARTY_ORDER, (0.0, 0.0), (1.0, 1.0), (0.5, 0.5), (0.763, 0.4)):
        assert res._pt(*frac) == res.win.to_screen(*frac), frac


def test_off_aspect_a_point_moves_into_the_play_area():
    """The whole point. On a 16:10 client the raw fraction lands 60px low;
    mapping through the inset puts it where the picture actually is."""
    res = _resolved(1920, 1200)
    raw = res.win.to_screen(*PARTY_ORDER)
    fixed = res._pt(*PARTY_ORDER)
    assert fixed != raw
    assert fixed[0] == raw[0]                    # no horizontal inset at 16:10
    assert fixed[1] == 60 + int(round(PARTY_ORDER[1] * 1080))


def test_the_window_origin_is_still_added():
    """A windowed game sits at an arbitrary desktop position, and the inset is
    inside the client — the two offsets compose rather than replace."""
    res = _resolved(1920, 1200, origin=(300, 150))
    x, y = res._pt(0.5, 0.5)
    assert x == 300 + 960
    assert y == 150 + 60 + 540


def test_off_aspect_still_warns_even_though_it_now_corrects():
    """Correcting is not the same as being verified. The warning has to
    survive the fix, or the assumption becomes invisible."""
    res = _resolved(1920, 1200)
    warning = res.aspect_warning
    assert warning is not None
    assert "1920x1080" in warning and "ASSUMES" in warning
    assert _resolved(2560, 1440).aspect_warning is None


def test_the_model_is_recorded_as_unverified():
    """A marker, not a behaviour. Delete this test when a real non-16:9 frame
    has confirmed that DD pads the HUD as well as the board — and if it turns
    out DD anchors HUD furniture to the screen edges instead, `play_rect` needs
    to apply to board points only."""
    from bridge import layout
    assert "assumed" in layout.play_rect.__doc__.lower()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
