"""A corpse is a target, not scenery.

Closing a gap that had been open for the whole life of the project:
`get_valid_actions` built its enemy pool from `e.is_alive()`, and
`is_alive() == not is_dead and not is_corpse`, so no hero was ever OFFERED an
attack on a corpse. The real game gives corpse_A a stat block precisely so you
can destroy one — `.hp 7 .def -20% .prot 0` and `.can_be_missed False` — and the
play it enables is the one raised from live play: with one enemy left behind a
corpse, breaking the corpse collapses the line and pulls the survivor into
reach of a front-rank skill.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests._fixtures import get_store

import pytest

from engine import SeededRNG, CombatEngine
from engine.action import Action


from data.paths import GAME_DIR
from data.paths import CSV_DIR


@pytest.fixture(scope="module")
def store():
    return get_store()


def _engine(store, hero_ids, enemy_ids, seed=7):
    engine = CombatEngine(store, SeededRNG(seed))
    engine.reset([store.heroes[h] for h in hero_ids],
                 [store.enemies[e] for e in enemy_ids],
                 auto_resolve_to_hero=False)
    return engine


def _make_corpse(engine, enemy):
    """Kill an enemy the way a normal (non-crit, non-DoT) blow does."""
    enemy.hp = 0
    engine._handle_deaths_door(enemy, engine.heroes[0], is_crit=False)
    return enemy


# ── the stat block ──

def test_the_corpse_classes_load_and_stay_out_of_the_enemy_pool(store):
    """Real data, in its own dict. Merging them into store.enemies would put
    them in reach of encounter sampling and every diagnostic that iterates it."""
    assert store.corpses["corpse_A"].stats.hp == 7
    assert store.corpses["corpse_large_A"].stats.hp == 15
    assert store.corpses["corpse_large_A"].size == 2
    assert not [k for k in store.enemies if k.startswith("corpse")]


def test_a_fresh_corpse_carries_the_corpse_stat_block_not_the_dead_monsters(store):
    """The whole point: 7 HP, not the husk of a 40-HP monster. Without this,
    'attack the corpse' would be a multi-turn job nobody would ever choose."""
    engine = _engine(store, ["crusader"], ["cultist_brawler_A"])
    enemy = engine.enemies[0]
    assert enemy.max_hp > 7, "pick a monster whose own HP differs from a corpse's"
    _make_corpse(engine, enemy)
    assert enemy.is_corpse
    assert enemy.max_hp == store.corpses["corpse_A"].stats.hp == 7
    assert enemy.hp == 7
    assert enemy.prot == 0.0


def test_the_variant_follows_the_slain_monsters_own_death_class(store):
    """corpse_A/B/C/D is the difficulty scale, and the slain monster names its
    own via `death_class: .monster_class_id`. Reading it off the wrong variant
    would silently give a Champion corpse Apprentice HP."""
    engine = _engine(store, ["crusader"], ["cultist_brawler_B"])
    ed = store.enemies["cultist_brawler_B"]
    assert ed.corpse_id == "corpse_B"
    _make_corpse(engine, engine.enemies[0])
    assert engine.enemies[0].max_hp == store.corpses["corpse_B"].stats.hp


# ── the action ──

def test_a_corpse_is_offered_as_a_target(store):
    engine = _engine(store, ["crusader"], ["cultist_brawler_A", "cultist_brawler_A"])
    corpse = _make_corpse(engine, engine.enemies[0])
    actions = engine.get_valid_actions(engine.heroes[0].id)
    assert any(a.target_id in corpse.occupied_positions for a in actions), \
        "no action reaches the corpse's rank"


def test_a_skill_with_no_damage_is_not_offered_against_a_corpse(store):
    """Blinding Gas is `.dmg -100%` — a stun that cannot scratch a corpse, and a
    corpse resists stun at 200% and never takes a turn anyway. Offering it would
    widen the action space with a move that provably does nothing."""
    engine = _engine(store, ["plague_doctor"], ["cultist_brawler_A", "cultist_brawler_A"])
    hero = engine.heroes[0]
    corpse = _make_corpse(engine, engine.enemies[0])
    gas = engine.get_skill_data("blinding_gas", level=hero.skill_tier)
    assert gas is not None and gas.dmg_mod == -1.0
    reaching_gas = [a for a in engine.get_valid_actions(hero.id)
                    if a.skill_id == "blinding_gas"
                    and a.target_id in corpse.occupied_positions]
    assert reaching_gas == []


def test_an_ally_skill_never_reaches_a_corpse(store):
    engine = _engine(store, ["vestal", "crusader"], ["cultist_brawler_A", "cultist_brawler_A"])
    _make_corpse(engine, engine.enemies[0])
    for hero in engine.heroes:
        for a in engine.get_valid_actions(hero.id):
            if engine._is_ally_skill(a.skill_id):
                assert a.target_id < len(engine.heroes)


# ── resolution ──

def test_an_attack_on_a_corpse_cannot_miss(store):
    """`battle_modifier: ... .can_be_missed False`. Asserted over many seeds
    rather than one: a single lucky roll proves nothing about a hit floor."""
    for seed in range(30):
        engine = _engine(store, ["crusader"], ["cultist_brawler_A", "cultist_brawler_A"],
                         seed=seed)
        hero = engine.heroes[0]
        corpse = _make_corpse(engine, engine.enemies[0])
        before = corpse.hp
        engine._execute_action(Action(hero.id, "smite", corpse.position))
        assert corpse.hp < before or not corpse.is_corpse, \
            f"seed {seed}: the corpse took no damage"


def test_destroying_a_corpse_pulls_the_survivor_forward(store):
    """The play this whole change exists for. A rank-1-only skill could not
    reach the survivor while the corpse held the front rank; after the corpse
    breaks, it can."""
    engine = _engine(store, ["crusader"], ["cultist_brawler_A", "cultist_brawler_A"])
    hero = engine.heroes[0]
    corpse, survivor = engine.enemies[0], engine.enemies[1]
    _make_corpse(engine, corpse)
    engine._collapse_enemy_position_gaps()
    assert corpse.position == 0 and survivor.position == 1

    corpse.hp = 1
    engine._execute_action(Action(hero.id, "smite", corpse.position))
    assert not corpse.is_corpse, "the corpse survived a full hit at 1 HP"
    assert survivor.position == 0, "the line did not close over the broken corpse"


def test_breaking_a_corpse_is_not_a_second_death(store):
    """A corpse is already is_dead. Routing its destruction back through
    _handle_deaths_door would re-run the slain-monster path — a second 'is
    slain!', another killing-blow stress relief, and a fresh corpse."""
    engine = _engine(store, ["crusader"], ["cultist_brawler_A", "cultist_brawler_A"])
    hero = engine.heroes[0]
    corpse = _make_corpse(engine, engine.enemies[0])
    corpse.hp = 1
    stress_before = hero.stress
    engine._execute_action(Action(hero.id, "smite", corpse.position))
    assert not corpse.is_corpse
    assert corpse.corpse_rounds_remaining is None
    assert hero.stress == stress_before
    assert sum(1 for line in engine.log if "is slain" in line) == 1


def test_a_corpse_never_ripostes_guards_or_takes_effects(store):
    """Damage and nothing else — every status a corpse could receive is either
    resisted at 200% or inert because it never takes a turn."""
    engine = _engine(store, ["hellion"], ["cultist_brawler_A", "cultist_brawler_A"])
    hero = engine.heroes[0]
    corpse = _make_corpse(engine, engine.enemies[0])
    # Wicked Slice/Iron Swan carry bleed; pick whichever damaging skill this
    # hero can launch at the corpse's rank.
    action = next(a for a in engine.get_valid_actions(hero.id)
                  if a.target_id in corpse.occupied_positions)
    engine._execute_action(action)
    assert not corpse.bleeds and not corpse.blights
    assert not corpse.is_stunned


def test_an_aoe_that_can_only_reach_a_corpse_still_resolves(store):
    """get_valid_actions and _resolve_action_targets must agree. An AoE offered
    because a corpse stood in its ranks, then re-resolved to zero targets at
    execution time, would silently burn the turn."""
    # Leper's Hew: launch 1-2, target ~1-2, a real damaging front AoE.
    engine = _engine(store, ["leper"], ["cultist_brawler_A", "cultist_brawler_A",
                                        "cultist_brawler_A"])
    hero = engine.heroes[0]
    # Corpse in front, so Hew's ranks hold a corpse alongside the living.
    corpse = _make_corpse(engine, engine.enemies[0])
    checked = 0
    for a in engine.get_valid_actions(hero.id):
        sd = engine.get_skill_data(a.skill_id, level=hero.skill_tier)
        if sd is None or not sd.is_aoe:
            continue
        checked += 1
        targets = engine._resolve_action_targets(a, sd, is_ally_skill=False)
        assert targets, f"{a.skill_id} was offered but resolves to nothing"
    assert checked, "no AoE was exercised — the test proved nothing"
    assert corpse.is_corpse


# ── what must NOT have changed ──

def test_the_monster_ai_still_never_targets_a_corpse(store):
    """`can_be_random_target False`. include_corpses defaults off precisely so
    every other caller of _find_combatant_by_position keeps meaning 'living'."""
    engine = _engine(store, ["crusader"], ["cultist_brawler_A", "cultist_brawler_A"])
    corpse = _make_corpse(engine, engine.enemies[0])
    found = engine._find_combatant_by_position(corpse.position, enemy=True)
    assert found is None or found is not corpse


def test_a_dot_kill_still_leaves_no_corpse_to_attack(store):
    """Wiki-confirmed and already modelled: a unit killed by bleed/blight leaves
    none. The new targeting must not resurrect one."""
    engine = _engine(store, ["crusader"], ["cultist_brawler_A", "cultist_brawler_A"])
    enemy = engine.enemies[0]
    enemy.hp = 0
    engine._check_deaths_door_dot(enemy)
    assert enemy.is_dead and not enemy.is_corpse
    actions = engine.get_valid_actions(engine.heroes[0].id)
    assert all(a.target_id != enemy.position or not enemy.is_corpse for a in actions)


def test_a_crit_kill_still_leaves_no_corpse(store):
    engine = _engine(store, ["crusader"], ["cultist_brawler_A", "cultist_brawler_A"])
    enemy = engine.enemies[0]
    enemy.hp = 0
    engine._handle_deaths_door(enemy, engine.heroes[0], is_crit=True)
    assert enemy.is_dead and not enemy.is_corpse


def test_a_corpse_survives_four_round_ends_not_three(store):
    """The wiki's own worked example, which is what settles the off-by-one:
    *"a creature killed in round 1 will have its corpse on the field until the
    end of round 4, when it will disappear and leave an unoccupied slot for
    round 5."*

    The game file gives the NUMBER (`life_time: .alive_round_limit 3`) but not
    the comparison, and the two readings differ by a whole round. The round the
    corpse appears in does not count against the limit, so it is fixed by
    skipping that round rather than by changing the constant to 4 -- 3 is real
    data and should stay readable as such.

    Not cosmetic: a corpse blocks the rank it holds, so an extra round of
    blocking is an extra round the enemy line does not collapse forward.
    """
    engine = _engine(store, ["crusader"], ["cultist_brawler_A", "cultist_brawler_A"])
    engine.round = 1
    enemy = engine.enemies[0]
    enemy.hp = 0
    engine._handle_deaths_door(enemy, engine.heroes[0], is_crit=False)
    assert enemy.is_corpse, "premise: this monster leaves a corpse"
    assert enemy.corpse_spawn_round == 1

    # End of the round it appeared in: that round does not count.
    engine._tick_corpse_durations()
    assert enemy.is_corpse, "the spawn round must not consume a tick"

    for rnd in (2, 3):
        engine.round = rnd
        engine._tick_corpse_durations()
        assert enemy.is_corpse, f"rotted early, at the end of round {rnd}"

    engine.round = 4
    engine._tick_corpse_durations()
    assert not enemy.is_corpse, "should be gone entering round 5"
    assert enemy.corpse_spawn_round is None
