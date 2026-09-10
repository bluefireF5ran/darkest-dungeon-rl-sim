from __future__ import annotations
import json
from pathlib import Path
from .types import MonsterBrain, SkillDesire, TargetDesire, BonusInitiativeDesire

# fill_ally_captor_empty_skill/_target (The Hag only) is deliberately
# excluded — it's the cauldron hero-capture mechanic, a distinct game system
# (captured status, rescue) this combat-only simulator doesn't implement at
# all, not an AI gap. next_initiative_target is excluded too: real data has
# exactly one occurrence, in a brain literally named "test" that zero real
# enemies reference (confirmed via DataLoader — no enemy's monster_brain_id
# is "test"). See SkillDesire/TargetDesire docstrings in data/types.py.
MODELED_SKILL_DESIRE_TYPES = {
    "specific_skill", "random_skill", "preferred_skill", "performing_turn_skill",
    "stun_skill", "ally_alive_skill", "ally_dead_skill", "heal_skill",
    "effect_key_status_skill",
}
MODELED_TARGET_DESIRE_TYPES = {
    "random_target", "marked_target", "health_target",
    "stunned_target", "stress_target", "resistance_target",
    "rank_target", "ally_class_target",
}
# death and ally_last_damaged excluded — see BonusInitiativeDesire docstring.
MODELED_BONUS_INITIATIVE_TYPES = {
    "guaranteed", "ally_actor_class_count", "hp_ratio_threshold", "last_skill",
}


class MonsterBrainLoader:
    """Load raid/ai/base.monster_brains.json into typed MonsterBrain records.

    See SkillDesire/TargetDesire docstrings in data/types.py for exactly
    which desire types are modeled and why the rest aren't.
    """

    @staticmethod
    def load(path: str | Path) -> dict[str, MonsterBrain]:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)

        brains: dict[str, MonsterBrain] = {}
        for entry in raw.get("monster_brains", []):
            skill_desires = []
            for d in entry.get("skill_selection_desires", []):
                if d.get("type") not in MODELED_SKILL_DESIRE_TYPES:
                    continue
                data = d.get("data", {})
                skill_desires.append(SkillDesire(
                    type=d["type"],
                    base_chance=float(data.get("base_chance", 0.0)),
                    combat_skill_id=str(data.get("combat_skill_id", "")),
                    performing_turn=data.get("performing_turn"),
                    non_stunned_heroes_min=data.get("non_stunned_heroes_min"),
                    marked_heroes_min=data.get("marked_heroes_min"),
                    marked_heroes_max=data.get("marked_heroes_max"),
                    ally_base_class_id=str(data.get("ally_base_class_id", "")),
                    hp_ratio_threshold=data.get("hp_ratio_threshold"),
                    monsters_size_min=data.get("monsters_size_min"),
                    monsters_size_max=data.get("monsters_size_max"),
                    first_initiative_only=bool(data.get("first_initiative_only", False)),
                    effect_key_status=str(data.get("effect_key_status", "")),
                    guarded_monsters_min=data.get("guarded_monsters_min"),
                    guarded_monsters_max=data.get("guarded_monsters_max"),
                    monsters_min=data.get("monsters_min"),
                    monsters_max=data.get("monsters_max"),
                    virtued_heroes_max=data.get("virtued_heroes_max"),
                    heroes_min=data.get("heroes_min"),
                    non_deaths_door_heroes_min=data.get("non_deaths_door_heroes_min"),
                    non_marked_heroes_min=data.get("non_marked_heroes_min"),
                    performing_turn_min=data.get("performing_turn_min"),
                    is_valid_when_corpse_enabled=bool(
                        data.get("is_valid_when_corpse_enabled", True)),
                ))

            target_desires = []
            for d in entry.get("target_selection_desires", []):
                if d.get("type") not in MODELED_TARGET_DESIRE_TYPES:
                    continue
                data = d.get("data", {})
                target_desires.append(TargetDesire(
                    type=d["type"],
                    base_chance=float(data.get("base_chance", 0.0)),
                    specific_combat_skill_id=str(data.get("specific_combat_skill_id", "")),
                    is_greater_comparison=bool(data.get("is_greater_comparison", False)),
                    resistance_type_id=str(data.get("resistance_type_id", "")),
                    is_friendly_target_desire=bool(data.get("is_friendly_target_desire", False)),
                    is_enemy_target_desire=bool(data.get("is_enemy_target_desire", True)),
                    is_exclusive_desire=bool(data.get("is_exclusive_desire", False)),
                    can_target_stunned=bool(data.get("can_target_stunned", True)),
                    can_target_deaths_door=bool(data.get("can_target_deaths_door", True)),
                    can_target_last_hero=bool(data.get("can_target_last_hero", True)),
                    can_target_afflicted=bool(data.get("can_target_afflicted", True)),
                    can_target_virtued=bool(data.get("can_target_virtued", True)),
                    can_target_not_overstressed=bool(
                        data.get("can_target_not_overstressed", True)),
                    is_guarded=bool(data.get("is_guarded", True)),
                    is_guarding=bool(data.get("is_guarding", True)),
                    ally_base_class_id=str(data.get("ally_base_class_id", "")),
                ))

            skill_cooldowns = {}
            for cd in entry.get("skill_cooldowns", []):
                sk_id = cd.get("combat_skill_id")
                if sk_id:
                    skill_cooldowns[sk_id] = int(cd.get("amount", 0))

            bonus_initiative_desires = []
            for d in entry.get("bonus_initiative_desires", []):
                if d.get("type") not in MODELED_BONUS_INITIATIVE_TYPES:
                    continue
                data = d.get("data", {})
                bonus_initiative_desires.append(BonusInitiativeDesire(
                    type=d["type"],
                    is_round_start=bool(data.get("is_round_start", False)),
                    is_pre_turn=bool(data.get("is_pre_turn", False)),
                    is_round_finish=bool(data.get("is_round_finish", False)),
                    is_post_turn=bool(data.get("is_post_turn", False)),
                    combat_skill_id_override=str(data.get("combat_skill_id_override", "")),
                    ally_base_class_id=str(data.get("ally_base_class_id", "")),
                    ally_count_min=data.get("ally_count_min"),
                    ally_count_max=data.get("ally_count_max"),
                    monsters_min=data.get("monsters_min"),
                    monsters_max=data.get("monsters_max"),
                    monsters_size_limit=data.get("monsters_size_limit"),
                    last_combat_skill_id=str(data.get("last_combat_skill_id", "")),
                    health_ratio_threshold=data.get("health_ratio_threshold"),
                    is_under_threshold=bool(data.get("is_under_threshold", True)),
                    heroes_min=data.get("heroes_min"),
                    heroes_max=data.get("heroes_max"),
                ))

            brain_id = entry.get("id", "")
            if brain_id:
                brains[brain_id] = MonsterBrain(
                    id=brain_id, skill_desires=skill_desires, target_desires=target_desires,
                    skill_cooldowns=skill_cooldowns,
                    bonus_initiative_desires=bonus_initiative_desires,
                )
        return brains
