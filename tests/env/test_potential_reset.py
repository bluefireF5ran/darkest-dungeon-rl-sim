"""The per-combat potential must NOT be zeroed at an internal combat end.

Found 2026-08-23. `DarkestDungeonEnv.step` pinned `phi_after = 0.0` whenever
`engine.done`, which in an expedition fires 8-18 times per episode -- every
combat end, almost all of which `ExpeditionSequenceEnv.step` immediately
un-terminates. Pinning the endpoint makes one combat's shaping telescope to

    g*0 - phi_start + (g-1)*sum(intermediate)  ~=  -phi_start

Two consequences, and the second is the worse one:

* **A healthier party is charged more.** ~-0.33 per combat boundary between a
  healthy and a hurt party, ~-4.9 over a long expedition, against a -0.10
  threat-phase turn and a -20.0 hero death. That lands exactly on the measured
  defect: PLAYBOOK 3.3 says spend secured-kill turns on recovery, and the
  policy did so on 17.2% of them.
* **Everything inside the combat cancels.** With the endpoint pinned, the HP
  term, the stress term, guard, riposte and `W_ENEMY_STUNNED` contribute
  nothing to the combat's shaping total -- only `phi_start` survives, as a
  penalty. The whole potential function was being washed out once per fight.

It also broke the invariance argument: `CLAUDE.md` names exactly two
deliberate non-potential terms, and this was an undeclared third. The
expedition-level potential already zeroed only at the true end, so the
per-combat one was the odd one out rather than the precedent.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests._fixtures import get_store

from env.gym_env import DarkestDungeonEnv, SHAPING_GAMMA
from env.expedition_sequence_env import ExpeditionSequenceEnv
import pytest

store = get_store()


def _expedition(seed=0, combats=4):
    env = ExpeditionSequenceEnv(store=store, sampler_seed=0)
    env.reset(seed=seed, options={"length_band": "short", "num_combats": combats})
    return env


def test_a_single_combat_env_is_unchanged():
    """`DarkestDungeonEnv` and `ExpeditionEnv` are one combat per episode, so
    for them the combat end IS the episode end. The fix must be invisible
    there -- if this moves, the base env's reward changed too."""
    env = DarkestDungeonEnv(store=store)
    env.reset(seed=0)
    assert env._is_episode_terminal() is env.engine.done
    env.engine.done = True
    assert env._is_episode_terminal() is True
    print("  PASS test_a_single_combat_env_is_unchanged")


def test_an_internal_combat_end_is_not_an_episode_end():
    """The whole bug in one assertion. `engine.done` is True and the episode
    is NOT over, so the potential must not be pinned."""
    env = _expedition(combats=4)
    env._combat_idx = 0
    env.engine.done = True
    env.engine.winner = "heroes"
    assert env.engine.done is True
    assert env._is_episode_terminal() is False
    print("  PASS test_an_internal_combat_end_is_not_an_episode_end")


def test_the_last_combat_ends_the_episode():
    env = _expedition(combats=4)
    env._combat_idx = env._num_combats - 1
    env.engine.done = True
    env.engine.winner = "heroes"
    assert env._is_episode_terminal() is True
    print("  PASS test_the_last_combat_ends_the_episode")


def test_a_wipe_ends_the_episode_wherever_it_happens():
    env = _expedition(combats=8)
    env._combat_idx = 1
    for h in env.engine.heroes:
        h.hp = 0
        h.is_dead = True
    env.engine.done = True
    env.engine.winner = "enemies"
    assert env._is_episode_terminal() is True
    print("  PASS test_a_wipe_ends_the_episode_wherever_it_happens")


def test_a_retreat_from_the_LAST_combat_does_not_end_it():
    """A fled room stays on the map and the party re-takes it -- the same rule
    that stops a retreat being a paid skip. `step` sets `terminated = False`
    and restarts the encounter, so the potential must not be pinned either.
    This is the case a naive `last_combat` test gets wrong."""
    env = _expedition(combats=4)
    env._combat_idx = env._num_combats - 1
    env.engine.done = True
    env.engine.winner = "retreat"
    assert env._is_episode_terminal() is False
    print("  PASS test_a_retreat_from_the_LAST_combat_does_not_end_it")


@pytest.mark.xdist_group("torch")
def test_the_predicate_is_actually_wired_into_the_reward():
    """A predicate nothing reads is the failure mode this whole session kept
    finding, so this checks the wiring rather than the logic: force it True and
    the reward on the very same transition must change.

    The delta is `SHAPING_GAMMA * Φ(board)` -- the term that was being thrown
    away at every combat boundary.
    """
    import numpy as np
    from sb3_contrib.common.maskable.utils import get_action_masks

    def run(force_terminal):
        env = ExpeditionSequenceEnv(store=store, sampler_seed=0)
        obs, _ = env.reset(seed=11, options={"length_band": "short",
                                             "num_combats": 6})
        if force_terminal:
            env._is_episode_terminal = lambda: env.engine.done
        rewards = []
        for _ in range(400):
            mask = get_action_masks(env)
            a = int(np.flatnonzero(mask)[0])
            was = env._combat_idx
            obs, r, term, trunc, _i = env.step(a)
            rewards.append((was, env._combat_idx, r))
            if term or trunc:
                break
        return rewards

    fixed = run(False)
    broken = run(True)
    # The two runs are identical up to the reward, because the action choice is
    # deterministic and the engine RNG is seeded the same way.
    assert len(fixed) == len(broken), "the two runs diverged; not a reward test"
    transitions = [i for i, (a, b, _r) in enumerate(fixed) if a != b]
    assert transitions, "no internal combat transition happened in this episode"
    moved = [i for i in transitions
             if abs(fixed[i][2] - broken[i][2]) > 1e-9]
    assert moved, ("the reward at every internal transition is identical with "
                   "and without the fix -- the predicate is not reaching "
                   "phi_after")
    i = moved[0]
    delta = fixed[i][2] - broken[i][2]
    assert delta > 0, ("the fix should ADD the discarded potential back, so "
                       f"the reward must rise; it moved {delta:+.4f}")
    print(f"  PASS test_the_predicate_is_actually_wired_into_the_reward "
          f"({len(moved)}/{len(transitions)} transitions moved, first {delta:+.4f})")


def test_the_shaping_over_a_combat_is_no_longer_pinned_to_its_start():
    """The deeper property. With the endpoint pinned, a combat's shaping total
    is `-phi_start` regardless of what happened inside -- so two combats with
    the same start and very different endings score the same. Unpinned, the
    ending has to matter.

    Checked directly on the arithmetic rather than through a rollout, because
    a rollout cannot hold `phi_start` fixed while varying the ending.
    """
    g = SHAPING_GAMMA
    phi = [0.30, 0.28, 0.26, 0.24]          # a combat going badly
    good_end, bad_end = 0.45, 0.05

    def total(seq, end, pinned):
        after = [*seq[1:], 0.0 if pinned else end]
        return sum(g * a - b for a, b in zip(after, seq))

    pinned_gap = abs(total(phi, good_end, True) - total(phi, bad_end, True))
    live_gap = abs(total(phi, good_end, False) - total(phi, bad_end, False))
    assert pinned_gap < 1e-9, "pinned should erase the ending entirely"
    assert live_gap > 0.3, f"the ending must reach the total; gap {live_gap:.3f}"
    print(f"  PASS test_the_shaping_over_a_combat_is_no_longer_pinned_to_its_start "
          f"(pinned gap {pinned_gap:.3f}, live gap {live_gap:.3f})")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
