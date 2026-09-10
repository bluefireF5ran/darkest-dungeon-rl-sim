"""The eval must face the same dungeons twice, or no delta it prints is real.

`reset(seed=)` reaches the ENGINE rng only. The torch level, which monsters are
drawn and whether the quest is a boss quest all come from `_sampler_rng`, which
was constructed once in `__init__` and never reseeded -- and
`eval_expedition.py` builds its env with `sampler_seed=None`, i.e. OS entropy.

So two runs of one command on one checkpoint faced DIFFERENT dungeons. Measured
2026-08-24 on the all-comps eval: short read 91.8% and then 93.8%, and every
delta this project quoted from that file carried the difference silently. The
`sampler_seed` reset option is the fix.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from env.expedition_sequence_env import ExpeditionSequenceEnv


def _scenario(env, sampler_seed, seed=7):
    opts = {"length_band": "medium"}
    if sampler_seed is not None:
        opts["sampler_seed"] = sampler_seed
    env.reset(seed=seed, options=opts)
    return (env._comp_name, env._zone_id, env._tier, env._num_combats,
            round(env.engine.torch_level, 6),
            tuple(e.definition_id for e in env.engine.enemies))


def test_the_same_sampler_seed_draws_the_same_dungeon():
    a = ExpeditionSequenceEnv(sampler_seed=None)
    b = ExpeditionSequenceEnv(sampler_seed=None)
    assert _scenario(a, 12345) == _scenario(b, 12345)


def test_a_different_sampler_seed_draws_a_different_one():
    """Otherwise the option would be reproducible by doing nothing at all."""
    env = ExpeditionSequenceEnv(sampler_seed=None)
    seen = {_scenario(env, s) for s in range(1, 12)}
    assert len(seen) > 1, "the sampler seed changed nothing"


def test_WITHOUT_the_option_two_envs_can_diverge():
    """The bug this exists to prevent. `sampler_seed=None` is OS entropy, so
    two envs given the identical reset seed still draw different scenarios.

    Written as 'can diverge' rather than 'does': two OS-entropy draws could
    coincide. Twelve resets each makes that vanishingly unlikely, and the test
    would fail loudly rather than flakily if the semantics were ever changed
    to derive the sampler from `seed`.
    """
    a = ExpeditionSequenceEnv(sampler_seed=None)
    b = ExpeditionSequenceEnv(sampler_seed=None)
    assert ([_scenario(a, None) for _ in range(12)]
            != [_scenario(b, None) for _ in range(12)])


def test_training_is_untouched_when_the_option_is_absent():
    """Opt-in, because each SubprocVecEnv worker gets `sampler_seed + i`
    precisely so the workers see DIFFERENT scenarios. Deriving the sampler
    from the per-episode `seed` would collapse that into N copies of one
    episode -- the exact narrowing CLAUDE.md warns about for --n-envs."""
    a = ExpeditionSequenceEnv(sampler_seed=99)
    b = ExpeditionSequenceEnv(sampler_seed=99)
    assert ([_scenario(a, None) for _ in range(6)]
            == [_scenario(b, None) for _ in range(6)]), (
        "a constructor sampler_seed no longer reproduces its own stream")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  PASS {name}")
