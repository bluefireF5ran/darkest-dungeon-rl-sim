"""Does `threat_forecast` actually predict what happens?

Its two inputs are validated separately -- the brain weights 46/46 against the
running game (`tools/brain_oracle.py`) and the hit rule 155/155
(`tools/hit_oracle.py`) -- but the ASSEMBLED prediction never was. Correct
parts can still be wired into a wrong whole: a missing damage multiplier, a
target pool that does not match `_execute_enemy_skill`, or `actions_per_round`
applied in the wrong place would all leave both oracles green.

This is the end-to-end check. Before each enemy phase, record the forecast.
After it, record what the heroes actually lost. Over many rounds the mean of
the realised values must track the mean of the predicted ones.

  ratio ~1.0   calibrated
  ratio <1     the forecast OVER-predicts -- the policy is being told the board
               is more dangerous than it is
  ratio >1     the forecast UNDER-predicts, which is the worse direction: the
               feature exists so a hero at 12 HP can be seen to be in danger

Per-round variance is huge (one crit swamps a round), so read the AGGREGATE
ratio and the correlation, never a single round.

    python tools/forecast_calibration.py --model <ckpt> --episodes 20
"""
import argparse
import statistics as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sb3_contrib import MaskablePPO
from sb3_contrib.common.maskable.utils import get_action_masks

from env.expedition_env import build_comp_pool
from env.expedition_sequence_env import (EXPEDITION_LENGTH_BANDS,
                                         ExpeditionSequenceEnv)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="models/forecast_20260825.zip")
    ap.add_argument("--episodes", type=int, default=20)
    ap.add_argument("--tiers", default="1,3,5")
    ap.add_argument("--band", default="long")
    args = ap.parse_args()

    model = MaskablePPO.load(args.model, device="cpu")
    env = ExpeditionSequenceEnv(sampler_seed=0)
    pool = sorted(build_comp_pool(env.store), key=lambda c: c[0])
    lo, hi = EXPEDITION_LENGTH_BANDS[args.band]
    tiers = [int(t) for t in args.tiers.split(",")]

    print(f"model={args.model}  band={args.band}  {args.episodes} eps/tier")
    print()
    print(f"  {'tier':5s} {'rounds':>7s} {'pred dmg':>9s} {'real dmg':>9s} "
          f"{'ratio':>7s} | {'pred str':>9s} {'real str':>9s} {'ratio':>7s}")

    for tier in tiers:
        pred_d, real_d, pred_s, real_s = [], [], [], []
        for i in range(args.episodes):
            comp, zone, hero_ids = pool[(i * 7 + tier) % len(pool)]
            n_c = (i * 13 + tier * 5) % (hi - lo + 1) + lo
            seed = 90_000 + i
            obs, _ = env.reset(seed=seed, options={
                "hero_ids": hero_ids, "comp_name": comp, "zone_id": zone,
                "tier": tier, "length_band": args.band, "num_combats": n_c,
                "sampler_seed": seed})
            term = trunc = False
            steps = 0
            last_round = env.engine.round
            # Snapshot at the START of a round, compare at the next boundary.
            f = env.engine.threat_forecast()
            hp0 = {h.id: h.hp for h in env.engine.heroes}
            s0 = {h.id: h.stress for h in env.engine.heroes}
            while not (term or trunc) and steps < 2000:
                steps += 1
                a = int(model.predict(obs, deterministic=True,
                                      action_masks=get_action_masks(env))[0])
                obs, _r, term, trunc, _i = env.step(a)
                if env.engine.round != last_round:
                    lost = sum(max(0.0, hp0.get(h.id, h.hp) - h.hp)
                               for h in env.engine.heroes)
                    gained = sum(max(0.0, h.stress - s0.get(h.id, h.stress))
                                 for h in env.engine.heroes)
                    pred_d.append(sum(v["damage"] for v in f.values()))
                    real_d.append(lost)
                    pred_s.append(sum(v["stress"] for v in f.values()))
                    real_s.append(gained)
                    last_round = env.engine.round
                    f = env.engine.threat_forecast()
                    hp0 = {h.id: h.hp for h in env.engine.heroes}
                    s0 = {h.id: h.stress for h in env.engine.heroes}
        if not pred_d:
            continue
        pd, rd = st.mean(pred_d), st.mean(real_d)
        ps, rs = st.mean(pred_s), st.mean(real_s)
        print(f"  {tier:<5d} {len(pred_d):7d} {pd:9.2f} {rd:9.2f} "
              f"{(rd/pd if pd else float('nan')):7.2f} | "
              f"{ps:9.2f} {rs:9.2f} {(rs/ps if ps else float('nan')):7.2f}")

    print()
    print("  ratio = realised / predicted, aggregated. >1 means the forecast")
    print("  UNDER-predicts, which is the dangerous direction: the whole point")
    print("  of the feature is that a hero at 12 HP can be SEEN to be at risk.")
    print()
    print("  Two known reasons it should read slightly BELOW 1.0, both declared")
    print("  in threat_forecast's docstring: damage is the mean of the band so")
    print("  crits are not in it, and heroes act between enemy turns -- a kill")
    print("  or a stun removes damage the forecast had already counted.")


if __name__ == "__main__":
    main()
