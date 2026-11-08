"""`data/map_generator.py`: map budgets and the hunger rate derived from them."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from data.map_generator import hunger_per_fight, map_budgets  # noqa: E402
from data.paths import GAME_DIR  # noqa: E402

if not (Path(GAME_DIR) / "scripts" / "map_generator.darkest").exists():
    pytest.skip("no game install", allow_module_level=True)


def test_every_base_zone_ships_an_explore_budget_per_length():
    budgets = map_budgets(str(GAME_DIR))
    for zone in ("crypts", "warrens", "weald", "cove"):
        for size in ("short", "medium", "long"):
            assert (zone, size, "explore") in budgets


def test_hunger_per_fight_is_well_under_the_old_declared_0_6():
    """The simulator's `HUNGER_CHECK_CHANCE = 0.6` was a proxy whose own
    falsifier was a real count. The shipped maps answer it."""
    rates = [hunger_per_fight(s, str(GAME_DIR)) for s in ("short", "medium", "long")]
    assert all(0.1 < r < 0.5 for r in rates), rates


def test_an_unreadable_game_gives_no_rate(tmp_path):
    assert hunger_per_fight("short", str(tmp_path)) is None
