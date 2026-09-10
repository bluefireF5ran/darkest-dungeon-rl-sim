"""The env stack `train.py` actually builds must survive its own dependencies.

This file exists because of a silent breakage, not a hypothetical one. Every
mode's env was wrapped with `ActionMasker(env, lambda e: e.action_masks())`,
which depends on `gymnasium.Wrapper.__getattr__` forwarding attribute lookups
down to the inner env. Gymnasium removed that forwarding in 1.x, and
`requirements.txt` pinned `gymnasium>=1.0` with no ceiling — so a plain
`pip install` eventually produced:

    AttributeError: 'TimeLimit' object has no attribute 'action_masks'

on the first rollout step of `train.py --expedition` and of the default fixed
mode. `--full-expedition` kept working the whole time, because it is the one
mode deliberately exempt from `TimeLimit` (it self-truncates). So two of three
training modes were dead, the flagship one was not, and nothing failed loudly.

The general lesson these assertions encode: **`train.py`'s wiring has no test
coverage from the engine suite**, because every other test drives the raw env
directly and never goes through `make_env`. Anything that only breaks once the
env is wrapped is invisible to all of them.

Deliberately cheap: no `.learn()`, no checkpoint, no episode. It builds each
stack, resets once, and asks for the mask the way MaskablePPO's rollout
collection does.
"""

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# Imported inside the two tests that use it: at module level it made every
# xdist worker load torch during COLLECTION (~454 MB each). See pytest.ini.

import train
from env.gym_env import ACTION_SPACE_SIZE, mask_fn

MODES = ["fixed", "expedition", "full_expedition"]


@pytest.fixture(scope="module", params=MODES)
def wired(request):
    """One `make_env` stack per mode — built once, reused by every assertion.

    Module-scoped because construction loads the whole DataStore.
    """
    env = train.make_env(mode=request.param, sampler_seed=0)
    env.reset(seed=0)
    return request.param, env


@pytest.mark.xdist_group("torch")
def test_get_action_masks_works_on_the_stack_train_actually_builds(wired):
    """The exact call MaskablePPO.collect_rollouts makes on every step.

    Not `env.action_masks()` — that would pass even with the bug, since it
    reaches the raw env directly. The regression only shows through
    `get_action_masks`, which goes via the wrapper.
    """
    from sb3_contrib.common.maskable.utils import get_action_masks
    mode, env = wired
    mask = get_action_masks(env)
    assert mask.shape == (ACTION_SPACE_SIZE,), (
        f"{mode}: mask is {mask.shape}, expected ({ACTION_SPACE_SIZE},)")
    assert mask.dtype == np.bool_
    assert mask.any(), f"{mode}: no legal action at reset"


def test_mask_fn_reaches_through_whatever_wrappers_are_in_the_way(wired):
    """`mask_fn` must not care how deeply the env is wrapped.

    `fixed` and `expedition` sit under a TimeLimit; `full_expedition` does not.
    Both must answer, and answer identically to the unwrapped env — otherwise
    the policy is being masked against a different board than it is shown.
    """
    _mode, env = wired
    assert np.array_equal(mask_fn(env), env.unwrapped.action_masks())


def test_the_wrapper_is_not_relying_on_gymnasium_attribute_forwarding():
    """Pin the actual root cause, so a future gymnasium cannot re-hide it.

    If forwarding ever comes back, the naive lambda starts working again and
    this file would stop being able to tell a fixed `mask_fn` from a lucky one.
    Asserting on the absence of `__getattr__` documents which world we are in;
    if it flips, the assertion below is the thing that should be re-read, not
    silently deleted.
    """
    import gymnasium

    forwards = hasattr(gymnasium.core.Wrapper, "__getattr__")
    if forwards:
        pytest.skip("this gymnasium forwards attributes again; mask_fn is "
                    "still correct, but the naive lambda would also pass")

    from gymnasium.wrappers import TimeLimit
    from env import DarkestDungeonEnv

    wrapped = TimeLimit(DarkestDungeonEnv(), max_episode_steps=10)
    with pytest.raises(AttributeError):
        wrapped.action_masks()          # the old lambda's body
    wrapped.reset(seed=0)
    assert mask_fn(wrapped).shape == (ACTION_SPACE_SIZE,)   # the fix


def test_stress_weight_reaches_the_workers_and_not_just_the_log_line():
    """`--slot-permute` was echoed as True in the distribution line while NO
    env ever received it, and the smoke test looked like a pass (CHANGELOG,
    2026-08-24). `--stress-weight` is the same shape of flag -- one number
    threaded through two factories to three construction sites -- so it gets
    the same check: ask the WORKERS, not the args.
    """
    import train

    vec = train.make_vec_env("full_expedition", n_envs=2, sampler_seed=0,
                             stress_weight=7.0)
    try:
        got = vec.get_attr("stress_weight")
        assert got == [7.0, 7.0], got
    finally:
        vec.close()

    single = train.make_env("full_expedition", sampler_seed=0, stress_weight=3.0)
    assert single.unwrapped.stress_weight == 3.0

    default = train.make_env("full_expedition", sampler_seed=0)
    assert default.unwrapped.stress_weight == 1.0, (
        "the default moved -- every archived checkpoint was trained at 1.0")


def test_affliction_penalty_reaches_the_workers_and_not_just_the_log_line():
    """Same shape of flag as `--stress-weight` and therefore the same check:
    one number threaded through two factories to a construction site, and
    `--slot-permute` proved that the distribution line agreeing with the args
    says nothing about what the envs got."""
    import train

    vec = train.make_vec_env("full_expedition", n_envs=2, sampler_seed=0,
                             affliction_penalty=20.0)
    try:
        assert vec.get_attr("affliction_penalty") == [20.0, 20.0]
    finally:
        vec.close()

    single = train.make_env("full_expedition", sampler_seed=0,
                            affliction_penalty=15.0)
    assert single.unwrapped.affliction_penalty == 15.0

    default = train.make_env("full_expedition", sampler_seed=0)
    assert default.unwrapped.affliction_penalty == 0.0, (
        "the default moved -- every archived checkpoint was trained at 0.0, "
        "and this is a NON-potential term so a nonzero default silently "
        "changes what every future run is optimising")


@pytest.mark.xdist_group("torch")
def test_the_hyperparameter_flags_reach_the_MODEL_not_just_the_log_line():
    """`--net-arch`, `--lr-schedule` and `--n-epochs` were added on 2026-08-28,
    the first time any PPO hyperparameter other than the net width has been
    touched. Same check `--stress-weight` gets and for the same reason:
    `--slot-permute` was once echoed as True in the distribution line while no
    env ever received it, and the smoke test looked like a pass.

    The LR schedule is checked by its VALUE, not by `callable()` -- SB3 wraps a
    constant in a callable too, so the obvious check passes either way.
    """
    from sb3_contrib import MaskablePPO

    import train

    env = train.make_env("full_expedition", sampler_seed=0)

    wide = MaskablePPO("MlpPolicy", env, device="cpu", n_steps=64, batch_size=32,
                       n_epochs=5, learning_rate=lambda p: 3e-4 * p, seed=0,
                       policy_kwargs=dict(net_arch=dict(pi=[512, 512],
                                                        vf=[512, 512])))
    widths = [p.shape[0] for n, p in wide.policy.mlp_extractor.named_parameters()
              if "weight" in n and p.dim() == 2]
    assert widths == [512, 512, 512, 512], widths
    assert wide.n_epochs == 5

    # Decay: full LR at the start of training, ~0 at the end.
    assert abs(wide.lr_schedule(1.0) - 3e-4) < 1e-9
    assert wide.lr_schedule(0.0) < 1e-9

    flat = MaskablePPO("MlpPolicy", env, device="cpu", n_steps=64, batch_size=32,
                       n_epochs=10, learning_rate=3e-4, seed=0,
                       policy_kwargs=dict(net_arch=dict(pi=[256, 256],
                                                        vf=[256, 256])))
    assert abs(flat.lr_schedule(1.0) - 3e-4) < 1e-9
    assert abs(flat.lr_schedule(0.0) - 3e-4) < 1e-9, (
        "the constant schedule decayed -- every archived checkpoint was trained "
        "at a flat 3e-4")
    assert flat.n_epochs == 10


if __name__ == "__main__":
    # Imported here, not at module scope: sb3_contrib costs ~500 MB and
    # every xdist worker collects this file. Missing entirely until
    # 2026-09-08, so `python tests/training/test_train_wiring.py` -- the
    # script path README advertises -- died on a NameError.
    from sb3_contrib.common.maskable.utils import get_action_masks

    for mode in MODES:
        env = train.make_env(mode=mode, sampler_seed=0)
        env.reset(seed=0)
        m = get_action_masks(env)
        assert m.shape == (ACTION_SPACE_SIZE,), (mode, m.shape)
        print(f"{mode:16s} ok  mask {m.shape}  {int(m.sum())} legal at reset")
    print("train wiring ok")
