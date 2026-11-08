from __future__ import annotations
import math
from dataclasses import dataclass
from typing import Optional
from .combatant import CombatantState
from .rng import SeededRNG
from data.types import CombatRules, SkillLevelData


@dataclass
class AttackResult:
    hit: bool = False
    is_crit: bool = False
    raw_damage: float = 0.0
    applied_damage: int = 0
    target_deaths_door: bool = False
    target_killed: bool = False
    miss_chance: float = 0.0
    excess_crit_bonus: float = 0.0
    blocked_by_aegis: bool = False
    # Calculation trace — populated on every resolve_attack so the engine can
    # print the full arithmetic under CombatEngine.verbose. Cheap to fill
    # (plain floats, no formatting) so this costs nothing during training.
    acc_used: float = 0.0        # skill .atk + attacker bonus + hidden +5
    dodge_used: float = 0.0
    hit_roll: float = 0.0
    crit_chance: float = 0.0
    crit_roll: float = 0.0
    dmg_low: int = 0             # attacker's damage range before modifiers
    dmg_high: int = 0
    base_roll: float = 0.0       # the uniform draw inside that range
    dmg_mod: float = 0.0         # skill's own damage modifier
    damage_multiplier: float = 1.0   # conditional bonuses (marked/type/etc)
    crit_multiplier: float = 1.0
    prot_applied: float = 0.0


# **There is no hidden +5 ACC. There is a roll floor.** (2026-08-19, DECOMP.md F4)
#
# This file carried `HIDDEN_ACC_BONUS = 0.05` for most of the project's life,
# added to accuracy before a clamp, on the strength of the Houndmaster hero
# doc: a hidden +5 ACC always applies, so an attack always has a minimum 5%
# chance to hit regardless of DODGE. **The observable effect is real and the
# implementation was a different mechanic.**
#
# The game's own to-hit function (`FUN_1405aa7f0`) decides in this order:
#
#     roll <= cutoff_always_miss (0.00)  ->  miss
#     roll >= cutoff_always_hit  (0.95)  ->  HIT, whatever the chance is
#     otherwise                          ->  hit if roll < chance
#
# and the chance is never clamped. So the 5% floor is the top 5% of the roll
# range always landing, not five points of accuracy — and the ceiling is not a
# clamp either.
#
#     game:  P(hit) = 0.05 + min(0.95, raw)      raw = ACC - DODGE
#     old :  P(hit) =        min(0.95, raw + 0.05)
#
# Identical for raw <= 0.90, which is most attacks; above that the old rule
# under-hit, and at raw >= 0.95 it missed 5% of attacks the real game never
# misses. Measured over 166 real (chance, roll) pairs from `archive/traces/`:
# 24 of 166 differed, largest gap 5.0pp, mean P(hit) 0.8580 against 0.8512.
#
# Three kinds of evidence, in DECOMP.md F4: the decompiled function; captured
# chances reaching 1.05, which a 0.95 clamp forbids outright; and the outcomes
# either side of the boundary — 6/6 attacks with roll >= 0.95 AND roll > chance
# hit (a damage roll follows each), against 17/17 misses just below it.

# Wiki-confirmed (darkestdungeon.wiki.gg/wiki/Combat_Mechanics): PROT cannot
# reduce damage past 80%, regardless of how much PROT is stacked (Stalwart
# virtue, Bulwark, guard buffs, trinkets). No rules.json field backs this —
# grepped the shipped file for any "prot"-named key and found none, the same
# "hardcoded in the binary" situation as HIDDEN_ACC_BONUS above. Distinct
# from the 0.95 ceiling combat_engine.py clamps the raw PROT *stat* to; that
# clamp is on the stat itself, this is the separate cap on its damage effect.
PROT_DAMAGE_REDUCTION_CAP = 0.80


def compute_hit_chance(
    attacker: CombatantState,
    target: CombatantState,
    skill: SkillLevelData,
    rules: CombatRules,
    acc_bonus: float = 0.0,
) -> float:
    """The raw chance, **unclamped and with no hidden bonus** — see the note
    above and `resolve_to_hit`, which is what turns it into a hit or a miss.

    Returning it unclamped is deliberate: the real game's chance genuinely
    exceeds 1.0 (1.05 observed in a real capture) and genuinely goes negative,
    and both cutoffs apply to the ROLL rather than to this number.
    """
    acc = skill.atk + attacker.atk_bonus + acc_bonus
    return acc - target.dodge


def resolve_to_hit(roll: float, hit_chance: float, rules: CombatRules) -> bool:
    """Did it land? The game's own three-way decision, in its own order.

    One function so the two callers cannot drift: `resolve_attack` and the
    riposte in `combat_engine`. The cutoffs bracket the **roll**, not the
    chance, which is the whole of DECOMP.md F4.
    """
    if roll <= rules.cutoff_always_miss:
        return False
    if roll >= rules.cutoff_always_hit:
        return True
    return roll < hit_chance


def check_crit(attacker: CombatantState, target: CombatantState,
                skill: SkillLevelData, rng: SeededRNG,
                crit_roll: Optional[float] = None) -> bool:
    # Real bug found and fixed: is_crit_valid (default True; 65 real skill-
    # level records across 10 hero files explicitly set it False -- e.g.
    # Plague Doctor's blinding_gas/disorienting_blast, Arbalest/Musketeer's
    # flare, Vestal's illumination) was parsed but never consulted -- every
    # skill could crit regardless, incorrectly triggering stress relief and
    # on-crit passives on skills the real game flags as crit-ineligible.
    if not skill.is_crit_valid:
        return False
    # target.crit_received_bonus: real stat_type "crit_received_chance"
    # (e.g. Man-at-Arms' Bellow, +5-10% while the target is marked) --
    # raises the DEFENDER's chance of being crit against, on top of the
    # attacker's own crit + skill crit.
    crit_chance = attacker.crit + skill.crit + target.crit_received_bonus
    roll = rng.random() if crit_roll is None else crit_roll
    return roll < crit_chance


def compute_excess_crit_bonus(attacker: CombatantState, skill: SkillLevelData,
                                rules: CombatRules) -> float:
    """Crit chance can stack above 100% (trinkets/buffs), but the roll can
    never use more than 100% of it — rules.json's excessCritMult/excessCritMax
    convert the wasted overflow into a flat bonus damage multiplier applied
    on every hit (not just crits), so a maxed-out crit stat isn't fully
    wasted. Verified data-inert: excessCritMult is 0.0 in every shipped
    rules.json (base + radiant/new_game_plus/bloodmoon overrides), so this
    always evaluates to 0.0 bonus against real game data today — implemented
    for correctness/future-proofing rather than because it currently changes
    any number (see ASSUMPTIONS.md).
    """
    crit_chance = attacker.crit + skill.crit
    excess = max(0.0, crit_chance - 1.0)
    return min(excess * rules.excess_crit_mult, rules.excess_crit_max)


def resolve_attack(
    attacker: CombatantState,
    target: CombatantState,
    skill: SkillLevelData,
    rules: CombatRules,
    rng: SeededRNG,
    damage_multiplier: float = 1.0,
    acc_bonus: float = 0.0,
    hit_roll: Optional[float] = None,
    crit_roll: Optional[float] = None,
    damage_u: Optional[float] = None,
) -> AttackResult:
    """`hit_roll` lets a multi-target skill supply ONE roll for all its targets.

    The real game rolls to-hit once per skill use and compares that single roll
    against each target's own hit chance — confirmed from captured game debug
    output: 11/11 multi-target attacks (2- and 3-target) shared one roll with
    zero counterexamples, while crit and damage stayed per-target. See
    CHANGELOG.md. Leave it `None` (the default) for single-target attacks so the
    RNG stream is byte-identical to before this parameter existed.

    `crit_roll` and `damage_u` are the GAME's own draws for this target, known
    in advance from the save's RNG state (`engine/game_rng.py`, DECOMP.md F15).
    Supplied together with `hit_roll` by `CombatEngine.fate`; when given, no
    draw is taken for them. The damage is the game's integer
    `lo + int(u * (hi - lo + 1))` over the skill-modified range, expressed back
    in the engine's base units so every multiplier after it applies as usual.
    """
    result = AttackResult()
    hit_chance = compute_hit_chance(attacker, target, skill, rules, acc_bonus)
    result.miss_chance = hit_chance
    result.acc_used = skill.atk + attacker.atk_bonus + acc_bonus
    result.dodge_used = target.dodge

    roll = rng.random() if hit_roll is None else hit_roll
    result.hit_roll = roll
    result.hit = resolve_to_hit(roll, hit_chance, rules)
    if target.is_corpse:
        # `battle_modifier: ... .can_be_missed False` on every corpse class —
        # a corpse is an object, not something that dodges. The roll is still
        # drawn above so the RNG stream stays aligned with any other attack.
        result.hit = True

    if not result.hit:
        return result

    result.crit_chance = attacker.crit + skill.crit + target.crit_received_bonus
    result.is_crit = check_crit(attacker, target, skill, rng, crit_roll=crit_roll)
    result.excess_crit_bonus = compute_excess_crit_bonus(attacker, skill, rules)

    if damage_u is None:
        base_damage = rng.uniform(float(attacker.dmg_min), float(attacker.dmg_max))
    else:
        m = 1.0 + skill.dmg_mod
        lo = math.ceil(attacker.dmg_min * m - 1e-9)
        hi = math.ceil(attacker.dmg_max * m - 1e-9)
        drawn = lo + int(damage_u * (hi - lo + 1))
        base_damage = drawn / m if m > 0 else float(drawn)
    if result.is_crit:
        # Wiki-confirmed (darkestdungeon.wiki.gg/wiki/Critical_Hit, corroborated
        # by a second source): "A critical hit's damage is equal to 150% of the
        # MAXIMUM damage of a skill, both for heroes and enemies... Unlike
        # normal hits, whose base damage varies within a fixed range, the base
        # damage of a critical hit is always equal to 150% of the maximum
        # damage of the skill. Additional modifiers are then applied as usual."
        #
        # We were multiplying the ROLL by 1.5, which averages 1.5x the midpoint
        # instead of 1.5x the max — roughly 30% too little crit damage on a
        # typical 6-14 range (15 instead of 21). The roll is still drawn above
        # so the RNG stream stays aligned with non-crit resolution.
        base_damage = float(attacker.dmg_max)
    result.dmg_low, result.dmg_high = attacker.dmg_min, attacker.dmg_max
    result.base_roll = base_damage
    result.dmg_mod = skill.dmg_mod
    result.damage_multiplier = damage_multiplier
    # `.dmg_per_burn_stack` -- Backdraft, and nothing else in the game. It
    # ships at `.dmg -50%` and buys the damage back off the target's burn:
    # +25% per stack, so a target on 6 stacks takes 0.5 x (1 + 1.5) = 1.25x.
    # Read off `burn_stacks`, the count, NOT `burn_total_outstanding` -- the
    # game's key is per STACK and the two differ (a stack of 7 is worth 11).
    burn_scale = 1.0 + skill.dmg_per_burn_stack * float(target.burn_stacks or 0)
    applied = (base_damage * (1.0 + skill.dmg_mod) * damage_multiplier
               * burn_scale * (1.0 + result.excess_crit_bonus))

    if result.is_crit:
        mult = rules.player_crit_multiplier if attacker.is_hero else rules.monster_crit_multiplier
        result.crit_multiplier = mult
        applied *= mult

    # Wiki-verified: Grave Robber's "Pick to the Face" (ignore_protection)
    # is "Armor Piercing" -- bypasses the target's PROT entirely.
    if not skill.ignore_protection:
        prot = min(target.prot, PROT_DAMAGE_REDUCTION_CAP)
        applied *= (1.0 - prot)
        result.prot_applied = prot
    # A monster skill shipped at `.dmg 0 0` (182 of 892 monster skill lines:
    # the Prophet's mark, every stress attack, curses, guards) deals NOTHING:
    # the game's own trace logs no damage draw for one (tools/CLAUDE.md, the
    # hit oracle). The floor below would give it 1, and a hit on a hero at
    # Death's Door would then roll a deathblow off a scream. It still lands,
    # so its effects and stress apply; it consumes no Aegis Block either,
    # which is spent only by "non-zero direct damage".
    if not attacker.is_hero and attacker.dmg_max <= 0:
        result.raw_damage = result.applied_damage = 0
        return result
    applied = max(1, round(applied))

    # Shieldbreaker's Aegis Block (Serpent's Sway) -- "When hit by any non-
    # zero direct damage, an Aegis Block is consumed, preventing damage/
    # Deathblow check" per her own doc. Every hit here deals >=1 damage
    # (the max(1, ...) floor above), so any landed hit qualifies. Doesn't
    # touch Stress/DoT/Debuff/KB/Pull/Stun -- those don't route through
    # resolve_attack at all.
    if target.damage_blocks > 0:
        target.damage_blocks -= 1
        result.blocked_by_aegis = True
        applied = 0

    result.raw_damage = applied
    target.hp -= applied
    result.applied_damage = applied

    if target.hp <= 0:
        result.target_deaths_door = True

    return result
