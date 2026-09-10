"""Walk the screens between one raid and the next.

    python bridge/town.py --where              # which screen, and what it would do
    python bridge/town.py --to-town            # raid results -> the Hamlet
    python bridge/town.py --to-town --act
    python bridge/town.py --embark --act       # Hamlet -> provisioned -> in a raid

A raid ending does not return the party to the Hamlet, and reaching the Hamlet
does not start the next raid. Between them sit six screens that are pure
clicking, and until now a human did them.

## One button does almost all of it

DD draws a single action control at the bottom centre and relabels it per
screen: `Next` on the raid results, `Return to Town` on the progression page,
`Embark` in the Hamlet, `Provision` on the estate map, `Embark` again in the
provisioner. They all resolve to `layout.RAID_RESULTS_NEXT`, measured once by
hue. So the chain is a table from a recognised screen to what that button
means there, not six calibrations.

Modals are the same story: every dialog's first option sits at `MODAL_OK`,
measured independently at the DLC dialog and the underprepared-embark dialog
and agreeing to (0.0000, 0.0007).

## It is a table, and everything not in it REFUSES

The screens here sit over the world and over a save list. A blind click is how
a party gets moved, a curio poked, or a campaign deleted -- so an unrecognised
screen stops the walk and says which one it was, and every step verifies that
the screen actually changed before taking the next.
"""
from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Callable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# What the bottom-centre control means on each screen, and therefore what
# clicking it does. A screen absent from this table is one the walk refuses.
ADVANCE = {
    # **A retreat or a wipe ends on a page headed "Defeat" over its own art.**
    # Same "Next" control at the bottom, and it used to be a reference of its
    # own -- `raid_results_defeat` -- because it does not match `raid_results`
    # and read UNKNOWN, which stopped the walk home dead after the first
    # Darkest Dungeon abandon.
    #
    # **TWO SIBLING REFERENCES THEN VETOED EACH OTHER, which is worse.** The
    # recogniser refuses when the runner-up is within `MARGIN` of the winner,
    # and on the live defeat page of 2026-09-05 the two scored 0.159 and
    # 0.308: a gap of 0.149 against a margin of 0.150. It read UNKNOWN again,
    # for the opposite reason, and stranded the loop after a lost raid --
    # which is the state a bridge that is still being debugged reaches most.
    #
    # They are now ONE screen with a variant (`raid_results@defeat`), which
    # is what `screen.VARIANT_SEP` is for: the runner-up test exists to stop
    # two screens wanting DIFFERENT clicks being confused, and these two want
    # the same click. `test_screens_that_want_one_click_are_ONE_screen` is
    # what keeps the two tables agreeing.
    "raid_results": "Next (the loot summary, or the defeat one)",
    "raid_progression": "Next (resolve and quirks)",
    "raid_progression_quirks": "Return to Town",
    "hamlet": "Embark",
    "estate_map": "Provision",
    "provision": "Embark",
}
# Screens that need something other than that button.
# Panels drawn OVER the Hamlet, each dismissed by its own X -- which DD puts
# at one place, `TOWN_CLOSE_PANEL`. `town_event` is the "Hear Ye, Hear Ye!"
# proclamation that greets a new week; it is a different picture from the
# activity log and so needs its own reference, but the same click.
CLOSE_PANEL = ("town", "town_event")

# Screens whose way out is a measured point OF THEIR OWN -- not the shared
# panel X, not the shared Next.
#
# **The Butcher's Circus is a screen the bridge reaches BY ACCIDENT.** Its
# marker is planted in the Hamlet on top of the stage coach, and the shipped
# scene point for the coach landed inside its hotspot, so the first
# unattended town visit of 2026-09-05 walked into the Circus and every
# iteration after it reported "not in the Hamlet (screen: UNKNOWN)". Nothing
# was damaged -- but nothing could proceed either, because there is no
# keyboard way back: ESC opens the pause menu, and its only exits are to the
# main menu and to the desktop.
LEAVE_AT = {"butchers_circus": "circus_exit"}

# **THE WALK HAS A DIRECTION, and for a long time it only had one.** `ADVANCE`
# is the FORWARD control on each screen, and `to_town` walked it toward the
# Hamlet -- which works from the raid-results chain, where forward and homeward
# are the same way, and is exactly backwards on the outbound embark chain.
#
# A night resuming on the estate map therefore clicked "Provision": away from
# town, and with no party seated it does nothing at all, so the walk retried it
# four times and gave up. Three consecutive iterations of 2026-09-06 died that
# way, each reporting "estate_map did not respond to 'Provision' after 4
# tries" -- a message that is precisely true and says nothing about the walk
# having been pointed the wrong way.
#
# Both screens draw the same red `<<<` at the left of their title bar, and it
# is measured as one point because it IS one widget: 113 px, span 12x17,
# identical centre on both. Consulted only by `to_town`; `to_raid` still walks
# `ADVANCE`, or the two would undo each other.
RETREAT = {"estate_map": "title_bar_back",
           "provision": "title_bar_back"}

# Screens dismissed by a KEY rather than a coordinate. DD says what to press
# on this one -- "Press [SPACE] or [CLICK] to Continue" -- so the key is the
# game's own instruction and needs no calibration.
# **Every region draws its OWN intro plate.** The structure is shared --
# a title, a gameplay tip, "Press [SPACE] or [CLICK] to Continue" -- but
# the art between them is the region's, and `SIG_BOX` is the centre of the
# frame, so one reference does not cover another. The Darkest Dungeon's
# read UNKNOWN, which let `to_raid` report "we are in the raid" while the
# plate was still up. A region added here needs its own capture.
PRESS = {"dungeon_intro": 0x20,           # VK_SPACE
         "dungeon_intro_darkest": 0x20}
# Any dialog: its first option is MODAL_OK, which DD draws at one fixed place.
# `embark_underprepared` is the "continue without a full contingent?" warning,
# and it is named separately only because the recogniser needs its own
# reference to tell it from `modal` at all -- the ACTION is identical.
CONFIRM = ("modal", "embark_underprepared")

# Where `to_town` is allowed to STOP. Only the Hamlet proper: the activity log
# ("town") is drawn over it on arrival and is a screen to close on the way, not
# a destination -- stopping there leaves a panel covering the Embark button.
ARRIVED = ("hamlet",)
# Anything that means "not in a raid", for the other direction.
TOWN_SCREENS = ("hamlet", "town", "town_event", "estate_map", "provision")

# Screens that ARE a building interior, so `_panel_is_open` can be a POSITIVE
# test instead of a refusal.
#
# `stage_coach_panel` was banked by `_bank_panel_frame` on the first live run
# that clicked the measured scene point -- the guard could not confirm the
# panel, banked the frame, and that frame WAS the open panel. It separates
# from every other reference by 0.700, far above the 0.35 threshold. Anything
# under `captures/screens/<name>.png` is auto-registered, so adding a building
# is a capture plus a name here.
BUILDING_SCREENS: tuple = ("stage_coach_panel",)

# Long enough that a click does not land inside the reveal animation on the
# progression page, which is where the first live walk lost one.
STEP_SETTLE = 1.8
RETRY_SETTLE = 1.5
SAME_SCREEN_TRIES = 4
# How many settles an unrecognised screen gets before the walk gives up on it.
UNKNOWN_TRIES = 8
# DD warns below eight food on a short quest, in its own words. Buying what it
# asks for is what stops that warning being another screen to handle.
FOOD_TO_BUY = 8
TORCHES_TO_BUY = 8
CHANGE_TIMEOUT = 25.0
POLL = 0.4
MAX_STEPS = 30


@dataclass
class Walked:
    ok: bool
    steps: int
    seen: list
    stopped_on: str
    why: str = ""


def _look(win) -> str:
    """Which screen is up -- and if the mouse is covering the answer, move it.

    `recognise_live` only parks the cursor when the first look came back with
    no name, so the walk pays for it exactly when a tooltip has made the frame
    unreadable. See its docstring for the measurement that made this
    necessary: a pointer left on a supply-cart item put the provision screen
    at 0.4401 against a 0.35 cut, and the embark reported an unrecognised
    screen for sixteen seconds.
    """
    from bridge import screen                        # noqa: PLC0415
    return screen.recognise_live(win)


def _await_change(win, was: str, timeout: float = CHANGE_TIMEOUT) -> str:
    """Wait for the screen to stop being `was`. Returns what it became.

    Transitions go through black frames that match nothing, so an
    unrecognised frame is not a stopping condition here -- only a DIFFERENT
    recognised screen is.
    """
    end = time.time() + timeout
    while time.time() < end:
        now = _look(win)
        if now and now != was:
            return now
        time.sleep(POLL)
    return _look(win)


def _modal_family(win) -> bool:
    """Is SOME dialog up, whichever one it is?

    **Telling modals apart is not the job; knowing one is up is.** DD draws
    every dialog's first option at the same fixed place, so the action is
    identical -- and the signature is the centre block, where a modal is
    centred text over a darkened copy of whatever it covers, which is exactly
    why they crowd each other and `recognise` returns UNKNOWN on a near-tie
    between two of them.

    Measured on the food warning: `modal` 0.159, `embark_underprepared` 0.287
    -- a 0.128 margin, under MARGIN, so no name at all. Both are modals, both
    take the same click, and the walk stalled for want of a label it did not
    need.

    So this asks the weaker question the caller actually has: is the best
    match a modal, and is it close enough to be one? It deliberately ignores
    MARGIN, which is safe ONLY because every member of the family shares a
    confirm button -- do not reuse this shape for screens that do not.
    """
    from bridge import screen                        # noqa: PLC0415
    img = screen.grab(win)
    if img is None:
        return False
    _name, scored = screen.rank(img)
    if not scored:
        return False
    best, distance = scored[0]
    return best in CONFIRM and distance < screen.THRESHOLD


def walk(win, backend, *, until: Callable[[str], bool], say=print,
         act: bool = False, budget: int = MAX_STEPS,
         buy: bool = True, basket: Optional[dict] = None,
         back: bool = False) -> Walked:
    """Click the chain until `until(screen)` says stop.

    `until` is a predicate rather than a screen name because the two ends of
    this walk are different KINDS of thing: "we are in the Hamlet" is a
    screen, and "we are in a raid" is not -- no screen says so, the save does.

    `back` consults `RETREAT` first, for the screens where homeward is not
    forward. It is a preference and not a separate table: every screen the
    retreat table does not name still walks `ADVANCE`, because most of the
    chain has only one control and pressing it is the way out either way.
    """
    from bridge.layout import load as load_layout    # noqa: PLC0415

    fractional = load_layout()
    if fractional is None:
        return Walked(False, 0, [], "", "no overlay_layout.json")
    resolved = fractional.resolve(win)

    seen: list = []
    stuck_on, stuck_count, unknown = "", 0, 0
    bought: set = set()
    for step in range(budget):
        here = _look(win)
        if here and (not seen or seen[-1] != here):
            seen.append(here)
        if until(here):
            return Walked(True, step, seen, here)

        if not here and not _modal_family(win):
            # A transition, not a dead end -- wait it out rather than clicking
            # into whatever is fading in. Bounded, though: an unrecognised
            # screen that never resolves used to burn the entire step budget
            # doing nothing and then report "no end", which says nothing about
            # what it was looking at.
            unknown += 1
            if unknown > UNKNOWN_TRIES:
                return Walked(False, step, seen, "",
                              f"an unrecognised screen for "
                              f"{unknown * STEP_SETTLE:.0f}s")
            time.sleep(STEP_SETTLE)
            continue
        unknown = 0

        if here == "provision" and buy and "provision" not in bought:
            bought.add("provision")
            _buy_basket(win, backend, resolved, basket, say=say, act=act)

        if here in PRESS:
            vk = PRESS[here]
            say(f"  [town] {here}: SPACE to continue")
            if not act:
                return Walked(False, step, seen, here, "not armed")
            backend.key(vk)
            time.sleep(STEP_SETTLE)
            _await_change(win, here)
            continue

        if here in CONFIRM or _modal_family(win):
            point = resolved.menu_point("modal_ok")
            what = f"confirm {here or 'an unnamed dialog'}"
        elif back and here in RETREAT:
            point = getattr(resolved, RETREAT[here])
            what = f"back out of {here}"
        elif here in LEAVE_AT:
            point = getattr(resolved, LEAVE_AT[here])
            what = f"leave {here}"
        elif here in CLOSE_PANEL:
            point, what = resolved.town_close_panel, "close the activity log"
        elif here in ADVANCE:
            point, what = resolved.raid_results_next, ADVANCE[here]
        else:
            return Walked(False, step, seen, here,
                          f"no move known for {here!r}")

        say(f"  [town] {here}: {what}  {point}")
        if not act:
            return Walked(False, step, seen, here, "not armed")
        backend.click(point)
        time.sleep(STEP_SETTLE)
        became = _await_change(win, here)
        if became == here:
            # **A swallowed click, not a dead screen.** These pages animate --
            # the progression page reveals the quirk rewards over a couple of
            # seconds -- and a click that lands mid-animation does nothing.
            # Measured: the same click that "failed" here worked on its own a
            # moment later, going quirks -> town in 3.6 s. So the same move is
            # tried again rather than the walk giving up, bounded so a screen
            # that genuinely does not respond still stops it.
            stuck_count = stuck_count + 1 if stuck_on == here else 1
            stuck_on = here
            if stuck_count >= SAME_SCREEN_TRIES:
                return Walked(False, step, seen, here,
                              f"{here} did not respond to {what!r} after "
                              f"{stuck_count} tries")
            say(f"  [town] {here} did not move - trying again "
                f"({stuck_count}/{SAME_SCREEN_TRIES})")
            time.sleep(RETRY_SETTLE)
        else:
            stuck_on, stuck_count = "", 0
    return Walked(False, budget, seen, _look(win), f"{budget} steps, no end")


def to_town(win, backend, *, say=print, act: bool = False) -> Walked:
    """From wherever a raid -- or a refused embark -- left us, to the Hamlet.

    `back=True`: this is the homeward direction, and on the two outbound
    screens homeward is not the same as forward. See `RETREAT`.
    """
    return walk(win, backend, until=lambda s: s in ARRIVED, say=say, act=act,
                back=True)


# ── inside a building ────────────────────────────────────────────────────
#
# `walk` above moves BETWEEN screens, one relabelled button at a time. This
# moves WITHIN one, and the difference is what it can trust.
#
# A screen transition is visible: `_await_change` watches the recogniser and
# knows the click landed. **Nothing inside a building panel changes the screen
# at all** -- buying an upgrade, trading heirlooms and hiring a recruit all
# leave the recogniser reading the same panel -- so the acknowledgement has to
# come from somewhere else, and the only honest source is the SAVE.
#
# That is the same save-is-truth / screen-is-geometry split the rest of the
# bridge runs on, and here it is not a preference: a click that silently did
# nothing and a click that worked are pixel-identical on this screen.

# How long to give DD to write a save after a purchase, a trade or a hire. It
# writes on the action rather than on a timer, so this is a bound on the write
# and not a settle time; a step that has not shown up by now did not happen.
SAVE_ACK_TIMEOUT = 6.0
SAVE_ACK_POLL = 0.25

# How long to let a building panel animate in before asking whether it opened.
# Its twin lives in `bridge/act.py` as the `Step.after` on the open click; this
# one is the extra settle before the SCREEN is read, and it is separate because
# a step gap and a recognition delay are different quantities that happen to be
# similar today.
PANEL_OPEN_SETTLE = 0.9

# Where the frame goes when the panel guard cannot confirm an open. One fixed
# name, overwritten, beside the run's own logs -- the same discipline
# `act.CURIO_SHOT` follows, and for the same reason: a night that misses six
# opens must not leave six near-identical PNGs.
PANEL_SHOT = Path(__file__).resolve().parent.parent / "archive" / "logs" /     "overnight" / "_panel_open.png"


@dataclass
class Did:
    """One attempted town action and whether the save agreed it happened."""
    what: str
    sent: bool = False
    confirmed: bool = False
    why: str = ""

    def __str__(self) -> str:
        if not self.sent:
            return f"SKIP {self.what}  ({self.why})"
        return (f"{'OK  ' if self.confirmed else 'UNCONFIRMED'} {self.what}"
                + (f"  ({self.why})" if self.why else ""))


def _wallet(profile) -> dict:
    """The heirloom wallet, or `{}` if it cannot be read.

    Read fresh each time rather than tracked in memory: the point of the check
    is that the GAME agrees, and a number this process is carrying forward
    agrees with itself by construction.
    """
    from bridge.town_state import read_town_state    # noqa: PLC0415
    try:
        return dict(read_town_state(profile).wallet or {})
    except Exception:                                # noqa: BLE001
        return {}


def _roster_guids(profile) -> set:
    from bridge.town_state import read_town_state    # noqa: PLC0415
    try:
        return {h.guid for h in read_town_state(profile).heroes}
    except Exception:                                # noqa: BLE001
        return set()


def _await(reader, before, timeout: Optional[float] = None):
    """Poll `reader` until it stops equalling `before`. Returns the new value.

    Returns `before` unchanged on timeout, so a caller compares rather than
    catching -- there is no exception to raise here, only a fact to report.

    **The timeout defaults to None and is resolved from the module global HERE,
    not in the signature.** A default argument binds once, at definition, so
    `SAVE_ACK_TIMEOUT` in the signature would be frozen at 6.0 forever and a
    test that lowered it would silently wait the full six seconds per action.
    That is not hypothetical -- it hung `tests/bridge/test_town_visit.py` past a
    two-minute timeout on the first run, thirteen visits deep.
    """
    if timeout is None:
        timeout = SAVE_ACK_TIMEOUT
    end = time.time() + timeout
    first = True
    while first or time.time() < end:
        first = False
        now = reader()
        if now and now != before:
            return now
        time.sleep(SAVE_ACK_POLL)
    return before


# A zero timeout still has to check ONCE -- otherwise a test that disables the
# wait also disables the acknowledgement it is trying to observe.



# **A PARKED CURSOR DRAWS A TOOLTIP, AND A TOOLTIP BREAKS `recognise`.**
# `screen.SIG_BOX` is the middle of the frame (x 0.34-0.65, y 0.28-0.63) and
# DD draws an item tooltip right there. Measured live on 2026-09-02: the
# basket bought perfectly, the cursor stayed on the last torch cell, its
# tooltip covered the signature box, and `walk` sat on "an unrecognised screen
# for 16 s" and gave up ON THE PROVISION SCREEN with the Embark button lit.
#
# Nothing was wrong with the buying or the walking. The cursor was in the way
# of the camera. So every town send parks it afterwards, on the far-left edge
# of the frame -- outside `SIG_BOX` on every town screen, and over inert
# scenery on all of them (the estate frame, the provisioner's shelf art, the
# Hamlet's trees).
CURSOR_PARK = (25.0 / 1920.0, 540.0 / 1080.0)


def _park_cursor(backend, resolved) -> None:
    """Move the mouse off whatever it just clicked. Best effort, never raises."""
    move = getattr(backend, "move", None)
    at = resolved.at(CURSOR_PARK) if resolved is not None else None
    if move is None or at is None:
        return
    try:
        move(at)
    except Exception:                                # noqa: BLE001
        pass


def _send(backend, plan, *, act: bool, say, resolved=None) -> bool:
    """Execute one `InputPlan`'s steps, or explain why nothing was sent.

    Deliberately NOT `Actuator.execute`: that owns the kill switch, the focus
    wait and the turn dedupe, all of which belong to the dungeon poll loop.
    This runs inside a bounded, sequential town visit that has already checked
    those once.
    """
    say(f"  [town] {plan}")
    if not plan.actionable or not act:
        return False
    for step in plan.steps:
        if step.kind == "click":
            backend.click(step.screen, mods=step.mods)
        elif step.kind == "drag":
            if not backend.drag(step.screen, step.to):
                return False
        elif step.kind == "key":
            backend.key(step.vk, step.hold)
        time.sleep(step.after or STEP_SETTLE)
    _park_cursor(backend, resolved)
    return True


def _panel_is_open(win) -> bool:
    """Did a building panel actually come up?

    **The check is one-sided on purpose, and it fails SAFE.** No building
    interior has a banked screen reference yet, so `recognise` returns nothing
    for one -- which means "we are inside a panel" is not directly observable.
    What IS observable is the opposite: the Hamlet and the activity-log screen
    both have references, so **still seeing one of those proves the panel did
    not open**.

    That asymmetry is exactly the guarantee needed here. Everything after the
    open -- upgrade nodes, recruit drags -- is clicked at coordinates that only
    mean anything inside the panel, so sending them onto a live Hamlet would
    click buildings in the scene and drag heroes across the town. The one
    reading that must never be wrong is "it opened when it had not".

    It matters right now rather than in theory: `town_screen_layout` carries
    `button_navigation_offscreen_pos -40 230` beside `button_navigation_pos
    70 230`, so the navigation strip has a hidden state and neither banked
    Hamlet capture draws it. The derived button may be pointing at a widget
    that is not on screen.

    Bank a reference for each interior and this can become the positive test
    it should be; until then, a refusal on the negative evidence is the honest
    version.
    """
    from bridge import screen                        # noqa: PLC0415
    img = screen.grab(win)
    if img is None:
        return False                                 # cannot see: do not act
    here = screen.recognise(img) or ""
    # **UNKNOWN means NOT CONFIRMED, and getting this backwards is what let the
    # first live run click six times into an open Hamlet.** The first version
    # returned `here not in TOWN_SCREENS`, so an unrecognised frame -- which is
    # what a mid-transition or an un-banked interior looks like -- read as
    # "the panel opened". A one-sided test has to say no when it cannot see,
    # not yes.
    #
    # So this stays False until a building interior has a banked reference to
    # recognise. That is deliberately conservative: it costs a refusal on a
    # panel that did open, and it prevents a sequence of clicks landing on a
    # town nobody meant to click.
    return here in BUILDING_SCREENS


def _on_town_screen(win) -> bool:
    """Is the bare town visible -- i.e. is nothing covering it?"""
    from bridge import screen                        # noqa: PLC0415
    img = screen.grab(win)
    return img is not None and (screen.recognise(img) or "") in TOWN_SCREENS


def _bank_panel_frame(win) -> str:
    """Screenshot the moment the panel guard gave up. Best effort.

    This is how the first building interior gets its reference: the guard
    refuses because nothing is banked, and the frame it banks is the thing to
    bank. `bank a frame at the moment anything fails` -- three of the last four
    root causes here were only visible in one.
    """
    try:
        from PIL import Image                        # noqa: PLC0415
        from bridge import screen                    # noqa: PLC0415
        img = screen.grab(win)
        if img is None:
            return ""
        PANEL_SHOT.parent.mkdir(parents=True, exist_ok=True)
        Image.fromarray(img).save(PANEL_SHOT)
        return f" (frame banked at {PANEL_SHOT.name})"
    except Exception:                                # noqa: BLE001
        return ""


def _tree_index(node) -> int:
    """Which upgrade TREE this node belongs to, as a panel column index.

    It was hardcoded `0`, which was invisible while `numrecruits` was the only
    tree on the priority path and became a real misclick the day `rostersize`
    joined it: both trees have an `a`, so the plan clicked the top path's
    already-owned first rank and reported the wallet not moving.

    The panel draws the trees in the order the upgrade file declares them, and
    `load_building_upgrades` preserves that order, so the index is the
    position of this node's tree in that list.
    """
    from bridge.townplan import GAME_DIR, load_building_upgrades  # noqa: PLC0415
    building = node.tree_id.split(".")[0]
    seen: list = []
    for n in load_building_upgrades(str(GAME_DIR), building):
        if n.tree_id not in seen:
            seen.append(n.tree_id)
    return seen.index(node.tree_id) if node.tree_id in seen else 0


# -- the heirloom exchange ------------------------------------------------
#
# The panel is drawn over the bottom-left corner of the Hamlet, outside
# `screen.SIG_BOX`, so `recognise` cannot see it -- the trick that confirms a
# building panel does not work here. What replaces it is a colour probe on the
# panel's own icons, which answers a stronger question than "is it open":
# **which heirloom is the spinner about to spend.**
#
# The reference icons are the CURRENCY BAR's, taken from the same frame, so
# the match is immune to the game's own grading and to any brightness setting.
# Measured distances on a live panel: bust 16.9, portrait 7.5, deed 11.6,
# crest 17.7, shard 46.6, against a next-best of 74.6 for the worst of them.

# Currency-bar icon centres, measured on a live Hamlet at 1280x720. The bar is
# HUD furniture rather than layout-file geometry, hence measured -- and it is
# read, never clicked.
EXCHANGE_BAR_ICONS = {
    "bust":     (248.6 / 1280.0, 669.9 / 720.0),
    "portrait": (295.1 / 1280.0, 666.4 / 720.0),
    "deed":     (347.9 / 1280.0, 671.0 / 720.0),
    "crest":    (393.6 / 1280.0, 669.5 / 720.0),
    "shard":    (440.5 / 1280.0, 669.0 / 720.0),
}
# How far around an icon to sample, as a fraction of the frame width.
EXCHANGE_PROBE_R = 10.0 / 1280.0
# A pixel is part of an icon rather than the panel behind it above this.
EXCHANGE_INK = 60.0
# The worst acceptable colour distance, and how far the runner-up must be --
# the same shape of test `bridge/screen.py` uses, and for the same reason: a
# near-tie is not a recognition.
# Measured over the five spinner positions, on frames banked under
# `captures/exchange/`: distances 9.8 / 22.3 / 22.5 / 34.1 / 49.0 with
# runner-up margins 40.1 / 43.9 / 4.6 / 5.5 / 28.0, seven of seven named
# correctly. The bounds sit just outside that -- deliberately, because this is
# the outer of THREE gates (the spinner read, the destination-row read, and an
# exact wallet delta), so a tight margin here costs a refusal rather than a
# wrong trade.
EXCHANGE_ICON_TOLERANCE = 55.0
EXCHANGE_ICON_MARGIN = 3.0
# A settle after each spinner click. The icon cross-fades.
EXCHANGE_SPIN_SETTLE = 0.7
EXCHANGE_TOGGLE_SETTLE = 1.1
#: How many times to re-send the toggle before admitting the panel is stuck.
#: Two, because the first click can land during the open animation and be
#: eaten; a third would be guessing rather than retrying.
EXCHANGE_CLOSE_ATTEMPTS = 2


def _img_pt(img, frac):
    """A layout fraction -> a pixel in a GRABBED FRAME, not on the screen.

    `ResolvedLayout.at` answers where to click, which is a desktop coordinate;
    this answers where to look, which is an index into the client bitmap. Both
    go through the same letterbox correction, so a probe and a click on one
    fraction cannot disagree about where the widget is.
    """
    from bridge.layout import CALIBRATED_ASPECT, play_rect   # noqa: PLC0415
    if img is None or frac is None:
        return None
    h, w = img.shape[:2]
    ox, oy, pw, ph = play_rect(w, h, CALIBRATED_ASPECT)
    return (ox + frac[0] * pw, oy + frac[1] * ph)


def _ink(img, frac):
    """Mean colour of the drawn pixels around `frac`, or None if too few.

    Averaging only the lit pixels is what makes this stable: the panel's
    background is a dark gradient and including it would drag every icon's
    mean toward the same purple.
    """
    import numpy as np                               # noqa: PLC0415
    pt = _img_pt(img, frac)
    if pt is None:
        return None
    h, w = img.shape[:2]
    r = max(4, int(round(EXCHANGE_PROBE_R * w)))
    x, y = int(round(pt[0])), int(round(pt[1]))
    box = np.asarray(img[max(0, y - r):y + r, max(0, x - r):x + r, :3],
                     dtype=float)
    if box.size == 0:
        return None
    lit = box.max(axis=2) > EXCHANGE_INK
    return box[lit].mean(axis=0) if lit.sum() >= 10 else None


def _name_heirloom(img, frac, refs):
    """`(name, distance, margin)` for the icon drawn at `frac`.

    Returns `("", inf, 0.0)` when nothing is drawn there, which is what a
    closed panel looks like and is therefore not an error.
    """
    import numpy as np                               # noqa: PLC0415
    here = _ink(img, frac)
    if here is None or not refs:
        return "", float("inf"), 0.0
    scored = sorted((float(np.abs(here - v).sum()), k)
                    for k, v in refs.items() if v is not None)
    if not scored:
        return "", float("inf"), 0.0
    best = scored[0]
    second = scored[1][0] if len(scored) > 1 else best[0] + 999.0
    return best[1], best[0], second - best[0]


def _bar_refs(img) -> dict:
    """The currency bar's own icons, from THIS frame."""
    return {k: _ink(img, v) for k, v in EXCHANGE_BAR_ICONS.items()}


def _exchange_spinner(win):
    """Which heirloom the exchange is set to give away, or "" if not open.

    One-sided in the same direction as `_panel_is_open`: an unreadable frame
    is "not open", never "open and showing what I expected".
    """
    from bridge import screen                        # noqa: PLC0415
    from bridge import townlayout as TL              # noqa: PLC0415
    img = screen.grab(win)
    if img is None:
        return ""
    name, dist, margin = _name_heirloom(img, TL.exchange_from_icon(),
                                        _bar_refs(img))
    if dist > EXCHANGE_ICON_TOLERANCE or margin < EXCHANGE_ICON_MARGIN:
        return ""
    return name


def open_exchange(win, backend, resolved, *, say=print,
                  act: bool = False) -> str:
    """Toggle the exchange shut and open again, and say what it is showing.

    **Always a fresh open, never "it is already up".** Fran: "when you click
    the trade heirlooms button you always appear on the busts" -- so a panel
    just opened is a KNOWN state, type on bust and count on bust's cheapest
    rate, while a panel left over from the previous trade is whatever that
    trade left behind. The count cannot be read back (it is a rendered digit),
    so the only way to know it is to have just created it.
    """
    from bridge import act as act_mod                # noqa: PLC0415
    if not act:
        say("  [town] WOULD open the heirloom exchange")
        return ""
    if _exchange_spinner(win):
        _send(backend, act_mod.plan_open_exchange(resolved), act=act, say=say)
        time.sleep(EXCHANGE_TOGGLE_SETTLE)
    _send(backend, act_mod.plan_open_exchange(resolved), act=act, say=say)
    time.sleep(EXCHANGE_TOGGLE_SETTLE)
    return _exchange_spinner(win)


def close_exchange(win, backend, resolved, *, say=print,
                   act: bool = False) -> bool:
    """Put the exchange panel away, and say whether it actually went.

    **The exchange is an OVERLAY on the Hamlet, and the building scene points
    are underneath it.** `do_exchange` used to return with the panel still up,
    and the very next thing `visit_stage_coach` does after a trade is click
    the stage coach at its scene point -- which is a coordinate inside the open
    overlay. Measured live 2026-09-06 on a fresh campaign: the trade confirmed
    (crest 20 -> 17, deed 7 -> 8), the re-open did not, and the two hire drags
    then went into a Hamlet with an exchange panel over it. Both were
    correctly reported UNCONFIRMED and neither should have been sent at all.

    It is the same toggle that opens it, so closing is one click -- and the
    acknowledgement is the pixel test, because no save is written when a panel
    closes. One-sided in the same direction as everything else here: an
    unreadable frame reads as "not open", so this can report success on a
    frame it could not see, and never the reverse.
    """
    from bridge import act as act_mod                # noqa: PLC0415
    if not act:
        return True
    for _ in range(EXCHANGE_CLOSE_ATTEMPTS):
        if not _exchange_spinner(win):
            return True
        _send(backend, act_mod.plan_open_exchange(resolved), act=act, say=say)
        time.sleep(EXCHANGE_TOGGLE_SETTLE)
    shut = not _exchange_spinner(win)
    if not shut:
        say("  [town] the exchange panel would not close")
    return shut


def do_exchange(win, backend, resolved, profile, trade, *, say=print,
                act: bool = False) -> "Did":
    """One trade, spun to by reading the panel and checked against the wallet.

    The wallet check is EXACT rather than "something moved": a spinner one
    position out spends a different heirloom, and "the wallet changed" would
    call that a success. `visit_stage_coach` stops the whole visit on a
    failure here, because the next purchase was costed against a wallet that
    no longer exists.

    **Whoever opened the panel closes it, on every path out** -- including the
    failures, which are exactly the paths that used to leave it up. See
    `close_exchange` for what that cost.
    """
    from bridge import townlayout as TL              # noqa: PLC0415

    want = trade.rate.from_type
    if want not in TL.EXCHANGE_CYCLE:
        return Did(trade.label(), why=f"{want} is not on the spinner")

    if not act:
        say(f"  [town] WOULD trade {trade.label()}")
        return Did(trade.label(), why="dry run")

    here = open_exchange(win, backend, resolved, say=say, act=act)
    if not here:
        return Did(trade.label(),
                   why="the exchange panel did not open, or its icon "
                       "could not be read")
    try:
        return _trade_in_open_exchange(win, backend, resolved, profile,
                                       trade, here, say=say, act=act)
    finally:
        close_exchange(win, backend, resolved, say=say, act=act)


def _trade_in_open_exchange(win, backend, resolved, profile, trade, here, *,
                            say=print, act: bool = False) -> "Did":
    """The spin, the two reads and the confirm, with the panel known open.

    Split out of `do_exchange` so the close can be a `finally` rather than a
    line repeated on each of six returns -- one of which would inevitably be
    missed, which is the shape of the bug this replaces.
    """
    from bridge import townlayout as TL              # noqa: PLC0415
    from bridge import act as act_mod                # noqa: PLC0415
    from data.heirlooms import load_rates            # noqa: PLC0415

    want = trade.rate.from_type
    cycles = ((TL.EXCHANGE_CYCLE.index(want) - TL.EXCHANGE_CYCLE.index(here))
              % len(TL.EXCHANGE_CYCLE))
    # The count spinner opens at the cheapest rate the type has, so the clicks
    # are the difference rather than `times - 1`.
    starts = [r.from_amount for r in load_rates(str(TL.GAME_DIR))
              if r.from_type == want]
    if not starts:
        return Did(trade.label(), why=f"{want} has no exchange rate")
    count_clicks = trade.spends - min(starts)
    say(f"  [town] exchange: showing {here}, want {want} "
        f"({cycles} spins), give {trade.spends} "
        f"(opens at {min(starts)}, {count_clicks} clicks)")

    plan = act_mod.plan_exchange(trade, resolved, cycles=cycles,
                                 count_clicks=count_clicks)
    if not plan.actionable:
        return Did(trade.label(), why=plan.why)

    # Spin first and RE-READ, so a mis-click is caught before the confirm --
    # which is the only irreversible step in the town.
    for _ in range(cycles):
        backend.click(resolved.at(TL.exchange_type_arrow(up=True)))
        time.sleep(EXCHANGE_SPIN_SETTLE)
    now = _exchange_spinner(win)
    if now != want:
        return Did(trade.label(), sent=True, confirmed=False,
                   why=f"the spinner reads {now or 'UNKNOWN'} after "
                       f"{cycles} clicks, not {want} - nothing was confirmed")
    for _ in range(count_clicks):
        backend.click(resolved.at(TL.exchange_count_arrow(up=True)))
        time.sleep(EXCHANGE_SPIN_SETTLE)

    row = TL.exchange_to_row(want, trade.rate.to_type)
    # **Check the destination too, before the only irreversible click.** The
    # row is derived from `HEIRLOOMS` order, which was measured on all four
    # spinner positions -- but confirming the wrong row buys the wrong
    # heirloom just as surely as spinning to the wrong one, and the panel is
    # right there to be read.
    from bridge import screen                        # noqa: PLC0415
    frame = screen.grab(win)
    seen, dist, margin = _name_heirloom(frame, TL.exchange_to_icon(row),
                                        _bar_refs(frame))
    if seen != trade.rate.to_type:
        return Did(trade.label(), sent=True, confirmed=False,
                   why=f"to-row {row} reads {seen or 'UNKNOWN'} "
                       f"(d={dist:.0f}, margin={margin:.0f}), "
                       f"not {trade.rate.to_type} - nothing was confirmed")
    before = _wallet(profile)
    say(f"  [town] confirm on to-row {row} ({trade.rate.to_type})")
    backend.click(resolved.at(TL.exchange_confirm(row)))
    after = _await(lambda: _wallet(profile), before)
    if after == before:
        return Did(trade.label(), sent=True, confirmed=False,
                   why="the wallet did not move")
    got = {k: after.get(k, 0) - before.get(k, 0) for k in set(before) | set(after)}
    want_delta = {trade.rate.from_type: -trade.spends,
                  trade.rate.to_type: trade.gains}
    moved = {k: v for k, v in got.items() if v}
    if moved != want_delta:
        return Did(trade.label(), sent=True, confirmed=False,
                   why=f"the wallet moved by {moved}, not {want_delta}")
    return Did(trade.label(), sent=True, confirmed=True)


def visit_stage_coach(win, backend, profile, *, store=None, say=print,
                      act: bool = False, roster_room: int = 2) -> list:
    """Trade, upgrade and recruit, in that order, verifying each against the save.

    **The order is a dependency, not a preference.** A trade pays for an
    upgrade, and an upgrade (`numrecruits`) changes how many heroes the coach
    offers -- so hiring last is the only order in which each step sees what the
    previous one bought. It mirrors the RANKS -> SKILLS -> TRINKETS ordering
    `CompSpace.finish` follows, and for the same reason.

    Returns a list of `Did`, one per attempted action. It never raises: a town
    visit that half-worked is a normal outcome to report, and the caller
    (`tools/overnight.py`) has a raid to get on with either way.
    """
    from bridge import act as act_mod                # noqa: PLC0415
    from bridge.layout import load as load_layout    # noqa: PLC0415
    from bridge.town_state import read_town_state    # noqa: PLC0415
    from bridge import townplan                      # noqa: PLC0415

    done: list = []
    fractional = load_layout()
    if fractional is None:
        return [Did("open the stage coach", why="no overlay_layout.json")]
    resolved = fractional.resolve(win)

    try:
        town = read_town_state(profile)
    except Exception as exc:                         # noqa: BLE001
        return [Did("read the town", why=f"{type(exc).__name__}: {exc}")]

    upgrades_file = None
    if profile is not None:
        candidate = Path(profile) / "persist.upgrades.json"
        if candidate.exists():
            upgrades_file = candidate

    advice = townplan.advise(town, store=store, upgrades_file=upgrades_file,
                             roster_room=roster_room)
    for line in advice.lines():
        say(f"  [town]{line}")
    if advice.empty:
        return [Did("nothing to do in town", why="; ".join(advice.notes))]

    # **A panel that is ALREADY open must not be opened again.** The scene
    # point is a building in the Hamlet, and with the coach up it is a
    # coordinate INSIDE the panel -- so re-sending it clicks whatever the
    # panel happens to draw there. The visit can legitimately start here: a
    # human left it open, or a previous step ended inside one.
    # **An exchange overlay left over from anywhere is shut FIRST.** Unlike a
    # building panel it does not replace the Hamlet, it sits on top of it -- so
    # the recogniser still reads a town screen, `_panel_is_open` is right that
    # no panel is up, and the building scene points are simply underneath it.
    # Seen live 2026-09-06, with the overlay left behind by an earlier visit:
    # the first click of the next visit landed on the leftover panel's header,
    # nothing opened, and the guard below correctly refused -- having spent a
    # blind click into a live game to find that out.
    close_exchange(win, backend, resolved, say=say, act=act)
    already = _panel_is_open(win)
    if already:
        say("  [town] the stage coach is already open")
    opened = already or _send(
        backend, act_mod.plan_open_building("stage_coach", resolved),
        act=act, say=say)
    # **Sent is not open.** Everything below clicks coordinates that only mean
    # something inside the panel, so this is the gate that stops a missed
    # navigation button turning into upgrade clicks and hero drags scattered
    # over a live Hamlet.
    if opened and not already:
        time.sleep(PANEL_OPEN_SETTLE)
        if not _panel_is_open(win):
            note = _bank_panel_frame(win)
            # **Leave nothing open on the way out.** The guard cannot tell
            # "did not open" from "opened but has no banked reference", and in
            # the second case returning here would leave a panel sitting over
            # the Hamlet -- which then swallows the embark walk's clicks and
            # reports as an unrecognised screen with no clue why. So the close
            # is attempted whenever the screen is NOT a town screen, which is
            # the one reading that says something is covering it.
            if not _on_town_screen(win):
                _send(backend, act_mod.plan_close_building(resolved),
                      act=act, say=say)
            done.append(Did("open the stage coach", sent=True, confirmed=False,
                            why=f"could not confirm the panel opened{note}"))
            return done
    # `sent` means input was sent, so an already-open panel is not a send --
    # a dry run that reported one would be claiming to have clicked.
    done.append(Did("open the stage coach", sent=opened and not already,
                    confirmed=opened,
                    why="it was already open" if already else ""))
    if not opened:
        return done

    # The trees are not drawn until the crest is clicked -- see
    # `act.plan_open_upgrades`. Only bother when something is actually going
    # to be bought; it is a view switch, but a pointless click is still a
    # click into a live game.
    if advice.purchases:
        _send(backend, act_mod.plan_open_upgrades(resolved), act=act, say=say)

    # 1. trades, then 2. the upgrades they pay for
    for purchase in advice.purchases:
        # **The trade happens on the TOWN screen, not in the panel.** The
        # exchange toggle sits on the Hamlet's currency bar, which the coach
        # panel covers -- so the panel is closed for the trades and reopened
        # for the purchase they paid for.
        if purchase.trades:
            _send(backend, act_mod.plan_close_building(resolved),
                  act=act, say=say)
        stop = False
        for trade in purchase.trades:
            got = do_exchange(win, backend, resolved, profile, trade,
                              say=say, act=act)
            done.append(got)
            # **A misfired trade stops the visit.** Everything after it was
            # costed against a wallet that no longer exists, and the next
            # purchase would be sent at a price nobody has checked.
            stop = act and got.sent and not got.confirmed
            if stop:
                break
        if stop:
            # **Return, do not fall through to the hires.** Breaking out of
            # the purchase loop alone left the coach panel closed -- it is
            # closed for a trade, because the exchange toggle is on the bar
            # underneath it -- and the hire drags then went into a live
            # Hamlet. Every one was correctly reported UNCONFIRMED, and none
            # of them should have been sent at all.
            _send(backend, act_mod.plan_close_building(resolved),
                  act=act, say=say)
            return done
        if purchase.trades:
            reopened = _send(backend,
                             act_mod.plan_open_building("stage_coach", resolved),
                             act=act, say=say)
            if reopened:
                time.sleep(PANEL_OPEN_SETTLE)
                if not _panel_is_open(win):
                    # **Return, do not `break`.** Same rule as the misfired
                    # trade above, and it was missed one branch lower: a
                    # `break` leaves the purchase loop and falls straight into
                    # the hires, which then drag across whatever is actually
                    # on screen. Measured live 2026-09-06 -- two recruits
                    # dragged into an open exchange panel, both correctly
                    # UNCONFIRMED, neither one that should have been sent.
                    if not _on_town_screen(win):
                        _send(backend, act_mod.plan_close_building(resolved),
                              act=act, say=say)
                    done.append(Did("re-open the stage coach", sent=True,
                                    confirmed=False,
                                    why="could not confirm the panel reopened"))
                    return done
                _send(backend, act_mod.plan_open_upgrades(resolved),
                      act=act, say=say)
        # The trades are gone from the plan by the time it is built, which is
        # what `plan_buy_upgrade` refuses on -- so rebuild it without them.
        paid = type(purchase)(node=purchase.node, trades=())
        before = _wallet(profile)
        sent = _send(backend,
                     act_mod.plan_buy_upgrade(paid, _tree_index(purchase.node),
                                              resolved),
                     act=act, say=say)
        after = _await(lambda: _wallet(profile), before) if sent else before
        done.append(Did(purchase.node.label(), sent=sent,
                        confirmed=sent and after != before,
                        why="" if after != before else "the wallet did not move"))

    # 3. the hires. A recruit DOES write the roster, unlike a pack
    #    rearrangement -- so unlike a drag in a dungeon, this one can be
    #    acknowledged by the save rather than by pixels.
    row = len(town.heroes)
    # **The coach panel compacts after every hire**, so a recruit's row on
    # screen is its index minus however many above it have already been taken.
    # Hiring by the planning-time index dragged from an empty row on the
    # second hire and the roster, correctly, did not grow.
    taken: list = []
    for hire in advice.hires:
        panel_row = hire.index - sum(1 for i in taken if i < hire.index)
        before = _roster_guids(profile)
        sent = _send(backend, act_mod.plan_hire(hire, _drop_row(row), resolved,
                                                panel_row=panel_row),
                     act=act, say=say)
        after = _await(lambda: _roster_guids(profile), before) if sent else before
        done.append(Did(f"hire {hire.recruit.hero_class}", sent=sent,
                        confirmed=sent and len(after) > len(before),
                        why="" if len(after) > len(before)
                            else "the roster did not grow"))
        if len(after) > len(before):
            row += 1
            taken.append(hire.index)

    _send(backend, act_mod.plan_close_building(resolved), act=act, say=say)
    return done


# -- the post-tutorial week skip ------------------------------------------
#
# Fran's procedure, and it is a PROCEDURE rather than a decision: the two
# weeks after the tutorial are skipped by embarking ONE hero into the Darkest
# Dungeon and abandoning the quest in the first room. The week ticks, the
# stage coach refreshes, and the roster gains four heroes a skip.
#
#   1. Embark            Hamlet -> the estate map, which is where the party
#                        bar and the roster list are actually drawn
#   2. clear the bar     drag every occupied slot back onto the roster
#   3. seat one hero     alone, in RANK 1 -- the RIGHTMOST slot
#   4. the Darkest Dungeon, provisioned with nothing
#   5. abandon in the first room, before any hallway
#   6. hire everything the refreshed coach is offering
#
# **The Darkest Dungeon reading is the LITERAL one, and it was settled by the
# running game rather than by the files.** `quest.restriction.json` gates DD
# quests behind Resolve 5, which is why an earlier session ruled this reading
# out as un-embarkable at week 1 -- and the game embarks anyway. The Vestal's
# refusal line is flavour, and the sacrifice DD warns about did not fire on a
# solo abandon: she came home carrying the virtue Calm.
#
# **Almost nothing here can be acknowledged by the save.** The party bar is
# not written until Embark, so a drag that did nothing and a drag that worked
# leave the same file -- the same problem a building panel has, and the reason
# `Did` reports `sent` and `confirmed` separately. What DOES move is the bar
# itself, so every party drag is confirmed by the bar's own pixels changing.
# The hires are the exception and keep the save check, because a recruit
# writes `persist.roster.json`.

WEEK_SKIP_REGION = "darkest_dungeon"
# A portrait animates into its slot; a second grab during that lands on
# nothing. Measured generously -- the whole sequence is nine drags.
DRAG_SETTLE = 1.4
# `town_roster_list_layout.element_max`. Past this the list scrolls, so a row
# index beyond it is not on screen and there is nothing to drop onto.
ROSTER_ROWS_VISIBLE = 8
PARTY_RANKS = 4
# The only size the party layout states is `hero_slot_spacing` (93 across), so
# it stands in for the slot when cropping the bar to compare it.
PARTY_SLOT_SIZE = 93.0
# How many dialogs one abandon may raise. DD asks to confirm the retreat and
# the Darkest Dungeon adds its own warning; the bound is what stops a
# mis-recognition clicking MODAL_OK forever.
ABANDON_CONFIRMS = 4
ABANDON_SETTLE = 2.2
# Mean pixel levels of difference that count as "the bar changed". The same
# bar re-grabbed differs by rounding and by the light flickering behind it; a
# portrait arriving or leaving is worth tens of levels.
BAR_CHANGED = 1.5


def _party_bar(win, resolved):
    """The party bar as a small float image, or None.

    This is the acknowledgement channel for a party drag. Pixels rather than
    the save, because the save does not carry the bar until the party actually
    embarks -- see the note at the top of this section.
    """
    import numpy as np                               # noqa: PLC0415
    from bridge import screen                        # noqa: PLC0415
    from bridge import townlayout as TL              # noqa: PLC0415
    first, last = TL.party_slot(0), TL.party_slot(PARTY_RANKS - 1)
    if first is None or last is None:
        return None
    img = screen.grab(win)
    if img is None:
        return None
    # **`_img_pt`, not `resolved.at`** -- this crops a GRABBED FRAME, and
    # `resolved.at` answers in DESKTOP pixels. Cropping a 1280x720 frame at a
    # desktop coordinate of ~1300 gives an empty slice, so the whole
    # acknowledgement channel silently returned None: a live run reported five
    # party drags as UNCONFIRMED while the frame showed the hero seated. Same
    # class of confusion the exchange probe hit, which is why `_img_pt` exists.
    a = _img_pt(img, first)
    b = _img_pt(img, (last[0] + PARTY_SLOT_SIZE / 1920.0,
                      last[1] + PARTY_SLOT_SIZE / 1080.0))
    if a is None or b is None:
        return None
    x0, y0 = max(0, int(a[0])), max(0, int(a[1]))
    x1, y1 = min(img.shape[1], int(b[0])), min(img.shape[0], int(b[1]))
    if x1 - x0 < 8 or y1 - y0 < 8:
        return None
    return np.asarray(img[y0:y1, x0:x1], dtype=float)


def _party_slots(win, resolved):
    """The four party slots as separate crops, or None if unreadable.

    **`_party_bar` is one crop of the whole strip, and that is the wrong
    granularity for "are there four heroes on it".** It answers "did anything
    change", which is the right acknowledgement for ONE drag and cannot tell a
    fourth hero arriving from a seated one being moved between ranks -- the
    bar changes either way, and the second leaves three.

    Per slot the question becomes decidable, and it needs no empty-versus-
    occupied threshold: `clear_party` runs first, so the slots have a known
    EMPTY baseline, and a slot that differs from its own cleared self is a
    slot something was dropped into. Relative, like every other pixel test
    here.
    """
    import numpy as np                               # noqa: PLC0415
    from bridge import screen                        # noqa: PLC0415
    from bridge import townlayout as TL              # noqa: PLC0415
    img = screen.grab(win)
    if img is None:
        return None
    half = PARTY_SLOT_SIZE / 1920.0 / 2
    out = []
    for index in range(PARTY_RANKS):
        centre = TL.party_portrait(index)
        if centre is None:
            return None
        a = _img_pt(img, (centre[0] - half, centre[1] - half))
        b = _img_pt(img, (centre[0] + half, centre[1] + half))
        if a is None or b is None:
            return None
        x0, y0 = max(0, int(a[0])), max(0, int(a[1]))
        x1, y1 = min(img.shape[1], int(b[0])), min(img.shape[0], int(b[1]))
        if x1 - x0 < 8 or y1 - y0 < 8:
            return None
        out.append(np.asarray(img[y0:y1, x0:x1], dtype=float))
    return out


def _slots_filled(empty, full) -> Optional[int]:
    """How many slots gained something, or None if either read failed."""
    if empty is None or full is None or len(empty) != len(full):
        return None
    return sum(1 for a, b in zip(empty, full) if _bar_moved(a, b))


def _bar_moved(before, after) -> bool:
    """Did the party bar change? False when either frame is missing.

    Failing to False is deliberate and matches `_panel_is_open`: not being
    able to see is not evidence that something happened.
    """
    import numpy as np                               # noqa: PLC0415
    if before is None or after is None or before.shape != after.shape:
        return False
    return float(np.abs(before - after).mean()) > BAR_CHANGED


def _drop_row(row: int) -> int:
    """Where to DROP a hero onto the roster list, given the next free row.

    Clamped to the last visible row, because the list scrolls and a row past
    `ROSTER_ROWS_VISIBLE` is not on screen -- `roster_portrait` returns None
    for one, which used to end a hire loop with "no roster row 8" the moment a
    campaign reached eight heroes. That is the ordinary state after one week
    skip, so it stopped the second batch of hires outright.

    Dropping onto an OCCUPIED row is fine, and that is measured rather than
    assumed -- see `_park_row`.
    """
    return min(row, ROSTER_ROWS_VISIBLE - 1)


def _park_row(town) -> Optional[int]:
    """A roster row to drop a hero out of the party onto.

    **An OCCUPIED row is a valid drop target, measured 2026-09-01.** This used
    to refuse whenever the roster filled all `ROSTER_ROWS_VISIBLE` rows, on
    the theory that dropping onto an occupant is a reorder rather than an
    unseat. It is not: with 8 heroes and the Vestal seated at rank 1, dragging
    her portrait onto row 7 emptied the party bar and left
    `persist.roster.json` in exactly the same order. That matters because a
    roster of 8 is the ordinary post-week-skip state, so the old refusal would
    have blocked every clear from the second week on.
    """
    n = len(town.heroes)
    return n if n < ROSTER_ROWS_VISIBLE else ROSTER_ROWS_VISIBLE - 1


# -- the roster list is SCROLLED, and the save does not say so ------------
#
# **This was a live bug, not a hypothetical one.** `town_roster_list_layout`
# draws `element_max` = 8 rows and the campaign's roster is 12, so the list
# arrives scrolled -- measured at **offset 4** on the estate map on
# 2026-09-02, before anything had touched it. Every caller here was passing a
# SAVE index straight into `roster_portrait`, so `seat_hero(0)` would have
# grabbed `town.heroes[4]`. It would have been "confirmed" too: the party bar
# changes when the wrong hero lands in it exactly as it does for the right one.
#
# It had been harmless only by accident. `week_skip` and `recruit_all` were
# written when the roster held eight or fewer, where no scroll is possible.
#
# **Nothing in the save carries the offset.** `persist.roster.json` holds no
# name, no sort key and no scroll state -- 33 fields per hero and none of them
# ordering. What it does carry is the class list in list order, and the game
# draws its own portrait art, so the offset is recoverable from PIXELS: match
# the eight drawn portraits against the class sequence at every candidate
# offset and take the best alignment.
#
# Eight rows over-determine one integer, which is what makes this robust
# despite a single portrait matching poorly under the game's colour grading.
# Measured on the live estate map, walking a 12-hero list 4 -> 3 -> 2 -> 1 -> 0
# with one click each: the winning offset beat the runner-up by **5.6 to 6.9**
# mean levels every time, and the correct answer won at every step.

# How far ahead of the runner-up the best alignment has to be. Measured
# margins were 5.6-6.9; this is a third of the smallest, because the cost of
# being wrong is dragging the wrong hero and the cost of refusing is a note.
ROSTER_MATCH_MARGIN = 2.0

# Half-width of the portrait patch compared, in FRAME pixels at 1280x720. A
# roster portrait is about 48 across there, so this is most of it without the
# row's frame furniture.
ROSTER_PATCH = 22

# A scroll click animates the list; a re-grab during that reads the old rows.
ROSTER_SCROLL_SETTLE = 0.9


@lru_cache(maxsize=2)
def _roster_portrait_refs(game_dir: str) -> dict:
    """`{class: (art, ...)}` from the game's own roster portraits.

    Every class ships one per variant (`<class>_A..D`), and all of them are
    kept: a hero's variant is not in the save, so the match takes the best of
    the four. DLC classes live under `dlc/**` and are globbed the same way.
    """
    import numpy as np                                # noqa: PLC0415
    from PIL import Image                             # noqa: PLC0415

    root = Path(game_dir)
    size = 2 * ROSTER_PATCH
    out: dict = {}
    patterns = ("heroes/*/*_?/*_portrait_roster.png",
                "dlc/**/heroes/*/*_?/*_portrait_roster.png")
    for pattern in patterns:
        for path in root.glob(pattern):
            hero_class = path.parts[-2].rsplit("_", 1)[0]
            try:
                art = Image.open(path).convert("RGB").resize((size, size))
            except OSError:
                continue
            out.setdefault(hero_class, []).append(np.asarray(art, dtype=float))
    return {k: tuple(v) for k, v in out.items()}


def _roster_offset(win, town, game_dir=None):
    """Which save row is drawn in the list's TOP row, or None if unreadable.

    Refusing is the point. A wrong offset does not miss -- it drags a
    different hero and the party bar confirms it happily -- so an alignment
    that does not beat its runner-up by `ROSTER_MATCH_MARGIN` reports nothing
    and the caller declines to seat rather than guessing.
    """
    import numpy as np                                # noqa: PLC0415
    from bridge import screen                         # noqa: PLC0415
    from bridge import townlayout as TL               # noqa: PLC0415

    if game_dir is None:
        from bridge.townplan import GAME_DIR          # noqa: PLC0415
        game_dir = GAME_DIR
    classes = [h.hero_class for h in town.heroes]
    if len(classes) <= ROSTER_ROWS_VISIBLE:
        return 0                                      # nothing can scroll
    refs = _roster_portrait_refs(str(game_dir))
    img = screen.grab(win)
    if img is None or not refs:
        return None
    img = np.asarray(img, dtype=float)
    height, width = img.shape[0], img.shape[1]
    side = 2 * ROSTER_PATCH

    def cost(row: int, hero_class: str) -> float:
        point = TL.roster_portrait(row)
        arts = refs.get(hero_class)
        if point is None or not arts:
            return 999.0
        cx = int(round(point[0] * width))
        cy = int(round(point[1] * height))
        got = img[cy - ROSTER_PATCH:cy + ROSTER_PATCH,
                  cx - ROSTER_PATCH:cx + ROSTER_PATCH, :3]
        if got.shape != (side, side, 3):
            return 999.0
        return min(float(np.abs(got - a).mean()) for a in arts)

    scored = sorted(
        (sum(cost(r, classes[off + r]) for r in range(ROSTER_ROWS_VISIBLE))
         / ROSTER_ROWS_VISIBLE, off)
        for off in range(len(classes) - ROSTER_ROWS_VISIBLE + 1))
    if len(scored) > 1 and scored[1][0] - scored[0][0] < ROSTER_MATCH_MARGIN:
        return None
    return scored[0][1]


def scroll_roster_to(win, backend, resolved, town, want: int, *, say=print,
                     act: bool = False) -> "Did":
    """Scroll until the list's top row is save row `want`. Confirms each step.

    Bounded by the roster size, and it stops the moment a click fails to move
    the offset -- the chevron is only DRAWN when it can act, so a click at the
    end of the list is a no-op rather than a misclick, and looping on one
    would be the real bug.
    """
    from bridge import act as act_mod                 # noqa: PLC0415

    label = f"scroll the roster to row {want}"
    at = _roster_offset(win, town)
    if at is None:
        return Did(label, why="cannot read the list offset off the portraits")
    if at == want:
        return Did(label, sent=False, confirmed=True, why="already there")
    if not act:
        say(f"  [town] WOULD scroll the roster {at} -> {want}")
        return Did(label, why="not armed")

    for _ in range(len(town.heroes) + 1):
        if at == want:
            return Did(label, sent=True, confirmed=True)
        plan = act_mod.plan_scroll_roster(at < want, resolved)
        if not plan.actionable:
            return Did(label, why=plan.refusal)
        _send(backend, plan, act=True, say=say)
        time.sleep(ROSTER_SCROLL_SETTLE)
        now = _roster_offset(win, town)
        if now is None:
            return Did(label, sent=True, confirmed=False,
                       why="lost the list offset mid-scroll")
        if now == at:
            return Did(label, sent=True, confirmed=False,
                       why=f"stuck at offset {at}; the chevron is not drawn "
                           f"when it cannot act")
        at = now
    return Did(label, sent=True, confirmed=False, why=f"stopped at offset {at}")


def _roster_strip(win, resolved):
    """The roster list as a small float image, or None.

    Cropped with `_img_pt` for the same reason `_party_bar` is: this reads a
    GRABBED FRAME and `resolved.at` answers in desktop pixels.
    """
    import numpy as np                               # noqa: PLC0415
    from bridge import screen                        # noqa: PLC0415
    from bridge import townlayout as TL              # noqa: PLC0415

    first = TL.roster_portrait(0)
    last = TL.roster_portrait(ROSTER_ROWS_VISIBLE - 1)
    if first is None or last is None:
        return None
    img = screen.grab(win)
    if img is None:
        return None
    a = _img_pt(img, (first[0] - 0.02, first[1] - 0.03))
    b = _img_pt(img, (last[0] + 0.06, last[1] + 0.03))
    if a is None or b is None:
        return None
    x0, y0 = max(0, int(a[0])), max(0, int(a[1]))
    x1, y1 = min(img.shape[1], int(b[0])), min(img.shape[0], int(b[1]))
    if x1 - x0 < 8 or y1 - y0 < 8:
        return None
    return np.asarray(img[y0:y1, x0:x1], dtype=float)


def force_roster_to_top(win, backend, resolved, town, *, say=print,
                        act: bool = False) -> "Did":
    """Click UP until the list stops moving, so the offset is 0 by force.

    **`_roster_offset` cannot always be read, and that is not a bug in it.**
    It matches the visible portraits against the class art and refuses a
    near-tie; a roster with duplicated classes produces exactly that. Measured
    on a 21-hero campaign carrying two each of bounty hunter, crusader,
    duelist, highwayman, man-at-arms, plague doctor, runaway and vestal --
    eight ambiguous pairs, and every seat came back "cannot read the list
    offset off the portraits". Loosening the margin there would not answer the
    question, it would guess at it, and a wrong offset does not miss: it seats
    a different hero.

    So this stops asking where the list is and MOVES it somewhere known. The
    same trick the save list needed: an extreme is the one position that can
    be reached without knowing the current one.

    Over-clicking is safe by the list's own design -- the chevron is only
    drawn when it can act, so a click at the end is a no-op rather than a
    misclick (`scroll_roster_to` relies on the same fact). The stop condition
    is therefore the STRIP not changing, which is what "already at the top"
    looks like, and the bound is the roster size.
    """
    from bridge import act as act_mod                 # noqa: PLC0415

    label = "scroll the roster to the top"
    if not act:
        say("  [town] WOULD scroll the roster to the top")
        return Did(label, why="not armed")
    plan = act_mod.plan_scroll_roster(False, resolved)      # False == up
    if not plan.actionable:
        return Did(label, why=plan.refusal)
    before = _roster_strip(win, resolved)
    if before is None:
        return Did(label, why="cannot see the roster list")
    for _ in range(len(town.heroes) + 2):
        _send(backend, plan, act=True, say=say)
        time.sleep(ROSTER_SCROLL_SETTLE)
        after = _roster_strip(win, resolved)
        if after is None:
            return Did(label, sent=True, confirmed=False,
                       why="lost sight of the roster list mid-scroll")
        if not _bar_moved(before, after):
            return Did(label, sent=True, confirmed=True, why="at the top")
        before = after
    return Did(label, sent=True, confirmed=False,
               why="the list never stopped moving")


def _bring_into_view(win, backend, resolved, town, index: int, *, say=print,
                     act: bool = False):
    """Scroll the least that puts save row `index` on screen. Returns
    `(offset, Did or None)` -- the offset is None when it could not be read."""
    at = _roster_offset(win, town)
    if at is None:
        # The portraits could not say where the list is -- duplicated classes
        # make that the ORDINARY case on a full roster. Put it somewhere the
        # answer is known instead of guessing.
        got = force_roster_to_top(win, backend, resolved, town, say=say,
                                  act=act)
        if not got.confirmed:
            return None, got
        at = 0
    if at <= index < at + ROSTER_ROWS_VISIBLE:
        return at, None
    want = index if index < at else index - ROSTER_ROWS_VISIBLE + 1
    want = max(0, min(want, len(town.heroes) - ROSTER_ROWS_VISIBLE))
    got = scroll_roster_to(win, backend, resolved, town, want, say=say, act=act)
    return _roster_offset(win, town), got



def _drag(backend, resolved, src, dst, *, act: bool, say) -> bool:
    """One drag between two design-space points. Sends nothing without `act`."""
    a, b = resolved.at(src), resolved.at(dst)
    if a is None or b is None:
        return False
    say(f"  [town] drag {a} -> {b}")
    if not act:
        return False
    ok = backend.drag(a, b)
    time.sleep(DRAG_SETTLE)
    _park_cursor(backend, resolved)
    return ok


def clear_party(win, backend, resolved, town, *, say=print,
                act: bool = False) -> list:
    """Drag every party slot back onto the roster.

    All four are attempted even when only two can be occupied: Fran asked for
    it as a general "clean team" step, and a drag from an empty slot grabs
    nothing -- which reports as an unconfirmed drag rather than as an error.
    """
    from bridge import townlayout as TL              # noqa: PLC0415
    row = _park_row(town)
    if row is None:
        return [Did("clear the party bar",
                    why=f"no empty roster row: {len(town.heroes)} heroes fill "
                        f"all {ROSTER_ROWS_VISIBLE} visible rows")]
    park = TL.roster_portrait(row)
    if park is None:
        return [Did("clear the party bar", why=f"no roster row {row}")]

    done = []
    for index in range(PARTY_RANKS):
        src = TL.party_portrait(index)
        if src is None:
            done.append(Did(f"clear party slot {index}", why="no such slot"))
            continue
        before = _party_bar(win, resolved)
        sent = _drag(backend, resolved, src, park, act=act, say=say)
        moved = _bar_moved(before, _party_bar(win, resolved)) if sent else False
        done.append(Did(f"clear party slot {index}", sent=sent, confirmed=moved,
                        why="" if moved else "the bar did not change "
                                             "(an empty slot looks like this)"))
    return done


def seat_hero(win, backend, resolved, roster_row: int, rank: int = 1, *,
              say=print, act: bool = False) -> "Did":
    """Drag one roster row into a combat RANK of the party bar.

    **`roster_row` is a SCREEN row, 0-7, not an index into `town.heroes`.**
    The list shows 8 of up to 28 and arrives scrolled, so the two differ by
    `_roster_offset` -- which is measured off the drawn portraits, because the
    save carries no ordering. Passing a save index here seats a different
    hero and the party bar confirms it happily.

    `rank` is the combat rank, not a slot index: rank 1 is the hero nearest
    the enemies and DD draws it RIGHTMOST, so `party_slot_for_rank` is the
    only accessor an embark may use. See its note in `bridge/townlayout.py`.
    """
    from bridge import townlayout as TL              # noqa: PLC0415
    src = TL.roster_portrait(roster_row)
    dst = TL.party_slot_for_rank(rank)
    if src is None or dst is None:
        return Did(f"seat roster row {roster_row} at rank {rank}",
                   why="no such row or rank")
    before = _party_bar(win, resolved)
    sent = _drag(backend, resolved, src, dst, act=act, say=say)
    moved = _bar_moved(before, _party_bar(win, resolved)) if sent else False
    return Did(f"seat roster row {roster_row} at rank {rank}", sent=sent,
               confirmed=moved, why="" if moved else "the bar did not change")


def pick_quest(win, backend, resolved, region: str = WEEK_SKIP_REGION, *,
               quest=None, say=print, act: bool = False) -> list:
    """Select a quest on the estate map.

    **Two ways in, and the second one supersedes the first.** `region` is a
    key into the hand-measured `REGION_SCENE_POINTS`, which holds exactly one
    entry (the Darkest Dungeon, for the week skip) and clicks a banner and
    then an emblem -- two clicks, because the banner only NAMES the region and
    clicking it alone left the previous quest selected.

    `quest` is a `town_state.Quest`, and its token is **shipped geometry**:
    `quest_select.layout.darkest` gives every dungeon a `quest_map_pos` and
    the offset to its first quest button, and the derivation lands within half
    a design pixel of three independently measured tokens out of two different
    layout files. One click, no measurement, every dungeon. See
    `townlayout.QUEST_BUTTON_OFFSET`.
    """
    from bridge import townlayout as TL              # noqa: PLC0415

    if quest is not None:
        label = f"select the {quest.dungeon} quest"
        pt = TL.quest_point(quest)
        if pt is None:
            return [Did(label, why=f"no derived token for {quest.dungeon!r} "
                                   f"(a second quest in one region refuses -- "
                                   f"see QUEST_ROW_MEASURED)")]
        at = resolved.at(pt)
        say(f"  [town] {label} at {at}")
        if act and at is not None:
            backend.click(at)
            time.sleep(STEP_SETTLE)
        return [Did(label, sent=act, confirmed=act)]

    done = []
    for which in ("banner", "emblem"):
        pt = TL.region_point(region, which)
        if pt is None:
            done.append(Did(f"click the {region} {which}",
                            why="that region was never measured"))
            continue
        at = resolved.at(pt)
        say(f"  [town] click the {region} {which} at {at}")
        if act and at is not None:
            backend.click(at)
            time.sleep(STEP_SETTLE)
        done.append(Did(f"click the {region} {which}", sent=act, confirmed=act))
    return done


def abandon_quest(win, backend, resolved, *, say=print,
                  act: bool = False) -> list:
    """Leave the dungeon from the raid HUD, answering whatever DD asks.

    The banner only ASKS -- DD raises "Are you sure you want to retreat?" --
    which is what makes it safe to click at all, unlike its neighbour in the
    pause menu. The confirmations are bounded and each one first checks that
    a dialog is actually up, so a mis-recognition cannot become a stream of
    clicks at `MODAL_OK`.
    """
    done = []
    at = resolved.raid_abandon_quest
    say(f"  [town] click the abandon-quest banner at {at}")
    if at is None:
        return [Did("abandon the quest", why="no abandon point")]
    if act:
        backend.click(at)
        time.sleep(ABANDON_SETTLE)
    done.append(Did("abandon the quest", sent=act, confirmed=act))

    for i in range(ABANDON_CONFIRMS):
        if not act or not _modal_family(win):
            break
        ok = resolved.menu_point("modal_ok")
        say(f"  [town] confirm {i + 1} at {ok}")
        if ok is None:
            done.append(Did(f"confirm the abandon ({i + 1})",
                            why="no modal_ok point"))
            break
        backend.click(ok)
        time.sleep(ABANDON_SETTLE)
        done.append(Did(f"confirm the abandon ({i + 1})", sent=True,
                        confirmed=not _modal_family(win)))
    return done


def _burnable_row(town, hero_class: str = "vestal") -> Optional[int]:
    """Which roster row to send in alone, or None if nobody is free.

    **Fran named the Vestal, and the game is why that is a reliable name**:
    `stage_coach.building.json` ships `first_hero_classes: [plague_doctor,
    vestal]`, so the first coach roll of every campaign offers one. She is
    therefore the default rather than a special case.

    The fallback matters anyway, because whether she is HIRED is the town
    planner's call and it recruits toward comp shapes. Then the pick is the
    hero carrying the least investment -- lowest resolve XP, ties to the most
    stressed -- because the procedure ends by dismissing whoever went, and the
    cheapest hero to dismiss is the newest one. It never picks a hero who is
    already in a building.
    """
    free = [(i, h) for i, h in enumerate(town.heroes) if h.available]
    if not free:
        return None
    named = [i for i, h in free if h.hero_class == hero_class]
    if named:
        return named[0]
    return min(free, key=lambda ih: (ih[1].hero.resolve_xp
                                     if hasattr(ih[1].hero, "resolve_xp")
                                     else 0, -ih[1].stress))[0]


def week_skip(win, backend, profile, *, hero_class: str = "vestal",
              roster_row: Optional[int] = None, say=print,
              act: bool = False) -> list:
    """One post-tutorial week: embark one hero, abandon, come home.

    Starts and ends in the Hamlet. Returns a list of `Did` -- it never raises,
    for the same reason `visit_stage_coach` does not: a week skip that
    half-worked is a fact to report, and the caller has a campaign to get on
    with either way.
    """
    from bridge.layout import load as load_layout    # noqa: PLC0415
    from bridge.town_state import read_town_state    # noqa: PLC0415

    done: list = []
    fractional = load_layout()
    if fractional is None:
        return [Did("week skip", why="no overlay_layout.json")]
    resolved = fractional.resolve(win)

    try:
        town = read_town_state(profile)
    except Exception as exc:                         # noqa: BLE001
        return [Did("week skip", why=f"{type(exc).__name__}: {exc}")]

    if roster_row is None:
        roster_row = _burnable_row(town, hero_class)
        if roster_row is None:
            return [Did("week skip",
                        why=f"nobody free in a roster of {len(town.heroes)}")]
    say(f"  [town] week skip: roster row {roster_row} "
        f"({town.heroes[roster_row].hero_class}) alone into "
        f"the {WEEK_SKIP_REGION}")

    here = _look(win)
    if here not in TOWN_SCREENS:
        return [Did("week skip",
                    why=f"not in the Hamlet (screen: {here or 'UNKNOWN'})")]
    # 1. Hamlet -> the estate map, where the bar and the roster are drawn.
    got = walk(win, backend, until=lambda s: s == "estate_map", say=say,
               act=act, buy=False)
    done.append(Did("reach the estate map", sent=act, confirmed=got.ok,
                    why="" if got.ok else got.why or got.stopped_on))
    if not got.ok:
        return done

    # 2. **The quest FIRST, and that is the game's ordering rather than
    # Fran's.** His procedure reads embark -> clear -> seat -> pick, which
    # works at the keyboard because the previous week's quest is still
    # selected when you arrive. From a fresh estate map nothing is selected,
    # the party bar is inert, and every drag into it is silently ignored --
    # measured on 2026-09-01: five drags sent, bar unchanged, and the same
    # drag succeeded the moment a quest was lit.
    done += pick_quest(win, backend, resolved, say=say, act=act)

    # 3-4. one hero, alone, at rank 1.
    done += clear_party(win, backend, resolved, town, say=say, act=act)
    # `_burnable_row` returns a SAVE index. That was the same thing as a
    # screen row only while the roster fit in the list's eight rows, which is
    # how this went unnoticed until the roster reached 12.
    offset, scrolled = _bring_into_view(win, backend, resolved, town,
                                        roster_row, say=say, act=act)
    if scrolled is not None:
        done.append(scrolled)
    if offset is None or not offset <= roster_row < offset + ROSTER_ROWS_VISIBLE:
        done.append(Did(f"seat roster index {roster_row}",
                        why=f"not on screen at offset {offset}"))
        return done
    done.append(seat_hero(win, backend, resolved, roster_row - offset, 1,
                          say=say, act=act))

    # 5. the walk in -- buying nothing.
    got = to_raid(win, backend, say=say, act=act, provisions=False)
    done.append(Did("embark", sent=act, confirmed=got.ok,
                    why="" if got.ok else got.why or got.stopped_on))
    if not got.ok:
        return done

    # 6. straight back out, before a hallway.
    done += abandon_quest(win, backend, resolved, say=say, act=act)
    got = to_town(win, backend, say=say, act=act)
    done.append(Did("walk home", sent=act, confirmed=got.ok,
                    why="" if got.ok else got.why or got.stopped_on))
    return done


# -- the embark chain -----------------------------------------------------
#
# Fran's order, 2026-09-01, and it is his order rather than the obvious one:
#
#     EMBARK, scout missions and place comp, go back to hamlet into
#     guild/blacksmith, upgrade comp, set attacks for the comp and go back to
#     embark and start the mission
#
# The comp is placed BEFORE the guild is visited because which four heroes are
# going is what decides whose skills are worth buying -- PLAYBOOK 4.4:
# *"Primero se decide quien va a la mision, y se maximiza a ese cuarteto si el
# dinero llega."* Never the whole roster.
#
# `bridge/embarkplan.py` decides all of it offline; this walks the screens.

def embark_setup(win, backend, profile, *, store=None, plan=None, say=print,
                 act: bool = False, seed: Optional[int] = None,
                 embark: bool = True, min_party: Optional[int] = None) -> list:
    """Quest, comp, formation, guild, provisions -- in Fran's order.

    Starts in the Hamlet and ends in a raid. Returns a list of `Did` and never
    raises: a half-done embark is a fact to report, the same contract
    `visit_stage_coach` and `week_skip` keep.
    """
    import random                                    # noqa: PLC0415
    from bridge import embarkplan                    # noqa: PLC0415
    from bridge.layout import load as load_layout    # noqa: PLC0415
    from bridge.town_state import read_town_state    # noqa: PLC0415

    done: list = []
    fractional = load_layout()
    if fractional is None:
        return [Did("embark setup", why="no overlay_layout.json")]
    resolved = fractional.resolve(win)

    here = _look(win)
    if here not in TOWN_SCREENS:
        return [Did("embark setup",
                    why=f"not in the Hamlet (screen: {here or 'UNKNOWN'})")]

    # 1. Hamlet -> the estate map, which is where the quest map, the party bar
    #    and the roster list are all drawn.
    got = walk(win, backend, until=lambda s: s == "estate_map", say=say,
               act=act, buy=False)
    done.append(Did("reach the estate map", sent=act, confirmed=got.ok,
                    why="" if got.ok else got.why or got.stopped_on))
    if not got.ok:
        return done

    # 2. Scout. The quests are in `persist.quest.json`, so this is a save read
    #    and nothing is clicked for it.
    if plan is None:
        if store is None:
            from data.store_cache import load_store_cached   # noqa: PLC0415
            from bridge.townplan import GAME_DIR             # noqa: PLC0415
            store = load_store_cached(
                str(Path(__file__).resolve().parent.parent), str(GAME_DIR))
        try:
            town = read_town_state(profile)
        except Exception as exc:                     # noqa: BLE001
            return done + [Did("scout the quests",
                               why=f"{type(exc).__name__}: {exc}")]
        plan = embarkplan.plan_embark(town, store, profile,
                                      random.Random(seed))
    if plan is None:
        return done + [Did("scout the quests",
                           why="no simulable quest on the map")]
    for line in plan.report().splitlines():
        say(f"  [town] {line}")
    done.append(Did(f"scout: {plan.quest.label()} with {plan.comp}",
                    sent=False, confirmed=True, why="read from the save"))

    # **FOUR HEROES OR NOBODY GOES.** Fran: *"we should make sure we are
    # sending 4 people comps during embark"*.
    #
    # This loop sent a two-hero party into an eight-room Crypts on
    # 2026-09-05 and it wiped, twice. The plan said so up front -- "only 2
    # heroes are free and under the resolve cap", printed three lines above
    # the drags -- and nothing read it. A short party is not a slightly worse
    # party: every curated comp is written for four, and the rank rules that
    # decide which skills are legal are about four ranks.
    #
    # Refusing HERE rather than after the drags is deliberate. Seating and
    # provisioning are the reversible half; the moment `to_raid` runs, gold is
    # spent and a raid is running. And it cannot deadlock a night: the coach
    # visit and the week skip both come BEFORE this in the loop, so a roster
    # that is short today is one they get a week to fix.
    want = embarkplan.PARTY_SIZE if min_party is None else int(min_party)
    if len(plan.party) < want:
        return done + [Did("embark",
                           why=f"only {len(plan.party)} hero(es) could be "
                               f"seated and a party is {want} - hire at the "
                               f"coach or skip the week first")]

    try:
        town = read_town_state(profile)
    except Exception as exc:                         # noqa: BLE001
        return done + [Did("read the roster", why=f"{type(exc).__name__}: {exc}")]

    # 3. **The quest FIRST, then the party.** From a fresh estate map nothing
    #    is selected, the party bar is inert and every drag into it is
    #    silently ignored -- measured on 2026-09-01, five drags sent and the
    #    bar unchanged, with the same drag working the moment a quest was lit.
    done += pick_quest(win, backend, resolved, quest=plan.quest,
                       say=say, act=act)
    done += clear_party(win, backend, resolved, town, say=say, act=act)
    # The known-empty baseline for the per-slot count below. Taken here rather
    # than derived from a threshold: `clear_party` has just emptied the bar,
    # so this IS what empty looks like on this screen at this resolution.
    empty_slots = _party_slots(win, resolved) if act else None

    # **The roster INDEX is not the roster ROW.** The list draws 8 of 12 and
    # arrives scrolled -- measured at offset 4 on this campaign before
    # anything touched it -- so every seat is scrolled into view and then
    # converted through the measured offset. A wrong conversion does not miss;
    # it seats a different hero and the bar confirms it.
    # **FOUR HEROES IN FOUR DIFFERENT RANKS.** Today they always are:
    # `embarkplan` builds every seat list with `rank=index + 1` and the two
    # repair passes carry `seat.rank` through unchanged, so this cannot fire.
    # It is here because of what breaking it would LOOK like -- four confirmed
    # drags into three ranks is a three-hero party that every count below
    # calls four, since the drags land, the bar changes each time, and the
    # third hero is simply replaced. An invariant whose violation is silent
    # and whose cost is a raid is worth one line at the seam.
    #
    # Before anything is sent: a party half-seated into the wrong shape is
    # worse than one not seated at all.
    ranks = [seat.rank for seat in plan.party]
    if act and len(set(ranks)) != len(ranks):
        return done + [Did("embark",
                           why=f"the plan seats two heroes in one rank "
                               f"({sorted(ranks)}) - refusing before any drag")]

    index_of = {h.guid: i for i, h in enumerate(town.heroes)}
    # **Seated in ROSTER order, not rank order.** Which rank a hero lands in
    # is decided by the slot the drag ENDS on, so the order of the drags is
    # free -- and taking them in list order means the list is walked once
    # instead of scrolled back and forth. Four heroes at save rows 0, 2, 6 and
    # 11 cost one scroll this way and up to three the other.
    # **THE SEATS ARE COLLECTED, NOT SLICED OFF THE TAIL.** See the count
    # below: a scroll appends to `done` too, so `done[-4:]` is not four seats.
    seats: list = []
    for seat in sorted(plan.party, key=lambda s: index_of.get(s.hero.guid, 99)):
        label = f"seat {seat.hero.label()} at rank {seat.rank}"
        index = index_of.get(seat.hero.guid)
        if index is None:
            seats.append(Did(label, why="that hero is not in the roster any more"))
            done.append(seats[-1])
            continue
        offset, scrolled = _bring_into_view(win, backend, resolved, town, index,
                                            say=say, act=act)
        if scrolled is not None:
            done.append(scrolled)
        if offset is None:
            seats.append(Did(label, why="cannot read the list offset off the "
                                        "portraits, so the row is not known"))
            done.append(seats[-1])
            continue
        if not offset <= index < offset + ROSTER_ROWS_VISIBLE:
            seats.append(Did(label, why=f"save row {index} is not on screen at "
                                        f"offset {offset}"))
            done.append(seats[-1])
            continue
        seats.append(seat_hero(win, backend, resolved, index - offset,
                               seat.rank, say=say, act=act))
        done.append(seats[-1])

    # **AND FOUR MUST ACTUALLY BE ON THE BAR.** The plan having four says the
    # roster can field them; it does not say the drags landed. A seat can be
    # skipped for a hero that left the roster, an offset the portraits could
    # not give, or a row that never scrolled into view -- all three report and
    # continue, which is right for a report and wrong for a party.
    #
    # Counted on `confirmed`, which is the party bar itself, so this is the
    # game's answer and not the bridge's. Only when acting: a dry run confirms
    # nothing by construction, and refusing there would make `--no-act` unable
    # to print a plan.
    #
    # **COUNTED OVER `seats`, BECAUSE A TAIL SLICE IS NOT FOUR SEATS.** This
    # read `done[-len(plan.party):]`, and `_bring_into_view` appends a scroll
    # of its own whenever the list has to move -- so a party needing one
    # scroll pushed the first seat out of the window and the guard read 3 of
    # 4. It then refused the embark. Four consecutive iterations of
    # 2026-09-06 died that way with every single seat reporting OK on the
    # line above, which is what makes a slice the wrong instrument: the
    # evidence was right there and the count disagreed with it.
    #
    # The `startswith("seat ")` filter was the tell. A count that has to
    # re-identify its own entries by their prose is a count that does not
    # know what it is counting.
    seated = sum(1 for d in seats if d.confirmed)
    # **AND THE BAR ITSELF OUTRANKS THE DRAG COUNT.** A confirmed drag means
    # the strip changed, which a hero MOVING between ranks also does; counting
    # slots that gained something against their own cleared baseline cannot be
    # fooled that way. Used only when both reads succeeded -- not being able
    # to see is not evidence, so it falls back to the drags rather than
    # refusing an embark it merely could not photograph.
    filled = _slots_filled(empty_slots, _party_slots(win, resolved))
    if filled is not None and filled != seated:
        say(f"  [town] the drags say {seated} seated and the bar shows "
            f"{filled} occupied - trusting the bar")
    if filled is not None:
        seated = filled
    if act and seated < want:
        return done + [Did("embark",
                           why=f"the party bar took {seated} of {want} heroes "
                               f"- not embarking short-handed")]

    # 4-6. The Guild, and the Blacksmith after it.
    done += _visit_guild(win, backend, resolved, profile, plan, say=say, act=act)

    # 7. Back to the raid, buying the basket on the way through.
    if not embark:
        # The seating and the guild are the reversible half; provisioning
        # spends gold and embarking starts a raid. `--no-embark` stops here so
        # the half that can be checked can be checked on its own.
        done.append(Did("embark", why="--no-embark: stopped after the party"))
        return done
    got = to_raid(win, backend, say=say, act=act, basket=plan.basket)
    done.append(Did("embark", sent=act, confirmed=got.ok,
                    why="" if got.ok else got.why or got.stopped_on))
    return done


def _visit_guild(win, backend, resolved, profile, plan, *, say=print,
                 act: bool = False) -> list:
    """Buy the comp's missing skills and equip its four. Refuses today.

    Two things are missing and each one is a single live visit away:

    * **`BUILDING_SCENE_POINTS["guild"]`.** A building is entered by clicking
      it in the Hamlet scene, and the scene's x is derivable from
      `town.layout.darkest` while its y is NOT -- the scene is a perspective
      projection with a per-building z. So every building but the stage coach
      is refused rather than guessed at.
    * **A banked frame of the guild panel.** The stage coach is why: its
      derived tree points landed in the panel's left margin until a live frame
      moved them, and no tree was drawn at all until the building crest was
      clicked. `townlayout.GUILD_MEASURED` is that gate.

    It reports what it WOULD buy either way, because that half is decided and
    correct -- and a plan that names the purchases is what makes the first
    live visit worth making.
    """
    from bridge import act as act_mod                # noqa: PLC0415
    from bridge import townlayout as TL              # noqa: PLC0415

    unlocks = plan.unlocks
    if not unlocks:
        return [Did("visit the guild", why="nothing to unlock for this comp")]

    done: list = []
    for hero, skill in unlocks:
        say(f"  [town] WOULD unlock {hero.hero_class}.{skill} (1000g)")
    if TL.building_scene_point("guild") is None:
        done.append(Did(f"unlock {len(unlocks)} skill(s)",
                        why="the guild's scene point has never been measured; "
                            "a building is entered by clicking it in the "
                            "Hamlet and only the stage coach is measured"))
        return done

    opened = _send(backend, act_mod.plan_open_building("guild", resolved),
                   act=act, say=say)
    if opened:
        time.sleep(PANEL_OPEN_SETTLE)
        if not _panel_is_open(win):
            note = _bank_panel_frame(win)
            if not _on_town_screen(win):
                _send(backend, act_mod.plan_close_building(resolved),
                      act=act, say=say)
            return done + [Did("open the guild", sent=True, confirmed=False,
                               why=f"could not confirm the panel opened{note}")]
    done.append(Did("open the guild", sent=opened, confirmed=opened))
    if opened and not TL.GUILD_MEASURED:
        # Bank the frame that un-gates this, then leave nothing open.
        note = _bank_panel_frame(win)
        done.append(Did(f"unlock {len(unlocks)} skill(s)", sent=False,
                        confirmed=False,
                        why=f"GUILD_MEASURED is False{note}"))
        _send(backend, act_mod.plan_close_building(resolved), act=act, say=say)
        return done

    for hero, skill in unlocks:
        before = _wallet(profile)
        row = _guild_row(hero.hero_class, skill)
        if row is None:
            done.append(Did(f"unlock {hero.hero_class}.{skill}",
                            why="that skill is not in the class's tree"))
            continue
        sent = _send(backend, act_mod.plan_buy_skill(row, resolved),
                     act=act, say=say)
        after = _await(lambda: _wallet(profile), before) if sent else before
        done.append(Did(f"unlock {hero.hero_class}.{skill}", sent=sent,
                        confirmed=sent and after != before,
                        why="" if after != before else "the wallet did not move"))
    _send(backend, act_mod.plan_close_building(resolved), act=act, say=say)
    return done


def _guild_row(hero_class: str, skill: str) -> Optional[int]:
    """Which row of the Guild panel a skill sits on.

    The panel draws one row per skill in the order the class's own
    `<class>.upgrades.json` declares them, which is the order
    `data/town_costs.py` reads out -- the same "the file's order is the
    panel's order" rule `_tree_index` rests on for the coach.
    """
    from data.town_costs import load_hero_costs      # noqa: PLC0415
    from bridge.townplan import GAME_DIR             # noqa: PLC0415

    costs = load_hero_costs(str(GAME_DIR), hero_class)
    if costs is None or skill not in costs.skills:
        return None
    return list(costs.skills).index(skill)


def recruit_all(win, backend, profile, *, say=print, act: bool = False,
                limit: int = 4) -> list:
    """Hire everyone the stage coach is offering, dragging each to the roster.

    Deliberately NOT `visit_stage_coach`: that one decides -- it trades,
    upgrades, and then hires toward a curated comp shape. This is Fran's
    week-skip step, which is "take all four", and the decision layer would
    refuse most of them.

    **`recruit_slot(0)` is grabbed every time.** The panel compacts after a
    hire, so the next recruit slides up into the first row; walking the index
    instead would drag from a row the list no longer draws.
    """
    from bridge import act as act_mod                # noqa: PLC0415
    from bridge import townlayout as TL              # noqa: PLC0415
    from bridge.layout import load as load_layout    # noqa: PLC0415

    done: list = []
    fractional = load_layout()
    if fractional is None:
        return [Did("recruit all", why="no overlay_layout.json")]
    resolved = fractional.resolve(win)

    # **A panel that is ALREADY open must not be opened again.** The scene
    # point is a building in the Hamlet, and with the coach up it is a
    # coordinate INSIDE the panel -- so re-sending it clicks whatever the
    # panel happens to draw there. The visit can legitimately start here: a
    # human left it open, or a previous step ended inside one.
    # **An exchange overlay left over from anywhere is shut FIRST.** Unlike a
    # building panel it does not replace the Hamlet, it sits on top of it -- so
    # the recogniser still reads a town screen, `_panel_is_open` is right that
    # no panel is up, and the building scene points are simply underneath it.
    # Seen live 2026-09-06, with the overlay left behind by an earlier visit:
    # the first click of the next visit landed on the leftover panel's header,
    # nothing opened, and the guard below correctly refused -- having spent a
    # blind click into a live game to find that out.
    close_exchange(win, backend, resolved, say=say, act=act)
    already = _panel_is_open(win)
    if already:
        say("  [town] the stage coach is already open")
    opened = already or _send(
        backend, act_mod.plan_open_building("stage_coach", resolved),
        act=act, say=say)
    if opened and not already:
        time.sleep(PANEL_OPEN_SETTLE)
        if not _panel_is_open(win):
            note = _bank_panel_frame(win)
            if not _on_town_screen(win):
                _send(backend, act_mod.plan_close_building(resolved),
                      act=act, say=say)
            return [Did("open the stage coach", sent=True, confirmed=False,
                        why=f"could not confirm the panel opened{note}")]
    # `sent` means input was sent, so an already-open panel is not a send --
    # a dry run that reported one would be claiming to have clicked.
    done.append(Did("open the stage coach", sent=opened and not already,
                    confirmed=opened,
                    why="it was already open" if already else ""))
    if not opened:
        return done

    row = len(_roster_guids(profile))
    for i in range(limit):
        src = TL.recruit_slot(0)
        dst = TL.roster_portrait(_drop_row(row))
        if src is None or dst is None:
            done.append(Did(f"hire {i + 1}",
                            why=f"no recruit slot 0 or roster row {row}"))
            break
        before = _roster_guids(profile)
        sent = _drag(backend, resolved, src, dst, act=act, say=say)
        after = _await(lambda: _roster_guids(profile), before) if sent else before
        grew = len(after) > len(before)
        done.append(Did(f"hire {i + 1}", sent=sent, confirmed=grew,
                        why="" if grew else "the roster did not grow"))
        if not grew:
            continue
        row += 1

    _send(backend, act_mod.plan_close_building(resolved), act=act, say=say)
    return done


def to_raid(win, backend, *, say=print, act: bool = False,
            provisions: bool = True, basket: Optional[dict] = None) -> Walked:
    """From the Hamlet into a provisioned raid.

    The provisioning is deliberately minimal and is NOT a decision layer:
    `bridge/embark.py` already ranks quest and party by simulation, and this
    module is the hands, not the head. What it buys is the two supplies a run
    cannot start without -- and the game states its own recommendation for one
    of them, warning below eight food for a short quest.
    """
    from bridge.live_state import find_profile, read_live_state  # noqa: PLC0415

    def in_a_raid(_screen: str) -> bool:
        try:
            profile = find_profile()
            state = read_live_state(
                raid_file=profile / "persist.raid.json",
                roster_file=profile / "persist.roster.json")
            # **In the raid save is not the same as in the dungeon.** DD
            # writes the raid state and THEN shows a "Press [SPACE] or
            # [CLICK] to Continue" plate over it. Returning on the save alone
            # handed the play phase a game that was still on that plate: the
            # bridge read `rooH` from the save, found no map or inventory
            # panel drawn, correctly refused to press TAB blind, and stalled
            # for 180 s. Caught by the watchdog's banked frame.
            #
            # **A MODAL IS NOT THE DUNGEON EITHER, and the save agrees with
            # it too early to help.** DD writes `persist.raid.json` when
            # Embark is CLICKED, then draws the "you haven't purchased much
            # food" warning over the provision screen -- so the save reads
            # `in_raid` while the walk still has a dialog to answer. The
            # screen is UNKNOWN there (a dialog has no reference of its own),
            # which passed both tests above and returned "we are in the raid"
            # at step 0, having clicked nothing. Same shape as the loading
            # plate: the save leads the screen, so the SCREEN decides.
            return (bool(state.in_raid) and _screen not in TOWN_SCREENS
                    and _screen not in PRESS and not _modal_family(win))
        except Exception:                                        # noqa: BLE001
            return False

    return walk(win, backend, until=in_a_raid, say=say, act=act,
                buy=provisions, basket=basket)


# **THE PROVISIONER'S SAVE DOES NOT MOVE, AND THE SHELF DOES.** Measured live
# on 2026-09-02: a whole 3550-gold basket went in, the HUD read 6,670 -> 3,120
# to the gold, every stock count fell by exactly what was bought -- and
# `persist.estate.json` still said 6,670, so all eight purchases reported
# UNCONFIRMED. That is the documented town save lag ("in town the save can lag
# a whole session, and the HUD is the truer channel"), and reporting eight
# working purchases as failures is worse than useless: the run below it treats
# them as not done.
#
# The channel that DOES move is the shop cell itself. Each cell draws its
# remaining stock as a number above the icon, so buying redraws the cell --
# and unlike the wallet, this is PER ITEM, which is a stronger claim than "some
# gold left". It is the same pixel-difference test `_bar_moved` runs on the
# party bar, for the same reason.
#
# The cell is the grid pitch, 80 x 170 design px, anchored on the slot corner
# -- so the crop takes in the count as well as the icon.
SHOP_CELL = (80.0, 170.0)


def _shop_cell(win, item: str, length: int = 1):
    """The provisioner's cell for one item, as a small float image, or None."""
    import numpy as np                               # noqa: PLC0415
    from bridge import screen                        # noqa: PLC0415
    from bridge import townlayout as TL              # noqa: PLC0415

    index = TL.shop_item_index(item, length)
    corner = TL.shop_slot(index) if index is not None else None
    img = screen.grab(win)
    if corner is None or img is None:
        return None
    a = _img_pt(img, corner)
    b = _img_pt(img, (corner[0] + SHOP_CELL[0] / 1920.0,
                      corner[1] + SHOP_CELL[1] / 1080.0))
    if a is None or b is None:
        return None
    x0, y0 = max(0, int(a[0])), max(0, int(a[1]))
    x1, y1 = min(img.shape[1], int(b[0])), min(img.shape[0], int(b[1]))
    if x1 - x0 < 8 or y1 - y0 < 8:
        return None
    return np.asarray(img[y0:y1, x0:x1], dtype=float)


def _buy_basket(win, backend, resolved, basket: Optional[dict] = None, *,
                say=print, act: bool = False) -> list:
    """Buy a whole basket ON the provisioner screen. See `data/provisions.py`.

    Called when the walk ARRIVES there rather than before it starts -- the
    first version ran once up front, which on a walk beginning in the Hamlet
    is always the wrong screen, so it silently bought nothing.

    **`basket=None` keeps the old floor**, which was never a provisioning
    decision: DD warns below eight food on a short quest, that warning is
    another modal to recognise and dismiss, and buying the eight it asks for
    removes the screen. A real basket comes from `bridge/embarkplan.py`, which
    reserves PLAYBOOK 1.5's recommended amounts before any gold is spent
    elsewhere.

    Every item goes through `townlayout.shop_item`, whose cell index is the
    SHIPPED store order -- so the food and torch cells it computes are the two
    `bridge/layout.py` constants a human clicked, and everything else is the
    same grid.

    **Acknowledged against the SHELF, not the wallet** -- see `SHOP_CELL`. The
    save does not move here at all, so a wallet check reports every working
    purchase as a failure. The stock count redraws per item, which is both a
    channel that exists and a stricter one.
    """
    from bridge import act as act_mod                # noqa: PLC0415

    want = dict(basket or {"food": FOOD_TO_BUY, "torch": TORCHES_TO_BUY})
    done: list = []
    for item, count in sorted(want.items()):
        plan = act_mod.plan_buy_provision(item, int(count), resolved)
        shelf = _shop_cell(win, item)
        sent = _send(backend, plan, act=act, say=say, resolved=resolved)
        moved = _bar_moved(shelf, _shop_cell(win, item)) if sent else False
        done.append(Did(f"buy {count} x {item}", sent=sent, confirmed=moved,
                        why="" if moved or not sent else
                            (plan.refusal or "the shop's stock count did not "
                                             "change")))
    return done


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--where", action="store_true")
    ap.add_argument("--to-town", action="store_true")
    ap.add_argument("--embark", action="store_true")
    ap.add_argument("--coach", action="store_true",
                    help="visit the stage coach: trade, upgrade, recruit. "
                         "Without --act it prints the plan and sends nothing.")
    ap.add_argument("--week-skip", action="store_true",
                    help="one post-tutorial week: embark one hero into the "
                         "Darkest Dungeon, abandon, come home")
    ap.add_argument("--recruit-all", action="store_true",
                    help="hire every hero the coach is offering")
    ap.add_argument("--embark-setup", action="store_true",
                    help="the whole embark chain: scout the quests, seat a "
                         "curated comp in its own formation, visit the guild, "
                         "provision and go. Without --act it prints every "
                         "click and sends nothing.")
    ap.add_argument("--seed", type=int, default=None,
                    help="pin the comp coin flip for --embark-setup")
    ap.add_argument("--no-embark", action="store_true",
                    help="--embark-setup: seat the party and stop, buying "
                         "nothing and starting no raid")
    ap.add_argument("--hero", default="vestal",
                    help="which class the week skip burns, when the roster "
                         "has one (default: vestal)")
    ap.add_argument("--profile", default=None,
                    help="the save directory; default is the most recently "
                         "played campaign")
    ap.add_argument("--no-store", action="store_true",
                    help="skip the DataStore, so recruiting falls back to "
                         "merit order instead of curated comp shapes")
    ap.add_argument("--act", action="store_true")
    ap.add_argument("--process", default="Darkest.exe")
    args = ap.parse_args(argv)

    from bridge.act import SendInputBackend, _force_foreground  # noqa: PLC0415
    from bridge.window import find_window                       # noqa: PLC0415
    win = find_window(args.process)
    if win is None:
        print("Darkest.exe is not running")
        return 1

    here = _look(win)
    if args.where or not (args.to_town or args.embark or args.coach
                          or args.week_skip or args.recruit_all
                          or args.embark_setup):
        what = ("confirm the dialog" if here in CONFIRM else
                "close the activity log" if here in CLOSE_PANEL else
                ADVANCE.get(here, "-- no move known --"))
        print(f"screen : {here or 'UNKNOWN'}")
        print(f"button : {what}")
        return 0

    _force_foreground(win.hwnd)
    time.sleep(1.0)
    win = find_window(args.process)
    backend = SendInputBackend(win)

    if args.coach or args.week_skip or args.recruit_all or args.embark_setup:
        # **The profile is RESOLVED, never assembled from `SAVE_ROOT`.** It
        # used to be `SAVE_ROOT / "profile_1"`, which is two directory levels
        # short of a real save path -- so it never existed, `None` was passed,
        # and `read_town_state` fell back to whichever campaign was touched
        # last. That is the documented town footgun, and it was live here.
        from bridge.live_state import find_profile              # noqa: PLC0415
        profile = find_profile(args.profile)
        print(f"profile: {profile}")

        if args.week_skip:
            done = week_skip(win, backend, profile, hero_class=args.hero,
                             act=args.act)
        elif args.embark_setup:
            store = None
            if not args.no_store:
                from bridge.townplan import GAME_DIR            # noqa: PLC0415
                from data.store_cache import load_store_cached  # noqa: PLC0415
                store = load_store_cached(
                    str(Path(__file__).resolve().parent.parent), str(GAME_DIR))
            done = embark_setup(win, backend, profile, store=store,
                                act=args.act, seed=args.seed,
                                embark=not args.no_embark)
        elif args.recruit_all:
            done = recruit_all(win, backend, profile, act=args.act)
        else:
            from bridge.townplan import GAME_DIR                # noqa: PLC0415
            store = None
            if not args.no_store:
                from data.store_cache import load_store_cached  # noqa: PLC0415
                store = load_store_cached(
                    str(Path(__file__).resolve().parent.parent), str(GAME_DIR))
            done = visit_stage_coach(win, backend, profile=profile,
                                     store=store, act=args.act)
        for d in done:
            print(f"  {d}")
        unconfirmed = [d for d in done if d.sent and not d.confirmed]
        # Non-zero when something was SENT and the save did not agree. A dry
        # run sends nothing, so it exits 0 and says what it would have done.
        return 1 if unconfirmed else 0

    got = (to_town(win, backend, act=args.act) if args.to_town
           else to_raid(win, backend, act=args.act))
    print(f"  -> {'ok' if got.ok else 'STOPPED'} after {got.steps} steps "
          f"on {got.stopped_on or 'UNKNOWN'}"
          f"{'  (' + got.why + ')' if got.why else ''}")
    print(f"     saw: {' > '.join(got.seen)}")
    return 0 if got.ok else 1


if __name__ == "__main__":
    sys.exit(main())
