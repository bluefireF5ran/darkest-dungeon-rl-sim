"""`--all-comps`: the eval stops sampling comps and evaluates the pool.

Comp-to-comp spread is the dominant error term in the full-expedition eval and
it dwarfs episode noise. Measured 2026-08-23 over 12 comps x 3 bands x 50
episodes:

| band | mean | comp-to-comp SD | SE from 12 comps |
|---|---|---|---|
| short | 84.2% | 7.3 | +-2.1pp |
| medium | 77.0% | 12.1 | +-3.5pp |
| long | 56.8% | **16.2** | **+-4.7pp** |

Individual comps ran 30% to 88% on the long band. Reaching +-2pp by sampling
harder would need ~252 comps and only ~152 exist, so the term cannot be beaten
that way -- it has to be removed. 152 x 3 x 4 is the same 1,824 episodes as
12 x 3 x 50 and leaves no comp-sampling error at all.

**Passing a big `--comps` is not the same thing**, which is the whole reason
this is a separate flag. See `all_comps`'s docstring for the three reasons; the
tests below pin two of them.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import random

from tests._fixtures import get_store

import eval_expedition as ee
from env.expedition_env import build_comp_pool

store = get_store()


def test_all_comps_is_the_whole_pool_in_a_stable_order():
    got = ee.all_comps(store)
    pool = build_comp_pool(store)
    assert len(got) == len(pool), (len(got), len(pool))
    assert {c[0] for c in got} == {c[0] for c in pool}
    assert [c[0] for c in got] == sorted(c[0] for c in pool), "order is not stable"
    # Calling twice must give the identical list -- no RNG anywhere in it.
    assert ee.all_comps(store) == got
    print(f"  PASS test_all_comps_is_the_whole_pool_in_a_stable_order ({len(got)})")


def test_a_big_comps_value_IS_a_superset_but_NOT_a_nested_sample():
    """A correction that was itself corrected, which is why it is worth a test.

    `random.sample` takes a different branch at `k=n` than at `k=12`
    (selection-set versus partial-shuffle, on CPython's `setsize` heuristic).
    That was first claimed to make a big draw disagree with a small one; then
    checked at 166 comps, where the first 12 picks agreed, and recorded as
    harmless. At **169** comps -- three of Fran's hand-upgrades promoted on
    2026-08-28 -- they diverge at index 8. Nothing changed but the population.

    So prefix agreement was a coincidence of population size and never a
    property, and this test now pins the two claims that ARE properties: a big
    draw is a superset, and it is not a nested one. Do not re-add an assertion
    that the prefixes match -- it will pass or fail on how many comps happen to
    exist that week.
    """
    pool = build_comp_pool(store)
    small = [c[0] for c in ee.pick_representative_comps(store, 12, 1)]
    big = [c[0] for c in ee.pick_representative_comps(store, len(pool), 1)]
    assert set(big) == {c[0] for c in pool}, "a full draw must be the whole pool"
    assert set(small) <= set(big), "a full draw must contain every smaller draw"
    assert len(set(small)) == 12
    print("  PASS test_a_big_comps_value_IS_a_superset_but_NOT_a_nested_sample "
          f"(prefix agrees: {big[:12] == small})")


def test_a_cell_scenario_is_independent_of_every_other_cell():
    """The second reason. The shared `sampler_rng` is consumed sequentially
    across the comp x band x episode nest, so adding one comp shifts the tier
    and length of every cell after it. `_cell_scenario` hashes the cell's own
    identity instead, so a cell is reproducible on its own and the comp list
    can grow without moving anything."""
    a = ee._cell_scenario("Some Comp", "long", 0, 14, 16)
    b = ee._cell_scenario("Some Comp", "long", 0, 14, 16)
    assert a == b, "not reproducible"

    tier, combats, seed = a
    assert 1 <= tier <= 5
    assert 14 <= combats <= 16
    assert 0 <= seed < 2 ** 31

    # A different cell must not collide, and must not be correlated by
    # construction.
    others = {ee._cell_scenario(f"Comp {i}", "long", 0, 14, 16)
              for i in range(40)}
    assert len(others) > 20, f"only {len(others)} distinct scenarios in 40 cells"
    print("  PASS test_a_cell_scenario_is_independent_of_every_other_cell")


def test_inserting_a_comp_does_not_move_other_cells():
    """The property that makes the pool safe to grow. Fran adds curated comps
    regularly -- 144 -> 156 on 2026-08-23 alone -- and under the sequential
    sampler every one of those shifted the scenarios of every later cell."""
    before = {(c, b, e): ee._cell_scenario(c, b, e, 14, 16)
              for c in ("Alpha", "Zulu") for b in ("long",) for e in range(4)}
    # Pretend a comp was inserted between them; the hash of the others cannot
    # know or care.
    _new = [ee._cell_scenario("Mike", "long", e, 14, 16) for e in range(4)]
    after = {(c, b, e): ee._cell_scenario(c, b, e, 14, 16)
             for c in ("Alpha", "Zulu") for b in ("long",) for e in range(4)}
    assert before == after
    print("  PASS test_inserting_a_comp_does_not_move_other_cells")


def test_the_band_range_is_respected_for_every_band():
    from env.expedition_sequence_env import EXPEDITION_LENGTH_BANDS
    for band, (lo, hi) in EXPEDITION_LENGTH_BANDS.items():
        for ep in range(12):
            _t, combats, _s = ee._cell_scenario("X", band, ep, lo, hi)
            assert lo <= combats <= hi, (band, combats, lo, hi)
    print("  PASS test_the_band_range_is_respected_for_every_band")


def test_the_flags_exist_and_default_off():
    """`--all-comps` must not change what an unflagged run does -- every
    archived invocation has to keep meaning what it meant."""
    import subprocess

    out = subprocess.run(
        [sys.executable, str(Path(ee.__file__)), "--help"],
        capture_output=True, text=True, cwd=str(Path(ee.__file__).parent),
    ).stdout
    assert "--all-comps" in out and "--detail" in out

    # Both are `store_true`, so an unflagged parse leaves them False and the
    # sampled path is untouched.
    src = Path(ee.__file__).read_text(encoding="utf-8")
    for flag in ('"--all-comps"', '"--detail"'):
        i = src.index(flag)
        assert 'action="store_true"' in src[i:i + 200], flag
    print("  PASS test_the_flags_exist_and_default_off")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
