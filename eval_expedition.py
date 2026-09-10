"""Evaluate a trained PPO model across difficulty bands (tier 1-5, mixing
zones per tier) x several real hero comps, many episodes per combination.
Hero Resolve Level is paired to each band (Apprentice/Veteran/Champion ->
resolve 2/4/6, see env/expedition_env.py) so every matchup is a fair fight
at that band's power level, not max-gear heroes against the full unpaired
difficulty range.

Usage:
    python eval_expedition.py --model models/dd_combat_ppo_expedition.zip --episodes 100
"""

import argparse
import numpy as np
import random
import statistics
import zlib
from dataclasses import dataclass
from gymnasium.wrappers import TimeLimit
# sb3_contrib is imported LAZILY, inside the four functions that touch a model.
# It drags torch in, and torch is ~465 MB of resident memory per process. Three
# test files import this module for `_run_quality` / `pick_representative_comps`
# alone, and under xdist each such file lands on its own worker -- so a
# module-level import here multiplied 465 MB by the worker count and was a
# measured part of the 7.45 GB the suite peaked at. See pytest.ini.

from env import DarkestDungeonEnv, ExpeditionSequenceEnv, mask_fn
from env.expedition_env import (CSV_ZONE_TO_ZONE_ID, TIER_TO_HERO_LEVEL,
                                TIER_TO_QUEST_DIFFICULTY, build_comp_pool)
from env.expedition_sequence_env import EXPEDITION_LENGTH_BANDS
from data.composition_loader import CompositionLoader

ENEMY_CATEGORIES = {"hall", "room", "boss", "stall"}
MAX_EPISODE_STEPS = 150


def pick_representative_comps(store, n: int, seed: int):
    """`n` comps drawn from the SAME pool `ExpeditionEnv` trains on.

    Delegates to `build_comp_pool` rather than keeping its own copy: this
    function used to mirror `_build_hero_pool` by hand, and a hand-synced
    mirror is how the two drift.

    NOTE that every `--comp-seed` archived before 2026-08-23 selected from the
    retired CSV and cannot be reproduced. That comparability was already gone
    -- the comp folder was re-exported and `--comp-seed 1` resolved 1 of its
    12 names to a real loadout -- so this does not lose anything that still
    worked, but an eval quoted against an older log is comparing two different
    comp sets and must say so.
    """
    pool = build_comp_pool(store)
    rng = random.Random(seed)
    return rng.sample(pool, min(n, len(pool)))


def all_comps(store):
    """The WHOLE pool, in a stable sorted order — the `--all-comps` eval.

    Why this exists rather than `--comps 152`. Comp-to-comp spread is the
    dominant error term in this eval and it dwarfs episode noise: measured
    2026-08-23, the long band has an SD of **16.2 points** across comps
    (individual comps run 30% to 88%), so a 12-comp sample buys only +-4.7pp,
    a 95% interval of [47.7, 66.0]. Reaching +-2pp by sampling harder would
    need 252 comps, and only ~152 exist. Evaluating the whole pool removes the
    term outright: 152 comps x 3 bands x 4 episodes is the same 1,824 episodes
    as 12 x 3 x 50, with no comp-sampling error left in it.

    Passing a big `--comps` does NOT do this, for two reasons:

    * the shared `sampler_rng` is consumed SEQUENTIALLY across the comp x band
      x episode nest, so changing the comp count changes the tier and length
      drawn for every later cell. This is the one that matters, and it is why
      simply widening the sample is not enough. `--all-comps` gives each cell
      its own seeded draw instead (see `_cell_scenario`), which also makes a
      single cell reproducible on its own and lets the pool grow without
      moving anything.
    * the per-cell detail block is one line per (comp, band) — 456 lines at
      full pool — and `tools/summarize_eval.py` regexes `succ NN.N%` out of
      every line, so it would silently report `cells=456` against a `cells=36`
      archived log.

    A third reason was claimed, then called FALSE, and is now true again —
    which is the useful part. `random.sample` takes a different branch at
    `k=n` than at `k=12` (selection-set versus partial-shuffle, on CPython's
    `setsize` heuristic). At 166 comps the two agreed on their first 12 picks,
    so the branch was recorded here as harmless. At **169** they diverge at
    index 8. Nothing changed but the population size.

    So the honest statement is that prefix agreement is a COINCIDENCE OF
    POPULATION SIZE and never was a property. A big `--comps` is still a
    superset — that part holds and `tests/training/test_all_comps_eval.py` checks it —
    but its first 12 are not the same 12 a `--comps 12` run would pick, so the
    two are not nested and their results are not comparable cell by cell.
    Which is a third reason to use `--all-comps` rather than a big `--comps`,
    on top of the sequential RNG and the summarizer's line count.
    """
    return sorted(build_comp_pool(store), key=lambda c: c[0])


def _cell_scenario(comp_name: str, length_band: str, ep: int, lo: int, hi: int):
    """`(tier, num_combats, seed)` for one episode, from the cell's own hash.

    Independent of every other cell, which is what lets the comp list grow or
    shrink without moving the scenarios of the cells around it. The shared
    sequential `sampler_rng` cannot offer that.
    """
    h = zlib.crc32(f"{comp_name}|{length_band}|{ep}".encode())
    rng = random.Random(h)
    return rng.randint(1, 5), rng.randint(lo, hi), h % (2**31)


def resolve_skill_ids(store, comp_name, hero_ids):
    """Real 4-of-~7 skill loadout for this comp, if one was scraped for it
    (COMPS/curated_comps/*.json via store.comp_loadouts) — mirrors
    env/expedition_env.py's ExpeditionEnv._resolve_skill_ids, including
    its POSITIONAL (not hero_id-keyed dict) alignment, since a hero_id-
    keyed lookup silently collapses a duplicate-class comp (e.g. 3
    distinct Crusaders) down to whichever one was inserted last. Falls
    back to None (full movepool) when no match exists by name.

    Abomination is always left unrestricted ([]) regardless of what the
    JSON says — unlike every other hero, he always has all 7 combat
    skills equipped in the real game, no 4-skill loadout choice exists for
    him. The scraped JSON still lists 4 "activeSkills" for him like any
    other hero (no Abomination carve-out in the source tool), which would
    otherwise strip 3 of his real skills — see ExpeditionEnv.
    _resolve_skill_ids's docstring for the confirmed concrete case."""
    loadout = store.comp_loadout_by_name(comp_name)
    if not loadout:
        return None
    heroes = [h for h in loadout.heroes if h.hero_id in store.heroes]
    if [h.hero_id for h in heroes] != hero_ids:
        return None
    resolved = []
    for h in heroes:
        if h.hero_id == "abomination":
            resolved.append([])
        elif not h.skill_ids:
            return None
        else:
            resolved.append(h.skill_ids)
    return resolved


def resolve_trinkets_and_quirks(store, comp_name, hero_ids):
    """Real 2 trinkets + quirks per hero, same positional-alignment
    convention as resolve_skill_ids — mirrors ExpeditionEnv.
    _resolve_trinkets_and_quirks."""
    loadout = store.comp_loadout_by_name(comp_name)
    if not loadout:
        return None
    heroes = [h for h in loadout.heroes if h.hero_id in store.heroes]
    if [h.hero_id for h in heroes] != hero_ids:
        return None
    return [(h.trinket_ids, h.quirk_ids.get("positive", []) + h.quirk_ids.get("negative", []))
            for h in heroes]


def build_band_pools(store):
    """tier (1-5) -> list of EnemyCompEntry pooled across the 4 core zones
    (darkestdungeon excluded — it has no tiers 1-5, only scripted tier-6
    quest content)."""
    bands = {t: [] for t in range(1, 6)}
    for e in store.enemy_comps:
        if e.zone_id not in CSV_ZONE_TO_ZONE_ID.values():
            continue
        if e.category not in ENEMY_CATEGORIES:
            continue
        if e.tier in bands:
            bands[e.tier].append(e)
    return bands


def run_episode(env, model, hero_ids, enemy_ids, hero_levels, hero_skill_ids, seed,
                 trinkets_and_quirks=None):
    from sb3_contrib.common.maskable.utils import get_action_masks
    options = {"hero_ids": hero_ids, "enemy_ids": enemy_ids, "hero_levels": hero_levels}
    if hero_skill_ids is not None:
        options["hero_skill_ids"] = hero_skill_ids
    obs, _ = env.reset(seed=seed, options=options)
    if trinkets_and_quirks:
        for hero_combatant, (trinket_ids, quirk_ids) in zip(env.unwrapped.engine.heroes, trinkets_and_quirks):
            if trinket_ids or quirk_ids:
                env.unwrapped.engine.apply_trinkets_and_quirks(hero_combatant, trinket_ids, quirk_ids)
    total_reward = 0.0
    steps = 0
    terminated = truncated = False
    info = {}
    act = deterministic_actor(model)
    while not (terminated or truncated):
        action = act(obs, get_action_masks(env))
        obs, reward, terminated, truncated, info = env.step(action)
        total_reward += reward
        steps += 1
    winner = info.get("winner")
    timed_out = truncated and winner is None
    return winner, total_reward, steps, timed_out


# ── Deterministic action selection, without SB3's per-call wrapper ──────────

def deterministic_actor(model):
    """`lambda obs, masks: int(action)`, equivalent to

        model.predict(obs, deterministic=True, action_masks=masks)[0]

    and **3.2x cheaper** -- 103 us against 331 us, measured 2026-08-27 on the
    shipped checkpoint. The network is a 1636->256->256->38 MLP whose forward
    pass is ~30 us; the rest is `predict`'s per-call scaffolding (obs reshaping,
    `is_vectorized_observation`, the tensor round trip, building a
    `MaskableCategorical`). At 470 us of env step, that scaffolding was a third
    of the whole rollout loop -- and the rollout loop IS the cost of
    `tools/comp_ga.py`, `tools/comp_leaderboard.py` and every eval here, none of
    which does any SGD to hide behind.

    Equivalence is exact, not approximate, and rests on one fact:
    `MaskableCategorical` masks by `where(mask, logits, -1e8)` and
    `deterministic=True` takes `probs.argmax(-1)`. Softmax is monotone, so that
    is `argmax` over the masked logits, with the same first-index tie-break.
    `tests/test_fast_predict.py` checks the two agree on every decision of
    several real expeditions rather than trusting the argument.

    **It refuses rather than guesses.** Anything that is not a plain
    `MaskableActorCriticPolicy` -- `PlaybookPolicy`, a recurrent policy, an
    observation space that needs real preprocessing -- falls back to
    `model.predict`, so a caller never has to ask which kind of arm it holds.
    """
    fast = getattr(model, "_ddsim_fast_actor", None)
    if fast is not None:
        return fast

    def fallback(obs, masks):
        return int(model.predict(obs, deterministic=True, action_masks=masks)[0])

    try:
        import torch
        from gymnasium import spaces
        from sb3_contrib.common.maskable.policies import MaskableActorCriticPolicy
    except Exception:
        return fallback

    policy = getattr(model, "policy", None)
    if not isinstance(policy, MaskableActorCriticPolicy):
        return fallback
    # A Box observation of float32 is the only case `preprocess_obs` leaves
    # alone; anything else (images to normalise, Discrete to one-hot) means the
    # shortcut would be reimplementing preprocessing, which is how these
    # things go wrong silently.
    obs_space = getattr(policy, "observation_space", None)
    if not isinstance(obs_space, spaces.Box) or obs_space.dtype != np.float32:
        return fallback

    policy.set_training_mode(False)
    shared = getattr(policy, "share_features_extractor", True)
    action_net = policy.action_net
    # An equivariant head scores each action from that action's own payload, so
    # it needs the observation as well as the latent. Detected by a marker on
    # the head rather than by importing it -- `env/equivariant_policy` imports
    # torch at module level, and this file is one of the four pinned lazy-import
    # sites (`tests/suite/test_suite_memory.py`).
    head_needs_obs = bool(getattr(action_net, "NEEDS_OBS", False))
    mlp = policy.mlp_extractor
    extract = policy.extract_features
    device = policy.device
    HUGE_NEG = -1e8

    def _as_tensor(arr, dtype):
        """`from_numpy` shares the buffer; `as_tensor` on a mismatched dtype
        copies. The env already hands back float32 and bool, so the copy is
        avoidable on the hot path and is kept only as the fallback."""
        if isinstance(arr, np.ndarray) and arr.dtype == dtype:
            return torch.from_numpy(arr)
        return torch.as_tensor(np.asarray(arr, dtype=dtype))

    @torch.no_grad()
    def fast_actor(obs, masks):
        obs_t = _as_tensor(obs, np.float32).to(device).unsqueeze(0)
        feats = extract(obs_t)
        latent_pi = mlp.forward_actor(feats if shared else feats[0])
        logits = (action_net(latent_pi, obs_t) if head_needs_obs
                  else action_net(latent_pi))
        mask_t = _as_tensor(masks, np.bool_).to(device).reshape(logits.shape)
        return int(torch.where(mask_t, logits, torch.full_like(logits, HUGE_NEG))
                   .argmax(dim=-1).item())

    try:
        model._ddsim_fast_actor = fast_actor
    except AttributeError:                     # a frozen/slotted stand-in
        pass
    return fast_actor


@dataclass
class ExpeditionOutcome:
    """One full-expedition episode's result, split into the three genuinely
    distinct end states this eval used to conflate.

    The old metric was a single `zero_death = len(living) == len(hero_ids)`
    evaluated at episode end **regardless of why it ended**, paired with a
    `timed_out = truncated and not living` that only counted truncations
    where the whole party had ALSO died. That combination silently scored
    the most common failure mode — the policy dithering until the step
    budget ran out, party alive but the expedition barely started — as a
    zero-death success with no timeout flagged. Confirmed against real eval
    data: "Stun Control: Beast" short band reported 28% zero-death while
    reaching only 3.0 of a sampled 8-11 combats; for those 28% to be real
    completions the other 72% would have to wipe inside half a combat,
    which is implausible, so they were overwhelmingly stall-outs.

    `success` (completed the whole expedition AND nobody died) is the real
    headline number and the one that maps to the project's stated goal.
    `zero_death` is kept as a secondary column purely for continuity with
    earlier runs — it is NOT a success rate on its own.
    """
    success: bool          # completed every combat AND zero deaths
    abandoned: bool        # the policy chose ACTION_ABANDON and walked home
    completed: bool        # reached the end of the last combat (not wiped, not truncated)
    zero_death: bool       # nobody died — however the episode ended
    wiped: bool            # whole party dead
    truncated_alive: bool  # ran out of step budget with survivors (stalling)
    final_stress: float
    # The state the party actually came home in, and the two halves of the
    # stated objective -- Fran, 2026-08-24: "complete an expedition with all
    # heroes at 0 stress, without anyone dying".
    #
    # SUCCESS is a gate, not a goal: it is binary, it says nothing about the
    # shape the party is in, and short/medium sit at ~93% so it can no longer
    # resolve an improvement. `quality` is the graded companion.
    final_hp_frac: float = 1.0
    quality: float = 0.0
    # The two halves of `quality`, UNWEIGHTED and each in [0, 1] -- (hp, calm).
    # Carried so a consumer can weight them for itself: `tools/comp_ga.py`
    # ranks on 2:1 stress-over-HP (Fran, 2026-08-26) while the eval headline
    # keeps the declared 50/50, and re-deriving that from `quality` alone is
    # impossible once the two are already mixed.
    quality_parts: tuple = (0.0, 0.0)
    combats_reached: int = 0
    num_combats: int = 0
    reward: float = 0.0
    steps: int = 0
    # How many heroes were actually lost. `zero_death` only says "any", and a
    # party that comes back one hero down is a very different outcome from one
    # that comes back three down — which is the distinction the embark and
    # retreat advisors have to rank on.
    deaths: int = 0
    # Combats the party broke off rather than cleared. SUCCESS still means
    # "completed every combat AND zero deaths", so a retreated combat is not a
    # completion — but it is not a wipe either, and lumping the two together
    # would hide the whole point of giving the policy the option.
    retreats: int = 0
    # SUCCESS under the PRE-RETREAT definition — reached the end alive, however
    # many fights were skipped. Meaningless as a headline (fleeing everything
    # would score 100%) and essential as a control: it is the only column that
    # is measured the same way an archived eval measured it, so it separates
    # "the policy got better" from "the bar moved".
    legacy_success: bool = False
    # --- instrumentation for the large-monster targeting fix ---
    #
    # `saw_large_monster` was the first attempt and it does not discriminate:
    # measured over a real eval, 11 of 12 expeditions met a size-2+ monster at
    # some point, which is unsurprising once an expedition runs 3-16 combats.
    # A 92/8 split leaves the control column with no data.
    #
    # So the dose is measured instead — what FRACTION of this expedition's
    # combats fielded one — and episodes are compared across a median split of
    # it. If the fix helps, the high-dose half has to improve more than the
    # low-dose half.
    large_combats: int = 0
    combats_seen: int = 0
    # Direct behavioural evidence, independent of any split: how many of the
    # policy's chosen attacks aimed at a rank that was ILLEGAL before the fix —
    # a large monster's back rank. Zero here means the restored actions are
    # never used, which falsifies the fix mattering regardless of what SUCCESS
    # does.
    back_rank_actions: int = 0

    @property
    def large_combat_share(self) -> float:
        return self.large_combats / self.combats_seen if self.combats_seen else 0.0


def run_full_expedition_episode(env, model, comp_name, hero_ids, zone_id, tier, length_band,
                                 num_combats, seed, hero_skill_ids=None,
                                 extra_options=None) -> ExpeditionOutcome:
    """Runs one full expedition episode deterministically. comp_name is
    passed through so ExpeditionSequenceEnv.reset()'s explicit-options
    branch can resolve trinkets/quirks itself; unlike those, hero_skill_ids
    is NOT auto-resolved from comp_name on that branch
    (options.get("hero_skill_ids") only), so it must be passed in
    explicitly here — this was previously missing entirely, meaning every
    prior full-expedition eval ran with each hero's full ~7-skill movepool
    instead of the real 4-skill loadout actually used during training.

    `extra_options` merges straight into the env's reset options, which is how
    `bridge/rollout.py` runs a REAL party (per-hero resolve levels, weapon and
    armour ranks, its own trinkets/quirks, and a starting carry_over) through
    the same episode loop and the same outcome accounting this eval uses. Kept
    as one pass-through dict rather than another six positional parameters."""
    from sb3_contrib.common.maskable.utils import get_action_masks
    options = {
        "hero_ids": hero_ids, "comp_name": comp_name, "zone_id": zone_id, "tier": tier,
        "length_band": length_band, "num_combats": num_combats,
    }
    if hero_skill_ids is not None:
        options["hero_skill_ids"] = hero_skill_ids
    options.update(extra_options or {})
    obs, _ = env.reset(seed=seed, options=options)
    unwrapped = env.unwrapped
    total_reward = 0.0
    steps = 0
    terminated = truncated = False
    # Sampled every step rather than once per combat: the env swaps its enemy
    # line in place between combats and exposes no per-combat hook, and a
    # monster can also arrive mid-combat as a reinforcement. Keyed by
    # `_combat_idx` so each combat is counted once however many steps it takes.
    large_combats = set()
    combats_seen = set()
    back_rank_actions = 0
    act = deterministic_actor(model)
    while not (terminated or truncated):
        action = act(obs, get_action_masks(env))
        combats_seen.add(unwrapped._combat_idx)
        if any(e.size > 1 for e in unwrapped.engine.enemies if e.is_alive()):
            large_combats.add(unwrapped._combat_idx)
        back_rank_actions += _is_back_rank_attack(unwrapped, action)
        obs, reward, terminated, truncated, info = env.step(action)
        total_reward += reward
        steps += 1
    living = [h for h in unwrapped.engine.heroes if h.is_alive()]
    wiped = not living
    # ExpeditionSequenceEnv.step() only leaves `terminated` True at the TRUE
    # expedition end (party wipe, or the last combat finishing) — internal
    # combat transitions reset it to False — so terminated-and-not-wiped is
    # exactly "played every sampled combat through".
    retreats = getattr(unwrapped, "_combats_retreated", 0)
    # "Reached the end" is not "cleared it". A party that fled three fights and
    # walked out has `terminated` set exactly like one that won them all, so
    # without this the retreat option would inflate SUCCESS by definition
    # rather than by playing better.
    # ABANDONING IS NOT COMPLETING, and it sets `terminated` with the party
    # alive exactly like a clean finish does -- so without this line a policy
    # could score 100% SUCCESS by walking out of every expedition it was
    # allowed to. Same trap `retreats == 0` above exists for, and the reason
    # ACTION_ABANDON could not be added without touching this file.
    abandoned = bool(getattr(unwrapped, "_abandoned", False))
    completed = terminated and not wiped and retreats == 0 and not abandoned
    zero_death = len(living) == len(hero_ids)
    return ExpeditionOutcome(
        success=completed and zero_death,
        abandoned=abandoned,
        legacy_success=(terminated and not wiped) and zero_death,
        completed=completed,
        zero_death=zero_death,
        wiped=wiped,
        truncated_alive=truncated and not wiped,
        final_stress=statistics.mean(min(h.stress, 200) for h in living) if living else 200.0,
        final_hp_frac=(sum(h.hp for h in living)
                       / max(sum(h.max_hp for h in living), 1)) if living else 0.0,
        quality=_run_quality(unwrapped.engine.heroes, len(hero_ids)),
        quality_parts=run_quality_components(unwrapped.engine.heroes,
                                             len(hero_ids)),
        combats_reached=unwrapped._combat_idx + 1,
        num_combats=num_combats,
        reward=total_reward,
        steps=steps,
        large_combats=len(large_combats),
        combats_seen=len(combats_seen),
        back_rank_actions=back_rank_actions,
        deaths=len(hero_ids) - len(living),
        retreats=retreats,
    )


LARGE_SPLIT_PREFIX = "LARGE-MONSTER SPLIT:"


def _is_back_rank_attack(unwrapped, action: int) -> bool:
    """Would this chosen action have been ILLEGAL before the large-monster
    targeting fix — i.e. is it aimed at a rank a size-2+ monster covers but
    does not stand on at the front?

    Counted per step because it is the one measurement that does not depend on
    a split: if the policy never picks such an action, the restored actions are
    unused and the fix cannot be what moved any number."""
    act = unwrapped._action_map.get(action)
    if act is None or act.target_id is None or act.target_id < 0:
        return False
    if act.skill_id.startswith("__"):
        return False
    return any(e.is_alive() and e.size > 1
               and act.target_id in e.occupied_positions
               and act.target_id != e.position
               for e in unwrapped.engine.enemies)


# The QUALITY weighting, `(hp, stress)`, summing to 1.
#
# **50/50 IS THE ARCHIVE'S WEIGHTING, NOT FRAN'S OBJECTIVE**, and the two are
# now known to disagree. His hierarchy, 2026-09-02:
#
#   1. finish without deaths -- "an end game hero means many resources spent,
#      so the value of the party alive and with the resources of the dungeon
#      outvalues the stress of the party";
#   2. low stress -- "stress carries outside the dungeon, making the hero
#      unable of playing dungeons back to back as opposed to HP";
#   3. HP last -- "hp doesnt carry outside the dungeon, the heroes enter full
#      HP, hp matters only to not lose heroes, but **if you outvalue hp to
#      stress its a problem as the afflictions will defeat you in the end**".
#
# Deaths are already the dominant term: a dead hero scores zero for its whole
# share of BOTH halves, which is what puts (1) above everything. What (2) and
# (3) say is that the remaining split should not be even -- and
# `tools/comp_ga.py` already ranks on 2:1 stress-over-HP, a call Fran made for
# the SEARCH on 2026-08-26. So the eval and the search have been using
# different rulers, and his hierarchy says the search is the closer one.
#
# The default stays 50/50 so every archived number remains comparable, and
# **`QUALITY_ALT` is printed beside it on every band line** so the alternative
# costs no rerun. Changing the DEFAULT re-scores every measurement this repo
# has ever taken; that is a new ruler and is Fran's call, not a tidy-up.
QUALITY_WEIGHTS = (0.5, 0.5)
QUALITY_ALT = (1.0 / 3.0, 2.0 / 3.0)


def quality_from_parts(hp_share: float, stress_share: float,
                       weights=None) -> float:
    """Weight the two halves. Split out so nothing has to re-play a run to
    re-score it -- `RolloutResult` carries the parts unweighted for exactly
    this reason."""
    w_hp, w_stress = weights or QUALITY_WEIGHTS
    return 100.0 * (w_hp * hp_share + w_stress * stress_share)


def _run_quality(heroes, roster_size: int, weights=None) -> float:
    """How well the party came home, 0-100, against the stated objective.

    Per hero: 0 if dead, else a weighted split of HP and stress. Averaged over
    the ROSTER, not the survivors, so a dead hero costs its whole share --
    which is what stops a policy improving mean stress by letting the stressed
    hero die. A wipe is 0; full HP at 0 stress is 100.

    THE WEIGHTS ARE A DECLARED CONVENTION -- see `QUALITY_WEIGHTS`, including
    where Fran's stated objective disagrees with them -- so every caller prints
    the three components beside this number. A composite that can hide a
    component moving the wrong way is how a heal metric broke a five-arm
    monotonic climb earlier this month.

    Stress is scored against the 200 ceiling rather than the 100 Resolve Check,
    deliberately: the reward's own `combat_end_condition` clamps at 100 and is
    therefore flat above it, and a RULER must not go blind exactly where the
    runs it is grading end up.
    """
    hp_share, stress_share = run_quality_components(heroes, roster_size)
    return quality_from_parts(hp_share, stress_share, weights)


def run_quality_components(heroes, roster_size: int) -> tuple:
    """The two halves of `_run_quality`, unweighted, each already averaged over
    the ROSTER and in [0, 1].

    Split out so a CALLER can weight them differently without touching the eval
    headline. `tools/comp_ga.py` does: Fran chose a 2:1 stress-over-HP weighting
    for the SEARCH on 2026-08-26, after the first run came home at 38.8 mean
    stress against 19.8 for his own top comps. The eval's QUALITY column keeps
    the declared 50/50 so every archived number stays comparable.

    A dead hero contributes zero to BOTH shares, which is what stops a policy
    improving mean stress by letting the stressed hero die -- and is why the
    denominator is the roster rather than the survivors.
    """
    if not roster_size:
        return 0.0, 0.0
    hp_total = stress_total = 0.0
    for h in heroes:
        if not h.is_alive():
            continue
        hp = h.hp / max(h.max_hp, 1)
        calm = 1.0 - min(h.stress, 200.0) / 200.0
        hp_total += max(0.0, min(1.0, hp))
        stress_total += max(0.0, min(1.0, calm))
    return hp_total / roster_size, stress_total / roster_size


def _large_monster_split_line(outcomes) -> str:
    """SUCCESS across a median split on how much of the expedition was spent
    against size-2+ monsters, plus how often the restored back-rank actions
    were actually chosen.

    The first version of this split on "met a large monster at all" and was
    useless: 11 of 12 expeditions do, because an expedition runs 3-16 combats.
    Dose is the discriminating variable, and a median split balances the two
    groups by construction whatever the distribution turns out to be.

    Read it as: `hi` should improve more than `lo` if the fix is what helped,
    and `backrank` must be non-zero for that story to be possible at all.

    One machine-readable line so `tools/summarize_eval.py` can compare
    checkpoints without re-reading a whole table."""
    if not outcomes:
        return f"{LARGE_SPLIT_PREFIX} no episodes"
    shares = sorted(o.large_combat_share for o in outcomes)
    cut = statistics.median(shares)
    # `> cut` rather than `>=`: with a lopsided distribution the median can BE
    # the modal value, and >= would sweep everything into the high group.
    hi = [o for o in outcomes if o.large_combat_share > cut]
    lo = [o for o in outcomes if o.large_combat_share <= cut]

    def cell(group):
        if not group:
            return "n=0 succ   n/a"
        return f"n={len(group)} succ {sum(o.success for o in group) / len(group) * 100:5.1f}%"

    back = sum(o.back_rank_actions for o in outcomes)
    episodes_using = sum(1 for o in outcomes if o.back_rank_actions)
    return (f"{LARGE_SPLIT_PREFIX} cut={cut:.2f} hi-large {cell(hi)} | lo-large {cell(lo)} | "
            f"backrank {back} actions in {episodes_using}/{len(outcomes)} episodes")


def run_full_expedition_eval(args):
    from sb3_contrib import MaskablePPO
    from sb3_contrib.common.wrappers import ActionMasker
    # A checkpoint trained with --blind-forecast must be EVALUATED blinded.
    # Running it on sighted observations is a distribution mismatch that would
    # understate the control arm and quietly break the attribution the arm
    # exists to produce.
    # `--underleveled` opens TWO gates, and one alone does nothing.
    #
    # `resolve_sampling` is the first: with it off, `TIER_TO_HERO_LEVEL` pins
    # every hero at the TOP of its bracket, so the shortfall against
    # `TIER_TO_QUEST_DIFFICULTY` is zero in every bracket and no penalty can
    # exist to charge. `quest_difficulty_level` in the reset options is the
    # second (see the call site below).
    #
    # Opening only the second is measurably a no-op: an `--all-comps` run with
    # it came back byte-identical to the run without, 96.4/94.7/81.7 on both,
    # which is what sent me looking for this one.
    base_env = ExpeditionSequenceEnv(sampler_seed=None,
                                     blind_forecast=args.blind_forecast,
                                     resolve_sampling=args.underleveled)
    env = ActionMasker(base_env, mask_fn)  # see env.gym_env.mask_fn for why not a lambda
    model = MaskablePPO.load(args.model, env=env)
    store = base_env.store

    if args.all_comps:
        comps = all_comps(store)
    else:
        comps = pick_representative_comps(store, args.comps, args.comp_seed)
    length_bands = list(EXPEDITION_LENGTH_BANDS.keys())

    # The CELL COUNT is printed on its own line and up front, because
    # `tools/summarize_eval.py` reports `cells=N` and comparing a 456-cell run
    # against a 36-cell one is comparing two different rulers.
    n_cells = len(comps) * len(length_bands)
    mode = "ALL COMPS" if args.all_comps else f"sampled, comp-seed {args.comp_seed}"
    if args.boss_quests:
        mode += " | BOSS QUESTS (last combat is the region's boss)"
    print(f"Model: {args.model} | Episodes per cell: {args.episodes}")
    print(f"Comps: {len(comps)} ({mode}) | cells: {n_cells} | "
          f"episodes total: {n_cells * args.episodes}")
    if args.all_comps:
        print("Comp-sampling error is ZERO here: this is the whole trainable pool.")
    else:
        print(f"Comps sampled ({len(comps)}):")
        for name, zone_id, hero_ids in comps:
            print(f"  - {name} ({zone_id}, {len(hero_ids)} heroes: {hero_ids})")
    print()

    header = f"{'Comp':30s} " + " ".join(f"{b:>10s}" for b in length_bands)
    print(header)
    print("-" * len(header))

    results = {}
    all_outcomes = []
    sampler_rng = random.Random(12345)
    for comp_name, zone_id, hero_ids in comps:
        # Real 4-of-~7 skill loadout for this comp, if one was scraped for
        # it — previously missing entirely from this eval path (unlike the
        # single-combat eval below), so every prior full-expedition eval ran
        # with each hero's full movepool instead of the real training
        # restriction. Trinkets/quirks resolve automatically inside
        # ExpeditionSequenceEnv.reset() from the comp_name passed through
        # run_full_expedition_episode's options.
        hero_skill_ids = resolve_skill_ids(store, comp_name, hero_ids)

        row = []
        for length_band in length_bands:
            lo, hi = EXPEDITION_LENGTH_BANDS[length_band]
            outcomes = []
            for ep in range(args.episodes):
                if args.all_comps:
                    tier, num_combats, seed = _cell_scenario(
                        comp_name, length_band, ep, lo, hi)
                else:
                    tier = sampler_rng.randint(1, 5)
                    num_combats = sampler_rng.randint(lo, hi)
                    seed = zlib.crc32(
                        f"{comp_name}|{length_band}|{ep}".encode()) % (2**31)
                outcomes.append(run_full_expedition_episode(
                    env, model, comp_name, hero_ids, zone_id, tier, length_band, num_combats, seed,
                    hero_skill_ids=hero_skill_ids,
                    # `--boss-quests` forces the LAST combat of every episode to
                    # be the region's boss. Not a fraction and not mixed into
                    # the corridor number: a boss run is a different question,
                    # and diluting one measurement with the other would hide
                    # both. `ExpeditionSequenceEnv` already honours an explicit
                    # `boss_quest` in its reset options, which is what makes
                    # this three lines instead of a knob.
                    # THE SCENARIO SEED, not just the engine seed. Without it
                    # `_sampler_rng` is never reseeded and the torch, the
                    # monsters and the encounter draws differ between two runs
                    # of the same command -- measured at 91.8% vs 93.8% on the
                    # short band. Every delta this file printed before
                    # 2026-08-24 carried that error silently.
                    extra_options=dict(
                        **({"boss_quest": True} if args.boss_quests else {}),
                        # THE UNDERLEVELED PENALTY IS OFF BY DEFAULT HERE,
                        # and that is a TRAIN/EVAL MISMATCH rather than a
                        # preference. `ExpeditionSequenceEnv.reset` sets
                        # `_quest_difficulty_level` from `TIER_TO_QUEST_
                        # DIFFICULTY` on the SAMPLED path (training) and to
                        # `options.get(...)` -> None on the EXPLICIT path
                        # (this eval), and `CombatEngine.reset` reads None as
                        # zero shortfall. So a policy trained with the
                        # 2026-09-02 headline fidelity fix is scored without
                        # it: measured on a tier-1 cell, hero levels came out
                        # [1, 0, 2, 0] against difficulty 1 and every hero
                        # still entered on 0 stress instead of +20 for the two
                        # at resolve 0.
                        #
                        # Default OFF keeps every archived number reproducible;
                        # `--underleveled` is how the cost gets measured.
                        **({"quest_difficulty_level":
                            TIER_TO_QUEST_DIFFICULTY.get(tier)}
                           if args.underleveled else {}),
                        sampler_seed=seed),
                ))
            all_outcomes.extend(outcomes)
            n = len(outcomes)
            success_rate = sum(o.success for o in outcomes) / n
            results[(comp_name, length_band)] = {
                "success_rate": success_rate,
                "completion_rate": sum(o.completed for o in outcomes) / n,
                "zero_death_rate": sum(o.zero_death for o in outcomes) / n,
                "wipe_rate": sum(o.wiped for o in outcomes) / n,
                "stall_rate": sum(o.truncated_alive for o in outcomes) / n,
                # The headline measurement of the retreat retrain, and the only
                # column here that is not comparable to an archived eval: the
                # option did not exist before, and `completed` now requires
                # `retreats == 0`. A SUCCESS that moved could be better play or
                # a moved goalpost; the retreat rate and the death count are
                # what read directly.
                "legacy_success_rate": sum(o.legacy_success for o in outcomes) / n,
                "retreat_rate": sum(1 for o in outcomes if o.retreats) / n,
                "mean_retreats": statistics.mean(o.retreats for o in outcomes),
                "mean_deaths": statistics.mean(o.deaths for o in outcomes),
                "mean_final_stress": statistics.mean(o.final_stress for o in outcomes),
                # THE DISTRIBUTION, not just its centre. A band mean of
                # 16.8 is silent about whether one hero came home at 100
                # and one at 0, and `bat/play.log` has a real Shieldbreaker
                # at 180. Carried per episode so a caller can take any
                # quantile without replaying -- the same reason
                # `mean_quality_parts` is carried unweighted.
                "final_stresses": [o.final_stress for o in outcomes],
                "mean_final_hp": statistics.mean(o.final_hp_frac for o in outcomes),
                "mean_quality": statistics.mean(o.quality for o in outcomes),
                # Carried UNWEIGHTED so a caller can re-score without
                # re-playing -- see `QUALITY_WEIGHTS`.
                "mean_quality_parts": (
                    statistics.mean(o.quality_parts[0] for o in outcomes),
                    statistics.mean(o.quality_parts[1] for o in outcomes)),
                "mean_combats_reached": statistics.mean(o.combats_reached for o in outcomes),
                "mean_combats_target": statistics.mean(o.num_combats for o in outcomes),
                "mean_reward": statistics.mean(o.reward for o in outcomes),
                "mean_len": statistics.mean(o.steps for o in outcomes),
            }
            row.append(f"{success_rate*100:5.0f}% ")
        print(f"{comp_name[:30]:30s} " + " ".join(f"{r:>9s}" for r in row))

    print()
    print(_large_monster_split_line(all_outcomes))
    print()
    print("Columns = SUCCESS rate: completed every combat AND zero deaths.")
    print("(This is stricter than the old 'zero_death' column, which also counted")
    print(" episodes that stalled out on the step budget with the party still alive.)")
    print()
    ret = statistics.mean(o.retreats for o in all_outcomes)
    fled = sum(1 for o in all_outcomes if o.retreats) / max(len(all_outcomes), 1)
    print(f"RETREAT: {fled*100:.1f}% of expeditions broke off at least one fight, "
          f"{ret:.2f} combats fled per expedition, "
          f"{statistics.mean(o.deaths for o in all_outcomes):.2f} deaths per expedition.")
    ab = [o for o in all_outcomes if o.abandoned]
    if ab:
        print(f"ABANDON: {len(ab)/max(len(all_outcomes),1)*100:.1f}% of expeditions "
              f"were abandoned, at {statistics.mean(o.combats_reached/max(o.num_combats,1) for o in ab)*100:.0f}% "
              f"of the way through, {statistics.mean(o.deaths for o in ab):.2f} deaths already taken.")
        print(f"  Abandoning is NOT completing -- it is excluded from SUCCESS the same")
        print(f"  way a fled combat is. Read it as the policy declining a run it judged")
        print(f"  lost; a HIGH rate on a strong checkpoint means the exit is too cheap.")
    else:
        print("ABANDON: never used.")
    print()
    legacy = sum(o.legacy_success for o in all_outcomes) / max(len(all_outcomes), 1)
    print(f"  A fled combat is NOT a completion, so SUCCESS is not comparable to an")
    print(f"  archived eval. The control is SUCCESS under the OLD definition (end")
    print(f"  reached alive, fights skipped or not): {legacy*100:.1f}%. That column is")
    print(f"  the one to diff against an archived run, and it is NOT a headline —")
    print(f"  fleeing every fight would score 100% on it.")
    print()
    if args.all_comps and not args.detail:
        print(f"Per-cell detail suppressed for {len(results)} cells "
              f"(pass --detail to print it). Band summary:")
        for band in length_bands:
            cells = [r for (_c, b), r in results.items() if b == band]
            if not cells:
                continue
            n = len(cells)
            mean = sum(c["success_rate"] for c in cells) / n
            sd = statistics.stdev([c["success_rate"] for c in cells]) if n > 1 else 0.0
            se = sd / (n ** 0.5) if n else 0.0
            # CELL sd, not comp-to-comp sd: at a small --episodes each cell's
            # rate is quantised (4 episodes -> {0,25,50,75,100}%), so this
            # carries binomial episode noise on top of the real spread between
            # comps and reads much higher than the comp variance alone. The SE
            # beside it is unaffected -- it is the standard error of the mean
            # over cells, which is what the headline needs, and it correctly
            # absorbs both sources.
            qual = sum(c["mean_quality"] for c in cells) / n
            qsd = statistics.stdev([c["mean_quality"] for c in cells]) if n > 1 else 0.0
            qse = qsd / (n ** 0.5) if n else 0.0
            # The same runs re-scored under Fran's hierarchy, free: the parts
            # are carried unweighted precisely so nothing has to be re-played.
            alt = [quality_from_parts(*c["mean_quality_parts"], QUALITY_ALT)
                   for c in cells if c.get("mean_quality_parts")]
            alt_txt = f" (1:2 {sum(alt) / len(alt):5.1f})" if alt else ""
            print(f"  {band:>6s}: succ {mean*100:5.1f}% +-{se*100:4.2f}   "
                  f"qual {qual:5.1f} +-{qse:4.2f}{alt_txt}   "
                  f"(stress {sum(c['mean_final_stress'] for c in cells)/n:5.1f}  "
                  f"hp {sum(c['mean_final_hp'] for c in cells)/n*100:4.1f}%  "
                  f"deaths {sum(c['mean_deaths'] for c in cells)/n:4.2f})   "
                  f"[cell SD {sd*100:4.1f}pp over {n} comps x {args.episodes} ep]")
            # THE STRESS TAIL, on its own line. Stress is half of QUALITY and
            # the second thing Fran ranks, and a mean hides exactly the runs
            # that matter: a party home at 30 twice reads the same as one home
            # at 0 and one at 60, and only the second lost a hero to a Resolve
            # Check. The count over 100 is the one that names a real event --
            # 100 is where the game rolls for an affliction.
            stresses = sorted(s for c in cells for s in c.get("final_stresses", []))
            if stresses:
                def _q(frac):
                    return stresses[min(len(stresses) - 1, int(len(stresses) * frac))]
                over = sum(1 for s in stresses if s >= 100.0)
                print(f"          stress p50 {_q(0.50):5.1f}  p90 {_q(0.90):5.1f}  "
                      f"max {stresses[-1]:5.1f}   "
                      f"{over}/{len(stresses)} episodes ended a hero over 100 "
                      f"({over / len(stresses):.1%})")
        print()
        print("  SUCCESS is the gate (finished every combat, nobody died). QUALITY is")
        print("  the goal, 0-100 over the roster: half HP, half stress, a dead hero")
        print("  scoring zero for its whole share. The three components are printed")
        print("  beside it because the weights are a convention -- read them, not just")
        print("  the composite.")
        print("  (1:2) re-scores the SAME runs at one third HP, two thirds stress --")
        print("  Fran's hierarchy: stress carries out of the dungeon and HP does not.")
        print("  It costs no rerun because the parts are carried unweighted.")
        return

    print("Detail (success | completed | zero-death | wipe | stall | fled | deaths | stress | combats reached/target | reward):")
    for (comp_name, length_band), r in results.items():
        lo, hi = EXPEDITION_LENGTH_BANDS[length_band]
        print(f"  {comp_name[:30]:30s} {length_band:>6s} ({lo}-{hi}): "
              f"succ {r['success_rate']*100:5.1f}% | old-succ {r['legacy_success_rate']*100:5.1f}% | "
              f"compl {r['completion_rate']*100:5.1f}% | "
              f"0-death {r['zero_death_rate']*100:5.1f}% | wipe {r['wipe_rate']*100:5.1f}% | "
              f"stall {r['stall_rate']*100:5.1f}% | fled {r['retreat_rate']*100:5.1f}% "
              f"({r['mean_retreats']:.2f}) | deaths {r['mean_deaths']:4.2f} | "
              f"stress {r['mean_final_stress']:5.1f} | "
              f"combats {r['mean_combats_reached']:4.1f}/{r['mean_combats_target']:4.1f} | "
              f"reward {r['mean_reward']:+7.2f}")


def main():
    from sb3_contrib import MaskablePPO
    from sb3_contrib.common.wrappers import ActionMasker
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="models/dd_combat_ppo_expedition.zip")
    parser.add_argument("--episodes", type=int, default=100)
    parser.add_argument("--comps", type=int, default=5, help="number of hero comps to sample")
    parser.add_argument("--comp-seed", type=int, default=0)
    parser.add_argument("--blind-forecast", action="store_true",
                        help="Emit the threat forecast as zeros, matching a "
                             "checkpoint trained with train.py --blind-forecast. "
                             "A blinded checkpoint evaluated SIGHTED is a "
                             "distribution mismatch.")
    parser.add_argument("--all-comps", action="store_true",
                        help="evaluate EVERY comp in the trainable pool instead of "
                             "sampling. Removes comp-sampling error, which is the "
                             "dominant term: the long band's comp-to-comp SD is 16.2pp, "
                             "so 12 comps buys only +-4.7pp. Pair with a small "
                             "--episodes; 152 comps x 3 bands x 4 costs the same as "
                             "12 x 3 x 50. Ignores --comps and --comp-seed.")
    parser.add_argument("--boss-quests", action="store_true",
                        help="make EVERY episode a boss quest -- the last combat "
                             "is the region's boss. The corridor eval never "
                             "contains one, so 37 boss encounter rows and the 60 "
                             "monsters reachable only through them (Collector, "
                             "Brigand Cannon, the Hag's cauldron, Necromancer "
                             "forms) have never been measured, nor has the "
                             "life_link / untargetable-cauldron / summon work "
                             "written for them. Report this as its own number, "
                             "never merged with the corridor one.")
    parser.add_argument("--underleveled", action="store_true",
                        help="Evaluate in the world TRAINING actually saw: draw "
                             "each hero's resolve within its bracket AND pass "
                             "quest_difficulty_level, so the underleveled-hero "
                             "penalty can fire. BOTH are needed -- resolve "
                             "sampling off pins every hero at the top of its "
                             "bracket (zero shortfall), and a None difficulty "
                             "makes CombatEngine read zero shortfall anyway. "
                             "Off by default so archived numbers stay "
                             "reproducible.")
    parser.add_argument("--detail", action="store_true",
                        help="print the per-cell detail block even under --all-comps, "
                             "where it is 450+ lines")
    parser.add_argument("--full-expedition", action="store_true",
                         help="evaluate a full-expedition model (env.ExpeditionSequenceEnv) — reports "
                              "zero_death_rate/mean_final_stress/mean_combats_reached per length band "
                              "(short/medium/long) instead of per-combat win-rate per numbered tier")
    args = parser.parse_args()

    if args.full_expedition:
        run_full_expedition_eval(args)
        return

    base_env = DarkestDungeonEnv(render_mode=None)
    env = ActionMasker(TimeLimit(base_env, max_episode_steps=MAX_EPISODE_STEPS), mask_fn)
    model = MaskablePPO.load(args.model, env=env)
    store = base_env.store

    comps = pick_representative_comps(store, args.comps, args.comp_seed)
    bands = build_band_pools(store)

    print(f"Model: {args.model} | Episodes per cell: {args.episodes}")
    print(f"Comps sampled ({len(comps)}):")
    for name, zone_id, hero_ids in comps:
        print(f"  - {name} ({zone_id}, {len(hero_ids)} heroes: {hero_ids})")
    print()

    header = f"{'Comp':30s} " + " ".join(f"Tier{t:>7d}" for t in range(1, 6))
    print(header)
    print("-" * len(header))

    results = {}
    sampler_rng = random.Random(12345)
    for comp_name, comp_zone_id, hero_ids in comps:
        # Real 4-of-~7 skill loadout and 2 trinkets + quirks for this comp
        # (COMPS/curated_comps/*.json via store.comp_loadouts), if one
        # was scraped for it — matches env/expedition_env.py's ExpeditionEnv
        # so eval mirrors whatever the model was actually trained against.
        # Both fall back to None when no match exists by name.
        hero_skill_ids = resolve_skill_ids(store, comp_name, hero_ids)
        trinkets_and_quirks = resolve_trinkets_and_quirks(store, comp_name, hero_ids)

        row = []
        for tier in range(1, 6):
            pool = bands[tier]
            weights = [max(e.chance, 0.01) for e in pool]
            hero_levels = [TIER_TO_HERO_LEVEL[tier]] * len(hero_ids)
            wins = 0
            timeouts = 0
            rewards = []
            lengths = []
            for ep in range(args.episodes):
                entry = sampler_rng.choices(pool, weights=weights, k=1)[0]
                # Deterministic seed (Python's built-in hash() is randomized
                # per-process by default, would break reproducibility here).
                seed = zlib.crc32(f"{comp_name}|{tier}|{ep}".encode()) % (2**31)
                winner, reward, steps, timed_out = run_episode(
                    env, model, hero_ids, entry.enemy_ids, hero_levels, hero_skill_ids, seed,
                    trinkets_and_quirks=trinkets_and_quirks,
                )
                if winner == "heroes":
                    wins += 1
                if timed_out:
                    timeouts += 1
                rewards.append(reward)
                lengths.append(steps)
            win_rate = wins / args.episodes
            mean_reward = statistics.mean(rewards)
            mean_len = statistics.mean(lengths)
            results[(comp_name, tier)] = {
                "win_rate": win_rate, "mean_reward": mean_reward,
                "mean_len": mean_len, "timeouts": timeouts,
            }
            row.append(f"{win_rate*100:5.0f}%{'*' if timeouts else ' '}")
        print(f"{comp_name[:30]:30s} " + " ".join(f"{r:>8s}" for r in row))

    print()
    print("* = at least one episode hit the 150-step cap without a winner")
    print()
    print("Detail (win_rate | mean_reward | mean_ep_len | timeouts):")
    for (comp_name, tier), r in results.items():
        print(f"  {comp_name[:30]:30s} tier{tier} (resolve {TIER_TO_HERO_LEVEL[tier]}): "
              f"{r['win_rate']*100:5.1f}% | reward {r['mean_reward']:+6.2f} | "
              f"len {r['mean_len']:5.1f} | timeouts {r['timeouts']}")


if __name__ == "__main__":
    main()
