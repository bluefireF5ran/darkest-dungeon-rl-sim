"""The two arms of the 2026-08-27 retrain, on one shared dungeon list.

Arm 1 `burnfix` carries the Runaway fix alone. Arm 2 `prevention` carries the
same fix plus `--prevention-reward`. Same seed, same recipe, so the only thing
between them is the reward change -- which is the point: the fidelity fix moves
the world for both, and without a control there is no way to say which half of
an improvement came from where.

Both arms are scored in the SAME env. The prevention terms live inside Φ and
change reward only, never dynamics (`tests/env/test_prevention_reward.py` pins
that), so a single scoring env is fair to both and avoids the confound of
grading each arm in its own world.

    python tools/compare_prevention.py --episodes 8

Prints a paired per-band table plus an exact McNemar on SUCCESS, which is a
paired BINARY outcome and wants McNemar rather than a mean -- the lesson
`tools/ablate_forecast.py` learned the hard way when a quality interval could
not resolve a 3pp success change.
"""
import argparse
import collections
import json
import math
import pathlib
import sys
import zlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from tools.comp_ga import CSV_DIR, GAME_DIR, Evaluator, Scenario, genome_from_loadout
from env.expedition_env import TIER_TO_HERO_LEVEL, build_comp_pool
from env.expedition_sequence_env import EXPEDITION_LENGTH_BANDS

BANDS = ("short", "medium", "long")


def mcnemar(a_wins: int, b_wins: int) -> float:
    """Exact two-sided binomial test on the discordant pairs."""
    n = a_wins + b_wins
    if n == 0:
        return 1.0
    k = min(a_wins, b_wins)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / (2 ** n)
    return min(1.0, 2 * tail)


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--a", default="models/burnfix_20260827.zip")
    ap.add_argument("--b", default="models/prevention_20260827.zip")
    ap.add_argument("--episodes", type=int, default=8,
                    help="episodes per comp per band")
    ap.add_argument("--comps", type=int, default=24)
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--tier", type=int, default=5)
    ap.add_argument("--zones", default="crypts,warrens,weald,cove")
    ap.add_argument("--out", default="archive/logs/prevention_ab_20260827.json")
    args = ap.parse_args()

    from data.store_cache import load_store_cached
    store = load_store_cached(CSV_DIR, GAME_DIR)
    pool = build_comp_pool(store)
    # A deterministic spread across the pool rather than the first N, so the
    # comparison is not decided by whatever sorts first.
    picked = sorted(pool, key=lambda r: zlib.crc32(r[0].encode()))[:args.comps]

    genomes, names = [], []
    for name, _zone, _heroes in picked:
        genome = genome_from_loadout(store.comp_loadout_by_name(name), store)
        if genome is not None:
            genomes.append(genome)
            names.append(name)

    zones = tuple(z.strip() for z in args.zones.split(",") if z.strip())
    rows = {}
    per_comp = collections.defaultdict(dict)

    for band in BANDS:
        lo, hi = EXPEDITION_LENGTH_BANDS[band]
        scenario = Scenario(zones=zones, tier=args.tier, band=band,
                            num_combats=(lo + hi) // 2,
                            episodes_per_zone=args.episodes,
                            resolve_level=TIER_TO_HERO_LEVEL[args.tier])
        band_scores = {}
        for arm, path in (("burnfix", args.a), ("prevention", args.b)):
            evaluator = Evaluator(path, args.workers)
            try:
                band_scores[arm] = evaluator.score_many(genomes, scenario, 0)
            finally:
                evaluator.close()
            print(f"  {band:6s} {arm:10s} done", flush=True)
        rows[band] = band_scores
        for name, sa, sb in zip(names, band_scores["burnfix"],
                                band_scores["prevention"]):
            per_comp[name][band] = (sa.run_score, sb.run_score,
                                    sa.success, sb.success)

    print("")
    print(f"paired A/B -- {len(genomes)} comps x {len(zones)} zones x "
          f"{args.episodes} episodes, tier {args.tier}")
    print("")
    print(f"  {'band':7s} {'burnfix':>18s} {'prevention':>18s} {'delta':>9s} "
          f"{'succ A':>8s} {'succ B':>8s} {'stress A':>9s} {'stress B':>9s}")
    a_wins_all = b_wins_all = 0
    for band in BANDS:
        a_list, b_list = rows[band]["burnfix"], rows[band]["prevention"]
        a_mean = sum(s.run_score for s in a_list) / len(a_list) * 100
        b_mean = sum(s.run_score for s in b_list) / len(b_list) * 100
        a_se = (sum(s.run_score_se ** 2 for s in a_list) ** 0.5 / len(a_list)) * 100
        b_se = (sum(s.run_score_se ** 2 for s in b_list) ** 0.5 / len(b_list)) * 100
        a_succ = sum(s.success for s in a_list) / len(a_list) * 100
        b_succ = sum(s.success for s in b_list) / len(b_list) * 100
        a_stress = sum(s.stress for s in a_list) / len(a_list)
        b_stress = sum(s.stress for s in b_list) / len(b_list)
        a_wins = sum(1 for x, y in zip(a_list, b_list) if x.success > y.success)
        b_wins = sum(1 for x, y in zip(a_list, b_list) if y.success > x.success)
        a_wins_all += a_wins
        b_wins_all += b_wins
        print(f"  {band:7s} {a_mean:12.1f} +-{a_se:3.1f} "
              f"{b_mean:12.1f} +-{b_se:3.1f} {b_mean - a_mean:+9.1f} "
              f"{a_succ:7.1f}% {b_succ:7.1f}% {a_stress:9.1f} {b_stress:9.1f}")

    print("")
    print(f"  SUCCESS is a paired BINARY outcome, so McNemar rather than a mean:")
    print(f"    burnfix-only wins {a_wins_all}, prevention-only wins {b_wins_all}, "
          f"p = {mcnemar(a_wins_all, b_wins_all):.4f}")

    out = pathlib.Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(
        {"a": args.a, "b": args.b, "episodes": args.episodes,
         "comps": names, "zones": list(zones), "tier": args.tier,
         "bands": {band: {arm: [s.as_dict() for s in scores]
                          for arm, scores in rows[band].items()}
                   for band in BANDS}}, indent=2), encoding="utf-8")
    print(f"\n  wrote {out}")


if __name__ == "__main__":
    main()
