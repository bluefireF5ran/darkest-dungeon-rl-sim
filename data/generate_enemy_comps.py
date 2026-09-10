"""One-off generator for the ENEMY_COMPS/ JSON battery.

ENEMY_COMPS/<zone>.json is a generated *snapshot* for browsing/reference —
mirrors the role CSVs play for hero data (metadata index, not combat-
authoritative). The live, authoritative source is always
DataStore.enemy_comps (data/enemy_comp_loader.py), parsed fresh from the
game install on every DataLoader.load_all() call. Re-run this script to
regenerate the snapshot if the game install's mash.darkest files change.

Usage (from repo root — must run as a module, not a script path: running it
as `python data/generate_enemy_comps.py` makes Python shadow the stdlib
`types` module with this repo's data/types.py and crash on import):
    python -m data.generate_enemy_comps
"""

from __future__ import annotations
import json
from pathlib import Path
from collections import defaultdict

from data.csv_loader import csv_root
from data.data_loader import DataLoader
from data.enemy_comp_loader import ZONE_NAMES

from data.paths import GAME_DIR
from data.paths import CSV_DIR
OUT_DIR = csv_root(CSV_DIR) / "ENEMY_COMPS"

ZONE_FILE_NAMES = {
    "crypts": "ruins",
    "weald": "weald",
    "warrens": "warrens",
    "cove": "cove",
    "darkestdungeon": "darkest_dungeon",
}


def main():
    store = DataLoader.load_all(CSV_DIR, GAME_DIR)
    OUT_DIR.mkdir(exist_ok=True)

    by_zone: dict[str, list] = defaultdict(list)
    for entry in store.enemy_comps:
        by_zone[entry.zone_id].append(entry)

    for zone_id, zone_name in ZONE_NAMES.items():
        entries = by_zone.get(zone_id, [])
        entries.sort(key=lambda e: (e.tier, e.category, e.name or "", e.enemy_ids))
        payload = {
            "zoneId": zone_id,
            "zoneName": zone_name,
            "source": f"dungeons/{zone_id}/{zone_id}.<tier>.mash.darkest",
            "comps": [
                {
                    "tier": e.tier,
                    "category": e.category,
                    "chance": e.chance,
                    "name": e.name,
                    "enemies": e.enemy_ids,
                }
                for e in entries
            ],
        }
        out_path = OUT_DIR / f"{ZONE_FILE_NAMES[zone_id]}.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        print(f"{out_path.name}: {len(entries)} comps")


if __name__ == "__main__":
    main()
