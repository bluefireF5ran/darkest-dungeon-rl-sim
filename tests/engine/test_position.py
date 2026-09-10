"""Test position/range validation and target selection."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from engine.action import Action, validate_action, get_valid_actions
from data.types import SkillLevelData


def make_skill_data(skill_id: str, launch: list[int], target: list[int], is_aoe: bool = False):
    return SkillLevelData(
        level=4,
        atk=1.0,
        dmg_mod=0.0,
        crit=0.0,
        launch=launch,
        target=target,
        is_aoe=is_aoe,
        is_crit_valid=True,
        is_continue_turn=False,
        effects=[],
        self_effects=[],
        heal_min=None,
        heal_max=None,
        move_self=0,
        move_target=0,
        push=0,
        pull=0,
        valid_modes=["human"],
        per_battle_limit=None,
        per_turn_limit=None,
        is_stall_invalidating=True,
    )


def test_launch_validation():
    """Hero in position 2 can use skill that requires launch [3,2]."""
    skill_map = {"manacles": make_skill_data("manacles", launch=[3, 2], target=[1, 2, 3])}
    enemy_pos = [0, 1, 2, 3]

    valid = get_valid_actions(0, 1, enemy_pos, ["manacles"], skill_map)
    assert len(valid) > 0, "Should have valid targets"
    assert valid[0].hero_id == 0
    assert valid[0].skill_id == "manacles"

    # Hero at position 4 should not be able to use it
    valid_p4 = get_valid_actions(0, 3, enemy_pos, ["manacles"], skill_map)
    assert len(valid_p4) == 0, "Position 4 cannot use manacles (launch=[3,2])"
    print("  PASS launch_validation")


def test_launch_range_position_1():
    """Position 1 (0-indexed=0) hero cannot use skill requiring launch [4,3]."""
    skill_map = {"sniper_shot": make_skill_data("sniper_shot", launch=[4, 3], target=[2, 3, 4])}
    enemy_pos = [0, 1, 2, 3]
    valid = get_valid_actions(0, 0, enemy_pos, ["sniper_shot"], skill_map)
    assert len(valid) == 0, "Frontline hero cannot use backline skill"
    print("  PASS launch_range_position_1")


def test_self_targeting():
    """Self-targeting skills have no target positions."""
    skill_map = {"absolution": make_skill_data("absolution", launch=[4, 3, 2, 1], target=[])}
    valid = get_valid_actions(0, 2, [], ["absolution"], skill_map)
    assert len(valid) == 1
    assert valid[0].target_id == -1
    print("  PASS self_targeting")


def test_aoe_skill():
    """AoE skills hit all enemies regardless of specific target position."""
    skill_map = {"grapeshot": make_skill_data("grapeshot", launch=[3, 2], target=[3, 2], is_aoe=True)}
    enemy_pos = [0, 1, 2, 3]
    valid = get_valid_actions(0, 2, enemy_pos, ["grapeshot"], skill_map)
    assert len(valid) > 0
    print("  PASS aoe_skill")


def test_target_enemy_not_ally():
    """Skills targeting enemy positions should not target allies."""
    skill_map = {"manacles": make_skill_data("manacles", launch=[3, 2], target=[1, 2, 3])}
    enemy_pos = [0, 1, 2]  # positions 1-3 on enemy side
    valid = get_valid_actions(0, 1, enemy_pos, ["manacles"], skill_map, target_allies=False)
    assert len(valid) == 3  # targets 1,2,3
    for a in valid:
        assert 0 <= a.target_id < 3
    print("  PASS target_enemy_not_ally")


def test_validate_action_correct():
    skill_map = {"manacles": make_skill_data("manacles", launch=[3, 2], target=[1, 2, 3])}
    hero_pos = {0: 1}
    enemy_pos = [0, 1, 2, 3]
    action = Action(0, "manacles", 0)
    valid, msg = validate_action(action, hero_pos, enemy_pos, [], {0: ["manacles"]}, skill_map)
    assert valid, f"Should be valid: {msg}"
    print("  PASS validate_action_correct")


def test_validate_action_wrong_launch():
    skill_map = {"manacles": make_skill_data("manacles", launch=[3, 2], target=[1, 2, 3])}
    hero_pos = {1: 3}  # position 4
    enemy_pos = [0, 1, 2]
    action = Action(1, "manacles", 0)
    valid, msg = validate_action(action, hero_pos, enemy_pos, [], {1: ["manacles"]}, skill_map)
    assert not valid
    print("  PASS validate_action_wrong_launch")


if __name__ == "__main__":
    print("=== Position/Range Validation Tests ===\n")
    for name, func in sorted(globals().items()):
        if name.startswith("test_"):
            try:
                func()
                print(f"  PASS {name}")
            except Exception as e:
                print(f"  FAIL {name}: {e}")
    print("\nDone.")
