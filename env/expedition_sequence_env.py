"""Full-expedition Gym env: one episode = one N-combat expedition (real
comp/zone sampled once, one Resolve-Level-paired difficulty tier fixed for
the whole trip), instead of ExpeditionEnv's single randomized combat.

Chains CombatEngine.reset() calls internally — same per-turn action/
observation space as every other combat, plus 7 extra observation floats
of expedition-level context. HP/stress/Death's-Door/mortality-tier carry
forward between combats (CombatEngine.reset(carry_over=...)); a hero death
does not end the episode (continues with survivors, repositioned
automatically since dropping a hero from hero_defs and leaving
hero_positions unset re-packs the rest into contiguous ranks 0..N-1) — only
a full party wipe or the last sampled combat's end does.

Named "Sequence" (not a further overload of ExpeditionEnv) because the
semantics genuinely change: ExpeditionEnv.reset() = one combat;
ExpeditionSequenceEnv.reset() = the start of an N-combat run, and step()
gains "combat just ended, auto-start the next one" logic the single-combat
env has no notion of. Subclasses ExpeditionEnv purely to reuse its comp/
enemy-pool sampling (_sample_comp/_sample_enemy/_resolve_skill_ids).
"""

from __future__ import annotations
from dataclasses import replace
from typing import Optional
import numpy as np
from gymnasium import spaces

from .expedition_env import (ExpeditionEnv, TIER_TO_HERO_LEVEL,  # noqa: F401
                            TIER_TO_QUEST_DIFFICULTY, TORCH_LEVEL_DECILES)
from .gym_env import SHAPING_GAMMA, GAME_DIR, ACTION_ABANDON
from engine import CarryOverHeroState
from engine.camping import resolve_camp
from data.map_gen_loader import MapGenLoader
from data.camping_loader import FIREWOOD_PER_BAND
from data.district_loader import (districts_at_tier, food_bonus,
                                  hero_district_buff_ids, hunger_disabled,
                                  party_camping_point_bonus)

# Short/Medium/Long combat-count ranges, derived from the real
# scripts/map_generator.darkest (hallway_battle + total_room_battles, as the
# envelope across every quest type and zone the game can generate at that
# size). Yields short (3, 8) / medium (6, 10) / long (14, 16).
#
# This was previously the invented convention short (8, 11) / medium (10, 14)
# / long (14, 18), on the recorded belief that no game file exposed room
# counts. It does — see data/map_gen_loader.py and CHANGELOG.md. The old
# convention ran roughly twice the real number of combats at short and medium,
# so a simulated "short" expedition was longer than a real *medium* one.
#
# Parsed at import time rather than from a DataStore: it is one small
# plain-text file, and keeping this a module-level dict preserves the public
# API that eval_expedition.py and tools/analyze_behaviour.py import.
_FALLBACK_LENGTH_BANDS = {"short": (3, 8), "medium": (6, 10), "long": (14, 16)}
EXPEDITION_LENGTH_BANDS = (
    MapGenLoader.combat_count_bands(MapGenLoader.load_all(GAME_DIR))
    or _FALLBACK_LENGTH_BANDS
)

# Starting provision quantities — simulator convention, explicitly flagged
# as having no real "recommended loadout" data source (campaign/provision/
# provision.json only has store purchase LIMITS per dungeon-length index,
# not recommendations). Capped at each item's real stack_limit so this
# convention can never exceed what the real game would even allow buying.
BASE_PROVISION_AMOUNTS = {"bandage": 2, "antivenom": 2, "holy_water": 1, "laudanum": 1}
LENGTH_BAND_PROVISION_MULT = {"short": 1.0, "medium": 1.5, "long": 2.0}
# REAL, from `campaign/provision/provision.json`'s
# `default_store_inventory_item_lists`, where food is the empty-id `provision`
# row. Was an invented 2/3/4 -- nine times too small -- on the belief that the
# file exposed no per-length loadout. It does, and the SAME rows carry the
# firewood column 0/1/2 that `FIREWOOD_PER_BAND` already treats as real.
FOOD_AMOUNTS = {"short": 18, "medium": 24, "long": 36}
FOOD_HEAL_FRACTION = 0.05        # rules.json provision_hp_heal
# rules.json `hallway_hunger_HPrestore` / `hallway_hunger_starve_HPdmg`.
# Starving is FOUR TIMES worse than eating is good, which is what makes food
# insurance rather than healing.
HUNGER_HEAL_FRACTION = 0.05
HUNGER_STARVE_FRACTION = 0.20
# DECLARED CONVENTION. The real trigger is hallway travel, which this simulator
# does not model, so a completed combat is the proxy. The RATE is derived from
# the shipped stock rather than chosen: 36 food at one per hero is 9 checks,
# over a long run's ~15 combats. Falsifier: count hunger checks in a real long
# run -- well under 9 means the stock is meant to be bought partially and this
# is too high.
HUNGER_CHECK_CHANCE = 0.6

# DELIBERATE EATING. `rules.json`'s `provision_hp_heal` (0.05) is what a hero
# restores by eating, and until now nothing in this simulator ever spent food
# on purpose -- the involuntary hunger check was food's only consumer, so a
# long run ended with half its ration unspent and a Champion run (where the
# Mill switches hunger off) with ALL of it. That deleted the whole no-healer
# strategy, which is exactly what Fran flagged on 2026-08-24.
#
# ONE DECLARED CONVENTION: the top-up threshold. The real trigger is a player
# clicking food in a corridor, and no shipped key states when they do it. 0.90
# is the reading that a player tops the party up between rooms rather than
# hoarding; the reserve below is what stops it eating the hunger check's ration.
# Falsifier: a real long run that ends with a large unspent ration means
# players eat less often than this, and the threshold is too high.
EAT_TOPUP_FRACTION = 0.90
# HOW MUCH THE PARTY MAY EAT AT ONE STOP. The game's own number --
# `rules.json max_provisions_before_full` (4) -- read off the store rather
# than declared here, so this constant is only the fallback for a store that
# predates the key.
#
# It replaced an invented per-combat rate of 2.0 on 2026-08-24, a declared
# approximation of Fran's "4 food per 2 combats or so" that turned out to be
# the largest single cost measured on the long band. At Champion it blocked
# 82.8% of all refused meal-stops -- 46 of them with the party under 75% HP --
# while the run ended carrying 26.6 of its 46 food. Paired over 50 scenarios on
# one checkpoint with no retraining: wipe 46.0% -> 24.0%, finished 42.0% ->
# 60.0%, and the HP the party carries INTO a fight 79.5% -> 85.0%. That last
# number is the point -- the first-death probe measures the combat that takes
# the party's first death as entered at 66.3% HP against 85.2% for combats that
# take none, so the rate limit was holding the party just under the line it has
# to stay above.
#
# Three real limiters remain and none is invented: the ration itself
# (FOOD_AMOUNTS, plus the Granary), the reserve held back for future hunger
# checks below Champion, and EAT_TOPUP_FRACTION stopping a healthy party from
# eating at all.
EAT_MAX_MEALS_PER_STOP = 4

# Districts are ON. This is a fidelity fix, not a distribution knob -- the same
# class of change as burn or surprise -- so it is not defaulted off. Set False
# to reproduce a pre-2026-08-24 distribution for an A/B arm.
DISTRICTS_ENABLED = True

# TRAIN HARDER THAN YOU DEPLOY -- Fran, 2026-08-24: "we should focus heavier on
# long champion missions... a harsher training model makes a better output
# tool." Uniform sampling spends 2/3 of training on short and medium bands that
# the policy already clears at ~90%, and 4/5 of it below Champion.
#
# There is also a measured argument for it: the districts retrain lost 4.0pp to
# a checkpoint that had never seen districts, and the difference between them
# was that the losing arm trained in the EASIER world it was then evaluated in.
# Weighting toward the hard corner is the same lever pulled deliberately.
#
# Both default to the uniform distribution, so an archived checkpoint's
# distribution is still reproducible and `--hard-focus` is what turns this on.
UNIFORM_BAND_WEIGHTS = {"short": 1.0, "medium": 1.0, "long": 1.0}
UNIFORM_TIER_WEIGHTS = {1: 1.0, 2: 1.0, 3: 1.0, 4: 1.0, 5: 1.0}
HARD_FOCUS_BAND_WEIGHTS = {"short": 1.0, "medium": 2.0, "long": 5.0}
HARD_FOCUS_TIER_WEIGHTS = {1: 1.0, 2: 1.0, 3: 2.0, 4: 2.0, 5: 6.0}

# A tighter per-combat step budget than the single-combat env's blanket 150
# (sized for worst-case pathological single fights, not typical ones) —
# typical combats resolve well under this per eval_expedition.py's own
# mean_len figures. Self-truncating here replaces wrapping this env in the
# external gymnasium.wrappers.TimeLimit, since the right cap depends on the
# sampled num_combats (unknown until reset()).
#
# **It is enforced twice, and for a long time only the second one existed.**
# `_max_steps = MAX_STEPS_PER_COMBAT * num_combats` is an expedition TOTAL, so
# nothing bounded any single combat: one fight could legally consume the whole
# pool. That is not hypothetical — a 220-round combat was measured, and the
# arithmetic identifies it exactly (a 15-combat expedition pools 600 steps, and
# the runaway ran at ~2.7 steps per round). The constant is named per-combat and
# documented as per-combat; it now is one.
#
# 40 is the right number and was all along: measured over 483 real combats of
# the trained policy, hero steps per combat run p50 13, p90 22, p95 25, p99 38,
# max 70 — so a 40-step cap cuts 0.62% of combats while capping a runaway at 40
# instead of 600. What those runaways contain is the dither the cap exists for:
# the worst one spent 53 of its 70 steps on __move__, battlefield_bandage,
# wyrd_reconstruction and absolution.
#
# **RAISED TO 80 ON 2026-08-24, AND THE OLD 40 WAS NOT MIS-MEASURED -- IT WAS
# THE WRONG STATISTIC.** Re-measured on the no-abandon checkpoint with the cap
# lifted to 150 so the tail was visible, Champion long reads mean 15.9, p95 28,
# **p99 exactly 40**, p99.5 46, max 65; tier 3 reads p99 33 with one 99-step
# outlier. So 40 really is the per-combat p99, exactly as recorded.
#
# The error is that a per-combat p99 was applied to a FIFTEEN-combat episode.
# A run draws from this distribution once per combat, so p99 per combat is
# `1 - 0.993**15` = **10% of long runs truncated** -- and a truncation is a
# pure loss, party alive with the clock gone. That is precisely what was
# measured: 8.3% of Champion long runs truncating, every one of them on the
# per-combat cap rather than the episode pool.
#
# 80 is set from the RUN-level target instead: nothing observed at Champion
# exceeds 65, so run-level truncation from this cap drops to roughly 1%, while
# a runaway is still bounded at 80 against the ~600-step (220-round) pathology
# the cap exists for -- 7.5x of headroom. The dither this guards against is
# unchanged and still real.
MAX_STEPS_PER_COMBAT = 80

# The EXPEDITION pool is a separate concern and deliberately did NOT move with
# the cap above. It was the same constant only by coincidence, and multiplying
# a raised anti-runaway guard by the combat count would have doubled the
# worst-case episode length -- paying for a pathology that does not happen in
# training cost that does. A long run averages 15.9 steps a combat, so 40 x N
# is already ~2.5x what a real expedition spends.
STEP_POOL_PER_COMBAT = 40

# Reward realignment (post-Milestone-10 fix) — the original design below
# rewarded "win every combat" (REWARD_WIN fires every internal combat, up
# to +90 over an 18-combat expedition) far more than it penalized the
# user's actual objectives (1. never lose a hero, 2. minimize final
# aggregate stress): REWARD_HERO_DEATH was a mere -3, and the stress term
# was purely potential-based, so it telescopes to ~0 over the episode and
# never entered the objective at all. 3M then 6M timesteps of training
# under that reward converged (near-zero movement 3M→6M) to a real but
# unwanted optimum: 0-8% zero-death rate, final stress pinned near the
# 200 heart-attack ceiling almost everywhere (see ASSUMPTIONS.md). Fixed
# by making the real (non-potential, optimum-changing) terms dominate:
# self._reward_win is lowered to a small per-combat progress signal,
# REWARD_HERO_DEATH is raised so a single death outweighs a full
# expedition's worth of per-combat wins, and two new real terminal terms
# (REWARD_EXPEDITION_COMPLETE, TERMINAL_STRESS_PENALTY_WEIGHT) put survival
# and final-stress minimization directly into the objective instead of
# leaving stress as shaping-only. The existing potential-based shaping
# (per-combat Φ from DarkestDungeonEnv.step(), reused unchanged via
# super().step(), plus W_EXPEDITION_STRESS · Φ_expedition below) is kept
# as-is — it's policy-invariant (Ng/Harada/Russell 1999) and only helps
# learning, it was never the misalignment.
# A HERO DEATH IS PRACTICALLY LOSING THE RUN -- Fran, 2026-08-24, and the old
# number said the opposite. At -20 against 15 combats x 8 plus 10 for
# completing, a death cost 15% of a run: pushing on with three heroes strictly
# beat turning back, and the policy correctly learned to do exactly that. The
# measured consequence was the whole long-band failure mode -- the first death
# lands 48% of the way in, 2.07 more follow it, and 81% of wipes happen two to
# three combats LATER rather than in the fight that took the hero.
REWARD_HERO_DEATH = -50.0
REWARD_EXPEDITION_COMPLETE = 10.0

# ENDING A COMBAT WELL IS THE OBJECTIVE, not ending it at all -- Fran:
# "lets go with a model that focuses on ending the battles on the best state as
# possible". The next fight can open with a surprise (40.5% of combats open
# with someone flat-footed), so the state a party carries out of one room is
# what decides whether the next one is survivable.
#
# THIRD DELIBERATE NON-POTENTIAL TERM. `ASSUMPTIONS.md`'s invariance argument
# covers the phi terms and named two exceptions (two-phase tempo,
# STRESS_TICK_PENALTY); this is the third and it is meant to change the optimal
# policy, which is the entire point. It cannot be potential-based: a potential
# term telescopes away across the episode and therefore cannot express "arrive
# at each fight in good shape" -- only the sum of what happened.
#
# It only ever pays on a WIN, so it cannot buy anything by avoiding combat, and
# the floor keeps a ragged win strictly better than a loss.
# WHEN A RUN COUNTS AS GONE WRONG, i.e. when ACTION_ABANDON is offered at all.
# See `_can_abandon` for the measurement that forced this gate to exist. Half
# HP is the same line the justification for the feature used; 60 stress is
# where a party is close enough to the 100 Resolve Check that the next fight
# can start producing afflictions.
#
# Falsifier: if a trained policy abandons on nearly every run that becomes
# eligible, the gate is doing the deciding rather than the policy, and the
# thresholds are too loose.
# Tightened once, on measurement. At HP 0.50 / stress 60 the gated policy
# still abandoned 62% of runs, at a mean 61% party HP and 0.05 deaths -- i.e.
# almost all of them firing on the STRESS clause, because a mid-run party
# routinely passes 60 mean stress without the run being lost. 80 is where the
# next Resolve Check starts producing afflictions, and 0.40 is nearer "no way
# to absorb an ambush" than half HP is.
ABANDON_HP_FRACTION = 0.40
ABANDON_STRESS = 80.0

COMBAT_END_CONDITION_FLOOR = 0.25
W_END_HP = 0.60
W_END_STRESS = 0.40
# Stress is scored against the Resolve Check threshold, not the 200 ceiling:
# 100 is where a hero rolls for an affliction, which is the thing being avoided.
END_STRESS_SCALE = 100.0
# Lowered from 10.0 when STRESS_TICK_PENALTY (env/gym_env.py) began charging
# for stress densely, as it is gained. The intent is to MOVE the stress cost
# from terminal to immediate, not to stack a new one on top: a sparse
# terminal penalty is discounted to ~13% of face value at gamma=0.99 across a
# ~200-step expedition, which is why it never disciplined Abomination's free
# Transform (measured at 1.77 uses per combat, ~160 stress per ally per
# expedition). A residual terminal term is kept so that *ending* in a good
# state still matters beyond the sum of the increments.
TERMINAL_STRESS_PENALTY_WEIGHT = 4.0
W_EXPEDITION_STRESS = 1.0

# Running out the step budget with the party alive used to be a genuinely
# attractive strategy: no deaths, no lose-reward, and the eval metric even
# scored it as a zero-death success (see eval_expedition.ExpeditionOutcome
# for how that was measured and fixed). Confirmed in real eval data — e.g.
# "Ballad Spam, BIS!!" medium band reported 33% zero-death with 0% actually
# completing, every one of those a stall-out. Truncation is now an explicit
# failure worth roughly a death, so dithering can't beat playing the
# expedition out. Simulator convention, like the other magnitudes here.
REWARD_TRUNCATION = -20.0

# WHAT AN AFFLICTION COSTS, and it is 0.0 by default.
#
# Fran, 2026-09-02: "an afflicted hero its really bad almost the same as a
# death as it increases all the overall stress, and you only have a 25% of
# virtue chance". Against that, the only price the reward has ever put on an
# affliction is `W_AFFLICTED = 0.15` -- and that term sits INSIDE Phi, so by
# Ng/Harada/Russell it cannot change the optimal policy at all, whatever its
# size. It fixes credit timing and nothing else. The pricing has therefore
# been, exactly, zero.
#
# THIS IS A NON-POTENTIAL TERM and it IS meant to move the optimum -- the
# third one here, after the two-phase tempo and STRESS_TICK_PENALTY, and it
# needs the invariance argument in ASSUMPTIONS.md read the same way those two
# did. Default 0.0 keeps every archived checkpoint's reward byte-identical,
# the discipline all five distribution knobs and `--stress-weight` follow.
#
# CHARGED ONCE PER HERO PER EXPEDITION, at the moment the affliction is first
# seen, which is the same shape REWARD_HERO_DEATH has. Not per onset: an
# affliction now ENDS at 0 stress, so a hero can be afflicted, cured and
# afflicted again, and charging each onset would price one hero's bad raid
# above another hero's death. Not at the terminal either -- that would pay a
# policy for afflicting a hero who then dies, since a dead hero is dropped
# from the roster the terminal reads.
#
# Sized against REWARD_HERO_DEATH (-50.0) by whoever runs the arm: "almost the
# same as a death" argues for something in the 15-25 range, and the flag takes
# the number so a sweep can answer it instead of this comment guessing.
AFFLICTION_PENALTY_DEFAULT = 0.0

# combats-remaining (as a fraction AND on an absolute scale), alive fraction,
# mean party stress, + 4 provision counts.
EXPEDITION_CONTEXT_FEATURES = 8

# Divisor turning "combats still to fight" into a roughly 0..1 observation
# without erasing its absolute magnitude. Set from the longest expedition the
# real game generates (EXPEDITION_LENGTH_BANDS["long"][1] == 16, derived from
# scripts/map_generator.darkest); values above 1.0 are fine and simply mean an
# unusually long run.
MAX_EXPECTED_COMBATS = 16.0

# THE CORRIDOR BETWEEN TWO FIGHTS, in tiles.
#
# MEASURED, not derived. `scripts/map_generator.darkest` gives
# `.base_corridor_number` and `.spacing 4`, but no corridor length -- spacing
# is a grid stride between room centres and reading it as a tile count would
# be a convention wearing game data's clothes. `bridge/dungeon_map.py`'s
# `Area.tiles` is the real thing, off a real `persist.map.json`, and
# `archive/traces/raidsnaps*` holds captures of seven distinct generated
# dungeons:
#
#     3 tiles   1    (2.1%)        mean   5.625
#     4 tiles   2    (4.2%)        median 5
#     5 tiles  24   (50.0%)        n = 48 corridors
#     6 tiles  12   (25.0%)
#     7 tiles   6   (12.5%)
#     8 tiles   2    (4.2%)
#     9 tiles   1    (2.1%)
#
# Fran's own `bat/play.log` agrees from a completely different direction: its
# `a fight started after N step(s)` lines are the walk the actuator actually
# performed, and they run 1-11 with a mean of 4.3 (n=23). Lower than 5.6
# because a fight can start part-way down a corridor -- which is the same
# quantity seen from the near end, and it brackets the map figure from below.
#
# A CONSTANT, not a draw. The sequence env is a list of combats with no map
# between them, so there is no corridor to measure per boundary; drawing one
# would consume `_sampler_rng` and put a different dungeon in front of the two
# arms of every A/B this project runs. The expected tick count is what matters
# and a constant delivers it exactly.
CORRIDOR_TILES = 5.625


def _spend_dot_ticks(dots: list, ticks: int) -> int:
    """Advance every DoT in `dots` by up to `ticks`, dropping the expired ones,
    and return the damage that cost.

    Mutates in place and truncates to what each DoT has left -- a 2-tick bleed
    in a 3-tick corridor deals two ticks, not three.
    """
    dealt = 0
    alive = []
    for dot in dots:
        spent = min(ticks, dot.remaining_ticks)
        dealt += spent * dot.damage_per_tick
        dot.remaining_ticks -= spent
        if dot.remaining_ticks > 0:
            alive.append(dot)
    dots[:] = alive
    return dealt


def hallway_dot_ticks(tiles: float, ratio: float) -> int:
    """How many DoT ticks a corridor of `tiles` tiles is worth.

    DETERMINISTIC, never a draw. Rolling a tick per tile would preserve the
    same expectation and shift the RNG stream for everything after it -- the
    discipline `.dot_irresistible` follows by SKIPPING its roll rather than
    rolling against a zero resist, and the reason two arms of an A/B can face
    the same dungeon at all.
    """
    return int(round(tiles * ratio))


class ExpeditionSequenceEnv(ExpeditionEnv):
    # `step` below ALWAYS returns its own `_get_obs()`, because it mutates the
    # world after `super().step()` returns -- a combat transition, a roster
    # snapshot, a retreat re-entering the same room. So the base's observation
    # was not just redundant, it was stale in exactly the cases that matter,
    # and it cost a second full build every step: 279 us of a 736 us step,
    # 38% of the env. See `DarkestDungeonEnv._BASE_STEP_RETURNS_OBS`.
    _BASE_STEP_RETURNS_OBS = False

    def __init__(self, render_mode: Optional[str] = None, sampler_seed: Optional[int] = None,
                 store=None, fixed_scenario: Optional[dict] = None,
                 loadout_random_p: float = 0.0, cross_region_p: float = 0.0,
                 class_balance: float = 0.0,
                 resolve_sampling: bool = False, kit_random_p: float = 0.0,
                 boss_quest_p: float = 0.0, hold_out_classes=frozenset(),
                 stress_weight: float = 1.0,
                 affliction_penalty: float = AFFLICTION_PENALTY_DEFAULT,
                 slot_permute: bool = False, hard_focus: bool = False,
                 allow_abandon: bool = True, blind_forecast: bool = False,
                 prevention_reward: bool = False,
                 focus_mechanic=None, focus_p: float = 0.5):
        # loadout_random_p is forwarded, not re-implemented. It lived only on
        # ExpeditionEnv, so `--full-expedition` -- the mode every shipped
        # checkpoint is trained in -- could not reach the knob at all, and the
        # -16.7pp sensitivity measurement had no way to become training.
        super().__init__(render_mode=render_mode, sampler_seed=sampler_seed,
                         store=store, loadout_random_p=loadout_random_p,
                         cross_region_p=cross_region_p,
                         class_balance=class_balance,
                         stress_weight=stress_weight,
                         slot_permute=slot_permute,
                         blind_forecast=blind_forecast,
                         prevention_reward=prevention_reward,
                         focus_mechanic=focus_mechanic, focus_p=focus_p,
                         resolve_sampling=resolve_sampling,
                         kit_random_p=kit_random_p,
                         boss_quest_p=boss_quest_p,
                         hold_out_classes=hold_out_classes)
        self.affliction_penalty = float(affliction_penalty)
        # A scripted expedition, replayed on every reset instead of sampling
        # one. SB3 calls `reset()` with no options, so a pinned scenario has to
        # live on the env rather than be passed per episode. The dict is
        # exactly what `reset(options=)` already accepts -- see
        # `data/tutorial_loader.py`, whose whole job is producing one.
        self._fixed_scenario = dict(fixed_scenario) if fixed_scenario else None
        # Encounter names for a scripted dungeon, one per combat, in order.
        self._enemy_script: list[str] = []
        # A scripted dungeon is scripted in its torch too: the level is known
        # (the Old Road ships `torchlight: 100`), and sampling the measured
        # distribution over it would train a fight in a dark it never sees.
        self._scripted_torch: Optional[float] = None
        # Per-combat progress reward. This has to cover what a combat now
        # COSTS, or refusing to fight becomes the optimal policy.
        #
        # It was 1.0 back when the only per-turn cost was TURN_PENALTY =
        # -0.02. Since then the threat-phase turn cost rose to -0.10 and a
        # dense per-point stress charge was added — roughly a 10x increase in
        # what a combat costs — without this being revisited. Measured on the
        # resulting model: mean reward per COMPLETED combat was -0.03 for
        # "Riposte Style (Hag)" (15 steps, breaks even, still fights) but
        # -7.15 for "Stun Control: Beast" (31 steps). For that comp, eight
        # remaining combats at -7 each is far worse than a single -20
        # truncation discounted to about -2.6, so stalling was correct play —
        # and it duly stalled 82% of long-band episodes while keeping stress
        # low (53.5) by simply not fighting.
        #
        # Set so a slow, stressful combat is around break-even and a clean
        # one is clearly positive, making progress dominate refusal for every
        # comp rather than only the efficient ones. _reward_lose stays at the
        # inherited -5.0; deaths already do the heavy lifting there.
        self._reward_win = 8.0

        # 50 (per-combat, unchanged) + 7 expedition-context floats:
        # combats_remaining_frac, combats_remaining_abs, alive_hero_frac,
        # mean_party_stress_frac, and one remaining-fraction per tracked
        # in-combat provision type (bandage, antivenom, holy_water, laudanum).
        # Base per-combat observation (rank-indexed slots + skill slots +
        # globals, see env/gym_env.py) plus the expedition-context floats
        # added by this class's own _get_obs — derive the count from
        # EXPEDITION_CONTEXT_FEATURES, never hardcode the width.
        self.observation_space = spaces.Box(
            low=-np.inf, high=np.inf,
            shape=(self.base_obs_size + EXPEDITION_CONTEXT_FEATURES,), dtype=np.float32,
        )

        self._comp_name: Optional[str] = None
        self._zone_id: Optional[str] = None
        self._tier: int = 1
        self._length_band: str = "medium"
        self._num_combats: int = 1
        self._combat_idx: int = 0
        # Combats the party broke off instead of clearing. Not a failure and
        # not a completion — the distinction the SUCCESS metric turns on, and
        # the reason `engine.winner` gained a third value rather than folding a
        # retreat into "enemies".
        self._combats_retreated: int = 0
        self._step_count: int = 0
        self._max_steps: int = 0
        # Steps spent in the CURRENT combat — the per-combat half of the budget
        # (see MAX_STEPS_PER_COMBAT); _step_count is the expedition total.
        self._combat_step_count: int = 0

        self._original_hero_ids: list[str] = []
        # Per-hero, not one shared int: a sampled party is uniform by
        # convention (TIER_TO_HERO_LEVEL) but a real roster is four
        # independent resolve levels, and bridge/rollout.py passes those.
        self._original_hero_levels: list[int] = []
        self._original_hero_skill_ids: Optional[list[list[str]]] = None
        self._original_trinkets_and_quirks: Optional[list[tuple[list[str], list[str]]]] = None
        # Handed to `_on_board_ready` for the combat currently being built.
        # `_hp_targets` depends on that combat's carry_over, so it cannot be
        # recomputed from inside the hook.
        self._pending_hp_targets = None
        # (weapon_rank, armour_rank) per hero, or None for the sampled-comp
        # convention where gear tier follows resolve level. See
        # CombatEngine.apply_gear_ranks.
        self._original_gear_ranks: Optional[list[tuple[int, int]]] = None
        self._alive_indices: list[int] = []
        self._carry_over_by_index: dict[int, CarryOverHeroState] = {}
        self._provision_inventory: dict[str, int] = {}
        self._original_camp_skill_ids: Optional[list[list[str]]] = None
        # Combat indices AFTER which the party makes camp (see _camp_schedule).
        self._camp_after: set[int] = set()
        self._camps_made: int = 0
        self._camp_ambushes: int = 0
        self._hunger_checks: int = 0
        self._hunger_starved: int = 0
        self._meals_eaten: int = 0
        self._abandoned: bool = False
        # WITHHOLDING THE EXIT DURING TRAINING. Default True, so nothing
        # changes unless asked. The reason it exists: the end-state checkpoint
        # abandons 72% of Champion long runs, and forced to play them out it
        # wipes 43 of 43 -- but that forced arm is OFF-DISTRIBUTION, because a
        # policy trained with an exit never learns the states past it. The
        # food-era policy, which had no exit, completed ~37% of the same band.
        # So the exit may be suppressing play quality rather than reflecting
        # it, and `--no-abandon-train` is the one-run test: withhold it while
        # training, restore it at evaluation.
        self.allow_abandon = bool(allow_abandon)
        # Uniform unless `--hard-focus`, so an archived distribution is still
        # reproducible. Weights, not a filter: every band and tier stays
        # reachable, because a policy that has only ever seen Champion long
        # runs would be a different kind of overfit.
        self._band_weights = dict(
            HARD_FOCUS_BAND_WEIGHTS if hard_focus else UNIFORM_BAND_WEIGHTS)
        self._tier_weights = dict(
            HARD_FOCUS_TIER_WEIGHTS if hard_focus else UNIFORM_TIER_WEIGHTS)
        self.hard_focus = bool(hard_focus)

    # ── Setup ──

    def _resolve_camp_skill_ids(self, comp_name: Optional[str],
                                hero_ids: list[str]) -> Optional[list[list[str]]]:
        """Per-hero camp skills from the comp's real loadout, positional.

        `HeroLoadout.camp_skill_ids` has been parsed by CompLoadoutLoader since
        the loadout work but had no consumer until camping existed. Same
        defensive equality check as _resolve_trinkets_and_quirks: bail to None
        rather than risk applying one hero's kit to another.
        """
        if not comp_name:
            return None
        loadout = self.store.comp_loadout_by_name(comp_name)
        if not loadout:
            return None
        heroes = [h for h in loadout.heroes if h.hero_id in self.store.heroes]
        if [h.hero_id for h in heroes] != hero_ids:
            return None
        return [list(h.camp_skill_ids) for h in heroes]

    def _weighted_pick(self, weights):
        """One draw from `_sampler_rng`, whatever the weights.

        Deliberately draws exactly ONCE regardless of the distribution, so a
        weighted run and a uniform run consume the sampler stream at the same
        rate and a reseeded scenario still reproduces. Same reason the surprise
        rolls are both always taken.
        """
        keys = list(weights)
        total = float(sum(weights[k] for k in keys))
        r = self._sampler_rng.random() * total
        acc = 0.0
        for k in keys:
            acc += weights[k]
            if r < acc:
                return k
        return keys[-1]

    def _can_abandon(self) -> bool:
        """Abandoning is legal only when the run has actually gone wrong.

        APPROXIMATION on WHEN: the real game abandons from the map screen
        between rooms, so a mid-combat abandon here is really "retreat, then
        abandon" collapsed into one decision. That part is harmless -- the room
        is forfeited either way.

        THE TROUBLE GATE IS NOT COSMETIC, and it is not what I first shipped.
        Offering abandon on every turn was measured over a 400k probe and the
        policy collapsed onto it: **75% of runs abandoned, a median of 12% of
        the way in, at 74% party HP with 0.00 heroes dead, and not one wipe.**
        A safe terminal worth ~0 beats playing on whenever the policy's own
        value estimate is negative, which it is early in training, and once it
        stops exploring the far side it never learns otherwise.

        The repair is not an invented penalty but a narrowing of the legal set,
        the same move `_is_wasted_heal` makes on a strictly dominated action.
        The justification for modelling abandonment at all was "the move a real
        player makes at half HP with no food and a hero already dead" -- so
        that is exactly when it is offered, and a healthy party simply has no
        exit to take.

        Simulator convention, and the thresholds are the falsifiable part.
        """
        if not self.allow_abandon:
            return False
        alive = [h for h in self.engine.heroes if h.is_alive()]
        if not alive:
            return False
        if len(alive) < len(self.engine.heroes):
            return True                      # someone is already dead
        cap = sum(h.max_hp for h in alive)
        if cap and sum(h.hp for h in alive) / cap < ABANDON_HP_FRACTION:
            return True
        mean_stress = sum(h.stress for h in alive) / len(alive)
        return mean_stress >= ABANDON_STRESS

    def _abandon(self):
        """End the expedition with the party alive, and walk them home.

        Everything a normal terminal step does EXCEPT the two things that
        reward finishing: no win reward (no combat was resolved) and no
        REWARD_EXPEDITION_COMPLETE. So the price is entirely emergent, the same
        design retreat uses -- what abandoning costs is every remaining
        combat's win reward, and what it buys is not paying REWARD_HERO_DEATH
        for the heroes that would have died.
        """
        self._abandoned = True
        phi_before = self._compute_potential()
        phi_exp_before = self._compute_expedition_potential()
        # Terminal on BOTH potentials, matching what a real episode end does.
        reward = SHAPING_GAMMA * 0.0 - phi_before
        reward += W_EXPEDITION_STRESS * (SHAPING_GAMMA * 0.0 - phi_exp_before)
        reward -= (self.stress_weight * TERMINAL_STRESS_PENALTY_WEIGHT
                   * self._mean_roster_stress_frac())
        info = {"winner": "abandoned", "abandoned": True,
                "combats_done": self._combat_idx,
                "combats_planned": self._num_combats}
        return self._get_obs(), reward, True, False, info

    def win_reward(self) -> float:
        """Clearing a combat pays for the STATE the party finishes in.

        `combat_end_condition()` is 1.0 for a full-HP, unstressed, intact party
        and 0.0 for one that barely stood up, so the payout runs from
        `_reward_win` down to `COMBAT_END_CONDITION_FLOOR` of it. Over a
        15-combat run that is a ~90 point spread between playing clean and
        playing ragged -- larger than completing the expedition is worth, which
        is deliberate: finishing every room in tatters is not the goal.
        """
        cond = self.combat_end_condition()
        return self._reward_win * (COMBAT_END_CONDITION_FLOOR
                                   + (1.0 - COMBAT_END_CONDITION_FLOOR) * cond)

    def combat_end_condition(self) -> float:
        """How well the party is standing, in [0, 1].

        HP fraction and stress, over the LIVING party. A hero lost during the
        combat is not counted here -- `REWARD_HERO_DEATH` prices that, and
        counting it twice would make one death dominate the term it is meant to
        modulate. Death's Door is treated as zero HP for this purpose: a hero
        one hit from dying is not a hero the party can take into an ambush.
        """
        alive = [h for h in self.engine.heroes if h.is_alive()]
        if not alive:
            return 0.0
        cap = sum(h.max_hp for h in alive)
        hp = sum(0 if h.is_deaths_door else h.hp for h in alive)
        hp_frac = (hp / cap) if cap > 0 else 0.0
        stress = sum(h.stress for h in alive) / len(alive)
        stress_ok = max(0.0, 1.0 - stress / END_STRESS_SCALE)
        return max(0.0, min(1.0, W_END_HP * hp_frac + W_END_STRESS * stress_ok))

    def _built_districts(self):
        """Which districts this expedition is treated as having built.

        Empty below Veteran, so an Apprentice expedition is byte-identical to
        the pre-districts behaviour and tiers 1-2 stay a control group.
        """
        if not DISTRICTS_ENABLED:
            return set()
        return districts_at_tier(self.store.districts, self._tier)

    def _deliberate_eat(self) -> int:
        """Spend food to heal, the way a player does between rooms.

        Reserves what the remaining hunger checks are expected to demand, so
        eating can never CAUSE a starvation -- which costs 20% max HP against
        the 5% a meal restores, i.e. four meals wasted to buy one.
        """
        alive = [h for h in self.engine.heroes if h.is_alive()]
        if not alive:
            return 0
        frac = float(self.store.rules.provision_hp_heal)
        if frac <= 0.0:
            return 0

        built = self._built_districts()
        if hunger_disabled(self.store.districts, built):
            reserve = 0
        else:
            remaining = max(0, self._num_combats - self._combat_idx - 1)
            reserve = int(round(remaining * HUNGER_CHECK_CHANCE * len(alive)))
        # `max_provisions_before_full` is per HERO, and one meal here feeds
        # every living hero one food, so it bounds the meals at this stop.
        cap = int(getattr(self.store.rules, "max_provisions_before_full",
                          EAT_MAX_MEALS_PER_STOP))
        meals = 0
        while meals < max(1, cap):
            have = self._provision_inventory.get("food", 0)
            if have - len(alive) < reserve:
                break
            if all(h.hp >= EAT_TOPUP_FRACTION * h.max_hp for h in alive):
                break
            self._provision_inventory["food"] = have - len(alive)
            for h in alive:
                # `hp_heal_received_percent` sub_type 'eat' is the Granary's
                # own buff and reaches the hero through the ordinary buff
                # machinery, so it is already on `h` by the time we read it.
                gain = round(h.max_hp * frac * h.eat_heal_received_mult)
                h.hp = min(h.max_hp, h.hp + max(1, gain))
            meals += 1
        self._meals_eaten += meals
        return meals

    @staticmethod
    def _camp_schedule(num_combats: int, firewood: int) -> set[int]:
        """Which combat indices the party camps AFTER.

        Firewood by raid length is real and user-confirmed: **0 short, 1
        medium, 2 long** (FIREWOOD_PER_BAND). Short granting none is
        load-bearing when reading a retrain — the short band cannot move
        because of camping, so if it does, the cause is elsewhere.

        Campfires are spread evenly rather than front- or back-loaded: the
        real game places them at the player's discretion, and even spacing is
        the neutral choice that neither hoards recovery for a final push nor
        wastes it on a fresh party.
        """
        if firewood <= 0 or num_combats <= 1:
            return set()
        firewood = min(firewood, num_combats - 1)
        return {
            max(1, min(num_combats - 1, round((i + 1) * num_combats / (firewood + 1))))
            for i in range(firewood)
        }

    def _make_camp(self) -> None:
        """Resolve one campfire against the CURRENT (post-combat, pre-reset)
        hero states, so its HP/stress recovery flows straight into the
        carry_over that _snapshot_and_advance_roster is about to build.

        Camp skills are indexed POSITIONALLY against the surviving roster —
        never a hero_id-keyed dict. 37 real comps field duplicate classes with
        genuinely different loadouts, and a dict silently collapses them.
        """
        if self._original_camp_skill_ids is None:
            return
        camp_skills = [self._original_camp_skill_ids[i] for i in self._alive_indices]
        extra = party_camping_point_bonus(
            self.store.districts,
            [getattr(self.store.heroes.get(h.definition_id), "tags", ())
             for h in self.engine.heroes],
            self._built_districts())
        # **The camp MEAL, from the party's own ration.** A feast is the only
        # stress relief in the game that costs no respite points, and until
        # 2026-09-02 the simulator skipped the food phase entirely -- so a
        # medium or long run camped without eating, which is not a thing a
        # player has ever done. `meals_table` is shipped; see `choose_meal`.
        food_id = "food"
        result = resolve_camp(self.engine.rng, self.store, self.engine.heroes,
                              camp_skills, apply_buff=self.engine._apply_buff_to_stats,
                              extra_points=extra,
                              food=int(self._provision_inventory.get(food_id, 0)))
        if result.food_spent:
            self._provision_inventory[food_id] = max(
                0, int(self._provision_inventory.get(food_id, 0)) - result.food_spent)
        self._camps_made += 1
        self._camp_buffs_by_slot = result.granted_buffs
        if result.ambush:
            # A night ambush is a real extra battle, so the expedition grows by
            # one combat and gets the step budget to fight it. Without the
            # budget bump the ambush would be scored as the party's failure to
            # finish rather than as the hazard it is.
            self._camp_ambushes += 1
            self._num_combats += 1
            self._max_steps += STEP_POOL_PER_COMBAT
        for line in result.log:
            self.engine.log.append(line)

    def _build_starting_provisions(self, hero_ids: list[str]) -> dict[str, int]:
        mult = LENGTH_BAND_PROVISION_MULT[self._length_band]
        inventory: dict[str, int] = {}
        for pid, base in BASE_PROVISION_AMOUNTS.items():
            cap = self.store.provisions[pid].stack_limit if pid in self.store.provisions else 999
            inventory[pid] = min(cap, round(base * mult))
        for hid in hero_ids:
            bonus_pid = self.store.hero_class_starting_provisions.get(hid)
            if bonus_pid in inventory:
                cap = self.store.provisions[bonus_pid].stack_limit if bonus_pid in self.store.provisions else 999
                inventory[bonus_pid] = min(cap, inventory[bonus_pid] + 1)
        inventory["food"] = FOOD_AMOUNTS[self._length_band]
        # The Granary. Its shipped range is 4-10 and the game rolls it, but the
        # roll is rerollable from the provision screen, so a player who wants
        # the maximum takes it -- Fran's call, and modelling the roll would add
        # variance no real player is exposed to.
        inventory["food"] += food_bonus(self.store.districts, self._built_districts())
        return inventory

    def reset(self, *, seed: Optional[int] = None, options: Optional[dict] = None):
        options = dict(options or self._fixed_scenario or {})

        # SCENARIO REPRODUCIBILITY, opt-in. `seed=` reaches the ENGINE rng
        # only, so the torch level, which monsters are drawn and whether the
        # quest is a boss quest all come from `_sampler_rng` -- constructed
        # once and never reseeded. An eval built with `sampler_seed=None` was
        # therefore NOT reproducible: two runs of one command on one
        # checkpoint faced different dungeons, measured at 91.8% vs 93.8% on
        # the short band back to back.
        #
        # Opt-in rather than derived from `seed`, because training must stay
        # byte-identical: each SubprocVecEnv worker gets `sampler_seed + i`
        # precisely so the workers see DIFFERENT scenarios, and reseeding from
        # a per-episode `seed` would collapse that.
        if options.get("sampler_seed") is not None:
            # `seed_scenario`, not `_sampler_rng.seed`: loadouts are a SECOND
            # stream (`_loadout_rng`) and pinning a scenario means pinning
            # both. With the randomisation knobs off the two are equivalent --
            # the draws happen but decide nothing -- so this changes no
            # existing result, and it is what makes a randomised-loadout eval
            # paired at all.
            self.seed_scenario(options["sampler_seed"])

        if "hero_ids" in options:
            # Explicit scenario — bypass sampling entirely (matches
            # ExpeditionEnv's own convention for direct testing/eval).
            hero_ids = options["hero_ids"]
            self._comp_name = options.get("comp_name")
            self._zone_id = options.get("zone_id")
            self._tier = options.get("tier", 3)
            # An explicit scenario keeps the old behaviour unless the caller
            # asks, the same rule the kit and loadout knobs follow -- every
            # pinned-comp path (`eval_expedition`, `bridge/rollout`,
            # `tools/comp_ga`) is byte-identical unless it opts in.
            self._quest_difficulty_level = options.get("quest_difficulty_level")
            self._length_band = options.get("length_band", "medium")
            self._num_combats = options.get("num_combats", EXPEDITION_LENGTH_BANDS[self._length_band][0])
            hero_skill_ids = options.get("hero_skill_ids")
            hero_levels = options.get("hero_levels")
            self._original_gear_ranks = options.get("gear_ranks")
            explicit_kit = options.get("trinkets_and_quirks")
            explicit_camp_skills = options.get("camp_skill_ids")
            initial_carry_over = options.get("carry_over")
        else:
            comp_name, zone_id, hero_ids = self._sample_comp()
            self._comp_name = comp_name
            self._zone_id = zone_id
            self._tier = self._weighted_pick(self._tier_weights)
            self._length_band = self._weighted_pick(self._band_weights)
            lo, hi = EXPEDITION_LENGTH_BANDS[self._length_band]
            self._num_combats = self._sampler_rng.randint(lo, hi)
            # **Set where the tier is PICKED, not only where a combat starts.**
            # The first combat of a sequence goes through the base env's own
            # `reset`, which reads this attribute; `_start_next_combat` passes
            # the same value explicitly for every combat after it. Setting it
            # in one place and reading it in two is what made the first attempt
            # charge nothing at all on the opening fight.
            self._quest_difficulty_level = TIER_TO_QUEST_DIFFICULTY.get(self._tier)
            hero_skill_ids = self._resolve_skill_ids(comp_name, hero_ids)
            hero_levels = None
            self._original_gear_ranks = None
            explicit_kit = None
            explicit_camp_skills = None
            initial_carry_over = None

        self._original_hero_ids = hero_ids
        # Sampled ONCE per expedition and carried, not redrawn per combat:
        # a hero's resolve is a property of the hero, and rerolling it between
        # fights would quietly re-equip the party mid-dungeon.
        self._original_hero_levels = list(hero_levels) if hero_levels else (
            self.sample_hero_levels(self._tier, len(hero_ids)))
        self._original_hero_skill_ids = hero_skill_ids
        # An explicit kit wins over the comp lookup — a real party has no
        # comp_name to resolve against, and passing one that half-matches would
        # be worse than passing none.
        self._original_trinkets_and_quirks = explicit_kit or (
            self._resolve_trinkets_and_quirks(self._comp_name, hero_ids) if self._comp_name else None
        )
        self._alive_indices = list(range(len(hero_ids)))
        self._carry_over_by_index = {}
        self._provision_inventory = self._build_starting_provisions(hero_ids)
        self._combat_idx = 0
        self._combats_retreated = 0
        self._step_count = 0
        self._combat_step_count = 0
        self._max_steps = STEP_POOL_PER_COMBAT * self._num_combats
        self._original_camp_skill_ids = self._resolve_camp_skill_ids(self._comp_name, hero_ids)
        self._camp_after = self._camp_schedule(
            self._num_combats, FIREWOOD_PER_BAND.get(self._length_band, 0))
        self._camps_made = 0
        self._camp_ambushes = 0
        self._hunger_checks = 0
        self._hunger_starved = 0
        self._corridor_dot_damage = 0
        self._corridor_downed = 0
        # Roster indices already charged `affliction_penalty` this expedition.
        self._afflicted_charged: set[int] = set()
        self._meals_eaten = 0
        self._abandoned = False
        if explicit_camp_skills is not None:
            # A real party has no comp_name to look camp skills up by, and
            # leaving them empty is not neutral: it silently removes the
            # campfire's whole recovery from medium and long runs, biasing any
            # comparison in favour of the short band.
            self._original_camp_skill_ids = [list(s) for s in explicit_camp_skills]

        self._enemy_script = [str(n) for n in (options.get("enemy_comp_names") or [])]

        # Decided ONCE per expedition, before any combat is drawn: a boss quest
        # is a property of the quest, not a coin flipped at the last room.
        # From `_sampler_rng`, like the monsters and the torch, so a reseeded
        # scenario reproduces whether it was one.
        self._is_boss_quest = (
            bool(options.get("boss_quest")) if options is not None
            and "boss_quest" in options
            else (self._boss_quest_p > 0.0
                  and self._sampler_rng.random() < self._boss_quest_p))

        first_hero_ids = [self._original_hero_ids[i] for i in self._alive_indices]
        first_hero_levels = [self._original_hero_levels[i] for i in self._alive_indices]
        first_hero_skill_ids = (
            [self._original_hero_skill_ids[i] for i in self._alive_indices]
            if self._original_hero_skill_ids is not None else None
        )
        entry = (self._scripted_enemy(0) if self._enemy_script
                 else self._draw_encounter(0))
        # Kept because a RETREAT does not consume the encounter -- see
        # _start_next_combat's `entry=`. Re-drawing would send the party back
        # into the room it just fled and find different monsters in it.
        self._current_entry = entry

        # An explicit torch is the CURRENT combat's, read off a real save — the
        # rest of the dungeon is unknown, so later combats go back to sampling.
        # Unless the dungeon is SCRIPTED, in which case the rest of it is known
        # too and the torch is pinned with the encounters.
        opening_torch = (options["torch_level"] if options.get("torch_level") is not None
                         else self._sample_torch_level())
        self._scripted_torch = (opening_torch if self._enemy_script
                                and options.get("torch_level") is not None else None)
        self._current_torch = opening_torch

        combat_options = {
            "hero_ids": first_hero_ids,
            "enemy_ids": entry.enemy_ids,
            "hero_levels": first_hero_levels,
            "provisions": self._provision_inventory,
            "torch_level": opening_torch,
            # The opening combat goes through `DarkestDungeonEnv.reset`, which
            # builds a FRESH engine -- so the zone has to travel in the options
            # here as well as in `_start_next_combat`, or the first fight of
            # every expedition would be the one place `in_dungeon` quirks still
            # paid out everywhere.
            "zone_id": self._zone_id or "",
            # **The same reason the zone travels here.** The opening combat
            # goes through `DarkestDungeonEnv.reset`, and `ExpeditionEnv.reset`
            # clears its own `_quest_difficulty_level` on the explicit-scenario
            # path -- so an attribute set on this env would be wiped before the
            # engine ever saw it, and the first fight of every expedition would
            # be the one place the underleveled penalty did not apply.
            "quest_difficulty_level": self._quest_difficulty_level,
        }
        if first_hero_skill_ids is not None:
            combat_options["hero_skill_ids"] = first_hero_skill_ids
        if initial_carry_over is not None:
            # Opening a rollout mid-dungeon: the party starts damaged, stressed
            # and possibly afflicted, which is the whole point of asking.
            combat_options["carry_over"] = [initial_carry_over[i] for i in self._alive_indices]
        # combat_options always has both hero_ids and enemy_ids, so
        # ExpeditionEnv.reset()'s own auto-sampling branch never triggers —
        # this just passes straight through to DarkestDungeonEnv.reset().
        self._pending_hp_targets = self._hp_targets(combat_options.get("carry_over"))
        try:
            obs, info = super().reset(seed=seed, options=combat_options)
        finally:
            self._pending_hp_targets = None
        return self._get_obs(), info

    def _scripted_enemy(self, combat_idx: int):
        """The `EnemyCompEntry` this combat is scripted to face.

        Looked up by the mash entry's own `name`, which only `named:` entries
        have -- and `named` is deliberately excluded from the random training
        pool (`ENEMY_CATEGORIES_FOR_TRAINING`), so a scripted encounter cannot
        leak into a sampled expedition and a sampled one cannot be scripted by
        accident.

        Raises rather than falling back to sampling. A typo in a script that
        silently became "some random encounter of that zone" would be a fight
        nobody could reproduce, reported as the policy having got worse.
        """
        name = self._enemy_script[min(combat_idx, len(self._enemy_script) - 1)]
        entry = next((e for e in self.store.enemy_comps if e.name == name), None)
        if entry is None:
            raise ValueError(f"no enemy comp named {name!r} in the loaded data")
        return entry

    def _hp_targets(self, carry_over):
        """Intended HP per living hero, or None to leave HP untouched.

        Only the real-party path (`gear_ranks` given) needs this — see
        `_apply_persistent_equipment_for_current_roster`. With no carry_over the
        party is starting fresh, so the target is "full", expressed as a value
        the max-HP clamp will cap rather than as a separate sentinel."""
        if not self._original_gear_ranks:
            return None
        if carry_over is None:
            return [10 ** 6] * len(self._alive_indices)
        return [c.hp for c in carry_over]

    def _on_board_ready(self):
        """Gear, trinkets, quirks and the HP ceiling, applied while the board
        is being built rather than after `reset()` has returned.

        Overrides ExpeditionEnv's version outright: this env never reaches
        that one's sampled branch (it always passes explicit hero_ids and
        enemy_ids), and everything persistent it fields goes through
        `_original_gear_ranks` / `_original_trinkets_and_quirks` instead.
        """
        self._apply_persistent_equipment_for_current_roster(
            hp_targets=self._pending_hp_targets)

    def _apply_persistent_equipment_for_current_roster(self, hp_targets=None):
        """Everything a hero carries for the WHOLE expedition rather than for
        one fight: real gear ranks, then trinkets and quirks, then HP.

        Reapplied at the start of every individual combat because
        `CombatEngine.reset()` rebuilds fresh CombatantStates each time —
        unlike buffs and DoTs, which correctly reset per combat. Filtered to
        `_alive_indices` so a hero lost in an earlier combat doesn't misalign
        the kit against the shrunken current roster.

        Order matters and is the engine's own (base stats, then buffs), the same
        one `bridge/seed.py` follows against a real save: `apply_gear_ranks`
        moves the max-HP ceiling and `health_stone` moves it again on top.

        **`hp_targets` is why HP comes last.** `_apply_carry_over` clamps
        `hp = min(carry.hp, max_hp)` inside `reset()`, against a ceiling that is
        still the resolve-derived one. Raise the ceiling afterwards and the
        clamped value never catches up — a Crusader with rank-5 armour at
        resolve 4 opened at 54/67 instead of full, and worse, it compounds:
        every combat of an expedition re-clamps against the low ceiling, so a
        real party would quietly bleed HP between fights it never lost.
        Passed (aligned with `_alive_indices`) only on the explicit real-party
        path; `None` leaves HP exactly as the sampled-comp path has always set
        it. A target above max simply means full."""
        built = self._built_districts()
        # Empty below Veteran, which keeps the per-hero branch below unentered
        # and the whole path byte-identical to the pre-districts code.
        # Memoised per class within one call: a comp with three Crusaders
        # resolves the same tag list once.
        district_ids: dict[str, list[str]] = {}
        for k, orig_idx in enumerate(self._alive_indices):
            hero = self.engine.heroes[k]
            if self._original_gear_ranks:
                weapon_rank, armour_rank = self._original_gear_ranks[orig_idx]
                self.engine.apply_gear_ranks(
                    hero, self._original_hero_levels[orig_idx], weapon_rank, armour_rank)
            if self._original_trinkets_and_quirks:
                trinket_ids, quirk_ids = self._original_trinkets_and_quirks[orig_idx]
                if trinket_ids or quirk_ids:
                    self.engine.apply_trinkets_and_quirks(hero, trinket_ids, quirk_ids)
            # Districts last the whole campaign, so they belong here with the
            # gear and the trinkets rather than anywhere per-combat -- and
            # BEFORE hp_targets, because `districts_training_ring_2` is a
            # `combat_stat_multiply max_hp` and moves the ceiling the target is
            # clamped against, exactly as `health_stone` does.
            if built:
                ids = district_ids.get(hero.definition_id)
                if ids is None:
                    hero_def = self.store.heroes.get(hero.definition_id)
                    ids = hero_district_buff_ids(
                        self.store.districts, getattr(hero_def, "tags", ()), built)
                    district_ids[hero.definition_id] = ids
                if ids:
                    # A HERO AT FULL HP STAYS AT FULL. `districts_training_ring_2`
                    # is `combat_stat_multiply max_hp`, and `_apply_buff_to_stats`
                    # only ever clamps HP DOWN to a new ceiling -- correct for a
                    # mid-combat buff, which must not heal. But a district is
                    # permanent roster equipment that is already in force when the
                    # raid begins, so applying it after `reset()` has filled the
                    # party left a training_ring hero opening every combat at
                    # 49/53. That is the `health_stone` trap the `hp_targets`
                    # machinery exists for, and it COMPOUNDS across an expedition.
                    # A hero who is genuinely hurt keeps their absolute HP, which
                    # is what a bigger pool means.
                    was_full = hero.hp >= hero.max_hp
                    self.engine._apply_buff_ids(hero, ids)
                    if was_full:
                        hero.hp = hero.max_hp
            if hp_targets is not None:
                hero.hp = max(0, min(int(hp_targets[k]), hero.max_hp))

    # ── Sequencing ──

    def _use_provisions_between_combats(self) -> int:
        """Bandage a bleed, antivenom a blight, laudanum a Horror -- in the
        corridor, which is where the game permits it.

        Each is gated on the condition it treats, the same gate the in-combat
        version carried and for the same reason: a provision is a finite
        resource that has to last 8-18 combats, and its cost is invisible at
        the moment of use. 82% of the trained policy's in-combat provision
        uses were wasted when nothing stopped it.

        **This was a pure no-op when it was first written, and the fix was in
        `CarryOverHeroState`.** DoTs did not cross the combat boundary, so a
        bleed cured here was a bleed the next `reset()` was about to clear for
        free -- the code spent a finite provision to prevent nothing. The
        game's own `dotHallwayStepRatio` says a DoT walks the corridor with the
        party; carrying it is what gives this method something to do.

        Two of the six item effects are deliberately absent, both because the
        condition they key on is not available at this moment:

        * `holy_water` is a PROACTIVE buff rather than a cure -- there is no
          condition to gate it on, so "use it when it would help" has no honest
          reading here, and applying it after every fight would spend the whole
          stock on the corridor rather than on the fight it is meant to open.
        * `medicinal_herbs` is `.clear_debuff 1`, and a combat debuff does not
          reach the corridor here: buffs are the one class `CarryOverHeroState`
          still drops, faithfully, since their durations are in ROUNDS and a
          corridor has none. So there is nothing to clear, and gating it on
          nothing would spend the stock the way the in-combat version did.
          `docs/ASSUMPTIONS.md` carries the falsifier.

        Recorded rather than guessed at, like every other two-reading key.

        Returns how many were used.
        """
        used = 0
        for hero in self.engine.heroes:
            if not hero.is_alive():
                continue
            for pid, has_condition in (("bandage", bool(hero.bleeds)),
                                       ("antivenom", bool(hero.blights)),
                                       ("laudanum", bool(hero.stress_dots))):
                if not has_condition:
                    continue
                if self._provision_inventory.get(pid, 0) <= 0:
                    continue
                self._provision_inventory[pid] -= 1
                if pid == "bandage":
                    hero.bleeds.clear()
                elif pid == "antivenom":
                    hero.blights.clear()
                else:
                    hero.stress_dots.clear()
                used += 1
        self._provisions_used = getattr(self, "_provisions_used", 0) + used
        return used

    def _snapshot_and_advance_roster(self, completed: bool = True):
        """Called once a combat has ended and more combats remain — rolls
        the food auto-heal (if available) and builds the carry_over state
        for whoever survived, dropping anyone who actually died.

        `completed=False` is a RETREAT: the party broke off and is standing in
        the corridor it came from, so neither of the between-rooms recoveries
        has happened. It has not eaten (the meal is what the party does having
        cleared a room) and it has not camped (`_combat_idx` has not advanced,
        so the camp still owes itself to whoever finishes this fight). Getting
        either wrong would make fleeing a way to BUY recovery, which is the
        opposite of what it costs.
        """
        # THE HUNGER CHECK, replacing an unconditional per-combat auto-heal.
        # A check costs one food PER HERO and either feeds everyone or starves
        # them; it is not a free 5% after every fight. See the constants above.
        fed = starved = False
        # The Mill (Color of Madness, Champion-only) carries
        # `DistrictDisableBuffData .disabled_type "hunger"`, which removes the
        # check entirely rather than making it cheaper. The roll is SKIPPED
        # rather than rolled-and-discarded, the `dot_irresistible` precedent:
        # drawing when the draw cannot matter desynchronises every trace
        # comparison after it.
        hunger_off = hunger_disabled(self.store.districts, self._built_districts())
        if (completed and not hunger_off
                and self.engine.rng.random() < HUNGER_CHECK_CHANCE):
            alive = sum(1 for h in self.engine.heroes if h.is_alive())
            have = self._provision_inventory.get("food", 0)
            if have >= alive:
                self._provision_inventory["food"] = have - alive
                fed = True
            else:
                # Partial food does not partially feed: the party goes hungry.
                # Modelling a partial feed would need a rule for WHO eats, and
                # no shipped key states one.
                self._provision_inventory["food"] = 0
                starved = True
            self._hunger_checks += 1
            if starved:
                self._hunger_starved += 1

        # THE PROVISIONS, at the moment the game actually allows them. The
        # engine used to offer bandage/antivenom/holy_water/laudanum as free
        # in-combat actions, which `cant_use_items_in_battle` forbids outright;
        # they belong here, beside the meal, in the corridor after a fight.
        #
        # NOT gated on `completed`, unlike the meal and the campfire. Those
        # are recoveries the game grants for clearing a room; using an item
        # is a player action, and a party that fled is standing in a corridor
        # with the same pack and the same bleed. It is also the moment a
        # bandage matters most -- the fled encounter is re-taken at full
        # strength and the party would otherwise walk back into it bleeding.
        self._use_provisions_between_combats()

        # Eating is the same between-rooms moment, and lands AFTER the hunger
        # check so a meal tops up whatever the check left -- and after a
        # starvation, which is when the party most needs it. Not on a retreat:
        # `completed=False` means the party is standing in the corridor it fled
        # into, and neither recovery has happened.
        if completed:
            self._deliberate_eat()

        # Camping runs BEFORE the snapshot so its recovery lands in carry_over.
        # `_combat_idx` still names the combat that just finished here.
        self._camp_buffs_by_slot: dict[int, list[str]] = {}
        if completed and self._combat_idx in self._camp_after:
            self._make_camp()

        still_alive: list[int] = []
        carry: dict[int, CarryOverHeroState] = {}
        for k, orig_idx in enumerate(self._alive_indices):
            h = self.engine.heroes[k]
            if h.is_dead:
                continue
            hp = h.hp
            if fed and hp > 0:
                hp = min(h.max_hp, hp + round(h.max_hp * HUNGER_HEAL_FRACTION))
            elif starved and hp > 0:
                # Never lethal: starvation in the real game does not kill
                # outright, and a death here would arrive between combats where
                # nothing can respond to it.
                hp = max(1, hp - round(h.max_hp * HUNGER_STARVE_FRACTION))
            still_alive.append(orig_idx)
            carry[orig_idx] = CarryOverHeroState(
                hp=hp, stress=h.stress, is_deaths_door=h.is_deaths_door,
                mortality_tier=h.mortality_tier,
                camp_buff_ids=list(self._camp_buffs_by_slot.get(k, [])),
                # An affliction lasts the rest of the raid, not the rest of the
                # fight — see CarryOverHeroState for the camping-skill evidence.
                affliction_id=h.affliction_id, virtue_id=h.virtue_id,
                # ...and so does a bleed. `rules.json`'s `dotHallwayStepRatio`
                # is a rate for ticking a DoT in a HALLWAY, which is only a
                # meaningful number if the DoT gets there. Snapshotted AFTER
                # the provisions above, so a bandage actually prevents
                # something.
                bleeds=[replace(d) for d in h.bleeds],
                blights=[replace(d) for d in h.blights],
                stress_dots=[replace(d) for d in h.stress_dots],
                burn_stacks=h.burn_stacks,
            )
        # AND THEN THE PARTY WALKS. Gated on `completed` for the same reason
        # the meal and the campfire are: `completed=False` is a retreat, and a
        # fled party is standing in the corridor it came FROM. It has not
        # walked a new one.
        if completed:
            self._walk_the_corridor(carry)

        self._alive_indices = still_alive
        self._carry_over_by_index = carry

    def _walk_the_corridor(self, carry: dict[int, CarryOverHeroState]) -> None:
        """Spend `CORRIDOR_TILES` worth of DoT ticks on the way to the next room.

        `rules.json` ships `dotHallwayStepRatio = 0.5` and nothing read it, so
        a bleed crossed the room boundary and then sat there costing nothing
        until the next fight began. That is the whole reason the key exists: a
        per-hallway-tile rate is only a meaningful number if a DoT reaches the
        hallway.

        Measured under random play, 18.4% of combat boundaries have a hero
        holding a DoT, so this is not a rare case -- and it lands exactly where
        the bandage and the antivenom are already used, one block above. That
        is the coherence check on the whole mechanic: the corridor is where you
        treat a bleed, and it is now also where an untreated one bites.

        BLEED AND BLIGHT ONLY, and the two omissions are deliberate:

        * BURN is a decaying stack rather than a tick budget -- `_tick_burn`'s
          own comment says there is nothing to decrement -- and it ticks at the
          end of a TURN, which a corridor does not have. Same argument that
          expires buffs by rounds and not by tiles.
        * HORROR (`stress_dots`) is left because stress crossing 100 owes a
          Resolve Check, and `_apply_stress` is what fires one. Ticking it here
          would apply the stress and skip the check, which is a worse error
          than not ticking it. `ASSUMPTIONS.md` carries it with its falsifier.

        NOBODY DIES IN A CORRIDOR. A hero emptied here arrives at the next
        fight on DEATH'S DOOR at 0 HP, which is what `_handle_deaths_door`
        does for a hero not already on it -- but the deathblow roll a hero
        already on the door would face is NOT taken. Two reasons, and the
        second is the one that decides it: a draw between combats moves the
        RNG stream, and a death arriving where nothing can respond to it is
        the case the starvation clamp above already refuses in Fran's own
        words. So the consequence is carried into the next fight, where the
        engine can resolve it properly, instead of being invented here.
        """
        ticks = hallway_dot_ticks(CORRIDOR_TILES,
                                  float(self.store.rules.dot_hallway_step_ratio))
        if ticks <= 0:
            return
        for state in carry.values():
            dealt = (_spend_dot_ticks(state.bleeds, ticks)
                     + _spend_dot_ticks(state.blights, ticks))
            if dealt <= 0:
                continue
            self._corridor_dot_damage += dealt
            state.hp -= dealt
            if state.hp <= 0:
                # `_apply_carry_over` restores Death's Door only when hp <= 0,
                # so both halves are needed for it to survive the boundary.
                state.hp = 0
                if not state.is_deaths_door:
                    self._corridor_downed += 1
                state.is_deaths_door = True

    def _draw_encounter(self, combat_idx: int):
        """The encounter for one combat: the region's boss if this is the last
        fight of a boss quest, otherwise a normal corridor draw.

        The boss sits at the END because that is where the game puts it -- a
        static boss spawns in the room furthest from the entrance, so the party
        arrives having already spent the dungeon's attrition on it. Putting it
        anywhere else would make it a different fight entirely.
        """
        if (self._is_boss_quest
                and combat_idx >= self._num_combats - 1):
            boss = self._sample_boss(self._zone_id, self._tier)
            if boss is not None:
                return boss
        return self._sample_enemy(self._zone_id, self._tier)

    def _start_next_combat(self, entry=None, torch=None):
        """`entry`/`torch` given = a RE-TAKE of the fight the party just fled,
        not the next room. The encounter is a fixture of the map, so it has to
        be the same one; drawing again would quietly reroll the monsters the
        party retreated FROM. Torch is carried for the same reason and because
        travel does not burn torch in this simulator at all."""
        hero_ids = [self._original_hero_ids[i] for i in self._alive_indices]
        hero_levels = [self._original_hero_levels[i] for i in self._alive_indices]
        hero_skill_ids = (
            [self._original_hero_skill_ids[i] for i in self._alive_indices]
            if self._original_hero_skill_ids is not None else None
        )
        carry_over = [self._carry_over_by_index[i] for i in self._alive_indices]
        if entry is None:
            entry = (self._scripted_enemy(self._combat_idx) if self._enemy_script
                     else self._draw_encounter(self._combat_idx))
        if torch is None:
            torch = (self._scripted_torch if self._scripted_torch is not None
                     else self._sample_torch_level())
        self._current_entry = entry
        self._current_torch = torch

        hero_defs = [self.store.heroes[hid] for hid in hero_ids]
        enemy_defs = [self.store.enemies[eid] for eid in entry.enemy_ids]
        self._pending_hp_targets = self._hp_targets(carry_over)
        try:
            self.engine.reset(hero_defs, enemy_defs, torch_level=torch,
                              hero_levels=hero_levels,
                              hero_skill_ids=hero_skill_ids,
                              provisions=self._provision_inventory,
                              carry_over=carry_over,
                              # The region, so `in_dungeon`-gated quirks fire
                              # only where they belong. Before 2026-08-27 the
                              # engine had no zone and granted all of them
                              # everywhere, which made a Cove Scrounger free
                              # stats in the Weald -- and a comp search will
                              # always take free stats.
                              zone_id=self._zone_id or "",
                              # **The underleveled-hero penalty**, from the
                              # tier this expedition sampled. Charged once per
                              # COMBAT, which is what the engine does with it
                              # -- the entry stress is applied at `reset` and
                              # `carry_over` then carries the total forward, so
                              # a hero does not pay it fresh every fight. See
                              # `TIER_TO_QUEST_DIFFICULTY`.
                              quest_difficulty_level=self._quest_difficulty_level,
                              on_board_ready=self._on_board_ready)
        finally:
            self._pending_hp_targets = None
        # Discounted recovery turns are budgeted PER COMBAT, so each internal
        # combat of an expedition gets its own allowance. So is the step budget.
        self._recovery_turns_used = 0
        self._combat_step_count = 0
        self._rebuild_action_table()

    # ── Reward (expedition-level, layered on top of the existing per-combat
    # potential-based shaping from DarkestDungeonEnv.step()) ──

    def _mean_roster_stress_frac(self) -> float:
        """Mean stress fraction over the ORIGINAL roster (not just the
        living), counting any hero no longer in play — dead in a prior
        combat (absent from self.engine.heroes) or is_dead in the current
        one — as max stress (frac 1.0). Averaging over living heroes only
        (the old behavior) made the expedition potential rise whenever a
        stressed hero died, since their high stress term simply dropped
        out of the mean — a real, previously-unnoticed gradient rewarding
        killing off stressed heroes. Death's contribution (1.0) is always
        >= any living stress frac (min(stress,200)/200 <= 1.0), so a death
        can never increase this mean, closing that gradient. Also used
        directly (un-negated) for the real, non-potential terminal stress
        penalty in step() — factored out here since both need the same
        roster-wide accounting."""
        n_total = len(self._original_hero_ids)
        if n_total == 0:
            return 0.0
        n_dead_prior = n_total - len(self.engine.heroes)
        total = float(n_dead_prior)
        for h in self.engine.heroes:
            total += 1.0 if h.is_dead else min(h.stress, 200) / 200.0
        return total / n_total

    def _is_episode_terminal(self) -> bool:
        """A combat ending here is usually an internal transition, not the end
        of the episode -- so the per-combat potential must NOT be zeroed at it.

        The base class pins `phi_after` to 0.0 whenever `engine.done`, which is
        correct for a single combat and wrong for an expedition: `engine.done`
        fires 8-18 times per episode and `step` un-terminates all but the last.
        See `DarkestDungeonEnv._is_episode_terminal` for what that cost.

        The three conditions mirror `step`'s own transition block exactly, and
        have to stay in sync with it:

        * a party WIPE ends the episode wherever it happens;
        * the LAST combat ends it -- but only if it was actually resolved. A
          retreat from the last combat does NOT end the expedition, because a
          fled room stays on the map and the party re-takes it (the same rule
          that stops a retreat being a paid skip), so `step` sets
          `terminated = False` and restarts the encounter;
        * anything else is an internal transition and the episode continues.

        Deliberately NOT included: step-budget truncation. It is decided after
        `super().step()` returns, from `_step_count` this method cannot see the
        final value of, and a truncated episode is a stall rather than an
        outcome. The expedition-level potential zeroes on truncation because it
        is computed in `step` where the flag exists; this one cannot, and the
        difference is worth ~2% of episodes.
        """
        if not self.engine.done:
            return False
        if not any(h.is_alive() for h in self.engine.heroes):
            return True
        if self.engine.winner == "retreat":
            return False
        return self._combat_idx >= self._num_combats - 1

    def _compute_expedition_potential(self) -> float:
        """-_mean_roster_stress_frac(). A second, independent potential-
        based term (same SHAPING_GAMMA as the per-combat Φ) — summing two
        valid potential-based terms is itself a valid potential-based term
        (Ng/Harada/Russell 1999 composes linearly), so adding this
        alongside the existing per-combat Φ preserves its policy-
        invariance guarantee."""
        return -self._mean_roster_stress_frac()

    def step(self, action: int):
        if int(action) == ACTION_ABANDON and self._can_abandon():
            return self._abandon()
        dead_before = {h.id for h in self.engine.heroes if h.is_dead}
        phi_exp_before = self._compute_expedition_potential()

        obs, reward, terminated, truncated, info = super().step(action)
        self._step_count += 1
        self._combat_step_count += 1
        # Read BEFORE the combat-transition block below resets it: a fight that
        # ran out its own budget must end the episode, not roll into the next
        # combat with the counter cleared.
        combat_ran_over = self._combat_step_count >= MAX_STEPS_PER_COMBAT and not terminated

        # Flat, one-time terminal-style penalty per actual hero death (not
        # Death's Door) — same treatment as REWARD_WIN/REWARD_LOSE, not
        # folded into Φ (keeps Φ smooth/interpretable; a -3.0 cliff baked
        # into potential would create a large one-step Φ delta exactly at
        # the death step).
        dead_after = {h.id for h in self.engine.heroes if h.is_dead}
        newly_dead = dead_after - dead_before
        if newly_dead:
            reward += REWARD_HERO_DEATH * len(newly_dead)

        party_wiped = not any(h.is_alive() for h in self.engine.heroes)
        last_combat = self._combat_idx >= self._num_combats - 1
        # A RETREAT DOES NOT CONSUME THE ENCOUNTER. Fran, 2026-08-21: "a
        # retreat doesn't really mean that you skip the combat, as the combat is
        # still on the map". The wiki's Retreating page agrees -- the party is
        # left in the hallway it entered from, and a wandering boss "will
        # reappear if heroes re-enter the same hallway where they retreated".
        #
        # This used to advance `_combat_idx`, which made a retreat a PAID SKIP:
        # 20 stress and the forgone +8 bought the party out of the fight
        # entirely. Two things were wrong with that, and the second is worse
        # than the first. A fled room stays on the map, so the fight is still
        # owed. And because a retreat sets `terminated`, retreating from the
        # LAST combat fell straight through to `terminated and not party_wiped`
        # and collected REWARD_EXPEDITION_COMPLETE -- the full completion bonus
        # for walking out of the final fight.
        #
        # So the party faces the same room again, having paid the stress. Its
        # real alternatives -- abandon the quest, or re-route to a different
        # room -- are DUNGEON-layer decisions and this env has no map, the same
        # reason traversal and curios are out of scope. What is modelled is the
        # third: re-take it. Which is the one Fran singles out as the reason to
        # flee in the first place, "specially after being surprised", because a
        # re-take undoes the scramble and the lost opening round.
        #
        # The price of leaving is otherwise unchanged and still nothing
        # invented: `_reward_retreat` (0.0) instead of `self._reward_win` (+8),
        # plus `combat_retreat_stress` per living hero through the existing
        # dense term.
        if self.engine.winner == "retreat":
            self._combats_retreated += 1
            if terminated and not party_wiped:
                self._snapshot_and_advance_roster(completed=False)
                self._start_next_combat(entry=self._current_entry,
                                        torch=self._current_torch)
                terminated = False

        if terminated and not party_wiped and not last_combat:
            self._snapshot_and_advance_roster()
            self._combat_idx += 1
            self._start_next_combat()
            terminated = False
        elif terminated and (party_wiped or last_combat):
            terminated = True

        # Two budgets, both real: the expedition total, and this combat's own
        # share of it. The second is what stops one runaway fight eating the
        # whole pool — see MAX_STEPS_PER_COMBAT.
        if self._step_count >= self._max_steps or combat_ran_over:
            truncated = True

        # THE AFFLICTION CHARGE. Swept over the roster rather than diffed
        # around `super().step()`, because an affliction can also land in the
        # enemy turns `reset()` auto-resolves at the top of the NEXT combat --
        # a diff taken before the transition block above would never see those,
        # and they would then be inside `afflicted_before` on the following
        # step and never charged at all.
        #
        # `_alive_indices` is what makes the ledger stable: it maps this
        # combat's hero slot to the ORIGINAL roster index, which survives a
        # party re-pack after a death. A hero id would not.
        if self.affliction_penalty:
            for slot, hero in enumerate(self.engine.heroes):
                if not hero.is_afflicted or slot >= len(self._alive_indices):
                    continue
                idx = self._alive_indices[slot]
                if idx not in self._afflicted_charged:
                    self._afflicted_charged.add(idx)
                    reward -= self.affliction_penalty

        # Terminal potential forced to 0 only at the TRUE expedition end
        # (party wipe / last combat done), not at every internal combat
        # transition — mirrors how the per-combat Φ already forces its own
        # terminal to 0, but at the expedition's own terminal step, not
        # each individual combat's.
        phi_exp_after = 0.0 if (terminated or truncated) else self._compute_expedition_potential()
        reward += W_EXPEDITION_STRESS * (SHAPING_GAMMA * phi_exp_after - phi_exp_before)

        # Real (non-potential-based) terminal terms — these are the ones
        # that actually encode the objective; unlike Φ_expedition they
        # don't telescope to ~0 over the episode. REWARD_EXPEDITION_COMPLETE
        # only at the true end (terminated, not just truncated) and only
        # without a party wipe — `terminated and not party_wiped` at this
        # point in the method can only mean the last combat was won (see
        # CombatEngine._check_win_loss: winner=="enemies" iff no heroes
        # alive), never an internal combat transition (those reset
        # `terminated` to False above).
        if terminated and not party_wiped:
            reward += REWARD_EXPEDITION_COMPLETE
        if truncated and not terminated:
            # Stalling out the clock is a failure, not a survival. Without
            # this it strictly dominates finishing a risky expedition:
            # no deaths, no lose-reward, and the party ends alive.
            reward += REWARD_TRUNCATION
        if terminated or truncated:
            reward -= (self.stress_weight * TERMINAL_STRESS_PENALTY_WEIGHT
                   * self._mean_roster_stress_frac())

        return self._get_obs(), reward, terminated, truncated, info

    # ── Observation ──

    def _get_obs(self) -> np.ndarray:
        base = super()._get_obs()
        combats_remaining = self._num_combats - self._combat_idx
        combats_remaining_frac = combats_remaining / max(self._num_combats, 1)
        # The fraction alone cannot express *how much* is left: halfway through
        # a 4-combat run (2 to go) and halfway through a 16-combat one (8 to go)
        # both read 0.5, while the right resource pacing is opposite. That was
        # survivable when every band sat between 8 and 18 combats, but
        # EXPEDITION_LENGTH_BANDS now spans 3..16 with a gap at 11-13, so the
        # same fraction maps to wildly different remaining workloads. Measured
        # consequence: the policy learned one sprint pace, cutting short-band
        # stress (e.g. 81.6 -> 36.1) while long-band stress exploded (105 ->
        # 176) and long-band SUCCESS fell 19.8pp. See CHANGELOG.md.
        combats_remaining_abs = combats_remaining / MAX_EXPECTED_COMBATS
        alive_frac = sum(1 for h in self.engine.heroes if h.is_alive()) / max(len(self._original_hero_ids), 1)
        living = [h for h in self.engine.heroes if h.is_alive()]
        mean_stress_frac = (
            sum(min(h.stress, 200) / 200.0 for h in living) / len(living) if living else 0.0
        )
        extra = [combats_remaining_frac, combats_remaining_abs, alive_frac, mean_stress_frac]
        for pid in ("bandage", "antivenom", "holy_water", "laudanum"):
            cap = max(self.store.provisions[pid].stack_limit, 1) if pid in self.store.provisions else 1
            extra.append(self._provision_inventory.get(pid, 0) / cap)
        return np.concatenate([base, np.array(extra, dtype=np.float32)]).astype(np.float32)
