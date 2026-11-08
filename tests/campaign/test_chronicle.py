"""Tests for campaign/chronicle.py -- the story is the logs, told."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from campaign.chronicle import chronicle, pick_best
from campaign.week import WeekLog


def _raid(week, heroes, success, fallen=(), plot_id="", level_ups=()):
    return WeekLog(week=week, success=success, plot_id=plot_id, fallen=tuple(fallen),
                   deaths=tuple(c for _g, c, _l in fallen), party=tuple(c for _g, c, _l in heroes),
                   level_ups=tuple(level_ups),
                   raid={"dungeon": "crypts", "difficulty": 1, "length": 2,
                         "heroes": [{"guid": g, "hero_class": c, "resolve_level": lv}
                                    for g, c, lv in heroes]})


PARTY = [(1, "crusader", 2), (2, "vestal", 2), (3, "highwayman", 2), (4, "plague_doctor", 2)]


def test_deaths_bosses_and_milestones_are_told():
    logs = [_raid(1, PARTY, True),
            _raid(2, PARTY, True, plot_id="plot_kill_necromancer_1", level_ups=("vestal->3",)),
            _raid(3, PARTY, False, fallen=[(2, "vestal", 3)]),
            WeekLog(week=4)]
    text = chronicle(logs, seed=9)
    assert "| first Veteran | week 2 |" in text
    assert "| bosses killed | 1 |" in text
    assert "| heroes dead | 1 (0 whole parties) |" in text
    assert "the Necromancer falls" in text
    assert "The Vestal #2 dies at resolve 3, after 2 raids survived" in text
    assert "An Apprentice medium raid into the Ruins" in text
    assert "Week 4" not in text                  # a quiet week is not a line


def test_a_wipe_is_one_whole_party():
    text = chronicle([_raid(1, PARTY, False, fallen=PARTY)], seed=0)
    assert "| heroes dead | 4 (1 whole party) |" in text
    assert "**the whole party is lost**" in text


def test_the_best_campaign_is_fewest_dead_then_most_plots():
    rows = [{"seed": 0, "dead": 9, "plots": 9, "champion_week": 30},
            {"seed": 1, "dead": 5, "plots": 2, "champion_week": None},
            {"seed": 2, "dead": 5, "plots": 4, "champion_week": 40}]
    assert pick_best(rows)["seed"] == 2


def test_the_sanitarium_is_told_and_counted():
    w = WeekLog(week=5, sanitarium=(("cure", "the_black_plague", 550), ("remove", "fragile", 1350, 7, "leper")))
    text = chronicle([w], seed=0)
    assert "| Sanitarium visits | 2 |" in text
    assert "**Week 5.** No raid. The Sanitarium cures the black plague and removes fragile from the Leper #7." in text


if __name__ == "__main__":
    for t in (test_deaths_bosses_and_milestones_are_told, test_a_wipe_is_one_whole_party,
              test_the_best_campaign_is_fewest_dead_then_most_plots,
              test_the_sanitarium_is_told_and_counted):
        t()
        print("  ok ", t.__name__)
