"""Tests for conditional (rule_type-gated) trinket/quirk buffs.

Real bug found and fixed: shared/buffs/base.buffs.json's BuffDefinition
carries a rule_type/rule_data field (in_rank, meleeonly/rangedonly,
hpbelow/hpabove, at_deaths_door, is_guarded, firstroundonly, stress_above,
actorStatus/is_actor_status -- ~445 real trinket/quirk buff variants) that
gates whether the buff is CURRENTLY active, e.g. "+10% DMG while below 50%
HP" or "+5% ACC while in the front rank". CombatEngine.apply_trinkets_and_
quirks used to apply every buff unconditionally and permanently at equip
time regardless of rule_type -- these tests construct synthetic
BuffDefinitions (same pattern as test_gaps.py) exercising each rule type
directly against CombatEngine._is_conditional_rule_active/_sync_conditional_
buffs, since real trinket data for a given rule type is sparse and buried
behind class/rarity requirements.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests._fixtures import get_store

from engine import SeededRNG, CombatEngine
from engine.resolution import resolve_attack
from data.types import BuffDefinition, SkillLevelData


from data.paths import GAME_DIR
from data.paths import CSV_DIR
store = get_store(fresh=True)


def make_buff(**kwargs) -> BuffDefinition:
    defaults = dict(
        id="TEST_BUFF", stat_type="combat_stat_add", stat_sub_type="attack_rating",
        amount=0.1, duration=None, duration_type="rounds", rule_type="always",
        is_false_rule=False, remove_on_battle_complete=False,
        remove_if_not_active=False, rule_data_float=0.0, rule_data_string="",
    )
    defaults.update(kwargs)
    return BuffDefinition(**defaults)


def _fresh_engine():
    jester = store.heroes["jester"]
    brawler = store.enemies["cultist_brawler_A"]
    engine = CombatEngine(store, SeededRNG(1))
    engine.reset(hero_defs=[jester], enemy_defs=[brawler])
    return engine, engine.heroes[0]


def test_always_rule_still_applies_immediately_and_unconditionally():
    """Unchanged pre-fix behavior for the 959 real rule_type="always" buffs."""
    engine, hero = _fresh_engine()
    before = hero.atk_bonus
    bd = make_buff(rule_type="always", amount=0.1)
    store.buffs["TEST_ALWAYS"] = bd
    engine.apply_trinkets_and_quirks(hero, [], [])  # no-op, just ensures no crash
    engine._apply_buff_ids(hero, ["TEST_ALWAYS"])
    assert abs(hero.atk_bonus - (before + 0.1)) < 1e-9
    assert hero.conditional_buffs == []
    print("  PASS test_always_rule_still_applies_immediately_and_unconditionally")


def test_in_rank_buff_only_active_at_the_matching_rank():
    engine, hero = _fresh_engine()
    bd = make_buff(rule_type="in_rank", rule_data_float=0.0, amount=0.1)
    assert engine._is_conditional_rule_active(hero, bd) is True  # hero is at position 0
    hero.position = 3
    assert engine._is_conditional_rule_active(hero, bd) is False
    print("  PASS test_in_rank_buff_only_active_at_the_matching_rank")


def test_in_rank_is_false_rule_inverts_the_check():
    """TRINKET_NOTBACKRANK_* pattern: is_false_rule=true means 'active
    whenever NOT in this rank', not 'active in this rank'."""
    engine, hero = _fresh_engine()
    bd = make_buff(rule_type="in_rank", rule_data_float=3.0, is_false_rule=True)
    hero.position = 0
    assert engine._is_conditional_rule_active(hero, bd) is True
    hero.position = 3
    assert engine._is_conditional_rule_active(hero, bd) is False
    print("  PASS test_in_rank_is_false_rule_inverts_the_check")


def test_hpbelow_and_hpabove_use_current_hp_fraction():
    engine, hero = _fresh_engine()
    hero.max_hp = 100
    below = make_buff(rule_type="hpbelow", rule_data_float=0.5)
    above = make_buff(rule_type="hpabove", rule_data_float=0.5)
    hero.hp = 80
    assert engine._is_conditional_rule_active(hero, below) is False
    assert engine._is_conditional_rule_active(hero, above) is True
    hero.hp = 20
    assert engine._is_conditional_rule_active(hero, below) is True
    assert engine._is_conditional_rule_active(hero, above) is False
    print("  PASS test_hpbelow_and_hpabove_use_current_hp_fraction")


def test_at_deaths_door_and_is_guarded_and_firstroundonly():
    engine, hero = _fresh_engine()
    dd_buff = make_buff(rule_type="at_deaths_door")
    guard_buff = make_buff(rule_type="is_guarded")
    round_buff = make_buff(rule_type="firstroundonly")

    assert engine._is_conditional_rule_active(hero, dd_buff) is False
    hero.is_deaths_door = True
    assert engine._is_conditional_rule_active(hero, dd_buff) is True

    assert engine._is_conditional_rule_active(hero, guard_buff) is False
    hero.guard_target_id = 999
    assert engine._is_conditional_rule_active(hero, guard_buff) is True

    assert engine.round == 0
    assert engine._is_conditional_rule_active(hero, round_buff) is True
    engine.round = 1
    assert engine._is_conditional_rule_active(hero, round_buff) is False
    print("  PASS test_at_deaths_door_and_is_guarded_and_firstroundonly")


def test_stress_above_and_actor_status():
    engine, hero = _fresh_engine()
    stress_buff = make_buff(rule_type="stress_above", rule_data_float=50.0)
    tagged_buff = make_buff(rule_type="is_actor_status", rule_data_string="tagged")

    hero.stress = 30
    assert engine._is_conditional_rule_active(hero, stress_buff) is False
    hero.stress = 70
    assert engine._is_conditional_rule_active(hero, stress_buff) is True

    assert engine._is_conditional_rule_active(hero, tagged_buff) is False
    hero.is_marked = True
    assert engine._is_conditional_rule_active(hero, tagged_buff) is True
    print("  PASS test_stress_above_and_actor_status")


def test_meleeonly_and_rangedonly_depend_on_the_skill_being_used():
    engine, hero = _fresh_engine()
    melee_buff = make_buff(rule_type="meleeonly")
    ranged_buff = make_buff(rule_type="rangedonly")
    # jester's "dirk_stab" is a real melee skill id
    assert engine._is_conditional_rule_active(hero, melee_buff, skill_id="dirk_stab") is True
    assert engine._is_conditional_rule_active(hero, ranged_buff, skill_id="dirk_stab") is False
    print("  PASS test_meleeonly_and_rangedonly_depend_on_the_skill_being_used")


def test_apply_trinkets_and_quirks_defers_conditional_buffs_instead_of_baking_them():
    engine, hero = _fresh_engine()
    store.buffs["TEST_HPBELOW"] = make_buff(
        id="TEST_HPBELOW", rule_type="hpbelow", rule_data_float=0.5,
        stat_type="combat_stat_add", stat_sub_type="attack_rating", amount=0.2)
    store.trinkets["TEST_TRINKET_HPBELOW"] = type(store.trinkets[next(iter(store.trinkets))])(
        id="TEST_TRINKET_HPBELOW", name="Test", rarity="common",
        class_requirements=[], buffs=["TEST_HPBELOW"], limit=1)

    before = hero.atk_bonus
    engine.apply_trinkets_and_quirks(hero, ["TEST_TRINKET_HPBELOW"], [])
    # hero is at full HP -- condition false, buff must NOT be baked in yet
    assert abs(hero.atk_bonus - before) < 1e-9
    assert any(bd.id == "TEST_HPBELOW" for bd in hero.conditional_buffs)
    assert "TEST_HPBELOW" not in hero.active_conditional_buffs
    print("  PASS test_apply_trinkets_and_quirks_defers_conditional_buffs_instead_of_baking_them")


def test_sync_applies_and_reverses_as_hp_crosses_the_threshold():
    engine, hero = _fresh_engine()
    hero.max_hp = 50
    hero.hp = 50
    before_atk = hero.atk_bonus
    before_dmg_min = hero.dmg_min
    bd = make_buff(id="TEST_HPBELOW_DMG", rule_type="hpbelow", rule_data_float=0.5,
                     stat_type="combat_stat_multiply", stat_sub_type="damage_low", amount=0.5)
    hero.conditional_buffs.append(bd)

    engine._sync_conditional_buffs(hero)
    assert hero.dmg_min == before_dmg_min, "full HP: buff must stay inactive"
    assert "TEST_HPBELOW_DMG" not in hero.active_conditional_buffs

    hero.hp = 10  # 20% of max_hp, below the 50% threshold
    engine._sync_conditional_buffs(hero)
    assert hero.dmg_min > before_dmg_min, "below 50% HP: buff must now be active"
    assert "TEST_HPBELOW_DMG" in hero.active_conditional_buffs

    hero.hp = 50  # healed back up
    engine._sync_conditional_buffs(hero)
    assert hero.dmg_min == before_dmg_min, "healed back above threshold: buff must reverse exactly"
    assert "TEST_HPBELOW_DMG" not in hero.active_conditional_buffs
    print("  PASS test_sync_applies_and_reverses_as_hp_crosses_the_threshold")


def test_sync_all_conditional_buffs_only_passes_skill_id_to_the_performer():
    engine, hero = _fresh_engine()
    enemy = engine.enemies[0]
    melee_buff_hero = make_buff(id="TEST_MELEE_HERO", rule_type="meleeonly",
                                  stat_type="combat_stat_add", stat_sub_type="attack_rating", amount=0.05)
    hero.conditional_buffs.append(melee_buff_hero)

    engine._sync_all_conditional_buffs(performer=hero, skill_id="dirk_stab")
    assert "TEST_MELEE_HERO" in hero.active_conditional_buffs

    engine._sync_all_conditional_buffs(performer=enemy, skill_id="dirk_stab")
    assert "TEST_MELEE_HERO" not in hero.active_conditional_buffs, \
        "hero wasn't the performer this time -- its skill-dependent buff must go inactive"
    print("  PASS test_sync_all_conditional_buffs_only_passes_skill_id_to_the_performer")


def test_crit_received_bonus_raises_defenders_chance_of_being_crit():
    """Man-at-Arms' Bellow (MAA_Bellow_CritReceived_N): crit_received_chance
    stat_type, gated by is_actor_status=='tagged' -- previously the
    stat_type itself had no handler at all (check_crit never took a target
    param), independent of the rule_type gating fixed here."""
    engine, hero = _fresh_engine()
    enemy = engine.enemies[0]
    enemy.crit_received_bonus = 1.0  # rng.random() is always < 1.0 -- deterministic crit
    rng = SeededRNG(1)
    skill = SkillLevelData(
        level=4, atk=1.0, dmg_mod=0.0, crit=0.0, launch=[1], target=[1],
        is_aoe=False, is_crit_valid=True, is_continue_turn=False, effects=[],
        self_effects=[], heal_min=None, heal_max=None, move_self=0,
        move_target=0, push=0, pull=0, valid_modes=["human"],
        per_battle_limit=None, per_turn_limit=None, is_stall_invalidating=True,
    )
    hero.crit = 0.0
    result = resolve_attack(hero, enemy, skill, engine.rules, rng)
    assert result.hit
    assert result.is_crit, "0% base crit + 100% crit_received_bonus must always crit"
    print("  PASS test_crit_received_bonus_raises_defenders_chance_of_being_crit")


# ---------------------------------------------------------------------------
# Torchlight-gated buffs (2026-08-21). `lightbelow`/`lightabove` are 234 of
# the shipped buffs and were reaching the always-active fallback, so every
# darkness trinket paid out in broad daylight. See CHANGELOG.md.
# ---------------------------------------------------------------------------

def _at_torch(torch):
    jester = store.heroes["jester"]
    brawler = store.enemies["cultist_brawler_A"]
    engine = CombatEngine(store, SeededRNG(1))
    engine.reset(hero_defs=[jester], enemy_defs=[brawler], torch_level=torch)
    return engine, engine.heroes[0]


def test_both_light_comparisons_are_strict():
    """The threshold itself belongs to NEITHER side. This is the whole
    convention, and it is what makes `lightbelow 26` name a band."""
    engine, hero = _at_torch(50.0)
    below = make_buff(rule_type="lightbelow", rule_data_float=50.0)
    above = make_buff(rule_type="lightabove", rule_data_float=50.0)
    assert engine._is_conditional_rule_active(hero, below) is False
    assert engine._is_conditional_rule_active(hero, above) is False
    engine.torch_level = 49.9
    assert engine._is_conditional_rule_active(hero, below) is True
    engine.torch_level = 50.1
    assert engine._is_conditional_rule_active(hero, above) is True
    print("  PASS test_both_light_comparisons_are_strict")


def test_the_shipped_thresholds_name_exactly_one_darkness_band():
    """`lightbelow 26` (105 trinkets) and `lightabove 75` (56) are the two
    dominant constants in the data, and under a STRICT reading each lines up
    with a band edge: the bands exclude their lower bound, so their tops are
    25/50/75. Read inclusively both would spill a point into the neighbouring
    band and name nothing. That is the evidence the operator choice rests on,
    so it is pinned rather than left in a comment."""
    engine, hero = _at_torch(100.0)
    dark = make_buff(rule_type="lightbelow", rule_data_float=26.0)
    radiant = make_buff(rule_type="lightabove", rule_data_float=75.0)
    for torch, want_dark, want_radiant in [
            (0.0, True, False), (25.0, True, False), (26.0, False, False),
            (50.0, False, False), (75.0, False, False), (75.1, False, True),
            (100.0, False, True)]:
        engine.torch_level = torch
        assert engine._is_conditional_rule_active(hero, dark) is want_dark, torch
        assert engine._is_conditional_rule_active(hero, radiant) is want_radiant, torch
    print("  PASS test_the_shipped_thresholds_name_exactly_one_darkness_band")


def test_ancestors_candle_pays_nothing_in_the_dark():
    """End to end on the real trinket the comps actually equip -- 28 hero
    slots, the largest torch-gated carrier in the game. `lightabove 50`:
    +15%/+15% damage, +2 SPD, +5% dodge, and none of it below the line."""
    lit, hero_lit = _at_torch(100.0)
    lit.apply_trinkets_and_quirks(hero_lit, ["ancestors_candle"], [])
    dim, hero_dim = _at_torch(30.0)
    dim.apply_trinkets_and_quirks(hero_dim, ["ancestors_candle"], [])
    assert hero_lit.speed - hero_dim.speed == 2
    assert hero_lit.dodge > hero_dim.dodge
    assert hero_lit.dmg_min > hero_dim.dmg_min
    # The one buff on it that is NOT torch-gated stays on in both.
    assert hero_lit.stress_dmg_received_mult == hero_dim.stress_dmg_received_mult
    print("  PASS test_ancestors_candle_pays_nothing_in_the_dark")


def test_the_light_quirks_mean_what_they_are_named():
    """An independent read on the direction, from data nobody tuned for us:
    lygophobia is the fear of DARKNESS and phengophobia the fear of DAYLIGHT,
    and each must raise stress taken in its own condition and nowhere else."""
    for torch, dark_expected in ((10.0, True), (100.0, False)):
        engine, hero = _at_torch(torch)
        base = hero.stress_dmg_received_mult
        engine.apply_trinkets_and_quirks(hero, [], ["lygophobia"])
        lygo = hero.stress_dmg_received_mult > base
        engine2, hero2 = _at_torch(torch)
        base2 = hero2.stress_dmg_received_mult
        engine2.apply_trinkets_and_quirks(hero2, [], ["phengophobia"])
        pheng = hero2.stress_dmg_received_mult > base2
        assert lygo is dark_expected, (torch, "lygophobia")
        assert pheng is (not dark_expected), (torch, "phengophobia")
    print("  PASS test_the_light_quirks_mean_what_they_are_named")


def test_a_light_buff_follows_the_torch_when_it_changes_mid_combat():
    """Nothing in the engine moves the torch today (`.torch_increase` is
    unimplemented), so this is about the buff living on the conditional path
    rather than being baked in at equip time -- which is what will make it
    correct for free when a torch effect does land."""
    engine, hero = _at_torch(100.0)
    engine.apply_trinkets_and_quirks(hero, ["ancestors_candle"], [])
    lit = hero.speed
    engine.torch_level = 10.0
    engine._sync_all_conditional_buffs()
    assert hero.speed == lit - 2
    engine.torch_level = 100.0
    engine._sync_all_conditional_buffs()
    assert hero.speed == lit
    print("  PASS test_a_light_buff_follows_the_torch_when_it_changes_mid_combat")


def test_a_camp_buff_is_never_active_in_a_fight():
    """`in_camp` reached the always-active fallback, so the party collected
    its campfire stress relief mid-combat. 25 instances equipped across the
    real comps. Camping lives in engine/camping.py and never touches this
    machinery, so inside CombatEngine the answer is a constant False."""
    engine, hero = _at_torch(100.0)
    bd = make_buff(rule_type="in_camp", stat_type="stress_heal_percent",
                   stat_sub_type="", amount=0.33)
    assert engine._is_conditional_rule_active(hero, bd) is False
    before = hero.stress_heal_produced_mult
    engine.apply_trinkets_and_quirks(hero, ["cc_set_vest_salacious_diary"], [])
    assert hero.stress_heal_produced_mult == before
    # ...while the same trinket's un-gated half still applies.
    assert hero.hp_heal_produced_mult != 0.0
    print("  PASS test_a_camp_buff_is_never_active_in_a_fight")


def test_an_unknown_rule_type_is_still_treated_as_always_active():
    """The fallback is a decision about which way to be wrong, and it is
    load-bearing for the rule types deliberately left out. Pinned so that
    adding a branch is always a deliberate act."""
    engine, hero = _at_torch(100.0)
    bd = make_buff(rule_type="monsterType", rule_data_string="unholy")
    assert engine._is_conditional_rule_active(hero, bd) is True
    print("  PASS test_an_unknown_rule_type_is_still_treated_as_always_active")


if __name__ == "__main__":
    print("=== Conditional Buff Tests ===\n")
    for name, func in sorted(globals().items()):
        if name.startswith("test_"):
            try:
                func()
            except Exception as e:
                import traceback
                print(f"  FAIL {name}: {e}")
                traceback.print_exc()
    print("\nDone.")
