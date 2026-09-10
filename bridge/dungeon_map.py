"""Decode `persist.map.json` into a dungeon graph: rooms, corridors, and what
is on every tile.

The third thing the bridge can read. `live_state.py` answers "what is the board
in this fight", `town_state.py` answers "what does the Hamlet hold"; this
answers **"what is left in this dungeon, and how do we get there"** — the input
both route advice (PLAYBOOK 2.2) and curio advice (PLAYBOOK 2.4) need.

Same discipline as `live_state.py`: a pure save reader, no `DataLoader`, cheap
enough to poll. Its only game-file reads are the curio and trap name tables,
cached at module level.

## What the format turned out to be

**The graph is in the area names.** Areas are `rooA`, `rooB`, `coAB` — a room is
`roo<letter>`, a corridor is `co<letter><letter>` naming the two rooms it joins.
Nothing else in the file stores connectivity.

**`entrance_id` / `final_room_id` are the 4-char area name packed little-endian**,
NOT `dd_name_hash` (which is what the ids elsewhere in the format use, so this is
worth knowing before spending an afternoon inverting the wrong function).
`final_room_id` reads `'none'` until the quest goal room is known.

**`curio_prop` and `trap` ARE `dd_name_hash`**, and both invert cleanly: 22/22
distinct curios and 2/2 traps in `archive/traces/raidsnaps*/` resolved against
`curios/curio_props.csv` and `props/trap_definitions.json`. Decode the hash
**unsigned** — DSON reads 4-byte fields signed, so `poison_cloud` arrives as
`-702901882`. Same trap that cost the corpse decoder 45% of its lookups.

**`content` is the live state; `curio_prop` is the identity.** A used curio keeps
its `curio_prop` and drops to `content = 0`, so "is there still a crate here"
is `content`, and "what was it" is `curio_prop`. 339 of the captured curio tiles
are already-consumed ones.

## The content enum, and how each code was pinned

`scripts/map_generator.darkest` names the vocabulary — corridors get
battle/trap/obstacle/curio/hunger, rooms get battle/curio/treasure and their
guarded variants. Codes 1, 3, 7 and the two guarded ones fall out of what
co-occurs with each (a battle tile always carries a `mash_index`, a trap tile
always a `trap`, and the guarded room codes resolve to `heirloom_chest` /
`locked_strongbox` for treasure against `eldritch_altar` for curio).

**4 vs 8 needed a separate argument**, since both are corridor-only with no
curio, trap or mash to key on. Every `hallway_hunger` range in the generator has
a **minimum of 1** while 33 of 44 `hallway_obstacle` ranges start at **0** — and
across the 30 distinct captured maps, code 8 is never absent and code 4 is absent
in 5. Hence 4 = obstacle, 8 = hunger.

The room codes come in guarded/unguarded pairs, and the unguarded ones are
**rare** — code 9 (plain treasure) appears 6 times in the whole capture corpus
against 361 of code 10, which is why a first pass over 120 snapshots missed it
entirely. `room_curio` presumably has a code of its own that has never been
captured, so an unrecognised content code is reported as `unknown:N` and left
alone rather than guessed at. `tests/bridge/test_dungeon_map.py` fails when a new one
shows up, which is how 9 was found.
"""

from __future__ import annotations

import csv
import glob
import json
import re
import struct
import sys
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data.dson import parse_bytes, parse_file, dd_name_hash

from data.paths import GAME_DIR

CONTENT_EMPTY = 0
CONTENT_BATTLE = 1
CONTENT_TRAP = 3
CONTENT_OBSTACLE = 4
CONTENT_GUARDED_CURIO = 6
CONTENT_CURIO = 7
CONTENT_HUNGER = 8
CONTENT_TREASURE = 9
CONTENT_GUARDED_TREASURE = 10

CONTENT_KINDS = {
    CONTENT_EMPTY: "empty",
    CONTENT_BATTLE: "battle",
    CONTENT_TRAP: "trap",
    CONTENT_OBSTACLE: "obstacle",
    CONTENT_GUARDED_CURIO: "guarded_curio",
    CONTENT_CURIO: "curio",
    CONTENT_HUNGER: "hunger",
    CONTENT_TREASURE: "treasure",
    CONTENT_GUARDED_TREASURE: "guarded_treasure",
}

# Room codes worth detouring for once the objective is met (PLAYBOOK 2.1).
TREASURE_CONTENTS = (CONTENT_TREASURE, CONTENT_GUARDED_TREASURE, CONTENT_GUARDED_CURIO)

# Everything that costs a fight to walk into. The guarded room codes are
# battles too — the "guard" IS an encounter.
BATTLE_CONTENTS = (CONTENT_BATTLE, CONTENT_GUARDED_CURIO, CONTENT_GUARDED_TREASURE)

CURIO_CONTENTS = (CONTENT_CURIO, CONTENT_GUARDED_CURIO,
                  CONTENT_TREASURE, CONTENT_GUARDED_TREASURE)

KNOWLEDGE_UNKNOWN = 1
KNOWLEDGE_SCOUTED = 2
KNOWLEDGE_VISITED = 3

KNOWLEDGE_NAMES = {
    KNOWLEDGE_UNKNOWN: "unknown",
    KNOWLEDGE_SCOUTED: "scouted",
    KNOWLEDGE_VISITED: "visited",
}

#: What the format writes for "there is nothing here". 0xCCCCCCCC is MSVC's
#: uninitialised-memory fill, so it is not a hash at all -- and a live save
#: simply omits the key, which is why this went unseen for months. The plain
#: JSON template writes the sentinel instead, and without this every tile in
#: it reports a trap called `#3435973836`.
EMPTY_HASHES = frozenset({0xCCCCCCCC, 0xFFFFFFFF})

_TILE_RE = re.compile(r"tile(\d+)$")
_CORRIDOR_RE = re.compile(r"^co(.+)$")

_name_table: Optional[dict[int, str]] = None


def _load_name_table() -> dict[int, str]:
    """hash -> name for every curio prop and trap, base game plus DLC.

    Only the two id spaces `persist.map.json` actually stores as hashes. Kept
    lazy and cached so importing this module costs nothing.
    """
    global _name_table
    if _name_table is not None:
        return _name_table
    names: set[str] = set()
    for path in glob.glob(str(GAME_DIR / "**" / "curio_props.csv"), recursive=True):
        try:
            with open(path, newline="", encoding="utf-8", errors="replace") as fh:
                for row in csv.reader(fh):
                    if row and row[0].strip() and " " not in row[0]:
                        names.add(row[0].strip())
        except OSError:
            continue
    for path in glob.glob(str(GAME_DIR / "**" / "trap_definitions.json"), recursive=True):
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                for prop in json.load(fh).get("props", []):
                    if isinstance(prop, dict) and prop.get("name"):
                        names.add(prop["name"])
        except (OSError, ValueError):
            continue
    _name_table = {dd_name_hash(n): n for n in names}
    return _name_table


def resolve_name(hashed: Optional[int]) -> Optional[str]:
    """Invert a stored id. Falls back to `#<hash>` rather than losing the value.

    Unsigned first: DSON decodes 4-byte fields signed and `dd_name_hash` is
    unsigned mod 2**32.
    """
    if not hashed:
        return None
    key = hashed & 0xFFFFFFFF
    if key in EMPTY_HASHES:
        return None
    return _load_name_table().get(key) or f"#{key}"


def _unpack_area_id(value: Optional[int]) -> Optional[str]:
    """`entrance_id`/`final_room_id` are the area name as 4 little-endian bytes."""
    if value is None:
        return None
    try:
        name = struct.pack("<i", int(value)).decode("ascii", "replace").rstrip("\x00")
    except (struct.error, ValueError):
        return None
    return None if name == "none" else name


@dataclass(frozen=True)
class Tile:
    index: int
    content: int
    curio_id: Optional[str]
    trap_id: Optional[str]
    mash_index: int
    knowledge: int
    lit: bool
    # Where the tile sits on the drawn map, in map units, one unit per tile.
    # None when the save carries no static half (the plain-text template).
    # This is the ONLY link between an area id and a pixel: the map panel is
    # the sole way out of a room, and it renders this grid. See
    # `bridge/mappanel.py`.
    mappos: Optional[tuple[float, float]] = None

    @property
    def kind(self) -> str:
        return CONTENT_KINDS.get(self.content, f"unknown:{self.content}")

    @property
    def is_active(self) -> bool:
        """Still there to deal with. A consumed curio keeps `curio_id` but
        drops to `content == 0`."""
        return self.content != CONTENT_EMPTY

    @property
    def has_battle(self) -> bool:
        """Keyed on `content`, NOT on `mash_index >= 0`.

        Both agree on every captured tile — `mash_index` runs 0..20 on battle
        tiles and -1 everywhere else — but `content` is the field that says
        whether the fight is still there, and keying on it is what makes the
        guarded room codes count as the battles they are. **`mash_index == 0` is
        a real index**, so any test of this field must be `>= 0`; treating it as
        falsy is how a throwaway survey invented 59 mash-less battles here."""
        return self.is_active and self.content in BATTLE_CONTENTS

    def describe(self) -> str:
        what = self.curio_id or self.trap_id or ""
        return f"{self.kind}{f'({what})' if what else ''}"


@dataclass(frozen=True)
class Walk:
    """Where the party is along a corridor, from `Area.walked`.

    `passed` counts the tiles already visited, `from_front` says the party
    entered at tile 0 rather than at the last tile. `index` is the tile it is
    standing on, in the save's own numbering, which is what a curio or a
    battle tile is keyed by.
    """
    passed: int
    total: int
    from_front: bool

    @property
    def index(self) -> int:
        return self.passed - 1 if self.from_front else self.total - self.passed

    @property
    def remaining(self) -> int:
        return self.total - self.passed

    @property
    def entry_index(self) -> int:
        """The tile the party came in by. `A` walks back toward it."""
        return 0 if self.from_front else self.total - 1

    def key_toward(self, index: int) -> str:
        """`D` or `A`: which key walks toward tile `index`.

        **A HALLWAY HAS NO MAP DIRECTION -- only backtrack and advance.**
        Fran, after watching a party cross a corridor the wrong way and come
        back: *"hallways have no directions, only left (backtrack) and right
        (advance)"*. DD draws the party entering at the LEFT of the hallway
        art whichever way that hallway runs on the map, so `D` always walks
        deeper and `A` always walks back out the door it came in by. The rule
        was already written down in `act.plan_hallway_sweep`, in his words --
        *"a hallway is entered at its LEFT and left at its RIGHT"* -- and this
        property used to contradict it a hundred lines away.

        So the tile ORDER is a fact about the save's numbering and says
        nothing about the keyboard. What it does give is a reference point:
        the party advances away from `entry_index`, so a tile closer to that
        end than the party is behind it, and reaching it means backtracking.
        """
        here = abs(self.index - self.entry_index)
        there = abs(index - self.entry_index)
        return "A" if there < here else "D"

    def line(self) -> str:
        return (f"tile {self.index} of {self.total}, "
                f"{self.remaining} to go, entered "
                f"{'front' if self.from_front else 'back'}")


@dataclass(frozen=True)
class Area:
    id: str
    is_room: bool
    knowledge: int
    tiles: tuple[Tile, ...]
    reversed_: bool = False

    @property
    def walked(self) -> Optional["Walk"]:
        """How far along this corridor the party has got, and from which end.

        **The save does carry a within-area position after all**, and it is
        this. Per-TILE `knowledge` rises to `KNOWLEDGE_VISITED` behind the
        party as it walks, so the visited tiles are a growing run from the end
        it came in by — measured over the whole capture corpus, **2517
        corridor observations and not one where that run is discontiguous**.
        `light` falls to 0 over the same tiles, one behind, which is what
        pins the frontier tile the party is standing on.

        Everything in `bridge/act.py` that walks used to be blind between one
        area boundary and the next: a held `A`/`D` wrote nothing to the save
        until the party crossed into the next area, so a walk could not be
        acknowledged, a curio could not be walked up to, and left-versus-right
        could only be learned by walking the wrong way and flipping. All three
        of those were consequences of this field not being read.

        None when the corridor has not been entered, or is fully walked and so
        no longer says which end it was entered by.
        """
        flags = [t.knowledge >= KNOWLEDGE_VISITED for t in self.tiles]
        if len(flags) < 2 or not any(flags) or all(flags):
            return None
        if flags[0]:
            passed = next((i for i, f in enumerate(flags) if not f), len(flags))
            return Walk(passed=passed, total=len(flags), from_front=True)
        passed = sum(flags)
        return Walk(passed=passed, total=len(flags), from_front=False)

    @property
    def standing_on(self) -> Optional[tuple[int, bool]]:
        """`(tile the party is on, entered at tile 0)` from the per-tile `lit`.

        **This is the position signal, and `walked` is the one it replaces.**
        Tiles go DARK behind the party as it walks, one per tile, so the dark
        run's leading edge is where the party stands and which END the run
        grows from is which end they came in by.

        Graded against `raid.areatile` -- the game's own count of steps from
        the entry -- over every paired capture in the corpus: **224 of 224
        agree exactly**, versus 172 right / 51 WRONG / 45 silent for the
        knowledge run `walked` uses. The knowledge run is not a position: it
        marks tiles as revealed, so it can jump two at a time and can stall for
        a step while the party actually moves (`coCD` reads `..#####` at both
        areatile 3 and areatile 4). Steering a walk by it stopped the tutorial
        curio walk short.

        None when it genuinely cannot say: a corridor not entered (all lit),
        one already dark end to end from an earlier pass (all dark -- and
        `areatile` still works there, it just carries no direction), or a dark
        run in the middle, which is not a walk.
        """
        lit = [t.lit for t in self.tiles]
        if len(lit) < 2 or all(lit) or not any(lit):
            return None
        if not lit[0]:
            return (next(i for i, x in enumerate(lit) if x) - 1, True)
        if not lit[-1]:
            n = next(i for i, x in enumerate(reversed(lit)) if x)
            return (len(lit) - n, False)
        return None

    @property
    def rooms(self) -> tuple[str, ...]:
        """The rooms a corridor joins, from its name. Empty for a room."""
        m = _CORRIDOR_RE.match(self.id)
        if self.is_room or not m:
            return ()
        letters = m.group(1)
        if len(letters) % 2:
            return ()
        half = len(letters) // 2
        return (f"roo{letters[:half]}", f"roo{letters[half:]}")

    @property
    def explored(self) -> bool:
        return self.knowledge >= KNOWLEDGE_VISITED

    @property
    def known(self) -> bool:
        """Seen at all — walked through, or revealed by scouting."""
        return self.knowledge >= KNOWLEDGE_SCOUTED

    def active(self, *contents: int) -> tuple[Tile, ...]:
        return tuple(t for t in self.tiles if t.is_active and t.content in contents)


@dataclass(frozen=True)
class DungeonMap:
    entrance_id: Optional[str]
    final_room_id: Optional[str]
    areas: dict[str, Area]

    # --- structure ---

    @property
    def rooms(self) -> dict[str, Area]:
        return {k: v for k, v in self.areas.items() if v.is_room}

    @property
    def corridors(self) -> dict[str, Area]:
        return {k: v for k, v in self.areas.items() if not v.is_room}

    def graph(self) -> dict[str, dict[str, str]]:
        """room -> {neighbour room: the corridor between them}.

        **The corridor NAME is only the endpoint pair in the tutorial.** The
        Old Road ships `coAB`, and `Area.rooms` splits that into `rooA`+`rooB`
        correctly. A GENERATED dungeon numbers its corridors instead --
        `corA`..`corH` -- and the same split reads `corA` as `roo` + `r` +
        `A`, inventing a room `roor` that does not exist and hanging every
        corridor off it. That is a star through a phantom hub where the real
        map is a grid, and `path()` plans over it, so **every route on every
        real raid was computed against a fabricated topology**. It went
        unnoticed because the only map ever validated was the tutorial's.

        So connectivity is taken from GEOMETRY first: `Tile.mappos` is a
        contiguous unit grid, and a corridor's two ends each sit exactly one
        unit from the room they open into. That is derived rather than parsed,
        and it agrees with the name convention on the tutorial while getting
        the generated case right.

        The name remains the fallback for a corridor whose tiles carry no
        coordinates, which is what an area the party has not reached looks
        like in some saves.
        """
        adj: dict[str, dict[str, str]] = {r: {} for r in self.rooms}
        by_geometry = self._corridor_endpoints()
        for cid, corridor in self.corridors.items():
            pair = by_geometry.get(cid) or corridor.rooms
            if len(pair) != 2:
                continue
            a, b = pair
            adj.setdefault(a, {})[b] = cid
            adj.setdefault(b, {})[a] = cid
        return adj

    def corridor_rooms(self, area_id: str) -> tuple[str, ...]:
        """The two rooms a corridor joins -- GEOMETRY first, name as fallback.

        `graph()` has taken connectivity from `mappos` since the phantom-hub
        bug, but `Area.rooms` was left as the raw name split and callers kept
        reaching for it. On a generated map that reads `corE` as `roo` + `r` +
        `E` and answers `rooE`, which is a real room and NOT an end of `corE`
        -- so the wrong answer looks entirely plausible. `bridge/route.py` used
        it to decide which way to leave a corridor, and therefore planned from
        a room the party could not step into.

        Same fallback order as `graph()`, so the two can never disagree.
        """
        pair = self._corridor_endpoints().get(area_id)
        if pair and len(pair) == 2:
            return pair
        area = self.areas.get(area_id)
        return tuple(area.rooms) if area is not None else ()

    def _corridor_endpoints(self) -> dict[str, tuple[str, ...]]:
        """corridor id -> the two rooms its ends touch, from `mappos`.

        Manhattan distance 1, not Chebyshev: a corridor meets a room through a
        door, edge-on, and a diagonal neighbour is a room across a corner that
        it does not actually open into.

        A corridor is reported only when BOTH ends resolve to a room and the
        two are different. A partial answer would be worse than none: `graph`
        falls back to the name, and half a derived pair mixed with half a
        parsed one is a topology neither source agrees with.
        """
        room_at: dict[tuple[float, float], str] = {}
        for rid, room in self.rooms.items():
            for tile in room.tiles:
                if tile.mappos is not None:
                    room_at[tile.mappos] = rid
        if not room_at:
            return {}

        out: dict[str, tuple[str, ...]] = {}
        for cid, corridor in self.corridors.items():
            spots = [t.mappos for t in corridor.tiles if t.mappos is not None]
            if len(spots) < 1:
                continue
            ends = []
            for end in (spots[0], spots[-1]):
                touching = [rid for (rx, ry), rid in room_at.items()
                            if abs(rx - end[0]) + abs(ry - end[1]) == 1]
                if len(touching) == 1:
                    ends.append(touching[0])
            if len(ends) == 2 and ends[0] != ends[1]:
                out[cid] = tuple(ends)
        return out

    def path(self, start: str, goal: str) -> Optional[list[str]]:
        """Fewest-corridors route, as alternating room/corridor area ids.

        Breadth-first on purpose: every corridor costs one move, so there is no
        weight to respect yet. Cost by tiles, danger or unexplored-ness belongs
        to the route *policy*, not to the decoder.
        """
        if start == goal:
            return [start]
        adj = self.graph()
        if start not in adj or goal not in adj:
            return None
        prev: dict[str, tuple[str, str]] = {}
        seen = {start}
        queue = deque([start])
        while queue:
            room = queue.popleft()
            for nxt, corridor in adj.get(room, {}).items():
                if nxt in seen:
                    continue
                seen.add(nxt)
                prev[nxt] = (room, corridor)
                if nxt == goal:
                    out = [goal]
                    cur = goal
                    while cur != start:
                        room_before, corridor_id = prev[cur]
                        out.extend((corridor_id, room_before))
                        cur = room_before
                    return list(reversed(out))
                queue.append(nxt)
        return None

    # --- what is left ---

    def unexplored(self) -> tuple[Area, ...]:
        return tuple(a for a in self.areas.values() if not a.explored)

    def battles(self) -> tuple[tuple[str, Tile], ...]:
        return self._collect(lambda t: t.has_battle)

    def curios(self) -> tuple[tuple[str, Tile], ...]:
        return self._collect(lambda t: t.content in CURIO_CONTENTS)

    def traps(self) -> tuple[tuple[str, Tile], ...]:
        return self._collect(lambda t: t.content == CONTENT_TRAP)

    def obstacles(self) -> tuple[tuple[str, Tile], ...]:
        return self._collect(lambda t: t.content == CONTENT_OBSTACLE)

    def treasures(self) -> tuple[tuple[str, Tile], ...]:
        return self._collect(lambda t: t.content in TREASURE_CONTENTS)

    def _collect(self, pred) -> tuple[tuple[str, Tile], ...]:
        return tuple((aid, t) for aid, a in self.areas.items()
                     for t in a.tiles if t.is_active and pred(t))

    def explored_fraction(self) -> float:
        """Share of tiles walked. **Not verified against the quest's own
        counter** — an explore quest wants 90% of *something*, and which
        denominator the game uses is unconfirmed. Treat as an estimate until a
        live run pins it."""
        tiles = [t for a in self.areas.values() for t in a.tiles]
        if not tiles:
            return 0.0
        walked = sum(1 for t in tiles if t.knowledge >= KNOWLEDGE_VISITED)
        return walked / len(tiles)


def _as_float(value) -> Optional[float]:
    """A coordinate out of either save form.

    The two forms differ by container, which is what makes this exact rather
    than a heuristic: the text template writes `mappos` as an object of real
    floats, and DSON writes it as a 2-element array of **int32s that are float
    bit patterns** (`-1050673152` is `-14.0`). The same encoding carries
    `map.bounds`, which decodes to (min_x, max_x, min_y, max_y) and matches the
    tile extent exactly in all three captured dungeons.
    """
    if isinstance(value, float):
        return value
    if isinstance(value, int):
        return struct.unpack("<f", struct.pack("<i", value))[0]
    return None


def _read_geometry(map_field) -> dict[str, dict[int, tuple[float, float]]]:
    """`{area: {tile index: (x, y)}}` — the map's static half.

    A live `persist.map.json` splits the map in two: `static_dynamic.areas`
    holds what changes (knowledge, light, what is still on a tile) and
    `static_dynamic.static_save` holds the layout that was generated once. The
    second is a **length-prefixed nested DSON buffer**, exactly like a hero's
    `raw_data`: a little-endian u32 count, then the `01 B1 00 00` magic. So
    the geometry is not missing from live saves, it is one decode further in
    — which is why the bridge went months believing `mappos` was unavailable.

    Best-effort by design. Everything else this module reports works without
    the geometry, so a save that has no static half (the plain-text template,
    a torn write) must lose the coordinates and nothing more.
    """
    holder = map_field.get("static_dynamic") if map_field is not None else None
    blob = holder.get("static_save") if holder is not None else None
    raw = getattr(blob, "raw_value", b"") or b""
    if len(raw) < 8:
        return {}
    try:
        inner = parse_bytes(raw[4:]).to_dict()
    except Exception:                             # noqa: BLE001
        return {}
    areas = inner.get("areas")
    if not isinstance(areas, dict):
        return {}

    out: dict[str, dict[int, tuple[float, float]]] = {}
    for name, area in areas.items():
        if not isinstance(area, dict):
            continue
        tiles = area.get("tiles")
        if not isinstance(tiles, dict):
            continue
        per: dict[int, tuple[float, float]] = {}
        for key, tile in tiles.items():
            m = _TILE_RE.match(key)
            if m is None or not isinstance(tile, dict):
                continue
            pos = tile.get("mappos")
            if isinstance(pos, dict):
                x, y = _as_float(pos.get("x")), _as_float(pos.get("y"))
            elif isinstance(pos, (list, tuple)) and len(pos) >= 2:
                x, y = _as_float(pos[0]), _as_float(pos[1])
            else:
                continue
            if x is not None and y is not None:
                per[int(m.group(1))] = (x, y)
        if per:
            out[name] = per
    return out


def _read_tiles(area_dict: dict,
                geometry: Optional[dict[int, tuple[float, float]]] = None,
                ) -> tuple[Tile, ...]:
    raw = area_dict.get("tiles") or {}
    out = []
    for key, tile in raw.items():
        m = _TILE_RE.match(key)
        if m is None or not isinstance(tile, dict):
            continue
        index = int(m.group(1))
        out.append(Tile(
            index=index,
            content=int(tile.get("content") or 0),
            # `curio_prop` in a live save, `cur` in the shipped JSON
            # template -- same hash, two spellings of the same field.
            curio_id=resolve_name(tile.get("curio_prop", tile.get("cur"))),
            trap_id=resolve_name(tile.get("trap")),
            mash_index=int(tile.get("mash_index", -1) if tile.get("mash_index") is not None else -1),
            knowledge=int(tile.get("knowledge") or KNOWLEDGE_UNKNOWN),
            lit=bool(tile.get("light")),
            mappos=(geometry or {}).get(index),
        ))
    return tuple(sorted(out, key=lambda t: t.index))


def _read_plain_json(path: Path) -> Optional[DungeonMap]:
    """The map as the game SHIPS it, rather than as it saves it.

    Two files are written this way and both are worth having:
    `scripts/starting_save/persist.map.json` is the Old Road, so the tutorial
    is a fixture that never needs the game running; `maps/*.dm` are the four
    Darkest Dungeon floors and the town invasion, which are DSON and take the
    binary path below.

    Three differences from a live save, all of them shallow:

    * the map hangs off `data.map`, not `map`;
    * `areas` is directly under it -- there is no `static_dynamic` split,
      because nothing has been played yet, so the geometry is simply present
      on each tile as `mappos` instead of hiding in a nested blob;
    * a tile spells its curio `cur` rather than `curio_prop`, and writes
      0xCCCCCCCC where a live save omits the key (see `EMPTY_HASHES`).

    The first two are handled here; the third is in `_read_tiles` and
    `resolve_name`, because a DSON map can carry the sentinel too.
    """
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            raw = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    m = raw.get("data", raw)
    m = m.get("map") if isinstance(m, dict) else None
    if not isinstance(m, dict) or not isinstance(m.get("areas"), dict):
        return None

    areas: dict[str, Area] = {}
    for name, area in m["areas"].items():
        if not isinstance(area, dict):
            continue
        geometry: dict[int, tuple[float, float]] = {}
        for key, tile in (area.get("tiles") or {}).items():
            match = _TILE_RE.match(key)
            pos = tile.get("mappos") if isinstance(tile, dict) else None
            if match is None or not isinstance(pos, dict):
                continue
            x, y = _as_float(pos.get("x")), _as_float(pos.get("y"))
            if x is not None and y is not None:
                geometry[int(match.group(1))] = (x, y)
        areas[name] = Area(
            id=name,
            is_room=name.startswith("roo"),
            knowledge=int(area.get("knowledge") or KNOWLEDGE_UNKNOWN),
            tiles=_read_tiles(area, geometry),
            reversed_=bool(area.get("reversed")),
        )
    return DungeonMap(
        entrance_id=_unpack_area_id(m.get("entrance_id")),
        final_room_id=_unpack_area_id(m.get("final_room_id")),
        areas=areas,
    )


def read_dungeon_map(map_file: str | Path) -> Optional[DungeonMap]:
    """Decode one `persist.map.json`. None when there is no map (in town).

    Never call this on the live save directly — copy first, exactly as
    `live_state.py` and `save_watch.py` do.

    Also reads the two shipped forms: the plain-JSON starting save (the Old
    Road) and the binary `maps/*.dm` floors, which are ordinary DSON and need
    no special case at all.
    """
    path = Path(map_file)
    if not path.exists():
        return None
    with open(path, "rb") as fh:
        head = fh.read(1)
    if head == b"{":
        return _read_plain_json(path)
    root = parse_file(path)
    m = root.get("map") if root is not None else None
    if m is None:
        return None
    holder = m.get("static_dynamic")
    areas_field = holder.get("areas") if holder is not None else None
    if areas_field is None:
        return None

    geometry = _read_geometry(m)
    areas: dict[str, Area] = {}
    for area in (areas_field.children or []):
        as_dict = area.to_dict()
        areas[area.name] = Area(
            id=area.name,
            is_room=area.name.startswith("roo"),
            knowledge=int(as_dict.get("knowledge") or KNOWLEDGE_UNKNOWN),
            tiles=_read_tiles(as_dict, geometry.get(area.name)),
            reversed_=bool(as_dict.get("reversed")),
        )

    return DungeonMap(
        entrance_id=_unpack_area_id(m.get("entrance_id").value if m.get("entrance_id") else None),
        final_room_id=_unpack_area_id(m.get("final_room_id").value if m.get("final_room_id") else None),
        areas=areas,
    )


def _fmt(items: Iterable[tuple[str, Tile]]) -> str:
    grouped: dict[str, list[str]] = {}
    for aid, tile in items:
        grouped.setdefault(aid, []).append(tile.describe())
    return "  ".join(f"{aid}[{', '.join(v)}]" for aid, v in sorted(grouped.items())) or "none"


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("map_file", nargs="?", help="a persist.map.json (a COPY, never the live one)")
    ap.add_argument("--route-to", default=None, metavar="ROOM",
                    help="print the shortest route from the entrance to this room id")
    args = ap.parse_args()

    if args.map_file is None:
        ap.error("pass a persist.map.json path")
    dm = read_dungeon_map(args.map_file)
    if dm is None:
        print("no dungeon map in that file (in town?)")
        return

    adj = dm.graph()
    print(f"entrance={dm.entrance_id}  goal_room={dm.final_room_id or 'not yet known'}  "
          f"rooms={len(dm.rooms)}  corridors={len(dm.corridors)}  "
          f"explored={100 * dm.explored_fraction():.0f}% of tiles")

    print("\nlayout:")
    for room in sorted(dm.rooms):
        area = dm.areas[room]
        links = ", ".join(f"{n} via {c}" for n, c in sorted(adj.get(room, {}).items()))
        contents = "  ".join(t.describe() for t in area.tiles if t.is_active) or "-"
        print(f"  {room:6s} {KNOWLEDGE_NAMES.get(area.knowledge, '?'):8s} {contents:34s} -> {links}")

    print(f"\nbattles left:   {_fmt(dm.battles())}")
    print(f"curios left:    {_fmt(dm.curios())}")
    print(f"treasures left: {_fmt(dm.treasures())}")
    print(f"traps armed:    {_fmt(dm.traps())}")
    print(f"obstacles:      {_fmt(dm.obstacles())}")

    unexplored = [a.id for a in dm.unexplored()]
    print(f"unexplored:     {' '.join(sorted(unexplored)) or 'none'}")

    if args.route_to:
        route = dm.path(dm.entrance_id or "", args.route_to)
        print(f"\nroute {dm.entrance_id} -> {args.route_to}: "
              f"{' > '.join(route) if route else 'no path'}")


if __name__ == "__main__":
    main()
