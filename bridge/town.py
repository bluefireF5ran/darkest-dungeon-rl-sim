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
LEAVE_AT = {"butchers_circus": "circus_exit",
            # **THE WON RAID'S OWN BANNER, and the walk home ran into it for
            # the same reason it ran into `hunger`.** `act` presses this
            # inside the raid, but the banner is a modal shown once and it is
            # still up when the walk starts -- so `to_town` met a screen it
            # had no move for and ended three iterations in four seconds
            # each, on a quest the party had just COMPLETED (2026-09-21,
            # Fran's campaign). Homeward, the answer is never ambiguous:
            # "Return to Hamlet" is what this walk means. `Continue Raid`
            # stays `act`'s to choose, because only the raid layer knows
            # whether there is loot left worth the torch.
            "quest_complete": "quest_return_to_hamlet"}

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
# "guild_panel" banked 2026-09-25 from a live frame: the Guild opened and
# was refused as "could not confirm", because only the coach was banked.
BUILDING_SCREENS: tuple = ("stage_coach_panel", "guild_panel", "abbey_panel",
                           "blacksmith_panel")

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
    hunger_tries = 0
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
        elif here in CLOSE_PANEL or here.endswith("_panel"):
            # **EVERY BUILDING PANEL CLOSES BY THE SAME X.** DD draws one
            # close glyph for all of them -- `townlayout.CLOSE_GLYPH_CENTRE`
            # records that `activity_log_layout.close_pos` and
            # `building_base_layout.close_pos` are both (1496, 144) -- so the
            # walk does not need a reference per building to get out of one.
            #
            # Measured 2026-09-22: a run stopped mid-town-visit left the
            # stage coach open, and three iterations died in under ten
            # seconds each on "no move known for 'stage_coach_panel'". The
            # suffix is what makes the guild, abbey and tavern panels free
            # rather than three more entries to remember.
            point, what = resolved.town_close_panel, f"close {here}"
        elif here == "hunger":
            # **THE HUNGER CHECK FIRES IN THE DUNGEON AND THE WALK HOME RUNS
            # STRAIGHT INTO IT.** `act.ActRunner._answer_hunger` has answered
            # it since the panel was measured, but that is the RAID layer:
            # `to_town` has its own screen table and hunger was in none of it,
            # so the walk stopped dead with "no move known for 'hunger'".
            # Measured 2026-09-21 on Fran's own campaign -- three iterations
            # ended in four seconds each, having played 379 actions into the
            # raid before that.
            #
            # **Eat first, refuse second**, which is `_answer_hunger`'s order
            # and for its reason: eating is the answer whenever food is
            # available, and asking for it without food costs nothing. The
            # points are `layout`'s measured ones rather than a second
            # reading of the same panel.
            from bridge.layout import (HUNGER_EAT,          # noqa: PLC0415
                                       HUNGER_REFUSE)
            eating = hunger_tries == 0
            point = resolved.at(HUNGER_EAT if eating else HUNGER_REFUSE)
            what = "eat" if eating else "refuse (eating did not take)"
            hunger_tries += 1
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
    if here in BUILDING_SCREENS:
        return True
    # **Or its title plate**, which a speech bubble or a changed list cannot
    # hide (2026-09-23: the coachman's "Let's do" over the coach panel failed
    # the whole-frame match and the town visit was skipped). Still POSITIVE
    # evidence of a banked panel, never a guess from an unknown frame.
    return screen.panel_title_match(img) in BUILDING_SCREENS


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
                      act: bool = False,
                      roster_room: Optional[int] = None) -> list:
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
                             roster_room=roster_room, sanitarium_spends=False)
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
    # **The class-and-level cap first**, from the bare Hamlet where the
    # roster tab opens (Fran, 2026-09-26).
    if advice.dismissals:
        done += dismiss_surplus(win, backend, resolved, profile,
                                advice.dismissals, say=say, act=act)
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
    # **Only the COACH's own trees are bought here.** Since PLAYBOOK 7.4 the
    # plan also names guild, blacksmith and sanitarium ranks, and live
    # 2026-09-24 this loop clicked "buy guild.cost.a" and "buy
    # sanitarium.slots.a" at points INSIDE THE COACH PANEL -- whatever coach
    # control sits there. Each building needs its own panel measured before
    # its ranks may be bought; until then they are reported, not sent.
    coach_buys = [pu for pu in advice.purchases
                  if pu.node.tree_id.split(".")[0] == "stage_coach"]
    # The other buildings' ranks are bought AFTER the coach closes, each in
    # its own panel -- see `buy_building_upgrades`.
    other_buys = [pu for pu in advice.purchases if pu not in coach_buys]
    if coach_buys:
        _send(backend, act_mod.plan_open_upgrades(resolved), act=act, say=say)

    # 1. trades, then 2. the upgrades they pay for
    for purchase in coach_buys:
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
            # **STOP BUYING, BUT DO NOT ABANDON THE HIRES.** The purchases
            # after this one were costed against a wallet that no longer
            # exists, so none of them may be sent -- but a hire costs NOTHING
            # and is the most valuable thing in the visit.
            #
            # This used to return. The reason given was real: the panel is
            # closed for a trade (the exchange toggle sits on the bar
            # underneath it), so the hire drags went into a live Hamlet. The
            # answer to a closed panel is to open it, not to skip the hires.
            #
            # Measured 2026-09-22 on Fran's campaign: the plan wanted
            # `stage_coach.numrecruits.c` at 15 crest + 16 deed against a
            # wallet holding ONE deed, the trade for it misfired, and the
            # visit returned -- so `abomination` and `plague_doctor`, both
            # free and both already chosen, were never hired. That happened
            # every week of the campaign. Fran: *"you didnt recruit anyone as
            # i told, so now you're stuck with a limited pool of characters
            # and all stressed out"*, and *"if you dont have enough heirlooms
            # to unlock level, the heirlooms+upgrades step is useless"*.
            say("  [town] a trade misfired - skipping the rest of the "
                "upgrades and going straight to the hires")
            if not _panel_is_open(win):
                _send(backend,
                      act_mod.plan_open_building("stage_coach", resolved),
                      act=act, say=say)
                time.sleep(PANEL_OPEN_SETTLE)
            if not _panel_is_open(win):
                done.append(Did("re-open the stage coach for the hires",
                                sent=True, confirmed=False,
                                why="the panel did not come back, so the "
                                    "hire drags would land on the Hamlet"))
                return done
            break
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
    if other_buys:
        time.sleep(PANEL_OPEN_SETTLE)
        done += buy_building_upgrades(win, backend, resolved, profile,
                                      other_buys, say=say, act=act)
    if advice.treatments:
        time.sleep(PANEL_OPEN_SETTLE)
        done += treat_heroes(win, backend, resolved, profile,
                             advice.treatments, say=say, act=act)
    if getattr(advice, "nurture", ()):
        time.sleep(PANEL_OPEN_SETTLE)
        done += nurture_visit(win, backend, resolved, profile,
                              advice.nurture, say=say, act=act)
    return done


def nurture_visit(win, backend, resolved, profile, buys, *, say=print,
                  act: bool = False) -> list:
    """Buy the nurture hero's ranks, Guild first, then the Blacksmith.

    One hero per visit (`townplan.plan_nurture`). Each rank is a click on the
    building's row for that hero, witnessed by the wallet; the first rank the
    wallet does not confirm stops that building, because every later rank of
    the same tree needs it."""
    from types import SimpleNamespace                  # noqa: PLC0415
    from bridge import act as act_mod                  # noqa: PLC0415
    from bridge import townlayout as TL                # noqa: PLC0415

    done: list = []
    if not buys:
        return done
    hero = SimpleNamespace(guid=buys[0].guid, hero_class=buys[0].hero_class)
    for building in ("guild", "blacksmith"):
        mine = [b for b in buys if b.building == building]
        if not mine:
            continue
        if building == "blacksmith" and not TL.BLACKSMITH_MEASURED:
            for b in mine:
                done.append(Did(f"nurture {b.label()}", sent=False,
                                why="the Blacksmith's rank buttons are not "
                                    "measured (BLACKSMITH_MEASURED)"))
            continue
        for b in mine:
            say(f"  [town] NURTURE {b.label()}")
        if not act:
            done.append(Did(f"nurture at the {building}", sent=False,
                            why="not armed"))
            continue
        opened = _send(backend, act_mod.plan_open_building(building, resolved),
                       act=act, say=say)
        time.sleep(PANEL_OPEN_SETTLE)
        if not opened or not _panel_is_open(win):
            note = _bank_panel_frame(win)
            if not _on_town_screen(win):
                _send(backend, act_mod.plan_close_building(resolved),
                      act=act, say=say)
            done.append(Did(f"open the {building}", sent=bool(opened),
                            why=f"could not confirm the panel opened{note}"))
            continue
        put = _put_hero_in_guild(win, backend, resolved, profile, hero,
                                 say=say, act=act, cursor={})
        done.append(put)
        if put.confirmed:
            for b in mine:
                before = _wallet(profile)
                if building == "guild":
                    row = _guild_row(b.hero_class, b.tree)
                    plan = (act_mod.plan_buy_skill(row, resolved, rank=b.index + 1)
                            if row is not None else None)
                else:
                    plan = act_mod.plan_buy_gear(0 if b.tree == "weapon" else 1,
                                                 b.index, resolved)
                if plan is None:
                    done.append(Did(f"nurture {b.label()}",
                                    why="no row for that tree"))
                    break
                sent = _send(backend, plan, act=act, say=say)
                after = _await(lambda: _wallet(profile), before) if sent else before
                ok = sent and after != before
                why = "" if ok else "the wallet did not move" + _bank_panel_frame(win)
                done.append(Did(f"nurture {b.label()}", sent=sent, confirmed=ok,
                                why=why))
                if not ok:
                    break
        # Close until the Hamlet is back: with a hero in the slot one close
        # was not enough (live 2026-09-28: the embark then found the screen
        # UNKNOWN and the iteration was lost).
        for _close in range(3):
            _send(backend, act_mod.plan_close_building(resolved), act=act, say=say)
            time.sleep(PANEL_OPEN_SETTLE)
            if _on_town_screen(win):
                break
    return done


def buy_building_upgrades(win, backend, resolved, profile, purchases, *,
                          say=print, act: bool = False) -> list:
    """Buy guild / blacksmith / sanitarium / ... ranks, each in its OWN panel.

    Live 2026-09-24 these ranks were clicked at points inside the COACH panel
    and were gated off after that. The geometry itself was never the issue:
    every building draws its trees from the one `building_base_layout`, which
    is what `townlayout.upgrade_node` derives from and what was verified on
    the coach. What was wrong was the panel. So each building is opened,
    confirmed open, its crest clicked to draw the trees, and each rank bought
    against the wallet. The first rank the wallet does not confirm stops that
    building: a click that bought nothing is the geometry disagreeing, and the
    next click would repeat it. Ranks needing an heirloom trade are left for
    the coach's exchange flow (reported, not sent).
    """
    from bridge import act as act_mod                  # noqa: PLC0415

    done: list = []
    by_building: dict = {}
    for pu in purchases:
        by_building.setdefault(pu.node.tree_id.split(".")[0], []).append(pu)
    for building, buys in by_building.items():
        payable = [pu for pu in buys if not pu.trades]
        for pu in buys:
            if pu.trades:
                done.append(Did(f"buy {pu.node.label()}",
                                why="needs an heirloom trade first - not sent"))
        if not payable:
            continue
        if not _on_town_screen(win):
            _send(backend, act_mod.plan_close_building(resolved), act=act, say=say)
            time.sleep(PANEL_OPEN_SETTLE)
        opened = _send(backend, act_mod.plan_open_building(building, resolved),
                       act=act, say=say)
        if not opened:
            done.append(Did(f"open the {building}", why="not sent"))
            continue
        time.sleep(PANEL_OPEN_SETTLE)
        if act and not _panel_is_open(win):
            note = _bank_panel_frame(win)
            if not _on_town_screen(win):
                _send(backend, act_mod.plan_close_building(resolved),
                      act=act, say=say)
            done.append(Did(f"open the {building}", sent=True, confirmed=False,
                            why=f"could not confirm the panel opened{note}"))
            continue
        _send(backend, act_mod.plan_open_upgrades(resolved), act=act, say=say)
        for purchase in payable:
            before = _wallet(profile)
            sent = _send(backend,
                         act_mod.plan_buy_upgrade(purchase, _tree_index(purchase.node),
                                                  resolved),
                         act=act, say=say)
            after = _await(lambda: _wallet(profile), before) if sent else before
            ok = sent and after != before
            done.append(Did(purchase.node.label(), sent=sent, confirmed=ok,
                            why="" if ok else "the wallet did not move"))
            if sent and not ok:
                break
        _send(backend, act_mod.plan_close_building(resolved), act=act, say=say)
        time.sleep(PANEL_OPEN_SETTLE)
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


def drawn_roster(town):
    """The heroes the roster list actually DRAWS, in the order it draws them.

    **A hero standing in the party bar is not in the list.** DD moves them
    out of it, so a roster of 11 with 3 seated draws exactly 8 -- which is
    also `ROSTER_ROWS_VISIBLE`, so nothing can scroll and the chevron is not
    even drawn.

    Everything here used to reason about `town.heroes`, the SAVE order, and
    that is a different sequence. Measured live 2026-09-22 on Fran's
    campaign: save rows 3, 6 and 9 (jester, leper, hellion) were in the
    party, the list drew the remaining eight with Dismas -- save row 0 -- in
    its top row, and `_roster_offset` matched the 11-long class sequence
    against 8 drawn portraits and reported **offset 2**. The seat for save
    row 0 was then refused as "not on screen", the scroll could not move a
    list that was already at its top, and the night looped.

    The failure is worse than a refusal when the alignment happens to pass:
    this function's own warning is that a wrong offset does not miss, it
    drags a DIFFERENT hero -- which is how a Leper reached a back rank,
    reported by Fran as *"the seating is seating a leper on the back which is
    really sus"* while the plan had correctly put him at rank 1.
    """
    return [h for h in town.heroes if not getattr(h, "in_party", False)]


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
    classes = [h.hero_class for h in drawn_roster(town)]
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


def _roster_pitch_px(strip) -> Optional[float]:
    """One roster row, in the strip's own pixels (the strip is frame-sized)."""
    from bridge import townlayout as TL              # noqa: PLC0415
    first = TL.roster_portrait(0)
    last = TL.roster_portrait(ROSTER_ROWS_VISIBLE - 1)
    if strip is None or first is None or last is None:
        return None
    span = (last[1] - first[1]) + 0.06              # the strip's crop margins
    return strip.shape[0] * (last[1] - first[1]) / (ROSTER_ROWS_VISIBLE - 1) / span


def _rows_moved(before, after, max_rows: int = 3) -> Optional[int]:
    """How many rows the list scrolled DOWN between two strips, or None.

    A one-row scroll shifts every drawn row up by exactly one pitch, so rows
    1-7 of `before` reappear as rows 0-6 of `after`. The k whose shifted
    overlap matches best is the answer -- an identity-free witness that a
    counted scroll moved exactly as many rows as it was clicked. None when the
    frames are missing or nothing matches well (a panel over the list, a
    mid-animation grab)."""
    import numpy as np                               # noqa: PLC0415
    if before is None or after is None or before.shape != after.shape:
        return None
    pitch = _roster_pitch_px(before)
    if not pitch:
        return None
    best_k, best = None, None
    for k in range(0, max_rows + 1):
        cut = int(round(k * pitch))
        if cut >= before.shape[0] - 8:
            break
        a, b = before[cut:], after[:before.shape[0] - cut]
        diff = float(np.abs(a - b).mean())
        if best is None or diff < best:
            best_k, best = k, diff
    if best is None or best > ROWS_MOVED_MAX_DIFF:
        return None
    return best_k


#: Mean per-channel difference under which a shifted overlap counts as the
#: same rows. Set from live frames (see the changelog, 2026-09-26).
ROWS_MOVED_MAX_DIFF = 6.0


#: Measured live 2026-09-26 on the Hamlet roster: one chevron click scrolls a
#: row in ~0.58 s, and clicks sent 0.1 s apart QUEUE -- five of them moved
#: exactly five rows in 2.0 s. The old path waited `TOWN_PANEL_SETTLE` plus
#: `ROSTER_SCROLL_SETTLE`, 1.8 s a row, which on a 100-hero roster made one
#: lookup a two-minute scroll and a town visit forty minutes.
ROSTER_CLICK_GAP = 0.1
ROSTER_SETTLE_FAST = 0.75
#: Rows per verified burst. `_rows_moved` compares the overlap of two strips
#: of eight rows, so a burst must leave some overlap to witness.
ROSTER_CHUNK = 6


def scroll_roster_by(win, backend, resolved, clicks: int, *, say=print,
                     act: bool = False) -> "Did":
    """Scroll DOWN exactly `clicks` rows from wherever the list is.

    Counted, and now WITNESSED per burst: up to `ROSTER_CHUNK` clicks are
    sent `ROSTER_CLICK_GAP` apart, and after they settle `_rows_moved` must
    read exactly that many rows. A short burst (a dropped click) is topped
    up; an unreadable strip or an over-long move refuses, because a list at
    the wrong offset seats and equips the wrong hero. Pairs with
    `force_roster_to_top`, which makes "wherever" mean the top.
    """
    from bridge import townlayout as TL               # noqa: PLC0415

    label = f"scroll the roster down {clicks}"
    if clicks <= 0:
        return Did(label, sent=False, confirmed=True, why="nothing to do")
    if not act:
        say(f"  [town] WOULD scroll the roster down {clicks}")
        return Did(label, why="not armed")
    pt = resolved.at(TL.roster_scroll(True))
    if pt is None or not TL.ROSTER_SCROLL_MEASURED:
        return Did(label, why="no verified roster scroll arrow")
    left, retries = int(clicks), 0
    while left > 0:
        burst = min(ROSTER_CHUNK, left)
        before = _roster_strip(win, resolved)
        for _ in range(burst):
            backend.click(pt)
            time.sleep(ROSTER_CLICK_GAP)
        time.sleep(ROSTER_SETTLE_FAST)
        moved = _rows_moved(before, _roster_strip(win, resolved),
                            max_rows=min(ROSTER_CHUNK + 1, ROSTER_ROWS_VISIBLE - 1))
        if moved is None or moved > burst:
            return Did(label, sent=True, confirmed=False,
                       why=f"could not witness the scroll (read {moved} of "
                           f"{burst} rows)")
        if moved < burst:
            retries += 1
            if retries > 3:
                return Did(label, sent=True, confirmed=False,
                           why="the list stopped moving before the target row")
        left -= moved
    return Did(label, sent=True, confirmed=True)


def force_roster_to_top(win, backend, resolved, town, *, say=print,
                        act: bool = False) -> "Did":
    """Click UP until the list cannot move, so the offset is 0 by force.

    **`_roster_offset` cannot always be read**, and a roster with repeated
    classes produces exactly that: so this stops asking where the list is and
    MOVES it somewhere known. Over-clicking is safe by the list's own design
    -- the chevron is only drawn when it can act -- so the clicks go in one
    fast burst (enough for the whole roster) and one more click afterwards
    must move NOTHING, which is what "at the top" looks like.
    """
    from bridge import townlayout as TL               # noqa: PLC0415

    label = "scroll the roster to the top"
    if not act:
        say("  [town] WOULD scroll the roster to the top")
        return Did(label, why="not armed")
    pt = resolved.at(TL.roster_scroll(False))
    if pt is None or not TL.ROSTER_SCROLL_MEASURED:
        return Did(label, why="no verified roster scroll arrow")
    if _roster_strip(win, resolved) is None:
        return Did(label, why="cannot see the roster list")
    # Bursts of up-clicks until one moves NOTHING. Cost scales with how far
    # down the list is, not with the roster's length: ~1.4 s a burst.
    for _burst in range(len(getattr(town, "heroes", ()) or ()) // ROSTER_CHUNK + 3):
        before = _roster_strip(win, resolved)
        for _ in range(ROSTER_CHUNK):
            backend.click(pt)
            time.sleep(ROSTER_CLICK_GAP)
        time.sleep(ROSTER_SETTLE_FAST)
        after = _roster_strip(win, resolved)
        if before is None or after is None:
            return Did(label, sent=True, confirmed=False,
                       why="lost sight of the roster list mid-scroll")
        if not _bar_moved(before, after):
            return Did(label, sent=True, confirmed=True, why="at the top")
    return Did(label, sent=True, confirmed=False,
               why="the list never stopped moving")


def _bring_into_view(win, backend, resolved, town, index: int, *, say=print,
                     act: bool = False):
    """Scroll the least that puts save row `index` on screen. Returns
    `(offset, Did or None)` -- the offset is None when it could not be read."""
    # **ANCHOR, DO NOT MEASURE. PORTRAIT MATCHING IS NOT SOUND ON A MODDED
    # INSTALL.** `_roster_offset` compares the drawn portraits against the
    # art shipped in the game files, and `Amara's Character UI` -- one of the
    # 17 mods on Fran's save -- replaces that art. Measured on the Hamlet
    # 2026-09-22: the eight rows scored 26 to 57 mean levels, with runner-up
    # gaps as small as 0.02 against a required margin of 2.0, and the matcher
    # confidently named a `flagellant` that is not on the roster. It reported
    # offset 2 for a list whose up-chevron was not drawn, which is the list
    # telling us it is at its top.
    #
    # A confident wrong offset is the worst outcome available here: it does
    # not miss, it drags a DIFFERENT hero, and the party bar confirms it.
    # That is how a Leper reached a back rank -- Fran: *"the seating is
    # seating a leper on the back which is really sus"* -- with the plan
    # correctly putting him at rank 1.
    #
    # `force_roster_to_top` needs no art: it clicks up until the STRIP stops
    # changing, which is what "already at the top" looks like, and the
    # chevron is only drawn when it can act so over-clicking is a no-op. From
    # there the offset is 0 by construction and any scroll down is counted
    # rather than re-read.
    got = force_roster_to_top(win, backend, resolved, town, say=say, act=act)
    if not got.confirmed:
        return None, got
    if index < ROSTER_ROWS_VISIBLE:
        return 0, got
    want = min(index - ROSTER_ROWS_VISIBLE + 1,
               max(0, len(drawn_roster(town)) - ROSTER_ROWS_VISIBLE))
    scrolled = scroll_roster_by(win, backend, resolved, want, say=say, act=act)
    if not scrolled.confirmed:
        return None, scrolled
    return want, scrolled



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


#: How textured a party slot's portrait patch has to be to count as FILLED.
#
# **"The bar did not change" could never tell an empty slot from a failed
# drag**, and that ambiguity is what hid the real bug for a whole night: the
# clear left heroes on the bar, the embark planner then picked heroes who
# were ON it, and those cannot be dragged out of a roster list that does not
# draw them. Measured 2026-09-22 with the party bar filled and then emptied,
# same slots, same screen:
#
#     empty     mean 15.2  std  7.9   (all four identical -- it is frame art)
#     occupied  mean 32.5-74.5  std 42.0-69.5
#
# The STD is the discriminator rather than the mean: an empty slot is flat
# frame, a filled one carries a portrait. 20 sits five times clear of both
# sides, and the mean is deliberately not used -- a dark portrait and a lit
# frame can share one.
PARTY_SLOT_TEXTURE = 20.0
PARTY_SLOT_PATCH = 20


def party_slot_filled(win, resolved, index: int) -> Optional[bool]:
    """Is party slot `index` holding a hero? None when it cannot be read."""
    from bridge import townlayout as TL                # noqa: PLC0415

    try:
        import numpy as np                             # noqa: PLC0415
        from bridge import screen as screen_mod        # noqa: PLC0415

        point = resolved.at(TL.party_portrait(index))
        img = screen_mod.grab(win)
        if point is None or img is None:
            return None
        ox, oy = resolved.win.client_origin
        cx, cy = point[0] - ox, point[1] - oy
        pad = PARTY_SLOT_PATCH
        patch = np.asarray(img, dtype=float)[cy - pad:cy + pad,
                                             cx - pad:cx + pad, :3]
        if patch.size == 0:
            return None
        return float(patch.std()) >= PARTY_SLOT_TEXTURE
    except Exception:                                  # noqa: BLE001
        return None


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
        # **ASK WHETHER IT IS EMPTY, NOT WHETHER SOMETHING MOVED.** A slot
        # that was already empty and a drag that failed look identical to
        # `_bar_moved`, and the difference is a raid: heroes left on the bar
        # are not drawn in the roster list, so the seats for them are refused
        # and the party goes out short. Measured 2026-09-22: the planner
        # picked guids 7 and 8 while `in_party` held [2, 8, 7, 27], and both
        # seats failed every iteration.
        filled = party_slot_filled(win, resolved, index)
        if filled is False:
            done.append(Did(f"clear party slot {index}", sent=False,
                            confirmed=True, why="already empty"))
            continue
        sent = _drag(backend, resolved, src, park, act=act, say=say)
        time.sleep(STEP_SETTLE)
        after = party_slot_filled(win, resolved, index)
        if after is None:
            # Cannot see it: fall back to the old movement test rather than
            # claim either way.
            done.append(Did(f"clear party slot {index}", sent=sent,
                            confirmed=False,
                            why="could not read the slot to confirm"))
            continue
        done.append(Did(f"clear party slot {index}", sent=sent,
                        confirmed=not after,
                        why="" if not after else "the hero is STILL on the bar"))
    return done


def learn_roster_rows(win, backend, resolved, profile, *, rows=None,
                      say=print, act: bool = False) -> dict:
    """Which hero sits on which roster row, learned by ASKING THE GAME.

    **Every way of inferring this is broken on Fran's install.** The list
    order is not the save's (Ceauce is save index 5 and draws at row 3), the
    portrait matcher that used to recover it compares against art that
    `Amara's Character UI` replaces, and the save carries no row. So this
    stops inferring and runs an experiment: drag a row onto the bar, read
    `persist.roster.json` to see whose `in_party` just turned on, and put
    them back.

    That is the one channel the mods cannot touch -- the save records the
    party, and it is the same evidence `clear_party` and the seat count
    already trust.

    Returns `{roster_row: guid}` for every row it could identify. Rows it
    could not are simply absent, which is the honest answer and lets the
    caller seat whoever it did find.

    Costs one drag out and one back per row, so it is run ONCE per town
    visit and the result is handed to the seating.
    """
    from bridge import townlayout as TL                # noqa: PLC0415

    found: dict = {}
    if not act:
        say("  [town] WOULD learn which hero is on which roster row")
        return found
    rows = range(ROSTER_ROWS_VISIBLE) if rows is None else rows
    park = TL.roster_portrait(_park_row(_Snapshot(profile)))
    for row in rows:
        src_pt = resolved.at(TL.roster_portrait(row))
        dst_pt = resolved.at(TL.party_portrait(0))
        if src_pt is None or dst_pt is None:
            continue
        before = _in_party(profile)
        if party_slot_filled(win, resolved, 0):
            continue                      # rank 1 is busy; nothing to learn
        backend.drag(src_pt, dst_pt)
        time.sleep(ROSTER_SCROLL_SETTLE)
        after = _in_party(profile)
        gained = after - before
        if len(gained) == 1:
            guid = next(iter(gained))
            found[row] = guid
            say(f"  [town] roster row {row} is guid {guid}")
        # Put them back, whoever they were, so the next row starts clean.
        backend.drag(dst_pt, resolved.at(park) if park else src_pt)
        time.sleep(ROSTER_SCROLL_SETTLE)
    return found


# The roster list's SORT buttons, above it on the Hamlet and the estate map
# alike (client fractions, measured live 2026-09-23 at 1280x720 by their own
# tooltips): Level, Stress, Class, Activity. A second click on the same button
# flips the order, and the order is NOT in the save -- so which way it runs
# is read back off the list. A hero seated in the party stays pinned at the top.
ROSTER_SORT = {"level": (1143 / 1280, 64 / 720), "stress": (1168 / 1280, 64 / 720),
               "class": (1194 / 1280, 64 / 720), "activity": (1219 / 1280, 64 / 720)}


def predicted_order(profile, by_stress: int = 0) -> list:
    """The roster list PREDICTED from the save.

    **The game SAVES the roster in the order it is DISPLAYED.** Measured
    2026-09-23 and 2026-09-24: the learned rows were the living heroes in
    `persist.roster.json` file order (the dead, `roster.status` 3, stay in the
    file and are not drawn), whatever sort button was active when the file
    was written. First under an all-ties stress sort, then under a
    most-stressed-first one, where the FILE itself had been rewritten
    most-stressed first. So with no click since the last save, the list IS
    the file order (`by_stress=0`). A sort click re-orders the display at once
    and the file only at the next save, so after one the list is a STABLE sort
    of the file order: `by_stress=+1` least stressed first, `-1` most.
    """
    heroes = list(_Snapshot(profile).heroes)
    if by_stress:
        heroes = sorted(heroes, key=lambda h: by_stress * float(h.stress))
    return [h.guid for h in heroes]


def positions_from_order(order) -> dict:
    """`{guid: (scroll_clicks, row)}` for a list in this order, the shape
    `learn_roster_positions` returns: rows 0-7 on page 0, then one click per
    further hero, each revealed at row 7."""
    last = ROSTER_ROWS_VISIBLE - 1
    return {g: (0, i) if i <= last else (i - last, last)
            for i, g in enumerate(order)}


def sort_roster_low_stress_first(win, backend, resolved, profile, *,
                                 say=print, act: bool = False) -> dict:
    """Every hero's `{guid: (scroll_clicks, row)}` from the SAVE, checked on
    two rows; `{}` to fall back to the full walk.

    Fran, 2026-09-23: avoid *"pushing into comp for every member of the
    roster"*. The list is the save's order (see `predicted_order`), so the
    first try clicks nothing at all: predict the file order, probe the first
    and last rows of page 0. If that misses, click the Stress button and try
    both stable sorts of the file order, since the direction a click lands on
    is not in the save. Anything else falls back to the walk. Every seat is
    still checked against the save by identity.
    """
    if not act:
        say("  [town] WOULD predict the roster order from the save")
        return {}
    base = predicted_order(profile)
    if not base:
        return {}
    probe_rows = sorted({0, min(ROSTER_ROWS_VISIBLE, len(base)) - 1})

    def matches(order) -> Optional[dict]:
        top = force_roster_to_top(win, backend, resolved, _Snapshot(profile),
                                  say=lambda _s: None, act=act)
        if not top.confirmed:
            return None
        seen = learn_roster_rows(win, backend, resolved, profile,
                                 rows=probe_rows, say=lambda _s: None, act=act)
        return seen if seen and all(seen.get(r) == order[r]
                                    for r in probe_rows) else {}

    first = matches(base)
    if first is None:
        return {}
    if first:
        say(f"  [town] roster order read from the save and confirmed on rows "
            f"{probe_rows} - no sort, no walk")
        return positions_from_order(base)
    for _attempt in range(2):
        backend.click(resolved.at(ROSTER_SORT["stress"]))
        time.sleep(ROSTER_SCROLL_SETTLE)
        low, high = predicted_order(profile, +1), predicted_order(profile, -1)
        got = matches(low)
        if got is None:
            return {}
        if got:
            say(f"  [town] roster sorted least stressed first, predicted from "
                f"the save and confirmed on rows {probe_rows}")
            return positions_from_order(low)
        if matches(high):
            say("  [town] the stress sort came up most-stressed first - flipping")
            continue
    say("  [town] the list does not match any order predicted from the save - "
        "walking it instead")
    return {}


def learn_roster_positions(win, backend, resolved, profile, *, say=print,
                           act: bool = False) -> dict:
    """`{guid: (scroll_clicks, row)}` for the WHOLE roster, not one page.

    `learn_roster_rows` only sees the eight rows the list draws, and a roster
    of 18 hides ten. Measured 2026-09-23: every planned hero outside the first
    page fell back to scroll-and-guess, all four seats were dragged from row 7,
    and the bar took 3 of 4.

    One scroll click moves the list one row, so after `k` clicks down from the
    top the hero newly revealed at the bottom row is list position `7 + k`.
    Page 0 is learned whole; every further click reveals exactly one new hero
    at row 7, which is probed the same way -- drag to the bar, ask the save who
    arrived, put them back. A swallowed click shows up as a guid already known,
    and is retried rather than trusted.
    """
    from bridge import act as act_mod                  # noqa: PLC0415

    found: dict = {}
    if not act:
        say("  [town] WOULD learn every roster row")
        return found
    got = force_roster_to_top(win, backend, resolved, _Snapshot(profile),
                              say=say, act=act)
    if not got.confirmed:
        return found
    for row, guid in learn_roster_rows(win, backend, resolved, profile,
                                       say=say, act=act).items():
        found[guid] = (0, row)
    total = len(_Snapshot(profile).heroes)
    down = act_mod.plan_scroll_roster(True, resolved)
    clicks = 0
    misses = 0
    # **Whether the list MOVED is the strip's question, not the probe's.**
    # Measured 2026-09-23: a busy hero (in a building) at row 7 cannot be
    # dragged, so the probe learns nothing -- and the old loop counted that
    # as a swallowed click. The list HAD moved, so every later position was
    # one short, and three busy heroes in a row ended the pass with an
    # occultist and an antiquarian never learned. Seats then fell back to
    # guessing and two went to the same row.
    while (len(found) < total and clicks < total + 2 and misses < 3
           and down.actionable):
        before = _roster_strip(win, resolved)
        _send(backend, down, act=True, say=lambda _s: None)
        time.sleep(ROSTER_SCROLL_SETTLE)
        after = _roster_strip(win, resolved)
        if before is not None and after is not None                 and not _bar_moved(before, after):
            misses += 1                  # the list did not move: at the end,
            continue                     # or a swallowed click
        misses = 0
        clicks += 1
        page = learn_roster_rows(win, backend, resolved, profile,
                                 rows=[ROSTER_ROWS_VISIBLE - 1],
                                 say=lambda _s: None, act=act)
        guid = page.get(ROSTER_ROWS_VISIBLE - 1)
        if guid is None or guid in found:
            continue                     # a hero that cannot be dragged
        found[guid] = (clicks, ROSTER_ROWS_VISIBLE - 1)
        say(f"  [town] list position {ROSTER_ROWS_VISIBLE - 1 + clicks} "
            f"is guid {guid}")
    return found


def _in_party(profile) -> set:
    """Guids the save currently marks as seated."""
    try:
        from bridge.town_state import read_town_state   # noqa: PLC0415

        return {h.guid for h in read_town_state(profile).heroes
                if getattr(h, "in_party", False)}
    except Exception:                                   # noqa: BLE001
        return set()


class _Snapshot:
    """Just enough of a TownState for `_park_row`, without a second read."""

    def __init__(self, profile):
        try:
            from bridge.town_state import read_town_state  # noqa: PLC0415

            self.heroes = read_town_state(profile).heroes
        except Exception:                                  # noqa: BLE001
            self.heroes = []


# The hero tab's DISMISS icon and close X, as client fractions. Measured live
# 2026-09-23 at 1280x720, on the Hamlet and the estate map alike: a RIGHT-click
# on a roster row opens the tab (a left click only starts a drag), the icons
# down its top-left edge are Rename (y 110), Dismiss (y 133) and Switch Outfit
# (y 157), and Dismiss raises "This will dismiss the hero permanently" with
# Yes on `MODAL_OK`. The save drops the guid within a second.
HERO_TAB_DISMISS = (119 / 1280, 133 / 720)
HERO_TAB_CLOSE = (1003 / 1280, 110 / 720)
DISMISS_WITNESS_S = 5.0


def dismiss_heroes(win, backend, resolved, profile, guids, *, say=print,
                   act: bool = False) -> list:
    """Dismiss each hero in `guids` that the first page of the list shows.

    **Irreversible, so it never acts on a guess.** Each row is identified by
    `learn_roster_rows`, the drag probe the save witnesses, immediately
    before its tab is opened. The page is re-learned after every dismissal,
    because the list closes up. A hero not on the first page is left alone:
    sort the list by stress first (the button above it; the order is not
    saved) so the stressed heroes Fran's rule targets are on that page.
    Only the estate map can probe, since the Hamlet has no party bar to drag
    onto.
    """
    from bridge import act as act_mod                  # noqa: PLC0415
    from bridge import townlayout as TL                # noqa: PLC0415
    from bridge.actuation.plan import InputPlan, Step  # noqa: PLC0415

    wanted = [int(g) for g in guids]
    done: list = []
    if not wanted:
        return done
    if not act:
        say(f"  [town] WOULD dismiss {len(wanted)} hero(es): {wanted}")
        return done
    actuator = act_mod.Actuator(backend=backend, enabled=True,
                                kill=act_mod.KillSwitch())
    while wanted:
        page = learn_roster_rows(win, backend, resolved, profile,
                                 say=lambda _s: None, act=act)
        hits = [(row, g) for row, g in sorted(page.items()) if g in wanted]
        if not hits:
            say(f"  [town] {len(wanted)} hero(es) to dismiss are not on the "
                f"first page - leaving them")
            break
        row, guid = hits[0]
        label = f"dismiss guid {guid} (row {row})"
        pt = resolved.at(TL.roster_portrait(row))
        before = {h.guid for h in _Snapshot(profile).heroes}
        if pt is None or guid not in before:
            done.append(Did(label, why="row point unknown or hero already gone"))
            wanted.remove(guid)
            continue
        actuator.execute(InputPlan(
            (Step("rclick", screen=pt, label=f"open row {row}'s tab"),),
            "town", "open the hero tab"))
        time.sleep(1.2)
        backend.click(resolved.at(HERO_TAB_DISMISS))
        time.sleep(1.0)
        backend.click(resolved.modal_ok)
        gone = False
        deadline = time.time() + DISMISS_WITNESS_S
        while time.time() < deadline:
            time.sleep(0.5)
            now = {h.guid for h in _Snapshot(profile).heroes}
            if now != before:
                gone = now == before - {guid}
                break
        if not gone:
            # Whatever happened, stop: an irreversible step that did not do
            # exactly what was asked is not repeated.
            backend.click(resolved.at(HERO_TAB_CLOSE))
            done.append(Did(label, sent=True, confirmed=False,
                            why="the save did not drop exactly this hero"))
            break
        say(f"  [town] dismissed guid {guid} - confirmed by the save")
        done.append(Did(label, sent=True, confirmed=True))
        wanted.remove(guid)
        time.sleep(0.8)
    return done

#: At most this many dismissals per town visit. The cap's first pass on a
#: 102-hero roster is 12; bounding it keeps one visit from turning into an
#: hour if something is slow, and the rest go next week.
MAX_DISMISSALS_PER_VISIT = 12


#: The Abbey's activity slots, measured off `captures/screens/abbey_panel.png`
#: (1280x720, 2026-09-26): three rows -- Cloister (meditation), Transept
#: (prayer), Penance Hall (flagellation) -- of three slots. An unlocked empty
#: slot is a dark square, a locked one a brick arch, and a drop onto either an
#: occupied or a locked slot does nothing, which the save shows.
ABBEY_ROWS = {"meditation": 210.0 / 720, "prayer": 360.0 / 720,
              "flagellation": 513.0 / 720}
ABBEY_SLOT_X = (765.0 / 1280, 855.0 / 1280, 945.0 / 1280)
#: The confirm (check mark) drawn under a filled slot; the treatment is only
#: bought when it is clicked (measured live 2026-09-26: gold 14,315 -> 13,315
#: on the click, and nothing without it).
ABBEY_CONFIRM_DY = 58.0 / 720
TREAT_WITNESS_S = 4.0
#: Confirm clicks per placed hero before the treatment is reported missed.
TREAT_CONFIRM_TRIES = 2


def treat_heroes(win, backend, resolved, profile, treatments, *, say=print,
                 act: bool = False) -> list:
    """Put each planned hero into their stress activity.

    `townplan.choose_treatments` has decided these every visit since it was
    written, and nothing executed them: live 2026-09-26 the same Duelist at 99
    stress was planned into the Cloister on every town visit of a night. Only
    the Abbey is measured; other buildings are reported, not sent. The drag
    is from the hero's roster row (found with the witnessed scroll) onto the
    activity's slots left to right, and a slot counts only when the SAVE puts
    that hero in the building.
    """
    from bridge import act as act_mod                  # noqa: PLC0415
    from bridge import townlayout as TL                # noqa: PLC0415
    from bridge.town_state import read_town_state      # noqa: PLC0415

    done: list = []
    abbey = [t for t in treatments if t.building == "abbey"
             and t.activity in ABBEY_ROWS and t.guid]
    for t in treatments:
        if t not in abbey:
            done.append(Did(f"treat {t.hero_class} in the {t.building}",
                            why="that building's slots are not measured"))
    if not abbey:
        return done
    if not act:
        say(f"  [town] WOULD treat {len(abbey)} hero(es) in the Abbey")
        return done
    if not _on_town_screen(win):
        _send(backend, act_mod.plan_close_building(resolved), act=act, say=say)
        time.sleep(PANEL_OPEN_SETTLE)
    _send(backend, act_mod.plan_open_building("abbey", resolved), act=act, say=say)
    time.sleep(PANEL_OPEN_SETTLE)
    if not _panel_is_open(win):
        note = _bank_panel_frame(win)
        if not _on_town_screen(win):
            _send(backend, act_mod.plan_close_building(resolved), act=act, say=say)
        return done + [Did("open the abbey", sent=True, confirmed=False,
                           why=f"could not confirm the panel opened{note}")]
    cursor: dict = {}
    for t in abbey:
        label = f"treat {t.hero_class} ({t.activity})"
        try:
            town_now = read_town_state(profile)
        except Exception as exc:                       # noqa: BLE001
            done.append(Did(label, why=f"could not read the roster ({exc})"))
            break
        rows = [i for i, h in enumerate(town_now.heroes) if h.guid == t.guid]
        if not rows:
            done.append(Did(label, why="not on the roster any more"))
            continue
        screen_row, why = _roster_row_for(win, backend, resolved, town_now,
                                          rows[0], act=act, cursor=cursor)
        if screen_row is None:
            done.append(Did(label, why=why))
            continue
        src = resolved.at(TL.roster_portrait(screen_row))
        placed = False
        # Only the UNLOCKED slots (`Treatment.slots`, from the ranks owned),
        # and the purchase is the confirm under the slot, witnessed by the
        # wallet: the save does not put the hero in the building until the
        # panel closes.
        for x in ABBEY_SLOT_X[:max(1, int(getattr(t, "slots", 1) or 1))]:
            row_y = ABBEY_ROWS[t.activity]
            before = _wallet(profile).get("gold")
            backend.drag(src, resolved.at((x, row_y)))
            time.sleep(0.8)
            # Two confirms, each witnessed by the wallet. Live 2026-09-27 the
            # first treatment of a visit (a Leper, meditation) sat in its slot
            # uncharged while the same geometry confirmed a prayer seconds
            # later; a second click on the check mark costs nothing when the
            # first already took (the slot is then occupied and the mark gone).
            for _confirm in range(TREAT_CONFIRM_TRIES):
                backend.click(resolved.at((x, row_y + ABBEY_CONFIRM_DY)))
                deadline = time.time() + TREAT_WITNESS_S
                while time.time() < deadline:
                    time.sleep(0.5)
                    now = _wallet(profile).get("gold")
                    if before is not None and now is not None and now < before:
                        placed = True
                        break
                if placed:
                    break
            if placed:
                break
            cursor.clear()          # the drag may have moved the list
            screen_row, why = _roster_row_for(win, backend, resolved,
                                              read_town_state(profile),
                                              rows[0], act=act, cursor=cursor)
            if screen_row is None:
                break
            src = resolved.at(TL.roster_portrait(screen_row))
        done.append(Did(label, sent=True, confirmed=placed,
                        why="" if placed else "no slot in that row took the hero"
                                              + _bank_panel_frame(win)))
    _send(backend, act_mod.plan_close_building(resolved), act=act, say=say)
    time.sleep(PANEL_OPEN_SETTLE)
    return done


def dismiss_surplus(win, backend, resolved, profile, heroes, *, say=print,
                    act: bool = False) -> list:
    """Dismiss the heroes `townplan` named, reaching any row of the list.

    Fran, 2026-09-26: per-class caps by level BAND (`hero_roles.band_caps`),
    fodder only. Unlike `dismiss_heroes` this is not limited to the first page:
    `_roster_row_for` rewinds and scrolls with every burst witnessed off the
    strip (verified live: save row 40 opened save row 40's sheet). Each
    dismissal is right-click -> Dismiss -> Yes, and the SAVE must then drop
    exactly that guid -- anything else stops the pass, since the step cannot
    be undone. The save is re-read before each one because the list closes up.
    """
    from bridge import townlayout as TL                # noqa: PLC0415
    from bridge.town_state import read_town_state      # noqa: PLC0415

    done: list = []
    wanted = list(heroes)[:MAX_DISMISSALS_PER_VISIT]
    if not wanted:
        return done
    if not act:
        say(f"  [town] WOULD dismiss {len(wanted)}: "
            + ", ".join(h.label() for h in wanted))
        return done
    for hero in wanted:
        label = f"dismiss {hero.label()}"
        try:
            town_now = read_town_state(profile)
        except Exception as exc:                       # noqa: BLE001
            done.append(Did(label, why=f"could not read the roster ({exc})"))
            break
        rows = [i for i, h in enumerate(town_now.heroes) if h.guid == hero.guid]
        if not rows:
            done.append(Did(label, why="not on the roster any more"))
            continue
        screen_row, why = _roster_row_for(win, backend, resolved, town_now,
                                          rows[0], act=act, cursor=None)
        if screen_row is None:
            done.append(Did(label, why=why))
            break
        before = {h.guid for h in town_now.heroes}
        backend.right_click(resolved.at(TL.roster_portrait(screen_row)))
        time.sleep(1.2)
        backend.click(resolved.at(HERO_TAB_DISMISS))
        time.sleep(1.0)
        backend.click(resolved.modal_ok)
        gone = False
        deadline = time.time() + DISMISS_WITNESS_S
        while time.time() < deadline:
            time.sleep(0.5)
            now = {h.guid for h in _Snapshot(profile).heroes}
            if now != before:
                gone = now == before - {hero.guid}
                break
        if not gone:
            backend.click(resolved.at(HERO_TAB_CLOSE))
            done.append(Did(label, sent=True, confirmed=False,
                            why="the save did not drop exactly this hero - "
                                "stopping"))
            break
        say(f"  [town] dismissed {hero.label()} - confirmed by the save")
        done.append(Did(label, sent=True, confirmed=True))
        time.sleep(0.6)
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


def _look_frame(win):
    """A grab for comparing one screen against itself a moment later."""
    try:
        import numpy as np                             # noqa: PLC0415
        from bridge import screen as screen_mod        # noqa: PLC0415

        img = screen_mod.grab(win)
        return None if img is None else np.asarray(img, dtype=float)
    except Exception:                                  # noqa: BLE001
        return None


#: How much of the frame has to differ before a click counts as having landed.
#
# The estate map animates, so "identical" is too strict. Measured on it
# 2026-09-22, mean absolute difference over the whole frame:
#
#     idle, no click at all      0.023
#     a click on empty map       0.042
#     a click on a quest emblem  1.247   (the left panel redraws)
#
# 0.6 is fourteen times the noise and half the signal, which is as wide a gap
# as this file has for any threshold.
FRAME_SAME = 0.6


def _frames_match(before, after) -> bool:
    try:
        import numpy as np                             # noqa: PLC0415

        if before.shape != after.shape:
            return False
        return float(np.abs(before - after).mean()) < FRAME_SAME
    except Exception:                                  # noqa: BLE001
        return False


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
        if not act or at is None:
            return [Did(label, sent=act, confirmed=act)]
        # **A QUEST CLICK THAT HITS NOTHING LEAVES THE LAST ONE SELECTED**, and
        # that is how a Resolve-0 party was sent into the endless Farmstead.
        # Measured 2026-09-22 on Fran's campaign: the plan chose `warrens
        # cleanse`, the derived token resolved to (583, 359), and the raid
        # that started was `farm wave` -- the quest selected on a previous
        # iteration. The save carries no "selected" marker to check against,
        # so the evidence is the SCREEN: picking a different quest always
        # redraws the left-hand panel, which names it and lists its goals and
        # rewards.
        #
        # This install is why it misses at all. `More Available Quests` puts
        # TEN quests on the map where vanilla draws three to five -- four of
        # them in the Ruins alone -- and `quest_point` derives one token per
        # DUNGEON from shipped geometry, which cannot address the second quest
        # in a region and lands off the emblem when the row grows.
        #
        # Refusing is much cheaper than the alternative: the list holds
        # `darkestdungeon` at difficulty 6 and the Farmstead, so a missed
        # click can embark a level-0 party on content that ends a campaign.
        before = _look_frame(win)
        backend.click(at)
        time.sleep(STEP_SETTLE)
        after = _look_frame(win)
        if before is not None and after is not None and _frames_match(before, after):
            return [Did(label, sent=True, confirmed=False,
                        why="the estate map did not change, so the click hit "
                            "no emblem and whatever was selected before is "
                            "still selected - refusing to embark blind")]
        return [Did(label, sent=True, confirmed=True)]

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
                 embark: bool = True, min_party: Optional[int] = None,
                 veto=None) -> list:
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
    if here not in TOWN_SCREENS and act:
        # **Walk back before refusing.** Live 2026-09-28, twice: a town visit
        # ended on a screen the recogniser could not name (a panel left up, or
        # the Butcher's Circus behind the coach's scene point) and the embark
        # refused, throwing the whole iteration away. The frame is banked for
        # the record, then the same walk that leaves the results pages brings
        # the Hamlet back.
        note = _bank_panel_frame(win)
        say(f"  [town] embark: not in the Hamlet (screen: "
            f"{here or 'UNKNOWN'}){note} - walking back")
        walk(win, backend, until=lambda s: s in TOWN_SCREENS, say=say,
             act=act, buy=False)
        here = _look(win)
        # `walk` waits out a screen it cannot name and never clicks on it; an
        # unbanked panel is exactly that. Every building panel closes by the
        # same X, and nothing here is the bare Hamlet, so it is tried.
        for _try in range(2):
            if here in TOWN_SCREENS:
                break
            pt = resolved.town_close_panel
            if pt is None:
                break
            backend.click(pt)
            time.sleep(PANEL_OPEN_SETTLE)
            here = _look(win)
    if here not in TOWN_SCREENS:
        return [Did("embark setup",
                    why=f"not in the Hamlet (screen: {here or 'UNKNOWN'})")]

    # 2. Scout -- FIRST since 2026-09-24: the plan is a save read, and the
    #    Guild visit it drives has to happen in the Hamlet (below). The quests are in `persist.quest.json`, so this is a save read
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
                                      random.Random(seed), veto=veto)
    if plan is None:
        return done + [Did("scout the quests",
                           why="no simulable quest on the map")]
    for line in plan.report().splitlines():
        say(f"  [town] {line}")
    done.append(Did(f"scout: {plan.quest.label()} with {plan.comp}",
                    sent=False, confirmed=True, why="read from the save"))

    # 4-6 (moved). **The Guild is visited from the HAMLET.** It used to run
    # after seating, with the estate map still open -- where no building can
    # be clicked -- so every planned skill unlock failed as "could not confirm
    # the panel opened" (live 2026-09-24).
    done += _visit_guild(win, backend, resolved, profile, plan, say=say, act=act)
    # The comp's four skills on each seated hero (Fran, 2026-09-26), after
    # the Guild has bought what was missing.
    done += select_loadouts(win, backend, resolved, profile, plan, say=say, act=act)
    # Trinkets next, still in the Hamlet: the chest is on its bottom bar.
    done += equip_trinkets(win, backend, resolved, profile, plan,
                           say=say, act=act)
    if act and _look(win) not in TOWN_SCREENS:
        from bridge import act as act_mod            # noqa: PLC0415
        _send(backend, act_mod.plan_close_building(resolved), act=act, say=say)

    # 1. Hamlet -> the estate map, which is where the quest map, the party bar
    #    and the roster list are all drawn.
    got = walk(win, backend, until=lambda s: s == "estate_map", say=say,
               act=act, buy=False)
    done.append(Did("reach the estate map", sent=act, confirmed=got.ok,
                    why="" if got.ok else got.why or got.stopped_on))
    if not got.ok:
        return done

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

    # **THE DRAWN LIST, NOT THE SAVE.** See `drawn_roster`: a hero in the
    # party bar is not in the list, so a save index is not a list index and
    # converting one through the offset seats somebody else.
    # **RE-READ AFTER THE CLEAR, because clearing changes what is DRAWN.**
    # `town` was read before `clear_party`, when the heroes about to be seated
    # were still standing in the party bar -- and a hero in the bar is not in
    # the list (see `drawn_roster`). Indexing that snapshot dropped exactly
    # the heroes this loop is here to seat, which reported as "that hero is
    # not in the roster any more" for three of four (measured 2026-09-22).
    #
    # The save is the honest source: `clear_party` confirms each slot against
    # it, so by here it has been written. If the re-read fails, the stale
    # snapshot is still better than nothing and the seats that can be found
    # are still sent.
    try:
        town = read_town_state(profile)
    except Exception:                                # noqa: BLE001
        pass
    # **THE CLEAR IS THE TRUTH, NOT THE FILE.** `persist.roster.json` is
    # written late -- this repo's oldest live lesson -- so right after
    # `clear_party` its `in_party` flags still describe the bar as it WAS.
    # Trusting them dropped a hero from the drawn list who was no longer on
    # the bar, so nothing dragged him and the count came home 3 of 4 twice
    # over (measured 2026-09-22, highwayman#2 both times).
    #
    # `clear_party` has just emptied every slot it could, so the list draws
    # everybody again. That is what the seats are computed against.
    try:
        from dataclasses import replace as _replace   # noqa: PLC0415
        town = _replace(town, heroes=[_replace(h, in_party=False)
                                      for h in town.heroes])
    except Exception:                                # noqa: BLE001
        pass
    index_of = {h.guid: i for i, h in enumerate(drawn_roster(town))}
    already_seated = {h.guid for h in town.heroes
                      if getattr(h, "in_party", False)}
    # **Seated in ROSTER order, not rank order.** Which rank a hero lands in
    # is decided by the slot the drag ENDS on, so the order of the drags is
    # free -- and taking them in list order means the list is walked once
    # instead of scrolled back and forth. Four heroes at save rows 0, 2, 6 and
    # 11 cost one scroll this way and up to three the other.
    # **THE SEATS ARE COLLECTED, NOT SLICED OFF THE TAIL.** See the count
    # below: a scroll appends to `done` too, so `done[-4:]` is not four seats.
    seats: list = []
    # **ASCENDING, AND THE LIST IS TRACKED AS IT SHRINKS -- which is what
    # makes scrolling unnecessary.** DD moves a seated hero out of the roster
    # list into the party bar, so every row below them shifts up by one and a
    # once-computed index goes stale. That was the "3 of 4" which survived the
    # offset fix: measured 2026-09-22, all four seats reported OK and two of
    # them dragged the SAME hero, because a scroll at the list's bottom is a
    # no-op and the row was therefore unchanged.
    #
    # Taken in ascending order with the seated hero POPPED, indices only ever
    # decrease: the k-th seat sits at `index - k`. Four seats out of eleven
    # therefore never exceed row 7 -- the worst case is index 10 as the fourth
    # seat, 10 - 3 = 7 -- so the whole job fits inside the eight drawn rows
    # and no scroll is needed at all. That matters because a scroll cannot be
    # verified here: `_roster_offset` reads portrait art, and this install's
    # art is replaced by a UI mod.
    #
    # The list is popped rather than re-read because the save is written late:
    # asking the file after each drag would answer about the bar as it was.
    # `_bring_into_view` is still the fallback for a roster big enough that
    # the arithmetic above does not fit.
    # **ASK THE GAME WHICH ROW HOLDS WHOM.** Every way of inferring it is
    # broken here: the list order is not the save's, and the portrait matcher
    # that used to recover it compares against art a UI mod replaces. See
    # `learn_roster_rows` -- it drags each row onto the bar, reads the save to
    # see whose `in_party` turned on, and puts them back. Measured 2026-09-22
    # on Fran's roster, it recovered rows 1-7 in one pass and the answer was
    # nothing like the save order.
    # **Sort first, and the first page is usually enough.** Every planned
    # hero on page 0 means no whole-list walk at all.
    positions = sort_roster_low_stress_first(win, backend, resolved, profile,
                                             say=say, act=act)
    if not positions:
        positions = learn_roster_positions(win, backend, resolved, profile,
                                           say=say, act=act)
    row_of = {guid: row for guid, (clicks, row) in positions.items()
              if clicks == 0}
    live_rows = [h.guid for h in drawn_roster(town)]
    # The learning pass leaves the list scrolled DOWN, so a page-0 row is
    # only right after the list is rewound; `at_top` says whether it is.
    at_top = not positions
    for seat in sorted(plan.party,
                       key=lambda s: (positions.get(s.hero.guid, (0, 0))[0],
                                      index_of.get(s.hero.guid, 99))):
        label = f"seat {seat.hero.label()} at rank {seat.rank}"
        index = index_of.get(seat.hero.guid)
        if index is None and seat.hero.guid in already_seated:
            # **ALREADY ON THE BAR IS NOT MISSING.** A hero standing in the
            # party bar is not drawn in the roster list, so there is nothing
            # to drag them from -- and `clear_party` does not always empty
            # every slot ("the bar did not change" cannot tell an empty slot
            # from a failed drag). Refusing here cost the embark outright:
            # measured 2026-09-22, three of four seated and the fourth
            # reported as "not in the roster any more" while it was sitting
            # on the bar the whole time.
            #
            # The RANK may not be the one planned, which is the honest cost
            # of this and is said out loud rather than hidden -- a party on
            # the wrong ranks still raids, and a party of three does not go
            # at all.
            seats.append(Did(label, sent=False, confirmed=True,
                             why="already on the party bar, so not dragged; "
                                 "its rank is whatever the bar already had"))
            done.append(seats[-1])
            continue
        if index is None:
            seats.append(Did(label, why="that hero is not in the roster any more"))
            done.append(seats[-1])
            continue
        # **A hero learned on a LATER page is reached by replaying the clicks
        # that revealed them, and the seat is checked against the save** --
        # the only witness that says WHO arrived rather than that something
        # moved. See `learn_roster_positions`.
        pos = positions.get(seat.hero.guid)
        if pos is not None and pos[0] > 0:
            label = f"seat {seat.hero.label()} at rank {seat.rank}"
            top = force_roster_to_top(win, backend, resolved, town,
                                      say=lambda _s: None, act=act)
            scrolled = scroll_roster_by(win, backend, resolved, pos[0],
                                        say=lambda _s: None, act=act)
            if not (top.confirmed and scrolled.confirmed):
                seats.append(Did(label, why="could not bring the page back"))
                done.append(seats[-1])
                continue
            at_top = False
            seat_hero(win, backend, resolved, pos[1], seat.rank,
                      say=say, act=act)
            time.sleep(ROSTER_SCROLL_SETTLE)
            arrived = seat.hero.guid in _in_party(profile)
            seats.append(Did(label, sent=True, confirmed=arrived,
                             why="" if arrived else
                             "a different hero arrived - the page drifted"))
            done.append(seats[-1])
            continue
        # The LEARNED row wins; the positional guess is only a fallback for a
        # hero the learning pass could not identify.
        row = row_of.get(seat.hero.guid)
        if row is None and positions:
            # **The list was LEARNED and this hero is not in it: do not
            # guess.** The positional guess sent two heroes to row 6 on
            # 2026-09-23 and the second drag merely moved the first.
            seats.append(Did(label, why="not found in the learned roster "
                                        "list - not guessing a row"))
            done.append(seats[-1])
            continue
        if row is None:
            try:
                row = live_rows.index(seat.hero.guid)
            except ValueError:
                row = None
        if row is not None and row < ROSTER_ROWS_VISIBLE and not at_top:
            force_roster_to_top(win, backend, resolved, town,
                                say=lambda _s: None, act=act)
            at_top = True
        if row is not None and row < ROSTER_ROWS_VISIBLE:
            # **A SEAT MUST MAKE THE BAR FULLER, and "the bar changed" does
            # not.** The roster list re-flows with an ANIMATION when a hero
            # leaves it, so a second drag aimed at the row the last hero
            # vacated can still grab that same hero -- who then MOVES from
            # one rank to another. The bar changes, the drag reports OK, and
            # the party is one short. Measured 2026-09-22: rows 3, 3, 6, 7
            # into ranks 3, 2, 4, 1, all four "OK", bar took 3 of 4.
            before_n = sum(1 for i in range(PARTY_RANKS)
                           if party_slot_filled(win, resolved, i))
            got = seat_hero(win, backend, resolved, row, seat.rank,
                            say=say, act=act)
            time.sleep(ROSTER_SCROLL_SETTLE)      # let the list re-flow
            after_n = sum(1 for i in range(PARTY_RANKS)
                          if party_slot_filled(win, resolved, i))
            if got.confirmed and act and after_n != before_n + 1:
                got = Did(got.what, sent=True, confirmed=False,
                          why=f"the bar went {before_n} -> {after_n}, so that "
                              f"drag MOVED a hero already seated rather than "
                              f"adding one - the list had not re-flowed yet")
            elif got.confirmed and act and positions                     and seat.hero.guid not in _in_party(profile):
                # **WHO arrived, not just that someone did.** With the order
                # predicted rather than probed row by row, the save is the
                # check that the row held the planned hero.
                got = Did(got.what, sent=True, confirmed=False,
                          why="a different hero arrived - the predicted row "
                              "was wrong")
            seats.append(got)
            done.append(got)
            # **NO POP: the estate-map list does NOT re-flow.** Measured
            # 2026-09-22 -- a seated hero STAYS in the list at the same row
            # and gains a red marker, and dragging that row again grabs the
            # same hero and merely MOVES them between ranks, which is why the
            # bar sat at 1 through three further drags. The 33.7 change in
            # the row's patch after a seat was the marker appearing, not a
            # different hero arriving.
            continue
        # Too far down for the drawn rows: fall back to scrolling, which is
        # only reachable on a roster larger than the arithmetic above covers.
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
        got = seat_hero(win, backend, resolved, index - offset, seat.rank,
                        say=say, act=act)
        seats.append(got)
        done.append(got)
        if got.confirmed and row is not None:
            live_rows.pop(row)

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

    # 4-6. The Guild ran in the Hamlet, before the estate map (see above).

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
    # **THE MISSING SCENE POINT IS NO LONGER A REFUSAL.** It used to return
    # here, which meant every skill the embark plan had already budgeted for
    # was skipped and the party embarked carrying a comp it had not been
    # given. `act.plan_open_building` now opens the stage coach -- the one
    # measured scene point -- and hops along the navigation strip, which was
    # verified live on 2026-09-21.
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

    # **THE TREE IS NOT DRAWN UNTIL A HERO IS IN THE SLOT.** The panel opens
    # on "Drag a hero from the roster here", and every skill row -- and so
    # every buy button -- appears only once one is dropped. Measured live
    # 2026-09-21: the panel was open, the crest tree was up, and the seven
    # skill rows did not exist until Dismas was dragged in.
    in_slot = None
    cursor: dict = {}
    for hero, skill in unlocks:
        before = _wallet(profile)
        row = _guild_row(hero.hero_class, skill)
        if row is None:
            done.append(Did(f"unlock {hero.hero_class}.{skill}",
                            why="that skill is not in the class's tree"))
            continue
        if in_slot != getattr(hero, "guid", id(hero)):
            put = _put_hero_in_guild(win, backend, resolved, profile, hero,
                                     say=say, act=act, cursor=cursor)
            done.append(put)
            if not put.confirmed:
                continue
            in_slot = getattr(hero, "guid", id(hero))
        sent = _send(backend, act_mod.plan_buy_skill(row, resolved),
                     act=act, say=say)
        after = _await(lambda: _wallet(profile), before) if sent else before
        done.append(Did(f"unlock {hero.hero_class}.{skill}", sent=sent,
                        confirmed=sent and after != before,
                        why="" if after != before else "the wallet did not move"))
    _send(backend, act_mod.plan_close_building(resolved), act=act, say=say)
    return done


def _put_hero_in_guild(win, backend, resolved, profile, hero, *, say=print,
                       act: bool = False,
                       cursor: Optional[dict] = None) -> "Did":
    """Drag one hero into the Guild panel's slot so their tree is drawn.

    **The roster list is SCROLLED and the save does not say so**, which is
    why this goes through `_roster_offset` rather than using the save index:
    a wrong offset does not miss, it drags a DIFFERENT hero, and the panel
    then happily draws that hero's tree for a purchase meant for someone
    else. Refusing is the cheaper failure.
    """
    from bridge import townlayout as TL                # noqa: PLC0415
    from bridge.town_state import read_town_state      # noqa: PLC0415

    label = f"put {hero.hero_class} in the guild"
    try:
        town_now = read_town_state(profile)
    except Exception as exc:                           # noqa: BLE001
        return Did(label, why=f"could not read the roster ({exc})")
    guid = getattr(hero, "guid", None)
    save_row = next((i for i, h in enumerate(town_now.heroes)
                     if getattr(h, "guid", None) == guid), None)
    if save_row is None:
        return Did(label, why="that hero is not on the roster any more")
    # **The list IS the save's order** (`predicted_order`, measured live
    # 2026-09-24), so the hero's place is `save_row` among the living, and
    # the portrait matcher -- broken by Fran's UI mods -- is not needed. The
    # list is rewound to the top and scrolled down to that place, the same
    # way the seating reaches a later page.
    if act:
        screen_row, why = _roster_row_for(win, backend, resolved, town_now,
                                          save_row, act=act, cursor=cursor)
        if screen_row is None:
            return Did(label, why=why)
    else:
        screen_row = min(save_row, ROSTER_ROWS_VISIBLE - 1)
    src_pt = resolved.at(TL.roster_portrait(screen_row))
    dst_pt = resolved.at(TL.GUILD_HERO_SLOT)
    if src_pt is None or dst_pt is None:
        return Did(label, why=f"no point for roster row {screen_row}")
    say(f"  [town] drag {hero.hero_class} (save row {save_row}, screen row "
        f"{screen_row}) into the guild")
    if not act:
        return Did(label, sent=False, confirmed=False, why="not armed")
    sent = bool(backend.drag(src_pt, dst_pt))
    time.sleep(PANEL_OPEN_SETTLE)
    return Did(label, sent=sent, confirmed=sent)


#: A hero's Equipment slot counts as CHANGED by a drag when its box differs
#: from the frame before by more than this mean per-channel difference. An
#: empty slot is a dark bag outline; a trinket is a full-colour card.
TRINKET_SLOT_CHANGED = 12.0


def _slot_box_diff(before, after, resolved, frac) -> Optional[float]:
    """Mean absolute difference over one Equipment slot's box, or None."""
    try:
        import numpy as np                           # noqa: PLC0415
        pt = resolved.at(frac)
        cx, cy = resolved.win.client[0], resolved.win.client[1]
        x, y = pt[0] - cx, pt[1] - cy
        h, w = before.shape[:2]
        dx, dy = max(8, int(22 * w / 1280)), max(16, int(44 * h / 720))
        a = before[max(0, y - dy):y + dy, max(0, x - dx):x + dx].astype(int)
        b = after[max(0, y - dy):y + dy, max(0, x - dx):x + dx].astype(int)
        return float(np.abs(a - b).mean()) if a.size and a.shape == b.shape else None
    except Exception:                                # noqa: BLE001
        return None


def _roster_row_for(win, backend, resolved, town_now, save_row: int, *,
                    act: bool, cursor: Optional[dict] = None):
    """Bring save row `save_row` into view; `(screen_row, "")` or
    `(None, why)`. See `_open_sheet_with_chest` for what `cursor` saves."""
    last = ROSTER_ROWS_VISIBLE - 1
    known = cursor.get("offset") if cursor else None
    if known is not None:
        # BOTH frames, and unchanged: `_bar_moved` says False when it cannot
        # see, and "cannot see" must send this back to the rewind.
        was, now = cursor.get("strip"), _roster_strip(win, resolved)
        if was is None or now is None or _bar_moved(was, now):
            known = None
    if known is not None and save_row >= known:
        if save_row > known + last:
            scrolled = scroll_roster_by(win, backend, resolved,
                                        save_row - last - known,
                                        say=lambda _s: None, act=act)
            if not scrolled.confirmed:
                return None, "could not scroll to the hero"
            known = save_row - last
        screen_row = save_row - known
    else:
        top = force_roster_to_top(win, backend, resolved, town_now,
                                  say=lambda _s: None, act=act)
        if not top.confirmed:
            return None, "could not rewind the roster list"
        known = 0
        if save_row > last:
            scrolled = scroll_roster_by(win, backend, resolved, save_row - last,
                                        say=lambda _s: None, act=act)
            if not scrolled.confirmed:
                return None, "could not scroll to the hero"
            known = save_row - last
        screen_row = save_row - known
    if cursor is not None:
        cursor["offset"] = known
        cursor["strip"] = _roster_strip(win, resolved)
    return screen_row, ""


def _open_sheet_with_chest(win, backend, resolved, town_now, guid, *,
                           act: bool, cursor: Optional[dict] = None) -> str:
    """Scroll to a hero, right-click their portrait, open the chest.

    Returns "" on success, else why not. The sheet and the Trinket Inventory
    then sit side by side, which is where every drag below happens.

    **`cursor` saves the rewind.** A full rewind plus a counted scroll per
    hero, at ~1.5 s a row on a 60-hero roster, made a town visit take twenty
    minutes (live 2026-09-25). After one rewind the offset is KNOWN -- the
    same counted trust `scroll_roster_by` already runs on -- so the next hero
    further down only scrolls the difference. It is reused only while the
    roster strip on screen still matches the one captured after the last
    scroll; anything that moved the list sends it back to the rewind.
    """
    from bridge import townlayout as TL                # noqa: PLC0415
    save_row = next((i for i, h in enumerate(town_now.heroes)
                     if getattr(h, "guid", None) == guid), None)
    if save_row is None:
        return "not on the roster any more"
    screen_row, why = _roster_row_for(win, backend, resolved, town_now,
                                      save_row, act=act, cursor=cursor)
    if screen_row is None:
        return why
    portrait = resolved.at(TL.roster_portrait(screen_row))
    chest = resolved.at(TL.TRINKET_CHEST_BUTTON)
    if portrait is None or chest is None:
        return "no point for the portrait or the chest"
    backend.right_click(portrait)
    time.sleep(PANEL_OPEN_SETTLE)
    backend.click(chest)
    time.sleep(PANEL_OPEN_SETTLE)
    return ""


def _close_sheet_and_chest(backend, resolved) -> None:
    """The inventory's X, then the sheet's. The game saves on the close."""
    from bridge import townlayout as TL                # noqa: PLC0415
    for frac in (TL.TRINKET_PANEL_CLOSE, TL.HERO_SHEET_CLOSE):
        pt = resolved.at(frac)
        if pt is not None:
            backend.click(pt)
            time.sleep(PANEL_OPEN_SETTLE)


#: Classes with no 4-of-7 choice (`env/expedition_env.UNRESTRICTED_HEROES`,
#: restated here so the bridge does not import the training env).
UNRESTRICTED_LOADOUT = frozenset({"abomination", "duelist"})


def loadout_toggles(order, selected, unlocked, want, size: int = 4):
    """Which sheet icons to click to wear `want`: `(off, on, target)`.

    `order` is the class's skill order (the sheet's), `selected` what the
    hero wears now, `unlocked` what they own. The target is the wanted skills
    they own, topped up to `size` with what they already wear, so a hero
    missing a comp skill keeps a full bar. Deselections go first: a fifth
    selection is refused by the game."""
    own = set(unlocked)
    target = [s for s in want if s in own][:size]
    for s in selected:
        if len(target) >= size:
            break
        if s not in target and s in own:
            target.append(s)
    off = [order.index(s) for s in selected if s not in target and s in order]
    on = [order.index(s) for s in target if s not in selected and s in order]
    return off, on, target


def select_loadouts(win, backend, resolved, profile, plan, *, say=print,
                    act: bool = False) -> list:
    """Make each seated hero wear the comp's four skills before embarking.

    Fran, 2026-09-26: *"choose their optimal loadout skills for the comp and
    make them use it before embarking"*. Until now the party left with
    whatever was last selected. Right-click the roster portrait for the
    sheet, click the icons `loadout_toggles` names, close; the SAVE is the
    witness (it takes the selection on the close)."""
    from bridge import townlayout as TL                # noqa: PLC0415
    from bridge.town_state import read_town_state      # noqa: PLC0415
    from data.hero_skills import unlocked              # noqa: PLC0415
    from data.town_costs import load_hero_costs        # noqa: PLC0415
    from bridge.townplan import GAME_DIR               # noqa: PLC0415

    done: list = []
    seats = [s for s in (getattr(plan, "party", ()) or ()) if s.skills]
    if not seats:
        return done
    try:
        town_now = read_town_state(profile)
    except Exception as exc:                           # noqa: BLE001
        return [Did("select loadouts", why=f"could not read the roster ({exc})")]
    by_guid = {h.guid: h for h in town_now.heroes}
    rows = {h.guid: i for i, h in enumerate(town_now.heroes)}
    cursor: dict = {}
    for seat in sorted(seats, key=lambda st: rows.get(st.hero.guid, 0)):
        hero = by_guid.get(seat.hero.guid)
        what = f"loadout {seat.hero.label()}"
        if hero is None:
            done.append(Did(what, why="not on the roster any more"))
            continue
        # The Abomination and the Duelist wear all seven, always; a mode
        # decides which are usable (live 2026-09-27: the save "wears" seven).
        if hero.hero_class in UNRESTRICTED_LOADOUT:
            continue
        costs = load_hero_costs(str(GAME_DIR), hero.hero_class)
        order = list(costs.skills) if costs is not None else []
        have = unlocked(profile, hero.hero)
        selected = list(getattr(hero.hero, "selected_skills", None) or [])
        off, on, target = loadout_toggles(order, selected, have, seat.skills)
        if not off and not on:
            continue
        say(f"  [town] {what}: wear {', '.join(target)} "
            f"(was {', '.join(selected) or 'nothing'})")
        if not act:
            done.append(Did(what, sent=False, why="not armed"))
            continue
        screen_row, why = _roster_row_for(win, backend, resolved, town_now,
                                          rows[hero.guid], act=act, cursor=cursor)
        if screen_row is None:
            done.append(Did(what, why=why))
            continue
        backend.right_click(resolved.at(TL.roster_portrait(screen_row)))
        time.sleep(PANEL_OPEN_SETTLE)
        for i in off + on:
            backend.click(resolved.at(TL.sheet_skill(i)))
            time.sleep(0.3)
        backend.click(resolved.at(TL.HERO_SHEET_CLOSE))
        time.sleep(PANEL_OPEN_SETTLE)
        try:
            now = next((h for h in read_town_state(profile).heroes
                        if h.guid == hero.guid), None)
        except Exception:                              # noqa: BLE001
            now = None
        got = set(getattr(getattr(now, "hero", None), "selected_skills", None) or [])
        ok = got == set(target)
        done.append(Did(what, sent=True, confirmed=ok,
                        why="" if ok else f"the save wears {sorted(got)}"))
    return done


def reset_trinkets(win, backend, resolved, profile, *, say=print,
                   act: bool = False) -> list:
    """Unequip every hero's trinkets and sort the chest by rarity.

    Fran, 2026-09-26: *"Unequip all: before every embark, to know the whole
    pool"*. One button does what the benched-strip pass did a drag at a time,
    and the sort makes the chest's order a known function of the trinkets
    (rarity rank, then display name -- measured 2026-09-27) that the game
    also writes back to the save on the close. Witnessed by the save: nobody
    wears anything afterwards.
    """
    from bridge import townlayout as TL                # noqa: PLC0415
    from bridge.layout import MODAL_OK                 # noqa: PLC0415
    from bridge.town_state import read_town_state      # noqa: PLC0415
    label = "unequip all trinkets and sort by rarity"
    if not act:
        say(f"  [town] WOULD {label}")
        return [Did(label, sent=False, why="not armed")]
    for frac in (TL.TRINKET_CHEST_BUTTON, TL.TRINKET_UNEQUIP_ALL, MODAL_OK,
                 TL.TRINKET_SORT_RARITY, TL.TRINKET_PANEL_CLOSE):
        pt = resolved.at(frac)
        if pt is None:
            return [Did(label, why="no point for a chest control")]
        backend.click(pt)
        time.sleep(PANEL_OPEN_SETTLE)
    try:
        worn = sum(1 for h in read_town_state(profile).heroes
                   for t in (getattr(h.hero, "trinkets", None) or []) if t)
    except Exception as exc:                           # noqa: BLE001
        return [Did(label, sent=True, why=f"could not re-read the roster ({exc})")]
    say(f"  [town] {label}: {worn} still worn")
    return [Did(label, sent=True, confirmed=worn == 0,
                why="" if worn == 0 else f"{worn} trinket(s) still worn")]


def equip_trinkets(win, backend, resolved, profile, plan, *, say=print,
                   act: bool = False, strip: bool = True) -> list:
    """`reset_trinkets` first, then `_equip_trinkets` on the whole pool. The
    benched-strip pass stays as the fallback when the reset is unconfirmed."""
    pre: list = []
    cells = None
    if strip and act:
        pool = _trinket_pool(profile)
        pre = reset_trinkets(win, backend, resolved, profile, say=say, act=act)
        if pre and pre[-1].confirmed:
            strip = False
            # **The chest's order is COMPUTED, not read.** Right after an
            # unequip-all the estate save still lists the chest as it was
            # before the heroes' trinkets came back, and a whole party was
            # equipped one cell off from it (live 2026-09-27). The rarity sort
            # is a function of the pool (`data/trinket_order.py`, 37 of 37
            # cells over two chests), so the cells are that function.
            if pool is not None:
                from data.trinket_order import rarity_sorted  # noqa: PLC0415
                cells = dict(enumerate(rarity_sorted(pool)))
    return pre + _equip_trinkets(win, backend, resolved, profile, plan,
                                 say=say, act=act, strip=strip, cells=cells)


def _trinket_pool(profile):
    """Every trinket the estate owns: the chest plus what heroes wear."""
    from bridge.town_state import (                    # noqa: PLC0415
        find_profile, read_town_state, read_trinket_cells)
    try:
        chest = list(read_trinket_cells(
            find_profile(str(profile) if profile else None)
            / "persist.estate.json").values())
        worn = [t for h in read_town_state(profile).heroes
                for t in (getattr(h.hero, "trinkets", None) or []) if t]
    except Exception:                                  # noqa: BLE001
        return None
    return chest + worn


def _equip_trinkets(win, backend, resolved, profile, plan, *, say=print,
                    act: bool = False, strip: bool = True,
                    cells: Optional[dict] = None) -> list:
    """Fill the party's EMPTY trinket slots with what the team-builder picks.

    Fran (2026-09-25): *"No se equipa ningun tipo de trinket"*. The decision
    is `bridge/trinketplan.py` (the dd-team-builder's `reequipParty`); this is
    only the hands, measured live the same night: the bottom-bar chest opens
    the Trinket Inventory, a right-click on a roster portrait opens the
    hero's sheet beside it, and a drag from a cell to an Equipment slot
    equips. What a party hero already wears is left alone -- this fills, it
    never strips the party -- and every drag is checked on the SCREEN.

    **Benched heroes are the chest's other half.** The loop picks a new party
    most raids, so a trinket equipped last time sits on someone staying home.
    The planner is offered those too; any it picks is first dragged off its
    benched wearer (the panels then close, which saves), and the equip pass
    runs again on the re-read estate with `strip=False`.
    """
    from bridge import townlayout as TL                # noqa: PLC0415
    from bridge.town_state import (                    # noqa: PLC0415
        find_profile, read_town_state, read_trinket_cells)
    from bridge.trinketplan import plan_trinkets       # noqa: PLC0415

    label = "equip trinkets"
    try:
        town_now = read_town_state(profile)
        if cells is None:
            cells = read_trinket_cells(
                find_profile(str(profile) if profile else None)
                / "persist.estate.json")
    except Exception as exc:                           # noqa: BLE001
        return [Did(label, why=f"could not read the estate ({exc})")]
    seats = list(getattr(plan, "party", ()) or ())
    party = {s.hero.guid for s in seats}
    # (hero, list position, id) for everything a benched hero wears.
    # The SLOT, not the list position: live 2026-09-26 a Plague Doctor wore
    # speed_stone in slot 1 with slot 0 empty, the drag took slot 0, and the
    # save rightly said the trinket was still on him.
    benched = [] if not strip else [
        (h, i, t) for h in town_now.heroes if h.guid not in party
        for i, t in (sorted((getattr(h.hero, "trinket_slots", None) or {}).items())
                     or list(enumerate(getattr(h.hero, "trinkets", None) or [])))
        if t]
    if not cells and not benched:
        return [Did(label, why="no trinkets in the inventory")]
    # **Only what the grid DRAWS is offered.** Three rows of seven are
    # visible without the chest's scroll, which is not measured; live
    # 2026-09-27 two picks sorted to cell 24 and could not be reached. A cell
    # is its rank among the chest's trinkets, so the first 21 are the drawn.
    visible = TL.TRINKET_GRID_COLS * TL.TRINKET_GRID_ROWS_VISIBLE
    cells = dict(sorted(cells.items())[:visible])
    wanted = plan_trinkets([(s.hero.hero_class, list(s.skills)) for s in seats],
                           list(cells.values()) + [t for _h, _i, t in benched])
    if not wanted:
        return [Did(label, why="the team-builder proposed nothing "
                               "(or node is unavailable)")]
    free = dict(cells)                                 # cell -> id, shrinking
    loose = list(benched)                              # benched, shrinking
    jobs = []                                          # (seat, held, [(cell, id)])
    strips = []                                        # (hero, position, id)
    for seat, picks in zip(seats, wanted):
        # What the hero wears NOW, from the save read above -- not the embark
        # plan's snapshot, which predates the unequip-all (live 2026-09-27:
        # an Arbalest "still held" a survival guide the reset had taken off,
        # so only one of two slots was filled).
        now_hero = next((h for h in town_now.heroes
                         if h.guid == seat.hero.guid), seat.hero)
        held = [t for t in (getattr(now_hero.hero, "trinkets", None) or []) if t]
        room = 2 - len(held)
        chosen = []
        for tid in picks:
            if room <= 0:
                break
            if tid in held:
                continue
            cell = next((c for c, t in sorted(free.items()) if t == tid), None)
            if cell is not None:
                chosen.append((cell, tid))
                del free[cell]
                room -= 1
                continue
            worn = next((w for w in loose if w[2] == tid), None)
            if worn is not None:
                strips.append(worn)
                loose.remove(worn)
                room -= 1
        if chosen:
            jobs.append((seat, held, chosen))
    if strips:
        for hero, _i, tid in strips:
            say(f"  [town] TRINKET take {tid} off benched {hero.hero_class}")
        if not act:
            return [Did(label, sent=False, why="not armed")]
        done = _strip_trinkets(win, backend, resolved, profile, town_now,
                               len(cells), strips, say=say, act=act)
        return done + equip_trinkets(win, backend, resolved, profile, plan,
                                     say=say, act=act, strip=False)
    if not jobs:
        return [Did(label, why="every party slot is already filled or no pick "
                               "is in the inventory")]
    for seat, held, chosen in jobs:
        say(f"  [town] TRINKET {seat.hero.hero_class}: "
            + ", ".join(f"{t} (cell {c})" for c, t in chosen))
    if not act:
        return [Did(label, sent=False, why="not armed")]

    from bridge import screen as screen_mod           # noqa: PLC0415
    done: list = []
    left = set(cells)                                  # save keys still in the chest
    cursor: dict = {}
    rows = {h.guid: i for i, h in enumerate(town_now.heroes)}
    jobs.sort(key=lambda j: rows.get(j[0].hero.guid, 0))
    for seat, held, chosen in jobs:
        what = f"equip {seat.hero.hero_class}"
        why = _open_sheet_with_chest(win, backend, resolved, town_now,
                                     seat.hero.guid, act=act, cursor=cursor)
        if why:
            done.append(Did(what, why=why))
            continue
        slot_i = len(held)
        # **The grid COMPACTS when it is reopened.** Within one opening a
        # taken trinket leaves a hole, but closing and reopening the panel
        # packs what is left to the front. Live 2026-09-25 the second hero
        # was handed the trinket one cell to the right of the plan. So a
        # cell's place is its rank among the trinkets still there when the
        # panel opened, and `left` is only shrunk after the panel closes.
        at_open = sorted(left)
        taken = []
        for key, tid in chosen:
            cell = at_open.index(key)
            if cell >= TL.TRINKET_GRID_COLS * TL.TRINKET_GRID_ROWS_VISIBLE:
                done.append(Did(f"{what} {tid}",
                                why=f"cell {cell} is below the visible grid"))
                continue
            dst_frac = TL.HERO_TRINKET_SLOTS[min(slot_i, 1)]
            src, dst = resolved.at(TL.trinket_cell(cell)), resolved.at(dst_frac)
            before = screen_mod.grab(win)
            sent = bool(backend.drag(src, dst))
            time.sleep(PANEL_OPEN_SETTLE)
            after = screen_mod.grab(win)
            diff = (_slot_box_diff(before, after, resolved, dst_frac)
                    if before is not None and after is not None else None)
            ok = sent and diff is not None and diff > TRINKET_SLOT_CHANGED
            done.append(Did(f"{what} {tid}", sent=sent, confirmed=ok,
                            why=f"slot changed by {diff:.1f}" if diff is not None
                                else "no frame to check"))
            if ok:
                slot_i += 1
                taken.append(key)
        left.difference_update(taken)
        _close_sheet_and_chest(backend, resolved)
    # **The save says what each hero ended up wearing**, and a slot that
    # changed on screen is not proof it was the RIGHT trinket.
    try:
        worn = {h.guid: sorted(getattr(h.hero, "trinkets", None) or [])
                for h in read_town_state(profile).heroes}
    except Exception:                                  # noqa: BLE001
        worn = None
    if worn is not None:
        for seat, held, chosen in jobs:
            want = sorted(list(held) + [t for _c, t in chosen])
            got = worn.get(seat.hero.guid, [])
            ok = got == want
            done.append(Did(f"trinkets on {seat.hero.hero_class}", sent=True,
                            confirmed=ok,
                            why="" if ok else f"wears {got}, planned {want}"))
    return done


def _strip_trinkets(win, backend, resolved, profile, town_now, in_chest: int,
                    strips, *, say=print, act: bool = False) -> list:
    """Drag trinkets off benched heroes into the chest's first empty cells.

    The save does not keep a hero's slot KEYS (`live_state._item_ids` lists
    ids), so the slot is the id's list position -- right for a hero whose
    slots fill from the left, which is how `equip_trinkets` fills them. The
    witness is the save re-read after the close, not a guess about the slot.
    """
    from bridge import townlayout as TL                # noqa: PLC0415
    from bridge.town_state import read_town_state      # noqa: PLC0415
    by_hero: dict = {}
    for hero, pos, tid in strips:
        by_hero.setdefault(hero.guid, (hero, []))[1].append((pos, tid))
    sent_for: list = []                                # (guid, class, id, sent)
    failed: list = []
    dropped = 0
    cursor: dict = {}
    rows = {h.guid: i for i, h in enumerate(town_now.heroes)}
    for guid, (hero, items) in sorted(by_hero.items(),
                                      key=lambda kv: rows.get(kv[0], 0)):
        why = _open_sheet_with_chest(win, backend, resolved, town_now, guid,
                                     act=act, cursor=cursor)
        if why:
            failed.append(Did(f"strip {hero.hero_class}", why=why))
            continue
        # Right slot first, so taking it cannot shift the left one.
        for pos, tid in sorted(items, reverse=True):
            cell = in_chest + dropped
            if cell >= TL.TRINKET_GRID_COLS * TL.TRINKET_GRID_ROWS_VISIBLE:
                failed.append(Did(f"strip {tid}",
                                  why="the chest's visible grid is full"))
                continue
            sent = bool(backend.drag(resolved.at(TL.HERO_TRINKET_SLOTS[min(pos, 1)]),
                                     resolved.at(TL.trinket_cell(cell))))
            time.sleep(PANEL_OPEN_SETTLE)
            dropped += 1 if sent else 0
            sent_for.append((guid, hero.hero_class, tid, sent))
        _close_sheet_and_chest(backend, resolved)
    try:
        worn = {h.guid: list(getattr(h.hero, "trinkets", None) or [])
                for h in read_town_state(profile).heroes}
    except Exception:                                  # noqa: BLE001
        worn = None
    done = list(failed)
    for guid, cls, tid, sent in sent_for:
        ok = sent and worn is not None and tid not in worn.get(guid, [])
        done.append(Did(f"strip {tid} off {cls}", sent=sent, confirmed=ok,
                        why="" if ok else "the save still has it on the hero"))
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
