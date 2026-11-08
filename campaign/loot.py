"""What a raid itself pays, on top of the quest reward: battle drops and
curios, as EXPECTED values from the shipped tables.

Every count and weight is game data, read through code the bridge already
uses for the same purpose:

- battle drops: each monster's `loot: .code .count` (`EnemyDefinition.loot_code`
  / `loot_count`) priced by `data.loot_loader.LootTables.expected`, averaged
  over the zone/tier's hall and room encounters by their mash `chance`;
- curio counts per map: `scripts/map_generator.darkest` through
  `bridge.provision_needs.map_budgets` (`hallway_curio`, `room_curio`,
  `room_guarded_curio`, `room_treasure`, `room_guarded_treasure`);
- which curio: `<zone>.props.darkest` weights (`provision_needs.zone_props`);
- what a curio pays: `bridge.lootplan.curio_options`.

ASSUMPTIONS, each a lower or upper bound rather than a guess at the middle:
- a curio gets the shop item that beats bare hands on it
  (`provision_needs._item_wins`, the rule the basket is derived from) while
  the raid's basket still holds one -- shovels for the hallway obstacles
  first -- and is taken bare-handed otherwise. Without a basket, every curio
  is bare-handed (the pre-2026-10-11 lower bound). Treasures are served
  first, then room curios, then hall curios;
- the pack never fills -- an upper bound: "missing curios" on the live raids
  were a full pack 33 times in 37 (memory: dd-full-pack-not-missed-w);
- torch is ignored (no darkness bonus, gold multiplier 1);
- loot scales with the fraction of the map's fights reached, so a retreat
  halfway pays half.
Calibrate the total against the live raids before trusting it in a ruler.
"""
from __future__ import annotations

from functools import lru_cache

from bridge.lootplan import curio_options
from bridge.provision_needs import _item_wins, map_budgets, zone_props
from data.loot_loader import LootValue, load_loot_tables
from data.paths import GAME_DIR

SIZE_FOR_LENGTH = {1: "short", 2: "medium", 3: "long", 4: "long", 5: "long"}
HALL_KEYS = ("hallway_curio",)
ROOM_KEYS = ("room_curio", "room_guarded_curio")
TREASURE_KEYS = ("room_treasure", "room_guarded_treasure")
BATTLE_CATEGORIES = ("hall", "room")


@lru_cache(maxsize=1)
def _tables():
    return load_loot_tables(str(GAME_DIR))


def _mean(lo_hi) -> float:
    lo, hi = lo_hi
    return (float(lo) + float(hi)) / 2.0


def battle_loot(store, dungeon: str, difficulty: int) -> LootValue:
    """Expected drop of one fight in this zone and tier."""
    tables = _tables()
    total, weight = LootValue(), 0.0
    for e in store.enemy_comps:
        if e.zone_id != dungeon or e.tier != difficulty or e.category not in BATTLE_CATEGORIES:
            continue
        w = max(float(e.chance), 0.0)
        if w <= 0:
            continue
        fight = LootValue()
        for mid in e.enemy_ids:
            m = store.enemies.get(mid)
            if m is None or not m.loot_code or m.loot_code == "NONE":
                continue
            fight = fight + tables.expected(m.loot_code, difficulty, dungeon) * m.loot_count
        total, weight = total + fight * w, weight + w
    return total * (1.0 / weight) if weight else LootValue()


@lru_cache(maxsize=256)
def _kind_value(dungeon: str, kind: str, difficulty: int) -> LootValue:
    props = zone_props(dungeon).get(kind, {})
    total, weight = LootValue(), 0.0
    for prop, w in props.items():
        opts = curio_options(prop, difficulty, dungeon)
        bare = next((o for o in opts if o.item is None), None)
        if bare is not None:
            total = total + bare.base * w
        weight += w
    return total * (1.0 / weight) if weight else LootValue()


def curio_loot(dungeon: str, difficulty: int, length: int,
               quest_type: str = "explore", supplies=None) -> LootValue:
    """Expected yield of every curio a map of this kind holds; with
    `supplies` (the raid's basket), each curio whose winning item is still in
    it takes the item (see the module docstring)."""
    budgets = map_budgets()
    size = SIZE_FOR_LENGTH.get(length, "long")
    b = budgets.get((dungeon, size, quest_type)) or budgets.get((dungeon, size, "explore")) or {}
    if supplies is None:
        total = LootValue()
        for keys, kind in ((HALL_KEYS, "hall_curios"), (ROOM_KEYS, "room_curios"),
                           (TREASURE_KEYS, "room_treasures")):
            n = sum(_mean(b[k]) for k in keys if k in b)
            if n:
                total = total + _kind_value(dungeon, kind, difficulty) * n
        return total
    left = {k: float(v) for k, v in supplies.items()}
    left["shovel"] = max(0.0, left.get("shovel", 0.0) - _mean(b.get("hallway_obstacle", (0, 0))))
    total = LootValue()
    for keys, kind in ((TREASURE_KEYS, "room_treasures"), (ROOM_KEYS, "room_curios"),
                       (HALL_KEYS, "hall_curios")):
        n = sum(_mean(b[k]) for k in keys if k in b)
        props = zone_props(dungeon).get(kind, {})
        weight = sum(props.values())
        if not n or not weight:
            continue
        for prop, w in props.items():
            opts = {o.item: o.base for o in curio_options(prop, difficulty, dungeon)}
            if None not in opts:
                continue
            count = n * w / weight
            item = next(iter(_item_wins(prop)), None)
            used = min(count, left.get(item, 0.0)) if item in opts else 0.0
            if used:
                left[item] -= used
                total = total + opts[item] * used
            total = total + opts[None] * (count - used)
    return total


def raid_loot(store, *, dungeon: str, difficulty: int, length: int, quest_type: str,
              combats_won: int, combats_total: int, supplies=None) -> LootValue:
    """The whole raid's expected take, scaled by how far the party got."""
    reached = combats_won / combats_total if combats_total else 0.0
    return (battle_loot(store, dungeon, difficulty) * combats_won
            + curio_loot(dungeon, difficulty, length, quest_type, supplies) * reached)
