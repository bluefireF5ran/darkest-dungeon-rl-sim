"""The enemy group of every fight still waiting in this dungeon -- from the save.

Every battle tile in `persist.map.json` carries `mash_index` and `mash_type`:
an index into the zone's `<zone>.<tier>.mash.darkest` table, `hall` for
`mash_type` 0 and `room` for 1, fixed when the map was generated. Verified
2026-09-25 against the captured raids: 10 of 10 resolvable fights matched
their mash entry EXACTLY. A player cannot see this; the bridge can read it.

`-1` marks a tile with no fight waiting (or a fight already won), which is
also how `live_state._read_remaining_battles` counts what is left.
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Optional

from data.dson import parse_file
from data.enemy_comp_loader import ENEMY_ID_ALIASES
from data.paths import GAME_DIR

MASH_KINDS = {0: "hall", 1: "room", 2: "boss"}


@lru_cache(maxsize=32)
def mash_table(zone: str, tier: int, game_dir: str = str(GAME_DIR)) -> dict:
    """`{kind: [[enemy ids], ...]}` in file order, which is what the index counts."""
    path = Path(game_dir) / "dungeons" / zone / f"{zone}.{tier}.mash.darkest"
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    out: dict = {}
    for kind, types in re.findall(r"^(\w+):.*?\.types\s+([^\n]+)", text, re.M):
        ids = [ENEMY_ID_ALIASES.get(t, t) for t in types.split()]
        out.setdefault(kind, []).append(ids)
    return out


def fights_ahead(map_file, raid_file, here: Optional[str] = None,
                 game_dir: str = str(GAME_DIR)) -> list:
    """`[[enemy ids], ...]` for every fight still on the map, nearest first.

    Nearest by map distance from the party's area (`here`, default read from
    the raid). An unresolvable entry is skipped, never guessed. `[]` when
    anything needed is missing.
    """
    from bridge.dungeon_map import read_dungeon_map        # noqa: PLC0415
    from bridge.route import read_position                 # noqa: PLC0415
    # **A staged, validated copy**, like every other live read: the raw
    # parse of a save the game was mid-write on raised, the `except` below
    # turned it into `[]`, and the forecast silently fell back to sampling.
    # Live 2026-09-25 that is one poll reading 8% and the next 58%.
    try:
        from bridge.live_state import _stage               # noqa: PLC0415
        staged = _stage([Path(map_file), Path(raid_file)])
        map_file = staged / Path(map_file).name
        raid_file = staged / Path(raid_file).name
    except Exception:                                      # noqa: BLE001
        pass
    try:
        raid = parse_file(Path(raid_file))
        inst = raid.get("raid_instance")
        zone, tier = str(inst.get("dungeon").value), int(inst.get("difficulty").value)
        raw = parse_file(Path(map_file)).get("map").get("static_dynamic").get("areas")
        dungeon = read_dungeon_map(Path(map_file))
        here = here or read_position(raid_file)
    except Exception:                                      # noqa: BLE001
        return []
    table = mash_table(zone, tier, game_dir)
    origin = None
    if dungeon is not None and here in dungeon.areas:
        pts = [t.mappos for t in dungeon.areas[here].tiles if t.mappos]
        origin = pts[0] if pts else None
    names = list(dungeon.areas.keys()) if dungeon is not None else []
    found = []
    for n, area in enumerate(raw.children):
        tiles = area.get("tiles")
        if tiles is None:
            continue
        name = names[n] if n < len(names) else str(area.name)
        for i, tile in enumerate(tiles.children):
            idx_f, type_f = tile.get("mash_index"), tile.get("mash_type")
            if idx_f is None or idx_f.value in (None, -1):
                continue
            kind = MASH_KINDS.get(int(type_f.value) if type_f is not None else 0)
            entries = table.get(kind, [])
            idx = int(idx_f.value)
            if not 0 <= idx < len(entries):
                continue
            dist = 0.0
            if origin is not None and dungeon is not None and name in dungeon.areas:
                tl = dungeon.areas[name].tiles
                pos = tl[i].mappos if i < len(tl) and tl[i].mappos else None
                if pos:
                    dist = abs(pos[0] - origin[0]) + abs(pos[1] - origin[1])
            found.append((dist, list(entries[idx])))
    return [ids for _d, ids in sorted(found, key=lambda x: x[0])]
