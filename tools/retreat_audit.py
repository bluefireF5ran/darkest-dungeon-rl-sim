"""Did the bridge ever decide to LEAVE? — the stay/leave ruler, on real play.

Fran, 2026-09-03: *"using the model in game I had several deaths which I didn't
really understand why it went for that kind of play"*.

`bat/play.log` answers it, because `bridge/advisor.py::survival_note` prints the
model's OWN survival forecast beside every decision:

    [13:23:03] [R1] Shieldbreaker (rank 3, 12/22 hp, 152 stress)
      10 combats left in crypts (tier 1): P(finish intact) 0% +-0, expected deaths 3.8
      leaving costs ~6,278 gold, staying ~-375 - worth pushing on

It predicted the whole party would die and priced staying as a net GAIN of 375
gold. Over the 2026-09-01/02 corpus that is not an outlier: the verdict is
"worth pushing on" at **every** survival probability from 0% to 100%, including
**91 of 96 decisions at P(finish intact) <= 8%**.

The combat play in those scenes is unremarkable. **The macro layer was telling
it the run was profitable**, for three reasons, all fixed on 2026-09-03 and all
re-checkable with this tool:

1. A death was priced at ~1/30 of its value — `advisor.py` called
   `advise_retreat` with no `profile`, taking the equipped-gear fallback that
   `retreat.py`'s own docstring calls wrong by a factor of thirty.
2. The stress ahead was priced at exactly ZERO — `mean_final_stress` is a
   per-hero mean and `carried` was a party SUM, so `max(0, mean - sum)` clamped
   for any party carrying real stress.
3. The reward is credited on `completion_rate`, which counts runs that reach the
   end having lost heroes. Faithful to the game, and the reason a survival
   floor is needed on top of the arithmetic rather than instead of it.

## Reading it

`--explain` re-prices one logged decision. What the log carries is the FORECAST
(P(finish intact), expected deaths) and the two BILLS, not the rollout's full
result, so the re-pricing is partial by construction and says which parts it
could recover. That is enough for the question actually being asked, because
the survival floor needs only the expected deaths — which the log prints.

Like `tools/skill_tiers.py`, `tools/team_probe.py` and `tools/play_log_probe.py`
this is a **RULER**: nothing it measures may become a reward term, an action
mask or a training target. `tests/tools/test_retreat_audit.py` enforces that nothing
under `engine/`, `env/`, `data/`, `bridge/` or `train.py` imports it.

    python tools/retreat_audit.py                       # bat/play.log
    python tools/retreat_audit.py --log <file> ...      # several, pooled
    python tools/retreat_audit.py --explain 13:23:03    # re-price one decision
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# `[20:03:01] [R1] Highwayman (rank 2, 23/23 hp, 0 stress)`
#
# **`(?:after .*?-> )?` is not optional decoration.** A block resumed after the
# enemies acted reads `[R1] after 1 enemy turn -> Shieldbreaker (rank 3, ...)`,
# and without the skip the hero's NAME comes out as "after 1 enemy turn ->
# Shieldbreaker". A first pass at this analysis used `(\w[\w ]*?)` instead,
# which does not match those lines at all -- and they are not a rare shape:
# it under-counted Death's Door turns 7 against 14, missed the worst stress in
# the corpus (180, reported as 152), and every one of the misses was a hero in
# trouble, because that is when the enemies get to act between your turns.
# `tools/play_log_probe.py` learned the same lesson on the same log.
HERO = re.compile(
    r"^\[(\d\d:\d\d:\d\d)\] \[R(\d+)\] (?:after .*?-> )?(.+?) "
    r"\(rank (\d+), (\d+)/(\d+) hp, (\d+) stress\)")

# `  10 combats left in crypts (tier 1): P(finish intact) 0% +-0, expected deaths 3.8`
#
# **The +- is the trap.** It is a non-ASCII plus-minus followed by a count with
# NO fixed width, and the first pass at this analysis used a pattern that needed
# two digits after it -- which silently dropped every `+-0` row, i.e. all 110 of
# the fully-confident decisions and all 54 of the hopeless ones. The two ends of
# the distribution are exactly the rows worth reading, so the separator is
# matched as "whatever is not a digit".
FORECAST = re.compile(
    r"(\d+) combats? left in (\w+) \(tier (\d+)\): "
    r"P\(finish intact\) (\d+)%\s*[^\d,]*(\d+), expected deaths ([\d.]+)")

# `  leaving costs ~6,278 gold, staying ~-375 - worth pushing on`
VERDICT = re.compile(
    r"leaving costs ~(-?[\d,]+) gold, staying ~(-?[\d,]+)\s*.\s*(.+?)\s*$")

#: Below this, the run is a coin flip or worse and a human would be looking at
#: the exit. Used only to report a count -- it is not a threshold the bridge
#: applies (`bridge/retreat.FLOOR_DEATH_CHANCE` is).
DIRE = 25


@dataclass(frozen=True)
class Decision:
    """One stay/leave call, as the log recorded it."""
    line: int
    time: str
    combats: int
    zone: str
    tier: int
    intact: int              # P(finish intact), whole percent
    se: int
    deaths: float            # expected deaths over the remaining combats
    leaving: float           # gold
    staying: float           # gold
    verdict: str             # "worth pushing on" / "RETREAT" / a floor reason
    hero: Optional[str] = None
    hp: Optional[int] = None
    max_hp: Optional[int] = None
    stress: Optional[int] = None

    @property
    def retreated(self) -> bool:
        return "RETREAT" in self.verdict.upper()


def _num(text: str) -> float:
    return float(text.replace(",", ""))


def parse(lines) -> list[Decision]:
    """Every stay/leave decision in one log.

    The forecast and the verdict are two consecutive lines; the hero header is
    the most recent one above them, which is what attaches an HP and a stress
    to the call. A forecast with no verdict under it is skipped rather than
    half-recorded -- it means the block was truncated mid-write, which happens
    when the log is read while the bridge is still running.
    """
    out, hero = [], None
    for i, line in enumerate(lines):
        head = HERO.match(line)
        if head:
            hero = (head.group(3), int(head.group(5)), int(head.group(6)),
                    int(head.group(7)), head.group(1))
            continue
        fc = FORECAST.search(line)
        if not fc:
            continue
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        vd = VERDICT.search(nxt)
        if not vd:
            continue
        out.append(Decision(
            line=i + 1, time=hero[4] if hero else "",
            combats=int(fc.group(1)), zone=fc.group(2), tier=int(fc.group(3)),
            intact=int(fc.group(4)), se=int(fc.group(5)), deaths=float(fc.group(6)),
            leaving=_num(vd.group(1)), staying=_num(vd.group(2)),
            verdict=vd.group(3).strip(),
            hero=hero[0] if hero else None, hp=hero[1] if hero else None,
            max_hp=hero[2] if hero else None, stress=hero[3] if hero else None))
    return out


def verdict_table(decisions) -> str:
    """The headline: what did it decide, at each level of confidence?

    A stay/leave rule that is insensitive to the survival forecast is not a
    rule. Reading DOWN this table is the whole finding -- if the RETREAT column
    is empty at the top it means the layer never says no.
    """
    by = {}
    for d in decisions:
        push, ret = by.setdefault(d.intact, [0, 0])
        by[d.intact] = [push + (not d.retreated), ret + d.retreated]
    out = ["  P(intact)     n   push on   RETREAT",
           "  ---------  ----  --------  --------"]
    for pct in sorted(by):
        push, ret = by[pct]
        out.append(f"  {pct:7d}%  {push + ret:4d}  {push:8d}  {ret:8d}")
    return "\n".join(out)


def crisis_lines(decisions, all_lines) -> str:
    """The states real play actually reached, which a rate cannot show."""
    heroes = [HERO.match(l) for l in all_lines]
    heroes = [m for m in heroes if m]
    door = [m for m in heroes if int(m.group(5)) == 0]
    resolve = [m for m in heroes if int(m.group(7)) >= 100]
    dire = [d for d in decisions if d.intact <= DIRE]
    pushed = [d for d in dire if not d.retreated]
    out = [f"  decisions at P(intact) <= {DIRE}%: {len(dire)}"
           f"  ({len(pushed)} of them pushed on)"]
    if dire:
        worst = min(dire, key=lambda d: (d.intact, -d.deaths))
        out.append(f"    worst: {worst.intact}% intact, {worst.deaths:.1f} expected "
                   f"deaths, {worst.combats} combats left -> \"{worst.verdict}\"")
    out.append(f"  turns taken by a hero on Death's Door: {len(door)}")
    out.append(f"  turns taken past the Resolve Check (>=100 stress): {len(resolve)}")
    if resolve:
        top = max(resolve, key=lambda m: int(m.group(7)))
        out.append(f"    worst: {top.group(3)} at {top.group(7)} stress")
    return "\n".join(out)


def explain(decision: Decision, party_size: int = 4) -> str:
    """Re-price one logged decision, and say what could not be recovered.

    **What the log carries is the forecast and the two totals, not the rollout
    result**, so `mean_final_stress` and `completion_rate` -- the two inputs to
    the continue bill -- are not recoverable and the gold columns cannot be
    fully rebuilt. That is stated rather than papered over with a guess.

    What IS recoverable is the half that decides this case: the survival floor
    reads `expected deaths`, and the log prints it.
    """
    from bridge.retreat import FLOOR_MEAN_DEATHS               # noqa: PLC0415

    floored = decision.deaths >= FLOOR_MEAN_DEATHS
    out = [f"  {decision.time}  line {decision.line}"]
    if decision.hero:
        out.append(f"  {decision.hero} at {decision.hp}/{decision.max_hp} hp, "
                   f"{decision.stress} stress")
    out.append(f"  {decision.combats} combats left in {decision.zone} "
               f"(tier {decision.tier})")
    out.append(f"  forecast: P(finish intact) {decision.intact}% +-{decision.se}, "
               f"expected deaths {decision.deaths:.1f}")
    out.append("")
    out.append(f"  AS LOGGED:  leaving ~{decision.leaving:,.0f} gold, "
               f"staying ~{decision.staying:,.0f}  ->  {decision.verdict}")
    out.append("")
    out.append("  RE-PRICED:")
    out.append(f"    survival floor ({FLOOR_MEAN_DEATHS:.1f} expected deaths): "
               + ("FIRES -> RETREAT, whatever the gold says" if floored
                  else "does not fire"))
    out.append("    death term: the logged bill used the equipped-gear fallback")
    out.append("      (~750 gold a hero). At a real campaign's ~23,750 that term")
    out.append(f"      alone is ~{decision.deaths * 23750:,.0f} gold rather than "
               f"~{decision.deaths * 750:,.0f}.")
    if decision.stress is not None:
        out.append("    stress term: priced at 0 in the logged bill (the mean/sum")
        out.append("      units bug). Not recoverable from the log -- rebuilding it")
        out.append("      needs the rollout's mean_final_stress, which is not printed.")
    out.append("")
    verdict = "RETREAT" if floored else "unchanged by the floor; the gold decides"
    out.append(f"  VERDICT NOW: {verdict}")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("logs", nargs="*", default=None,
                    help="play logs to pool (default: bat/play.log)")
    ap.add_argument("--log", action="append", default=[],
                    help="another log to pool in")
    ap.add_argument("--explain", default=None,
                    help="re-price the decision whose block matches this text "
                         "(a timestamp like 13:23:03, or a line number)")
    args = ap.parse_args()

    root = Path(__file__).resolve().parent.parent
    paths = [Path(p) for p in (list(args.logs) + list(args.log))]
    if not paths:
        paths = [root / "bat" / "play.log"]
    paths = [p for p in paths if p.is_file()]
    if not paths:
        print("no play log found - run bat/combat_play.bat first")
        return 1

    lines, decisions = [], []
    for path in paths:
        text = path.read_text(encoding="utf-8", errors="replace").splitlines()
        decisions += parse(text)
        lines += text

    if not decisions:
        print("no stay/leave decisions in the log - was --survival on?")
        return 1

    if args.explain:
        needle = args.explain
        hits = [d for d in decisions
                if needle in d.time or needle == str(d.line)]
        if not hits:
            print(f"no decision matching {needle!r}")
            return 1
        for d in hits[:3]:
            print(explain(d))
            print()
        return 0

    print(f"{len(decisions)} stay/leave decisions over "
          f"{', '.join(p.name for p in paths)}\n")
    print(verdict_table(decisions))
    print()
    print(crisis_lines(decisions, lines))
    print()
    retreats = sum(d.retreated for d in decisions)
    print(f"  RETREAT was advised {retreats} time(s) in {len(decisions)} "
          f"decisions ({retreats / len(decisions):.1%}).")
    print()
    print("  A stay/leave rule insensitive to the forecast is not a rule. If the")
    print("  RETREAT column is empty against a low P(intact), the layer never")
    print("  says no -- see this file's header for the three causes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
