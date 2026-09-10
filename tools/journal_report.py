"""What did the corpus actually capture? — read `bat/advise.jsonl`.

`bridge/journal.py` writes one JSON object per event while somebody plays.
This is the other end: pool one or more of those files and say what is in them,
so a recording session can be checked WHILE it is running rather than found to
be empty afterwards.

    python tools/journal_report.py                      # bat/advise.jsonl
    python tools/journal_report.py bat/play.jsonl ...   # pool several
    python tools/journal_report.py --differs            # every disagreement

## What to read it for

- **`play` is the demonstration corpus** — every combat turn the human took,
  with the board it was taken on. It does not need a checkpoint, which is the
  whole reason it is separate from `decision`.
- **`decision` is agreement** — advised against played, per layer. It needs a
  loadable checkpoint, so it is empty while the observation width and the
  checkpoints disagree.
- **The town events are the half that never existed.** `run.py` `continue`d
  past every town visit ever made until 2026-09-03.

A **RULER**, like the other probes here: nothing under `engine/`, `env/`,
`data/`, `bridge/` or `train.py` may import it.
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def load(paths) -> list[dict]:
    """Every event, in file order. A malformed line is skipped and counted
    rather than fatal — the writer appends while the game runs, so the last
    line of a live file can legitimately be half-written."""
    rows, bad = [], 0
    for path in paths:
        for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                bad += 1
    if bad:
        print(f"  ({bad} unreadable line(s) skipped — normal if the file is live)\n")
    return rows


def _hours(rows) -> float:
    stamps = [r["t"] for r in rows if isinstance(r.get("t"), (int, float))]
    if len(stamps) < 2:
        return 0.0
    # Sum of gaps under ten minutes: a session is several sittings, and the
    # wall clock between them is not play. Same split `tools/retreat_audit.py`
    # would need for a rate.
    gaps = [b - a for a, b in zip(sorted(stamps), sorted(stamps)[1:]) if 0 < b - a < 600]
    return sum(gaps) / 3600.0


def report(rows) -> None:
    kinds = collections.Counter(r.get("kind", "?") for r in rows)
    plays = [r for r in rows if r.get("kind") == "play"]
    decisions = [r for r in rows if r.get("kind") == "decision"]
    hours = _hours(rows)

    print(f"{len(rows):,} events over ~{hours:.1f} h of play")
    print("  " + "  ".join(f"{k} {n}" for k, n in kinds.most_common()))
    print()

    dead = [r for r in rows if r.get("kind") == "hero_died"]
    if dead:
        print("CASUALTIES")
        for r in dead:
            print(f"  {r.get('class', '?')} #{r.get('guid', '?')} died on "
                  f"{r.get('hp', 0):.0f} hp, {r.get('stress', 0):.0f} stress; "
                  f"{len(r.get('survivors', []))} walked on")
        print()

    print("DEMONSTRATIONS (combat turns the human played; no checkpoint needed)")
    if plays:
        what = collections.Counter(r.get("played", "?") for r in plays)
        print(f"  {len(plays):,} turns:  "
              + "  ".join(f"{k} {n}" for k, n in what.most_common()))
        boards = sum(1 for r in plays if r.get("board", {}).get("heroes"))
        print(f"  {boards:,} carry the board they were played on"
              + ("" if boards == len(plays) else "  <-- the rest are unusable"))
    else:
        print("  none — was a raid actually played while this ran?")
    print()

    print("AGREEMENT (advised vs played; needs a loadable checkpoint)")
    if decisions:
        by = collections.defaultdict(collections.Counter)
        for r in decisions:
            by[r.get("layer", "?")][r.get("verdict", "?")] += 1
        for layer in sorted(by):
            c = by[layer]
            total = sum(c.values())
            m = c.get("MATCH", 0)
            print(f"  {layer:8s} {total:4d}   MATCH {m:4d} ({m / total:4.0%})   "
                  f"DIFFER {c.get('DIFFER', 0):4d}   ? {c.get('?', 0):3d}")
    else:
        print("  none — expected while no checkpoint loads at the current")
        print("  observation width. The demonstrations above are unaffected.")
    print()

    embarks = [r for r in rows if r.get("kind") == "raid_started"]
    # A `raid_in_progress` is the recorder finding the party already
    # underground when it started. Its pack is mid-raid inventory, NOT a
    # purchase, so it is counted separately and never priced.
    joined = [r for r in rows if r.get("kind") == "raid_in_progress"]
    print("THE EMBARK (what was bought, and who went)")
    if joined:
        print(f"  ({len(joined)} raid(s) already under way when recording "
              f"began — their packs are not purchases and are not shown)")
    # Schema 1 had no baseline: `in_raid` started False, so the first poll of a
    # session that found the party already underground reported it as an
    # embark. Such an entry's pack is mid-raid inventory and must not be read
    # as a purchase — and unlike the upgrades case it is not separable after
    # the fact, so the whole row is suspect rather than a subset of it.
    if any(r.get("schema", 1) < 2 for r in embarks):
        print("  !! this file predates the schema-2 baseline fix. An entry that")
        print("     fired at session start is the recorder finding a raid already")
        print("     under way, not an embark, and its pack is mid-raid inventory.")
    if not embarks:
        print("  no embark seen — start the recorder in the Hamlet to catch one")
    else:
        for r in embarks:
            q = r.get("quest", {})
            pack = {k: v for k, v in (r.get("pack") or {}).items() if v}
            print(f"  {q.get('dungeon', '?')} tier {q.get('difficulty', '?')} "
                  f"len {q.get('length', '?')}: "
                  + (", ".join(f"{k} x{v}" for k, v in sorted(pack.items())) or "empty pack"))
    print()

    town = [r for r in rows if r.get("phase") == "town"]
    print("THE HAMLET (never recorded before 2026-09-03)")
    if not town:
        print("  nothing — the recorder only sees the town if the game was in it")
    else:
        gold = 0
        for r in town:
            if r.get("kind") == "wallet":
                gold += r.get("changed", {}).get("gold", {}).get("delta", 0)
        counts = collections.Counter(r.get("kind") for r in town)
        print(f"  {len(town)} events:  "
              + "  ".join(f"{k} {n}" for k, n in counts.most_common()))
        print(f"  net gold across the session: {gold:+,}")
        # Schema 1 counted a hired hero's existing trees as purchases. The rest
        # of such a file is fine, so it is worth reading with a caveat rather
        # than discarding — but the caveat has to be printed, or the number
        # gets quoted.
        stale = [r for r in town
                 if r.get("kind") == "upgrades_bought" and r.get("schema", 1) < 2]
        if stale:
            biggest = max(len(r.get("trees", {})) for r in stale)
            print(f"  !! {len(stale)} upgrade event(s) predate the schema-2 fix "
                  f"(largest claims {biggest} trees at once).")
            print("     Those are hires arriving with their own kits, not purchases.")
            print("     Every other event in this file is unaffected.")
        for r in town:
            if r.get("kind") == "hero_joined":
                print(f"    hired  {r.get('class', '?')} #{r.get('guid', '?')}")
            elif r.get("kind") == "hero_left":
                print(f"    lost   {r.get('class', '?')} #{r.get('guid', '?')}")


def differs(rows) -> None:
    out = [r for r in rows if r.get("kind") == "decision" and r.get("verdict") == "DIFFER"]
    if not out:
        print("no disagreements recorded")
        return
    print(f"{len(out)} disagreement(s). Annotate each by hand:")
    print("  A impossible (a bridge bug)   B legal but worse   C better than the advice\n")
    for r in out:
        where = r.get("area") or f"round {r.get('round', '?')}"
        print(f"  [{r.get('layer', '?'):6s}] {where}: {r.get('reason', '')}")
        if r.get("advised"):
            print(f"           advised {r['advised']}, played {r.get('played', '?')}")


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("logs", nargs="*", help="journals to pool (default: bat/advise.jsonl)")
    ap.add_argument("--differs", action="store_true",
                    help="list every disagreement instead of the summary")
    args = ap.parse_args()

    root = Path(__file__).resolve().parent.parent
    paths = [Path(p) for p in args.logs] or [root / "bat" / "advise.jsonl"]
    paths = [p for p in paths if p.is_file()]
    if not paths:
        print("no journal found — run bat/combat_advise.bat and play for a bit")
        return 1

    rows = load(paths)
    if not rows:
        print("the journal is empty")
        return 1
    print(f"{', '.join(p.name for p in paths)}\n")
    differs(rows) if args.differs else report(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
