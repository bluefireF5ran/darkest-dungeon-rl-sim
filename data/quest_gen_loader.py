"""Quest generation tables -- what the quest board CAN offer, and what each
quest pays.

Added 2026-10-10 for the campaign simulator (`docs/CAMPAIGN_SIM.md`). Every
number here is a shipped file; the rules for USING them that the files do
not state are in `campaign/board.py`, each labelled as an assumption.

Sources, all Darkest mode (`modes/base` ships no override of any of them):

- `campaign/quest/quest.generation.json` -- dungeons and when they unlock,
  the per-dungeon cap, difficulty by resolve level, the quest table per
  dungeon LEVEL, and the reward tables.
- `campaign/quest/number.quest.generation.json` -- quests per town visit.
- `campaign/progression/progression.json` -- dungeon level thresholds and
  the dungeon XP a completed quest pays.
- DLC layers: Crimson Court
  (`dlc/580100_crimson_court/features/crimson_court/campaign/quest/`) adds
  the Courtyard and REPLACES the per-visit table; Color of Madness
  (`com.quest.generation.json`) ADDS a per-visit modifier. Both are applied.

**The reward tables are indexed [difficulty][length]** -- the file's own
comment says so ("ROWS are DIFFICULTY, COLs are QUEST LENGTH"), and every one
of the 15 generated quests on profile_8's board on 2026-10-10 matches it
exactly: gold (`item_table`), resolve XP, and heirloom amount. Difficulty is
the raw 1/3/5 (rows 0, 2, 4, 6 are empty padding), length is 1..3.

**`trinket_chance_table` is a set of rarity WEIGHTS, not probabilities**: at
each [difficulty][length] exactly one rarity carries a 1 in the base game
(d1 L1 common ... d5 L3 ancestral), and the Courtyard's `crimson_court` row
carries 100000 where it applies, which only reads as "this one, always" if
the cells are weights. Every quest on profile_8's board paid one trinket.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from data.paths import GAME_DIR


@dataclass(frozen=True)
class QuestOption:
    """One row of a `generated_quest_table` cell."""
    quest_type: str
    length: int
    chance: float = 1.0
    difficulty_override: Optional[int] = None
    required_plot_quest_completed: str = ""
    required_plot_quest_not_completed: str = ""
    torch_setting: str = ""
    raid_rules_override: str = ""


@dataclass(frozen=True)
class GeneratedDungeon:
    dungeon: str
    required_quests_finished: int = 0
    required_plot_quest: str = ""


@dataclass
class QuestGenTables:
    dungeons: list[GeneratedDungeon] = field(default_factory=list)
    max_per_dungeon: int = 4
    #: (resolve levels, difficulty), in file order.
    difficulty_bands: list[tuple[tuple[int, ...], int]] = field(default_factory=list)
    #: dungeon -> [dungeon level] -> options.
    quest_tables: dict[str, list[list[QuestOption]]] = field(default_factory=dict)
    #: per town visit, the replaced base table plus every DLC modifier.
    quests_per_visit: list[int] = field(default_factory=list)
    heirloom_types: dict[str, tuple[str, ...]] = field(default_factory=dict)
    #: heirloom type -> [difficulty][length] -> amount.
    heirloom_amounts: dict[str, list[list[int]]] = field(default_factory=dict)
    #: [difficulty][length] -> gold.
    gold: list[list[int]] = field(default_factory=list)
    #: [difficulty][length] -> resolve XP.
    resolve_xp: list[list[int]] = field(default_factory=list)
    #: dungeon ("" = every dungeon) -> rarity -> [difficulty][length] weight.
    trinket_weights: dict[str, dict[str, list[list[float]]]] = field(default_factory=dict)
    dungeon_level_thresholds: list[int] = field(default_factory=list)
    dungeon_xp_per_quest: list[int] = field(default_factory=list)

    # -- lookups -----------------------------------------------------------
    def dungeon_level(self, xp: int) -> int:
        th = self.dungeon_level_thresholds or [0]
        return max(i for i, t in enumerate(th) if xp >= t)

    def options(self, dungeon: str, level: int) -> list[QuestOption]:
        rows = self.quest_tables.get(dungeon) or []
        if not rows:
            return []
        return rows[min(level, len(rows) - 1)]

    def visit_count(self, visit_index: int) -> int:
        t = self.quests_per_visit or [0]
        return t[min(max(visit_index, 0), len(t) - 1)]

    @staticmethod
    def _cell(table, difficulty: int, length: int, default=0):
        try:
            row = table[difficulty]
            return row[length] if row else default
        except (IndexError, TypeError):
            return default

    def gold_reward(self, difficulty: int, length: int) -> int:
        return int(self._cell(self.gold, difficulty, length))

    def xp_reward(self, difficulty: int, length: int) -> int:
        return int(self._cell(self.resolve_xp, difficulty, length))

    def heirloom_amount(self, kind: str, difficulty: int, length: int) -> int:
        return int(self._cell(self.heirloom_amounts.get(kind, []), difficulty, length))

    def trinket_rarity_weights(self, dungeon: str, difficulty: int,
                               length: int) -> dict[str, float]:
        """Rarity weights for one quest. A dungeon-specific table (the
        Courtyard's) is added to the global one, as the file ships both."""
        out: dict[str, float] = {}
        for key in ("", dungeon):
            for rarity, table in self.trinket_weights.get(key, {}).items():
                w = float(self._cell(table, difficulty, length))
                if w > 0:
                    out[rarity] = out.get(rarity, 0.0) + w
        return out


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")).get("generation", {})


def _layer_files(game_dir: Path, pattern: str) -> list[Path]:
    """DLC files matching `pattern`, excluding every non-Darkest mode."""
    dlc = game_dir / "dlc"
    if not dlc.is_dir():
        return []
    return sorted(p for p in dlc.rglob(pattern) if "modes" not in p.parts)


def _option(rec: dict) -> QuestOption:
    return QuestOption(
        quest_type=rec.get("type", ""),
        length=int(rec.get("length", 1)),
        chance=float(rec.get("chance", 1)),
        difficulty_override=rec.get("difficulty_override"),
        required_plot_quest_completed=rec.get("required_plot_quest_completed", ""),
        required_plot_quest_not_completed=rec.get("required_plot_quest_not_completed", ""),
        torch_setting=rec.get("torch_setting", ""),
        raid_rules_override=rec.get("raid_rules_override", ""),
    )


def _merge(t: QuestGenTables, g: dict) -> None:
    d = g.get("dungeon", {})
    if "generated_quests_max_threshold" in d:
        t.max_per_dungeon = int(d["generated_quests_max_threshold"])
    for rec in d.get("generated_dungeons", []):
        gd = GeneratedDungeon(rec["id"], int(rec.get("required_number_of_quests_finished", 0)),
                              rec.get("required_plot_quest", ""))
        t.dungeons = [x for x in t.dungeons if x.dungeon != gd.dungeon] + [gd]

    bands = g.get("difficulty", {}).get("generated_resolve_level_difficulties")
    if bands:
        t.difficulty_bands = [(tuple(b["resolve_levels"]), int(b["difficulty"])) for b in bands]

    for rec in g.get("type", {}).get("available_quests_table", []):
        t.quest_tables[rec["dungeon"]] = [[_option(o) for o in cell]
                                          for cell in rec.get("generated_quest_table", [])]

    r = g.get("rewards", {})
    for rec in r.get("heirloom_type_map", []):
        t.heirloom_types[rec["dungeon"]] = tuple(rec.get("types", ()))
    for rec in r.get("heirloom_amount_table", []):
        t.heirloom_amounts[rec["type"]] = rec.get("amounts", [])
    if "item_table" in r:
        t.gold = [[sum(i.get("amount", 0) for i in cell if i.get("type") == "gold")
                   for cell in row] for row in r["item_table"]]
    if "resolve_xp_table" in r:
        t.resolve_xp = r["resolve_xp_table"]
    for rec in r.get("trinket_chance_table", []):
        t.trinket_weights.setdefault(rec.get("dungeon", ""), {})[rec["rarity"]] = rec["chances"]


def _number_late(t: QuestGenTables, g: dict, modifiers: list) -> None:
    """Per-visit replacement first, modifiers after, whatever the file order:
    the modifier is defined against the table, not against a position."""
    num = g.get("number", {})
    if "number_of_quests_per_town_visit_table" in num:
        t.quests_per_visit = [int(x) for x in num["number_of_quests_per_town_visit_table"]]
    if num.get("number_of_quests_per_town_visit_table_modifier"):
        modifiers.append(num["number_of_quests_per_town_visit_table_modifier"])


def load(game_dir: Path | str = GAME_DIR) -> QuestGenTables:
    game_dir = Path(game_dir)
    qdir = game_dir / "campaign" / "quest"
    t = QuestGenTables()
    layers = [qdir / "quest.generation.json", qdir / "number.quest.generation.json"]
    layers += _layer_files(game_dir, "*quest.generation.json")
    modifiers: list = []
    for path in layers:
        if not path.is_file():
            continue
        g = _read(path)
        number = g.pop("number", None)
        _merge(t, g)
        if number:
            _number_late(t, {"number": number}, modifiers)
    for mod in modifiers:
        n = max(len(mod), len(t.quests_per_visit))
        base = t.quests_per_visit + [t.quests_per_visit[-1] if t.quests_per_visit else 0] \
            * (n - len(t.quests_per_visit))
        t.quests_per_visit = [b + (int(mod[i]) if i < len(mod) else 0) for i, b in enumerate(base)]

    prog = game_dir / "campaign" / "progression" / "progression.json"
    if prog.is_file():
        dun = json.loads(prog.read_text(encoding="utf-8")).get("dungeon", {})
        t.dungeon_level_thresholds = [int(x) for x in dun.get("level_threshold_table", [])]
        t.dungeon_xp_per_quest = [int(x) for x in dun.get("quest_completion_xp_table", [])]
    return t


# -- plot quests ------------------------------------------------------------

@dataclass(frozen=True)
class PlotQuest:
    """One `quest.plot_quests.json` entry (+ DLC layers), reduced to what a
    campaign needs. `boss_monsters` comes from the goal ids through
    `quest.types.json` (`kill_monster` goals)."""
    plot_id: str
    dungeon: str
    difficulty: int
    length: int
    dungeon_level: int = 0
    dependency: str = ""
    is_progression: bool = False
    is_repeatable: bool = False
    is_generated_by_event: bool = False
    can_retreat: bool = True
    roster_stress_cleared: bool = False
    resolve_xp: int = 0
    gold: int = 0
    heirlooms: tuple = ()          # ((kind, amount), ...)
    trinkets: tuple = ()           # trinket ids paid outright
    extra_trinket_rarities: tuple = ()
    boss_monsters: tuple = ()


def _plot_files(game_dir: Path) -> list[Path]:
    base = game_dir / "campaign" / "quest" / "quest.plot_quests.json"
    return ([base] if base.is_file() else []) + _layer_files(game_dir, "*plot_quests.json")


def _goal_monsters(game_dir: Path) -> dict[str, tuple]:
    out: dict[str, tuple] = {}
    files = [game_dir / "campaign" / "quest" / "quest.types.json"]
    files += _layer_files(game_dir, "*quest.types.json")
    for path in files:
        if not path.is_file():
            continue
        for g in json.loads(path.read_text(encoding="utf-8")).get("goals", []):
            if g.get("type") == "kill_monster":
                out[g["id"]] = tuple(g.get("data", {}).get("monster_class_ids", ()))
    return out


def load_plot_quests(game_dir: Path | str = GAME_DIR) -> list[PlotQuest]:
    """Every plot quest, in file order (base first, then DLC) -- the order
    `campaign.board.plot_board` relies on for "the first uncompleted"."""
    game_dir = Path(game_dir)
    goals = _goal_monsters(game_dir)
    out: dict[str, PlotQuest] = {}
    for path in _plot_files(game_dir):
        for rec in json.loads(path.read_text(encoding="utf-8")).get("plot_quests", []):
            q = rec.get("quest", {})
            reward = q.get("completion_reward", {})
            items = list(reward.get("items_definition", {}).get("items", {}).values())
            monsters = tuple(m for gid in q.get("goal_ids", []) for m in goals.get(gid, ()))
            out[rec["id"]] = PlotQuest(
                plot_id=rec["id"], dungeon=q.get("dungeon", ""),
                difficulty=int(q.get("difficulty", 1)), length=int(q.get("length", 1)),
                dungeon_level=int(rec.get("dungeon_level", 0) or 0),
                dependency=rec.get("plot_quest_dependency", "") or "",
                is_progression=bool(rec.get("is_progression")),
                is_repeatable=bool(rec.get("is_repeatable")),
                is_generated_by_event=bool(rec.get("is_generated_by_event")),
                can_retreat=bool(rec.get("can_retreat", True)),
                roster_stress_cleared=bool(rec.get("is_roster_stress_cleared_on_completion")),
                resolve_xp=int(reward.get("resolve_xp", 0)),
                gold=sum(int(i.get("amount", 0)) for i in items if i.get("type") == "gold"),
                heirlooms=tuple((i["id"], int(i["amount"])) for i in items if i.get("type") == "heirloom"),
                trinkets=tuple(i["id"] for i in items if i.get("type") == "trinket"),
                extra_trinket_rarities=tuple(
                    t["rarity"] for t in rec.get("additional_trinket_completion_rewards", [])
                    for _ in range(int(t.get("amount", 1)))),
                boss_monsters=monsters,
            )
    return list(out.values())
