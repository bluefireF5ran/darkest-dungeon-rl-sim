"""The campaign's gameplay options, and whether the simulator agrees with them.

Darkest Dungeon lets a campaign switch off rules that change combat outright,
and the binary reads each one at the point it matters (DECOMP.md F11):

| option (`persist.game.json` -> `profile_options`) | what the game does when it is OFF |
|---|---|
| `retreats_can_fail` | `AttemptBattleRetreat` succeeds every time -- no roll |
| `multiplied_enemy_crits` | a monster crit deals max damage x1.0, not x1.5 |
| `corpses` | the target-selection desires skip the corpse logic |
| `stall_penalty` | no stall stress / reinforcement escalation |
| `deaths_door_recovery_debuffs` | no debuff on leaving Death's Door |

`engine/` implements every one of them ON, which is the default and what all
three of Fran's campaigns use (checked 2026-10-04: profile_0, _1 and _8). A
campaign with any of them off is one the simulator -- and so the combat policy
and every rollout -- misprices without saying so. This module says so.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from data.dson import parse_file

#: The value `engine/` behaves as if each option had.
SIMULATOR_ASSUMES = {
    "retreats_can_fail": 1,
    "multiplied_enemy_crits": 1,
    "corpses": 1,
    "stall_penalty": 1,
    "deaths_door_recovery_debuffs": 1,
}


def read_options(profile: str | Path) -> dict[str, object]:
    """`{option: current value}` from a profile's `persist.game.json`.

    Toggles are stored as `[current, default]`; the first is what applies.
    An unreadable or absent file gives `{}`, never an exception -- this is a
    warning channel and must not stop a run.
    """
    path = Path(profile) / "persist.game.json"
    try:
        root = parse_file(path)
        values = root.get("profile_options").get("values")
    except Exception:                                   # noqa: BLE001
        return {}
    out: dict[str, object] = {}
    for c in values.children or []:
        v = c.value
        if isinstance(v, (list, tuple)):
            v = v[0] if v else None
        out[c.name] = v
    return out


def divergences(options: dict[str, object]) -> list[str]:
    """One line per option the campaign has set differently from the engine."""
    out = []
    for name, want in SIMULATOR_ASSUMES.items():
        if name in options and options[name] != want:
            out.append(f"{name} is {options[name]!r} in this campaign; the simulator "
                       f"assumes {want!r}")
    return out


def warn(profile: str | Path, say=print) -> Optional[list[str]]:
    lines = divergences(read_options(profile))
    for line in lines:
        say(f"  WARNING: gameplay option {line} -- combat advice is priced on the wrong rules")
    return lines
