"""Does the policy actually depend on the loadout it trained with?

    python tools/loadout_sensitivity.py --model models/dd_combat_ppo_full_expedition.zip
    python tools/loadout_sensitivity.py --model <ckpt> --self-check

The gate for the loadout phase. Training only ever shows a class the four skills
its comp curated, so the same class holding a different legal four is off
distribution. That is a hypothesis, and this measures it before anybody spends
3M steps on the strength of it.

**One model, two loadouts, the same dungeons.** Arm A fields each comp's curated
four; arm B fields a random legal 4-of-7 per hero. The scenario list, the paired
statistics and the per-scenario reseeding all come from `tools/ab_compare.py` --
this differs only in what the two arms vary, which is the loadout rather than
the checkpoint.

Two things make the pairing honest, both inherited:

* the scenario is reseeded before every episode, so both arms face the same
  monsters, the same torch and the same combat rolls;
* the randomised loadout is drawn from the SCENARIO seed, so re-running this
  command reproduces the same 'random' loadouts exactly.

`--self-check` plays the curated loadout as both arms. Every delta must be
exactly 0.0; anything else means the harness moved and no number it prints can
be believed. That discipline is not decoration here -- two measurements in this
project have returned a confident, wrong zero.

**Reading the result.** A large drop says the retrain is worth its compute. A
small one says the policy already generalises and the compute would buy noise,
which is this project's oldest failure mode -- and in that case the honest
outcome is to report it and not train.
"""

from __future__ import annotations

import argparse
import random
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sb3_contrib import MaskablePPO
from sb3_contrib.common.wrappers import ActionMasker

from env.expedition_env import SKILLS_PER_HERO, UNRESTRICTED_HEROES
from env.expedition_sequence_env import EXPEDITION_LENGTH_BANDS, ExpeditionSequenceEnv
from env.gym_env import mask_fn
from eval_expedition import resolve_skill_ids, run_full_expedition_episode
from tools.ab_compare import (METRICS, RATE_METRICS, bootstrap_ci,
                              build_scenarios, mcnemar_exact)


def random_loadout(store, hero_ids, seed: int):
    """A random legal 4-of-7 per hero, deterministic in `seed`.

    `UNRESTRICTED_HEROES` get `[]` -- exactly as
    `ExpeditionEnv._resolve_skill_ids` does it: the real game gives them all
    seven always, so there is no four to choose. Imported rather than repeated,
    because the arm this tool calls "curated" has to mean the same thing here
    as it does in the env, or the two arms differ by more than the loadout.
    """
    rng = random.Random(seed)
    out = []
    for hid in hero_ids:
        if hid in UNRESTRICTED_HEROES:
            out.append([])
            continue
        hero = store.heroes.get(hid)
        ids = sorted({s.id for s in hero.skills}) if hero else []
        out.append(sorted(rng.sample(ids, SKILLS_PER_HERO))
                   if len(ids) > SKILLS_PER_HERO else list(ids))
    return out


def play(env, model, store, sc, skills):
    """One scenario with an explicit loadout, reproducibly.

    The reseed is what pairs the arms: without it the enemy draw depends on how
    many episodes this env has already run, so the second arm would face a
    different dungeon and the delta would be luck.
    """
    env.unwrapped._sampler_rng.seed(sc.ep_seed)
    return run_full_expedition_episode(
        env, model, sc.comp_name, list(sc.hero_ids), sc.zone_id, sc.tier,
        sc.length_band, sc.num_combats, sc.ep_seed, hero_skill_ids=skills)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", required=True)
    ap.add_argument("--episodes", type=int, default=4,
                    help="episodes per comp x band cell")
    ap.add_argument("--comps", type=int, default=12)
    ap.add_argument("--comp-seed", type=int, default=1,
                    help="1 with --comps 12 reproduces the archived comp set")
    ap.add_argument("--metric", default="success", choices=METRICS)
    ap.add_argument("--bootstrap", type=int, default=10000)
    ap.add_argument("--self-check", action="store_true",
                    help="curated as BOTH arms; every delta must be 0.0")
    args = ap.parse_args()

    base = ExpeditionSequenceEnv(sampler_seed=None)
    env = ActionMasker(base, mask_fn)
    store = base.store
    model = MaskablePPO.load(args.model, env=env, device="cpu")

    scenarios = build_scenarios(store, args.comps, args.comp_seed, args.episodes)
    print(f"model: {args.model}")
    print(f"A = curated loadout, B = "
          f"{'curated (SELF-CHECK)' if args.self_check else 'random legal 4-of-7'}")
    print(f"{len(scenarios)} paired scenarios ({args.comps} comps x "
          f"{len(EXPEDITION_LENGTH_BANDS)} bands x {args.episodes} episodes)\n")

    rows = []
    for i, sc in enumerate(scenarios, 1):
        curated = resolve_skill_ids(store, sc.comp_name, list(sc.hero_ids))
        other = curated if args.self_check else random_loadout(
            store, list(sc.hero_ids), sc.ep_seed)
        rows.append((sc, play(env, model, store, sc, curated),
                     play(env, model, store, sc, other)))
        if i % 20 == 0 or i == len(scenarios):
            print(f"  {i}/{len(scenarios)}")

    print(f"\n  {'metric':<16s} {'curated':>9s} {'random':>9s} {'delta':>9s}  95% CI")
    for m in METRICS:
        av = [float(getattr(oa, m)) for _s, oa, _o in rows]
        bv = [float(getattr(ob, m)) for _s, _o, ob in rows]
        deltas = [b - a for a, b in zip(av, bv)]
        lo, hi = bootstrap_ci(deltas, iters=args.bootstrap)
        scale = 100.0 if m in RATE_METRICS else 1.0
        unit = "pp" if m in RATE_METRICS else ""
        print(f"  {m:<16s} {statistics.mean(av) * scale:9.2f} "
              f"{statistics.mean(bv) * scale:9.2f} "
              f"{statistics.mean(deltas) * scale:+8.2f}{unit} "
              f" [{lo * scale:+.2f}, {hi * scale:+.2f}]"
              + ("   <-- must be 0.00" if args.self_check else ""))

    m = args.metric
    b = sum(1 for _s, oa, ob in rows if getattr(oa, m) and not getattr(ob, m))
    c = sum(1 for _s, oa, ob in rows if getattr(ob, m) and not getattr(oa, m))
    print(f"\n  {m}: curated-only {b}, random-only {c}, "
          f"tied {len(rows) - b - c} of {len(rows)}")
    print(f"  exact McNemar p = {mcnemar_exact(b, c):.4f}")
    print("\n  A large drop justifies the retrain. A small one says the policy "
          "already\n  generalises and the compute would buy noise -- report it "
          "and do not train.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
