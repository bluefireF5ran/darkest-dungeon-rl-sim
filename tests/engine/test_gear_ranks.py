"""Which gear a hero of a given resolve level is fielded with.

This used to be a declared convention (`tier_idx = level - 1`) whose own comment
said the real unlock table "only gives a non-linear *minimum* resolve level per
tier". The table is right there in the game's data and nobody read it:
`upgrades/heroes/<class>.upgrades.json`, each weapon/armour tree's
`prerequisite_resolve_level`.

The convention agreed with the data for veterans and champions and was wrong by
a full rank for apprentices, which is the bracket `TIER_TO_HERO_LEVEL` fields
for mash tiers 1-2 — so every Apprentice combat ever trained was fought
under-equipped.

Fran's independent statement of the same rule, which is what prompted the
check: *"a hero resolve 2 can reach lvl 3 armor, weapon and skills, lvl 1 will
have 2-2-2 ... veteran ... level 3-4 so they will both have lvl 4-4-4 ...
champion ... resolve 5-6 and their equipement will always be lvl 5-5-5."*
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests._fixtures import get_store

from engine.combat_engine import (GEAR_RANK_RESOLVE_THRESHOLDS, CombatEngine,
                                  max_gear_rank_for_resolve)
from engine.rng import SeededRNG

from data.paths import GAME_DIR


@pytest.fixture(scope="module")
def store():
    return get_store()


def test_the_thresholds_are_read_off_every_shipped_upgrade_file():
    """One table is only correct if every class agrees with it. Re-reads the
    real files rather than trusting the constant — if a DLC or a patch ever
    ships a class with different gating, this is what catches it."""
    trees = 0
    for path in sorted((GAME_DIR / "upgrades" / "heroes").glob("*.upgrades.json")):
        raw = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        for tree in raw.get("trees", []):
            tid = str(tree.get("id") or "")
            if not (tid.endswith(".weapon") or tid.endswith(".armour")):
                continue
            got = tuple(r.get("prerequisite_resolve_level")
                        for r in tree.get("requirements", []))
            assert got == GEAR_RANK_RESOLVE_THRESHOLDS, (tid, got)
            trees += 1
    assert trees == 30, f"expected 15 classes x 2 trees, saw {trees}"


def test_the_resolve_to_rank_table_is_what_the_game_allows():
    """Rank 1 is free; each threshold passed buys one more."""
    assert {r: max_gear_rank_for_resolve(r) for r in range(7)} == {
        0: 1, 1: 2, 2: 3, 3: 4, 4: 4, 5: 5, 6: 5}


def test_the_three_brackets_match_the_spec(store):
    """Apprentice / Veteran / Champion, stated as the difficulty brackets
    rather than as raw resolve numbers — this is the form the requirement
    was given in and the form a regression would be noticed in."""
    apprentice = {max_gear_rank_for_resolve(r) for r in (1, 2)}
    veteran = {max_gear_rank_for_resolve(r) for r in (3, 4)}
    champion = {max_gear_rank_for_resolve(r) for r in (5, 6)}
    assert apprentice == {2, 3}, apprentice
    assert veteran == {4}, "both veteran resolves field rank 4"
    assert champion == {5}, "both champion resolves field rank 5"


def test_an_apprentice_party_is_no_longer_under_equipped(store):
    """The regression this fix exists for. A resolve-2 Crusader must be built
    on tier index 2 (rank 3), not the old `level - 1` = 1 (rank 2). Checked
    through `reset()` on a real stat, because the tier index is what selects
    the weapon/armour row and therefore the hero's actual numbers."""
    hd = store.heroes["crusader"]
    rank3 = hd.weapon_tiers[2]
    rank2 = hd.weapon_tiers[1]
    assert (rank3.dmg_min, rank3.dmg_max) != (rank2.dmg_min, rank2.dmg_max), \
        "premise: the two ranks differ, or this test proves nothing"

    engine = CombatEngine(store, SeededRNG(1))
    enemy = next(k for k in store.enemies if "cultist_brawler" in k)
    engine.reset(hero_defs=[hd], enemy_defs=[store.enemies[enemy]],
                 hero_levels=[2])
    hero = engine.heroes[0]
    assert hero.skill_tier == 2, "resolve 2 should field rank-3 gear"
    assert (hero.dmg_min, hero.dmg_max) == (rank3.dmg_min, rank3.dmg_max)


def test_veterans_and_champions_did_not_move(store):
    """The fix must be confined to the apprentice bracket. Veteran and
    champion parties were already right, and silently restatting them would
    invalidate comparisons for no reason."""
    hd = store.heroes["crusader"]
    engine = CombatEngine(store, SeededRNG(1))
    enemy = next(k for k in store.enemies if "cultist_brawler" in k)
    for level, expected_tier in ((4, 3), (6, 4)):
        engine.reset(hero_defs=[hd], enemy_defs=[store.enemies[enemy]],
                     hero_levels=[level])
        assert engine.heroes[0].skill_tier == expected_tier, level


def test_no_hero_levels_still_means_max_gear(store):
    """The backward-compatible default, which CLAUDE.md calls out explicitly:
    `hero_levels=None` is max tier and NO resist bonus, and is a different
    thing from an implicit level 6."""
    hd = store.heroes["crusader"]
    engine = CombatEngine(store, SeededRNG(1))
    enemy = next(k for k in store.enemies if "cultist_brawler" in k)
    engine.reset(hero_defs=[hd], enemy_defs=[store.enemies[enemy]])
    assert engine.heroes[0].skill_tier == len(hd.weapon_tiers) - 1


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
