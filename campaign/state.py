"""The campaign's whole state between weeks, and its two views:

- `to_town_state()` -- the `bridge.town_state.TownState` the decide layers
  (`bridge.townplan`, `bridge.embarkplan`) read, unchanged;
- `write_profile()` -- a profile directory holding the one save file those
  layers read from disk, `persist.upgrades.json` (skills and gear through
  `data.hero_skills`, building upgrades through `data.heirlooms`).

Written in the same shape as a real save rather than adapting the readers,
so the simulated Hamlet decides with exactly the code the live player runs.

**Where a campaign starts.** The game's own `scripts/starting_save/` --
Reynauld and Dismas, their quirks and skills, the starting heirlooms -- plus
the tutorial quest's completion reward (`plot_tutorial_crypts`). The
tutorial itself is a scripted map and is not simulated. Recruits are free in
Darkest Dungeon 1, so the first stage coach (`first_hero_classes`: Plague
Doctor, Vestal) is offered rather than given.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from bridge.live_state import LiveHero
from bridge.town_state import (Quest, Recruit, RosterHero, TownState,
                               load_resolve_caps)
from data.dson import dd_name_hash
from data.hero_skills import unknown
from data.paths import GAME_DIR
from data.quest_gen_loader import load_plot_quests

STARTING_SAVE = ("scripts", "starting_save")


@dataclass
class CampaignState:
    week: int = 0
    heroes: list[LiveHero] = field(default_factory=list)
    dead: list[LiveHero] = field(default_factory=list)
    #: guid -> town building the hero is in this week ("abbey", "tavern",
    #: "sanitarium"); absent = free.
    buildings: dict[int, str] = field(default_factory=dict)
    wallet: dict[str, int] = field(default_factory=dict)
    trinkets: list[str] = field(default_factory=list)
    #: (instance, tree id) -> ranks bought. A hero tree's instance is the
    #: hero's guid and its tree id is "<class>.<tree>"; a building tree is
    #: instance 0 and "<building>.<tree>".
    ranks: dict[tuple[int, str], int] = field(default_factory=dict)
    dungeon_xp: dict[str, int] = field(default_factory=dict)
    quests_finished: int = 0
    completed_plots: set = field(default_factory=set)
    recruits: list[Recruit] = field(default_factory=list)
    districts: dict = field(default_factory=dict)
    next_guid: int = 1
    #: Town visits so far: indexes the per-visit quest count.
    visits: int = 0
    #: guid -> quirk ids locked at the Sanitarium.
    locked: dict[int, set] = field(default_factory=dict)

    # -- views -------------------------------------------------------------
    def roster(self) -> list[RosterHero]:
        return [RosterHero(hero=h, building=self.buildings.get(h.guid, ""))
                for h in self.heroes]

    def to_town_state(self, quests: list[Quest] = (),
                      game_dir: Path = GAME_DIR) -> TownState:
        return TownState(
            heroes=self.roster(),
            quests=list(quests),
            wallet=dict(self.wallet),
            trinket_inventory=list(self.trinkets),
            recruits=list(self.recruits),
            districts=dict(self.districts),
            resolve_caps=load_resolve_caps(game_dir),
            quests_finished=self.quests_finished,
            locked_quirks={g: frozenset(q) for g, q in self.locked.items()},
        )

    def upgrades_json(self) -> dict:
        """`persist.upgrades.json` in the DSON layout `read_rank_counts`
        reads: one `purchases.<n>` entry per rank bought."""
        purchases: dict[str, dict] = {}
        for (instance, tree), n in sorted(self.ranks.items()):
            for _ in range(n):
                purchases[str(len(purchases))] = {
                    "tree_id": dd_name_hash(tree) & 0xFFFFFFFF,
                    "instance_number": instance,
                }
        return {"version": 1, "purchases": purchases}

    def write_profile(self, profile_dir: Path | str) -> Path:
        d = Path(profile_dir)
        d.mkdir(parents=True, exist_ok=True)
        (d / "persist.upgrades.json").write_text(json.dumps(self.upgrades_json()),
                                                 encoding="utf-8")
        return d

    # -- mutations ---------------------------------------------------------
    def add_hero(self, hero_class: str, *, resolve_xp: int = 0, resolve_level: int = 0,
                 stress: float = 0.0, quirks=(), combat_skills=(), camping_skills=(),
                 weapon_rank: int = 0, armour_rank: int = 0,
                 game_dir: Path = GAME_DIR) -> LiveHero:
        """Hire. Each starting skill is a rank-1 purchase on the hero's own
        guid, exactly as the starting save records Reynauld's and Dismas'.

        A combat skill the class's tree does not have is refused: the bridge
        readers would silently report it as never unlocked, and the Guild
        would then be asked to buy a skill that does not exist."""
        bad = unknown((hero_class, 0), combat_skills, str(game_dir))
        if bad:
            raise ValueError(f"{hero_class} has no combat skill {bad}")
        h = LiveHero(guid=self.next_guid, hero_class=hero_class, stress=float(stress),
                     resolve_xp=resolve_xp, resolve_level=resolve_level,
                     weapon_rank=weapon_rank, armour_rank=armour_rank,
                     selected_skills=list(combat_skills),
                     camping_skills=list(camping_skills), quirks=list(quirks))
        self.next_guid += 1
        for tree in (*combat_skills, *camping_skills):
            self.ranks[(h.guid, f"{hero_class}.{tree}")] = 1
        self.heroes.append(h)
        return h

    def kill(self, guid: int) -> None:
        for h in list(self.heroes):
            if h.guid == guid:
                self.heroes.remove(h)
                self.dead.append(h)
                self.buildings.pop(guid, None)

    def pay(self, kind: str, amount: int) -> None:
        self.wallet[kind] = int(self.wallet.get(kind, 0)) + int(amount)

    def finish_plot(self, plot_id: str, game_dir: Path = GAME_DIR) -> None:
        """Book a completed plot quest's FIXED rewards. Its extra random
        trinkets and the party's resolve XP are the expedition's to apply."""
        plot = next((p for p in load_plot_quests(game_dir) if p.plot_id == plot_id), None)
        if plot is None:
            raise KeyError(plot_id)
        self.completed_plots.add(plot_id)
        self.quests_finished += 1
        self.pay("gold", plot.gold)
        for kind, amount in plot.heirlooms:
            self.pay(kind, amount)
        self.trinkets.extend(plot.trinkets)


def _template(game_dir: Path, name: str) -> dict:
    path = Path(game_dir).joinpath(*STARTING_SAVE, name)
    return json.loads(path.read_text(encoding="utf-8")).get("data", {})


def new_campaign(game_dir: Path | str = GAME_DIR, *,
                 after_tutorial: bool = True) -> CampaignState:
    """A campaign as the game starts one: the starting save's two heroes and
    wallet, and (by default) the tutorial already won."""
    game_dir = Path(game_dir)
    s = CampaignState()
    for rec in _template(game_dir, "persist.estate.json").get("wallet", {}).values():
        s.pay(rec["type"], int(rec["amount"]))
    roster = _template(game_dir, "persist.roster.json")
    for guid in sorted(roster.get("heroes", {}), key=int):
        rec = roster["heroes"][guid]
        s.next_guid = int(guid)
        skills = rec.get("skills", {})
        s.add_hero(rec["heroClass"], resolve_xp=int(rec.get("resolveXp", 0)),
                   stress=float(rec.get("stress", 0)),
                   quirks=tuple(rec.get("quirks", {})),
                   combat_skills=tuple(skills.get("selected_combat_skills", {})),
                   camping_skills=tuple(skills.get("selected_camping_skills", {})),
                   weapon_rank=int(rec.get("weapon_rank", 0)),
                   armour_rank=int(rec.get("armour_rank", 0)))
    s.next_guid = max(s.next_guid, int(roster.get("nextGuid", s.next_guid)))
    if after_tutorial:
        s.finish_plot("plot_tutorial_crypts", game_dir)
    return s
