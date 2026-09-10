"""Expedition training env: randomizes (real hero comp, zone-matched real
enemy encounter, paired Resolve Level) every reset() instead of
DarkestDungeonEnv's fixed default (2x Crusader vs 2x Cultist Brawler).

Named after the game's own term for "embark with a chosen party into a
chosen dungeon zone" — that's exactly what this env randomizes over.

Goal: train a policy that generalizes across real party comps and real
per-zone enemy groups, on the premise that a comp built for a given zone
(COMPS/ CSV's Location field, community-curated) should perform better
there — the model should pick that up implicitly through the reward signal
by seeing that comp repeatedly against that zone's actual enemies, rather
than overfitting to one fixed pairing.

Hero Resolve Level is paired to the enemy encounter's difficulty band
(user-specified mapping): quest difficulty levels 1/3/5 are
Apprentice/Veteran/Champion in the real game, and mash.darkest tiers 1-2 /
3-4 / 5 verified to correspond to those same three bands (confirmed via
real enemy HP scaling — see ASSUMPTIONS.md). A party fielded at the top of
its bracket is used: resolve 2/4/6 for Apprentice/Veteran/Champion, which
(via CombatEngine.reset()'s tier_idx = level-1 mapping) equips weapon/armour/
skill tier 2/4/5(max) respectively. Without this pairing, heroes were always
loaded at max gear regardless of the enemy band, an unpaired power gap the
real game's resolve-level-gated questing never allows.

Both hero/enemy data sources are real, not invented: store.comps (COMPS/
CSV, wired up in data/comps_csv_loader.py) and store.enemy_comps
(data/enemy_comp_loader.py, real dungeons/<zone>/<zone>.<tier>.mash.darkest
encounter tables).
"""

from __future__ import annotations
import random
from typing import Optional

from .gym_env import DarkestDungeonEnv
from data.composition_loader import CompositionLoader

# COMPS/ CSV's Location string -> the zone_id EnemyCompLoader/store.enemy_comps
# uses. Only the 4 zones with real numbered mash tables are trainable this
# way; "DD2" (2 comps) and "Courtyard" (1 comp, Crimson Court DLC) have no
# matching zone in store.enemy_comps and are skipped.
CSV_ZONE_TO_ZONE_ID = {
    "Ruins": "crypts",
    "Warrens": "warrens",
    "Cove": "cove",
    "Weald": "weald",
}

# The two files speak different dialects: a loadout JSON says "The Cove", the
# retired CSV said "Cove". Since the JSON is now the ONLY source, every lookup
# goes through here rather than through the bare dict.
#
# A location that resolves to nothing is dropped, and that is load-bearing
# rather than defensive: two comps are tagged "The Darkest Dungeon II", a place
# this simulator has no enemy pool for.
def zone_id_for_location(location: str | None) -> str | None:
    if not location:
        return None
    return (CSV_ZONE_TO_ZONE_ID.get(location)
            or CSV_ZONE_TO_ZONE_ID.get(location[4:] if location.startswith("The ")
                                       else location))


# Comps that exist as loadouts but must never be SAMPLED.
#
# Until 2026-08-23 this was enforced by accident -- the pool was built from
# `COMPS/Comps <hash>.csv` and these comps simply had no row. That CSV is
# retired (Fran's call: it had drifted to 113 of 145 rows naming comps that no
# longer exist, and every bug it ever caused -- alphabetised formations,
# deduplicated duplicate-class rosters -- came from treating it as truth), so
# the exclusion has to be said out loud or the tutorial party leaks into
# training and quietly corrupts `tools/eval_tutorial.py`, the one exactly
# reproducible ruler this project has.
PINNED_COMP_NAMES = frozenset({"The Old Road"})


# ── MECHANIC FAMILIES, for `--focus-mechanic` (2026-08-27) ──────────────────
#
# Fran's constraint, and it governs the whole design:
#
#   *"we want to fine tune this model so it learns 'The mechanics of Darkest
#   Dungeon and the importance of certain numbers' rather than making a player
#   that follows the same plan always, as this wont enable the use of modded
#   classes and will hurt our scalability on the future"*
#
# So a family is defined by what a comp CAN DO, never by which comps it is. A
# party fielding Solo, Protect Me, Invigorating Vapours or Guard Dog is a dodge
# party whoever is in it -- including a modded class that arrives later and has
# no name in any list here. Same argument that removed the 20-class one-hot
# from the observation.
#
# Membership is read off the EQUIPPED skills' effects, so it tracks the game's
# data rather than a table that has to be maintained beside it.
MECHANIC_FAMILIES = ("dodge", "bleed", "blight", "burn")


def _skill_grants_dodge(store, skill_id: str, hero_id: str) -> bool:
    """Does this skill raise someone's DODGE?

    `defense_rating` is the stat the engine's own `_apply_buff_to_stats` reads,
    reached either through a named buff or through an inline stat buff. Fran
    named Jester's Solo, Antiquarian's Protect Me and Invigorating Vapours, and
    Houndmaster's Guard Dog; this finds those and anything else that does the
    same thing.
    """
    hero = store.heroes.get(hero_id)
    skill = next((s for s in (hero.skills if hero else []) if s.id == skill_id), None)
    if skill is None or not skill.levels:
        return False
    lv = skill.levels[max(skill.levels)]
    for name in (lv.effects or []):
        eff = store.effects.get(name)
        if eff is None:
            continue
        if eff.is_guard:
            # Guard is dodge-adjacent in exactly the way Fran means: a
            # dodge-built Houndmaster guarding the front rank makes the whole
            # front rank dodge, which is the play he described.
            return True
        # The inline `combat_stat_buff` + `.defense_rating_add` pair, which is
        # how EVERY shipped dodge skill actually grants it -- Solo, Protect Me,
        # Invigorating Vapours and Bolster all use this form, not a named buff.
        # `INLINE_STAT_FIELD_MAP` is the engine's own table for it.
        if getattr(eff, "defense_rating_add", None):
            return True
        for bid in (eff.buff_ids or []):
            buff = store.buffs.get(bid)
            if buff and "defense" in (getattr(buff, "stat_sub_type", "") or "").lower():
                return True
    return False


def _skill_applies(store, skill_id: str, hero_id: str, payload: str) -> bool:
    """Does this skill apply bleed / blight / burn?"""
    attr = {"bleed": "bleed_amount", "blight": "blight_amount",
            "burn": "burn_amount"}[payload]
    hero = store.heroes.get(hero_id)
    skill = next((s for s in (hero.skills if hero else []) if s.id == skill_id), None)
    if skill is None or not skill.levels:
        return False
    lv = skill.levels[max(skill.levels)]
    for name in (lv.effects or []):
        eff = store.effects.get(name)
        if eff is not None and getattr(eff, attr, None):
            return True
    return False


#: How many skills a comp must field before it COUNTS as built around a
#: mechanic. One is not a plan: at a threshold of 1, 144 of 163 comps are
#: "dodge comps" and 130 are "bleed comps", which is not a focus, it is the
#: pool. At 2 they are 98 and 77 -- comps that can actually stack the thing.
FOCUS_MIN_SOURCES = 2
#: Below this many comps a family is too thin to train on half the time, so
#: the threshold relaxes to 1 rather than shipping a two-comp "focus" that is
#: really just a smaller training set. Burn is the case: only the Runaway
#: burns, so 7 comps can do it at all and 2 can stack it.
FOCUS_MIN_POOL = 8


def comp_mechanic_counts(store, loadout) -> dict:
    """How many equipped skills back each mechanic family, per comp.

    Equipped, not the full movepool: a Jester who did not bring Solo is not a
    dodge comp, and `--loadout-random-p` varies exactly that.
    """
    found: dict = {}
    for hero in (loadout.heroes if loadout else []):
        if not hero.hero_id:
            continue
        # An unrestricted class (Abomination, Duelist) equips all seven, and
        # `skill_ids` is empty for it -- read the whole movepool there.
        skill_ids = list(hero.skill_ids)
        if not skill_ids:
            hd = store.heroes.get(hero.hero_id)
            skill_ids = [s.id for s in (hd.skills if hd else [])]
        for sid in skill_ids:
            if _skill_grants_dodge(store, sid, hero.hero_id):
                found["dodge"] = found.get("dodge", 0) + 1
            for payload in ("bleed", "blight", "burn"):
                if _skill_applies(store, sid, hero.hero_id, payload):
                    found[payload] = found.get(payload, 0) + 1
    return found


def comp_mechanics(store, loadout, min_sources: int = 1) -> frozenset:
    """The families this comp can play, at or above `min_sources` skills."""
    counts = comp_mechanic_counts(store, loadout)
    return frozenset(fam for fam, n in counts.items() if n >= min_sources)


def build_comp_pool(store, hold_out_classes=frozenset(),
                    only_classes=None) -> list[tuple[str, str, list[str]]]:
    """Every sampleable comp, as `(name, zone_id, hero_ids)`.

    Module-level because `eval_expedition.pick_representative_comps` needs the
    IDENTICAL pool and used to keep a hand-synced copy of it -- which is how
    the two drifted apart the last time this logic changed.

    `hold_out_classes` drops every comp fielding one of those classes, and
    `only_classes` keeps only comps fielding one. Together they cut the pool
    into the two halves the TRANSFER falsifier needs (2026-08-23): train with a
    class held out, then measure the policy on that class cold. It is the
    falsifier Fran chose for the mechanic-encoding change, because a win-rate
    delta cannot carry it -- the seed-to-seed spread on success here is ~1.4pp,
    wider than most effects this project measures, whereas a policy that has
    genuinely never seen a class either plays it or does not.

    Both are properties of the SPLIT, not of the party sampler, so they belong
    here rather than behind one of the five randomisation knobs.
    """
    pool = []
    for name, loadout in sorted(store.comp_loadouts.items()):
        if name in PINNED_COMP_NAMES or loadout.alias in PINNED_COMP_NAMES:
            continue
        # A comp that LOST a hero slot to a class this project cannot load is
        # not a fair party -- "Holy Hymn: Monk" was really 4 heroes, 3 of them
        # real, and trained as a 3-hero party. This replaced a name list read
        # from `COMPS/Comps/*.md` on 2026-08-23: the list named 11 comps, and
        # the structural test catches ANY unloadable class including ones
        # nobody has listed, with no external file to keep in sync. An EMPTY
        # heroClass is padding, not a loss, and is not counted.
        if len(loadout.heroes) < loadout.declared_heroes:
            continue
        zone_id = zone_id_for_location(loadout.location)
        if zone_id is None:
            continue
        hero_ids = [h.hero_id for h in loadout.heroes if h.hero_id in store.heroes]
        if len(hero_ids) < 2:
            continue
        if hold_out_classes and hold_out_classes.intersection(hero_ids):
            continue
        if only_classes is not None and not set(only_classes).intersection(hero_ids):
            continue
        pool.append((name, zone_id, hero_ids))
    return pool

# How many combat skills a hero equips. Every one of the 18 loadable classes
# has exactly 7 in its `.info.darkest` and the game lets you take 4, so a
# random legal loadout is one of 35 combinations. The Abomination is the
# exception and is never restricted -- see `_resolve_skill_ids`.
SKILLS_PER_HERO = 4

# Heroes the game never lets you choose four skills for -- they carry all seven
# always, so there is no 4-of-7 to resolve and none to randomise.
#
#   abomination      all 7 equipped, human/beast gating which are usable
#   duelist          all 7 equipped, stance_defensive/stance_aggressive gating
#
# Same shape, same reason: a MODE decides what is usable, not a loadout choice.
# Stripping either to four removes skills the player cannot unequip -- for the
# Duelist it would leave a whole stance empty, which is the stance-hero version
# of the Abomination bug where a comp's JSON dropped all three beast attacks.
UNRESTRICTED_HEROES = frozenset({"abomination", "duelist"})


def unrestricted_heroes(store) -> frozenset:
    """`UNRESTRICTED_HEROES`, DERIVED from the store instead of listed.

    The constant above is a hand-kept copy of something the game already
    states: `skill_selection: .can_select_combat_skills false`, shipped on
    exactly the Abomination and the Duelist and `true` on the other eighteen.
    Deriving it reproduces that pair exactly -- which is the check that the
    derivation reads the right key -- and it is also the only way a THIRD-PARTY
    stance class can ever be treated correctly, because a frozenset of two ids
    is identity and a modded class has no identity here by design.

    The Sibyl is the case that forced it: three stances (moon / eclipse / sun),
    `align` cycling between them, and five of her seven skills gated on a mode.
    Cut to a 4-of-7 she can lose `align` and be stranded in one stance for the
    whole raid, with three of her remaining skills permanently illegal. Her
    file says `can_select_combat_skills false` for exactly that reason.

    The constant stays, and stays exported: a dozen tools import it, and it is
    the right answer for the shipped roster. Prefer this wherever a store is in
    hand. `store=None` falls back to the constant rather than raising, so a
    caller without one degrades to the vanilla answer.
    """
    if store is None:
        return UNRESTRICTED_HEROES
    derived = {hero_id for hero_id, hd in getattr(store, "heroes", {}).items()
               if not getattr(hd, "can_select_combat_skills", True)}
    return frozenset(derived | UNRESTRICTED_HEROES)

# Trinket rarities that drop in ordinary play, from the game's OWN
# `trinkets/base.rarities.trinkets.json` -> `award_category: "universal"`.
# Everything else is gated behind something this simulator does not model:
#
#   kickstarter (294 trinkets!)  its own award category, backer flavour items
#   trophy                       boss-kill rewards
#   dd                           Darkest Dungeon runs only
#   battle / quest               Shambler, Collector, Courtier, Madman, Crow
#
# The filter matters more than it sounds. `kickstarter` alone is 294 of the 615
# loaded trinkets, so an unfiltered "random trinket" would be close to a coin
# flip on an item no dungeon ever awards. Universal leaves 213 -- 114 generic,
# the rest class-locked -- which is about 120 legal choices per hero.
UNIVERSAL_TRINKET_RARITIES = frozenset({
    "ancestral", "very_rare", "rare", "uncommon", "common", "very_common",
})

# A hero carries two trinkets; 494 of the 534 scraped comp heroes do.
RANDOM_TRINKETS_PER_HERO = 2

# How many quirks of each polarity a randomised hero carries, inclusive.
#
# CONVENTION, and a bigger distribution change than the trinkets are: the
# scraped comps almost never specify quirks at all (516 of 534 heroes have zero
# positive, 525 zero negative), so the policy has effectively never trained
# against the quirk subsystem even though the engine models it fully. The real
# game caps a hero at five of each and gives new recruits one or two, so 1-3 is
# a mid-campaign roster rather than a fresh or a maxed one. No shipped file
# states a distribution to copy, so this is a choice and is marked as one.
RANDOM_QUIRKS_PER_POLARITY = (1, 3)

# The Fire's Edge classes. A comp fielding one of them CANNOT be given a row in
# `COMPS/Comps <hash>.csv`, because `pick_representative_comps` samples that
# file and one extra row silently changes which comps every archived
# `--comp-seed` selects -- the same trap that keeps the tutorial party out of
# the pool. So they are registered as a loadout directory only, and reach the
# TRAINING pool through `_build_hero_pool` below while leaving every eval
# comparison byte-identical.
FIRES_EDGE_HEROES = frozenset({"runaway", "duelist"})

# Regular encounter categories. 'named' (scripted/special, e.g. rare summon
# variants) was already excluded to keep the distribution representative of
# normal random encounters -- but 'boss' and 'stall' were NOT, and they are the
# two that are least like a normal encounter (fixed 2026-08-20, see
# CHANGELOG.md):
#
#   boss:  .chance 1 .types siren_A                 <- an actual boss
#   boss:  .chance 1 .types pew_small_B ... prophet_B
#   stall: .chance 2 .types fishman_harpoon_A fishman_harpoon_A
#
# A `boss:` row is a real boss, which the game presents ONLY at the end of a
# boss quest and never in a random hallway or room. A `stall:` row is the
# REINFORCEMENT WAVE that spawns when the party stalls -- something the engine
# summons mid-combat, not an encounter anyone walks into. Measured before the
# fix, they were 3.3-6.1% and ~9% of every draw respectively, so roughly an
# eighth of all training combats were encounters that cannot occur.
#
# KNOWN GAP, deliberately not closed here: the policy now never trains against
# a boss at all. The faithful model is that SOME expeditions are boss quests
# whose LAST combat is the boss, which is a structural change to how an
# expedition is generated rather than a filter. Uniformly sprinkling bosses
# through every expedition -- what the old set did -- is not a closer
# approximation of that, it is a different wrong thing.
ENEMY_CATEGORIES_FOR_TRAINING = {"hall", "room"}

# The four core zones a sampled expedition can be routed through. A comp is
# tagged with ONE Location in its CSV/JSON, and until 2026-08-20 that tag was
# also the only zone it ever fought in -- so a Cove comp never once met a
# skeleton. Real parties are taken wherever the quest is, and a comp that only
# works in its suggested region is not a comp anyone would field.
CORE_ZONE_IDS = ("crypts", "warrens", "weald", "cove")

# A boss is the LAST fight of a boss quest, never a corridor encounter.
#
# `boss` was removed from ENEMY_CATEGORIES_FOR_TRAINING because sprinkling
# bosses uniformly through every expedition models nothing -- but excluding
# them outright left the policy never having seen one, which is its own
# problem the moment the bridge walks into a real boss room. This is the
# structural version: an expedition is EITHER a boss quest, whose final combat
# is that region's boss, or it is not.
#
# The rosters come straight from the mash files' own `boss:` rows and match
# the game exactly -- two static bosses per core region:
#
#   crypts    Necromancer          | Prophet (+ 3 pews)
#   cove      Siren                | Drowned Captain
#   warrens   Swine Prince (+ piglet) | Formless Flesh (4 parts)
#   weald     Hag (+ cauldron)     | Brigand Pounder (cannon crew)   [+ Nest]
#
# They sit on tiers 1/3/5, which are the real Apprentice/Veteran/Champion quest
# difficulties -- tiers 2 and 4 carry only a stray Necromancer row, so a boss
# quest snaps its tier to the bracket's canonical odd value rather than
# fielding that one boss half the time.
BOSS_QUEST_TIER_FOR = {1: 1, 2: 1, 3: 3, 4: 3, 5: 5}

# NOT modelled: the four ROAMING bosses (Shambler, Collector, Fanatic, Thing
# from the Stars), which spawn on their own conditions mid-expedition rather
# than sitting in the last room. They live in the `battle`/`quest` award
# categories and would need a spawn trigger, not a slot in the schedule.

# mash.darkest tier -> Resolve Level fielded to fight it (top of that tier's
# bracket — Apprentice/Veteran/Champion). Verified real HP scaling puts
# tiers 1-2 at Apprentice, 3-4 at Veteran, 5 at Champion (see
# ASSUMPTIONS.md "Status after curriculum training..." phase notes).
TIER_TO_HERO_LEVEL = {1: 2, 2: 2, 3: 4, 4: 4, 5: 6}

# Resolve levels a party is actually fielded at, PER HERO, and how often.
#
# `TIER_TO_HERO_LEVEL` above puts every hero at the TOP of its bracket, which
# is a best case rather than a party: a real Apprentice roster is a mix of
# resolve 0-2 heroes, not four resolve-2 ones. Sampling within the bracket is
# what makes the difficulty honest, and because gear rank is now derived from
# resolve (GEAR_RANK_RESOLVE_THRESHOLDS), a resolve-0 hero is correctly fielded
# at rank-1 gear without anything else being said.
#
# The weights are Fran's, and they deliberately lean high -- "lets favour the
# heroes a bit here". They are a training-distribution choice, not a claim
# about how often the game hands you a given roster; no shipped file states
# one.
#
#   Apprentice (mash tiers 1-2):  resolve 0 20% | 1 40% | 2 40%
#   Veteran    (mash tiers 3-4):  resolve 3 40% | 4 60%
#   Champion   (mash tier 5):     resolve 5 40% | 6 60%
#
# **A caller DOES pass it now, as of 2026-09-02, and this is the revisit that
# comment asked for.** `TIER_TO_QUEST_DIFFICULTY` below feeds
# `quest_difficulty_level`, so a resolve-0 hero on an Apprentice quest takes
# the real game's 20 stress before the first turn and +25% stress damage for
# the rest of the raid -- which is exactly what Fran's own play logs show and
# what the simulator was silently forgiving. The weights are unchanged: 20% of
# Apprentice heroes still field at resolve 0, and they are now charged for it.
# Mash tier -> the QUEST DIFFICULTY the game would gate that dungeon behind.
# Tiers 1-2 are Apprentice, 3-4 Veteran, 5 Champion (confirmed), and DD's own
# quest difficulties for those are 1 / 3 / 5 -- `town_state.Quest.difficulty`
# carries the same three values and `Quest.simulable` bounds them the same way.
#
# This is what makes the underleveled-hero penalty reachable. A hero whose
# resolve falls short of it takes `UNDERLEVELED_STRESS_PER_LEVEL` (20) per
# level on entry and `UNDERLEVELED_STRESS_DMG_MULT_PER_LEVEL` (+25%) per level
# on every stress event for the whole raid.
#
# Note which brackets it can actually fire in: `TIER_TO_RESOLVE_WEIGHTS` fields
# Veteran at resolve 3-4 against difficulty 3 and Champion at 5-6 against
# difficulty 5, so both are always at or above the bar. **Only the Apprentice
# bracket has a shortfall, and only for its 20% resolve-0 draw** -- which is
# the real game's own situation, since resolve 0 is what a fresh recruit is and
# the wiki says "Resolve level 0 heroes will always have this stress penalty on
# their first expedition".
TIER_TO_QUEST_DIFFICULTY = {1: 1, 2: 1, 3: 3, 4: 3, 5: 5}

TIER_TO_RESOLVE_WEIGHTS = {
    1: ((0, 0.2), (1, 0.4), (2, 0.4)),
    2: ((0, 0.2), (1, 0.4), (2, 0.4)),
    3: ((3, 0.4), (4, 0.6)),
    4: ((3, 0.4), (4, 0.6)),
    5: ((5, 0.4), (6, 0.6)),
}

# The torchlight a combat is actually fought at.
#
# Every expedition ever trained here ran at torch 100.0, because neither this
# env nor ExpeditionSequenceEnv._start_next_combat ever passed torch_level and
# CombatEngine.reset() defaults it. That is not a small omission: torch is IN
# the observation (env/gym_env.py, engine.torch_level / 100), so holding it
# constant made that feature carry zero information, AND it put the policy
# off-distribution for any fight in the dark — where the game's own rules.json
# changes crit, ACC, dodge, stress and surprise chance on both sides. It is not
# a corner case either: over 206 in-battle snapshots captured from real play,
# only 68% were radiant — 19% black, 11% dim, 2% dark (min 3, p25 62, median 89,
# p75 95).
#
# These are the deciles of that empirical distribution, interpolated within each
# bin, drawn from _sampler_rng (the scenario RNG) so a scenario reproduces its
# torch along with its comp and encounters.
#
# Drawn independently per combat rather than decayed across an expedition: the
# deciles are already the MARGINAL over real captures, which bakes in the
# player's own relighting behaviour, and nothing in Discrete(37) can act on the
# torch — so the within-expedition correlation would buy the policy nothing
# while costing a traversal model the simulator does not have.
TORCH_LEVEL_DECILES = (3.0, 25.0, 34.0, 62.0, 83.0, 89.0, 92.0, 94.0, 99.0, 100.0, 100.0)

# **The pitch-black band was UNREACHABLE, and the bridge fights there.**
#
# `rules.json`'s darkness table gives torch <= 0 its own band -- +35 stress
# chance, +40% stress damage, +5 monster crit, +30% loot gold -- and the
# deciles above start at 3.0, so the sampler could not produce it. Not rarely:
# **never**, in any training run or any eval this project has taken.
#
# The deciles are not wrong; they are STALE. They were measured from 206
# in-battle captures taken before `bridge/torchlight.py` existed, and that
# layer now snuffs the torch to 0 on purpose for the loot, gated on
# `about_to_win`. So the corpus describes a player who did not do the thing the
# bridge does.
#
# Measured on `bat/play.log`, Fran's own dungeons, by attributing each area
# entry to whether a fight actually followed it: **4 of 30 fights (13.3%)
# opened at pitch black**, while the rest of the in-combat distribution matches
# the deciles closely (63.3% radiant against 63.9%, mean 74.3 against 73.0).
# So this is the one place the sampler is wrong, and it is wrong by 13 points
# against zero.
#
# n = 30 is thin, which is why this is a named constant with the sample size in
# it rather than a re-fitted decile table: a bigger `play.log` should move the
# number, and `tools/play_log_probe.py --distribution` is what re-measures it.
TORCH_SNUFFED_P = 4.0 / 30.0


class ExpeditionEnv(DarkestDungeonEnv):
    #: The zone the last sampled scenario drew, for `in_dungeon` buff gating.
    _sampled_zone_id: Optional[str] = None

    def __init__(self, render_mode: Optional[str] = None, sampler_seed: Optional[int] = None,
                 store=None, loadout_random_p: float = 0.0,
                 cross_region_p: float = 0.0,
                 resolve_sampling: bool = False,
                 kit_random_p: float = 0.0,
                 boss_quest_p: float = 0.0,
                 hold_out_classes=frozenset(),
                 stress_weight: float = 1.0,
                 slot_permute: bool = False,
                 blind_forecast: bool = False,
                 prevention_reward: bool = False,
                 focus_mechanic: Optional[str] = None,
                 focus_p: float = 0.5,
                 class_balance: float = 0.0):
        super().__init__(render_mode=render_mode, store=store,
                         stress_weight=stress_weight,
                         slot_permute=slot_permute,
                         blind_forecast=blind_forecast,
                         prevention_reward=prevention_reward)
        # Classes this env must never field. See `build_comp_pool` -- this is
        # the training half of the transfer falsifier, and it is deliberately
        # NOT one of the five randomisation knobs: those vary the party within
        # a fixed pool, this changes which pool exists at all.
        self._hold_out_classes = frozenset(hold_out_classes)
        self._sampler_rng = random.Random(sampler_seed)
        # Resolved kit for the combat currently being built, read by
        # `_on_board_ready`. Lives for the duration of one `reset()` only.
        self._pending_kit = None
        # Probability that a hero is fielded with a RANDOM legal 4-of-7 rather
        # than the four its comp curated. 0.0 is the historical behaviour, byte
        # for byte -- every archived checkpoint trained at 0.0.
        #
        # Why it exists: the policy only ever sees a class holding the four
        # skills some comp author picked for it, so the same class with a
        # different four is off-distribution. Measured on the tutorial party,
        # distribution was worth 36pp -- larger than any fidelity fix this
        # project has ever landed.
        self._loadout_random_p = max(0.0, min(1.0, float(loadout_random_p)))
        # How often a comp is taken to a region other than the one it is
        # tagged with. 0.0 keeps the old behaviour exactly: every comp
        # fights only in its own Location.
        self._cross_region_p = max(0.0, min(1.0, float(cross_region_p)))
        # Draw each hero's resolve from its bracket instead of pinning the
        # whole party at the top of it. False keeps the old behaviour.
        self._resolve_sampling = bool(resolve_sampling)
        # Probability a hero is fielded with a random legal trinket pair
        # and quirk sheet instead of its comp's. 0.0 is the old behaviour.
        self._kit_random_p = max(0.0, min(1.0, float(kit_random_p)))
        # How hard to correct the pool's class skew when DRAWING a comp.
        # Houndmaster fills 102 hero slots and Duelist 12, so uniform sampling
        # practises one nine times more than the other. 0.0 is uniform and is
        # every archived checkpoint; 1.0 weights fully by inverse frequency.
        #
        # A weight and never a filter -- every comp stays reachable at every
        # setting. And it is a TRAINING dial only: which classes are strong,
        # and where, is a fact about the game that must keep showing up in
        # evaluation, so nothing in `tools/comp_leaderboard.py` reads this.
        self._class_balance = max(0.0, min(1.0, float(class_balance)))
        # Probability an expedition is a boss quest, i.e. its LAST combat
        # is the region's boss. 0.0 means no boss is ever fielded, which
        # is the state every checkpoint so far trained in.
        self._boss_quest_p = max(0.0, min(1.0, float(boss_quest_p)))
        # A SEPARATE stream for the loadout draw, and the separation is the
        # whole point. Drawing loadouts from `_sampler_rng` would advance the
        # generator that also picks the monsters and the torch, so a curated
        # arm and a randomised arm would face DIFFERENT dungeons -- which is
        # exactly the "measuring luck instead of policy" failure
        # `tools/ab_compare.py` exists to prevent. Independent, so the enemy
        # stream is bit-identical whatever p is.
        self._loadout_rng = random.Random(sampler_seed)
        self._hero_pool = self._build_hero_pool()
        # `--focus-mechanic`: specialise on what a comp CAN DO, never on which
        # comps it is. Empty unless asked for, so the sampler is unchanged.
        self.focus_p = float(focus_p)
        self._focus_pool = self._build_focus_pool(focus_mechanic)
        if focus_mechanic and not self._focus_pool:
            raise ValueError(
                f"no comp in the pool can play {focus_mechanic!r} -- a focus "
                "that matches nothing would train on the full pool and look "
                "like a successful experiment")
        # {id(pool): [weight per comp]}, or empty while balancing is off.
        self._comp_weights = {}
        if self._class_balance > 0.0:
            for pool in (self._hero_pool, self._focus_pool):
                if pool:
                    self._comp_weights[id(pool)] = self._balance_weights(pool)
        self._enemy_pool_by_zone = self._build_enemy_pool()
        self._boss_pool_by_zone = self._build_boss_pool()
        if not self._hero_pool:
            raise RuntimeError("ExpeditionEnv: no usable hero comps found in store.comps")

    def _build_hero_pool(self) -> list[tuple[str, str, list[str]]]:
        """Built from `store.comp_loadouts` -- the per-comp JSON -- and from
        nothing else. `COMPS/Comps <hash>.csv` was the source until 2026-08-23
        and is retired; the JSON was already authoritative for hero identity,
        order and COUNT, so the CSV only ever supplied the LIST of comps and
        their zone tag, and it supplied both badly:

        - alphabetically sorted, not the real front-to-back formation, and
          `CombatEngine.reset()` assigns `position = index` -- so the CSV's
          order silently reformed every party ("Pigpen Cleanup"'s real
          Hellion-front order alphabetises to Arbalest-front);
        - it DEDUPLICATES repeated hero classes, so "Ascetic Movement" --
          really 3 distinct Crusaders + 1 Flagellant -- trained as 2 heroes;
        - and after the comp folder was re-exported, 113 of its 145 rows named
          comps that no longer exist. `--comp-seed 1` resolved 1 of 12.

        Dropping it also retires `_fires_edge_comps`, which existed for exactly
        one reason: a comp fielding a Fire's Edge class could not be given a
        CSV row without shifting which comps every archived `--comp-seed`
        picks. With no CSV there is no row to withhold and no archived seed
        left to protect, so every loadout comp now enters by the same rule.
        """
        return build_comp_pool(self.store,
                               hold_out_classes=self._hold_out_classes)

    def _balance_weights(self, pool) -> list[float]:
        """One draw weight per comp, correcting the pool's class skew.

        A class's frequency is counted in HERO SLOTS, not in comps: a comp
        fielding three Crusaders practises the Crusader three times, and
        counting it once would leave exactly the duplicate-heavy comps
        under-corrected.

        The interpolation is linear between uniform and fully inverse, so
        `class_balance` reads as "how much of the skew to remove" rather than
        as an exponent nobody can predict. Weights are normalised to mean 1.0
        so the number means the same thing whatever the pool size.
        """
        slots: dict[str, int] = {}
        for _name, _zone, heroes in pool:
            for hero_id in heroes:
                slots[hero_id] = slots.get(hero_id, 0) + 1

        raw = []
        for _name, _zone, heroes in pool:
            if heroes:
                raw.append(sum(1.0 / slots[h] for h in heroes) / len(heroes))
            else:
                raw.append(0.0)
        mean = (sum(raw) / len(raw)) if raw else 1.0
        if mean <= 0:
            return [1.0] * len(pool)
        b = self._class_balance
        return [(1.0 - b) + b * (r / mean) for r in raw]

    def _build_boss_pool(self) -> dict:
        """{zone_id: [EnemyCompEntry, ...]} for the `boss` category only.

        A separate pool from `_enemy_pool_by_zone` on purpose: nothing that
        samples a corridor encounter can reach these, which is the property
        that was missing when `boss` sat inside
        ENEMY_CATEGORIES_FOR_TRAINING.
        """
        by_zone: dict = {}
        for entry in self.store.enemy_comps:
            if entry.category != "boss" or entry.zone_id not in CORE_ZONE_IDS:
                continue
            by_zone.setdefault(entry.zone_id, []).append(entry)
        return by_zone

    def _build_focus_pool(self, mechanic: Optional[str]) -> list:
        """The subset of the training pool that can play one MECHANIC.

        Built from `comp_mechanics`, which reads the equipped kit's effects --
        so a modded class that applies bleed joins the bleed family with no
        entry anywhere. That is the whole point: Fran does not want a policy
        that *"follows the same plan always, as this wont enable the use of
        modded classes and will hurt our scalability on the future"*.
        """
        if not mechanic:
            return []
        if mechanic not in MECHANIC_FAMILIES:
            raise ValueError(
                f"unknown mechanic {mechanic!r}; known: {list(MECHANIC_FAMILIES)}")
        def pool_at(threshold):
            return [entry for entry in self._hero_pool
                    if mechanic in comp_mechanics(
                        self.store,
                        self.store.comp_loadout_by_name(entry[0]),
                        min_sources=threshold)]

        out = pool_at(FOCUS_MIN_SOURCES)
        if len(out) < FOCUS_MIN_POOL:
            # Relaxed rather than shipped thin -- a two-comp focus taken half
            # the time is a smaller training set wearing a knob's clothes.
            out = pool_at(1)
        return out

    def _sample_boss(self, zone_id: str, tier=None):
        """The boss waiting at the end of a boss quest, or None.

        Drawn from `_sampler_rng` -- unlike the four randomisation knobs, WHICH
        boss is part of the scenario in the same way the corridor encounters
        are, so it belongs to the stream that a reseeded scenario reproduces
        along with the monsters and the torch.
        """
        candidates = self._boss_pool_by_zone.get(zone_id, [])
        if not candidates:
            return None
        want = BOSS_QUEST_TIER_FOR.get(tier, tier)
        tiered = [c for c in candidates if c.tier == want]
        pool = tiered or candidates
        weights = [max(c.chance, 0.01) for c in pool]
        return self._sampler_rng.choices(pool, weights=weights, k=1)[0]

    def _build_enemy_pool(self) -> dict[str, list]:
        by_zone: dict[str, list] = {}
        for entry in self.store.enemy_comps:
            if entry.category not in ENEMY_CATEGORIES_FOR_TRAINING:
                continue
            by_zone.setdefault(entry.zone_id, []).append(entry)
        return by_zone

    def _sample_comp(self) -> tuple[str, str, list[str]]:
        """(comp_name, zone_id, hero_ids) — the hero-comp half of scenario
        sampling, split out from _sample_scenario so ExpeditionSequenceEnv
        can sample a comp once per expedition while still re-sampling the
        enemy encounter (_sample_enemy) once per combat within it.

        With `cross_region_p`, the zone is drawn independently of the comp's
        own tag instead of being taken from it. See `_sample_zone_for`.
        """
        pool = self._hero_pool
        if self._focus_pool and self._sampler_rng.random() < self.focus_p:
            # HALF the episodes from the focus family, half from the whole
            # pool. Half-and-half is the design, not a hedge: a pure focus run
            # is just a smaller training set, and Fran asked to *"make it a bit
            # more specialist on certain comps/archetipes WITHOUT losing that
            # expertise"*.
            pool = self._focus_pool
        # `choice` is KEPT for the unbalanced path rather than passing
        # uniform weights to `choices`: the two consume the RNG differently, so
        # swapping them would silently reproduce a different episode stream
        # from the same seed and quietly invalidate every archived arm.
        weights = self._comp_weights.get(id(pool))
        if weights is None:
            comp_name, zone_id, hero_ids = self._sampler_rng.choice(pool)
        else:
            comp_name, zone_id, hero_ids = self._sampler_rng.choices(
                pool, weights=weights, k=1)[0]
        return comp_name, self._sample_zone_for(zone_id), hero_ids

    def sample_hero_levels(self, tier: Optional[int], n_heroes: int) -> list[int]:
        """One resolve level per hero for a dungeon of this mash tier.

        Off by default, in which case every hero sits at the top of the
        bracket exactly as before. On, each hero is drawn INDEPENDENTLY --
        a party is four separate heroes off a roster, and drawing once for
        the party would field four clones and reproduce the very thing this
        replaces.

        Drawn from `_loadout_rng` for the same reason the loadout and the
        region are: `_sampler_rng` also picks the monsters and the torch, so
        spending draws here would shift the enemy sequence and make two arms
        of a comparison face different dungeons.
        """
        if not self._resolve_sampling:
            return [TIER_TO_HERO_LEVEL.get(tier, 6)] * n_heroes
        table = TIER_TO_RESOLVE_WEIGHTS.get(tier)
        if not table:
            return [TIER_TO_HERO_LEVEL.get(tier, 6)] * n_heroes
        levels, weights = zip(*table)
        return [self._loadout_rng.choices(levels, weights=weights, k=1)[0]
                for _ in range(n_heroes)]

    def _sample_zone_for(self, tagged_zone: str) -> str:
        """Which region this comp actually gets taken to.

        A comp carries exactly one Location, and that tag used to be the only
        region it ever fought in -- so a Cove comp never met a skeleton and a
        Ruins comp never met a fishman. That is not how a roster is used: the
        quest decides the region, and a comp that only works in its suggested
        one is not a comp worth having.

        Drawn from `_loadout_rng`, NOT `_sampler_rng`, for the same reason the
        loadout draw is: `_sampler_rng` also picks the monsters and the torch,
        so consuming from it here would shift the enemy sequence and make two
        arms of a comparison face different dungeons. `seed_scenario` pins
        both streams, so a reseeded scenario still reproduces its region.
        """
        if self._cross_region_p <= 0.0:
            return tagged_zone
        if self._loadout_rng.random() >= self._cross_region_p:
            return tagged_zone
        return self._loadout_rng.choice(CORE_ZONE_IDS)

    def _sample_enemy(self, zone_id: str, tier: Optional[int] = None):
        """A single EnemyCompEntry, weighted by real .chance, from the given
        zone — optionally restricted to one mash-file tier (so a full
        expedition can keep one consistent difficulty across all its
        combats rather than re-rolling tier every fight). Falls back to the
        zone's full tier range if the tier has no entries (shouldn't happen
        with real data, but defensive)."""
        candidates = self._enemy_pool_by_zone.get(zone_id, [])
        if tier is not None:
            tier_candidates = [c for c in candidates if c.tier == tier]
            if tier_candidates:
                candidates = tier_candidates
        weights = [max(c.chance, 0.01) for c in candidates]
        return self._sampler_rng.choices(candidates, weights=weights, k=1)[0]

    def _resolve_skill_ids(self, comp_name: str, hero_ids: list[str]) -> Optional[list[list[str]]]:
        """Real 4-of-~7 skill loadout (COMPS/curated_comps/*.json, via
        store.comp_loadouts) for this exact comp, if one was found by name
        — 131/145 CSV comps match. Falls back to None (full movepool,
        pre-existing behavior) for the ~14 that don't, e.g. modded-hero
        comps with no scraped JSON.

        Aligned to hero_ids by POSITION, not by hero_id lookup — a
        hero_id-keyed dict silently collapses a duplicate-class comp (37
        of 131 loadout comps have one, e.g. "Ascetic Movement"'s 3
        distinct Crusaders) down to whichever one of them got inserted
        last, applying the WRONG skills to the other two. Safe because
        _build_hero_pool builds hero_ids from this exact same filtered
        loadout.heroes list, in the same order — the equality check below
        is a defensive guard against that invariant ever breaking, not the
        normal path.

        Abomination is a special case: unlike every other hero, he always
        has all 7 combat skills equipped in the real game (no 4-skill
        loadout choice exists for him at all — confirmed via HEROES/Heroes'
        own doc: "Unlike other heroes who can only equip 4 combat skills,
        Abomination always has all 7 combat skills equipped"). The scraped
        comp JSON still lists exactly 4 "activeSkills" for him regardless
        (the source tool has no Abomination carve-out), which would
        otherwise wrongly strip 3 of his real skills — confirmed on "Stun
        Control: Beast"'s 2nd Abomination, whose JSON list is missing ALL
        THREE of his beast-form attacks (Rake/Rage/Slam), leaving him
        unable to deal any damage at all while transformed. His entry is
        left unrestricted ([]) here regardless of what the JSON says."""
        loadout = self.store.comp_loadout_by_name(comp_name)
        if not loadout:
            return None
        heroes = [h for h in loadout.heroes if h.hero_id in self.store.heroes]
        if [h.hero_id for h in heroes] != hero_ids:
            return None
        resolved = []
        unrestricted = unrestricted_heroes(self.store)
        for h in heroes:
            if h.hero_id in unrestricted:
                # Unrestricted whatever the mode: the real game gives these
                # heroes all seven always, so there is no 4-of-7 to randomise.
                resolved.append([])
            elif not h.skill_ids:
                return None
            elif (self._loadout_random_p
                  and self._loadout_rng.random() < self._loadout_random_p):
                resolved.append(self._random_skill_ids(h.hero_id))
            else:
                resolved.append(h.skill_ids)
        return resolved

    def seed_scenario(self, ep_seed: int) -> None:
        """Reseed everything that defines a scenario: monsters, torch, loadouts.

        Callers that pin a scenario have historically reseeded `_sampler_rng`
        by hand (`Rollout.evaluate`, `tools/ab_compare.py::play`). Loadouts are
        a second stream, so pinning a scenario now means pinning both -- this
        is the one place that knows that, rather than three call sites each
        remembering.
        """
        self._sampler_rng.seed(ep_seed)
        self._loadout_rng.seed(ep_seed)

    def _random_skill_ids(self, hero_id: str) -> list[str]:
        """A random legal loadout for one hero: 4 distinct of their 7.

        Drawn from `_sampler_rng`, the SCENARIO generator, never the combat
        RNG -- so reseeding a scenario reproduces its loadouts along with its
        monsters and its torch. `Rollout.evaluate` depends on exactly that to
        rank two parties over the same dungeon rather than over different luck.
        """
        hero = self.store.heroes.get(hero_id)
        ids = sorted({s.id for s in hero.skills}) if hero else []
        if len(ids) <= SKILLS_PER_HERO:
            return list(ids)
        return sorted(self._loadout_rng.sample(ids, SKILLS_PER_HERO))

    def _random_trinket_ids(self, hero_id: str) -> list[str]:
        """Two legal trinkets for this hero: generic or locked to their class,
        and droppable in ordinary play.

        Empty `class_requirements` means generic; otherwise the hero's own id
        must appear in it. That is the whole of the class-specific/generic
        distinction, and it is data rather than a list maintained here.
        """
        pool = sorted(t.id for t in self.store.trinkets.values()
                      if t.rarity in UNIVERSAL_TRINKET_RARITIES
                      # A trinket with no buffs is an empty slot here. Only 3
                      # of the 213 universal ones are (`flickering_lamplight`,
                      # whose real effect is torchlight, and two Runaway mod
                      # items), but handing one out teaches the policy from a
                      # hero wearing a no-op, so they are left out rather than
                      # silently wasted. Note `limit == 0` is NOT a validity
                      # signal -- it means "no limit" and 192 of 213 have it.
                      and t.buffs
                      and (not t.class_requirements
                           or hero_id in t.class_requirements))
        if len(pool) <= RANDOM_TRINKETS_PER_HERO:
            return pool
        return sorted(self._loadout_rng.sample(pool, RANDOM_TRINKETS_PER_HERO))

    def _incompatible_with(self, quirk_id: str) -> set:
        """Everything that cannot coexist with this quirk, BOTH ways round.

        The data states the pair from one side only -- `tough` names `fragile`
        and `fragile` does not name `tough` -- so checking a single direction
        would happily produce a hero who is tough and fragile at once: two
        buffs on the same stat pulling opposite ways, a state the game cannot
        reach.
        """
        out = set(self.store.quirks[quirk_id].incompatible_quirks)
        out.update(other.id for other in self.store.quirks.values()
                   if quirk_id in other.incompatible_quirks)
        return out

    def _random_quirk_ids(self) -> list[str]:
        """A plausible quirk sheet: some positive, some negative, no diseases,
        nothing self-contradictory."""
        chosen: list[str] = []
        blocked: set = set()
        lo, hi = RANDOM_QUIRKS_PER_POLARITY
        for positive in (True, False):
            pool = sorted(q.id for q in self.store.quirks.values()
                          if q.is_positive is positive and not q.is_disease)
            wanted = self._loadout_rng.randint(lo, hi)
            # Walk a shuffled pool and take the first `wanted` that are still
            # compatible, rather than re-sampling until something fits.
            for qid in self._loadout_rng.sample(pool, len(pool)):
                if wanted <= 0:
                    break
                if qid in blocked:
                    continue
                chosen.append(qid)
                blocked.add(qid)
                blocked |= self._incompatible_with(qid)
                wanted -= 1
        return chosen

    def _random_kit(self, hero_id: str) -> tuple:
        """(trinket_ids, quirk_ids) for one hero, ignoring what its comp says."""
        return self._random_trinket_ids(hero_id), self._random_quirk_ids()

    def _resolve_trinkets_and_quirks(self, comp_name: str, hero_ids: list[str]
                                      ) -> Optional[list[tuple[list[str], list[str]]]]:
        """Real 2 trinkets + quirks per hero (COMPS/curated_comps/*.json,
        via store.comp_loadouts) — same positional-alignment convention as
        _resolve_skill_ids (see its docstring for why a hero_id-keyed
        lookup silently breaks on duplicate-class comps). Applied via the
        pre-existing CombatEngine.apply_trinkets_and_quirks, which nothing
        called before this — quirk_ids' positive/negative split doesn't
        matter for application, both resolve to real buff ids the same
        way, so they're flattened together."""
        loadout = self.store.comp_loadout_by_name(comp_name)
        if not loadout:
            return None
        heroes = [h for h in loadout.heroes if h.hero_id in self.store.heroes]
        if [h.hero_id for h in heroes] != hero_ids:
            return None
        out = []
        for h in heroes:
            if self._kit_random_p and self._loadout_rng.random() < self._kit_random_p:
                out.append(self._random_kit(h.hero_id))
            else:
                out.append((h.trinket_ids,
                            h.quirk_ids.get("positive", [])
                            + h.quirk_ids.get("negative", [])))
        return out

    def _apply_trinkets_and_quirks(self, resolved: list[tuple[list[str], list[str]]]):
        for hero_combatant, (trinket_ids, quirk_ids) in zip(self.engine.heroes, resolved):
            if trinket_ids or quirk_ids:
                self.engine.apply_trinkets_and_quirks(hero_combatant, trinket_ids, quirk_ids)

    def _sample_torch_level(self) -> float:
        """One draw from the measured real-play torch distribution
        (TORCH_LEVEL_DECILES), linearly interpolated inside whichever decile bin
        the draw lands in so the result is continuous rather than 11 spikes.

        **With an explicit point mass at 0** -- see `TORCH_SNUFFED_P`. The
        pitch-black band is `torch <= 0`, and interpolating a bin down to 0.0
        would not reach it: a continuous draw over [0, 25] lands ON zero with
        probability zero. A snuffed fight is a discrete event -- the player
        clicked the meter out -- so it is sampled as one.

        Drawn from `_sampler_rng` like everything else about a scenario, so a
        reseeded scenario reproduces its darkness along with its monsters.
        """
        if self._sampler_rng.random() < TORCH_SNUFFED_P:
            return 0.0
        # **The snuffed mass REPLACES the bottom decile, it does not stack on
        # top of it.** The 206-capture corpus had a minimum of 3, so its lowest
        # bin (3..25) is where a snuffed-and-partly-relit fight already landed
        # -- adding a point mass at 0 without removing it would count the same
        # darkness twice and drag the mean down by ten points.
        #
        # Real play agrees that the bin should go: of 30 logged fights, **none
        # opened in `dark` or `black`** (63.3% radiant, 23.3% dim, 13.3% pitch
        # black). A fight is either lit or snuffed; the in-between is where a
        # party is walking, not fighting.
        u = 1.0 + self._sampler_rng.random() * 9.0
        i = min(int(u), 9)
        lo, hi = TORCH_LEVEL_DECILES[i], TORCH_LEVEL_DECILES[i + 1]
        return lo + (hi - lo) * (u - i)

    def _sample_scenario(self) -> tuple[str, list[str], list[str], list[int], Optional[list[list[str]]]]:
        comp_name, zone_id, hero_ids = self._sample_comp()
        entry = self._sample_enemy(zone_id)
        hero_levels = self.sample_hero_levels(entry.tier, len(hero_ids))
        hero_skill_ids = self._resolve_skill_ids(comp_name, hero_ids)
        # Stashed rather than returned: the tuple's shape is asserted by tests
        # and read positionally at the call site, and the zone is needed one
        # layer down in `reset` rather than here.
        self._sampled_zone_id = zone_id
        # Stashed for the same reason, and read by `reset` one layer down: the
        # underleveled-hero penalty needs the QUEST difficulty this tier would
        # be gated behind, not the tier itself.
        self._quest_difficulty_level = TIER_TO_QUEST_DIFFICULTY.get(entry.tier)
        return comp_name, hero_ids, entry.enemy_ids, hero_levels, hero_skill_ids

    def reset(self, *, seed: Optional[int] = None, options: Optional[dict] = None):
        options = dict(options or {})
        comp_name = None
        hero_ids = None
        # An EXPLICIT scenario keeps the old behaviour, the same rule the kit
        # and loadout knobs follow: a caller who names the party and the
        # monsters has said everything, and inventing a quest difficulty for it
        # would charge stress nobody asked for. Read from the options rather
        # than blanked, so the value `ExpeditionSequenceEnv` passes through for
        # its opening combat survives -- and so the attribute still describes
        # the run after `reset` returns instead of reading stale.
        self._quest_difficulty_level = options.get("quest_difficulty_level")
        if "hero_ids" not in options and "enemy_ids" not in options:
            comp_name, hero_ids, enemy_ids, hero_levels, hero_skill_ids = self._sample_scenario()
            options["hero_ids"] = hero_ids
            options["enemy_ids"] = enemy_ids
            options.setdefault("hero_levels", hero_levels)
            if hero_skill_ids is not None:
                options.setdefault("hero_skill_ids", hero_skill_ids)
            # Same gap, same fix as ExpeditionSequenceEnv: a randomized combat
            # that always ran at torch 100 taught the policy nothing about the
            # torch feature and never showed it the dark. Sampling lives on the
            # subclass because that is where the constant and its evidence are.
            options.setdefault("torch_level", self._sample_torch_level())
            # The region, so `in_dungeon`-gated quirks fire only where they
            # belong. Sampled path only: an explicit scenario is explicit, the
            # same rule the kit and loadout knobs follow.
            options.setdefault("zone_id", self._sampled_zone_id or "")
        # Resolved BEFORE the board is built, applied by `_on_board_ready`
        # while it is being built. It used to be applied after `super().reset()`
        # returned, which is after the opening enemy turns had already landed
        # on an untrinketed party.
        #
        # Only on the auto-sampled path, matching test_explicit_options_
        # bypass_sampling's contract: explicit hero_ids/enemy_ids behaves
        # exactly like the base DarkestDungeonEnv, no real-loadout enrichment.
        self._pending_kit = (self._resolve_trinkets_and_quirks(comp_name, hero_ids)
                             if comp_name and hero_ids else None)
        try:
            return super().reset(seed=seed, options=options)
        finally:
            self._pending_kit = None

    def _on_board_ready(self):
        if self._pending_kit:
            self._apply_trinkets_and_quirks(self._pending_kit)
