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


def hero(hero_class: str, guid: int, stress: float = 0.0, resolve: int = 0):
    return RosterHero(hero=LiveHero(guid=guid, hero_class=hero_class,
                                    stress=stress, resolve_level=resolve))


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
    plan = EP.plan_embark(town(), store, tmp_path, random.Random(0), GD)
    reserved = P.cost(P.basket("crypts", 1, "recommended"))
    assert reserved == 3550
    assert plan.basket_cost <= reserved
    party = [s.hero.hero_class for s in plan.party]
    assert plan.basket_cost == P.cost(
        P.basket("crypts", 1, "recommended", party=party))
    assert plan.gold_after == plan.gold - plan.basket_cost - plan.unlocks_cost


def test_the_party_is_not_sold_what_the_game_gives_it(store, tmp_path, free):
    """`raid_starting_hero_class_item_lists` hands eight classes a provision
    each on entering a raid. Buying it again is 150-500 gold a week, and the
    plan's own note has to name what was skipped -- a silent discount is
    indistinguishable from a basket bug."""
    plan = EP.plan_embark(town(), store, tmp_path, random.Random(0), GD)
    party = [s.hero.hero_class for s in plan.party]
    free_items = P.granted(party, plan.quest.length, GD)
    for item, n in free_items.items():
        if item in ("firewood", "dog_treats"):
            continue          # not sold, so never in a basket either way
        want = (P.GUIDE["crypts"]["short"].get(item) or (0, 0))[1]
        assert plan.basket.get(item, 0) == max(0, want - n), item
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
    assert sorted(h.guid for h in benched) == [1, 3, 5]
    assert sorted(h.guid for h in kept) == [2, 4, 6, 7, 8]
    # 99 stays and 100 goes: the cut is the Resolve Check itself, not a margin
    # somebody chose.
    assert EP.RESOLVE_CHECK == 100.0


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
