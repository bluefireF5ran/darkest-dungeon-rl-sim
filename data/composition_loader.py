"""Load team compositions from JSON files.

Format:
{
    "teamName": "African Vapors",
    "location": "The Ruins",
    "heroes": [
        {
            "heroClass": "Shieldbreaker",
            "activeSkills": ["Pierce", "Puncture", "Expose", "Serpent Sway"],
            "activeCampSkills": ["Encourage", "Wound Care", "Snake Skin", "Sandstorm"],
            "trinket1": "Ancestor's Pen",
            "trinket2": "Surgical Gloves",
            "quirks": {"positive": ["tough"], "negative": ["fragile"]},
            "lockedQuirks": {"positive": [], "negative": []}
        }
    ]
}
"""

from __future__ import annotations
import json
from pathlib import Path
from typing import Any, Optional
from dataclasses import dataclass, field

from .types import DataStore, HeroDefinition, EnemyDefinition, SkillDefinition
from .data_loader import DataLoader


@dataclass
class TeamHeroSlot:
    hero_class: str
    active_skills: list[str]
    active_camp_skills: list[str]
    trinkets: list[str]
    quirks_positive: list[str]
    quirks_negative: list[str]
    locked_positive: list[str]
    locked_negative: list[str]


@dataclass
class TeamComposition:
    team_name: str
    location: str
    heroes: list[TeamHeroSlot]


@dataclass
class CombatSetup:
    hero_defs: list[HeroDefinition]
    hero_trinkets: list[list[str]]
    hero_quirks: list[list[str]]
    enemy_defs: list[EnemyDefinition]


class CompositionLoader:
    """Load team compositions from JSON and validate them against game data."""

    HERO_CLASS_MAP = {
        "Crusader": "crusader",
        "Vestal": "vestal",
        "Highwayman": "highwayman",
        "Hellion": "hellion",
        "Jester": "jester",
        "Leper": "leper",
        "Plague Doctor": "plague_doctor",
        "Occultist": "occultist",
        "Bounty Hunter": "bounty_hunter",
        "Man-at-Arms": "man_at_arms",
        "Grave Robber": "grave_robber",
        "Arbalest": "arbalest",
        "Musketeer": "musketeer",
        "Houndmaster": "houndmaster",
        "Flagellant": "flagellant",
        "Abomination": "abomination",
        "Antiquarian": "antiquarian",
        "Shieldbreaker": "shieldbreaker",
        # The Fire's Edge (2026-08-20). Absent here, a comp fielding one of
        # them loses that slot SILENTLY and is evaluated as a smaller party --
        # the exact bug `modded_hero_comp_names` was added to catch for
        # fan-made classes, except these two really do load.
        "Runaway": "runaway",
        "Duelist": "duelist",
    }

    def __init__(self, store: DataStore):
        self.store = store

    def load_composition(self, path: str | Path) -> TeamComposition:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return self._parse(data)

    def parse_dict(self, data: dict) -> TeamComposition:
        return self._parse(data)

    def _parse(self, data: dict) -> TeamComposition:
        team_name = data.get("teamName", "Unnamed Team")
        location = data.get("location", "")
        hero_slots: list[TeamHeroSlot] = []
        for hdata in data.get("heroes", []):
            hero_class = hdata.get("heroClass", "")
            hero_id = self.HERO_CLASS_MAP.get(hero_class)
            if hero_id is None:
                raise ValueError(f"Unknown hero class: '{hero_class}'")

            hd = self.store.heroes.get(hero_id)
            if hd is None:
                raise ValueError(f"Hero '{hero_class}' ({hero_id}) not in data store")

            valid_skill_ids = {s.id for s in hd.skills}
            active_skills = hdata.get("activeSkills", [])
            for sk_name in active_skills:
                sk_id = self._skill_name_to_id(hero_id, sk_name)
                if sk_id not in valid_skill_ids:
                    raise ValueError(
                        f"Skill '{sk_name}' not valid for {hero_class}. "
                        f"Valid: {sorted(valid_skill_ids)}"
                    )

            trinket1 = hdata.get("trinket1", "")
            trinket2 = hdata.get("trinket2", "")
            trinkets = []
            for tname in [trinket1, trinket2]:
                if tname:
                    tid = self._trinket_name_to_id(tname)
                    if tid and tid not in self.store.trinkets:
                        print(f"  Warning: trinket '{tname}' ({tid}) not found in trinket data")
                    trinkets.append(tid or tname)

            qdata = hdata.get("quirks", {})
            quirks = qdata.get("positive", []) + qdata.get("negative", [])
            lqdata = hdata.get("lockedQuirks", {})
            locked = lqdata.get("positive", []) + lqdata.get("negative", [])

            slot = TeamHeroSlot(
                hero_class=hero_class,
                active_skills=active_skills,
                active_camp_skills=hdata.get("activeCampSkills", []),
                trinkets=trinkets,
                quirks_positive=qdata.get("positive", []),
                quirks_negative=qdata.get("negative", []),
                locked_positive=lqdata.get("positive", []),
                locked_negative=lqdata.get("negative", []),
            )
            hero_slots.append(slot)
        return TeamComposition(team_name=team_name, location=location, heroes=hero_slots)

    def to_combat_setup(self, team: TeamComposition) -> CombatSetup:
        hero_defs: list[HeroDefinition] = []
        hero_trinkets: list[list[str]] = []
        hero_quirks: list[list[str]] = []

        for slot in team.heroes:
            hero_id = self.HERO_CLASS_MAP[slot.hero_class]
            hd = self.store.heroes[hero_id]
            hero_defs.append(hd)

            all_quirk_ids = slot.quirks_positive + slot.quirks_negative + \
                            slot.locked_positive + slot.locked_negative
            hero_trinkets.append(slot.trinkets)
            hero_quirks.append(all_quirk_ids)

        return CombatSetup(
            hero_defs=hero_defs,
            hero_trinkets=hero_trinkets,
            hero_quirks=hero_quirks,
            enemy_defs=[],
        )

    def add_enemies(self, setup: CombatSetup,
                    enemy_ids: list[str]) -> CombatSetup:
        enemy_defs = []
        for eid in enemy_ids:
            ed = self.store.enemies.get(eid)
            if ed:
                enemy_defs.append(ed)
            else:
                print(f"  Warning: enemy '{eid}' not found")
        setup.enemy_defs = enemy_defs
        return setup

    def find_enemy_variant(self, base_name: str, variant: str = "A") -> str | None:
        for eid in self.store.enemies:
            if eid.startswith(base_name) and eid.endswith(f"_{variant}"):
                return eid
        return None

    def _skill_name_to_id(self, hero_id: str, skill_name: str) -> str:
        """Convert a display skill name to internal ID."""
        id_map = {
            "crusader": {
                "Smite": "smite", "Zealous Accusation": "zealous_accusation",
                "Stunning Blow": "stunning_blow", "Bulwark of Faith": "bulwark_of_faith",
                "Battle Heal": "battle_heal", "Holy Lance": "holy_lance",
                "Inspiring Cry": "inspiring_cry",
            },
            "vestal": {
                "Mace Bash": "mace_bash", "Judgement": "judgement",
                "Dazzling Light": "dazzling_light", "Divine Grace": "divine_grace",
                "Divine Comfort": "gods_comfort", "Illumination": "gods_illumination",
            },
            "highwayman": {
                "Wicked Slice": "wicked_slice", "Pistol Shot": "pistol_shot",
                "Point Blank Shot": "point_blank_shot",
                "Grapeshot Blast": "grape_shot_blast",
                "Tracking Shot": "take_aim", "Duelist's Advance": "duelist_advance",
                "Open Vein": "opened_vein",
            },
            "shieldbreaker": {
                "Pierce": "pierce", "Puncture": "puncture",
                "Adder's Kiss": "adders_kiss", "Impale": "impale",
                "Expose": "expose", "Captivate": "captivate",
                "Serpent Sway": "serpent_sway",
            },
        }
        hero_map = id_map.get(hero_id, {})
        if skill_name in hero_map:
            return hero_map[skill_name]
        return skill_name.lower().replace(" ", "_").replace("'", "").replace("-", "_")

    def _trinket_name_to_id(self, name: str) -> str | None:
        """Convert trinket display name to internal ID by fuzzy matching."""
        tid = name.lower().replace(" ", "_").replace("'", "").replace("-", "_")
        if tid in self.store.trinkets:
            return tid
        for t in self.store.trinkets:
            if name.lower() in t.replace("_", " "):
                return t
        return None
