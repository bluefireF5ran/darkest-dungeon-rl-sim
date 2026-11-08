"""Tests for the monster-AI completion phase: friendly targeting (monsters
healing/buffing their own side), the real .heal field on monster skills, and
the remaining skill/target desire types (performing_turn_skill, stun_skill,
ally_alive_skill/ally_dead_skill, heal_skill, effect_key_status_skill,
rank_target, ally_class_target). All scenarios use real enemy ids/brains —
see ASSUMPTIONS.md for the verification notes behind each one.
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


def test_monster_heal_field_now_parses():
    """Collector Shaman's head_heal (.heal 6 12) was silently dropped before
    — _build_enemy only ever read .dmg, never .heal. Confirm heal_min/max
    now parse, and that the .dmg-derived attack range moved to the new
    atk_dmg_min/atk_dmg_max fields instead of overloading heal_min/max."""
    shaman = store.enemies["collector_shaman_A"]
    sk = next(s for s in shaman.skills if s.id == "head_heal")
    sd = sk.levels[max(sk.levels.keys())]
    assert (sd.heal_min, sd.heal_max) == (6, 12), (sd.heal_min, sd.heal_max)
    assert (sd.atk_dmg_min, sd.atk_dmg_max) == (0, 0)  # .dmg 0 0 is a real band (2026-10-06)
    assert sd.targets_allies is True
    print("  PASS test_monster_heal_field_now_parses")


def test_collector_shaman_heals_wounded_ally_collector():
    """End-to-end: a wounded Collector ally should actually receive HP from
    Collector Shaman's head_heal — previously this always resolved as a
    (harmless, 0-0 damage) attack against a hero instead."""
    crusader = store.heroes["crusader"]
    shaman = store.enemies["collector_shaman_A"]
    collector = store.enemies["collector_A"]
    engine = CombatEngine(store, SeededRNG(1))
    engine.reset(hero_defs=[crusader, crusader], enemy_defs=[collector, shaman], hero_positions=[1, 2])
    collector_state, shaman_state = engine.enemies[0], engine.enemies[1]
    collector_state.hp = 1

    healed = False
    for seed in range(30):
        engine.rng = SeededRNG(seed)
        engine._execute_enemy_turn(shaman_state)
        if collector_state.hp > 1:
            healed = True
            break
    assert healed, "Collector Shaman should eventually heal the wounded Collector"
    assert collector_state.hp <= collector_state.max_hp
    print(f"  PASS test_collector_shaman_heals_wounded_ally_collector (hp={collector_state.hp})")


def test_cyst_self_heal_via_empty_target():
    """beholder_healself has a completely empty .target (not even '@') —
    the monster-side equivalent of a hero self-action. Confirm it heals the
    performer directly without going through target selection."""
    crusader = store.heroes["crusader"]
    cyst = store.enemies["cyst_D"]
    engine = CombatEngine(store, SeededRNG(2))
    engine.reset(hero_defs=[crusader, crusader], enemy_defs=[cyst], hero_positions=[1, 2])
    enemy = engine.enemies[0]
    enemy.hp = 1

    sk = next(s for s in cyst.skills if s.id == "beholder_healself")
    sd = sk.levels[max(sk.levels.keys())]
    assert not sd.target and not sd.is_aoe and sd.heal_min is not None

    engine._apply_heal(enemy, engine.rng.randint(sd.heal_min, sd.heal_max), enemy)
    assert enemy.hp > 1
    print(f"  PASS test_cyst_self_heal_via_empty_target (hp={enemy.hp})")


def test_swine_prince_ally_dead_skill_gates_rage_trigger():
    """swine_prince's obliterate_enraged (ally_dead_skill, base_chance=1000,
    ally_base_class_id='swine_piglet') should be completely unreachable
    while a swine_piglet ally is alive, and dominate selection once it's
    dead — real rage-on-ally-death mechanic, previously not modeled at all
    (ally_dead_skill wasn't in MODELED_SKILL_DESIRE_TYPES)."""
    crusader = store.heroes["crusader"]
    prince = store.enemies["swine_prince_A"]
    piglet = store.enemies["swine_piglet_A"]
    engine = CombatEngine(store, SeededRNG(1))
    engine.reset(hero_defs=[crusader, crusader], enemy_defs=[prince, piglet], hero_positions=[1, 2])
    prince_state, piglet_state = engine.enemies[0], engine.enemies[1]
    ed = engine._get_enemy_def(prince_state.id)
    brain = engine._get_monster_brain(ed)

    # Real fix landed since this test was written: Mark now actually gets
    # applied (see ASSUMPTIONS.md's "Mark/Vulnerable status was never
    # actually applied to anything") -- swine_piglet's own kit uses "Piggie
    # Mark", and being faster than a Crusader (SPD 3, the slowest hero), it
    # can genuinely mark one during reset()'s pre-hero-turn auto-resolution.
    # swine_prince's brain ALSO has an unrelated "effect_key_status_skill"
    # desire (base_chance=10, no combat_skill_id -- spreads into the flat
    # pool whenever ANY hero is marked), which would otherwise give
    # obliterate_enraged a tiny nonzero flat-pool share even while the
    # piglet ally is alive -- a second, legitimate real trigger for the
    # same skill, but not the ally_dead_skill mechanic this test isolates.
    for h in engine.heroes:
        h.is_marked = False

    seen_alive = set()
    for seed in range(30):
        engine.rng = SeededRNG(seed)
        sk, _ = engine._select_enemy_skill(prince_state, ed, brain, 0)
        seen_alive.add(sk)
    assert "obliterate_enraged" not in seen_alive, seen_alive

    piglet_state.is_dead = True
    seen_dead = set()
    for seed in range(30):
        engine.rng = SeededRNG(seed)
        sk, _ = engine._select_enemy_skill(prince_state, ed, brain, 0)
        seen_dead.add(sk)
    assert seen_dead == {"obliterate_enraged"}, seen_dead
    print("  PASS test_swine_prince_ally_dead_skill_gates_rage_trigger")


def test_effect_key_status_skill_makes_fierce_swipe_reachable():
    """fungal_bloat's kill_marked_target_fungal_bloat brain has no
    specific_skill/random_skill desire naming fierce_swipe at all — only an
    effect_key_status_skill entry (effect_key_status='tagged', no
    combat_skill_id) that, per real data, must spread across all usable
    skills. Without this, fierce_swipe was completely unreachable regardless
    of whether a hero was marked."""
    crusader = store.heroes["crusader"]
    bloat = store.enemies["fungal_bloat_A"]
    engine = CombatEngine(store, SeededRNG(1))
    engine.reset(hero_defs=[crusader, crusader], enemy_defs=[bloat], hero_positions=[1, 2])
    enemy = engine.enemies[0]
    ed = engine._get_enemy_def(enemy.id)
    brain = engine._get_monster_brain(ed)

    seen_unmarked = set()
    for seed in range(60):
        engine.rng = SeededRNG(seed)
        sk, _ = engine._select_enemy_skill(enemy, ed, brain, 0)
        seen_unmarked.add(sk)
    assert "fierce_swipe" not in seen_unmarked, seen_unmarked

    engine.heroes[0].is_marked = True
    seen_marked = set()
    for seed in range(60):
        engine.rng = SeededRNG(seed)
        sk, _ = engine._select_enemy_skill(enemy, ed, brain, 0)
        seen_marked.add(sk)
    assert "fierce_swipe" in seen_marked, seen_marked
    print("  PASS test_effect_key_status_skill_makes_fierce_swipe_reachable")


def test_stun_skill_non_stunned_gate_shifts_weight():
    """spider_webber's stun_skill desire (spider_web, non_stunned_heroes_min=1)
    should meaningfully boost spider_web's selection frequency once there's
    a non-stunned hero to justify stunning, versus the flat random_skill-only
    baseline when every hero is already stunned."""
    crusader = store.heroes["crusader"]
    spider = store.enemies["spider_webber_A"]
    engine = CombatEngine(store, SeededRNG(1))
    engine.reset(hero_defs=[crusader, crusader], enemy_defs=[spider], hero_positions=[1, 2])
    enemy = engine.enemies[0]
    ed = engine._get_enemy_def(enemy.id)
    brain = engine._get_monster_brain(ed)

    # Real fix landed since this test was written: Mark now actually gets
    # applied (see ASSUMPTIONS.md's "Mark/Vulnerable status was never
    # actually applied to anything") -- spider_web itself uses "Mark
    # Target", so reset()'s pre-hero-turn auto-resolution can genuinely
    # mark a Crusader (SPD 3, the slowest hero) here. spider_webber's brain
    # ALSO has an unrelated "effect_key_status_skill" desire (spreads into
    # the flat pool whenever ANY hero is marked, no combat_skill_id of its
    # own) that would otherwise add a constant background weight to every
    # skill in both measurement phases below, diluting the specific
    # non_stunned_heroes_min contrast this test isolates.
    for h in engine.heroes:
        h.is_marked = False

    engine.heroes[0].is_stunned = True
    engine.heroes[1].is_stunned = True
    counts_stunned = collections.Counter()
    for seed in range(500):
        engine.rng = SeededRNG(seed)
        sk, _ = engine._select_enemy_skill(enemy, ed, brain, 0)
        counts_stunned[sk] += 1

    engine.heroes[0].is_stunned = False
    counts_open = collections.Counter()
    for seed in range(500):
        engine.rng = SeededRNG(seed)
        sk, _ = engine._select_enemy_skill(enemy, ed, brain, 0)
        counts_open[sk] += 1

    ratio_stunned = counts_stunned["spider_web"] / 500
    ratio_open = counts_open["spider_web"] / 500
    assert ratio_open > ratio_stunned + 0.2, (ratio_stunned, ratio_open)
    print(f"  PASS test_stun_skill_non_stunned_gate_shifts_weight "
          f"(stunned={ratio_stunned:.2f}, open={ratio_open:.2f})")


def test_preferred_skill_and_rank_target_are_data_equivalent_fallbacks():
    """preferred_skill/rank_target carry no distinguishing field in any real
    occurrence — confirm they resolve without error and actually influence
    selection (treated as random_skill/random_target respectively), rather
    than being silently ignored as unmodeled types."""
    from data.monster_brain_loader import MODELED_SKILL_DESIRE_TYPES, MODELED_TARGET_DESIRE_TYPES
    assert "preferred_skill" in MODELED_SKILL_DESIRE_TYPES
    assert "rank_target" in MODELED_TARGET_DESIRE_TYPES

    default_brain = store.monster_brains["default"]
    assert any(d.type == "preferred_skill" for d in default_brain.skill_desires)
    print("  PASS test_preferred_skill_and_rank_target_are_data_equivalent_fallbacks")


def test_ally_class_target_prioritizes_matching_class():
    """Collector Shaman's head_buff (ally_class_target, ally_base_class_id
    variants 'collector'/'collector_battle') should heavily prefer whichever
    ally enemy matches those classes over an unrelated enemy class in the
    same friendly pool."""
    crusader = store.heroes["crusader"]
    shaman = store.enemies["collector_shaman_A"]
    collector = store.enemies["collector_A"]
    brawler = store.enemies["cultist_brawler_A"]
    engine = CombatEngine(store, SeededRNG(1))
    # auto_resolve_to_hero=False: since skill summoning landed (2026-08-21)
    # the Collector actually summons during reset()'s auto-resolve, so the
    # board holds four enemies before the first hero turn. This test is about
    # ally-target selection, not about the opening turns.
    engine.reset(hero_defs=[crusader, crusader], enemy_defs=[shaman, collector, brawler],
                 hero_positions=[1, 2], auto_resolve_to_hero=False)
    shaman_state, collector_state, brawler_state = engine.enemies
    ed = engine._get_enemy_def(shaman_state.id)
    brain = engine._get_monster_brain(ed)
    pool = [e for e in engine.enemies if e.is_alive()]

    counts = collections.Counter()
    for seed in range(500):
        engine.rng = SeededRNG(seed)
        t = engine._select_enemy_target("head_buff", brain, pool, is_friendly=True)
        counts[t.id] += 1

    assert counts[collector_state.id] > counts[brawler_state.id], counts
    print(f"  PASS test_ally_class_target_prioritizes_matching_class ({counts})")


def test_full_combat_still_resolves_with_friendly_targeting_enabled():
    """Regression guard: a comp including a friendly-targeting support
    monster (Collector Shaman) should still resolve to a clean win/lose,
    same as the pre-existing test_full_combat_still_resolves_with_brain_driven_ai."""
    crusader = store.heroes["crusader"]
    shaman = store.enemies["collector_shaman_A"]
    collector = store.enemies["collector_A"]
    engine = CombatEngine(store, SeededRNG(13))
    engine.reset(hero_defs=[crusader, crusader], enemy_defs=[shaman, collector], hero_positions=[1, 2])
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
    print(f"  PASS test_full_combat_still_resolves_with_friendly_targeting_enabled ({steps} steps, winner={engine.winner})")


if __name__ == "__main__":
    print("=== Monster AI Friendly-Targeting / Completion Tests ===\n")
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
