"""The game logs one PRNG draw at every layer it passes through.

Counting `Random ...` lines overcounts real draws by up to 3x: the raw uniform,
the bare int conversion and the A,B conversion are all logged separately with
the SAME seed, because they are one advance reported three times. On a real
capture that was 1798 logged lines for 1347 actual advances.

This matters for the differential harness: any attempt to align the game's RNG
stream against `SeededRNG` must dedupe by seed first, or it desynchronises on
the first attack. It also pins the exact conversion arithmetic, verified 90/90
against a real capture:

    int range N     -> floor(u * N)
    int range A,B   -> A + floor(u * (B-A+1))     [A and B both inclusive]

See CHANGELOG.md.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.game_trace import (
    parse, prng_advances, check_int_range_formula, damage_ranges_by_actor,
    skill_execution_shapes, RngDraw,
)

# One real attack, copied verbatim from a capture (trace_diff.log).
SAMPLE = """[24856] Battle: state change to bs_turn_do_skill, (from bs_turn_wait_to_do_skill). 24856
[24856] check pCurrentBattleActor #0: Arbalest 1
[24856] PID: 24856 Name: Battle Random uniform: 0.0252787955 Seed: 54285798
[24856] PID: 24856 Name: Battle Random uniform: 0.8607006669 Seed: 1848340658
[24856] PID: 24856 Name: Battle Random uniform: 0.7965067029 Seed: 1710485151
[24856] Chance to hit: 0.885000
[24856] Roll to hit: 0.025279
[24856] PID: 24856 Name: Battle Random uniform: 0.8884044886 Seed: 1907834115
[24856] PID: 24856 Name: Battle Random int range: 3 Value: 2 Seed: 1907834115
[24856] PID: 24856 Name: Battle Random int range A,B: 2 , 4 Value: 4 Seed: 1907834115
[24856] PID: 24856 Name: Battle Random uniform: 0.4142697155 Seed: 889637448
[24856] Battle: state change to bs_after_turn, (from bs_turn_presentation). 24856
""".splitlines()

EVENTS = parse(SAMPLE)


def test_one_advance_reported_at_three_layers_counts_once():
    """The three lines sharing Seed 1907834115 are a single PRNG advance."""
    logged = [e for e in EVENTS if isinstance(e, RngDraw)]
    assert len(logged) == 7
    assert len(prng_advances(EVENTS)) == 5


def test_deduping_keeps_the_first_layer_not_the_last():
    """The uniform is the primary form; the conversions are derived from it."""
    adv = prng_advances(EVENTS)
    shared = [d for d in adv if d.seed == 1907834115]
    assert len(shared) == 1
    assert shared[0].kind == "uniform"


def test_distinct_seeds_are_never_collapsed():
    seeds = [d.seed for d in prng_advances(EVENTS)]
    assert seeds == [54285798, 1848340658, 1710485151, 1907834115, 889637448]


def test_int_range_ab_is_inclusive_of_both_ends():
    """2 + floor(0.8884044886 * (4-2+1)) = 2 + 2 = 4, which is what the game got."""
    ok, total, fails = check_int_range_formula(EVENTS)
    assert (ok, total) == (1, 1), fails
    assert not fails


def test_the_to_hit_roll_is_the_first_advance_of_the_execution():
    """It is REPORTED two advances later, which is why lazy simulator draws
    (hit, then crit only on a hit) cannot be aligned draw-for-draw."""
    runs = skill_execution_shapes(EVENTS)
    assert len(runs) == 1
    first = next(e for e in runs[0] if isinstance(e, RngDraw))
    hit_check = next(e for e in runs[0] if not isinstance(e, RngDraw))
    assert first.uniform is not None
    assert abs(first.uniform - hit_check.roll) < 1e-6


def test_damage_ranges_are_attributed_to_the_acting_combatant():
    ranges = damage_ranges_by_actor(EVENTS)
    assert ranges["Arbalest"][(2, 4)] == 1


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  ok  {name}")
