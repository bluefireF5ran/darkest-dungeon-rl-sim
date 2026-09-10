"""Parallel rollout envs must widen the sample, not clone one episode N times.

`ExpeditionEnv` draws its comp, its zone-matched enemy encounter and its torch
level from `_sampler_rng`, seeded by `sampler_seed`. So if every worker in a
`SubprocVecEnv` were built with the same `sampler_seed`, all N would walk the
same scenario sequence in lockstep: N times the samples, one episode's worth of
diversity, and a training distribution quietly NARROWER than the single-env run
it replaced -- while every throughput number looked great.

That failure is invisible to a smoke test (training runs, fps goes up, loss
falls), so it gets its own test. The rule is `sampler_seed + i` per worker,
with `None` staying `None`.

These use `n_envs<=2` and `DummyVecEnv` where possible: the point is the seed
arithmetic and the env contract, not throughput, and spawning 12 interpreters
per test would cost more than the whole rest of the file.
"""

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import train
from env.gym_env import ACTION_SPACE_SIZE


@pytest.mark.xdist_group("torch")
def test_one_env_stays_in_process():
    """A single env in a subprocess is strictly worse: same speed, plus IPC."""
    from stable_baselines3.common.vec_env import DummyVecEnv
    venv = train.make_vec_env("fixed", 1, sampler_seed=0)
    try:
        assert isinstance(venv, DummyVecEnv)
        assert venv.num_envs == 1
    finally:
        venv.close()


def test_workers_get_distinct_sampler_seeds():
    """The whole point of the fan-out: worker i must not replay worker 0.

    Asserted on `worker_sampler_seeds` rather than by spying on `make_env`:
    with SubprocVecEnv the factories run INSIDE the workers, so a parent-side
    monkeypatch observes nothing and would pass vacuously (it did).
    """
    seeds = train.worker_sampler_seeds(100, 4)
    assert seeds == [100, 101, 102, 103]
    assert len(set(seeds)) == 4, "workers would replay the same scenarios"


def test_none_stays_none_so_unseeded_runs_stay_unseeded():
    assert train.worker_sampler_seeds(None, 3) == [None, None, None]


def test_workers_actually_face_different_scenarios():
    """The behavioural half: distinct seeds must reach the sampler.

    `ExpeditionEnv` draws comp/enemies/torch from `_sampler_rng`, so two
    workers seeded differently should not open on an identical observation.
    Uses DummyVecEnv-sized n=2 through the real factory path.
    """
    venv = train.make_vec_env("expedition", 2, sampler_seed=0)
    try:
        obs = venv.reset()
        assert obs.shape[0] == 2
        assert not np.array_equal(obs[0], obs[1]), (
            "both workers opened on the same board -- sampler_seed is not "
            "being varied per worker")
    finally:
        venv.close()


@pytest.mark.xdist_group("torch")
def test_the_vec_env_masks_have_the_right_shape():
    """Masking has to survive vectorisation -- MaskablePPO calls it per env."""
    from sb3_contrib.common.maskable.utils import get_action_masks

    venv = train.make_vec_env("fixed", 2, sampler_seed=0)
    try:
        venv.reset()
        masks = get_action_masks(venv)
        assert masks.shape == (2, ACTION_SPACE_SIZE), masks.shape
        assert masks.any(axis=1).all(), "an env offered no legal action at reset"
    finally:
        venv.close()


def test_a_vec_step_advances_every_env():
    venv = train.make_vec_env("fixed", 2, sampler_seed=0)
    try:
        venv.reset()
        obs, rewards, dones, infos = venv.step(np.array([36, 36]))   # PASS
        assert obs.shape[0] == 2 and len(rewards) == 2 and len(infos) == 2
    finally:
        venv.close()


@pytest.mark.parametrize("mode", ["fixed", "expedition", "full_expedition"])
def test_every_mode_vectorises(mode):
    venv = train.make_vec_env(mode, 2, sampler_seed=0)
    try:
        obs = venv.reset()
        assert obs.shape[0] == 2
    finally:
        venv.close()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v", "-p", "no:xdist"]))
