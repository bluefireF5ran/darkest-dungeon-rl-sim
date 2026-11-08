"""Tests for campaign/board.py -- the generated quest board.

These pin the properties the three boards on disk show (2026-10-10), not the
exact draw: the per-dungeon cap, long quests only from dungeon level 3, the
Courtyard behind its plot quest, and rewards read from the right cell.
"""

import random
import sys
from collections import Counter
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from campaign.board import (generate_board, offered_difficulties, open_dungeons,
                            plot_board)
from data.quest_gen_loader import load, load_plot_quests

T = load()
LATE = dict(visit_index=30, quests_finished=40,
            dungeon_xp={"crypts": 34, "warrens": 25, "cove": 13, "weald": 8, "courtyard": 8},
            roster_levels=[1, 3, 5, 6], completed_plots={"plot_crimson_court_0"})


def test_no_quests_before_the_tutorial_is_finished():
    assert open_dungeons(T, 0, set()) == []
    assert generate_board(random.Random(0), T, visit_index=0, quests_finished=0,
                          dungeon_xp={}, roster_levels=[0]) == []


def test_the_first_board_is_short_crypts_only():
    b = generate_board(random.Random(0), T, visit_index=1, quests_finished=1,
                       dungeon_xp={}, roster_levels=[0, 0, 0, 0])
    assert b and {q.quest.dungeon for q in b} == {"crypts"}
    assert {q.quest.length for q in b} == {1} and {q.quest.difficulty for q in b} == {1}


def test_late_board_respects_the_caps():
    for seed in range(50):
        b = generate_board(random.Random(seed), T, **LATE)
        per = Counter(q.quest.dungeon for q in b)
        assert max(per.values()) <= T.max_per_dungeon
        assert per.get("courtyard", 0) <= 1
        assert len(b) <= T.visit_count(LATE["visit_index"])


def test_long_quests_only_from_level_three():
    # weald at 8 XP is level 2: never a length-3 weald quest.
    for seed in range(50):
        for q in generate_board(random.Random(seed), T, **LATE):
            if q.quest.dungeon == "weald":
                assert q.quest.length < 3


def test_courtyard_needs_its_plot_quest():
    late = dict(LATE, completed_plots=set())
    for seed in range(20):
        assert all(q.quest.dungeon != "courtyard"
                   for q in generate_board(random.Random(seed), T, **late))


def test_rewards_come_from_the_tables():
    for q in generate_board(random.Random(1), T, **LATE):
        d, n = q.quest.difficulty, q.quest.length
        assert q.quest.gold_reward == T.gold_reward(d, n) > 0
        assert q.resolve_xp == T.xp_reward(d, n) > 0
        assert q.heirloom_amount == T.heirloom_amount(q.heirloom, d, n) > 0
        assert q.trinket_rarity


def test_difficulties_follow_the_roster():
    assert offered_difficulties(T, [0, 1]) == [1]
    assert offered_difficulties(T, [2]) == [1, 3]
    assert offered_difficulties(T, [6]) == [5]
    assert offered_difficulties(T, []) == [1]


# The three saves on disk, 2026-10-10: dungeon XP, completed plots, and the
# plot quests the real board offered. Copied rather than read, because the
# saves move every time Fran plays.
SAVES = {
    "profile_0": (
        {"cove": 20, "weald": 12, "warrens": 26, "crypts": 34},
        {"plot_tutorial_crypts", "plot_kill_swine_prince_1"},
        {"plot_kill_formless_flesh_1", "plot_kill_hag_1", "plot_kill_necromancer_1",
         "plot_kill_siren_1", "plot_darkest_dungeon_1", "plot_crimson_court_0"}),
    "profile_1": (
        {"cove": 5, "weald": 2, "warrens": 4, "crypts": 7},
        {"plot_tutorial_crypts"},
        {"plot_kill_necromancer_1", "plot_darkest_dungeon_1", "plot_crimson_court_0"}),
    "profile_8": (
        {"cove": 13, "weald": 8, "warrens": 25, "crypts": 34, "courtyard": 8},
        {"plot_color_of_madness_0", "plot_color_of_madness_1", "plot_crimson_court_0",
         "plot_crimson_court_1", "plot_kill_drowned_crew_1", "plot_kill_formless_flesh_1",
         "plot_kill_formless_flesh_2", "plot_kill_hag_1", "plot_kill_necromancer_1",
         "plot_kill_necromancer_2", "plot_kill_prophet_1", "plot_kill_prophet_2",
         "plot_kill_siren_1", "plot_kill_swine_prince_1", "plot_kill_swine_prince_2",
         "plot_tutorial_crypts"},
        {"plot_kill_necromancer_3", "plot_darkest_dungeon_1", "plot_crimson_court_2"}),
}


def test_plot_board_matches_every_save():
    plots = load_plot_quests()
    for name, (xp, done, offered) in SAVES.items():
        got = {p.plot_id for p in plot_board(plots, T, dungeon_xp=xp, completed_plots=done)}
        assert got == offered, (name, sorted(got ^ offered))


def test_the_darkest_dungeon_chain_advances_one_link_at_a_time():
    plots = load_plot_quests()
    done = {"plot_tutorial_crypts"}
    for n in (1, 2, 3, 4):
        got = {p.plot_id for p in plot_board(plots, T, dungeon_xp={}, completed_plots=done)}
        assert f"plot_darkest_dungeon_{n}" in got
        assert not any(f"plot_darkest_dungeon_{m}" in got for m in range(n + 1, 5))
        done.add(f"plot_darkest_dungeon_{n}")
    heart = next(p for p in plots if p.plot_id == "plot_darkest_dungeon_4")
    assert heart.boss_monsters == ("ancestor_heart_D",) and not heart.can_retreat


if __name__ == "__main__":
    for name, fn in sorted(list(globals().items())):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  ok  {name}")
