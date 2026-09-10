"""Tests for true multi-target AoE resolution.

Found while fixing the documented "enemy is_aoe hardcoded False" gap: the
real bug was much bigger than the doc entry described — `is_aoe` was never
consulted anywhere in the live execution path (engine/combat_engine.py) for
EITHER heroes or enemies, only in vestigial free functions in engine/action.py
used solely by tests/engine/test_position.py. get_valid_actions generated one
single-target Action per candidate position regardless of is_aoe, and
_execute_action/_execute_enemy_turn only ever resolved against one target.
So no AoE skill, hero or monster, ever hit more than one target before this.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests._fixtures import get_store

from engine import SeededRNG, CombatEngine, Action


from data.paths import GAME_DIR
from data.paths import CSV_DIR
store = get_store()


def test_enemy_is_aoe_detected_from_real_data():
    """Regression test for the hardcoded is_aoe=False in hero_loader._build_enemy."""
    ed = store.enemies["ancestor_big_D"]
    sk = next(s for s in ed.skills if s.id == "ancestor_fuse_all")
    lvl = max(sk.levels.keys())
    assert sk.levels[lvl].is_aoe is True
    print("  PASS test_enemy_is_aoe_detected_from_real_data")


def test_hero_aoe_skill_hits_all_enemies_in_range():
    """Man-at-Arms' Bellow (real AoE skill, target ~1234 = all 4 ranks)
    should hit every living enemy in one action, not just one."""
    maa = store.heroes["man_at_arms"]
    brawler = store.enemies["cultist_brawler_A"]
    engine = CombatEngine(store, SeededRNG(3))
    engine.reset(hero_defs=[maa], enemy_defs=[brawler, brawler, brawler], hero_positions=[1])

    hp_before = [e.hp for e in engine.enemies]
    engine.submit_action(Action(engine.heroes[0].id, "bellow", 0))
    engine.step()
    hp_after = [e.hp for e in engine.enemies]

    changed = sum(1 for b, a in zip(hp_before, hp_after) if b != a)
    assert changed == 3, f"expected all 3 enemies affected, got {changed} ({hp_before} -> {hp_after})"
    print(f"  PASS test_hero_aoe_skill_hits_all_enemies_in_range ({hp_before} -> {hp_after})")


def test_enemy_aoe_skill_hits_all_heroes_in_range():
    """Forces ancestor_fuse_all directly via _execute_enemy_skill rather than
    relying on the brain's weighted-random selection landing on it for one
    specific seed — that coupling made this test fragile to any upstream
    change shifting the RNG draw sequence (e.g. a parser fix changing a
    monster's SPD/ATK value shifts turn-order/skill-selection rolls even
    though the AoE mechanic itself is untouched). Testing the mechanic in
    isolation (does a forced is_aoe skill hit everyone in range) is both
    more robust and more precisely what this test is actually about."""
    crusader = store.heroes["crusader"]
    enemy_def = store.enemies["ancestor_big_D"]
    engine = CombatEngine(store, SeededRNG(5))
    engine.reset(hero_defs=[crusader, crusader, crusader, crusader], enemy_defs=[enemy_def])

    enemy = engine.enemies[0]
    sk = next(s for s in enemy_def.skills if s.id == "ancestor_fuse_all")
    sd = sk.levels[max(sk.levels.keys())]
    brain = engine._get_monster_brain(enemy_def)

    hp_before = [h.hp for h in engine.heroes]
    engine._execute_enemy_skill(enemy, "ancestor_fuse_all", sd, brain)
    hp_after = [h.hp for h in engine.heroes]

    changed = sum(1 for b, a in zip(hp_before, hp_after) if b != a)
    assert changed == 4, f"expected all 4 heroes affected, got {changed} ({hp_before} -> {hp_after})"
    print(f"  PASS test_enemy_aoe_skill_hits_all_heroes_in_range ({hp_before} -> {hp_after})")


def test_get_valid_actions_generates_one_action_per_aoe_skill():
    """Before this fix, get_valid_actions generated one Action per candidate
    position for AoE skills too (e.g. 3 separate 'bellow' actions targeting
    3 different enemies) even though they'd all resolve identically (hit
    everyone) — wasteful and confusing action space. Should be exactly one
    action per AoE skill now.
    """
    maa = store.heroes["man_at_arms"]
    brawler = store.enemies["cultist_brawler_A"]
    engine = CombatEngine(store, SeededRNG(1))
    engine.reset(hero_defs=[maa], enemy_defs=[brawler, brawler, brawler], hero_positions=[1])

    acts = engine.get_valid_actions(engine.heroes[0].id)
    bellow_actions = [a for a in acts if a.skill_id == "bellow"]
    assert len(bellow_actions) == 1, f"expected exactly 1 'bellow' action, got {len(bellow_actions)}"
    print("  PASS test_get_valid_actions_generates_one_action_per_aoe_skill")


def test_aoe_heal_affects_every_target_in_range():
    """AoE heals (e.g. a party-wide heal) should heal everyone in range,
    each with their own independent roll, not just one target."""
    vestal = store.heroes["vestal"]
    sk = next(s for s in vestal.skills if s.id == "gods_comfort")
    lv4 = sk.levels[4]
    assert lv4.is_aoe is True, "gods_comfort should be AoE (target ~1234)"

    crusader = store.heroes["crusader"]
    engine = CombatEngine(store, SeededRNG(2))
    engine.reset(hero_defs=[vestal, crusader, crusader], enemy_defs=[store.enemies["cultist_brawler_A"]])
    for h in engine.heroes:
        h.hp = 1

    engine.submit_action(Action(engine.heroes[0].id, "gods_comfort", 0))
    # Bypass turn-order pausing/enemy auto-resolve by calling _execute_action directly
    engine._execute_action(engine._action_buffer)

    healed = sum(1 for h in engine.heroes if h.hp > 1)
    assert healed == len(engine.heroes), f"expected all {len(engine.heroes)} heroes healed, got {healed}"
    print(f"  PASS test_aoe_heal_affects_every_target_in_range (healed {healed}/{len(engine.heroes)})")


def test_full_combat_still_resolves_with_aoe_skills_available():
    maa = store.heroes["man_at_arms"]
    brawler = store.enemies["cultist_brawler_A"]
    engine = CombatEngine(store, SeededRNG(9))
    engine.reset(hero_defs=[maa, maa], enemy_defs=[brawler, brawler])
    steps = 0
    while not engine.done and steps < 300:
        steps += 1
        actor = engine.get_current_actor_id()
        if actor is not None:
            acts = engine.get_valid_actions(actor)
            if acts:
                engine.submit_action(acts[0])
        engine.step()
    assert engine.done, f"combat did not finish in {steps} steps"
    print(f"  PASS test_full_combat_still_resolves_with_aoe_skills_available ({steps} steps, winner={engine.winner})")


if __name__ == "__main__":
    print("=== AoE Multi-Target Tests ===\n")
    for name, func in sorted(globals().items()):
        if name.startswith("test_"):
            try:
                func()
                print()
            except Exception as e:
                import traceback
                print(f"  FAIL {name}: {e}")
                traceback.print_exc()
                print()
    print("Done.")
