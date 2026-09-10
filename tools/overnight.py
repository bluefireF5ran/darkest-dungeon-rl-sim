"""Run the bridge round and round, unattended, and write down what happened.

    python tools/overnight.py --dry --max-iterations 2      # sends nothing
    python tools/overnight.py --max-iterations 20
    python tools/overnight.py --until 07:00

Every defect the bridge has ever had was found by a live run and read out of
its own log -- the click that landed at half its x, the dungeon graph routing
through a room that does not exist, the map panel that had already animated
shut, the corridor heading that could not be unlearned. **That loop is the
method, and until now it needed a human between iterations** to delete the
save and start the next campaign.

This is that human. It boots a campaign, plays it, tears it down, and does it
again, recording each iteration so a morning can be spent reading rather than
reproducing.

## The three things that make it safe to leave alone

**It can only delete a save it created.** `bridge/reset.claim_slot` refuses a
directory that already exists and is the only way to make the `OwnedSlot` that
`delete_owned` accepts -- so `profile_0`, `profile_8` and `profile_9` are not
merely avoided, they are unrepresentable. The claim happens BEFORE the boot,
which is what gives the refusal something to fire on.

**Each phase is a subprocess with its own timeout.** A hung poll loop, a torn
save, a crash inside torch -- none of them can take the runner down with them,
because the runner is not in the same process. It reads the phase's log
afterwards, which is the same evidence a human would read.

**It escalates rather than retries.** A phase that fails is not attempted
again with the same inputs: the run is torn down, and only if teardown ITSELF
fails does the game get killed and relaunched. Relaunches are bounded for the
night. An overnight loop that retries a broken thing forever produces a large
log and no information.

## What it will not do

It refuses to start beside a training run or a pytest suite. Both have crashed
this machine, and an overnight loop is exactly when someone would start one.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from data.paths import GAME_DIR
GAME_EXE = GAME_DIR / "_windows" / "win64" / "Darkest.exe"
# `-skipfeflowintro` is worth ~60 s a boot and was probed on this build's own
# argument table. `-skipfeflow` is NOT used: probed the same day, it hangs on a
# black screen even for a slot that exists.
# `-resetcampaigntutorials` is what keeps every iteration starting in the OLD
# ROAD. Completing the tutorial once is a permanent change: DD then skips it
# for new campaigns and drops them straight into the Hamlet, which was
# observed here as a campaign with 0 gold while `run.py` -- reading the raid
# template the slot was created from -- happily advised on `rooA` in a dungeon
# that was not loaded. The tutorial is the known-good path and the one the
# whole dungeon layer is validated against, so it is restored deliberately
# rather than worked around.
# **WINDOWED, NOT BORDERLESS FULLSCREEN, and that is the whole difference
# between a capture and a black rectangle.** A borderless window covering the
# entire monitor is eligible for DWM's independent-flip path: the game is
# scanned out directly and never lands in the composited surface GDI reads, so
# `screen.grab` returns `min=0 max=0` while the desktop clock one pixel outside
# the window ticks normally. That reads exactly like "no display attached" and
# cost two sessions to the wrong diagnosis.
#
# Measured 2026-09-03 on one boot of one machine: `-full_as_borderless 1` gave
# a pure black frame, `-fullscreen 0` gave a live one. What matters is that the
# window does not COVER the monitor; 16:9 keeps `overlay_layout.json` valid
# unchanged either way, since every point in it is a fraction of the CLIENT
# rect rather than a screen pixel.
#
# **`-size` IS ACCEPTED AND IGNORED**, so it is not here. `app.log` echoes
# `size '1600x900'` and the client still comes up 1280x720: the windowed size
# is the game's own Video resolution setting, not a launch option. It is left
# off rather than left in, because a flag that appears to work is worse than no
# flag -- see `MIN_CLIENT_W`, which is what actually guards the size.
LAUNCH_FLAGS = ["-skipvalidation", "-skipfeflowintro",
                "-resetcampaigntutorials", "-fullscreen", "0"]

# **Every pixel layer degrades SILENTLY with the window, and the map goes
# first.** DD's own resolution setting drifted to 853x480 mid-session -- two
# thirds of 1280x720 -- and nothing announced it: the recogniser still named
# screens, `panel()` still said "map", and only the box detector quietly
# stopped finding enough cells to fit. The router reported "widest map view was
# 14 cells over 13 frame(s), and still no click" and every raid was lost at the
# first room.
#
# 1024 is below the 1280 that has worked all session and well above the 853
# that failed, so it separates the two measured cases without pretending to be
# a threshold anyone derived.
MIN_CLIENT_W = 1024

# How many polls in a row the screen must be unreadable before the save is
# allowed to say "this is a dungeon". A dungeon has no reference and stays
# unrecognised; a fade between screens is unrecognised for a moment. One poll
# was enough to mistake a title screen for a raid.
UNKNOWN_IS_A_DUNGEON = 3

# The screens `_load_campaign` knows how to click. Anything else that the
# recogniser NAMES means a campaign is already open -- the Hamlet, an estate
# map, a raid-results or raid-progression chain -- and `town.to_town` is what
# walks those. Listing the front end and inverting is the right way round: a
# new post-raid screen gets banked far more often than a new menu, and the
# failure of the other reading is silent (it refuses a campaign that is
# already loaded and burns the iteration).
FRONT_END_SCREENS = ("title", "save_select", "dlc", "modal", "difficulty",
                     "loading", "cinematic", "message", "town_event",
                     "dungeon_intro", "dungeon_intro_darkest")

# How long to wait for the game to actually disappear after `taskkill /F`,
# and how long to let its handles drain afterwards. The delete is refused
# outright if it is still there -- see `kill_game`.
# What counts as "it never touched the game", and how many in a row end the
# run. Three, because two can legitimately happen while the game finishes a
# transition the recogniser has no name for yet.
# 10, not 3. An iteration that actually plays a raid takes MINUTES, so ten
# seconds is still comfortably "it never got started" -- and 3 was too tight to
# catch the real case: a map refusal ended every play phase in about four
# seconds and the run spent fifteen iterations on it before anyone looked.
INSTANT_SECONDS = 10.0
INSTANT_FAILURES = 3

KILL_TIMEOUT = 25.0
KILL_SETTLE = 3.0

LOGS = REPO / "archive" / "logs" / "overnight"
STOP_FILE = LOGS / "STOP"

BOOT_TIMEOUT = 300.0
PLAY_TIMEOUT = 900.0
LAUNCH_SETTLE = 25.0
# How long to let the game arrive at SOME recognisable screen before acting.
# Logos and the first loading screen match no reference, and pressing ESC into
# them does nothing.
SETTLE_TIMEOUT = 75.0
# Consecutive failures to get a window, not total launches -- the loop
# restarts the game every iteration by design.
MAX_LAUNCH_FAILURES = 4
# `--dry` exercises the loop, not the phases: neither a dry boot nor a dry play
# can finish on its own, so they are cut short rather than allowed to burn the
# real allowance.
DRY_TIMEOUT = 20.0

# The line `run.py` prints when the party leaves a raid. Watching for it is
# what stops each iteration burning its whole allowance after the work is done.
RAID_OVER = "-- raid over --"
PLAY_POLL = 3.0
# How long nothing may happen before the iteration is written off. Generous
# against DD's slowest animation and a torch-lit walk, and far short of the
# 900 s allowance a stall used to burn.
STALL_LIMIT = 180.0


@dataclass
class Iteration:
    n: int
    started: str = ""
    seconds: float = 0.0
    booted: bool = False
    raid_over: bool = False
    raids: int = 0                 # raids finished in this campaign
    towns: int = 0                 # times it walked back to the Hamlet
    embarks: int = 0               # times it walked back out again
    # The stage-coach visit, counted separately from `actions` because these
    # are the only actions in the night whose success is checked against the
    # SAVE rather than against a screen -- nothing inside a building panel
    # changes what the recogniser sees, so "sent" and "worked" are genuinely
    # different numbers here and pooling them would hide the gap.
    town_actions: int = 0
    town_confirmed: int = 0
    actions: int = 0
    refusals: int = 0
    unknown_screens: int = 0
    areas: list = field(default_factory=list)
    outcome: str = ""
    note: str = ""


class Overnight:
    def __init__(self, *, model: str, dry: bool, max_iterations: int,
                 until: Optional[datetime], raids_per_campaign: int = 3,
                 slot: int = 1, resume: bool = False, say=None):
        # Flushed, because the whole point is that somebody can `tail -f` this
        # at 3am. Redirected to a file, Python buffers stdout in 8 KB blocks
        # and an eight-hour run shows nothing until it ends.
        self.say = say or (lambda *a, **k: (print(*a, **k),
                                            sys.stdout.flush()))
        self.model = model
        self.dry = dry
        self.max_iterations = max_iterations
        self.raids_per_campaign = max(1, raids_per_campaign)
        # WHICH save slot the night plays. It was hardcoded to 1 in four
        # places, which is fine until slot 1 holds a leftover this tool can no
        # longer prove it created -- then the run cannot start at all, and the
        # only way forward was to delete a save on a hunch. A free slot is the
        # answer that bends no invariant.
        self.slot = int(slot)
        # RECORD ON THE CAMPAIGN THAT IS ALREADY LOADED, instead of
        # manufacturing a fresh one per iteration. Two reasons, and the
        # second is the one that forced it:
        #
        # - a campaign with a real roster and real gold reaches raids the
        #   tutorial cannot, so an hour of it is worth more per hour as a
        #   recording;
        # - and a new campaign is not always REACHABLE. DD draws one save
        #   row per existing campaign and only two are visible, so with
        #   three on disk the empty letter sits under the fold and the
        #   boot correctly refuses (see `menus.empty_letter_row`).
        #
        # It claims no slot and runs no teardown, because it OWNS nothing
        # -- which is exactly why it may not delete anything either.
        self.resume = bool(resume)
        self.until = until
        self.relaunches = 0
        self.launch_failures = 0
        self.iterations: list[Iteration] = []
        LOGS.mkdir(parents=True, exist_ok=True)
        self.run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.summary_path = LOGS / f"summary_{self.run_id}.json"

    # ── the machine around the loop ─────────────────────────────────────

    def _game(self):
        from bridge.window import find_window          # noqa: PLC0415
        return find_window("Darkest.exe")

    def _window_is_big_enough(self, win) -> bool:
        """Say so LOUDLY if the game came up too small, or too big, to read.

        Checked rather than set: the size is the game's Video option and no
        launch flag reaches it reliably, so the only honest thing the loop can
        do is refuse to spend a night on a window whose map it cannot fit.
        """
        w, h = getattr(win, "client_size", (0, 0))

        # **MEASURE THE BLINDNESS, do not infer it from the window size.**
        # This used to refuse any window filling the desktop, as a proxy for
        # two real faults: DWM's independent-flip path, which returns
        # `min=0 max=0` to GDI, and a display fallen back to a small mode,
        # which renders the game cropped. The proxy is WRONG on at least one
        # real setup -- over RDP the game comes up borderless at the full
        # 1366x768 and captures perfectly, mean brightness 11.2 with the title
        # screen recognised -- and it refused three iterations of a working
        # session before anyone looked.
        #
        # `display_is_live` measures the thing itself: a live Darkest Dungeon
        # never holds a frame still, and it already separates a pure-black
        # flip case from a frozen desktop and names the remedy for each.
        try:
            from bridge import screen                  # noqa: PLC0415
            live, why = screen.display_is_live(win)
        except Exception:                              # noqa: BLE001
            live, why = True, ""                       # cannot see: do not judge
        if not live:
            self.say(f"  [night] THE GAME IS NOT DRAWING: {why}")
            return False
        if w >= MIN_CLIENT_W:
            return True
        self.say(f"  [night] THE GAME WINDOW IS {w}x{h}, under {MIN_CLIENT_W} "
                 f"wide. Every pixel layer still LOOKS like it works at this "
                 f"size -- screens are recognised, the panel is named -- and "
                 f"the map box detector quietly stops finding enough cells to "
                 f"fit, so every raid is lost at the first room. `-size` does "
                 f"not fix it: set the resolution in the game's Video "
                 f"options.")
        return False

    def _screen(self) -> str:
        from bridge import screen                      # noqa: PLC0415
        win = self._game()
        if win is None:
            return ""
        # `recognise_live`, not `recognise`: the actuator leaves the mouse
        # where it last clicked, and a tooltip over the middle of the frame is
        # enough to put a known screen past THRESHOLD. That cost two whole
        # iterations of this loop on 2026-09-05, both reported as "never
        # reached a playable campaign" while the game sat on the provision
        # screen waiting to be told to embark.
        return screen.recognise_live(win)

    def ensure_game(self) -> bool:
        """Running and reachable. Launches it if not.

        **A launch per iteration is the NORMAL path, not an escalation.** The
        slot is re-used every time and DD caches its save list, so the game has
        to be restarted between a delete and the next campaign -- see
        `reset.delete_owned`. So the bound here counts CONSECUTIVE FAILURES to
        get a window, not launches: bounding launches would have capped the
        night at six iterations.
        """
        running = self._game()
        if running is not None:
            # Checked on the ALREADY-RUNNING path too, not only after a
            # launch: the size is the game's own setting and it drifted
            # mid-session, so "we did not start it" is no evidence about it.
            return self._window_is_big_enough(running)
        if self.launch_failures >= MAX_LAUNCH_FAILURES:
            self.say(f"  [night] {MAX_LAUNCH_FAILURES} launches in a row "
                     "produced no window - stopping rather than thrashing")
            return False
        self.relaunches += 1
        self.say(f"  [night] launching the game (#{self.relaunches})")
        subprocess.Popen([str(GAME_EXE), *LAUNCH_FLAGS], cwd=str(GAME_DIR))
        deadline = time.time() + 120
        while time.time() < deadline:
            win = self._game()
            if win is not None:
                time.sleep(LAUNCH_SETTLE)
                self.launch_failures = 0
                return self._window_is_big_enough(self._game() or win)
            time.sleep(2.0)
        self.launch_failures += 1
        self.say("  [night] the game never opened a window")
        return False

    def kill_game(self) -> bool:
        """Terminate the game and WAIT until it is really gone.

        Returns True when no window remains. **The return value is a
        precondition for the delete, not a courtesy.**

        This used to fire `taskkill /F` and sleep four seconds, which is a
        hope rather than a check. Fran's report was that the save came back
        after every teardown and that "the close makes the save restore" --
        the mechanism turned out to be Steam Cloud syncing the deleted slot
        back down on exit rather than DD rewriting it, and closing Steam fixed
        it. But the shape of his hypothesis is worth making impossible
        anyway: deleting a slot while the process that owns it is still alive
        is the one ordering that cannot be recovered from, and a fixed sleep
        cannot rule it out.

        `/F` runs no shutdown handler, so a surviving process means the kill
        did not land, not that it is flushing.
        """
        subprocess.run(["taskkill", "/F", "/IM", "Darkest.exe"],
                       capture_output=True)
        deadline = time.time() + KILL_TIMEOUT
        while time.time() < deadline:
            if self._game() is None:
                # Settle even once it is gone: the window disappears before
                # the last handles are released, and a delete into that
                # window fails on Windows with a sharing violation.
                time.sleep(KILL_SETTLE)
                return True
            time.sleep(0.5)
        self.say("  [night] the game did not close - NOT deleting anything")
        return False

    def to_title(self) -> bool:
        """Whatever is on screen, end up at the title. Best effort."""
        from bridge import act, reset                  # noqa: PLC0415
        win = self._game()
        if win is None:
            return False
        act._force_foreground(win.hwnd)
        time.sleep(1.0)
        win = self._game()
        # **Wait for the game to finish arriving before deciding anything.**
        # A freshly launched DD spends a while on logos and a loading screen
        # that match no reference, and ESC into that does nothing -- the first
        # campaign-mode run died here, reporting "stuck-before-boot" 18 s
        # after a launch that was simply not ready yet.
        deadline = time.time() + SETTLE_TIMEOUT
        seen = ""
        while time.time() < deadline:
            seen = self._screen()
            if seen:
                break
            time.sleep(1.0)
        if seen == "title":
            return True
        if not seen:
            # BEFORE blaming recognition, ask whether anything is being DRAWN.
            # A machine with no attached display launches the game happily --
            # window, foreground, not minimised -- and paints nothing, so every
            # capture is a frozen copy of the desktop and every screen reads
            # UNKNOWN. Probed 2026-09-03: that symptom reached the log as "no
            # recognisable screen" and sent the reader looking for a hash
            # mismatch that did not exist.
            try:
                from bridge.screen import display_is_live   # noqa: PLC0415
                live, why = display_is_live(win)
            except Exception as exc:                        # noqa: BLE001
                live, why = True, f"(liveness check failed: {exc})"
            if not live:
                self.say(f"  [night] THERE IS NO LIVE DISPLAY: {why}")
                self.say("  [night] the bridge cannot see the game on this "
                         "machine - attach a session and retry")
                return False
            self.say(f"  [night] no recognisable screen after "
                     f"{SETTLE_TIMEOUT:.0f}s - not pressing ESC blind")
            return False
        try:
            return reset.exit_to_main_menu(
                win, act.SendInputBackend(win), say=self.say, act=not self.dry)
        except Exception as exc:                       # noqa: BLE001
            self.say(f"  [night] exit to menu raised: {exc}")
            return False

    # ── one iteration ───────────────────────────────────────────────────

    def iterate(self, n: int) -> Iteration:
        from bridge import reset                       # noqa: PLC0415
        it = Iteration(n=n, started=datetime.now().isoformat(timespec="seconds"))
        t0 = time.time()
        log = LOGS / f"iter_{self.run_id}_{n:03d}.log"

        try:
            if not self.ensure_game():
                it.outcome = "no-game"
                return it

            if self.resume:
                # No boot, no claim, no teardown. `_campaign` gets
                # `embark_first` because a resumed campaign is standing in
                # the Hamlet, not in a dungeon, and `_play` would watch for
                # a raid that never starts.
                where = self._load_campaign()
                it.booted = where is not None
                if not it.booted:
                    it.outcome = "not-loaded"
                    return it
                # A campaign saved MID-RAID resumes into the dungeon, not the
                # Hamlet -- DD writes the raid per turn, so this is the normal
                # state after a crash and after any stop that was not at a
                # week boundary. Embarking first would be walking away from a
                # raid that is already running.
                self._campaign(log, it, embark_first=(where == "town"))
                it.outcome = "played" if it.raids else "no-raid-end"
                return it

            if not self.to_title():
                it.outcome = "stuck-before-boot"
                self.kill_game()
                return it

            # CLAIM BEFORE CREATING. The refusal in `claim_slot` only means
            # anything if the directory does not exist yet.
            owned = reset.claim_slot(self.slot)
            it.booted = self._boot(log)
            if it.booted:
                self._campaign(log, it)
            it.outcome = ("played" if it.raids else
                          "boot-failed" if not it.booted else "no-raid-end")

            # Teardown ALWAYS runs, including after a failure -- a half-made
            # campaign left on disk blocks the next claim. It closes the game
            # itself, so there is nothing to escalate to here; what it returns
            # is whether the GRACEFUL exit worked, which is the one check per
            # iteration that the UI was still responding.
            if not self._teardown(owned):
                it.note = (it.note + " " if it.note else "") + "ui-unresponsive"
        except reset.ResetRefused as exc:
            it.outcome = "refused"
            it.note = str(exc)
        except Exception as exc:                       # noqa: BLE001
            it.outcome = "error"
            it.note = f"{type(exc).__name__}: {exc}"
        finally:
            it.seconds = round(time.time() - t0, 1)
        return it

    def _campaign(self, log: Path, it: Iteration,
                  embark_first: bool = False) -> None:
        """Play several raids in ONE campaign, walking the town between them.

        An iteration used to be one raid and then a fresh campaign, which
        meant every iteration replayed the Old Road and nothing ever exercised
        the Hamlet. The tutorial is only the FIRST raid of a campaign; after
        it the party returns to town, embarks on a generated dungeon, and the
        interesting parts of the bridge -- the map panel on a real map, the
        corridor headings, the curios -- start getting used.

        A campaign ends early rather than being forced: a raid that does not
        finish, or a town walk that cannot find its way, stops this campaign
        and the iteration tears down as usual. Pushing on from a state nobody
        understands is how an unattended loop produces a large log and no
        information.
        """
        from bridge import act as act_mod                # noqa: PLC0415
        from bridge import town                          # noqa: PLC0415

        if embark_first and not self._between_raids(it):
            return
        for raid_n in range(1, self.raids_per_campaign + 1):
            self.say(f"  [night] raid {raid_n}/{self.raids_per_campaign}")
            before = it.raid_over
            self._play(log, it)
            if not it.raid_over:
                it.note = (it.note + " " if it.note else "") + \
                    f"raid{raid_n}-unfinished"
                return
            it.raids += 1
            it.raid_over = before               # re-armed for the next raid
            if raid_n == self.raids_per_campaign:
                return

            if not self._between_raids(it):
                return

    def _load_campaign(self) -> Optional[str]:
        """Title screen -> a playable campaign, for `--resume`.

        Returns where it ended up -- `"town"`, `"raid"`, or None for a refusal
        -- because a campaign can be saved in either place and the caller has
        to embark from only one of them.

        Resume's premise is that a campaign EXISTS at `--slot`, not that it is
        already on screen: the loop relaunches the game after a crash, and
        without this every iteration after one would sit at the title.

        It reuses `plan_for` for every screen, so the only thing it adds is
        which ROW to click -- the row of an existing campaign rather than the
        row of the empty letter -- and where to STOP. Standing in the Hamlet
        already satisfies it in zero clicks.

        It cannot create a campaign and does not try: `row_for_slot` returns
        None when the slot has no directory, and the refusal names it. That
        matters more than it looks -- clicking the wrong row here does not
        fail, it plays somebody else's save.
        """
        from bridge import act as act_mod                 # noqa: PLC0415
        from bridge import menus, screen as screen_mod    # noqa: PLC0415
        from bridge.layout import load as load_layout     # noqa: PLC0415

        row = menus.campaign_row(self.slot)
        if row is None:
            self.say(f"  [night] no campaign in slot {self.slot} - --resume "
                     f"plays an existing one and cannot make it")
            return None

        win = self._game()
        frac = load_layout()
        if win is None or frac is None:
            return None
        # The real Actuator, not a hand-rolled send loop: it is what honours
        # the F9 kill switch, the inter-input throttle DD needs to consume
        # them, and each step's own `after`.
        actuator = act_mod.Actuator(
            backend=act_mod.SendInputBackend(win),
            enabled=not self.dry, kill=act_mod.KillSwitch())
        unknown = 0
        for _ in range(20):
            win = self._game()
            if win is None:
                return None
            img = screen_mod.grab(win)
            name = screen_mod.recognise(img) if img is not None else ""
            if name and name not in FRONT_END_SCREENS:
                # A campaign is open and this is not a dungeon: the Hamlet, or
                # anywhere on the raid-results chain that follows a raid the
                # loop just played. `to_town` walks all of it, so stopping
                # here hands the job to the layer that owns it instead of
                # refusing a campaign that is already loaded.
                return "town"
            # THE DUNGEON HAS NO REFERENCE, so an unrecognised screen is where
            # "we are in a raid" has to be answered from the SAVE. Asking the
            # save first would be wrong: it still holds the last raid while
            # the game sits in the Hamlet, and that is most of a campaign.
            #
            # **But "unrecognised" is also what every TRANSITION looks like**,
            # and the save cannot tell those apart either. A title screen
            # caught mid-fade recognises as nothing, `persist.raid.json` is
            # still on disk from the last raid, and this concluded "raid" and
            # handed the play phase a game sitting at the front end. It then
            # reported map-fit failures about a dungeon that was not on
            # screen -- `panel()` is a two-way inventory/map discriminator and
            # answers "map" for anything that is neither.
            #
            # A dungeon STAYS unrecognised; a transition does not. So the save
            # only gets to speak once the screen has been unreadable for
            # several polls in a row.
            if not name:
                unknown += 1
                if unknown >= UNKNOWN_IS_A_DUNGEON and self._in_a_raid():
                    return "raid"
                time.sleep(2.0)
                continue
            unknown = 0
            if name == "save_select":
                if not self._click_save_row(win, frac.resolve(win),
                                            actuator.backend):
                    return None
                time.sleep(2.5)
                continue
            # `plan_for` clicks row == slot, which is the campaign to LOAD
            # here and the empty letter when the boot chain uses it. Same
            # click, different intent, and only the caller knows which.
            plan = menus.plan_for(name, frac.resolve(win), slot=self.slot)
            if not plan.actionable:
                self.say(f"  [night] load: stuck on {name!r} - {plan.refusal}")
                return None
            act_mod._force_foreground(win.hwnd)
            time.sleep(0.3)
            self.say(f"  [night] load: {plan.why} ({name})")
            actuator.execute(plan)
            time.sleep(2.5)
        self.say("  [night] load: never reached a playable campaign")
        return None

    def _click_save_row(self, win, resolved, backend) -> bool:
        """Scroll the save list to `--slot` and click it, or refuse.

        `plan_for` cannot do this: it is pure, and reaching a slot below the
        fold needs both the directory listing and several clicks that each
        change what the next one means.

        **The scroll is measured and the click is CHECKED**, which is the
        important half. The geometry (`menus.scroll_plan`) was read off one
        machine at one resolution, and the way it fails is not an exception --
        it is clicking a different campaign, silently, and then playing it. So
        the row's own content decides: a campaign draws a location / week /
        date column and an empty letter does not, and `--resume` is loading a
        campaign, so a row that is not one is a refusal.
        """
        from bridge import menus, screen as screen_mod     # noqa: PLC0415

        total = menus.total_save_rows()
        plan = menus.scroll_plan(self.slot, total)
        if plan is None:
            self.say(f"  [night] load: slot {self.slot} is not on a list of "
                     f"{total} rows")
            return False
        clicks, row = plan
        if clicks:
            down = resolved.menu_point("save_scroll_down")
            if down is None:
                self.say("  [night] load: no scroll-down coordinate")
                return False
            self.say(f"  [night] load: {clicks} page(s) down to reach slot "
                     f"{self.slot} at visible row {row}")
            for _ in range(clicks):
                if not self.dry:
                    backend.click(down)
                time.sleep(0.55)
            time.sleep(0.4)

        pt = resolved.save_row(row)
        if pt is None:
            self.say(f"  [night] load: visible row {row} has no coordinate")
            return False
        level = menus.row_info_level(screen_mod.grab(win), row)
        if level <= menus.SAVE_INFO_LEVEL:
            self.say(f"  [night] load: visible row {row} does not hold a "
                     f"campaign (info column {level:.1f}) - refusing rather "
                     f"than clicking an empty letter, which would START one")
            return False
        self.say(f"  [night] load: click slot {self.slot} at row {row} "
                 f"(info column {level:.1f})")
        if not self.dry:
            backend.click(pt)
        return True

    def _in_a_raid(self) -> bool:
        """Does the save say the party is in a dungeon right now?

        `inraid` is the field DD sets, and it survives a quit: a campaign left
        mid-raid resumes there. Any failure to read is False -- claiming a
        raid that is not running sends the play phase to watch an empty save.
        """
        from bridge.live_state import find_profile        # noqa: PLC0415
        try:
            raid = find_profile() / "persist.raid.json"
            return raid.is_file() and raid.stat().st_size > 0
        except Exception:                                 # noqa: BLE001
            return False

    def _between_raids(self, it: Iteration) -> bool:
        """Raid results -> the Hamlet -> the town visit -> embarked.

        Extracted so a RESUMED campaign can run it before its first raid.
        It is the identical chain either way, which is the point: a resumed
        run must not be a second, differently-behaved code path whose
        failures then mean something else.

        `to_town` is safe to call while already in the Hamlet -- it walks
        `until` a town screen, and standing on one satisfies that in zero
        clicks.
        """
        from bridge import act as act_mod                # noqa: PLC0415
        from bridge import town                          # noqa: PLC0415

        win = self._game()
        if win is None:
            it.note = (it.note + " " if it.note else "") + "game-gone"
            return False
        act_mod._force_foreground(win.hwnd)
        time.sleep(1.0)
        win = self._game()
        backend = act_mod.SendInputBackend(win)

        got = town.to_town(win, backend, say=self.say, act=not self.dry)
        if not got.ok:
            it.note = (it.note + " " if it.note else "") + \
                f"to-town:{got.why or got.stopped_on}"
            return False
        it.towns += 1

        # THE HAMLET. This is the only moment in the night with the game
        # standing in town, the activity log closed and a live backend in
        # hand -- `to_town` has just walked the raid-results chain and
        # `to_raid` is about to leave. Anything the town layer does has to
        # happen between these two lines.
        self._town(it, win, backend)

        # `embark_setup`, NOT `to_raid`. `to_raid` is the hands: it provisions
        # and clicks Embark with whatever party is ALREADY seated, which holds
        # only while DD is still remembering the last one. Across a night it
        # stops holding -- heroes die or stress out, and a campaign that has
        # never embarked has no party at all -- and the failure is quiet: the
        # estate map simply does not respond to `Provision`, because there is
        # nobody to provision for. Measured 2026-09-03 on a campaign fresh out
        # of the tutorial: four `Provision` clicks, no movement, iteration
        # lost. `embark_setup` scouts the quest, seats a party from the roster
        # and then calls `to_raid` itself, so this is a strict superset rather
        # than a second path.
        done = town.embark_setup(win, backend, self._profile(),
                                 store=self._store(), say=self.say,
                                 act=not self.dry)
        for d in done:
            self.say(f"  [night] {d}")
        if not any(d.what == "embark" and d.confirmed for d in done):
            why = next((d.why for d in reversed(done)
                        if d.why and not d.confirmed), "embark refused")
            it.note = (it.note + " " if it.note else "") + f"embark:{why}"
            return False
        it.embarks += 1
        return True

    def _town(self, it, win, backend) -> None:
        """Visit the stage coach: trade, upgrade, recruit.

        **Never fatal.** A town visit that half-worked is a normal outcome to
        report -- the raid is the point of the night, and refusing to embark
        because a recruit did not take would throw away the iteration's whole
        value. Everything it learns goes into the summary instead.

        The store is loaded once and reused: it is what turns recruiting from
        "take the best hero" into "take the pair that completes a curated
        comp", and rebuilding it per iteration would cost a second per town.
        """
        from bridge import town as town_mod             # noqa: PLC0415
        try:
            done = town_mod.visit_stage_coach(
                win, backend, profile=self._profile(), store=self._store(),
                say=self.say, act=not self.dry)
        except Exception as exc:                        # noqa: BLE001
            it.note = (it.note + " " if it.note else "") + \
                f"town:{type(exc).__name__}"
            return
        it.town_actions += sum(1 for d in done if d.sent)
        it.town_confirmed += sum(1 for d in done if d.confirmed)
        for d in done:
            self.say(f"  [night] {d}")

    #: Which profile a `--resume` night is reading, once it has been resolved.
    _resumed_profile = None

    def _profile(self):
        """The save directory this night is playing, or None.

        Normally `self.slot` -- the one `bridge/reset.claim_slot` owns -- so
        this can never read a campaign the tool did not create.

        **`--resume` IS THE EXCEPTION, and pinning it to the slot read the
        wrong campaign entirely.** Resume's whole premise is "play the campaign
        that is ALREADY loaded": nothing is claimed, nothing is created and
        nothing is deleted, so which slot the game happens to be in is not this
        tool's to decide. Measured 2026-09-06: the night resumed a raid in
        `profile_8`, played it through to the Hamlet, and then read the town
        out of `profile_1` -- a different roster, a different wallet, a
        different quest. It refused to embark because that OTHER campaign had
        two heroes, while the one on screen had nine. Nothing errored; the
        town layer simply answered a question about a save nobody was looking
        at.

        So a resumed night takes the profile the way `bridge/run.py` does --
        the most recently written campaign, which is the one the game is in --
        and **refuses `profile_0` outright**, because that is Fran's own
        campaign and the standing rule is that the bridge never drives it. The
        deletion guarantee is untouched: `--resume` deletes nothing, and
        `reset.claim_slot` still owns the only path that can.

        **THAT GUARANTEE WAS INVERTED, not merely weak.** This built the path as
        `SAVE_ROOT / f"profile_{slot}"`, and `SAVE_ROOT` is Steam's top-level
        `userdata` directory -- the profiles live three levels below it, under
        `<user id>/262060/remote/`. So the path never existed, `_profile()`
        always returned `None`, and every caller that takes `profile=None`
        (`read_town_state` among them) then resolved the MOST RECENTLY TOUCHED
        campaign instead. The one thing the docstring promised could not happen
        was the only thing that ever happened.

        `PROFILE_GLOB` is what says where profiles live, so the container is
        taken from a resolved profile's parent rather than assumed.
        """
        from bridge.live_state import find_profile      # noqa: PLC0415
        try:
            live = find_profile()
        except SystemExit:
            return None
        if self.resume:
            if live.name == "profile_0":
                self.say("  [night] the live campaign is profile_0 - that is "
                         "Fran's own and the bridge never drives it; reading "
                         "no town")
                return None
            if self._resumed_profile != live:
                self._resumed_profile = live
                self.say(f"  [night] resuming the campaign in {live.name}")
            return live
        path = live.parent / f"profile_{self.slot}"
        return path if path.exists() else None

    def _store(self):
        """The `DataStore`, built once per night and cached on the runner.

        Returns None rather than raising if it cannot be built: recruiting
        falls back to merit order and says so, which is a worse decision but a
        working night.
        """
        if getattr(self, "_store_cache", "unset") == "unset":
            try:
                from data.store_cache import load_store_cached  # noqa: PLC0415
                self._store_cache = load_store_cached(str(REPO), str(GAME_DIR))
            except Exception as exc:                    # noqa: BLE001
                self.say(f"  [night] no store ({type(exc).__name__}); "
                         f"recruiting on merit")
                self._store_cache = None
        return self._store_cache

    def _boot(self, log: Path) -> bool:
        cmd = [sys.executable, "bridge/menus.py", "--slot", str(self.slot)]
        cmd += ["--dry"] if self.dry else ["--act"]
        # A DRY boot cannot succeed unattended -- `menus.py --dry` follows a
        # human through the menus and sends nothing, so it can only ever time
        # out. Waiting the full allowance for that turns the structural smoke
        # test into a five-minute no-op, so dry mode gets a short one: what is
        # being checked here is the loop around the phase, not the phase.
        return self._phase(cmd, DRY_TIMEOUT if self.dry else BOOT_TIMEOUT,
                           log, "boot")

    def _play(self, log: Path, it: Iteration) -> None:
        """Play until the raid ends, then STOP.

        `run.py` has no reason to exit on its own -- it is a watcher, and a
        human closes it -- so left to `--duration` every iteration would keep
        polling an empty save for the remaining fourteen minutes. The log's
        own `-- raid over --` is the signal, and it is the same line the
        summary reads afterwards, so nothing new has to be true for this to
        work.
        """
        limit = DRY_TIMEOUT if self.dry else PLAY_TIMEOUT
        # The sentinel is COUNTED, not searched for. One log carries a whole
        # campaign now, so "the text is present" is true from the second raid
        # onward before the second raid has even started.
        seen_overs = (log.read_text(encoding="utf-8", errors="replace")
                      .count(RAID_OVER) if log.exists() else 0)
        cmd = [sys.executable, "bridge/run.py", "--model", self.model,
               "--log", str(log), "--duration", str(int(limit))]
        cmd += ["--backend", "dry"] if self.dry else ["--act"]
        self.say(f"  [night] play: until '{RAID_OVER}' or {limit:.0f}s")

        # **DEVNULL, not PIPE.** `run.py` prints the same block it writes to
        # `--log`, and a pipe nobody drains fills at ~64 KB and BLOCKS the
        # child on its next write. Observed: the play phase printed one room
        # block and then sat there for four minutes doing nothing, looking
        # exactly like a stuck bridge. The log file is the evidence channel;
        # stdout is a duplicate of it.
        proc = subprocess.Popen(cmd, cwd=str(REPO), stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL)
        deadline = time.time() + limit
        why = "timeout"
        # **Progress, not just a deadline.** A stalled iteration that runs its
        # full allowance costs the night five others: the first real stall
        # here sat for 900 s after one bad seed. Progress is the LOG growing
        # or the raid save being written -- either means something is still
        # happening, and neither moving means nothing is.
        last_progress = time.time()
        mark = self._progress_mark(log)
        try:
            while time.time() < deadline:
                if proc.poll() is not None:
                    why = "exited"
                    break
                if log.exists() and log.read_text(
                        encoding="utf-8", errors="replace"
                        ).count(RAID_OVER) > seen_overs:
                    why = "raid over"
                    break
                now_mark = self._progress_mark(log)
                if now_mark != mark:
                    mark, last_progress = now_mark, time.time()
                elif time.time() - last_progress > STALL_LIMIT:
                    why = f"stalled {STALL_LIMIT:.0f}s"
                    self._bank_frame(log)
                    break
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    proc.kill()
        self.say(f"  [night] play ended: {why}")
        self._read_log(log, it)
        it.raid_over = why == "raid over"

    def _phase(self, cmd, timeout: float, log: Path, name: str) -> bool:
        self.say(f"  [night] {name}: {' '.join(cmd[1:3])} ...")
        try:
            done = subprocess.run(cmd, cwd=str(REPO), timeout=timeout,
                                  capture_output=True, text=True)
        except subprocess.TimeoutExpired:
            self.say(f"  [night] {name} hit its {timeout:.0f}s timeout")
            with log.open("a", encoding="utf-8", errors="replace") as fh:
                fh.write(f"\n[night] {name} TIMED OUT after {timeout:.0f}s\n")
            return False
        with log.open("a", encoding="utf-8", errors="replace") as fh:
            fh.write(f"\n[night] --- {name} stdout ---\n{done.stdout}\n")
            if done.stderr:
                fh.write(f"[night] --- {name} stderr ---\n{done.stderr}\n")
        return done.returncode == 0

    @staticmethod
    def _progress_mark(log: Path):
        """Cheap evidence that something is still happening.

        The log growing covers the bridge thinking; the raid save's mtime
        covers the GAME moving even when the bridge has gone quiet. Either one
        alone would call a normal pause a stall.
        """
        size = log.stat().st_size if log.exists() else -1
        saved = -1.0
        try:
            from bridge.live_state import find_profile          # noqa: PLC0415
            raid = find_profile() / "persist.raid.json"
            saved = raid.stat().st_mtime
        except Exception:                                       # noqa: BLE001
            pass
        return (size, saved)

    def _bank_frame(self, log: Path) -> None:
        """A screenshot at the moment it gave up.

        Three of the last four root causes in this bridge were found only
        because a frame was banked where it stopped -- the save cannot show a
        greyed-out button, a popup, or where the party is standing.
        """
        try:
            from bridge import screen                           # noqa: PLC0415
            win = self._game()
            if win is None:
                return
            img = screen.grab(win)
            if img is None:
                return
            shot = log.with_name(log.stem + "_stall.png")
            if screen.save_png(img, shot):
                self.say(f"  [night] banked {shot.name}")
        except Exception:                                       # noqa: BLE001
            pass

    @staticmethod
    def _read_log(log: Path, it: Iteration) -> None:
        """What the run did, from its own log. The same evidence a human reads."""
        if not log.exists():
            return
        text = log.read_text(encoding="utf-8", errors="replace")
        it.actions = text.count("  [act] ")
        it.refusals = text.count("REFUSE")
        it.unknown_screens = text.count("unrecognised screen")

        seen, areas = set(), []
        for line in text.splitlines():
            if line.startswith("[") and "=== " in line:
                area = line.split("=== ")[1].split(" ===")[0].strip()
                if area not in seen:
                    seen.add(area)
                    areas.append(area)
        it.areas = areas

    def _teardown(self, owned) -> bool:
        """Leave the raid, CLOSE the game, then delete the slot.

        The close is not tidiness. DD caches its save list, so a slot deleted
        under a running game is not re-read: the next campaign created in it
        skips the difficulty screen and comes out named after the save-select
        hint text with no gold. Fran's report, and it reproduced exactly.

        The graceful exit still happens first, and its value is diagnostic --
        it is the one thing per iteration that proves the UI was still
        responding. A failure there is worth recording even though the kill
        that follows would have worked anyway.
        """
        from bridge import reset                       # noqa: PLC0415
        if self.dry:
            self.say("  [night] dry run: not deleting anything")
            return True
        # What the graceful attempt is actually measuring is whether the UI
        # was still RESPONDING, so the verdict is taken from the screen rather
        # than from whether ESC happened to work. `raid_results` has no pause
        # menu at all -- ESC does nothing there -- so an iteration that ends
        # normally would otherwise be filed as "ui-unresponsive" every single
        # time, which is a false signal in exactly the column a morning would
        # read first.
        seen = self._screen()
        graceful = self.to_title()
        responsive = graceful or bool(seen)
        if not graceful:
            self.say(f"  [night] no graceful exit from "
                     f"{seen or 'an unrecognised screen'} - killing")
        if not self.kill_game():
            # `game_running=False` would be a lie, and `delete_owned` believes
            # its caller about that.
            return False
        try:
            reset.delete_owned(owned, game_running=False)
        except reset.ResetRefused as exc:
            self.say(f"  [night] delete refused: {exc}")
            return False
        self.say(f"  [night] slot {owned.slot} removed, game closed")
        return responsive

    # ── the loop ────────────────────────────────────────────────────────

    def run(self) -> int:
        self.say(f"overnight run {self.run_id}"
                 f"{'  (DRY - nothing is sent)' if self.dry else ''}")
        self.say(f"  logs     : {LOGS}")
        self.say(f"  stop with: touch {STOP_FILE}")
        n = 0
        # AN ITERATION THAT FAILS INSTANTLY IS NOT A RETRY, IT IS A LOOP.
        # Measured 2026-09-03: an unbanked results screen made `_load_campaign`
        # refuse in 0 s, and the run spent its remaining 38 iterations in about
        # two seconds -- a summary full of attempts that never touched the
        # game. Anything that gets as far as launching or clicking takes
        # seconds, so a run of instant failures is the same failure repeating.
        instant = 0
        while n < self.max_iterations:
            if STOP_FILE.exists():
                self.say("  [night] STOP file present - finishing")
                break
            if self.until and datetime.now() >= self.until:
                self.say("  [night] reached --until - finishing")
                break
            n += 1
            self.say(f"\n=== iteration {n}/{self.max_iterations} ===")
            it = self.iterate(n)
            self.iterations.append(it)
            self.say(f"  -> {it.outcome}  {it.seconds:.0f}s  "
                     f"raids={it.raids} embarks={it.embarks} "
                     f"actions={it.actions} refusals={it.refusals}"
                     f"{'  ' + it.note if it.note else ''}")
            self._write_summary()
            instant = instant + 1 if it.seconds < INSTANT_SECONDS else 0
            if instant >= INSTANT_FAILURES:
                self.say(f"  [night] {instant} iterations in a row failed in "
                         f"under {INSTANT_SECONDS:.0f}s - that is one failure "
                         f"repeating, not {self.max_iterations} attempts. "
                         f"Stopping so the summary says what happened.")
                break
        self._write_summary()
        self.say(f"\nwrote {self.summary_path}")
        return 0

    def _write_summary(self) -> None:
        by_outcome: dict[str, int] = {}
        for it in self.iterations:
            by_outcome[it.outcome] = by_outcome.get(it.outcome, 0) + 1
        payload = {
            "run_id": self.run_id,
            "dry": self.dry,
            "iterations": len(self.iterations),
            "raids_completed": sum(i.raids for i in self.iterations),
            "embarks": sum(i.embarks for i in self.iterations),
            "relaunches": self.relaunches,
            "by_outcome": by_outcome,
            "detail": [asdict(i) for i in self.iterations],
        }
        self.summary_path.write_text(json.dumps(payload, indent=2),
                                     encoding="utf-8")


def busy_with_something_heavy() -> str:
    """A training run or a pytest suite beside this would crash the machine.

    Best effort and deliberately so: an unreadable process table must not stop
    the night, so an empty answer means "could not tell", not "nothing here".
    """
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name like 'python%'\" "
             "| Select-Object -ExpandProperty CommandLine"],
            capture_output=True, text=True, timeout=25).stdout
    except Exception:                                  # noqa: BLE001
        return ""
    for line in out.splitlines():
        low = line.lower()
        if "overnight.py" in low:
            continue                    # our own kind; the lock file handles it
        if "pytest" in low:
            return "a pytest suite is running"
        if "train.py" in low:
            return "a training run is going"
        if "comp_ga" in low or "comp_leaderboard" in low:
            return "a comp search is going"
    return ""


LOCK = LOGS / "overnight.lock"


def _pid_alive(pid: int) -> bool:
    try:
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                             capture_output=True, text=True, timeout=15).stdout
    except Exception:                                  # noqa: BLE001
        return True                                    # unknown: assume alive
    return str(pid) in out


def take_lock() -> bool:
    """One runner at a time. False when another live one holds it.

    **Two runners driving one game is the worst failure mode this tool has.**
    They fight over focus, one deletes the slot the other is booting into, and
    the logs interleave into something no morning can read. Three accumulated
    once, because the cleanup meant to stop them filtered on memory -- and a
    runner is small; only its `run.py` child is big.

    A PID file rather than scanning the process table: on Windows these are
    started through a launcher shim, so ONE logical runner shows up as TWO
    matching command lines and any count of them is wrong by construction.
    A stale lock from a killed run is detected and taken over, so a crash does
    not need a human to clear it.
    """
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    try:
        held = int(LOCK.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        held = 0
    if held and held != os.getpid() and _pid_alive(held):
        print(f"refusing to start: overnight run {held} is already going.")
        print("Two of them fight over the game and the save slot. Stop it, or")
        print(f"delete {LOCK} if you know it is dead.")
        return False
    LOCK.write_text(str(os.getpid()), encoding="utf-8")
    return True


def drop_lock() -> None:
    try:
        if LOCK.exists() and LOCK.read_text(encoding="utf-8").strip() == str(os.getpid()):
            LOCK.unlink()
    except OSError:
        pass


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    # THE MODS CHECKPOINT, because it is the one that LOADS. The observation
    # is 1652 wide and the vanilla shipped checkpoint is not -- `Advisor`
    # refuses it with one line and every combat turn of the night then has no
    # policy behind it, while route, torch, curio and camp carry on deciding.
    # A night that quietly plays without a combat policy looks like a working
    # night in the summary, which is why this is a default and not a note.
    ap.add_argument("--model",
                    default="models/dd_combat_ppo_full_expedition_mods.zip")
    ap.add_argument("--max-iterations", type=int, default=12)
    ap.add_argument("--raids-per-campaign", type=int, default=3,
                    help="how many raids to play before resetting the slot; "
                         "the first is always the Old Road tutorial")
    ap.add_argument("--slot", type=int, default=1,
                    help="which save slot to play. The tool can only ever "
                         "delete a slot it created, so a leftover it cannot "
                         "prove it owns makes that slot unusable -- point it "
                         "at a free one rather than deleting anything.")
    ap.add_argument("--until", default=None,
                    help="stop after this clock time, e.g. 07:00")
    ap.add_argument("--resume", action="store_true",
                    help="play the campaign that is ALREADY loaded "
                         "instead of creating one: no boot, no claim, "
                         "no teardown, nothing deleted")
    ap.add_argument("--dry", action="store_true",
                    help="drive the whole loop, send nothing, delete nothing")
    ap.add_argument("--force", action="store_true",
                    help="start even if something heavy is running")
    args = ap.parse_args(argv)

    busy = busy_with_something_heavy()
    if busy and not args.force:
        print(f"refusing to start: {busy}.")
        print("Both have crashed this machine beside a live run. --force to "
              "override.")
        return 2

    until = None
    if args.until:
        hh, mm = (int(x) for x in args.until.split(":"))
        until = datetime.now().replace(hour=hh, minute=mm, second=0,
                                       microsecond=0)
        if until <= datetime.now():
            until += timedelta(days=1)

    if not take_lock():
        return 3
    if STOP_FILE.exists():
        STOP_FILE.unlink()
    try:
        return _run_it(args, until)
    finally:
        drop_lock()


def _run_it(args, until) -> int:
    return Overnight(model=args.model, dry=args.dry,
                     max_iterations=args.max_iterations, until=until,
                     raids_per_campaign=args.raids_per_campaign,
                     slot=args.slot, resume=args.resume).run()


if __name__ == "__main__":
    sys.exit(main())
