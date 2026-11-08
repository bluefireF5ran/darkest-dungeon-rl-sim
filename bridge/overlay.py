"""A click-through heads-up display over the running game.

The bridge's advice has always been correct and in the wrong place: a terminal
behind the game window. What that costs is not reading time, it is **looking
away** — and looking away is what degrades both the play and the notes, which
are the only two products a live run has. So this draws the recommendation on
top of the game: a ring on the rank the advice points at, and a square on the
button to press for it.

Three things it deliberately is not:

* **It never clicks.** Layer 3 (input injection) stays unbuilt on purpose:
  automating clicks fixes nothing if the recommendation is wrong, and a HUD that
  can only be wrong on screen is a strictly safer way to find that out.
* **It is not a second decision path.** Every number on it comes from
  `bridge/run.py`'s `Update` records; the overlay renders and does not decide.
  It also never parses the printed block back into structure — a marker keyed on
  a regex over "-> SKILL on rank 3 (...)" would break the first time a label
  changed, and would break *silently*, by pointing at the wrong rank.
* **It is not the record.** The log is. If the overlay dies the run continues,
  which is why `Session.publish` swallows a front-end failure.

## Running it

Calibration is once per resolution, and wants a **battle on screen** so there
are sprites to click on:

```bash
python bridge/overlay.py --calibrate        # 12 clicks: 4 hero ranks, 4 enemy, the action bar, the card
python bridge/overlay.py --test             # draw the markers, check they line up
python bridge/overlay.py --model models/dd_combat_ppo_full_expedition.zip --log run1.log
```

Both `--calibrate` and `--test` wait `--delay` seconds before opening, because the
command is typed in a terminal and that leaves the terminal in front of the game;
the calibration window is a tint over whatever is behind it, so without the pause
the first attempt is spent clicking sprites on a picture of a terminal.

**The game has to be borderless, and there is no menu item for it.** DD's Video
options expose only `fullscreen` and `resolution` (`shared/options/
options.value_definitions.json` lists `resolution` and nothing else about the
window). Borderless is a **launch option**: the binary's SDL setup block, next to
`SDL_CreateWindow` and `plinth_app.cpp`, carries the option names `vsync`,
`fullscreen`, `full_as_borderless`, `window_pos`, `monitor_number` and `size`,
and the embedded default command line is `+appname "Darkest Dungeon" +shortname
"darkest" -vsync 1`. So:

    Steam -> Properties -> Launch Options:  -full_as_borderless 1

Over *exclusive* fullscreen a layered window is usually not composited at all and
the overlay simply will not appear — which is the test for whether the flag took.

The in-game resolution does not have to match the desktop. The layout is
calibrated in **screen** pixels and DD scales its 16:9 layout to fill, so a game
set to 1280x720 on a 2560x1440 monitor puts the ranks in the same screen
positions as one set to 2560x1440. Changing the desktop resolution, or the
aspect ratio, does invalidate it.

**F10 closes it**, not Esc — a click-through window cannot receive a keystroke by
design, so the key is read globally, and Esc is the game's own menu key.

## How it stays out of the way

Two Windows window styles, both needed and for different reasons:

* `-transparentcolor` keys one otherwise-unused colour out of the window, so
  everything not drawn on is see-through.
* `WS_EX_TRANSPARENT` makes the **whole** window ignore the mouse, including the
  parts that *are* drawn on. Without it the ring around an enemy would eat the
  click aimed at that enemy — the overlay would break the exact action it was
  drawn to recommend. `WS_EX_NOACTIVATE` keeps it from stealing focus from the
  game when it repaints.

Tk must be driven from the thread that created it, so the poll loop runs in a
daemon thread and hands `Update`s over a `queue.Queue` that the UI drains on a
timer. That also keeps a `--survival` rollout — seconds of simulation — from
freezing the display.
"""

from __future__ import annotations

import argparse
import json
import queue
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bridge.advisor import DEFAULT_TOTAL_COMBATS, Advisor
from bridge.live_state import find_profile
from bridge.overlay_slots import MOVE_SLOT, PASS_SLOT
from bridge.run import Session, Update, _watch

DEFAULT_LAYOUT = Path(__file__).resolve().parent / "overlay_layout.json"

# Keyed out of the window entirely. Any colour works as long as nothing drawn
# uses it, so it is deliberately a near-black nobody would pick for a marker.
TRANSPARENT_KEY = "#010203"

RANKS = 4
CARD_W, CARD_H = 620, 96

COLOURS = {
    "enemy": "#ff5a5a",
    "hero": "#4dd2ff",
    # A look-ahead recommendation is drawn in neither side's colour, on purpose:
    # it is not yet an instruction, and the whole failure this fixes was a
    # preview reading as one.
    "provisional": "#8a8f98",
    "skill": "#ffd24d",
    "card": "#0c0c10",
    "text": "#f2f2f4",
    "dim": "#9aa0a6",
    "warn": "#ffb340",
    "MATCH": "#5ddb7a",
    "DIFFER": "#ffb340",
    "?": "#9aa0a6",
}

# Windows extended window styles. See the module docstring for why each is here.
GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_NOACTIVATE = 0x08000000

# A click-through, never-activated window cannot receive a keystroke — the whole
# point is that input goes past it — so the Esc binding tkinter offers is dead
# the moment the overlay works as intended. Closing it needs a key read from
# OUTSIDE the window, and one the game does not use: DD binds Esc (menu), Tab,
# 1-4, Space and the movement keys, and nothing on F10.
VK_CLOSE = 0x79          # F10
CLOSE_KEY = "F10"

# Spelled out per rank rather than numbered, because "rank 1" is ambiguous on a
# screen: the party is drawn left-to-right with rank 1 at the RIGHT end, and the
# enemy line is its mirror. Getting one side backwards puts every marker on the
# wrong rank of that formation, and it looks like working software.
CALIBRATION_PROMPTS = (
    [f"HERO rank {i} — {where}" for i, where in (
        (1, "your FRONT hero: the RIGHT end of your party, nearest the enemies"),
        (2, "second from the right"),
        (3, "third from the right"),
        (4, "your BACK hero: the far LEFT of your party"))]
    + [f"ENEMY rank {i} — {where}" for i, where in (
        (1, "their FRONT: the LEFT end of the enemy line, nearest your party"),
        (2, "second from the left"),
        (3, "third from the left"),
        (4, "their BACK: the far RIGHT of the enemy line"))]
    + ["SKILL slot 1 — the leftmost icon in the action bar",
       "SKILL slot 5 — the MOVE icon (two curved arrows)",
       "The narrow X box at the right end of the bar — that is PASS",
       "Last one: click where the ADVICE CARD should sit (somewhere empty)"]
)

# The action bar, left to right: the hero's four equipped skills in the order
# the save lists them, then the game's own MOVE button, then the narrow X, which
# is PASS. That covers every action the policy can recommend except provisions,
# which are used from the inventory and not from this bar.
#
# Only slots 1 and 5 are clicked and 2-4 interpolated — the five are evenly
# spaced, and asking for five clicks to recover three numbers already implied is
# three extra chances to be off by a few pixels. The X is clicked separately
# because it is NOT on that spacing (it sits about two thirds of a step past
# slot 5) and is roughly a third as wide.
SKILL_SLOTS = 5

# Seconds between launching and the window appearing. The command is typed in a
# terminal, which leaves the terminal in front of the game — and the calibration
# window is a 45% tint over whatever is behind it, so without this pause the
# first attempt is spent clicking sprites on a screenshot of a terminal.
DEFAULT_DELAY = 6


@dataclass
class Layout:
    """Where the eight ranks and the card live, in screen pixels.

    Rank 1 is the FRONT rank on both sides — the two that face each other — which
    is the same numbering the engine, the advisor and the game's own tooltips
    use. Getting that backwards would put every marker on the mirror rank, and
    nothing else in the system would notice.
    """
    hero: list[tuple[int, int]] = field(default_factory=list)
    enemy: list[tuple[int, int]] = field(default_factory=list)
    # The five action-bar slots, or empty. Optional on purpose: a layout
    # calibrated before skill marking existed still loads and still works, it
    # just draws no square. Refusing it would throw away a good calibration
    # over an addition.
    skills: list[tuple[int, int]] = field(default_factory=list)
    skill_half: int = 46
    # The X is a tall sliver, not a square like the other five.
    pass_half: int = 17
    card: tuple[int, int] = (0, 0)
    screen: tuple[int, int] = (0, 0)
    radius: int = 58

    @property
    def complete(self) -> bool:
        return len(self.hero) == RANKS and len(self.enemy) == RANKS

    def point(self, side: str, rank: int) -> Optional[tuple[int, int]]:
        points = self.hero if side == "hero" else self.enemy if side == "enemy" else []
        return points[rank] if 0 <= rank < len(points) else None

    def skill_point(self, slot: int) -> Optional[tuple[int, int]]:
        """`slot` is 1-based and on-screen — 1-4 the equipped skills, 5 move."""
        return (self.skills[slot - 1]
                if self.skills and 1 <= slot <= len(self.skills) else None)

    @staticmethod
    def spread(first: tuple[int, int], last: tuple[int, int],
               count: int = SKILL_SLOTS) -> list[tuple[int, int]]:
        """Evenly space `count` slots between the two clicked ends."""
        if count < 2:
            return [first]
        return [(round(first[0] + (last[0] - first[0]) * i / (count - 1)),
                 round(first[1] + (last[1] - first[1]) * i / (count - 1)))
                for i in range(count)]

    def save(self, path: Path) -> None:
        path.write_text(json.dumps({
            "hero": [list(p) for p in self.hero],
            "enemy": [list(p) for p in self.enemy],
            "skills": [list(p) for p in self.skills],
            "skill_half": self.skill_half,
            "pass_half": self.pass_half,
            "card": list(self.card),
            "screen": list(self.screen),
            "radius": self.radius,
        }, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> Optional["Layout"]:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        layout = cls(
            hero=[tuple(p) for p in raw.get("hero", [])],
            enemy=[tuple(p) for p in raw.get("enemy", [])],
            skills=[tuple(p) for p in raw.get("skills", [])],
            skill_half=int(raw.get("skill_half") or 46),
            pass_half=int(raw.get("pass_half") or 17),
            card=tuple(raw.get("card") or (0, 0)),
            screen=tuple(raw.get("screen") or (0, 0)),
            radius=int(raw.get("radius") or 58),
        )
        return layout if layout.complete else None


def room_lines(detail: str, limit: int = 3) -> list[str]:
    """The out-of-combat block, squeezed to what fits on a card.

    Only the layer headers survive, and only their first line each: the terminal
    still has the whole block, and a HUD that needs reading is a HUD that is
    being read instead of the game.

    ROUTE is left out on purpose — the headline is already the next step, and
    repeating it would spend one of three lines saying the same thing twice.
    """
    keep = ("CURIO", "TORCH", "CAMP", "STAY?")
    out = []
    for line in detail.splitlines():
        if line.startswith(keep) and len(out) < limit:
            out.append(" ".join(line.split())[:96])
    return out


class Overlay:
    """The window. Renders `Update`s; decides nothing."""

    def __init__(self, layout: Layout, updates: "queue.Queue[Update]",
                 tk_module=None):
        self.tk = tk_module or __import__("tkinter")
        self.layout = layout
        self.updates = updates
        self.last_verdict = ""
        self.tally: tuple[tuple[str, int], ...] = ()

        self.root = self.tk.Tk()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        width, height = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        self.root.geometry(f"{width}x{height}+0+0")
        self.root.configure(bg=TRANSPARENT_KEY)
        self.root.attributes("-transparentcolor", TRANSPARENT_KEY)
        self.canvas = self.tk.Canvas(self.root, width=width, height=height,
                                     bg=TRANSPARENT_KEY, highlightthickness=0,
                                     borderwidth=0)
        self.canvas.pack()
        self.root.bind("<Escape>", lambda _e: self.root.destroy())
        self.root.after(80, self._make_click_through)
        self.root.after(120, self._drain)

    # -- windows plumbing --

    def _make_click_through(self) -> None:
        """Let every click land on the game instead of on the HUD.

        Without this the ring drawn around an enemy swallows the click aimed at
        that enemy, so the overlay would block the one action it was drawn to
        recommend. Failure is not fatal — a HUD that steals clicks is worse than
        no HUD, so say so loudly rather than carrying on quietly.
        """
        try:
            import ctypes
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id()) \
                or self.root.winfo_id()
            styles = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            ctypes.windll.user32.SetWindowLongW(
                hwnd, GWL_EXSTYLE,
                styles | WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE)
        except Exception as exc:                  # noqa: BLE001
            print(f"WARNING: the overlay could not be made click-through ({exc}).\n"
                  "         It will intercept mouse clicks. Close it with Esc.")

    # -- drawing --

    def _outlined(self, x: int, y: int, text: str, size: int, colour: str,
                  anchor: str = "w", bold: bool = True) -> None:
        """Text with a hand-drawn halo, because the background is a video game.

        Tk has no text stroke, and a single colour is unreadable over both a lit
        corridor and a black one."""
        font = ("Segoe UI", size, "bold" if bold else "normal")
        for dx, dy in ((-2, 0), (2, 0), (0, -2), (0, 2), (-1, -1), (1, 1)):
            self.canvas.create_text(x + dx, y + dy, text=text, fill="#000000",
                                    font=font, anchor=anchor)
        self.canvas.create_text(x, y, text=text, fill=colour, font=font, anchor=anchor)

    def _ring(self, update: Update) -> None:
        """Point at the rank — but only when there is a board to point at.

        A ring is the most instruction-like thing on screen, so it is withheld
        whenever it would be a guess:

        * **not while enemies can still reposition somebody.** 46 of 246
          monsters can, and if one does, the advised rank holds a different
          combatant by the time the hero acts. A confident circle on the wrong
          enemy is worse than no circle — the advisor already computes exactly
          this set for its terminal warning, so nothing new is being guessed.
        * **greyed and dashed while it is a look-ahead at all**, because the
          board can change in smaller ways too.
        """
        point = self.layout.point(update.side, update.rank)
        if point is None:
            return
        if update.provisional and update.movers:
            return
        x, y = point
        r = self.layout.radius
        colour = (COLOURS["provisional"] if update.provisional
                  else COLOURS.get(update.side, COLOURS["enemy"]))
        dash = (8, 6) if update.provisional else ()
        self.canvas.create_oval(x - r - 3, y - r - 3, x + r + 3, y + r + 3,
                                outline="#000000", width=7)
        self.canvas.create_oval(x - r, y - r, x + r, y + r,
                                outline=colour, width=4, dash=dash)
        label = f"rank {update.rank + 1}" + (" (preview)" if update.provisional else "")
        self._outlined(x, y - r - 16, label, 13, colour, anchor="center")

    def _square(self, update: Update) -> None:
        """Mark WHICH button to press, not just what to press it on.

        The ring answers "who", this answers "what" — and between them the
        recommendation is fully on screen, which is the point of the overlay.
        A square rather than a ring so the two are never confused at a glance:
        the action bar and the formation are far apart, and a player mid-fight
        is pattern-matching shapes, not reading."""
        point = self.layout.skill_point(update.skill_slot)
        if point is None:
            return
        if update.provisional and update.movers:
            return
        x, y = point
        half = (self.layout.pass_half if update.skill_slot == PASS_SLOT
                else self.layout.skill_half)
        colour = COLOURS["provisional"] if update.provisional else COLOURS["skill"]
        dash = (8, 6) if update.provisional else ()
        self.canvas.create_rectangle(x - half - 3, y - self.layout.skill_half - 3,
                                     x + half + 3, y + self.layout.skill_half + 3,
                                     outline="#000000", width=7)
        self.canvas.create_rectangle(x - half, y - self.layout.skill_half,
                                     x + half, y + self.layout.skill_half,
                                     outline=colour, width=4, dash=dash)

    def _card(self, update: Update) -> None:
        x, y = self.layout.card
        provisional = update.phase == "battle" and update.provisional
        border = (COLOURS["warn"] if provisional
                  else COLOURS.get(self.last_verdict, "#3a3a44"))
        self.canvas.create_rectangle(x, y, x + CARD_W, y + CARD_H,
                                     fill=COLOURS["card"], outline=border, width=3)
        head = update.headline or "…"
        # A preview is not written in the same ink as an instruction. The
        # headline already leads with "2 ENEMY TURNS FIRST, then …" (see
        # run.Session._battle_update); this makes it read as one at a glance,
        # which is the only way a HUD is read.
        self._outlined(x + 16, y + 26, head[:72], 17,
                       COLOURS["warn"] if provisional else COLOURS["text"])

        if update.phase == "battle":
            bits = []
            if provisional:
                bits.append("WAIT — not your turn yet")
                if update.movers:
                    bits.append(f"{', '.join(update.movers)} can shuffle ranks, "
                                "so the target may change")
            if update.confidence:
                bits.append(f"{update.confidence * 100:.0f}% confident")
            self._outlined(x + 16, y + 54, "  ·  ".join(bits)[:96], 12,
                           COLOURS["warn"] if provisional else COLOURS["dim"],
                           bold=False)
        else:
            for i, line in enumerate(room_lines(update.detail)):
                self._outlined(x + 16, y + 50 + i * 18, line, 11, COLOURS["dim"],
                               bold=False)

        if self.tally:
            counts = "  ".join(f"{name} {n}" for name, n in self.tally)
            self._outlined(x + CARD_W - 16, y + CARD_H - 14, counts, 11,
                           COLOURS.get(self.last_verdict, COLOURS["dim"]),
                           anchor="e", bold=False)

    def render(self, update: Update) -> None:
        self.canvas.delete("all")
        if update.tally:
            self.tally = update.tally
        if update.phase == "verdict":
            # A verdict closes the PREVIOUS turn, so it repaints the badge and
            # clears the ring rather than pointing anywhere: the board it
            # referred to is already gone.
            #
            # The badge belongs to the COMBAT turn. Route, torch, curio and camp
            # verdicts arrive between fights and are shown on the card, but they
            # must not repaint it — a DIFFER on which corridor to walk would
            # otherwise sit over the next combat recommendation as if that were
            # the one that had been judged.
            if update.layer in ("", "combat"):
                self.last_verdict = update.verdict
            self._card(update)
            return
        if update.phase == "battle":
            self._ring(update)
            self._square(update)
        self._card(update)

    # -- the loop --

    def _close_requested(self) -> bool:
        try:
            import ctypes
            return bool(ctypes.windll.user32.GetAsyncKeyState(VK_CLOSE) & 0x8000)
        except Exception:                         # noqa: BLE001
            return False

    def _drain(self) -> None:
        # Drain to the LATEST and drop the backlog: a queued recommendation is
        # about a board that has already moved on, and painting it would put a
        # ring on a rank the fight has left behind.
        latest = None
        try:
            while True:
                latest = self.updates.get_nowait()
        except queue.Empty:
            pass
        if latest is not None:
            try:
                self.render(latest)
            except Exception as exc:              # noqa: BLE001
                print(f"  (overlay render failed: {exc})")
        if self._close_requested():
            self.root.destroy()
            return
        self.root.after(120, self._drain)

    def run(self) -> None:
        self.root.mainloop()


def countdown(seconds: int, what: str) -> None:
    """Give the player time to put the game back in front of the terminal."""
    for remaining in range(int(seconds), 0, -1):
        print(f"\r  {what} in {remaining}s — click the GAME window now… ",
              end="", flush=True)
        time.sleep(1)
    print("\r" + " " * 70 + "\r", end="", flush=True)


def calibrate(path: Path, tk_module=None) -> Layout:
    """Twelve clicks, once per resolution.

    Deliberately interactive rather than derived from the window size: DD's
    formation is laid out by the art, not by a grid, and the sprite centres move
    with the aspect ratio. A dozen clicks the player makes once beats a formula
    that is quietly wrong at 21:9.

    The ninth is where the card goes. It is worth a click because the alternative
    is guessing a corner and finding out mid-fight that it covers the torch
    meter.
    """
    tk = tk_module or __import__("tkinter")
    root = tk.Tk()
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    width, height = root.winfo_screenwidth(), root.winfo_screenheight()
    root.geometry(f"{width}x{height}+0+0")
    root.attributes("-alpha", 0.45)
    canvas = tk.Canvas(root, width=width, height=height, bg="#000010",
                       highlightthickness=0)
    canvas.pack()
    points: list[tuple[int, int]] = []

    def draw() -> None:
        canvas.delete("all")
        step = len(points)
        prompt = (CALIBRATION_PROMPTS[step] if step < len(CALIBRATION_PROMPTS)
                  else "done")
        canvas.create_text(width // 2, 60,
                           text=f"[{step + 1}/{len(CALIBRATION_PROMPTS)}]  {prompt}",
                           fill="#ffffff", font=("Segoe UI", 24, "bold"))
        canvas.create_text(width // 2, 100,
                           text="rank 1 is the FRONT rank on each side · "
                                "Backspace undoes · Esc aborts",
                           fill="#9aa0a6", font=("Segoe UI", 14))
        for i, (px, py) in enumerate(points):
            canvas.create_oval(px - 26, py - 26, px + 26, py + 26,
                               outline="#4dd2ff", width=3)
            canvas.create_text(px, py, text=str(i + 1), fill="#4dd2ff",
                               font=("Segoe UI", 16, "bold"))

    def click(event) -> None:
        points.append((event.x, event.y))
        if len(points) >= len(CALIBRATION_PROMPTS):
            root.destroy()
            return
        draw()

    def undo(_event) -> None:
        if points:
            points.pop()
        draw()

    canvas.bind("<Button-1>", click)
    root.bind("<BackSpace>", undo)
    root.bind("<Escape>", lambda _e: (points.clear(), root.destroy()))
    # An overrideredirect window is not given focus by the window manager, so
    # without this Esc and Backspace do nothing until the first click lands.
    root.focus_force()
    draw()
    root.mainloop()

    if len(points) < len(CALIBRATION_PROMPTS):
        raise SystemExit("calibration aborted — nothing written")
    layout = Layout(hero=points[0:4], enemy=points[4:8],
                    skills=Layout.spread(points[8], points[9]) + [points[10]],
                    card=points[11], screen=(width, height))
    layout.save(path)
    print(f"wrote {path}")
    return layout


def _demo_update() -> Update:
    return Update(phase="battle",
                  headline="Vestal: JUDGEMENT on rank 2 (Bone Soldier, 8 hp)",
                  detail="", side="enemy", rank=1, confidence=0.61,
                  tally=(("MATCH", 12), ("DIFFER", 8), ("?", 1)))


def main() -> int:
    ap = argparse.ArgumentParser(description="a click-through HUD over the game")
    ap.add_argument("--calibrate", action="store_true",
                    help="click the 4 hero ranks, the 4 enemy ranks and the card spot")
    ap.add_argument("--test", action="store_true",
                    help="draw every marker and a sample card, with no game running")
    ap.add_argument("--layout", default=str(DEFAULT_LAYOUT))
    ap.add_argument("--delay", type=int, default=DEFAULT_DELAY, metavar="SECONDS",
                    help="pause before --calibrate/--test open, so you can bring "
                         "the game back in front of this terminal (default "
                         f"{DEFAULT_DELAY}; 0 to open at once)")
    ap.add_argument("--model", default="models/dd_combat_ppo_full_expedition.zip")
    ap.add_argument("--profile", default=None)
    ap.add_argument("--log", default=None, metavar="FILE")
    ap.add_argument("--no-playbook-heals", action="store_true",
                    help="do not apply PLAYBOOK 3.2 to the shortlist "
                         "- let the policy's own heal ranking stand")
    ap.add_argument("--survival", type=int, nargs="?", const=12, default=0)
    ap.add_argument("--combats-total", type=int, default=None)
    ap.add_argument("--interval", type=float, default=1.0)
    ap.add_argument("--duration", type=float, default=7200.0)
    args = ap.parse_args()

    layout_path = Path(args.layout)
    if args.calibrate:
        print(f"Calibration: {len(CALIBRATION_PROMPTS)} clicks. Have a BATTLE on screen, ideally against "
              "four small enemies\nso all four ranks are separate sprites.")
        countdown(args.delay, "opening")
        calibrate(layout_path)
        return 0

    layout = Layout.load(layout_path)
    if layout is None:
        print(f"no usable layout at {layout_path}\n"
              "Open a battle in the game, then run:  "
              "python bridge/overlay.py --calibrate")
        return 1
    screen_now = (0, 0)
    updates: "queue.Queue[Update]" = queue.Queue()

    if args.test:
        countdown(args.delay, "drawing the markers")
        overlay = Overlay(layout, updates)
        screen_now = (overlay.root.winfo_screenwidth(),
                      overlay.root.winfo_screenheight())
        if tuple(layout.screen) not in ((0, 0), screen_now):
            print(f"WARNING: calibrated at {tuple(layout.screen)}, now {screen_now} "
                  "— re-run --calibrate or every marker is off")
        for side in ("hero", "enemy"):
            for rank in range(RANKS):
                overlay._ring(Update(phase="battle", side=side, rank=rank))
        for slot in range(1, PASS_SLOT + 1):
            overlay._square(Update(phase="battle", skill_slot=slot))
        overlay.tally = (("MATCH", 12), ("DIFFER", 8), ("?", 1))
        overlay._card(_demo_update())
        print(f"markers drawn — check they sit on the sprites. {CLOSE_KEY} closes.")
        overlay.run()
        return 0

    model = args.model if args.model and Path(args.model).exists() else None
    if args.model and model is None:
        print(f"note: {args.model} not found — listing legal actions instead")
    print("loading the game data and the checkpoint…")
    advisor = Advisor(model,
                      total_combats=args.combats_total or DEFAULT_TOTAL_COMBATS,
                      combats_total_explicit=args.combats_total is not None,
                      survival_episodes=args.survival,
                      playbook_heals=not args.no_playbook_heals)
    session = Session(advisor, Path(args.log) if args.log else None,
                      on_update=updates.put)
    profile = find_profile(args.profile)

    # Daemon so closing the window ends the process: Tk owns the main thread and
    # a KeyboardInterrupt never reaches a worker anyway.
    threading.Thread(target=_watch,
                     args=(session, profile, args.interval, args.duration),
                     daemon=True).start()

    overlay = Overlay(layout, updates)
    if tuple(layout.screen) not in ((0, 0), (overlay.root.winfo_screenwidth(),
                                             overlay.root.winfo_screenheight())):
        print("WARNING: the screen is not the resolution this layout was "
              "calibrated at — re-run --calibrate")
    print(f"overlay up. Play in BORDERLESS WINDOWED. {CLOSE_KEY} closes it; the "
          "log keeps running until you Ctrl-C this terminal.\n")
    # Ctrl-C lands inside Tk's mainloop, and the first live run lost its whole
    # tally that way — the run finished, the raid ended, and the one number the
    # session existed to produce never printed. The summary is the product; it
    # gets printed however the loop ends.
    try:
        overlay.run()
    except KeyboardInterrupt:
        pass
    finally:
        print("\n" + session.summary())
        if session.log is not None:
            session.log.write(f"\n{session.summary()}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
