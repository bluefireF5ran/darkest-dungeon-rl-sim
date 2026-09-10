"""The suite must not load torch during COLLECTION.

Every xdist worker collects the WHOLE suite before it runs anything, so a
single module-level `import torch` anywhere under `tests/` is paid by every
worker, not just the one that runs that file. Measured on 2026-08-27:

    collection with torch      626 MB   ->  17 workers, 7.4 GB peak
    collection without torch   149 MB   ->  16 workers, 3.2 GB peak

7.4 GB was enough to crash a 32 GB machine whenever anything else was running,
and it is why `train.py`, `tools/ab_compare.py`, `tools/ablate_forecast.py` and
`eval_expedition.py` all import sb3_contrib lazily now. This file is the
tripwire for that, and it is STATIC on purpose: importing 121 test modules to
find out would cost exactly the memory it is trying to protect.

The rule it enforces has three parts:

1. No test module may reach torch/sb3 through a module-level import chain
   unless it carries an `xdist_group` mark. The mark pins it to one worker, so
   the cost is paid once rather than sixteen times.
2. Every test FUNCTION that imports torch/sb3 in its own body carries
   `@pytest.mark.xdist_group("torch")`, so the run-time load lands on one
   worker too. There are six of them in 1819 tests.
3. The mark goes on the TEST, never on the module. A module-level `pytestmark`
   pins every test in the file to one worker, and that is what the suite's wall
   clock is made of: `test_live_bridge.py` is 68 s of work with exactly ONE
   test that touches torch, and pinning the file made those 68 s the critical
   path of a 97 s suite. Marked per test, its other ~200 tests scatter.

Part 2 replaced a warning. The old version could only look at module-level
imports and had to guess about run-time loads, so it printed a note and moved
on -- and under that note five of six marks were stale, costing wall clock for
nothing. A function-body import IS statically visible; measuring it directly
(a `sys.settrace` pass over the suite) confirmed the static answer exactly, so
this is now an assertion.
"""
import ast
import functools
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

ROOT = pathlib.Path(__file__).resolve().parents[2]
# rglob, not glob: the suite lives in subdirectories since 2026-09-07, and a non-recursive glob here would keep PASSING while checking nothing.
TESTS = ROOT / "tests"

# The third-party roots that pull torch in.
HEAVY = {"torch", "sb3_contrib", "stable_baselines3"}


def _parse(path: pathlib.Path):
    """Parse one file. Deliberately NOT cached: what is expensive is re-walking
    the same repo module once per test file that reaches it -- and
    `_module_level_imports` caches the small frozenset it derives, which is
    what kills the repeat work without holding ~120 syntax trees alive. Caching
    the tree instead was 7.8 s -> 0.6 s and +85 MB of retained AST in every
    xdist worker, which is the wrong trade in a file whose whole subject is the
    suite's memory."""
    try:
        return ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return None


@functools.lru_cache(maxsize=None)
def _module_level_imports(path: pathlib.Path) -> frozenset:
    """Top-level import targets only -- an import inside a function is exactly
    the fix this file exists to encourage, so it must not count against it."""
    tree = _parse(path)
    if tree is None:
        return frozenset()
    out = set()
    for node in tree.body:                       # tree.body == module level
        if isinstance(node, ast.Import):
            out.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            out.add(node.module.split(".")[0])
            out.add(node.module)
    return frozenset(out)


@functools.lru_cache(maxsize=None)
def _repo_module_path(name: str) -> pathlib.Path | None:
    for cand in (ROOT / (name.replace(".", "/") + ".py"),
                 ROOT / name.replace(".", "/") / "__init__.py"):
        if cand.exists():
            return cand
    return None


_REACHES_HEAVY: dict[str, bool] = {}


def _reaches_heavy(name: str, seen: set[str]) -> bool:
    """Does this repo module reach torch/sb3 through MODULE-LEVEL imports?

    `seen` still breaks import cycles within one walk. The memo is only
    consulted and only written for a TRUE answer, which is safe under cycles: a
    False can be an artefact of where a particular walk started (the cycle was
    cut), a True never is -- it names a real import chain.
    """
    if name in seen:
        return False
    if _REACHES_HEAVY.get(name):
        return True
    seen.add(name)
    if name.split(".")[0] in HEAVY:
        _REACHES_HEAVY[name] = True
        return True
    path = _repo_module_path(name)
    if path is None:
        return False
    hit = any(_reaches_heavy(dep, seen) for dep in _module_level_imports(path))
    if hit:
        _REACHES_HEAVY[name] = True
    return hit


def _marked(path: pathlib.Path) -> bool:
    """Does the module carry `pytestmark = pytest.mark.xdist_group(...)`?"""
    src = path.read_text(encoding="utf-8")
    return "pytestmark" in src and "xdist_group" in src


def _function_body_imports(node: ast.FunctionDef) -> set[str]:
    """Import targets anywhere INSIDE a function -- the lazy-import pattern."""
    out = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Import):
            out.update(a.name.split(".")[0] for a in sub.names)
        elif isinstance(sub, ast.ImportFrom) and sub.module:
            out.add(sub.module.split(".")[0])
    return out


def _has_group_mark(node: ast.FunctionDef) -> bool:
    for deco in node.decorator_list:
        call = deco.func if isinstance(deco, ast.Call) else deco
        if isinstance(call, ast.Attribute) and call.attr == "xdist_group":
            return True
    return False


def _heavy_test_functions():
    """(file, test name, has the mark) for every test that imports torch/sb3."""
    out = []
    for f in sorted(TESTS.rglob("test_*.py")):
        if f.name == pathlib.Path(__file__).name:
            continue
        tree = _parse(f)
        if tree is None:
            continue
        for node in tree.body:
            if not isinstance(node, ast.FunctionDef) or not node.name.startswith("test_"):
                continue
            if _function_body_imports(node) & HEAVY:
                out.append((f.name, node.name, _has_group_mark(node)))
    return out


def _heavy_test_files():
    heavy, marked = [], []
    for f in sorted(TESTS.rglob("test_*.py")):
        if f.name == pathlib.Path(__file__).name:
            continue
        reaches = any(_reaches_heavy(dep, set()) for dep in _module_level_imports(f))
        if reaches:
            heavy.append(f)
        if _marked(f):
            marked.append(f)
    return heavy, marked


def test_no_test_module_drags_torch_into_collection_unmarked():
    """The failure this catches is silent: the suite still passes, it just
    quietly costs 4 GB more and takes the machine down with a training job."""
    heavy, _ = _heavy_test_files()
    unmarked = [f.name for f in heavy if not _marked(f)]
    assert not unmarked, (
        "these test modules reach torch/sb3 through a MODULE-LEVEL import and "
        f"carry no xdist_group mark: {unmarked}. Either import it inside the "
        "test that needs it (preferred -- see train.py and tools/ab_compare.py "
        "for the pattern), or add "
        '`pytestmark = pytest.mark.xdist_group(\"torch\")` so the cost is paid '
        "by one worker instead of every one. See pytest.ini.")
    print("  PASS no unmarked test module pulls torch into collection")


def test_the_lazy_import_fix_is_still_in_place():
    """The four modules whose module-level sb3 import cost 454 MB x 16 workers.
    Named individually because a revert here is invisible -- nothing fails, the
    suite just gets heavy again."""
    for name in ("train", "tools.ab_compare", "tools.ablate_forecast",
                 "eval_expedition"):
        path = _repo_module_path(name)
        assert path is not None, name
        top = _module_level_imports(path)
        offending = sorted(t for t in top if t.split(".")[0] in HEAVY)
        assert not offending, (
            f"{name} imports {offending} at module level again. Every xdist "
            "worker collects the whole suite, so this is paid ~16 times over; "
            "move it inside the function that uses it.")
    print("  PASS train/ab_compare/ablate_forecast/eval_expedition stay lazy")


def test_every_torch_test_is_pinned_to_one_worker():
    """A run-time torch load is 454 MB in whichever worker takes the test. Six
    tests need it; scattered, they can put it in six workers at once."""
    heavy = _heavy_test_functions()
    assert heavy, "no test imports torch at all -- has HEAVY gone stale?"
    unpinned = [f"{fn}::{name}" for fn, name, marked in heavy if not marked]
    assert not unpinned, (
        f"these tests import torch/sb3 in their body and carry no group mark: "
        f"{unpinned}. Add `@pytest.mark.xdist_group(\"torch\")` to the test "
        "itself -- NOT a module-level `pytestmark`, which pins the whole file.")
    print(f"  PASS all {len(heavy)} torch-loading tests are pinned")


def test_a_module_level_mark_is_declared_and_justified():
    """A module-level `pytestmark` serialises every test in the file onto one
    worker, and that is right for exactly one reason: the file's tests SPIKE, so
    running them concurrently is what sets the suite's peak.

    It is wrong for torch. `test_live_bridge.py` is 68 s of capture decoding
    with ONE test that touches torch, and pinning the file made those 68 s the
    critical path of a 97 s suite. A torch test gets its own mark; a spiky file
    gets the module's.

    So the marks are enumerated here with their measured spike. A new one is a
    deliberate act, not something that drifts in.
    """
    # file -> the per-test transient spike above baseline that justifies it,
    # measured by sampling RSS at 20 ms inside a serial run. Re-measured
    # 2026-08-27 after the DSON fix; `test_mappanel` fell 92 -> 33 because the
    # decode it was spiking on got 1.8x cheaper, and it is kept marked because
    # 33 MB x 8 workers is still the wrong way to spend the peak. The largest
    # single spike in the suite is NOT here and should not be:
    # `test_expedition_sequence_env.py::test_check_env_gymnasium_api_compliance`
    # at 468 MB is a torch load, and a torch load takes a per-TEST mark.
    JUSTIFIED = {
        "test_pe_anchors.py": 385,
        "test_screen.py": 195,
        "test_mappanel.py": 33,
        "test_curiopanel.py": 19,
    }
    found = {f.name for f in sorted(TESTS.rglob("test_*.py"))
             if f.name != pathlib.Path(__file__).name and _marked(f)}
    undeclared = sorted(found - set(JUSTIFIED))
    assert not undeclared, (
        f"module-level xdist_group marks not declared here: {undeclared}. "
        "Pinning a whole file costs wall clock for every test in it that does "
        "not need pinning -- if the reason is a torch import, mark the TEST "
        "instead. If the reason is a memory spike, measure it and add it above.")
    missing = sorted(set(JUSTIFIED) - found)
    assert not missing, (
        f"these files lost their xdist_group mark: {missing}. Each spikes by "
        f"{ {k: JUSTIFIED[k] for k in missing} } MB per test; unpinned, up to "
        "16 of those land at once.")
    print(f"  PASS {len(found)} module-level marks, all declared")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
