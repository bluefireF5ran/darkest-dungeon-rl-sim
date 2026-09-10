"""The documentation's own shape.

`CLAUDE.md` reached 3,878 lines and was loaded in full on every session before
2026-09-07. Nothing stopped that happening, and nothing stopped the facts in
it going stale either -- when it was split, three claims it made about the repo
were wrong (`AGENTS.md` said 244 tests where there were 2,830; `README.md`
described a `-0.02` turn penalty and a `-3.0` death penalty where the constants
were `-0.10`/`-0.01` and `-50.0`), and its own repo layout said "38 of 55" test
files where there were 166.

Prose cannot be tested for truth. These are the three properties that CAN be
checked, and each one caught something real on the day it was written.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

REPO = Path(__file__).resolve().parents[2]

#: The always-loaded file. Its whole point is to be small.
CLAUDE_MD_BUDGET = 500

#: Directories that carry their own contracts file.
CONTRACT_DIRS = ("engine", "env", "data", "bridge", "tools", "tests", "metadata")

FENCE = re.compile(r"^\s*(```|~~~)")

#: A repo-relative path inside backticks. Deliberately narrow: it must start
#: with a real top-level directory of THIS repo, so prose like `--all-comps`
#: cannot be mistaken for a filename. `scripts/` is excluded on purpose -- it
#: is a directory of the GAME install, not of this repo.
PATHLIKE = re.compile(
    r"`((?:engine|env|data|bridge|tools|tests|docs|metadata|bat|archive"
    r"|captures|models|logs)/[A-Za-z0-9_./-]+)`")

#: Only these are files. Without this, `tools/dson.parse_file` and
#: `bridge/screen.display_is_live` -- module.function references, which this
#: project writes constantly -- read as missing paths.
FILE_SUFFIXES = {".py", ".md", ".json", ".darkest", ".csv", ".xml", ".zip",
                 ".txt", ".bat", ".ini", ".png", ".exe"}

#: Historical records. A path here was correct when it was written, and
#: `env/curriculum_env.py` really is gone. Rewriting the past to satisfy a
#: linter is worse than the lint. (Note the distinction: references to files
#: that still exist but MOVED were updated, because those still point at
#: something real.)
HISTORICAL = ("docs/assumptions/session-history.md", "docs/next-archive.md",
              "docs/changelog/")


def _docs():
    seen = {}
    for pattern in ("*.md", "docs/**/*.md", "*/CLAUDE.md", "bat/*.md"):
        for p in REPO.glob(pattern):
            if p.is_file():
                seen[p.resolve()] = None
    return sorted(seen)


def test_the_always_loaded_file_stays_small():
    n = len((REPO / "CLAUDE.md").read_text(encoding="utf-8").splitlines())
    assert n <= CLAUDE_MD_BUDGET, (
        f"CLAUDE.md is {n} lines, over the {CLAUDE_MD_BUDGET}-line budget. It "
        "is loaded in FULL on every session, so growth here is paid on every "
        "task. Contracts belong in the CLAUDE.md of the directory they "
        "constrain; measurements belong in docs/findings/.")
    print(f"  PASS CLAUDE.md is {n} lines (budget {CLAUDE_MD_BUDGET})")


def test_every_directory_with_contracts_has_them():
    missing = [d for d in CONTRACT_DIRS if not (REPO / d / "CLAUDE.md").exists()]
    assert not missing, (
        f"these directories have no CLAUDE.md, so Claude Code loads nothing "
        f"when it touches them: {missing}")
    print(f"  PASS all {len(CONTRACT_DIRS)} contract files present")


def test_no_doc_is_an_orphan():
    """Every doc must be reachable by name from another doc.

    A findings file nothing links to is a file nobody will read, which is the
    state the notebook was already in -- it was reachable only by scrolling.
    """
    docs = _docs()
    names = {p.name for p in docs}
    corpus = "\n".join(p.read_text(encoding="utf-8", errors="replace")
                       for p in docs)
    roots = {"CLAUDE.md", "README.md", "AGENTS.md"}
    orphans = []
    for p in docs:
        if p.name in roots:
            continue
        rel = p.relative_to(REPO).as_posix()
        # linked either by full path or, for uniquely-named files, by name
        if rel in corpus:
            continue
        if list(names).count(p.name) == 1 and p.name in corpus:
            continue
        orphans.append(rel)
    assert not orphans, (
        "nothing links to these, so nothing will find them: " + ", ".join(orphans))
    print(f"  PASS all {len(docs)} docs are reachable")


def test_a_path_named_in_a_doc_exists():
    """Catches rot of the kind the old CLAUDE.md carried for months.

    Only backticked, repo-rooted, concrete paths are checked -- no globs, no
    `<placeholders>` -- because a check with false positives gets deleted
    rather than fixed.
    """
    # Paths that are examples or deliberate references to things not in git.
    ALLOW = {
        "models/",          # gitignored
        "logs/",            # gitignored
        "archive/models/",
        "archive/logs/",
    }
    offenders = []
    for doc in _docs():
        rel_doc = doc.relative_to(REPO).as_posix()
        if any(rel_doc.startswith(h) for h in HISTORICAL):
            continue
        in_fence = False
        mark = ""
        for i, line in enumerate(
                doc.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            f = FENCE.match(line)
            if f:
                m = f.group(1)
                if not in_fence:
                    in_fence, mark = True, m
                elif m == mark:
                    in_fence = False
                continue
            if in_fence:
                continue                      # commands, not claims
            for m in PATHLIKE.finditer(line):
                rel = m.group(1)
                if any(rel.startswith(a) for a in ALLOW):
                    continue
                if "*" in rel or "<" in rel or rel.endswith("/"):
                    continue
                if Path(rel).suffix not in FILE_SUFFIXES:
                    continue
                if not (REPO / rel).exists():
                    offenders.append(
                        f"{doc.relative_to(REPO).as_posix()}:{i} -> {rel}")
    assert not offenders, (
        f"{len(offenders)} paths named in docs do not exist:\n  "
        + "\n  ".join(offenders[:40]))
    print("  PASS every concrete path named in prose exists")


if __name__ == "__main__":
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            _fn()
    print("ok")
