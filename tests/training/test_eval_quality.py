"""The QUALITY ruler's weights, which are a declared convention.

PLAYBOOK 0.1 states the objective they are meant to encode, and they do
not currently encode it -- see `CHANGELOG.md`, "FRAN'S VALUE HIERARCHY".
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest


# -- the QUALITY weighting is a convention, and Fran's objective disagrees ---

def test_the_default_weighting_is_the_archive_s_and_sums_to_one():
    """50/50 stays the default because changing it re-scores every measurement
    this repo has ever taken. That is a new ruler, not a fix."""
    import eval_expedition as E

    assert E.QUALITY_WEIGHTS == (0.5, 0.5)
    assert abs(sum(E.QUALITY_WEIGHTS) - 1.0) < 1e-9
    assert abs(sum(E.QUALITY_ALT) - 1.0) < 1e-9


def test_the_alternative_weights_STRESS_over_HP():
    """PLAYBOOK 0.1: stress carries out of the dungeon and HP does not, so
    "if you outvalue hp to stress its a problem"."""
    import eval_expedition as E

    hp_w, stress_w = E.QUALITY_ALT
    assert stress_w > hp_w
    assert stress_w / hp_w == pytest.approx(2.0)


def test_re_scoring_needs_no_rerun():
    """`RolloutResult` carries the halves unweighted for exactly this. A ruler
    you have to re-play a season to change is a ruler nobody changes."""
    import eval_expedition as E

    assert E.quality_from_parts(1.0, 1.0) == pytest.approx(100.0)
    assert E.quality_from_parts(0.0, 0.0) == pytest.approx(0.0)
    assert E.quality_from_parts(1.0, 0.4) == pytest.approx(70.0)
    assert E.quality_from_parts(1.0, 0.4, E.QUALITY_ALT) == pytest.approx(60.0)


def test_a_dead_hero_still_costs_its_whole_share_under_EITHER_weighting():
    """The one part of the hierarchy already satisfied, and it must survive a
    re-weighting: a policy must not be able to improve mean stress by letting
    the stressed hero die."""
    import eval_expedition as E

    class _H:
        def __init__(self, alive, hp=10, max_hp=10, stress=0):
            self._a, self.hp, self.max_hp, self.stress = alive, hp, max_hp, stress

        def is_alive(self):
            return self._a

    four_alive = [_H(True) for _ in range(4)]
    one_dead = [_H(True) for _ in range(3)] + [_H(False)]
    for weights in (E.QUALITY_WEIGHTS, E.QUALITY_ALT):
        good = E.quality_from_parts(*E.run_quality_components(four_alive, 4), weights)
        bad = E.quality_from_parts(*E.run_quality_components(one_dead, 4), weights)
        assert good > bad, weights
        assert bad == pytest.approx(75.0)
