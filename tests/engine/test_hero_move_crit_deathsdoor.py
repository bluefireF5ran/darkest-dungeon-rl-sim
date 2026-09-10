"""Tests for 3 previously-unparsed real per-hero data sources, all found
in the same session while auditing hero wiki pages' "Other information"
section against the real .info.darkest files:

- `combat_move_skill:` — per-hero voluntary reposition distance
  (backward/forward), never read by HeroLoader before this (only mentioned
  in a docstring comment in darkest_parser.py).
- `crit: .effects "on_crit_X"` — per-class passive that fires on ANY crit
  with ANY skill, never read at all.
- `deaths_door: .recovery_buffs / .recovery_heart_attack_buffs` — 2 further
  real debuff tiers beyond the active `.buffs` tier (already implemented);
  only `.buffs` was read before this.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests._fixtures import get_store



from data.paths import GAME_DIR
from data.paths import CSV_DIR
store = get_store()


# (backward, forward), verified directly against combat_move_skill records,
# cross-checked against the wiki's "Other information" Movement field.
EXPECTED_MOVE = {
    "crusader": (1, 1), "leper": (0, 1), "hellion": (0, 1), "plague_doctor": (1, 1),
    "occultist": (2, 2), "vestal": (1, 1), "jester": (3, 3), "arbalest": (2, 0),
    "houndmaster": (2, 2), "grave_robber": (2, 2), "highwayman": (2, 2),
    "bounty_hunter": (2, 2), "man_at_arms": (2, 2), "abomination": (2, 1),
    "antiquarian": (2, 2), "musketeer": (2, 0), "flagellant": (1, 3),
    "shieldbreaker": (2, 2),
    # The Fire's Edge. The Duelist's move is itself mode-gated, which no other
    # hero's is -- worth knowing before anyone assumes movement is stance-free.
    "duelist": (3, 3), "runaway": (2, 1),
}

EXPECTED_CRIT_EFFECT = {
    "crusader": "on_crit_prot", "shieldbreaker": "on_crit_prot",
    "leper": "on_crit_acc", "hellion": "on_crit_dmgbleeding",
    "plague_doctor": "on_crit_blightchance", "occultist": "on_crit_healdone",
    "vestal": "on_crit_healdone", "jester": "on_crit_stresshealdone",
    "arbalest": "on_crit_dmgmarked", "bounty_hunter": "on_crit_dmgmarked",
    "musketeer": "on_crit_dmgmarked", "houndmaster": "on_crit_bleedchance",
    "flagellant": "on_crit_bleedchance", "grave_robber": "on_crit_def",
    "antiquarian": "on_crit_def", "highwayman": "on_crit_speed",
    "man_at_arms": "on_crit_stressresist", "abomination": "on_crit_dmg",
    "duelist": "on_crit_duelist_def", "runaway": "on_crit_speed",
}


def test_every_hero_has_a_move_skill_matching_real_data():
    assert set(EXPECTED_MOVE.keys()) == set(store.heroes.keys()) - set()  # sanity: table covers loaded heroes
    for hero_id, (backward, forward) in EXPECTED_MOVE.items():
        hd = store.heroes[hero_id]
        assert hd.move_skill is not None, hero_id
        assert (hd.move_skill.backward, hd.move_skill.forward) == (backward, forward), \
            f"{hero_id}: expected {(backward, forward)}, got {(hd.move_skill.backward, hd.move_skill.forward)}"
        assert hd.move_skill.launch == [4, 3, 2, 1], (hero_id, hd.move_skill.launch)
    print(f"  PASS test_every_hero_has_a_move_skill_matching_real_data ({len(EXPECTED_MOVE)} heroes)")


def test_every_hero_has_a_crit_effect_matching_real_data():
    for hero_id, expected_effect in EXPECTED_CRIT_EFFECT.items():
        hd = store.heroes[hero_id]
        assert hd.crit_effect_id == expected_effect, (hero_id, hd.crit_effect_id, expected_effect)
    print(f"  PASS test_every_hero_has_a_crit_effect_matching_real_data ({len(EXPECTED_CRIT_EFFECT)} heroes)")


def test_crit_effect_ids_resolve_to_real_effects():
    for hero_id, effect_id in EXPECTED_CRIT_EFFECT.items():
        eff = store.effects.get(effect_id)
        assert eff is not None, f"{hero_id}'s crit_effect_id {effect_id!r} missing from store.effects"
    print("  PASS test_crit_effect_ids_resolve_to_real_effects")


def test_deaths_door_recovery_tiers_present():
    """Every hero should have a real 5-id .buffs list (existing) and a
    real .recovery_heart_attack_buffs list; .recovery_buffs is usually 5
    ids too but is verified genuinely EMPTY for flagellant (real data —
    his .buffs tier has 8 ids instead of the usual 5, and his file has no
    .recovery_buffs entries at all before .recovery_heart_attack_buffs)."""
    for hero_id, hd in store.heroes.items():
        assert len(hd.deaths_door_buffs) >= 5, (hero_id, hd.deaths_door_buffs)
        assert len(hd.recovery_heart_attack_buffs) == 5, (hero_id, hd.recovery_heart_attack_buffs)
        if hero_id != "flagellant":
            assert len(hd.recovery_buffs) == 5, (hero_id, hd.recovery_buffs)
    assert store.heroes["flagellant"].recovery_buffs == []
    assert len(store.heroes["flagellant"].deaths_door_buffs) == 8
    print("  PASS test_deaths_door_recovery_tiers_present")


def test_crusader_deaths_door_tiers_resolve_to_real_buffs_with_expected_amounts():
    """Cross-checks the 3-tier table verified this session: opposite-signed
    stress terms between mortality (+10%) and heart-attack (-15%) rule out
    "additive" — these are alternate tiers."""
    hd = store.heroes["crusader"]

    def stat_delta(buff_ids, stat_type, stat_sub_type):
        total = 0.0
        for bid in buff_ids:
            bd = store.buffs.get(bid)
            if bd and bd.stat_type == stat_type and bd.stat_sub_type == stat_sub_type:
                total += bd.amount
        return total

    active_acc = stat_delta(hd.deaths_door_buffs, "combat_stat_add", "attack_rating")
    mortality_acc = stat_delta(hd.recovery_buffs, "combat_stat_add", "attack_rating")
    heart_attack_acc = stat_delta(hd.recovery_heart_attack_buffs, "combat_stat_add", "attack_rating")
    assert active_acc < mortality_acc < 0, (active_acc, mortality_acc)
    assert abs(mortality_acc - (-0.02)) < 0.001, mortality_acc
    assert abs(heart_attack_acc - (-0.03)) < 0.001, heart_attack_acc

    active_sr = stat_delta(hd.deaths_door_buffs, "stress_dmg_received_percent", "")
    mortality_sr = stat_delta(hd.recovery_buffs, "stress_dmg_received_percent", "")
    heart_attack_sr = stat_delta(hd.recovery_heart_attack_buffs, "stress_dmg_received_percent", "")
    assert abs(active_sr - 0.33) < 0.001, active_sr
    assert mortality_sr > 0, mortality_sr
    assert heart_attack_sr < 0, heart_attack_sr  # opposite sign confirms alternate, not additive, tiers
    print(f"  PASS test_crusader_deaths_door_tiers_resolve_to_real_buffs_with_expected_amounts "
          f"(mortality_sr={mortality_sr}, heart_attack_sr={heart_attack_sr})")


if __name__ == "__main__":
    print("=== Hero Move/Crit/Death's-Door-Tier Loader Tests ===\n")
    for name, func in sorted(globals().items()):
        if name.startswith("test_"):
            try:
                func()
            except Exception as e:
                import traceback
                print(f"  FAIL {name}: {e}")
                traceback.print_exc()
    print("\nDone.")
