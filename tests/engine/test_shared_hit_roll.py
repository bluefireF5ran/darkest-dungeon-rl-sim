"""A multi-target attack rolls to-hit ONCE, against every target's own chance.

Found by the game-trace oracle rather than by reading the wiki: captured
`OutputDebugString` output from the real game (tools/dbwin_capture.py, parsed
by tools/game_trace.py --aoe) showed 11/11 multi-target attacks sharing a
single `Roll to hit` while each target got its own `Chance to hit`:

    2 targets | roll=[0.565382] chance=[0.585, 0.635]
    2 targets | roll=[0.974187] chance=[0.600, 0.650]   <- both miss
    3 targets | roll=[0.441106] chance=[0.625, 0.750]

Zero counterexamples, across 2- and 3-target skills. Crit and damage rolls
stayed distinct per target in the same traces, so only the to-hit roll is
shared.

This does not change any single target's expected hit rate — it changes the
correlation between targets. A real AoE tends to hit everything or miss
everything; independent per-target rolls average out instead. See CHANGELOG.md.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests._fixtures import get_store

from engine import SeededRNG, CombatEngine, Action
from engine.resolution import resolve_attack, compute_hit_chance

from data.paths import GAME_DIR
from data.paths import CSV_DIR
store = get_store()


def _aoe_engine(n_enemies=3, seed=3):
    maa = store.heroes["man_at_arms"]
    brawler = store.enemies["cultist_brawler_A"]
    engine = CombatEngine(store, SeededRNG(seed))
    engine.reset(hero_defs=[maa], enemy_defs=[brawler] * n_enemies, hero_positions=[1])
    return engine


def test_shared_hit_roll_is_none_for_single_target():
    """Single-target attacks must keep drawing their own roll, so the RNG
    stream stays byte-identical to before this parameter existed."""
    engine = _aoe_engine(n_enemies=1)
    assert engine._shared_hit_roll(engine.enemies[:1]) is None
    assert engine._shared_hit_roll([]) is None


def test_shared_hit_roll_drawn_once_for_multi_target():
    engine = _aoe_engine()
    draws = []
    real = engine.rng.random
    engine.rng.random = lambda: (draws.append(1), real())[1]
    roll = engine._shared_hit_roll(engine.enemies)
    assert roll is not None
    assert len(draws) == 1, f"expected exactly one draw, got {len(draws)}"


def test_resolve_attack_uses_supplied_roll_and_draws_nothing():
    """With hit_roll supplied, resolve_attack must not consume a to-hit draw."""
    engine = _aoe_engine()
    attacker, target = engine.heroes[0], engine.enemies[0]
    sk = next(s for s in store.heroes["man_at_arms"].skills if s.id == "bellow")
    sd = sk.levels[max(sk.levels.keys())]

    # Rolls chosen either side of THIS attack's chance, and kept strictly
    # inside the two cutoffs so neither of those branches decides it. The
    # extremes 0.0 and 1.0 used to serve here, on the reasoning that "0.0
    # always lands and 1.0 never does" -- both are now backwards: the cutoffs
    # bracket the roll, so `roll <= 0.00` misses and `roll >= 0.95` hits
    # whatever the chance is (DECOMP.md F4).
    # Bellow is 110% ACC and this target barely dodges, so the natural chance
    # is 1.1 -- itself a live example of what the old clamp was flattening to
    # 0.95. Raise dodge to put the chance mid-band, where the roll comparison
    # rather than a cutoff decides the outcome.
    target.dodge += compute_hit_chance(attacker, target, sd, engine.rules) - 0.6
    chance = compute_hit_chance(attacker, target, sd, engine.rules)
    assert 0.0 < chance < 0.95, f"expected a mid-band chance, got {chance}"
    below, above = chance / 2.0, (chance + 0.95) / 2.0

    draws = []
    real = engine.rng.random
    engine.rng.random = lambda: (draws.append(1), real())[1]
    hit = resolve_attack(attacker, target, sd, engine.rules, engine.rng, hit_roll=below)
    to_hit_draws_on_a_hit = len(draws)
    miss = resolve_attack(attacker, target, sd, engine.rules, engine.rng, hit_roll=above)
    engine.rng.random = real

    assert hit.hit_roll == below and hit.hit is True
    assert miss.hit_roll == above and miss.hit is False
    # The actual point of the test, which it never asserted: a miss resolves
    # with no draw at all when the roll is supplied. (A HIT still draws, for
    # crit and damage -- so only the miss can count to zero.)
    assert len(draws) == to_hit_draws_on_a_hit, (
        f"a supplied roll still consumed {len(draws) - to_hit_draws_on_a_hit} "
        f"draw(s) on the miss path")


def test_all_targets_of_one_aoe_share_the_same_hit_roll():
    """The headline behaviour, observed end-to-end through the engine."""
    engine = _aoe_engine()
    seen = []
    real = resolve_attack

    import engine.combat_engine as ce

    def spy(*args, **kwargs):
        r = real(*args, **kwargs)
        seen.append(r.hit_roll)
        return r

    ce.resolve_attack = spy
    try:
        engine.submit_action(Action(engine.heroes[0].id, "bellow", 0))
        engine.step()
    finally:
        ce.resolve_attack = real

    assert len(seen) >= 3, f"expected the AoE to resolve against 3 enemies, saw {len(seen)}"
    aoe_rolls = seen[:3]
    assert len(set(aoe_rolls)) == 1, f"AoE targets used different rolls: {aoe_rolls}"


def test_aoe_outcome_is_all_or_nothing_when_chances_are_equal():
    """With identical targets, a shared roll makes the AoE hit all or none —
    never a partial split. That correlation is the whole point of the fix."""
    engine = _aoe_engine()
    hp_before = [e.hp for e in engine.enemies]
    engine.submit_action(Action(engine.heroes[0].id, "bellow", 0))
    engine.step()
    hp_after = [e.hp for e in engine.enemies]

    changed = [b != a for b, a in zip(hp_before, hp_after)]
    assert all(changed) or not any(changed), (
        f"identical targets should share one outcome, got {changed} "
        f"({hp_before} -> {hp_after})")


def test_single_target_attacks_still_vary_independently():
    """Guard against over-applying the fix: two *separate* attacks must not
    reuse one roll."""
    engine = _aoe_engine(n_enemies=1)
    attacker, target = engine.heroes[0], engine.enemies[0]
    sk = next(s for s in store.heroes["man_at_arms"].skills if s.id == "crush")
    sd = sk.levels[max(sk.levels.keys())]
    rolls = {resolve_attack(attacker, target, sd, engine.rules, engine.rng).hit_roll
             for _ in range(20)}
    assert len(rolls) > 1, "independent single-target attacks collapsed onto one roll"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  ok  {name}")
