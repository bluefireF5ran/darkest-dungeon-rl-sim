"""What to do in the Hamlet: which upgrades to buy, and whom to recruit.

`bridge/townplan.py` is the decision layer beside `route.py` and `curios.py` --
it never clicks anything, which is what lets all of this be tested with the
game closed.

Two things are being pinned, and they failed for opposite reasons on the way
in, so both failures are kept as tests:

* **Recruiting is a SHAPE question, and the obvious metric has no gradient.**
  Scoring a hire by "how many curated comps can the roster field afterwards"
  returns zero for every candidate from the two-hero tutorial party, because
  no four-hero comp is fieldable from three heroes. All 179 shapes score 0 and
  the choice silently collapses onto the tie-break.
* **And hires INTERACT, so they cannot be chosen one at a time.** A comp is a
  class multiset; the best single hire followed by the best remaining one can
  leave a pair that completes nothing while some other pair completes a comp
  outright.

The upgrade half is Fran's instruction, and `tests/data/test_heirlooms.py` pins the
arithmetic underneath it. What is pinned here is the POLICY: the top path, two
ranks, and `rostersize` on it because the week skip needs the room.
"""
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest

from bridge.live_state import LiveHero
from bridge.town_state import Recruit, RosterHero, TownState
from bridge.townplan import (MAX_RANKS_PER_VISIT, UPGRADE_PRIORITY,
                             UPGRADE_PRIORITY_TOP_ONLY,
                             UPGRADE_PRIORITY_WITH_ROSTER, _all_shapes,
                             choose_recruits, choose_upgrades, distance,
                             fieldable, missing_for, reachable)
from tests._fixtures import get_store

from data.paths import GAME_DIR

pytestmark = pytest.mark.skipif(not GAME_DIR.exists(),
                                reason="needs the game install")

STARTING_WALLET = {"bust": 10, "portrait": 10, "deed": 10, "crest": 20}
TUTORIAL = Counter(["crusader", "highwayman"])


def hero(hero_class, guid=1):
    return RosterHero(hero=LiveHero(guid=guid, hero_class=hero_class))


def offer(hero_class, slot=0, xp=0, weapon=0, armour=0, row=None):
    """`row` defaults to `slot` because the gold listing's rows and slots
    coincide in a fresh save; the two are separate arguments because they stop
    coinciding the moment the shard store has entries."""
    return Recruit(slot=slot, listing="hero_recruit", hero_class=hero_class,
                   resolve_xp=xp, weapon_rank=weapon, armour_rank=armour,
                   row=slot if row is None else row)


def town_with(roster, offers):
    return TownState(heroes=[hero(c, i) for i, c in enumerate(roster)],
                     recruits=list(offers))


@pytest.fixture(scope="module")
def shapes():
    return [classes for _name, classes in _all_shapes(get_store())]


# -- the metrics ----------------------------------------------------------

def test_a_shape_is_a_MULTISET_and_duplicates_are_counted():
    """Three Crusaders is not one Crusader. This is the bug the CSV's
    deduplication caused ("Ascetic Movement" scored as a 2-hero party), and
    the reason `comp_loadouts` is the source rather than the CSV."""
    triple = ["crusader", "crusader", "crusader", "flagellant"]
    assert missing_for(triple, Counter(["crusader", "flagellant"])) == 2
    assert missing_for(triple, Counter({"crusader": 3, "flagellant": 1})) == 0
    assert fieldable([triple], Counter(["crusader", "flagellant"])) == 0


def test_fieldable_has_NO_gradient_from_the_tutorial_roster(shapes):
    """The measurement that forced `reachable` to exist.

    Every possible hire scores exactly the same on `fieldable` from a two-hero
    roster, because a three-hero roster fields no four-hero comp. A metric
    that only turns on at the finish line cannot steer the steps to it.
    """
    base = fieldable(shapes, TUTORIAL)
    gains = set()
    for cls in {c for shape in shapes for c in shape}:
        after = Counter(TUTORIAL)
        after[cls] += 1
        gains.add(fieldable(shapes, after) - base)
    assert gains == {0}, "if this ever varies, re-read `reachable`'s docstring"


def test_reachable_DOES_have_a_gradient_there(shapes):
    """Nine shapes are within two hires, and the classes that close the
    distance are the ones a Crusader/Highwayman core actually wants."""
    assert reachable(shapes, TUTORIAL, 2) == 9
    scored = {}
    for cls in {c for shape in shapes for c in shape}:
        after = Counter(TUTORIAL)
        after[cls] += 1
        scored[cls] = reachable(shapes, after, 1)
    best = max(scored.values())
    top = {c for c, n in scored.items() if n == best}
    assert best > 0
    assert {"vestal", "plague_doctor", "occultist", "man_at_arms",
            "houndmaster", "grave_robber"} <= top


def test_distance_falls_when_a_wanted_class_is_added(shapes):
    """The tie-break under `reachable`, which flattens on the last hire of a
    visit (a budget of zero collapses it back into `fieldable`)."""
    before = distance(shapes, TUTORIAL)
    after = Counter(TUTORIAL)
    after["vestal"] += 1
    assert distance(shapes, after) < before


# -- choosing the hires ---------------------------------------------------

def test_the_pair_that_completes_a_comp_beats_two_better_heroes(shapes):
    """The interaction that greedy misses, and the reason the SET is searched.

    Two decoys with nine resolve XP and rank-3 gear against two rank-0
    recruits that finish a real curated comp. Merit loses, and should.
    """
    target = next(c for _n, c in _all_shapes(get_store())
                  if len(c) == 4 and Counter(c)["crusader"]
                  and Counter(c)["highwayman"])
    need = list((Counter(target) - TUTORIAL).elements())
    assert len(need) == 2

    town = town_with(["crusader", "highwayman"], [
        offer("leper", 0, xp=9, weapon=3, armour=3),
        offer(need[0], 1),
        offer("jester", 2, xp=9, weapon=3, armour=3),
        offer(need[1], 3),
    ])
    picks = choose_recruits(town, shapes, limit=2)
    assert {p.recruit.hero_class for p in picks} == set(need)

    after = Counter(TUTORIAL)
    for p in picks:
        after[p.recruit.hero_class] += 1
    assert fieldable(shapes, after) > fieldable(shapes, TUTORIAL)


def test_merit_decides_only_when_the_roster_outcome_ties(shapes):
    """Two offers of the SAME class leave the roster in the same shape, so the
    better hero wins -- and that is the only situation in which merit should
    decide anything."""
    town = town_with(["crusader", "highwayman"], [
        offer("vestal", 0, xp=0),
        offer("vestal", 1, xp=9, weapon=4, armour=4),
    ])
    picks = choose_recruits(town, shapes, limit=1)
    assert len(picks) == 1
    assert picks[0].index == 1, "the better-equipped Vestal"


def test_every_hire_carries_a_reason(shapes):
    town = town_with(["crusader", "highwayman"],
                     [offer("vestal", 0), offer("jester", 1)])
    for pick in choose_recruits(town, shapes, limit=2):
        assert pick.why
        assert pick.recruit.hero_class in pick.label()


def test_the_index_is_the_recruits_PANEL_ROW(shapes):
    """`townlayout.recruit_slot(index)` is the drag source, so the index has
    to be the row the coach column draws -- `Recruit.row` -- and not the
    position in the pooled `TownState.recruits`."""
    town = town_with(["crusader"], [offer("vestal", 0), offer("jester", 1),
                                    offer("leper", 2)])
    for pick in choose_recruits(town, shapes, limit=3):
        assert pick.index == pick.recruit.row
        assert pick.recruit in town.recruits


def test_a_shard_store_hero_is_never_hired(shapes):
    """The coach keeps two stores and `read_recruits` pools them. The second
    is Color of Madness's, bought with SHARDS, and it is not drawn in the gold
    column at all: on 2026-09-01 a live save held four of them behind one gold
    offer, and hiring by the pooled index dragged from empty space. The rows
    that reach a drag must come from the gold listing and be numbered within
    it."""
    gold = offer("vestal", 0)
    shard_a, shard_b = offer("hellion", 1), offer("abomination", 2)
    shard_a.listing = shard_b.listing = "shard_hero_recruit"
    shard_a.row, shard_b.row = 0, 1
    town = town_with(["crusader"], [gold, shard_a, shard_b])

    picks = choose_recruits(town, shapes, limit=3)
    assert [p.recruit.hero_class for p in picks] == ["vestal"]
    assert picks[0].index == 0


def test_a_blank_offer_slot_is_skipped_not_hired(shapes):
    """The coach's list is padded, and an empty `heroClass` is a real value in
    the save -- the same padding that gives the 2-hero tutorial comp two blank
    slots."""
    town = town_with(["crusader"], [offer("", 0), offer("vestal", 1)])
    picks = choose_recruits(town, shapes, limit=2)
    assert [p.recruit.hero_class for p in picks] == ["vestal"]


def test_no_offers_means_no_hires(shapes):
    assert choose_recruits(town_with(["crusader"], []), shapes, limit=2) == []
    assert choose_recruits(town_with(["crusader"], [offer("vestal")]),
                           shapes, limit=0) == []


def test_without_a_store_it_hires_on_merit_and_says_so():
    """A caller with no `DataStore` still gets a usable answer rather than an
    exception -- the upgrade half needs no store at all."""
    town = town_with(["crusader"], [offer("vestal", 0),
                                    offer("leper", 1, xp=9)])
    picks = choose_recruits(town, [], limit=1)
    assert len(picks) == 1
    assert picks[0].recruit.hero_class == "leper"
    assert "merit" in picks[0].why


# -- choosing the upgrades ------------------------------------------------

def test_the_default_path_is_exactly_Frans_instruction():
    """Two ranks of `numrecruits`, the second paid for by one crest->deed
    trade. `tests/data/test_heirlooms.py` pins the arithmetic; this pins that the
    policy asks for it."""
    buys, left = choose_upgrades(STARTING_WALLET, set(), GAME_DIR)
    assert [(b.node.tree_id, b.node.code) for b in buys] == [
        ("stage_coach.numrecruits", "a"),
        ("stage_coach.numrecruits", "b"),
        ("stage_coach.rostersize", "a"),
    ]
    assert buys[0].trades == ()
    assert len(buys[1].trades) == 1
    assert buys[1].trades[0].rate.from_type == "crest"
    assert buys[1].trades[0].rate.to_type == "deed"
    assert all(v >= 0 for v in left.values())


def test_rostersize_is_ON_the_default_path_and_this_is_why():
    """It costs nine crest-equivalent in trades on week one -- `2 bust -> 3
    crest` and `2 portrait -> 3 deed` -- and it is bought anyway, because the
    week skip needs the room. `stage_coach.building.json` says the base roster
    is 9 and rank `a` takes it to 12; two tutorial heroes plus two skips'
    worth of four hires is ten, so without it the last hire has nowhere to
    go."""
    top_only, left_top = choose_upgrades(
        STARTING_WALLET, set(), GAME_DIR, priority=UPGRADE_PRIORITY_TOP_ONLY)
    default, left_default = choose_upgrades(STARTING_WALLET, set(), GAME_DIR)

    assert len(default) == len(top_only) + 1
    extra = default[-1]
    assert extra.node.tree_id == "stage_coach.rostersize"
    assert extra.node.code == "a"
    assert len(extra.trades) == 2, "it is only affordable after two trades"
    assert sum(left_default.values()) < sum(left_top.values())


def test_the_roster_the_default_path_buys_HOLDS_the_week_skip_hires():
    """The derivation, against the shipped file rather than against a memory
    of it. Both numbers come out of `stage_coach.building.json`: the coach
    offers four heroes after `numrecruits.b`, and the roster holds twelve
    after `rostersize.a`."""
    import json
    path = (GAME_DIR / "campaign" / "town" / "buildings" / "stage_coach"
            / "stage_coach.building.json")
    store = json.loads(path.read_text(encoding="utf-8"))["data"]["stores"][0]
    data = store["data"]

    def amount(rows, code):
        for row in rows:
            if row.get("upgrade_requirement_code") == code:
                return row["amount"]
        raise AssertionError(code)

    assert data["number_of_recruits_upgrades"][0]["amount"] == 2
    assert amount(data["number_of_recruits_upgrades"], "b") == 4
    assert data["roster_size_upgrades"][0]["amount"] == 9
    assert amount(data["roster_size_upgrades"], "a") == 12

    tutorial, skips = 2, 2
    hired = skips * amount(data["number_of_recruits_upgrades"], "b")
    assert tutorial + hired > data["roster_size_upgrades"][0]["amount"]
    assert tutorial + hired <= amount(data["roster_size_upgrades"], "a")


def test_a_rank_already_owned_is_not_bought_again():
    buys, _ = choose_upgrades(STARTING_WALLET,
                              {("stage_coach.numrecruits", "a")}, GAME_DIR)
    top = [b.node.code for b in buys
           if b.node.tree_id == "stage_coach.numrecruits"]
    assert top == ["b", "c"][:MAX_RANKS_PER_VISIT]
    assert all(b.node.key != ("stage_coach.numrecruits", "a") for b in buys)


def test_prerequisites_are_re_checked_WITHIN_one_visit():
    """`b` is only reachable once `a` is owned, and both are bought in the same
    visit -- so this cannot be a set difference against `next_nodes`, which
    answers "what is open now"."""
    buys, _ = choose_upgrades(STARTING_WALLET, set(), GAME_DIR)
    # Per TREE: the visit walks several, and `rostersize.a` following
    # `numrecruits.b` is the priority order, not an out-of-order rank.
    for tree in {b.node.tree_id for b in buys}:
        codes = [b.node.code for b in buys if b.node.tree_id == tree]
        assert codes == sorted(codes), f"{tree}: ranks must be taken in order"


def test_an_empty_wallet_buys_nothing_rather_than_going_negative():
    buys, left = choose_upgrades({"bust": 0, "portrait": 0, "deed": 0,
                                  "crest": 0}, set(), GAME_DIR)
    assert buys == []
    assert all(v >= 0 for v in left.values())


def test_the_priority_list_is_a_constant_a_caller_can_replace():
    """A policy, not a rule buried in a branch -- which is what makes the
    `rostersize` decision reviewable rather than invisible."""
    assert UPGRADE_PRIORITY == (("stage_coach", "stage_coach.numrecruits"),
                                ("stage_coach", "stage_coach.rostersize"))
    assert UPGRADE_PRIORITY_TOP_ONLY == UPGRADE_PRIORITY[:1]
    assert UPGRADE_PRIORITY_WITH_ROSTER == UPGRADE_PRIORITY


# -- the roster cap (2026-09-05) ------------------------------------------

def test_the_cap_tiers_are_read_from_the_COACH_and_not_listed():
    """`roster_size_upgrades` ships 9 / 12 / 16 / 20 / 24 / 28. Reading them
    means a patch that adds a rank moves this on its own; the numbers are
    asserted so a parse that silently returns nothing is caught."""
    from bridge import townplan as TP

    tiers = TP.roster_size_tiers()
    assert tiers[:6] == (9, 12, 16, 20, 24, 28)
    assert list(tiers) == sorted(tiers)


def test_a_FULL_roster_plans_no_hires_and_says_why():
    """THE LIVE BUG. `roster_room` was the constant 2, so a campaign 9 heroes
    over its cap planned two hires every single week, dragged twice and got
    "the roster did not grow" twice -- while buying `rostersize` upgrades on
    the theory that they were what was missing. Measured live at 25 heroes
    against a cap of 16.
    """
    from bridge import townplan as TP

    town = town_with(["crusader"] * 20, [offer("vestal", 0)])
    monkey = TP.roster_cap
    try:
        TP.roster_cap = lambda *a, **k: 16
        advice = TP.advise(town)
    finally:
        TP.roster_cap = monkey
    assert advice.hires == ()
    assert any("cap of 16" in n for n in advice.notes)


def test_a_roster_with_ROOM_FOR_ONE_plans_exactly_one():
    """The cap clamps the plan rather than switching it off -- one free slot
    is still worth filling."""
    from bridge import townplan as TP

    town = town_with(["crusader"] * 15, [offer("vestal", 0), offer("jester", 1)])
    monkey = TP.roster_cap
    try:
        TP.roster_cap = lambda *a, **k: 16
        advice = TP.advise(town)
    finally:
        TP.roster_cap = monkey
    assert len(advice.hires) == 1


def test_an_UNREADABLE_cap_does_not_starve_the_roster():
    """Not knowing must not block: the failure direction that matters is
    refusing hires a campaign could make, which leaves it unable to field a
    comp at all. An impossible hire is merely a wasted drag, and the
    actuator's own check reports it."""
    from bridge import townplan as TP

    town = town_with(["crusader"], [offer("vestal", 0), offer("jester", 1)])
    monkey = TP.roster_cap
    try:
        TP.roster_cap = lambda *a, **k: None
        advice = TP.advise(town)
    finally:
        TP.roster_cap = monkey
    assert advice.hires
    assert any("could not read the roster cap" in n for n in advice.notes)


def test_the_cap_needs_the_UPGRADES_FILE_to_mean_anything():
    """Rank 0 is the shipped 9, and without the file there is no rank at
    all -- so the reader answers None rather than guessing the base tier at a
    campaign that has plainly bought some."""
    from bridge import townplan as TP

    assert TP.roster_cap(None) is None
