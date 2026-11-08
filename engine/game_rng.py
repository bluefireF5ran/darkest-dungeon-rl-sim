"""The game's own random number generator, reproduced exactly.

Darkest Dungeon draws every battle roll from `prng_minstd` -- the Park-Miller
"minimal standard" Lehmer generator, `seed = seed * 16807 mod (2^31 - 1)`. The
constant is in the binary (`0x41a7` in the step code), and the debug channel
logs the state after every draw (`Battle Random uniform: u Seed: s`, with
`u = s / (2^31 - 1)`). Over the 2026-10-04 paired session and the August
traces, every consecutive pair of logged states either chains through this
recurrence or repeats (an int range read off the same uniform), apart from the
documented reseeds. See DECOMP.md F15.

**And the save carries the live state.** `persist.raid.json`'s battle `seed`
is the generator's state at the start of the turn being decided -- 67 of 67
hero turns in that session. So the next draws are known before the hero acts.

How a hero's turn consumes them, measured on every attack in that session
(`tools/fate_report.py` re-derives it):

    draw 1  a uniform at skill selection (spent whatever is chosen)
    draw 2  THE HIT ROLL -- one roll shared by every target          (50/50)
    draw 3, 4  spent before the first target
    draw 5  the first target's damage: lo + int(u * (hi - lo + 1))    (62/62)
    draw 6  the first target's crit roll: crit if u < crit chance      (5/5)
    later targets: usually +3 per target (8, 9), shifted by effect rolls

Only the first target's draws are fixed regardless of what the skill applies,
so only those are treated as known.
"""
from __future__ import annotations

MINSTD_A = 16807
MINSTD_M = 2 ** 31 - 1

#: 1-based positions in a hero turn's stream, from the start-of-turn state.
HIT_ROLL_DRAW = 2
FIRST_DAMAGE_DRAW = 5
FIRST_CRIT_DRAW = 6


def step(seed: int) -> int:
    return (int(seed) * MINSTD_A) % MINSTD_M


def stream(seed: int, n: int) -> list[int]:
    """The next `n` states after `seed` -- the seeds the game will log."""
    out, s = [], int(seed)
    for _ in range(n):
        s = step(s)
        out.append(s)
    return out


def uniforms(seed: int, n: int) -> list[float]:
    return [s / MINSTD_M for s in stream(seed, n)]


def int_range(u: float, lo: int, hi: int) -> int:
    """`Random int range A,B` from a uniform -- exact on 62/62 traced draws."""
    return int(lo) + int(u * (int(hi) - int(lo) + 1))


class MinStd:
    """A live generator, for simulating the game's own stream forward."""

    def __init__(self, seed: int):
        self.seed = int(seed)

    def uniform(self) -> float:
        self.seed = step(self.seed)
        return self.seed / MINSTD_M

    def int_range(self, lo: int, hi: int) -> int:
        return int_range(self.uniform(), lo, hi)
