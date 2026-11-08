"""The suite's own shape, checked statically.

The suite moved into subdirectories on 2026-09-07. Three properties that were
free while it was flat are now things that can silently stop being true, and
each has a failure mode that is quiet rather than red -- which is this repo's
characteristic bug, the one `test_suite_hygiene.py` already exists for.

1. **The path bootstrap depth.** A file one level under `tests/` reaches the
   repo root at `parents[2]`. Get it wrong and pytest still passes, because
   pytest puts the file's own directory on `sys.path` and the repo root is
   already there from every other test in the session. It fails only under
   `python tests/<area>/<file>.py` -- the documented script path, and the one
   nothing in CI exercises.

2. **Basename uniqueness.** `tests/` has no `__init__.py`, deliberately, so
   pytest's prepend import mode keys modules on their BASENAME. Two files
   called `test_camp.py` in different areas would collide at import, and the
   error names neither of them usefully.

3. **The guards can see the whole suite.** `test_suite_hygiene`,
   `test_suite_memory` and `tools/find_silent_tests` each walk `tests/`
   themselves. All three used a NON-recursive `glob("test_*.py")` before the
   move; left that way they would have kept passing while checking zero
   files. Three enforcement layers, silently off. So the guards get a guard.
"""
import ast
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

REPO = Path(__file__).resolve().parents[2]
TESTS = REPO / "tests"

AREAS = ("engine", "env", "data", "bridge", "campaign", "tools", "training", "suite")


def _test_files():
    return sorted(TESTS.rglob("test_*.py"))


def test_every_test_file_reaches_the_repo_root():
    """Statically evaluate each `sys.path.insert` and require the repo root.

    The expression is walked rather than string-matched, so `parent.parent`,
    `parents[1]` and `parents[2]` are all understood and the check says what
    it means: this file, from where it sits, resolves to the repo root.
    """
    def climb(node):
        """How many levels up a `Path(__file__)...` expression goes, or None."""
        levels = 0
        while True:
            if isinstance(node, ast.Attribute):
                if node.attr == "parent":
                    levels += 1
                elif node.attr == "resolve":
                    pass
                else:
                    return None
                node = node.value
            elif isinstance(node, ast.Subscript):
                if not (isinstance(node.value, ast.Attribute)
                        and node.value.attr == "parents"):
                    return None
                idx = node.slice
                if not (isinstance(idx, ast.Constant)
                        and isinstance(idx.value, int)):
                    return None
                levels += idx.value
                node = node.value.value
            elif isinstance(node, ast.Call):
                f = node.func
                name = getattr(f, "id", None) or getattr(f, "attr", None)
                if name == "resolve":
                    node = f.value
                elif name == "Path":
                    args = node.args
                    ok = (len(args) == 1 and isinstance(args[0], ast.Name)
                          and args[0].id == "__file__")
                    return levels if ok else None
                else:
                    return None
            else:
                return None

    offenders = []
    for path in _test_files():
        depth = len(path.relative_to(TESTS).parts) - 1      # 0 for tests/*.py
        want = depth + 1                                    # up to the repo root
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        reaches = False
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "insert"):
                continue
            for arg in node.args:
                for sub in ast.walk(arg):
                    if climb(sub) == want:
                        reaches = True
        if not reaches:
            offenders.append(
                f"{path.relative_to(REPO).as_posix()} (needs parents[{want}])")
    assert not offenders, (
        "these do not put the repo root on sys.path at the right depth, so "
        "`python <file>` fails even though pytest passes: "
        + "; ".join(offenders))
    print(f"  PASS all {len(_test_files())} files reach the repo root")


def test_test_module_basenames_are_unique():
    seen = {}
    clashes = []
    for path in _test_files():
        rel = path.relative_to(REPO).as_posix()
        if path.name in seen:
            clashes.append(f"{seen[path.name]} vs {rel}")
        seen[path.name] = rel
    assert not clashes, (
        "pytest keys modules on basename here (tests/ has no __init__.py, "
        "deliberately), so these collide at import: " + "; ".join(clashes))
    print(f"  PASS {len(seen)} unique basenames")


def test_the_suite_guards_are_not_blind():
    """Each guard that walks the suite must reach files in subdirectories.

    Checked by SOURCE, not by running them: a non-recursive glob is exactly
    the failure that leaves them green. If one of these grows a different
    discovery mechanism, this test should be updated to exercise it -- not
    deleted.
    """
    walkers = {
        "tests/suite/test_suite_hygiene.py": 'rglob("test_*.py")',
        "tests/suite/test_suite_memory.py": 'rglob("test_*.py")',
        "tools/find_silent_tests.py": 'rglob("test_*.py")',
    }
    for rel, needle in walkers.items():
        src = (REPO / rel).read_text(encoding="utf-8", errors="replace")
        assert needle in src, (
            f"{rel} no longer uses {needle}. The suite lives in "
            "subdirectories; a non-recursive glob keeps PASSING while "
            "checking nothing.")
        assert 'glob("test_*.py")' not in src.replace('rglob("test_*.py")', ""), (
            f"{rel} still has a non-recursive glob for test files")

    # And the recursion actually finds them: more than the flat root holds.
    from tests.suite import test_suite_hygiene as hyg
    seen = list(hyg._modules())
    assert len(seen) >= 150, (
        f"test_suite_hygiene sees only {len(seen)} modules; there are "
        f"{len(_test_files())}")
    subdirs = {Path(p).parent.name for p, _t in seen}
    assert subdirs & set(AREAS), (
        f"test_suite_hygiene sees no files in subdirectories: {subdirs}")
    print(f"  PASS the guards see {len(seen)} modules across {len(subdirs)} dirs")


def test_every_test_lives_in_a_known_area():
    """A file dropped at the root of `tests/` is not wrong, but it is
    unfiled -- and the reason the suite was 166 flat files is that nobody was
    ever told where to put the 167th."""
    stray = [p.name for p in TESTS.glob("test_*.py")]
    assert not stray, (
        f"these sit at the root of tests/ instead of an area "
        f"({', '.join(AREAS)}): {stray}")
    unknown = sorted({p.relative_to(TESTS).parts[0] for p in _test_files()}
                     - set(AREAS))
    assert not unknown, f"unknown test areas: {unknown}"
    print(f"  PASS every test file sits in one of {len(AREAS)} areas")


def test_no_new_source_file_becomes_a_monolith():
    """A soft ceiling, with today's monoliths named rather than hidden.

    The 2026-09-07 refactor deliberately did NOT split these; the list is the
    honest record of what was left. Adding to it should be a decision, not an
    accident.
    """
    CEILING = 2000
    GRANDFATHERED = {
        "bridge/act.py": "actuation + town planning + hardware probes; three modules",
        "engine/combat_engine.py":
            "was 6,015 lines; seven mixins came out on 2026-09-08 and it "
            "is 3,188. What is left is the turn loop, reset, action "
            "legalisation and resolution -- the parts that share the most "
            "state and split the worst",
        "tools/comp_ga.py": "the two-stage search",
        "bridge/run.py": "the six dungeon layers behind one poll loop",
        "bridge/town.py": "navigation + screen reads + actuation",
        "env/gym_env.py": "obs layout + reward + action mapping",
    }
    offenders = []
    for d in ("engine", "env", "data", "bridge", "campaign", "tools"):
        for path in sorted((REPO / d).rglob("*.py")):
            rel = path.relative_to(REPO).as_posix()
            n = len(path.read_bytes().decode("utf-8", "replace").splitlines())
            if n > CEILING and rel not in GRANDFATHERED:
                offenders.append(f"{rel} ({n} lines)")
    assert not offenders, (
        f"over the {CEILING}-line ceiling and not grandfathered: "
        + "; ".join(offenders)
        + ". Split it, or add it to GRANDFATHERED with the reason.")
    stale = [f for f in GRANDFATHERED if not (REPO / f).exists()]
    assert not stale, f"GRANDFATHERED names files that no longer exist: {stale}"
    print(f"  PASS no unlisted file over {CEILING} lines")


if __name__ == "__main__":
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            _fn()
    print("ok")
