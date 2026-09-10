"""`CombatEngine.threat_forecast` -- what the enemy board is about to do.

This is the observability edge: a human estimates the enemy's next move from
experience, and every input here is exact. The brain weights are graded against
the running game at 46/46 by `tools/brain_oracle.py` and the hit rule at
155/155 by `tools/hit_oracle.py`.

The property that matters more than any number it returns is that reading it
CHANGES NOTHING. A redundant draw would move the RNG stream and invalidate
every trace comparison after it -- the same reason `.dot_irresistible` is
honoured by skipping its roll rather than rolling against a zero resist.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests._fixtures import get_store
from engine.combat_engine import CombatEngine
from engine.rng import SeededRNG
from env.expedition_sequence_env import ExpeditionSequenceEnv


def _trajectory(seed: int, peek: bool, steps: int = 60):
    """Play a fixed episode, optionally calling the forecast every step."""
    env = ExpeditionSequenceEnv(sampler_seed=0)
    obs, _ = env.reset(seed=seed)
    env.action_space.seed(seed)          # the space is NOT seeded by reset()
    trail = []
    for _ in range(steps):
        if peek:
            env.engine.threat_forecast()
        mask = env.action_masks()
        legal = [i for i, m in enumerate(mask) if m]
        if not legal:
            break
        a = legal[env.action_space.sample() % len(legal)]
        obs, r, term, trunc, _info = env.step(a)
        trail.append((round(float(r), 9), term, trunc,
                      tuple(round(h.hp, 6) for h in env.engine.heroes),
                      tuple(round(h.stress, 6) for h in env.engine.heroes),
                      tuple(round(e.hp, 6) for e in env.engine.enemies)))
        if term or trunc:
            break
    return trail


def test_reading_the_forecast_does_not_move_the_RNG():
    """The whole contract. If this fails, every trace comparison and every
    paired A/B taken after a forecast call is measuring the forecast."""
    for seed in (1, 7, 99):
        assert _trajectory(seed, peek=False) == _trajectory(seed, peek=True), (
            f"seed {seed}: calling threat_forecast changed the episode")


def test_a_stunned_enemy_forecasts_nothing():
    """A stunned enemy does not act this round, which is the entire reason
    stunning buys a stall turn. If it still contributed, the policy could
    never read that."""
    env = ExpeditionSequenceEnv(sampler_seed=0)
    env.reset(seed=3)
    live = [e for e in env.engine.enemies if e.is_alive()]
    assert live
    before = sum(v["damage"] + v["stress"] for v in env.engine.threat_forecast().values())
    for e in live:
        e.is_stunned = True
    after = sum(v["damage"] + v["stress"] for v in env.engine.threat_forecast().values())
    assert before > 0.0, "the board was forecast to do nothing at all"
    assert after == 0.0, "a stunned board still forecast a threat"


def test_it_reports_STRESS_separately_from_damage():
    """swine_wretch_A is the case that proves the point: a 1-1 damage band and
    one skill, `vomit`, that is really a stress attack. `_threat_ratio` reads
    that board as almost harmless because it only knows the damage band.

    The board is BUILT here, not sampled. It used to reset an expedition at a
    fixed seed and rely on the draw being swine -- which the 2026-08-26 comp
    re-export broke, because a different comp pool draws a different scenario
    from the same seed. A test about one monster should not depend on the
    sampler ever having offered it.
    """
    store = get_store()
    eng = CombatEngine(store, SeededRNG(1))
    eng.reset(hero_defs=[store.heroes["crusader"], store.heroes["vestal"]],
              enemy_defs=[store.enemies["swine_wretch_A"]] * 2)
    f = eng.threat_forecast()
    assert f, "no living heroes to forecast for"
    dmg = sum(v["damage"] for v in f.values())
    stress = sum(v["stress"] for v in f.values())
    assert stress > dmg, (
        "the forecast folded stress into damage; they are different currencies")


def test_every_living_hero_gets_an_entry_and_the_dead_do_not():
    env = ExpeditionSequenceEnv(sampler_seed=0)
    env.reset(seed=5)
    env.engine.heroes[0].hp = 0
    env.engine.heroes[0].is_dead = True
    f = env.engine.threat_forecast()
    living = {h.id for h in env.engine.heroes if h.is_alive()}
    assert set(f) == living


def test_probabilities_stay_in_range():
    env = ExpeditionSequenceEnv(sampler_seed=0)
    env.reset(seed=11)
    for v in env.engine.threat_forecast().values():
        assert 0.0 <= v["p_targeted"] <= 1.0
        assert v["damage"] >= 0.0 and v["stress"] >= 0.0


def test_crit_stress_is_forecast_and_is_NON_LOCAL():
    """A monster crit charges `monster_crit_stress_damage` (10) to whoever it
    hit and 0.5 x 5 to every OTHER living hero, so one crit is worth 17.5
    party stress with four alive. It is the largest stress source in a normal
    round.

    Measured 2026-08-25: without it the forecast under-predicted realised
    stress by ~2x at Veteran and Champion (ratios 1.97 and 2.11 over ~450
    rounds each). With it, 1.07 and 1.37.

    The NON-LOCAL half is the part worth pinning. An attack aimed at the front
    rank must raise the forecast stress of the hero at the BACK -- that is
    faithful, and it is most of what makes a crit frightening.
    """
    env = ExpeditionSequenceEnv(sampler_seed=0)
    env.reset(seed=2, options={"sampler_seed": 2})
    live = [e for e in env.engine.enemies if e.is_alive()]
    heroes = [h for h in env.engine.heroes if h.is_alive()]
    assert live and len(heroes) > 1

    for e in live:
        e.crit = 0.0
    zero = env.engine.threat_forecast()
    for e in live:
        e.crit = 1.0                  # every landed hit crits
    lots = env.engine.threat_forecast()

    assert sum(v["stress"] for v in lots.values()) >         sum(v["stress"] for v in zero.values()), "crit stress is not forecast"

    # Non-local: raise it for EVERY hero, including ones barely targeted.
    gained = [lots[h.id]["stress"] - zero[h.id]["stress"] for h in heroes]
    assert all(g > 0 for g in gained), (
        "a crit raised nobody's stress but the target's; the companion term "
        f"is missing (per-hero gains {gained})")


def test_crit_stress_still_draws_nothing():
    """The whole contract of the forecast, re-checked after adding a term that
    reads `attacker.crit` and the rules table."""
    for seed in (5, 21):
        assert _trajectory(seed, peek=False) == _trajectory(seed, peek=True)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  PASS {name}")
