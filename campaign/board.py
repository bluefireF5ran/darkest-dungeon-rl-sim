"""The quest board: what the game offers on one town visit.

The tables are shipped data (`data/quest_gen_loader.py`). HOW the game draws
from them is code, not data, and each rule below that the files do not state
is labelled ASSUMPTION with what it was checked against. Replace one with a
measured rule by changing it here and recording the evidence in
`docs/CAMPAIGN_SIM.md`.

Checked against the three boards on disk on 2026-10-10 (profile_0, _1, _8):
no dungeon ever carried more than 4 generated quests (the file's
`generated_quests_max_threshold`), and no length-3 quest appeared in a
dungeon below level 3 (the table's own rows).
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Iterable, Optional

from bridge.town_state import Quest
from data.quest_gen_loader import PlotQuest, QuestGenTables, QuestOption


@dataclass
class BoardQuest:
    """A generated quest with its whole completion reward. `quest` is the
    bridge's own type, so the decide layers read it unchanged."""
    quest: Quest
    resolve_xp: int
    heirloom: str
    heirloom_amount: int
    trinket_rarity: str
    torch_setting: str = ""
    raid_rules_override: str = ""


def offered_difficulties(tables: QuestGenTables, roster_levels: Iterable[int]) -> list[int]:
    """ASSUMPTION: a difficulty is offered when at least one roster hero sits
    at one of its resolve levels. The bands overlap at levels 2 and 4 exactly
    as the file ships them. profile_8 (levels 0-6) shows 1, 3 and 5;
    profile_1 (all low) shows only 1. An empty roster still gets 1."""
    levels = set(roster_levels)
    out = [d for lv, d in tables.difficulty_bands if levels & set(lv)]
    return out or [1]


def _allowed(opt: QuestOption, completed_plots: set) -> bool:
    if opt.required_plot_quest_completed and opt.required_plot_quest_completed not in completed_plots:
        return False
    if opt.required_plot_quest_not_completed and opt.required_plot_quest_not_completed in completed_plots:
        return False
    return True


def open_dungeons(tables: QuestGenTables, quests_finished: int,
                  completed_plots: set) -> list[str]:
    return [g.dungeon for g in tables.dungeons
            if quests_finished >= g.required_quests_finished
            and (not g.required_plot_quest or g.required_plot_quest in completed_plots)]


def _weighted(rng: random.Random, weights: dict) -> Optional[str]:
    total = sum(weights.values())
    if total <= 0:
        return None
    x = rng.random() * total
    for k, w in weights.items():
        x -= w
        if x < 0:
            return k
    return next(reversed(weights))


def generate_board(rng: random.Random, tables: QuestGenTables, *,
                   visit_index: int, quests_finished: int,
                   dungeon_xp: dict, roster_levels: Iterable[int],
                   completed_plots: set = frozenset()) -> list[BoardQuest]:
    """The generated quests of one town visit (plot quests are separate).

    ASSUMPTION: the per-visit count is indexed by the town visit, capped at
    the table's last row. The three saves on disk do not pin the index
    (they read 11, 15 and 15 generated quests); the visit is the reading the
    key's own name gives.
    ASSUMPTION: each quest goes to a uniformly drawn open dungeon that still
    has room under the per-dungeon cap, and its option is drawn by `chance`
    from that dungeon's row at its current dungeon level.
    ASSUMPTION: a dungeon gated on a plot quest (the Courtyard) takes at
    most one quest per visit -- Crimson Court's own per-visit modifier is +1
    from the third visit, and profile_8's board carried exactly one."""
    difficulties = offered_difficulties(tables, roster_levels)
    dungeons = open_dungeons(tables, quests_finished, completed_plots)
    count = {d: 0 for d in dungeons}
    gated = {g.dungeon for g in tables.dungeons if g.required_plot_quest}
    cap = {d: 1 if d in gated else tables.max_per_dungeon for d in dungeons}
    board: list[BoardQuest] = []
    for i in range(tables.visit_count(visit_index)):
        room = [d for d in dungeons if count[d] < cap[d]
                and any(_allowed(o, completed_plots)
                        for o in tables.options(d, tables.dungeon_level(dungeon_xp.get(d, 0))))]
        if not room:
            break
        dungeon = rng.choice(room)
        level = tables.dungeon_level(dungeon_xp.get(dungeon, 0))
        opts = [o for o in tables.options(dungeon, level) if _allowed(o, completed_plots)]
        pick = _weighted(rng, {j: o.chance for j, o in enumerate(opts)})
        opt = opts[pick]
        difficulty = opt.difficulty_override or rng.choice(difficulties)
        rarity = _weighted(rng, tables.trinket_rarity_weights(dungeon, difficulty, opt.length)) or ""
        kinds = tables.heirloom_types.get(dungeon, ())
        heirloom = rng.choice(kinds) if kinds else ""
        count[dungeon] += 1
        board.append(BoardQuest(
            quest=Quest(key=str(len(board)), quest_id=f"generated_{len(board)}",
                        dungeon=dungeon, quest_type=opt.quest_type,
                        difficulty=difficulty, length=opt.length,
                        gold_reward=tables.gold_reward(difficulty, opt.length)),
            resolve_xp=tables.xp_reward(difficulty, opt.length),
            heirloom=heirloom,
            heirloom_amount=tables.heirloom_amount(heirloom, difficulty, opt.length) if heirloom else 0,
            trinket_rarity=rarity,
            torch_setting=opt.torch_setting,
            raid_rules_override=opt.raid_rules_override,
        ))
    return board


def plot_board(plots: Iterable[PlotQuest], tables: QuestGenTables, *,
               dungeon_xp: dict, completed_plots: set) -> list[PlotQuest]:
    """The plot quests on offer: per dungeon, the FIRST uncompleted
    progression plot quest (file order) whose dungeon level is reached and
    whose dependency is done.

    Checked against all three saves on 2026-10-10: it predicts exactly the
    boss quests on each board (profile_0: 4, profile_1: 1, profile_8: 1 --
    six of six, none extra), and the next link of the Darkest Dungeon and
    Crimson Court chains. `plot_darkest_dungeon_1` is on every board,
    profile_1's included, so the Darkest Dungeon is never gated: when to go
    is the player's decision.

    Not offered in F1: event-generated and repeatable plots (the town
    invasion, the crow, trinket retention) and non-progression ones (the
    Color of Madness farmstead)."""
    out: list[PlotQuest] = []
    seen: set = set()
    for p in plots:
        if (p.dungeon in seen or p.plot_id in completed_plots or not p.is_progression
                or p.is_repeatable or p.is_generated_by_event):
            continue
        if p.dependency and p.dependency not in completed_plots:
            continue
        if p.dungeon_level > tables.dungeon_level(dungeon_xp.get(p.dungeon, 0)):
            continue
        seen.add(p.dungeon)
        out.append(p)
    return out
