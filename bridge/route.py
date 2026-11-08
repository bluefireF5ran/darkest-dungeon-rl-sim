"""Route policy — PLAYBOOK 2.2, over the decoded dungeon graph.

Where to walk next: cover what the quest actually requires, in as few moves as
possible, without walking the same corridor twice. Reads `bridge/dungeon_map.py`
for the graph and the raid save for where the party is and what it came for.

## The two facts that make this possible, both recovered here

**`raid.in_area` is the party's current area, stored as packed little-endian
ASCII** — the same convention as the map's `entrance_id`, not `dd_name_hash`.
`CLAUDE.md` had it flagged as "matches no real dungeon id", which was true of the
hash and hid the answer. 388 of 395 paired captures resolve to an area the map
already marks visited; the other 7 sit on the entrance one write early, since the
raid file lands before the map file on a room transition.

**`raid.raid_instance` holds the entire active quest** — `type`, `dungeon`,
`difficulty`, `goal_ids`, and the full completion reward down to the trinket.
Present in **399 of 399** captures.

> This contradicts a documented invariant. `CLAUDE.md` says zone and tier "come
> from the enemy line, not from the raid save (which has neither)", which is why
> `bridge/rollout.py::infer_zone_and_tier` scores encounter overlap to guess
> them. The raid save *does* have both. The inference still earns its place as a
> fallback for a capture with no `raid_instance`, but it is no longer the only
> option, and where they disagree the save wins.

**`goal_ids` is a length-prefixed string list**, not ids: a 4-byte count, then
per entry a 4-byte length and a NUL-terminated name — `explore_all_rooms`,
`battle_all_rooms`, `survive_short`. So the objective is stated in plain text and
does not have to be inferred from the quest type.

## What the planner does, and what it refuses to do

Rooms are nodes, corridors are edges. The mandatory target set comes from the
goal; the order is an exact shortest cover (Held-Karp over subsets — these maps
are 7-9 rooms, so exact is cheap and there is no reason to approximate).

**Optional detours are priced, not decided.** PLAYBOOK 2.1 prices a treasure at
"usually one extra fight for a very high reward", and how a player trades moves
and fights against loot is a judgement no shipped file contains. So the planner
reports the objective route and, separately, what each detour would add — and
leaves the trade to the reader rather than inventing a weight and hiding it.
"""

from __future__ import annotations

import struct
import sys
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bridge.dungeon_map import (
    BATTLE_CONTENTS, CURIO_CONTENTS, DungeonMap, KNOWLEDGE_VISITED,
    TREASURE_CONTENTS, read_dungeon_map,
)
from data.dson import parse_file

# goal_ids -> what the party must actually do. Anything unrecognised falls back
# to covering every room, which is the safe superset.
GOAL_EXPLORE = "explore_all_rooms"
GOAL_BATTLE = "battle_all_rooms"
GOAL_ACTIVATE = "activate"
GOAL_GATHER = "gather"

# Quest types that want the goal room and nothing else — PLAYBOOK 2.2: "go
# straight to the objective, to arrive at the boss with the best HP and stress".
RUSH_TYPES = {"kill_boss", "plot_kill_boss"}


def unpack_area(value: Optional[int]) -> Optional[str]:
    """A 4-char area name stored as a little-endian int."""
    if value is None:
        return None
    try:
        name = struct.pack("<i", int(value)).decode("ascii", "replace").rstrip("\x00")
    except (struct.error, ValueError):
        return None
    return None if (name == "none" or not name.isprintable()) else name


def decode_goal_ids(raw) -> tuple[str, ...]:
    """`goal_ids` as its list of names.

    Layout: 4-byte count, then per entry a 4-byte length and a NUL-terminated
    ASCII name. DSON hands this back either already-decoded (a list of ints, when
    the blob happened to parse as one) or as raw bytes; only the bytes form
    carries the names.
    """
    if isinstance(raw, str):
        raw = raw.encode("latin-1", "ignore")
    if not isinstance(raw, (bytes, bytearray)) or len(raw) < 8:
        return ()
    out: list[str] = []
    try:
        count = struct.unpack_from("<i", raw, 0)[0]
        offset = 4
        for _ in range(max(0, count)):
            length = struct.unpack_from("<i", raw, offset)[0]
            offset += 4
            name = raw[offset:offset + length].split(b"\x00")[0].decode("ascii", "replace")
            offset += length
            if name:
                out.append(name)
    except (struct.error, IndexError):
        return tuple(out)
    return tuple(out)


@dataclass(frozen=True)
class Quest:
    """The active quest, straight from `raid.raid_instance`."""
    quest_id: str = ""
    quest_type: str = ""
    dungeon: str = ""
    difficulty: int = 0
    length: int = 0
    goals: tuple[str, ...] = ()

    @property
    def rushes(self) -> bool:
        return self.quest_type in RUSH_TYPES


@dataclass(frozen=True)
class Detour:
    """An optional target and exactly what taking it costs."""
    area_id: str
    what: str
    extra_moves: int
    extra_fights: int

    def line(self) -> str:
        fights = f", +{self.extra_fights} fight{'s' if self.extra_fights != 1 else ''}" \
            if self.extra_fights else ""
        return f"{self.area_id:6s} {self.what:34s} +{self.extra_moves} moves{fights}"


@dataclass(frozen=True)
class RoutePlan:
    walk: tuple[str, ...]          # areas in order, alternating room/corridor
    moves: int                     # corridors traversed, repeats included
    fights: int
    new_areas: int
    revisits: int
    targets: tuple[str, ...]
    detours: tuple[Detour, ...] = ()
    note: str = ""

    @property
    def next_step(self) -> Optional[str]:
        return self.walk[1] if len(self.walk) > 1 else None

    def line(self) -> str:
        return (f"{' > '.join(self.walk)}\n"
                f"  {self.moves} moves, {self.fights} fights, "
                f"{self.new_areas} new areas, {self.revisits} revisits")


def read_quest(raid_file: str | Path) -> Optional[Quest]:
    """The active quest from a raid save. None outside a raid."""
    root = parse_file(Path(raid_file))
    instance = root.get("raid_instance") if root is not None else None
    if instance is None:
        return None
    data = instance.to_dict()
    return Quest(
        quest_id=str(data.get("id") or ""),
        quest_type=str(data.get("type") or ""),
        dungeon=str(data.get("dungeon") or ""),
        difficulty=int(data.get("difficulty") or 0),
        length=int(data.get("length") or 0),
        goals=decode_goal_ids(data.get("goal_ids")),
    )


def read_position(raid_file: str | Path) -> Optional[str]:
    """Which area the party stands in, from `raid.in_area`."""
    root = parse_file(Path(raid_file))
    if root is None:
        return None
    field_ = root.get("in_area")
    return unpack_area(field_.value) if field_ is not None else None


def read_last_area(raid_file: str | Path) -> Optional[str]:
    """The area the party came from, from `raid.last_room_id`.

    **This is the direction signal that survives a SECOND PASS.**
    `Area.standing_on` reads which end the party entered by off the dark run
    it leaves behind it, and a corridor already walked dark end to end has no
    run left to read -- which is exactly the corridor a deferred curio sends
    the party back into. Nothing had ever needed that before the loot layer
    started planning a walk home.

    The game records it outright, so it needs no inference and cannot
    saturate. Named `last_room_id`, but it holds whatever AREA was last left,
    corridor included: standing in `rooB` after crossing `coAB` it reads
    `coAB`, and standing in `coAB` after leaving `rooB` it reads `rooB` --
    which is the end the party is standing at.
    """
    root = parse_file(Path(raid_file))
    if root is None:
        return None
    field_ = root.get("last_room_id")
    return unpack_area(field_.value) if field_ is not None else None


def read_area_tile(raid_file: str | Path) -> Optional[int]:
    """How many tiles into the current area the party has walked.

    `raid.areatile`, counted from the end it ENTERED by and not in the map's
    own tile numbering -- so turning it into a `Tile.index` needs to know which
    end that was, which is what `Area.standing_on` answers. Monotone, exact,
    and it keeps working on a corridor already walked dark, where every
    map-side signal has saturated.
    """
    root = parse_file(Path(raid_file))
    if root is None:
        return None
    field_ = root.get("areatile")
    if field_ is None or not isinstance(field_.value, int):
        return None
    return field_.value


# --- graph ---

def _all_pairs(dungeon: DungeonMap) -> dict[str, dict[str, list[str]]]:
    """Shortest walk between every pair of rooms, as area sequences.

    BFS from each room. Unweighted because every corridor is one move; anything
    that makes one corridor worse than another (a fight, a trap) is reported
    alongside the route rather than folded into a distance the reader cannot see.
    """
    adjacency = dungeon.graph()
    out: dict[str, dict[str, list[str]]] = {}
    for start in adjacency:
        prev: dict[str, tuple[str, str]] = {}
        seen = {start}
        queue = deque([start])
        while queue:
            room = queue.popleft()
            for nxt, corridor in adjacency.get(room, {}).items():
                if nxt in seen:
                    continue
                seen.add(nxt)
                prev[nxt] = (room, corridor)
                queue.append(nxt)
        routes = {start: [start]}
        for room in seen - {start}:
            chain = [room]
            cur = room
            while cur != start:
                before, corridor = prev[cur]
                chain.extend((corridor, before))
                cur = before
            routes[room] = list(reversed(chain))
        out[start] = routes
    return out


def _cover_order(start: str, targets: Sequence[str],
                 routes: dict[str, dict[str, list[str]]]) -> list[str]:
    """Cheapest order to visit every target, exactly.

    Held-Karp over subsets. A real map has 7-9 rooms, so 2**n * n**2 is a few
    hundred thousand operations — there is no reason to approximate and then
    wonder whether a bad route is the planner's fault or the map's.
    """
    reachable = [t for t in targets if t in routes.get(start, {})]
    if not reachable:
        return []
    n = len(reachable)
    def dist(a: str, b: str) -> int:
        path = routes.get(a, {}).get(b)
        return len(path) // 2 if path else 10 ** 6

    best: dict[tuple[int, int], tuple[int, list[int]]] = {}
    for i, target in enumerate(reachable):
        best[(1 << i, i)] = (dist(start, target), [i])
    for _ in range(n - 1):
        for (mask, last), (cost, order) in list(best.items()):
            for nxt in range(n):
                if mask & (1 << nxt):
                    continue
                new_mask = mask | (1 << nxt)
                new_cost = cost + dist(reachable[last], reachable[nxt])
                current = best.get((new_mask, nxt))
                if current is None or new_cost < current[0]:
                    best[(new_mask, nxt)] = (new_cost, order + [nxt])
    full = (1 << n) - 1
    finals = [(c, o) for (mask, _), (c, o) in best.items() if mask == full]
    if not finals:
        return []
    _cost, order = min(finals, key=lambda x: x[0])
    return [reachable[i] for i in order]


def _stitch(start: str, order: Sequence[str],
            routes: dict[str, dict[str, list[str]]]) -> list[str]:
    walk = [start]
    cur = start
    for target in order:
        leg = routes.get(cur, {}).get(target)
        if not leg:
            continue
        walk.extend(leg[1:])
        cur = target
    return walk


# --- targets ---

def _rooms_needing_battle(dungeon: DungeonMap) -> list[str]:
    return sorted({area_id for area_id, tile in dungeon.battles()
                   if dungeon.areas[area_id].is_room})


def _unvisited_rooms(dungeon: DungeonMap) -> list[str]:
    return sorted(r for r, area in dungeon.rooms.items() if not area.explored)


def mandatory_targets(dungeon: DungeonMap, quest: Optional[Quest]) -> list[str]:
    """What the quest still requires, as rooms.

    Falls back to "every room not yet visited" for an unrecognised goal — the
    safe superset, never a guess at a narrower objective.
    """
    if quest is not None and quest.rushes and dungeon.final_room_id:
        return [dungeon.final_room_id]
    goals = set(quest.goals) if quest else set()
    if GOAL_BATTLE in goals:
        return _rooms_needing_battle(dungeon)
    if GOAL_EXPLORE in goals:
        return _unvisited_rooms(dungeon)
    return _unvisited_rooms(dungeon)


def _describe(dungeon: DungeonMap, area_id: str) -> str:
    area = dungeon.areas.get(area_id)
    if area is None:
        return area_id
    bits = [t.describe() for t in area.tiles if t.is_active]
    return ", ".join(bits) or "empty"


def optional_targets(dungeon: DungeonMap, quest: Optional[Quest] = None) -> dict[str, str]:
    """Rooms worth a detour.

    Unspent room treasure and curios always. On a **boss quest** unvisited rooms
    join them: PLAYBOOK 2.2 says go straight to the goal, so exploring stops
    being required and becomes a priced choice — which is the case where this
    list does the most work, since a cleanse or explore quest has already made
    almost every room mandatory.

    Corridor curios are left out: a corridor is only ever reached by walking it,
    so it is a property of the route, not an alternative to it.
    """
    out: dict[str, str] = {}
    for area_id, tile in dungeon.curios():
        if dungeon.areas[area_id].is_room and tile.content in (
                set(TREASURE_CONTENTS) | set(CURIO_CONTENTS)):
            out[area_id] = tile.describe()
    if quest is not None and quest.rushes:
        for room in _unvisited_rooms(dungeon):
            out.setdefault(room, _describe(dungeon, room))
    return out


def _walk_stats(dungeon: DungeonMap, walk: Sequence[str]) -> tuple[int, int, int, int]:
    """(moves, fights, new areas, revisits) for a walk."""
    corridors = [a for a in walk[1:] if a in dungeon.corridors]
    moves = len(corridors)
    seen: set[str] = set()
    revisits = 0
    new_areas = 0
    fights = 0
    for area_id in walk:
        area = dungeon.areas.get(area_id)
        if area is None:
            continue
        if area_id in seen:
            revisits += 1
        else:
            seen.add(area_id)
            if not area.explored:
                new_areas += 1
            fights += sum(1 for t in area.tiles if t.has_battle)
    return moves, fights, new_areas, revisits


# --- the policy ---

def plan_route(dungeon: DungeonMap, current: Optional[str] = None,
               quest: Optional[Quest] = None) -> RoutePlan:
    """The route the objective needs, with the detours priced beside it."""
    start = current or dungeon.entrance_id or next(iter(dungeon.rooms), "")
    routes = _all_pairs(dungeon)
    targets = mandatory_targets(dungeon, quest)

    # A party standing in a corridor can leave by either end, so BOTH are
    # planned and the cheaper wins. Picking an end arbitrarily silently adds a
    # move whenever it guesses the wrong one, and a corridor is where a save
    # gets written most often.
    corridor_prefix: list[str] = []
    # Only when there is something to cover. With the objective met, both
    # ends tie at zero and the first one won, so the walk named a room and
    # `next_step` was never empty -- the loot walk, which only takes over on
    # an empty step, lost every corridor. Live 2026-09-25: rooF -> coFG ->
    # rooF, four laps with an iron maiden still waiting past coFG.
    if start in dungeon.corridors and targets:
        best_plan: Optional[tuple[int, str, list[str]]] = None
        # `corridor_rooms`, never `Area.rooms`: the name split invents a room
        # on a generated map and this branch then plans from somewhere the
        # party cannot walk to. See `DungeonMap.corridor_rooms`.
        for end in dungeon.corridor_rooms(start):
            if end not in routes:
                continue
            candidate = _stitch(end, _cover_order(end, targets, routes), routes)
            cost = _walk_stats(dungeon, candidate)[0]
            if best_plan is None or cost < best_plan[0]:
                best_plan = (cost, end, candidate)
        if best_plan is not None:
            corridor_prefix = [start]
            start = best_plan[1]

    order = _cover_order(start, targets, routes)
    walk = corridor_prefix + _stitch(start, order, routes)
    moves, fights, new_areas, revisits = _walk_stats(dungeon, walk)

    note = ""
    if quest is not None and quest.rushes:
        note = "boss quest: straight to the goal room, saving HP and stress"
    elif not targets:
        note = "objective already met — what is left is loot, priced below"

    detours = []
    on_route = set(walk)
    for area_id, what in optional_targets(dungeon, quest).items():
        if area_id in on_route:
            continue
        with_detour = _cover_order(start, list(targets) + [area_id], routes)
        detour_walk = _stitch(start, with_detour, routes)
        d_moves, d_fights, _new, _re = _walk_stats(dungeon, detour_walk)
        detours.append(Detour(area_id=area_id, what=what,
                              extra_moves=max(0, d_moves - moves),
                              extra_fights=max(0, d_fights - fights)))

    return RoutePlan(walk=tuple(walk), moves=moves, fights=fights,
                     new_areas=new_areas, revisits=revisits,
                     targets=tuple(targets),
                     detours=tuple(sorted(detours, key=lambda d: (d.extra_moves, d.extra_fights))),
                     note=note)


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description="where to walk next")
    ap.add_argument("map_file", help="a persist.map.json (a COPY, never the live one)")
    ap.add_argument("--raid", default=None,
                    help="the paired persist.raid.json, for position and quest")
    ap.add_argument("--from", dest="start", default=None, help="override the current area")
    args = ap.parse_args()

    dungeon = read_dungeon_map(args.map_file)
    if dungeon is None:
        print("no dungeon map in that file (in town?)")
        return

    raid = args.raid or str(args.map_file).replace("map.json", "raid.json")
    quest = position = None
    if Path(raid).exists():
        quest = read_quest(raid)
        position = read_position(raid)

    if quest is not None:
        print(f"quest: {quest.quest_id}  {quest.quest_type} in {quest.dungeon} "
              f"(difficulty {quest.difficulty}, length {quest.length})  "
              f"goals={'/'.join(quest.goals) or 'unstated'}")
    print(f"party is in {args.start or position or dungeon.entrance_id}\n")

    plan = plan_route(dungeon, args.start or position, quest)
    print(f"targets left: {' '.join(plan.targets) or 'none'}")
    print(plan.line())
    if plan.note:
        print(f"  note: {plan.note}")
    if plan.detours:
        print("\ndetours, priced:")
        for detour in plan.detours:
            print(f"  {detour.line()}")


if __name__ == "__main__":
    main()
