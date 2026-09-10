"""The provision basket: one declared table, and everything else parsed.

`data/provisions.py` is deliberately two halves. `GUIDE` is PLAYBOOK 1.5 --
Fran's own table, a convention with a named source. Prices, stock, the store's
draw order and the underprepared-dialog floor are all game data, and if any of
them stops parsing the module degrades silently to an empty basket rather than
raising, so these are the tests that say it did not.

The load-bearing one is `test_the_store_order_reproduces_two_hand_measured_slots`:
two coordinates a human clicked, reproduced by a file nobody derived them from.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest

from data import provisions as P

from data.paths import GAME_DIR

pytestmark = pytest.mark.skipif(not GAME_DIR.exists(),
                                reason="needs the game install")

LENGTHS = (1, 2, 3)


def test_prices_are_parsed_from_the_shipped_item_file():
    unit = P.prices()
    # Food ships with an EMPTY id and `type "provision"`; every other row
    # carries its own id. If `_item_id`'s special case went, food would vanish
    # from the table and the basket would price at zero.
    assert unit["food"] == 75
    assert unit["torch"] == 75
    assert unit["shovel"] == 250
    assert unit["skeleton_key"] == 200
    # Firewood and dog treats are granted, never sold: a zero price is dropped.
    assert all(v > 0 for v in unit.values())


def test_the_store_order_reproduces_two_hand_measured_slots():
    """Food at 0 and torch at 8 -- `bridge/layout.py`'s two clicked constants.

    Nine items into a seven-column grid is what puts the torch on the second
    row, and the shipped list is what says nine. Neither constant was derived
    from this file.
    """
    order = P.store_order(1)
    assert order[0] == "food"
    assert order.index("torch") == 8
    assert len(order) == 9


def test_every_length_draws_the_same_items_in_the_same_order():
    """`store_order` is indexed by length because the file is; the claim that
    a caller may use any length's is what this pins."""
    orders = {P.store_order(n) for n in range(6)}
    orders.discard(())
    assert len(orders) == 1


def test_the_food_floor_is_the_games_own_number():
    got = [P.minimum_food(n) for n in range(6)]
    assert got == [0, 8, 8, 12, 12, 12]


def test_the_bridges_flat_eight_food_was_short_for_a_medium_quest():
    """Why `minimum_food` is read rather than hardcoded: the old constant was
    right for a short quest and four under for a medium one."""
    assert P.basket("crypts", 1)["food"] >= 8
    assert P.basket("crypts", 2)["food"] >= 12


def test_the_ruins_short_recommended_basket_is_3550_gold():
    want = P.basket("crypts", 1, "recommended")
    assert P.cost(want) == 3550
    assert P.cost(P.basket("crypts", 1, "minimum")) == 2400


def test_a_basket_never_asks_for_more_than_the_shop_stocks():
    for zone in P.ZONES:
        for length in LENGTHS:
            stock = P.store_stock(length)
            for item, qty in P.basket(zone, length).items():
                assert qty <= stock[item], (zone, length, item)


def test_the_recommended_end_is_never_below_the_minimum():
    for zone in P.ZONES:
        for length in LENGTHS:
            low = P.basket(zone, length, "minimum")
            high = P.basket(zone, length, "recommended")
            for item, qty in low.items():
                assert high.get(item, 0) >= qty, (zone, length, item)


def test_an_unknown_zone_returns_nothing_rather_than_guessing():
    """The courtyard and the Darkest Dungeon have no row in PLAYBOOK 1.5, and
    inventing one would be a convention wearing game data's clothes."""
    assert P.basket("courtyard", 1) == {}
    assert P.basket("darkestdungeon", 1) == {}
    assert P.basket("crypts", 9) == {}


def test_afford_trims_to_the_wallet_and_keeps_the_essentials_longest():
    full = P.basket("crypts", 1)
    for gold in (0, 500, 1200, 2400, 3550, 9999):
        got = P.afford(full, gold)
        assert P.cost(got) <= gold
        # Nothing is invented on the way down.
        assert all(got[k] <= full[k] for k in got)
    # Shovels go before torches: `CUT_ORDER` is a policy and this is it.
    tight = P.afford(full, 1500)
    assert "shovel" not in tight
    assert tight.get("torch", 0) > 0


def test_cut_order_covers_every_item_the_guide_can_ask_for():
    """An item missing from `CUT_ORDER` cannot be trimmed, so `afford` would
    loop over the others and return a basket that is still unaffordable."""
    named = {item for zone in P.GUIDE.values()
             for band in zone.values() for item in band}
    assert named <= set(P.CUT_ORDER)


def test_the_band_map_agrees_with_the_one_bridge_already_had():
    from bridge.rollout import QUEST_LENGTH_TO_BAND

    assert P.LENGTH_TO_BAND == QUEST_LENGTH_TO_BAND


def test_the_guide_covers_all_four_simulable_zones_and_three_bands():
    assert set(P.GUIDE) == set(P.ZONES)
    for zone, table in P.GUIDE.items():
        assert set(table) == {"short", "medium", "long"}, zone
        for band, items in table.items():
            for item, pair in items.items():
                assert len(pair) == 2 and pair[0] <= pair[1], (zone, band, item)
                # Priced from the shipped file, so a typo'd item id is caught
                # here rather than by a basket that silently costs less.
                assert item in P.prices(), (zone, band, item)


# -- what the party is HANDED, before a coin is spent ----------------------

def test_the_free_per_class_provisions_are_the_eight_the_game_ships():
    """`raid_starting_hero_class_item_lists`, parsed rather than typed in --
    and it matches darkestdungeon.wiki.gg's list exactly."""
    assert P.granted(["houndmaster"], 1)["dog_treats"] == 2
    assert P.granted(["plague_doctor"], 1)["antivenom"] == 1
    assert P.granted(["grave_robber"], 1)["shovel"] == 1
    assert P.granted(["arbalest"], 1)["bandage"] == 1
    assert P.granted(["crusader"], 1)["holy_water"] == 1
    assert P.granted(["leper"], 1)["medicinal_herbs"] == 1
    assert P.granted(["jester"], 1)["medicinal_herbs"] == 1
    assert P.granted(["antiquarian"], 1)["skeleton_key"] == 1
    # A class with no entry brings nothing but the length's firewood.
    assert "dog_treats" not in P.granted(["vestal"], 1)


def test_the_grant_is_PER_HERO_so_duplicates_stack():
    """Two Houndmasters bring four Dog Treats. Passing a set instead of a
    sequence would silently undercount exactly the party that benefits most,
    which is why `granted` takes duplicates."""
    assert P.granted(["houndmaster", "houndmaster"], 1)["dog_treats"] == 4
    assert P.granted(["leper", "jester"], 1)["medicinal_herbs"] == 2


def test_firewood_by_length_is_0_1_2_and_comes_from_the_same_file():
    """The wiki's Short 0 / Medium 1 / Long 2, and the table
    `data/camping_loader.FIREWOOD_PER_BAND` carries -- read here off
    `raid_starting_length_inventory_item_lists` instead."""
    assert P.granted((), 1).get("firewood", 0) == 0
    assert P.granted((), 2)["firewood"] == 1
    assert P.granted((), 3)["firewood"] == 2
    from data.camping_loader import FIREWOOD_PER_BAND
    assert [FIREWOOD_PER_BAND[b] for b in ("short", "medium", "long")] == [0, 1, 2]


def test_a_party_reduces_the_basket_by_exactly_what_it_is_given():
    """The GUIDE says what to CARRY; the grant is part of what is carried. A
    Crusader's free holy water is 150 gold the plan should not spend."""
    plain = P.basket("crypts", 1)
    with_cru = P.basket("crypts", 1, party=["crusader"])
    assert with_cru["holy_water"] == plain["holy_water"] - 1
    assert P.cost(plain) - P.cost(with_cru) == P.prices()["holy_water"]

    # An item the party is given MORE of than the guide asks for drops out
    # entirely rather than going negative.
    herbs = P.basket("crypts", 1, party=["leper", "jester"])
    assert "medicinal_herbs" not in herbs


def test_no_party_is_byte_identical_to_before_the_grant_existed():
    """Every existing caller passes no party, so the default must not move a
    single number -- the same discipline the five training knobs follow."""
    for zone in P.ZONES:
        for length in (1, 2, 3):
            for level in ("minimum", "recommended"):
                assert (P.basket(zone, length, level)
                        == P.basket(zone, length, level, party=()))


def test_the_food_floor_still_applies_to_what_is_CARRIED():
    """`minimum_food` is DD's own dialog and it counts the pack, not the
    receipt. No class grants food, so the two readings agree today -- pinned
    because a DLC class that did would otherwise silently trip the dialog."""
    assert "food" not in P.granted(
        ["crusader", "leper", "jester", "houndmaster", "arbalest",
         "antiquarian", "grave_robber", "plague_doctor"], 2)
    assert P.basket("crypts", 2, party=["crusader"])["food"] >= P.minimum_food(2)
