"""The reward, and every weight in the potential.

Split out of `env/gym_env.py` on 2026-09-08, verbatim and with every
comment attached. Re-exported from there, which is where
`tools/exchange_rates.py` and the expedition envs import it from.

**`SHAPING_GAMMA` must match PPO's `gamma` in `train.py`.** The
potential-based invariance guarantee -- that shaping cannot change which
policy is optimal -- assumes the same discount the RL algorithm uses, so
these two numbers are one number written twice.

**Three terms sit OUTSIDE the potential on purpose** and each one is a
deliberate exception to that argument rather than an oversight. Adding a
fourth means re-checking the invariance argument in
`docs/ASSUMPTIONS.md`, not just picking a weight.

Every number here is actively tuned. Read them from this file rather
than from any prose describing them -- README.md carried `-0.02` for the
turn penalty long after it had become `-0.10`.
"""

from __future__ import annotations

# Reward shaping constants (potential-based, Ng/Harada/Russell 1999).
# SHAPING_GAMMA must match PPO's gamma in train.py — the invariance guarantee
# (shaping doesn't change the optimal policy) assumes the same discount the
# RL algorithm uses.
SHAPING_GAMMA = 0.99
REWARD_WIN = 5.0
REWARD_LOSE = -5.0
# A retreat is neither a win nor a loss, and it is NOT free: the party pays
# `combat_retreat_stress` (20, straight from rules.json) per living hero, which
# this env already charges densely through STRESS_TICK_PENALTY on the step that
# stress lands. A terminal bonus here would bill the same event twice.
REWARD_RETREAT = 0.0

# ── Two-phase tempo model (user-specified, matching advanced DD play) ──
#
# An advanced player does not treat every turn the same. There are two
# regimes, and the correct action differs sharply between them:
#
#   THREAT phase (several enemies still able to act): play PROACTIVELY —
#   kill, stun, riposte, buff dodge, prevent damage. Healing here is a trap:
#   in a 4v4 the incoming damage always outpaces what heals can restore, so
#   the winning line is to reduce the number of things acting against you as
#   fast as possible, and to LEAVE this phase quickly.
#
#   RECOVERY phase (one enemy left): this is the real stall window, where a
#   good player deliberately slows down to heal HP/stress back up. Knowing
#   *when* to stall is the skill — it isn't always available (a heavy
#   stress-dealer or heavy hitter has to just die instead), and if
#   reinforcements arrive the fight reverts to the THREAT phase.
#
# The mechanism is the per-turn time cost, because that is the ONLY thing in
# this reward that distinguishes a fast combat from a slow one: the
# potential-based shaping telescopes, so two paths to the same end state
# earn identical shaping regardless of how many turns they took. The old
# flat TURN_PENALTY of -0.02 summed to about -5 across a whole ~250-turn
# expedition — roughly a quarter of a single hero death — which is why the
# trained policy converged on turtling (Crusader healing on 73% of turns,
# Abomination self-healing on 74%; see ASSUMPTIONS.md).
#
# Magnitudes are simulator convention, chosen so that the *gradient* between
# fast and slow play across an expedition (~16-40 threat-phase turns per
# combat, ~10 combats: roughly -16 fast vs -40 slow) exceeds one death
# (-20), while staying far below the cost of a wipe (4 x REWARD_HERO_DEATH
# = -80, plus the forfeited completion bonus) so that dying never becomes a
# cheap way to end an expensive episode.
TURN_PENALTY_THREAT = -0.10
TURN_PENALTY_STALL = -0.01

# Charged per point of party stress GAINED, at the moment it is gained.
#
# Stress used to be paid for only at the expedition's end
# (TERMINAL_STRESS_PENALTY_WEIGHT), which is the same structural mistake as
# the original truncation penalty: a sparse terminal cost discounted at
# gamma=0.99 across ~200 remaining steps is worth roughly 13% of face value
# at the moment of choosing, so it cannot discipline a cheap immediate
# action. The action it failed to discipline is Abomination's Transform,
# which is FREE (is_continue_turn, so no turn penalty applies) but inflicts
# +8 stress on every other party member, takes 6 stress/turn while in beast
# form, and only refunds 2 on reverting.
#
# Measured on the 6M model: 1.77 transforms per combat — essentially the
# full 2-per-battle allowance every fight — for ~153-166 stress per ally per
# expedition from transforms alone, against an affliction threshold of 100
# and a heart-attack threshold of 200. The two worst comps in every sweep
# (Dark Ritual: Ballad, Stun Control: Beast) are exactly the Abomination
# ones, and their mean final stress (169 on long bands) versus the best comp
# (69.6) is the whole performance gap.
#
# Rate chosen to move roughly the existing total stress cost from terminal
# to dense rather than to add new cost on top: ~600 stress-points gained
# across a 4-hero expedition x 0.01 = about -6, against the old terminal
# term's -4.5 to -8.5. TERMINAL_STRESS_PENALTY_WEIGHT is reduced to match
# (see expedition_sequence_env). Only INCREASES are charged — refunding
# reward for stress healing would just pay the policy to farm heal-stress
# loops, and recovery is already valued through the potential function.
STRESS_TICK_PENALTY = 0.01
# "One enemy left" per the user's spec. Deliberately NOT
# rules.stall_enemy_size_threshold (2) — that's the game's own threshold for
# when stalling starts accruing a *penalty*, a different question from when
# a player should choose to stall.
STALL_PHASE_MAX_ENEMIES = 1

# Cheap recovery turns are only granted while the party actually has
# something to recover. Without this the discount is a hole in the reward:
# the policy kills down to the last enemy and then dawdles forever, because
# a recovery turn costs -0.01 *now* while the truncation penalty (-20) is
# terminal and, discounted at gamma=0.99 across ~200 remaining steps, worth
# only about -2.6 at the moment of choosing. A sparse terminal penalty
# cannot discipline a dense cheap action. Measured directly on the first
# phase-model run: "Ballad Spam" medium hit 60% stall-outs with a healthy
# party (mean stress 71) and only 14% success. Encoding "stall to recover,
# then move on" as "you get the discount while hurt, not once topped up" is
# also the more faithful statement of the real skill, which is knowing WHEN
# stalling is worth it rather than that stalling is free.
RECOVERY_HP_FRACTION = 0.85
RECOVERY_STRESS_FLOOR = 30.0
# WHAT ENDS THE CHEAP-TURN WINDOW. This was `RECOVERY_TURN_BUDGET = 10`, an
# invented hard cap, until 2026-08-24. It is now the GAME'S OWN stall pricing,
# because the game already charges for standing around and two prices for one
# thing is what makes the play unlearnable.
#
# `rules.json` bills a stall round at `stall_round_stress_effect_threshold`
# (3 rounds -> party stress) and `stall_round_summon_effect_threshold` (4 ->
# reinforcements), and `stall_number_of_invalidating_skills_threshold` (2)
# says two real actions in a round reset the counter. That last number IS
# Fran's technique: two heroes chip the last enemy while two heal, and the
# party can farm indefinitely. `combat_engine.py` models all of it, including
# the accelerated increments — so the simulator permitted the play and only
# the reward forbade it.
#
# Measured before removing it: the cap fires on 9.6% of Champion combats and
# 3.9% at Veteran, and costs 0.90 reward on a 20-turn stall, 1.80 at 30 and
# 2.70 at 40. The frequency was the WRONG test to gate this on — what matters
# is the magnitude when it fires — and the prize it was blocking is larger
# still: taking 30 stress off the party once is worth +5.5 discounted return
# (95% CI +-7.8, n=39), because the party then enters every REMAINING combat
# lighter and each of those pays through `combat_end_condition`.
#
# An earlier claim here that "even uncapped a 20-turn stall does not pay" was
# measured with a harness that priced only the CURRENT combat and is retracted.
#
# `_recovery_turns_used` survives as pure instrumentation — it gates nothing.
# The replacement lives in `DarkestDungeonEnv._stall_is_still_cheap`.

# Weights inside the potential function — see _hero_score/_enemy_score.
W_STRESS = 0.50
W_DEATHS_DOOR = 0.25
W_AFFLICTED = 0.15
W_VIRTUOUS = 0.15
ENEMY_DEAD_SCORE = -0.15
HERO_DEAD_SCORE = -1.0
# Action economy / prevention, the currency of the threat phase. A stunned
# enemy is one that cannot act this round, which is worth nearly as much as
# damage; guard and riposte are how a party prevents damage instead of
# healing it back. These live in the potential function, so by
# Ng/Harada/Russell they cannot change the optimal policy — they exist to
# make the credit assignment for "disable and prevent" immediate rather than
# leaving the policy to discover it through many turns of consequences.
W_ENEMY_STUNNED = 0.30
W_HERO_GUARDED = 0.10
W_HERO_RIPOSTE = 0.10
# A corpse standing in FRONT of a living enemy, which is the only kind that
# costs anything: it holds a rank the survivor would otherwise have advanced
# into, so it is what puts a back-rank enemy out of a front-rank skill's reach.
# A corpse with nothing behind it is inert and scores nothing.
#
# Exists to give the newly-legal "attack the corpse" action a gradient. Without
# it, breaking a corpse reads to the policy as a turn spent on a target whose HP
# does not enter Φ at all (a corpse is is_dead, so _enemy_score short-circuits
# to ENEMY_DEAD_SCORE) — i.e. a pure turn-penalty loss, and the action would be
# learned away rather than learned. Potential-based like the rest of Φ, so by
# Ng/Harada/Russell it cannot change the optimal policy: if clearing a blocking
# corpse is not actually worth a turn, this only mis-times the credit, it does
# not buy the behaviour.
W_BLOCKING_CORPSE = 0.10

# ── PREVENTION, not restoration (2026-08-27, `--prevention-reward`) ──────────
#
# Fran's ask, and his own Plague Doctor guide states the thesis outright:
# *"Facetanking attacks and simply trying to outheal enemy offense is just a
# very bad idea in general. It's much safer and more efficient to be proactive,
# such as having actual proper defensive plans, rather than being reactive as in
# just facetanking and spamming Heals."*
#
# Both terms live INSIDE Φ, so by Ng/Harada/Russell they cannot change the
# optimal policy. They exist to fix CREDIT TIMING, which is the whole
# complaint -- exactly the argument `W_ENEMY_STUNNED` and `W_BLOCKING_CORPSE`
# already rest on.
#
# W_ENEMY_DOT -- damage already committed but not yet landed.
#   `_enemy_score` read `hp/max_hp` and nothing else, so an enemy on 20 HP
#   carrying 15 points of pending blight looked untouched. Applying a DoT
#   earned ZERO immediate credit while the turn penalty charged the turn at
#   once. That is a complete account of why the Plague Doctor is under-used:
#   her entire kit is deferred damage. Capped at the target's own HP so a
#   massive overkill DoT cannot out-score a kill, and it is not double
#   counted when the DoT ticks -- the HP it removes leaves `hp/max_hp` by
#   exactly the amount it leaves this term.
#
# W_INCOMING -- what the board is about to do, from `threat_forecast()`.
#   This is what makes prevention pay and healing not. Killing an enemy,
#   stunning it, raising a hero's DODGE or lowering an enemy's ACC all reduce
#   next round's forecast; a heal restores HP and changes NOTHING about what is
#   coming. Fran's dodge stacks therefore become worth something without a
#   dedicated dodge term.
W_ENEMY_DOT = 0.35
W_INCOMING_DAMAGE = 0.30
W_INCOMING_STRESS = 0.15
