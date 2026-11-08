"""Veto an embark the raid layer would abandon at the entrance.

`embarkplan` picks the comp by COIN FLIP among the fieldable ones -- Fran's
ask, and the reason that module has no rollout. This does not rank anything
and does not replace the flip. It answers one narrower question about the
comp the flip drew: **would the survival layer, asking the same simulator the
raid will ask, walk this party straight back out?**

Live 2026-09-25: the flip drew Antiquarian + Arbalest + Houndmaster + Man at
Arms for a short Cove explore. The first forecast in the entrance room, at
full HP, read P(finish intact) 0%, 3.2 expected deaths; the raid retreated
after one fight and the 4,550 gold of provisions bought for it were lost. Two
layers disagreeing about a decision is a bug signal -- the embark spent the
gold on a party its own raid layer had already written off.

The threshold is a declared convention, `VETO_MEAN_DEATHS`: a draw the
simulator expects to lose half the party on is re-drawn. The comps that ran
fine the same night read 0.3 - 0.9; the retreated one read 3.2 - 3.5.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Callable, Optional

from data.paths import GAME_DIR

#: A drawn party the simulator expects to lose this many heroes on is re-drawn.
VETO_MEAN_DEATHS = 2.0
#: The map's loot at the start of a raid, which the raid layer's retreat bill
#: credits to staying. No map exists at the embark, so a declared estimate:
#: below every fresh short raid's first "LOOT" line on 2026-09-27 (about
#: 1,400-2,250 gold). With 0 here the veto re-drew a 79%-intact, 0.6-death
#: party the raid layer, seeing the map's loot, would have kept.
EMBARK_LOOT_ESTIMATE = 1000.0
#: Episodes per candidate. The veto is a coarse gate, not a ranking.
VETO_EPISODES = 24

_FALLBACK_BATTLES = {"short": 5, "medium": 8, "long": 12}


def expected_battles(zone: str, band: str, quest_type: str,
                     game_dir: str = str(GAME_DIR)) -> int:
    """Fights a fresh map of this kind holds, from `map_generator.darkest`:
    the midpoints of `hallway_battle` and `total_room_battles`."""
    try:
        from data.map_generator import map_budgets              # noqa: PLC0415
        b = map_budgets(game_dir)
        row = (b.get((zone, band, quest_type)) or b.get((zone, band, "explore"))
               or {})
        hall, room = row.get("hallway_battle"), row.get("total_room_battles")
        if hall and room:
            return max(1, int(round(sum(hall) / 2 + sum(room) / 2)))
    except Exception:                                           # noqa: BLE001
        pass
    return _FALLBACK_BATTLES.get(band, 8)


def make_veto(model_path: str, *, episodes: int = VETO_EPISODES,
              threshold: float = VETO_MEAN_DEATHS,
              say=print) -> Optional[Callable]:
    """`veto(quest, seats) -> ""` to accept, else the reason. None if the
    model cannot be loaded -- then nothing is vetoed, as before."""
    try:
        from bridge.rollout import (PartyMember, QUEST_LENGTH_TO_BAND,  # noqa: PLC0415
                                    Rollout)
        rollout = Rollout(model_path)
    except Exception as exc:                                    # noqa: BLE001
        say(f"  [town] embark veto off - could not load {model_path} ({exc})")
        return None

    def veto(quest, seats) -> str:
        try:
            party = [replace(PartyMember.from_live_hero(s.hero.hero),
                             hp=None,                 # the Hamlet heals
                             skill_ids=tuple(s.skills) or
                             tuple(s.hero.hero.selected_skills))
                     for s in sorted(seats, key=lambda s: s.rank)]
            band = QUEST_LENGTH_TO_BAND.get(int(quest.length), "medium")
            fights = expected_battles(quest.dungeon, band, quest.quest_type)
            result = rollout.evaluate(party, zone_id=quest.dungeon,
                                      tier=int(quest.difficulty),
                                      num_combats=fights, length_band=band,
                                      episodes=episodes)
        except Exception as exc:                                # noqa: BLE001
            say(f"  [town] embark veto could not simulate ({exc}) - accepting")
            return ""
        line = (f"{fights} fights: P(finish intact) "
                f"{result.success_rate * 100:.0f}%, expected deaths "
                f"{result.mean_deaths:.1f}")
        # **The raid layer's own verdict, not a looser proxy for it.** Live
        # 2026-09-27: a Cove party at 38% intact and 1.2 expected deaths
        # passed the 2.0-deaths gate, and `advise_retreat` -- which the raid
        # asks at every room -- said RETREAT from the entrance and pulled the
        # party out after one fight: +20 stress on all four and the
        # provisions lost. Asking the same function here is what makes the
        # two layers agree.
        try:
            from bridge.retreat import QuestReward, advise_retreat  # noqa: PLC0415
            advice = advise_retreat(
                [s.hero.hero for s in seats], result,
                QuestReward(gold=int(getattr(quest, "gold_reward", 0) or 0)),
                loot_ahead_gold=EMBARK_LOOT_ESTIMATE)
            if advice.retreat:
                line += f"; the raid layer would RETREAT ({advice.line()})"
        except Exception as exc:                                # noqa: BLE001
            advice = None
            say(f"  [town] embark veto could not price a retreat ({exc})")
        say(f"  [town] VETO?  {line}")
        vetoed = (result.mean_deaths >= threshold
                  or (advice is not None and advice.retreat))
        return line if vetoed else ""

    return veto
