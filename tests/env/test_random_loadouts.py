"""Fielding a hero with a random legal 4-of-7 instead of its comp's curated four.

The policy only ever trained on the four skills some comp author picked, so the
same class holding a different four is off-distribution. The tutorial specialist
measured distribution at 36pp, which is larger than any fidelity fix this project
has landed -- so this is the knob that phase is built around.

The most important test here is `test_the_default_changes_nothing`. Every
archived checkpoint trained at p=0.0, and a randomiser that perturbs the default
path would invalidate them silently rather than deliberately.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests._fixtures import get_store

from env.expedition_env import SKILLS_PER_HERO, ExpeditionEnv


@pytest.fixture(scope="module")
def store():
    return get_store()


def _comp_with(store, predicate):
    env = ExpeditionEnv(store=store, sampler_seed=0)
    for name, _zone, heroes in env._hero_pool:
        if predicate(name, heroes):
            return name, heroes
    pytest.skip("no comp matching the predicate")


def test_the_default_changes_nothing(store):
    """p=0.0 must return exactly the curated loadout, for every comp in the
    pool. This is what keeps every archived checkpoint's training distribution
    intact until a retrain deliberately changes it."""
    plain = ExpeditionEnv(store=store, sampler_seed=0)
    off = ExpeditionEnv(store=store, sampler_seed=0, loadout_random_p=0.0)
    for name, _zone, heroes in plain._hero_pool:
        assert off._resolve_skill_ids(name, heroes) == plain._resolve_skill_ids(name, heroes)


def test_a_random_loadout_is_four_real_skills_of_that_hero(store):
    """Legal means: four, distinct, and actually in that hero's movepool. A
    skill from the wrong class would be silently dropped by the engine and the
    hero would fight with three."""
    env = ExpeditionEnv(store=store, sampler_seed=1, loadout_random_p=1.0)
    for hero_id, hero in store.heroes.items():
        real = {s.id for s in hero.skills}
        got = env._random_skill_ids(hero_id)
        assert len(got) == SKILLS_PER_HERO, hero_id
        assert len(set(got)) == len(got), hero_id
        assert set(got) <= real, (hero_id, set(got) - real)


def test_abomination_is_never_restricted(store):
    """He has all seven equipped in the real game, so there is no 4-of-7 to
    randomise. `_resolve_skill_ids` returns [] for him at any p -- randomising
    him would strip skills the game never lets you unequip."""
    env = ExpeditionEnv(store=store, sampler_seed=2, loadout_random_p=1.0)
    name, heroes = _comp_with(store, lambda n, hs: "abomination" in hs)
    resolved = env._resolve_skill_ids(name, heroes)
    for hero_id, skills in zip(heroes, resolved):
        if hero_id == "abomination":
            assert skills == [], "the Abomination was restricted"


def test_duplicate_classes_draw_independently(store):
    """37 of 133 loadouts repeat a class, and the whole reason skills resolve
    POSITIONALLY is that a hero_id-keyed dict collapses them. A randomiser that
    drew once per class would reintroduce exactly that bug."""
    name, heroes = _comp_with(
        store, lambda n, hs: len(hs) >= 3 and len(set(hs)) < len(hs))
    env = ExpeditionEnv(store=store, sampler_seed=3, loadout_random_p=1.0)
    resolved = env._resolve_skill_ids(name, heroes)
    assert len(resolved) == len(heroes)
    dup = next(h for h in heroes if heroes.count(h) > 1)
    draws = [tuple(s) for h, s in zip(heroes, resolved) if h == dup]
    assert len(set(draws)) > 1, f"all {dup} copies drew the same loadout: {draws}"


def test_a_pinned_scenario_reproduces_its_loadouts(store):
    """Loadouts reseed with the scenario, like the monsters and the torch --
    but through `seed_scenario`, which pins BOTH streams. This test originally
    reseeded `_sampler_rng` alone and broke the moment loadouts moved to their
    own generator, which is the argument for having one helper own it instead
    of three call sites each remembering."""
    name, heroes = _comp_with(store, lambda n, hs: "abomination" not in hs)
    env = ExpeditionEnv(store=store, sampler_seed=0, loadout_random_p=1.0)

    env.seed_scenario(1234)
    first = env._resolve_skill_ids(name, heroes)
    env.seed_scenario(1234)
    assert env._resolve_skill_ids(name, heroes) == first

    env.seed_scenario(9999)
    assert env._resolve_skill_ids(name, heroes) != first, "seed did not matter"


def test_randomising_loadouts_does_not_disturb_the_enemy_draw(store):
    """The property the whole comparison rests on. If the loadout draw came
    from `_sampler_rng`, a randomised arm would consume from the generator that
    also picks monsters and torch, and the two arms would face DIFFERENT
    dungeons -- measuring luck, which is the failure `tools/ab_compare.py`
    exists to prevent."""
    name, heroes = _comp_with(store, lambda n, hs: "abomination" not in hs)
    plain = ExpeditionEnv(store=store, sampler_seed=0)
    rand = ExpeditionEnv(store=store, sampler_seed=0, loadout_random_p=1.0)

    for seed in (11, 22, 33):
        plain.seed_scenario(seed)
        rand.seed_scenario(seed)
        plain._resolve_skill_ids(name, heroes)
        rand._resolve_skill_ids(name, heroes)
        # after resolving loadouts, the SCENARIO stream must be at the same
        # place in both, so every subsequent enemy and torch draw matches
        assert (plain._sample_enemy("crypts", 1).enemy_ids
                == rand._sample_enemy("crypts", 1).enemy_ids), seed
        assert plain._sample_torch_level() == rand._sample_torch_level(), seed


def test_p_is_clamped_rather_than_trusted(store):
    env = ExpeditionEnv(store=store, sampler_seed=0, loadout_random_p=5.0)
    assert env._loadout_random_p == 1.0
    env = ExpeditionEnv(store=store, sampler_seed=0, loadout_random_p=-1.0)
    assert env._loadout_random_p == 0.0


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
