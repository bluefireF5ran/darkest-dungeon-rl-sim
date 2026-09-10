"""Tests for the enemy encounter comp system — real hall/room/boss/stall/
named groups parsed from dungeons/<zone>/<zone>.<tier>.mash.darkest, the
game's own weighted-random encounter tables (not hand-curated), analogous
to the hero comp system (COMPS/ CSV + tests/fixtures/*.json) but sourced
live from game data instead. See ASSUMPTIONS.md for what's excluded
(.conditional. files, DLC zones) and why.
"""

import sys
import json
import collections
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests._fixtures import get_store

from engine import SeededRNG, CombatEngine
from data.csv_loader import csv_root
from data.enemy_comp_loader import EnemyCompLoader, ZONE_NAMES


from data.paths import GAME_DIR
from data.paths import CSV_DIR
store = get_store()


def test_all_5_zones_present_with_real_counts():
    """Matches direct grep counts against the source mash.darkest files
    (hall/room/boss/stall/named record lines, excluding .conditional.)."""
    expected = {"crypts": 282, "weald": 259, "warrens": 287, "cove": 234, "darkestdungeon": 43}
    by_zone = collections.Counter(e.zone_id for e in store.enemy_comps)
    assert by_zone == expected, by_zone
    print(f"  PASS test_all_5_zones_present_with_real_counts ({dict(by_zone)})")


def test_every_enemy_id_resolves():
    """The core validation this whole system exists for: every enemy_id in
    every extracted comp must resolve in store.enemies. Only failed once
    during development (carrion_eater_large_A, a stale internal name in the
    game's own warrens.2/4 mash files — fixed via ENEMY_ID_ALIASES)."""
    missing = set()
    for e in store.enemy_comps:
        for eid in e.enemy_ids:
            if eid not in store.enemies:
                missing.add(eid)
    assert not missing, missing
    print(f"  PASS test_every_enemy_id_resolves ({len(store.enemy_comps)} comps checked)")


def test_carrion_eater_large_alias_resolves():
    """Regression test for the one real data quirk found: warrens.2/4's
    mash files reference 'carrion_eater_large_A', but the monster's actual
    asset folder (and therefore its real id) is carrion_eater_big_A."""
    warrens_hall_tier2 = [e for e in store.enemy_comps
                           if e.zone_id == "warrens" and e.tier == 2 and e.category == "hall"]
    aliased = [e for e in warrens_hall_tier2 if "carrion_eater_big_A" in e.enemy_ids]
    assert aliased, "expected at least one warrens tier-2 hall comp with the aliased id"
    assert all("carrion_eater_large_A" not in e.enemy_ids for e in warrens_hall_tier2)
    print(f"  PASS test_carrion_eater_large_alias_resolves ({len(aliased)} comps)")


def test_cove_tier1_boss_entries_match_source_file():
    """Direct spot-check against cove.1.mash.darkest's literal content:
    two boss entries, siren_A and drowned_captain_A, each solo."""
    cove_t1_bosses = [e for e in store.enemy_comps
                       if e.zone_id == "cove" and e.tier == 1 and e.category == "boss"]
    boss_sets = sorted(tuple(e.enemy_ids) for e in cove_t1_bosses)
    assert boss_sets == [("drowned_captain_A",), ("siren_A",)], boss_sets
    print("  PASS test_cove_tier1_boss_entries_match_source_file")


def test_darkestdungeon_zone_only_has_tier_6():
    """The Darkest Dungeon zone has no tiers 1-5 — its only mash file is
    darkestdungeon.6.mash.darkest, entirely scripted per the 4 story quests
    (all 'named' entries plus a couple of boss/hall/room/stall fallbacks)."""
    dd_entries = [e for e in store.enemy_comps if e.zone_id == "darkestdungeon"]
    assert dd_entries and all(e.tier == 6 for e in dd_entries)
    named_count = sum(1 for e in dd_entries if e.category == "named")
    assert named_count >= 30, named_count  # most entries are scripted quest encounters
    print(f"  PASS test_darkestdungeon_zone_only_has_tier_6 ({len(dd_entries)} entries, {named_count} named)")


def test_zone_names_map_to_real_display_names():
    assert ZONE_NAMES == {
        "crypts": "Ruins", "weald": "Weald", "warrens": "Warrens",
        "cove": "Cove", "darkestdungeon": "Darkest Dungeon",
    }
    print("  PASS test_zone_names_map_to_real_display_names")


def test_to_enemy_defs_resolves_real_comp_in_order():
    """A crypts tier-1 hall comp (3x skeleton_common_A, verified against
    crypts.1.mash.darkest's first line) should resolve to 3 EnemyDefinitions
    in the same order, ready for CombatEngine.reset()."""
    comp = next(e for e in store.enemy_comps
                if e.zone_id == "crypts" and e.tier == 1 and e.category == "hall"
                and e.enemy_ids == ["skeleton_common_A"] * 3)
    defs = EnemyCompLoader.to_enemy_defs(store, comp)
    assert len(defs) == 3
    assert all(d.id == "skeleton_common_A" for d in defs)
    print("  PASS test_to_enemy_defs_resolves_real_comp_in_order")


def test_full_combat_with_real_extracted_comp():
    """End-to-end: take a real weald tier-1 room comp and run a full
    combat against a 4-hero party, confirm it resolves cleanly."""
    room_comps = [e for e in store.enemy_comps
                  if e.zone_id == "weald" and e.tier == 1 and e.category == "room"]
    comp = max(room_comps, key=lambda e: len(e.enemy_ids))
    enemy_defs = EnemyCompLoader.to_enemy_defs(store, comp)
    assert enemy_defs

    crusader = store.heroes["crusader"]
    vestal = store.heroes["vestal"]
    hero_defs = [crusader, vestal, crusader, vestal][:4]

    engine = CombatEngine(store, SeededRNG(7))
    engine.reset(hero_defs=hero_defs, enemy_defs=enemy_defs)
    steps = 0
    while not engine.done and steps < 300:
        steps += 1
        actor = engine.get_current_actor_id()
        if actor is not None:
            acts = engine.get_valid_actions(actor)
            if acts:
                engine.submit_action(acts[0])
        engine.step()
    assert engine.done, f"combat did not finish in {steps} steps"
    assert engine.winner in ("heroes", "enemies")
    print(f"  PASS test_full_combat_with_real_extracted_comp "
          f"(zone={comp.zone_name}, enemies={comp.enemy_ids}, steps={steps}, winner={engine.winner})")


def test_size2_enemy_combined_with_others_gets_non_overlapping_positions():
    """Real bug found while building this system: CombatEngine.reset()
    assigned enemy positions via naive enumerate() (position=i), not
    accounting for a size-2 enemy occupying 2 ranks. ~25% of real
    hall/room/boss/stall comps combine a size-2 enemy (e.g.
    skeleton_captain_A, brigand_blood_A) with others, so the next enemy in
    the list would silently land on top of the size-2 enemy's second
    occupied position. Fixed via a running slot cursor advanced by each
    enemy's own .size instead of the list index."""
    comp = next(e for e in store.enemy_comps
                if e.zone_id == "crypts" and e.tier == 2 and e.category == "room"
                and e.enemy_ids == ["skeleton_captain_A", "skeleton_spear_A", "skeleton_arbalist_A"])
    assert store.enemies["skeleton_captain_A"].size == 2

    crusader = store.heroes["crusader"]
    engine = CombatEngine(store, SeededRNG(1))
    engine.reset(hero_defs=[crusader, crusader, crusader, crusader],
                 enemy_defs=EnemyCompLoader.to_enemy_defs(store, comp))

    occupied = []
    for e in engine.enemies:
        occupied.extend(e.occupied_positions)
    assert occupied == sorted(occupied) and len(occupied) == len(set(occupied)), \
        f"expected 4 distinct, non-overlapping positions, got {occupied}"
    assert engine.enemies[0].occupied_positions == [0, 1]  # size-2 captain up front
    assert engine.enemies[1].position == 2
    assert engine.enemies[2].position == 3
    print(f"  PASS test_size2_enemy_combined_with_others_gets_non_overlapping_positions ({occupied})")


def test_generated_json_battery_matches_live_data():
    """ENEMY_COMPS/<zone>.json is a generated snapshot (data/generate_enemy_comps.py)
    — cross-check its comp counts against the live loader to catch it going
    stale relative to the game install."""
    zone_files = {
        "crypts": "ruins.json", "weald": "weald.json", "warrens": "warrens.json",
        "cove": "cove.json", "darkestdungeon": "darkest_dungeon.json",
    }
    live_counts = collections.Counter(e.zone_id for e in store.enemy_comps)
    for zone_id, filename in zone_files.items():
        path = csv_root(CSV_DIR) / "ENEMY_COMPS" / filename
        assert path.exists(), path
        with open(path, encoding="utf-8") as f:
            payload = json.load(f)
        assert payload["zoneId"] == zone_id
        assert len(payload["comps"]) == live_counts[zone_id], \
            (zone_id, len(payload["comps"]), live_counts[zone_id])
    print("  PASS test_generated_json_battery_matches_live_data")


if __name__ == "__main__":
    print("=== Enemy Comp Tests ===\n")
    for name, func in sorted(globals().items()):
        if name.startswith("test_"):
            try:
                func()
                print()
            except Exception as e:
                import traceback
                print(f"  FAIL {name}: {e}")
                traceback.print_exc()
                print()
    print("Done.")
