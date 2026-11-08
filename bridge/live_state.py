"""Read the real game's live state from its save files — the bridge's eye.

The `OutputDebugStringA` channel emits no HP, no stress, no torchlight and no
buffs, which looked like it ruled out ever driving the real game. It does not:
the game persists all of that, every 1-5 seconds, *including during a fight*.

Two files, both decoded by `tools/dson.py`:

* **`persist.raid.json`** — `torchlight`, the party's hero guids in formation
  order, traversal position, inventory, and while a fight is running a whole
  `battle` block: `round`, `currentturn`, the battle `seed`, the full
  `initiative` table, the enemy records in rank order, and the consecutive
  stun/dodge/miss counters.
* **`persist.roster.json`** — per-hero state: the `actor` sub-record
  (`current_hp`, `stunned`, `combat_ready`, `current_mode_id`,
  `health_damage_blocks`, `buff_group`, `actor_dot`) plus `m_Stress`,
  `affliction_type_id`, `virtue_type_id`, `resolveXp`, `weapon_rank`,
  `armour_rank`, and the equipped `skills`/`trinkets`/`quirks`.

**`persist.raid.json` exists only while a raid is in progress** and is deleted
on the way out, which is why it went unnoticed for so long. Capture versions of
it with `tools/save_watch.py --capture`.

Ids line up with this repo's `DataStore` directly: `monster_class` reads back as
`carrion_eater_A` / `swine_reaver_A`, `heroClass` as `man_at_arms`, and
`selected_combat_skills` as `chop` / `hew` / `solemnity`, so no name resolution
is needed to seed the engine.

**`actor.current_hp` is absolute HP, and only meaningful for heroes currently on
the raid.** This took two wrong readings to pin down. First it looked absolute
(29.5, 24.0), then several heroes read exactly 1.0 at embark so it looked like a
fraction — but pairing a captured battle snapshot with the roster settled it:
the party in that fight read 35 / 25 / 27 / 22, matching Leper / Bounty Hunter /
Musketeer / Plague Doctor max HP. The 1.0 readings belong to heroes left behind
in town, whose `actor` record is simply not maintained. Monsters are absolute
too (11, 21, 15). So: trust the value for heroes in `state.party_guids`, ignore
it for everyone else — which is what `read_live_state` already does by
filtering `heroes` to the party.

**Enemy list order IS rank order.** `battle.enemies` reorders when a monster
dies (the survivors close ranks) and a slain one is replaced in place by a
`corpse_A` record, which is exactly the engine's own model — see
`_collapse_enemy_position_gaps` in `engine/combat_engine.py`.

**Turn order is derived, not stored flat.** `battle.initiative` holds two maps,
`heroes` (keyed to `roster_guid`) and `monsters` (keyed to `battle_guid`), each
entry carrying an `initiative` value that is a **float32 in an int field** — the
same `Field.as_float()` treatment `m_Stress` needs. Merging both maps and
sorting descending reconstructs the single interleaved queue the engine models.
Observed in one round: `14.007, 10.004, 9.005, 7.007, 5.003, 3.003, 2.002`; the
integer part is the initiative roll and the fraction is a tiebreaker.

**Everything temporary lives in `actor.buff_group`, including DoTs.** The
sibling `actor_dot` field looks like the obvious home for bleed and blight and
is empty in all 445 captured snapshots — including the ones where a Highwayman
lost exactly 2 HP a round for three rounds. That bleed is a `buff_group` entry.
See `bridge/seed.py` for the `stat_type` mapping and the evidence behind it.

**Do not use `currentturn` to decide whose turn it is.** It does index the
merged order, but it is written to disk *after* the turn it names has already
resolved, so following it is reliably one turn late. That was the first
implementation and it failed in live play exactly the way you would predict:
when the game asked for the Man-at-Arms, the advice on screen was still the
Vestal's. Reproduced offline afterwards against `archive/traces/raidsnaps2/`.

**`actor.performing_turn` is the ground truth** — a per-combatant count of
turns TAKEN this battle (not a boolean flag; that misreading cost time too).
Anyone who has acted in round R has a counter of at least R, so the next
actor is the first queue entry still below it, skipping the stunned. That
lands a full save-write earlier: it names the next hero in the very write that
records the previous one finishing. `LiveState.current_actor` does this, and
falls back to `currentturn` only when no roster is paired with the raid file.

Usage:
    python bridge/live_state.py                     # read the live profile
    python bridge/live_state.py --raid <snapshot>   # read a captured snapshot
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data.dson import Field, is_complete, parse_file

SAVE_ROOT = Path(r"D:\Program Files (x86)\Steam\userdata")
PROFILE_GLOB = "*/262060/remote/profile_*"

# Duplicated from env/gym_env.py, same as every tests/test_*.py does. This
# module deliberately does NOT go through DataLoader: it must stay a pure save
# reader that costs milliseconds, so the advisor can poll it in a loop. The
# only game file it touches is the resolve threshold table below.
from data.paths import GAME_DIR

# A monster record whose class starts with this is a corpse occupying a rank,
# not a fighter.
#
# **A PREFIX, not the single id it used to be.** This was `== "corpse_A"`, and
# the game ships EIGHT corpse classes: `corpse_A..D` and `corpse_large_A..D`,
# one pair per difficulty tier. So only the Apprentice-tier small corpse was
# recognised. Every other corpse fell through as an ordinary monster, was not
# found in `store.enemies`, and `bridge/seed.py` then refused the whole board:
# `cannot seed: unknown monster id(s): ['corpse_large_A']`, after which the
# advisor is silent for the rest of the fight.
#
# It hid because the Old Road is Apprentice and its monsters are small, so the
# only corpse the tutorial can produce is the one id that worked. Found by the
# overnight loop on its second iteration, the first time something large died.
CORPSE_CLASS_PREFIX = "corpse_"
# Kept because it names the specific class the tutorial produces and reads
# clearly in tests; it is NOT what the check keys on any more.
CORPSE_MONSTER_CLASS = "corpse_A"

# The party inventory keys the engine's in-combat provisions by. Food ships
# with an EMPTY id in the save (type "provision"), which is why it is matched
# on type rather than id below.
SUPPLY_IDS = ("bandage", "antivenom", "holy_water", "laudanum")


@dataclass
class LiveBuff:
    """One entry of an `actor.buff_group` — the game's universal modifier slot.

    Everything temporary a combatant carries lives here: skill buffs and
    debuffs, guard, and (the surprise) **damage-over-time**. `actor_dot` sits
    next to it in the same record and is empty in all 445 captured snapshots,
    including ones where a hero visibly bled out 2 HP a round — the DoT is a
    `buff_group` entry with `stat_type` 5.

    Kept raw on purpose. Translating `stat_type` (an int into the game's own
    enum) into the engine's vocabulary needs the DataStore, so it happens in
    `bridge/seed.py`; this module stays a pure save reader.

    `amount` is a float32 in an int slot, same as `m_Stress` and `initiative`.
    """
    stat_type: int
    stat_sub_type: str = ""
    amount: float = 0.0
    duration: Optional[int] = None
    duration_type: str = ""
    buff_id: str = ""            # "" for an inline buff, else a base.buffs.json id
    # What granted this buff (the game's `bsrc_*` enum). Its values are not
    # decoded, and do not need to be: entries granted by one event share one
    # `source`, which is enough to recognise a multi-entry group such as
    # riposte by equality alone.
    source: int = 0
    # Set only on a guard marker; the guid of the combatant on the other end.
    guarded_guid: int = 0
    # Which damage type applied this, when it is a DoT: 5 and 6 are the only
    # values observed, and 6 appears exclusively in blight-only encounters.
    damage_source_type: int = 0


@dataclass
class LiveHero:
    guid: int
    hero_class: str = ""
    stress: float = 0.0
    hp: float = 0.0                  # absolute; valid only while on the raid
    stunned: bool = False
    # Trait ids, straight into store.traits / CarryOverHeroState — the save
    # stores these as length-prefixed STRINGS ("" when neither applies), not
    # as the int name-hash the rest of the format uses for type ids.
    affliction_id: Optional[str] = None
    virtue_id: Optional[str] = None
    mode_id: Optional[str] = None
    # Death's Door history, for the engine's sticky mortality tier.
    visited_deaths_door: bool = False
    has_had_heart_attack: bool = False
    # actor.performing_turn — turns TAKEN this battle. Ground truth for whose
    # turn comes next; see LiveState.current_actor.
    turns_taken: int = 0
    resolve_xp: int = 0
    resolve_level: int = 0
    weapon_rank: int = 0
    armour_rank: int = 0
    # The 4 combat skills this hero actually has equipped, in the order the
    # save lists them. Feeds CombatEngine.reset(hero_skill_ids=...).
    selected_skills: list[str] = field(default_factory=list)
    camping_skills: list[str] = field(default_factory=list)
    trinkets: list[str] = field(default_factory=list)
    #: `{equipment slot: trinket id}` -- the slot KEYS `trinkets` drops, which a
    #: drag off the hero sheet needs (slot 1 can be filled with slot 0 empty).
    trinket_slots: dict = field(default_factory=dict)
    quirks: list[str] = field(default_factory=list)
    buffs: list[LiveBuff] = field(default_factory=list)


@dataclass
class LiveEnemy:
    enemy_id: str            # matches DataStore.enemies, e.g. "swine_reaver_A"
    name: str = ""
    hp: float = 0.0
    stunned: bool = False
    # Index in `battle.enemies`, which is rank ORDER but NOT a rank number:
    # a size-2 monster covers two ranks, so everything behind it sits one rank
    # further back than its slot suggests. Deriving the real rank needs each
    # monster's `size`, which needs the DataStore — so it is done in
    # `bridge/seed.py`, and this stays what the file literally says. Reading a
    # slot as a rank is what announced a rank-4 Fusilier as rank 3.
    slot: int = 0
    battle_guid: Optional[int] = None
    is_corpse: bool = False
    turns_taken: int = 0     # actor.performing_turn — a COUNT, not a flag
    # For a corpse, `monster_class` is the literal string "corpse_A" and the
    # monster it came from is recorded here as the format's own NAME HASH
    # (see data.dson.dd_name_hash). Left as the raw int: resolving it needs
    # the DataStore, and this module stays a pure save reader.
    previous_class_hash: Optional[int] = None
    buffs: list[LiveBuff] = field(default_factory=list)


@dataclass
class TurnEntry:
    side: str                # "hero" | "monster"
    guid: int                # roster_guid for heroes, battle_guid for monsters
    initiative: float
    # actor.performing_turn — how many turns this combatant has TAKEN in this
    # battle. Filled in after both files are read (heroes live in the roster).
    turns_taken: int = 0
    stunned: bool = False


@dataclass
class LiveState:
    in_raid: bool = False
    torchlight: float = 0.0
    area_tile: Optional[int] = None
    in_battle: bool = False
    battle_round: Optional[int] = None
    battle_turn: Optional[int] = None
    battle_seed: Optional[int] = None
    party_surprised: bool = False
    monsters_surprised: bool = False
    party_guids: list[int] = field(default_factory=list)
    # How many heroes embarked, which is NOT len(party_guids) once someone
    # has died — the expedition-context observation needs the original size.
    start_party_size: int = 0
    heroes: list[LiveHero] = field(default_factory=list)
    enemies: list[LiveEnemy] = field(default_factory=list)
    turn_order: list[TurnEntry] = field(default_factory=list)
    # Combats finished in THIS raid so far — real, from the raid's own stat
    # database, not counted by the watcher. Feeds the expedition-context
    # features the full-expedition policy was trained with.
    battles_completed: int = 0
    # Fights the dungeon still holds, from persist.map.json's unconsumed
    # `mash_index` tiles. None when no map file was available. See
    # `_read_remaining_battles` — it is a floor, since a camping ambush is a
    # real fight that never had a tile.
    remaining_battles: Optional[int] = None
    # {supply id: amount}, summed across the inventory's split stacks.
    provisions: dict[str, int] = field(default_factory=dict)

    @property
    def current_actor(self) -> Optional[TurnEntry]:
        """Whose turn it is ABOUT to be — the first combatant in the queue who
        has not yet acted this round.

        **Not `turn_order[currentturn]`.** That was the first implementation
        and it is reliably one turn late, which is a real defect and not a
        rounding detail: `currentturn` is written to disk *after* the turn it
        names has already resolved. Measured across a captured fight, the
        write labelled `t1` already showed the Vestal's `performing_turn` at 1
        — she had played. Following it meant that when the game asked for the
        Man-at-Arms, the advice on screen was still the Vestal's. Reported
        from live play, then reproduced offline against the capture.

        `actor.performing_turn` counts turns taken this battle, so a
        combatant who has acted in round R has a counter of at least R. The
        next actor is the first queue entry still below that, skipping anyone
        stunned (their turn is consumed without the counter moving).

        Two refinements, both from a second live run:

        **The queue is consumed in order, so whoever has acted is a PREFIX of
        it.** Scanning from the last combatant known to have acted, rather than
        from the front, is what keeps a trailing counter from blocking the
        rest of the round — a combatant stunned through one round comes back
        permanently one behind (observed: a Fungal Bloat sat at 0 while the
        party was at 2), and a front-of-queue entry like that would otherwise
        be reported as "next" for the whole fight.

        **Returning None is a real answer.** When the only combatant left to
        act is stunned, its turn is consumed without it doing anything and the
        round simply ends. The first version fell back to `currentturn` here,
        which re-announced a hero who had already played — reported from live
        play as the text "not updating properly" after two monster turns in a
        row. Holding the previous output is correct; re-announcing a stale
        hero is not."""
        pending = self.pending_queue()
        return pending[0] if pending else None

    def pending_queue(self) -> list[TurnEntry]:
        """Everyone still to act this round, in order, stunned excluded.

        A stunned combatant's slot is consumed without it acting, so it is not
        part of what is coming."""
        queue = self.turn_order
        if not queue:
            return []
        rnd = self.battle_round or 1
        acted = [t.turns_taken >= rnd for t in queue]
        last_acted = max((i for i, a in enumerate(acted) if a), default=-1)
        return [queue[i] for i in range(last_acted + 1, len(queue))
                if not acted[i] and not queue[i].stunned]

    def next_hero(self, skip: int = 0) -> tuple[Optional[TurnEntry], int]:
        """The next HERO to act, and how many enemy turns land before them.

        The save is only written when an action resolves, so a run of enemy
        turns produces no update at all — measured at 17 seconds of silence
        between two hero turns. Advising only for `current_actor` therefore
        leaves the previous hero's move on screen for that whole window, which
        is what "it doesn't move on to whoever's turn it is" looked like from
        the outside.

        Reading ahead to the next hero removes that dead air: the advice is
        already up when the game finally asks for the move. The board can shift
        while the enemies act, so a caller showing this must say how many enemy
        turns it is looking past.

        **`skip` IS A RECOVERY, NOT A CHOICE, and the counters cannot do
        better** (2026-09-05). Graded over the capture corpus on the 110 pairs
        where a hero demonstrably acted, this rule is right 108 times — and so
        are both alternatives anyone would reach for (fewest-turns-first, and
        `currentturn + 1`). All three miss the same two. So there is no better
        ruler to swap in, and the 2% has to be recovered from instead.

        It is worth recovering because the failure is fatal rather than
        cosmetic: the game asks for hero B, the bridge presses B's slot number
        while advising A's skill, the click lands nowhere, and after three
        retries the run stops with "play it yourself". Seen live in the Cove —
        the save said Runaway, the screen said Crusader, and the raid ended
        there. `skip=1` hands back the hero after the one that would not move.
        """
        enemies_first = 0
        for entry in self.pending_queue():
            if entry.side == "hero":
                if skip <= 0:
                    return entry, enemies_first
                skip -= 1
                continue
            enemies_first += 1
        return None, enemies_first

    def hero_for_turn(self, skip: int = 0) -> tuple[Optional[TurnEntry], int]:
        """`next_hero`, with the wrap past the pending queue built in.

        THE ONE RESOLVER, and it exists because there were briefly two.
        `run.py` needs the hero to build its dedupe key and to arm the claim;
        `advisor.py` needs the same hero to seed the engine. When each wrapped
        the end of the queue its own way they disagreed at `skip >= 1` -- the
        key and the armed guid named one hero while the advice was for
        another, which is the same class of bug the skip exists to fix.
        """
        entry, enemies_first = self.next_hero(skip)
        if entry is not None or not skip:
            return entry, enemies_first
        # Past the end of the pending queue. Both corpus misses name a hero
        # the prefix scan EXCLUDED, so the last resort is any other living
        # hero rather than "nobody".
        first, _ = self.next_hero()
        others = self.other_heroes(first.guid if first else -1)
        if not others:
            return None, enemies_first
        return others[(skip - 1) % len(others)], 0

    def other_heroes(self, guid: int) -> list[TurnEntry]:
        """Every other living hero on the board, in queue order.

        The fallback beyond `pending_queue`. Both of the corpus misses name a
        hero the prefix scan has already EXCLUDED, so a `skip` that only walks
        the pending list cannot reach them; this can.
        """
        return [t for t in self.turn_order
                if t.side == "hero" and t.guid != guid and not t.stunned]

    def hero_by_guid(self, guid: int) -> Optional[LiveHero]:
        return next((h for h in self.heroes if h.guid == guid), None)


def load_resolve_thresholds(game_dir: Path = GAME_DIR) -> list[int]:
    """Resolve XP needed per resolve level, from the real game data.

    `campaign/roster/roster.variables.json` ships
    `resolve_level_thresholds: [0, 2, 8, 14, 24, 36, 48]` — seven entries for
    resolve levels 0-6. That makes `resolveXp -> level` game data rather than
    a simulator convention, so it is read rather than hardcoded."""
    path = game_dir / "campaign" / "roster" / "roster.variables.json"
    with open(path, encoding="utf-8-sig") as fh:
        return list(json.load(fh)["resolve_level_thresholds"])


def resolve_level_for_xp(xp: int, thresholds: list[int]) -> int:
    """Highest level whose threshold the hero's XP has reached."""
    level = 0
    for i, need in enumerate(thresholds):
        if xp >= need:
            level = i
    return level


def _find(node: Optional[Field], name: str) -> Optional[Field]:
    """Depth-first search by field name. The battle block sits several levels
    down and its exact nesting is not worth hardcoding."""
    if node is None:
        return None
    if node.name == name:
        return node
    for child in node.children or []:
        hit = _find(child, name)
        if hit is not None:
            return hit
    return None


def _val(node: Optional[Field], name: str, default=None):
    child = node.get(name) if node is not None else None
    return default if child is None else child.value


def _flt(node: Optional[Field], name: str, default=0.0) -> float:
    child = node.get(name) if node is not None else None
    if child is None:
        return default
    got = child.as_float()
    return default if got is None else got


def _child_names(node: Optional[Field], key: str) -> list[str]:
    """Names of the children of `node[key]`. Equipped skills and quirks are
    stored keyed BY ID, so the ids are the keys."""
    sub = node.get(key) if node is not None else None
    return [c.name for c in (sub.children or [])] if sub is not None else []


def _item_slots(node: Optional[Field], key: str) -> dict:
    """`{slot: id}` of an inventory-style block keyed by slot number."""
    sub = node.get(key) if node is not None else None
    out = {}
    for slot in (sub.children or []) if sub is not None else []:
        item_id = _val(slot, "id")
        if item_id and str(slot.name).isdigit():
            out[int(slot.name)] = str(item_id)
    return out


def _item_ids(node: Optional[Field], key: str) -> list[str]:
    """Ids of an inventory-style block, where the KEYS are slot numbers.

    Trinkets are the exception to `_child_names`: `trinkets.items` is keyed
    "0"/"1" with the real id inside each slot. Reading the keys gave
    `['0', '1']`, which resolves against nothing, so trinkets were silently
    equipped as no-ops — including `health_stone`, whose max-HP buff is the
    reason a hero could read 38.75 HP that the sim had no room for."""
    sub = node.get(key) if node is not None else None
    out = []
    for slot in (sub.children or []) if sub is not None else []:
        item_id = _val(slot, "id")
        if item_id:
            out.append(str(item_id))
    return out


def find_profile(explicit: str | None = None) -> Path:
    """The campaign profile to watch: the most recently touched one.

    **A `profile_*` directory is not necessarily a campaign.** The Butcher's
    Circus keeps its own profile alongside them, holding
    `persist.circus_estate.json` and `persist.roster.network.json` and **no
    `persist.game.json` at all** — and on this machine it is the most recently
    touched of the three, so the plain mtime heuristic returned it. Everything
    downstream then reads a directory that can never contain a raid, and waits
    forever without saying why. `persist.game.json` is the file every campaign
    has and nothing else does, so it is the filter.

    The fallback to unfiltered profiles is deliberate: if no directory looks
    like a campaign, returning the newest one and letting the caller fail on
    real data beats raising here, where the message would be less specific.
    """
    if explicit:
        return Path(explicit)
    profiles = sorted(SAVE_ROOT.glob(PROFILE_GLOB))
    if not profiles:
        raise SystemExit(f"no save profile under {SAVE_ROOT}")
    campaigns = [p for p in profiles if (p / "persist.game.json").exists()]
    return max(campaigns or profiles, key=lambda p: max(
        (f.stat().st_mtime for f in p.glob("persist.*.json")), default=0))


_STAGE_ATTEMPTS = 6
_STAGE_BACKOFF = 0.15


def _stage(paths: list[Path]) -> Path:
    """Copy before parsing. Never hold a read handle on a file the game may be
    mid-write on — doing that is suspected to have contributed to a crash once,
    and copying costs nothing at these sizes.

    **A copy that succeeded can still hold a torn save.** Retrying only on
    `OSError` covers the case where Windows refuses the read, not the case where
    it grants it and hands back half a file. Validate the bytes and retry; see
    `tools/dson.is_complete`.

    **And the staging directory is per-process, because the readers collide with
    each other far more reliably than any of them collides with the game.** The
    destination name was fixed, so two bridge tools running at once — and
    `overlay.py` *contains* `run.py`'s loop, so "run both" was a documented way
    to get exactly two — would `copy2` into the same file while the other read
    it. Both wake on the same mtime change within milliseconds, which makes that
    a near-certainty rather than a race, and it is what actually crashed a live
    run. Racing the game's own write is possible too; this was the cause."""
    tmp = Path(tempfile.gettempdir()) / f"dd_live_state_{os.getpid()}"
    tmp.mkdir(exist_ok=True)
    for src in paths:
        if not src.exists():
            continue
        dst = tmp / src.name
        for _ in range(_STAGE_ATTEMPTS):
            try:
                shutil.copy2(src, dst)
            except OSError:
                time.sleep(_STAGE_BACKOFF)
                continue
            try:
                if is_complete(dst.read_bytes()):
                    break
            except OSError:
                pass
            time.sleep(_STAGE_BACKOFF)
    return tmp


def _read_buffs(actor: Optional[Field]) -> list[LiveBuff]:
    """Decode `actor.buff_group`, whose keys are the game's own buff guids.

    Slot keys are meaningless here (they are that combatant's running
    `buff_group_next_guid`), so only the values are kept. Entries with no
    readable `amount` are dropped rather than guessed at."""
    group = actor.get("buff_group") if actor is not None else None
    out: list[LiveBuff] = []
    for slot in (group.children if group is not None else []):
        stat_type = _val(slot, "stat_type")
        if stat_type is None:
            continue
        out.append(LiveBuff(
            stat_type=int(stat_type),
            stat_sub_type=str(_val(slot, "stat_sub_type") or ""),
            amount=_flt(slot, "amount"),
            duration=_val(slot, "duration"),
            duration_type=str(_val(slot, "duration_type") or ""),
            buff_id=str(_val(slot, "id") or ""),
            source=int(_val(slot, "source") or 0),
            guarded_guid=int(_val(slot, "guarded_guid") or 0),
            damage_source_type=int(_val(slot, "damageSourceType") or 0),
        ))
    return out


def _read_turn_order(battle: Field) -> list[TurnEntry]:
    """Merge battle.initiative's two maps into the single interleaved queue.

    Heroes are keyed by `roster_guid`, monsters by `battle_guid` (which matches
    `enemies[].data.battle_guid`). The `initiative` field is a float32 stored
    in an int slot, hence `as_float()`."""
    ini = battle.get("initiative")
    if ini is None:
        return []
    out: list[TurnEntry] = []
    for side, guid_key in (("hero", "roster_guid"), ("monster", "battle_guid")):
        group = ini.get("heroes" if side == "hero" else "monsters")
        for slot in (group.children if group is not None else []):
            guid = _val(slot, guid_key)
            iv = slot.get("initiative")
            if guid is None or iv is None or iv.as_float() is None:
                continue
            out.append(TurnEntry(side=side, guid=int(guid), initiative=iv.as_float()))
    out.sort(key=lambda t: -t.initiative)
    return out


def _read_enemies(battle: Field) -> list[LiveEnemy]:
    enemies = battle.get("enemies")
    out: list[LiveEnemy] = []
    for slot, record in enumerate(enemies.children if enemies is not None else []):
        data = record.get("data")
        actor = data.get("actor") if data is not None else None
        if data is None or actor is None:
            continue
        enemy_id = str(_val(data, "monster_class") or "")
        out.append(LiveEnemy(
            enemy_id=enemy_id,
            name=str(_val(actor, "name") or ""),
            hp=_flt(actor, "current_hp"),
            stunned=bool(_val(actor, "stunned") or 0),
            slot=slot,
            battle_guid=_val(data, "battle_guid"),
            is_corpse=enemy_id.startswith(CORPSE_CLASS_PREFIX),
            turns_taken=int(_val(actor, "performing_turn") or 0),
            previous_class_hash=_val(data, "previous_monster_class") or None,
            buffs=_read_buffs(actor),
        ))
    return out


def _read_provisions(raid: Field) -> dict[str, int]:
    """Sum the party inventory's supply stacks by id.

    The same supply occupies several slots (bandage 6 + bandage 1), so this
    adds them up rather than taking the first hit."""
    party = raid.get("party")
    inv = party.get("inventory") if party is not None else None
    items = inv.get("items") if inv is not None else None
    out: dict[str, int] = {}
    for slot in (items.children if items is not None else []):
        item_id = str(_val(slot, "id") or "")
        amount = int(_val(slot, "amount") or 0)
        if item_id:
            out[item_id] = out.get(item_id, 0) + amount
        elif str(_val(slot, "type") or "") == "provision":
            # Food: shipped with an empty id, identified only by its type.
            out["food"] = out.get("food", 0) + amount
    return out


def _read_remaining_battles(map_file: Path) -> Optional[int]:
    """How many fights the dungeon still has waiting, from `persist.map.json`.

    A tile that holds an encounter carries a `mash_index` into the zone's mash
    table; an empty one carries **-1**. The entry is consumed when that fight is
    won, so counting them gives the fights left, and
    `battles_completed + remaining` gives the length of the expedition — the
    number the advisor was previously guessing at with a fixed default.

    Verified across four captured raids: the sum holds constant at 6, 7 and 6
    for three of them while battles were fought, and rises by exactly 1 in the
    long run, which is what a **camping ambush** looks like — a real fight that
    was never on the map. So this is a floor on the total, not a promise.

    `content` looks like the obvious field and is not: it is overloaded, it
    changes as tiles are merely explored, and in one capture a completed fight
    left the `content == 1` count untouched. Do not go back to it.

    Returns None rather than 0 when the file is absent, so a caller can tell
    "no map read" from "nothing left to fight"."""
    if not map_file.exists():
        return None
    try:
        areas = parse_file(map_file).get("map").get("static_dynamic").get("areas")
    except (AttributeError, OSError, ValueError):
        return None
    remaining = 0
    for area in (areas.children if areas is not None else []):
        tiles = area.get("tiles")
        for tile in (tiles.children if tiles is not None else []):
            index = _val(tile, "mash_index")
            if index is not None and index != -1:
                remaining += 1
    return remaining


def _read_battles_completed(raid: Field) -> int:
    db = raid.get("stat_database")
    entry = db.get("BATTLE_COMPLETED") if db is not None else None
    return int(_val(entry, "count") or 0)


def _read_hero(guid: int, rec: Field, thresholds: list[int]) -> LiveHero:
    actor = rec.get("actor")
    skills = rec.get("skills")
    xp = int(_val(rec, "resolveXp") or 0)
    return LiveHero(
        guid=guid,
        hero_class=str(_val(rec, "heroClass") or ""),
        stress=_flt(rec, "m_Stress"),
        hp=_flt(actor, "current_hp") if actor is not None else 0.0,
        stunned=bool(_val(actor, "stunned") or 0) if actor is not None else False,
        affliction_id=_val(rec, "affliction_type_id") or None,
        virtue_id=_val(rec, "virtue_type_id") or None,
        visited_deaths_door=bool(_val(rec, "visited_deaths_door") or 0),
        has_had_heart_attack=bool(_val(rec, "has_had_heart_attack") or 0),
        turns_taken=int(_val(actor, "performing_turn") or 0) if actor is not None else 0,
        mode_id=_val(actor, "current_mode_id") if actor is not None else None,
        resolve_xp=xp,
        resolve_level=resolve_level_for_xp(xp, thresholds) if thresholds else 0,
        weapon_rank=int(_val(rec, "weapon_rank") or 0),
        armour_rank=int(_val(rec, "armour_rank") or 0),
        selected_skills=_child_names(skills, "selected_combat_skills"),
        camping_skills=_child_names(skills, "selected_camping_skills"),
        trinkets=_item_ids(rec.get("trinkets"), "items"),
        trinket_slots=_item_slots(rec.get("trinkets"), "items"),
        quirks=[c.name for c in ((rec.get("quirks").children or []) if rec.get("quirks") else [])],
        buffs=_read_buffs(actor),
    )


def read_live_state(profile: str | Path | None = None,
                    raid_file: str | Path | None = None,
                    roster_file: str | Path | None = None,
                    thresholds: Optional[list[int]] = None,
                    map_file: str | Path | None = None) -> LiveState:
    """Current state of the real game. Pass `raid_file`/`roster_file` to read a
    captured snapshot instead of the live profile (which is how this is
    tested — the live files vanish when the raid ends).

    `map_file` defaults to the raid file's sibling `persist.map.json`, so a
    captured snapshot picks it up without the caller naming it.

    `thresholds` overrides the resolve XP table so a test can run without the
    game install; by default it is read from the game data."""
    if raid_file is None or roster_file is None:
        prof = find_profile(str(profile) if profile else None)
        staged = _stage([prof / "persist.raid.json", prof / "persist.roster.json",
                         prof / "persist.map.json"])
        raid_file = raid_file or staged / "persist.raid.json"
        roster_file = roster_file or staged / "persist.roster.json"
        map_file = map_file or staged / "persist.map.json"
    raid_file, roster_file = Path(raid_file), Path(roster_file)
    if map_file is None:
        map_file = raid_file.parent / raid_file.name.replace("raid", "map")
    map_file = Path(map_file)

    state = LiveState()
    if not raid_file.exists():
        return state                     # not in a raid: the file only exists then

    raid = parse_file(raid_file)
    state.in_raid = True
    state.torchlight = _flt(raid, "torchlight")
    state.area_tile = _val(raid, "areatile")
    state.battles_completed = _read_battles_completed(raid)
    state.provisions = _read_provisions(raid)
    state.remaining_battles = _read_remaining_battles(map_file)

    party = raid.get("party")
    guids = _val(party, "heroes") if party is not None else None
    if isinstance(guids, list) and guids:
        # Leading element is the count; the rest are guids in formation order.
        state.party_guids = [g for g in guids[1:1 + guids[0]] if g != -1]
    state.start_party_size = int(_val(party, "start_heroes_size") or 0) or len(state.party_guids)

    battle = _find(raid, "battle")
    if battle is not None:
        state.in_battle = True
        state.battle_round = _val(battle, "round")
        state.battle_turn = _val(battle, "currentturn")
        state.battle_seed = _val(battle, "seed")
        state.party_surprised = bool(_val(battle, "party_surprised") or 0)
        state.monsters_surprised = bool(_val(battle, "monsters_surprised") or 0)
        state.enemies = _read_enemies(battle)
        state.turn_order = _read_turn_order(battle)

    if roster_file.exists():
        if thresholds is None:
            try:
                thresholds = load_resolve_thresholds()
            except (OSError, KeyError, ValueError):
                thresholds = []
        roster = parse_file(roster_file)
        by_guid: dict[int, LiveHero] = {}
        for entry in (roster.get("heroes").children if roster.get("heroes") else []):
            rec = _find(entry, "raw_data")
            rec = rec.value if rec is not None else None
            if not isinstance(rec, Field):
                continue
            by_guid[int(entry.name)] = _read_hero(int(entry.name), rec, thresholds)
        # Formation order, not roster order.
        state.heroes = [by_guid[g] for g in state.party_guids if g in by_guid]
    _fill_turn_counters(state)
    return state


def _fill_turn_counters(state: LiveState) -> None:
    """Copy each combatant's turns-taken and stun onto its queue entry.

    Done here rather than in `_read_turn_order` because a hero's counter lives
    in the ROSTER while the queue lives in the raid file, so it is only
    knowable once both have been read. Without it `current_actor` silently
    falls back to the one-turn-late `currentturn` index."""
    heroes = {h.guid: h for h in state.heroes}
    monsters = {e.battle_guid: e for e in state.enemies}
    for entry in state.turn_order:
        src = heroes.get(entry.guid) if entry.side == "hero" else monsters.get(entry.guid)
        if src is None:
            continue
        entry.turns_taken = src.turns_taken
        entry.stunned = src.stunned


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default=None)
    ap.add_argument("--raid", default=None, help="a captured persist.raid.json")
    ap.add_argument("--roster", default=None, help="a persist.roster.json to pair with it")
    args = ap.parse_args()

    st = read_live_state(args.profile, args.raid, args.roster)
    if not st.in_raid:
        print("not in a raid (persist.raid.json absent)")
        return 0
    print(f"raid: torch {st.torchlight:.0f}  tile {st.area_tile}  "
          f"battle={st.in_battle} round={st.battle_round} turn={st.battle_turn} "
          f"seed={st.battle_seed}")
    print(f"party guids (formation order): {st.party_guids}")
    for rank, h in enumerate(st.heroes):
        print(f"  [{rank}] {h.hero_class:<14} hp={h.hp:<6.1f} stress={h.stress:<6.1f} "
              f"lvl={h.resolve_level} stunned={h.stunned} affl={h.affliction_id}")
        print(f"      skills={h.selected_skills} trinkets={h.trinkets}")
    for e in st.enemies:
        tag = "corpse" if e.is_corpse else f"hp={e.hp:<6.1f}"
        print(f"  [{e.slot}] {e.enemy_id:<20} {e.name:<18} {tag} stunned={e.stunned}")
    if st.turn_order:
        cur = st.current_actor
        print("turn order: " + ", ".join(
            f"{t.side[:1]}{t.guid}@{t.initiative:.3f}" for t in st.turn_order))
        print(f"current actor: {cur.side} {cur.guid}" if cur else "current actor: ?")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
