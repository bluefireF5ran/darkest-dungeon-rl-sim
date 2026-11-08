"""Who goes to the Abbey — the thirteenth decision.

A Darkest Dungeon player makes roughly nineteen kinds of decision per cycle and
this project decided twelve. Stress treatment was missing despite being the
cheapest to add: the save already carries every hero's stress and which
building they are in, and the Abbey and Tavern ship their price, their heal and
their slot count per upgrade tier. Deciding needs no screen at all — only
ACTING on it does.

Everything here is read from shipped data except one threshold, and these tests
pin the parts that would rot silently: the exclusions (which are correctness,
not preference) and the ordering rule (which is the whole argument for the
feature).
"""

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from bridge.town_state import RosterHero, TownState                # noqa: E402
from bridge.townplan import TREAT_ABOVE, choose_treatments         # noqa: E402
from data.town_costs import load_stress_treatments                 # noqa: E402
from env.gym_env import GAME_DIR                                   # noqa: E402


_GUIDS = iter(range(1, 10**6))


def _hero(name, cls, stress, building="", in_party=False, level=3):
    # Level 3 by default: since 2026-09-26 only an INVESTED or NURTURE hero is
    # treated (`bridge/hero_roles.py`), and a level-3 band is invested by
    # itself; fodder is replaced from the coach instead.
    return RosterHero(hero=SimpleNamespace(name=name, hero_class=cls, stress=stress,
                                           resolve_level=level, quirks=[],
                                           guid=next(_GUIDS)),
                      building=building, in_party=in_party)


def _town(heroes, gold=100_000):
    return TownState(heroes=heroes, wallet={"gold": gold})


def test_the_prices_and_slots_are_shipped_data():
    """Six activities across two buildings, 1 slot each un-upgraded and 3 maxed.

    If this fails the game's files moved, and every number the planner spends
    is wrong rather than merely stale.
    """
    acts = load_stress_treatments(str(GAME_DIR))
    assert len(acts) == 6, [a.activity for a in acts]
    assert {a.building for a in acts} == {"abbey", "tavern"}
    assert sum(a.slots_at(0) for a in acts) == 6
    assert sum(a.slots_at(2) for a in acts) == 18
    cheapest = min(a.cost_at(0) for a in acts)
    assert cheapest == 1000, cheapest


def test_a_hero_already_in_a_building_is_never_re_treated():
    """`RosterHero.building` is non-empty exactly while a hero is being
    treated. Sending them again would buy a slot that does nothing."""
    town = _town([_hero("Barristan", "man_at_arms", 95, building="abbey")])
    assert choose_treatments(town, GAME_DIR) == []


def test_a_hero_marked_for_the_party_is_never_treated():
    """A hero in a building cannot embark, so treating one silently cancels
    the raid it was selected for. That is a correctness rule, not a taste."""
    town = _town([_hero("Tardif", "leper", 99, in_party=True)])
    assert choose_treatments(town, GAME_DIR) == []


def test_below_the_threshold_nobody_is_sent():
    """The cheapest slot removes 45 stress; treating a hero at 20 throws most
    of it away and that hero is nowhere near an affliction."""
    town = _town([_hero("Boudica", "hellion", TREAT_ABOVE - 1)])
    assert choose_treatments(town, GAME_DIR) == []
    town = _town([_hero("Boudica", "hellion", TREAT_ABOVE + 1)])
    assert len(choose_treatments(town, GAME_DIR)) == 1


def test_the_most_stressed_hero_is_treated_first():
    """The ordering IS the argument for the feature: the hero nearest the
    Resolve Check is the one an affliction is about to cost, and
    `--affliction-penalty` measured afflictions as largely unpreventable once
    a raid is under way."""
    town = _town([_hero("low", "vestal", 75), _hero("high", "highwayman", 95),
                  _hero("mid", "crusader", 85)])
    got = [t.hero_name for t in choose_treatments(town, GAME_DIR)]
    assert got == ["high", "mid", "low"], got


def test_the_gold_buys_the_most_HEROES_not_the_most_stress():
    """Cheapest slot first while slots are scarce. An untreated hero at 90 is
    a coming affliction and a treated one at 45 is not, so covering two beats
    over-healing one."""
    # A tenth of the bank is the spend (the Guild rule, 2026-09-26), so a
    # 22,500 bank is the 2,250 this test is about.
    town = _town([_hero("a", "vestal", 90), _hero("b", "crusader", 90)],
                 gold=22500)
    got = choose_treatments(town, GAME_DIR)
    # Abbey only since PLAYBOOK 7.4: its two cheapest slots, 1000 + 1250.
    assert len(got) == 2, "2250 gold buys the two cheapest slots, not one dear one"
    assert sorted(t.cost for t in got) == [1000, 1250], [t.cost for t in got]
    assert all(t.building == "abbey" for t in got)


def test_it_stops_at_the_wallet():
    """A plan that spends gold the town does not have is not a plan."""
    town = _town([_hero(f"h{i}", "vestal", 90) for i in range(6)], gold=25000)
    got = choose_treatments(town, GAME_DIR)
    assert sum(t.cost for t in got) <= 2500
    assert len(got) == 2, [t.cost for t in got]


def test_capacity_is_bounded_by_the_SLOTS_not_by_the_roster():
    """Twelve stressed heroes and a full wallet still only fill what the
    buildings have. The Abbey alone since PLAYBOOK 7.4 (the tavern can cost a
    hero trinkets): three un-upgraded, nine maxed."""
    town = _town([_hero(f"h{i}", "vestal", 90) for i in range(12)])
    assert len(choose_treatments(town, GAME_DIR, tier=0)) == 3
    assert len(choose_treatments(town, GAME_DIR, tier=2)) == 9


def test_fodder_never_uses_the_abbey_and_nurture_does():
    """Fran, 2026-09-26: fodder "shouldnt use the facilities of the hamlet at
    all" -- not even on a heavy bank, which superseded that day's earlier
    "S-tier quirk or heavy gold" exception. A nurture hero is treated."""
    from bridge import hero_roles
    green = _hero("g", "vestal", 95, level=0)
    assert choose_treatments(_town([green], gold=10**6), GAME_DIR) == []
    as_nurture = {green.guid: hero_roles.HeroRole(
        guid=green.guid, hero_class="vestal", level=0, band="0-2",
        role="nurture", sunk=0, nurture=9.0, stress=95)}
    assert len(choose_treatments(_town([green], gold=20_000), GAME_DIR,
                                 roles=as_nurture)) == 1


def test_an_afflicted_invested_hero_is_treated_below_the_threshold():
    """The town is the only place an affliction comes off."""
    h = _hero("a", "vestal", 40)
    h.hero.affliction_id = "masochistic"
    assert len(choose_treatments(_town([h]), GAME_DIR)) == 1


def test_treatments_never_take_more_than_a_tenth_of_the_gold():
    town = _town([_hero(f"h{i}", "vestal", 95) for i in range(3)], gold=13_315)
    got = choose_treatments(town, GAME_DIR)
    assert sum(t.cost for t in got) <= 1331, [t.cost for t in got]


def test_a_flagellant_goes_to_the_penance_hall_only():
    """Live 2026-09-27: prayer and meditation both refused a Flagellant; his
    class allows flagellation (and the Sanitarium) and nothing else."""
    flag = _hero("f", "flagellant", 95)
    got = choose_treatments(_town([flag]), GAME_DIR)
    assert [t.activity for t in got] == ["flagellation"]


def test_a_barred_quirk_keeps_a_hero_out_of_that_activity():
    h = _hero("a", "vestal", 95)
    h.hero.quirks = ["alcoholism"]
    got = choose_treatments(_town([h]), GAME_DIR)
    assert all(t.activity not in ("meditation", "prayer", "flagellation")
               for t in got)


def test_a_slot_held_by_a_resident_is_not_planned():
    """Live 2026-09-28: the Cloister's one unlocked slot held a resident
    (`resident_occupied 0`, `hero 0`), read as free, and every meditation
    failed. A resident is read as -1: taken, no hero."""
    town = _town([_hero("v", "vestal", 95)])
    town.activity_slots = {("abbey", "meditation"): [-1, 0, 0]}
    got = choose_treatments(town, GAME_DIR, owned={"abbey.meditation": set()})
    assert all(t.activity != "meditation" for t in got)



if __name__ == "__main__":
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            _fn()
            print(f"ok {_name}")
