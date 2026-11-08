"""Booting the game: title screen -> a new campaign -> inside the Old Road.

    python bridge/menus.py --dry        # say what it would click, send nothing
    python bridge/menus.py --slots      # which save slot is free, and is it reachable
    python bridge/menus.py --act        # actually do it

## Why the tutorial

`GAME_DIR/scripts/starting_save/persist.map.json` is hand-authored, so the Old
Road is byte-identical every single run:

    rooA  ->  coAB t0 t1 t2 t3 t4 t5  ->  rooB
                    ^battle    ^curio        ^final room (the goal)

Two rooms, one six-tile corridor, no branches, no mash rolls, torch 100. Every
corridor fix so far came out of Fran narrating a stall mid-raid and it being
guessed at afterwards; against a fixed layout a corridor regression becomes a
failing assertion instead of an anecdote.

Getting there is shorter than it looks. `starting_save/persist.game.json` ships
as `{"inraid": true, "raiddungeon": "tutorial"}`, so **creating the save slot
drops the party straight into the raid** -- there is no town step and no
quest to embark on. `captures/00_8_old_road.png` is the LOADING SCREEN, and
`app.log` records its crest as `Continue pressed in Loading Screen`
immediately before `as_raid_start_done_loading`.

## It is a state machine, not a click sequence

A recorded sequence of clicks with sleeps between them is the failure mode this
project keeps re-learning: it cannot tell a slow screen from a wrong one. So
every step here is keyed on what `bridge/screen.py` says is actually up, and
the acknowledgement is that the screen CHANGED -- not that a timer expired.

`save_select` appears twice in the flow and needs no special case, because the
click is the same both times: row `slot`, which holds the empty letter before
the campaign exists and the new campaign after. The screen-change wait
disambiguates them.

## The slot rule, which is a disk rule and not a pixel rule

DD renders save rows in slot order and puts the empty letter at the **first
free slot index**. On Fran's machine `profile_0` is used and `profile_1` is
free, and both captures show the letter (then the new campaign) in row 2 --
exactly that rule. So the target row is derived from `SAVE_ROOT.glob(
"*/262060/remote/profile_*")` and never from the screen.

**Two rows are visible and there is no scrolling here.** The list viewport runs
955..1319 of 1440 and the screen is black below. So the first free slot must be
0 or 1; at 2 or more this refuses and says so. That is a real limit on
iterating: each tutorial run consumes a slot, so the second run needs the first
one deleted -- **and this module never deletes anything.** The delete X sits
185px from `DD_Model_1.2` (Week 22) and a wrong row destroys it with no undo.

`-profile N` was the hoped-for way out and is not one: probed 2026-08-17, it
selects an EXISTING slot and crashes the game at boot when the slot is absent,
which is exactly the case that matters here. See `BRIDGE.md`.

## Mouse only, so `send-input` only

The rest of the bridge leans on DD's hotkeys and needs the mouse for one click
per turn. The front end has no hotkeys at all, which makes it the first thing
to depend entirely on clicking -- and that is how `post-message`'s click was
found not to register. Its MOVE lands (the Campaign button highlights, so the
coordinate and the client-rect maths are right) and the button press that
follows does nothing. `act.DEFAULT_BACKEND` is `send-input` because of it.
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bridge import applog, screen
from bridge.act import (
    Actuator, InputPlan, KillSwitch, Step, VK_RETURN, VK_SPACE, refuse,
)
from bridge.layout import VISIBLE_SAVE_ROWS
from bridge.live_state import PROFILE_GLOB, SAVE_ROOT

# How long to let one screen stay on screen after we acted on it before
# offering the click again. Was 12s, which is a long time to watch a menu do
# nothing when the recovery is simply to click once more; the front end has no
# animation slower than this except the campaign load, and `UNKNOWN_GRACE`
# covers that separately.
SCREEN_TIMEOUT = 6.0
# A pause between two steps of one plan when the second depends on the first
# having been registered -- currently only the campaign-type screen, where the
# click selects and the RETURN confirms.
SELECT_SETTLE = 0.6
# Loading a campaign takes a while and shows things this module has no
# reference for. Unknown is tolerated for this long -- and poked along with a
# click in the dead zone -- before it becomes a pause.
UNKNOWN_GRACE = 45.0
# How many clicks one screen may eat before it is declared unresponsive.
MAX_REPEAT = 3
# Except on the loading screen, where repeating IS the mechanism. Its Continue
# crest does nothing until the raid has finished loading, so the click has to
# be offered again and again rather than once -- reported live: "you need to
# wait till the game loads so needs to keep re-clicking". Harmless to repeat
# (an inert button), and bounded like everything else. The screen after it is
# the dungeon, which has no reference, so `app.log` is what ends the wait.
# `dungeon_intro` is the SAME SHAPE and was not in this list, which cost a
# whole campaign on 2026-08-31. SPACE dismisses the dungeon's title plate --
# but only once the dungeon has finished loading, and the plate stays up
# meanwhile. Measured live: three SPACE presses over 12 s, the screen still
# reading `dungeon_intro`, `MAX_REPEAT` reached, "PAUSED - did not respond to
# 3 clicks". A minute later the party was standing in the Ruins. **The boot had
# worked.** The night called it `boot-failed` and deleted the campaign.
#
# The tell is in the `loading` comment above: the screen after this one is the
# dungeon, which has no reference, so `app.log` is what ends the wait -- and
# `app.log` cannot report a raid that has not started loading yet. A repeat
# counter measures "is this screen responding", which is the wrong question
# for a gate that is waiting on a load.
PATIENT_SCREENS = {"loading": 25, "dungeon_intro": 12,
                   "dungeon_intro_darkest": 12}
# The overall budget, DERIVED rather than picked. The real sequence is six
# actions, so a dozen is generous -- but a flat 24 silently overrode the
# loading screen's 25 and stopped the run one click short of its own
# allowance. Two bounds that can disagree about which one is in force are one
# bound too many.
MAX_STEPS = 12 + max(PATIENT_SCREENS.values())
# How long to wait for a screen to respond before offering the click again.
# Short on a patient screen: the point there is cadence, not patience.
SCREEN_RETRY = 1.5
POLL = 0.6
# A dry run announces each screen once and then waits for the human. Without a
# heartbeat that is indistinguishable from a hang -- which is exactly how the
# first one was read.
DRY_HEARTBEAT = 15.0


def first_free_slot(save_root: Path = SAVE_ROOT,
                    glob: str = PROFILE_GLOB) -> int:
    """The `N` of the first `profile_N` that does not exist.

    That is where DD puts the empty letter, and therefore which row to click.
    Derived from the directory listing rather than from the rendered list
    because the rendered list scrolls and the directory does not.
    """
    used = set()
    for p in Path(save_root).glob(glob):
        tail = p.name.rsplit("_", 1)[-1]
        if tail.isdigit():
            used.add(int(tail))
    n = 0
    while n in used:
        n += 1
    return n


#: How far one click of the scrollbar's down arrow moves the list. MEASURED
#: on the live game 2026-09-03 with slots {0, 1, 8} on disk, by reading each
#: visible row's info column (only a campaign draws location / week / date):
#:
#:     clicks 0 -> CAMPAIGN CAMPAIGN   (slots 0, 1)
#:     clicks 1 -> letter   letter     (slots 2, 3)
#:     clicks 2 -> letter   letter     (slots 4, 5)
#:     clicks 3 -> letter   CAMPAIGN   (slots 7, 8)  and unchanged to 12
#:
#: So the arrow PAGES -- one click per viewport, not per row -- and it clamps
#: at the bottom. Both halves matter: paging is why the count is small, and
#: clamping is why over-clicking is safe.
SAVE_ROWS_PER_CLICK = VISIBLE_SAVE_ROWS


def total_save_rows(save_root: Path = SAVE_ROOT,
                    glob: str = PROFILE_GLOB) -> int:
    """How many rows the save list draws.

    One per slot index up to the highest campaign on disk, plus the empty
    letter at the first free slot -- whichever reaches further. With
    `profile_0`, `profile_1` and `profile_8` that is 9 rows: two campaigns,
    six letters, and `profile_8` last. Confirmed by scrolling to the bottom
    and finding exactly one campaign row there.
    """
    campaigns = campaign_slots(save_root, glob)
    highest = max(campaigns) + 1 if campaigns else 0
    return max(highest, first_free_slot(save_root, glob) + 1)


#: What makes a `profile_N` directory a CAMPAIGN rather than just a directory.
#: `profile_9` on this machine is the Butcher's Circus multiplayer profile --
#: `persist.circus_estate.json`, `persist.mp_progression.json`,
#: `persist.rankings.json`, nine files and no `persist.game.json` -- and DD
#: does not draw it in the campaign list at all. Counting it added a tenth row
#: that does not exist and moved every scroll target by one.
CAMPAIGN_MARKER = "persist.game.json"


def is_campaign(path: Path) -> bool:
    """Does this `profile_N` directory hold a campaign DD will list?"""
    return (Path(path) / CAMPAIGN_MARKER).is_file()


def campaign_slots(save_root: Path = SAVE_ROOT,
                   glob: str = PROFILE_GLOB) -> set:
    """The slot numbers DD draws a campaign row for."""
    out = set()
    for p in Path(save_root).glob(glob):
        tail = p.name.rsplit("_", 1)[-1]
        if tail.isdigit() and is_campaign(p):
            out.add(int(tail))
    return out


def scroll_plan(slot: int, total: int, visible: int = VISIBLE_SAVE_ROWS,
                per_click: int = SAVE_ROWS_PER_CLICK
                ) -> Optional[tuple[int, int]]:
    """`(down-arrow clicks, which visible row the slot lands on)`, or None.

    The offsets the arrow can reach are `0, per_click, 2*per_click, ...`
    clamped to `total - visible`, so this picks the FEWEST clicks that bring
    `slot` into view and then says where it will be. Fewest, because every
    click is an input into a list of campaigns and the cheapest way to click
    the wrong one is to send more clicks than the answer needs.

    None when the slot is not on the list at all. It never returns a row for a
    scroll it cannot reach: the caller must be able to tell "scroll there" from
    "there is no there".
    """
    if total <= 0 or not (0 <= slot < total):
        return None
    max_offset = max(0, total - visible)
    if slot < visible:
        return 0, slot
    clicks = -(-(slot - visible + 1) // per_click)          # ceil division
    offset = min(clicks * per_click, max_offset)
    row = slot - offset
    if not (0 <= row < visible):
        return None
    return clicks, row


# --- is the row we are about to click the KIND we expect? ------------------
#
# The geometry above is measured on one machine at one resolution, and the way
# it fails is not an exception -- it is clicking a different campaign. So the
# click is gated on the row's own CONTENT as well.

#: The location / week / date column, and how bright it has to read. A
#: campaign draws text there; an empty letter draws nothing. Measured on two
#: live frames: campaign rows 8.4 and 10.8, letter rows 1.8.
SAVE_INFO_X = (0.625, 0.780)
SAVE_INFO_BAND = 0.42
SAVE_INFO_LEVEL = 4.0


def row_info_level(img, row: int) -> float:
    """Mean brightness of a visible row's info column. 0.0 if unreadable."""
    import numpy as np                                # noqa: PLC0415
    from bridge.layout import SAVE_ROW_PITCH, SAVE_ROW_Y0   # noqa: PLC0415

    if img is None or getattr(img, "ndim", 0) != 3:
        return 0.0
    h, w = img.shape[:2]
    centre = (SAVE_ROW_Y0 + SAVE_ROW_PITCH * row) * h
    half = SAVE_INFO_BAND * SAVE_ROW_PITCH * h
    y0, y1 = int(max(0, centre - half)), int(min(h, centre + half))
    x0, x1 = int(SAVE_INFO_X[0] * w), int(SAVE_INFO_X[1] * w)
    if y1 - y0 < 4 or x1 - x0 < 4:
        return 0.0
    return float(np.asarray(img[y0:y1, x0:x1], dtype=float).mean())


def row_holds_a_campaign(img, row: int) -> bool:
    """Is this visible row a saved campaign rather than an empty letter?"""
    return row_info_level(img, row) > SAVE_INFO_LEVEL


def campaign_row(slot: int, save_root: Path = SAVE_ROOT,
                 glob: str = PROFILE_GLOB) -> Optional[int]:
    """The row an EXISTING campaign is drawn on, or None if it has none.

    **The row IS the slot**, and this exists only to answer "is there a
    campaign there at all" without a caller re-deriving the glob.

    That was checked rather than assumed, because the obvious alternative --
    a COMPACT list, one row per campaign with holes closed up -- is very
    nearly as consistent with the evidence, and acting on it created a
    campaign nobody asked for. Scrolled to the bottom of the live list on
    2026-09-03 with `profile_0`, `profile_1` and `profile_8` on disk, DD
    draws: "Click the letter to begin..." , "Click the letter to begin...",
    then "Darkest - Hamlet, Week 6". Those are slots 6, 7 and **8**. Every
    free slot gets its own letter row, so row and slot are the same number
    and `profile_8` is on row 8 -- eight rows down a list two rows tall.

    `None` for a slot with no directory. Returning the row anyway would name
    an empty letter, and clicking that starts a NEW campaign rather than
    loading the one asked for: exactly what happened when this function
    briefly believed in the compact list.
    """
    return slot if slot in campaign_slots(save_root, glob) else None


def profile_dir(slot: int, save_root: Path = SAVE_ROOT,
                glob: str = PROFILE_GLOB) -> Optional[Path]:
    """Where `profile_<slot>` would live, derived from a sibling's path.

    There is no way to name the directory without knowing the Steam user id,
    and the id is only discoverable from an existing profile -- so a machine
    with no saves at all cannot be predicted, and this returns None rather
    than inventing a path to watch.
    """
    for p in sorted(Path(save_root).glob(glob)):
        return p.parent / f"profile_{slot}"
    return None


# ── the table ──

def plan_for(name: str, resolved, *, slot: int, scrolled: int = 0,
             total_rows: Optional[int] = None) -> InputPlan:
    """One recognised screen -> one plan. Pure; no window, no sending.

    Keyed on the names `bridge/screen.py` produces, so the recogniser's
    vocabulary and this table cannot drift apart.

    `scrolled` is how many down-arrow clicks the caller has already sent on
    THIS visit to the save list. It is a parameter rather than state because
    this function has to stay pure -- the runner owns the count and resets it
    the moment the screen changes.
    """
    def click(point_name: str, why: str, layer: str = "menu") -> InputPlan:
        pt = resolved.menu_point(point_name)
        if pt is None:
            return refuse(f"no coordinate for {point_name}", layer)
        return InputPlan((Step("click", screen=pt, label=point_name),),
                         layer, why)

    if name in ("cinematic", "message"):
        return click("dead_zone", f"skip the {name}", "intro")

    if name == "title":
        return click("campaign", "open the campaign list")

    if name == "save_select":
        # THE ROW IS THE SLOT. Every free slot draws its own "Click the letter
        # to begin..." row, so the list is indexed by slot number and not
        # packed -- see `campaign_row`, which was written on the opposite
        # belief and cost a campaign nobody asked for.
        #
        # **AND A SLOT PAST THE VIEWPORT IS SCROLLED TO, not refused.**
        # `scroll_plan` and `MENU_SAVE_SCROLL_DOWN` were both written and
        # tested and neither was ever reached from here, so every campaign
        # below the second row was unloadable -- which on this machine is the
        # only campaign the bridge owns. The refusal below still stands for a
        # slot the arrow genuinely cannot bring into view.
        rows = total_save_rows() if total_rows is None else total_rows
        where = scroll_plan(slot, rows)
        if where is None:
            return refuse(f"slot {slot} is not on a list of {rows} row(s)",
                          "menu")
        clicks, row = where
        if scrolled < clicks:
            # ONE click per plan, never the whole run in one go. The list
            # animates, and a plan that sends four clicks blind cannot be
            # stopped halfway by the kill switch or checked against what the
            # rows now say.
            pt = resolved.menu_point("save_scroll_down")
            if pt is None:
                return refuse("no coordinate for save_scroll_down", "menu")
            return InputPlan(
                (Step("click", screen=pt, label="scroll the save list"),),
                "menu", f"scroll down ({scrolled + 1} of {clicks}) to reach "
                        f"slot {slot}")
        pt = resolved.save_row(row)
        if pt is None:
            return refuse(
                f"slot {slot} is row {slot + 1} and only {VISIBLE_SAVE_ROWS} "
                f"rows are visible - delete a campaign by hand, or launch "
                f"with -profile N", "menu")
        # Deliberately does NOT say which of the two this row is. The same
        # click serves the empty letter before a campaign exists, that
        # campaign afterwards, and an existing save `--resume` names; only
        # the caller knows which it asked for.
        return InputPlan((Step("click", screen=pt, label=f"save row {slot}"),),
                         "menu", f"slot {slot}, row {row} of the visible list")

    if name == "dlc":
        return click("dlc_ok", "accept the DLC list as it ships")

    if name == "modal":
        return click("modal_ok", "Start Campaign")

    if name == "difficulty":
        pt = resolved.menu_point("darkest")
        if pt is None:
            return refuse("no coordinate for darkest", "menu")
        # RETURN accepts the estate-name field, which DD leaves defaulted to
        # the campaign type -- which is why the created save is called
        # "Darkest" in `00_7_After_creating_save.png`.
        #
        # **These two steps are NOT independent**, which is why the click asks
        # for `SELECT_SETTLE` after it. The click selects and the RETURN
        # confirms, and at the default 80ms gap the RETURN lands before the
        # selection has registered: the same plan went through on one run and
        # left the screen sitting on the next -- "did get stuck by selecting
        # but not clicking darkest difficulty".
        return InputPlan((Step("click", screen=pt, label="darkest",
                               after=SELECT_SETTLE),
                          Step("key", vk=VK_RETURN, label="confirm the name")),
                         "menu", "Darkest, then accept the default name")

    if name == "town_event":
        # "Hear Ye, Hear Ye!" -- the announcement DD raises on entering
        # the Hamlet on a festival week (the Butcher's Circus, a
        # plague, a fete). It decides nothing the bridge cares about and
        # it swallows every click aimed at the town underneath, so it is
        # CLOSED rather than read. Without this the boot refused it as an
        # unmodelled screen and threw the whole campaign away.
        pt = resolved.town_close_panel
        if pt is None:
            return refuse("no coordinate for the panel close", "menu")
        return InputPlan((Step("click", screen=pt, label="close event"),),
                         "menu", "dismiss the town event")

    if name == "loading":
        return click("loading_continue", "Continue into the Old Road")

    if name == "dungeon_intro":
        # The plate DD shows over a loaded dungeon -- "Press [SPACE] or
        # [CLICK] to Continue", in its own words, so the key is the game's
        # instruction and needs no coordinate. The boot ends here rather than
        # in the raid, and without this the whole campaign was thrown away as
        # a failed boot at the very last step.
        return InputPlan((Step("key", vk=VK_SPACE, label="continue"),),
                         "menu", "past the dungeon's title plate")

    return refuse(f"unmodelled screen {name!r}", "menu")


# ── the runner ──

@dataclass
class BootRunner:
    """Drive the front end until a raid is running, or stop and say why.

    Holds no policy. It recognises, looks the screen up in `plan_for`, sends,
    and waits for the screen to change -- and every way it can fail ends in a
    printed reason rather than in another click.
    """
    window: object
    resolved: object
    actuator: Actuator
    slot: int = 0
    armed: bool = False
    log: object = None
    steps: int = 0
    _raid_before: int = 0
    unknown_clicks: int = 0
    seen: list = field(default_factory=list)
    #: Down-arrow clicks sent on THIS visit to the save list. Reset the moment
    #: the screen is anything else, because the list re-opens at the top.
    scrolled: int = 0
    _frame: object = None

    def _say(self, text: str) -> None:
        try:
            print(text)
        except UnicodeEncodeError:
            print(text.encode("cp1252", "replace").decode("cp1252"))
        if self.log:
            self.log.write(text + "\n")
            self.log.flush()

    def _look(self) -> tuple[str, list]:
        from bridge.window import refresh as refresh_window
        cur = refresh_window(self.window)
        if cur is not None:
            self.window = cur
            self.resolved = self.resolved.frac.resolve(cur)
        img = screen.grab(self.window)
        self._frame = img
        if img is None:
            return "", []
        return screen.rank(img)

    def run(self, *, timeout: float = 300.0) -> bool:
        """True once `app.log` says a raid is running.

        Ctrl-C is a normal way to stop this -- it holds the foreground and can
        sit waiting on a human for minutes -- so it exits with a summary
        rather than a traceback. That matters more once armed: an interrupt
        halfway through the front end should say what it already clicked.
        """
        try:
            return self._run(timeout=timeout)
        except KeyboardInterrupt:
            done = " -> ".join(self.seen) if self.seen else "nothing"
            self._say(f"\n  [boot] stopped by hand after {self.steps} "
                      f"action(s): {done}")
            return False

    def _run(self, *, timeout: float) -> bool:
        raid_before = self._raid_before = applog.count(applog.RAID_RUNNING)
        deadline = time.perf_counter() + timeout
        unknown_since = None
        last, repeats, announced = None, 0, time.perf_counter()

        while time.perf_counter() < deadline:
            if self.actuator.kill.check():
                self._say("  [boot] KILL SWITCH - stopping")
                return False
            if applog.count(applog.RAID_RUNNING) > raid_before:
                self._say("  [boot] the raid is running (app.log)")
                return True
            if self.steps >= MAX_STEPS:
                self._say(f"  [boot] {MAX_STEPS} steps without reaching a raid "
                          f"- stopping. Saw: {' -> '.join(self.seen)}")
                return False

            name, scored = self._look()
            if not name:
                if unknown_since is None:
                    unknown_since = time.perf_counter()
                    near = "  ".join(f"{n}={d:.2f}" for n, d in scored[:3])
                    self._say(f"  [boot] unrecognised screen ({near or 'no refs'})")
                if time.perf_counter() - unknown_since > UNKNOWN_GRACE:
                    # Print the scores, not just the verdict. "Unknown screen"
                    # alone cannot be acted on; the distances say whether this
                    # is a screen with no reference (everything ~0.9) or one
                    # that two references are arguing over (two close values),
                    # which are different bugs with different fixes.
                    near = "  ".join(f"{n}={d:.3f}" for n, d in scored[:3])
                    self._say(f"  [boot] PAUSED - still cannot name this screen "
                              f"after {UNKNOWN_GRACE:.0f}s ({near or 'no refs'}). "
                              f"Nothing will be clicked.")
                    return False
                self._nudge()
                time.sleep(POLL)
                continue

            unknown_since = None
            if name in self.IN_CAMPAIGN:
                self._say(f"  [boot] a campaign is loaded ({name})")
                return True
            if name != "save_select":
                self.scrolled = 0
            plan = plan_for(name, self.resolved, slot=self.slot,
                            scrolled=self.scrolled)
            # **A NEW CAMPAIGN WANTS ANY EMPTY LETTER, NOT A COMPUTED ROW.**
            # Only when the slot asked for holds no campaign -- loading a
            # named save still goes through the arithmetic, because there the
            # row identifies WHICH save and a wrong one is a wrong campaign.
            # A REFUSAL IS NOT A STARTING POINT. `plan_for` refuses a slot
            # that is not on the list at all, and turning that into "scroll
            # and look" would carry on past the one thing that was certain.
            # Nor does this run on a frame it cannot read: a search that
            # cannot see must not be the thing that decides, which is the
            # rule `_save_row_looks_wrong` already carries.
            if (name == "save_select" and not plan.refusal
                    and campaign_row(self.slot) is None
                    and self._frame is not None
                    and getattr(self._frame, "ndim", 0) == 3):
                found, why = self._empty_letter_plan()
                if found is not None:
                    plan = found
                elif self.scrolled < self.MAX_LETTER_SCROLLS:
                    pt = self.resolved.menu_point("save_scroll_down")
                    if pt is not None:
                        plan = InputPlan(
                            (Step("click", screen=pt,
                                  label="scroll the save list"),),
                            "menu", f"{why} - scrolling to find one "
                                    f"({self.scrolled + 1} of "
                                    f"{self.MAX_LETTER_SCROLLS})")
                else:
                    plan = refuse(
                        f"{why} after {self.scrolled} scroll(s) - the list "
                        f"may be scrolled past them", "menu")
            if name == "save_select" and not plan.refusal:
                stop = self._save_row_looks_wrong(plan)
                if stop:
                    self._say(f"  [boot] REFUSING the save list: {stop}")
                    return False
            if name != last:
                # Say WOULD out loud when nothing is armed. The mode is
                # announced once at startup and then the log reads exactly
                # like a real run, which is how a dry run gets mistaken for a
                # broken one.
                lead = "[boot]" if self.armed else "[boot] WOULD"
                self._say(f"  {lead} [{name}] {plan}")
                repeats, announced = 0, time.perf_counter()
            last = name
            if plan.refusal:
                return False
            if not self.armed:
                # A dry run sends nothing, so the screen CANNOT change on its
                # own -- which makes "it did not respond" meaningless here. It
                # follows the operator instead: click through the menus by
                # hand and it narrates what it would have clicked, which is a
                # better check of the table than any offline test.
                #
                # ...but announcing a screen once then going quiet for minutes
                # is indistinguishable from having hung, so it says so.
                if time.perf_counter() - announced > DRY_HEARTBEAT:
                    waited = time.perf_counter() - announced
                    self._say(f"  [boot] still on {name} after {waited:.0f}s "
                              f"- move on, or re-run with --act to send it")
                    announced = time.perf_counter()
                time.sleep(POLL)
                continue
            if not self._focused():
                return False
            if not self.actuator.execute(plan):
                time.sleep(POLL)
                continue
            self.steps += 1
            self.seen.append(name)
            if name == "save_select" and plan.steps                     and plan.steps[0].label == "scroll the save list":
                # A scroll leaves the screen NAMED the same, so the repeat
                # guard would call four legitimate clicks a stuck screen.
                # Progress here is the offset, not the screen name.
                self.scrolled += 1
                repeats = 0
            # Repeats count ACTIONS SENT on one screen, not trips round the
            # loop. Counting the latter made a dry run declare itself stuck
            # after three looks at a title screen it was never going to click.
            repeats += 1
            allowed = PATIENT_SCREENS.get(name, MAX_REPEAT)
            if repeats >= allowed:
                self._say(f"  [boot] PAUSED - {name!r} did not respond to "
                          f"{allowed} clicks")
                return False
            self._await_change(name, patient=name in PATIENT_SCREENS)
        self._say("  [boot] timed out")
        return False

    #: How many scroll clicks the new-campaign search may spend before it
    #: gives up. One is enough on any disk seen so far -- the list scrolls
    #: about three rows per click -- and the bound matters because scrolling
    #: PAST the letters lands on the campaigns at the bottom, where searching
    #: harder finds nothing and clicking would load somebody's save.
    MAX_LETTER_SCROLLS = 3

    def _empty_letter_plan(self):
        """Click a row the FRAME says is an empty letter. `(plan, why)`.

        **The slot arithmetic cannot be trusted and no longer has to be.**
        `scroll_plan` needs three numbers to be right at once -- how many rows
        the list holds, how many are visible, and how far one arrow click
        scrolls -- and measured live 2026-09-21 the last two are both wrong:
        `VISIBLE_SAVE_ROWS` says 2 where the viewport draws 3, and one click
        moves about three rows rather than `SAVE_ROWS_PER_CLICK`. The arrival
        row it computes is therefore a guess, and the guard that catches the
        guess then refuses the boot.

        None of it is needed for a NEW campaign. `reset.claim_created` owns
        whatever slot the game ends up making, so the requirement is no longer
        "the row for slot N" -- it is "any empty letter", and the frame says
        which rows those are. `row_holds_a_campaign` is the same predicate the
        safety gate already uses, so this cannot disagree with the check that
        follows it.

        Returns `(None, why)` when there is no letter in view, so the caller
        can scroll and look again rather than click something it did not
        identify.
        """
        img = self._frame
        if img is None or getattr(img, "ndim", 0) != 3:
            return None, "no frame to read the rows from"
        for row in range(VISIBLE_SAVE_ROWS):
            if row_holds_a_campaign(img, row):
                continue
            pt = self.resolved.save_row(row)
            if pt is None:
                continue
            return InputPlan(
                (Step("click", screen=pt, label=f"save row {row}"),),
                "menu", f"the empty letter drawn on visible row {row}"), ""
        return None, (f"none of the {VISIBLE_SAVE_ROWS} visible rows is an "
                      f"empty letter")

    def _save_row_looks_wrong(self, plan) -> str:
        """Refuse a save-row click whose row does not hold what disk says.

        **CLICKING AN EMPTY LETTER STARTS A NEW CAMPAIGN**, and that has
        already happened once here on a wrong belief about how the list is
        indexed. The disk knows whether slot N holds a campaign and the frame
        knows whether the row about to be clicked draws one, so the two have
        to agree before an irreversible click goes out.

        Two-sided on purpose. Expecting a campaign and finding a letter means
        the scroll landed somewhere else; expecting a letter and finding a
        campaign means the same thing, and clicking it would LOAD a campaign
        the caller did not ask for. Neither is recoverable by trying again.

        Empty string when the click is safe -- including when the frame cannot
        be read, since a gate that cannot see must not also be the thing that
        decides. `plan_for` is what refuses an unreachable slot.
        """
        if not plan.steps or not str(plan.steps[0].label).startswith("save row"):
            return ""
        img = self._frame
        if img is None or getattr(img, "ndim", 0) != 3:
            return ""
        where = scroll_plan(self.slot, total_save_rows())
        if where is None:
            return ""
        _clicks, row = where
        drawn = row_holds_a_campaign(img, row)
        wanted = campaign_row(self.slot) is not None
        if drawn == wanted:
            return ""
        if wanted:
            return (f"slot {self.slot} holds a campaign on disk but visible "
                    f"row {row} draws an empty letter - clicking it would "
                    f"START a campaign, not load one")
        return (f"slot {self.slot} has no campaign on disk but visible row "
                f"{row} draws one - clicking it would LOAD somebody else's")

    # Screens that mean "a campaign is loaded and playable".
    #
    # The Old Road's template ships `inraid: true`, so this module was written
    # around "a raid started" as the only finish line. That is ONE outcome of
    # creating a campaign, not the definition of it: a boot that raced past the
    # difficulty screen still produced a real, loaded campaign sitting in the
    # Hamlet, and calling that a failure threw away a working save AND made the
    # overnight loop discard every iteration that took the other path. The
    # Hamlet is also where the town layer's work is.
    #
    # Tested against the name the loop ALREADY has rather than by polling
    # again: a second `_look()` per iteration doubles the screen grabs and,
    # in the tests, consumes a screen from the scripted sequence.
    IN_CAMPAIGN = ("hamlet", "town", "estate_map")

    def _nudge(self) -> None:
        """A click in the dead zone, to push an animation along.

        Only after something has already been recognised and acted on: an
        unknown screen at the very start is a screen this module has never
        seen, not a transition of its own making, and poking it is exactly the
        blind click the rest of the bridge refuses to make.
        """
        if not (self.armed and self.steps) or self.unknown_clicks >= 12:
            return
        pt = self.resolved.menu_point("dead_zone")
        if pt is None:
            return
        self.unknown_clicks += 1
        self.actuator.execute(InputPlan(
            (Step("click", screen=pt, label="dead zone"),), "intro",
            "advance whatever is playing"))

    def _await_change(self, was: str, *, patient: bool = False) -> None:
        """Wait for the screen to move on, or for the raid to start.

        **The raid starting counts as the screen changing**, and has to: what
        follows the loading screen is the dungeon, which has no reference, so
        waiting on the recogniser alone would sit through the full timeout
        after the click that actually worked.

        `patient` shortens the wait rather than lengthening it. On the loading
        screen the click is inert until the load finishes, so the useful
        behaviour is to come back and offer it again soon, not to stare.
        """
        end = time.perf_counter() + (SCREEN_RETRY if patient else SCREEN_TIMEOUT)
        while time.perf_counter() < end:
            time.sleep(POLL)
            if applog.count(applog.RAID_RUNNING) > self._raid_before:
                return
            now, _ = self._look()
            if now != was:
                return
        if not patient:
            self._say(f"  [boot] {was!r} did not change in {SCREEN_TIMEOUT:.0f}s")

    def _focused(self) -> bool:
        """Foreground, TAKEN if possible and always VERIFIED.

        The boot chain is the one part of the bridge that runs before a human
        could plausibly be watching -- `tools/overnight.py` calls it once per
        iteration, all night -- and it used to wait 30 s for a click that was
        never coming and then give up on the whole campaign. `act` already
        knows how to take the foreground properly (`AttachThreadInput`, since
        `SetForegroundWindow` alone merely flashes the taskbar for a
        background process); it simply was not wired to here.

        Taking it is an ATTEMPT and nothing more: the wait loop below is
        unchanged and still has the final say, so a human clicking the window
        works exactly as it did.
        """
        if getattr(self.window, "is_foreground", False):
            return True
        from bridge.act import _force_foreground
        from bridge.window import refresh as refresh_window
        _force_foreground(self.window.hwnd)
        time.sleep(0.35)
        cur = refresh_window(self.window)
        if cur is not None and cur.is_foreground:
            self.window = cur
            self.resolved = self.resolved.frac.resolve(cur)
            return True
        self._say("  [boot] the game is not focused - click it")
        for _ in range(120):
            time.sleep(0.25)
            from bridge.window import refresh as refresh_window
            cur = refresh_window(self.window)
            if cur is not None and cur.is_foreground:
                self.window = cur
                self.resolved = self.resolved.frac.resolve(cur)
                time.sleep(0.35)
                return True
            if self.actuator.kill.check():
                return False
        self._say("  [boot] gave up waiting for focus")
        return False


def build(process: str = "Darkest.exe", backend: Optional[str] = None,
          armed: bool = False, slot: Optional[int] = None, log=None
          ) -> Optional[BootRunner]:
    """`backend=None` takes `act.DEFAULT_BACKEND`, which is `send-input`.

    The front end is all mouse and no hotkeys, so it is the one part of the
    bridge that cannot fall back on the keyboard -- and `post-message`'s click
    does not register (see `act.choose_backend`). Naming a backend here rather
    than inheriting the default is almost always a mistake.
    """
    from bridge.act import DEFAULT_BACKEND, choose_backend
    from bridge.layout import load as load_layout
    from bridge.window import find_window
    win = find_window(process)
    if win is None:
        return None
    lay = load_layout()
    if lay is None:
        return None
    resolved = lay.resolve(win)
    return BootRunner(window=win, resolved=resolved,
                      actuator=Actuator(backend=choose_backend(win, backend or DEFAULT_BACKEND),
                                        enabled=armed, kill=KillSwitch()),
                      slot=first_free_slot() if slot is None else slot,
                      armed=armed, log=log)


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Boot the game into the tutorial.")
    ap.add_argument("--dry", action="store_true",
                    help="the default: send nothing and follow you through "
                         "the menus, naming each screen and the click it "
                         "would have made")
    ap.add_argument("--act", action="store_true", help="actually click")
    ap.add_argument("--slots", action="store_true",
                    help="which save slot is free, and is it reachable")
    ap.add_argument("--slot", type=int, default=None)
    ap.add_argument("--backend", default=None,
                    help="default: send-input. post-message's "
                         "click does not register in DD.")
    ap.add_argument("--process", default="Darkest.exe")
    ap.add_argument("--timeout", type=float, default=300.0)
    args = ap.parse_args(argv)

    if args.slots:
        slot = first_free_slot()
        where = profile_dir(slot)
        used = sorted(p.name for p in SAVE_ROOT.glob(PROFILE_GLOB))
        print(f"profiles on disk: {', '.join(used) or 'none'}")
        print(f"first free slot : {slot}  -> row {slot + 1} of "
              f"{VISIBLE_SAVE_ROWS} visible  (every free slot draws its own "
              f"letter, so the row IS the slot)")
        print(f"would become    : {where}")
        rows = total_save_rows()
        campaigns = sorted(campaign_slots())
        print(f"list draws      : {rows} row(s), {VISIBLE_SAVE_ROWS} visible; "
              f"campaign(s) at slot {campaigns or 'none'}")
        for candidate in sorted({slot, *campaigns}):
            plan = scroll_plan(candidate, rows)
            what = "campaign" if candidate in campaigns else "empty letter"
            if plan is None:
                print(f"  slot {candidate:2d} ({what:13s}) UNREACHABLE - "
                      f"not on a list of {rows} row(s)")
                continue
            clicks, row = plan
            how = "visible" if not clicks else f"{clicks} scroll click(s)"
            print(f"  slot {candidate:2d} ({what:13s}) {how}, then visible "
                  f"row {row}")
        return 0

    runner = build(args.process, args.backend, armed=args.act, slot=args.slot)
    if runner is None:
        from bridge.layout import load as _load                # noqa: PLC0415
        from bridge.window import diagnose                     # noqa: PLC0415
        print("no calibrated layout - run: python bridge/layout.py --resolve"
              if _load() is None else diagnose(args.process))
        return 1
    if not screen.load_refs():
        print("no screen references - run: python bridge/screen.py --calibrate")
        return 1
    if args.act:
        print(f"boot: slot {runner.slot}, ACTING - F9 stops it, "
              f"and the game must hold focus")
    else:
        print(f"boot: slot {runner.slot}, DRY RUN - it will NOT click "
              f"anything.\n      Walk the menus yourself and it names what it "
              f"would have done; add --act to let it play.")
    return 0 if runner.run(timeout=args.timeout) else 1


if __name__ == "__main__":
    sys.exit(main())
