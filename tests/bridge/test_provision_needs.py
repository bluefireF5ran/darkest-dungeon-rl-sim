"""`bridge/provision_needs.py` -- provisions from the game's own data.

PLAYBOOK 7.7 (Fran, 2026-09-23): game averages rather than a community table,
erring over rather than under.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from bridge import provision_needs as PN  # noqa: E402
from data.paths import GAME_DIR  # noqa: E402

GD = str(GAME_DIR)
if not (Path(GD) / "scripts" / "map_generator.darkest").exists():
    pytest.skip("no game install", allow_module_level=True)


def test_every_zone_and_length_has_an_explore_budget():
    budgets = PN.map_budgets(GD)
    for zone in ("crypts", "warrens", "weald", "cove"):
        for size in ("short", "medium", "long"):
            b = budgets[(zone, size, "explore")]
            assert b["hallway_hunger"][1] >= b["hallway_hunger"][0] >= 0


def test_food_covers_the_most_hunger_checks_a_length_can_roll():
    """Top of the range, one food per hero: short maps roll up to 3 checks."""
    hi = PN.map_budgets(GD)[("cove", "short", "explore")]["hallway_hunger"][1]
    assert PN.derived_basket("cove", "short", "explore", GD)["food"] >= 4 * hi


def test_shovels_cover_the_most_obstacles_a_length_can_roll():
    hi = PN.map_budgets(GD)[("weald", "long", "explore")]["hallway_obstacle"][1]
    assert PN.derived_basket("weald", "long", "explore", GD)["shovel"] >= hi


def test_an_unknown_quest_type_falls_back_to_explore():
    assert (PN.derived_basket("crypts", "short", "no_such_type", GD)
            == PN.derived_basket("crypts", "short", "explore", GD))
