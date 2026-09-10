"""Virtual key codes, Win32 structures, and every timing tunable.

Split out of `bridge/act.py` on 2026-09-08, verbatim. Re-exported from
`bridge.act`, which is where `bridge/menus.py`, `bridge/reset.py`,
`tools/overnight.py` and `tests/bridge/test_act.py` import these from.

Nearly all of it is a NUMBER SOMEBODY MEASURED against the running game:
how long a key must be held for the game to see it, how long to wait before
believing a frame, how many times to retry a click that the game may have
eaten. They read as arbitrary and they are not -- `docs/BRIDGE.md` and
`docs/CHANGELOG.md` carry what each one cost to find. Changing one is a
change to what the bridge believes about the game, not a tidy-up.

`VK_TAB` and `_cursor_pos` are each defined a second time further down the
original file, in the hardware-probe block. That shadowing is PRE-EXISTING --
the later definition won for everything below it -- and the re-export order in
`bridge/act.py` preserves it exactly.
"""

from __future__ import annotations
from typing import Optional, Sequence
import ctypes
from ctypes import wintypes as wt

# ── virtual key codes (the documented DD hotkeys) ──
VK_1, VK_2, VK_3, VK_4, VK_5 = 0x31, 0x32, 0x33, 0x34, 0x35
VK_A, VK_D, VK_T = 0x41, 0x44, 0x54
# `W` = the game's `dungeon_interact_with_curio`. NOT in the in-game hotkey
# help (which lists only A/D/T/TAB/SPACE/1-5/C/H/Q/E), but the command name is
# in the binary's input table at 0xac2658 alongside `dungeon_interact_with_door`
# and `dungeon_event_yes`, and Fran confirmed it in play. It performs the
# BARE-HANDED interaction; `dungeon_chose_item_use` is a separate command.
VK_W = 0x57
VK_SPACE = 0x20
VK_ESCAPE = 0x1B
# The front end only. DD's campaign-type screen puts the estate name in a text
# field and takes RETURN as its confirm -- there is no visible OK button, which
# is why Fran's click list reads "click7 on darkest difficulty, Enter". Nothing
# in a raid sends this.
VK_RETURN = 0x0D
# Swaps the right-hand panel between the MAP and the raid inventory -- they
# share one slot, and the game's own hotkey help lists TAB for it. Which of
# the two is showing appears in no save file, so `bridge/screen.py::panel`
# is what makes this key safe to press rather than a coin flip.
VK_TAB = 0x09
# Discarding a raid item is SHIFT-CLICK on its slot. That is not a guess: the
# game's own `str_discard_item_instructions` reads "[SHIFT-CLICK] on this item
# to discard it." There is no discard hotkey and no confirmation dialog on PC
# (the "Discard this item?" strings live only in `PSN.string_table.xml`, for
# the controller's hold-TRIANGLE), so the click is immediate and irreversible.
VK_SHIFT = 0x10
# CTRL, and it is only ever held WITH shift, on one widget. See
# One SHIFT+click is one darkness step down. `torchlight.TORCH_RESTORE` is the
# same 25 from the other direction -- what one torch item puts back.
TORCH_STEP = 25.0
# The meter animates; a second click during it lands on nothing.
TORCH_CLICK_GAP = 0.35
# How many SHIFT+clicks the fallback may send before giving up. 100 -> 0 is
# four, and the bound is what stops a mis-read torch level clicking forever.
TORCH_MAX_CLICKS = 6

# How long a modifier is held BEFORE the button goes down, and it is not
# padding. SDL tracks SHIFT and CTRL from its own key events, so the game only
# sees a modified click if it has processed the keydown by the time the
# mouse-down arrives -- one frame at 60fps is 16ms and DD does not always run
# at 60. The old 20ms was inside that margin, which makes "the modifier did not
# reach the game" indistinguishable from "the pixel is wrong", and the torch
# snuff has already been blamed on the pixel twice. 120ms is ~7 frames.
MOD_DWELL = 0.12

# How many times a torch reading is re-taken before "it did not move" is
# believed. The save is written per TURN in a raid, so a reduction made
# between turns is not on disk the instant the click lands -- and a single
# read 0.35s later records a working widget as a dead one, which is the
# cheapest possible way to refute the right pixel. Reads, not seconds, so a
# test that stubs `time.sleep` still exercises the real cadence.
TORCH_CONFIRM_READS = 3

# `T` is the game's own torch hotkey. SHIFT+T as a REDUCE is Fran's suggestion
# rather than a documented binding -- `localization/miscellaneous.string_table
# .xml` names only the two mouse gestures -- so it is probed, never assumed,
# and the probe watches for the torch going UP (a bare `T` relight) as well as
# down.
VK_T = 0x54

# `plan_snuff_torch`: `str_snuff_torch_tip` documents the snuff as
# SHIFT+CTRL+CLICK, and SHIFT alone on the same widget merely REDUCES the
# torch. Naming both is what keeps those two apart.
VK_CONTROL = 0x11
SKILL_VKS = (VK_1, VK_2, VK_3, VK_4, VK_5)

# The kill switch. NOT F10 -- `overlay.py` already polls that to close itself,
# and NOT Esc, which is DD's own menu key.
VK_KILL = 0x78          # F9

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WM_KEYDOWN, WM_KEYUP = 0x0100, 0x0101
WM_LBUTTONDOWN, WM_LBUTTONUP = 0x0201, 0x0202
WM_MOUSEMOVE = 0x0200
MK_LBUTTON = 0x0001
MK_SHIFT = 0x0004

INPUT_MOUSE, INPUT_KEYBOARD = 0, 1
KEYEVENTF_KEYUP = 0x0002
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_ABSOLUTE = 0x8000
# MEASURED, not read off a doc page: without this flag Windows maps the
# 0..65535 absolute range across the PRIMARY monitor, whatever the coordinates
# were normalised against. `_normalise` correctly spans the VIRTUAL desktop, so
# on a 3840x1080 desktop with a 1920x1080 primary every x landed at exactly
# half -- asked (732,744), got (366,743) -- while y was perfect because the
# virtual and primary HEIGHTS happen to match. That asymmetry is the tell.
#
# It is invisible on a single-monitor machine, where the two interpretations
# coincide, which is why the click path passed its probe and then missed every
# button the day a second monitor appeared.
MOUSEEVENTF_VIRTUALDESK = 0x4000
MOUSEEVENTF_WHEEL = 0x0800


# How many read-back corrections `move` will make. One is enough in practice:
# the error is under a pixel and the correction is exact to first order. Two
# bounds a pathological display without ever looping.
MOVE_CORRECTIONS = 2


def _virtual_desktop() -> tuple[int, int, int, int]:
    """`(x, y, w, h)` of the whole desktop, read FRESH.

    Never cached: a monitor plugged in, unplugged or rearranged mid-run
    changes all four, and a cached span aims every later click at a desktop
    that no longer exists.
    """
    return (user32.GetSystemMetrics(SM_XVIRTUALSCREEN),
            user32.GetSystemMetrics(SM_YVIRTUALSCREEN),
            user32.GetSystemMetrics(SM_CXVIRTUALSCREEN) or 1,
            user32.GetSystemMetrics(SM_CYVIRTUALSCREEN) or 1)


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


def _err(got: Optional[tuple[int, int]], want: tuple[int, int]) -> int:
    """Chebyshev distance, or a large number when the cursor cannot be read.

    Chebyshev rather than Euclidean because the question is "is any axis off",
    and it keeps the comparison in whole pixels.
    """
    if got is None:
        return 1 << 30
    return max(abs(got[0] - want[0]), abs(got[1] - want[1]))


def _cursor_pos() -> Optional[tuple[int, int]]:
    pt = _POINT()
    if not user32.GetCursorPos(ctypes.byref(pt)):
        return None
    return (int(pt.x), int(pt.y))


def _norm_axis(value: int, origin: int, span: int) -> int:
    """One axis of the absolute-mouse mapping. Split out to be TESTABLE.

    The whole of `_normalise` is three `GetSystemMetrics` calls and this, so
    keeping the arithmetic separate is what lets the geometry be checked over
    monitor layouts this machine does not have -- negative origins, portrait
    spans, a 1-pixel desktop -- without a second monitor to plug in.
    """
    span = span or 1
    scaled = (value - origin + 0.5) * 65535.0 / span
    return max(0, min(65535, int(round(scaled))))
WHEEL_DELTA = 120                      # one notch, per the Win32 contract

# The map's reopen animation. It peaked at 8 cells between +1.0s and +2.1s and
# was back to 2 by +3.1s, so the watch has to outlast the whole arc rather than
# aim at its middle -- the peak moves with the map's size.
# A drag is walked across rather than jumped, because SDL reads the drag from
# the motion between press and release. Twelve hops at 15 ms is ~0.2 s of
# travel, which is slower than a human flick and well inside what DD samples.
DRAG_STEPS = 12
DRAG_STEP_GAP = 0.015
# Dwell after the press and before the release. The first lets the game
# register a grab before the pointer moves; the second stops the drop
# resolving while it is still travelling.
DRAG_DWELL = 0.09

MAP_RESET_WATCH = 3.5
MAP_RESET_STEP = 0.25

MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 78, 79
SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN = 76, 77
KEYEVENTF_SCANCODE = 0x0008
MAPVK_VK_TO_VSC = 0
# 20ms was too short to be sure a game's input poll saw the key at all.
# A real keypress is tens of ms; 60 costs nothing at one action per turn.
KEY_HOLD = 0.06
# How long to wait for the operator to focus the game before giving up on
# a turn. Generous because a lost turn cannot be recovered.
FOCUS_WAIT = 30
# DD animates. The save is written when an action resolves, so the bridge is
# woken WHILE the animation for that action is still playing -- and input
# sent into an animation is dropped. Measured live: the Webber's attack wrote
# a save, the bridge fired ~0.35s later, the keystroke vanished, and because
# Session.battle had already consumed that turn_key the turn was never
# re-offered. The run simply stopped with the party waiting.
SETTLE_BEFORE_SEND = 1.2
# How long to wait for the save to change after sending. No change means the
# input did not land.
ACK_TIMEOUT = 6.0
# ...except for a COMBAT TURN, where 6s was measured to be inside the body of
# the distribution rather than in its tail. Over the captured corpus, the gap
# between two consecutive save writes with no human decision in it -- one
# monster turn resolving -- runs median 4.8s, p90 6.1s, max 11.5s (n=16;
# `archive/traces/raidsnaps*`, mtimes, so each is an over-read by up to the
# 1s poll). DD does not write on every combatant's turn either: 175 of 191
# in-battle writes land on a HERO's turn, so the ack after a hero acts is that
# hero's animation PLUS every enemy turn before the next hero. At 6s the
# acknowledgement was arriving after the retry, which credited the retry with
# the first send's write and, worse, sent a second action into a turn already
# spent. That is the shape of "the highwayman keeps moving back and forth".
# 15s costs nothing when the input lands (the wait returns on the change) and
# only delays the report of a genuine drop.
TURN_ACK_TIMEOUT = 15.0
SEND_ATTEMPTS = 3
# A corridor is several steps and `raid.in_area` only changes at a boundary,
# so walking is a bounded loop rather than one keypress per published block.
MAX_WALK_STEPS = 12
# How far a re-measured map point may sit from the planned one before the move
# is worth saying out loud, as a fraction of the frame. 0.005 is ~6 px at 1280
# wide against a map cell of ~32, so it is well inside "the same box" and only
# a real pan or zoom trips it. It changes nothing about WHICH point is used --
# the fresh one always wins -- it only decides whether the log mentions it.
MAP_POINT_TOLERANCE = 0.005
#: How warm a drawn map cell has to be to be the PARTY'S OWN.
#:
#: The party's cell carries a lit torch in a gold frame and nothing else on
#: the panel does. Measured on a whole-map view of a real Crypts run: the
#: party's cell reads **0.155** of its pixels warm, the next-warmest cell
#: 0.014, and the other twelve exactly 0.000. An order of magnitude, so the
#: floor is not a tuned number.
PARTY_CELL_WARM = 0.06

#: How many cells to hover when the fit could not place the destination.
#: Each costs about a second, and the direction ordering puts the answer
#: first or second on a map with one route out.
BLIND_CANDIDATES = 5

# How many fresh frames to try before the re-measure gives up, and how long
# between them. The panel animates, so one grab is a coin flip on whether it
# fits; six over ~1.2 s is what makes the guard a check rather than a blocker.
MAP_REFIT_TRIES = 6
MAP_REFIT_STEP = 0.25
# How long to let the tooltip appear after the pointer lands, and after it is
# parked again. DD draws it on a short delay, and the same wait on the way out
# is what stops the "before" frame of the NEXT probe still carrying the last
# one.
TOOLTIP_SETTLE = 0.5

#: How long to keep looking for the hover highlight, and how often.
#:
#: **THE HIGHLIGHT PULSES, so ONE LOOK IS A COIN FLIP.** Measured live
#: 2026-09-05 on a map cell the game does offer, sampling every 0.2 s after
#: the pointer arrived:
#:
#:     +0.23 none   +0.93 none   +1.64 none   +2.34 none
#:     +0.47 none   +1.16 HIT    +1.87 none   +2.57 HIT
#:     +0.70 none   +1.40 none   +2.10 none
#:
#: About one hit every 1.4 s. A single look at a fixed 0.5 s offset therefore
#: finds it roughly one time in six -- which is exactly what Fran was
#: watching: *"when it sets the mouse on it and then tooltip shows it's not
#: clicking. never leaving the room"*. The permission was there and the
#: instrument blinked.
#:
#: Sampled over a window instead, the separation is wide: **9 to 12 hits on
#: the offered cell against 0 to 2 on every other**, in the same pass, three
#: passes running. `TOOLTIP_MIN_HITS` sits at 3 -- three times the noise and a
#: third of the signal.
#:
#: **A longer window cannot manufacture a positive**, which is the control
#: that makes this safe rather than merely more sensitive: with the cursor
#: parked and nothing hovered at all, twelve consecutive samples over three
#: seconds fired **zero** times. The panel does animate -- the party's torch
#: pulses and a curio icon glints -- and `what_appeared` is already blind to
#: it, because it asks what APPEARED rather than what changed.
TOOLTIP_SAMPLE_FOR = 2.0
TOOLTIP_SAMPLE_STEP = 0.2
TOOLTIP_MIN_HITS = 3

#: How long to wait after a pack drag before re-reading the save. DD writes
#: the pack when the item is PUT DOWN rather than when it is picked up, and a
#: read taken too soon reports the pack unchanged -- which this layer treats
#: as "the input did not land" and stops on.
PACK_SETTLE = 1.2
# How many drawn cells to offer the game when its answer to the fit's own
# point is no. Ordered by distance from that point, so the first is the fit's
# best guess and the rest are its near misses -- four covers a cell that was
# merged with its neighbour or placed one over, and stops well short of
# walking the whole panel asking about every room.
MAP_CANDIDATES = 4
# How many presses in a row may leave the party on the same TILE before the
# walk calls it stuck. Not a measurement -- a lower bound on "that press
# achieved nothing". `Area.walked` is granular to one tile and `WALK_HOLD`
# covers well under one, so a single non-advancing read is the NORMAL case
# mid-tile. At 1 it was a bug: the tutorial's tent sat two tiles ahead, one
# press moved the party without crossing a boundary, and the walk reported
# "something is in the way" and gave up. Seen live as the curio being skipped.
WALK_STALL_LIMIT = 3
# Entering a room is an ANIMATION and DD writes the save when it ends, so the
# acknowledgement lags the keypress by more than a walk step does. Watched in
# slices rather than one long sleep, because a fight on entry (a guarded curio)
# should stop the walk as soon as it is visible.
DOOR_WAIT = 0.5
DOOR_WAITS = 6

#: How many times to try the door, stepping the party BACK between tries.
#:
#: **ONE PRESS IS NOT ENOUGH AND A LIVE RAID PROVED IT.** 2026-09-05, `coEJ`:
#: the party stood at absolute tile 1 of 6 with `rooE` through the door, the
#: direction was derived correctly (`key_toward(0)` is `D`, and it pressed
#: `D`), the stall check fired, `W` went out, the area did not change, and the
#: walk gave up -- then did the whole thing again on every re-plan for six
#: minutes with the save's mtime frozen the entire time.
#:
#: Probed by hand at that exact position: `D` moved nothing, `A` moved nothing,
#: and `W` put the party in `rooE` in under two seconds.
#:
#: **THE STEP IS BACKWARDS, and that is Fran's reading of the scene rather
#: than mine.** From the frame alone I had this as a party one step SHORT of
#: the door; he looked at it and said the party "have gone too far into the
#: right side without opening the door, it skipped it". That fits the probe
#: better than my version does -- the `A` is what put them back on the door,
#: and the `W` after it worked -- and it fits the stall: `areatile` ticks only
#: at a tile boundary, so a party jammed against the wall PAST the door reads
#: as "moved nothing" for exactly the same reason one still walking does.
#:
#: Getting the sign wrong is not neutral. A forward nudge from an overshoot
#: pushes them harder into the wall and the retry buys nothing; a backward one
#: from a party that is short of the door costs one step, and the NEXT
#: forward walk re-covers it. So the cheap direction to be wrong in is back.
#:
#: Bounded and cheap: each retry is one step back and one `W`, and the whole
#: thing is skipped the moment the area changes.
#: How long to wait for the action bar to stop changing, and how often to
#: look. See `ActRunner._read_bar`: the save is ahead of the screen, so the
#: first grab of a turn can be the PREVIOUS hero's bar.
#:
#: 0.6 s is four looks. It is spent only on a turn where the bar is genuinely
#: mid-animation -- a settled bar agrees on the second look and costs one
#: extra grab, about 15 ms.
#: How many `.is_continue_turn` actions one turn may take before the bridge
#: stops believing it.
#:
#: The data bounds it at ONE -- every shipped stance skill carries
#: `.per_turn_limit 1` -- so two is already the game disagreeing with itself
#: and three is a loop. The cap exists because the alternative to a wrong
#: guess here is a hero asked forever, which is the failure this whole file
#: keeps rediscovering.
MAX_FREE_ACTIONS = 3

#: How many times one turn may be deferred for a transient refusal before it
#: is treated as spent.
#:
#: A dark bar clears in an animation, so a handful of polls is generous. The
#: cap is not tuning: it is the difference between "wait for the game" and the
#: republish-forever deadlock this file has hit three times -- the tutorial
#: retreat, the relight in `rooH`, and the curio. Bounded, then handed to
#: `run.py`'s stall heartbeat, which is the layer that knows how to try
#: somebody else.
MAX_DEFERRALS = 12

BAR_SETTLE_FOR = 0.6
BAR_SETTLE_STEP = 0.15

DOOR_TRIES = 3
# Same shape for a formation swap: the two heroes trade places on screen and
# the save follows when they land. One 0.6s look called a swap that worked a
# failure -- see `_swap_ranks`.
SWAP_WAIT = 0.4
SWAP_WAITS = 6
# How long the party takes to come to a stop after a held A/D is released. `W`
# acts on what they are standing in front of, and while they are still gliding
# that is nothing -- which is the difference between the `W` that opens a door
# (they have auto-stopped at the end of the corridor) and the `W` that missed
# the tutorial tent three runs running (mid-hallway, still moving).
SETTLE = 1.2
# How many times to try `W` before giving up on a curio, stepping one press
# further along between attempts. Positioning by tile has been falsified twice
# -- on the curio's tile and one short of it -- so this searches instead, and
# verifies each attempt rather than trusting a coordinate.
# How many times to press `W` at a curio, stepping a little further along
# between attempts. Was 3, which is under a tile: `WALK_HOLD` covers "well
# under one tile" by measurement, so three sub-tile nudges could not close a
# gap the banked frame showed to be most of a tile wide. Bounded on the other
# side by position, not by count -- the loop stops nudging once the curio is
# behind the party, so the search can never walk into the door at the end of
# the corridor and leave the area.
CURIO_TRIES = 10

#: ...and how many in a ROOM, where nothing is being walked up to,
#: so the only thing a retry buys is time for the panel to be drawn.
ROOM_CURIO_TRIES = 3

#: How many times to look for the curio panel after `W`, `PANEL_WAIT` apart.
#: Four is ~1.8 s, which is comfortably over what DD takes to draw it; the
#: cost of an extra look is a screen grab, and the cost of one too few is a
#: curio abandoned for the whole raid.
CURIO_PANEL_READS = 4
# How far to step BETWEEN two presses at the same curio. Deliberately much
# shorter than `WALK_HOLD`, and this is the whole of Fran's "get the curio
# distance properly".
#
# **The interaction window is narrower than one walk press.** Measured off
# `captures/_curio_approach.png`: the front hero's right edge sits about 650 px
# and the tutorial tent starts about 690 px at 1280 wide, so the party is ~40 px
# short -- and `WALK_HOLD` (0.9 s) covers well over a hundred. A single nudge
# therefore steps clean OVER the window rather than into it, which is exactly
# what the live run shows: three nudges took the party from "40 px short" to
# "in the fight beyond the tent", pressing `W` at every point except the one
# that would have worked.
#
# So the search is finer, not longer: 0.25 s is on the order of the window
# itself. `CURIO_TRIES` rises with it so the total ground covered stays
# comparable (10 x 0.25 = 2.5 s against the old 6 x 0.9 = 5.4 s), and the extra
# presses cost nothing when a fight ends the loop early anyway.
#
# Fran's rule for what "right" looks like: *"the curio needs to be a bit to the
# right of the party, you cant interact with curio on top of them."*
CURIO_NUDGE_HOLD = 0.25
# Where `_bank_curio` puts the frame from a curio that would not respond. One
# fixed name, overwritten, gitignored, diagnostic only -- same rules as
# `MAP_SHOT`, and for the same reason: the save cannot show what the party is
# standing in front of, and the screen can.
CURIO_SHOT = "_curio_miss.png"
# The frame at the moment of the FIRST press, banked whatever happens next.
# The 2026-08-20 capture run reached attempt 3 of 6 and then a fight started,
# which returns down a path with no camera on it -- so the commonest exit was
# the uninstrumented one and the run produced no picture at all. This one is
# unconditional: it is also the frame the click target has to be measured
# from, since the tent's screen position when the party is ADJACENT cannot be
# derived from a frame taken when it was not.
CURIO_APPROACH_SHOT = "_curio_approach.png"
# How long to let the curio panel draw after `W` before looking for it.
PANEL_WAIT = 0.6
# And where a turn that would not go in banks its frame. A hero standing
# still for 45s logs the same line whether a tutorial popup was up, whether
# the terminal had stolen focus, or whether the game never thought it was
# that hero's turn -- the save distinguishes none of those and one frame
# distinguishes all three.
SEND_SHOT = "_send_miss.png"
# Movement is the one command that is NOT discrete. DD walks the party for as
# long as A/D is held, so `KEY_HOLD`'s 60ms tap only nudges it -- Fran watched
# it happen ("pulso D y si se movio ligeramente el grupo") and then twelve of
# those taps still left the party in `coBC`. Held for the best part of a
# second instead, so one step of the loop is one real step of the party.
WALK_HOLD = 0.9
# "the caller did not read the position", as distinct from `None`, which
# means "read it, and the save could not say". `_forward_key` treats those
# two differently and a plain `None` default would collapse them.
_UNREAD = object()
# The gap between two steps of ONE plan, when the step does not ask for more.
# Enough that the game does not coalesce them, small enough that a skill digit
# followed by a target click still feels like one action. `Step.after`
# overrides it where the second step depends on the first having landed.
STEP_GAP = 0.08
# How long DD takes to drop a hover tooltip once the pointer leaves. Measured
# generously: the cost of being wrong is a frame with a tooltip in it, which is
# exactly the failure `_park_cursor` exists to stop.
TOOLTIP_FADE = 0.35
# DD's dungeon inventory is 16 slots. Confirmed against a live save that had
# all 16 occupied at the moment the game refused 'take all'.
INVENTORY_SLOTS = 16
# Where `_show_map` banks the map panel it stopped in front of. ONE fixed name,
# overwritten every time: a run that stalls in six rooms would otherwise leave
# six near-identical PNGs in a directory that is meant to hold reference
# captures. Diagnostic only -- nothing reads it back.
MAP_SHOT = "_map_panel.png"


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wt.LONG), ("dy", wt.LONG), ("mouseData", wt.DWORD),
                ("dwFlags", wt.DWORD), ("time", wt.DWORD),
                ("dwExtraInfo", ctypes.POINTER(wt.ULONG))]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ctypes.POINTER(wt.ULONG))]


class _INPUTunion(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wt.DWORD), ("u", _INPUTunion)]


user32.SendInput.argtypes = [wt.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
user32.SendInput.restype = wt.UINT
user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.PostMessageW.restype = wt.BOOL
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short
user32.GetSystemMetrics.argtypes = [ctypes.c_int]
user32.GetSystemMetrics.restype = ctypes.c_int
user32.SetForegroundWindow.argtypes = [wt.HWND]
user32.MapVirtualKeyW.argtypes = [wt.UINT, wt.UINT]
user32.MapVirtualKeyW.restype = wt.UINT
user32.GetCursorPos.argtypes = [ctypes.POINTER(wt.POINT)]
user32.GetCursorPos.restype = wt.BOOL



def _cursor_pos():
    """Where the REAL cursor is. Used to prove PostMessage never moves it."""
    pt = wt.POINT()
    return (pt.x, pt.y) if user32.GetCursorPos(ctypes.byref(pt)) else None
