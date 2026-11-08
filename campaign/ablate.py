"""What kills a campaign party? Re-run the campaign's own raids with one
factor changed, same seed, and count the deaths.

    python -m campaign.ablate --campaigns 6 --weeks 40

The raids come from real simulated campaigns (`WeekLog.raid`), so the
parties are the ones the bridge's decisions actually field: their levels,
gear, stress, quirks and rolled skills. Each variant changes ONE thing:

- `asis`     -- the raid as run (must reproduce the logged deaths);
- `calm`     -- every hero at 0 stress and unafflicted;
- `kit`      -- every hero equipped with the comp's own four skills;
- `level`    -- every hero at the training env's level for the quest
                (TIER_TO_HERO_LEVEL) with the best gear for it;
- `levelonly`/`gearonly` -- the two halves of `level`; `gearonly` is the
                best gear the hero's OWN level allows;
- `trinkets` -- the comp's own trinkets (campaign heroes start with none);
- `noquirks` -- quirks removed;
- `all`      -- all of them at once (the eval's world: what the policy trained on).

Paired per raid, so the SE is of the per-raid difference.
"""
from __future__ import annotations

import argparse
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bridge.live_state import LiveHero
from campaign.expedition import ExpeditionRunner
from campaign.run import DEFAULT_MODEL
from campaign.week import Campaign

from engine.combat_engine import max_gear_rank_for_resolve
from env.expedition_env import TIER_TO_HERO_LEVEL


def gear_for(level: int) -> int:
    """The save-scale (0 = base) gear rank the training env fields a hero of
    this resolve at: the best the Blacksmith allows (GEAR_RANK_RESOLVE_THRESHOLDS)."""
    return max_gear_rank_for_resolve(level) - 1


def hero_from(rec: dict, variant: str, difficulty: int) -> LiveHero:
    h = LiveHero(guid=rec["guid"], hero_class=rec["hero_class"],
                 resolve_level=rec["resolve_level"], stress=rec["stress"],
                 weapon_rank=rec["weapon_rank"], armour_rank=rec["armour_rank"],
                 selected_skills=list(rec["selected_skills"]),
                 camping_skills=list(rec["camping_skills"]),
                 quirks=list(rec["quirks"]), trinkets=list(rec["trinkets"]),
                 affliction_id=rec["affliction_id"], virtue_id=rec.get("virtue_id"))
    every = variant == "all"
    if variant == "calm" or every:
        h.stress, h.affliction_id = 0.0, None
    if (variant == "kit" or every) and rec["comp_skills"]:
        h.selected_skills = list(rec["comp_skills"])
    if variant == "level" or every:
        # The training env's pairing: the TOP of the quest's bracket
        # (Apprentice -> 2, Veteran -> 4, Champion -> 6), best gear for it.
        h.resolve_level = max(h.resolve_level, TIER_TO_HERO_LEVEL.get(difficulty, difficulty))
        h.weapon_rank = h.armour_rank = max(h.weapon_rank, gear_for(h.resolve_level))
    if variant == "levelonly":
        h.resolve_level = max(h.resolve_level, TIER_TO_HERO_LEVEL.get(difficulty, difficulty))
    if variant == "gearonly":
        # The best gear the hero's OWN level allows -- what a Blacksmith
        # that kept up would have given them.
        h.weapon_rank = h.armour_rank = max(h.weapon_rank, gear_for(h.resolve_level))
    if (variant == "trinkets" or every) and rec.get("comp_trinkets"):
        h.trinkets = list(rec["comp_trinkets"])
    if variant == "noquirks" or every:
        h.quirks = []
    return h


VARIANTS = ("asis", "calm", "kit", "level", "levelonly", "gearonly", "trinkets",
            "noquirks", "all")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--campaigns", type=int, default=6)
    ap.add_argument("--weeks", type=int, default=40)
    ap.add_argument("--seed", type=int, default=1000)
    ap.add_argument("--model", default=str(DEFAULT_MODEL))
    args = ap.parse_args(argv)

    runner = ExpeditionRunner(args.model)
    raids = []
    for k in range(args.campaigns):
        c = Campaign(runner, seed=args.seed + k)
        for _ in range(args.weeks):
            w = c.week()
            if w.raid:
                raids.append(w.raid)
    print(f"{len(raids)} raids from {args.campaigns} campaigns")

    deaths = {v: [] for v in VARIANTS}
    for r in raids:
        for v in VARIANTS:
            party = [hero_from(h, v, r["difficulty"]) for h in r["heroes"]]
            res = runner.run(party, dungeon=r["dungeon"], difficulty=r["difficulty"],
                             length=r["length"], seed=r["seed"],
                             boss_monsters=tuple(r["boss_monsters"]))
            deaths[v].append(res.deaths)
    repro = sum(a == r["deaths"] for a, r in zip(deaths["asis"], raids))
    print(f"asis reproduces the logged deaths on {repro}/{len(raids)} raids")
    base = deaths["asis"]
    for v in VARIANTS:
        d = [x - y for x, y in zip(deaths[v], base)]
        se = statistics.stdev(d) / len(d) ** 0.5 if len(d) > 1 else float("nan")
        print(f"  {v:9s} deaths/raid {statistics.fmean(deaths[v]):5.2f}   "
              f"vs asis {statistics.fmean(d):+5.2f} +- {se:4.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
