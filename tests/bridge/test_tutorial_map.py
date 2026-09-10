"""The Old Road: the one dungeon in the game that cannot drift.

Every corridor fix so far — the room-vs-corridor refusal, the `W` door press,
the held `A`/`D` — came out of Fran narrating a stall mid-raid and it being
guessed at afterwards. The tutorial fixes that, because its layout is not
generated: `GAME_DIR/scripts/starting_save/persist.map.json` is hand-authored
and copied verbatim into a new save slot.

    rooA  ->  coAB t0 t1 t2 t3 t4 t5  ->  rooB
                    ^battle    ^curio        ^final room (the goal)

So a corridor regression becomes a failing assertion against a known walk
instead of an anecdote. These tests pin the walk, and fail loudly if a game
patch ever changes it — at which point the rig, not the bridge, is what broke.

The layout is fixed; the two fights are not. Both battle tiles carry a
`mash_index`, so the monsters are rolled while the rooms, the corridor length,
the curio and their order repeat exactly. That is the right split here: what
is under test is deterministic and the combat on top of it still varies.

**The template is not shaped like a live save.** A running `persist.map.json`
is DSON and puts the areas under `map.static_dynamic`; the template is plain
text JSON and puts everything under `data.map`. That is why `read_dungeon_map`
returns None on it, which is correct: it is the seed the game expands, not the
layout it plays. What matters here is that `parse_file` reads it at all —
`tools/dson.py` used to raise `bad magic` on every one of these files, and a
freshly created campaign holds nothing else until the game re-saves it.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from data.dson import dd_name_hash, is_complete, parse_file    # noqa: E402

from data.paths import GAME_DIR
TEMPLATE = GAME_DIR / "scripts" / "starting_save"

pytestmark = pytest.mark.skipif(not TEMPLATE.exists(),
                                reason="game not installed")

# The tutorial's own tile-content codes, read off the template. `content` is
# the same field `bridge/dungeon_map.Tile` decodes from a live save.
CONTENT_EMPTY, CONTENT_BATTLE = 0, 1
CONTENT_FINAL_ROOM, CONTENT_CURIO = 6, 7


@pytest.fixture(scope="module")
def tutorial_map():
    return parse_file(TEMPLATE / "persist.map.json").to_dict()["data"]["map"]


@pytest.fixture(scope="module")
def tutorial_raid():
    return parse_file(TEMPLATE / "persist.raid.json").to_dict()["data"]


# --- the template reads at all ---

def test_every_template_file_is_plain_text_and_still_parses():
    """A new slot is these seven files byte-for-byte, and none of them has the
    DSON magic. Before the fallback, every reader in `bridge/` raised on the
    very files it had just been told to watch."""
    files = sorted(TEMPLATE.glob("persist.*.json"))
    assert len(files) == 7, [f.name for f in files]
    for f in files:
        assert f.read_bytes()[:4] != b"\x01\xb1\x00\x00", f.name
        assert is_complete(f.read_bytes()), f"{f.name} read as torn"
        assert parse_file(f).get("data") is not None, f.name


def test_a_truncated_text_save_is_still_reported_as_torn(tmp_path):
    """The fallback must not turn 'half a file' into 'fine'. That distinction
    is the entire reason `is_complete` exists."""
    good = (TEMPLATE / "persist.game.json").read_bytes()
    assert is_complete(good)
    assert not is_complete(good[:len(good) // 2])


# --- creating the slot IS the embark ---

def test_a_new_campaign_starts_inside_the_tutorial_raid():
    """There is no town step and no Old Road to click in a quest list. The
    template says the party is already in the raid, which is why
    `captures/00_8_old_road.png` is a LOADING screen and its crest is
    Continue."""
    game = parse_file(TEMPLATE / "persist.game.json").to_dict()["data"]
    assert game["inraid"] is True
    assert game["raiddungeon"] == "tutorial"


def test_the_quest_is_the_plot_tutorial(tutorial_raid):
    q = tutorial_raid["raid_instance"]
    assert q["id"] == "tutorial" and q["is_plot_quest"] is True
    assert q["goal_ids"] == {"0": "tutorial_final_room"}
    assert q["dungeon"] == "weald", "the Old Road uses the Weald tileset"
    assert q["difficulty"] == 1 and q["length"] == 1


def test_the_quest_id_hashes_to_what_a_finished_campaign_records():
    """`persist.progression.json`'s `last_raid_quest_id` is 1293978704 in a
    profile taken right after the tutorial. Same hash, so 'has the tutorial
    been played' is answerable from disk."""
    assert dd_name_hash("tutorial") == 1293978704


def test_the_party_starts_at_full_torch_in_rooA(tutorial_raid):
    assert tutorial_raid["in_area"] == "rooA"
    assert tutorial_raid["party"]["torchlight"] == 100


# --- the walk, which is the whole point ---

def test_the_layout_is_two_rooms_and_one_corridor(tutorial_map):
    assert set(tutorial_map["areas"]) == {"rooA", "coAB", "rooB"}


def test_the_corridor_is_six_tiles_and_joins_the_two_rooms(tutorial_map):
    areas = tutorial_map["areas"]
    assert list(areas["coAB"]["tiles"]) == [f"tile{i}" for i in range(6)]
    assert len(areas["rooA"]["tiles"]) == len(areas["rooB"]["tiles"]) == 1
    doors = lambda a: {d["area_to"] for k, d in areas[a].items()
                       if k.startswith("door") and d["area_to"] != "none"}
    assert doors("rooA") == doors("rooB") == {"coAB"}


def test_the_expected_walk_is_exactly_one_battle_then_one_curio(tutorial_map):
    """`rooA -> coAB t0..t5 -> rooB`. The corridor holds a fight at t2 and a
    curio at t4, and nothing else; this is the sequence a run of the bridge
    should produce, in this order."""
    tiles = tutorial_map["areas"]["coAB"]["tiles"]
    content = [tiles[f"tile{i}"]["content"] for i in range(6)]
    assert content == [CONTENT_EMPTY, CONTENT_EMPTY, CONTENT_BATTLE,
                       CONTENT_EMPTY, CONTENT_CURIO, CONTENT_EMPTY]
    assert tiles["tile4"]["cur"] == 824803909, "the corridor curio moved"


def test_the_goal_is_the_far_room_and_it_holds_the_last_fight(tutorial_map):
    rooB = tutorial_map["areas"]["rooB"]["tiles"]["tile0"]
    assert rooB["content"] == CONTENT_FINAL_ROOM
    assert rooB["mash_index"] == 1, "the final room's fight is mash-driven"


def test_the_only_thing_left_to_chance_is_which_monsters_show_up(tutorial_map):
    """The LAYOUT is fixed; the two fights are not.

    Both battle tiles carry a `mash_index` (0 in the corridor, 1 in the final
    room), so the rooms, the corridor length, the curio and their order repeat
    exactly while the enemy comps are rolled. That is the right split for this
    rig: the corridor behaviour under test is deterministic, and the combat
    that sits on top of it still varies.
    """
    mashed = {f"{a}.{tn}": t["mash_index"]
              for a, area in tutorial_map["areas"].items()
              for tn, t in area.get("tiles", {}).items()
              if t.get("mash_index", -1) != -1}
    assert mashed == {"coAB.tile2": 0, "rooB.tile0": 1}
    for name, area in tutorial_map["areas"].items():
        assert area.get("mash_index", -1) in (-1, None), name


def test_the_ids_are_packed_area_names_not_hashes(tutorial_map):
    """`entrance_id` reads as an int and is really four ASCII bytes:
    1097822066 is 0x416F6F72, i.e. "rooA" little-endian. It is NOT a
    `dd_name_hash`, which is the obvious wrong guess -- `dd_name_hash("rooA")`
    is something else entirely. `_unpack_area_id` is what the bridge already
    uses, and `final_room_id` unpacks to the "none" sentinel, so the goal is
    named by `goal_ids` rather than by a room id."""
    from bridge.dungeon_map import _unpack_area_id
    assert _unpack_area_id(tutorial_map["entrance_id"]) == "rooA"
    assert tutorial_map["entrance_id"] != dd_name_hash("rooA")
    assert tutorial_map["final_room_id"] == 1701736302
    assert _unpack_area_id(tutorial_map["final_room_id"]) is None
