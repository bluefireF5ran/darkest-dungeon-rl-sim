"""Paired comparison of two checkpoints that need DIFFERENT env settings.

`tools/ab_compare.py` plays both arms in one env. That is wrong here: a
checkpoint trained with `--blind-forecast` must be evaluated blinded, and its
sighted counterpart must not be. Running either on the other's observation
distribution is a mismatch that would corrupt the very attribution the control
arm exists to produce.

So each arm carries its own `blind` flag, and both play the SAME dungeon list —
`_cell_scenario` hashes `comp|band|episode`, and `sampler_seed` is passed
through, so the two arms face identical scenarios down to the torch level and
the monsters drawn.

Reports McNemar on SUCCESS (a paired binary outcome) and a bootstrap-free
normal CI on QUALITY. Read both: quality cannot resolve a 3pp success change at
n~300, which is how an earlier harness here nearly reported the opposite of the
truth.

    python tools/compare_arms.py \\
        --a models/forecast_20260825.zip \\
        --b models/blindctl_20260825.zip --b-blind \\
        --episodes 2

`--self-check` plays ONE model as both arms; every delta must be exactly zero
or nothing the tool prints means anything.
"""
import argparse
import random
import statistics as st
import sys
from math import comb
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sb3_contrib import MaskablePPO
from sb3_contrib.common.maskable.utils import get_action_masks

from env.expedition_env import UNRESTRICTED_HEROES, build_comp_pool
from env.expedition_sequence_env import (EXPEDITION_LENGTH_BANDS,
                                         ExpeditionSequenceEnv)
from eval_expedition import (_cell_scenario, _run_quality,
                             resolve_skill_ids)


def random_loadout(store, hero_ids, seed):
    """A random legal 4-of-7 per hero, drawn HERE rather than by the env.

    `--loadout-random-p` is unreachable from an explicit scenario: the
    `"hero_ids" in options` branch of `ExpeditionSequenceEnv.reset` takes
    `hero_skill_ids` straight from the options and never calls
    `_resolve_skill_ids`, which is where the knob lives. So every pinned-comp
    path -- eval_expedition, this tool, Rollout.evaluate -- gets the comp's own
    loadout no matter what the knob says. Verified: two different scenario
    seeds produced identical loadouts at `loadout_random_p=1.0`.

    Drawing from a seed derived from the scenario keeps both arms identical
    without touching either env's RNG.
    """
    rng = random.Random(seed ^ 0x5EED10AD)
    out = []
    for hid in hero_ids:
        if hid in UNRESTRICTED_HEROES:
            out.append([])          # all 7 always equipped; nothing to choose
            continue
        hd = store.heroes.get(hid)
        ids = [sk.id for sk in hd.skills] if hd else []
        out.append(rng.sample(ids, 4) if len(ids) >= 4 else list(ids))
    return out


def play(env, model, comp, zone, hero_ids, tier, band, n_c, seed,
         rand_loadout=0.0):
    # The comp's REAL 4-of-7 loadout, exactly as eval_expedition resolves it.
    # Omitting this ran every comp on its full 7-skill movepool -- a different
    # and easier distribution than the eval measures, and the same restricted-
    # vs-full distinction `_hero_capabilities` turns on.
    opts = {
        "hero_ids": hero_ids, "comp_name": comp, "zone_id": zone,
        "tier": tier, "length_band": band, "num_combats": n_c,
        "sampler_seed": seed}
    if rand_loadout and random.Random(seed ^ 0xC0FFEE).random() < rand_loadout:
        opts["hero_skill_ids"] = random_loadout(env.store, hero_ids, seed)
    else:
        skills = resolve_skill_ids(env.store, comp, hero_ids)
        if skills is not None:
            opts["hero_skill_ids"] = skills
    obs, _ = env.reset(seed=seed, options=opts)
    term = trunc = False
    steps = 0
    while not (term or trunc) and steps < 3000:
        steps += 1
        a = int(model.predict(obs, deterministic=True,
                              action_masks=get_action_masks(env))[0])
        obs, _r, term, trunc, _i = env.step(a)
    heroes = env.engine.heroes
    alive = [h for h in heroes if h.is_alive()]
    completed = (term and alive and not trunc
                 and not getattr(env, "_abandoned", False)
                 and getattr(env, "_combats_retreated", 0) == 0)
    return {
        "success": bool(completed) and len(alive) == len(hero_ids),
        "quality": _run_quality(heroes, len(hero_ids)),
        "deaths": len(hero_ids) - len(alive),
    }


def mcnemar(xs, ys):
    a = sum(1 for x, y in zip(xs, ys) if x["success"] and not y["success"])
    b = sum(1 for x, y in zip(xs, ys) if y["success"] and not x["success"])
    n = a + b
    if n == 0:
        return a, b, 1.0
    k = min(a, b)
    tail = sum(comb(n, i) for i in range(0, k + 1)) / (2.0 ** n)
    return a, b, min(1.0, 2.0 * tail)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--a", required=True)
    ap.add_argument("--b")
    ap.add_argument("--a-blind", action="store_true")
    ap.add_argument("--b-blind", action="store_true")
    ap.add_argument("--episodes", type=int, default=2)
    ap.add_argument("--bands", default="short,medium,long")
    ap.add_argument("--self-check", action="store_true",
                    help="Play --a as BOTH arms. Every delta must be 0.0.")
    # ROBUSTNESS MODE. The five knobs were argued for generalisation to comps
    # and kits the policy has not seen, and the standard eval cannot test that
    # -- it presents every comp with its own real loadout, which is the narrow
    # DEPLOYMENT distribution. Turning the knobs on at EVAL time asks the
    # question they were actually built for.
    #
    # Both arms get the identical randomisation because `sampler_seed` now
    # pins `_loadout_rng` as well as `_sampler_rng` (see
    # ExpeditionSequenceEnv.reset -> seed_scenario).
    ap.add_argument("--rand-loadout", type=float, default=0.0,
                    help="Evaluate with random legal 4-of-7 loadouts at this "
                         "probability, for BOTH arms.")
    ap.add_argument("--rand-kit", type=float, default=0.0,
                    help="Evaluate with random trinket pairs and quirk sheets "
                         "at this probability, for BOTH arms.")
    args = ap.parse_args()

    b_path = args.a if args.self_check else args.b
    b_blind = args.a_blind if args.self_check else args.b_blind
    if not b_path:
        sys.exit("need --b (or --self-check)")

    knobs = dict(loadout_random_p=args.rand_loadout,
                 kit_random_p=args.rand_kit)
    env_a = ExpeditionSequenceEnv(sampler_seed=None,
                                  blind_forecast=args.a_blind, **knobs)
    env_b = ExpeditionSequenceEnv(sampler_seed=None, blind_forecast=b_blind,
                                  store=env_a.store, **knobs)
    model_a = MaskablePPO.load(args.a, device="cpu")
    model_b = MaskablePPO.load(b_path, device="cpu")

    pool = sorted(build_comp_pool(env_a.store), key=lambda c: c[0])
    bands = [x.strip() for x in args.bands.split(",") if x.strip()]

    print(f"  A {args.a}  blind={args.a_blind}")
    print(f"  B {b_path}  blind={b_blind}"
          + ("   [SELF-CHECK]" if args.self_check else ""))
    print(f"  {len(pool)} comps x {len(bands)} bands x {args.episodes} ep, paired")
    if args.rand_loadout or args.rand_kit:
        print(f"  ROBUSTNESS MODE: loadout_random_p={args.rand_loadout} "
              f"kit_random_p={args.rand_kit} -- both arms, identical draws")
    print()

    rows = {}
    for band in bands:
        lo, hi = EXPEDITION_LENGTH_BANDS[band]
        A, B = [], []
        for comp, zone, hero_ids in pool:
            for ep in range(args.episodes):
                tier, n_c, seed = _cell_scenario(comp, band, ep, lo, hi)
                A.append(play(env_a, model_a, comp, zone, hero_ids, tier,
                              band, n_c, seed, args.rand_loadout))
                B.append(play(env_b, model_b, comp, zone, hero_ids, tier,
                              band, n_c, seed, args.rand_loadout))
        rows[band] = (A, B)

    def ci(xs):
        return 1.96 * st.stdev(xs) / (len(xs) ** 0.5) if len(xs) > 1 else 0.0

    print(f"  {'band':>7s} {'n':>5s} | {'A succ':>7s} {'B succ':>7s} {'d':>8s}"
          f" | {'A qual':>7s} {'B qual':>7s} {'d':>7s} {'95% CI':>16s}")
    allA, allB = [], []
    for band in bands:
        A, B = rows[band]
        allA += A
        allB += B
        n = len(A)
        sa = sum(x["success"] for x in A) / n * 100
        sb = sum(x["success"] for x in B) / n * 100
        qa = st.mean(x["quality"] for x in A)
        qb = st.mean(x["quality"] for x in B)
        pair = [x["quality"] - y["quality"] for x, y in zip(A, B)]
        d, h = st.mean(pair), ci(pair)
        print(f"  {band:>7s} {n:5d} | {sa:6.1f}% {sb:6.1f}% {sa-sb:+7.1f}pp"
              f" | {qa:7.1f} {qb:7.1f} {d:+7.2f} [{d-h:+6.2f},{d+h:+6.2f}]")

    print()
    print(f"  {'band':>7s} {'A only':>7s} {'B only':>7s} {'tied':>6s} {'p':>8s}")
    for band in bands:
        a, b, p = mcnemar(*rows[band])
        print(f"  {band:>7s} {a:7d} {b:7d} {len(rows[band][0])-a-b:6d} {p:8.4f}")
    a, b, p = mcnemar(allA, allB)
    print(f"  {'POOLED':>7s} {a:7d} {b:7d} {len(allA)-a-b:6d} {p:8.4f}")

    pair = [x["quality"] - y["quality"] for x, y in zip(allA, allB)]
    d, h = st.mean(pair), ci(pair)
    print()
    print(f"  overall quality delta {d:+.2f}  95% CI [{d-h:+.2f}, {d+h:+.2f}]")
    print(f"  deaths/run  A {st.mean(x['deaths'] for x in allA):.3f}   "
          f"B {st.mean(x['deaths'] for x in allB):.3f}")
    if args.self_check:
        ok = (a == 0 and b == 0 and abs(d) < 1e-9)
        print()
        print("  SELF-CHECK " + ("PASSED" if ok else "FAILED — the harness is "
                                 "not deterministic and no delta it prints "
                                 "means anything"))


if __name__ == "__main__":
    main()
