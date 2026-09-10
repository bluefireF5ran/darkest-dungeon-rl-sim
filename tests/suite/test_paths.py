"""`data/paths.py` is the only place the two absolute roots may be written.

Before 2026-09-07 they were defined in `env/gym_env.py` and re-declared
verbatim in about a hundred files -- 73 test files repeated `GAME_DIR`, 43
repeated `CSV_DIR`, and **31 of those hardcoded the absolute string
`F:\\Personal_Fran\\...\\SIM`**, which pinned the whole repo to one drive
letter from inside its own test suite.

Two things are checked here, and the second is the one that matters in a year:

1. The values did not change when they were centralised. A refactor that
   silently pointed the loader at a different install would not fail loudly --
   `CsvLoader.find_csv` returns `None` for a missing directory rather than
   raising, so the store would come back EMPTY and the failures would appear
   somewhere else entirely.
2. Nothing re-declares them. That is what stops the sprawl growing back.
"""
import ast
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

REPO = Path(__file__).resolve().parents[2]

# The literals as they stood in env/gym_env.py before centralisation.
HISTORICAL_GAME_DIR = Path(
    r"D:\Program Files (x86)\Steam\steamapps\common\DarkestDungeon")

# Where a re-declaration would be a regression. `tests/` is in this list: its
# ~100 copies came out in the same pass that moved the suite into
# subdirectories, and 31 of them had hardcoded the absolute repo path.
PRODUCTION = ("engine", "env", "data", "bridge", "tools", "tests")


def test_the_values_did_not_change_when_they_were_centralised():
    from data.paths import CSV_DIR, GAME_DIR, REPO_ROOT
    assert GAME_DIR == HISTORICAL_GAME_DIR, (
        "GAME_DIR moved. Every number this project has ever measured was "
        "parsed out of the install at the old path.")
    assert CSV_DIR == REPO_ROOT == REPO, (
        "CSV_DIR is the repo ROOT (not metadata/), and it is now derived from "
        "__file__ rather than hardcoded -- which is what unpins the drive.")
    print("  PASS the centralised roots equal the literals they replaced")


def test_gym_env_still_exports_both_names():
    """~33 files do `from env.gym_env import GAME_DIR`. It re-exports now, and
    that has to keep working -- and keep being the SAME object."""
    from env import gym_env
    from data import paths
    assert gym_env.GAME_DIR is paths.GAME_DIR
    assert gym_env.CSV_DIR is paths.CSV_DIR
    print("  PASS env.gym_env re-exports data.paths")


def test_the_environment_overrides_win():
    """The override is the whole point: without it the repo is pinned to one
    machine. Checked in a subprocess because the module reads os.environ at
    import time, and re-importing it in-process would leak into other tests."""
    env = dict(os.environ)
    env["DDSIM_GAME_DIR"] = r"X:\somewhere\else"
    env["DDSIM_CSV_DIR"] = r"Y:\also\elsewhere"
    out = subprocess.run(
        [sys.executable, "-c",
         "import data.paths as p; print(p.GAME_DIR); print(p.CSV_DIR)"],
        cwd=str(REPO), env=env, capture_output=True, text=True, check=True)
    lines = out.stdout.strip().splitlines()
    assert lines[0] == r"X:\somewhere\else", lines
    assert lines[1] == r"Y:\also\elsewhere", lines
    print("  PASS DDSIM_GAME_DIR / DDSIM_CSV_DIR override the defaults")


def _paths_aliases(tree):
    """Names this module imported from `data.paths`."""
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "data.paths":
            out.update(a.asname or a.name for a in node.names)
    return out


def _redeclarations(path):
    """`GAME_DIR = <something not derived from data.paths>`, by AST.

    Derived assignments are fine and three tools need one: they consume the
    roots as `str`, so they do `GAME_DIR = str(_GAME_DIR)`. That still has a
    single definition behind it, which is the property being guarded. What is
    forbidden is a value that owes nothing to `data.paths` -- a fresh literal,
    or another `Path(__file__).parents[2]` walk.

    Checked by AST rather than regex so the docstring of `data/paths.py` and
    any comment quoting the old path cannot trip it. A guard with false
    positives gets deleted.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return []
    aliases = _paths_aliases(tree)
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        names = [t.id for t in targets
                 if isinstance(t, ast.Name) and t.id in {"GAME_DIR", "CSV_DIR"}]
        if not names or node.value is None:
            continue
        referenced = {n.id for n in ast.walk(node.value)
                      if isinstance(n, ast.Name)}
        if referenced & aliases:
            continue
        out.extend((n, node.lineno) for n in names)
    return out


def test_nothing_in_production_redeclares_the_roots():
    offenders = []
    for d in PRODUCTION:
        for path in sorted((REPO / d).rglob("*.py")):
            if path.resolve() == (REPO / "data" / "paths.py").resolve():
                continue
            for name, lineno in _redeclarations(path):
                offenders.append(
                    f"{path.relative_to(REPO).as_posix()}:{lineno} {name}")
    assert not offenders, (
        "these assign GAME_DIR/CSV_DIR instead of importing them from "
        "data.paths: " + "; ".join(offenders))
    print(f"  PASS no re-declaration across {'/'.join(PRODUCTION)}")


def test_no_source_file_spells_the_install_path_itself():
    """The literal belongs in exactly one file. A second copy is how the two
    drift apart without anything failing."""
    needle = re.compile(r"steamapps.{0,3}common.{0,3}DarkestDungeon")
    # Two files may spell it: the one that defines it, and this one, which
    # pins the historical value so a refactor cannot quietly repoint the
    # loader at a different install.
    allowed = {(REPO / "data" / "paths.py").resolve(),
               Path(__file__).resolve()}
    offenders = []
    for d in PRODUCTION:
        for path in sorted((REPO / d).rglob("*.py")):
            if path.resolve() in allowed:
                continue
            for i, line in enumerate(
                    path.read_text(encoding="utf-8", errors="replace")
                        .splitlines(), 1):
                if needle.search(line):
                    offenders.append(
                        f"{path.relative_to(REPO).as_posix()}:{i}")
    assert not offenders, (
        "the install path is spelled outside data/paths.py: "
        + "; ".join(offenders))
    print("  PASS the install path is spelled in exactly one file")


if __name__ == "__main__":
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            _fn()
    print("ok")
