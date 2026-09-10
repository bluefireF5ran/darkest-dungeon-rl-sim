"""The store cache must never be able to hand back a stale DataStore.

This project's whole premise is that the game install is the source of truth --
"CSVs are metadata; all numeric truth comes from the game install". So a cache
that can serve a store built from data that has since changed is not a
performance trade-off, it is the exact failure mode CHANGELOG.md exists to
prevent: every test keeps passing, against numbers the game no longer ships.

These tests are therefore mostly about *invalidation*, not about speed. The
speed is incidental and already visible in the suite wall time (275 s -> 44 s);
what needs pinning is that the cache notices when it must not be used.
"""

import os
import sys
import pickle
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from data import store_cache
from data.data_loader import DataLoader
from data.store_cache import (
    fingerprint, load_store_cached, _cache_path, _loader_sources,
)
from env.gym_env import CSV_DIR, GAME_DIR


# `CombatEngine` uses `store.buffs` as a MEMO for buffs it synthesises from
# inline effect fields -- `__inline__<effect>__<stat>` and
# `__buff_type__<effect>` (engine/combat_engine.py:2150, :2185), and
# `bridge/seed.py` does the same for a save's un-named `buff_group` entries
# under `__live__`. All three are get-or-create keyed on something that
# determines the definition, so writing one twice cannot produce two different
# answers.
#
# That memo was always process-lived; sharing the store just means it now
# survives across envs instead of dying with each one. It is therefore expected
# that a store which has run combat holds MORE buffs than a freshly loaded one,
# and any comparison between the two has to exclude them. Discovering this is
# what the first version of this test did -- it compared raw counts and failed
# at 2567 vs 2529 only under xdist, i.e. only when some other test in the same
# worker had run a fight first.
#
# **The prefix list is a convention, and this test is the only thing enforcing
# it.** `bridge/seed.py` used to key its memo `LIVE_...`, which is not
# `__`-prefixed, so it read as pollution -- and only when the scheduler put a
# save-seeding test and this one in the same worker, which is to say it lay
# dormant until four new test files shifted the distribution. A new synthesis
# site must adopt the prefix, NOT be added as another exemption here; the
# whole value of the tripwire is that the list stays short.
ENGINE_SYNTHESISED = ("__inline__", "__buff_type__", "__live__")


def _loaded_buffs(store):
    return {k for k in store.buffs
            if not k.startswith(ENGINE_SYNTHESISED)}


def _shape(store):
    """A cheap content signature of what the LOADER produced.

    Deliberately excludes the engine's synthesised buffs -- see above.
    """
    return (
        len(store.heroes), len(store.enemies), len(store.comps),
        len(_loaded_buffs(store)), len(store.effects), len(store.enemy_comps),
        len(store.comp_loadouts),
    )


def test_cached_store_matches_a_freshly_loaded_one():
    """The cache must be transparent: same content, whatever the path in."""
    assert _shape(load_store_cached(CSV_DIR, GAME_DIR)) == \
           _shape(DataLoader.load_all(CSV_DIR, GAME_DIR))


def test_only_engine_synthesised_buffs_ever_differ():
    """Pin the exemption, so it cannot quietly widen.

    If some other code starts writing non-`__`-prefixed entries into a shared
    store, that IS cross-test pollution and this should fail rather than being
    absorbed by a laxer comparison.
    """
    cached = load_store_cached(CSV_DIR, GAME_DIR)
    fresh = DataLoader.load_all(CSV_DIR, GAME_DIR)
    extra = set(cached.buffs) - set(fresh.buffs)
    assert all(k.startswith(ENGINE_SYNTHESISED) for k in extra), \
        f"unexpected non-synthesised additions to a shared store: " \
        f"{sorted(k for k in extra if not k.startswith(ENGINE_SYNTHESISED))[:10]}"


@pytest.mark.skipif(store_cache._disabled(),
                    reason="DDSIM_NO_STORE_CACHE=1 disables the memo this asserts")
def test_the_same_process_gets_one_shared_instance():
    """This is the ~98-rebuilds-per-run fix; identity is the whole point.

    Skipped under the bypass, where returning two DISTINCT stores is the
    correct behaviour and `test_the_bypass_returns_a_private_store` below is
    what pins it. Without the guard this failed every
    `DDSIM_NO_STORE_CACHE=1 python -m pytest tests/` run -- the one command
    CLAUDE.md asks for to prove the cache is a speedup and not a correctness
    crutch, which is a poor thing to have a standing red in."""
    assert load_store_cached(CSV_DIR, GAME_DIR) is load_store_cached(CSV_DIR, GAME_DIR)


def test_fingerprint_is_stable_when_nothing_changed():
    assert fingerprint(Path(CSV_DIR), Path(GAME_DIR)) == \
           fingerprint(Path(CSV_DIR), Path(GAME_DIR))


def test_a_loader_edit_invalidates_the_cache(tmp_path):
    """A parser change alters the store without touching a single data file.

    This is the invalidation case a naive "stat the game dir" key would miss
    entirely, and the one most likely to bite during development: edit
    darkest_parser.py, re-run the suite, and silently test the old parser.

    Driven through a SYNTHETIC source file rather than the real
    `data/darkest_parser.py`. The first version mutated the real one's mtime
    and restored it, which passed alone and failed under `pytest -n auto`:
    every other worker computing a fingerprint during that window saw the
    digest move, and `test_fingerprint_is_stable_when_nothing_changed` failed
    intermittently. A test that mutates shared filesystem state cannot be run
    in parallel with one that reads it -- so it does not mutate it any more.
    """
    fake = tmp_path / "loader.py"
    fake.write_text("# pretend parser", encoding="utf-8")
    args = (Path(CSV_DIR), Path(GAME_DIR))

    before = fingerprint(*args, sources=[fake])
    os.utime(fake, ns=(0, 10**18))
    assert fingerprint(*args, sources=[fake]) != before


def test_the_real_loader_sources_are_part_of_the_digest():
    """The injection point above must not have made production stop looking at
    `data/*.py` -- that is the whole invalidation case."""
    names = {p.name for p in _loader_sources()}
    assert "darkest_parser.py" in names and "data_loader.py" in names
    assert fingerprint(Path(CSV_DIR), Path(GAME_DIR)) != \
        fingerprint(Path(CSV_DIR), Path(GAME_DIR), sources=[])


def test_a_game_data_edit_invalidates_the_cache(tmp_path):
    """The case the project actually cares about: the install changed.

    Driven through a synthetic tree rather than by touching the real Steam
    install, which no test may modify.
    """
    game = tmp_path / "game"
    (game / "shared").mkdir(parents=True)
    rules = game / "shared" / "rules.json"
    rules.write_text("{}", encoding="utf-8")

    before = fingerprint(tmp_path / "csv", game)
    rules.write_text('{"changed": 1}', encoding="utf-8")
    os.utime(rules, ns=(0, 10**18))          # force a distinct mtime
    assert fingerprint(tmp_path / "csv", game) != before


def test_a_metadata_edit_invalidates_the_cache(tmp_path):
    """The CSV half of the digest, which was DEAD for months.

    `fingerprint` built its CSV roots from `csv_dir` and not from
    `csv_root(csv_dir)`. The metadata directories moved under `metadata/` on
    2026-08-20 and this line did not follow them, so every CSV root resolved
    to a path that no longer exists -- and `_iter_files` skips a missing root
    silently, by design, because a genuinely absent DLC is not an error. The
    result: adding or editing a comp, a hero CSV or any other metadata file
    did NOT invalidate the store cache.

    It surfaced only because Fran added a comp directory and a freshly-loaded
    store had 134 loadouts against the cache's 133. That is the exact failure
    mode CHANGELOG.md exists to prevent -- a cache serving stale game data --
    and nothing here was testing for it, because every other test drives the
    digest through a synthetic tree where csv_dir and csv_root coincide.
    """
    csv = tmp_path / "csv"
    (csv / "metadata" / "COMPS").mkdir(parents=True)
    comp = csv / "metadata" / "COMPS" / "whatever.csv"
    comp.write_text("a,b" + chr(10), encoding="utf-8")

    before = fingerprint(csv, tmp_path / "game")
    comp.write_text("a,b,c" + chr(10), encoding="utf-8")
    os.utime(comp, ns=(0, 10**18))
    assert fingerprint(csv, tmp_path / "game") != before, (
        "a metadata/ edit did not move the digest")


def test_the_real_metadata_tree_is_actually_being_stat_ed():
    """The regression above passes even against a `metadata/`-blind digest if
    the tree is empty, so pin that the REAL one contributes: dropping the CSV
    roots must change the answer."""
    from data.csv_loader import csv_root
    root = csv_root(CSV_DIR)
    assert (root / "COMPS").is_dir(), "the metadata tree moved again"
    assert (fingerprint(Path(CSV_DIR), Path(GAME_DIR))
            != fingerprint(root / "__does_not_exist__", Path(GAME_DIR)))


def test_a_deleted_file_invalidates_the_cache(tmp_path):
    """Deletion has to move the digest too, or a removed DLC is invisible."""
    game = tmp_path / "game"
    (game / "shared").mkdir(parents=True)
    victim = game / "shared" / "rules.json"
    victim.write_text("{}", encoding="utf-8")

    before = fingerprint(tmp_path / "csv", game)
    victim.unlink()
    assert fingerprint(tmp_path / "csv", game) != before


def test_a_corrupt_pickle_is_a_miss_not_a_crash(tmp_path, monkeypatch):
    """A torn cache file must degrade to a rebuild, never to an exception.

    The same reasoning as the bridge's torn-save rule: the writer can be
    killed mid-write, and the reader is the one that has to cope.
    """
    monkeypatch.setenv("DDSIM_STORE_CACHE_DIR", str(tmp_path))
    store_cache.clear()
    path = _cache_path(Path(CSV_DIR).resolve(), Path(GAME_DIR).resolve())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x80\x05 not a pickle at all")

    store = load_store_cached(CSV_DIR, GAME_DIR)      # must not raise
    assert _shape(store) == _shape(DataLoader.load_all(CSV_DIR, GAME_DIR))
    store_cache.clear()


def test_a_stale_fingerprint_in_the_pickle_is_a_miss(tmp_path, monkeypatch):
    """A well-formed pickle whose fingerprint no longer matches is not used."""
    monkeypatch.setenv("DDSIM_STORE_CACHE_DIR", str(tmp_path))
    store_cache.clear()
    path = _cache_path(Path(CSV_DIR).resolve(), Path(GAME_DIR).resolve())
    path.parent.mkdir(parents=True, exist_ok=True)

    sentinel = object()
    with open(path, "wb") as fh:
        pickle.dump("a fingerprint from a different world", fh)
        pickle.dump({"not": "a store"}, fh)

    store = load_store_cached(CSV_DIR, GAME_DIR)
    assert _shape(store)          # a real store, not the dict we planted
    store_cache.clear()


def test_the_env_var_bypasses_the_cache_entirely(monkeypatch):
    """`DDSIM_NO_STORE_CACHE=1` must give a private, freshly built store."""
    monkeypatch.setenv("DDSIM_NO_STORE_CACHE", "1")
    a = load_store_cached(CSV_DIR, GAME_DIR)
    b = load_store_cached(CSV_DIR, GAME_DIR)
    assert a is not b
    assert _shape(a) == _shape(b)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v", "-p", "no:xdist"]))
