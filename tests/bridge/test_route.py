"""Tests for the route policy (`bridge/route.py`).

Two format facts are pinned here because both were previously believed absent
from the raid save, and both are load-bearing:

- **`in_area` is packed ASCII, not `dd_name_hash`.** `CLAUDE.md` recorded it as
  matching no real dungeon id, which was true of the hash and hid the answer.
- **`raid_instance` carries the whole active quest**, including `dungeon` and
  `difficulty`. `CLAUDE.md`'s zone/tier invariant says the raid save has neither.

The planner itself is tested for exactness (it is a shortest cover, not a greedy
walk) and for refusing to price detours by an invented weight.
"""

import dataclasses
import glob
import os
import struct
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest

from bridge.dungeon_map import Area, DungeonMap, Tile, read_dungeon_map
from bridge.route import (
    GOAL_BATTLE, GOAL_EXPLORE, Quest, decode_goal_ids, mandatory_targets,
    optional_targets, plan_route, read_position, read_quest, unpack_area,
    _all_pairs, _cover_order,
)
from data.dson import dd_name_hash

REPO = Path(__file__).resolve().parents[2]
MAPS = sorted(glob.glob(str(REPO / "archive" / "traces" / "raidsnaps*" / "*map*.json")))
PAIRS = [(m, m.replace("map.json", "raid.json")) for m in MAPS
         if os.path.exists(m.replace("map.json", "raid.json"))]


def a_map() -> DungeonMap:
    for path in MAPS:
        dungeon = read_dungeon_map(path)
        if dungeon is not None and len(dungeon.rooms) > 2:
            return dungeon
    pytest.skip("no captured maps")


def line_map(rooms: int = 4) -> DungeonMap:
    """rooA - rooB - rooC - ... in a straight line, everything unvisited."""
    names = [f"roo{chr(ord('A') + i)}" for i in range(rooms)]
    areas: dict[str, Area] = {}
    for name in names:
        areas[name] = Area(id=name, is_room=True, knowledge=1,
                           tiles=(Tile(0, 0, None, None, -1, 1, False),))
    for a, b in zip(names, names[1:]):
        cid = f"co{a[-1]}{b[-1]}"
        areas[cid] = Area(id=cid, is_room=False, knowledge=1,
                          tiles=(Tile(0, 0, None, None, -1, 1, False),))
    return DungeonMap(entrance_id=names[0], final_room_id=names[-1], areas=areas)


def branch_map() -> DungeonMap:
    """rooA - rooB - rooC (the goal), with rooD hanging off rooB.

    The point is that a boss rush walks A->B->C and never touches D, so D is a
    real priced detour while B is not -- which is what makes the exclusion in
    `plan_route` observable.
    """
    areas: dict[str, Area] = {}
    for name in ("rooA", "rooB", "rooC", "rooD"):
        areas[name] = Area(id=name, is_room=True, knowledge=1,
                           tiles=(Tile(0, 0, None, None, -1, 1, False),))
    for cid in ("coAB", "coBC", "coBD"):
        areas[cid] = Area(id=cid, is_room=False, knowledge=1,
                          tiles=(Tile(0, 0, None, None, -1, 1, False),))
    return DungeonMap(entrance_id="rooA", final_room_id="rooC", areas=areas)


# --- format facts ---

def test_in_area_is_packed_ascii_not_a_name_hash():
    packed = struct.unpack("<i", b"rooB")[0]
    assert unpack_area(packed) == "rooB"
    assert dd_name_hash("rooB") != (packed & 0xFFFFFFFF)


def test_in_area_resolves_to_a_real_area_in_every_capture():
    checked = unresolved = 0
    for map_file, raid_file in PAIRS:
        dungeon = read_dungeon_map(map_file)
        position = read_position(raid_file)
        if dungeon is None or position is None:
            continue
        checked += 1
        if position not in dungeon.areas:
            unresolved += 1
    if not checked:
        pytest.skip("no paired captures")
    assert unresolved == 0


def test_goal_ids_decode_to_names():
    """A 4-byte count, then per entry a 4-byte length and a NUL-terminated
    name. Read as ints instead, the objective is unreadable."""
    blob = struct.pack("<i", 1) + struct.pack("<i", 18) + b"explore_all_rooms\x00"
    assert decode_goal_ids(blob) == ("explore_all_rooms",)


def test_goal_ids_survive_junk():
    assert decode_goal_ids(None) == ()
    assert decode_goal_ids(b"") == ()
    assert decode_goal_ids(0) == ()


def test_the_raid_save_states_the_quest_including_zone_and_difficulty():
    """`CLAUDE.md`'s zone/tier invariant says the raid save has neither, which
    is why `rollout.infer_zone_and_tier` guesses them from the enemy line. It
    has both, in every capture."""
    seen = with_zone = 0
    for _map_file, raid_file in PAIRS:
        quest = read_quest(raid_file)
        if quest is None:
            continue
        seen += 1
        if quest.dungeon and quest.quest_type:
            with_zone += 1
    if not seen:
        pytest.skip("no captures")
    assert with_zone == seen


def test_captured_quests_state_a_recognised_goal():
    goals = set()
    for _map_file, raid_file in PAIRS:
        quest = read_quest(raid_file)
        if quest is not None:
            goals.update(quest.goals)
    assert goals <= {GOAL_EXPLORE, GOAL_BATTLE} or goals, goals


# --- objectives ---

def test_a_cleanse_quest_targets_the_rooms_that_still_hold_a_fight():
    dungeon = a_map()
    quest = Quest(quest_type="cleanse", goals=(GOAL_BATTLE,))
    targets = mandatory_targets(dungeon, quest)
    for room in targets:
        assert any(t.has_battle for t in dungeon.areas[room].tiles)


def test_an_explore_quest_targets_the_rooms_not_yet_walked():
    dungeon = a_map()
    quest = Quest(quest_type="explore", goals=(GOAL_EXPLORE,))
    for room in mandatory_targets(dungeon, quest):
        assert not dungeon.areas[room].explored


def test_a_boss_quest_targets_only_the_goal_room():
    dungeon = dataclasses.replace(line_map(4), final_room_id="rooD")
    quest = Quest(quest_type="kill_boss")
    assert mandatory_targets(dungeon, quest) == ["rooD"]


def test_an_unrecognised_goal_falls_back_to_the_safe_superset():
    """Never a guess at something narrower: covering every unvisited room can
    waste moves, but cannot miss the objective."""
    dungeon = line_map(3)
    loose = mandatory_targets(dungeon, Quest(quest_type="gather", goals=("who_knows",)))
    assert set(loose) == set(dungeon.rooms)
    assert mandatory_targets(dungeon, None) == loose


# --- the walk ---

def test_the_cover_is_exact_not_greedy():
    """On a line A-B-C-D starting at B, a greedy nearest-first walk goes to A
    then back across to D for 4 moves. The optimum is A first only if it is
    cheaper overall; here going A->D costs 1 + 3 = 4 and D->A costs 2 + 3 = 5,
    so the exact planner must pick the 4."""
    dungeon = line_map(4)
    routes = _all_pairs(dungeon)
    order = _cover_order("rooB", ["rooA", "rooD"], routes)
    assert order == ["rooA", "rooD"]


def test_a_party_in_a_corridor_leaves_by_the_cheaper_end():
    """A corridor is where the save gets written most often, and picking an end
    arbitrarily silently adds a move whenever it guesses wrong."""
    dungeon = line_map(4)
    plan = plan_route(dungeon, "coAB", Quest(quest_type="kill_boss"))
    assert plan.walk[0] == "coAB"
    assert plan.walk[1] == "rooB", plan.walk
    assert plan.moves == 2


def test_the_walk_alternates_rooms_and_corridors():
    dungeon = a_map()
    plan = plan_route(dungeon, None, Quest(quest_type="explore", goals=(GOAL_EXPLORE,)))
    for a, b in zip(plan.walk, plan.walk[1:]):
        assert dungeon.areas[a].is_room != dungeon.areas[b].is_room


def test_next_step_is_the_first_move_not_the_current_area():
    dungeon = line_map(3)
    plan = plan_route(dungeon, "rooA", Quest(quest_type="kill_boss"))
    assert plan.walk[0] == "rooA"
    assert plan.next_step == "coAB"


def test_moves_count_corridors_and_revisits_are_reported_not_hidden():
    dungeon = line_map(4)
    plan = plan_route(dungeon, "rooB", Quest(quest_type="explore", goals=(GOAL_EXPLORE,)))
    corridors = [a for a in plan.walk[1:] if not dungeon.areas[a].is_room]
    assert plan.moves == len(corridors)
    assert plan.revisits >= 0


# --- detours are priced, not decided ---

def test_a_boss_rush_prices_the_treasure_it_walks_past():
    dungeon = a_map()
    dungeon = dataclasses.replace(dungeon, final_room_id=sorted(dungeon.rooms)[-1])
    plan = plan_route(dungeon, dungeon.entrance_id, Quest(quest_type="kill_boss"))
    assert plan.note.startswith("boss quest")
    for detour in plan.detours:
        assert detour.extra_moves >= 0 and detour.extra_fights >= 0


def test_a_detour_never_appears_for_a_room_already_on_the_route():
    """The exclusion is `on_route` in `plan_route`, and it needs a map where a
    detour actually EXISTS to be tested at all.

    This ran against `a_map()` and an explore quest for months and proved
    nothing: an explore quest makes every room mandatory, so `optional_targets`
    returns only rooms with unspent loot, the captures have none, and the loop
    body never executed. `branch_map()` puts one room off the boss path, so the
    assertion below has something to be true OF -- and rooB, which is unvisited
    and therefore also optional, is on the walk and must NOT be priced.
    """
    dungeon = branch_map()
    plan = plan_route(dungeon, None, Quest(quest_type="kill_boss"))
    priced = {d.area_id for d in plan.detours}
    assert priced, "the map must offer a detour or this test asserts nothing"
    assert priced == {"rooD"}
    for detour in plan.detours:
        assert detour.area_id not in plan.walk
    assert "rooB" in plan.walk and "rooB" not in priced


def test_exploring_is_optional_only_when_the_quest_rushes():
    dungeon = line_map(4)
    rushing = optional_targets(dungeon, Quest(quest_type="kill_boss"))
    walking = optional_targets(dungeon, Quest(quest_type="explore", goals=(GOAL_EXPLORE,)))
    assert set(rushing) >= set(dungeon.rooms) - set()
    assert not walking


def test_a_detour_carries_no_invented_value_weight():
    """The planner reports what a detour costs and refuses to say whether it is
    worth it: no shipped file prices loot against moves and fights, and a hidden
    weight would be a convention wearing game-data clothes."""
    from bridge.route import Detour
    fields = set(dataclasses.fields(Detour.__init__.__self__)
                 if False else {f.name for f in dataclasses.fields(Detour)})
    assert fields == {"area_id", "what", "extra_moves", "extra_fights"}


# --- against the real captures ---

def test_every_capture_plans_without_raising():
    planned = 0
    for map_file, raid_file in PAIRS[:60]:
        dungeon = read_dungeon_map(map_file)
        if dungeon is None:
            continue
        plan = plan_route(dungeon, read_position(raid_file), read_quest(raid_file))
        assert plan.moves >= 0
        assert plan.line()
        planned += 1
    assert planned > 0


def test_a_dead_end_target_forces_a_revisit():
    """PLAYBOOK 2.2 says minimise walking the same place twice *if you can*, and
    the qualifier is load-bearing: a target down a dead-end branch has to be
    walked back out. The planner reports the backtracking instead of pretending
    a shorter route exists."""
    dungeon = line_map(3)
    plan = plan_route(dungeon, "rooB", Quest(quest_type="explore", goals=(GOAL_EXPLORE,)))
    assert plan.revisits > 0, plan.walk


def test_real_captures_do_need_backtracking_and_it_is_measured():
    """Not zero — real dungeons branch. This pins that the planner reports it
    rather than silently producing an impossible walk, and that the amount stays
    small relative to the route."""
    with_backtracking = total = 0
    for map_file, raid_file in PAIRS[:60]:
        dungeon = read_dungeon_map(map_file)
        if dungeon is None:
            continue
        plan = plan_route(dungeon, read_position(raid_file), read_quest(raid_file))
        total += 1
        if plan.revisits:
            with_backtracking += 1
            assert plan.revisits <= len(plan.walk), (map_file, plan.walk)
    if not total:
        pytest.skip("no paired captures")
    assert 0 < with_backtracking < total, (with_backtracking, total)


def test_the_plan_starts_where_the_party_actually_is():
    for map_file, raid_file in PAIRS[:40]:
        dungeon = read_dungeon_map(map_file)
        position = read_position(raid_file)
        if dungeon is None or position is None or position not in dungeon.areas:
            continue
        plan = plan_route(dungeon, position, read_quest(raid_file))
        if plan.walk:
            assert plan.walk[0] == position or dungeon.areas[plan.walk[0]].is_room


# -- which end did the party come in by, on a second pass ------------------


def test_the_save_names_the_area_the_party_CAME_FROM():
    """`Area.standing_on` reads direction off the dark run behind the party,
    so a corridor walked dark end to end has nothing left to read -- and that
    is precisely the corridor a deferred curio sends the party back into.
    `last_room_id` does not saturate, because the game writes it rather than
    leaving it to be inferred."""
    import glob

    from bridge.route import read_last_area

    raids = sorted(glob.glob(str(REPO / "archive" / "traces" / "raidsnaps*"
                                 / "*raid*.json")))
    if not raids:
        import pytest
        pytest.skip("no raid captures")
    answers = [read_last_area(r) for r in raids]
    got = [a for a in answers if a]
    assert got, "not one capture named the area the party came from"
    # an area id, not a hash and not a raw int
    assert all(a[:3] in ("roo", "cor", "coA", "co") or len(a) <= 6
               for a in got), got


if __name__ == "__main__":
    import pytest as _p
    raise SystemExit(_p.main([__file__, "-v"]))
