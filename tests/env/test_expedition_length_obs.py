"""The policy must be able to tell a short expedition from a long one.

`ExpeditionSequenceEnv` originally exposed only `combats_remaining_frac`, i.e.
remaining / total. That normalises the one quantity resource pacing depends on
straight out of the observation: the start of a 3-combat run and the start of a
16-combat run both read 1.0, and halfway through each both read 0.5, while the
correct play is opposite (spend freely vs. conserve).

It was survivable while every band sat between 8 and 18 combats. After
EXPEDITION_LENGTH_BANDS was corrected against real game data it spans 3..16 with
a gap at 11-13, and the same fraction started mapping to wildly different
remaining workloads. Measured: the policy converged on a single sprint pace —
short-band stress fell (Shadowed Purse Strings 81.6 -> 36.1) while long-band
stress exploded (Marked Prey: Snipe 105 -> 176) and long-band SUCCESS dropped
19.8pp, even though the long band had itself become *shorter* (14-18 -> 14-16).

`combats_remaining_abs` restores the absolute scale. See CHANGELOG.md.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from env.expedition_sequence_env import (
    ExpeditionSequenceEnv,
    EXPEDITION_CONTEXT_FEATURES,
    EXPEDITION_LENGTH_BANDS,
    MAX_EXPECTED_COMBATS,
)

env = ExpeditionSequenceEnv(sampler_seed=0)

FRAC_IDX, ABS_IDX = 0, 1


def _context(num_combats: int):
    obs, _ = env.reset(seed=1, options={
        "hero_ids": ["crusader", "vestal"], "zone_id": "crypts", "tier": 3,
        "num_combats": num_combats,
    })
    return obs[-EXPEDITION_CONTEXT_FEATURES:]


def test_observation_width_follows_the_feature_constant():
    """Derive the width, never hardcode it (project convention)."""
    assert env.observation_space.shape == (env.base_obs_size + EXPEDITION_CONTEXT_FEATURES,)


def test_fraction_alone_cannot_distinguish_lengths():
    """Pins the flaw itself: this is why the extra feature is needed."""
    assert _context(3)[FRAC_IDX] == _context(16)[FRAC_IDX]


def test_absolute_remaining_distinguishes_lengths():
    short, long = _context(3), _context(16)
    assert short[ABS_IDX] < long[ABS_IDX]
    assert short[ABS_IDX] * MAX_EXPECTED_COMBATS == 3
    assert long[ABS_IDX] * MAX_EXPECTED_COMBATS == 16


def test_absolute_remaining_covers_every_real_band():
    """Every length the real game can generate must land in a sane range —
    0 < abs <= 1.0, since MAX_EXPECTED_COMBATS is the longest real expedition."""
    for band, (lo, hi) in EXPEDITION_LENGTH_BANDS.items():
        for n in (lo, hi):
            v = _context(n)[ABS_IDX]
            assert 0.0 < v <= 1.0, f"{band} n={n} gave abs={v}"


def test_absolute_remaining_decreases_as_combats_are_cleared():
    """It must track progress, not just the initial length."""
    ctx0 = _context(8)
    start = ctx0[ABS_IDX]
    env._combat_idx += 3
    later = env._get_obs()[-EXPEDITION_CONTEXT_FEATURES:][ABS_IDX]
    assert later < start
    assert round(later * MAX_EXPECTED_COMBATS) == 5


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  ok  {name}")
