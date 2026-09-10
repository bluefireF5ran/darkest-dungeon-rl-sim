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


def main(argv=None) -> int:
    import argparse
    import time

    ap = argparse.ArgumentParser(description="Locate the game window.")
    ap.add_argument("--process", default=DEFAULT_PROCESS)
    ap.add_argument("--title", default=None, help="also require this in the title")
    ap.add_argument("--list", action="store_true", help="every visible window")
    ap.add_argument("--watch", type=float, default=0.0,
                    help="re-poll for N seconds; move/resize the window to check tracking")
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
