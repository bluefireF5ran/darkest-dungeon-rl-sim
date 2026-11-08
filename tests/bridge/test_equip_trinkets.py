"""`town.equip_trinkets` -- the hands for the team-builder's trinket picks.

Measured live 2026-09-25: the Trinket Inventory is positional while it is
open (a taken trinket leaves a hole) and COMPACTS when reopened. The first
armed run handed the second hero the trinket one cell right of the plan.
"""
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from bridge import town, townlayout as TL  # noqa: E402


class _Resolved:
    def __init__(self):
        self.win = SimpleNamespace(client=(0, 0, 1280, 720), hwnd=None)

    def at(self, frac):
        return None if frac is None else (int(frac[0] * 1280), int(frac[1] * 720))


class _Backend:
    def __init__(self):
        self.drags = []

    def drag(self, src, dst):
        self.drags.append((src, dst))
        return True

    def click(self, pt):
        return True

    def right_click(self, pt):
        return True


def _hero(cls, guid):
    return SimpleNamespace(hero_class=cls, guid=guid,
                           hero=SimpleNamespace(trinkets=[]))


def test_the_second_hero_drags_from_the_COMPACTED_grid(monkeypatch):
    import bridge.town_state as ts
    import bridge.trinketplan as tp
    import bridge.screen as screen_mod
    a, b = _hero("bounty_hunter", 1), _hero("abomination", 2)
    town_now = SimpleNamespace(heroes=[a, b])
    monkeypatch.setattr(ts, "read_town_state", lambda p: town_now)
    monkeypatch.setattr(ts, "find_profile", lambda p: Path("."))
    monkeypatch.setattr(ts, "read_trinket_cells",
                        lambda f: {4: "snake_oil", 5: "speed_stone", 6: "fetish"})
    monkeypatch.setattr(tp, "plan_trinkets",
                        lambda party, owned: [["snake_oil"], ["speed_stone"]])
    monkeypatch.setattr(town, "force_roster_to_top",
                        lambda *a, **k: town.Did("top", sent=True, confirmed=True))
    monkeypatch.setattr(town.time, "sleep", lambda s: None)
    # The pre-pass is pinned by its own test; this one is about the drags.
    monkeypatch.setattr(town, "reset_trinkets", lambda *a, **k: [])
    monkeypatch.setattr(town, "_trinket_pool", lambda p: None)
    frames = iter([np.zeros((720, 1280, 3), np.uint8), np.full((720, 1280, 3), 200, np.uint8)] * 4)
    monkeypatch.setattr(screen_mod, "grab", lambda win: next(frames))
    plan = SimpleNamespace(party=[SimpleNamespace(hero=a, skills=()),
                                  SimpleNamespace(hero=b, skills=())])
    be = _Backend()
    done = town.equip_trinkets(None, be, _Resolved(), "p", plan, act=True)
    # The end-of-pass save check has its own test; these fakes never wear.
    done = [d for d in done if not d.what.startswith("trinkets on")]
    assert [d.confirmed for d in done] == [True, True], done
    r = _Resolved()
    # First opening: keys 4, 5, 6 draw at positions 0, 1, 2 -> snake_oil at 0.
    assert be.drags[0][0] == r.at(TL.trinket_cell(0))
    # Reopened: snake_oil is gone and the grid packed -> speed_stone is at 0.
    assert be.drags[1][0] == r.at(TL.trinket_cell(0))


def test_nothing_is_dragged_unarmed(monkeypatch):
    import bridge.town_state as ts
    import bridge.trinketplan as tp
    a = _hero("bounty_hunter", 1)
    monkeypatch.setattr(ts, "read_town_state", lambda p: SimpleNamespace(heroes=[a]))
    monkeypatch.setattr(ts, "find_profile", lambda p: Path("."))
    monkeypatch.setattr(ts, "read_trinket_cells", lambda f: {0: "snake_oil"})
    monkeypatch.setattr(tp, "plan_trinkets", lambda party, owned: [["snake_oil"]])
    be = _Backend()
    done = town.equip_trinkets(None, be, _Resolved(), "p",
                               SimpleNamespace(party=[SimpleNamespace(hero=a, skills=())]))
    assert be.drags == [] and not done[0].sent


def test_a_pick_worn_by_a_BENCHED_hero_is_taken_off_then_equipped(monkeypatch):
    """The loop picks a new party most raids, so last raid's trinkets sit on
    heroes staying home. The planner is offered them; the one it picks comes
    off the benched hero into the chest, and the equip pass re-reads."""
    import bridge.town_state as ts
    import bridge.trinketplan as tp
    import bridge.screen as screen_mod
    party_hero = _hero("abomination", 1)
    bench = SimpleNamespace(hero_class="bounty_hunter", guid=2,
                            hero=SimpleNamespace(trinkets=["speed_stone"]))
    bare = SimpleNamespace(hero_class="bounty_hunter", guid=2,
                           hero=SimpleNamespace(trinkets=[]))
    towns = iter([SimpleNamespace(heroes=[party_hero, bench]),     # plan
                  SimpleNamespace(heroes=[party_hero, bare]),      # strip check
                  SimpleNamespace(heroes=[party_hero, bare])])     # re-plan
    monkeypatch.setattr(ts, "read_town_state", lambda p: next(towns))
    monkeypatch.setattr(ts, "find_profile", lambda p: Path("."))
    estates = iter([{0: "snake_oil"}, {0: "snake_oil", 1: "speed_stone"}])
    monkeypatch.setattr(ts, "read_trinket_cells", lambda f: next(estates))
    offered = []
    monkeypatch.setattr(tp, "plan_trinkets",
                        lambda party, owned: offered.append(sorted(owned))
                        or [["speed_stone"]])
    monkeypatch.setattr(town, "force_roster_to_top",
                        lambda *a, **k: town.Did("top", sent=True, confirmed=True))
    monkeypatch.setattr(town.time, "sleep", lambda s: None)
    # The pre-pass is pinned by its own test; this one is about the drags.
    monkeypatch.setattr(town, "reset_trinkets", lambda *a, **k: [])
    monkeypatch.setattr(town, "_trinket_pool", lambda p: None)
    frames = iter([np.zeros((720, 1280, 3), np.uint8),
                   np.full((720, 1280, 3), 200, np.uint8)] * 4)
    monkeypatch.setattr(screen_mod, "grab", lambda win: next(frames))
    plan = SimpleNamespace(party=[SimpleNamespace(hero=party_hero, skills=())])
    be = _Backend()
    done = town.equip_trinkets(None, be, _Resolved(), "p", plan, act=True)
    # The end-of-pass save check has its own test; these fakes never wear.
    done = [d for d in done if not d.what.startswith("trinkets on")]
    assert offered[0] == ["snake_oil", "speed_stone"]
    assert [(d.what, d.confirmed) for d in done] == [
        ("strip speed_stone off bounty_hunter", True),
        ("equip abomination speed_stone", True)], done
    r = _Resolved()
    # Off the benched hero's slot 0 into the first empty cell (1 in the chest).
    assert be.drags[0] == (r.at(TL.HERO_TRINKET_SLOTS[0]), r.at(TL.trinket_cell(1)))
    # Then from where the re-read estate says it is, onto the party hero.
    assert be.drags[1] == (r.at(TL.trinket_cell(1)), r.at(TL.HERO_TRINKET_SLOTS[0]))


# -- the roster cursor (2026-09-25): a town visit took twenty minutes ------


def test_the_roster_is_rewound_once_then_scrolled_by_the_difference(monkeypatch):
    calls = []
    monkeypatch.setattr(town, "force_roster_to_top",
                        lambda *a, **k: calls.append("top") or
                        town.Did("top", sent=True, confirmed=True))
    monkeypatch.setattr(town, "scroll_roster_by",
                        lambda w, b, r, n, **k: calls.append(n) or
                        town.Did("s", sent=True, confirmed=True))
    strip = [np.zeros((4, 4))]
    monkeypatch.setattr(town, "_roster_strip", lambda w, r: strip[0])
    tn = SimpleNamespace(heroes=[])
    cur: dict = {}
    last = town.ROSTER_ROWS_VISIBLE - 1
    assert town._roster_row_for(None, None, None, tn, 30, act=True,
                                cursor=cur) == (last, "")
    assert calls == ["top", 30 - last]
    calls.clear()
    # further down: only the difference, no rewind
    assert town._roster_row_for(None, None, None, tn, 40, act=True,
                                cursor=cur) == (last, "")
    assert calls == [10]
    calls.clear()
    # already on screen: nothing sent
    assert town._roster_row_for(None, None, None, tn, 36, act=True,
                                cursor=cur)[0] == 36 - (40 - last)
    assert calls == []
    # the list moved under us: back to the rewind
    strip[0] = np.full((4, 4), 255.0)
    town._roster_row_for(None, None, None, tn, 41, act=True, cursor=cur)
    assert calls[0] == "top"
    calls.clear()
    # cannot see the list: also the rewind, never a guess
    strip[0] = None
    town._roster_row_for(None, None, None, tn, 45, act=True, cursor=cur)
    assert calls[0] == "top"


# -- the witnessed fast scroll (2026-09-26) ---------------------------------


def _synthetic_strip(offset, height=480, width=40):
    """A roster strip whose row n is a flat shade unique to n, `offset` rows in."""
    import numpy as np
    pitch = town._roster_pitch_px(np.zeros((height, width, 3)))
    ys = np.arange(height)
    row = ((ys + offset * pitch) // pitch).astype(int)
    shade = (row * 37) % 251
    return np.repeat(np.repeat(shade[:, None, None], width, 1), 3, 2).astype(float)


def test_the_rows_a_scroll_moved_are_read_off_the_strip():
    base = _synthetic_strip(10)
    for k in (0, 1, 3, 6):
        assert town._rows_moved(base, _synthetic_strip(10 + k), max_rows=7) == k
    assert town._rows_moved(base, None) is None
    import numpy as np
    noise = np.random.default_rng(0).uniform(0, 255, base.shape)
    assert town._rows_moved(base, noise) is None


def test_the_reset_is_one_chest_opening_and_is_witnessed_by_the_save(monkeypatch):
    """Unequip all -> Yes -> sort by rarity -> close, and the save must then
    show nobody wearing anything (Fran, 2026-09-26)."""
    from bridge.layout import MODAL_OK
    monkeypatch.setattr(town.time, "sleep", lambda s: None)
    from bridge import town_state as ts
    worn, bare = _hero("vestal", 1), _hero("vestal", 1)
    worn.hero.trinkets = ["x"]
    res = _Resolved()
    for heroes, ok in (([bare], True), ([worn], False)):
        monkeypatch.setattr(ts, "read_town_state",
                            lambda p, h=heroes: SimpleNamespace(heroes=h))
        be, clicks = _Backend(), []
        be.click = lambda pt: clicks.append(pt) or True
        done = town.reset_trinkets(None, be, res, "p", act=True)
        assert done[-1].confirmed is ok
        assert clicks == [res.at(f) for f in (
            TL.TRINKET_CHEST_BUTTON, TL.TRINKET_UNEQUIP_ALL, MODAL_OK,
            TL.TRINKET_SORT_RARITY, TL.TRINKET_PANEL_CLOSE)]


def test_loadout_toggles_deselect_first_and_keep_a_full_bar():
    order = ["crush", "rampart", "bellow", "defender", "retribution",
             "command", "bolster"]
    off, on, target = town.loadout_toggles(
        order, ["crush", "bellow", "defender", "bolster"], order,
        ("rampart", "bellow", "defender", "bolster"))
    assert (off, on) == ([0], [1])
    assert target == ["rampart", "bellow", "defender", "bolster"]
    # A comp skill the hero does not own is not asked for; the bar stays full.
    off, on, target = town.loadout_toggles(
        order, ["crush", "bellow", "defender", "bolster"],
        ["crush", "bellow", "defender", "bolster"],
        ("rampart", "bellow", "defender", "command"))
    assert (off, on) == ([], []) and len(target) == 4


def test_the_save_check_reports_a_trinket_on_the_wrong_cell(monkeypatch):
    """Live 2026-09-27: every drag "changed the slot" and the party still
    wore the wrong trinkets. The pass ends by reading what each hero wears."""
    from bridge import town_state as ts
    from bridge import trinketplan as tp
    a = _hero("abomination", 1)
    monkeypatch.setattr(ts, "find_profile", lambda p: Path("."))
    monkeypatch.setattr(ts, "read_trinket_cells", lambda f: {0: "speed_stone"})
    monkeypatch.setattr(tp, "plan_trinkets", lambda party, owned: [["speed_stone"]])
    monkeypatch.setattr(town, "force_roster_to_top",
                        lambda *a, **k: SimpleNamespace(confirmed=True))
    monkeypatch.setattr(town.time, "sleep", lambda s: None)
    monkeypatch.setattr(town, "reset_trinkets", lambda *a, **k: [])
    monkeypatch.setattr(town, "_trinket_pool", lambda p: None)
    from bridge import screen as screen_mod
    frames = iter([np.zeros((720, 1280, 3), np.uint8),
                   np.full((720, 1280, 3), 200, np.uint8)] * 4)
    monkeypatch.setattr(screen_mod, "grab", lambda win: next(frames))
    wrong = _hero("abomination", 1)
    wrong.hero.trinkets = ["move_stone"]
    reads = []

    def read(p):                      # the plan's read, then the save after
        reads.append(p)
        return SimpleNamespace(heroes=[a] if len(reads) == 1 else [wrong])
    monkeypatch.setattr(ts, "read_town_state", read)
    plan = SimpleNamespace(party=[SimpleNamespace(hero=a, skills=())])
    done = town.equip_trinkets(None, _Backend(), _Resolved(), "p", plan, act=True)
    check = [d for d in done if d.what.startswith("trinkets on")]
    assert check and check[0].confirmed is False and "move_stone" in check[0].why


def test_held_trinkets_come_from_the_roster_after_the_reset(monkeypatch):
    """Live 2026-09-27: the embark plan's hero still listed a survival guide
    the unequip-all had taken off, so only one slot was planned."""
    from bridge import town_state as ts
    from bridge import trinketplan as tp
    stale = _hero("arbalest", 1)
    stale.hero.trinkets = ["survival_guide"]
    fresh = _hero("arbalest", 1)
    monkeypatch.setattr(ts, "read_town_state",
                        lambda p: SimpleNamespace(heroes=[fresh]))
    monkeypatch.setattr(ts, "find_profile", lambda p: Path("."))
    monkeypatch.setattr(ts, "read_trinket_cells",
                        lambda f: {0: "archers_ring", 1: "survival_guide"})
    monkeypatch.setattr(tp, "plan_trinkets",
                        lambda party, owned: [["archers_ring", "survival_guide"]])
    monkeypatch.setattr(town, "reset_trinkets", lambda *a, **k: [])
    monkeypatch.setattr(town, "_trinket_pool", lambda p: None)
    said = []
    plan = SimpleNamespace(party=[SimpleNamespace(hero=stale, skills=())])
    town.equip_trinkets(None, _Backend(), _Resolved(), "p", plan,
                        say=said.append, act=False)
    line = next(s for s in said if "TRINKET arbalest" in s)
    assert "archers_ring" in line and "survival_guide" in line
