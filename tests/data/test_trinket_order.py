"""The chest's "sort by rarity" order, measured live 2026-09-27."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from data.trinket_order import rarity_sorted  # noqa: E402


def test_the_measured_chest_order_is_rarity_then_display_name():
    shuffled = ["agile_talon", "recovery_chalice", "agile_talon", "swordsmans_crest",
                "flag_5", "rw_warm_scarf", "duelist_steel_tip_boots",
                "quickening_satchel", "move_stone", "protection_stone",
                "archers_ring", "survival_guide", "sturdy_greaves", "stun_stone",
                "move_stone", "disease_charm", "diseased_herb", "speed_stone",
                "speed_stone", "vengeful_boots"]
    drawn = ["agile_talon", "agile_talon", "archers_ring", "diseased_herb",
             "flag_5", "quickening_satchel", "speed_stone", "speed_stone",
             "duelist_steel_tip_boots", "sturdy_greaves", "survival_guide",
             "swordsmans_crest", "vengeful_boots", "recovery_chalice",
             "rw_warm_scarf", "disease_charm", "move_stone", "move_stone",
             "protection_stone", "stun_stone"]
    assert rarity_sorted(shuffled) == drawn


def test_a_madman_trinket_sorts_before_the_commons():
    assert rarity_sorted(["speed_stone", "madman_1"])[0] == "madman_1"


if __name__ == "__main__":
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            _fn()
            print(f"ok {_name}")
