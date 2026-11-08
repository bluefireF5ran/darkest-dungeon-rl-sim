"""Did the bridge's forecast of the dice come true in the running game?

    python tools/fate_live_check.py <run.py log> <recording dir with trace.log>

`run.py --fate` prints, before every hero acts, `fate: hit roll X` -- the hit
roll it read off the save (DECOMP.md F15). The game then logs `Roll to hit: Y`
over its debug channel (`bridge/recorder.py --trace`). This pairs the two in
order: each forecast is matched to the first not-yet-used traced roll that
equals it to three decimals, searching forward only, so a coincidence would
have to be both equal and in sequence.

Forecasts with no match are expected in two cases and reported separately:
the hero did not attack (a heal, a move, a pass never draws a hit roll), or
the advice was refreshed and printed twice for one turn.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

_FORECAST = re.compile(r"fate: hit roll ([0-9.]+)")
_ROLL = re.compile(r"Roll to hit: ([0-9.]+)")


def pair(forecasts: list[float], rolls: list[float], window: int = 12) -> tuple[int, int]:
    """(matched, unmatched): an in-order match within `window` traced rolls."""
    i = matched = 0
    for f in forecasts:
        # The first forecast may sit anywhere: a log can start part-way
        # through a recording that began earlier. After that, stay in step.
        end = len(rolls) if matched == 0 else min(len(rolls), i + window)
        for j in range(i, end):
            if abs(rolls[j] - f) < 6e-4:
                matched += 1
                i = j + 1
                break
    return matched, len(forecasts) - matched


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("log", type=Path)
    ap.add_argument("recording", type=Path)
    args = ap.parse_args(argv)
    text = args.log.read_text(encoding="utf-8", errors="replace")
    forecasts = [float(m) for m in _FORECAST.findall(text)]
    # A refreshed advice block repeats its forecast; keep the first of a run.
    dedup = [f for k, f in enumerate(forecasts) if k == 0 or f != forecasts[k - 1]]
    trace = (args.recording / "trace.log").read_text(encoding="utf-8", errors="replace")
    rolls = [float(m) for m in _ROLL.findall(trace)]
    matched, unmatched = pair(dedup, rolls)
    print(f"{len(dedup)} forecasts (from {len(forecasts)} printed), {len(rolls)} traced rolls")
    print(f"  came true, in order: {matched}")
    print(f"  no matching roll   : {unmatched}  (non-attacks, or a turn re-advised)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
