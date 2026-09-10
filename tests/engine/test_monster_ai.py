"""Tests for monster AI (skill/target selection driven by
raid/ai/base.monster_brains.json instead of pure randomness).

Real data used throughout — cultist_brawler_A's brain: two specific_skill
desires (brawler_claw, brawler_claw_weak, both weight 1.0) and two
target_selection_desires (random_target weight 1.0, marked_target weight
2.0 gated to brawler_claw specifically).
"""

import sys
import collections
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests._fixtures import get_store

from engine import SeededRNG, CombatEngine


from data.paths import GAME_DIR
from data.paths import CSV_DIR
store = get_store()


def _fresh_engine(seed=1):
    engine = CombatEngine(store, SeededRNG(seed))
    crusader = store.heroes["crusader"]
    brawler = store.enemies["cultist_brawler_A"]
    engine.reset(hero_defs=[crusader, crusader], enemy_defs=[brawler], hero_positions=[1, 2])
    return engine


def test_monster_brains_resolve_for_all_enemies():
    unresolved = [e.id for e in store.enemies.values()
                  if e.monster_brain_id not in store.monster_brains]
    assert not unresolved, f"enemies with no matching brain: {unresolved}"
    print(f"  PASS test_monster_brains_resolve_for_all_enemies ({len(store.enemies)} enemies)")


def test_cultist_brawler_uses_both_skills_not_just_first():
    """Regression test: _execute_enemy_turn used to always use ed.skills[0]
    only — cultist_brawler_A's second skill (brawler_claw_weak) was never
    reachable at all."""
    engine = _fresh_engine()
    ed = engine._get_enemy_def(engine.enemies[0].id)
    brain = engine._get_monster_brain(ed)
    seen = set()
    for seed in range(100):
        engine.rng = SeededRNG(seed)
        skill_id, _ = engine._select_enemy_skill(engine.enemies[0], ed, brain)
        seen.add(skill_id)
    assert seen == {"brawler_claw", "brawler_claw_weak"}, seen
    print(f"  PASS test_cultist_brawler_uses_both_skills_not_just_first ({seen})")


def test_marked_target_is_preferred_for_gated_skill():
    """marked_target desire (weight 2.0, gated to brawler_claw specifically)
    should make the marked hero targeted roughly 3x more than unmarked
    (random_target weight 1.0 + marked_target weight 2.0 = 3 vs 1)."""
    engine = _fresh_engine()
    ed = engine._get_enemy_def(engine.enemies[0].id)
    brain = engine._get_monster_brain(ed)
    engine.heroes[1].is_marked = True

    counts = collections.Counter()
    for seed in range(1000):
        engine.rng = SeededRNG(seed)
        t = engine._select_enemy_target("brawler_claw", brain, engine.heroes)
        counts[t.id] += 1

    ratio = counts[engine.heroes[1].id] / max(counts[engine.heroes[0].id], 1)
    assert 2.0 < ratio < 4.0, f"expected marked/unmarked ratio near 3.0, got {ratio:.2f} ({counts})"
    print(f"  PASS test_marked_target_is_preferred_for_gated_skill (ratio={ratio:.2f})")


def test_marked_target_desire_does_not_apply_to_unrelated_skill():
    """marked_target's specific_combat_skill_id gates it to brawler_claw —
    for brawler_claw_weak (no such gate), it should NOT apply, and target
    selection should fall back to a roughly even split (random_target only).
    """
    engine = _fresh_engine()
    ed = engine._get_enemy_def(engine.enemies[0].id)
    brain = engine._get_monster_brain(ed)
    engine.heroes[1].is_marked = True

    counts = collections.Counter()
    for seed in range(1000):
        engine.rng = SeededRNG(seed)
        t = engine._select_enemy_target("brawler_claw_weak", brain, engine.heroes)
        counts[t.id] += 1

    ratio = counts[engine.heroes[1].id] / max(counts[engine.heroes[0].id], 1)
    assert 0.7 < ratio < 1.4, f"expected roughly even split, got ratio={ratio:.2f} ({counts})"
    print(f"  PASS test_marked_target_desire_does_not_apply_to_unrelated_skill (ratio={ratio:.2f})")


def test_skill_limits_still_respected_by_brain_selection():
    """_select_enemy_skill filters through _check_skill_limits first —
    confirm a skill at its per-battle limit is never selected."""
    engine = _fresh_engine()
    ed = engine._get_enemy_def(engine.enemies[0].id)
    brain = engine._get_monster_brain(ed)
    enemy = engine.enemies[0]
    # Fabricate a per-battle limit and pretend it's exhausted.
    sk = next(s for s in ed.skills if s.id == "brawler_claw")
    sd = sk.levels[max(sk.levels.keys())]
    original_limit = sd.per_battle_limit
    sd.per_battle_limit = 1
    enemy.skill_uses["brawler_claw"] = 1
    try:
        for seed in range(50):
            engine.rng = SeededRNG(seed)
            skill_id, _ = engine._select_enemy_skill(enemy, ed, brain)
            assert skill_id != "brawler_claw", "exhausted skill should never be selected"
    finally:
        sd.per_battle_limit = original_limit
    print("  PASS test_skill_limits_still_respected_by_brain_selection")


def test_monster_skill_effects_now_apply():
    """Regression test: _execute_enemy_turn never called _apply_skill_effects
    before — brawler_claw's Minor Bleed 1 effect was silently never applied,
    only raw damage. Force a guaranteed hit+bleed scenario and confirm the
    target actually gets a bleed DoT queued.
    """
    engine = _fresh_engine(seed=3)
    enemy = engine.enemies[0]
    target = engine.heroes[0]
    target.bleed_resist = 0.0  # guarantee the resist roll doesn't block it

    sk = next(s for s in store.enemies["cultist_brawler_A"].skills if s.id == "brawler_claw")
    sd = sk.levels[max(sk.levels.keys())]
    assert "Minor Bleed 1" in sd.effects

    engine._apply_skill_effects(target, enemy, sd, hit=True)
    assert len(target.bleeds) > 0, "brawler_claw's Minor Bleed 1 should apply a bleed DoT"
    print(f"  PASS test_monster_skill_effects_now_apply (bleeds={target.bleeds})")


def test_full_combat_still_resolves_with_brain_driven_ai():
    engine = _fresh_engine(seed=11)
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
    assert engine.winner in ("heroes", "enemies")
    print(f"  PASS test_full_combat_still_resolves_with_brain_driven_ai ({steps} steps, winner={engine.winner})")


def test_monster_skill_cooldown_enforced_and_ticks_down():
    """unclean_giant_B's brain (giant_B) declares confusion_spores/
    poison_spores on a 2-round cooldown — a skill just used should be
    excluded from selection until its cooldown expires."""
    giant = next(ed for ed in store.enemies.values() if ed.monster_brain_id == "giant_B")
    brain = store.monster_brains["giant_B"]
    assert brain.skill_cooldowns == {"confusion_spores": 2, "poison_spores": 2}

    crusader = store.heroes["crusader"]
    engine = CombatEngine(store, SeededRNG(1))
    engine.reset(hero_defs=[crusader, crusader, crusader, crusader], enemy_defs=[giant])
    enemy = engine.enemies[0]

    engine._execute_enemy_turn(enemy)
    used = [sk_id for sk_id in ("confusion_spores", "poison_spores") if sk_id in enemy.skill_cooldowns]
    assert used, "expected one of the cooldown skills to have been used"
    used_skill = used[0]
    assert enemy.skill_cooldowns[used_skill] == 2

    # While on cooldown, _select_enemy_skill must never re-pick it.
    for _ in range(2):
        skill_id, _ = engine._select_enemy_skill(enemy, engine._get_enemy_def(enemy.id), brain)
        assert skill_id != used_skill, f"{used_skill} should be on cooldown"
        engine._tick_enemy_skill_cooldowns()

    assert used_skill not in enemy.skill_cooldowns, "cooldown should have expired after 2 ticks"
    print(f"  PASS test_monster_skill_cooldown_enforced_and_ticks_down ({used_skill})")


if __name__ == "__main__":
    print("=== Monster AI Tests ===\n")
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
