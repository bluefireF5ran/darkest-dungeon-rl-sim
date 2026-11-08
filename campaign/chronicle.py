"""A campaign's chronicle: its weeks told as a story (CAMPAIGN_SIM.md, F3).

    python -m campaign.chronicle --seed 7 --weeks 60 --out chronicle_s7.md
    python -m campaign.chronicle --best archive/logs/campaign/SW_v8_g0.5_s0.jsonl

Built from the `WeekLog`s alone, so it tells exactly what the simulator did:
every raid, every death with the hero's level and how many raids they had
survived, the first Veteran and Champion, every boss, every wipe. Heroes
have no names in the simulator; they are their class and roster number
("the Crusader #12"). `--best` picks the campaign to tell from a ruler run:
fewest dead, then most plots, then the earliest Champion.

Nothing here is game text: zone, difficulty and boss labels are the
repo's own words for the ids.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ZONES = {"crypts": "the Ruins", "warrens": "the Warrens", "weald": "the Weald",
         "cove": "the Cove", "darkest_dungeon": "the Darkest Dungeon"}
TIERS = {0: "Apprentice", 1: "Apprentice", 2: "Apprentice", 3: "Veteran",
         4: "Veteran", 5: "Champion", 6: "Champion"}
LENGTHS = {1: "short", 2: "medium", 3: "long"}


def _hero(guid, hero_class) -> str:
    return f"the {hero_class.replace('_', ' ').title()} #{guid}"


def _boss(plot_id: str) -> str:
    """`plot_kill_hag_1` -> "the Hag"."""
    stem = plot_id.removeprefix("plot_kill_").rstrip("_0123456789")
    return "the " + stem.replace("_", " ").title()


def _a(phrase: str) -> str:
    return ("An " if phrase[:1] in "AEIOU" else "A ") + phrase


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:]


_VERBS = {"cure": "cures", "remove": "removes", "lock": "locks"}


def _sanitarium(visits) -> str:
    """`(action, quirk, gold, ...)` per visit -> "the Sanitarium cures
    tetanus and removes fragile". A hero guid and class, when the log
    carries them, name the patient."""
    parts = []
    for v in visits:
        action, quirk = v[0], v[1].replace("_", " ")
        who = f" from {_hero(v[3], v[4])}" if len(v) >= 5 and action != "lock" else (
            f" on {_hero(v[3], v[4])}" if len(v) >= 5 else "")
        parts.append(f"{_VERBS.get(action, action)} {quirk}{who}")
    if not parts:
        return ""
    return "the Sanitarium " + (", ".join(parts[:-1]) + " and " + parts[-1] if len(parts) > 1 else parts[0])


def pick_best(rows: list[dict]) -> dict:
    return min(rows, key=lambda r: (r["dead"], -r["plots"],
                                    r.get("champion_week") or 10**6, r["seed"]))


def chronicle(logs, seed: int) -> str:
    """Markdown: a header of milestones, then one paragraph per notable week."""
    raids_survived: Counter = Counter()
    first_level: dict = {}
    lines: list[str] = []
    dead = wipes = bosses = visits = 0
    for w in logs:
        r = w.raid or {}
        heroes = r.get("heroes", [])
        events: list[str] = []
        san = _sanitarium(getattr(w, "sanitarium", ()))
        if san:
            visits += len(getattr(w, "sanitarium", ()))
            events.append(san)
        for u in w.level_ups:
            lv = int(u.rsplit("->", 1)[1])
            if lv in (3, 5) and lv not in first_level:
                first_level[lv] = w.week
                events.append(f"the roster's first {TIERS[lv]} ({u.split('->')[0].replace('_', ' ')}, "
                              f"resolve {lv})")
        if heroes:
            where = ZONES.get(r.get("dungeon"), r.get("dungeon"))
            party = ", ".join(f"{h['hero_class'].replace('_', ' ')} {h['resolve_level']}"
                              for h in heroes)
            quest = (f"{TIERS.get(r.get('difficulty'), '?')} {LENGTHS.get(r.get('length'), '')} "
                     f"raid into {where}")
            if w.plot_id:
                quest += f" after {_boss(w.plot_id)}"
            outcome = "won" if w.success else "lost"
            if w.plot_id and w.success:
                bosses += 1
                events.append(f"{_boss(w.plot_id)} falls")
            fallen = list(w.fallen)
            if fallen and len(fallen) == len(heroes):
                wipes += 1
                events.append("**the whole party is lost**")
            for guid, cls, lv in fallen:
                dead += 1
                n = raids_survived[guid]
                events.append(f"{_hero(guid, cls)} dies at resolve {lv}"
                              + (f", after {n} raid{'s' * (n != 1)} survived" if n else
                                 ", on their first raid"))
            for h in heroes:
                if h["guid"] not in {g for g, _c, _l in fallen}:
                    raids_survived[h["guid"]] += 1
            head = f"**Week {w.week}.** {_a(quest)} ({party}): {outcome}."
        else:
            if not events:
                continue
            head = f"**Week {w.week}.** No raid."
        lines.append(head + (" " + _cap("; ".join(events)) + "." if events else ""))
    veteran, champion = first_level.get(3), first_level.get(5)
    out = [f"# Campaign {seed}: {len(logs)} weeks", "",
           "| | this campaign | Fran (profile_8) |", "|---|---|---|",
           f"| first Veteran | week {veteran or '-'} | week 20 |",
           f"| first Champion | week {champion or '-'} | week 35 |",
           f"| bosses killed | {bosses} | 16 by week 54 |",
           f"| heroes dead | {dead} ({wipes} whole part{'y' if wipes == 1 else 'ies'}) | 8 by week 54 |",
           f"| Sanitarium visits | {visits} | - |",
           "", *lines, ""]
    return "\n".join(out)


def main(argv=None) -> int:
    from campaign.run import DEFAULT_MODEL, _set_knobs
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--weeks", type=int, default=60)
    ap.add_argument("--gear-budget", type=float, default=0.5)
    ap.add_argument("--model", default=str(DEFAULT_MODEL))
    ap.add_argument("--best", help="a campaign.run --jsonl file: tell its best campaign")
    ap.add_argument("--out", help="write the Markdown here (default: stdout)")
    args = ap.parse_args(argv)
    if args.best:
        rows = [json.loads(line) for line in Path(args.best).read_text().splitlines() if line.strip()]
        args.seed = pick_best(rows)["seed"]
    from campaign.expedition import ExpeditionRunner
    from campaign.week import Campaign
    _set_knobs()
    c = Campaign(ExpeditionRunner(args.model), seed=args.seed, gear_budget=args.gear_budget)
    for _ in range(args.weeks):
        c.week()
    text = chronicle(c.log, args.seed)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8", newline="\n")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
