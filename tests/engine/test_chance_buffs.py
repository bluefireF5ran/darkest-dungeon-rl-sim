"""Buff stat_types the engine silently ignored.

Fifth audit sweep: `shared/buffs/base.buffs.json` is where trinkets, quirks,
traits and skill effects all cash out, so a `stat_type` `_apply_buff_to_stats`
has no branch for is a **silent no-op** — the buff applies, nothing happens, and
no test or eval can see it.

Gated on real training exposure rather than on how many buffs exist, because a
buff nothing equips is not a bug worth fixing. Measured over 150 sampled
expeditions, counting only kit actually put on a hero:

    stun_chance             1.85 per expedition   <- the largest in the game
    scouting_chance         1.06                  (out of scope: no map layer)
    debuff_chance           0.65                  (blocked, see below)
    resolve_check_percent   0.47
    move_chance             0.19

`stun_chance` and `move_chance` follow the shape `bleed_chance`/`poison_chance`
already set: the attacker's bonus lowers the target's effective resist, because
the engine has ONE resist roll per payload and no separate chance-to-apply roll
to add a bonus to.

`resolve_check_percent` shifts the VIRTUE side of the Resolve Check.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests._fixtures import get_store

from engine.combat_engine import CombatEngine
from engine.rng import SeededRNG
from engine.stress import resolve_check


@pytest.fixture(scope="module")
def store():
    return get_store()


def _engine(store, seed=1):
    engine = CombatEngine(store, SeededRNG(seed))
    engine.reset(hero_defs=[store.heroes["crusader"], store.heroes["vestal"]],
                 enemy_defs=[store.enemies["cultist_brawler_A"]] * 2,
                 surprise=(False, False), auto_resolve_to_hero=False)
    return engine


# ── the types are wired at all ──

@pytest.mark.parametrize("stat_type,field", [
    ("stun_chance", "stun_chance_bonus"),
    ("move_chance", "move_chance_bonus"),
    ("resolve_check_percent", "resolve_check_mod"),
])
def test_the_buff_reaches_the_combatant(store, stat_type, field):
    bd = next(b for b in store.buffs.values() if b.stat_type == stat_type)
    engine = _engine(store)
    hero = engine.heroes[0]
    before = getattr(hero, field)
    engine._apply_buff_to_stats(hero, bd)
    assert getattr(hero, field) == pytest.approx(before + bd.amount)
    engine._reverse_buff_from_stats(hero, bd)
    assert getattr(hero, field) == pytest.approx(before)


def test_these_are_the_types_worth_wiring(store):
    """The gate this work used: exposure in real training kit, not how many
    buffs exist. If a future comp change makes another unhandled type common,
    the number to beat is `move_chance`'s 0.19 per expedition.

    `debuff_chance` was the one left out, because nothing rolled DEBUFF RES for
    it to modify. That is no longer true, so it must move the bonus it names
    and nothing else."""
    assert any(b.stat_type == "stun_chance" for b in store.buffs.values())
    engine = _engine(store)
    hero = engine.heroes[0]
    bd = next(b for b in store.buffs.values() if b.stat_type == "debuff_chance")
    snapshot = (hero.debuff_resist, hero.stun_chance_bonus, hero.move_chance_bonus)
    before = hero.debuff_chance_bonus
    engine._apply_buff_to_stats(hero, bd)
    assert hero.debuff_chance_bonus == pytest.approx(before + bd.amount)
    assert (hero.debuff_resist, hero.stun_chance_bonus,
            hero.move_chance_bonus) == snapshot, \
        "debuff_chance lowers the TARGET's effective resist; it is not a resist"


# ── behaviour ──

def test_a_stun_bonus_lands_more_stuns(store):
    """Against a 50% STUN RES target, a +25% attacker bonus should roughly
    halve the resists."""
    eff = store.effects["Weak Stun 1"]
    out = {}
    for bonus in (0.0, 0.25):
        engine = _engine(store, seed=7)
        engine.heroes[0].stun_chance_bonus = bonus
        stuns = 0
        for _ in range(400):
            target = engine.enemies[0]
            target.stun_resist = 0.5
            target.is_stunned = False
            engine._apply_effect_to_target(target, target, engine.heroes[0],
                                           eff, None)
            stuns += bool(target.is_stunned)
        out[bonus] = stuns
    assert out[0.25] > out[0.0], out


def test_a_move_bonus_lands_more_pushes(store):
    eff = store.effects["Push 2A"]
    out = {}
    for bonus in (0.0, 0.4):
        engine = _engine(store, seed=9)
        engine.heroes[0].move_chance_bonus = bonus
        moved = 0
        for _ in range(300):
            target = engine.enemies[0]
            target.move_resist = 0.6
            target.position = 0
            engine.enemies[1].position = 1
            was = target.position
            engine._resolve_effect(target, engine.heroes[0], eff, None)
            moved += target.position != was
        out[bonus] = moved
    assert out[0.4] > out[0.0], out


def test_the_bonus_cannot_drive_a_resist_below_zero(store):
    """A huge bonus makes the payload certain, never more than certain — a
    negative effective resist would still read correctly against `roll <`,
    but only by accident."""
    engine = _engine(store)
    engine.heroes[0].stun_chance_bonus = 5.0
    target = engine.enemies[0]
    target.stun_resist = 0.5
    target.is_stunned = False
    engine._apply_effect_to_target(target, target, engine.heroes[0],
                                   store.effects["Weak Stun 1"], None)
    # 5.0 over a 0.5 resist: the resist roll cannot fire. The `.chance` roll
    # still can, so this asserts the resist specifically.
    eff_resist = max(0.0, target.stun_resist - engine.heroes[0].stun_chance_bonus)
    assert eff_resist == 0.0


def test_resolve_check_percent_shifts_the_virtue_side(store):
    """Positive is better for the hero, and the carriers say so: `mercurial`
    is a positive quirk and carries +, `ennui` and `never_again_affliction`
    carry -."""
    rules = store.rules
    out = {}
    for mod in (-0.2, 0.0, +0.5):
        virtues = 0
        for seed in range(300):
            engine = _engine(store, seed=seed)
            hero = engine.heroes[0]
            hero.resolve_check_mod = mod
            hero.stress = 100
            resolve_check(hero, rules, SeededRNG(seed))
            virtues += hero.is_virtuous
        out[mod] = virtues
    assert out[-0.2] < out[0.0] < out[0.5], out


def test_the_carriers_have_the_sign_the_code_assumes(store):
    """If a patch ever flips the sign convention, this is where it shows."""
    def amount(buff_id):
        return store.buffs[buff_id].amount

    ids = {b.id: b.amount for b in store.buffs.values()
           if b.stat_type == "resolve_check_percent"}
    assert ids.get("never_again_affliction", 0) < 0
    assert any(v > 0 for v in ids.values())


def test_an_unmodified_check_is_the_shipped_split(store):
    """The default has to stay byte-identical: 25% virtue / 75% affliction,
    which is the pair of rules.json values summing to 1.0."""
    rules = store.rules
    virtues = 0
    for seed in range(600):
        engine = _engine(store, seed=seed)
        hero = engine.heroes[0]
        hero.stress = 100
        resolve_check(hero, rules, SeededRNG(seed))
        virtues += hero.is_virtuous
    assert 0.20 < virtues / 600 < 0.31, virtues / 600


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
