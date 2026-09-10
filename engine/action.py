from __future__ import annotations
from enum import Enum, auto
from dataclasses import dataclass


class TargetType(Enum):
    ENEMY = auto()
    ALLY = auto()
    SELF = auto()
    ALL_ENEMIES = auto()
    ALL_ALLIES = auto()


@dataclass
class Action:
    hero_id: int
    skill_id: str
    target_id: int

    def __post_init__(self):
        if self.target_id is not None:
            self.target_id = int(self.target_id)


def validate_action(
    action: Action,
    hero_positions: dict[int, int],
    enemy_positions: list[int],
    ally_positions: list[int],
    hero_skills: dict[int, list[str]],
    skill_data_map: dict,
) -> tuple[bool, str]:
    """Validate an action. Returns (valid, reason)."""
    sid = action.hero_id

    if sid not in hero_positions:
        return False, f"Hero {sid} not found"

    if action.skill_id not in hero_skills.get(sid, []):
        return False, f"Hero {sid} doesn't have skill {action.skill_id}"

    if action.skill_id not in skill_data_map:
        return False, f"Skill {action.skill_id} not in skill data"

    pos = hero_positions[sid]
    sk = skill_data_map[action.skill_id]
    launch = sk.launch if hasattr(sk, 'launch') else []
    target_positions = sk.target if hasattr(sk, 'target') else []

    if launch and (pos + 1) not in launch:
        return False, f"Hero at position {pos+1} cannot use skill (needs launch positions {launch})"

    if not target_positions:
        if action.target_id is not None and action.target_id >= 0:
            return False, f"Self-targeting skill with non-self target"
        return True, ""

    target_slot = int(action.target_id)
    if target_slot < 0:
        return True, ""

    target_game_pos = target_slot + 1  # convert 0-indexed to game position

    if sk.is_aoe:
        if target_slot in enemy_positions:
            return True, ""
        if target_slot in ally_positions:
            return True, ""
        return False, f"No valid targets at position {target_slot}"

    if target_game_pos in target_positions:
        if target_slot in enemy_positions or target_slot in ally_positions:
            return True, ""

    return False, f"Target position {target_slot+1} not in valid targets {target_positions} or not occupied"


def get_valid_actions(
    hero_id: int,
    hero_position: int,
    target_positions_occupied: list[int],
    hero_skill_ids: list[str],
    skill_data_map: dict,
    target_allies: bool = False,
) -> list[Action]:
    """Return all valid (skill, target) combos for a hero.

    Args:
        target_positions_occupied: positions occupied by valid targets
            (enemy positions for attacks, ally positions for heals)
        target_allies: if True, targets are allies; otherwise enemies
    """
    valid: list[Action] = []
    for sk_id in hero_skill_ids:
        if sk_id not in skill_data_map:
            continue
        sk = skill_data_map[sk_id]
        launch = sk.launch if hasattr(sk, 'launch') else []

        if launch and (hero_position + 1) not in launch:
            continue

        sk_targets = sk.target if hasattr(sk, 'target') else []

        if not sk_targets:
            valid.append(Action(hero_id, sk_id, -1))
            continue

        candidates = []
        if sk.is_aoe:
            if target_positions_occupied:
                candidates.append(target_positions_occupied[0])
        else:
            # sk_targets are 1-indexed game positions; convert to 0-indexed
            candidates = sorted(set(
                p - 1 for p in sk_targets
                if (p - 1) in target_positions_occupied
            ))

        for t in candidates:
            valid.append(Action(hero_id, sk_id, t))

    return valid
