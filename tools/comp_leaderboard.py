"""Score every comp Fran plays, on the shipped policy, on one scenario.

Two jobs, and the second is the one that decides whether the comp search is
worth running at all.

**1. The bar.** `tools/comp_ga.py` emits proposals; a proposal is only
interesting if it beats what already exists. This is what "already exists"
scores. It is deliberately PAIRED with the search: same `Scenario`, same
`--seed`, so a curated comp and a GA candidate at the same seed face the
identical dungeon list and the difference between them is not two independent
estimates of two different dungeons.

**2. A validity check on the fitness.** These are comps Fran has played in the
real game and vouches for. If they do not beat comps drawn at random from the
legal space, the fitness is not measuring what makes a comp good, and no amount
of search will fix that -- so `--random-control` scores a matched sample of
random legal genomes on the same scenario and the same seed, and the verdict is
printed either way. That check is cheap and it is the whole reason to run this
before the search rather than after it.

It changes no file under `metadata/`.

    python tools/comp_leaderboard.py --model models/dd_combat_ppo_full_expedition.zip \
        --tier 3 --band long --episodes 16 --workers 12 --seed 0

The scenario is not guessed. Run `tools/comp_ga.py --scenario-probe` first and
pass the tier/band it names: on a corner the policy always wins or always
loses, every comp scores the same and the table is noise in rank order.
"""
import argparse
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.comp_ga import (CSV_DIR, GAME_DIR, CompSpace, Evaluator, Scenario,
                           _genome_from_json, genome_from_loadout)
from env.expedition_env import (CSV_ZONE_TO_ZONE_ID, TIER_TO_HERO_LEVEL,
                                build_comp_pool)
from env.expedition_sequence_env import EXPEDITION_LENGTH_BANDS


def _resolve_pool(store):
    """(name, zone, genome) for every comp the training pool contains.

    `build_comp_pool` is the single source -- the same one `ExpeditionEnv` and
    `eval_expedition` use -- so this table describes the population the model
    was actually trained on, and a comp dropped into `curated_comps/` appears
    here with no other step.

    A comp is resolved through `comp_loadout_by_name`, never by indexing
    `comp_loadouts`: comps get renamed wholesale (the 2026-08-26 re-export
    replaced most of the folder) and the alias is the only thing that survives.
    """
    rows, unresolved = [], []
    for name, zone, _heroes in build_comp_pool(store):
        loadout = store.comp_loadout_by_name(name)
        genome = genome_from_loadout(loadout, store) if loadout else None
        if genome is None:
            unresolved.append(name)
            continue
        rows.append((name, zone, genome))
    return rows, unresolved


def _extra_comps(store, args, space):
    """Comp JSONs from outside the pool -- `GA_comps/`, typically.

    Read through `_genome_from_json`, the same reader `--explain` uses, so a
    proposal is scored exactly as written: its own skills, trinkets, quirks and
    formation, not a re-kit. They are scored on the SAME scenario and the SAME
    seed as the curated pool, which is the only thing that makes "it beats the
    bar" a paired statement rather than two independent estimates.

    They are kept out of the curated statistics and out of the validity check.
    A proposal is what is being MEASURED against that population; folding it in
    would move the bar it is being measured against.
    """
    out = []
    for pattern in args.extra:
        for path in sorted(Path().glob(pattern)):
            data = json.loads(path.read_text(encoding="utf-8"))
            genome = _genome_from_json(data, store, space)
            if genome is None:
                print(f"  could not read a comp out of {path}")
                continue
            # A SHORT GENOME IS REFUSED, NEVER SCORED.
            #
            # An unresolvable `heroClass` drops its slot silently, and the comp
            # is then scored as a three-hero party -- which is not a low score,
            # it is a different comp. `GA sibyl1 01` came back at 36.2 here
            # while the search that emitted it scored 86.1, because the emitter
            # wrote "Sibyl_Ms" and nothing resolved it. Both ends are fixed, and
            # this stays because the failure is silent by construction: the
            # number looks like a verdict on the comp.
            declared = sum(1 for h in data.get("heroes", []) if h.get("heroClass"))
            if declared and len(genome) < declared:
                have = {g.hero_class for g in genome}
                missing = [h["heroClass"] for h in data.get("heroes", [])
                           if h.get("heroClass")
                           and h["heroClass"] not in have
                           and space.store.heroes.get(h["heroClass"]) is None]
                print(f"  REFUSING {path.name}: {declared} heroes declared, "
                      f"{len(genome)} resolved -- unresolved class name(s) "
                      f"{missing or '?'}. Scoring it would report a "
                      f"{len(genome)}-hero party's number as this comp's.")
                continue
            out.append((data.get("teamName") or path.stem, path, genome))
    return out


def _random_control(store, args, n):
    """Random legal comps, for the validity check.

    Drawn from the SAME space the search draws from (`CompSpace.random_genome`
    with the same flags), so "curated beats random" is a statement about the
    fitness and not about two different spaces.
    """
    import random
    space = CompSpace(store, args.trinket_rarities, args.negative_quirks,
                      args.class_trinket_bias, args.synergy_bias)
    rng = random.Random(args.seed + 9973)
    return [space.random_genome(rng, args.party_size, None) for _ in range(n)]


def _score_all(evaluator, genomes, scenario, seed, label, chunk=16):
    """Scored in chunks purely so a long run prints progress."""
    out = []
    t0 = time.time()
    for i in range(0, len(genomes), chunk):
        out.extend(evaluator.score_many(genomes[i:i + chunk], scenario, seed))
        done = len(out)
        rate = (time.time() - t0) / max(done, 1)
        print(f"  {label}: {done}/{len(genomes)} "
              f"({rate * (len(genomes) - done):.0f}s left)", flush=True)
    return out


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--model", default="models/dd_combat_ppo_full_expedition.zip")
    p.add_argument("--workers", type=int, default=12)
    p.add_argument("--episodes", type=int, default=16,
                   help="episodes PER ZONE per comp (default 16, i.e. 64 total "
                        "across the four zones)")
    p.add_argument("--zones", default="crypts,warrens,weald,cove")
    p.add_argument("--tier", type=int, default=3,
                   help="from `comp_ga.py --scenario-probe`, never guessed")
    p.add_argument("--band", default="long", choices=sorted(EXPEDITION_LENGTH_BANDS))
    p.add_argument("--combats", type=int, default=None)
    p.add_argument("--resolve", type=int, default=None)
    p.add_argument("--seed", type=int, default=0,
                   help="MUST match the search's --seed for the comparison to "
                        "be paired rather than two independent estimates")
    p.add_argument("--limit", type=int, default=0, help="score only the first N comps")
    p.add_argument("--extra", action="append", default=[],
                   help="glob of comp JSONs to score ALONGSIDE the pool and "
                        "rank in the same table (e.g. "
                        "'metadata/COMPS/GA_comps/GA_strongest_*.json'). Kept "
                        "out of the curated statistics and the validity check.")
    p.add_argument("--random-control", type=int, default=16,
                   help="how many random legal comps to score as the validity "
                        "check; 0 disables it")
    p.add_argument("--out", default="archive/logs/comp_leaderboard.json")
    # Mirrors of the search's space flags, so the random control is drawn from
    # the space the search will actually explore.
    p.add_argument("--party-size", type=int, default=4)
    p.add_argument("--trinket-rarities", default=None)
    p.add_argument("--negative-quirks", action="store_true")
    p.add_argument("--class-trinket-bias", type=float, default=0.5)
    p.add_argument("--synergy-bias", type=float, default=0.25)
    args = p.parse_args(argv)

    args.zones = [z.strip() for z in args.zones.split(",") if z.strip()]
    args.trinket_rarities = ([r.strip() for r in args.trinket_rarities.split(",")
                              if r.strip()] if args.trinket_rarities else None)
    known = sorted(set(CSV_ZONE_TO_ZONE_ID.values()))
    unknown = [z for z in args.zones if z not in known]
    if unknown:
        p.error(f"unknown zone(s) {unknown}; known: {known}")
    if args.combats is None:
        lo, hi = EXPEDITION_LENGTH_BANDS[args.band]
        args.combats = (lo + hi) // 2
    if args.resolve is None:
        args.resolve = TIER_TO_HERO_LEVEL[args.tier]

    from data.store_cache import load_store_cached
    store = load_store_cached(CSV_DIR, GAME_DIR)
    rows, unresolved = _resolve_pool(store)
    if args.limit:
        rows = rows[:args.limit]

    scenario = Scenario(zones=tuple(args.zones), tier=args.tier, band=args.band,
                        num_combats=args.combats, episodes_per_zone=args.episodes,
                        resolve_level=args.resolve)
    total = len(rows) * args.episodes * len(args.zones)
    print(f"comp leaderboard | {len(rows)} comps | zones {','.join(args.zones)} "
          f"tier {args.tier} {args.band} x{args.combats} | resolve {args.resolve} "
          f"| seed {args.seed}\n  {args.episodes} episodes x {len(args.zones)} "
          f"zone(s) = {total} expeditions", flush=True)
    if unresolved:
        print(f"  {len(unresolved)} comp(s) in the pool did not resolve to a "
              f"genome and are not scored: {unresolved}")

    space = CompSpace(store, args.trinket_rarities, args.negative_quirks,
                      args.class_trinket_bias, args.synergy_bias)
    extra = _extra_comps(store, args, space) if args.extra else []
    if extra:
        print(f"  plus {len(extra)} comp(s) from outside the pool")

    evaluator = Evaluator(args.model, args.workers)
    try:
        scores = _score_all(evaluator, [g for _n, _z, g in rows], scenario,
                            args.seed, "curated")
        extra_scores = (_score_all(evaluator, [g for _n, _p, g in extra],
                                   scenario, args.seed, "extra") if extra else [])
        control = []
        if args.random_control:
            control = _score_all(evaluator,
                                 _random_control(store, args, args.random_control),
                                 scenario, args.seed, "random")
    finally:
        evaluator.close()

    ranked = sorted(zip((n for n, _z, _g in rows), (z for _n, z, _g in rows), scores),
                    key=lambda r: r[2].run_score, reverse=True)
    # One table, both populations, so a proposal's PLACE is readable rather
    # than something the reader has to work out from two sorted lists.
    merged = sorted([(n, z, sc, "") for n, z, sc in ranked]
                    + [(n, "GA", sc, " <-- proposal")
                       for (n, _p, _g), sc in zip(extra, extra_scores)],
                    key=lambda r: r[2].run_score, reverse=True)

    print(f"\n{'#':>4s} {'comp':38s} {'tag':8s} | line")
    for i, (name, zone, s, mark) in enumerate(merged, 1):
        print(f"{i:>4d} {name[:38]:38s} {zone:8s} | {s.line()}{mark}")
        print(f"{'':>4s} {'':38s} {'':8s} |   per zone: {s.zone_line()}")

    vals = [s.run_score for _n, _z, s in ranked]
    ses = [s.run_score_se for _n, _z, s in ranked]
    between = statistics.stdev(vals) if len(vals) > 1 else 0.0
    within = statistics.fmean(ses) if ses else 0.0
    print(f"\ncurated: n {len(vals)} | mean {statistics.fmean(vals) * 100:.1f} "
          f"| median {statistics.median(vals) * 100:.1f} "
          f"| min {min(vals) * 100:.1f} | max {max(vals) * 100:.1f}")
    print(f"  between-comp SD {between * 100:.1f} vs mean within-comp SE "
          f"{within * 100:.1f} -- the ruler "
          f"{'RESOLVES' if between > 2 * within else 'CANNOT RESOLVE'} "
          f"real differences between these comps at this episode count")

    verdict = None
    if control:
        cvals = [s.run_score for s in control]
        cmean = statistics.fmean(cvals)
        mean = statistics.fmean(vals)
        # Two independent samples, so the difference's SE adds in quadrature.
        se = ((statistics.stdev(vals) ** 2 / len(vals)
               + statistics.stdev(cvals) ** 2 / len(cvals)) ** 0.5
              if len(cvals) > 1 else 0.0)
        gap = mean - cmean
        beat = sum(1 for v in vals if v > cmean)
        print(f"\nVALIDITY CHECK -- curated vs {len(cvals)} random legal comps, "
              f"same scenario, same seed")
        print(f"  curated {mean * 100:.1f} | random {cmean * 100:.1f} | "
              f"gap {gap * 100:+.1f} +-{se * 100:.1f} | "
              f"{beat}/{len(vals)} curated comps beat the random mean")
        if gap > 2 * se:
            verdict = "PASS"
            print("  PASS: comps Fran vouches for outscore random legal comps by "
                  "more than twice the error. The fitness is measuring something "
                  "about comp quality.")
        else:
            verdict = "FAIL"
            print("  FAIL: curated comps are NOT distinguishable from random legal "
                  "ones on this fitness. Do not spend compute on a search until "
                  "this is understood -- the search would be optimising a ruler "
                  "that cannot tell a good comp from an arbitrary one.")

    if extra:
        # A proposal's PLACE against the population, which is the number that
        # decides whether it is worth Fran's time. Beating the median is not
        # the bar -- half his own comps do that by definition.
        print(f"\nPROPOSALS against the {len(vals)}-comp curated pool")
        for (name, path, _g), sc in sorted(zip(extra, extra_scores),
                                           key=lambda r: -r[1].run_score):
            better = sum(1 for v in vals if v < sc.run_score)
            pct = 100.0 * better / len(vals)
            print(f"  {sc.run_score * 100:5.1f} +-{sc.run_score_se * 100:4.1f} "
                  f"| beats {better:>3d}/{len(vals)} ({pct:4.1f}th pct) | "
                  f"{name} [{path.name}]")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "model": args.model,
        "scenario": {"zones": args.zones, "tier": args.tier, "band": args.band,
                     "combats": args.combats, "resolve": args.resolve,
                     "episodesPerZone": args.episodes, "seed": args.seed},
        "unresolved": unresolved,
        "curated": [{"rank": i, "name": n, "zoneTag": z, **s.as_dict()}
                    for i, (n, z, s) in enumerate(ranked, 1)],
        "extra": [{"name": n, "path": str(pth), **sc.as_dict()}
                  for (n, pth, _g), sc in zip(extra, extra_scores)],
        "randomControl": [s.as_dict() for s in control],
        "validity": verdict,
    }, indent=2), encoding="utf-8")
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
