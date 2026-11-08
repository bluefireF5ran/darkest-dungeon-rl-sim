"""`bridge/fights_ahead.py` -- the real fights ahead, read from the map.

A battle tile's `mash_index` / `mash_type` name its group in the zone's mash
file. Verified 10/10 against captured raids on 2026-09-25.
"""
import glob
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from bridge.fights_ahead import fights_ahead, mash_table  # noqa: E402
from data.paths import GAME_DIR  # noqa: E402

MAPS = sorted(glob.glob(str(Path(__file__).resolve().parents[2]
                            / "archive/traces/raidsnaps2/*_persist.map.json")))
needs = pytest.mark.skipif(not MAPS or not Path(GAME_DIR).exists(),
                           reason="no captured raid or no game install")


@needs
def test_the_first_fight_of_a_captured_raid_is_the_group_it_really_met():
    """raidsnaps2 opens in the Weald; its first fight (coFG) was two Webbers
    and a Spitter -- the group the map's first mash_index names."""
    groups = fights_ahead(MAPS[0], MAPS[0].replace("map.json", "raid.json"))
    assert groups, "no fights read"
    assert sorted(groups[0]) == ["spider_spitter_A", "spider_webber_A",
                                 "spider_webber_A"]


@needs
def test_a_mash_table_has_hall_and_room_entries_in_file_order():
    table = mash_table("crypts", 1, str(GAME_DIR))
    assert table.get("hall") and table.get("room")
    assert table["hall"][0] == ["skeleton_common_A"] * 3


def test_a_missing_map_reads_as_no_fights(tmp_path):
    assert fights_ahead(tmp_path / "nope.json", tmp_path / "nope2.json") == []


@needs
def test_the_simulator_fights_the_group_it_is_given():
    from env.expedition_sequence_env import ExpeditionSequenceEnv
    env = ExpeditionSequenceEnv()
    group = ["spider_webber_A", "spider_webber_A", "spider_spitter_A"]
    env.reset(seed=0, options={"zone_id": "weald", "tier": 1, "num_combats": 3,
                               "enemy_group_script": [group]})
    ids = sorted(e.definition_id for e in env.unwrapped.engine.enemies)
    assert ids == sorted(group)
