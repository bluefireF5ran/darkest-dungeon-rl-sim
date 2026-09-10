from __future__ import annotations
import random
from typing import Sequence


class SeededRNG:
    """Deterministic RNG wrapper for reproducible combat tests."""

    def __init__(self, seed: int | None = None):
        self._rng = random.Random(seed)
        self.seed = seed
        self.call_count = 0

    def random(self) -> float:
        self.call_count += 1
        return self._rng.random()

    def uniform(self, low: float, high: float) -> float:
        return low + self.random() * (high - low)

    def randint(self, a: int, b: int) -> int:
        self.call_count += 1
        return self._rng.randint(a, b)

    def choice(self, seq: Sequence):
        self.call_count += 1
        return self._rng.choice(seq)

    def shuffle(self, seq: list) -> None:
        """In place, through the seeded stream like everything else.

        Added for the surprise scramble; `random.shuffle` draws n-1 times, so
        calling it on an EMPTY or single-element list costs nothing and cannot
        desynchronise a trace.
        """
        self.call_count += 1
        self._rng.shuffle(seq)

    def roll(self, sides: int = 8) -> int:
        return self.randint(1, sides)

    def weighted_choice(self, items: Sequence, weights: Sequence[float]):
        """Pick one item with probability proportional to its weight.
        Falls back to a uniform pick among `items` if all weights are <= 0.
        """
        total = sum(w for w in weights if w > 0)
        if total <= 0:
            return self.choice(list(items))
        self.call_count += 1
        target = self._rng.random() * total
        cumulative = 0.0
        for item, weight in zip(items, weights):
            if weight <= 0:
                continue
            cumulative += weight
            if target < cumulative:
                return item
        return items[-1]

    def get_state(self) -> tuple:
        return (self.seed, self.call_count)
