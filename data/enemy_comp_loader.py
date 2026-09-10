from __future__ import annotations
from pathlib import Path
from .types import DataStore, EnemyCompEntry, EnemyDefinition
from .darkest_parser import DarkestParser

# Internal zone id -> real in-game display name. Verified against
# dungeons/<id>/<id>.dungeon.json (id_index 0-4) plus the well-known,
# unambiguous canonical names for these five zones.
ZONE_NAMES = {
    "crypts": "Ruins",
    "weald": "Weald",
    "warrens": "Warrens",
    "cove": "Cove",
    "darkestdungeon": "Darkest Dungeon",
}

# Recognized mash.darkest record types — every record in every core-zone
# mash file (verified: 0 unrecognized record types across all 21 files).
MASH_CATEGORIES = {"hall", "room", "boss", "stall", "named"}

# Stale internal name in the game's own encounter tables (8 occurrences,
# warrens.2/warrens.4.mash.darkest only) — the monster's actual asset
# folder (and therefore its resolvable id in this simulator) is
# monsters/carrion_eater_big/, not monsters/carrion_eater_large/, which
# doesn't exist anywhere in the install. Confirmed genuine legacy mismatch
# in the shipped game data, not a parsing issue: carrion_eater_big_A's own
# .info.darkest has no alternate .id field, so "large" only ever appears in
# these 8 mash lines. Corrected here rather than left silently unresolvable.
ENEMY_ID_ALIASES = {
    "carrion_eater_large_A": "carrion_eater_big_A",
}


class EnemyCompLoader:
    """Load real enemy encounter groups from dungeons/<zone>/<zone>.<tier>.mash.darkest
    — the game's own weighted-random encounter tables (hall/room/boss/stall/
    named), not hand-curated data. Only the 5 core zones' base numbered mash
    files are loaded (crypts/weald/warrens/cove tiers 1-5, darkestdungeon
    tier 6 only — it has no tiers 1-5, its zone is entirely the 4 scripted
    story quests). `<zone>.conditional.<tier>.mash.darkest` files are
    deliberately excluded: they're rare, condition-gated special-event
    encounters (e.g. a 1-3% chance Shambler ambush, a Collector event gated
    on inventory value) rather than normal encounters, and would skew a
    representative "battery" of comps. DLC zones (Crimson Court's Courtyard,
    Color of Madness's Farmstead) and DLC-added encounters for the base
    zones aren't loaded either — see ASSUMPTIONS.md.
    """

    @staticmethod
    def load_all(game_dir: str | Path) -> list[EnemyCompEntry]:
        game_dir = Path(game_dir)
        dungeons_dir = game_dir / "dungeons"
        entries: list[EnemyCompEntry] = []

        for zone_id, zone_name in ZONE_NAMES.items():
            zone_dir = dungeons_dir / zone_id
            if not zone_dir.exists():
                continue
            for mash_path in sorted(zone_dir.glob(f"{zone_id}.*.mash.darkest")):
                # Filename shape is "<zone>.<tier>.mash.darkest" — the
                # middle segment is always a bare integer for the base
                # numbered tables; "conditional" sorts here too and is
                # skipped by the int() guard.
                stem_parts = mash_path.name.split(".")
                if len(stem_parts) < 4:
                    continue
                tier_str = stem_parts[1]
                if not tier_str.isdigit():
                    continue
                tier = int(tier_str)

                records = DarkestParser.parse_file(mash_path)
                for rec in records:
                    category = rec.get("_type", "")
                    if category not in MASH_CATEGORIES:
                        continue
                    types_raw = rec.get("types", "")
                    enemy_ids = str(types_raw).split() if types_raw else []
                    if not enemy_ids:
                        continue
                    enemy_ids = [ENEMY_ID_ALIASES.get(eid, eid) for eid in enemy_ids]
                    entries.append(EnemyCompEntry(
                        zone_id=zone_id,
                        zone_name=zone_name,
                        tier=tier,
                        category=category,
                        chance=float(rec.get("chance", 1.0)),
                        enemy_ids=enemy_ids,
                        name=rec.get("name"),
                    ))
        return entries

    @staticmethod
    def to_enemy_defs(store: DataStore, comp: EnemyCompEntry) -> list[EnemyDefinition]:
        """Resolve an EnemyCompEntry's enemy_ids against store.enemies, in
        the comp's own order (used as position 1..N) — ready to pass
        straight to CombatEngine.reset(enemy_defs=...). Mirrors
        CompositionLoader.add_enemies' warn-and-skip behavior for any id
        that doesn't resolve (shouldn't happen for the 5 core zones after
        ENEMY_ID_ALIASES, but DLC/modded stores may not have every id)."""
        defs = []
        for eid in comp.enemy_ids:
            ed = store.enemies.get(eid)
            if ed:
                defs.append(ed)
            else:
                print(f"  Warning: enemy '{eid}' not found")
        return defs
