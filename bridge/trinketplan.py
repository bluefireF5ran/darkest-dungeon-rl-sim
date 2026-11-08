"""Which owned trinket goes on which seated hero -- Fran's team-builder decides.

Fran, 2026-09-25: *"No se equipa ningun tipo de trinket, aunque no sean los
perfectos se podrian equipar ... puedes consultar el apartado suggest del repo
dd-team-builder"*. That repo's re-equip (`src/utils/trinketReequip.js`, valued
by `trinketValue.js` / `heroNeeds.js`, best-in-slot from `bisIndex.js`) is his
rules in code, so it is CALLED, through `tools/teambuilder_reequip.js`, and
never ported. Anything that stops it (no Node, no builder checkout, a parse
error) returns no picks: an unequipped party still raids.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Optional

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "tools" / "teambuilder_reequip.js"
TIMEOUT_S = 60


def _key(name: str) -> str:
    """The builder's `nameKey`: lowercase, apostrophes gone, runs of
    anything else to one space."""
    s = re.sub(r"['\u2019`]", "", str(name).replace("_", " ").lower())
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


@lru_cache(maxsize=1)
def display_names() -> dict:
    """`{trinket_id: English display name}` off the game's string tables.

    The builder knows trinkets by NAME. Sending `flag_5` or `rw_warm_scarf`
    as "flag 5" / "rw warm scarf" matched nothing, so on the live chest
    (2026-09-27) every class trinket was invisible to the planner: the
    Flagellant's Heartburst Hood, the Runaway's Warm Scarf, the Bounty
    Hunter's Agility Talon (which `agile_talon` does not spell either)."""
    try:
        from data.comp_loadout_loader import CompLoadoutLoader  # noqa: PLC0415
        from data.paths import GAME_DIR                          # noqa: PLC0415
        return dict(CompLoadoutLoader._load_english_entries(
            GAME_DIR, "str_inventory_title_trinket"))
    except Exception:                                            # noqa: BLE001
        return {}


def plan_trinkets(party, owned) -> list:
    """`[[trinket_id, ...] per hero, in party order]`, or `[]` when unavailable.

    `party` is `[(hero_class, [skill ids]), ...]` in rank order; `owned` the
    estate's trinket ids. Returned ids are the OWNED ids, matched back by
    name key, so the caller can find each in the inventory.
    """
    if not owned or not party or shutil.which("node") is None or not SCRIPT.exists():
        return []
    names = display_names()
    payload = {"party": [{"heroClass": cls, "activeSkills": list(skills or [])}
                         for cls, skills in party],
               "owned": [names.get(t, t) for t in owned]}
    try:
        out = subprocess.run(["node", str(SCRIPT)], input=json.dumps(payload),
                             capture_output=True, text=True, timeout=TIMEOUT_S,
                             env=dict(os.environ))
        data = json.loads(out.stdout or "{}")
    except (OSError, subprocess.SubprocessError, ValueError):
        return []
    by_key: dict = {}
    for tid in owned:
        by_key.setdefault(_key(names.get(tid, tid)), []).append(tid)
    plan = []
    for hero in data.get("heroes") or []:
        picks = []
        for slot in ("trinket1", "trinket2"):
            name = hero.get(slot) or ""
            ids = by_key.get(_key(name)) if name else None
            if ids:
                picks.append(ids.pop(0))
        plan.append(picks)
    return plan


def main(argv: Optional[list] = None) -> int:
    from bridge.live_state import find_profile            # noqa: PLC0415
    from bridge.town_state import read_town_state         # noqa: PLC0415
    town = read_town_state(find_profile())
    party = [(h.hero_class, list(getattr(h.hero, "skills", []) or []))
             for h in town.heroes[:4]]
    print(plan_trinkets(party, town.trinket_inventory))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
