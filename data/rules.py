from __future__ import annotations
import json
from pathlib import Path
from .types import CombatRules, DarknessBand


class RulesLoader:
    """Load combat rules from the game's rules.json."""

    KEY_MAP = {
        "cutoffAlwaysMiss": "cutoff_always_miss",
        "cutoffAlwaysHit": "cutoff_always_hit",
        "excessCritMult": "excess_crit_mult",
        "excessCritMax": "excess_crit_max",
        "passed_turn_stress_dmg": "passed_turn_stress_dmg",
        "initiative_base": "initiative_base",
        "deaths_door_self_stress_chance": "deaths_door_self_stress_chance",
        "deaths_door_self_stress_dmg": "deaths_door_self_stress_dmg",
        "death_party_stress_chance": "death_party_stress_chance",
        "death_party_stress_damage": "death_party_stress_damage",
        "effect_crit_roll_mod": "effect_crit_roll_mod",
        "deaths_door_companion_stress_chance": "deaths_door_companion_stress_chance",
        "deaths_door_companion_stress_dmg": "deaths_door_companion_stress_dmg",
        "deathblow_chance": "deathblow_chance",
        "affliction_base_onset_chance": "affliction_base_onset_chance",
        "affliction_critical_success_chance": "affliction_critical_success_chance",
        "affliction_min_chance": "affliction_min_chance",
        "affliction_max_chance": "affliction_max_chance",
        "stress_affliction_resisted": "stress_affliction_resisted",
        "stress_affliction_critically_resisted": "stress_affliction_critically_resisted",
        "killing_blow_relief_chance": "killing_blow_relief_chance",
        "killing_blow_stress_relief": "killing_blow_stress_relief",
        "stall_enemy_size_threshold": "stall_enemy_size_threshold",
        "stall_round_stress_effect_threshold": "stall_round_stress_effect_threshold",
        "stall_round_summon_effect_threshold": "stall_round_summon_effect_threshold",
        "stall_party_size_threshold": "stall_party_size_threshold",
        "stall_number_of_invalidating_skills_threshold": "stall_number_of_invalidating_skills_threshold",
        # Unread until 2026-08-23. `stall_extra_round_size_*` is the SIZE half
        # of the enemy test -- a large monster counts for more than its head --
        # and the two `accelerated_only` increments are what the accelerated
        # path had been approximating with a hardcoded threshold.
        "stall_extra_round_size_threshold": "stall_extra_round_size_threshold",
        "stall_extra_round_size_amount": "stall_extra_round_size_amount",
        # The last of the twelve stall scalars to go unread. See
        # CombatEngine._maybe_reset_stall_counter for the declared reading.
        "stall_round_reset_threshold": "stall_round_reset_threshold",
        "stall_round_accelerated_only_increment": "stall_round_accelerated_only_increment",
        "stall_round_accelerated_only_consecutive_increment": "stall_round_accelerated_only_consecutive_increment",
        "consecutive_miss_reduction_acc_buff": "consecutive_miss_reduction_acc_buff",
        # A DoT advances while the party WALKS, at half rate per hallway tile.
        # Unread until 2026-09-02, and it is the evidence that a bleed reaches
        # the corridor at all -- a per-tile rate is only a meaningful number if
        # the DoT gets there. See ExpeditionSequenceEnv._walk_the_corridor.
        "dotHallwayStepRatio": "dot_hallway_step_ratio",
        "health_damage_blocks_max": "health_damage_blocks_max",
        # Camping. Real shipped values: 12 points per camp, resting costs 6,
        # a night ambush is a flat 33% before any reduce_ambush_chance effect,
        # and camping restores the torch to full. camp_relieve_stress is 0 in
        # every shipped rules.json — resting gives HP, not stress; stress
        # relief only ever comes from a camp skill.
        "camp_start_camping_points": "camp_start_camping_points",
        "camp_rest_point_threshold": "camp_rest_point_threshold",
        "camp_rest_number_of_hero_threshold": "camp_rest_number_of_hero_threshold",
        "camp_relieve_stress": "camp_relieve_stress",
        "camp_restore_torch": "camp_restore_torch",
        "ambush_camping_base_chance": "ambush_camping_base_chance",
        "provision_hp_heal": "provision_hp_heal",
        "max_provisions_before_full": "max_provisions_before_full",
        # The quest's quirk and disease rolls; see CombatRules.
        "negStress0Success": "neg_stress0_success",
        "posStress0Success": "pos_stress0_success",
        "negStress100Success": "neg_stress100_success",
        "posStress100Success": "pos_stress100_success",
        "negStress0Failure": "neg_stress0_failure",
        "posStress0Failure": "pos_stress0_failure",
        "negStress100Failure": "neg_stress100_failure",
        "posStress100Failure": "pos_stress100_failure",
        "disease_after_quest_min_resolve_level": "disease_after_quest_min_resolve_level",
        "disease_after_quest_min_chance": "disease_after_quest_min_chance",
        "disease_max_chance": "disease_max_chance",
        "disease_hero_disease_resist_weight": "disease_hero_disease_resist_weight",
        "quirks_max_positive": "quirks_max_positive",
        "quirks_max_negative": "quirks_max_negative",
        "quirks_max_diseases": "quirks_max_diseases",
        "combat_retreat_stress": "combat_retreat_stress",
        "combat_retreat_chance": "combat_retreat_chance",
        "combat_retreat_bonus_chance_per_attempt":
            "combat_retreat_bonus_chance_per_attempt",
        # Surprise. Corridor and room ship IDENTICAL base chances (0.1 both
        # ways), so this simulator not modelling which one an encounter is
        # costs nothing -- the pair only diverges once a room is scouted, and
        # scouting is out of scope. `surprise_ambush_party_base_chance` and
        # `surprise_ambush_monsters_base_chance` are deliberately NOT here:
        # they are in rules.json and absent from the binary.
        "surprise_corridor_party_base_chance": "surprise_corridor_party_base_chance",
        "surprise_corridor_monsters_base_chance": "surprise_corridor_monsters_base_chance",
        "surprise_room_party_base_chance": "surprise_room_party_base_chance",
        "surprise_room_monsters_base_chance": "surprise_room_monsters_base_chance",
        "surprise_known_corridor_party_base_chance":
            "surprise_known_corridor_party_base_chance",
        "surprise_known_corridor_monsters_base_chance":
            "surprise_known_corridor_monsters_base_chance",
        "surprise_known_room_party_base_chance":
            "surprise_known_room_party_base_chance",
        "surprise_known_room_monsters_base_chance":
            "surprise_known_room_monsters_base_chance",
        "surprise_max_party_surprised_chance": "surprise_max_party_surprised_chance",
        "surprise_max_monsters_surprised_chance": "surprise_max_monsters_surprised_chance",
    }

    @staticmethod
    def load(path: str | Path) -> CombatRules:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        rules = CombatRules()

        if "player_crit_multiplier_table" in raw:
            table = raw["player_crit_multiplier_table"]
            if table:
                rules.player_crit_multiplier = float(table[0].get("value", 1.5))
        if "monster_crit_multiplier_table" in raw:
            table = raw["monster_crit_multiplier_table"]
            if table:
                rules.monster_crit_multiplier = float(table[0].get("value", 1.5))

        for json_key, attr_name in RulesLoader.KEY_MAP.items():
            if json_key in raw:
                setattr(rules, attr_name, float(raw[json_key]))

        # The camp food phase. Sorted richest-first so a caller can take the
        # best it can afford by walking the tuple, and kept as data rather
        # than four constants because every number in it is shipped.
        meals = raw.get("meals_table")
        if isinstance(meals, list) and meals:
            rules.meals_table = tuple(sorted(
                ((str(m.get("type", "")), float(m.get("rations_per", 0.0)),
                  float(m.get("healing", 0.0)), float(m.get("stress", 0.0)))
                 for m in meals),
                key=lambda row: -row[1]))

        # **A LIST, so it cannot ride the float-only key map above.**
        # `effectiveDifficultyAfflictionOnsetModifiers` is indexed by the
        # hero's LEVEL SHORTFALL against the quest, 0..6, and decodes exactly
        # to the wiki's rule: -5% Virtue Chance per level of difference, and
        # -33% at a difference of six.
        modifiers = raw.get("effectiveDifficultyAfflictionOnsetModifiers")
        if isinstance(modifiers, list) and modifiers:
            rules.difficulty_affliction_onset_modifiers = tuple(
                float(v) for v in modifiers)

        if "stun_ends_turn" in raw:
            rules.stun_ends_turn = bool(raw["stun_ends_turn"])
        if "stun_recovery_buffs" in raw and raw["stun_recovery_buffs"]:
            rules.stun_recovery_buff_id = str(raw["stun_recovery_buffs"][0])

        if "darkness" in raw and "range_table" in raw["darkness"]:
            bands = []
            for entry in raw["darkness"]["range_table"]:
                r, v = entry["range"], entry["value"]
                bands.append(DarknessBand(
                    lower=float(r["lower"]),
                    upper=float(r["upper"]),
                    stress_chance_increase=float(v.get("stress_chance_increase", 0)),
                    stress_damage_increase=float(v.get("stress_damage_increase", 0)),
                    monster_attack_increase=float(v.get("monster_attack_increase", 0)),
                    monster_damage_increase=float(v.get("monster_damage_increase", 0)),
                    monster_crit_increase=float(v.get("monster_crit_increase", 0)),
                    player_crit_increase=float(v.get("player_crit_increase", 0)),
                    player_def_increase=float(v.get("player_def_increase", 0)),
                    loot_increase_gold=float(v.get("loot_increase_gold", 0)),
                    loot_increase_treasure_draws=float(
                        v.get("loot_increase_treasure_draws", 0)),
                    player_scouting_increase=float(v.get("player_scouting_increase", 0)),
                    monsters_surprised_increase=float(
                        v.get("monsters_surprised_increase", 0)),
                    heroes_surprised_increase=float(v.get("heroes_surprised_increase", 0)),
                ))
            rules.darkness_table = bands

        if "monster_crit" in raw:
            mc = raw["monster_crit"]
            rules.monster_crit_stress_chance = float(mc.get("stress_chance", 1.0))
            rules.monster_crit_stress_damage = float(mc.get("stress_damage", 10.0))
            rules.monster_crit_companion_stress_chance = float(mc.get("companion_stress_chance", 0.5))
            rules.monster_crit_companion_stress_damage = float(mc.get("companion_stress_damage", 5.0))
        if "player_crit" in raw:
            pc = raw["player_crit"]
            rules.player_crit_relief_chance = float(pc.get("relief_chance", 1.0))
            rules.player_crit_stress_relief = float(pc.get("stress_relief", 3.0))
            rules.player_crit_companion_relief_chance = float(pc.get("companion_relief_chance", 0.25))
            rules.player_crit_companion_stress_relief = float(pc.get("companion_stress_relief", 3.0))

        return rules
