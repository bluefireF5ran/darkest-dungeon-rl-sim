"""A camp effect's `.chance` was scored but never rolled.

Second finding of the 2026-08-23 camping sweep. `_effect_value` correctly
scales a candidate's expected value by `effect.chance` (`return total *
effect.chance`), and that was the ONLY place the field was read -- `_apply_effect`
fired every effect unconditionally.

Ten shipped effects carry a chance below 1.0, across **131 equipped comp
slots**, and the error runs in both directions:

* `gallows_humor` is meant to be a gamble -- 75% that an ally is relieved of 20
  stress, 25% that they gain 10. Both fired every time, so a mostly-good skill
  was a guaranteed net -10 per ally.
* `abandon_hope` inflicted 10 AND 5 stress on every ally where its expected
  cost is 7.5.
* `quarantine` healed 20 AND 15 where it should average 17.5.

DECLARED READING: one roll per TARGET, and a skill's rows roll INDEPENDENTLY.
Nothing in the data marks `gallows_humor`'s heal and its stress damage as
exclusive, so an ally can legitimately get both or neither. The alternative --
a single roll choosing between them -- would need a marker the files do not
carry. Falsifier: camp with Gallows Humor in the real game and count how often
one ally takes the stress hit *and* the relief; independent rolls predict
18.75% of allies, exclusive rolls predict 0%.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from collections import Counter

from tests._fixtures import get_store

from engine.camping import resolve_camp
from engine.combat_engine import CombatEngine
from engine.rng import SeededRNG

store = get_store()


def _camp(hero_class, skills, seed, stress=60, caster_stress=None):
    eng = CombatEngine(store, SeededRNG(seed))
    eng.reset([store.heroes[hero_class]] * 4,
              [next(iter(store.enemies.values()))])
    for h in eng.heroes:
        h.stress = stress
    if caster_stress is not None:
        eng.heroes[0].stress = caster_stress
    res = resolve_camp(SeededRNG(seed), store, eng.heroes,
                       [list(skills)] + [[]] * 3,
                       apply_buff=eng._apply_buff_to_stats,
                       clear_mortality=eng.clear_mortality_tier)
    return eng, res


def test_the_data_still_carries_sub_one_chances():
    """The premise. If a re-export ever flattens these to 1.0 the whole fix is
    dead code, and this says so rather than passing quietly."""
    sub1 = [(cid, e.stat_type, e.chance)
            for cid, sk in store.camping_skills.items()
            for e in sk.effects if e.chance < 1.0]
    assert len(sub1) >= 8, sub1
    assert any(cid == "gallows_humor" for cid, _t, _c in sub1)
    print(f"  PASS test_the_data_still_carries_sub_one_chances ({len(sub1)} effects)")


def test_gallows_humor_is_a_gamble_not_a_certainty():
    """The headline case, and the one where the old behaviour was worst: both
    rows firing every time made a 75%-good skill a guaranteed net -10.

    Four outcomes must all appear, at roughly 0.75/0.25 crossed. Tolerances are
    wide because the point is the SHAPE -- the old code produced exactly one
    outcome, 100% of the time.
    """
    flat = []
    for seed in range(400):
        eng, _res = _camp("grave_robber", ["gallows_humor"], seed)
        flat.extend(h.stress for h in eng.heroes[1:])
    seen = Counter(flat)

    # 60 start: -20 heal -> 40, +10 damage -> 70, both -> 50, neither -> 60.
    assert set(seen) == {40, 50, 60, 70}, dict(seen)
    n = len(flat)
    healed_only = seen[40] / n
    both = seen[50] / n
    assert 0.50 < healed_only < 0.63, f"healed-only {healed_only:.3f}, want ~0.5625"
    assert 0.13 < both < 0.25, f"both {both:.3f}, want ~0.1875"
    mean = sum(flat) / n
    assert abs(mean - 47.5) < 1.5, f"mean {mean:.2f}, want 47.5"
    print(f"  PASS test_gallows_humor_is_a_gamble_not_a_certainty "
          f"(mean {mean:.2f}, 4 outcomes)")


def test_abandon_hope_no_longer_inflicts_both_penalties_every_time():
    """Two independent 50% stress hits on each ally: 10 and 5. Applying both
    unconditionally cost 15 where the expectation is 7.5 -- double.

    The caster is pinned near breaking and the allies are fresh, because that
    is the only state where the heuristic buys this at all: it trades party
    stress for the caster's, so with everyone at 20 the ally cost outweighs a
    self-heal capped at what the caster actually carries, and the skill is
    correctly declined.
    """
    cls = store.camping_skills["abandon_hope"].hero_classes[0]
    flat = []
    for seed in range(300):
        eng, res = _camp(cls, ["abandon_hope"], seed, stress=10,
                         caster_stress=100)
        if not any(s == "abandon_hope" for _i, s in res.skills_used):
            continue
        flat.extend(h.stress for h in eng.heroes[1:])
    assert flat, "abandon_hope was never bought; the scenario is wrong"
    seen = Counter(flat)
    assert 10 in seen, f"an ally must sometimes escape entirely: {dict(seen)}"
    assert seen.get(25, 0) < len(flat), "every ally still took both penalties"
    mean = sum(flat) / len(flat)
    assert abs(mean - 17.5) < 1.5, f"mean {mean:.2f}, want 17.5"
    print(f"  PASS test_abandon_hope_no_longer_inflicts_both_penalties_every_time "
          f"(mean {mean:.2f})")


def test_a_certainty_takes_no_draw():
    """The `dot_irresistible` precedent. 185 of 195 shipped camp effects are
    chance 1.0, and rolling against them would shift the RNG stream for every
    party without changing a single outcome -- invalidating every seeded trace
    for nothing."""
    eng = CombatEngine(store, SeededRNG(5))
    eng.reset([store.heroes["crusader"]] * 4,
              [next(iter(store.enemies.values()))])
    for h in eng.heroes:
        h.stress = 60
    rng = SeededRNG(5)
    before = rng.call_count
    # `encourage` is a single chance-1.0 stress heal.
    resolve_camp(rng, store, eng.heroes, [["encourage"]] + [[]] * 3,
                 apply_buff=eng._apply_buff_to_stats,
                 clear_mortality=eng.clear_mortality_tier)
    # The only draw resolve_camp itself takes is the ambush roll at the end.
    assert rng.call_count - before <= 1, (
        f"{rng.call_count - before} draws for a campfire of certainties")
    print("  PASS test_a_certainty_takes_no_draw")


def test_the_scoring_already_used_the_chance_and_still_does():
    """`_effect_value` scaling by chance was always right -- it is the expected
    value of the candidate, which is what a greedy spend should rank on. The
    fix is at apply time only, and this pins that the scoring was not broken
    in passing."""
    from engine.camping import _effect_value

    eng = CombatEngine(store, SeededRNG(0))
    eng.reset([store.heroes["grave_robber"]] * 4,
              [next(iter(store.enemies.values()))])
    for h in eng.heroes:
        h.stress = 60
    heal = next(e for e in store.camping_skills["gallows_humor"].effects
                if e.stat_type == "stress_heal_amount" and e.chance < 1.0)
    v = _effect_value(heal, eng.heroes, [1])
    assert v > 0
    # Scaled by 0.75, so strictly under what a certainty of the same size buys.
    certain = _effect_value(
        type(heal)(target=heal.target, amount=heal.amount,
                   stat_type=heal.stat_type, stat_sub_type=heal.stat_sub_type,
                   duration=None, requirements=[], chance=1.0),
        eng.heroes, [1])
    assert v < certain, (v, certain)
    assert abs(v - certain * 0.75) < 1e-9
    print("  PASS test_the_scoring_already_used_the_chance_and_still_does")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
