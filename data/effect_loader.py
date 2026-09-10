from __future__ import annotations
from pathlib import Path
from .darkest_parser import DarkestParser
from .types import EffectDefinition


class EffectLoader:
    """Load effect templates from base.effects.darkest.

    The .darkest format uses fields like:
      .dotBleed N   .dotPoison N   .duration N
      .stun 1       .push N        .pull N
      .kill 1       .heal N
      .combat_stat_buff 1  .damage_low_multiply X%  .damage_high_multiply X%
      .damage_low_add X%  .damage_high_add X%
      .attack_rating_add X% .crit_chance_add X%
      .protection_rating_add X% .defense_rating_add X%
      .speed_add N   .max_hp_percent X%
      .stress N (damage)  .healstress N (heal)  .dotStress N (Horror, stress DoT)
      .heal_percent X (percent-of-max-HP heal)  .dotHpHeal N (heal-over-time)
      .cure 1 (removes bleed+blight)  .cure_burn 1 (burn only — NOT .cure)
      .dotBurn N (burn STACKS, no duration)  .dot_irresistible (skip the resist roll)
      .keyStatus "tagged"   .removeStatus "tagged"
      .tag 1  (the real Marked/Vulnerable-application flag, on .target
      "target" effects; the same field is reused by several unrelated
      .target "performer" self-buffs -- see EffectDefinition.is_mark_effect)
      .buff_duration_type combat_end  (overrides .duration entirely -- the
      buff lasts the rest of combat, see EffectDefinition.buff_duration_type)
      .monsterType "man"  (conditional damage bonus vs a target's permanent
      monster-type tag, see EffectDefinition.monster_type_condition)
      .buff_type hp_heal_received_percent  .buff_amount 20%  (a third inline
      buff mechanism -- see EffectDefinition.inline_buff_type/inline_buff_amount)
      .apply_once true  .queue true
      .guard 1  .clearguarding 1  .clearguarded 1  .swap_source_and_target true
      .riposte 1  .riposte_on_hit_chance_add X%  .riposte_on_miss_chance_add X%
      .riposte_effect "name"  .clear_riposte 1
      .stealth 1  .unstealth 1
      .buff_ids "X" "Y" (or bare .buff_ids X)  .duration N
    """

    @staticmethod
    def load(path: str | Path) -> dict[str, EffectDefinition]:
        records = DarkestParser.parse_file(path)
        effects: dict[str, EffectDefinition] = {}
        for rec in records:
            if rec.get("_type") != "effect":
                continue
            name = rec.get("name", "")
            if not name:
                continue
            e = EffectDefinition(
                name=name,
                target=rec.get("target", "target"),
                chance=float(rec.get("chance", 1.0)),
                on_hit=rec.get("on_hit", True),
                on_miss=rec.get("on_miss", False),
                is_apply_once=rec.get("apply_once", False),
                is_queued=rec.get("queue", False),
                can_apply_on_death=rec.get("can_apply_on_death", False),
            )
            if "dotBleed" in rec:
                e.bleed_amount = int(rec["dotBleed"])
                e.bleed_rounds = int(rec.get("duration", 3))
            if "dotBurn" in rec:
                # No `.duration` is read here and that is not an omission:
                # burn is a stack count that halves, so the amount IS the
                # whole story. Every shipped burn effect pairs it with
                # .dot_irresistible.
                e.burn_amount = int(rec["dotBurn"])
            if "controlled_burn_amount" in rec:
                # A burn attached to the RANK rather than to the monster in it.
                # Five effects, all the Runaway's Controlled Burn, and the
                # duration is real here (unlike `dotBurn`, which decays instead
                # of expiring) because it is the ZONE that expires.
                e.controlled_burn_amount = int(rec["controlled_burn_amount"])
                e.controlled_burn_duration = int(
                    rec.get("controlled_burn_duration", 3))
            if "dot_irresistible" in rec:
                e.dot_irresistible = bool(rec["dot_irresistible"])
            if "dotPoison" in rec:
                e.blight_amount = int(rec["dotPoison"])
                e.blight_rounds = int(rec.get("duration", 3))
            if "stun" in rec:
                e.stun_chance = float(rec["stun"]) if isinstance(rec["stun"], (int, float)) else 1.0
            # PRESENCE, not truthiness. Both ship as VALUELESS flags --
            # `.shuffleparty  .chance 100%` -- so the parser hands back the
            # empty string and `bool("")` is False. Same trap as `.effect ""`
            # parsing to `['']`: in this format an empty value means the flag
            # is set, not that it is off. An explicit `false` is still honoured
            # in case a mod writes one.
            def _flag(key: str) -> bool:
                if key not in rec:
                    return False
                v = rec[key]
                return not (isinstance(v, str) and v.strip().lower() == "false")

            e.shuffle_target = _flag("shuffletarget")
            e.shuffle_party = _flag("shuffleparty")
            # --- 2026-08-23 audit, see EffectDefinition. `.untag`/`.unstun`
            # ship as valueless flags like the two above, so PRESENCE, never
            # truthiness.
            e.clears_mark = _flag("untag")
            e.clears_stun = _flag("unstun")
            e.grants_bonus_action = _flag("bonus_action_next_turn")
            e.requires_kill_target = _flag("requires_kill_target")
            if "torch_increase" in rec:
                e.torch_change = int(str(rec["torch_increase"]).split()[0])
            if "torch_decrease" in rec:
                # Shipped as a POSITIVE magnitude under a negative-sense key
                # (`Darkness 3 .torch_decrease 25`), so the sign is ours.
                e.torch_change = -int(str(rec["torch_decrease"]).split()[0])
            if "steal_buff_stat_type" in rec:
                v = rec["steal_buff_stat_type"]
                e.steal_dot_stat_type = v[0] if isinstance(v, list) else str(v)
            if "push" in rec:
                e.push_distance = int(rec["push"])
            if "pull" in rec:
                e.pull_distance = int(rec["pull"])
            if "kill" in rec:
                e.is_kill_effect = True
            if "heal" in rec:
                e.heal_min = int(rec.get("heal_min", rec["heal"]))
                e.heal_max = int(rec.get("heal_max", rec["heal"]))
            # Percent-of-max-HP heal (Flagellant's Redeem/death's-door-enter,
            # Color of Madness curio heals) -- distinct from the flat .heal
            # above, never appears alongside it on the same effect.
            if "heal_percent" in rec:
                e.heal_percent = float(rec["heal_percent"])
            # Heal-over-time / regeneration (Flagellant's Reclaim) -- same
            # .duration convention as dotBleed/dotPoison/dotStress above.
            if "dotHpHeal" in rec:
                e.heal_dot_amount = int(rec["dotHpHeal"])
                e.heal_dot_rounds = int(rec.get("duration", 3))
            # Real game files use .stress N (damage) and .healstress N (heal),
            # not .stress_damage / .stress_heal — verified against
            # base.effects.darkest (those keys never appear there).
            if "stress" in rec:
                e.stress_damage = int(rec["stress"])
            if "healstress" in rec:
                e.stress_heal = int(rec["healstress"])
            if "cure" in rec:
                e.cures_dots = bool(rec["cure"])
            if "cure_bleed" in rec:
                e.cures_bleed = bool(rec["cure_bleed"])
            if "cure_poison" in rec:
                e.cures_blight = bool(rec["cure_poison"])
            if "cure_burn" in rec:
                e.cures_burn = bool(rec["cure_burn"])
            if "clearDotStress" in rec:
                e.clears_stress_dot = bool(rec["clearDotStress"])
            if "summon_count" in rec:
                e.summon_count = int(rec["summon_count"])
            if "summon_monsters" in rec:
                raw = rec["summon_monsters"]
                e.summon_monster_ids = raw if isinstance(raw, list) else str(raw).split()
            if "summon_erase_data_on_roll" in rec:
                e.summon_erase_on_roll = bool(rec["summon_erase_data_on_roll"])
            if "summon_ranks" in rec:
                raw = rec["summon_ranks"]
                parts = raw if isinstance(raw, list) else str(raw).split()
                e.summon_ranks = [int(x) for x in parts if str(x).lstrip("-").isdigit()]
            if "summon_limits" in rec:
                raw = rec["summon_limits"]
                parts = raw if isinstance(raw, list) else str(raw).split()
                e.summon_limits = [int(x) for x in parts if str(x).lstrip("-").isdigit()]
            if "summon_chances" in rec:
                raw = rec["summon_chances"]
                parts = raw if isinstance(raw, list) else str(raw).split()
                e.summon_chances = [float(x) for x in parts]
            if "combat_stat_buff" in rec and bool(rec["combat_stat_buff"]):
                e.is_stat_buff = True
                e.buff_stat_type = "combat_stat_buff"
            # These stat-modifier fields aren't exclusive to combat_stat_buff
            # effects — e.g. "MAA Riposte 1" carries damage_low_multiply /
            # damage_high_multiply / crit_chance_add for its counter-attack
            # profile without a .combat_stat_buff flag. Parse unconditionally.
            if "damage_low_multiply" in rec:
                e.damage_low_multiply = float(rec.get("damage_low_multiply", 0))
            if "damage_high_multiply" in rec:
                e.damage_high_multiply = float(rec.get("damage_high_multiply", 0))
            if "damage_low_add" in rec:
                e.damage_low_add = float(rec.get("damage_low_add", 0))
            if "damage_high_add" in rec:
                e.damage_high_add = float(rec.get("damage_high_add", 0))
            if "attack_rating_add" in rec:
                e.attack_rating_add = float(rec.get("attack_rating_add", 0))
            if "crit_chance_add" in rec:
                e.crit_chance_add = float(rec.get("crit_chance_add", 0))
            if "protection_rating_add" in rec:
                e.protection_rating_add = float(rec.get("protection_rating_add", 0))
            if "defense_rating_add" in rec:
                e.defense_rating_add = float(rec.get("defense_rating_add", 0))
            # Real key is .speed_rating_add (97 occurrences); bare .speed_add
            # never appears in the real file — same class of naming
            # mismatch as .speed/.speed_rating on BuffDefinition.
            if "speed_rating_add" in rec:
                e.speed_add = int(rec.get("speed_rating_add", 0))
            elif "speed_add" in rec:
                e.speed_add = int(rec.get("speed_add", 0))
            if "max_hp_percent" in rec:
                # No .max_hp_percent (or any .max_hp* key) found anywhere in
                # base.effects.darkest — kept for forward-compat in case a
                # DLC/mode effects file uses it, but unverified against real
                # data; max_hp buffs observed so far all go through
                # .buff_ids referencing a BuffDefinition instead.
                e.max_hp_percent = float(rec.get("max_hp_percent", 0))
            if "set_mode" in rec:
                e.set_mode = str(rec["set_mode"])
            if rec.get("kill_enemy_types") == "corpse":
                e.is_clear_corpses = True
            # Horror (stress DoT) — real key is .dotStress (e.g. "Madman
            # Horror 1": ".dotStress 3 .duration 4"), not stress_damage_
            # per_round (never appears in any real file — see
            # EffectDefinition.stress_damage_per_round for the full story).
            if "dotStress" in rec:
                e.stress_damage_per_round = int(rec["dotStress"])
                e.stress_dot_rounds = int(rec.get("duration", 3))
            if "keyStatus" in rec:
                e.key_status = rec.get("keyStatus", "")
            if "removeStatus" in rec:
                e.remove_status = rec.get("removeStatus", "")
            if "tag" in rec:
                e.is_mark_effect = bool(int(rec.get("tag", 0) or 0))
            if "monsterType" in rec:
                e.monster_type_condition = rec.get("monsterType", "")
            if "health_damage_blocks" in rec:
                e.health_damage_blocks = int(rec["health_damage_blocks"])
            if "guard" in rec:
                e.is_guard = bool(rec["guard"])
            if "clearguarding" in rec:
                e.is_clear_guarding = bool(rec["clearguarding"])
            if "clearguarded" in rec:
                e.is_clear_guarded = bool(rec["clearguarded"])
            if "swap_source_and_target" in rec:
                e.swap_source_and_target = bool(rec["swap_source_and_target"])
            if "riposte" in rec:
                e.is_riposte = bool(rec["riposte"])
                e.riposte_duration = int(rec.get("duration", 2))
                if "riposte_on_hit_chance_add" in rec:
                    e.riposte_on_hit_chance_add = float(rec["riposte_on_hit_chance_add"])
                if "riposte_on_miss_chance_add" in rec:
                    e.riposte_on_miss_chance_add = float(rec["riposte_on_miss_chance_add"])
                if "riposte_effect" in rec:
                    e.riposte_effect = str(rec["riposte_effect"])
            if "clear_riposte" in rec:
                e.is_clear_riposte = bool(rec["clear_riposte"])
            if "stealth" in rec:
                e.stealth_duration = int(rec.get("duration", 1))
            if "unstealth" in rec:
                e.is_unstealth = bool(rec["unstealth"])
            if "buff_ids" in rec:
                # A quoted multi-value list (.buff_ids "X" "Y") already
                # parses to a real list. But several real effects use an
                # UNQUOTED bareword multi-value list instead (e.g. holy_water:
                # ".buff_ids holy_water_blight_resist holy_water_bleed_resist
                # holy_water_disease_resist holy_water_debuff_resist"), which
                # the parser returns as one space-joined string, not a list
                # — wrapping it as [raw_ids] treated the whole string as a
                # single (nonexistent) buff id, silently no-op'ing every
                # such effect (also affects leper_*, medicinal_herbs_*,
                # rake_buff_*l/*h). str(raw_ids).split() is a no-op for the
                # already-common single-bareword case (e.g.
                # "stealthed_crit_chance_buff" -> ["stealthed_crit_chance_buff"]).
                raw_ids = rec["buff_ids"]
                e.buff_ids = raw_ids if isinstance(raw_ids, list) else str(raw_ids).split()
            # Generic duration, used for buff_ids reversal AND inline
            # combat_stat_buff reversal (e.g. Abomination's beast_buff_N) —
            # not gated to buff_ids specifically, any effect can carry it.
            if "duration" in rec:
                e.duration = int(rec["duration"])
            if "buff_duration_type" in rec:
                e.buff_duration_type = rec.get("buff_duration_type", "rounds")
            if "buff_type" in rec:
                e.inline_buff_type = rec.get("buff_type", "")
                e.inline_buff_amount = float(rec.get("buff_amount", 0.0))
            effects[name] = e
        return effects
