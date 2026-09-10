"""Does the policy play a class it has NEVER SEEN?

The falsifier Fran chose for the 2026-08-23 observation rewrite, which replaced
the 20-class one-hot with per-skill mechanic payloads on the argument that a
policy reading "+DODGE" instead of "Invigorating Vapors" transfers to a class it
was not trained on -- a modded class included.

**Why not a win-rate delta.** Seed-to-seed spread on SUCCESS in this project is
~1.4pp, wider than most effects it has ever measured, so "the new encoding wins
more" is unanswerable at any eval size anyone here will run. "The policy can
play a class it never met" is a different KIND of claim: a policy that has
genuinely never seen a Duelist either handles her stance kit or it does not, and
the gap against the classes it did train on is large enough to read.

    # train with the class removed
    python train.py --full-expedition --timesteps 3000000 \\
        --hold-out-class duelist --out models/heldout_duelist.zip --seed 0

    # then score it cold, against its own seen half
    python tools/transfer_eval.py --model models/heldout_duelist.zip \\
                                  --hold-out-class duelist

**Read the GAP, never the absolute rates.** The two halves are different comps
facing the same dungeons, so their absolute difficulty is not equal and the
number that means something is how a held-out checkpoint's gap compares to a
NORMALLY trained checkpoint's gap on the same split. Pass `--baseline` to get
both in one table; without it you have half an experiment.
"""
from __future__ import annotations

import argparse
import random
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bridge.rollout import PartyMember, Rollout
from data.store_cache import load_store_cached
from env.expedition_env import (CSV_ZONE_TO_ZONE_ID, TIER_TO_HERO_LEVEL,
                                UNRESTRICTED_HEROES, build_comp_pool)
from env.gym_env import CSV_DIR, GAME_DIR

# Champion, medium, four zones -- the scenario `tools/comp_ga.py` settled on
# after its four-way probe, for the same reason: tier 1 and tier 3 are ceilings
# and tier 5 long is a floor, so only tier 5 medium separates anything.
DEFAULT_TIER = 5
DEFAULT_BAND = "medium"
DEFAULT_COMBATS = 8


def party_from_loadout(loadout, store):
    """A comp's real party, positionally. Never a dict keyed by class -- 37
    curated comps field a duplicate class and a dict collapses them."""
    party = []
    for h in loadout.heroes:
        if h.hero_id not in store.heroes:
            continue
        party.append(PartyMember(
            hero_class=h.hero_id,
            resolve_level=TIER_TO_HERO_LEVEL[DEFAULT_TIER],
            skill_ids=() if h.hero_id in UNRESTRICTED_HEROES else tuple(h.skill_ids),
            trinkets=tuple(h.trinket_ids),
            quirks=tuple(h.quirk_ids.get("positive", []) if isinstance(h.quirk_ids, dict)
                         else h.quirk_ids),
        ))
    return party


def score_pool(rollout, store, pool, episodes, seed, zones):
    """Mean success over every comp in `pool`, plus the per-comp rates.

    Every comp faces the SAME dungeon list -- `Rollout.evaluate`'s scenario key
    is `zone|tier|combats|band|seed` and deliberately does not mention the
    party -- so the comparison between the two halves is paired on scenario
    even though it cannot be paired on comp.
    """
    rates = []
    for name, zone_id, _hero_ids in pool:
        loadout = store.comp_loadout_by_name(name)
        if loadout is None:
            continue
        party = party_from_loadout(loadout, store)
        if len(party) < 2:
            continue
        use_zones = zones or [zone_id]
        per_zone = []
        for z in use_zones:
            res = rollout.evaluate(party, zone_id=z, tier=DEFAULT_TIER,
                                   num_combats=DEFAULT_COMBATS,
                                   length_band=DEFAULT_BAND,
                                   episodes=episodes, seed=seed)
            per_zone.append(res.success_rate)
        rates.append((name, sum(per_zone) / len(per_zone)))
    return rates


def summarise(label, rates):
    if not rates:
        return f"  {label:26s}   (no comps)", 0.0
    vals = [r for _n, r in rates]
    mean = sum(vals) / len(vals)
    sd = statistics.stdev(vals) if len(vals) > 1 else 0.0
    se = sd / (len(vals) ** 0.5) if vals else 0.0
    return (f"  {label:26s} {mean * 100:6.1f}%  +-{se * 100:4.1f}  "
            f"({len(vals)} comps)"), mean


def run_arm(model_path, store, held, seen_pool, held_pool, args):
    rollout = Rollout(model_path)
    try:
        zones = list(CSV_ZONE_TO_ZONE_ID.values()) if args.all_zones else None
        seen = score_pool(rollout, store, seen_pool, args.episodes, args.seed, zones)
        transfer = score_pool(rollout, store, held_pool, args.episodes, args.seed, zones)
    finally:
        close = getattr(rollout, "close", None)
        if callable(close):
            close()
    return seen, transfer


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", required=True,
                    help="the checkpoint trained WITH --hold-out-class")
    ap.add_argument("--hold-out-class", required=True,
                    help="the class that checkpoint never saw")
    ap.add_argument("--baseline", default=None,
                    help="a NORMALLY trained checkpoint. Without it you have "
                         "half an experiment: the two comp halves are not "
                         "equally hard, so only the difference of the two "
                         "gaps means anything.")
    ap.add_argument("--episodes", type=int, default=20,
                    help="episodes per comp per zone (default 20). 40 ranks "
                         "candidates; it does not measure a rate -- quote the "
                         "standard error with anything you report.")
    ap.add_argument("--seed", type=int, default=7,
                    help="scenario seed. Both arms and both halves share it, "
                         "which is what makes them comparable.")
    ap.add_argument("--all-zones", action="store_true",
                    help="score every comp in all four zones instead of its "
                         "own tagged zone. 4x the cost, and removes zone "
                         "difficulty as a confound between the two halves.")
    ap.add_argument("--limit", type=int, default=None,
                    help="cap the SEEN half to this many comps; the held-out "
                         "half is always scored in full. The halves are very "
                         "uneven (duelist: 136 vs 5), so this is how you keep "
                         "a probe run affordable.")
    args = ap.parse_args()

    store = load_store_cached(CSV_DIR, GAME_DIR)
    held = frozenset({args.hold_out_class})
    if args.hold_out_class not in store.heroes:
        raise SystemExit(f"{args.hold_out_class!r} is not a hero class")

    seen_pool = build_comp_pool(store, hold_out_classes=held)
    held_pool = build_comp_pool(store, only_classes=held)
    if not held_pool:
        raise SystemExit(f"no comp fields a {args.hold_out_class} -- nothing to transfer to")
    if args.limit:
        seen_pool = seen_pool[:args.limit]

    print(f"held out: {args.hold_out_class}   "
          f"seen {len(seen_pool)} comps / transfer {len(held_pool)} comps   "
          f"tier {DEFAULT_TIER} {DEFAULT_BAND} x{DEFAULT_COMBATS}, "
          f"{args.episodes} episodes, seed {args.seed}\n")

    arms = [("held-out ckpt", args.model)]
    if args.baseline:
        arms.append(("baseline ckpt", args.baseline))

    gaps, per_comp = {}, {}
    for label, path in arms:
        seen, transfer = run_arm(path, store, held, seen_pool, held_pool, args)
        line_a, mean_seen = summarise(f"{label}: seen classes", seen)
        line_b, mean_tx = summarise(f"{label}: {args.hold_out_class}", transfer)
        print(line_a)
        print(line_b)
        gaps[label] = mean_seen - mean_tx
        per_comp[label] = (dict(seen), dict(transfer))
        print(f"  {'gap':26s} {gaps[label] * 100:+6.1f}pp")
        print()

    if len(gaps) != 2:
        print("  No --baseline given, so this is half an experiment: the two comp")
        print("  halves are not equally hard and the gap above conflates that with")
        print("  the transfer effect. Re-run with --baseline before quoting it.")
        return 0

    # PAIRED, and this is the only reading worth quoting. Both arms faced the
    # identical comps on the identical dungeon lists, so differencing per comp
    # removes comp DIFFICULTY -- which is most of the variance here. The
    # held-out class's comps turned out to be EASIER than the seen half for
    # both arms, so the unpaired gaps above are largely measuring that.
    # Unpaired, the interval on the excess gap is about +-13pp and the
    # experiment answers nothing at all.
    h_seen, h_tx = per_comp["held-out ckpt"]
    b_seen, b_tx = per_comp["baseline ckpt"]
    d_seen = [h_seen[n] - b_seen[n] for n in h_seen if n in b_seen]
    d_tx = [h_tx[n] - b_tx[n] for n in h_tx if n in b_tx]
    if not d_seen or not d_tx:
        print("  the two arms did not face the same comps -- cannot pair")
        return 1

    point = (sum(d_seen) / len(d_seen)) - (sum(d_tx) / len(d_tx))
    rng = random.Random(0)
    boots = []
    for _ in range(10000):
        a = [rng.choice(d_seen) for _ in d_seen]
        b = [rng.choice(d_tx) for _ in d_tx]
        boots.append(sum(a) / len(a) - sum(b) / len(b))
    boots.sort()
    lo, hi = boots[int(0.025 * len(boots))], boots[int(0.975 * len(boots))]

    print(f"  PAIRED excess gap: {point * 100:+.1f}pp   "
          f"95% CI [{lo * 100:+.1f}, {hi * 100:+.1f}]   "
          f"({len(d_seen)} seen comps, {len(d_tx)} held-out comps)")
    print(f"  per-comp deltas on the held-out class: "
          f"{', '.join(f'{d * 100:+.0f}' for d in d_tx)}")
    print()
    if lo <= 0.0 <= hi:
        print("  The interval spans zero, so this CANNOT distinguish a policy")
        print("  that transfers from one that does not. Report it that way --")
        print(f"  with {len(d_tx)} held-out comps, that may be all this split")
        print("  can ever say, however many episodes are thrown at it.")
    elif point > 0:
        # The first half of this is what the measurement supports. The second
        # half is what it does NOT, and the tool used to print it anyway:
        # "and the class one-hot was doing real work after all". A held-out
        # class's one-hot index is zero for the ENTIRE training run, so at eval
        # it fires for the first time on a weight the network never trained.
        # Identity cannot carry information about a class that was never in
        # the data -- it can only be worse than a mechanic encoding here, never
        # better. Anyone reading this line was being pointed at a repair that
        # cannot work.
        print("  The held-out policy is genuinely WORSE on the class it never")
        print("  saw. The mechanic encoding did not transfer.")
        print()
        print("  This does NOT license putting the class one-hot back. A")
        print("  held-out class's one-hot index is 0 for the whole training")
        print("  run, so identity carries nothing about it either -- and fires")
        print("  an untrained input at eval on top. Whatever fixes zero-shot")
        print("  transfer, it is not identity.")
    else:
        print("  The held-out policy is no worse on a class it never saw.")
        print("  That is the claim landing.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
