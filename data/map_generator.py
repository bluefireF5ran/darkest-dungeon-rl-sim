"""`scripts/map_generator.darkest`: what a generated map contains.

Per zone (`dungeon_type`), length (`size`) and quest type, the game ships the
RANGE of every map feature: hallway battles, room battles, obstacles, curios,
hunger checks, corridors. `bridge/provision_needs.py` sizes the basket from it,
and the simulator's hunger rate comes from it (see `hunger_per_fight`).
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from data.paths import GAME_DIR

BASE_ZONES = ("crypts", "warrens", "weald", "cove")


@lru_cache(maxsize=2)
def map_budgets(game_dir: str = str(GAME_DIR)) -> dict:
    """`{(zone, size, quest_type): {field: (lo, hi)}}`; `{}` without the file."""
    path = Path(game_dir) / "scripts" / "map_generator.darkest"
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    out: dict = {}
    for block in text.split("map:")[1:]:
        fields = dict(re.findall(r"\.(\w+) ([^\n]+)", block))
        key = (fields.get("dungeon_type", "").strip(), fields.get("size", "").strip(),
               fields.get("quest_type", "").strip())
        ranges = {}
        for k, v in fields.items():
            nums = v.split()
            if nums and all(re.fullmatch(r"-?\d+(\.\d+)?", n) for n in nums):
                vals = [float(n) for n in nums]
                ranges[k] = (vals[0], vals[-1])
        out[key] = ranges
    return out


@lru_cache(maxsize=8)
def hunger_per_fight(size: str, game_dir: str = str(GAME_DIR)):
    """Hunger checks per fight a generated map of `size` holds, or None.

    Mean hallway hunger tiles over mean fights (hallway plus room battles),
    across every base-zone block of that size. Measured 2026-09-25: short
    0.36, medium 0.40, long 0.26 -- against the simulator's former flat 0.6,
    which was a declared proxy whose own falsifier was "count hunger checks in
    a real run".
    """
    mid = lambda r: (r[0] + r[1]) / 2                       # noqa: E731
    rows = [v for (zone, s, _q), v in map_budgets(game_dir).items()
            if s == size and zone in BASE_ZONES]
    if not rows:
        return None
    hunger = sum(mid(v.get("hallway_hunger", (0, 0))) for v in rows)
    fights = sum(mid(v.get("hallway_battle", (0, 0)))
                 + mid(v.get("total_room_battles", (0, 0))) for v in rows)
    return hunger / fights if fights else None
