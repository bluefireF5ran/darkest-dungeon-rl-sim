"""Evolve party compositions against the simulator instead of guessing them.

A comp is four heroes, their formation order, four skills each, four camping
skills, two trinkets and a quirk sheet. `metadata/COMPS/curated_comps/` holds
136 of them that Fran has played and vouches for. This searches for more.

## What makes this different from pointing a GA at a number

`bridge/rollout.py::Rollout.evaluate` already answers *does THIS party survive
THAT dungeon?* by playing it forward N times, and it reseeds the scenario per
episode so two parties are ranked over the **same** sequence of fights. That is
the whole fitness function; nothing here invents one.

The adversary is noise, not search. `RolloutResult.standard_error` on 20
episodes is ~11 points near a rate of 0.5, and `CLAUDE.md` records that +-2pp
would need ~2300 paired scenarios. A GA pointed at a noisy scalar evolves
noise. Four things here exist only to keep that from happening:

* **Common random numbers.** Every candidate in a generation is scored on the
  identical scenario list, because `Rollout.evaluate`'s scenario key is
  `zone|tier|combats|band|seed` and deliberately does not mention the party.
  Absolute rates stay noisy; the *ranking* between candidates is far less so.
* **The seed rotates per generation** (`--fixed-seed` turns that off). A
  candidate that only wins on one dungeon list is found out the next
  generation, and elites are re-scored rather than carrying a lucky number
  forward.
* **The hall of fame is re-measured on held-out seeds** at the end, at
  `--validate-episodes`, and that -- not the generation score -- is what gets
  written into a comp's `gaProvenance`.
* **The shaping term cannot outrank the headline.** Fitness is
  `success_rate + W * shaping`, `shaping` in [0, 1], with `W` forced below
  `1 / episodes` -- the smallest gap two success rates can have. So the
  tie-break only ever orders candidates the headline could not tell apart.

Read the standard error printed next to every rate. Two comps four points apart
are not ranked.

## What it does NOT do

It does not write to the training pool. Output lands in
`metadata/COMPS/GA_comps/`, which `CompLoadoutLoader` deliberately does not
read; promoting a comp means moving its file into `curated_comps/`, and for a
comp of ordinary classes it also means a CSV row -- which shifts every archived
`--comp-seed` selection and is a decision, not a side effect. Comps fielding a
Fire's Edge class (Duelist, Runaway) are the exception and reach the training
pool from the loadout directory alone (`FIRES_EDGE_HEROES`).

## Pick a scenario that DISCRIMINATES, and check that it still does

A fitness everything scores 100% on ranks nothing. Measured on the 2026-08-21
retrain, 8 candidates, 6 episodes, crypts:

    tier 3 medium   best 100.0%   ceiling, no gradient
    tier 3 long     best 100.0%   ceiling, no gradient
    tier 5 medium   best  66.7%   <- the default
    tier 5 long     best   0.0%   floor; only the shaping term separates

Hence the defaults below. Re-run that probe after any retrain: a stronger
policy pushes the ceiling out and the useful scenario moves with it.

## Every zone, at the top of the game

The scenario is all four zones at Champion with everything maxed -- resolve 6,
so `max_gear_rank_for_resolve` buys weapon and armour rank 5 and `reset()`
derives the top skill tier from it. Fran's call, and the reason is that a comp
scored in one zone is a comp *for* one zone: the enemy pool differs per zone
and per tier, so the Ruins selects for whatever beats skeletons and says
nothing about the Cove's pelagics. Every candidate therefore plays `--episodes`
expeditions in each of crypts, warrens, weald and cove, and the headline is the
episode-weighted mean over all four. It costs exactly 4x; `--zones crypts`
buys the old behaviour back.

Per-zone rates survive into `measured.byZone` and into the printed table, which
is the part worth reading: a comp at 70% everywhere is a different animal from
one at 100/100/40/40, and the mean hides that.

    python tools/comp_ga.py --self-check --model <ckpt>
    python tools/comp_ga.py --model <ckpt> --require-class duelist --tag duelist
    python tools/comp_ga.py --model <ckpt> --zones crypts --workers 8   # one zone, 4x cheaper
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
import random
import statistics
import re
import zlib
import sys
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data.comp_loadout_loader import HERO_DISPLAY_NAME_TO_ID, CompLoadoutLoader
from engine.combat_engine import max_gear_rank_for_resolve
from env.expedition_env import (
    CSV_ZONE_TO_ZONE_ID,
    RANDOM_QUIRKS_PER_POLARITY,
    RANDOM_TRINKETS_PER_HERO,
    SKILLS_PER_HERO,
    TIER_TO_HERO_LEVEL,
    UNIVERSAL_TRINKET_RARITIES,
    UNRESTRICTED_HEROES,
    unrestricted_heroes,
    build_comp_pool,
)
from env.expedition_sequence_env import EXPEDITION_LENGTH_BANDS

from data.paths import CSV_DIR as _CSV_DIR, GAME_DIR as _GAME_DIR

GAME_DIR = str(_GAME_DIR)
CSV_DIR = str(_CSV_DIR)

CURATED_DIR = Path(CSV_DIR) / "metadata" / "COMPS" / "curated_comps"
GA_DIR = Path(CSV_DIR) / "metadata" / "COMPS" / "GA_comps"

CAMP_SKILLS_PER_HERO = 4
TRINKETS_PER_HERO = 2

# The SEARCH fills all five positive quirk slots. `RANDOM_QUIRKS_PER_POLARITY`
# is (1, 3) and is shared with `--kit-random-p` in training, so it is left
# alone -- see CompSpace.__init__ for the measurement and the reasoning.
# `--sparse-quirks` restores the old draw exactly.
SEARCH_QUIRKS_PER_POLARITY = (5, 5)
PARTY_SIZE = 4

# How many `limit`-bearing trinkets one comp may field, in TOTAL, across all
# eight slots. Fran's rule, and stricter than the game's own.
#
# `limit` is the game's marker for a trinket you can only ever own one of, and
# it lands exactly on the four families he named -- Ancestral (9), Shambler
# ancestral (5), Crimson Court (38), Color of Madness comets (28) -- plus boss
# trophies (12) and the Collector's, Madman's, Mildred's, Crow's and eleven
# `very_rare` ones his list would have missed. 56 of a class's 154 wearable
# trinkets carry it.
#
# **The rule is PER ID, which is the game's own** -- corrected 2026-08-26.
#
# This was read as "one `limit`-bearing trinket per comp" from Fran's first
# review and recorded as "stricter than the game's per-id rule, and his call".
# It was a misreading of what he said. His words on being shown a comp holding
# three different Ancestral trinkets: *"the problem is not 1 ancestral trinket
# per comp, rather several copies of the same ancestral trinket"*. You own one
# Ancestor's Candle, so one hero can wear it -- and nothing stops another hero
# wearing Ancestor's Map at the same time. His own upgraded comp fields three
# different Ancestrals.
#
# So the constraint is that a trinket id appears at most `limit` times across
# the party. `limit` is the game's own field: 420 trinkets carry 1, two carry
# 2 and 3, and 193 carry 0, which means unlimited.
#
# The cap of one was over-restrictive for four months of searching and it cost
# the space its strongest gear. Kept as a name because `grant_one_off` still
# uses it to guarantee the search STARTS with at least one.
UNIQUE_TRINKETS_PER_COMP = 1

# How often an ordinary trinket draw offers a one-off instead of an unlimited
# trinket. A hero has ~56 one-offs against ~98 unlimited, so a uniform draw
# would make more than a third of every comp boss gear -- which is the failure
# the old cap of one was reaching for, and it was right about the risk if not
# about the rule. This is a prior, not a restriction: the per-id budget is the
# only hard limit, and a search that wants four different Ancestrals can still
# find them.
ONE_OFF_DRAW_RATE = 0.20

# How often a trinket draw prefers the hero's own class-locked trinkets.
#
# A class has ~9 of those out of 154 wearable, so a uniform draw finds a
# designed synergy 6% of the time and a ten-generation search essentially never
# explores them. They are the ones built around the class's kit, which is the
# thing a comp is FOR. This is a prior on where to look, not a restriction:
# every generic trinket stays reachable, and a class-specific one that does not
# earn its slot is dropped by selection like anything else.
CLASS_TRINKET_BIAS = 0.5

# How often a trinket draw prefers one whose CHANCE modifier matches something
# the hero's own skills actually apply.
#
# Fran, after the effect-resist fix: "if you want to stun more, +stun chance
# trinkets will help; if the model wants Illumination, with +debuff chance
# trinkets maybe it's not that worth". That synergy only became real on
# 2026-08-22 -- `stun_chance` and `move_chance` were inert until the day
# before, `debuff_chance` had no roll to modify at all, and with two rolls a
# +0.35 barely moved a payload that was already landing.
#
# It is a prior on where to look, for the same reason `CLASS_TRINKET_BIAS` is:
# only 75 of ~154 wearable trinket slots carry a chance stat, most heroes match
# a handful, and a uniform draw reaches them rarely enough that selection never
# gets a paired comparison. Measured, one such swap is worth about one sigma at
# 160 episodes (+3.8pp, `CHANGELOG.md`) -- real, and under what the fitness can
# resolve in a single generation, which is exactly the case for putting it in
# front of the search more often rather than waiting for it to be found.
#
# ONLY positive modifiers, and only where the kit delivers that payload. A
# trinket with a matching PENALTY is left in the general pool: selection can
# still take it for its other stats, which is what the search did with
# `haste_chalice` (-0.25 stun) on a Vestal who stuns.
SYNERGY_TRINKET_BIAS = 0.25

# The chance stat a trinket buff carries -> the payload it modifies.
CHANCE_STAT_PAYLOAD = {
    "stun_chance": "stun",
    "bleed_chance": "bleed",
    "poison_chance": "blight",
    "move_chance": "move",
    "debuff_chance": "debuff",
}

# zone_id -> the string a comp JSON's `location` field uses. The two files
# speak different dialects and always have: a loadout JSON says "The Cove",
# `COMPS/Comps <hash>.csv` says "Cove" (see `_fires_edge_comps`).
ZONE_ID_TO_JSON_LOCATION = {zid: f"The {csv_name}"
                            for csv_name, zid in CSV_ZONE_TO_ZONE_ID.items()}

# The tie-break, and the only convention in the fitness function.
#
# `success_rate` is the headline and matches eval_expedition.py's SUCCESS
# exactly, but it is coarse: on 12 episodes it can only take 13 values, and
# early generations are full of candidates that all score 0. The shaping term
# gives those a gradient -- it is a weighted mean of four rates, so it lands in
# [0, 1] -- and `_tiebreak_weight` then forces its whole range below the
# smallest gap two success rates can have. A candidate can therefore never win
# on shaping against a candidate that succeeded more often; the term only
# orders ties. The four weights themselves are a choice and are marked as one.
SHAPING_WEIGHTS = {"completion": 0.35, "zero_death": 0.25,
                   "combats_frac": 0.25, "stress": 0.15}
TIEBREAK_CAP = 0.04
# How much a duplicated class costs in the SEARCH's ranking, as a fraction of
# the tiebreak budget. 1.0 spends the whole of it on a party of four identical
# heroes and proportionally less on a single pair, so like the shaping term it
# can only order candidates the fitness cannot separate.
#
# **DEFAULT 0.0, BECAUSE IT WAS MEASURED AND IT DOES NOTHING.** Over 2,000
# bootstrapped populations of 24, scored with real rollouts, it changes which
# four elites survive in 8% of them and moves their duplicate share by
# **+0.0pp** -- 23.6% either way.
#
# Selection was never the stage that produces duplicates. The seeded population
# is 38% duplicates and the elites 23.6%, so the fitness already selects
# against them, and neither `mutate` nor `crossover` raises the rate either
# (31% -> 26% over eight rounds).
#
# **`--ban-class` produces them**, and it is arithmetic rather than a
# preference. Fran's batch method is: emit a comp, ban its four classes, search
# again. That shrinks the pool every round, and a four-hero party drawn from
# what is left repeats a class 29% of the time at 20 classes, 43% at 12, and
# **63% at 8** -- which is the 61% the emitted corpus carries. He named the
# cause himself: *"abuses too much of 2 copy of the same class comps BECAUSE OF
# THE RESTRICTIONS"*.
#
# Kept rather than deleted: the mechanism is right, it costs nothing at 0.0,
# and it would matter if the ban strategy changed. Turning it on while the bans
# stay as they are buys nothing.
DUPLICATE_COST = 0.0
# Stress is reported as a level, not a rate. 200 is two full afflictions' worth
# and is used only to map it into [0, 1] for the shaping mean.
STRESS_SCALE = 200.0


HEROES_JS = Path(CSV_DIR) / "metadata" / "HEROES" / "heroes.js"

_JS_CLASS_RE = re.compile(r"^\s*'([^']+)':\s*\{", re.MULTILINE)
_JS_LIST_RE = re.compile(r"^\s*(skills|campSkills):\s*\[(.*?)\]", re.MULTILINE | re.DOTALL)
_JS_ITEM_RE = re.compile(r"'((?:[^'\\]|\\.)*)'")


def canonical_order() -> dict:
    """{hero display name: {"skills": [...], "campSkills": [...]}} in the order
    the GAME lists them, from `metadata/HEROES/heroes.js`.

    Emitted comps are sorted into this order because that is how the skills
    appear on the character sheet, and a comp written in a different order is
    harder to read against the screen than it needs to be. Nothing about the
    simulation depends on it -- `CombatEngine` resolves skills by id and the
    loadout is a set -- so this is presentation, and it is the one thing in
    this tool that is purely presentation.

    Display names, deliberately: `heroes.js` is written in them, the comp JSON
    is written in them, and this only ever runs at that boundary. An id that
    the file does not mention keeps its position at the end rather than being
    dropped.
    """
    if not HEROES_JS.exists():
        return {}
    text = HEROES_JS.read_text(encoding="utf-8")
    out: dict = {}
    bounds = [(m.group(1), m.start()) for m in _JS_CLASS_RE.finditer(text)]
    for i, (name, start) in enumerate(bounds):
        end = bounds[i + 1][1] if i + 1 < len(bounds) else len(text)
        block = text[start:end]
        entry = {}
        for kind, body in _JS_LIST_RE.findall(block):
            entry[kind] = [item.replace("\\'", "'") for item in _JS_ITEM_RE.findall(body)]
        if entry:
            out[name] = entry
    return out


def _sorted_by(names: Sequence[str], order: Sequence[str]) -> list:
    """`names` in `order`, with anything unlisted kept, in place, at the end."""
    index = {n: i for i, n in enumerate(order)}
    known = sorted((n for n in names if n in index), key=lambda n: index[n])
    return known + [n for n in names if n not in index]


def _tiebreak_weight(episodes: int) -> float:
    """Small enough that shaping can never overturn a real success difference."""
    return min(TIEBREAK_CAP, 0.8 / max(1, episodes))


def duplication_cost(genome, episodes: int, weight: float = DUPLICATE_COST) -> float:
    """What a repeated class costs this candidate in the search's ranking.

    Zero for four different classes, `weight * _tiebreak_weight` for four of
    one, linear between. Sharing the tiebreak budget with the shaping term is
    the point: a duplicate party that genuinely scores better still wins, and
    the cost only decides between candidates the fitness cannot tell apart.

    `--prefer-unique` expresses the same preference at EMIT, over a converged
    hall of fame; by then the population is already 61% duplicates and there is
    nothing left to prefer. This is that preference moved to where selection
    happens.
    """
    if not weight:
        return 0.0
    classes = [h.hero_class for h in genome]
    if len(classes) < 2:
        return 0.0
    repeats = len(classes) - len(set(classes))
    return (_tiebreak_weight(episodes) * weight
            * repeats / (len(classes) - 1))


def wearable_rarities(store) -> frozenset:
    """Which trinket rarities a comp may equip -- read off the curated corpus,
    not chosen.

    `UNIVERSAL_TRINKET_RARITIES` is the pool `--kit-random-p` draws from, and
    it is the right pool for that: it is what a *sampled* party could plausibly
    have found. It is the wrong pool for designing a comp. 455 of the 1039
    trinket slots across Fran's 136 curated comps -- 44% -- sit outside it:
    Shambler trinkets, Crimson Court, Color of Madness comets, boss trophies,
    the Collector's, the Madman's. Ancestor's Candle alone is worn in 28 of
    them. A search that cannot propose those is not searching the same space
    the curated comps live in, and its output would be systematically weaker
    for a reason that has nothing to do with the comps.

    So the rule is: every rarity a comp Fran has actually played equips, plus
    the universal ones. That is data with an automatic falsifier -- a new
    curated comp wearing a new rarity widens the pool by itself -- and it still
    leaves out the 294 `kickstarter` backer trinkets, which no comp wears.
    """
    used = {store.trinkets[tid].rarity
            for entry in store.comp_loadouts.values()
            for hero in entry.heroes
            for tid in hero.trinket_ids
            if tid in store.trinkets}
    return frozenset(UNIVERSAL_TRINKET_RARITIES) | used


# --------------------------------------------------------------------------
# The genome
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class HeroGene:
    """One party slot. Internal ids throughout -- display names exist only at
    the JSON boundary, and going through them internally is how a renamed
    skill silently becomes a different one."""
    hero_class: str
    skills: tuple[str, ...]
    camp_skills: tuple[str, ...]
    trinkets: tuple[str, ...]
    quirks: tuple[str, ...]

    def key(self) -> tuple:
        """Identity for the fitness cache. Skills, camp skills, trinkets and
        quirks are order-insensitive to the engine; the SLOT is not, and lives
        in the genome's tuple order rather than in here."""
        return (self.hero_class, tuple(sorted(self.skills)),
                tuple(sorted(self.camp_skills)), tuple(sorted(self.trinkets)),
                tuple(sorted(self.quirks)))


Genome = tuple


def genome_key(genome: Genome) -> tuple:
    return tuple(g.key() for g in genome)


def _weighted_pick(rng: random.Random, items: list, weights: list):
    """One item, with probability proportional to its weight.

    `random.choices` exists and is not used: it draws from the module-level
    Random when handed none, and every draw in this file has to come from the
    seeded `rng` it is given or a search stops being reproducible.
    """
    total = sum(weights)
    if total <= 0:
        return rng.choice(items)
    r = rng.random() * total
    upto = 0.0
    for item, w in zip(items, weights):
        upto += w
        if r <= upto:
            return item
    return items[-1]


_UNRESTRICTED_MEMO: dict = {}


def unrestricted(store) -> frozenset:
    """`unrestricted_heroes(store)`, memoised on store identity.

    THE CONSTANT IS NOT ENOUGH HERE. `UNRESTRICTED_HEROES` is the vanilla pair,
    so a search running under `DDSIM_MOD_DIRS` cut the Sibyl -- who declares
    `can_select_combat_skills false` exactly as the Abomination and the Duelist
    do -- down to a 4-of-7 she is not allowed to have. The first Sibyl comp
    this search emitted carried FOUR skills instead of seven, and had one draw
    gone differently it would have lost `align` and been stranded in a single
    stance for the whole raid with three skills it could never use.

    Same memo shape as `inert_trinkets`: keyed on `id(store)` with the store
    held as the value, so an id cannot be reused for a different store.
    """
    cached = _UNRESTRICTED_MEMO.get(id(store))
    if cached is not None:
        return cached[1]
    result = unrestricted_heroes(store)
    _UNRESTRICTED_MEMO[id(store)] = (store, result)
    return result


_INERT_TRINKETS_MEMO: dict = {}


def inert_trinkets(store) -> frozenset:
    """Trinket ids whose every buff does nothing in THIS engine.

    Probed rather than listed: each trinket's buffs are applied to a scratch
    combatant and the numeric fields are compared before and after. A hardcoded
    list of unhandled `stat_type`s would drift the moment a buff type is wired
    up, and this file is the wrong place to keep a second copy of
    `_apply_buff_to_stats`'s dispatch table.

    It finds **four** -- `diseased_herb`, `scouting_whistle`, `cc_rat_carcass`,
    `cc_crimson_tincture` -- so this is a small correction, not the reason the
    first search under-used trinkets. Worth doing anyway: offering a hero a
    trinket that provably cannot change the outcome spends a slot on nothing.

    Note what it does NOT catch. A trinket whose buffs move real stats but
    whose real value is outside combat -- Ancestor's Map is the example, since
    this project models no map -- is correctly scored as *whatever its stats
    are worth*, which is honest but is not what the trinket is for. That gap
    belongs to the missing dungeon layer, not here.
    """
    # MEMOISED, because it probes all 615 trinkets through a real
    # `CombatEngine` and costs 21 ms. That is nothing once and 28 s from
    # inside a loop over the curated trinket slots -- which is what
    # `tests/tools/test_comp_ga.py` did, and it was two thirds of the whole suite's
    # 62 s wall clock. The key holds the store itself, not `id(store)`: a
    # reference keeps it alive, so an id can never be reused by a later store
    # and hand back another store's answer. `DataStore` is an `eq=True`
    # dataclass and therefore unhashable, which is why this is a dict keyed on
    # identity rather than an `lru_cache`.
    cached = _INERT_TRINKETS_MEMO.get(id(store))
    if cached is not None:
        return cached[1]

    import copy

    from engine import CombatEngine, SeededRNG

    engine = CombatEngine(store, SeededRNG(0))
    hero = next(iter(sorted(store.heroes)))
    enemy = next(iter(sorted(store.enemies)))
    engine.reset(hero_defs=[store.heroes[hero]], enemy_defs=[store.enemies[enemy]])
    base = engine.heroes[0]

    def numbers(c):
        return {k: v for k, v in vars(c).items() if isinstance(v, (int, float, bool))}

    out = set()
    for tid, trinket in store.trinkets.items():
        if not trinket.buffs:
            continue
        scratch = copy.deepcopy(base)
        before = numbers(scratch)
        for buff_id in trinket.buffs:
            definition = store.buffs.get(buff_id)
            if definition is not None:
                engine._apply_buff_to_stats(scratch, definition)
        if numbers(scratch) == before:
            out.add(tid)
    result = frozenset(out)
    _INERT_TRINKETS_MEMO[id(store)] = (store, result)
    return result


# --------------------------------------------------------------------------
# Legality
# --------------------------------------------------------------------------

class CompSpace:
    """Every legal choice, read out of the store -- and the display names the
    curated JSON format is written in.

    The pools deliberately mirror `ExpeditionEnv`'s `--kit-random-p` sampler
    rather than reimplementing legality: same trinket predicate (universal
    has buffs, generic or class-locked to this hero), same quirk rules (no
    diseases, no self-contradictory pair), same 4-of-7 skills with
    `UNRESTRICTED_HEROES` carrying all seven. The one deliberate difference is
    which trinket RARITIES count -- see `wearable_rarities`.
    """

    def __init__(self, store, rarities=None, negative_quirks: bool = False,
                 class_trinket_bias: float = CLASS_TRINKET_BIAS,
                 synergy_bias: float = SYNERGY_TRINKET_BIAS,
                 banned=None, rarity_bias: float = 0.0,
                 kit_prior_bias: float = 0.0,
                 quirk_slots=None):
        self.store = store
        # How hard the class draw leans toward classes the curated pool uses
        # rarely. 0.0 is a uniform draw and is byte-identical to the
        # pre-2026-08-28 behaviour. See `draw_class` -- and note the draw is
        # gated on `rank_fit`, without which rarity builds parties that all
        # want the front rank.
        self.rarity_bias = float(rarity_bias)
        # How hard the quirk and trinket draws lean toward what the curated
        # corpus actually equips. 0.0 is uniform and byte-identical to the
        # pre-2026-08-28 behaviour. See `kit_prior`.
        self.kit_prior_bias = float(kit_prior_bias)
        # Classes this search may not use at all.
        #
        # Fran's 2026-08-26 method, and it is a search strategy rather than a
        # filter: emit one comp, ban the four classes it used, search again.
        # Each round is forced into an unexplored part of the roster, and over
        # a few rounds the batch covers it instead of recombining the same six
        # strong classes.
        #
        # It is also the honest way to deal with the Vestal. Measured on the
        # curated pool in the Weald: 22 of 158 comps field her, she appears in
        # 45% of the top 20 and 0% of the bottom 20, and comps with her average
        # 74.2 against 60.6 without -- but the best Vestal-free comp is 85.3
        # against 86.6 with. **She lifts the mean by 13.6 and the ceiling by
        # 1.3.** A hill-climb converges on whatever lifts the mean, so every
        # lineage finds her early and cannot leave; banning her is what forces
        # the search into the other basin, which the ceiling says is there.
        self.banned = frozenset(banned or ())
        self.negative_quirks = negative_quirks
        # HOW MANY POSITIVE QUIRK SLOTS THE SEARCH FILLS, and it is NOT
        # RANDOM_QUIRKS_PER_POLARITY. That constant is (1, 3) and is shared
        # with `--kit-random-p` in TRAINING, so moving it would silently
        # shift the training distribution and invalidate every archived
        # measurement -- it stays exactly where it is.
        #
        # A hero can hold FIVE positive quirks and a player can buy them,
        # which the draw_quirks docstring already argues. Drawing 1-3 left a
        # legal resource unused, the same shape as the trinket bug that
        # TRINKETS_PER_HERO fixed (seven trinkets across four heroes).
        # Measured 2026-08-30 by topping every hero of the seven Cove comps
        # up to five live quirks, paired, on TWO checkpoints: **13 of 14
        # comparisons positive, mean +2.3**. The gains land on the WEAK
        # comps (cove7 +8.1, cove6 +11.7) because the strong ones are
        # already against the pool's 97.5 ceiling.
        #
        # Negative quirks keep the old range: filling five of THOSE is not
        # a state a player buys on purpose.
        self.quirk_slots = tuple(quirk_slots or SEARCH_QUIRKS_PER_POLARITY)
        self.rarities = frozenset(rarities) if rarities else wearable_rarities(store)
        self.class_trinket_bias = class_trinket_bias
        self.synergy_bias = synergy_bias
        self._payload_cache: dict = {}
        self._synergy_cache: dict = {}
        # (hero_id, skill_id) -> the ranks the skill LAUNCHES from, and how far
        # it moves the performer. Read off the top skill level, which is the
        # tier every comp here is fielded at; `.launch` does not vary by level
        # in any shipped hero file.
        self._launch: dict = {}
        self._move_self: dict = {}
        self.classes = sorted(c for c in store.heroes if c not in self.banned)
        self._inert = inert_trinkets(store)
        self.skill_pool = {}
        self.camp_pool = {}
        self.trinket_pool = {}
        self.limited_pool = {}
        self.unlimited_pool = {}
        self.class_pool = {}
        for hero_id in self.classes:
            hero = store.heroes[hero_id]
            self.skill_pool[hero_id] = sorted({s.id for s in hero.skills})
            # STRICT membership: an empty `hero_classes` means no class, not
            # every class. Exactly one skill has one -- `hobby` -- and treating
            # it as universal gave every class 8 skills and the Flagellant 5.
            # Strict gives 7 to all nineteen others and 4 to the Flagellant,
            # which is the real game's roster, and no curated comp equips
            # Hobby in 136 files. It is a template row, not a camping skill.
            self.camp_pool[hero_id] = sorted(
                s.id for s in store.camping_skills.values()
                if hero_id in s.hero_classes)
            self.trinket_pool[hero_id] = sorted(
                t.id for t in store.trinkets.values()
                if t.rarity in self.rarities and t.buffs
                and t.id not in self._inert
                and (not t.class_requirements or hero_id in t.class_requirements))
            # Three cuts of the same pool, because a draw needs all three.
            # `limit` is the game's own "you can only own one of these" marker
            # and 56 of 154 carry it; `class_requirements` is the ~9 built
            # around this hero's kit.
            self.limited_pool[hero_id] = [t for t in self.trinket_pool[hero_id]
                                          if store.trinkets[t].limit]
            self.unlimited_pool[hero_id] = [t for t in self.trinket_pool[hero_id]
                                            if not store.trinkets[t].limit]
            self.class_pool[hero_id] = [t for t in self.unlimited_pool[hero_id]
                                        if store.trinkets[t].class_requirements]
        self.quirk_pool = {
            True: sorted(q.id for q in store.quirks.values()
                         if q.is_positive and not q.is_disease),
            False: sorted(q.id for q in store.quirks.values()
                          if not q.is_positive and not q.is_disease),
        }
        self._incompat_cache: dict = {}
        self._display = _DisplayNames(store)

    def incompatible_with(self, quirk_id: str) -> frozenset:
        """Both directions. The data states a pair from one side only -- `tough`
        names `fragile` and `fragile` does not name `tough` -- so a one-way check
        happily builds a hero who is both."""
        hit = self._incompat_cache.get(quirk_id)
        if hit is None:
            out = set(self.store.quirks[quirk_id].incompatible_quirks)
            out.update(o.id for o in self.store.quirks.values()
                       if quirk_id in o.incompatible_quirks)
            hit = frozenset(out)
            self._incompat_cache[quirk_id] = hit
        return hit

    # -- construction ------------------------------------------------------

    # -- rank legality -----------------------------------------------------
    #
    # A skill that cannot be USED from the rank its hero stands in is a wasted
    # slot, and the fitness cannot see it: the policy simply plays whatever is
    # legal, so a dead skill costs nothing directly and only forfeits the
    # better skill that could have been there -- an opportunity cost smaller
    # than the fitness can resolve. Measured on the 2026-08-26 run before this
    # existed: **30 of 128 equipped skills (23%) could not be used from their
    # hero's own rank**, Hand of Light on a rank-4 Vestal in four of eight
    # comps, and one comp had 7 of 16 dead. Fran named it from the output
    # alone. Same shape of missing constraint as `TRINKETS_PER_HERO`.

    def launch_ranks(self, hero_id: str, skill_id: str) -> frozenset:
        """The ranks this skill can be performed FROM. Empty means anywhere."""
        key = (hero_id, skill_id)
        hit = self._launch.get(key)
        if hit is None:
            hit = frozenset()
            hero = self.store.heroes.get(hero_id)
            skill = next((x for x in hero.skills if x.id == skill_id), None) if hero else None
            if skill and skill.levels:
                lv = skill.levels[max(skill.levels)]
                hit = frozenset(lv.launch or ())
                self._move_self[key] = int(lv.move_self or 0)
            self._launch[key] = hit
        return hit

    def move_self(self, hero_id: str, skill_id: str) -> int:
        """Signed rank shift the skill applies to its performer.

        Negative moves FORWARD (toward rank 1), matching the engine's
        `performer.position + skill.move_self` on a 0-indexed position.
        """
        key = (hero_id, skill_id)
        if key not in self._move_self:
            self.launch_ranks(hero_id, skill_id)
        return self._move_self.get(key, 0)

    def reachable_ranks(self, hero_id: str, skills, rank: int,
                        size: int = PARTY_SIZE) -> frozenset:
        """Every rank this hero can put ITSELF in, using only its own kit.

        A fixpoint rather than a single step: Duelist's Advance from rank 4
        reaches 3, and from 3 reaches 2, so a two-step chain is real. Enemy
        pushes and pulls are deliberately NOT counted -- Fran's rule is that
        off-rank coverage matters in proportion to the comp's own self-move,
        and everything else is a surprise contingency that camping and
        scouting mostly prevent.
        """
        reach = {rank}
        changed = True
        while changed:
            changed = False
            for sid in skills:
                shift = self.move_self(hero_id, sid)
                if not shift:
                    continue
                launch = self.launch_ranks(hero_id, sid)
                for r in list(reach):
                    if launch and r not in launch:
                        continue
                    dest = min(size, max(1, r + shift))
                    if dest not in reach:
                        reach.add(dest)
                        changed = True
        return frozenset(reach)

    def dead_skills(self, hero_id: str, skills, rank: int,
                    size: int = PARTY_SIZE) -> tuple:
        """Equipped skills usable from no rank this hero can reach."""
        if not skills or hero_id in unrestricted(self.store):
            return ()
        reach = self.reachable_ranks(hero_id, skills, rank, size)
        return tuple(sid for sid in skills
                     if self.launch_ranks(hero_id, sid)
                     and not (self.launch_ranks(hero_id, sid) & reach))

    # -- trinket coherence -------------------------------------------------
    #
    # Fran, 2026-08-26: "using ranged trinkets with a full melee highwayman
    # doesnt make sense". The general rule behind that example is checkable
    # statically, because the game states the condition itself: a buff carries
    # a `rule_type`, and three of them are decided by facts the comp has
    # already fixed by the time trinkets are chosen.
    #
    #   in_rank      the buff fires only in one rank (`rule_data_float`, which
    #                the engine compares against the 0-indexed position)
    #   meleeonly    only on a MELEE skill
    #   rangedonly   only on a RANGED skill
    #
    # A trinket whose every buff is switched off by those tests is a worn slot
    # doing nothing -- the same class of waste as a hero carrying a skill it
    # cannot perform, and invisible to the fitness for the same reason. 92
    # trinkets carry an `in_rank` buff and 12 are gated by nothing else; 36
    # carry `rangedonly` and 31 `meleeonly`.
    #
    # Deliberately NOT judged here: `lightbelow`/`lightabove` (the torch is a
    # property of the run, not the comp), `monsterType` (the region decides
    # it, and a region-pinned search could judge it -- filed, not guessed) and
    # every unconditional buff, which is always live.

    _DEAD_GATES = ("in_rank", "meleeonly", "rangedonly", "lightabove", "lightbelow")

    # The torch is a PARTY-WIDE property the player controls, so a comp commits
    # to one light direction and everything gated the other way is dead.
    #
    # Fran, 2026-08-27: *"lightabove or lightbelow buffs doesn't really matter
    # as you can manipulate light with provisions easily"* -- and then, on why
    # it is a rule at all: *"its just to not mix both buffs as active at the
    # same time"*. Light gating is not a risk to avoid; it is a CHOICE to make
    # once, per comp. Mixing the two directions guarantees one of them never
    # fires whichever way the torch is played.
    #
    # Scoped to the COMP, not the hero, because the torch is one number for the
    # whole party. `dark_bracer` carries both directions on one trinket and is
    # the game's own dead point (torch exactly 51 fires neither) -- that is the
    # data's business, not a comp error, so a single trinket carrying both is
    # judged on whichever half matches the comp's direction.

    # Region quirks, and the region a search is FOR.
    #
    # `<region>_adventurer/_explorer/_scrounger/_tactician/_phobe` exist for
    # ruins, cove and weald only -- the Warrens has none. Each carries a
    # `rule_type: in_dungeon` buff naming its region, which the engine now
    # honours (2026-08-27); before that they all paid out everywhere, which is
    # why the search took them at random and Fran found "quirks for other
    # regions" in the output.
    #
    # With the gate live the search would learn to avoid the wrong ones on its
    # own, eventually. Rewriting them is faster and free: the RIGHT region's
    # quirk is strictly better than a dead one, so this can only help, and it
    # makes an emitted comp correct to play immediately.
    _REGION_QUIRK_FAMILIES = ("adventurer", "explorer", "scrounger",
                              "tactician", "phobe")
    #: `zone_id` -> the prefix its quirks are named with. The Ruins is
    #: `crypts` as a zone and `ruins` as a quirk, which is exactly the sort of
    #: mismatch that makes a string-built id wrong in silence.
    _ZONE_QUIRK_PREFIX = {"crypts": "ruins", "cove": "cove", "weald": "weald"}

    def region_of_quirk(self, quirk_id: str) -> tuple:
        """(prefix, family) if this is a region quirk, else ()."""
        for fam in self._REGION_QUIRK_FAMILIES:
            suffix = f"_{fam}"
            if quirk_id.endswith(suffix):
                return quirk_id[:-len(suffix)], fam
        return ()

    def retarget_region_quirks(self, genome: Genome, zone_id: str) -> Genome:
        """Point every region quirk at the region this comp is being built for.

        A quirk for another region is simply dead here, so swapping it is never
        a downgrade. The Warrens has no region quirks at all, so there a dead
        one is DROPPED rather than rewritten -- leaving it in would ship a comp
        whose quirk sheet claims a bonus it cannot have.
        """
        want = self._ZONE_QUIRK_PREFIX.get((zone_id or "").lower())
        out = []
        for gene in genome:
            quirks = []
            for qid in gene.quirks:
                parsed = self.region_of_quirk(qid)
                if not parsed:
                    quirks.append(qid)
                    continue
                prefix, fam = parsed
                if prefix == want:
                    quirks.append(qid)
                    continue
                if want:
                    swapped = f"{want}_{fam}"
                    # Only if it is real and not already worn -- a duplicate
                    # quirk is a different kind of broken comp.
                    if swapped in self.store.quirks and swapped not in quirks:
                        quirks.append(swapped)
                # No `want` (the Warrens, or an unstated zone) means drop it.
            out.append(replace(gene, quirks=tuple(sorted(quirks))))
        return tuple(out)

    def light_direction(self, genome: Genome) -> str:
        """Which way this comp plays the torch: 'above', 'below' or ''.

        Decided by weight of worn buffs, ties to 'above' -- a player keeps the
        torch up unless something specific pays for the dark.
        """
        above = below = 0
        for gene in genome:
            for tid in gene.trinkets:
                trinket = self.store.trinkets.get(tid)
                for bid in (trinket.buffs if trinket else ()) or ():
                    buff = self.store.buffs.get(bid)
                    rt = getattr(buff, "rule_type", None) if buff else None
                    if rt == "lightabove":
                        above += 1
                    elif rt == "lightbelow":
                        below += 1
        if not above and not below:
            return ""
        return "below" if below > above else "above"

    def _skill_types(self, hero_id: str, skills) -> frozenset:
        hero = self.store.heroes.get(hero_id)
        if not hero:
            return frozenset()
        wanted = set(skills) if skills else {x.id for x in hero.skills}
        return frozenset(x.skill_type for x in hero.skills if x.id in wanted)

    def _buff_is_live(self, buff, rank: int, types, light: str = "") -> bool:
        rt = getattr(buff, "rule_type", None)
        if rt not in self._DEAD_GATES:
            return True          # unconditional, or gated on something the
                                 # comp cannot decide in advance
        if rt == "in_rank":
            return int(getattr(buff, "rule_data_float", 0) or 0) + 1 == rank
        if rt in ("lightabove", "lightbelow"):
            # With no direction chosen yet, neither half is judged: a comp that
            # wears nothing light-gated must not have its first such trinket
            # declared dead on arrival.
            if not light:
                return True
            return rt == f"light{light}"
        from data.types import SkillType
        want = SkillType.MELEE if rt == "meleeonly" else SkillType.RANGED
        return want in types

    def trinket_is_dead(self, hero_id: str, skills, rank: int,
                        trinket_id: str, light: str = "") -> bool:
        """Every buff this trinket carries is switched off for this hero."""
        trinket = self.store.trinkets.get(trinket_id)
        if not trinket or not trinket.buffs:
            return False
        types = self._skill_types(hero_id, skills)
        live = False
        for bid in trinket.buffs:
            buff = self.store.buffs.get(bid)
            if buff is None:
                return False     # unknown: never call it dead on ignorance
            if self._buff_is_live(buff, rank, types, light):
                live = True
                break
        return not live

    def live_trinkets(self, hero_id: str, skills, rank: int, pool,
                      light: str = "") -> list:
        return [t for t in pool
                if not self.trinket_is_dead(hero_id, skills, rank, t, light)]

    def light_conflicts(self, genome: Genome) -> list:
        """Worn trinkets gated the OPPOSITE way to the comp's own direction."""
        light = self.light_direction(genome)
        if not light:
            return []
        out = []
        for slot, gene in enumerate(genome):
            for tid in gene.trinkets:
                if self.trinket_is_dead(gene.hero_class, gene.skills, slot + 1,
                                        tid, light):
                    out.append((slot + 1, gene.hero_class, tid))
        return out

    def repair_dead_trinkets(self, rng: random.Random, genome: Genome) -> Genome:
        """Swap out trinkets that cannot fire for the hero wearing them.

        Runs LAST in `finish`, after ranks and skills are settled, because both
        of those are inputs to the test. That ordering is Fran's build order --
        comp and ranks, then skills, then trinkets and quirks -- expressed as a
        dependency rather than as a preference.
        """
        size = len(genome)
        out = list(genome)
        # ONE direction for the whole comp, decided before any repair, so the
        # repair cannot chase its own tail by flipping the majority each swap.
        light = self.light_direction(genome)
        for slot, gene in enumerate(genome):
            rank = slot + 1
            dead = [t for t in gene.trinkets
                    if self.trinket_is_dead(gene.hero_class, gene.skills, rank,
                                            t, light)]
            if not dead:
                continue
            keep = [t for t in gene.trinkets if t not in dead]
            for _ in range(len(dead)):
                pick = self.draw_trinket(rng, gene.hero_class, exclude=tuple(keep),
                                         skills=gene.skills)
                if pick is None or self.trinket_is_dead(gene.hero_class,
                                                        gene.skills, rank, pick,
                                                        light):
                    continue
                keep.append(pick)
            # A hero left short goes to `top_up`, which is the single place
            # that refills a slot; never leave one wearing fewer than two.
            out[slot] = replace(gene, trinkets=tuple(sorted(keep)))
        return self.top_up(rng, tuple(out))

    def viable_ranks(self, hero_id: str, size: int = PARTY_SIZE) -> frozenset:
        """The ranks this class can field a FULL kit from.

        Derived, never written down: a rank is viable when the class has at
        least `SKILLS_PER_HERO` skills that launch from it. Read off the
        shipped data this reproduces the game's own common knowledge exactly --
        Arbalest and Musketeer 3-4, Leper, Hellion and Crusader 1-2, Vestal and
        Plague Doctor 2-4, Shieldbreaker 1-3 -- which is the check that it is
        measuring the right thing.

        This is a STRUCTURAL rule, enforced where Fran puts it: the comp and
        its 1-2-3-4 slots come first, then skills, then trinkets. A Leper in
        rank 4 is not a hero with a bad loadout, it is a hero with no legal
        loadout at all, and no amount of skill search repairs it.
        """
        key = ("viable", hero_id, size)
        hit = self._launch.get(key)
        if hit is None:
            want = min(SKILLS_PER_HERO, len(self.skill_pool.get(hero_id, ())))
            hit = frozenset(r for r in range(1, size + 1)
                            if len(self.usable_from(hero_id, r)) >= want)
            # A class with no viable rank at all would make the repair loop
            # unsatisfiable; fall back to every rank rather than deadlock.
            self._launch[key] = hit or frozenset(range(1, size + 1))
        return self._launch[key]

    def classes_for_rank(self, rank: int, size: int = PARTY_SIZE) -> list:
        return [c for c in self.classes if rank in self.viable_ranks(c, size)]

    def kit_prior(self) -> tuple:
        """({quirk_id: times equipped}, {(hero_id, trinket_id): times equipped}).

        Fran, 2026-08-28: *"i usually have to keep fixing quirks and trinkets,
        but the skills loadout are somewhat solid"* -- so the search is doing
        well on the axis it searches hardest and poorly on the two he then
        corrects by hand. 167 curated comps carry his actual answers.

        And his own caveat, which is why the two halves are treated
        differently: *"i havent innovated too much, nor change too much the
        quirks, as there are in the game some game breaking quirks that always
        are better than others, its difficult to gauge this."*

        * **Quirks: exploit.** If a handful genuinely are better, learning them
          is correct rather than biased.
        * **Trinkets: bias, do not narrow.** ~98 unlimited choices per hero is
          the widest and noisiest axis; a prior strong enough to freeze his
          habits there would remove the only part of the search that has ever
          found him something new.

        Counts, not probabilities: the caller blends them with a uniform draw
        so the whole pool stays reachable.
        """
        cached = getattr(self, "_kit_prior", None)
        if cached is not None:
            return cached
        quirks: dict = {}
        trinkets: dict = {}
        for entry in self.store.comp_loadouts.values():
            for hero in entry.heroes:
                if not hero.hero_id:
                    continue
                for pol in ("positive", "negative"):
                    for qid in hero.quirk_ids.get(pol, ()):
                        quirks[qid] = quirks.get(qid, 0) + 1
                for tid in hero.trinket_ids:
                    key = (hero.hero_id, tid)
                    trinkets[key] = trinkets.get(key, 0) + 1
        self._kit_prior = (quirks, trinkets)
        return self._kit_prior

    def _prior_weights(self, items: list, counts: dict, key=lambda x: x) -> list:
        """Blend a corpus count with a uniform draw at `self.kit_prior_bias`.

        Every item keeps a nonzero weight at any bias below 1.0, which is what
        makes this a prior rather than a filter -- the same contract
        `SYNERGY_TRINKET_BIAS` and `class_rarity` hold.
        """
        if not items:
            return []
        raw = [float(counts.get(key(i), 0)) for i in items]
        top = max(raw) or 1.0
        mean = sum(raw) / len(raw)
        base = (mean / top) or (1.0 / len(items))
        w = self.kit_prior_bias
        return [(1.0 - w) * base + w * (r / top) for r in raw]

    def class_rarity(self) -> dict:
        """{hero_id: draw weight}, from how RARE the class is in the corpus.

        Fran's pool is steeply skewed -- Houndmaster fills 96 hero slots and
        Crusader 64, against Musketeer 16, Runaway 7 and Duelist 6 -- and the
        search inherits that skew through its curated seeds. Weighting the draw
        by `1 / (1 + slots)` surfaces the rare classes without a ban list,
        which is what he asked for after four rounds of banning by hand.

        A PRIOR AND NOT A RESTRICTION, the same shape as
        `SYNERGY_TRINKET_BIAS`: every class keeps a nonzero weight, and
        `--ban-class` still overrides absolutely. And it is gated on
        `rank_fit` at the call site -- see `draw_class` -- because rarity on
        its own builds parties that all want rank 1.
        """
        cached = getattr(self, "_rarity", None)
        if cached is not None:
            return cached
        slots: dict = {}
        for entry in self.store.comp_loadouts.values():
            for hero in entry.heroes:
                if hero.hero_id:
                    slots[hero.hero_id] = slots.get(hero.hero_id, 0) + 1
        self._rarity = {c: 1.0 / (1.0 + slots.get(c, 0)) for c in self.classes}
        return self._rarity

    def draw_class(self, rng: random.Random, pool: list, current=None,
                   size: int = PARTY_SIZE, bias: float = 0.0) -> str:
        """One class from `pool`, optionally biased toward the rare ones.

        `bias` blends the rarity weights with a uniform draw: 0.0 is uniform
        (the pre-2026-08-28 behaviour, exactly), 1.0 is pure rarity.

        **`current` is the anti-loop.** When replacing a hero in a party, the
        substitution is refused if it lowers the party's `rank_fit` -- so
        rarity can push the search into unexplored roster but never into four
        heroes contending for the same rank. Without it, biasing toward
        unused classes reliably builds the Hellion/Leper/Flagellant/
        Shieldbreaker party Fran warned about: legal, and bad precisely
        because it is legal.
        """
        if not pool:
            pool = self.classes
        if not pool:
            return current
        if bias <= 0.0:
            return rng.choice(pool)
        weights = self.class_rarity()
        mean = sum(weights.get(c, 0.0) for c in pool) / len(pool) or 1.0
        blended = [(1.0 - bias) * mean + bias * weights.get(c, 0.0) for c in pool]
        total = sum(blended)
        if total <= 0:
            return rng.choice(pool)
        pick = _weighted_pick(rng, pool, blended)
        if current is None or current.get("genome") is None:
            return pick
        genome, slot = current["genome"], current["slot"]
        classes = [g.hero_class for g in genome]
        before = self.rank_fit(classes, size)
        after = list(classes)
        after[slot] = pick
        if self.rank_fit(after, size) + 1e-9 < before:
            return classes[slot]          # refused: the party would fit worse
        return pick

    def rank_fit(self, classes, size: int = PARTY_SIZE) -> float:
        """How well a class MULTISET can share the four ranks, at its best.

        Every other rank rule in this file is per-hero: `viable_ranks` asks
        whether one class can fight from one rank, and `repair_ranks` swaps
        until nobody stands anywhere illegal. Nothing asked the party-level
        question, and Fran named the gap exactly when he was offered a bias
        toward rare classes:

            "Hellion, Leper, flagellant and shieldbreaker all want pos 1, even
             if this comp is 'possible' doesnt mean it will be good because
             preciselly that, so biasing towards non used heroes can make bad
             comps with this."

        He is right, and `repair_ranks` cannot see it: four front-rank classes
        pass every individual test and then three of them stand somewhere they
        can barely act.

        So: over every assignment of heroes to ranks, take the BEST total of
        how many skills each hero can actually launch from where it stands. The
        maximum over permutations rather than the value of the current order,
        because the ordering search will find that arrangement if it exists --
        this is a property of the multiset, which is what the class draw picks.

        Averaged per hero so parties of different sizes compare, and so the
        number reads as "skills available per hero, at best". `SKILLS_PER_HERO`
        is a natural reference point: below it, somebody cannot fill a bar.
        """
        classes = tuple(classes)
        key = ("fit", classes, size)
        hit = self._launch.get(key)
        if hit is not None:
            return hit
        import itertools
        n = min(len(classes), size)
        best = 0
        for order in itertools.permutations(range(n)):
            total = sum(len(self.usable_from(classes[order[r]], r + 1))
                        for r in range(n))
            best = max(best, total)
        value = best / max(n, 1)
        self._launch[key] = value
        return value

    def repair_ranks(self, rng: random.Random, genome: Genome) -> Genome:
        """Put every hero in a rank it can actually fight from.

        Swaps first, because a swap preserves the class multiset -- the comp's
        identity, and the thing the hall of fame keys on. Only a hero that no
        swap can place has its class redrawn, which is the same last resort
        `enforce_required` takes.
        """
        size = len(genome)
        out = list(genome)

        def misplaced(seq):
            return [i for i, g in enumerate(seq)
                    if (i + 1) not in self.viable_ranks(g.hero_class, size)]

        bad = misplaced(out)
        if not bad:
            return tuple(out)
        improved = True
        while improved and bad:
            improved = False
            for i in list(bad):
                for j in range(size):
                    if i == j:
                        continue
                    trial = list(out)
                    trial[i], trial[j] = trial[j], trial[i]
                    if len(misplaced(trial)) < len(bad):
                        out = trial
                        bad = misplaced(out)
                        improved = True
                        break
                if improved:
                    break
        for i in misplaced(out):
            pool = self.classes_for_rank(i + 1, size)
            if not pool:
                continue
            out[i] = self.random_hero(rng, rng.choice(pool), rank=i + 1, size=size)
        return tuple(out)

    def usable_from(self, hero_id: str, rank: int) -> list:
        """The hero's skills that launch from `rank` with no movement needed."""
        return [sid for sid in self.skill_pool.get(hero_id, ())
                if not self.launch_ranks(hero_id, sid)
                or rank in self.launch_ranks(hero_id, sid)]

    def random_skills(self, rng: random.Random, hero_id: str,
                      rank: Optional[int] = None,
                      size: int = PARTY_SIZE) -> tuple:
        """A legal 4-of-7. With `rank`, one the hero can actually PERFORM.

        The rank-aware draw seeds from skills launchable where the hero stands
        and then lets `move_self` open the rest up, which is why it is a
        rejection loop rather than a filter: a kit built around Duelist's
        Advance is legal precisely because of a skill that is itself legal.
        """
        if hero_id in unrestricted(self.store):
            # No 4-of-7 exists for these: the game equips all seven and a MODE
            # decides which are usable. `()` is the engine's "unrestricted".
            return ()
        pool = self.skill_pool[hero_id]
        if len(pool) <= SKILLS_PER_HERO:
            return tuple(pool)
        if rank is None:
            return tuple(sorted(rng.sample(pool, SKILLS_PER_HERO)))
        # Seed from something performable here, so the fixpoint has a start.
        here = self.usable_from(hero_id, rank)
        for _ in range(24):
            if here:
                first = rng.choice(here)
                rest = [x for x in pool if x != first]
                pick = [first] + rng.sample(rest, SKILLS_PER_HERO - 1)
            else:
                pick = rng.sample(pool, SKILLS_PER_HERO)
            if not self.dead_skills(hero_id, pick, rank, size):
                return tuple(sorted(pick))
        # A hero with too few reachable skills keeps the least-dead draw rather
        # than looping forever; `repair_dead_skills` gets another go at it.
        return tuple(sorted(pick))

    def repair_dead_skills(self, rng: random.Random, genome: Genome) -> Genome:
        """Swap out every skill no hero can perform from where it stands.

        Applied in `finish`, so it runs after EVERY operator -- crossover and
        the position mutation both move a hero into a rank its kit was never
        drawn for, which is exactly when this appears.
        """
        size = len(genome)
        out = list(genome)
        for slot, gene in enumerate(genome):
            rank = slot + 1
            dead = self.dead_skills(gene.hero_class, gene.skills, rank, size)
            if not dead:
                continue
            keep = [x for x in gene.skills if x not in dead]
            spare = [x for x in self.usable_from(gene.hero_class, rank)
                     if x not in keep]
            rng.shuffle(spare)
            while len(keep) < SKILLS_PER_HERO and spare:
                keep.append(spare.pop())
            # Still short only if the class has fewer than four skills usable
            # here at all; fall back to the originals rather than field a
            # three-skill hero, which is a different and worse bug.
            for x in dead:
                if len(keep) >= SKILLS_PER_HERO:
                    break
                keep.append(x)
            out[slot] = replace(gene, skills=tuple(sorted(keep)))
        return tuple(out)

    def random_camp_skills(self, rng: random.Random, hero_id: str) -> tuple:
        pool = self.camp_pool[hero_id]
        n = min(CAMP_SKILLS_PER_HERO, len(pool))
        return tuple(sorted(rng.sample(pool, n))) if pool else ()

    def skill_payloads(self, hero_id: str, skill_id: str) -> frozenset:
        """Which resistable payloads this skill applies -- memoised per skill.

        Read off the effects the skill names, not off a list here: a skill
        gains or loses a payload when the game's data does.
        """
        key = (hero_id, skill_id)
        cached = self._payload_cache.get(key)
        if cached is not None:
            return cached
        hd = self.store.heroes.get(hero_id)
        sk = next((k for k in (hd.skills if hd else []) if k.id == skill_id), None)
        found = set()
        if sk is not None and sk.levels:
            lv = sk.levels[max(sk.levels)]
            for name in (lv.effects or []):
                eff = self.store.effects.get(name)
                if eff is None:
                    continue
                if eff.bleed_amount is not None:
                    found.add("bleed")
                if eff.blight_amount is not None:
                    found.add("blight")
                if eff.stun_chance is not None:
                    found.add("stun")
                if (eff.push_distance or 0) > 0 or (eff.pull_distance or 0) > 0:
                    found.add("move")
                if ((eff.buff_ids or eff.is_stat_buff or eff.inline_buff_type)
                        and eff.target == "target"):
                    found.add("debuff")
            if (lv.push or 0) or (lv.pull or 0):
                found.add("move")
        out = frozenset(found)
        self._payload_cache[key] = out
        return out

    def kit_payloads(self, hero_id: str, skills=None) -> frozenset:
        """What this hero's EQUIPPED kit applies. `skills=None` (or empty, as
        for an unrestricted class) falls back to the full movepool, which is
        the honest answer for a hero whose four are not chosen yet."""
        chosen = tuple(skills or ()) or tuple(self.skill_pool.get(hero_id, ()))
        out = set()
        for sid in chosen:
            out |= self.skill_payloads(hero_id, sid)
        return frozenset(out)

    def synergy_pool(self, hero_id: str, skills=None) -> tuple:
        """Unlimited trinkets carrying a POSITIVE chance modifier for a payload
        this kit actually delivers."""
        key = (hero_id, tuple(sorted(skills or ())))
        cached = self._synergy_cache.get(key)
        if cached is not None:
            return cached
        payloads = self.kit_payloads(hero_id, skills)
        out = []
        for tid in self.unlimited_pool[hero_id]:
            trinket = self.store.trinkets.get(tid)
            for buff_id in (trinket.buffs or []) if trinket else []:
                bd = self.store.buffs.get(buff_id)
                if (bd and bd.amount > 0
                        and CHANCE_STAT_PAYLOAD.get(bd.stat_type) in payloads):
                    out.append(tid)
                    break
        out = tuple(out)
        self._synergy_cache[key] = out
        return out

    def trinket_set(self, hero_id: str) -> tuple:
        """The complete trinket SET this class can wear, or ().

        Every one of the 20 classes has exactly one -- the Crimson Court pair
        for the vanilla eighteen, plus `duelist_set1`, `rw_set1` and
        `sb_set1`. `Trinket.set_id` is the game's own field and
        `CombatEngine` already counts worn sets and applies their buffs
        (`combat_engine.py:334`), so this is a search gap, not a fidelity one.

        **The search could not reach one by accident.** `trinket_pair` redraws
        two trinkets out of ~154 wearable, so landing both halves is about 1 in
        11,800 per redraw -- it has never assembled a set and never would.
        Fran's hand-upgrade of `GA_wealdban_01` put both Crimson Court trinkets
        on the Arbalest and healing trinkets and quirks on the Crusader, and
        measured paired against the original at 64 episodes it is worth
        **+17.5 +-3.5**: stress 39.1 -> 11.9, deaths 0.42 -> 0.06, same four
        classes in the same four ranks. His reading, and the measurement agrees:
        *"you can get vestal-like healing trinkets without vestal"*.
        """
        key = ("set", hero_id)
        hit = self._launch.get(key)
        if hit is None:
            members: dict = {}
            for tid in self.trinket_pool.get(hero_id, ()):  # wearable only
                trinket = self.store.trinkets.get(tid)
                if trinket and trinket.set_id:
                    members.setdefault(trinket.set_id, []).append(tid)
            complete = [tuple(sorted(v)) for v in members.values()
                        if len(v) >= TRINKETS_PER_HERO]
            hit = complete[0] if complete else ()
            self._launch[key] = hit
        return hit

    def draw_trinket(self, rng: random.Random, hero_id: str, exclude=(),
                     limited: bool = False, skills=None) -> Optional[str]:
        """One trinket for this hero, or None if nothing legal is left.

        `limited=True` draws ONLY from the one-offs. Otherwise the draw reaches
        both pools -- `ONE_OFF_DRAW_RATE` of the time it offers a one-off --
        through two priors that are about WHERE TO LOOK and neither of which
        restricts anything: a trinket whose chance modifier matches the kit's
        own payloads `SYNERGY_TRINKET_BIAS` of the time, then the hero's
        class-locked ones `class_trinket_bias` of the time.

        **One-offs became reachable from the ordinary draw on 2026-08-26.**
        They were not, because the comp was capped at a single one and
        `grant_one_off` placed it -- so the search could never build a party
        around several different Ancestrals, which is what Fran's own comps do.
        The cap was a misreading; the game's rule is per id, and
        `enforce_trinket_budget` is what keeps two heroes from wearing the same
        Ancestor's Candle. The CALLER still owns that budget, so drawing freely
        here is safe.
        """
        if limited:
            pool = [t for t in self.limited_pool[hero_id] if t not in exclude]
            return rng.choice(pool) if pool else None
        # ORDER: synergy, then one-offs, then the class pool, then uniform.
        # Matching the kit's own payload is a stronger reason to wear a trinket
        # than "it is boss gear", and putting the one-off roll first also broke
        # the flag's contract -- `--synergy-bias 1` must mean every draw is a
        # synergy trinket, which is what makes the pre-2026-08-22 arm
        # reproducible.
        if self.synergy_bias and rng.random() < self.synergy_bias:
            matched = [t for t in self.synergy_pool(hero_id, skills) if t not in exclude]
            if matched:
                return rng.choice(matched)
        if self.limited_pool[hero_id] and rng.random() < ONE_OFF_DRAW_RATE:
            one_offs = [t for t in self.limited_pool[hero_id] if t not in exclude]
            if one_offs:
                return rng.choice(one_offs)
        if self.class_pool[hero_id] and rng.random() < self.class_trinket_bias:
            biased = [t for t in self.class_pool[hero_id] if t not in exclude]
            if biased:
                return rng.choice(biased)
        pool = [t for t in self.unlimited_pool[hero_id] if t not in exclude]
        if not pool:
            return None
        if self.kit_prior_bias:
            # Weighted by how often Fran equips this trinket ON THIS CLASS --
            # a Houndmaster's answer is not an Arbalest's, and pooling them
            # would just rediscover the globally popular trinkets.
            _q, trinkets = self.kit_prior()
            weights = self._prior_weights(pool, trinkets,
                                          key=lambda t: (hero_id, t))
            return _weighted_pick(rng, pool, weights)
        return rng.choice(pool)

    def random_trinkets(self, rng: random.Random, hero_id: str, skills=None) -> tuple:
        """EXACTLY two, always.

        Two is the number of slots the game gives and, in Fran's words, "if the
        game would allow 3 you would go with 3" -- a hero wearing one trinket is
        never a plan, it is a slot the search forgot to fill. The first version
        took `min(2, pool)` and let `repair` drop below that, and it emitted
        comps with seven trinkets across four heroes.

        Either may be a one-off since 2026-08-26 -- see `draw_trinket`. The
        party-wide budget is not a per-hero decision, so `enforce_trinket_budget`
        is what stops two heroes wearing the same one.
        """
        chosen: list = []
        while len(chosen) < TRINKETS_PER_HERO:
            pick = self.draw_trinket(rng, hero_id, exclude=chosen, skills=skills)
            if pick is None:
                break
            chosen.append(pick)
        return tuple(sorted(chosen))

    def random_quirks(self, rng: random.Random) -> tuple:
        """Positive quirks only, unless the caller asks otherwise.

        `--kit-random-p` draws 1-3 of each polarity, because it is modelling a
        mid-campaign roster that a player did not choose. A comp is the
        opposite: it is a plan, and the Sanitarium lets a player lock the
        positives it wants and remove the negatives it does not. Letting the
        search pick which NEGATIVE quirks to carry optimises a variable the
        player does not control, and it flatters every result -- the search
        simply finds the mildest downsides and reports the score as the comp's.

        Fully-groomed is optimistic, but it is a state a player can actually
        buy, which "conveniently harmless flaws" is not. `--negative-quirks`
        restores the two-sided draw.
        """
        chosen = []
        blocked = set()
        polarities = (True, False) if self.negative_quirks else (True,)
        for positive in polarities:
            lo, hi = (self.quirk_slots if positive
                      else RANDOM_QUIRKS_PER_POLARITY)
            wanted = rng.randint(lo, hi)
            pool = self.quirk_pool[positive]
            if self.kit_prior_bias:
                # A weighted ORDER rather than a weighted pick: the loop below
                # walks the sequence taking the first compatible entries, so
                # biasing the order biases which quirks are reached first while
                # leaving every one of them reachable.
                quirk_counts, _t = self.kit_prior()
                weights = self._prior_weights(pool, quirk_counts)
                order, left, lw = [], list(pool), list(weights)
                while left:
                    pick = _weighted_pick(rng, left, lw)
                    idx = left.index(pick)
                    order.append(left.pop(idx))
                    lw.pop(idx)
            else:
                order = rng.sample(pool, len(pool))
            for qid in order:
                if wanted <= 0:
                    break
                if qid in blocked:
                    continue
                chosen.append(qid)
                blocked.add(qid)
                blocked |= self.incompatible_with(qid)
                wanted -= 1
        return tuple(sorted(chosen))

    def random_hero(self, rng: random.Random, hero_id: Optional[str] = None,
                    rank: Optional[int] = None,
                    size: int = PARTY_SIZE) -> HeroGene:
        if hero_id is None:
            # Draw from the classes that can FIGHT from this rank, so the
            # repair below is a safety net rather than the main mechanism.
            pool = self.classes_for_rank(rank, size) if rank else self.classes
            hero_id = self.draw_class(rng, pool or self.classes,
                                      size=size, bias=self.rarity_bias)
        skills = self.random_skills(rng, hero_id, rank=rank, size=size)
        return HeroGene(hero_class=hero_id,
                        skills=skills,
                        camp_skills=self.random_camp_skills(rng, hero_id),
                        trinkets=self.random_trinkets(rng, hero_id, skills=skills),
                        quirks=self.random_quirks(rng))

    def random_genome(self, rng: random.Random, size: int = PARTY_SIZE,
                      require: Optional[str] = None) -> Genome:
        genome = tuple(self.random_hero(rng, rank=i + 1, size=size)
                       for i in range(size))
        # Straight through `finish`, which is the single place every comp-level
        # invariant lives. It used to hand-roll a subset and skip the trinket
        # budget -- harmless while the ordinary draw could not produce a
        # one-off, and a duplicate-Ancestral generator the moment it could.
        return self.finish(rng, self.grant_one_off(rng, genome), require)

    def grant_one_off(self, rng: random.Random, genome: Genome) -> Genome:
        """Seed at least one one-off trinket, on a random hero.

        Those are the strongest trinkets in the game and a uniform draw reaches
        them rarely, so the search is given one to start from. It is a FLOOR,
        not a ceiling: since 2026-08-26 the only limit is the game's own per-id
        one, so a comp may legally field several DIFFERENT one-offs -- it just
        cannot field two copies of the same Ancestor's Candle.
        """
        if not UNIQUE_TRINKETS_PER_COMP or not genome:
            return genome
        slot = rng.randrange(len(genome))
        gene = genome[slot]
        pick = self.draw_trinket(rng, gene.hero_class, exclude=gene.trinkets, limited=True)
        if pick is None:
            return genome
        replaced = list(gene.trinkets)
        if replaced:
            replaced[rng.randrange(len(replaced))] = pick
        else:
            replaced = [pick]
        out = list(genome)
        out[slot] = replace(gene, trinkets=tuple(sorted(replaced)))
        return tuple(out)

    def count_one_offs(self, genome: Genome) -> int:
        return sum(1 for g in genome for t in g.trinkets
                   if t in self.store.trinkets and self.store.trinkets[t].limit)

    def duplicate_one_offs(self, genome: Genome) -> dict:
        """One-off trinket ids worn more times than the roster owns copies."""
        seen: dict = {}
        for gene in genome:
            for tid in gene.trinkets:
                seen[tid] = seen.get(tid, 0) + 1
        over = {}
        for tid, n in seen.items():
            trinket = self.store.trinkets.get(tid)
            if trinket and trinket.limit and n > trinket.limit:
                over[tid] = n - trinket.limit
        return over

    def enforce_trinket_budget(self, rng: random.Random, genome: Genome) -> Genome:
        """No trinket id worn more times than the roster owns copies of it.

        **This is the game's rule, per id** -- you own one Ancestor's Candle, so
        one hero wears it, and another may still wear Ancestor's Map. It used to
        cap the whole comp at ONE `limit`-bearing trinket, which was a
        misreading of Fran's first review and cost the space its strongest gear
        for four months. His correction, on being shown a comp holding three
        different Ancestrals: *"the problem is not 1 ancestral trinket per comp,
        rather several copies of the same ancestral trinket"*.

        Run after every operator, because crossover is exactly the move that
        breaks it: two parents each legally wearing Ancestor's Candle produce a
        child wearing two. A duplicate is REPLACED with a fresh draw rather than
        removed, so the hero keeps two trinkets.
        """
        used: dict = {}
        out = []
        for gene in genome:
            trinkets = []
            for tid in gene.trinkets:
                trinket = self.store.trinkets.get(tid)
                cap = trinket.limit if trinket else 0
                if not cap or used.get(tid, 0) < cap:
                    used[tid] = used.get(tid, 0) + 1
                    trinkets.append(tid)
                    continue
                swap = self.draw_trinket(rng, gene.hero_class, skills=gene.skills,
                                         exclude=trinkets + list(gene.trinkets)
                                         + [t for t, n in used.items()
                                            if (self.store.trinkets.get(t)
                                                and self.store.trinkets[t].limit
                                                and n >= self.store.trinkets[t].limit)])
                if swap is not None:
                    used[swap] = used.get(swap, 0) + 1
                    trinkets.append(swap)
            out.append(replace(gene, trinkets=tuple(sorted(trinkets))))
        return tuple(out)

    def finish(self, rng: random.Random, genome: Genome,
               require: Optional[str] = None) -> Genome:
        """Every comp-level invariant, in the one order that satisfies all of
        them: the pinned class first (it can add a whole hero), then the one-off
        budget, then the per-hero top-ups that the budget pass can leave short.
        """
        genome = self.enforce_ban(rng, genome)
        genome = self.enforce_required(rng, genome, require)
        # RANKS, THEN SKILLS, THEN TRINKETS -- Fran's build order, 2026-08-26,
        # and here it is a dependency rather than a preference: a hero in an
        # unfightable rank has no legal kit, and whether a trinket can fire is
        # decided by the rank it stands in and the skills it ended up with.
        genome = self.repair_ranks(rng, genome)
        genome = self.repair_dead_skills(rng, genome)
        genome = self.enforce_trinket_budget(rng, genome)
        genome = self.top_up(rng, genome)
        return self.repair_dead_trinkets(rng, genome)

    def enforce_ban(self, rng: random.Random, genome: Genome) -> Genome:
        """Replace any banned class, wherever it came from.

        Needed because the population is not built only by drawing: curated
        seeding, crossover and `conform` all import genes from outside the
        space, and a seeded comp fielding a banned class would otherwise
        survive the whole run.
        """
        if not self.banned:
            return genome
        size = len(genome)
        out = list(genome)
        for slot, gene in enumerate(genome):
            if gene.hero_class in self.banned:
                pool = self.classes_for_rank(slot + 1, size) or self.classes
                out[slot] = self.random_hero(rng, rng.choice(pool),
                                             rank=slot + 1, size=size)
        return tuple(out)

    def enforce_required(self, rng: random.Random, genome: Genome,
                         require: Optional[str]) -> Genome:
        """Repair, applied after every operator. Crossover and class mutation
        can both drop the pinned class, and a run asked for Duelist comps that
        quietly returns Crusader comps is worse than one that fails."""
        if not require or any(g.hero_class == require for g in genome):
            return genome
        slot = rng.randrange(len(genome))
        fresh = self.random_hero(rng, require, rank=slot + 1, size=len(genome))
        return genome[:slot] + (fresh,) + genome[slot + 1:]

    def conform(self, rng: random.Random, genome: Genome) -> Genome:
        """Pull a genome from OUTSIDE the search space back into it.

        Seeded curated comps need this and randomly generated ones do not: a
        curated hero can carry negative quirks (which the search does not
        choose -- see `random_quirks`), a camping skill their class is not
        granted by any file (42 such slots across the corpus), or a trinket
        outside the configured rarities. Left alone they would be genes that
        crossover can spread but no mutation can ever produce, and the emitted
        JSON could name a camping skill the real game would not let a player
        equip.

        Dropping is not enough on its own -- a hero left with two camping
        skills is a weaker hero for a reason that has nothing to do with the
        comp -- so what was dropped is topped back up from the legal pool.
        """
        out = []
        for gene in genome:
            gene = self.repair(gene)
            quirks = tuple(q for q in gene.quirks
                           if q in self.store.quirks
                           and (self.negative_quirks or self.store.quirks[q].is_positive))
            out.append(replace(gene, quirks=quirks or self.random_quirks(rng)))
        return self.enforce_trinket_budget(rng, self.top_up(rng, tuple(out)))

    def top_up(self, rng: random.Random, genome: Genome) -> Genome:
        """Refill every hero to two trinkets and four camping skills.

        The counts are not cosmetic. A hero holding one trinket is a slot the
        search forgot to fill, and a hero holding two camping skills is a
        weaker hero for a reason that has nothing to do with the comp -- both
        make a candidate look bad for the wrong cause, which is the one thing a
        fitness ranking must not do.

        Every path that can leave a hero short goes through here: a curated
        comp that only listed one trinket, `repair` dropping a gene the class
        cannot hold, and `enforce_trinket_budget` confiscating a second one-off.
        """
        out = []
        for gene in genome:
            camp_target = min(CAMP_SKILLS_PER_HERO, len(self.camp_pool[gene.hero_class]))
            missing = camp_target - len(gene.camp_skills)
            if missing > 0:
                spare = [c for c in self.camp_pool[gene.hero_class]
                         if c not in gene.camp_skills]
                gene = replace(gene, camp_skills=tuple(sorted(
                    gene.camp_skills + tuple(rng.sample(spare, min(missing, len(spare)))))))
            trinkets = list(gene.trinkets)
            while len(trinkets) < TRINKETS_PER_HERO:
                pick = self.draw_trinket(rng, gene.hero_class, exclude=trinkets,
                                         skills=gene.skills)
                if pick is None:
                    break
                trinkets.append(pick)
            out.append(replace(gene, trinkets=tuple(sorted(trinkets))))
        return tuple(out)

    def repair(self, gene: HeroGene) -> HeroGene:
        """Force a gene legal for its class. Only class mutation can produce an
        illegal one -- it keeps nothing from the old class -- but this is the one
        place that has to be true, so it is checked rather than reasoned about.
        """
        skills = tuple(s for s in gene.skills if s in self.skill_pool[gene.hero_class])
        if gene.hero_class in unrestricted(self.store):
            skills = ()
        camp = tuple(c for c in gene.camp_skills if c in self.camp_pool[gene.hero_class])
        trinkets = tuple(t for t in gene.trinkets if t in self.trinket_pool[gene.hero_class])
        return replace(gene, skills=skills, camp_skills=camp, trinkets=trinkets)

    # -- the JSON boundary -------------------------------------------------

    def to_json(self, genome: Genome, team_name: str, location: str,
                provenance: dict) -> dict:
        return {
            "teamName": team_name,
            "location": location,
            "heroes": [self._display.hero(g) for g in genome],
            "gaProvenance": provenance,
        }


class _DisplayNames:
    """id -> the name the curated JSON format writes.

    Built from the game's own English string tables, the same way
    `CompLoadoutLoader` builds the forward direction, so a file written here
    reads back through that loader unchanged. Camping skills are the one
    awkward case: localization stores them in ALL CAPS ("PLAY WITH FIRE") and
    the curated files use title case with small words intact ("Play with
    Fire"). Rather than invent a title-caser, the curated corpus is scanned for
    the spelling it already uses and only unseen skills fall back to `.title()`.
    """

    def __init__(self, store):
        game_dir = Path(GAME_DIR)
        # The 20 shipped rows first, then the game's OWN `hero_class_name_<id>`
        # for anything else. Without the second source a modded class was
        # emitted as `gene.hero_class.title()` -- "Sibyl_Ms" -- which resolves
        # through NOTHING when the comp is read back, so the loader dropped her
        # slot and every consumer silently scored a THREE-HERO party. That is
        # how `GA sibyl1 01` came back at 36.2 on a leaderboard while the search
        # that emitted it scored the same comp at 86.1.
        self.class_name = {v: k for k, v in HERO_DISPLAY_NAME_TO_ID.items()}
        for hero_id, disp in CompLoadoutLoader._load_english_entries(
                game_dir, "hero_class_name_").items():
            if hero_id in store.heroes:
                self.class_name.setdefault(hero_id, disp)
        skill_maps = CompLoadoutLoader._build_skill_name_maps(game_dir, set(store.heroes))
        self.skill_name = {hero_id: {sid: disp for disp, sid in m.items()}
                           for hero_id, m in skill_maps.items()}
        camp_upper = CompLoadoutLoader._build_camp_skill_name_map(game_dir)
        # Three sources, weakest first. Localization stores camping skills in
        # ALL CAPS, so a spelling has to come from somewhere: `.title()` is the
        # fallback and gets apostrophes wrong ("Lash'S Anger"); `heroes.js`
        # spells all 80 correctly; the curated corpus wins where it has an
        # opinion, so an emitted comp reads like the files next to it.
        self.camp_name = {sid: disp.title() for disp, sid in camp_upper.items()}
        for entry in canonical_order().values():
            for disp in entry.get("campSkills", []):
                sid = camp_upper.get(disp.upper())
                if sid:
                    self.camp_name[sid] = disp
        self.camp_name.update(_curated_camp_spellings(camp_upper))
        # Straight from localization, NOT by inverting the name->id maps.
        # Those maps are keyed by display name and ten trinket names are
        # claimed by two ids each, so inverting drops one id per collision and
        # `.get(tid, tid)` below then writes the raw ID into the comp file.
        # "Talisman of the Flame" is both `dd_trinket` and `_unlockdd_trinket`,
        # and that is exactly how `trinket1: "dd_trinket"` reached two emitted
        # GA comps -- a name no reader of this format can resolve.
        self.trinket_name = CompLoadoutLoader._load_english_entries(
            game_dir, "str_inventory_title_trinket")
        self.quirk_name = CompLoadoutLoader._load_english_entries(
            game_dir, "str_quirk_name_")
        # Canonical order resolved to IDS, not left as display names.
        #
        # `heroes.js` and the game's string table do not always spell a skill
        # the same way: the file says "First Aid" in the Duelist and Runaway
        # blocks and "Wound Care" in the nineteen vanilla ones, and both mean
        # `first_aid`. Name-matching therefore silently failed to place the
        # skill and dumped it at the end of the list, which is the one thing
        # this ordering exists to prevent.
        self.order = {}
        raw = canonical_order()
        for class_display, entry in raw.items():
            hero_id = HERO_DISPLAY_NAME_TO_ID.get(class_display)
            if not hero_id:
                continue
            forward = skill_maps.get(hero_id, {})
            self.order[hero_id] = {
                "skills": [forward[n] for n in entry.get("skills", []) if n in forward],
                "campSkills": [camp_upper[n.upper()] for n in entry.get("campSkills", [])
                               if n.upper() in camp_upper],
            }
        self.store = store

    def hero(self, gene: HeroGene) -> dict:
        skills = gene.skills or tuple(
            sorted(s.id for s in self.store.heroes[gene.hero_class].skills))
        names = self.skill_name.get(gene.hero_class, {})
        trinkets = list(gene.trinkets) + ["", ""]
        positive = [q for q in gene.quirks if self.store.quirks[q].is_positive]
        negative = [q for q in gene.quirks if not self.store.quirks[q].is_positive]
        class_name = self.class_name.get(gene.hero_class, gene.hero_class.title())
        # Sorted into the order the game lists them on the character sheet, so
        # a comp can be read against the screen. Presentation only -- the
        # engine resolves skills by id and a loadout is a set -- and done on
        # IDS before the names are looked up, so a spelling disagreement
        # between `heroes.js` and the string table cannot mis-sort anything.
        order = self.order.get(gene.hero_class, {})
        skill_names = [names.get(sid, sid)
                       for sid in _sorted_by(skills, order.get("skills", []))]
        camp_names = [self.camp_name.get(cid, cid)
                      for cid in _sorted_by(gene.camp_skills, order.get("campSkills", []))]
        return {
            "heroClass": class_name,
            "activeSkills": skill_names,
            "activeCampSkills": camp_names,
            "trinket1": self.trinket_name.get(trinkets[0], trinkets[0]),
            "trinket2": self.trinket_name.get(trinkets[1], trinkets[1]),
            "quirks": {"positive": [self.quirk_name.get(q, q) for q in positive],
                       "negative": [self.quirk_name.get(q, q) for q in negative]},
            "lockedQuirks": {"positive": [], "negative": []},
        }


def _curated_camp_spellings(camp_upper: dict) -> dict:
    """{skill_id: the title case the curated files actually use}."""
    out = {}
    for path in sorted(CURATED_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for hero in data.get("heroes", []):
            for name in hero.get("activeCampSkills", []):
                sid = camp_upper.get(str(name).upper())
                if sid:
                    out[sid] = str(name)
    return out


def genome_from_loadout(loadout, store) -> Optional[Genome]:
    """A curated comp as a genome, for seeding the population. Positional
    against `loadout.heroes`, never a dict keyed by hero id -- 37 of the
    curated comps field a duplicate class and a dict collapses them."""
    genes = []
    for h in loadout.heroes:
        if h.hero_id not in store.heroes:
            return None
        genes.append(HeroGene(
            hero_class=h.hero_id,
            skills=() if h.hero_id in unrestricted(store) else tuple(sorted(h.skill_ids)),
            camp_skills=tuple(sorted(h.camp_skill_ids)),
            trinkets=tuple(sorted(h.trinket_ids)),
            quirks=tuple(sorted(h.quirk_ids.get("positive", [])
                                + h.quirk_ids.get("negative", []))),
        ))
    return tuple(genes) if genes else None


# --------------------------------------------------------------------------
# Fitness
# --------------------------------------------------------------------------

@dataclass
class Score:
    success: float
    completion: float
    zero_death: float
    deaths: float
    stress: float
    combats_frac: float
    episodes: int
    # ((zone_id, run_score, episodes, success), ...) -- the headline is the
    # weighted mean over every zone, and a comp that carries it in one zone and
    # drowns in the other three is a different animal from one that is level.
    # Kept as a tuple of tuples so a Score stays hashable and pickles into a
    # worker unchanged.
    #
    # The RANKED quantity sits at index 1, because `emit._best_zone` picks the
    # comp's `location` tag off it: tagging the zone by one metric while
    # ordering the comps by another makes the label disagree with the ranking.
    # The success rate rides along at index 3 so `zone_line` can print both.
    by_zone: tuple = ()
    # Every episode's run score, pooled across zones. NOT a mean: `Score` is
    # built from one `RolloutResult` per zone, and a mean-of-means is wrong the
    # moment two zones run different episode counts. The spread is needed too,
    # and cannot be recovered from a mean.
    run_scores: tuple = ()
    hp_frac: float = 1.0
    quality: float = 0.0
    progress: float = 0.0

    @property
    def standard_error(self) -> float:
        """Standard error of SUCCESS. Binomial, and correct for a rate.

        Not the error bar on the ranking any more -- see `run_score_se`. Kept
        because SUCCESS is still reported, and reported rates need their own
        error.
        """
        if self.episodes <= 0:
            return 0.0
        p = self.success
        return (p * (1.0 - p) / self.episodes) ** 0.5

    @property
    def run_score(self) -> float:
        """The ranked quantity: how well the party came home, times how much of
        the expedition it actually did.

        This is Fran's stated objective (2026-08-24) read literally -- "complete
        an expedition with all heroes at 0 stress, without anyone dying" -- and
        all three clauses are load-bearing. QUALITY alone covers two of them and
        is exploitable as a fitness for exactly that reason: it is measured at
        episode end unconditionally, so a party that STALLS OUT at combat 2 of
        14 with everyone healthy scores ~0.90, above a party that fought all
        fourteen and came home bloodied. The progress factor is what closes it.
        """
        return statistics.fmean(self.run_scores) if self.run_scores else 0.0

    @property
    def run_score_se(self) -> float:
        """Sample SD / sqrt(n) -- NOT the binomial form.

        The run score is continuous on [0, 1], not a coin flip, so `p(1-p)/n`
        would overstate the error of a comp whose runs all land in one place.
        This is the interval every "inside the noise" test in this file must
        use, now that this is what the search ranks on.
        """
        if len(self.run_scores) < 2:
            return 0.0
        return statistics.stdev(self.run_scores) / (len(self.run_scores) ** 0.5)

    def fitness(self) -> float:
        shaping = (SHAPING_WEIGHTS["completion"] * self.completion
                   + SHAPING_WEIGHTS["zero_death"] * self.zero_death
                   + SHAPING_WEIGHTS["combats_frac"] * self.combats_frac
                   + SHAPING_WEIGHTS["stress"] * (1.0 - min(1.0, self.stress / STRESS_SCALE)))
        return self.run_score + _tiebreak_weight(self.episodes) * shaping

    def line(self) -> str:
        return (f"score {self.run_score * 100:5.1f} +-{self.run_score_se * 100:4.1f} | "
                f"qual {self.quality * 100:5.1f} | prog {self.progress * 100:5.1f}% | "
                f"succ {self.success * 100:5.1f}% | deaths {self.deaths:4.2f} | "
                f"stress {self.stress:5.1f} | hp {self.hp_frac * 100:4.0f}% | "
                f"fit {self.fitness():6.3f}")

    def zone_line(self) -> str:
        return "  ".join(f"{z} {r * 100:.0f}({s * 100:.0f}%)"
                         for z, r, _n, s in self.by_zone)

    def as_dict(self) -> dict:
        return {"runScore": round(self.run_score, 4),
                "runScoreSE": round(self.run_score_se, 4),
                "quality": round(self.quality, 4),
                "progress": round(self.progress, 4),
                "successRate": round(self.success, 4),
                "standardError": round(self.standard_error, 4),
                "completionRate": round(self.completion, 4),
                "zeroDeathRate": round(self.zero_death, 4),
                "meanDeaths": round(self.deaths, 3),
                "meanFinalStress": round(self.stress, 2),
                "meanFinalHpFrac": round(self.hp_frac, 4),
                "episodes": self.episodes,
                "byZone": {z: round(r, 4) for z, r, _n, _s in self.by_zone},
                "byZoneSuccess": {z: round(s, 4) for z, _r, _n, s in self.by_zone}}


@dataclass(frozen=True)
class Scenario:
    """What every candidate in a run is measured against. Held apart from the
    GA loop so `--self-check` and the final validation can reuse it with a
    different seed and nothing else changed."""
    zones: tuple
    tier: int
    band: str
    num_combats: int
    episodes_per_zone: int
    resolve_level: int


def _party_members(genome: Genome, scenario: Scenario):
    from bridge.rollout import PartyMember
    gear = max_gear_rank_for_resolve(scenario.resolve_level)
    return [PartyMember(hero_class=g.hero_class,
                        resolve_level=scenario.resolve_level,
                        weapon_rank=gear, armour_rank=gear,
                        skill_ids=tuple(g.skills),
                        camp_skills=tuple(g.camp_skills),
                        trinkets=tuple(g.trinkets),
                        quirks=tuple(g.quirks)) for g in genome]


def structural_key(genome: Genome) -> tuple:
    """What stage 1 is actually searching: the classes, IN RANK ORDER.

    Not `archetype()`, which is the class multiset and deliberately
    order-insensitive so two reorderings cannot both be emitted. Rank is half
    the decision here -- `CombatEngine.reset()` assigns `position = index` --
    so the structural search has to be able to tell a front-rank Leper from a
    back-rank one, and the cache key has to as well.
    """
    return tuple(g.hero_class for g in genome)


def _kit_rng(seed: int, episode: int) -> random.Random:
    """The kit draw for episode `k`, shared across candidates.

    Common random numbers, applied to the kit dimension the same way
    `Rollout.evaluate` applies them to the dungeon: every candidate's episode
    k draws from the same stream. It is a WEAKER pairing than the dungeon one
    and the difference matters -- two comps of different classes draw from
    different trinket pools, so the same stream does not hand them the same
    items, only the same sequence of decisions. Kit variance is reduced here,
    not eliminated the way dungeon variance is.
    """
    return random.Random((seed * 1_000_003) ^ (episode * 2_654_435_761))


def _episode_seed(seed: int, episode: int) -> int:
    """A distinct dungeon per structural episode, disjoint across seeds.

    `Rollout.evaluate` keys its scenario on the seed and derives its own
    per-episode seeds underneath, so a structural score cannot use one call
    for K episodes -- it needs K calls, each with its own kit. Multiplying by
    a stride keeps `--seed` and `--validate-seed` from ever colliding, which
    would put a tuned dungeon into the held-out measurement.
    """
    return seed * 1024 + episode


def rekit(space: "CompSpace", genome: Genome, rng: random.Random,
          require: Optional[str]) -> Genome:
    """Same classes and ranks, a freshly drawn legal kit.

    The draw is RANK-AWARE: a redrawn kit that a hero cannot perform from where
    it stands is not a kit, and stage 1 redraws one of these every episode.
    """
    fresh = tuple(space.random_hero(rng, g.hero_class, rank=i + 1,
                                    size=len(genome))
                  for i, g in enumerate(genome))
    return space.finish(rng, fresh, require)


# The `RolloutResult` attributes both aggregations average, named ONCE.
#
# `score_structure` and `score_genome` are two copies of one weighted mean over
# zones, and every time a metric was added to the Score it had to be added to
# both. Keeping the list here is what makes that impossible to half-do.
_WMEAN_ATTRS = ("success_rate", "completion_rate", "zero_death_rate",
                "mean_deaths", "mean_final_stress", "mean_combats_reached",
                "mean_quality", "mean_progress", "mean_hp_frac")


def _score_from(totals: dict, n: int, per_zone: dict, run_scores: list,
                scenario: Scenario) -> Score:
    """The one place a `Score` is assembled from pooled rollouts."""
    def wmean(attr):
        return totals.get(attr, 0.0) / max(1, n)

    by_zone = tuple((zone, acc["score"] / max(1, acc["n"]),
                     acc["n"], acc["succ"] / max(1, acc["n"]))
                    for zone, acc in per_zone.items())
    return Score(by_zone=by_zone,
                 run_scores=tuple(run_scores),
                 success=wmean("success_rate"), completion=wmean("completion_rate"),
                 zero_death=wmean("zero_death_rate"), deaths=wmean("mean_deaths"),
                 stress=wmean("mean_final_stress"),
                 quality=wmean("mean_quality"), progress=wmean("mean_progress"),
                 hp_frac=wmean("mean_hp_frac"),
                 combats_frac=wmean("mean_combats_reached") / max(1, scenario.num_combats),
                 episodes=n)


# Stress's share of the quality half of `run_score`, for the SEARCH only.
#
# Fran's call, 2026-08-26, after reviewing the first run: the emitted comps came
# home at 38.8 mean stress against 19.8 for his own top eight, level on HP, and
# the ranking could barely see it -- quality reads stress against the 200
# ceiling, so a 19-point stress gap is worth about 4.7 points of score. He chose
# "weight stress 2:1 over HP" over rescaling to the 100 Resolve Check, which
# keeps the shape of the metric and only changes what it cares about.
#
# `eval_expedition._run_quality` KEEPS the declared 50/50: the eval headline is
# what every archived number was measured on, and changing it would silently
# restate them all. This weight applies to the fitness and nowhere else.
SEARCH_STRESS_WEIGHT = 2.0 / 3.0


def _accumulate(acc_totals: dict, per_zone: dict, run_scores: list,
                zone: str, r) -> int:
    """Fold one zone's `RolloutResult` into the running pools."""
    acc = per_zone.setdefault(zone, {"score": 0.0, "succ": 0.0, "n": 0})
    # The SEARCH's weighting, not the eval's -- see `SEARCH_STRESS_WEIGHT`. Read
    # once here so the per-zone pool, the pooled sample and the headline can
    # never disagree about which quality they are made of.
    weighted = r.weighted_run_scores(SEARCH_STRESS_WEIGHT)
    acc["score"] += sum(weighted)
    acc["succ"] += r.success_rate * r.episodes
    acc["n"] += r.episodes
    run_scores.extend(weighted)
    for attr in _WMEAN_ATTRS:
        acc_totals[attr] = acc_totals.get(attr, 0.0) + getattr(r, attr) * r.episodes
    # The reported `qual` column must be the quality the score is MADE of, or
    # the printed line shows a product that does not multiply out.
    acc_totals["mean_quality"] = (acc_totals.get("mean_quality", 0.0)
                                  - r.mean_quality * r.episodes
                                  + sum(r.weighted_qualities(SEARCH_STRESS_WEIGHT)))
    return r.episodes


def score_structure(rollout, kitted: Sequence, scenario: Scenario, seed: int) -> Score:
    """Stage 1's fitness: one structure, K kits, K dungeons.

    `kitted` is the same classes and ranks re-kitted once per episode, drawn in
    the PARENT so a worker needs no `CompSpace` -- the worker pool is
    initialised with a `Rollout` and nothing else, and threading the whole
    trinket-space construction through it to redraw items it could have been
    handed is work for no gain.
    """
    per_zone: dict = {}
    totals: dict = {}
    run_scores: list = []
    n = 0
    for ep, genome in enumerate(kitted):
        party = _party_members(genome, scenario)
        ep_seed = _episode_seed(seed, ep)
        for zone in scenario.zones:
            r = rollout.evaluate(party, zone_id=zone, tier=scenario.tier,
                                 num_combats=scenario.num_combats,
                                 length_band=scenario.band,
                                 episodes=1, seed=ep_seed)
            n += _accumulate(totals, per_zone, run_scores, zone, r)
    return _score_from(totals, n, per_zone, run_scores, scenario)


def score_genome(rollout, genome: Genome, scenario: Scenario, seed: int) -> Score:
    """One candidate, over every zone in the scenario, at one seed.

    The seed reaches `Rollout.evaluate`'s scenario key and the party does not,
    so every candidate scored at this seed faces the identical dungeon list.
    That pairing is the only reason a 24-candidate ranking off 12 episodes each
    means anything at all.
    """
    party = _party_members(genome, scenario)
    per_zone: dict = {}
    totals: dict = {}
    run_scores: list = []
    n = 0
    for zone in scenario.zones:
        r = rollout.evaluate(party, zone_id=zone, tier=scenario.tier,
                             num_combats=scenario.num_combats,
                             length_band=scenario.band,
                             episodes=scenario.episodes_per_zone,
                             seed=seed)
        n += _accumulate(totals, per_zone, run_scores, zone, r)
    return _score_from(totals, n, per_zone, run_scores, scenario)


# -- worker-pool plumbing ---------------------------------------------------
# One Rollout per process, built once in the initializer: construction parses
# the whole game install and loads the checkpoint (~2 s), which is per worker
# and not per candidate. `quiet_torch()` inside Rollout pins one inference
# thread, which is what makes N processes actually N times faster instead of
# N copies of an OpenMP pool fighting over the same cores.

_WORKER = {}


def _worker_init(model_path: str) -> None:
    from bridge.rollout import Rollout
    _WORKER["rollout"] = Rollout(model_path)


def _worker_score(args):
    genome, scenario, seed = args
    return genome_key(genome), score_genome(_WORKER["rollout"], genome, scenario, seed)


def _worker_score_structure(args):
    kitted, scenario, seed = args
    return None, score_structure(_WORKER["rollout"], kitted, scenario, seed)


class Evaluator:
    """Scores populations, in-process or across a worker pool, with a cache
    keyed on (genome, seed) -- an elite that survives a generation at an
    unchanged seed is free, and duplicates inside a generation are scored once.

    **With `workers > 1` the caller MUST be under `if __name__ == "__main__"`.**
    Windows has no `fork`, so every worker re-imports the calling module, and a
    script that builds an Evaluator at import time spawns workers that spawn
    workers. Python catches it and raises, but the traceback names
    `_check_not_importing_main` rather than anything here.
    """

    def __init__(self, model_path: str, workers: int = 1):
        self.model_path = model_path
        self.workers = max(1, workers)
        self._cache = {}
        self._pool = None
        self._rollout = None
        if self.workers == 1:
            from bridge.rollout import Rollout
            self._rollout = Rollout(model_path)

    def local_rollout(self):
        """An in-process Rollout, built on demand.

        `--workers > 1` keeps no local one, but recording which skills the
        policy played means hooking the env it played in -- which a worker
        process does not share. Only `--explain` needs this; scoring stays in
        the pool.
        """
        if self._rollout is None:
            from bridge.rollout import Rollout
            self._rollout = Rollout(self.model_path)
        return self._rollout

    def _ensure_pool(self):
        if self._pool is None:
            from concurrent.futures import ProcessPoolExecutor
            self._pool = ProcessPoolExecutor(max_workers=self.workers,
                                             initializer=_worker_init,
                                             initargs=(self.model_path,))
        return self._pool

    def score_structures(self, kits: Sequence, scenario: Scenario, seed: int) -> list:
        """Stage 1. `kits` is one list of re-kitted genomes per candidate.

        Keyed on `structural_key` and NOT on the genome: the kit is a function
        of `(seed, episode)` and is deliberately not part of what is being
        measured, so two candidates with the same classes and ranks are the
        same candidate here even when their carried kits differ.
        """
        want, wanted_keys, order = [], set(), []
        for kitted in kits:
            ck = (structural_key(kitted[0]), seed, scenario, len(kitted))
            order.append(ck)
            if ck not in self._cache and ck not in wanted_keys:
                wanted_keys.add(ck)
                want.append((ck, kitted))
        if want:
            if self.workers == 1:
                for ck, kitted in want:
                    self._cache[ck] = score_structure(self._rollout, kitted,
                                                      scenario, seed)
            else:
                pool = self._ensure_pool()
                jobs = [(k, scenario, seed) for _ck, k in want]
                for (ck, _k), (_key, score) in zip(want,
                                                   pool.map(_worker_score_structure, jobs)):
                    self._cache[ck] = score
        return [self._cache[ck] for ck in order]

    def score_many(self, genomes: Sequence, scenario: Scenario, seed: int) -> list:
        # The SCENARIO is part of the key, not just the seed. It was not, and
        # that silently returned a 20-episode score for a 40-episode request:
        # the polish phase tunes on a half-episode scenario and then re-measures
        # on the full one at the same seed, so every one of those re-measurements
        # was reading back the tuning number it was supposed to check.
        want = []
        wanted_keys = set()
        order = []
        for g in genomes:
            ck = (genome_key(g), seed, scenario)
            order.append(ck)
            if ck not in self._cache and ck not in wanted_keys:
                wanted_keys.add(ck)
                want.append((ck, g))
        if want:
            if self.workers == 1:
                for ck, g in want:
                    self._cache[ck] = score_genome(self._rollout, g, scenario, seed)
            else:
                pool = self._ensure_pool()
                jobs = [(g, scenario, seed) for _ck, g in want]
                for (ck, _g), (_key, score) in zip(want, pool.map(_worker_score, jobs)):
                    self._cache[ck] = score
        return [self._cache[ck] for ck in order]

    def close(self):
        if self._pool is not None:
            self._pool.shutdown()
            self._pool = None


# --------------------------------------------------------------------------
# The genetic operators
# --------------------------------------------------------------------------

MUTATIONS = ("skill", "trinket", "trinket_pair", "trinket_set", "one_off",
             "camp", "quirk", "position", "class")
# Weights, and a convention. Trinkets carry 0.40 across their three operators,
# up from 0.22 on one, because Fran's read of the first run was that the search
# "is not exploiting powerful trinket+loadout combinations" -- and it could not
# have been: one swap at a time out of 98 unlimited choices, over ten
# generations, barely samples the space at all. `trinket_pair` exists because a
# combination is two trinkets that are good TOGETHER, which single swaps reach
# only by passing through the state where one of them is wrong.
# `trinket_set` takes its 0.06 from `trinket` and `trinket_pair`, which is
# where the redundant draws were: a hero has ~154 wearable trinkets and exactly
# one set, so the set has to be offered deliberately or not at all.
MUTATION_WEIGHTS = (0.22, 0.15, 0.11, 0.06, 0.08, 0.06, 0.08, 0.14, 0.10)


# Stage 1 searches structure only, so the kit operators are switched off:
# the fitness redraws the kit every episode, and a mutation the fitness is
# about to overwrite spends a generation to change nothing.
STRUCTURAL_MUTATIONS = ("position", "class")
STRUCTURAL_MUTATION_WEIGHTS = (0.45, 0.55)


def mutate(space: CompSpace, rng: random.Random, genome: Genome,
           require: Optional[str], structural: bool = False) -> Genome:
    if structural:
        kind = rng.choices(STRUCTURAL_MUTATIONS,
                           weights=STRUCTURAL_MUTATION_WEIGHTS, k=1)[0]
    else:
        kind = rng.choices(MUTATIONS, weights=MUTATION_WEIGHTS, k=1)[0]
    slot = rng.randrange(len(genome))
    gene = genome[slot]

    if kind == "position" and len(genome) > 1:
        a, b = rng.sample(range(len(genome)), 2)
        out = list(genome)
        out[a], out[b] = out[b], out[a]
        return tuple(out)

    if kind == "one_off":
        # Move the comp's single one-off to a different hero, or to a different
        # trinket. It is the strongest item on the board and WHICH hero holds it
        # is a real decision -- Ancestor's Candle on the Occultist is a
        # different comp from the same candle on the Crusader.
        stripped = tuple(
            replace(g, trinkets=tuple(t for t in g.trinkets
                                      if not (t in space.store.trinkets
                                              and space.store.trinkets[t].limit)))
            for g in genome)
        return space.finish(rng, space.grant_one_off(rng, space.top_up(rng, stripped)),
                            require)

    if kind == "class":
        # Never mutate away the LAST holder of a pinned class -- the repair
        # would only put it back in some other slot, wasting the generation.
        pinned = (require and gene.hero_class == require
                  and sum(1 for g in genome if g.hero_class == require) == 1)
        if not pinned:
            # The gated draw. `current` hands `draw_class` the party and the
            # slot, so a rarity-biased substitution is refused when it would
            # lower the party's `rank_fit` -- the anti-loop Fran asked for.
            slot = next((i for i, g in enumerate(genome) if g is gene), None)
            pick = space.draw_class(
                rng, space.classes, size=len(genome), bias=space.rarity_bias,
                current=None if slot is None else {"genome": genome, "slot": slot})
            gene = space.random_hero(rng, pick)
    elif kind == "skill":
        pool = [s for s in space.skill_pool[gene.hero_class] if s not in gene.skills]
        if gene.skills and pool:
            out = list(gene.skills)
            out[rng.randrange(len(out))] = rng.choice(pool)
            gene = replace(gene, skills=tuple(sorted(out)))
    elif kind == "trinket":
        pick = space.draw_trinket(rng, gene.hero_class, exclude=gene.trinkets,
                                  skills=gene.skills)
        if gene.trinkets and pick is not None:
            out = list(gene.trinkets)
            out[rng.randrange(len(out))] = pick
            gene = replace(gene, trinkets=tuple(sorted(out)))
    elif kind == "trinket_pair":
        gene = replace(gene, trinkets=space.random_trinkets(rng, gene.hero_class,
                                                            skills=gene.skills))
    elif kind == "trinket_set":
        # Both halves at once, or nothing. A set is worth having only complete,
        # and a hill-climb cannot walk to it one trinket at a time: the state
        # where a hero wears half a set is strictly worse than wearing two good
        # unrelated trinkets, so every path to it goes downhill first. Exactly
        # the argument `trinket_pair` was added on, one level up.
        worn = space.trinket_set(gene.hero_class)
        if worn:
            gene = replace(gene, trinkets=worn)
    elif kind == "camp":
        pool = [c for c in space.camp_pool[gene.hero_class] if c not in gene.camp_skills]
        if gene.camp_skills and pool:
            out = list(gene.camp_skills)
            out[rng.randrange(len(out))] = rng.choice(pool)
            gene = replace(gene, camp_skills=tuple(sorted(out)))
    elif kind == "quirk":
        gene = replace(gene, quirks=space.random_quirks(rng))

    out = list(genome)
    out[slot] = space.repair(gene)
    return space.finish(rng, tuple(out), require)


def crossover(space: CompSpace, rng: random.Random, a: Genome, b: Genome,
              require: Optional[str]) -> Genome:
    """Uniform over slots, and the slot INDEX is inherited with the gene.

    A hero's rank is most of what a comp is -- `CombatEngine.reset()` assigns
    `position = index` -- so recombining two parties by taking slot 1 from one
    and slot 2 from the other preserves the thing under selection. Splicing a
    back-rank Arbalest into a front rank would be a new random comp wearing a
    parent's name.
    """
    n = min(len(a), len(b))
    child = tuple(a[i] if rng.random() < 0.5 else b[i] for i in range(n))
    # `finish`, not just `enforce_required`: crossover is exactly the operator
    # that breaks the comp-wide one-off budget, since two parents each legally
    # holding one produce a child holding two.
    return space.finish(rng, child, require)


def tournament(rng: random.Random, population: Sequence, fits: Sequence, k: int) -> Genome:
    idx = rng.sample(range(len(population)), min(k, len(population)))
    return population[max(idx, key=lambda i: fits[i])]


# --------------------------------------------------------------------------
# The run
# --------------------------------------------------------------------------

def archetype(genome):
    """The comp's four PILLARS: its class multiset, order-insensitive.

    Fran's word, and his rule -- "having the same 4 pillars is practically
    making the same comp". Two emitted comps must never share one. Note what
    this deliberately does NOT distinguish: two orderings of the same four
    classes are the same archetype, so the search stays free to explore
    orderings (it should -- a Duelist plays differently from rank 1 than from
    rank 3) while the emitter only ever keeps the best ordering it found.
    """
    return tuple(sorted(g.hero_class for g in genome))


def seed_population(space: CompSpace, store, rng: random.Random, size: int,
                    require: Optional[str], party_size: int,
                    curated_fraction: float, max_per_archetype: int,
                    max_class_share: float = 1.0) -> list:
    """A minority of curated comps, then random fill, capped per archetype.

    The first version seeded HALF the population from curated comps and the
    output showed it: five near-clones of the sample comp. Fran's correction is
    the design rule now -- he gathered a sample comp so the model would learn to
    USE the class, not to copy the comp, and five different funky comps teach
    more than five pseudo-clones. So curated comps are a quarter of the
    population: a starting point, not the search space.

    The archetype cap is what keeps the random half diverse too. Without it a
    strong class simply appears in most candidates and the population collapses
    onto one pillar set within a few generations.
    """
    seeds = []
    for name in sorted(store.comp_loadouts):
        genome = genome_from_loadout(store.comp_loadouts[name], store)
        if not genome or len(genome) != party_size:
            continue
        if require and not any(g.hero_class == require for g in genome):
            continue
        seeds.append(genome)
    rng.shuffle(seeds)
    wanted = int(size * curated_fraction)
    # A seed fielding a banned class is dropped rather than repaired: repairing
    # it keeps three quarters of a comp the search was told not to explore.
    if space.banned:
        seeds = [g for g in seeds
                 if not any(x.hero_class in space.banned for x in g)]
    population = [space.finish(rng, space.conform(rng, g), require)
                  for g in seeds[:wanted]]
    while len(population) < size:
        population.append(space.random_genome(rng, party_size, require))
    population = enforce_archetype_cap(space, rng, population, require,
                                       max_per_archetype, party_size)
    return enforce_class_share(space, rng, population, require, max_class_share,
                               party_size)


def enforce_archetype_cap(space: CompSpace, rng: random.Random, population: list,
                          require: Optional[str], cap: int, party_size: int) -> list:
    """Replace individuals beyond `cap` sharing one archetype with fresh random
    comps.

    Order matters: the list is walked front to back, so a caller that has
    sorted by fitness keeps its best of each archetype and spends the
    replacements on the duplicates below.
    """
    if cap <= 0:
        return population
    seen = {}
    out = []
    for genome in population:
        key = archetype(genome)
        if seen.get(key, 0) >= cap:
            for _ in range(24):
                candidate = space.random_genome(rng, party_size, require)
                if seen.get(archetype(candidate), 0) < cap:
                    genome = candidate
                    break
            else:
                genome = space.random_genome(rng, party_size, require)
            key = archetype(genome)
        seen[key] = seen.get(key, 0) + 1
        out.append(genome)
    return out


def enforce_class_share(space: CompSpace, rng: random.Random, population: list,
                        require: Optional[str], share: float, party_size: int) -> list:
    """No class may appear in more than `share` of the population.

    The archetype cap keeps class MULTISETS apart and that was not enough: six
    strong classes simply recombined into thirty different foursomes, and one
    run of six emitted comps used six of the twenty classes. Fran's read --
    "it has only used 6 from the 20 classes between all comps" -- is a
    diversity failure one level below the archetype.

    Walked front to back, so a caller that has sorted by fitness keeps its best
    users of a class and spends the replacements on the rest. The pinned class
    is exempt: `--require-class duelist` means every comp holds a Duelist, and
    capping that would fight the flag.
    """
    if share >= 1.0 or not population:
        return population
    cap = max(1, int(len(population) * share))
    counts: dict = {}
    out = []
    for genome in population:
        classes = {g.hero_class for g in genome}
        over = [c for c in classes
                if c != require and counts.get(c, 0) >= cap]
        if over:
            for _ in range(24):
                candidate = space.random_genome(rng, party_size, require)
                fresh = {g.hero_class for g in candidate}
                if not any(c != require and counts.get(c, 0) >= cap for c in fresh):
                    genome = candidate
                    break
            classes = {g.hero_class for g in genome}
        for c in classes:
            counts[c] = counts.get(c, 0) + 1
        out.append(genome)
    return out


def describe(genome) -> str:
    return " / ".join(g.hero_class for g in genome)


def polish_order(evaluator: "Evaluator", space: CompSpace, rng: random.Random,
                 genome, scenario: Scenario, seed: int, shortlist: int = 3):
    """Try EVERY ordering of a finished comp and return the best few.

    Rank is not a detail of a comp, it is most of what a comp is --
    `CombatEngine.reset()` assigns `position = index`, and which rank a hero
    stands in decides what they can reach, what can reach them, and half their
    skills' legality. Fran asked for exactly this: try the pinned class in other
    positions, and swap positions to see if that works best for the same comp.

    A four-hero party has 24 orderings, which is small enough to search
    EXHAUSTIVELY rather than sample -- unusual in this tool and worth doing
    where it is possible, since the position mutation only ever samples it. The
    pillars do not change, so every ordering is the same archetype and the
    emitter still writes one file.

    Returns a shortlist rather than a winner because this runs on the tuning
    seed; the caller re-measures on the held-out one.
    """
    # ONLY orderings every hero can fight from, and each one re-finished.
    #
    # This permuted raw and skipped `finish`, which quietly undid the rank
    # constraints one stage earlier -- the 2026-08-26 Weald run emitted a
    # Highwayman in rank 4 (viable 1-3) carrying two unperformable skills, in
    # its TOP comp. Rank is an input to both of the other repairs: moving a
    # hero changes which of its skills launch and which of its trinkets fire,
    # so an ordering that is not re-finished is not the comp it claims to be.
    size = len(genome)
    fightable = [tuple(genome[i] for i in perm)
                 for perm in itertools.permutations(range(size))
                 if all(slot + 1 in space.viable_ranks(gene.hero_class, size)
                        for slot, gene in enumerate(genome[i] for i in perm))]
    # Fall back to the whole set only if the comp has no fightable ordering at
    # all, which `repair_ranks` should have made impossible upstream.
    orderings, seen = [], set()
    for candidate in (fightable or [tuple(genome[i] for i in perm)
                                    for perm in itertools.permutations(range(size))]):
        finished = space.finish(rng, candidate)
        key = tuple((g.hero_class, g.skills, g.trinkets) for g in finished)
        if key in seen:
            continue
        seen.add(key)
        orderings.append(finished)
    scores = evaluator.score_many(orderings, scenario, seed)
    ranked = sorted(range(len(orderings)), key=lambda i: scores[i].fitness(), reverse=True)
    return [orderings[i] for i in ranked[:shortlist]]


# How much better a skill swap has to look before the hill-climb takes it,
# in standard errors of the comparison. One sigma is the conventional "it is
# probably real"; the point here is only to refuse steps that are indisputably
# noise, since the held-out re-measurement downstream is what actually decides
# whether the polish is kept.
SKILL_STEP_SIGMAS = 1.0


def polish_skills(evaluator: "Evaluator", space: CompSpace, rng: random.Random,
                  genome, scenario: Scenario, seed: int, rounds: int):
    """Hill-climb one comp's SKILLS, holding classes, ranks and trinkets fixed.

    This stage did not exist until 2026-08-26, and its absence was the single
    biggest defect in the search. The pipeline was:

      stage 1  classes and ranks, kit REDRAWN every episode (skills averaged out)
      stage 2  best of `--restage-kits` (6) whole-party random kits
               -> all 24 orderings, exhaustively
               -> `--polish` rounds of trinket hill-climbing

    So skills were chosen once, by keeping the best of six random draws out of
    ~35^4 = 1.5 million combinations, and never refined again, while trinkets
    got dedicated hill-climbing. Fran diagnosed the inversion from the output
    alone -- "it feels the model chose the trinkets first before having the
    skill loadout and base comp ready" -- and named the order it should be:
    **comp and ranks, then skills, then trinkets and quirks.** That is now the
    order, here and in `CompSpace.finish`.

    Every candidate is one legal single swap, and `finish` keeps it rank-legal,
    so a swap can never introduce a skill the hero cannot perform from where it
    stands. Paired like every other stage: same dungeon list, same seed, so a
    delta is between two loadouts rather than between two draws.
    """
    best = genome
    best_score = evaluator.score_many([best], scenario, seed)[0]
    for _ in range(rounds):
        candidates = []
        for slot, gene in enumerate(best):
            if gene.hero_class in unrestricted(space.store):
                # All seven are equipped and a MODE picks; there is no 4-of-7.
                continue
            rank = slot + 1
            spare = [x for x in space.usable_from(gene.hero_class, rank)
                     if x not in gene.skills]
            for out_id in gene.skills:
                for in_id in spare:
                    kit = tuple(sorted(
                        [x for x in gene.skills if x != out_id] + [in_id]))
                    mutated = list(best)
                    # Trinkets are NOT redrawn: this stage answers "which four
                    # skills", and letting the gear move underneath it would
                    # make every delta a two-variable comparison.
                    mutated[slot] = replace(gene, skills=kit)
                    candidates.append(space.finish(rng, tuple(mutated)))
        if not candidates:
            break
        scores = evaluator.score_many(candidates, scenario, seed)
        top = max(range(len(candidates)), key=lambda i: scores[i].fitness())
        # A MARGIN, not a bare improvement -- and this stage needs it more than
        # any other. Measured 2026-08-26 on the shipped checkpoint: a skill swap
        # is worth 1-3pp against a standard error of ~1.3pp at 64 episodes, and
        # region-pinning did not change that (sigma 1.3 in the Weald alone, so
        # the hypothesis that averaging over four regions was hiding the signal
        # is NOT supported). Taking the max of ~48 candidates that are all
        # inside the noise picks about two sigma of upward noise every round --
        # the winner's curse, and it is what would make this stage actively
        # harmful rather than merely weak.
        margin = SKILL_STEP_SIGMAS * max(best_score.run_score_se,
                                         scores[top].run_score_se)
        if scores[top].run_score <= best_score.run_score + margin:
            break
        best, best_score = candidates[top], scores[top]
    return best, best_score


def polish_trinkets(evaluator: "Evaluator", space: CompSpace, rng: random.Random,
                    genome, scenario: Scenario, seed: int, rounds: int):
    """Hill-climb one finished comp's trinkets, holding its pillars fixed.

    The GA searches classes, ranks, skills and trinkets at once, and trinkets
    are the widest of those axes by an order of magnitude -- ~98 unlimited
    choices per hero against 35 skill loadouts and 4 ranks. The population
    settles on a shape long before it has looked at more than a handful of
    trinket pairs, which is exactly the complaint the first run drew: the comps
    were shaped right and geared arbitrarily.

    Sound only because of the pairing -- every candidate in a round is scored
    on the same dungeon list at the same seed as the incumbent, so a difference
    is between two kits rather than between two draws. It changes nothing but
    trinkets, so the comp's identity, which is the part Fran reviews, survives
    it. It stops the moment a round fails to improve.
    """
    best = genome
    best_score = evaluator.score_many([best], scenario, seed)[0]
    for _ in range(rounds):
        candidates = []
        for slot in range(len(best)):
            gene = best[slot]
            for _ in range(2):
                mutated = list(best)
                mutated[slot] = replace(
                    gene, trinkets=space.random_trinkets(rng, gene.hero_class,
                                                         skills=gene.skills))
                candidates.append(space.finish(rng, tuple(mutated)))
            # The class's SET, offered explicitly once per hero per round. A
            # random pair reaches it about 1 in 11,800 times, so the polish
            # phase -- two draws per hero per round -- would never see it.
            worn = space.trinket_set(gene.hero_class)
            if worn and tuple(sorted(gene.trinkets)) != worn:
                mutated = list(best)
                mutated[slot] = replace(gene, trinkets=worn)
                candidates.append(space.finish(rng, tuple(mutated)))
        scores = evaluator.score_many(candidates, scenario, seed)
        top = max(range(len(candidates)), key=lambda i: scores[i].fitness())
        if scores[top].fitness() <= best_score.fitness():
            break
        best, best_score = candidates[top], scores[top]
    return best, best_score


def run_ga(args, store, evaluator: "Evaluator", scenario: Scenario) -> list:
    space = CompSpace(store, args.trinket_rarities, args.negative_quirks,
                      args.class_trinket_bias, args.synergy_bias,
                      banned=args.ban_class,
                      rarity_bias=getattr(args, "rarity_bias", 0.0),
                      quirk_slots=(RANDOM_QUIRKS_PER_POLARITY if getattr(args, "sparse_quirks", False) else None),
                      kit_prior_bias=getattr(args, "kit_prior_bias", 0.0))
    rng = random.Random(args.seed)
    population = seed_population(space, store, rng, args.population,
                                 args.require_class, args.party_size,
                                 args.curated_fraction, args.max_per_archetype,
                                 args.max_class_share)
    hall = {}

    structural = bool(args.structure_first)
    if structural:
        print(f"stage 1: STRUCTURE ONLY -- classes and ranks, kit redrawn every "
              f"episode ({scenario.episodes_per_zone} per zone). Stage 2 polishes "
              f"the kit on the winners.", flush=True)

    for gen in range(args.generations):
        # The seed rotates unless pinned: a candidate that only wins on one
        # dungeon list is found out next generation, and an elite carried
        # forward is RE-SCORED rather than keeping a lucky number.
        seed = args.seed if args.fixed_seed else args.seed + gen
        if structural:
            kits = [[rekit(space, g, _kit_rng(seed, ep), args.require_class)
                     for ep in range(scenario.episodes_per_zone)]
                    for g in population]
            scores = evaluator.score_structures(kits, scenario, seed)
        else:
            scores = evaluator.score_many(population, scenario, seed)
        # The duplication cost is subtracted HERE rather than inside
        # `Result.fitness()` because a Result does not know its genome, and
        # this is the one place the two are side by side. It therefore shapes
        # what survives into the next generation, which is the whole
        # difference from the emit-time preference.
        fits = [s.fitness() - duplication_cost(population[i], s.episodes,
                                               args.duplicate_cost)
                for i, s in enumerate(scores)]
        ranked = sorted(range(len(population)), key=lambda i: fits[i], reverse=True)

        best = ranked[0]
        distinct = len({archetype(g) for g in population})
        print(f"gen {gen:2d} seed {seed:5d} | best {scores[best].line()} | "
              f"{distinct:2d} archetypes | {describe(population[best])}", flush=True)
        # The hall keeps the BEST OF EACH ARCHETYPE rather than the best
        # overall, so one strong pillar set cannot crowd out every rival and
        # leave the emitter with nothing distinct to choose between.
        for i in ranked[:args.elites]:
            # In structural mode the RANKS are half of what is being searched,
            # so the hall keys on the ordered classes -- keying on the
            # order-insensitive `archetype` there would silently discard every
            # reordering the `position` operator found. Emission still dedups
            # on `archetype`, so only one of them can reach a file.
            key = structural_key(population[i]) if structural else archetype(population[i])
            if key not in hall or fits[i] > hall[key][1]:
                hall[key] = (population[i], fits[i])

        if gen == args.generations - 1:
            break
        nxt = [population[i] for i in ranked[:args.elites]]
        while len(nxt) < args.population:
            child = crossover(space, rng,
                              tournament(rng, population, fits, args.tournament),
                              tournament(rng, population, fits, args.tournament),
                              args.require_class)
            while rng.random() < args.mutation_rate:
                child = mutate(space, rng, child, args.require_class, structural)
            nxt.append(child)
        population = enforce_archetype_cap(space, rng, nxt, args.require_class,
                                           args.max_per_archetype, args.party_size)
        population = enforce_class_share(space, rng, population, args.require_class,
                                         args.max_class_share, args.party_size)

    # Held-out validation. The generation scores were paid for with a handful of
    # episodes each and selection has been fitting to them; the number that goes
    # into a comp's provenance must not be one the search optimised.
    finalists = [g for g, _f in hall.values()]
    if structural:
        # Hand stage 2 a kit worth polishing rather than the arbitrary one the
        # structural genome happened to carry. `--restage-kits` draws are scored
        # on the TUNING seed, never on `validate_seed`: picking a kit on the
        # held-out seed and then reporting that seed is the same overfit the
        # held-out validation exists to prevent.
        pick_seed = args.validate_seed + 1
        pick = replace(scenario, episodes_per_zone=max(4, args.validate_episodes // 2))
        print(f"\nstage 2: drawing {args.restage_kits} kits for each of "
              f"{len(finalists)} structures on seed {pick_seed}", flush=True)
        restaged = []
        for g in finalists:
            # zlib.crc32, never hash(): Python randomises str hashing per
            # process unless PYTHONHASHSEED is pinned, so a tuple-hash seed
            # would draw different kits on every run of the same command.
            cands = [rekit(space, g,
                           random.Random(zlib.crc32(
                               f"{args.seed}|{'/'.join(structural_key(g))}|{k}".encode())),
                           args.require_class)
                     for k in range(max(1, args.restage_kits))]
            picks = evaluator.score_many(cands, pick, pick_seed)
            restaged.append(cands[max(range(len(cands)),
                                      key=lambda i: picks[i].fitness())])
        finalists = restaged
    val = replace(scenario, episodes_per_zone=args.validate_episodes)
    print(f"\nvalidating {len(finalists)} distinct archetypes on held-out seed "
          f"{args.validate_seed} at {args.validate_episodes} episodes/zone", flush=True)
    val_scores = evaluator.score_many(finalists, val, args.validate_seed)
    pairs = sorted(zip(finalists, val_scores), key=lambda p: p[1].fitness(), reverse=True)

    if args.polish:
        # Distinct archetypes only, and as many as will be emitted: polishing a
        # comp that loses the dedup is time spent on a file nobody will read.
        keep, seen = [], set()
        for genome, score in pairs:
            key = archetype(genome)
            if key in seen:
                continue
            seen.add(key)
            keep.append((genome, score))
            if len(keep) >= args.emit:
                break

        # THREE seeds, not two, and the third is the whole point. Polishing
        # SELECTS on its seed, so a comp polished and then reported on the same
        # dungeon list carries a number it was fitted to -- the exact mistake
        # the held-out validation exists to avoid, reintroduced one step later.
        # So: tune on `polish_seed`, then re-measure on the untouched
        # `validate_seed` and report THAT, even when it is worse.
        polish_seed = args.validate_seed + 1
        tune = replace(val, episodes_per_zone=max(8, args.validate_episodes // 2))
        print(f"polishing the top {len(keep)} archetypes on tuning seed {polish_seed}: "
              f"all {math.factorial(args.party_size)} orderings, then "
              f"{args.polish_skills} rounds of skills, then {args.polish} "
              f"rounds of trinkets", flush=True)
        polished = []
        for genome, score in keep:
            # ORDER FIRST, then gear. Which rank a hero stands in decides what
            # their trinkets should be doing, so tuning the kit around an
            # ordering the comp is about to abandon is wasted.
            candidates = []
            for ordering in polish_order(evaluator, space, rng, genome, tune,
                                         polish_seed):
                # RANKS -> SKILLS -> TRINKETS. Which rank a hero stands in
                # decides which skills it can even perform, and which skills it
                # performs decide what its trinkets should be amplifying, so
                # any other order tunes a layer against one that is about to
                # move underneath it.
                kitted, _ = polish_skills(evaluator, space, rng, ordering, val,
                                          polish_seed, args.polish_skills)
                better, _tuned = polish_trinkets(evaluator, space, rng, kitted, val,
                                                 polish_seed, args.polish)
                candidates.append(better)
            held_out = evaluator.score_many(candidates, val, args.validate_seed)
            top = max(range(len(candidates)), key=lambda i: held_out[i].fitness())
            better, new_score = candidates[top], held_out[top]
            gain = new_score.run_score - score.run_score
            moved = "" if describe(better) == describe(genome) else f" -> {describe(better)}"
            print(f"  {describe(genome)}{moved}: {score.run_score * 100:5.1f} -> "
                  f"{new_score.run_score * 100:5.1f}  ({gain * 100:+.1f} held out)",
                  flush=True)
            # Keep whichever the HELD-OUT seed prefers. A polish that only
            # helped on its tuning seed is noise, and this is where it is
            # refused rather than shipped.
            polished.append((better, new_score) if new_score.fitness() > score.fitness()
                            else (genome, score))
        pairs = (sorted(polished, key=lambda pair: pair[1].fitness(), reverse=True)
                 + [pair for pair in pairs if archetype(pair[0]) not in seen])
    return pairs


def slugify(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_")


def emit(args, space: CompSpace, pairs, scenario: Scenario, evaluator_b=None) -> list:
    """Write the best comp of each DISTINCT archetype, best first.

    The dedup is Fran's hard rule and the reason it is enforced here rather
    than trusted to the search: "each comp independently should optimize for
    the best possible result with those pieces -- you can see the classes as
    the pillars of the comp, and having the same 4 pillars is practically
    making the same comp". The first run emitted two Duelist comps with
    identical classes and two Runaway comps that were one comp reordered.
    """
    GA_DIR.mkdir(parents=True, exist_ok=True)
    # `location` is not decoration: `ExpeditionEnv._build_hero_pool` routes a
    # loadout-only comp to the zone this field names, so it has to be one the
    # map knows. With every zone scored there is no single right answer, so it
    # names the zone the comp scored BEST in -- a label, chosen on one noisy
    # per-zone rate, not a claim. The full breakdown is in `byZone` and one
    # edit to this field re-routes the comp.
    def _best_zone(score: "Score") -> str:
        if not score.by_zone:
            return scenario.zones[0]
        return max(score.by_zone, key=lambda z: z[1])[0]
    written = []
    chosen = []
    seen = set()
    covered: set = set()
    remaining = list(pairs)
    while len(chosen) < args.emit and remaining:
        eligible = [i for i, (g, _s) in enumerate(remaining) if archetype(g) not in seen]
        if not eligible:
            break
        # Prefer a comp that brings a class not yet emitted -- but only from
        # among those the score cannot distinguish from the best remaining.
        #
        # Coverage is worth having (one run of six comps used six of twenty
        # classes) and it is not worth buying with real quality. "Within one
        # standard error of the best" is the same inside-the-noise bound the
        # rest of this tool uses: if the fitness cannot tell two comps apart,
        # taking the more novel one costs nothing it can measure.
        best_fit = remaining[eligible[0]][1].fitness()
        # The error bar on the RANKED quantity, not on SUCCESS. Coverage is
        # bought only from inside the noise, so the noise has to be measured on
        # whatever the ordering above it used.
        tolerance = remaining[eligible[0]][1].run_score_se
        fresh = [i for i in eligible
                 if {h.hero_class for h in remaining[i][0]} - covered
                 and remaining[i][1].fitness() >= best_fit - tolerance]
        pool = fresh or eligible
        if not pool:
            break
        pick = pool[0] if args.class_coverage else eligible[0]
        # ...and then prefer a party of FOUR DIFFERENT CLASSES, from inside the
        # same tolerance. Fran, 2026-08-28: a duplicated hero "should be more
        # like an exception rather than a rule".
        #
        # A PREFERENCE AND NOT A CAP, deliberately. His own pool is 27%
        # duplicates (33 pairs, 4 triples and 8 quads out of 167), so a hard
        # `--max-copies 2` would forbid comps he wrote himself -- Ballad
        # Quartet is four Jesters. Bought only from inside one standard error,
        # the same bound the coverage preference above uses: if the fitness
        # cannot tell two comps apart, taking the more varied one costs nothing
        # it can measure.
        #
        # Applied AFTER coverage, so a comp bringing a new class still wins a
        # tie -- covering the roster is the stronger of the two goals and the
        # one a batch is built around.
        if args.prefer_unique:
            unique = [i for i in pool
                      if len({h.hero_class for h in remaining[i][0]}) == len(remaining[i][0])
                      and remaining[i][1].fitness() >= best_fit - tolerance]
            if unique:
                pick = unique[0]
        genome, score = remaining.pop(pick)
        seen.add(archetype(genome))
        covered |= {h.hero_class for h in genome}
        chosen.append((genome, score))
    if len(chosen) < args.emit:
        print(f"  only {len(chosen)} distinct archetypes survived; asked for {args.emit}")
    print(f"  {len(covered)} distinct classes across the {len(chosen)} comps")

    # The cross-check: score the comps we are about to write with a SECOND,
    # independently trained policy, on the same held-out seed and the same
    # scenario. The fitness is P(this policy completes the run), so a comp can
    # score well by being easy for one policy rather than by being good -- and
    # nothing in a single-model search can tell those apart. A comp both
    # policies play well is much harder to explain as an artifact of either.
    #
    # It is reported, never selected on. Emission order is still the headline
    # model's, because changing that would make the second model a second
    # fitness and reintroduce the same problem one layer up.
    cross = {}
    if evaluator_b is not None and chosen:
        val = replace(scenario, episodes_per_zone=args.validate_episodes)
        print(f"  cross-checking {len(chosen)} comps against "
              f"{Path(args.model_b).name} on the same held-out seed", flush=True)
        for (genome, _s), other in zip(
                chosen, evaluator_b.score_many([g for g, _s in chosen], val,
                                               args.validate_seed)):
            cross[genome_key(genome)] = other

    for rank, (genome, score) in enumerate(chosen, start=1):
        team_name = f"GA {args.tag} {rank:02d}"
        best_zone = _best_zone(score)
        location = ZONE_ID_TO_JSON_LOCATION.get(best_zone, "The Ruins")
        # Point the region quirks at the region the comp is being SHIPPED for.
        # Written after `location` is chosen, because the comp does not know
        # which region it belongs to until then -- and a quirk for another
        # region is dead weight the player would have to fix by hand.
        #
        # The ORIGINAL is kept for the cross-check lookup: that dict is keyed
        # on `genome_key`, so rebinding `genome` here silently orphaned every
        # second-model column.
        scored_genome = genome
        genome = space.retarget_region_quirks(genome, best_zone)
        provenance = {
            "tag": args.tag,
            "model": args.model,
            "generations": args.generations,
            "population": args.population,
            "gaSeed": args.seed,
            "validationSeed": args.validate_seed,
            "zones": list(scenario.zones),
            "tier": scenario.tier,
            "lengthBand": scenario.band,
            "numCombats": scenario.num_combats,
            "resolveLevel": scenario.resolve_level,
            "requiredClass": args.require_class or "",
            "archetype": list(archetype(genome)),
            "polishRounds": args.polish,
            "generatedUtc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "measured": score.as_dict(),
            "crossCheck": ({"model": args.model_b,
                            **cross[genome_key(scored_genome)].as_dict()}
                           if genome_key(scored_genome) in cross else None),
            "readThisFirst": ("successRate is P(this POLICY completes the run), a lower "
                              "bound on a good human, and standardError is one sigma on "
                              "it. Rank comps by it; do not read it as a percentage."),
        }
        path = GA_DIR / f"GA_{slugify(args.tag)}_{rank:02d}.json"
        path.write_text(json.dumps(space.to_json(genome, team_name, location, provenance),
                                   indent=1, ensure_ascii=False), encoding="utf-8")
        written.append(path)
        print(f"  {rank:2d}. {score.line()} | {describe(genome)}  -> {path.name}")
        if len(score.by_zone) > 1:
            print(f"      per zone: {score.zone_line()}   (location: {location})")
        other = cross.get(genome_key(scored_genome))
        if other is not None:
            delta = (other.run_score - score.run_score) * 100
            print(f"      cross-check: {other.run_score * 100:5.1f} "
                  f"+-{other.run_score_se * 100:.1f}  ({delta:+.1f} vs the "
                  f"headline model)")
    return written


# --------------------------------------------------------------------------
# Explaining a comp: what the policy actually does with what it was given
# --------------------------------------------------------------------------

def _record_skill_use(rollout, genome, scenario: Scenario, seed: int) -> dict:
    """{(slot, skill_id): times the policy chose it} over one scored batch.

    Hooks the env's own `_action_map`, which is the same table `step` reads, so
    this records the decision the policy made rather than a re-derivation of it.
    A skill that never appears here is a slot the fitness cannot price: the
    search will happily leave a dud in it, because the cost of a dud is only
    ever the good skill it displaced, and that is a second-order effect the
    size of the noise floor.
    """
    env = rollout.env
    base = env.unwrapped
    used: dict = {}
    original = type(base).step

    def counting_step(self, action):
        actor = self.engine.get_current_actor_id()
        mapped = self._action_map.get(action)
        if actor is not None and mapped is not None:
            for slot, hero in enumerate(self.engine.heroes):
                if hero.id == actor:
                    key = (slot, mapped.skill_id)
                    used[key] = used.get(key, 0) + 1
                    break
        return original(self, action)

    type(base).step = counting_step
    try:
        score_genome(rollout, genome, scenario, seed)
    finally:
        type(base).step = original
    return used


def explain(args, store, evaluator: Evaluator, scenario: Scenario) -> int:
    """Why THIS comp holds these skills and these trinkets.

    Three questions, in the order that makes the answers mean something:

    1. **Does the policy use what it was given?** A skill it never plays is
       invisible to the fitness, so the search had no reason to remove it.
    2. **What would each slot rather hold?** Every legal skill swap, scored
       paired against the incumbent on one dungeon list.
    3. **Is the kit doing anything?** A sample of alternative trinket pairs
       per hero, same pairing.

    Nothing here changes a file. It is the tool for the question "what makes it
    choose those over others", and the honest answer is often "nothing did" --
    a delta inside the standard error is the fitness telling you it cannot see
    the difference, which is a finding about the instrument, not the comp.
    """
    space = CompSpace(store, args.trinket_rarities, args.negative_quirks,
                      args.class_trinket_bias, args.synergy_bias,
                      banned=args.ban_class,
                      rarity_bias=getattr(args, "rarity_bias", 0.0),
                      quirk_slots=(RANDOM_QUIRKS_PER_POLARITY if getattr(args, "sparse_quirks", False) else None),
                      kit_prior_bias=getattr(args, "kit_prior_bias", 0.0))
    rng = random.Random(args.seed)
    path = Path(args.explain)
    data = json.loads(path.read_text(encoding="utf-8"))
    genome = _genome_from_json(data, store, space)
    if genome is None:
        print(f"could not read a comp out of {path}")
        return 1

    val = replace(scenario, episodes_per_zone=args.validate_episodes)
    base = evaluator.score_many([genome], val, args.validate_seed)[0]
    print(f"{data.get('teamName', path.stem)} | {describe(genome)}")
    print(f"  baseline {base.line()}\n")

    used = _record_skill_use(evaluator.local_rollout(), genome, val, args.validate_seed)
    total = sum(used.values()) or 1
    names = space._display

    print("  what the policy actually played (share of all hero turns):")
    for slot, gene in enumerate(genome):
        shown = gene.skills or tuple(sorted(s.id for s in store.heroes[gene.hero_class].skills))
        print(f"    rank {slot + 1} {gene.hero_class}")
        for sid in shown:
            count = used.get((slot, sid), 0)
            flag = "   <- never played" if not count else ""
            label = names.skill_name.get(gene.hero_class, {}).get(sid, sid)
            print(f"      {label:<24} {count * 100.0 / total:5.1f}%{flag}")
        for sid, count in sorted(((k[1], v) for k, v in used.items() if k[0] == slot),
                                 key=lambda kv: -kv[1]):
            if sid not in shown and sid != "__pass__":
                label = names.skill_name.get(gene.hero_class, {}).get(sid, sid)
                print(f"      ({label}, not equipped) {count * 100.0 / total:5.1f}%")

    print("\n  every legal skill swap, paired on the same dungeons:")
    for slot, gene in enumerate(genome):
        if not gene.skills:
            print(f"    rank {slot + 1} {gene.hero_class}: unrestricted, no 4-of-7 to swap")
            continue
        spare = [s for s in space.skill_pool[gene.hero_class] if s not in gene.skills]
        candidates, labels = [], []
        for out_id in gene.skills:
            for in_id in spare:
                swapped = list(genome)
                kept = tuple(sorted([s for s in gene.skills if s != out_id] + [in_id]))
                swapped[slot] = replace(gene, skills=kept)
                candidates.append(tuple(swapped))
                labels.append((out_id, in_id))
        scores = evaluator.score_many(candidates, val, args.validate_seed)
        ranked = sorted(range(len(candidates)),
                        key=lambda i: scores[i].run_score, reverse=True)
        print(f"    rank {slot + 1} {gene.hero_class}")
        for i in ranked[:4]:
            out_id, in_id = labels[i]
            hero_names = names.skill_name.get(gene.hero_class, {})
            delta = scores[i].run_score - base.run_score
            mark = "  *" if abs(delta) > 2 * base.run_score_se else ""
            print(f"      -{hero_names.get(out_id, out_id):<22} "
                  f"+{hero_names.get(in_id, in_id):<22} {delta * 100:+6.1f}pp{mark}")

    print("\n  alternative trinket pairs, paired on the same dungeons:")
    for slot, gene in enumerate(genome):
        candidates = []
        for _ in range(args.explain_samples):
            swapped = list(genome)
            swapped[slot] = replace(gene, trinkets=space.random_trinkets(
                rng, gene.hero_class, skills=gene.skills))
            candidates.append(space.finish(rng, tuple(swapped)))
        scores = evaluator.score_many(candidates, val, args.validate_seed)
        best = max(range(len(candidates)), key=lambda i: scores[i].run_score)
        worn = " + ".join(names.trinket_name.get(t, t) for t in gene.trinkets)
        found = " + ".join(names.trinket_name.get(t, t) for t in candidates[best][slot].trinkets)
        delta = scores[best].run_score - base.run_score
        print(f"    rank {slot + 1} {gene.hero_class}")
        print(f"      wearing {worn}")
        print(f"      best of {args.explain_samples} alternatives: {found} ({delta * 100:+.1f}pp)")

    print(f"\n  one sigma on the baseline is {base.run_score_se * 100:.1f}; "
          f"a delta smaller than twice that is the fitness saying it cannot tell.")
    return 0


def _genome_from_json(data: dict, store, space: CompSpace):
    """Read a comp JSON back into a genome, via the real loader.

    Written into a temporary `curated_comps/` rather than parsed here, so
    `--explain` reads a file exactly the way training would.
    """
    import tempfile

    from data.comp_loadout_loader import CompLoadoutLoader

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "metadata" / "COMPS" / "curated_comps"
        root.mkdir(parents=True)
        (root / "comp.json").write_text(json.dumps(data, ensure_ascii=False),
                                        encoding="utf-8")
        entries = CompLoadoutLoader.load_all(tmp, GAME_DIR, store)
    if not entries:
        return None
    return genome_from_loadout(entries[0], store)


def _probe_spread(scores) -> tuple:
    """(mean, SD, SD/SE) over one arm of the probe.

    The RATIO is what ranks a corner, not the raw SD. A spread of 5 points
    measured with an error bar of 5 points is not a spread -- and the two arms
    carry very different error bars at the same episode count, because a comp
    that finishes every run has almost no variance and one that dies halfway
    has a great deal.
    """
    vals = [s.run_score for s in scores]
    mean = statistics.fmean(vals)
    sd = statistics.stdev(vals) if len(vals) > 1 else 0.0
    se = statistics.fmean(s.run_score_se for s in scores) or 1e-9
    return mean, sd, sd / se


def _probe_curated_genomes(store, args, rng: random.Random) -> list:
    """A sample of the comps Fran actually plays -- the region the search ends
    up in.

    Sampled from `build_comp_pool`, the single source, so it is the same
    population `tools/comp_leaderboard.py` ranks and the same one
    `--curated-fraction` seeds the GA from.
    """
    pool = build_comp_pool(store)
    picked = rng.sample(pool, min(args.probe_candidates, len(pool)))
    out = []
    for name, _zone, _heroes in picked:
        loadout = store.comp_loadout_by_name(name)
        genome = genome_from_loadout(loadout, store) if loadout else None
        if genome is not None:
            out.append(genome)
    return out


def scenario_probe(args, store, evaluator: Evaluator, scenario: Scenario) -> int:
    """Which (tier, band) actually SEPARATES candidates for THIS policy?

    `CLAUDE.md` has required this "after any retrain" since 2026-08-22 and never
    named a command for it, so it has been folklore. It matters because the
    fitness is `P(this policy completes the run)`: on a scenario the policy
    always wins, every candidate scores 1.0 and the search ranks noise; on one
    it always loses, every candidate scores 0.0 and the search ranks nothing at
    all. On the 2026-08-21 retrain tier 3 medium and tier 3 long were both
    ceilings and tier 5 long a floor, leaving only tier 5 medium usable -- and
    the shipped 2026-08-24 checkpoint reads **0% at tier 5**, so the current
    default cannot rank anything.

    Ranks the four corners by the SPREAD they produce, because spread is the
    whole property being bought. A scenario whose candidates all score the same
    is unusable however sensible its mean looks.

    **TWO populations, and a corner has to separate BOTH** (2026-08-26). This
    used to probe random candidates only, which is what generation 0 looks
    like -- and that is exactly half the run. A search CONVERGES into the
    curated region, so a corner that ranks random comps beautifully and then
    saturates on good ones ranks noise for its last generations, which are the
    generations that decide what gets emitted.

    Measured the day the second arm was added, on the shipped checkpoint: the
    random arm named **tier 3 long** (SD 19.0, against 4.6 at tier 5 long), and
    on the top 24 curated comps the order INVERTS -- tier 3 long collapses to
    SD 1.7 with 4.0 points of headroom left above the best comp, while tier 5
    long holds SD 12.9 with 19.1. Ranking on the random arm alone would have
    pointed the search at the corner where its endgame cannot measure anything.

    So both arms are scored, both are normalised by their own mean standard
    error (a spread smaller than the error bar is not a spread), and a corner
    is ranked by the WORSE of the two. `head` -- how far the best curated comp
    is from a perfect 100 -- is printed beside it, because a search asked to
    beat the curated bar cannot do it in a corner with no room left above it.
    """
    space = CompSpace(store, args.trinket_rarities, args.negative_quirks,
                      args.class_trinket_bias, args.synergy_bias,
                      banned=args.ban_class,
                      rarity_bias=getattr(args, "rarity_bias", 0.0),
                      quirk_slots=(RANDOM_QUIRKS_PER_POLARITY if getattr(args, "sparse_quirks", False) else None),
                      kit_prior_bias=getattr(args, "kit_prior_bias", 0.0))
    rng = random.Random(args.seed)
    genomes = [space.random_genome(rng, args.party_size, args.require_class)
               for _ in range(args.probe_candidates)]
    curated = _probe_curated_genomes(store, args, rng)
    print(f"scenario probe | {args.probe_candidates} random + {len(curated)} "
          f"curated candidates x {args.episodes} episodes x "
          f"{len(args.zones)} zone(s)")
    print(f"  {'tier':>4s} {'band':>7s} | {'rand':>6s} {'SD':>6s} {'SD/SE':>6s} "
          f"| {'cur':>6s} {'SD':>6s} {'SD/SE':>6s} {'head':>6s} "
          f"| {'succ':>6s} | verdict")
    rows = []
    for tier in (3, 5):
        for band in ("medium", "long"):
            lo, hi = EXPEDITION_LENGTH_BANDS[band]
            sc = replace(scenario, tier=tier, band=band,
                         num_combats=(lo + hi) // 2,
                         resolve_level=TIER_TO_HERO_LEVEL[tier])
            scores = evaluator.score_many(genomes, sc, args.seed)
            # The spread is measured on the RANKED quantity. A scenario is
            # bought for its ability to separate candidates on whatever the
            # search will order them by, and since 2026-08-26 that is
            # `run_score`, not SUCCESS. The SUCCESS columns are kept beside it
            # because the two disagreeing is itself the interesting case: a
            # corner where SUCCESS is a floor can still rank perfectly well on
            # how far the parties got and what shape they came home in.
            r_mean, r_sd, r_ratio = _probe_spread(scores)
            succ = [s.success for s in scores]
            c_mean = c_sd = c_ratio = 0.0
            head = 1.0
            if curated:
                c_scores = evaluator.score_many(curated, sc, args.seed)
                c_mean, c_sd, c_ratio = _probe_spread(c_scores)
                head = 1.0 - max(s.run_score for s in c_scores)
            # Ranked by the WORSE arm: a corner is only usable if it separates
            # the candidates the search STARTS with AND the ones it ends with.
            rank = min(r_ratio, c_ratio) if curated else r_ratio
            if rank < 1.0:
                verdict = ("USELESS - spread is under the error bar on the "
                           f"{'random' if r_ratio <= c_ratio else 'curated'} arm")
            elif curated and head < 0.05:
                verdict = "no headroom - the curated bar is already at the top"
            elif r_mean > 0.95 or c_mean > 0.95:
                verdict = "ceiling"
            elif r_mean < 0.05:
                verdict = "floor"
            else:
                verdict = "usable"
            rows.append((rank, tier, band, r_mean, verdict))
            print(f"  {tier:>4d} {band:>7s} | {r_mean * 100:5.1f} {r_sd * 100:5.1f} "
                  f"{r_ratio:6.1f} | {c_mean * 100:5.1f} {c_sd * 100:5.1f} "
                  f"{c_ratio:6.1f} {head * 100:5.1f} | "
                  f"{sum(succ) / len(succ) * 100:5.1f}% | {verdict}")
    rows.sort(reverse=True)
    best = rows[0]
    print()
    if best[0] < 1.0:
        print("  NO scenario separates these candidates by more than its own "
              "error bar. The search cannot rank anything here -- add episodes, "
              "widen the candidate pool, or change the zone.")
        return 1
    print(f"  Use tier {best[1]} {best[2]} (widest spread on its WORSE arm, "
          f"SD/SE {best[0]:.1f}). Pass it as `--tier {best[1]} --band {best[2]}`.")
    return 0


def self_check(args, store, evaluator: Evaluator, scenario: Scenario) -> int:
    """Two candidates, scored twice at the same seed, must come out identical.

    Every other tool here ships one of these because a fitness that is not
    reproducible makes every number the search prints meaningless.
    """
    space = CompSpace(store, args.trinket_rarities, args.negative_quirks,
                      args.class_trinket_bias, args.synergy_bias,
                      banned=args.ban_class,
                      rarity_bias=getattr(args, "rarity_bias", 0.0),
                      quirk_slots=(RANDOM_QUIRKS_PER_POLARITY if getattr(args, "sparse_quirks", False) else None),
                      kit_prior_bias=getattr(args, "kit_prior_bias", 0.0))
    rng = random.Random(args.seed)
    genomes = [space.random_genome(rng, args.party_size, args.require_class)
               for _ in range(2)]
    first = evaluator.score_many(genomes, scenario, args.seed)
    evaluator._cache.clear()
    second = evaluator.score_many(genomes, scenario, args.seed)
    bad = 0
    for i, (a, b) in enumerate(zip(first, second)):
        delta = abs(a.fitness() - b.fitness())
        print(f"  candidate {i}: {a.line()}")
        print(f"              rerun delta {delta:.6f}")
        if delta > 1e-9:
            bad += 1

    # The STRUCTURAL fitness needs its own leg, and it is the one more likely
    # to drift: it draws a kit per episode, so a non-reproducible draw would
    # move the score without touching the genome -- invisible to the check
    # above, which never calls it.
    kits = [[rekit(space, g, _kit_rng(args.seed, ep), args.require_class)
             for ep in range(scenario.episodes_per_zone)]
            for g in genomes]
    s_first = evaluator.score_structures(kits, scenario, args.seed)
    evaluator._cache.clear()
    kits2 = [[rekit(space, g, _kit_rng(args.seed, ep), args.require_class)
              for ep in range(scenario.episodes_per_zone)]
             for g in genomes]
    s_second = evaluator.score_structures(kits2, scenario, args.seed)
    for i, (a, b) in enumerate(zip(s_first, s_second)):
        delta = abs(a.fitness() - b.fitness())
        print(f"  structure {i}: {a.line()}")
        print(f"              rerun delta {delta:.6f}")
        if delta > 1e-9:
            bad += 1

    print("SELF-CHECK", "OK" if not bad else f"FAILED ({bad} non-deterministic)")
    return 1 if bad else 0


def build_parser() -> argparse.ArgumentParser:
    """Split out of `main` so a test can read the defaults without running a
    search -- the scenario defaults ARE a decision (all four zones, Champion,
    everything maxed) and a silent edit to one of them changes every comp the
    tool has ever emitted."""
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--model", default="models/dd_combat_ppo_full_expedition.zip")
    # All four zones by default, Fran's call: "i prefer to have better results
    # on a less biased comp". A comp scored only in the Ruins is a Ruins comp,
    # and the enemy pool differs per zone AND per tier (crypts fields 21
    # distinct monsters at tier 5, and they are not the cove's) -- so a single
    # zone selects for whatever beats skeletons. It costs exactly 4x: every
    # candidate now plays `--episodes` expeditions in each of the four.
    p.add_argument("--zones", default="crypts,warrens,weald,cove",
                   help="comma-separated zone ids; every candidate is scored on ALL of them, "
                        "so runtime is linear in how many (default: all four)")
    # Champion + medium, because that is where the policy currently scores in
    # the middle. See the probe table in the module docstring -- at tier 3 every
    # candidate completes and the search has nothing to select on.
    p.add_argument("--tier", type=int, default=5, choices=(1, 2, 3, 4, 5))
    p.add_argument("--band", default="medium", choices=tuple(EXPEDITION_LENGTH_BANDS))
    p.add_argument("--combats", type=int, default=None,
                   help="fights per expedition (default: the band's midpoint)")
    p.add_argument("--resolve", type=int, default=None,
                   help="resolve level for every hero (default: the tier's, per TIER_TO_HERO_LEVEL)")
    p.add_argument("--population", type=int, default=24)
    p.add_argument("--generations", type=int, default=8)
    p.add_argument("--episodes", type=int, default=12, help="episodes per zone per candidate")
    p.add_argument("--elites", type=int, default=4)
    p.add_argument("--tournament", type=int, default=3)
    p.add_argument("--mutation-rate", type=float, default=0.35,
                   help="probability of ANOTHER mutation after each one, so >1 is possible")
    p.add_argument("--party-size", type=int, default=PARTY_SIZE)
    p.add_argument("--require-class", default=None,
                   help="every comp must field this hero id (e.g. duelist)")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--fixed-seed", action="store_true",
                   help="do NOT rotate the scenario seed per generation")
    p.add_argument("--validate-episodes", type=int, default=30)
    p.add_argument("--validate-seed", type=int, default=9999,
                   help="held out from the generation seeds on purpose")
    p.add_argument("--emit", type=int, default=5, help="how many comps to write")
    p.add_argument("--tag", default=None, help="names the output files and the comps")
    p.add_argument("--trinket-rarities", default=None,
                   help="comma-separated rarity override; default is every rarity the "
                        "curated corpus equips plus the universal ones")
    p.add_argument("--curated-fraction", type=float, default=0.25,
                   help="share of the starting population seeded from curated comps; "
                        "the rest is uniform random")
    p.add_argument("--max-class-share", type=float, default=0.30,
                   help="no class may appear in more than this share of the population; "
                        "the pinned --require-class is exempt")
    p.add_argument("--class-coverage", action="store_true", default=True,
                   help="at emit time prefer the best comp that introduces a class not "
                        "yet emitted (on by default)")
    p.add_argument("--kit-prior", type=float, default=0.0, metavar="W",
                   dest="kit_prior_bias",
                   help="how hard the QUIRK and TRINKET draws lean toward what "
                        "the curated corpus equips. 0 is uniform and reproduces "
                        "the pre-2026-08-28 search exactly. Off by default "
                        "because it is unproven: Fran's own caveat is that his "
                        "corpus does not innovate on quirks, so the prior may "
                        "only be reproducing a habit. Run it paired against 0 "
                        "and read the CI before trusting it.")
    p.add_argument("--rarity-bias", type=float, default=0.35, metavar="W",
                   help="how hard the class draw leans toward classes the "
                        "curated pool uses rarely (Houndmaster fills 96 hero "
                        "slots, Duelist 6). 0 is a uniform draw and reproduces "
                        "the pre-2026-08-28 search exactly. A PRIOR, not a "
                        "restriction: every class keeps a nonzero weight, "
                        "--ban-class still overrides, and a biased swap is "
                        "REFUSED when it would lower the party's rank_fit -- "
                        "otherwise rarity builds parties that all want rank 1.")
    p.add_argument("--duplicate-cost", type=float, default=DUPLICATE_COST,
                   metavar="W",
                   help="what a repeated class costs in the SEARCH's ranking, "
                        "as a fraction of the tiebreak budget (default "
                        f"{DUPLICATE_COST}, i.e. off). MEASURED AND NULL: it "
                        "moves the elites' duplicate share by +0.0pp, because "
                        "selection is not what produces duplicates. "
                        "--ban-class is: it shrinks the class pool each round, "
                        "and four heroes drawn from eight classes repeat one "
                        "63%% of the time. Turning this on without changing "
                        "the bans buys nothing.")
    p.add_argument("--no-prefer-unique", dest="prefer_unique", action="store_false",
                   help="stop preferring a party of four DIFFERENT classes at "
                        "emit time. The preference is bought only from inside "
                        "one standard error of the best remaining comp, so it "
                        "never costs measurable quality; this flag restores the "
                        "pre-2026-08-28 output exactly, for reproducing an arm.")
    p.add_argument("--no-class-coverage", dest="class_coverage", action="store_false",
                   help="emit strictly by score instead")
    p.add_argument("--max-per-archetype", type=int, default=2,
                   help="how many individuals may share one class multiset")
    p.add_argument("--class-trinket-bias", type=float, default=CLASS_TRINKET_BIAS,
                   help="how often a trinket draw prefers the hero's class-locked ones")
    p.add_argument("--synergy-bias", type=float, default=SYNERGY_TRINKET_BIAS,
                   help="how often a trinket draw prefers one whose chance modifier "
                        "matches a payload the hero's EQUIPPED kit delivers; 0 "
                        "reproduces the pre-2026-08-22 search")
    p.add_argument("--no-structure-first", dest="structure_first",
                   action="store_false",
                   help="search the whole genome at once, the pre-2026-08-23 "
                        "behaviour. The default is two-stage: stage 1 ranks "
                        "class multiset + formation with the kit REDRAWN every "
                        "episode, so a structure cannot be flattered by a lucky "
                        "trinket draw; stage 2 is the usual order+trinket polish "
                        "on the winners.")
    p.set_defaults(structure_first=True)
    p.add_argument("--ban-class", default=None, metavar="a,b,c",
                   help="comma-separated hero ids the search may not use. The "
                        "way to get roster coverage across a BATCH: emit a "
                        "comp, ban the four classes it used, search again. "
                        "Also the honest way to test whether a class the "
                        "search always picks is actually necessary")
    p.add_argument("--polish-skills", type=int, default=3, metavar="N",
                   help="rounds of skill hill-climbing per finalist, between "
                        "the ordering search and the trinket polish. 0 restores "
                        "the pre-2026-08-26 behaviour, where skills were picked "
                        "by keeping the best of --restage-kits random draws and "
                        "never refined")
    p.add_argument("--restage-kits", type=int, default=6, metavar="N",
                   help="kits drawn per surviving structure at the stage-1 -> "
                        "stage-2 handoff, scored on the tuning seed. The polish "
                        "hill-climbs from the best of these.")
    p.add_argument("--polish", type=int, default=3,
                   help="rounds of trinket hill-climbing on the emitted comps; 0 to skip")
    p.add_argument("--sparse-quirks", action="store_true",
                   help="draw 1-3 positive quirks per hero instead of filling all five. Reproduces the pre-2026-08-30 search exactly; filling them is worth +2.3 mean over 14 paired comparisons on two checkpoints.")
    p.add_argument("--negative-quirks", action="store_true",
                   help="let the search pick negative quirks too; off because a "
                        "player removes those at the Sanitarium rather than choosing them")
    p.add_argument("--model-b", default=None, metavar="CKPT",
                   help="a SECOND, independently trained checkpoint. The comps "
                        "that get written are re-scored with it on the same "
                        "held-out seed and the result is recorded as crossCheck. "
                        "Reported, never selected on: a comp only one policy can "
                        "play is a policy artifact, and a single-model search "
                        "cannot see that at all.")
    p.add_argument("--workers", type=int, default=1)
    p.add_argument("--explain", default=None, metavar="COMP.json",
                   help="do not search: report why one comp holds the skills and "
                        "trinkets it does")
    p.add_argument("--explain-samples", type=int, default=12,
                   help="alternative trinket pairs sampled per hero by --explain")
    p.add_argument("--scenario-probe", action="store_true",
                   help="do not search: report which (tier, band) actually "
                        "SEPARATES candidates for this policy. Required after "
                        "any retrain -- the fitness is P(this policy completes "
                        "the run), so a scenario it always wins or always loses "
                        "ranks nothing. The shipped 2026-08-24 checkpoint reads "
                        "0%% at tier 5, which is the current default.")
    p.add_argument("--probe-candidates", type=int, default=8,
                   help="random candidates the scenario probe spreads")
    p.add_argument("--self-check", action="store_true")
    return p


def main(argv=None) -> int:
    p = build_parser()
    args = p.parse_args(argv)

    args.zones = [z.strip() for z in args.zones.split(",") if z.strip()]
    args.trinket_rarities = ([r.strip() for r in args.trinket_rarities.split(",") if r.strip()]
                             if args.trinket_rarities else None)
    known_zones = sorted(set(CSV_ZONE_TO_ZONE_ID.values()))
    unknown = [z for z in args.zones if z not in known_zones]
    if unknown:
        p.error(f"unknown zone(s) {unknown}; known: {known_zones}")
    if args.combats is None:
        lo, hi = EXPEDITION_LENGTH_BANDS[args.band]
        args.combats = (lo + hi) // 2
    if args.resolve is None:
        args.resolve = TIER_TO_HERO_LEVEL[args.tier]
    if args.tag is None:
        args.tag = args.require_class or args.zones[0]

    args.ban_class = ([b.strip() for b in args.ban_class.split(",") if b.strip()]
                      if args.ban_class else [])

    from data.store_cache import load_store_cached
    store = load_store_cached(CSV_DIR, GAME_DIR)
    unknown_bans = [b for b in args.ban_class if b not in store.heroes]
    if unknown_bans:
        # A typo would ban NOTHING and the run would look like a successful
        # experiment -- the same argument as `--hold-out-class`'s validation.
        p.error(f"unknown hero id(s) {unknown_bans}; known: {sorted(store.heroes)}")
    if args.require_class and args.require_class in args.ban_class:
        p.error(f"--require-class {args.require_class} is also banned")
    if len(store.heroes) - len(args.ban_class) < args.party_size:
        p.error("the ban list leaves fewer classes than a party needs")
    if args.require_class and args.require_class not in store.heroes:
        p.error(f"unknown hero id {args.require_class!r}; known: {sorted(store.heroes)}")

    scenario = Scenario(zones=tuple(args.zones), tier=args.tier, band=args.band,
                        num_combats=args.combats, episodes_per_zone=args.episodes,
                        resolve_level=args.resolve)
    evaluator = Evaluator(args.model, args.workers)
    try:
        if args.scenario_probe:
            return scenario_probe(args, store, evaluator, scenario)
        if args.self_check:
            return self_check(args, store, evaluator, scenario)
        if args.explain:
            return explain(args, store, evaluator, scenario)
        total = args.population * args.generations * args.episodes * len(args.zones)
        print(f"comp GA | tag {args.tag} | zones {','.join(args.zones)} tier {args.tier} "
              f"{args.band} x{args.combats} | resolve {args.resolve}\n"
              f"  {args.population} candidates x {args.generations} generations x "
              f"{args.episodes} episodes x {len(args.zones)} zone(s) "
              f"= up to {total} expeditions\n", flush=True)
        pairs = run_ga(args, store, evaluator, scenario)
        print()
        # Built only now: it costs a model load and a worker pool, and nothing
        # before this point has any use for it.
        evaluator_b = (Evaluator(args.model_b, args.workers) if args.model_b else None)
        try:
            emit(args, CompSpace(store, args.trinket_rarities, args.negative_quirks,
                                 args.class_trinket_bias, args.synergy_bias,
                                 banned=args.ban_class),
                 pairs, scenario, evaluator_b)
        finally:
            if evaluator_b is not None:
                evaluator_b.close()
    finally:
        evaluator.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
