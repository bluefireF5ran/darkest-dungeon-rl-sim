"""One loop, every dungeon layer, one log — the thing you actually run beside
the game.

Until now the bridge was seven separate command lines. `advisor.py` polled the
save for combat; `route.py`, `curios.py`, `torchlight.py`, `camp.py` and
`retreat.py` each answered one question about one captured snapshot, when asked.
That is fine for developing a layer and unusable while playing: it makes the
human decide *which layer to consult*, which is most of the decision.

This module polls once and dispatches on where the party is:

* **in a battle** -> the combat recommendation, plus the retreat bill
* **between rooms** -> position, route, curios here, torch, campfire, retreat

and writes all of it to a single timestamped log.

## The other half: marking the advice against what was played

Every acceptance run so far ended the same way — a terminal full of
recommendations, a directory full of captured saves, and no machine-readable
record of *which recommendations were taken*. That had to be reconstructed from
memory, so "the advisor said rank 3 and I did something else" was testimony.

The debug channel cannot close that gap: it instruments the monster AI only, and
a grep for every equipped hero skill across a 445 KB capture returns 0 hits.
There is no behavioural-cloning data to be had that way.

But the **save diff** recovers most of it. Between two writes exactly one hero
usually acts, and what changed says what kind of turn it was:

| signal in the diff | turn |
|---|---|
| a provision stack shrank | provision |
| the actor's own rank changed (and no enemy acted) | move |
| a live enemy lost HP, or vanished from the enemy list | attack, on that enemy |
| a hero gained HP | heal, on that hero |
| a hero lost stress, or gained a buff | support |

That identifies the *kind and the target*, which is what PLAYBOOK-level
disagreements are actually about. It does **not** identify the skill: several
plain-damage skills are indistinguishable in a diff, and `game_trace.py`'s
`Battle Random int range A,B` only separates the few with unique damage bands.
So this marks `MATCH` / `DIFFER`, never "you used Zealous Accusation".

**A miss leaves no trace at all**, and neither does a pass — those come out `?`,
and the summary prints the `?` rate so the instrument's own blind spot is
visible rather than assumed away. The rule everywhere is that ambiguous evidence
marks `?`: two heroes acting between writes, or two independent signals firing
at once, are never resolved by guessing which one the advice predicted. That
would bias every measurement towards agreement, which is the one direction a
tool built to find disagreements must not lean.

Only **non-provisional** advice is marked. `advisor.recommend` looks ahead past
pending enemy turns so the screen is not blank while they play, and that block
is explicitly labelled provisional — scoring it would score a prediction about a
board that had not happened yet.

```bash
python bridge/run.py --model models/dd_combat_ppo_full_expedition.zip --log run1.log
python bridge/run.py --model models/dd_combat_ppo_full_expedition.zip --survival 12
python bridge/run.py --raid <snap> --roster <snap> --once   # replay one capture
```
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import NamedTuple, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bridge.advisor import DEFAULT_TOTAL_COMBATS, Advisor
from bridge.camp import advise_from_live as advise_camp_live
from bridge.curios import CurioHero, advise as advise_curio
from bridge.dungeon_map import read_dungeon_map
from bridge.journal import Journal, diff_town, town_snapshot
from bridge.live_state import GAME_DIR, LiveState, _stage, find_profile, read_live_state
from bridge.overlay_slots import MOVE_SLOT, PASS_SLOT, RETREAT_SLOT
from bridge.route import Quest, plan_route, read_position, read_quest
from bridge.lootplan import plan_loot
from bridge.seed import SAVE_STAT_GUARD_MARKER, SeedError
from bridge.torchlight import advise_from_live as advise_torch_live
from bridge.torchlight import RADIANT_FLOOR, torches_to_restore
from bridge.torchlight import describe as describe_torch
from data.rules import RulesLoader
from engine.combat_engine import IN_COMBAT_PROVISION_IDS

# Quest `length` 1-4 is Short/Medium/Long/Exhausting per the localization, and
# the camp scheduler speaks in the simulator's three bands. Exhausting has no
# map-generator size of its own, so it borrows long's — the same choice
# `EXPEDITION_LENGTH_BANDS` makes.
LENGTH_BANDS = {1: "short", 2: "medium", 3: "long", 4: "long"}

# Kinds the save diff cannot tell apart. A skill that heals for its whole
# payload and one that buffs and incidentally heals both read as "a hero got
# better", and a heal on a full-HP ally reads as a buff. Treating them as one
# family means a MATCH here is a claim about *who* was helped, not about which
# skill did it — which is exactly as much as the evidence supports.
KIND_FAMILY = {"heal": "help", "support": "help"}

VERDICTS = ("MATCH", "DIFFER", "?")

# Seconds to let the operator click the game before layer 3 starts polling.
# Only used with --act. Not cosmetic: the first turn would otherwise arrive
# with the terminal focused, and a turn skipped for focus is lost for good.
GRACE_SECONDS = 8
#: How many other heroes to try when a battle turn will not land. Bounded by
#: the party, and deliberately below it -- past three the answer is not "a
#: different hero" and every extra try is a real input sent into the game.
MAX_TURN_SKIPS = 3
#: A battle save that has not been written for this long, while the bridge has
#: nothing pending, means the game is waiting for a hero nobody is advising.
#:
#: The failed-send path cannot catch every one of these, because a send can be
#: FALSELY CONFIRMED: `_send_and_confirm` watches the save for any change, and
#: an enemy acting is a change. Then nothing failed, the counters did not move,
#: the dedupe key is identical and the bridge goes quiet with the game waiting.
#: Seen live: three real turns played, then silence with the Runaway on Death's
#: Door and the Crusader's bar up.
#:
#: Comfortably longer than a hero turn resolving (measured 0.3-2.5 s) and than
#: a run of enemy turns (measured at 17 s of silence between two hero turns),
#: so an ordinary fight never trips it.
STUCK_SECONDS = 25.0
#: The same heartbeat OUT of combat, where it only fires on a genuinely frozen
#: room (walking and looting write the save). Measured 2026-09-28 over 40 raid
#: logs: 9.5 h of 15.2 h was out of combat and the median gap between room
#: plans was exactly 25 s -- the bridge idling until this timer fired.
ROOM_STUCK_SECONDS = 12.0
# How many room re-plans in a row, on a save that has not moved AT ALL, before
# the loop says so in a line an outer runner can act on.
#
# **The thing that reports a stall was defeating the thing that detects one.**
# `tools/overnight.py` calls a phase stalled when its log stops growing for
# 180 s -- and `note_room_stuck` prints a full room plan every 25 s, so a
# dungeon the bridge could not leave looked like the busiest phase of the
# night. Measured live 2026-09-20: the raid layer advised RETURN, no actuator
# existed for it, and the iteration re-planned the identical room for the
# whole 900 s allowance while printing continuously.
#
# 12 is ~144 s of a completely unwritten save at the 12 s room heartbeat
# (`ROOM_STUCK_SECONDS`; it was 6 at 25 s), which is far longer than any
# animation and short of the 180 s the outer loop already allows. The recovery
# itself stays UNBOUNDED -- `note_room_stuck` keeps re-planning after this,
# exactly as its docstring says it must. This only makes the situation
# SAYABLE; what to do about it is the caller's.
ROOM_STUCK_GIVE_UP = 12
#: Stuck re-plans after which a loot stop the raid is waiting on is given up.
LOOT_HOLD_GIVE_UP = 6
#: 'no room' modals after which a met objective means go home.
PACK_FULL_RETURN = 2
#: Area changes the loot walk may steer for one goal before giving it up.
LOOT_GOAL_MAX_PLANS = 6
#: Emitted once per stall. `tools/overnight.py` watches for it.
ROOM_STUCK_LINE = "-- the dungeon is not responding --"


def _family(kind: str) -> str:
    return KIND_FAMILY.get(kind, kind)


# ── what was advised ──

def advised_kind(engine, action) -> str:
    """The advice's turn kind, in the same vocabulary the diff speaks."""
    sid = action.skill_id
    if sid.startswith("__move"):
        return "move"
    if sid.startswith("__provision_"):
        return "provision"
    if sid == "__pass__":
        return "pass"
    if sid == "__retreat__":
        return "retreat"
    actor = next((h for h in engine.heroes if h.id == action.hero_id), None)
    tier = actor.skill_tier if actor is not None else 4
    # **A SELF-cast is not an attack.** The Abomination's Absolution heals and
    # relieves stress on the caster; `_is_ally_skill` does not count a
    # self-target, so it fell through to "attack" and the ledger reported
    # "DIFFER advised attack, played heal" seven times in two raids
    # (2026-09-24) for turns that were played exactly as advised.
    self_cast = action.target_id is None or action.target_id < 0
    if engine._is_ally_skill(sid) or self_cast:
        sd = engine.get_skill_data(sid, level=tier)
        return "heal" if (sd is not None and sd.heal_min is not None) else "support"
    return "attack"


def _hero_rank(engine, hero_id) -> int:
    """Where the acting hero is standing, 0-based, or -1.

    `CombatantState.position` IS the rank -- `CombatEngine.reset` assigns
    `position = index` and the party is contiguous from the front -- so this
    is a lookup rather than a derivation.
    """
    actor = next((h for h in engine.heroes if h.id == hero_id), None)
    return -1 if actor is None else int(getattr(actor, "position", -1))


def advised_slot(engine, action) -> tuple[str, int]:
    """`(side, rank)` the player has to click, or `("", -1)` for neither.

    This is the *screen* question, not the identity question `advised_target`
    answers, and it is deliberately the raw `action.target_id`: that is the rank
    the game accepts a click on. A size-2 monster answers to both of the ranks it
    covers, so there is no need to normalise to its front rank — and doing so
    would move the marker off the half of the sprite the advice pointed at.
    """
    if (action.skill_id.startswith("__provision_")
            or action.skill_id in ("__pass__", "__retreat__")):
        return "", -1
    if action.target_id is None or action.target_id < 0:
        # **A SELF-CAST still has a rank to click: the caster's own.** The
        # engine encodes "no target" as a negative id, and this returned
        # `("", -1)` for it, so `plan_battle` refused with "advice names no
        # target rank to click" -- which silently disabled every self-buff in
        # the game. Bulwark of Faith, Take Aim, Bolster, Solemnity: a class of
        # skill the bridge could recommend and never play.
        #
        # Caught by the overnight loop stalling on a Crusader at 7/33 HP whose
        # best move was Bulwark of Faith, with a Large Corpse the only thing
        # left standing. The refusal was correct about the DATA and wrong
        # about the screen: DD casts a self-targeted skill by pressing the
        # digit and clicking the caster, exactly like any other target.
        actor_rank = _hero_rank(engine, action.hero_id)
        return ("hero", actor_rank) if actor_rank >= 0 else ("", -1)
    if action.skill_id.startswith("__move"):
        return "hero", action.target_id
    actor = next((h for h in engine.heroes if h.id == action.hero_id), None)
    sd = (engine.get_skill_data(action.skill_id, level=actor.skill_tier)
          if actor is not None else None)
    return ("hero" if (sd is not None and sd.targets_allies) else "enemy"), action.target_id


def _actor_launches(state: LiveState, adv, action) -> tuple:
    """The acting hero's four equipped skills as `.launch` rank lists.

    In ACTION-BAR order -- `selected_skills`, which is what a digit selects --
    and `None` for anything the store cannot resolve, so an unreadable skill
    constrains nothing downstream. Returns `()` when there is no hero to read,
    which reads the same way: no opinion.

    Skill LEVEL is deliberately not consulted. `.launch` is a property of the
    skill and every shipped rank of it repeats the same list, so keying on the
    hero's gear rank would add a way to be wrong for nothing.
    """
    hero_id = getattr(action, "hero_id", -1)
    if not (0 <= hero_id < len(state.heroes)):
        return ()
    store = getattr(adv, "store", None)
    index = getattr(store, "skill_index", None)
    if index is None:
        return ()
    try:
        table = index()
    except Exception:                                        # noqa: BLE001
        return ()
    out = []
    for sid in (state.heroes[hero_id].selected_skills or ()):
        data = next((table.get((sid, lvl)) for lvl in range(6)
                     if table.get((sid, lvl)) is not None), None)
        launch = getattr(data, "launch", None) if data is not None else None
        out.append(tuple(launch) if launch else None)
    return tuple(out)


def _hero_class(state: LiveState, action) -> str:
    """The acting hero's class id, or "" when there is no hero to read.

    Used to cross-check the SAVE's answer against the screen's -- see
    `bridge/heropanel.py`. Empty reads as "no opinion", like everything else
    the actuator is handed.
    """
    hero_id = getattr(action, "hero_id", -1)
    if not (0 <= hero_id < len(state.heroes)):
        return ""
    return getattr(state.heroes[hero_id], "hero_class", "") or ""


def _continues_turn(adv, action) -> bool:
    """Does this skill hand the hero its turn straight back?

    `.is_continue_turn` -- a stance change, and the Duelist's and the Sibyl's
    whole class identity. The engine has honoured it since the Abomination
    (`combat_engine.py`, "Provisions AND any skill with .is_continue_turn");
    the bridge did not know it existed, so the actuator sent the stance change,
    marked the turn played and waited for a turn the game had already given
    back. Fran, watching it: *"the duelist change forms but didn't do the
    second action ... the model did the second action and didn't expect it as
    it expects 1 action per hero"*.

    False when the skill cannot be resolved, which is the same "no opinion"
    the rest of this file uses: an unknown skill is treated as ending the
    turn, and the cost of being wrong that way is one poll rather than a
    double-press.
    """
    sid = getattr(action, "skill_id", "") or ""
    if not sid or sid.startswith("__"):
        return False
    index = getattr(getattr(adv, "store", None), "skill_index", None)
    if index is None:
        return False
    try:
        table = index()
    except Exception:                                        # noqa: BLE001
        return False
    for lvl in range(6):
        data = table.get((sid, lvl))
        if data is not None:
            return bool(getattr(data, "is_continue_turn", False))
    return False


def advised_button(state: LiveState, action) -> int:
    """Which button in the game's action bar the advice means, 1-based.

    The action space indexes the hero's **full 7-skill movepool** so that slot
    *k* names the same skill for a class whatever a comp equips (see
    `env/gym_env.py`). The screen shows only the four the hero actually brought,
    in the order the save lists them — so the on-screen slot is the index in
    `LiveHero.selected_skills`, and mapping through the action index instead
    would point at the wrong icon for every hero whose loadout is not the first
    four of their movepool.

    Returns -1 when there is no button: a provision comes from the inventory.
    Abomination also lands here, deliberately — he is never skill-restricted, so
    the engine may offer a beast-form skill that is not in `selected_skills` at
    all, and pointing at a button that is not there would be worse than
    pointing at nothing.
    """
    sid = action.skill_id
    if sid.startswith("__move"):
        return MOVE_SLOT
    if sid == "__pass__":
        return PASS_SLOT
    if sid == "__retreat__":
        # Not one of the four skill icons -- pointing at a slot would point at
        # a skill the player would then actually use -- but not "no control"
        # either. **It is the raid HUD's X**, top left, and the game says so:
        # `[pc]str_help_raid_combat_4` reads "[CLICK] on the retreat icon
        # (looks like an "X") to attempt to retreat from the combat".
        #
        # This returned -1 for as long as retreat existed, so `plan_battle`
        # could never play the policy's own top pick and fell down the
        # shortlist to an attack instead. Measured live on 2026-09-05: in a
        # fight both rulers said to leave -- the policy ranked RETREAT at 72
        # to 78% and `advise_retreat` priced staying as a certain death -- and
        # the bridge attacked anyway, three turns running, with the Flagellant
        # on 1 of 22 HP.
        return RETREAT_SLOT
    if sid.startswith("__provision_"):
        return -1
    hero_id = action.hero_id
    if not (0 <= hero_id < len(state.heroes)):
        return -1
    equipped = list(state.heroes[hero_id].selected_skills)
    return equipped.index(sid) + 1 if sid in equipped else -1


def advised_target(engine, state: LiveState, action) -> tuple[frozenset[int], str]:
    """`(save guids the advice would touch, label)` — a SET, because an AoE has
    no single target and comparing one against one was a coin toss.

    Divine Comfort is `.target @~1234`: it heals the whole party and the caster.
    The advice printed one nominal rank, `observe` kept one arbitrary member of
    the healed set (`healed[0]`, dict order), and `judge` compared the two. When
    they happened to coincide it said MATCH and when they did not it said DIFFER,
    on the same play. Three of five DIFFERs in the first two-process live run
    were that, and every one of them was a turn the player had followed exactly.

    The footprint comes from **the engine's own `_resolve_action_targets`**, not
    from re-reading `.target` here: an AoE is re-resolved at execution against
    the live board, and a second implementation of that would drift from it
    silently. Resolution still goes through `occupied_positions`, never
    `.position` — 61 of 246 monsters are size 2+ and answer to every rank they
    stand on.

    Corpses are included. They became legal targets on 2026-08-15 and this
    function still filtered on `not e.is_dead`, so advice aimed at a corpse
    resolved to an empty set — which `judge` then read as "no target claim to
    check" and passed unconditionally.
    """
    if action.target_id is None or action.target_id < 0:
        return frozenset(), "self"
    # **A move is judged hero-side, and against a RANK.** `__move__` is not a
    # skill id, so `sd` stays None, `ally` stays False, and the fallback below
    # resolved the DESTINATION RANK against the enemy list: "MOVE to rank 1"
    # printed as "advice pointed at Brigand Bloodletter (slot 1)" and every
    # move the player actually made as advised was marked DIFFER. Seen live on
    # the tutorial. It corrupts `tools/agreement.py` the same way, since that
    # shares this marking -- so the -2.9pp the newly-legal Move action was said
    # to cost was measured through this bug and has to be re-measured.
    if action.skill_id.startswith("__move"):
        by_id = {sim.id: live.guid
                 for live, sim in zip(state.heroes, engine.heroes)}
        me = by_id.get(action.hero_id)
        return ((frozenset() if me is None else frozenset({me})),
                f"rank {action.target_id + 1}")
    actor = next((h for h in engine.heroes if h.id == action.hero_id), None)
    sd = None
    if actor is not None and not action.skill_id.startswith("__"):
        sd = engine.get_skill_data(action.skill_id, level=actor.skill_tier)
    rank = action.target_id
    ally = bool(sd is not None and sd.targets_allies)

    hits: list = []
    if sd is not None:
        try:
            hits = engine._resolve_action_targets(action, sd, ally)
        except Exception:                          # noqa: BLE001
            hits = []
    if not hits:
        pool = engine.heroes if ally else engine.enemies
        hits = [c for c in pool if rank in c.occupied_positions
                and (not c.is_dead or c.is_corpse)]

    if ally:
        guids = {sim.id: live.guid for live, sim in zip(state.heroes, engine.heroes)}
        names = {sim.id: sim.name for sim in engine.heroes}
    else:
        guids = {sim.id: live.battle_guid
                 for live, sim in zip(state.enemies, engine.enemies)}
        names = {sim.id: (live.name or live.enemy_id)
                 for live, sim in zip(state.enemies, engine.enemies)}

    found = frozenset(g for g in (guids.get(c.id) for c in hits) if g is not None)
    if not found:
        return frozenset(), f"rank {rank + 1}"
    # The label still names the ONE thing the player clicks — the nominal rank —
    # even when the footprint is wider. The set is for judging, not for reading.
    nominal = next((c for c in hits if rank in c.occupied_positions), hits[0])
    label = names.get(nominal.id, nominal.name)
    return found, (label if len(found) == 1 else f"{label} +{len(found) - 1}")


# ── what happened ──

def _guard_map(state: LiveState) -> dict[int, int]:
    """`{guarded guid: guardian guid}` for both sides.

    A guarded target's hits land on its guardian, so without this the diff sees
    the wrong combatant lose HP and the marker reports a disagreement on a turn
    the player followed exactly. Three of three DIFFERs in the second live run
    were one Pelagic Guardian standing in front of one Pelagic Grouper — and the
    bridge already *printed* the relation on the same screen
    (`octotank_A guards fishman_harpoon_A`), it simply was not judged with.

    `stat_type` 27 is the marker, it sits on the GUARDED combatant, and its
    `guarded_guid` names the guardian — the same reading `bridge/seed.py` uses.
    26 is riposte, not guard's other half.
    """
    out: dict[int, int] = {}
    for c in list(state.heroes) + list(state.enemies):
        guid = getattr(c, "guid", None)
        if guid is None:
            guid = getattr(c, "battle_guid", None)
        if guid is None:
            continue
        for buff in getattr(c, "buffs", ()) or ():
            if buff.stat_type == SAVE_STAT_GUARD_MARKER and buff.guarded_guid:
                out[guid] = buff.guarded_guid
    return out


@dataclass(frozen=True)
class Board:
    """The measurable part of a `LiveState`, keyed by save ids."""
    battle_round: int = 0
    hero_hp: dict[int, float] = field(default_factory=dict)
    hero_stress: dict[int, float] = field(default_factory=dict)
    hero_rank: dict[int, int] = field(default_factory=dict)
    hero_turns: dict[int, int] = field(default_factory=dict)
    hero_buffs: dict[int, int] = field(default_factory=dict)
    hero_names: dict[int, str] = field(default_factory=dict)
    enemy_hp: dict[int, float] = field(default_factory=dict)
    enemy_names: dict[int, str] = field(default_factory=dict)
    # Save-list position, purely so a label can tell duplicates apart. A real
    # encounter fields three identical Carrion Eaters on the same HP, and
    # "advised Carrion Eater, played Carrion Eater" reads as a tooling bug when
    # it is a genuine disagreement about which one to hit. NOT a rank: a size-2
    # monster covers two of them.
    enemy_slot: dict[int, int] = field(default_factory=dict)
    enemy_turns: dict[int, int] = field(default_factory=dict)
    provisions: dict[str, int] = field(default_factory=dict)
    # Corpses live in their OWN dict, not in `enemy_hp`. Both halves matter and
    # they pull against each other, which is why one dict cannot serve: a living
    # enemy leaving `enemy_hp` is the kill signal, and a killed enemy keeps its
    # battle guid while turning into a corpse — so folding corpses in would turn
    # every kill into "an enemy whose HP merely changed". Leaving them out
    # entirely, which is what this did until 2026-08-15, makes an attack on a
    # corpse invisible instead. Corpses became legal targets that same day and
    # this was not updated with them, so a followed recommendation to break one
    # was marked by whatever unrelated signal happened to be left over.
    corpse_hp: dict[int, float] = field(default_factory=dict)
    corpse_names: dict[int, str] = field(default_factory=dict)
    # guarded guid -> the guid eating its hits. The marker sits on the GUARDED
    # combatant and names its guardian (`bridge/seed.py`, and the game's own
    # `defender` data settles the direction), so this is read straight off it.
    guarded_by: dict[int, int] = field(default_factory=dict)

    @classmethod
    def of(cls, state: LiveState) -> "Board":
        live = [e for e in state.enemies if not e.is_corpse and e.battle_guid is not None]
        dead = [e for e in state.enemies if e.is_corpse and e.battle_guid is not None]
        return cls(
            battle_round=int(state.battle_round or 0),
            hero_hp={h.guid: h.hp for h in state.heroes},
            hero_stress={h.guid: h.stress for h in state.heroes},
            hero_rank={h.guid: i for i, h in enumerate(state.heroes)},
            hero_turns={h.guid: h.turns_taken for h in state.heroes},
            hero_buffs={h.guid: len(h.buffs) for h in state.heroes},
            hero_names={h.guid: h.hero_class for h in state.heroes},
            enemy_hp={e.battle_guid: e.hp for e in live},
            enemy_names={e.battle_guid: (e.name or e.enemy_id) for e in live},
            enemy_slot={e.battle_guid: e.slot for e in live},
            enemy_turns={e.battle_guid: e.turns_taken for e in live},
            provisions=dict(state.provisions or {}),
            corpse_hp={e.battle_guid: e.hp for e in dead},
            corpse_names={e.battle_guid: f"{e.name or e.enemy_id} corpse" for e in dead},
            guarded_by=_guard_map(state),
        )



#: P(the whole party dies) above which the survival layer may pull the party
#: out of a FIGHT. Fran, 2026-09-23: *"unless the probability exceeds a really
#: high threshold of wiping"*. Everything below leaves the fight to the model.
FIGHT_RETREAT_WIPE = 0.8

@dataclass(frozen=True)
class Update:
    """One structured event, for a front-end that is not a terminal.

    `bridge/overlay.py` draws from these. It exists so the overlay never has to
    parse the printed block back into structure the loop already had — a screen
    marker keyed on a regex over "-> SKILL on rank 3 (...)" would break the first
    time a label changed, and silently, by pointing at the wrong rank.
    """
    phase: str                       # battle | room | verdict | over
    headline: str = ""
    detail: str = ""
    side: str = ""                   # hero | enemy | ""
    rank: int = -1                   # 0-based; -1 = nothing to point at
    confidence: float = 0.0
    provisional: bool = False
    # How many enemy turns land before the advised hero, and which of those
    # enemies can move somebody. A front-end has to be able to say both: the
    # first is why the advice is early, the second is why its target rank may
    # not survive the wait.
    enemies_first: int = 0
    movers: tuple[str, ...] = ()
    # Which button in the game's action bar: 1-4 the equipped skills, 5 move,
    # 6 pass. -1 when there is none to point at (a provision is used from the
    # inventory, not from the bar).
    skill_slot: int = -1
    # `.is_continue_turn`: the hero acts and KEEPS the turn. The actuator must
    # not mark the turn played after one of these, or the second action never
    # goes out -- see `_continues_turn`.
    continues_turn: bool = False
    #: The acting hero's class, and every class in the party. `bridge/
    #: heropanel.py` needs both: the first to compare against, the second as
    #: the pool it matches within.
    actor_class: str = ""
    #: A provision for the acting hero before the skill: `(item, why)`.
    #: Items are FREE actions in a fight (measured live 2026-09-26).
    battle_item: tuple = ()
    #: The SURVIVAL layer says leave. Not the policy's opinion -- `advise_retreat`
    #: prices the whole rest of the dungeon and applies the death floor, and its
    #: verdict had no way to reach the actuator at all. See `plan_battle`.
    retreat_advised: bool = False
    party_classes: tuple = ()
    #: The acting hero's rank ACCORDING TO THE SAVE, 1-based. The save keeps
    #: the marching order and never records an in-combat swap, so this is the
    #: quantity `bridge/actionbar.rank_from_pattern` exists to cross-check.
    actor_rank: int = 0
    verdict: str = ""
    # Identifies the TURN this advice is for: (round, hero guid, turns taken,
    # enemies_first). `Session.battle` already dedupes on it, but an actuator
    # must be able to prove for itself that it has not acted on this turn
    # before -- a double-press is not a cosmetic bug there, it plays a second
    # move. Carried on the record rather than recomputed, so the two can never
    # disagree about what "the same turn" means.
    turn_key: tuple = ()
    # The engine skill id behind the advice (`take_aim`, not "Tracking Shot",
    # and `__move__` for a move). An actuator needs it to report back WHAT it
    # played: per-battle limits are not in the save, so the only record of
    # "Take Aim is spent" is the bridge's own memory of having played it.
    skill_id: str = ""
    # The out-of-combat decision as structure, not as the printed block.
    # `room_lines()` squeezing `detail` onto a card is fine for a HUD; an
    # actuator keyed on that text would break silently the first time a
    # label changed, by walking the wrong way. Same reason the combat side
    # carries side/rank/skill_slot instead of the headline.
    room_advice: object = None
    # The decoded `persist.map.json` for this poll. Carried because leaving a
    # room needs the map's GEOMETRY -- `Tile.mappos` against the pixels the
    # panel is drawing -- and re-reading and re-decoding the save inside the
    # actuator would be a second read of a file that is being written under us.
    # `object` for the same reason `room_advice` is: `bridge/act.py` must not
    # import the reader to accept the message.
    dungeon: object = None
    area: str = ""
    # Which layer the verdict belongs to. The overlay's badge is about the
    # COMBAT turn, so it must not be repainted by a route or curio verdict that
    # happens to arrive between two fights — it would sit there saying DIFFER
    # over a combat recommendation nobody had judged yet.
    layer: str = ""
    tally: tuple[tuple[str, int], ...] = ()
    # The rest of the advisor's shortlist, best first, WITHOUT the head.
    #
    # **Not decoration: this is what stops the actuator deadlocking.** The
    # policy can rank first an action the game draws no control for — retreat
    # is its own button and the tutorial has none, a provision comes from the
    # inventory, the Abomination can be offered a beast-form skill that is not
    # on his bar. `plan_battle` refused those, `ActRunner` returned without
    # marking the turn played, and the identical refusal republished every
    # poll for the rest of time.
    #
    # A fallback is only ever taken for a refusal about the SCREEN. The timing
    # refusals — provisional, enemy turns first, a pending repositioner — are
    # about the turn not being due, and every candidate shares them, so those
    # still refuse outright.
    alternatives: tuple["Alternative", ...] = ()
    # Put the torch out BEFORE playing this turn. Set only when the torch
    # layer says shutdown AND `about_to_win` says this move ends the fight --
    # see `_battle_torch`. Darkness is a straight loss on any earlier turn.
    snuff_torch: bool = False
    # The acting hero's four equipped skills as their `.launch` rank lists, in
    # action-bar order, and how many heroes are on the board.
    #
    # **Carried so the actuator can tell where the hero REALLY is.** The save
    # records the marching order and never updates it for an in-combat swap,
    # so when the party has been reordered every candidate the advisor ranks
    # can be a skill the game will not accept. The drawn bar says which are
    # legal; these two are what turn that pattern back into a rank. `None` for
    # a slot whose skill the store could not resolve, which then constrains
    # nothing -- see `bridge/actionbar.py::predicted_pattern`.
    actor_launches: tuple = ()
    party_size: int = 0


@dataclass(frozen=True)
class Alternative:
    """One runner-up move, in exactly the fields `plan_battle` reads.

    A flat record rather than a second `Update`: the planner needs four
    things to build a plan, and handing it a whole Update would invite it to
    read a field (`turn_key`, `provisional`) that belongs to the turn and not
    to the candidate.
    """
    skill_slot: int = -1
    side: str = ""
    rank: int = -1
    skill_id: str = ""
    headline: str = ""
    confidence: float = 0.0
    continues_turn: bool = False


@dataclass(frozen=True)
class Observed:
    kind: str                      # attack|heal|support|move|provision|?
    guid: Optional[int]
    label: str
    why: str = ""
    # EVERY combatant that showed this effect, not just the one named above.
    # `guid` stays the headline for printing; `guids` is what gets judged, so an
    # AoE is compared as a set against a set instead of one arbitrary member
    # against one nominal rank.
    guids: frozenset[int] = field(default_factory=frozenset)


def observe(before: Board, after: Board, actor_guid: int) -> Optional[Observed]:
    """What the actor did, or None while it is still their turn.

    Several signals firing at once is usually **one skill**, not ambiguity: the
    lunge family (Duelist's Advance, Rampart, Lunge) moves the performer and
    attacks in the same turn, and Judgement damages an enemy while healing the
    Vestal. So the signals are ranked by how directly they evidence a turn —
    nothing but an attack takes HP off an enemy on a hero's turn, which makes
    that the strongest — and the ones not chosen are reported alongside rather
    than hidden. Conflating move+attack into `?` cost 23 of 28 unresolved turns
    on the first replay of the existing captures, all of them Duelist's Advance.

    The ranking is fixed and derived from the game's skill design; it is never
    "pick whichever signal the advice predicted", which would make the
    measurement agree with itself. What genuinely cannot be attributed still
    resolves to `?`: two heroes advancing their turn counters between one pair
    of writes, an item used in the same window as a skill, or no change at all.
    """
    acted = [g for g, t in after.hero_turns.items() if t > before.hero_turns.get(g, t)]
    if actor_guid not in acted:
        return None
    if len(acted) > 1:
        return Observed("?", None, "", f"{len(acted)} heroes acted between saves")
    # Counted from turn-counter advances only. An enemy that acted and then died
    # in the same window is invisible here — but treating every *vanished* enemy
    # as having acted is worse, because the overwhelmingly common reason one
    # vanishes is that the hero just killed it. That version reported a phantom
    # enemy turn on every killing blow; the honest gap is the narrow one.
    enemies_acted = sum(1 for g, t in after.enemy_turns.items()
                        if t > before.enemy_turns.get(g, t))
    aside = f" ({enemies_acted} enemy turn(s) in between)" if enemies_acted else ""

    # An item is not a skill, so a provision stack shrinking is the one signal
    # that cannot share a turn with the others.
    spent = [pid for pid, held in before.provisions.items()
             if pid in IN_COMBAT_PROVISION_IDS
             and after.provisions.get(pid, held) < held]

    signals: list[Observed] = []
    hurt = {g: hp - after.enemy_hp.get(g, 0.0)
            for g, hp in before.enemy_hp.items()
            if g not in after.enemy_hp or after.enemy_hp[g] < hp}
    if hurt:
        guid = max(hurt, key=lambda g: hurt[g])
        killed = " (killed)" if guid not in after.enemy_hp else f" (-{hurt[guid]:.0f} hp)"
        extra = f", {len(hurt) - 1} other enemy also hurt" if len(hurt) > 1 else ""
        signals.append(Observed("attack", guid,
                                before.enemy_names.get(guid, str(guid)),
                                f"{killed}{extra}", frozenset(hurt)))
    # Breaking a corpse is an attack, and it was invisible here until the board
    # started carrying corpses. Ranked BELOW damage to a living enemy: when both
    # show up in one window the living one is the more informative claim, and an
    # AoE that clips a corpse on its way through is the common way both appear.
    #
    # A corpse that VANISHED is only counted when the round did not advance.
    # Rot is resolved at end of round, so inside a round a corpse can only leave
    # by being destroyed — across a round boundary the two are indistinguishable
    # and this refuses to guess.
    same_round = after.battle_round == before.battle_round
    broke = {g: hp - after.corpse_hp.get(g, 0.0)
             for g, hp in before.corpse_hp.items()
             if (g in after.corpse_hp and after.corpse_hp[g] < hp)
             or (g not in after.corpse_hp and same_round)}
    if broke:
        guid = max(broke, key=lambda g: broke[g])
        gone = " (destroyed)" if guid not in after.corpse_hp else f" (-{broke[guid]:.0f} hp)"
        signals.append(Observed("attack", guid,
                                before.corpse_names.get(guid, str(guid)),
                                gone, frozenset(broke)))
    healed = [g for g, hp in before.hero_hp.items() if after.hero_hp.get(g, hp) > hp]
    if healed:
        extra = f", {len(healed)} heroes healed" if len(healed) > 1 else ""
        signals.append(Observed("heal", healed[0],
                                before.hero_names.get(healed[0], str(healed[0])),
                                extra, frozenset(healed)))
    # Only trust a rank change when nobody could have pushed or pulled them.
    if (not enemies_acted
            and after.hero_rank.get(actor_guid) != before.hero_rank.get(actor_guid)):
        signals.append(Observed("move", actor_guid,
                                f"rank {after.hero_rank.get(actor_guid, -1) + 1}",
                                "", frozenset({actor_guid})))
    # Stress only ever goes UP from an enemy turn, so a drop is safe to read
    # even when monsters acted in between; a buff appearing is not.
    soothed = [g for g, s in before.hero_stress.items()
               if after.hero_stress.get(g, s) < s]
    buffed = [g for g, n in before.hero_buffs.items()
              if after.hero_buffs.get(g, n) > n]
    helped = soothed or (buffed if not enemies_acted else [])
    if helped:
        signals.append(Observed("support", helped[0],
                                before.hero_names.get(helped[0], str(helped[0])),
                                "", frozenset(helped)))

    if spent and signals:
        kinds = ", ".join([f"provision {spent[0]}"] + [s.kind for s in signals])
        return Observed("?", None, "", f"an item and a skill in one window ({kinds}){aside}")
    if spent:
        return Observed("provision", None, spent[0], aside)
    if not signals:
        return Observed("?", None, "",
                        "nothing observable changed — a miss, a pass, or a "
                        "buff the diff cannot see" + aside)
    chosen = signals[0]
    also = ", ".join(s.kind for s in signals[1:])
    return Observed(chosen.kind, chosen.guid, chosen.label,
                    chosen.why + (f", also {also}" if also else "") + aside,
                    chosen.guids)


def who(board: Board, guid: Optional[int], fallback: str = "") -> str:
    """A label that survives duplicates — the thing plain names do not."""
    if guid is None:
        return fallback
    if guid in board.enemy_names:
        return f"{board.enemy_names[guid]} (slot {board.enemy_slot.get(guid, 0) + 1})"
    if guid in board.hero_names:
        return f"{board.hero_names[guid]} (rank {board.hero_rank.get(guid, 0) + 1})"
    return fallback or str(guid)


def judge(kind: str, targets: frozenset[int], obs: Observed,
          before: Board, label: str = "") -> tuple[str, str]:
    """`(verdict, reason)` — MATCH, DIFFER or `?`.

    Set against set. `targets` is everything the advised action would have
    touched and `obs.guids` is everything that visibly changed, so an AoE is
    judged on its whole footprint instead of on one nominal rank.

    The permissiveness this buys is real and is therefore **printed, not
    hidden**: a single-target heal aimed inside a party-wide footprint does read
    as MATCH, so the reason line carries "n of m" and a shortfall is visible on
    the line rather than folded into the tally. Making it stricter would mean
    knowing who *could* have shown the effect — who was actually damaged — and
    the board carries current HP, not max.
    """
    if obs.kind == "?":
        return "?", obs.why
    # A pass leaves no trace, so a bare rank change is exactly as consistent
    # with "passed, then got shoved" as with "chose to move" — 46 of 246
    # monsters can reposition, and the diff cannot tell a pull from a step.
    # Reported from live play: advised pass, player passed, marked DIFFER on a
    # rank change an enemy caused. `?` is the bucket for what the instrument
    # cannot tell, and claiming a disagreement here manufactures one.
    if kind == "pass" and obs.kind == "move":
        return "?", ("a pass leaves no trace, and this rank change could be a "
                     "shove — indistinguishable")
    if _family(obs.kind) != _family(kind):
        return "DIFFER", f"advised {kind}, played {obs.kind}"
    # A move names a DESTINATION RANK, and the one thing the diff can see is
    # the actor's new rank -- which `observe` already carries in its label. A
    # guid set cannot answer "did they go where they were told" (the actor is
    # the only guid on either side of it), so the ranks are compared directly.
    if kind == "move" and obs.kind == "move":
        if label and obs.label and label != obs.label:
            return "DIFFER", f"advised a move to {label}, ended on {obs.label}"
        return "MATCH", (f"to {obs.label}, as advised" if obs.label else obs.why)
    if not targets or not obs.guids:
        return "MATCH", obs.why
    # A guarded target's hits land on its guardian, so the guardian standing in
    # front of an advised target IS that target as far as the click goes: the
    # player aims where told and the save records someone else losing HP. The
    # guardians widen what counts as reached, but never the FOOTPRINT the count
    # is reported against — a redirected single-target hit is 1 of 1, not 1 of 2.
    guardians = {g for t in targets if (g := before.guarded_by.get(t)) is not None}
    reached = obs.guids & (targets | guardians)
    if not reached:
        named = ", ".join(sorted(who(before, g) for g in targets))
        return "DIFFER", f"same kind, advice pointed at {named}"
    if reached != obs.guids:
        outside = ", ".join(sorted(who(before, g) for g in obs.guids - targets - guardians))
        return "DIFFER", f"same kind, but {outside} is outside what was advised"
    why = obs.why
    if reached & guardians:
        blocked = ", ".join(sorted(who(before, g) for g in reached & guardians))
        why += f" (guarded — the hit landed on {blocked})"
    if len(targets) > 1:
        why += f" ({len(reached & targets)} of {len(targets)} in the advised footprint)"
    return "MATCH", why


# ── what the out-of-combat block advised, and whether it was taken ──
#
# Until now only `Session.battle` armed a recommendation, so combat had a
# measured agreement rate (84% over 32 recommendations, third live run) and the
# five dungeon layers had **none, ever**. That asymmetry is the risk: every live
# run this project has done found a real defect, and the one time the
# out-of-combat block was looked at the verdict was "no se entiende qué pasillo
# te recomienda" — a defect rate of 1 for 1 on first inspection.

LAYERS = ("combat", "route", "torch", "curio", "camp")


@dataclass(frozen=True)
class RoomAdvice:
    """The out-of-combat block's advice as structure, not as text.

    The overlay's rule, for the overlay's reason: a marker that recovered
    `NEXT -> coAB  (corridor, 1 fight)` with a regex over the printed block
    would break the first time a label changed, and silently — by scoring the
    wrong claim. `Layers.block` returns this alongside the text so both come out
    of one route computation, which is the only expensive part of the loop.

    **A layer with nothing to say leaves its field empty and is never armed.**
    That is the `hold` rule, and it is what keeps the number honest: "carry on"
    is not a recommendation, and scoring it against a save where nothing changed
    would manufacture agreement for free — the same way fleeing every fight
    scores 100% on `old-succ`. Two curio actions are holds by this rule and are
    deliberately not armed: `skip` ("don't touch it") and `optional` ("your
    call"), which is not advice at all.
    """
    next_step: str = ""          # area id the route says to walk into
    # The first ROOM on the planned walk, which is a different thing from
    # `next_step` and is the only one of the two that can be clicked. Leaving a
    # room goes through the map, and the game's own help is explicit about what
    # the map accepts: "[CLICK] on a room in the map to begin moving to that
    # room" (`str_help_raid_room_0`) -- it routes you through the connecting
    # hallway itself. `next_step` from a room is that hallway, so it names a
    # thing the map will not take. Empty in a corridor, where walking is `A`/`D`
    # and no map is involved.
    next_room: str = ""
    raid_end_priced: str = ""    # what `_raid_end` WOULD say, objective or not
    torch: str = ""              # "relight" | "shutdown"; "hold" never lands here
    # **HOW MANY TORCHES THE RELIGHT NEEDS, and it is not one.** One torch
    # restores 25 (`torchlight.TORCH_RESTORE`), so 0 -> the 76 radiant floor
    # is FOUR presses of `T`. The actuator sent exactly one and the field to
    # say otherwise did not exist, so a snuffed party walked into the next
    # fight at 25 or less with the log reporting a relight had been sent.
    # Fran, 2026-09-20: *"try to never combat on under 75 torch, it started a
    # combat at 0 torch"*.
    #
    # Capped by what the pack holds, so it never promises light the party
    # cannot buy, and 0 whenever the action is not a relight.
    torch_count: int = 0
    curio_area: str = ""
    curio_id: str = ""
    curio_action: str = ""       # "use" | "interact"
    curio_item: str = ""         # the provision a "use" names
    camp: bool = False
    # "return" | "continue" -- the raid-end choice, and empty until the quest
    # objective is actually met. Completing the objective does NOT end a raid:
    # DD raises a banner offering both and waits, which is where every live run
    # stopped, printing "no room advice to act on" at the one moment the room
    # really did have nothing left to advise.
    raid_end: str = ""
    #: The loot plan's verdict on the curio the party is standing on, when it
    #: differs from "resolve it now". `defer` is its own thing and NOT a skip:
    #: skip means never, defer means not at this light, and the walk home
    #: passes here again. Conflating them either throws the curio away or opens
    #: it for a third of what it is worth.
    curio_defer: bool = False
    #: What the whole plan is expected to be worth from here, in gold, and how
    #: many fights it expects to take in the dark. Reported so `_raid_end` and
    #: a reader can weigh "keep looting" against `bridge/retreat.py` -- neither
    #: of which this module decides.
    loot_worth: float = 0.0
    dark_battles: float = 0.0
    #: Provisions to use here, each on its trigger (`bridge/provision_use.py`):
    #: `((hero guid, rank, item, why), ...)`.
    provisions: tuple = ()

    @property
    def empty(self) -> bool:
        return not (self.next_step or self.torch or self.curio_action
                    or self.camp or self.raid_end or self.curio_defer
                    or self.provisions)


@dataclass(frozen=True)
class RoomBoard:
    """The measurable out-of-combat state.

    `LiveState` already carries all of it except the curio, which is the live
    `content` field in the map save: a spent curio keeps its `curio_prop` and
    drops to `content == 0`, so `dungeon.curios()` stops reporting it. Counted
    rather than flagged, because one real area can hold two of the same prop.
    """
    area: str = ""
    torch: float = 0.0
    provisions: dict[str, int] = field(default_factory=dict)
    battles: int = 0
    curio_left: int = -1         # -1 = no claim to measure, or the map was unreadable
    party: frozenset = field(default_factory=frozenset)

    @classmethod
    def of(cls, state: LiveState, area: str, dungeon=None,
           curio_area: str = "", curio_id: str = "") -> "RoomBoard":
        left = -1
        if dungeon is not None and curio_id:
            left = sum(1 for aid, tile in dungeon.curios()
                       if aid == curio_area and tile.curio_id == curio_id)
        return cls(area=area or "", torch=float(state.torchlight or 0.0),
                   provisions=dict(state.provisions or {}),
                   battles=int(state.battles_completed or 0), curio_left=left,
                   party=frozenset(h.guid for h in state.heroes))


def same_raid(before: RoomBoard, after: RoomBoard) -> bool:
    """Is this still the expedition the claim was armed in?

    Neither of these can happen inside one raid: the battle counter only ever
    climbs, and nobody joins a party mid-dungeon (heroes leave it, so a subset is
    normal and a *new* guid is not). Both can happen between two captures in one
    replay directory, and it showed: the whole inventory of the previous raid read
    as "spent" in one poll, producing `advised skeleton_key -> lost_luggage, spent
    19, 8, 9, antivenom, bandage, bust, citrine, crest, deed, emerald, jade, ...`
    — a disagreement invented entirely by the party walking home.

    The combat marker has had the equivalent guard since it was written (the round
    number going backwards ends a fight); the room marker was missing it.
    """
    if after.battles < before.battles:
        return False
    return not (after.party - before.party)


@dataclass
class RoomPending:
    """One armed room recommendation, plus what was seen while it stood.

    The latches are the part that matters. A room recommendation has no clock —
    the party can stand in a corridor for a minute — so the only reliable
    boundary is walking out of it, and the verdict is taken when the area
    changes. But a torch relit and then walked with decays again, so comparing
    the two endpoints would read a followed relight as "never relit". The events
    are therefore latched as they happen, per poll, and endpoint comparison is
    kept for the two things that cannot go back: the area and the curio.
    """
    advice: RoomAdvice
    board: RoomBoard
    # Ways out of this area other than the one the party came in by. 1 or 0 means
    # the route layer had no choice to get right — see `ways_forward`.
    ways: int = 0
    torch_up: bool = False
    torch_out: bool = False
    camped: bool = False
    spent: dict[str, int] = field(default_factory=dict)


def about_to_win(engine, action) -> bool:
    """Is the killing blow the next thing that happens?

    PLAYBOOK 2.3's shutdown — drop the torch before the blow that ends the fight,
    so the corpses pay better — is the one dungeon decision that is purely
    economic. It is also **only** safe on that turn: darkness hands the monsters
    +12.5 accuracy, +25% damage and +5 crit, so any earlier it is a bet, and
    `torchlight.advise` refuses to recommend it without this.

    Which is why it had never been recommended at all. `Layers.block` asks the
    torch layer `about_to_win=state.in_battle`, and `Session.room` — the only
    caller of `block` in the loop — runs **only when the party is not in a
    battle**. So the argument was always False and the shutdown was unreachable:
    zero samples was the loop never asking the question, not the play being rare.

    **The test is "expects to be the last turn", not "cannot fail to be".**
    It was the damage FLOOR -- `hp <= dmg_min`, a kill that cannot miss its
    roll -- and that is far stricter than the play. Live on 2026-09-06 the
    tutorial's last blow was 4 hp against a floor of 3, so the snuff never
    fired in a fight the party won on that swing.

    Fran, asked directly whether likely is good enough:

    > *"it's very likely to kill it's good enough to go dark on, worst case
    > scenario should be (enemy dodges and attacks one of our heroes with crit
    > and other member defeats it at 0 torch) but even if that's the worst
    > case you should be 4v1 usually so its safe to do it"*
    > *"we should assume that snuff it's used on advantaged positions that
    > expect last turn anyway"*

    So the line is the AVERAGE roll rather than the worst one: `hp` at or
    under the midpoint of the hero's damage band. The downside he prices is
    one enemy turn taken in the dark -- +12.5 accuracy, +25% damage, +5 crit
    on a single attack -- against a fight that is already one enemy against a
    standing party, which is what "advantaged position" means here and what
    the single-enemy test above already enforces.

    Still bounded by that test: one living enemy, or a target already doomed
    by its own DoTs. This does not open the snuff mid-fight, only earlier
    within the turn that ends it.
    """
    from env.gym_env import _is_doomed
    alive = [e for e in engine.enemies if e.is_alive() and not e.is_corpse]
    if len(alive) != 1:
        return False
    foe = alive[0]
    if _is_doomed(foe):
        return True
    actor = next((h for h in engine.heroes if h.id == action.hero_id), None)
    if actor is None:
        return False
    expected = (actor.dmg_min + actor.dmg_max) / 2.0
    return foe.hp <= expected


def ways_forward(dungeon, here: str, came_from: str) -> int:
    """How many areas the party could walk into that are not where they came from.

    **A route recommendation with one answer is not a recommendation.** From
    inside a corridor there is the far room and the way back; from a two-corridor
    room there is the other corridor and the way back. Marking those scores the
    layer for the shape of the map rather than for its judgement — which is how
    the first replay of this came out 18 for 18 on route, a number that says
    nothing about whether the layer is any good. Forced steps are counted and
    reported, never folded into the rate.

    Adjacency comes from `DungeonMap.graph()` (`{room: {room: corridor}}`) rather
    than from the `coAB` name convention, so both readings of an id agree by
    construction.
    """
    if not here or dungeon is None:
        return 0
    try:
        adj = dungeon.graph()
    except Exception:                              # noqa: BLE001
        return 0
    if here in adj:                                # a room: its exits are corridors
        outs = set(adj[here].values())
    else:                                          # a corridor: its endpoint rooms
        outs = {room for room, links in adj.items() if here in links.values()}
    return len(outs - {came_from})


def dark_ceiling(rules) -> float:
    """The top of the darkest *lit* darkness band, from `rules.json` itself.

    `darkness.range_table` ships five bands and the lowest of them is
    `(-inf, 0]` — pitch black is exactly torch 0, and the band above it runs
    `(0, 25]`. So 25 is the shipped boundary a snuff has to jump, and it is read
    off the table rather than written down here, which is the difference between
    game data and a convention that looks like it.
    """
    try:
        lit = [b.upper for b in rules.darkness_table if b.lower >= 0.0]
        return min(lit) if lit else 0.0
    except Exception:                              # noqa: BLE001
        return 0.0


def latch(pending: RoomPending, before: RoomBoard, after: RoomBoard,
          dark_ceiling_level: float) -> None:
    """Fold one poll's change into the armed claim.

    Called every tick, **inside a battle as well as out of one**: the torch gets
    relit from within a fight too, and a room recommendation the player took
    during the next battle is still a room recommendation they took.
    """
    if after.torch > before.torch:
        # Decay only ever lowers the torch, so any rise is a relight and no
        # threshold is needed at all. How much one torch restores is in no
        # shipped file, which is exactly why the test is on the direction.
        pending.torch_up = True
    elif before.torch > dark_ceiling_level and after.torch <= 0.0:
        # Snuffing writes 0 in one step, from wherever the torch was. Decay
        # reaches 0 as well — so the endpoint says nothing, and even the
        # single-step transition is not enough on its own: a torch decaying
        # 7 -> 0 on the last leg of a walk hits the same test, and read as a
        # snuff it would score a party that ignored the advice as having taken
        # it. Requiring the step to cross the whole darkest lit band is the
        # discriminator, and 25 is `rules.json`'s own boundary, not a guess.
        pending.torch_out = True
    for pid, held in before.provisions.items():
        gone = held - after.provisions.get(pid, 0)
        if gone > 0:
            pending.spent[pid] = pending.spent.get(pid, 0) + gone
    if pending.spent.get("firewood"):
        pending.camped = True


# Not a verdict — a claim held open. A curio the party walks past is very often
# one they come back for: the dungeon is a graph with dead ends, and the captured
# route `coEF(sack) > rooE > coEI(crate) > rooI > coEI > rooE > coEF > rooF` walks
# both curios twice and uses each on the return leg. Scoring the first pass as a
# disagreement read the curio layer at 72% when the advice was in fact taken —
# four of its five DIFFERs were this. So it is parked, and settled by whether the
# party ever comes back. Anything still parked when the run ends IS a DIFFER.
DEFER = "DEFER"


def _judge_curio(pending: RoomPending, after: RoomBoard) -> tuple[str, str, str]:
    a = pending.advice
    if pending.board.curio_left < 0 or after.curio_left < 0:
        return "curio", "?", "the map save was not readable on both sides"
    if after.curio_left >= pending.board.curio_left:
        return "curio", DEFER, f"advised {a.curio_action} {a.curio_id}, walked past it"
    if a.curio_action != "use" or not a.curio_item:
        return "curio", "MATCH", f"{a.curio_id} was used"
    if pending.spent.get(a.curio_item):
        return "curio", "MATCH", f"{a.curio_item} -> {a.curio_id}"
    # `state.provisions` is the whole party inventory, heirlooms and loot
    # included, so this list is capped rather than filtered — deciding which ids
    # "are really provisions" would be a convention, and the judgement above
    # already turns on the ONE advised id, never on the list.
    names = sorted(pending.spent)
    others = ", ".join(names[:4]) + (f" +{len(names) - 4} more" if len(names) > 4 else "")
    return ("curio", "DIFFER",
            f"advised {a.curio_item} -> {a.curio_id}, spent {others or 'nothing'}")


def judge_room(pending: RoomPending, after: RoomBoard) -> list[tuple[str, str, str]]:
    """`[(layer, verdict, reason)]` — one verdict per claim the block made.

    Per layer and never pooled. A single number across all of them would let a
    route layer at 30% hide behind a torch layer that is right by default, which
    is the failure this whole measurement exists to prevent.
    """
    a = pending.advice
    out: list[tuple[str, str, str]] = []

    # `ways <= 1` is the route layer's `hold`: there was one way forward, so
    # walking it is not evidence of anything. Counted by the caller, not scored.
    if a.next_step and pending.ways > 1:
        if not after.area:
            out.append(("route", "?", "the save did not name an area"))
        elif after.area == a.next_step:
            out.append(("route", "MATCH", f"walked into {after.area}"))
        else:
            out.append(("route", "DIFFER",
                        f"advised {a.next_step}, walked into {after.area}"))

    if a.torch == "relight":
        out.append(("torch", "MATCH", "the torch went up") if pending.torch_up
                   else ("torch", "DIFFER", "advised a relight, the torch only burned down"))
    elif a.torch == "shutdown":
        out.append(("torch", "MATCH", "the torch went out") if pending.torch_out
                   else ("torch", "DIFFER", "advised shutting the torch down, it stayed lit"))

    if a.curio_action:
        out.append(_judge_curio(pending, after))

    if a.camp:
        out.append(("camp", "MATCH", "burned a firewood") if pending.camped
                   else ("camp", "DIFFER", "advised camping here, walked on"))

    return out


# ── the out-of-combat block ──

def _step_toward(dungeon, here: str, target: str) -> tuple[str, str]:
    """`(first area to enter, the room it leads to)` on the shortest walk from
    room `here` to `target` (a room, or a corridor between two rooms)."""
    try:
        from bridge.route import _all_pairs            # noqa: PLC0415
        graph = dungeon.graph()
        if here not in graph:
            # In a corridor: head for whichever end is nearer the target.
            ends = [r for r, nb in graph.items() if here in nb.values()]
            if target in ends:
                return target, target
            allp = _all_pairs(dungeon)
            best = min((len(allp.get(e, {}).get(target, [])) or 99, e)
                       for e in ends) if ends else (99, "")
            return (best[1], best[1]) if best[1] else ("", "")
        pairs = _all_pairs(dungeon).get(here, {})
        if target in pairs:
            walk = pairs[target]
        else:
            ends = [r for r, nb in dungeon.graph().items()
                    if target in nb.values()]
            if here in ends:
                other = next((r for r in ends if r != here), "")
                return target, other
            reach = [pairs[r] + [target] for r in ends if r in pairs]
            walk = min(reach, key=len) if reach else []
        if len(walk) < 2:
            return "", ""
        step = walk[1]
        room = next((a for a in walk[1:] if a.startswith("roo")), "")
        return step, room
    except Exception:                                  # noqa: BLE001
        return "", ""


def _loot_outstanding(loot, abandoned=None, prefer=None):
    """The best thing the loot plan still means to collect, or None.

    A `take` is loot the plan is walking to; a `defer` is loot it has walked
    PAST on the promise of coming back for it in the dark. Both are reasons
    not to bank the quest yet. A `skip` is not -- that verdict means never,
    at any light, and the distinction is the whole point of having two words.

    Returns `(stop, gold)` for the most valuable one, so the caller can say
    what it is waiting for rather than just that it is waiting.
    """
    if loot is None:
        return None
    # **A CURIO THE ACTUATOR HAS GIVEN UP ON IS NOT A REASON TO STAY.**
    # `ActRunner._give_up_on_curio` stops trying one for the rest of the raid
    # -- a whole iteration was once eaten by a corridor sack -- but the
    # decision layer could not see that, so the plan kept pricing it and
    # `_raid_end` kept answering CONTINUE. Live 2026-09-06: ten presses at the
    # tutorial tent with no panel, "giving up for this raid", and then the
    # party walked `rooA` to `coAB` and back for as long as the run lasted.
    #
    # It is the same shape as the deferral above -- a promise nothing can keep
    # -- one layer along, and it must be a SKIP rather than a silence: the
    # loot is still on the map and the report should say so.
    skip = set(abandoned or ())
    best = held = None
    for stop in loot.stops:
        if stop.action not in ("take", "defer"):
            continue
        if skip and (stop.area, stop.curio_id) in skip:
            continue
        gold = stop.value.worth()
        if gold <= 0.0:
            continue
        if prefer and (stop.area, stop.curio_id) == tuple(prefer):
            held = (stop, gold)
        if best is None or gold > best[1]:
            best = (stop, gold)
    # **A CHOICE ALREADY MADE OUTRANKS A MARGINALLY BETTER ONE.** The plan is
    # rebuilt every poll from wherever the party now stands, so two comparably
    # priced stops swap places as it walks, and the walk reverses. Fran named
    # it exactly: *"wants to go left side room, model detects downside room on
    # the map first, and goes back like it was wrong the first time, both ways
    # are good in a sense as both have loot, but it needs to maintain the
    # pathing consistent to not loop"*.
    #
    # So the held goal wins while it is still outstanding and still worth
    # something. It is not a tie-break -- a tie-break still flips when the
    # numbers cross -- and it costs nothing real: the two were within a few
    # gold of each other, and walking back and forth between them is worth
    # less than either.
    return held or best


def _dark_pays_here(loot, here, next_step) -> bool:
    """Does the loot plan want darkness at the party's feet, or one step on?

    Fran's rule, and it is about the moment right after a won fight: *"when
    you beat a battle and immediately after there is a loot curio -- next box
    on a hallway, or the curio on the same room, or 1st box on the next
    hallway -- you should keep the torch low for that part of the dungeon"*.

    **The stops are walked IN ORDER, and that is the whole guard.** The first
    version asked only "is there a dark pickup nearby", and the bridge oracle
    caught what that does: in `coHM` the next dark stop was the heirloom chest
    in `rooH`, on the far side of a FIGHT in `rooH`, so the rule held the
    light and walked the party into a dim combat. Fran's other standing rule
    -- *"try to never combat on under 75 torch"* -- is exactly what that
    breaks, and `lootplan`'s hard torch floor is built on it.

    So a `fight` ends the scan, and so does the plan's own `relight`: both are
    the plan saying the light is wanted before anything else it has queued.
    Only a dark `take`/`defer` reached BEFORE either of them counts.

    `skip` is excluded on the way past -- a stop the plan refuses is loot
    nobody is going to collect, so it cannot justify holding anything.
    """
    if loot is None:
        return False
    near = {a for a in (here, next_step) if a}
    if not near:
        return False
    for stop in getattr(loot, "stops", ()):
        if stop.action in ("fight", "relight"):
            return False
        if stop.action not in ("take", "defer"):
            continue
        if stop.area in near and stop.torch < RADIANT_FLOOR:
            return True
    return False


@dataclass
class Layers:
    """The six dungeon layers, held together so the loop has one thing to call."""
    advisor: Advisor
    rules: object
    quest: Optional[Quest] = None
    #: `ActRunner.gave_up_on` when layer 3 is running -- the curios it has
    #: stopped trying. Set by `run.py` after the runner is built, because the
    #: decision layers are constructed first and must work without one.
    abandoned: object = None
    #: `(area, curio_id)` of the loot stop the walk is currently committed to,
    #: so a re-plan cannot turn the party round. Cleared when it is collected
    #: or priced out -- see `_loot_outstanding`.
    loot_goal: tuple = ()
    #: `() -> int`, the actuator's count of 'no room' modals this raid.
    pack_full: object = None
    #: Set by a stuck re-plan: loot is no longer a reason to stay this raid.
    loot_off: bool = False
    #: Areas the loot walk steered through per goal (`LOOT_GOAL_MAX_PLANS`).
    _goal_plans: dict = field(default_factory=dict)

    @staticmethod
    def _raid_end(state: LiveState, plan, mine, loot=None,
                  abandoned=None, prefer=None) -> tuple[str, str]:
        """Objective met: leave, or keep looting? `(choice, why)`.

        **The default is to LEAVE, and that asymmetry is the whole rule.**
        Everything still on the map is optional by definition -- the quest is
        already won -- so continuing spends real risk on loot, while returning
        banks a completed quest. `RoutePlan.targets` being empty IS the
        objective being met; it is not inferred from the printed note.

        Two things buy a "continue", and both must hold: something priced
        POSITIVE is still reachable, and the party is in shape to go and take
        it. `mine` carries only curios the party is standing on, so a "skip"
        (net negative bare-handed) correctly buys nothing.

        The risk gate is stress and affliction rather than HP, because
        `LiveHero` carries no `max_hp` and a bare HP number cannot be turned
        into a fraction here. An afflicted hero is the game's own signal that
        a run has gone wrong, and 100 is `rules.json`'s Resolve Check.

        This DECIDES; it never clicks. `ActRunner` additionally requires the
        quest-complete banner to be on screen before it presses anything, so a
        stale or wrong reading of this cannot end a raid on its own.
        """
        afflicted = [h for h in state.heroes if h.affliction_id]
        stressed = [h for h in state.heroes if h.stress >= 100]
        if afflicted or stressed:
            who = ", ".join(h.hero_class for h in (afflicted or stressed))
            return "return", (f"objective met and {who} is "
                              f"{'afflicted' if afflicted else 'at a Resolve Check'}"
                              f" - bank the quest")
        # **AND THE SAME SKIP ONE BRANCH EARLIER.** `mine` is the curio
        # layer's own verdict on what is underfoot, and it is consulted BEFORE
        # the loot plan -- so filtering only `_loot_outstanding` left the loop
        # exactly where it was. Live 2026-09-06: the actuator gave up on the
        # tutorial tent, and the party then held station in `coAB` printing
        # "take it first" every thirty seconds until the run was killed.
        #
        # Both branches answer the same question -- is there something here
        # worth staying for -- so both have to honour the same "nothing will
        # collect it" fact. A filter applied to one of two readers is not a
        # filter.
        skip_here = set(abandoned or ())
        worth = [c for c in mine if c.action in ("use", "interact")
                 and (c.area_id, c.curio_id) not in skip_here]
        if worth:
            return "continue", (f"objective met, but {worth[0].curio_id} here "
                                f"is priced positive - take it first")
        # **The loot plan, and it has to be asked BEFORE `plan.detours`.**
        # `route.optional_targets` deliberately excludes corridor curios -- a
        # corridor is a property of the route rather than an alternative to it
        # -- so a tent standing in a hallway is invisible to `detours` and to
        # `mine` alike. Live on 2026-09-06 that printed two adjacent lines
        # that contradicted each other:
        #
        #     LOOT    346g + ... still on the map
        #     RAID    RETURN  - objective met and nothing left is priced positive
        #
        # and the party went home leaving a DEFERRED curio behind, which is
        # the one thing a deferral must never do: `lootplan` only defers
        # because the walk home comes back through here, so returning early
        # converts "worth three times as much later" into "lost".
        # Reported by Fran as "it went back to the hamlet".
        outstanding = _loot_outstanding(loot, abandoned, prefer)
        if outstanding is not None:
            stop, worth_gold = outstanding
            promised = " (and it was DEFERRED for this light)" \
                if stop.action == "defer" else ""
            return "continue", (f"objective met, but {stop.curio_id or 'loot'} "
                                f"in {stop.area} is worth ~{worth_gold:.0f} "
                                f"gold at torch {stop.torch:.0f}{promised}")
        # **AND A DETOUR THE LOOT LAYER HAS PRICED AS A SKIP IS NOT ONE.**
        # `plan.detours` is the ROUTE layer's list of optional targets and it
        # predates any pricing: it offers anything reachable. `lootplan` is
        # the layer that decides whether a curio is worth opening, and on the
        # Old Road it refuses `bandits_trapped_chest` outright -- `Blight 1`
        # bare-handed, and the party has no skeleton key.
        #
        # Live 2026-09-06, once the tent stopped holding the party, this
        # branch took over and held it in exactly the same place: "objective
        # met, curio(bandits_trapped_chest) is +1 moves away", every thirty
        # seconds, for a chest nothing was ever going to open. Third reader of
        # the same question in one method, third one that had to be told.
        #
        # Matched on the AREA and the curio id inside `what`, because a detour
        # names its target as `curio(<id>)` and the plan names the id alone.
        priced_out = {(s.area, s.curio_id) for s in
                      (loot.stops if loot is not None else ())
                      if s.action == "skip" and s.curio_id}
        # ...and so is one the actuator or a loot bound has GIVEN UP on (live
        # 2026-09-27: an heirloom chest that did not answer three presses
        # held the raid as a +0-move detour).
        priced_out |= {tuple(g) for g in (abandoned or ()) if len(g) == 2}
        live_detours = [d for d in plan.detours
                        if not any(a == d.area_id and c and c in d.what
                                   for a, c in priced_out)]
        if live_detours:
            d = live_detours[0]
            return "continue", (f"objective met, {d.what} is +{d.extra_moves} "
                                f"moves / +{d.extra_fights} fights away")
        return "return", "objective met and nothing left is priced positive"

    def block(self, state: LiveState, raid_file: Path, map_file: Path,
              dungeon=None, here=None) -> tuple[str, str, RoomAdvice]:
        """`(full block, one-line headline, the advice as structure)`.

        The headline is what a glance needs — where to walk next, or the loudest
        thing standing between the party and walking there. It is returned from
        here rather than recomputed by the caller because the route is an exact
        subset cover; planning it twice to print it twice is the one part of
        this loop that is not cheap. The `RoomAdvice` rides along for the same
        reason, and so that the marker never has to read the text back.

        `dungeon` and `here` are accepted pre-read: the caller needs both to
        close out the previous room's recommendation, and the map is a DSON
        parse that should happen once per area change, not twice.
        """
        out: list[str] = []
        headline = ""
        advice = RoomAdvice()
        if dungeon is None:
            dungeon = _safe(read_dungeon_map, map_file)
        if here is None:
            here = _safe(read_position, raid_file)
        plan = _safe(plan_route, dungeon, here, self.quest) if dungeon else None
        # The torch layer cannot see the route, and needs it to tell "one fight
        # left" from "the run ends after this fight" — the two are not the same
        # claim, and conflating them priced a shutdown at zero with five
        # corridors still to walk. See torchlight.advise_from_live.
        moves_left = plan.moves if plan is not None else None
        # `about_to_win` is the question "is the killing blow the next thing that
        # happens", and this passed a hard True — so out of combat the layer
        # answered the in-battle question and printed "SHUT DOWN the torch before
        # the killing blow ... on this fight" in every corridor, for a fight that
        # was not happening. Reported from live play. Out of combat there is no
        # loot roll pending, so there is nothing darkness can buy.
        torch = _safe(advise_torch_live, state, self.rules, False, False,
                      state.in_battle, moves_left)
        head = f"=== {here or 'in the dungeon'} ==="
        if torch is not None:
            head += f"  torch {state.torchlight:.0f} ({torch.bracket})"
        out.append(head)

        if dungeon is not None:
            if plan is not None:
                # The one thing a glance needs, on its own line and first: WHICH
                # WAY. The full path is planning context and reads as a wall of
                # 4-character ids — "no se entiende qué pasillo te recomienda"
                # was the live report, and the answer was in the middle of it.
                step = plan.next_step
                if step:
                    headline = f"go to {step}"
                    # `walk` alternates room/corridor, so the first room after
                    # the current area is the click target when we are standing
                    # in a room. Read off the plan rather than guessed from the
                    # id, because a walk can start with a room the party is
                    # already in.
                    room = next((a for a in plan.walk[1:]
                                 if a.startswith("roo")), "")
                    advice = replace(advice, next_step=step, next_room=room)
                    out.append(f"NEXT -> {step}{self._whats_in(dungeon, step)}")
                walk = list(plan.walk)
                shown = " > ".join(walk[:8])
                if len(walk) > 8:
                    shown += f" > ... (+{len(walk) - 8} more)"
                out.append(f"        via {shown}")
                out.append(f"        {plan.moves} moves, {plan.fights} fights, "
                           f"{plan.new_areas} new areas, {plan.revisits} revisits")
                if plan.note:
                    out.append(f"        {plan.note}")
                for detour in plan.detours[:3]:
                    out.append(f"        detour: {detour.line()}")
            # The loot plan is the layer `route.py` deliberately left out:
            # it prices what is on the map through the game's own tables and
            # decides the ORDER and the LIGHT together. `route.py` still owns
            # the objective cover; this owns what to do with the rest.
            loot = _safe(plan_loot, dungeon, self.quest, here,
                         float(state.torchlight or 0.0),
                         dict(state.provisions or {}))
            curio_lines, mine = self._curios(state, dungeon, here, plan)
            out.extend(curio_lines)
            if loot is not None:
                advice = replace(advice, loot_worth=loot.value.worth(),
                                 dark_battles=loot.expected_dark_battles())
                # The retreat bill reads this: leaving forfeits it.
                if getattr(self, "advisor", None) is not None:
                    self.advisor.loot_ahead_gold = float(loot.value.worth())
                out.append(f"LOOT    {loot.value.line()} still on the map; "
                           f"{loot.expected_dark_battles():.2f} fights in the "
                           f"dark expected")
                # The next DECISION, not the next step -- the route lines
                # above already say where the party is walking, and the block
                # has a ten-line budget it has to stay inside to be a glance.
                nxt = next((s for s in loot.stops
                            if s.action not in ("walk", "finish")), None)
                if nxt is not None:
                    out.append(f"        then {nxt.line().strip()}")
                # **Once the objective is met, `plan_route` stops planning** --
                # `targets` is empty, so `next_step` is empty, and a "continue"
                # from `_raid_end` would be a decision with no way to act on
                # it. The party would stand in the room agreeing that it ought
                # to go and fetch something. That is the `_LayerDone` failure
                # in a new place: a layer that CAN act (loot) being unable to,
                # because the layer that usually moves has nothing to say.
                #
                # So the walk comes off the loot plan instead, and only then:
                # while the objective is unmet the route owns movement
                # outright, because a detour taken before the quest is won is
                # a different and much worse trade.
                # Computed once per poll and REMEMBERED, so the walk
                # below and the raid-end verdict above cannot disagree about
                # which stop the party is going to.
                goal = _loot_outstanding(loot, self.abandoned, self.loot_goal)
                self.loot_goal = ((goal[0].area, goal[0].curio_id)
                                  if goal else ())
                # **A goal the LOOT WALK has steered for `LOOT_GOAL_MAX_PLANS`
                # area changes without being taken is given up.** Live
                # 2026-09-27: a knife rack priced at torch 0 kept the party
                # pacing rooD <-> coAD at torch 86 -- the save moved every
                # step, so no stall bound ever fired, and nothing was ever
                # going to darken the torch. Counted only while the loot walk
                # is what moves the party (the route has no step of its own),
                # and only on a NEW area: the replay oracle caught the first
                # version abandoning a curio the route was still walking to.
                if self.loot_goal and not advice.next_step:
                    seen = self._goal_plans.setdefault(self.loot_goal, [])
                    if not seen or seen[-1] != here:
                        seen.append(here)
                    n = len(seen)
                    if n > LOOT_GOAL_MAX_PLANS:
                        if self.abandoned is None:
                            self.abandoned = set()
                        self.abandoned.add(self.loot_goal)
                        out.append(f"LOOT    gave up {goal[0].curio_id} in "
                                   f"{goal[0].area}: {n - 1} areas walked "
                                   f"for it without taking it")
                        self.loot_goal = ()
                        goal = None
                if not advice.next_step and goal:
                    # **The step AFTER where the party is, not the head of
                    # the walk.** This was a filter -- every area except
                    # `here` -- so a party halfway along its own plan was
                    # always aimed at the plan's first entry, which is behind
                    # it. Live 2026-09-06, with the quest done and a strongbox
                    # left in `rooA`: rooC -> corE -> rooC -> corE, twenty
                    # seconds a lap, until Fran said *"right now it's looping
                    # a bit between hallways"*.
                    #
                    # The walk can legitimately name an area twice -- that is
                    # what a deferred curio's revisit IS -- so it is the LAST
                    # occurrence of `here` that says how far along the party
                    # is. Falling back to the head is right for an area the
                    # walk does not mention at all: the party is off-plan and
                    # the plan's start is the way back onto it.
                    walk = list(loot.walk)
                    at = len(walk) - 1 - walk[::-1].index(here)                         if here in walk else -1
                    ahead = [a for a in walk[at + 1:] if a != here]                         if at >= 0 else [a for a in walk if a != here]
                    # A walk ahead that never reaches the goal's area is not
                    # a walk to the goal (live 2026-09-27: "NEXT -> rooD (for
                    # the loot)" with the chest in rooA, the party turning
                    # round mid-corridor).
                    if ahead and goal[0].area not in ahead                             and goal[0].area != here:
                        ahead = []
                    if ahead:
                        room = next((a for a in ahead if a.startswith("roo")),
                                    "")
                        advice = replace(advice, next_step=ahead[0],
                                         next_room=room)
                        out.append(f"NEXT -> {ahead[0]} (for the loot)")
                    elif goal[0].area != here:
                        # **The walk is over and the stop is BEHIND the
                        # party** (live 2026-09-27: an heirloom chest in the
                        # adjacent rooA, a knife rack in the corridor just
                        # walked). A TAKE is walked back to; a DEFER was a
                        # promise about the walk home, and there is no walk
                        # left to keep it on, so it is given up.
                        if goal[0].action == "defer":
                            if self.abandoned is None:
                                self.abandoned = set()
                            self.abandoned.add(self.loot_goal)
                            self.loot_goal = ()
                            out.append(f"LOOT    gave up the deferred "
                                       f"{goal[0].curio_id} in {goal[0].area}: "
                                       f"no walk left to take it on")
                        else:
                            step, room = _step_toward(dungeon, here,
                                                      goal[0].area)
                            if step:
                                advice = replace(advice, next_step=step,
                                                 next_room=room)
                                out.append(f"NEXT -> {step} (back for the "
                                           f"{goal[0].curio_id})")
            if curio_lines and curio_lines[0].startswith("CURIO"):
                headline = curio_lines[0][8:].split("  - ")[0].strip()
            # Only a curio the party is STANDING on is a decision due now, and
            # only "use"/"interact" is a decision at all — see `RoomAdvice`.
            deferred_here = set()
            if loot is not None:
                deferred_here = {s.curio_id for s in loot.stops
                                 if s.action == "defer" and s.area == here}
            for c in mine:
                if c.action in ("use", "interact"):
                    if c.curio_id in deferred_here:
                        # Standing on it, and the plan says come back for it
                        # in the dark. Arming the "use" here would take it for
                        # a third of its value, and the walk home passes this
                        # corridor anyway -- so the deferral costs nothing.
                        advice = replace(advice, curio_area=c.area_id,
                                         curio_id=c.curio_id, curio_defer=True)
                        out.append(f"        DEFER {c.curio_id} - worth more "
                                   f"at torch 0 and the way home comes back "
                                   f"through {here}")
                        break
                    advice = replace(advice, curio_area=c.area_id,
                                     curio_id=c.curio_id, curio_action=c.action,
                                     curio_item=c.item or "")
                    break
            if plan is not None:
                choice, why = self._raid_end(state, plan, mine, loot,
                                             self.abandoned, self.loot_goal)
                full = self.pack_full() if callable(self.pack_full) else 0
                if choice == "continue" and self.loot_off and not plan.targets:
                    choice, why = "return", (
                        "objective met, and the dungeon stopped responding "
                        "while the party was looting - going home")
                if choice == "continue" and full >= PACK_FULL_RETURN:
                    choice, why = "return", (
                        f"objective met and the pack is full ({full} 'no "
                        f"room' this raid) - loot here cannot come home")
                if not plan.targets:
                    # The bridge's OWN reading of "the objective is met", and
                    # it is the one that arms the actuator.
                    advice = replace(advice, raid_end=choice)
                    out.append(f"RAID    {choice.upper()}  - {why}")
                else:
                    # **THE GAME FINISHES AN EXPLORE QUEST BEFORE THE MAP IS
                    # COVERED, and the bridge had no answer for that window.**
                    # `quest.types.json` gives `explore_all_rooms` a
                    # `percentage` of 0.9, so `Quest Complete!` is drawn with
                    # rooms still unvisited -- live 2026-09-06, 7 of 8 -- while
                    # `RoutePlan.targets` is still full and this branch never
                    # ran. `act._end_raid` then had nothing priced to go on and
                    # fell back to "CONTINUE, because the route wants
                    # somewhere", which is not a decision about the raid at
                    # all: it never asked whether anything left was worth the
                    # risk, or whether the party was in shape to take it.
                    #
                    # So the verdict is computed anyway and carried
                    # separately. It arms nothing -- only the actuator's
                    # banner branch reads it, and only when the GAME has drawn
                    # the banner, which is the game stating the objective is
                    # met. Deriving the 90% rule here instead was rejected:
                    # one live observation (7 of 8 completing) does not settle
                    # whether the threshold floors, rounds or ceils, and
                    # guessing it would move the raid-end decision on every
                    # map. The banner is the game's own answer and needs no
                    # arithmetic.
                    advice = replace(advice, raid_end_priced=choice)

        # **A RELIGHT MUST NOT UNDO THE SNUFF THAT WAS JUST PAID FOR.** The
        # torch layer is asked before the loot plan exists and cannot see it,
        # so out of combat it read "below radiant, torches in the pack" and
        # answered RELIGHT in the very corridor the loot plan had snuffed for.
        # Fran, 2026-09-20: *"when you beat a battle and immediately after
        # there is a loot curio -- next box on a hallway, or the curio on the
        # same room, or 1st box on the next hallway -- you should keep the
        # torch low for that part of the dungeon"*.
        #
        # The rule invents no weight, because `lootplan` has already decided
        # the light it wants at every stop: a stop priced at a dark torch is
        # the plan saying "open this one in the dark". Relighting on top of
        # that spends a torch to make the next pickup worth less, and then
        # another to put it back.
        #
        # Bounded to HERE and the NEXT STEP on purpose. Darkness outside the
        # killing blow is a straight loss (+12.5 monster accuracy, +25%
        # damage, +5 crit), so this may only hold the light across the pickups
        # that are already in front of the party -- never across the map.
        if (torch is not None and torch.action == "relight"
                and _dark_pays_here(loot, here, advice.next_step)):
            torch = replace(torch, action="hold", reason=(
                "the loot plan wants this pickup dark, and it is here or one "
                "step away -- relighting would spend a torch to make it worth "
                "less"))

        # Holding the light is the default and the usual answer, so saying it
        # every room is fifteen lines of "carry on". Only a torch decision that
        # is actually available gets printed out of combat.
        if torch is not None and (state.in_battle or torch.action != "hold"):
            if torch.action in ("relight", "shutdown"):
                # `torches_needed` is computed against 100 by `advise_torch`;
                # what a fight actually requires is clearing the floor, and
                # buying more light than that spends the pack on nothing.
                want = (min(int(torch.torches_held),
                            torches_to_restore(state.torchlight or 0.0,
                                               RADIANT_FLOOR))
                        if torch.action == "relight" else 0)
                advice = replace(advice, torch=torch.action, torch_count=want)
            out.append(f"TORCH   {torch.line()}")
            if torch.action == "shutdown":
                out.append(f"        +{torch.gold_bonus:.0f}% gold, "
                           f"+{torch.extra_draws:.0f} draws; "
                           f"{torch.torches_needed} torches to relight "
                           f"(holding {torch.torches_held})")
            if torch.action == "relight":
                headline = "RELIGHT"
        band = LENGTH_BANDS.get(self.quest.length) if self.quest else None
        camp = _safe(advise_camp_live, state, self.advisor.store, None, band)
        # No firewood is not a decision, it is a fact about the quest, and on a
        # short one it never changes — so saying it twice per room for the whole
        # run is two lines of nothing. The section stays whenever a campfire is
        # actually available, which is the only time there is a choice.
        has_wood = int((state.provisions or {}).get("firewood", 0) or 0) > 0
        if camp is not None and (has_wood or camp.should_camp):
            out.append("CAMP    " + camp.line().replace("\n", "\n        "))
            for action in camp.plan:
                out.append(f"          {action.line()}")
            # Camping outranks a curio and the next corridor: it is the only one
            # of the three that is gone once you walk past it.
            if camp.should_camp:
                headline = "CAMP HERE"
                advice = replace(advice, camp=True)
        # **Provisions on their triggers** (Fran, 2026-09-25): food under 30%
        # HP, a cure for the DoT it cures, never what the curios ahead need.
        from bridge.provision_use import plan_room_provisions  # noqa: PLC0415
        uses = _safe(plan_room_provisions, state, self.advisor.store,
                     locals().get("loot"), band, state.remaining_battles) or []
        # What the curios ahead need is not for a fight either.
        from bridge.provision_use import room_reserve  # noqa: PLC0415
        self._item_reserve = _safe(room_reserve, locals().get("loot"),
                                   len(state.heroes), band,
                                   state.remaining_battles) or {}
        if uses:
            out.extend(u.line() for u, _rank in uses)
            advice = replace(advice, provisions=tuple(
                (u.guid, rank, u.item, u.why) for u, rank in uses))
        survival = _safe(self.advisor.survival_note, state)
        if survival:
            out.append("STAY?   " + survival.replace("\n", "\n        "))
            if "RETREAT" in survival:
                headline = "RETREAT"
        return "\n".join(out), headline or "keep going", advice

    @staticmethod
    def _whats_in(dungeon, area_id: str) -> str:
        """What the party walks into, appended to the NEXT line.

        A bare `coFI` says nothing on screen — the game shows doors, not area
        ids — so the id has to be paired with what makes it worth walking to.
        """
        area = dungeon.areas.get(area_id)
        if area is None:
            return ""
        bits = ["room" if area.is_room else "corridor"]
        if area.explored:
            bits.append("already walked")
        for label, n in (("fight", sum(1 for t in area.tiles if t.has_battle)),
                         ("curio", sum(1 for t in area.tiles if t.curio_id)),
                         ("trap", sum(1 for t in area.tiles if t.trap_id))):
            if n:
                bits.append(f"{n} {label}{'s' if n > 1 else ''}")
        return f"  ({', '.join(bits)})"

    def _curios(self, state: LiveState, dungeon, here: Optional[str],
                plan=None) -> tuple[list[str], list]:
        """`(lines, the advices for curios in THIS area)`.

        Curios standing where the party is, then what is on the way.

        Split because they are different decisions: the one in this room is
        "touch it now, with what, using whom"; the others are inventory planning.

        The ahead-list is restricted to **the planned route** and to three
        entries. It used to print every remaining curio in the dungeon, every
        time the party moved — eleven lines of areas they had not chosen to walk
        to yet, re-printed per room. The compulsion warnings are hoisted out of
        the per-curio lines for the same reason: `grave_robber is curious (20%)`
        is a fact about the party, not about the curio, and repeating it on
        every line was most of the width.
        """
        party = [CurioHero.from_live_hero(h) for h in state.heroes]
        inventory = dict(state.provisions or {})
        on_route = set(plan.walk) if plan is not None else None
        mine, ahead, compulsions = [], [], {}
        for area_id, tile in dungeon.curios():
            if not tile.curio_id:
                continue
            advice = _safe(advise_curio, tile.curio_id, area_id, inventory, party)
            if advice is None:
                continue
            for c in advice.compulsions:
                compulsions[(c.hero, c.quirk)] = c.chance
            if area_id == here:
                mine.append(advice)
            elif on_route is None or area_id in on_route:
                ahead.append(advice)

        def strip(a) -> str:
            return a.line().split("  !! ")[0]

        def short(a) -> str:
            """Area plus the decision, and nothing else. The reasoning matters
            for the curio you are standing on; for one three rooms away the only
            question it answers is what to keep in the bag."""
            if a.action == "use":
                return f"{a.area_id} {a.item}->{a.curio_id}"
            if a.action == "skip":
                return f"{a.area_id} skip {a.curio_id}"
            return f"{a.area_id} {a.curio_id}"

        out = [f"CURIO   {strip(a)}" for a in mine]
        if ahead:
            more = f" +{len(ahead) - 3}" if len(ahead) > 3 else ""
            out.append("        on the way: "
                       + ", ".join(short(a) for a in ahead[:3]) + more)
        if (mine or ahead) and compulsions:
            out.append("        !! " + ", ".join(
                f"{hero} is {quirk} ({chance:.0%})"
                for (hero, quirk), chance in sorted(compulsions.items())))
        return out, mine


def _safe(fn, *args):
    """Run one layer; a layer that raises must not take the loop with it.

    Every one of these reads a save the game is actively writing, and a partial
    write or an id this repo has not decoded yet is a normal event mid-run —
    losing the combat advice over a curio nobody recognised would be a poor
    trade. The failure is printed once so it is not silent."""
    try:
        return fn(*args)
    except Exception as exc:                      # noqa: BLE001 - reported, not swallowed
        print(f"  ({getattr(fn, '__name__', fn)} failed: {exc})")
        return None


class _Pending(NamedTuple):
    """An armed combat recommendation, waiting for the board that settles it.

    A NamedTuple rather than a bare tuple because this shape is built in one
    place and unpacked in another, and `tests/bridge/test_run.py` builds it a third
    time by hand — adding `board` as a plain sixth element broke that test with
    an unpack error a long way from the change. `board` therefore carries a
    default, so a positional construction that predates it still works.
    """
    actor: int
    kind: str
    targets: object
    label: str
    before: object
    board: Optional[dict] = None


def _position(state: LiveState, actor_guid: int) -> dict:
    """The board a decision was made on, compactly.

    Advise mode captures no saves — `--replay` reads `archive/traces/`, but a
    live watch writes nothing but its log — so anything not recorded here is
    gone. Enemies carry `slot`, which is rank ORDER and NOT a rank number (a
    size-2 monster covers two), and it is stored under that name rather than
    "rank" for exactly the reason `LiveEnemy.slot` documents.
    """
    try:
        return {
            "actor": actor_guid,
            "heroes": [{"guid": h.guid, "class": h.hero_class,
                        "hp": round(float(h.hp), 1), "stress": round(float(h.stress), 1),
                        "stunned": bool(h.stunned),
                        "afflicted": h.affliction_id or "", "virtue": h.virtue_id or ""}
                       for h in state.heroes],
            "enemies": [{"id": e.enemy_id, "name": e.name, "hp": round(float(e.hp), 1),
                         "slot": e.slot, "stunned": bool(e.stunned),
                         "corpse": bool(e.is_corpse)}
                        for e in state.enemies],
            "torch": round(float(getattr(state, "torchlight", 0.0) or 0.0), 1),
        }
    except Exception:                                 # noqa: BLE001
        # A torn save can hand us a half-parsed state. A missing board is worth
        # far less than a dead poll thread.
        return {}


# ── the loop ──

class Session:
    """Poll, dispatch, mark, log."""

    def __init__(self, advisor: Advisor, log_path: Optional[Path] = None,
                 on_update=None, on_score=None, journal_path: Optional[Path] = None):
        self.advisor = advisor
        self.layers = Layers(advisor, RulesLoader.load(GAME_DIR / "shared" / "rules.json"))
        self.log = open(log_path, "a", encoding="utf-8", buffering=1) if log_path else None
        # The STRUCTURED record beside the readable one. `self.log` is prose for
        # a person; this is one JSON object per event for a tool, because every
        # analysis built on the prose so far has had to regex it back into
        # structure and two of those regexes were wrong. Disabled (and free)
        # when no path is given. See `bridge/journal.py`.
        self.journal = Journal(journal_path)
        self.counts = {v: 0 for v in VERDICTS}
        # The same verdicts split by which layer made the claim. The aggregate
        # above stays the running indicator a front-end draws; this is what the
        # summary reads, because one pooled number would let a route layer at
        # 30% hide behind combat at 84%.
        self.by_layer = {ell: {v: 0 for v in VERDICTS} for ell in LAYERS}
        # Called with an `Update` per event. The terminal path never sets it;
        # `bridge/overlay.py` does. A front-end that raises must not take the
        # loop down with it — the log is the record of record, the screen is a
        # convenience.
        self.on_update = on_update
        # Called with (layer, verdict, key) for every verdict scored. The key
        # identifies the DECISION POINT and not the advice, so two checkpoints
        # replayed over one capture set produce the same key sequence and can be
        # paired decision by decision. `tools/agreement.py` is the only caller
        # and it refuses to report a delta unless the sequences match exactly.
        self.on_score = on_score
        #: Called once when the room re-planner gives up, so the actuator can
        #: re-state a refusal `_say_once` has been swallowing. Set by `main`
        #: to `ActRunner.explain_again`; None in replay and advise mode, where
        #: there is no actuator to ask.
        self.on_stall = None
        self._pending: Optional[tuple] = None     # (guid, kind, guid_target, label, Board)
        # WHAT WAS PLAYED, recorded independently of whether there was advice
        # to compare it against. `_pending` is armed only when the advisor
        # produced a recommendation, so with no loadable checkpoint -- the
        # state this repo is in until the retrain -- `_resolve` returns
        # immediately and every combat turn the human plays is lost. A
        # demonstration corpus must not depend on the thing it exists to grade.
        self._demo: Optional[tuple] = None        # (guid, Board)
        self._battle_key = None
        # The wrong-hero recovery. `_battle_sig` is the board state the skip
        # was raised against; the moment any counter moves, the skip retires.
        self._battle_skip = 0
        #: A fight has been SEEN this raid, won or not. `battles_completed`
        #: does not move on a combat retreat, and see `_fought` at the gate.
        self._fought = False
        #: The class `bridge/heropanel.py` says the game is asking, when it
        #: could read one. A hypothesis about ONE stuck position, exactly like
        #: the skip, so it retires with it -- a stale class would steer a
        #: later turn to a hero the game is no longer asking, which is the
        #: failure this whole path exists to fix, inverted.
        self._battle_asked = ""
        self._battle_sig: Optional[tuple] = None
        self._replan = False
        self._room_key = None
        self._room_pending: Optional[RoomPending] = None
        self._room_prev: Optional[RoomBoard] = None
        self._room_last_area = ""
        # Where the party is, kept so `battle()` can arm a claim without a
        # raid-file path of its own — a fight happens in the area last walked to.
        self._area = ""
        # The last decoded map, kept so the BATTLE path can ask it how
        # many fights are left. `_battle_torch` has no map file in hand
        # and the answer is not in the raid save -- see `_battles_left`.
        self._dungeon = None
        # Route steps that had one way forward, so were never scored. Reported,
        # because "18 of 18" meant nothing until this was separated out.
        self.forced_route = 0
        # {(area, curio): reason} — walked past, not yet settled. See DEFER.
        self._deferred: dict[tuple[str, str], str] = {}

    def emit(self, text: str) -> None:
        # A Windows console is cp1252 here, not UTF-8, and several layers print
        # an em dash. `print` raises UnicodeEncodeError on those, which — from
        # inside the daemon poll thread — killed the loop and took the bridge
        # silent with it, the same failure mode as a torn save and just as
        # invisible. The log is opened utf-8 and keeps the real text; only the
        # screen degrades.
        try:
            print(text)
        except UnicodeEncodeError:
            enc = getattr(sys.stdout, "encoding", None) or "ascii"
            print(text.encode(enc, "replace").decode(enc, "replace"))
        if self.log is not None:
            self.log.write(f"[{time.strftime('%H:%M:%S')}] {text}\n")
            self.log.flush()

    def publish(self, update: Update) -> None:
        if self.on_update is None:
            return
        try:
            self.on_update(update)
        except Exception as exc:                  # noqa: BLE001
            print(f"  (overlay update failed: {exc})")

    @property
    def tally(self) -> tuple[tuple[str, int], ...]:
        return tuple((v, self.counts[v]) for v in VERDICTS)

    # -- marking --

    def _score(self, layer: str, verdict: str, key: tuple = (),
               info: Optional[dict] = None) -> None:
        self.counts[verdict] += 1
        self.by_layer.setdefault(layer, {v: 0 for v in VERDICTS})[verdict] += 1
        # Every marked decision, structured. This is the single funnel for all
        # five dungeon layers, so hooking it here rather than at each call site
        # is what makes the corpus complete by construction: a layer that gains
        # a marking rule later is recorded without touching this file.
        self.journal.event("decision", layer=layer, verdict=verdict,
                           key=list(key), **(info or {}))
        if self.on_score is not None:
            try:
                self.on_score(layer, verdict, key, info or {})
            except Exception:                     # noqa: BLE001
                pass          # a scorer must never take the poll thread down

    def _record_play(self, state: LiveState) -> None:
        """Record the move that was actually played, advice or no advice.

        This is the DEMONSTRATION half and it is deliberately separate from
        `_resolve`'s agreement half: one says what the human did, the other
        says whether we would have done the same. Fusing them -- which is what
        the code did until 2026-09-03 -- means no checkpoint, no corpus, and
        the corpus is most valuable exactly when the policy is in doubt.
        """
        if self._demo is None or not self.journal.enabled:
            return
        actor_guid, before, board = self._demo
        after = Board.of(state)
        if not state.in_battle or after.battle_round < before.battle_round:
            self._demo = None
            return
        obs = observe(before, after, actor_guid)
        if obs is None:
            return
        self.journal.event("play", layer="combat", actor=actor_guid,
                           played=obs.kind, target=obs.guid, label=obs.label,
                           round=before.battle_round,
                           board=board)
        self._demo = None

    def _resolve(self, state: LiveState) -> None:
        """Close out the previous recommendation against the board that follows."""
        self._record_play(state)
        if self._pending is None:
            return
        p = self._pending
        actor_guid, kind, target_guids, label, before = (
            p.actor, p.kind, p.targets, p.label, p.before)
        board = p.board or {}
        after = Board.of(state)
        # Turn counters restart every fight, so a pending recommendation from
        # the last one would sit unresolved forever and then be closed out
        # against a stranger's turn. The round number only ever climbs inside a
        # battle, so it going backwards (or the battle ending) is the boundary.
        if not state.in_battle or after.battle_round < before.battle_round:
            self._pending = None
            return
        obs = observe(before, after, actor_guid)
        if obs is None:
            return
        verdict, reason = judge(kind, target_guids, obs, before, label)
        # The key names the TURN, not the recommendation: round, whose turn it
        # was, and what the human actually played. All three come from the
        # save, so a second checkpoint replaying the same captures produces the
        # identical key here even when it advises something else.
        self._score("combat", verdict,
                    (before.battle_round, actor_guid, obs.kind, obs.guid),
                    # `advised` is the only part that moves with the checkpoint,
                    # which is what makes a DIFFER breakdown readable: it says
                    # what the policy wanted INSTEAD of what was played.
                    #
                    # The POSITION goes in too, because advise mode captures no
                    # saves: without it the journal records that a decision was
                    # disagreed with and loses the board it was made on, which
                    # is the half a later reader needs to judge who was right.
                    {"advised": kind, "played": obs.kind, "reason": reason,
                     "round": before.battle_round,
                     "board": board})
        detail = f" - {reason}" if reason else ""
        played = who(before, obs.guid, obs.label)
        self.emit(f"  {verdict:6s} played {obs.kind} {played}{detail}".rstrip() + "\n")
        self.publish(Update(phase="verdict", verdict=verdict, layer="combat",
                            headline=f"played {obs.kind} {played}",
                            detail=reason, tally=self.tally))
        self._pending = None

    def _arm(self, state: LiveState, hero_guid: int) -> None:
        adv = self.advisor
        if adv.last_action is None or adv.last_engine is None or adv.last_provisional:
            self._pending = None
            return
        kind = advised_kind(adv.last_engine, adv.last_action)
        guids, label = advised_target(adv.last_engine, state, adv.last_action)
        # The POSITION goes in beside the Board: `Board` is the compact
        # comparison key and carries no roster, so `_position` must be
        # taken from the LiveState here while we still have one.
        self._pending = _Pending(hero_guid, kind, guids, label,
                                 Board.of(state), _position(state, hero_guid))

    # -- marking, the dungeon layers --

    def _latch_room(self, state: LiveState) -> None:
        """Fold this poll into any armed room claim. Called on EVERY tick, in a
        battle as well as out of one — see `latch`."""
        tick = RoomBoard.of(state, "")
        if self._room_pending is not None:
            if not same_raid(self._room_pending.board, tick):
                self._room_pending = None
                self._room_last_area = ""
            elif self._room_prev is not None:
                latch(self._room_pending, self._room_prev, tick,
                      dark_ceiling(self.layers.rules))
        self._room_prev = tick

    def _arm_room(self, state: LiveState, here: str, dungeon,
                  advice: RoomAdvice) -> None:
        """Arm the claims this block made, or fill in claims that appeared later.

        **A claim never changes once made, and re-printing the block never
        resets a latch.** The block reprints inside one area whenever the torch
        crosses a band or a provision is spent — which is to say, mostly as a
        *consequence* of what the player just did. Re-arming there would wipe the
        evidence that they followed the advice and then score them for it. What
        the player was shown on arrival is the recommendation that gets marked;
        a layer that was silent then can still gain a claim later (the party
        picks up firewood mid-room), but one that spoke keeps its word.
        """
        if advice.empty:
            return
        if self._room_pending is not None and self._room_pending.board.area == here:
            held = self._room_pending.advice
            merged = RoomAdvice(
                next_step=held.next_step or advice.next_step,
                torch=held.torch or advice.torch,
                curio_area=held.curio_area or advice.curio_area,
                curio_id=held.curio_id or advice.curio_id,
                curio_action=held.curio_action or advice.curio_action,
                curio_item=held.curio_item or advice.curio_item,
                camp=held.camp or advice.camp)
            if merged.curio_id and not held.curio_id:
                # A curio claim that arrives late still needs its "before" count,
                # and the board it was armed with does not have one.
                self._room_pending.board = RoomBoard.of(
                    state, here, dungeon, merged.curio_area, merged.curio_id)
            self._room_pending.advice = merged
            return
        self._room_pending = RoomPending(
            advice=advice,
            board=RoomBoard.of(state, here, dungeon,
                               advice.curio_area, advice.curio_id),
            ways=ways_forward(dungeon, here, self._room_last_area))

    def _resolve_room(self, state: LiveState, here: str, dungeon) -> None:
        """Close out the room's claims, once the party has walked out of it.

        Walking out is the boundary because a room recommendation has no clock.
        The curio is counted in the area it stood in, never in the one the party
        just entered.
        """
        pending, self._room_pending = self._room_pending, None
        self._room_last_area = pending.board.area if pending is not None else here
        if pending is None or pending.advice.empty:
            return
        if pending.advice.next_step and pending.ways <= 1:
            self.forced_route += 1
        after = RoomBoard.of(state, here, dungeon,
                             pending.advice.curio_area, pending.advice.curio_id)
        key = (pending.advice.curio_area, pending.advice.curio_id)
        for layer, verdict, reason in judge_room(pending, after):
            if verdict == DEFER:
                self._deferred[key] = reason
                self.emit(f"  {'later':6s} {layer:6s} {reason} (open until the "
                          f"party is done with that area)")
                continue
            if layer == "curio" and verdict == "MATCH" and key in self._deferred:
                self._deferred.pop(key)
                reason += " (came back for it)"
            # Keyed on the AREA the claim was made in plus the curio that stood
            # there -- both read off the save, so the key survives a change of
            # checkpoint. See the combat site for why that matters.
            self._score(layer, verdict,
                        (pending.board.area, layer, key[0], key[1]),
                        {"reason": reason, "area": pending.board.area})
            self.emit(f"  {verdict:6s} {layer:6s} {reason}")
            self.publish(Update(phase="verdict", verdict=verdict, layer=layer,
                                headline=f"{layer}: {reason}", tally=self.tally))

    # -- dispatch --

    @staticmethod
    def _turn_signature(state: LiveState) -> tuple:
        """What changes when the board actually moves on.

        Every counter, not just the round: a hero turn that lands bumps one of
        them and nothing else does. This is what retires a `_battle_skip` --
        the skip is a hypothesis about a stuck position, so the evidence that
        ends it is the position becoming unstuck.
        """
        return (state.battles_completed, state.battle_round,
                tuple(sorted((t.guid, t.turns_taken) for t in state.turn_order)))

    def battle(self, state: LiveState) -> None:
        sig = self._turn_signature(state)
        if sig != self._battle_sig:
            self._battle_sig = sig
            self._battle_skip = 0
            self._battle_asked = ""
        # `hero_for_turn`, NOT `next_hero`: the advisor resolves the same skip
        # and the two must land on the same hero, or the key and the armed
        # claim name someone the advice is not for.
        hero, enemies_first = state.hero_for_turn(self._battle_skip)
        # **THE SCREEN NAMED A CLASS, SO STOP GUESSING AND GO TO IT.** The
        # skip walks the queue blind; `note_turn_missed(asked=)` carries the
        # class the hero panel actually read, and the smallest skip that
        # resolves to it is the answer in one step instead of up to
        # MAX_TURN_SKIPS. Falls back to the walk when no skip reaches that
        # class -- the panel can be right about a hero this queue cannot
        # produce, and a wrong steer is worse than a slow one.
        want = self._battle_asked
        if want and (hero is None or
                     getattr(state.hero_by_guid(hero.guid), "hero_class", "")
                     != want):
            for probe in range(MAX_TURN_SKIPS + 1):
                cand, cand_first = state.hero_for_turn(probe)
                if cand is None:
                    continue
                who = state.hero_by_guid(cand.guid)
                if who is not None and who.hero_class == want:
                    if probe != self._battle_skip:
                        self._battle_skip = probe
                        self._battle_key = None
                    hero, enemies_first = cand, cand_first
                    break
        # **THE PANEL'S ANSWER IS NEVER PROVISIONAL.** `enemies_first` is read
        # off the save's turn queue -- the one thing here that is KNOWN to lag
        # -- while the hero panel is the game's own live prompt, and a hero
        # cannot be asked for input while enemy turns are still pending. So if
        # the queue still owes an enemy turn and the screen is already asking
        # this hero, the queue is stale and the screen is right.
        #
        # Without this the recovery merely MOVES the deadlock: the bridge
        # solves the skip, publishes the hero the panel named, labels the
        # block provisional off the stale count, and the actuator refuses it
        # -- because a timing refusal must never fall through to the next
        # candidate. Measured live 2026-09-06, a tutorial fight alternating
        # "the game is asking the crusader" with "provisional look-ahead -
        # this turn is not due yet" until the raid was abandoned.
        #
        # It also un-sticks the dedupe: `enemies_first` is IN the turn key, so
        # correcting it produces a key the actuator has not already refused.
        if want and hero is not None and enemies_first:
            who = state.hero_by_guid(hero.guid)
            if who is not None and who.hero_class == want:
                enemies_first = 0
                self._battle_key = None
        # **A SKIP THAT BUYS NO DIFFERENT HERO MEANS THE HERO IS NOT THE
        # PROBLEM.** With one hero left there is nobody else to try, and the
        # third shape of the turn-order lag then shows itself: the queue says
        # a monster acts first, so the block is provisional and the actuator
        # refuses -- while that monster has ALREADY acted and its counter has
        # not caught up. The bridge waits for an enemy turn that is never
        # coming. Seen live: three heartbeats at 25 s and then nothing, with
        # one hero on Death's Door and the game waiting for him.
        #
        # "Provisional" means enemy turns are still ahead. Nothing having
        # moved for `STUCK_SECONDS` is evidence that they are not, so the
        # look-ahead is published as the live position instead.
        first, _ = state.hero_for_turn(0)
        forced = bool(self._battle_skip) and (
            hero is None or (first is not None and hero.guid == first.guid))
        if forced:
            hero, enemies_first = first, 0
        if hero is None:
            return
        # `_battle_skip` is IN THE KEY. Without it the retry produces the same
        # tuple, the dedupe drops it, and the recovery never runs.
        key = (state.battle_round, hero.guid, hero.turns_taken, enemies_first,
               self._battle_skip)
        if key == self._battle_key:
            return
        self._battle_key = key
        # BELOW the dedupe, or it prints on every poll of a position that is
        # still stuck -- which is the one situation it exists to announce.
        if forced:
            self.emit(f"-- nobody else to try, so {hero.side} {hero.guid}'s "
                      f"look-ahead is published as the live turn --")
        self._room_key = None
        try:
            # ZERO when forced. `battle()` has already resolved the skip back
            # to the first hero, and handing the advisor the raw count makes
            # it walk past that hero again and answer "no hero left to act" --
            # which is what the first live run of this printed, one line after
            # announcing the hero it was about to advise.
            block = self.advisor.recommend(state, 0 if forced
                                           else self._battle_skip)
        except SeedError as exc:
            self.emit(f"[R{state.battle_round}] cannot seed: {exc}\n")
            self.publish(Update(phase="battle", headline="cannot seed this board",
                                detail=str(exc), tally=self.tally))
            return
        self.emit(block)
        self._arm(state, hero.guid)
        # Armed UNCONDITIONALLY, and after `_arm` so it cannot be cleared by
        # the advisor having nothing to say. See `_record_play`.
        self._demo = (hero.guid, Board.of(state), _position(state, hero.guid))
        self._battle_torch(state)
        self.publish(self._battle_update(state, block, enemies_first, key))

    def abandon_loot_goal(self) -> None:
        """Give up the loot stop the raid layer is holding the party for."""
        # **Stuck while looting ends the looting.** One goal at a time was
        # too slow: live 2026-09-27 a raid with the objective met gave up a
        # sack, then a pack, and the loop's own stall bound killed the
        # iteration first -- three times, and the idle guard stopped the night.
        self.layers.loot_off = True
        goal = tuple(getattr(self.layers, "loot_goal", ()) or ())
        if not goal:
            return
        if self.layers.abandoned is None:
            self.layers.abandoned = set()
        self.layers.abandoned.add(goal)
        self.emit(f"-- giving up {goal[1] or 'loot'} in {goal[0]}: the raid "
                  f"was held for it and nothing could act on it --")

    def note_room_stuck(self) -> None:
        """The dungeon is frozen out of combat. Plan the room again.

        Unbounded on purpose, unlike `note_turn_missed`. That one walks a
        finite list of heroes and stops when it runs out; this re-runs a plan
        that is allowed to reach a different answer every time -- the map is
        re-opened, re-fitted and re-clicked, and the route layer already
        refuses rather than repeating a click it cannot justify. A bound here
        would turn a recoverable stall into the end of the night, which is
        exactly what the 180 s timeout already does.
        """
        self._room_key = None
        self._replan = True

    def take_replan(self) -> bool:
        """Consume a pending re-plan. ONE dispatch only.

        Read-and-clear, the same shape and for the same reason as
        `_take_snuff`: a flag left set would re-dispatch on every tick of a
        save that is not moving, which is exactly the situation this exists
        for. See `note_turn_missed`.
        """
        pending = self._replan
        self._replan = False
        return pending

    def note_turn_missed(self, asked: str = "") -> None:
        """The actuator could not make a turn land. Advise a different hero.

        **`asked` is the class the SCREEN says is being asked**, when
        `bridge/heropanel.py` could read it. Without it this walks the queue
        one skip at a time and hopes -- which is what it did on the first
        live run of the panel reader, and the log is the argument for this
        change:

            the game is asking the highwayman and the save says duelist
            the game is asking the highwayman and the save says antiquarian
            the game is asking the highwayman and the save says bounty_hunter

        Three polls, three wrong heroes, the right one named every time and
        never tried. With `asked` the skip is SOLVED instead of incremented:
        `battle()` searches for the skip that resolves to that class.

        The save's turn order and the screen disagree about 2% of the time and
        no counter-based rule does better (measured over the capture corpus --
        see `LiveState.next_hero`), so the 2% is recovered from rather than
        predicted away. Without this the bridge re-plans the identical
        unplayable move until the retry budget runs out and then stops for
        good; a live Cove raid ended exactly there.

        Bounded by `MAX_TURN_SKIPS`: past that the answer is not "a different
        hero", and looping would send inputs for every hero on every poll.
        `battle()` clears it as soon as any counter moves.
        """
        # Remembered rather than resolved here: this has no `LiveState` in
        # hand, and the skip that reaches a class is a property of the board
        # at the moment `battle()` reads it, not of the moment it missed.
        self._battle_asked = asked or ""
        if self._battle_skip < MAX_TURN_SKIPS:
            self._battle_skip += 1
            self._battle_key = None
            # **AND THE WATCHER HAS TO BE TOLD.** `_watch` skips a poll whose
            # raid signature has not moved, which is every poll while the
            # bridge is stuck on the wrong hero -- so clearing the dedupe key
            # alone changed nothing at all and the live re-run behaved exactly
            # as before. This is the third gate between the callback and a new
            # block, and the one that is easiest to forget: the other two are
            # inside `battle()` and visible from it.
            self._replan = True

    def note_turn_stuck(self) -> None:
        """Every hero has been tried and the board still has not moved.

        `note_turn_missed` is bounded because walking the queue blind past a
        few skips is hopeless. **This is what happens after that bound, and
        it exists because the alternative was nothing.** With the budget
        spent, that method records the class and returns without setting
        `_replan`; `_watch` skips every poll whose raid signature has not
        moved; and a stuck raid's save never moves. Three gates, and the
        last one closed on the right answer -- live on 2026-09-06 a tutorial
        fight stopped dead for six minutes with the party at 13/23 hp while
        the hero panel had NAMED the hero the game was waiting on.

        The skip returns to 0 rather than advancing: the hypothesis being
        retired is "some other hero in the queue", so the walk starts over.
        `_battle_asked` is deliberately KEPT -- `battle()` solves the skip
        from it in one lookup, and the panel's answer is the best information
        the bridge has about a disagreement the save is known to lose 2% of
        the time.

        Rate-limited by `STUCK_SECONDS` at the call site rather than by a
        count, which is the right bound for a recovery that cannot make
        things worse: every attempt is gated by the action bar and refuses
        before it sends anything.
        """
        self._battle_skip = 0
        self._battle_key = None
        self._replan = True

    def _battles_left(self, state: LiveState) -> Optional[int]:
        """Fights still on the map AFTER the one being fought, or None.

        `state.remaining_battles` cannot answer this and must not be used for
        it: it counts what the save knows, and an unexplored area can hold
        more -- the confusion that once had the torch layer print "the run's
        last fight" with five moves and eight unexplored areas left.

        The MAP does know, because every tile's content is in the save from
        turn zero whether or not the party has scouted it. None when there is
        no map to read, and None keeps the old torch-budget reasoning rather
        than assuming the best case.
        """
        dungeon = self._dungeon
        if dungeon is None:
            return None
        from bridge.lootplan import events_on          # noqa: PLC0415
        fights = [e for e in events_on(dungeon) if e.kind == "battle"]
        # The one being fought is on the tile the party is standing on, so it
        # is still counted as unresolved -- hence the -1.
        return max(0, len(fights) - 1)

    def _take_snuff(self) -> bool:
        """Consume the pending snuff. ONE turn only.

        A flag left set would snuff on every turn after the first, and the
        turns after the killing blow are exactly the ones where darkness is
        pure cost. Read-and-clear is the only shape that cannot do that.
        """
        pending = getattr(self, "_snuff_next_turn", False)
        self._snuff_next_turn = False
        return pending

    def _battle_torch(self, state: LiveState) -> None:
        """Ask the torch layer the question only a battle can answer.

        The shutdown is an in-combat play — "before the killing blow" — and the
        out-of-combat block cannot reach it (see `about_to_win`). It is printed
        with the combat move rather than on its own, because it is a decision
        about *this* turn, and armed so the run measures it like everything else.
        The latch already runs on every tick including battle ticks, so a snuff
        is caught whenever it lands, and the claim settles when the party walks
        out of the area.
        """
        adv = self.advisor
        if (adv.last_action is None or adv.last_engine is None
                or adv.last_provisional):
            return
        if not about_to_win(adv.last_engine, adv.last_action):
            return
        torch = _safe(advise_torch_live, state, self.layers.rules, False, False,
                      True, None, _safe(self._battles_left, state))
        if torch is None or torch.action != "shutdown":
            return
        self.emit(f"  TORCH  {torch.line()}")
        area = self._room_pending.board.area if self._room_pending else self._area
        self._arm_room(state, area, None, RoomAdvice(torch="shutdown"))
        # ...and tell the ACTUATOR, which is the half that was missing. This
        # method has decided the snuff correctly since it was written; nothing
        # carried the verdict to the battle plan, so `layout.TORCH_METER` sat
        # measured and unwired. `plan_battle` prefixes it: snuff, then swing.
        self._snuff_next_turn = True

    def _battle_item(self, state, engine, adv) -> tuple:
        """`(item, why)` for the acting hero, or (). Never on a look-ahead:
        an item is sent on the hero's OWN turn, where it is a free action."""
        from bridge.provision_use import battle_item    # noqa: PLC0415
        if getattr(adv, "last_provisional", False):
            return ()
        use = _safe(battle_item, state, engine, adv.last_action, adv.store,
                    getattr(self, "_item_reserve", None) or {})
        if use is None:
            return ()
        self.emit(f"  {use.line()}")
        return (use.item, use.why)

    def _battle_update(self, state: LiveState, block: str,
                       enemies_first: int, turn_key: tuple = ()) -> Update:
        """The recommendation as structure, including *why it may be early*.

        `recommend` reads ahead to the next hero so the screen is not blank
        through a run of enemy turns — measured at 17 seconds of silence, since
        the save is only written when an action resolves. 13 of 77 hero
        advisories across the capture set are that look-ahead.

        The terminal block leads with `after 2 enemy turns ->`. The first
        version of this dropped that lead and published only the move, so a
        front-end had nothing to say it with and presented a preview as an
        instruction — reported from live play as the advice not matching whose
        turn the game was on. The count and the repositioners travel with the
        update now, and a front-end that ignores them is the one at fault.
        """
        self._fought = True
        adv = self.advisor
        # **THE VERDICT WAS ONLY EVER PROSE.** `advise_retreat` prints
        # "leaving costs ~X gold, staying ~Y - RETREAT" into the block and
        # `Layers.block` turns that into a headline for the ROOM path, but
        # nothing structural came out of it and the battle path never looked.
        # Matched on a line ENDING in the word: the model's own shortlist
        # carries "alt: RETREAT on self  7%", and a bare `in` would read the
        # policy's low-ranked option as the survival layer's verdict.
        retreat_advised = any(line.rstrip().endswith("RETREAT")
                              for line in block.splitlines())
        # **Inside a fight the combat model decides.** Fran, 2026-09-23: keep
        # the parts separate, and let the dungeon layer overrule a fight only
        # when the probability of wiping is really high. Below that its
        # verdict waits for the fight to end and is acted on between fights.
        if retreat_advised and getattr(adv, "last_wipe_rate", 0.0)                 < FIGHT_RETREAT_WIPE:
            retreat_advised = False
        if adv.last_action is None or adv.last_engine is None:
            return Update(phase="battle", headline=block.splitlines()[0],
                          detail=block, tally=self.tally,
                          enemies_first=enemies_first, turn_key=turn_key,
                          retreat_advised=retreat_advised,
                          provisional=bool(enemies_first))
        engine = adv.last_engine
        actor = next((h for h in engine.heroes
                      if h.id == adv.last_action.hero_id), None)
        side, rank = advised_slot(engine, adv.last_action)
        who_acts = actor.name if actor is not None else "?"
        move = f"{who_acts}: {adv.describe(engine, adv.last_action)}"
        movers: tuple[str, ...] = ()
        if enemies_first:
            movers = tuple(adv.pending_repositioners(state, enemies_first))
            plural = "S" if enemies_first > 1 else ""
            move = f"{enemies_first} ENEMY TURN{plural} FIRST, then {move}"
        alts = []
        for action, conf in list(getattr(adv, "last_actions", []))[1:]:
            alt_side, alt_rank = advised_slot(engine, action)
            alts.append(Alternative(
                skill_slot=advised_button(state, action),
                side=alt_side, rank=alt_rank, skill_id=action.skill_id,
                headline=f"{who_acts}: {adv.describe(engine, action)}",
                confidence=conf,
                continues_turn=_continues_turn(adv, action)))
        return Update(
            phase="battle", headline=move, detail=block, side=side, rank=rank,
            actor_launches=_actor_launches(state, adv, adv.last_action),
            party_size=len(state.heroes),
            actor_class=_hero_class(state, adv.last_action),
            actor_rank=(adv.last_action.hero_id + 1
                        if 0 <= getattr(adv.last_action, "hero_id", -1)
                        < len(state.heroes) else 0),
            party_classes=tuple(h.hero_class for h in state.heroes),
            skill_slot=advised_button(state, adv.last_action),
            skill_id=adv.last_action.skill_id,
            retreat_advised=retreat_advised,
            continues_turn=_continues_turn(adv, adv.last_action),
            confidence=adv.last_confidence,
            snuff_torch=self._take_snuff(),
            battle_item=self._battle_item(state, engine, adv),
            provisional=bool(enemies_first), enemies_first=enemies_first,
            movers=movers, tally=self.tally, turn_key=turn_key,
            alternatives=tuple(alts))

    def room(self, state: LiveState, raid_file: Path, map_file: Path) -> None:
        # Standing in a room is the end of a fight, so nothing is spent any
        # more. `battles_completed` already keys the ledger; this is the same
        # boundary `ActRunner.acted` is cleared on, and it costs nothing.
        # Guarded because the advisor is a DUCK here, not a type: the room
        # layers are driven by a much smaller object in `tests/bridge/test_run.py`
        # (`_RoomOnly`), which is the seam that lets the dungeon layers be
        # replayed without a checkpoint. An unguarded call raised inside the
        # poll thread, which is the one place in this file that must not.
        forget = getattr(self.advisor, "forget_skill_uses", None)
        if forget is not None:
            forget()
        here = _safe(read_position, raid_file)
        if here:
            self._area = here
        # Leaving the area is what closes out the previous room's claims. The map
        # is a DSON parse, so it is read once here and handed to the block below
        # rather than read again by it.
        dungeon = None
        if (self._room_pending is not None and here
                and here != self._room_pending.board.area):
            dungeon = _safe(read_dungeon_map, map_file)
            self._resolve_room(state, here, dungeon)
        # Keyed on the torch BRACKET, never the raw level. The torch burns down
        # continuously while the party walks, so keying on the value reprinted
        # the whole fifteen-line block for every single point — six identical
        # blocks in one corridor in the first live run. None of the six layers
        # reads the raw number: what changes their answer is crossing a band.
        key = (here, describe_torch(self.layers.rules, state.torchlight),
               state.battles_completed,
               tuple(sorted((state.provisions or {}).items())))
        if key == self._room_key:
            return
        self._room_key = key
        self._battle_key = None
        if dungeon is None:
            dungeon = _safe(read_dungeon_map, map_file)
        if dungeon is not None:
            self._dungeon = dungeon
        block, headline, advice = self.layers.block(state, raid_file, map_file,
                                                    dungeon=dungeon, here=here)
        self.emit(block + "\n")
        # **THE SURVIVAL VERDICT REACHED THE BATTLE PATH AND NOTHING ELSE.**
        # `advise_retreat` prints "... - RETREAT" into this block for the ROOM
        # path too, and only the battle Update ever carried it as a flag -- so
        # a party told to leave kept walking as long as it was between rooms.
        # Measured 2026-09-21 on Fran's campaign: two heroes left, the
        # Houndmaster on 4 HP at 100 stress, `advise_retreat` reporting
        # P(finish intact) 0%, expected deaths 1.50, P(a death) 75% -- and the
        # bridge routed to the next room. Matched on a line ENDING in the
        # word, the same way the battle path does it, because the model's own
        # shortlist carries "alt: RETREAT on self  7%".
        retreat_advised = any(line.rstrip().endswith("RETREAT")
                              for line in block.splitlines())
        # **NOT BEFORE THE FIRST FIGHT.** A retreat on the entrance room is
        # not a survival decision, it is a verdict on the QUEST CHOICE -- and
        # by then the provisions are already bought, so acting on it pays for
        # the expedition twice and banks nothing. Measured 2026-09-22 with
        # `--survival 12` newly wired in: a fresh party at torch 100 was
        # handed a 7-fight Warrens cleanse, the rollout read P(finish intact)
        # 8% and P(a death) 58%, and the loop abandoned four seconds into the
        # raid -- twice, at about 5,000 gold of provisions each time, taking
        # the wallet 9,865 -> 5,015 -> 20.
        #
        # The gold agreed with pushing on both times (staying ~3,719 against
        # leaving ~4,778); only the 50% death floor overrode it. That floor
        # is right about the DANGER and the answer to it belongs at embark,
        # where the party or the quest can still change. What Fran asked for
        # is the other case -- *"its stuck on the hunger page or retire from
        # the dungeon"*, said of a party down to one hero on 3 HP -- and that
        # one always has fights behind it.
        # **A fight RETREATED FROM counts.** Measured 2026-09-23: every fight
        # in a Cove raid ended in a combat retreat, so `battles_completed`
        # stayed 0, this gate held the raid-level RETREAT back ten times, and
        # the party walked back into the same fight each time -- +27 stress
        # per retreat -- until three heroes died of stress at 200.
        if (retreat_advised and not (state.battles_completed or 0)
                and not getattr(self, "_fought", False)):
            retreat_advised = False
            self.emit("  (retreat advised on the entrance room and NOT sent: "
                      "that is a verdict on the quest, and the provisions "
                      "are already paid for)" + chr(10))
        self.publish(Update(phase="room", headline=headline, detail=block,
                            tally=self.tally, room_advice=advice,
                            retreat_advised=retreat_advised,
                            dungeon=dungeon, area=here or ""))
        self._arm_room(state, here or "", dungeon, advice)

    def summary(self) -> str:
        # A curio still parked when the run ends was never come back for, so it
        # settles as the disagreement it always looked like. Done here rather
        # than live because "did they come back" is only answerable at the end.
        while self._deferred:
            (area, curio), held = self._deferred.popitem()
            # The reason the claim was PARKED is the reason it now fails, and
            # without it the journal records a bare "curio DIFFER" with an
            # empty explanation — which is what Fran's fourth raid produced.
            # A disagreement a reader cannot interpret cannot be annotated
            # A/B/C, so it is a lost row rather than a recorded one.
            self._score("curio", "DIFFER", ("deferred", "curio", area, curio),
                        {"reason": f"never came back for it: {held}",
                         "area": area, "curio": curio})
        total = sum(self.counts.values())
        if not total:
            return "no recommendation was resolved against a played turn"
        parts = " ".join(f"{v} {self.counts[v]} ({self.counts[v] / total:.0%})"
                         for v in VERDICTS)
        lines = [f"{total} recommendation(s) resolved: {parts}", "", "  by layer:"]
        for layer in LAYERS:
            counts = self.by_layer.get(layer, {})
            n = sum(counts.values())
            if not n:
                continue
            cells = "  ".join(f"{v} {counts[v]:3d} ({counts[v] / n:3.0%})"
                              for v in VERDICTS)
            lines.append(f"    {layer:7s} {n:3d}   {cells}")
        if self.forced_route:
            lines.append(f"    (route: {self.forced_route} more step(s) had one "
                         f"way forward and were not scored)")
        lines += [
            "",
            "  '?' is the instrument's blind spot, not a disagreement — a "
            "missed attack leaves no trace in the save.",
            "  Layers are never pooled into one rate: a route layer at 30% "
            "would disappear inside a combat layer at 84%.",
            "  Annotate the DIFFER and the '?' lines by hand: A impossible "
            "(bridge bug), B legal but worse, C better than the advice.",
        ]
        return "\n".join(lines)


#: The town saves. Watched by their own signature, because the loop's main
#: trigger is `persist.raid.json` and that file does not move while the party
#: is in the Hamlet — which is the whole reason the town has never been
#: recorded: the loop simply `continue`d past every town visit ever made.
TOWN_FILES = ("persist.roster.json", "persist.quest.json",
              "persist.estate.json", "persist.town.json",
              "persist.upgrades.json")


def _town_sig(profile: Path) -> Optional[tuple]:
    try:
        return tuple((profile / n).stat().st_mtime_ns if (profile / n).exists()
                     else 0 for n in TOWN_FILES)
    except OSError:
        return None


def _read_town(profile: Path) -> Optional[dict]:
    """A town snapshot, or None if it cannot be read right now.

    Everything is best-effort: the game writes these while we read them, and a
    town visit that goes unrecorded is a gap in a corpus while an exception
    here is the end of the session.
    """
    try:
        from bridge.town_state import read_town_state      # noqa: PLC0415
        from data.heirlooms import read_rank_counts        # noqa: PLC0415
    except Exception:                                      # noqa: BLE001
        return None
    try:
        state = read_town_state(profile)
    except Exception:                                      # noqa: BLE001
        return None
    upgrades = {}
    try:
        upgrades = read_rank_counts(profile / "persist.upgrades.json")
    except Exception:                                      # noqa: BLE001
        pass
    try:
        return town_snapshot(state, upgrades)
    except Exception:                                      # noqa: BLE001
        return None


def _watch_town(session: Session, profile: Path, previous: Optional[dict]) -> Optional[dict]:
    """Diff the Hamlet against the last time we looked, and record what moved.

    The FIRST snapshot of a session emits a `town_opened` baseline rather than
    a diff — there is nothing to subtract from, and reporting the whole roster
    as twelve `hero_joined` events would be a lie about what just happened.
    """
    now = _read_town(profile)
    if now is None:
        return previous
    if previous is None:
        session.journal.event("town_opened", **now)
        return now
    events = diff_town(previous, now)
    for kind, payload in events:
        session.journal.event(kind, phase="town", **payload)
        session.emit(f"  TOWN   {kind}: {_town_line(kind, payload)}\n")
    return now


def _town_line(kind: str, payload: dict) -> str:
    """One readable line per town event, for the prose log beside the JSONL."""
    if kind == "wallet":
        return ", ".join(f"{k} {v['delta']:+d} (now {v['to']:,})"
                         for k, v in payload.get("changed", {}).items())
    if kind in ("hero_joined", "hero_left"):
        return f"{payload.get('class', '?')} #{payload.get('guid', '?')}"
    if kind == "hero_changed":
        moved = {k: v for k, v in payload.items()
                 if isinstance(v, dict) and "from" in v}
        return (f"{payload.get('class', '?')} #{payload.get('guid', '?')}: "
                + ", ".join(f"{k} {v['from']} -> {v['to']}" for k, v in moved.items()))
    if kind == "upgrades_bought":
        return f"{len(payload.get('trees', {}))} tree(s) advanced"
    if kind == "trinkets":
        return (f"+{len(payload.get('gained', []))} "
                f"-{len(payload.get('lost', []))} (holding {payload.get('held', 0)})")
    if kind == "recruits_gone":
        return ", ".join(payload.get("slots", {}).values()) or "coach cleared"
    return ""


def _note_deaths(session: Session, state: LiveState, seen: Optional[dict]) -> dict:
    """Emit `hero_died` for anyone who left the party mid-raid.

    **A DEATH WAS NOT AN EVENT, WHICH IS ABSURD** — it is the thing
    `REWARD_HERO_DEATH` is built around, the thing the whole retreat layer
    exists to avoid, and the corpus recorded it only as an absence. Fran's
    Shambler raid on 2026-09-03 is the case: the Highwayman goes 28 -> 22 -> 11
    -> healed to 22 -> 16 -> 12 -> 4 -> 0.0 for three turns and then simply
    stops appearing in the boards, party 4 -> 3. Every fact is present and
    nothing says "died".

    **A hero on Death's Door is still listed**, at 0 HP — that is exactly what
    those three turns are. Leaving the party list is the death, and inside a
    raid there is no other way to leave it, which is why this is sound here and
    would not be in town (where `hero_left` deliberately refuses to name a
    cause, because a dismissal looks identical).

    `seen` is `None` before the first look, so a fresh session cannot report
    the party it starts with as casualties — the same baseline rule the town
    diff and `raid_started` both follow.
    """
    party = {}
    for hero in state.heroes:
        party[hero.guid] = {"class": hero.hero_class,
                            "hp": round(float(hero.hp), 1),
                            "stress": round(float(hero.stress), 1)}
    if seen is not None:
        for guid in sorted(set(seen) - set(party)):
            gone = seen[guid]
            session.journal.event("hero_died", phase="raid", guid=guid,
                                  **{k: v for k, v in gone.items()},
                                  survivors=[h["class"] for h in party.values()])
            session.emit(f"  DEATH  {gone['class']} died "
                         f"(was on {gone['hp']:.0f} hp, {gone['stress']:.0f} stress)\n")
    return party


def _journal_path(args) -> Optional[Path]:
    """Where the structured stream goes.

    **It rides along with `--log` by default rather than needing its own flag.**
    Fran runs these from `bat/*.bat` by double-click; a corpus that only exists
    when someone remembers a second option is a corpus that does not exist.
    `--journal none` opts out.
    """
    explicit = getattr(args, "journal", None)
    if explicit:
        return None if str(explicit).lower() == "none" else Path(explicit)
    return Path(args.log).with_suffix(".jsonl") if getattr(args, "log", None) else None


def _watch(session: Session, profile: Path, interval: float, duration: float) -> None:
    raid = profile / "persist.raid.json"
    print(f"watching {profile}\n(play; Ctrl-C to stop and print the tally)\n")
    last_sig = None
    # When the raid signature last CHANGED, and whether a fight was on at that
    # moment. Both feed the stuck check below, which runs on polls that never
    # parse anything and so cannot ask the state.
    stuck_since = None
    room_stuck_runs = 0
    in_battle = False
    town_sig, town_seen = None, None
    # TRI-STATE. None = we have not looked yet, so the first poll is a
    # BASELINE and not an embark. See the raid_started event below.
    in_raid = None
    # {guid: {class, hp, stress}} as of the last in-raid poll, or None
    # before the first one. Leaving this set mid-raid IS the death.
    party_seen = None
    start = time.time()
    try:
        while time.time() - start < duration:
            time.sleep(interval)
            # THE TOWN POLL RUNS FIRST, and outside the raid signature's early
            # returns. Every `continue` below is keyed on `persist.raid.json`,
            # which does not move while the party is in the Hamlet — so putting
            # this after them is the same as not writing it. Five stat calls.
            if session.journal.enabled:
                fresh = _town_sig(profile)
                if fresh is not None and fresh != town_sig:
                    town_sig = fresh
                    town_seen = _watch_town(session, profile, town_seen)
            try:
                sig = (raid.stat().st_mtime, raid.stat().st_size) if raid.exists() else None
            except OSError:
                continue
            # **A RAID WHOSE SAVE HAS STOPPED MOVING IS A RAID NOBODY IS
            # PLAYING**, and both halves of it stall the same way: the poll
            # below skips an unchanged signature, so whatever did not land is
            # never tried again and the night dies where it stood.
            #
            # In a FIGHT the answer is usually a different hero, so the skip
            # is bumped -- bounded, once per hero. OUT of a fight there is no
            # hero to change: the room simply has to be planned again, which
            # re-opens the map, re-fits it and re-clicks. Measured live
            # 2026-09-05: a map click for `rooK` went nowhere, the save sat
            # unwritten for 180 s and the iteration ended with the party
            # standing in `rooJ` at 100 and 94 stress.
            #
            # Only a genuinely frozen raid trips this. Walking, fighting and
            # looting all write the save, so the signature moves and the timer
            # resets before it ever fires.
            if sig == last_sig and sig is not None:
                if stuck_since is None:
                    stuck_since = time.time()
                elif time.time() - stuck_since >= (
                        STUCK_SECONDS if in_battle else ROOM_STUCK_SECONDS):
                    stuck_since = time.time()
                    if in_battle and session._battle_skip < MAX_TURN_SKIPS:
                        session.emit(f"-- nothing has moved for "
                                     f"{STUCK_SECONDS:.0f}s and a fight is on; "
                                     f"trying another hero --")
                        session.note_turn_missed()
                    elif in_battle:
                        # **THE BRANCH THAT WAS NOT HERE.** Past
                        # `MAX_TURN_SKIPS` this had no `else` at all, so the
                        # heartbeat simply stopped and the loop went silent
                        # for good -- `take_replan` is the only thing that
                        # dispatches on an unmoved save, and nothing was left
                        # to set it. A retry budget is a bound on GUESSING;
                        # it must never become a bound on trying.
                        asked = getattr(session, "_battle_asked", "")
                        session.emit(
                            f"-- nothing has moved for {STUCK_SECONDS:.0f}s "
                            f"and every hero has been tried; starting the "
                            f"walk over"
                            + (f" (the panel says {asked})" if asked else "")
                            + " --")
                        session.note_turn_stuck()
                        # **The fight may be over on screen and not in the
                        # save.** Measured 2026-09-23: the last enemy died on
                        # its own turn, the save kept `in_battle` with one
                        # enemy left, and six minutes went on heroes the game
                        # was not asking. The actuator looks for enemy bars
                        # and, finding none, takes one step to make the game
                        # write the save.
                        session.publish(Update(
                            phase="phantom_fight",
                            headline="the save says a fight; is the screen?"))
                    elif not in_battle and in_raid:
                        room_stuck_runs += 1
                        session.emit(f"-- nothing has moved for "
                                     f"{ROOM_STUCK_SECONDS:.0f}s in the dungeon; "
                                     f"planning the room again "
                                     f"({room_stuck_runs}) --")
                        session.note_room_stuck()
                        # **A LOOT HOLD NOTHING CAN ACT ON IS ABANDONED.**
                        # Live 2026-09-27: objective met, a knife rack
                        # DEFERRED in the corridor behind the party "worth
                        # ~185 gold at torch 0", no walk ahead to reach it
                        # and no layer to darken the torch -- so the raid
                        # layer said CONTINUE for 27 minutes while nothing
                        # moved. After `LOOT_HOLD_GIVE_UP` stuck re-plans the
                        # held stop joins the given-up set, and the next plan
                        # prices the raid without it.
                        if room_stuck_runs % LOOT_HOLD_GIVE_UP == 0:
                            session.abandon_loot_goal()
                        # Once, at the bound. Said rather than acted on: the
                        # re-planning continues, and an unattended runner now
                        # has something to end the iteration on instead of
                        # burning its whole allowance on a frozen dungeon.
                        if room_stuck_runs == ROOM_STUCK_GIVE_UP:
                            # Ask the actuator to explain itself on the next
                            # poll. It has been refusing for the same reason
                            # every 25 s and `_say_once` has been swallowing
                            # it, so the stall report otherwise names nothing.
                            if session.on_stall is not None:
                                try:
                                    session.on_stall()
                                except Exception:          # noqa: BLE001
                                    pass
                            session.emit(
                                f"{ROOM_STUCK_LINE} "
                                f"{ROOM_STUCK_GIVE_UP} room plans in a row on "
                                f"a save that has not been written once. "
                                f"Every layer is still deciding; none of them "
                                f"has an input the game accepts from here.")
            else:
                stuck_since = None
                room_stuck_runs = 0
            # `take_replan` is what lets a dispatch happen on a save that
            # has NOT moved. Nothing else can: the wrong-hero recovery is by
            # definition a situation where nobody acted, so the file is
            # identical poll after poll.
            if sig == last_sig and not session.take_replan():
                continue
            last_sig = sig
            if sig is None:
                if session._battle_key or session._room_key:
                    session.emit("-- raid over --\n")
                    session._battle_key = session._room_key = None
                    session.publish(Update(phase="over", headline="raid over",
                                           tally=session.tally))
                continue
            # Copy before parsing, once, and hand the same staged files to every
            # layer — the map and the route reader need paths, not a LiveState,
            # and re-staging per layer would read the live save five times a
            # second while the game is writing it.
            try:
                staged = _stage([raid, profile / "persist.roster.json",
                                 profile / "persist.map.json"])
                raid_copy = staged / "persist.raid.json"
                map_copy = staged / "persist.map.json"
                state = read_live_state(raid_file=raid_copy,
                                        roster_file=staged / "persist.roster.json",
                                        map_file=map_copy)
                if not state.in_raid:
                    in_raid = False
                    party_seen = None
                    continue
                if session.layers.quest is None:
                    session.layers.quest = _safe(read_quest, raid_copy)
                    session.advisor.load_quest(raid_copy)
                if in_raid is not True:
                    # THE EMBARK, which is otherwise invisible. Buying
                    # provisions happens between the town and the raid, so it
                    # reaches the town diff as a bare gold delta with nothing
                    # to explain it — and what was bought is PLAYBOOK 1.5's
                    # whole decision. The pack as it stands on entry IS the
                    # purchase, and the raid save is the first place it exists.
                    #
                    # **`in_raid` IS TRI-STATE, and that is the whole
                    # correctness of this event.** It starts `None`, not
                    # `False`: a session begun while the party is already
                    # underground would otherwise report the first poll as an
                    # embark, and the pack it read would be mid-raid inventory
                    # rather than a purchase. Measured on the first real
                    # recording session, BOTH "embarks" fired 0.0 s after
                    # startup and neither was one. Same rule the town baseline
                    # follows one block above — a first look has nothing to
                    # subtract from — and it was not applied here.
                    kind = "raid_in_progress" if in_raid is None else "raid_started"
                    in_raid = True
                    quest = session.layers.quest
                    session.journal.event(
                        kind, phase="raid",
                        pack=dict(state.provisions or {}),
                        torch=round(float(state.torchlight or 0.0), 1),
                        party=[{"guid": h.guid, "class": h.hero_class,
                                "resolve": h.resolve_level,
                                "stress": round(float(h.stress), 1)}
                               for h in state.heroes],
                        quest={"dungeon": getattr(quest, "dungeon", ""),
                               "difficulty": getattr(quest, "difficulty", 0),
                               "length": getattr(quest, "length", 0),
                               "type": getattr(quest, "quest_type", "")}
                        if quest is not None else {})
                party_seen = _note_deaths(session, state, party_seen)
                session._latch_room(state)
                session._resolve(state)
                in_battle = bool(state.in_battle)
                if state.in_battle:
                    session.battle(state)
                else:
                    session.room(state, raid_copy, map_copy)
            except Exception as exc:              # noqa: BLE001
                # This thread is a daemon, and the overlay's only source of
                # Updates. Letting it raise takes the whole bridge silent while
                # the human keeps playing and keeps believing it is watching —
                # which is worse than any single bad poll, and is what a torn
                # save actually cost in a live run. Report and carry on.
                # `last_sig = None` so the very next tick retries THIS save
                # rather than waiting for the game to write again.
                session.emit(f"-- poll failed, retrying: {type(exc).__name__}: {exc} --\n")
                last_sig = None
    except KeyboardInterrupt:
        pass


def replay(session: Session, directory: Path) -> None:
    """Run a whole capture directory through the loop, offline.

    This is how the marking gets validated before anyone plays: the captures in
    `archive/traces/raidsnaps2/` are real turns from real fights, so replaying
    them measures the instrument's own `?` rate against evidence that already
    exists. A live run is expensive and unrepeatable; this is neither.

    Captures are ordered by the elapsed-seconds prefix `save_watch.py --capture`
    writes, which is the order they were taken in — sorting by name works only
    because that prefix is zero-padded.
    """
    triples = sorted(directory.glob("*_persist.raid.json"))
    print(f"replaying {len(triples)} capture(s) from {directory}\n")
    for raid_file in triples:
        roster = Path(str(raid_file).replace("raid.json", "roster.json"))
        map_file = Path(str(raid_file).replace("raid.json", "map.json"))
        if not roster.exists():
            continue
        try:
            state = read_live_state(raid_file=raid_file, roster_file=roster,
                                    map_file=map_file)
        except Exception as exc:                  # noqa: BLE001
            print(f"  ({raid_file.name}: {exc})")
            continue
        if not state.in_raid:
            continue
        if session.layers.quest is None:
            session.layers.quest = _safe(read_quest, raid_file)
            session.advisor.load_quest(raid_file)
        session._latch_room(state)
        session._resolve(state)
        if state.in_battle:
            session.battle(state)
        else:
            # This dispatch did not exist, and its absence is exactly why the
            # five dungeon layers had never been measured — not even offline,
            # against captures that were already on disk. `raidsnaps2` and
            # `raidsnaps4` carry a map save beside every raid save, so route,
            # curio, torch and camp can be marked without anyone playing.
            session.room(state, raid_file, map_file)



def _default_raid_path(args):
    """Where the live raid save is, for the actuator's acknowledgement.

    Best effort: an explicit --raid wins, otherwise the watched profile. None
    is tolerated -- the runner then sends without confirming, which is what it
    did before, rather than refusing to act at all.
    """
    if getattr(args, "raid", None):
        return Path(args.raid)
    try:
        return find_profile(args.profile) / "persist.raid.json"
    except Exception:                             # noqa: BLE001
        return None


def _boot(args) -> bool:
    """Click through the front end into a new campaign. See `bridge/menus.py`.

    Creating a save slot IS the embark: the shipped template has
    `inraid: true, raiddungeon: "tutorial"`, so a new campaign starts inside
    the Old Road with no town step. That makes the tutorial -- whose layout is
    hand-authored and identical every run -- reachable in one command, which is
    the whole reason this exists.

    Armed by `--act` like everything else in layer 3. Without it the boot is a
    dry run: it names each screen and the click it would send, and then stops
    rather than watching a game that never left the menu.
    """
    try:
        from bridge import menus, screen
    except Exception as exc:                      # noqa: BLE001
        print(f"boot unavailable: {exc}")
        return False
    if not screen.load_refs():
        print("no screen references - run: python bridge/screen.py --calibrate")
        return False
    runner = menus.build(armed=args.act, slot=args.boot_slot,
                         backend=args.backend or "post-message")
    if runner is None:
        # Two causes, and they were reported as one line until 2026-09-06.
        # `window.diagnose` separates "not running" from "running but not
        # drawable", which is what an unplugged monitor looks like from here.
        from bridge.layout import load as load_layout      # noqa: PLC0415
        from bridge.window import diagnose                 # noqa: PLC0415
        if load_layout() is None:
            print("no calibrated layout - run: python bridge/layout.py "
                  "--resolve")
        else:
            print(diagnose())
        return False
    print(f"boot: slot {runner.slot}, "
          f"{'ACTING' if args.act else 'dry run'}, F9 stops")
    if not runner.run():
        print("boot did not reach a raid - not watching")
        return False
    # The game writes the profile as the raid starts, and `find_profile` picks
    # the most recently touched one. Give it a moment to be the new one.
    time.sleep(2.0)
    return True


def _build_actuator(args, raid_path=None):
    """An `ActRunner`, or None when layer 3 is not wanted.

    Returns None unless `--act` or an explicit `--backend` was given, so the
    default `run.py` invocation is byte-for-byte the advisor it has always
    been. Layer 3 is opt-in at the command line, and armed only by `--act`;
    naming a backend alone gets you the plan printed, nothing sent.
    """
    if not (args.act or args.backend):
        return None
    try:
        from bridge.act import ActRunner, DEFAULT_BACKEND, choose_backend
        from bridge.layout import load as load_layout
        from bridge.window import find_window, describe
    except Exception as exc:                      # noqa: BLE001
        print(f"layer 3 unavailable: {exc}")
        return None

    win = find_window()
    if win is None:
        print("the game is not running - layer 3 disabled for this session")
        return None
    lay = load_layout()
    if lay is None:
        print("no calibrated layout - run `python bridge/overlay.py --calibrate`")
        return None
    resolved = lay.resolve(win)
    if resolved.aspect_warning:
        print(f"WARNING: {resolved.aspect_warning}")

    backend = choose_backend(win, args.backend or DEFAULT_BACKEND)
    # The actuator's own log handle, onto the SAME file the session writes.
    # `_say` has always supported this and was never given one, so every
    # `[act]` line -- the plan, the refusal, the pause and its reason -- went to
    # stdout and nowhere else. The first live run that misbehaved could not be
    # diagnosed from its own log, which is the whole point of keeping one.
    # Both handles append and flush per line, so the two streams interleave
    # into one chronological record.
    act_log = None
    if args.log:
        try:
            act_log = open(args.log, "a", encoding="utf-8", buffering=1)
        except OSError as exc:                    # noqa: BLE001
            print(f"could not open {args.log} for the actuator: {exc}")
    runner = ActRunner(win, resolved, backend,
                       armed=args.act, confirm=args.confirm,
                       raid_path=raid_path, log=act_log,
                       full_pack=getattr(args, "full_pack", "pause"))
    print(describe(win))
    print(f"\nLAYER 3 {'ARMED' if args.act else 'DRY RUN'} "
          f"via {backend.name}"
          f"{', asking before every combat turn' if args.confirm else ''}")
    print("  F9 stops it. The game must stay FOCUSED - every working input")
    print("  path needs that, and it WAITS for focus rather than skipping.")
    if args.act and not args.confirm:
        print("  It acts WITHOUT asking. Watch it. --confirm to be asked.")
    if args.act:
        # The operator has, by definition, just typed this command, so the
        # TERMINAL has focus and the game does not. Without a grace period the
        # first turn arrives unfocused -- and a turn skipped for focus is GONE,
        # because Session.battle dedupes on _battle_key and never re-offers it.
        import time as _t
        print()
        for i in range(GRACE_SECONDS, 0, -1):
            print(f"  click the GAME now - starting in {i}s")
            _t.sleep(1)
    print()
    return runner


def build_parser() -> argparse.ArgumentParser:
    """The CLI, split out of `main` so the flags can be tested without a game.

    Worth its own function for one flag: `--act` used to arm five layers
    unsupervised and then gate the sixth on a stdin prompt, which is not a
    safety property anyone would design and did read as "it didn't play" in
    live use. A default that surprising deserves a test, and a parser buried
    inside `main` next to a window handle cannot have one.
    """
    ap = argparse.ArgumentParser(
        description="every dungeon layer, one loop, one log")
    ap.add_argument("--model", default="models/dd_combat_ppo_full_expedition.zip")
    ap.add_argument("--profile", default=None)
    ap.add_argument("--raid", default=None, help="replay one captured snapshot")
    ap.add_argument("--roster", default=None)
    ap.add_argument("--replay", default=None, metavar="DIR",
                    help="run a whole capture directory through the loop "
                         "offline and print the marking tally")
    ap.add_argument("--once", action="store_true", help="print one block and exit")
    ap.add_argument("--fate", action="store_true",
                    help="read the game's next rolls from the save (DECOMP.md F15) "
                         "and let them veto the policy's pick; says so in the log")
    ap.add_argument("--log", default=None, metavar="FILE")
    ap.add_argument("--journal", default=None, metavar="FILE",
                    help="structured JSONL beside --log: every marked decision, "
                         "the board it was made on, and every change to the "
                         "Hamlet. Defaults to the --log path with a .jsonl "
                         "suffix; pass a path to override, or 'none' to skip.")
    ap.add_argument("--no-playbook-heals", action="store_true",
                    help="do not apply PLAYBOOK 3.2 to the shortlist "
                         "- let the policy's own heal ranking stand")
    ap.add_argument("--survival", type=int, nargs="?", const=12, default=0,
                    metavar="EPISODES",
                    help="price the retreat by simulating what is left of the "
                         "dungeon this many times (default 12)")
    ap.add_argument("--combats-total", type=int, default=None)
    # --- layer 3 ---
    ap.add_argument("--act", action="store_true",
                    help="PLAY, do not just print. Off by default. Arms all "
                         "six layers: combat, loot, curios, movement, the map "
                         "click and the pack. F9 stops it.")
    ap.add_argument("--confirm", action="store_true",
                    help="with --act, ask on stdin before each COMBAT turn. "
                         "Only combat -- the five out-of-combat layers have "
                         "never asked and this does not make them. It BLOCKS "
                         "the poll loop while it waits, so an unanswered "
                         "prompt is a stopped bridge.")
    ap.add_argument("--auto", action="store_true",
                    help=argparse.SUPPRESS)      # what --act alone now means
    ap.add_argument("--backend", default=None,
                    choices=["post-message", "send-input", "scancode", "dry"],
                    help="how to send input. Default send-input: it moves your "
                         "real cursor, but post-message's CLICK does not "
                         "register in DD (its move does). All paths need the "
                         "game focused.")
    ap.add_argument("--full-pack", default="pause", choices=["pause", "abandon", "swap"],
                    help="when the pack is full and nothing in it is free to "
                         "throw away: 'pause' (default) hands over to you; "
                         "'abandon' closes the loot window and walks on. The "
                         "bridge cannot see what is in that window, so abandon "
                         "leaves value behind WITHOUT being able to say how "
                         "much. Chaff is discarded automatically either way.")
    ap.add_argument("--interval", type=float, default=1.0)
    ap.add_argument("--duration", type=float, default=7200.0)
    ap.add_argument("--boot", action="store_true",
                    help="click through the front end into a NEW campaign "
                         "(the Old Road tutorial) before watching")
    ap.add_argument("--boot-slot", type=int, default=None,
                    help="save slot to create; default is the first free one")
    return ap


def main() -> int:
    args = build_parser().parse_args()

    model = args.model if args.model and Path(args.model).exists() else None
    if args.model and model is None:
        print(f"note: {args.model} not found — listing legal actions instead")
    advisor = Advisor(model,
                      total_combats=args.combats_total or DEFAULT_TOTAL_COMBATS,
                      combats_total_explicit=args.combats_total is not None,
                      survival_episodes=args.survival,
                      playbook_heals=not args.no_playbook_heals,
                      fate=args.fate)

    # The boot phase runs FIRST and everything below is resolved afterwards.
    # `find_profile` returns the most recently touched campaign, and a
    # campaign created during the boot IS a new profile directory -- so
    # resolving the raid path before booting would leave the actuator
    # acknowledging against the previous campaign's save and the watcher
    # polling a directory the party is no longer in.
    if args.boot and not _boot(args):
        return 1

    runner = _build_actuator(args, _default_raid_path(args))
    session = Session(advisor, Path(args.log) if args.log else None,
                      on_update=runner.handle if runner else None,
                      journal_path=_journal_path(args))
    if runner is not None:
        # What the bridge played comes back the other way, for the one thing
        # the save cannot answer: which per-battle-limited skills are spent.
        runner.on_played = advisor.note_skill_used
        # ...and what it could NOT play, for the one thing the save gets
        # wrong: whose turn it is. See `Session.note_turn_missed`.
        runner.on_turn_missed = session.note_turn_missed
        # ...and which curios it has stopped trying, so `_raid_end` does not
        # hold the party in the dungeon for something nothing will collect.
        session.layers.abandoned = runner.gave_up_on
        # ...and whether the pack has refused loot, which ends a loot chase.
        session.layers.pack_full = lambda: runner.pack_full_seen
        # ...and the provisions the rest of the raid needs, so a full pack
        # can make room for loot out of the surplus only.
        runner.item_reserve = lambda: getattr(session.layers, "_item_reserve",
                                              None)
        # ...and when the dungeon stops responding, let it say why rather
        # than leaving the last refusal sixty lines up the log.
        session.on_stall = runner.explain_again
    if args.log:
        print(f"logging to {args.log}\n")

    if args.replay:
        replay(session, Path(args.replay))
        print("\n" + session.summary())
        if runner:
            print(runner.summary())
        # Replay is how the marking is validated offline, so it must produce
        # the same journal a live watch does — otherwise the format is only
        # ever exercised against a running game and breaks unobserved.
        session.journal.event("session_end",
                              by_layer={k: dict(v) for k, v in session.by_layer.items()},
                              totals=dict(session.counts), source="replay")
        session.journal.close()
        return 0

    if args.raid or args.once:
        if args.raid:
            raid_file = Path(args.raid)
            roster = Path(args.roster) if args.roster else Path(
                str(args.raid).replace("raid.json", "roster.json"))
        else:
            profile = find_profile(args.profile)
            staged = _stage([profile / "persist.raid.json",
                             profile / "persist.roster.json",
                             profile / "persist.map.json"])
            raid_file, roster = staged / "persist.raid.json", staged / "persist.roster.json"
        map_file = raid_file.parent / raid_file.name.replace("raid", "map")
        state = read_live_state(raid_file=raid_file, roster_file=roster, map_file=map_file)
        if not state.in_raid:
            print("not in a raid")
            return 0
        session.layers.quest = _safe(read_quest, raid_file)
        advisor.load_quest(raid_file)
        if state.in_battle:
            session.battle(state)
        else:
            session.room(state, raid_file, map_file)
        return 0

    from bridge.game_options import warn as warn_options   # noqa: PLC0415
    warn_options(find_profile(args.profile))
    _watch(session, find_profile(args.profile), args.interval, args.duration)
    print("\n" + session.summary())
    if session.log is not None:
        session.log.write(f"\n{session.summary()}\n")
    # The per-layer tally as data, so a session is self-describing: a reader
    # pooling many journals can weight them without re-deriving the counts.
    session.journal.event("session_end",
                          by_layer={k: dict(v) for k, v in session.by_layer.items()},
                          totals=dict(session.counts))
    session.journal.close()
    if session.journal.path is not None:
        print(f"structured journal: {session.journal.path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
