"""The observation layout, and the helpers that read a combatant.

Split out of `env/gym_env.py` on 2026-09-08, verbatim. Re-exported from
there: `tools/ablate_forecast.py` takes HERO_THREAT_FORECAST and
HERO_SLOT_SCALARS, `tools/check_class_play.py` takes `_outstanding_dot`,
and `bridge/run.py` and `tools/playbook_policy.py` take `_is_doomed`.

**The observation encodes MECHANICS, not identity.** No hero-class
one-hot: what a class can DO reaches the vector, what it is called does
not. That is the property the whole cross-class transfer question turns
on, and five separate attempts to beat it are recorded as null in
`docs/findings/transfer.md`.

**The width is a property of the loaded DATA as well as of this file.**
A loader change that alters what a feature CONTAINS keeps the width and
silently feeds a checkpoint numbers it never trained on --
`tests/suite/test_obs_width_tripwire.py` is the guard.

`RETREAT_CHANCE_FROM_END` is measured from the END of the globals block
because a test counts back from the tail; anything appended to the
globals must bump it by the same amount.
"""

from __future__ import annotations
from engine.combatant import (CombatantState, BURN_BASE_DECAY,
                              burn_total_outstanding)
from .action_space import RANKS

# The STATE half of a hero rank. 15th is the derived "doomed by its own DoT"
# flag — see _is_doomed. Burn is its own scalar beside bleed and blight rather
# than folded into either: it decays instead of expiring, so the same displayed
# number means a different amount of remaining damage, and a policy that cannot
# tell them apart cannot value Searing Strike's "+DMG vs Burning" at all.
HERO_STATE_SCALARS = 56
# HERO_SLOT_SCALARS and ENEMY_SLOT_SCALARS are both declared below, after
# SKILL_MECHANIC_FEATURES, because both DEPEND on it: each slot carries that
# combatant's whole capability payload on top of its state.
# A skill slot is 10 descriptive floats plus SKILL_MECHANIC_FEATURES payload
# floats. The 10 came first and are UNCHANGED in position; the payload was
# APPENDED, the same discipline burn and the retreat action were added under.
#
# Why the payload exists (measured 2026-08-23): the 10 alone are
#   exists, legal_now, targets_allies, is_heal, is_aoe, atk, dmg_mod, crit,
#   is_stall_invalidating, is_continue_turn
# and **125 of 140 hero skills (89.3%) carry at least one payload none of them
# describes** -- stat buff/debuff on 60.7%, mark on 10.7%, bleed 10.0%, stun
# 8.6%, push/pull 8.6%, stress heal 5.7%, guard/riposte/cure/burn ~2% each.
# Two skills of one class were therefore distinguishable ONLY by the 20-class
# one-hot plus the slot index, so the policy memorised `(class, slot)` pairs
# instead of learning that +DODGE is worth having. A modded class has no
# one-hot slot at all and so transferred nothing.
#
# `data/types.py::EffectDefinition` already parses every one of these; the
# observation was throwing them away.
# v[45] is corpse clearing, APPENDED 2026-09-19 -- every index below it still
# means what it meant, the discipline burn and the heal headroom were added
# under. It could NOT reuse a free slot: there is none (0-17 flags and
# magnitudes, 18-41 the two stat-bucket banks, 42 limited-use, 43 kill, 44
# damage blocks), and reusing one would have been the SILENT break rather
# than the loud one -- the width would still match, the shipped checkpoint
# would still load, and it would be fed a dimension whose weights were
# trained against a constant 0.0.
# v[46]..v[52] APPENDED 2026-09-19 by the same audit, in one batch so the
# width moves ONCE: torch change (signed), summon, clears-guard,
# clears-control, unstealth, bonus action, irresistible DoT. All coarse
# flags -- the forecast finding says a coarse NEW signal pays and every
# refinement of a signal already present measured null, so riposte magnitude
# and the other refinements of existing flags were deliberately left out.
SKILL_MECHANIC_FEATURES = 53
SKILL_SLOT_FEATURES = 20 + SKILL_MECHANIC_FEATURES

# A hero rank is its state PLUS that hero's own capability payload -- the same
# max-pooled moveset vector the enemy slot carries, THREE times: once over the
# skills aimed at an ALLY, once over the SELF-only skills, once over the skills
# aimed at an ENEMY. APPENDED after the 56, so every index below it still means
# what it meant.
#
# Why it exists, and it is a MEASURED repair rather than a guess: the 20-class
# one-hot was dropped on 2026-08-23 and the held-out-class falsifier came back
# decisively negative -- +46.9pp excess gap, CI [+37.2, +56.9], 17 of 17
# held-out comps worse, while the two arms stayed level on SEEN comps. The
# policy could read the ACTING hero's seven skill slots and knew nothing about
# what the other three ranks could do, so "the rank behind me can heal" was not
# a fact about the board it could represent at all. This is the repair
# `_hero_slot_vec`'s own docstring named: capability, never identity.
# ...plus one more, APPENDED after the capability pools so every index below
# is untouched: the hero's ABSOLUTE HP deficit, on the same /30 scale the skill
# payload's heal amount uses.
#
# Why it is not redundant with `hp / max_hp` two dozen features up, which is the
# same argument `_is_doomed` rests on. The hero slot carries HP as a FRACTION
# and the skill slot carries a heal as an ABSOLUTE amount over 30. To judge "is
# this heal wasted" the policy has to compare them, and that needs the target's
# max HP — which the observation never carried, so the comparison was not
# recoverable from the vector at all.
#
# Measured 2026-08-24, and this is the defect it exists to close: 51.9% of heals
# land on a target at a median 94% HP, restoring 2.4 HP. Priced out, a marginal
# heal is worth (3.4/35)/4 = +0.024 in Φ against a −0.10 turn penalty, so the
# reward already scores it at about −0.076 — and the realised step reward for
# those heals measures −0.0819. The reward is right and the policy does it
# anyway, because it cannot see the quantity the decision turns on.
HERO_HEAL_HEADROOM = 1

# ...and four more, APPENDED after the heal headroom so every index below is
# untouched: WHAT IS ABOUT TO HAPPEN TO THIS HERO.
#
# `CombatEngine.threat_forecast()` reads the enemy's own decision
# distributions -- `tools/brain_oracle.py` grades those weights against the
# running game at 46/46 -- through the hit rule `tools/hit_oracle.py`
# validates at 155/155. A human estimates this from experience; every input
# here is exact, which is the whole answer to "can the model have an edge a
# player does not".
#
# The observation carried the board but never what the board was ABOUT to do.
# "Kill it before it swings", "guard the Vestal, she is the one being aimed
# at", and "this hero cannot survive the round" are all judgements about the
# next round, and none of them was recoverable from the vector.
#
#   0  incoming damage / current HP     -- 1.0 means dead this round in
#                                          expectation. The lethality read.
#   1  incoming damage / MAX HP         -- the same quantity on an ABSOLUTE
#                                          scale. Not redundant: the same
#                                          fraction-vs-absolute argument that
#                                          HERO_HEAL_HEADROOM rests on, and
#                                          it is what says "a big hit is
#                                          coming" to a hero still at full HP.
#   2  incoming STRESS, /20             -- the currency the stated objective
#                                          is denominated in, and previously
#                                          invisible at every level.
#   3  P(anything aims at this hero)    -- who the board has decided to hit.
#
# `p_deaths_door` and `p_death` were APPENDED here on 2026-08-25 and REVERTED
# the same day. `CombatEngine.threat_forecast` still computes both and
# `tools/time_to_secure.py` reads them -- they are correct and validated. They
# are simply not in the observation, because a 3M arm carrying them scored
# 78.9% / 73.3 against 78.9% / 73.8 without: indistinguishable.
#
# Reverted rather than left in for two reasons. Eight floats of observation for
# no measured benefit is a cost, and more importantly the width change makes
# every 1636-wide checkpoint UNLOADABLE -- which stranded three good arms and
# made the robustness comparison impossible to run at all.
#
# The wider lesson is in CHANGELOG: the forecast itself paid +6.2pp, and BOTH
# refinements of it (stress calibration, the tail) were null. The policy reads
# a coarse "danger here" and is insensitive to precision below it.
#
# It is a FORECAST, not a guarantee: damage is the mean of the band, so what
# actually kills a hero at 12 HP is the top of the band times the crit
# multiplier. See threat_forecast's docstring for the simplifications.
HERO_THREAT_FORECAST = 4

HERO_SLOT_SCALARS = (HERO_STATE_SCALARS + 3 * SKILL_MECHANIC_FEATURES
                     + HERO_HEAL_HEADROOM + HERO_THREAT_FORECAST)

# Where the heal headroom sits, counting from the END of the hero slot. Named
# because a test pinned it as `[-1]` and appending after it silently moved it
# -- exactly the trap the append-only discipline exists to avoid, arriving
# from the test side instead of the code side.
HERO_HEAL_HEADROOM_INDEX = -(HERO_THREAT_FORECAST + 1)

# 39 board/stat scalars PLUS `SKILL_MECHANIC_FEATURES` of monster CAPABILITY --
# the payload of that monster's whole moveset, max-pooled, so the policy can
# see whether the thing standing in rank 2 stuns, bleeds, marks, or heals its
# friends. Of 22 EnemyDefinition fields the observation used to read TWO.
#
# DERIVED, never written down: sizing it by hand is exactly how it landed 6
# short the first time, because the payload had grown 39 -> 45 underneath it.
ENEMY_SLOT_SCALARS = 39 + SKILL_MECHANIC_FEATURES

# The eight stat buckets a buff or debuff lands in, recorded TWICE per skill --
# once for what it does to its target, once for what it does to the performer.
# Splitting them is what makes the sign unambiguous: every bucket is signed
# "better for whoever receives it", so a Weakening Curse reads as negative-to-
# target and Bolster as positive-to-self, and the policy is not asked to infer
# an XOR against `targets_allies`.
# Twelve, and the last four were added after a census of what buffs the game
# actually ships: `stress_dmg_received_percent` alone has 183 carriers and
# stress is what kills parties here, so folding it into "some unmapped stat"
# threw away the biggest single axis after raw damage.
STAT_BUCKETS = ("acc", "dmg", "crit", "dodge", "prot", "speed", "hp", "resist",
                "stress_taken", "dmg_taken", "healing", "status_chance")

# (stat_type, stat_sub_type) -> bucket. `combat_stat_add`/`combat_stat_multiply`
# carry the real axis in the SUB type, which is why this is keyed on the pair.
_STAT_BUCKET_BY_TYPE = {
    "attack_rating": "acc", "attack_rating_add": "acc",
    "damage_low": "dmg", "damage_high": "dmg",
    "crit_chance": "crit", "crit_chance_add": "crit",
    "defense": "dodge", "defense_rating": "dodge", "dodge": "dodge",
    "prot": "prot", "protection_rating": "prot",
    "speed": "speed", "speed_rating": "speed",
    "max_hp": "hp",
    "resistance": "resist", "stun": "resist", "bleed": "resist",
    "poison": "resist", "move": "resist", "debuff": "resist",
    "disease": "resist", "death_blow": "resist",
    # How much stress/damage lands on the holder, and how well healing takes.
    # These are `_received_` percentages, so a POSITIVE shipped amount is worse
    # for the holder -- see _RECEIVED_IS_BAD.
    "stress_dmg_received_percent": "stress_taken",
    "damage_received_percent": "dmg_taken",
    "crit_received_chance": "dmg_taken",
    "hp_heal_percent": "healing", "hp_heal_received_percent": "healing",
    "stress_heal_percent": "healing", "stress_heal_received_percent": "healing",
    # Attacker-side bonuses that lower the TARGET's effective resist -- the
    # shape `_apply_buff_to_stats` already uses for stun/move/bleed/poison.
    "stun_chance": "status_chance", "bleed_chance": "status_chance",
    "poison_chance": "status_chance", "move_chance": "status_chance",
    "debuff_chance": "status_chance",
}

# Buckets whose shipped sign is inverted relative to "better for the holder":
# +20% stress damage RECEIVED is a debuff, not a buff. Flipping here keeps the
# one convention this vector has -- every bucket reads better-is-positive --
# so the policy never has to learn that two axes mean the opposite thing.
_RECEIVED_IS_BAD = frozenset({"stress_taken", "dmg_taken"})
_STAT_BUCKET_BY_SUBTYPE = {
    "attack_rating": "acc", "damage_low": "dmg", "damage_high": "dmg",
    "crit_chance": "crit", "defense_rating": "dodge",
    "protection_rating": "prot", "speed_rating": "speed", "max_hp": "hp",
    "stun_resist": "resist", "bleed_resist": "resist", "poison_resist": "resist",
    "move_resist": "resist", "debuff_resist": "resist",
    "disease_resist": "resist", "death_blow_resist": "resist",
}


def _stat_bucket(stat_type: str, sub_type: str):
    """Which of STAT_BUCKETS this buff moves, or None if it is not a stat."""
    if stat_type in ("combat_stat_add", "combat_stat_multiply"):
        return _STAT_BUCKET_BY_SUBTYPE.get(sub_type)
    return _STAT_BUCKET_BY_TYPE.get(stat_type)
# round, torch, living heroes, living enemies, stall progress, real actions
# landed this round. The last two are what make deliberate stalling playable:
# the advanced technique in a 1-enemy fight is to keep landing the required
# number of stall-invalidating actions each round so reinforcements never
# arrive, while the other heroes heal. Without the stall timer in the
# observation the policy cannot see how close a reinforcement wave is and has
# no way to learn that at all.
#
# 7th: the party's CURRENT retreat chance. Not a constant — it climbs 5 points
# per failed attempt this combat (`combat_retreat_bonus_chance_per_attempt`), so
# without it the policy cannot see that a second attempt is a better bet than
# the first was, and "keep trying" is unlearnable.
# 14, plus 3 appended 2026-08-24 for the party-level threat forecast: what
# the whole board is about to deal, and whether the party can heal through it.
# `_threat_ratio` is deliberately LEFT AS IT WAS rather than upgraded in place
# -- it is a crude damage-band heuristic that other code and docs refer to by
# that description, and replacing its meaning under a stable index is a
# different kind of change from adding one.
GLOBAL_FEATURES = 14 + 3

# How far the live retreat-chance global sits from the END of the globals
# block. Named because a test counted back from the tail and every append
# since has silently moved it -- bumped to -2, then -8, then -11. Anything
# appended to the globals must bump this by the same amount.
RETREAT_CHANCE_FROM_END = 11


from engine.combat_engine import MORTALITY_TIER_RANK


def _buff_min_duration(c) -> float:
    """How soon the nearest timed buff on `c` expires, 0.0 if none is timed.

    Permanent buffs (trinkets, quirks, gear) carry no duration and are excluded
    -- they are already fully described by the live stats, and folding them in
    here would read as "nothing expires soon" exactly when a temporary buff is
    about to lapse.
    """
    # `ActiveBuff.remaining_rounds`, NOT `rounds_remaining` -- the transposed
    # name is what `CombatantState` uses for mark/stealth/guard, and reading it
    # here through a defaulting getattr made this feature a silent constant
    # 0.0 on all eight slots. Attribute access is direct now so a rename raises
    # instead of zeroing an input. See tests/env/test_turn_order_obs.py for the
    # same bug in the queue feature and why neither was caught by width checks.
    rounds = [b.remaining_rounds for b in c.buffs
              if b.remaining_rounds is not None]
    if not rounds:
        return 0.0
    return _clamp01(min(rounds) / 4.0)


def _uses_remaining(actor, skill_id: str, sd) -> float:
    """Fraction of a per-battle-limited skill's uses still available; 1.0 when
    the skill has no limit at all."""
    limit = sd.per_battle_limit
    if not limit:
        return 1.0
    used = actor.skill_uses.get(skill_id, 0)
    return _clamp01((limit - used) / limit)


def _rank_bits(ranks) -> list:
    """A 4-bit mask over the board's ranks. `launch` and `target` are
    1-indexed rank lists in the game's own files."""
    bits = [0.0] * RANKS
    for r in (ranks or ()):
        i = int(r) - 1
        if 0 <= i < RANKS:
            bits[i] = 1.0
    return bits


def _hero_has_modes(store, h) -> float:
    """1.0 if this hero has stances at all. Two of twenty do (Abomination
    human/beast, Duelist defensive/aggressive) and both are in
    `UNRESTRICTED_HEROES` -- all seven skills equipped, the MODE deciding which
    are usable. Without this the policy sees seven skills, some inexplicably
    illegal, and no state that explains why."""
    hd = store.heroes.get(h.definition_id)
    return 1.0 if hd is not None and len(hd.modes) > 1 else 0.0


def _hero_mode_index(store, h) -> float:
    """Which stance, normalised over however many the class has -- not a
    one-hot, so a modded class with three modes still encodes."""
    hd = store.heroes.get(h.definition_id)
    if hd is None or len(hd.modes) < 2:
        return 0.0
    ids = [m.id for m in hd.modes]
    try:
        i = ids.index(h.mode)
    except ValueError:
        return 0.0
    return i / (len(ids) - 1)


def _clamp01(x: float) -> float:
    """`max(0.0, min(1.0, x))` without the two builtin calls.

    Called **~354x per env step** (1.42M times per 4000 steps) building the
    observation, so the pair of `min`/`max` calls was itself measurable. That
    figure read "~74x (~297k)" until it was re-counted on 2026-09-02 -- 4.8x
    low, which STRENGTHENS the reason this function exists rather than
    weakening it. A performance note is a measurement and goes stale like any
    other.

    Behaviour is identical to the old expression **including for NaN**, which
    is the only input where the obvious rewrite would differ: `nan < 1.0` is
    False, so the old `min(1.0, nan)` returned 1.0 and `max(0.0, 1.0)` gave
    1.0. The branch order below reproduces that. Writing it as
    `if x < 0: 0.0 elif x > 1: 1.0 else x` would instead propagate NaN into
    the observation -- arguably better, but a behaviour change, and this is
    meant to be a pure speed change.
    """
    if x < 1.0:
        return x if x > 0.0 else 0.0
    return 1.0


def _dot_total(dots) -> float:
    """Total outstanding damage from a DoT stack, normalized. Encodes both
    'how hard is it ticking' and 'how long is left' in one number."""
    return _clamp01(sum(d.damage_per_tick * d.remaining_ticks for d in dots) / 20.0)


def _burn_total(c: CombatantState) -> float:
    """Burn on the same 0-1 scale as `_dot_total`, so the three DoT features
    are comparable. Uses outstanding damage rather than the raw stack count
    because a stack of 7 is worth 11 damage, not 7."""
    return _clamp01(burn_total_outstanding(
        c.burn_stacks, BURN_BASE_DECAY + c.burn_decay_bonus) / 20.0)


def _outstanding_dot(e: CombatantState) -> float:
    """Damage already committed to this enemy that has not landed yet.

    Bleed and blight are `damage_per_tick x remaining_ticks`; burn is a
    DECAYING stack count, so its outstanding total is
    `burn_total_outstanding`, never `burn_stacks` -- a stack of 7 is worth 11.

    Split out of `_is_doomed`, which computed exactly this and threw the number
    away after one comparison. `Φ` needs the quantity, not the boolean.
    """
    if not e.is_alive() or e.hp <= 0:
        return 0.0
    total = sum(d.damage_per_tick * d.remaining_ticks for d in e.bleeds)
    total += burn_total_outstanding(e.burn_stacks,
                                    BURN_BASE_DECAY + e.burn_decay_bonus)
    total += sum(d.damage_per_tick * d.remaining_ticks for d in e.blights)
    return float(total)


def _is_doomed(e: CombatantState) -> bool:
    """Already dead, it just hasn't happened yet: outstanding bleed + blight
    covers the target's remaining HP, so it dies to its own DoT before it can
    act again, whatever anyone does.

    A derived feature, but not a redundant one. The raw parts were both already
    observable — hp fraction, and a normalized DoT total per kind — yet the
    comparison between them needs the target's ABSOLUTE max HP, which the
    observation never carried, so 'is this kill already secured?' was not
    recoverable from the vector at all. It is the exact quantity PLAYBOOK 3.3
    turns on, and the measured violation is large: of the turns taken with a
    kill already secured, 60.4% were spent attacking that same doomed target."""
    if not e.is_alive() or e.hp <= 0:
        return False
    return _outstanding_dot(e) >= e.hp
