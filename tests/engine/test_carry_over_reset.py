"""Tests for cross-combat carry-over — CombatEngine.reset(carry_over=...),
the primitive that lets a full expedition (Milestone 9) chain multiple
combats with the same 4 heroes, continuing where the last one left off.
HP/stress/Death's-Door/mortality-tier, the overstress personality and the
DoTs persist; everything else (buffs, guard, riposte, stun, cooldowns,
round) resets fresh every combat.

The DoTs were added on 2026-09-02 on the strength of `rules.json`'s
`dotHallwayStepRatio` -- see `CarryOverHeroState`.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests._fixtures import get_store

from engine import SeededRNG, CombatEngine, CarryOverHeroState
from engine.combatant import DotInstance


from data.paths import GAME_DIR
from data.paths import CSV_DIR
store = get_store()


def test_none_carry_over_is_identical_to_before_this_param_existed():
    # Jester (SPD 9), not Crusader (SPD 3, the slowest hero in the game) --
    # reset() auto-resolves any enemy turns faster than the first hero
    # before returning (see CombatEngine.reset()'s docstring), so a slower
    # hero can legitimately take real pre-turn damage here with the real
    # hidden +5 ACC bonus now applied (engine/resolution.py). This test
    # only cares about carry_over=None not artificially altering a fresh
    # hero's state, so it needs a hero fast enough to never be attacked
    # before its own first turn, not literally zero combat resolution.
    jester = store.heroes["jester"]
    brawler = store.enemies["cultist_brawler_A"]
    engine = CombatEngine(store, SeededRNG(1))
    engine.reset(hero_defs=[jester], enemy_defs=[brawler])
    hero = engine.heroes[0]
    assert hero.hp == hero.max_hp
    assert hero.stress == 0
    assert hero.mortality_tier == "none"
    assert not hero.is_deaths_door
    print("  PASS test_none_carry_over_is_identical_to_before_this_param_existed")


def test_hp_and_stress_carry_forward_into_next_combat():
    crusader = store.heroes["crusader"]
    brawler = store.enemies["cultist_brawler_A"]
    engine = CombatEngine(store, SeededRNG(1))
    engine.reset(hero_defs=[crusader], enemy_defs=[brawler])
    hero1 = engine.heroes[0]
    hero1.hp = 25
    hero1.stress = 40

    carry = [CarryOverHeroState(hp=hero1.hp, stress=hero1.stress,
                                 is_deaths_door=False, mortality_tier="none")]
    engine.reset(hero_defs=[crusader], enemy_defs=[brawler], carry_over=carry)
    hero2 = engine.heroes[0]
    assert hero2.hp == 25
    assert hero2.stress == 40
    print("  PASS test_hp_and_stress_carry_forward_into_next_combat")


def test_deaths_door_status_and_buffs_carry_forward():
    crusader = store.heroes["crusader"]
    brawler = store.enemies["cultist_brawler_A"]
    engine = CombatEngine(store, SeededRNG(1))
    carry = [CarryOverHeroState(hp=0, stress=60, is_deaths_door=True, mortality_tier="none")]
    engine.reset(hero_defs=[crusader], enemy_defs=[brawler], carry_over=carry)
    hero = engine.heroes[0]
    assert hero.is_deaths_door is True
    assert hero.hp <= 0
    assert hero.atk_bonus < 0, "active Death's Door debuffs should be re-applied on entry"
    print("  PASS test_deaths_door_status_and_buffs_carry_forward")


def test_mortality_tier_buffs_reapplied_fresh_each_combat():
    """A hero carrying mortality_tier='mortality' into a fresh combat
    should have that tier's debuffs re-applied immediately (they don't
    persist as ActiveBuffs across reset() — CombatantState is rebuilt from
    scratch — only the fact that they're owed, via mortality_tier, does)."""
    crusader = store.heroes["crusader"]
    brawler = store.enemies["cultist_brawler_A"]
    engine = CombatEngine(store, SeededRNG(1))
    fresh = CombatEngine(store, SeededRNG(1))
    fresh.reset(hero_defs=[crusader], enemy_defs=[brawler])
    baseline_atk = fresh.heroes[0].atk_bonus

    carry = [CarryOverHeroState(hp=61, stress=10, is_deaths_door=False, mortality_tier="mortality")]
    engine.reset(hero_defs=[crusader], enemy_defs=[brawler], carry_over=carry)
    hero = engine.heroes[0]
    assert hero.mortality_tier == "mortality"
    assert abs(hero.atk_bonus - (baseline_atk - 0.02)) < 0.001, hero.atk_bonus
    print("  PASS test_mortality_tier_buffs_reapplied_fresh_each_combat")


def test_heart_attack_tier_applies_instead_of_mortality_never_both():
    crusader = store.heroes["crusader"]
    brawler = store.enemies["cultist_brawler_A"]
    engine = CombatEngine(store, SeededRNG(1))
    fresh = CombatEngine(store, SeededRNG(1))
    fresh.reset(hero_defs=[crusader], enemy_defs=[brawler])
    baseline_atk = fresh.heroes[0].atk_bonus

    carry = [CarryOverHeroState(hp=61, stress=10, is_deaths_door=False, mortality_tier="heart_attack")]
    engine.reset(hero_defs=[crusader], enemy_defs=[brawler], carry_over=carry)
    hero = engine.heroes[0]
    assert hero.mortality_tier == "heart_attack"
    assert abs(hero.atk_bonus - (baseline_atk - 0.03)) < 0.001, hero.atk_bonus
    print("  PASS test_heart_attack_tier_applies_instead_of_mortality_never_both")


def test_combat_state_resets_fresh_despite_carry_over():
    """Skill buffs and the round counter expire in the corridor and must not
    survive into the next combat even with carry_over set.

    The DoTs used to be in that list and are not any more -- but an EMPTY
    carry must still produce an unbled hero, which is what this pins. The two
    tests below pin the other direction.

    Buffs still expire because their durations are counted in ROUNDS and a
    corridor has none."""
    crusader = store.heroes["crusader"]
    brawler = store.enemies["cultist_brawler_A"]
    engine = CombatEngine(store, SeededRNG(1))
    engine.reset(hero_defs=[crusader], enemy_defs=[brawler])
    hero1 = engine.heroes[0]
    hero1.stress = 100

    carry = [CarryOverHeroState(hp=hero1.hp, stress=hero1.stress,
                                 is_deaths_door=False, mortality_tier="none")]
    engine.reset(hero_defs=[crusader], enemy_defs=[brawler], carry_over=carry)
    hero2 = engine.heroes[0]
    assert hero2.bleeds == [] and hero2.blights == []
    assert hero2.buffs == []
    assert engine.round == 0
    print("  PASS test_combat_state_resets_fresh_despite_carry_over")


def test_an_affliction_survives_into_the_next_combat():
    """**This test used to assert the opposite** — "affliction is per-battle,
    should not carry" — pinning a claim the game's own data contradicts.

    `zealous_vigil` and `turn_back_time` each grant an extra 15 stress heal
    gated on `requirements: ["afflicted"]`, and both are CAMPING skills.
    Camping happens between combats, so an afflicted hero at a campfire can
    only exist if the affliction outlived the fight. Clearing it every combat
    also handed a stressed hero a fresh 25% virtue roll in every room.

    The personality is restored by id, not re-rolled, and its stat buffs come
    back with it."""
    afflicted = [s for s in store.camping_skills.values()
                 for e in s.effects if "afflicted" in e.requirements]
    assert afflicted, "no camping skill gates on `afflicted` — the evidence is gone"

    crusader = store.heroes["crusader"]
    brawler = store.enemies["cultist_brawler_A"]
    engine = CombatEngine(store, SeededRNG(1))
    engine.reset(hero_defs=[crusader], enemy_defs=[brawler])
    hero1 = engine.heroes[0]
    hero1.is_afflicted = True
    hero1.stress = 120
    engine._draw_trait = lambda h: store.traits["fearful"]
    engine._apply_affliction_effects(hero1)
    assert hero1.affliction_id == "fearful"

    carry = [CarryOverHeroState(hp=hero1.hp, stress=hero1.stress,
                                 is_deaths_door=False, mortality_tier="none",
                                 affliction_id=hero1.affliction_id,
                                 virtue_id=hero1.virtue_id)]
    engine.reset(hero_defs=[crusader], enemy_defs=[brawler], carry_over=carry)
    hero2 = engine.heroes[0]
    assert hero2.is_afflicted is True
    assert hero2.affliction_id == "fearful"
    assert hero2.is_virtuous is False

    baseline = CombatEngine(store, SeededRNG(1))
    baseline.reset(hero_defs=[crusader], enemy_defs=[brawler])
    assert hero2.dmg_max < baseline.heroes[0].dmg_max, "trait buffs were not re-applied"
    print("  PASS test_an_affliction_survives_into_the_next_combat")


def test_carry_over_index_aligned_shorter_than_hero_defs_is_safe():
    """Fewer carry entries than heroes (e.g. because the caller filters
    out dead heroes before calling reset) shouldn't crash — missing
    entries just mean "fresh" for that index."""
    jester = store.heroes["jester"]  # SPD 9 -- see the comment in
    # test_none_carry_over_is_identical_to_before_this_param_existed for
    # why a fast hero is needed here instead of Crusader.
    brawler = store.enemies["cultist_brawler_A"]
    engine = CombatEngine(store, SeededRNG(1))
    carry = [CarryOverHeroState(hp=30, stress=20, is_deaths_door=False, mortality_tier="none")]
    engine.reset(hero_defs=[jester, jester], enemy_defs=[brawler], carry_over=carry)
    assert engine.heroes[0].hp == 30
    assert engine.heroes[1].hp == engine.heroes[1].max_hp
    print("  PASS test_carry_over_index_aligned_shorter_than_hero_defs_is_safe")


def test_a_bleed_follows_the_party_into_the_next_room():
    """`rules.json` ships `dotHallwayStepRatio = 0.5` -- a rate for ticking a
    DoT per HALLWAY TILE. That number is only meaningful if the DoT reaches the
    hallway, so dropping bleeds at the combat boundary was a fidelity gap and
    not a faithful simplification. It is also what makes a bandage worth
    carrying at all."""
    crusader = store.heroes["crusader"]
    brawler = store.enemies["cultist_brawler_A"]
    engine = CombatEngine(store, SeededRNG(1))
    engine.reset(hero_defs=[crusader], enemy_defs=[brawler])
    hero1 = engine.heroes[0]

    carry = [CarryOverHeroState(
        hp=hero1.hp, stress=hero1.stress, is_deaths_door=False,
        mortality_tier="none",
        bleeds=[DotInstance("Bleed 3", 3, 5)],
        blights=[DotInstance("Poison 2", 2, 5)],
        stress_dots=[DotInstance("Horror", 5, 5)],
        burn_stacks=4)]
    engine.reset(hero_defs=[crusader], enemy_defs=[brawler], carry_over=carry)
    hero2 = engine.heroes[0]
    # `reset()` auto-resolves up to the first hero turn, and a DoT ticks at the
    # start of its owner's turn -- so a carried DoT can legitimately have spent
    # one tick before this line. Asserting an exact count here would be
    # asserting the initiative roll. What matters is that it ARRIVED, with its
    # own name and damage rather than a fresh one.
    assert [(d.effect_name, d.damage_per_tick) for d in hero2.bleeds] == [("Bleed 3", 3)]
    assert [(d.effect_name, d.damage_per_tick) for d in hero2.blights] == [("Poison 2", 2)]
    assert [(d.effect_name, d.damage_per_tick) for d in hero2.stress_dots] == [("Horror", 5)]
    assert all(4 <= d.remaining_ticks <= 5
               for d in hero2.bleeds + hero2.blights + hero2.stress_dots)
    assert hero2.burn_stacks == 4
    print("  PASS test_a_bleed_follows_the_party_into_the_next_room")


def test_a_carried_dot_is_copied_not_aliased():
    """The carry record outlives the reset that reads it -- the env holds one
    per hero across the whole expedition. Handing the new CombatantState the
    SAME DotInstance object would let the first tick of the next combat
    decrement `remaining_ticks` inside the record it came from, rewriting what
    the previous room ended with."""
    crusader = store.heroes["crusader"]
    brawler = store.enemies["cultist_brawler_A"]
    engine = CombatEngine(store, SeededRNG(1))
    engine.reset(hero_defs=[crusader], enemy_defs=[brawler])
    hero1 = engine.heroes[0]
    original = DotInstance("Bleed 3", 3, 2)
    carry = [CarryOverHeroState(hp=hero1.hp, stress=hero1.stress,
                                is_deaths_door=False, mortality_tier="none",
                                bleeds=[original])]
    engine.reset(hero_defs=[crusader], enemy_defs=[brawler], carry_over=carry)
    hero2 = engine.heroes[0]
    assert hero2.bleeds[0] is not original
    hero2.bleeds[0].remaining_ticks = 0
    assert original.remaining_ticks == 2
    print("  PASS test_a_carried_dot_is_copied_not_aliased")


if __name__ == "__main__":
    print("=== Carry-Over Reset Tests ===\n")
    for name, func in sorted(globals().items()):
        if name.startswith("test_"):
            try:
                func()
            except Exception as e:
                import traceback
                print(f"  FAIL {name}: {e}")
                traceback.print_exc()
    print("\nDone.")
