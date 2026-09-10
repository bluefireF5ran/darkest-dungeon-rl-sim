"""The SEARCH fills five positive quirk slots; TRAINING still draws 1-3.

`RANDOM_QUIRKS_PER_POLARITY` is (1, 3) and is shared between
`ExpeditionEnv`'s `--kit-random-p` and the comp search. Moving it would
silently shift the TRAINING distribution and invalidate every archived
measurement, so the search got its own constant instead.

Measured 2026-08-30, topping every hero of the seven Cove comps up to five
live quirks and scoring paired on two checkpoints: 13 of 14 comparisons
positive, mean +2.3, with the gains landing on the weak comps because the
strong ones are already against the pool's ceiling.
"""
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from env.expedition_env import RANDOM_QUIRKS_PER_POLARITY
from tools.comp_ga import SEARCH_QUIRKS_PER_POLARITY, CompSpace
from tests._fixtures import get_store

store = get_store()


def test_training_draw_is_untouched():
    """The shared constant must stay (1, 3) -- training reproducibility."""
    assert RANDOM_QUIRKS_PER_POLARITY == (1, 3), (
        "moving this changes what --kit-random-p feeds TRAINING, which is "
        "not what the 2026-08-30 measurement was about")


def test_the_search_fills_all_five():
    assert SEARCH_QUIRKS_PER_POLARITY == (5, 5)
    space = CompSpace(store)
    rng = random.Random(0)
    for _ in range(20):
        q = space.random_quirks(rng)
        assert len(q) == 5, f"drew {len(q)} positive quirks, expected 5: {q}"
        assert len(set(q)) == len(q), "a quirk was drawn twice"


def test_sparse_quirks_reproduces_the_old_draw():
    """The off switch has to restore the old behaviour exactly."""
    space = CompSpace(store, quirk_slots=RANDOM_QUIRKS_PER_POLARITY)
    rng = random.Random(0)
    lo, hi = RANDOM_QUIRKS_PER_POLARITY
    for _ in range(20):
        assert lo <= len(space.random_quirks(rng)) <= hi


def test_negative_quirks_are_NOT_filled_to_five():
    """Five negatives is not a state a player buys on purpose."""
    space = CompSpace(store, negative_quirks=True)
    rng = random.Random(0)
    lo, hi = RANDOM_QUIRKS_PER_POLARITY
    for _ in range(20):
        q = space.random_quirks(rng)
        neg = [x for x in q if not store.quirks[x].is_positive]
        assert lo <= len(neg) <= hi, f"drew {len(neg)} negative quirks"


if __name__ == "__main__":
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            _fn(); print("  PASS", _name)
