from __future__ import annotations
import re
from pathlib import Path
from .types import CompositionDefinition
from .csv_loader import CsvLoader, csv_root


class CompsCsvLoader:
    """Load COMPS/Comps <hash>.csv — community-curated hero party comps,
    each tagged with the dungeon zone (Location) it's designed for. Metadata
    only (hero class names + zone), same as every other CSV in this project
    — no skills/trinkets/quirks. For those, see
    data/comp_loadout_loader.py (CompLoadoutLoader), which parses a
    separate, richer per-comp JSON source (COMPS/curated_comps/*.json)
    with each hero's real 4-of-~7 equipped skills, camp skills, trinkets,
    and quirks — store.comp_loadouts, keyed by the same comp name used
    here. CombatEngine.reset()'s hero_skill_ids param consumes that to
    restrict a HeroDefinition's normally-full skill list down to what a
    given comp actually equips."""

    @staticmethod
    def load(csv_dir: str | Path) -> list[CompositionDefinition]:
        comps_dir = csv_root(csv_dir) / "COMPS"
        path = CsvLoader.find_csv(comps_dir, "Comps", all_variant=False)
        if not path:
            return []
        rows = CsvLoader.load_table(path)
        comps: list[CompositionDefinition] = []
        for row in rows:
            name = row.get("Nombre", "").strip()
            heroes = [h.strip() for h in row.get("Heroes", "").split(",") if h.strip()]
            location = row.get("Location", "").strip()
            if not name or not heroes:
                continue
            comps.append(CompositionDefinition(name=name, heroes=heroes, location=location))
        return comps

    @staticmethod
    def load_modded_hero_comp_names(csv_dir: str | Path) -> set[str]:
        """**DEAD as of 2026-08-23** -- the files this reads were deleted and
        it now always returns an empty set. `CompLoadoutEntry.declared_heroes`
        replaced it: comparing the slots a comp DECLARED against the ones that
        resolved catches any unloadable class, not just the eleven listed here,
        and needs no external file. Kept only so an archived checkout still
        loads; nothing in `env/` or `bridge/` consults the result any more.

        COMPS/Comps/<name> <hash>.md (a Notion export, one per comp,
        sibling to the per-comp <name>/ subfolder CompLoadoutLoader reads)
        has an explicit "Modded Heroes: X, Y" line -- blank/absent for a
        comp built entirely from real classes, non-empty when one or more
        slots is a fan-made class this project's data has no support for
        (Monk, Black Mage, Spartan, Falconer, Sisters, Cataphract, Thrall,
        ... — distinct from the base-game class mods, shieldbreaker/
        musketeer/flagellant, which DO fully load, see HeroLoader.load_all).
        Real, concrete problem this causes if not filtered out: e.g. "Holy
        Hymn: Monk"'s own "Heroes:" line lists only 3 of its real 4 slots
        (Highwayman, Jester, Vestal — the 4th, Monk, is simply absent, not
        even an unresolvable placeholder) — training/evaluating it as a
        3-hero party is not a fair test of a comp actually designed and
        balanced around 4, and skews any conclusion drawn from that comp's
        results. Matched by the comp's own "# <name>" header line against
        store.comps/store.comp_loadouts' name field, not the filename
        (which includes a Notion page-id hash suffix)."""
        comps_dir = csv_root(csv_dir) / "COMPS" / "Comps"
        modded: set[str] = set()
        for md_path in comps_dir.glob("*.md"):
            text = md_path.read_text(encoding="utf-8", errors="ignore")
            title_match = re.search(r"^#\s+(.+?)\s*$", text, re.MULTILINE)
            modded_match = re.search(r"^Modded Heroes:\s*(.*)$", text, re.MULTILINE)
            if title_match and modded_match and modded_match.group(1).strip():
                modded.add(title_match.group(1).strip())
        return modded
