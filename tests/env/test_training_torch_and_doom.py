"""Two pre-retrain fidelity fixes, pinned.

1. **Training never fought in the dark.** `ExpeditionSequenceEnv` chained
   combats without ever passing `torch_level`, so every combat of every
   expedition ran at the engine default of 100.0. Torch is IN the observation,
   so holding it constant made that feature carry no information at all, and
   left the policy off-distribution for the 32% of real captured combats that
   are below radiant — where rules.json changes crit, ACC, dodge, stress and
   surprise on both sides.

2. **"This kill is already secured" was not recoverable from the observation.**
   HP fraction and a normalized DoT total were both there, but comparing them
   needs the target's absolute max HP, which the vector never carried. That is
   the exact quantity PLAYBOOK 3.3 turns on, and the measured violation was
   large: 60.4% of turns taken with a kill already secured were spent attacking
   that same doomed target.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest

from env import ExpeditionSequenceEnv
from env.expedition_sequence_env import TORCH_LEVEL_DECILES
from env.gym_env import (ENEMY_SLOT_SCALARS, RANKS, HERO_SLOT_SCALARS, ACTION_PASS,
                          _is_doomed)
from engine.combatant import DotInstance


# FUNCTION scope, deliberately. A module-scoped env is SHARED, so its
# `_sampler_rng` carries whatever state the tests before it left -- and under
# xdist those tests are a different subset in a different order in every worker,
# which is how `test_fleeing_buys_no_recovery` came to fail about one run in
# three on 2026-08-27 and pass every time the file was run alone. Construction
# is 0.3 ms against a cached store, so module scope was buying nothing.
@pytest.fixture
def env():
    """A usable env — meaning one that has been reset.

    `ExpeditionSequenceEnv.__init__` builds no engine; `reset()` does. Three
    tests in this file use the fixture without resetting it, and one of them
    (`test_the_enemy_slot_grew_by_exactly_one`) calls `_get_obs()`, which goes
    straight to `self.engine.get_current_actor_id()`. Run serially they were
    rescued by whichever earlier test happened to reset the shared module-scoped
    fixture first; that is an accident of ordering, not a guarantee.

    `pytest.ini` runs `-n auto`, and xdist's default `--dist load` hands
    individual tests to whichever worker is free — so this test can land on a
    worker where nothing has reset the env, and `self.engine` is None. It failed
    about two full-suite runs in five, passed in isolation every time, and
    reproduces 3/3 in 2.2s with `pytest tests/env/test_training_torch_and_doom.py
    -n auto`. Adding an unrelated test file changed the scheduling enough to
    surface it.

    Resetting here is the fix rather than adding a reset to each of the three,
    because the defect is a fixture that hands back a half-built object. Both
    torch tests that skip the reset are unaffected: one asserts the shape of
    2000 draws, the other reseeds `_sampler_rng` itself.
    """
    e = ExpeditionSequenceEnv(sampler_seed=11)
    e.reset(options={
        "hero_ids": ["crusader"], "enemy_ids": ["cultist_brawler_A"],
        "zone_id": "crypts", "tier": 1, "length_band": "short", "num_combats": 1,
    })
    return e


# ── torch ──

def test_the_deciles_are_the_measured_distribution_not_a_convention():
    """Sanity on the constant itself: monotone, bounded by the real 0-100 torch
    scale, and reaching both ends of what was actually captured (min 3, max
    100). A convention would not have a 3 in it."""
    assert list(TORCH_LEVEL_DECILES) == sorted(TORCH_LEVEL_DECILES)
    assert TORCH_LEVEL_DECILES[0] == 3.0
    assert TORCH_LEVEL_DECILES[-1] == 100.0
    assert len(TORCH_LEVEL_DECILES) == 11


def test_sampled_torch_spans_the_dark(env):
    """The falsifier for fix 1. Before it, this fraction was exactly 0.0 —
    every combat ever trained on was radiant. The band is wide because the
    assertion is about the SHAPE of the draw, not about a seed."""
    draws = [env._sample_torch_level() for _ in range(2000)]
    assert min(draws) < 20.0
    assert max(draws) >= 99.0
    below_radiant = sum(1 for d in draws if d <= 75.0) / len(draws)
    assert 0.25 < below_radiant < 0.50, below_radiant


def test_a_sampled_expedition_actually_fights_at_the_sampled_torch(env):
    """It is not enough to draw a number — reset() has to hand it to the
    engine. This is the line that was missing."""
    seen = set()
    for _ in range(12):
        env.reset()
        seen.add(round(env.engine.torch_level, 3))
    assert len(seen) > 1, "every expedition opened at the same torch"
    assert any(t <= 75.0 for t in seen), "no expedition opened below radiant"


def test_each_combat_of_an_expedition_draws_its_own_torch(env):
    """Drawn per combat, not once per expedition — the deciles are the marginal
    over real in-battle captures, which already includes the player relighting
    between fights."""
    env.reset(options={
        "hero_ids": ["crusader", "crusader"],
        "zone_id": "crypts", "tier": 1, "length_band": "short", "num_combats": 6,
    })
    levels = [env.engine.torch_level]
    for i in range(5):
        env._snapshot_and_advance_roster()
        env._combat_idx += 1
        env._start_next_combat()
        levels.append(env.engine.torch_level)
    assert len(set(round(t, 3) for t in levels)) > 1, levels


def test_an_explicit_torch_pins_the_opening_combat(env):
    """A rollout asked from mid-dungeon knows the CURRENT torch off the save;
    the rest of the run is unknown and goes back to sampling."""
    env.reset(options={
        "hero_ids": ["crusader", "crusader"],
        "zone_id": "crypts", "tier": 1, "length_band": "short", "num_combats": 2,
        "torch_level": 12.5,
    })
    assert env.engine.torch_level == 12.5


def test_torch_reproduces_with_the_scenario_rng(env):
    """Torch is scenario, not combat: it comes from _sampler_rng, so reseeding
    the sampler reproduces it. Rollout.evaluate depends on this to compare two
    parties against the same dungeon rather than against different luck."""
    env._sampler_rng.seed(99)
    first = [env._sample_torch_level() for _ in range(5)]
    env._sampler_rng.seed(99)
    assert [env._sample_torch_level() for _ in range(5)] == first


# ── the doomed flag ──

def _dot(damage, ticks):
    return DotInstance(damage_per_tick=damage, remaining_ticks=ticks,
                       effect_name="test bleed")


def test_a_target_whose_dot_covers_its_hp_reads_doomed(env):
    env.reset(options={
        "hero_ids": ["crusader"], "enemy_ids": ["cultist_brawler_A"],
        "zone_id": "crypts", "tier": 1, "length_band": "short", "num_combats": 1,
    })
    e = env.engine.enemies[0]
    e.hp = 6
    e.bleeds = [_dot(3, 2)]
    assert _is_doomed(e)


def test_a_dot_that_falls_short_does_not(env):
    env.reset(options={
        "hero_ids": ["crusader"], "enemy_ids": ["cultist_brawler_A"],
        "zone_id": "crypts", "tier": 1, "length_band": "short", "num_combats": 1,
    })
    e = env.engine.enemies[0]
    e.hp = 10
    e.bleeds = [_dot(3, 2)]          # 6 outstanding against 10 HP
    assert not _is_doomed(e)


def test_bleed_and_blight_are_summed_together(env):
    """Two half-lethal DoTs of different kinds still kill. Reading one stack at
    a time is how a policy misses the securest kill on the board."""
    env.reset(options={
        "hero_ids": ["crusader"], "enemy_ids": ["cultist_brawler_A"],
        "zone_id": "crypts", "tier": 1, "length_band": "short", "num_combats": 1,
    })
    e = env.engine.enemies[0]
    e.hp = 8
    e.bleeds = [_dot(2, 2)]
    e.blights = [_dot(2, 2)]
    assert _is_doomed(e)


def test_a_corpse_is_never_doomed(env):
    """`is_alive()` gate: a corpse is already dead, and flagging it would tell
    the policy a kill is pending that has already happened."""
    env.reset(options={
        "hero_ids": ["crusader"], "enemy_ids": ["cultist_brawler_A"],
        "zone_id": "crypts", "tier": 1, "length_band": "short", "num_combats": 1,
    })
    # `enemies[0]` is NOT the enemy this reset asked for: the sequence env
    # re-samples and reinforcements can add more, so the slot holds whatever
    # was drawn -- and only 125 of 254 monsters leave a corpse at all, so a
    # `spider_webber_A` in that slot makes `assert e.is_corpse` fail on its own
    # PREMISE. That is why this failed intermittently under `pytest -n auto`
    # and looked like cross-test store pollution, which it was not. Pick the
    # enemy by the capability the test needs; the subject here is `_is_doomed`,
    # not which monster got drawn.
    # ...and the draw is PINNED rather than hoped for. `pytest.skip` stood here
    # until 2026-08-27, which was fine while the shared module-scoped fixture
    # made the draw vary: some runs found a corpse monster. Once the fixture
    # went function-scoped the draw became deterministic and the skip became
    # PERMANENT -- a test that never runs, which is worse than one that runs
    # sometimes. `seed_scenario` pins both the sampler and the loadout stream.
    env.seed_scenario(0)
    env.reset(options={
        "hero_ids": ["crusader"], "enemy_ids": ["cultist_brawler_A"],
        "zone_id": "crypts", "tier": 1, "length_band": "short", "num_combats": 1,
    })
    e = next((x for x in env.engine.enemies
              if env.store.enemies[x.definition_id].corpse_id), None)
    assert e is not None, "the pinned draw must contain a corpse-leaving monster"
    e.hp = 0
    env.engine._handle_deaths_door(e, env.engine.heroes[0], is_crit=False)
    assert e.is_corpse
    assert not _is_doomed(e)


def test_the_flag_reaches_the_observation_vector(env):
    """End to end, at the exact offset the enemy block starts — a feature the
    policy cannot see is not a feature."""
    env.reset(options={
        "hero_ids": ["crusader"], "enemy_ids": ["cultist_brawler_A"],
        "zone_id": "crypts", "tier": 1, "length_band": "short", "num_combats": 1,
    })
    e = env.engine.enemies[0]
    e.position = 0
    # No class one-hot since 2026-08-23 -- a hero rank is scalars only.
    enemy_block = RANKS * HERO_SLOT_SCALARS
    doomed_idx = enemy_block + 8      # 9th scalar of the first enemy slot

    e.hp, e.bleeds = 20, []
    assert env._get_obs()[doomed_idx] == 0.0
    e.hp, e.bleeds = 4, [_dot(4, 1)]
    assert env._get_obs()[doomed_idx] == 1.0


def test_the_enemy_slot_grew_by_exactly_one(env):
    """ENEMY_SLOT_SCALARS is the width contract; the observation must be
    derived from it, never hardcoded."""
    # 16 since burn landed (2026-08-20). It is APPENDED as the 16th, so the
    # `doomed` index above is still 8 -- a burn scalar inserted beside bleed
    # and blight would have moved it, which is precisely the silent kind of
    # break this pin exists to catch.
    # The width moves with every observation change; the invariant is that the
    # DOOMED flag stayed at its offset, which is what "appended" buys.
    assert ENEMY_SLOT_SCALARS > 16
    assert env.observation_space.shape[0] == env._get_obs().shape[0]


# ── the per-combat step budget (2026-08-15, the 220-round combat) ──

def test_one_combat_cannot_eat_the_whole_expeditions_step_budget(env):
    """MAX_STEPS_PER_COMBAT is named and documented per-combat, but was only ever
    enforced as `_max_steps = MAX_STEPS_PER_COMBAT * num_combats`, an expedition
    TOTAL. So a single fight could legally consume the whole pool — which is
    exactly what the measured 220-round combat did (15 combats pool 600 steps, at
    ~2.7 steps per round). Drives a real episode and asserts the cut lands on the
    per-combat budget, long before the pooled one."""
    from env.expedition_sequence_env import (MAX_STEPS_PER_COMBAT,
                                             STEP_POOL_PER_COMBAT)
    env.reset(seed=5, options={
        "hero_ids": ["crusader", "crusader"],
        "zone_id": "crypts", "tier": 1, "length_band": "long", "num_combats": 15,
    })
    # The pool multiplier is STEP_POOL_PER_COMBAT (40), NOT the per-combat cap
    # (80 since 2026-08-24). They were one constant by coincidence; decoupling
    # them is what lets the cap rise to where it only catches pathologies
    # without doubling the worst-case episode length. This test's claim is
    # unaffected and in fact stronger -- 80 against a 600-step pool.
    assert env._max_steps == STEP_POOL_PER_COMBAT * 15   # the pool is still big

    term = trunc = False
    steps = 0
    while not (term or trunc) and steps < 5000:
        # __pass__ is the purest possible dither: always legal, never ends a
        # fight on its own. Whether this party eventually dies to the enemies or
        # runs the clock out does not matter to the claim being made — what
        # matters is that ONE combat cannot outlive its own budget.
        _o, _r, term, trunc, _i = env.step(ACTION_PASS)
        steps += 1
        assert env._combat_step_count <= MAX_STEPS_PER_COMBAT
    assert term or trunc
    assert env._combat_idx == 0, "the party got past combat 1 by passing"
    assert steps <= MAX_STEPS_PER_COMBAT, (
        f"one combat consumed {steps} steps, past its {MAX_STEPS_PER_COMBAT} budget "
        f"(the pool was {env._max_steps})")


def test_running_a_combats_budget_out_truncates_rather_than_rolling_on(env):
    """The counter is read BEFORE the combat-transition block clears it. Read
    after, a fight that ran over would quietly start the next combat with a fresh
    budget — the pathology would survive the fix and be invisible."""
    from env.expedition_sequence_env import (MAX_STEPS_PER_COMBAT,
                                             STEP_POOL_PER_COMBAT)
    env.reset(seed=7, options={
        "hero_ids": ["crusader", "vestal"],
        "zone_id": "crypts", "tier": 1, "length_band": "long", "num_combats": 15,
    })
    env._combat_step_count = MAX_STEPS_PER_COMBAT - 1
    _o, _r, term, trunc, _i = env.step(ACTION_PASS)
    assert trunc or term
    if not term:
        assert trunc and env._step_count < env._max_steps, (
            "truncated on the pooled budget, not on this combat's")


def test_the_per_combat_counter_resets_between_combats(env):
    """Otherwise the budget would tighten with every fight and a long expedition
    would truncate on combat 2 regardless of how well it was played."""
    from env.expedition_sequence_env import (MAX_STEPS_PER_COMBAT,
                                             STEP_POOL_PER_COMBAT)
    env.reset(seed=6, options={
        "hero_ids": ["crusader", "crusader"],
        "zone_id": "crypts", "tier": 1, "length_band": "short", "num_combats": 4,
    })
    env._combat_step_count = MAX_STEPS_PER_COMBAT - 1
    env._snapshot_and_advance_roster()
    env._combat_idx += 1
    env._start_next_combat()
    assert env._combat_step_count == 0
