"""The engine's tuning constants, and the three helpers that read them.

Split out of `combat_engine.py` on 2026-09-08, verbatim and with every comment
attached. They sat in 180 lines of preamble ahead of a 6,000-line class, and
the mixins that class is being split into all need some of them -- importing
them back out of `combat_engine` would be a cycle, so they live below it
instead.

**Every name here is re-exported from `engine.combat_engine`**, which is where
the rest of the repo imports them from: `DEATHBLOW_RESIST_CAP`,
`CORPSE_ALIVE_ROUND_LIMIT`, `BOARD_RANKS`, `MORTALITY_TIER_RANK`,
`IN_COMBAT_PROVISION_IDS`, `GEAR_RANK_RESOLVE_THRESHOLDS`,
`UNDERLEVELED_STRESS_*` and `max_gear_rank_for_resolve` all have importers
outside `engine/`. Nothing about where they are defined is visible to them.

This module imports nothing from this repo, on purpose: it is the bottom of
the engine, and anything it reached for would be reaching sideways.
"""

INLINE_STAT_FIELD_MAP = [
    ("damage_low_multiply", "combat_stat_multiply", "damage_low"),
    ("damage_high_multiply", "combat_stat_multiply", "damage_high"),
    ("damage_low_add", "combat_stat_add", "damage_low"),
    ("damage_high_add", "combat_stat_add", "damage_high"),
    ("attack_rating_add", "combat_stat_add", "attack_rating"),
    ("crit_chance_add", "combat_stat_add", "crit_chance"),
    ("protection_rating_add", "combat_stat_add", "protection_rating"),
    ("defense_rating_add", "combat_stat_add", "defense"),
    ("speed_add", "combat_stat_add", "speed"),
    ("max_hp_percent", "combat_stat_multiply", "max_hp"),
]

# Real free (turn-preserving) in-combat provisions — food is a real
# provision too but has no in-combat effect record (a between-combat
# concern handled at the expedition-sequencing layer, not here), so it's
# deliberately excluded even if present in provision_inventory.
# Wiki-confirmed hard cap on Death Blow resist, regardless of quirks/trinkets.
DEATHBLOW_RESIST_CAP = 0.87

# Wiki-confirmed (darkestdungeon.wiki.gg/wiki/Critical_Hit): a hero's heal
# has its own fixed crit chance, entirely separate from a normal attack's
# crit roll (attacker.crit + skill.crit) -- no stat scales it. Single-target
# heals crit at HEAL_CRIT_CHANCE_SINGLE_TARGET; a multi-target heal (AoE
# heal skill, or a party-wide named-effect heal) rolls HEAL_CRIT_CHANCE_
# PER_TARGET independently for each target hit. A crit heal doubles the
# applied amount and cuts the target's stress by HEAL_CRIT_STRESS_REDUCTION.
# No rules.json field backs any of these three numbers -- same "hardcoded
# in the binary" treatment as DEATHBLOW_RESIST_CAP. (This used to cite
# HIDDEN_ACC_BONUS as the other example. That constant is gone: the binary
# says the 5% floor is a roll cutoff, not an accuracy bonus -- DECOMP.md F4.
# These three are still unbacked by any shipped file and still unverified
# against the binary, so the caveat stands for them.)
HEAL_CRIT_CHANCE_SINGLE_TARGET = 0.12
HEAL_CRIT_CHANCE_PER_TARGET = 0.05
HEAL_CRIT_STRESS_REDUCTION = 4.0

# Wiki-confirmed underleveled-hero penalty: "20 stress for every level of
# difference" and "+25% stress for every level of difference" to incoming
# stress damage, when a hero's resolve level is below the expedition's own
# difficulty level (Apprentice/Veteran/Champion = quest difficulty 1/3/5 on
# the same 1-6 scale as hero_levels). No rules.json field backs this, same
# "hardcoded in the binary" treatment as TIER_TO_HERO_LEVEL. See
# CombatEngine.reset()'s quest_difficulty_level param.
UNDERLEVELED_STRESS_PER_LEVEL = 20.0
UNDERLEVELED_STRESS_DMG_MULT_PER_LEVEL = 0.25

# How many full rounds a corpse blocks its rank before rotting away. Real
# value, read straight off the game's own corpse monster class:
# monsters/corpse/corpse_A/corpse_A.info.darkest carries
# `life_time: .alive_round_limit 3`. Corpses used to persist for the whole
# combat here (nothing ever cleared them except an explicit
# .kill_enemy_types corpse effect), which — stacked on top of the two
# corpse bugs fixed alongside this — left the board far more clogged than
# the real game's, systematically penalising melee/front-rank comps that
# need the enemy line to collapse forward to reach anything.
CORPSE_ALIVE_ROUND_LIMIT = 3

# Ranks per side. The rear-most rank a combatant can stand on is
# BOARD_RANKS - size, since it occupies `size` consecutive ranks going back.
BOARD_RANKS = 4

# The affliction baseline (-15 resists, -10% MAX HP) and the act-out rate used
# to live here as a SIMULATOR CONVENTION, on the belief that "Fearful" appeared
# nowhere in the install except localization and base.buffs.json and so the real
# tables had to be binary-only. They are not: shared/trait/trait_library.json
# carries both, per personality, and it disagreed with the convention on four
# counts. See data/trait_loader.py and CombatEngine._maybe_act_out.
#
# The one act-out amount the data genuinely does NOT specify: Vigorous's
# `heal_self` row carries no effect name, so the amount is a declared
# convention. A tenth of max HP is the smallest meaningful step and keeps the
# weakest virtue from out-healing a real heal skill.
VIRTUE_SELF_HEAL_MAX_HP_FRACTION = 0.10


def _crit_dot_rounds(rounds: int, is_crit: bool) -> int:
    """Bleed/Blight from a critical hit last "50% longer, rounded up, or 2
    additional turns for most skills" (the standard 3-round DoT becomes 5)."""
    if not is_crit:
        return rounds
    import math
    return math.ceil(rounds * 1.5)

# **THE GAME DOES NOT ALLOW THIS, AND SAYS SO IN AS MANY WORDS.**
# `localization/miscellaneous.string_table.xml` ships
# `cant_use_items_in_battle` -- "Can't use items during battle" -- twice, once
# plainly and once as `str_user_information_cant_use_items_in_battle`, the
# prefix the game uses for a refusal it shows the player, and translated
# ("Objets inutilisables en combat"). Provisions are used in a hallway or a
# cleared room, between fights.
#
# So these four were a simulator invention: four FREE, turn-preserving actions
# a real party can never take. Fran went looking for the opposite defect --
# *"our model really lacks on the bat file is the usage of bandages, antivenom
# and other provisions"* -- and the bridge was right all along to report no
# button, because there is no button.
#
# The waste this file already recorded is the tell, in hindsight: 82% of the
# trained policy's provision uses were wasted, 274 of 278 laudanums drunk with
# no Horror. A policy will happily learn to spam a free action; it cannot learn
# that the action does not exist.
#
# **The provisions still matter -- they moved OUT of combat**, to the
# between-rooms moment that already models eating, where the game actually
# puts them. See `ExpeditionSequenceEnv._use_provisions_between_combats`.
#
# The set is kept and exported because the ACTION INDICES stay where they are:
# `PROVISION_ACTION_ORDER` is `[32..35]` and `ACTION_SPACE_SIZE` is unchanged,
# so every archived checkpoint still loads. The actions are simply never legal
# now, exactly as `retreat` is never legal on a surprised opening round.
IN_COMBAT_PROVISIONS_ALLOWED = False

IN_COMBAT_PROVISION_IDS = {"bandage", "antivenom", "holy_water", "laudanum"}

# No rules.json source for this (there's no real "voluntarily do nothing"
# UI option in the base game — every hero normally has some legal action).
# Only offered as a last-resort fallback when get_valid_actions would
# otherwise be empty (e.g. a boxed-in backline hero with no reachable
# skill or Move target) — simulator convention, magnitude matches
# stall_stress's own +5/round for "nothing productive happening this round".
PASS_TURN_STRESS = 5.0

# Wiki-sourced (no rules.json key exposes this — confirmed absent
# project-wide, same "hardcoded in the binary" situation as the expedition-
# length bands): a hero who is Afflicted and reaches 200 stress suffers a
# heart attack — HP to 0 / Death's Door (or instant death if already
# there, bypassing the deathblow-resist roll), stress reset to 170 (not 0,
# not left at 200).
HEART_ATTACK_STRESS_THRESHOLD = 200.0
HEART_ATTACK_RESULTING_STRESS = 170

# Wiki-confirmed (darkestdungeon.wiki.gg/wiki/Stalling): when every living
# enemy has the real .accelerate_stall_penalty flag, reinforcements arrive
# after 2 stall rounds instead of the normal rules.stall_round_summon_
# effect_threshold (4). No rules.json field directly names "2" for this —
# stall_round_accelerated_only_increment/_consecutive_increment clearly
# drive the same real mechanic, but their exact arithmetic couldn't be
# pinned down with confidence, so the wiki-confirmed outcome is applied
# directly instead. See CombatEngine._check_stall.
ACCELERATED_STALL_SUMMON_THRESHOLD = 2

# Sticky, monotonic Death's Door recovery tier ranking — real buff amounts
# (opposite-signed stress terms between the two tiers) confirm these
# replace rather than stack; a hero's recovery tier only ever moves up.
MORTALITY_TIER_RANK = {"none": 0, "mortality": 1, "heart_attack": 2}


# Minimum resolve level to buy each weapon/armour upgrade, straight out of
# `upgrades/heroes/<class>.upgrades.json` -> each tree's
# `prerequisite_resolve_level`. Verified IDENTICAL across all 15 vanilla classes
# for BOTH the weapon and the armour tree, which is what makes one table right
# rather than a per-class approximation (`tests/engine/test_gear_ranks.py` re-reads
# every file and fails if any class disagrees).
#
# A hero owns rank 1 for free, so the highest rank affordable at resolve R is
# `1 + how many of these thresholds R has passed`:
#
#     resolve  0  1  2  3  4  5  6
#     rank     1  2  3  4  4  5  5
#
# This REPLACES the old `tier_idx = level - 1` convention, whose own comment
# conceded it was "a deliberate simulator convention ... not a literal game
# rule". It was right for veterans and champions and wrong for apprentices: a
# resolve-2 hero was fielded at rank 2 when the game lets them carry rank 3, so
# every Apprentice-bracket combat was fought a full gear rank under-equipped.
GEAR_RANK_RESOLVE_THRESHOLDS = (1, 2, 3, 5)


def _int_stat_snapshot(c) -> dict:
    """The three integer stats `combat_stat_multiply` can touch.

    Snapshotted before a trait's buffs go on so the cure can put them back
    exactly; see `CombatantState.overstress_stats` for why reversing does not
    round-trip.
    """
    return {"max_hp": c.max_hp, "dmg_min": c.dmg_min, "dmg_max": c.dmg_max}


def max_gear_rank_for_resolve(resolve_level: int) -> int:
    """Highest weapon/armour rank (1-5) a hero at this resolve may own."""
    return 1 + sum(1 for t in GEAR_RANK_RESOLVE_THRESHOLDS if resolve_level >= t)
