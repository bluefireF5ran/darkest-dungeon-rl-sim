"""Which combat skills a hero has UNLOCKED, and what the missing ones cost.

A curated comp names four skills per hero. A recruit arrives from the stage
coach with four *random* ones, so **"can this roster field this comp" is a
question about the Guild, not about the roster** -- and the answer is already
in the save, which means the embark plan needs no screen to decide it.

## `instance_number` is the hero's guid

`persist.upgrades.json` keys every purchase by `dd_name_hash(tree.id)` with an
`instance_number` beside it, and `data/heirlooms.py::read_rank_counts` tallies
them. That docstring establishes presence-is-purchase and count-is-rank; what
this module adds is that the instance is **the hero's guid**, so a tree can be
attributed to one hero. Three independent agreements say so, measured over the
live twelve-hero roster:

* every hash that inverts for a given instance belongs to **that hero's own
  class** -- never another's;
* the ten restricted classes carry **exactly four** combat trees at rank 1,
  which is exactly `selected_combat_skills`;
* the Abomination carries **seven**, which is
  `skill_selection: .can_select_combat_skills false` -- `UNRESTRICTED_HEROES`
  falling out of the save with nothing here testing for it.

## An unrecognised hash is a CAMP skill, not a missing unlock

Camp trees live outside `upgrades/`, so they cannot be named here. The number
of hashes left over is **exactly the hero's own `selected_camping_skills`
count** -- 3 for nearly every hero, and the exception is what makes it a rule
rather than a coincidence: `flagellant#60` in Fran's Week 22 save carries two
camp skills and leaves exactly two hashes unnamed. A hardcoded three was the
first version of this claim and that hero falsified it.

`unaccounted` returns them rather than discarding them, because the count
matching is the invariant that says the reading still holds; one more would
mean a tree this module is silently failing to name, and `unlocked` would
understate what a hero owns.

## What this module deliberately does not do

It does not read `selected_combat_skills` -- which four of the unlocked skills
are currently *equipped*. That is a different question, answered on the Guild
panel by clicking, and it is `bridge/` work.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Iterable, Optional

from data.heirlooms import read_rank_counts
from data.town_costs import load_hero_costs

from data.paths import GAME_DIR

# The two non-skill trees a hero carries, so `unaccounted` can subtract them.
GEAR_TREES = ("weapon", "armour")

def _hero_key(hero) -> tuple[str, int]:
    """`(class, guid)` from a `RosterHero`, a `LiveHero`, or a plain pair.

    Duck-typed on purpose: `data/` must not import `bridge/`, and a test wants
    to ask about a class and a guid without building a roster record.
    """
    if isinstance(hero, tuple):
        return str(hero[0]), int(hero[1])
    return str(hero.hero_class), int(hero.guid)


def _upgrades_file(profile) -> Path:
    path = Path(profile)
    return path if path.is_file() else path / "persist.upgrades.json"


@lru_cache(maxsize=8)
def _counts_cached(path: str, stamp: tuple) -> dict:
    return read_rank_counts(path)


def rank_counts(profile) -> dict[tuple[int, int], int]:
    """`read_rank_counts`, memoised on the file's own `(size, mtime_ns)`.

    The guild step re-reads this after every purchase to acknowledge it, so a
    plain `lru_cache` would serve the pre-purchase answer forever. Stamping the
    key is the same invalidation `data/store_cache.py` uses, for the same
    reason.
    """
    path = _upgrades_file(profile)
    try:
        st = path.stat()
        stamp = (st.st_size, st.st_mtime_ns)
    except OSError:
        return {}
    return _counts_cached(str(path), stamp)


@lru_cache(maxsize=64)
def _tree_hashes(game_dir: str, hero_class: str) -> dict[int, str]:
    """`{tree hash: tree name}` for one class -- its skills plus weapon/armour.

    The hash is one-way, so this is the inversion table: hash the candidates
    and match. Same method `bridge/dungeon_map.py` uses for `curio_prop`.
    """
    from data.dson import dd_name_hash

    costs = load_hero_costs(game_dir, hero_class)
    names: tuple[str, ...] = ()
    if costs is not None:
        names = tuple(costs.skills) + GEAR_TREES
    return {dd_name_hash(f"{hero_class}.{name}") & 0xFFFFFFFF: name
            for name in names}


def _named(profile, hero, game_dir: str) -> tuple[dict[str, int], dict[int, int]]:
    hero_class, guid = _hero_key(hero)
    table = _tree_hashes(game_dir, hero_class)
    named: dict[str, int] = {}
    left: dict[int, int] = {}
    for (tree_hash, instance), ranks in rank_counts(profile).items():
        if instance != guid:
            continue
        name = table.get(tree_hash)
        if name is None:
            left[tree_hash] = ranks
        else:
            named[name] = ranks
    return named, left


def unlocked(profile, hero, game_dir: str = str(GAME_DIR)) -> dict[str, int]:
    """`{skill_id: rank}` -- the combat skills this hero can equip.

    Rank is how many times the tree was bought: 1 is a skill the recruit
    arrived with or the Guild has just unlocked, 5 is a fully upgraded one.
    Weapon and armour are excluded; ask `gear_ranks` for those.

    A class whose upgrade file this install does not have (a modded hero)
    returns `{}`, which a caller must not read as "has no skills" -- pair it
    with `unlock_cost`, which returns `None` for exactly that case.
    """
    named, _ = _named(profile, hero, game_dir)
    return {k: v for k, v in named.items() if k not in GEAR_TREES}


def gear_ranks(profile, hero, game_dir: str = str(GAME_DIR)) -> dict[str, int]:
    """`{"weapon": rank, "armour": rank}`, absent when never bought."""
    named, _ = _named(profile, hero, game_dir)
    return {k: v for k, v in named.items() if k in GEAR_TREES}


def unaccounted(profile, hero, game_dir: str = str(GAME_DIR)) -> dict[int, int]:
    """The hashes this hero carries that name no tree here -- the camp skills.

    Expected to be exactly the hero's own `selected_camping_skills` count --
    three for nearly every hero, two for at least one shipped Flagellant. More
    than that means a tree is going unnamed, which would make `unlocked`
    quietly understate what the hero has.
    """
    _, left = _named(profile, hero, game_dir)
    return left


def missing(profile, hero, wanted: Iterable[str],
            game_dir: str = str(GAME_DIR)) -> tuple[str, ...]:
    """The skills in `wanted` this hero has not unlocked, in `wanted`'s order.

    A skill the class's tree does not contain is **not** reported missing: it
    is a comp naming something this class cannot learn, which is a data
    problem, not a purchase. `unknown` reports those separately.
    """
    have = unlocked(profile, hero, game_dir)
    hero_class, _ = _hero_key(hero)
    costs = load_hero_costs(game_dir, hero_class)
    known = set(costs.skills) if costs is not None else set()
    return tuple(s for s in wanted if s in known and s not in have)


def unknown(hero, wanted: Iterable[str],
            game_dir: str = str(GAME_DIR)) -> tuple[str, ...]:
    """The skills in `wanted` that are not in this class's tree at all."""
    hero_class, _ = _hero_key(hero)
    costs = load_hero_costs(game_dir, hero_class)
    if costs is None:
        return tuple(wanted)
    return tuple(s for s in wanted if s not in costs.skills)


def unlock_cost(profile, hero, wanted: Iterable[str],
                game_dir: str = str(GAME_DIR)) -> Optional[int]:
    """Gold to unlock every skill in `wanted` this hero lacks.

    A rank-1 unlock is **1000 gold with no prerequisite and
    `prerequisite_resolve_level: 0`** in all twenty shipped trees, so this is a
    flat multiple in practice; it is summed from the file anyway, because a
    number typed in here would be the kind of convention this repo writes
    parsers to avoid.

    Returns `None` when the class has no upgrade file -- a modded hero, whose
    unlocks cannot be priced and must not be assumed free.

    An unrestricted class (Abomination, Duelist) needs **no special case**: the
    save already carries all seven of its trees, so `missing` is empty and this
    returns 0 with nothing here testing for identity.
    """
    hero_class, _ = _hero_key(hero)
    costs = load_hero_costs(game_dir, hero_class)
    if costs is None:
        return None
    total = 0
    for skill in missing(profile, hero, wanted, game_dir):
        ranks = costs.skills.get(skill, ())
        total += ranks[0] if ranks else 0
    return total


def main() -> int:
    import argparse
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--profile", required=True)
    ap.add_argument("--game-dir", default=str(GAME_DIR))
    args = ap.parse_args()

    from bridge.town_state import read_town_state

    town = read_town_state(Path(args.profile))
    for hero in town.heroes:
        have = unlocked(args.profile, hero, args.game_dir)
        gear = gear_ranks(args.profile, hero, args.game_dir)
        left = unaccounted(args.profile, hero, args.game_dir)
        camp = len(hero.hero.camping_skills)
        flag = "" if len(left) == camp else f"  <-- {len(left)} unnamed, {camp} camp"
        print(f"{hero.label():34s} {len(have)} skills {sorted(have)}"
              f" gear={gear or '-'}{flag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
