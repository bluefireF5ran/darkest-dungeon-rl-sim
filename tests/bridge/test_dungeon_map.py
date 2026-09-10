"""Tests for the dungeon-map reader (`bridge/dungeon_map.py`).

These pin the format facts that were expensive to recover and would be silent if
they regressed: the graph living in the area *names*, `entrance_id` being packed
ASCII rather than `dd_name_hash` (the file uses both conventions), the unsigned
decode that a signed read gets wrong for half the id space, and `content` being
the live state while `curio_prop` is the identity — read the wrong one and a
consumed crate is still advertised as loot.

Fixtures are the real captured maps in `archive/traces/raidsnaps*/`.
"""

import glob
import struct
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests._captures import dungeon_map, map_paths

import pytest

from bridge.dungeon_map import (
    CONTENT_BATTLE, CONTENT_CURIO, CONTENT_EMPTY, CONTENT_GUARDED_CURIO,
    CONTENT_GUARDED_TREASURE, CONTENT_HUNGER, CONTENT_OBSTACLE, CONTENT_TRAP,
    CONTENT_TREASURE,
    KNOWLEDGE_SCOUTED, KNOWLEDGE_UNKNOWN, KNOWLEDGE_VISITED,
    Area, DungeonMap, Tile, read_dungeon_map, resolve_name, _unpack_area_id,
)
from data.dson import dd_name_hash

from data.paths import GAME_DIR

REPO = Path(__file__).resolve().parents[2]
SNAPSHOTS = map_paths()


def a_map() -> DungeonMap:
    for path in SNAPSHOTS:
        dm = dungeon_map(path)
        if dm is not None and dm.rooms:
            return dm
    pytest.skip("no captured map snapshots available")


# --- the graph is in the names ---

def test_corridor_name_names_the_two_rooms_it_joins():
    corridor = Area(id="coAB", is_room=False, knowledge=1, tiles=())
    assert corridor.rooms == ("rooA", "rooB")


def test_a_room_joins_nothing_by_name():
    assert Area(id="rooA", is_room=True, knowledge=1, tiles=()).rooms == ()


def test_graph_is_symmetric_and_every_corridor_becomes_an_edge():
    dm = a_map()
    adj = dm.graph()
    for corridor in dm.corridors.values():
        pair = corridor.rooms
        if len(pair) != 2:
            continue
        a, b = pair
        assert adj[a][b] == corridor.id
        assert adj[b][a] == corridor.id


def test_path_alternates_room_and_corridor():
    dm = a_map()
    adj = dm.graph()
    start = dm.entrance_id or next(iter(dm.rooms))
    reachable = [r for r in dm.rooms if r != start and dm.path(start, r)]
    if not reachable:
        pytest.skip("captured map has no second reachable room")
    route = dm.path(start, reachable[0])
    assert route[0] == start and route[-1] == reachable[0]
    for i, area_id in enumerate(route):
        assert dm.areas[area_id].is_room is (i % 2 == 0)


def test_path_to_self_is_trivial_and_to_nowhere_is_none():
    dm = a_map()
    start = dm.entrance_id or next(iter(dm.rooms))
    assert dm.path(start, start) == [start]
    assert dm.path(start, "rooZZZ") is None


# --- ids: two conventions in one file ---

def test_entrance_id_is_packed_ascii_not_a_name_hash():
    """The format stores SOME ids as dd_name_hash and this one as raw bytes.
    Inverting with the wrong one silently yields no entrance at all."""
    packed = struct.unpack("<i", b"rooA")[0]
    assert _unpack_area_id(packed) == "rooA"
    assert dd_name_hash("rooA") != (packed & 0xFFFFFFFF)


def test_final_room_id_reads_none_until_the_goal_is_known():
    assert _unpack_area_id(struct.unpack("<i", b"none")[0]) is None


def test_curio_and_trap_ids_invert_unsigned():
    """DSON decodes 4-byte fields signed; dd_name_hash is unsigned mod 2**32.
    poison_cloud arrives negative, and a signed lookup finds nothing."""
    assert resolve_name(-702901882) == "poison_cloud"
    assert resolve_name(1855193228) == "blade_wheel"
    assert resolve_name(dd_name_hash("crate")) == "crate"


def test_an_unknown_id_is_kept_not_lost():
    assert resolve_name(1).startswith("#")


def test_no_id_is_falsy_not_an_error():
    assert resolve_name(0) is None
    assert resolve_name(None) is None


# --- content is the live state, curio_prop is the identity ---

def test_a_consumed_curio_keeps_its_identity_but_is_no_longer_active():
    spent = Tile(index=0, content=CONTENT_EMPTY, curio_id="crate", trap_id=None,
                 mash_index=-1, knowledge=KNOWLEDGE_VISITED, lit=True)
    assert spent.curio_id == "crate"
    assert not spent.is_active


def test_consumed_curios_are_excluded_from_what_is_left():
    dm = a_map()
    for _area_id, tile in dm.curios():
        assert tile.is_active


def test_every_captured_map_has_consumed_curios_to_exclude():
    """Guards the test above from passing vacuously — if a run never spent a
    curio, filtering on `content` would be untested."""
    spent = 0
    for path in SNAPSHOTS:
        dm = dungeon_map(path)
        if dm is None:
            continue
        spent += sum(1 for a in dm.areas.values() for t in a.tiles
                     if t.curio_id and not t.is_active)
    assert spent > 0


def test_a_guarded_room_counts_as_a_battle():
    """The guard IS an encounter, so a treasure room must show up in
    `battles()` as well as in `treasures()`."""
    guarded = Tile(index=0, content=CONTENT_GUARDED_TREASURE, curio_id="locked_strongbox",
                   trap_id=None, mash_index=4, knowledge=KNOWLEDGE_SCOUTED, lit=False)
    assert guarded.has_battle


def test_mash_index_zero_is_a_real_index():
    """`mash_index` is 0 on 59 captured battle tiles. Any truthiness test on
    this field (`t.get('mash_index') or -1`) silently reclassifies them, which
    is exactly how a survey of this corpus invented a category of mash-less
    battles that does not exist."""
    tile = Tile(index=0, content=CONTENT_BATTLE, curio_id=None, trap_id=None,
                mash_index=0, knowledge=KNOWLEDGE_UNKNOWN, lit=False)
    assert tile.mash_index == 0 and tile.has_battle

    at_zero = sum(1 for p in SNAPSHOTS
                  for dm in [dungeon_map(p)] if dm
                  for _a, t in dm.battles() if t.mash_index == 0)
    assert at_zero > 0


def test_every_battle_tile_in_the_corpus_carries_a_mash_index():
    """Keeps `has_battle` honest: it keys on `content`, and this asserts the
    two criteria have never disagreed on real data."""
    for path in SNAPSHOTS:
        dm = dungeon_map(path)
        if dm is None:
            continue
        for _area_id, tile in dm.battles():
            assert tile.mash_index >= 0


# --- the content enum, against every captured map ---

def test_content_codes_stay_within_the_decoded_enum():
    seen = set()
    for path in SNAPSHOTS:
        dm = dungeon_map(path)
        if dm is None:
            continue
        for area in dm.areas.values():
            for tile in area.tiles:
                seen.add(tile.content)
                assert not tile.kind.startswith("unknown:"), \
                    f"undecoded tile content {tile.content} in {path}"
    assert {CONTENT_EMPTY, CONTENT_BATTLE, CONTENT_CURIO} <= seen


def test_hunger_and_obstacle_are_corridor_only_and_treasure_is_room_only():
    """The evidence that pinned 4=obstacle vs 8=hunger also constrains where
    each may appear. A room-side hunger tile would mean the codes are swapped."""
    for path in SNAPSHOTS:
        dm = dungeon_map(path)
        if dm is None:
            continue
        for area in dm.areas.values():
            for tile in area.tiles:
                if tile.content in (CONTENT_HUNGER, CONTENT_OBSTACLE, CONTENT_TRAP):
                    assert not area.is_room, f"{tile.kind} in room {area.id}"
                if tile.content in (CONTENT_GUARDED_CURIO, CONTENT_TREASURE,
                                    CONTENT_GUARDED_TREASURE):
                    assert area.is_room, f"{tile.kind} in corridor {area.id}"


def test_the_rare_unguarded_treasure_code_is_decoded():
    """Code 9 appears 6 times in the whole corpus against 361 of code 10, and a
    survey of the first 120 snapshots missed it. It is a room treasure with no
    guarding encounter."""
    found = [(p, t) for p in SNAPSHOTS
             for dm in [dungeon_map(p)] if dm
             for _a, t in dm.treasures() if t.content == CONTENT_TREASURE]
    assert found, "no unguarded treasure in the corpus — has it moved?"
    _path, tile = found[0]
    assert tile.kind == "treasure"
    assert tile.curio_id and not tile.has_battle


def test_hunger_is_never_absent_from_a_map_but_obstacles_can_be():
    """The discriminator itself: every `hallway_hunger` range in
    map_generator.darkest starts at 1, while most `hallway_obstacle` ranges
    start at 0. Swap the two codes and this inverts."""
    maps_without_hunger = maps_without_obstacle = 0
    checked = 0
    for path in SNAPSHOTS:
        dm = dungeon_map(path)
        if dm is None or not dm.rooms:
            continue
        checked += 1
        contents = [t.content for a in dm.areas.values() for t in a.tiles]
        maps_without_hunger += CONTENT_HUNGER not in contents
        maps_without_obstacle += CONTENT_OBSTACLE not in contents
    if checked == 0:
        pytest.skip("no captured maps")
    assert maps_without_hunger == 0
    assert maps_without_obstacle > 0


# --- knowledge ---

def test_knowledge_is_a_tri_state_and_ordered():
    assert KNOWLEDGE_UNKNOWN < KNOWLEDGE_SCOUTED < KNOWLEDGE_VISITED
    scouted = Area(id="rooA", is_room=True, knowledge=KNOWLEDGE_SCOUTED, tiles=())
    assert scouted.known and not scouted.explored
    visited = Area(id="rooA", is_room=True, knowledge=KNOWLEDGE_VISITED, tiles=())
    assert visited.known and visited.explored


def test_the_entrance_is_always_already_visited_once_the_party_moved():
    """A map captured mid-raid must show the entrance walked; if it reads
    unknown, the knowledge codes are inverted."""
    walked = 0
    for path in SNAPSHOTS:
        dm = dungeon_map(path)
        if dm is None or not dm.entrance_id:
            continue
        entrance = dm.areas.get(dm.entrance_id)
        if entrance is not None and entrance.explored:
            walked += 1
    assert walked > 0


def test_explored_fraction_is_a_fraction():
    for path in SNAPSHOTS[:20]:
        dm = dungeon_map(path)
        if dm is None:
            continue
        assert 0.0 <= dm.explored_fraction() <= 1.0


# --- reading ---

def test_every_captured_snapshot_decodes():
    if not SNAPSHOTS:
        pytest.skip("no captured map snapshots available")
    decoded = sum(dungeon_map(p) is not None for p in SNAPSHOTS)
    assert decoded == len(SNAPSHOTS)


def test_a_missing_file_is_none_not_an_exception():
    assert dungeon_map(REPO / "no" / "such" / "persist.map.json") is None


def test_tiles_come_back_in_order():
    dm = a_map()
    for area in dm.areas.values():
        indices = [t.index for t in area.tiles]
        assert indices == sorted(indices)


def test_rooms_hold_one_tile_and_corridors_hold_several():
    dm = a_map()
    assert all(len(a.tiles) == 1 for a in dm.rooms.values())
    assert all(len(a.tiles) > 1 for a in dm.corridors.values())


def test_the_dark_run_and_areatile_agree_in_every_captured_corridor():
    """The two position signals are independent and must never disagree.

    `raid.areatile` is the game's own count of tiles walked from the end the
    party entered by; `Area.standing_on` reads the same distance off the dark
    run the party leaves behind it, and adds the direction `areatile` lacks.
    Over the whole capture corpus they agree **223 of 223** times, with 45
    observations where the dark run cannot say (a corridor already walked
    end-to-end carries no direction, and `areatile` alone still counts).

    This is the test that would have caught the signal it replaced. The
    per-tile `knowledge` run is not a position -- it marks tiles as REVEALED,
    so it can jump two at a time and can read identically at two different
    positions -- and grading it the same way gives 172 right, **51 wrong**,
    45 silent.
    """
    from tests._captures import decoded_maps
    from bridge.route import read_position, read_area_tile
    agree = disagree = silent = 0
    for path, dungeon in decoded_maps():
        path = Path(path)
        raid = path.with_name(path.name.replace("map", "raid"))
        if not raid.exists():
            continue
        tile = read_area_tile(raid)
        area = dungeon.areas.get(read_position(raid) or "")
        if tile is None or area is None or area.is_room:
            continue
        where = area.standing_on
        if where is None:
            silent += 1
            continue
        absolute, from_front = where
        expect = tile if from_front else len(area.tiles) - 1 - tile
        if expect == absolute:
            agree += 1
        else:
            disagree += 1
    assert disagree == 0, f"{disagree} corridors where the two signals differ"
    assert agree > 200, f"only {agree} corridors graded - the corpus moved"


def test_the_dark_run_refuses_rather_than_guessing():
    """Both ends of the refusal. A corridor not entered is all lit and there
    is no run; one already walked end to end is all dark and the run no longer
    says which end it grew from. Returning a tile for either would be
    inventing a position, which is what steering wanted a signal to stop."""
    from bridge.dungeon_map import Area, Tile

    def corridor(lit_flags):
        return Area(id="coAB", is_room=False, knowledge=3,
                    tiles=tuple(Tile(index=i, content=0, curio_id=None,
                                     trap_id=None, mash_index=-1,
                                     knowledge=3, lit=x)
                                for i, x in enumerate(lit_flags)))

    assert corridor([True] * 5).standing_on is None, "not entered"
    assert corridor([False] * 5).standing_on is None, "walked dark end to end"
    assert corridor([True, False, False, True, True]).standing_on is None, \
        "a dark patch in the middle is not a walk"
    assert corridor([False, False, True, True, True]).standing_on == (1, True)
    assert corridor([True, True, True, False, False]).standing_on == (3, False)


def test_the_shipped_starting_save_decodes_as_the_OLD_ROAD():
    """`scripts/starting_save/persist.map.json` is plain JSON, not DSON, and
    it is the tutorial map. Reading it makes the Old Road a fixture that never
    needs the game running -- which matters because the tutorial is where the
    bridge starts every campaign and there is no capture of it anywhere.

    Its shape is Fran's description exactly: two rooms, one hallway between,
    a battle and a curio in the hallway and a guarded curio in the far room.
    """
    import pytest


    path = (GAME_DIR / "scripts" / "starting_save" / "persist.map.json")
    if not path.exists():
        pytest.skip("the game is not installed at the hardcoded path")
    dungeon = read_dungeon_map(path)
    assert dungeon is not None
    assert dungeon.entrance_id == "rooA"
    assert dungeon.graph() == {"rooA": {"rooB": "coAB"},
                               "rooB": {"rooA": "coAB"}}
    active = {aid: [(t.index, t.content, t.curio_id)
                    for t in area.tiles if t.is_active]
              for aid, area in dungeon.areas.items()}
    assert active["coAB"] == [(2, 1, None), (4, 7, "travellers_tent_tutorial")]
    assert active["rooB"] == [(0, 6, "bandits_trapped_chest")]


def test_the_empty_hash_sentinel_is_not_a_curio():
    """0xCCCCCCCC is MSVC's uninitialised fill, not an id. A live save omits
    the key; the JSON template writes the sentinel, and without this every
    tile in it reports a trap called `#3435973836`."""
    from bridge.dungeon_map import resolve_name

    assert resolve_name(0xCCCCCCCC) is None
    assert resolve_name(0xFFFFFFFF) is None
    assert resolve_name(None) is None
    # ...and a real hash still inverts.
    assert resolve_name(908094902) == "bandits_trapped_chest"


def test_a_corridor_names_its_real_ENDS_and_the_name_split_does_not():
    """`Area.rooms` splits `coAB` correctly and reads a generated `corE` as
    `roo` + `r` + `E`, answering `rooE` -- a room that exists and is NOT an end
    of `corE`. `graph()` has taken connectivity from geometry since the
    phantom-hub bug; `corridor_rooms` is that same answer made reachable,
    because `bridge/route.py` was still using the name split to decide which
    way to leave a corridor.
    """
    import pytest


    path = GAME_DIR / "maps" / "tutorial_crypts.dm"
    if not path.exists():
        pytest.skip("the game is not installed at the hardcoded path")
    dungeon = read_dungeon_map(path)
    assert dungeon is not None
    assert dungeon.areas["corE"].rooms == ("roor", "rooE")     # the wrong one
    assert dungeon.corridor_rooms("corE") == ("rooC", "rooF")  # the real one
    # every corridor's derived ends agree with the graph it builds
    for cid in dungeon.corridors:
        a, b = dungeon.corridor_rooms(cid)
        assert dungeon.graph()[a][b] == cid


if __name__ == "__main__":
    import pytest as _p
    raise SystemExit(_p.main([__file__, "-v"]))


# --- within-area position: the field nobody read ---
#
# `bridge/act.py` walked blind between one area boundary and the next, on the
# stated grounds that "the save records no within-area position to steer by".
# It does. Per-tile `knowledge` rises to VISITED behind the party, so the
# visited tiles are a run growing from the end it came in by.

def test_a_corridor_reports_how_far_along_it_the_party_is():
    from bridge.dungeon_map import Area, Tile, KNOWLEDGE_UNKNOWN, KNOWLEDGE_VISITED

    def area(marks):
        return Area(id="coAB", is_room=False, knowledge=KNOWLEDGE_VISITED,
                    tiles=tuple(Tile(index=i, content=0, curio_id=None,
                                     trap_id=None, mash_index=-1,
                                     knowledge=(KNOWLEDGE_VISITED if m
                                                else KNOWLEDGE_UNKNOWN),
                                     lit=not m)
                                for i, m in enumerate(marks)))

    w = area([1, 1, 1, 0, 0]).walked
    assert w.passed == 3 and w.total == 5 and w.from_front
    assert w.index == 2 and w.remaining == 2 and w.entry_index == 0

    # Entered from the other end: the run grows backwards, so the tile INDEX
    # counts down as the party advances and the entrance is the LAST tile.
    # It used to also claim the walk KEY flipped -- see `key_toward`, which
    # is where that reading was retired.
    w = area([0, 0, 1, 1, 1]).walked
    assert w.passed == 3 and not w.from_front
    assert w.index == 2 and w.entry_index == 4


def test_D_advances_and_A_backtracks_whichever_end_was_entered():
    """**A HALLWAY HAS NO MAP DIRECTION.** Fran, after watching a party cross
    a corridor the wrong way and come straight back: *"hallways have no
    directions, only left (backtrack) and right (advance)"*. DD draws the
    party entering at the LEFT of the hallway art however that corridor runs
    on the map, so `D` is always deeper and `A` is always back out the door.

    The tile ORDER is the save's own numbering and is a different question --
    it says which way the INDEX moves, which is what `entry_index` is for. A
    `Walk.heading` property used to conflate the two and hand back `A` for any
    party that entered at the far end, which walked whole corridors backwards:
    `coEF` live, and the `corH` oscillation before it.
    """
    from bridge.dungeon_map import Walk

    # Entered at tile 0, standing on tile 2 of 6.
    front = Walk(passed=3, total=6, from_front=True)
    assert front.entry_index == 0
    assert front.key_toward(5) == "D"          # deeper
    assert front.key_toward(0) == "A"          # back to the door
    assert front.key_toward(2) == "D"          # already there: not a reversal

    # Entered at tile 5, standing on tile 3. Deeper is now a LOWER index and
    # the key is still `D`.
    back = Walk(passed=3, total=6, from_front=False)
    assert back.index == 3 and back.entry_index == 5
    assert back.key_toward(0) == "D"
    assert back.key_toward(5) == "A"


def test_an_unentered_or_finished_corridor_reports_nothing():
    """Neither end says anything: an untouched corridor has no party in it,
    and a fully walked one no longer records which way they came."""
    from bridge.dungeon_map import Area, Tile, KNOWLEDGE_UNKNOWN, KNOWLEDGE_VISITED

    def area(k):
        return Area(id="coAB", is_room=False, knowledge=k,
                    tiles=tuple(Tile(index=i, content=0, curio_id=None,
                                     trap_id=None, mash_index=-1, knowledge=k,
                                     lit=True) for i in range(4)))
    assert area(KNOWLEDGE_UNKNOWN).walked is None
    assert area(KNOWLEDGE_VISITED).walked is None


def test_the_visited_run_is_contiguous_in_every_captured_corridor():
    """The measurement the whole thing rests on: 2517 corridor observations
    across the capture corpus, and not one where the visited tiles are
    discontiguous. If this ever fails, `walked` is reading a field that means
    something else and every walk built on it is steering by noise."""
    from tests._captures import decoded_maps
    seen = broken = 0
    for _path, dungeon in decoded_maps():
        for a in dungeon.areas.values():
            if a.is_room or len(a.tiles) < 2:
                continue
            seen += 1
            flags = "".join("1" if t.knowledge >= 3 else "0" for t in a.tiles)
            if flags.count("01") + flags.count("10") > 1:
                broken += 1
    if not seen:
        pytest.skip("no captured maps")
    assert broken == 0, f"{broken}/{seen} corridors had a discontiguous run"
    assert seen > 2000, f"only {seen} corridor observations - corpus shrank"


# ── the corridor NAME is only the endpoint pair in the tutorial ──

RUINS_SAVE = REPO / "archive" / "traces" / "ruins_map_paired" / "persist.map.json"


def _ruins():
    import pytest                                              # noqa: PLC0415
    if not RUINS_SAVE.exists():
        pytest.skip("no paired Ruins save")
    got = read_dungeon_map(RUINS_SAVE)
    if got is None:
        pytest.skip("Ruins save did not decode")
    return got


def test_a_generated_dungeon_does_not_route_through_a_phantom_room():
    """THE BUG: `corA` was read as `roo` + `r` + `A`.

    The Old Road ships `coAB`, whose name really is its endpoint pair, and
    `Area.rooms` splits it correctly. A generated dungeon NUMBERS its
    corridors -- `corA`..`corH` -- and the same split invents a room `roor`
    and hangs all eight corridors off it. That is a star through a phantom hub
    where the real map is a grid, and `path()` plans over it, so every route
    on every real raid was computed against a fabricated topology.

    It survived because the only map ever validated was the tutorial's, where
    the two readings agree.
    """
    d = _ruins()
    assert "roor" not in d.areas, "the save has no such room"
    graph = d.graph()
    assert "roor" not in graph, "the graph invented a room the save does not have"
    for room, neighbours in graph.items():
        for other in neighbours:
            assert other in d.areas, f"{room} -> {other}, which does not exist"


def test_the_generated_topology_is_the_one_the_coordinates_describe():
    """Derived from `mappos`, and checkable by hand.

    Rooms sit at unit (1,1) (8,1) (15,1) / (1,8) (8,8) (15,8) / (1,15) /
    (1,22); each corridor is six tiles running between two of them. So `corA`
    at (2,1)..(7,1) joins rooA (1,1) to rooB (8,1), and nothing else can.
    """
    d = _ruins()
    graph = d.graph()
    expected = {
        "rooA": {"rooB": "corA", "rooD": "corB"},
        "rooB": {"rooA": "corA", "rooC": "corC", "rooE": "corD"},
        "rooC": {"rooB": "corC", "rooF": "corE"},
        "rooD": {"rooA": "corB", "rooE": "corF", "rooG": "corG"},
        "rooE": {"rooB": "corD", "rooD": "corF"},
        "rooF": {"rooC": "corE"},
        "rooG": {"rooD": "corG", "rooH": "corH"},
        "rooH": {"rooG": "corH"},
    }
    assert graph == expected


def test_every_room_of_a_generated_dungeon_is_reachable_from_the_entrance():
    """The property that actually matters to `route.py`.

    A phantom hub makes everything trivially "reachable" through a room that
    is not there, so this is only meaningful alongside the test above: the
    graph must be connected AND contain no invented room.
    """
    d = _ruins()
    entrance = d.entrance_id
    assert entrance == "rooH"
    for room in d.rooms:
        route = d.path(entrance, room)
        assert route, f"{room} is unreachable from {entrance}"
        assert route[0] == entrance and route[-1] == room
        # alternating room / corridor, and every id real
        for step in route:
            assert step in d.areas


def test_the_tutorial_still_resolves_the_same_way():
    """The regression that mattered: geometry must AGREE with the name where
    the name was right. `coAB`'s ends sit one unit from rooA and rooB, so both
    readings give the same pair and the Old Road is untouched."""
    from bridge.dungeon_map import Area, DungeonMap, Tile        # noqa: PLC0415

    def tile(i, pos):
        return Tile(index=i, content=0, curio_id=None, trap_id=None,
                    mash_index=-1, knowledge=3, lit=True, mappos=pos)

    rooA = Area(id="rooA", is_room=True, knowledge=3,
                tiles=(tile(0, (-10.5, -7.0)),))
    rooB = Area(id="rooB", is_room=True, knowledge=3,
                tiles=(tile(0, (-3.5, -7.0)),))
    coAB = Area(id="coAB", is_room=False, knowledge=3,
                tiles=tuple(tile(i, (-9.5 + i, -7.0)) for i in range(6)))
    d = DungeonMap(areas={"rooA": rooA, "rooB": rooB, "coAB": coAB},
                   entrance_id="rooA", final_room_id="rooB")
    assert d.graph() == {"rooA": {"rooB": "coAB"}, "rooB": {"rooA": "coAB"}}


def test_a_corridor_with_no_coordinates_falls_back_to_its_name():
    """Geometry is preferred, not required. An area the party has not reached
    can arrive without `mappos`, and the name convention is still the best
    available answer there -- so the fallback has to stay."""
    from bridge.dungeon_map import Area, DungeonMap, Tile        # noqa: PLC0415

    def bare(i):
        return Tile(index=i, content=0, curio_id=None, trap_id=None,
                    mash_index=-1, knowledge=0, lit=False, mappos=None)

    rooA = Area(id="rooA", is_room=True, knowledge=0, tiles=(bare(0),))
    rooB = Area(id="rooB", is_room=True, knowledge=0, tiles=(bare(0),))
    coAB = Area(id="coAB", is_room=False, knowledge=0,
                tiles=tuple(bare(i) for i in range(6)))
    d = DungeonMap(areas={"rooA": rooA, "rooB": rooB, "coAB": coAB},
                   entrance_id="rooA", final_room_id="rooB")
    assert d.graph() == {"rooA": {"rooB": "coAB"}, "rooB": {"rooA": "coAB"}}
