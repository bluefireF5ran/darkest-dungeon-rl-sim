"""One roll, and the resist is subtracted from it.

This engine used to roll twice for a status effect: `.chance` to apply, then a
second roll against the target's resist. Two things were wrong with that, and
the shipped data is what says so.

**Every `.chance` above 100% was dead.** 572 of the game's 1973 effects carry
one -- laddered per skill rank (1.0 / 1.2 / 1.4 / 1.6) and reaching 5.0 on
Guard Break, which the wiki glosses as "500% chance". Under two independent
rolls anything at or above 1.0 is the same as 1.0, so a rank-5 skill applied
its status exactly as often as a rank-1 one and 29% of the game's effects
carried numbers that could not matter. Headroom over 100% is only worth
writing down if something is taken off it.

**DEBUFF RES was never rolled at all**, so every stat debuff landed. Measured
cost on one comp at Champion: 20 points of expedition success, and a Vestal the
search insisted on giving Illumination -- which is where this whole thread
started (`CHANGELOG.md`).

`rules.json` agrees with the single-roll shape independently: it ships the crit
bonus as `effect_crit_roll_mod: -0.2`, a modifier on the ROLL rather than on
the chance, which is the shape of one comparison.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests._fixtures import get_store

from engine.combat_engine import CombatEngine
from engine.rng import SeededRNG


@pytest.fixture(scope="module")
def store():
    return get_store()


def _engine(store, seed=1):
    engine = CombatEngine(store, SeededRNG(seed))
    engine.reset(hero_defs=[store.heroes["crusader"], store.heroes["vestal"]],
                 enemy_defs=[store.enemies["cultist_brawler_A"]] * 2,
                 surprise=(False, False), auto_resolve_to_hero=False)
    return engine


def _land_rate(store, effect_name, resist, trials=600, seed=3, resist_attr="debuff_resist"):
    """How often `effect_name`, thrown by a hero at a monster, sticks."""
    engine = _engine(store, seed)
    eff = store.effects[effect_name]
    hero, target = engine.heroes[0], engine.enemies[0]
    landed = 0
    for _ in range(trials):
        setattr(target, resist_attr, resist)
        target.buffs.clear()
        target.is_marked = False
        engine._apply_effect_to_target(target, target, hero, eff, None)
        landed += bool(target.buffs)
    return landed / trials


# ── the ladder is live ──

def test_a_higher_chance_beats_a_resist_that_a_lower_one_does_not(store):
    """The whole argument for the change. Same skill, ranks 1 and 5, same
    target: under the old two-roll shape these were identical."""
    lo = _land_rate(store, "Cutthroat Slice Debuff 1", 0.5)   # chance 1.0
    hi = _land_rate(store, "Cutthroat Slice Debuff 5", 0.5)   # chance 1.4
    assert lo == pytest.approx(0.5, abs=0.06), lo
    assert hi == pytest.approx(0.9, abs=0.05), hi


def test_the_rate_is_chance_minus_resist(store):
    for resist, expected in ((0.0, 1.0), (0.25, 0.75), (0.6, 0.4), (1.0, 0.0)):
        got = _land_rate(store, "Cutthroat Slice Debuff 1", resist)
        assert got == pytest.approx(expected, abs=0.06), (resist, got)


def test_a_resist_above_the_chance_makes_it_impossible(store):
    """A corpse carries 200% DEBUFF/STUN/MOVE RES, which is the game saying
    'never' rather than 'rarely'."""
    assert _land_rate(store, "Cutthroat Slice Debuff 5", 2.0) == 0.0


# ── who it applies to ──

def test_debuff_res_gates_a_stat_modifier_crossing_to_the_other_side(store):
    assert _land_rate(store, "Cutthroat Slice Debuff 1", 0.9) < 0.25


def test_a_self_inflicted_debuff_is_never_resisted(store):
    """Leper's Vulnerability is a cost he pays, not something he resists. The
    side test is the predicate: nothing in the effect record distinguishes
    Bolster from a Weakening Curse -- both are `combat_stat_buff`."""
    engine = _engine(store)
    hero = engine.heroes[0]
    hero.debuff_resist = 0.9
    eff = store.effects["Leper Vulnerability"]
    assert eff.buff_ids and eff.target == "performer"
    assert engine._effect_resist(eff, hero, hero) == (None, 0.0)
    # And behaviourally: it is a `combat_end` buff, so it is not tracked in
    # `.buffs` -- the stat it moves is the evidence that it landed every time.
    before = hero.dodge
    for _ in range(50):
        engine._apply_effect_to_target(hero, hero, hero, eff, None)
    assert hero.dodge != before
    assert not [line for line in engine.log if "resists" in line]


def test_a_mark_rides_through_a_resisted_debuff(store):
    """The wiki is explicit that Mark cannot be resisted by anyone, and
    Occultist's Vulnerability Curse carries both a mark and a stat debuff in
    one effect. One draw, two comparisons: the debuff half loses, the mark
    still lands."""
    engine = _engine(store, seed=5)
    eff = store.effects["Vulnerability Curse 1"]
    hero, target = engine.heroes[0], engine.enemies[0]
    marked = 0
    for _ in range(200):
        target.debuff_resist = 5.0        # the debuff half cannot land
        target.is_marked = False
        engine._apply_effect_to_target(target, target, hero, eff, None)
        marked += target.is_marked
    assert marked == 200


def test_dot_irresistible_skips_the_subtraction(store):
    """Honoured by not subtracting rather than by subtracting a zero -- the
    same rule burn already follows, and the reason is that an extra draw
    desynchronises every trace comparison after it."""
    engine = _engine(store)
    eff = next(e for e in store.effects.values() if e.dot_irresistible)
    assert eff.burn_amount is not None, "every carrier shipped today is burn"
    kind, resist = engine._effect_resist(eff, engine.enemies[0], engine.heroes[0])
    assert (kind, resist) == (None, 0.0)


# ── the shape itself ──

def test_one_draw_per_effect_not_two(store):
    """Two draws was the bug. A test on the count, because the difference is
    invisible in any single outcome and shows up as a desynchronised RNG
    stream three turns later."""
    engine = _engine(store)
    hero, target = engine.heroes[0], engine.enemies[0]
    target.debuff_resist = 0.4
    calls = []
    real = engine.rng.random
    engine.rng.random = lambda: (calls.append(1), real())[1]
    engine._apply_effect_to_target(target, target, hero,
                                   store.effects["Cutthroat Slice Debuff 1"], None)
    assert len(calls) == 1, calls


def test_exactly_one_shipped_effect_carries_two_resist_types(store):
    """`_effect_resist` returns ONE type, which is only sound because the data
    is. If a patch or a mod adds a second, this is the tripwire."""
    def types(e):
        found = []
        if e.bleed_amount is not None:
            found.append("bleed")
        if e.blight_amount is not None:
            found.append("blight")
        if e.stun_chance is not None:
            found.append("stun")
        if (e.push_distance or 0) > 0 or (e.pull_distance or 0) > 0:
            found.append("move")
        if e.buff_ids or e.is_stat_buff or e.inline_buff_type:
            found.append("debuff")
        return found
    multi = [e.name for e in store.effects.values() if len(types(e)) > 1]
    assert multi == ["Baron Blight"], multi


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
