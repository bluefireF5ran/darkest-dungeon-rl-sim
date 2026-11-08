"""Does the survival forecast predict what happens in the REAL game?

`tools/forecast_calibration.py` checks the simulator against itself. This
checks `bridge/rollout.py`'s live forecast against the raids the bridge
actually played, read from the overnight logs.

Per raid it takes the FIRST forecast ("N combats left ... expected deaths D"),
turns it into a rate per fight (D / N), and compares that rate times the
fights the raid actually reached against the deaths that raid actually had.
Per fight, not per raid, because a raid that ends early reaches fewer fights
than forecast. Doing it per raid is the confound that misled the 2026-09-23
audit.

**Only valid raids count.** Fran, 2026-09-23: sessions where the bridge
retreated on sight or looped at the first fight carry no information. Use
`--since` to start after the fix that makes the raids you are measuring real.

    python tools/forecast_vs_game.py --since 20260923_2200
"""
from __future__ import annotations

import argparse
import glob
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FORECAST = re.compile(r"(\d+) combats? left in (\w+) \(tier (\d)\): "
                      r"P\(finish intact\) (\d+)% .*?expected deaths ([\d.]+)")


def raids(text: str):
    """(zone, forecast combats, rate per fight, fights reached, deaths)."""
    for raid in text.split("-- raid over --")[:-1]:
        m = FORECAST.search(raid)
        if not m:
            continue
        rounds = [int(x) for x in re.findall(r"\] \[R(\d+)\]", raid)]
        fights = sum(1 for i, r in enumerate(rounds)
                     if i == 0 or r < rounds[i - 1])
        n = int(m.group(1))
        yield (m.group(2), n, float(m.group(5)) / max(1, n), fights,
               len(re.findall(r"DEATH ", raid)))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--since", default="",
                    help="only logs whose run id (YYYYMMDD_HHMMSS) is >= this")
    ap.add_argument("--logs", default=str(REPO / "archive/logs/overnight"))
    args = ap.parse_args(argv)
    rows = []
    for f in sorted(glob.glob(str(Path(args.logs) / "iter_*.log"))):
        run_id = Path(f).stem[len("iter_"):][:15]
        if args.since and run_id < args.since:
            continue
        text = Path(f).read_text(encoding="utf-8", errors="replace")
        rows.extend((run_id, *r) for r in raids(text))
    if not rows:
        print("no raid with a forecast in range")
        return 0
    exp = sum(r[3] * r[4] for r in rows)
    act = sum(r[5] for r in rows)
    fights = sum(r[4] for r in rows)
    for r in rows:
        print(f"  {r[0]}  {r[1]:8} forecast {r[2]} fights, reached {r[4]}, "
              f"expected {r[3] * r[4]:.2f} deaths, got {r[5]}")
    print(f"{len(rows)} raids, {fights} fights reached: forecast {exp:.1f} deaths, "
          f"actual {act}" + (f" (ratio {act / exp:.2f})" if exp else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
