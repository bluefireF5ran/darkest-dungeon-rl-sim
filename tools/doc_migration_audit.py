"""Prove a documentation refactor lost nothing.

The 2026-09-07 restructure split three very large, chronologically-ordered
documents -- `CLAUDE.md` (3,878 lines), `docs/ASSUMPTIONS.md` (4,176) and
`docs/CHANGELOG.md` (16,647) -- into subject-organised files. The promise
attached to that move was **"nothing is deleted"**, and a promise about 25,000
lines of prose is worth exactly as much as the check behind it.

So this is the docs' `--self-check`: `--emit` photographs the doc set before
the move, and the default mode asserts every heading in that photograph is
still reachable afterwards.

Two things it deliberately does NOT do:

- **It does not compare bodies line by line.** Re-flowing a paragraph or
  moving a table under a new parent heading is the whole point of the
  refactor; a diff would fire on all of it. What it tracks instead is the
  heading inventory plus each section's non-blank line count, aggregated per
  source file -- so a heading that survives with its body gutted still shows
  up, as a shortfall in the total.
- **It does not police where a heading landed.** A heading may move to any
  file. Requiring a destination would mean writing the mapping down twice.

`# comments` inside fenced code blocks are not headings, and this file's own
subject matter guarantees there are hundreds of them -- every `bash` block in
`CLAUDE.md`. Fences are tracked, and text inside them is skipped.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# Where prose lives. Kept as globs so a file added during the refactor is
# picked up by the "after" side without editing this list.
DOC_GLOBS = (
    "*.md",
    "docs/**/*.md",
    "*/CLAUDE.md",
    "bat/*.md",
)

HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
FENCE = re.compile(r"^\s*(```|~~~)")


def doc_paths(root: Path = REPO) -> list[Path]:
    seen: dict[Path, None] = {}
    for pattern in DOC_GLOBS:
        for path in sorted(root.glob(pattern)):
            if path.is_file():
                seen.setdefault(path.resolve(), None)
    return list(seen)


def normalise(text: str) -> str:
    """Compare headings on their words, not their whitespace or emphasis.

    A heading that keeps its meaning through a re-flow -- `## The bar, and the
    check` becoming `## The bar, and the check` on a rewrapped line -- must
    compare equal, or the audit reports churn as loss.
    """
    text = re.sub(r"[`*_]", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip().casefold()


# A heading whose body legitimately shrank, because the section carried two
# subjects and only one of them kept the heading. Declared here with its
# reason rather than silently tolerated -- the same convention
# `tests/suite/test_rulers.py` and `test_suite_memory.py` use, and for the
# same reason: a bare exception list decays into "the checks that were
# annoying".
KNOWN_SPLITS = {
    normalise("Is an observation feature actually USED? Ablate it (2026-08-25)"):
        "two topics under one heading -- the ablation METHOD kept it, and "
        "the suite/memory rules underneath moved to tests/CLAUDE.md on "
        "2026-09-07 under a heading of their own",
}


def headings(path: Path) -> list[dict]:
    """Every heading in one file, with the size of the body under it."""
    raw = path.read_text(encoding="utf-8", errors="replace")
    out: list[dict] = []
    in_fence = False
    fence_mark = ""
    for lineno, line in enumerate(raw.splitlines(), 1):
        fence = FENCE.match(line)
        if fence:
            mark = fence.group(1)
            if not in_fence:
                in_fence, fence_mark = True, mark
            elif mark == fence_mark:
                in_fence = False
            continue
        if in_fence:
            if out:
                out[-1]["body"] += 1        # fenced content is body, not noise
            continue
        match = HEADING.match(line)
        if match:
            out.append({
                "file": path.relative_to(REPO).as_posix(),
                "level": len(match.group(1)),
                "text": match.group(2),
                "key": normalise(match.group(2)),
                "line": lineno,
                "body": 0,
            })
        elif line.strip() and out:
            out[-1]["body"] += 1
    return out


def inventory(root: Path = REPO) -> dict:
    files, entries = {}, []
    for path in doc_paths(root):
        found = headings(path)
        entries.extend(found)
        raw = path.read_text(encoding="utf-8", errors="replace")
        files[path.relative_to(root).as_posix()] = {
            "lines": len(raw.splitlines()),
            "nonblank": sum(1 for line in raw.splitlines() if line.strip()),
            "headings": len(found),
        }
    return {"files": files, "headings": entries}


def _totals(inv: dict) -> tuple[int, int, int]:
    files = inv["files"]
    return (len(files),
            sum(f["nonblank"] for f in files.values()),
            len(inv["headings"]))


def audit(before: dict, after: dict) -> int:
    """Returns a process exit code. Prints what moved and what vanished."""
    have = {}
    for entry in after["headings"]:
        have.setdefault(entry["key"], []).append(entry)

    missing = [e for e in before["headings"] if e["key"] not in have]

    # A heading can survive while its section is emptied. Compare the total
    # body under each heading KEY, since one section legitimately splits into
    # several.
    shrunk, declared = [], []
    for entry in before["headings"]:
        landed = have.get(entry["key"])
        if landed and entry["body"] > 20:
            now = sum(x["body"] for x in landed)
            if now < entry["body"] * 0.5:
                if entry["key"] in KNOWN_SPLITS:
                    declared.append((entry, now))
                else:
                    shrunk.append((entry, now))

    nf_b, nb_b, nh_b = _totals(before)
    nf_a, nb_a, nh_a = _totals(after)
    print(f"  files     {nf_b:>6} -> {nf_a}")
    print(f"  non-blank {nb_b:>6} -> {nb_a}   ({nb_a - nb_b:+d})")
    print(f"  headings  {nh_b:>6} -> {nh_a}   ({nh_a - nh_b:+d})")

    if missing:
        print(f"\n  {len(missing)} HEADINGS VANISHED:")
        for entry in missing[:60]:
            print(f"    {entry['file']}:{entry['line']}  "
                  f"{'#' * entry['level']} {entry['text']}")
        if len(missing) > 60:
            print(f"    ... and {len(missing) - 60} more")

    if shrunk:
        print(f"\n  {len(shrunk)} SECTIONS LOST MORE THAN HALF THEIR BODY:")
        for entry, now in shrunk[:40]:
            print(f"    {entry['text'][:70]}  {entry['body']} -> {now} lines")

    for entry, now in declared:
        print()
        print(f"  declared split: {entry['text'][:64]}")
        print(f"    {entry['body']} -> {now} lines; "
              f"{KNOWN_SPLITS[entry['key']]}")

    if not missing and not shrunk:
        print("\n  PASS every heading survived, no section was gutted")
        return 0
    return 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--emit", metavar="FILE", nargs="?", const="-",
                    help="write the heading inventory as JSON and stop")
    ap.add_argument("--baseline", metavar="FILE",
                    default=str(REPO / "docs" / ".doc_baseline.json"),
                    help="the inventory to audit against")
    args = ap.parse_args(argv)

    inv = inventory()
    if args.emit:
        text = json.dumps(inv, indent=1)
        if args.emit == "-":
            print(text)
        else:
            Path(args.emit).write_text(text, encoding="utf-8")
            nf, nb, nh = _totals(inv)
            print(f"wrote {args.emit}: {nf} files, {nh} headings, "
                  f"{nb} non-blank lines")
        return 0

    baseline = Path(args.baseline)
    if not baseline.exists():
        print(f"no baseline at {baseline}; run --emit {baseline} first")
        return 2
    before = json.loads(baseline.read_text(encoding="utf-8"))
    return audit(before, inv)


if __name__ == "__main__":
    sys.exit(main())
