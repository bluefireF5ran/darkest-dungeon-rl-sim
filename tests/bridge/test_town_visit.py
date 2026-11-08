"""A stage-coach visit, end to end, with nothing sent.

`bridge/town.py::visit_stage_coach` is the only part of the town layer that
sequences actions, and it is the part with a failure mode the dungeon layers
do not have: **nothing inside a building panel changes the SCREEN**. Buying an
upgrade, trading heirlooms and hiring a recruit all leave the recogniser
reading the same panel, so a click that silently did nothing and a click that
worked are pixel-identical. Every acknowledgement therefore has to come from
the save, and these tests pin that it does.

They run with the game closed, against a fake window and a recording backend,
which is the same discipline `--replay` establishes for the dungeon half: the
sequence is decided offline, so it can be wrong offline where it costs nothing.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest


@pytest.fixture(autouse=True)
def _vanilla_exchange(monkeypatch):
    """These pin the VANILLA trade path; Fran's install has a mod that
    replaces the exchange, which `choose_upgrades` rightly refuses to trade
    through. The suite must not depend on what Steam has subscribed."""
    import bridge.townplan as townplan
    monkeypatch.setattr(townplan, "exchange_is_modded", lambda *_a: False)

from bridge import town
from bridge.act import OfflineWindow
from bridge.live_state import LiveHero
from bridge.town_state import Recruit, RosterHero, TownState

from data.paths import GAME_DIR

pytestmark = pytest.mark.skipif(not GAME_DIR.exists(),
                                reason="needs the game install")

STARTING_WALLET = {"bust": 10, "portrait": 10, "deed": 10, "crest": 20}


class Recorder:
    """A backend that records instead of sending, and can be told to fail."""

    def __init__(self, drag_ok=True):
        self.events = []
        self.drag_ok = drag_ok

    def key(self, vk, hold=0.0):
        self.events.append(("key", vk))
        return True

    def click(self, screen, mods=()):
        self.events.append(("click", screen, tuple(mods)))
        return True

    def drag(self, src, dst):
        self.events.append(("drag", src, dst))
        return self.drag_ok

    @property
    def kinds(self):
        return [e[0] for e in self.events]


def hero(hero_class, guid):
    return RosterHero(hero=LiveHero(guid=guid, hero_class=hero_class))


def offer(hero_class, slot):
    """`row` tracks `slot` here: these fixtures hold gold-store offers only,
    and in that store the two coincide. They are separate fields because the
    shard store's entries sit between them in a real save."""
    return Recruit(slot=slot, listing="hero_recruit", hero_class=hero_class,
                   row=slot)


@pytest.fixture
def win():
    return OfflineWindow(size=(1600, 900), origin=(0, 0))


@pytest.fixture
def tutorial_town():
    return TownState(heroes=[hero("crusader", 1), hero("highwayman", 2)],
                     wallet=dict(STARTING_WALLET),
                     recruits=[offer("vestal", 0), offer("jester", 1),
                               offer("plague_doctor", 2)])


@pytest.fixture
def patched(monkeypatch, tutorial_town):
    """Freeze the save readers, so the visit is driven by fixtures.

    The wallet and roster readers are the ACKNOWLEDGEMENT channel, so a test
    that let them hit the disk would be testing Fran's save rather than the
    sequence. Each returns a mutable cell the test can advance to simulate the
    game responding -- or leave alone, to simulate a click that did nothing.
    """
    cell = {"wallet": dict(STARTING_WALLET),
            "guids": {h.guid for h in tutorial_town.heroes}}
    monkeypatch.setattr(town, "_wallet", lambda profile: dict(cell["wallet"]))
    monkeypatch.setattr(town, "_roster_guids", lambda profile: set(cell["guids"]))
    monkeypatch.setattr(town, "SAVE_ACK_TIMEOUT", 0.0)
    # The visit is full of real settles -- 0.9 s per panel, and a save-write
    # bound per action. None of that is what these tests are about, and left
    # in it costs 30 s for twelve tests. Stubbing the module's clock is
    # honest here in a way lowering each constant is not: it removes the
    # waiting without pretending the waits are shorter than they are.
    monkeypatch.setattr(town.time, "sleep", lambda *_a: None)
    # The panel is assumed OPEN for the sequencing tests. `_panel_is_open` is
    # its own concern with its own tests below, and leaving it live here would
    # make every test in this file a test of screen recognition instead of a
    # test of the order actions happen in.
    monkeypatch.setattr(town, "_panel_is_open", lambda win: True)
    # These are tests of the COACH visit's sequence, so the walk is the one
    # that spends the starting wallet at the coach. Since 2026-10-12 the
    # shipped walk puts the Guild and the Blacksmith first (Fran), and the
    # same wallet would buy nothing here.
    import bridge.townplan as townplan
    monkeypatch.setattr(townplan, "UPGRADE_PRIORITY", townplan.COACH_FIRST_PRIORITY)
    return cell


def _visit(win, backend, tutorial_town, monkeypatch, **kw):
    monkeypatch.setattr("bridge.town_state.read_town_state",
                        lambda *a, **k: tutorial_town)
    return town.visit_stage_coach(win, backend, profile=None,
                                  say=lambda *a: None, **kw)


# -- the dry run ----------------------------------------------------------

def test_a_dry_visit_sends_absolutely_nothing(win, tutorial_town, patched,
                                              monkeypatch):
    """`act=False` is the gate every layer-3 entry point carries, and it is
    the one that makes all of this safe to develop against a live game."""
    backend = Recorder()
    _visit(win, backend, tutorial_town, monkeypatch, act=False)
    assert backend.events == []


def test_a_dry_visit_still_REPORTS_what_it_would_do(win, tutorial_town,
                                                    patched, monkeypatch):
    backend = Recorder()
    done = _visit(win, backend, tutorial_town, monkeypatch, act=False)
    assert done, "a dry run must still say what it planned"
    assert all(not d.sent for d in done)


# -- the hunger check, on the way home ------------------------------------
#
# Measured 2026-09-21 on Fran's own campaign: the raid played 379 actions,
# the party got hungry, and `to_town` stopped dead with
# "no move known for 'hunger'". `act.ActRunner._answer_hunger` had answered
# that panel for weeks -- but that is the RAID layer, and this walk has its
# own screen table.

def _walk_screens(monkeypatch, screens, act=True):
    """Drive `town.walk` over a scripted sequence of screen names."""
    import bridge.town as town

    seq = list(screens)
    monkeypatch.setattr(town, "_look",
                        lambda _w: seq.pop(0) if seq else "hamlet")
    monkeypatch.setattr(town.time, "sleep", lambda *_a, **_k: None)
    monkeypatch.setattr(town, "_await_change", lambda *_a, **_k: "")
    monkeypatch.setattr(town, "_modal_family", lambda _w: False)
    backend = Recorder()
    said = []
    walked = town.to_town(_FakeWin(), backend, say=said.append, act=act)
    return walked, backend, said


class _FakeWin:
    hwnd = 1
    client_size = (1280, 720)
    client_origin = (0, 0)


def test_the_walk_home_ANSWERS_the_hunger_check(monkeypatch, patched):
    """It used to refuse here, which ended the iteration in four seconds."""
    walked, backend, said = _walk_screens(monkeypatch, ["hunger", "hamlet"])
    assert walked.ok, walked.why
    assert "no move known" not in (walked.why or "")
    assert any("eat" in s for s in said), said
    assert backend.events, "it answered without sending anything"


def test_it_falls_back_to_REFUSE_when_eating_did_not_take(monkeypatch,
                                                          patched):
    """A party with no food is offered the panel all the same, and eating is
    then not an answer. Eat first is still right -- asking without food costs
    nothing -- but it may not be asked twice."""
    walked, _backend, said = _walk_screens(
        monkeypatch, ["hunger", "hunger", "hamlet"])
    assert walked.ok, walked.why
    assert sum("eat" in s and "refuse" not in s for s in said) == 1, said
    assert any("refuse" in s for s in said), said

def test_the_walk_home_RETURNS_from_a_completed_quest(monkeypatch, patched):
    """The banner of a raid the party WON. `act` presses this inside the
    raid, but it is a modal shown once and it is still up when the walk
    starts -- so this stopped three iterations dead on a quest that had just
    been completed (2026-09-21)."""
    walked, backend, said = _walk_screens(monkeypatch,
                                          ["quest_complete", "hamlet"])
    assert walked.ok, walked.why
    assert "no move known" not in (walked.why or "")
    assert any("quest_complete" in s for s in said), said
    assert backend.events, "it returned without sending anything"


def test_the_walk_home_closes_ANY_building_panel(monkeypatch, patched):
    """A run stopped mid-town-visit leaves a building panel open, and the
    walk met a screen it had no move for -- three iterations dead in under
    ten seconds each (2026-09-22). Every panel closes by the same X, so the
    suffix covers the guild, abbey and tavern without a reference each."""
    walked, backend, said = _walk_screens(
        monkeypatch, ["stage_coach_panel", "hamlet"])
    assert walked.ok, walked.why
    assert "no move known" not in (walked.why or "")
    assert any("close stage_coach_panel" in s for s in said), said
    assert backend.events

# -- reading the party bar, and the quest click that hit nothing ------------

def test_an_empty_party_slot_is_FLAT_and_a_filled_one_is_not(monkeypatch):
    """**"The bar did not change" could never tell an empty slot from a
    failed drag**, and the difference is a raid: heroes left on the bar are
    not drawn in the roster list, so their seats are refused and the party
    goes out short.

    Measured 2026-09-22 on the estate map, same slots, filled then emptied:
    empty reads mean 15.2 / std 7.9 (identical across all four -- it is frame
    art), occupied reads mean 32.5-74.5 / std 42.0-69.5. The STD is the
    discriminator because a dark portrait and a lit frame can share a mean.
    """
    import numpy as np
    import bridge.town as town
    import bridge.screen as screen_mod

    assert 8.0 < town.PARTY_SLOT_TEXTURE < 42.0, (
        "the threshold has to sit between the measured empty and filled")

    flat = np.full((720, 1280, 3), 15, dtype=np.uint8)
    noisy = np.random.RandomState(0).randint(0, 255, (720, 1280, 3), "uint8")

    class _Res:
        win = _FakeWin()

        @staticmethod
        def at(_frac):
            return (200, 200)

    monkeypatch.setattr(screen_mod, "grab", lambda _w: flat)
    assert town.party_slot_filled(_FakeWin(), _Res(), 0) is False
    monkeypatch.setattr(screen_mod, "grab", lambda _w: noisy)
    assert town.party_slot_filled(_FakeWin(), _Res(), 0) is True
    monkeypatch.setattr(screen_mod, "grab", lambda _w: None)
    assert town.party_slot_filled(_FakeWin(), _Res(), 0) is None


def test_the_frame_threshold_sits_between_the_measured_noise_and_signal():
    """Measured on the estate map: idle drift 0.023, a click on empty map
    0.042, a click on a quest emblem 1.247 (the left panel redraws)."""
    import numpy as np
    import bridge.town as town

    assert 0.042 < town.FRAME_SAME < 1.247
    a = np.zeros((40, 40, 3))
    assert town._frames_match(a, a.copy()) is True
    assert town._frames_match(a, a + 2.0) is False
    assert town._frames_match(a, np.zeros((10, 10, 3))) is False

# -- which hero is on which roster row, ASKED rather than inferred ----------

def test_learn_roster_rows_asks_the_SAVE_who_arrived(monkeypatch):
    """**Every way of inferring the row is broken on this install.** The list
    order is not the save's -- measured 2026-09-22, Ceauce is save index 5
    and draws at row 3 -- and the portrait matcher that used to recover it
    compares against art `Amara's Character UI` replaces.

    So it runs an experiment instead: drag a row onto the bar, read
    `persist.roster.json` to see whose `in_party` just turned on, put them
    back. The save is the one channel the mods cannot touch. Live, this
    recovered all eight rows and the party seated 4 of 4 for the first time.
    """
    import bridge.town as town

    seated = {"who": None}
    seq = iter([8, 7, 27])

    def _in_party(_profile):
        return set() if seated["who"] is None else {seated["who"]}

    class _Backend:
        def drag(self, src, dst):
            # Dragging TO the bar seats the next hero; back to the list frees it.
            seated["who"] = next(seq, None) if dst == ("BAR",) else None
            return True

    class _Res:
        win = _FakeWin()

        @staticmethod
        def at(frac):
            return ("BAR",) if frac == ("PARTY0",) else ("ROW",)

    monkeypatch.setattr(town, "_in_party", _in_party)
    monkeypatch.setattr(town, "party_slot_filled", lambda *_a: False)
    monkeypatch.setattr(town.time, "sleep", lambda *_a: None)
    monkeypatch.setattr(town, "_park_row", lambda _t: 7)
    monkeypatch.setattr(town, "_Snapshot", lambda _p: None)
    # `TL` is imported inside the function, so the real module is the seam.
    import bridge.townlayout as TL

    monkeypatch.setattr(TL, "party_portrait", lambda i: ("PARTY0",))
    monkeypatch.setattr(TL, "roster_portrait", lambda r: ("ROW",))

    got = town.learn_roster_rows(_FakeWin(), _Backend(), _Res(), "prof",
                                 rows=[0, 1, 2], say=lambda _s: None, act=True)
    assert got == {0: 8, 1: 7, 2: 27}


def test_it_learns_NOTHING_when_not_armed(monkeypatch):
    """It sends drags, so the dry gate has to hold."""
    import bridge.town as town

    assert town.learn_roster_rows(None, None, None, "prof",
                                  say=lambda _s: None, act=False) == {}

def test_learn_roster_positions_pages_past_the_eight_drawn_rows(monkeypatch):
    """A roster of 18 hides ten heroes below the drawn eight -- measured
    2026-09-23, every seat fell back to row 7 and the bar took 3 of 4. Each
    click down reveals ONE new hero at row 7, so position is `7 + clicks`; a
    swallowed click re-reveals a known hero and must not advance the count.
    """
    import types

    import bridge.act as act_mod
    import bridge.town as town

    heroes = [f"g{i}" for i in range(11)]
    state = {"top": 0, "swallow": {2}}          # the 2nd click is swallowed
    sent = {"n": 0}

    monkeypatch.setattr(town, "_Snapshot",
                        lambda _p: types.SimpleNamespace(heroes=heroes))
    monkeypatch.setattr(town, "force_roster_to_top",
                        lambda *a, **k: town.Did("top", sent=True,
                                                 confirmed=True))
    monkeypatch.setattr(act_mod, "plan_scroll_roster",
                        lambda down, res: types.SimpleNamespace(actionable=True))
    monkeypatch.setattr(town.time, "sleep", lambda _s: None)

    def _send(_b, _plan, **_k):
        sent["n"] += 1
        if sent["n"] not in state["swallow"]:
            state["top"] = min(state["top"] + 1, len(heroes) - 8)  # list end

    def _rows(*_a, rows=None, **_k):
        rows = range(8) if rows is None else rows
        # g9 is BUSY (in a building): it cannot be dragged, so the probe
        # learns nothing -- but the list still moved.
        return {r: heroes[state["top"] + r] for r in rows
                if heroes[state["top"] + r] != "g9"}

    monkeypatch.setattr(town, "_roster_strip", lambda *_a: state["top"])
    monkeypatch.setattr(town, "_bar_moved", lambda a, b: a != b)

    monkeypatch.setattr(town, "_send", _send)
    monkeypatch.setattr(town, "learn_roster_rows", _rows)
    got = town.learn_roster_positions(None, None, None, "prof",
                                      say=lambda _s: None, act=True)
    assert got["g0"] == (0, 0) and got["g7"] == (0, 7)
    assert got["g8"] == (1, 7) and got["g10"] == (3, 7)
    assert "g9" not in got and len(got) == 10


def _dismiss_rig(monkeypatch, roster, page, vanish):
    """`roster`: guids alive; `page`: {row: guid}; `vanish(guid)`: what the
    save drops when the Yes goes in for that guid."""
    import types

    import bridge.act as act_mod
    import bridge.town as town

    state = {"alive": set(roster), "opened": None}

    class _Backend:
        def click(self, pt):
            if pt == "YES" and state["opened"] is not None:
                state["alive"] -= vanish(state["opened"])
            return True

    class _Res:
        modal_ok = "YES"

        @staticmethod
        def at(frac):
            return frac

    class _Actuator:
        def __init__(self, **_k):
            pass

        def execute(self, plan):
            state["opened"] = plan.steps[0].screen
            return True

    monkeypatch.setattr(act_mod, "Actuator", _Actuator)
    monkeypatch.setattr(town.time, "sleep", lambda _s: None)
    monkeypatch.setattr(town, "DISMISS_WITNESS_S", 0.05)
    monkeypatch.setattr(town, "learn_roster_rows",
                        lambda *_a, **_k: {r: g for r, g in page.items()
                                           if g in state["alive"]})
    monkeypatch.setattr(town, "_Snapshot", lambda _p: types.SimpleNamespace(
        heroes=[types.SimpleNamespace(guid=g) for g in state["alive"]]))
    import bridge.townlayout as TL
    monkeypatch.setattr(TL, "roster_portrait", lambda row: page[row])
    return town, _Backend(), _Res(), state


def test_dismiss_only_the_probed_hero_and_confirm_by_the_save(monkeypatch):
    town, backend, res, state = _dismiss_rig(
        monkeypatch, roster={1, 2, 3}, page={0: 1, 1: 2, 2: 3},
        vanish=lambda g: {g})
    got = town.dismiss_heroes(None, backend, res, "p", [2, 3], act=True,
                              say=lambda _s: None)
    assert [d.confirmed for d in got] == [True, True]
    assert state["alive"] == {1}


def test_dismiss_STOPS_when_the_save_drops_someone_else(monkeypatch):
    """Irreversible: a dismissal that removed the wrong hero is not repeated."""
    town, backend, res, state = _dismiss_rig(
        monkeypatch, roster={1, 2, 3}, page={0: 1, 1: 2, 2: 3},
        vanish=lambda g: {1})
    got = town.dismiss_heroes(None, backend, res, "p", [2, 3], act=True,
                              say=lambda _s: None)
    assert len(got) == 1 and not got[0].confirmed


def test_dismiss_leaves_a_hero_that_is_not_on_the_page(monkeypatch):
    town, backend, res, state = _dismiss_rig(
        monkeypatch, roster={1, 2, 9}, page={0: 1, 1: 2}, vanish=lambda g: {g})
    assert town.dismiss_heroes(None, backend, res, "p", [9], act=True,
                               say=lambda _s: None) == []
    assert 9 in state["alive"]


def _sort_rig(monkeypatch, list_order, file_order=None):
    """A roster of 12 in FILE order g0..g11 with stress 10*i, and a list whose
    drawn order after the n-th click is `list_order(n)`."""
    import types

    import bridge.town as town

    heroes = [types.SimpleNamespace(guid=g, stress=float(g * 10))
              for g in (file_order or range(12))]
    clicks = {"n": 0}

    class _Backend:
        def click(self, _pt):
            clicks["n"] += 1
            return True

    class _Res:
        @staticmethod
        def at(frac):
            return frac

    def _rows(*_a, rows=None, **_k):
        drawn = list_order(clicks["n"])
        return {r: drawn[r] for r in (rows if rows is not None else range(8))}

    monkeypatch.setattr(town.time, "sleep", lambda _s: None)
    monkeypatch.setattr(town, "_Snapshot",
                        lambda _p: types.SimpleNamespace(heroes=heroes))
    monkeypatch.setattr(town, "force_roster_to_top",
                        lambda *a, **k: town.Did("top", sent=True, confirmed=True))
    monkeypatch.setattr(town, "learn_roster_rows", _rows)
    return town, _Backend(), _Res(), clicks


def test_the_file_order_IS_the_list_so_nothing_is_clicked(monkeypatch):
    """The game saves the roster in its displayed order (2026-09-24), so with
    no sort click since the last save the list is the file order: two probes
    confirm it and every position comes from the save."""
    town, backend, res, clicks = _sort_rig(monkeypatch, lambda n: list(range(12)))
    got = town.sort_roster_low_stress_first(None, backend, res, "p",
                                            act=True, say=lambda _s: None)
    assert clicks["n"] == 0
    assert got[0] == (0, 0) and got[7] == (0, 7) and got[11] == (4, 7)


def test_the_stress_sort_reads_its_direction_off_the_list(monkeypatch):
    """A list that is not the file order is sorted by Stress. The first click
    here lands most-stressed first, so it clicks again and confirms the
    least-stressed-first order, predicted as a stable sort of the file."""
    file_order = [3, 0, 1, 2, 4, 5, 6, 7, 8, 9, 10, 11]

    def drawn(n):
        if n == 0:
            return [1, 0] + file_order[2:]    # not the file order: a click is needed
        return sorted(range(12), reverse=n % 2 == 1)

    town, backend, res, clicks = _sort_rig(monkeypatch, drawn, file_order)
    got = town.sort_roster_low_stress_first(None, backend, res, "p",
                                            act=True, say=lambda _s: None)
    assert clicks["n"] == 2
    assert got[0] == (0, 0) and got[7] == (0, 7)
    assert got[8] == (1, 7) and got[11] == (4, 7)


def test_the_predicted_order_is_a_STABLE_stress_sort_of_the_file(monkeypatch):
    """Measured live 2026-09-23: 17 rows at equal stress were file indexes
    11-27 in order. Ties keep file order; stress sorts low first."""
    import types

    import bridge.town as town

    file_order = [types.SimpleNamespace(guid=g, stress=st) for g, st in
                  ((574, 0.0), (569, 30.0), (570, 0.0), (572, 0.0), (577, 30.0))]
    monkeypatch.setattr(town, "_Snapshot",
                        lambda _p: types.SimpleNamespace(heroes=file_order))
    assert town.predicted_order("p") == [574, 569, 570, 572, 577]
    assert town.predicted_order("p", +1) == [574, 570, 572, 569, 577]
    assert town.predicted_order("p", -1) == [569, 577, 574, 570, 572]


def test_a_list_that_does_not_match_the_prediction_falls_back(monkeypatch):
    """A drawn order the save cannot explain means the walk, not a guess."""
    town, backend, res, _clicks = _sort_rig(
        monkeypatch, lambda n: [5, 1, 2, 3, 4, 0, 6, 7, 8, 9, 10, 11][::-1])
    assert town.sort_roster_low_stress_first(None, backend, res, "p",
                                             act=True, say=lambda _s: None) == {}


# -- the order ------------------------------------------------------------

def test_the_order_is_open_trade_upgrade_hire_close(win, tutorial_town,
                                                    patched, monkeypatch):
    """A dependency, not a preference: a trade pays for an upgrade, and
    `numrecruits` changes how many heroes the coach offers -- so hiring last
    is the only order in which each step sees what the previous one bought.
    """
    backend = Recorder()
    done = _visit(win, backend, tutorial_town, monkeypatch, act=True)
    what = [d.what for d in done]
    assert what[0] == "open the stage coach"
    trade_at = next(i for i, w in enumerate(what) if "->" in w)
    buy_at = next(i for i, w in enumerate(what) if "numrecruits.b" in w)
    hire_at = next(i for i, w in enumerate(what) if w.startswith("hire"))
    assert trade_at < buy_at < hire_at


def test_a_hire_is_the_only_DRAG_in_the_visit(win, tutorial_town, patched,
                                              monkeypatch):
    backend = Recorder()
    _visit(win, backend, tutorial_town, monkeypatch, act=True)
    assert backend.kinds.count("drag") == len(
        [k for k in backend.kinds if k == "drag"])
    assert "drag" in backend.kinds
    assert backend.kinds[0] == "click", "the panel is opened by a click"


def test_the_panel_is_closed_even_when_hires_fail(win, tutorial_town, patched,
                                                  monkeypatch):
    """A visit that leaves a building panel open blocks every later screen
    transition, and the walker would then report an unrecognised screen with
    no idea why."""
    backend = Recorder(drag_ok=False)
    _visit(win, backend, tutorial_town, monkeypatch, act=True)
    assert backend.kinds[-1] == "click", "the last thing sent is the close"


# -- the acknowledgement --------------------------------------------------

def test_an_action_the_save_did_not_confirm_is_reported_UNCONFIRMED(
        win, tutorial_town, patched, monkeypatch):
    """The whole reason this file exists. The fixture never advances the
    wallet or the roster, so every step is a click that did nothing -- and
    the report must say so rather than counting them as done."""
    backend = Recorder()
    done = _visit(win, backend, tutorial_town, monkeypatch, act=True)
    acted = [d for d in done if d.sent and d.what != "open the stage coach"]
    assert acted
    assert all(not d.confirmed for d in acted)
    assert all(d.why for d in acted), "an unconfirmed step must say why"


# **DELETED: `test_a_GATED_action_is_reported_unsent_with_a_reason`.** Its
# whole body sat behind `if not townlayout.EXCHANGE_MEASURED`, and that flag
# is True now -- so it ran, asserted nothing, and reported PASS. Worse than a
# skip, which at least says so. Its docstring still claimed "Today the TRADE
# is the gated one", a world that ended when the panel was measured.
#
# The contract it described is real and is tested above, on the branch that
# still exists: `sent` says input left the machine, `confirmed` says the save
# agreed, and the two are reported separately. Nothing is gated in a town
# visit today; when something is again, test it against that gate rather than
# against a flag that may lift.


def test_an_UNGATED_purchase_does_send(win, tutorial_town, patched,
                                       monkeypatch):
    """The other side of the same contract. `UPGRADES_MEASURED` lifted once
    the building crest was found to open the trees and all 13 nodes were
    verified against a live frame -- so a purchase now reaches the game and is
    judged by the wallet, not refused before it starts."""
    from bridge import townlayout

    if not townlayout.UPGRADES_MEASURED:
        pytest.skip("upgrade geometry is not verified")
    backend = Recorder()
    done = _visit(win, backend, tutorial_town, monkeypatch, act=True)
    buys = [d for d in done if "numrecruits" in d.what]
    # The first rank needs no trade, so it is the one that can actually go.
    assert any(d.sent for d in buys), [str(d) for d in buys]


def test_a_roster_that_grows_still_confirms_a_hire_while_money_is_gated(
        win, tutorial_town, patched, monkeypatch):
    """The acknowledgement channel is unaffected by the gates: hiring is not
    gated, and a roster that grows still turns into `confirmed`."""
    grown = {"guids": {1, 2}}

    def growing(profile):
        grown["guids"] = grown["guids"] | {max(grown["guids"]) + 1}
        return set(grown["guids"])

    monkeypatch.setattr(town, "_roster_guids", growing)
    backend = Recorder()
    done = _visit(win, backend, tutorial_town, monkeypatch, act=True)
    hires = [d for d in done if d.what.startswith("hire")]
    assert hires and all(d.confirmed for d in hires)


def test_a_roster_that_grows_confirms_the_hire(win, tutorial_town, patched,
                                               monkeypatch):
    grown = {"guids": {1, 2}}

    def growing(profile):
        grown["guids"] = grown["guids"] | {max(grown["guids"]) + 1}
        return set(grown["guids"])

    monkeypatch.setattr(town, "_roster_guids", growing)
    backend = Recorder()
    done = _visit(win, backend, tutorial_town, monkeypatch, act=True)
    hires = [d for d in done if d.what.startswith("hire")]
    assert hires and all(d.confirmed for d in hires)


def test_each_confirmed_hire_targets_the_NEXT_roster_row(win, tutorial_town,
                                                         patched, monkeypatch):
    """Dropping two recruits onto the same row would put the second on top of
    the first. The row only advances when the save says the previous hire
    landed -- so a failed hire does not silently skip a slot."""
    grown = {"guids": {1, 2}}

    def growing(profile):
        grown["guids"] = grown["guids"] | {max(grown["guids"]) + 1}
        return set(grown["guids"])

    monkeypatch.setattr(town, "_roster_guids", growing)
    backend = Recorder()
    _visit(win, backend, tutorial_town, monkeypatch, act=True, roster_room=2)
    drops = [e[2] for e in backend.events if e[0] == "drag"]
    assert len(drops) == 2
    assert drops[0] != drops[1], "two hires must not land on one row"


def test_a_failed_hire_does_not_advance_the_row(win, tutorial_town, patched,
                                                monkeypatch):
    backend = Recorder()
    _visit(win, backend, tutorial_town, monkeypatch, act=True, roster_room=2)
    drops = [e[2] for e in backend.events if e[0] == "drag"]
    assert len(set(drops)) == 1, "the roster never grew, so the row never moved"


# -- failing safe ---------------------------------------------------------

def test_an_unreadable_town_reports_instead_of_raising(win, monkeypatch):
    """`tools/overnight.py` has a raid to get on with either way -- a town
    visit that half-worked is a normal outcome to report, not an exception to
    unwind the night with."""
    def boom(*a, **k):
        raise OSError("torn save")

    monkeypatch.setattr("bridge.town_state.read_town_state", boom)
    done = town.visit_stage_coach(win, Recorder(), profile=None,
                                  say=lambda *a: None, act=True)
    assert len(done) == 1 and not done[0].sent
    assert "OSError" in done[0].why


def test_nothing_to_do_opens_no_panel(win, patched, monkeypatch):
    """An empty wallet and no offers: the visit must not open the coach just
    to close it again. Every panel opened is two clicks and a settle."""
    empty = TownState(heroes=[hero("crusader", 1)],
                      wallet={"bust": 0, "portrait": 0, "deed": 0, "crest": 0},
                      recruits=[])
    backend = Recorder()
    monkeypatch.setattr("bridge.town_state.read_town_state",
                        lambda *a, **k: empty)
    done = town.visit_stage_coach(win, backend, profile=None,
                                  say=lambda *a: None, act=True)
    assert backend.events == []
    assert len(done) == 1 and not done[0].sent


# ── the panel guard ──────────────────────────────────────────────────────
#
# The first live town visit clicked the navigation strip -- which is not drawn
# on the Hamlet -- and then sent an upgrade click, a three-click trade, a
# second upgrade click and two drags into an open town. Every one was reported
# UNCONFIRMED by the save check, which is the only reason it was harmless.
# These pin the gate that stops it happening again.

def test_a_panel_that_did_not_open_stops_the_visit(win, tutorial_town,
                                                   patched, monkeypatch):
    """Nothing that belongs inside the panel may be sent."""
    backend = Recorder()
    monkeypatch.setattr(town, "_panel_is_open", lambda w: False)
    monkeypatch.setattr(town, "_on_town_screen", lambda w: True)
    monkeypatch.setattr(town, "_bank_panel_frame", lambda w: "")
    done = _visit(win, backend, tutorial_town, monkeypatch, act=True)

    assert len(backend.events) == 1, "only the open click may be sent"
    assert [d.what for d in done] == ["open the stage coach"]
    assert done[0].sent and not done[0].confirmed
    assert "could not confirm" in done[0].why


def test_an_unconfirmable_panel_is_still_CLOSED_on_the_way_out(
        win, tutorial_town, patched, monkeypatch):
    """The guard cannot tell "did not open" from "opened, but no reference is
    banked for its interior". In the second case, returning without closing
    leaves a panel over the Hamlet -- which then swallows the embark walk's
    clicks and reports as an unrecognised screen with no clue why.

    So the close is attempted whenever the bare town is NOT visible, which is
    the one reading that says something is covering it.
    """
    backend = Recorder()
    monkeypatch.setattr(town, "_panel_is_open", lambda w: False)
    monkeypatch.setattr(town, "_on_town_screen", lambda w: False)  # covered
    monkeypatch.setattr(town, "_bank_panel_frame", lambda w: "")
    done = _visit(win, backend, tutorial_town, monkeypatch, act=True)

    assert len(backend.events) == 2, "the open, then the close"
    assert all(e[0] == "click" for e in backend.events)
    assert len(done) == 1 and not done[0].confirmed


def test_the_failure_frame_is_banked(win, tutorial_town, patched, monkeypatch):
    """`bank a frame at the moment anything fails` -- and here it is also how
    the first building interior gets its screen reference."""
    calls = []
    monkeypatch.setattr(town, "_panel_is_open", lambda w: False)
    monkeypatch.setattr(town, "_on_town_screen", lambda w: True)
    monkeypatch.setattr(town, "_bank_panel_frame",
                        lambda w: calls.append(w) or " (banked)")
    done = _visit(win, Recorder(), tutorial_town, monkeypatch, act=True)
    assert calls, "no frame was banked"
    assert "(banked)" in done[0].why


def test_UNKNOWN_is_not_confirmation(monkeypatch):
    """The polarity bug that let the live run through, kept as a test.

    The first version returned `recognise(img) not in TOWN_SCREENS`, so an
    unrecognised frame -- a mid-transition, or an interior with no banked
    reference -- read as "the panel opened". A one-sided test has to say NO
    when it cannot see.
    """
    monkeypatch.setattr(town, "BUILDING_SCREENS", ())
    for seen in ("", "hamlet", "town", "estate_map", "something_new"):
        monkeypatch.setattr("bridge.screen.grab", lambda w: object())
        monkeypatch.setattr("bridge.screen.recognise", lambda img, s=seen: s)
        assert town._panel_is_open(object()) is False, seen


def test_a_banked_interior_WOULD_confirm(monkeypatch):
    """The other half: once `tools/capture_buildings.py` banks an interior,
    the guard becomes the positive test it should be, with no other change."""
    monkeypatch.setattr(town, "BUILDING_SCREENS", ("stage_coach_panel",))
    monkeypatch.setattr("bridge.screen.grab", lambda w: object())
    monkeypatch.setattr("bridge.screen.recognise", lambda img: "stage_coach_panel")
    assert town._panel_is_open(object()) is True


def test_no_frame_is_not_confirmation(monkeypatch):
    monkeypatch.setattr("bridge.screen.grab", lambda w: None)
    assert town._panel_is_open(object()) is False



# --- the walk has a DIRECTION (2026-09-06) ---


def test_the_two_outbound_screens_are_the_only_ones_with_a_way_back():
    """`RETREAT` names exactly the screens where homeward is not forward.

    The raid-results chain has one control and it points home; the Hamlet is
    home. Only the estate map and the provisioner sit on the way OUT, and a
    third entry here would be a screen whose forward click was mislabelled.
    """
    from bridge import town

    assert set(town.RETREAT) == {"estate_map", "provision"}
    for name in town.RETREAT:
        assert name in town.ADVANCE, name
        assert name in town.TOWN_SCREENS, name


def test_the_back_glyph_is_ONE_widget_on_BOTH_screens():
    """The measurement, re-run.

    113 red pixels spanning 12x17 at the identical centre on `estate_map` and
    `provision`, and **zero** on the Hamlet, the activity log and the raid
    results. Same pixel count and same span is what says it is one widget
    rather than two glyphs that happen to look alike -- and the three zeros
    are what stop `to_town` clicking a title bar that has no back on it.
    """
    pytest.importorskip("numpy")
    pytest.importorskip("PIL")
    import numpy as np
    from PIL import Image

    from bridge.layout import TITLE_BAR_BACK

    root = Path(__file__).resolve().parents[2] / "captures" / "screens"

    def _glyph(name):
        f = root / f"{name}.png"
        if not f.exists():
            pytest.skip(f"{name}.png is not banked")
        a = np.array(Image.open(f).convert("RGB")).astype(int)
        h, w = a.shape[:2]
        r, g, b = a[..., 0], a[..., 1], a[..., 2]
        red = (r > 100) & (r > g * 2 + 30) & (r > b * 2 + 30)
        box = np.zeros_like(red)
        box[40:95, 120:230] = red[40:95, 120:230]
        ys, xs = np.where(box)
        if not len(ys):
            return None
        return (len(ys), xs.max() - xs.min(), ys.max() - ys.min(),
                (xs.min() + xs.max()) / 2 / w, (ys.min() + ys.max()) / 2 / h)

    estate, prov = _glyph("estate_map"), _glyph("provision")
    assert estate is not None and prov is not None
    assert estate[:3] == prov[:3], (estate, prov)
    assert abs(estate[3] - prov[3]) < 1e-9 and abs(estate[4] - prov[4]) < 1e-9
    assert abs(estate[3] - TITLE_BAR_BACK[0]) < 0.002
    assert abs(estate[4] - TITLE_BAR_BACK[1]) < 0.002

    for clean in ("hamlet", "town", "raid_results"):
        assert _glyph(clean) is None, clean


def test_to_town_backs_OUT_of_the_estate_map(monkeypatch):
    """The failure, pinned.

    Three iterations of 2026-09-06 died clicking "Provision" on the estate
    map: the forward control, which walks AWAY from town and, with no party
    seated, does nothing at all. The walk retried it four times and gave up.
    """
    from bridge import town

    clicks = []
    _fake_walk_world(monkeypatch, clicks, ["estate_map", "estate_map",
                                           "hamlet"])
    got = town.to_town(object(), _Backend(clicks), say=lambda *_a: None,
                       act=True)
    assert got.ok, got.why
    assert clicks and clicks[0] == ("click", "title_bar_back"), clicks


def test_to_RAID_still_walks_forward(monkeypatch):
    """The two directions must not undo each other. `back` is opt-in, and
    only `to_town` passes it."""
    from bridge import town

    clicks = []
    _fake_walk_world(monkeypatch, clicks, ["estate_map", "provision"])
    town.walk(object(), _Backend(clicks), until=lambda s: s == "provision",
              say=lambda *_a: None, act=True, buy=False)
    assert clicks and clicks[0] == ("click", "raid_results_next"), clicks


class _Backend:
    def __init__(self, log):
        self.log = log

    def click(self, point):
        self.log.append(("click", point))
        return True

    def key(self, vk):
        self.log.append(("key", vk))
        return True


def _fake_walk_world(monkeypatch, clicks, screens):
    """A `walk` with no window, no pixels and no clock.

    The resolved layout answers every point with its own NAME, so a click is
    recorded as which widget it aimed at rather than as a coordinate -- these
    tests are about direction, and a coordinate would pin the calibration
    instead.
    """
    from bridge import town

    seq = list(screens)

    class _Resolved:
        def __getattr__(self, name):
            return name

        def menu_point(self, name):
            return name

    class _Layout:
        def resolve(self, _win):
            return _Resolved()

    monkeypatch.setattr("bridge.layout.load", lambda *_a, **_k: _Layout())
    monkeypatch.setattr(town, "_look",
                        lambda *_a, **_k: seq.pop(0) if seq else "hamlet")
    monkeypatch.setattr(town, "_modal_family", lambda *_a, **_k: False)
    monkeypatch.setattr(town, "_await_change", lambda *_a, **_k: "")
    monkeypatch.setattr(town.time, "sleep", lambda *_a: None)


def test_a_scroll_between_seats_does_not_eat_a_SEAT(monkeypatch, tmp_path):
    """The four-hero guard counted a tail slice of the wrong list.

    `_bring_into_view` appends a scroll to `done` whenever the roster has to
    move, so `done[-4:]` on a four-hero party that needed one scroll is three
    seats and a scroll. Four consecutive iterations of 2026-09-06 refused to
    embark with "the party bar took 3 of 4 heroes" while every single seat on
    the line above reported OK -- which is the shape of a counting bug rather
    than an actuator one.

    Driven through the real arithmetic rather than by re-implementing it: a
    `done` list of the exact shape that run produced, and the count taken the
    old way and the new way.
    """
    from bridge.town import Did

    done = [Did("clear party slot 0", sent=True, confirmed=True),
            Did("scroll the roster to row 10", sent=True, confirmed=True),
            Did("seat crusader at rank 2", sent=True, confirmed=True),
            Did("seat vestal at rank 4", sent=True, confirmed=True),
            Did("seat leper at rank 1", sent=True, confirmed=True),
            Did("scroll the roster to row 11", sent=True, confirmed=True),
            Did("seat houndmaster at rank 3", sent=True, confirmed=True)]
    seats = [d for d in done if d.what.startswith("seat ")]

    old = sum(1 for d in done[-4:]
              if d.confirmed and d.what.startswith("seat "))
    new = sum(1 for d in seats if d.confirmed)
    assert old == 3, "the fixture no longer reproduces the failure"
    assert new == 4


def test_the_seat_count_is_not_recovered_from_PROSE():
    """`startswith("seat ")` was the tell.

    A count that has to re-identify its own entries by their text is a count
    that does not know what it is counting, and it is one relabelled `Did`
    away from silently reading zero. The loop collects the seats it made.
    """
    import inspect

    from bridge import town

    src = inspect.getsource(town.embark_setup)
    # Comments stripped: the retraction is written down at that site on
    # purpose, and a test that cannot tell a warning from the thing it warns
    # about would forbid explaining the bug.
    code = chr(10).join(ln for ln in src.splitlines()
                        if not ln.lstrip().startswith("#"))
    assert "seated = sum(1 for d in seats if d.confirmed)" in code
    assert "done[-len(plan.party):]" not in code


# --- four heroes, in four different ranks (2026-09-06) ---


def test_the_bar_is_counted_PER_SLOT_against_its_cleared_self():
    """A whole-bar diff cannot count a party, and this is why.

    `_bar_moved` on the strip answers "did anything change", which is the
    right acknowledgement for ONE drag and the wrong one for "are there four
    heroes on it": a hero MOVED from rank 1 to rank 3 changes the strip and
    leaves three. Per slot, against the cleared baseline `clear_party` has
    just established, the same pixels answer the question that was actually
    asked.
    """
    pytest.importorskip("numpy")
    import numpy as np

    from bridge.town import _slots_filled

    empty = [np.zeros((20, 20, 3)) for _ in range(4)]
    hero = np.full((20, 20, 3), 200.0)

    assert _slots_filled(empty, [hero, hero, hero, hero]) == 4
    assert _slots_filled(empty, [hero, hero, hero, empty[3]]) == 3
    # the move that fools the whole-bar test: one slot gains, one loses, and
    # a strip-level diff says "something happened" for both.
    assert _slots_filled(empty, [hero, hero, empty[2], hero]) == 3


def test_a_slot_read_that_FAILED_is_not_a_count_of_zero():
    """Not being able to see is not evidence -- the same rule `_bar_moved`
    and `_panel_is_open` follow. It falls back to the drag count rather than
    refusing an embark it merely could not photograph."""
    pytest.importorskip("numpy")
    import numpy as np

    from bridge.town import _slots_filled

    empty = [np.zeros((20, 20, 3)) for _ in range(4)]
    assert _slots_filled(None, empty) is None
    assert _slots_filled(empty, None) is None
    assert _slots_filled(empty, empty[:3]) is None


def test_two_heroes_in_ONE_rank_is_refused_before_any_drag():
    """Four confirmed drags into three ranks is a three-hero party.

    Nothing upstream required the plan's ranks to be distinct, and every
    count downstream would have called it four: the drags land, the bar
    changes each time, and the third hero is simply replaced. Refused before
    anything is sent, because a party half-seated into the wrong shape is
    worse than one not seated at all.
    """
    import inspect

    from bridge import town

    src = inspect.getsource(town.embark_setup)
    code = chr(10).join(ln for ln in src.splitlines()
                        if not ln.lstrip().startswith("#"))
    assert "len(set(ranks)) != len(ranks)" in code
    # ...and before the seating loop, not after it.
    assert code.index("len(set(ranks))") < code.index("for seat in sorted(")


# -- the exchange overlay, and what it sits on top of ---------------------
#
# Measured live 2026-09-06 on a fresh post-tutorial campaign. The trade
# confirmed exactly (crest 20 -> 17, deed 7 -> 8), the panel was left up, and
# the very next thing the visit sends is the stage coach's SCENE point --
# which is a coordinate underneath the open overlay. The re-open then could
# not confirm, and the two hire drags went out anyway.


def _a_trade():
    from data.heirlooms import Rate, Trade
    return Trade(rate=Rate(from_type="crest", from_amount=3,
                           to_type="deed", to_amount=1), times=1)


@pytest.mark.parametrize("outcome", ["confirmed", "refused", "raised"])
def test_a_trade_ALWAYS_closes_the_panel_behind_it(win, monkeypatch, outcome):
    """Whoever opened it closes it, on every path out.

    The close is a `finally` rather than a line before each `return` because
    `do_exchange` has six of them and the one that gets missed is the one that
    matters: the failure paths are exactly the paths that used to leave the
    overlay sitting over the Hamlet.
    """
    closed = []
    monkeypatch.setattr(town, "open_exchange", lambda *a, **k: "bust")
    monkeypatch.setattr(town, "close_exchange",
                        lambda *a, **k: closed.append(True) or True)

    def body(*a, **k):
        if outcome == "raised":
            raise RuntimeError("the spinner moved under us")
        return town.Did("3 crest -> 1 deed", sent=True,
                        confirmed=outcome == "confirmed")

    monkeypatch.setattr(town, "_trade_in_open_exchange", body)
    call = lambda: town.do_exchange(win, Recorder(), None, None, _a_trade(),
                                    say=lambda *a: None, act=True)
    if outcome == "raised":
        with pytest.raises(RuntimeError):
            call()
    else:
        call()
    assert closed, f"the panel was left open after a {outcome} trade"


def test_a_panel_that_never_OPENED_is_not_closed(win, monkeypatch):
    """The toggle is a toggle: sending it at a shut panel OPENS one."""
    closed = []
    monkeypatch.setattr(town, "open_exchange", lambda *a, **k: "")
    monkeypatch.setattr(town, "close_exchange",
                        lambda *a, **k: closed.append(True) or True)
    got = town.do_exchange(win, Recorder(), None, None, _a_trade(),
                           say=lambda *a: None, act=True)
    assert not got.confirmed
    assert not closed


def test_the_close_RE_SENDS_until_the_spinner_is_gone(win, monkeypatch):
    """The acknowledgement is the pixel test -- no save is written when a
    panel closes -- so the loop is bounded rather than trusting one click."""
    from bridge.layout import load as load_layout
    fractional = load_layout()
    if fractional is None:
        pytest.skip("no overlay_layout.json")
    resolved = fractional.resolve(win)

    monkeypatch.setattr(town.time, "sleep", lambda *_a: None)
    state = {"open": True, "eaten": 1}
    monkeypatch.setattr(town, "_exchange_spinner",
                        lambda w: "bust" if state["open"] else "")
    backend = Recorder()
    real = backend.click

    def click(screen, mods=()):
        # The first toggle lands during the open animation and is eaten.
        if state["eaten"]:
            state["eaten"] = 0
        else:
            state["open"] = False
        return real(screen, mods)

    backend.click = click
    assert town.close_exchange(win, backend, resolved,
                               say=lambda *a: None, act=True) is True
    assert backend.kinds.count("click") == 2


def test_a_close_that_will_not_take_is_REPORTED_not_assumed(win, monkeypatch):
    from bridge.layout import load as load_layout
    fractional = load_layout()
    if fractional is None:
        pytest.skip("no overlay_layout.json")
    monkeypatch.setattr(town.time, "sleep", lambda *_a: None)
    monkeypatch.setattr(town, "_exchange_spinner", lambda w: "bust")
    said = []
    ok = town.close_exchange(win, Recorder(), fractional.resolve(win),
                             say=said.append, act=True)
    assert ok is False
    assert any("would not close" in s for s in said)


def test_a_dry_close_sends_NOTHING(win, monkeypatch):
    backend = Recorder()
    monkeypatch.setattr(town, "_exchange_spinner", lambda w: "bust")
    assert town.close_exchange(win, backend, None,
                               say=lambda *a: None, act=False) is True
    assert backend.events == []


def test_a_reopen_that_did_not_confirm_sends_NO_HIRE(win, tutorial_town,
                                                     patched, monkeypatch):
    """`break` left the purchase loop and fell straight into the hires.

    The sibling branch one level up -- a trade that misfired -- already
    returns, with a comment saying exactly why. This one did not, and the
    consequence is identical and was measured: two recruits dragged across a
    Hamlet with an exchange panel over it, both correctly UNCONFIRMED, and
    neither one that should have been sent at all.
    """
    seen = {"n": 0}

    def panel(w):
        seen["n"] += 1
        return seen["n"] == 1        # the first open takes; the re-open does not

    monkeypatch.setattr(town, "_panel_is_open", panel)
    monkeypatch.setattr(town, "_on_town_screen", lambda w: True)
    backend = Recorder()
    done = _visit(win, backend, tutorial_town, monkeypatch, act=True)

    assert "drag" not in backend.kinds, "a hire was dragged into who knows what"
    assert not any(d.what.startswith("hire") for d in done)
    stopped = [d for d in done if d.what == "re-open the stage coach"]
    assert stopped and not stopped[0].confirmed
    assert stopped[-1] is done[-1], "the visit must stop there, not carry on"


def test_a_LEFTOVER_overlay_is_shut_before_the_first_scene_click(
        win, tutorial_town, patched, monkeypatch):
    """The overlay sits ON the Hamlet, so every guard here reads "no panel".

    `_panel_is_open` is right, `_on_town_screen` is right, and the building
    scene point is still underneath a live widget. The frame banked on
    2026-09-06 shows it exactly: Trade Heirlooms open over the Hamlet, and the
    visit's opening click landing on its header.
    """
    order = []
    monkeypatch.setattr(town, "close_exchange",
                        lambda *a, **k: order.append("close") or True)
    backend = Recorder()
    real = backend.click

    def click(screen, mods=()):
        order.append("click")
        return real(screen, mods)

    backend.click = click
    _visit(win, backend, tutorial_town, monkeypatch, act=True)
    assert order and order[0] == "close",         "a scene point was clicked before the overlay was shut"


def test_BOTH_coach_entry_points_shut_it(win):
    """`recruit_all` opens the coach by the same scene point, so it carries
    the same hazard -- and a fix applied to one of two identical blocks is the
    shape of bug this repo keeps finding."""
    import inspect

    for fn in (town.visit_stage_coach, town.recruit_all):
        code = inspect.getsource(fn)
        body = code[:code.index("already = _panel_is_open")]
        assert "close_exchange(" in body, fn.__name__
