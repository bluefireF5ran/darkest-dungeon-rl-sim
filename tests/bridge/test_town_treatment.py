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


def _hero(name, cls, stress, building="", in_party=False):
    return RosterHero(hero=SimpleNamespace(name=name, hero_class=cls, stress=stress),
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
    town = _town([_hero("low", "vestal", 55), _hero("high", "highwayman", 95),
                  _hero("mid", "crusader", 75)])
    got = [t.hero_name for t in choose_treatments(town, GAME_DIR)]
    assert got == ["high", "mid", "low"], got


def test_the_gold_buys_the_most_HEROES_not_the_most_stress():
    """Cheapest slot first while slots are scarce. An untreated hero at 90 is
    a coming affliction and a treated one at 45 is not, so covering two beats
    over-healing one."""
    town = _town([_hero("a", "vestal", 90), _hero("b", "crusader", 90)],
                 gold=2000)
    got = choose_treatments(town, GAME_DIR)
    assert len(got) == 2, "2000 gold buys two 1000g slots, not one dear one"
    assert all(t.cost == 1000 for t in got), [t.cost for t in got]


def test_it_stops_at_the_wallet():
    """A plan that spends gold the town does not have is not a plan."""
    town = _town([_hero(f"h{i}", "vestal", 90) for i in range(6)], gold=2500)
    got = choose_treatments(town, GAME_DIR)
    assert sum(t.cost for t in got) <= 2500
    assert len(got) == 2, [t.cost for t in got]


def test_capacity_is_bounded_by_the_SLOTS_not_by_the_roster():
    """Twelve stressed heroes and a full wallet still only fill what the
    buildings have: six un-upgraded, eighteen maxed."""
    town = _town([_hero(f"h{i}", "vestal", 90) for i in range(12)])
    assert len(choose_treatments(town, GAME_DIR, tier=0)) == 6
    assert len(choose_treatments(town, GAME_DIR, tier=2)) == 12  # roster-bound


if __name__ == "__main__":
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            _fn()
            print(f"ok {_name}")
