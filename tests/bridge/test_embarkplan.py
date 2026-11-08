"""The embark decision: which quest, which comp, which formation, what to buy.

`bridge/embarkplan.py` has no policy in it and that is the design -- Fran asked
for "just an option that works", so the score is affordability and the choice
among the affordable is a coin flip. What has to be right is everything around
that: the quest filter, the curated-only rule, the rank order, and the fact
that the wallet never goes negative.

The roster here is SYNTHETIC. A live save moves under the test, and the point
of a plan module is that it replays offline.
"""
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest

from bridge import embarkplan as EP
from bridge.live_state import LiveHero
from bridge.town_state import Quest, RosterHero, TownState
from data import provisions as P

from data.paths import GAME_DIR

pytestmark = pytest.mark.skipif(not GAME_DIR.exists(),
                                reason="needs the game install")

GD = str(GAME_DIR)


# INVESTED by default (resolve 1, rank-1 gear: an Experienced Recruit): since
# 2026-09-26 FODDER is never bought skills (Fran), so the unlock-costing tests
# need heroes who can be.
def hero(hero_class: str, guid: int, stress: float = 0.0, resolve: int = 1,
         weapon: int = 1):
    return RosterHero(hero=LiveHero(guid=guid, hero_class=hero_class,
                                    stress=stress, resolve_level=resolve,
                                    weapon_rank=weapon))


# The live campaign's twelve, which is what makes the fieldable set realistic.
ROSTER = [hero("crusader", 1, 4.0, 1), hero("highwayman", 2, 0.0, 1),
          hero("plague_doctor", 7), hero("vestal", 8, 100.0),
          hero("leper", 18), hero("antiquarian", 19), hero("antiquarian", 20),
          hero("jester", 21), hero("occultist", 28), hero("duelist", 29),
          hero("man_at_arms", 30), hero("abomination", 31)]

RUINS = Quest(key="0", quest_id="plot_tutorial_crypts", dungeon="crypts",
              quest_type="explore", difficulty=1, length=1, gold_reward=3000)
COURTYARD = Quest(key="2", quest_id="plot_crimson_court_0", dungeon="courtyard",
                  quest_type="inventory_activate", difficulty=1, length=1,
                  gold_reward=5000)
DARKEST = Quest(key="1", quest_id="plot_darkest_dungeon_1",
                dungeon="darkestdungeon", quest_type="kill_boss",
                difficulty=6, length=2, gold_reward=15000)
LONG_WEALD = Quest(key="3", quest_id="generated_1", dungeon="weald",
                   quest_type="explore", difficulty=1, length=3,
                   gold_reward=9000)


def town(quests=(RUINS, COURTYARD, DARKEST), gold=6670, heroes=None):
    return TownState(heroes=list(ROSTER if heroes is None else heroes),
                     quests=list(quests), wallet={"gold": gold})


@pytest.fixture(scope="module")
def store():
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from tests._fixtures import get_store
    return get_store()


@pytest.fixture
def free(monkeypatch):
    """Every skill already unlocked -- the affordable branch."""
    monkeypatch.setattr(EP, "missing_skills", lambda *a, **k: ())
    monkeypatch.setattr(EP, "unlock_cost", lambda *a, **k: 0)


@pytest.fixture
def nothing_unlocked(monkeypatch):
    """No hero owns anything -- the fallback branch, four unlocks per hero."""
    monkeypatch.setattr(EP, "missing_skills",
                        lambda _p, _h, wanted, _g=None: tuple(wanted))
    monkeypatch.setattr(EP, "unlock_cost",
                        lambda _p, _h, wanted, _g=None: 1000 * len(tuple(wanted)))


# -- the quest ------------------------------------------------------------

def test_a_plot_quest_in_a_real_region_is_taken():
    """The bug this replaces: `is_generated` refused `plot_tutorial_crypts`,
    which was the ONLY quest on the live map, and reported an empty one."""
    assert EP.pick_quest(town()) is RUINS


def test_the_courtyard_and_the_darkest_dungeon_are_refused():
    assert EP.pick_quest(town(quests=(COURTYARD, DARKEST))) is None


def test_the_shortest_quest_wins_and_gold_only_breaks_a_tie():
    assert EP.pick_quest(town(quests=(LONG_WEALD, RUINS))) is RUINS
    rich = Quest(key="4", quest_id="generated_2", dungeon="cove",
                 quest_type="explore", difficulty=1, length=1, gold_reward=8000)
    assert EP.pick_quest(town(quests=(RUINS, rich))) is rich


def test_no_playable_quest_produces_no_plan(store, tmp_path):
    assert EP.plan_embark(town(quests=(COURTYARD,)), store, tmp_path,
                          random.Random(0), GD) is None


# -- the comp -------------------------------------------------------------

def test_only_curated_comps_are_offered(store, tmp_path, free):
    """`composed_comps/` is training coverage -- nobody has played those, and
    sending a live party on one is the claim the two-folder split exists to
    keep separable."""
    rows = EP.fieldable_comps(town(), store, tmp_path, RUINS, GD)
    assert rows
    for name, _seats, _cost in rows:
        assert store.comp_loadout_by_name(name).source == EP.CURATED


def test_a_two_hero_comp_is_never_offered(store, tmp_path, free):
    """`The Old Road` is fieldable from this roster and free, so it would lead
    a list ordered by cost -- and it is the tutorial pair, not a party."""
    names = {n for n, _s, _c in EP.fieldable_comps(town(), store, tmp_path, RUINS, GD)}
    for name, seats, _cost in EP.fieldable_comps(town(), store, tmp_path, RUINS, GD):
        assert len(seats) == EP.PARTY_SIZE, name
    assert "The Old Road" not in names


def test_the_formation_is_the_comps_own_order_with_rank_1_at_the_front(store, tmp_path, free):
    plan = EP.plan_embark(town(), store, tmp_path, random.Random(0), GD)
    assert [s.rank for s in plan.party] == [1, 2, 3, 4]
    comp = store.comp_loadout_by_name(plan.comp)
    assert [s.hero.hero_class for s in plan.party] == [h.hero_id for h in comp.heroes]


def test_rank_1_is_the_rightmost_party_slot():
    """The half of the formation that lives on screen. `party_slot` is indexed
    left to right and the front rank is on the right, so seating a rank-ordered
    party into slots 0..3 would embark it REVERSED."""
    from bridge import townlayout as TL

    assert TL.party_slot_for_rank(1) == TL.party_portrait(3)
    assert TL.party_slot_for_rank(4) == TL.party_portrait(0)


def test_a_hero_is_never_seated_twice(store, tmp_path, free):
    for _name, seats, _cost in EP.fieldable_comps(town(), store, tmp_path, RUINS, GD):
        guids = [s.hero.guid for s in seats]
        assert len(set(guids)) == len(guids)


def test_the_choice_is_random_among_the_affordable_and_seed_pins_it(store, tmp_path, free):
    picks = {EP.plan_embark(town(), store, tmp_path, random.Random(s), GD).comp
             for s in range(30)}
    assert len(picks) > 1, "a coin flip that always lands the same way is a ranking"
    twice = [EP.plan_embark(town(), store, tmp_path, random.Random(7), GD).comp
             for _ in range(2)]
    assert twice[0] == twice[1]


def test_the_cheaper_of_two_same_class_heroes_is_seated(store, tmp_path, monkeypatch):
    """Not `embark.fill_shape`, which picks lowest stress within a class.

    Two Antiquarians differing in one skill is the live roster's own case, and
    choosing by stress would report a comp unaffordable that is affordable
    with the other one of the pair.
    """
    monkeypatch.setattr(EP, "missing_skills",
                        lambda _p, h, wanted, _g=None: () if h.guid == 20 else tuple(wanted))
    monkeypatch.setattr(EP, "unlock_cost",
                        lambda _p, h, wanted, _g=None: 0 if h.guid == 20 else 4000)
    # #19 has the lower guid and identical stress, so anything ordering by
    # stress alone could return either; ordering by gold cannot.
    for _name, seats, _cost in EP.fieldable_comps(town(), store, tmp_path, RUINS, GD):
        for seat in seats:
            if seat.hero.hero_class == "antiquarian":
                assert seat.hero.guid == 20


# -- the money ------------------------------------------------------------

def test_provisions_are_reserved_before_any_skill_is_bought(store, tmp_path, free):
    """Fran's order: the basket first, the skill budget out of what is left.

    The RESERVATION is the party-less 3550, because the comp has not been
    chosen when the budget is computed and the free per-class provisions are a
    property of the party. What is actually BOUGHT is that basket net of the
    grant, which is never larger -- so reserving the un-discounted amount can
    only leave the skill budget bigger than promised, never smaller."""
    # **The reservation is about the SKILL BUDGET, so it is asserted on the
    # decision, not on the final basket.** Since 2026-09-21 `plan_embark`
    # spends the remainder on torches and food AFTER the unlocks are chosen,
    # so `basket_cost` can exceed the reservation while the budget it
    # protected is untouched -- which is the property this test exists for.
    decided = EP._decide_embark(town(), store, tmp_path, random.Random(0), GD)
    plan = EP.plan_embark(town(), store, tmp_path, random.Random(0), GD)
    reserved = P.cost(P.basket("crypts", 1, "recommended"))
    assert reserved == 3550
    assert decided.basket_cost <= reserved
    party = [s.hero.hero_class for s in plan.party]
    # Since PLAYBOOK 7.7 the derived needs replace the guide item by item.
    from bridge.provision_needs import derived_basket
    assert decided.basket_cost == P.cost(
        P.basket("crypts", 1, "recommended", party=party,
                 derived=derived_basket("crypts", "short", "explore", str(GD))))
    # The top-up changed nothing the budget depended on.
    assert plan.unlocks_cost == decided.unlocks_cost
    assert plan.gold_after >= 0
    assert plan.gold_after == plan.gold - plan.basket_cost - plan.unlocks_cost


def test_the_party_is_not_sold_what_the_game_gives_it(store, tmp_path, free):
    """`raid_starting_hero_class_item_lists` hands eight classes a provision
    each on entering a raid. Buying it again is 150-500 gold a week, and the
    plan's own note has to name what was skipped -- a silent discount is
    indistinguishable from a basket bug."""
    plan = EP.plan_embark(town(), store, tmp_path, random.Random(0), GD)
    party = [s.hero.hero_class for s in plan.party]
    free_items = P.granted(party, plan.quest.length, GD)
    # **The guide is a FLOOR, not an equality, since 2026-09-21.** Leftover
    # gold is spent on torches and food after the unlocks are chosen
    # (`_spend_the_remainder`), so the basket can exceed the guide -- but
    # never fall below it, and never in anything except those two. The
    # ordering this test is about is unchanged: the reservation still happens
    # before any skill is bought, and the top-up runs on what nothing else
    # claimed.
    for item, n in free_items.items():
        if item in ("firewood", "dog_treats"):
            continue          # not sold, so never in a basket either way
        # Derived from the game where it can be (PLAYBOOK 7.7), else the guide.
        from bridge.provision_needs import derived_basket
        derived = derived_basket("crypts", "short", "explore", str(GD))
        want = derived.get(item,
                           (P.GUIDE["crypts"]["short"].get(item) or (0, 0))[1])
        floor = max(0, want - n)
        got = plan.basket.get(item, 0)
        assert got >= floor, item
        if got > floor:
            assert item in P.TOP_UP_ORDER, (
                f"{item} exceeded the guide and is not a top-up item")
    if any(k not in ("firewood",) for k in free_items):
        assert any("not bought" in note for note in plan.notes)


def test_the_wallet_never_goes_negative(store, tmp_path, nothing_unlocked):
    for gold in (0, 1000, 3550, 6670, 20000):
        plan = EP.plan_embark(town(gold=gold), store, tmp_path, random.Random(1), GD)
        assert plan.gold_after >= 0, gold
        assert plan.basket_cost + plan.unlocks_cost <= gold, gold


def test_an_unaffordable_comp_is_taken_part_equipped_from_the_front(
        store, tmp_path, nothing_unlocked):
    """Fran's "or other if i dont have any combination". The comp's classes and
    formation still stand, and the front rank -- which takes the hits -- keeps
    its unlocks while the back rank keeps its own skills."""
    plan = EP.plan_embark(town(), store, tmp_path, random.Random(0), GD)
    assert plan.unlocks_cost <= plan.gold - plan.basket_cost
    assert len(plan.party) == EP.PARTY_SIZE
    equipped = [bool(s.unlocks) for s in plan.party]
    # Whatever is dropped is dropped from the back.
    assert equipped == sorted(equipped, reverse=True)
    assert any("part-equipped" in n for n in plan.notes)


def test_a_basket_too_dear_for_the_wallet_is_cut_and_said_so(store, tmp_path, free):
    plan = EP.plan_embark(town(gold=900), store, tmp_path, random.Random(0), GD)
    assert P.cost(plan.basket) <= 900
    assert any("basket cut" in n for n in plan.notes)


def test_a_roster_that_can_field_nothing_still_produces_a_plan(store, tmp_path, free):
    thin = [hero("leper", 18), hero("jester", 21), hero("hellion", 22),
            hero("arbalest", 23, 40.0)]
    plan = EP.plan_embark(town(heroes=thin), store, tmp_path, random.Random(0), GD)
    assert plan is not None
    assert plan.unlocks_cost == 0
    assert len(plan.party) == 4
    # Lowest stress first, so the 40-stress Arbalest goes to the back.
    assert plan.party[-1].hero.guid == 23
    assert any("no curated comp" in n for n in plan.notes)


def test_the_report_names_the_quest_the_comp_and_every_rank(store, tmp_path, free):
    text = EP.plan_embark(town(), store, tmp_path, random.Random(0), GD).report()
    assert "crypts" in text
    for rank in (1, 2, 3, 4):
        assert f"rank {rank}" in text


# --- an afflicted hero is not sent, and a rested one is preferred ---

def test_a_hero_past_the_resolve_check_is_benched():
    """Three raids died on 2026-09-05 proving the game will let you do this.

    The last one fielded a Plague Doctor at 179 and a Shieldbreaker at 148 out
    of a roster holding eleven heroes at zero. At or past the check the hero is
    already afflicted -- the trait lasts the raid and no play in the dungeon
    takes it off.
    """
    pool = [hero("crusader", 1, 200.0), hero("crusader", 2, 0.0),
            hero("vestal", 3, 179.0), hero("vestal", 4, 12.0),
            hero("jester", 5, 100.0), hero("jester", 6, 99.0),
            hero("leper", 7, 0.0), hero("leper", 8, 0.0)]
    kept, benched = EP._rested(pool)
    # **Four rested heroes exist, so the STRAINED line applies** and the
    # jester at 99 sits the week out with the afflicted ones.
    assert sorted(h.guid for h in benched) == [1, 3, 5, 6]
    assert sorted(h.guid for h in kept) == [2, 4, 7, 8]
    assert EP.RESOLVE_CHECK == 100.0
    assert EP.STRAINED == 50.0


def test_the_STRAINED_line_gives_way_rather_than_refuse_the_week():
    """Never starves. With fewer than four heroes under 50 the gate falls
    back to the affliction line -- a party of three is worse than a party of
    four with a tired hero in it, and a campaign that cannot field anybody is
    one the Hamlet has to fix rather than one this should refuse."""
    pool = [hero("crusader", 1, 60.0), hero("vestal", 2, 70.0),
            hero("jester", 3, 80.0), hero("leper", 4, 0.0),
            hero("leper", 5, 120.0)]
    kept, benched = EP._rested(pool)
    assert sorted(h.guid for h in kept) == [1, 2, 3, 4], "it starved the week"
    assert sorted(h.guid for h in benched) == [5]


def test_it_sends_EVERYBODY_when_even_that_is_not_enough():
    pool = [hero("crusader", 1, 150.0), hero("vestal", 2, 170.0),
            hero("jester", 3, 180.0), hero("leper", 4, 190.0)]
    kept, benched = EP._rested(pool)
    assert len(kept) == 4 and benched == []


def test_it_would_rather_send_a_tired_hero_than_a_party_of_three():
    """Never starves. Under four rested heroes the whole pool comes back.

    A campaign that cannot field anybody is one the Hamlet has to fix; refusing
    here would only replace a bad raid with no raid, and the notes say who is
    being sent anyway.
    """
    pool = [hero("crusader", 1, 200.0), hero("vestal", 2, 150.0),
            hero("jester", 3, 0.0), hero("leper", 4, 0.0),
            hero("hellion", 5, 120.0)]
    kept, benched = EP._rested(pool)
    assert benched == []
    assert len(kept) == len(pool)


def test_an_unequipped_slot_goes_to_the_freshest_hero_of_its_class():
    """`fill_cheapest` ranks by gold, and once nothing is bought gold is moot.

    Live at 15 gold: the plan seated Highwayman #2 at 87 stress to save an
    unlock bill it then did not pay, while #28, #64 and #65 sat at zero.
    """
    seated = hero("highwayman", 2, 87.0, 1)
    fresh = hero("highwayman", 64, 0.0)
    pool = [seated, fresh, hero("highwayman", 65, 40.0),
            hero("crusader", 9, 5.0)]
    seats = (EP.Seat(rank=1, hero=seated, skills=("a", "b")),)
    got = EP._reseat_unbought(seats, pool)
    assert got[0].hero.guid == 64
    assert got[0].skills == ("a", "b"), "the comp's skills stay with the slot"
    assert got[0].rank == 1


def test_a_hero_whose_skills_were_BOUGHT_is_never_swapped_out():
    """The money follows the hero, so a paid-for slot is settled."""
    seated = hero("highwayman", 2, 87.0, 1)
    pool = [seated, hero("highwayman", 64, 0.0)]
    seats = (EP.Seat(rank=1, hero=seated, skills=("a",), unlocks=("a",),
                     cost=1000),)
    assert EP._reseat_unbought(seats, pool)[0].hero.guid == 2


def test_reseating_never_seats_one_hero_twice():
    """Two slots of one class, and the fresh hero can only fill one of them."""
    a = hero("highwayman", 2, 87.0)
    b = hero("highwayman", 3, 90.0)
    fresh = hero("highwayman", 64, 0.0)
    seats = (EP.Seat(rank=1, hero=a), EP.Seat(rank=2, hero=b))
    got = EP._reseat_unbought(seats, [a, b, fresh])
    guids = [seat.hero.guid for seat in got]
    assert len(set(guids)) == 2
    assert 64 in guids


# --- four heroes or nobody goes ---

def test_a_short_party_is_flagged_on_the_plan_itself():
    """Fran: *"we should make sure we are sending 4 people comps during
    embark"*.

    The loop sent a two-hero party into an eight-room Crypts twice on
    2026-09-05 and it wiped both times. The plan had said so three lines above
    the drags -- "only 2 heroes are free and under the resolve cap" -- in
    prose, which nothing reads. This is the same fact where a caller can act
    on it.
    """
    full = tuple(EP.Seat(rank=i + 1, hero=hero("crusader", i))
                 for i in range(EP.PARTY_SIZE))
    assert not EP.EmbarkPlan(quest=RUINS, comp="x", party=full).under_strength
    assert EP.EmbarkPlan(quest=RUINS, comp="x", party=full[:2]).under_strength
    assert EP.EmbarkPlan(quest=RUINS, comp="x").under_strength


def test_the_embark_chain_refuses_a_short_party_before_it_spends_anything(
        monkeypatch):
    """Refused BEFORE the drags, the guild and the provisions.

    Seating is reversible and `to_raid` is not -- it buys the basket and starts
    a raid. And refusing cannot deadlock a night: the coach visit and the week
    skip both run before this in `tools/overnight.py`, so a roster that is
    short today gets a week to fix it.

    The two seams that touch the screen are stubbed, which is what lets the
    rest of the chain be exercised with no game running.
    """
    from bridge import town

    monkeypatch.setattr(town, "_look", lambda win: "town")
    monkeypatch.setattr(town, "walk", lambda *a, **k: town.Walked(
        ok=True, steps=1, seen=["estate_map"], stopped_on="estate_map"))
    plan = EP.EmbarkPlan(quest=RUINS, comp="(no comp)",
                         party=(EP.Seat(rank=1, hero=hero("crusader", 1)),
                                EP.Seat(rank=2, hero=hero("highwayman", 2))))
    done = town.embark_setup(object(), None, "profile", plan=plan,
                             say=lambda *a: None, act=False)
    last = done[-1]
    assert last.what == "embark" and not last.confirmed
    assert "only 2 hero(es)" in last.why
    assert not any(d.what.startswith("seat ") for d in done), \
        "it dragged before refusing"


def test_a_full_party_is_not_refused(monkeypatch):
    """The other half, or the guard would pass by refusing everything.

    A full party walks straight past it and on into the screen code, which the
    stub window cannot answer -- so the AttributeError IS the evidence, and it
    is a sharper one than a clean return would be: the guard returns a list, so
    anything that raises got past it. The short-party case above returns.
    """
    from bridge import town

    monkeypatch.setattr(town, "_look", lambda win: "town")
    monkeypatch.setattr(town, "walk", lambda *a, **k: town.Walked(
        ok=True, steps=1, seen=["estate_map"], stopped_on="estate_map"))
    plan = EP.EmbarkPlan(quest=RUINS, comp="x", party=tuple(
        EP.Seat(rank=i + 1, hero=hero("crusader", i))
        for i in range(EP.PARTY_SIZE)))
    with pytest.raises(AttributeError, match="client_size"):
        town.embark_setup(object(), None, "profile", plan=plan,
                          say=lambda *a: None, act=False)


# -- the formation the FALLBACK seats ------------------------------------
#
# No curated comp is fieldable from a four-hero roster, so this branch is the
# only one the whole early campaign takes -- and it seated by stress and
# called the sort order a formation. Measured live 2026-09-06 on the first
# post-tutorial roster: Plague Doctor rank 1, Vestal rank 2, Crusader rank 3,
# Highwayman rank 4, which is the healers standing in front of the front-liner.


def test_the_starting_four_are_stood_where_they_can_ACT(store, tmp_path):
    """The check that the derivation measures the right thing is that it
    reproduces common knowledge unaided -- Crusader front, Plague Doctor back
    -- from nothing but the shipped `.launch` lists."""
    four = [hero("crusader", 1, 7.0, 1), hero("highwayman", 2, 14.0, 1),
            hero("plague_doctor", 7), hero("vestal", 8)]
    plan = EP.plan_embark(town(heroes=four), store, tmp_path,
                          random.Random(0), GD)
    assert plan is not None
    where = {s.hero.hero_class: s.rank for s in plan.party}
    assert where["crusader"] == 1
    assert where["plague_doctor"] == 4
    assert where["vestal"] in (2, 3)


def test_the_formation_beats_the_order_it_was_handed(store):
    """A maximum, not a nudge: the seating it replaces is a legal input here,
    and must score strictly worse."""
    worst = [hero("plague_doctor", 7), hero("vestal", 8),
             hero("crusader", 1), hero("highwayman", 2)]

    def score(order):
        return sum(EP.launchable_from(store, h.hero_class, r + 1)
                   for r, h in enumerate(order))

    assert score(EP.best_formation(store, worst)) > score(worst)


def test_a_TIE_keeps_the_order_it_was_handed(store):
    """So the stress sort still decides, and two runs agree."""
    same = [hero("crusader", 1), hero("crusader", 2),
            hero("crusader", 3), hero("crusader", 4)]
    assert [h.hero.guid for h in EP.best_formation(store, same)] == [1, 2, 3, 4]


def test_a_party_of_ONE_is_not_permuted(store):
    one = [hero("vestal", 8)]
    assert EP.best_formation(store, one) == one
    assert EP.best_formation(store, []) == []


def test_an_unknown_class_is_counted_as_ZERO_not_crashed(store):
    """A modded class the store could not load must not take the embark down
    with it -- the fallback is the path that runs when nothing else does."""
    assert EP.launchable_from(store, "no_such_hero", 1) == 0


def test_the_derivation_AGREES_with_the_search_on_every_class(store):
    """Two copies of one rule is how this repo has twice ended up with two
    answers, so they are pinned against each other rather than trusted.

    `tools/comp_ga.py` is the search and may not be imported by `bridge/`;
    the test may import both, which is the whole point of putting the
    cross-check here.
    """
    from tools.comp_ga import CompSpace, PARTY_SIZE, SKILLS_PER_HERO

    space = CompSpace(store)
    for hero_id in sorted(space.classes):
        for rank in range(1, PARTY_SIZE + 1):
            assert EP.launchable_from(store, hero_id, rank) ==                 len(space.usable_from(hero_id, rank)), (hero_id, rank)
        # ...and therefore on the rule the search actually enforces.
        want = min(SKILLS_PER_HERO, len(space.skill_pool.get(hero_id, ())))
        mine = frozenset(r for r in range(1, PARTY_SIZE + 1)
                         if EP.launchable_from(store, hero_id, r) >= want)
        assert (mine or frozenset(range(1, PARTY_SIZE + 1))) ==             space.viable_ranks(hero_id), hero_id

# -- the top-up has a floor -------------------------------------------------

def test_the_top_up_leaves_a_RESERVE(store, tmp_path, free):
    """**"Max if possible" is not "all of it".** Measured live 2026-09-22:
    the top-up took the wallet 5,070 -> 20, which is 99.6% of the estate's
    gold into one expedition's food and torches. The next week could then buy
    no skill unlock (1,000), no treatment (1,000-1,250) and no recruit -- and
    a retreat, which the survival layer calls for often at Resolve 0, threw
    the lot away.
    """
    import bridge.embarkplan as EP

    assert EP.TOP_UP_RESERVE >= 2000, (
        "the floor has to cover a skill unlock plus a treatment")
    plan = EP.plan_embark(town(), store, tmp_path, random.Random(0), GD)
    assert plan.gold_after >= 0
    # The reserve is only claimed when there IS a surplus; it never cuts the
    # guide basket, so a party cannot go out under-provisioned because of it.
    decided = EP._decide_embark(town(), store, tmp_path, random.Random(0), GD)
    assert plan.basket_cost >= decided.basket_cost


def test_a_poor_estate_tops_up_NOTHING_rather_than_going_negative(
        store, tmp_path, free):
    """With less than the reserve spare there is no surplus to spend, and the
    basket is the guide's -- untouched."""
    import bridge.embarkplan as EP
    from dataclasses import replace as _replace

    decided = EP._decide_embark(town(), store, tmp_path, random.Random(0), GD)
    poor = decided.basket_cost + decided.unlocks_cost + 10
    out = EP._spend_the_remainder(decided, decided.quest, poor, str(GD))
    assert out.basket == decided.basket, "it spent into the reserve"



# -- the embark veto (2026-09-25) ------------------------------------------
#
# The flip stays; a draw the survival layer would abandon at the entrance is
# re-drawn. Live that night a drawn party read 0% / 3.2 deaths at full HP in
# the entrance room, retreated after one fight, and lost 4,550g of provisions.


def test_no_veto_leaves_the_flip_exactly_as_it_was(store, tmp_path, free):
    a = EP.plan_embark(town(), store, tmp_path, random.Random(7), GD).comp
    b = EP.plan_embark(town(), store, tmp_path, random.Random(7), GD,
                       veto=None).comp
    assert a == b


def test_a_vetoed_draw_is_redrawn(store, tmp_path, free):
    first = EP.plan_embark(town(), store, tmp_path, random.Random(7), GD).comp
    asked = []

    def veto(quest, seats):
        asked.append(len(seats))
        return "3.2 expected deaths" if len(asked) == 1 else ""

    plan = EP.plan_embark(town(), store, tmp_path, random.Random(7), GD,
                          veto=veto)
    assert len(asked) == 2
    assert plan.comp != first
    assert any(n.startswith(f"VETO {first}") for n in plan.notes)


def test_when_every_draw_is_vetoed_the_first_one_stands(store, tmp_path, free):
    first = EP.plan_embark(town(), store, tmp_path, random.Random(7), GD).comp
    plan = EP.plan_embark(town(), store, tmp_path, random.Random(7), GD,
                          veto=lambda q, s: "hopeless")
    assert plan.comp == first
    assert any("every re-draw was vetoed" in n for n in plan.notes)


# -- Fran's unlock rules (2026-09-25) --------------------------------------
#
# The coin flip drew a new comp every raid and bought 4-5k of skills each
# time; gold went 17,265 -> 1,185 in one night. Level-0 heroes are never
# bought skills, unlocks never take more than UNLOCK_GOLD_FRACTION of the gold
# on hand, and the flip is weighted toward comps needing fewer unlocks.


def test_fodder_is_never_bought_skills(store, tmp_path, nothing_unlocked):
    """Level 1 on XP alone with nothing bought is fodder too (Fran's Mortmain
    case, 2026-09-26), not only level 0."""
    green = [hero(h.hero_class, h.guid, h.stress, resolve=h.guid % 2, weapon=0)
             for h in ROSTER]
    plan = EP.plan_embark(town(heroes=green, gold=10**6), store, tmp_path,
                          random.Random(0), GD)
    assert plan.unlocks_cost == 0
    assert all(not s.unlocks for s in plan.party)


def test_unlocks_never_take_more_than_a_tenth_of_the_gold(store, tmp_path,
                                                          nothing_unlocked):
    for gold in (6670, 20000, 60000):
        plan = EP.plan_embark(town(gold=gold), store, tmp_path,
                              random.Random(0), GD)
        assert plan.unlocks_cost <= EP.UNLOCK_GOLD_FRACTION * gold, (gold, plan.unlocks_cost)


def test_the_flip_favours_comps_needing_fewer_unlocks(store, tmp_path, monkeypatch):
    """One comp needs nothing, three need four unlocks each. Unweighted the
    free one comes up a quarter of the time; weighted 1/(1+n)^2 it is about
    86%."""
    party = ROSTER[:EP.PARTY_SIZE]

    def comp(name, n):
        seats = tuple(EP.Seat(rank=i + 1, hero=h, skills=("a", "b", "c", "d"),
                              unlocks=("x",) * n if i == 0 else (), cost=0)
                      for i, h in enumerate(party))
        return (name, seats, 0)

    fake = [comp("free", 0), comp("dear1", 4), comp("dear2", 4), comp("dear3", 4)]
    monkeypatch.setattr(EP, "fieldable_comps", lambda *a, **k: list(fake))
    drawn = [EP.plan_embark(town(gold=10**6), store, tmp_path, random.Random(s), GD).comp
             for s in range(400)]
    rate = drawn.count("free") / len(drawn)
    assert rate > 0.7, rate
    assert set(drawn) != {"free"}, "a weighted flip is still a flip"


def test_the_draw_favours_invested_rested_one_band_parties():
    inv = [hero(c, i, 0.0) for i, c in enumerate("abcd")]
    fod = [hero(c, i, 0.0, weapon=0) for i, c in enumerate("abcd")]
    tired = [hero(c, i, 150.0) for i, c in enumerate("abcd")]
    mixed = inv[:3] + [hero("e", 9, 0.0, resolve=5)]
    assert EP.party_weight(inv) > EP.party_weight(fod)
    assert EP.party_weight(inv) > EP.party_weight(tired)
    assert EP.party_weight(inv) > EP.party_weight(mixed)


def test_the_top_up_fills_open_stacks_and_never_opens_a_slot():
    """2026-09-27: "+6 food, +9 torch" opened two surplus slots per raid."""
    got = EP.fill_open_stacks({"food": 12, "torch": 9}, {"food": 18, "torch": 18})
    assert got == {"food": 12, "torch": 16}
    got = EP.fill_open_stacks({"food": 8, "torch": 8}, {"food": 24, "torch": 12})
    assert got == {"food": 12, "torch": 8}


# -- climbing the difficulty (2026-10-05) -----------------------------------

VET_RUINS = Quest(key="5", quest_id="generated_5", dungeon="crypts",
                  quest_type="cleanse", difficulty=3, length=1, gold_reward=5000)
NECRO_JUNK = Quest(key="6", quest_id="plot_kill_necromancer_1", dungeon="crypts",
                   quest_type="kill_boss", difficulty=1, length=2, gold_reward=4500,
                   reward_trinkets=("dark_tambourine",))
NECRO_GOOD = Quest(key="7", quest_id="plot_kill_necromancer_1", dungeon="crypts",
                   quest_type="kill_boss", difficulty=1, length=2, gold_reward=4500,
                   reward_trinkets=("blasphemous_vial",))


def _veterans(level=3):
    return [hero(c, 100 + i, 0.0, level) for i, c in
            enumerate(("crusader", "highwayman", "plague_doctor", "vestal"))]


def test_a_levelled_roster_climbs_to_veteran():
    """The 2026-09-28 audit: shortest-first kept the campaign at Apprentice for
    77 weeks. Four rested level-3 heroes are a Veteran party -- and the game
    would refuse to send them on the Apprentice one anyway."""
    assert EP.pick_quest(town(quests=(RUINS, VET_RUINS), heroes=_veterans(3))) is VET_RUINS


def test_a_green_roster_stays_at_apprentice():
    assert EP.pick_quest(town(quests=(RUINS, VET_RUINS))) is RUINS


def test_one_underlevelled_hero_is_allowed_two_are_not():
    one = _veterans(3)[:3] + [hero("leper", 200, 0.0, 2)]
    two = _veterans(3)[:2] + [hero("leper", 200, 0.0, 2), hero("jester", 201, 0.0, 2)]
    assert EP.pick_quest(town(quests=(RUINS, VET_RUINS), heroes=one)) is VET_RUINS
    assert EP.pick_quest(town(quests=(RUINS, VET_RUINS), heroes=two)) is RUINS


def test_a_boss_only_for_a_trinket_worth_it():
    """Fran passed on every boss whose very rare sat low on his tier list."""
    assert EP.pick_quest(town(quests=(RUINS, NECRO_JUNK))) is RUINS
    assert EP.pick_quest(town(quests=(RUINS, NECRO_GOOD))) is NECRO_GOOD


def test_a_boss_is_never_taken_underlevelled():
    green = [hero(c, 300 + i, 0.0, 0) for i, c in
             enumerate(("crusader", "highwayman", "plague_doctor", "vestal"))]
    assert EP.pick_quest(town(quests=(RUINS, NECRO_GOOD), heroes=green)) is RUINS


def test_nothing_in_reach_never_falls_back_to_a_boss():
    """The fallback took the lowest difficulty, and an Apprentice boss is one."""
    green = [hero(c, 300 + i, 0.0, 0) for i, c in
             enumerate(("crusader", "highwayman", "plague_doctor", "vestal"))]
    assert EP.pick_quest(town(quests=(NECRO_GOOD,), heroes=green)) is None
    assert EP.pick_quest(town(quests=(NECRO_GOOD, LONG_WEALD), heroes=green)) is LONG_WEALD


def test_a_boss_party_is_seated_at_the_quest_level(store, tmp_path):
    """`quest_in_reach` checks the top four; the comp draw used to seat the
    cheapest, level-0 fodder included."""
    # Without the fix this seats two of the level-0s on every seed.
    vets = ("leper", "jester", "occultist", "antiquarian")
    green = ("crusader", "highwayman", "plague_doctor", "vestal")
    both = ([hero(c, 400 + i, 0.0, 1) for i, c in enumerate(vets)]
            + [hero(c, 500 + i, 0.0, 0) for i, c in enumerate(green)])
    for s in range(6):
        plan = EP.plan_embark(town(quests=(NECRO_GOOD,), heroes=both), store, tmp_path,
                              random.Random(s), GD)
        assert plan.quest is NECRO_GOOD
        assert all(seat.hero.resolve_level >= 1 for seat in plan.party), plan.report()

def test_a_boss_party_is_the_highest_level_the_quest_allows(store, tmp_path):
    """Fran, 2026-10-12: an Apprentice boss goes with level-2 heroes when the
    roster has them, not the level-1s the quest only requires."""
    twos = ("leper", "jester", "occultist", "antiquarian")
    ones = ("crusader", "highwayman", "plague_doctor", "vestal")
    both = ([hero(c, 400 + i, 0.0, 2) for i, c in enumerate(twos)]
            + [hero(c, 500 + i, 0.0, 1) for i, c in enumerate(ones)])
    t = town(quests=(NECRO_GOOD,), heroes=both, gold=400000)
    fieldable = EP.fieldable_comps(t, store, tmp_path, NECRO_GOOD, GD)
    best = max(sum(st.hero.resolve_level for st in c[1]) for c in fieldable)
    assert min(sum(st.hero.resolve_level for st in c[1]) for c in fieldable) < best
    for s in range(6):
        plan = EP.plan_embark(t, store, tmp_path, random.Random(s), GD)
        assert plan.quest is NECRO_GOOD
        assert sum(seat.hero.resolve_level for seat in plan.party) == best, plan.report()

def test_a_veteran_party_is_seated_with_at_most_one_underlevelled(store, tmp_path):
    """The same mismatch for an ordinary quest: four Veterans put the
    Veteran quest in reach, and the draw seated the level-0s."""
    vets = ("crusader", "highwayman", "plague_doctor", "vestal")
    green = ("leper", "jester", "occultist", "antiquarian")
    both = ([hero(c, 400 + i, 0.0, 3) for i, c in enumerate(vets)]
            + [hero(c, 500 + i, 0.0, 0) for i, c in enumerate(green)])
    for s in range(6):
        plan = EP.plan_embark(town(quests=(VET_RUINS,), heroes=both), store, tmp_path,
                              random.Random(s), GD)
        assert plan.quest is VET_RUINS
        assert sum(seat.hero.resolve_level < 3 for seat in plan.party) <= 1, plan.report()

def test_with_no_comp_at_level_the_fewest_underlevelled_are_seated(store, tmp_path):
    """Four Veterans whose classes no curated comp combines without the
    level-0s (and level-0 copies of the Veterans' own classes, which the
    cheapest fill prefers): the draw keeps the comps seating the fewest."""
    vets = ("leper", "jester", "occultist", "antiquarian")
    green = ("crusader", "highwayman", "plague_doctor", "vestal") + vets
    both = ([hero(c, 400 + i, 0.0, 3) for i, c in enumerate(vets)]
            + [hero(c, 500 + i, 0.0, 0) for i, c in enumerate(green)])
    fieldable = EP.fieldable_comps(town(quests=(VET_RUINS,), heroes=both), store, tmp_path,
                                   VET_RUINS, GD)
    fewest = min(sum(st.hero.resolve_level < 3 for st in c[1]) for c in fieldable)
    assert fewest > EP.MAX_UNDERLEVELED          # the case this is about
    for s in range(6):
        # Gold enough for every comp, so the flip (not the cheapest) decides.
        plan = EP.plan_embark(town(quests=(VET_RUINS,), heroes=both, gold=400000), store,
                              tmp_path, random.Random(s), GD)
        assert sum(seat.hero.resolve_level < 3 for seat in plan.party) <= fewest, plan.report()

def test_the_level_gap_knob_bars_a_far_underlevelled_seat():
    """`UNDERLEVEL_MAX_GAP` (experiment, default None): three Veterans and
    a level-0 reach a Veteran quest as coded; at a gap of 1 they do not, and
    three Veterans and a level-2 still do."""
    far = _veterans(3)[:3] + [hero("leper", 200, 0.0, 0)]
    near = _veterans(3)[:3] + [hero("leper", 200, 0.0, 2)]
    assert EP.UNDERLEVEL_MAX_GAP is None
    assert EP.pick_quest(town(quests=(RUINS, VET_RUINS), heroes=far)) is VET_RUINS
    saved = EP.UNDERLEVEL_MAX_GAP
    try:
        EP.UNDERLEVEL_MAX_GAP = 1
        assert EP.pick_quest(town(quests=(RUINS, VET_RUINS), heroes=far)) is RUINS
        assert EP.pick_quest(town(quests=(RUINS, VET_RUINS), heroes=near)) is VET_RUINS
    finally:
        EP.UNDERLEVEL_MAX_GAP = saved

def test_the_boss_level_margin_raises_the_bar():
    """`BOSS_LEVEL_MARGIN` (experiment, default 0): at 1, a level-1 party no
    longer reaches an Apprentice boss and a level-2 one does."""
    ones = [hero(c, 600 + i, 0.0, 1) for i, c in
            enumerate(("crusader", "highwayman", "plague_doctor", "vestal"))]
    twos = [hero(c, 700 + i, 0.0, 2) for i, c in
            enumerate(("crusader", "highwayman", "plague_doctor", "vestal"))]
    assert EP.BOSS_LEVEL_MARGIN == 0
    assert EP.pick_quest(town(quests=(RUINS, NECRO_GOOD), heroes=ones)) is NECRO_GOOD
    saved = EP.BOSS_LEVEL_MARGIN
    try:
        EP.BOSS_LEVEL_MARGIN = 1
        assert EP.pick_quest(town(quests=(RUINS, NECRO_GOOD), heroes=ones)) is RUINS
        assert EP.pick_quest(town(quests=(RUINS, NECRO_GOOD), heroes=twos)) is NECRO_GOOD
    finally:
        EP.BOSS_LEVEL_MARGIN = saved

