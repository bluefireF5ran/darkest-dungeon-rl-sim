"""The A/B instrument's own correctness.

`tools/ab_compare.py` exists because the project could not tell a ~3pp effect
from ~3pp of noise. That makes its statistics load-bearing: a tool that reports
a confident wrong answer is worse than the eval it replaces, because it would
be believed. So the pieces are tested against closed-form values rather than
against themselves.

The determinism claim -- the same model twice gives bit-identical outcomes -- is
covered by `tools/ab_compare.py --self-check`, which needs a real checkpoint and
so is not run here. What IS covered here is the part that must hold with no
checkpoint at all: the scenario list is a pure function of its inputs, and it
does not depend on the model, the env, or on how many episodes ran before.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests._fixtures import get_store
from tools.ab_compare import (
    bootstrap_ci, build_scenarios, episodes_needed, mcnemar_exact,
)



# --- McNemar, against hand-computable values ---

def test_mcnemar_no_discordant_pairs_is_no_evidence():
    """All ties means the two policies never differed. p must be 1."""
    assert mcnemar_exact(0, 0) == 1.0


def test_mcnemar_is_symmetric_in_its_arguments():
    """Which arm is 'A' cannot change the two-sided p-value."""
    for b, c in [(3, 7), (1, 9), (5, 5), (0, 4)]:
        assert mcnemar_exact(b, c) == pytest.approx(mcnemar_exact(c, b))


def test_mcnemar_matches_the_binomial_by_hand():
    """b=0,c=5 is five straight wins: 2 * (1/2)^5 = 0.0625."""
    assert mcnemar_exact(0, 5) == pytest.approx(0.0625)
    # b=0,c=6 -> 2 * (1/2)^6 = 0.03125
    assert mcnemar_exact(0, 6) == pytest.approx(0.03125)
    # An even split is maximally unconvincing.
    assert mcnemar_exact(5, 5) == pytest.approx(1.0)


def test_mcnemar_never_exceeds_one():
    """The 2x one-tail doubling has to be clamped, or a near-even split
    reports p > 1 and the tool looks broken at exactly the moment it is
    reporting 'no difference'."""
    for b in range(0, 12):
        for c in range(0, 12):
            assert 0.0 <= mcnemar_exact(b, c) <= 1.0


def test_mcnemar_ignores_concordant_pairs():
    """The sensitivity argument for pairing, stated as a test: 1000 scenarios
    where both models agree add nothing, so p depends only on the discordant
    counts."""
    assert mcnemar_exact(2, 8) == mcnemar_exact(2, 8)   # no n parameter exists
    assert mcnemar_exact(2, 8) < 0.5


# --- bootstrap CI ---

def test_bootstrap_of_a_constant_is_that_constant():
    lo, hi = bootstrap_ci([0.25] * 40, iters=500)
    assert lo == pytest.approx(0.25) and hi == pytest.approx(0.25)


def test_bootstrap_of_all_zeros_is_exactly_zero():
    """The self-check depends on this: identical arms must give [0, 0], not a
    hairline interval that reads as a real difference."""
    assert bootstrap_ci([0.0] * 30, iters=500) == (0.0, 0.0)


def test_bootstrap_brackets_the_mean():
    deltas = [1.0] * 30 + [-1.0] * 10 + [0.0] * 60
    lo, hi = bootstrap_ci(deltas, iters=4000)
    mean = sum(deltas) / len(deltas)
    assert lo < mean < hi


def test_bootstrap_is_deterministic_for_a_given_seed():
    """Two people running the same comparison must quote the same interval."""
    d = [1.0, 0.0, -1.0, 0.0, 1.0, 1.0, 0.0]
    assert bootstrap_ci(d, iters=2000, seed=7) == bootstrap_ci(d, iters=2000, seed=7)


def test_bootstrap_of_nothing_does_not_crash():
    lo, hi = bootstrap_ci([], iters=10)
    assert lo != lo and hi != hi          # NaN


def test_a_wider_spread_gives_a_wider_interval():
    tight = bootstrap_ci([0.1] * 50 + [-0.1] * 50, iters=4000)
    wide = bootstrap_ci([1.0] * 50 + [-1.0] * 50, iters=4000)
    assert (wide[1] - wide[0]) > (tight[1] - tight[0])


# --- sample-size guidance ---

def test_episodes_needed_grows_with_noise():
    quiet = episodes_needed([0.1] * 50 + [-0.1] * 50)
    loud = episodes_needed([1.0] * 50 + [-1.0] * 50)
    assert loud > quiet


def test_episodes_needed_on_a_constant_is_the_sample_itself():
    assert episodes_needed([0.5] * 20) == 20


def test_episodes_needed_needs_two_points():
    assert episodes_needed([1.0]) is None


# --- the scenario list is a pure function ---

@pytest.fixture(scope="module")
def store():
    return get_store()


def test_scenarios_are_reproducible(store):
    assert build_scenarios(store, 4, 1, 2) == build_scenarios(store, 4, 1, 2)


def test_scenarios_do_not_depend_on_call_order(store):
    """No hidden module-level RNG state: building other lists in between must
    not perturb the answer. If it did, two A/B runs would silently compare
    different dungeon sets."""
    first = build_scenarios(store, 3, 1, 2)
    build_scenarios(store, 9, 5, 4)
    build_scenarios(store, 2, 3, 1)
    assert build_scenarios(store, 3, 1, 2) == first


def test_comp_seed_actually_changes_the_comps(store):
    a = {s.comp_name for s in build_scenarios(store, 5, 1, 1)}
    b = {s.comp_name for s in build_scenarios(store, 5, 999, 1)}
    assert a != b, "comp_seed is not reaching pick_representative_comps"


def test_the_list_is_comps_times_bands_times_episodes(store):
    from env.expedition_sequence_env import EXPEDITION_LENGTH_BANDS
    scs = build_scenarios(store, 4, 1, 3)
    assert len(scs) == 4 * len(EXPEDITION_LENGTH_BANDS) * 3
    assert len({s.length_band for s in scs}) == len(EXPEDITION_LENGTH_BANDS)


def test_every_scenario_is_fully_specified(store):
    """A scenario has to pin everything that decides difficulty, or the two
    arms are not really playing the same dungeon."""
    for s in build_scenarios(store, 3, 1, 2):
        assert s.comp_name and s.zone_id and s.hero_ids
        assert 1 <= s.tier <= 5
        assert s.num_combats >= 1
        assert isinstance(s.ep_seed, int) and s.ep_seed >= 0


def test_a_scenario_is_hashable_and_carries_no_model_identity(store):
    """Keyed on the dungeon only. If a model ever leaked into the key, the two
    arms would face different draws and the comparison would measure luck."""
    scs = build_scenarios(store, 2, 1, 2)
    assert len(set(scs)) == len(scs)
    assert not any("model" in f for f in type(scs[0]).__dataclass_fields__)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v", "-p", "no:xdist"]))
