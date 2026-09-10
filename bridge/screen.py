"""Reading the screen: the sense the bridge never had.

    python bridge/screen.py --calibrate      # rebuild the references from captures/
    python bridge/screen.py --once           # what is on screen right now
    python bridge/screen.py --watch          # ...once a second
    python bridge/screen.py --once --panel   # map or inventory, in a dungeon

## Why

Every other acknowledgement in `bridge/` is a save file changing. That model is
inert in three places that matter, and all three are blocking something:

* **The front end.** Title, save-slot list, DLC page and mode select write
  nothing at all, so a menu click cannot be confirmed. `bridge/applog.py`
  supplies checkpoints, but the log never names the screen that is up.
* **Which of the map / inventory panels is showing.** They share one slot and
  the save records neither. That is the whole reason a room cannot be exited.
* **The curio panel's button row.** Its `y` flows with the description length
  (`layout.py:103-109`), so it has to be found, not assumed.

The first is `recognise()`, the second is `panel()`. The third is still open.

Two different techniques, because the problems are different. A menu screen is
one fixed picture, so a stored hash works. A dungeon MAP is a different picture
in every dungeon -- the three map captures sit up to 0.802 from each other,
against 0.971 to the nearest inventory -- so no single reference represents
"a map", and `panel()` keys on the inventory grid's own geometry instead.

## How a screen is recognised

One number per screen: the cosine distance between a **mean-normalised
grayscale hash** of a fixed region and a stored reference. Measured offline
against `captures/00_*.png`, over the centre block `(870,400)-(1660,900)` at
24x14, every distinct screen sits at least **0.484** from its nearest
neighbour -- title 0.798, DLC 0.907, modal 0.484, difficulty 0.484, the Old
Road loading screen 0.720. Mean-normalising is what makes it survive the
menus' animated fire and drifting clouds: it compares the SHAPE of the light,
not its level.

`recognise` demands two things, not one: the best match must be under
`THRESHOLD` **and** beat the runner-up by `MARGIN`. The margin is the load
-bearing half. The DLC list and the "Start Campaign" modal are the closest
pair, and mistaking the list for the modal would click `layout.MODAL_OK`
straight into the DLC rows and silently switch a DLC off. A near-tie must
resolve to "I do not know", which every caller is required to treat as a pause.

`00_3_Select_Save.png` and `00_7_After_creating_save.png` sit 0.004 apart and
deliberately share one name: they ARE one screen, differing only in whether
row 2 holds an empty letter or the new save. That distinction is read from
disk (how many `profile_*` directories exist), never from pixels.

## Capture

Pure `ctypes` + `numpy`, matching `window.py`'s no-pywin32 rule and adding no
dependency: `GetDC` -> `CreateCompatibleBitmap` -> `BitBlt(SRCCOPY)` ->
`GetDIBits` with a NEGATIVE `biHeight`, which is what asks for a top-down
buffer instead of the bottom-up one GDI defaults to. Measured 2560x1440 in
73 ms, which is nothing next to the 1.2 s `act.py` already waits for DD's
animations to settle.

Pillow is used in exactly one place -- `--calibrate`, to decode the PNGs -- and
is never imported at runtime. The grayscale and downsample maths is the same
numpy code on both paths on purpose: PIL's `convert("L")` and `resize(BOX)`
are close to, but not the same as, the obvious numpy version, and a reference
built with one and matched with the other would be quietly biased.

**It captures the game's own window DC, not the desktop.** So do not expect it
to see through `overlay.py` -- a layered window sitting on top of the game
leaves the overlapped region undefined. Run one or the other.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import json
import sys
import time
from pathlib import Path
from typing import Optional

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bridge.layout import INVENTORY_COLS, INVENTORY_X

REFS_PATH = Path(__file__).resolve().parent / "screen_refs.json"
CAPTURES = Path(__file__).resolve().parent.parent / "captures"
# A SECOND reference family, named by its own filename.
#
# `CAPTURE_NAMES` below maps the original nine `00_N_*.png` by prefix, which
# means adding a screen requires editing this module -- and that is why the
# front end went unmodelled for months while the intro screens sat one click
# away from a running game. Anything under `captures/screens/<name>.png` is a
# reference called `<name>`, so a capture session adds screens without a diff.
CAPTURES_SCREENS = CAPTURES / "screens"

# The region every signature is taken over, as FRACTIONS of the client area so
# it survives a windowed game at any size. (870,400)-(1660,900) of 2560x1440:
# the centre block, which is where every menu puts its panel and where the
# modal that overlays the DLC list actually differs from it.
SIG_BOX = (870 / 2560, 400 / 1440, 1660 / 2560, 900 / 1440)
SIG_GRID = (24, 14)

# Cosine distance. A live frame must land under THRESHOLD of its reference,
# and MARGIN clear of the next best.
#
# **The two do different jobs and only one of them prevents a wrong click.**
# MARGIN is the guard: it is what stops the DLC list being read as the modal
# drawn over it and `MODAL_OK` landing in the DLC rows. THRESHOLD only asks
# "does this resemble the reference at all", and setting it too tight does not
# make the answer safer -- it makes a screen that IS up read as unknown, and
# the boot then stalls at step 0 and pauses.
#
# Measured against the running game (2026-08-17), not against the PNGs:
#   title        0.076 and 0.176 on two consecutive looks, runner-up 0.79
#   save_select  0.011,                                    runner-up 0.79
#   a black frame / an animation mid-play   0.90 to 1.03 against everything
# The title screen's 0.176 is the animated fire and drifting cloud in the
# centre block, and it left only 0.074 of headroom at the original 0.25.
# 0.35 is twice the worst self-match ever observed and still far below the
# 0.484 worst-case gap between two distinct screens, so it cannot bring two
# references into contention -- MARGIN decides those regardless.
THRESHOLD = 0.35
MARGIN = 0.15

# ITU-R 601-2 luma, the same weights PIL's convert("L") uses -- stated here
# because the reference builder and the runtime must agree exactly.
LUMA = np.array([0.299, 0.587, 0.114], dtype=np.float64)

# The shared right-hand panel slot -- inventory or map, never both. Slightly
# wider and taller than the inventory cells themselves so the outer frame is
# inside it. See `panel()` for what is measured over it.
PANEL_X = (1290 / 2560, 2175 / 2560)
PANEL_Y = (1020 / 1440, 1390 / 1440)
# Above this the panel's column borders line up with the inventory grid.
# Measured over the eight dungeon captures: maps 0.20-0.23, inventories
# 1.99-2.36. 1.0 is roughly four times clear of either side.
PANEL_GRID_SCORE = 1.0

# The nine screens `captures/00_*.png` covers. `00_3` and `00_7` are one
# screen (see the module docstring), so the name is shared and the later
# capture simply overwrites the earlier -- they are 0.004 apart, so which one
# wins is immaterial.
CAPTURE_NAMES = {
    "00_0": "cinematic",
    "00_1": "message",
    "00_2": "title",
    "00_3": "save_select",
    "00_4": "dlc",
    "00_5": "modal",
    "00_6": "difficulty",
    "00_7": "save_select",
    "00_8": "loading",
}

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
SRCCOPY = 0x00CC0020
DIB_RGB_COLORS = 0


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG),
                ("biHeight", wt.LONG), ("biPlanes", wt.WORD),
                ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", wt.LONG),
                ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD),
                ("biClrImportant", wt.DWORD)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wt.DWORD * 3)]


# ── capture ──

def grab(win) -> Optional[np.ndarray]:
    """The game's CLIENT area as an (h, w, 3) uint8 RGB array, or None.

    The client rect comes from the same `GameWindow` every click resolves
    against, so a pixel this function reads and a pixel `act.py` clicks are the
    same pixel by construction -- which is the only reason it is safe to
    decide an action from one and send the other.
    """
    hwnd = getattr(win, "hwnd", None)
    w, h = getattr(win, "client_size", (0, 0))
    if not hwnd or w <= 0 or h <= 0:
        return None
    hdc = user32.GetDC(hwnd)
    if not hdc:
        return None
    mem = bmp = None
    try:
        mem = gdi32.CreateCompatibleDC(hdc)
        bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
        if not mem or not bmp:
            return None
        gdi32.SelectObject(mem, bmp)
        if not gdi32.BitBlt(mem, 0, 0, w, h, hdc, 0, 0, SRCCOPY):
            return None
        info = BITMAPINFO()
        info.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        info.bmiHeader.biWidth = w
        # Negative height asks GDI for a TOP-DOWN buffer. Positive gives the
        # bottom-up DIB convention and every signature comes out mirrored --
        # which still matches itself, so the bug would survive its own test.
        info.bmiHeader.biHeight = -h
        info.bmiHeader.biPlanes = 1
        info.bmiHeader.biBitCount = 32
        buf = ctypes.create_string_buffer(w * h * 4)
        if not gdi32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(info),
                               DIB_RGB_COLORS):
            return None
        arr = np.frombuffer(buf, dtype=np.uint8).reshape(h, w, 4)
        return arr[..., 2::-1].copy()               # BGRA -> RGB
    finally:
        if bmp:
            gdi32.DeleteObject(bmp)
        if mem:
            gdi32.DeleteDC(mem)
        user32.ReleaseDC(hwnd, hdc)


# ── signatures ──

# --- is there a DISPLAY at all? ---------------------------------------------

#: How long apart to sample, and how many samples must agree. 1.2 s is far
#: longer than any frame Darkest Dungeon holds still: the title screen's flames,
#: the Hamlet's banners and every combat idle animate continuously, so two
#: identical frames that far apart do not occur in a live game.
LIVENESS_GAP = 1.2
LIVENESS_SAMPLES = 3


def display_is_live(win, gap: float = LIVENESS_GAP,
                    samples: int = LIVENESS_SAMPLES) -> tuple[bool, str]:
    """`(live, why)` — is anything actually being drawn?

    **`OpenInputDesktop` SUCCEEDING IS NOT EVIDENCE OF A DISPLAY**, and that is
    the whole reason this exists. Probed 2026-09-03 on this machine: the
    session was unlocked, `WTSGetActiveConsoleSessionId` matched the process's
    own session, `OpenInputDesktop` returned a handle, the game launched, took
    a 1920x1080 borderless window and reported itself foreground and not
    minimised -- and **nothing was ever painted**. Every GDI capture came back
    as a frozen copy of the desktop, with a taskbar clock reading 10:09 in a
    frame grabbed at 12:15.

    That is what a machine with no attached display does: the compositor has
    nowhere to draw, so the last frame persists and new windows never appear.
    Without this check the symptom reaches the operator as "no recognisable
    screen after 75s", which sends them hunting for a hash mismatch or a
    resolution change -- neither of which is wrong with anything.

    The test is that a LIVE Darkest Dungeon never holds a frame still: flames
    on the title, banners in the Hamlet, idle animations in combat. So
    identical frames `gap` apart mean the picture is frozen, not that the game
    is calm.

    **There are TWO causes and they need OPPOSITE fixes, so `why` says which
    one this is.** The discriminator is the brightness, and it is decisive:

    - **A frozen copy of the desktop** (bright, ~70 here) is the compositor
      having nowhere to draw. The game never appears, and the fix is to attach
      a display.
    - **Pure black** (`min == max == 0`) is the opposite situation: the display
      is fine -- the desktop clock one pixel outside the window ticks normally
      -- and the WINDOW is bypassing composition. DWM gives a borderless
      window covering a whole monitor the independent-flip path, scanning the
      game out directly and leaving nothing behind for GDI to read. The fix is
      to stop it covering the monitor: `-fullscreen 0 -size 1280 720`.

    Measured 2026-09-03 on one boot of one machine: `-full_as_borderless 1`
    captured pure black, `-fullscreen 0 -size 1280 720` captured a live frame
    that the recogniser then read as `title` at a separation of 0.15. Before
    that reading the message here sent the operator after a missing display,
    which was the wrong half of a working machine.

    **A STILL FRAME THE RECOGNISER CAN NAME IS A LIVE GAME**, and the premise
    above is wrong about it. `raid_results` and the two `raid_progression`
    pages are flat images with no animation at all: measured 2026-09-05, three
    identical frames 1.2 s apart at mean brightness 9.8, with the recogniser
    reading `raid_results` off every one of them. The game was fine and
    waiting for a click, and `tools/overnight.py` refused the whole night
    twice over with "THE GAME IS NOT DRAWING".

    So a name is the second opinion, and it is a strong one in exactly the
    direction that matters: a frozen copy of the desktop matches no banked
    game screen, and pure black matches none either. What it can still let
    through is a game HUNG on a real screen -- and that is the better error,
    because the caller then has a screen name to act on and the actuator's own
    acknowledgement catches an action that does not land.

    **The remaining false positive is a still frame with no name** -- a
    mid-load black screen, or an alt-tabbed game that stopped rendering -- and
    it is indistinguishable from the flip case in one sample. Sample again
    after the load.
    """
    frames = []
    for i in range(max(2, samples)):
        if i:
            time.sleep(gap)
        img = grab(win)
        if img is None:
            return False, "no window to capture"
        frames.append(img)
    first = frames[0]
    if not all(np.array_equal(first, f) for f in frames[1:]):
        return True, "the picture is changing"
    # A NAME BEATS THE STILLNESS. Checked before either brightness branch,
    # because both of those describe a display fault and this frame is a game.
    named = ""
    try:
        named = recognise(first) or ""
    except Exception:                                      # noqa: BLE001
        named = ""
    if named:
        return True, (f"the picture is still, but it is the {named} screen -- "
                      f"the end-of-raid pages do not animate")
    head = (f"{len(frames)} identical frames {gap:.1f}s apart "
            f"(mean brightness {first.mean():.0f}) - nothing is being drawn "
            f"and no banked screen matches it. ")
    # PURE BLACK is a different fault from a FROZEN DESKTOP, and naming the
    # wrong one costs an hour: one wants a display attached, the other wants
    # the window to stop covering the monitor.
    if int(first.max()) == 0:
        return False, head + (
            "The frame is PURE BLACK while the window is up, which is the "
            "signature of a window bypassing composition rather than of a "
            "dead display: DWM gives a borderless window covering a whole "
            "monitor the independent-flip path and scans it out directly, so "
            "GDI reads nothing. Relaunch windowed and smaller than the "
            "monitor -- `-fullscreen 0 -size 1280 720` -- and check the "
            "desktop clock is ticking before blaming the display.")
    # NOT pure black, so the window is reaching the compositor -- and there
    # the certainty ends. TWO things look like this and brightness does not
    # separate them: a frozen copy of the DESKTOP (~70 here) and the GAME
    # itself hung on a real frame (measured at 13 on a stuck title screen).
    # Naming one would be a guess, and the first version of this message
    # guessed "desktop" and was wrong the first time it mattered.
    return False, head + (
        "It is not pure black, so something is reaching the compositor. Two "
        "things look like this: a frozen copy of the DESKTOP, which reads at "
        "ordinary desktop brightness (~70 here) and means there is nowhere to "
        "draw, or the GAME hung on a real frame, which can read anything "
        "(a stuck title screen measured 13). To tell them apart, grab the "
        "WHOLE virtual desktop (`ImageGrab.grab(all_screens=True)`) and look "
        "at the clock -- a plain `grab()` only ever returns the primary. A "
        "ticking clock with a still game means the GAME is hung and wants "
        "restarting; a frozen clock on every monitor means the display is.")


def _boxdown(a: np.ndarray, nx: int, ny: int) -> np.ndarray:
    """Area-average `a` down to (ny, nx). Plain numpy, no PIL at runtime."""
    h, w = a.shape
    ye = np.linspace(0, h, ny + 1).round().astype(int)
    xe = np.linspace(0, w, nx + 1).round().astype(int)
    rows = np.add.reduceat(a, ye[:-1], axis=0) / np.diff(ye)[:, None]
    return np.add.reduceat(rows, xe[:-1], axis=1) / np.diff(xe)[None, :]


def signature(img: np.ndarray, box=SIG_BOX, grid=SIG_GRID) -> np.ndarray:
    """Mean-normalised grayscale hash of `box`, unit length.

    Subtracting the mean and dividing by the norm is what makes this robust to
    the menus' flickering fire and to a monitor's brightness: it compares the
    shape of the light rather than its level. A flat region (an all-black
    screen) has no shape, so it returns zeros and matches nothing -- which is
    the honest answer for a frame that is still fading in.
    """
    h, w = img.shape[:2]
    fx0, fy0, fx1, fy1 = box
    x0, x1 = int(round(fx0 * w)), int(round(fx1 * w))
    y0, y1 = int(round(fy0 * h)), int(round(fy1 * h))
    crop = img[max(0, y0):max(1, y1), max(0, x0):max(1, x1)]
    if crop.size == 0:
        return np.zeros(grid[0] * grid[1])
    gray = crop.astype(np.float64) @ LUMA if crop.ndim == 3 else crop.astype(np.float64)
    v = _boxdown(gray, grid[0], grid[1]).ravel()
    v -= v.mean()
    n = float(np.linalg.norm(v))
    return v / n if n else v


def panel(img: np.ndarray, raid_running: Optional[bool] = None) -> str:
    """Which of the two right-hand panels is showing: `inventory` / `map` / ``.

    They share one slot (`TAB`, or `I`/`M`) and **the save records neither**,
    which is the whole reason a room cannot be left: exiting is a click on the
    map, and the map has to be the panel in front.

    Not done with a reference hash, and the measurements say why. Over the
    eight dungeon captures, the three MAP panels sit up to **0.802** from each
    other -- they are three different dungeons -- against 0.971 to the nearest
    inventory. One reference cannot represent "a map".

    So it keys on the one thing the inventory panel always has and a map never
    does: **its own 8x2 cell grid**, whose column borders sit on the 106.66 px
    pitch `layout.INVENTORY_X` was measured at. A border is bright at every y
    down the cell while content is not, so the min-over-y profile isolates the
    frame and ignores whatever is in the cells.

    The profile is then normalised by its OWN spread before the on-pitch
    columns are compared with the rest, which is what makes it survive a
    dimmed screen. An absolute brightness test got 4 of 5 inventories and
    missed `full_inventory.png`, where DD darkens the background behind its
    modal -- and that capture is precisely the case `bridge/inventory.py`
    exists for. Measured: maps 0.20-0.23, inventories 1.99-2.36.

    Untested on an EMPTY bag, which is what the tutorial opens with. Empty
    cells still draw their borders in every capture that has one, so it should
    score higher rather than lower, but `--panel` is there to check it live
    before anything acts on it.

    **IT IS A TWO-WAY DISCRIMINATOR AND HAS NO "NEITHER", so `"map"` means
    "not an inventory grid" and nothing more.** Handed a title screen it
    answers `"map"`, and on 2026-09-03 that turned a game sitting at the front
    end into a router complaining it could not fit the map of a dungeon that
    was not on screen.

    `raid_running` is the fix, and it is not a pixel test. Pass `False` and
    this returns `""` -- which every caller already treats as "cannot tell, do
    not act". `None` (the default) is the old behaviour exactly, so nothing
    changes for a caller that does not opt in.

    **A POSITIVE PIXEL TEST WAS TRIED AND ABANDONED; do not repeat it in this
    form.** Four scale-free features over the panel region -- bright fraction,
    edge-thinness, ground darkness, row variance -- were measured across the
    72 banked captures on known maps against known non-maps. **Not one of them
    separates the two groups.** A three-feature box does separate the 17
    hand-labelled captures and then accepts 31 of the 72, including
    `save_list_scrollbar`, `_embark_modal` and `_town_chain`. Thresholds tuned
    on a small label set that leak on the rest are overfitting, not a test.
    The earlier attempts failed too: the strip's spread separates nothing (a
    title fade measured 4.99 against a real map's 1.78), and one reference hash
    cannot represent "a map" because three dungeons sit 0.802 apart.

    The question was wrong rather than hard. A caller does not need the pixels
    to say "map"; it needs to know a raid is running, and that is in the SAVE:
    `persist.raid.json` exists only during one, which is how
    `live_state.read_live_state` decides `in_raid`. One `Path.exists()`, no
    parse.
    """
    if raid_running is False:
        return ""
    if img is None or img.ndim < 2:
        return ""
    h, w = img.shape[:2]
    gray = img.astype(np.float64) @ LUMA if img.ndim == 3 else img.astype(np.float64)
    y0, y1 = int(PANEL_Y[0] * h), int(PANEL_Y[1] * h)
    x0, x1 = int(PANEL_X[0] * w), int(PANEL_X[1] * w)
    strip = gray[y0:y1, x0:x1]
    if strip.size == 0:
        return ""
    prof = strip.min(axis=0)
    prof = prof - np.median(prof)
    spread = float(prof.std())
    if spread < 1e-6:
        return ""                      # a flat panel says nothing either way
    prof /= spread
    pitch = (INVENTORY_X[1] - INVENTORY_X[0]) * w / INVENTORY_COLS
    left = INVENTORY_X[0] * w - x0
    on = np.zeros(len(prof), bool)
    for k in range(INVENTORY_COLS + 1):
        c = int(round(left + pitch * k))
        on[max(0, c - 2):min(len(prof), c + 3)] = True
    if not on.any() or on.all():
        return ""
    got = float(prof[on].mean() - prof[~on].mean())
    return "inventory" if got >= PANEL_GRID_SCORE else "map"


# ── what changed between two frames ──
#
# The game answers questions by DRAWING, and the cheapest way to read an
# answer is to look at what appeared. Hover a map cell the party may move to
# and DD draws "Move to this room"; hover anything else and it draws nothing.
# Fran: *"if you don't get the tooltip the game won't let you go to that
# path"*. That is a positive permission, and it beats the alternative the map
# click used until now -- send, then wait 15 s for a save change that never
# comes.

#: How much brighter a pixel must get to count as newly drawn, and how many
#: such pixels make it a THING rather than an animation. Both are set by what
#: the panel does on its own: the map's party token pulses and the torch
#: flickers, which moves a few hundred pixels by a few levels, while a tooltip
#: is a light plate over a panel whose own mean is under 4.
APPEARED_LEVEL = 40.0
APPEARED_PIXELS = 400


def what_appeared(before: Optional[np.ndarray], after: Optional[np.ndarray],
                  box: Optional[tuple[int, int, int, int]] = None,
                  level: float = APPEARED_LEVEL,
                  min_pixels: int = APPEARED_PIXELS):
    """`(x0, x1, y0, y1, count)` of what got brighter, or None.

    **Differential, so nothing about the tooltip is hardcoded** -- not its
    position, not its colour, not its wording. That matters more than it
    looks: `bridge/screen.py` already treats the game's language as unknown
    (actor names arrive localised), and a detector keyed on "Move to this
    room" would work on exactly one install.

    Brighter ONLY. A tooltip is a light plate drawn over a dark panel, so the
    sign is information: something that merely moved -- the party token
    pulsing, a torch guttering -- darkens as much as it brightens and does not
    survive the one-sided test.

    `box` restricts the comparison to `(x0, x1, y0, y1)`; the returned
    coordinates are in the FRAME's own space either way, so a caller that
    passed a region does not have to add the offset back.
    """
    if before is None or after is None:
        return None
    if getattr(before, "shape", None) != getattr(after, "shape", None):
        return None
    if getattr(before, "ndim", 0) != 3:
        return None
    h, w = before.shape[:2]
    x0, x1, y0, y1 = box if box else (0, w, 0, h)
    x0, x1 = max(0, int(x0)), min(w, int(x1))
    y0, y1 = max(0, int(y0)), min(h, int(y1))
    if x1 - x0 < 2 or y1 - y0 < 2:
        return None
    a = before[y0:y1, x0:x1].astype(np.float64) @ LUMA
    b = after[y0:y1, x0:x1].astype(np.float64) @ LUMA
    lit = (b - a) > level
    count = int(lit.sum())
    if count < min_pixels:
        return None
    ys, xs = np.nonzero(lit)
    return (int(xs.min()) + x0, int(xs.max()) + x0,
            int(ys.min()) + y0, int(ys.max()) + y0, count)


def save_png(img: np.ndarray, path) -> bool:
    """Write a grab to disk. Diagnostic only, and best-effort.

    Pillow is a dev-only dependency here, so a machine without it loses the
    screenshot and nothing else -- no decision path may ever call this.

    It exists because the one thing still missing to leave a room is a MEASURED
    map panel, and the bridge is already standing in front of one when it
    stops. Asking a human to alt-tab and screenshot the moment it pauses is a
    worse way to get the same pixels.
    """
    try:
        from PIL import Image                   # noqa: PLC0415  (dev-only)
    except Exception:                           # noqa: BLE001
        return False
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Image.fromarray(np.asarray(img, dtype=np.uint8)).save(path)
        return True
    except Exception:                           # noqa: BLE001
        return False


def distance(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine distance in 0..2. Two zero signatures are maximally far apart,
    not identical: 'both frames were featureless' is not a match."""
    if not np.any(a) or not np.any(b):
        return 2.0
    return 1.0 - float(a @ b)


# ── references ──

def load_refs(path: Path = REFS_PATH) -> dict[str, np.ndarray]:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: np.asarray(v, dtype=np.float64)
            for k, v in (raw.get("screens") or {}).items()}


def save_refs(refs: dict[str, np.ndarray], path: Path = REFS_PATH) -> None:
    Path(path).write_text(json.dumps({
        "box": list(SIG_BOX),
        "grid": list(SIG_GRID),
        "source": "captures/00_*.png at 2560x1440",
        "screens": {k: [round(float(x), 6) for x in v]
                    for k, v in sorted(refs.items())},
    }, indent=1), encoding="utf-8")


def build_refs(captures: Path = CAPTURES) -> dict[str, np.ndarray]:
    """Decode `captures/00_*.png` into references. Pillow, and dev-only.

    Only the PNG decode uses PIL. The grayscale and the downsample are the
    same numpy functions the runtime uses, so a reference cannot be biased
    against the thing it will be compared with.
    """
    from PIL import Image                       # noqa: PLC0415  (dev-only)
    refs: dict[str, np.ndarray] = {}
    for png in sorted(Path(captures).glob("00_*.png")):
        name = CAPTURE_NAMES.get(png.name[:4])
        if not name:
            continue
        img = np.asarray(Image.open(png).convert("RGB"), dtype=np.uint8)
        refs[name] = signature(img)
    # The named family is read SECOND and wins on a collision, so a screen
    # recaptured live supersedes its `00_*` original rather than being shadowed
    # by load order.
    for png in sorted((Path(captures) / "screens").glob("*.png")):
        img = np.asarray(Image.open(png).convert("RGB"), dtype=np.uint8)
        refs[png.stem] = signature(img)
    return refs


# **A screen may have more than one LOOK.** `name@variant.png` banks a second
# reference for `name`: the raid-progression screen draws a pulsing glow over
# an afflicted hero, and on those frames it sits 0.28 from its own reference
# and 0.33 from `raid_progression` -- inside the threshold, but the runner-up
# margin correctly refuses a near-tie and the walk stalls for 16 s. Every week
# skip afflicts its hero, so this is the ordinary case, not an edge.
#
# The base name is what callers see, and the margin test compares the best
# against the best entry with a DIFFERENT base -- two looks at one screen must
# never be each other's runner-up, which would make banking a variant actively
# harmful.
VARIANT_SEP = "@"


def base_name(name: str) -> str:
    """The screen a reference belongs to, variant suffix stripped."""
    return name.split(VARIANT_SEP, 1)[0]


# ── recognition ──

def recognise(img: np.ndarray, refs: Optional[dict[str, np.ndarray]] = None, *,
              thresh: float = THRESHOLD, margin: float = MARGIN) -> str:
    """Name the screen, or `""` when it is not sure.

    Two tests, not one. The runner-up test is the one that matters: the DLC
    list and the modal drawn on top of it are the closest distinct pair in the
    corpus, and reading the list as the modal would click `MODAL_OK` into the
    DLC rows and switch a DLC off with nothing to notice it. A near-tie must
    come back as "I do not know".
    """
    return rank(img, refs, thresh=thresh, margin=margin)[0]


#: Where to put the mouse when a tooltip is in the way, as client fractions.
#: OUTSIDE `SIG_BOX` (x 0.34-0.65, y 0.28-0.63) and above it, so a tooltip
#: hanging down from whatever sits there still lands short of the signature.
CURSOR_PARK = (0.5, 0.02)

#: How long DD takes to drop a tooltip once the mouse leaves it.
PARK_SETTLE = 0.35


def park_cursor(win, at=CURSOR_PARK) -> bool:
    """Move the mouse somewhere it is not covering the screen with a tooltip.

    A move, never a click, so it cannot play anything -- which is what makes it
    safe to do behind a caller's back. Best effort: a platform without
    `SetCursorPos` simply does not get the second chance.
    """
    try:
        ox, oy = win.client_origin
        cw, ch = win.client_size
        ctypes.windll.user32.SetCursorPos(int(ox + cw * at[0]),
                                          int(oy + ch * at[1]))
        return True
    except Exception:                                        # noqa: BLE001
        return False


def recognise_live(win, refs: Optional[dict[str, np.ndarray]] = None) -> str:
    """Which screen the RUNNING game is on, with the mouse got out of the way.

    **A tooltip is enough to make a known screen unrecognisable, and the
    actuator leaves the cursor wherever it last clicked.** Measured on the
    provision screen, 2026-09-05, with the pointer resting on a Holy Water in
    the supply cart: `provision` at **0.4401** against a THRESHOLD of 0.35, so
    no name -- and the next-best at 0.7431, which is to say the identification
    was never in doubt, only the distance. Moving the mouse and looking again
    gives **0.2426**. The tooltip was 45% of the distance.

    That mattered: `tools/overnight.py` reported "an unrecognised screen for
    16s" on the embark and then failed two whole iterations with "never
    reached a playable campaign", because it could not tell where it was
    standing.

    The park costs nothing in the ordinary case -- it happens only when the
    first look already failed, which is the one moment nothing is mid-gesture.
    Loosening THRESHOLD would have been the other repair and it is not
    available: distinct banked screens sit as close as **0.305**
    (`embark_underprepared` against `modal`), so the absolute cut is load
    bearing and it is the FRAME that has to be made clean.
    """
    img = grab(win)
    if img is None:
        return ""
    name = recognise(img, refs)
    if name or not park_cursor(win):
        return name
    time.sleep(PARK_SETTLE)
    img = grab(win)
    return "" if img is None else recognise(img, refs)


def rank(img: np.ndarray, refs: Optional[dict[str, np.ndarray]] = None, *,
         thresh: float = THRESHOLD, margin: float = MARGIN
         ) -> tuple[str, list[tuple[str, float]]]:
    """`(name_or_empty, [(name, distance), ...] sorted)`. For diagnostics."""
    refs = load_refs() if refs is None else refs
    if img is None or not refs:
        return "", []
    sig = signature(img)
    scored = sorted(((n, distance(sig, r)) for n, r in refs.items()),
                    key=lambda t: t[1])
    if not scored:
        return "", []
    best = scored[0]
    # The runner-up is the nearest reference of a DIFFERENT screen; another
    # look at the same one is agreement, not ambiguity.
    rival = next((s for s in scored[1:]
                  if base_name(s[0]) != base_name(best[0])), (None, 9.0))
    if best[1] > thresh or (rival[1] - best[1]) < margin:
        return "", scored
    return base_name(best[0]), scored


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Read the game's screen.")
    ap.add_argument("--calibrate", action="store_true",
                    help="rebuild screen_refs.json from captures/00_*.png")
    ap.add_argument("--check", action="store_true",
                    help="offline: every reference must identify its own "
                         "capture and reject the others")
    ap.add_argument("--panel", action="store_true",
                    help="which right-hand panel is showing")
    ap.add_argument("--capture", metavar="NAME",
                    help="bank the live frame as captures/screens/NAME.png "
                         "and report its separation from every known screen")
    ap.add_argument("--force", action="store_true",
                    help="--capture: bank even when the frame collides with "
                         "an existing reference")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--watch", action="store_true")
    ap.add_argument("--interval", type=float, default=1.0)
    ap.add_argument("--process", default="Darkest.exe")
    args = ap.parse_args(argv)

    if args.calibrate:
        refs = build_refs()
        if not refs:
            print(f"no captures under {CAPTURES}")
            return 1
        save_refs(refs)
        print(f"wrote {len(refs)} references to {REFS_PATH}")
        args.check = True

    if args.check:
        refs = load_refs()
        if not refs:
            print("no references - run --calibrate")
            return 1
        from PIL import Image                   # noqa: PLC0415  (dev-only)
        worst, bad = (9.0, ""), 0
        # Both families, or a reference banked by `--capture` is written into
        # screen_refs.json and then never verified -- which is the one failure
        # this check exists to catch.
        pairs = [(png, CAPTURE_NAMES.get(png.name[:4]))
                 for png in sorted(CAPTURES.glob("00_*.png"))]
        pairs += [(png, png.stem)
                  for png in sorted(CAPTURES_SCREENS.glob("*.png"))]
        for png, want in pairs:
            if not want:
                continue
            img = np.asarray(Image.open(png).convert("RGB"), dtype=np.uint8)
            got, scored = rank(img, refs)
            # Compare BASE names: `name@variant.png` is a second look at
            # `name`, so recognising it as `name` is the success case, and its
            # separation is measured against other SCREENS rather than against
            # its own sibling.
            want_base = base_name(want)
            others = [d for n, d in scored if base_name(n) != want_base]
            sep = min(others) if others else 9.0
            worst = min(worst, (sep, png.name))
            flag = "" if got == want_base else f"  <-- got {got or 'UNKNOWN'!r}"
            bad += got != want_base
            print(f"  {png.name:32s} -> {want:12s} "
                  f"self {scored[0][1]:.3f}  nearest-other {sep:.3f}{flag}")
        print(f"  worst separation {worst[0]:.3f} ({worst[1]})")
        return 1 if bad else 0

    from window import find_window               # noqa: E402 (script-mode)
    win = find_window(args.process)
    if win is None:
        print(f"{args.process} is not running")
        return 1
    refs = load_refs()

    if args.capture:
        img = grab(win)
        if img is None:
            print("  capture failed")
            return 1
        sig = signature(img)
        scored = sorted(((n, distance(sig, r)) for n, r in refs.items()),
                        key=lambda t: t[1])
        near = [(n, d) for n, d in scored if n != args.capture]
        sep = near[0][1] if near else 9.0
        for n, d in scored[:4]:
            print(f"  vs {n:16s} {d:.3f}"
                  + ("   <-- same name" if n == args.capture else ""))
        print(f"  mean {img.mean():.0f}   nearest OTHER screen {sep:.3f}")
        # A reference that lands inside MARGIN of a different screen does not
        # merely fail to help: `recognise` demands the runner-up be MARGIN
        # clear, so banking a colliding frame turns the screen it collides
        # with into a permanent UNKNOWN. Refusing is the same call the
        # recogniser makes on a near-tie, moved one step earlier.
        if near and sep < MARGIN and not args.force:
            print(f"  REFUSED: too close to {near[0][0]!r} "
                  f"({sep:.3f} < MARGIN {MARGIN}) -- this would make BOTH "
                  f"screens unrecognisable. --force to override.")
            return 1
        dest = CAPTURES_SCREENS / f"{args.capture}.png"
        if not save_png(img, dest):
            print(f"  could not write {dest} (Pillow missing?)")
            return 1
        print(f"  wrote {dest}")
        print("  run --calibrate to fold it into screen_refs.json")
        return 0

    if not refs:
        print("no references - run --calibrate")
        return 1

    def report():
        t0 = time.perf_counter()
        img = grab(win)
        ms = 1000 * (time.perf_counter() - t0)
        if img is None:
            print("  capture failed")
            return
        if args.panel:
            print(f"  panel: {panel(img) or 'UNKNOWN':10s} "
                  f"({ms:.0f}ms, mean {img.mean():.0f})")
            return
        got, scored = rank(img, refs)
        top = "  ".join(f"{n}={d:.3f}" for n, d in scored[:3])
        print(f"  {got or 'UNKNOWN':12s}  ({ms:.0f}ms, mean {img.mean():.0f})"
              f"   {top}")

    if args.watch:
        print(f"watching {args.process} (ctrl-c to stop)")
        try:
            while True:
                report()
                time.sleep(args.interval)
        except KeyboardInterrupt:
            return 0
    report()
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    sys.exit(main())
