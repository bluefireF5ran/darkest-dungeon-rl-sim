"""Compact one-line summary per eval_expedition.py --full-expedition log.

Used to compare training checkpoints against each other while looking for a
plateau, without re-reading a full 36-cell table each time.

    python tools/summarize_eval.py archive/logs/eval_6M.log archive/logs/eval_9M.log ...

Reports the SUCCESS rate (completed every combat AND zero deaths) — not the
zero-death column, which counts stall-outs as survivals and is kept in the
eval output only for continuity with pre-fix runs (see ASSUMPTIONS.md).

The trailing `big=HI/LO%` is SUCCESS across a median split on how much of each
expedition was fought against size-2+ monsters, and `backrank` counts the
chosen attacks that were illegal before the large-monster targeting fix. It is
the control for anything that only changes large-monster boards: HI moving more
than LO is evidence, both moving together is evidence for something else, and a
`backrank` of 0 falsifies the story outright. Logs written before 2026-08-12
have no such line and show `-`.
"""

import re
import statistics
import sys


LARGE_SPLIT_PREFIX = "LARGE-MONSTER SPLIT:"


def _large_split(lines: list[str]) -> str:
    """The high-dose / low-dose SUCCESS pair and the back-rank action count.

    Absent from every log written before 2026-08-12, so its absence is normal
    and reported as `-` rather than treated as an error — the older runs are
    still the baselines worth comparing against."""
    hit = next((ln for ln in lines if ln.startswith(LARGE_SPLIT_PREFIX)), None)
    if hit is None:
        return "big=          - backrank=    -"
    nums = re.findall(r"succ\s+([0-9.]+)%", hit)
    back = re.search(r"backrank\s+(\d+)", hit)
    pair = (f"{float(nums[0]):5.1f}/{float(nums[1]):5.1f}%" if len(nums) == 2 else "      n/a")
    return f"big={pair} backrank={back.group(1) if back else '-':>5s}"


def summarize(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        lines = f.read().splitlines()
    # The split line carries its own `succ NN.N%` pairs, so it has to come out
    # before the per-cell scan or it would be counted as two extra cells and
    # drag the mean toward itself.
    cells = "\n".join(ln for ln in lines if not ln.startswith(LARGE_SPLIT_PREFIX))
    succ = [float(m) for m in re.findall(r"succ\s+([0-9.]+)%", cells)]
    if not succ:
        return f"{path}: no result cells parsed (did the eval finish?)"
    wipe = [float(m) for m in re.findall(r"wipe\s+([0-9.]+)%", cells)]
    stall = [float(m) for m in re.findall(r"stall\s+([0-9.]+)%", cells)]
    stress = [float(m) for m in re.findall(r"stress\s+([0-9.]+)", cells)]
    return (
        f"{path:24s} cells={len(succ):2d} "
        f"succ mean={statistics.mean(succ):5.1f}% median={statistics.median(succ):5.1f}% "
        f"max={max(succ):5.1f}% min={min(succ):5.1f}% | "
        f"wipe={statistics.mean(wipe):5.1f}% stall={statistics.mean(stall):5.1f}% "
        f"stress={statistics.mean(stress):5.1f} | {_large_split(lines)}"
    )


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(1)
    for p in sys.argv[1:]:
        print(summarize(p))
