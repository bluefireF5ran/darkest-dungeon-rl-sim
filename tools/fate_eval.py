"""What knowing the dice is worth: the policy alone against the policy with a
look-ahead that sees the rolls, on identical expeditions.

    python tools/fate_eval.py --episodes 60                    # all three arms
    python tools/fate_eval.py --arms policy,fate --episodes 200
    python tools/fate_eval.py --model models/<ckpt>.zip --workers 6

Arms (`bridge/fate.py` holds the machinery):

- **policy** -- the checkpoint, deterministic. The baseline.
- **oracle** -- every legal action scored `r + gamma V(s')` on a clone that
  keeps the simulator's own RNG, so the whole future of that step is the true
  one. An upper bound: the live game reveals less.
- **dice** -- no critic at all: an attack the known dice say will MISS is
  swapped for the policy's best-rated candidate that will hit (a kill first),
  and a candidate that will KILL beats a pick that will not.
- **gate** -- fate used as an informed veto: the policy's own top-k actions
  only, and the policy's pick stands unless the known dice make another one
  better by more than `--margin`. `oracle_gate` is the same with the true
  future (its upper bound).
- **fate** -- the honest arm. Only what the save reveals in the real game is
  known (the hit roll, the first target's crit roll and damage, DECOMP.md F15);
  everything after is a fresh stream, averaged over `--continuations` draws.
  This is the number that predicts the live advantage.

Scenarios come from the same generator as `eval_expedition.py --all-comps`
(`_cell_scenario` + `sampler_seed`), so every arm faces the same comp, zone,
tier, length, torch and monsters, and the comparison is paired per scenario.
SUCCESS is the eval's own definition (completed, no retreat, no abandon, no
death). The standard error is quoted with every rate; one seed of scenarios
is not two.
"""
from __future__ import annotations

import argparse
import math
import multiprocessing as mp
import statistics
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

DEFAULT_MODEL = REPO / "models" / "dd_combat_ppo_full_expedition.zip"


def scenarios(store, n: int, offset: int = 0):
    from eval_expedition import (_cell_scenario, all_comps,          # noqa: PLC0415
                                 resolve_skill_ids)
    from env.expedition_sequence_env import EXPEDITION_LENGTH_BANDS  # noqa: PLC0415
    comps = all_comps(store)
    bands = list(EXPEDITION_LENGTH_BANDS)
    out = []
    i = 0
    while len(out) < n:
        k = offset + i
        name, zone, heroes = comps[k % len(comps)]
        band = bands[(k // len(comps)) % len(bands)]
        lo, hi = EXPEDITION_LENGTH_BANDS[band]
        tier, num, seed = _cell_scenario(name, band, k // (len(comps) * len(bands)), lo, hi)
        out.append(dict(comp=name, zone=zone, heroes=heroes, band=band, tier=tier,
                        num=num, seed=seed, skills=resolve_skill_ids(store, name, heroes)))
        i += 1
    return out


def make_actor(arm: str, model, env, continuations: int, margin: float = 2.0,
               topk: int = 4):
    from bridge.fate import (gated_choice, make_value_fn, peek_fate,  # noqa: PLC0415
                             q_values, searchable)
    from eval_expedition import deterministic_actor                  # noqa: PLC0415
    base = deterministic_actor(model)
    if arm == "policy":
        return base
    value_fn = make_value_fn(model)
    unwrapped = env.unwrapped

    def actor(obs, masks):
        legal = [int(i) for i in range(len(masks)) if masks[i]]
        if arm in ("oracle", "fate"):
            legal = searchable(legal) or legal
        if len(legal) <= 1:
            return legal[0] if legal else base(obs, masks)
        if arm == "oracle":
            q = q_values(unwrapped, value_fn, legal)
        elif arm == "dice":
            from bridge.fate import dice_choice                         # noqa: PLC0415
            return dice_choice(model, unwrapped, obs, masks, peek_fate(unwrapped.engine.rng),
                               topk=topk)
        elif arm in ("gate", "oracle_gate"):
            fate = None if arm == "oracle_gate" else peek_fate(unwrapped.engine.rng)
            return gated_choice(model, unwrapped, value_fn, obs, masks, fate,
                                continuations=1 if fate is None else continuations,
                                margin=margin, topk=topk)
        else:
            q = q_values(unwrapped, value_fn, legal, fate=peek_fate(unwrapped.engine.rng),
                         continuations=continuations, reseed=True)
        return max(q, key=q.get)
    return actor


def _be_a_good_neighbour() -> None:
    """One torch thread and below-normal priority per worker.

    The first run left torch at its default (one thread per core) in every
    worker, and two of them beside a 12-env training run cut the trainer to a
    tenth of its speed. A look-ahead is many tiny forward passes, which gain
    nothing from intra-op threads anyway."""
    try:
        import torch                                                  # noqa: PLC0415
        torch.set_num_threads(1)
    except Exception:                                                 # noqa: BLE001
        pass
    try:
        import psutil                                                 # noqa: PLC0415
        p = psutil.Process()
        p.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS if hasattr(psutil, "BELOW_NORMAL_PRIORITY_CLASS") else 10)
    except Exception:                                                 # noqa: BLE001
        pass


def run_arm(job):
    _be_a_good_neighbour()
    arm, model_path, scen, continuations, margin, topk, allow_abandon = job
    from sb3_contrib import MaskablePPO                               # noqa: PLC0415
    from sb3_contrib.common.wrappers import ActionMasker              # noqa: PLC0415
    from env import ExpeditionSequenceEnv, mask_fn                    # noqa: PLC0415
    from eval_expedition import run_full_expedition_episode           # noqa: PLC0415
    from env.fate_wrapper import wrap_for                             # noqa: PLC0415
    base_env = ExpeditionSequenceEnv(sampler_seed=None, allow_abandon=allow_abandon)
    probe = MaskablePPO.load(str(model_path), device="cpu")
    # a --fate-obs / --fate-outcomes checkpoint plays behind its wrapper
    env = ActionMasker(wrap_for(base_env, probe.observation_space.shape[0]), mask_fn)
    model = MaskablePPO.load(str(model_path), env=env, device="cpu")
    model._ddsim_fast_actor = make_actor(arm, model, base_env, continuations, margin, topk)
    rows = []
    for s in scen:
        t0 = time.time()
        o = run_full_expedition_episode(
            env, model, s["comp"], s["heroes"], s["zone"], s["tier"], s["band"], s["num"],
            s["seed"], hero_skill_ids=s["skills"], extra_options={"sampler_seed": s["seed"]})
        rows.append(dict(seed=s["seed"], band=s["band"], success=o.success, deaths=o.deaths,
                         wiped=o.wiped, quality=o.quality, stress=o.final_stress,
                         completed=o.completed, retreats=o.retreats,
                         truncated=o.truncated_alive, abandoned=o.abandoned,
                         combats_reached=o.combats_reached, num_combats=o.num_combats,
                         secs=time.time() - t0))
    return arm, rows


def _rate(xs):
    n = len(xs)
    p = sum(xs) / n if n else 0.0
    return p, math.sqrt(p * (1 - p) / n) if n else 0.0


def report(results: dict[str, list[dict]]) -> str:
    lines = []
    arms = list(results)
    for arm in arms:
        rows = results[arm]
        p, se = _rate([r["success"] for r in rows])
        d = statistics.mean(r["deaths"] for r in rows)
        q = statistics.mean(r["quality"] for r in rows)
        sec = statistics.mean(r["secs"] for r in rows)
        lines.append(f"{arm:8s} n={len(rows):4d}  SUCCESS {100*p:5.1f}% (SE {100*se:.1f})  "
                     f"deaths/run {d:.3f}  quality {q:.3f}  {sec:.1f}s/episode")
    if "policy" in results:
        base = {r["seed"]: r for r in results["policy"]}
        for arm in arms:
            if arm == "policy":
                continue
            pairs = [(base[r["seed"]], r) for r in results[arm] if r["seed"] in base]
            if not pairs:
                continue
            diff = [int(b["success"]) - int(a["success"]) for a, b in pairs]
            m = statistics.mean(diff)
            se = statistics.pstdev(diff) / math.sqrt(len(diff)) if len(diff) > 1 else 0.0
            wins = sum(1 for x in diff if x > 0)
            losses = sum(1 for x in diff if x < 0)
            dq = statistics.mean(b["quality"] - a["quality"] for a, b in pairs)
            dd = statistics.mean(b["deaths"] - a["deaths"] for a, b in pairs)
            lines.append(f"  {arm} - policy (paired, n={len(pairs)}): SUCCESS {100*m:+.1f}pp "
                         f"(SE {100*se:.1f}; {wins} won, {losses} lost)  "
                         f"deaths {dd:+.3f}/run  quality {dq:+.3f}")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--model", default=str(DEFAULT_MODEL))
    ap.add_argument("--episodes", type=int, default=60)
    ap.add_argument("--offset", type=int, default=0, help="scenario index to start from")
    ap.add_argument("--arms", default="policy,oracle,fate")
    ap.add_argument("--json", default=None, help="also write every row here, for pairing runs")
    ap.add_argument("--continuations", type=int, default=3)
    ap.add_argument("--margin", type=float, default=2.0,
                    help="gate: switch from the policy's pick only when the known "
                         "dice make another top-k action better by more than this")
    ap.add_argument("--topk", type=int, default=4)
    ap.add_argument("--no-abandon", action="store_true",
                    help="mask ACTION_ABANDON, as `train.py --no-abandon-train` does "
                         "in training: a policy that never saw the exit has no "
                         "learned value for it")
    ap.add_argument("--workers", type=int, default=3,
                    help="processes; each loads the store and the model (~1.5 GB each)")
    args = ap.parse_args(argv)
    from env import ExpeditionSequenceEnv                             # noqa: PLC0415
    store = ExpeditionSequenceEnv(sampler_seed=None).store
    scen = scenarios(store, args.episodes, args.offset)
    arms = [a.strip() for a in args.arms.split(",") if a.strip()]
    w = max(1, args.workers)
    chunks = [scen[i::w] for i in range(w)]
    jobs = [(a, args.model, c, args.continuations, args.margin, args.topk,
             not args.no_abandon)
            for a in arms for c in chunks if c]
    t0 = time.time()
    results: dict[str, list[dict]] = {a: [] for a in arms}
    with mp.get_context("spawn").Pool(min(len(jobs), w)) as pool:
        for arm, rows in pool.imap_unordered(run_arm, jobs):
            results[arm].extend(rows)
            print(f"  [{time.time()-t0:6.0f}s] {arm}: +{len(rows)} episodes", flush=True)
    print(report(results))
    if args.json:
        import json                                                    # noqa: PLC0415
        Path(args.json).write_text(json.dumps(results), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
