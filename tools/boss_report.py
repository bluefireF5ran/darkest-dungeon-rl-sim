"""Where boss expeditions kill heroes: per boss, and in the boss fight or before it.

    python tools/boss_report.py --model models/dd_combat_ppo_full_expedition.zip
    python tools/boss_report.py --model models/boss25_s0.zip --episodes 900

`eval_expedition.py --boss-quests` says boss quests cost 0.72-0.97 deaths per
expedition (2026-10-06, the shipped checkpoint); this says WHICH bosses and
WHEN, so the next step after training exposure is aimed instead of guessed.

Scenarios are the `--all-comps` generator's (`tools/fate_eval.scenarios`) with
`boss_quest` set, so two models are compared on identical expeditions. The
policy plays deterministically through the eval's own fast actor.
"""
from __future__ import annotations

import argparse
import collections
import multiprocessing as mp
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

DEFAULT_MODEL = REPO / "models" / "dd_combat_ppo_full_expedition.zip"


def _play(job):
    model_path, scen = job
    try:
        import torch
        torch.set_num_threads(1)
    except Exception:                                            # noqa: BLE001
        pass
    from sb3_contrib import MaskablePPO
    from sb3_contrib.common.maskable.utils import get_action_masks
    from sb3_contrib.common.wrappers import ActionMasker
    from env import ExpeditionSequenceEnv, mask_fn
    from env.fate_wrapper import wrap_for
    from eval_expedition import deterministic_actor
    base = ExpeditionSequenceEnv(sampler_seed=None)
    probe = MaskablePPO.load(str(model_path), device="cpu")
    env = ActionMasker(wrap_for(base, probe.observation_space.shape[0]), mask_fn)
    model = MaskablePPO.load(str(model_path), env=env, device="cpu")
    act = deterministic_actor(model)
    rows = []
    for s in scen:
        obs, _ = env.reset(seed=s["seed"], options=dict(
            hero_ids=s["heroes"], comp_name=s["comp"], zone_id=s["zone"], tier=s["tier"],
            length_band=s["band"], num_combats=s["num"], hero_skill_ids=s["skills"],
            sampler_seed=s["seed"], boss_quest=True))
        u = env.unwrapped
        n = len(s["heroes"])
        deaths_before = deaths_in_boss = 0
        boss = None
        done = False
        while not done:
            alive_before = sum(1 for h in u.engine.heroes if h.is_alive())
            in_boss = u._combat_idx >= u._num_combats - 1
            if in_boss and boss is None:
                boss = "+".join(sorted({e.definition_id if hasattr(e, "definition_id")
                                        else e.name for e in u.engine.enemies
                                        if getattr(e, "is_boss", False)} or
                                       {e.name for e in u.engine.enemies}))
            obs, _r, term, trunc, _ = env.step(act(obs, get_action_masks(env)))
            done = term or trunc
            lost = max(0, alive_before - sum(1 for h in u.engine.heroes if h.is_alive()))
            if in_boss:
                deaths_in_boss += lost
            else:
                deaths_before += lost
        living = sum(1 for h in u.engine.heroes if h.is_alive())
        rows.append(dict(zone=s["zone"], tier=s["tier"], band=s["band"], boss=boss or "?",
                         reached_boss=boss is not None, deaths_before=deaths_before,
                         deaths_in_boss=deaths_in_boss, wiped=living == 0,
                         won=bool(term and living > 0 and not getattr(u, "_abandoned", False)
                                  and u._combat_idx >= u._num_combats - 1)))
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--model", default=str(DEFAULT_MODEL))
    ap.add_argument("--episodes", type=int, default=600)
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args(argv)
    from env import ExpeditionSequenceEnv
    from tools.fate_eval import scenarios
    scen = scenarios(ExpeditionSequenceEnv(sampler_seed=None).store, args.episodes)
    chunks = [scen[i::args.workers] for i in range(args.workers)]
    with mp.get_context("spawn").Pool(args.workers) as pool:
        rows = [r for part in pool.map(_play, [(args.model, c) for c in chunks]) for r in part]
    by = collections.defaultdict(list)
    for r in rows:
        by[(r["zone"], r["boss"])].append(r)
    n = len(rows)
    print(f"{n} boss expeditions, {Path(args.model).name}")
    print(f"  deaths before the boss {sum(r['deaths_before'] for r in rows)/n:.3f}/run, "
          f"in the boss fight {sum(r['deaths_in_boss'] for r in rows)/n:.3f}/run, "
          f"wipes {sum(r['wiped'] for r in rows)}")
    print(f"{'zone':10s} {'boss':38s} {'n':>4s} {'boss-fight deaths/run':>22s} {'wipe%':>6s}")
    for (zone, boss), rs in sorted(by.items(), key=lambda kv: -sum(r['deaths_in_boss'] for r in kv[1]) / len(kv[1])):
        k = len(rs)
        print(f"{zone:10s} {boss[:38]:38s} {k:4d} "
              f"{sum(r['deaths_in_boss'] for r in rs)/k:22.2f} "
              f"{100*sum(r['wiped'] for r in rs)/k:6.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
