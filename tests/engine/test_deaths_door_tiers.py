"""Tests for the 3-tier Death's Door recovery system — real data confirms
these are alternate tiers (sticky, monotonic: none -> mortality ->
heart_attack), not additive: opposite-signed stress terms between
mortality (+10%) and heart_attack (-15%) rule out stacking.
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
    crusader = store.heroes["crusader"]
    brawler = store.enemies["cultist_brawler_A"]
    engine = CombatEngine(store, SeededRNG(1))
    engine.reset(hero_defs=[crusader], enemy_defs=[brawler])
    return engine, engine.heroes[0]


def test_normal_deaths_door_recovery_applies_mortality_tier_only():
    engine, hero = _fresh_hero()
    acc_original = hero.atk_bonus
    hero.hp = 0
    engine._handle_deaths_door(hero, engine.enemies[0])
    assert hero.is_deaths_door
    assert hero.atk_bonus < acc_original, "active tier's -10% ACC should be applied while at Death's Door"

    engine._apply_heal(hero, 20)
    assert not hero.is_deaths_door
    assert hero.mortality_tier == "mortality"
    # Active tier (-10% ACC) reversed, then mortality tier's own -2% ACC
    # applied — net result should be a lighter penalty than the active
    # tier, but still below the hero's original (undebuffed) ACC.
    assert hero.atk_bonus < acc_original
    assert abs(hero.atk_bonus - (acc_original - 0.02)) < 0.001, hero.atk_bonus
    print("  PASS test_normal_deaths_door_recovery_applies_mortality_tier_only")


def test_heart_attack_recovery_applies_heart_attack_tier_not_mortality():
    engine, hero = _fresh_hero()
    hero.is_afflicted = True
    hero.stress = 199
    engine._apply_stress(hero, 5)  # crosses 200 -> heart attack
    assert hero.is_deaths_door
    assert hero.stress == 170
    assert hero.next_recovery_is_heart_attack

    engine._apply_heal(hero, 20)
    assert hero.mortality_tier == "heart_attack"
    print("  PASS test_heart_attack_recovery_applies_heart_attack_tier_not_mortality")


def test_mortality_tier_never_downgrades_after_heart_attack():
    engine, hero = _fresh_hero()
    hero.is_afflicted = True
    hero.stress = 199
    engine._apply_stress(hero, 5)
    engine._apply_heal(hero, 20)
    assert hero.mortality_tier == "heart_attack"

    # A second, ordinary Death's Door visit + recovery must not downgrade
    # the hero back to "mortality".
    hero.hp = 0
    engine._handle_deaths_door(hero, engine.enemies[0])
    engine._apply_heal(hero, 20)
    assert hero.mortality_tier == "heart_attack", "should stay at the higher tier, not downgrade"
    print("  PASS test_mortality_tier_never_downgrades_after_heart_attack")


def test_default_mortality_tier_is_none_without_any_deaths_door_visit():
    engine, hero = _fresh_hero()
    assert hero.mortality_tier == "none"
    print("  PASS test_default_mortality_tier_is_none_without_any_deaths_door_visit")


if __name__ == "__main__":
    print("=== Death's Door 3-Tier Tests ===\n")
    for name, func in sorted(globals().items()):
        if name.startswith("test_"):
            try:
                func()
            except Exception as e:
                import traceback
                print(f"  FAIL {name}: {e}")
                traceback.print_exc()
    print("\nDone.")
