"""Pair two `tools/fate_eval.py --json` runs scenario by scenario.

    python tools/pair_runs.py A.json B.json [--arm policy]

Both runs must have used the same `--episodes`/`--offset`, so every scenario
seed appears in both; the comparison is then paired (same comp, zone, tier,
length, torch and monsters) and reported with its standard error and an exact
McNemar test on the discordant SUCCESS pairs.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
from math import comb


def mcnemar(b: int, c: int) -> float:
    """Exact two-sided McNemar p-value for b wins against c losses."""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    p = sum(comb(n, i) for i in range(k + 1)) / 2 ** n
    return min(1.0, 2 * p)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("a")
    ap.add_argument("b")
    ap.add_argument("--arm", default="policy", help="arm to read from both files")
    ap.add_argument("--arm-a", default=None)
    ap.add_argument("--arm-b", default=None)
    args = ap.parse_args(argv)
    A = {r["seed"]: r for r in json.load(open(args.a))[args.arm_a or args.arm]}
    B = {r["seed"]: r for r in json.load(open(args.b))[args.arm_b or args.arm]}
    seeds = sorted(set(A) & set(B))
    d = [int(B[s]["success"]) - int(A[s]["success"]) for s in seeds]
    m = statistics.mean(d)
    se = statistics.pstdev(d) / math.sqrt(len(d))
    wins, losses = sum(x > 0 for x in d), sum(x < 0 for x in d)
    dd = [B[s]["deaths"] - A[s]["deaths"] for s in seeds]
    dq = [B[s]["quality"] - A[s]["quality"] for s in seeds]
    print(f"n={len(seeds)} paired scenarios")
    print(f"SUCCESS  A {100*statistics.mean(A[s]['success'] for s in seeds):.1f}%  "
          f"B {100*statistics.mean(B[s]['success'] for s in seeds):.1f}%  "
          f"B-A {100*m:+.1f}pp (SE {100*se:.1f})  {wins} won / {losses} lost  "
          f"McNemar p={mcnemar(wins, losses):.4f}")
    print(f"deaths/run B-A {statistics.mean(dd):+.3f} "
          f"(SE {statistics.pstdev(dd)/math.sqrt(len(dd)):.3f})   "
          f"quality B-A {statistics.mean(dq):+.2f} "
          f"(SE {statistics.pstdev(dq)/math.sqrt(len(dq)):.2f})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
