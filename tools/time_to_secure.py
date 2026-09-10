"""When is a fight SECURED? -- the expert's own signal, as an instrument.

Fran, 2026-08-24: "The fights are mostly 'secured' by turn 4. You can stall,
you can keep playing to heal and improve that output, but most of the danger is
out of the question." His definition is explicitly NOT "I have lethal", and
explicitly not "I can perma-stun it": it is that the remaining incoming damage
is outhealable.

Why this and not the win rate: SUCCESS reads ~94% / ~90% on the short and
medium bands, so it is saturated and cannot resolve an improvement. Round-to-
secure is a PER-COMBAT quality measure that does not depend on winning or
losing at all, and it is stated in the vocabulary the expert actually uses.

Both sides are computed, not guessed:

  incoming    `CombatEngine.threat_forecast()` -- the game's own brain weights
              (46/46 against the running game) through the validated hit rule
              (155/155). Stunned and dead enemies contribute nothing.
  throughput  the party's healing per round: each living hero's best
              ally-or-self heal among the skills it ACTUALLY has equipped.

A combat is SECURED at the first round where `incoming <= throughput`.

Read the distribution, never the mean alone. If every combat secures at the
same round the metric is measuring a cap rather than the play.

    python tools/time_to_secure.py --model <ckpt> --episodes 30
    python tools/time_to_secure.py --model <ckpt> --tier 5 --band long
"""
import argparse
import statistics as st
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sb3_contrib import MaskablePPO
from sb3_contrib.common.maskable.utils import get_action_masks

from env.expedition_env import build_comp_pool
from env.expedition_sequence_env import (EXPEDITION_LENGTH_BANDS,
                                         ExpeditionSequenceEnv)

NEVER = -1


def heal_throughput(engine) -> float:
    """Party HP per round if every living hero spent its turn healing.

    The RESTRICTED loadout, not the full movepool: a Vestal who did not bring
    a heal must not count as a healer, which is the same rule
    `_hero_capabilities` follows for exactly the same reason.
    """
    total = 0.0
    for h in engine.heroes:
        if not h.is_alive():
            continue
        best = 0.0
        for sid in engine.get_hero_skill_ids(h.id):
            sd = engine.get_skill_data(sid, level=h.skill_tier)
            if sd is None or sd.heal_min is None:
                continue
            if not engine._is_ally_skill(sid):
                continue          # a lifesteal heals the attacker, not the party
            best = max(best, (float(sd.heal_min) + float(sd.heal_max)) / 2.0)
        total += best
    return total


def secure_state(engine):
    """`(incoming, throughput, worst_lethality, living_enemies)`.

    ONE threshold is a convention, and the first version of this tool picked
    the loosest one (`incoming <= throughput`) and reported a median secure
    round of 0 on every tier -- i.e. it was measuring its own bar, not the
    play, which is exactly the failure its docstring warns about.

    So report a FAMILY and let the data pick. `worst_lethality` is
    max over heroes of expected incoming / current HP: below 1.0 no hero dies
    to the average round, below 0.5 none dies to two of them.
    """
    f = engine.threat_forecast()
    incoming = sum(v["damage"] for v in f.values())
    worst = 0.0
    for h in engine.heroes:
        if not h.is_alive():
            continue
        worst = max(worst, f.get(h.id, {}).get("damage", 0.0) / max(h.hp, 1.0))
    living = sum(1 for e in engine.enemies if e.is_alive() and not e.is_corpse)
    return incoming, heal_throughput(engine), worst, living


# The threshold family. Each takes (incoming, throughput, worst_lethality,
# living_enemies) and says whether the fight counts as secured this round.
CRITERIA = {
    # The loosest: the party could out-heal the board if everyone healed.
    "outheal": lambda inc, thr, worst, live: inc <= thr,
    # Comfortably: half the board's output covered.
    "outheal2x": lambda inc, thr, worst, live: inc <= thr * 0.5,
    # DEGENERATE, and kept only so the degeneracy is visible: both report
    # 100% reached at round 0 on every tier. `threat_forecast` returns a MEAN,
    # and an average round never kills anybody -- so these measure the fact
    # that means are not tails, not the play.
    #
    # The fix is a real P(this hero dies this round) from the damage BAND and
    # the crit multiplier rather than the mean. That is the forecast's largest
    # remaining gap and it is exactly the quantity the stated objective
    # ("without anyone dying") turns on.
    "nolethal": lambda inc, thr, worst, live: worst < 1.0,
    "safe2rd": lambda inc, thr, worst, live: worst < 0.5,
    # Fran's own literal example: one enemy left that can be held on rank 1.
    "onelive": lambda inc, thr, worst, live: live <= 1,
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="models/noabandon_20260824.zip")
    ap.add_argument("--episodes", type=int, default=30)
    ap.add_argument("--tier", type=int, default=0, help="0 = sweep 1/3/5")
    ap.add_argument("--band", default="long")
    args = ap.parse_args()

    model = MaskablePPO.load(args.model, device="cpu")
    env = ExpeditionSequenceEnv(sampler_seed=0)
    pool = sorted(build_comp_pool(env.store), key=lambda c: c[0])
    lo, hi = EXPEDITION_LENGTH_BANDS[args.band]
    tiers = [args.tier] if args.tier else [1, 3, 5]

    print(f"model={args.model}  band={args.band}  {args.episodes} eps/tier")
    print()
    print("  Round a fight becomes SECURED, by criterion. One threshold is a")
    print("  convention, so read the family: if they all report the same round")
    print("  the metric is measuring its own bar rather than the play.")
    print()
    print(f"  {'tier':5s} {'combats':>8s} {'criterion':>10s} {'reached':>8s} "
          f"{'by r4':>7s} {'median':>7s} {'p90':>5s} "
          f"{'open dmg':>9s} {'heal/rd':>8s} {'stress/fight':>13s}")

    for tier in tiers:
        rounds = {k: [] for k in CRITERIA}
        opens, heals, stress_taken = [], [], []
        combats = 0
        for i in range(args.episodes):
            name, zone, hero_ids = pool[(i * 7 + tier) % len(pool)]
            n_c = (i * 13 + tier * 5) % (hi - lo + 1) + lo
            obs, _ = env.reset(seed=90_000 + i, options={
                "hero_ids": hero_ids, "comp_name": name, "zone_id": zone,
                "tier": tier, "length_band": args.band, "num_combats": n_c,
                "sampler_seed": 90_000 + i})
            term = trunc = False
            steps, cur = 0, env._combat_idx
            found = {k: None for k in CRITERIA}
            opened = None
            s0 = sum(h.stress for h in env.engine.heroes if h.is_alive())
            while not (term or trunc) and steps < 2000:
                steps += 1
                inc, thr, worst, living = secure_state(env.engine)
                if opened is None:
                    opened = inc
                    heals.append(thr)
                for key, test in CRITERIA.items():
                    if found[key] is None and test(inc, thr, worst, living):
                        found[key] = env.engine.round
                a = int(model.predict(obs, deterministic=True,
                                      action_masks=get_action_masks(env))[0])
                obs, r, term, trunc, _i = env.step(a)
                if env._combat_idx != cur:
                    combats += 1
                    for key in CRITERIA:
                        rounds[key].append(
                            found[key] if found[key] is not None else NEVER)
                    opens.append(opened or 0.0)
                    s1 = sum(h.stress for h in env.engine.heroes if h.is_alive())
                    stress_taken.append(max(0.0, s1 - s0))
                    s0, cur, opened = s1, env._combat_idx, None
                    found = {k: None for k in CRITERIA}

        for ki, key in enumerate(CRITERIA):
            rs = rounds[key]
            if not rs:
                continue
            got = [r for r in rs if r != NEVER]
            by4 = sum(1 for r in got if r <= 4) / len(rs) * 100
            sg = sorted(got)
            head = f"  {tier:<5d} {combats:8d}" if ki == 0 else " " * 15
            print(f"{head} {key:>10s} {len(got)/len(rs)*100:7.1f}% {by4:6.1f}% "
                  f"{(st.median(sg) if sg else float('nan')):7.1f} "
                  f"{(sg[min(len(sg)-1, int(0.9*len(sg)))] if sg else -1):5d}"
                  + (f" {st.mean(opens):9.2f} {st.mean(heals):8.2f} "
                     f"{st.mean(stress_taken):13.1f}" if ki == 0 else ""))
        print()

    print("  outheal    incoming <= party heal/round if EVERYONE healed. The")
    print("             loosest bar, and on its own it reported median round 0")
    print("             on every tier -- it was measuring the bar, not the play.")
    print("  outheal2x  half the board's output covered.")
    print("  nolethal   no hero dies to an AVERAGE round.")
    print("  safe2rd    no hero dies to TWO average rounds. Closest to Fran's")
    print("             'the danger is out of the question' rather than 'we are")
    print("             currently surviving'.")
    print("  onelive    his literal example: one enemy left, holdable on rank 1.")
    print()
    print("  'reached' counts fights where it EVER became true; a fight won by")
    print("  killing fast can end without it, so a low rate is not bad play.")
    print("  'by r4' is his claim that a well-played fight is secured by turn 4.")


if __name__ == "__main__":
    main()
