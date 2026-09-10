"""Tests for scripts/map_generator.darkest — the real dungeon layout data that
supersedes the invented EXPEDITION_LENGTH_BANDS convention.

The point of these tests is to pin the bands to the *game file*, so that if
someone re-invents a convention (or the multi-line block parser regresses and
silently drops every `.key` line, which is exactly what the pre-existing
line-oriented parser did) the eval distribution can't drift back without a
test going red.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests._fixtures import get_store

from data.darkest_parser import DarkestParser
from data.map_gen_loader import MapGenLoader
from env.expedition_sequence_env import EXPEDITION_LENGTH_BANDS

from data.paths import GAME_DIR
from data.paths import CSV_DIR
store = get_store()


def test_map_generator_file_exists_and_parses():
    """The file the docs claimed didn't exist. 44 map records ship with the game."""
    assert len(store.map_gen_settings) == 44


def test_block_parser_keeps_keys_the_line_parser_drops():
    """map_generator.darkest is the multi-line block layout: `map:` on its own
    line, one `.key value` per following line. The line-oriented parse_file
    sees the headers but drops every field — that silent-empty-record failure
    is what parse_blocks exists to avoid."""
    path = GAME_DIR / "scripts" / "map_generator.darkest"
    line_recs = DarkestParser.parse_file(path)
    block_recs = DarkestParser.parse_blocks(path)

    assert len(line_recs) == len(block_recs) == 44
    assert all(set(r) == {"_type"} for r in line_recs)      # every field lost
    assert all("hallway_battle" in r for r in block_recs)    # every field kept


def test_multi_value_ranges_parse_to_int_pairs():
    """`.hallway_battle 2 4` reaches the loader as the string "2 4"; it must
    come out as an inclusive (min, max) int pair."""
    s = next(x for x in store.map_gen_settings
             if x.size == "short" and x.quest_type == "explore" and x.dungeon_type == "crypts")
    assert s.hallway_battles == (2, 4)
    assert s.room_battles == (1, 3)
    assert s.total_battles == (3, 7)
    assert s.base_room_number == 9


def test_derived_bands_match_the_game_file():
    """The headline numbers. Recomputed straight from the parsed records so
    this can't pass against a hardcoded copy."""
    bands = MapGenLoader.combat_count_bands(store.map_gen_settings)
    assert bands == {"short": (3, 8), "medium": (6, 10), "long": (14, 16)}

    expected = {}
    for s in store.map_gen_settings:
        lo, hi = s.total_battles
        cur = expected.get(s.size)
        expected[s.size] = (min(cur[0], lo), max(cur[1], hi)) if cur else (lo, hi)
    assert bands == expected


def test_env_uses_the_derived_bands_in_canonical_order():
    """Callers index these positionally (`list(BANDS)[ep % 3]`), so both the
    values and the short->medium->long order are load-bearing."""
    assert EXPEDITION_LENGTH_BANDS == {"short": (3, 8), "medium": (6, 10), "long": (14, 16)}
    assert list(EXPEDITION_LENGTH_BANDS) == ["short", "medium", "long"]


def test_bands_are_shorter_than_the_old_invented_convention():
    """Regression guard on the actual bug: the old convention ran roughly
    twice the real combats at short/medium — a simulated 'short' expedition
    was longer than a real 'medium' one."""
    old = {"short": (8, 11), "medium": (10, 14), "long": (14, 18)}
    for size in ("short", "medium"):
        assert EXPEDITION_LENGTH_BANDS[size][1] < old[size][1]
    # The specific inversion that made this severe.
    assert EXPEDITION_LENGTH_BANDS["short"][1] <= EXPEDITION_LENGTH_BANDS["medium"][1]
    assert old["short"][0] < old["medium"][1]  # old short overlapped old medium


def test_every_record_has_a_sane_battle_range():
    for s in store.map_gen_settings:
        assert s.hallway_battles[0] <= s.hallway_battles[1]
        assert s.room_battles[0] <= s.room_battles[1]
        assert s.total_battles[0] >= 0
        assert s.size in ("short", "medium", "long")


if __name__ == "__main__":
    for name, fn in sorted(list(globals().items())):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  ok  {name}")
