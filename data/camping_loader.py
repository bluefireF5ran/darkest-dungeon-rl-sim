"""Loader for the real camping skills, and the firewood each raid length grants.

Five files, not one: `raid/camping/default.camping_skills.json` holds the 64
base skills, and every DLC ships its own under `dlc/**/raid/camping/`. Reading
only the base file left **four classes with no camping skills whatsoever** —
Shieldbreaker, Flagellant, Duelist, Runaway — because a DLC class is granted the
shared skills by its DLC re-declaring them, never by the base file naming a
class that did not exist when it was written.

`CampingSkillDefinition`/`CampingSkillEffect` have existed in `data/types.py`
since the loadout work, and `CompLoadoutLoader` already resolves each comp
hero's `activeCampSkills` into `HeroLoadout.camp_skill_ids` — but nothing ever
loaded the definitions, so camping was inert. Camping was listed as
deliberately out of scope in CLAUDE.md and ASSUMPTIONS.md; it stopped being
defensible once the eval showed the whole remaining gap sitting in the long
band (37.2% SUCCESS, 45.5% wipes, mean stress 117.5 against affliction at 100)
while the short band, which grants no firewood at all, sat at 80.7%. See
CHANGELOG.md.

**Firewood per length is user-confirmed, not inferred**: 0 on short, 1 on
medium, 2 on long. `provision.json`'s `raid_starting_length_inventory_item_lists`
holds firewood 0/0/1/2/4/0 across indices 0-5, which pins indices 1/2/3 to
short/medium/long; index 4's 4 firewood is the Darkest Dungeon final quests,
which this simulator does not model.
"""

from __future__ import annotations

import json
from pathlib import Path

from .types import CampingSkillDefinition, CampingSkillEffect

# Confirmed by the player against the real game, and consistent with
# provision.json's length-indexed starting inventory (see module docstring).
# Short granting zero is load-bearing for interpreting any retrain: the short
# band CANNOT move because of camping, so if it does, something else changed.
FIREWOOD_PER_BAND: dict[str, int] = {"short": 0, "medium": 1, "long": 2}


class CampingLoader:
    RELATIVE_PATH = Path("raid") / "camping" / "default.camping_skills.json"
    DLC_GLOB = "dlc/**/raid/camping/*.camping_skills.json"

    @staticmethod
    def _paths(game_dir: Path) -> list[Path]:
        """Base first, then every DLC camping file. Order matters: the first
        file to define a skill id owns its cost, use_limit and effects."""
        paths = []
        base = game_dir / CampingLoader.RELATIVE_PATH
        if base.exists():
            paths.append(base)
        paths.extend(sorted(p for p in game_dir.glob(CampingLoader.DLC_GLOB)
                            if p != base))
        # Third-party classes last, so a mod re-declaring a base skill cannot
        # reprice it -- first file to define an id owns its cost and effects.
        # Its `hero_classes` still UNIONS, which is exactly how a mod grants
        # its own class the shared camping skills, the same way the
        # Shieldbreaker DLC does. Without this a modded hero cannot camp at
        # all: `hero_classes` is strict membership and no shipped file names it.
        from .mod_roots import mod_files
        paths.extend(mod_files("**/raid/camping/*.camping_skills.json"))
        return paths

    @staticmethod
    def _effect(raw: dict) -> CampingSkillEffect | None:
        stat_type = raw.get("type")
        if not stat_type:
            return None
        chance = raw.get("chance") or {}
        return CampingSkillEffect(
            target=str(raw.get("selection") or "self"),
            amount=float(raw.get("amount") or 0.0),
            stat_type=str(stat_type),
            stat_sub_type=str(raw.get("sub_type") or ""),
            duration=None,
            requirements=[str(r) for r in (raw.get("requirements") or [])],
            # `chance` is an object {code, amount}; `code` is only an effect
            # slot label (a/b/c...), the probability is `amount`.
            chance=float(chance.get("amount", 1.0)) if isinstance(chance, dict) else 1.0,
        )

    @staticmethod
    def load_all(game_dir: str | Path) -> dict[str, CampingSkillDefinition]:
        """Every camping skill, merged across the base file and all DLC.

        **`hero_classes` is UNIONED; everything else is base-wins.** That split
        is the data's own, not a convenience:

        * `dlc/702540_shieldbreaker/raid/camping/default.camping_skills.json` is
          a whole second copy of all 64 base skills whose only difference is
          `shieldbreaker` prepended to each `hero_classes`. Base-wins on the
          list would drop the one thing the file exists to say.
        * `dlc/4964110_fires_edge/.../duelist.camping_skills.json` re-declares
          `encourage` with `hero_classes: ["duelist"]` — sixteen classes short.
          Last-wins on the list would revoke Encourage from the entire base
          roster, so the DLC cannot be REPLACING the entry either. A union is
          the only reading both files are consistent with.

        Costs and effects stay base-wins, the same rule the rest of the DLC
        merge uses, so a DLC re-declaration cannot silently reprice a base
        skill.
        """
        game_dir = Path(game_dir)
        out: dict[str, CampingSkillDefinition] = {}
        for path in CampingLoader._paths(game_dir):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except (OSError, json.JSONDecodeError):
                continue
            for raw in data.get("skills", []):
                skill_id = raw.get("id")
                if not skill_id:
                    continue
                skill_id = str(skill_id)
                classes = [str(c) for c in (raw.get("hero_classes") or [])]
                existing = out.get(skill_id)
                if existing is not None:
                    # The file carries one record per upgrade level, so this is
                    # also what keeps a re-parse stable within a single file.
                    known = set(existing.hero_classes)
                    existing.hero_classes.extend(c for c in classes if c not in known
                                                 and not known.add(c))
                    continue
                effects = [e for e in (CampingLoader._effect(r) for r in raw.get("effects", []))
                           if e is not None]
                out[skill_id] = CampingSkillDefinition(
                    id=skill_id,
                    cost=int(raw.get("cost") or 0),
                    use_limit=int(raw.get("use_limit") or 0),
                    hero_classes=classes,
                    effects=effects,
                )
        return out

    @staticmethod
    def firewood_for_band(band: str) -> int:
        return FIREWOOD_PER_BAND.get(band, 0)
