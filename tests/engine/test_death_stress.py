"""What a death costs the survivors — which, until 2026-08-21, was nothing.

`shared/rules.json` ships `death_party_stress_chance: 0.5` and
`death_party_stress_damage: 12`. Both are read by `Darkest.exe` (parse sites
`0x1404ea351` and `0x1404ea3d1`) and were read by nothing here, so a hero dying
was priced purely as lost output: the party lost a turn per round and gained
not one point of stress for watching a friend die.

That is the single largest reason the **death spiral** could not happen in this
simulator. 12 stress at even odds across three survivors is ~18 points of party
stress per death, landing precisely when the party can least afford it, and it
is the mechanism that turns one loss into a cascade of afflictions and then an
expedition. A policy trained without it has never seen the real cost of a
death, and neither has any reward tuned against it.
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


def _party(store, n=4, seed=1):
    engine = CombatEngine(store, SeededRNG(seed))
    engine.reset(hero_defs=[store.heroes[h] for h in
                            ("crusader", "vestal", "arbalest", "hellion")][:n],
                 enemy_defs=[store.enemies["cultist_brawler_A"]],
                 surprise=(False, False), auto_resolve_to_hero=False)
    return engine


def _kill(engine, hero):
    """Put a hero on Death's Door and then take the deathblow.

    Deliberately does NOT reseed: the survivors' 50% rolls come off the
    engine's own stream, so reseeding here would make every trial in a loop
    draw the same numbers and turn an average into one sample repeated 120
    times. It read exactly 12.00 stress per death, which is what gave it
    away."""
    hero.is_deaths_door = True
    hero.hp = 0
    while not hero.is_dead:
        engine._handle_deaths_door(hero, engine.enemies[0])
    return hero


# ── the numbers are the game's ──

def test_the_scalars_are_parsed(store):
    assert store.rules.death_party_stress_chance == 0.5
    assert store.rules.death_party_stress_damage == 12.0


def test_a_death_stresses_the_survivors(store):
    """The mechanic itself. Averaged over seeds because the roll is per ally at
    50%, so any single trial can legitimately stress nobody."""
    total, trials = 0, 120
    for seed in range(trials):
        engine = _party(store, seed=seed)
        victim = engine.heroes[0]
        before = [h.stress for h in engine.heroes[1:]]
        _kill(engine, victim)
        total += sum(h.stress - b for h, b in zip(engine.heroes[1:], before))
    per_death = total / trials
    # 3 survivors x 12 stress x 50% = 18 expected.
    assert 13.0 < per_death < 23.0, per_death


def test_the_dead_hero_pays_nothing_extra(store):
    """`exclude=` and the `is_alive()` filter both cover this, and it is worth
    pinning: charging the corpse would silently inflate every mean-stress
    number the expedition reward is built on."""
    engine = _party(store)
    victim = engine.heroes[0]
    before = victim.stress
    _kill(engine, victim)
    assert victim.stress == before


def test_a_lone_hero_dying_stresses_nobody(store):
    """The last hero's death charges nobody, and the survivor loop must not
    charge the corpse itself.

    Only the `# must not raise` comment stood here until 2026-08-27, so the test
    could not distinguish "nobody was stressed" from "everybody was".
    """
    engine = _party(store, n=1)
    hero = engine.heroes[0]
    before = hero.stress
    _kill(engine, hero)
    assert hero.is_dead
    assert hero.stress == before, "the dead hero was charged for its own death"


def test_no_stress_for_a_monster_dying(store):
    """`_apply_stress_to_allies` walks the SOURCE's side, so this would have
    charged the other monsters — and `_apply_stress` no-ops on non-heroes, but
    relying on that silently would break the moment monsters got stress."""
    engine = _party(store)
    before = [h.stress for h in engine.heroes]
    enemy = engine.enemies[0]
    enemy.hp = 0
    engine._handle_deaths_door(enemy, engine.heroes[0])
    assert enemy.is_dead
    assert [h.stress for h in engine.heroes] == before


# ── every route a hero can die by ──

def test_a_dot_kill_charges_the_party_too(store):
    """A bleed finishing a hero at Death's Door routes through
    `_handle_deaths_door`, so it must charge the same as a weapon did."""
    engine = _party(store)
    victim = engine.heroes[0]
    victim.is_deaths_door = True
    victim.hp = 0
    charged = 0
    for _ in range(40):
        before = sum(h.stress for h in engine.heroes[1:])
        engine._check_deaths_door_dot(victim)
        charged += sum(h.stress for h in engine.heroes[1:]) - before
        if victim.is_dead:
            break
    assert victim.is_dead
    assert charged > 0


def test_a_heart_attack_charges_the_party(store):
    """The one death that bypasses the deathblow roll entirely."""
    engine = _party(store)
    victim = engine.heroes[0]
    victim.is_deaths_door = True
    victim.hp = 0
    before = sum(h.stress for h in engine.heroes[1:])
    engine._trigger_heart_attack(victim)
    assert victim.is_dead
    assert sum(h.stress for h in engine.heroes[1:]) > before


def test_an_instant_kill_effect_now_fires_death_reactions(store):
    """A bug found by routing all three death sites through one helper:
    `is_kill_effect` never called `_maybe_trigger_death_reactions`, so a
    Flagellant executed by an instant-kill effect skipped Last Gift and Last
    Insult. Both are Flagellant-only, which is why nothing noticed."""
    engine = CombatEngine(store, SeededRNG(1))
    engine.reset(hero_defs=[store.heroes[h] for h in
                            ("flagellant", "crusader", "vestal")],
                 enemy_defs=[store.enemies["cultist_brawler_A"]],
                 surprise=(False, False), auto_resolve_to_hero=False)
    flag = engine.heroes[0]
    assert store.heroes["flagellant"].death_reactions, \
        "premise: the Flagellant is the class that has these"
    for h in engine.heroes[1:]:
        h.hp = max(1, h.max_hp // 2)
    hp_before = [h.hp for h in engine.heroes[1:]]
    # `kill_target`, not the first is_kill_effect in the store: `kill_self`
    # retargets onto the PERFORMER, which here is the monster.
    kill = store.effects["kill_target"]
    engine._resolve_effect(flag, engine.enemies[0], kill, None)
    assert flag.is_dead
    # "Flag Last Gift" heals the rest of the party 10 HP.
    assert [h.hp for h in engine.heroes[1:]] != hp_before


# ── the crit status bonus is the game's own number now ──

def test_the_crit_status_bonus_comes_from_rules_json(store):
    """`CRIT_STATUS_CHANCE_BONUS = 0.20` was a wiki-sourced hardcode, and
    `rules.json` has been shipping exactly it as `effect_crit_roll_mod: -0.2`
    all along — expressed as a modifier on the ROLL, hence the sign.

    `roll + mod < chance` and `roll < chance - mod` are the same predicate, and
    unlike the accuracy cutoffs (DECOMP F4) nothing clamps either side, so the
    two forms cannot diverge. Same behaviour, one fewer invented constant."""
    import engine.combat_engine as ce
    assert not hasattr(ce, "CRIT_STATUS_CHANCE_BONUS"), \
        "the hardcode is gone; the rules value is the only source"
    assert store.rules.effect_crit_roll_mod == -0.2


def test_a_crit_lands_a_status_more_often(store):
    """The behaviour the constant buys, measured rather than asserted from the
    source: the same effect at the same chance applies more often on a crit."""
    eff = store.effects["Weak Stun 1"]
    assert eff.chance == 0.6 and eff.stun_chance
    hits = {False: 0, True: 0}
    for crit in (False, True):
        engine = _party(store, seed=11)
        for _ in range(400):
            target = engine.enemies[0]
            target.stun_resist = 0.0
            target.is_stunned = False
            engine._apply_effect_to_target(target, target, engine.heroes[0],
                                           eff, None, is_crit=crit)
            hits[crit] += bool(target.is_stunned)
    # 60% against 80%: about 240 versus about 320 over 400 draws.
    assert hits[False] < hits[True], hits
    assert 0.12 < (hits[True] - hits[False]) / 400 < 0.28, hits


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
