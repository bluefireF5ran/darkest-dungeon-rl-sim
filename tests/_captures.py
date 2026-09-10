"""The captured-save corpus, decoded once per process instead of once per test.

`archive/traces/raidsnaps*/` holds ~390 real `(raid, map, roster)` triples from
live play. They are the only ground truth the bridge has, so a lot of tests walk
the whole corpus -- and each one re-decoded every file from scratch. After the
DataStore fix these were the entire remaining hot spot:

    test_dungeon_map.py    8 tests x ~4.4 s   all re-decoding the same maps
    test_live_bridge.py    5 tests x ~4.5 s
    test_route.py          1 test  x  5.9 s
    test_run.py            several x ~4 s

DSON decoding is pure and the decoded types are immutable -- `Tile`, `Area` and
`DungeonMap` are all `@dataclass(frozen=True)` -- so one decode can be shared by
every reader without any isolation question. That is why this file caches
objects rather than bytes.

Paths are the cache key, so a test that wants a *file* still gets the file; only
the decode is skipped. Nothing here reads the live save -- these are archived
copies, and the "never open the live save in place" rule is about `live_state`.
"""

from __future__ import annotations

import sys
from functools import lru_cache
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

REPO = Path(__file__).resolve().parent.parent
TRACES = REPO / "archive" / "traces"


@lru_cache(maxsize=1)
def map_paths() -> tuple[str, ...]:
    """Every captured `persist.map.json`, sorted, across all raidsnaps dirs."""
    return tuple(sorted(str(p) for p in TRACES.glob("raidsnaps*/*map*.json")))


@lru_cache(maxsize=1)
def raid_paths() -> tuple[str, ...]:
    return tuple(sorted(str(p) for p in TRACES.glob("raidsnaps*/*persist.raid.json")))


@lru_cache(maxsize=8)
def raid_paths_in(dirname: str) -> tuple[str, ...]:
    return tuple(sorted(str(p) for p in (TRACES / dirname).glob("*persist.raid.json")))


@lru_cache(maxsize=4096)
def dungeon_map(path: str):
    """`read_dungeon_map`, memoised. Returns the shared frozen DungeonMap.

    `maxsize` comfortably exceeds the corpus, so within a run this is a plain
    memo table rather than an LRU that could thrash.
    """
    from bridge.dungeon_map import read_dungeon_map
    return read_dungeon_map(path)


def decoded_maps():
    """(path, DungeonMap) for every capture that decodes to a real map."""
    for p in map_paths():
        dm = dungeon_map(p)
        if dm is not None:
            yield p, dm
