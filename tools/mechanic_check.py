"""Is a comp bad, or merely UNPRACTISED in the mechanic it is built on?

The question that made this necessary: with Backdraft and Controlled Burn
implemented (2026-08-27), `Burn Quartet` still scored **160th of 163** -- which
is either a verdict on the comp or a verdict on the training distribution, and
the two are not distinguishable without an arm that has actually seen the
mechanic. Only 7 comps in the whole pool can burn at all.

So: the comps of one mechanic FAMILY, scored by a general policy and by one
trained with `--focus-mechanic <family>`, on one shared dungeon list. A large
positive delta means the comps were unpractised; a null means they are simply
weak with the mechanic implemented.

**Read the per-comp column, not the family mean.** Measured on burn, the family
mean was +3.7 +-3.2 (noise) while `Burn Quartet` alone moved **+20.3**, four
standard errors. The dose is what matters: a comp whose whole plan is the
unpractised mechanic is scored on the policy's inexperience, one that merely
contains it is not.

    python tools/mechanic_check.py --mechanic dodge         --general models/prevention_20260827.zip         --focused models/dodgefocus_20260827.zip
"""
import argparse
import json
import pathlib
import statistics
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from tools.comp_ga import (CSV_DIR, GAME_DIR, Evaluator, Scenario,
                           genome_from_loadout)
from env.expedition_env import (TIER_TO_HERO_LEVEL, build_comp_pool,
                                comp_mechanics)
from env.expedition_sequence_env import EXPEDITION_LENGTH_BANDS


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mechanic", default="burn",
                    choices=("dodge", "bleed", "blight", "burn"))
    ap.add_argument("--general", default="models/prevention_20260827.zip")
    ap.add_argument("--focused", default="models/burnfocus_20260827.zip")
    ap.add_argument("--episodes", type=int, default=32)
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--zones", default="crypts,warrens,weald,cove")
    ap.add_argument("--tier", type=int, default=5)
    ap.add_argument("--band", default="medium")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    from data.store_cache import load_store_cached
    store = load_store_cached(CSV_DIR, GAME_DIR)

    names, genomes = [], []
    for name, _zone, _heroes in build_comp_pool(store):
        loadout = store.comp_loadout_by_name(name)
        if not loadout or args.mechanic not in comp_mechanics(store, loadout):
            continue
        genome = genome_from_loadout(loadout, store)
        if genome is not None:
            names.append(name)
            genomes.append(genome)
    if not genomes:
        print(f"no comp in the pool can {args.mechanic}")
        return

    lo, hi = EXPEDITION_LENGTH_BANDS[args.band]
    scenario = Scenario(zones=tuple(z.strip() for z in args.zones.split(",") if z.strip()),
                        tier=args.tier, band=args.band, num_combats=(lo + hi) // 2,
                        episodes_per_zone=args.episodes,
                        resolve_level=TIER_TO_HERO_LEVEL[args.tier])

    scored = {}
    focused_label = f"{args.mechanic}-focused"
    for arm, path in (("general", args.general), (focused_label, args.focused)):
        evaluator = Evaluator(path, args.workers)
        try:
            scored[arm] = evaluator.score_many(genomes, scenario, 0)
        finally:
            evaluator.close()
        print(f"  {arm} done", flush=True)

    print("")
    print(f"the {len(names)} comps in the pool built around {args.mechanic.upper()} -- "
          f"{args.episodes} episodes x {len(scenario.zones)} zones, "
          f"tier {args.tier} {args.band}")
    print("")
    print(f"  {'comp':34s} {'general':>18s} {focused_label:>18s} {'delta':>8s}")
    deltas = []
    for name, a, b in zip(names, scored["general"], scored[focused_label]):
        gap = (b.run_score - a.run_score) * 100
        deltas.append(gap)
        print(f"  {name[:34]:34s} {a.run_score * 100:12.1f} +-{a.run_score_se * 100:3.1f} "
              f"{b.run_score * 100:12.1f} +-{b.run_score_se * 100:3.1f} {gap:+8.1f}")

    mean = statistics.fmean(deltas)
    se = (statistics.stdev(deltas) / len(deltas) ** 0.5) if len(deltas) > 1 else 0.0
    verdict = "REAL" if abs(mean) > 2 * se else "inside the noise"
    print("")
    print(f"  paired mean {mean:+.1f} +-{se:.1f}  ->  {verdict}")
    print(f"  {sum(1 for d in deltas if d > 0)}/{len(deltas)} comps better focused")
    print("")
    print("  A large positive delta means the comps were UNPRACTISED, not bad --")
    print("  the mechanic works, the policy had simply never been shown it.")
    print(f"  A null means the comps really are weak with {args.mechanic} implemented.")

    out = pathlib.Path(args.out or
                       f"archive/logs/mechanic_check_{args.mechanic}_20260827.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(
        {"general": args.general, "focused": args.focused,
         "mechanic": args.mechanic, "episodes": args.episodes,
         "rows": [{"name": n, "general": a.as_dict(), "focused": b.as_dict()}
                  for n, a, b in zip(names, scored["general"],
                                     scored[focused_label])]}, indent=2),
        encoding="utf-8")
    print(f"\n  wrote {out}")


if __name__ == "__main__":
    main()
