"""Where third-party content lives, in exactly one place.

`DDSIM_MOD_DIRS` is an `os.pathsep`-separated list of Workshop (or any other)
content roots. It is **off unless set**, so nothing about the default store
changes and every archived measurement stays reproducible.

`HeroLoader` has read this variable since 2026-08-24 to discover modded hero
classes. That was only ever half a class: a hero file names effects, buffs,
trinkets and camping skills that live in the mod's OWN `effects/`,
`shared/buffs/`, `trinkets/` and `raid/camping/` directories, and every one of
those loaders read `GAME_DIR` alone. The result was a hero with correct stats
and damage and **no payloads at all** -- 93.8% of the Sibyl's 160 effect
references resolved to nothing, including the three `switch_mode_*_self`
effects her whole three-stance design turns on, so five of her seven skills
were unreachable.

## One rule, everywhere: a mod may ADD, never SHADOW

The DLC merge in `DataLoader.load_all` uses `dict.update()`, which lets a later
file override an earlier id. That is tolerable for first-party DLC, which does
not redefine base ids. It is not tolerable here. A mod that redefines
`Stun 1` or `crusader` would silently change what every vanilla comp does, and
the change would be invisible -- no error, no missing id, just different
numbers in a store that half this project's measurements are anchored to.

So `merge_new_only` adds an id only when nothing already holds it, which is the
same guarantee `HeroLoader._load_mod_heroes` already gives for class ids. Base
wins, DLC wins over nothing, mods take what is left.

## The cache does not key on this

`data/store_cache.py` digests the game subtrees and `data/*.py`, not the
environment. Flipping `DDSIM_MOD_DIRS` therefore does **not** invalidate a
cached store on its own -- pair it with `DDSIM_NO_STORE_CACHE=1`, or the first
run after enabling mods may serve a store built without them.
"""
from __future__ import annotations

import os
from pathlib import Path

MOD_DIRS_ENV = "DDSIM_MOD_DIRS"


def mod_roots() -> list[Path]:
    """Every existing directory named by `DDSIM_MOD_DIRS`, in the order given.

    Order is the caller's, and it matters wherever first-wins does: the first
    mod to claim an id keeps it.
    """
    raw = os.environ.get(MOD_DIRS_ENV, "").strip()
    if not raw:
        return []
    out = []
    for entry in raw.split(os.pathsep):
        entry = entry.strip()
        if not entry:
            continue
        root = Path(entry)
        if root.is_dir():
            out.append(root)
    return out


def mod_files(pattern: str) -> list[Path]:
    """Every file matching `pattern` under every mod root, sorted per root.

    Sorted so two runs on one machine see the same order, which is what makes
    first-wins deterministic when two mods ship the same id.
    """
    out: list[Path] = []
    for root in mod_roots():
        out.extend(sorted(root.glob(pattern)))
    return out


def merge_new_only(target: dict, incoming: dict) -> int:
    """Add ids `target` does not already hold. Returns how many were added.

    The counterpart of `dict.update()`, and the difference is the whole point:
    a mod must not be able to redefine a shipped effect, buff, trinket or quirk.
    """
    added = 0
    for key, value in incoming.items():
        if key not in target:
            target[key] = value
            added += 1
    return added
