"""Grade a checkpoint against REAL captured positions, not simulator SUCCESS.

    python tools/agreement.py --a models/dd_combat_ppo_full_expedition.zip
    python tools/agreement.py --a <ckpt-A> --b <ckpt-B>          # paired
    python tools/agreement.py --a <ckpt> --self-check            # prove the ruler

## What this measures, and what it does not

`eval_expedition.py` asks "does this checkpoint win fights **in the simulator**".
That is the question the whole project has been unable to make answer anything:
the eval's own noise is wider than any fidelity fix ever measured, and the
simulator is the thing whose faithfulness is in doubt in the first place.

This asks a different question with no simulator in the loop: **over positions
that really happened, how often does the checkpoint advise the move that was
actually played?** `bridge/run.py` already computes exactly that -- it arms a
recommendation, watches the next save, and marks MATCH / DIFFER / `?`. This
runs that marking over the capture corpus for one or two checkpoints and pairs
the results.

**MATCH is agreement, not correctness.** Fran is the expert whose criteria
became `PLAYBOOK.md`, so agreeing with him is evidence -- but a DIFFER can mean
the policy found something better, and the marker itself says to annotate them
by hand as A (impossible: a bridge bug), B (legal but worse) or C (better than
the advice). A rise in agreement is a transfer signal; it is not a score.

**`?` is the instrument's blind spot, not a disagreement.** A missed attack
leaves no trace in the save. It runs ~3-7% and is excluded from the rate rather
than counted as a loss.

## Why pairing, and why the self-check

Both arms replay the *same* captures, so every decision point is shared and the
comparison can be made decision by decision instead of rate against rate. That
is the same variance reduction `tools/ab_compare.py` exists for, and here it is
free: the corpus is fixed.

The keys come from the SAVE (round, whose turn, what was played), never from the
advice, so a change of checkpoint cannot change them. The tool asserts the two
key sequences are identical before reporting anything. If they are not, the arms
were not facing the same decisions and no delta they produce means anything --
so it refuses rather than printing a number. `--self-check` runs one checkpoint
as both arms; every delta must be exactly zero.

## Statistical power, stated up front

The corpus yields ~106 markable combat decisions. Unpaired, that resolves
nothing smaller than about 13pp. Paired it is far better, but it is still one
corpus of one player's raids -- so read the McNemar count, not just the delta.
"""

from __future__ import annotations

import argparse
import io
import math
import random
import sys
from contextlib import redirect_stdout
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

REPO = Path(__file__).resolve().parent.parent
DEFAULT_SETS = ("raidsnaps2", "raidsnaps3", "raidsnaps4")
LAYERS = ("combat", "route", "torch", "curio", "camp")
BOOTSTRAP = 10000


@dataclass
class Arm:
    """One checkpoint's verdicts over the corpus, in decision order."""
    label: str
    keys: list[tuple] = field(default_factory=list)
    verdicts: list[str] = field(default_factory=list)
    layers: list[str] = field(default_factory=list)
    infos: list[dict] = field(default_factory=list)

    def scored(self, layer: str) -> list[tuple[tuple, str]]:
        """(key, verdict) for one layer, `?` dropped -- see the module docstring."""
        return [(k, v) for k, v, ell in zip(self.keys, self.verdicts, self.layers)
                if ell == layer and v != "?"]

    def unknown(self, layer: str) -> int:
        return sum(1 for v, ell in zip(self.verdicts, self.layers)
                   if ell == layer and v == "?")

    def disagreements(self) -> dict[tuple[str, str], int]:
        """{(played, advised): n} over combat DIFFERs.

        The point of the breakdown: "advised an attack where an attack was
        played" is a targeting quibble, while "advised a heal where an attack
        was played" is a different policy. One number cannot tell them apart.
        """
        out: dict[tuple[str, str], int] = {}
        for v, ell, info in zip(self.verdicts, self.layers, self.infos):
            if ell != "combat" or v != "DIFFER":
                continue
            k = (str(info.get("played", "?")), str(info.get("advised", "?")))
            out[k] = out.get(k, 0) + 1
        return out


def play(model_path: str, sets: tuple[str, ...], label: str,
         quiet: bool = True) -> Arm:
    """Replay every capture set with one checkpoint and collect the verdicts.

    `run.py`'s replay prints a whole dungeon's worth of advice per set, which is
    the point when a human runs it and noise when a tool does. It is captured
    and dropped unless --verbose.
    """
    from bridge.advisor import Advisor
    from bridge.run import Session, replay

    arm = Arm(label=label)
    for name in sets:
        directory = REPO / "archive" / "traces" / name
        if not directory.exists():
            print(f"  (missing capture set {name})")
            continue

        def record(ell, verdict, key, info=None, _name=name):
            arm.keys.append((_name,) + tuple(key))
            arm.verdicts.append(verdict)
            arm.layers.append(ell)
            arm.infos.append(dict(info or {}))

        advisor = Advisor(model_path=model_path)
        session = Session(advisor, None, on_score=record)
        sink = io.StringIO()
        if quiet:
            with redirect_stdout(sink):
                replay(session, directory)
                session.summary()          # settles the deferred curios
        else:
            replay(session, directory)
            print(session.summary())
    return arm


# ── statistics ──

def mcnemar_exact(b: int, c: int) -> float:
    """Two-sided exact McNemar on the discordant pairs only.

    b = A right where B was wrong, c = the reverse. The concordant pairs carry
    no information about the difference, which is exactly why pairing is worth
    the trouble.
    """
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / (2 ** n)
    return min(1.0, 2 * tail)


def bootstrap_ci(diffs: list[int], iters: int = BOOTSTRAP,
                 seed: int = 0) -> tuple[float, float]:
    """Percentile CI on the mean paired difference."""
    if not diffs:
        return (0.0, 0.0)
    rng = random.Random(seed)
    n = len(diffs)
    means = []
    for _ in range(iters):
        means.append(sum(diffs[rng.randrange(n)] for _ in range(n)) / n)
    means.sort()
    return (means[int(0.025 * iters)], means[int(0.975 * iters)])


def rate(pairs: list[tuple[tuple, str]]) -> float:
    return (sum(1 for _, v in pairs if v == "MATCH") / len(pairs)) if pairs else 0.0


# ── reporting ──

def report_single(arm: Arm) -> None:
    print(f"\nagreement with real play - {arm.label}")
    print(f"  {'layer':8s} {'n':>4s}  {'MATCH':>7s}  {'DIFFER':>7s}  {'?':>4s}")
    for layer in LAYERS:
        pairs = arm.scored(layer)
        unk = arm.unknown(layer)
        if not pairs and not unk:
            continue
        m = sum(1 for _, v in pairs if v == "MATCH")
        print(f"  {layer:8s} {len(pairs):4d}  {m:3d} {rate(pairs):5.1%}  "
              f"{len(pairs)-m:3d} {1-rate(pairs):5.1%}  {unk:4d}")
    print("\n  MATCH is agreement with what was PLAYED, not correctness -"
          " a DIFFER can be the better move.")
    print("  '?' is excluded from the rate: a missed attack leaves no trace"
          " in the save.")
    report_disagreements(arm)


def report_paired(a: Arm, b: Arm, self_check: bool = False) -> int:
    if a.keys != b.keys:
        print("\nREFUSING to compare: the two arms did not face the same "
              "decisions.")
        print(f"  A scored {len(a.keys)}, B scored {len(b.keys)}")
        for i, (ka, kb) in enumerate(zip(a.keys, b.keys)):
            if ka != kb:
                print(f"  first divergence at {i}: {ka}  vs  {kb}")
                break
        print("  A delta between misaligned arms means nothing. Not printing one.")
        return 1

    print(f"\npaired agreement with real play")
    print(f"  A = {a.label}")
    print(f"  B = {b.label}")
    print(f"\n  {'layer':8s} {'n':>4s}  {'A':>7s}  {'B':>7s}  {'delta':>8s}"
          f"  {'95% CI':>16s}  {'b/c':>7s}  {'p':>6s}")
    worst = 0
    for layer in LAYERS:
        pa, pb = a.scored(layer), b.scored(layer)
        if not pa:
            continue
        ra, rb = rate(pa), rate(pb)
        diffs = [(1 if vb == "MATCH" else 0) - (1 if va == "MATCH" else 0)
                 for (_, va), (_, vb) in zip(pa, pb)]
        lo, hi = bootstrap_ci(diffs)
        bb = sum(1 for d in diffs if d < 0)        # A matched, B did not
        cc = sum(1 for d in diffs if d > 0)        # B matched, A did not
        p = mcnemar_exact(bb, cc)
        print(f"  {layer:8s} {len(pa):4d}  {ra:6.1%}  {rb:6.1%}  {rb-ra:+7.1%}"
              f"  [{lo:+6.1%},{hi:+6.1%}]  {bb:3d}/{cc:<3d}  {p:6.3f}")
        if self_check and (bb or cc or abs(rb - ra) > 1e-12):
            worst = 1
            print(f"           SELF-CHECK FAILED on {layer}: one checkpoint "
                  f"played as both arms must give an exact zero.")
    if self_check:
        print("\nself-check: PASSED - every delta is exactly zero, so any "
              "delta this tool reports is the checkpoints and not the harness."
              if not worst else "\nself-check: FAILED")
        return worst
    print("\n  b/c are the DISCORDANT pairs -- b: A agreed and B did not, "
          "c: the reverse.")
    print("  Concordant pairs carry no information about the difference; "
          "with few discordant ones, a large delta can still be noise.")
    report_disagreements(a, b)
    return 0


def report_disagreements(*arms: Arm) -> None:
    """What each arm wanted instead, on the combat turns it got wrong."""
    tables = [(arm.label, arm.disagreements()) for arm in arms]
    kinds = sorted({k for _, t in tables for k in t})
    if not kinds:
        return
    print(f"\n  combat DIFFERs by kind (what was PLAYED -> what was ADVISED):")
    width = max(len(lbl) for lbl, _ in tables)
    header = "  " + " " * 26 + "  ".join(f"{lbl:>{max(6, width)}s}"
                                         for lbl, _ in tables)
    print(header)
    for played, advised in kinds:
        cells = "  ".join(f"{t.get((played, advised), 0):>{max(6, width)}d}"
                          for _, t in tables)
        print(f"  {played:>10s} -> {advised:<12s}  {cells}")
    print("  played == advised means the SAME KIND of action against a "
          "different target or footprint.")
    print("  The other rows are a different play entirely, which is the more "
          "serious kind of divergence.")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Agreement with real captured play, per layer.")
    ap.add_argument("--a", required=True, help="checkpoint A")
    ap.add_argument("--b", default=None, help="checkpoint B (paired comparison)")
    ap.add_argument("--self-check", action="store_true",
                    help="play A as BOTH arms; every delta must be zero")
    ap.add_argument("--sets", nargs="*", default=list(DEFAULT_SETS),
                    help="capture set names under archive/traces/")
    ap.add_argument("--verbose", action="store_true",
                    help="let the replay print its full advice stream")
    args = ap.parse_args(argv)

    sets = tuple(args.sets)
    print(f"replaying {len(sets)} capture set(s): {', '.join(sets)}")

    arm_a = play(args.a, sets, Path(args.a).name, quiet=not args.verbose)
    if not arm_a.keys:
        print("no decisions were scored - is the capture corpus present?")
        return 1

    if args.self_check:
        arm_b = play(args.a, sets, Path(args.a).name + " (again)",
                     quiet=not args.verbose)
        return report_paired(arm_a, arm_b, self_check=True)

    if args.b is None:
        report_single(arm_a)
        return 0

    arm_b = play(args.b, sets, Path(args.b).name, quiet=not args.verbose)
    return report_paired(arm_a, arm_b)


if __name__ == "__main__":
    sys.exit(main())
