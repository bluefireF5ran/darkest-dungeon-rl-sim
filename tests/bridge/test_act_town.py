"""Turning a town decision into input, and refusing when it cannot.

The dungeon planners in `tests/bridge/test_act.py` work from coordinates a human
clicked. These work from coordinates DERIVED out of the game's own layout
files (`bridge/townlayout.py`), which changes the failure mode: a hand-clicked
point is either right or obviously wrong, while a derived one can be
*plausibly* wrong -- off by a panel offset, or reading the wrong widget's
anchor. So the refusals matter more here, not less.

Three rules being pinned, each of which cost something to establish:

* **A recruit is a DRAG.** DD's coach panel selects on click and hires on
  drop, so a click reads as "look at this hero" and the log would show a hire
  that never happened.
* **The trades are not bundled into the purchase.** Each is its own confirmed
  exchange on a different widget; one `InputPlan` that half-succeeds is
  exactly what `Actuator.execute` stops on and cannot undo.
* **An unknown building refuses rather than falling back.** A typo that opened
  the Abbey instead of the coach would look like a working run right up until
  it committed a hero to a week of prayer.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest

from bridge.act import (MAX_EXCHANGE_CLICKS, OfflineWindow, plan_buy_upgrade,
                        plan_close_building, plan_exchange, plan_hire,
                        plan_open_building, plan_open_exchange)
from bridge.layout import load as load_layout
from bridge import townlayout
from data.heirlooms import HEIRLOOMS, Rate, Trade, UpgradeNode

from data.paths import GAME_DIR

pytestmark = pytest.mark.skipif(not GAME_DIR.exists(),
                                reason="needs the game install")


@pytest.fixture
def resolved():
    return load_layout().resolve(OfflineWindow(size=(1600, 900),
                                               origin=(100, 50)))


class _Hire:
    def __init__(self, index, hero_class="vestal"):
        self.index = index
        self.recruit = type("R", (), {"hero_class": hero_class,
                                      "label": lambda s: hero_class})()


def _node(code="a", tree="stage_coach.numrecruits"):
    return UpgradeNode(tree_id=tree, code=code, cost={"deed": 3, "crest": 4})


class _Purchase:
    def __init__(self, node, trades=()):
        self.node = node
        self.trades = tuple(trades)


# -- opening and closing a building ---------------------------------------

@pytest.mark.parametrize("name", sorted(townlayout.BUILDING_SCENE_POINTS))
def test_a_MEASURED_building_has_a_plan(resolved, name):
    plan = plan_open_building(name, resolved)
    assert plan.actionable, name
    assert len(plan.steps) == 1
    assert plan.steps[0].kind == "click"
    assert plan.steps[0].screen != (0, 0)


@pytest.mark.parametrize("name", [n for n in townlayout.BUILDINGS
                                  if n not in townlayout.BUILDING_SCENE_POINTS])
def test_an_UNMEASURED_building_refuses(resolved, name):
    """The lesson from the first live town visit, as a rule.

    `building_button` derives the navigation strip's button perfectly, and the
    strip **is not drawn on the Hamlet** -- DD puts it inside a building panel.
    Clicking the derived point opened nothing and the visit then sent six more
    clicks into an open town. So a building whose scene point has not been
    MEASURED must refuse, not fall back to the derivable-but-wrong one.
    """
    plan = plan_open_building(name, resolved)
    assert not plan.actionable, name
    assert plan.steps == ()
    assert "measured" in plan.refusal


def test_the_measured_buildings_are_at_distinct_points(resolved):
    """If two ever resolved to the same pixel, one building would be
    unreachable and nothing else would say so."""
    names = sorted(townlayout.BUILDING_SCENE_POINTS)
    pts = {plan_open_building(n, resolved).steps[0].screen for n in names}
    assert len(pts) == len(names)


def test_the_scene_point_is_NOT_the_navigation_strip(resolved):
    """They are different widgets in different places, and conflating them is
    exactly the bug. Pinned so a future edit cannot quietly point the opener
    back at the strip."""
    for name in townlayout.BUILDING_SCENE_POINTS:
        assert (townlayout.building_scene_point(name)
                != townlayout.building_button(name))


def test_an_unknown_building_is_refused_not_defaulted(resolved):
    for name in ("stagecoach", "STAGE_COACH", "", "abbey2"):
        plan = plan_open_building(name, resolved)
        assert not plan.actionable, name
        assert plan.steps == ()


def test_closing_a_panel_is_one_click(resolved):
    plan = plan_close_building(resolved)
    assert plan.actionable
    assert len(plan.steps) == 1 and plan.steps[0].kind == "click"


# -- buying an upgrade ----------------------------------------------------

# **BOTH GATES ARE OPEN, AND THE TESTS OF THE CLOSED SIDE ARE GONE.**
# `UPGRADES_MEASURED` lifted once the building crest was found to open the
# trees and all 13 nodes landed within 1 px of their box centres on a live
# frame; `EXCHANGE_MEASURED` lifted with the panel. The two tests that pinned
# the REFUSAL -- "not verified" -- could only skip from that moment on, and a
# test that can only skip is a line in the report and nothing else. They were
# still passing, silently, until `tools/find_silent_tests.py` was pointed at
# them. What they protected is covered by the tests below, which check the
# clicks the open gates let through.
#
# The finding inside the purchase one is worth keeping and is not lost: no
# upgrade tree is drawn on `captures/screens/stage_coach_panel.png` at all
# -- the derived nodes land in the panel's dark left margin -- which is why
# the crest click exists. `test_the_crest_opens_the_trees` is where that
# lives now.


@pytest.mark.skipif(not townlayout.UPGRADES_MEASURED,
                    reason="upgrade tree geometry is not verified yet")
def test_buying_a_rank_clicks_its_own_node(resolved):
    a = plan_buy_upgrade(_Purchase(_node("a")), 0, resolved)
    b = plan_buy_upgrade(_Purchase(_node("b")), 0, resolved)
    assert a.actionable and b.actionable
    assert a.steps[0].screen != b.steps[0].screen, "ranks are separate nodes"


@pytest.mark.skipif(not townlayout.UPGRADES_MEASURED,
                    reason="upgrade tree geometry is not verified yet")
def test_different_trees_are_different_rows(resolved):
    first = plan_buy_upgrade(_Purchase(_node("a")), 0, resolved)
    second = plan_buy_upgrade(_Purchase(_node("a")), 1, resolved)
    assert first.steps[0].screen[1] != second.steps[0].screen[1]


def test_a_purchase_that_still_needs_a_trade_is_REFUSED(resolved):
    """Not a defensive check -- the ordering rule. The trade is its own
    confirmed exchange on another widget, and bundling the two makes a plan
    that can half-succeed with the wallet in a state nobody planned for.

    Refused today by the geometry gate above as well; this asserts the reason
    survives once that gate lifts, which is why it checks the plan is not
    actionable rather than checking which refusal fired.
    """
    trade = Trade(Rate("crest", 3, "deed", 1), 1)
    plan = plan_buy_upgrade(_Purchase(_node("b"), [trade]), 0, resolved)
    assert not plan.actionable


def test_an_unknown_rank_code_is_refused(resolved):
    plan = plan_buy_upgrade(_Purchase(_node("z")), 0, resolved)
    assert not plan.actionable


# -- hiring ---------------------------------------------------------------

def test_a_hire_is_a_DRAG_and_not_a_click(resolved):
    plan = plan_hire(_Hire(0), 0, resolved)
    assert plan.actionable
    assert len(plan.steps) == 1
    assert plan.steps[0].kind == "drag"
    assert plan.steps[0].screen != plan.steps[0].to


def test_a_hire_drags_from_the_recruits_own_slot(resolved):
    """`Hire.index` is the position in the save's recruit list, which is the
    order the panel draws them. Two hires must not grab the same portrait."""
    a = plan_hire(_Hire(0), 0, resolved).steps[0].screen
    b = plan_hire(_Hire(1), 0, resolved).steps[0].screen
    assert a != b


def test_a_roster_row_past_the_visible_list_is_refused(resolved):
    """The roster panel holds 8 and then scrolls. A ninth row would drop the
    hero below the panel, onto the Hamlet."""
    assert plan_hire(_Hire(0), 7, resolved).actionable
    plan = plan_hire(_Hire(0), 8, resolved)
    assert not plan.actionable
    assert "scroll" in plan.refusal


def test_a_hire_from_a_slot_that_does_not_resolve_is_refused(resolved):
    plan = plan_hire(_Hire(-1), 0, resolved)
    assert not plan.actionable


# -- the exchange ---------------------------------------------------------

def test_opening_the_exchange_is_one_MEASURED_click(resolved):
    """Opening it is safe on its own -- it reveals a widget and spends
    nothing -- and unlike the panel's contents this point was measured off a
    real frame."""
    plan = plan_open_exchange(resolved)
    assert plan.actionable
    assert len(plan.steps) == 1 and plan.steps[0].kind == "click"
    assert plan.steps[0].screen != (0, 0)


def test_the_toggle_is_NOT_where_the_panel_is_drawn(resolved):
    """Pinned so a future edit cannot quietly point the opener at the panel
    origin, which is the mistake that made the whole chain a no-op.

    `town_screen_layout.heirloom_exchange_pos` is where the PANEL is drawn --
    frame (227, 472) at 1280x720 -- while the toggle that opens it sits at
    (502, 674), out on the currency bar. Nothing opened the panel, so the
    first live town visit clicked `exchange_from(0)` at (279, 545) on a bare
    Hamlet. The wallet, correctly, did not move. A trade SPENDS heirlooms, so
    a wrong click chain here is worse than one anywhere else in the town --
    which is why this is pinned rather than left to the gate that used to
    cover it.
    """
    toggle = townlayout.exchange_button()
    assert toggle != townlayout.exchange_from(0, GAME_DIR)
    assert toggle[1] > 0.9, "the toggle lives on the bottom currency bar"


@pytest.mark.skipif(not townlayout.EXCHANGE_MEASURED,
                    reason="exchange panel geometry is not verified yet")
def test_a_trade_already_on_its_type_and_count_is_ONE_click(resolved):
    """The panel is two spinners and a confirm, not a give/get pair of
    columns. With the type already showing and the count already right, the
    only thing left to send is the confirm on the destination row."""
    plan = plan_exchange(Trade(Rate("crest", 3, "deed", 1), 1), resolved,
                         cycles=0, count_clicks=0)
    assert plan.actionable
    assert [s.kind for s in plan.steps] == ["click"]
    assert plan.steps[-1].label == "confirm the trade"


@pytest.mark.skipif(not townlayout.EXCHANGE_MEASURED,
                    reason="exchange panel geometry is not verified yet")
def test_every_spin_and_every_extra_is_one_more_click(resolved):
    """`cycles` walks the type spinner and `count_clicks` walks the count
    spinner, so the plan is exactly `cycles + count_clicks + 1` long. Both
    come from the CALLER, because where the spinners already stand is a
    property of the live panel and a plan is data."""
    for cycles in (0, 1, 4):
        for extra in (0, 1, 3):
            plan = plan_exchange(Trade(Rate("crest", 3, "deed", 1), 1),
                                 resolved, cycles=cycles, count_clicks=extra)
            assert plan.actionable, (cycles, extra)
            assert len(plan.steps) == cycles + extra + 1, (cycles, extra)


def test_a_walk_past_the_end_of_the_cycle_is_refused(resolved):
    """The spinner cycles through five positions and comes back to bust, so
    more clicks than that is never a walk to a type -- it is a caller that
    lost count."""
    plan = plan_exchange(Trade(Rate("crest", 3, "deed", 1), 1), resolved,
                         cycles=len(townlayout.EXCHANGE_CYCLE) + 1,
                         count_clicks=0)
    assert not plan.actionable


def test_an_absurd_multiple_is_refused(resolved):
    """A trade needing many arrows means the solver picked a bad route, and
    machine-gunning a widget is how a UI reaches a state nobody can read."""
    plan = plan_exchange(Trade(Rate("crest", 3, "deed", 1), 1), resolved,
                         cycles=0, count_clicks=MAX_EXCHANGE_CLICKS + 1)
    assert not plan.actionable


def test_a_non_heirloom_currency_is_refused(resolved):
    """Gold is in the same `currency_cost` array and must never reach the
    exchange widget."""
    plan = plan_exchange(Trade(Rate("gold", 100, "deed", 1), 1), resolved,
                         cycles=0, count_clicks=0)
    assert not plan.actionable


def test_the_give_and_get_columns_differ_per_currency(resolved):
    """The declared reading: `HEIRLOOMS` order is the widget's column order.
    What is pinned here is only that the four are DISTINCT -- if they were
    not, every trade would click the same column and the wallet check could
    never tell which reading was wrong.

    Asserted on the GEOMETRY rather than on a plan, so it keeps its meaning
    while `plan_exchange` is gated."""
    firsts = {}
    for i, name in enumerate(HEIRLOOMS):
        pt = townlayout.exchange_from(i, GAME_DIR)
        assert pt is not None, name
        firsts[name] = pt
    assert len(set(firsts.values())) == len(firsts)


# -- nothing degenerate ---------------------------------------------------

def test_no_town_plan_ever_emits_a_degenerate_click(resolved):
    """(0,0) is the tell for an unresolved coordinate, and a derived point can
    be plausibly wrong in a way a clicked one cannot."""
    # Unmeasured buildings refuse; a refusal has no steps, so it passes
    # trivially -- which is the point. Both kinds are swept.
    plans = [plan_open_building(n, resolved) for n in townlayout.BUILDINGS]
    plans.append(plan_close_building(resolved))
    plans += [plan_buy_upgrade(_Purchase(_node(c)), t, resolved)
              for c in "abcde" for t in range(3)]
    plans += [plan_hire(_Hire(i), r, resolved)
              for i in range(-1, 6) for r in range(-1, 10)]
    plans += [plan_exchange(Trade(Rate(a, 2, b, 3), n), resolved,
                            cycles=0, count_clicks=0)
              for a in HEIRLOOMS for b in HEIRLOOMS for n in (0, 1, 3, 99)]
    for plan in plans:
        for step in plan.steps:
            if step.kind in ("click", "drag"):
                assert step.screen != (0, 0), plan
                if step.kind == "drag":
                    assert step.to != (0, 0), plan


def test_a_refusal_sends_nothing(resolved):
    """The rule the whole of layer 3 rests on: no keystrokes before a
    refusal."""
    for plan in (plan_open_building("nope", resolved),
                 plan_hire(_Hire(0), 99, resolved),
                 plan_buy_upgrade(_Purchase(_node("z")), 0, resolved),
                 plan_exchange(Trade(Rate("gold", 1, "deed", 1), 1),
                               resolved, cycles=0, count_clicks=0)):
        assert not plan.actionable
        assert plan.steps == ()


def test_every_town_point_moves_with_the_window(resolved):
    """The letterbox correction applies to derived points exactly as it does
    to clicked ones -- `Resolved.at` is the single seam, so this is really a
    check that nothing bypassed it."""
    other = load_layout().resolve(OfflineWindow(size=(1600, 900),
                                                origin=(500, 400)))
    a = plan_open_building("stage_coach", resolved).steps[0].screen
    b = plan_open_building("stage_coach", other).steps[0].screen
    assert (b[0] - a[0], b[1] - a[1]) == (400, 350)
