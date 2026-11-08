"""Record how a human plays: every input sent to the game, what was on screen
when it was sent, and every save the game wrote.

Fran, 2026-09-28: *"a logger of the inputs to try to teach the bridge how I
play"*. The audit that morning found the bridge losing on the decisions
AROUND the fights -- quest choice, farming, bosses -- and those are few per
week, so the recording is a spec and a ruler for the decide layers, and a
measurement of pace for the actuator. It is not combat training data.

## What a session holds

    archive/recordings/<YYYYmmdd-HHMMSS>/
        events.jsonl     one JSON object per line, in time order
        frames/          a 640x360 JPEG per mouse press / key press (rate-limited)
        saves/           every save file whose CONTENTS changed, gzipped

`tools/recording_report.py` reads it: a summary, clicks named against the
bridge's own layout, and `--unpack DIR`, which writes the raid/roster/map
triples `bridge/run.py --replay DIR` already knows how to mark.

## Three rules

- **Nothing is recorded unless the game is the focused window.** The hook
  sees the whole desktop's input, and the callback drops anything aimed at
  another window before it is queued -- not even a count survives. A tool that
  can read keystrokes must not be able to read a password typed elsewhere.
- **Raw now, meaning later.** The recorder names the screen and keeps the
  frame; it does not decide which button a click hit. Labelling happens
  offline in the report, so a better labeller re-reads every old session.
- **Input the bridge sent is not the human's.** Injected events carry the
  LLKHF/LLMHF_INJECTED flag and are counted, never recorded as play.

The hook is a low-level Windows hook (`WH_MOUSE_LL` / `WH_KEYBOARD_LL`) on
its own thread with its own message loop; the callback only filters and
queues, because Windows unhooks a low-level hook that is slow to return.
"""
from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
import gzip
import hashlib
import json
import queue
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from bridge.live_state import PROFILE_GLOB, SAVE_ROOT        # noqa: E402

DEFAULT_OUT = REPO / "archive" / "recordings"

#: Minimum seconds between two banked frames. A combat turn is a key and a
#: click inside half a second; both are worth a frame, a double-click is not.
FRAME_MIN_GAP = 0.25
FRAME_SIZE = (640, 360)
#: JPEG, not PNG: a game frame barely compresses losslessly, and a PNG per
#: input is about a gigabyte per evening. At 70 a frame is ~40 KB and still
#: reads every label on the HUD.
FRAME_QUALITY = 70
#: Consecutive all-black grabs before warning. A loading fade is one or two.
BLACK_STREAK = 5
SAVE_POLL = 0.25
WINDOW_POLL = 2.0
#: Stop on our own once the game has been gone this long, having been seen.
GAME_GONE_STOP = 120.0
#: Windows removes a low-level hook whose callback is slow to return, and says
#: nothing: the session keeps banking saves while recording no input at all.
#: The system's own last-input tick is the witness -- every input, moves
#: included, passes through our callbacks, so input newer than our last
#: callback by more than this means the hook is gone. Checked every
#: WINDOW_POLL while the game is focused, and the hook is reinstalled.
HOOK_SLACK_MS = 3000
#: A one-line progress print, so a glance at the console says it is alive.
STATUS_EVERY = 300.0
#: The key that flags "look at this moment" (`tools/recording_report.MARK_KEY`).
MARK_VK = 0x77

WH_KEYBOARD_LL, WH_MOUSE_LL = 13, 14
WM_QUIT = 0x0012
LLKHF_INJECTED, LLMHF_INJECTED = 0x10, 0x01

#: Mouse messages worth keeping. Moves are not: a drag is its press and its
#: release, and a hover is what the frame shows.
MOUSE_MESSAGES = {
    0x0201: ("left", "down"), 0x0202: ("left", "up"),
    0x0204: ("right", "down"), 0x0205: ("right", "up"),
    0x0207: ("middle", "down"), 0x0208: ("middle", "up"),
    0x020A: ("wheel", "wheel"),
}
KEY_DOWN = {0x0100, 0x0104}          # WM_KEYDOWN, WM_SYSKEYDOWN
KEY_UP = {0x0101, 0x0105}            # WM_KEYUP, WM_SYSKEYUP

_NAMED_KEYS = {
    0x08: "backspace", 0x09: "tab", 0x0D: "enter", 0x10: "shift", 0x11: "ctrl",
    0x12: "alt", 0x13: "pause", 0x14: "capslock", 0x1B: "esc", 0x20: "space",
    0x21: "pageup", 0x22: "pagedown", 0x23: "end", 0x24: "home", 0x25: "left",
    0x26: "up", 0x27: "right", 0x28: "down", 0x2D: "insert", 0x2E: "delete",
    0xA0: "lshift", 0xA1: "rshift", 0xA2: "lctrl", 0xA3: "rctrl",
    0xA4: "lalt", 0xA5: "ralt", 0x5B: "lwin", 0x5C: "rwin",
    0xBA: ";", 0xBB: "=", 0xBC: ",", 0xBD: "-", 0xBE: ".", 0xBF: "/",
    0xC0: "`", 0xDB: "[", 0xDC: "\\", 0xDD: "]", 0xDE: "'",
}
MODIFIER_VKS = {0x10, 0x11, 0x12, 0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5,
                0x5B, 0x5C, 0x14}


def key_name(vk: int) -> str:
    """A readable name for a virtual-key code. Unknown codes keep their hex."""
    if 0x30 <= vk <= 0x39 or 0x41 <= vk <= 0x5A:
        return chr(vk).lower()
    if 0x60 <= vk <= 0x69:
        return f"num{vk - 0x60}"
    if 0x70 <= vk <= 0x87:
        return f"f{vk - 0x6F}"
    return _NAMED_KEYS.get(vk, f"vk{vk:#04x}")


def mouse_kind(msg: int) -> Optional[tuple[str, str]]:
    """(button, action) for a mouse message worth recording, else None."""
    return MOUSE_MESSAGES.get(int(msg))


def should_record(foreground_pid: int, game_pid: Optional[int],
                  injected: bool) -> bool:
    """The privacy gate, as a pure function so it can be tested.

    False unless the game is known AND is the process that owns the
    foreground window. An unknown game (not running, not found yet) records
    nothing rather than everything.
    """
    return (not injected) and bool(game_pid) and foreground_pid == game_pid


def hook_is_deaf(last_input_tick: int, hook_tick: int,
                 slack_ms: int = HOOK_SLACK_MS) -> bool:
    """True when the system saw input the hook never did.

    Both are GetTickCount milliseconds, which wrap every 49.7 days, so the
    lag is taken modulo 2**32 and a "negative" lag (the hook is ahead, the
    normal case) is never deaf.
    """
    lag = (int(last_input_tick) - int(hook_tick)) & 0xFFFFFFFF
    return slack_ms < lag < 0x80000000


# ---------------------------------------------------------------------------
# The event log
# ---------------------------------------------------------------------------

class EventLog:
    """Append-only JSONL, one line per event, flushed per line.

    Flushed because the useful sessions are the long ones, and a crash or a
    closed terminal must not cost the whole file.
    """

    def __init__(self, path: Path, start: float):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.start = start
        self._lock = threading.Lock()
        self._fh = open(self.path, "a", encoding="utf-8")
        self.counts: dict[str, int] = {}

    def write(self, kind: str, t: Optional[float] = None, **data) -> None:
        t = time.time() if t is None else t
        rec = {"t": round(t, 3), "dt": round(t - self.start, 3), "kind": kind}
        rec.update(data)
        line = json.dumps(rec, ensure_ascii=False, default=str)
        with self._lock:
            self._fh.write(line + "\n")
            self._fh.flush()
            self.counts[kind] = self.counts.get(kind, 0) + 1

    def close(self) -> None:
        with self._lock:
            try:
                self._fh.close()
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Save snapshots
# ---------------------------------------------------------------------------

@dataclass
class SaveWatcher:
    """Copy every save file whose CONTENTS changed, gzipped, into `out`.

    - **Content, not mtime.** The game rewrites files it did not change; a
      hash keeps the session to real changes.
    - **Stable before copied.** A file is copied on the poll AFTER its
      (mtime, size) stopped moving, so a half-written save is not banked.
    - **The whole profile once, the first time it moves.** Replaying needs the
      roster beside the raid, and a profile that only ever wrote its raid file
      would otherwise have no roster in the session at all. Profiles that
      never move cost nothing -- a new campaign made mid-session is found the
      same way, by its first write.
    """
    out: Path
    root: Path = SAVE_ROOT
    glob: str = PROFILE_GLOB
    seq: int = 0
    _stat: dict = field(default_factory=dict)       # path -> (mtime, size)
    _pending: dict = field(default_factory=dict)    # path -> (mtime, size)
    _hash: dict = field(default_factory=dict)       # path -> sha1
    _active: set = field(default_factory=set)       # profile dirs baselined

    def profiles(self) -> list[Path]:
        return sorted(p for p in Path(self.root).glob(self.glob) if p.is_dir())

    @staticmethod
    def _files(profile: Path) -> list[Path]:
        return sorted(profile.glob("persist.*.json"))

    def prime(self) -> None:
        """Remember what is on disk now, without copying any of it."""
        for prof in self.profiles():
            for f in self._files(prof):
                try:
                    st = f.stat()
                except OSError:
                    continue
                self._stat[f] = (st.st_mtime, st.st_size)

    def poll(self) -> list[dict]:
        """One pass. Returns a record per file banked."""
        banked: list[dict] = []
        for prof in self.profiles():
            for f in self._files(prof):
                try:
                    st = f.stat()
                except OSError:
                    continue
                sig = (st.st_mtime, st.st_size)
                if self._stat.get(f) == sig and f not in self._pending:
                    continue
                if self._pending.get(f) != sig:
                    self._pending[f] = sig          # still moving: wait a poll
                    self._stat[f] = sig
                    continue
                del self._pending[f]
                if prof not in self._active:
                    self._active.add(prof)
                    banked.extend(self._baseline(prof, skip=f))
                rec = self._bank(prof, f)
                if rec:
                    banked.append(rec)
        return banked

    def _baseline(self, prof: Path, skip: Path) -> list[dict]:
        out = []
        for f in self._files(prof):
            if f == skip:
                continue
            rec = self._bank(prof, f, baseline=True)
            if rec:
                out.append(rec)
        return out

    def _bank(self, prof: Path, f: Path, baseline: bool = False
              ) -> Optional[dict]:
        data = None
        for _ in range(3):
            try:
                data = f.read_bytes()
                break
            except OSError:
                time.sleep(0.05)
        if data is None:
            return None
        digest = hashlib.sha1(data).hexdigest()
        if self._hash.get(f) == digest:
            return None
        self._hash[f] = digest
        self.seq += 1
        name = f"{self.seq:06d}_{prof.name}__{f.name}.gz"
        self.out.mkdir(parents=True, exist_ok=True)
        (self.out / name).write_bytes(gzip.compress(data, 6))
        return {"profile": prof.name, "file": f.name, "snap": f"saves/{name}",
                "bytes": len(data), "baseline": baseline}


# ---------------------------------------------------------------------------
# The game window, shared between threads
# ---------------------------------------------------------------------------

class Gate:
    """What the hook callback needs, updated off the hook thread."""

    def __init__(self):
        self.win = None                 # bridge.window.GameWindow or None
        self.pid: Optional[int] = None
        self.seen = False
        self.last_seen = 0.0

    def refresh(self) -> None:
        from bridge import window as window_mod     # noqa: PLC0415
        try:
            win = window_mod.find_window()
        except Exception:                           # noqa: BLE001
            win = None
        self.win = win
        self.pid = win.pid if win else None
        if win:
            self.seen = True
            self.last_seen = time.time()


def _foreground_pid(user32) -> int:
    hwnd = user32.GetForegroundWindow()
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value)


# ---------------------------------------------------------------------------
# The hook thread
# ---------------------------------------------------------------------------

class _KBD(ctypes.Structure):
    _fields_ = [("vkCode", wt.DWORD), ("scanCode", wt.DWORD),
                ("flags", wt.DWORD), ("time", wt.DWORD),
                ("dwExtraInfo", ctypes.c_size_t)]


class _MSE(ctypes.Structure):
    _fields_ = [("pt", wt.POINT), ("mouseData", wt.DWORD),
                ("flags", wt.DWORD), ("time", wt.DWORD),
                ("dwExtraInfo", ctypes.c_size_t)]


class InputHook(threading.Thread):
    """Installs both low-level hooks and pumps messages until stopped."""

    def __init__(self, gate: Gate, sink: "queue.Queue",
                 accept_injected: bool = False):
        super().__init__(daemon=True, name="recorder-hook")
        self.gate = gate
        self.sink = sink
        # Only `--selftest` sets this: it proves the hook-to-queue path with
        # synthetic input, which Windows always flags as injected.
        self.accept_injected = accept_injected
        self.injected = 0
        self.ignored = 0
        self._tid = None
        self._held: set[int] = set()
        self.ready = threading.Event()
        self.error: Optional[str] = None
        #: The input tick of the last callback of ANY kind, moves included --
        #: what `hook_is_deaf` compares against the system's last input.
        self.last_tick = 0

    def run(self) -> None:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        LRESULT = ctypes.c_ssize_t
        PROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wt.WPARAM, wt.LPARAM)
        user32.SetWindowsHookExW.argtypes = [ctypes.c_int, PROC, wt.HINSTANCE,
                                             wt.DWORD]
        user32.SetWindowsHookExW.restype = wt.HHOOK
        user32.CallNextHookEx.argtypes = [wt.HHOOK, ctypes.c_int, wt.WPARAM,
                                          wt.LPARAM]
        user32.CallNextHookEx.restype = LRESULT
        user32.UnhookWindowsHookEx.argtypes = [wt.HHOOK]
        kernel32.GetModuleHandleW.restype = wt.HMODULE
        kernel32.GetTickCount.restype = wt.DWORD
        self._tid = kernel32.GetCurrentThreadId()

        def mouse_cb(code, wparam, lparam):
            try:
                if code >= 0:
                    m = ctypes.cast(lparam, ctypes.POINTER(_MSE)).contents
                    self.last_tick = int(m.time)
                    kind = mouse_kind(wparam)
                    if kind is not None:
                        injected = (bool(m.flags & LLMHF_INJECTED)
                                    and not self.accept_injected)
                        if should_record(_foreground_pid(user32), self.gate.pid,
                                         injected):
                            delta = ctypes.c_short(m.mouseData >> 16).value \
                                if kind[0] == "wheel" else 0
                            self.sink.put((time.time(), "mouse", kind[0],
                                           kind[1], m.pt.x, m.pt.y, delta,
                                           _mods(user32)))
                        elif injected:
                            self.injected += 1
            except Exception:                           # noqa: BLE001
                pass
            return user32.CallNextHookEx(None, code, wparam, lparam)

        def key_cb(code, wparam, lparam):
            try:
                if code >= 0:
                    self.last_tick = int(ctypes.cast(
                        lparam, ctypes.POINTER(_KBD)).contents.time)
                if code >= 0 and (wparam in KEY_DOWN or wparam in KEY_UP):
                    k = ctypes.cast(lparam, ctypes.POINTER(_KBD)).contents
                    injected = (bool(k.flags & LLKHF_INJECTED)
                                and not self.accept_injected)
                    if should_record(_foreground_pid(user32), self.gate.pid,
                                     injected):
                        vk = int(k.vkCode)
                        if wparam in KEY_DOWN:
                            if vk not in self._held:     # drop auto-repeat
                                self._held.add(vk)
                                self.sink.put((time.time(), "key", vk, "down",
                                               0, 0, 0, _mods(user32)))
                        else:
                            self._held.discard(vk)
                            self.sink.put((time.time(), "key", vk, "up",
                                           0, 0, 0, _mods(user32)))
                    elif injected:
                        self.injected += 1
                    else:
                        self._held.discard(int(k.vkCode))
            except Exception:                           # noqa: BLE001
                pass
            return user32.CallNextHookEx(None, code, wparam, lparam)

        # Held on self: a WINFUNCTYPE that is garbage collected while hooked
        # takes the whole desktop's input down with it.
        self._mouse_proc = PROC(mouse_cb)
        self._key_proc = PROC(key_cb)
        hmod = kernel32.GetModuleHandleW(None)
        hm = user32.SetWindowsHookExW(WH_MOUSE_LL, self._mouse_proc, hmod, 0)
        hk = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._key_proc, hmod, 0)
        if not hm or not hk:
            self.error = f"SetWindowsHookEx failed ({ctypes.get_last_error()})"
            self.ready.set()
            return
        # Input older than the install is not input this hook missed.
        self.last_tick = int(kernel32.GetTickCount())
        self.ready.set()
        msg = wt.MSG()
        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            user32.UnhookWindowsHookEx(hm)
            user32.UnhookWindowsHookEx(hk)

    def stop(self) -> None:
        if self._tid:
            ctypes.windll.user32.PostThreadMessageW(self._tid, WM_QUIT, 0, 0)


def _mods(user32) -> list[str]:
    out = []
    for vk, name in ((0x10, "shift"), (0x11, "ctrl"), (0x12, "alt")):
        if user32.GetKeyState(vk) & 0x8000:
            out.append(name)
    return out


# ---------------------------------------------------------------------------
# The worker: raw input -> an event with a screen name and a frame
# ---------------------------------------------------------------------------

class Annotator:
    """Turns queued raw input into events. Owns the grabs and the frames."""

    def __init__(self, gate: Gate, log: EventLog, frames: Optional[Path],
                 say=print, on_mark=None):
        self.gate = gate
        self.log = log
        self.frames = frames
        self.say = say
        # F8 lands with no sign in the game, so a mark is acknowledged by ear:
        # the player is looking at the game, not at this console.
        self.on_mark = _beep if on_mark is None else on_mark
        self.marks = 0
        self._last_grab = 0.0
        self._last_screen = ""
        self._last_frame = ""
        self.frame_count = 0
        self._warned_black = False
        self._black_run = 0

    def _look(self, now: float) -> tuple[str, str]:
        """(screen name, frame path) -- fresh unless one was just taken."""
        if now - self._last_grab < FRAME_MIN_GAP:
            return self._last_screen, self._last_frame
        self._last_grab = now
        win = self.gate.win
        if win is None:
            return "", ""
        try:
            from bridge import screen as screen_mod     # noqa: PLC0415
            img = screen_mod.grab(win)
        except Exception:                               # noqa: BLE001
            img = None
        if img is None:
            self._last_screen, self._last_frame = "", ""
            return "", ""
        self._black_run = self._black_run + 1 if int(img.max()) == 0 else 0
        if not self._warned_black and self._black_run >= BLACK_STREAK:
            # A borderless/fullscreen game covering its monitor is scanned out
            # past the compositor and every grab is black (bridge/CLAUDE.md,
            # "LAUNCH WITH -size 1280 720"). Inputs and saves still record;
            # the frames and screen names would silently be empty.
            # A STREAK, not one frame: the first real session warned on a
            # loading-screen fade at 43 s and then banked 2,489 good frames.
            self._warned_black = True
            self.log.write("warning", what="black_frame")
            self.say("  WARNING: the screenshots are black -- run the game "
                     "windowed (-fullscreen 0 -size 1280 720) to get frames")
        try:
            name = screen_mod.recognise(img) or ""
        except Exception:                               # noqa: BLE001
            name = ""
        rel = ""
        if self.frames is not None:
            rel = self._save_frame(img, now)
        self._last_screen, self._last_frame = name, rel
        return name, rel

    def _save_frame(self, img, now: float) -> str:
        try:
            from PIL import Image                       # noqa: PLC0415
        except Exception:                               # noqa: BLE001
            return ""
        try:
            self.frames.mkdir(parents=True, exist_ok=True)
            self.frame_count += 1
            name = f"{self.frame_count:06d}_{now - self.log.start:09.2f}.jpg"
            Image.fromarray(img).resize(FRAME_SIZE).save(
                self.frames / name, quality=FRAME_QUALITY)
            return f"frames/{name}"
        except Exception:                               # noqa: BLE001
            return ""

    def handle(self, raw: tuple) -> None:
        t, source, a, action, sx, sy, delta, mods = raw
        win = self.gate.win
        if source == "mouse":
            data = {"button": a, "action": action, "mods": mods}
            if win is not None:
                cx, cy = sx - win.client[0], sy - win.client[1]
                w, h = win.client_size
                data.update(x=cx, y=cy, w=w, h=h,
                            fx=round(cx / w, 4) if w else None,
                            fy=round(cy / h, 4) if h else None)
            if a == "wheel":
                data["delta"] = delta
            if action in ("down", "wheel"):
                data["screen"], data["frame"] = self._look(t)
            else:
                data["screen"] = self._last_screen
            self.log.write("mouse", t=t, **data)
        else:
            vk = int(a)
            data = {"vk": vk, "key": key_name(vk), "action": action,
                    "mods": mods}
            if action == "down" and vk not in MODIFIER_VKS:
                data["screen"], data["frame"] = self._look(t)
            self.log.write("key", t=t, **data)
            if action == "down" and vk == MARK_VK:
                self.marks += 1
                self.say(f"  mark {self.marks} at "
                         f"{(t - self.log.start) / 60:.1f} min")
                try:
                    self.on_mark()
                except Exception:                       # noqa: BLE001
                    pass


def _beep() -> None:
    import winsound                                     # noqa: PLC0415
    winsound.MessageBeep(winsound.MB_OK)


def _last_input_tick() -> Optional[int]:
    """GetLastInputInfo's tick: the system's own record of the last input."""
    class _LII(ctypes.Structure):
        _fields_ = [("cbSize", wt.UINT), ("dwTime", wt.DWORD)]
    lii = _LII(cbSize=ctypes.sizeof(_LII))
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii)):
        return None
    return int(lii.dwTime)


# ---------------------------------------------------------------------------
# The session
# ---------------------------------------------------------------------------

def start_trace(session: Path, say=print):
    """Run `tools/dbwin_capture.py` beside the recorder, into this session.

    The game logs every attack's `Chance to hit`, `Roll to hit` and damage
    draw over OutputDebugString with no launch flag (DECOMP.md F3). Stamped on
    the recorder's clock, those lines pair with the saves banked here, which
    is what `tools/damage_oracle.py` needs to grade F9's damage rule against
    the running game. Only one reader can own the debug channel, so a capture
    that dies at once (DebugView open, a second recorder) is reported, and the
    session carries on without it.
    """
    import subprocess                                   # noqa: PLC0415
    session.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, str(REPO / "tools" / "dbwin_capture.py"),
           "--process", "Darkest.exe", "--quiet", "--stamp",
           "--out", str(session / "trace.log")]
    console = open(session / "trace_console.log", "w", encoding="utf-8")
    proc = subprocess.Popen(cmd, stdout=console, stderr=subprocess.STDOUT)
    time.sleep(1.5)
    if proc.poll() is not None:
        console.close()
        say(f"  debug-channel capture did not start (exit {proc.returncode}); "
            f"see {session / 'trace_console.log'} -- recording without it")
        return None
    say(f"  capturing the game's debug channel -> {session / 'trace.log'}")
    proc._console = console                             # closed in stop_trace
    return proc


def stop_trace(proc) -> None:
    if proc is None:
        return
    proc.terminate()
    try:
        proc.wait(5)
    except Exception:                                   # noqa: BLE001
        proc.kill()
    proc._console.close()


def run(out_root: Path = DEFAULT_OUT, duration: float = 12 * 3600,
        frames: bool = True, say=print, trace: bool = False) -> Path:
    start = time.time()
    session = Path(out_root) / time.strftime("%Y%m%d-%H%M%S",
                                             time.localtime(start))
    log = EventLog(session / "events.jsonl", start)
    tracer = start_trace(session, say) if trace else None
    if trace:
        log.write("trace", state="on" if tracer else "failed")
    gate = Gate()
    gate.refresh()
    saves = SaveWatcher(out=session / "saves")
    saves.prime()
    raw: "queue.Queue" = queue.Queue()
    hook = InputHook(gate, raw)
    annot = Annotator(gate, log, session / "frames" if frames else None,
                      say=say)

    log.write("session_start", version=1, out=str(session),
              game=_describe(gate.win), frames=frames,
              profiles=[p.name for p in saves.profiles()])
    say(f"recording to {session}")
    say("  only while Darkest Dungeon is the focused window; "
        "Ctrl+C here to stop")
    if gate.win is None:
        say("  (the game is not running yet -- start it whenever you like)")
    else:
        _check_flip(gate.win, log, say)

    hook.start()
    hook.ready.wait(5.0)
    if hook.error:
        log.write("error", where="hook", detail=hook.error)
        say(f"  input hook failed: {hook.error} -- recording saves only")
    injected = 0

    focused = False
    next_window = 0.0
    next_save = 0.0
    next_status = start + STATUS_EVERY
    try:
        while time.time() - start < duration:
            now = time.time()
            if now >= next_window:
                next_window = now + WINDOW_POLL
                had = gate.win is not None
                gate.refresh()
                if gate.win is not None and not had:
                    log.write("game", state="found", game=_describe(gate.win))
                    say("  game found")
                    _check_flip(gate.win, log, say)
                elif gate.win is None and had:
                    log.write("game", state="gone")
                    say("  game gone")
                if (gate.seen and gate.win is None
                        and now - gate.last_seen > GAME_GONE_STOP):
                    say("  the game has been closed for a while -- stopping")
                    break
                last = _last_input_tick() if focused and not hook.error                     else None
                if last is not None and hook_is_deaf(last, hook.last_tick):
                    log.write("error", where="hook", detail="hook_lost",
                              lag_ms=(last - hook.last_tick) & 0xFFFFFFFF)
                    say("  the input hook went deaf (Windows drops a slow "
                        "one) -- reinstalling it")
                    hook.stop()
                    hook.join(2.0)
                    injected += hook.injected
                    hook = InputHook(gate, raw)
                    hook.start()
                    hook.ready.wait(5.0)
                    if hook.error:
                        log.write("error", where="hook", detail=hook.error)
                        say(f"  reinstall failed: {hook.error} -- recording "
                            "saves only")
            if now >= next_status:
                next_status = now + STATUS_EVERY
                say(f"  {(now - start) / 60:.0f} min: "
                    f"{log.counts.get('mouse', 0)} mouse, "
                    f"{log.counts.get('key', 0)} key, "
                    f"{log.counts.get('save', 0)} saves, "
                    f"{annot.frame_count} frames, {annot.marks} marks")
            fg = False
            if gate.pid:
                try:
                    fg = _foreground_pid(ctypes.windll.user32) == gate.pid
                except Exception:                       # noqa: BLE001
                    fg = False
            if fg != focused:
                focused = fg
                log.write("focus", state="in" if fg else "out")
            if now >= next_save:
                next_save = now + SAVE_POLL
                for rec in saves.poll():
                    log.write("save", **rec)
            try:
                while True:
                    annot.handle(raw.get(timeout=0.05))
            except queue.Empty:
                pass
    except KeyboardInterrupt:
        say("  stopped")
    finally:
        stop_trace(tracer)
        hook.stop()
        hook.join(2.0)
        try:
            while True:
                annot.handle(raw.get_nowait())
        except queue.Empty:
            pass
        for rec in saves.poll() + saves.poll():
            log.write("save", **rec)
        log.write("session_end", seconds=round(time.time() - start, 1),
                  counts=dict(log.counts), frames=annot.frame_count,
                  injected_ignored=injected + hook.injected,
                  marks=annot.marks)
        log.close()
    say(f"  {log.counts.get('mouse', 0)} mouse, {log.counts.get('key', 0)} "
        f"key, {log.counts.get('save', 0)} save events, "
        f"{annot.frame_count} frames -> {session}")
    return session


def _check_flip(win, log: EventLog, say) -> None:
    """Say it NOW if the frames are going to be black or frozen.

    A game window that covers its monitor exactly is scanned out past the
    compositor (bridge/CLAUDE.md, "LAUNCH WITH -size 1280 720"): every grab
    is black or, worse, frozen on an old frame the recogniser still names.
    The recorder never moves the window -- it sends nothing -- so it warns
    while there is still time to relaunch, instead of after an evening.
    """
    from bridge import window as window_mod             # noqa: PLC0415
    try:
        covers = window_mod.covers_a_monitor(win)
    except Exception:                                   # noqa: BLE001
        return
    if covers:
        log.write("warning", what="covers_monitor")
        say("  WARNING: the game covers its monitor exactly, so screenshots "
            "will be black or frozen.")
        say("           Inputs and saves still record. For frames, set the "
            "Steam launch options to")
        say("           -size 1280 720   (two tokens) and restart the game.")


def _describe(win) -> Optional[dict]:
    if win is None:
        return None
    return {"pid": win.pid, "title": win.title, "client": list(win.client),
            "size": list(win.client_size)}


def selftest(say=print) -> int:
    """Prove hook -> queue works, with no game and no risky input.

    Treats whatever window is focused as "the game" for a second, accepts
    injected input, and sends a SHIFT tap and one wheel notch up then down --
    inputs that change nothing in any window. Passes when both arrive.
    Run it before trusting a session that recorded nothing: otherwise a dead
    hook is indistinguishable from "I did not play".
    """
    user32 = ctypes.windll.user32

    class _Gate(Gate):
        def refresh(self):                      # "the game" = the foreground
            self.pid = _foreground_pid(user32)

    gate = _Gate()
    gate.refresh()
    sink: "queue.Queue" = queue.Queue()
    hook = InputHook(gate, sink, accept_injected=True)
    hook.start()
    hook.ready.wait(5.0)
    if hook.error:
        say(f"FAIL: {hook.error}")
        return 1
    time.sleep(0.2)

    class _KI(ctypes.Structure):
        _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD),
                    ("time", wt.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

    class _MI(ctypes.Structure):
        _fields_ = [("dx", wt.LONG), ("dy", wt.LONG), ("mouseData", wt.DWORD),
                    ("dwFlags", wt.DWORD), ("time", wt.DWORD),
                    ("dwExtraInfo", ctypes.c_size_t)]

    class _U(ctypes.Union):
        _fields_ = [("ki", _KI), ("mi", _MI), ("pad", ctypes.c_byte * 32)]

    class _IN(ctypes.Structure):
        _fields_ = [("type", wt.DWORD), ("u", _U)]

    def send(inp):
        user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_IN))

    for flags in (0, 0x0002):                     # SHIFT down, SHIFT up
        inp = _IN(type=1)
        inp.u.ki = _KI(wVk=0x10, dwFlags=flags)
        send(inp)
    # One notch up and one back: a zero-distance wheel is dropped by Windows
    # before any hook sees it (measured), so the net-zero pair is the
    # smallest mouse input that proves the mouse half of the path.
    for delta in (120, -120):
        wheel = _IN(type=0)
        wheel.u.mi = _MI(mouseData=ctypes.c_uint32(delta).value,
                         dwFlags=0x0800)                # MOUSEEVENTF_WHEEL
        send(wheel)
    time.sleep(0.5)
    hook.stop()
    hook.join(2.0)
    got = []
    while not sink.empty():
        got.append(sink.get_nowait())
    kinds = sorted({(g[1], g[3]) for g in got})
    ok = ("key", "down") in kinds and ("key", "up") in kinds \
        and ("mouse", "wheel") in kinds
    say(f"{'PASS' if ok else 'FAIL'}: hook delivered {kinds}")
    return 0 if ok else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--selftest", action="store_true",
                    help="prove the input hook delivers events, then exit")
    ap.add_argument("--out", default=str(DEFAULT_OUT),
                    help="where sessions go (default archive/recordings)")
    ap.add_argument("--duration", type=float, default=12 * 3600,
                    help="seconds before stopping on its own (default 12 h)")
    ap.add_argument("--no-frames", action="store_true",
                    help="do not bank a screenshot per input")
    ap.add_argument("--trace", action="store_true",
                    help="also capture the game's debug channel into the "
                         "session (trace.log), for tools/damage_oracle.py")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    run(Path(args.out), duration=args.duration, frames=not args.no_frames,
        trace=args.trace)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
