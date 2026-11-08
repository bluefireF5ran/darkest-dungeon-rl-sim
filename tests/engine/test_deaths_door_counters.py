"""The engine's Death's Door counters, and the `--dd-exposure-penalty` charge
that reads them (Fran, 2026-10-12: "Train it", docs/NEXT.md item 3).

`deaths_door_entries`, `deathblow_rolls` and `deathblow_deaths` count over
the engine's LIFE, so `reset()` between combats must leave them alone; the
env diffs `deathblow_rolls` around a step that can cross a combat boundary.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests._fixtures import get_store

from engine import SeededRNG, CombatEngine

store = get_store()


def _engine(seed=0):
    e = CombatEngine(store, SeededRNG(seed))
    e.reset(hero_defs=[store.heroes["crusader"]] * 4, enemy_defs=[store.enemies["madman_A"]])
    return e


def test_an_entry_rolls_nothing_and_each_later_hit_rolls_once():
    e = _engine()
    h = e.heroes[0]
    h.hp = 0
    e._handle_deaths_door(h, e.enemies[0])
    assert (e.deaths_door_entries, e.deathblow_rolls, e.deathblow_deaths) == (1, 0, 0)
    rolls = 0
    while not h.is_dead and rolls < 200:
        e._handle_deaths_door(h, e.enemies[0])
        rolls += 1
    assert e.deathblow_rolls == rolls and e.deathblow_deaths == 1
    assert e.deaths_door_entries == 1


def test_the_counters_survive_a_reset_between_combats():
    e = _engine()
    e.heroes[0].hp = 0
    e._handle_deaths_door(e.heroes[0], e.enemies[0])
    e._handle_deaths_door(e.heroes[0], e.enemies[0])
    before = (e.deaths_door_entries, e.deathblow_rolls)
    e.reset(hero_defs=[store.heroes["crusader"]] * 4, enemy_defs=[store.enemies["madman_A"]])
    assert (e.deaths_door_entries, e.deathblow_rolls) == before


def _play(penalty, seed, steps=3000):
    """First legal action every step, so two envs on one seed walk one path:
    the penalty changes the reward, never the dynamics."""
    from env import ExpeditionSequenceEnv, mask_fn
    env = ExpeditionSequenceEnv(sampler_seed=seed, dd_exposure_penalty=penalty)
    env.reset(seed=seed)
    rewards, rolls = [], []
    for _ in range(steps):
        before = env.engine.deathblow_rolls
        action = int(next(i for i, ok in enumerate(mask_fn(env)) if ok))
        _obs, r, term, trunc, _info = env.step(action)
        rewards.append(r)
        rolls.append(env.engine.deathblow_rolls - before)
        if term or trunc:
            break
    return rewards, rolls


def test_the_penalty_charges_exactly_p_per_roll_and_nothing_else():
    seen = 0
    for seed in range(6):
        base, rolls = _play(0.0, seed)
        charged, rolls2 = _play(5.0, seed)
        assert rolls == rolls2
        for r0, r1, k in zip(base, charged, rolls):
            assert abs((r0 - r1) - 5.0 * k) < 1e-6
        seen += sum(rolls)
    assert seen > 0, "no deathblow roll in six expeditions: the test proves nothing"


if __name__ == "__main__":
    for name, t in list(globals().items()):
        if name.startswith("test_"):
            t()
            print("  ok ", name)
