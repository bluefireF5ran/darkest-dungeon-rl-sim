"""Window discovery and the fractional layout.

Two things get pinned here.

**The window rect is not the client rect.** `GetWindowRect` includes the
titlebar and borders; the game renders into the client area. Measured on a real
bordered window while writing this: window `-8,-8 .. 2568,1400` against client
`0,0 .. 2560,1392` -- an 8px origin offset and 16px of size. Using the window
rect would push every click down by roughly a titlebar, which reads as "it
clicks the wrong hero" rather than as a coordinate bug.

**The fractional migration is lossless.** `overlay_layout.json` holds a real
12-click calibration in absolute screen pixels, taken borderless-fullscreen at
2560x1440 -- so at that moment client == screen and dividing by the recorded
`screen` recovers the fraction exactly. If that round trip ever stops being
exact, the calibration has been silently degraded and every marker and click
moves with it.

Window-discovery tests run against whatever windows happen to exist, so they
assert invariants rather than specific windows, and skip when nothing is
available (a headless or locked session).
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from bridge import window as W
from bridge.layout import (
    CALIBRATED_ASPECT, DEFAULT_PATH, FractionalLayout, RANKS, load,
)

pytestmark = pytest.mark.skipif(sys.platform != "win32",
                                reason="win32 window APIs")


# --- window discovery ---

@pytest.fixture(scope="module")
def some_windows():
    wins = W.enumerate_windows()
    if not wins:
        pytest.skip("no visible windows in this session")
    return wins


def test_enumerated_windows_have_a_positive_client_area(some_windows):
    for w in some_windows:
        cw, ch = w.client_size
        assert cw > 0 and ch > 0, w.title


def test_the_client_box_is_never_larger_than_the_window_box(some_windows):
    """Borders and titlebar can only shrink the client area, never grow it."""
    for w in some_windows:
        wl, wt_, wr, wb = w.window
        cl, ct, cr, cb = w.client
        assert cr - cl <= wr - wl, w.title
        assert cb - ct <= wb - wt_, w.title


def test_client_origin_is_in_screen_coordinates(some_windows):
    """`GetClientRect` is client-origin (0,0); the screen position has to come
    from `ClientToScreen`. If that step were skipped every origin would be
    (0,0) and every window would appear to sit at the top-left."""
    origins = {w.client_origin for w in some_windows}
    assert len(origins) > 1 or origins != {(0, 0)}, \
        "every client origin is (0,0) - ClientToScreen is probably not applied"


def test_borderless_means_client_equals_window(some_windows):
    for w in some_windows:
        assert w.borderless == (w.client == w.window), w.title


def test_a_missing_process_is_none_not_an_exception():
    assert W.find_window("definitely_not_a_real_process_xyz.exe") is None


def test_find_window_prefers_the_largest_client_area(some_windows):
    """A process can own a splash or a tooltip host as well as its render
    target; the render target is by far the biggest."""
    by_proc = {}
    for w in some_windows:
        by_proc.setdefault(w.process.lower(), []).append(w)
    multi = [p for p, ws in by_proc.items() if len(ws) > 1 and p]
    if not multi:
        pytest.skip("no process owns two visible windows right now")
    proc = multi[0]
    got = W.find_window(proc)
    biggest = max(by_proc[proc], key=lambda w: w.client_size[0] * w.client_size[1])
    assert got.client_size == biggest.client_size


def test_refresh_returns_current_geometry(some_windows):
    w = some_windows[0]
    again = W.refresh(w)
    if again is None:
        pytest.skip("window closed mid-test")
    assert again.hwnd == w.hwnd


def test_refresh_of_a_dead_window_is_none():
    fake = W.GameWindow(hwnd=0x1, pid=0, title="", process="",
                        window=(0, 0, 1, 1), client=(0, 0, 1, 1))
    assert W.refresh(fake) is None


def test_safe_survives_characters_the_console_cannot_encode():
    """This module hit `UnicodeEncodeError` on its first run, from a terminal
    tab titled with U+25D1. Window titles are arbitrary OS-supplied Unicode."""
    assert W.safe("plain") == "plain"
    out = W.safe("tab ◑ title 中文")
    assert isinstance(out, str) and "title" in out


# --- coordinate conversion ---

def _win(size=(1600, 900), origin=(120, 80)):
    left, top = origin
    return W.GameWindow(hwnd=1, pid=1, title="t", process="p",
                        window=(left - 8, top - 31, left + size[0] + 8,
                                top + size[1] + 8),
                        client=(left, top, left + size[0], top + size[1]))


def test_fraction_to_screen_and_back():
    win = _win()
    assert win.to_screen(0.0, 0.0) == (120, 80)
    assert win.to_screen(1.0, 1.0) == (1720, 980)
    assert win.to_client(*win.to_screen(0.5, 0.5)) == (800, 450)
    fx, fy = win.to_fraction(*win.to_screen(0.25, 0.75))
    assert fx == pytest.approx(0.25, abs=1e-3)
    assert fy == pytest.approx(0.75, abs=1e-3)


def test_client_size_excludes_the_border():
    win = _win(size=(1600, 900))
    assert win.client_size == (1600, 900)
    assert win.window[2] - win.window[0] > 1600


# --- the layout migration ---

def test_the_shipped_calibration_migrates():
    lay = load()
    assert lay is not None and lay.complete
    assert len(lay.hero) == RANKS and len(lay.enemy) == RANKS


def test_migration_is_lossless_against_the_original_pixels():
    """The 12-click calibration must survive the change of coordinate space
    exactly -- otherwise the change quietly costs a recalibration."""
    raw = json.loads(DEFAULT_PATH.read_text(encoding="utf-8"))
    if raw.get("space") == "client":
        pytest.skip("layout is already native-fractional")
    sw, sh = raw["screen"]
    lay = load()
    for key in ("hero", "enemy", "skills"):
        for (fx, fy), (x, y) in zip(getattr(lay, key), raw[key]):
            assert round(fx * sw) == x and round(fy * sh) == y, (key, x, y)


def test_all_fractions_are_inside_the_client_area():
    lay = load()
    for key in ("hero", "enemy", "skills"):
        for fx, fy in getattr(lay, key):
            assert 0.0 <= fx <= 1.0 and 0.0 <= fy <= 1.0, (key, fx, fy)


def test_hero_ranks_sit_left_of_enemy_ranks():
    """A mirrored layout aims every action at the wrong party."""
    lay = load()
    assert max(p[0] for p in lay.hero) < min(p[0] for p in lay.enemy)


def test_the_two_sides_are_roughly_symmetric_about_the_centre():
    """Front ranks face each other across the middle of the screen. A sanity
    check on the migration arithmetic, not on DD's art."""
    lay = load()
    assert lay.hero[0][0] == pytest.approx(1.0 - lay.enemy[0][0], abs=0.05)


def test_resolution_scales_proportionally():
    lay = load()
    a = lay.resolve(_win(size=(800, 450), origin=(0, 0)))
    b = lay.resolve(_win(size=(1600, 900), origin=(0, 0)))
    ax, ay = a.point("enemy", 1)
    bx, by = b.point("enemy", 1)
    assert bx == pytest.approx(ax * 2, abs=2)
    assert by == pytest.approx(ay * 2, abs=2)


def test_resolution_follows_the_window_origin():
    """A windowed game can be dragged; the layout must move with it."""
    lay = load()
    a = lay.resolve(_win(size=(1600, 900), origin=(0, 0)))
    b = lay.resolve(_win(size=(1600, 900), origin=(300, 200)))
    ax, ay = a.point("hero", 0)
    bx, by = b.point("hero", 0)
    assert (bx - ax, by - ay) == (300, 200)


def test_a_matching_aspect_raises_no_warning():
    lay = load()
    r = lay.resolve(_win(size=(1600, 900)))
    assert r.aspect_warning is None


def test_a_wrong_aspect_is_reported_not_silently_used():
    """DD letterboxes rather than stretching, so at 21:9 the play area is inset
    and a plain fraction lands off-target. Reported, never guessed at."""
    lay = load()
    r = lay.resolve(_win(size=(2560, 1080)))     # 21:9
    assert r.aspect_warning and "aspect" in r.aspect_warning


def test_out_of_range_ranks_and_slots_are_none():
    lay = load()
    r = lay.resolve(_win())
    assert r.point("enemy", 9) is None
    assert r.point("nobody", 0) is None
    assert r.skill_point(0) is None
    assert r.skill_point(99) is None


def test_a_layout_with_no_screen_field_cannot_migrate(tmp_path):
    """An absolute layout with no recorded resolution is unconvertible.
    Guessing one would silently misplace every point."""
    p = tmp_path / "bad.json"
    p.write_text(json.dumps({"hero": [[1, 2]], "enemy": [[3, 4]]}), encoding="utf-8")
    assert FractionalLayout.from_overlay_json(p) is None


def test_a_missing_file_is_none():
    assert FractionalLayout.from_overlay_json(Path("nope_does_not_exist.json")) is None


def test_native_fractional_round_trips(tmp_path):
    lay = load()
    p = tmp_path / "frac.json"
    lay.save(p)
    back = FractionalLayout.from_overlay_json(p)
    assert back.hero == lay.hero and back.enemy == lay.enemy
    assert back.source_aspect == pytest.approx(lay.source_aspect)


def test_the_source_aspect_is_the_calibrated_one():
    assert load().source_aspect == pytest.approx(CALIBRATED_ASPECT, abs=1e-3)


# -- why is there no window? three causes, three remedies ------------------
#
# They reach the caller as one `None`, and reporting them as one line cost a
# boot attempt on 2026-09-06: the game was running, the layout was calibrated,
# and `run.py` printed "the game is not running, or there is no calibrated
# layout". Fran's monitor was unplugged.


def test_a_process_that_is_not_running_says_exactly_that():
    from bridge.window import diagnose
    assert "not running" in diagnose("definitely-not-a-real-process.exe")


def test_the_unplugged_monitor_case_is_named_not_confused_with_absence(
        monkeypatch):
    """With no display attached the process lives, keeps its windows, and
    reports an EMPTY client rect -- so every filter that wants something
    drawable rejects it exactly as it would reject a game that never started.
    The remedy is physical, so the message must say so rather than invite a
    retry."""
    import bridge.window as window

    monkeypatch.setattr(window, "find_window", lambda *a, **k: None)
    monkeypatch.setattr(window, "owned_windows", lambda *a, **k: [1, 2, 3])
    said = window.diagnose("Darkest.exe")
    assert "not running" not in said
    assert "display" in said or "minimis" in said


def test_a_window_that_is_there_is_reported_as_there(monkeypatch):
    import bridge.window as window

    monkeypatch.setattr(window, "find_window", lambda *a, **k: object())
    assert "on screen" in window.diagnose("Darkest.exe")


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v", "-p", "no:xdist"]))
