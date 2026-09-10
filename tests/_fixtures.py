"""Shared test helpers: one `DataStore` for the whole run, and env factories.

Why this is a plain module and not `conftest.py` fixtures: every test file here
is also runnable as a script (`python tests/data/test_loader.py`), a property
CLAUDE.md calls out and the files' own `__main__` blocks depend on. Pytest
fixtures are invisible to that. A module-level function works under both.

## The two kinds of store, and why the distinction is load-bearing

`get_store()` returns a **shared, cached** DataStore. That is safe because the
store is read-only reference data -- the engine and envs only ever read from
it -- and it is what removes ~98 rebuilds (~98 s) from a full-suite run.

`get_store(fresh=True)` returns a **private** one. Four test files deliberately
inject synthetic entries to exercise paths no shipped game data reaches:

    test_conditional_buffs.py   store.buffs["TEST_ALWAYS"], TEST_HPBELOW, a
                                synthetic trinket
    test_gaps.py                store.buffs["__test_maxhp_shrink__"]

Those must not land in a store other tests read. The keys are namespaced, so
today nothing would notice -- but "nothing notices yet" is how cross-test
pollution gets discovered six months later via a failure that looks like a
fidelity bug. Mutating callers ask for `fresh=True` and the sharing question
stops being a matter of luck.

## One legitimate writer, which sharing makes visible

`CombatEngine` treats `store.buffs` as a memo for buffs it synthesises from
inline effect fields -- `__inline__<effect>__<stat>` and `__buff_type__<effect>`
(engine/combat_engine.py:2150, :2185; bridge/seed.py:350). It is get-or-create
keyed on the effect name, and `store.effects` is keyed by name too, so the id
determines the definition and rewriting it cannot change an answer.

That memo was always process-lived. Sharing the store only means it now
outlives the env that filled it, so **a store that has run combat holds more
buffs than a freshly loaded one**. That is expected, and `test_store_cache.py`
pins it: engine-synthesised keys are exempt from the cached-vs-fresh
comparison, and anything else appearing there fails the test as real pollution.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data.data_loader import DataLoader
from data.store_cache import load_store_cached
from env.gym_env import CSV_DIR, GAME_DIR


def get_store(fresh: bool = False):
    """The DataStore. Shared and cached unless you intend to mutate it.

    Pass `fresh=True` if the test writes into `store.buffs`, `store.trinkets`,
    `store.rules` or any other field -- see this module's docstring.
    """
    if fresh:
        return DataLoader.load_all(CSV_DIR, GAME_DIR)
    return load_store_cached(CSV_DIR, GAME_DIR)


def make_env(kind: str = "fixed", *, store=None, **kwargs):
    """Construct an env against the shared store.

    `kind` is "fixed" | "expedition" | "full_expedition", matching train.py's
    mode names so the two vocabularies do not drift apart.

    The env classes already fall back to the cached store when `store=` is
    omitted, so this is about giving tests one obvious way to do it rather
    than about the saving itself.
    """
    from env import DarkestDungeonEnv, ExpeditionEnv, ExpeditionSequenceEnv

    cls = {
        "fixed": DarkestDungeonEnv,
        "expedition": ExpeditionEnv,
        "full_expedition": ExpeditionSequenceEnv,
    }[kind]
    return cls(store=store if store is not None else get_store(), **kwargs)


def comp_with_classes(*hero_classes, store=None):
    """A comp loadout fielding exactly these classes, found by SHAPE not by name.

    Tests used to pin a party by writing its comp name down --
    `store.comp_loadouts["Holy Hymn: Twin Heist"]`. That breaks on every
    re-export of `metadata/COMPS/curated_comps/`, and it broke on the
    2026-08-26 one: 94 of the tracked comps were deleted outright and 100 new
    ones added, so thirteen tests in `test_traits.py` alone died on a KeyError
    before asserting anything. The 2026-08-23 re-export had already done the
    same thing by renaming 66 of 138.

    What those tests actually need is a party of a given SHAPE -- "a
    man-at-arms, two highwaymen and a vestal" -- and the shape survives a
    rename. Matching on it is what makes the test say what it means.

    Deterministic: candidates are considered in sorted name order, exact
    multiset matches first, then any comp whose roster CONTAINS the requested
    classes. Raises rather than returning None, because a silent skip here
    would hide the pool drifting out from under the suite.
    """
    import collections

    if store is None:
        store = get_store()
    want = collections.Counter(hero_classes)

    exact, superset = [], []
    for name in sorted(store.comp_loadouts):
        have = collections.Counter(h.hero_id for h in store.comp_loadouts[name].heroes
                                   if h.hero_id)
        if have == want:
            exact.append(name)
        elif not (want - have):
            superset.append(name)

    for pool in (exact, superset):
        if pool:
            return store.comp_loadouts[pool[0]]
    raise LookupError(
        f"no comp in the pool fields {sorted(want.elements())}; "
        f"{len(store.comp_loadouts)} loadouts loaded")
