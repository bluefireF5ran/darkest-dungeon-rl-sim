"""Provisions a quest NEEDS, derived from the game's own map and curio data.

Fran, PLAYBOOK 7.7 (2026-09-23): *"it should come from game averages rather
than community guidelines, but in this game its preferable to go over than
under on provisions (we prefer getting extra food to heal rather than eating a
hunger/shovel check without it) there are curios that can't be opened with
good odds without that provisions"*. `data/provisions.py::GUIDE` is a
community image; this is the derivation that replaces it item by item.

Everything here is read from shipped files:

* `scripts/map_generator.darkest` -- per zone, quest type and length, the
  RANGE of hallway obstacles, hunger checks, hallway curios and guarded room
  curios a generated map gets.
* `dungeons/<zone>/<zone>.props.darkest` -- the zone's curio spawn weights
  (hall and room) and its obstacle.
* the curio sheet (`bridge/curios.py`) -- for each curio and each provision,
  whether using it beats bare hands.

**Rounded up at the top of every range**, per "go over rather than under".
**Torches** (2026-09-23): enough to hold MAX light (PLAYBOOK 7.2) across every
corridor the length generates, walked once. A new tile burns `tile_light_loss`
`dim` (6) at any light -- measured live, see `data/loot_loader.walk_costs` --
over `WALKABLE_TILES` per corridor, and a torch restores 25.
"""
from __future__ import annotations

import math
import re
from functools import lru_cache
from pathlib import Path

from data.paths import GAME_DIR

#: Heroes that eat at a hunger check -- one food each.
PARTY = 4
#: Walkable tiles in a corridor. Captured maps average 5.8 tiles including
#: the two door ends; the live corridor walks cost about 19-24 light, which is
#: four new tiles at 6.
WALKABLE_TILES = 4

#: Items a curio can take that the provisioner sells.
SHOP_ITEMS = ("food", "shovel", "antivenom", "bandage", "medicinal_herbs",
              "skeleton_key", "holy_water", "laudanum", "torch")


def map_budgets(game_dir: str = str(GAME_DIR)) -> dict:
    """`{(zone, size, quest_type): {field: (lo, hi)}}` -- see `data/map_generator`."""
    from data.map_generator import map_budgets as _budgets    # noqa: PLC0415
    return _budgets(game_dir)


@lru_cache(maxsize=8)
def zone_props(zone: str, game_dir: str = str(GAME_DIR)) -> dict:
    """`{kind: {prop: weight}}` from `<zone>.props.darkest`."""
    path = Path(game_dir) / "dungeons" / zone / f"{zone}.props.darkest"
    out: dict = {}
    for kind, chance, types in re.findall(
            r"(\w+):\s*\.chance\s+([\d.]+)\s+\.types\s+([\w ]+)",
            path.read_text(encoding="utf-8", errors="replace")):
        for prop in types.split():
            out.setdefault(kind, {})[prop] = out.get(kind, {}).get(prop, 0) + float(chance)
    return out


def _item_wins(curio_prop: str) -> dict:
    """`{item: 1}` for each shop item that beats bare hands on this curio."""
    from bridge import curios                   # noqa: PLC0415
    cur = curios.definition_for(curio_prop)
    if cur is None:
        return {}
    bare = curios.bare_hand_value(cur)
    best, best_v = None, bare
    for item, inter in cur.items.items():
        if item not in SHOP_ITEMS:
            continue
        v = curios.item_value(inter)
        if v > best_v:
            best, best_v = item, v
    return {best: 1} if best else {}


def curio_needs(zone: str, count: float, kind: str,
                game_dir: str = str(GAME_DIR)) -> dict:
    """Expected items for `count` curios of `kind` spawned in `zone`."""
    weights = zone_props(zone, game_dir).get(kind, {})
    total = sum(weights.values())
    out: dict = {}
    if not total:
        return out
    for prop, w in weights.items():
        for item in _item_wins(prop):
            out[item] = out.get(item, 0.0) + count * w / total
    return out


def derived_basket(zone: str, band: str, quest_type: str = "explore",
                   game_dir: str = str(GAME_DIR)) -> dict:
    """`{item: quantity}` for one quest, every count at the TOP of its range.

    Falls back to the zone's `explore` budget when the quest type has no
    block of its own (every zone ships explore for every length).
    """
    budgets = map_budgets(game_dir)
    b = budgets.get((zone, band, quest_type)) or budgets.get((zone, band, "explore"))
    if not b:
        return {}
    hi = {k: v[1] for k, v in b.items()}
    need: dict = {}
    for item, n in curio_needs(zone, hi.get("hallway_curio", 0), "hall_curios",
                               game_dir).items():
        need[item] = need.get(item, 0.0) + n
    rooms = hi.get("room_guarded_curio", 0) + hi.get("room_curio", 0)
    for item, n in curio_needs(zone, rooms, "room_curios", game_dir).items():
        need[item] = need.get(item, 0.0) + n
    treasures = hi.get("room_guarded_treasure", 0) + hi.get("room_treasure", 0)
    for item, n in curio_needs(zone, treasures, "room_treasures", game_dir).items():
        need[item] = need.get(item, 0.0) + n
    need["shovel"] = need.get("shovel", 0.0) + hi.get("hallway_obstacle", 0)
    # Max light the whole way: every generated corridor, walked once.
    from bridge.torchlight import TORCH_RESTORE          # noqa: PLC0415
    from data.loot_loader import walk_costs              # noqa: PLC0415
    per_tile = walk_costs(100.0, game_dir).light_loss
    corridors = hi.get("base_corridor_number", 0)
    need["torch"] = need.get("torch", 0.0) + (
        corridors * WALKABLE_TILES * per_tile / TORCH_RESTORE)
    need["food"] = need.get("food", 0.0) + PARTY * hi.get("hallway_hunger", 0)
    return {k: int(math.ceil(v)) for k, v in need.items() if v > 0}


def main() -> None:
    from data.provisions import GUIDE, ZONES      # noqa: PLC0415
    for zone in ZONES:
        for band in ("short", "medium", "long"):
            guide = {k: v[1] for k, v in GUIDE.get(zone, {}).get(band, {}).items()}
            print(f"{zone:8} {band:6} derived {derived_basket(zone, band)}")
            print(f"{'':15} guide   {guide}")


if __name__ == "__main__":
    main()
