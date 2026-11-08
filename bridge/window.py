"""Find the running game's window and its client rectangle, in screen pixels.

    python bridge/window.py                 # locate Darkest.exe, print its rects
    python bridge/window.py --process notepad.exe --watch 5

## Why this exists

`bridge/overlay.py` has always assumed the game covers the whole desktop: it
sizes its Tk window to `winfo_screenwidth()/height()` at `+0+0`, and
`overlay_layout.json` stores the eight rank points as **absolute screen pixels**
validated against `Layout.screen`. That works for borderless-fullscreen and
fails for a real window, which has a titlebar, a border, an arbitrary position,
and a client area smaller than the screen.

Nothing in the repo could find a window at all -- a grep for `FindWindow`,
`EnumWindows` or `GetWindowRect` across the whole tree returned nothing. The
only `user32` calls were the overlay's own click-through styling and
`GetAsyncKeyState`.

## The distinction that matters: window rect vs client rect

`GetWindowRect` includes the titlebar and borders. The game renders into the
**client** area, so every coordinate the actuator or the overlay cares about is
relative to the client origin, not the window origin. Using the window rect
would offset every click down by the titlebar height -- roughly one rank's worth
of error, i.e. exactly the kind of bug that reads as "it clicks the wrong hero".

`GetClientRect` returns a client-origin box (left/top always 0), so the screen
position of the client area comes from `ClientToScreen` on (0, 0).

DD scales its fixed 16:9 layout to fill whatever client rect it is given -- the
same property that already lets one calibration work across screen resolutions
(`overlay.py`'s "the game's own resolution is irrelevant" note). That is what
makes a *fractional* layout well-defined: a rank sits at the same fraction of
the client area no matter how the window is sized.

Pure ctypes, matching `tools/dbwin_capture.py`'s existing approach. No pywin32.
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes as wt
from dataclasses import dataclass
from typing import Callable, Optional

DEFAULT_PROCESS = "Darkest.exe"

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

user32.EnumWindows.argtypes = [WNDENUMPROC, wt.LPARAM]
user32.EnumWindows.restype = wt.BOOL
user32.IsWindowVisible.argtypes = [wt.HWND]
user32.IsWindowVisible.restype = wt.BOOL
user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
user32.GetWindowRect.restype = wt.BOOL
user32.GetClientRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
user32.GetClientRect.restype = wt.BOOL
user32.ClientToScreen.argtypes = [wt.HWND, ctypes.POINTER(wt.POINT)]
user32.ClientToScreen.restype = wt.BOOL
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.GetWindowThreadProcessId.restype = wt.DWORD
user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.restype = ctypes.c_int
user32.GetForegroundWindow.restype = wt.HWND
user32.IsIconic.argtypes = [wt.HWND]
user32.IsIconic.restype = wt.BOOL
user32.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, wt.UINT]
user32.SetWindowPos.restype = wt.BOOL
user32.MonitorFromWindow.argtypes = [wt.HWND, wt.DWORD]
user32.MonitorFromWindow.restype = wt.HANDLE
user32.GetSystemMetrics.argtypes = [ctypes.c_int]
user32.GetSystemMetrics.restype = ctypes.c_int

SWP_NOSIZE = 0x0001
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
MONITOR_DEFAULTTONEAREST = 0x0002
SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN = 76, 77
SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 78, 79


class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", wt.RECT),
                ("rcWork", wt.RECT), ("dwFlags", wt.DWORD)]


user32.GetMonitorInfoW.argtypes = [wt.HANDLE, ctypes.POINTER(_MONITORINFO)]
user32.GetMonitorInfoW.restype = wt.BOOL

# How far to shove a flipped window. Big enough that no rounding or DPI
# correction can leave it still exactly covering the monitor, small enough
# that on a two-monitor desktop it stays entirely visible -- which is what
# keeps every click the actuator then sends on screen.
FLIP_NUDGE = 300

kernel32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
kernel32.OpenProcess.restype = wt.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [
    wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD)]
kernel32.QueryFullProcessImageNameW.restype = wt.BOOL
kernel32.CloseHandle.argtypes = [wt.HANDLE]


def safe(text: str) -> str:
    """Text that a cp1252 console can actually print.

    Not defensive padding -- this module hit the failure on its first run.
    Window titles are arbitrary OS-supplied Unicode (a terminal tab was titled
    with U+25D1), and `print` on a cp1252 console raises `UnicodeEncodeError`
    rather than mangling. `BRIDGE.md` already carries this rule for
    `Session.emit`, where the same crash killed the daemon poll thread and took
    the bridge silent while the human kept playing. Here it would abort window
    discovery over a character in someone else's title bar.
    """
    enc = (getattr(sys.stdout, "encoding", None) or "utf-8")
    return text.encode(enc, errors="replace").decode(enc, errors="replace")


def process_name(pid: int) -> str:
    """Image name for a pid, or "" if it cannot be opened.

    Same approach as `tools/dbwin_capture.py::process_name`. Kept local rather
    than imported: `bridge/` must not depend on `tools/` (diagnostics), and the
    bridge's rule is that its readers stay cheap and dependency-free.
    """
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return ""
    try:
        size = wt.DWORD(1024)
        buf = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return buf.value.rsplit("\\", 1)[-1]
        return ""
    finally:
        kernel32.CloseHandle(h)


def window_title(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(hwnd, buf, 512)
    return buf.value


@dataclass(frozen=True)
class GameWindow:
    """A located window, with the client area resolved to screen coordinates.

    `client_*` is the region the game actually renders into. Everything the
    actuator and a fractional layout are expressed against is this box, never
    the window box and never the screen.
    """
    hwnd: int
    pid: int
    title: str
    process: str
    # Full window box, titlebar and borders included.
    window: tuple[int, int, int, int]      # (left, top, right, bottom)
    # Client box in SCREEN coordinates.
    client: tuple[int, int, int, int]

    @property
    def client_origin(self) -> tuple[int, int]:
        return self.client[0], self.client[1]

    @property
    def client_size(self) -> tuple[int, int]:
        left, top, right, bottom = self.client
        return right - left, bottom - top

    @property
    def borderless(self) -> bool:
        """True when the client area IS the whole window (no titlebar/border).

        Not required by anything here -- the point of this module is that a
        bordered window works too -- but worth reporting, since it tells you
        whether `-full_as_borderless 1` took effect.
        """
        return self.client == self.window

    def to_screen(self, fx: float, fy: float) -> tuple[int, int]:
        """A client-relative FRACTION (0..1) -> absolute screen pixel.

        This is the whole reason a fractional layout is portable: DD scales its
        16:9 layout to fill the client area, so a rank sits at a fixed fraction
        of it regardless of window size or screen resolution.
        """
        left, top = self.client_origin
        w, h = self.client_size
        return left + int(round(fx * w)), top + int(round(fy * h))

    def to_client(self, sx: int, sy: int) -> tuple[int, int]:
        """Absolute screen pixel -> client-relative pixel (for PostMessage)."""
        left, top = self.client_origin
        return sx - left, sy - top

    def to_fraction(self, sx: int, sy: int) -> tuple[float, float]:
        """Absolute screen pixel -> client-relative fraction.

        Used once, to migrate the existing absolute `overlay_layout.json`
        without throwing away a good calibration.
        """
        cx, cy = self.to_client(sx, sy)
        w, h = self.client_size
        return (cx / w if w else 0.0, cy / h if h else 0.0)

    @property
    def is_foreground(self) -> bool:
        return int(user32.GetForegroundWindow()) == self.hwnd

    @property
    def is_minimised(self) -> bool:
        return bool(user32.IsIconic(self.hwnd))


def _rects(hwnd: int) -> Optional[tuple[tuple[int, int, int, int],
                                        tuple[int, int, int, int]]]:
    wr = wt.RECT()
    cr = wt.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(wr)):
        return None
    if not user32.GetClientRect(hwnd, ctypes.byref(cr)):
        return None
    # GetClientRect is client-origin (left/top are 0), so the screen position
    # has to come from ClientToScreen. Skipping this step is the titlebar-offset
    # bug described in the module docstring.
    origin = wt.POINT(0, 0)
    if not user32.ClientToScreen(hwnd, ctypes.byref(origin)):
        return None
    client = (origin.x, origin.y,
              origin.x + (cr.right - cr.left),
              origin.y + (cr.bottom - cr.top))
    return (wr.left, wr.top, wr.right, wr.bottom), client


def enumerate_windows(predicate: Optional[Callable[[int], bool]] = None
                      ) -> list[GameWindow]:
    """Every visible top-level window with a non-empty client area."""
    found: list[GameWindow] = []

    @WNDENUMPROC
    def _cb(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        if predicate is not None and not predicate(hwnd):
            return True
        rects = _rects(hwnd)
        if rects is None:
            return True
        window, client = rects
        if client[2] - client[0] <= 0 or client[3] - client[1] <= 0:
            return True
        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        found.append(GameWindow(
            hwnd=int(hwnd), pid=int(pid.value), title=window_title(hwnd),
            process=process_name(int(pid.value)), window=window, client=client))
        return True

    user32.EnumWindows(_cb, 0)
    return found


def find_window(process: str = DEFAULT_PROCESS,
                title_contains: Optional[str] = None) -> Optional[GameWindow]:
    """The game's main window, or None if it is not running.

    Matched on the process image name rather than the window title: DD's title
    is localized, and the capture tooling already learned that lesson the hard
    way ("Actor names come out in the game's active language").

    When a process owns several visible windows -- a splash, a tooltip host --
    the largest client area wins. The render target is by far the biggest.
    """
    want = process.lower()
    cands = [w for w in enumerate_windows() if w.process.lower() == want]
    if title_contains:
        cands = [w for w in cands if title_contains.lower() in w.title.lower()]
    if not cands:
        return None
    return max(cands, key=lambda w: w.client_size[0] * w.client_size[1])


def owned_windows(process: str = DEFAULT_PROCESS) -> list[int]:
    """Every top-level window of `process`, INCLUDING undrawable ones.

    `enumerate_windows` drops anything invisible or with an empty client area,
    which is right for finding something to click and wrong for working out
    why there is nothing to click.
    """
    want = process.lower()
    found: list[int] = []

    @WNDENUMPROC
    def _cb(hwnd, _lparam):
        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if process_name(int(pid.value)).lower() == want:
            found.append(int(hwnd))
        return True

    user32.EnumWindows(_cb, 0)
    return found


def diagnose(process: str = DEFAULT_PROCESS) -> str:
    """Why `find_window` came back None, in one sentence you can act on.

    Three causes arrive at the caller as the same `None` and want three
    different remedies, and reporting them as one line cost a boot attempt on
    2026-09-06: the game was running, the layout was calibrated, and the
    message said "the game is not running, or there is no calibrated layout".
    Fran's monitor was unplugged.

    That third case is the one worth naming, because it is invisible from
    here: with no display attached the process lives, keeps its window, and
    reports an EMPTY CLIENT RECT -- so every filter that asks for something
    drawable rejects it, exactly as it would reject a game that had never
    started. It is the same family as the black-frame trap in
    `screen.display_is_live`: the remedy is physical and no amount of retrying
    reaches it.
    """
    if find_window(process) is not None:
        return f"{process} is on screen"
    owned = owned_windows(process)
    if not owned:
        return f"{process} is not running - start the game first"
    return (f"{process} is running and owns {len(owned)} window(s), but none "
            f"of them is drawable: no display is attached, or the window is "
            f"minimised. Plug the monitor back in or restore the window - "
            f"nothing the bridge can send will fix this one.")


def refresh(win: GameWindow) -> Optional[GameWindow]:
    """Re-read the rects for an already-located window.

    A windowed game can be moved or resized at any time, unlike a
    borderless-fullscreen one -- so a cached rect goes stale silently and every
    click lands at an offset. Callers re-poll rather than caching.
    Returns None if the window has gone away.
    """
    rects = _rects(win.hwnd)
    if rects is None:
        return None
    window, client = rects
    if client[2] - client[0] <= 0 or client[3] - client[1] <= 0:
        return None
    return GameWindow(hwnd=win.hwnd, pid=win.pid, title=window_title(win.hwnd),
                      process=win.process, window=window, client=client)



def monitor_rect(win: GameWindow) -> Optional[tuple[int, int, int, int]]:
    """The screen box of the monitor this window mostly sits on."""
    mon = user32.MonitorFromWindow(win.hwnd, MONITOR_DEFAULTTONEAREST)
    if not mon:
        return None
    info = _MONITORINFO()
    info.cbSize = ctypes.sizeof(_MONITORINFO)
    if not user32.GetMonitorInfoW(mon, ctypes.byref(info)):
        return None
    r = info.rcMonitor
    return (r.left, r.top, r.right, r.bottom)


def virtual_screen() -> tuple[int, int, int, int]:
    """The box every monitor together covers, in screen coordinates."""
    x = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
    y = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
    return (x, y,
            x + user32.GetSystemMetrics(SM_CXVIRTUALSCREEN),
            y + user32.GetSystemMetrics(SM_CYVIRTUALSCREEN))


def covers_a_monitor(win: GameWindow) -> bool:
    """True when the window box is EXACTLY one monitor's box.

    That is the condition for DWM's independent-flip path, which is what makes
    `screen.grab` return a pure black frame while the game is visibly running
    -- see `screen.display_is_live`. It is an exact test on purpose: a window
    one pixel smaller than the monitor is composited normally and reads fine.
    """
    mon = monitor_rect(win)
    return mon is not None and tuple(win.window) == mon


def move_to(win: GameWindow, x: int, y: int) -> Optional[GameWindow]:
    """Move the window, keeping its size, and return the re-read rects."""
    if not user32.SetWindowPos(win.hwnd, 0, int(x), int(y), 0, 0,
                               SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE):
        return None
    return refresh(win)


def nudge_target(window: tuple[int, int, int, int],
                 monitor: tuple[int, int, int, int],
                 virtual: tuple[int, int, int, int],
                 by: int = FLIP_NUDGE) -> tuple[tuple[int, int], bool]:
    """Where to put a flipped window: `((x, y), clipped)`.

    Split out from `nudge_off_monitor` so the choice can be tested without a
    window manager -- it is the only part of the repair with a decision in it.

    In preference order, and the first two are the only ones that keep the
    whole window on the virtual desktop. The 1-px fallbacks exist for a
    single-monitor machine, where nothing can both clear the monitor and stay
    wholly inside it; clipping one pixel is the cheapest way to lose the flip
    path, and it costs one pixel of a frame that is currently costing all of
    them. `clipped` says which of the two happened, because the caller has to
    tell the operator.
    """
    wl, wt_, wr, wb = window
    w, h = wr - wl, wb - wt_
    vl, vt, vr, vb = virtual
    candidates = [(wl + by, wt_), (wl - by, wt_),
                  (wl, wt_ + by), (wl, wt_ - by),
                  (wl + 1, wt_), (wl - 1, wt_)]
    def overhang(pos: tuple[int, int]) -> int:
        x, y = pos
        return (max(0, vl - x) + max(0, vt - y)
                + max(0, x + w - vr) + max(0, y + h - vb))

    inside = [c for c in candidates if overhang(c) == 0]
    if inside:
        return inside[0], False
    # NOTHING fits, which is the single-monitor case: the window is exactly
    # the desktop, so every offset hangs off it. Take the SMALLEST overhang
    # rather than the first candidate -- the preference order is about which
    # nudge is nicest when they all fit, and it puts the 300 px one first,
    # which here would throw away 300 px of frame to save one.
    return min(candidates, key=overhang), True


def nudge_off_monitor(win: GameWindow, by: int = FLIP_NUDGE
                      ) -> tuple[Optional[GameWindow], str]:
    """Shove a flip-path window sideways so GDI can read it again.

    **This turns the one fault that ended a whole unattended night into a
    two-line recovery.** A borderless window covering exactly one monitor is
    scanned out directly by DWM, so every capture comes back pure black and
    every pixel layer -- screen recogniser, map panel, action bar -- is blind
    while the game itself is running perfectly. Measured on this machine
    2026-09-20: window (0,0,1920,1080) on a 3840x1080 desktop captured mean
    brightness **0.0**; moved to x=300 the same frame read **11.1** and the
    recogniser named `title`.

    The size is deliberately not touched. It is the game's own Video option,
    the render target is sized from it, and `-size` is accepted and ignored --
    so resizing the window from outside asks the game a question it was never
    asked at launch. Moving asks it nothing.

    Returns `(window, why)`. `window` is None when there was nothing to do or
    the move failed, and `why` always says which.
    """
    mon = monitor_rect(win)
    if mon is None:
        return None, "cannot tell which monitor the window is on"
    if tuple(win.window) != mon:
        return None, "the window does not cover a monitor, so this is not the flip case"
    was = tuple(win.window)
    (x, y), clipped = nudge_target(was, mon, virtual_screen(), by)
    moved = move_to(win, x, y)
    if moved is None:
        return None, f"the move to {x},{y} was refused by the window manager"
    if tuple(moved.window) == mon:
        return None, "the window snapped straight back onto the monitor"
    note = (f"moved the window from {was[0]},{was[1]} to {x},{y} so it no longer "
            f"covers the monitor {mon}")
    if clipped:
        note += (" - NOTE it now hangs off the virtual desktop, so the frame "
                 "is clipped; set a smaller resolution in the game's Video "
                 "options for a clean capture")
    return moved, note


def describe(win: GameWindow) -> str:
    w, h = win.client_size
    wl, wt_, wr, wb = win.window
    aspect = (w / h) if h else 0.0
    return (f"{win.process} (pid {win.pid}) hwnd 0x{win.hwnd:X}\n"
            f"  title       {safe(repr(win.title))}\n"
            f"  window      {wl},{wt_} .. {wr},{wb}  ({wr-wl}x{wb-wt_})\n"
            f"  client      {win.client[0]},{win.client[1]} .. "
            f"{win.client[2]},{win.client[3]}  ({w}x{h})  aspect {aspect:.3f}\n"
            f"  borderless  {win.borderless}\n"
            f"  foreground  {win.is_foreground}   minimised {win.is_minimised}")



# -- keeping the display awake ------------------------------------------------
#
# **A SLEEPING MONITOR IS INVISIBLE TO EVERY CHECK THE BRIDGE HAS** and it cost
# a whole unattended night on 2026-09-21. The game kept running perfectly --
# 16.9% of the GPU's 3D engine, a live `title` screen, PlayFab pinging on
# schedule -- and every `screen.grab` came back pure black, because a window's
# frames stop reaching the surface GDI reads once the display powers down. The
# DESKTOP still read fine, since its last composition is cached, so the usual
# "is anything drawing?" control says yes while the game says no. Four
# iterations were refused, the run was scored `no-game`, and the machine was
# fine the whole time. Fran's one-line fix was "I've moved the mouse to wake up
# the monitor".
#
# `SetThreadExecutionState` is the supported way to say "I am running an
# unattended job, do not power the display down". It is per-THREAD and lasts
# only as long as the process, which is exactly the scope wanted: it changes
# nothing about Fran's power plan and needs no elevation.

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
ES_DISPLAY_REQUIRED = 0x00000002


def keep_display_awake(on: bool = True) -> bool:
    """Ask Windows not to sleep the display (or release the request).

    Returns True when the request was accepted. It does NOT wake a display
    that is already off -- see `wake_display` -- so assert it at the START of
    an unattended run, while somebody is still there to see it begin.
    """
    flags = ES_CONTINUOUS | (ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED if on
                             else 0)
    return bool(kernel32.SetThreadExecutionState(ctypes.c_uint(flags)))


def wake_display() -> bool:
    """Try to wake a display that has already powered down.

    Asserting `ES_DISPLAY_REQUIRED` alone does not do it, so this also sends a
    zero-distance mouse move, which is what a human does by touching the mouse.
    It is deliberately the smallest possible input: the cursor does not move,
    so it cannot disturb a hover the recogniser depends on
    (`park_cursor` exists because a stray tooltip cost two nights).
    """
    ok = keep_display_awake(True)
    try:
        user32.mouse_event(0x0001, 0, 0, 0, 0)          # MOUSEEVENTF_MOVE, 0px
    except Exception:                                    # noqa: BLE001
        return ok
    return ok


def main(argv=None) -> int:
    import argparse
    import time

    ap = argparse.ArgumentParser(description="Locate the game window.")
    ap.add_argument("--process", default=DEFAULT_PROCESS)
    ap.add_argument("--title", default=None, help="also require this in the title")
    ap.add_argument("--list", action="store_true", help="every visible window")
    ap.add_argument("--watch", type=float, default=0.0,
                    help="re-poll for N seconds; move/resize the window to check tracking")
    ap.add_argument("--nudge", action="store_true",
                    help="if the window exactly covers a monitor, shove it "
                         "sideways so GDI can capture it again")
    args = ap.parse_args(argv)

    if args.list:
        for w in sorted(enumerate_windows(),
                        key=lambda w: -(w.client_size[0] * w.client_size[1])):
            cw, ch = w.client_size
            print(safe(f"{w.process:24s} {cw:5d}x{ch:<5d} {w.title[:50]!r}"))
        return 0

    win = find_window(args.process, args.title)
    if win is None:
        print(f"{args.process} not found. Is the game running?")
        print("Tip: `python bridge/window.py --list` shows every visible window.")
        return 1
    print(describe(win))
    print(f"  covers a monitor  {covers_a_monitor(win)}"
          f"   (that is the pure-black capture case)")

    if args.nudge:
        moved, why = nudge_off_monitor(win)
        print()
        print(f"  nudge: {why}")
        if moved is not None:
            win = moved
            print(describe(win))

    if args.watch:
        print(f"\nwatching {args.watch:.0f}s - move or resize the window...")
        end = time.time() + args.watch
        last = None
        while time.time() < end:
            cur = refresh(win)
            if cur is None:
                print("  window closed")
                return 1
            if cur.client != last:
                cw, ch = cur.client_size
                print(f"  client {cur.client[0]:5d},{cur.client[1]:<5d} {cw}x{ch}")
                last = cur.client
            time.sleep(0.25)
    return 0


if __name__ == "__main__":
    sys.exit(main())
