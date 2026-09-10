"""The Old Road, read from the game's own shipped starting save.

The tutorial is the one expedition in Darkest Dungeon that is **not generated**:
`GAME_DIR/scripts/starting_save/` is hand-authored and byte-identical on every new
campaign, which is what makes it this project's corridor test rig (see `BRIDGE.md`).
That also makes it the one expedition a policy can be trained on *exactly* — same two
heroes, same four skills each, same quirks, same two encounters, every time.

Nothing here is invented. Everything comes from four shipped files:

| what | file |
|---|---|
| party, formation order, the 4 equipped skills, camp skills, quirks, stress, HP | `scripts/starting_save/persist.roster.json` |
| torch level and the party's provisions | `scripts/starting_save/persist.raid.json` |
| which dungeon and how many battle tiles | `scripts/starting_save/persist.map.json`, `persist.raid.json`'s `raid_instance` |
| the two encounters themselves | `dungeons/weald/weald.1.mash.darkest`, via `store.enemy_comps` |

**The starting save is plain JSON, not DSON.** Slot creation copies these seven files
verbatim and the game rewrites them as DSON a minute or two later, so `tools/dson.py`
cannot read them and `json.load` can.

**The one inference.** The map's two battle tiles carry `mash_index` 0 (in `coAB`) and 1
(in `rooB`); the raid's dungeon (`weald`) ships exactly two `named:` mash entries called
`tutorial_1` and `tutorial_2`. Index *i* is read as the *i*-th of those, in name order.
Corroborated by live play: the `rooB` fight was Brigand Bloodletter + Brigand Fusilier,
which is `tutorial_2` exactly.

**The one convention** is the torch. The save ships `torchlight: 100` and the raid is two
fights long, so both are trained at 100 rather than at `ExpeditionEnv`'s measured torch
distribution. The real torch does burn as the party walks; how much per tile is in no
shipped file, and sampling the general distribution would train the tutorial in a dark
it never sees.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# The comp registered for this party in COMPS/Comps/. It exists so the tutorial
# party is a first-class comp for the tooling that works by name (embark, eval,
# coverage) -- `tests/env/test_tutorial_scenario.py` pins it to agree with the save,
# which is what stops the two drifting.
TUTORIAL_COMP_NAME = "The Old Road"

# `named:` mash entries whose name starts with this belong to the tutorial.
TUTORIAL_MASH_PREFIX = "tutorial_"


@dataclass(frozen=True)
class TutorialScenario:
    """The Old Road as the expedition env's explicit-scenario options."""
    comp_name: str
    zone_id: str
    tier: int
    hero_ids: tuple[str, ...]
    hero_skill_ids: tuple[tuple[str, ...], ...]
    camp_skill_ids: tuple[tuple[str, ...], ...]
    hero_levels: tuple[int, ...]
    gear_ranks: tuple[tuple[int, int], ...]
    start_hp: tuple[int, ...]
    start_stress: tuple[int, ...]
    enemy_comp_names: tuple[str, ...]
    torch_level: float
    provisions: int

    @property
    def num_combats(self) -> int:
        return len(self.enemy_comp_names)

    def reset_options(self) -> dict:
        """The dict `ExpeditionSequenceEnv.reset(options=)` takes.

        `carry_over` is how the starting stress of 10 gets in: the env has no
        other way to open a hero above zero, and 10 of 100 is not nothing on a
        two-hero party with no stress healer in it.
        """
        from engine.combatant import CarryOverHeroState
        return {
            "comp_name": self.comp_name,
            "zone_id": self.zone_id,
            "tier": self.tier,
            "hero_ids": list(self.hero_ids),
            "hero_skill_ids": [list(s) for s in self.hero_skill_ids],
            "camp_skill_ids": [list(s) for s in self.camp_skill_ids],
            "hero_levels": list(self.hero_levels),
            "gear_ranks": [tuple(g) for g in self.gear_ranks],
            "num_combats": self.num_combats,
            "length_band": "short",
            "enemy_comp_names": list(self.enemy_comp_names),
            "torch_level": self.torch_level,
            "carry_over": [
                CarryOverHeroState(hp=hp, stress=stress, is_deaths_door=False,
                                   mortality_tier="none")
                for hp, stress in zip(self.start_hp, self.start_stress)
            ],
        }


def _starting_save(game_dir: Path, which: str) -> dict:
    path = Path(game_dir) / "scripts" / "starting_save" / f"persist.{which}.json"
    return json.loads(path.read_text(encoding="utf-8"))["data"]


def _battle_tile_count(map_data: dict) -> int:
    """How many tiles of the hand-authored map hold a battle.

    Counted rather than assumed, so a map change moves the encounter list with
    it. `mash_index` is -1 on every tile that holds nothing.
    """
    found: set[int] = set()

    def walk(node):
        if isinstance(node, dict):
            idx = node.get("mash_index")
            if isinstance(idx, int) and idx >= 0:
                found.add(idx)
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(map_data)
    return len(found)


def load_tutorial_scenario(game_dir: Path, store,
                           resolve_thresholds: Optional[list[int]] = None
                           ) -> TutorialScenario:
    """The Old Road, from the shipped files. Raises if they are not there."""
    roster = _starting_save(game_dir, "roster")
    raid = _starting_save(game_dir, "raid")
    map_data = _starting_save(game_dir, "map")

    # Formation order is `actor.ranks`, not the dict order and not
    # `last_party_guids` -- the roster is keyed by guid and a dict's order is
    # not a formation. Reynauld is rank 1, Dismas rank 2.
    heroes = sorted(roster["heroes"].values(),
                    key=lambda h: h.get("actor", {}).get("ranks", 99))

    hero_ids, skills, camps, levels, gear, hp, stress = [], [], [], [], [], [], []
    for h in heroes:
        hero_id = h["heroClass"]
        if hero_id not in store.heroes:
            raise ValueError(f"the starting save names a hero this repo cannot "
                             f"load: {hero_id}")
        hero_ids.append(hero_id)
        # The roster carries INTERNAL ids ("take_aim"), not display names, so
        # no localization lookup is needed here -- unlike the comp JSON, which
        # is written in display names and has to be resolved back.
        skills.append(tuple(h["skills"]["selected_combat_skills"].keys()))
        camps.append(tuple(h["skills"]["selected_camping_skills"].keys()))
        levels.append(_resolve_level(int(h.get("resolveXp", 0)), resolve_thresholds))
        gear.append((int(h.get("weapon_rank", 0)), int(h.get("armour_rank", 0))))
        hp.append(int(h.get("actor", {}).get("current_hp", 0)))
        stress.append(int(h.get("stress", 0)))

    instance = raid.get("raid_instance", {})
    zone_id = instance.get("dungeon", "weald")
    party = raid.get("party", {})

    named = sorted(
        (e for e in store.enemy_comps
         if e.zone_id == zone_id and (e.name or "").startswith(TUTORIAL_MASH_PREFIX)),
        key=lambda e: e.name,
    )
    if not named:
        raise ValueError(f"no tutorial_* mash entries for zone {zone_id!r}")
    battles = _battle_tile_count(map_data)
    if battles > len(named):
        raise ValueError(f"the map has {battles} battle tiles but {zone_id} ships "
                         f"{len(named)} tutorial encounters")
    tier = named[0].tier

    return TutorialScenario(
        comp_name=TUTORIAL_COMP_NAME,
        zone_id=zone_id,
        tier=tier,
        hero_ids=tuple(hero_ids),
        hero_skill_ids=tuple(skills),
        camp_skill_ids=tuple(camps),
        hero_levels=tuple(levels),
        gear_ranks=tuple(gear),
        start_hp=tuple(hp),
        start_stress=tuple(stress),
        enemy_comp_names=tuple(e.name for e in named[:battles]),
        torch_level=float(party.get("torchlight", 100.0)),
        provisions=sum(int(i.get("amount", 0))
                       for i in party.get("inventory", {}).get("items", {}).values()
                       if i.get("type") == "provision"),
    )


def _resolve_level(xp: int, thresholds: Optional[list[int]]) -> int:
    """Resolve XP -> resolve level. The tutorial party is at 0 on both."""
    if not thresholds:
        return 0
    level = 0
    for i, need in enumerate(thresholds):
        if xp >= need:
            level = i
    return level
