"""Seed a `CombatEngine` from a real, running game — `LiveState` -> engine.

This is the join between the two halves of the project: `bridge/live_state.py`
reads what Darkest Dungeon is actually doing, and this module reconstructs that
situation inside the simulator so a trained policy can be asked what it would
do about it.

Everything reuses the paths that already exist rather than inventing a second
way to build a combat:

* party HP / stress / affliction go in through **`carry_over=`**, the exact
  mechanism `ExpeditionSequenceEnv` uses to continue an expedition;
* equipped skills go in through **`hero_skill_ids=`**, positionally against the
  hero list — never a dict keyed by class, which would collapse the duplicate
  classes a real party can absolutely contain (two Lepers is legal);
* resolve level goes in through **`hero_levels=`**, derived from the save's
  `resolveXp` against the game's own `resolve_level_thresholds`.

Then a **sync pass**, because `reset()` builds a combat that is starting and a
real one is usually already underway: enemy HP/stun/corpse flags, the round
counter, and the initiative queue with its pointer.

`reset(auto_resolve_to_hero=False)` matters here. Left at its default, reset()
walks the queue and lets monsters act — the engine inventing turns the real
game has already played, on top of the state we are trying to copy.

Active buffs, debuffs, DoTs and guard come across too — see `_sync_buffs` and
the `SAVE_STAT_TYPE` table above for how the save's buff-stat enum was
recovered. An entry whose `stat_type` is not in that table is counted into
`engine.seed_unmapped_buffs` and dropped, never guessed at.

## What does NOT transfer, and why it is safe enough

**Monster AI intent and skill cooldowns.** The engine re-derives these; the
real fight's history is not in the save.

**Torch decay.** Torchlight is copied as the current value; this simulator
holds it fixed for the encounter either way.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bridge.live_state import LiveBuff, LiveState
from data.types import BuffDefinition, DataStore
from engine import CombatEngine, SeededRNG
from engine.combat_engine import CORPSE_ALIVE_ROUND_LIMIT
from engine.combatant import (ActiveBuff, CarryOverHeroState, CombatantState,
                              DotInstance, RiposteState)
from data.dson import dd_name_hash


class SeedError(RuntimeError):
    """The live state cannot be represented in the engine — an unknown hero
    class or monster id (modded content, or a monster this repo never
    loaded). Raised rather than silently dropping the combatant, which would
    hand the policy a board that does not match the screen."""


_ENEMY_ID_BY_HASH: dict[int, str] = {}

# --- The save's buff-stat enum -------------------------------------------
#
# `buff_group[].stat_type` is an int index into an enum the game ships in the
# binary and in no data file, so it was recovered from the saves themselves.
# Every entry below is evidence, not inference:
#
# The five stat types were pinned by taking the save entries that carry a real
# buff `id` and reading that id's `stat_type` STRING out of
# `shared/buffs/base.buffs.json` — a direct, unambiguous cross-reference:
#
#   2  <- on_crit_spd            (combat_stat_add, speed_rating +2)
#   3  <- townFlagellationDMG*   (combat_stat_multiply, damage_low/high)
#   7  <- on_crit_healbuff       (hp_heal_percent +25%)
#   11 <- STUNRECOVERYBUFF       (resistance, stun +50%)
#   30 <- STRESSDMG-10, BRAWLER_STRESSDMG_D1  (stress_dmg_received_percent)
#
# Do NOT try to extend this by assuming the enum is the alphabetical order of
# the stat types that appear in base.buffs.json. It is not: that ordering
# happens to reproduce 2 and 3 and then misses 7, 11 and 30, because the real
# enum contains values no shipped buff uses.
SAVE_STAT_TYPE = {
    2: "combat_stat_add",
    3: "combat_stat_multiply",
    7: "hp_heal_percent",
    11: "resistance",
    30: "stress_dmg_received_percent",
}

# Damage-over-time, and finding it in `buff_group` at all is the whole reason
# DoTs transfer — the obvious `actor.actor_dot` field is empty in all 445
# captured snapshots. Measured rather than assumed: a Highwayman carrying one
# `stat_type 5, amount 2, duration 3` entry went 30 -> 28 -> 26 HP as the
# duration counted 3 -> 2 -> 1, and a Man-at-Arms carrying two `amount 1`
# entries lost exactly 2 HP a round. `amount` is the per-tick damage with
# stacks already summed, `duration` the ticks left.
#
# **The kind is the stat_type, NOT `damageSourceType`.** Shipping it the other
# way round was a real error, killed by a run flown specifically to test it —
# a party whose only DoT source was the Highwayman's `opened_vein`:
#
#   * that bleed landed on an ENEMY as `stat_type 5, damageSourceType 6`;
#   * a Rabid Dog's bleed landed on the CRUSADER as `stat_type 5,
#     damageSourceType 5`, in a fight where the only other monster
#     (fungal_bloat) applies no DoT at all.
#
# Two confirmed bleeds, same stat_type, different damageSourceType. So
# `damageSourceType` tracks WHO applied it (5 monster-sourced, 6 hero-sourced)
# and says nothing about the kind. It matters because `bandage` is only offered
# against bleed and `antivenom` only against blight.
SAVE_STAT_BLEED = 5
# By elimination, and weaker: `stat_type 4` appears only in encounters holding
# a `dotPoison` monster, never in a bleed-only one. One sample resists both
# readings (a Highwayman carrying a `stat_type 5` in a spider-only fight) and
# is left on the record rather than explained away.
SAVE_STAT_BLIGHT = 4
SAVE_DOT_STAT_TYPES = (SAVE_STAT_BLEED, SAVE_STAT_BLIGHT)

# BURN IS MISSING HERE, and deliberately so. The engine models it as of
# 2026-08-20 (`CombatantState.burn_stacks`), but its save `stat_type` is
# unknown: no capture in the corpus contains one, because no captured run
# fielded a Runaway. Both numbers above were pinned by flying a run chosen to
# isolate them -- bleed by a party whose only DoT source was `opened_vein` --
# and inventing a third by pattern-matching would be exactly the error the
# `damageSourceType` note above records having already made once.
#
# To close it: play a raid with the Runaway, land a Firefly or Searing Strike,
# capture the save mid-fight, and look for a stat_type on the burning target
# that is neither 4 nor 5. Until then a live board that is burning seeds with
# `burn_stacks = 0`, so the advisor under-values Searing Strike's "+DMG vs
# Burning" and over-values finishing a target the fire would have killed.

# Guard is a single marker entry (27, amount 0) carrying the other party's
# guid, and it sits on the GUARDED combatant naming its guardian — not the
# other way round, which is the reading that looks natural and is wrong. The
# game's own data settles it: Man-at-Arms `defender` fires `MAA Guard 1`
# (`.target "target"`, `.guard 1`) at the ally and `Defender N`
# (`.target "performer"`, `.protection_rating_add 0.15`) at itself. In the
# capture where a Man-at-Arms guards a Vestal, the +15% PROT is on the
# Man-at-Arms and the marker is on the Vestal.
SAVE_STAT_GUARD_MARKER = 27

# Riposte is a GROUP of six entries sharing one `source`, and 26 is its flag.
# Reading 26 as guard's other half was the obvious guess and it does not
# survive counting: 26 occurs 156 times against guard's 17, in perfect lockstep
# with `riposte_on_hit_chance` and `riposte_on_miss_chance` (156 each). The
# group carries exactly the five fields `RiposteState` holds, so an active
# riposte transfers whole. Grouping is by equal `source`, which needs no
# knowledge of what the source enum's values mean.
#
# These must NOT go through `_apply_buff_to_stats`: the group's -33% damage and
# +2% crit belong to the riposte's counter-attack, not to the hero's own
# attacks. Applying them straight would have quietly handed every riposting
# Highwayman a third off their damage — which is most of the party in the comp
# this was tested against.
SAVE_STAT_RIPOSTE_MARKER = 26
RIPOSTE_SUB_TYPES = ("riposte_on_hit_chance", "riposte_on_miss_chance")

# `duration -1` means permanent, and permanent is the giveaway for a buff that
# was NOT granted in this fight: trinket, quirk and town/district buffs all
# read that way (224 inline entries plus 240 townFlagellation* ones in the
# captures). Those already came in through `_sync_equipment`, so applying them
# again here would double every one of them.
PERMANENT_DURATION = 0


def _resolve_enemy_id(live, store: DataStore) -> Optional[str]:
    """The DataStore id for one slot of the real enemy line, corpses included.

    A corpse is stored with `monster_class: "corpse_A"`, which is not a monster
    this repo loads — and that made seeding raise the moment anything died,
    so the advisor went silent for the rest of every fight. It is a hard
    failure that only appears once a kill lands, which is why the first
    captures (all of them mid-fight, nothing dead yet) never showed it.

    The record keeps the original monster in `previous_monster_class`, stored
    as the format's NAME HASH. Inverting it against the loaded monster ids
    recovers the real definition — which matters beyond cosmetics, because a
    large monster's corpse occupies two ranks and the engine takes that from
    the definition's `size`."""
    if not live.is_corpse:
        return live.enemy_id if live.enemy_id in store.enemies else None
    if not _ENEMY_ID_BY_HASH:
        _ENEMY_ID_BY_HASH.update({dd_name_hash(k): k for k in store.enemies})
    # DSON decodes a 4-byte field as SIGNED; `dd_name_hash` is unsigned mod
    # 2**32. Any monster whose hash has the top bit set therefore arrived here
    # as a negative number and matched nothing — 72 of 160 captured corpses,
    # 45%, silently falling through to the wrong monster entirely
    # (cultist_witch_A, fungal_bloat_A and cultist_brawler_A were the three).
    key = live.previous_class_hash
    resolved = _ENEMY_ID_BY_HASH.get(key if key is None or key >= 0 else key + 2**32)
    if resolved:
        return resolved
    # Unresolvable corpse: better a rank-blocking corpse of unknown origin than
    # refusing the whole board. Smallest available monster, because `size` is
    # the only thing a corpse contributes and over-stating it would block ranks
    # the real board leaves open — the old fallback took whatever id the store
    # happened to yield first, which is insertion-order luck and could be a
    # size-4 boss.
    return min(store.enemies, key=lambda k: store.enemies[k].size, default=None)


def _mortality_tier(hero) -> str:
    """Map the save's Death's-Door history onto the engine's sticky tier.

    The engine's tiers are "none" -> "mortality" -> "heart_attack" and never
    downgrade, which is the same shape as the save's two independent booleans
    with heart attack dominating."""
    if hero.has_had_heart_attack:
        return "heart_attack"
    if hero.visited_deaths_door:
        return "mortality"
    return "none"


def _carry_over_for(hero) -> CarryOverHeroState:
    return CarryOverHeroState(
        hp=int(round(hero.hp)),
        stress=int(round(hero.stress)),
        # The engine treats Death's Door as "on it right now", which is an
        # HP fact, not the save's "has ever been there" history.
        is_deaths_door=hero.hp <= 0,
        mortality_tier=_mortality_tier(hero),
        affliction_id=hero.affliction_id,
        virtue_id=hero.virtue_id,
    )


def build_engine_from_live(state: LiveState, store: DataStore,
                           rng: Optional[SeededRNG] = None,
                           actor=None,
                           skill_uses: Optional[dict] = None) -> CombatEngine:
    """A CombatEngine holding the position the real game is in right now.

    `actor` overrides whose turn the engine is set to — used by the advisor to
    read one or more enemy turns ahead to the next hero, since the save goes
    silent while enemies act. Defaults to `state.current_actor`.

    `skill_uses` is `{hero roster guid: {skill_id: times used THIS battle}}`,
    which **is not in the save** and has to be remembered by the caller. See
    `_sync_skill_uses`.

    Raises SeedError if the party or the enemy line contains anything this
    repo cannot load (modded heroes have no `.info.darkest` anywhere in the
    install, so they genuinely cannot be simulated)."""
    if not state.in_battle:
        raise SeedError("not in a battle — nothing to seed")
    if not state.heroes:
        raise SeedError("no party heroes resolved (is the roster paired with this raid?)")

    missing = [h.hero_class for h in state.heroes if h.hero_class not in store.heroes]
    if missing:
        raise SeedError(f"unknown hero class(es): {missing} (modded heroes cannot be simulated)")
    enemy_ids = [_resolve_enemy_id(e, store) for e in state.enemies]
    unknown = [live.enemy_id for live, rid in zip(state.enemies, enemy_ids) if rid is None]
    if unknown:
        raise SeedError(f"unknown monster id(s): {unknown}")

    hero_defs = [store.heroes[h.hero_class] for h in state.heroes]
    # Formation order IS the order LiveState.heroes comes in — party_guids is
    # front-to-back, and CombatEngine.reset assigns position = index.
    hero_positions = list(range(len(hero_defs)))
    hero_levels = [h.resolve_level for h in state.heroes]
    hero_skill_ids = [list(h.selected_skills) for h in state.heroes]
    carry_over = [_carry_over_for(h) for h in state.heroes]
    enemy_defs = [store.enemies[eid] for eid in enemy_ids]

    engine = CombatEngine(store, rng or SeededRNG(state.battle_seed))
    engine.reset(
        hero_defs, enemy_defs,
        hero_positions=hero_positions,
        torch_level=state.torchlight,
        hero_levels=hero_levels,
        hero_skill_ids=hero_skill_ids,
        carry_over=carry_over,
        auto_resolve_to_hero=False,
        # The save already KNOWS, so never re-roll it. A rolled surprise
        # would scramble the party's ranks over the very formation being
        # copied -- the same class of bug as auto_resolve_to_hero letting the
        # engine invent enemy turns the real game already played.
        surprise=(state.party_surprised, state.monsters_surprised),
    )
    _sync_enemies(engine, state)
    _sync_heroes(engine, state)
    _sync_skill_uses(engine, state, skill_uses)
    _sync_gear(engine, state)
    _sync_equipment(engine, state)
    engine.seed_unmapped_buffs = _sync_buffs(engine, state, store)
    # HP last: trinkets can raise max HP (health_stone), and a buff can too
    # (combat_stat_add/max_hp), so the ceiling has to be final before the real
    # value is written against it.
    _sync_hp(engine, state)
    _sync_turn_pointer(engine, state, actor)
    return engine


def build_party_from_live(state: LiveState, store: DataStore,
                          rng: Optional[SeededRNG] = None) -> CombatEngine:
    """The party as engine combatants, with no fight in progress.

    Camping happens BETWEEN fights, which is exactly the state
    `build_engine_from_live` refuses. This builds the hero half only, with the
    same gear/trinket/quirk/HP handling — including **HP written last**, since
    armour rank and `health_stone` both raise the ceiling and a value written
    before them reads a full hero as damaged.

    `CombatEngine.reset` needs an enemy line to build anything, so one throwaway
    monster is fielded and never read. Battle buffs and the turn pointer are
    deliberately not synced: out of combat there is no battle state to copy, and
    the save's `buff_group` entries at that moment are the persistent ones the
    equipment pass already applies.
    """
    if not state.heroes:
        raise SeedError("no party heroes resolved (is the roster paired with this raid?)")
    missing = [h.hero_class for h in state.heroes if h.hero_class not in store.heroes]
    if missing:
        raise SeedError(f"unknown hero class(es): {missing} (modded heroes cannot be simulated)")
    if not store.enemies:
        raise SeedError("no monsters loaded")

    hero_defs = [store.heroes[h.hero_class] for h in state.heroes]
    placeholder = store.enemies[sorted(store.enemies)[0]]

    engine = CombatEngine(store, rng or SeededRNG(state.battle_seed or 0))
    engine.reset(
        hero_defs, [placeholder],
        hero_positions=list(range(len(hero_defs))),
        torch_level=state.torchlight,
        hero_levels=[h.resolve_level for h in state.heroes],
        hero_skill_ids=[list(h.selected_skills) for h in state.heroes],
        carry_over=[_carry_over_for(h) for h in state.heroes],
        auto_resolve_to_hero=False,
        surprise=(state.party_surprised, state.monsters_surprised),
    )
    _sync_gear(engine, state)
    _sync_equipment(engine, state)
    _sync_hp(engine, state)
    return engine


def _live_buff_definition(buff: LiveBuff, store: DataStore) -> Optional[BuffDefinition]:
    """The engine-side definition for one temporary save buff, or None if its
    `stat_type` is not one this repo has evidence for.

    A named entry resolves straight to the shipped definition. An inline one
    (`id: ""`, which is how skill buffs and debuffs are stored) is synthesised
    and REGISTERED in `store.buffs` under a deterministic id, because
    `_tick_buff_durations` reverses an expiring buff by looking its id back up
    there — an unregistered inline buff would apply and then never come off."""
    if buff.buff_id:
        return store.buffs.get(buff.buff_id)
    stat_type = SAVE_STAT_TYPE.get(buff.stat_type)
    if stat_type is None:
        return None
    # `__live__`, not `LIVE_`. The store is SHARED and cached across a whole
    # test session, and `tests/data/test_store_cache.py` is the tripwire for real
    # cross-test pollution: anything appearing in a used store that is not
    # `__`-prefixed is treated as a leak and fails. This site is a legitimate
    # get-or-create memoisation exactly like `combat_engine`'s `__inline__`
    # and `__buff_type__` ones, but it did not follow the convention, so it
    # read as pollution the moment a seeding test and the cache test landed in
    # the same xdist worker. The key only has to be deterministic and unique;
    # it is created and looked up in this one expression and nowhere else.
    key = f"__live__{buff.stat_type}_{buff.stat_sub_type}_{buff.amount:g}"
    existing = store.buffs.get(key)
    if existing is not None:
        return existing
    bd = BuffDefinition(
        id=key,
        stat_type=stat_type,
        stat_sub_type=buff.stat_sub_type,
        amount=buff.amount,
        duration=buff.duration,
        duration_type=buff.duration_type,
        rule_type="always",
        is_false_rule=False,
        remove_on_battle_complete=True,
        remove_if_not_active=False,
    )
    store.buffs[key] = bd
    return bd


def _apply_riposte(sim: CombatantState, live_buffs: list[LiveBuff],
                   riposte_sources: set[int]) -> None:
    """Rebuild an active riposte from its buff group.

    The group's five stat entries line up one-for-one with `RiposteState`'s
    fields, which is what makes this exact rather than approximate. Only the
    riposte's own counter-attack effect is missing from the save, and the
    engine already defaults that."""
    for source in riposte_sources:
        group = [b for b in live_buffs if b.source == source]
        by_sub = {b.stat_sub_type: b.amount for b in group}
        duration = max((int(b.duration or 0) for b in group), default=0)
        if not duration:
            continue
        sim.active_riposte = RiposteState(
            remaining_rounds=duration,
            dmg_mod=by_sub.get("damage_low", 0.0),
            crit_mod=by_sub.get("crit_chance", 0.0),
            on_hit_chance=by_sub.get("riposte_on_hit_chance", 0.0),
            on_miss_chance=by_sub.get("riposte_on_miss_chance", 0.0),
        )


def _sync_buffs(engine: CombatEngine, state: LiveState, store: DataStore) -> int:
    """Transfer every temporary modifier the real fight is carrying: buffs,
    debuffs, DoTs, guard and riposte. Returns how many entries went
    unrecognised.

    Without this the policy was advised on a board that looked healthier and
    calmer than the real one — no bleed ticking down, no speed buff, no idea
    that the Man-at-Arms was already covering the Vestal or that two
    Highwaymen were standing in riposte. All four mechanics are fully modelled
    by the engine, so the whole gap was in the translation, not the simulator.

    Permanent entries are skipped (see PERMANENT_DURATION), and an unknown
    `stat_type` is counted and dropped rather than guessed at — a wrong stat is
    worse than a missing one."""
    unmapped = 0
    by_hero_guid = {h.guid: sim for h, sim in zip(state.heroes, engine.heroes)}
    by_monster_guid = {e.battle_guid: sim for e, sim in zip(state.enemies, engine.enemies)}
    protectable = {**by_hero_guid, **by_monster_guid}

    pairs: list[tuple[list[LiveBuff], CombatantState]] = [
        (live.buffs, sim) for live, sim in zip(state.heroes, engine.heroes)]
    pairs += [(live.buffs, sim) for live, sim in zip(state.enemies, engine.enemies)]

    for buffs, sim in pairs:
        live_buffs = [b for b in buffs if int(b.duration or 0) > PERMANENT_DURATION]
        riposte_sources = {b.source for b in live_buffs
                           if b.stat_sub_type in RIPOSTE_SUB_TYPES}
        _apply_riposte(sim, live_buffs, riposte_sources)

        for buff in live_buffs:
            duration = int(buff.duration or 0)
            if buff.source in riposte_sources:
                continue          # consumed by _apply_riposte
            if buff.stat_type == SAVE_STAT_GUARD_MARKER:
                # This entry is on the GUARDED combatant and names the guardian.
                guardian = protectable.get(buff.guarded_guid)
                if guardian is not None and guardian is not sim:
                    guardian.guard_target_id = sim.id
                    guardian.guard_rounds_remaining = duration
                    sim.guard_source_id = guardian.id
                continue
            if buff.stat_type in SAVE_DOT_STAT_TYPES:
                dot = DotInstance(
                    effect_name="live_save",
                    damage_per_tick=max(0, int(round(buff.amount))),
                    remaining_ticks=duration,
                )
                if dot.damage_per_tick:
                    target = (sim.blights if buff.stat_type == SAVE_STAT_BLIGHT
                              else sim.bleeds)
                    target.append(dot)
                continue
            bd = _live_buff_definition(buff, store)
            if bd is None:
                unmapped += 1
                continue
            delta = engine._apply_buff_to_stats(sim, bd)
            sim.buffs.append(ActiveBuff(buff_id=bd.id, remaining_rounds=duration,
                                        applied_delta=delta))
    return unmapped


def _sync_skill_uses(engine: CombatEngine, state: LiveState,
                     skill_uses: Optional[dict]) -> None:
    """Restore per-battle skill limits, which the SAVE DOES NOT RECORD.

    13 hero skills carry `.per_battle_limit` (the Highwayman's Take Aim —
    "Tracking Shot" on screen — Hellion's Barbaric YAWP, Abomination's
    Transform, and ten more), and the engine has enforced them since it was
    written. A freshly seeded engine starts every one of them at zero, so once
    the party has spent one the advisor keeps recommending it, DD keeps the
    button greyed out, and the click lands on nothing. In the log that is
    indistinguishable from a dropped input — three attempts, 45 seconds, no
    save change — which is exactly how it was found.

    The save genuinely does not carry it: `skill_log` and `party_skill_log` are
    empty in all 585 captured snapshots **and** in a live save taken while a
    Highwayman had already spent Take Aim that battle, and no hero record holds
    a use count. DD tracks it in memory only. So the caller has to remember,
    and what it can remember with certainty is what the bridge itself played.
    """
    if not skill_uses:
        return
    for live, sim in zip(state.heroes, engine.heroes):
        used = skill_uses.get(live.guid)
        if used:
            sim.skill_uses.update(used)


def _sync_gear(engine: CombatEngine, state: LiveState) -> None:
    """Correct each hero's stats to their REAL weapon and armour ranks.

    The stat math lives in `CombatEngine.apply_gear_ranks` — see its docstring
    for why reset()'s single gear tier is wrong for a real roster and what it
    cost. Shared with `bridge/rollout.py`, which needs the identical correction
    to simulate a real party forward rather than a sampled one."""
    for live, sim in zip(state.heroes, engine.heroes):
        engine.apply_gear_ranks(sim, live.resolve_level,
                                live.weapon_rank, live.armour_rank)


def _sync_hp(engine: CombatEngine, state: LiveState) -> None:
    """Write each hero's real HP once max HP is final.

    `_apply_carry_over` already clamped it against reset()'s max HP, which is
    both the wrong gear tier and missing any trinket that raises the ceiling —
    `health_stone` is the one that bites, and it is why a hero could read
    38.75 HP the sim had nowhere to put."""
    for live, sim in zip(state.heroes, engine.heroes):
        sim.hp = max(0, min(int(round(live.hp)), sim.max_hp))


def _sync_equipment(engine: CombatEngine, state: LiveState) -> None:
    """Apply the party's real trinkets and quirks.

    Not optional decoration: quirks carry genuine stat buffs, and a real roster
    accumulates several per hero (`improved_balance`, `dud_hitter`, …). Unknown
    ids are skipped by `apply_trinkets_and_quirks` itself, which is the right
    behaviour for a save that may hold modded or DLC entries this repo does not
    load."""
    for live, sim in zip(state.heroes, engine.heroes):
        if live.trinkets or live.quirks:
            engine.apply_trinkets_and_quirks(sim, list(live.trinkets), list(live.quirks))


def _sync_enemies(engine: CombatEngine, state: LiveState) -> None:
    """Copy the real enemy line onto the freshly built one.

    Position is the list index in both, so they line up 1:1 — the save's
    `battle.enemies` order IS rank order and already reflects the real game
    closing ranks, which is the same invariant `_collapse_enemy_position_gaps`
    maintains here."""
    for live, sim in zip(state.enemies, engine.enemies):
        sim.hp = int(round(live.hp))
        sim.is_stunned = live.stunned
        if live.is_corpse:
            sim.is_corpse = True
            # Unknown from the save: how many of its rounds the corpse has
            # already burned. Assume a full life so the policy is not told a
            # blocking corpse is about to clear when it might not be.
            sim.corpse_rounds_remaining = CORPSE_ALIVE_ROUND_LIMIT
        elif sim.hp <= 0:
            sim.is_dead = True


def _sync_heroes(engine: CombatEngine, state: LiveState) -> None:
    """HP and stress already arrived via carry_over; stun did not (it is a
    per-combat flag the carry_over path deliberately drops)."""
    for live, sim in zip(state.heroes, engine.heroes):
        sim.is_stunned = live.stunned


def _sync_turn_pointer(engine: CombatEngine, state: LiveState, actor=None) -> None:
    """Replace the engine's rolled initiative queue with the real one.

    The save stores the actual order (see `LiveState.turn_order`), so there is
    no reason to let the engine roll SPD+d8 and land somewhere else. Heroes are
    matched by their index in the party, monsters by their index in the enemy
    line — `battle_guid` is a per-battle id with no meaning here, but the two
    lists are in the same order on both sides.

    Falls back to the engine's own rolled order if the save's queue cannot be
    matched, rather than leaving a queue with holes in it."""
    engine.round = max(1, int(state.battle_round or 1))

    hero_by_guid = {h.guid: i for i, h in enumerate(state.heroes)}
    monster_by_guid = {e.battle_guid: i for i, e in enumerate(state.enemies)}

    queue = []
    for entry in state.turn_order:
        if entry.side == "hero":
            idx = hero_by_guid.get(entry.guid)
            if idx is not None and idx < len(engine.heroes):
                queue.append(engine.heroes[idx])
        else:
            idx = monster_by_guid.get(entry.guid)
            if idx is not None and idx < len(engine.enemies):
                queue.append(engine.enemies[idx])

    if len(queue) == len(state.turn_order) and queue:
        engine.turn_order = queue
        # Point at whoever is up NEXT, which is derived from the turns-taken
        # counters — NOT at the save's `currentturn`, which is written after
        # its turn resolves and therefore names the combatant who just went.
        # Using it put the engine (and every recommendation) one turn behind.
        actor = actor or state.current_actor
        turn = state.turn_order.index(actor) if actor in state.turn_order else int(
            state.battle_turn or 0)
        engine.current_turn_index = max(0, min(turn, len(queue) - 1))

    # The real game has already run this turn's start-of-turn effects; re-running
    # them here would double-tick a DoT and re-roll an act-out that already fired.
    engine._dot_ticked_for_index = engine.current_turn_index
    engine._act_out_rolled_for_index = engine.current_turn_index


def current_hero_id(engine: CombatEngine) -> Optional[int]:
    """The engine's view of whose turn it is, after seeding. None means the
    save says a monster is acting (or the pointer landed on a corpse), and the
    advisor should wait rather than recommend."""
    return engine.get_current_actor_id()
