"""The actuator plans the right inputs, and refuses in every case it must.

Layer 3 sends real clicks into a real save file. The asymmetry that shapes this
file: a plan that is merely *absent* costs a turn of advice, while a plan that
is *wrong* plays a move the human did not choose, into a run that cannot be
undone. So most of what is pinned here is refusal.

The three refusals that are not defensive padding but load-bearing rules:

* **provisional** -- `run.py` looks ahead past pending enemy turns so the screen
  is not blank through ~17 s of no save write. On screen that is a greyed ring.
  Actuated, it would play a turn that is not due. Measured at 12 of 74 battle
  updates in `raidsnaps2`, so this is a sixth of all traffic, not a corner.
* **enemies_first / movers** -- the advised target rank may not survive the wait.
* **slot out of range** -- a provision is used from the inventory and retreat has
  no calibrated button, so `advised_button` returns -1. Clicking anyway would
  press whatever happens to sit at that coordinate.

Geometry is checked against a synthetic client rect, which is also how
`--replay` validates the whole capture corpus with the game closed.
"""

import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from bridge.act import (
    Actuator, DryRunBackend, InputPlan, KillSwitch, OfflineWindow,
    SKILL_VKS, Step, VK_A, VK_D, VK_T, plan_battle, plan_room, refuse,
)
from bridge.actionbar import ActionBar
from bridge.layout import load as load_layout
from bridge.overlay_slots import MOVE_SLOT, PASS_SLOT


def bar(pattern: str) -> ActionBar:
    """An `ActionBar` from a six-character mask, "#" lit and "." greyed.

    The measured numbers live in `tests/bridge/test_actionbar.py` against real frames;
    what this file pins is what the actuator DOES with the verdict, so the
    verdict is handed over directly.
    """
    lit = tuple(c == "#" for c in pattern)
    return ActionBar(lit, tuple(0.5 if x else 0.0 for x in lit),
                     tuple(150.0 if x else 55.0 for x in lit))


@dataclass(frozen=True)
class U:
    """The fields of `run.Update` that the actuator reads."""
    skill_slot: int = 1
    side: str = "enemy"
    rank: int = 0
    headline: str = "test"
    provisional: bool = False
    enemies_first: int = 0
    movers: tuple = ()
    alternatives: tuple = ()
    # The out-of-combat decision, carried as structure. Real `Update`s have
    # had this all along; the fixture gained it when the torch proxy needed
    # an Update to strike a field out of.
    room_advice: object = None
    # Put the torch out before playing this turn -- set only on the killing
    # blow. See `plan_snuff_torch`.
    snuff_torch: bool = False
    # What the rescue in `plan_reposition` reads: the acting hero's four
    # `.launch` lists and how many heroes are on the board.
    actor_launches: tuple = ()
    party_size: int = 0


@dataclass(frozen=True)
class A:
    """The fields of `run.RoomAdvice` that the actuator reads."""
    next_step: str = ""
    next_room: str = ""
    torch: str = ""
    curio_area: str = ""
    curio_id: str = ""
    curio_action: str = ""
    curio_item: str = ""
    camp: bool = False

    @property
    def empty(self):
        return not (self.next_step or self.torch or self.curio_action or self.camp)


@pytest.fixture(scope="module")
def resolved():
    lay = load_layout()
    assert lay is not None, "bridge/overlay_layout.json did not migrate"
    return lay.resolve(OfflineWindow(size=(1600, 900), origin=(100, 50)))


# --- combat: the happy path is one key plus one click ---

def test_a_skill_is_a_digit_then_a_click(resolved):
    plan = plan_battle(U(skill_slot=2, side="enemy", rank=0), resolved)
    assert plan.actionable
    assert [s.kind for s in plan.steps] == ["key", "click"]
    assert plan.steps[0].vk == SKILL_VKS[1]          # '2'


def test_every_slot_maps_to_its_own_digit(resolved):
    for slot in range(1, MOVE_SLOT + 1):
        plan = plan_battle(U(skill_slot=slot, side="enemy", rank=0), resolved)
        assert plan.actionable, slot
        assert plan.steps[0].vk == SKILL_VKS[slot - 1], slot


def test_move_is_slot_five_and_still_needs_a_destination(resolved):
    """MOVE is the 5th action-bar button and then a rank to move to -- the
    game's numbering, which `overlay_slots.MOVE_SLOT` owns."""
    plan = plan_battle(U(skill_slot=MOVE_SLOT, side="hero", rank=2), resolved)
    assert plan.actionable
    assert plan.steps[0].vk == SKILL_VKS[MOVE_SLOT - 1]
    assert plan.steps[1].kind == "click"


def test_pass_is_a_click_with_no_hotkey(resolved):
    """The X has no documented hotkey, so it is the one combat action that is
    a bare click. Pressing a digit for it would select a skill instead."""
    plan = plan_battle(U(skill_slot=PASS_SLOT, side="", rank=-1), resolved)
    assert plan.actionable
    assert [s.kind for s in plan.steps] == ["click"]


def test_clicks_land_inside_the_client_rect(resolved):
    """A coordinate outside the window is a click on the desktop, or on
    whatever else is there."""
    for side in ("hero", "enemy"):
        for rank in range(4):
            plan = plan_battle(U(skill_slot=1, side=side, rank=rank), resolved)
            x, y = plan.steps[1].screen
            assert 100 <= x <= 100 + 1600, (side, rank, x)
            assert 50 <= y <= 50 + 900, (side, rank, y)


def test_hero_and_enemy_ranks_are_on_opposite_sides(resolved):
    """Rank 1 is the FRONT rank on both sides. If this ever mirrors, every
    click goes to the wrong party and nothing downstream objects."""
    hero1 = plan_battle(U(1, "hero", 0), resolved).steps[1].screen
    enemy1 = plan_battle(U(1, "enemy", 0), resolved).steps[1].screen
    assert hero1[0] < enemy1[0], "hero ranks must sit left of enemy ranks"


def test_rank_order_runs_outward_from_the_middle(resolved):
    """Hero rank 1 is nearest the enemies and rank 4 furthest; enemies mirror."""
    hx = [plan_battle(U(1, "hero", r), resolved).steps[1].screen[0] for r in range(4)]
    ex = [plan_battle(U(1, "enemy", r), resolved).steps[1].screen[0] for r in range(4)]
    assert hx == sorted(hx, reverse=True), hx
    assert ex == sorted(ex), ex


# --- refusals ---

def test_a_provisional_update_is_never_actuated(resolved):
    plan = plan_battle(U(1, "enemy", 0, provisional=True), resolved)
    assert not plan.actionable and "provisional" in plan.refusal


def test_pending_enemy_turns_block_the_plan(resolved):
    plan = plan_battle(U(1, "enemy", 0, enemies_first=2), resolved)
    assert not plan.actionable and "enemy turn" in plan.refusal


def test_a_repositioning_enemy_blocks_the_plan(resolved):
    plan = plan_battle(U(1, "enemy", 0, movers=("Bone Soldier",)), resolved)
    assert not plan.actionable and "reposition" in plan.refusal


def test_no_button_means_no_click(resolved):
    """`advised_button` returns -1 for a provision (used from the inventory)
    and for retreat. Neither has a calibrated target."""
    plan = plan_battle(U(skill_slot=-1, side="enemy", rank=0), resolved)
    assert not plan.actionable


def test_advice_without_a_rank_is_refused(resolved):
    """A skill needs somewhere to point. Clicking nothing, or clicking (0,0),
    would both be worse than doing nothing."""
    plan = plan_battle(U(skill_slot=1, side="", rank=-1), resolved)
    assert not plan.actionable


# ── the shortlist fallback ────────────────────────────────────────────────
#
# The tutorial draws no retreat control, and the policy ranks retreat first
# often enough that this was a live stall: `advised_button` returned -1, the
# plan refused, `ActRunner` returned WITHOUT marking the turn played, and the
# identical refusal republished on every poll for the rest of the run. It
# never misclicked -- it stopped. These pin the repair and, just as
# importantly, the limit of it.


@dataclass(frozen=True)
class Alt:
    """The fields of `run.Alternative` that `plan_one_move` reads."""
    skill_slot: int = 1
    side: str = "enemy"
    rank: int = 0
    skill_id: str = ""
    headline: str = "alt"
    confidence: float = 0.0


def test_an_unplayable_top_pick_falls_through_to_the_shortlist(resolved):
    """Retreat has no button; the next-best move does."""
    plan = plan_battle(
        U(skill_slot=-1, side="enemy", rank=0,
          alternatives=(Alt(skill_slot=2, side="enemy", rank=1,
                            headline="Highwayman: Wicked Slice", confidence=0.31),)),
        resolved)
    assert plan.actionable
    assert plan.steps[0].vk == SKILL_VKS[1]
    assert "Wicked Slice" in plan.why
    # The log must never hide that this was not the recommendation.
    assert "FALLBACK" in plan.why and "31%" in plan.why


def test_the_fallback_reports_the_TOP_pick_reason(resolved):
    """A reader chasing a stall wants to know why the recommendation could not
    be played, not why the fifth candidate could not."""
    plan = plan_battle(
        U(skill_slot=-1, side="enemy", rank=0,
          alternatives=(Alt(skill_slot=1, side="", rank=-1),      # no rank
                        Alt(skill_slot=3, side="enemy", rank=2),)),
        resolved)
    assert plan.actionable
    assert "no action-bar slot" in plan.why       # the TOP pick's refusal
    assert plan.steps[0].vk == SKILL_VKS[2]       # the SECOND alternative won


def test_a_shortlist_with_no_playable_move_still_refuses(resolved):
    plan = plan_battle(
        U(skill_slot=-1, side="enemy", rank=0,
          alternatives=(Alt(skill_slot=-1), Alt(skill_slot=1, side="", rank=-1))),
        resolved)
    assert not plan.actionable
    assert "no alternative" in plan.refusal


@pytest.mark.parametrize("timing", [
    {"provisional": True},
    {"enemies_first": 2},
    {"movers": ("Bone Soldier",)},
])
def test_a_TIMING_refusal_never_falls_through(resolved, timing):
    """The separation the whole repair rests on.

    A timing refusal says the turn is not due yet, and EVERY candidate for
    that turn shares it -- so walking the shortlist would only find a
    different way to act too early. Only refusals about the SCREEN fall
    through. If this ever goes green-by-accident the fallback becomes a way to
    play provisional advice, which is the one thing layer 3 must not do.
    """
    plan = plan_battle(
        U(skill_slot=1, side="enemy", rank=0,
          alternatives=(Alt(skill_slot=2, side="enemy", rank=1),), **timing),
        resolved)
    assert not plan.actionable


def test_the_fallback_cannot_emit_a_degenerate_click(resolved):
    """The same exhaustion `test_no_plan_ever_emits_a_degenerate_click` does,
    now with a shortlist under it."""
    for slot in range(-1, 8):
        for side in ("hero", "enemy", ""):
            for rank in (-1, 0, 3, 9):
                alts = tuple(Alt(s, sd, r)
                             for s in (-1, 1, 9) for sd in ("enemy", "")
                             for r in (-1, 2, 9))
                p = plan_battle(U(slot, side, rank, alternatives=alts), resolved)
                for s in p.steps:
                    if s.kind == "click":
                        assert s.screen != (0, 0), (slot, side, rank)


def test_no_plan_ever_emits_a_degenerate_click(resolved):
    """(0,0) is the tell for an unresolved coordinate."""
    for slot in range(-1, 8):
        for side in ("hero", "enemy", ""):
            for rank in (-1, 0, 3, 9):
                p = plan_battle(U(slot, side, rank), resolved)
                for s in p.steps:
                    if s.kind == "click":
                        assert s.screen != (0, 0), (slot, side, rank)


# --- the room layers ---

def test_torch_relight_is_the_t_key_and_needs_no_pixels():
    plan = plan_room(A(torch="relight"), None)
    assert plan.actionable
    assert [s.vk for s in plan.steps] == [VK_T]


def test_walking_is_a_direction_key():
    assert plan_room(A(next_step="rooH"), None).steps[0].vk == VK_D


def test_torch_is_handled_before_movement():
    """Relighting then walking is what the torch layer means; walking first
    spends the corridor in the dark."""
    plan = plan_room(A(next_step="rooH", torch="relight"), None)
    assert plan.steps[0].vk == VK_T


def test_camp_is_honestly_refused():
    """It has no calibrated coordinates. Saying so names the next screen worth
    modelling; guessing a click does not.

    The curio half of this test retired on 2026-09-05, when the panel's item
    slot was measured -- see `test_a_curio_that_wants_an_item_opens_the_panel`.
    """
    assert "camp" in plan_room(A(camp=True), None).refusal


def test_torch_shutdown_is_not_a_room_action():
    """`torchlight.advise` gates SHUTDOWN on `about_to_win`, which is false
    everywhere the room block runs -- so it must never arrive here as an
    instruction."""
    assert plan_room(A(torch="shutdown"), None).refusal


def test_empty_advice_is_refused():
    assert not plan_room(A(), None).actionable
    assert not plan_room(None, None).actionable


# --- the actuator refuses to send unless told twice ---

def test_nothing_is_sent_while_disabled():
    """`enabled` defaults False. Building an Actuator must not arm it."""
    act = Actuator(backend=DryRunBackend())
    assert act.enabled is False
    plan = InputPlan((Step("key", vk=VK_T),), "torch", "x")
    assert act.execute(plan) is False
    assert act.backend.sent == []


def test_a_refusal_is_never_executed():
    act = Actuator(backend=DryRunBackend(), enabled=True)
    assert act.execute(refuse("nope", "battle")) is False
    assert act.backend.sent == []


def test_an_enabled_actuator_sends_every_step():
    act = Actuator(backend=DryRunBackend(), enabled=True, min_interval=0.0)
    plan = InputPlan((Step("key", vk=VK_T), Step("click", screen=(5, 6))), "x", "y")
    assert act.execute(plan) is True
    assert [s.kind for s in act.backend.sent] == ["key", "click"]


def test_the_kill_switch_stops_everything():
    class Tripped(KillSwitch):
        def check(self):
            return True

    act = Actuator(backend=DryRunBackend(), enabled=True, kill=Tripped(),
                   min_interval=0.0)
    assert act.execute(InputPlan((Step("key", vk=VK_T),), "x", "y")) is False
    assert act.backend.sent == []


def test_counters_separate_sent_from_refused():
    act = Actuator(backend=DryRunBackend(), enabled=True, min_interval=0.0)
    act.execute(InputPlan((Step("key", vk=VK_T),), "x", "y"))
    act.execute(refuse("no", "x"))
    assert (act.executed, act.refused) == (1, 1)


# --- the offline window used by --replay ---

def test_offline_window_round_trips_screen_and_client():
    win = OfflineWindow(size=(1600, 900), origin=(100, 50))
    assert win.to_screen(0.0, 0.0) == (100, 50)
    assert win.to_screen(1.0, 1.0) == (1700, 950)
    assert win.to_client(*win.to_screen(0.5, 0.5)) == (800, 450)


def test_the_layout_resolves_against_any_client_size():
    """The point of fractions: a differently sized window still resolves, and
    to a proportionally different pixel."""
    lay = load_layout()
    small = lay.resolve(OfflineWindow(size=(800, 450), origin=(0, 0)))
    big = lay.resolve(OfflineWindow(size=(1600, 900), origin=(0, 0)))
    sx, sy = small.point("enemy", 0)
    bx, by = big.point("enemy", 0)
    assert bx == pytest.approx(sx * 2, abs=2)
    assert by == pytest.approx(sy * 2, abs=2)



# --- the curio panel's item slot (2026-09-05) ---------------------------

class _Buttons:
    """A stand-in for `curiopanel.CurioButtons`, so these tests need no frame.

    The real one is pinned against real captures in `test_curiopanel.py`; what
    is under test here is which of its three points `act.py` clicks.
    """

    def __init__(self, *, hand=(0.64, 0.43), close=(0.76, 0.43),
                 items=((0.70, 0.43),), usable=(True,)):
        self.hand, self.close, self.items, self.usable = hand, close, items, usable

    @property
    def readable(self):
        return self.hand is not None

    def sole_item(self):
        lit = [p for p, ok in zip(self.items, self.usable) if ok]
        return lit[0] if len(lit) == 1 else None


def _panel_runner(monkeypatch, buttons):
    """A runner whose screen always shows `buttons`."""
    from bridge import curiopanel
    from bridge import screen as screen_mod
    r = _runner(monkeypatch)
    monkeypatch.setattr(screen_mod, "grab", lambda w: object())
    monkeypatch.setattr(curiopanel, "find", lambda img: buttons)
    return r


def _clicked(runner):
    """`DryRunBackend.sent` holds `Step`s, and the label is empty on them --
    it records the PRIMITIVE, not the plan. So assert on `screen`."""
    return [s for s in runner.actuator.backend.sent if s.kind == "click"]


def test_the_panel_item_slot_is_clicked_for_a_use_curio(monkeypatch):
    """THE LIVE BLOCKER. The party stood at the Cove's Shipwreck with four
    shovels and the bridge could only click the X, so the raid ended stuck in
    a corridor. The item slot is one pitch left of the X."""
    buttons = _Buttons()
    r = _panel_runner(monkeypatch, buttons)
    assert r._work_curio_panel("barnacle_crusted_chest",
                               A(curio_action="use", curio_id="x",
                                 curio_item="shovel"))
    sent = _clicked(r)
    assert len(sent) == 1
    assert sent[0].screen == r.resolved.win.to_screen(*buttons.items[0])


def test_the_hand_is_clicked_for_a_bare_handed_curio(monkeypatch):
    buttons = _Buttons()
    r = _panel_runner(monkeypatch, buttons)
    assert r._work_curio_panel("sack", A(curio_action="interact",
                                         curio_id="sack"))
    sent = _clicked(r)
    assert len(sent) == 1
    assert sent[0].screen == r.resolved.win.to_screen(*buttons.hand)


def test_no_advice_still_clicks_the_hand(monkeypatch):
    """The pre-2026-09-05 behaviour, which several callers still rely on."""
    r = _panel_runner(monkeypatch, _Buttons())
    assert r._work_curio_panel("sack")
    assert len(_clicked(r)) == 1


def test_a_use_curio_with_a_dark_slot_CLOSES_rather_than_using_the_hand(monkeypatch):
    """The rule the old refusal existed to protect, kept.

    The bare-handed outcome of a curio that wants holy water is often the bad
    one, so falling back to the hand would be worse than doing nothing. The X
    is clicked instead, because a panel left standing is modal over the world
    and stalls every later keystroke.
    """
    buttons = _Buttons(usable=(False,))
    r = _panel_runner(monkeypatch, buttons)
    assert not r._work_curio_panel("altar", A(curio_action="use",
                                              curio_id="altar",
                                              curio_item="holy_water"))
    sent = _clicked(r)
    assert len(sent) == 1
    assert sent[0].screen == r.resolved.win.to_screen(*buttons.close)


def test_two_lit_slots_are_refused_rather_than_guessed(monkeypatch):
    """Eight of sixty curios offer two provisions. `curios.py` names one, and
    nothing on the panel says which slot holds it."""
    buttons = _Buttons(items=((0.64, 0.43), (0.70, 0.43)), usable=(True, True))
    r = _panel_runner(monkeypatch, buttons)
    assert not r._work_curio_panel("locked_strongbox",
                                   A(curio_action="use", curio_id="s",
                                     curio_item="shovel"))
    sent = _clicked(r)
    assert len(sent) == 1
    assert sent[0].screen == r.resolved.win.to_screen(*buttons.close)


def test_an_unreadable_row_sends_nothing_at_all(monkeypatch):
    """A red button with no gold hand plate is 60 of the 62 frames in the
    corpus that pass the red-X test alone. Clicking any of them puts a click
    in the world."""
    r = _panel_runner(monkeypatch, _Buttons(hand=None))
    assert not r._work_curio_panel("sack", A(curio_action="interact"))
    assert r.actuator.backend.sent == []


def test_the_stray_panel_dismissal_needs_a_READABLE_panel(monkeypatch):
    """Same gate, on the path that runs every single room."""
    r = _panel_runner(monkeypatch, _Buttons(hand=None))
    assert not r._dismiss_stray_curio_panel()
    assert r.actuator.backend.sent == []


# --- obstacles (2026-09-05) ---------------------------------------------

def test_an_obstacle_panel_is_cleared_with_the_zone_tool(monkeypatch):
    """A `W` that opens a panel did not open a door. The save does not name
    the obstacle, so the tool comes from the zone."""
    from bridge import obstacles
    buttons = _Buttons()
    r = _panel_runner(monkeypatch, buttons)
    monkeypatch.setattr(
        r, "_obstacle_advice",
        lambda: obstacles.ObstacleAdvice("use", "shovel", "the pack has 4"))
    assert r._work_obstacle_panel("coNO")
    sent = _clicked(r)
    assert len(sent) == 1
    assert sent[0].screen == r.resolved.win.to_screen(*buttons.items[0])


def test_an_obstacle_with_no_tool_is_cleared_BY_HAND_and_never_skipped(monkeypatch):
    """THE DIFFERENCE FROM A CURIO, and it is the whole reason this is its own
    method. An obstacle stands in the corridor the party has to walk down, and
    the game ships `clear_on_failure: true` -- so there is no walking away,
    only paying -5% max HP for it."""
    from bridge import obstacles
    buttons = _Buttons(usable=(False,))
    r = _panel_runner(monkeypatch, buttons)
    monkeypatch.setattr(
        r, "_obstacle_advice",
        lambda: obstacles.ObstacleAdvice("interact", None, "no shovel"))
    assert r._work_obstacle_panel("coNO")
    sent = _clicked(r)
    assert len(sent) == 1
    assert sent[0].screen == r.resolved.win.to_screen(*buttons.hand)


def test_a_W_that_really_opened_a_door_costs_one_screenshot(monkeypatch):
    """The guard that makes the hook free. No panel on the frame means the
    `W` was a door, and nothing must be clicked."""
    r = _panel_runner(monkeypatch, None)
    assert not r._work_obstacle_panel("coNO")
    assert r.actuator.backend.sent == []

def test_plan_room_walks_PAST_a_deferred_curio_without_pressing_W():
    """DEFER is `bridge/lootplan.py`'s verdict and it is not a skip: the curio
    is worth about three times as much at torch 0 and the walk home passes
    here again, so the party leaves it standing ON PURPOSE.

    Two things have to hold together. `W` must not be sent -- pressing it now
    takes the curio for a third of its value -- and the route step must still
    happen, which is the `_LayerDone` rule: a layer that will not act must not
    block one that will.
    """
    from bridge import act as act_mod
    from bridge.run import RoomAdvice

    plan = act_mod.plan_room(
        RoomAdvice(next_step="coAB", curio_id="travellers_tent_tutorial",
                   curio_action="interact", curio_defer=True),
        None, area="coAB")
    assert plan.layer == "route"
    assert [s.vk for s in plan.steps] == [act_mod.VK_D]
    assert act_mod.VK_W not in [s.vk for s in plan.steps]
    assert "COMING BACK FOR travellers_tent_tutorial" in plan.why, plan.why
    assert "torch 0" in plan.why

    # ...and the same curio NOT deferred is still opened here and now.
    normal = act_mod.plan_room(
        RoomAdvice(next_step="coAB", curio_id="crate", curio_action="interact"),
        None, area="coAB")
    assert normal.layer == "curio"
    assert [s.vk for s in normal.steps] == [act_mod.VK_W]


# -- the walk home through a corridor already cleared (2026-09-06) ---------
#
# Live: the party re-entered `coAB` bound for `rooA` to collect a DEFERRED
# curio, could not tell it had moved, spent its step budget, pressed `W` into
# the gate it had arrived through and landed back in `rooB` -- then oscillated
# between the two, ten torch a lap. The door logic's own comment says this
# "cannot arise here ... the direction was derived from which end the party
# entered by". On a second pass it is not derived: `_walk_progress` is silent
# and the default is `D`.


def _walk_keys(monkeypatch, tmp_path, came_from, target):
    """One walk with the position signal dead, returning the keys it sent."""
    import bridge.act as act
    import bridge.route as route
    r, _raid, _s = _full_pack_runner(monkeypatch, tmp_path, 15,
                                     full_pack="abandon")
    monkeypatch.setattr(r, "_walk_progress", lambda *_a, **_k: None)
    monkeypatch.setattr(route, "read_last_area", lambda *_a, **_k: came_from)
    monkeypatch.setattr(r, "_peek",
                        lambda *_a, **_k: {"in_battle": False,
                                           "area": target, "torch": 100.0})
    monkeypatch.setattr(act.time, "sleep", lambda *_a, **_k: None)
    r._walk_toward(target, "coAB")
    return [s.vk for s in r.actuator.backend.sent if s.kind == "key"]


def test_a_corridor_walked_dark_still_knows_which_way_is_BACK(monkeypatch,
                                                              tmp_path):
    """Target == where we came from, so the door is behind the party and `A`
    is the only key that reaches it. `D` here walks the length of the corridor
    away from the thing it was sent to fetch."""
    import bridge.act as act
    keys = _walk_keys(monkeypatch, tmp_path, "rooA", "rooA")
    assert act.VK_A in keys, keys
    assert act.VK_D not in keys, keys


def test_and_which_way_is_ON(monkeypatch, tmp_path):
    """The other half, and the reason this cannot just always press `A`: the
    party came from `rooB`, so `rooA` is the far end and `D` crosses to it."""
    import bridge.act as act
    keys = _walk_keys(monkeypatch, tmp_path, "rooB", "rooA")
    assert act.VK_D in keys, keys
    assert act.VK_A not in keys, keys


def test_the_derivation_is_only_a_FALLBACK(monkeypatch, tmp_path):
    """A readable position stays authoritative -- it is two cross-checked
    signals (`areatile` and the dark run) against one, and the save's
    `last_room_id` says which area was left, not where in this one the party
    is standing."""
    import inspect

    import bridge.act as act

    src = inspect.getsource(act.ActRunner._walk_toward)
    head = src[:src.index("read_last_area")]
    assert "if walk is None" in head, "the fallback must be gated on silence"


# -- the curio is SWEPT, not aimed at (2026-09-06) -------------------------
#
# Fran: *"you can use W+D at the same time and should stop on curio on
# interaction rather than mapping out the coordinates"*. `_walk_up_to` is gone
# with the nine tests that pinned it: it needed a position, a direction and a
# stopping distance, and all three had been falsified -- the last of them live,
# when a corridor walked dark a second time left no position and the approach
# walked the party away from the tent for twelve presses.


def _curio_steps(monkeypatch, tmp_path, travelled=3, landed=False):
    """Drive one corridor curio and return the steps it sent."""
    r, _raid, _s = _full_pack_runner(monkeypatch, tmp_path, 15,
                                     full_pack="abandon")
    if callable(travelled):
        monkeypatch.setattr(r, "_tiles_travelled", travelled)
    else:
        monkeypatch.setattr(r, "_tiles_travelled", lambda: travelled)
    if landed:
        monkeypatch.setattr(r, "_curio_landed",
                            lambda a, c: (True, "it is used up"))
    r.handle(_room_update(A(curio_action="interact", curio_id="sack"),
                          area="coBC"))
    return r, r.actuator.backend.sent


def test_the_corridor_curio_is_approached_with_D_and_W_TOGETHER(monkeypatch,
                                                                tmp_path):
    """The whole point: holding the interact key while walking means nothing
    has to compute where to stop. A bare `W` has to be aimed; a sweep does
    not."""
    import bridge.act as act
    _r, sent = _curio_steps(monkeypatch, tmp_path)
    combos = [s for s in sent if act.VK_W in _down(s) and act.VK_D in _down(s)]
    assert combos, "no D+W step -- the curio is still being aimed at"
    assert all(s.kind == "keys" for s in combos)


def test_it_will_not_sweep_while_still_beside_the_arrival_door(monkeypatch,
                                                               tmp_path):
    """`W` at the gate the party came in by walks them straight back out of
    the area -- the failure `plan_hallway_sweep`'s clearance exists for, and
    one this repo has already paid for once. Plain `D` until clear."""
    import bridge.act as act
    _r, sent = _curio_steps(monkeypatch, tmp_path, travelled=0)
    first = next(s for s in sent if s.kind in ("key", "keys"))
    assert act.VK_W not in _down(first), "held W while still at the door"
    assert first.vk == act.VK_D


def test_the_INTERACTION_is_what_stops_the_sweep(monkeypatch, tmp_path):
    """"stop on curio on interaction rather than mapping out the
    coordinates". Once the curio answers there is nothing left to walk
    toward, so the loop ends -- it does not keep sweeping to some tile."""
    import bridge.act as act
    _r, sent = _curio_steps(monkeypatch, tmp_path, landed=True)
    combos = [s for s in sent if act.VK_W in _down(s)]
    assert len(combos) == 1, f"swept {len(combos)}x after it had worked"


def test_a_sweep_that_walks_BACK_toward_the_door_stops(monkeypatch, tmp_path):
    """The clearance is checked once, before the first step, so a sweep
    heading the wrong way meets the same danger from the other side. Distance
    from the door FALLING is that, measured between two attempts rather than
    predicted from a rule about which key goes which way -- which is the rule
    currently in doubt on a re-walked corridor."""
    seq = iter([5, 4, 3, 2, 1, 0])

    def falling():
        return next(seq, 0)

    r, sent = _curio_steps(monkeypatch, tmp_path, travelled=falling)
    assert ("coBC", "sack") in r.gave_up_on, "it kept sweeping into the door"


def test_the_sweep_clearance_needs_only_DISTANCE_not_direction():
    """What survives a second pass is `areatile`, and the clearance test asks
    only "is the party still beside the door" -- which `areatile` answers. The
    direction it lacks is not part of the question, and that is what makes the
    sweep usable exactly where the aiming approach died."""
    from bridge.act import plan_hallway_sweep

    assert plan_hallway_sweep("coAB", None, travelled=3).refusal == ""
    assert plan_hallway_sweep("coAB", None, travelled=0).refusal
    # with neither signal it still refuses -- not knowing where the party
    # stands is when holding an interact key is most dangerous
    assert plan_hallway_sweep("coAB", None).refusal
    # and a room is not a hallway
    assert plan_hallway_sweep("rooA", None, travelled=3).refusal


# -- the hunger check blocks every layer (2026-09-06) ----------------------
#
# Fran, watching a run stall: *"Right now its stuck on a random hunger
# check"*, *"its rare on the tutorial but can happen"*. The modal takes all
# input, so the party will not walk and `W` does nothing -- and the curio
# approach spent all ten of its presses against it and gave up on a tent that
# was never the problem. Nothing in the bridge could see it.


HUNGER_REF = (Path(__file__).resolve().parents[2]
              / "captures" / "screens" / "hunger.png")


def _hunger_frame():
    import numpy as np
    from PIL import Image
    if not HUNGER_REF.exists():
        import pytest
        pytest.skip("the hunger reference has not been banked")
    return np.asarray(Image.open(HUNGER_REF).convert("RGB")).astype(float)


def test_the_hunger_panel_is_a_RECOGNISED_screen():
    """It has to be nameable before it can be answered, and it is the
    recogniser that says a modal is up at all -- every layer below reads a
    blocked game as "my input did not land"."""
    import numpy as np

    import bridge.screen as screen_mod
    said = screen_mod.recognise(_hunger_frame().astype(np.uint8))
    assert said == "hunger", said


def test_the_EAT_box_is_the_gold_one_and_that_is_how_it_is_chosen():
    """Fran: *"It should click always the yellow box if possible"*. The two
    boxes are compared against EACH OTHER rather than against a threshold,
    which is what lets one banked frame settle it honestly: that frame has
    food, so it shows what "offered" looks like and what the other button
    looks like beside it, and says nothing about a greyed-out eat box because
    it contains none."""
    import bridge.act as act
    from bridge import layout as layout_mod

    img = _hunger_frame()
    h, w, _ = img.shape

    def gold(frac):
        cx, cy = int(frac[0] * w), int(frac[1] * h)
        half = max(6, int(0.016 * w))
        box = img[cy - half:cy + half, cx - half:cx + half]
        lum = box.mean(axis=2)
        return float(((box[:, :, 0] - box[:, :, 2] > 30) & (lum > 60)).mean())

    eat, refuse = gold(layout_mod.HUNGER_EAT), gold(layout_mod.HUNGER_REFUSE)
    assert eat > refuse, (eat, refuse)
    assert eat >= act.ActRunner.HUNGER_LIT_FLOOR, eat


def test_the_hunger_buttons_are_nowhere_near_the_pack(monkeypatch, tmp_path):
    """Same guard the torch meter carries, for the same reason: a click that
    lands in the raid pack SHIFT-discards something. These are the only two
    points the bridge sends at a modal, so both are checked."""
    from bridge import layout as layout_mod
    r, _raid, _s = _full_pack_runner(monkeypatch, tmp_path, 15,
                                     full_pack="abandon")
    resolved = r.resolved
    for frac in (layout_mod.HUNGER_EAT, layout_mod.HUNGER_REFUSE):
        _x, y = resolved.at(frac)
        for i in range(16):
            _sx, sy = resolved.inventory_point(i)
            assert sy - y > resolved.win.client_size[1] * 0.2


def test_the_modal_is_actually_CLICKED_and_where(monkeypatch, tmp_path):
    """**The test the first version of this did not have, and it cost a live
    run.** The other three here check detection, geometry and the two ways of
    doing nothing -- all of which passed while the clicking half raised
    `NameError: name 'layout' is not defined` on every real frame. `handle`
    catches and logs, so the loop survived and simply never acted: nine room
    blocks, no clicks, no clue.

    A branch that only runs against a live screen needs a test that fakes the
    live screen."""
    import numpy as np

    import bridge.screen as screen_mod
    from bridge import layout as layout_mod

    r, _raid, _s = _full_pack_runner(monkeypatch, tmp_path, 15,
                                     full_pack="abandon")
    seen = iter(["hunger", "raid"])
    monkeypatch.setattr(screen_mod, "recognise_live",
                        lambda *a, **k: next(seen, "raid"))
    monkeypatch.setattr(screen_mod, "grab",
                        lambda *a, **k: np.zeros((768, 1366, 3), np.uint8))
    import bridge.act as act
    monkeypatch.setattr(act.time, "sleep", lambda *_a, **_k: None)

    assert r._answer_hunger() is True, "it did not take the poll"
    clicks = [s for s in r.actuator.backend.sent if s.kind == "click"]
    assert len(clicks) == 1, f"sent {len(clicks)} clicks at a modal"
    # a black frame lights neither box, so the fallback order applies and eat
    # is first -- eating is the answer whenever it is available
    assert tuple(clicks[0].screen) == tuple(r.resolved.at(layout_mod.HUNGER_EAT))


def test_the_answer_waits_for_FOCUS_before_it_clicks(monkeypatch, tmp_path):
    """`Actuator.execute` does not check focus -- `_send_and_confirm` does,
    and this path does not go through it.

    Live 2026-09-06, at torch 11 in a crypts corridor: the panel was
    recognised, both boxes measured correctly, both answers "sent", and the
    panel did not move. The clicks were going to whatever window was in
    front, which during a supervised session is the operator's own terminal.
    A click into somebody else's window is worse than no click, so this
    REFUSES rather than sending blind -- and takes the poll, because the
    modal is still up and no layer below can act through it.
    """
    import numpy as np

    import bridge.screen as screen_mod
    r, _raid, _s = _full_pack_runner(monkeypatch, tmp_path, 15,
                                     full_pack="abandon")
    monkeypatch.setattr(screen_mod, "recognise_live", lambda *a, **k: "hunger")
    monkeypatch.setattr(screen_mod, "grab",
                        lambda *a, **k: np.zeros((768, 1366, 3), np.uint8))
    monkeypatch.setattr(type(r), "_focused_or_wait", lambda _self: False)
    before = len(r.actuator.backend.sent)

    assert r._answer_hunger() is True, "the modal still blocks the poll"
    assert len(r.actuator.backend.sent) == before,         "it clicked into whatever window was in front"


# The SAME panel at torch 11 in the crypts is a different frame: DD tints the
# whole scene by the light level, and the recogniser hashes the whole frame.
# The tutorial-lit reference scored 0.459 against it -- nowhere near a match
# -- so `_answer_hunger` returned "not hunger" and every layer below spent its
# retries against a modal. `name@variant` is the shipped answer to a screen
# with more than one look; this is the second look.

HUNGER_DARK_REF = (Path(__file__).resolve().parents[2]
                   / "captures" / "screens" / "hunger@dark.png")


def test_the_DARK_hunger_panel_is_recognised_too():
    import numpy as np
    from PIL import Image

    import bridge.screen as screen_mod
    if not HUNGER_DARK_REF.exists():
        pytest.skip("the dark hunger reference has not been banked")
    img = np.asarray(Image.open(HUNGER_DARK_REF).convert("RGB"), dtype=np.uint8)
    assert screen_mod.recognise(img) == "hunger"


def test_the_SAME_two_points_answer_the_dark_panel():
    """The geometry did not move -- only the tint did.

    That is the whole reason this is a banked variant and not a second pair of
    constants: measured on the dark frame the eat box still reads gold at the
    same fraction and still beats refuse, so Fran's rule (*"always the yellow
    box if possible"*) picks the same answer at torch 11 as at torch 100.
    """
    import numpy as np
    from PIL import Image

    import bridge.act as act
    from bridge import layout as layout_mod
    if not HUNGER_DARK_REF.exists():
        pytest.skip("the dark hunger reference has not been banked")
    img = np.asarray(Image.open(HUNGER_DARK_REF).convert("RGB")).astype(float)
    h, w, _ = img.shape

    def gold(frac):
        cx, cy = int(frac[0] * w), int(frac[1] * h)
        half = max(6, int(0.016 * w))
        box = img[cy - half:cy + half, cx - half:cx + half]
        lum = box.mean(axis=2)
        return float(((box[:, :, 0] - box[:, :, 2] > 30) & (lum > 60)).mean())

    eat, refuse = gold(layout_mod.HUNGER_EAT), gold(layout_mod.HUNGER_REFUSE)
    assert eat > refuse, (eat, refuse)
    assert eat >= act.ActRunner.HUNGER_LIT_FLOOR, eat


def test_no_modal_means_no_click_and_no_interference(monkeypatch, tmp_path):
    """It runs ahead of EVERY layer on EVERY update, so a frame that is not
    the hunger panel -- or a capture that throws -- has to cost nothing. A
    modal detector that can break the loop is worse than none."""
    import bridge.screen as screen_mod
    r, _raid, _s = _full_pack_runner(monkeypatch, tmp_path, 15,
                                     full_pack="abandon")
    before = len(r.actuator.backend.sent)

    monkeypatch.setattr(screen_mod, "recognise_live", lambda *a, **k: "hamlet")
    assert r._answer_hunger() is False
    def _boom(*_a, **_k):
        raise RuntimeError("no display")
    monkeypatch.setattr(screen_mod, "recognise_live", _boom)
    assert r._answer_hunger() is False
    assert len(r.actuator.backend.sent) == before, "it sent something anyway"


# -- the survival layer gets a vote (2026-09-06) ---------------------------
#
# Live, on a real turn with the bar lit:
#
#   -> OPEN VEIN on rank 2 (Brigand Cutthroat, 5 hp)   76%
#      alt: RETREAT on self  7%
#   leaving costs ~5,889 gold, staying ~4,972 - RETREAT
#   (the run loses somebody 67% of the time - over the 50% floor)
#   [act] [battle] Highwayman: OPEN VEIN on rank 2
#
# It attacked. Four turns later both heroes were dead. The retreat pricing
# had been fixed on 2026-09-03; nobody was listening to the number.


def _retreat_update(retreat_advised, slot=7):
    """A real `Update` and a real `Alternative` -- not stubs.

    `plan_battle` reads more of the update than a hand-rolled fake carries,
    and every field it grows is a field the fake goes stale on. The types are
    cheap to build, so build them.
    """
    from bridge.run import Alternative, Update
    return Update(phase="battle", skill_slot=1, skill_id="open_vein",
                  side="enemy", rank=2, confidence=0.76,
                  retreat_advised=retreat_advised,
                  alternatives=(Alternative(skill_slot=slot, side="self",
                                            rank=0, skill_id="__retreat__",
                                            headline="RETREAT on self",
                                            confidence=0.07),))


def test_a_RETREAT_verdict_outranks_the_policys_top_pick(monkeypatch):
    """The two layers answer different questions -- "what is the best move"
    and "should we still be here" -- and when they disagreed the policy won by
    default, because it was the only one wired to the actuator."""
    import bridge.act as act
    from bridge.overlay_slots import RETREAT_SLOT

    calls = []
    monkeypatch.setattr(act, "plan_one_move",
                        lambda c, r, b=None: calls.append(c.skill_slot) or
                        act.InputPlan((act.Step("key", vk=0x31, label="x"),),
                                      "battle", f"slot {c.skill_slot}"))
    bar = type("B", (), {"waiting": True})()
    plan = act.plan_battle(_retreat_update(True), None, bar)
    assert "LEAVE" in plan.why, plan.why
    assert calls[0] == RETREAT_SLOT, calls


def test_without_the_verdict_the_policy_is_untouched(monkeypatch):
    """Off by default in every sense: no verdict, no reordering, and the
    ordinary top-pick path runs exactly as before."""
    import bridge.act as act
    from bridge.overlay_slots import RETREAT_SLOT

    monkeypatch.setattr(act, "plan_one_move",
                        lambda c, r, b=None: act.InputPlan(
                            (act.Step("key", vk=0x31, label="x"),),
                            "battle", f"slot {c.skill_slot}"))
    monkeypatch.setattr(act, "_with_snuff", lambda _u, p, _r: p)
    bar = type("B", (), {"waiting": True})()
    plan = act.plan_battle(_retreat_update(False), None, bar)
    assert "LEAVE" not in plan.why
    assert "slot 1" in plan.why, plan.why


def test_a_retreat_with_no_CONTROL_does_not_block_the_turn(monkeypatch):
    """The game still decides whether it is playable. A raid that draws no
    retreat control -- the tutorial -- must fall through to the ordinary pick
    rather than stand still, which is the `_LayerDone` rule again: a step that
    cannot act must not block the one that can."""
    import bridge.act as act
    from bridge.overlay_slots import RETREAT_SLOT

    def _plan(c, _r, _b=None):
        if c.skill_slot == RETREAT_SLOT:
            return act.refuse("no calibrated retreat control", "battle")
        return act.InputPlan((act.Step("key", vk=0x31, label="x"),),
                             "battle", f"slot {c.skill_slot}")

    monkeypatch.setattr(act, "plan_one_move", _plan)
    monkeypatch.setattr(act, "_with_snuff", lambda _u, p, _r: p)
    bar = type("B", (), {"waiting": True})()
    plan = act.plan_battle(_retreat_update(True), None, bar)
    assert plan.actionable, plan.refusal
    assert "slot 1" in plan.why, plan.why


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v", "-p", "no:xdist"]))


# --- every backend must implement the WHOLE interface ---
#
# This exists because of a real break: `ScanCodeBackend` was inserted as a
# subclass in the middle of `SendInputBackend`, which silently moved
# `move`/`_normalise`/`click` INTO the subclass and left the parent with only
# `key()`. Nothing failed until a live probe crashed with
# `'SendInputBackend' object has no attribute 'move'` -- against a running
# game, mid-session. A class whose methods can be relocated by an edit needs a
# test that says which methods it is supposed to have.

BACKEND_METHODS = ("key", "move", "click")


@pytest.mark.parametrize("name", ["post-message", "send-input", "scancode"])
def test_every_backend_implements_the_full_interface(name):
    from bridge.act import PostMessageBackend, ScanCodeBackend, SendInputBackend
    cls = {"post-message": PostMessageBackend,
           "send-input": SendInputBackend,
           "scancode": ScanCodeBackend}[name]
    assert cls.name == name
    for meth in BACKEND_METHODS:
        assert callable(getattr(cls, meth, None)), f"{name} is missing {meth}()"


def test_scancode_only_overrides_the_keyboard():
    """It differs from its parent in `key` and nothing else; if it ever grows
    its own `click`, that is the class having been split again."""
    from bridge.act import ScanCodeBackend
    own = {k for k, v in vars(ScanCodeBackend).items()
           if callable(v) and not k.startswith("__")}
    assert own == {"key"}, f"ScanCodeBackend unexpectedly defines {own}"


def test_the_dry_run_backend_also_satisfies_the_interface():
    """`Actuator` defaults to it, so a missing method would only surface the
    first time someone enabled a real run."""
    from bridge.act import DryRunBackend
    b = DryRunBackend()
    b.key(0x41)
    b.click((3, 4))
    assert len(b.sent) == 2


def test_virtual_screen_normalisation_spans_the_full_range():
    """0..65535 across the VIRTUAL screen, not the primary monitor -- getting
    that wrong puts every click on the wrong display in a multi-head setup."""
    from bridge.act import SendInputBackend
    b = SendInputBackend(None)
    lo = b._normalise((-10_000, -10_000))
    hi = b._normalise((10_000, 10_000))
    assert lo[0] < hi[0] and lo[1] < hi[1]


# --- ActRunner: the three gates ---
#
# This is the class that can actually play a move into a real save. The tests
# below are all about it NOT doing that: the failure that matters is an action
# nobody sanctioned, not a missed one.

class _Win:
    """A stand-in GameWindow. `foreground` is settable, which is the point."""
    def __init__(self, foreground=True, gone=False):
        self.client_size = (1600, 900)
        self.client_origin = (0, 0)
        self.is_foreground = foreground
        self.gone = gone
        self.hwnd = 1

    def to_screen(self, fx, fy):
        # Faithful to `window.GameWindow.to_screen`, which ROUNDS, reads
        # `client_size`, and adds `client_origin`. This double did none of the
        # three -- it truncated against a hardcoded 1600x900 -- and nothing
        # noticed while every call went through it. Routing layout points
        # through `ResolvedLayout._pt` (which implements the real arithmetic)
        # surfaced the drift as a one-pixel disagreement in five tests.
        left, top = self.client_origin
        w, h = self.client_size
        return left + int(round(fx * w)), top + int(round(fy * h))

    def to_client(self, sx, sy):
        return sx, sy


def _runner(monkeypatch, *, armed=True, confirm=False, foreground=True):
    import bridge.act as act
    # The real FOCUS_WAIT is 30s, because a turn lost to a missed focus cannot
    # be recovered and the operator deserves time to click. In tests that is
    # 30s of nothing, so it is collapsed here -- the behaviour under test is
    # "does it wait rather than skip", not how long.
    monkeypatch.setattr(act, "FOCUS_WAIT", 0)
    # Real values are 1.2s settle / 6s ack, sized for DD's animations.
    # Tests assert the LOGIC, not the wall clock.
    monkeypatch.setattr(act, "SETTLE_BEFORE_SEND", 0)
    monkeypatch.setattr(act, "ACK_TIMEOUT", 0.05)
    monkeypatch.setattr(act, "TURN_ACK_TIMEOUT", 0.05)
    # The named constants above are not the whole of it: the curio, loot and
    # swap paths carry BARE `time.sleep(0.35 .. 0.8)` calls sized for DD's
    # animations, and three tests were already patching this one at a time.
    # Three curio tests alone spent 52 s of the suite asleep. Nothing in this
    # file asserts on the wall clock -- the Actuator pacing tests construct
    # their own with `min_interval=0.0` -- so it is collapsed for every test
    # here, which is the same call the constants above already make.
    monkeypatch.setattr("time.sleep", lambda *_a: None)
    win = _Win(foreground=foreground)
    resolved = load_layout().resolve(win)
    monkeypatch.setattr(act, "refresh_window", lambda w: win)
    r = act.ActRunner(win, resolved, act.DryRunBackend(),
                      armed=armed, confirm=confirm)
    return r


def _battle_update(**kw):
    from bridge.run import Update
    base = dict(phase="battle", skill_slot=2, side="enemy", rank=0,
                headline="SMITE", turn_key=(1, 99, 0, 0))
    base.update(kw)
    return Update(**base)


def test_an_unarmed_runner_sends_nothing(monkeypatch):
    """`--act` not passed: it prints the plan and stops."""
    r = _runner(monkeypatch, armed=False)
    r.handle(_battle_update())
    assert r.actuator.backend.sent == []
    assert r.stats["acted"] == 0


def test_an_armed_runner_sends(monkeypatch):
    r = _runner(monkeypatch, armed=True, confirm=False)
    r.handle(_battle_update())
    assert r.stats["acted"] == 1
    assert [s.kind for s in r.actuator.backend.sent] == ["key", "click"]


def test_the_same_turn_is_never_played_twice(monkeypatch):
    """A double-press is not cosmetic here - it plays a second move."""
    r = _runner(monkeypatch, armed=True, confirm=False)
    u = _battle_update()
    r.handle(u)
    r.handle(u)
    r.handle(u)
    assert r.stats["acted"] == 1


def test_a_new_turn_is_played(monkeypatch):
    r = _runner(monkeypatch, armed=True, confirm=False)
    r.handle(_battle_update(turn_key=(1, 99, 0, 0)))
    r.handle(_battle_update(turn_key=(1, 99, 1, 0)))
    assert r.stats["acted"] == 2


def test_nothing_is_sent_while_the_game_is_not_focused(monkeypatch):
    """Every working input path needs focus; sending anyway types into
    whatever IS focused, which during a supervised run is the operator's own
    terminal."""
    r = _runner(monkeypatch, armed=True, confirm=False, foreground=False)
    r.handle(_battle_update())
    assert r.stats["acted"] == 0
    assert r.actuator.backend.sent == []


def test_a_confirmed_send_reports_what_it_played(monkeypatch, tmp_path):
    """Per-battle limits are not in the save, so the only record that Take Aim
    is spent is the bridge remembering it played it. `turn_key[1]` is the hero
    guid -- the one identifier that survives duplicate classes."""
    raid = tmp_path / "persist.raid.json"
    raid.write_bytes(b"a")
    r = _runner(monkeypatch, armed=True, confirm=False)
    r.raid_path = raid
    played = []
    r.on_played = lambda guid, sid: played.append((guid, sid))
    real = r.actuator.execute
    monkeypatch.setattr(r.actuator, "execute",
                        lambda plan: (raid.write_bytes(b"ab"), real(plan))[1])
    r.handle(_battle_update(skill_id="take_aim"))
    assert played == [(99, "take_aim")]


def test_a_send_that_never_landed_reports_nothing(monkeypatch, tmp_path):
    """Recording a use that did not happen would suppress a legal skill for
    the rest of the fight - worse than the bug this feeds."""
    raid = tmp_path / "persist.raid.json"
    raid.write_bytes(b"a")                     # never changes
    r = _runner(monkeypatch, armed=True, confirm=False)
    r.raid_path = raid
    monkeypatch.setattr(r, "_bank_frame", lambda shot, note: True)
    played = []
    r.on_played = lambda guid, sid: played.append((guid, sid))
    r.handle(_battle_update(skill_id="take_aim"))
    assert played == []


def test_an_unfocused_turn_is_retried_rather_than_burned(monkeypatch):
    """It must NOT be marked as acted: the operator clicks the game and the
    same turn should still be playable."""
    import bridge.act as act
    monkeypatch.setattr(act, "FOCUS_WAIT", 0)
    win = _Win(foreground=False)
    resolved = load_layout().resolve(win)
    monkeypatch.setattr(act, "refresh_window", lambda w: win)
    r = act.ActRunner(win, resolved, act.DryRunBackend(), armed=True, confirm=False)
    u = _battle_update()
    r.handle(u)
    assert r.stats["acted"] == 0
    win.is_foreground = True
    r.handle(u)
    assert r.stats["acted"] == 1


def test_a_provisional_update_is_refused_by_the_runner_too(monkeypatch):
    r = _runner(monkeypatch, armed=True, confirm=False)
    r.handle(_battle_update(provisional=True, enemies_first=2))
    assert r.stats["acted"] == 0 and r.stats["refused"] == 1


def test_non_battle_phases_are_ignored(monkeypatch):
    r = _runner(monkeypatch, armed=True, confirm=False)
    for phase in ("room", "verdict", "over"):
        r.handle(_battle_update(phase=phase))
    assert r.stats["acted"] == 0


def test_declining_marks_the_turn_so_it_is_not_re_asked(monkeypatch):
    import bridge.act as act
    r = _runner(monkeypatch, armed=True, confirm=True)
    monkeypatch.setattr(act.ActRunner, "_approve", lambda self: False)
    u = _battle_update()
    r.handle(u)
    r.handle(u)
    assert r.stats["declined"] == 1 and r.stats["acted"] == 0


def test_a_tripped_kill_switch_stops_the_runner(monkeypatch):
    import bridge.act as act
    r = _runner(monkeypatch, armed=True, confirm=False)
    monkeypatch.setattr(r.actuator.kill, "check", lambda: True)
    r.handle(_battle_update())
    assert r.stats["acted"] == 0


def test_handle_never_raises(monkeypatch):
    """It runs on run.py's daemon poll thread. BRIDGE.md's rule: an exception
    there takes the bridge silent while the human keeps playing and keeps
    believing it is watching."""
    import bridge.act as act
    r = _runner(monkeypatch, armed=True, confirm=False)
    monkeypatch.setattr(act, "plan_battle",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    r.handle(_battle_update())          # must not propagate
    assert r.stats["errors"] == 1


def test_a_vanished_window_does_not_crash_or_send(monkeypatch):
    import bridge.act as act
    r = _runner(monkeypatch, armed=True, confirm=False)
    monkeypatch.setattr(act, "refresh_window", lambda w: None)
    r.handle(_battle_update())
    assert r.stats["acted"] == 0


# --- the save is the acknowledgement ---
#
# The first live run died here. DD writes the save when an action RESOLVES, so
# the poll that wakes the bridge fires while that action is still animating;
# input sent into an animation is dropped. Nothing noticed, `Session.battle`
# had already consumed the turn_key, and the run stopped with the party waiting
# for a keystroke that had evaporated.

def test_a_send_is_confirmed_by_the_save_changing(monkeypatch, tmp_path):
    import bridge.act as act
    raid = tmp_path / "persist.raid.json"
    raid.write_bytes(b"a")
    r = _runner(monkeypatch, armed=True, confirm=False)
    r.raid_path = raid
    # the game "resolves the action": the save grows
    real = r.actuator.execute

    def execute_and_write(plan):
        ok = real(plan)
        raid.write_bytes(b"ab")
        return ok

    monkeypatch.setattr(r.actuator, "execute", execute_and_write)
    r.handle(_battle_update())
    assert r.stats["acted"] == 1


def test_an_unconfirmed_send_is_retried_then_given_up(monkeypatch, tmp_path):
    """The save never changes, so the input never landed. It must retry a
    BOUNDED number of times and then stop -- hammering an input that cannot
    land is worse than saying so."""
    import bridge.act as act
    monkeypatch.setattr(act, "SEND_ATTEMPTS", 3)
    raid = tmp_path / "persist.raid.json"
    raid.write_bytes(b"a")                     # never changes
    r = _runner(monkeypatch, armed=True, confirm=False)
    r.raid_path = raid
    r.handle(_battle_update())
    assert r.stats["acted"] == 0
    # three attempts, each one key + one click
    assert len(r.actuator.backend.sent) == 6


def test_an_input_that_goes_nowhere_banks_the_frame(monkeypatch, tmp_path):
    """45 seconds of a hero standing still logs the same line whether a
    tutorial popup was eating clicks, the terminal had focus, or the game
    never agreed it was that hero's turn. The save distinguishes none of
    those and one frame distinguishes all three."""
    raid = tmp_path / "persist.raid.json"
    raid.write_bytes(b"a")                     # never changes
    r = _runner(monkeypatch, armed=True, confirm=False)
    r.raid_path = raid
    banked = []
    monkeypatch.setattr(r, "_bank_frame",
                        lambda shot, note: banked.append(shot) or True)
    r.handle(_battle_update())
    import bridge.act as act
    assert banked == [act.SEND_SHOT], (
        "banked at the FIRST failure only - by the third the operator has "
        "reacted and whatever swallowed the input is gone from the screen")


def test_focus_is_rechecked_before_every_attempt(monkeypatch, tmp_path):
    """`_handle` cleared focus up to 15s before the retry, and the likeliest
    thing to happen in between is the operator clicking their own terminal to
    read the log. A click sent then lands in whatever window that is."""
    import bridge.act as act
    raid = tmp_path / "persist.raid.json"
    raid.write_bytes(b"a")                     # never changes
    r = _runner(monkeypatch, armed=True, confirm=False)
    r.raid_path = raid
    monkeypatch.setattr(r, "_bank_frame", lambda shot, note: True)
    real = r.actuator.execute

    def execute_then_lose_focus(plan):
        ok = real(plan)
        r.window.is_foreground = False
        return ok

    monkeypatch.setattr(r.actuator, "execute", execute_then_lose_focus)
    r.handle(_battle_update())
    assert len(r.actuator.backend.sent) == 2, "kept firing into another window"


def test_a_turn_that_never_landed_is_not_marked_as_acted(monkeypatch, tmp_path):
    """UNWIRED, the key stays open: so it can still be played by hand, and is
    not silently counted.

    This is the `--replay` and bare-runner case. With `run.py` behind it the
    contract is the opposite one -- see the two tests below -- because there
    the mark is what lets a DIFFERENT hero be advised instead.
    """
    raid = tmp_path / "persist.raid.json"
    raid.write_bytes(b"a")
    r = _runner(monkeypatch, armed=True, confirm=False)
    r.raid_path = raid
    assert r.on_turn_missed is None
    u = _battle_update()
    r.handle(u)
    assert u.turn_key not in r.acted
    assert r.stats["refused"] == 1


def test_a_missed_turn_asks_for_the_NEXT_HERO(monkeypatch, tmp_path):
    """THE REPAIR FOR THE 2% WHERE THE SAVE AND THE SCREEN DISAGREE.

    Measured over the capture corpus: on the 110 pairs where a hero
    demonstrably acted, the shipped rule names the right one 108 times -- and
    so do both obvious alternatives, missing the same two. There is no better
    ruler, so the 2% is recovered from instead.

    Live, the failure was fatal rather than cosmetic: the save said Runaway,
    the game was asking the Crusader, the number key selected the Crusader's
    slot while the click aimed at the Runaway's target, nothing landed, and
    the raid ended in a Cove corridor.
    """
    raid = tmp_path / "persist.raid.json"
    raid.write_bytes(b"a")
    r = _runner(monkeypatch, armed=True, confirm=False)
    r.raid_path = raid
    asked = []
    r.on_turn_missed = lambda: asked.append(True)
    u = _battle_update()
    r.handle(u)
    assert asked == [True]
    # ...and the key is spent, or the dedupe re-serves the same dead move.
    assert u.turn_key in r.acted


def test_a_wired_runner_stops_after_ONE_failed_attempt(monkeypatch, tmp_path):
    """Re-sending the identical plan cannot fix the wrong hero.

    Three attempts at 15 s spend 45 s re-proving the same thing, and in the
    live Cove fight that was most of the wall clock -- the disagreement hit
    roughly every other turn. One failure is enough to go and ask about a
    different hero. Unwired, the old budget stands: there is nothing else to
    try, so a transient is worth two more goes.
    """
    raid = tmp_path / "persist.raid.json"
    raid.write_bytes(b"a")
    r = _runner(monkeypatch, armed=True, confirm=False)
    r.raid_path = raid
    r.on_turn_missed = lambda: None
    r.handle(_battle_update())
    keys = [s for s in r.actuator.backend.sent if s.kind == "key"]
    assert len(keys) == 1, "re-sent a plan that cannot land"

    r2 = _runner(monkeypatch, armed=True, confirm=False)
    r2.raid_path = raid
    assert r2.on_turn_missed is None
    r2.handle(_battle_update())
    keys2 = [s for s in r2.actuator.backend.sent if s.kind == "key"]
    assert len(keys2) > 1, "unwired, a transient deserves the full budget"


def test_the_recovery_never_counts_the_turn_as_played(monkeypatch, tmp_path):
    """Marking the key is a DEDUPE fact, not a success one. `acted` must not
    move, `refused` must, and the demonstration corpus must not record a play
    that did not happen."""
    raid = tmp_path / "persist.raid.json"
    raid.write_bytes(b"a")
    r = _runner(monkeypatch, armed=True, confirm=False)
    r.raid_path = raid
    played = []
    r.on_turn_missed = lambda: None
    r.on_played = lambda *a, **k: played.append(a)
    before = r.stats["acted"]
    r.handle(_battle_update())
    assert r.stats["acted"] == before
    assert r.stats["refused"] == 1
    assert played == []


def test_a_save_that_moves_between_attempts_cancels_the_retry(monkeypatch, tmp_path):
    """The window between attempts was a blind spot, and it is where the second
    action came from.

    `before` used to be re-read at the top of every attempt, AFTER the settle.
    So when DD wrote the save during that settle -- which is the normal case,
    since the ack window was shorter than DD's own write latency -- that write
    silently became the new baseline and the retry went out into a turn that
    had already been played. Nothing reported it: from the retry's point of
    view the save had never moved.

    Live, the same key and the same click going twice is a hero moving out and
    straight back again."""
    import bridge.act as act
    raid = tmp_path / "persist.raid.json"
    raid.write_bytes(b"a")
    r = _runner(monkeypatch, armed=True, confirm=False)
    r.raid_path = raid
    real = r.actuator.execute

    def execute_then_write_late(plan):
        ok = real(plan)
        # Too late for the ack window, but before the retry is sent: exactly
        # the case that used to double-press.
        raid.write_bytes(b"ab")
        return ok

    monkeypatch.setattr(r.actuator, "execute", execute_then_write_late)
    monkeypatch.setattr(r, "_wait_for_save_change", lambda *a, **k: False)
    r.handle(_battle_update())
    assert len(r.actuator.backend.sent) == 2, "sent the turn twice"
    assert r.stats["acted"] == 1


def test_a_late_ack_is_still_credited_to_the_send_that_earned_it(monkeypatch, tmp_path):
    """And the turn counts as played, so the poll loop stops re-offering it."""
    import bridge.act as act
    raid = tmp_path / "persist.raid.json"
    raid.write_bytes(b"a")
    r = _runner(monkeypatch, armed=True, confirm=False)
    r.raid_path = raid
    real = r.actuator.execute
    monkeypatch.setattr(r.actuator, "execute",
                        lambda plan: (raid.write_bytes(b"ab"), real(plan))[1])
    monkeypatch.setattr(r, "_wait_for_save_change", lambda *a, **k: False)
    u = _battle_update()
    r.handle(u)
    assert u.turn_key in r.acted


def test_the_combat_ack_window_outlasts_a_dd_animation(monkeypatch):
    """Measured, not chosen. Over `archive/traces/raidsnaps*`, consecutive save
    writes with no human decision between them -- one monster turn resolving --
    are median 4.8s / p90 6.1s / max 11.5s, and DD writes on a HERO's turn 175
    times out of 191, so a hero's ack is that hero's animation PLUS every enemy
    turn before the next hero. 6s sat inside that distribution, which is why
    every send in the fourth live run reported "did not land" and was then
    confirmed on the retry."""
    import bridge.act as act
    assert act.TURN_ACK_TIMEOUT > 11.5


def test_no_raid_path_still_sends(monkeypatch):
    """Confirmation is best-effort: with nothing to check against it sends
    anyway rather than refusing to act at all."""
    r = _runner(monkeypatch, armed=True, confirm=False)
    r.raid_path = None
    r.handle(_battle_update())
    assert r.stats["acted"] == 1


def test_the_kill_switch_stops_a_retry_loop(monkeypatch, tmp_path):
    import bridge.act as act
    raid = tmp_path / "persist.raid.json"
    raid.write_bytes(b"a")
    r = _runner(monkeypatch, armed=True, confirm=False)
    r.raid_path = raid
    monkeypatch.setattr(r.actuator.kill, "check", lambda: True)
    r.handle(_battle_update())
    assert r.actuator.backend.sent == []


# --- out of combat: loot, then walk ---

def _room_update(advice=None, area="rooJ", dungeon=None):
    from bridge.run import Update
    return Update(phase="room", headline="NEXT -> coIJ", detail="",
                  room_advice=advice, dungeon=dungeon, area=area)


def test_loot_is_taken_with_space_after_a_fight(monkeypatch, tmp_path):
    """SPACE = take ALL when the loot window is up, the game's own hotkey.
    The loot window swallows movement input, so it must be cleared first."""
    import bridge.act as act
    raid = tmp_path / "persist.raid.json"
    raid.write_bytes(b"a")
    r = _runner(monkeypatch, armed=True, confirm=False)
    r.raid_path = raid
    real = r.actuator.execute

    def execute_and_write(plan):
        ok = real(plan)
        raid.write_bytes(raid.read_bytes() + b"x")
        return ok

    monkeypatch.setattr(r.actuator, "execute", execute_and_write)
    r.handle(_battle_update())                     # marks "was in battle"
    r.handle(_room_update(A(next_step="coIJ")))    # fight just ended
    assert any(s.vk == act.VK_SPACE for s in r.actuator.backend.sent)


def test_the_first_room_block_does_not_also_walk(monkeypatch, tmp_path):
    """Loot first and only loot: walking into an open loot window does
    nothing, and would burn the step believing it had moved."""
    import bridge.act as act
    raid = tmp_path / "persist.raid.json"
    raid.write_bytes(b"a")
    r = _runner(monkeypatch, armed=True, confirm=False)
    r.raid_path = raid
    real = r.actuator.execute
    monkeypatch.setattr(r.actuator, "execute",
                        lambda p: (raid.write_bytes(raid.read_bytes() + b"x"), real(p))[1])
    r.handle(_battle_update())
    r.handle(_room_update(A(next_step="coIJ")))
    assert not any(s.vk in (act.VK_A, act.VK_D) for s in r.actuator.backend.sent)


def test_walking_stops_as_soon_as_the_area_changes(monkeypatch, tmp_path):
    import bridge.act as act
    raid = tmp_path / "persist.raid.json"
    raid.write_bytes(b"a")
    r = _runner(monkeypatch, armed=True, confirm=False)
    r.raid_path = raid
    monkeypatch.setattr(r.actuator, "execute",
                        lambda p: (raid.write_bytes(raid.read_bytes() + b"x"), True)[1])
    monkeypatch.setattr(r, "_peek", lambda: {"in_battle": False, "area": "coIJ"})
    r._walk_toward("coIJ", "rooJ")
    assert r.stats["acted"] == 1                   # one step, then it arrived


def test_arriving_somewhere_else_is_REPORTED_not_corrected(monkeypatch,
                                                          tmp_path):
    """There is nothing to correct. A hallway has no map direction -- `D`
    advances and `A` backtracks, always -- so a walk that ends in an
    unexpected area is news about the ROUTE, not about the keyboard, and the
    actuator says so and stops rather than reversing."""
    raid = tmp_path / "persist.raid.json"
    raid.write_bytes(b"a")
    r = _runner(monkeypatch, armed=True, confirm=False)
    r.raid_path = raid
    monkeypatch.setattr(r.actuator, "execute",
                        lambda p: (raid.write_bytes(raid.read_bytes() + b"x"), True)[1])
    monkeypatch.setattr(r, "_peek", lambda: {"in_battle": False, "area": "coAB"})
    said = []
    monkeypatch.setattr(r, "_say", lambda m: said.append(m))
    r._walk_toward("coIJ", "rooJ")                 # arrived somewhere else
    assert any("entered coAB (NOT coIJ)" in m for m in said), said
    assert r.stats["acted"] == 1, "walked on after landing off-route"


def test_walking_stops_when_a_fight_starts(monkeypatch, tmp_path):
    raid = tmp_path / "persist.raid.json"
    raid.write_bytes(b"a")
    r = _runner(monkeypatch, armed=True, confirm=False)
    r.raid_path = raid
    monkeypatch.setattr(r.actuator, "execute",
                        lambda p: (raid.write_bytes(raid.read_bytes() + b"x"), True)[1])
    monkeypatch.setattr(r, "_peek", lambda: {"in_battle": True, "area": "rooJ"})
    r._walk_toward("coIJ", "rooJ")
    assert r.stats["acted"] == 1


def test_walking_is_bounded(monkeypatch, tmp_path):
    """It must not press a direction forever if the party never arrives.

    ONE budget. There used to be two, the second being a flip-and-walk-back
    that told a corridor's ends apart by trying both -- which was the whole of
    how direction was learned, and is exactly what the game does not require.
    Reversing on an exhausted budget walks the party AWAY from the exit.
    """
    import bridge.act as act
    monkeypatch.setattr(act, "MAX_WALK_STEPS", 4)
    raid = tmp_path / "persist.raid.json"
    raid.write_bytes(b"a")
    r = _runner(monkeypatch, armed=True, confirm=False)
    r.raid_path = raid
    monkeypatch.setattr(r.actuator, "execute",
                        lambda p: (raid.write_bytes(raid.read_bytes() + b"x"), True)[1])
    monkeypatch.setattr(r, "_peek", lambda: {"in_battle": False, "area": "rooJ"})
    r._walk_toward("coIJ", "rooJ")
    assert r.stats["acted"] == 4


def test_a_stalled_walk_TRIES_THE_DOOR_instead_of_giving_up(monkeypatch,
                                                             tmp_path):
    """THE DOOR IS WHAT THE PARTY IS USUALLY STALLED AGAINST.

    Measured live on 2026-09-05 and it cost the whole raid: the party stood at
    the far end of `coAD` with `rooA` through the door in front of it, `D`
    moved nothing three times, and the walk returned -- while the `W` twenty
    lines below, the only thing that opens a door, was reached solely by
    exhausting all twelve `MAX_WALK_STEPS`. Pressing `W` by hand at that exact
    position put the party in `rooA` in two seconds.

    Same rule as `_LayerDone` one layer up: a step that cannot act must not
    block the one that can.
    """
    import bridge.act as act
    r = _walk_runner(monkeypatch, tmp_path, ["coAD"] * 200)
    # Position never advances: the party is against something.
    monkeypatch.setattr(r, "_walk_progress", lambda a: _W(1, from_front=False,
                                                          total=5))
    monkeypatch.setattr(r, "_target_end_index", lambda a, b: None)
    r._walk_toward("rooA", "coAD")
    keys = [st.vk for st in r.actuator.backend.sent if st.kind == "key"]
    assert act.VK_W in keys, keys
    # WALK_STALL_LIMIT walking steps and not one more: the door retries step
    # BACK, because the party overshoots the door rather than stopping short
    # of it. See DOOR_TRIES.
    assert keys.count(act.VK_D) == act.WALK_STALL_LIMIT, keys
    assert keys.count(act.VK_A) == act.DOOR_TRIES - 1, keys
    assert keys[0] == act.VK_D and keys[-1] == act.VK_W, keys


def test_a_stalled_walk_toward_a_CORRIDOR_still_just_reports(monkeypatch,
                                                             tmp_path):
    """`W` opens a door and interacts with what is in front of the party. A
    corridor is not entered through a door from another corridor, so there is
    nothing for it to do there and pressing it would interact with whatever
    the party happens to be facing."""
    import bridge.act as act
    r = _walk_runner(monkeypatch, tmp_path, ["coAD"] * 200)
    monkeypatch.setattr(r, "_walk_progress", lambda a: _W(1, from_front=False,
                                                          total=5))
    monkeypatch.setattr(r, "_target_end_index", lambda a, b: None)
    said = []
    monkeypatch.setattr(r, "_say", lambda m: said.append(m))
    r._walk_toward("coDE", "coAD")
    keys = [st.vk for st in r.actuator.backend.sent if st.kind == "key"]
    assert act.VK_W not in keys, keys
    assert any("moved nothing" in m for m in said), said


# --- curios: W does the bare-handed one, and nothing blocks the walk ---

def test_a_bare_handed_curio_is_the_W_key():
    """`W` is the game's own `dungeon_interact_with_curio` (binary input table
    at 0xac2658), and it needs no coordinates. 35 of 71 distinct curio
    decisions in the corpus are bare-handed, so this is about half of them."""
    from bridge.act import VK_W
    plan = plan_room(A(curio_action="interact", curio_id="bookshelf"), None)
    assert plan.actionable and plan.layer == "curio"
    assert [s.vk for s in plan.steps] == [VK_W]


def test_a_curio_that_wants_an_item_opens_the_panel():
    """THE SAME `W`, and the difference is what gets clicked next.

    `W` opens the panel either way -- what it does NOT do is the bare-handed
    interaction, which happens only when the hand is clicked. So the plan is
    the keystroke and the choice of button is made against the panel on
    screen, by `_work_curio_panel`, because only the panel says whether the
    provision is actually in a slot.
    """
    from bridge.act import VK_W
    plan = plan_room(A(curio_action="use", curio_id="altar",
                       curio_item="holy_water"), None)
    assert plan.actionable and plan.layer == "curio"
    assert [s.vk for s in plan.steps] == [VK_W]
    assert "holy_water" in plan.why and "altar" in plan.why


def test_the_first_turn_of_the_SECOND_fight_is_not_deduped(monkeypatch, tmp_path):
    """The bug behind every "went straight to the battle and stuck there".

    `turn_key` is (battle_round, hero guid, turns_taken, enemies_first) and all
    four reset when a new fight starts, so the opening turn of every combat is
    `(1, guid, 0, 0)`. `acted` used to carry across the fight boundary, so the
    bridge played `(1, 2, 0, 0)` in `coAB` and then silently dropped the
    identical key in `rooB` -- always the second fight, always without a word
    until `_say_once` named it:

        [act] already played this turn (1, 2, 0, 0) - waiting for the next one
    """
    r = _runner(monkeypatch, armed=True, confirm=False)
    monkeypatch.setattr(r, "_send_and_confirm", lambda plan, **kw: True)
    monkeypatch.setattr(r, "_focused_or_wait", lambda: True)
    monkeypatch.setattr(r, "_refresh_layout", lambda: r.resolved)

    r.handle(_battle_update())                     # fight one, opening turn
    assert r.stats["acted"] == 1
    played = set(r.acted)
    assert played, "the turn was not recorded at all"

    # The fight ends: a room block with no advice is still a fight boundary.
    monkeypatch.setattr(r, "_take_loot", lambda after="the fight": None)
    monkeypatch.setattr(r, "_reset_formation", lambda: None)
    r.handle(_room_update(A()))
    assert not r.acted, "turn keys carried into the next fight"

    r.handle(_battle_update())                     # fight two, same key
    assert r.stats["acted"] == 2, "the second fight's opening turn was dropped"


def test_a_turn_is_still_never_played_twice_within_one_fight(monkeypatch,
                                                             tmp_path):
    """The dedupe the clearing must not break. A republish of the SAME turn
    inside one fight has to stay a no-op -- a double press here plays a second
    move."""
    r = _runner(monkeypatch, armed=True, confirm=False)
    monkeypatch.setattr(r, "_send_and_confirm", lambda plan, **kw: True)
    monkeypatch.setattr(r, "_focused_or_wait", lambda: True)
    monkeypatch.setattr(r, "_refresh_layout", lambda: r.resolved)
    r.handle(_battle_update())
    r.handle(_battle_update())
    r.handle(_battle_update())
    assert r.stats["acted"] == 1, "replayed a turn already played"


def test_a_curio_that_works_is_pressed_exactly_once(monkeypatch, tmp_path):
    """The rule that matters from `coBC`'s sack: never press `W` twice into an
    open loot window. Most curios ARE loot curios and DD saves nothing until
    the loot is taken, so "the curio still reads active" is not evidence the
    press failed -- and the generic three-attempt retry, which believed it
    was, pressed `W` into the open window twice more.

    A press that landed is therefore never repeated. What changed is how
    "landed" is decided: see the retry test below."""
    import bridge.act as act
    r, _raid, _s = _full_pack_runner(monkeypatch, tmp_path, 15,
                                     full_pack="abandon")
    monkeypatch.setattr(r, "_curio_landed", lambda a, c: (True, "it is used up"))
    r.handle(_room_update(A(curio_action="interact", curio_id="sack"),
                          area="coBC"))
    ws = [s for s in r.actuator.backend.sent if act.VK_W in _down(s)]
    assert len(ws) == 1, f"pressed W {len(ws)}x"


def test_a_curio_that_does_nothing_at_all_is_tried_again(monkeypatch, tmp_path):
    """Three live runs in a row, `W` on `travellers_tent_tutorial` did nothing
    -- from its own tile, from one tile short, and from two. Meanwhile the
    same `W` opened the door at the end of the same corridor every time. The
    difference is not the tile, it is that at a door the party has auto-stopped
    and mid-hallway it is still gliding.

    Positioning was the theory twice and was falsified twice, so this searches
    instead. It is safe to retry now only because SPACE goes out BEFORE the
    decision: with no loot window SPACE is inert, so any window the press did
    open is already cleared, and `W` can never land in one."""
    import bridge.act as act
    r, _raid, _s = _full_pack_runner(monkeypatch, tmp_path, 15,
                                     full_pack="abandon")
    monkeypatch.setattr(r, "_curio_landed",
                        lambda a, c: (False, "still active"))
    r.handle(_room_update(A(curio_action="interact", curio_id="sack"),
                          area="coBC"))
    ws = [s for s in r.actuator.backend.sent if act.VK_W in _down(s)]
    assert len(ws) == act.CURIO_TRIES, f"pressed W {len(ws)}x"


def test_a_grown_pack_is_what_proves_a_loot_curio_worked(monkeypatch, tmp_path):
    """The acknowledgement a loot curio actually gives. `content` stays put
    until the loot is taken, so the curio flag cannot answer -- but SPACE has
    just taken it, and the pack is one item bigger than it was."""
    r, _raid, _s = _full_pack_runner(monkeypatch, tmp_path, 2,
                                     full_pack="abandon")
    packs = iter([["a", "b"], ["a", "b", "c"]])
    monkeypatch.setattr(r, "_read_pack", lambda: next(packs, ["a", "b", "c"]))
    monkeypatch.setattr(r, "_curio_active", lambda a, c: True)
    monkeypatch.setattr(r, "_take_loot", lambda after="": None)
    worked, why = r._curio_landed("coBC", "sack")
    assert worked, why
    assert "2 -> 3" in why, why


def test_a_curio_is_followed_by_taking_its_loot(monkeypatch, tmp_path):
    """A sack full of loot left open swallows the walk that comes next, the
    same way the post-fight window does."""
    import bridge.act as act
    r, _raid, _s = _full_pack_runner(monkeypatch, tmp_path, 15,
                                     full_pack="abandon")
    r.handle(_room_update(A(curio_action="interact", curio_id="sack"),
                          area="coBC"))
    sent = r.actuator.backend.sent
    ws = [i for i, s in enumerate(sent) if act.VK_W in _down(s)]
    spaces = [i for i, s in enumerate(sent) if act.VK_SPACE in _down(s)]
    assert ws and spaces, "no loot attempt after the curio"
    assert spaces[0] > ws[0], "took the loot before opening the curio"


def test_a_curio_falls_through_to_the_walk(monkeypatch, tmp_path):
    """Third time this bit: the room is published ONCE, so any handler that
    returns instead of carrying on strands the party. Here it stood next to
    the sack it had just emptied."""
    import bridge.act as act
    r, _raid, _s = _full_pack_runner(monkeypatch, tmp_path, 15,
                                     full_pack="abandon")
    monkeypatch.setattr(r, "_walk_toward", lambda *a: r.stats.__setitem__(
        "walked", r.stats.get("walked", 0) + 1))
    r.handle(_room_update(A(curio_action="interact", curio_id="sack",
                            next_step="rooC"), area="coBC"))
    assert any(act.VK_W in _down(s) for s in r.actuator.backend.sent)
    assert r.stats.get("walked"), "emptied the curio and stood there"


def test_the_curio_is_not_pressed_twice_by_the_re_plan(monkeypatch, tmp_path):
    """Re-planning must strike the curio out, or it interacts forever."""
    import bridge.act as act
    r, _raid, _s = _full_pack_runner(monkeypatch, tmp_path, 15,
                                     full_pack="abandon")
    monkeypatch.setattr(r, "_walk_toward", lambda *a: None)
    monkeypatch.setattr(r, "_curio_landed", lambda a, c: (True, "it is used up"))
    r.handle(_room_update(A(curio_action="interact", curio_id="sack",
                            next_step="rooC"), area="coBC"))
    ws = [s for s in r.actuator.backend.sent if act.VK_W in _down(s)]
    assert len(ws) == 1, f"pressed W {len(ws)}x"


def test_a_curio_in_a_room_does_not_then_try_to_walk_out(monkeypatch, tmp_path):
    """The re-plan goes through `plan_room`, so the room/corridor rule still
    applies and a room exit is still refused rather than hammered."""
    r, _raid, _s = _full_pack_runner(monkeypatch, tmp_path, 15,
                                     full_pack="abandon")
    walked = []
    monkeypatch.setattr(r, "_walk_toward", lambda *a: walked.append(a))
    r.handle(_room_update(A(curio_action="interact", curio_id="bookshelf",
                            next_step="coBC"), area="rooB"))
    assert not walked, "tried to walk out of a room"


def test_looting_falls_through_to_the_rest_of_the_room(monkeypatch, tmp_path):
    """There is no "next poll" for a room the party is standing still in.

    `Session.room` dedupes on (area, torch bracket) and picking loot up
    changes neither, so returning after the loot meant the curio and the walk
    never happened. Seen live: the full-pack recovery ran perfectly and then
    the party stood in `coBC` with a corridor to walk and nothing driving it.
    """
    import bridge.act as act
    r, _raid, _s = _full_pack_runner(monkeypatch, tmp_path, 15,
                                     full_pack="abandon")
    monkeypatch.setattr(r, "_walk_toward", lambda *a: r.stats.__setitem__(
        "walked", r.stats.get("walked", 0) + 1))
    r.handle(_battle_update())
    r.handle(_room_update(A(next_step="rooC"), area="coBC"))
    assert r.stats.get("walked"), "looted and then stood there"


def test_a_pause_during_looting_does_not_fall_through(monkeypatch, tmp_path):
    """Falling through is for a loot that RESOLVED. A pause means the screen
    is not understood, and walking away from that is exactly the blind
    behaviour the pause exists to prevent."""
    r, _raid, _s = _full_pack_runner(monkeypatch, tmp_path, 16)   # pauses
    monkeypatch.setattr(r, "_walk_toward", lambda *a: r.stats.__setitem__(
        "walked", r.stats.get("walked", 0) + 1))
    r.handle(_battle_update())
    r.handle(_room_update(A(next_step="rooC"), area="coBC"))
    assert r.paused
    assert not r.stats.get("walked"), "walked away from a pause"


def _walk_runner(monkeypatch, tmp_path, areas):
    """A runner whose `_peek` returns each area in turn as the walk proceeds."""
    raid = tmp_path / "persist.raid.json"
    raid.write_bytes(b"a")
    r = _runner(monkeypatch, armed=True, confirm=False)
    r.raid_path = raid
    seq = list(areas)
    def peek():
        return {"in_battle": False, "area": seq.pop(0) if seq else areas[-1]}
    monkeypatch.setattr(r, "_peek", peek)
    monkeypatch.setattr("time.sleep", lambda *_a: None)
    return r


def test_walking_does_not_demand_a_save_change_per_step(monkeypatch, tmp_path):
    """The assumption that broke movement.

    `raid.in_area` is area-granular and the save carries no within-area
    position, so walking a corridor writes NOTHING until a boundary is
    crossed. The loop used to require a save change after every keypress and
    bailed on the first with "the party did not move" -- which was false; it
    had moved, invisibly.
    """
    import bridge.act as act
    r = _walk_runner(monkeypatch, tmp_path, ["coBC"] * 4 + ["rooC"])
    r._walk_toward("rooC", "coBC")
    walks = [s for s in r.actuator.backend.sent if s.vk == act.VK_D]
    assert len(walks) >= 4, "gave up after one step again"


def test_the_end_of_a_corridor_is_a_W_into_the_room(monkeypatch, tmp_path):
    """"w solo va hacia cofres o de pasillo a sala" -- so after the corridor
    runs out, the door is W and not another D."""
    import bridge.act as act
    r = _walk_runner(monkeypatch, tmp_path, ["coBC"] * 40)
    r._walk_toward("rooC", "coBC")
    assert any(act.VK_W in _down(s) for s in r.actuator.backend.sent), \
        "never tried the door"


def test_no_W_is_sent_when_the_target_is_a_corridor(monkeypatch, tmp_path):
    """W enters rooms. A corridor-to-corridor step has no door to open."""
    import bridge.act as act
    r = _walk_runner(monkeypatch, tmp_path, ["rooB"] * 40)
    r._walk_toward("coBC", "rooB")
    assert not any(act.VK_W in _down(s) for s in r.actuator.backend.sent)


def _W(index, from_front=True, total=6):
    """A `dungeon_map.Walk` positioned on `index`."""
    from bridge.dungeon_map import Walk
    passed = index + 1 if from_front else total - index
    return Walk(passed=passed, total=total, from_front=from_front)


def _walking(start=0, from_front=True, total=40):
    """A `_walk_progress` stand-in for a party that is actually MOVING.

    A constant `_W` is a party that presses a key and gets nowhere, which is
    what a stalled walk looks like -- and a stall is now read as "the direction
    may be wrong" rather than as scenery, so a static mock makes every walk
    flip. That is the mock lying, not the loop: a walk that advances is what
    these tests mean by walking.
    """
    state = {"i": start}

    def read(_area):
        index = state["i"] if from_front else total - 1 - state["i"]
        walk = _W(max(0, min(index, total - 1)), from_front=from_front,
                  total=total)
        state["i"] += 1
        return walk
    return read


class _AreaWithCurio:
    """The minimum `_map_area` shape `_walk_up_to` reads: one live curio."""
    def __init__(self, index):
        from bridge.dungeon_map import Tile
        self.tiles = (Tile(index=index, content=7, curio_id="tent",
                           trap_id=None, mash_index=-1, knowledge=1, lit=True),)


def _actions(r):
    """What was actually DONE, excluding pointer moves.

    `_park_cursor` slides the pointer into the dead zone before every map
    read -- it presses nothing, and a test asserting "this path sends no
    input" means no keys and no clicks, not no mouse motion."""
    return [s for s in r.actuator.backend.sent if s.kind != "move"]


def test_leaving_a_room_brings_the_map_up_first(monkeypatch):
    """The half of the room exit that can be proved. The two panels share one
    slot, TAB swaps them, and the save records neither -- so this reads the
    panel, presses TAB only when the bag is in front, and confirms the swap.
    It stops there: the destination's pixel has never been measured."""
    import bridge.act as act
    from bridge import screen as screen_mod
    r = _runner(monkeypatch, armed=True, confirm=False)
    seen = ["inventory", "map"]
    monkeypatch.setattr(screen_mod, "grab", lambda w: object())
    monkeypatch.setattr(screen_mod, "panel", lambda i, _raid=None: seen.pop(0) if seen else "map")
    monkeypatch.setattr(screen_mod, "save_png", lambda *a: False)
    monkeypatch.setattr(r, "_focused_or_wait", lambda: True)
    monkeypatch.setattr(r, "_refresh_layout", lambda: r.resolved)
    monkeypatch.setattr("time.sleep", lambda *_a: None)
    r.handle(_room_update(A(next_step="coAB"), area="rooA"))
    assert [s.vk for s in _actions(r)] == [act.VK_TAB]


def test_the_map_already_being_up_costs_no_keypress(monkeypatch):
    """TAB is a toggle. Pressing it on a map that is already showing puts the
    bag back, which is the opposite of the intent."""
    from bridge import screen as screen_mod
    r = _runner(monkeypatch, armed=True, confirm=False)
    monkeypatch.setattr(screen_mod, "grab", lambda w: object())
    monkeypatch.setattr(screen_mod, "panel", lambda i, _raid=None: "map")
    monkeypatch.setattr(screen_mod, "save_png", lambda *a: False)
    monkeypatch.setattr(r, "_focused_or_wait", lambda: True)
    monkeypatch.setattr(r, "_refresh_layout", lambda: r.resolved)
    r.handle(_room_update(A(next_step="coAB"), area="rooA"))
    assert _actions(r) == []


def test_the_destination_room_is_clicked_once_the_map_is_up(monkeypatch):
    """The other half of the room exit. `mappanel` measures where the room is
    drawn -- the map zooms and pans, so it is measured per frame and never
    stored -- and the click goes to that point on the already-calibrated
    client rect."""
    from bridge import screen as screen_mod
    from bridge import mappanel
    r = _runner(monkeypatch, armed=True, confirm=False)
    monkeypatch.setattr(screen_mod, "grab", lambda w: object())
    monkeypatch.setattr(screen_mod, "panel", lambda i, _raid=None: "map")
    monkeypatch.setattr(mappanel, "locate",
                        lambda img, d, a: ((0.75, 0.86), "48.0 px/unit"))
    monkeypatch.setattr(r, "_focused_or_wait", lambda: True)
    monkeypatch.setattr(r, "_refresh_layout", lambda: r.resolved)
    monkeypatch.setattr(r, "_send_and_confirm", lambda plan, **kw: True)
    r.handle(_room_update(A(next_step="coAB", next_room="rooB"),
                          area="rooA", dungeon=object()))
    assert r.stats["acted"] == 1


def test_the_click_uses_the_LAST_fit_not_the_planning_one(monkeypatch):
    """The map animates while it settles, and `_reset_map_view` returns the
    best of a window of frames -- so the point can describe a view that has
    since moved. A stale point does not fail loudly: it lands wherever the map
    now draws that spot and the save simply does not change.

    Measured live 2026-09-03 leaving `rooC` for `rooF`: the click went to
    client (929, 602), the save never moved, and the frame banked a minute
    later fitted 7/7 with `rooF` at (928, 603) -- right for the settled map,
    wrong for the one on screen when it was sent.
    """
    from bridge import screen as screen_mod
    from bridge import mappanel
    seen = []
    calls = []

    def _locate(_img, _d, _a):
        # The planning fit once, then the fresh one on every sampled frame:
        # the loop scores ALL of them and keeps the best, so it does not stop
        # at the first success and the stub must not run out.
        calls.append(1)
        if len(calls) == 1:
            return ((0.75, 0.86), "planning fit")
        return ((0.40, 0.20), "fresh fit")
    r = _runner(monkeypatch, armed=True, confirm=False)
    monkeypatch.setattr(screen_mod, "grab", lambda w: object())
    monkeypatch.setattr(screen_mod, "panel", lambda i, _raid=None: "map")
    monkeypatch.setattr(mappanel, "locate", _locate)
    monkeypatch.setattr("time.sleep", lambda *_a: None)
    monkeypatch.setattr(r, "_focused_or_wait", lambda: True)
    monkeypatch.setattr(r, "_refresh_layout", lambda: r.resolved)
    monkeypatch.setattr(r, "_send_and_confirm",
                        lambda plan, **kw: (seen.append(plan.steps[0].screen),
                                            True)[1])
    r.handle(_room_update(A(next_step="coAB", next_room="rooB"),
                          area="rooA", dungeon=object()))
    assert len(seen) == 1
    assert seen[0] == r.resolved.win.to_screen(0.40, 0.20), \
        "clicked the point the map had already moved away from"


def test_a_view_that_will_not_fit_NOW_is_a_refusal(monkeypatch, capsys):
    """Falling back on the stale point here would be the bug: a view that
    cannot be fitted at the moment of the click is exactly the view that
    swallows one. Refusing keeps the turn re-offerable."""
    from bridge import screen as screen_mod
    from bridge import mappanel
    # The planning fit once, then a view that never fits again -- the code
    # SAMPLES several fresh frames now, because the panel animates and one
    # grab is a coin flip. Refusing still has to happen when none of them fit.
    seen = []

    def _locate(_img, _d, _a):
        seen.append(1)
        if len(seen) == 1:
            return ((0.75, 0.86), "planning fit")
        return (None, "could not fit the map to the 20 cells drawn")
    r = _runner(monkeypatch, armed=True, confirm=False)
    monkeypatch.setattr(screen_mod, "grab", lambda w: object())
    monkeypatch.setattr(screen_mod, "panel", lambda i, _raid=None: "map")
    monkeypatch.setattr(screen_mod, "save_png", lambda *a: False)
    monkeypatch.setattr(mappanel, "locate", _locate)
    monkeypatch.setattr("time.sleep", lambda *_a: None)
    monkeypatch.setattr(r, "_focused_or_wait", lambda: True)
    monkeypatch.setattr(r, "_refresh_layout", lambda: r.resolved)
    monkeypatch.setattr(r, "_send_and_confirm",
                        lambda plan: (_ for _ in ()).throw(
                            AssertionError("sent a click on a view that "
                                           "would not fit")))
    # The throwing `_send_and_confirm` is the real assertion: without the
    # fresh re-locate the stale point is clicked and this raises. The counter
    # is not usable here -- leaving a room refuses A/D first, by design.
    r.handle(_room_update(A(next_step="coAB", next_room="rooB"),
                          area="rooA", dungeon=object()))
    out = capsys.readouterr().out
    assert "would not fit on any" in out
    assert len(seen) > 2, "gave up after one fresh frame instead of sampling"


def test_a_map_that_has_not_moved_still_clicks(monkeypatch):
    """The guard must not turn a working click into a refusal -- the whole
    path is normally two fits of the same settled view."""
    from bridge import screen as screen_mod
    from bridge import mappanel
    r = _runner(monkeypatch, armed=True, confirm=False)
    monkeypatch.setattr(screen_mod, "grab", lambda w: object())
    monkeypatch.setattr(screen_mod, "panel", lambda i, _raid=None: "map")
    monkeypatch.setattr(mappanel, "locate",
                        lambda img, d, a: ((0.75, 0.86), "48.0 px/unit"))
    monkeypatch.setattr(r, "_focused_or_wait", lambda: True)
    monkeypatch.setattr(r, "_refresh_layout", lambda: r.resolved)
    monkeypatch.setattr(r, "_send_and_confirm", lambda plan, **kw: True)
    r.handle(_room_update(A(next_step="coAB", next_room="rooB"),
                          area="rooA", dungeon=object()))
    assert r.stats["acted"] == 1


def test_the_corridor_is_never_the_click_target(monkeypatch):
    """The game's own help is explicit -- "[CLICK] on a room in the map to
    begin moving to that room" -- and it walks the connecting hallway for you.
    `next_step` from a room IS that hallway, so aiming at it would aim at
    something the map does not accept. `next_room` is what gets asked for."""
    from bridge import screen as screen_mod
    from bridge import mappanel
    asked = []
    r = _runner(monkeypatch, armed=True, confirm=False)
    monkeypatch.setattr(screen_mod, "grab", lambda w: object())
    monkeypatch.setattr(screen_mod, "panel", lambda i, _raid=None: "map")
    monkeypatch.setattr(mappanel, "locate",
                        lambda img, d, a: (asked.append(a), (None, "no"))[1])
    monkeypatch.setattr(screen_mod, "save_png", lambda *a: False)
    monkeypatch.setattr(r, "_focused_or_wait", lambda: True)
    monkeypatch.setattr(r, "_refresh_layout", lambda: r.resolved)
    r.handle(_room_update(A(next_step="coAB", next_room="rooB"),
                          area="rooA", dungeon=object()))
    assert asked == ["rooB"]


def test_an_unlocatable_room_is_refused_and_the_frame_is_banked(monkeypatch,
                                                                capsys):
    """A wrong click here is not a no-op: the panel sits over the world, and a
    miss moves the party or pokes a curio. So a refusal has to be a refusal --
    and it banks the frame, because the pixels that would explain it are on
    screen at exactly the moment the bridge gives up."""
    import bridge.act as act
    from bridge import screen as screen_mod
    from bridge import mappanel
    wrote = []
    r = _runner(monkeypatch, armed=True, confirm=False)
    monkeypatch.setattr(screen_mod, "grab", lambda w: object())
    monkeypatch.setattr(screen_mod, "panel", lambda i, _raid=None: "map")
    monkeypatch.setattr(mappanel, "locate",
                        lambda img, d, a: (None, "rooB is not drawn on the map"))
    monkeypatch.setattr(screen_mod, "save_png",
                        lambda img, path: wrote.append(path) or True)
    monkeypatch.setattr(r, "_focused_or_wait", lambda: True)
    monkeypatch.setattr(r, "_refresh_layout", lambda: r.resolved)
    r.handle(_room_update(A(next_step="coAB", next_room="rooB"),
                          area="rooA", dungeon=object()))
    out = capsys.readouterr().out
    assert "NOT clicking the map" in out and "not drawn" in out
    assert _actions(r) == []
    assert len(wrote) == 1 and wrote[0].name == act.MAP_SHOT
    assert wrote[0].parent.name == "captures"


def test_a_failed_screenshot_still_explains_the_refusal(monkeypatch, capsys):
    """Pillow is a dev-only import, so `save_png` returns False on a machine
    without it. The banking is a bonus; the reason the run stopped is the
    deliverable, and it must not be conditional on the bonus succeeding."""
    from bridge import screen as screen_mod
    from bridge import mappanel
    r = _runner(monkeypatch, armed=True, confirm=False)
    monkeypatch.setattr(screen_mod, "grab", lambda w: object())
    monkeypatch.setattr(screen_mod, "panel", lambda i, _raid=None: "map")
    monkeypatch.setattr(mappanel, "locate", lambda img, d, a: (None, "no fit"))
    monkeypatch.setattr(screen_mod, "save_png", lambda *a: False)
    monkeypatch.setattr(r, "_focused_or_wait", lambda: True)
    monkeypatch.setattr(r, "_refresh_layout", lambda: r.resolved)
    r.handle(_room_update(A(next_step="coAB", next_room="rooB"),
                          area="rooA", dungeon=object()))
    out = capsys.readouterr().out
    assert "NOT clicking the map: no fit" in out
    assert "banked" not in out


def test_an_unreadable_panel_never_presses_TAB(monkeypatch):
    """A toggle pressed on an unknown state is a coin flip, and the losing
    side hides the map the whole point was to show."""
    from bridge import screen as screen_mod
    r = _runner(monkeypatch, armed=True, confirm=False)
    monkeypatch.setattr(screen_mod, "grab", lambda w: object())
    monkeypatch.setattr(screen_mod, "panel", lambda i, _raid=None: "")
    monkeypatch.setattr(r, "_focused_or_wait", lambda: True)
    monkeypatch.setattr(r, "_refresh_layout", lambda: r.resolved)
    r.handle(_room_update(A(next_step="coAB"), area="rooA"))
    assert _actions(r) == []


def test_the_pointer_is_parked_before_the_map_is_read(monkeypatch):
    """The bridge was corrupting its own evidence. After a successful map
    click the cursor STAYS where SendInput left it -- on a room -- so DD drew
    a hover border and a "Move to this room" tooltip, and the next frame came
    back with that room's cell merged with its neighbour plus thirteen
    letter-shaped boxes in a row. 19 cells instead of 6, and no fit. Moving,
    never clicking, into the dead zone that is black on every capture."""
    import bridge.act as act
    from bridge import screen as screen_mod
    r = _runner(monkeypatch, armed=True, confirm=False)
    monkeypatch.setattr(screen_mod, "grab", lambda w: object())
    monkeypatch.setattr(screen_mod, "panel", lambda i, _raid=None: "")
    monkeypatch.setattr(r, "_focused_or_wait", lambda: True)
    monkeypatch.setattr(r, "_refresh_layout", lambda: r.resolved)
    r.handle(_room_update(A(next_step="coAB"), area="rooA"))
    moves = [s for s in r.actuator.backend.sent if s.kind == "move"]
    assert moves, "read the map without parking the pointer"
    assert moves[0].screen == r.resolved.win.to_screen(*act.MENU_DEAD_ZONE)
    assert not any(s.kind == "click" for s in r.actuator.backend.sent)


def test_a_backend_that_cannot_move_the_pointer_still_reads_the_map(monkeypatch):
    """Best-effort. Parking is a defence against a tooltip, not a
    precondition -- a backend without a working move must lose the defence and
    nothing else."""
    from bridge import screen as screen_mod
    r = _runner(monkeypatch, armed=True, confirm=False)
    said = []
    monkeypatch.setattr(r.actuator.backend, "move",
                        lambda pt: (_ for _ in ()).throw(RuntimeError("no")))
    monkeypatch.setattr(screen_mod, "grab", lambda w: said.append(1) or object())
    monkeypatch.setattr(screen_mod, "panel", lambda i, _raid=None: "")
    monkeypatch.setattr(r, "_focused_or_wait", lambda: True)
    monkeypatch.setattr(r, "_refresh_layout", lambda: r.resolved)
    r.handle(_room_update(A(next_step="coAB"), area="rooA"))
    assert said, "a failed park stopped the read"




def test_a_room_curio_is_pressed_without_walking(monkeypatch, tmp_path):
    """A room is one tile and the party is standing on it, so the curio is
    already in front of them. Walking there would be the shuffle `plan_room`
    refuses everywhere else."""
    import bridge.act as act
    r, _raid, _s = _full_pack_runner(monkeypatch, tmp_path, 15,
                                     full_pack="abandon")
    # This test is about the WALK. Let the press land so the search in
    # `_interact_curio` does not add attempts on top of it.
    monkeypatch.setattr(r, "_curio_landed", lambda a, c: (True, "used up"))
    r.handle(_room_update(A(curio_action="interact", curio_id="chest"),
                          area="rooB"))
    keys = [s.vk for s in r.actuator.backend.sent if s.kind == "key"]
    assert keys[0] == act.VK_W
    assert act.VK_D not in keys and act.VK_A not in keys












def test_the_forward_key_is_D_unless_a_named_tile_is_BEHIND(monkeypatch,
                                                           tmp_path):
    """`A` is backtrack, and a named tile already passed is the only caller
    that can ask for it.

    This used to be `test_the_direction_is_read_from_the_save_not_guessed`,
    which asserted `A` for a curio the party had NOT reached -- on the
    since-retired theory that a party entering at the far end walks the tile
    order backwards on screen too. It does not: the tile order is the save's
    own numbering and says nothing about the keyboard.

    Note the second pair. Entering at the far end makes the deeper tiles the
    LOWER-numbered ones, and the key for them is still `D`. That is the exact
    case the old reading got backwards.
    """
    import bridge.act as act
    r, _raid, _s = _full_pack_runner(monkeypatch, tmp_path, 15,
                                     full_pack="abandon")
    # Entered at tile 0, standing on tile 20 of 40.
    front = _W(20, from_front=True, total=40)
    assert r._forward_key("coAB", front) == act.VK_D, "no tile named: forward"
    assert r._forward_key("coAB", front, toward=30) == act.VK_D
    assert r._forward_key("coAB", front, toward=10) == act.VK_A

    # Entered at tile 39, standing on tile 20. Deeper is a LOWER index now.
    back = _W(20, from_front=False, total=40)
    assert r._forward_key("coAB", back, toward=10) == act.VK_D
    assert r._forward_key("coAB", back, toward=30) == act.VK_A

    # An unreadable position walks forward rather than refusing.
    assert r._forward_key("coAB", None, toward=10) == act.VK_D






def test_an_almost_empty_pack_never_pauses_the_run(monkeypatch, tmp_path):
    """SPACE with no loot window is inert and is meant to be -- but the save
    then does not change, which used to route into the full-pack recovery.
    It clicked the modal coordinate into the dungeon backdrop and then STOPPED
    the run at 1/16 slots. The modal means "the loot did not fit"; the save
    lags by a pickup, not by half a pack."""
    r, _raid, _s = _full_pack_runner(monkeypatch, tmp_path, 1,
                                     full_pack="pause")
    r._make_room()
    assert not r.paused


def test_a_genuinely_full_pack_still_pauses(monkeypatch, tmp_path):
    """The guard above must not swallow the case it was built around."""
    r, _raid, _s = _full_pack_runner(monkeypatch, tmp_path, 16,
                                     full_pack="pause")
    r._make_room()
    assert r.paused


def test_a_corridor_never_reaches_the_map_path(monkeypatch):
    """Corridors walk. Only a ROOM needs the map, and pressing TAB in a
    corridor would swap away the pack the loot layer reads."""
    from bridge import screen as screen_mod
    r = _runner(monkeypatch, armed=True, confirm=False)
    called = []
    monkeypatch.setattr(screen_mod, "panel", lambda i, _raid=None: called.append(1) or "map")
    monkeypatch.setattr(r, "_walk_toward", lambda *a, **k: None)
    monkeypatch.setattr(r, "_focused_or_wait", lambda: True)
    monkeypatch.setattr(r, "_refresh_layout", lambda: r.resolved)
    r.handle(_room_update(A(next_step="rooC"), area="coBC"))
    assert called == []



def test_a_destination_BEHIND_the_party_is_walked_to_with_A(monkeypatch,
                                                            tmp_path):
    """A destination back the way the party came is walked to with `A`.

    The party entered at tile 0, so tile 0 is the door behind it. A route step
    naming the room at THAT end is a backtrack, and backtrack is `A` -- the
    only case in the whole layer that is not plain `D`.

    This replaces a one-step "is the gap growing" check that flipped the
    direction mid-corridor. The gap could grow for one reason only, which was
    facing the wrong way, and facing is not a thing here.
    """
    import bridge.act as act
    r = _walk_runner(monkeypatch, tmp_path, ["coEF"] * 40)
    monkeypatch.setattr(r, "_target_end_index", lambda a, b: 0)
    monkeypatch.setattr(r, "_walk_progress", _walking(0, from_front=True))
    r._walk_toward("rooF", "coEF")
    sent = [step.vk for step in r.actuator.backend.sent]
    assert act.VK_A in sent
    # The door back-off is the OPPOSITE of the walk, so here it is `D` -- and
    # there are at most DOOR_TRIES-1 of them. Anything more would be the walk
    # itself having turned round, which is the thing this test is named for.
    assert sent.count(act.VK_D) <= act.DOOR_TRIES - 1, (
        "walked away from a destination behind us")


def test_a_destination_at_the_FAR_end_is_plain_D(monkeypatch, tmp_path):
    """The control, and the common case: the party entered at tile 0 and the
    room it wants is at the other end, so it walks forward the whole way."""
    import bridge.act as act
    r = _walk_runner(monkeypatch, tmp_path, ["coEF"] * 6 + ["rooE"])
    monkeypatch.setattr(r, "_target_end_index", lambda a, b: 39)
    monkeypatch.setattr(r, "_walk_progress", _walking(0, from_front=True))
    r._walk_toward("rooE", "coEF")
    sent = [step.vk for step in r.actuator.backend.sent]
    assert act.VK_A not in sent, "backtracked toward the far end"


def test_an_unknown_target_end_walks_FORWARD(monkeypatch, tmp_path):
    """No map, no `mappos`, or a corridor the save cannot place: `D`. Entering
    a corridor to cross it is the overwhelmingly common case, and it is what
    the route asks for whenever it extends the path rather than retracing
    it."""
    import bridge.act as act
    r = _walk_runner(monkeypatch, tmp_path, ["coBC"] * 4 + ["rooC"])
    monkeypatch.setattr(r, "_target_end_index", lambda a, b: None)
    monkeypatch.setattr(r, "_walk_progress", _walking(0, from_front=True))
    r._walk_toward("rooC", "coBC")
    sent = [step.vk for step in r.actuator.backend.sent]
    assert act.VK_A not in sent and act.VK_D in sent


def test_an_unreadable_map_yields_no_target_end(monkeypatch, tmp_path):
    """`_target_end_index` answers "which end", and `None` when it cannot.

    `None` is load-bearing rather than defensive: paired with
    `Walk.entry_index` this is the whole backtrack decision, so a caller that
    cannot get an answer walks `D`, which is what a route extending the
    party's path always wants.
    """
    r = _runner(monkeypatch, armed=True, confirm=False)
    r.raid_path = None
    assert r._target_end_index("coEF", "rooE") is None


def test_crossing_a_corridor_is_D_whichever_end_it_was_entered_by(
        monkeypatch, tmp_path):
    """The rule, in the shape the old test had backwards.

    It used to assert `A` here, on the theory that a party entering at the far
    end walks the tile order backwards on screen. Fran: *"hallways have no
    directions, only left (backtrack) and right (advance)"* -- DD draws the
    party entering at the LEFT of the hallway art however the corridor runs on
    the map. `plan_hallway_sweep` had his words for it all along ("a hallway
    is entered at its LEFT and left at its RIGHT") a hundred lines from the
    property that contradicted them.
    """
    import bridge.act as act
    for from_front in (True, False):
        r = _walk_runner(monkeypatch, tmp_path, ["coBC"] * 4 + ["rooB"])
        monkeypatch.setattr(r, "_walk_progress",
                            _walking(4, from_front=from_front))
        r._walk_toward("rooB", "coBC")
        sent = [s.vk for s in r.actuator.backend.sent]
        assert act.VK_A not in sent, from_front
        assert sent.count(act.VK_D) >= 4, from_front


def test_the_walk_is_never_reversed(monkeypatch, tmp_path):
    """The walk-back existed to tell one end of a corridor from the other by
    trying both. With the direction derived there is nothing to try, and an
    exhausted budget means the corridor is long or something is in the way --
    reversing on either walks the party AWAY from the exit."""
    import bridge.act as act
    r = _walk_runner(monkeypatch, tmp_path, ["coBC"] * 200)
    monkeypatch.setattr(r, "_walk_progress", _walking(2, from_front=True))
    r._walk_toward("rooC", "coBC")
    sent = [s.vk for s in r.actuator.backend.sent]
    # The whole budget walked forward, and NOT ONE step of it re-walked.
    assert sent.count(act.VK_D) == act.MAX_WALK_STEPS
    # The door back-offs are `A` and they are not a reversal: a reversal is
    # re-walking the corridor, which is what Fran ruled out ("not useful to
    # walk backwards because of enhanced stress"), and this is at most two
    # steps against a door the party has overshot. The BOUND is what keeps
    # the two apart, so it is the bound that is pinned.
    assert sent.count(act.VK_A) <= act.DOOR_TRIES - 1
    assert act.DOOR_TRIES - 1 < act.MAX_WALK_STEPS







def test_the_fit_ORDERS_the_candidates_and_the_GAME_picks(monkeypatch):
    """The inversion the layer needed.

    Every map failure this session was a transform that was confident and
    wrong -- 6/8 boxes explained put `rooE` 72 px from where 9/9 put it -- and
    no amount of tightening the fit fixes a measurement whose only check is a
    15 s silence. Asking the game about the fit's near misses costs a hover
    each and does not care whether the fit was exactly right, only close.
    """
    import numpy as np
    from bridge import screen as screen_mod
    from bridge import mappanel
    sent = []
    r = _runner(monkeypatch, armed=True, confirm=False)
    monkeypatch.setattr(screen_mod, "grab",
                        lambda w: np.zeros((720, 1280, 3), dtype=np.uint8))
    monkeypatch.setattr(screen_mod, "panel", lambda i, _raid=None: "map")
    monkeypatch.setattr(screen_mod, "save_png", lambda *a: False)
    monkeypatch.setattr(mappanel, "locate",
                        lambda img, d, a: ((0.50, 0.50), "48.0 px/unit"))
    # One cell sits a little away from the fit's own point; only it is legal.
    good = mappanel.Box(700, 740, 400, 440)
    monkeypatch.setattr(mappanel, "find_boxes", lambda img: [good])
    monkeypatch.setattr(r, "_focused_or_wait", lambda: True)
    monkeypatch.setattr(r, "_refresh_layout", lambda: r.resolved)
    want = r.resolved.win.to_screen(720 / 1280, 420 / 720)
    monkeypatch.setattr(r, "_map_offers_move",
                        lambda sm, mp, at: at == want)
    monkeypatch.setattr(r, "_send_and_confirm",
                        lambda plan, **kw: (sent.append(plan.steps[0].screen),
                                            True)[1])
    r.handle(_room_update(A(next_step="coAB", next_room="rooB"),
                          area="rooA", dungeon=object()))
    assert sent == [want], "did not click the cell the game actually offered"


def test_the_fits_OWN_point_is_not_offered_back_to_itself(monkeypatch):
    """The caller has just been told no about it, so re-probing it wastes a
    hover and, worse, could pick it anyway on a flaky second read."""
    import numpy as np
    from bridge import screen as screen_mod
    from bridge import mappanel
    r = _runner(monkeypatch, armed=True, confirm=False)
    img = np.zeros((720, 1280, 3), dtype=np.uint8)
    monkeypatch.setattr(mappanel, "find_boxes",
                        lambda i: [mappanel.Box(636, 644, 356, 364)])
    near = r._nearby_cells(screen_mod, mappanel, img, (0.5, 0.5))
    assert near == [], "offered the fit its own point back"


def test_room_sized_near_misses_are_probed_before_corridor_ones(monkeypatch):
    """ONLY A ROOM IS A LEGAL DESTINATION -- the game walks the connecting
    hallway itself, which is why `RoutePlan.next_step` is not a click target.

    Each candidate costs a hover of about a second and `MAP_CANDIDATES` is 4,
    so probing corridor tiles the game will never offer spends most of the
    budget on certain refusals. The nearest cell here is a hall and the room
    is further away; the room must still be asked first.
    """
    import numpy as np
    from bridge import mappanel, mapicons
    from bridge import screen as screen_mod
    r = _runner(monkeypatch, armed=True, confirm=False)
    img = np.zeros((720, 1280, 3), dtype=np.uint8)
    scale = 12.4
    hall, room = mapicons.hall_px(scale), mapicons.room_px(scale)

    def _box(cx, cy, side):
        half = int(round(side / 2))
        return mappanel.Box(int(cx) - half, int(cx) + half,
                            int(cy) - half, int(cy) + half)

    near_hall, far_room = _box(660, 360, hall), _box(720, 360, room)
    monkeypatch.setattr(mappanel, "find_boxes", lambda i: [near_hall, far_room])
    monkeypatch.setattr(mappanel, "tiles_of", lambda d: [(0.0, 0.0)])
    monkeypatch.setattr(mappanel, "kinds_of", lambda d: {(0.0, 0.0): "room"})
    monkeypatch.setattr(mappanel, "fit", lambda *a, **k: mappanel.MapFit(
        scale=scale, ox=0.0, oy=0.0, matched=2, boxes=2, residual=0.0))

    got = r._nearby_cells(screen_mod, mappanel, img, (0.5, 0.5),
                          dungeon=object())
    assert [round(x * 1280) for x, _y in got] == [720, 660], got

    # ...and with no dungeon to fit against it is plain nearest-first, which
    # is what every caller had before and what a failure here must fall back
    # to rather than losing the move.
    plain = r._nearby_cells(screen_mod, mappanel, img, (0.5, 0.5))
    assert [round(x * 1280) for x, _y in plain] == [660, 720], plain

def test_no_tooltip_means_the_game_REFUSES_the_move(monkeypatch):
    """Fran, on a raid that would not leave its room: "if you don't get the
    tooltip the game won't let you go to that path". A destination the game
    will not accept is indistinguishable, from the save alone, from a
    coordinate that missed -- and this layer spent a whole raid unable to tell
    those apart."""
    from bridge import screen as screen_mod
    from bridge import mappanel
    r = _runner(monkeypatch, armed=True, confirm=False)
    monkeypatch.setattr(screen_mod, "grab", lambda w: object())
    monkeypatch.setattr(screen_mod, "panel", lambda i, _raid=None: "map")
    monkeypatch.setattr(screen_mod, "save_png", lambda *a: False)
    monkeypatch.setattr(mappanel, "locate",
                        lambda img, d, a: ((0.75, 0.86), "48.0 px/unit"))
    monkeypatch.setattr(r, "_focused_or_wait", lambda: True)
    monkeypatch.setattr(r, "_refresh_layout", lambda: r.resolved)
    monkeypatch.setattr(r, "_map_offers_move", lambda *a: False)
    monkeypatch.setattr(r, "_send_and_confirm",
                        lambda plan, **kw: (_ for _ in ()).throw(
                            AssertionError("clicked a move the game refused")))
    said = []
    monkeypatch.setattr(r, "_say", lambda m: said.append(m))
    r.handle(_room_update(A(next_step="coAB", next_room="rooB"),
                          area="rooA", dungeon=object()))
    # POSITIVE evidence, not just the absence of a click. This was written as
    # a throwing stub and nothing else, which `tools/find_silent_tests.py`
    # correctly flagged: a test that can only fail by raising passes just as
    # happily when the code under it stops running at all.
    #
    # It asserts the REASON rather than a refusal count, because two layers
    # refuse this room -- `plan_room` first ("leaving a room needs the door")
    # and then the map -- and a count cannot tell which one spoke.
    assert any("offers no move at or near rooB" in m for m in said), said
    # A cursor MOVE is expected and is the probe itself parking the pointer;
    # a move cannot change the game's state. A CLICK can, and is the thing
    # that must not happen.
    assert not [x for x in r.actuator.backend.sent if x.kind != "move"],         r.actuator.backend.sent


def test_an_UNASKABLE_tooltip_still_clicks(monkeypatch):
    """`None` is "could not ask" and is deliberately distinct from `False`.
    A backend with no pointer control, or a frame that is not an image, must
    fall back to the old behaviour rather than refuse a move it simply could
    not check -- otherwise adding the probe would disable the layer wherever
    it cannot run."""
    from bridge import screen as screen_mod
    from bridge import mappanel
    sent = []
    r = _runner(monkeypatch, armed=True, confirm=False)
    monkeypatch.setattr(screen_mod, "grab", lambda w: object())
    monkeypatch.setattr(screen_mod, "panel", lambda i, _raid=None: "map")
    monkeypatch.setattr(mappanel, "locate",
                        lambda img, d, a: ((0.75, 0.86), "48.0 px/unit"))
    monkeypatch.setattr(r, "_focused_or_wait", lambda: True)
    monkeypatch.setattr(r, "_refresh_layout", lambda: r.resolved)
    monkeypatch.setattr(r, "_map_offers_move", lambda *a: None)
    monkeypatch.setattr(r, "_send_and_confirm",
                        lambda plan, **kw: (sent.append(1), True)[1])
    r.handle(_room_update(A(next_step="coAB", next_room="rooB"),
                          area="rooA", dungeon=object()))
    assert sent, "refused a move it could not check"


def test_the_probe_parks_the_pointer_before_the_BEFORE_frame(monkeypatch):
    """The comparison frame has to be one with no tooltip on it -- and parking
    is also what makes the fit upstream trustworthy, since the tooltip is
    drawn OVER the map and occludes the cells underneath."""
    import numpy as np

    import bridge.act as act
    from bridge import screen as screen_mod
    from bridge import mappanel
    order = []
    frame = np.zeros((32, 48, 3), dtype=np.uint8)
    r = _runner(monkeypatch, armed=True, confirm=False)
    monkeypatch.setattr(r, "_park_cursor", lambda: order.append("park"))
    monkeypatch.setattr(screen_mod, "grab",
                        lambda w: (order.append("grab"), frame)[1])
    monkeypatch.setattr("time.sleep", lambda *_a: None)
    # The sampling window is real wall clock, so shut it after the BEFORE
    # frame -- this test is about the ORDER of the first three calls, and the
    # pulse itself is pinned by its own test.
    monkeypatch.setattr(act, "TOOLTIP_SAMPLE_FOR", 0.0)
    r.actuator.backend.move = lambda at: (order.append("move"), True)[1]
    r._map_offers_move(screen_mod, mappanel, (10, 10))
    assert order[:3] == ["park", "grab", "move"], order

def test_a_map_click_is_sent_ONCE(monkeypatch):
    """A map coordinate does not survive its own miss.

    `_send_and_confirm` re-sends the identical plan up to three times, which
    is right for a keystroke and wrong for a point measured off a panel: a
    click that missed may itself have dragged the view, so the retries name
    somewhere else and each one can pan it further. Fran, on the stalled raid:
    "you did drag the map I think... if you drag you lose the map".
    """
    from bridge import screen as screen_mod
    from bridge import mappanel
    got = {}
    r = _runner(monkeypatch, armed=True, confirm=False)
    monkeypatch.setattr(screen_mod, "grab", lambda w: object())
    monkeypatch.setattr(screen_mod, "panel", lambda i, _raid=None: "map")
    monkeypatch.setattr(mappanel, "locate",
                        lambda img, d, a: ((0.75, 0.86), "48.0 px/unit"))
    monkeypatch.setattr(r, "_focused_or_wait", lambda: True)
    monkeypatch.setattr(r, "_refresh_layout", lambda: r.resolved)
    monkeypatch.setattr(r, "_send_and_confirm",
                        lambda plan, **kw: (got.update(kw), True)[1])
    r.handle(_room_update(A(next_step="coAB", next_room="rooB"),
                          area="rooA", dungeon=object()))
    assert got.get("attempts") == 1, "the map click is still retried blindly"


def test_a_MISSED_map_click_recentres_the_view(monkeypatch):
    """A click that misses can leave the panel panned off the explored map,
    and a panned view is worse than a broken one: it still FITS, on the part
    of the grid it can see, and hands the next poll a confident point for the
    wrong place. The banked frame from the live stall shows exactly that --
    empty grid where rooms had been."""
    from bridge import screen as screen_mod
    from bridge import mappanel
    reset = []
    r = _runner(monkeypatch, armed=True, confirm=False)
    monkeypatch.setattr(screen_mod, "grab", lambda w: object())
    monkeypatch.setattr(screen_mod, "panel", lambda i, _raid=None: "map")
    monkeypatch.setattr(screen_mod, "save_png", lambda *a: False)
    monkeypatch.setattr(mappanel, "locate",
                        lambda img, d, a: ((0.75, 0.86), "48.0 px/unit"))
    monkeypatch.setattr(r, "_focused_or_wait", lambda: True)
    monkeypatch.setattr(r, "_refresh_layout", lambda: r.resolved)
    monkeypatch.setattr(r, "_send_and_confirm", lambda plan, **kw: False)
    monkeypatch.setattr(r, "_reset_map_view",
                        lambda *a, **k: (reset.append(1), None)[1])
    r.handle(_room_update(A(next_step="coAB", next_room="rooB"),
                          area="rooA", dungeon=object()))
    assert reset, "left the view wherever the miss put it"

def test_an_EMPTY_panel_reopens_it_rather_than_giving_up(monkeypatch):
    """`locate` refuses four ways and two of them are about the VIEW. "nothing
    is drawn" was on the wrong side of that split and it cost a raid: the
    panel was up and fully drawn a second later, `find_boxes` came back empty
    on one transient frame, and the turn was given up on because the message
    does not contain "could not fit the map"."""
    from bridge import screen as screen_mod
    from bridge import mappanel
    r = _runner(monkeypatch, armed=True, confirm=False)
    monkeypatch.setattr(screen_mod, "grab", lambda w: object())
    monkeypatch.setattr(screen_mod, "panel", lambda i, _raid=None: "map")
    monkeypatch.setattr(screen_mod, "save_png", lambda *a: False)
    monkeypatch.setattr(mappanel, "locate",
                        lambda img, d, a: (None, "nothing is drawn in the map panel"))
    monkeypatch.setattr(r, "_focused_or_wait", lambda: True)
    monkeypatch.setattr(r, "_refresh_layout", lambda: r.resolved)
    reset = []
    monkeypatch.setattr(r, "_reset_map_view",
                        lambda *a, **k: (reset.append(1), None)[1])
    r.handle(_room_update(A(next_step="coAB", next_room="rooB"),
                          area="rooA", dungeon=object()))
    assert reset, "an empty panel did not reopen it"


def test_a_room_that_is_NOT_DRAWN_still_refuses_without_pressing_keys(monkeypatch):
    """The other side of the split, and it must not move: an unscouted room is
    not on the map and reopening the panel cannot put it there. A refusal that
    quietly presses keys first is not a refusal."""
    from bridge import screen as screen_mod
    from bridge import mappanel
    r = _runner(monkeypatch, armed=True, confirm=False)
    monkeypatch.setattr(screen_mod, "grab", lambda w: object())
    monkeypatch.setattr(screen_mod, "panel", lambda i, _raid=None: "map")
    monkeypatch.setattr(screen_mod, "save_png", lambda *a: False)
    monkeypatch.setattr(mappanel, "locate",
                        lambda img, d, a: (None, "rooB is not drawn on the map"))
    monkeypatch.setattr(r, "_focused_or_wait", lambda: True)
    monkeypatch.setattr(r, "_refresh_layout", lambda: r.resolved)
    reset = []
    monkeypatch.setattr(r, "_reset_map_view",
                        lambda *a, **k: (reset.append(1), None)[1])
    r.handle(_room_update(A(next_step="coAB", next_room="rooB"),
                          area="rooA", dungeon=object()))
    assert not reset, "reopened the panel for a room that is simply not there"

def test_a_stray_curio_panel_is_CLOSED_before_anything_else(monkeypatch):
    """It is modal over the world: every keystroke after it goes into it.

    Seen live -- the party reached `pile_of_scrolls`, the plan correctly said
    "LEAVING pile_of_scrolls BEHIND: wants torch used on it, and only the
    bare-handed W is bound", and then the torch relight and the door `W` each
    reported "no save change in 15s" three times over while TAB and A/D kept
    working. Those two reach the game; the other two reached the panel.
    """
    from bridge import curiopanel
    from bridge import screen as screen_mod
    r = _runner(monkeypatch, armed=True, confirm=False)
    monkeypatch.setattr(screen_mod, "grab", lambda w: object())
    monkeypatch.setattr(curiopanel, "find", lambda img: curiopanel.CurioButtons(
        hand=(0.64, 0.43), close=(0.76, 0.43)))
    monkeypatch.setattr(r, "_focused_or_wait", lambda: True)
    monkeypatch.setattr(r, "_refresh_layout", lambda: r.resolved)
    assert r._dismiss_stray_curio_panel() is True
    sent = [s for s in r.actuator.backend.sent if s.kind == "click"]
    assert sent, "the panel was detected and nothing was clicked"
    assert sent[-1].screen == r.resolved.win.to_screen(0.76, 0.43),         "clicked the hand instead of the red X"


def test_no_panel_means_no_click(monkeypatch):
    """Positive evidence only. This runs on every room, ahead of everything
    else, so a frame with no panel must cost nothing."""
    from bridge import curiopanel
    from bridge import screen as screen_mod
    r = _runner(monkeypatch, armed=True, confirm=False)
    monkeypatch.setattr(screen_mod, "grab", lambda w: object())
    monkeypatch.setattr(curiopanel, "find", lambda img: None)
    assert r._dismiss_stray_curio_panel() is False
    assert not r.actuator.backend.sent


def test_an_unreadable_frame_costs_nothing(monkeypatch):
    """`find` is pixel arithmetic and raises on anything that is not an image.
    It is inside the guard with the grab for that reason."""
    from bridge import curiopanel
    from bridge import screen as screen_mod
    r = _runner(monkeypatch, armed=True, confirm=False)
    monkeypatch.setattr(screen_mod, "grab", lambda w: object())
    monkeypatch.setattr(curiopanel, "find",
                        lambda img: (_ for _ in ()).throw(ValueError("not an image")))
    assert r._dismiss_stray_curio_panel() is False
    assert not r.actuator.backend.sent

def test_a_door_says_which_room_it_actually_opened_onto(monkeypatch,
                                                       tmp_path):
    """The third way out of a corridor, and the only one that used to report
    nothing when it went wrong.

    The `A` that made it go wrong is gone -- a `W` at the wrong end put the
    party back in the room it came from, and it was pressed there because the
    walk had gone backwards. What stays is the report: a door can still open
    onto somewhere the route did not name, and a run log that says `rooB`
    when the party is in `rooA` is unreadable.

    Measured live 2026-09-03 on the tutorial map: rooA -> map click -> coAB ->
    `A` -> "at the end of coAB" -> `W` -> rooA, once every 24 s for the whole
    900 s play budget. Nothing else in the raid advanced.
    """
    import bridge.act as act
    for landed, verdict in (("rooA", "entered rooA (NOT rooB)"),
                            ("rooB", "entered rooB (as advised)")):
        r = _walk_runner(monkeypatch, tmp_path,
                         ["coAB"] * (act.MAX_WALK_STEPS + 1) + [landed] * 5)
        monkeypatch.setattr(r, "_walk_progress", _walking(0, from_front=True))
        said = []
        monkeypatch.setattr(r, "_say", lambda m: said.append(m))
        r._walk_toward("rooB", "coAB")
        assert any(verdict in m for m in said), (landed, said)


def test_a_door_is_watched_through_the_animation(monkeypatch, tmp_path):
    """Entering a room is an animation and DD writes the save when it ends, so
    one look 0.6s after the `W` decided the party was still in the corridor
    while it was walking into the room. Live, the log said "still in coAB after
    12 steps" about a party that finished in rooB."""
    r = _walk_runner(monkeypatch, tmp_path, ["coAB"] * 14 + ["rooB"] * 5)
    said = []
    monkeypatch.setattr(r, "_say", lambda m: said.append(m))
    r._walk_toward("rooB", "coAB")
    assert any("entered rooB" in m for m in said), said
    assert not any("still in coAB" in m for m in said), said


def test_a_fight_on_entering_a_room_stops_the_walk(monkeypatch, tmp_path):
    """A guarded curio opens a fight the moment the party steps in, and that
    is a save change that never touches `in_area`. Watching only the area read
    it as "the door did not open"."""
    raid = tmp_path / "persist.raid.json"
    raid.write_bytes(b"a")
    r = _runner(monkeypatch, armed=True, confirm=False)
    r.raid_path = raid
    seen = {"n": 0}
    def peek():
        seen["n"] += 1
        return {"in_battle": seen["n"] > 13, "area": "coAB"}
    monkeypatch.setattr(r, "_peek", peek)
    monkeypatch.setattr("time.sleep", lambda *_a: None)
    said = []
    monkeypatch.setattr(r, "_say", lambda m: said.append(m))
    r._walk_toward("rooB", "coAB")
    assert any("a fight started entering rooB" in m for m in said), said


def test_a_room_is_not_walked_out_of_with_A_or_D():
    """Measured live in `rooB`. `D` shuffles the party along inside a room --
    Fran confirmed it by hand -- but never takes it out; leaving is a door
    interaction. And the save is area-granular, so the loop could not even see
    the movement it did cause. It hammered `D` twelve times regardless."""
    plan = plan_room(A(next_step="coBC"), None, area="rooB")
    assert not plan.actionable and plan.layer == "route"
    assert "door" in plan.refusal


def test_a_corridor_still_walks():
    from bridge.act import VK_D
    plan = plan_room(A(next_step="rooC"), None, area="coBC")
    assert plan.actionable and [s.vk for s in plan.steps] == [VK_D]


def test_walking_HOLDS_the_key_and_every_other_command_taps_it():
    """The last live failure, and the subtlest one. Movement is the only
    command in the game that is not discrete: DD walks the party for as long as
    A/D is held, so `KEY_HOLD`'s 60ms tap only nudges it. Fran saw exactly that
    ("pulso D y si se movio ligeramente el grupo") and the run still ended
    "still in coBC after 12 steps" -- twelve nudges is not a corridor.

    A skill digit, take-all and interact are genuine events, so holding them
    would at best waste a second and at worst auto-repeat into a second press.
    Both halves are pinned here."""
    import bridge.act as act
    walk = plan_room(A(next_step="rooC"), None, area="coBC").steps[0]
    assert walk.hold == act.WALK_HOLD > 0.5
    assert "0.9s" in str(walk)

    for plan in (plan_room(A(torch="relight"), None),
                 plan_room(A(curio_action="interact", curio_id="x"), None)):
        assert plan.steps[0].hold == 0.0, str(plan.steps[0])


def test_the_walk_loop_holds_too(monkeypatch, tmp_path):
    """`_walk_toward` builds its own steps rather than calling `plan_room`, so
    the constant has to reach both. It did not, the first time."""
    import bridge.act as act
    r = _walk_runner(monkeypatch, tmp_path, ["coBC"] * 40)
    r._walk_toward("rooC", "coBC")
    walks = [s for s in r.actuator.backend.sent if s.vk == act.VK_D]
    assert walks and all(s.hold == act.WALK_HOLD for s in walks)


def test_an_unknown_area_is_treated_as_a_corridor():
    """`area` is empty on a replayed capture that never published one. The
    corridor path is the one that was working, so it stays the default."""
    from bridge.act import VK_D
    plan = plan_room(A(next_step="coAB"), None, area="")
    assert plan.actionable and [s.vk for s in plan.steps] == [VK_D]


def test_a_bare_handed_curio_still_fires_inside_a_room():
    """The room refusal is about ROUTE. A curio in that room is still pressed
    -- W needs no coordinates and does not care where the party stands."""
    from bridge.act import VK_W
    plan = plan_room(A(curio_action="interact", curio_id="bookshelf",
                       next_step="coBC"), None, area="rooB")
    assert plan.layer == "curio" and [s.vk for s in plan.steps] == [VK_W]


def test_a_curio_never_blocks_the_route_step():
    """The bug behind "corridor movement does not work". Every curio branch
    used to return its refusal ahead of the route step, so a room with any
    curio in it -- even one advised SKIP -- never walked."""
    from bridge.act import VK_D
    # "use" left this list on 2026-09-05: the panel's item slot is measured
    # now, so a "use" curio is acted on rather than walked past. It takes
    # priority over the route for the same reason "interact" always did --
    # the walk leaves the area and the curio with it.
    for action in ("skip", "optional"):
        plan = plan_room(A(curio_action=action, curio_id="x",
                           curio_item="holy_water", next_step="coAB"), None)
        assert plan.actionable, f"{action} blocked the walk"
        assert plan.layer == "route"
        assert [s.vk for s in plan.steps] == [VK_D]


def test_a_curio_left_behind_says_so_in_the_plan():
    """Walking past a curio loses real loot, so it is named in the reason
    rather than dropped silently.

    The example moved from "use" to "optional" on 2026-09-05. "use" is acted
    on now; the summon altar is the curio that is still deliberately walked
    past, because PLAYBOOK 2.4 makes it a judgement call and the bridge does
    not make those.
    """
    plan = plan_room(A(curio_action="optional", curio_id="sacrificial_stone",
                       curio_item="torch", next_step="coAB"), None)
    assert "sacrificial_stone" in plan.why
    assert "LEAVING" in plan.why


def test_camping_does_not_block_the_route_either():
    from bridge.act import VK_D
    plan = plan_room(A(camp=True, next_step="coAB"), None)
    assert plan.actionable and plan.layer == "route"
    assert [s.vk for s in plan.steps] == [VK_D]


def test_an_unactionable_curio_with_nowhere_to_go_still_refuses():
    """Falling through must not turn a refusal into silence when there is no
    walk to fall through TO."""
    plan = plan_room(A(curio_action="optional", curio_id="sacrificial_stone",
                       curio_item="torch"), None)
    assert not plan.actionable and plan.layer == "curio"


def test_the_bare_handed_curio_beats_the_walk():
    """Ordering: do the curio, then walk on the next poll. Walking first would
    leave the area and lose it."""
    from bridge.act import VK_W
    plan = plan_room(A(curio_action="interact", curio_id="bookshelf",
                       next_step="coAB"), None)
    assert plan.layer == "curio" and [s.vk for s in plan.steps] == [VK_W]


def test_a_room_update_with_no_advice_does_nothing(monkeypatch):
    r = _runner(monkeypatch, armed=True, confirm=False)
    r.handle(_room_update(None))
    assert r.stats["acted"] == 0


# --- a full pack: clear the modal, free a slot, or stop ---
#
# Seen live: SPACE opened DD's "you don't have room to take all" modal, which
# changes nothing in the save -- so the generic retry read that as "the input
# did not land" and pressed SPACE again into a dialog already up. The pack was
# genuinely 16/16, which the save says outright.
#
# The recovery is OK -> discard -> SPACE. What is pinned below is mostly the
# refusals again, for the same asymmetry as the rest of this file and then
# some: a discard is the one input the bridge sends that DESTROYS something,
# and a raid cannot be undone.

def _slots(*specs):
    """A synthetic pack. Each spec is (type, id, amount), packed from slot 0."""
    from bridge.inventory import Slot
    return [Slot(slot=i, type=t, id=n, amount=a)
            for i, (t, n, a) in enumerate(specs)]


def _pack_of(n, *, chaff=0):
    """`n` occupied slots, `chaff` of them dog treats (the one free discard)."""
    out = [("supply", "dog_treats", 2)] * chaff
    filler = [("supply", "torch", 8), ("supply", "bandage", 6),
              ("gem", "ruby", 1), ("heirloom", "crest", 3),
              ("gold", "", 400), ("supply", "shovel", 4)]
    while len(out) < n:
        out.append(filler[len(out) % len(filler)])
    return _slots(*out[:n])


def _full_pack_runner(monkeypatch, tmp_path, slots, *, chaff=0,
                      full_pack="pause", pack=None):
    raid = tmp_path / "persist.raid.json"
    raid.write_bytes(b"a")
    r = _runner(monkeypatch, armed=True, confirm=False)
    r.raid_path = raid
    r.full_pack = full_pack
    state = {"pack": pack if pack is not None else _pack_of(slots, chaff=chaff)}
    monkeypatch.setattr(r, "_read_pack", lambda: state["pack"])
    # A party with no Houndmaster, which is what makes the dog treats in
    # `_pack_of` dead weight. With one present they are +15% ACC / +50% damage
    # and must never be discarded -- `test_inventory.py` pins that side.
    monkeypatch.setattr(r, "_party_classes", lambda: frozenset({"crusader"}))
    # **The party is three tiles in, i.e. clear of the door it arrived by.**
    # That is a PRECONDITION of the curio sweep, not scenery: `W` held at the
    # arrival gate walks the party back out of the area, so
    # `plan_hallway_sweep` refuses until the clearance is established. Stating
    # it here lets the curio tests be about the interaction; the clearance
    # itself has its own tests.
    monkeypatch.setattr(r, "_tiles_travelled", lambda: 3)
    return r, raid, state


def _clicks(r):
    return [s for s in r.actuator.backend.sent if s.kind == "click"]


def _keys(r, vk):
    return [s for s in r.actuator.backend.sent if s.kind == "key" and s.vk == vk]


def _hit_full_pack(r):
    """Drive one fight-then-room cycle, which is what opens the loot window.

    The battle turn's own key-and-click are dropped from the record first, so
    every assertion below is about the recovery and not about the fight that
    triggered it.
    """
    r.handle(_battle_update())
    r.actuator.backend.sent.clear()
    r.handle(_room_update(A(next_step="coIJ")))


def test_space_is_never_pressed_twice_into_the_modal(monkeypatch, tmp_path):
    """The bug that started all of this. One SPACE, then recovery -- never a
    blind retry, because the modal absorbs it and nothing changes."""
    import bridge.act as act
    r, _raid, _s = _full_pack_runner(monkeypatch, tmp_path, 16)
    _hit_full_pack(r)
    assert len(_keys(r, act.VK_SPACE)) == 1


def test_the_modal_is_dismissed_before_anything_else(monkeypatch, tmp_path):
    """OK first. The modal is modal -- until it goes, no other click lands."""
    from bridge.layout import MODAL_OK
    r, _raid, _s = _full_pack_runner(monkeypatch, tmp_path, 16, chaff=1)
    _hit_full_pack(r)
    clicks = _clicks(r)
    assert clicks, "nothing was clicked"
    assert clicks[0].screen == r.resolved.win.to_screen(*MODAL_OK)
    assert clicks[0].mods == ()


def _discarding_runner(monkeypatch, tmp_path, *, per_unit, chaff=1):
    """A runner whose shift-clicks really change the pack.

    `per_unit=False` models a shift-click that takes the whole stack;
    `per_unit=True` models one that takes a single unit, which is how the game
    documents the same gesture on the torch METER (`[SHIFT+CLICK] to reduce
    torch`). Nothing shipped says which one a PACK stack does, so both are
    pinned and the actuator has to work under either.
    """
    import bridge.act as act
    r, _raid, state = _full_pack_runner(monkeypatch, tmp_path, 16, chaff=chaff)
    real = r.actuator.execute

    def execute_and_apply(plan):
        ok = real(plan)
        if not any(act.VK_SHIFT in s.mods for s in plan.steps):
            return ok
        pack = state["pack"]
        head = pack[0]
        if per_unit and head.amount > 1:
            from bridge.inventory import Slot
            state["pack"] = [Slot(0, head.type, head.id, head.amount - 1)] + pack[1:]
        else:
            state["pack"] = _pack_of(len(pack) - 1)   # the slot really went
        return ok

    monkeypatch.setattr(r.actuator, "execute", execute_and_apply)
    return r, state


def test_a_whole_stack_discard_frees_the_slot_in_one_click(
        monkeypatch, tmp_path):
    """The one automatic case: dog treats, which the game prices at 0/0."""
    import bridge.act as act
    r, _state = _discarding_runner(monkeypatch, tmp_path, per_unit=False)
    _hit_full_pack(r)

    shift = [c for c in _clicks(r) if act.VK_SHIFT in c.mods]
    assert len(shift) == 1, "expected exactly one discard"
    assert shift[0].screen == r.resolved.inventory_point(0)
    assert r.stats["discarded"] == 1
    assert len(_keys(r, act.VK_SPACE)) == 2, "loot was not retried after freeing"


def test_a_per_unit_discard_clicks_until_the_slot_is_actually_empty(
        monkeypatch, tmp_path):
    """The regression this test exists for.

    An earlier check asked "is slot 0 still holding `dog_treats x2`". A
    per-unit discard passes that on the FIRST click -- x2 becomes x1, the
    label differs, the slot is still occupied -- so the runner would report
    the slot freed and press SPACE straight into a second modal. The success
    test is now the occupied-slot COUNT, which is the only thing that decides
    whether the loot fits.
    """
    import bridge.act as act
    r, _state = _discarding_runner(monkeypatch, tmp_path, per_unit=True)
    _hit_full_pack(r)

    shift = [c for c in _clicks(r) if act.VK_SHIFT in c.mods]
    assert len(shift) == 2, "a 2-unit stack needs two per-unit clicks"
    assert r.stats["discarded"] == 1
    assert len(_keys(r, act.VK_SPACE)) == 2, "loot was not retried after freeing"


def test_it_never_clicks_more_times_than_the_stack_is_deep(
        monkeypatch, tmp_path):
    """Bounded by `Discard.amount`. A pack that shrinks but never frees a slot
    must not spin -- and must not report success."""
    import bridge.act as act
    from bridge.inventory import Slot
    r, _raid, state = _full_pack_runner(monkeypatch, tmp_path, 16, chaff=1)
    real = r.actuator.execute

    def shrink_forever(plan):
        ok = real(plan)
        if any(act.VK_SHIFT in s.mods for s in plan.steps):
            head = state["pack"][0]
            state["pack"] = ([Slot(0, head.type, head.id, head.amount - 1)]
                             + state["pack"][1:])
        return ok

    monkeypatch.setattr(r.actuator, "execute", shrink_forever)
    _hit_full_pack(r)
    shift = [c for c in _clicks(r) if act.VK_SHIFT in c.mods]
    assert len(shift) <= 2, f"clicked {len(shift)}x on a 2-deep stack"
    assert r.stats["discarded"] == 0 and r.paused


def test_it_stops_if_another_item_slides_into_the_slot(monkeypatch, tmp_path):
    """Discarding may re-pack the slots. A second click at the same coordinate
    would then land on whatever moved into it -- which could be the ruby."""
    import bridge.act as act
    from bridge.inventory import Slot
    r, _raid, state = _full_pack_runner(monkeypatch, tmp_path, 16, chaff=1)
    real = r.actuator.execute

    def repack(plan):
        ok = real(plan)
        if any(act.VK_SHIFT in s.mods for s in plan.steps):
            # same slot count, but slot 0 now holds something valuable
            state["pack"] = ([Slot(0, "gem", "ruby", 1)]
                             + state["pack"][1:])
        return ok

    monkeypatch.setattr(r.actuator, "execute", repack)
    _hit_full_pack(r)
    shift = [c for c in _clicks(r) if act.VK_SHIFT in c.mods]
    assert len(shift) == 1, "clicked again after the slot changed identity"
    assert r.stats["discarded"] == 0 and r.paused


def test_a_discard_that_does_not_register_stops_the_run(monkeypatch, tmp_path):
    """No proof, no second discard. `_discard_slot` re-reads the pack rather
    than trusting the save's mtime, because the wrong thing changing is worse
    than nothing changing."""
    import bridge.act as act
    r, _raid, _s = _full_pack_runner(monkeypatch, tmp_path, 16, chaff=2)
    _hit_full_pack(r)                              # pack never changes
    shift = [c for c in _clicks(r) if act.VK_SHIFT in c.mods]
    assert len(shift) == 1, "shift-clicked again after the first did nothing"
    assert r.paused and r.stats["discarded"] == 0
    assert len(_keys(r, act.VK_SPACE)) == 1, "retried loot without making room"


def test_a_houndmasters_treats_are_never_shift_clicked(monkeypatch, tmp_path):
    """The live near-miss. Fran's party HAD a Houndmaster and the pack was
    16/16 with two dog-treat stacks, so the actuator was one working
    shift-click away from binning +15% ACC / +50% damage for three rounds.
    Nothing may be discarded when its class is in the party."""
    import bridge.act as act
    r, _raid, _s = _full_pack_runner(monkeypatch, tmp_path, 16, chaff=2)
    monkeypatch.setattr(r, "_party_classes", lambda: frozenset({"houndmaster"}))
    _hit_full_pack(r)
    assert not [c for c in _clicks(r) if act.VK_SHIFT in c.mods]
    assert r.stats["discarded"] == 0 and r.paused


def test_an_unreadable_party_never_discards(monkeypatch, tmp_path):
    """`None` from `_party_classes` means unknown, not empty. Treating it as
    empty would discard a class item on the strength of a failed roster read."""
    import bridge.act as act
    r, _raid, _s = _full_pack_runner(monkeypatch, tmp_path, 16, chaff=2)
    monkeypatch.setattr(r, "_party_classes", lambda: None)
    _hit_full_pack(r)
    assert not [c for c in _clicks(r) if act.VK_SHIFT in c.mods]
    assert r.paused


def test_nothing_worthless_means_pause_by_default(monkeypatch, tmp_path):
    """The common case: 19 of 25 full packs in the corpus look like this."""
    import bridge.act as act
    r, _raid, _s = _full_pack_runner(monkeypatch, tmp_path, 16)
    _hit_full_pack(r)
    assert r.paused and r.stats["paused"] == 1
    assert not [c for c in _clicks(r) if act.VK_SHIFT in c.mods]
    assert r.stats["abandoned"] == 0


def test_abandon_closes_the_loot_window_instead(monkeypatch, tmp_path):
    """Opt-in only. It leaves value behind and cannot say how much, because
    loot is not in the save until it is picked up."""
    from bridge.layout import LOOT_CLOSE, PARTY_ORDER
    r, _raid, _s = _full_pack_runner(monkeypatch, tmp_path, 16,
                                     full_pack="abandon")
    _hit_full_pack(r)
    seen = [c.screen for c in _clicks(r)]
    close = r.resolved.win.to_screen(*LOOT_CLOSE)
    assert close in seen, seen
    # The formation reset comes after, so this can no longer be the LAST click
    # -- but it must still be the click that ENDS the loot recovery.
    assert seen.index(close) < seen.index(r.resolved.win.to_screen(*PARTY_ORDER))
    assert r.stats["abandoned"] == 1
    assert not r.paused


def test_the_party_goes_back_in_order_after_a_fight(monkeypatch, tmp_path):
    """Combat reorders the party and nothing put it back. A Move swaps two
    heroes, and where they end up is where the NEXT fight starts -- a
    Highwayman parked at rank 1 opens it with `pistol_shot` (.launch 432) and
    `grape_shot_blast` (.launch 23) both greyed out. Reported live: "Didn't
    use the set order after the combat ... went stuck there as it tried to use
    a skill that cant be used in pos 1"."""
    from bridge.layout import PARTY_ORDER
    r, _raid, _s = _full_pack_runner(monkeypatch, tmp_path, 2,
                                     full_pack="abandon")
    _hit_full_pack(r)
    assert r.resolved.win.to_screen(*PARTY_ORDER) in [c.screen for c in _clicks(r)]


def test_the_tutorial_order_is_fixed_with_moves_when_the_button_is_inert(
        monkeypatch, tmp_path):
    """The button restores what the CAMPAIGN recorded at embark, and the
    tutorial never embarked -- its save ships mid-raid, so there is no default
    and the click does nothing. Reported live: "tutorial has no 'default'
    party order, so specifically during the tutorial it should move to restart
    the order, to do this we can use the move action outside of combat"."""
    import bridge.act as act
    r, _raid, _s = _full_pack_runner(monkeypatch, tmp_path, 2,
                                     full_pack="abandon")
    r.marching_order = ["crusader", "highwayman"]
    # The button is inert here: the order reads swapped before and after.
    monkeypatch.setattr(r, "_party_order", lambda: ["highwayman", "crusader"])
    r.actuator.backend.sent.clear()
    r._sort_into_marching_order(["highwayman", "crusader"])
    keys = [s.vk for s in r.actuator.backend.sent if s.kind == "key"]
    assert keys.count(act.VK_5) == 1, "never used the move action"
    clicks = [s.screen for s in r.actuator.backend.sent if s.kind == "click"]
    assert r.resolved.point("hero", 1) in clicks, "did not select the hero"
    assert r.resolved.point("hero", 0) in clicks, "did not name a destination"


def test_an_order_that_is_already_right_sends_nothing(monkeypatch, tmp_path):
    """The common case. A swap costs three inputs into a live dungeon."""
    r, _raid, _s = _full_pack_runner(monkeypatch, tmp_path, 2,
                                     full_pack="abandon")
    r.marching_order = ["crusader", "highwayman"]
    monkeypatch.setattr(r, "_party_order", lambda: ["crusader", "highwayman"])
    r.actuator.backend.sent.clear()
    r._sort_into_marching_order(["crusader", "highwayman"])
    assert _actions(r) == []


def test_an_unwatched_order_is_never_imposed(monkeypatch, tmp_path):
    """`marching_order` is WATCHED, not read -- the save has no field for the
    intended order. An order this runner never saw un-fought is not one it may
    rearrange the party into."""
    r, _raid, _s = _full_pack_runner(monkeypatch, tmp_path, 2,
                                     full_pack="abandon")
    assert r.marching_order == []
    monkeypatch.setattr(r, "_party_order", lambda: ["highwayman", "crusader"])
    r.actuator.backend.sent.clear()
    r._sort_into_marching_order(["highwayman", "crusader"])
    assert _actions(r) == []


def test_a_dead_hero_is_not_sorted_around(monkeypatch, tmp_path):
    """A party that lost someone cannot be put back in an order that names
    them, and shuffling the survivors toward it would be inventing a plan."""
    r, _raid, _s = _full_pack_runner(monkeypatch, tmp_path, 2,
                                     full_pack="abandon")
    r.marching_order = ["crusader", "highwayman", "vestal"]
    monkeypatch.setattr(r, "_party_order", lambda: ["highwayman", "crusader"])
    r.actuator.backend.sent.clear()
    r._sort_into_marching_order(["highwayman", "crusader"])
    assert _actions(r) == []


def test_the_party_order_is_not_reset_without_a_fight(monkeypatch, tmp_path):
    """It is the fight that disorders the party. Clicking it on every room
    block would send a click per poll into whatever else is on screen."""
    from bridge.layout import PARTY_ORDER
    r, _raid, _s = _full_pack_runner(monkeypatch, tmp_path, 2,
                                     full_pack="abandon")
    r.handle(_room_update(A(next_step="coIJ")))
    assert r.resolved.win.to_screen(*PARTY_ORDER) not in [c.screen for c in _clicks(r)]


def test_a_free_slot_does_not_mean_the_modal_is_absent(monkeypatch, tmp_path):
    """The bug Fran hit on the first real raid.

    He had ONE free slot and FOUR items on the floor. SPACE took one, the pack
    filled, and the modal came up about the other three -- while the save still
    read 15/16, because DD does not write a save when loot is picked up. The
    old code saw "not full", concluded "a screen the bridge does not model",
    and stranded the run behind a dialog it knew how to dismiss.

    `free_slots == 0` was never the trigger. `loot > free_slots` is, and the
    bridge cannot see either number.
    """
    from bridge.layout import MODAL_OK
    r, _raid, _s = _full_pack_runner(monkeypatch, tmp_path, 15)
    _hit_full_pack(r)
    clicks = _clicks(r)
    assert clicks, "a free slot suppressed the OK click"
    assert clicks[0].screen == r.resolved.win.to_screen(*MODAL_OK)


def test_an_unreadable_pack_still_dismisses_the_modal(monkeypatch, tmp_path):
    """OK first, questions after. The click is safe where no modal is up -- it
    lands on the dungeon backdrop -- and not sending it strands the run."""
    from bridge.layout import MODAL_OK
    r, _raid, state = _full_pack_runner(monkeypatch, tmp_path, 16)
    state["pack"] = None
    _hit_full_pack(r)
    assert r.paused
    assert [c.screen for c in _clicks(r)] == [
        r.resolved.win.to_screen(*MODAL_OK)]


def test_a_paused_runner_acts_on_nothing(monkeypatch, tmp_path):
    r, _raid, _s = _full_pack_runner(monkeypatch, tmp_path, 16)
    r.paused = True
    before = len(r.actuator.backend.sent)
    r.handle(_battle_update(turn_key=(9, 9, 9, 0)))
    r.handle(_room_update(A(next_step="coIJ")))
    assert len(r.actuator.backend.sent) == before


def test_it_resumes_once_the_pack_has_room(monkeypatch, tmp_path):
    """The human sorts the inventory; the bridge notices from the save."""
    r, _raid, state = _full_pack_runner(monkeypatch, tmp_path, 16)
    r.paused = True
    assert r._still_paused() is True
    state["pack"] = _pack_of(14)
    assert r._still_paused() is False
    assert r.paused is False


def test_an_unreadable_inventory_stays_paused(monkeypatch, tmp_path):
    """Unknown is not the same as fine. If the pack cannot be read, it must
    not resume acting on a guess."""
    r, _raid, state = _full_pack_runner(monkeypatch, tmp_path, 16)
    r.paused = True
    state["pack"] = None
    assert r._still_paused() is True


# --- the pack's screen geometry ---

def test_the_sixteen_slots_are_distinct_and_row_major(resolved):
    """Slot n in the save is cell n on screen, 8 across then 8 more.

    Verified against `captures/end_of_battle_rewards_1_item.png`, whose top row
    reads 2,2,6,12,6,6,8,3 -- exactly the save's slots 0-7 in order.
    """
    pts = [resolved.inventory_point(i) for i in range(16)]
    assert all(p is not None for p in pts)
    assert len(set(pts)) == 16
    top, bottom = pts[:8], pts[8:]
    assert [p[1] for p in top] == [top[0][1]] * 8, "top row is not level"
    assert [p[1] for p in bottom] == [bottom[0][1]] * 8
    assert bottom[0][1] > top[0][1], "row 2 must sit below row 1"
    assert [p[0] for p in top] == sorted(p[0] for p in top)
    assert [p[0] for p in top] == [p[0] for p in bottom], "columns misaligned"


def test_slot_points_land_inside_the_client_rect(resolved):
    win = resolved.win
    x0, y0 = win.client_origin
    w, h = win.client_size
    for i in range(16):
        x, y = resolved.inventory_point(i)
        assert x0 < x < x0 + w and y0 < y < y0 + h, f"slot {i} outside at {x},{y}"


def test_out_of_range_slots_have_no_point(resolved):
    assert resolved.inventory_point(-1) is None
    assert resolved.inventory_point(16) is None


def test_the_pack_geometry_scales_with_the_window():
    """A dragged or resized window must not offset the grid -- the same
    property that makes the rank points work in windowed mode."""
    from bridge.act import OfflineWindow
    lay = load_layout()
    small = lay.resolve(OfflineWindow(size=(1280, 720), origin=(0, 0)))
    big = lay.resolve(OfflineWindow(size=(2560, 1440), origin=(0, 0)))
    for i in range(16):
        sx, sy = small.inventory_point(i)
        bx, by = big.inventory_point(i)
        assert abs(bx - 2 * sx) <= 1 and abs(by - 2 * sy) <= 1


# --- modifiers reach every backend ---

def test_every_backend_accepts_held_modifiers():
    """`ScanCodeBackend` was already broken once by a method landing in the
    wrong class. A SHIFT that silently vanishes turns a discard into a plain
    click, which SELECTS the item instead of destroying it -- a quiet no-op
    that would read as 'the click missed'."""
    import inspect
    import bridge.act as act
    for cls in (act.Backend, act.DryRunBackend, act.PostMessageBackend,
                act.SendInputBackend, act.ScanCodeBackend):
        sig = inspect.signature(cls.click)
        assert "mods" in sig.parameters, f"{cls.__name__}.click drops modifiers"


def test_the_dry_run_backend_records_the_modifier():
    import bridge.act as act
    b = act.DryRunBackend()
    b.click((10, 20), (act.VK_SHIFT,))
    assert b.sent[-1].mods == (act.VK_SHIFT,)


def test_the_torch_meter_is_nowhere_near_the_pack(resolved):
    """SHIFT-CLICK is overloaded: on a pack slot it discards, on the torch
    meter it lowers the light (`str_reduce_torch_tip`). They must not be close
    enough for a mis-resolved window to turn one into the other."""
    from bridge.layout import TORCH_CANDIDATES

    # EVERY candidate, not just the first: `_snuff_torch` sweeps the list, so
    # any of them can receive a SHIFT-click, and any of them landing on a pack
    # slot would DISCARD something instead of dimming the light.
    for _name, frac in TORCH_CANDIDATES:
        _tx, ty = resolved.at(frac)
        for i in range(16):
            _sx, sy = resolved.inventory_point(i)
            assert sy - ty > resolved.win.client_size[1] * 0.5


def test_nothing_plans_a_torch_meter_click_yet(resolved):
    """The coordinate exists; no planner uses it. `run.py` calls the torch
    layer with `about_to_win=state.in_battle`, so out of combat there is never
    a shutdown to act on -- and a plan that clicked here anyway would be
    lowering the light nobody asked to lower."""
    plan = plan_room(A(torch="shutdown"), None)
    assert not plan.actionable
    assert plan.layer == "torch"


def test_a_modified_click_says_so_when_printed():
    """The plan is what the operator approves in supervised mode, so a discard
    must not print as an ordinary click."""
    import bridge.act as act
    s = act.Step("click", screen=(4, 5), mods=(act.VK_SHIFT,), label="discard")
    assert "SHIFT+" in str(s)


# ── absolute mouse coordinates span the VIRTUAL desktop ──


def _sent_mouse_flags(monkeypatch, call):
    """Run `call` with SendInput stubbed, and return the flags of each event.

    The real call is the only place the bug could live, so it is the only
    thing worth intercepting: everything above it deals in screen pixels and
    was always right.
    """
    import bridge.act as act

    seen = []

    def fake_send(n, ref, size):
        seen.append(int(ref._obj.u.mi.dwFlags))
        return n

    monkeypatch.setattr(act.user32, "SendInput", fake_send)
    call(act)
    return seen


def test_every_absolute_mouse_event_declares_the_virtual_desktop(monkeypatch):
    """MEASURED BUG, and invisible on a one-monitor machine.

    `_normalise` maps a screen pixel over the VIRTUAL desktop, which is right.
    But Windows only interprets the 0..65535 range that way when the event
    carries `MOUSEEVENTF_VIRTUALDESK`; without it the range spans the PRIMARY
    monitor whatever the caller intended.

    On a 3840x1080 desktop with a 1920x1080 primary that halves every x: asked
    for (732,744), the cursor landed at (366,743) -- y exact, because the
    virtual and primary HEIGHTS happen to match, which is the asymmetry that
    identified it. Every click the bridge sent missed, while the keyboard
    worked perfectly, so it read as "DD ignores synthetic clicks".

    On a single-monitor machine the two interpretations coincide and this test
    is the only thing standing between that and a silent regression.
    """
    class _Win:
        hwnd = 1

        def to_client(self, pt):
            return pt

    flags = _sent_mouse_flags(
        monkeypatch, lambda act: act.SendInputBackend(_Win()).click((10, 20)))
    assert flags, "no mouse events were sent at all"
    for f in flags:
        import bridge.act as act
        assert f & act.MOUSEEVENTF_ABSOLUTE, "absolute positioning was dropped"
        assert f & act.MOUSEEVENTF_VIRTUALDESK, (
            "an absolute mouse event did not declare the virtual desktop; on a "
            "multi-monitor machine this lands every click on the wrong pixel")


def test_a_move_declares_it_too(monkeypatch):
    """`move` is the path the mouse probe uses to prove the coordinate maths,
    so a move that is wrong makes the probe lie about the click."""
    class _Win:
        hwnd = 1

        def to_client(self, pt):
            return pt

    flags = _sent_mouse_flags(
        monkeypatch, lambda act: act.SendInputBackend(_Win()).move((10, 20)))
    import bridge.act as act
    assert flags and all(f & act.MOUSEEVENTF_VIRTUALDESK for f in flags)


# ── the absolute mouse mapping, over monitor layouts this machine has not got ──

# `(name, origin_x, origin_y, width, height)`. The point of the list is the
# ones that are NOT this machine: a second monitor to the LEFT or ABOVE makes
# the virtual origin negative, and `SM_XVIRTUALSCREEN` is the only thing that
# says so. Getting that wrong puts every click on the wrong monitor, and no
# test on a single-monitor developer machine would ever notice.
LAYOUTS = [
    ("single 1080p",            0,     0, 1920, 1080),
    ("dual side by side",       0,     0, 3840, 1080),
    ("second monitor LEFT",  -1920,    0, 3840, 1080),
    ("second monitor ABOVE",     0, -1080, 1920, 2160),
    ("above and left",       -2560, -1440, 4480, 2520),
    ("portrait",                 0,     0, 1080, 1920),
    ("4K",                       0,     0, 3840, 2160),
    ("three across 8K",      -3840,    0, 11520, 2160),
    ("one pixel",                0,     0,    1,    1),
    ("tall thin",             -100,  -100,    3, 4000),
]


@pytest.mark.parametrize("name,ox,oy,w,h", LAYOUTS)
def test_the_normalised_range_is_never_left(name, ox, oy, w, h):
    """`SendInput` takes 0..65535 and silently WRAPS anything else.

    A caller can legitimately ask for a point off the desktop -- a window
    dragged half off-screen -- so the clamp is what stops that becoming a
    click on the far side of the map.
    """
    import bridge.act as act
    probes = [ox - 10_000, ox - 1, ox, ox + w // 2, ox + w - 1, ox + w + 10_000]
    for v in probes:
        assert 0 <= act._norm_axis(v, ox, w) <= 65535, (name, v)
    for v in [oy - 10_000, oy, oy + h - 1, oy + h + 10_000]:
        assert 0 <= act._norm_axis(v, oy, h) <= 65535, (name, v)


@pytest.mark.parametrize("name,ox,oy,w,h", LAYOUTS)
def test_the_mapping_is_monotonic_and_spans_the_range(name, ox, oy, w, h):
    """Two properties that together say it is a real mapping of the desktop.

    Monotonic, so a point further right never normalises to a smaller value --
    the failure that would make clicks land in mirror image. And the two ends
    reach the two ends of the range, so the whole desktop is addressable
    rather than a band in the middle of it.
    """
    import bridge.act as act
    xs = [ox + int(w * f) for f in (0, 0.1, 0.25, 0.5, 0.75, 0.9)] + [ox + w - 1]
    got = [act._norm_axis(v, ox, w) for v in xs]
    assert got == sorted(got), (name, xs, got)
    # The bound is ONE PIXEL's worth of the range, not a round number: a
    # pixel is a cell and `_norm_axis` aims at its centre, so on a 3-wide
    # span the first pixel legitimately sits a sixth of the way in. A fixed
    # threshold here fails the narrow layouts for being narrow.
    one_px = 65535.0 / max(1, w)
    assert act._norm_axis(ox, ox, w) <= one_px, (name, one_px)
    assert act._norm_axis(ox + w - 1, ox, w) >= 65535 - one_px, (name, one_px)


@pytest.mark.parametrize("name,ox,oy,w,h", LAYOUTS)
def test_a_round_trip_stays_within_a_pixel(name, ox, oy, w, h):
    """Normalise, map back the way Windows does, and land on the same pixel.

    The inverse used here is the nominal `n * span / 65536`. It is NOT exactly
    what Windows does -- probed on a real desktop, some pixels are not
    addressable at all (normalised 14654 gives y=240 and 14655 gives y=242,
    repeatably, with no cursor clip and no DPI scaling) -- which is why
    `SendInputBackend.move` reads the cursor back and corrects rather than
    trusting arithmetic.

    So this pins the arithmetic to within the ONE pixel that the hardware
    itself costs, and the live correction handles the rest.
    """
    import bridge.act as act
    if w <= 1:
        # A one-pixel span has no round trip to check -- every point IS the
        # only point. What still has to hold is that it stays in range rather
        # than dividing by `w - 1`, which is the arithmetic that would raise.
        assert 0 <= act._norm_axis(ox, ox, w) <= 65535
        return
    for f in (0.0, 0.01, 0.3, 0.5, 0.7, 0.99):
        want = ox + int((w - 1) * f)
        n = act._norm_axis(want, ox, w)
        back = ox + int(n * w / 65536)
        assert abs(back - want) <= 1, (name, want, n, back)


def test_a_degenerate_span_cannot_divide_by_zero():
    """`GetSystemMetrics` returns 0 for a span it cannot determine -- a
    display being reconfigured, or a session with no desktop at all. The
    fallback keeps the arithmetic defined; the caller's own checks are what
    decide whether to send anything."""
    import bridge.act as act
    assert 0 <= act._norm_axis(5, 0, 0) <= 65535


def test_negative_origins_are_subtracted_not_assumed_zero():
    """The specific regression: a monitor left of the primary.

    With the origin ignored, a point on the LEFT monitor normalises negative,
    clamps to 0, and every click on that monitor lands on its far edge. Here
    the left monitor's midpoint must land near the middle of the range.
    """
    import bridge.act as act
    ox, w = -1920, 3840
    left_middle = -960                       # centre of the left monitor
    n = act._norm_axis(left_middle, ox, w)
    assert 15000 < n < 17500, n
    assert act._norm_axis(0, ox, w) == pytest.approx(32768, abs=200)


def test_normalise_reads_the_metrics_fresh_every_call(monkeypatch):
    """A monitor plugged in mid-run must not need a restart.

    The geometry is read per call rather than cached at construction, so this
    changes the answer by changing only the metrics.
    """
    import bridge.act as act

    class _Win:
        hwnd = 1

        def to_client(self, pt):
            return pt

    span = {"w": 1920}
    metrics = {act.SM_XVIRTUALSCREEN: 0, act.SM_YVIRTUALSCREEN: 0,
               act.SM_CYVIRTUALSCREEN: 1080}
    monkeypatch.setattr(
        act.user32, "GetSystemMetrics",
        lambda m: span["w"] if m == act.SM_CXVIRTUALSCREEN else metrics[m])

    backend = act.SendInputBackend(_Win())
    narrow = backend._normalise((960, 540))
    span["w"] = 3840
    wide = backend._normalise((960, 540))
    assert wide[0] < narrow[0], "the wider desktop must map 960 further left"
    assert wide[1] == narrow[1], "the height did not change"


# ── click-and-drag ──


class _DragWin:
    hwnd = 1

    def to_client(self, pt):
        return pt


def _drag_flags(monkeypatch, src=(100, 100), dst=(400, 300), mods=()):
    """Every mouse event a drag sends, in order, as flag words."""
    import bridge.act as act

    seen = []

    def fake_send(n, ref, size):
        seen.append(int(ref._obj.u.mi.dwFlags))
        return n

    monkeypatch.setattr(act.user32, "SendInput", fake_send)
    ok = act.SendInputBackend(_DragWin()).drag(src, dst, mods)
    return ok, seen


def test_a_drag_presses_then_MOVES_then_releases(monkeypatch):
    """**The motion between press and release IS the mechanic.**

    SDL decides a drag happened from the motion events in between. A press,
    one jump and a release is commonly read as a plain click on the source --
    which in DD's roster panels means "select this hero" rather than "move
    this hero", a wrong action that looks like a successful drag in the log.

    So this pins the ORDER and the presence of real travel, not just that
    three events were sent.
    """
    import bridge.act as act
    ok, flags = _drag_flags(monkeypatch)
    assert ok

    downs = [i for i, f in enumerate(flags) if f & act.MOUSEEVENTF_LEFTDOWN]
    ups = [i for i, f in enumerate(flags) if f & act.MOUSEEVENTF_LEFTUP]
    assert len(downs) == 1, f"exactly one press, got {len(downs)}"
    assert len(ups) == 1, f"exactly one release, got {len(ups)}"
    assert downs[0] < ups[0], "released before it pressed"

    between = [f for f in flags[downs[0] + 1:ups[0]]
               if not (f & (act.MOUSEEVENTF_LEFTDOWN | act.MOUSEEVENTF_LEFTUP))]
    assert len(between) >= 2, (
        f"only {len(between)} motion events inside the drag; SDL will read "
        "this as a click on the source")


def test_every_event_of_a_drag_declares_the_virtual_desktop(monkeypatch):
    """The same rule the click path carries, and for the same reason: on a
    multi-monitor desktop an absolute event without it lands at a fraction of
    its intended x. A drag that starts correctly and travels wrong is worse
    than a click that misses, because it drops something somewhere real."""
    import bridge.act as act
    ok, flags = _drag_flags(monkeypatch)
    assert ok and flags
    for f in flags:
        assert f & act.MOUSEEVENTF_ABSOLUTE, "absolute positioning was dropped"
        assert f & act.MOUSEEVENTF_VIRTUALDESK, (
            "a drag event did not declare the virtual desktop")


def test_a_zero_length_drag_is_refused_rather_than_sent(monkeypatch):
    """It is always a caller bug -- a source and destination that resolved to
    the same slot. SENT, it is an ordinary click on that slot: a different
    action that succeeds, and therefore reads as a working drag in the log.
    Refusing makes the bug visible where it happens."""
    import bridge.act as act
    ok, flags = _drag_flags(monkeypatch, src=(250, 250), dst=(250, 250))
    assert ok is False
    assert not any(f & act.MOUSEEVENTF_LEFTDOWN for f in flags), (
        "a zero-length drag pressed the button")


def test_a_drag_releases_its_modifiers_even_when_the_send_fails(monkeypatch):
    """The `Backend` docstring's rule, which exists because SHIFT-CLICK is the
    one input that destroys something: a modifier left stuck down turns the
    operator's next real click into a discard. A drag holds them for longer
    than a click does, so it matters more here."""
    import bridge.act as act

    released = []
    backend = act.SendInputBackend(_DragWin())
    # The move has to SUCCEED, or the modifiers are never pressed and there is
    # nothing to release -- which is correct behaviour and would make this
    # test pass without exercising anything. The failure is injected at the
    # button events, which is where a real one lands.
    monkeypatch.setattr(backend, "move", lambda pt: True)
    monkeypatch.setattr(backend, "_send_abs",
                        lambda *a, **k: False)
    monkeypatch.setattr(backend, "_mods",
                        lambda mods, down: released.append(down))
    assert backend.drag((10, 10), (90, 90), (act.VK_SHIFT,)) is False
    assert released == [True, False], (
        f"modifiers were not released after a failed send: {released}")


def test_the_actuator_dispatches_a_drag_step():
    """`Actuator.execute` was a two-way ternary over "key" and "click". A
    third kind that fell through to `click` would drop the destination
    silently and press the source instead."""
    import bridge.act as act
    backend = act.DryRunBackend()
    actuator = act.Actuator(backend=backend, enabled=True)
    plan = act.InputPlan(
        (act.Step("drag", screen=(1, 2), to=(3, 4), label="recruit"),),
        "town", "take the recruit")
    assert actuator.execute(plan)
    assert len(backend.sent) == 1
    got = backend.sent[0]
    assert got.kind == "drag"
    assert got.screen == (1, 2) and got.to == (3, 4)


def test_a_drag_prints_both_ends():
    """A plan is what the operator approves in supervised mode, so a drag must
    not print like a click on its source."""
    import bridge.act as act
    line = str(act.Step("drag", screen=(1, 2), to=(3, 4), label="hero->party"))
    assert line.startswith("DRAG")
    assert "1,2" in line.replace(" ", "") and "3,4" in line.replace(" ", "")


def test_post_message_cannot_drag():
    """Its CLICK does not register in DD -- measured, and the reason
    `DEFAULT_BACKEND` is send-input -- so a drag cannot either. It must report
    that rather than appear to work."""
    import bridge.act as act
    assert act.PostMessageBackend.drag is act.Backend.drag


# ── a layer that cannot act must not block one that can ──────────────────
#
# `plan_room` states this rule in its own docstring, and it was learned from
# the curio branch stranding the party. The TORCH branch kept returning, and
# on 2026-08-31 it stalled a live raid for 180 s: `KEY T` reported "the input
# did not land" three times in rooH, the actuator gave up on the turn as
# designed, and then -- because `plan_room` checks the torch FIRST -- every
# following poll re-planned the same relight and the route step was never
# reached. The party stood still until the watchdog killed the iteration.

def test_striking_the_torch_out_lets_the_route_through(resolved):
    """The repair, stated as the behaviour it restores."""
    from bridge.act import _torch_done

    advice = A(next_step="corH", torch="relight")
    update = U(room_advice=advice)

    blocked = plan_room(advice, None, area="rooH")
    assert blocked.layer == "torch", "the torch wins while it is still set"

    after = _torch_done(update).room_advice
    assert after.torch == ""
    assert after.next_step == "corH", "the rest of the advice survives"
    assert plan_room(after, None, area="rooH").layer == "route"


def test_striking_the_torch_out_does_not_disturb_the_curio(resolved):
    """The two proxies share an implementation now, so each must blank only
    its own field -- a shared `_BLANK` table is exactly where that goes
    wrong."""
    from bridge.act import _curio_done, _torch_done

    advice = A(next_step="corH", torch="relight",
               curio_action="interact", curio_id="sconce")
    update = U(room_advice=advice)

    no_torch = _torch_done(update).room_advice
    assert no_torch.torch == "" and no_torch.curio_action == "interact"

    no_curio = _curio_done(update).room_advice
    assert no_curio.curio_action == "" and no_curio.torch == "relight"


def test_an_advice_with_everything_struck_out_reads_EMPTY(resolved):
    """`empty` is what `plan_room` uses to decide there is nothing left here,
    so it has to be recomputed over what REMAINS. Reading it off the
    underlying advice would say "still busy" forever, which is the stall in a
    different costume."""
    from bridge.act import _curio_done, _torch_done

    advice = A(next_step="", torch="relight", curio_action="interact")
    once = _torch_done(U(room_advice=advice))
    assert not once.room_advice.empty, "the curio is still to do"
    twice = _curio_done(once)
    assert twice.room_advice.empty


def test_the_curio_nudge_is_FINER_than_a_walk_press():
    """The interaction window is narrower than one walk press.

    Measured off `captures/_curio_approach.png`: the front hero's right edge
    is about 650 px and the tutorial tent starts about 690 px at 1280 wide, so
    the party is ~40 px short -- while `WALK_HOLD` covers well over a hundred.
    A nudge of `WALK_HOLD` therefore steps clean OVER the window, which is what
    the live run did: three nudges went from "40 px short" to "in the fight
    beyond the tent", pressing `W` everywhere except where it would have
    worked.

    So this is not a tunable to tidy up -- collapsing the nudge back onto
    `WALK_HOLD` restores the bug. Fran's rule for what right looks like: *"the
    curio needs to be a bit to the right of the party, you cant interact with
    curio on top of them."*
    """
    from bridge.act import CURIO_NUDGE_HOLD, CURIO_TRIES, WALK_HOLD

    assert CURIO_NUDGE_HOLD < WALK_HOLD / 2, "a nudge must resolve the window"
    # ...and the search must still cover comparable ground, or a curio the
    # party stops well short of is never reached at all.
    assert CURIO_NUDGE_HOLD * CURIO_TRIES >= WALK_HOLD * 2


# ── the hallway sweep: D and W held together ─────────────────────────────
#
# Fran's technique, and it removes a problem rather than solving it:
#
#   "you can keep pressing both D+W at the same time and interact with things
#    by walking, but take care to only do that after separating from the
#    initial door, if not we'll loop again."
#
# Holding interact while walking means nothing has to compute a stopping
# distance -- and the bridge's tile arithmetic was measured 124 px short of a
# position Fran set by hand, after three separate stopping theories had
# already been falsified.
#
# UNFLOWN at the time of writing. These pin the shape, not the outcome.

def _walk(passed, total=6, from_front=True):
    from bridge.dungeon_map import Walk
    return Walk(passed=passed, total=total, from_front=from_front)


def test_the_sweep_holds_the_walk_and_the_interact_TOGETHER():
    from bridge.act import VK_D, VK_W, plan_hallway_sweep

    plan = plan_hallway_sweep("coAB", _walk(3))
    assert plan.actionable
    assert len(plan.steps) == 1
    step = plan.steps[0]
    assert step.kind == "keys"
    assert step.vks == (VK_D, VK_W), "walk first, interact second"
    assert step.hold > 0, "a sweep is a hold, not a tap"


@pytest.mark.parametrize("from_front", [True, False])
def test_the_sweep_REFUSES_at_the_door(from_front):
    """The whole safety argument.

    A hallway is entered at its LEFT and left at its RIGHT, so the party
    arrives standing at the door it came through and `W` there walks it
    straight back out. `_interact_curio`'s docstring records exactly that from
    a live run, and a `W`-at-the-door fallback written the same day was
    reverted unflown for it.
    """
    from bridge.act import HALLWAY_DOOR_CLEARANCE, plan_hallway_sweep

    at_the_door = plan_hallway_sweep("coAB", _walk(1, from_front=from_front))
    assert not at_the_door.actionable
    assert at_the_door.steps == (), "a refusal sends nothing"
    assert "door" in at_the_door.refusal

    clear = plan_hallway_sweep("coAB",
                               _walk(1 + HALLWAY_DOOR_CLEARANCE,
                                     from_front=from_front))
    assert clear.actionable


def test_clearance_counts_the_same_from_EITHER_end():
    """`passed - 1` is the tiles travelled whichever end the party came in at.
    Deriving it from `index` instead needs a branch that is easy to get
    backwards -- and getting it backwards means sweeping at the door from one
    direction only, which is a bug that would show up half the time."""
    from bridge.act import plan_hallway_sweep

    for passed in (1, 2, 3):
        front = plan_hallway_sweep("coAB", _walk(passed, from_front=True))
        back = plan_hallway_sweep("coAB", _walk(passed, from_front=False))
        assert front.actionable == back.actionable, passed


def test_the_sweep_is_D_whichever_end_the_corridor_was_entered_by():
    """A sweep only ever goes DEEPER, and deeper is `D` in every hallway. It
    used to read `A` off `Walk.heading` for a party that entered at the far
    end, which walked it back through the door it had just come in by -- the
    exact re-entry the clearance check two lines up exists to prevent."""
    from bridge.act import VK_D, plan_hallway_sweep

    for from_front in (True, False):
        got = plan_hallway_sweep("coAB", _walk(3, from_front=from_front))
        assert got.steps[0].vks[0] == VK_D, from_front


def test_the_sweep_refuses_a_room_and_an_unknown_position():
    """A room is one tile, so there is nothing to sweep. And an unreadable
    position is exactly when holding an interact key is most dangerous."""
    from bridge.act import plan_hallway_sweep

    assert not plan_hallway_sweep("rooA", _walk(3)).actionable
    assert not plan_hallway_sweep("coAB", None).actionable


def test_the_keys_step_sends_all_down_then_all_up_in_REVERSE():
    """Releasing the movement first would leave an interact pressed against a
    party that has stopped -- the door-reentry case in miniature."""
    from bridge.act import (Actuator, DryRunBackend, VK_D, VK_W,
                            plan_hallway_sweep)

    backend = DryRunBackend()
    act = Actuator(backend=backend, enabled=True)
    act.execute(plan_hallway_sweep("coAB", _walk(3)))
    assert len(backend.sent) == 1
    assert backend.sent[0].kind == "keys"
    assert backend.sent[0].vks == (VK_D, VK_W)


# -- the torch snuff: "snuff, then swing" ---------------------------------
#
# Fran's tutorial note, and the reason `layout.TORCH_METER` was banked and
# left unwired for weeks: snuffing the torch at the moment the killing blow
# lands gets much better loot. `torchlight.py` has decided this correctly all
# along and `run.py::_battle_torch` gates it on `about_to_win`; nothing
# carried the verdict to the actuator.

def test_the_snuff_holds_BOTH_modifiers(resolved):
    """`str_snuff_torch_tip` is SHIFT+CTRL+CLICK. SHIFT alone on the SAME
    widget only reduces the torch, so dropping a modifier turns a snuff into a
    partial dim and the loot bonus is lost quietly."""
    from bridge.act import VK_CONTROL, VK_SHIFT, plan_snuff_torch

    plan = plan_snuff_torch(resolved)
    assert plan.actionable
    assert len(plan.steps) == 1
    step = plan.steps[0]
    assert step.kind == "click"
    assert set(step.mods) == {VK_SHIFT, VK_CONTROL}
    assert step.screen != (0, 0)


def test_the_snuff_goes_FIRST_and_the_turn_still_happens(resolved):
    """Order and completeness, both load-bearing.

    After the blow is too late -- the loot roll is taken on the kill. And a
    snuff that REPLACED the attack would forfeit the very kill it exists to
    sweeten.
    """
    plain = plan_battle(U(skill_slot=2, side="enemy", rank=0), resolved)
    snuffed = plan_battle(U(skill_slot=2, side="enemy", rank=0,
                            snuff_torch=True), resolved)

    assert len(snuffed.steps) == len(plain.steps) + 1
    assert snuffed.steps[0].kind == "click" and snuffed.steps[0].mods
    assert snuffed.steps[1:] == plain.steps, "the turn is untouched"


def test_no_snuff_unless_it_is_asked_for(resolved):
    """Every turn that is NOT the killing blow must be byte-identical to
    before -- darkness on those turns is pure cost."""
    for slot in (1, 2, MOVE_SLOT, PASS_SLOT):
        a = plan_battle(U(skill_slot=slot, side="hero", rank=1), resolved)
        b = plan_battle(U(skill_slot=slot, side="hero", rank=1,
                          snuff_torch=False), resolved)
        assert a.steps == b.steps


def test_a_TIMING_refusal_is_not_rescued_by_the_snuff(resolved):
    """A provisional turn must send nothing at all -- least of all a snuff,
    which is irreversible for the rest of the fight."""
    plan = plan_battle(U(skill_slot=2, side="enemy", rank=0,
                         snuff_torch=True, provisional=True), resolved)
    assert not plan.actionable
    assert plan.steps == ()


def test_the_snuff_rides_the_FALLBACK_too(resolved):
    """The killing blow can be played by the second-choice action -- the top
    pick having no button does not make it a different turn."""
    plan = plan_battle(
        U(skill_slot=-1, side="enemy", rank=0, snuff_torch=True,
          alternatives=(Alt(skill_slot=2, side="enemy", rank=1),)),
        resolved)
    assert plan.actionable
    assert plan.steps[0].kind == "click" and plan.steps[0].mods
    assert any(s.kind == "key" for s in plan.steps)


def test_the_pending_snuff_is_consumed_after_ONE_turn():
    """A flag left set would snuff every turn after the first, and those are
    precisely the turns where darkness is a straight loss."""
    from bridge.run import Session

    s = Session.__new__(Session)
    s._snuff_next_turn = True
    assert s._take_snuff() is True
    assert s._take_snuff() is False


# -- the tile order is not a screen direction -----------------------------
#
# Fran, 2026-09-02: "The model is randomly using A instead of D" and
# "Map/Hallway management its bugged". `Walk.heading` read the walk direction
# off the tile ORDER, and the measurement that was taken to justify a
# CORRECTOR for it actually refutes the whole idea: over the capture corpus
# the tile order is monotonic in `mappos` but runs BOTH WAYS -- 25 corridors
# ascending, 23 descending, and 23 of the 48 VERTICAL and therefore carrying
# no screen left/right at all.
#
# A quantity that is right half the time and undefined for half the corpus is
# not a noisy measurement of the walk direction. It is not a measurement of it.
# Fran, 2026-09-03: "hallways have no directions, only left (backtrack) and
# right (advance)" -- DD draws the party entering at the LEFT of the hallway
# art however the corridor runs on the map, so `D` is deeper and `A` is back
# out the door, always. `plan_hallway_sweep` had his words for it
# ("a hallway is entered at its LEFT and left at its RIGHT") a hundred lines
# from the property that contradicted them.
#
# What made the wrong reading expensive is that a BACKWARD STEP IS INVISIBLE.
# Per-tile `knowledge` and `light` are effectively monotonic -- 108 growth
# events across the corpus against 2 that ever shrank -- so walking the wrong
# way writes nothing and reads exactly like walking into a wall. That is why
# four separate mechanisms grew to correct a direction that was never in
# doubt: a global `Heading` bit, a per-corridor "the save was wrong here" set,
# a stall-driven flip, and a one-step "is the gap growing" check.




def test_a_route_walk_that_gets_nowhere_STOPS(monkeypatch, tmp_path):
    """The same on the route side. The budget used to be burned twice over --
    forward, then flipped and back again -- and on the Darkest Dungeon that is
    Fran's "not useful to walk backwards because of enhanced stress"."""
    import bridge.act as act
    r = _walk_runner(monkeypatch, tmp_path, ["coBC"] * 200)
    said = []
    monkeypatch.setattr(r, "_say", lambda m: said.append(m))
    monkeypatch.setattr(r, "_walk_progress", lambda a: _W(2, from_front=True))
    r._walk_toward("rooC", "coBC")
    sent = [s.vk for s in r.actuator.backend.sent]
    # It stops WALKING at the stall. What follows is the door, whose back-offs
    # are BOUNDED by DOOR_TRIES -- what must not come back is a walk that
    # keeps stepping on its own, in either direction.
    assert sent.count(act.VK_D) == act.WALK_STALL_LIMIT
    assert sent.count(act.VK_A) <= act.DOOR_TRIES - 1
    assert any("moved nothing" in m for m in said), said


def test_the_direction_survives_a_walk_that_ends_in_the_wrong_area(
        monkeypatch, tmp_path):
    """A corridor cannot falsify a direction that is not a hypothesis.

    Landing in `rooB` when the route asked for `rooC` used to flip a global
    bit AND mark the corridor, so the next visit walked it the other way. It
    means the ROUTE was wrong, or the map graph is; the keyboard is not in
    question, and reversing on it is how the party ended up crossing `coEF`
    twice.
    """
    import bridge.act as act
    r = _walk_runner(monkeypatch, tmp_path, ["coBC", "rooB"])
    monkeypatch.setattr(r, "_walk_progress", _walking(2, from_front=True))
    r._walk_toward("rooC", "coBC")                 # ends up in rooB, not rooC
    assert r._forward_key("coBC") == act.VK_D


# -- the snuff is verified against the save now ---------------------------
#
# Fran, live, 2026-09-02: "Torch manipulation is not working properly, its not
# using ctrl+shift+click on the same time so it just lowers 25 torch with shift
# click each click". That is `str_reduce_torch_tip`'s gesture, i.e. the CTRL
# was not reaching the game and the snuff was a quarter of a snuff -- while the
# log said the torch was out.


def _down(step) -> tuple:
    """Every key a step puts DOWN -- plain, or held as part of a combo.

    The curio approach sweeps now: `Step("keys", vks=(VK_D, VK_W))` holds both
    at once, which is Fran's technique and the reason nothing has to compute
    where to stop. Counting `s.vk` alone stopped seeing the `W` in that, so
    every "did it press W" assertion here reads through this instead.
    """
    return tuple(step.vks) if getattr(step, "vks", ()) else (step.vk,)


def _torch_runner(monkeypatch, tmp_path, levels):
    """A runner whose `_peek` walks a scripted torch sequence."""
    import bridge.act as act
    r, _raid, _s = _full_pack_runner(monkeypatch, tmp_path, 15,
                                     full_pack="abandon")
    seq = list(levels)
    seen = []

    def peek(_self=None):
        value = seq[0] if len(seq) == 1 else (seq.pop(0) if seq else 0.0)
        seen.append(value)
        return {"in_battle": True, "area": "coAB", "torch": value}
    monkeypatch.setattr(r, "_peek", peek)
    monkeypatch.setattr(act.time, "sleep", lambda *_a, **_k: None)
    return r, seen


def _torch_clicks(runner):
    import bridge.act as act
    return [s for s in runner.actuator.backend.sent if s.kind == "click"]


def test_a_snuff_that_lands_sends_exactly_one_click(monkeypatch, tmp_path):
    """The documented gesture, when it works, is one click and nothing else."""
    r, _seen = _torch_runner(monkeypatch, tmp_path, [100.0, 0.0])
    assert r._snuff_torch() is True
    assert len(_torch_clicks(r)) == 1


def test_a_snuff_that_only_dropped_a_step_is_finished_by_shift_clicks(
        monkeypatch, tmp_path):
    """Exactly Fran's symptom: 100 -> 75 and the log claiming it was out. The
    fallback walks it down and the save says when to stop."""
    import bridge.act as act
    r, _seen = _torch_runner(monkeypatch, tmp_path,
                             [100.0, 75.0, 50.0, 50.0, 25.0, 25.0, 0.0])
    assert r._snuff_torch() is True
    clicks = _torch_clicks(r)
    assert len(clicks) > 1, "it believed the one-click gesture"
    # The first is the full snuff (SHIFT+CTRL), the rest are SHIFT only.
    assert set(clicks[0].mods) == {act.VK_SHIFT, act.VK_CONTROL}
    assert all(tuple(c.mods) == (act.VK_SHIFT,) for c in clicks[1:])


def test_a_meter_that_stops_moving_stops_the_loop(monkeypatch, tmp_path):
    """A torch stuck at 75 must not be clicked forever.

    The bound is PER CANDIDATE now: a dead point costs the snuff plus the one
    step that proves it dead, and then the sweep moves on. What must not
    happen is `TORCH_MAX_CLICKS` being spent on each of eight points.
    """
    from bridge.layout import TORCH_CANDIDATES
    r, _seen = _torch_runner(monkeypatch, tmp_path, [75.0])
    assert r._snuff_torch() is False
    assert len(_torch_clicks(r)) <= 2 * len(TORCH_CANDIDATES)


def test_the_sweep_stops_at_the_point_that_answers(monkeypatch, tmp_path):
    """The whole reason for a list is that the game picks. Once a point moves
    the torch the rest are not tried -- clicking on past the answer is how a
    working widget gets un-found."""
    r, _seen = _torch_runner(monkeypatch, tmp_path, [100.0, 0.0])
    assert r._snuff_torch() is True
    assert len(_torch_clicks(r)) == 1


def test_the_winning_point_is_remembered_for_the_next_snuff(monkeypatch,
                                                            tmp_path):
    """A raid can want the torch down more than once, and re-walking eight
    candidates each time is up to sixteen wasted clicks."""
    r, _seen = _torch_runner(monkeypatch, tmp_path, [100.0, 75.0, 0.0])
    assert r._snuff_torch() is True
    found = r._torch_point
    assert found is not None
    assert r._torch_order()[0] == found
    assert any(tuple(c.screen) == tuple(found[1]) for c in _torch_clicks(r))


def test_a_lagging_save_does_not_refute_a_working_click(monkeypatch, tmp_path):
    """**This is how the right pixel gets thrown away.** A raid save is
    written per TURN, so a torch reduced between turns is not on disk when the
    click returns. Read once and a working widget reports as dead."""
    import bridge.act as act
    assert act.TORCH_CONFIRM_READS >= 3, "fewer reads than the stale window"
    # 100, then two stale readings, and only then the real 0.
    r, _seen = _torch_runner(monkeypatch, tmp_path,
                             [100.0, 100.0, 100.0, 0.0])
    assert r._snuff_torch() is True
    assert len(_torch_clicks(r)) == 1, "it clicked again on a stale reading"


def test_the_modifier_is_held_longer_than_a_frame():
    """SDL takes SHIFT and CTRL from its own key events, so a modified click
    only reads as modified if the keydown was PROCESSED first. One frame at
    60fps is 16.7ms and DD does not always run at 60 -- the old 20ms dwell sat
    inside that margin, which is what made "the modifier never arrived"
    indistinguishable from "the pixel is wrong"."""
    import bridge.act as act
    assert act.MOD_DWELL > 4.0 / 60.0


def test_an_unreadable_torch_refuses_rather_than_clicking_blind(
        monkeypatch, tmp_path):
    import bridge.act as act
    r, _raid, _s = _full_pack_runner(monkeypatch, tmp_path, 15,
                                     full_pack="abandon")
    monkeypatch.setattr(act.time, "sleep", lambda *_a, **_k: None)
    monkeypatch.setattr(r, "_peek", lambda: None)
    assert r._snuff_torch() is False
    assert len(_torch_clicks(r)) <= 1


def test_plan_reduce_torch_is_shift_only_and_refuses_a_bad_count(monkeypatch,
                                                                 tmp_path):
    import bridge.act as act
    r, _raid, _s = _full_pack_runner(monkeypatch, tmp_path, 15,
                                     full_pack="abandon")
    plan = act.plan_reduce_torch(r.resolved, 3)
    assert plan.actionable and len(plan.steps) == 3
    assert all(tuple(s.mods) == (act.VK_SHIFT,) for s in plan.steps)
    assert not act.plan_reduce_torch(r.resolved, 0).actionable
    assert not act.plan_reduce_torch(r.resolved, -1).actionable
    assert not act.plan_reduce_torch(r.resolved, True).actionable


def test_the_snuff_still_comes_BEFORE_the_swing(monkeypatch, tmp_path):
    """The one ordering that is not negotiable: the loot roll is taken on the
    kill, so snuffing after the blow is too late. Splitting the plan preserved
    the order -- this is the test that says so."""
    import bridge.act as act
    r, _seen = _torch_runner(monkeypatch, tmp_path, [100.0, 0.0])
    r.armed = True
    order = []
    real_execute = r.actuator.execute

    def spy(plan):
        order.append(plan.layer)
        return real_execute(plan)
    monkeypatch.setattr(r.actuator, "execute", spy)
    r.handle(_battle_update(snuff_torch=True))
    assert order and order[0] == "torch", order
    assert "battle" in order, order
    assert order.index("torch") < order.index("battle")


# --- retreat has a control, and it is the raid HUD's X ---------------------

def test_retreat_is_planned_as_the_raid_HUD_X_and_a_confirm(resolved):
    """THE GAME NAMES THIS CONTROL ITSELF, so it is not an inference.

    `[pc]str_help_raid_combat_4`: *"[CLICK] on the retreat icon (looks like an
    "X") to attempt to retreat from the combat"*, and `str_help_raid_room_5`
    and `_hallway_7` name the same icon for abandoning the quest -- one
    control, two meanings, decided by whether a fight is on.

    It returned -1 for as long as retreat existed, so `plan_battle` could
    never play the policy's own top pick. Measured live 2026-09-05: the policy
    ranked RETREAT at 72-78%, `advise_retreat` priced staying as a certain
    death, and the bridge attacked anyway for three turns with a hero on 1 of
    22 HP.
    """
    import bridge.act as act
    from bridge.overlay_slots import RETREAT_SLOT
    plan = act.plan_one_move(Alt(skill_slot=RETREAT_SLOT), resolved)
    assert plan.actionable, plan.refusal
    assert [st.screen for st in plan.steps] == [resolved.raid_abandon_quest,
                                                resolved.modal_ok]
    assert plan.steps[0].after >= 1.0, "no time for the dialog to be drawn"


def test_the_retreat_click_and_its_CONFIRM_are_one_plan(resolved):
    """An X with no confirm leaves a dialog sitting over the board swallowing
    every later input. Keeping them in one plan is what makes them atomic
    under the kill switch."""
    import bridge.act as act
    from bridge.overlay_slots import RETREAT_SLOT
    plan = act.plan_one_move(Alt(skill_slot=RETREAT_SLOT), resolved)
    assert len(plan.steps) == 2
    assert all(st.kind == "click" for st in plan.steps)


def test_only_a_BATTLE_can_reach_the_retreat_plan():
    """**Out of combat the same click throws the whole expedition away.**

    `plan_one_move` is the only route into `plan_retreat`, and `plan_battle`
    is its only caller -- so the guard is structural rather than a flag
    somebody has to remember to pass. This is what says it stayed that way.
    """
    import inspect
    import bridge.act as act
    callers = [name for name, fn in vars(act).items()
               if inspect.isfunction(fn) and fn.__module__ == act.__name__
               and "plan_retreat(" in inspect.getsource(fn)
               and name != "plan_retreat"]
    # `plan_battle` joined it on 2026-09-06 and the guard still means what it
    # meant: both are on the BATTLE path. `plan_battle` plans the retreat
    # itself when the survival layer says LEAVE and the policy's shortlist
    # carries no retreat candidate to route through `plan_one_move` -- which
    # is the case that was measured, six polls in a row, and healed instead.
    assert sorted(callers) == ["plan_battle", "plan_one_move"], callers
    users = [name for name, fn in vars(act).items()
             if inspect.isfunction(fn) and fn.__module__ == act.__name__
             and "plan_one_move(" in inspect.getsource(fn)
             and name != "plan_one_move"]
    assert users == ["plan_battle"], users


def test_advised_button_gives_retreat_its_slot():
    from bridge.run import advised_button                      # noqa: PLC0415
    from bridge.overlay_slots import RETREAT_SLOT, MOVE_SLOT, PASS_SLOT
    state = _FakeState()
    assert advised_button(state, _FakeAct("__retreat__")) == RETREAT_SLOT
    # ...and the other two rankless ones are unmoved.
    assert advised_button(state, _FakeAct("__pass__")) == PASS_SLOT
    assert advised_button(state, _FakeAct("__move_1")) == MOVE_SLOT
    assert advised_button(state, _FakeAct("__provision_food__")) == -1
    assert RETREAT_SLOT not in (MOVE_SLOT, PASS_SLOT)


class _FakeAct:
    def __init__(self, skill_id, hero_id=0):
        self.skill_id, self.hero_id = skill_id, hero_id


class _FakeState:
    heroes = ()


# --- the game's own action bar overrules the model ---

def test_a_greyed_slot_is_refused_however_confident_the_advice(resolved):
    """The save keeps the marching order and never records an in-combat swap.

    So the advised slot can name a skill this hero cannot perform from where it
    is really standing. Pressing the digit selects nothing and the click then
    lands on an empty selection -- the save never moves and the next poll
    re-derives the identical plan. Six minutes of a live Crypts raid went that
    way on 2026-09-05.
    """
    plan = plan_battle(U(skill_slot=4, side="enemy", rank=0), resolved,
                       bar("###.##"))
    assert not plan.actionable
    assert "greyed out" in plan.refusal


def test_it_falls_through_to_a_slot_the_game_WILL_take(resolved):
    """A greyed slot is a fact about the SCREEN, so the shortlist walk applies.

    That separation is the whole reason the fallback is safe: the timing
    refusals still stop everything, and this one does not.
    """
    from bridge.run import Alternative
    plan = plan_battle(
        U(skill_slot=4, side="enemy", rank=0,
          alternatives=(Alternative(skill_slot=1, side="enemy", rank=2,
                                    headline="PIERCE on rank 3",
                                    confidence=0.3),)),
        resolved, bar("###.##"))
    assert plan.actionable
    assert plan.steps[0].vk == SKILL_VKS[0]
    assert "FALLBACK" in plan.why and "greyed out" in plan.why


def test_no_bar_plans_exactly_what_it_planned_before(resolved):
    """`None` is "no opinion". Every offline caller passes it, including
    `--replay` over the whole capture corpus, so it must change nothing."""
    u = U(skill_slot=4, side="enemy", rank=0)
    blind = plan_battle(u, resolved)
    same = plan_battle(u, resolved, None)
    assert blind.actionable and same.actionable
    assert [(s.kind, s.vk, s.screen) for s in blind.steps] == \
           [(s.kind, s.vk, s.screen) for s in same.steps]


def test_a_hero_with_nothing_legal_is_MOVED_somewhere_it_can_fight(resolved):
    """The rescue, and it is the exact position the live raid deadlocked in.

    A Plague Doctor whose kit launches from 2, 3 and 4, standing at rank 1 of a
    two-hero party: four greyed skills, MOVE offered. The destination is
    derived from the bar -- the greyed pattern pins her at rank 1, and rank 2
    is the nearest rank she could launch anything from.
    """
    pd = ((4, 3), (4, 3), (4, 3), (4, 3, 2))
    plan = plan_battle(U(skill_slot=4, side="enemy", rank=0,
                         actor_launches=pd, party_size=2),
                       resolved, bar("....##"))
    assert plan.actionable
    assert [s.kind for s in plan.steps] == ["key", "click"]
    assert plan.steps[0].vk == SKILL_VKS[MOVE_SLOT - 1]
    assert plan.steps[1].screen == resolved.point("hero", 1)
    assert "rank 2" in plan.why and "rank 1" in plan.why


def test_the_rescue_needs_the_game_to_be_ASKING(resolved):
    """A bar with nothing lit at all is nobody's turn, not a stuck hero.

    Between the two, only the drawn MOVE separates "this hero cannot act" from
    "the enemies are still animating" -- and sending a swap into the second
    plays a turn that is not due.
    """
    pd = ((4, 3), (4, 3), (4, 3), (4, 3, 2))
    plan = plan_battle(U(skill_slot=4, side="enemy", rank=0,
                         actor_launches=pd, party_size=2),
                       resolved, bar("......"))
    assert not plan.actionable


def test_the_rescue_refuses_when_it_cannot_locate_the_hero(resolved):
    """No launch data, no rescue -- a swap aimed at a guess is a wasted turn.

    `party_size` under 2 lands here too: there is nobody to swap with.
    """
    for u in (U(skill_slot=4, side="enemy", rank=0, party_size=2),
              U(skill_slot=4, side="enemy", rank=0, party_size=1,
                actor_launches=((4, 3), (4, 3), (4, 3), (4, 3, 2)))):
        assert not plan_battle(u, resolved, bar("....##")).actionable


def test_a_hero_that_CAN_act_is_never_repositioned(resolved):
    """The rescue is for a stuck hero and only for a stuck hero.

    With one skill lit the shortlist walk is the right answer, and swapping
    instead would throw away a turn the hero could have spent fighting.
    """
    pd = ((4, 3), (4, 3), (4, 3), (4, 3, 2))
    plan = plan_battle(U(skill_slot=1, side="enemy", rank=0,
                         actor_launches=pd, party_size=2),
                       resolved, bar("...###"))
    assert not plan.actionable, "slot 1 is greyed, and slot 4 is not advised"
    assert "greyed out" in plan.refusal


def test_the_door_is_tried_more_than_once_with_a_step_between(monkeypatch,
                                                              tmp_path):
    """ONE PRESS IS NOT ENOUGH, and six minutes of a live raid proved it.

    2026-09-05, `coEJ`: the party at absolute tile 1 of 6 with `rooE` through
    the door, the direction derived correctly, the stall check fired, `W` went
    out, the area did not change, and the walk gave up -- then repeated the
    whole thing on every re-plan with the save's mtime frozen throughout.
    Probed by hand at that exact position, `D` moved nothing, `A` moved
    nothing, and `W` put the party in `rooE` in under two seconds.

    **The step is BACKWARDS**, which is Fran's reading of the scene: the party
    "have gone too far into the right side without opening the door, it
    skipped it". `areatile` ticks only at a tile boundary, so a party jammed
    against the wall past the door reads as "moved nothing" for the same
    reason one still walking does -- and the `A` in that probe is what put
    them back on it.
    """
    import bridge.act as act
    r = _walk_runner(monkeypatch, tmp_path, ["coAD"] * 200)
    monkeypatch.setattr(r, "_walk_progress", lambda a: _W(1, from_front=False,
                                                          total=5))
    monkeypatch.setattr(r, "_target_end_index", lambda a, b: None)
    said = []
    monkeypatch.setattr(r, "_say", lambda m: said.append(m))
    r._walk_toward("rooA", "coAD")
    keys = [st.vk for st in r.actuator.backend.sent if st.kind == "key"]
    assert keys.count(act.VK_W) == act.DOOR_TRIES, keys
    # Every retry is preceded by a step BACK, and the first try is not.
    assert sum("stepping back and trying again" in m for m in said) \
        == act.DOOR_TRIES - 1, said
    tail = keys[keys.index(act.VK_W):]
    assert tail == [act.VK_W, act.VK_A, act.VK_W, act.VK_A, act.VK_W], tail


def test_the_door_is_not_retried_once_the_party_is_through(monkeypatch,
                                                           tmp_path):
    """The retry is bounded by success first and by DOOR_TRIES second.

    A second `W` into a room the party has already entered is not harmless --
    `W` is the universal interact, so in a room it opens whatever is in front
    of the party, which is how a curio gets triggered with no plan behind it.
    """
    import bridge.act as act
    # `coAD` for each of the WALK_STALL_LIMIT walking steps, then `rooA` from
    # the first look after the door press -- so the party leaves through the
    # door and not before it.
    r = _walk_runner(monkeypatch, tmp_path,
                     ["coAD"] * 3 + ["rooA"] * 200)
    monkeypatch.setattr(r, "_walk_progress", lambda a: _W(1, from_front=False,
                                                          total=5))
    monkeypatch.setattr(r, "_target_end_index", lambda a, b: None)
    r._walk_toward("rooA", "coAD")
    keys = [st.vk for st in r.actuator.backend.sent if st.kind == "key"]
    assert keys.count(act.VK_W) == 1, keys


# --- a curio that will not open must not eat the raid ---

def test_a_curio_given_up_on_is_not_tried_again_this_raid(monkeypatch,
                                                          tmp_path):
    """`CURIO_TRIES` bounds one dispatch, and that stopped being enough.

    The 25-second stall heartbeat clears `_room_key` and re-plans, which
    restarts the ten. Measured live 2026-09-05: a sack in `coEJ` took **286
    curio actions and 208 loot presses across a whole 900 s iteration**, the
    party never left the corridor, and the raid ended where it started. A
    curio is worth a few provisions; a raid is worth the run.
    """
    import bridge.act as act
    r = _walk_runner(monkeypatch, tmp_path, ["coAD"] * 200)
    tried = []
    monkeypatch.setattr(r, "_tiles_travelled", lambda: tried.append(1) or 3)
    r._give_up_on_curio("coAD", "sack", "sack", "it would not open")
    r._interact_curio(None, A(curio_area="coAD", curio_id="sack",
                              curio_action="interact"), "coAD")
    assert tried == [], "it approached a curio it had already abandoned"


def test_giving_up_is_per_AREA_and_per_CURIO(monkeypatch, tmp_path):
    """The same id appears in several corridors and each is a different thing.

    A crate in `coAD` that will not open says nothing about the crate in
    `coBC`, and abandoning both on one failure would throw away most of a
    dungeon's loot.
    """
    import bridge.act as act
    r = _walk_runner(monkeypatch, tmp_path, ["coAD"] * 200)
    r._give_up_on_curio("coAD", "crate", "crate", "no response")
    assert ("coAD", "crate") in r.gave_up_on
    assert ("coBC", "crate") not in r.gave_up_on
    assert ("coAD", "sack") not in r.gave_up_on


def test_the_memory_is_cleared_with_the_rest_of_the_per_raid_state():
    """It is per RAID, not for the life of the process.

    `acted` is cleared at the same boundary and for the same reason -- a new
    raid is a new dungeon, and a sack in `coAD` there has never been tried.
    Checked in the source because the boundary itself is a save read.
    """
    import ast
    import inspect

    import bridge.act as act

    src = inspect.getsource(act.ActRunner)
    tree = ast.parse("class C:\n" + "\n".join(
        " " + line for line in src.splitlines()[1:]))
    cleared = [n for n in ast.walk(tree)
               if isinstance(n, ast.Attribute) and n.attr == "clear"]
    names = {getattr(n.value, "attr", "") for n in cleared}
    assert {"acted", "gave_up_on"} <= names, names


# --- when the fit cannot place the room, ask the game ---

MAP_FRAME = Path(__file__).resolve().parents[2] / "captures" / "map_whole_view_rooJ.png"
MAP_SAVE = MAP_FRAME.with_suffix(".map.json")


def _whole_map():
    """The frame a live raid could not leave a room from, and its save.

    `rooJ`, 33 cells drawn, the whole map on screen -- and `mappanel.fit`
    refuses it outright while the settled view fits it at 38.1 px/unit against
    a true 11.4, predicting every room hundreds of pixels off the panel. That
    is the state Fran watched: *"it needs to click on the room below ... never
    leaving the room"*.
    """
    pytest.importorskip("PIL")
    pytest.importorskip("numpy")
    import numpy as np
    from PIL import Image

    if not (MAP_FRAME.exists() and MAP_SAVE.exists()):
        pytest.skip("the whole-map fixture is not banked")
    from bridge.dungeon_map import read_dungeon_map
    return (np.array(Image.open(MAP_FRAME).convert("RGB")),
            read_dungeon_map(MAP_SAVE))


def _bare_runner():
    """An ActRunner with nothing constructed. These three read no state."""
    import bridge.act as act
    return object.__new__(act.ActRunner)


def test_the_party_cell_is_the_warm_one():
    """The party carries a lit torch and nothing else on the panel does.

    0.155 of its pixels warm against 0.014 for the next-warmest cell and 0.000
    for the other twelve -- an order of magnitude, which is why the floor is
    not a tuned number.
    """
    import bridge.act as act
    from bridge import mappanel

    img, _dungeon = _whole_map()
    boxes = mappanel.find_boxes(img)
    mine = act.ActRunner._party_cell(_bare_runner(), mappanel, img, boxes)
    assert mine is not None
    cx, cy = mine.centre
    assert abs(cx - 1074) < 3 and abs(cy - 644) < 3, (cx, cy)


def test_a_panel_with_no_party_marker_refuses():
    """None is a refusal, not a guess. Everything built on this is a
    direction, and a direction from the wrong origin points somewhere
    confidently wrong."""
    import numpy as np

    import bridge.act as act
    from bridge import mappanel

    img, _dungeon = _whole_map()
    cold = np.zeros_like(img)                       # nothing warm anywhere
    boxes = mappanel.find_boxes(img)
    assert act.ActRunner._party_cell(_bare_runner(), mappanel, cold,
                                     boxes) is None


def test_the_offset_comes_from_the_SAVE_not_the_screen():
    """`Tile.mappos`, so it is independent of whatever the panel is doing."""
    import bridge.act as act

    _img, dungeon = _whole_map()
    r = _bare_runner()
    assert act.ActRunner._map_offset(r, dungeon, "rooJ", "rooO") == (0.0, 7.0)
    assert act.ActRunner._map_offset(r, dungeon, "rooJ", "rooE") == (0.0, -7.0)
    assert act.ActRunner._map_offset(r, dungeon, "rooJ", "rooJ") is None
    assert act.ActRunner._map_offset(r, dungeon, "rooJ", "nowhere") is None


def test_the_room_BELOW_is_the_first_cell_offered_to_the_game(monkeypatch,
                                                              tmp_path):
    """The whole point, and Fran named the answer before the code found it.

    `rooO` is straight below `rooJ` in the save (offset 0, +7), screen y and
    map y run the same way, and the cell at (1074, 720) is the room below the
    party on the panel. It has to be hovered FIRST -- each hover costs about a
    second and the budget is five.
    """
    import bridge.act as act
    from bridge import mappanel

    img, dungeon = _whole_map()
    r = _walk_runner(monkeypatch, tmp_path, ["rooJ"] * 20)
    asked = []

    def _offers(_screen, _mappanel, at):
        # The game says yes to the FIRST thing it is asked, so what the
        # returned point proves is the ORDER -- which is the claim.
        asked.append(at)
        return True

    monkeypatch.setattr(r, "_map_offers_move", _offers)
    got = r._ask_the_game_which_cell(None, mappanel, img, dungeon,
                                     "rooJ", "rooO")
    assert got is not None, "it never found the room below"
    assert abs(got[0] * img.shape[1] - 1074) < 3
    assert abs(got[1] * img.shape[0] - 720) < 3
    assert len(asked) == 1, "the room below was not the first cell tried"


def test_the_best_ranked_cell_is_taken_when_the_game_confirms_none(
        monkeypatch, tmp_path):
    """Never clicking costs the raid; a click that misses costs one poll.

    "Not clicking blind" was right while blind meant UNORDERED -- the only
    ranking was a fit, and a wrong fit put `rooE` 72 px from where a right one
    put it. The ordering here comes from the save: the party's own cell found
    by its torch, and the direction to the destination in map units. The hover
    still gets first refusal, and `_send_and_confirm` is already capped at one
    attempt for map clicks.
    """
    import bridge.act as act
    from bridge import mappanel

    img, dungeon = _whole_map()
    r = _walk_runner(monkeypatch, tmp_path, ["rooJ"] * 20)
    tried = []
    monkeypatch.setattr(r, "_map_offers_move",
                        lambda *a: tried.append(a[-1]) or False)
    got = r._ask_the_game_which_cell(None, mappanel, img, dungeon,
                                     "rooJ", "rooO")
    assert 0 < len(tried) <= act.BLIND_CANDIDATES, "the hover was skipped"
    assert got is not None, "it refused instead of taking the best cell"
    # ...and the cell it takes is the same one it hovered FIRST: the room
    # below, which is where the save says `rooO` is.
    assert abs(got[0] * img.shape[1] - 1074) < 3
    assert abs(got[1] * img.shape[0] - 720) < 3


def test_cells_the_WRONG_WAY_are_never_hovered(monkeypatch, tmp_path):
    """A cell behind the party cannot be the destination, and the budget is
    five hovers. Dropped rather than ranked last."""
    from bridge import mappanel

    img, dungeon = _whole_map()
    r = _walk_runner(monkeypatch, tmp_path, ["rooJ"] * 20)
    tried = []
    monkeypatch.setattr(r, "_map_offers_move",
                        lambda *a: tried.append(a[-1]) or False)
    # rooE is straight UP from rooJ, so nothing below may be hovered.
    r._ask_the_game_which_cell(None, mappanel, img, dungeon, "rooJ", "rooE")
    party_y = 644
    assert tried, "it hovered nothing at all"
    for at in tried:
        frac_y = at[1] / r.resolved.win.client_size[1]
        assert frac_y * img.shape[0] < party_y, at


# --- the save is ahead of the screen (2026-09-06) ---


def _bar_reader(monkeypatch, patterns):
    """An `ActRunner._read_bar` fed a scripted sequence of bar patterns."""
    import bridge.act as act
    from bridge import actionbar

    frames = list(patterns)
    seen = []

    def _read(_img, _resolved):
        seen.append(len(seen))
        if not frames:
            return None
        p = frames.pop(0)
        if p is None:
            return None
        lit = tuple(c == "#" for c in p)
        return actionbar.ActionBar(lit, tuple(1.0 if x else 0.0 for x in lit),
                                   tuple(200.0 if x else 50.0 for x in lit))

    monkeypatch.setattr(actionbar, "read", _read)
    monkeypatch.setattr("bridge.screen.grab", lambda _w: object())
    monkeypatch.setattr("time.sleep", lambda *_a: None)
    r = object.__new__(act.ActRunner)
    r.resolved = type("R", (), {"win": object()})()
    return r, seen


def test_a_SETTLED_bar_is_returned_on_the_second_look(monkeypatch):
    """The ordinary case costs one extra grab, about 15 ms."""
    r, seen = _bar_reader(monkeypatch, ["#..###", "#..###"])
    bar = r._read_bar()
    assert bar is not None and str(bar) == "#..###"
    assert len(seen) == 2


def test_a_bar_STILL_ANIMATING_is_no_opinion_rather_than_a_wrong_one(
        monkeypatch):
    """The bug this exists for.

    `run.py` polls the SAVE, so a turn is planned the instant the save says a
    new hero is up -- while the screen is still drawing the PREVIOUS hero's
    bar. One grab there reports that hero's lit slots, the greyed-slot guard
    sees nothing wrong, the digit selects nothing and the save never moves.
    Measured 2026-09-06 on a Duelist turn: `KEY 1` sent twice into a slot the
    frame banked 15 s later showed at saturation 0.000.
    """
    r, _seen = _bar_reader(monkeypatch,
                           ["####", ".#..", "#.#.", "..##", "#..#"])
    assert r._read_bar() is None


def test_it_settles_LATE_rather_than_giving_up_early(monkeypatch):
    """A bar that stops changing part way through the budget is trusted."""
    r, _seen = _bar_reader(monkeypatch, ["####..", ".#....", ".#....",
                                         "######"])
    bar = r._read_bar()
    assert bar is not None and str(bar) == ".#...."


def test_an_UNREADABLE_frame_is_still_None_immediately(monkeypatch):
    """None from the reader is "cannot see" and must not be retried into an
    opinion -- an uncalibrated layout does not become calibrated by looking
    again, and the settle budget is for animation, not for hope."""
    r, seen = _bar_reader(monkeypatch, [None, "####", "####"])
    assert r._read_bar() is None
    assert len(seen) == 1


def test_the_settle_compares_SKILLS_and_not_the_whole_bar(monkeypatch):
    """MOVE and the X are lit whenever a hero is being asked, so including
    them would make the test easier to pass, not harder. What has to hold
    still is the four slots the digit selects."""
    r, _seen = _bar_reader(monkeypatch, ["#..#.#", "#..###"])
    bar = r._read_bar()
    assert bar is not None
    assert bar.skill_pattern == (True, False, False, True)


# --- a free action is not the whole turn (2026-09-06) ---


def _stance(**kw):
    """The advice for a stance change: a self-target skill that KEEPS the
    turn. `.is_continue_turn` -- the Duelist's and the Sibyl's whole class
    identity, and the Abomination's transform is the vanilla instance."""
    base = dict(skill_slot=1, side="hero", rank=3, headline="ANTICIPATION",
                continues_turn=True, turn_key=(2, 27, 0, 0))
    base.update(kw)
    return _battle_update(**base)


def test_a_continue_turn_skill_carries_through_to_the_PLAN():
    """It has to survive `plan_one_move`, or nothing downstream can know the
    turn is still open."""
    import bridge.act as act

    resolved = load_layout().resolve(_Win())
    assert act.plan_one_move(_stance(), resolved).continues_turn is True
    assert act.plan_one_move(_battle_update(), resolved).continues_turn is False


def test_the_FALLBACK_does_not_drop_the_flag():
    """`plan_battle` rebuilds the plan when it falls down the shortlist, and
    a rebuilt plan that forgot the flag closes a turn the game kept open."""
    import bridge.act as act
    from bridge.run import Alternative

    resolved = load_layout().resolve(_Win())
    alt = Alternative(skill_slot=1, side="hero", rank=3, headline="ANTICIPATION",
                      confidence=0.4, continues_turn=True)
    update = _battle_update(skill_slot=99, alternatives=(alt,))
    plan = act.plan_battle(update, resolved)
    assert plan.actionable and "FALLBACK" in plan.why
    assert plan.continues_turn is True


def test_the_turn_is_NOT_marked_played_after_a_free_action(monkeypatch):
    """The bug, in one assertion.

    The bridge sent the stance change, put the turn in `acted`, and waited
    for a turn the game had already handed back. Fran, watching it: *"the
    duelist change forms but didn't do the second action ... it expects 1
    action per hero"*. So the same turn must be planned again -- and the
    second plan is a real action, because the stance skill is
    `.per_turn_limit 1` and the game greys it out.
    """
    r = _runner(monkeypatch, armed=True, confirm=False)
    u = _stance()
    r.handle(u)
    assert r.stats["acted"] == 1
    assert u.turn_key not in r.acted, "the turn was closed by a free action"
    r.handle(u)
    assert r.stats["acted"] == 2, "the second action never went out"


def test_an_ORDINARY_turn_is_still_closed_exactly_once(monkeypatch):
    """The control. A double-press is not cosmetic here -- it plays a second
    move -- so the free-action path must not have relaxed the dedupe for
    everything else."""
    r = _runner(monkeypatch, armed=True, confirm=False)
    u = _battle_update()
    r.handle(u)
    r.handle(u)
    assert r.stats["acted"] == 1
    assert u.turn_key in r.acted


def test_free_actions_on_one_turn_are_CAPPED(monkeypatch):
    """A hero asked forever is the failure this file keeps rediscovering.

    The data bounds it at one -- every shipped stance skill carries
    `.per_turn_limit 1` -- so the cap is a loop-breaker rather than a tuning
    knob.
    """
    import bridge.act as act

    r = _runner(monkeypatch, armed=True, confirm=False)
    u = _stance()
    for _ in range(act.MAX_FREE_ACTIONS + 2):
        r.handle(u)
    assert r.stats["acted"] == act.MAX_FREE_ACTIONS
    assert u.turn_key in r.acted


def test_the_free_action_count_is_cleared_WITH_the_turns():
    """`_continued` is keyed on `turn_key`, and a `turn_key` repeats across
    combats -- `(round, guid, turns_taken, enemies_first)` all reset. That is
    exactly why `acted` is cleared at a combat boundary instead of being
    trusted to be unique, so a counter sharing its key has to be cleared in
    the same breath or round 1 of combat 2 starts with a spent budget.

    Asserted on the source, because the boundary is deep inside the poll loop
    and the invariant is "these two lines stay adjacent".
    """
    import inspect

    import bridge.act as act

    src = inspect.getsource(act.ActRunner)
    lines = [ln.strip() for ln in src.splitlines()]
    i = lines.index("self.acted.clear()")
    assert lines[i + 1] == "self._continued.clear()", lines[i:i + 3]


def test_the_run_layer_reads_is_continue_turn_off_the_STORE():
    """Not off a class list. The Duelist, the Sibyl and the Abomination all
    have it, and a modded stance class would have it too with no entry
    anywhere -- the same reason `UNRESTRICTED_HEROES` is derived."""
    import inspect

    from bridge import run

    src = inspect.getsource(run._continues_turn)
    assert "skill_index" in src and "is_continue_turn" in src

    # Unresolvable is False: an unknown skill ends the turn, and being wrong
    # that way costs one poll rather than a double-press.
    def _act(sid):
        return type("A", (), {"skill_id": sid})()

    assert run._continues_turn(object(), _act("nope")) is False
    for sid in ("__pass__", "__retreat__", "__move_1__", ""):
        assert run._continues_turn(object(), _act(sid)) is False


# --- a dark bar is the game not listening (2026-09-06) ---


class _Bar:
    """A hand-built action bar. `pattern` is 6 chars: 4 skills, MOVE, PASS."""

    def __init__(self, pattern):
        self.lit = tuple(c == "#" for c in pattern)

    @property
    def waiting(self):
        return any(self.lit[4:])

    @property
    def any_skill(self):
        return any(self.lit[:4])

    @property
    def skill_pattern(self):
        return tuple(self.lit[:4])

    def slot_lit(self, slot):
        return bool(self.lit[slot - 1]) if 1 <= slot <= len(self.lit) else False

    def __str__(self):
        return "".join("#" if x else "." for x in self.lit)


def test_a_DARK_bar_is_refused_once_and_for_the_right_reason():
    """It used to fall through to the per-candidate greyed-slot message,
    which is true of the pixels and wrong about the cause.

    That cost real time on 2026-09-06: a log full of "not legal from the rank
    this hero is really standing in" sent the reader after a formation bug
    that was not there. Nobody was being asked.
    """
    import bridge.act as act

    plan = act.plan_battle(_battle_update(), load_layout().resolve(_Win()),
                           _Bar("......"))
    assert not plan.actionable
    assert "not asking anyone for input" in plan.refusal
    assert "standing in" not in plan.refusal


def test_a_dark_bar_does_NOT_spend_the_turn(monkeypatch):
    """The deadlock this file keeps rediscovering, in the other direction.

    The three timing gates are safe to mark spent because `turn_key` carries
    `enemies_first`, so resolving them changes the key. A dark bar changes
    NOTHING about the key -- same hero, same round, same turn count the
    instant the animation ends -- so marking it spent drops a live turn.
    """
    import bridge.act as act

    r = _runner(monkeypatch, armed=True, confirm=False)
    monkeypatch.setattr(r, "_read_bar", lambda: _Bar("......"))
    u = _battle_update()
    r.handle(u)
    assert u.turn_key not in r.acted

    # ...and once the game starts listening, the same turn is played.
    monkeypatch.setattr(r, "_read_bar", lambda: _Bar("##..##"))
    r.handle(u)
    assert r.stats["acted"] == 1
    assert u.turn_key in r.acted


def test_deferring_is_BOUNDED(monkeypatch):
    """Then it is handed to `run.py`'s stall heartbeat, which is the layer
    that knows how to try somebody else. Unbounded deferral is the
    republish-forever loop the tutorial retreat, the `rooH` relight and the
    curio all were."""
    import bridge.act as act

    r = _runner(monkeypatch, armed=True, confirm=False)
    monkeypatch.setattr(r, "_read_bar", lambda: _Bar("......"))
    u = _battle_update()
    for _ in range(act.MAX_DEFERRALS + 3):
        r.handle(u)
    assert u.turn_key in r.acted
    assert r.actuator.backend.sent == [], "it sent something into a dark bar"


def test_the_timing_gates_are_still_checked_BEFORE_the_bar():
    """A provisional turn must not be reported as "the bar is dark".

    Both are true at once while enemies act, and the timing reason is the one
    that says WHY the bar is dark -- the save can see the enemy turns coming
    and the pixels cannot.
    """
    import bridge.act as act

    u = _battle_update(provisional=True, enemies_first=2)
    plan = act.plan_battle(u, load_layout().resolve(_Win()), _Bar("......"))
    assert "provisional" in plan.refusal


def test_a_free_action_still_SETTLES_and_still_checks_the_kill_switch():
    """It bypasses `_send_and_confirm` -- the save cannot confirm it -- so
    everything that path does BEFORE the send has to be repeated here.

    "Sent into an animation" is the first failure that path ever closed: the
    save is written when an action RESOLVES, so the poll that woke the bridge
    fires while that action is still animating and input goes nowhere.
    """
    import inspect

    import bridge.act as act

    src = inspect.getsource(act.ActRunner._handle)
    branch = src[src.index("if plan.continues_turn:"):]
    branch = branch[:branch.index("if self._send_and_confirm(")]
    assert "SETTLE_BEFORE_SEND" in branch
    assert "kill.check()" in branch
    assert "the backend refused to send" in branch


# --- ask the SCREEN whose turn it is, before spending a keystroke ---


def test_a_wrong_hero_turn_sends_NOTHING(monkeypatch):
    """Six misses in one raid on 2026-09-06, 15 s each -- and the cost was
    never only the timeout.

    The digit SELECTS a skill on the other hero's bar and DD dims the board
    into target-select, so the game is left holding a live selection that a
    later click anywhere legal would execute. Refusing before the send is
    both faster and the only version that leaves the game untouched.
    """
    r = _runner(monkeypatch, armed=True, confirm=False)
    monkeypatch.setattr(r, "_asked_hero", lambda _u: "duelist")
    called = []
    r.on_turn_missed = lambda: called.append(1)
    u = _battle_update(actor_class="antiquarian",
                       party_classes=("antiquarian", "duelist"))
    r.handle(u)
    assert r.actuator.backend.sent == [], "it sent into the wrong hero's bar"
    assert called == [1], "it did not ask about the other hero"
    assert u.turn_key in r.acted


def test_the_panel_AGREEING_changes_nothing(monkeypatch):
    """The control. 98% of turns look like this and must cost nothing."""
    r = _runner(monkeypatch, armed=True, confirm=False)
    monkeypatch.setattr(r, "_asked_hero", lambda _u: "antiquarian")
    r.handle(_battle_update(actor_class="antiquarian",
                            party_classes=("antiquarian", "duelist")))
    assert [s.kind for s in r.actuator.backend.sent] == ["key", "click"]


def test_a_panel_that_CANNOT_TELL_defers_to_the_save(monkeypatch):
    """One-sided: it names a hero or it says nothing, and nothing means the
    save keeps its answer. The save is right 98% of the time, so a refusal
    here would cost far more than it saves."""
    r = _runner(monkeypatch, armed=True, confirm=False)
    monkeypatch.setattr(r, "_asked_hero", lambda _u: None)
    r.handle(_battle_update(actor_class="antiquarian",
                            party_classes=("antiquarian", "duelist")))
    assert [s.kind for s in r.actuator.backend.sent] == ["key", "click"]


def test_it_does_not_drop_the_turn_when_no_recovery_is_wired(monkeypatch):
    """Same rule the timeout path follows. With no `on_turn_missed` -- a bare
    runner, or `--replay` -- nothing else would ever try, so marking the key
    spent would be the whole loss rather than a handover."""
    r = _runner(monkeypatch, armed=True, confirm=False)
    monkeypatch.setattr(r, "_asked_hero", lambda _u: "duelist")
    r.on_turn_missed = None
    u = _battle_update(actor_class="antiquarian",
                       party_classes=("antiquarian", "duelist"))
    r.handle(u)
    assert r.actuator.backend.sent == []
    assert u.turn_key not in r.acted


def test_the_party_classes_reach_the_update():
    """`heropanel.read` needs the pool to match within, and a pool of one
    cannot be confirmed by a comparison -- so a two-hero party is the smallest
    that this can ever help, and `run.py` has to supply it."""
    import inspect

    from bridge import run

    src = inspect.getsource(run.Session._battle_update)
    assert "party_classes=" in src and "actor_class=" in src


def test_the_log_names_a_STALE_FORMATION_when_the_bar_disagrees(monkeypatch,
                                                                capsys):
    """Instrumented, not acted on -- deliberately.

    A Highwayman the save put at rank 4 was advised MOVE to rank 2, with MOVE
    lit, and nothing landed. That looks exactly like a stale formation, and
    it also looks like several other things; two inferences on 2026-09-06
    were wrong before the measurement arrived. So the line goes in first and
    the next run answers it.
    """
    said = []
    r = _runner(monkeypatch, armed=True, confirm=False)
    monkeypatch.setattr(r, "_say", said.append)
    monkeypatch.setattr(r, "_read_bar", lambda: _Bar("#...##"))
    monkeypatch.setattr(r, "_asked_hero", lambda _u: None)
    # A kit whose slot 1 launches ONLY from rank 1 -- so a lit slot 1 can only
    # mean the hero is standing at rank 1, whatever the save says.
    r.handle(_battle_update(
        actor_launches=((1,), (2,), (3,), (4,)),
        party_size=4, actor_rank=4))
    line = next((s for s in said if "bar:" in s), "")
    assert "STALE FORMATION" in line, said
    assert "the bar says rank 1" in line and "the save says 4" in line


def test_it_says_AGREED_when_they_agree(monkeypatch):
    """A disagreement is only worth printing if agreement is printed too --
    otherwise the absence of the line is ambiguous between "they agree" and
    "the rank could not be recovered"."""
    said = []
    r = _runner(monkeypatch, armed=True, confirm=False)
    monkeypatch.setattr(r, "_say", said.append)
    monkeypatch.setattr(r, "_read_bar", lambda: _Bar("#...##"))
    monkeypatch.setattr(r, "_asked_hero", lambda _u: None)
    r.handle(_battle_update(
        actor_launches=((1,), (2,), (3,), (4,)),
        party_size=4, actor_rank=1))
    line = next((s for s in said if "bar:" in s), "")
    assert "rank 1, agreed" in line, said


def test_an_AMBIGUOUS_pattern_says_nothing(monkeypatch):
    """A kit that launches from everywhere lights all four everywhere, so the
    signature refutes no rank and there is nothing to report. Silence here is
    the reader refusing, which is its contract."""
    said = []
    r = _runner(monkeypatch, armed=True, confirm=False)
    monkeypatch.setattr(r, "_say", said.append)
    monkeypatch.setattr(r, "_read_bar", lambda: _Bar("######"))
    monkeypatch.setattr(r, "_asked_hero", lambda _u: None)
    r.handle(_battle_update(
        actor_launches=((1, 2, 3, 4),) * 4, party_size=4, actor_rank=2))
    line = next((s for s in said if "bar:" in s), "")
    assert "rank" not in line, line


# --- a MOVE is invisible to the save (2026-09-06) ---


def test_only_a_MOVE_asks_the_screen_to_witness_it():
    """`persist.raid.json` keeps the party's MARCHING order and never records
    an in-combat swap -- which is the whole reason `bridge/actionbar.py`
    exists. An attack writes a save; a swap does not."""
    import bridge.act as act

    resolved = load_layout().resolve(_Win())
    move = _battle_update(skill_slot=act.MOVE_SLOT, side="hero", rank=1,
                          headline="MOVE to rank 2")
    assert act.plan_one_move(move, resolved).screen_witness is True
    assert act.plan_one_move(_battle_update(), resolved).screen_witness is False


def test_the_FALLBACK_keeps_the_witness_too():
    """A MOVE reached down the shortlist is still a MOVE. `plan_battle`
    rebuilds the plan there, and a rebuilt one that forgot this waits 15 s
    for a write that is never coming."""
    import bridge.act as act
    from bridge.run import Alternative

    resolved = load_layout().resolve(_Win())
    alt = Alternative(skill_slot=act.MOVE_SLOT, side="hero", rank=1,
                      headline="MOVE to rank 2", confidence=0.19)
    plan = act.plan_battle(_battle_update(skill_slot=99, alternatives=(alt,)),
                           resolved)
    assert plan.actionable and plan.screen_witness is True


def test_a_MOVE_is_not_reported_as_a_MISS(monkeypatch):
    """The bug, live on 2026-09-06.

    A Highwayman the save put at rank 4, MOVE lit, `MOVE to rank 2` sent --
    and "no save change in 15s - the input did not land". The engine's own
    distance bound says that move is legal (`.move 2 2`), the click landed on
    the sprite, and the save was never going to say anything either way. The
    turn was then given up and the real formation drifted from the save,
    which is what put every later target off.
    """
    import bridge.act as act

    said = []
    r = _runner(monkeypatch, armed=True, confirm=False)
    monkeypatch.setattr(r, "_say", said.append)
    monkeypatch.setattr(r, "_asked_hero", lambda _u: None)
    bars = iter([_Bar("#...##"), _Bar("###.##")])
    monkeypatch.setattr(r, "_read_bar", lambda: next(bars, _Bar("###.##")))
    u = _battle_update(skill_slot=act.MOVE_SLOT, side="hero", rank=1,
                       headline="MOVE to rank 2")
    r.handle(u)
    assert r.stats["acted"] == 1
    assert u.turn_key in r.acted, "a MOVE still ends the turn"
    assert not any("did not land" in s for s in said), said
    assert any("the bar is the witness" in s for s in said), said


def test_a_MOVE_the_bar_did_not_confirm_still_ENDS_the_turn(monkeypatch):
    """It is reported honestly and the turn is spent anyway.

    The alternative is asking the same hero forever, and the save -- the only
    other witness -- could never have answered the question. `Did(sent=,
    confirmed=)` discipline: a send that was not acknowledged is reported as
    a send.
    """
    import bridge.act as act

    said = []
    r = _runner(monkeypatch, armed=True, confirm=False)
    monkeypatch.setattr(r, "_say", said.append)
    monkeypatch.setattr(r, "_asked_hero", lambda _u: None)
    monkeypatch.setattr(r, "_read_bar", lambda: _Bar("#...##"))
    u = _battle_update(skill_slot=act.MOVE_SLOT, side="hero", rank=1,
                       headline="MOVE to rank 2")
    r.handle(u)
    assert u.turn_key in r.acted
    assert any("the bar did not change" in s for s in said), said


# -- the save lags, so "unchanged" is not "did not happen" ----------------
#
# Measured live 2026-09-06 in a crypts raid. `persist.raid.json` carries
# `torchlight` and is written per TURN, and both torch actions happen where
# there is no turn to write: a relight in a corridor, a snuff inside a hero's
# turn before the killing blow. Read as an answer, that silence refuted eight
# candidate widgets at torch 70 and told the run its relight had missed --
# 58 s before the save caught up and the marker printed
# `MATCH torch the torch went up`.


class _Torch:
    """The two readers `_torch_settled` uses, and nothing else."""

    def __init__(self, torch, sigs):
        self._torch = list(torch)
        self._sigs = list(sigs)

    def _torch_now(self):
        return self._torch[0] if len(self._torch) == 1 else self._torch.pop(0)

    def _save_sig(self):
        return self._sigs[0] if len(self._sigs) == 1 else self._sigs.pop(0)


def _settled(monkeypatch, torch, sigs, before=70.0):
    import bridge.act as act
    monkeypatch.setattr(act.time, "sleep", lambda *_a: None)
    return act.ActRunner._torch_settled(_Torch(torch, sigs), before)


def test_a_torch_that_MOVED_is_a_confirmation(monkeypatch):
    level, witnessed = _settled(monkeypatch, [70.0, 45.0], [(1.0, 10)])
    assert (level, witnessed) == (45.0, True)


def test_an_unchanged_torch_with_a_REWRITTEN_save_is_a_refutation(monkeypatch):
    """The save answered and the torch is still 70, so that point is not it."""
    level, witnessed = _settled(monkeypatch, [70.0],
                                [(1.0, 10), (2.0, 10)])
    assert (level, witnessed) == (70.0, True)


def test_an_unchanged_torch_with_a_SILENT_save_says_NOTHING(monkeypatch):
    """The case that refuted eight good-or-bad candidates in one run."""
    level, witnessed = _settled(monkeypatch, [70.0], [(1.0, 10)])
    assert (level, witnessed) == (70.0, False)


def test_an_unreadable_save_is_not_a_witness(monkeypatch):
    level, witnessed = _settled(monkeypatch, [70.0], [None])
    assert (level, witnessed) == (70.0, False)


def test_the_SWEEP_stops_at_the_first_thing_it_cannot_tell():
    """Every later candidate is read through the same silent save, so the
    remaining clicks buy nothing -- and each one is live input into a running
    game. One run spent sixteen of them and learned nothing."""
    import inspect

    from bridge import act

    src = inspect.getsource(act.ActRunner._snuff_torch)
    body = chr(10).join(ln for ln in src.splitlines()
                        if not ln.lstrip().startswith("#"))
    assert "if not witnessed:" in body
    assert body.index("if not witnessed:") < body.index("is not the torch widget")


def test_a_RELIGHT_is_sent_exactly_once():
    """`T` burns a torch, so it is the exception to "a keystroke means the
    same thing the second time". Three attempts at 15 s each reported a miss,
    the run gave up, and the save then showed 70 -> 100: one torch restores
    25, so 95 was the ceiling for a single press and at least two were spent.
    """
    import inspect

    from bridge import act

    src = inspect.getsource(act.ActRunner._handle_room)
    body = chr(10).join(ln for ln in src.splitlines()
                        if not ln.lstrip().startswith("#"))
    assert 'tries = 1 if plan.layer == "torch" else 0' in body
    assert "_send_and_confirm(plan, attempts=tries)" in body


def test_the_verdict_does_not_need_the_POLICYS_PERMISSION(resolved):
    """The shortlist is the policy's top four, not the menu of legal moves.

    Measured live 2026-09-06, two combats from the end of a crypts raid: the
    survival layer printed RETREAT on six consecutive polls at
    `P(finish intact) 33%`, and every one of them healed instead, because no
    candidate carried `RETREAT_SLOT` and the scan fell through. The policy is
    the half that has already been outvoted; it does not also get to decide
    whether the verdict is reachable.
    """
    import bridge.act as act

    bar = type("B", (), {"waiting": True})()
    plan = act.plan_battle(_retreat_update(True, slot=1), resolved, bar)
    assert plan.actionable, plan.refusal
    assert "offer it at all" in plan.why, plan.why
    assert plan.steps == act.plan_retreat(resolved).steps


def test_a_shortlisted_retreat_is_still_taken_the_OLD_way(resolved, monkeypatch):
    """The proven path is untouched: when the policy did rank it, that
    candidate is what gets planned, and the reason says which it was."""
    import bridge.act as act

    bar = type("B", (), {"waiting": True})()
    plan = act.plan_battle(_retreat_update(True), resolved, bar)
    assert plan.actionable, plan.refusal
    assert "rank it first" in plan.why, plan.why


def test_no_verdict_means_no_retreat_is_planned_ANYWHERE(resolved):
    """Off by default in every sense, including the new branch."""
    import bridge.act as act

    plan = act.plan_battle(_retreat_update(False, slot=1), resolved, bar=None)
    assert "LEAVE" not in plan.why
