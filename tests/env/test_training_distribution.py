"""What the training distribution is actually made of.

Nothing guarded this before, which is exactly why two errors sat in it
unnoticed while every other part of the sampler was carefully tested:

* **`boss` and `stall` were sampled as ordinary encounters.** A `boss:` row in
  a mash file is a real boss (Siren, Prophet-with-pews), which the game shows
  only at the end of a boss quest; a `stall:` row is the reinforcement wave the
  engine summons when a party stalls. Measured before the fix, they were
  3.3-6.1% and ~9% of every draw -- roughly an eighth of all training combats
  were encounters that cannot occur in a corridor.
* **A comp only ever fought in the region it is tagged with.** So a Cove comp
  never met a skeleton, and because 78 of 145 comps are tagged Ruins, 55% of
  all expeditions were in the Ruins.

Both are distribution facts rather than crashes, so only an assertion about the
distribution can catch them.
"""

import re
import sys
from collections import Counter
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests._fixtures import get_store

from env.expedition_env import (CORE_ZONE_IDS, ENEMY_CATEGORIES_FOR_TRAINING,
                                ExpeditionEnv)


@pytest.fixture(scope="module")
def store():
    return get_store()


# ── encounter categories ──

def test_a_boss_is_never_a_random_encounter(store):
    """The Siren does not wander the corridors. Sampled rather than read off
    the constant, because the constant is only half the contract -- the pool
    has to be built from it too."""
    env = ExpeditionEnv(store=store, sampler_seed=0)
    for zone in CORE_ZONE_IDS:
        cats = Counter(env._sample_enemy(zone, 1).category for _ in range(2000))
        assert set(cats) <= {"hall", "room"}, (zone, dict(cats))


def test_the_reinforcement_wave_is_not_an_encounter_you_walk_into(store):
    """`stall:` rows are what the engine summons mid-combat against a stalling
    party. Drawing one as the opening encounter is a different mechanic
    wearing its data."""
    assert "stall" not in ENEMY_CATEGORIES_FOR_TRAINING
    assert "boss" not in ENEMY_CATEGORIES_FOR_TRAINING
    assert "named" not in ENEMY_CATEGORIES_FOR_TRAINING


def test_the_zone_rosters_are_still_the_right_monsters(store):
    """The counterpart guard: having narrowed the categories, each zone must
    still be dominated by its OWN faction. This is what says the narrowing
    removed the wrong encounters and not the zone's actual content."""
    env = ExpeditionEnv(store=store, sampler_seed=0)
    expected = {"cove": "fishman", "crypts": "skeleton",
                "warrens": "swine", "weald": "fungal"}
    for zone, faction in expected.items():
        mon = Counter()
        for _ in range(3000):
            for eid in env._sample_enemy(zone, 1).enemy_ids:
                mon[re.sub(r"_[A-D]$", "", eid)] += 1
        share = sum(v for k, v in mon.items() if k.startswith(faction)) / sum(mon.values())
        assert share > 0.15, f"{zone}: {faction} only {share:.1%} of draws"


# ── which region a comp is taken to ──

def test_the_default_keeps_every_comp_in_its_own_region(store):
    """p=0.0 must be exactly the old behaviour -- the comp's tag and nothing
    else -- or archived checkpoints silently change training distribution."""
    env = ExpeditionEnv(store=store, sampler_seed=0, cross_region_p=0.0)
    tagged = {name: zone for name, zone, _h in env._hero_pool}
    for _ in range(500):
        name, zone, _heroes = env._sample_comp()
        assert zone == tagged[name]


def test_cross_region_actually_spreads_across_all_four(store):
    env = ExpeditionEnv(store=store, sampler_seed=0, cross_region_p=1.0)
    seen = Counter(env._sample_comp()[1] for _ in range(3000))
    assert set(seen) == set(CORE_ZONE_IDS), dict(seen)
    # Uniform-ish: the worst zone must not be starved. The tagged
    # distribution is 55% Ruins, so this fails loudly if the tag leaks in.
    assert min(seen.values()) / max(seen.values()) > 0.7, dict(seen)


def test_choosing_a_region_does_not_disturb_the_enemy_draw(store):
    """The same property the loadout randomiser has to hold, and for the same
    reason: the region is drawn from `_loadout_rng`, so it cannot advance
    `_sampler_rng` and make two arms of a comparison face different monsters."""
    plain = ExpeditionEnv(store=store, sampler_seed=0)
    cross = ExpeditionEnv(store=store, sampler_seed=0, cross_region_p=1.0)
    for seed in (11, 22, 33):
        plain.seed_scenario(seed)
        cross.seed_scenario(seed)
        plain._sample_comp()
        cross._sample_comp()
        assert (plain._sample_enemy("crypts", 1).enemy_ids
                == cross._sample_enemy("crypts", 1).enemy_ids), seed
        assert plain._sample_torch_level() == cross._sample_torch_level(), seed


def test_a_pinned_scenario_reproduces_its_region(store):
    env = ExpeditionEnv(store=store, sampler_seed=0, cross_region_p=1.0)
    env.seed_scenario(4321)
    first = [env._sample_comp() for _ in range(10)]
    env.seed_scenario(4321)
    assert [env._sample_comp() for _ in range(10)] == first


def test_p_is_clamped_rather_than_trusted(store):
    assert ExpeditionEnv(store=store, sampler_seed=0,
                         cross_region_p=5.0)._cross_region_p == 1.0
    assert ExpeditionEnv(store=store, sampler_seed=0,
                         cross_region_p=-1.0)._cross_region_p == 0.0


# ── resolve within the difficulty bracket ──

def test_the_default_still_pins_the_party_at_the_top_of_its_bracket(store):
    """Off is the old behaviour exactly: four heroes, all at TIER_TO_HERO_LEVEL."""
    from env.expedition_env import TIER_TO_HERO_LEVEL
    env = ExpeditionEnv(store=store, sampler_seed=0)
    for tier in (1, 2, 3, 4, 5):
        assert env.sample_hero_levels(tier, 4) == [TIER_TO_HERO_LEVEL[tier]] * 4


def test_sampled_resolve_matches_the_specified_weights(store):
    """Apprentice 0/1/2 at 20/40/40, Veteran 3/4 at 40/60, Champion 5/6 at
    40/60 -- deliberately leaning high ("favour the heroes a bit"). A
    training-distribution choice, not a claim about the real game, which is
    why it is pinned here rather than derived from a file."""
    env = ExpeditionEnv(store=store, sampler_seed=0, resolve_sampling=True)
    expected = {1: {0: 0.2, 1: 0.4, 2: 0.4},
                3: {3: 0.4, 4: 0.6},
                5: {5: 0.4, 6: 0.6}}
    for tier, want in expected.items():
        seen = Counter(lvl for _ in range(4000)
                       for lvl in env.sample_hero_levels(tier, 4))
        total = sum(seen.values())
        for level, share in want.items():
            got = seen[level] / total
            assert abs(got - share) < 0.03, (tier, level, got, share)
        assert set(seen) == set(want), (tier, sorted(seen))


def test_each_hero_is_drawn_independently(store):
    """A party is four heroes off a roster, not one hero copied four times.
    Drawing once per party would field clones and reproduce exactly the
    top-of-bracket uniformity this replaces."""
    env = ExpeditionEnv(store=store, sampler_seed=0, resolve_sampling=True)
    parties = [tuple(env.sample_hero_levels(1, 4)) for _ in range(200)]
    assert any(len(set(p)) > 1 for p in parties), "every party was uniform"


def test_resolve_zero_is_reachable_and_costs_no_stress(store):
    """20% of apprentice heroes are resolve 0, and that must not quietly
    trip the underleveled-hero penalty (20 stress + 25% stress taken per
    level short). It fires on `quest_difficulty_level`, which the envs never
    pass -- this pins that they still don't."""
    from engine.combat_engine import CombatEngine
    from engine.rng import SeededRNG
    env = ExpeditionEnv(store=store, sampler_seed=0, resolve_sampling=True)
    levels = [lvl for _ in range(300) for lvl in env.sample_hero_levels(1, 4)]
    assert 0 in levels, "resolve 0 never came up"

    engine = CombatEngine(store, SeededRNG(1))
    enemy = next(k for k in store.enemies if "cultist_brawler" in k)
    # auto_resolve_to_hero=False, or the enemy acts first and lands
    # BRAWLER_STRESSDMG_D1 (+10% stress taken) -- which reads exactly like the
    # underleveled penalty this test is trying to rule out.
    engine.reset(hero_defs=[store.heroes["crusader"]],
                 enemy_defs=[store.enemies[enemy]], hero_levels=[0],
                 auto_resolve_to_hero=False)
    hero = engine.heroes[0]
    assert hero.stress == 0, hero.stress
    assert hero.stress_dmg_received_mult == 1.0
    assert hero.skill_tier == 0, "resolve 0 buys rank 1 gear"


def test_sampling_resolve_does_not_disturb_the_enemy_draw(store):
    """Third knob, same invariant: it draws from `_loadout_rng`, so the
    monsters and torch a scenario produces are untouched."""
    plain = ExpeditionEnv(store=store, sampler_seed=0)
    sampled = ExpeditionEnv(store=store, sampler_seed=0, resolve_sampling=True)
    for seed in (5, 6, 7):
        plain.seed_scenario(seed)
        sampled.seed_scenario(seed)
        plain.sample_hero_levels(1, 4)
        sampled.sample_hero_levels(1, 4)
        assert (plain._sample_enemy("weald", 3).enemy_ids
                == sampled._sample_enemy("weald", 3).enemy_ids), seed
        assert plain._sample_torch_level() == sampled._sample_torch_level(), seed



if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
