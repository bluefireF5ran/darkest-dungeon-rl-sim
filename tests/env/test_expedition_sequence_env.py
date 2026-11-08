"""Tests for ExpeditionSequenceEnv (env/expedition_sequence_env.py) — one
Gym episode = one full N-combat expedition, chaining CombatEngine.reset()
calls internally with HP/stress/Death's-Door/mortality-tier carried
forward (CombatEngine.reset(carry_over=...)), instead of ExpeditionEnv's
single randomized combat per episode.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from env import ExpeditionSequenceEnv
from env.expedition_sequence_env import (
    EXPEDITION_LENGTH_BANDS, MAX_STEPS_PER_COMBAT, STEP_POOL_PER_COMBAT, EXPEDITION_CONTEXT_FEATURES,
)
from env.gym_env import ACTION_PASS
import pytest


def _pick_attack_idx(env):
    """Same 'genuine enemy-targeting attack' heuristic used elsewhere in
    this test suite (e.g. tests/engine/test_bonus_initiative.py) — avoids getting
    stuck looping self-buffs/Move/provisions instead of actually fighting.

    Returns a fixed ACTION-SPACE index (env._action_map key), not a position
    in env._valid_actions: action indices now have stable semantics
    (skill_slot x target_rank) rather than being list offsets."""
    engine = env.engine
    for idx in sorted(env._action_map):
        a = env._action_map[idx]
        if a.skill_id.startswith("__"):
            continue
        if engine._is_ally_skill(a.skill_id):
            continue
        if a.target_id < 0:
            continue
        return idx
    return next(iter(sorted(env._action_map)), ACTION_PASS)


def test_reset_samples_a_valid_expedition_length():
    env = ExpeditionSequenceEnv(sampler_seed=1)
    for seed in range(10):
        env.reset(seed=seed)
        lo, hi = EXPEDITION_LENGTH_BANDS[env._length_band]
        assert lo <= env._num_combats <= hi, (env._length_band, env._num_combats)
    print("  PASS test_reset_samples_a_valid_expedition_length")


def test_observation_extends_base_with_expedition_context():
    """This class's observation is the per-combat one plus its own
    expedition-context block; the base width is no longer a magic 50 (it's
    derived from the rank/skill-slot layout and the class/type vocabularies),
    so assert the relationship rather than a hardcoded total."""
    env = ExpeditionSequenceEnv(sampler_seed=1)
    obs, _ = env.reset(seed=1)
    expected = env.base_obs_size + EXPEDITION_CONTEXT_FEATURES
    assert obs.shape == (expected,), obs.shape
    assert env.observation_space.shape == (expected,)
    print(f"  PASS test_observation_extends_base_with_expedition_context ({expected} wide)")


def test_explicit_options_bypass_sampling():
    env = ExpeditionSequenceEnv(sampler_seed=1)
    env.reset(seed=1, options={
        "hero_ids": ["crusader", "crusader"],
        "zone_id": "crypts", "tier": 1, "length_band": "short", "num_combats": 2,
    })
    assert env._num_combats == 2
    assert env._tier == 1
    assert [h.definition_id for h in env.engine.heroes] == ["crusader", "crusader"]
    print("  PASS test_explicit_options_bypass_sampling")


def test_episode_spans_multiple_internal_combats_when_party_survives():
    env = ExpeditionSequenceEnv(sampler_seed=3)
    env.reset(seed=3, options={
        "hero_ids": ["crusader", "crusader", "crusader", "crusader"],
        "zone_id": "crypts", "tier": 1, "length_band": "short", "num_combats": 3,
    })
    steps = 0
    terminated = truncated = False
    max_combat_idx_seen = 0
    while not (terminated or truncated) and steps < 2000:
        steps += 1
        idx = _pick_attack_idx(env)
        obs, reward, terminated, truncated, info = env.step(idx)
        max_combat_idx_seen = max(max_combat_idx_seen, env._combat_idx)
    assert terminated, "expected the expedition to finish, not time out, with an easy tier-1 comp"
    assert max_combat_idx_seen == 2, f"expected all 3 combats (idx 0,1,2) to run, saw up to {max_combat_idx_seen}"
    assert sum(1 for h in env.engine.heroes if h.is_alive()) == 4
    print(f"  PASS test_episode_spans_multiple_internal_combats_when_party_survives ({steps} steps)")


def test_trinkets_reapply_at_the_start_of_every_internal_combat():
    """Trinkets/quirks are permanent-for-the-expedition equipment, unlike
    buffs/DoTs which correctly reset per combat -- but CombatEngine.reset()
    rebuilds a fresh CombatantState from scratch every individual combat,
    so they'd silently vanish after the first fight without being
    reapplied each time _start_next_combat() runs. Verified directly: real
    comp "Pigpen Cleanup"'s Hellion carries "Thirsting Blade"
    (com_hel_thirsting_blade), whose +15% ACC buff is unconditional
    (checked here rather than its own +8% crit-vs-bleeding-target buff,
    which is real but conditional -- rule_type="actorStatus", only active
    while the Hellion's *target* is currently bleeding, correctly inactive
    at a fresh reset before any attack; see CombatEngine._is_conditional_
    rule_active); confirm it's still present after the expedition's second
    combat starts, not just the first."""
    env = ExpeditionSequenceEnv(sampler_seed=1)
    env.reset(seed=1, options={
        "hero_ids": ["hellion", "houndmaster", "plague_doctor", "arbalest"],
        "comp_name": "Pigpen Cleanup", "zone_id": "warrens", "tier": 1,
        "length_band": "short", "num_combats": 2,
    })
    hellion_idx = [h.definition_id for h in env.engine.heroes].index("hellion")
    acc_combat_1 = env.engine.heroes[hellion_idx].atk_bonus

    baseline_env = ExpeditionSequenceEnv(sampler_seed=1)
    baseline_env.reset(seed=1, options={
        "hero_ids": ["hellion", "houndmaster", "plague_doctor", "arbalest"],
        "zone_id": "warrens", "tier": 1, "length_band": "short", "num_combats": 2,
    })
    baseline_acc = baseline_env.engine.heroes[hellion_idx].atk_bonus
    assert acc_combat_1 > baseline_acc, (acc_combat_1, baseline_acc)  # sanity: trinket applied

    steps = 0
    while env._combat_idx == 0 and steps < 500:
        steps += 1
        idx = _pick_attack_idx(env)
        obs, reward, terminated, truncated, info = env.step(idx)
        if terminated or truncated:
            break
    assert env._combat_idx >= 1, "expected the easy tier-1 first combat to end within 500 steps"
    hellion_idx_2 = [h.definition_id for h in env.engine.heroes].index("hellion")
    acc_combat_2 = env.engine.heroes[hellion_idx_2].atk_bonus
    assert acc_combat_2 == acc_combat_1, (acc_combat_1, acc_combat_2)
    print(f"  PASS test_trinkets_reapply_at_the_start_of_every_internal_combat "
          f"(ACC bonus {acc_combat_1} preserved into combat #2)")


def test_hero_death_does_not_end_the_episode():
    """Put the party on Death's Door with no death_blow_resist, so the first
    hit a hero takes kills, then watch a hero die mid-expedition and the
    survivors carry on. (It was 1 HP, which leaned on a turn-start deathblow
    roll the game does not make -- changelog 2026-10-11.)

    This asserted NOTHING until 2026-08-27 -- it played the episode out and
    printed a summary, so it could only ever fail by raising. Both halves of the
    claim are checked now: the step a hero dies on does not terminate while
    others live, and the shrunken party goes on to finish the expedition.
    """
    env = ExpeditionSequenceEnv(sampler_seed=5)
    env.reset(seed=5, options={
        "hero_ids": ["crusader", "crusader", "crusader", "crusader"],
        "zone_id": "crypts", "tier": 1, "length_band": "short", "num_combats": 2,
    })
    for h in env.engine.heroes:
        h.hp = 0
        h.is_deaths_door = True
        h.death_blow_resist = 0.0
    original_count = len(env.engine.heroes)

    steps = 0
    survived_a_death = 0
    terminated = truncated = False
    while not (terminated or truncated) and steps < 2000:
        steps += 1
        idx = _pick_attack_idx(env)
        obs, reward, terminated, truncated, info = env.step(idx)
        living = [h for h in env.engine.heroes if h.is_alive()]
        if not living:
            break  # party wipe is a legitimate true ending, stop watching
        # Someone has died, someone is still standing: the episode must go on.
        if len(env.engine.heroes) < original_count and not terminated:
            survived_a_death += 1

    assert survived_a_death, (
        "no step was observed where the party had lost a hero and kept playing "
        f"-- the scenario stopped teaching anything (steps={steps}, "
        f"heroes={len(env.engine.heroes)}/{original_count})")
    # ...and the survivors finished the expedition rather than the death ending it.
    assert len(env.engine.heroes) < original_count
    assert any(h.is_alive() for h in env.engine.heroes)
    assert info.get("winner") == "heroes"


def test_food_quantities_come_from_the_shipped_store():
    """`FOOD_AMOUNTS` was an invented 2/3/4 -- nine times too small. The real
    per-length stock is the empty-id `provision` row in
    `default_store_inventory_item_lists`, and the SAME rows carry the firewood
    column 0/1/2 that `FIREWOOD_PER_BAND` already treats as real data. One
    column of those rows was trusted and the other invented."""
    from env.expedition_sequence_env import FOOD_AMOUNTS
    assert FOOD_AMOUNTS == {"short": 18, "medium": 24, "long": 36}
    env = ExpeditionSequenceEnv(sampler_seed=1)
    env.reset(seed=1, options={
        "hero_ids": ["crusader", "crusader"],
        "zone_id": "crypts", "tier": 1, "length_band": "long", "num_combats": 2,
    })
    assert env._provision_inventory["food"] == 36
    print("  PASS test_food_quantities_come_from_the_shipped_store")


def test_a_hunger_check_costs_one_food_PER_HERO():
    """Food is not a per-combat auto-heal. A hunger check feeds the whole
    party at one food each, which is what makes 36 a necessity rather than a
    luxury -- and what makes a full-HP party still pay for it."""
    from env.expedition_sequence_env import (HUNGER_HEAL_FRACTION,
                                             HUNGER_CHECK_CHANCE)
    env = ExpeditionSequenceEnv(sampler_seed=1)
    env.reset(seed=1, options={
        "hero_ids": ["crusader", "crusader", "vestal", "hellion"],
        "zone_id": "crypts", "tier": 1, "length_band": "long", "num_combats": 3,
    })
    # FULL HP, and that is load-bearing rather than incidental. Deliberate
    # eating (2026-08-24) spends food at this same between-rooms moment, so a
    # hurt party would spend on BOTH and this test could no longer see what the
    # check alone costs. A full party declines to eat -- and "a full-HP party
    # still pays for the check" is exactly the claim in the docstring.
    for h in env.engine.heroes:
        h.hp = h.max_hp
    alive = sum(1 for h in env.engine.heroes if h.is_alive())
    before = env._provision_inventory["food"]

    # Force the check rather than waiting for the roll.
    env.engine.rng.random = lambda: 0.0
    env._snapshot_and_advance_roster()

    assert env._meals_eaten == 0, "a full party ate; the check is not isolated"
    spent = before - env._provision_inventory["food"]
    assert spent == alive, f"spent {spent} food for {alive} heroes"
    assert env._hunger_checks == 1
    assert env._hunger_starved == 0
    for orig_idx, h in zip(env._alive_indices, env.engine.heroes):
        carried = env._carry_over_by_index[orig_idx].hp
        assert carried >= h.hp, "a fed hero must not lose HP"
    print(f"  PASS test_a_hunger_check_costs_one_food_PER_HERO ({spent} for {alive})")


def test_starving_costs_four_times_what_eating_gives():
    """`hallway_hunger_starve_HPdmg` 0.20 against `hallway_hunger_HPrestore`
    0.05. That asymmetry is the whole reason food is insurance rather than
    healing, and the old model -- which could never run out -- had none of it."""
    from env.expedition_sequence_env import (HUNGER_HEAL_FRACTION,
                                             HUNGER_STARVE_FRACTION)
    assert HUNGER_STARVE_FRACTION == 4 * HUNGER_HEAL_FRACTION

    env = ExpeditionSequenceEnv(sampler_seed=1)
    env.reset(seed=1, options={
        "hero_ids": ["crusader", "crusader"],
        "zone_id": "crypts", "tier": 1, "length_band": "long", "num_combats": 3,
    })
    env._provision_inventory["food"] = 0          # nothing to eat
    for h in env.engine.heroes:
        h.hp = h.max_hp
    env.engine.rng.random = lambda: 0.0
    env._snapshot_and_advance_roster()

    assert env._hunger_starved == 1
    for orig_idx, h in zip(env._alive_indices, env.engine.heroes):
        carried = env._carry_over_by_index[orig_idx].hp
        assert carried < h.max_hp, "a starving hero must lose HP"
        assert carried == max(1, h.max_hp - round(h.max_hp * HUNGER_STARVE_FRACTION))
    print("  PASS test_starving_costs_four_times_what_eating_gives")


def test_starvation_is_never_lethal():
    """A death arriving between combats has nothing that can respond to it --
    no heal, no retreat, no deathblow roll. The real game does not kill by
    starvation either."""
    env = ExpeditionSequenceEnv(sampler_seed=1)
    env.reset(seed=1, options={
        "hero_ids": ["crusader", "crusader"],
        "zone_id": "crypts", "tier": 1, "length_band": "long", "num_combats": 3,
    })
    env._provision_inventory["food"] = 0
    for h in env.engine.heroes:
        h.hp = 1
    env.engine.rng.random = lambda: 0.0
    env._snapshot_and_advance_roster()
    for orig_idx in env._alive_indices:
        assert env._carry_over_by_index[orig_idx].hp >= 1
    print("  PASS test_starvation_is_never_lethal")


def test_self_truncates_based_on_num_combats():
    env = ExpeditionSequenceEnv(sampler_seed=1)
    env.reset(seed=1, options={
        "hero_ids": ["crusader", "crusader"],
        "zone_id": "crypts", "tier": 1, "length_band": "short", "num_combats": 2,
    })
    # The pool multiplier is STEP_POOL_PER_COMBAT, not the per-combat cap.
    # They were the same constant until 2026-08-24 and are now separate
    # concerns: the cap bounds ONE runaway fight, the pool bounds the run.
    assert env._max_steps == STEP_POOL_PER_COMBAT * 2
    print("  PASS test_self_truncates_based_on_num_combats")


@pytest.mark.xdist_group("torch")
def test_check_env_gymnasium_api_compliance():
    from stable_baselines3.common.env_checker import check_env
    env = ExpeditionSequenceEnv(sampler_seed=1)
    check_env(env, warn=True)
    print("  PASS test_check_env_gymnasium_api_compliance")


if __name__ == "__main__":
    print("=== ExpeditionSequenceEnv Tests ===\n")
    for name, func in sorted(globals().items()):
        if name.startswith("test_"):
            try:
                func()
            except Exception as e:
                import traceback
                print(f"  FAIL {name}: {e}")
                traceback.print_exc()
    print("\nDone.")
