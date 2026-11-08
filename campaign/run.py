"""Run N simulated campaigns and print the ruler.

    python -m campaign.run --campaigns 20 --weeks 60
    python -m campaign.run --campaigns 1 --weeks 30 --log      # one, week by week

The ruler is Fran's own campaign (profile_8, audited 2026-09-28): Veteran
from week 20, Champion from week 35, 16 plot/boss quests by week 54, 8 dead.
"Veteran"/"Champion" here = the first week a hero reaches resolve level 3/5.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from campaign.expedition import ExpeditionRunner
from campaign.week import Campaign
from data.paths import REPO_ROOT

DEFAULT_MODEL = REPO_ROOT / "models" / "dd_combat_ppo_full_expedition.zip"
FRAN = {"veteran_week": 20, "champion_week": 35, "plots_by_54": 16, "dead_by_54": 8}


def summarise(c: Campaign) -> dict:
    log, s = c.log, c.state
    first = lambda lv: next((w.week for w in log if any(            # noqa: E731
        u.endswith(f"->{lv}") for u in w.level_ups)), None)
    raids = [w for w in log if w.party]
    return {
        "seed": c.seed,
        "weeks": len(log),
        "raids": len(raids),
        "success_rate": (sum(bool(w.success) for w in raids) / len(raids)) if raids else 0.0,
        "dead": len(s.dead),
        "wipes": sum(len(w.deaths) == len(w.party) and bool(w.party) for w in raids),
        "plots": len(s.completed_plots) - 1,          # minus the tutorial
        "veteran_week": first(3),
        "champion_week": first(5),
        "max_level": max((h.resolve_level for h in s.heroes), default=0),
        "gold_per_raid": statistics.fmean(w.gold_reward + w.loot_gold for w in raids) if raids else 0.0,
        "gold_end": int(s.wallet.get("gold", 0)),
        "roster_end": len(s.heroes),
        "upgrades": sum(n for (inst, _t), n in s.ranks.items() if inst == 0),
        "trinkets_per_raid": statistics.fmean(w.trinkets_equipped for w in raids) if raids else 0.0,
        "gear_gold": sum(w.gear_bought for w in raids),
        # embarks whose provisions were cut to fit the wallet
        "quirks_gained": sum(len(w.quirks_gained) for w in log),
        "diseases_end": sum(1 for h in s.heroes for q in h.quirks
                            if q in c.store.quirks and c.store.quirks[q].is_disease),
        "negative_end": sum(1 for h in s.heroes for q in h.quirks
                            if q in c.store.quirks and not c.store.quirks[q].is_positive
                            and not c.store.quirks[q].is_disease),
        "basket_cuts": sum(any("basket cut" in t for t in w.notes) for w in raids),
        "sanitarium": sum(len(w.sanitarium) for w in log),
        "sanitarium_gold": sum(v[2] for w in log for v in w.sanitarium),
        **{f"raids_{b}": sum(w.branch == b for w in raids) for b in ("comp", "partial", "none")},
        **{f"deaths_{b}": sum(len(w.deaths) for w in raids if w.branch == b)
           for b in ("comp", "partial", "none")},
    }


def _set_knobs(kit_aware=True, unlock=None, margin=0, coach_first=False,
               discount_cap=None, gates=True, quest_quirks=True, max_underleveled=1,
               max_gap=None, sanitarium=True) -> None:
    """The decide layers' experiment knobs, set the same way in a worker and
    in the serial path. Every default is the shipped behaviour."""
    import bridge.embarkplan as embarkplan
    import bridge.townplan as townplan
    embarkplan.KIT_AWARE = kit_aware
    if unlock is not None:
        embarkplan.UNLOCK_GOLD_FRACTION = unlock
    embarkplan.BOSS_LEVEL_MARGIN = margin
    embarkplan.MAX_UNDERLEVELED = max_underleveled
    embarkplan.UNDERLEVEL_MAX_GAP = max_gap
    # Smith-first is shipped (Fran, 2026-10-12); this restores the v8 order,
    # the Stage Coach ahead of the Guild and the Blacksmith.
    townplan.UPGRADE_PRIORITY = (townplan.COACH_FIRST_PRIORITY if coach_first
                                 else townplan.SHIPPED_UPGRADE_PRIORITY)
    townplan.BUILDING_GATES = gates
    if discount_cap is not None:
        townplan.SERVICE_DISCOUNT_MAX_RANKS = discount_cap or None
    townplan.SANITARIUM = sanitarium
    import campaign.quirks as quirks
    quirks.QUEST_QUIRKS = quest_quirks


def _one(job) -> dict:
    """One campaign in a worker process. The runner (env + policy) is built
    once per process and cached on the function."""
    model, seed, weeks, veto, gear, knobs = job
    _set_knobs(**knobs)
    if getattr(_one, "runner", None) is None:
        _one.runner = ExpeditionRunner(model)
        _one.veto = None
        if veto:
            from bridge.embark_veto import make_veto
            _one.veto = make_veto(model, say=lambda *_a, **_k: None)
    c = Campaign(_one.runner, seed=seed, veto=_one.veto, gear_budget=gear)
    for _ in range(weeks):
        c.week()
    return summarise(c)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--campaigns", type=int, default=10)
    ap.add_argument("--weeks", type=int, default=60)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--model", default=str(DEFAULT_MODEL))
    ap.add_argument("--log", action="store_true", help="print every week")
    ap.add_argument("--jsonl", default=None, help="write one summary per line here")
    ap.add_argument("--veto", action="store_true",
                    help="pass bridge.embark_veto, as the live town does")
    ap.add_argument("--no-kit-aware", action="store_true",
                    help="embarkplan.KIT_AWARE off: the pre-2026-10-10 comp draw")
    ap.add_argument("--gear-budget", type=float, default=0.0,
                    help="EXPERIMENT: buy the party's gear with up to this fraction of gold")
    ap.add_argument("--boss-margin", type=int, default=0,
                    help="EXPERIMENT: embarkplan.BOSS_LEVEL_MARGIN (0 = Fran's rule)")
    ap.add_argument("--max-underleveled", type=int, default=1,
                    help="EXPERIMENT: embarkplan.MAX_UNDERLEVELED (1 = Fran's rule)")
    ap.add_argument("--no-sanitarium", action="store_true",
                    help="EXPERIMENT: the town before Fran's Sanitarium ruling (2026-10-12)")
    ap.add_argument("--max-gap", type=int, default=None,
                    help="EXPERIMENT: embarkplan.UNDERLEVEL_MAX_GAP (none = Fran's rule as coded)")
    ap.add_argument("--unlock-fraction", type=float, default=None,
                    help="EXPERIMENT: embarkplan.UNLOCK_GOLD_FRACTION (Fran's cap, 0.10)")
    ap.add_argument("--coach-first", action="store_true",
                    help="EXPERIMENT: the v8 order, Stage Coach ahead of Guild/Blacksmith")
    ap.add_argument("--no-gates", action="store_true",
                    help="EXPERIMENT: every building open from week 1 (the world before E23)")
    ap.add_argument("--discount-cap", type=int, default=None,
                    help="EXPERIMENT: townplan.SERVICE_DISCOUNT_MAX_RANKS (0 = no cap)")
    ap.add_argument("--no-quest-quirks", action="store_true",
                    help="no quest-end quirk or disease rolls (the pre-2026-10-11 world)")
    ap.add_argument("--workers", type=int, default=1,
                    help="campaigns in parallel processes (each loads the policy)")
    args = ap.parse_args(argv)

    knobs = dict(kit_aware=not args.no_kit_aware, unlock=args.unlock_fraction,
                 margin=args.boss_margin, coach_first=args.coach_first,
                 gates=not args.no_gates,
                 discount_cap=args.discount_cap, quest_quirks=not args.no_quest_quirks,
                 max_underleveled=args.max_underleveled, max_gap=args.max_gap,
                 sanitarium=not args.no_sanitarium)
    rows = []
    t0 = time.time()
    if args.workers > 1 and not args.log:
        import multiprocessing as mp
        jobs = [(args.model, args.seed + k, args.weeks, args.veto, args.gear_budget, knobs)
                for k in range(args.campaigns)]
        with mp.get_context("spawn").Pool(args.workers) as pool:
            for row in pool.imap_unordered(_one, jobs):
                rows.append(row)
                print(json.dumps(row), flush=True)
                if args.jsonl:
                    with open(args.jsonl, "a", encoding="utf-8") as fh:
                        fh.write(json.dumps(row) + "\n")
        _report(rows, args, t0)
        return 0

    _set_knobs(**knobs)
    runner = ExpeditionRunner(args.model)
    veto = None
    if args.veto:
        from bridge.embark_veto import make_veto
        veto = make_veto(args.model, say=lambda *_a, **_k: None)
    for k in range(args.campaigns):
        c = Campaign(runner, seed=args.seed + k, veto=veto, gear_budget=args.gear_budget)
        for _ in range(args.weeks):
            w = c.week()
            if args.log:
                print(f"w{w.week:3d} {w.quest[:38]:38s} {w.plot_id[:22]:22s} "
                      f"{'ok ' if w.success else 'FAIL' if w.success is False else '-  '} "
                      f"dead={len(w.deaths)} gold={w.gold_after:6d} roster={w.roster:2d} "
                      f"{' '.join(w.level_ups)}")
        row = summarise(c)
        rows.append(row)
        print(json.dumps(row))
        if args.jsonl:
            with open(args.jsonl, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(row) + "\n")

    _report(rows, args, t0)
    return 0


def _report(rows, args, t0) -> None:
    def stats(key):
        vals = [r[key] for r in rows if r[key] is not None]
        if not vals:
            return float("nan"), float("nan"), 0
        se = statistics.stdev(vals) / len(vals) ** 0.5 if len(vals) > 1 else float("nan")
        return statistics.fmean(vals), se, len(vals)

    print(f"\n{len(rows)} campaigns x {args.weeks} weeks in {time.time() - t0:.0f}s")
    for key in ("success_rate", "dead", "wipes", "plots", "max_level", "gold_per_raid",
                "veteran_week", "champion_week",
                "raids_comp", "raids_partial", "raids_none",
                "deaths_comp", "deaths_partial", "deaths_none"):
        m, se, n = stats(key)
        print(f"  {key:15s} {m:9.2f} +- {se:6.2f}   (n={n})")
    print(f"  Fran (profile_8): {FRAN}")


if __name__ == "__main__":
    raise SystemExit(main())
