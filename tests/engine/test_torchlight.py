"""Tests for torchlight/darkness modifiers, wired from rules.json's real
darkness.range_table (5 bands: [75,inf), [50,75), [25,50), [0,25), [-inf,0)).

Torch level is fixed for the whole encounter in this simulator (no
mid-combat camping/consumables), so modifiers are applied once at reset()
as permanent stat deltas.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests._fixtures import get_store

from engine import SeededRNG, CombatEngine


from data.paths import GAME_DIR
from data.paths import CSV_DIR
store = get_store()


def _fresh_engine(torch_level, seed=1):
    engine = CombatEngine(store, SeededRNG(seed))
    crusader = store.heroes["crusader"]
    brawler = store.enemies["cultist_brawler_A"]
    engine.reset(hero_defs=[crusader], enemy_defs=[brawler], hero_positions=[1], torch_level=torch_level)
    return engine


def test_darkness_table_loaded_with_real_values():
    """Sanity check against the real rules.json values (not invented)."""
    assert len(store.rules.darkness_table) == 5
    brightest = next(b for b in store.rules.darkness_table if b.lower == 75.0)
    assert brightest.player_def_increase == 4.0
    assert brightest.monster_damage_increase == 0.0
    darkest = next(b for b in store.rules.darkness_table if b.upper == 0.0)
    assert darkest.stress_damage_increase == 40.0
    assert darkest.monster_crit_increase == 5.0
    print("  PASS test_darkness_table_loaded_with_real_values")


def test_band_lookup_boundaries():
    """Bands EXCLUDE their lower bound, per `darkness.lowest_excluded: true`.

    That yields the tiers the real game documents and this file's own comments
    used to cite while asserting the opposite — 76-100 brightest, 51-75 dim,
    26-50 dark, 0-25 pitch black. So **75 is dim, not radiant**, and an exact 0
    lands in the bottom band rather than in [0, 25).

    The old expectations here encoded `lower <= torch < upper`. Changing them is
    the point of the fix, not collateral: training and every archived eval field
    torch 100 and are unaffected, but `bridge/seed.py` feeds the engine the real
    torch out of a save, and PLAYBOOK 2.3 parks it at exactly 75 on purpose.
    See CHANGELOG.md.
    """
    engine = _fresh_engine(torch_level=100)
    cases = {
        100.0: (75.0, 3.402823e+38),
        75.1: (75.0, 3.402823e+38),
        75.0: (50.0, 75.0),
        74.9: (50.0, 75.0),
        50.0: (25.0, 50.0),
        49.9: (25.0, 50.0),
        25.0: (0.0, 25.0),
        24.9: (0.0, 25.0),
        0.0: (-3.402823e+38, 0.0),
        -0.1: (-3.402823e+38, 0.0),
    }
    for torch, expected in cases.items():
        engine.torch_level = torch
        band = engine._get_darkness_band()
        assert band is not None, f"no band matched torch={torch}"
        assert (band.lower, band.upper) == expected, f"torch={torch}: got {(band.lower, band.upper)}, expected {expected}"
    print("  PASS test_band_lookup_boundaries")


def test_brightest_torch_only_buffs_player_defense():
    """At torch=100 (brightest band), every monster/stress modifier in the
    real table is 0 — only player_def_increase (+4%) applies."""
    baseline = _fresh_engine(torch_level=100)
    hero, enemy = baseline.heroes[0], baseline.enemies[0]

    # Recompute what stats would be with NO torch modifiers at all, by
    # comparing against a hero/enemy built the same way but the base game
    # already has crit=0 for cultist_brawler_A — assert deltas directly
    # instead of needing an unmodified reference engine.
    assert abs(hero.dodge - (store.heroes["crusader"].armour_tiers[-1].dodge + 0.04)) < 1e-9
    assert hero.crit == store.heroes["crusader"].weapon_tiers[-1].crit_bonus  # unchanged, player_crit_increase=0
    assert enemy.atk_bonus == 0.0  # monster_attack_increase=0 at this band
    assert enemy.crit == 0.0  # monster_crit_increase=0 at this band
    print("  PASS test_brightest_torch_only_buffs_player_defense")


def test_darkest_torch_buffs_monsters_and_player_crit():
    """At torch=0: monsters get tougher, players swing wilder (more crit) but
    lose the brightest band's defense bonus.

    Exactly 0 is the BOTTOM band, not [0, 25) — the lower bound is excluded —
    so the player crit bonus is +3.0, not +2.0. This is the band a shift-clicked
    torch bar actually lands in, and the one PLAYBOOK 2.3's shutdown is after.
    """
    dark = _fresh_engine(torch_level=0)
    hero, enemy = dark.heroes[0], dark.enemies[0]

    base_weapon_crit = store.heroes["crusader"].weapon_tiers[-1].crit_bonus
    base_armour_dodge = store.heroes["crusader"].armour_tiers[-1].dodge

    assert hero.crit > base_weapon_crit, "player crit should increase in near-darkness"
    assert abs(hero.crit - (base_weapon_crit + 0.03)) < 1e-9  # player_crit_increase=3.0 -> +0.03
    assert abs(hero.dodge - base_armour_dodge) < 1e-9  # player_def_increase=0 at this band
    assert enemy.atk_bonus > 0.0
    assert enemy.crit > 0.0
    print(f"  PASS test_darkest_torch_buffs_monsters_and_player_crit (hero.crit={hero.crit:.4f})")


def test_monster_damage_scales_multiplicatively():
    """monster_damage_increase can't be baked into enemy.dmg_min/dmg_max at
    reset() time — _execute_enemy_turn re-derives those from the skill's
    heal_min/heal_max every turn (monster damage ranges are parsed into
    those fields) and would silently overwrite a one-time modifier. Confirm
    it's applied at that point of use instead, by actually running a turn.
    """
    bright = _fresh_engine(torch_level=100)
    dark = _fresh_engine(torch_level=0)
    bright._execute_enemy_turn(bright.enemies[0])
    dark._execute_enemy_turn(dark.enemies[0])
    bright_dmg = bright.enemies[0].dmg_max
    dark_dmg = dark.enemies[0].dmg_max
    assert dark_dmg > bright_dmg, (bright_dmg, dark_dmg)
    print(f"  PASS test_monster_damage_scales_multiplicatively (bright={bright_dmg}, dark={dark_dmg})")


def test_stress_amount_and_chance_scale_with_torch():
    # torch=-1 lands in the true darkest band [-inf, 0) — stress_damage_increase=40%.
    # (torch=0 itself lands in [0,25) at 30%, per the lower-inclusive convention.)
    dark = _fresh_engine(torch_level=-1)
    hero = dark.heroes[0]
    hero.stress = 0
    # reset() may let the enemy act first if it wins initiative (real turn
    # order, SPD+d8) — cultist_brawler_A's brawler_claw can land Brawler
    # Rend Debuff 1 (BRAWLER_STRESSDMG_D1, +10% stress received) before this
    # test ever runs. That's correct fidelity for the debuff itself, but
    # this test isolates torch-only scaling, so normalize it back to 1.0.
    hero.stress_dmg_received_mult = 1.0
    dark._apply_stress(hero, 10.0, chance=1.0)  # base amount 10 * 1.4 -> 14
    assert hero.stress == 14, f"expected 14 (10 * 1.4), got {hero.stress}"
    print(f"  PASS test_stress_amount_and_chance_scale_with_torch (stress={hero.stress})")


def test_default_torch_level_is_100_and_matches_pre_torch_behavior():
    """Default reset() (no torch_level passed) should behave exactly like
    torch_level=100 explicitly — a regression guard against accidentally
    changing the default."""
    default_engine = CombatEngine(store, SeededRNG(9))
    crusader = store.heroes["crusader"]
    brawler = store.enemies["cultist_brawler_A"]
    default_engine.reset(hero_defs=[crusader], enemy_defs=[brawler], hero_positions=[1])
    assert default_engine.torch_level == 100.0
    explicit_engine = _fresh_engine(torch_level=100, seed=9)
    assert default_engine.heroes[0].dodge == explicit_engine.heroes[0].dodge
    assert default_engine.heroes[0].crit == explicit_engine.heroes[0].crit
    print("  PASS test_default_torch_level_is_100_and_matches_pre_torch_behavior")


if __name__ == "__main__":
    print("=== Torchlight Tests ===\n")
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
