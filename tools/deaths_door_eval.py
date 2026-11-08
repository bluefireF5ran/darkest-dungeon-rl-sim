"""Death's Door, measured: how often heroes go down, how often they are hit
there, and how often that kills them -- per checkpoint, on paired scenarios.

    python tools/deaths_door_eval.py --models models/a.zip,models/b.zip --episodes 600
    python tools/deaths_door_eval.py --models models/a.zip --episodes 1200 --offset 1200

The question (Fran, 2026-10-12, docs/NEXT.md item 3): after the 2026-10-11
engine fix a downed hero dies 0.36 per entry against his 0 in 9, and takes
0.86 deathblow rolls per entry. The engine counts all three
(`CombatEngine.deaths_door_entries`, `.deathblow_rolls`, `.deathblow_deaths`),
so nothing here parses a log.

Scenarios are `tools/fate_eval.py`'s, the `eval_expedition.py --all-comps`
generator, so every checkpoint faces the same comp, zone, tier, length,
torch and monsters, and the second model is paired with the first by seed.
The headline is DEATHS PER ENTRY with its standard error, and SUCCESS
beside it: a policy that never enters Death's Door because it retreats
from everything is not the one Fran asked for.
"""
from __future__ import annotations

import argparse
import json
import math
import multiprocessing as mp
import statistics
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

DEFAULT_MODEL = REPO / "models" / "dd_combat_ppo_full_expedition.zip"


def run_chunk(job):
    from tools.fate_eval import _be_a_good_neighbour                   # noqa: PLC0415
    _be_a_good_neighbour()
    model_path, scen = job
    from sb3_contrib import MaskablePPO                                # noqa: PLC0415
    from sb3_contrib.common.wrappers import ActionMasker               # noqa: PLC0415
    from env import ExpeditionSequenceEnv, mask_fn                     # noqa: PLC0415
    from env.fate_wrapper import wrap_for                              # noqa: PLC0415
    from eval_expedition import deterministic_actor, run_full_expedition_episode  # noqa: PLC0415
    base_env = ExpeditionSequenceEnv(sampler_seed=None)
    probe = MaskablePPO.load(str(model_path), device="cpu")
    env = ActionMasker(wrap_for(base_env, probe.observation_space.shape[0]), mask_fn)
    model = MaskablePPO.load(str(model_path), env=env, device="cpu")
    model._ddsim_fast_actor = deterministic_actor(model)
    rows = []
    for s in scen:
        o = run_full_expedition_episode(
            env, model, s["comp"], s["heroes"], s["zone"], s["tier"], s["band"], s["num"],
            s["seed"], hero_skill_ids=s["skills"], extra_options={"sampler_seed": s["seed"]})
        e = base_env.engine
        rows.append(dict(seed=s["seed"], band=s["band"], success=bool(o.success),
                         deaths=int(o.deaths), entries=int(e.deaths_door_entries),
                         rolls=int(e.deathblow_rolls), dd_deaths=int(e.deathblow_deaths)))
    return str(model_path), rows


def summary(rows: list[dict]) -> dict:
    """Totals and ratio-of-sums rates, with a per-episode delta-method SE
    (entries are clustered by episode, so the episode is the unit)."""
    n = len(rows)
    ent = sum(r["entries"] for r in rows)
    out = {"n": n, "entries": ent, "rolls": sum(r["rolls"] for r in rows),
           "dd_deaths": sum(r["dd_deaths"] for r in rows),
           "success": sum(r["success"] for r in rows) / n if n else 0.0,
           "deaths_per_run": sum(r["deaths"] for r in rows) / n if n else 0.0}
    out["deaths_per_entry"] = out["dd_deaths"] / ent if ent else 0.0
    out["rolls_per_entry"] = out["rolls"] / ent if ent else 0.0
    if n > 1 and ent:
        mean_e = ent / n
        resid = [r["dd_deaths"] - out["deaths_per_entry"] * r["entries"] for r in rows]
        out["deaths_per_entry_se"] = statistics.pstdev(resid) / mean_e / math.sqrt(n)
    else:
        out["deaths_per_entry_se"] = 0.0
    p = out["success"]
    out["success_se"] = math.sqrt(p * (1 - p) / n) if n else 0.0
    return out


def paired(a: list[dict], b: list[dict], key: str) -> tuple[float, float, int]:
    base = {r["seed"]: r for r in a}
    diff = [float(r[key]) - float(base[r["seed"]][key]) for r in b if r["seed"] in base]
    if len(diff) < 2:
        return 0.0, 0.0, len(diff)
    return statistics.mean(diff), statistics.stdev(diff) / math.sqrt(len(diff)), len(diff)


def report(results: dict[str, list[dict]]) -> str:
    lines = []
    for name, rows in results.items():
        s = summary(rows)
        lines.append(f"{Path(name).name:40s} n={s['n']:5d}  SUCCESS {100*s['success']:5.1f}% "
                     f"(SE {100*s['success_se']:.1f})  deaths/run {s['deaths_per_run']:.3f}  "
                     f"DD entries {s['entries']}  rolls/entry {s['rolls_per_entry']:.2f}  "
                     f"deaths/entry {s['deaths_per_entry']:.3f} (SE {s['deaths_per_entry_se']:.3f})")
    names = list(results)
    for other in names[1:]:
        a, b = results[names[0]], results[other]
        for key in ("success", "deaths", "entries", "rolls", "dd_deaths"):
            m, se, n = paired(a, b, key)
            lines.append(f"  {Path(other).name} - {Path(names[0]).name} {key:9s} "
                         f"{m:+.3f} +- {se:.3f}/run (n={n}, {m / se if se else 0:+.1f} SE)")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--models", default=str(DEFAULT_MODEL),
                    help="comma-separated; the first is the base the others pair against")
    ap.add_argument("--episodes", type=int, default=600)
    ap.add_argument("--offset", type=int, default=0)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--json", help="also write every row here")
    args = ap.parse_args(argv)
    from env import ExpeditionSequenceEnv                              # noqa: PLC0415
    from tools.fate_eval import scenarios                              # noqa: PLC0415
    scen = scenarios(ExpeditionSequenceEnv(sampler_seed=None).store, args.episodes,
                     offset=args.offset)
    models = [m for m in args.models.split(",") if m]
    if len(set(models)) != len(models):
        ap.error("each model once: rows are pooled by path, so a repeat would merge two runs")
    k = max(1, args.workers)
    jobs = [(m, scen[i::k]) for m in models for i in range(k)]
    results: dict[str, list[dict]] = {m: [] for m in models}
    with mp.get_context("spawn").Pool(k) as pool:
        for name, rows in pool.imap_unordered(run_chunk, jobs):
            results[name] += rows
    print(report(results))
    if args.json:
        Path(args.json).write_text(json.dumps(results), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
