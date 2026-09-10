"""What a hero is worth, and the three ways the old valuation got it wrong.

Fran's spec, 2026-09-02: "+1000 per other skill unlocked +X per level of each
skill +Y per level of armor +Z per level of weapon, +N per positive quirk
locked, +All the value of the inventory". Every number is shipped; what these
pin is that they are read rather than assumed, and that the free slots, the
free ranks and the quirk SIDE are all handled.

The correction is not cosmetic: on Fran's Week 22 roster the old code priced
30 heroes at 750 gold and this prices them at 23,750.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest

from data import hero_value as HV
from data.town_costs import load_hero_costs

from data.paths import GAME_DIR

pytestmark = pytest.mark.skipif(not GAME_DIR.exists(),
                                reason="needs the game install")

GD = str(GAME_DIR)

RESTRICTED = ("crusader", "vestal", "highwayman", "leper", "antiquarian",
              "occultist", "jester", "man_at_arms", "plague_doctor", "hellion",
              "arbalest", "bounty_hunter", "grave_robber", "houndmaster",
              "musketeer", "flagellant", "shieldbreaker", "runaway")
UNRESTRICTED = ("abomination", "duelist")


class _Hero:
    def __init__(self, hero_class, guid):
        self.hero_class, self.guid = hero_class, guid


@pytest.fixture
def fake_unlocks(monkeypatch):
    """Drive the valuation off a stated `{skill: rank}` instead of a save."""
    def install(mapping, gear=None):
        monkeypatch.setattr(HV, "unlocked", lambda _p, _h, _g=None: dict(mapping))
        monkeypatch.setattr(HV, "gear_ranks", lambda _p, _h, _g=None: dict(gear or {}))
    return install


# -- the free slots -------------------------------------------------------

def test_the_free_slot_count_is_derived_and_reproduces_the_known_pair():
    """`number_of_selected_combat_skills_max` is 4 on eighteen classes and 7
    on exactly the two with `can_select_combat_skills false`. Reproducing that
    split from a different key in the same file is the check that the
    derivation reads the right thing -- and it is what prices a modded stance
    class correctly with no entry here."""
    for cls in RESTRICTED:
        assert HV.skill_slots(cls, GD) == 4, cls
    for cls in UNRESTRICTED:
        assert HV.skill_slots(cls, GD) == 7, cls


def test_an_unknown_class_falls_back_rather_than_raising():
    assert HV.skill_slots("sibyl_ms", GD) == HV.DEFAULT_SKILL_SLOTS


# -- the skills -----------------------------------------------------------

def test_the_four_skills_a_recruit_arrives_with_are_free(fake_unlocks):
    """The old code charged 1000 each for them whenever gear had been bought,
    inflating a fully-equipped hero by up to 4000 gold."""
    four = list(load_hero_costs(GD, "crusader").skills)[:4]
    fake_unlocks({s: 1 for s in four})
    value = HV.hero_value(None, _Hero("crusader", 1), None, GD)
    assert value.unlocks == 0
    assert value.total == 0, value.line()


def test_a_fifth_skill_costs_one_unlock(fake_unlocks):
    five = list(load_hero_costs(GD, "crusader").skills)[:5]
    fake_unlocks({s: 1 for s in five})
    value = HV.hero_value(None, _Hero("crusader", 1), None, GD)
    assert value.unlocks == 1
    assert value.unlock_gold == 1000
    assert value.total == 1000


def test_an_unrestricted_class_owning_all_seven_has_bought_nothing(fake_unlocks):
    """Seven free slots, seven owned. Nothing here tests for the Abomination
    by name -- it falls out of the shipped max, which is why a modded stance
    class gets the same treatment."""
    seven = list(load_hero_costs(GD, "abomination").skills)
    assert len(seven) == 7
    fake_unlocks({s: 1 for s in seven})
    value = HV.hero_value(None, _Hero("abomination", 31), None, GD)
    assert value.unlocks == 0 and value.total == 0


def test_ranks_above_the_unlock_are_charged_on_every_skill(fake_unlocks):
    """Free to OWN is not free to UPGRADE. The ladder is 250/750/1250/2500
    above the 1000 unlock, so one of the four starting skills at rank 3 is
    250 + 750."""
    four = list(load_hero_costs(GD, "crusader").skills)[:4]
    fake_unlocks({four[0]: 3, four[1]: 1, four[2]: 1, four[3]: 1})
    value = HV.hero_value(None, _Hero("crusader", 1), None, GD)
    assert value.unlocks == 0
    assert value.skill_rank_gold == 1000
    assert value.total == 1000


def test_the_unlock_price_is_read_from_the_tree_not_typed_in():
    """1000 in all twenty shipped trees and all four campaign modes. If a mod
    changed it, the valuation would follow rather than quoting a memory."""
    for cls in RESTRICTED + UNRESTRICTED:
        ranks = next(iter(load_hero_costs(GD, cls).skills.values()))
        assert ranks[0] == 1000, cls


# -- gear -----------------------------------------------------------------

def test_gear_is_priced_from_the_real_ranks_not_from_the_weapon(fake_unlocks):
    """The old code assumed every skill sat at the weapon's rank -- a stated
    convention, made when nothing could read the real ranks."""
    costs = load_hero_costs(GD, "crusader")
    four = list(costs.skills)[:4]
    fake_unlocks({s: 1 for s in four}, gear={"weapon": 2, "armour": 1})
    value = HV.hero_value(None, _Hero("crusader", 1), None, GD)
    assert value.weapon_gold == sum(costs.weapon[:2])
    assert value.armour_gold == costs.armour[0]
    assert value.skill_rank_gold == 0, "rank-2 gear must not upgrade skills"


# -- quirks ---------------------------------------------------------------

def test_the_quirk_lock_ladder_is_the_shipped_one():
    prices = HV.quirk_lock_prices(GD)
    assert prices[:3] == (7500, 6750, 6000)
    assert HV.quirk_lock_gold(2, 0, GD) == 15000
    assert HV.quirk_lock_gold(0, 0, GD) == 0
    # Tier 0 is the dearest, which is the conservative default for a retreat.
    assert HV.quirk_lock_gold(1, 0, GD) >= HV.quirk_lock_gold(1, 2, GD)


def test_only_a_POSITIVE_locked_quirk_is_gold_somebody_spent():
    """The game locks diseases and permanent negatives by itself. The single
    `is_locked True` in either live campaign is `torn_rotator_cuff` on a
    Vestal -- a negative -- so counting every locked quirk would price a
    diseased hero as though they carried a bought positive."""
    from tests._fixtures import get_store

    store = get_store()
    assert HV.positive_locked(("torn_rotator_cuff",), store) == ()
    positive = next(q for q, d in store.quirks.items()
                    if getattr(d, "is_positive", False))
    assert HV.positive_locked((positive,), store) == (positive,)
    # An unknown id is excluded rather than assumed positive.
    assert HV.positive_locked(("__no_such_quirk__",), store) == ()


def test_a_missing_roster_file_reads_as_nothing_locked(tmp_path):
    assert HV.locked_quirks(tmp_path / "persist.roster.json") == {}


# -- the inventory --------------------------------------------------------

class _Item:
    def __init__(self, type_, id_="", amount=1):
        self.type, self.id, self.amount = type_, id_, amount


def test_the_pack_is_priced_only_where_the_game_prices_it():
    got = HV.inventory_value([_Item("gold", amount=1500),
                              _Item("supply", "torch", 8),
                              _Item("supply", "shovel", 2),
                              _Item("trinket", "ancestors_map"),
                              _Item("heirloom", "bust", 3)], GD)
    assert got.gold == 1500
    assert got.provisions_gold == 8 * 75 + 2 * 250
    assert got.total == 1500 + 600 + 500
    # Counted, never priced: DD sells neither, so any number would be invented.
    assert got.trinkets == 1 and got.heirlooms == 3
    assert "unpriced" in got.line()


# -- the class this install cannot price ----------------------------------

def test_an_unpriceable_class_says_so_instead_of_reading_zero():
    value = HV.hero_value(None, _Hero("sibyl_ms", 40), None, GD)
    assert value.priceable is False
    assert value.total == 0
    assert "not priceable" in value.line()
