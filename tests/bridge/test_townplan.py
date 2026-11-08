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
from bridge.townplan import (COACH_FIRST_PRIORITY, MAX_RANKS_PER_VISIT,
                             ROSTER_SIZE_TREE, UPGRADE_PRIORITY,
                             UPGRADE_PRIORITY_TOP_ONLY, priority_for,
                             UPGRADE_PRIORITY_WITH_ROSTER, _all_shapes,
                             choose_recruits, choose_upgrades, distance,
                             fieldable, missing_for, reachable)
from tests._fixtures import get_store

from data.paths import GAME_DIR


@pytest.fixture(autouse=True)
def _vanilla_exchange(monkeypatch):
    """These pin the VANILLA trade path; Fran's install has a mod that
    replaces the exchange, which `choose_upgrades` rightly refuses to trade
    through. The suite must not depend on what Steam has subscribed."""
    import bridge.townplan as townplan
    monkeypatch.setattr(townplan, "exchange_is_modded", lambda *_a: False)

pytestmark = pytest.mark.skipif(not GAME_DIR.exists(),
                                reason="needs the game install")

STARTING_WALLET = {"bust": 10, "portrait": 10, "deed": 10, "crest": 20}
TUTORIAL = Counter(["crusader", "highwayman"])



#: The pre-2026-09-23 order, which the tutorial-wallet tests below were
#: written against. They test the trade arithmetic, not the order.
COACH_ONLY = (("stage_coach", "stage_coach.numrecruits"),
              ("stage_coach", "stage_coach.rostersize"))

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
    # 9 -> 10 on 2026-09-19: the team-builder comp sync grew the pool
    # 178 -> 477 files, so one more shape is within two recruits of the
    # tutorial pair. A count over the pool, bumped deliberately.
    assert reachable(shapes, TUTORIAL, 2) == 10
    scored = {}
    for cls in {c for shape in shapes for c in shape}:
        after = Counter(TUTORIAL)
        after[cls] += 1
        scored[cls] = reachable(shapes, after, 1)
    best = max(scored.values())
    top = {c for c, n in scored.items() if n == best}
    assert best > 0
    # THE GRADIENT is the claim -- `reachable` discriminates between
    # candidate hires where `fieldable` is flat. This used to pin the six
    # classes that tied at the top, which was a snapshot of the pool
    # rather than a property: after the 2026-09-19 comp sync only
    # `man_at_arms` tops it, and the test failed while the behaviour it
    # names was working perfectly.
    assert 0 < len(top) < len(scored), (
        f"no gradient: {len(top)} of {len(scored)} classes tie at {best}")


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

    Two decoys against two recruits that finish a real curated comp. The
    decoys used to carry nine resolve XP and rank-3 gear ("merit loses, and
    should"); since 2026-09-26 an Experienced Recruit outranks comp shape
    (`test_an_experienced_recruit_is_hired_before_a_level_0_one`), so they
    are level-0 decoys with a better slot now.
    """
    target = next(c for _n, c in _all_shapes(get_store())
                  if len(c) == 4 and Counter(c)["crusader"]
                  and Counter(c)["highwayman"])
    need = list((Counter(target) - TUTORIAL).elements())
    assert len(need) == 2

    town = town_with(["crusader", "highwayman"], [
        offer("leper", 0),
        offer(need[0], 1),
        offer("jester", 2),
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
    buys, left = choose_upgrades(STARTING_WALLET, set(), GAME_DIR,
                                 priority=COACH_ONLY)
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


def test_a_service_discount_stops_at_its_first_rank(monkeypatch):
    """PLAYBOOK 4.1: a discount is worth rank a, not maximising. With every
    discount at a and a wallet that reaches everything, no discount b is
    bought -- except the Nomad Wagon's, Fran's own habit. Off (the shipped
    default, measured null), the old walk buys them: the control."""
    import bridge.townplan as townplan
    monkeypatch.setattr(townplan, "SERVICE_DISCOUNT_MAX_RANKS", 1)
    rich = {"bust": 500, "portrait": 500, "deed": 500, "crest": 500}
    owned = {(t, "a") for t in townplan.SERVICE_DISCOUNT_TREES}

    def discounts():
        buys, _ = choose_upgrades(rich, owned, GAME_DIR)
        return {(x.node.tree_id, x.node.code) for x in buys}

    on = discounts()
    assert not {k for k in on if k[0] in townplan.SERVICE_DISCOUNT_TREES}
    assert ("blacksmith.weapon", "a") in on
    assert any(k[0] == "nomad_wagon.cost" for k in on)
    monkeypatch.setattr(townplan, "SERVICE_DISCOUNT_MAX_RANKS", None)
    assert ("blacksmith.cost", "b") in discounts()


def test_rostersize_is_ON_the_default_path_and_this_is_why():
    """It costs nine crest-equivalent in trades on week one -- `2 bust -> 3
    crest` and `2 portrait -> 3 deed` -- and it is bought anyway, because the
    week skip needs the room. `stage_coach.building.json` says the base roster
    is 9 and rank `a` takes it to 12; two tutorial heroes plus two skips'
    worth of four hires is ten, so without it the last hire has nowhere to
    go."""
    top_only, left_top = choose_upgrades(
        STARTING_WALLET, set(), GAME_DIR, priority=UPGRADE_PRIORITY_TOP_ONLY)
    default, left_default = choose_upgrades(STARTING_WALLET, set(), GAME_DIR,
                                            priority=COACH_ONLY)

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
                              {("stage_coach.numrecruits", "a")}, GAME_DIR,
                              priority=COACH_FIRST_PRIORITY)
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
    # PLAYBOOK 7.4 (2026-09-23): abbey last, the tavern never; Fran
    # 2026-10-12: Guild and Blacksmith ahead of the Coach.
    buildings = [b for b, _t in UPGRADE_PRIORITY]
    assert [t for _b, t in UPGRADE_PRIORITY[:3]] == [
        "guild.skill_levels", "blacksmith.weapon", "blacksmith.armour"]
    assert COACH_FIRST_PRIORITY[0] == ("stage_coach", "stage_coach.numrecruits")
    assert sorted(UPGRADE_PRIORITY) == sorted(COACH_FIRST_PRIORITY)
    assert buildings.index("stage_coach") < buildings.index("sanitarium")
    assert buildings[-1] == "abbey"
    assert "tavern" not in buildings
    assert buildings.index("guild") < buildings.index("sanitarium")         < buildings.index("camping_trainer")
    assert UPGRADE_PRIORITY_TOP_ONLY == COACH_FIRST_PRIORITY[:1]
    assert UPGRADE_PRIORITY_WITH_ROSTER == UPGRADE_PRIORITY


def test_a_full_vanilla_roster_buys_roster_size_first():
    """Fran, 2026-10-12: smith first, "but we need to manage the vanilla
    roster size, not modded". Room for every offer keeps the smith order; a
    roster that cannot take them all puts `rostersize` first; an unread or
    modded cap (~1,000 on his install) never does."""
    assert priority_for(10, 2, 12) == UPGRADE_PRIORITY
    order = priority_for(11, 2, 12)
    assert order[0] == ROSTER_SIZE_TREE
    assert sorted(order) == sorted(UPGRADE_PRIORITY)
    assert priority_for(40, 4, None) == UPGRADE_PRIORITY
    assert priority_for(40, 4, 1012) == UPGRADE_PRIORITY


def test_a_full_roster_spends_the_starting_wallet_on_its_room():
    order = priority_for(12, 4, 12)
    buys, _ = choose_upgrades(STARTING_WALLET, set(), GAME_DIR, priority=order)
    assert buys[0].node.key == ("stage_coach.rostersize", "a")


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


def _lvl(guid, level, stress=0.0, quirks=(), **kw):
    return RosterHero(hero=LiveHero(guid=guid, hero_class="crusader",
                                    resolve_level=level, stress=stress,
                                    quirks=list(quirks)), **kw)


def test_dismissal_takes_quirkless_low_level_most_stressed_first():
    """Fran's rule: no S/A/B quirk AND (level 0 OR below the roster mean)."""
    from bridge.townplan import choose_dismissals
    town = TownState(heroes=[
        _lvl(1, 0, stress=10),
        _lvl(2, 0, stress=80),
        _lvl(3, 0, quirks=["slayer_of_man"]),      # S tier: kept
        _lvl(4, 1, quirks=["fated"]),              # E tier only, below mean
        _lvl(5, 6),                                # above the mean: kept
        _lvl(6, 0, building="abbey"),              # busy: never offered
    ])
    got = [h.guid for h in choose_dismissals(town, need=5)]
    assert got == [2, 1, 4]


def test_dismissal_plans_nothing_with_room():
    from bridge.townplan import choose_dismissals
    town = TownState(heroes=[_lvl(1, 0)])
    assert choose_dismissals(town, need=0) == []


def test_advise_hires_EVERY_offer_by_default():
    """Fran, 2026-09-23: hire all, not two -- the embark needs the whole pool
    to find a clean party. No upgrades file, so no cap is read."""
    from bridge.townplan import GOLD_RECRUIT_LISTING, advise
    offers = [Recruit(slot=i, listing=GOLD_RECRUIT_LISTING, hero_class=c, row=i)
              for i, c in enumerate(("crusader", "vestal", "jester", "leper",
                                     "arbalest"))]
    got = advise(TownState(recruits=offers))
    assert len(got.hires) == 5


def test_the_reset_takes_every_level_0_hero_over_50_stress():
    from bridge.townplan import reset_dismissals
    town = TownState(heroes=[_lvl(1, 0, stress=90), _lvl(2, 0, stress=50),
                             _lvl(3, 1, stress=99), _lvl(4, 0, stress=70,
                             quirks=["slayer_of_man"]), _lvl(5, 0, stress=60)])
    assert [h.guid for h in reset_dismissals(town, keep={5})] == [1, 4]


def test_treatment_is_for_USEFUL_heroes_over_70_and_never_the_tavern():
    """PLAYBOOK 7.3/7.4: passive recovery covers low stress, fodder is
    replaced rather than treated, and the tavern can lose trinkets."""
    from bridge.townplan import choose_treatments
    town = TownState(heroes=[_lvl(1, 3, stress=90), _lvl(2, 0, stress=95),
                             _lvl(3, 3, stress=60),
                             _lvl(4, 0, stress=80, quirks=["slayer_of_man"])],
                     wallet={"gold": 29000})  # cap 2,900
    got = choose_treatments(town, GAME_DIR)
    # Only #1: #4's lone S quirk scores under NURTURE_MIN for a Crusader,
    # whose curated comps do not build around it, so #4 is fodder.
    assert [t.guid for t in got] == [1]
    assert all(t.building != "tavern" for t in got)


def test_a_MODDED_exchange_plans_no_trade(monkeypatch):
    """Garphyield's Exchange adds heirloom -> gold rows, so the vanilla row
    map aims at the wrong row. With the table replaced, nothing is traded:
    a wallet that cannot pay outright buys nothing."""
    import bridge.townplan as townplan
    monkeypatch.setattr(townplan, "exchange_is_modded", lambda *_a: True)
    bought, _left = townplan.choose_upgrades({"crest": 500}, set(), GAME_DIR)
    assert all(not p.trades for p in bought)


def test_experienced_recruits_is_bought_in_the_visit_that_opens_it():
    """Fran, 2026-09-25: add Experienced Recruits. Shipped data gates
    `upgraded_recruits.a` on rank b of guild.skill_levels, blacksmith.weapon
    and blacksmith.armour. Listed before them it was checked while they were
    unowned and skipped every visit; after them, a rich visit buys the chain
    and then the coach rank."""
    rich = {"bust": 500, "portrait": 500, "deed": 500, "crest": 500, "gold": 10**6}
    buys, _left = choose_upgrades(rich, set(), GAME_DIR)
    keys = [f"{p.node.tree_id}.{p.node.code}" for p in buys]
    assert "stage_coach.upgraded_recruits.a" in keys, keys
    at = keys.index("stage_coach.upgraded_recruits.a")
    for pre in ("guild.skill_levels.b", "blacksmith.weapon.b",
                "blacksmith.armour.b"):
        assert keys.index(pre) < at, (pre, keys)


# -- the class-and-level cap (Fran, 2026-09-26) -----------------------------


def test_the_coach_never_hires_a_fifth_of_one_class_and_level():
    from bridge.townplan import MAX_PER_CLASS_LEVEL
    town = town_with(["vestal"] * MAX_PER_CLASS_LEVEL + ["leper"],
                     [offer("vestal", 0), offer("leper", 1), offer("vestal", 2)])
    hires = choose_recruits(town, [], limit=3)
    assert [h.recruit.hero_class for h in hires] == ["leper"]


def test_the_room_left_under_the_cap_goes_to_the_better_recruit():
    town = town_with(["vestal"] * 3, [offer("vestal", 0, weapon=0),
                                      offer("vestal", 1, weapon=2)])
    hires = choose_recruits(town, [], limit=2)
    assert [h.recruit.slot for h in hires] == [1]


def test_the_surplus_is_dismissed_worst_first_and_never_the_busy():
    from bridge.townplan import cap_dismissals
    heroes = []
    for i, stress in enumerate((10, 90, 50, 30, 70, 20)):
        heroes.append(RosterHero(hero=LiveHero(guid=i, hero_class="leper",
                                               stress=stress)))
    heroes[1] = RosterHero(hero=heroes[1].hero, in_party=True)   # 90, busy
    out = cap_dismissals(TownState(heroes=heroes))
    # six lepers at level 0: two go, the two most stressed that are free
    assert [h.guid for h in out] == [4, 2]


def test_a_levelled_roster_keeps_fewer_low_band_heroes():
    """Fran: "the more the average level of the roster, the lower the
    threshold for lower levels" -- the same five level-0 lepers lose one more
    to the cap when the rest of the roster is level 4."""
    from bridge.townplan import cap_dismissals
    lepers = [RosterHero(hero=LiveHero(guid=i, hero_class="leper"))
              for i in range(5)]
    vets = [RosterHero(hero=LiveHero(guid=100 + i, hero_class=c, resolve_level=6))
            for i, c in enumerate(["vestal", "crusader", "jester", "arbalest",
                                   "houndmaster", "occultist"] * 2)]
    low = cap_dismissals(TownState(heroes=lepers))
    high = cap_dismissals(TownState(heroes=lepers + vets))
    assert len(low) == 1 and len(high) > len(low)


def test_only_fodder_is_dismissed_for_the_cap():
    from bridge.townplan import cap_dismissals
    heroes = [RosterHero(hero=LiveHero(guid=i, hero_class="leper",
                                       weapon_rank=1 if i < 5 else 0))
              for i in range(6)]
    out = cap_dismissals(TownState(heroes=heroes))
    assert [h.guid for h in out] == [5]


def test_an_experienced_recruit_is_hired_before_a_level_0_one(shapes):
    """Fran, 2026-09-26: Experienced Recruits arrive with ~6,250 gold of work
    done, so they take the room first even when a level-0 offer fits a comp
    better."""
    town = town_with(["crusader", "vestal", "highwayman"],
                     [offer("plague_doctor", 0), offer("jester", 1, weapon=1)])
    hires = choose_recruits(town, shapes, limit=1)
    assert [h.recruit.hero_class for h in hires] == ["jester"]


def test_a_full_band_swaps_its_worst_fodder_for_a_better_recruit():
    """Fran, 2026-09-27: hero quality decides who takes the 4 class slots.
    Four fodder lepers fill band 0-2; an Experienced Recruit leper replaces
    the most stressed of them, a plain level-0 leper does not get in."""
    lepers = [RosterHero(hero=LiveHero(guid=i, hero_class="leper", stress=s))
              for i, s in enumerate((10, 80, 30, 50))]
    seasoned = offer("leper", 0, weapon=1)
    plain = offer("leper", 1)
    town = TownState(heroes=lepers, recruits=[seasoned, plain])
    hires = choose_recruits(town, [], limit=2)
    assert [h.recruit.slot for h in hires] == [0]
    assert hires[0].replaces is not None and hires[0].replaces.guid == 1


def test_a_nurture_recruit_outranks_a_fodder_one():
    from bridge.townplan import recruit_value
    star = Recruit(slot=0, listing="hero_recruit", hero_class="arbalest",
                   quirks=("corvids_eye", "luminous"), row=0)
    dull = Recruit(slot=1, listing="hero_recruit", hero_class="arbalest", row=1)
    assert recruit_value(star) > recruit_value(dull)


def test_a_rank_behind_an_unaffordable_prerequisite_is_not_planned():
    """Live 2026-09-27, twice: Experienced Recruits b (heirlooms) was planned
    while its guild.skill_levels c / blacksmith c prerequisites (gold) were
    dropped by the wallet, and the game refused it."""
    from bridge.townplan import choose_upgrades
    owned = {("guild.skill_levels", "a"), ("blacksmith.weapon", "a"),
             ("blacksmith.armour", "a"), ("stage_coach.upgraded_recruits", "a"),
             ("stage_coach.numrecruits", "a"), ("stage_coach.numrecruits", "b"),
             ("stage_coach.numrecruits", "c"), ("stage_coach.rostersize", "a"),
             ("stage_coach.rostersize", "b")}
    wallet = {"gold": 0, "bust": 22, "portrait": 12, "deed": 3, "crest": 23}
    buys, _left = choose_upgrades(wallet, owned, GAME_DIR)
    have = set(owned)
    for p in buys:
        assert all(q in have for q in p.node.prerequisites), p.node.key
        have.add(p.node.key)
    assert ("stage_coach.upgraded_recruits", "b") not in {p.node.key for p in buys}


def test_nurture_buys_stop_at_the_heros_resolve_and_the_building_ranks():
    """Fran, 2026-09-27: max out what a hero can take at their level. The
    shipped tree gates each rank on resolve and on the building's rank."""
    from bridge.townplan import nurture_buys
    vestal = RosterHero(hero=LiveHero(guid=7, hero_class="vestal",
                                      resolve_level=1,
                                      selected_skills=["mace_bash"]))
    owned = {"mace_bash": 1, "judgement": 1}
    bought = nurture_buys(vestal, owned, {("guild.skill_levels", "a")}, GAME_DIR)
    trees = [(b.tree, b.index) for b in bought]
    assert ("mace_bash", 1) in trees                 # rank 2: resolve 1, guild a
    assert ("mace_bash", 2) not in trees             # rank 3 needs resolve 2
    assert not any(t in ("weapon", "armour") for t, _i in trees)  # no blacksmith a
    assert trees[0] == ("mace_bash", 1)              # the worn skill first
    capped = nurture_buys(vestal, owned, {("guild.skill_levels", "a")}, GAME_DIR,
                          budget=300)
    assert sum(b.gold for b in capped) <= 300


def test_the_hero_already_being_nurtured_is_finished_first(monkeypatch):
    from bridge import hero_roles, townplan
    a = RosterHero(hero=LiveHero(guid=1, hero_class="vestal", resolve_level=1))
    b = RosterHero(hero=LiveHero(guid=2, hero_class="vestal", resolve_level=1))
    roles = {1: hero_roles.HeroRole(1, "vestal", 1, "0-2", "nurture", 0, 9.0, 0),
             2: hero_roles.HeroRole(2, "vestal", 1, "0-2", "invested", 3000, 5.0, 0)}
    monkeypatch.setattr(townplan, "_owned_ranks", lambda p, h, g: {})
    town = TownState(heroes=[a, b], wallet={"gold": 100_000})
    buys = townplan.plan_nurture(town, None, set(), GAME_DIR, roles=roles)
    assert buys and {x.guid for x in buys} == {2}


def test_the_granary_price_is_held_back_while_a_blueprint_waits():
    """Fran, 2026-09-27: with a blueprint, the Granary is the first building.
    Its busts and gold are not spent on other upgrades until it is bought."""
    from bridge.townplan import advise, granary_reserve
    town = TownState(wallet={"gold": 5000, "bust": 70, "blueprint": 1},
                     districts={"granary": False})
    assert granary_reserve(town) == {"gold": 2500, "bust": 60}
    assert granary_reserve(TownState(wallet={"blueprint": 0},
                                     districts={"granary": False})) == {}
    assert granary_reserve(TownState(wallet={"blueprint": 1},
                                     districts={"granary": True})) == {}
    advice = advise(town)
    assert advice.wallet_after.get("bust", 0) >= 60
    assert advice.wallet_after.get("gold", 0) >= 2500


def test_only_the_coach_rows_on_screen_are_hired():
    """Live 2026-09-27/28: 24 offers, a hire from row 7 dragged from below the
    drawn list and failed. Rows past `COACH_ROWS_VISIBLE` are not offered."""
    from bridge.townplan import COACH_ROWS_VISIBLE
    offers = [offer("leper", i) for i in range(COACH_ROWS_VISIBLE + 2)]
    hires = choose_recruits(town_with([], offers), [], limit=len(offers))
    assert hires and max(h.index for h in hires) < COACH_ROWS_VISIBLE
