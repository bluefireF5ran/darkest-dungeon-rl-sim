"""The two initiative-queue globals, and the trap that killed one of them.

`enemies_before_next_hero` shipped DEAD on 2026-08-23: `turn_order` holds
`CombatantState` objects, the first version looked each entry up with
`x.id == entry`, that comparison is False for every entry, and the loop
therefore `continue`d over the whole queue. The feature was a constant 0.0.

Nothing caught it, and nothing would have: the observation had the right
width, every value was finite and in range, and a constant feature trains
perfectly happily. It surfaced only because a test rig for an unrelated
mechanic put an int into `turn_order` and the engine raised on `.is_dead`.

So this file pins the two things that would have caught it:

* the feature is **not constant** over real play, and
* `turn_order` holds objects, not ids -- stated as an assertion so a future
  refactor that changes the representation fails here loudly instead of
  silently zeroing an input again.

Why the feature is worth having at all: "how much damage arrives before my
side moves again" is not recoverable from the rest of the vector. Speed is
observable, but the queue is SPEED + d8 rolled at reset, so the order is not
a function of the visible stats.
"""

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests._fixtures import get_store

from engine.combat_engine import CombatEngine
from engine.combatant import CombatantState
from engine.rng import SeededRNG
from env.gym_env import RANKS, DarkestDungeonEnv


@pytest.fixture(scope="module")
def store():
    return get_store()


def test_turn_order_holds_objects_not_ids(store):
    """The representation this feature depends on. If this ever changes,
    `_turn_order_features` must change with it -- reading it the wrong way is
    silent, not an error."""
    e = CombatEngine(store, SeededRNG(5))
    e.reset(hero_defs=[store.heroes["crusader"], store.heroes["vestal"]],
            enemy_defs=[store.enemies["cultist_brawler_A"]] * 2)
    assert e.turn_order, "empty queue -- the rest of this file proves nothing"
    assert all(isinstance(c, CombatantState) for c in e.turn_order)


def test_the_enemy_count_is_not_a_constant():
    """The actual regression. A dead feature is indistinguishable from a live
    one by width, finiteness or range -- only by variance."""
    env = DarkestDungeonEnv()
    seen = set()
    for seed in range(40):
        env.reset(seed=seed)
        for _ in range(6):
            seen.add(env._turn_order_features(env.engine.get_current_actor_id())[1])
            legal = np.flatnonzero(env.action_masks())
            if not len(legal):
                break
            _obs, _r, terminated, truncated, _i = env.step(int(legal[0]))
            if terminated or truncated:
                break
    assert len(seen) > 1, f"enemies_before_next_hero never moved off {seen}"


def test_it_counts_the_enemies_before_the_next_LIVING_hero(store):
    """Hand-built queue, so the expected number is not a guess.

    Counts to the next living HERO, not to the end of the round: a dead
    combatant in between is skipped rather than terminating the count, which
    is what makes the feature mean "damage arriving before I act again".
    """
    env = DarkestDungeonEnv()
    env.reset(seed=1)
    e = env.engine
    h0, h1 = e.heroes[0], e.heroes[1]
    foes = list(e.enemies)
    assert len(foes) >= 2, "need two enemies for this arrangement"

    # acting hero, then two enemies, then the next hero
    e.turn_order = [h0, foes[0], foes[1], h1]
    e.current_turn_index = 0
    assert env._turn_order_features(h0.id)[1] == pytest.approx(2 / RANKS)

    # a hero immediately after stops the count at zero
    e.turn_order = [h0, h1, foes[0], foes[1]]
    e.current_turn_index = 0
    assert env._turn_order_features(h0.id)[1] == 0.0

    # a DEAD enemy in the queue is skipped, not counted, and does not stop it
    foes[0].is_dead = True
    e.turn_order = [h0, foes[0], foes[1], h1]
    e.current_turn_index = 0
    assert env._turn_order_features(h0.id)[1] == pytest.approx(1 / RANKS)


def test_queue_position_is_a_fraction_of_the_round(store):
    env = DarkestDungeonEnv()
    env.reset(seed=1)
    e = env.engine
    e.turn_order = list(e.heroes) + list(e.enemies)
    e.current_turn_index = 0
    assert env._turn_order_features(e.heroes[0].id)[0] == 0.0
    e.current_turn_index = len(e.turn_order) // 2
    mid = env._turn_order_features(e.heroes[0].id)[0]
    assert 0.0 < mid < 1.0


def test_an_empty_queue_does_not_raise():
    env = DarkestDungeonEnv()
    env.reset(seed=1)
    env.engine.turn_order = []
    assert env._turn_order_features(0) == [0.0, 0.0]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
