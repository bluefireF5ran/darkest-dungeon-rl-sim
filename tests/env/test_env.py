"""Test Gym environment wrapper."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import numpy as np
from env import DarkestDungeonEnv
from env.gym_env import (
    ACTION_SPACE_SIZE, ACTION_MOVE_BASE, ACTION_PROVISION_BASE, ACTION_PASS,
    MAX_SKILL_SLOTS, RANKS, PROVISION_ACTION_ORDER,
)


def test_env_reset():
    env = DarkestDungeonEnv()
    obs, info = env.reset(seed=42, options={
        "hero_ids": ["crusader", "crusader"],
        "enemy_ids": ["cultist_brawler_A", "cultist_brawler_A"],
    })
    assert isinstance(obs, np.ndarray)
    assert obs.shape == env.observation_space.shape
    print("  PASS env_reset")


def test_env_step():
    env = DarkestDungeonEnv()
    env.reset(seed=42, options={
        "hero_ids": ["crusader"],
        "enemy_ids": ["cultist_brawler_A"],
    })
    obs, reward, terminated, truncated, info = env.step(1)
    assert isinstance(obs, np.ndarray)
    assert obs.shape == env.observation_space.shape
    print(f"  PASS env_step (done={terminated}, reward={reward})")


def test_action_masks_shape_and_dtype():
    env = DarkestDungeonEnv()
    env.reset(seed=42, options={
        "hero_ids": ["crusader", "crusader"],
        "enemy_ids": ["cultist_brawler_A", "cultist_brawler_A"],
    })
    mask = env.action_masks()
    assert mask.shape == (ACTION_SPACE_SIZE,)
    assert mask.dtype == bool
    print("  PASS action_masks_shape_and_dtype")


def test_action_masks_marks_exactly_the_valid_actions_true():
    """The mask must be True exactly on indices that map to a currently-legal
    action, and every legal engine action must be representable in the fixed
    action space.

    The old contract here was "True on 0..len(valid_actions)-1", because an
    action index was just a position in whatever list get_valid_actions()
    happened to return that turn. Indices now have fixed semantics
    (skill_slot x target_rank, move, provision, pass), so the mask is a
    statement about the board rather than about a list length — and the
    stronger property worth asserting is that the index<->Action mapping is
    faithful in both directions."""
    env = DarkestDungeonEnv()
    env.reset(seed=42, options={
        "hero_ids": ["crusader", "crusader"],
        "enemy_ids": ["cultist_brawler_A", "cultist_brawler_A"],
    })
    mask = env.action_masks()
    assert mask.shape == (ACTION_SPACE_SIZE,)
    assert set(np.where(mask)[0].tolist()) == set(env._action_map), \
        "mask must be True exactly where an index maps to a legal action"

    actor = env.engine.get_current_actor_id()
    slot_of = {sid: i for i, sid in enumerate(env.engine.get_hero_full_skill_ids(actor))}
    # Every legal action the engine offers is reachable through some index.
    for a in env._valid_actions:
        idx = env._action_index(a, slot_of)
        assert idx is not None, f"legal action {a} has no action-space index"
        assert mask[idx], f"index {idx} for {a} not marked legal"
    # And every open index round-trips back to a legal action.
    for idx, a in env._action_map.items():
        assert a in env._valid_actions, f"index {idx} maps to non-legal {a}"
    print(f"  PASS action_masks_marks_exactly_the_valid_actions_true (n={int(mask.sum())})")


def test_action_index_layout_is_stable_and_disjoint():
    """Each action category occupies its own index block, and a skill's index
    is fully determined by (slot, target rank) — the property the whole
    redesign exists to provide."""
    env = DarkestDungeonEnv()
    env.reset(seed=42, options={
        "hero_ids": ["crusader", "crusader"],
        "enemy_ids": ["cultist_brawler_A", "cultist_brawler_A"],
    })
    actor = env.engine.get_current_actor_id()
    full = env.engine.get_hero_full_skill_ids(actor)
    slot_of = {sid: i for i, sid in enumerate(full)}
    assert len(full) == MAX_SKILL_SLOTS, full

    from engine import Action
    # Same skill, different ranks -> different indices, RANKS apart in slot.
    a0 = Action(actor, full[2], 0)
    a1 = Action(actor, full[2], 1)
    assert env._action_index(a0, slot_of) == 2 * RANKS + 0
    assert env._action_index(a1, slot_of) == 2 * RANKS + 1
    # Categories don't overlap.
    assert env._action_index(Action(actor, "__move__", 3), slot_of) == ACTION_MOVE_BASE + 3
    assert env._action_index(Action(actor, "__provision_bandage__", 0), slot_of) == \
        ACTION_PROVISION_BASE + PROVISION_ACTION_ORDER.index("bandage")
    assert env._action_index(Action(actor, "__pass__", -1), slot_of) == ACTION_PASS
    print("  PASS action_index_layout_is_stable_and_disjoint")


def test_observation_encodes_which_hero_is_acting():
    """The single most important thing the old 50-float observation was
    missing: it had no current-actor signal at all, so the vector was
    identical whether the Crusader or the Vestal was choosing, and the
    policy could not condition on who it was controlling. Two different
    actors on an otherwise identical board must now produce different
    observations."""
    env = DarkestDungeonEnv()
    env.reset(seed=42, options={
        "hero_ids": ["crusader", "vestal"],
        "enemy_ids": ["cultist_brawler_A", "cultist_brawler_A"],
    })
    actor = env.engine.get_current_actor_id()
    assert actor is not None
    obs_a = env._get_obs().copy()

    other = next(h.id for h in env.engine.heroes if h.id != actor and h.is_alive())
    # Hand the turn to the other hero without changing anything else.
    env.engine.current_turn_index = env.engine.turn_order.index(
        next(c for c in env.engine.turn_order if c.id == other and c.is_hero))
    obs_b = env._get_obs()

    assert not np.array_equal(obs_a, obs_b), \
        "observation must change when a different hero is the actor"
    print("  PASS observation_encodes_which_hero_is_acting")


def test_action_masks_tracks_shrinking_valid_action_count_across_steps():
    """The whole point of masking is that the true count changes turn to
    turn (skills go on cooldown, per-battle-limit skills get used up,
    provisions run out) -- confirm the mask's True-count actually follows
    len(_valid_actions) rather than being frozen at reset-time."""
    env = DarkestDungeonEnv()
    env.reset(seed=1, options={
        "hero_ids": ["crusader", "crusader"],
        "enemy_ids": ["cultist_brawler_A", "cultist_brawler_A"],
    })
    seen_counts = set()
    done = False
    steps = 0
    while not done and steps < 30:
        n = len(env._valid_actions)
        mask = env.action_masks()
        assert mask.sum() == max(n, 1)
        seen_counts.add(mask.sum())
        obs, reward, terminated, truncated, info = env.step(0)
        done = terminated or truncated
        steps += 1
    assert len(seen_counts) >= 1
    print(f"  PASS action_masks_tracks_shrinking_valid_action_count_across_steps (counts seen: {sorted(seen_counts)})")


def test_env_full_episode():
    """A whole episode of random legal actions terminates cleanly.

    Asserted nothing until 2026-08-27: it played the episode and printed the
    result, so a run that hit the 100-step guard without ever finishing -- the
    exact "shuffles position forever instead of attacking" pathology `step()`'s
    no-clamp rule exists to prevent -- read as a pass.
    """
    env = DarkestDungeonEnv()
    obs, _info = env.reset(seed=7, options={
        "hero_ids": ["crusader", "crusader"],
        "enemy_ids": ["cultist_brawler_A", "cultist_brawler_A"],
    })
    env.action_space.seed(0)
    # Seeded for the same reason as tests/env/test_reward.py::_make_env:
    # `reset(seed=)` does not reach the action space, so an unseeded
    # `sample()` replays a different episode on every run.
    total_reward = 0.0
    steps = 0
    done = False
    while not done and steps < 100:
        action = env.action_space.sample()
        obs, reward, terminated, truncated, info = env.step(action)
        assert env.observation_space.contains(obs)
        total_reward += reward
        done = terminated or truncated
        steps += 1

    assert done, f"episode did not finish inside the 100-step guard ({steps} steps)"
    assert terminated and not truncated, "the env sets no truncation of its own"
    assert info.get("winner") in {"heroes", "enemies", "retreat"}
    assert steps > 1


if __name__ == "__main__":
    print("=== Gym Env Tests ===\n")
    for name, func in sorted(globals().items()):
        if name.startswith("test_"):
            try:
                func()
                print(f"  PASS {name}\n")
            except Exception as e:
                import traceback
                print(f"  FAIL {name}: {e}")
                traceback.print_exc()
                print()
    print("Done.")
