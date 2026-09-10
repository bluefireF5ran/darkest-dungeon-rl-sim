"""The QUALITY ruler: what state did the party come home in?

SUCCESS (finished every combat, nobody died) is a gate. It is binary, it says
nothing about the shape the party is in, and on 2026-08-24 the short and medium
bands read 93.6% and 92.3% -- saturated, so it can no longer resolve an
improvement. Fran's stated objective is "complete an expedition with all heroes
at 0 stress, without anyone dying", and only half of that was ever measured.

`_run_quality` is the graded companion. These tests pin its two endpoints, the
averaging rule that makes a death expensive, and the one place it deliberately
disagrees with the reward's own `combat_end_condition`.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from eval_expedition import _run_quality


class _Hero:
    def __init__(self, hp=100, max_hp=100, stress=0.0, alive=True):
        self.hp, self.max_hp, self.stress, self._alive = hp, max_hp, stress, alive

    def is_alive(self):
        return self._alive


def test_a_perfect_run_is_100_and_a_wipe_is_0():
    party = [_Hero(hp=40, max_hp=40, stress=0.0) for _ in range(4)]
    assert _run_quality(party, 4) == 100.0
    dead = [_Hero(hp=0, max_hp=40, stress=200.0, alive=False) for _ in range(4)]
    assert _run_quality(dead, 4) == 0.0


def test_the_two_halves_are_equally_weighted():
    full_hp_max_stress = [_Hero(hp=40, max_hp=40, stress=200.0) for _ in range(4)]
    no_hp_no_stress = [_Hero(hp=0, max_hp=40, stress=0.0) for _ in range(4)]
    assert _run_quality(full_hp_max_stress, 4) == 50.0
    assert _run_quality(no_hp_no_stress, 4) == 50.0


def test_a_dead_hero_costs_its_WHOLE_share_not_just_its_stress():
    """The averaging is over the ROSTER, not the survivors. Averaging over
    survivors would let a policy raise its score by letting the most stressed
    hero die, which is the exact opposite of the objective."""
    three_perfect = [_Hero(hp=40, max_hp=40, stress=0.0) for _ in range(3)]
    dead = _Hero(hp=0, max_hp=40, stress=200.0, alive=False)
    assert _run_quality(three_perfect + [dead], 4) == 75.0

    # The trap: the same party graded over survivors would read 100.
    survivors_only = _run_quality(three_perfect, 3)
    assert survivors_only == 100.0
    assert _run_quality(three_perfect + [dead], 4) < survivors_only


def test_it_does_NOT_go_blind_above_100_stress_the_way_the_reward_does():
    """`combat_end_condition` clamps stress at END_STRESS_SCALE (100), so it is
    flat between a hero at 110 and one at 200. A ruler must not be blind
    exactly where the runs it grades end up, so this scores against the 200
    ceiling and stays monotone the whole way."""
    from env.expedition_sequence_env import END_STRESS_SCALE
    assert END_STRESS_SCALE == 100.0

    def q(stress):
        return _run_quality([_Hero(hp=40, max_hp=40, stress=stress)
                             for _ in range(4)], 4)

    assert q(110.0) > q(150.0) > q(199.0), "the ruler flattened above 100 stress"


def test_stress_above_the_ceiling_cannot_push_the_score_negative():
    over = [_Hero(hp=40, max_hp=40, stress=400.0) for _ in range(4)]
    assert _run_quality(over, 4) == 50.0


def test_an_empty_roster_is_zero_not_a_crash():
    assert _run_quality([], 0) == 0.0


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  PASS {name}")
