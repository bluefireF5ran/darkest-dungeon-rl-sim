"""Tests for campaign/loot.py -- the raid's own expected take.

Pinned: the loot count the store now carries (the Necromancer draws twice),
that harder tiers pay more, and that a retreat pays for the part walked.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests._fixtures import get_store

from campaign.loot import battle_loot, curio_loot, raid_loot

S = get_store()


def test_the_store_reads_a_monsters_loot_count():
    assert (S.enemies["necromancer_A"].loot_code, S.enemies["necromancer_A"].loot_count) == ("T", 2)
    assert S.enemies["skeleton_arbalist_A"].loot_count == 1


def test_harder_tiers_pay_more():
    for dungeon in ("crypts", "weald", "warrens", "cove"):
        assert 0 < battle_loot(S, dungeon, 1).gold < battle_loot(S, dungeon, 5).gold
        assert 0 < curio_loot(dungeon, 1, 1).gold < curio_loot(dungeon, 5, 1).gold


def test_a_longer_map_holds_more_curios():
    assert curio_loot("weald", 1, 1).gold < curio_loot("weald", 1, 3).gold


def test_a_retreat_pays_for_the_part_walked():
    kw = dict(dungeon="crypts", difficulty=1, length=1, quest_type="explore")
    full = raid_loot(S, combats_won=4, combats_total=4, **kw)
    half = raid_loot(S, combats_won=2, combats_total=4, **kw)
    assert abs(half.gold - full.gold / 2) < 1e-6
    assert raid_loot(S, combats_won=0, combats_total=4, **kw).gold == 0


def test_the_basket_is_used_on_the_curios_it_was_bought_for():
    """With the zone's Short basket a map's curios pay well over bare-handed
    (keys, shovels, herbs where they beat bare hands), and an empty basket is
    exactly bare-handed."""
    from data import provisions
    kw = dict(dungeon="crypts", difficulty=1, length=1)
    bare = curio_loot(**kw)
    assert abs(curio_loot(**kw, supplies={}).gold - bare.gold) < 1e-6
    basket = provisions.basket("crypts", 1)
    assert curio_loot(**kw, supplies=basket).gold > bare.gold * 1.5


if __name__ == "__main__":
    for name, fn in sorted(list(globals().items())):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  ok  {name}")
