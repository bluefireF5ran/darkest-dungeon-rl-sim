"""Read the Hamlet — the half of the game that happens between raids.

`bridge/live_state.py` reads a raid in progress. This reads everything the game
holds when there is no raid: the whole roster (not just the four who embarked),
the quests currently on the map, the wallet, and the trinket inventory. Together
they are the input to the embark decision, which is the first decision outside
combat this project can actually make.

Same discipline as `live_state.py`: a **pure save reader** with no `DataLoader`,
so it stays millisecond-cost, and it never opens the live save in place. Hero
records are parsed by `live_state._read_hero`, unchanged — a roster entry is the
same record whether the hero is in a dungeon or standing in the Abbey, and
having two parsers for it would be two things to keep in step.

## What the saves actually say, and what they do not

**Availability is on the hero, not in the building.** `persist.town.json` does
carry per-activity slots (`buildings.abbey.activities.prayer.0.hero`), but each
hero record already carries `roster.building_name` — empty when free. One field
on one record beats cross-referencing seventeen buildings, and it cannot
disagree with itself.

**HP in town is not a real number.** A hero who has never been deployed reads
`actor.current_hp` = 1.0, and a returning one keeps whatever the last raid left.
The game restores HP on returning to the Hamlet, so nothing here reads HP at
all: an embarking party is full by definition. Stress is the opposite — it
persists, it is the real constraint on who can go, and it is read.

**Hero names are not in the save.** No name, display_name or m_Name field exists
on a roster record; the game generates them client-side. Heroes are identified
by class plus guid, which is stable across saves.

**Quest difficulty IS the mash tier.** Difficulty takes 1/3/5, and the zone ships
`<zone>.conditional.1/3/5.mash.darkest` alongside `<zone>.1..5.mash.darkest` —
the same three numbers the encounter tables are keyed by. So a quest's
difficulty can be handed straight to the simulator as its tier.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bridge.live_state import (GAME_DIR, LiveHero, _find, _read_hero, _stage,
                               _val, find_profile, load_resolve_thresholds)
from data.dson import Field, parse_file

# `campaign/quest/quest.restriction.json` — "This table restricts what level
# heroes can go on what level quest. ROWS are DIFFICULTY". Indexed by the
# quest's difficulty: a hero whose resolve level is ABOVE the threshold refuses
# the quest. Difficulty 1 caps at 2 (Apprentice), 3 caps at 4 (Veteran), 5 is 99
# (Champion, unrestricted) — exactly the brackets the game shows.
#
# The file's comment also claims "COLs are QUEST LENGTH", but the shipped table
# is one-dimensional, so length does not vary the cap in any shipped data.
RESTRICTION_PATH = ("campaign", "quest", "quest.restriction.json")
FALLBACK_RESOLVE_CAPS = [2, 2, 3, 4, 5, 99, 99]

# The four core zones the simulator has encounter tables for. `darkestdungeon`
# is excluded on purpose: it has no tiers 1-5, only scripted tier-6 content.
SIMULABLE_DUNGEONS = ("crypts", "warrens", "cove", "weald")


@dataclass
class Quest:
    """One entry on the quest map, as offered."""
    key: str                  # the save's own index for it
    quest_id: str             # "generated_0", "plot_darkest_dungeon_1"
    dungeon: str              # "weald"
    quest_type: str           # "explore", "cleanse", "kill_boss", …
    difficulty: int           # 1 / 3 / 5, and IS the mash tier
    length: int               # 1..4, see QUEST_LENGTH_TO_BAND
    gold_reward: int = 0

    @property
    def is_generated(self) -> bool:
        return self.quest_id.startswith("generated")

    @property
    def simulable(self) -> bool:
        """Whether this repo can field an encounter table for it. Plot quests and
        the DLC modes decline here rather than being simulated against the wrong
        monsters."""
        return self.dungeon in SIMULABLE_DUNGEONS and 1 <= self.difficulty <= 5

    def label(self) -> str:
        return (f"{self.dungeon} {self.quest_type} "
                f"(difficulty {self.difficulty}, length {self.length})")


@dataclass
class RosterHero:
    """A hero in the Hamlet: the raid record plus the two facts only town has.

    Wraps `LiveHero` rather than restating its twenty fields — it is the same
    record, parsed by the same function."""
    hero: LiveHero
    building: str = ""        # "" when free; "abbey"/"tavern"/"sanitarium" when not
    in_party: bool = False    # roster.status 1 = currently selected to embark

    @property
    def guid(self) -> int:
        return self.hero.guid

    @property
    def hero_class(self) -> str:
        return self.hero.hero_class

    @property
    def resolve_level(self) -> int:
        return self.hero.resolve_level

    @property
    def stress(self) -> float:
        return self.hero.stress

    @property
    def available(self) -> bool:
        """Free to embark. An afflicted hero is NOT excluded here — the game
        lets you send one, and whether that is wise is exactly the question the
        rollout answers."""
        return not self.building

    def label(self) -> str:
        return f"{self.hero_class}#{self.guid} (resolve {self.resolve_level})"


@dataclass
class Recruit:
    """One hero on offer at the stage coach.

    **The coach's roster is IN THE SAVE**, which is what makes recruiting a
    data decision rather than a screen-reading one:
    `persist.town.json` -> `buildings.stage_coach.store.<list>.generated.<n>`
    carries a full hero record -- class, resolve XP, weapon and armour rank,
    quirks and equipped skills. So WHO to take is answerable offline and the
    screen is needed only for the geometry of the drag.

    `slot` is the save's own key for the entry, and `listing` says which of the
    two lists it came from. Both are kept because the panel draws them in that
    order and the drag needs an index, not an identity.
    """
    slot: int
    listing: str              # "hero_recruit" | "shard_hero_recruit"
    hero_class: str
    resolve_xp: int = 0
    weapon_rank: int = 0
    armour_rank: int = 0
    quirks: tuple = ()
    skills: tuple = ()
    # **Which row the PANEL draws this on -- not `slot`, and not the position
    # in the pooled list.** The two listings are two stores, and the coach
    # column draws `hero_recruit` alone: on 2026-09-01 the save held five
    # entries (one hero_recruit, four shard_hero_recruit) and the panel drew
    # exactly one, so two hires dragged from empty space and came back
    # UNCONFIRMED. `slot` is the save's key and skips numbers; this counts
    # from 0 within the listing, which is what `recruit_slot(row)` wants.
    row: int = 0

    def label(self) -> str:
        return (f"{self.hero_class} (xp {self.resolve_xp}, "
                f"w{self.weapon_rank}/a{self.armour_rank})")


# The two lists the coach keeps. `shard_hero_recruit` is Color of Madness's
# shard-bought roster; it is read because it is drawn in the same panel, and
# named separately because nothing should silently treat the two as one pool.
RECRUIT_LISTINGS = ("hero_recruit", "shard_hero_recruit")


def read_recruits(town_file: Path) -> list[Recruit]:
    """Who the stage coach is offering, in the save's own order.

    Returns an empty list rather than raising when the file is missing or has
    no coach: a fresh campaign has neither, and a town reader that throws on
    the ordinary case is one every caller has to wrap.
    """
    try:
        root = parse_file(town_file)
    except (OSError, ValueError):
        return []
    coach = root.find_all("stage_coach")
    if not coach:
        return []
    out: list[Recruit] = []
    rows: dict = {}
    for child in coach[0].children:
        if child.name != "store":
            continue
        for listing in child.children:
            if listing.name not in RECRUIT_LISTINGS:
                continue
            entries = (listing.to_dict() or {}).get("generated") or {}
            for key, rec in entries.items():
                if not isinstance(rec, dict):
                    continue
                out.append(Recruit(
                    slot=int(key) if str(key).isdigit() else -1,
                    listing=listing.name,
                    hero_class=str(rec.get("heroClass") or ""),
                    resolve_xp=int(rec.get("resolveXp") or 0),
                    weapon_rank=int(rec.get("weapon_rank") or 0),
                    armour_rank=int(rec.get("armour_rank") or 0),
                    quirks=tuple(sorted((rec.get("quirks") or {}).keys())),
                    skills=tuple(sorted(
                        ((rec.get("skills") or {})
                         .get("selected_combat_skills") or {}).keys())),
                ))
    out = sorted(out, key=lambda r: (RECRUIT_LISTINGS.index(r.listing),
                                     r.slot))
    # Number the rows AFTER the sort -- the panel draws them in the sorted
    # order, and the dict the entries came out of is keyed by the save's slot.
    for r in out:
        rows[r.listing] = rows.get(r.listing, -1) + 1
        r.row = rows[r.listing]
    return out


@dataclass
class TownState:
    heroes: list[RosterHero] = field(default_factory=list)
    quests: list[Quest] = field(default_factory=list)
    wallet: dict[str, int] = field(default_factory=dict)
    trinket_inventory: list[str] = field(default_factory=list)
    recruits: list[Recruit] = field(default_factory=list)
    resolve_caps: list[int] = field(default_factory=lambda: list(FALLBACK_RESOLVE_CAPS))

    def resolve_cap(self, difficulty: int) -> int:
        """Highest resolve level allowed on a quest of this difficulty."""
        if 0 <= difficulty < len(self.resolve_caps):
            return self.resolve_caps[difficulty]
        return 99

    def eligible(self, quest: Quest) -> list[RosterHero]:
        """Who could actually be sent on this quest: free, and not over the
        resolve cap the game itself enforces."""
        cap = self.resolve_cap(quest.difficulty)
        return [h for h in self.heroes if h.available and h.resolve_level <= cap]


def load_resolve_caps(game_dir: Path = GAME_DIR) -> list[int]:
    import json
    path = game_dir.joinpath(*RESTRICTION_PATH)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        table = data["restriction"]["difficulty"]["resolve_level_threshold_table"]
        return [int(v) for v in table] or list(FALLBACK_RESOLVE_CAPS)
    except (OSError, KeyError, ValueError, TypeError):
        return list(FALLBACK_RESOLVE_CAPS)


def _read_quests(quest_file: Path) -> list[Quest]:
    if not quest_file.exists():
        return []
    root = parse_file(quest_file)
    block = root.get("quests")
    out = []
    for entry in (block.children or []) if block is not None else []:
        reward = entry.get("completion_reward")
        gold = 0
        items = reward.get("items_definition") if reward is not None else None
        items = items.get("items") if items is not None else None
        for slot in (items.children or []) if items is not None else []:
            if _val(slot, "type") == "gold":
                gold = int(_val(slot, "amount") or 0)
        out.append(Quest(
            key=entry.name,
            quest_id=str(_val(entry, "id") or ""),
            dungeon=str(_val(entry, "dungeon") or ""),
            quest_type=str(_val(entry, "type") or ""),
            difficulty=int(_val(entry, "difficulty") or 0),
            length=int(_val(entry, "length") or 1),
            gold_reward=gold,
        ))
    return out


def _read_estate(estate_file: Path) -> tuple[dict[str, int], list[str]]:
    if not estate_file.exists():
        return {}, []
    root = parse_file(estate_file)
    wallet: dict[str, int] = {}
    block = root.get("wallet")
    for slot in (block.children or []) if block is not None else []:
        kind = _val(slot, "type")
        if kind:
            wallet[str(kind)] = int(_val(slot, "amount") or 0)
    # Same slot-keyed shape as a hero's equipped trinkets: the keys are slot
    # numbers and the id is INSIDE. Reading the keys gives ['0','1',…].
    trinkets: list[str] = []
    tblock = root.get("trinkets")
    tblock = tblock.get("items") if tblock is not None else None
    for slot in (tblock.children or []) if tblock is not None else []:
        tid = _val(slot, "id")
        if tid:
            trinkets.append(str(tid))
    return wallet, trinkets


def _read_roster(roster_file: Path, thresholds: list[int]) -> list[RosterHero]:
    if not roster_file.exists():
        return []
    root = parse_file(roster_file)
    out = []
    for entry in (root.get("heroes").children if root.get("heroes") else []):
        rec = _find(entry, "raw_data")
        rec = rec.value if rec is not None else None
        if not isinstance(rec, Field):
            continue
        guid = int(entry.name)
        out.append(RosterHero(
            hero=_read_hero(guid, rec, thresholds),
            building=str(_val(rec, "roster.building_name") or ""),
            in_party=int(_val(rec, "roster.status") or 0) == 1,
        ))
    return out


def read_town_state(profile: str | Path | None = None,
                    roster_file: str | Path | None = None,
                    quest_file: str | Path | None = None,
                    estate_file: str | Path | None = None,
                    town_file: str | Path | None = None,
                    thresholds: Optional[list[int]] = None,
                    game_dir: Path = GAME_DIR) -> TownState:
    """The Hamlet right now. Pass explicit files to read a captured snapshot
    instead of the live profile — same convention as `read_live_state`."""
    if roster_file is None or quest_file is None or estate_file is None:
        prof = find_profile(str(profile) if profile else None)
        staged = _stage([prof / "persist.roster.json", prof / "persist.quest.json",
                         prof / "persist.estate.json", prof / "persist.town.json"])
        roster_file = roster_file or staged / "persist.roster.json"
        quest_file = quest_file or staged / "persist.quest.json"
        estate_file = estate_file or staged / "persist.estate.json"
        town_file = town_file or staged / "persist.town.json"

    if thresholds is None:
        try:
            thresholds = load_resolve_thresholds(game_dir)
        except (OSError, KeyError, ValueError):
            thresholds = []

    wallet, trinkets = _read_estate(Path(estate_file))
    return TownState(
        heroes=_read_roster(Path(roster_file), thresholds),
        quests=_read_quests(Path(quest_file)),
        wallet=wallet,
        trinket_inventory=trinkets,
        recruits=read_recruits(Path(town_file)) if town_file else [],
        resolve_caps=load_resolve_caps(game_dir),
    )


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--profile", default=None)
    args = ap.parse_args()

    state = read_town_state(args.profile)
    gold = state.wallet.get("gold", 0)
    print(f"Wallet: {gold} gold | " + " ".join(
        f"{k} {v}" for k, v in state.wallet.items() if k != "gold" and v))
    print(f"Trinkets in stock: {len(state.trinket_inventory)}")
    print()
    print(f"Quests on the map ({sum(q.is_generated for q in state.quests)} generated):")
    for q in state.quests:
        if not q.is_generated:
            continue
        mark = "" if q.simulable else "   [not simulable]"
        print(f"  {q.label()}  reward {q.gold_reward} gold{mark}")
    print()
    free = [h for h in state.heroes if h.available]
    print(f"Roster: {len(state.heroes)} heroes, {len(free)} free")
    for h in sorted(free, key=lambda h: (-h.resolve_level, h.hero_class)):
        flags = []
        if h.in_party:
            flags.append("in party")
        if h.hero.affliction_id:
            flags.append(h.hero.affliction_id)
        if h.hero.virtue_id:
            flags.append(h.hero.virtue_id)
        tail = f"  [{', '.join(flags)}]" if flags else ""
        print(f"  {h.label():34s} stress {h.stress:5.1f}{tail}")
    busy = [h for h in state.heroes if not h.available]
    for h in busy:
        print(f"  {h.label():34s} busy in the {h.building}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
