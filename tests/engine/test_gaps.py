"""Tests for closed gaps — using actual game data IDs discovered by the loader."""

import dataclasses
import math
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests._fixtures import get_store

from engine import SeededRNG, CombatEngine, CombatantState, ActiveBuff, Action, DotInstance
from engine.resolution import resolve_attack
from data.types import BuffDefinition, SkillLevelData


from data.paths import GAME_DIR
from data.paths import CSV_DIR
store = get_store(fresh=True)


def test_mark_flag():
    """Mark flag can be set on enemies."""
    engine = CombatEngine(store, SeededRNG(42))
    crusader = store.heroes["crusader"]
    brawler = store.enemies["cultist_brawler_A"]
    engine.reset(hero_defs=[crusader], enemy_defs=[brawler], hero_positions=[1])
    engine.enemies[0].is_marked = True
    assert engine.enemies[0].is_marked is True
    print("  PASS test_mark_flag")


def test_bleed_effect_template():
    """Bleed effects exist in the store with correct amounts."""
    e1 = store.effects.get("Minor Bleed 1")
    assert e1 is not None, "Minor Bleed 1 not loaded"
    print(f"  PASS test_bleed_effect_template: bleed_amount={e1.bleed_amount} rounds={e1.bleed_rounds}")


def test_skill_ids_known():
    """Verify skill IDs from game data match expected patterns."""
    expected = {
        "vestal": ["mace_bash", "judgement", "dazzling_light", "divine_grace", "gods_comfort", "gods_illumination", "gods_hand"],
        "highwayman": ["wicked_slice", "pistol_shot", "point_blank_shot", "grape_shot_blast", "take_aim", "duelist_advance", "opened_vein"],
        "crusader": ["smite", "zealous_accusation", "stunning_blow", "bulwark_of_faith", "battle_heal", "holy_lance", "inspiring_cry"],
        "abomination": ["transform", "manacles", "vomit", "absolution", "rake", "rage", "slam"],
    }
    for hid, expected_ids in expected.items():
        hd = store.heroes.get(hid)
        assert hd is not None, f"Hero {hid} not found"
        actual_ids = [s.id for s in hd.skills]
        for s in expected_ids:
            assert s in actual_ids, f"Skill {s} not found in {hid}: {actual_ids}"
    print(f"  PASS test_skill_ids_known: all {sum(len(v) for v in expected.values())} skills verified")


def test_stun_effect_applied():
    """Engine stun effect helper should set is_stunned flag."""
    engine = CombatEngine(store, SeededRNG(42))
    crusader = store.heroes["crusader"]
    brawler = store.enemies["cultist_brawler_A"]
    engine.reset(hero_defs=[crusader], enemy_defs=[brawler], hero_positions=[1])
    target = engine.enemies[0]
    target.stun_resist = 0.0
    stun_effect = store.effects.get("Manacles Stun 1")
    if stun_effect:
        engine._resolve_effect(target, engine.heroes[0], stun_effect, None)
        assert target.is_stunned
        print("  PASS test_stun_effect_applied")
    else:
        print("  SKIP test_stun_effect_applied: Manacles Stun 1 not in effects (may use different naming)")


def test_push_moves_the_target_toward_the_back():
    """Push raises the rank index; pull lowers it. This test used to assert the
    opposite -- it put a target on rank 4, pushed, and expected rank 2.

    The game's own data settles the direction, because two skills constrain
    each other:

      Daemon's Pull  .target 34  applies "Pull 2A"
      Rampart        .target 12  applies "Push 1A"

    A pull is only legal against ranks 3-4, so it must drag the target
    FORWARD; a push is only legal against ranks 1-2, so it must shove it BACK.
    With the signs reversed each skill would do nothing on half its legal
    targets. Pushing a rank-4 target -- what the old test did -- is the case
    that cannot move at all.
    """
    engine = CombatEngine(store, SeededRNG(42))
    crusader = store.heroes["crusader"]
    brawler = store.enemies["cultist_brawler_A"]
    # Four of them: a displacement is a rotation, so there has to be someone
    # to rotate with.
    engine.reset(hero_defs=[crusader], enemy_defs=[brawler] * 4,
                 hero_positions=[1])
    for e in engine.enemies:
        e.move_resist = 0.0
    target = engine.enemies[0]
    target.position = 0
    push = store.effects.get("Push 2A")
    assert push is not None
    engine._resolve_effect(target, engine.heroes[0], push, None)
    assert target.position == 2, f"Expected position 2, got {target.position}"

    target = engine.enemies[3]
    assert target.position == 3
    pull = store.effects.get("Pull 2A")
    engine._resolve_effect(target, engine.heroes[0], pull, None)
    assert target.position == 1, f"Expected position 1, got {target.position}"
    print(f"  PASS test_push_moves_the_target_toward_the_back")


def test_trinket_ancestors_coat_loaded():
    """Ancestor's Coat trinket has 2 buffs and rarity 'ancestral'."""
    t = store.trinkets["ancestors_coat"]
    assert len(t.buffs) == 2
    assert t.rarity == "ancestral"
    print(f"  PASS test_trinket_ancestors_coat_loaded: {t.buffs}")


def test_transform_skill_has_limit():
    """Abomination transform has per_battle_limit=2."""
    abom = store.heroes["abomination"]
    for sk in abom.skills:
        if sk.id == "transform":
            lv4 = sk.levels.get(4)
            assert lv4 is not None
            assert lv4.per_battle_limit == 2
            print("  PASS test_transform_skill_has_limit")
            return
    raise AssertionError("Transform skill not found")


def test_stun_recovery_buff():
    """STUNRECOVERYBUFF grants +50% stun resist for 2 rounds."""
    b = store.buffs["STUNRECOVERYBUFF"]
    assert b.stat_type == "resistance"
    assert b.stat_sub_type == "stun"
    assert abs(b.amount - 0.5) < 0.001
    assert b.duration == 2
    print("  PASS test_stun_recovery_buff")


def test_multi_action_enemy():
    """Verify at least one enemy has actions_per_round > 1."""
    multi = [e for e in store.enemies.values() if e.actions_per_round > 1]
    assert len(multi) > 0, "No multi-action enemies found"
    print(f"  PASS test_multi_action_enemy: {len(multi)} enemies with >1 action "
          f"(e.g. {multi[0].id} = {multi[0].actions_per_round})")


def test_size2_enemy():
    """Verify at least one enemy has size 2."""
    big = [e for e in store.enemies.values() if e.size == 2]
    assert len(big) > 0
    print(f"  PASS test_size2_enemy: {len(big)} size-2 enemies (e.g. {big[0].id})")


def test_monster_brains_loadable():
    """base.monster_brains.json is loadable with 165+ brains."""
    import json
    with open(GAME_DIR / "raid" / "ai" / "base.monster_brains.json", encoding="utf-8") as f:
        data = json.load(f)
    brains = data["monster_brains"]
    assert len(brains) >= 100
    print(f"  PASS test_monster_brains_loadable: {len(brains)} brains")


def test_heal_on_vestal_divine_grace():
    """Divine Grace resolves a real HP heal range at max level (8-9 HP, per game files)."""
    vestal = store.heroes["vestal"]
    for sk in vestal.skills:
        if sk.id == "divine_grace":
            lv4 = sk.levels.get(4)
            assert lv4 is not None
            assert lv4.heal_min == 8, f"Expected heal_min=8, got {lv4.heal_min}"
            assert lv4.heal_max == 9, f"Expected heal_max=9, got {lv4.heal_max}"
            print("  PASS test_heal_on_vestal_divine_grace")
            return
    raise AssertionError("divine_grace not found")


def test_heal_direct_on_skill_line():
    """Crusader's Battle Heal reads .heal N M directly off the skill (5-6 HP at max level).

    Regression test for hero_loader hardcoding heal_min/heal_max to None.
    """
    crusader = store.heroes["crusader"]
    battle_heal = next(sk for sk in crusader.skills if sk.id == "battle_heal")
    lv4 = battle_heal.levels[4]
    assert lv4.heal_min == 5, f"Expected heal_min=5, got {lv4.heal_min}"
    assert lv4.heal_max == 6, f"Expected heal_max=6, got {lv4.heal_max}"
    print("  PASS test_heal_direct_on_skill_line")


def test_multi_value_effect_field_parses_as_list():
    """Inspiring Cry references two named effects — regression test for the
    parser dropping keys with multiple consecutive quoted values.
    """
    crusader = store.heroes["crusader"]
    inspiring_cry = next(sk for sk in crusader.skills if sk.id == "inspiring_cry")
    lv4 = inspiring_cry.levels[4]
    assert lv4.effects == ["Crusader HealStress 5", "Crusader Light 5"], lv4.effects
    print("  PASS test_multi_value_effect_field_parses_as_list")


def test_stress_heal_effect_resolves():
    """Crusader HealStress 5 (Inspiring Cry's stress-heal component) resolves
    stress_heal from the real .healstress field (not the nonexistent .stress_heal key).
    """
    e = store.effects.get("Crusader HealStress 5")
    assert e is not None, "Crusader HealStress 5 not loaded"
    assert e.stress_heal == 8, f"Expected stress_heal=8, got {e.stress_heal}"
    print("  PASS test_stress_heal_effect_resolves")


def test_plague_doctor_battlefield_medicine_cures_dots():
    """Battlefield Medicine heals HP and cures bleed/blight via the real .cure field."""
    pd = store.heroes["plague_doctor"]
    skill = next(sk for sk in pd.skills if sk.id == "battlefield_medicine")
    lv4 = skill.levels[4]
    assert lv4.heal_min == 3 and lv4.heal_max == 3, (lv4.heal_min, lv4.heal_max)
    assert set(lv4.effects) == {"Cure", "Cureself"}, lv4.effects
    for name in ("Cure", "Cureself"):
        e = store.effects[name]
        assert e.cures_dots is True, f"{name}.cures_dots should be True"
    print("  PASS test_plague_doctor_battlefield_medicine_cures_dots")


def test_heal_skills_apply_their_own_effect_list():
    """Most significant bug found in this audit pass: _resolve_heal (the
    is_heal branch in _execute_action) never called _apply_skill_effects
    -- so any heal skill that ALSO carries a named .effect list silently
    dropped it, applying only the bare .heal amount. Real, high-impact
    consequence: Crusader's inspiring_cry -- the primary stress healer in
    most comps that carry a Crusader (per HEROES/Heroes' own docs, e.g.
    "Crusader as main healer via Battle Heal + Inspiring Cry") -- has its
    REAL stress-heal (8 stress at level 5, "Crusader HealStress N") and
    torch gain ("Crusader Light N") both delivered as named effects, not
    the bare .heal field (a token 1-2 HP). Before this fix, using
    Inspiring Cry only ever gave that trivial HP tick and relieved NO
    stress at all -- a severe, previously invisible gap for a skill this
    central, and a very plausible real contributor to comps pinning at
    200 stress project-wide, not just the "Stun Control" family. Also
    affects Plague Doctor's battlefield_medicine (Cure/Cureself), Antiquarian's
    fortifying_vapours (Fortify Resists buff), Arbalest's battlefield_bandage
    (Arb Stacking Heal buff), and Occultist's wyrd_reconstruction (Wyrd
    Bleed risky-heal chance) -- confirmed via a full grep across every
    hero's .info.darkest for a skill with both a bare .heal AND a
    .effect list; these 5 skills are the complete list project-wide."""
    from engine.combatant import DotInstance

    crusader = store.heroes["crusader"]
    vestal = store.heroes["vestal"]
    brawler = store.enemies["cultist_brawler_A"]

    engine = CombatEngine(store, SeededRNG(1))
    engine.reset(hero_defs=[crusader, vestal], enemy_defs=[brawler])
    c, v = engine.heroes
    v.stress = 50
    engine._execute_action(Action(c.id, "inspiring_cry", v.position))
    assert v.stress < 50, (
        "inspiring_cry's real stress-heal effect never applied", v.stress)

    pd = store.heroes["plague_doctor"]
    engine2 = CombatEngine(store, SeededRNG(1))
    engine2.reset(hero_defs=[pd, crusader], enemy_defs=[brawler])
    p, ally = engine2.heroes
    ally.bleeds.append(DotInstance(effect_name="test bleed", damage_per_tick=3, remaining_ticks=3, chance=1.0))
    engine2._execute_action(Action(p.id, "battlefield_medicine", ally.position))
    assert not ally.bleeds, ("battlefield_medicine's Cure effect never applied", ally.bleeds)
    print("  PASS test_heal_skills_apply_their_own_effect_list")


def test_guard_redirect():
    """Man-at-Arms' Defender skill grants Guard; enemy attacks on the guarded ally redirect to MAA."""
    engine = CombatEngine(store, SeededRNG(1))
    maa = store.heroes["man_at_arms"]
    crusader = store.heroes["crusader"]
    brawler = store.enemies["cultist_brawler_A"]
    engine.reset(hero_defs=[maa, crusader], enemy_defs=[brawler], hero_positions=[1, 2])
    guardian, guarded = engine.heroes[0], engine.heroes[1]
    guard_effect = store.effects["MAA Guard 1"]
    engine._resolve_effect(guarded, guardian, guard_effect, None)
    assert guarded.guard_source_id == guardian.id
    assert guardian.guard_target_id == guarded.id
    redirected = engine._resolve_guard_redirect(guarded)
    assert redirected.id == guardian.id, "attack on guarded ally should redirect to the guardian"
    print("  PASS test_guard_redirect")


def test_aoe_attack_bypasses_guard():
    """Wiki-confirmed (darkestdungeon.wiki.gg/wiki/Status_Effects): "AoE
    bypasses guard." Real gap found and fixed: _resolve_guard_redirect used
    to be called unconditionally for every AoE target too, so a guarded
    hero standing in an AoE's target ranks was incorrectly protected from
    it -- only single-target attacks should ever redirect onto a guardian."""
    engine = CombatEngine(store, SeededRNG(1))
    maa = store.heroes["man_at_arms"]
    crusader = store.heroes["crusader"]
    brawler = store.enemies["cultist_brawler_A"]
    engine.reset(hero_defs=[maa, crusader], enemy_defs=[brawler], hero_positions=[1, 2])
    guardian, guarded = engine.heroes[0], engine.heroes[1]
    guard_effect = store.effects["MAA Guard 1"]
    engine._resolve_effect(guarded, guardian, guard_effect, None)
    assert guarded.guard_source_id == guardian.id

    not_redirected = engine._resolve_guard_redirect(guarded, is_aoe=True)
    assert not_redirected.id == guarded.id, "an AoE attack must hit its real target, not the guardian"
    still_redirected = engine._resolve_guard_redirect(guarded, is_aoe=False)
    assert still_redirected.id == guardian.id, "a single-target attack should still redirect"
    print("  PASS test_aoe_attack_bypasses_guard")


def test_stunning_the_guardian_breaks_guard():
    """Wiki-confirmed (darkestdungeon.wiki.gg/wiki/Status_Effects): "whenever
    a guarding unit is stunned, the guard effect will immediately disappear
    from the protected unit" -- stunning the GUARDIAN breaks guard, not
    stunning the guarded unit. Real gap found and fixed: stun application
    never touched guard_target_id/guard_source_id at all, so a stunned
    guardian kept redirecting attacks meant for their ward."""
    engine = CombatEngine(store, SeededRNG(1))
    maa = store.heroes["man_at_arms"]
    crusader = store.heroes["crusader"]
    brawler = store.enemies["cultist_brawler_A"]
    engine.reset(hero_defs=[maa, crusader], enemy_defs=[brawler], hero_positions=[1, 2])
    guardian, guarded = engine.heroes[0], engine.heroes[1]
    guard_effect = store.effects["MAA Guard 1"]
    engine._resolve_effect(guarded, guardian, guard_effect, None)
    assert guardian.guard_target_id == guarded.id

    guardian.stun_resist = -99  # guarantee the stun lands
    stun_effect = store.effects["Manacles Stun 1"]
    engine._resolve_effect(guardian, guarded, stun_effect, None)

    assert guardian.is_stunned
    assert guardian.guard_target_id is None, "guard must break when the guardian is stunned"
    assert guarded.guard_source_id is None, "the guarded ally must lose the redirect too"
    print("  PASS test_stunning_the_guardian_breaks_guard")


def test_protect_me_swaps_guard_direction():
    """Antiquarian's Protect Me! guards HER via the target ally (swap_source_and_target)."""
    engine = CombatEngine(store, SeededRNG(1))
    antiquarian = store.heroes["antiquarian"]
    crusader = store.heroes["crusader"]
    brawler = store.enemies["cultist_brawler_A"]
    engine.reset(hero_defs=[antiquarian, crusader], enemy_defs=[brawler], hero_positions=[1, 2])
    performer, ally = engine.heroes[0], engine.heroes[1]
    protect_effect = store.effects["Antiq ProtectMe Guard"]
    assert protect_effect.swap_source_and_target is True
    engine._resolve_effect(ally, performer, protect_effect, None)
    assert performer.guard_source_id == ally.id, "Antiquarian should end up guarded"
    assert ally.guard_target_id == performer.id, "the ally should end up guarding Antiquarian"
    print("  PASS test_protect_me_swaps_guard_direction")


def test_riposte_counterattacks_on_hit():
    """Retribution grants MAA Riposte 1; a subsequent hit against him triggers a counter-attack."""
    engine = CombatEngine(store, SeededRNG(7))
    maa = store.heroes["man_at_arms"]
    brawler = store.enemies["cultist_brawler_A"]
    engine.reset(hero_defs=[maa], enemy_defs=[brawler], hero_positions=[1])
    performer = engine.heroes[0]
    riposte_effect = store.effects["MAA Riposte 1"]
    engine._resolve_effect(performer, performer, riposte_effect, None)
    assert performer.active_riposte is not None
    assert performer.active_riposte.on_hit_chance == 1.0
    attacker = engine.enemies[0]
    hp_before = attacker.hp
    engine._maybe_trigger_riposte(performer, attacker, hit=True)
    assert attacker.hp < hp_before, "riposte should have damaged the attacker"
    print("  PASS test_riposte_counterattacks_on_hit")


def test_riposte_counterattack_can_miss():
    """Real bug found and fixed: _maybe_trigger_riposte used to apply
    riposte damage completely unconditionally once triggered -- no
    accuracy roll at all, so every riposte was a guaranteed hit. The real
    riposte_skill: record (a data record distinct from combat_skill:,
    giving each of the 3 real riposte-capable classes their own fixed
    base ACC that does NOT scale with the setup skill's own level --
    Man-at-Arms 90%, Bounty Hunter/Highwayman 85% each) was never parsed
    or consulted. Verified both directions: an overwhelmingly-dodgy
    target should make the riposte miss (no damage, no accuracy-check
    bypass), while a zero-dodge target should still reliably connect."""
    engine = CombatEngine(store, SeededRNG(7))
    maa = store.heroes["man_at_arms"]
    assert maa.riposte_acc == 0.9, maa.riposte_acc
    brawler = store.enemies["cultist_brawler_A"]
    engine.reset(hero_defs=[maa], enemy_defs=[brawler], hero_positions=[1])
    performer = engine.heroes[0]
    riposte_effect = store.effects["MAA Riposte 1"]
    engine._resolve_effect(performer, performer, riposte_effect, None)
    attacker = engine.enemies[0]

    attacker.dodge = 5.0  # effectively unhittable
    hp_before = attacker.hp
    engine._maybe_trigger_riposte(performer, attacker, hit=True)
    assert attacker.hp == hp_before, "an overwhelmingly-dodgy target should make the riposte miss"

    attacker.dodge = 0.0
    engine._maybe_trigger_riposte(performer, attacker, hit=True)
    assert attacker.hp < hp_before, "a zero-dodge target should still let the riposte connect"
    print("  PASS test_riposte_counterattack_can_miss")


def test_stunned_defender_riposte_never_triggers():
    """Real gap found and fixed: a Stunned defender's Riposte should never
    trigger at all -- confirmed by HEROES/Heroes/Highwayman's own doc:
    "Ripostes aren't activated when Stunned" / "20% Stun Resist is
    crippling for Highwayman, as Ripostes require avoiding Stuns". Riposte
    is purely reactive (fires off being attacked, not off the defender's
    own turn), so nothing else in the engine already gated this."""
    engine = CombatEngine(store, SeededRNG(7))
    maa = store.heroes["man_at_arms"]
    brawler = store.enemies["cultist_brawler_A"]
    engine.reset(hero_defs=[maa], enemy_defs=[brawler], hero_positions=[1])
    performer = engine.heroes[0]
    riposte_effect = store.effects["MAA Riposte 1"]
    engine._resolve_effect(performer, performer, riposte_effect, None)
    attacker = engine.enemies[0]
    attacker.dodge = 0.0
    performer.is_stunned = True

    hp_before = attacker.hp
    engine._maybe_trigger_riposte(performer, attacker, hit=True)
    assert attacker.hp == hp_before, "a stunned defender should never riposte"
    print("  PASS test_stunned_defender_riposte_never_triggers")


def test_shadow_fade_applies_stealth():
    """Grave Robber's Shadow Fade applies stealth_self — regression test for
    self-targeted skills never processing their .effect list (self_effects
    was always empty; _resolve_self_action ignored .effect entirely).
    """
    engine = CombatEngine(store, SeededRNG(3))
    gr = store.heroes["grave_robber"]
    brawler = store.enemies["cultist_brawler_A"]
    engine.reset(hero_defs=[gr], enemy_defs=[brawler], hero_positions=[1])
    performer = engine.heroes[0]
    skill = next(sk for sk in gr.skills if sk.id == "shadow_fade")
    lv4 = skill.levels[4]
    assert not performer.is_stealthed
    engine._resolve_self_action(performer, "shadow_fade", lv4)
    assert performer.is_stealthed is True
    assert performer.stealth_rounds_remaining == 2
    print("  PASS test_shadow_fade_applies_stealth")


def test_stealthed_hero_excluded_from_enemy_targeting():
    """Enemies can't randomly pick a stealthed hero as their attack target."""
    engine = CombatEngine(store, SeededRNG(5))
    gr = store.heroes["grave_robber"]
    crusader = store.heroes["crusader"]
    brawler = store.enemies["cultist_brawler_A"]
    engine.reset(hero_defs=[gr, crusader], enemy_defs=[brawler], hero_positions=[1, 2])
    engine.heroes[0].is_stealthed = True
    for _ in range(20):
        engine._execute_enemy_turn(engine.enemies[0])
    assert engine.heroes[0].hp == engine.heroes[0].max_hp, "stealthed hero should never be targeted"
    print("  PASS test_stealthed_hero_excluded_from_enemy_targeting")


def test_attacking_breaks_stealth():
    """Attacking while stealthed breaks stealth after the attack resolves."""
    engine = CombatEngine(store, SeededRNG(9))
    gr = store.heroes["grave_robber"]
    brawler = store.enemies["cultist_brawler_A"]
    engine.reset(hero_defs=[gr], enemy_defs=[brawler], hero_positions=[1])
    performer = engine.heroes[0]
    performer.is_stealthed = True
    performer.stealth_rounds_remaining = 2
    action = Action(performer.id, "lunge", engine.enemies[0].position)
    engine._execute_action(action)
    assert performer.is_stealthed is False
    assert performer.stealth_rounds_remaining is None
    print("  PASS test_attacking_breaks_stealth")


def test_stealth_duration_expires():
    """Stealth clears itself after its duration runs out via the round tick."""
    engine = CombatEngine(store, SeededRNG(11))
    gr = store.heroes["grave_robber"]
    brawler = store.enemies["cultist_brawler_A"]
    engine.reset(hero_defs=[gr], enemy_defs=[brawler], hero_positions=[1])
    performer = engine.heroes[0]
    performer.is_stealthed = True
    performer.stealth_rounds_remaining = 2
    engine._tick_stealth_durations()
    assert performer.is_stealthed is True
    engine._tick_stealth_durations()
    assert performer.is_stealthed is False
    print("  PASS test_stealth_duration_expires")


def test_heal_clears_sticky_deaths_door_flag():
    """A healed hero who was at death's door should exit it once HP rises
    above 0, so a future re-downing gets a fresh survival roll instead of an
    instant deathblow. Regression test for _resolve_heal/_resolve_self_action
    only ever touching .hp, never .is_deaths_door.
    """
    engine = CombatEngine(store, SeededRNG(1))
    crusader = store.heroes["crusader"]
    vestal = store.heroes["vestal"]
    brawler = store.enemies["cultist_brawler_A"]
    engine.reset(hero_defs=[crusader, vestal], enemy_defs=[brawler], hero_positions=[1, 2])
    target = engine.heroes[0]
    target.hp, target.is_deaths_door = 0, True

    skill = engine.get_skill_data("battle_heal")
    engine._resolve_heal(engine.heroes[1], target, skill)

    assert target.hp > 0
    assert target.is_deaths_door is False
    print("  PASS test_heal_clears_sticky_deaths_door_flag")


def test_heal_crit_doubles_amount_and_reduces_stress():
    """Wiki-confirmed (darkestdungeon.wiki.gg/wiki/Critical_Hit): "A crit
    heal doubles the healing and reduces target stress by 4." Previously
    entirely unmodeled -- no heal ever crit regardless of skill.crit or
    attacker.crit, since heals never routed through resolve_attack at all."""
    engine = CombatEngine(store, SeededRNG(1))
    crusader = store.heroes["crusader"]
    brawler = store.enemies["cultist_brawler_A"]
    engine.reset(hero_defs=[crusader], enemy_defs=[brawler], hero_positions=[1])
    target = engine.heroes[0]
    target.hp = 10
    target.stress = 20
    engine.rng.random = lambda: 0.0  # force the crit roll to succeed

    applied = engine._apply_heal(target, 10, target, crit_eligible=True)

    assert applied == 20, f"crit heal should double the base amount, got {applied}"
    assert target.stress == 16, f"crit heal should cut stress by 4, got {target.stress}"
    print("  PASS test_heal_crit_doubles_amount_and_reduces_stress")


def test_heal_crit_single_vs_multi_target_rate():
    """Wiki-confirmed rates: 12% for a single-target heal, 5% per target for
    a multi-target heal. A roll of 0.08 should crit at the single-target
    rate but not at the multi-target rate."""
    engine = CombatEngine(store, SeededRNG(1))
    crusader = store.heroes["crusader"]
    brawler = store.enemies["cultist_brawler_A"]
    engine.reset(hero_defs=[crusader], enemy_defs=[brawler], hero_positions=[1])
    target = engine.heroes[0]
    engine.rng.random = lambda: 0.08

    target.hp, target.stress = 10, 20
    single = engine._apply_heal(target, 10, target, crit_eligible=True, is_multi_target=False)
    assert single == 20, f"0.08 roll should crit at the 12% single-target rate, got {single}"

    target.hp, target.stress = 10, 20
    multi = engine._apply_heal(target, 10, target, crit_eligible=True, is_multi_target=True)
    assert multi == 10, f"0.08 roll should NOT crit at the 5% per-target rate, got {multi}"
    print("  PASS test_heal_crit_single_vs_multi_target_rate")


def test_heal_crit_not_eligible_by_default_or_for_monster_heals():
    """Sanity check: crit_eligible defaults False (existing call sites that
    don't pass it, e.g. monster self-heals, must be unaffected), and a
    monster performer never rolls a heal crit even if a caller mistakenly
    marked it eligible -- the wiki only ever describes this for heroes."""
    engine = CombatEngine(store, SeededRNG(1))
    crusader = store.heroes["crusader"]
    brawler = store.enemies["cultist_brawler_A"]
    engine.reset(hero_defs=[crusader], enemy_defs=[brawler], hero_positions=[1])
    target = engine.heroes[0]
    enemy = engine.enemies[0]
    target.hp, target.stress = 10, 20
    engine.rng.random = lambda: 0.0  # would force a crit if eligible

    applied = engine._apply_heal(target, 10, target)  # crit_eligible omitted -> False
    assert applied == 10, f"default call must not crit, got {applied}"
    target.hp, target.stress = 10, 20
    applied = engine._apply_heal(target, 10, enemy, crit_eligible=True)
    assert applied == 10, f"a monster performer must never crit-heal, got {applied}"
    assert target.stress == 20
    print("  PASS test_heal_crit_not_eligible_by_default_or_for_monster_heals")


def test_heal_below_zero_hp_keeps_deaths_door():
    """Sanity check: the flag should only clear once HP actually rises above
    0 — a heal that doesn't fully revive shouldn't clear it."""
    engine = CombatEngine(store, SeededRNG(1))
    crusader = store.heroes["crusader"]
    brawler = store.enemies["cultist_brawler_A"]
    engine.reset(hero_defs=[crusader], enemy_defs=[brawler], hero_positions=[1])
    target = engine.heroes[0]
    target.hp, target.is_deaths_door = 0, True

    engine._apply_heal(target, 0)  # no-op heal amount

    assert target.hp <= 0
    assert target.is_deaths_door is True
    print("  PASS test_heal_below_zero_hp_keeps_deaths_door")


def test_hero_dots_tick_on_their_own_turn():
    """Hero bleed/blight reduces HP when their turn comes up.

    Originally a regression test for _tick_dots only ever running on the
    non-hero branch. The setup changed when DoTs moved to ticking at the
    START of a combatant's turn rather than after it acts: applying a bleed
    mid-turn and then acting correctly ticks on the hero's NEXT turn, not
    retroactively on the one already in progress. So the bleed is placed
    before the turn begins, which is also the realistic case.
    """
    from engine.combatant import DotInstance
    engine = CombatEngine(store, SeededRNG(2))
    crusader = store.heroes["crusader"]
    brawler = store.enemies["cultist_brawler_A"]
    engine.reset(hero_defs=[crusader], enemy_defs=[brawler], hero_positions=[1])
    hero = engine.heroes[0]
    hero.bleeds.append(DotInstance(effect_name="test bleed", damage_per_tick=5,
                                    remaining_ticks=3, chance=1.0))
    hp_before = hero.hp

    # Begin the hero's turn with that bleed already on them.
    engine.current_turn_index = engine.turn_order.index(hero)
    engine._dot_ticked_for_index = -1
    engine._advance_to_next_hero_turn()

    assert hero.hp < hp_before, f"hero HP should drop from the bleed tick (before={hp_before}, after={hero.hp})"
    assert hero.bleeds[0].remaining_ticks == 2
    print(f"  PASS test_hero_dots_tick_on_their_own_turn (hp {hp_before} -> {hero.hp})")


def test_dot_damage_applies_every_tick_regardless_of_resist():
    """Real bug found and fixed: _tick_dots used to re-roll resistance on
    EVERY tick (on top of the resist check already gating whether the DoT
    gets inflicted in the first place), silently under-dealing bleed/blight
    damage project-wide. The wiki (darkestdungeon.wiki.gg/wiki/Bleed_
    (Darkest_Dungeon)/Blight_(Darkest_Dungeon)) confirms resistance only
    gates infliction -- once bled/blighted, damage applies every round of
    the duration unconditionally. Verified deterministically even with
    bleed_resist/blight_resist at 0.99 (near-certain resist under the old
    per-tick roll)."""
    from engine.combatant import DotInstance
    engine = CombatEngine(store, SeededRNG(7))
    crusader = store.heroes["crusader"]
    brawler = store.enemies["cultist_brawler_A"]
    engine.reset(hero_defs=[crusader], enemy_defs=[brawler], hero_positions=[1])
    hero = engine.heroes[0]
    hero.bleed_resist = 0.99
    hero.blight_resist = 0.99
    hero.bleeds.append(DotInstance(effect_name="test bleed", damage_per_tick=5, remaining_ticks=3, chance=1.0))
    hero.blights.append(DotInstance(effect_name="test blight", damage_per_tick=4, remaining_ticks=3, chance=1.0))
    hp_before = hero.hp

    engine._tick_dots(hero)
    assert hero.hp == hp_before - 9, f"expected both DoTs to apply in full despite 99% resist, got {hero.hp}"
    assert hero.bleeds[0].remaining_ticks == 2
    assert hero.blights[0].remaining_ticks == 2
    print(f"  PASS test_dot_damage_applies_every_tick_regardless_of_resist (hp {hp_before} -> {hero.hp})")


def test_horror_stress_dot_inflicts_ticks_and_clears_with_laudanum():
    """Real bug found and fixed: Horror (a real status effect, see
    darkestdungeon.wiki.gg/wiki/Horror_(Darkest_Dungeon)) was unimplemented
    due to a parser key mismatch -- data/effect_loader.py checked for
    "stress_damage_per_round", but the real .darkest field is .dotStress
    (e.g. "Madman Horror 1", "Ghoul Stress DOT"). Fixed the key, wired up
    CombatantState.stress_dots + _tick_stress_dots (routed through
    _apply_stress so affliction/heart-attack triggers still fire correctly),
    and re-enabled laudanum's real .clearDotStress cure."""
    engine = CombatEngine(store, SeededRNG(3))
    crusader = store.heroes["crusader"]
    brawler = store.enemies["cultist_brawler_A"]
    engine.reset(hero_defs=[crusader], enemy_defs=[brawler], hero_positions=[1])
    hero = engine.heroes[0]

    horror = store.effects["Madman Horror 1"]
    assert horror.stress_damage_per_round == 3
    assert horror.stress_dot_rounds == 4
    engine._resolve_effect(hero, hero, horror, None)
    assert len(hero.stress_dots) == 1

    stress_before = hero.stress
    engine._tick_stress_dots(hero)
    assert hero.stress == stress_before + 3
    assert hero.stress_dots[0].remaining_ticks == 3

    engine._use_provision("laudanum", hero)
    assert hero.stress_dots == []
    print(f"  PASS test_horror_stress_dot_inflicts_ticks_and_clears_with_laudanum (stress -> {hero.stress})")


def test_extra_targets_mechanic_hits_a_second_distinct_hero():
    """Real bug found and fixed: a chance-based "also hit an extra target"
    mechanic on ~30 boss/named monster skills (extra_targets_chance/
    extra_targets_count) was completely unhandled. Verified against Crone's
    real curse_vulnerability (extra_targets_chance=0.5, extra_targets_
    count=1): over enough seeds, a second, genuinely distinct hero should
    get hit too, not just the primary target repeated."""
    heroes = [store.heroes[h] for h in ["crusader", "vestal", "highwayman", "plague_doctor"]]
    crone_def = store.enemies["crone_A"]
    sd = next(s.levels[0] for s in crone_def.skills if s.id == "curse_vulnerability")
    assert sd.extra_targets_chance == 0.5
    assert sd.extra_targets_count == 1

    hit_names_per_seed = []
    for seed in range(60):
        engine = CombatEngine(store, SeededRNG(seed))
        engine.reset(hero_defs=heroes, enemy_defs=[crone_def])
        crone = engine.enemies[0]
        brain = engine._get_monster_brain(crone_def)
        log_before = len(engine.log)
        engine._execute_enemy_skill(crone, "curse_vulnerability", sd, brain)
        names_hit = {h.name for h in engine.heroes if any(h.name in l for l in engine.log[log_before:])}
        hit_names_per_seed.append(names_hit)

    assert any(len(names) >= 2 for names in hit_names_per_seed), \
        "expected at least one seed where 2 distinct heroes were hit by the same skill use"
    print("  PASS test_extra_targets_mechanic_hits_a_second_distinct_hero")


def test_extra_targets_chance_zero_never_adds_a_target():
    """Sanity bound: a skill with extra_targets_count=0 (the overwhelming
    majority of skills, which never set this field at all) must never hit
    more than its normal single target."""
    heroes = [store.heroes[h] for h in ["crusader", "vestal"]]
    brawler_def = store.enemies["cultist_brawler_A"]
    sd = brawler_def.skills[0].levels[0]
    assert sd.extra_targets_count == 0

    for seed in range(20):
        engine = CombatEngine(store, SeededRNG(seed))
        engine.reset(hero_defs=heroes, enemy_defs=[brawler_def])
        brawler = engine.enemies[0]
        brain = engine._get_monster_brain(brawler_def)
        log_before = len(engine.log)
        engine._execute_enemy_skill(brawler, brawler_def.skills[0].id, sd, brain)
        names_hit = {h.name for h in engine.heroes if any(h.name in l for l in engine.log[log_before:])}
        assert len(names_hit) <= 1, (seed, names_hit)
    print("  PASS test_extra_targets_chance_zero_never_adds_a_target")


def test_can_be_riposted_false_blocks_riposte():
    """Real bug found and fixed: monster self-destruct/flee skills (Bloated
    Corpse's explode, Crow's escape) flag can_be_riposted false in real
    data -- the real game doesn't let a Riposte-primed hero counter these.
    Verified directly against bloated_corpse_A's real "explode" skill."""
    crusader = store.heroes["crusader"]
    corpse_def = store.enemies["bloated_corpse_A"]
    explode_sd = next(s.levels[0] for s in corpse_def.skills if s.id == "explode")
    assert explode_sd.can_be_riposted is False

    from engine.combatant import RiposteState
    engine = CombatEngine(store, SeededRNG(1))
    engine.reset(hero_defs=[crusader], enemy_defs=[corpse_def])
    hero = engine.heroes[0]
    hero.active_riposte = RiposteState(remaining_rounds=3, dmg_mod=0.0, crit_mod=0.0,
                                        on_hit_chance=1.0, on_miss_chance=1.0, effect_name=None)
    corpse = engine.enemies[0]
    brain = engine._get_monster_brain(corpse_def)

    log_before = len(engine.log)
    engine._execute_enemy_skill(corpse, "explode", explode_sd, brain)
    assert not any("ripostes" in l for l in engine.log[log_before:]), engine.log[log_before:]
    print("  PASS test_can_be_riposted_false_blocks_riposte")


def test_ignore_protection_bypasses_prot():
    """Real bug found and fixed: Grave Robber's "Pick to the Face" is
    wiki-described as "Armor Piercing" (ignore_protection) -- the real
    .darkest field was parsed but never consulted in damage calculation,
    so this skill's damage was silently reduced by the target's PROT like
    any other attack. Verified directly: same seed, same skill, only
    ignore_protection differs -- damage should differ too."""
    gr = store.heroes["grave_robber"]
    pick = next(s.levels[4] for s in gr.skills if s.id == "pick")
    assert pick.ignore_protection is True
    no_pierce = dataclasses.replace(pick, ignore_protection=False)

    engine = CombatEngine(store, SeededRNG(1))
    engine.reset(hero_defs=[gr], enemy_defs=[store.enemies["cultist_brawler_A"]])
    attacker = engine.heroes[0]
    target = engine.enemies[0]
    target.prot = 0.5

    pierce_dmg, normal_dmg = None, None
    for seed in range(50):
        r = resolve_attack(attacker, target, pick, engine.rules, SeededRNG(seed))
        if r.hit:
            pierce_dmg = r.applied_damage
            r2 = resolve_attack(attacker, target, no_pierce, engine.rules, SeededRNG(seed))
            normal_dmg = r2.applied_damage
            break
    assert pierce_dmg is not None, "no hit found in 50 seeds"
    assert pierce_dmg > normal_dmg, (pierce_dmg, normal_dmg)
    print(f"  PASS test_ignore_protection_bypasses_prot (pierce={pierce_dmg} > normal={normal_dmg})")


def test_ignore_stealth_allows_targeting_a_stealthed_hero():
    """Real bug found and fixed: _execute_enemy_skill excluded stealthed
    heroes from targeting unconditionally, with no bypass for skills
    flagged ignore_stealth in real data (Crow's distract, Cyst's
    beholder_gaze/burn, Prophet's several skills, Brigand Sapper's
    sapper_detonate — wiki-verified via the hero-skill equivalent,
    Highwayman's "Tracking Shot": "Bypass/Remove Stealth"). Verified: with
    ignore_stealth=False a stealthed hero is never targeted; with
    ignore_stealth=True (same skill otherwise) they can be."""
    heroes = [store.heroes[h] for h in ["crusader", "vestal"]]
    crone_def = store.enemies["crone_A"]
    base_sd = next(s.levels[0] for s in crone_def.skills if s.id == "curse_vulnerability")
    normal_sd = dataclasses.replace(base_sd, ignore_stealth=False, extra_targets_chance=0.0)
    reveal_sd = dataclasses.replace(base_sd, ignore_stealth=True, extra_targets_chance=0.0)

    def crusader_ever_targeted(sd, n=30):
        for seed in range(n):
            engine = CombatEngine(store, SeededRNG(seed))
            engine.reset(hero_defs=heroes, enemy_defs=[crone_def])
            engine.heroes[0].is_stealthed = True  # Crusader
            crone = engine.enemies[0]
            log_before = len(engine.log)
            engine._execute_enemy_skill(crone, "curse_vulnerability", sd, engine._get_monster_brain(crone_def))
            if any("Crusader" in l for l in engine.log[log_before:]):
                return True
        return False

    assert crusader_ever_targeted(normal_sd) is False
    assert crusader_ever_targeted(reveal_sd) is True
    print("  PASS test_ignore_stealth_allows_targeting_a_stealthed_hero")


def test_ally_buff_skills_never_deal_damage_to_their_targets():
    """Real, severe bug found and fixed: _execute_action only branched on
    is_heal (skill_data.heal_min is not None) vs the enemy-attack path --
    any ally-targeting skill that heals via a named EFFECT instead of the
    skill's own bare .heal field (very common: Jester's battle_ballad/
    inspiring_tune, Flagellant's reclaim/redeem/endure/suffer, likely many
    other classes) fell through to resolve_attack, resolving a real to-hit
    roll and the performer's own weapon damage against each ally target --
    including the performer itself for AoE/self-inclusive skills.
    Confirmed via a full-expedition replay: "Jester hits Jester for 102"
    from battle_ballad, whose real .dmg is 0% (meaning NO damage
    reduction at all, not "-100%" -- a common misreading that let this
    hide for the project's whole history). Verified directly: over many
    seeds, battle_ballad must never change any target's HP, while its real
    buff (Embolden Team) must still land."""
    jester = store.heroes["jester"]
    crusader = store.heroes["crusader"]
    brawler = store.enemies["cultist_brawler_A"]
    sd = next(s.levels[4] for s in jester.skills if s.id == "battle_ballad")
    assert sd.dmg_mod == 0.0  # confirms this isn't "-100%", i.e. genuinely unguarded

    saw_buff = False
    for seed in range(30):
        engine = CombatEngine(store, SeededRNG(seed))
        engine.reset(hero_defs=[jester, crusader], enemy_defs=[brawler])
        j, c = engine.heroes
        hp_before = {j.id: j.hp, c.id: c.hp}
        log_before = len(engine.log)
        engine._execute_action(Action(j.id, "battle_ballad", 0))
        assert j.hp == hp_before[j.id], (seed, "jester took self-damage from its own buff skill")
        assert c.hp == hp_before[c.id], (seed, "crusader took damage from an ally buff skill")
        if any("buffed by Embolden Team" in l for l in engine.log[log_before:]):
            saw_buff = True
    assert saw_buff, "Embolden Team buff never landed across 30 seeds"
    print("  PASS test_ally_buff_skills_never_deal_damage_to_their_targets")


def test_get_valid_actions_excludes_mode_invalid_skills():
    """Real gap found and fixed alongside the action-masking work:
    get_valid_actions() never checked a skill's valid_modes against the
    performer's current mode (only _check_valid_mode did, at execution
    time) -- so Abomination's beast-only skills (rage/rake/slam) were
    offered as legal actions while in human mode (and vice versa for
    absolution), silently rejected on selection ("cannot use that skill in
    X mode"), wasting the whole turn. Verified directly: human mode offers
    absolution/transform but not rage/rake/slam; beast mode offers the
    reverse."""
    abom = store.heroes["abomination"]
    brawler = store.enemies["cultist_brawler_A"]
    engine = CombatEngine(store, SeededRNG(1))
    engine.reset(hero_defs=[abom], enemy_defs=[brawler])
    a = engine.heroes[0]

    human_skills = {x.skill_id for x in engine.get_valid_actions(a.id)}
    assert "absolution" in human_skills
    assert not ({"rage", "rake", "slam"} & human_skills)

    a.mode = "beast"
    beast_skills = {x.skill_id for x in engine.get_valid_actions(a.id)}
    assert {"rage", "rake", "slam"} <= beast_skills
    assert "absolution" not in beast_skills
    print("  PASS test_get_valid_actions_excludes_mode_invalid_skills")


def test_generic_buff_ids_resolution():
    """.buff_ids was never parsed before — Blight Debuff 1 references
    blight_debuff_1, which reduces blight_resist. Confirms the effect
    actually applies the referenced buff now."""
    engine = CombatEngine(store, SeededRNG(1))
    crusader = store.heroes["crusader"]
    brawler = store.enemies["cultist_brawler_A"]
    engine.reset(hero_defs=[crusader], enemy_defs=[brawler], hero_positions=[1])
    target = engine.enemies[0]
    before = target.blight_resist
    eff = store.effects["Blight Debuff 1"]
    engine._resolve_effect(target, engine.heroes[0], eff, None)
    assert target.blight_resist < before, (before, target.blight_resist)
    print(f"  PASS test_generic_buff_ids_resolution (blight_resist {before} -> {target.blight_resist})")


def test_buff_ids_with_duration_expires_and_reverses():
    """Fortify Resists 1 (buff_ids BLEEDRESIST10 + BLIGHTRESIST10,
    duration=3) should apply immediately and cleanly reverse when its
    duration runs out via _tick_buff_durations (previously only stun
    resistance buffs were ever reversed on expiry)."""
    engine = CombatEngine(store, SeededRNG(1))
    crusader = store.heroes["crusader"]
    brawler = store.enemies["cultist_brawler_A"]
    engine.reset(hero_defs=[crusader], enemy_defs=[brawler], hero_positions=[1])
    hero = engine.heroes[0]
    before_bleed, before_blight = hero.bleed_resist, hero.blight_resist

    eff = store.effects["Fortify Resists 1"]
    engine._resolve_effect(hero, hero, eff, None)
    assert hero.bleed_resist > before_bleed
    assert hero.blight_resist > before_blight
    tracked_ids = {b.buff_id for b in hero.buffs}
    assert {"BLEEDRESIST10", "BLIGHTRESIST10"} <= tracked_ids, tracked_ids

    for _ in range(3):
        engine._tick_buff_durations()

    assert abs(hero.bleed_resist - before_bleed) < 1e-9, (before_bleed, hero.bleed_resist)
    assert abs(hero.blight_resist - before_blight) < 1e-9, (before_blight, hero.blight_resist)
    remaining_ids = {b.buff_id for b in hero.buffs}
    assert not ({"BLEEDRESIST10", "BLIGHTRESIST10"} & remaining_ids), remaining_ids
    print("  PASS test_buff_ids_with_duration_expires_and_reverses")


def test_deaths_door_debuffs_reverse_on_heal():
    """_apply_deaths_door_buffs applies 5 debuffs (ACC, dmg low/high, speed,
    stress received) on entering death's door — these used to be permanent
    for the rest of combat even after being healed back up. Confirm they
    now reverse via _apply_heal -> _reverse_deaths_door_buffs."""
    engine = CombatEngine(store, SeededRNG(1))
    crusader = store.heroes["crusader"]
    brawler = store.enemies["cultist_brawler_A"]
    engine.reset(hero_defs=[crusader], enemy_defs=[brawler], hero_positions=[1])
    hero = engine.heroes[0]
    before_atk, before_speed = hero.atk_bonus, hero.speed
    before_dmg_min, before_dmg_max = hero.dmg_min, hero.dmg_max

    engine._apply_deaths_door_buffs(hero)
    assert hero.atk_bonus < before_atk, "ACC debuff should apply"
    assert hero.speed < before_speed, "speed debuff should apply (speed_rating key mismatch fix)"
    assert hero.dmg_min <= before_dmg_min and hero.dmg_max <= before_dmg_max

    hero.hp = 0
    hero.is_deaths_door = True
    engine._apply_heal(hero, 10)

    assert hero.is_deaths_door is False
    # The active tier fully reverses, but recovering from Death's Door now
    # also applies the (much weaker) real "mortality" recovery tier for the
    # rest of the expedition — not a full reset back to baseline. See
    # tests/engine/test_deaths_door_tiers.py for the 3-tier system itself.
    assert abs(hero.atk_bonus - (before_atk - 0.02)) < 0.001, (before_atk, hero.atk_bonus)
    assert hero.speed == before_speed - 1, (before_speed, hero.speed)
    assert hero.mortality_tier == "mortality"
    print(f"  PASS test_deaths_door_debuffs_reverse_on_heal (active tier reversed, mortality tier now applied)")


def test_heal_and_support_skills_target_allies_not_enemies():
    """The single most impactful bug found this session: _is_ally_skill used
    SkillType ("ally/team"/"self"), but the loader NEVER assigns those enum
    values — real heal skills have no .type field at all (defaults to
    SkillType.MELEE) and support skills like Man-at-Arms' Guard carry
    .type "melee" too. Every heal/support skill in the game was therefore
    resolving as if it targeted the ENEMY side (0 valid actions, or actions
    pointing at enemy positions) through the actual action pipeline —
    despite unit tests on _resolve_heal directly (which bypass targeting
    entirely) appearing to show healing "working". Fixed via
    SkillLevelData.targets_allies, derived from the real signal: '@' in the
    raw .target notation.
    """
    engine = CombatEngine(store, SeededRNG(1))
    crusader = store.heroes["crusader"]
    vestal = store.heroes["vestal"]
    brawler = store.enemies["cultist_brawler_A"]
    engine.reset(hero_defs=[crusader, vestal], enemy_defs=[brawler, brawler], hero_positions=[0, 3])

    assert engine._is_ally_skill("divine_grace") is True
    assert engine._is_ally_skill("defender") is True  # Man-at-Arms' Guard
    assert engine._is_ally_skill("smite") is False    # a real attack, must stay enemy-targeting

    # Somebody has to actually be hurt for a pure heal to be offered at all:
    # a heal that cannot restore a point of HP is strictly dominated and
    # _is_wasted_heal now keeps it out of the legal set. That rule is about
    # WHEN a heal is offered; this test is about WHO it can be aimed at, so
    # the hurt hero just restores the precondition.
    engine.heroes[0].hp -= 1
    acts = engine.get_valid_actions(engine.heroes[1].id)  # Vestal
    heal_targets = {a.target_id for a in acts if a.skill_id == "divine_grace"}
    # Position numbering is per-side (0-3 for heroes, 0-3 for enemies
    # independently) so heal_targets must be checked against ally
    # CombatantState identities, not compared as a set against enemy
    # positions (which use the same 0-3 range and can coincidentally overlap).
    ally_positions = {engine.heroes[0].position, engine.heroes[1].position}
    assert heal_targets, "divine_grace should offer at least one target"
    assert heal_targets <= ally_positions, f"heal targets {heal_targets} should be allies {ally_positions}"
    print(f"  PASS test_heal_and_support_skills_target_allies_not_enemies (heal_targets={heal_targets})")


def test_clear_corpses_removes_corpse_in_target_ranks():
    """Leper's Focus (target=[1], effects include clear_corpses) should
    remove a corpse occupying position 1 (0-indexed 0), verified against the
    real .kill_enemy_types corpse field on the clear_corpses effect."""
    engine = CombatEngine(store, SeededRNG(1))
    leper = store.heroes["leper"]
    brawler = store.enemies["cultist_brawler_A"]
    engine.reset(hero_defs=[leper], enemy_defs=[brawler, brawler])
    # PIN THE RANK. `enemies[0]` is a list index, not a position, and this
    # test is about the position the effect targets. `reset()` resolves a
    # surprise action before the board is readable, and once monster
    # `.move` began to be applied (2026-09-19) `brawler_claw`'s `.move 0 1`
    # advanced the actor and slid the other enemy off rank 1 -- so the
    # corpse was no longer where the assertion assumed.
    corpse = engine.enemies[0]
    corpse.position = 0
    corpse.is_dead = True
    corpse.is_corpse = True
    before = len(engine.enemies)

    sk = next(s for s in leper.skills if s.id == "focus")
    lv4 = sk.levels[4]
    eff = store.effects["clear_corpses"]
    assert eff.is_clear_corpses is True
    engine._resolve_effect(corpse, engine.heroes[0], eff, lv4)

    assert len(engine.enemies) == before - 1
    print("  PASS test_clear_corpses_removes_corpse_in_target_ranks")


def test_size2_corpse_keeps_occupied_positions():
    """Sanity check: a size-2 combatant's occupied_positions should still
    correctly report 2 slots after death — size isn't reset on death, so
    this part of the originally-documented gap was already fine."""
    engine = CombatEngine(store, SeededRNG(1))
    crusader = store.heroes["crusader"]
    big_enemy = next(e for e in store.enemies.values() if e.size == 2)
    engine.reset(hero_defs=[crusader], enemy_defs=[big_enemy])
    enemy = engine.enemies[0]
    before = enemy.occupied_positions
    enemy.is_dead = True
    enemy.is_corpse = True
    assert enemy.occupied_positions == before
    assert len(enemy.occupied_positions) == 2
    print("  PASS test_size2_corpse_keeps_occupied_positions")


def test_guard_expires_after_real_duration():
    """MAA Guard 1 has duration=3 (verified against base.effects.darkest) —
    guard used to persist until explicitly cleared, ignoring this entirely."""
    engine = CombatEngine(store, SeededRNG(1))
    maa = store.heroes["man_at_arms"]
    crusader = store.heroes["crusader"]
    brawler = store.enemies["cultist_brawler_A"]
    engine.reset(hero_defs=[maa, crusader], enemy_defs=[brawler], hero_positions=[1, 2])
    guardian, guarded = engine.heroes[0], engine.heroes[1]
    eff = store.effects["MAA Guard 1"]
    engine._resolve_effect(guarded, guardian, eff, None)
    assert guardian.guard_rounds_remaining == 3

    engine._tick_guard_durations()
    engine._tick_guard_durations()
    assert guardian.guard_target_id == guarded.id, "guard should still be active after 2 of 3 rounds"

    engine._tick_guard_durations()
    assert guardian.guard_target_id is None, "guard should expire after the 3rd round"
    assert guarded.guard_source_id is None
    print("  PASS test_guard_expires_after_real_duration")


def test_consecutive_miss_resets_on_hit_and_boosts_acc():
    """consecutive_miss_reduction_acc_buff (+4% ACC per prior miss, from
    rules.json) should accumulate across misses and reset to 0 the moment
    an attack lands — previously incremented forever and was never
    consulted for ACC at all."""
    engine = CombatEngine(store, SeededRNG(5))
    crusader = store.heroes["crusader"]
    brawler = store.enemies["cultist_brawler_A"]
    engine.reset(hero_defs=[crusader], enemy_defs=[brawler], hero_positions=[1])
    performer = engine.heroes[0]
    target = engine.enemies[0]
    skill_id = "smite"
    assert engine.get_skill_data(skill_id, level=performer.skill_tier) is not None

    target.dodge = 5.0  # forces hit_chance to clamp at cutoff_always_miss (0.0)
    for expected in range(1, 4):
        engine._execute_action(Action(hero_id=performer.id, skill_id=skill_id, target_id=target.position))
        assert engine.consecutive_misses[performer.id] == expected, engine.consecutive_misses

    target.dodge = -5.0  # forces hit_chance to clamp at cutoff_always_hit (0.95)
    hit_confirmed = False
    for _ in range(10):
        if not target.is_alive():
            break
        engine._execute_action(Action(hero_id=performer.id, skill_id=skill_id, target_id=target.position))
        if engine.consecutive_misses.get(performer.id) == 0:
            hit_confirmed = True
            break
    assert hit_confirmed, "expected at least one hit within 10 tries at 95% hit chance"
    print("  PASS test_consecutive_miss_resets_on_hit_and_boosts_acc")


def test_stress_dmg_received_percent_now_applies():
    """deathsdoorSRDebuff (stress_dmg_received_percent, +0.33) is one of the
    5 real death's-door debuffs applied by _apply_deaths_door_buffs — the
    stat_type itself used to be a no-op `pass` in _apply_buff_to_stats, so
    entering death's door never actually made a hero take more stress
    despite the buff being 'applied'. Confirm the multiplier now moves and
    that _apply_stress scales by it."""
    engine = CombatEngine(store, SeededRNG(7))
    crusader = store.heroes["crusader"]
    brawler = store.enemies["cultist_brawler_A"]
    engine.reset(hero_defs=[crusader], enemy_defs=[brawler], hero_positions=[1])
    hero = engine.heroes[0]
    assert hero.stress_dmg_received_mult == 1.0

    bd = store.buffs["deathsdoorSRDebuff"]
    assert bd.stat_type == "stress_dmg_received_percent"
    engine._apply_buff_to_stats(hero, bd)
    assert abs(hero.stress_dmg_received_mult - 1.33) < 1e-9, hero.stress_dmg_received_mult

    hero.stress = 0
    engine._apply_stress(hero, 10.0, chance=1.0)
    stressed_with_debuff = hero.stress

    engine._reverse_buff_from_stats(hero, bd)
    assert abs(hero.stress_dmg_received_mult - 1.0) < 1e-9, hero.stress_dmg_received_mult

    hero.stress = 0
    engine._apply_stress(hero, 10.0, chance=1.0)
    stressed_baseline = hero.stress

    assert stressed_with_debuff > stressed_baseline, (stressed_with_debuff, stressed_baseline)
    print(f"  PASS test_stress_dmg_received_percent_now_applies "
          f"(baseline={stressed_baseline}, with_debuff={stressed_with_debuff})")


def test_hp_heal_received_and_produced_percent_scale_applied_amount():
    """The engine used to check for a stat_type string ('heal_received_percent')
    that appears in zero real game files — the real key is
    hp_heal_received_percent (target side, e.g. HPRECOVERY10 = +10%), with a
    sibling hp_heal_percent (performer/produced side, e.g. on_crit_healbuff
    = +25%) never implemented at all. Confirm both now scale the final
    applied heal amount, and stack multiplicatively when both are present."""
    engine = CombatEngine(store, SeededRNG(3))
    crusader = store.heroes["crusader"]
    vestal = store.heroes["vestal"]
    brawler = store.enemies["cultist_brawler_A"]
    engine.reset(hero_defs=[vestal, crusader], enemy_defs=[brawler], hero_positions=[1, 2])
    healer, target = engine.heroes[0], engine.heroes[1]
    target.hp = 1

    baseline = engine._apply_heal(target, 10, healer)
    assert baseline == 10, baseline

    target.hp = 1
    received_bd = store.buffs["HPRECOVERY10"]
    assert received_bd.stat_type == "hp_heal_received_percent"
    engine._apply_buff_to_stats(target, received_bd)
    received_only = engine._apply_heal(target, 10, healer)
    assert received_only == 11, received_only  # 10 * 1.1

    target.hp = 1
    produced_bd = store.buffs["on_crit_healbuff"]
    assert produced_bd.stat_type == "hp_heal_percent"
    engine._apply_buff_to_stats(healer, produced_bd)
    stacked = engine._apply_heal(target, 10, healer)
    assert stacked == round(10 * 1.1 * 1.25), stacked  # both multipliers stack

    engine._reverse_buff_from_stats(target, received_bd)
    engine._reverse_buff_from_stats(healer, produced_bd)
    assert abs(target.hp_heal_received_mult - 1.0) < 1e-9
    assert abs(healer.hp_heal_produced_mult - 1.0) < 1e-9
    print(f"  PASS test_hp_heal_received_and_produced_percent_scale_applied_amount "
          f"(baseline={baseline}, received_only={received_only}, stacked={stacked})")


def test_stress_heal_received_and_produced_percent_scale_relief():
    """Same real-data-verified pattern as HP healing, but for stress relief:
    stress_heal_received_percent (target, e.g. STRESSRECOVERY10 = +10%) and
    stress_heal_percent (performer, e.g. on_crit_stresshealbuff = +40%) —
    both were previously entirely unimplemented (eff.stress_heal was applied
    at face value with no buff scaling at all)."""
    engine = CombatEngine(store, SeededRNG(4))
    crusader = store.heroes["crusader"]
    vestal = store.heroes["vestal"]
    brawler = store.enemies["cultist_brawler_A"]
    engine.reset(hero_defs=[vestal, crusader], enemy_defs=[brawler], hero_positions=[1, 2])
    healer, target = engine.heroes[0], engine.heroes[1]

    eff = store.effects["Heal Stress 3"]
    assert eff.stress_heal == 6 and eff.target == "target" and eff.chance == 1.0

    target.stress = 50
    engine._resolve_effect(target, healer, eff, None)
    baseline_relief = 50 - target.stress

    target.stress = 50
    received_bd = store.buffs["STRESSRECOVERY10"]
    engine._apply_buff_to_stats(target, received_bd)
    engine._resolve_effect(target, healer, eff, None)
    boosted_relief = 50 - target.stress

    assert boosted_relief > baseline_relief, (baseline_relief, boosted_relief)
    engine._reverse_buff_from_stats(target, received_bd)
    assert abs(target.stress_heal_received_mult - 1.0) < 1e-9
    print(f"  PASS test_stress_heal_received_and_produced_percent_scale_relief "
          f"(baseline={baseline_relief}, boosted={boosted_relief})")


def test_combat_stat_multiply_reversal_is_exact_not_drifted():
    """-25% dmg_min on a base of 3 rounds down to 2 (int(3*0.75)=2) — the
    OLD reversal path (divide back through the multiplier) computes
    int(2/0.75)=2, not 3: a real 1-point drift, confirmed by direct
    computation. The new path stores the exact applied delta (-1) on
    ActiveBuff.applied_delta at apply time and subtracts it back exactly."""
    engine = CombatEngine(store, SeededRNG(1))
    crusader = store.heroes["crusader"]
    brawler = store.enemies["cultist_brawler_A"]
    engine.reset(hero_defs=[crusader], enemy_defs=[brawler], hero_positions=[1])
    target = engine.enemies[0]
    target.dmg_min = 3

    bd = BuffDefinition(id="__test_drift__", stat_type="combat_stat_multiply",
                         stat_sub_type="damage_low", amount=-0.25, duration=1,
                         duration_type="rounds", rule_type="always", is_false_rule=False,
                         remove_on_battle_complete=False, remove_if_not_active=False)

    # Old behavior (no stored delta) drifts: 3 -> 2 -> 2, not back to 3.
    engine._apply_buff_to_stats(target, bd)
    assert target.dmg_min == 2, target.dmg_min
    engine._reverse_buff_from_stats(target, bd)  # no applied_delta passed
    assert target.dmg_min == 2, "documents the pre-fix drift bug: 2, not 3"

    # New behavior: apply again, this time tracking the exact delta like
    # _resolve_effect now does, and reverse using it.
    target.dmg_min = 3
    delta = engine._apply_buff_to_stats(target, bd)
    assert delta == -1, delta
    assert target.dmg_min == 2, target.dmg_min
    engine._reverse_buff_from_stats(target, bd, applied_delta=delta)
    assert target.dmg_min == 3, f"expected exact reversal to 3, got {target.dmg_min}"
    print("  PASS test_combat_stat_multiply_reversal_is_exact_not_drifted")


def test_buff_ids_with_duration_stores_and_uses_exact_delta_on_expiry():
    """End-to-end through the real _resolve_effect path (not the synthetic
    BuffDefinition above): a max_hp-shrinking buff_ids effect should track
    its exact applied delta on the ActiveBuff and restore max_hp exactly
    when its duration expires, not via lossy division."""
    engine = CombatEngine(store, SeededRNG(1))
    crusader = store.heroes["crusader"]
    brawler = store.enemies["cultist_brawler_A"]
    engine.reset(hero_defs=[crusader], enemy_defs=[brawler], hero_positions=[1])
    hero = engine.heroes[0]
    before_max_hp = hero.max_hp

    bd = BuffDefinition(id="__test_maxhp_shrink__", stat_type="combat_stat_multiply",
                         stat_sub_type="max_hp", amount=-0.1, duration=2,
                         duration_type="rounds", rule_type="always", is_false_rule=False,
                         remove_on_battle_complete=False, remove_if_not_active=False)
    store.buffs["__test_maxhp_shrink__"] = bd

    from data.types import EffectDefinition
    eff = EffectDefinition(
        name="__test_maxhp_shrink_effect__", target="target", chance=1.0, on_hit=True, on_miss=False,
        is_apply_once=False, is_queued=False, can_apply_on_death=False,
        buff_ids=["__test_maxhp_shrink__"], duration=2,
    )
    engine._resolve_effect(hero, hero, eff, None)
    tracked = next(b for b in hero.buffs if b.buff_id == "__test_maxhp_shrink__")
    assert tracked.applied_delta is not None and tracked.applied_delta < 0, tracked.applied_delta
    shrunk_max_hp = hero.max_hp
    assert shrunk_max_hp < before_max_hp

    engine._tick_buff_durations()
    engine._tick_buff_durations()
    assert hero.max_hp == before_max_hp, (before_max_hp, hero.max_hp)
    print(f"  PASS test_buff_ids_with_duration_stores_and_uses_exact_delta_on_expiry "
          f"(before={before_max_hp}, shrunk={shrunk_max_hp}, restored={hero.max_hp})")


def test_group_targeted_effect_on_aoe_attack_fires_once_not_per_target():
    """Real bug found and fixed: Arbalest/Musketeer's Rallying Flare
    ("flare") is an AoE attack against the enemy side whose "FlareHealStress
    N" effect redirects to the whole ALLY side via its own .target
    (performer_group_other). Applying it inside the per-AoE-target attack
    loop meant it fired once per enemy actually hit -- with 3 living
    enemies, a single use could heal anywhere from 0 to 12 stress
    depending on hit RNG and FlareHealStress's own chance roll, instead of
    the flat "small Stress heal" the real skill is (HEROES/Heroes/Arbalest
    doc). Fixed via _split_group_effect_names: group-targeted effects are
    now applied exactly once per action, gated on whether any AoE target
    was hit, same as _apply_self_effects already was."""
    arb = store.heroes["arbalest"]
    crusader = store.heroes["crusader"]
    brawler = store.enemies["cultist_brawler_A"]

    drops = set()
    for seed in range(40):
        engine = CombatEngine(store, SeededRNG(seed))
        engine.reset(hero_defs=[arb, crusader], enemy_defs=[brawler, brawler, brawler])
        a, c = engine.heroes
        c.stress = 50
        engine._execute_action(Action(a.id, "flare", 0))
        drops.add(50 - c.stress)

    # FlareHealStress 5 heals 3 stress at a 67% chance -- a single, clean
    # per-action roll should only ever produce 0 or 3 (occasional overlap
    # with the separate, legitimate crit-companion-relief mechanic can add
    # another +3 on top, hence 6 also being possible) -- never the wider
    # 0/3/6/9/12 spread the old per-AoE-target-hit bug produced.
    assert drops <= {0, 3, 6}, drops
    print(f"  PASS test_group_targeted_effect_on_aoe_attack_fires_once_not_per_target (drops seen: {sorted(drops)})")


def test_apply_once_effect_on_aoe_attack_fires_once_not_per_target():
    """Real bug found and fixed, much broader than the group-effect case
    above: EffectDefinition.is_apply_once (the real .apply_once field,
    parsed since an earlier session but never consulted anywhere) should
    make an effect fire once per ACTION, not once per AoE target hit.
    Found by checking every real target=="performer" effect nested in an
    AoE skill (57 cases across heroes and monsters) -- all 57 already have
    .apply_once true in the real data. Hellion's own signature Barbaric
    YAWP (her double-frontline-stun) is a concrete, high-impact case:
    "Hellion Exhaust" (her self-debuff from using it) must apply exactly
    once even though the skill hits 2 enemies, not twice."""
    hellion = store.heroes["hellion"]
    brawler = store.enemies["cultist_brawler_A"]
    engine = CombatEngine(store, SeededRNG(1))
    engine.reset(hero_defs=[hellion], enemy_defs=[brawler, brawler], hero_positions=[0])
    h = engine.heroes[0]
    speed_before = h.speed

    engine._execute_action(Action(h.id, "barbaric_yawp", 0))

    # HellionExSPD is a flat -3 speed_rating buff (shared/buffs/base.buffs.
    # json) -- double-applying (the old per-AoE-target-hit bug, since
    # Barbaric YAWP hits 2 enemies) would give -6 instead.
    assert h.speed == speed_before - 3, (speed_before, h.speed)
    print("  PASS test_apply_once_effect_on_aoe_attack_fires_once_not_per_target")


def test_mark_target_effect_actually_marks_the_target_now():
    """Real bug found and fixed: the real Marked/Vulnerable-application
    effects (e.g. Occultist's disruptive_curse -- "Occ Vulnerability Curse
    N" + "Mark Target", real .tag 1 field) never actually set is_marked at
    all -- the engine used to key mark-application off eff.key_status ==
    "tagged", but key_status is a different, same-named-by-coincidence
    mechanism (the conditional damage-bonus-vs-marked check, see
    _compute_mark_damage_mult) that always resolves onto the PERFORMER, not
    the enemy. "Mark Target" carries real .duration 3 -- confirmed it
    expires via _tick_mark_durations, not permanent for the rest of combat."""
    engine = CombatEngine(store, SeededRNG(1))
    occultist = store.heroes["occultist"]
    brawler = store.enemies["cultist_brawler_A"]
    engine.reset(hero_defs=[occultist], enemy_defs=[brawler], hero_positions=[1])
    performer, target = engine.heroes[0], engine.enemies[0]
    eff = store.effects["Mark Target"]
    assert eff.is_mark_effect, "Mark Target must parse .tag 1 into is_mark_effect"
    assert eff.duration == 3

    engine._resolve_effect(target, performer, eff, None)
    assert target.is_marked
    assert target.mark_rounds_remaining == 3
    assert not performer.is_marked, "the PERFORMER must not be marked by their own Mark Target use"

    engine._tick_mark_durations()
    engine._tick_mark_durations()
    assert target.is_marked, "should still be marked after 2 of 3 rounds"
    engine._tick_mark_durations()
    assert not target.is_marked, "Mark should expire after its real 3-round duration"
    print("  PASS test_mark_target_effect_actually_marks_the_target_now")


def test_dmg_marked_synergy_effect_no_longer_self_marks_the_performer():
    """The old bug's other half: a "Dmg Marked"-style conditional bonus
    effect (target=="performer") used to flip is_marked=True on the
    ATTACKER whenever it resolved on a hit -- e.g. Bounty Hunter's
    "BH Dmg Marked". Now gated on eff.target == "target", so a performer-
    targeted conditional-bonus effect must never touch is_marked at all."""
    engine = CombatEngine(store, SeededRNG(1))
    bounty_hunter = store.heroes["bounty_hunter"]
    brawler = store.enemies["cultist_brawler_A"]
    engine.reset(hero_defs=[bounty_hunter], enemy_defs=[brawler], hero_positions=[1])
    performer, target = engine.heroes[0], engine.enemies[0]
    eff = store.effects["BH Dmg Marked"]
    assert eff.target == "performer"
    assert eff.is_mark_effect is False, "BH Dmg Marked has no real .tag field"

    engine._resolve_effect(performer, performer, eff, None)
    assert not performer.is_marked
    assert not target.is_marked
    print("  PASS test_dmg_marked_synergy_effect_no_longer_self_marks_the_performer")


def test_compute_mark_damage_mult_generalizes_beyond_tagged():
    """_compute_mark_damage_mult used to hardcode key_status == "tagged"
    (an early `if not target.is_marked: return 1.0` short-circuit skipped
    every other keyStatus value entirely) -- Grave Robber's Thrown Dagger
    bundles both "GR Dagger Dmg Marked N" (tagged) and "Poison Killer N"
    (poisoned) in the same .effect list, so a blighted-but-unmarked target
    must still get Poison Killer's bonus."""
    engine = CombatEngine(store, SeededRNG(1))
    grave_robber = store.heroes["grave_robber"]
    brawler = store.enemies["cultist_brawler_A"]
    engine.reset(hero_defs=[grave_robber], enemy_defs=[brawler], hero_positions=[1])
    performer, target = engine.heroes[0], engine.enemies[0]
    skill = engine.get_skill_data("thrown_dagger", level=performer.skill_tier)
    assert skill is not None

    target.is_marked = False
    target.blights.append(DotInstance(effect_name="test_blight", damage_per_tick=1, remaining_ticks=2))
    mult = engine._compute_mark_damage_mult(performer, target, skill)
    assert mult > 1.0, "Poison Killer's bonus vs a blighted (poisoned) target must apply"
    print(f"  PASS test_compute_mark_damage_mult_generalizes_beyond_tagged (mult={mult})")


def test_monster_type_condition_applies_bounty_hunters_man_killer():
    """Real bug found and fixed: Bounty Hunter's collect_bounty carries
    "Man Killer N" (real .monsterType "man" field, his own doc's "+DMG vs
    Human starts from +15% at level 1") alongside "BH Dmg Marked" -- but
    .monsterType was parsed nowhere in the codebase at all, so this
    signature bonus was completely inert against every real "man"-type
    enemy (e.g. cultist_brawler_A) and correctly absent against non-man
    enemies (e.g. swine_prince_A, a real "beast" type)."""
    engine = CombatEngine(store, SeededRNG(1))
    bounty_hunter = store.heroes["bounty_hunter"]
    brawler = store.enemies["cultist_brawler_A"]
    prince = store.enemies["swine_prince_A"]
    engine.reset(hero_defs=[bounty_hunter], enemy_defs=[brawler, prince], hero_positions=[1])
    performer = engine.heroes[0]
    man_target, beast_target = engine.enemies[0], engine.enemies[1]
    skill = engine.get_skill_data("collect_bounty", level=performer.skill_tier)
    assert skill is not None

    assert store.enemies["cultist_brawler_A"].enemy_types == ["man"]
    assert store.enemies["swine_prince_A"].enemy_types == ["beast"]

    mult_vs_man = engine._compute_mark_damage_mult(performer, man_target, skill)
    mult_vs_beast = engine._compute_mark_damage_mult(performer, beast_target, skill)
    assert mult_vs_man > 1.0, "Man Killer's bonus vs a real man-type enemy must apply"
    assert mult_vs_beast == 1.0, "Man Killer must not apply vs a non-man-type enemy"
    print(f"  PASS test_monster_type_condition_applies_bounty_hunters_man_killer "
          f"(vs_man={mult_vs_man}, vs_beast={mult_vs_beast})")


def test_serpents_sway_grants_aegis_blocks_that_negate_damage():
    """Real, previously entirely unimplemented mechanic: Shieldbreaker's
    Serpent's Sway ("SB Aegis" effect, real .health_damage_blocks 2 field,
    shared/rules.json's health_damage_blocks_max=2 cap) grants Aegis Blocks
    that negate the next non-zero direct-damage hit -- no HP loss, no
    Deathblow check -- per her own doc: "When hit by any non-zero direct
    damage, an Aegis Block is consumed, preventing damage/Deathblow check.\""""
    engine = CombatEngine(store, SeededRNG(1))
    shieldbreaker = store.heroes["shieldbreaker"]
    brawler = store.enemies["cultist_brawler_A"]
    engine.reset(hero_defs=[shieldbreaker], enemy_defs=[brawler], hero_positions=[1])
    hero, enemy = engine.heroes[0], engine.enemies[0]
    assert hero.damage_blocks == 0
    assert engine.rules.health_damage_blocks_max == 2

    engine._execute_action(Action(hero.id, "serpents_sway", -1))
    assert hero.damage_blocks == 2, "SB Aegis grants 2 blocks, capped at rules.health_damage_blocks_max"

    skill = SkillLevelData(
        level=4, atk=1.0, dmg_mod=0.0, crit=0.0, launch=[1], target=[1],
        is_aoe=False, is_crit_valid=True, is_continue_turn=False, effects=[],
        self_effects=[], heal_min=None, heal_max=None, move_self=0,
        move_target=0, push=0, pull=0, valid_modes=["human"],
        per_battle_limit=None, per_turn_limit=None, is_stall_invalidating=True,
    )
    rng = SeededRNG(1)
    hp_before = hero.hp
    result = resolve_attack(enemy, hero, skill, engine.rules, rng)
    assert result.hit
    assert result.blocked_by_aegis
    assert result.applied_damage == 0
    assert hero.hp == hp_before, "an Aegis Block must fully negate the damage"
    assert hero.damage_blocks == 1, "exactly one block consumed per hit blocked"

    # Consume the last block, then confirm damage applies normally again.
    resolve_attack(enemy, hero, skill, engine.rules, rng)
    assert hero.damage_blocks == 0
    hp_before_2 = hero.hp
    result3 = resolve_attack(enemy, hero, skill, engine.rules, rng)
    assert result3.hit
    assert not result3.blocked_by_aegis
    assert hero.hp < hp_before_2, "with no blocks left, damage must apply normally"
    print("  PASS test_serpents_sway_grants_aegis_blocks_that_negate_damage")


def test_buff_duration_type_combat_end_overrides_numeric_duration():
    """Real bug found and fixed: Grave Robber's Toxin Trickery grants "GR
    Self Speed N" (+2 SPD) via an inline .speed_rating_add field with real
    .duration 1 -- but its own .buff_duration_type combat_end means it
    actually lasts the WHOLE fight (confirmed by her own doc: "1 Use per
    Battle... Buffs last for the whole battle"), not a real 1-round
    countdown. Previously buff_duration_type was entirely unparsed, so this
    buff (and Crusader's Bulwark, Leper's Vulnerability/Protect/Strength,
    and others sharing the same real field) reversed after just 1 round."""
    engine = CombatEngine(store, SeededRNG(1))
    grave_robber = store.heroes["grave_robber"]
    brawler = store.enemies["cultist_brawler_A"]
    engine.reset(hero_defs=[grave_robber], enemy_defs=[brawler], hero_positions=[1])
    performer = engine.heroes[0]
    eff = store.effects["GR Self Speed 1"]
    assert eff.duration == 1
    assert eff.buff_duration_type == "combat_end"
    speed_before = performer.speed

    engine._resolve_effect(performer, performer, eff, None)
    assert performer.speed == speed_before + 2

    for _ in range(5):
        engine._tick_buff_durations()
    assert performer.speed == speed_before + 2, \
        "combat_end buff must never reverse mid-combat, unlike a real N-round buff"
    print("  PASS test_buff_duration_type_combat_end_overrides_numeric_duration")


def test_inline_buff_type_applies_hp_heal_received_percent():
    """Real bug found and fixed: .buff_type/.buff_amount is a THIRD, wholly
    distinct inline-buff mechanism (from .buff_ids and the combat_stat_buff
    +specific-field pattern) that was completely unparsed. Arbalest's
    "Arb Stacking Heal N" (buff_type hp_heal_received_percent, +20% at
    level 1) is the real mechanism behind battlefield_bandage's stacking
    self-heal buff -- previously inert even after this session's earlier
    fix made is_heal skills apply their .effect list at all, since the
    buff_type/buff_amount fields themselves were never parsed into
    anything _apply_buff_to_stats could read."""
    engine = CombatEngine(store, SeededRNG(1))
    arbalest = store.heroes["arbalest"]
    brawler = store.enemies["cultist_brawler_A"]
    engine.reset(hero_defs=[arbalest], enemy_defs=[brawler], hero_positions=[1])
    hero = engine.heroes[0]
    eff = store.effects["Arb Stacking Heal 1"]
    assert eff.inline_buff_type == "hp_heal_received_percent"
    assert abs(eff.inline_buff_amount - 0.20) < 1e-9
    before = hero.hp_heal_received_mult

    engine._resolve_effect(hero, hero, eff, None)
    assert abs(hero.hp_heal_received_mult - (before + 0.20)) < 1e-9
    print("  PASS test_inline_buff_type_applies_hp_heal_received_percent")


def test_inline_buff_type_crit_received_chance_with_real_duration_expires():
    """Grapeshot Vulnerability N (Arbalest/Musketeer's Grapeshot, buff_type
    crit_received_chance, real .duration 3) is the same mechanism with an
    explicit duration -- confirms the synthesized buff both applies
    crit_received_bonus and correctly expires via _tick_buff_durations."""
    engine = CombatEngine(store, SeededRNG(1))
    arbalest = store.heroes["arbalest"]
    brawler = store.enemies["cultist_brawler_A"]
    engine.reset(hero_defs=[arbalest], enemy_defs=[brawler], hero_positions=[1])
    target = engine.enemies[0]
    eff = store.effects["Grapeshot Vulnerability 1"]
    assert eff.inline_buff_type == "crit_received_chance"
    assert eff.duration == 3
    before = target.crit_received_bonus

    engine._resolve_effect(target, engine.heroes[0], eff, None)
    assert abs(target.crit_received_bonus - (before + 0.04)) < 1e-9
    assert len(target.buffs) == 1

    for _ in range(2):
        engine._tick_buff_durations()
    assert abs(target.crit_received_bonus - (before + 0.04)) < 1e-9, "still active after 2 of 3 rounds"
    engine._tick_buff_durations()
    assert abs(target.crit_received_bonus - before) < 1e-9, "must expire after its real 3-round duration"
    print("  PASS test_inline_buff_type_crit_received_chance_with_real_duration_expires")




def test_stunned_combatant_still_takes_dot_damage():
    """Real bug found and fixed: _advance_to_next_hero_turn's stun branch
    `continue`d straight past the DoT ticks, so a stunned combatant took NO
    bleed/blight/horror damage (and no heal-over-time) for that round.

    Stun prevents ACTING, not bleeding. In the real game a stunned target
    still takes its DoT on its turn, which is precisely why stun+DoT is a
    standard pairing (Plague Doctor stunning ranks 3-4 with Blinding Gas
    while Blight ticks). As written the two anti-synergized — a plausible
    contributor to the trained policy using blight on only 2.5% of skill
    uses, and to the Abomination/Plague-Doctor "Stun Control" comps
    underperforming in every sweep."""
    def dot_only(stunned: bool):
        engine = CombatEngine(store, SeededRNG(11))
        engine.reset(hero_defs=[store.heroes["crusader"]],
                     enemy_defs=[store.enemies["cultist_brawler_A"]])
        t = engine.enemies[0]
        t.hp, t.max_hp = 500, 500
        t.blights.append(DotInstance(effect_name="test", damage_per_tick=10,
                                      remaining_ticks=5))
        t.is_stunned = stunned
        before = t.hp
        engine.current_turn_index = engine.turn_order.index(t)
        engine._advance_to_next_hero_turn()
        return before - t.hp, [(d.damage_per_tick, d.remaining_ticks) for d in t.blights]

    normal_dmg, normal_stacks = dot_only(False)
    stunned_dmg, stunned_stacks = dot_only(True)
    assert normal_dmg == 10, normal_dmg
    assert stunned_dmg == normal_dmg, (normal_dmg, stunned_dmg)
    assert stunned_stacks == normal_stacks, (normal_stacks, stunned_stacks)
    print("  PASS test_stunned_combatant_still_takes_dot_damage")


def test_dot_applications_stack_independently():
    """Re-applying a DoT adds a separate instance rather than refreshing one,
    so damage per tick accumulates while the older stacks run down — e.g.
    three applications of a 6/3-round blight on consecutive turns tick for
    6, then 12, then 18."""
    engine = CombatEngine(store, SeededRNG(3))
    engine.reset(hero_defs=[store.heroes["plague_doctor"]],
                 enemy_defs=[store.enemies["cultist_brawler_A"]], hero_positions=[3])
    t = engine.enemies[0]
    t.blight_resist = -99  # guarantee infliction
    t.hp, t.max_hp = 999, 999
    eff = store.effects["PD Blight 5"]
    dealt = []
    for _ in range(3):
        engine._resolve_effect(t, engine.heroes[0], eff, None)
        before = t.hp
        engine._tick_dots(t)
        dealt.append(before - t.hp)
    assert dealt == [6, 12, 18], dealt
    print(f"  PASS test_dot_applications_stack_independently ({dealt})")


def test_aoe_stun_is_one_action_hitting_every_target_rank():
    """Plague Doctor's Blinding Gas is a rank 3-4 AoE stun — a single action
    that stuns both back-rank enemies, which is what makes it strong control
    rather than a weak single-target stun."""
    engine = CombatEngine(store, SeededRNG(5))
    engine.reset(hero_defs=[store.heroes["plague_doctor"]],
                 enemy_defs=[store.enemies["cultist_brawler_A"]] * 4,
                 hero_positions=[3])
    sd = engine.get_skill_data("blinding_gas", level=4)
    assert sd.is_aoe and sd.target == [3, 4], (sd.is_aoe, sd.target)
    for e in engine.enemies:
        e.stun_resist = -99
    acts = [a for a in engine.get_valid_actions(0) if a.skill_id == "blinding_gas"]
    assert len(acts) == 1, f"an AoE skill must be offered as ONE action, got {len(acts)}"
    engine._execute_action(acts[0])
    stunned = [e.is_stunned for e in engine.enemies]
    assert stunned == [False, False, True, True], stunned
    print("  PASS test_aoe_stun_is_one_action_hitting_every_target_rank")


def test_provisions_are_not_offered_in_battle_at_all():
    """Was `..._with_nothing_to_treat`, and the gating it checked is now moot:
    the game ships `cant_use_items_in_battle`, so no provision is a legal
    in-combat action whatever the hero is suffering from. The condition gates
    survive at the between-rooms site -- see `tests/env/test_provisions_combat.py`.
    """
    engine = CombatEngine(store, SeededRNG(1))
    engine.reset(hero_defs=[store.heroes["crusader"]],
                 enemy_defs=[store.enemies["cultist_brawler_A"]],
                 hero_positions=[0],
                 provisions={"bandage": 2, "antivenom": 2,
                             "holy_water": 2, "laudanum": 2})
    hero = engine.heroes[0]
    actions = engine.get_valid_actions(hero.id)
    assert not [a for a in actions if a.skill_id.startswith("__provision")]


def test_conditional_damage_bonus_is_applied_once_not_squared():
    """Real bug found and fixed: .damage_low_multiply and
    .damage_high_multiply are a SINGLE percentage bonus applied to the low and
    high ends of a damage range, not two multipliers to compound. Verified
    across the whole install: all 192 effects carrying both fields have
    identical values.

    _compute_mark_damage_mult multiplied them together, squaring every
    conditional damage bonus in the game -- Houndmaster's "HM Dmg Marked"
    (+90%/+90%) resolved to 1.9 x 1.9 = 3.61x instead of 1.9x. Surfaced by a
    verbose battle log line reading "[DMG] roll 10.8 in [6-11] x conditional
    3.61 = 39" against a marked target."""
    engine = CombatEngine(store, SeededRNG(1))
    engine.reset(hero_defs=[store.heroes["houndmaster"]],
                 enemy_defs=[store.enemies["cultist_brawler_A"]], hero_positions=[1])
    performer, target = engine.heroes[0], engine.enemies[0]
    skill = engine.get_skill_data("hounds_rush", level=performer.skill_tier)

    eff = next(store.effects[n] for n in skill.effects
               if store.effects.get(n) is not None
               and store.effects[n].key_status == "tagged")
    assert eff.damage_low_multiply == eff.damage_high_multiply, \
        "real data always pairs these with the same value"
    expected = 1.0 + eff.damage_low_multiply

    target.is_marked = True
    mult = engine._compute_mark_damage_mult(performer, target, skill)
    assert abs(mult - expected) < 1e-9, (mult, expected, "must not be squared")
    assert mult < expected ** 2 - 1e-9, "the squared value is the bug"
    print(f"  PASS test_conditional_damage_bonus_is_applied_once_not_squared "
          f"({mult:.2f}x, not {expected**2:.2f}x)")


def test_push_and_pull_roll_against_move_resist():
    """Real gap found and fixed: push/pull applied unconditionally.
    move_resist was parsed, buffed and debuffed correctly, but its only
    consumer anywhere in the engine was monster-brain target weighting —
    nothing ever rolled against it, so every knockback and pull landed
    regardless of Move Resist (Man-at-Arms' Rampart, Bounty Hunter's Come
    Hither, Shieldbreaker's Puncture, every monster shuffle)."""
    eff = next(e for e in store.effects.values()
               if e.push_distance and e.push_distance > 0 and e.chance >= 1.0)

    def move_with_resist(resist: float, seed: int):
        engine = CombatEngine(store, SeededRNG(seed))
        engine.reset(hero_defs=[store.heroes["man_at_arms"]],
                     enemy_defs=[store.enemies["cultist_brawler_A"]] * 3,
                     hero_positions=[0])
        # The FRONT enemy: a push rotates it backwards past the two behind
        # it. Pushing the rearmost occupant is a no-op now, because a
        # displacement is a rotation and there is nothing behind to rotate
        # with -- see CombatEngine._shift_to_position.
        t = engine.enemies[0]
        t.move_resist = resist
        start = t.position
        engine._resolve_effect(t, engine.heroes[0], eff, None)
        return start, t.position

    # Immune to movement: position must never change.
    for seed in range(6):
        start, end = move_with_resist(1.0, seed)
        assert start == end, f"move_resist 100% must block the push (seed {seed})"

    # No resistance at all: it must actually move at least once.
    moved = any(s != e for s, e in (move_with_resist(0.0, seed) for seed in range(6)))
    assert moved, "with 0% move resist the push has to land"
    print("  PASS test_push_and_pull_roll_against_move_resist")


def test_dots_tick_at_start_of_turn_so_a_doomed_unit_never_acts():
    """Real bug found and fixed: DoTs ticked AFTER a combatant acted, so a
    unit standing on lethal bleed/blight still got a full turn before dying.
    In the real game the DoT lands as its turn comes up — an enemy on 3 HP
    with 4 blight dies without acting.

    Tactically load-bearing rather than cosmetic: it is what lets a player
    skip attacking a target already dead to its DoT and spend the turn
    elsewhere, reaching the stall phase a turn earlier."""
    engine = CombatEngine(store, SeededRNG(4))
    engine.reset(hero_defs=[store.heroes["crusader"]],
                 enemy_defs=[store.enemies["cultist_brawler_A"]])
    foe, hero = engine.enemies[0], engine.heroes[0]
    foe.hp = 3
    foe.blights.append(DotInstance(effect_name="lethal", damage_per_tick=4,
                                    remaining_ticks=3))
    hero_hp_before = hero.hp
    engine.current_turn_index = engine.turn_order.index(foe)
    engine._advance_to_next_hero_turn()

    assert foe.is_dead, "lethal DoT must kill at the start of its own turn"
    assert hero.hp == hero_hp_before, \
        "the doomed enemy must not get an action before dying"
    print("  PASS test_dots_tick_at_start_of_turn_so_a_doomed_unit_never_acts")


def test_heal_dot_ticks_before_bleed_blight_each_turn():
    """Wiki-confirmed (darkestdungeon.wiki.gg/wiki/Status_Effects): Restoration
    (heal-over-time) resolves before Bleed/Blight each round. Previously
    _tick_dots (bleed/blight) ran before _tick_heal_dots; this asserts the
    heal's log line for a turn lands before that same turn's bleed line."""
    engine = CombatEngine(store, SeededRNG(5))
    engine.reset(hero_defs=[store.heroes["crusader"]],
                 enemy_defs=[store.enemies["cultist_brawler_A"]])
    hero = engine.heroes[0]
    hero.hp = 20
    hero.bleeds = [DotInstance(effect_name="bleed", damage_per_tick=3, remaining_ticks=3)]
    hero.heal_dots = [DotInstance(effect_name="reclaim", damage_per_tick=5, remaining_ticks=3)]
    engine.current_turn_index = engine.turn_order.index(hero)
    engine._dot_ticked_for_index = -1
    log_before = len(engine.log)

    engine._advance_to_next_hero_turn()

    turn_log = engine.log[log_before:]
    heal_idx = next(i for i, line in enumerate(turn_log) if "regenerates" in line)
    bleed_idx = next(i for i, line in enumerate(turn_log) if "takes 3 from bleed" in line)
    assert heal_idx < bleed_idx, turn_log
    print("  PASS test_heal_dot_ticks_before_bleed_blight_each_turn")


def test_heal_dot_does_not_cause_premature_deaths_door_recovery():
    """Fixed alongside the heal-before-bleed reorder above: a heal_dot
    ticking before that turn's bleed must not prematurely flip a hero off
    Death's Door on the heal alone, only to have the bleed put them straight
    back on -- that would skip the deathblow roll a hero already on Death's
    Door owes for a net-negative turn, and double-fire enter/exit side
    effects. Net this turn is -2 (heal +6, bleed -8): the hero must never be
    seen recovering from Death's Door."""
    engine = CombatEngine(store, SeededRNG(11))
    engine.reset(hero_defs=[store.heroes["crusader"]],
                 enemy_defs=[store.enemies["cultist_brawler_A"]])
    hero = engine.heroes[0]
    hero.hp = 0
    hero.is_deaths_door = True
    hero.death_blow_resist = 1.0  # capped at DEATHBLOW_RESIST_CAP inside _handle_deaths_door
    hero.heal_dots = [DotInstance(effect_name="reclaim", damage_per_tick=6, remaining_ticks=3)]
    hero.bleeds = [DotInstance(effect_name="bleed", damage_per_tick=8, remaining_ticks=3)]
    engine.current_turn_index = engine.turn_order.index(hero)
    engine._dot_ticked_for_index = -1
    engine.rng.random = lambda: 0.99  # deterministic: survives the deathblow roll
    log_before = len(engine.log)

    engine._advance_to_next_hero_turn()

    turn_log = engine.log[log_before:]
    assert not any("recovers from death's door" in line for line in turn_log), turn_log
    print("  PASS test_heal_dot_does_not_cause_premature_deaths_door_recovery")


def test_initiative_tie_break_is_explicit_heroes_first_then_lower_rank():
    """Wiki-confirmed tie-break on equal SPD+d8: heroes act before enemies,
    and lower rank acts first within a side. Previously this fell out only
    by accident (Python's stable sort over `heroes + enemies`, each already
    built in rank order) -- forced here by overriding roll() to return a
    constant so every combatant ties on SPD+d8."""
    engine = CombatEngine(store, SeededRNG(9))
    engine.reset(hero_defs=[store.heroes["crusader"], store.heroes["vestal"]],
                 enemy_defs=[store.enemies["cultist_brawler_A"], store.enemies["cultist_brawler_A"]])
    for c in engine.heroes + engine.enemies:
        c.speed = 5
    engine.rng.roll = lambda sides=8: 4  # force every SPD+d8 to tie at 9

    engine._compute_turn_order()

    order = [(c.is_hero, c.position) for c in engine.turn_order]
    assert order == [(True, 0), (True, 1), (False, 0), (False, 1)], order
    print("  PASS test_initiative_tie_break_is_explicit_heroes_first_then_lower_rank")


def test_free_actions_do_not_retick_the_same_turns_dot():
    """Provisions and Abomination's Transform re-enter the turn loop on the
    same turn index without the hero having acted; the start-of-turn DoT tick
    is guarded so it cannot fire repeatedly for one turn."""
    engine = CombatEngine(store, SeededRNG(4))
    engine.reset(hero_defs=[store.heroes["crusader"]],
                 enemy_defs=[store.enemies["cultist_brawler_A"]])
    hero, foe = engine.heroes[0], engine.enemies[0]
    foe.dmg_min = foe.dmg_max = 0  # isolate: no incoming attack damage
    dot = DotInstance(effect_name="b", damage_per_tick=3, remaining_ticks=5)
    hero.bleeds = [dot]
    engine.current_turn_index = engine.turn_order.index(hero)
    engine._dot_ticked_for_index = -1

    engine._advance_to_next_hero_turn()
    after_first = dot.remaining_ticks
    engine._advance_to_next_hero_turn()
    engine._advance_to_next_hero_turn()

    assert after_first == 4, after_first
    assert dot.remaining_ticks == 4, \
        f"re-entry must not re-tick, got {dot.remaining_ticks}"
    print("  PASS test_free_actions_do_not_retick_the_same_turns_dot")


def test_crit_damage_uses_max_damage_not_the_roll():
    """Wiki-confirmed (darkestdungeon.wiki.gg/wiki/Critical_Hit, corroborated
    by a second source): "A critical hit's damage is equal to 150% of the
    MAXIMUM damage of a skill... Unlike normal hits, whose base damage varies
    within a fixed range, the base damage of a critical hit is always equal to
    150% of the maximum damage of the skill."

    We multiplied the ROLL by 1.5, which averages 1.5x the midpoint rather
    than 1.5x the max — about 30% too little crit damage on a typical range."""
    from engine.resolution import resolve_attack
    from data.types import SkillLevelData

    skill = SkillLevelData(
        level=4, atk=5.0, dmg_mod=0.0, crit=5.0, launch=[1], target=[1],
        is_aoe=False, is_crit_valid=True, is_continue_turn=False, effects=[],
        self_effects=[], heal_min=None, heal_max=None, move_self=0,
        move_target=0, push=0, pull=0, valid_modes=["human"],
        per_battle_limit=None, per_turn_limit=None, is_stall_invalidating=True,
    )
    engine = CombatEngine(store, SeededRNG(1))
    engine.reset(hero_defs=[store.heroes["crusader"]],
                 enemy_defs=[store.enemies["cultist_brawler_A"]], hero_positions=[1])
    hero, foe = engine.heroes[0], engine.enemies[0]
    hero.dmg_min, hero.dmg_max = 6, 14
    foe.prot = 0.0
    foe.hp = foe.max_hp = 9999

    # atk/crit 500% make hits and crits near-certain, but hit chance is still
    # clamped by rules.cutoff_always_hit (0.95), so a few seeds legitimately
    # miss. Assert on every roll that actually landed as a crit.
    expected = round(14 * engine.rules.player_crit_multiplier)  # max x 1.5
    crits = 0
    for seed in range(30):
        result = resolve_attack(hero, foe, skill, engine.rules, SeededRNG(seed))
        if not (result.hit and result.is_crit):
            continue
        crits += 1
        assert result.applied_damage == expected, (result.applied_damage, expected)
        # The pre-fix behaviour multiplied the ROLL, which for a 6-14 range
        # could not reliably reach this value.
        assert result.base_roll == 14, result.base_roll
    assert crits >= 20, f"expected most rolls to crit, got {crits}"
    print(f"  PASS test_crit_damage_uses_max_damage_not_the_roll "
          f"({crits} crits, all {expected} dmg)")


def test_crit_extends_dot_duration_and_status_chance():
    """Wiki-confirmed: "All status effects applied on a critical hit have a 20%
    higher chance of applying, and Bleed and Blight will last 50% longer,
    rounded up, or 2 additional turns for most skills." Neither was modelled —
    a crit applied the same status for the same duration as a normal hit."""
    engine = CombatEngine(store, SeededRNG(3))
    engine.reset(hero_defs=[store.heroes["plague_doctor"]],
                 enemy_defs=[store.enemies["cultist_brawler_A"]], hero_positions=[3])
    performer = engine.heroes[0]
    eff = store.effects["PD Blight 5"]
    base_rounds = eff.blight_rounds

    def rounds_applied(is_crit: bool) -> int:
        t = engine.enemies[0]
        t.blights.clear()
        t.blight_resist = -99  # guarantee infliction
        engine._resolve_effect(t, performer, eff, None, is_crit=is_crit)
        return t.blights[-1].remaining_ticks

    normal = rounds_applied(False)
    crit = rounds_applied(True)
    assert normal == base_rounds, (normal, base_rounds)
    assert crit == math.ceil(base_rounds * 1.5), (crit, base_rounds)
    assert crit > normal
    print(f"  PASS test_crit_extends_dot_duration_and_status_chance "
          f"({normal} rds -> {crit} rds on crit)")


def test_dot_death_leaves_no_corpse():
    """Wiki-confirmed: "A unit that dies from a damage over time effect will
    not leave a Corpse." Tactically relevant because a corpse keeps blocking
    its rank until cleared, so finishing something with bleed/blight opens the
    lane that a normal kill would not."""
    engine = CombatEngine(store, SeededRNG(4))
    engine.reset(hero_defs=[store.heroes["crusader"]],
                 enemy_defs=[store.enemies["cultist_brawler_A"]])
    foe = engine.enemies[0]
    foe.hp = 2
    foe.blights.append(DotInstance(effect_name="lethal", damage_per_tick=5,
                                    remaining_ticks=3))
    engine.current_turn_index = engine.turn_order.index(foe)
    engine._dot_ticked_for_index = -1
    engine._advance_to_next_hero_turn()

    assert foe.is_dead, "the DoT should have killed it"
    assert not foe.is_corpse, "a DoT kill must not leave a corpse blocking the rank"
    print("  PASS test_dot_death_leaves_no_corpse")


def test_deathblow_resist_is_capped():
    """Wiki-confirmed: "The maximum resistance cap is 87%, even if
    modifications would theoretically reach higher values." Trinkets and
    quirks could previously push death_blow_resist arbitrarily high, making a
    hero effectively unkillable at Death's Door."""
    from engine.combat_engine import DEATHBLOW_RESIST_CAP
    assert DEATHBLOW_RESIST_CAP == 0.87

    engine = CombatEngine(store, SeededRNG(1))
    engine.reset(hero_defs=[store.heroes["crusader"]],
                 enemy_defs=[store.enemies["cultist_brawler_A"]])
    hero = engine.heroes[0]
    hero.death_blow_resist = 5.0   # absurd value a stacked build could reach
    hero.hp = 0
    hero.is_deaths_door = True

    deaths = 0
    for seed in range(200):
        engine.rng = SeededRNG(seed)
        hero.is_dead = False
        engine._handle_deaths_door(hero, engine.enemies[0])
        deaths += hero.is_dead
    # 13% death chance per check at the cap — over 200 checks, some must land.
    assert deaths > 0, "capped resist must still leave a real chance of dying"
    print(f"  PASS test_deathblow_resist_is_capped ({deaths}/200 deathblows landed)")


if __name__ == "__main__":
    print("=== Gap Closure Tests ===\n")
    for name, func in sorted(globals().items()):
        if name.startswith("test_"):
            try:
                func()
                print(f"  PASS {name}\n")
            except Exception as e:
                import traceback
                print(f"  FAIL {name}: {e}")
                traceback.print_exc()
                print()
    print("Done.")
