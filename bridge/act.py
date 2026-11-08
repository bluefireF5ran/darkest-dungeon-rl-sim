"""Layer 3: turn an `Update` into keystrokes and clicks. Dry-run by default.

    python bridge/act.py --probe                 # which backend can drive the game
    python bridge/act.py --replay <capture-dir>  # plan every captured turn, send nothing
    python bridge/act.py --demo                  # show a plan for a synthetic turn

Layer 3 was left unbuilt on purpose for the whole project ("automating clicks
fixes nothing if the recommendation is wrong"). The recommendation is now
measured -- 84% MATCH over real captured turns -- so the reason has expired.
The caution has not, which is why **nothing here sends anything unless a caller
explicitly asks for a live backend**, and why the default is a plan you can read.

## What made this small

Two findings, both from the game's own data rather than from guesswork.

`localization/miscellaneous.string_table.xml` documents DD's PC hotkeys:

    1/2/3/4/5   quick select a skill for the current hero
    A / D       move left / right
    T           use a torch
    SPACE       take ALL when the LOOT window is up

The `1..5` numbering is exactly `bridge/overlay_slots.py`'s (`MOVE_SLOT = 5`)
and exactly what `run.py::advised_button` already computes. So **a combat turn
is one keystroke plus one click** -- press the digit, click the target rank --
and the eight rank points are already calibrated. Movement, torch and loot
become pure keystrokes needing no coordinates at all, which is most of the gap
`bridge/overlay.py` left open (it has pixels for combat only).

Second, `bridge/window.py` + `bridge/layout.py` put every coordinate in the
game's CLIENT rect as a fraction, so a windowed game works and can be moved
mid-fight.

## Rules that are not preferences

- **A provisional update is never actuated.** `run.py` looks ahead past pending
  enemy turns so the screen is not blank through ~17 s of no save write. On
  screen that is a greyed ring; here it would be *playing a turn that is not
  due*. `plan()` refuses on `provisional`, and separately whenever an enemy
  that can reposition is still to act.
- **An unmodelled screen pauses, it never guesses.** The bridge understands
  combat, movement, curios, camp, torch and retreat -- not loot popups,
  level-up, the results screen or any dialog. On anything it does not
  recognise the actuator stops acting and says so. Every pause names the next
  screen worth modelling, which is the opposite of a blind click-through.
- **Refusing is a first-class outcome.** `InputPlan.refusal` carries why, and
  `--replay` reports refusal rates per reason. A planner that silently emitted
  nothing would be indistinguishable from one that had nothing to do.
- **The save is the ACK, not a timer.** Movement in particular is
  self-correcting: `RoomAdvice.next_step` is an area id and the map graph is
  undirected, so nothing says whether a target is left or right. Press `D`,
  read `in_area`, reverse if it went the wrong way. See `plan_room`.
"""

from __future__ import annotations

import ctypes
import math
import sys
import time
from ctypes import wintypes as wt
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bridge.layout import MENU_DEAD_ZONE
from bridge.actionbar import rank_from_pattern
from bridge.overlay_slots import MOVE_SLOT, PASS_SLOT, RETREAT_SLOT

# Re-exported, not defined here: bridge/actuation/keys.py owns these.
from .actuation.keys import (  # noqa: F401
    ACK_TIMEOUT, BAR_SETTLE_FOR, BAR_SETTLE_STEP, BLIND_CANDIDATES,
    CURIO_APPROACH_SHOT, CURIO_NUDGE_HOLD, CURIO_PANEL_READS, CURIO_SHOT,
    CURIO_TRIES, DOOR_BACKOFF, DOOR_TRIES, DOOR_WAIT, DOOR_WAITS, DRAG_DWELL, DRAG_STEPS,
    DRAG_STEP_GAP, FOCUS_WAIT, INPUT, INPUT_KEYBOARD, INPUT_MOUSE,
    INVENTORY_SLOTS, KEYBDINPUT, KEYEVENTF_KEYUP, KEYEVENTF_SCANCODE,
    KEY_HOLD, MAPVK_VK_TO_VSC, MAP_CANDIDATES, MAP_POINT_TOLERANCE,
    MAP_REFIT_STEP, MAP_REFIT_TRIES, MAP_RESET_STEP, MAP_RESET_WATCH,
    MAP_SHOT, MAX_DEFERRALS, MAX_FREE_ACTIONS, MAX_WALK_STEPS, MK_LBUTTON,
    MK_SHIFT, MOD_DWELL, MOUSEEVENTF_ABSOLUTE, MOUSEEVENTF_LEFTDOWN,
    MOUSEEVENTF_LEFTUP, MOUSEEVENTF_MOVE, MOUSEEVENTF_RIGHTDOWN,
    MOUSEEVENTF_RIGHTUP, MOUSEEVENTF_VIRTUALDESK,
    MOUSEEVENTF_WHEEL, MOUSEINPUT, MOVE_CORRECTIONS, PACK_SETTLE,
    PANEL_WAIT, PARTY_CELL_WARM, ROOM_CURIO_TRIES, SEND_ATTEMPTS,
    SEND_SHOT, SETTLE, SETTLE_BEFORE_SEND, SKILL_VKS, SM_CXVIRTUALSCREEN,
    SM_CYVIRTUALSCREEN, SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN, STEP_GAP,
    SWAP_WAIT, SWAP_WAITS, TOOLTIP_FADE, TOOLTIP_MIN_HITS,
    TOOLTIP_SAMPLE_FOR, TOOLTIP_SAMPLE_STEP, TOOLTIP_SETTLE,
    TORCH_CLICK_GAP, TORCH_CONFIRM_READS, TORCH_MAX_CLICKS, TORCH_STEP,
    TURN_ACK_TIMEOUT, VK_1, VK_2, VK_3, VK_4, VK_5, VK_A, VK_CONTROL, VK_D,
    VK_ESCAPE, VK_KILL, VK_LCONTROL, VK_LSHIFT, VK_RETURN, VK_SHIFT,
    VK_SPACE, VK_T, VK_TAB, VK_W,
    WALK_HOLD, WALK_STALL_LIMIT, WHEEL_DELTA, WM_KEYDOWN, WM_KEYUP,
    WM_LBUTTONDOWN, WM_LBUTTONUP, WM_MOUSEMOVE, _INPUTunion, _POINT,
    _UNREAD, _cursor_pos, _err, _norm_axis, _virtual_desktop, kernel32,
    user32)


# ── the plan ──

# Re-exported, not defined here: bridge/actuation/plan.py owns these.
from .actuation.plan import (  # noqa: F401
    InputPlan, Step, _vk_name, refuse)


# ── planning: Update -> InputPlan (pure; no window, no sending) ──

def plan_one_move(cand, resolved, bar=None) -> InputPlan:
    """Plan ONE candidate move: press the skill digit, then click the rank.

    `cand.skill_slot` is the GAME's 1-based action-bar slot -- 1-4 the four
    equipped skills in save order, 5 MOVE, 6 the narrow X (PASS) -- computed by
    `run.py::advised_button` as `selected_skills.index(skill_id) + 1`. Going
    through the action-space index instead points at the wrong icon for any
    hero whose loadout is not the first four of their movepool, and a
    class-keyed lookup collapses a two-Highwayman party.

    Takes an `Update` or a `run.Alternative` -- both carry the same four
    fields, and this reads nothing else, which is what lets `plan_battle`
    retry it down the shortlist. Every refusal it returns is about the SCREEN
    (no such button, no calibrated point), never about the turn's timing;
    that separation is what makes a fallback safe.

    **`bar` is the game's own answer and it OVERRULES the model.** The save
    keeps the party's marching order and never records an in-combat swap, so
    `cand.skill_slot` can name a skill that is greyed out for the rank the
    hero is really standing in -- and the digit then selects nothing, the
    click lands on an empty selection, and the save never moves. A greyed slot
    is a fact about the SCREEN, so it refuses here and the shortlist walk in
    `plan_battle` finds a skill the game will accept. Pass `bar=None` to plan
    without looking, which is what every offline caller does.
    """
    slot = cand.skill_slot
    if bar is not None and 1 <= slot <= MOVE_SLOT and not bar.slot_lit(slot):
        # `waiting` separates the two ways a slot can be dark, and they want
        # different readings: a hero IS being asked and this skill is not one
        # of its options, against nobody being asked at all. `plan_battle`
        # catches the second before it gets here, so this is the fallback for
        # a caller that planned one move on its own.
        why = ("it is not legal from the rank this hero is really standing in"
               if bar.waiting else
               "the whole bar is dark - the game is not asking for input yet")
        return refuse(f"the game has slot {slot} greyed out - {why}", "battle")
    if slot == RETREAT_SLOT:
        return plan_retreat(resolved)
    if slot == PASS_SLOT:
        # No documented hotkey for pass; the X is a calibrated point.
        pt = resolved.skill_point(PASS_SLOT)
        if pt is None:
            return refuse("no calibrated PASS button", "battle")
        return InputPlan((Step("click", screen=pt, label="PASS (the X)"),),
                         "battle", "pass the turn")
    if not (1 <= slot <= MOVE_SLOT):
        return refuse(f"no action-bar slot for this advice (slot={slot}) - "
                      f"a provision is used from the inventory", "battle")

    steps = [Step("key", vk=SKILL_VKS[slot - 1],
                  label=f"select slot {slot}"
                        f"{' (MOVE)' if slot == MOVE_SLOT else ''}")]
    # **A MOVE IS INVISIBLE TO THE SAVE.** `persist.raid.json` keeps the
    # party's MARCHING order and never records an in-combat swap -- that is
    # why `bridge/actionbar.py` exists at all -- so a move that worked
    # perfectly produces no save write, and `_send_and_confirm` has been
    # reporting every one of them as "the input did not land". The turn is
    # then given up, the real formation drifts away from the save, and every
    # target computed afterwards is off. The witness is the bar: a hero at a
    # different rank can launch different skills.
    screen_witness = slot == MOVE_SLOT

    # MOVE still needs a destination rank clicked, same as a skill.
    if cand.side and cand.rank >= 0:
        pt = resolved.point(cand.side, cand.rank)
        if pt is None:
            return refuse(f"no calibrated point for {cand.side} rank "
                          f"{cand.rank + 1}", "battle")
        steps.append(Step("click", screen=pt,
                          label=f"{cand.side} rank {cand.rank + 1}"))
    else:
        return refuse("advice names no target rank to click", "battle")

    return InputPlan(tuple(steps), "battle",
                     cand.headline or f"slot {slot} on {cand.side} "
                                      f"rank {cand.rank + 1}",
                     continues_turn=bool(getattr(cand, "continues_turn",
                                                 False)),
                     screen_witness=screen_witness)


def torch_targets(resolved) -> tuple:
    """Every point that might be the torch widget, best guess first.

    The list lives in `layout.TORCH_CANDIDATES`, which is where the evidence
    for each one is written down. There is more than one because the two
    points measured off a screenshot were both refuted live and the shipped
    layout file does not say what its offsets are anchored to -- so the caller
    sends them in order and reads `torchlight` back from the save between,
    letting the game settle it.

    The two legacy properties are kept as the first two entries rather than
    resolved through `at()`, so a layout that predates the candidate list
    still aims somewhere.
    """
    from bridge.layout import TORCH_CANDIDATES               # noqa: PLC0415

    out = []
    for name, frac in TORCH_CANDIDATES:
        pt = resolved.at(frac)
        if pt is not None:
            out.append((name, pt))
    return tuple(out)


def plan_snuff_torch(resolved, point=None, label: str = "") -> InputPlan:
    """CTRL+SHIFT+click the torch dial: the FULL snuff, in one click.

    **MEASURED LIVE 2026-09-21 against DD's own light tooltip**, at the dial
    (the round number at the centre of the light bar), holding the modifiers
    `MOD_DWELL` before the click:

        plain +25   |   SHIFT -25   |   CTRL+SHIFT -100, `BLACK AS PITCH (0)`

    An earlier reading in the same session said CTRL+SHIFT was worth only -25
    and is RETRACTED: it was taken at a 0.12s dwell, where CTRL never reached
    SDL and the gesture degraded into the SHIFT one. The table above is the
    one taken at 0.35s, and it matches what Fran reported by hand.

    **The degraded forms are not weaker, they are OPPOSITE.** At the short
    dwell, SHIFT+click read as a plain click and RAISED the light 25 a time --
    a torch spent per click, in the middle of the fight the snuff exists to
    darken. That is the bug Fran caught live: *"the model didn't snuff torch,
    in fact, it increased torch before killing the last enemy just now"*.

    Fran, on the tutorial: snuffing the torch at the moment the killing blow
    lands gets much better loot. The game documents the gesture --
    `str_snuff_torch_tip` is the SHIFT+CTRL+CLICK one, and `str_reduce_torch_
    tip` is SHIFT+CLICK on the SAME widget for a partial reduction -- so both
    modifiers are named rather than assumed.

    **The timing is the whole play and it does not live here.** Darkness hands
    the monsters +12.5 accuracy, +25% damage and +5 crit, so a snuff is only
    free on the turn that ends the fight; `bridge/torchlight.py` refuses to
    recommend one otherwise and `run.py::_battle_torch` gates it on
    `about_to_win`. This only sends it.

    `layout.TORCH_METER` has been measured and deliberately unwired since it
    was banked. Its own comment says actuating a shutdown "means putting it in
    the BATTLE plan (snuff, then swing)" -- which is where `plan_battle` now
    puts it.
    """
    pt = point if point is not None else getattr(resolved, "torch_meter",
                                                 None)
    if pt is None:
        return refuse("no calibrated torch meter", "torch")
    return InputPlan((Step("click", screen=pt,
                           mods=(VK_LSHIFT, VK_LCONTROL),
                           label="snuff the torch"),),
                     "torch", "snuff the torch before the killing blow")


def plan_reduce_torch(resolved, clicks: int = 1, point=None) -> InputPlan:
    """SHIFT+click the torch dial, `clicks` times: 25 points down each.

    **It is the partial gesture, and `plan_snuff_torch` is the whole one** --
    CTRL+SHIFT takes the light to 0 in a single click, measured 2026-09-21.
    This stays for the case where a partial reduction is what is wanted.

    **The dwell is load-bearing.** At `MOD_DWELL` 0.12s this click read as an
    UNMODIFIED one and raised the light by 25 instead of lowering it, which is
    how a raid came to burn torches mid-fight. The constant is 0.35s now and
    the evidence is on it.

    The game documents both gestures on the same widget --
    `str_snuff_torch_tip` is SHIFT+CTRL+CLICK for a full snuff and
    `str_reduce_torch_tip` is SHIFT+CLICK for a partial reduction -- and this
    is the one that has been seen to work.

    **It is the fallback for the snuff, not a second feature.** Fran, live:
    "its not using ctrl+shift+click on the same time so it just lowers 25
    torch with shift click each click, so either we go 4 clicks to lower it or
    do something else". Whether CTRL reaches SDL through `SendInput` is not
    something this code can assert from here, so `ActRunner._snuff_torch`
    tries the one-click gesture, READS THE TORCH BACK, and comes here for the
    rest when it did not take.
    """
    from bridge import layout as layout_mod                # noqa: PLC0415

    pt = point if point is not None else getattr(resolved, "torch_meter",
                                                 None)
    if pt is None:
        return refuse("no calibrated torch meter", "torch")
    if not isinstance(clicks, int) or isinstance(clicks, bool) or clicks <= 0:
        return refuse("nothing to reduce", "torch")
    steps = tuple(Step("click", screen=pt, mods=(VK_LSHIFT,),
                       label=f"reduce the torch ({i + 1}/{clicks})",
                       after=TORCH_CLICK_GAP) for i in range(clicks))
    return InputPlan(steps, "torch", f"reduce the torch {clicks} step(s)")


#: How long DD takes to draw the retreat confirmation over the board.
#: `town.abandon_quest` uses 2.2 s for the same dialog raised from the same
#: control and has been reliable at it; this is that number.
RETREAT_SETTLE = 2.2


def plan_retreat(resolved) -> InputPlan:
    """Flee the combat: the raid HUD's X, then confirm.

    **The retreat control is the ABANDON-QUEST banner**, and the game says so
    rather than us inferring it -- `[pc]str_help_raid_combat_4` is *"[CLICK]
    on the retreat icon (looks like an "X") to attempt to retreat from the
    combat"*, and `str_help_raid_room_5` and `_hallway_7` name the same icon
    for abandoning the quest. One control, two meanings, decided by whether a
    fight is on.

    **That is exactly why this may only be planned inside a battle**, and it
    is the reason `plan_one_move` is the only route in: out of combat the same
    click throws the whole expedition away. `plan_battle` is the sole caller
    and it runs on the battle branch.

    Two steps, because the X only ASKS -- DD then draws "Are you sure you want
    to retreat?" with Yes on `MODAL_OK`, so the irreversible half is the
    confirm. Sending both in one plan keeps them atomic under the kill switch:
    an X with no confirm leaves a dialog sitting over the board swallowing
    every later input, which is the failure `_panel_is_open` exists to catch
    one layer up.
    """
    x = resolved.raid_abandon_quest
    ok = resolved.modal_ok
    if x is None or ok is None:
        return refuse("no calibrated retreat control", "battle")
    return InputPlan(
        (Step("click", screen=x, label="the retreat X",
              after=RETREAT_SETTLE),
         Step("click", screen=ok, label="confirm the retreat")),
        "battle", "retreat from this combat")


def plan_abandon_quest(resolved) -> InputPlan:
    """RETREAT from a raid whose objective is NOT met: the X, top left.

    **This is a different widget from `plan_leave_raid`'s seal, and which one
    works depends on whether the objective is met.** Measured 2026-09-21 with
    the quest COMPLETE: the scroll moved nothing over a six-second watch and
    the seal above it reached `raid_results` in two. Fran, on the retreat
    case: *"for retreat you need to click the X button on the top left"*.
    They sit 40 px apart -- the seal centres at (52, 50) in client pixels and
    the X banner under it at (52, 92) -- and they are not interchangeable.

    Unlike the seal this raises DD's own "Are you sure you want to retreat?"
    dialog, which `ActRunner._leave_raid` answers when it sees one. It is
    still ONE click here: the confirm is a response to a dialog appearing,
    never part of the plan.
    """
    x = getattr(resolved, "raid_abandon_quest", None)
    if x is None:
        return refuse("no calibrated abandon-quest banner", "raid")
    return InputPlan(
        (Step("click", screen=x, label="the abandon-quest X"),),
        "raid", "retreat - the survival layer priced pushing on as worse")


def plan_leave_raid(resolved) -> InputPlan:
    """Leave a raid from the HUD, with no quest-complete banner needed.

    **The control is the red WAX SEAL, not the scroll under it**, and getting
    that wrong cost a live raid. Fran: *"if you continue you can simply click
    the top left banner icon to end the raid at any time outside of combat"*
    -- and then, watching this click the scroll and achieve nothing, *"do not
    click the scroll, click the red symbol"*.

    Measured live 2026-09-21 with the quest complete: a click on
    `RAID_ABANDON_QUEST` (the scroll) moved nothing over a six-second watch;
    a click on `RAID_QUEST_SEAL` (the seal above it) reached `raid_results`
    within two seconds. They are 40 px apart and they are different widgets.

    **And it takes no confirm.** The results page comes straight up, so this
    sends ONE click. `plan_retreat`'s two-step shape does not apply here:
    that one is the in-combat retreat, which really does raise "Are you
    sure?". `ActRunner._leave_raid` still answers a modal if one appears,
    rather than assuming either way.

    **With the objective already met this banks the quest**, which is why the
    only caller is the raid-end branch -- out of combat, and only once
    `RoutePlan.targets` is empty. Sent with the objective unmet it would throw
    the expedition away, so the gate is the caller's and it is not optional.
    """
    seal = getattr(resolved, "raid_quest_seal", None)
    if seal is None:
        return refuse("no calibrated quest seal", "raid")
    return InputPlan(
        (Step("click", screen=seal, label="the quest banner's red seal"),),
        "raid", "leave the raid - the objective is met")


class _PassAdvice:
    """The minimum `plan_one_move` reads to plan a PASS."""
    skill_slot = PASS_SLOT
    side = ""
    rank = -1
    headline = "pass the turn"
    continues_turn = False


def plan_battle(update, resolved, bar=None) -> InputPlan:
    """A combat turn, falling down the advisor's shortlist if it has to.

    **The timing gates are checked ONCE and refuse outright.** Provisional
    advice, enemy turns still to resolve and a pending repositioner are all
    statements about the turn not being due yet; every candidate for that turn
    shares them, so walking the shortlist would only find a different way to
    act too early.

    **Everything below them is a statement about the SCREEN**, and those DO
    fall through. The policy ranks retreat first often enough, and the game
    draws no retreat control in the tutorial -- so the top pick had no button,
    `plan_battle` refused, `ActRunner` returned without marking the turn
    played, and the identical refusal republished on every poll for the rest
    of the run. It never misclicked; it stopped. Playing the next-best legal
    move is strictly better than standing still, and the plan says which
    position in the ranking it came from so the log never hides it.

    **`bar` closes the one deadlock the shortlist could not.** When the party
    has been reordered mid-fight -- a Move, an enemy shove, an afflicted
    `change_pos` -- the save still describes the marching order, so every
    candidate in the shortlist can be a skill this hero cannot perform from
    where it now stands. The bar says so, all four are refused, and the
    rescue below plays the one thing the game is still offering: the swap that
    puts the hero somewhere it can fight from.
    """
    if update.provisional:
        return refuse("provisional look-ahead - this turn is not due yet", "battle")
    if update.enemies_first:
        return refuse(f"{update.enemies_first} enemy turn(s) resolve first", "battle")
    if update.movers:
        return refuse(f"a pending enemy can reposition ({', '.join(update.movers)})",
                      "battle")

    # **A DARK BAR IS A TIMING FACT, not a legality one**, and it belongs up
    # here with the other three for the same reason: every candidate shares
    # it, so walking the shortlist can only find a different way to act while
    # the game is not listening.
    #
    # It used to fall through to `plan_one_move`, which refused each candidate
    # in turn with "slot N is greyed out - it is not legal from the rank this
    # hero is really standing in". True of the pixels and wrong about the
    # cause, and it cost real time on 2026-09-06: three provisional turns in
    # one log read `.##.##` while the save said two enemy turns were still to
    # come, which looked like the save being wrong about the timing and was
    # in fact the bar being read mid-animation. The next iteration, with the
    # settle test in `_read_bar` live, read `......` on all four. A refusal
    # that names the wrong cause sends the reader after the wrong bug.
    if bar is not None and not bar.waiting:
        # **TRANSIENT: the same turn key resolves this one.** The three gates
        # above are safe to mark spent because `turn_key` carries
        # `enemies_first`, so the moment those resolve the key is a different
        # one and the turn is planned again. A dark bar changes NOTHING about
        # the key -- the hero, the round and the turn count are all the same
        # the instant the game finishes animating -- so marking it spent drops
        # a live turn and leaves the hero waiting to be asked by a heartbeat.
        return InputPlan(
            layer="battle", transient=True,
            refusal="the game is not asking anyone for input right now "
                    "(the whole bar is dark)")

    # **THE SURVIVAL LAYER GETS A VOTE, AND IT HAD NONE.** `advise_retreat`
    # prices the whole rest of the dungeon, applies the death floor, and says
    # RETREAT -- and nothing carried that to the actuator, which sends the
    # combat POLICY's ranked pick. The two answer different questions, and
    # when they disagreed the policy won by default.
    #
    # Measured, live, 2026-09-06, on a real turn with the bar lit:
    #
    #   -> OPEN VEIN on rank 2 (Brigand Cutthroat, 5 hp)   76%
    #      alt: RETREAT on self  7%
    #   leaving costs ~5,889 gold, staying ~4,972 - RETREAT
    #   (the run loses somebody 67% of the time - over the 50% floor)
    #   [act] [battle] Highwayman: OPEN VEIN on rank 2
    #
    # It attacked. Four turns later both heroes were dead. This is Fran's
    # original complaint -- *"several deaths which I didn't really understand
    # why it went for that kind of play"* -- one layer further along than the
    # pricing bugs fixed on 2026-09-03: the number was right by then, and
    # nobody was listening to it.
    #
    # The retreat still has to be PLAYABLE and it is still the game that says
    # so: this walks the same shortlist through the same `plan_one_move`, so a
    # missing retreat control refuses exactly as any other missing button
    # does, and the ordinary top-pick path below runs untouched.
    # **...AND THE SHORTLIST IS NOT WHERE THE RETREAT HAS TO BE.** The scan
    # below was written against a live turn whose alternatives happened to
    # include `RETREAT on self  7%`, and it reads as though the shortlist were
    # the menu of legal actions. It is not -- it is the policy's top four, and
    # the policy is the half that has already been outvoted. Measured live
    # 2026-09-06, two combats from the end of a crypts raid:
    #
    #   -> BATTLEFIELD MEDICINE on rank 2 (Vestal)   99%
    #      alt: EMBOLDENING VAPOURS / MOVE to rank 3 / BATTLEFIELD MEDICINE
    #   2 combats left: P(finish intact) 33% +-14, expected deaths 0.7
    #   leaving costs ~4,333 gold, staying ~-2,031 - RETREAT
    #
    # Six polls in a row printed RETREAT and every one of them healed instead,
    # because no candidate carried `RETREAT_SLOT` and the loop fell straight
    # through. The verdict does not need the policy's permission to be acted
    # on, so `plan_retreat` is planned DIRECTLY when the shortlist has none.
    #
    # The game still gets the last word either way: `plan_retreat` refuses
    # without a calibrated control, exactly as any other missing button does.
    if getattr(update, "retreat_advised", False):
        said = "  [the survival layer says LEAVE and the policy did not "
        unranked = said + "rank it first]"
        unoffered = said + "offer it at all]"
        for cand in (update,) + tuple(getattr(update, "alternatives", ()) or ()):
            if getattr(cand, "skill_slot", 0) != RETREAT_SLOT:
                continue
            out = plan_one_move(cand, resolved, bar)
            if out.actionable:
                # No snuff: the party is leaving, and the torch is what makes
                # the walk out survivable.
                return InputPlan(out.steps, "battle", out.why + unranked,
                                 continues_turn=out.continues_turn,
                                 screen_witness=out.screen_witness)
            break
        else:
            out = plan_retreat(resolved)
            if out.actionable:
                return InputPlan(out.steps, "battle", out.why + unoffered,
                                 continues_turn=out.continues_turn,
                                 screen_witness=out.screen_witness)

    plan = plan_one_move(update, resolved, bar)
    if plan.actionable:
        return _with_snuff(update, plan, resolved)

    first_refusal = plan.refusal
    for i, alt in enumerate(getattr(update, "alternatives", ()) or (), start=2):
        fallback = plan_one_move(alt, resolved, bar)
        if not fallback.actionable:
            continue
        pct = f"{alt.confidence * 100:.0f}%"
        return _with_snuff(update, InputPlan(
            fallback.steps, "battle",
            f"{fallback.why}  [FALLBACK #{i} at {pct}: the top pick is "
            f"unplayable - {first_refusal}]",
            continues_turn=fallback.continues_turn,
            screen_witness=fallback.screen_witness), resolved)

    rescue = plan_reposition(update, resolved, bar)
    if rescue is not None:
        return rescue

    # Nothing in the shortlist has a control. Report the TOP pick's reason:
    # it is the one a reader is looking for, and the alternatives failed for
    # reasons that only matter once that one is understood.
    return refuse(f"{first_refusal} (and no alternative has one either)",
                  "battle")


def plan_reposition(update, resolved, bar) -> Optional[InputPlan]:
    """The hero cannot fight from where it stands. Swap it somewhere it can.

    Returns None unless the game itself says this is the situation: a bar that
    is `waiting` (MOVE or the X is lit, so a hero IS being asked to choose),
    with every skill greyed and MOVE offered. Guessing at it from the save is
    exactly the thing that cannot be done -- `party.heroes` is the marching
    order and an in-combat swap is never written to it.

    **The destination is derived from the drawn bar, not from the model.** The
    lit/greyed pattern of four skills is a 4-bit signature that `.launch`
    predicts exactly, so `rank_from_pattern` recovers where the hero really is;
    the target is then the NEAREST occupied rank it could launch something
    from. When the pattern is ambiguous -- a kit that launches from everywhere
    lights all four everywhere -- there is nothing to rescue, because such a
    hero is never stuck.

    A swap the game will not take costs one poll and nothing else: the turn is
    marked played either way, and `run.py`'s stall heartbeat is what stops a
    hero being asked forever.
    """
    if bar is None or not bar.waiting or bar.any_skill:
        return None
    if not bar.slot_lit(MOVE_SLOT):
        return None
    launches = tuple(getattr(update, "actor_launches", ()) or ())
    size = int(getattr(update, "party_size", 0) or 0)
    if not launches or size < 2:
        return None
    here = rank_from_pattern(launches, bar.skill_pattern, size)
    if here is None:
        return None
    fightable = sorted(
        (r for r in range(1, size + 1)
         if r != here and any(lp and r in set(lp) for lp in launches)),
        key=lambda r: (abs(r - here), r))
    if not fightable:
        return None
    dest = fightable[0]
    pt = resolved.point("hero", dest - 1)
    if pt is None:
        return None
    return InputPlan(
        (Step("key", vk=SKILL_VKS[MOVE_SLOT - 1], label="select slot 5 (MOVE)"),
         Step("click", screen=pt, label=f"hero rank {dest}")),
        "battle",
        f"MOVE to rank {dest} - the game has all four skills greyed out, so "
        f"this hero is really at rank {here} and cannot fight from there")


def _with_snuff(update, plan: InputPlan, resolved) -> InputPlan:
    """Put the torch out FIRST, then play the turn -- "snuff, then swing".

    Only one order is safe. Snuffing AFTER the blow is too late: the loot roll
    is taken on the kill, which is why Fran does it at the moment the last
    enemy dies. Snuffing on any EARLIER turn is the expensive mistake, because
    the party then fights the rest of the round in the dark for loot it has
    not earned -- and that gate lives in `run.py::_battle_torch` behind
    `about_to_win`, not here.

    APPENDS rather than replaces: the turn still gets played. A snuff that
    swallowed the attack would forfeit the very kill it exists to sweeten.
    """
    if not getattr(update, "snuff_torch", False):
        return plan
    snuff = plan_snuff_torch(resolved)
    if not snuff.actionable:
        return plan
    return InputPlan(snuff.steps + plan.steps, plan.layer,
                     f"{plan.why}  [+ snuff the torch first]")


class _LayerDone:
    """An `Update` with one layer's advice struck out, so re-planning moves on.

    A proxy rather than a copy: `Update` and `RoomAdvice` both grow fields, and
    a hand-built duplicate would silently drop whichever one was added last.

    Generalised from the curio-only version on 2026-08-31, because the TORCH
    had the same defect and it stalled a live raid for 180 s -- see
    `_handle_room`.
    """

    def __init__(self, update, *fields: str):
        self._u = update
        self._advice = _AdviceWithout(getattr(update, "room_advice", None),
                                      fields)

    @property
    def room_advice(self):
        return self._advice

    def __getattr__(self, name):
        return getattr(self._u, name)


class _AdviceWithout:
    """`RoomAdvice` with the named fields blanked.

    `empty` is recomputed over what is LEFT, and it has to be: it is what
    `plan_room` uses to decide there is nothing to do here, and reading the
    underlying advice would say "still busy" forever.
    """

    _BLANK = {"curio_action": "", "torch": "", "camp": False}

    def __init__(self, advice, fields):
        self._a = advice
        self._blanked = {f: self._BLANK.get(f, "") for f in fields}

    @property
    def empty(self) -> bool:
        def get(name, default):
            if name in self._blanked:
                return self._blanked[name]
            return getattr(self._a, name, default)
        return not (get("next_step", "") or get("torch", "")
                    or get("curio_action", "") or get("camp", False))

    def __getattr__(self, name):
        if name in self._blanked:
            return self._blanked[name]
        return getattr(self._a, name)


def _curio_done(update):
    return (_LayerDone(update, "curio_action")
            if getattr(update, "room_advice", None) else update)


def _torch_done(update):
    """The torch has had its go. Move on to the route.

    **A layer that cannot act must never block one that can** -- the rule
    `plan_room` states in its own docstring, and which the curio branch was
    fixed for while the torch branch kept returning. A relight the save will
    not acknowledge therefore pinned the party in place: `plan_room` checks the
    torch FIRST, so every following poll re-planned the same relight and the
    route step was never reached.

    Seen live on 2026-08-31: `KEY T` reported "the input did not land" three
    times in rooH, the actuator gave up on the turn as designed, and the raid
    then stalled for 180 s and was killed with the party standing still. The
    torch may well have been correct to refuse -- the game ships
    `str_cant_use_torch_at_limit`, "It's no use...this is all the light we are
    getting for now" -- which is exactly why an unacknowledged torch must be
    survivable rather than fatal.
    """
    return (_LayerDone(update, "torch")
            if getattr(update, "room_advice", None) else update)


def in_a_room(area: str) -> bool:
    """True for a room area id, False for a corridor.

    `rooB` vs `coBC` -- the same convention `dungeon_map.py:388` uses to set
    `Area.is_room`. It matters because the two are moved through differently:
    see `plan_room`.
    """
    return str(area or "").startswith("roo")


# How long to let a building panel draw before clicking inside it, and how
# long a purchase or a recruit needs before the save is re-read. Both are the
# same shape of number as `CURIO_SETTLE`: DD animates its panels in, and a
# click into a panel that is still sliding lands on the Hamlet behind it.
TOWN_PANEL_SETTLE = 0.9

# The most up-arrow clicks one trade may need. A solved trade is one or two
# multiples; anything larger means the route is wrong, and sending thirty
# clicks at a widget is how a UI ends up in a state nobody can read.
MAX_EXCHANGE_CLICKS = 6


def plan_open_building(name: str, resolved) -> InputPlan:
    """Click one building ON THE HAMLET to open its panel.

    **Not the navigation strip.** That strip is derivable and it is not drawn
    on the town screen -- DD puts it inside a building panel, so it is how to
    hop between buildings once one is open. The first live town visit clicked
    it, nothing opened, and six further clicks went into an open Hamlet. See
    `townlayout.BUILDING_SCENE_POINTS`.

    Refuses for a building whose scene point has not been measured, and for an
    unknown name. Both are the same rule: a refusal costs a town visit, and a
    guessed coordinate costs whatever happens to be under it.
    """
    from bridge import townlayout

    pt = resolved.at(townlayout.building_scene_point(name))
    if pt is not None:
        return InputPlan((Step("click", screen=pt, label=f"open the {name}",
                               after=TOWN_PANEL_SETTLE),),
                         "town", f"open the {name}")

    # **NO SCENE POINT IS NOT A DEAD END: OPEN THE ONE WE HAVE, THEN HOP.**
    # The strip is not drawn on the Hamlet, so it cannot open the FIRST
    # building -- but once any panel is up it opens every other one, and the
    # stage coach's scene point has been measured since 2026-08-31. Two
    # clicks reach a building nobody has measured in the scene.
    #
    # This is what the Guild needed. Its skill unlocks were being planned and
    # BUDGETED and then skipped with "the guild's scene point has never been
    # measured", so the party embarked carrying a comp whose skills it had
    # never been given -- Fran: *"the former comp didnt truly use the
    # potential of the comp"*. Verified live 2026-09-21: coach, then the
    # strip, and the Guild panel came up.
    strip = resolved.at(townlayout.building_button(name))
    first = resolved.at(townlayout.building_scene_point("stage_coach"))
    if strip is None or first is None:
        known = ", ".join(sorted(townlayout.BUILDING_SCENE_POINTS)) or "none"
        return refuse(f"no measured scene point for {name!r} and no strip "
                      f"button to hop with (scene points: {known})", "town")
    return InputPlan(
        (Step("click", screen=first, label="open the stage coach",
              after=TOWN_PANEL_SETTLE),
         Step("click", screen=strip, label=f"hop to the {name}",
              after=TOWN_PANEL_SETTLE)),
        "town", f"open the {name} (via the coach, then the strip)")


def plan_close_building(resolved) -> InputPlan:
    from bridge import townlayout

    pt = resolved.at(townlayout.building_close())
    if pt is None:
        return refuse("no derived close button", "town")
    return InputPlan((Step("click", screen=pt, label="close the panel",
                           after=TOWN_PANEL_SETTLE),),
                     "town", "close the panel")


def plan_open_upgrades(resolved) -> InputPlan:
    """Click the building crest, which is what draws the upgrade trees.

    **A freshly opened building panel shows no trees at all** -- the coachman
    and the recruit list fill it, and the derived nodes land in the dark left
    margin. That cost a day of `UPGRADES_MEASURED` being False. The crest
    beside the panel title reveals them, verified live: all three trees
    appeared, with the pitches, rank counts and prices the shipped files
    predict (`captures/stage_coach_upgrades.png`).

    Safe on its own -- it switches a view and spends nothing.
    """
    from bridge import townlayout

    pt = resolved.at(townlayout.building_crest())
    if pt is None:
        return refuse("no measured building crest", "town")
    return InputPlan((Step("click", screen=pt, label="show the upgrade trees",
                           after=TOWN_PANEL_SETTLE),),
                     "town", "open the upgrade trees")


def plan_buy_upgrade(purchase, tree_index: int, resolved) -> InputPlan:
    """Click one upgrade node, after any trades it needs.

    **The trades are NOT in this plan.** Each one is its own confirmed
    exchange on a different widget, and bundling them would make a single
    `InputPlan` that half-succeeds -- which is exactly the failure
    `Actuator.execute` stops on but cannot undo. `plan_exchange` is called
    first, per trade, and each is verified against the wallet in the save
    before the next is sent.
    """
    from bridge import townlayout

    if not townlayout.UPGRADES_MEASURED:
        return refuse("the upgrade tree geometry is not verified - no tree is "
                      "drawn on the captured stage coach panel, so a click "
                      "would land in its left margin", "town")
    if purchase.trades:
        return refuse(f"{purchase.node.label()} needs "
                      f"{len(purchase.trades)} trade(s) first", "town")
    # `node` order within a tree is the file's, which is the panel's.
    rank = "abcde".find(purchase.node.code)
    if rank < 0:
        return refuse(f"unknown upgrade code {purchase.node.code!r}", "town")
    pt = resolved.at(townlayout.upgrade_node(tree_index, rank))
    if pt is None:
        return refuse("no derived upgrade node", "town")
    return InputPlan((Step("click", screen=pt,
                           label=f"buy {purchase.node.tree_id}."
                                 f"{purchase.node.code}"),),
                     "town", f"buy {purchase.node.label()}")


def plan_exchange(trade, resolved, *, cycles: int,
                  count_clicks: int) -> InputPlan:
    """One heirloom trade: spin to the type, set how many, confirm the row.

    **`cycles` and `count_clicks` come from the CALLER because they depend on
    what the panel is currently showing**, and a plan is data. The panel is
    two spinners and a list of destination rows -- see the note above
    `townlayout.EXCHANGE_MEASURED` -- so where the type spinner already
    stands decides how many times to click it, and only `bridge/town.py`,
    which has a frame in hand, knows that.

    The row is derived rather than passed: the panel lists every heirloom
    except the one being given, in `HEIRLOOMS` order, measured on all four
    spinner positions.

    **It is still checked against the save, not assumed.**
    `bridge/town.py::do_exchange` re-reads the wallet after every trade and
    requires the exact delta, so a wrong spinner shows up on the first
    attempt rather than as a slow drain of the wrong currency.
    """
    from bridge import townlayout

    if not townlayout.EXCHANGE_MEASURED:
        return refuse("the exchange panel's geometry is not verified - the "
                      "town screen shows no panel until its toggle is "
                      "clicked, and a trade spends heirlooms", "town")
    if cycles > len(townlayout.EXCHANGE_CYCLE) or cycles < 0:
        return refuse(f"{cycles} type clicks is not a walk around a "
                      f"{len(townlayout.EXCHANGE_CYCLE)}-long cycle", "town")
    if not 0 <= count_clicks <= MAX_EXCHANGE_CLICKS:
        return refuse(f"{trade.label()} needs {count_clicks} count clicks - "
                      f"the solver picked a bad route", "town")
    row = townlayout.exchange_to_row(trade.rate.from_type, trade.rate.to_type)
    if row is None:
        return refuse(f"{trade.rate.from_type} -> {trade.rate.to_type} is not "
                      f"an heirloom pair", "town")

    type_pt = resolved.at(townlayout.exchange_type_arrow(up=True))
    count_pt = resolved.at(townlayout.exchange_count_arrow(up=True))
    ok_pt = resolved.at(townlayout.exchange_confirm(row))
    if None in (type_pt, count_pt, ok_pt):
        return refuse("the exchange widget did not resolve", "town")

    steps = [Step("click", screen=type_pt, label="next heirloom")
             for _ in range(cycles)]
    # The spinner opens at the type's CHEAPEST rate, not at one -- bust 2,
    # portrait 1, deed 2, crest 3, matching `min(from_amount)` per type. Off
    # by one here trades the wrong amount, which the wallet check catches but
    # only after it has happened.
    steps += [Step("click", screen=count_pt, label="one more")
              for _ in range(count_clicks)]
    steps.append(Step("click", screen=ok_pt, label="confirm the trade",
                      after=TOWN_PANEL_SETTLE))
    return InputPlan(tuple(steps), "town", trade.label())


def plan_open_exchange(resolved) -> InputPlan:
    """Click the toggle that opens the heirloom exchange on the town screen.

    Measured, unlike everything inside the panel -- see
    `townlayout.HEIRLOOM_EXCHANGE_BUTTON`. Opening it is safe on its own: it
    reveals a widget and spends nothing.
    """
    from bridge import townlayout

    pt = resolved.at(townlayout.exchange_button())
    if pt is None:
        return refuse("no measured exchange toggle", "town")
    return InputPlan((Step("click", screen=pt, label="open the exchange",
                           after=TOWN_PANEL_SETTLE),),
                     "town", "open the heirloom exchange")


def plan_hire(hire, roster_row: int, resolved,
              panel_row: Optional[int] = None) -> InputPlan:
    """DRAG a recruit from the coach panel onto the roster.

    A drag and not a click: DD's coach panel selects on click and hires on
    drop, so a click here reads as "look at this hero" and the log would show
    a working hire that never happened. `Step(kind="drag")` interpolates the
    motion between press and release, which is what SDL reads the gesture
    from.
    """
    from bridge import townlayout

    # **`panel_row` is not `hire.index`.** The index is where the recruit sat
    # when the visit was planned; the panel compacts as heroes are hired out
    # of it, so by the second hire those two differ. The caller tracks what it
    # has taken; the default keeps a single-hire caller working.
    row = hire.index if panel_row is None else panel_row
    src = resolved.at(townlayout.recruit_slot(row))
    dst = resolved.at(townlayout.roster_slot(roster_row))
    if src is None:
        return refuse(f"no derived coach slot {row}", "town")
    if dst is None:
        return refuse(f"no derived roster row {roster_row} "
                      f"(the list holds 8 and then scrolls)", "town")
    if src == dst:
        return refuse("a zero-length drag is not a drag", "town")
    return InputPlan((Step("drag", screen=src, to=dst,
                           label=f"hire {hire.recruit.hero_class}"),),
                     "town", f"hire {hire.recruit.label()}")



# One click per unit, because DD's provisioner has no quantity field: the
# stock icon adds one to the pack per click. Slower than a spinner and much
# easier to acknowledge -- the pack, the stock count and the gold all move
# by a known amount.
PROVISION_CLICK_GAP = 0.25


def plan_buy_provision(item: str, count: int, resolved,
                       length: int = 1) -> InputPlan:
    """Buy `count` of one provision, one click each.

    `length` picks which store list to index -- the shipped lists agree on the
    order for every quest length, so it changes nothing today and is passed
    anyway, because relying on that agreement silently is how a shipped list
    that stops agreeing becomes a wrong click.
    """
    from bridge import townlayout

    if not isinstance(count, int) or isinstance(count, bool) or count <= 0:
        return refuse(f"nothing to buy for {item!r}", "town")
    pt = resolved.at(townlayout.shop_item(item, length))
    if pt is None:
        return refuse(f"{item!r} is not in the shipped store list", "town")
    steps = tuple(Step("click", screen=pt, label=f"buy {item} ({i + 1}/{count})",
                       after=PROVISION_CLICK_GAP) for i in range(count))
    return InputPlan(steps, "town", f"buy {count} x {item}")


# The Guild draws a row per skill and the row carries the rank buttons. Which
# element of the row is the SELECTION toggle and which is the first purchase
# is **not settled** -- see `townlayout.GUILD_MEASURED`. Both plans below
# compose the point and then refuse, so the geometry is ready for the frame
# that settles it and nothing is clicked before then.

def plan_buy_gear(row: int, index: int, resolved) -> InputPlan:
    """Buy one weapon (`row` 0) or armour (1) rank at the Blacksmith.

    `index` is the rank code bought, 0 being the first upgrade. Refused until
    `townlayout.BLACKSMITH_MEASURED`."""
    from bridge import townlayout

    if not townlayout.BLACKSMITH_MEASURED:
        return refuse("the blacksmith's rank buttons are not measured", "town")
    pt = resolved.at(townlayout.blacksmith_node(row, index))
    if pt is None:
        return refuse(f"no blacksmith row {row} rank {index}", "town")
    label = "weapon" if row == 0 else "armour"
    return InputPlan((Step("click", screen=pt,
                           label=f"buy {label} rank {index}"),),
                     "town", f"buy blacksmith row {row} rank {index}")


def plan_buy_skill(skill_row: int, resolved, rank: int = 1) -> InputPlan:
    """Unlock (or upgrade) one combat skill in the Guild.

    `rank` is 1-based the way the game prices it -- rank 1 is the 1000-gold
    unlock -- and it indexes the row's buttons from 0, so rank 1 is button 0.
    """
    from bridge import townlayout

    if not isinstance(rank, int) or isinstance(rank, bool) or rank < 1:
        return refuse(f"rank {rank!r} is not a rank", "town")
    pt = resolved.at(townlayout.guild_skill_node(skill_row, rank - 1))
    if pt is None:
        return refuse(f"no derived guild row {skill_row} rank {rank}", "town")
    if not townlayout.GUILD_MEASURED:
        return refuse("the guild panel geometry is not verified - no frame of "
                      "it has been banked, and the stage coach's derived nodes "
                      "landed in a margin until one was", "town")
    return InputPlan((Step("click", screen=pt,
                           label=f"buy skill row {skill_row} rank {rank}"),),
                     "town", f"buy guild skill row {skill_row} rank {rank}")


def plan_select_skill(skill_row: int, resolved) -> InputPlan:
    """Equip one of the hero's unlocked skills -- the 4-of-7 choice.

    Distinct from `plan_buy_skill`: buying makes a skill ownable, selecting
    puts it on the bar. A comp names four, and the other three have to come
    OFF, so a caller toggles rather than assuming an empty bar.
    """
    from bridge import townlayout

    pt = resolved.at(townlayout.guild_skill_node(skill_row, 0))
    if pt is None:
        return refuse(f"no derived guild row {skill_row}", "town")
    if not townlayout.GUILD_MEASURED:
        return refuse("the guild panel geometry is not verified, and which "
                      "element of a skill row is the selection toggle is "
                      "exactly what a banked frame has to settle", "town")
    return InputPlan((Step("click", screen=pt,
                           label=f"select skill row {skill_row}"),),
                     "town", f"select guild skill row {skill_row}")


def plan_scroll_roster(down: bool, resolved, clicks: int = 1) -> InputPlan:
    """Scroll the town's roster list, which shows 8 of up to 28 heroes.

    **The re-indexing this used to refuse over is not silent any more.**
    `town._roster_offset` reads which save row is drawn at the top by matching
    the drawn portraits against the save's class order, so a scroll is
    confirmed the way a purchase is -- and it turned out the list was ALREADY
    arriving at offset 4 before anything scrolled it, which made refusing a
    way of not noticing rather than a way of being safe.

    One click is one row. `town.scroll_roster_to` re-measures after each and
    stops when the offset stops moving, because the chevron is only drawn when
    it can act.
    """
    from bridge import townlayout

    if not isinstance(clicks, int) or isinstance(clicks, bool) or clicks <= 0:
        return refuse("nothing to scroll", "town")
    pt = resolved.at(townlayout.roster_scroll(down))
    if pt is None:
        return refuse("no derived roster scroll arrow", "town")
    if not townlayout.ROSTER_SCROLL_MEASURED:
        return refuse("the roster scroll arrows are not verified", "town")
    where = "down" if down else "up"
    steps = tuple(Step("click", screen=pt, label=f"scroll {where}",
                       after=TOWN_PANEL_SETTLE) for _ in range(clicks))
    return InputPlan(steps, "town", f"scroll the roster {where} x{clicks}")



# How many tiles the party must have put between itself and the tile it
# entered on before D+W is allowed. See `plan_hallway_sweep`.
HALLWAY_DOOR_CLEARANCE = 1


def plan_hallway_sweep(area: str, walk, hold: float = WALK_HOLD,
                       travelled: Optional[int] = None) -> InputPlan:
    """Walk the hallway with D and W held TOGETHER.

    Fran's technique, and it replaces a problem rather than solving it:

    > *"you can keep pressing both D+W at the same time and interact with
    > things by walking, but take care to only do that after separating from
    > the initial door, if not we'll loop again."*

    Holding the interact key while walking means **nothing has to compute
    where to stop**. The bridge's tile arithmetic put the party 124 px short
    of the tent (measured against a position Fran set by hand --
    `captures/curio_correct_distance.png`), and three separate theories about
    the right stopping distance have been falsified here. A sweep does not
    have a stopping distance.

    **THE DOOR CLEARANCE IS THE WHOLE SAFETY ARGUMENT.** A hallway is entered
    at its LEFT and left at its RIGHT -- Fran again -- so the party arrives
    standing at the door it just came through, and `W` there walks it straight
    back. That is not hypothetical: `_interact_curio`'s docstring records it
    from a live run ("it exited properly but immediately used W instead of
    D"), and a `W`-at-the-door fallback written earlier the same day was
    reverted unflown for exactly this. So the sweep REFUSES until the party
    has advanced `HALLWAY_DOOR_CLEARANCE` tiles from where it entered, and
    plain `D` covers that first stretch.

    `walk` is a `Walk` from `_walk_progress`, or None when the position could
    not be read. `travelled` is the same number the safety argument actually
    needs -- **tiles advanced from the entry, which is `raid.areatile` and
    nothing else** -- and it is what makes this usable on a corridor being
    re-walked, where `Area.standing_on` has no dark run left to read and
    `walk` is therefore None.

    That is a real narrowing of what has to be known, not a loosened guard.
    The clearance test asks one question: is the party still next to the door
    it came in by? `areatile` answers exactly that and is exact and monotone
    there (measured live 2026-09-06, 2 -> 3 under `D`); the direction it
    lacks is not part of the question. With neither, this still refuses --
    not knowing where the party stands is exactly when holding an interact
    key is most dangerous.
    """
    if in_a_room(area):
        return refuse("a sweep is for hallways; a room is one tile", "route")
    if walk is None and travelled is None:
        return refuse("no position in this hallway - not holding W blind",
                      "route")
    # Tiles advanced from the one the party entered on. `passed - 1` is that
    # number whichever END they came in at -- `index` is `passed - 1` from the
    # front and `total - passed` from the back, and both give `passed - 1`
    # tiles of travel. Deriving it from `index` needs a branch that is easy to
    # get backwards; this does not.
    travelled = (max(0, walk.passed - 1) if walk is not None
                 else max(0, int(travelled)))
    if travelled < HALLWAY_DOOR_CLEARANCE:
        return refuse(f"still at the door ({travelled} tile(s) in) - plain "
                      f"walking until clear, or W walks us back through it",
                      "route")
    # Deeper is `D`, always -- see `dungeon_map.Walk.key_toward`. A sweep only
    # ever goes deeper: it is the door behind the party that `W` would walk
    # back through, which is what the clearance check above is protecting.
    return InputPlan((Step("keys", vks=(VK_D, VK_W), hold=hold,
                           label="sweep D+W"),),
                     "route", f"sweep {area or 'the hallway'} with D+W")


def plan_room(advice, state, area: str = "") -> InputPlan:
    """Out of combat: torch, then the curio, then movement.

    **A layer that cannot act never blocks one that can.** Every branch here
    used to `return` its refusal, so a room holding a curio -- including one
    the advisor said to SKIP -- never reached the route step and the party
    stood still. Reported live as "corridor movement does not work". A curio
    the actuator cannot press is now named in the walk plan's reason and
    walked past, which the marker already records as a DIFFER.


    Movement is `D`, and there is nothing to derive. A hallway has no map
    direction: DD draws the party entering at the LEFT of the hallway art
    whichever way the corridor runs, so right advances and left backtracks --
    Fran, *"hallways have no directions, only left (backtrack) and right
    (advance)"*, and the same rule in his words two functions up in
    `plan_hallway_sweep`. See `dungeon_map.Walk.key_toward`, which is where
    the one case that is NOT plain `D` lives: walking back to something the
    party has already passed.
    """
    if advice is None or getattr(advice, "empty", True):
        return refuse("no room advice to act on", "room")

    # Torch first: relighting before walking is what the torch layer means,
    # and `T` needs no coordinates.
    if getattr(advice, "torch", "") == "relight":
        # **ONE `T` IS 25 LIGHT, AND THE FLOOR IS 76.** This sent exactly one
        # press whatever the level was, so a party that had just snuffed to 0
        # walked into the next fight at 25 -- the log saying a relight had
        # been sent, the game agreeing, and the hard torch floor every other
        # layer is built on quietly broken. Fran, 2026-09-20: *"try to never
        # combat on under 75 torch, it started a combat at 0 torch"*.
        #
        # `RoomAdvice.torch_count` is what the decision layer worked out, and
        # it is already capped by the pack. One is the floor rather than the
        # default, so an advice built by an older caller that has no count
        # behaves exactly as it did.
        count = max(1, int(getattr(advice, "torch_count", 0) or 1))
        steps = tuple(Step("key", vk=VK_T, label=f"use a torch ({i + 1}/{count})",
                           after=TORCH_CLICK_GAP)
                      for i in range(count))
        return InputPlan(steps, "torch",
                         f"relight before moving on ({count} torch(es), "
                         f"{TORCH_STEP:.0f} light each)")
    if getattr(advice, "torch", "") == "shutdown":
        # Unreachable in practice, and kept as a guard rather than a plan.
        # `run.py` calls the torch layer with `about_to_win=state.in_battle`,
        # so out of combat it never returns a shutdown at all. The INPUT for
        # one is known and measured -- `[SHIFT+CTRL+CLICK]` on
        # `layout.TORCH_METER`, per the game's own `str_snuff_torch_tip` --
        # but acting on it belongs in the battle plan ("snuff, then swing"),
        # not here, so this refuses rather than actuating a room-path
        # shutdown that the advisor never asked for.
        return refuse("torch SHUTDOWN is a combat-timing play, not a room one",
                      "torch")

    # Curio. `W` is the game's own `dungeon_interact_with_curio`, and it does
    # the BARE-HANDED interaction -- using an item on a curio is a separate
    # command (`dungeon_chose_item_use`) with no key bound to it. So W covers
    # `curios.py`'s "interact" and nothing else. Over the capture corpus that
    # is 35 of 71 distinct curio decisions (49%); "use" is 29 and "skip" 7.
    #
    # Pressing W on a "use" curio would be actively wrong, not merely useless:
    # the bare-handed outcome of a curio that wants holy water is often the
    # bad one, which is exactly why `curios.py` named an item.
    curio = getattr(advice, "curio_action", "")
    curio_id = getattr(advice, "curio_id", "") or "?"
    # DEFER is decided by `bridge/lootplan.py` and is NOT a skip. A curio is
    # worth about three times as much opened at torch 0 -- the gold multiplier
    # plus a 95% extra draw -- and the walk home has to come back through this
    # corridor anyway to hand the quest in, so the deferral costs nothing. It
    # reaches here as an instruction to WALK ON, which is exactly what the
    # `_LayerDone` rule already wants: a layer that will not act must not stop
    # one that will.
    if getattr(advice, "curio_defer", False):
        curio = ""
    if curio == "interact":
        return InputPlan((Step("key", vk=VK_W, label="interact"),),
                         "curio", f"interact with {curio_id} bare-handed")
    if curio == "use":
        # The SAME `W`. It opens the panel either way -- what changes is which
        # button `_work_curio_panel` then clicks, and that is decided against
        # the panel on screen rather than here, because only the panel says
        # whether the provision is actually in a slot. If it is not, the
        # worker closes the panel and the party walks on; it does NOT fall
        # back to the hand, because a bare-handed holy-water curio is the bad
        # outcome and is exactly why `curios.py` named an item.
        item = getattr(advice, "curio_item", "?")
        return InputPlan((Step("key", vk=VK_W, label="open the curio panel"),),
                         "curio", f"use {item} on {curio_id}")
    held_up = ""
    if getattr(advice, "curio_defer", False):
        held_up = (f"{curio_id} is DEFERRED - it pays about three times as "
                   f"much at torch 0 and the way home passes here again")
    elif curio == "optional":
        held_up = f"curio {curio_id} is a judgement call, not a rule"
    elif getattr(advice, "camp", False):
        held_up = "camping needs the campfire button and skill picks"

    # A layer that cannot act MUST NOT block a layer that can. Every curio
    # action used to `return` a refusal here, ahead of the route step -- so a
    # room holding any curio at all, including one the advisor said to SKIP,
    # never walked. The party simply stood there. Reported live as "corridor
    # movement does not work", which it was not.
    if getattr(advice, "next_step", ""):
        # `A`/`D` DO move the party inside a room -- Fran pressed `D` by hand
        # and it shuffled along -- but they never take it OUT of one. Leaving
        # is a door interaction (`dungeon_interact_with_door`, in the binary's
        # input table), and no key for it is known yet.
        #
        # So the walk loop cannot work here twice over: it cannot reach the
        # exit, and it cannot even tell that it moved. `Area.walked` does not
        # rescue this one -- a room is a SINGLE tile, so there is no run of
        # visited tiles to measure and it returns None by construction. That
        # is only true of rooms; corridors do carry a position. `_walk_toward`
        # read the silence as "the input did not land" and hammered `D` twelve
        # times against a room it was never going to leave.
        if in_a_room(area):
            return refuse(
                f"leaving {area or 'a room'} needs the door - A/D shuffles "
                f"the party inside a room but never out of it, and a room is "
                f"one tile, so there is no position within it to steer by",
                "route")
        why = f"toward {advice.next_step}"
        if held_up:
            label = ("COMING BACK FOR" if getattr(advice, "curio_defer", False)
                     else "LEAVING")
            why += f" ({label} {curio_id}: {held_up})"
        return InputPlan((Step("key", vk=VK_D, label="walk D",
                               hold=WALK_HOLD),), "route", why)
    if held_up:
        return refuse(held_up, "curio" if curio else "camp")
    return refuse("room advice carries nothing actuatable", "room")


# ── backends ──

#: How long a room relight waits for the save before carrying on. The save
#: writes torchlight per TURN, so outside a fight the answer rarely comes.
TORCH_ACK_TIMEOUT = 2.0

#: How long `_curio_landed` keeps re-reading the save for an answer.
CURIO_ACK_S = 2.5


def pack_grew(before, after) -> str:
    """What the pack gained, by item QUANTITY, or "" for nothing.

    A slot count misses loot that merges into a stack already carried --
    gold, above all -- which is most of what a loot curio gives.
    """
    if before is None or after is None:
        return ""
    def totals(pack):
        out: dict = {}
        for slot in pack:
            key = getattr(slot, "key", slot)
            out[key] = out.get(key, 0) + int(getattr(slot, "amount", 1) or 0)
        return out
    was, now = totals(before), totals(after)
    gained = [f"{(k[1] or k[0]) if isinstance(k, tuple) else k} +{n - was.get(k, 0)}"
              for k, n in now.items()
              if n > was.get(k, 0)]
    return ", ".join(gained)


class Backend:
    """`mods` are virtual keys held DOWN across the click and released after.

    Only SHIFT is used today, and only for discarding a pack item, which is the
    one input in the whole bridge that destroys something. Every backend must
    release what it pressed even when the click itself fails -- a SHIFT left
    stuck down turns the operator's next real click into another discard.
    """
    name = "base"

    def key(self, vk: int, hold: float = 0.0) -> bool:
        raise NotImplementedError

    def keys(self, vks: tuple[int, ...], hold: float = 0.0) -> bool:
        """Hold several keys down TOGETHER, then release them in reverse.

        The default is a sequential fallback so a backend that cannot do it
        still does something defensible -- but SIMULTANEITY IS THE POINT here
        (D+W walks and interacts at once), so `SendInputBackend` overrides it
        properly and this default is only ever a degraded path.
        """
        ok = True
        for vk in vks:
            ok = self.key(vk, hold) and ok
        return ok

    def click(self, screen: tuple[int, int], mods: tuple[int, ...] = ()) -> bool:
        raise NotImplementedError

    def right_click(self, screen: tuple[int, int],
                    mods: tuple[int, ...] = ()) -> bool:
        """The right button. See `MOUSEEVENTF_RIGHTDOWN` for why it exists.

        Default is a refusal rather than a left click: a backend that cannot
        send this must not silently send the other button, because the two
        mean different things to this game and the wrong one on an inventory
        item is a discard or a nothing, never the intended use.
        """
        return False

    def move(self, screen: tuple[int, int]) -> bool:
        """Move the pointer WITHOUT pressing anything.

        Both live backends already have one; it is declared here because
        `_park_cursor` calls it on whatever backend it was handed, and a
        backend that cannot move the pointer must say so rather than raise.
        """
        return False

    def drag(self, src: tuple[int, int], dst: tuple[int, int],
             mods: tuple[int, ...] = ()) -> bool:
        """Press at `src`, travel to `dst`, release. False if unsupported.

        Returning False rather than raising is the same contract `move` has: a
        backend that cannot drag says so, and the caller refuses the plan
        instead of the actuator dying on a daemon thread.
        """
        return False


class DryRunBackend(Backend):
    """Records what it was asked to do and sends nothing. The default."""
    name = "dry-run"

    def __init__(self):
        self.sent: list[Step] = []

    def key(self, vk: int, hold: float = 0.0) -> bool:
        self.sent.append(Step("key", vk=vk, hold=hold))
        return True

    def keys(self, vks: tuple[int, ...], hold: float = 0.0) -> bool:
        self.sent.append(Step("keys", vks=tuple(vks), hold=hold))
        return True

    def click(self, screen: tuple[int, int], mods: tuple[int, ...] = ()) -> bool:
        self.sent.append(Step("click", screen=screen, mods=tuple(mods)))
        return True

    def right_click(self, screen: tuple[int, int],
                    mods: tuple[int, ...] = ()) -> bool:
        self.sent.append(Step("rclick", screen=screen, mods=tuple(mods)))
        return True

    def move(self, screen: tuple[int, int]) -> bool:
        self.sent.append(Step("move", screen=screen))
        return True

    def drag(self, src: tuple[int, int], dst: tuple[int, int],
             mods: tuple[int, ...] = ()) -> bool:
        self.sent.append(Step("drag", screen=src, to=dst, mods=tuple(mods)))
        return True


class PostMessageBackend(Backend):
    """Background input: the real cursor never moves, the game need not focus.

    Tried first because it is the only backend that leaves the machine usable
    while the bridge plays. Whether it WORKS is an empirical question about
    DD's input path -- it is SDL-based, and SDL reads the keyboard through
    `WM_KEYDOWN`/`WM_CHAR` (which `PostMessage` can supply) but the mouse
    partly through raw input, which it cannot. `--probe` is what settles it,
    with the game running; do not assume either way from this docstring.

    Mouse coordinates go in CLIENT space, which is exactly what
    `GameWindow.to_client` produces.
    """
    name = "post-message"

    def __init__(self, win):
        self.win = win

    def key(self, vk: int, hold: float = 0.0) -> bool:
        # lParam is not optional decoration. Bits 16-23 carry the SCAN CODE,
        # and SDL -- which DD is built on -- keys off scan codes, not virtual
        # keys. Posting lParam=0 gives scan code 0, which SDL maps to
        # SDL_SCANCODE_UNKNOWN and drops. The first probe sent exactly that.
        #   bits 0-15  repeat count (1)
        #   bits 16-23 scan code
        #   bit 30     previous key state (1 on keyup)
        #   bit 31     transition state   (1 on keyup)
        scan = user32.MapVirtualKeyW(vk, MAPVK_VK_TO_VSC)
        down = 1 | (scan << 16)
        up = down | (1 << 30) | (1 << 31)
        ok = user32.PostMessageW(self.win.hwnd, WM_KEYDOWN, vk, down)
        # A real held key auto-repeats, and a game that walks while a key is
        # down may be watching the repeats rather than the key state. Post
        # them, with bit 30 set as Windows does on a repeat.
        held = hold or KEY_HOLD
        if hold:
            repeat = down | (1 << 30)
            end = time.perf_counter() + hold
            while time.perf_counter() < end:
                time.sleep(0.03)
                user32.PostMessageW(self.win.hwnd, WM_KEYDOWN, vk, repeat)
        else:
            time.sleep(held)
        ok &= user32.PostMessageW(self.win.hwnd, WM_KEYUP, vk, up)
        return bool(ok)

    def move(self, screen: tuple[int, int]) -> bool:
        """Move the GAME's cursor without moving the real one.

        If this works, clicks cost you nothing but focus: your pointer
        stays where you left it while the bridge plays.
        """
        cx, cy = self.win.to_client(*screen)
        return bool(user32.PostMessageW(self.win.hwnd, WM_MOUSEMOVE, 0,
                                        (cy << 16) | (cx & 0xFFFF)))

    def click(self, screen: tuple[int, int], mods: tuple[int, ...] = ()) -> bool:
        """A modifier travels in wParam, and that may not be enough.

        `MK_SHIFT` is how a real `WM_LBUTTONDOWN` reports a held shift, so a
        window that reads wParam sees it. SDL does not: it tracks modifiers
        from its own key events, so it would need the SHIFT keydown posted
        separately as well. Both are sent. Whether DD honours either through
        PostMessage is unproven -- the probe only ever tested a plain click --
        so `_discard_slot` verifies the result against the save and stops
        rather than shift-clicking a second slot on faith.
        """
        cx, cy = self.win.to_client(*screen)
        lparam = (cy << 16) | (cx & 0xFFFF)
        wparam = MK_LBUTTON | (MK_SHIFT if VK_SHIFT in mods else 0)
        for m in mods:
            scan = user32.MapVirtualKeyW(m, MAPVK_VK_TO_VSC)
            user32.PostMessageW(self.win.hwnd, WM_KEYDOWN, m, 1 | (scan << 16))
        if mods:
            time.sleep(MOD_DWELL)
        try:
            ok = user32.PostMessageW(self.win.hwnd, WM_MOUSEMOVE, 0, lparam)
            ok &= user32.PostMessageW(self.win.hwnd, WM_LBUTTONDOWN, wparam, lparam)
            time.sleep(0.03)
            ok &= user32.PostMessageW(self.win.hwnd, WM_LBUTTONUP,
                                      wparam & ~MK_LBUTTON, lparam)
            return bool(ok)
        finally:
            for m in reversed(mods):
                scan = user32.MapVirtualKeyW(m, MAPVK_VK_TO_VSC)
                user32.PostMessageW(self.win.hwnd, WM_KEYUP, m,
                                    1 | (scan << 16) | (1 << 30) | (1 << 31))


class SendInputBackend(Backend):
    """Foreground input: moves the real cursor. Works with essentially anything.

    The fallback, and the one that costs you the machine while it runs: the
    game must be focused and the cursor is genuinely moved. Paired with the F9
    kill switch for that reason.
    """
    name = "send-input"

    def __init__(self, win=None, restore_cursor: bool = True):
        self.win = win
        self.restore_cursor = restore_cursor

    #: While the game's help card is open, hold every input -- Fran's pause
    #: switch (2026-09-23). Checked at most this often, so a burst of inputs
    #: does not pay for a screen grab each.
    HELP_CHECK_S = 0.5

    def _hold_for_help_card(self) -> None:
        # Only against a REAL window: tests hand the backend stand-ins, and a
        # screen grab of a stand-in is not a question worth blocking on.
        hwnd = getattr(self.win, "hwnd", None)
        if not isinstance(hwnd, int) or not hwnd or not user32.IsWindow(hwnd):
            return
        now = time.time()
        if now - getattr(self, "_help_checked", 0.0) < self.HELP_CHECK_S:
            return
        try:
            from bridge import screen as screen_mod           # noqa: PLC0415
            said = False
            while screen_mod.help_card_is_up(screen_mod.grab(self.win)):
                if not said:
                    print("  [act] PAUSED - the help card is open; close it to "
                          "resume", flush=True)
                    said = True
                time.sleep(1.0)
            if said:
                print("  [act] resumed - the help card is closed", flush=True)
        except Exception:                                     # noqa: BLE001
            pass
        self._help_checked = time.time()

    def key(self, vk: int, hold: float = 0.0) -> bool:
        """Send both the virtual key AND its scan code.

        A vk with `wScan = 0` is enough for anything reading `WM_KEYDOWN`, and
        not enough for SDL/DirectInput/raw-input games, which read the SCAN
        CODE. DD is SDL-based, so the original vk-only form could be accepted
        by Windows -- `GetAsyncKeyState` confirms the injection landed -- and
        still be ignored by the game. Filling `wScan` costs nothing and covers
        both readers; `scancode_only()` is the stricter variant if even this
        is not enough.
        """
        self._hold_for_help_card()
        scan = user32.MapVirtualKeyW(vk, MAPVK_VK_TO_VSC)
        down = INPUT(type=INPUT_KEYBOARD,
                     u=_INPUTunion(ki=KEYBDINPUT(wVk=vk, wScan=scan)))
        up = INPUT(type=INPUT_KEYBOARD,
                   u=_INPUTunion(ki=KEYBDINPUT(wVk=vk, wScan=scan,
                                               dwFlags=KEYEVENTF_KEYUP)))
        n = user32.SendInput(1, ctypes.byref(down), ctypes.sizeof(INPUT))
        time.sleep(hold or KEY_HOLD)
        n += user32.SendInput(1, ctypes.byref(up), ctypes.sizeof(INPUT))
        return n == 2

    def keys(self, vks: tuple[int, ...], hold: float = 0.0) -> bool:
        """All down, hold, all up in REVERSE order.

        Reverse on release because the keys are not interchangeable: D is the
        movement and W the interaction, and letting go of the movement first
        leaves an interact pressed against a party that has stopped -- which
        is the door-reentry case in miniature. Releasing last-pressed-first
        keeps the walk alive until the interact is done.

        Every event carries its SCAN CODE for the same reason `key` does: DD
        is SDL-based and SDL reads scan codes, so a vk-only event can be
        accepted by Windows and ignored by the game.
        """
        self._hold_for_help_card()
        if not vks:
            return False
        sent = 0
        events = []
        for vk in vks:
            scan = user32.MapVirtualKeyW(vk, MAPVK_VK_TO_VSC)
            events.append((vk, scan))
        for vk, scan in events:
            down = INPUT(type=INPUT_KEYBOARD,
                         u=_INPUTunion(ki=KEYBDINPUT(wVk=vk, wScan=scan)))
            sent += user32.SendInput(1, ctypes.byref(down), ctypes.sizeof(INPUT))
        time.sleep(hold or KEY_HOLD)
        for vk, scan in reversed(events):
            up = INPUT(type=INPUT_KEYBOARD,
                       u=_INPUTunion(ki=KEYBDINPUT(wVk=vk, wScan=scan,
                                                   dwFlags=KEYEVENTF_KEYUP)))
            sent += user32.SendInput(1, ctypes.byref(up), ctypes.sizeof(INPUT))
        return sent == 2 * len(events)

    def _send_abs(self, nx: int, ny: int, extra: int = 0) -> bool:
        flags = (MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE
                 | MOUSEEVENTF_VIRTUALDESK | extra)
        ev = INPUT(type=INPUT_MOUSE,
                   u=_INPUTunion(mi=MOUSEINPUT(dx=nx, dy=ny, dwFlags=flags)))
        return user32.SendInput(1, ctypes.byref(ev), ctypes.sizeof(INPUT)) == 1

    def move(self, screen: tuple[int, int]) -> bool:
        """Move the REAL cursor to `screen`, and CHECK that it got there.

        Visible, and the reason F9 exists.

        **The absolute mapping is not exactly invertible, so this measures
        instead of predicting.** `SendInput` takes a 0..65535 value that
        Windows scales back to a pixel, and the rounding in that step is not
        the one the obvious arithmetic assumes: probed over this desktop, the
        breakpoints sit 17 normalised units apart (65536/3840 = 17.07) but do
        not fall where `floor`, `round`, or a half-pixel offset put them, and
        no single linear inverse fits the low, middle and top of the axis at
        once. Aiming open-loop lands **-1 or 0** off, never +1 -- 12 of 20
        probe points before this.

        So the position is read back and corrected. One pixel of error is
        `65536/span` normalised units, which makes the correction exact to
        first order and converges in a single step in practice. That is the
        same "verify, do not assume" rule `_send_and_confirm` and
        `_reset_map_view` follow, and unlike a fitted formula it holds for any
        monitor arrangement, any DPI scaling and any future change to how
        Windows rounds -- which is the whole point.

        Returns False only if the send itself was rejected; landing near but
        not exactly on the target is reported by `landed_exactly()` rather
        than failing the move, because a click is tens of pixels wide and
        refusing over one would be worse than the miss.
        """
        want = (int(screen[0]), int(screen[1]))
        nx, ny = self._normalise(want)
        if not self._send_abs(nx, ny):
            return False
        _vx, _vy, vw, vh = _virtual_desktop()
        best_n, best_err = (nx, ny), _err(_cursor_pos(), want)
        for _ in range(MOVE_CORRECTIONS):
            if best_err == 0:
                break
            got = _cursor_pos()
            if got is None:
                break
            dx, dy = want[0] - got[0], want[1] - got[1]
            if dx == 0 and dy == 0:
                break
            # one pixel is span/65536 of the range, so this is the exact
            # first-order correction rather than a search
            nx = max(0, min(65535, nx + int(round(dx * 65536.0 / vw))))
            ny = max(0, min(65535, ny + int(round(dy * 65536.0 / vh))))
            if not self._send_abs(nx, ny):
                return False
            err = _err(_cursor_pos(), want)
            if err < best_err:
                best_n, best_err = (nx, ny), err
            else:
                # **Not every pixel is reachable, so this STOPS rather than
                # hunting.** Probed on this desktop: normalised 14654 lands on
                # y=240 and 14655 on y=242, repeatably, with no cursor clip
                # and no DPI scaling -- y=241 cannot be addressed at all. A
                # correction loop that keeps going oscillates between the two
                # neighbours and can end FURTHER from the target than the
                # first shot. Keeping the best seen makes the loop a
                # guaranteed improvement or a no-op, never a regression.
                self._send_abs(*best_n)
                break
        self._last_target, self._last_error = want, best_err
        return True

    def landed_exactly(self) -> bool:
        """Did the last `move` end on the pixel it was given?

        Diagnostics only. Nothing refuses on it -- see `move`. `last_error`
        carries how far off it settled, in pixels, which is the number worth
        reporting when a display cannot address every pixel.
        """
        return getattr(self, "_last_error", None) == 0

    @property
    def last_error(self) -> Optional[int]:
        """Chebyshev pixels between the last `move`'s target and where the
        cursor actually settled. None until a move has been made."""
        return getattr(self, "_last_error", None)

    def _normalise(self, screen):
        """Screen pixel -> the 0..65535 range SendInput wants.

        Read fresh every call, so plugging a monitor in, unplugging one, or
        rearranging them mid-run needs no restart and no cached geometry.

        Three things this has to get right, and each was wrong once:

        **The span is the VIRTUAL desktop, not the primary monitor.** Paired
        with `MOUSEEVENTF_VIRTUALDESK` on the event itself -- normalising one
        way and flagging the other put every click at half its x on a
        3840x1080 desktop with a 1920x1080 primary.

        **The ORIGIN can be negative.** A monitor placed left of or above the
        primary makes `SM_XVIRTUALSCREEN`/`SM_YVIRTUALSCREEN` negative, which
        is why they are subtracted rather than assumed to be zero.

        **A pixel is a cell, not a point.** Windows maps the normalised value
        back by truncation, so aiming at a pixel's top-left corner lands one
        short whenever the division does not come out exact. Measured over the
        whole desktop before this: errors of **-1 or 0, never +1** -- the
        signature of a floor, not of noise. Aiming at the pixel's CENTRE
        (`+ 0.5`) removes it, and it does so for any span rather than for this
        machine's.

        Clamped because a caller can legitimately ask for a point off the
        desktop -- a window dragged half off-screen -- and an out-of-range
        value is silently wrapped rather than refused by `SendInput`.
        """
        vx = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
        vy = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
        vw = user32.GetSystemMetrics(SM_CXVIRTUALSCREEN) or 1
        vh = user32.GetSystemMetrics(SM_CYVIRTUALSCREEN) or 1
        return (_norm_axis(screen[0], vx, vw), _norm_axis(screen[1], vy, vh))

    def click(self, screen: tuple[int, int], mods: tuple[int, ...] = ()) -> bool:
        """Click at `screen`, on the pixel actually asked for.

        The positioning goes through `move`, which corrects itself, rather
        than repeating the open-loop mapping -- the button events are then
        sent WITHOUT re-aiming, so they land wherever the verified move left
        the cursor and cannot reintroduce the error it just removed.
        """
        return self._press(screen, mods, MOUSEEVENTF_LEFTDOWN,
                           MOUSEEVENTF_LEFTUP)

    def right_click(self, screen: tuple[int, int],
                    mods: tuple[int, ...] = ()) -> bool:
        """The right button, same aiming and the same modifier dwell.

        `[pc]str_help_raid_room_6` -- *"While interacting with an object:
        [RIGHT-CLICK] an inventory item to apply it to the object."*
        """
        return self._press(screen, mods, MOUSEEVENTF_RIGHTDOWN,
                           MOUSEEVENTF_RIGHTUP)

    def _press(self, screen, mods, down_flag: int, up_flag: int) -> bool:
        self._hold_for_help_card()
        if not self.move(screen):
            return False
        nx, ny = self._normalise(screen)
        self._mods(mods, down=True)
        try:
            time.sleep(MOD_DWELL if mods else 0.02)
            # **A MODIFIED CLICK WHOSE MODIFIER DID NOT ARRIVE IS A DIFFERENT
            # ACTION, NOT A WEAKER ONE.** On the torch widget SHIFT+click
            # reduces the light and a BARE click USES A TORCH: measured live
            # 2026-09-21, a snuff sent at torch 82 left it at 100, which is
            # +25 capped -- the opposite play, a torch burnt, and the loot
            # bonus on the blow it was meant to sweeten thrown away. Fran
            # watched it happen: *"the model didn't snuff torch, in fact, it
            # increased torch before killing the last enemy just now"*.
            #
            # `MOD_DWELL` was a blind fix for the same failure and this is the
            # check it never had. Asking the OS is cheap and decisive: if
            # SendInput's key-down did not take, GetAsyncKeyState says so.
            if mods and not self._mods_are_down(mods):
                self._mods(mods, down=False)
                self._mods(mods, down=True)
                time.sleep(MOD_DWELL)
                if not self._mods_are_down(mods):
                    print("  [act] the modifier never went down - NOT "
                          "clicking, because the same click without it is a "
                          "different action")
                    return False
            ok = self._send_abs(nx, ny, down_flag)
            time.sleep(0.03)
            ok &= self._send_abs(nx, ny, up_flag)
            return ok
        finally:
            self._mods(mods, down=False)

    def drag(self, src: tuple[int, int], dst: tuple[int, int],
             mods: tuple[int, ...] = ()) -> bool:
        """Press at `src`, travel to `dst` in steps, release.

        **The intermediate motion is the mechanic, not padding.** SDL decides a
        drag happened from the motion events BETWEEN the press and the
        release; a press, one jump and a release is commonly read as a plain
        click on the source, which in DD's roster panels means "select this
        hero" instead of "move this hero". So the pointer is walked across in
        `DRAG_STEPS` hops with a dwell at each end.

        The two dwells are for different reasons and neither is a guess at the
        animation: the one after the press is what lets the game register a
        grab before anything moves, and the one before the release is what
        stops the drop landing while the pointer is still travelling.

        Positioning goes through the verified `move`, so a drag inherits the
        read-back correction and cannot reintroduce the open-loop error --
        which matters more here than for a click, because a drop that lands
        one slot over is a different action rather than a near miss.

        A zero-length drag is REFUSED rather than sent. It is always a caller
        bug (a source and destination that resolved to the same slot), and
        sent, it is an ordinary click on that slot -- a wrong action that
        looks like a successful drag in the log.
        """
        self._hold_for_help_card()
        if tuple(src) == tuple(dst):
            return False
        if not self.move(src):
            return False
        self._mods(mods, down=True)
        try:
            time.sleep(DRAG_DWELL)
            nx, ny = self._normalise(src)
            if not self._send_abs(nx, ny, MOUSEEVENTF_LEFTDOWN):
                return False
            time.sleep(DRAG_DWELL)
            for i in range(1, DRAG_STEPS + 1):
                f = i / DRAG_STEPS
                mid = (int(round(src[0] + (dst[0] - src[0]) * f)),
                       int(round(src[1] + (dst[1] - src[1]) * f)))
                mx, my = self._normalise(mid)
                if not self._send_abs(mx, my):
                    return False
                time.sleep(DRAG_STEP_GAP)
            # settle exactly on the destination before letting go
            self.move(dst)
            time.sleep(DRAG_DWELL)
            ex, ey = self._normalise(dst)
            return self._send_abs(ex, ey, MOUSEEVENTF_LEFTUP)
        finally:
            self._mods(mods, down=False)

    def scroll(self, screen: tuple[int, int], notches: int) -> bool:
        """Wheel over `screen`. The pointer is moved there first.

        Aiming is not optional: the wheel goes to whatever sits under the
        cursor, so an unaimed scroll zooms whichever panel the operator
        happened to leave it over, or the world behind them.

        **Nothing routes through this, deliberately.** It was written to zoom
        the dungeon map out when too little of it was drawn to fit, and the
        live test refuted that use: scrolling is RELATIVE to wherever the view
        already is and has no stop, so it zoomed the map further in and left
        the panel stuck showing two cells. `ActRunner._reset_map_view` closes
        and reopens the panel instead, which lands on the same whole-map view
        every time.

        Kept because the capability is real and measured -- the wheel does
        reach the game, which is what proved the panel is stateful in the
        first place -- and because the next thing that needs a wheel should
        not have to rediscover `MOUSEEVENTF_WHEEL`. Same reason
        `layout.TORCH_METER` is banked and unwired.
        """
        self._hold_for_help_card()
        nx, ny = self._normalise(screen)
        flags = MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK
        move = INPUT(type=INPUT_MOUSE, u=_INPUTunion(
            mi=MOUSEINPUT(dx=nx, dy=ny, dwFlags=flags)))
        if user32.SendInput(1, ctypes.byref(move), ctypes.sizeof(INPUT)) != 1:
            return False
        time.sleep(0.03)
        ok = True
        for _ in range(abs(notches)):
            ev = INPUT(type=INPUT_MOUSE, u=_INPUTunion(
                mi=MOUSEINPUT(dx=0, dy=0,
                              mouseData=WHEEL_DELTA * (1 if notches > 0 else -1),
                              dwFlags=MOUSEEVENTF_WHEEL)))
            ok &= user32.SendInput(1, ctypes.byref(ev),
                                   ctypes.sizeof(INPUT)) == 1
            time.sleep(0.05)
        return ok

    @staticmethod
    def _mods_are_down(mods: tuple[int, ...]) -> bool:
        """Does Windows agree that every one of `mods` is held right now?

        `GetAsyncKeyState`'s high bit is the live physical-or-injected state,
        so this reports whether `SendInput`'s key-down actually took. It is
        the only evidence available here: SDL's view cannot be read from
        outside the game, and the game's reaction is what we are trying not
        to have to guess at.
        """
        try:
            return all(user32.GetAsyncKeyState(int(m)) & 0x8000 for m in mods)
        except Exception:                                   # noqa: BLE001
            return True          # cannot ask: behave exactly as before

    def _mods(self, mods: tuple[int, ...], down: bool) -> None:
        """Hold or release modifier keys, scan code filled in as `key` does.

        Released in a `finally`, always: a SHIFT left down after a failed
        discard would make the operator's next click discard something too.
        """
        # **SCAN CODES, NOT VIRTUAL KEYS -- because CTRL was being lost.**
        # Fran, having tested the gesture by hand: *"again, ctrl shift click
        # works to snuff it all, ive just tested it"*. Through `SendInput`
        # with `wVk` set it did not: the same click measured -25, the SHIFT
        # reduction, so CTRL never reached SDL while SHIFT did. `ScanCodeBackend`
        # already existed for exactly this reason -- "the form a raw-input game
        # is most likely to accept" -- but it only overrode `key()`, so every
        # modifier in the process was still going out as a virtual key.
        #
        # Windows synthesises the vk from the scan code, so the event is
        # indistinguishable from a real keyboard at the level SDL reads, and
        # `GetAsyncKeyState` still reports it for `_mods_are_down`.
        for m in (mods if down else tuple(reversed(mods))):
            scan = user32.MapVirtualKeyW(m, MAPVK_VK_TO_VSC)
            flags = KEYEVENTF_SCANCODE | (0 if down else KEYEVENTF_KEYUP)
            ev = INPUT(type=INPUT_KEYBOARD, u=_INPUTunion(
                ki=KEYBDINPUT(wVk=0, wScan=scan, dwFlags=flags)))
            user32.SendInput(1, ctypes.byref(ev), ctypes.sizeof(INPUT))
            time.sleep(0.02)


class ScanCodeBackend(SendInputBackend):
    """SendInput with `KEYEVENTF_SCANCODE` and no virtual key at all.

    The form a raw-input game is most likely to accept: Windows synthesises
    the vk from the scan code, so the event is indistinguishable from a real
    keyboard at the level SDL reads. Kept separate from `SendInputBackend`
    rather than replacing it, because the probe's job is to find out which
    form DD actually takes, not to assume one.
    """
    name = "scancode"

    def key(self, vk: int, hold: float = 0.0) -> bool:
        self._hold_for_help_card()
        scan = user32.MapVirtualKeyW(vk, MAPVK_VK_TO_VSC)
        flags = KEYEVENTF_SCANCODE
        down = INPUT(type=INPUT_KEYBOARD,
                     u=_INPUTunion(ki=KEYBDINPUT(wVk=0, wScan=scan, dwFlags=flags)))
        up = INPUT(type=INPUT_KEYBOARD,
                   u=_INPUTunion(ki=KEYBDINPUT(wVk=0, wScan=scan,
                                               dwFlags=flags | KEYEVENTF_KEYUP)))
        n = user32.SendInput(1, ctypes.byref(down), ctypes.sizeof(INPUT))
        time.sleep(hold or KEY_HOLD)
        n += user32.SendInput(1, ctypes.byref(up), ctypes.sizeof(INPUT))
        return n == 2


# ── the actuator ──

class KillSwitch:
    """F9, polled globally. A click-through/background actuator cannot receive
    a normal keystroke, which is the same reason `overlay.py` polls F10 rather
    than binding Esc."""

    def __init__(self, vk: int = VK_KILL):
        self.vk = vk
        self.tripped = False

    def check(self) -> bool:
        if user32.GetAsyncKeyState(self.vk) & 0x8000:
            self.tripped = True
        return self.tripped


@dataclass
class Actuator:
    """Executes plans, or refuses to.

    `enabled` is False by default. A caller has to opt in to sending, and the
    dry-run stage never does.
    """
    backend: Backend = field(default_factory=DryRunBackend)
    enabled: bool = False
    kill: KillSwitch = field(default_factory=KillSwitch)
    min_interval: float = 0.35
    _last: float = 0.0
    executed: int = 0
    refused: int = 0

    def execute(self, plan: InputPlan) -> bool:
        """Send a plan's steps. Returns False if nothing was sent."""
        if plan.refusal or not plan.steps:
            self.refused += 1
            return False
        if not self.enabled:
            self.refused += 1
            return False
        if self.kill.check():
            self.refused += 1
            return False
        # A throttle, not a timing model: DD animates, and hammering inputs
        # faster than the game consumes them loses some of them silently.
        gap = time.perf_counter() - self._last
        if gap < self.min_interval:
            time.sleep(self.min_interval - gap)
        for step in plan.steps:
            if self.kill.check():
                return False
            if step.kind == "keys":
                ok = self.backend.keys(step.vks, step.hold)
            elif step.kind == "key":
                ok = self.backend.key(step.vk, step.hold)
            elif step.kind == "drag":
                ok = self.backend.drag(step.screen, step.to, step.mods)
            elif step.kind == "rclick":
                ok = self.backend.right_click(step.screen, step.mods)
            else:
                ok = self.backend.click(step.screen, step.mods)
            if not ok:
                return False
            time.sleep(step.after or STEP_GAP)
        self._last = time.perf_counter()
        self.executed += 1
        return True


BACKENDS = {
    "dry": lambda win: DryRunBackend(),
    "send-input": lambda win: SendInputBackend(win),
    "scancode": lambda win: ScanCodeBackend(win),
    "post-message": lambda win: PostMessageBackend(win),
}


class ActRunner:
    """Subscribes to `run.py`'s `Update` stream and plays the advised turn.

    It decides NOTHING. `run.py` is still the whole player; this converts an
    already-made recommendation into keystrokes. That separation is why the
    overlay's rule applies here too -- never parse the printed block, always
    read the structured `Update`.

    ## Three gates, in order, and none of them is optional

    1. **`armed`** -- off unless `--act` was passed. Constructing a runner does
       not arm it, and neither does passing a live backend.
    2. **`confirm`** -- on by default. Every action is shown and waits for the
       operator. The first live action of this project should be one a human
       agreed to, not one that merely happened.
    3. **`turn_key`** -- an action fires at most once per turn. `Session.battle`
       already dedupes, but a double-press here is not cosmetic: it plays a
       second move. The runner proves it for itself rather than trusting the
       caller.

    ## Why it runs on the poll thread and must never raise

    `on_update` is called from `run.py`'s daemon `_watch` thread. BRIDGE.md's
    standing rule is that an exception there takes the bridge silent while the
    human keeps playing and keeps believing it is watching. An actuator that
    dies mid-raid is strictly worse than one that never started, so `handle`
    catches everything and reports.
    """

    def __init__(self, window, resolved, backend, *, armed: bool = False,
                 confirm: bool = True, log=None, raid_path=None,
                 full_pack: str = "pause"):
        self.window = window
        self.resolved = resolved
        self.actuator = Actuator(backend=backend, enabled=armed)
        self.armed = armed
        self.confirm = confirm
        self.log = log
        # What to do when the pack is full and nothing in it is free to throw
        # away. "pause" hands it back to the operator (the default, and the
        # only setting that never loses value). "abandon" closes the loot
        # window and walks on. See `_make_room`.
        self.full_pack = full_pack
        # The save file, used as the ACK: if it does not change after a
        # send, the input did not land. Without it the actuator is
        # fire-and-hope, and one dropped keystroke silently ends the run.
        self.raid_path = raid_path
        # Set by `run.py`. Called when a battle turn would not land, so the
        # next poll can advise a different hero. See `LiveState.next_hero`.
        self.on_turn_missed = None
        # **CURIOS THIS RAID HAS ALREADY GIVEN UP ON**, keyed `(area, id)`.
        #
        # `CURIO_TRIES` bounds the attempts inside ONE dispatch, and that was
        # enough while a room was published once. It is not any more: the
        # 25-second stall heartbeat added on 2026-09-05 clears `_room_key` and
        # re-plans, which restarts the ten. Measured the same night -- a sack
        # in `coEJ` took **286 curio actions and 208 loot presses across a
        # whole 900 s iteration**, the party never left the corridor, and the
        # raid ended where it started.
        #
        # So the bound has to outlive the dispatch. Per RAID rather than per
        # plan, and per `(area, curio)` rather than per curio, because the same
        # id appears in several corridors and each is a different object.
        self.gave_up_on: set = set()
        #: 'no room' modals answered this raid. Read by the raid layer: with
        #: the objective met, loot the game will not let into the pack is no
        #: reason to stay (live 2026-09-27: a 30-minute loot chase at 13/16
        #: that ended on the play timeout, "no room" at every curio).
        self.pack_full_seen = 0
        #: `() -> dict`, the raid layer's provision reserve (run.py wires it).
        self.item_reserve = None
        #: How many free actions this turn has taken, per `turn_key`. See
        #: `MAX_FREE_ACTIONS`.
        self._continued: dict = {}
        #: How many times this turn has been deferred. See `MAX_DEFERRALS`.
        self._deferred: dict = {}
        self.acted: set = set()
        #: Destinations a map-view reset has already been spent on, so a room
        #: that genuinely is not on the map costs `MAP_RESET_WATCH` once
        #: rather than on every poll. Per runner, like `gave_up_on`: the view
        #: is a property of this session's panel, not of the save.
        self._view_reset_for: set = set()
        self._was_in_battle = False
        # The order the party embarked in, learned by watching rather than
        # read: the save records the CURRENT formation and nothing records the
        # intended one. Captured the first time a room block arrives with no
        # fight behind it, which is the party as the human arranged it.
        self.marching_order: list = []
        # A formation reset the Quest Complete banner put off: it swallows the
        # Default Party order click, so the reset waits for the banner to go.
        self._formation_due = False
        # Tags already reported by `_say_once`.
        self._said: set = set()
        # Called with (hero guid, skill id) after a send the save confirmed.
        # The advisor uses it for per-battle skill limits, which the save does
        # not record -- see `bridge/seed.py::_sync_skill_uses`. A callback
        # rather than a reference, so the actuator still knows nothing about
        # the advisor.
        self.on_played = None
        # Set when an unmodelled screen is hit. While paused the runner
        # keeps watching but acts on nothing -- the standing rule is
        # pause and alert, never best-effort.
        self.paused = False
        self.stats = {"acted": 0, "refused": 0, "declined": 0, "errors": 0,
                      "paused": 0, "discarded": 0, "abandoned": 0}

    # -- plumbing --

    def _say(self, text: str) -> None:
        try:
            print(text)
        except UnicodeEncodeError:
            enc = getattr(sys.stdout, "encoding", None) or "ascii"
            print(text.encode(enc, "replace").decode(enc, "replace"))
        if self.log is not None:
            self.log.write(text + "\n")
            self.log.flush()

    def _refresh_layout(self):
        """Re-read the window rect before every action.

        A windowed game can be dragged between two turns, and a cached rect
        offsets every click silently -- the failure mode that looks like bad
        calibration rather than a stale rectangle.
        """
        cur = refresh_window(self.window)
        if cur is None:
            return None
        self.window = cur
        self.resolved = self.resolved.frac.resolve(cur)
        return cur

    # -- the hook run.py calls --

    def handle(self, update) -> None:
        try:
            self._handle(update)
        except Exception as exc:               # noqa: BLE001
            self.stats["errors"] += 1
            self._say(f"  [act] error, continuing: {type(exc).__name__}: {exc}")

    def _answer_hunger(self) -> bool:
        """A raid event modal blocks every layer. Answer it and say so.

        **This is not a layer, it is an interruption**, which is why it runs
        before the phase switch below rather than inside `plan_room`. While
        the Hunger panel is up the party cannot walk, `W` does nothing and the
        save does not move -- so every layer reads as "my input did not land"
        and none of them can tell why. Live 2026-09-06 the curio approach
        spent all ten of its presses against it and gave up on a tent that was
        never the problem; Fran, watching: *"Right now its stuck on a random
        hunger check"*.

        **EAT FIRST, and let the game decide whether it can.** Which answer is
        right depends on food the bridge cannot reliably count, and the wrong
        one costs HP and stress -- so rather than predict it, this clicks eat,
        re-reads the screen, and falls back to refusing only if the panel is
        still up. A greyed-out eat button simply does nothing, which makes the
        fallback the game's own answer to "do you have food".

        Returns True when it acted, so the caller drops this poll: whatever
        the update was planning was planned against a board behind a modal.
        """
        if not self.armed or getattr(self, "window", None) is None:
            return False
        # **Every read of the screen is best-effort.** This runs on EVERY
        # update, ahead of every layer, so a capture that throws -- a fake
        # window in a test, a frame grabbed mid-transition, no display -- must
        # cost nothing and change nothing. A modal detector that can break the
        # loop is worse than no modal detector.
        try:
            import bridge.screen as screen_mod                # noqa: PLC0415
            # **THE PANEL, NOT THE SCREEN.** `recognise_live` matches the
            # whole frame, which carries the room art behind this panel -- so
            # a hunger check in an unbanked room read UNKNOWN and this
            # returned False, leaving a modal up that swallows every other
            # input. `hunger_panel_is_up` asks about the widget alone and is
            # the same answer in every room (measured: 0.000-0.002 against
            # the banked frames, 0.792 against the nearest thing that is not
            # hunger). The name is still accepted, so nothing that worked
            # before stops working.
            if (screen_mod.recognise_live(self.window) != "hunger"
                    and not screen_mod.hunger_panel_is_up(
                        screen_mod.grab(self.window))):
                return False
        except Exception:                                     # noqa: BLE001
            return False
        if self._refresh_layout() is None:
            return False
        # **The game has to have FOCUS, and `Actuator.execute` does not check.**
        # `_send_and_confirm` is where that check lives, and this path does not
        # go through it -- so the answer went to whatever window was in front,
        # which during a supervised session is the operator's own terminal.
        # Live 2026-09-06: the panel was recognised, both boxes were measured
        # correctly, both clicks were "sent", and the panel did not move.
        if not self._focused_or_wait():
            self._say("  [act] [event] hunger is up and the game is not in "
                      "front - not clicking into somebody else's window")
            return True
        for what, frac in self._hunger_answers(screen_mod):
            self._say(f"  [act] [event] hunger - {what}")
            plan = InputPlan((Step("click", screen=self.resolved.at(frac),
                                   label=f"{what} (hunger)"),),
                             "event", f"answer the hunger check: {what}")
            if not self.actuator.execute(plan):
                return True
            self.stats["acted"] += 1
            time.sleep(PANEL_WAIT)
            try:
                still = screen_mod.hunger_panel_is_up(
                    screen_mod.grab(self.window))
            except Exception:                                 # noqa: BLE001
                still = False
            if not still:
                self._say("  [act] [event] the panel is gone")
                return True
        self._say("  [act] [event] the hunger panel is still up after both "
                  "answers - leaving it to you")
        return True

    #: How much of an option box has to be gold before it counts as offered.
    #: NOT a threshold -- the two boxes are compared against EACH OTHER, so
    #: this is only the tie-break floor below which neither is lit and the
    #: order falls back to eat-then-refuse. Measured on
    #: `captures/screens/hunger.png`: eat 0.279 against refuse 0.087.
    HUNGER_LIT_FLOOR = 0.15

    def _hunger_answers(self, screen_mod):
        """The two answers, most GOLD first. Fran: *"It should click always
        the yellow box if possible"*.

        **Compared against each other rather than against a constant**, which
        is what lets one banked frame decide it honestly: that frame has food,
        so it measures what "offered" looks like (warm-lit 0.279) and what the
        other button looks like beside it (0.087), and it says nothing about a
        greyed-out eat box because it contains none. A relative test needs it
        to say nothing: whichever box is the more gold is the one the game is
        offering, and if the read is wrong the caller tries the other anyway.

        Falls back to the fixed order when the frame cannot be read or neither
        box is lit -- eat first, because eating is the answer whenever it is
        available and the cost of asking for it without food is nothing.
        """
        from bridge.layout import (HUNGER_EAT,                # noqa: PLC0415
                                   HUNGER_REFUSE)
        order = [("eat", HUNGER_EAT), ("refuse", HUNGER_REFUSE)]
        try:
            import numpy as np                                # noqa: PLC0415
            img = np.asarray(screen_mod.grab(self.window)).astype(float)
            h, w, _ = img.shape
            scored = []
            for what, frac in order:
                cx, cy = int(frac[0] * w), int(frac[1] * h)
                half = max(6, int(0.016 * w))
                box = img[cy - half:cy + half, cx - half:cx + half]
                lum = box.mean(axis=2)
                gold = float(((box[:, :, 0] - box[:, :, 2] > 30)
                              & (lum > 60)).mean())
                scored.append((gold, what, frac))
            if max(g for g, _w, _f in scored) >= self.HUNGER_LIT_FLOOR:
                scored.sort(key=lambda s: -s[0])
                return [(w_, f) for _g, w_, f in scored]
        except Exception:                                     # noqa: BLE001
            pass
        return order

    #: Green under a hero while a skill is selected marks a legal target.
    #: Measured 2026-09-23 in target-select: legal ranks 0.022-0.071, the
    #: one the game would not take exactly 0.0.
    TARGET_GREEN_FLOOR = 0.008

    def _legal_hero_ranks(self) -> Optional[set]:
        """Ranks the game draws as legal targets right now, or None."""
        import numpy as np                                    # noqa: PLC0415
        import bridge.screen as screen_mod                    # noqa: PLC0415
        img = screen_mod.grab(self.window)
        if img is None:
            return None
        a = np.asarray(img).astype(int)
        h, w = a.shape[:2]
        y0, y1 = int(440 / 720 * h), int(468 / 720 * h)
        half = max(10, int(35 / 1280 * w))
        legal = set()
        for i in range(4):
            pt = self.resolved.point("hero", i)
            if pt is None:
                return None
            cx, _cy = self.window.to_client(*pt)
            box = a[y0:y1, max(0, cx - half):cx + half, :3]
            if box.size == 0:
                continue
            r, g, b = box[..., 0], box[..., 1], box[..., 2]
            if float(((g > 100) & (g > 1.4 * r) & (g > 1.4 * b)).mean())                     > self.TARGET_GREEN_FLOOR:
                legal.add(i + 1)
        return legal or None

    def _retarget_to_a_legal_hero(self, update, plan, bar):
        """Select the skill, ASK THE SCREEN which heroes it can reach, click.

        Measured 2026-09-23: a Musketeer's heal on the Bounty Hunter, whom
        the game did not bracket as a target. The click did nothing, the
        miss was read as "the game is asking another hero", and ten minutes
        went on heroes it was not asking while her self-heal was legal the
        whole time. Returns the plan still to send -- just the click once the
        key has gone out.
        """
        steps = tuple(plan.steps)
        if (not self.armed or plan.screen_witness or len(steps) < 2
                or steps[-1].kind != "click"
                or not (steps[-1].label or "").startswith("hero rank ")):
            return plan
        try:
            want = int(steps[-1].label.split()[-1])
        except ValueError:
            return plan
        time.sleep(SETTLE_BEFORE_SEND)
        if not self.actuator.execute(InputPlan(steps[:-1], plan.layer,
                                               "select the skill")):
            return plan
        # **Nothing bracketed is not "everyone legal".** Live, the second
        # time: no bracket at all, because the key had TOGGLED OFF a skill
        # already selected or the read beat the draw. So look again, and
        # once press the key again, before trusting the original click.
        legal = None
        for attempt in range(3):
            time.sleep(0.4)
            legal = self._legal_hero_ranks()
            if legal is not None:
                break
            if attempt == 1:
                self.actuator.execute(InputPlan(steps[-2:-1], plan.layer,
                                                "select the skill again"))
        keep = dict(continues_turn=plan.continues_turn,
                    screen_witness=plan.screen_witness)
        if legal is None or want in legal:
            return InputPlan((steps[-1],), plan.layer, plan.why, **keep)
        slot = getattr(update, "skill_slot", 0)
        for alt in getattr(update, "alternatives", ()) or ():
            if getattr(alt, "skill_slot", 0) != slot:
                continue
            p = plan_one_move(alt, self.resolved, bar)
            last = p.steps[-1] if p.actionable and p.steps else None
            label = (last.label or "") if last is not None else ""
            if label.startswith("hero rank ") and label.split()[-1].isdigit()                     and int(label.split()[-1]) in legal:
                self._say(f"  [act] [battle] rank {want} is not a legal "
                          f"target on screen (legal {sorted(legal)}) - "
                          f"{label} instead")
                return InputPlan((last,), plan.layer,
                                 p.why + " [retargeted]", **keep)
        rank = min(legal)
        self._say(f"  [act] [battle] rank {want} is not a legal target on "
                  f"screen (legal {sorted(legal)}) and no alternative names "
                  f"one - hero rank {rank}")
        return InputPlan((Step("click", screen=self.resolved.point("hero",
                                                                    rank - 1),
                               label=f"hero rank {rank}"),),
                         plan.layer, plan.why + " [retargeted]", **keep)

    def _answer_journal(self) -> bool:
        """Close a curio's journal page if one is up. True when it acted."""
        import bridge.screen as screen_mod                    # noqa: PLC0415
        if not screen_mod.journal_page_is_up(screen_mod.grab(self.window)):
            return False
        if not (self.armed and self._focused_or_wait()):
            return False
        pt = self.resolved.at(screen_mod.JOURNAL_CLOSE)
        if pt is None:
            return False
        self._say("  [act] [event] a journal page is up - closing it")
        self.actuator.execute(InputPlan(
            (Step("click", screen=pt, label="close the journal page"),),
            "event", "close the journal page"))
        time.sleep(0.8)
        return True

    def _answer_phantom_fight(self) -> None:
        """One short step when the save says fight and no enemy is drawn.

        Harmless if the read is wrong: A/D in a real fight moves nobody.
        """
        import bridge.screen as screen_mod                    # noqa: PLC0415
        img = screen_mod.grab(self.window)
        red = screen_mod.enemy_bars_drawn(img)
        if red is None or red > 0.0:
            # **Every hero tried and the fight still on: the marks are what
            # is stuck.** Measured 2026-09-23: an Abomination's MOVE counted
            # as played with the bar unchanged, the game sat in MOVE
            # target-select, and each round-robin skipped him as "already
            # played this turn" for half an hour. Forgetting the marks lets
            # the next pass really try every hero again.
            # **Second time round with nothing moved: PASS.** Whoever the
            # game is asking, passing their turn moves the fight on -- which
            # is why this lives here and not in `plan_battle`, where the bar
            # may belong to a different hero than the save names. Measured
            # 2026-09-23: an Abomination whose whole shortlist was greyed was
            # refused on every rotation for half an hour.
            self._stuck_rotations = getattr(self, "_stuck_rotations", 0) + 1
            if self._stuck_rotations >= 2 and self.armed:
                bar = self._read_bar()
                if bar is not None and bar.waiting and bar.slot_lit(PASS_SLOT):
                    plan = plan_one_move(_PassAdvice(), self.resolved, bar)
                    if plan.actionable and self._focused_or_wait():
                        self._say("  [act] [battle] two full rotations and "
                                  "nothing moved - PASSING the asked hero's "
                                  "turn")
                        self.actuator.execute(plan)
                        self._stuck_rotations = 0
            n = len(self.acted)
            self.acted.clear()
            self._continued.clear()
            self._deferred.clear()
            self._say(f"  [act] [battle] enemy bars read {red:.3f} - the fight "
                      f"is real; forgot {n} played-turn mark(s) so every hero "
                      f"is tried afresh" if red is not None else
                      "  [act] [battle] enemy bars unreadable - forgot the "
                      "played-turn marks")
            return
        if not (self.armed and self._focused_or_wait()):
            return
        self._say("  [act] [battle] no enemy on screen while the save says a "
                  "fight - one step to make the game write the save")
        self.actuator.execute(InputPlan(
            (Step("key", vk=VK_D, hold=0.2, label="edge D"),),
            "route", "end a fight the save has not seen end"))

    def _handle(self, update) -> None:
        # **Every drop says so.** A battle turn that reaches the body below
        # always prints something -- a plan or a refusal -- so a log that shows
        # an advisory block and then nothing means the update was dropped
        # ABOVE this point, and for three live runs there was no way to tell
        # which of the two silent returns did it. Said once per reason, not
        # once per poll, because both fire repeatedly by design.
        if self.paused and self._still_paused():
            self._say_once("paused", "  [act] still paused - not acting on "
                                     "anything until the pack has room")
            return
        # Before anything else: a modal makes every layer below look broken.
        if self._answer_hunger():
            return
        if self._answer_journal():
            return
        phase = getattr(update, "phase", "")
        if phase == "phantom_fight":
            self._answer_phantom_fight()
            return
        if phase == "room":
            if self._refresh_layout() is None:
                return
            self._handle_room(update)
            return
        if phase != "battle":
            return
        self._was_in_battle = True
        key = tuple(getattr(update, "turn_key", ()) or ())
        if key and key in self.acted:
            self._say_once(f"acted{key}",
                           f"  [act] already played this turn {key} - "
                           f"waiting for the next one")
            return

        if self._refresh_layout() is None:
            self._say("  [act] the game window is gone - not acting")
            return

        # **The snuff is executed and VERIFIED before the turn is planned**,
        # not bundled into it. `_with_snuff` prefixed the click to the attack
        # so the two went out as one plan, which preserved the order -- snuff,
        # then swing -- and made the snuff impossible to acknowledge. The order
        # is preserved here too, and the save now gets a say.
        if getattr(update, "snuff_torch", False) and self.armed \
                and not self.actuator.kill.check():
            self._snuff_torch()
            try:
                from dataclasses import replace as _replace   # noqa: PLC0415
                update = _replace(update, snuff_torch=False)
            except Exception:                                 # noqa: BLE001
                pass
        # **SAY WHAT THE BAR SAID, on the actionable path as well as the
        # refusal.** `plan_one_move`'s greyed-slot guard is unconditional, so
        # a digit that went out for a greyed slot means the bar was
        # UNREADABLE at plan time -- and the two cases are indistinguishable
        # in the log, which cost an hour of inference on 2026-09-06. A Duelist
        # turn sent `KEY 1` into a slot the frame banked 15 s later showed at
        # saturation 0.000, twice, and the raid stopped there.
        #
        # One short line, and it is the ONLY thing that separates "the guard
        # let it through" from "the guard never ran".
        bar = self._read_bar()
        # **SAY WHERE THE BAR THINKS THIS HERO IS STANDING**, beside where the
        # save says. `rank_from_pattern` turns the lit/greyed signature into a
        # rank, and a disagreement means every target and every MOVE
        # destination on this turn was computed from a stale formation.
        #
        # Recorded rather than acted on, deliberately. A Highwayman the save
        # put at rank 4 was advised MOVE to rank 2, with MOVE lit, and nothing
        # landed -- which LOOKS like exactly this. It also looks like several
        # other things, and two inferences today were wrong before the
        # measurement arrived. One line, then the next run answers it.
        where = ""
        if bar is not None:
            implied = rank_from_pattern(
                tuple(getattr(update, "actor_launches", ()) or ()),
                bar.skill_pattern,
                int(getattr(update, "party_size", 0) or 0))
            save_rank = int(getattr(update, "actor_rank", 0) or 0)
            if implied and save_rank and implied != save_rank:
                where = (f"  [the bar says rank {implied}, the save says "
                         f"{save_rank} - STALE FORMATION]")
            elif implied:
                where = f"  [rank {implied}, agreed]"
        self._say(f"  [act] [battle] bar: "
                  f"{bar if bar is not None else 'unreadable'}{where}")
        # **THE SCREEN SAYS THE TURN IS DUE, whatever the save says.** The save
        # is written per turn, so a hero's advice arrives "provisional" (enemy
        # turns still to resolve) while the game is already asking that hero:
        # the bar is lit. Live 2026-09-23 every such turn waited out a 25 s
        # stall first. When the bar is waiting AND the hero panel names the
        # advised hero, the timing gates are the save being late, not the
        # turn being early.
        if (getattr(update, "provisional", False)
                or getattr(update, "enemies_first", 0)
                or getattr(update, "movers", ()))                 and bar is not None and bar.waiting:
            asked = self._asked_hero(update)
            mine = getattr(update, "actor_class", "") or ""
            if asked and mine and asked == mine:
                try:
                    from dataclasses import replace as _replace  # noqa: PLC0415
                    # `movers` too: a repositioning enemy "still to act" is
                    # the same late save -- live 2026-09-24 a Bone Arbalist
                    # held an Occultist the game was already asking.
                    update = _replace(update, provisional=False,
                                      enemies_first=0, movers=())
                    self._say(f"  [act] [battle] the game is asking the {asked} "
                              f"now - not waiting for the save")
                except Exception:                            # noqa: BLE001
                    pass
        if getattr(update, "battle_item", ()) and not getattr(update, "provisional", False):
            self._use_battle_item(update)
        plan = plan_battle(update, self.resolved, bar)
        if not plan.actionable:
            self.stats["refused"] += 1
            self._say(f"  [act] {plan}")
            # **A refusal the next poll will reproduce exactly is spent, not
            # pending.** Nothing about the board changes while nobody acts, so
            # re-publishing it is an infinite loop -- which is what the
            # tutorial retreat stall actually was: the top pick had no button,
            # this returned without touching `acted`, the dedupe guard never
            # tripped, and the same line printed every poll forever.
            #
            # Marking it is safe for the TRANSIENT refusals too, and that is a
            # property of the key rather than a hope: `turn_key` is
            # `(round, guid, turns_taken, enemies_first)`, and all three timing
            # refusals above are gated on `enemies_first` -- so the moment those
            # enemy turns resolve the key is a different one and the turn is
            # planned again from scratch.
            if key:
                # A transient refusal is a request to be asked again, not an
                # answer -- see `MAX_DEFERRALS`.
                if plan.transient:
                    self._deferred[key] = self._deferred.get(key, 0) + 1
                    if self._deferred[key] < MAX_DEFERRALS:
                        return
                    self._say(f"  [act] [battle] still nothing after "
                              f"{MAX_DEFERRALS} looks - giving the turn up so "
                              f"the stall heartbeat can try another hero")
                self.acted.add(key)
            return

        self._say(f"  [act] {plan}")
        if not self.armed:
            self._say("  [act] not armed (--act was not passed) - nothing sent")
            return
        if self.actuator.kill.check():
            self._say("  [act] KILL SWITCH tripped - nothing sent")
            return
        if self.confirm and not self._approve():
            self.stats["declined"] += 1
            self._say("  [act] declined - play this turn yourself")
            if key:
                self.acted.add(key)            # do not re-ask on a republish
            return

        if not self._focused_or_wait():
            return

        # **ASK THE SCREEN WHOSE TURN IT IS, BEFORE SPENDING A KEYSTROKE.**
        # The save is wrong about it on ~2% of raid turns, and the cost of
        # finding out the slow way is not just the 15 s timeout: the digit
        # SELECTS a skill on the other hero's bar, DD dims the board into
        # target-select, and a later click anywhere legal would execute it.
        # Measured 2026-09-06 -- six misses in one raid, and the banked frame
        # showed the Duelist's slot 3 lit up gold while the log said the
        # Antiquarian was acting.
        #
        # Refusing here turns a 15 s timeout into an immediate retarget AND
        # leaves the game untouched. The turn is marked spent because the
        # recovery is a DIFFERENT hero, not this one again.
        asked = self._asked_hero(update)
        mine = getattr(update, "actor_class", "") or ""
        if asked and mine and asked != mine:
            self.stats["refused"] += 1
            self._say(f"  [act] [battle] the game is asking the {asked} and "
                      f"the save says {mine} - not sending; asking about the "
                      f"other hero on the next poll")
            # **Marked spent ONLY when a recovery is wired**, the same rule the
            # timeout path below follows: with `on_turn_missed` set `run.py`
            # advises a different hero and the mark is what stops the dedupe
            # re-serving the old one, while without it -- a bare runner, or
            # `--replay` -- nothing else would ever try and dropping the turn
            # would be the whole loss.
            if self.on_turn_missed is not None:
                if key:
                    self.acted.add(key)
                self._tell_missed(asked)
            return

        # **ONE ATTEMPT WHEN A RECOVERY IS WIRED, three when it is not.**
        # Re-sending the identical plan can only help against a transient --
        # focus lost, a backend hiccup, an animation still running. It cannot
        # help against the dominant cause measured live: the game is asking a
        # different hero, so the number key selects that hero's slot and the
        # click aims where their skill cannot reach. Three attempts at 15 s
        # each spend 45 s proving that twice over, and in the Cove fight that
        # was most of the wall clock. With `on_turn_missed` set, one failure is
        # enough evidence to go and ask about a different hero instead.
        # **A FREE ACTION IS NOT THE WHOLE TURN.** `.is_continue_turn` hands
        # the hero its turn straight back -- a stance change is the Duelist's
        # and the Sibyl's entire class identity, and the Abomination's
        # transform is the vanilla instance of it. `combat_engine.py` has
        # honoured it for as long as the Abomination has existed; this file
        # had never heard of it, so the bridge sent the stance change, put the
        # turn in `acted`, and waited for a turn the game had already given
        # back. Fran, watching it: *"the duelist change forms but didn't do
        # the second action ... it expects 1 action per hero"*.
        #
        # Two consequences and they are separate. The turn is NOT marked, so
        # the next poll plans the same hero again -- and the second plan is a
        # real action, because the stance change is `.per_turn_limit 1` and
        # the game greys it out, which `plan_one_move`'s bar guard then reads.
        #
        # And the SAVE IS THE WRONG WITNESS for this one. It is written per
        # turn, and this did not end a turn; `_send_and_confirm` therefore
        # reported "no save change in 15s - the input did not land" about an
        # input Fran could see landing on his screen. The action bar is the
        # right witness, because a new stance lights DIFFERENT skills -- so a
        # changed skill pattern is the game itself acknowledging the change.
        plan = self._retarget_to_a_legal_hero(update, plan, bar)
        tries = 1 if self.on_turn_missed is not None else 0
        if plan.screen_witness and not plan.continues_turn:
            # Same split as the free action above, for the same reason and a
            # different mechanic: this DOES end the turn, so the key is
            # marked -- but the save will not say so, and waiting 15 s for a
            # write that is never coming was costing a turn AND leaving the
            # bridge believing a stale formation.
            time.sleep(SETTLE_BEFORE_SEND)
            if self.actuator.kill.check():
                self._say("  [act] KILL SWITCH - stopping")
                return
            before = self._read_bar()
            if not self.actuator.execute(plan):
                self._say("  [act] the backend refused to send")
                return
            after = self._read_bar()
            moved = (before is not None and after is not None
                     and before.skill_pattern != after.skill_pattern)
            self.stats["acted"] += 1
            self._say("  [act] [battle] a MOVE - the save does not record an "
                      "in-combat swap, so the bar is the witness"
                      + (f": {before} -> {after}" if moved else
                         " (the bar did not change - the swap may not have "
                         "taken, but the save could never have said so)"))
            if key:
                self.acted.add(key)
            if moved:
                self._report_played(update, key)
            return
        if plan.continues_turn:
            # The same settle `_send_and_confirm` opens with, and for the same
            # documented reason: the save is written when an action RESOLVES,
            # so the poll that woke the bridge fires while that action is
            # still animating and input then goes nowhere. Skipping it here
            # would reintroduce the first failure the send path ever closed.
            time.sleep(SETTLE_BEFORE_SEND)
            if self.actuator.kill.check():
                self._say("  [act] KILL SWITCH - stopping")
                return
            before = self._read_bar()
            if not self.actuator.execute(plan):
                self._say("  [act] the backend refused to send")
                return
            after = self._read_bar()
            changed = (before is not None and after is not None
                       and before.skill_pattern != after.skill_pattern)
            self.stats["acted"] += 1
            self._say(f"  [act] [battle] a free action - the hero keeps the "
                      f"turn, so the next poll plans it again"
                      + ("" if changed else
                         " (the bar did not change; asking again anyway)"))
            # **REPORT IT ONLY IF THE BAR AGREED.** `_report_played` feeds the
            # per-battle use tracker, and its own rule is "only after the save
            # confirmed it, because a use that never landed would suppress a
            # legal skill for the rest of the fight". The save cannot confirm
            # this one, so the bar stands in -- and if the bar could not be
            # read either, nothing is reported, which keeps that rule intact.
            #
            # It matters most for exactly this skill: a stance change is
            # `.per_turn_limit 1`, so it is the one the next plan of this same
            # turn must not offer again.
            if changed:
                self._report_played(update, key)
            self._continued[key] = self._continued.get(key, 0) + 1
            if self._continued[key] >= MAX_FREE_ACTIONS and key:
                self._say(f"  [act] [battle] {MAX_FREE_ACTIONS} free actions "
                          f"on one turn - that is more than any shipped skill "
                          f"allows, so treating the turn as spent")
                self.acted.add(key)
            return
        if self._send_and_confirm(plan, attempts=tries):
            self.stats["acted"] += 1
            if key:
                self.acted.add(key)
            self._report_played(update, key)
        else:
            self.stats["refused"] += 1
            # **A turn that will not land is a WRONG-HERO hypothesis**, and
            # saying so is what lets the next poll test it. Seen live in the
            # Cove: the save said Runaway, the screen said Crusader, and the
            # bridge re-planned the identical unplayable move until the retry
            # budget ran out and the raid ended in a corridor.
            #
            # **The key is marked spent ONLY when a recovery is wired**, which
            # is the difference between moving on and giving up. With
            # `on_turn_missed` set, `run.py` advises a different hero and the
            # mark is what stops the dedupe re-serving the old one; without it
            # -- a bare runner, or `--replay` -- nothing else would ever try,
            # so the key stays open exactly as it did before.
            if self.on_turn_missed is not None:
                if key:
                    self.acted.add(key)
                self._say("  [act] [battle] trying the NEXT hero on the next "
                          "poll - the save and the screen may disagree about "
                          "whose turn it is")
                self._tell_missed(self._asked_hero(update))

    # -- out of combat --

    def _handle_room(self, update) -> None:
        """Loot, then the curio, then walk -- all in ONE call.

        Ordering is not arbitrary. The loot window sits on top of everything
        after a fight and swallows movement input, so it has to be cleared
        first -- and `SPACE` (take ALL) is the game's own documented hotkey for
        exactly that, so it needs no coordinates.

        **Looting must not return.** It used to, on the reasoning "let the next
        poll re-read the room" -- but there is no next poll for this room.
        `Session.room` dedupes on a key of (area, torch BRACKET), and neither
        changes by picking loot up, so the room is never republished and the
        walk never happens. Seen live: the whole full-pack recovery ran
        correctly, and then the party stood in `coBC` with a corridor to walk
        and nothing driving it. The same dedupe is why `_walk_toward` is a
        bounded loop instead of one step per update.
        """
        advice = getattr(update, "room_advice", None)
        area = getattr(update, "area", "") or ""

        # A CURIO PANEL LEFT STANDING SWALLOWS EVERY KEY AFTER IT, which is
        # the same hazard as the loot window below and needs clearing for the
        # same reason.
        #
        # **But NOT when a fight has just ended, and that ordering threw away
        # the loot of every battle.** The window standing over the board then
        # is the loot window, `curiopanel.find` matches it -- same red X, same
        # gold hand plate -- and closing it is exactly the wrong move. The log
        # read: "a curio panel is still open - closing it at (1042, 413)",
        # then "SPACE (take all) after the fight", then "15 slots free - there
        # was no loot window, nothing to take". Reported live by Fran as "it
        # refused to take the loot from the first battle".
        #
        # After a fight the modal IS the thing we want, and SPACE is what it
        # wants, so the loot is taken first and anything still standing is
        # dismissed afterwards.
        if not self._was_in_battle:
            self._dismiss_stray_curio_panel()

        # **THE SURVIVAL LAYER SAYS LEAVE, AND BETWEEN ROOMS THE EXIT IS THE
        # SEAL RATHER THAN THE COMBAT RETREAT.** `plan_battle` has acted on
        # `retreat_advised` since the six-polls-of-RETREAT incident, but that
        # is the BATTLE path: out of combat there is no retreat button, the
        # verdict went nowhere, and a party told to leave kept walking to the
        # next room. Measured 2026-09-21 on Fran's campaign -- two heroes
        # left, the Houndmaster on 4 HP at 100 stress, `advise_retreat`
        # reporting P(finish intact) 0% and P(a death) 75% -- and the router
        # planned the next corridor. Fran, watching: *"its stuck on the
        # hunger page or retire from the dungeon"*.
        #
        # `_leave_raid` normally runs with the objective MET, and here it
        # deliberately does not: forfeiting the quest is the WHOLE POINT of a
        # retreat, and the bill for it was already priced against pushing on.
        # The condition that actually keeps this safe is the same one that
        # always did -- no battle is on, which this path hangs off.
        #
        # It waits for the loot window to clear (`_was_in_battle`), because
        # leaving with it open would abandon loot already won for no gain.
        if getattr(update, "retreat_advised", False) and not self._was_in_battle:
            seen = ""
            try:
                from bridge import screen as screen_mod  # noqa: PLC0415
                seen = screen_mod.recognise_live(self.window) or ""
            except Exception:                            # noqa: BLE001
                seen = ""
            self._say("  [act] [raid] the survival layer says LEAVE - "
                      "abandoning the quest by the HUD seal")
            # **With the objective MET the exit is the SEAL, not the X.**
            # Live 2026-09-25: RETURN and RETREAT agreed, a completed quest's
            # HUD shows no abandon scroll, the X click hit nothing, and
            # `raid-abandoned` then barred every later poll -- thirteen
            # iterations of 189 s each, one click apiece.
            met = bool(getattr(getattr(update, "room_advice", None),
                               "raid_end", "")) or seen == "quest_complete"
            # **A banner that is up is answered, not clicked behind.** Live
            # 2026-09-27: `quest_complete` on screen, the seal clicked under
            # the modal, nothing moved, and `raid-left` then made every later
            # poll return silently -- the party stood in rooG with the
            # survival layer saying LEAVE until the iteration was killed.
            if seen == "quest_complete":
                from types import SimpleNamespace   # noqa: PLC0415
                if self._end_raid(SimpleNamespace(raid_end="return")):
                    return
            if self._leave_raid(seen, abandon=not met):
                return

        # No fight behind this block, so the formation is the one the human
        # arranged. First such sighting IS the marching order -- see
        # `_sort_into_marching_order` for why it has to be watched.
        if not self._was_in_battle and not self.marching_order:
            self.marching_order = self._party_order()
            if self.marching_order:
                self._say(f"  [act] [route] marching order is "
                          f"{'>'.join(self.marching_order)}")

        # A fight just ended: the loot window is up.
        if self._was_in_battle:
            self._was_in_battle = False
            # **Turn keys are unique within ONE combat, not within a raid.**
            # `turn_key` is (battle_round, hero guid, turns_taken,
            # enemies_first) and every one of those resets when a new fight
            # starts, so the FIRST turn of every combat is `(1, guid, 0, 0)`.
            # Carrying `acted` across the boundary therefore silently drops the
            # opening turn of every fight after the first: the bridge played
            # `(1, 2, 0, 0)` in `coAB`, and in `rooB` the same key came round
            # again and was deduped. That is every "went straight to the battle
            # and stuck there" report -- always the SECOND fight, always in
            # silence until `_say_once` finally named it.
            self.acted.clear()
            self._continued.clear()
            self._deferred.clear()
            self.gave_up_on.clear()
            self._fed_again_after_fight()
            self._said = {t for t in self._said if not t.startswith("acted")}
            self._take_loot()
            # Now it is safe: whatever is still up after SPACE is not a loot
            # window, so it is a modal that will swallow the walk.
            self._dismiss_stray_curio_panel()
            if self.paused:
                return
            self._reset_formation()
            # ...and fall through, because nothing will bring us back here.
        elif self._formation_due:
            # Put off behind the Quest Complete banner; retried every poll
            # until the banner is gone and the click can land.
            self._reset_formation()

        if advice is None:
            return
        # The raid-end choice comes before every other room layer, because it
        # is the only one that can be due while the party is standing on a
        # banner that blocks all the others.
        if self._end_raid(advice):
            return
        if getattr(advice, "provisions", ()) and self.armed:
            self._use_provisions(advice, area)
        plan = plan_room(advice, None, area=area)
        if not plan.actionable:
            self.stats["refused"] += 1
            self._say(f"  [act] {plan}")
            if plan.layer == "route" and in_a_room(area):
                self._show_map(advice, area, getattr(update, "dungeon", None))
            return
        # The curio layer prints its own plan, because it walks up to the
        # curio first and announcing the `W` before the `D` reads as the bug
        # that was just fixed ("KEY W interact" then "walk D").
        if plan.layer != "curio":
            self._say(f"  [act] {plan}")
        if not (self.armed and self._focused_or_wait()):
            return

        if plan.layer == "route":
            self._walk_toward(getattr(advice, "next_step", ""), area)
        elif plan.layer == "curio":
            self._interact_curio(plan, advice, area)
            # ...and carry on to the walk. Same reason as the loot: this room
            # is never published again, so a `return` here strands the party
            # next to the curio it just emptied. Re-planning with the curio
            # struck out reuses every rule in `plan_room` instead of copying
            # the room-versus-corridor test to a second place.
            if not self.paused:
                self._handle_room(_curio_done(update))
        else:
            # **ONE attempt when the keystroke SPENDS something.**
            # `_send_and_confirm`'s retry rests on "a keystroke means the same
            # thing the second time", and `T` is the exception: it burns a
            # torch. Its own acknowledgement channel is the one that cannot
            # carry it -- `torchlight` lives in `persist.raid.json`, which is
            # written per TURN, and a relight happens in a corridor where
            # there are no turns. Measured live 2026-09-06: three attempts at
            # 15 s each reported "the input did not land", the run gave up,
            # and 58 s later the save caught up with `MATCH torch the torch
            # went up` at 70 -> 100. One torch restores 25, so 95 was the
            # ceiling for a single press: at least two were spent.
            #
            # Same rule as the SHIFT-CLICK discard, and for the same reason:
            # an input that destroys something is sent once and REPORTED,
            # never re-sent on the word of a channel that has not answered.
            tries = 1 if plan.layer == "torch" else 0
            # And a SHORT wait for it: the save does not write torchlight in a
            # room either, so the full 15 s was a guaranteed wait for nothing
            # on every relight (live 2026-09-25, twice in one raid, each
            # followed by the torch having gone up after all).
            if plan.layer == "torch":
                if self._send_and_confirm(plan, attempts=1,
                                          ack_timeout=TORCH_ACK_TIMEOUT):
                    self.stats["acted"] += 1
                else:
                    self._say("  [act] [torch] sent once; the save writes the "
                              "torch per turn, so the next area confirms it")
            elif self._send_and_confirm(plan, attempts=tries):
                self.stats["acted"] += 1
            # ...and carry on to the walk either way. Same rule as the curio
            # above: this room is never published again, so a `return` here --
            # which is what used to happen -- strands the party. The torch is
            # checked FIRST in `plan_room`, so an unacknowledged relight
            # otherwise re-plans itself on every poll and the route step is
            # never reached. That is a 180 s stall and a killed iteration.
            if plan.layer == "torch" and not self.paused:
                self._handle_room(_torch_done(update))

    def _park_cursor(self) -> None:
        """Get the pointer off the map before reading it.

        The bridge corrupts its own evidence otherwise, and it did: after a
        successful map click the cursor STAYS where SendInput left it, which is
        on top of a room. DD then draws a hover border round that room and a
        "Move to this room" tooltip under it -- and the next frame the bridge
        grabbed had the room's cell merged with its neighbour (111 px tall
        instead of 40) plus thirteen letter-shaped boxes in a row. 19 cells
        instead of 6, and no fit. The banked frame is what showed it.

        `MENU_DEAD_ZONE` is the bottom-left corner, black on all nine menu
        captures and outside the panel entirely. Moving, never clicking, so
        this cannot itself do anything -- and best-effort, because a backend
        that cannot move the pointer (PostMessage's move works, but a dry run
        has nowhere to move it) must not stop the read.
        """
        try:
            self.actuator.backend.move(
                self.resolved.win.to_screen(*MENU_DEAD_ZONE))
        except Exception:                        # noqa: BLE001
            pass
        time.sleep(TOOLTIP_FADE)

    def _show_map(self, advice, area: str, dungeon) -> None:
        """Leave a room: bring the MAP up, then click the destination room.

        Fran's rule: "para salir de salas siempre se interactua con el mapa, y
        el mapa tiene que estar activo, o esta activo el mapa o el
        inventario". The two panels share one slot, `TAB` swaps them, and **the
        save says which is showing nowhere at all** -- so until
        `bridge/screen.py` existed the bridge could not even tell whether a
        click on the map would land on the map.

        **The target is the destination ROOM, not the corridor.** The game says
        so: "[CLICK] on a room in the map to begin moving to that room"
        (`str_help_raid_room_0`), and it walks the connecting hallway for you.
        `advice.next_step` is that hallway, so clicking it would be clicking
        something the map does not accept -- which is why `next_room` exists.

        The pixel is measured off the frame, never stored: the map zooms and
        pans (`[MOUSEWHEEL]`, `[RIGHT-CLICK]` + `[DRAG]`, and an `Auto-Center
        Dungeon Map` option), and three real captures came out at 48.0, 15.3
        and 10.7 px per map unit. `bridge/mappanel.py` recovers the transform
        from the cells that are drawn against the tiles the save says exist,
        and refuses unless the destination is sitting inside one of them.

        Deliberately not in `plan_room`: that is pure and has no window, which
        is what lets `--replay` run it over the capture corpus with the game
        closed. Panel state can only come from a screenshot.
        """
        try:
            from bridge import screen as screen_mod
            from bridge import mappanel
        except Exception as exc:                  # noqa: BLE001
            self._say(f"  [act] [route] cannot read the screen: {exc}")
            return
        if not (self.armed and self._focused_or_wait()):
            return
        target = getattr(advice, "next_room", "") or ""
        self._refresh_layout()
        self._park_cursor()
        img = screen_mod.grab(self.window)
        showing = screen_mod.panel(img, self._raid_is_running())
        if not showing and screen_mod.title_card_is_up(img):
            # **The dungeon's title card is still up.** Measured 2026-09-23:
            # the embark walk ended before the Cove card was recognised, and
            # the route refused "cannot tell which panel is up" for seven
            # minutes on a screen that wanted one SPACE.
            self._say("  [act] [route] the dungeon title card is up - SPACE")
            self.actuator.execute(InputPlan(
                (Step("key", vk=VK_SPACE, label="dismiss the title card"),),
                "route", "the dungeon title card"))
            return
        if not showing:
            self._say("  [act] [route] cannot tell which panel is up - "
                      "not pressing TAB blind")
            return
        self._say(f"  [act] [route] the {showing} panel is up")
        if showing == "inventory":
            plan = InputPlan((Step("key", vk=VK_TAB, label="swap to the map"),),
                             "route", f"the map, to leave {area or 'this room'}")
            self._say(f"  [act] {plan}")
            if not self.actuator.execute(plan):
                return
            self.stats["acted"] += 1
            time.sleep(0.5)
            self._refresh_layout()
            self._park_cursor()
            img = screen_mod.grab(self.window)
            if screen_mod.panel(img, self._raid_is_running()) != "map":
                self._say("  [act] [route] TAB did not bring up the map")
                return
            self._say("  [act] [route] the map is up")

        point, why = (None, "the route named no room to walk to")
        if target and dungeon is not None:
            point, why = mappanel.locate(img, dungeon, target, here=area)
            # **The map panel keeps its zoom and pan between openings.** A fit
            # can therefore fail for no better reason than that the view is
            # zoomed in on the party: measured live at 2 cells drawn where the
            # same save, same second, gave 8 -- and `MIN_MATCHED` is 3.
            # Closing and reopening resets it to the whole map, so that is the
            # lever rather than the wheel; see `_reset_map_view`.
            # ONLY when the refusal is about the VIEW. `locate` refuses for
            # four reasons and two of them reopening cannot change: a room
            # with no coordinates in the save, and a room genuinely not drawn
            # because it has not been scouted. A refusal that quietly presses
            # keys first is not a refusal -- the rule
            # `test_an_unlocatable_room_is_refused` pins.
            #
            # **"nothing is drawn" belongs on THIS side and was on the other**,
            # which cost a raid: the panel was up and fully drawn a second
            # later, `find_boxes` came back empty on one transient frame, and
            # because the message does not contain "could not fit the map" the
            # reset was skipped and the turn given up on. An empty panel is the
            # most view-ish refusal there is -- reopening is exactly what fixes
            # a frame caught mid-animation.
            #
            # **"IS NOT DRAWN ON THE MAP" BELONGS HERE TOO, and it was the
            # one left out.** The note above reasons that a room which has
            # not been scouted cannot be made to appear by reopening -- true,
            # and it is not the common case. DD draws every room it has a
            # cell for, including unexplored ones, so the usual reason a
            # target is not drawn is that the VIEW is zoomed past it.
            # Measured live 2026-09-20 in a crypts raid: `rooD is not drawn`
            # at 5 cells, the hover phase confirmed nothing, the click went
            # out anyway and missed, and the reset on the NEXT poll found
            # **12 cells of 12 frames** and the click landed. Four polls and
            # one wasted click, every time, for a two-keystroke fix that was
            # already written.
            #
            # Bounded per destination, which is what keeps a genuinely
            # unscouted room from paying `MAP_RESET_WATCH` on every poll: the
            # first refusal for a given target resets, and the rest fall
            # through to the hover exactly as they did.
            if point is None and ("could not fit the map" in why
                                  or "nothing is drawn" in why
                                  or ("is not drawn on the map" in why
                                      and target not in self._view_reset_for)):
                self._view_reset_for.add(target)
                again = self._reset_map_view(screen_mod, mappanel,
                                             dungeon, target, here=area)
                if again is not None:
                    img, point, why = again
        # **THE FIT IS A PREFERENCE AND THE GAME IS THE AUTHORITY.** A
        # refusal here used to end the turn, and a live raid sat in exactly
        # that state until the iteration timed out. See
        # `_ask_the_game_which_cell` for the measurement.
        offered_by_hover = False
        if point is None and target and dungeon is not None and area:
            spot = self._ask_the_game_which_cell(screen_mod, mappanel, img,
                                                 dungeon, area, target)
            if spot is not None:
                point = spot
                why = "the fit could not place it; the game offered this cell"
                offered_by_hover = True
        if point is None:
            self.stats["refused"] += 1
            self._say(f"  [act] [route] NOT clicking the map: {why}")
            self._bank_map(img, screen_mod)
            return

        # **RE-LOCATE ON A FRESH FRAME, because the one this point came from
        # is not necessarily the one the click will land on.**
        #
        # `_reset_map_view` presses TAB twice and then scans for
        # `MAP_RESET_WATCH` seconds, keeping the frame with the most cells
        # behind a successful fit. That frame can be seconds old by the time
        # the click is built, and the panel animates while it settles -- so
        # the point describes a view that has since moved.
        #
        # A stale point does not fail loudly. It lands wherever the map now
        # draws that spot, the save does not change, and three send attempts
        # later the turn is lost. Measured live 2026-09-03 leaving `rooC` for
        # `rooF`: the click went to client (929, 602), the save never moved,
        # and the frame banked one minute afterwards fitted 7/7 and put
        # `rooF` at (928, 603) -- the point was right for the settled map and
        # wrong for the map that was on screen when it was sent.
        #
        # So the fresh fit WINS when there is one, and its absence is a
        # refusal rather than a reason to fall back on the stale point: a view
        # that will not fit now is exactly the view that swallows a click.
        # Skipped for a point the GAME just confirmed by tooltip: the
        # re-measure exists to catch a stale fit, and there is no fit here to
        # be stale. Running it would refuse the move for the same reason the
        # fit refused it in the first place, which is the deadlock this whole
        # branch exists to break.
        if target and dungeon is not None and not offered_by_hover:
            # **SAMPLE, do not snapshot.** The panel animates continuously --
            # the party token pulses and the view eases after a reset -- so a
            # single fresh grab lands on an unfittable frame often enough to
            # block the layer entirely: measured live, the reset scan found a
            # fit over 11 frames and the very next grab did not, and the raid
            # stalled on "the map moved while planning". Trying a handful over
            # about a second is the same tactic `_reset_map_view` already
            # uses, and it keeps the guard's point -- the click still goes to a
            # fit measured moments before it, never to the planning one.
            # **The BEST of the samples, not the first that fits.** A fit is
            # not pass/fail: `MIN_EXPLAINED` is 0.75, so a 6-of-8 frame is
            # accepted and a 9-of-9 frame is accepted, and they do not agree.
            # Measured live on one room: 9/9 put `rooE` at (890, 634) and
            # clicked it, 6/8 put it at (818, 613) and missed by 72 px. Taking
            # the first acceptable frame is a coin flip between them; taking
            # the best is the same rule `_reset_map_view` uses one call
            # earlier, where the widest view wins.
            fresh = again = None
            why_again = "no map frame to re-measure"
            best_seen = (-1, -1.0)
            prev_got = None
            for attempt in range(MAP_REFIT_TRIES):
                if attempt:
                    time.sleep(MAP_REFIT_STEP)
                shot = screen_mod.grab(self.window)
                if shot is None or screen_mod.panel(
                        shot, self._raid_is_running()) != "map":
                    continue
                if fresh is None:
                    fresh = shot                 # something to bank on refusal
                got, note = mappanel.locate(shot, dungeon, target, here=area)
                if got is None:
                    why_again = note
                    continue
                # Scoring the frame is a PREFERENCE, never a gate: if it
                # cannot be computed the loop still behaves as it did before,
                # taking the first frame that located the room. Guarded for
                # that reason and not for tidiness.
                try:
                    shape = mappanel.fit(mappanel.find_boxes(shot),
                                         mappanel.tiles_of(dungeon),
                                         mappanel.kinds_of(dungeon))
                    rank = ((shape.matched, shape.explained) if shape
                            else (0, 0.0))
                except Exception:                        # noqa: BLE001
                    rank = (0, 0.0)
                if rank > best_seen:
                    best_seen, again, why_again, fresh = rank, got, note, shot
                # **A STILL view beats the best-scoring one.** The panel eases
                # after a reset, and the best-scoring frame can be one taken
                # BEFORE it settled. Live 2026-09-23 in the Ruins: the party
                # room sat at client (786, 548) in the fitted frame and at
                # (855, 587) when the click landed, so `rooE` was clicked on
                # empty grid and the raid sat in `rooA`. Two consecutive fits
                # agreeing on the target means the view has stopped: take
                # that one and stop sampling.
                if prev_got is not None and max(
                        abs(got[0] - prev_got[0]),
                        abs(got[1] - prev_got[1])) <= MAP_POINT_TOLERANCE:
                    again, why_again, fresh = got, note, shot
                    break
                prev_got = got
            if fresh is not None:
                if again is None:
                    self.stats["refused"] += 1
                    self._say(f"  [act] [route] the map would not fit on any "
                              f"of {MAP_REFIT_TRIES} fresh frames: {why_again}")
                    self._bank_map(fresh, screen_mod)
                    return
                moved = max(abs(again[0] - point[0]), abs(again[1] - point[1]))
                if moved > MAP_POINT_TOLERANCE:
                    self._say(f"  [act] [route] the map moved while planning "
                              f"({moved:.4f} of the frame) - taking the "
                              f"fresh fit")
                point, img, why = again, fresh, why_again

        plan = InputPlan((Step("click", screen=self.resolved.win.to_screen(*point),
                               label=f"{target} on the map"),), "route",
                         f"leave {area or 'this room'} for {target} ({why})")
        # **ASK THE GAME BEFORE SPENDING THE CLICK.** A destination it will
        # not accept is indistinguishable, from the save alone, from a
        # coordinate that missed -- and this layer spent a whole raid unable
        # to tell those apart. The tooltip separates them for free.
        offered = True if offered_by_hover else self._map_offers_move(
            screen_mod, mappanel, self.resolved.win.to_screen(*point))
        if offered_by_hover:
            # **SAY WHICH ONE IT WAS.** The hover phase already asked, and it
            # may have come back a refusal that the last resort overrode -- so
            # printing "the game offers the move" here would be the log
            # claiming a confirmation the game never gave. Same discipline as
            # `Did(sent=, confirmed=)`: a send that was not acknowledged is
            # reported as a send.
            self._say(f"  [act] [route] clicking the cell the hover phase "
                      f"chose for {target}")
        elif offered:
            self._say(f"  [act] [route] the game offers the move to {target}")
        elif offered is False:
            # **The fit ORDERS the candidates; the game PICKS.** That is the
            # inversion this whole layer needed. Every failure tonight was a
            # transform that was confident and wrong -- 6/8 explained putting
            # `rooE` 72 px from where 9/9 put it -- and no amount of tightening
            # the fit fixes a measurement whose only check is a 15 s silence.
            # Asking the game about the fit's near misses costs a hover each
            # and does not care whether the fit was exactly right, only
            # whether it was close.
            near = self._nearby_cells(screen_mod, mappanel, img, point,
                                      dungeon)
            for spot in near[:MAP_CANDIDATES]:
                at = self.resolved.win.to_screen(*spot)
                if self._map_offers_move(screen_mod, mappanel, at) is True:
                    self._say(f"  [act] [route] the fit's own point was "
                              f"refused; the game offers this one instead")
                    point = spot
                    plan = InputPlan(
                        (Step("click", screen=at,
                              label=f"{target} on the map"),), "route",
                        f"leave {area or 'this room'} for {target} "
                        f"(offered, {why})")
                    offered = True
                    break
            if not offered:
                # **THE SAVE-ORDERED PHASE IS THE FALLBACK HERE TOO, and its
                # absence is what a live raid stalled on.** When the fit
                # produces NO point, `_ask_the_game_which_cell` runs and ends
                # in a documented last resort -- *"a click that misses costs
                # one poll, never clicking costs the raid"*. When the fit
                # produces a point the game then REFUSES, there was no such
                # path: this branch refused and returned, every poll, forever.
                #
                # Live 2026-09-20, the last thing the overnight loop did:
                # `the game offers no move at or near rooE - not clicking
                # blind`, then six identical room plans and the stall line.
                # The party had somewhere to go and the layer that knows how
                # to find it was never asked.
                #
                # It is the same asymmetry the last-resort comment already
                # argues against, one branch along. The ordering it uses comes
                # from the SAVE -- the party's own cell found by its torch,
                # and the direction to the destination in map units -- not
                # from the transform that was just refused.
                spot = self._ask_the_game_which_cell(
                    screen_mod, mappanel, img, dungeon, area, target)
                if spot is not None:
                    at = self.resolved.win.to_screen(*spot)
                    self._say(f"  [act] [route] the fit's point and its "
                              f"neighbours were all refused; falling back to "
                              f"the save-ordered search")
                    point = spot
                    plan = InputPlan(
                        (Step("click", screen=at,
                              label=f"{target} on the map"),), "route",
                        f"leave {area or 'this room'} for {target} "
                        f"(save-ordered, {why})")
                    offered = True
            if not offered:
                self.stats["refused"] += 1
                self._say(f"  [act] [route] the game offers no move at or "
                          f"near {target} - not clicking blind")
                self._bank_map(img, screen_mod)
                return

        self._say(f"  [act] {plan}")
        # **ONE attempt. A map coordinate does not survive its own miss.**
        # `_send_and_confirm` re-sends the identical plan up to three times,
        # which is right for a keystroke and wrong here: the point was
        # measured off a panel, and a click that missed may itself have
        # dragged the view -- so the retries go to a coordinate that now names
        # somewhere else, and each one can pan it further. Seen live as three
        # attempts into a panel that had gone blank.
        #
        # Refusing after one lets the next poll re-centre and re-measure,
        # which is the only thing that can produce a correct point.
        if self._send_and_confirm(plan, attempts=1):
            self.stats["acted"] += 1
        else:
            # The click landed somewhere and the party did not move. Bank the
            # frame: the transform is the only new thing in this path, and a
            # miss is the evidence needed to tighten it.
            self._bank_map(img, screen_mod)
            # **THEN PUT THE VIEW BACK ON THE PARTY.** Fran, watching a
            # stalled raid: *"you did drag the map I think, you should center
            # on the team. if you drag you lose the map"* -- and the banked
            # frame agrees, showing EMPTY GRID where rooms had been. A click
            # that misses can leave the panel panned off the explored map, and
            # a panned view is worse than a broken one: it still FITS, on the
            # part of the grid it can see, and hands the next poll a confident
            # point for the wrong place.
            #
            # Recentring here rather than before the locate is deliberate. The
            # rule that a refusal must not press keys first stands -- an
            # unscouted room is not findable and reopening cannot make it so
            # (`test_a_room_that_is_NOT_DRAWN_still_refuses_without_pressing
            # _keys`). This is not a refusal: a click was really sent and
            # really missed, and leaving the view where that put it is what
            # loses the map.
            self._reset_map_view(screen_mod, mappanel, dungeon, target, here=area)

    # DO NOT press `W` here as a fallback. Tried on 2026-09-01 and REVERTED
    # the same hour, unflown, because Fran named the geometry that makes it
    # harmful: **a hallway's entrance is on the LEFT and its exit on the
    # RIGHT, and the party always advances rightward.**
    #
    # The stall frame that prompted it (`iter_20260901_003327_002_stall.png`)
    # shows the party at the far left with a barred gate beside them. That
    # gate is the entrance they ARRIVED through, not the way on -- so `W`
    # there walks them BACKWARDS into the previous area. `_interact_curio`'s
    # docstring already records exactly this failure from a live run: "the
    # party enters a corridor standing at the door it just came through, so
    # the one thing in front of it is the room it just left... it exited
    # properly but immediately used W instead of D".
    #
    # The lesson is not "W is wrong" -- it opens doors, and the game says so.
    # It is that a door has a SIDE, and nothing in this path knows which side
    # the party is on. Direction is what that needs, and `Heading` is where it
    # would come from.

    def _reset_map_view(self, screen_mod, mappanel, dungeon, target, here=""):
        """TAB away and back, and return the best frame of the animation.

        `(img, point, why)`, or None if the panel never came back.

        The panel is stateful -- it keeps whatever zoom and pan it was left
        in, and DD re-centres it on the party. Measured on ONE save seconds
        apart, knowledge byte-identical in both: **8 cells drawn, then 2**. At
        2 the fit correctly refuses (`MIN_MATCHED` is 3) and the party cannot
        leave the room.

        **Reopening is a zoom ANIMATION, not a state change, and that is why
        this samples instead of sleeping.** Measured after one reopen:

            +0.01s  2 cells      +2.06s  8
            +0.52s  5            +2.57s  5
            +1.03s  8            +3.08s  2
            +1.55s  8

        The whole map is on screen for roughly a second and a half in the
        middle. A fixed delay would land in it by luck; this walks frames,
        which is the same "verify, do not time" rule `_send_and_confirm`
        follows.

        **The frame it keeps is the MOST-SUPPORTED success, and getting there
        took both wrong answers first.** Keeping the widest frame refused a
        click: the widest fitted at 8.8 px/unit on 6 of 8 boxes and predicted
        the destination outside every detected cell. Keeping the FIRST success
        then sent a click that missed: it resolved on frame 1, still zoomed
        in, where 5 cells admit a fit that is self-consistent and wrong -- a
        small cell count can support a plausible scale that a wider view
        immediately contradicts.

        So the whole window is scanned, successes are collected, and the one
        with the most cells behind it wins. Cell count is the tie-break among
        failures too, so the frame banked on a refusal is still the most
        informative one.

        The wheel is NOT the lever here even though `SendInputBackend.scroll`
        works: scrolling is relative to wherever the view already is and has
        no stop, so it zoomed the panel further in and left it stuck. `TAB` is
        the game's own documented map/inventory toggle, so this costs two
        keystrokes and no coordinates.
        """
        if not self.armed:
            return None
        plan = InputPlan((Step("key", vk=VK_TAB, label="close the map"),
                          Step("key", vk=VK_TAB, label="reopen it, reset"),),
                         "route", "reset the map view to show the whole map")
        self._say_once("map-reset", f"  [act] {plan}")
        if not self.actuator.execute(plan):
            return None
        self.stats["acted"] += 1
        self._park_cursor()
        best, best_cells, fallback, fallback_cells, seen = None, -1, None, -1, 0
        deadline = time.time() + MAP_RESET_WATCH
        while time.time() < deadline:
            img = screen_mod.grab(self.window)
            if img is not None and screen_mod.panel(
                    img, self._raid_is_running()) == "map":
                seen += 1
                cells = len(mappanel.find_boxes(img))
                point, why = mappanel.locate(img, dungeon, target, here=here)
                if point is not None:
                    if cells > best_cells:
                        best, best_cells = (img, point, why), cells
                elif cells > fallback_cells:
                    fallback, fallback_cells = (img, point, why), cells
            time.sleep(MAP_RESET_STEP)
        if best is not None:
            self._say(f"  [act] [route] best map view: {best_cells} cells "
                      f"of {seen} frame(s)")
            return best
        if fallback is not None:
            self._say(f"  [act] [route] widest map view was {fallback_cells} "
                      f"cells over {seen} frame(s), and still no click")
        return fallback

    def _party_cell(self, mappanel, img, boxes):
        """Which drawn cell the party is standing in, by the torch it carries.

        The one thing on the map panel that is warm and lit. Returns the box or
        None, and None is a refusal rather than a guess -- everything built on
        this is a direction, and a direction from the wrong origin points
        somewhere confidently wrong.
        """
        try:
            import numpy as np                               # noqa: PLC0415
        except Exception:                                    # noqa: BLE001
            return None
        best, best_warm = None, 0.0
        for b in boxes:
            x0, x1, y0, y1 = int(b.x0), int(b.x1), int(b.y0), int(b.y1)
            if x1 - x0 < 6 or y1 - y0 < 6:
                continue
            patch = np.asarray(img[y0:y1, x0:x1], dtype="float32")
            if patch.size == 0:
                continue
            red, green, blue = patch[..., 0], patch[..., 1], patch[..., 2]
            warm = float(((red > green + 25) & (green >= blue)
                          & (red > 90)).mean())
            if warm > best_warm:
                best, best_warm = b, warm
        return best if best_warm >= PARTY_CELL_WARM else None

    def _ask_the_game_which_cell(self, screen_mod, mappanel, img, dungeon,
                                 here: str, target: str):
        """The fit could not place the destination. Ask the GAME instead.

        **This is the inversion the layer already believes in, applied one
        step earlier.** `_nearby_cells` says it out loud -- "the fit ORDERS the
        candidates; the game PICKS" -- but it only ran once the fit had
        produced a point the game then refused. When the fit produces NO point
        the caller refused outright, and that is the state a live raid sat in:
        Fran, watching it, *"it needs to click on the room below ... when it
        sets the mouse on it and then tooltip shows it's not clicking. never
        leaving the room"*.

        The fit deserves that demotion here. Measured on the frame it stalled
        on: 33 cells drawn, `find_boxes` returning twelve three-pixel slivers
        and missing most of the corridor tiles, and `fit` either refusing
        outright or -- on the settled view -- returning **38.1 px/unit against
        a true 11.4**, which predicts every room hundreds of pixels off the
        panel. A fit that wrong cannot order anything, and it does not have to:
        the tooltip is a positive permission straight from the game.

        **The anchor is the party's own cell and the ordering is the DIRECTION
        the save says to go.** Both come free: the party's cell is the warm one
        (`_party_cell`), and `mappos` gives the offset from here to there in
        map units. Screen y and map y run the same way, so the sign needs no
        flip -- checked on the stalling frame, where `rooE` at map y -7 is
        drawn 80 px above `rooJ` at map y 0.

        Room-sized cells only. The game walks the connecting hallway itself,
        so a corridor tile is never a destination, and each hover costs a
        second of a budget that is `BLIND_CANDIDATES` long.
        """
        try:
            boxes = mappanel.find_boxes(img)
        except Exception:                                    # noqa: BLE001
            return None
        if not boxes:
            return None
        mine = self._party_cell(mappanel, img, boxes)
        if mine is None:
            self._say("  [act] [route] cannot tell which cell the party is "
                      "in - not hovering the map blind")
            return None
        want = self._map_offset(dungeon, here, target)
        if want is None:
            return None

        # Room-sized: at least half the widest cell on the panel. A threshold
        # relative to what is DRAWN rather than an absolute pixel count, so it
        # survives the zoom the panel keeps between openings.
        widest = max(min(b.x1 - b.x0, b.y1 - b.y0) for b in boxes)
        mx, my = mine.centre
        cands = []
        for b in boxes:
            if b is mine or min(b.x1 - b.x0, b.y1 - b.y0) < widest * 0.5:
                continue
            cx, cy = b.centre
            dx, dy = cx - mx, cy - my
            gap = (dx * dx + dy * dy) ** 0.5
            if gap < 1.0:
                continue
            # Cosine against the direction the save says to travel. Negative
            # is the wrong way and is dropped rather than ranked last: the
            # budget is five hovers and a cell behind the party cannot be it.
            cos = (dx * want[0] + dy * want[1]) / (gap * (want[0] ** 2 + want[1] ** 2) ** 0.5)
            if cos <= 0.3:
                continue
            cands.append((-cos, gap, b))
        cands.sort(key=lambda c: (c[0], c[1]))
        if not cands:
            return None

        height, width = img.shape[:2]
        self._say(f"  [act] [route] the fit could not place {target}; asking "
                  f"the game about {min(len(cands), BLIND_CANDIDATES)} cell(s) "
                  f"in its direction")
        for _cos, _gap, b in cands[:BLIND_CANDIDATES]:
            cx, cy = b.centre
            spot = (cx / width, cy / height)
            at = self.resolved.win.to_screen(*spot)
            if self._map_offers_move(screen_mod, mappanel, at) is True:
                self._say(f"  [act] [route] the game offers the move at "
                          f"{at} - taking it for {target}")
                return spot

        # **LAST RESORT: TAKE THE BEST-RANKED CELL ANYWAY.**
        #
        # "Not clicking blind" is the right rule when blind means *unordered*,
        # and it was: the only ranking was a fit, and a wrong fit put `rooE`
        # 72 px from where a right one put it. It is the wrong rule now. The
        # ordering here comes from the SAVE -- the party's own cell found by
        # its torch, and the direction to the destination in map units -- so
        # the top candidate is a cell that lies the way the route says to go,
        # not a coordinate somebody's transform invented.
        #
        # And the two outcomes are not comparable in cost. A click that misses
        # is one poll: the save does not move, the next poll re-measures, and
        # `_send_and_confirm` is already capped at one attempt here precisely
        # because a map point does not survive its own miss. Never clicking
        # is the raid -- Fran, watching this exact room: *"never leaving the
        # room"*, and the two iterations before it spent their whole 900 s
        # standing in a corridor.
        #
        # The hover still gets first refusal, so this only fires when the
        # game declined to confirm ANY of them -- which the pulse measurement
        # says is itself sometimes the instrument rather than the game.
        _cos, _gap, b = cands[0]
        cx, cy = b.centre
        self._say(f"  [act] [route] the game confirmed none of them; taking "
                  f"the best-ranked cell toward {target} anyway - a click "
                  f"that misses costs one poll, never clicking costs the raid")
        return (cx / width, cy / height)

    def _map_offset(self, dungeon, here: str, target: str):
        """`(dx, dy)` in MAP units from one area to another, or None.

        Read off `Tile.mappos`, which is the save's own geometry, so it is
        independent of everything the panel is doing on screen.
        """
        try:
            a = dungeon.areas.get(here)
            b = dungeon.areas.get(target)
            here_pos = next((t.mappos for t in (a.tiles or ()) if t.mappos), None)
            there_pos = next((t.mappos for t in (b.tiles or ()) if t.mappos), None)
        except Exception:                                    # noqa: BLE001
            return None
        if not here_pos or not there_pos:
            return None
        dx, dy = there_pos[0] - here_pos[0], there_pos[1] - here_pos[1]
        return None if (dx == 0 and dy == 0) else (dx, dy)

    def _nearby_cells(self, screen_mod, mappanel, img, point,
                      dungeon=None) -> list:
        """Drawn cells near `point`, best first, as client fractions.

        The fit's own answer is already excluded -- the caller has just been
        told no about it -- so these are its near misses: the cell it merged
        with a neighbour, or the one it placed a row over. Read off the SAME
        frame the fit used, so a panel that has since moved cannot put a
        candidate somewhere the pointer will not find it.

        **ROOM-SIZED CELLS COME FIRST when the frame can be fitted**, because
        only a room is a legal destination -- the game walks the connecting
        hallway itself, which is why `RoutePlan.next_step` is not a click
        target. Each candidate costs a hover of about a second, and `MAP_CAN-
        DIDATES` is 4, so spending them on corridor tiles the game will never
        offer is most of the probe budget. Distance still orders within each
        group; the kind only decides which group.

        Everything about the kind is inside the guard and degrades to plain
        nearest-first: this runs mid-raid and a classification that cannot be
        computed must not cost the move.
        """
        try:
            height, width = img.shape[:2]
            boxes = mappanel.find_boxes(img)
        except Exception:                                # noqa: BLE001
            return []
        kinds = {}
        if dungeon is not None:
            try:
                from bridge import mapicons
                shape = mappanel.fit(boxes, mappanel.tiles_of(dungeon),
                                     mappanel.kinds_of(dungeon))
                if shape is not None:
                    kinds = {id(b): mapicons.classify_size(max(b.size),
                                                           shape.scale)
                             for b in boxes}
            except Exception:                            # noqa: BLE001
                kinds = {}
        px, py = point[0] * width, point[1] * height
        out = []
        for b in boxes:
            cx, cy = b.centre
            gap = ((cx - px) ** 2 + (cy - py) ** 2) ** 0.5
            if gap < 2.0:                                # the fit's own point
                continue
            kind = kinds.get(id(b))
            # 0 room, 1 unknown-or-unclassified, 2 hall. An UNKNOWN cell is a
            # merge, and a merge often HOLDS the room -- so it outranks a
            # confident hall rather than being dropped.
            rank = 1 if kind is None else {"room": 0, "hall": 2}.get(kind.kind, 1)
            out.append((rank, gap, (cx / width, cy / height)))
        out.sort(key=lambda row: (row[0], row[1]))
        return [spot for _rank, _gap, spot in out]

    def _map_offers_move(self, screen_mod, mappanel, at) -> Optional[bool]:
        """Hover `at` and ask the GAME whether that move is allowed.

        Fran, on a raid that would not leave its room: *"if you don't get the
        tooltip the game won't let you go to that path"*. Hovering a legal
        destination draws "Move to this room"; hovering anything else draws
        nothing. That is a POSITIVE permission, and it replaces the worst
        acknowledgement in the bridge -- click, then wait 15 s for a save
        change that never comes, three times over.

        It is read differentially (`screen.what_appeared`), so nothing about
        the tooltip is hardcoded: not its position, not its colour, not its
        wording. The last one matters -- this module already treats the game's
        language as unknown, and a detector keyed on "Move to this room" would
        work on exactly one install.

        **The pointer is parked FIRST**, because the "before" frame has to be
        one with no tooltip on it -- and parking is also what makes the fit
        upstream trustworthy, since the tooltip is drawn OVER the map and
        occludes the cells underneath.

        `None` means the question could not be asked (no pointer control, no
        frames) and is deliberately distinct from `False`, which is the game
        saying no: the caller falls back to clicking blind on `None` rather
        than refusing a move it simply could not check.
        """
        move = getattr(self.actuator.backend, "move", None)
        if not callable(move) or not self.actuator.enabled:
            return None
        try:
            self._park_cursor()
            time.sleep(TOOLTIP_SETTLE)
            before = screen_mod.grab(self.window)
            if before is None or not move(at):
                return None
            # **SAMPLE THE PULSE, do not take one look at it.** See
            # TOOLTIP_SAMPLE_FOR: the highlight fires about every 1.4 s, so a
            # single grab at a fixed offset found it one time in six and the
            # layer read a granted permission as a refusal. Early exit on the
            # third hit, so an offered cell still answers in under a second
            # and only a refusal pays the whole window.
            h, w = before.shape[:2]
            box = mappanel.panel_rect(w, h)
            hits, got, deadline = 0, None, time.time() + TOOLTIP_SAMPLE_FOR
            while time.time() < deadline and hits < TOOLTIP_MIN_HITS:
                time.sleep(TOOLTIP_SAMPLE_STEP)
                after = screen_mod.grab(self.window)
                if after is None:
                    continue
                seen = screen_mod.what_appeared(before, after, box=box)
                if seen is not None:
                    hits += 1
                    got = seen
            if hits < TOOLTIP_MIN_HITS:
                got = None
        except Exception:                                # noqa: BLE001
            # EVERYTHING is inside the guard, including reading the frames'
            # own shape. This runs before every map click and must degrade to
            # "could not ask" rather than to an exception -- a frame that is
            # not an image is exactly what a stubbed or headless caller has.
            return None
        return got is not None

    def _bank_map(self, img, screen_mod) -> None:
        """Save the map frame that could not be acted on.

        The pixels that would settle whatever just went wrong are on screen at
        exactly the moment the bridge gives up, so it keeps them rather than
        asking a human to alt-tab and reproduce the moment. One fixed filename,
        overwritten: a run that stalls in six rooms must not leave six
        near-identical PNGs in the directory that holds the reference corpus.
        Best-effort -- Pillow is a dev-only import, and a failed write costs the
        screenshot and never the message saying why the run stopped.
        """
        shot = Path(__file__).resolve().parent.parent / "captures" / MAP_SHOT
        if screen_mod.save_png(img, shot):
            self._say(f"  [act] [route] map banked to captures/{MAP_SHOT}")

    def _raid_is_running(self):
        """Is the party in a raid at all? `None` when it cannot be known.

        `persist.raid.json` exists ONLY during a raid -- that absence is how
        `live_state.read_live_state` decides `in_raid` -- so this is one
        `stat()` with no parse. It exists because `screen.panel()` is a
        two-way test with no "neither" and answers "map" for a title screen;
        the pixels cannot settle it and the save can.

        `None` rather than `True` when there is no path to check, so a caller
        that cannot establish the answer keeps the old behaviour instead of
        being told a raid is running.
        """
        if self.raid_path is None:
            return None
        try:
            return Path(self.raid_path).exists()
        except OSError:
            return None

    def _target_end_index(self, from_area: str, target_area: str):
        """Which END of `from_area` the destination sits at: 0 or total-1.

        **An END, not a direction** -- and with `Walk.entry_index` that is
        enough to give the direction outright, because the party came in by
        one end and a destination at THAT end is behind it. Everything else
        is ahead, and ahead is `D`.

        Map units, so it is the save's own geometry and not a screen reading.
        None whenever anything is missing, and the caller then walks `D`.
        """
        try:
            from bridge.dungeon_map import read_dungeon_map    # noqa: PLC0415
            if self.raid_path is None or not from_area or not target_area:
                return None
            path = Path(str(self.raid_path).replace("raid.json", "map.json"))
            dungeon = read_dungeon_map(path)
            here = dungeon.areas.get(from_area) if dungeon else None
            there = dungeon.areas.get(target_area) if dungeon else None
            if here is None or there is None or len(here.tiles) < 2:
                return None
            goal = next((t.mappos for t in there.tiles if t.mappos), None)
            first = here.tiles[0].mappos
            last = here.tiles[-1].mappos
            if goal is None or first is None or last is None:
                return None
        except Exception:                                     # noqa: BLE001
            return None

        def gap(pos):
            return (pos[0] - goal[0]) ** 2 + (pos[1] - goal[1]) ** 2

        return 0 if gap(first) < gap(last) else len(here.tiles) - 1

    def _map_area(self, area: str):
        """The live `Area` record for one area, or None.

        Re-read rather than taken from the `Update`, because everything that
        uses it is inside a walk loop and needs the CURRENT save, not the one
        the poll started with.
        """
        if self.raid_path is None or not area:
            return None
        try:
            from bridge.dungeon_map import read_dungeon_map
            path = Path(str(self.raid_path).replace("raid.json", "map.json"))
            dungeon = read_dungeon_map(path)
            return dungeon.areas.get(area) if dungeon else None
        except Exception:                        # noqa: BLE001
            return None

    def _curio_active(self, area: str, curio_id: str):
        """Is that curio still standing? None when the save cannot say.

        `content` is the live state and `curio_prop` is the identity -- a
        consumed curio keeps its id and drops to `content == 0`. It is NOT the
        save MTIME: most curios are loot curios, and DD writes nothing until
        the loot is actually taken, which is why this reports rather than gates.
        """
        found = self._map_area(area)
        if found is None:
            return None
        hits = [t for t in found.tiles if t.curio_id == curio_id]
        return any(t.is_active for t in hits) if hits else None

    def _tiles_travelled(self) -> Optional[int]:
        """Tiles advanced from the end the party came in by: `raid.areatile`.

        The one position signal that survives a corridor being walked a second
        time -- `Area.standing_on` reads its direction off the dark run behind
        the party and a cleared corridor has none. Measured live 2026-09-06:
        exact and monotone there, 2 -> 3 under `D`.
        """
        if self.raid_path is None:
            return None
        try:
            from bridge.route import read_area_tile          # noqa: PLC0415
            return read_area_tile(self.raid_path)
        except Exception:                                    # noqa: BLE001
            return None

    def _walk_progress(self, area: str):
        """How far along this corridor the party is. None when unreadable.

        **Two independent signals, cross-checked, and it refuses when they
        disagree.** `raid.areatile` is the game's own count of tiles walked
        from the end the party entered by -- exact and monotone, but carrying
        no direction. `Area.standing_on` reads the direction off the dark run
        the party leaves behind it. Agreeing, they give an absolute tile.

        This replaces the per-tile `knowledge` run (`Area.walked`), which is
        kept only as the fallback. Knowledge marks tiles as REVEALED, not as
        walked: graded against `areatile` over the whole capture corpus it is
        right 172 times, **wrong 51**, and silent 45, while these two agree
        223 of 223. A corridor can read the same knowledge flags at two
        different positions (`coCD` gives `..#####` at both areatile 3 and 4),
        which is a walk that looks stalled while the party is moving -- and
        that is what stopped the tutorial curio walk two tiles short.
        """
        found = self._map_area(area)
        if found is None:
            return None
        from bridge.dungeon_map import Walk
        from bridge.route import read_area_tile
        tile = None
        if self.raid_path is not None:
            try:
                tile = read_area_tile(self.raid_path)
            except Exception:                    # noqa: BLE001
                tile = None
        end = found.standing_on
        if tile is not None and end is not None:
            absolute, from_front = end
            total = len(found.tiles)
            if (tile if from_front else total - 1 - tile) == absolute:
                return Walk(passed=tile + 1, total=total, from_front=from_front)
            # Two exact signals that disagree is not a position to steer by.
            # Refusing costs one walk; guessing walks somewhere wrong.
            return None
        # **THE DARK RUN IS GONE ON A SECOND PASS, AND THE ASSUMPTION UNDER
        # THE WHOLE WALK IS WHAT BROKE.** A fallback built here on 2026-09-06
        # -- pair `areatile` with `last_room_id` for an absolute tile -- was
        # reverted the same hour: walking up to the tutorial tent the derived
        # index ran **3, 2, 1, 0**, one press of `D` at a time, and
        # `_walk_up_to` aimed the party away from the curio for twelve
        # presses. A position that counts backwards is worse than none.
        #
        # Two live measurements, and together they say something sharper than
        # "the fallback was wrong":
        #
        #   * entered from `rooA`, pressed `D` 17 times -- the party ARRIVED
        #     IN `rooA`, and `areatile` fell 3, 2, 1, 0 on the way;
        #   * entered from `rooB`, pressed `D` 3 times -- `areatile` rose
        #     2 -> 3.
        #
        # `rooA` is `coAB` tile 0 by mappos (-10.5 against tiles -9.5 .. -4.5,
        # `reversed` False on every area), so BOTH runs have `D` moving the
        # party toward tile 0 -- toward `rooA` -- whichever end they came in
        # by. That also makes `areatile` entry-relative in both, exactly as
        # `read_area_tile` says: away from `rooB` is toward `rooA`.
        #
        # So the term that is wrong is not `areatile` and not
        # `_target_end_index`. It is the rule this whole method rests on --
        # `Walk.key_toward`'s "`D` always walks deeper and `A` always walks
        # back out the door it came in by". On a corridor being re-walked,
        # `D` appears to be a FIXED map direction instead. That rule is
        # Fran's, it is written down twice, and it is right for a first
        # traversal; nothing here can say yet whether re-entry genuinely
        # differs or whether the game re-anchors the hallway art on arrival.
        #
        # Until that is answered this stays silent, per the rule three lines
        # above: two signals that disagree are not a position to steer by, and
        # guessing walks somewhere wrong. The DIRECTION half is a weaker claim
        # -- "is the target at the end I came in by" -- it is validated live
        # in both directions, and it lives in `_walk_toward` and only there.
        #
        # The experiment that settles it: stand in a cleared corridor entered
        # from a KNOWN end, press `D` once, and read `areatile` and the area
        # you end up in. Two corridors, both entry ends, four runs.
        return found.walked

    def _torch_now(self):
        """The live torch level, or None when the save cannot be read."""
        peek = self._peek()
        return None if peek is None else peek.get("torch")

    def _torch_settled(self, before: float) -> tuple:
        """Re-read the torch, and say whether the answer means anything.

        Returns `(level, witnessed)`. `witnessed` is the half that matters and
        it did not exist until 2026-09-06: **an unchanged torch is only
        evidence when the save was REWRITTEN while we watched.**

        A raid save is written per TURN. A snuff is sent before the killing
        blow, which is inside a hero's turn, so the next write is the swing
        that has not happened yet -- and the window closes with the file
        untouched. That is not "the click did nothing", it is "nobody has
        answered", and reading it as the first is what refuted all eight
        candidates in one live run at torch 70 while saying nothing about any
        of them. The relight one layer up proved the lag directly: `T` landed
        and the save took 58 s to show it.

        So: the torch moving is a confirmation, the save moving with the torch
        unchanged is a refutation, and the save not moving at all is neither.
        """
        sig = self._save_sig()
        for _ in range(TORCH_CONFIRM_READS):
            time.sleep(TORCH_CLICK_GAP)
            now = self._torch_now()
            if now is not None and now != before:
                return now, True
        return before, sig is not None and self._save_sig() != sig

    def _torch_order(self):
        """Candidate torch points, the one that has already worked first.

        The sweep costs up to two clicks per dead candidate, which is fine
        once and wasteful every time -- and a snuff can be advised several
        times in one raid. So the winner is remembered for the life of the
        runner. It is deliberately NOT persisted: the answer belongs in
        `layout.TORCH_CANDIDATES` once a live run has named it, and a cache
        that quietly outlives the process would stop anyone noticing.
        """
        targets = torch_targets(self.resolved)
        # **A candidate that RAISED the torch is removed, not reordered.**
        # It is the USE control, so trying it again spends another torch and
        # lights the room; there is no ordering in which that is worth doing.
        raiser = getattr(self, "_torch_raiser", None)
        if raiser is not None:
            targets = tuple(t for t in targets if t[1] != raiser[1])
        found = getattr(self, "_torch_point", None)
        if found is None:
            return targets
        return tuple([found] + [t for t in targets if t[1] != found[1]])

    def _snuff_torch(self) -> bool:
        """Put the torch out, and CHECK -- sweeping until the game answers.

        **Three live runs have now sent this at a torch that did not move**,
        and each time the failure was silent in the worst way: the window
        accepted the click, the log said the torch was out, and the game
        disagreed. What was never separated is WHY, so both causes are closed
        at once and the log says which one it was.

        The gesture is not in doubt. `str_snuff_torch_tip` is
        `[SHIFT+CTRL+CLICK]` and `str_reduce_torch_tip` is `[SHIFT+CLICK]` on
        the same widget, and Fran confirmed the widget by hand: "if you spam
        shift+click on the torch icon the torch will lower". What was in
        doubt is the PIXEL (`layout.TORCH_CANDIDATES` now offers both readings
        of the shipped layout, and the game picks) and whether the MODIFIER
        arrived in time (`MOD_DWELL`).

        `persist.raid.json` carries `torchlight`, so this is one of the few
        actuations the bridge can have graded rather than assumed -- through
        `_torch_settled`, because that save is written per turn and reading it
        too eagerly is itself a way to refute a working click.

        Returns True when the torch actually reached 0. A False is reported
        and the turn is still played: a snuff that swallowed the attack would
        forfeit the very kill it exists to sweeten.
        """
        # **One failure a raid is enough.** Live 2026-09-24 the snuff was
        # swept, refuted and re-tried on fight after fight -- 16 times in two
        # raids, 32 dead clicks on the torch. Until the gesture is found, a
        # snuff that did not take once is not sent again this raid.
        if getattr(self, "_snuff_failed", False):
            self._say_once("snuff-off", "  [act] [torch] the snuff already "
                                        "failed this raid - not trying again")
            return False
        level = self._torch_now()
        if level is None:
            self._say("  [act] [torch] cannot read the torch - "
                      "not clicking blind")
            return False
        opened = level
        targets = self._torch_order()
        if not targets:
            self._say("  [act] [torch] no calibrated torch meter")
            return False
        # The candidate the sweep tries first: the remembered winner when the
        # runner has one, otherwise the best guess. It is the ONLY one allowed
        # a burst of clicks the save has not answered for.
        first = targets[0]

        for name, point in targets:
            if level <= 0.0:
                break
            # **THE FULL SNUFF IS N SHIFT-CLICKS, NOT ONE CTRL+SHIFT CLICK.**
            # Measured live 2026-09-21 against DD's own light tooltip:
            # CTRL+SHIFT sent through `SendInput` moves the light 25, exactly
            # like SHIFT -- `RADIANT LIGHT ( 100 )` -> `DIM LIGHT ( 75 )` --
            # because CTRL does not reach SDL even held for 0.35s. An earlier
            # reading in the same session called it a full snuff and is
            # RETRACTED: it was taken at light 25, where -25 and -100 both
            # land on 0 and the two cannot be told apart.
            #
            # Four SHIFT clicks at the dial took 75 -> `BLACK AS PITCH ( 0 )`
            # in one gesture, so that is what gets sent. `plan_reduce_torch`
            # already paces them at `TORCH_CLICK_GAP`.
            need = int(math.ceil(level / TORCH_STEP))
            snuff = plan_reduce_torch(
                self.resolved, min(need, TORCH_MAX_CLICKS), point)
            if not snuff.actionable:
                continue
            self._say(f"  [act] {snuff}")
            if not self.actuator.execute(snuff):
                return False
            self.stats["acted"] += 1
            level, witnessed = self._torch_settled(level)
            moved = level < opened

            # **A RISE IS NOT A MISS, AND SWEEPING ON AFTER ONE BURNS A TORCH
            # PER CANDIDATE.** This branch used to read `level > opened` as
            # "did not move" and try the next point, so a modifier that failed
            # to arrive could spend several torches raising the light to 100 in
            # the middle of the fight the snuff existed to darken. Measured
            # live 2026-09-21: 82 -> 100 on a killing blow. The candidate that
            # did this is the torch's USE control, so it is recorded as one to
            # keep away from rather than merely "not the widget".
            if level > opened:
                self._torch_raiser = (name, point)
                self._say(f"  [act] [torch] STOP - the torch went UP "
                          f"{opened:.0f} -> {level:.0f}. '{name}' is the USE "
                          f"control and the modifier did not arrive, so that "
                          f"click spent a torch and lit the room. Not trying "
                          f"another candidate.")
                return False

            if witnessed and not moved:
                # **This point is not the widget** -- and only because the
                # save answered. Nothing moved, so clicking it harder cannot
                # help; try the next candidate rather than spend the turn.
                # That is the whole reason there is more than one.
                self._say(f"  [act] [torch] {level:.0f} did not move - "
                          f"{name} is not the torch widget")
                continue

            # **A PARTIAL SNUFF IS WORTH ALMOST NOTHING, AND THAT IS WHAT WAS
            # BEING SENT.** `str_snuff_torch_tip` documents SHIFT+CTRL+CLICK
            # as a full snuff, but Fran has seen the game take it as the
            # SHIFT+CLICK reduction -- one 25-point step -- and the loop that
            # was meant to finish the job required a SAVE WRITE between every
            # click. A snuff is sent inside a hero's turn, so the next write
            # is the swing that has not happened yet: the first read came back
            # unwitnessed and the whole thing stopped after one click. Live
            # 2026-09-20 that left the torch at 20 with the log reporting it
            # had not moved at all. Fran: *"it only clicks once ... usually
            # you want full snuff of the torch for best value"*.
            #
            # The darkness table is why the difference is not marginal: at 1-25
            # a fight's bonus is +8 treasure draws and +15% gold, and at 0 it
            # is +12 and +30%. Stopping one step short gives up the bracket
            # the play exists to reach.
            #
            # So the remaining steps are sent as ONE plan and verified
            # afterwards, rather than each one being gated on an answer that
            # cannot arrive yet. Sending them blind is bounded twice: by the
            # torch level itself, and to the candidate the sweep would try
            # first -- which `_torch_order` makes the remembered winner when
            # there is one. A candidate the save has REFUTED never gets here.
            # **The first plan already sent the whole ladder**, so its clicks
            # count against the budget: starting this at 0 spent it twice and
            # sent eight clicks for a four-step snuff.
            sent = len(snuff.steps)
            while (level > 0.0 and sent < TORCH_MAX_CLICKS
                   and (moved or (name, point) == first)):
                need = int(math.ceil(level / TORCH_STEP))
                step = plan_reduce_torch(
                    self.resolved, min(need, TORCH_MAX_CLICKS - sent), point)
                if not step.actionable:
                    break
                self._say(f"  [act] {step}")
                if not self.actuator.execute(step):
                    return False
                self.stats["acted"] += 1
                sent += len(step.steps)
                after, witnessed = self._torch_settled(level)
                if after >= level:
                    # Either the widget stopped responding (witnessed) or
                    # nobody answered (not witnessed). Neither is a reason to
                    # click again: the first says it cannot go lower, the
                    # second says another click would tell us no more than
                    # the last one did.
                    break
                moved = True
                level = after

            if moved:
                # It IS the widget. Remember it, and SAY so -- this line is
                # how the constant gets back into `layout.TORCH_CANDIDATES`
                # instead of being rediscovered every raid.
                self._torch_point = (name, point)
                self._say(f"  [act] [torch] the widget is '{name}' at {point}")
                break
            if not witnessed:
                # **Stop, do not sweep on.** Every later candidate would be
                # read through the same silent save and refuted the same way,
                # so the remaining seven clicks buy no information and each
                # one is a live click into a running game. One live run spent
                # sixteen of them and learned nothing.
                self._say(f"  [act] [torch] {level:.0f} unchanged and the save "
                          f"has not been written since the clicks, so this says "
                          f"nothing about {name} - stopping the sweep")
                break

        if level <= 0.0:
            self._say(f"  [act] [torch] out ({opened:.0f} -> 0)")
            return True
        self._say("  [act] [torch] the torch did not go out - tried "
                  + ", ".join(n for n, _pt in targets))
        self._snuff_failed = True
        return False


    def _forward_key(self, area: str, walk=_UNREAD, toward=None) -> int:
        """The key that walks toward `toward`, or deeper when nothing is named.

        **THERE IS NOTHING TO LEARN HERE, and learning it was the bug.** This
        used to read a direction off the save (`Walk.heading`), keep a global
        `Heading` bit, flip that bit whenever a walk ended somewhere
        unexpected, and remember per corridor that the save had been wrong --
        four mechanisms for one question that does not exist. A hallway has no
        map direction. DD draws the party entering at the LEFT of the hallway
        art however the corridor runs on the map, so `D` advances and `A`
        backtracks, everywhere, always.

        Both live oscillations are explained by that and by nothing else:
        `corH`, where the save said `A` and the entrance was on the left, and
        `coEF`, where the party walked `A` the length of the corridor and came
        out in the room it had started in. Fran, watching the second:
        *"hallways have no directions, only left (backtrack) and right
        (advance)"*.

        `toward` is a tile index the party wants to reach, and it is the only
        thing that can ask for `A`: a curio already walked past is behind, and
        behind means back toward the door. `walk` is the caller's already-read
        position -- a walk loop has one, and re-reading decodes
        `persist.map.json` twice per step. `None` is a real value there
        ("read, and unreadable"), hence the sentinel default.
        """
        if toward is None:
            return VK_D
        if walk is _UNREAD:
            walk = self._walk_progress(area)
        if walk is None:
            return VK_D
        return VK_D if walk.key_toward(toward) == "D" else VK_A

    def _give_up_on_curio(self, area: str, curio_id: str, name: str,
                          why: str) -> None:
        """Stop trying this one for the rest of the raid, and say so once.

        The alternative is what happened on 2026-09-05: every re-plan restarts
        `CURIO_TRIES`, and a corridor sack ate all 900 seconds of an iteration
        while the route layer never got a turn. A curio is worth a few
        provisions; a raid is worth the run.
        """
        self.gave_up_on.add((area, curio_id or name))
        self._say(f"  [act] [curio] giving up on {name} for this raid - {why}")
        self._bank_curio(name)

    def _interact_curio(self, plan, advice, area: str) -> None:
        """Walk up to the curio, then `W` it, then take what it coughed up.

        **`W` acts on whatever the party is STANDING IN FRONT OF**, which the
        game says in as many words -- `tutorial_popup_using_doors_description`:
        "To enter a door, [CLICK] or press [W] when standing in front of it",
        and `str_help_raid_hallway_4` for objects. That makes an unconditional
        `W` on arrival in a corridor not merely useless but actively wrong: the
        party enters a corridor standing at the door it just came through, so
        the one thing in front of it is the room it just left. Seen live in the
        tutorial -- the bridge left `rooA` correctly, pressed `W` for `coAB`'s
        tent, and walked straight back into `rooA`. Reported as "it exited
        properly but immediately used W instead of D".

        **The walk is a loop now, and `Area.walked` is what earns it one.** A
        single held press covers well under one tile -- "it did walk a little
        but not even 1 square of the 4" -- and the first version stopped after
        one because there was no way to tell whether walking had achieved
        anything. There is: per-tile `knowledge` marks the tiles behind the
        party, so this walks until the party is standing on the curio's own
        tile, and stops early the moment progress stalls (an obstacle, or the
        curio itself blocking the way).

        **`_curio_active` still only reports.** The tempting gate -- press `W`,
        ask whether the curio went inactive, press again -- reads a success as
        a failure, because most curios are LOOT curios and DD writes nothing
        until the loot is taken. That is the same trap that made the generic
        three-attempt `_send_and_confirm` press `W` twice more into an open
        loot window, seen live on `coBC`'s sack.

        In a room there is nowhere to walk: the area is one tile and the party
        is standing on it, so the curio is already in front of them.

        The loot window is why this ends in `_take_loot` -- leaving it open
        swallows the walk that comes next, exactly as after a fight.
        """
        curio_id = getattr(advice, "curio_id", "") or ""
        name = curio_id or "the curio"
        if (area, name) in self.gave_up_on:
            # Said once, when it was given up on. Repeating it every re-plan
            # would be the same flood one message quieter.
            return
        if not self.armed:
            self._say(f"  [act] {plan}")
            return
        corridor = not in_a_room(area)
        # **NO PRE-POSITIONING.** `_walk_up_to` aimed at the curio's tile, and
        # Fran's instruction is to stop doing that: *"you can use W+D at the
        # same time and should stop on curio on interaction rather than
        # mapping out the coordinates"*. Three theories about the right
        # stopping distance have been falsified against the save, the last of
        # them on 2026-09-06 when a cleared corridor left no position to aim
        # with and the approach walked the party away from the tent for twelve
        # presses. A sweep has no stopping distance: it holds the interact key
        # while walking, and the INTERACTION is what stops it.
        self._say(f"  [act] {plan}")

        swept_from: Optional[int] = None
        walked = 0
        for attempt in range(1, CURIO_TRIES + 1):
            # Let the party come to a REST first. This is the whole difference
            # between the `W` that opens a door and the `W` that missed the
            # tent: at a door they have auto-stopped, mid-hallway they are
            # still gliding, and `W` acts on what is in front of them NOW.
            time.sleep(SETTLE)
            where = self._walk_progress(area) if corridor else None
            gone = self._tiles_travelled() if corridor else None
            # **WALKING BACK TOWARD THE DOOR IS THE ONE WAY THIS ENDS BADLY.**
            # `W` at the arrival gate walks the party out of the area, which
            # is the failure `plan_hallway_sweep`'s clearance exists to
            # prevent -- and clearance is checked once, before the first step.
            # `areatile` is distance from that door, so it FALLING mid-sweep
            # is the same danger arriving from the other side. Cheap, live,
            # and it needs no theory about which key goes which way: it is
            # measured between two attempts.
            if (gone is not None and swept_from is not None
                    and gone < swept_from):
                self._give_up_on_curio(
                    area, curio_id, name,
                    f"the sweep is walking back toward the door it came in by "
                    f"({swept_from} tiles in, now {gone})")
                return
            if gone is not None:
                swept_from = gone
            self._say(f"  [act] [curio] attempt {attempt}/{CURIO_TRIES} on "
                      f"{name}" + (f" from {where.line()}" if where else "")
                      + (f", {gone} tile(s) in" if where is None and gone
                         is not None else ""))
            if attempt == 1:
                # Unconditional, before anything can interrupt: this is the
                # only frame that shows where the curio sits relative to a
                # party standing next to it.
                self._bank_frame(CURIO_APPROACH_SHOT,
                                 f"{name} as the party sees it, first press")
            step = plan
            swept = False
            if corridor:
                # **WHEN THE SAVE CANNOT SAY, COUNT OUR OWN STEPS.** The
                # clearance test asks only "is the party still beside the door
                # it came in by", and after `walked` plain `D` presses it is
                # at least that many tiles from wherever this approach started
                # -- which is the same guarantee, established rather than
                # read. Without this the sweep refuses forever on a corridor
                # with no position and the curio is never touched at all: ten
                # walks and no interaction, which is worse than the aiming it
                # replaced.
                sweep = plan_hallway_sweep(
                    area, where, travelled=gone if gone is not None else walked)
                if sweep.refusal:
                    # Still beside the door the party arrived through, so `W`
                    # would walk them back out of the area. Plain `D` until
                    # clear, exactly as `plan_hallway_sweep` prescribes.
                    self._say(f"  [act] [curio] {sweep.refusal}")
                    step = InputPlan(
                        (Step("key", vk=VK_D, hold=WALK_HOLD, label="walk D"),),
                        "curio", f"clearing the door before D+W in {area}")
                    walked += 1
                else:
                    step, swept = sweep, True
            if not self.actuator.execute(step):
                return
            self.stats["acted"] += 1
            time.sleep(PANEL_WAIT)       # let the panel draw
            self._work_curio_panel(name, advice, area=area, curio_id=curio_id)
            worked, why = self._curio_landed(area, curio_id)
            if worked:
                self._say(f"  [act] [curio] {name}: {why}")
                return
            # **A ROOM CURIO GOT EXACTLY ONE PRESS, and one is not enough.**
            # The cap was `not corridor`, argued from "in a room there is
            # nowhere to walk" -- true, and beside the point: the retry is not
            # for walking, it is for the panel taking a moment to be drawn.
            # Live 2026-09-06 in `rooF`, with the quest done and a key in the
            # pack: `no panel on screen after W`, given up on for the whole
            # raid on the first attempt, and a curio panel closed by the
            # cleanup two minutes later -- so it did open, after the one look.
            # Fran: *"it skipped the chest, didn't interact with it"*.
            #
            # `W` in a room is safe to repeat: a room is one tile and the
            # party cannot walk out of it (that is why leaving needs the map),
            # so the press either opens the thing in front or does nothing.
            # Three, not ten: nothing is being approached, so a panel that has
            # not appeared by the third look is not going to.
            if attempt >= (CURIO_TRIES if corridor else ROOM_CURIO_TRIES):
                # The pixels are the only thing left. Three theories about
                # WHERE to press have now been falsified against the save, and
                # the save cannot show what the party is actually standing in
                # front of. Same trick that settled the map panel: bank the
                # frame at the moment of the failure, because asking a human to
                # alt-tab and reproduce it is a worse way to get the picture.
                self._give_up_on_curio(
                    area, curio_id, name,
                    f"no response after {attempt} press(es) - {why}")
                return
            if corridor and self._walked_past(area, curio_id):
                self._give_up_on_curio(
                    area, curio_id, name,
                    "it is behind the party now and walking further would go "
                    "into the door")
                return
            nores = getattr(self, "_curio_nores", None)
            if nores is None:
                nores = self._curio_nores = {}
            nores[name] = nores.get(name, 0) + 1
            self._say(f"  [act] [curio] {name} did not respond ({why}) - "
                      f"{'sweeping on' if swept else 'one more step'} and "
                      f"trying again")
            if swept:
                # The sweep IS the step. Nudging as well would move the party
                # twice per attempt and walk it past the curio in half the
                # tries -- which is `_walked_past`, i.e. the give-up below.
                continue
            if not self._nudge(area):
                # A fight starting mid-approach is the ORDINARY way out of this
                # loop, not an edge case -- and it used to leave no trace.
                self._bank_curio(name)
                return

    def _corridor_has_obstacle(self, area: str) -> bool:
        """Does `area` still hold an uncleared obstacle (tile content 4)?"""
        try:
            from bridge.dungeon_map import (CONTENT_OBSTACLE,   # noqa: PLC0415
                                            read_dungeon_map)
            if self.raid_path is None or not area:
                return False
            path = Path(str(self.raid_path).replace("raid.json", "map.json"))
            dungeon = read_dungeon_map(path)
            there = dungeon.areas.get(area) if dungeon else None
            return there is not None and any(
                getattr(t, "content", None) == CONTENT_OBSTACLE
                for t in there.tiles)
        except Exception:                                     # noqa: BLE001
            return False

    def _work_curio_panel(self, name: str, advice=None, area: str = "",
                          curio_id: str = "") -> bool:
        """`W` opened the panel. Click the hand, or the item, inside it.

        **This is the half that was missing.** `W` was never the problem: it
        opens the curio panel, and the bridge then sent SPACE, saw the curio
        still `active` and the pack unchanged, decided "did not respond", and
        pressed `W` again into an open panel -- six times a visit, for four
        sessions, while the party stood in exactly the right place.

        The panel offers a bare-handed interact (the hand), an item slot, and a
        red X. `bridge/curios.py` already decides *whether* a provision is
        worth spending and *which*; the slot is one pitch left of the X, so
        acting on that decision is now a click and not a drag.

        **A "use" curio is never taken bare-handed as a fallback.** The
        bare-handed outcome of a curio that wants holy water is often the bad
        one -- which is precisely why `curios.py` named an item -- so when the
        slot cannot be read this closes the panel and walks away instead.
        """
        if self.resolved is None:
            return False
        try:
            from bridge import curiopanel
            from bridge import screen as screen_mod
        except Exception:                        # noqa: BLE001
            return False
        # **THE PANEL ANIMATES IN, AND THIS USED TO LOOK ONCE.** A single grab
        # straight after `W` catches the frame before DD has drawn anything,
        # so a curio that opened perfectly reported "no panel on screen" --
        # and the caller then pressed `W` again, or gave up on it for the
        # raid.
        #
        # The evidence is the CLEANUP path, which looks on a later poll and
        # finds the very same panel: live 2026-09-07 in `rooA`, three attempts
        # at `locked_strongbox` all reporting no panel, "giving up on
        # locked_strongbox for this raid", and ninety seconds later
        # `a curio panel is still open - closing it at (1008, 339)`. Two
        # readers of one screen disagreeing is a timing bug, not a detector
        # bug -- the same tell the town layer's `PANEL_OPEN_SETTLE` exists for.
        found = None
        for look in range(CURIO_PANEL_READS):
            if look:
                time.sleep(PANEL_WAIT)
            try:
                img = screen_mod.grab(self.resolved.win)
            except Exception:                    # noqa: BLE001
                return False
            found = curiopanel.find(img)
            if found is not None:
                break
        if found is None:
            self._say(f"  [act] [curio] no panel on screen {CURIO_PANEL_READS} "
                      f"looks after W - either it did not open, or it is not "
                      f"where the detector looks")
            return False
        if not found.readable:
            # A red button with no gold hand plate beside it. Sixty of the
            # sixty-two frames in the corpus that pass the red-X test land
            # here, so this is the ordinary answer and not an alarm.
            self._say("  [act] [curio] something red on screen after W, but "
                      "the row around it is not a curio panel - leaving it")
            return False

        # **The OBSTACLE's panel, not this curio's.** Live 2026-09-24 in the
        # Warrens: walking to a sconce, the party met the rubble first, its
        # panel came up, and this path clicked the HAND -- cleared bare-
        # handed (-20 torch; 100 -> 56 over one corridor) with shovels in the
        # pack. A curio that takes no provision never lights an item slot,
        # so a lit slot while the corridor still holds an obstacle means the
        # panel is the obstacle's: `_work_obstacle_panel` uses the shovel.
        if area and found.sole_item() is not None                 and self._corridor_has_obstacle(area):
            try:
                from bridge import curios as _curios     # noqa: PLC0415
                cur = _curios.definition_for(curio_id or name)
                takes_items = bool(cur and cur.items)
            except Exception:                            # noqa: BLE001
                takes_items = True
            if not takes_items:
                self._say(f"  [act] [curio] a lit item slot on the way to "
                          f"{name}, which takes none, and {area} still holds "
                          f"an obstacle - that is the obstacle's panel")
                return self._work_obstacle_panel(area)

        # **An item-taking curio behind an OBSTACLE.** Live 2026-09-26: the
        # party met rubble on the way to an altar_of_light, the rubble's
        # panel came up, and holy water was right-clicked into it ten times
        # (nothing spent, nothing cleared, the altar abandoned). The guard
        # above only covers curios that take no item. Once a use has gone
        # unanswered with the corridor still blocked, the panel is the
        # obstacle's.
        if (area and self._corridor_has_obstacle(area)
                and (getattr(self, "_curio_nores", None) or {}).get(name, 0) >= 1):
            self._say(f"  [act] [curio] {name} did not answer and {area} still "
                      f"holds an obstacle - clearing the obstacle first")
            return self._work_obstacle_panel(area)
        want = (getattr(advice, "curio_item", "") or "") if advice else ""
        if want and getattr(advice, "curio_action", "") == "use":
            return self._use_on_curio(name, want, found)

        # `win.to_screen`, NOT `resolved._pt`. These fractions were measured on
        # the grabbed frame, which IS the client area, so they are already in
        # client space -- putting them through the letterbox correction (which
        # exists for layout constants calibrated at 16:9) would inset them
        # twice.
        pt = self.resolved.win.to_screen(*found.hand)
        self._say(f"  [act] [curio] panel is up - clicking the hand at {pt}")
        plan = InputPlan((Step("click", screen=pt, label="curio hand"),),
                         "curio", f"interact with {name} bare-handed")
        if not self.actuator.execute(plan):
            return False
        self.stats["acted"] += 1
        time.sleep(0.8)                          # let the interaction animate
        return True

    def _use_on_curio(self, name: str, item: str, found) -> bool:
        """Spend a provision on the curio in front of the party.

        The item slot is drawn on every panel -- `travellers_tent` has no item
        interaction at all and still reserves one -- so "a slot is there" is
        not "something is in it". `sole_item` returns a point only when exactly
        one slot is lit, which is the only case where the panel itself says
        which provision the click would spend.

        Refusing closes the panel. Leaving it standing is what stalled a whole
        raid once: it is modal over the world, so every keystroke after it goes
        into the panel and the route layer reports "no save change in 15s".
        """
        # **THE VERB IS A RIGHT-CLICK ON THE INVENTORY, and the bridge had
        # neither half of that.** `[pc]str_help_raid_room_6`: *"While
        # interacting with an object: [RIGHT-CLICK] an inventory item to
        # apply it to the object."* This used to LEFT-click a slot on the
        # CURIO PANEL -- wrong button and wrong widget -- so every loot curio
        # that wanted a key or a shovel was unreachable. Fran, 2026-09-21:
        # *"the model seems to be unable to use inventory items to right
        # click with curio, specially with loot curios"*.
        #
        # The pack slot comes from the SAVE, which names what is in every
        # cell, rather than from the panel's own item row: the save is exact
        # about WHICH item, and the panel only ever said how many slots were
        # lit. `sole_item` remains the fallback for a pack that cannot be
        # read.
        screen_pt, how = self._pack_slot_for(item)
        if screen_pt is None:
            pt = found.sole_item()
            if pt is None:
                lit = sum(1 for ok in found.usable if ok)
                why = ("the slot is empty or greyed out" if not lit
                       else f"{lit} slots are lit and nothing says which is {item}")
                self._say(f"  [act] [curio] {name} wants {item}, {how}, and "
                          f"{why} - closing the panel rather than guessing")
                self._close_curio_panel(found, f"cannot spend {item} on {name}")
                return False
            screen_pt, how = self.resolved.win.to_screen(*pt), "the panel's item slot"
        self._say(f"  [act] [curio] panel is up - right-clicking {item} on "
                  f"{how} at {screen_pt} for {name}")
        plan = InputPlan((Step("rclick", screen=screen_pt,
                               label=f"{item} on the curio"),),
                         "curio", f"use {item} on {name}")
        if not self.actuator.execute(plan):
            return False
        self.stats["acted"] += 1
        time.sleep(0.8)                          # let the interaction animate
        return True

    def _use_provisions(self, advice, area: str) -> None:
        """Use each provision the decide layer named, on its trigger.

        Measured live 2026-09-25: out of combat, clicking a hero's sprite
        selects them and a RIGHT-click on a pack cell uses that item on them
        (food 12 -> 11, the Musketeer 10 -> 12 HP); at full HP the game refuses
        the food and nothing is spent. So:

        * the INVENTORY panel must be the one up (TAB swaps it with the map --
          `_show_map` swaps back when it is time to leave);
        * every use is sent ONCE and witnessed by the pack count, since a
          provision is consumed and a retry on silence spends a second one;
        * food is capped per hero at `max_provisions_before_full` and a portion
          the pack did not lose marks that hero full for the raid.
        """
        from bridge import screen as screen_mod           # noqa: PLC0415
        from bridge.provision_use import MAX_FOOD_BEFORE_FULL  # noqa: PLC0415
        if not self._focused_or_wait():
            return
        sent = getattr(self, "_provisions_sent", None)
        if sent is None:
            sent = self._provisions_sent = {}
        full = getattr(self, "_food_full", None)
        if full is None:
            full = self._food_full = set()
        panel_ok = False
        for guid, rank, item, why in advice.provisions:
            key = (area, guid, item)
            if item == "food":
                eaten = sum(n for (a, g, i), n in sent.items()
                            if g == guid and i == "food")
                if guid in full or eaten >= MAX_FOOD_BEFORE_FULL:
                    continue
            elif sent.get(key):
                continue                  # one cure per hero per area
            if not panel_ok:
                self._park_cursor()
                img = screen_mod.grab(self.window)
                showing = screen_mod.panel(img, self._raid_is_running())
                if showing == "map":
                    self.actuator.execute(InputPlan(
                        (Step("key", vk=VK_TAB, label="swap to the pack"),),
                        "provision", "the pack, to use a provision"))
                    time.sleep(0.6)
                    showing = screen_mod.panel(screen_mod.grab(self.window),
                                               self._raid_is_running())
                if showing != "inventory":
                    self._say(f"  [act] [provision] cannot see the pack "
                              f"(panel: {showing or 'unknown'}) - not using {item}")
                    return
                panel_ok = True
            hero_pt = self.resolved.point("hero", int(rank) - 1)
            slot_pt, how = self._provision_slot(item)
            if hero_pt is None or slot_pt is None:
                self._say(f"  [act] [provision] no point for rank {rank} or "
                          f"{item} ({how}) - skipped")
                continue
            before = self._provision_count(item)
            self._say(f"  [act] [provision] {item} on rank {rank} - {why}")
            self.actuator.execute(InputPlan(
                (Step("click", screen=hero_pt, label=f"select rank {rank}"),
                 Step("rclick", screen=slot_pt, label=f"use {item}")),
                "provision", f"use {item}"))
            sent[key] = sent.get(key, 0) + 1
            self.stats["acted"] += 1
            after = before
            for _ in range(5):
                time.sleep(0.5)
                after = self._provision_count(item)
                if before is not None and after is not None and after < before:
                    break
            if before is not None and after is not None and after < before:
                self._say(f"  [act] [provision] used - {item} {before} -> {after}")
            elif item == "food":
                full.add(guid)
                self._say("  [act] [provision] the food was not eaten - "
                          "treating that hero as full")
            else:
                self._say(f"  [act] [provision] {item} count did not move "
                          f"({before} -> {after}) - sent once, not retried")

    def _use_battle_item(self, update) -> None:
        """Right-click the provision `run.py` named, on the acting hero's own
        turn -- a free action, so the skill plan follows unchanged. Sent once
        per turn and witnessed by the pack count; never retried."""
        from bridge import screen as screen_mod           # noqa: PLC0415
        item, why = update.battle_item
        key = ("battle-item", tuple(getattr(update, "turn_key", ()) or ()), item)
        if key in self.acted:
            return
        self.acted.add(key)
        img = screen_mod.grab(self.window)
        showing = screen_mod.panel(img, self._raid_is_running())
        if showing == "map":
            self.actuator.execute(InputPlan(
                (Step("key", vk=VK_TAB, label="swap to the pack"),),
                "provision", "the pack, for a battle item"))
            time.sleep(0.6)
            showing = screen_mod.panel(screen_mod.grab(self.window),
                                       self._raid_is_running())
        if showing != "inventory":
            self._say(f"  [act] [provision] cannot see the pack - not using {item}")
            return
        slot_pt, how = self._provision_slot(item)
        if slot_pt is None:
            self._say(f"  [act] [provision] no {item} to use ({how})")
            return
        before = self._provision_count(item)
        self._say(f"  [act] [provision] {item} before the skill - {why}")
        self.actuator.execute(InputPlan(
            (Step("rclick", screen=slot_pt, label=f"use {item}"),),
            "provision", f"use {item} in the fight"))
        self.stats["acted"] += 1
        time.sleep(1.0)
        after = self._provision_count(item)
        self._say(f"  [act] [provision] {item} {before} -> {after}"
                  + ("" if (before is not None and after is not None and after < before)
                     else " (the save may answer at the end of the turn)"))

    def _provision_slot(self, item: str):
        """`(screen point, how)` for the pack cell holding `item`. Food is the
        one supply the save files by TYPE ("provision") with no id."""
        try:
            from bridge.inventory import read_pack          # noqa: PLC0415
            if self.raid_path is None:
                return None, "no raid save"
            pack = read_pack(Path(self.raid_path))
        except Exception as exc:                            # noqa: BLE001
            return None, f"the pack could not be read ({exc})"
        hits = [sl for sl in pack if sl.amount > 0 and (
            sl.id == item or (item == "food" and sl.type == "provision"
                              and not sl.id))]
        if not hits:
            return None, f"the pack holds no {item}"
        return self.resolved.inventory_point(hits[0].slot), f"slot {hits[0].slot}"

    def _provision_count(self, item: str):
        pack = self._read_pack()
        if pack is None:
            return None
        return sum(sl.amount for sl in pack if sl.id == item or (
            item == "food" and sl.type == "provision" and not sl.id))

    def _pack_slot_for(self, item: str):
        """Where `item` sits in the raid pack. `(screen point, description)`.

        The SAVE names what is in every cell, so this is exact about which
        item is being spent -- the curio panel's own row only ever said how
        many slots were lit, which is why `sole_item` refuses whenever more
        than one is. `layout.inventory_point` is the same 2x8 grid the discard
        and stack-merge paths already use.
        """
        try:
            from bridge.inventory import read_pack          # noqa: PLC0415
            if self.raid_path is None:
                return None, "there is no raid save to read the pack from"
            pack = read_pack(Path(self.raid_path))
        except Exception as exc:                            # noqa: BLE001
            return None, f"the pack could not be read ({exc})"
        hits = [s for s in pack if s.id == item and s.amount > 0]
        if not hits:
            return None, f"the pack holds no {item}"
        at = self.resolved.inventory_point(hits[0].slot)
        if at is None:
            return None, f"{item} is in slot {hits[0].slot}, which is off the grid"
        return at, f"pack slot {hits[0].slot}"

    def _work_obstacle_panel(self, area: str) -> bool:
        """A panel came up where a door was expected. Clear the obstacle.

        The save does not name an obstacle -- `content == 4` and both
        `curio_id` and `trap_id` are None -- so which provision it wants comes
        from the ZONE, which `bridge/obstacles.py` derives from the shipped
        props. Every base zone draws one obstacle type and every one of those
        inherits `shovel_obstacle`; the Courtyard draws two that disagree, and
        there the tool is None and this clears it bare-handed.

        **Clearing it bare-handed is always available and always works**
        (`clear_on_failure: true`), at -5% max HP, -20 torch and 2 stress. So
        unlike a curio there is no walking away: the obstacle is standing in
        the corridor the party has to walk down. That is why the refusal path
        here uses the hand and the one in `_use_on_curio` uses the X.

        Returns True only when something was clicked, which is the signal to
        the caller that the `W` was consumed by a panel rather than a door.
        """
        if self.resolved is None:
            return False
        try:
            from bridge import curiopanel                  # noqa: PLC0415
            from bridge import screen as screen_mod        # noqa: PLC0415
            img = screen_mod.grab(self.resolved.win)
            found = None if img is None else curiopanel.find(img)
        except Exception:                                  # noqa: BLE001
            return False
        if found is None or not found.readable:
            return False

        advice = self._obstacle_advice()
        item = found.sole_item() if advice.action == "use" else None
        if item is not None:
            pt = self.resolved.win.to_screen(*item)
            self._say(f"  [act] [route] {area} is blocked and the panel is up "
                      f"- {advice.line()}")
            plan = InputPlan((Step("click", screen=pt,
                                   label=f"{advice.item} on the obstacle"),),
                             "route", f"clear the obstacle with a {advice.item}")
        else:
            if advice.action == "use":
                # The zone named a tool and the slot is not lit. Reaching here
                # means the pack read and the panel disagree, so believe the
                # panel -- it is the thing the click would land on.
                self._say(f"  [act] [route] the obstacle wants a "
                          f"{advice.item} and its slot is not lit - hands it "
                          f"is ({advice.cost.line()})")
            else:
                self._say(f"  [act] [route] {area} is blocked - "
                          f"{advice.line()}")
            pt = self.resolved.win.to_screen(*found.hand)
            plan = InputPlan((Step("click", screen=pt, label="clear by hand"),),
                             "route", "clear the obstacle bare-handed")
        if not self.actuator.execute(plan):
            return False
        self.stats["acted"] += 1
        time.sleep(1.0)                          # the clearing animates
        return True

    def _obstacle_advice(self):
        """What the zone says clears an obstacle, and what the pack holds.

        Both halves degrade rather than raise: an unreadable zone gives the
        bare-handed answer, which is the one that always works.
        """
        from bridge import obstacles                       # noqa: PLC0415
        zone, pack = "", {}
        try:
            if self.raid_path is not None:
                zone = obstacles.zone_from_save(self.raid_path)
            for slot in self._read_pack() or ():
                if slot.type == "supply":
                    pack[slot.id] = pack.get(slot.id, 0) + slot.amount
        except Exception:                                  # noqa: BLE001
            pass
        return obstacles.advise(zone, pack)

    def _close_curio_panel(self, found, why: str) -> bool:
        """Click the panel's X. Always safe, and the only always-safe click."""
        if self.resolved is None:
            return False
        pt = self.resolved.win.to_screen(*found.close)
        plan = InputPlan((Step("click", screen=pt, label="curio close"),),
                         "curio", why)
        if not self.actuator.execute(plan):
            return False
        self.stats["acted"] += 1
        time.sleep(0.6)
        return True

    def _dismiss_stray_curio_panel(self) -> bool:
        """Close a curio panel nobody is going to use.

        `W` opens the panel, and the bridge opens it whenever it walks up to a
        curio -- but when `bridge/curios.py` decides the curio wants an ITEM
        the actuator cannot spend, the plan walks away and leaves the panel
        standing. It is modal over the world: every keystroke after that goes
        into it.

        **Seen live, and the symptom names the wrong layer twice.** The party
        reached `pile_of_scrolls` in `coCF`; the plan correctly said "LEAVING
        pile_of_scrolls BEHIND: wants torch used on it, and only the
        bare-handed W is bound"; and then the torch relight and the door `W`
        each reported "no save change in 15s" three times over, while TAB and
        A/D kept working. Those two reach the game and the other two reached
        the panel -- so the log blamed the actuator for a modal nobody closed.

        Positive evidence only: nothing is clicked unless the detector
        actually finds a panel, so a frame it cannot read costs nothing.
        """
        if self.resolved is None or not self.armed:
            return False
        # The DETECTION is inside the guard as well as the grab. This runs on
        # every room, ahead of everything else, so a frame it cannot read has
        # to cost nothing at all -- and `find` is pixel arithmetic that will
        # raise on anything that is not an image.
        try:
            from bridge import curiopanel                  # noqa: PLC0415
            from bridge import screen as screen_mod        # noqa: PLC0415
            img = screen_mod.grab(self.resolved.win)
            found = None if img is None else curiopanel.find(img)
        except Exception:                                  # noqa: BLE001
            return False
        if found is None or not found.readable:
            # `readable` and not merely "an X was found". The red-X rule alone
            # fires on 62 of the 302 frames in this repo -- the pause menu,
            # the raid-results page, two dozen Hamlet screens -- and every one
            # of those would have taken a click in the world. Requiring the
            # hand's gold plate beside it leaves the two real panels.
            return False
        # `win.to_screen`, not `resolved._pt`: these fractions were measured
        # on the grabbed frame, which IS the client area, so the letterbox
        # correction would inset them twice. Same note as `_interact_curio`.
        pt = self.resolved.win.to_screen(*found.close)
        # Banked first: some of these are TRAP panels in a trapped corridor,
        # and a frame of one is what `traps.DISARM_MEASURED` waits for.
        self._bank_frame("_stray_panel.png", "a panel nobody asked for - a "
                         "curio, or a trap's disarm panel")
        self._say(f"  [act] [curio] a curio panel is still open - closing it "
                  f"at {pt}, it swallows every key while it stands")
        plan = InputPlan((Step("click", screen=pt, label="curio close"),),
                         "curio", "dismiss a curio panel nobody will use")
        if not self.actuator.execute(plan):
            return False
        self.stats["acted"] += 1
        time.sleep(0.6)
        return True

    def _tell_missed(self, asked) -> None:
        """Hand the recovery whatever the screen said, if anything.

        The callback took no argument for a long time, so this passes the
        class only when there is one and falls back to the bare call -- a
        bare `ActRunner` in a test or a caller with its own listener must not
        break because a keyword appeared.
        """
        if self.on_turn_missed is None:
            return
        try:
            if asked:
                self.on_turn_missed(asked)
            else:
                self.on_turn_missed()
        except TypeError:
            try:
                self.on_turn_missed()
            except Exception:                                # noqa: BLE001
                pass
        except Exception:                                    # noqa: BLE001
            pass

    def _asked_hero(self, update):
        """Which class the GAME is asking, or None when it cannot tell.

        The save is wrong about whose turn it is on about 2% of raid turns and
        the failure is silent and total -- see `bridge/heropanel.py`. Best
        effort, and None changes nothing.
        """
        if self.resolved is None:
            return None
        party = tuple(getattr(update, "party_classes", ()) or ())
        if len(party) < 2:
            return None
        try:
            from bridge import heropanel                     # noqa: PLC0415
            from bridge import screen as screen_mod          # noqa: PLC0415
            from bridge.townplan import GAME_DIR             # noqa: PLC0415
            return heropanel.read(screen_mod.grab(self.resolved.win),
                                  party, GAME_DIR)
        except Exception:                                    # noqa: BLE001
            return None

    def _read_bar(self):
        """The game's own action bar, or None when it cannot be read.

        Best effort in every sense, and None is the answer that changes
        nothing: `plan_battle` plans exactly as it did before this existed.
        That is deliberate -- a screen grab is a dev-only import and a display
        that will not read must cost the cross-check, never the turn.

        **READ IT TWICE AND REQUIRE AGREEMENT, because the save is ahead of
        the screen.** `run.py` polls on the save, so a turn is planned the
        instant the save says a new hero is up -- which is exactly when the
        screen is still finishing the previous one's animation and still
        drawing the PREVIOUS hero's bar. A single grab there reports that
        hero's lit slots, the greyed-slot guard sees nothing wrong, the digit
        goes out and selects nothing, and the click lands on an empty
        selection. The save never moves and 15 s burn.

        Measured 2026-09-06: a Duelist turn sent `KEY 1` twice into a slot the
        frame banked 15 s later showed at saturation **0.000** -- dead grey --
        while the same raid refused two OTHER slots correctly. Intermittent,
        which is the signature of a race rather than of a bad threshold; the
        reader itself was right about that frame when asked about it offline.

        Two consecutive reads agreeing on the four skill slots is the settle
        test, budgeted so a bar mid-animation costs a few hundred
        milliseconds and never the turn. Disagreement to the end of the budget
        returns None -- no opinion, which is what "I am watching something
        change" honestly is.
        """
        if self.resolved is None:
            return None
        try:
            import time as _time                             # noqa: PLC0415
            from bridge import actionbar                     # noqa: PLC0415
            from bridge import screen as screen_mod          # noqa: PLC0415
            last, deadline = None, time.time() + BAR_SETTLE_FOR
            while True:
                bar = actionbar.read(screen_mod.grab(self.resolved.win),
                                     self.resolved)
                if bar is None:
                    return None
                if last is not None and bar.skill_pattern == last.skill_pattern:
                    return bar
                if _time.time() >= deadline:
                    return None
                last = bar
                _time.sleep(BAR_SETTLE_STEP)
        except Exception:                                    # noqa: BLE001
            return None

    def _bank_curio(self, name: str) -> None:
        """Screenshot the moment a curio refused to respond. Best effort."""
        self._bank_frame(CURIO_SHOT, f"{name} is somewhere in it")

    def _bank_frame(self, shot: str, note: str) -> bool:
        """Bank the frame at the moment something did not work.

        One fixed filename per KIND of failure, overwritten, gitignored: a run
        that misses six inputs must not leave six near-identical PNGs in the
        directory that holds the reference corpus. Best effort in every sense
        -- Pillow is a dev-only import, and a failed write must cost the
        screenshot and never the message saying what went wrong.
        """
        try:
            from bridge import screen as screen_mod
            if self.resolved is None:
                return False
            img = screen_mod.grab(self.resolved.win)
            path = Path(__file__).resolve().parent.parent / "captures" / shot
            if screen_mod.save_png(img, path):
                self._say(f"  [act] frame banked to captures/{shot} - {note}")
                return True
        except Exception:                        # noqa: BLE001
            pass
        return False

    def _curio_landed(self, area: str, curio_id: str) -> tuple:
        """Did the `W` do anything? `(worked, why)`.

        Three acknowledgements, because no single one covers every curio:

        * the curio going **inactive** -- `content` drops to 0 when it is
          consumed, which is the clean answer for anything that is not loot;
        * the **pack growing**, which is what a LOOT curio gives once SPACE has
          taken what it coughed up -- and `travellers_tent_tutorial` is one
          (`curio_type_library.csv`: Loot, 100%, 2 draws);
        * neither, which is the case that had no name for three runs.

        SPACE is sent here rather than after, because it is *part of* the
        check: with no loot window it is inert and writes nothing, so a pack
        that grew is proof a window was open, and a pack that did not is
        evidence the press hit nothing. That also keeps `W` from ever being
        pressed twice into an open loot window -- the trap that made the
        generic retry misbehave on `coBC`'s sack -- because a retry only
        happens after SPACE has already cleared any window there was.
        """
        before = self._read_pack()
        self._take_loot(after="the curio")
        # **Loot TAKEN is an answer**: SPACE moved the save, so a window was
        # up. "Declined what did not fit" is NOT one, tempting as it was on
        # 2026-09-25 (a crate whose loot did not fit was pressed four times
        # and given up): that path is inferred from SPACE NOT moving the save
        # with the pack over half full, which is also exactly what a W that
        # opened nothing looks like. It needs a loot-window detector; the
        # no-room path now banks `captures/_no_room.png` to build one from.
        if getattr(self, "_loot_outcome", "") == "taken":
            return True, "its loot was taken"
        # **Polled, and by QUANTITY.** Live 2026-09-25 a discarded pack was
        # opened on attempt 4 and pressed six more times: its gold merged
        # into a stack already in the pack, so the slot COUNT stood still,
        # and the save had not yet written `content = 0` when it was read
        # once. Either answer can arrive late (the save is written per
        # turn), so both are re-read for a short window before "no".
        import time as _time                                 # noqa: PLC0415
        # Against a real window only -- tests drive this with stand-ins whose
        # save never changes, and a wait there is 2.5s of nothing per press.
        hwnd = getattr(getattr(self.resolved, "win", None), "hwnd", None)
        real = isinstance(hwnd, int) and bool(hwnd) and bool(user32.IsWindow(hwnd))
        deadline = _time.time() + (CURIO_ACK_S if real else 0.0)
        while True:
            if self._curio_active(area, curio_id) is False:
                return True, "it is used up"
            after = self._read_pack()
            grew = pack_grew(before, after)
            if grew:
                return True, f"the pack gained {grew}"
            if _time.time() >= deadline:
                return False, "still active and the pack did not grow"
            _time.sleep(0.5)

    def _walked_past(self, area: str, curio_id: str) -> bool:
        """Is the curio now more than a tile BEHIND the party?

        The bound on the forward search. Without it, six presses of `W` with a
        step between each could walk the party to the end of the corridor,
        where `W` is the door and the area they were searching is gone.
        Unknown position answers False: not knowing must not stop a search that
        has nothing else to steer by.
        """
        found = self._map_area(area)
        walk = self._walk_progress(area)
        if found is None or walk is None:
            return False
        tile = next((t for t in found.tiles if t.curio_id == curio_id), None)
        if tile is None:
            return False
        ahead = (tile.index - walk.index if walk.from_front
                 else walk.index - tile.index)
        return ahead < -1

    def _nudge(self, area: str) -> bool:
        """One more step along, between two attempts at the same curio."""
        if self.actuator.kill.check():
            self._say("  [act] KILL SWITCH - stopping")
            return False
        walk = self._walk_progress(area)
        step = InputPlan((Step("key", vk=self._forward_key(area, walk),
                               hold=CURIO_NUDGE_HOLD,
                               label="edge D"),),
                         "curio", "a little further along")
        self._say(f"  [act] {step}")
        if not self.actuator.execute(step):
            return False
        self.stats["acted"] += 1
        state = self._peek() or {}
        if state.get("in_battle"):
            self._say("  [act] [curio] a fight started on the way")
            return False
        here = state.get("area") or ""
        if here and here != area:
            self._say(f"  [act] [curio] walked out of {area} into {here}")
            return False
        return True

    # `_walk_up_to` lived here and is GONE (2026-09-06). It walked the party
    # to the curio's tile and then pressed `W`, which needs a position, a
    # direction and a stopping distance -- and all three had been falsified,
    # the last of them live when a corridor walked dark a second time left no
    # position at all and the approach walked the party away from the tent for
    # twelve presses.
    #
    # Fran: *"you can use W+D at the same time and should stop on curio on
    # interaction rather than mapping out the coordinates"*. A sweep needs
    # none of the three: it holds the interact key while walking and the
    # INTERACTION is the stop condition. See `plan_hallway_sweep`, whose
    # clearance check is the one thing that still needs a number, and needs
    # only distance from the door -- which is `raid.areatile` and survives a
    # second pass.
    #
    # Deleted rather than left uncalled: an approach this repo has falsified
    # three times should not be sitting here looking reusable. `git log` has
    # it if the sweep ever needs a fallback.

    def _reset_formation(self) -> None:
        """Put the party back in its marching order after a fight.

        Combat reorders the party and nothing put it back. A Move swaps two
        heroes, and where they end up is where the next fight starts -- a
        Highwayman parked at rank 1 opens it with `pistol_shot` (.launch 432)
        and `grape_shot_blast` (.launch 23) both greyed out, half his movepool
        gone. Reported live: "Didn't use the set order after the combat".

        One click on the game's own "Default Party order" button, which is
        idempotent: pressing it on a party already in order does nothing. So
        this is NOT gated on detecting disorder -- the save records the current
        formation but not the intended one, so "is it already right?" is a
        question that cannot be asked, and the click is cheap enough that
        asking it is not worth a wrong answer.

        Reported, never confirmed, for the same reason: a party already in
        order produces no save change, so demanding one would read the common
        success as a failure. That is the `_curio_active` trap, and this is
        the same shape.
        """
        if not self.armed:
            self._say("  [act] would reset the party order - not armed")
            return
        if self.resolved is None or not self._focused_or_wait():
            return
        # **Not behind the Quest Complete banner.** Evidence 2026-09-28: the
        # first numbered case (`captures/_party_order_1_*`) is the crest
        # rising over the loot window -- the click landed on the modal and the
        # next fight started out of order. The reset waits for the banner to
        # be answered and runs on the first poll after.
        seen = ""
        try:
            from bridge import screen as screen_mod     # noqa: PLC0415
            seen = screen_mod.recognise_live(self.window) or ""
        except Exception:                                # noqa: BLE001
            seen = ""
        if seen == "quest_complete":
            self._formation_due = True
            self._say_once("formation-deferred", "  [act] [route] the Quest "
                           "Complete banner is up - the party order waits "
                           "for it")
            return
        self._formation_due = False
        before = self._party_order()
        plan = InputPlan((Step("click", screen=self.resolved.party_order,
                               label="Default Party order"),),
                         "route", "put the party back in marching order")
        self._say(f"  [act] {plan}")
        # Evidence (2026-09-28): the next fight started out of the marching
        # order 38 times in 41, so the button's effect is banked either side.
        self._bank_frame("_party_order_before.png", "the party before the "
                         "Default Party order click")
        if not self.actuator.execute(plan):
            return
        self.stats["acted"] += 1
        time.sleep(0.5)                  # the heroes slide back into place
        self._bank_frame("_party_order_after.png", "the party after the "
                         "Default Party order click")
        after = self._party_order()
        if before and after and before != after:
            self._say(f"  [act] [route] party order {'>'.join(before)} "
                      f"-> {'>'.join(after)}")
        # **The button is not always enough.** It restores the order the
        # CAMPAIGN recorded at embark, and the tutorial never embarked -- its
        # save ships mid-raid, so there is no default and the click is inert.
        # Reported live: "tutorial has no 'default' party order, so
        # specifically during the tutorial it should move to restart the
        # order, to do this we can use the move action outside of combat".
        # Hence the watched `marching_order` and, when the button leaves the
        # party in the wrong one, explicit swaps.
        self._sort_into_marching_order(after)

    #: What the screen becomes once the party is out of the dungeon. The
    #: results chain, in the order DD draws it, plus the defeat variants --
    #: leaving with a completed objective is a Victory page, leaving without
    #: one is the same page relabelled, and neither is the bridge's business
    #: here. Prefix-matched, so the `@defeat` suffixes come along.
    LEFT_THE_RAID = ("raid_results", "raid_progression")

    #: Screens that are DD's standard modal frame asking a Yes/No, where
    #: `MODAL_OK` is the Yes.
    #
    # **The retreat confirm reads as `embark_underprepared`**, which is a
    # different dialog drawn in the same frame, and gating the confirm on the
    # exact name `modal` left the retreat dialog standing with the party in
    # the dungeon. Measured 2026-09-21 on Fran's campaign: "Are you sure you
    # want to retreat? The heroes will suffer the stress of defeat..." with
    # Yes at client (630, 390) -- and `modal_ok` resolves to (631, 390), so
    # the POINT was right all along and only the name was wrong. Fran,
    # watching it sit there: *"needs to click the yes now to the are you sure
    # screen"*.
    CONFIRM_SCREENS = ("modal", "embark_underprepared")

    def _leave_raid(self, seen, abandon: bool = False) -> bool:
        """Leave by the HUD's abandon-quest banner. Out of combat only.

        The caller has already established the two things that make this safe:
        the objective is MET (`run.py` only fills `raid_end` once
        `RoutePlan.targets` is empty) and no battle is on (this whole path
        hangs off the room update). Sent under either of those being false it
        would forfeit the expedition, which is why the gate lives in the
        caller and there is no way to reach this from `plan_battle`.

        Confirmed by the SCREEN reaching the results chain, not by a save
        write -- the same reasoning `_end_raid` gives for the banner: the
        button is a UI transition and DD writes the raid save on its own
        schedule.
        """
        if not self.armed:
            self._say_once("leave-raid-unarmed",
                           "  [act] [raid] would LEAVE by the abandon-quest "
                           "banner - not armed")
            return True
        # **ITS OWN KEY, and sharing `raid-end` would have re-created the very
        # stranding this method exists to end.** `_end_raid` adds `raid-end`
        # when it answers the banner -- including when it answers CONTINUE --
        # so a raid that continued for a deferred curio would then find the
        # guard already set and refuse to leave, once, silently, with the
        # party standing in the entrance room.
        key = "raid-abandoned" if abandon else "raid-left"
        if key in self.acted:
            # An abandon that did not get us out is not the end of it: the
            # objective may be met (no X is drawn then), so try the seal
            # ONCE rather than standing in the corridor for good.
            if abandon and "raid-left" not in self.acted:
                self._say("  [act] [raid] the abandon X did not leave - "
                          "trying the quest seal")
                return self._leave_raid(seen, abandon=False)
            return True
        # **The objective decides the widget.** The seal banks a COMPLETED
        # quest; the X abandons an unfinished one and asks first.
        plan = (plan_abandon_quest(self.resolved) if abandon
                else plan_leave_raid(self.resolved))
        if not plan.actionable:
            self._say_once("leave-raid-refused", f"  [act] {plan}")
            return False
        if not self._focused_or_wait():
            return False
        self.acted.add(key)
        if abandon:
            self._say(f"  [act] [raid] RETREATING (screen: "
                      f"{seen or 'UNKNOWN'}) - the X under the quest seal")
        else:
            self._say(f"  [act] [raid] the objective is met and no banner is "
                      f"up (screen: {seen or 'UNKNOWN'}) - leaving by the "
                      f"quest seal")
        self._say(f"  [act] {plan}")
        if not self.actuator.execute(plan):
            return True
        self.stats["acted"] += 1
        from bridge import screen as screen_mod       # noqa: PLC0415
        for _ in range(20):
            time.sleep(0.5)
            after = screen_mod.grab(self.resolved.win)
            if after is None:
                continue
            now = screen_mod.recognise(after) or ""
            if now.startswith(self.LEFT_THE_RAID):
                self._say(f"  [act] [raid] out of the dungeon ({now})")
                return True
            # **ANSWER A MODAL IF ONE APPEARS, AND ONLY THEN.** With the
            # objective met the seal goes straight to the results page and
            # there is nothing to confirm -- measured. But "no dialog ever"
            # is a claim about one situation, and a `MODAL_OK` sent blindly
            # lands in whatever is drawn instead. So the confirm is a
            # response to seeing one, not part of the plan.
            if now in self.CONFIRM_SCREENS and "leave-confirm" not in self.acted:
                ok = getattr(self.resolved, "modal_ok", None)
                if ok is not None:
                    self.acted.add("leave-confirm")
                    self._say("  [act] [raid] a dialog came up - confirming")
                    self.actuator.execute(InputPlan(
                        (Step("click", screen=ok,
                              label="confirm leaving the raid"),),
                        "raid", "answer the leave dialog"))
        # **Not a failure the loop should act on.** The confirm may simply be
        # slow, and re-sending would click `MODAL_OK` into whatever is drawn
        # next. `acted` already bars a second attempt this raid; the room
        # stall line is what says so if nothing did happen.
        self._say("  [act] [raid] the leave was sent and the results page has "
                  "not appeared yet")
        return True

    def _end_raid(self, advice) -> bool:
        """Press "Return to Hamlet" / "Continue Raid". True if handled.

        **Two independent things must agree before this clicks**, and that is
        the point of it. `run.py` decides from the SAVE that the objective is
        met, and this checks the SCREEN is actually showing the choice. Either
        alone is not enough: the save says nothing about which banner is up,
        and a screen match alone would hand the decision to a hash.

        That matters more here than anywhere else the actuator clicks, because
        both buttons are irreversible in opposite directions -- one forfeits
        whatever is still on the map, the other commits the party to more of a
        dungeon it has already won. So an unrecognised screen REFUSES, in the
        same shape as every other unmodelled screen: it does not guess, and it
        does not fall through to a click aimed at where the button usually is.
        """
        want = getattr(advice, "raid_end", "")
        if self.resolved is None:
            return False
        from bridge import screen as screen_mod   # noqa: PLC0415  (dev-only)
        # **Best-effort, the same rule `_answer_hunger` carries.** This used to
        # run only when `run.py` had already decided, and now it looks on every
        # room update -- so a frame it cannot read has to cost nothing rather
        # than take the poll down. A banner detector that can break the loop is
        # worse than no banner detector.
        try:
            img = screen_mod.grab(self.resolved.win)
            seen = None if img is None else screen_mod.recognise(img)
        except Exception:                            # noqa: BLE001
            img, seen = None, None
        if img is None:
            if want:
                self._say_once("raid-end-noscreen", "  [act] [raid] cannot "
                               "read the screen - not acting")
            return False
        if seen != "quest_complete":
            # **A RETURN DOES NOT NEED THE BANNER, and waiting for one that
            # will never come is how a won raid gets stranded.** The banner is
            # a modal shown once, at the moment the objective completes; press
            # Continue and it is gone for good. The HUD's abandon-quest
            # control is the standing exit and it works from any room out of
            # combat, so that is what a RETURN uses when the modal is not up.
            #
            # CONTINUE still needs the banner, because "continue" is only ever
            # an answer TO the banner -- there is no other control it means.
            if want == "return":
                return self._leave_raid(seen)
            if want:
                self._say_once(
                    f"raid-end-wrongscreen-{seen}",
                    f"  [act] [raid] would {want.upper()}, but the "
                    f"quest-complete banner is not up "
                    f"(screen: {seen or 'UNKNOWN'}) - holding")
            return False
        if not want:
            # **THE BANNER IS A MODAL AND IT CAN BE UP WHILE `run.py` HAS NO
            # OPINION.** Its raid-end block only runs once `plan.targets` is
            # empty, and DD's own objective completes at 90% of rooms
            # (`quest.types.json`, `explore_all_rooms.data.percentage`) -- so
            # a map with a room still to open finishes the QUEST while the
            # route is still planning. Live 2026-09-06: 7 of 8 rooms explored,
            # `Quest Complete!` drawn over the map, and the route layer spent
            # every poll clicking `rooF` through the banner and reporting that
            # the game offered no move.
            #
            # The default is CONTINUE and only when the route still has
            # somewhere to go, which inverts `_raid_end`'s "leave by default"
            # on purpose: that rule is about a map with nothing left on it.
            # Here the bridge has already decided it wants to be somewhere,
            # and of the two buttons only RETURN forfeits anything.
            # **THE BANNER IS THE GAME SAYING THE OBJECTIVE IS MET, so the
            # raid-end pricing is what should answer it.** `run.py` carries
            # that verdict as `raid_end_priced` precisely for this moment: it
            # weighs what is still reachable against the party's stress and
            # afflictions, which is the question, and it exists whether or
            # not the bridge's own `RoutePlan.targets` has emptied yet.
            #
            # The old fallback was "CONTINUE, because the route wants
            # somewhere to go", which is not a decision about the raid at
            # all -- the route always wants somewhere until the map is
            # covered, and DD finishes an explore quest at 90% of rooms
            # (`quest.types.json`). So it continued into a won raid with an
            # afflicted party as readily as with a fresh one.
            want = getattr(advice, "raid_end_priced", "")
            if want:
                self._say(f"  [act] [raid] the quest is complete and the "
                          f"raid layer prices this as {want.upper()}")
            elif getattr(advice, "next_step", ""):
                # No priced verdict (an older caller, or no route): fall back
                # to continuing. CONTINUE is the recoverable direction --
                # the abandon-quest banner top left still ends the raid
                # whenever the bridge is ready, which `plan_leave_raid`
                # sends. RETURN forfeits everything still on the map.
                want = "continue"
                self._say("  [act] [raid] the quest is complete and the route "
                          "still wants " + str(getattr(advice, "next_step", "")) +
                          " - CONTINUE, because the abandon-quest banner still "
                          "ends the raid whenever we are ready")
            else:
                return False
        if not self.armed:
            self._say_once("raid-end-unarmed",
                           f"  [act] would {want.upper()} from the raid "
                           f"- not armed")
            return True
        if not self._focused_or_wait():
            return False
        # Once per raid. The poll loop republishes the same room until the
        # save moves, and this banner does not move it -- so without a guard
        # the second poll presses the other outcome's neighbour on a screen
        # that has already gone.
        if "raid-end" in self.acted:
            return True
        self.acted.add("raid-end")
        point = (self.resolved.quest_return_to_hamlet if want == "return"
                 else self.resolved.quest_continue_raid)
        label = ("Return to Hamlet" if want == "return" else "Continue Raid")
        plan = InputPlan((Step("click", screen=point, label=label),),
                         "raid", f"{want} - the quest objective is met")
        self._say(f"  [act] {plan}")
        if not self.actuator.execute(plan):
            return True
        self.stats["acted"] += 1
        # Confirmed by the SCREEN leaving the banner, not by a save write: the
        # button is a UI transition and DD writes the raid save on its own
        # schedule, so demanding a save change here would read the ordinary
        # success as a failure -- the `_curio_active` trap.
        for _ in range(20):
            time.sleep(0.5)
            after = screen_mod.grab(self.resolved.win)
            if after is not None and screen_mod.recognise(after) != "quest_complete":
                self._say(f"  [act] [raid] {label} - banner cleared")
                return True
        self._say(f"  [act] [raid] {label} sent, but the banner is still up")
        return True

    def _sort_into_marching_order(self, order: list) -> None:
        """Swap heroes until the formation matches `self.marching_order`.

        Selection sort with the MOVE action, which works out of combat exactly
        as it does in it -- the dungeon HUD shows the same action bar, slot 5
        the same circular arrows. One swap is: click the hero who belongs
        further forward, press `5`, click the rank they belong in. At most
        n-1 swaps, and each is verified against the save before the next.

        Silent when there is nothing to compare against. `marching_order` is
        WATCHED, not read -- the save has no field for the intended order -- so
        an order this runner never saw un-fought is not one it may impose.
        """
        want = list(self.marching_order)
        if not want or not order or sorted(want) != sorted(order):
            return
        if want == order:
            self._say(f"  [act] [route] party order {'>'.join(order)} "
                      f"(the marching order)")
            return
        self._say(f"  [act] [route] party order is {'>'.join(order)}, "
                  f"want {'>'.join(want)} - swapping")
        # Kept apart from the per-fight before/after pair, which the next
        # fight overwrites: this is the frame where the order is still wrong
        # AFTER the Default Party order click (2026-09-28 evidence).
        self._bank_frame("_party_order_wrong.png", "still out of order after "
                         "the Default Party order click")
        # The first few cases are KEPT (the per-fight pair is overwritten by
        # the next fight): the last one banked on 2026-09-28 was the
        # quest-complete banner, which says nothing about the ordinary case.
        self._keep_party_order_evidence()
        for rank in range(len(want)):
            order = self._party_order()
            if not order or order == want:
                break
            if order[rank] == want[rank]:
                continue
            if want[rank] not in order[rank + 1:]:
                self._say(f"  [act] [route] cannot place {want[rank]} - giving up")
                return
            src = order.index(want[rank], rank + 1)
            if not self._swap_ranks(src, rank, want[rank]):
                return
        final = self._party_order()
        if final:
            self._say(f"  [act] [route] party order now {'>'.join(final)}"
                      + ("" if final == want else " - NOT the marching order"))

    def _swap_ranks(self, src: int, dst: int, who: str) -> bool:
        """Move the hero at 0-based rank `src` to rank `dst`. False on refusal.

        The same three inputs a combat move takes -- select, `5`, destination
        -- because out of combat it is the same control. Verified by re-reading
        the formation rather than by the save's mtime: a swap that did not
        happen leaves the order alone, which is exactly what this can see.
        """
        if self.actuator.kill.check():
            self._say("  [act] KILL SWITCH - stopping")
            return False
        pick = self.resolved.point("hero", src)
        drop = self.resolved.point("hero", dst)
        if pick is None or drop is None:
            self._say(f"  [act] [route] no calibrated rank for the "
                      f"{src + 1}->{dst + 1} swap")
            return False
        plan = InputPlan((Step("click", screen=pick, label=f"select {who}"),
                          Step("key", vk=SKILL_VKS[MOVE_SLOT - 1],
                               label="select slot 5 (MOVE)"),
                          Step("click", screen=drop,
                               label=f"hero rank {dst + 1}")),
                         "route", f"move {who} to rank {dst + 1}")
        self._say(f"  [act] {plan}")
        if not self.actuator.execute(plan):
            return False
        self.stats["acted"] += 1
        # Wait for the SAVE, not for a fixed animation. One 0.6s look reported
        # "cannot place highwayman - giving up" about a swap that had in fact
        # landed -- verified afterwards, the save read crusader>highwayman. A
        # formation change is written when the heroes finish trading places,
        # and that is slower than a keypress.
        before = self._party_order()
        for _ in range(SWAP_WAITS):
            time.sleep(SWAP_WAIT)
            if self._party_order() != before:
                return True
        self._say(f"  [act] [route] the {src + 1}->{dst + 1} swap did not "
                  f"reach the save")
        return False

    def _party_order(self) -> list:
        """Hero classes front to back, from the save. [] when unreadable."""
        try:
            from bridge.live_state import read_live_state
            if self.raid_path is None:
                return []
            roster = Path(str(self.raid_path).replace("raid.json", "roster.json"))
            st = read_live_state(raid_file=self.raid_path, roster_file=roster)
            return [h.hero_class for h in st.heroes]
        except Exception:                        # noqa: BLE001
            return []

    def _take_loot(self, after: str = "the fight") -> None:
        """SPACE = take ALL when the loot window is up (the game's own hotkey).

        Sent unconditionally after a fight rather than gated on detecting the
        window, because the save does not expose one. SPACE with no loot window
        is inert, so the cost of being wrong is nothing -- unlike a click,
        which would land on whatever happens to be there.

        If the pack is full DD refuses with a modal instead, and `_make_room`
        takes over.
        """
        if not self.armed:
            self._say("  [act] would take loot (SPACE) - not armed")
            return
        if not self._focused_or_wait():
            return
        self._say(f"  [act] [loot] SPACE (take all) after {after}")
        # What SPACE met, for `_curio_landed`: "taken", "left" (the rest was
        # declined because it did not fit), or "" for no sign of a window.
        self._loot_outcome = ""
        plan = InputPlan((Step("key", vk=VK_SPACE, label="take all loot"),),
                         "loot", "clear the loot window")
        before = self._save_sig()
        time.sleep(SETTLE_BEFORE_SEND)
        if not self.actuator.execute(plan):
            return
        self.stats["acted"] += 1
        if before is not None and self._wait_for_save_change(before):
            self._say("  [act] [loot] taken - confirmed by the save")
            self._loot_outcome = "taken"
            return
        self._make_room()

    # -- the full pack --

    def _make_room(self) -> None:
        """SPACE did not clear the loot window. Dismiss the modal, then decide.

        The generic retry is wrong here: DD answers a take-all that does not
        fit with a modal ("You don't have enough room in inventory to take
        all"), so the retry reads "no save change" as "the input did not land"
        and presses SPACE again into a dialog already up. Seen live.

        ## Two things this got wrong on the first real raid, both now fixed

        **The modal does not mean the pack is FULL, it means the loot did not
        FIT.** The first version bailed out with "the pack is NOT full, this is
        a screen the bridge does not model" whenever a slot was free. Fran hit
        exactly the case that disproves it: one free slot, four items on the
        floor. SPACE took one, the pack filled, and the modal came up for the
        other three -- with the save still reading 15/16. `free_slots == 0` was
        never the trigger; `loot > free_slots` is.

        **And the pack cannot be read while this is happening.** DD writes the
        save when an action RESOLVES, and picking loot up is not such an
        action, so the pack this reads is stale by exactly the amount at issue.
        The count is therefore never trusted to the item -- but it IS trusted
        to the half, which is the ordering below.

        **The click is NOT free, and used to be sent on every fight.** The
        first version dismissed the modal unconditionally, reasoning that OK
        lands on the middle of the dungeon backdrop when no modal is up. The
        game's own curio text says otherwise: *"[CLICK] or press [W] to
        investigate them"* -- a click in the middle of a hallway is one of the
        two documented ways to work a curio, and SPACE with no loot window is
        inert, so this path ran after every fight and every curio. So the pack
        is read FIRST: it lags by a PICKUP, not by half a pack, and a pack
        under half full cannot have raised a "did not fit" modal. That keeps
        the case which disproved the old slot check -- one free slot, four
        items, save reading 15/16 -- because 15 of 16 is not under half.

        The staleness also means a discard usually cannot be confirmed, so it
        usually will not happen; `--full-pack abandon` is the path that
        actually keeps a run moving. See `_abandon_or_pause`.
        """
        pack = self._read_pack()
        if pack is not None:
            self._say(f"  [act] [loot] pack reads {len(pack)}/{INVENTORY_SLOTS} "
                      f"(the save lags a pickup, so it may be one behind)")
            if len(pack) <= INVENTORY_SLOTS // 2:
                self._say(f"  [act] [loot] {INVENTORY_SLOTS - len(pack)} slots "
                          f"free - there was no loot window, nothing to take")
                return
        # Only a pack POSITIVELY read as under half skips the click. "Could not
        # read it" is not that: it is the case with no information at all, and
        # a dialog nobody dismissed strands the run, so it still goes.
        self._bank_frame("_no_room.png", "what SPACE left on screen when it "
                         "did not move the save - a loot window, or nothing")
        if not self._dismiss_modal():
            return self._pause("could not dismiss the 'no room' modal")
        if pack is None:
            return self._pause("the loot window did not clear, and the pack "
                               "could not be read to find out why")

        from bridge.inventory import item_defs, plan_discards
        # **MERGE BEFORE ANYTHING ELSE.** Combining split stacks throws
        # nothing away and needs no judgement about what is chaff, so it is
        # strictly better than a discard and it is free. The layer detected
        # these and only PRINTED them until 2026-09-05, and the first live
        # raid to reach a full pack stopped on exactly that: "2 stacks of
        # torch hold 3 of a 8 limit - merging them frees 1 slot(s), for free",
        # immediately followed by "nothing in the pack is free to discard".
        if self._merge_stacks(pack):
            return self._retake_loot()
        # Without the party's classes nothing is ever proposed -- a class item
        # is only dead weight when its class is absent, and "I could not read
        # the roster" must not read as "there is no Houndmaster".
        plan = plan_discards(pack, want=1, defs=item_defs(),
                             party_classes=self._party_classes())
        for note in plan.notes:
            self._say(f"  [act] [pack] {note}")

        if plan.discards:
            self._say(f"  [act] [pack] {plan}")
            if self._discard_slot(plan.discards[0]):
                return self._retake_loot()
            return self._pause("the discard did not register - SHIFT-CLICK may "
                               "not survive this backend")

        # **Surplus provisions make room for loot.** Only what the raid
        # layer's own reserve says the rest of the map will never use.
        from bridge.inventory import plan_surplus_discard
        reserve = self.item_reserve() if callable(self.item_reserve) else None
        if reserve is None:
            self._say("  [act] [pack] no provision reserve from the raid layer "
                      "- not trading supplies for loot")
        else:
            spare = plan_surplus_discard(pack, reserve)
            if spare is not None:
                self._say(f"  [act] [pack] DISCARD slot {spare.slot} "
                          f"({spare.label}) - {spare.why}")
                if self._discard_slot(spare):
                    return self._retake_loot()
            else:
                held = ", ".join(f"{s.id or s.type} x{s.amount}" for s in pack
                                 if s.type in ("supply", "provision"))
                self._say(f"  [act] [pack] no supply stack is wholly surplus "
                          f"(reserve {dict(reserve)}; held {held})")

        self._abandon_or_pause(pack)

    def _keep_party_order_evidence(self, keep: int = 5) -> None:
        """Copy this fight's before/after/wrong frames to numbered names, for
        the first `keep` out-of-order cases of the run. Best effort."""
        n = getattr(self, "_party_order_kept", 0)
        if n >= keep:
            return
        self._party_order_kept = n + 1
        try:
            import shutil                                 # noqa: PLC0415
            base = Path(__file__).resolve().parent.parent / "captures"
            for tag in ("before", "after", "wrong"):
                src = base / f"_party_order_{tag}.png"
                if src.exists():
                    shutil.copyfile(src, base / f"_party_order_{n + 1}_{tag}.png")
        except Exception:                                 # noqa: BLE001
            pass

    def _fed_again_after_fight(self) -> None:
        """**"Full" lasts until the next completed fight** (Fran, 2026-09-28:
        "if you complete a combat you can re-feed your hero"). Both the
        refused-portion mark and the portions counted toward
        `MAX_FOOD_BEFORE_FULL` start over when a fight ends."""
        self._food_full = set()
        sent = getattr(self, "_provisions_sent", None)
        if sent:
            self._provisions_sent = {k: n for k, n in sent.items()
                                     if k[2] != "food"}

    def _merge_stacks(self, pack) -> bool:
        """Combine split stacks by dragging. True when a slot was freed.

        **A DRAG, not a click.** DD moves an item by picking it up and putting
        it down, and `SendInputBackend.drag` walks the pointer across in hops
        because SDL decides a drag happened from the motion events BETWEEN the
        press and the release -- the same reason the coach's hire is a drag.

        **The acknowledgement is the pack getting SHORTER**, which is why
        `plan_stack_merges` only ever emits a pour that empties its source. A
        partial merge tops the destination up and leaves the source holding
        the remainder, so the pack comes back the same length and there is no
        way to tell it from an input that never landed. One number decides it.

        Re-read between drags rather than trusting the plan: a merge re-packs
        nothing here (the slots keep their indices) but the save is the only
        thing that knows a drag landed, and a second drag onto a slot that did
        not take the first would drop an item on the floor.
        """
        from bridge.inventory import item_defs, plan_stack_merges
        merges = plan_stack_merges(pack, item_defs())
        if not merges:
            return False
        drag = getattr(self.actuator.backend, "drag", None)
        if not callable(drag):
            self._say("  [act] [pack] this backend cannot drag - not merging")
            return False
        freed = 0
        for merge in merges:
            src = self.resolved.inventory_point(merge.src)
            dst = self.resolved.inventory_point(merge.dst)
            if src is None or dst is None:
                self._say(f"  [act] [pack] no cell for slot {merge.src} or "
                          f"{merge.dst} - not merging")
                break
            before = len(pack)
            self._say(f"  [act] [pack] MERGE {merge.label()}")
            plan = InputPlan((Step("drag", screen=src, to=dst,
                                   label=f"slot {merge.src} onto {merge.dst}"),),
                             "pack", f"merge {merge.item}, freeing a slot")
            if not self.actuator.execute(plan):
                break
            self.stats["acted"] += 1
            time.sleep(PACK_SETTLE)
            now = self._read_pack()
            if now is None or len(now) >= before:
                self._say("  [act] [pack] the merge did not register")
                break
            pack, freed = now, freed + 1
        if freed:
            self._say(f"  [act] [pack] {freed} slot(s) freed for nothing")
        return freed > 0

    def _abandon_or_pause(self, pack) -> None:
        """Nothing in the pack is free to throw away. Two honest options.

        Neither is obviously right, so the choice is the operator's and the
        default is the one that cannot lose anything.

        `pause` stops and hands over. `abandon` closes the loot window and
        walks on, which is a legitimate play -- the modal itself offers "choose
        only what you can carry" -- but the bridge CANNOT SEE what it is
        walking away from: loot is not in the save until it is picked up, so
        there is no way to report, or weigh, what was left. That asymmetry is
        the whole reason `pause` is the default.

        This is the common case and not the rare one. Of 25 distinct full packs
        in `archive/traces/`, ZERO held anything the discard rule considers
        free and only 6 had a stack merge available -- so a bridge that could
        only discard chaff would still stop at nineteen of them.
        """
        from bridge.inventory import describe
        if self.full_pack == "swap":
            # Trade the cheapest stack of value for the loot, then walk on
            # if nothing in the pack is cheap enough -- never pause, because
            # an unattended run has nobody to hand over to.
            from bridge.inventory import plan_value_discard
            cheap = plan_value_discard(pack)
            if cheap is not None and not getattr(self, "_swapped_this_window", False):
                self._say(f"  [act] [pack] DISCARD slot {cheap.slot} ({cheap.label}) - {cheap.why}")
                self._swapped_this_window = True
                if self._discard_slot(cheap):
                    return self._retake_loot()
            self._swapped_this_window = False
        if self.full_pack not in ("abandon", "swap"):
            self._say("  [act] [pack] nothing in the pack is free to discard")
            return self._pause(
                "the loot did not all fit and nothing in the pack is free to "
                "throw away. Take what you want by hand and close the window, "
                "or re-run with --full-pack abandon to leave the rest behind",
                detail=describe(pack))
        pt = self.resolved.loot_close
        self._say("  [act] [loot] leaving the rest behind - closing the window")
        plan = InputPlan((Step("click", screen=pt, label="decline loot (X)"),),
                         "loot", "abandon what does not fit")
        before = self._save_sig()
        if not self.actuator.execute(plan):
            return self._pause("could not close the loot window")
        self.stats["acted"] += 1
        self.stats["abandoned"] += 1
        if before is not None and not self._wait_for_save_change(before):
            # Declining loot need not write a save, so this is a note rather
            # than a failure. The next poll re-reads the room either way.
            self._say("  [act] [loot] closed (no save change - expected)")

    def _dismiss_modal(self) -> bool:
        """Click OK on the 'no room' modal.

        The coordinate is measured rather than calibrated (`layout.MODAL_OK`),
        and the modal is the only thing accepting input while it is up, so a
        click at its centre cannot reach the board behind it. Returns True on
        send: the modal writes no save, so there is nothing to verify against
        and the next step's own check is what catches a miss.
        """
        pt = self.resolved.modal_ok
        self.pack_full_seen += 1
        self._say(f"  [act] [pack] OK on the 'no room' modal at {pt}")
        plan = InputPlan((Step("click", screen=pt, label="OK"),),
                         "pack", "dismiss the full-inventory modal")
        if not self.actuator.execute(plan):
            return False
        self.stats["acted"] += 1
        time.sleep(0.4)
        return True

    def _discard_slot(self, discard) -> bool:
        """SHIFT-CLICK a pack slot until the slot is genuinely EMPTY.

        ## The success test is the slot COUNT, not the slot's contents

        Whether one shift-click discards a whole stack or a single unit is
        undocumented. The game ships `[SHIFT+CLICK] to reduce torch` and
        `[SHIFT+CTRL+CLICK] to snuff out torch` for the torch METER, so the
        same gesture meaning "one unit" on a pack stack is entirely plausible,
        and `str_discard_item_instructions` does not say.

        An earlier version tested "is slot n still holding `dog_treats x2`",
        which a per-unit discard passes on the first click -- `x2` becomes
        `x1`, the label differs, and the runner would have reported the slot
        freed and pressed SPACE into a second modal. The only thing that
        actually decides whether the loot fits is **how many slots are
        occupied**, so that is what is measured.

        So this does not pick a behaviour, it observes one: click, re-read,
        and stop the moment the pack is one slot shorter. Bounded by the
        stack's own `amount`, which is at most 2 for the only item the rule
        ever proposes.

        ## Re-clicking the same coordinate is only safe while it is the same item

        Discarding may re-pack the slots, and a second click at slot n would
        then land on whatever slid into it. Every iteration re-checks that the
        target still holds the same item id, and bails if it does not.
        """
        pt = self.resolved.inventory_point(discard.slot)
        if pt is None:
            return False
        before = self._read_pack()
        if before is None:
            return False
        occupied = len(before)
        remaining = max(1, int(getattr(discard, "amount", 1) or 1))
        self._say(f"  [act] [pack] SHIFT-CLICK slot {discard.slot} "
                  f"({discard.label}) at {pt} - {discard.why}")

        for attempt in range(1, remaining + 1):
            plan = InputPlan(
                (Step("click", screen=pt, mods=(VK_SHIFT,),
                      label=f"discard {discard.label}"),),
                "pack", f"free a slot by discarding {discard.label}")
            if not self.actuator.execute(plan):
                return False
            self.stats["acted"] += 1

            pack = self._await_pack_change(occupied, discard, remaining)
            if pack is None:
                self._say(f"  [act] [pack] click {attempt} changed nothing - "
                          f"stopping")
                return False
            if len(pack) < occupied:
                self._say(f"  [act] [pack] slot {discard.slot} freed after "
                          f"{attempt} click(s) ({len(pack)}/{INVENTORY_SLOTS})")
                self.stats["discarded"] += 1
                return True
            here = next((s for s in pack if s.slot == discard.slot), None)
            if here is None or here.id != discard.id or here.type != discard.type:
                self._say(f"  [act] [pack] slot {discard.slot} now holds "
                          f"something else - not clicking it again")
                return False
            remaining = here.amount
            self._say(f"  [act] [pack] stack reduced to {here.amount} - "
                      f"the click takes ONE unit, continuing")
        self._say(f"  [act] [pack] slot {discard.slot} never emptied")
        return False

    def _await_pack_change(self, occupied: int, discard, stack: int):
        """The pack once it differs from what the click was sent against.

        Returns None on timeout, which the caller treats as "the input did not
        land" -- never as "it worked". A discard that cannot be observed is
        indistinguishable from one that did nothing, and guessing the
        optimistic reading of that is how you press SPACE into a modal.
        """
        deadline = time.time() + ACK_TIMEOUT
        while time.time() < deadline:
            time.sleep(0.3)
            pack = self._read_pack()
            if pack is None:
                continue
            if len(pack) < occupied:
                return pack
            here = next((s for s in pack if s.slot == discard.slot), None)
            if here is None or here.id != discard.id or here.amount < stack:
                return pack
        return None

    def _retake_loot(self) -> None:
        """SPACE once more, now that there is room. One attempt, not a loop."""
        self._say("  [act] [loot] SPACE again, with room this time")
        plan = InputPlan((Step("key", vk=VK_SPACE, label="take all loot"),),
                         "loot", "take the loot that now fits")
        before = self._save_sig()
        if not self.actuator.execute(plan):
            return
        self.stats["acted"] += 1
        if before is not None and self._wait_for_save_change(before):
            self._say("  [act] [loot] taken - confirmed by the save")
            return
        self._pause("made room, but the loot still did not go in")

    def _read_pack(self):
        """The raid pack from the live save, or None if it cannot be read."""
        try:
            from bridge.inventory import read_pack
            if self.raid_path is None:
                return None
            return read_pack(Path(self.raid_path))
        except Exception:                        # noqa: BLE001
            return None

    def _party_classes(self):
        """The classes on the raid right now, or None if unreadable.

        None is not an empty set and the difference is destructive: an empty
        set says "there is no Houndmaster", which makes dog treats -- +15% ACC
        and +50% damage for three rounds -- look like dead weight. `None` makes
        `plan_discards` propose nothing at all, which is the right answer when
        the roster cannot be read.
        """
        try:
            from bridge.live_state import read_live_state
            if self.raid_path is None:
                return None
            roster = Path(str(self.raid_path).replace("raid.json",
                                                      "roster.json"))
            state = read_live_state(raid_file=Path(self.raid_path),
                                    roster_file=roster)
            classes = {h.hero_class for h in (state.heroes or [])
                       if getattr(h, "hero_class", "")}
            return frozenset(classes) if classes else None
        except Exception:                        # noqa: BLE001
            return None

    def _pause(self, why: str, detail: str = "") -> None:
        """Stop acting and say why, loudly. Never guess past this point."""
        self.paused = True
        self.stats["paused"] += 1
        print("", end="", flush=True)          # console bell
        self._say("")
        self._say("  " + "=" * 62)
        self._say(f"  PAUSED - {why}")
        for line in (detail or "").splitlines():
            self._say(f"    {line}")
        self._say("  Deal with it in the game; the bridge resumes on its own "
                  "once the save shows the pack has room.")
        self._say("  " + "=" * 62)
        self._say("")

    def _inventory_use(self):
        """(occupied slots, capacity) from the raid save, or (None, cap)."""
        pack = self._read_pack()
        return (len(pack) if pack is not None else None, INVENTORY_SLOTS)

    def _still_paused(self) -> bool:
        """Resume once the pack has room again -- the human sorted it out."""
        slots, cap = self._inventory_use()
        if slots is not None and slots < cap:
            self.paused = False
            self._said.discard("paused")
            self._say(f"  [act] pack has room again ({slots}/{cap}) - resuming")
            return False
        return True

    def _walk_toward(self, target_area: str, from_area: str) -> None:
        """Press a direction until the party leaves this area.

        A corridor is several steps, and `raid.in_area` is area-granular -- it
        does not change until the party crosses a boundary. So one keypress per
        published room block would never get anywhere: the room key is keyed on
        the area and the torch BRACKET, so it does not republish while walking
        inside one corridor. Hence a bounded loop here rather than one step per
        poll.

        It stops the moment the area changes, a fight starts, or the step
        budget runs out.

        **THE DIRECTION IS DECIDED ONCE, BEFORE THE FIRST STEP, AND NEVER
        REVISED.** Three mechanisms used to revise it -- a global `Heading`
        bit flipped on arriving somewhere unexpected, a per-corridor
        "the save was wrong here" set, and a one-step check that the party was
        getting closer to the destination end -- and all three were solving a
        question the game does not pose. `D` advances and `A` backtracks in
        every hallway (`dungeon_map.Walk.key_toward`), so the only thing to
        decide is whether the destination is ahead or behind, and the save
        answers that outright: the party came in by one end, and a target at
        that end is behind it.

        `_target_end_index` is the mappos half of that. When it cannot answer
        -- no map file, an area the save does not carry -- the default is `D`,
        because entering a corridor to cross it is the overwhelmingly common
        case and it is what the route asks for whenever it is extending the
        party's path rather than retracing it.
        """
        walk = self._walk_progress(from_area)
        end = self._target_end_index(from_area, target_area)
        backtracking = (walk is not None and end is not None
                        and end == walk.entry_index)
        # **AND WHEN THE POSITION IS UNREADABLE, ASK THE SAVE WHICH DOOR WE
        # CAME IN BY.** `_walk_progress` is silent on a corridor already
        # walked dark end to end: `Area.standing_on` reads the direction off
        # the dark run the party leaves behind it, and a second pass leaves no
        # run to read. The default is then `D` -- right for crossing a fresh
        # corridor, wrong for retracing one.
        #
        # That is not a cosmetic default, because the door logic at the bottom
        # of this method RESTS on the direction having been derived. Its own
        # comment says `W` at the arrival gate "cannot arise here ... the
        # direction was derived from which end the party entered by, so a
        # stall is always against the far end of the walk".
        #
        # It arose. Live 2026-09-06, walking back for a deferred curio: the
        # party re-entered `coAB` bound for `rooA`, the walk could not tell it
        # had got anywhere, the step budget ran out, `W` went into the gate it
        # had arrived through and the party landed back in `rooB` -- then
        # oscillated, burning ten torch a lap. **The loot layer's deferral is
        # the first thing in this project that ever sends the party back
        # through a corridor it has already cleared**, which is why a
        # well-tested walk had never met it.
        #
        # `raid.last_room_id` is the game's own answer and needs no inference.
        if walk is None and self.raid_path is not None:
            came_from = None
            try:
                from bridge.route import read_last_area
                came_from = read_last_area(self.raid_path)
            except Exception:                              # noqa: BLE001
                came_from = None
            if came_from:
                backtracking = (came_from == target_area)
                self._say(f"  [act] [route] {from_area} is walked dark, so "
                          f"there is no run to read - the save says the party "
                          f"came in from {came_from}")
        vk = VK_A if backtracking else VK_D
        if backtracking:
            self._say(f"  [act] [route] {target_area} is back the way we came "
                      f"into {from_area} - walking A")
        # **Position IS read per step here now, and only to STEER.** The old
        # loop read it once and then walked up to `MAX_WALK_STEPS` blind,
        # learning the direction only if it fell out of the corridor into the
        # wrong area -- a whole wasted traversal per mistake, and no learning
        # at all in a dead end. The objection in the note below is about
        # STOPPING, which this still does not do: the last step out of a
        # corridor is exactly the step that makes `walked` go None, so a
        # position that stops advancing ends the walk nowhere near the door.
        # Steering is a different question and the same reading answers it.
        seen = walk.passed if walk is not None else None
        stalled = 0
        for step in range(1, MAX_WALK_STEPS + 1):
            if self.actuator.kill.check():
                self._say("  [act] KILL SWITCH - stopping")
                return
            plan = InputPlan((Step("key", vk=vk, hold=WALK_HOLD,
                                   label=f"walk {'A' if backtracking else 'D'}"),),
                             "route", f"step {step} toward {target_area}")
            if not self.actuator.execute(plan):
                return
            self.stats["acted"] += 1
            # NO per-step AREA check as the acknowledgement. `raid.in_area` is
            # area-granular, so walking along a corridor does not change it
            # until the party crosses a boundary. The old code demanded a save
            # change after every keypress and bailed on the first one with
            # "the party did not move" -- which was false: it had moved,
            # invisibly. That single wrong assumption is most of why movement
            # never worked.
            #
            # Per-tile `knowledge` DOES advance per step (`Area.walked`), and
            # `_walk_up_to` steers by it -- but it is not wanted here. This
            # loop wants to leave the corridor, and the last step out of one is
            # exactly the step that makes `walked` go None. Stopping on that
            # would stop one step short of the boundary it is walking toward.
            time.sleep(0.35)
            state = self._peek()
            if state is None:
                return
            if state.get("in_battle"):
                self._say(f"  [act] [route] a fight started after {step} step(s)")
                return
            here = state.get("area") or ""
            if here and here != from_area:
                verdict = ("as advised" if here == target_area
                           else f"NOT {target_area}")
                self._say(f"  [act] [route] entered {here} ({verdict})")
                return
            # Still in the corridor: has the party actually got anywhere?
            now = self._walk_progress(from_area)
            if now is not None and seen is not None:
                if now.passed > seen:
                    seen, stalled = now.passed, 0
                else:
                    stalled += 1
                    if stalled >= WALK_STALL_LIMIT:
                        # This used to spend its first stall flipping the
                        # direction, because a backward step is INVISIBLE in
                        # the save -- per-tile `knowledge` is monotonic (108
                        # growth events against 2 that ever shrank, over the
                        # whole capture corpus) -- so a party walking the
                        # wrong way reads exactly like a party walking into a
                        # wall. With the direction derived there is only one
                        # of those left.
                        #
                        # **AND IT IS USUALLY THE DOOR.** This `return` cost a
                        # whole live raid on 2026-09-05: the party stood at
                        # the far end of `coAD` with `rooA` through the door
                        # in front of it, `D` moved nothing three times, and
                        # the walk gave up -- while the code twenty lines
                        # below, which presses `W` and is the ONLY thing that
                        # opens a door, was reached solely by exhausting all
                        # `MAX_WALK_STEPS`. Pressing `W` by hand at that exact
                        # position moved the party into `rooA` in two seconds.
                        #
                        # `W` is right for the other stall too. It is the
                        # game's universal "interact with what is in front of
                        # you" (`str_help_raid_hallway_4`), so it clears an
                        # obstacle as well as it opens a door -- and the case
                        # this must never become, `W` at the entrance walking
                        # the party BACK the way it came, cannot arise here:
                        # the direction was derived from which end the party
                        # entered by, so a stall is always against the far
                        # end of the walk, never against the door behind it.
                        #
                        # Same rule as `_LayerDone` one layer up: a step that
                        # cannot act must not block the one that can.
                        self._say(f"  [act] [route] {WALK_STALL_LIMIT} steps "
                                  f"toward {target_area} moved nothing - "
                                  f"trying the door")
                        break
            elif now is not None and seen is None:
                seen = now.passed
        # The end of the corridor. Stepping into a ROOM is a door interaction,
        # and `W` is what does it -- confirmed in play: "w solo va hacia cofres
        # o de pasillo a sala". Leaving a room is NOT this; that is the map,
        # and `plan_room` refuses it before we ever get here.
        # **THE DOOR OPENS FOR THE FRONT TWO HEROES.** Fran, 2026-09-26: at the
        # last tile of a hallway the door is drawn among the party, and W only
        # opens it when one of the two FRONT heroes stands at it -- pinned at
        # the far end it sits at the back rank. Measured live the same day in
        # coBC: W at the end did nothing, and A-steps of 0.25 s opened it at
        # 1.0 s. So the first back-off is ~two hero widths (0.75 s), then
        # quarter-second steps (`DOOR_BACKOFF`).
        for attempt in range(1, DOOR_TRIES + 1) if in_a_room(target_area) else ():
            # **A STEP BACK BEFORE EVERY RETRY, not just another press.** The
            # stall check reads `areatile`, which only ticks at a tile
            # boundary, so a party jammed against the wall PAST the door reads
            # as "moved nothing" -- Fran, watching the scene this stalled in:
            # the party "have gone too far into the right side without opening
            # the door, it skipped it". Backing off one step is what puts them
            # on the door again, and it is the cheap direction to be wrong in.
            # See DOOR_TRIES.
            if attempt > 1:
                back = VK_D if backtracking else VK_A
                self._say(f"  [act] [route] the door did not open - the party "
                          f"may have walked past it, stepping back and trying "
                          f"again ({attempt}/{DOOR_TRIES})")
                hold = DOOR_BACKOFF[min(attempt - 2, len(DOOR_BACKOFF) - 1)]
                nudge = InputPlan((Step("key", vk=back, hold=hold,
                                        label=f"back off {'D' if backtracking else 'A'}"),),
                                  "route", f"back onto the door of {target_area}")
                if not self.actuator.execute(nudge):
                    return
                self.stats["acted"] += 1
                time.sleep(0.35)
            self._say(f"  [act] [route] at the end of {from_area} - W into "
                      f"{target_area}")
            door = InputPlan((Step("key", vk=VK_W, label="enter the room"),),
                             "route", f"through the door into {target_area}")
            if self.actuator.execute(door):
                self.stats["acted"] += 1
                # **A `W` THAT OPENS A PANEL DID NOT OPEN A DOOR**, and that
                # is what killed a raid on 2026-09-05: `coNO` tile 2 is an
                # obstacle, the walk stalled against it, the fall-through
                # pressed `W`, the Shipwreck panel came up -- and nothing
                # clicked it, so the loop walked twelve steps each way and
                # the expedition ended in a corridor with four shovels in
                # the pack.
                #
                # Positive evidence only: `_work_obstacle_panel` grabs a
                # frame and returns False unless a READABLE panel is on it,
                # so a `W` that really did open a door costs one screenshot
                # and nothing else. It has to come before the door watch,
                # because a panel is modal and the party will never leave
                # `from_area` while it stands.
                # Only where the SAVE puts an obstacle (tile content 4).
                # Live 2026-09-26 a W at the far end of coBC -- which holds
                # none -- met some other panel, this read it as an obstacle,
                # clicked the hand and returned, and the back-off below never
                # ran: three iterations stood at that door.
                if (self._corridor_has_obstacle(from_area)
                        and self._work_obstacle_panel(from_area)):
                    return
                # Walk THROUGH a door, not just press at it. Entering a room
                # is an animation and DD writes the save at the end of it, so
                # one 0.6s look decided the party was still in the corridor
                # while it was walking into the room -- the log then said
                # "still in coAB after 12 steps" about a party that finished
                # in rooB. It also has to watch for a FIGHT: a guarded curio
                # opens one on entry, and that is a save change that never
                # touches `in_area`.
                for _ in range(DOOR_WAITS):
                    time.sleep(DOOR_WAIT)
                    state = self._peek() or {}
                    if state.get("in_battle"):
                        self._say(f"  [act] [route] a fight started entering "
                                  f"{target_area}")
                        return
                    here = state.get("area") or ""
                    if here and here != from_area:
                        # SAY WHERE IT ENDED UP, which is not always where
                        # the route asked. Measured live 2026-09-03: rooA ->
                        # map click -> coAB -> `A` -> "at the end of coAB" ->
                        # `W` -> rooA, once every 24 s for the whole 900 s
                        # play budget, nine times over -- a `W` pressed at the
                        # wrong end puts the party back in the room it came
                        # from. That `A` is the bug this file no longer has;
                        # the report stays, because a door can still open onto
                        # somewhere the route did not name.
                        verdict = ("as advised" if here == target_area
                                   else f"NOT {target_area}")
                        self._say(f"  [act] [route] entered {here} ({verdict})")
                        return
        # **NO WALK-BACK.** There used to be one: an exhausted budget flipped
        # the heading and re-walked the corridor the other way, which was the
        # only way to tell its two ends apart while the direction was being
        # guessed. It is not guessed any more, so a budget that runs out means
        # the corridor is longer than `MAX_WALK_STEPS` or something is in the
        # way -- and reversing on either of those walks the party AWAY from
        # the exit it was heading for.
        self._say(f"  [act] [route] still in {from_area} after "
                  f"{MAX_WALK_STEPS} steps each way - stopping")

    def _peek(self):
        """Cheap look at the live save: current area and whether a fight is on.

        `live_state` is deliberately a millisecond-cost pure save reader (it
        does not use `DataLoader`), which is what makes polling it inside the
        walk loop reasonable.
        """
        try:
            from bridge.live_state import read_live_state
            from bridge.route import read_position
            if self.raid_path is None:
                return None
            roster = Path(str(self.raid_path).replace("raid.json", "roster.json"))
            st = read_live_state(raid_file=self.raid_path, roster_file=roster)
            return {"in_battle": bool(st.in_battle),
                    "area": read_position(self.raid_path) or "",
                    "torch": float(getattr(st, "torchlight", 0.0) or 0.0)}
        except Exception:                        # noqa: BLE001
            return None

    def _save_sig(self):
        """(mtime, size) of the raid save -- the same gate `_watch` polls on."""
        try:
            st = self.raid_path.stat()
            return (st.st_mtime, st.st_size)
        except (OSError, AttributeError):
            return None

    def _send_and_confirm(self, plan, attempts: int = 0,
                          ack_timeout: Optional[float] = None) -> bool:
        """Send, then prove it landed; retry if it did not.

        `attempts` overrides `SEND_ATTEMPTS` for callers whose plan STALES.
        Re-sending is only safe when the plan means the same thing the second
        time -- a keystroke does, a coordinate measured off a panel does not.
        See the map click, which passes 1.

        Two failures this closes, both seen in the first live run:

        * **Sent into an animation.** The save is written when an action
          resolves, so the poll that wakes the bridge fires while that action
          is still animating, and input then goes nowhere.
          `SETTLE_BEFORE_SEND` waits it out.
        * **Silently dropped.** Nothing noticed the keystroke had vanished;
          `Session.battle` had already consumed the turn_key, so the turn was
          never re-offered and the run stopped with the party waiting. The save
          is now the acknowledgement: no change means no action, and it retries.

        **The baseline belongs to the CALL, not to the attempt.** Re-reading it
        per attempt made the gap between attempts a blind spot: DD wrote the
        save while the bridge was settling for the retry, that write silently
        became the new baseline, and the retry then played a SECOND action into
        a turn already spent. Nothing reported it, because from the retry's
        point of view the save had never moved. With one baseline the same
        write is what it always was -- the acknowledgement of the first send --
        and the retry is cancelled instead of sent.

        Bounded by `SEND_ATTEMPTS`. If the input genuinely cannot land,
        hammering it is worse than stopping and saying so.
        """
        baseline = _UNREAD
        tries = attempts if attempts > 0 else SEND_ATTEMPTS
        for attempt in range(1, tries + 1):
            time.sleep(SETTLE_BEFORE_SEND if attempt == 1
                       else SETTLE_BEFORE_SEND * 2)
            if self.actuator.kill.check():
                self._say("  [act] KILL SWITCH - stopping")
                return False
            now = self._save_sig()
            if baseline is _UNREAD:
                # Read AFTER the settle, so the previous action's own write
                # cannot be mistaken for this one's.
                baseline = now
            elif baseline is not None and now != baseline:
                self._say(f"  [act] the save moved while waiting - attempt "
                          f"{attempt - 1} landed after all, not re-sending")
                return True
            # Focus is checked per ATTEMPT, not once per turn. `_handle`
            # cleared it up to 15s ago, and the most likely thing to happen in
            # between is the operator clicking their own terminal to read the
            # log -- at which point the click in this plan goes to whatever is
            # focused instead. Free when the game already has focus.
            if not self._focused_or_wait():
                return False
            if not self.actuator.execute(plan):
                self._say("  [act] the backend refused to send")
                return False
            if baseline is None:
                self._say("  [act] sent (no save to confirm against)")
                return True
            sent = time.perf_counter()
            if self._wait_for_save_change(baseline, ack_timeout or TURN_ACK_TIMEOUT):
                extra = "" if attempt == 1 else f" (attempt {attempt})"
                # The latency is printed because the timeout was set from a
                # corpus of HUMAN play, and one run of these numbers replaces
                # that with the bridge's own.
                self._say(f"  [act] sent{extra} - confirmed by the save after "
                          f"{time.perf_counter() - sent:.1f}s")
                return True
            # The wait ACTUALLY used, not the default: the torch waits 2 s and
            # the log said 15, which inflated a pace measurement 2026-09-28.
            self._say(f"  [act] no save change in "
                      f"{(ack_timeout or TURN_ACK_TIMEOUT):.0f}s - the "
                      f"input did not land (attempt {attempt}/{tries})")
            if attempt == 1:
                # At the FIRST failure, not the last: by the third the
                # operator has usually reacted, and whatever swallowed the
                # input is no longer on screen.
                self._diagnose_miss()
        self._say("  [act] giving up on this turn - play it yourself. If this "
                  "repeats, try --backend send-input.")
        return False

    def _diagnose_miss(self) -> None:
        """An input went nowhere. Report facts about it, not theories.

        Every candidate looks identical in the log -- a tutorial popup eating
        clicks, the terminal holding focus, the game disagreeing about whose
        turn it is, a backend this window does not accept. The screen shows
        all four and the save shows none, so the frame is banked here.
        """
        cur = refresh_window(self.window)
        if cur is None:
            self._say("  [act] the game window is gone")
            return
        if not cur.is_foreground:
            self._say("  [act] the game does NOT have focus - the input went "
                      "to whatever does, most likely this terminal")
        self._bank_frame(SEND_SHOT, "whatever swallowed the input is in it")

    def _wait_for_save_change(self, before, timeout=None) -> bool:
        deadline = time.perf_counter() + (ACK_TIMEOUT if timeout is None
                                          else timeout)
        while time.perf_counter() < deadline:
            time.sleep(0.25)
            if self._save_sig() != before:
                return True
            if self.actuator.kill.check():
                return False
        return False

    def _focused_or_wait(self) -> bool:
        """Make sure the GAME has focus at the moment of sending.

        Every working input path needs it -- the probe settled that -- and
        sending regardless types into whatever IS focused. In confirm mode
        that is guaranteed to be the operator's own terminal, because they
        just typed `y` into it: the approval prompt and the send have
        mutually exclusive focus requirements, so without this every
        supervised turn would refuse itself.

        Waiting rather than refusing, and NOT marking the turn as acted on
        timeout, so clicking the game back makes the same turn playable.
        """
        cur = refresh_window(self.window)
        if cur is not None and cur.is_foreground:
            return True
        # TAKE it first, then verify -- the same order `_wait_for_focus`
        # uses under `auto`, and for the same reason: an unattended run has
        # nobody to click, so without this every turn of every raid times out
        # and is LOST. It is only ever an ATTEMPT. Windows refuses
        # `SetForegroundWindow` from a background process often enough that
        # trusting it would be the bug the probe was written to catch, so the
        # wait loop below still runs and still decides.
        if cur is not None:
            _force_foreground(cur.hwnd)
            time.sleep(0.35)
            cur = refresh_window(self.window)
            if cur is not None and cur.is_foreground:
                self.window = cur
                self.resolved = self.resolved.frac.resolve(cur)
                return True
        # WAIT, in both modes. Skipping was wrong and the message that went
        # with it ("will retry") was false: `Session.battle` dedupes on
        # `_battle_key` and sets it BEFORE publishing, so a skipped turn is
        # never republished -- it is consumed, and the run stalls with the
        # advice already spent. Waiting is also right at launch, where the
        # operator has by definition just typed into the terminal.
        self._say("  [act] click the DARKEST DUNGEON window - sending once it "
                  f"has focus ({FOCUS_WAIT}s)...")
        for _ in range(FOCUS_WAIT * 4):
            cur = refresh_window(self.window)
            if cur is not None and cur.is_foreground:
                self.window = cur
                self.resolved = self.resolved.frac.resolve(cur)
                time.sleep(0.35)          # let the game settle after the click
                return True
            time.sleep(0.25)
        self._say("  [act] never got focus - this turn is LOST "
                  "(run.py will not re-offer it). Play it yourself.")
        return False

    def _report_played(self, update, key) -> None:
        """Tell whoever is listening which skill just went in.

        Only after the SAVE confirmed it. A use that never landed would
        suppress a legal skill for the rest of the fight, which is worse than
        the bug this feeds.
        """
        self._stuck_rotations = 0          # a turn landed: the fight moves
        if self.on_played is None:
            return
        skill_id = getattr(update, "skill_id", "")
        # `turn_key` is (round, hero guid, turns taken, enemies first) -- the
        # guid is the one identifier that survives duplicate classes.
        guid = key[1] if key and len(key) > 1 else None
        if not skill_id or guid is None:
            return
        try:
            self.on_played(guid, skill_id)
        except Exception:                        # noqa: BLE001
            pass          # a listener must never take the poll thread down

    def _say_once(self, tag: str, message: str) -> None:
        """Say something the first time only. For states that repeat by design.

        The poll loop republishes the same turn until it is played, so an
        honest "I am dropping this" would otherwise print several times a
        second. Silence was the alternative and it cost three runs of not
        knowing where a turn went.
        """
        if tag in self._said:
            return
        self._said.add(tag)
        self._say(message)

    def explain_again(self) -> None:
        """Forget what has already been said, so the next poll re-explains.

        **A refusal that repeats forever is the one case `_say_once` gets
        wrong.** Its job is to stop a turn's refusal printing several times a
        second while the poll loop republishes it, and for a turn that
        eventually gets played that is exactly right. For a raid that is STUCK
        it is the opposite: the actuator keeps refusing for the same reason
        every 25 s and says nothing, so the log shows room plans with no
        `[act]` line under them at all and reads as though the actuator had
        died.

        Live 2026-09-20, the last thing the overnight loop did before it was
        stopped: six identical room plans, the stall line firing correctly --
        and the actual cause, `the game offers no move at or near rooE - not
        clicking blind`, sixty lines earlier and looking unrelated. The stall
        report said "none of them has an input the game accepts from here",
        which is true and names nothing.

        Called once, at the give-up bound, so the noise `_say_once` exists to
        prevent does not come back: the next poll explains itself in full and
        then goes quiet again.
        """
        self._said.clear()

    def _approve(self) -> bool:
        """Ask, on stdin, before a combat turn. `--confirm` only.

        Two things about this that are not obvious from the call site. It gates
        **combat and nothing else** -- the five out-of-combat layers never
        consult `self.confirm`, because it was written when combat was all the
        actuator did. And it BLOCKS: `input()` runs on the poll thread, so an
        unanswered prompt is a stopped bridge, not a paused one.
        """
        try:
            return input("  [act] send it? [y/N/q] ").strip().lower().startswith("y")
        except (EOFError, KeyboardInterrupt):
            return False

    def summary(self) -> str:
        s = self.stats
        extra = ""
        if s["discarded"] or s["abandoned"]:
            extra = (f", {s['discarded']} item(s) discarded, "
                     f"{s['abandoned']} loot window(s) abandoned")
        return (f"actuator: {s['acted']} sent, {s['refused']} refused, "
                f"{s['declined']} declined, {s['paused']} pause(s), "
                f"{s['errors']} errors{extra}")


DEFAULT_BACKEND = "send-input"


def choose_backend(win, prefer: str = DEFAULT_BACKEND) -> Backend:
    """Pick a backend by name. The default is measured, not assumed.

    Probed against the real game on 2026-08-16, then corrected in live play on
    2026-08-17:

        keys   post-message / send-input / scancode  all WORK, focused only
        mouse  send-input   moves the REAL cursor, and CLICKS
               post-message moves the GAME's cursor and does NOT click

    The keyboard needed `wScan` filled in -- DD is SDL-based and drops a
    virtual key with scan code 0, which is why every backend looked dead at
    first. Background delivery does not work by any route: PostMessage reaches
    the game only while it is focused.

    **`post-message`'s click is the one thing the 2026-08-16 probe could not
    test, and it turned out not to work.** The probe validated MOVE only,
    because a stray real click during a probe is not something you can take
    back, and this file said so at the time. Aiming the boot sequence at the
    title screen settled it: `WM_MOUSEMOVE` landed -- the Campaign button
    *highlighted*, which also proves the coordinate and the client-rect maths
    are right -- and the `WM_LBUTTONDOWN`/`UP` that followed did nothing. That
    is the documented SDL split: SDL takes the keyboard from window messages
    and the mouse button partly from raw input, which `PostMessage` cannot
    supply.

    So the default is `send-input`, which costs the operator their pointer and
    actually plays the game. `post-message` is kept because its move works and
    it is the only path that leaves the cursor alone -- but nothing should
    choose it to click with.
    """
    backend = BACKENDS.get(prefer, BACKENDS[DEFAULT_BACKEND])(win)
    release_movement_keys(win)
    return backend


def release_movement_keys(win) -> bool:
    """Send a key-UP for every walking key. Harmless when none is held.

    A bridge killed between a walk's key-down and its key-up leaves the game
    believing the key is still held, and the party walks on its own the
    moment nothing else is pressed. Live 2026-09-25: after a run was stopped
    mid "walking A", every `D` press was undone as soon as it ended and the
    party slid back to the door -- which a fresh bridge would read as its own
    input failing. Real windows only: a stand-in has no game to release.
    """
    hwnd = getattr(win, "hwnd", None)
    if not isinstance(hwnd, int) or not hwnd or not user32.IsWindow(hwnd):
        return False
    sent = 0
    for vk in (VK_A, VK_D, VK_W, 0x25, 0x26, 0x27, 0x28):   # + the arrows
        scan = user32.MapVirtualKeyW(vk, MAPVK_VK_TO_VSC)
        up = INPUT(type=INPUT_KEYBOARD,
                   u=_INPUTunion(ki=KEYBDINPUT(wVk=vk, wScan=scan,
                                               dwFlags=KEYEVENTF_KEYUP)))
        sent += user32.SendInput(1, ctypes.byref(up), ctypes.sizeof(INPUT))
    return sent > 0


# ── CLI ──

def _demo() -> int:
    """Show a plan for a synthetic turn, with no game and no window."""
    from bridge.layout import load as load_layout

    class _FakeWin:
        client_origin = (100, 50)
        client_size = (1600, 900)

        def to_screen(self, fx, fy):
            # Rounds, like the real GameWindow. Truncating here made the demo
            # print points a pixel off from what a live run would send.
            return (self.client_origin[0] + int(round(fx * self.client_size[0])),
                    self.client_origin[1] + int(round(fy * self.client_size[1])))

        def to_client(self, sx, sy):
            return sx - 100, sy - 50

    lay = load_layout()
    if lay is None:
        print("no layout to demo with")
        return 1
    resolved = lay.resolve(_FakeWin())

    @dataclass(frozen=True)
    class U:
        skill_slot: int
        side: str
        rank: int
        headline: str = ""
        provisional: bool = False
        enemies_first: int = 0
        movers: tuple = ()

    print("A 1600x900 window at (100,50) - what would be sent:\n")
    for u in (U(2, "enemy", 0, "SMITE on rank 1"),
              U(5, "hero", 2, "MOVE to rank 3"),
              U(6, "", -1, "pass"),
              U(1, "enemy", 1, "look-ahead", provisional=True),
              U(3, "enemy", 0, "after 2 enemy turns", enemies_first=2)):
        print(plan_battle(u, resolved), "\n")
    return 0


class OfflineWindow:
    """A synthetic client rect, for planning with no game running.

    The planner's correctness does not depend on a real window -- only on the
    layout resolving to *some* client area -- so the whole capture corpus can be
    replayed through it offline. That is the point of the dry-run stage: the
    first bugs get found against archived saves, not against a live profile.
    """

    def __init__(self, size=(1600, 900), origin=(100, 50)):
        self.client_size = size
        self.client_origin = origin

    def to_screen(self, fx, fy):
        return (self.client_origin[0] + int(round(fx * self.client_size[0])),
                self.client_origin[1] + int(round(fy * self.client_size[1])))

    def to_client(self, sx, sy):
        return sx - self.client_origin[0], sy - self.client_origin[1]


def replay_plans(directory: Path, model: Optional[str], limit: int = 0) -> int:
    """Plan every captured turn, send nothing, and report what would happen.

    This is stage 1 of the three-stage rollout: dry-run over the ~390 archived
    `(raid, roster, map)` triples, then supervised live, then unattended. It
    reuses `run.py`'s own `Session`/`replay` -- the actuator subscribes to the
    same `Update` stream the overlay draws, so what is validated here is the
    real path, not a parallel reimplementation of it.
    """
    from bridge.advisor import Advisor
    from bridge.layout import load as load_layout
    from bridge.run import Session, replay

    lay = load_layout()
    if lay is None:
        print("no layout - run `python bridge/overlay.py --calibrate` first")
        return 1
    resolved = lay.resolve(OfflineWindow())

    plans: list[InputPlan] = []
    seen = {"battle": 0, "other": 0}

    def on_update(update):
        if getattr(update, "phase", "") != "battle":
            seen["other"] += 1
            return
        seen["battle"] += 1
        plans.append(plan_battle(update, resolved))

    advisor = Advisor(model)
    session = Session(advisor, on_update=on_update)
    replay(session, directory)

    print(f"\n{'=' * 62}\nACTUATOR DRY RUN - nothing was sent\n{'=' * 62}")
    print(f"battle updates seen : {seen['battle']}")
    print(f"other-phase updates : {seen['other']} (not actuated)")
    if not plans:
        print("no battle updates in this capture set")
        return 0

    ok = [p for p in plans if p.actionable]
    print(f"plans built         : {len(plans)}")
    print(f"  actionable        : {len(ok)} ({100 * len(ok) / len(plans):.1f}%)")
    print(f"  refused           : {len(plans) - len(ok)}")

    reasons: dict[str, int] = {}
    for p in plans:
        if p.refusal:
            key = p.refusal.split(" - ")[0].split(" (")[0]
            reasons[key] = reasons.get(key, 0) + 1
    for reason, n in sorted(reasons.items(), key=lambda kv: -kv[1]):
        print(f"      {n:4d}  {reason}")

    # The two structural invariants a plan must never violate.
    bad_click = [p for p in ok
                 if any(s.kind == "click" and s.screen == (0, 0) for s in p.steps)]
    bad_shape = [p for p in ok
                 if not any(s.kind == "click" for s in p.steps)
                 and not any(s.kind == "key" for s in p.steps)]
    print(f"\nplans with a degenerate (0,0) click : {len(bad_click)}")
    print(f"plans with no steps at all          : {len(bad_shape)}")
    keys = sum(1 for p in ok for s in p.steps if s.kind == "key")
    clicks = sum(1 for p in ok for s in p.steps if s.kind == "click")
    print(f"would have sent                     : {keys} keys, {clicks} clicks")
    return 1 if (bad_click or bad_shape) else 0


VK_H = 0x48          # contextual help. CONFIRMED to work when pressed by hand
                     # on the real save, so it is a valid probe key and a
                     # negative result means the INJECTION failed, not the key.
# VK_TAB is imported above, from actuation/keys.py. It used to be
# redefined here, to the same 0x09, which shadowed the earlier one for
# everything below this point -- harmless because the values matched,
# and exactly the shape of a bug that is not harmless when they stop
# matching. The split made it visible and it is gone.

PROBE_KEYS = {"h": (VK_H, "the contextual help overlay"),
              "esc": (VK_ESCAPE, "the in-game menu"),
              "tab": (VK_TAB, "the map/inventory toggle (in a raid only)")}

# Token information class for the elevation query.
TOKEN_QUERY = 0x0008
TOKEN_ELEVATION = 20


def _is_elevated(pid: Optional[int] = None) -> Optional[bool]:
    """Is this process (or `pid`) running elevated? None if unknowable.

    This matters because of UIPI: Windows refuses `PostMessage`/`SendMessage`
    from a lower-integrity process into a higher-integrity window, and refuses
    synthetic input into an elevated foreground window from a non-elevated
    sender. If Steam (and so the game) was started as administrator and the
    bridge was not, BOTH backends fail with the API still reporting success --
    which is exactly the shape of a probe that returns three negatives while
    the same key works by hand.
    """
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    try:
        if pid is None:
            # The pseudo-handle from GetCurrentProcess() is -1 and needs a
            # declared restype, or ctypes truncates it on x64 and every
            # subsequent call fails -- which reads as "unknowable" rather than
            # as a bug. shell32 answers the same question directly.
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        else:
            h_proc = ctypes.windll.kernel32.OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not h_proc:
                return None
        token = wt.HANDLE()
        if not advapi.OpenProcessToken(h_proc, TOKEN_QUERY, ctypes.byref(token)):
            return None
        try:
            elevation = wt.DWORD()
            size = wt.DWORD()
            ok = advapi.GetTokenInformation(
                token, TOKEN_ELEVATION, ctypes.byref(elevation),
                ctypes.sizeof(elevation), ctypes.byref(size))
            return bool(elevation.value) if ok else None
        finally:
            ctypes.windll.kernel32.CloseHandle(token)
    except Exception:                          # noqa: BLE001
        return None


PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def refresh_window(win):
    """Re-read the window rect, tolerating the window having gone.

    A windowed game can be moved between the probe's countdown and its
    keystroke, and SetForegroundWindow on a stale handle is a silent
    no-op that would look like the backend failing.
    """
    try:
        from bridge.window import refresh
        return refresh(win)
    except Exception:
        return None


def _countdown(secs: int, msg: str) -> None:
    for i in range(secs, 0, -1):
        print(f"\r  {msg} in {i}... ", end="", flush=True)
        time.sleep(1)
    print("\r" + " " * 60 + "\r", end="", flush=True)


class _Inconclusive:
    """Distinct from both False and None.

    `None` already means "nobody observed this round". A send that Windows
    confirms delivering, over a screen that then does not change, is a third
    thing: the send worked and the OBSERVATION was uninformative. Collapsing
    it into either of the other two is what made the probe blame the backend
    for a key that is inert on the front end.
    """

    def __bool__(self) -> bool:                 # never truthy by accident
        return False

    def __repr__(self) -> str:
        return "INCONCLUSIVE"


INCONCLUSIVE = _Inconclusive()


def _ask(question: str) -> Optional[bool]:
    """Ask the human. **`None` means nobody answered**, which is not "no".

    This returned `False` on `EOFError`, and that is how an autonomous run --
    no TTY, stdin at the null device -- printed a confident "Neither reached
    the game" three lines under its own `reached input : yes`. An unanswered
    question is the same class of thing as `recognise` seeing a near-tie: the
    honest report is "I do not know", and every caller has to handle it.
    """
    if not sys.stdin or not sys.stdin.isatty():
        print(f"  {question} [y/N] -- no terminal to ask, recording UNKNOWN")
        return None
    try:
        return input(f"  {question} [y/N] ").strip().lower().startswith("y")
    except (EOFError, OSError):
        return None


class _ScreenWatch:
    """Answer the probe's question from the SCREEN, so no human is needed.

    The probe key toggles something visible, so "did it appear?" is a question
    about pixels and `bridge/screen.py` is already a pixel reader. What makes
    this honest rather than a difference of two frames is the CONTROL: DD's
    menus animate -- the title screen's fire moves every frame -- so a bare
    before/after delta reads the animation as a keystroke.

    `idle` samples that motion first, at the same spacing the real test uses,
    and a verdict needs a change well clear of it. Same shape of argument as
    the mouse probe parking the pointer on rank 1 and posting to rank 4: the
    effect has to be attributable to the send and to nothing else.
    """

    def __init__(self, win, factor: float = 4.0, floor: float = 0.02):
        # Deferred like every other `screen` use in this module: it is the
        # dev-only half of the bridge and must not be imported to plan a turn.
        from bridge import screen as screen_mod   # noqa: PLC0415
        self._screen = screen_mod
        self.win, self.factor, self.floor = win, factor, floor
        self._before = None
        self.idle = self._measure_idle()

    def _sig(self):
        img = self._screen.grab(self.win)
        return None if img is None else self._screen.signature(img)

    def _measure_idle(self) -> float:
        seen, a = [], self._sig()
        for _ in range(3):
            time.sleep(0.7)
            b = self._sig()
            if a is not None and b is not None:
                seen.append(self._screen.distance(a, b))
            a = b
        return max(seen) if seen else 0.0

    @property
    def threshold(self) -> float:
        return max(self.floor, self.idle * self.factor)

    def before(self) -> None:
        self._before = self._sig()

    def after(self) -> Optional[bool]:
        now = self._sig()
        if self._before is None or now is None:
            return None
        d = self._screen.distance(self._before, now)
        print(f"  screen change : {d:.4f} "
              f"(idle {self.idle:.4f}, needs > {self.threshold:.4f})")
        return d > self.threshold


def _force_foreground(hwnd) -> None:
    """Take the foreground, which `SetForegroundWindow` alone will not do.

    Windows refuses the call from a process that does not own the foreground,
    returning success and merely flashing the taskbar -- which is why an
    unattended probe round reported SKIPPED (never focused) straight after one
    that worked. Attaching to the current foreground thread's input queue
    makes this process a legitimate caller for the duration.

    Best-effort throughout: every step is advisory, and the CALLER still
    verifies `is_foreground` rather than trusting any of it.
    """
    try:
        SW_RESTORE = 9
        user32.ShowWindow(hwnd, SW_RESTORE)
        cur = user32.GetForegroundWindow()
        mine = kernel32.GetCurrentThreadId()
        theirs = user32.GetWindowThreadProcessId(cur, None)
        attached = bool(user32.AttachThreadInput(theirs, mine, True))
        try:
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
            user32.SetActiveWindow(hwnd)
        finally:
            if attached:
                user32.AttachThreadInput(theirs, mine, False)
    except Exception:                           # noqa: BLE001
        pass


def _wait_for_focus(win, secs: int = 10, auto: bool = False):
    """Block until the GAME is genuinely foreground, or give up.

    `SetForegroundWindow` from a background process is restricted by Windows --
    it commonly just flashes the taskbar and returns, so a probe that calls it
    and sends anyway is testing whether the keystroke reaches the TERMINAL.
    That is the likeliest reason the first probe reported SendInput as "no
    effect" for a key that works by hand.

    So focus is not requested, it is *waited for and verified*.

    `auto` tries to take it first (still verifying, never assuming) so an
    unattended run is not blocked on a human clicking. The verification is
    what makes that safe: `SetForegroundWindow` from a background process
    frequently only flashes the taskbar, and the round is SKIPPED rather than
    scored if the game did not actually come forward.
    """
    if auto:
        _force_foreground(win.hwnd)
        for _ in range(secs * 4):
            cur = refresh_window(win) or win
            if cur.is_foreground:
                return cur
            time.sleep(0.25)
        return None
    print(f"  click on the DARKEST DUNGEON window now ({secs}s)...")
    for _ in range(secs * 4):
        cur = refresh_window(win)
        if cur is not None and cur.is_foreground:
            time.sleep(0.4)                   # let the game settle
            return cur
        time.sleep(0.25)
    return None


def _arm_keystate(vk: int) -> None:
    """Clear `GetAsyncKeyState`'s sticky bit BEFORE injecting.

    Order is the whole trick, and getting it wrong makes the check useless in
    a way that looks like a finding: the low bit means "pressed since the
    previous call", so calling it *after* the send consumes the very evidence
    being tested and the answer is False no matter what. The first version of
    this probe did exactly that and reported "blocked before the game" for an
    injection that may well have worked.
    """
    user32.GetAsyncKeyState(vk)


def _keystate_saw_it(vk: int) -> bool:
    """Did the injected key show up in global key state?

    Separates two failures a human cannot tell apart by looking at the game:

        saw it, game did nothing  -> the game rejects synthetic input
        never saw it              -> blocked before the game (UIPI, a bad call)

    Only meaningful for SendInput. PostMessage delivers straight into one
    window's message queue and deliberately never touches global key state, so
    a False there is by design, not by failure.
    """
    return bool(user32.GetAsyncKeyState(vk) & 0x0001)


VK_F13 = 0x7C            # no app binds it; safe to fire at the desktop


def sendinput_works_on_this_machine() -> Optional[bool]:
    """Does SendInput reach the OS at all, independent of the game?

    The control this probe was missing. Without it, "the game did not react"
    conflates *DD rejects synthetic input* with *SendInput is not working here
    at all* -- and those have completely different consequences: the first
    means layer 3 needs a driver-level sender, the second means something on
    this machine is eating injections and the game is innocent.

    F13 is used because nothing binds it, so firing it at whatever has focus
    is harmless.
    """
    try:
        _arm_keystate(VK_F13)
        down = INPUT(type=INPUT_KEYBOARD, u=_INPUTunion(ki=KEYBDINPUT(wVk=VK_F13)))
        up = INPUT(type=INPUT_KEYBOARD,
                   u=_INPUTunion(ki=KEYBDINPUT(wVk=VK_F13, dwFlags=KEYEVENTF_KEYUP)))
        n = user32.SendInput(1, ctypes.byref(down), ctypes.sizeof(INPUT))
        time.sleep(0.03)
        n += user32.SendInput(1, ctypes.byref(up), ctypes.sizeof(INPUT))
        if n != 2:
            return False
        return _keystate_saw_it(VK_F13)
    except Exception:                          # noqa: BLE001
        return None


def _probe(win, probe_key: str = "h", auto: bool = False) -> int:
    """Settle PostMessage vs SendInput against the running game.

    Two things are being asked, and conflating them is how this test goes
    wrong:

      1. Does the backend reach the game AT ALL (game focused)?
      2. Does it reach the game while something ELSE is focused?

    (2) is the whole reason to prefer PostMessage -- it is what decides whether
    you keep the use of your machine while the bridge plays. SendInput cannot
    pass (2) by construction: it injects at the system level into whatever has
    focus, so it is only asked (1).

    The keystroke is `H` (contextual help). It is visible, harmless, and
    toggles, so the probe leaves the game exactly as it found it.

    A countdown rather than a prompt: with a prompt, the terminal still has
    focus at the instant the key fires, so a SendInput `H` lands in the
    terminal and the backend looks broken when it is not.
    """
    print("\n" + "=" * 62)
    print("BACKEND PROBE")
    print("=" * 62)

    # --- environment first: the thing that makes every send fail silently ---
    mine = _is_elevated()
    theirs = _is_elevated(win.pid)
    print(f"  this process elevated : {mine}")
    print(f"  game elevated         : {theirs}")
    if theirs and not mine:
        print()
        print("  STOP. The game is ELEVATED and this process is not.")
        print("  Windows (UIPI) blocks both synthetic input and posted messages")
        print("  from a lower-integrity process into a higher-integrity window,")
        print("  while the API calls still report success. That alone explains a")
        print("  probe failing on a key that works when pressed by hand.")
        print()
        print("  Fix: run this terminal as Administrator, or stop launching")
        print("  Steam/Darkest Dungeon elevated. Then re-run --probe.")
        return 2

    ctl = sendinput_works_on_this_machine()
    print(f"  SendInput reaches OS : {ctl}")
    if ctl is False:
        print("  -> injection is blocked on this MACHINE, before any game.")
        print("     An anti-cheat driver or input filter is the usual cause.")
        return 2

    vk, expect = PROBE_KEYS[probe_key]
    print(f"  probe key             : {_vk_name(vk)}  ({expect})")
    print("  It toggles, so each test undoes itself.\n")

    results = {}
    watch = None
    if auto:
        watch = _ScreenWatch(win)
        print(f"  observer              : screen "
              f"(idle motion {watch.idle:.4f}, "
              f"needs > {watch.threshold:.4f})")
        print()

    # --- rounds 1-2: does the backend reach the game when it IS focused? ---
    for backend in (PostMessageBackend(win), SendInputBackend(win),
                    ScanCodeBackend(win)):
        print(f"-- {backend.name}, game focused --")
        cur = _wait_for_focus(win, auto=auto)
        if cur is None:
            print("  the game never became foreground - skipping this round.")
            print("  (that is a result too: nothing can be concluded here)")
            results[backend.name] = None
            print()
            continue
        uses_keystate = backend.name in ("send-input", "scancode")
        if uses_keystate:
            _arm_keystate(vk)              # clear BEFORE, or the check is a lie
        if watch:
            watch.before()
        ok = backend.key(vk)
        injected = _keystate_saw_it(vk) if uses_keystate else None
        time.sleep(0.7)
        print(f"  API call      : {'accepted' if ok else 'REJECTED by Windows'}")
        if injected is not None:
            print(f"  reached input : "
                  f"{'yes' if injected else 'NO - blocked before the game'}")
        # The screen is read BEFORE the key is toggled back, or the effect
        # being measured has already been undone.
        seen = watch.after() if watch else _ask(f"did {expect} appear?")
        backend.key(vk)                        # toggle back
        # An injection Windows confirms delivering, followed by no visible
        # change, does NOT distinguish "DD ignored it" from "this key does
        # nothing on the screen that happens to be up" -- `H` is contextual
        # help and is inert in the front end. Recording that as a failure is
        # how the probe blames the backend for its own choice of screen.
        if seen is False and injected:
            seen = INCONCLUSIVE
            print("  -> INCONCLUSIVE: the OS confirms the key was injected,"
                  " so the send worked; the screen")
            print("     did not change, which on this screen may just mean"
                  " the key is inert here.")
            print("     Re-run in a raid or the Hamlet.")
        results[backend.name] = seen
        print()

    # --- round 3: background delivery (PostMessage only) ---
    print("-- post-message, game NOT focused --")
    print("  Click THIS TERMINAL and leave it focused. Your cursor will not")
    print("  move and the game will not be raised. The key is left ON so you")
    print("  can go and look afterwards.")
    if not auto:
        _countdown(6, "sending to the unfocused game")
    cur = refresh_window(win) or win
    if cur.is_foreground:
        print("  NOTE: the game is STILL foreground, so this round does not")
        print("        test background delivery. Click the terminal, re-run.")
    pm = PostMessageBackend(cur)
    if watch and not cur.is_foreground:
        watch.before()
        pm.key(vk)
        time.sleep(0.7)
        background = watch.after()
    elif watch:
        # The game is still foreground, so this round cannot test background
        # delivery at all. That is UNKNOWN, not a failure -- scoring it would
        # blame PostMessage for a condition the probe failed to set up.
        print("  game is still foreground - background delivery NOT tested")
        pm.key(vk)
        time.sleep(0.7)
        background = None
    else:
        pm.key(vk)
        print("  sent. Alt-tab to Darkest Dungeon and look.")
        background = _ask("was it ALREADY showing when you got there?")
    pm.key(vk)

    # --- verdict ---
    print("\n" + "=" * 62)
    print("RESULT")
    print("=" * 62)
    for name, worked in results.items():
        state = ("SKIPPED (never focused)" if worked is None
                 else "INJECTED, no visible change" if worked is INCONCLUSIVE
                 else "WORKS" if worked else "no effect")
        print(f"  {name:14s} focused    : {state}")
    print(f"  post-message   background  : "
          f"{'UNKNOWN (not observed)' if background is None else 'WORKS' if background else 'no effect'}")
    print()
    if background:
        print("  -> PostMessageBackend. The bridge plays in the background and")
        print("     you keep the machine. Best case.")
    elif results.get("post-message") is True:
        print("  -> PostMessage reaches the game only when focused, which buys")
        print("     nothing over SendInput. Use SendInputBackend; F9 stops it.")
    elif results.get("scancode") is True:
        print("  -> ScanCodeBackend. DD reads SCAN CODES, not virtual keys -")
        print("     the SDL behaviour. Set backend=ScanCodeBackend(win). The")
        print("     game must stay focused; F9 is the kill switch.")
    elif results.get("send-input") is True:
        print("  -> SendInputBackend. DD ignores posted messages (SDL reads the")
        print("     mouse through raw input). The game must stay focused and the")
        print("     cursor really moves. F9 is the kill switch.")
    elif any(v is INCONCLUSIVE for v in results.values()):
        print("  -> NO VERDICT: Windows confirms the key reached the input")
        print("     system, but nothing on screen changed. On the front end")
        print("     that is expected -- the contextual help overlay does not")
        print("     draw there -- so this says nothing against the backend.")
        print("     Re-run in a raid or the Hamlet to settle it.")
    elif all(v is None for v in results.values()):
        print("  -> NO VERDICT, which is not the same as nothing working.")
        print("     Every round recorded UNKNOWN. Either nothing observed the")
        print("     result (no terminal to ask, no --auto), or the probe key is")
        print("     inert on the screen that was up -- check the `reached input`")
        print("     lines above: `yes` means the send itself is fine and only")
        print("     the OBSERVATION failed. Re-run in a raid or the Hamlet,")
        print("     where the contextual help overlay actually draws.")
    else:
        print("  -> Neither reached the game. Rule out, in order:")
        print("     1. elevation mismatch - reported above, the usual cause")
        print("     2. the game never actually had focus (now verified, not")
        print("        assumed, so a SKIPPED round means exactly that)")
        print("     3. DD rejects both synthetic paths, which would mean layer 3")
        print("        needs a driver-level sender - a different project. Do not")
        print("        paper over that with retries.")
    return 0

def _geometry(win=None, samples: int = 40) -> int:
    """Report the desktop this machine has, and whether clicks can hit it.

    The one command to run on a NEW machine, or after moving a monitor. It
    answers three separate questions that are easy to conflate:

      1. what the desktop actually is -- span, origin, per-monitor DPI;
      2. whether this process sees it truthfully (DPI awareness);
      3. whether a click can be placed on an arbitrary pixel of it.

    (3) is the only one that matters in the end, and it is measured rather
    than argued: it moves the cursor to sampled points across the WHOLE
    virtual desktop and reads back where it landed.
    """
    print()
    print("=" * 62)
    print("DESKTOP GEOMETRY")
    print("=" * 62)
    vx, vy, vw, vh = _virtual_desktop()
    print(f"  virtual desktop : {vw}x{vh} at ({vx},{vy})")
    print(f"  primary         : {user32.GetSystemMetrics(0)}x"
          f"{user32.GetSystemMetrics(1)}")
    if vx or vy:
        print("  NOTE: the origin is non-zero, so a monitor sits left of or")
        print("        above the primary. Absolute coordinates must SUBTRACT")
        print("        it -- ignoring it puts every click on that monitor at")
        print("        its far edge.")

    aware = None
    try:
        val = ctypes.c_int()
        ctypes.windll.shcore.GetProcessDpiAwareness(None, ctypes.byref(val))
        aware = val.value
    except Exception:                           # noqa: BLE001
        pass
    names = {0: "UNAWARE", 1: "system", 2: "per-monitor"}
    print(f"  DPI awareness   : {names.get(aware, aware)}")

    scaled = []
    try:
        class _MI(ctypes.Structure):
            _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", wt.RECT),
                        ("rcWork", wt.RECT), ("dwFlags", wt.DWORD)]

        proc = ctypes.WINFUNCTYPE(ctypes.c_int, wt.HMONITOR, wt.HDC,
                                  ctypes.POINTER(wt.RECT), wt.LPARAM)

        def _each(hmon, _hdc, _rc, _lp):
            info = _MI()
            info.cbSize = ctypes.sizeof(_MI)
            user32.GetMonitorInfoW(hmon, ctypes.byref(info))
            r = info.rcMonitor
            dx, dy = ctypes.c_uint(), ctypes.c_uint()
            try:
                ctypes.windll.shcore.GetDpiForMonitor(
                    hmon, 0, ctypes.byref(dx), ctypes.byref(dy))
            except Exception:                   # noqa: BLE001
                dx.value = 96
            scale = dx.value / 96.0
            scaled.append(scale)
            print(f"    monitor  ({r.left},{r.top})-({r.right},{r.bottom})  "
                  f"{dx.value} dpi  {scale:.2f}x")
            return 1

        user32.EnumDisplayMonitors(None, None, proc(_each), 0)
    except Exception as exc:                    # noqa: BLE001
        print(f"    could not enumerate monitors: {exc}")

    if aware == 0 and any(s != 1.0 for s in scaled):
        print()
        print("  WARNING: this process is DPI-UNAWARE and a monitor is")
        print("  scaled. Windows then reports virtualised coordinates while")
        print("  input is placed in physical ones, and the two disagree by")
        print("  the scale factor. The round trip below is the thing to")
        print("  believe -- if it passes, the pair are consistent anyway.")

    print()
    print("-" * 62)
    print(f"ROUND TRIP  ({samples} points across the whole desktop)")
    print("-" * 62)
    if win is None:
        from bridge.window import find_window   # noqa: PLC0415
        win = find_window()
    if win is None:
        print("  the game is not running, and a backend needs a window.")
        print("  Geometry above is still valid; re-run with the game up to")
        print("  measure the round trip.")
        return 1

    import random                               # noqa: PLC0415  (dev-only)
    backend = SendInputBackend(win)
    rng = random.Random(0)
    points = [(vx, vy), (vx + vw - 1, vy + vh - 1),
              (vx + vw // 2, vy + vh // 2)]
    points += [(vx + rng.randrange(vw), vy + rng.randrange(vh))
               for _ in range(max(0, samples - 3))]
    errors = []
    for point in points:
        backend.move(point)
        time.sleep(0.02)
        errors.append(backend.last_error or 0)
    worst = max(errors)
    exact = sum(1 for e in errors if e == 0)
    print(f"  exact         : {exact}/{len(errors)}")
    print(f"  worst error   : {worst} px")
    for cut in (1, 2):
        n = sum(1 for e in errors if e <= cut)
        print(f"  within {cut} px    : {n}/{len(errors)}")
    print()
    if worst == 0:
        print("  -> every sampled pixel is addressable exactly.")
    elif worst <= 1:
        print("  -> within one pixel everywhere. Some pixels are simply not")
        print("     addressable by absolute input on this display; `move`")
        print("     converges to the nearest and stops rather than hunting.")
        print("     A click target is tens of pixels, so this is fine.")
    else:
        print(f"  -> OFF BY UP TO {worst} px, which is enough to miss a small")
        print("     button. Suspect DPI scaling with an unaware process, or a")
        print("     display driver that remaps the pointer. Do not arm the")
        print("     actuator until this reads 1 or less.")
    return 0 if worst <= 1 else 2


def _probe_click(win, allow_click: bool = False) -> int:
    """Does synthetic MOUSE input reach the game? Movement only by default.

    Keys and clicks are separate questions, and the answer to one does not
    imply the other. SDL reads the keyboard from `WM_KEYDOWN` and scan codes --
    which is why the keyboard probe passed once `wScan` was filled in -- but it
    commonly reads the MOUSE through raw input, which `PostMessage` cannot
    supply at all. A combat turn is one key plus one click, so a working key
    path is only half an actuator.

    Movement is tested rather than clicking because it is unambiguous and
    cannot do anything: DD highlights whatever the cursor is over, so a hover
    response proves the game accepted a synthetic mouse position. The real
    click is opt-in (`--allow-click`) and aimed at a combat rank coordinate,
    which in the Hamlet is scenery.
    """
    from bridge.layout import load as load_layout

    print("\n" + "=" * 62)
    print("MOUSE PROBE - movement only unless --allow-click was passed")
    print("=" * 62)

    lay = load_layout()
    if lay is None:
        print("  no layout to aim with")
        return 1
    resolved = lay.resolve(win)
    target = resolved.point("enemy", 0)
    print(f"  aiming at enemy rank 1: {target}")
    if resolved.aspect_warning:
        print(f"  WARNING: {resolved.aspect_warning}")

    results = {}

    print("\n-- send-input: moves the REAL cursor --")
    cur = _wait_for_focus(win)
    if cur is None:
        print("  the game never became foreground - skipping")
    else:
        before = _cursor_pos()
        SendInputBackend(cur).move(target)
        time.sleep(0.4)
        after = _cursor_pos()
        moved = (after is not None
                 and abs(after[0] - target[0]) < 4
                 and abs(after[1] - target[1]) < 4)
        print(f"  cursor {before} -> {after}")
        print(f"  landed on target: {moved}   (objective - no judgement needed)")
        results["send-input move"] = moved

    # The real pointer is PARKED on rank 1 and PostMessage aims at rank 4.
    #
    # Aiming both rounds at the same point cannot discriminate: the SendInput
    # round leaves the real cursor exactly there, so the game highlights that
    # rank whether or not the posted message did anything, and the honest
    # answer is "yes" either way. Two DIFFERENT ranks make it decidable -- if
    # the highlight moves to rank 4 while the pointer visibly stays on rank 1,
    # only the posted message can have caused it.
    decoy = resolved.point("enemy", 0)
    probe_at = resolved.point("enemy", 3)
    print("\n-- post-message: moves the GAME's cursor, not yours --")
    cur = _wait_for_focus(win) or win
    SendInputBackend(cur).move(decoy)          # park the REAL pointer on rank 1
    time.sleep(0.5)
    parked = _cursor_pos()
    print(f"  real pointer parked on enemy rank 1  {parked}")
    print(f"  posting a move to enemy rank 4       {probe_at}")
    PostMessageBackend(cur).move(probe_at)
    time.sleep(0.6)
    after = _cursor_pos()
    print(f"  real cursor now {after}  (must still be rank 1)")
    if after != parked:
        print("  WARNING: the real cursor moved - this round is NOT conclusive.")
    results["post-message move"] = _ask(
        "did the highlight move to enemy rank 4 (the FAR one) while your "
        "pointer stayed on rank 1?")

    if allow_click:
        print("\n-- one real LEFT CLICK at that point --")
        print("  In the Hamlet there is no combat rank there, so it lands on")
        print("  scenery. Watch for any reaction at all.")
        _countdown(5, "clicking")
        cur = _wait_for_focus(win) or win
        SendInputBackend(cur).click(target)
        results["send-input click"] = _ask("did the game react to the click?")

    print("\n" + "=" * 62)
    print("RESULT")
    print("=" * 62)
    for k, v in results.items():
        print(f"  {k:20s}: {'WORKS' if v else 'no effect'}")
    print()
    if results.get("post-message move"):
        print("  -> PostMessage moves the game's cursor WITHOUT moving yours.")
        print("     Best available: the game must hold focus, but your pointer")
        print("     stays where you left it.")
    elif results.get("send-input move"):
        print("  -> SendInput only - the real cursor is moved. Expected if DD")
        print("     reads the mouse through raw input. Keep F9 in reach and do")
        print("     not touch the mouse while it plays.")
    else:
        print("  -> Neither reached the game's cursor. CLICKS are the blocker,")
        print("     not keys. Do not start a supervised raid on this: the")
        print("     actuator would press a skill and then fail to target it,")
        print("     which is worse than doing nothing.")
    return 0


#: Longest a torch change may take to reach `persist.raid.json`. The save is
#: written per turn in a raid, so a change made between turns is not instant --
#: and a probe that reads too early records a working pixel as a dead one.
TORCH_PROBE_SETTLE = 2.5


def _probe_torch(win, profile=None, prefer: str = DEFAULT_BACKEND) -> int:
    """WHICH PIXEL IS THE TORCH? Let the save answer, one click at a time.

    The snuff is the last piece of Fran's tutorial route that does not work,
    and it is worth ~454 gold a fight on the Old Road -- the single largest
    term in `data/loot_loader.py`'s model. Two live runs have now sent
    `SHIFT+CTRL+CLICK` at a torch that did not move, and the failure is silent
    in the worst way: the click is accepted by the window, the log says the
    torch was put out, and the game disagrees.

    **It is not "clicking is broken".** The very next line of the same log is a
    combat click confirmed by the save. What has never been separated is:

      1. the MODIFIER not reaching SDL in time (fixed blind by `MOD_DWELL`, and
         a fix you cannot see is a fix you cannot trust);
      2. the PIXEL not being the widget.

    So the probe re-sends the two already-refuted points FIRST, now with the
    dwell. If one of them answers, the dwell was the bug and the geometry was
    right all along; if neither does, the grid settles the geometry. Either
    way one live raid produces a measured constant instead of a third guess.

    `persist.raid.json` carries `torchlight`, so every attempt is graded by
    the game rather than by us -- the same acknowledgement rule the town layer
    runs on. **It costs light**: a working click takes 25 off, which is the
    proof it worked. Run it when the plan wanted the torch down anyway.
    """
    from bridge.layout import load as load_layout        # noqa: PLC0415
    from bridge.live_state import find_profile, read_live_state  # noqa: PLC0415

    print("\n" + "=" * 62)
    print("TORCH PROBE - the game must be IN A RAID")
    print("=" * 62)

    prof = Path(profile) if profile else find_profile()
    raid = Path(prof) / "persist.raid.json"
    roster = Path(prof) / "persist.roster.json"
    print(f"  profile: {prof}")
    if not raid.exists():
        print(f"  no {raid.name} - the game is not in a raid")
        return 1

    def torch():
        try:
            st = read_live_state(raid_file=raid, roster_file=roster)
            return float(getattr(st, "torchlight", 0.0) or 0.0)
        except Exception:                                # noqa: BLE001
            return None

    level = torch()
    if level is None:
        print("  cannot read torchlight")
        return 1
    print(f"  torch now: {level:.0f}")
    if level <= 0.0:
        print("  already out - nothing this probe could detect. Relight first.")
        return 1

    lay = load_layout()
    if lay is None:
        print("  no layout to aim with")
        return 1
    resolved = lay.resolve(win)
    backend = choose_backend(win, prefer)
    print(f"  backend: {backend.name}")
    if resolved.aspect_warning:
        print(f"  WARNING: {resolved.aspect_warning}")

    def settled(before: float):
        """Poll the save until the torch moves, or the settle window closes."""
        end = time.perf_counter() + TORCH_PROBE_SETTLE
        while time.perf_counter() < end:
            time.sleep(0.15)
            now = torch()
            if now is not None and now != before:
                return now
        return before

    # -- 0. the keystroke, which needs no coordinate at all ---------------
    holder = getattr(backend, "_mods", None)
    if holder is not None:
        print("\n  [0] SHIFT+T (no coordinate)")
        holder((VK_SHIFT,), down=True)
        try:
            time.sleep(MOD_DWELL)
            backend.key(VK_T)
        finally:
            holder((VK_SHIFT,), down=False)
        now = settled(level)
        if now > level:
            print(f"      torch went UP {level:.0f} -> {now:.0f}: SHIFT+T is a "
                  f"bare T and RELIT. Stopping before it burns another.")
            return 0
        if now < level:
            print(f"      *** SHIFT+T WORKS *** {level:.0f} -> {now:.0f}")
            print("      No pixel needed. Wire the keystroke, not a click.")
            return 0
        print("      nothing moved")

    # -- 1. every candidate, in `layout.TORCH_CANDIDATES` order -----------
    from bridge.layout import TORCH_CANDIDATES              # noqa: PLC0415

    attempts = [(name, resolved.at(frac), frac)
                for name, frac in TORCH_CANDIDATES
                if resolved.at(frac) is not None]

    for i, (label, pt, frac) in enumerate(attempts, start=1):
        print(f"\n  [{i}] {label}  ->  screen {pt}")
        if not backend.click(pt, mods=(VK_SHIFT,)):
            print("      the click itself failed to send")
            continue
        now = settled(level)
        if now == level:
            print(f"      still {now:.0f}")
            continue
        print(f"      *** THE TORCH MOVED *** {level:.0f} -> {now:.0f}")
        print(f"      the winner is ({frac[0]:.6f}, {frac[1]:.6f})  # {label}")
        if "measured" in label:
            print("      -- an already-refuted point answered, so the PIXEL "
                  "was never wrong: MOD_DWELL was the bug.")
        return 0

    print("\n  nothing on the grid moved the torch.")
    print("  Next: the widget may be inert while a modal or a tutorial popup is")
    print("  up, or the strip may want a hover before it accepts a click.")
    return 1


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Layer 3: the actuator.")
    ap.add_argument("--demo", action="store_true", help="plan synthetic turns, send nothing")
    ap.add_argument("--replay", metavar="DIR",
                    help="plan every captured turn in DIR offline, send nothing")
    ap.add_argument("--model", default=None, help="checkpoint for --replay")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--probe", action="store_true",
                    help="with the game RUNNING: try each backend on a harmless key "
                         "and report which one the game accepts")
    ap.add_argument("--geometry", action="store_true",
                    help="report the virtual desktop, per-monitor DPI, and "
                         "measure whether a click can hit an arbitrary pixel")
    ap.add_argument("--auto", action="store_true",
                    help="--probe: take focus and read the answer off the "
                         "SCREEN instead of asking, so it runs unattended")
    ap.add_argument("--process", default="Darkest.exe")
    ap.add_argument("--probe-torch", action="store_true",
                    help="with the game IN A RAID: find which pixel is the "
                         "torch by clicking and reading the save back")
    ap.add_argument("--profile", default=None,
                    help="--probe-torch: the save profile directory")
    ap.add_argument("--backend", default=DEFAULT_BACKEND,
                    help="--probe-torch: which backend to send through")
    ap.add_argument("--probe-click", action="store_true",
                    help="does synthetic MOUSE input reach the game? "
                         "movement only, unless --allow-click")
    ap.add_argument("--allow-click", action="store_true",
                    help="with --probe-click, also send one real click")
    ap.add_argument("--key", default="h", choices=sorted(PROBE_KEYS),
                    help="which key to probe with. h is confirmed to work "
                         "by hand on this save, so a negative means the "
                         "injection failed, not the key.")
    args = ap.parse_args(argv)

    if args.demo:
        return _demo()

    if args.replay:
        return replay_plans(Path(args.replay), args.model, args.limit)

    if args.probe:
        from bridge.window import describe, diagnose, find_window
        win = find_window(args.process)
        if win is None:
            print(diagnose(args.process))
            return 1
        print(describe(win))
        return _probe(win, args.key, auto=args.auto)

    if args.geometry:
        from bridge.window import find_window   # noqa: PLC0415
        return _geometry(find_window(args.process))

    if args.probe_torch:
        from bridge.window import describe, diagnose, find_window   # noqa: PLC0415
        win = find_window(args.process)
        if win is None:
            print(diagnose(args.process))
            return 1
        print(describe(win))
        return _probe_torch(win, args.profile, args.backend)

    if args.probe_click:
        from bridge.window import describe, diagnose, find_window
        win = find_window(args.process)
        if win is None:
            print(diagnose(args.process))
            return 1
        print(describe(win))
        return _probe_click(win, args.allow_click)

    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
