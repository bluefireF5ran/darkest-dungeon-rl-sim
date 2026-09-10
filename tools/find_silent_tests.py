"""Which tests assert NOTHING? — the ruler for a whole class of stale test.

A test that only ever fails by raising is not a test, it is a smoke run wearing
a test's name. This project accumulated nine of them, and the way they got there
is always one of two moves:

- **print instead of assert.** Several files here still carry a `__main__`
  runner and a `print("  PASS ...")` tail from before pytest, and a few of those
  functions never grew the assertion the print was standing in for.
- **an assertion behind a guard that stopped being true.**
  `test_a_detour_never_appears_for_a_room_already_on_the_route` looped over
  `plan.detours`, which is empty for the map it used, so the assertion inside
  never executed. `test_tutorial_scenario` iterated `store.comps`, emptied when
  the CSV was retired on 2026-08-23. Both passed for months.

Static analysis cannot see either one — the assertion is right there in the
source. So this measures it at RUN time: a `sys.settrace` line hook scoped to
`tests/` records which lines each test actually executed, and the executed set
is intersected with every assertion line the AST can find. Empty intersection =
the test asserted nothing on this run.

    python tools/find_silent_tests.py            # the whole suite (~3 min)
    python tools/find_silent_tests.py tests/bridge/test_route.py

Two rules that make the answer usable:

- **The trace function returns None for every frame outside `tests/`**, so
  engine code costs one dict lookup per call event and nothing per line. The
  suite runs ~2x slower under it, not 50x.
- **A silent test is a FINDING, not automatically a bug.** `check_env` asserts
  inside stable_baselines3, which this cannot see. Anything genuinely exempt
  belongs in `EXPECTED_SILENT` with the reason written down, and the tool exits
  non-zero only on a silent test that is not listed there.
"""

from __future__ import annotations

import argparse
import ast
import glob
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

# rglob, not glob: the suite lives in subdirectories since 2026-09-07, and a non-recursive glob here would keep PASSING while checking nothing.
REPO = Path(__file__).resolve().parent.parent

# nodeid substring -> why asserting nothing is correct here.
EXPECTED_SILENT = {
    "test_check_env_gymnasium_api_compliance":
        "check_env() raises inside stable_baselines3; the assertion is external",
}

PLUGIN = r'''
import json, os, sys
import pytest

_BASE = os.environ["ASSERT_TRACE_OUT"]
_WID = os.environ.get("PYTEST_XDIST_WORKER", "")
OUT = _BASE.replace(".json", f".{_WID}.json") if _WID else _BASE

_tests_dir = None
_acc = None
_seen = {}


def _local(frame, event, arg):
    if event == "line" and _acc is not None:
        _acc.setdefault(os.path.basename(frame.f_code.co_filename), set()).add(frame.f_lineno)
    return _local


def _global(frame, event, arg):
    if _tests_dir and frame.f_code.co_filename.startswith(_tests_dir):
        return _local
    return None


def pytest_configure(config):
    global _tests_dir
    _tests_dir = os.path.join(os.path.abspath(str(config.rootdir)), "tests") + os.sep


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item):
    global _acc
    _acc = _seen.setdefault(item.nodeid, {})
    sys.settrace(_global)
    try:
        yield
    finally:
        sys.settrace(None)
        _acc = None


def pytest_sessionfinish(session, exitstatus):
    sys.settrace(None)
    with open(OUT, "w", encoding="utf-8", newline="\n") as fh:
        json.dump({k: {f: sorted(v) for f, v in d.items()} for k, d in _seen.items()}, fh)
'''

# Names that constitute an assertion when called. `raises`/`warns` are the
# context-manager forms; the bare `assert` statement is handled separately.
ASSERTING_CALLS = {"fail", "raises", "warns", "approx",
                   "assertEqual", "assertTrue", "assertRaises"}


def assertion_lines() -> dict[str, set[int]]:
    """Every line in tests/ that constitutes an assertion, by file basename."""
    out: dict[str, set[int]] = {}
    for path in sorted((REPO / "tests").rglob("test_*.py")):
        lines: set[int] = set()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Assert):
                lines.add(node.lineno)
            elif isinstance(node, ast.Call):
                func = node.func
                name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
                if name in ASSERTING_CALLS:
                    lines.add(node.lineno)
        out[path.name] = lines
    return out


def run(targets: list[str], workdir: Path) -> dict[str, dict[str, set[int]]]:
    plugin_dir = workdir / "plugin"
    plugin_dir.mkdir()
    (plugin_dir / "assert_trace.py").write_text(PLUGIN, encoding="utf-8", newline="\n")
    out_base = workdir / "trace.json"

    env = dict(os.environ)
    env["PYTHONPATH"] = str(plugin_dir) + os.pathsep + env.get("PYTHONPATH", "")
    env["ASSERT_TRACE_OUT"] = str(out_base)

    cmd = [sys.executable, "-m", "pytest", *(targets or ["tests/"]), "-q", "-p", "assert_trace"]
    print(f"$ {' '.join(cmd)}\n")
    proc = subprocess.run(cmd, cwd=REPO, env=env)
    if proc.returncode not in (0, 1):
        sys.exit(f"pytest exited {proc.returncode}; nothing to report")

    merged: dict[str, dict[str, set[int]]] = {}
    for path in glob.glob(str(out_base).replace(".json", "*.json")):
        for nodeid, files in json.load(open(path, encoding="utf-8")).items():
            per = merged.setdefault(nodeid, {})
            for name, lines in files.items():
                per.setdefault(name, set()).update(lines)
    return merged


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("targets", nargs="*", help="pytest targets (default: tests/)")
    args = ap.parse_args()

    with tempfile.TemporaryDirectory() as tmp:
        traced = run(args.targets, Path(tmp))
    if not traced:
        sys.exit("no tests were traced -- did the plugin load?")

    expected = assertion_lines()
    silent = [nodeid for nodeid, files in traced.items()
              if not any(lines & expected.get(name, set()) for name, lines in files.items())]

    unexplained = []
    print(f"\n{len(silent)} of {len(traced)} tests executed ZERO assertion lines\n")
    for nodeid in sorted(silent):
        why = next((r for k, r in EXPECTED_SILENT.items() if k in nodeid), None)
        if why:
            print(f"  ok      {nodeid}\n            {why}")
        else:
            unexplained.append(nodeid)
            print(f"  SILENT  {nodeid}")

    if unexplained:
        print(f"\n{len(unexplained)} test(s) assert nothing and are not declared exempt.")
        print("Each one can only fail by raising. Give it the assertion its name promises,")
        print("or add it to EXPECTED_SILENT with the reason.")
        sys.exit(1)
    print("\nEvery test asserted something.")


if __name__ == "__main__":
    main()
