"""`store.skill_index()` must answer exactly what the linear scan answered.

`CombatEngine.get_skill_data` and `_is_ally_skill` were O(all skills in the
game) string-compare scans called ~15.6x per env step. Replacing them with dict
lookups is a pure speed change -- and the failure mode if it is *not* pure is
the worst kind this project has: a skill silently resolving to a different
skill's numbers, which no test would notice because every combat still runs.

So the index is not spot-checked, it is checked exhaustively against the
original algorithm, reimplemented verbatim below, for every (skill_id, level)
pair the game ships. The two subtleties both concern *which duplicate wins*:

  * heroes were scanned before enemies, so a hero skill beats a monster skill
    sharing its id;
  * within either group the FIRST match was returned, so on a duplicate id the
    earlier definition wins -- which is why the index is built with
    `setdefault` and not plain assignment. Building it the obvious way makes
    the LAST duplicate win instead, and nothing else in the system would
    object.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests._fixtures import get_store


# --- the original implementations, kept verbatim as the oracle ---

def scan_skill_data(store, skill_id, level):
    for hd in store.heroes.values():
        for sk in hd.skills:
            if sk.id == skill_id and level in sk.levels:
                return sk.levels[level]
    for ed in store.enemies.values():
        for sk in ed.skills:
            if sk.id == skill_id and level in sk.levels:
                return sk.levels[level]
    return None


def scan_is_ally_skill(store, skill_id):
    for hd in store.heroes.values():
        for sk in hd.skills:
            if sk.id == skill_id and sk.levels:
                return next(iter(sk.levels.values())).targets_allies
    return False


def all_pairs(store):
    seen = set()
    for defs in (store.heroes.values(), store.enemies.values()):
        for d in defs:
            for sk in d.skills:
                for lvl in sk.levels:
                    seen.add((sk.id, lvl))
    return sorted(seen)


@pytest.fixture(scope="module")
def store():
    return get_store()


def test_index_matches_the_scan_for_every_skill_the_game_ships(store):
    idx = store.skill_index()
    pairs = all_pairs(store)
    assert len(pairs) > 600, f"only {len(pairs)} pairs — catalogue did not load"

    mismatches = [(sid, lvl) for sid, lvl in pairs
                  if idx.get((sid, lvl)) is not scan_skill_data(store, sid, lvl)]
    assert not mismatches, (
        f"{len(mismatches)} of {len(pairs)} resolve to a DIFFERENT "
        f"SkillLevelData than the scan: {mismatches[:8]}")


def test_ally_index_matches_the_scan_for_every_skill_id(store):
    idx = store.ally_skill_index()
    ids = sorted({sid for sid, _lvl in all_pairs(store)})
    bad = [sid for sid in ids
           if idx.get(sid, False) != scan_is_ally_skill(store, sid)]
    assert not bad, f"{len(bad)} ids disagree on targets_allies: {bad[:8]}"


def test_an_unknown_id_is_a_miss_not_a_crash(store):
    assert store.skill_index().get(("__no_such_skill__", 4)) is None
    assert store.ally_skill_index().get("__no_such_skill__", False) is False


def test_the_engine_methods_agree_with_the_scan(store):
    """Through the real call path, not just the index in isolation."""
    from engine.combat_engine import CombatEngine
    from engine.rng import SeededRNG

    engine = CombatEngine(store, SeededRNG(0))
    for sid, lvl in all_pairs(store)[::37]:          # a spread across the table
        assert engine.get_skill_data(sid, lvl) is scan_skill_data(store, sid, lvl)
        assert engine._is_ally_skill(sid) == scan_is_ally_skill(store, sid)


def test_hero_definitions_beat_monster_definitions_on_a_shared_id(store):
    """The precedence the scan had, stated directly.

    If no id is actually shared this asserts nothing about collisions -- but it
    still documents the rule, and starts failing the day the data does collide
    and the index gets it backwards.
    """
    hero_ids = {sk.id for hd in store.heroes.values() for sk in hd.skills}
    idx = store.skill_index()
    for hd in store.heroes.values():
        for sk in hd.skills:
            for lvl, data in sk.levels.items():
                if idx[(sk.id, lvl)] is not data:
                    # only legal if an EARLIER hero definition claimed it
                    assert scan_skill_data(store, sk.id, lvl) is idx[(sk.id, lvl)]
    assert hero_ids, "no hero skills loaded"


def test_the_index_survives_a_pickle_round_trip(store):
    """Workers get the index from the store pickle, not by rebuilding."""
    import pickle
    built = store.skill_index()
    clone = pickle.loads(pickle.dumps(store, protocol=pickle.HIGHEST_PROTOCOL))
    assert len(clone.skill_index()) == len(built)
    sid, lvl = next(iter(built))
    assert clone.skill_index()[(sid, lvl)] is not None


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v", "-p", "no:xdist"]))
