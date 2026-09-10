"""Tests for ProvisionLoader (data/provision_loader.py) — parses
inventory/base.supply.inventory.items.darkest and resolves each modeled
provision (bandage/antivenom/holy_water/laudanum/food) to its real
mechanical effect in store.effects. Also regression-locks a real parser
bug found while verifying holy_water: EffectLoader's .buff_ids handling
only correctly split a QUOTED multi-value list ('.buff_ids "X" "Y"') into
a real list — an UNQUOTED bareword multi-value list (holy_water's real
'.buff_ids holy_water_blight_resist holy_water_bleed_resist
holy_water_disease_resist holy_water_debuff_resist') parsed as a single
un-splittable string, wrapped into a one-element list containing the whole
space-joined blob, which never matches any real buff id — silently
no-op'ing holy_water (and leper_*/medicinal_herbs_*/rake_buff_*l/*h,
which use the same unquoted-list shape).
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests._fixtures import get_store



from data.paths import GAME_DIR
from data.paths import CSV_DIR
store = get_store()


def test_all_five_modeled_provisions_load():
    expected = {"bandage", "antivenom", "holy_water", "laudanum", "food"}
    assert set(store.provisions.keys()) == expected, store.provisions.keys()
    for pid, pd in store.provisions.items():
        assert pd.stack_limit > 0, (pid, pd.stack_limit)
        assert pd.id == pid
    print(f"  PASS test_all_five_modeled_provisions_load ({list(store.provisions.keys())})")


def test_bandage_cures_bleed_antivenom_cures_blight():
    bandage = store.effects["bandage"]
    assert bandage.cures_bleed is True
    assert bandage.cures_blight is False
    antivenom = store.effects["antivenom"]
    assert antivenom.cures_blight is True
    assert antivenom.cures_bleed is False
    print("  PASS test_bandage_cures_bleed_antivenom_cures_blight")


def test_laudanum_clears_stress_dot():
    laudanum = store.effects["laudanum"]
    assert laudanum.clears_stress_dot is True
    print("  PASS test_laudanum_clears_stress_dot")


def test_holy_water_buff_ids_bareword_list_parses_correctly():
    """Regression test for the unquoted-bareword-buff_ids-list bug: holy_water's
    real effect line is a SPACE-SEPARATED BAREWORD list, not quoted, and
    must resolve to 4 separate buff ids, not one un-splittable blob."""
    hw = store.effects["holy_water"]
    assert hw.buff_ids == [
        "holy_water_blight_resist", "holy_water_bleed_resist",
        "holy_water_disease_resist", "holy_water_debuff_resist",
    ], hw.buff_ids
    assert hw.duration == 3
    for buff_id in hw.buff_ids:
        bd = store.buffs.get(buff_id)
        assert bd is not None, f"{buff_id} missing from store.buffs"
        assert bd.stat_type == "resistance"
        assert abs(bd.amount - 0.33) < 0.001, (buff_id, bd.amount)
    print("  PASS test_holy_water_buff_ids_bareword_list_parses_correctly")


def test_stall_summon_effects_parsed():
    for suffix, monster_prefix in [("A", "_A"), ("B", "_B"), ("C", "_C")]:
        eff = store.effects[f"stall_summon_{suffix}"]
        assert eff.summon_count == 2
        assert eff.summon_monster_ids == [f"cultist_brawler{monster_prefix}", f"cultist_witch{monster_prefix}"]
        assert eff.summon_chances == [1.0, 1.0]
    print("  PASS test_stall_summon_effects_parsed")


if __name__ == "__main__":
    print("=== Provision Loader Tests ===\n")
    for name, func in sorted(globals().items()):
        if name.startswith("test_"):
            try:
                func()
            except Exception as e:
                import traceback
                print(f"  FAIL {name}: {e}")
                traceback.print_exc()
    print("\nDone.")
