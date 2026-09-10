from __future__ import annotations
import json
from pathlib import Path
from typing import Any
from .types import BuffDefinition, TrinketDefinition, QuirkDefinition


class BuffLoader:
    """Load buffs, trinkets, and quirks from game JSON files."""

    @staticmethod
    def load_buffs(path: str | Path) -> dict[str, BuffDefinition]:
        result: dict[str, BuffDefinition] = {}
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        for entry in data.get("buffs", []):
            rule_data = entry.get("rule_data") or {}
            buf = BuffDefinition(
                id=entry.get("id", ""),
                stat_type=entry.get("stat_type", ""),
                stat_sub_type=entry.get("stat_sub_type", ""),
                amount=float(entry.get("amount", 0)),
                duration=entry.get("duration"),
                duration_type=entry.get("duration_type", "rounds"),
                rule_type=entry.get("rule_type", "always"),
                is_false_rule=entry.get("is_false_rule", False),
                remove_on_battle_complete=entry.get("remove_on_battle_complete", False),
                remove_if_not_active=entry.get("remove_if_not_active", False),
                rule_data_float=float(rule_data.get("float", 0) or 0),
                rule_data_string=rule_data.get("string", "") or "",
            )
            result[buf.id] = buf
        return result

    @staticmethod
    def load_trinkets(path: str | Path) -> dict[str, TrinketDefinition]:
        result: dict[str, TrinketDefinition] = {}
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        for entry in data.get("entries", []):
            trinket = TrinketDefinition(
                id=entry.get("id", ""),
                name=entry.get("id", ""),
                rarity=entry.get("rarity", ""),
                class_requirements=entry.get("hero_class_requirements", []),
                buffs=entry.get("buffs", []),
                limit=int(entry.get("limit", 1)),
                set_id=str(entry.get("set_id", "") or ""),
                attack_effects=list(entry.get("attack_skill_additional_effects", []) or []),
            )
            result[trinket.id] = trinket
        return result

    @staticmethod
    def load_trinket_sets(path: str | Path) -> dict[str, list[str]]:
        """`*.sets.trinkets.json` -> {set_id: [buff_id, ...]}.

        Four files ship one (Crimson Court, Shieldbreaker, Duelist, Runaway)
        and the base game ships none, so this is DLC-only content -- but the
        Crimson Court sets are equipped by real curated comps, which is what
        makes it worth reading. Shape is `{"sets": [{"id": ..., "buffs":
        [...]}]}`.
        """
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        out: dict[str, list[str]] = {}
        for entry in data.get("sets", []):
            sid = entry.get("id")
            if sid:
                out[str(sid)] = list(entry.get("buffs", []))
        return out

    @staticmethod
    def load_quirks(path: str | Path) -> dict[str, QuirkDefinition]:
        result: dict[str, QuirkDefinition] = {}
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        for entry in data.get("quirks", []):
            quirk = QuirkDefinition(
                id=entry.get("id", ""),
                name=entry.get("id", ""),
                is_positive=entry.get("is_positive", True),
                is_disease=entry.get("is_disease", False),
                classification=entry.get("classification", ""),
                buffs=entry.get("buffs", []),
                incompatible_quirks=entry.get("incompatible_quirks", []),
            )
            result[quirk.id] = quirk
        return result
