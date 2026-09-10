"""What clears the thing blocking the corridor, from the game's own props.

    python bridge/obstacles.py
    python bridge/obstacles.py --zone cove --have shovel=4

## Why this is a module and not two lines in `act.py`

A raid died to an obstacle on 2026-09-05. The party stood on `coNO` tile 2 in
the Cove with **four shovels in the pack**, `W` opened the Shipwreck panel, and
nothing in the bridge knew how to click anything in it -- so the route layer
walked twelve steps each way, gave up, and the expedition ended in a corridor.

An obstacle is not a curio and the differences all matter:

* **The save does not name it.** A curio tile carries `curio_id`; an obstacle
  tile carries `content == 4` and nothing else. Checked against a live Cove
  map: `coCH` tile 1 and `coNO` tile 2 are both `curio=None trap=None`.
* **Walking away is not an option.** A curio can be skipped; an obstacle is
  standing in the corridor the party has to walk down. So there is no `skip`
  here -- only which of two ways to clear it.
* **Bare hands always work.** `props/prop_definitions.json` ships
  `clear_on_failure: true`, so the question is never "can we" but "what does it
  cost": `health: -0.05`, `torchlight: -20.0` and `fail_effects: ["Stress 2"]`.

## Which tool, derived rather than remembered

The base `obstacle` prop declares `interaction_type: "shovel_obstacle"` and
every obstacle in the game inherits it. Two DLC props override it to
`torch_obstacle` (Crimson Court's `mist`, Color of Madness's `farm_mist`).

Which props a zone can draw is also shipped, in `<zone>.props.darkest`:

    cove       obstacles: .chance 1 .types shipwreck
    crypts     obstacles: .chance 1 .types rubble
    warrens    obstacles: .chance 1 .types rubble
    weald      obstacles: .chance 1 .types thorny_thicket

One type each, all inheriting the base, so all four base zones want a **shovel**
and the derivation says so without the word appearing anywhere in this file.

**And the Courtyard is why this refuses instead of defaulting.** It draws
`mist` *and* `pillar` -- a torch obstacle and a shovel one -- so the zone alone
cannot name the tool there. `tool_for_zone` returns None and the caller clears
it bare-handed, which is correct rather than merely safe: guessing would spend
the wrong provision and still leave the obstacle standing.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data.darkest_parser import DarkestParser          # noqa: E402
from env.gym_env import GAME_DIR                       # noqa: E402

#: `interaction_type` -> the supply id the panel's slot then holds. The two
#: values are the only ones the shipped props carry; an unknown one resolves to
#: None, which clears the obstacle bare-handed rather than clicking blind.
INTERACTION_ITEM = {
    "shovel_obstacle": "shovel",
    "torch_obstacle": "torch",
}

#: The base prop's own `interaction_type`, and the name every obstacle
#: `inherits_from`. Read, never assumed -- `_base_interaction` fetches it.
BASE_PROP = "obstacle"


@dataclass(frozen=True)
class ObstacleCost:
    """What clearing one bare-handed costs, from the base prop."""
    max_hp_fraction: float = 0.0     # `health`, negative
    torchlight: float = 0.0          # `torchlight`, negative
    effects: tuple[str, ...] = ()    # `fail_effects`

    def line(self) -> str:
        parts = []
        if self.max_hp_fraction:
            parts.append(f"{self.max_hp_fraction:+.0%} max HP")
        if self.torchlight:
            parts.append(f"{self.torchlight:+.0f} torch")
        parts.extend(self.effects)
        return ", ".join(parts) or "nothing measurable"


@dataclass(frozen=True)
class ObstacleAdvice:
    """`action` is "use" or "interact". Never "skip" -- see the docstring."""
    action: str
    item: Optional[str]
    why: str
    cost: ObstacleCost = ObstacleCost()

    def line(self) -> str:
        if self.action == "use":
            return f"clear the obstacle with a {self.item} - {self.why}"
        return f"clear the obstacle bare-handed ({self.cost.line()}) - {self.why}"


def _props_files(game_dir: Path) -> list[Path]:
    base = game_dir / "props" / "obstacle_definitions.json"
    found = [base] if base.is_file() else []
    found.extend(sorted(game_dir.glob("dlc/**/obstacle_definitions.json")))
    return found


@lru_cache(maxsize=4)
def obstacle_props(game_dir: str = str(GAME_DIR)) -> dict[str, Optional[str]]:
    """`{prop name: interaction_type}`, base merged with every DLC file.

    A DLC prop is a NEW id here rather than a redefinition -- `farm_mist` is
    not `mist` -- so a plain merge is right and there is nothing to shadow.
    A prop that declares no `interaction_type` inherits the base one, and that
    inheritance is resolved in `tool_for_prop` rather than here, so this stays
    a faithful read of the files.
    """
    out: dict[str, Optional[str]] = {}
    root = Path(game_dir)
    for path in _props_files(root):
        try:
            data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except Exception:                                  # noqa: BLE001
            continue
        for prop in data.get("props", []):
            name = prop.get("name")
            if name:
                out[name] = prop.get("default_data", {}).get("interaction_type")
    return out


@lru_cache(maxsize=4)
def _base_prop(game_dir: str = str(GAME_DIR)) -> dict:
    path = Path(game_dir) / "props" / "prop_definitions.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:                                      # noqa: BLE001
        return {}
    for prop in data.get("props", []):
        if prop.get("name") == BASE_PROP:
            return prop.get("default_data", {}) or {}
    return {}


def bare_handed_cost(game_dir: str = str(GAME_DIR)) -> ObstacleCost:
    """What the base prop charges for clearing one with your hands."""
    base = _base_prop(game_dir)
    effects = base.get("fail_effects") or []
    return ObstacleCost(max_hp_fraction=float(base.get("health") or 0.0),
                        torchlight=float(base.get("torchlight") or 0.0),
                        effects=tuple(str(e) for e in effects))


def tool_for_prop(prop: str, game_dir: str = str(GAME_DIR)) -> Optional[str]:
    """The supply a named obstacle prop wants, inheritance resolved."""
    props = obstacle_props(game_dir)
    if prop not in props:
        return None
    kind = props[prop] or _base_prop(game_dir).get("interaction_type")
    return INTERACTION_ITEM.get(kind or "")


@lru_cache(maxsize=32)
def zone_obstacle_types(zone: str,
                        game_dir: str = str(GAME_DIR)) -> tuple[str, ...]:
    """Which obstacle props `<zone>.props.darkest` says this zone can draw."""
    path = Path(game_dir) / "dungeons" / zone / f"{zone}.props.darkest"
    if not path.is_file():
        # The DLC zones keep theirs under their own feature tree.
        hits = sorted(Path(game_dir).glob(f"dlc/**/{zone}/{zone}.props.darkest"))
        if not hits:
            return ()
        path = hits[0]
    try:
        records = DarkestParser().parse_file(str(path))
    except Exception:                                      # noqa: BLE001
        return ()
    out: list[str] = []
    for rec in records:
        if rec.get("_type") != "obstacles":
            continue
        types = rec.get("types")
        # A key with several quoted values parses to a list and a single one
        # stays a str. Both shapes are shipped -- the Courtyard has two.
        out.extend([types] if isinstance(types, str) else list(types or ()))
    return tuple(out)


def tool_for_zone(zone: str,
                  game_dir: str = str(GAME_DIR)) -> Optional[str]:
    """The supply this zone's obstacles want, or None when they disagree.

    None is a real answer, not a failure: the Courtyard draws a torch obstacle
    and a shovel one, and the save does not say which is on the tile.
    """
    tools = {tool_for_prop(p, game_dir) for p in zone_obstacle_types(zone, game_dir)}
    tools.discard(None)
    return tools.pop() if len(tools) == 1 else None


def zone_from_save(raid_file: str | Path) -> str:
    """Which dungeon the party is in, from `persist.game.json`.

    NOT from `persist.raid.json`, which is the file every other reader here
    takes -- it carries the party, the pack and the position and says nothing
    about the zone. `raiddungeon` lives one file over, and the argument is the
    raid path only so callers can keep passing the one path they already hold.

    "" on anything unreadable, which `advise` turns into the bare-handed
    answer rather than an exception in the middle of a raid.
    """
    path = Path(str(raid_file).replace("raid.json", "game.json"))
    if not path.is_file():
        return ""
    try:
        from data.dson import parse_file                  # noqa: PLC0415
        game = parse_file(path)
    except Exception:                                      # noqa: BLE001
        return ""
    # `parse_file` hands back a `Field` tree, not a dict: `.get` returns the
    # child FIELD and the string is on its `.value`. Reading the field itself
    # as the answer is how this returned "" against a save that plainly says
    # `cove`.
    field = game.get("raiddungeon") if game is not None else None
    value = getattr(field, "value", None)
    return value if isinstance(value, str) else ""


def advise(zone: str, inventory: Optional[dict] = None,
           game_dir: str = str(GAME_DIR)) -> ObstacleAdvice:
    """Use the tool if the zone names one and the pack has it; else hands."""
    cost = bare_handed_cost(game_dir)
    tool = tool_for_zone(zone, game_dir) if zone else None
    if tool is None:
        types = zone_obstacle_types(zone, game_dir) if zone else ()
        why = (f"{zone or 'this zone'} draws {' and '.join(types) or 'nothing'}"
               f" and the save does not say which is on the tile"
               if types else f"no obstacle type is declared for {zone or '?'}")
        return ObstacleAdvice("interact", None, why, cost)
    have = int((inventory or {}).get(tool, 0))
    if have <= 0:
        return ObstacleAdvice("interact", None,
                              f"it wants a {tool} and the pack has none", cost)
    return ObstacleAdvice("use", tool,
                          f"{zone} obstacles want a {tool} and the pack has "
                          f"{have}", cost)


def main() -> int:
    args = sys.argv[1:]
    zone = ""
    have: dict[str, int] = {}
    while args:
        arg = args.pop(0)
        if arg == "--zone" and args:
            zone = args.pop(0)
        elif arg == "--have" and args:
            for pair in args.pop(0).split(","):
                key, _, num = pair.partition("=")
                have[key.strip()] = int(num or 1)
    print(f"bare-handed cost: {bare_handed_cost().line()}")
    props = obstacle_props()
    print(f"\n{len(props)} obstacle props:")
    for name in sorted(props):
        print(f"  {name:16s} {props[name] or '(inherits)':16s} "
              f"-> {tool_for_prop(name) or 'no tool'}")
    zones = [zone] if zone else ["cove", "crypts", "warrens", "weald",
                                 "darkestdungeon", "courtyard"]
    print("\nper zone:")
    for one in zones:
        types = zone_obstacle_types(one)
        print(f"  {one:16s} {', '.join(types) or '(none)':28s} "
              f"-> {tool_for_zone(one) or 'ambiguous, use hands'}")
    if zone:
        print(f"\n{advise(zone, have).line()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
