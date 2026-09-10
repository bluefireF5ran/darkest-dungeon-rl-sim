"""Compare two checkpoints on the SAME dungeons, and say whether the gap is real.

    python tools/ab_compare.py --a models/dd_combat_ppo_full_expedition.zip \
                               --b models/dd_combat_ppo_full_expedition.zip \
                               --episodes 8 --comps 12 --comp-seed 1

    python tools/ab_compare.py --a <ckpt> --self-check        # instrument test

## Why this exists

`eval_expedition.py --full-expedition` is not reproducible. It builds its env as
`ExpeditionSequenceEnv(sampler_seed=None)` -- `random.Random(None)` seeds from OS
entropy -- and never reseeds `_sampler_rng` per episode. `reset(seed=)` only
reseeds the COMBAT rng; which enemy comp each combat draws comes from the
sampler. So two runs of the identical command face different monsters.

Measured, same checkpoint, `--episodes 4 --comps 2 --comp-seed 1` twice:

    Marked Prey: Snipe  medium    75%  ->  50%
    Marked Prey: Snipe  long      25%  ->   0%
    overall                     41.7%  -> 33.3%

**8.4pp of spread with the model held fixed.** Every historical "this fidelity
fix moved SUCCESS by 3pp" was read off an instrument with more play in it than
the effect being measured, which is the real content of "the eval curve is noise
with an envelope".

## What this does instead

1. **Fixes the ruler.** The scenario list is derived deterministically from
   `--comp-seed`/`--comps`/`--episodes`, and `_sampler_rng` is reseeded from the
   scenario before every episode -- the rule `bridge/rollout.py::Rollout.evaluate`
   already follows and this eval path never did. Run it twice on one model and
   you get the same number, exactly; that is what `--self-check` asserts.

2. **Pairs the comparison.** Both checkpoints play the *same* dungeon list, and
   the statistic is the per-scenario difference. Scenario difficulty is by far
   the largest variance term and it cancels exactly in a paired design, so this
   resolves differences an unpaired comparison of the same size cannot.

3. **Reports uncertainty.** A bootstrap CI over scenarios for the paired delta,
   plus McNemar's exact test on the discordant pairs for the binary SUCCESS
   metric. If the interval spans zero, the honest answer is "not distinguishable
   at this sample size" -- and the tool says how many episodes would be needed.

## What it does NOT do

It does not remove *policy* variance from training (two runs at different
`--seed` genuinely differ, ~4pp; that needs K seeds per arm, which is a training
question, not an eval one). It removes the scenario luck that sat on top of it.
"""

from __future__ import annotations

import argparse
import math
import random
import statistics
import sys
import zlib
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# sb3_contrib/stable_baselines3 are imported LAZILY, inside the functions that
# need them. They drag torch in, and torch is ~454 MB resident PER PROCESS --
# and every xdist worker COLLECTS the whole suite, so one module-level import
# here cost 454 MB x 16 workers. Collection went 626 MB -> 92 MB. See pytest.ini.

from env import ExpeditionSequenceEnv, mask_fn
from env.expedition_env import CORE_ZONE_IDS, UNRESTRICTED_HEROES
from env.expedition_sequence_env import EXPEDITION_LENGTH_BANDS
from eval_expedition import (
    ExpeditionOutcome, pick_representative_comps, resolve_skill_ids,
    run_full_expedition_episode,
)

# Fixed, and deliberately not exposed as a flag. It is the seed for the
# tier/num_combats draws that define the scenario list; making it settable would
# invite quoting a comparison from a scenario set nobody can reconstruct.
SCENARIO_RNG_SEED = 12345

METRICS = ("success", "legacy_success", "completed", "zero_death", "wiped",
           "truncated_alive", "deaths", "retreats", "final_stress", "reward",
           "combats_reached")
RATE_METRICS = {"success", "legacy_success", "completed", "zero_death",
                "wiped", "truncated_alive"}


@dataclass(frozen=True)
class Scenario:
    """One dungeon, identified independently of any policy.

    `ep_seed` seeds BOTH the combat rng (via `reset(seed=)`) and the scenario
    sampler (via `_sampler_rng.seed`), which together is what makes a scenario
    reproducible. Keyed on comp/band/index only -- never on which model is
    about to play it, or the two arms would face different dungeons and the
    whole exercise would measure the draw.
    """
    comp_name: str
    zone_id: str
    hero_ids: tuple[str, ...]
    length_band: str
    tier: int
    num_combats: int
    ep_seed: int
    episode: int


def build_scenarios(store, n_comps: int, comp_seed: int, episodes: int) -> list[Scenario]:
    """The scenario list, matching eval_expedition's construction.

    Same comp sampling, same band order, same `crc32(comp|band|ep)` episode
    seed and the same `random.Random(12345)` tier/num_combats draw order, so a
    cell here lines up with the same cell there. The one addition is that the
    result is a value we can hand to two models in turn.
    """
    comps = pick_representative_comps(store, n_comps, comp_seed)
    bands = list(EXPEDITION_LENGTH_BANDS.keys())
    rng = random.Random(SCENARIO_RNG_SEED)
    out = []
    for comp_name, zone_id, hero_ids in comps:
        for band in bands:
            lo, hi = EXPEDITION_LENGTH_BANDS[band]
            for ep in range(episodes):
                tier = rng.randint(1, 5)
                num_combats = rng.randint(lo, hi)
                seed = zlib.crc32(f"{comp_name}|{band}|{ep}".encode()) % (2**31)
                out.append(Scenario(comp_name, zone_id, tuple(hero_ids), band,
                                    tier, num_combats, seed, ep))
    return out


def randomised_options(gen, sc, parts, boss_p: float = 0.15) -> dict:
    """Reset options that move a scenario onto a randomised distribution.

    `parts` is the subset of shifts to apply. It MUST be able to match the
    config a checkpoint was trained with, and that is not a nicety: scoring a
    model on a bundle containing shifts it never trained on measures how it
    copes with unseen content, not whether its own training paid off. The first
    run of this tool made exactly that mistake -- a 6M model trained on
    loadout+region+resolve was scored against a bundle that also randomised the
    kit and added boss quests, so the +13pp it showed on the curated
    distribution vanished into a distribution neither arm had seen.

    Generated ONCE per scenario and handed to BOTH arms, so the pairing stays
    exactly as tight: same party, same kit, same region, same boss, same
    monsters. `gen` is a value-generator env with every knob on, reseeded per
    scenario by the caller.
    """
    hero_ids = list(sc.hero_ids)
    out = {}
    if "loadout" in parts:
        out["hero_skill_ids"] = [[] if h in UNRESTRICTED_HEROES
                                 else gen._random_skill_ids(h) for h in hero_ids]
    if "kit" in parts:
        out["trinkets_and_quirks"] = [gen._random_kit(h) for h in hero_ids]
    if "resolve" in parts:
        out["hero_levels"] = gen.sample_hero_levels(sc.tier, len(hero_ids))
    if "region" in parts:
        others = [z for z in CORE_ZONE_IDS if z != sc.zone_id]
        out["zone_id"] = gen._loadout_rng.choice(others + [sc.zone_id])
    if "boss" in parts:
        # `boss_p` is separately settable because 0.15 -- the training rate --
        # dilutes the one shift measured as significant by about six to one.
        # To ask "is this checkpoint better AT BOSSES", set it to 1.0; to ask
        # "is it better over a realistic campaign", leave it.
        out["boss_quest"] = gen._loadout_rng.random() < boss_p
    return out


RANDOMISABLE = ("loadout", "kit", "resolve", "region", "boss")


def play(env, model, store, sc: Scenario, extra=None) -> ExpeditionOutcome:
    """One scenario, one policy, reproducibly.

    The `_sampler_rng.seed` line is the whole point: without it the enemy draw
    depends on how many episodes this env instance has already run, so the
    second model to play would face a different dungeon than the first.
    """
    env.unwrapped._sampler_rng.seed(sc.ep_seed)
    return run_full_expedition_episode(
        env, model, sc.comp_name, list(sc.hero_ids), sc.zone_id, sc.tier,
        sc.length_band, sc.num_combats, sc.ep_seed,
        hero_skill_ids=resolve_skill_ids(store, sc.comp_name, list(sc.hero_ids)),
        extra_options=extra,
    )


def bootstrap_ci(deltas, iters=10000, alpha=0.05, seed=0):
    """Percentile bootstrap over scenarios for the mean paired difference.

    Non-parametric on purpose: the per-scenario delta for a rate metric is
    supported on {-1, 0, 1}, which is nowhere near normal at these sample
    sizes, and a t-interval would overstate precision.
    """
    if not deltas:
        return (float("nan"), float("nan"))
    rng = random.Random(seed)
    n = len(deltas)
    means = []
    for _ in range(iters):
        means.append(sum(deltas[rng.randrange(n)] for _ in range(n)) / n)
    means.sort()
    return (means[int(alpha / 2 * iters)], means[int((1 - alpha / 2) * iters)])


def mcnemar_exact(b: int, c: int) -> float:
    """Two-sided exact McNemar p-value on discordant counts.

    `b` = scenarios A won and B lost, `c` = the reverse. Concordant pairs carry
    no information about a difference and are correctly ignored -- which is
    exactly why a paired design is more sensitive here than comparing two
    marginal rates.
    """
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / (2 ** n)
    return min(1.0, 2 * tail)


def episodes_needed(deltas, target_ci_halfwidth=0.02):
    """Rough n for a given precision, from the observed paired SD.

    Reported so "not significant" comes with "here is what it would take"
    rather than leaving the reader to guess.
    """
    if len(deltas) < 2:
        return None
    sd = statistics.pstdev(deltas)
    if sd == 0:
        return len(deltas)
    return int(math.ceil((1.96 * sd / target_ci_halfwidth) ** 2))


def summarise(name, values):
    return f"{name}: {statistics.mean(values):.4f}"


def main():
    from sb3_contrib import MaskablePPO
    from sb3_contrib.common.wrappers import ActionMasker
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--a", required=True, help="baseline checkpoint")
    ap.add_argument("--b", help="candidate checkpoint (omit with --self-check)")
    ap.add_argument("--self-check", action="store_true",
                    help="play --a as BOTH arms. Every paired delta must be exactly "
                         "0.0; anything else means the harness is still "
                         "non-deterministic and no comparison it reports can be "
                         "trusted. Run this after touching the env or this file.")
    ap.add_argument("--episodes", type=int, default=8, help="episodes per comp x band cell")
    ap.add_argument("--comps", type=int, default=12)
    ap.add_argument("--comp-seed", type=int, default=1,
                    help="1 with --comps 12 reproduces the archived baselines' comp set")
    ap.add_argument("--metric", default="success", choices=METRICS,
                    help="headline metric for the significance test")
    ap.add_argument("--eval-randomised", nargs="?", const="all", default=None,
                    metavar="PARTS",
                    help="score both checkpoints on a RANDOMISED distribution "
                         "instead of the curated one. Optionally a comma list of "
                         + ",".join(RANDOMISABLE) + " (default: all). MATCH THIS "
                         "TO WHAT THE CHECKPOINTS TRAINED ON -- scoring a model "
                         "against shifts it never saw measures how it copes with "
                         "unseen content, not whether its training paid off. "
                         "Generated once per scenario and given to BOTH arms, so "
                         "the pairing is unchanged.")
    ap.add_argument("--eval-boss-p", type=float, default=0.15, metavar="P",
                    help="with --eval-randomised including boss, how often a "
                         "scenario is a boss quest. 0.15 matches training; 1.0 "
                         "isolates whether a checkpoint is better AT bosses, "
                         "which 0.15 dilutes about six to one.")
    ap.add_argument("--bootstrap", type=int, default=10000)
    args = ap.parse_args()

    if not args.self_check and not args.b:
        ap.error("--b is required unless --self-check is given")

    base = ExpeditionSequenceEnv(sampler_seed=None)
    env = ActionMasker(base, mask_fn)
    store = base.store
    # Value generator only, never played. Every knob on so its helpers work.
    gen = (ExpeditionSequenceEnv(store=store, sampler_seed=0,
                                 loadout_random_p=1.0, kit_random_p=1.0,
                                 resolve_sampling=True, cross_region_p=1.0)
           if args.eval_randomised else None)
    parts = (set(RANDOMISABLE) if args.eval_randomised in (None, "all")
             else {p.strip() for p in args.eval_randomised.split(",") if p.strip()})
    if gen is not None:
        unknown = parts - set(RANDOMISABLE)
        if unknown:
            ap.error(f"unknown --eval-randomised parts: {sorted(unknown)}")

    model_a = MaskablePPO.load(args.a, env=env, device="cpu")
    model_b = model_a if args.self_check else MaskablePPO.load(args.b, env=env, device="cpu")

    scenarios = build_scenarios(store, args.comps, args.comp_seed, args.episodes)
    if gen is not None:
        print(f"eval distribution: RANDOMISED ({','.join(sorted(parts))})")
    print(f"A: {args.a}")
    print(f"B: {args.b if not args.self_check else '(same as A - self-check)'}")
    print(f"{len(scenarios)} paired scenarios "
          f"({args.comps} comps x {len(EXPEDITION_LENGTH_BANDS)} bands x {args.episodes} episodes)")
    print()

    rows = []
    for i, sc in enumerate(scenarios, 1):
        extra = None
        if gen is not None:
            # ONE draw per scenario, handed to both arms -- the pairing is
            # unchanged, only the test bed moves.
            gen._loadout_rng.seed(sc.ep_seed)
            extra = randomised_options(gen, sc, parts, args.eval_boss_p)
        oa = play(env, model_a, store, sc, extra)
        ob = play(env, model_b, store, sc, extra)
        rows.append((sc, oa, ob))
        if i % 20 == 0 or i == len(scenarios):
            # stderr, so a redirected stdout stays a clean, parseable table.
            # `\r` erases nothing once stdout is a file, which is how the
            # progress counter ended up welded onto the header row.
            print(f"  {i}/{len(scenarios)}", end="\r", file=sys.stderr, flush=True)
    print(file=sys.stderr)

    print(f"{'metric':18s} {'A':>10s} {'B':>10s} {'B-A':>10s} "
          f"{'95% CI':>20s}  {'p':>8s}")
    print("-" * 82)

    headline_deltas = None
    for m in METRICS:
        av = [float(getattr(oa, m)) for _sc, oa, _ob in rows]
        bv = [float(getattr(ob, m)) for _sc, _oa, ob in rows]
        deltas = [b - a for a, b in zip(av, bv)]
        lo, hi = bootstrap_ci(deltas, iters=args.bootstrap)
        if m in RATE_METRICS:
            b_wins = sum(1 for d in deltas if d > 0)
            a_wins = sum(1 for d in deltas if d < 0)
            p = mcnemar_exact(a_wins, b_wins)
            ptxt = f"{p:8.4f}"
        else:
            ptxt = "       -"
        star = " *" if m in RATE_METRICS and (lo > 0 or hi < 0) else ""
        print(f"{m:18s} {statistics.mean(av):10.4f} {statistics.mean(bv):10.4f} "
              f"{statistics.mean(deltas):+10.4f} [{lo:+7.4f},{hi:+7.4f}]  {ptxt}{star}")
        if m == args.metric:
            headline_deltas = deltas

    print()
    if args.self_check:
        worst = max(abs(b - a) for _sc, oa, ob in rows
                    for a, b in [(oa.reward, ob.reward)])
        nonzero = [m for m in METRICS
                   if any(getattr(oa, m) != getattr(ob, m) for _sc, oa, ob in rows)]
        if nonzero:
            print("SELF-CHECK FAILED - the harness is NOT deterministic.")
            print(f"  metrics that differed between two runs of the SAME model: {nonzero}")
            print(f"  worst reward gap on a single scenario: {worst:.6f}")
            print("  Any A/B number from this harness is meaningless until this is 0.")
            return 1
        print("SELF-CHECK PASSED - the same model twice gives bit-identical outcomes")
        print(f"  across all {len(rows)} scenarios and all {len(METRICS)} metrics.")
        print("  Scenario luck is fully removed; a nonzero B-A is the policy.")
        return 0

    lo, hi = bootstrap_ci(headline_deltas, iters=args.bootstrap)
    mean = statistics.mean(headline_deltas)
    b_wins = sum(1 for d in headline_deltas if d > 0)
    a_wins = sum(1 for d in headline_deltas if d < 0)
    print(f"HEADLINE ({args.metric}): B - A = {mean:+.4f}  95% CI [{lo:+.4f}, {hi:+.4f}]")
    print(f"  discordant scenarios: B better on {b_wins}, A better on {a_wins}, "
          f"tied on {len(headline_deltas) - a_wins - b_wins}")
    if lo <= 0 <= hi:
        need = episodes_needed(headline_deltas)
        print("  NOT DISTINGUISHABLE at this sample size - the interval spans zero.")
        if need:
            print(f"  A +-2pp interval would need ~{need} paired scenarios "
                  f"(~{math.ceil(need / (args.comps * 3))} episodes per cell); "
                  f"this run had {len(headline_deltas)}.")
    else:
        print(f"  DISTINGUISHABLE: the interval excludes zero, so "
              f"{'B' if mean > 0 else 'A'} is genuinely ahead on this scenario set.")
    print()
    print("  Caveat: this removes SCENARIO variance, not training variance. Two runs")
    print("  at different --seed differ by ~4pp on their own; that needs K seeds per")
    print("  arm and is a training question, not an eval one.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
