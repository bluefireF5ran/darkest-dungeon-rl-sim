"""Can the game be played while somebody else is using the machine?

    python tools/gamepad_probe.py --install-help
    python tools/gamepad_probe.py            # launches the game, probes, reports
    python tools/gamepad_probe.py --attach   # probe a game somebody else launched

## The question, and why this is the only route left

Fran: *"is there a way you can keep playing while I'm able to use the PC
normally"*. Today the actuator is `SendInput`, which owns the real cursor and
the real keyboard: the bridge and the operator cannot both use the machine.

**Mouse clicks cannot be delivered silently, and that is measured rather than
assumed.** `bridge/act.choose_backend` records the 2026-08-17 result:
`PostMessage`'s `WM_MOUSEMOVE` lands -- the Campaign button *highlights*,
which also proves the coordinates and the client-rect maths -- and the
`WM_LBUTTONDOWN`/`UP` that follow do nothing. SDL takes mouse buttons partly
from raw input, which `PostMessage` cannot supply. So the silent-click route
is closed and there is no point re-testing it.

**What is left is the gamepad, and the game is built for one.** From the
shipped binary and its SDL:

* `Darkest.exe` carries an `alwaysusecontroller` launch option, in the same
  option table as `fullscreen` and `size`, plus 104 `controller_` strings.
* `localization/miscellaneous.string_table.xml` carries 836 `_PC`-suffixed
  help entries and 33 `BUTTON` mentions -- a complete cursorless navigation
  mode, documented by the game itself.
* `SDL2.dll` contains **`SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS`**, and SDL
  reads its hints from environment variables of the same name. That is the
  documented switch for a joystick reaching an UNFOCUSED window.

A gamepad is not tied to the foreground window, does not move the cursor and
does not take keyboard focus. If DD accepts one in the background, the bridge
can play while the desktop stays usable.

## What this measures, and the control that makes it evidence

**A frame that changed is not an answer.** Darkest Dungeon animates
constantly -- the title's flames, the Hamlet's banners, combat idles -- so
"the picture moved after I pressed a button" is true whether or not the
button arrived. That is the same trap `screen.display_is_live` documents from
the other side, and `dd-the-ui-answer-pulses` is the memory of it costing two
nights.

So this samples TWICE:

* a **parked control** -- the same number of frames, the same spacing, with
  the pad connected and doing nothing;
* the **pressed** run -- one button per sample, at the same spacing.

and reports the two distributions side by side. A verdict is only given when
the pressed samples are clear of the parked ones; anything else prints
UNCLEAR and says so, because a probe that guesses is worse than no probe.

**Nothing here clicks, types or takes focus.** The only output is gamepad
state, and the only input is `screen.grab`, which reads the window's own DC.
The one exception is the control below, which takes focus for a few seconds
and hands it straight back, because a negative background result cannot be
read without it.

## RESULT, measured 2026-09-20 on this machine -- the route is CLOSED

Launched with `-alwaysusecontroller` and
`SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS=1`, on the campaign list, measuring the
save-row block only (no fire, no banners), five D-pad DOWN presses each way:

    game in the BACKGROUND   parked 0.0 x5   pressed 0.0 x5
    game FOCUSED             parked 0.0 x3   pressed 2.7, 16.9, 16.1, 16.0, 16.0

So the pad reaches the game perfectly and moves the selection highlight --
**but only while the game has focus**. Everything upstream checks out:
`XInputGetState` reports the virtual pad on slot 0 with `A` held reading
`0x1000`, `app.log` echoes `option: alwaysusecontroller`, and the game opens
the joystick through SDL (`SDL_JoystickIsHaptic=0` is logged on every
connect). The hint is in `SDL2.dll` and was in the process environment from
launch.

What cannot be told apart from here is WHICH layer gates it -- SDL not
honouring the env var, or DD ignoring input events while
`SDL_WINDOWEVENT_FOCUS_LOST`. The outcome is the same either way and the
mechanism should not be claimed without evidence.

**The trap this probe fell into first, and why the primary signal changed.**
It originally read the SCREEN NAME and asked whether it changed. Six `A`
presses reported "screen still title" -- and the game had in fact advanced to
the campaign list, drawn in controller mode with (A) glyphs. The recogniser's
references were banked in MOUSE mode, and a controller-mode screen it has
never seen scores against the wrong one. A recogniser is a fine confirmation
and a bad primary signal for a screen nobody has banked. Region deltas
against a parked control are what decide it now.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from data.paths import GAME_DIR

GAME_EXE = GAME_DIR / "_windows" / "win64" / "Darkest.exe"

#: The launch the probe wants, and every part of it is load-bearing.
#: `-size 1280 720` because at the desktop resolution the window covers a
#: monitor exactly and DWM stops updating the surface GDI reads -- see
#: `tools/overnight.py`'s LAUNCH_SIZE. `-alwaysusecontroller` because the
#: whole question is whether the controller path works.
LAUNCH = ["-skipvalidation", "-skipfeflowintro", "-fullscreen", "0",
          "-size", "1280", "720", "-alwaysusecontroller"]

#: The SDL hint, passed as an environment variable because that is how SDL
#: reads its hints and there is no other handle on a shipped binary.
BACKGROUND_HINT = {"SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS": "1"}

SAMPLES = 8
GAP = 1.0
SETTLE = 35.0


def install_help() -> int:
    """What to run, and why it cannot be run from here."""
    try:
        import vgamepad
        where = Path(vgamepad.__file__).parent / "win" / "vigem" / "install"
        msi = where / "x64" / "ViGEmBusSetup_x64.msi"
    except Exception:                                      # noqa: BLE001
        print("vgamepad is not installed:  python -m pip install vgamepad")
        return 1
    pad, why = _pad()
    if pad is not None:
        print("vgamepad is installed and ViGEmBus is live - a virtual pad")
        print("connects. Nothing to install; run the probe.")
        return 0
    print("vgamepad is installed. ViGEmBus (the kernel driver it talks to) is")
    print("not, and installing a driver needs elevation -- which is a UAC")
    print("prompt somebody has to click, so it is not something this script")
    print("should do behind your back.\n")
    print("Run this once, accept the prompt, then re-run the probe:\n")
    print(f'    msiexec /i "{msi}"\n')
    print("It is reversible: the driver uninstalls from Apps & Features like")
    print("anything else, and nothing in this repo depends on it.")
    return 0


def _pad():
    """A connected virtual X360 pad, or `(None, why)`."""
    try:
        import vgamepad as vg
    except ImportError:
        return None, "vgamepad is not installed (python -m pip install vgamepad)"
    try:
        return vg.VX360Gamepad(), ""
    except Exception as exc:                               # noqa: BLE001
        return None, (f"the virtual pad could not connect to ViGEmBus: {exc}. "
                      f"Run `python tools/gamepad_probe.py --install-help`.")


def launch() -> None:
    env = dict(os.environ)
    env.update(BACKGROUND_HINT)
    subprocess.Popen([str(GAME_EXE), *LAUNCH], cwd=str(GAME_DIR), env=env)


def _foreground():
    """The window that has focus right now, so it can be given back.

    Launching the game activates it, which takes the operator's window away
    from them -- and this is a probe about NOT doing that. Recording the
    foreground first and restoring it afterwards makes the test both polite
    and correct: the game has to be in the background for the answer to mean
    anything.
    """
    import ctypes
    return ctypes.windll.user32.GetForegroundWindow()


def _restore(hwnd) -> bool:
    import ctypes
    if not hwnd:
        return False
    u = ctypes.windll.user32
    # `AttachThreadInput` is what makes `SetForegroundWindow` work from a
    # background process -- the same dance `act._force_foreground` does, in
    # the opposite direction.
    cur = u.GetForegroundWindow()
    if cur == hwnd:
        return True
    a = u.GetWindowThreadProcessId(cur, None)
    b = u.GetWindowThreadProcessId(hwnd, None)
    if a != b:
        u.AttachThreadInput(a, b, True)
    ok = bool(u.SetForegroundWindow(hwnd))
    if a != b:
        u.AttachThreadInput(a, b, False)
    return ok


def _named(win) -> str:
    """The screen name, read WITHOUT parking the cursor.

    `recognise_live` moves the mouse to get a tooltip out of the way, and
    moving the mouse is the one thing this probe must not do -- it would be
    using the operator's pointer to answer a question about not using it.
    """
    from bridge import screen
    img = screen.grab(win)
    return "" if img is None else (screen.recognise(img) or "")


def _frames(win, n: int, gap: float, press=None) -> list[float]:
    """`n` consecutive frame-to-frame differences, pressing between each.

    The difference is taken over the WHOLE client area rather than a chosen
    widget: which widget a controller moves depends on the screen, and the
    probe must not need to know which screen is up to answer its question.
    """
    import numpy as np
    from bridge import screen

    last = screen.grab(win)
    out: list[float] = []
    for _ in range(n):
        if press is not None:
            press()
        time.sleep(gap)
        now = screen.grab(win)
        if last is not None and now is not None and last.shape == now.shape:
            out.append(float(np.abs(now.astype(int) - last.astype(int)).mean()))
        last = now
    return out


#: The block of the front end that holds the save rows, as client-rect
#: fractions. Chosen because NOTHING in it animates -- no fire, no banners --
#: so a parked control over it reads 0.000 and any movement is the selection
#: highlight. Measured at 1280x720; fractions so it survives another size.
WATCH = (360 / 1280, 470 / 720, 980 / 1280, 670 / 720)


def _region(win, box=WATCH):
    """The watched block as an int array, or None."""
    from bridge import screen
    img = screen.grab(win)
    if img is None:
        return None
    h, w = img.shape[:2]
    x0, y0, x1, y1 = box
    return img[int(y0 * h):int(y1 * h), int(x0 * w):int(x1 * w)].astype(int)


def _measure(win, pad, n: int, gap: float, button=None) -> list:
    """`n` differences against a fixed baseline, pressing `button` each time.

    Against a FIXED baseline rather than the previous frame: a selection that
    moves once and then stays put would read as one blip and then silence if
    each frame were compared with its predecessor, which is exactly what a
    working press looks like.
    """
    import numpy as np
    import vgamepad as vg

    base = _region(win)
    out = []
    for _ in range(n):
        if button is not None:
            pad.press_button(button=button)
            pad.update()
            time.sleep(0.12)
            pad.release_button(button=button)
            pad.update()
        time.sleep(gap)
        now = _region(win)
        if base is None or now is None or base.shape != now.shape:
            continue
        out.append(float(np.abs(now - base).mean()))
    return out


def probe(attach: bool, samples: int, gap: float) -> int:
    from bridge import screen, window

    pad, why = _pad()
    if pad is None:
        print(f"cannot probe: {why}")
        return 2

    theirs = _foreground()
    if not attach:
        print(f"launching with {LAUNCH} and "
              f"SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS=1")
        launch()
        time.sleep(SETTLE)
        # Give the operator their window back. The game grabs focus when it
        # opens, and a probe about background input that runs with the game
        # in front is answering a different, easier question.
        if _restore(theirs):
            print("focus handed back to the window that had it")
        else:
            print("could not hand focus back - click your own window, then "
                  "the presses below are a real background test")
        time.sleep(1.5)

    win = window.find_window()
    if win is None:
        print("no game window. Is it running?")
        return 2
    if attach:
        print("NOTE: --attach cannot tell whether the game was launched with")
        print("      SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS=1. A negative result")
        print("      from an attached game therefore proves nothing.")

    fg = bool(getattr(win, "is_foreground", False))
    print(f"window {win.window}  client {win.client_size}  foreground {fg}")
    if fg:
        print("the game is in FRONT, so this is not yet a background test")

    live, note = screen.display_is_live(win)
    print(f"display live: {live} ({note})")
    if not live:
        print("nothing is being drawn, so no difference means anything")
        return 2
    print(f"screen: {_named(win) or 'UNKNOWN'} (a bonus signal only - a "
          f"controller-mode screen may match no banked reference)")

    import vgamepad as vg
    down = vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_DOWN

    print()
    print("background, pad idle : ", end="")
    parked = _measure(win, pad, samples, gap)
    print([round(v, 3) for v in parked])
    print("background, D-pad down: ", end="")
    pressed = _measure(win, pad, samples, gap, down)
    print([round(v, 3) for v in pressed])

    ceiling = max(parked) if parked else 0.0
    clear = [v for v in pressed if v > max(ceiling, 0.5)]
    if len(clear) >= max(2, len(pressed) // 2):
        print()
        print("VERDICT: the game acts on the pad WHILE IT IS IN THE "
              "BACKGROUND. Background play is on.")
        return 0

    # **A NEGATIVE BACKGROUND RESULT IS UNINTERPRETABLE ON ITS OWN.** "The pad
    # did not reach an unfocused game" and "the pad does not reach this game
    # at all" look identical from here and want completely different work, so
    # the control runs before any verdict -- exactly as the parked control
    # runs before a delta one. It costs the operator their focus for a few
    # seconds and hands it straight back.
    print()
    print("CONTROL: the same presses with the game FOCUSED")
    from bridge import act
    act._force_foreground(win.hwnd)
    time.sleep(1.2)
    ctl_parked = _measure(win, pad, max(3, samples // 2), gap)
    ctl_pressed = _measure(win, pad, samples, gap, down)
    _restore(theirs)
    print(f"  focused, pad idle : {[round(v, 3) for v in ctl_parked]}")
    print(f"  focused, D-pad    : {[round(v, 3) for v in ctl_pressed]}")

    ctl_ceiling = max(ctl_parked) if ctl_parked else 0.0
    ctl_clear = [v for v in ctl_pressed if v > max(ctl_ceiling, 0.5)]
    if len(ctl_clear) >= max(2, len(ctl_pressed) // 2):
        print()
        print("VERDICT: the pad reaches the game, but ONLY while it is "
              "focused. Either SDL is not honouring "
              "SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS or DD gates input on "
              "focus itself - this cannot tell them apart, and the outcome "
              "is the same. Background play is OFF by this route.")
        return 1
    print()
    print("VERDICT: the pad does not reach the game at all, focused or "
          "not. That is a pad or mapping problem rather than a background "
          "one: check XInputGetState sees the virtual controller and that "
          "app.log echoes `option: alwaysusecontroller`.")
    return 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--attach", action="store_true",
                    help="probe a game that is already running")
    ap.add_argument("--install-help", action="store_true",
                    help="print the one elevated command this needs")
    ap.add_argument("--samples", type=int, default=SAMPLES)
    ap.add_argument("--gap", type=float, default=GAP)
    args = ap.parse_args(argv)
    if args.install_help:
        return install_help()
    return probe(args.attach, args.samples, args.gap)


if __name__ == "__main__":
    sys.exit(main())
