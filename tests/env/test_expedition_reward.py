"""Tests for the expedition-level reward on ExpeditionSequenceEnv.

Covers both the original layer (Φ_expedition, a potential-based aggregate-
stress term; REWARD_HERO_DEATH, a flat one-time death penalty) and the
reward realignment that followed a 3M/6M-timestep training run where
zero-death rate stayed flat at 0-8% with final stress pinned near 200 —
diagnosis: REWARD_WIN (fires every internal combat) dominated the true
objective (never lose a hero, minimize final stress), and the stress term
was purely potential-based so it telescoped to ~0 over the episode and
never entered the objective at all. The realignment lowers the per-combat
win signal (self._reward_win), raises REWARD_HERO_DEATH, and adds two real
(non-potential) terminal terms — REWARD_EXPEDITION_COMPLETE and
TERMINAL_STRESS_PENALTY_WEIGHT — so survival and final stress actually
shape the optimum instead of just the learning dynamics. Mirrors the style
of tests/env/test_reward.py for the existing per-combat shaping: no fixtures,
states built via env.reset(options=...) + direct mutation.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import env.expedition_sequence_env as ese_mod
from env import ExpeditionSequenceEnv
from env.expedition_sequence_env import (
    REWARD_HERO_DEATH, REWARD_EXPEDITION_COMPLETE,
    TERMINAL_STRESS_PENALTY_WEIGHT, W_EXPEDITION_STRESS,
)


def _reset_2crusaders(env, seed=1, num_combats=3):
    return env.reset(seed=seed, options={
        "hero_ids": ["crusader", "crusader"],
        "zone_id": "crypts", "tier": 1, "length_band": "short", "num_combats": num_combats,
    })


def _force_expedition_terminal_step(env, kill_hero=False):
    """Force the current combat to end in a heroes' win this step (via
    monkeypatching CombatEngine.step, same trick as the death test below)
    so the surrounding ExpeditionSequenceEnv.step() call deterministically
    exercises its own end-of-combat / end-of-expedition branches without
    needing to control the full RNG pipeline down to a real victory."""
    original_engine_step = env.engine.step

    def forced_step(*a, **kw):
        result = original_engine_step(*a, **kw)
        env.engine.done = True
        env.engine.winner = "heroes"
        if kill_hero:
            env.engine.heroes[1].is_dead = True
        return result

    env.engine.step = forced_step
    return env.step(0)


# ── Φ_expedition (potential-based aggregate stress) ──


def test_expedition_potential_lower_with_higher_mean_stress():
    env = ExpeditionSequenceEnv(sampler_seed=1)
    _reset_2crusaders(env)
    phi_low = env._compute_expedition_potential()
    for h in env.engine.heroes:
        h.stress = 150
    phi_high = env._compute_expedition_potential()
    assert phi_high < phi_low, (phi_high, phi_low)
    print(f"  PASS test_expedition_potential_lower_with_higher_mean_stress ({phi_low} -> {phi_high})")


def test_expedition_potential_counts_dead_heroes_as_max_stress():
    """Fix for the perverse gradient: averaging over living heroes only
    used to make a dead hero's stress simply drop out of the mean, so
    killing off a stressed hero looked identical to (or better than)
    keeping them alive. Dead heroes must now count as max stress (1.0)."""
    env = ExpeditionSequenceEnv(sampler_seed=1)
    _reset_2crusaders(env)
    env.engine.heroes[0].stress = 20
    env.engine.heroes[1].stress = 20
    phi_both_alive = env._compute_expedition_potential()

    env.engine.heroes[1].is_dead = True
    env.engine.heroes[1].stress = 199  # irrelevant once dead -- always counts as 1.0
    phi_one_dead = env._compute_expedition_potential()
    assert phi_one_dead < phi_both_alive, (phi_one_dead, phi_both_alive)
    print("  PASS test_expedition_potential_counts_dead_heroes_as_max_stress")


def test_expedition_potential_worst_case_when_party_wiped():
    """A full wipe means every roster hero counts as max stress (1.0), so
    the potential is -1.0 -- the worst possible value, not a neutral 0.0.
    (The old living-heroes-only implementation special-cased an empty
    living list to 0.0; that special case is gone along with the rest of
    the living-only averaging it belonged to. step() still forces the
    shaping term itself to 0 at any true terminal step, independent of
    this method's own return value -- see phi_exp_after in step().)"""
    env = ExpeditionSequenceEnv(sampler_seed=1)
    _reset_2crusaders(env)
    for h in env.engine.heroes:
        h.is_dead = True
    assert env._compute_expedition_potential() == -1.0
    print("  PASS test_expedition_potential_worst_case_when_party_wiped")


def test_death_never_increases_expedition_potential():
    """No perverse incentive: killing a hero must never make the
    expedition potential less negative, regardless of their stress at the
    moment of death (death's fixed contribution, 1.0, is >= any living
    stress fraction, min(stress,200)/200 <= 1.0)."""
    env = ExpeditionSequenceEnv(sampler_seed=1)
    _reset_2crusaders(env)
    env.engine.heroes[0].stress = 30
    env.engine.heroes[1].stress = 190
    phi_before = env._compute_expedition_potential()

    env.engine.heroes[1].is_dead = True
    phi_after = env._compute_expedition_potential()
    assert phi_after <= phi_before, (phi_after, phi_before)
    print(f"  PASS test_death_never_increases_expedition_potential ({phi_before} -> {phi_after})")


def test_w_expedition_stress_weight_is_a_positive_constant():
    assert W_EXPEDITION_STRESS > 0
    print("  PASS test_w_expedition_stress_weight_is_a_positive_constant")


# ── REWARD_HERO_DEATH (flat, real, one-time) ──


def test_a_hero_dying_this_step_applies_the_flat_death_penalty():
    """Forcing a real death deterministically through the full action
    pipeline would require controlling both the deathblow roll AND
    monster target selection. Instead, deterministically test step()'s own
    newly-dead detection directly: monkeypatch engine.step to also kill a
    hero as part of the same call, so the death genuinely happens inside
    the window step() brackets with its dead_before/dead_after snapshot —
    exercising the real detection logic, not just re-deriving it."""
    env = ExpeditionSequenceEnv(sampler_seed=1)
    _reset_2crusaders(env)
    victim = env.engine.heroes[1]
    assert not victim.is_dead

    original_engine_step = env.engine.step

    def step_and_kill(*a, **kw):
        result = original_engine_step(*a, **kw)
        victim.is_dead = True
        return result

    env.engine.step = step_and_kill
    obs, reward, terminated, truncated, info = env.step(0)
    assert reward <= REWARD_HERO_DEATH + 1.0, \
        f"expected a step with a new hero death to be dominated by REWARD_HERO_DEATH, got {reward}"
    print(f"  PASS test_a_hero_dying_this_step_applies_the_flat_death_penalty (reward={reward})")


def test_no_death_step_reward_has_no_flat_death_component():
    """A step where nobody dies should never include a REWARD_HERO_DEATH-
    sized jump — sanity bound on ordinary per-step reward magnitude."""
    env = ExpeditionSequenceEnv(sampler_seed=1)
    _reset_2crusaders(env)
    obs, reward, terminated, truncated, info = env.step(0)
    assert reward > REWARD_HERO_DEATH + 1.0, reward
    print(f"  PASS test_no_death_step_reward_has_no_flat_death_component (reward={reward})")


# ── Realigned objective: death dominates, stress is real, completion bonus scoped ──


def test_all_alive_scores_higher_than_one_death_at_true_expedition_end():
    """The core realignment check: an expedition-ending win with everybody
    alive must score strictly higher than an otherwise-identical ending
    where one hero died this same step — REWARD_HERO_DEATH (-20) plus the
    extra TERMINAL_STRESS_PENALTY_WEIGHT contribution from the dead hero's
    max-stress accounting must outweigh the fact that both scenarios still
    get REWARD_EXPEDITION_COMPLETE (party isn't wiped in either)."""
    env_alive = ExpeditionSequenceEnv(sampler_seed=1)
    _reset_2crusaders(env_alive, num_combats=1)
    _, reward_all_alive, term_alive, _, _ = _force_expedition_terminal_step(env_alive, kill_hero=False)

    env_death = ExpeditionSequenceEnv(sampler_seed=1)
    _reset_2crusaders(env_death, num_combats=1)
    _, reward_one_death, term_death, _, _ = _force_expedition_terminal_step(env_death, kill_hero=True)

    assert term_alive and term_death
    assert reward_all_alive > reward_one_death, (reward_all_alive, reward_one_death)
    print(f"  PASS test_all_alive_scores_higher_than_one_death_at_true_expedition_end "
          f"({reward_all_alive} > {reward_one_death})")


def test_high_final_stress_scores_lower_than_low_final_stress():
    env_low = ExpeditionSequenceEnv(sampler_seed=1)
    _reset_2crusaders(env_low, num_combats=1)
    for h in env_low.engine.heroes:
        h.stress = 10
    _, reward_low, term_low, _, _ = _force_expedition_terminal_step(env_low, kill_hero=False)

    env_high = ExpeditionSequenceEnv(sampler_seed=1)
    _reset_2crusaders(env_high, num_combats=1)
    for h in env_high.engine.heroes:
        h.stress = 190
    _, reward_high, term_high, _, _ = _force_expedition_terminal_step(env_high, kill_hero=False)

    assert term_low and term_high
    assert reward_low > reward_high, (reward_low, reward_high)
    print(f"  PASS test_high_final_stress_scores_lower_than_low_final_stress ({reward_low} > {reward_high})")


def test_terminal_stress_penalty_is_a_real_non_telescoping_term():
    """Unlike Φ_expedition (potential-based, forced to 0 at the terminal
    step so its contribution is symmetric/cancels over the episode),
    TERMINAL_STRESS_PENALTY_WEIGHT must be a real term: zeroing it out via
    monkeypatch should change the terminal reward by exactly
    TERMINAL_STRESS_PENALTY_WEIGHT * mean_roster_stress_frac, proving the
    term actually reaches the returned reward rather than being absorbed
    or canceled by the potential-based shaping alongside it."""
    env = ExpeditionSequenceEnv(sampler_seed=1)
    _reset_2crusaders(env, num_combats=1)
    for h in env.engine.heroes:
        h.stress = 190
    frac = env._mean_roster_stress_frac()
    assert frac > 0.0

    original_weight = ese_mod.TERMINAL_STRESS_PENALTY_WEIGHT
    ese_mod.TERMINAL_STRESS_PENALTY_WEIGHT = 0.0
    try:
        _, reward_no_penalty, _, _, _ = _force_expedition_terminal_step(env, kill_hero=False)
    finally:
        ese_mod.TERMINAL_STRESS_PENALTY_WEIGHT = original_weight

    env2 = ExpeditionSequenceEnv(sampler_seed=1)
    _reset_2crusaders(env2, num_combats=1)
    for h in env2.engine.heroes:
        h.stress = 190
    _, reward_with_penalty, _, _, _ = _force_expedition_terminal_step(env2, kill_hero=False)

    delta = reward_no_penalty - reward_with_penalty
    # Measured AFTER the step, not before: `env.step(0)` is a real attack and
    # a crit relieves stress, so the fraction the penalty is charged on is the
    # post-step one. Both arms are the same seed and the same forced step, so
    # env2's final roster is env's. This used to read the pre-step fraction
    # and passed only while the opening turn happened not to change any
    # stress -- surprise reordering that turn is what exposed it.
    expected = original_weight * env2._mean_roster_stress_frac()
    assert abs(delta - expected) < 1e-6, (delta, expected)
    print(f"  PASS test_terminal_stress_penalty_is_a_real_non_telescoping_term (delta={delta}, expected={expected})")


def test_completion_bonus_only_on_true_expedition_end():
    """REWARD_EXPEDITION_COMPLETE must apply only when the expedition
    truly ends without a party wipe -- never on an internal combat
    transition (a win that isn't the last sampled combat), since that
    case resets `terminated` back to False and starts the next combat."""
    def run(num_combats, patch_value):
        e = ExpeditionSequenceEnv(sampler_seed=1)
        _reset_2crusaders(e, num_combats=num_combats)
        original = ese_mod.REWARD_EXPEDITION_COMPLETE
        ese_mod.REWARD_EXPEDITION_COMPLETE = patch_value
        try:
            _, reward, terminated, _, _ = _force_expedition_terminal_step(e, kill_hero=False)
        finally:
            ese_mod.REWARD_EXPEDITION_COMPLETE = original
        return reward, terminated

    # num_combats=2: this win is combat 0 of 2 -- an internal transition,
    # not the true expedition end.
    reward_mid_a, term_mid_a = run(num_combats=2, patch_value=0.0)
    reward_mid_b, term_mid_b = run(num_combats=2, patch_value=1000.0)
    assert not term_mid_a and not term_mid_b
    assert reward_mid_a == reward_mid_b, \
        f"internal combat transition must not apply REWARD_EXPEDITION_COMPLETE ({reward_mid_a} != {reward_mid_b})"

    # num_combats=1: this win IS the last (and only) combat -- true end.
    reward_last_a, term_last_a = run(num_combats=1, patch_value=0.0)
    reward_last_b, term_last_b = run(num_combats=1, patch_value=1000.0)
    assert term_last_a and term_last_b
    assert abs((reward_last_b - reward_last_a) - 1000.0) < 1e-6, (reward_last_a, reward_last_b)
    print("  PASS test_completion_bonus_only_on_true_expedition_end")


# -- the affliction penalty (2026-09-02) --------------------------------

def _afflicted_run(penalty, seed=3, steps=60):
    """One seeded episode with hero 0 afflicted from the first step, played by
    always taking the lowest legal action.

    Both arms force the SAME affliction, so both pay the same act-out draws and
    the RNG streams stay in step -- the only difference between them is the
    reward term. Forcing it in one arm only would diverge the trajectories and
    measure the divergence instead of the penalty.
    """
    env = ExpeditionSequenceEnv(sampler_seed=seed, affliction_penalty=penalty)
    _reset_2crusaders(env, seed=seed)
    env.engine.heroes[0].is_afflicted = True
    total = 0.0
    for _ in range(steps):
        legal = [i for i, ok in enumerate(env.action_masks()) if ok]
        _obs, reward, term, trunc, _info = env.step(legal[0])
        total += reward
        if term or trunc:
            break
    return total, env


def test_affliction_penalty_defaults_to_zero_and_charges_nothing():
    """The default has to be inert: this is a NON-potential term, so a nonzero
    default silently changes what every archived checkpoint's successor is
    optimising, and no comparison across it would mean anything."""
    from env.expedition_sequence_env import AFFLICTION_PENALTY_DEFAULT
    assert AFFLICTION_PENALTY_DEFAULT == 0.0

    env = ExpeditionSequenceEnv(sampler_seed=3)
    assert env.affliction_penalty == 0.0

    base, _ = _afflicted_run(0.0)
    same, _ = _afflicted_run(0.0)
    assert base == same
    print("  PASS test_affliction_penalty_defaults_to_zero_and_charges_nothing")


def test_an_affliction_is_charged_ONCE_however_long_it_lasts():
    """An affliction lasts the rest of the raid, so a per-step charge would
    price one hero's bad afternoon above four deaths. Charged once per hero per
    expedition -- the same shape REWARD_HERO_DEATH has."""
    base, _ = _afflicted_run(0.0)
    charged, env = _afflicted_run(20.0)
    assert env._afflicted_charged, "the sweep never saw the afflicted hero"
    assert round(base - charged, 6) == 20.0 * len(env._afflicted_charged), (
        base, charged, env._afflicted_charged)
    # ...and one hero was afflicted, so it is exactly one charge of 20.
    assert len(env._afflicted_charged) == 1
    assert round(base - charged, 6) == 20.0
    print("  PASS test_an_affliction_is_charged_ONCE_however_long_it_lasts")


def test_the_affliction_charge_is_NOT_a_potential_term():
    """`W_AFFLICTED` sits inside Phi and therefore cannot change the optimal
    policy at all -- that is the whole reason this term exists. A potential
    term telescopes to ~0 over an episode; this one must not, so the episode
    total has to move by the full penalty and stay moved."""
    base, _ = _afflicted_run(0.0)
    small, _ = _afflicted_run(5.0)
    large, _ = _afflicted_run(50.0)
    assert round(base - small, 6) == 5.0
    assert round(base - large, 6) == 50.0
    print("  PASS test_the_affliction_charge_is_NOT_a_potential_term")


if __name__ == "__main__":
    print("=== Expedition Reward Tests ===\n")
    for name, func in sorted(globals().items()):
        if name.startswith("test_"):
            try:
                func()
            except Exception as e:
                import traceback
                print(f"  FAIL {name}: {e}")
                traceback.print_exc()
    print("\nDone.")
