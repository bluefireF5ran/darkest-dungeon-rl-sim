"""The lint gate, and what it is allowed to be.

`pyproject.toml` selects four rules -- F821, F811, E722, B006 -- and no more.
They were chosen by measuring: those four report zero across the repo, and the
next tier reports 280, almost all of it either deliberate (a re-export is an
unused import on purpose) or cosmetic.

Enabling them was not busywork. The first run found four real defects:

- `tests/training/test_train_wiring.py` called `get_action_masks` in its
  `__main__` block without importing it, so `python tests/training/
  test_train_wiring.py` -- the script path `README.md` advertises and
  `tests/suite/test_suite_hygiene.py` exists to protect -- died on a
  NameError. The hygiene guard could not see it: that guard checks the
  STRUCTURE of a `__main__` block, not whether the names in it resolve.
- `tests/env/test_provisions_combat.py` called `pytest.skip()` without
  importing pytest, so on any machine without the game installed a graceful
  skip would have been a crash. Invisible here, where the game exists.
- `tools/dbwin_capture.py` and `tools/game_trace.py` each annotate a return
  type with a module imported only inside the function. Never evaluated, so
  never a crash -- one `typing.get_type_hints()` call away from one.

That is the argument for keeping the gate narrow rather than wide: a rule set
nobody can pass gets suppressed wholesale, and then it catches nothing.
"""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

REPO = Path(__file__).resolve().parents[2]

#: Kept in step with `[tool.ruff.lint] select` in pyproject.toml. Duplicated on
#: purpose: if someone widens the config without deciding to, the test below
#: says so instead of silently enforcing something else.
EXPECTED_RULES = ["B006", "E722", "F811", "F821"]


def _ruff():
    exe = shutil.which("ruff")
    if exe:
        return [exe]
    try:
        subprocess.run([sys.executable, "-m", "ruff", "--version"],
                       capture_output=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        return None
    return [sys.executable, "-m", "ruff"]


def test_the_configured_rule_set_is_the_one_that_was_agreed():
    """A gate that quietly grows stops being the gate that was measured."""
    import tomllib
    cfg = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    got = sorted(cfg["tool"]["ruff"]["lint"]["select"])
    assert got == EXPECTED_RULES, (
        f"pyproject.toml selects {got}, this test expects {EXPECTED_RULES}. "
        "Widening the set is fine -- but do it deliberately, re-measure what "
        "it reports, and update EXPECTED_RULES with the new count.")
    print(f"  PASS the gate is exactly {got}")


def test_the_repo_passes_its_own_lint_gate():
    cmd = _ruff()
    if cmd is None:
        pytest.skip("ruff is not installed (pip install -e .[dev])")
    proc = subprocess.run(cmd + ["check", "--output-format", "concise", "."],
                          cwd=str(REPO), capture_output=True, text=True)
    assert proc.returncode == 0, (
        "ruff found something the gate forbids:\n" + proc.stdout + proc.stderr)
    print("  PASS ruff check is clean")


def test_the_packaging_metadata_is_coherent():
    """`pyproject.toml` must name packages that exist, and must NOT take over
    the pytest configuration.

    `pytest.ini` carries sixty lines of measured commentary about why the
    worker count is a memory dial; pytest prefers `pytest.ini` when both
    exist, and this keeps it that way on purpose.
    """
    import tomllib
    cfg = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))

    assert "pytest" not in cfg.get("tool", {}), (
        "pyproject.toml now configures pytest. pytest.ini is the config here "
        "and its comments are load-bearing; two sources would drift.")

    st = cfg["tool"]["setuptools"]
    for pkg in st["packages"]:
        assert (REPO / pkg / "__init__.py").exists(), (
            f"packages names {pkg!r}, which is not an importable package")
    for mod in st["py-modules"]:
        assert (REPO / f"{mod}.py").exists(), f"py-modules names missing {mod}"

    # tools/ and tests/ are run by path and have no __init__.py; installing
    # them would be wrong, and their absence here is the decision, not an
    # oversight.
    assert "tools" not in st["packages"] and "tests" not in st["packages"]
    print(f"  PASS packaging names {len(st['packages'])} real packages")


if __name__ == "__main__":
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            _fn()
    print("ok")
