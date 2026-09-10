"""Tests for the heart attack mechanic (stress >= 200 while Afflicted) —
wiki-sourced (no rules.json key exposes this): HP->0/Death's Door, stress
reset to 170, or instant death (bypassing deathblow-resist) if already at
Death's Door. A Virtuous hero at 200 loses Virtue and resets to 0 instead.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests._fixtures import get_store

from engine import SeededRNG, CombatEngine


from data.paths import GAME_DIR
from data.paths import CSV_DIR
store = get_store()


def _fresh_hero():
    # Jester (SPD 9), not Crusader (SPD 3, the slowest hero in the game) --
    # reset() auto-resolves any enemy turns faster than the first hero
    # before returning, so a slower hero can legitimately take real pre-
    # turn stress/damage here with the real hidden +5 ACC bonus now
    # applied (engine/resolution.py). These tests manipulate stress/hp
    # directly afterward and need a hero guaranteed to still be exactly
    # "fresh" (0 stress) at that point, not literally zero combat
    # resolution having happened.
    jester = store.heroes["jester"]
    brawler = store.enemies["cultist_brawler_A"]
    engine = CombatEngine(store, SeededRNG(1))
    engine.reset(hero_defs=[jester], enemy_defs=[brawler])
    return engine, engine.heroes[0]


def test_afflicted_hero_crossing_200_stress_suffers_heart_attack():
    engine, hero = _fresh_hero()
    hero.is_afflicted = True
    hero.stress = 195
    engine._apply_stress(hero, 10)
    assert hero.hp == 0
    assert hero.is_deaths_door
    assert hero.stress == 170
    assert any("heart attack" in line for line in engine.log)
    print("  PASS test_afflicted_hero_crossing_200_stress_suffers_heart_attack")


def test_hero_already_at_deaths_door_dies_instantly_bypassing_resist():
    engine, hero = _fresh_hero()
    hero.is_deaths_door = True
    hero.hp = 0
    hero.death_blow_resist = 1.0  # would normally always resist a deathblow roll
    hero.is_afflicted = True
    hero.stress = 195
    engine._apply_stress(hero, 10)
    assert hero.is_dead, "heart attack should kill instantly, ignoring death_blow_resist entirely"
    print("  PASS test_hero_already_at_deaths_door_dies_instantly_bypassing_resist")


def test_virtuous_hero_at_200_loses_virtue_instead_of_heart_attack():
    engine, hero = _fresh_hero()
    hero.is_virtuous = True
    hero.stress = 195
    engine._apply_stress(hero, 10)
    assert not hero.is_virtuous
    assert hero.stress == 0
    assert not hero.is_deaths_door
    assert hero.hp > 0
    print("  PASS test_virtuous_hero_at_200_loses_virtue_instead_of_heart_attack")


def test_stress_below_200_does_not_trigger_heart_attack():
    """is_afflicted pre-set directly (bypassing resolve_check's own RNG,
    same as every other test here) so the stress value stays exactly
    controlled — resolve_check's guard (if already afflicted/virtuous,
    early-return) means it won't fire again and disturb it."""
    engine, hero = _fresh_hero()
    hero.is_afflicted = True
    hero.stress = 150
    engine._apply_stress(hero, 10)
    assert hero.stress == 160
    assert hero.hp > 0
    assert not hero.is_deaths_door
    print("  PASS test_stress_below_200_does_not_trigger_heart_attack")


if __name__ == "__main__":
    print("=== Heart Attack Tests ===\n")
    for name, func in sorted(globals().items()):
        if name.startswith("test_"):
            try:
                func()
            except Exception as e:
                import traceback
                print(f"  FAIL {name}: {e}")
                traceback.print_exc()
    print("\nDone.")
