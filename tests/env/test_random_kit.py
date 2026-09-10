"""Randomised trinkets and quirks.

Skills were already randomisable; the other two thirds of a hero's kit were
not. Fran's spec put all three together: *"Random: Selected skills (for non
stance heroes), Trinkets (Between the available ones for them, as the trinkets
can be class specific or generic), Quirks."*

Two things here are worth more than the code they test:

* **The rarity filter.** 294 of the 615 loaded trinkets are `kickstarter`
  rarity, which is its own award category and not something a dungeon hands
  out. Unfiltered, a "random trinket" would be close to a coin flip on an item
  no run can produce.
* **Quirks are near-absent today.** 516 of 534 scraped comp heroes carry zero
  positive quirks and 525 carry zero negative, so the engine models the
  subsystem fully and the policy has never once trained against it.
"""

import json
import sys
from collections import Counter
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests._fixtures import get_store

from env.expedition_env import (RANDOM_QUIRKS_PER_POLARITY,
                                RANDOM_TRINKETS_PER_HERO,
                                UNIVERSAL_TRINKET_RARITIES, ExpeditionEnv)

from data.paths import GAME_DIR


@pytest.fixture(scope="module")
def store():
    return get_store()


# FUNCTION scope, deliberately. A module-scoped env is SHARED, so its
# `_sampler_rng` carries whatever state the tests before it left -- and under
# xdist those tests are a different subset in a different order in every worker,
# which is how `test_fleeing_buys_no_recovery` came to fail about one run in
# three on 2026-08-27 and pass every time the file was run alone. Construction
# is 0.3 ms against a cached store, so module scope was buying nothing.
@pytest.fixture
def env(store):
    return ExpeditionEnv(store=store, sampler_seed=0, kit_random_p=1.0)


def test_the_universal_rarities_come_from_the_games_own_table():
    """`award_category: "universal"` is the game's own word for "drops in
    ordinary play". Re-read rather than trusted, so a DLC that adds a universal
    rarity fails here instead of being silently excluded from the pool."""
    path = GAME_DIR / "trinkets" / "base.rarities.trinkets.json"
    raw = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    universal = {r["id"] for r in raw["rarities"]
                 if r.get("award_category") == "universal"}
    assert universal == set(UNIVERSAL_TRINKET_RARITIES), universal


def test_kickstarter_trinkets_are_kept_out_of_the_pool(store, env):
    """The filter's whole reason for existing: they are 294 of 615."""
    ks = {t.id for t in store.trinkets.values() if t.rarity == "kickstarter"}
    assert len(ks) > 200, "premise changed; re-check the rarity table"
    drawn = {tid for _ in range(300) for tid in env._random_trinket_ids("crusader")}
    assert not (drawn & ks), sorted(drawn & ks)[:5]


def test_a_hero_only_gets_trinkets_it_could_equip(store, env):
    """Generic, or locked to this class — never another class's."""
    for hero_id in ("crusader", "vestal", "runaway", "duelist", "abomination"):
        for _ in range(80):
            for tid in env._random_trinket_ids(hero_id):
                req = store.trinkets[tid].class_requirements
                assert not req or hero_id in req, (hero_id, tid, req)


def test_two_distinct_trinkets(env):
    for _ in range(200):
        got = env._random_trinket_ids("houndmaster")
        assert len(got) == RANDOM_TRINKETS_PER_HERO
        assert len(set(got)) == len(got)


def test_a_quirk_sheet_is_never_self_contradictory(store, env):
    """`incompatible_quirks` is listed from ONE side only — `tough` names
    `fragile`, `fragile` does not name `tough`. Checking a single direction
    would field a hero who is both, which is two buffs on one stat pulling
    opposite ways and a state the game cannot reach."""
    for _ in range(400):
        sheet = env._random_quirk_ids()
        for a in sheet:
            for b in sheet:
                if a == b:
                    continue
                assert b not in store.quirks[a].incompatible_quirks, (a, b)
                assert a not in store.quirks[b].incompatible_quirks, (b, a)


def test_a_quirk_sheet_carries_both_polarities_and_no_disease(store, env):
    """A real hero has good and bad ones. Diseases are excluded: they are
    contracted, not part of a roster hero's standing quirk sheet."""
    lo, hi = RANDOM_QUIRKS_PER_POLARITY
    for _ in range(200):
        sheet = env._random_quirk_ids()
        pos = [q for q in sheet if store.quirks[q].is_positive]
        neg = [q for q in sheet if not store.quirks[q].is_positive]
        assert lo <= len(pos) <= hi, pos
        assert lo <= len(neg) <= hi, neg
        assert not any(store.quirks[q].is_disease for q in sheet)


def test_the_default_changes_nothing(store):
    """p=0.0 must return exactly the comp's own kit, for every comp in the
    pool — the same guarantee the loadout randomiser gives, and for the same
    reason: archived checkpoints keep their training distribution."""
    plain = ExpeditionEnv(store=store, sampler_seed=0)
    off = ExpeditionEnv(store=store, sampler_seed=0, kit_random_p=0.0)
    for name, _zone, heroes in plain._hero_pool:
        assert (off._resolve_trinkets_and_quirks(name, heroes)
                == plain._resolve_trinkets_and_quirks(name, heroes))


def test_randomising_the_kit_actually_changes_it(store):
    """The counterpart: at p=1.0 the comp's kit must NOT come back."""
    env = ExpeditionEnv(store=store, sampler_seed=0, kit_random_p=1.0)
    plain = ExpeditionEnv(store=store, sampler_seed=0)
    name, _zone, heroes = plain._hero_pool[0]
    assert (env._resolve_trinkets_and_quirks(name, heroes)
            != plain._resolve_trinkets_and_quirks(name, heroes))


def test_it_does_not_disturb_the_enemy_draw(store):
    """Fourth knob, same invariant: drawn from `_loadout_rng`, so the monsters
    and the torch a scenario produces are untouched and two arms of a
    comparison still face the same dungeon."""
    plain = ExpeditionEnv(store=store, sampler_seed=0)
    kitted = ExpeditionEnv(store=store, sampler_seed=0, kit_random_p=1.0)
    name, _zone, heroes = plain._hero_pool[0]
    for seed in (2, 4, 8):
        plain.seed_scenario(seed)
        kitted.seed_scenario(seed)
        plain._resolve_trinkets_and_quirks(name, heroes)
        kitted._resolve_trinkets_and_quirks(name, heroes)
        assert (plain._sample_enemy("cove", 3).enemy_ids
                == kitted._sample_enemy("cove", 3).enemy_ids), seed
        assert plain._sample_torch_level() == kitted._sample_torch_level(), seed


def test_a_pinned_scenario_reproduces_its_kit(store):
    env = ExpeditionEnv(store=store, sampler_seed=0, kit_random_p=1.0)
    name, _zone, heroes = env._hero_pool[0]
    env.seed_scenario(777)
    first = env._resolve_trinkets_and_quirks(name, heroes)
    env.seed_scenario(777)
    assert env._resolve_trinkets_and_quirks(name, heroes) == first


def test_every_drawn_id_resolves_to_real_buffs(store, env):
    """A trinket or quirk id the engine cannot resolve applies nothing and
    fails silently, which would make this whole knob a very elaborate no-op."""
    seen_t, seen_q = Counter(), Counter()
    for hero_id in ("crusader", "runaway", "occultist"):
        for _ in range(60):
            trinkets, quirks = env._random_kit(hero_id)
            for tid in trinkets:
                assert store.trinkets[tid].buffs, tid
                seen_t[tid] += 1
            for qid in quirks:
                assert qid in store.quirks, qid
                seen_q[qid] += 1
    assert len(seen_t) > 20 and len(seen_q) > 20, (len(seen_t), len(seen_q))


def test_p_is_clamped_rather_than_trusted(store):
    assert ExpeditionEnv(store=store, sampler_seed=0,
                         kit_random_p=9.0)._kit_random_p == 1.0
    assert ExpeditionEnv(store=store, sampler_seed=0,
                         kit_random_p=-3.0)._kit_random_p == 0.0


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
