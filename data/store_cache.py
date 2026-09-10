"""One `DataStore` per process, and one pickle on disk, both safely invalidated.

`DataLoader.load_all` costs ~1.0 s: it parses hundreds of real files out of the
Steam install. Nothing memoised it, so a full `pytest tests/` run rebuilt the
store **~98 times** -- ~98 s of the 275 s baseline, before any combat logic ran.
`--collect-only` alone cost 32 s, from the ~29 test modules that load at import.

Two layers, doing different jobs:

* `lru_cache` collapses the ~98 builds inside one process to one. This is the
  large win, and it is exact -- the store is read-only shared data.
* The disk pickle collapses *process startup*, which matters for `pytest -n
  auto` (16 workers) and for tools run repeatedly from the shell. Measured:
  `dumps` 0.02 s -> 2.3 MB, `loads` 0.03 s, against a 1.0 s rebuild.

## Why the invalidation is a stat sweep and not a timestamp

This project's entire premise is that the game files are the source of truth --
"CSVs are metadata; all numeric truth comes from the game install". A cache that
can serve a stale store is therefore not a performance trade-off, it is a
correctness hole of exactly the kind the CHANGELOG exists to prevent: every test
would keep passing against game data that no longer says what the cache thinks.

So the key is a digest over `(size, mtime_ns)` of every file the loader reads --
the game subtrees plus the repo's CSV directories -- and over the loader's own
source files, since a parser change alters the store without touching any data.
Measured cost of the sweep: **0.172 s over 10,292 files**, i.e. ~6x cheaper than
the rebuild it guards, and it happens once per process.

It is deliberately not "hash the file contents" (far too slow) and deliberately
not "trust an mtime on one sentinel file" (a Steam verify, a DLC install, or an
edit to one `.darkest` would slip straight through).

Escape hatches, in order of bluntness:
    DDSIM_NO_STORE_CACHE=1   bypass entirely -- always rebuild, never read/write
    DDSIM_STORE_CACHE_DIR    put the pickle somewhere else
"""

from __future__ import annotations

import hashlib
import os
import pickle
import sys
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Optional

from .csv_loader import csv_root
from .mod_roots import MOD_DIRS_ENV, mod_roots
from .data_loader import DataLoader
from .types import DataStore

# Bump when a change makes an existing pickle wrong in a way the file digest
# cannot see -- e.g. a DataStore field is renamed, so an old pickle unpickles
# "successfully" into the wrong shape.
CACHE_FORMAT_VERSION = 1

# The game subtrees DataLoader actually reads. Listing them rather than walking
# GAME_DIR wholesale keeps the sweep off the multi-GB art/audio/video trees,
# which is most of the install and none of the data.
GAME_SUBTREES = (
    "shared", "effects", "trinkets", "heroes", "monsters", "dungeons",
    "raid", "campaign", "inventory", "scripts", "dlc", "localization",
    "curios", "upgrades", "modes",
)

# The repo-side metadata directories, resolved under `csv_root(csv_dir)`
# -- they live in `metadata/` since 2026-08-20, and the digest has to stat
# the real files or an edit to one would not invalidate the cache.
CSV_SUBTREES = (
    "HEROES", "SKILLS", "ENEMIES", "TRINKETS", "QUIRKS",
    "CAMP_SKILLS", "COMPS", "ENEMY_COMPS",
)


def _disabled() -> bool:
    return os.environ.get("DDSIM_NO_STORE_CACHE", "").strip() not in ("", "0")


def cache_dir() -> Path:
    override = os.environ.get("DDSIM_STORE_CACHE_DIR")
    if override:
        return Path(override)
    return Path(tempfile.gettempdir()) / "ddsim_store_cache"


def _iter_files(roots: Iterable[Path]):
    for root in roots:
        if not root.exists():
            continue
        if root.is_file():
            yield root
            continue
        for dirpath, _dirnames, filenames in os.walk(root):
            for name in filenames:
                yield Path(dirpath) / name


def _loader_sources() -> list[Path]:
    """Every module under data/ -- a parser edit changes the store."""
    return sorted(Path(__file__).resolve().parent.glob("*.py"))


def fingerprint(csv_dir: Path, game_dir: Path, sources=None) -> str:
    """A digest of everything that can change what `load_all` returns.

    Order-independent by construction: each file contributes a hash of its own
    path+size+mtime, and those are XOR-folded, so `os.walk` ordering (which is
    filesystem-dependent) cannot change the answer.
    """
    acc = 0
    count = 0
    # `sources` is injectable ONLY so tests can point the loader-source half of
    # the digest at a temp tree. A test that mutated the real `data/*.py`
    # mtime to prove invalidation works was, under `pytest -n auto`, racing
    # every other worker's fingerprint call -- the stability test failed
    # intermittently and looked like the digest itself being unstable.
    # Production always passes None.
    roots = (
        [game_dir / s for s in GAME_SUBTREES]
        # csv_root, NOT csv_dir. The CSV dirs moved under `metadata/` on
        # 2026-08-20 and this line did not follow them, so for the whole time
        # since, every CSV root resolved to a path that does not exist --
        # and `_iter_files` skips a missing root silently. The CSV half of
        # the digest was contributing NOTHING: editing a comp, a hero CSV or
        # any other metadata file did not invalidate the cache, which is the
        # precise failure this digest exists to prevent. Found when Fran
        # added a comp directory and a freshly-loaded store had 134 loadouts
        # against the cache's 133.
        + [csv_root(csv_dir) / s for s in CSV_SUBTREES]
        # THIRD-PARTY CONTENT, or the cache serves a store without it.
        # `DDSIM_MOD_DIRS` is an environment variable, so nothing about the
        # game tree or `data/*.py` changes when it is flipped -- a cached
        # store built without mods would be handed straight back, and the
        # symptom is a hero whose class exists and whose kit is empty. That
        # was documented as "pair it with DDSIM_NO_STORE_CACHE=1", which is a
        # workaround costing a full rebuild in every one of a search's twelve
        # workers. Sweeping the roots makes the cache correct instead.
        + list(mod_roots())
        + (list(sources) if sources is not None else _loader_sources())
    )
    for path in _iter_files(roots):
        try:
            st = path.stat()
        except OSError:
            # A file that vanished mid-sweep still has to change the digest,
            # or a deletion would be invisible.
            acc ^= int(hashlib.blake2b(str(path).encode("utf-8", "replace"),
                                       digest_size=8).hexdigest(), 16)
            continue
        token = f"{path}|{st.st_size}|{st.st_mtime_ns}".encode("utf-8", "replace")
        acc ^= int(hashlib.blake2b(token, digest_size=8).hexdigest(), 16)
        count += 1
    # The VARIABLE itself, not only the files it names: removing a root from
    # the list changes what loads while leaving every remaining file untouched.
    mods = os.environ.get(MOD_DIRS_ENV, "")
    stamp = (f"{CACHE_FORMAT_VERSION}|{count}|{acc:016x}|{sys.version_info[:2]}"
             f"|{mods}")
    return hashlib.blake2b(stamp.encode(), digest_size=16).hexdigest()


def _cache_path(csv_dir: Path, game_dir: Path) -> Path:
    key = hashlib.blake2b(f"{csv_dir}|{game_dir}".encode("utf-8", "replace"),
                          digest_size=8).hexdigest()
    return cache_dir() / f"store_{key}.pkl"


def load_store_cached(csv_dir, game_dir, *, use_disk: bool = True) -> DataStore:
    """`DataLoader.load_all`, memoised per process and (optionally) on disk.

    Returns a **shared** instance. The store is read-only data -- envs and the
    engine only ever read from it -- so sharing is the point. Do not mutate it;
    if you need to, `DataLoader.load_all` directly is still there.
    """
    if _disabled():
        return DataLoader.load_all(csv_dir, game_dir)
    return _load_cached(str(Path(csv_dir).resolve()),
                        str(Path(game_dir).resolve()),
                        use_disk)


@lru_cache(maxsize=4)
def _load_cached(csv_dir: str, game_dir: str, use_disk: bool) -> DataStore:
    csv_path, game_path = Path(csv_dir), Path(game_dir)
    if not use_disk:
        return DataLoader.load_all(csv_path, game_path)

    fp = fingerprint(csv_path, game_path)
    path = _cache_path(csv_path, game_path)
    try:
        with open(path, "rb") as fh:
            stored_fp = pickle.load(fh)
            if stored_fp == fp:
                return pickle.load(fh)
    except (OSError, EOFError, pickle.UnpicklingError, AttributeError,
            ModuleNotFoundError, ImportError):
        # Any unreadable/stale/incompatible pickle is simply not a cache hit.
        pass

    store = DataLoader.load_all(csv_path, game_path)
    _write_atomic(path, fp, store)
    return store


def _write_atomic(path: Path, fp: str, store: DataStore) -> None:
    """Write via a temp file + replace, so a killed process cannot leave a
    torn pickle that the next run would then have to detect.

    Cache writing is best-effort: a read-only temp dir or a race with another
    worker must never fail the caller, which only asked for a DataStore.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as fh:
                pickle.dump(fp, fh, protocol=pickle.HIGHEST_PROTOCOL)
                pickle.dump(store, fh, protocol=pickle.HIGHEST_PROTOCOL)
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    except (OSError, pickle.PicklingError, RecursionError):
        pass


def clear(csv_dir=None, game_dir=None) -> None:
    """Drop the in-process memo, and the pickle if a pair is named."""
    _load_cached.cache_clear()
    if csv_dir is not None and game_dir is not None:
        try:
            _cache_path(Path(csv_dir).resolve(), Path(game_dir).resolve()).unlink()
        except OSError:
            pass
