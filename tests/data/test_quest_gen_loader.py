"""Tests for data/quest_gen_loader.py -- the quest board's tables.

The reward cells are pinned to quests the game actually generated: profile_8's
board on 2026-10-10 (15 generated quests, every one matching on gold, resolve
XP and heirloom amount). If the [difficulty][length] indexing ever slips by
one, these go red rather than the campaign simulator quietly paying the
neighbouring cell.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from data.quest_gen_loader import load

T = load()


def test_reward_cells_match_real_generated_quests():
    # (difficulty, length, gold, xp, heirloom, amount) read off profile_8.
    seen = [
        (5, 2, 9000, 12, "deed", 9),       # warrens gather
        (1, 2, 4500, 3, "bust", 2),        # weald gather
        (3, 3, 11250, 8, "deed", 12),      # cove explore
        (5, 3, 15000, 16, "portrait", 9),  # warrens explore
        (3, 1, 4500, 4, "bust", 2),        # warrens cleanse
        (5, 1, 6000, 8, "portrait", 3),    # warrens explore
        (3, 2, 6750, 6, "deed", 6),        # crypts explore
    ]
    for d, length, gold, xp, kind, amount in seen:
        assert T.gold_reward(d, length) == gold, (d, length)
        assert T.xp_reward(d, length) == xp, (d, length)
        assert T.heirloom_amount(kind, d, length) == amount, (kind, d, length)


def test_dungeons_unlock_in_the_shipped_order():
    req = {g.dungeon: g.required_quests_finished for g in T.dungeons}
    assert req == {"crypts": 1, "weald": 3, "warrens": 4, "cove": 4, "courtyard": 0}
    court = next(g for g in T.dungeons if g.dungeon == "courtyard")
    assert court.required_plot_quest == "plot_crimson_court_0"


def test_per_visit_count_layers_both_dlcs():
    # Crimson Court replaces the base table AND adds its own modifier; Color
    # of Madness adds another. Base alone would read [2, 6, 8, ...].
    cc = [2, 6, 9, 10, 11, 12, 13, 14]
    cc_mod = [0, 0, 1, 1, 1, 1, 1, 1]
    com_mod = [1, 1, 2, 2, 3, 3, 3, 3]
    assert T.quests_per_visit == [a + b + c for a, b, c in zip(cc, cc_mod, com_mod)]


def test_long_quests_need_dungeon_level_three():
    for dungeon in ("crypts", "weald", "warrens", "cove"):
        for level in range(8):
            lengths = {o.length for o in T.options(dungeon, level)}
            assert (3 in lengths) == (level >= 3), (dungeon, level, lengths)


def test_trinket_weights_are_one_rarity_per_cell_in_the_base_game():
    assert T.trinket_rarity_weights("crypts", 1, 1) == {"common": 1.0}
    assert T.trinket_rarity_weights("crypts", 5, 3) == {"ancestral": 1.0}
    court = T.trinket_rarity_weights("courtyard", 5, 2)
    assert max(court, key=court.get) == "crimson_court"


def test_dungeon_level_thresholds():
    assert [T.dungeon_level(x) for x in (0, 1, 2, 9, 10, 34, 99)] == [0, 0, 1, 2, 3, 6, 7]


if __name__ == "__main__":
    for name, fn in sorted(list(globals().items())):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  ok  {name}")
