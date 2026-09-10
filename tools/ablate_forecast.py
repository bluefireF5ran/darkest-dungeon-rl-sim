"""Did the policy actually LEARN to use the threat forecast?

The falsifier, committed before the run that it grades. `threat_forecast` gave
the policy seven numbers it never had -- per hero, incoming damage over current
HP, over max HP, incoming stress and P(targeted); plus three party-level
globals including SECURED. The argument for them is good. So was the argument
for the mechanics-not-identity rewrite and for the hero capability profile,
and both of those came back at +46.9pp and +46.6pp, i.e. changed nothing.

So: evaluate ONE checkpoint twice on identical scenarios, once normally and
once with exactly those features zeroed.

  a real drop   the policy is reading them. The magnitude is NOT the value of
                the feature -- zeroing is off-distribution, and a policy can
                be hurt by nonsense input it would not have been helped by.
  no drop       it learned to ignore them, and the line closes for the price
                of one training run rather than three.

Indices are DERIVED from the layout constants, never written down. Sizing an
observation block by hand is exactly how the enemy slot once landed 6 short.

    python tools/ablate_forecast.py --model models/forecast_20260825.zip
    python tools/ablate_forecast.py --model <ckpt> --episodes 4
"""
import argparse
import statistics as st
import sys
from math import comb
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
# sb3_contrib/stable_baselines3 are imported LAZILY, inside the functions that
# need them. They drag torch in, and torch is ~454 MB resident PER PROCESS --
# and every xdist worker COLLECTS the whole suite, so one module-level import
# here cost 454 MB x 16 workers. Collection went 626 MB -> 92 MB. See pytest.ini.

from env.gym_env import (HERO_SLOT_SCALARS, HERO_THREAT_FORECAST, RANKS)
from env.expedition_env import build_comp_pool
from env.expedition_sequence_env import (EXPEDITION_CONTEXT_FEATURES,
                                         EXPEDITION_LENGTH_BANDS,
                                         ExpeditionSequenceEnv)
from eval_expedition import (_cell_scenario, _run_quality,
                             resolve_skill_ids)

# Three party-level globals were appended after the base globals and before
# the expedition context block.
PARTY_FORECAST_GLOBALS = 3


def forecast_indices(width: int) -> np.ndarray:
    """Every observation index the forecast writes to, derived from layout."""
    idx = []
    for rank in range(RANKS):
        end = (rank + 1) * HERO_SLOT_SCALARS
        idx.extend(range(end - HERO_THREAT_FORECAST, end))
    g_end = width - EXPEDITION_CONTEXT_FEATURES
    idx.extend(range(g_end - PARTY_FORECAST_GLOBALS, g_end))
    return np.array(sorted(idx), dtype=np.int64)


def play(env, model, comp, zone, hero_ids, tier, band, n_c, seed, blind_idx):
    # The comp's REAL 4-of-7 loadout, exactly as eval_expedition resolves it.
    # Omitting this ran every comp on its full 7-skill movepool -- a different
    # and easier distribution than the eval measures, and the same restricted-
    # vs-full distinction `_hero_capabilities` turns on.
    from sb3_contrib.common.maskable.utils import get_action_masks
    opts = {
        "hero_ids": hero_ids, "comp_name": comp, "zone_id": zone,
        "tier": tier, "length_band": band, "num_combats": n_c,
        "sampler_seed": seed}
    skills = resolve_skill_ids(env.store, comp, hero_ids)
    if skills is not None:
        opts["hero_skill_ids"] = skills
    obs, _ = env.reset(seed=seed, options=opts)
    term = trunc = False
    steps = 0
    while not (term or trunc) and steps < 3000:
        steps += 1
        if blind_idx is not None:
            obs = obs.copy()
            obs[blind_idx] = 0.0
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


def main() -> None:
    from sb3_contrib import MaskablePPO
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--episodes", type=int, default=2)
    ap.add_argument("--bands", default="short,medium,long")
    args = ap.parse_args()

    env = ExpeditionSequenceEnv(sampler_seed=None)
    model = MaskablePPO.load(args.model, device="cpu")
    width = env.observation_space.shape[0]
    blind = forecast_indices(width)

    pool = sorted(build_comp_pool(env.store), key=lambda c: c[0])
    bands = [b.strip() for b in args.bands.split(",") if b.strip()]

    print(f"model={args.model}   obs width {width}")
    print(f"  zeroing {len(blind)} indices: {RANKS} hero slots x "
          f"{HERO_THREAT_FORECAST} + {PARTY_FORECAST_GLOBALS} globals")
    print(f"  {len(pool)} comps x {len(bands)} bands x {args.episodes} ep, paired")
    print()

    rows = {}
    for band in bands:
        lo, hi = EXPEDITION_LENGTH_BANDS[band]
        seen, blindd = [], []
        for comp, zone, hero_ids in pool:
            for ep in range(args.episodes):
                tier, n_c, seed = _cell_scenario(comp, band, ep, lo, hi)
                a = play(env, model, comp, zone, hero_ids, tier, band, n_c,
                         seed, None)
                b = play(env, model, comp, zone, hero_ids, tier, band, n_c,
                         seed, blind)
                seen.append(a)
                blindd.append(b)
        rows[band] = (seen, blindd)

    def ci(xs):
        return 1.96 * st.stdev(xs) / (len(xs) ** 0.5) if len(xs) > 1 else 0.0

    def mcnemar(seen, blindd):
        """Exact two-sided McNemar on SUCCESS -- the right test for a paired
        BINARY outcome, and the one this project uses in ab_compare and
        agreement. The first version of this harness put a CI on quality only
        and left success as a bare point estimate, which is precisely the
        column the signal turned out to be in.
        """
        a = sum(1 for x, y in zip(seen, blindd) if x["success"] and not y["success"])
        b = sum(1 for x, y in zip(seen, blindd) if y["success"] and not x["success"])
        n = a + b
        if n == 0:
            return a, b, 1.0
        # Exact binomial: P(|X - n/2| >= |a - n/2|) under p=0.5.
        k = min(a, b)
        tail = sum(comb(n, i) for i in range(0, k + 1)) / (2.0 ** n)
        return a, b, min(1.0, 2.0 * tail)

    print(f"  {'band':>7s} {'n':>5s} | {'succ':>7s} {'blind':>7s} {'d':>7s}"
          f" | {'qual':>6s} {'blind':>6s} {'d':>7s} {'95% CI':>15s}")
    for band in bands:
        seen, blindd = rows[band]
        n = len(seen)
        s1 = sum(x["success"] for x in seen) / n * 100
        s2 = sum(x["success"] for x in blindd) / n * 100
        q1 = st.mean(x["quality"] for x in seen)
        q2 = st.mean(x["quality"] for x in blindd)
        pair = [a["quality"] - b["quality"] for a, b in zip(seen, blindd)]
        d, h = st.mean(pair), ci(pair)
        print(f"  {band:>7s} {n:5d} | {s1:6.1f}% {s2:6.1f}% {s1-s2:+6.1f}pp"
              f" | {q1:6.1f} {q2:6.1f} {d:+7.2f} [{d-h:+6.2f},{d+h:+6.2f}]")

    print()
    print("  SUCCESS is a paired BINARY outcome, so it gets McNemar rather than")
    print("  a mean. Only the discordant pairs carry information.")
    print(f"  {'band':>7s} {'sighted only':>13s} {'blind only':>11s} "
          f"{'tied':>6s} {'p':>8s}")
    all_seen, all_blind = [], []
    for band in bands:
        seen, blindd = rows[band]
        all_seen += seen
        all_blind += blindd
        a, b, p = mcnemar(seen, blindd)
        print(f"  {band:>7s} {a:13d} {b:11d} {len(seen)-a-b:6d} {p:8.4f}")
    a, b, p = mcnemar(all_seen, all_blind)
    print(f"  {'POOLED':>7s} {a:13d} {b:11d} {len(all_seen)-a-b:6d} {p:8.4f}")

    allp = [a2["quality"] - b2["quality"]
            for band in bands for a2, b2 in zip(*rows[band])]
    d, h = st.mean(allp), ci(allp)
    print()
    print(f"  overall quality delta {d:+.2f}  95% CI [{d-h:+.2f}, {d+h:+.2f}]")
    print()
    if p < 0.05 or d - h > 0:
        print("  READ: blinding HURT the policy, so it is reading the forecast.")
        print(f"  (pooled McNemar p={p:.4f})")
        print("  The magnitude is NOT the feature's value -- zeroing is")
        print("  off-distribution, and a policy can be hurt by nonsense input it")
        print("  would not have been helped by.")
    else:
        print("  READ: blinding changed nothing the interval can resolve. The")
        print("  policy learned to IGNORE the forecast, and adding more of the")
        print("  same kind of information is not the lever. This is the result")
        print("  the harness exists to be able to report.")


if __name__ == "__main__":
    main()
