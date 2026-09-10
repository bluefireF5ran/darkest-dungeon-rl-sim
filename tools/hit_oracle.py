"""Grade the engine's to-hit rule against the REAL game.

Every other ruler in this repo compares the simulator to itself or to a data
file. This one compares it to `Darkest.exe` actually running: the game logs

    Chance to hit: 0.635000
    Roll to hit:   0.178166

for every attack, over `OutputDebugStringA`, with no flag needed (`DECOMP.md`
F3 — the `log_info` sink calls `OutputDebugStringA` before it looks at the
severity tag). `archive/traces/*.log` already holds 186 such pairs, captured
for other purposes.

**Reading the outcome.** The channel never says "hit" or "miss". What it does
say is the damage band: a landed attack draws `Battle Random int range A,B:
3 , 5 Value: 5` inside the same `bs_turn_presentation` block, and a miss draws
nothing. That marker is sound in one direction only — a skill with no damage
(a stress attack, a debuff, a mark) lands and still draws no band — so a turn
in which NOTHING drew damage is unclassifiable and is reported separately
rather than scored. 155 of 186 pairs are classifiable.

**What it proves.** Six pairs have a roll at or above `cutoffAlwaysHit` (0.95)
that is ALSO above the attack's chance, and all six dealt damage. Under the
pre-F4 rule (`P(hit) = min(0.95, raw + 0.05)`, compared as `roll < chance`)
every one of them is a miss. The corpus also logs chances of **1.01 and
1.05**, which is only possible if the chance is never clamped. Both halves of
`DECOMP.md` F4, confirmed against the running game rather than against the
disassembly.

    python tools/hit_oracle.py                     # grade the corpus
    python tools/hit_oracle.py --verbose           # every classifiable pair
    python tools/hit_oracle.py --emit              # refresh the tracked fixture

`--emit` distils the pairs into `archive/traces/hit_pairs.json`, which is what
`tests/tools/test_hit_oracle.py` reads: the raw captures are `*.log` and therefore
gitignored, so without the fixture the guard would vanish on a fresh clone.
Point `--traces` at a new capture directory to extend the corpus.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data.store_cache import load_store_cached
from engine.resolution import resolve_to_hit

from data.paths import CSV_DIR as _CSV_DIR, GAME_DIR as _GAME_DIR

GAME_DIR = str(_GAME_DIR)
CSV_DIR = str(_CSV_DIR)
FIXTURE = Path(CSV_DIR) / "archive" / "traces" / "hit_pairs.json"

RE_CHANCE = re.compile(r"Chance to hit: ([0-9.]+)")
RE_ROLL = re.compile(r"Roll to hit: ([0-9.]+)")
RE_DAMAGE = re.compile(r"Battle Random int range A,B")
RE_TURN = re.compile(r"state change to bs_turn_presentation")
RE_END = re.compile(r"state change to bs_after_turn,")


def parse_trace(path: str) -> list[dict]:
    """One record per (chance, roll) pair, tagged with its turn.

    `damage` is whether a damage band was drawn for THIS pair; `turn_damaged`
    is whether anything in the whole turn drew one, which is what makes the
    pair classifiable.
    """
    lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    out: list[dict] = []
    turn_id = 0
    i = 0
    while i < len(lines):
        if not RE_TURN.search(lines[i]):
            i += 1
            continue
        turn_id += 1
        pairs: list[dict] = []
        j = i + 1
        while j < len(lines) and not RE_END.search(lines[j]):
            mc = RE_CHANCE.search(lines[j])
            mr = RE_ROLL.search(lines[j + 1]) if j + 1 < len(lines) else None
            if mc and mr:
                k, damage = j + 2, False
                while (k < len(lines) and not RE_END.search(lines[k])
                       and not RE_CHANCE.search(lines[k])):
                    if RE_DAMAGE.search(lines[k]):
                        damage = True
                        break
                    k += 1
                pairs.append({"source": os.path.basename(path), "turn": turn_id,
                              "chance": float(mc.group(1)),
                              "roll": float(mr.group(1)), "damage": damage})
            j += 1
        any_damage = any(p["damage"] for p in pairs)
        for p in pairs:
            p["turn_damaged"] = any_damage
        out.extend(pairs)
        i = j
    return out


def load_corpus(trace_dir: str | None) -> tuple[list[dict], str]:
    if trace_dir:
        rows: list[dict] = []
        for f in sorted(glob.glob(os.path.join(trace_dir, "*.log"))):
            rows.extend(parse_trace(f))
        if rows:
            return rows, trace_dir
    if FIXTURE.exists():
        return json.loads(FIXTURE.read_text(encoding="utf-8")), str(FIXTURE)
    return [], "(nothing found)"


def grade(rows: list[dict], rules) -> dict:
    scored = [r for r in rows if r["turn_damaged"]]
    disagree = [r for r in scored
                if resolve_to_hit(r["roll"], r["chance"], rules) != r["damage"]]
    cutoff = [r for r in rows if r["roll"] >= rules.cutoff_always_hit
              and r["roll"] > r["chance"]]
    return {
        "pairs": len(rows),
        "classifiable": len(scored),
        "agree": len(scored) - len(disagree),
        "disagree": disagree,
        "misses": [r for r in scored if not r["damage"]],
        "cutoff_hits": cutoff,
        "unclamped": sorted({r["chance"] for r in rows if r["chance"] > 1.0}),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--traces", default=str(Path(CSV_DIR) / "archive" / "traces"),
                    help="directory of dbwin_capture .log files")
    ap.add_argument("--emit", action="store_true",
                    help="rewrite archive/traces/hit_pairs.json from the traces")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    rows, where = load_corpus(args.traces)
    if not rows:
        print("no (chance, roll) pairs found in %s" % where)
        return 1
    rules = load_store_cached(CSV_DIR, GAME_DIR).rules
    res = grade(rows, rules)

    print("corpus: %s" % where)
    print("  (chance, roll) pairs      %d" % res["pairs"])
    print("  classifiable              %d   (a turn that drew NO damage says"
          " nothing:\n%s a stress attack or a debuff lands and draws no band)"
          % (res["classifiable"], " " * 28))
    print("  engine agrees             %d / %d  (%.1f%%)"
          % (res["agree"], res["classifiable"],
             100.0 * res["agree"] / max(res["classifiable"], 1)))
    print("  of which real misses      %d" % len(res["misses"]))
    print()
    print("  rolls >= cutoffAlwaysHit (%.2f) that BEAT the chance: %d"
          % (rules.cutoff_always_hit, len(res["cutoff_hits"])))
    for r in res["cutoff_hits"]:
        print("      chance %.3f  roll %.6f  ->  damage drawn: %s"
              % (r["chance"], r["roll"], r["damage"]))
    print("      every one of these is a MISS under the pre-F4 rule.")
    print()
    print("  chances logged above 1.0 (so the chance is never clamped): %s"
          % (res["unclamped"] or "none"))

    if res["disagree"]:
        print()
        print("  DISAGREEMENTS:")
        for r in res["disagree"]:
            print("      %s turn %d: chance %.3f roll %.6f, engine says %s,"
                  " the game drew damage: %s"
                  % (r["source"], r["turn"], r["chance"], r["roll"],
                     resolve_to_hit(r["roll"], r["chance"], rules), r["damage"]))

    if args.verbose:
        print()
        for r in rows:
            print("   %-26s turn %3d  chance %.3f roll %.6f  dmg %-5s  %s"
                  % (r["source"], r["turn"], r["chance"], r["roll"], r["damage"],
                     "" if r["turn_damaged"] else "(unclassifiable)"))

    if args.emit:
        FIXTURE.parent.mkdir(parents=True, exist_ok=True)
        FIXTURE.write_text(json.dumps(rows, indent=1), encoding="utf-8")
        print("\nwrote %s (%d pairs)" % (FIXTURE, len(rows)))

    return 0 if not res["disagree"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
