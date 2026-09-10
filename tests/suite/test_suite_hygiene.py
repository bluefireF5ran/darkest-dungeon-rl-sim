"""Two ways a test can stop running without anything going red.

Both were live in this repo on 2026-08-27, and neither is visible to pytest, to
`tools/find_silent_tests.py` or to a reader skimming a 1500-line file. They are
static properties, so they are checked statically -- importing 123 test modules
to find out would cost exactly what `test_suite_memory.py` exists to protect.

1. **A duplicate function name.** Python keeps the last `def`; the earlier one
   is dead code that pytest never collects. `tests/tools/test_comp_ga.py` had a
   112-line block pasted twice, so EIGHT tests -- the two-trinkets rule, the
   inert-trinket probe, the archetype cap, the seeding spread, the canonical
   output order -- had silently stopped running. The two copies happened to be
   byte-identical, so nothing was lost that day. The next such paste will not
   be so lucky: the live copy would be whichever was pasted second, which is
   not necessarily the one that was edited.

2. **A `__main__` runner that is not the last statement.** Every test file here
   is also runnable as a plain script -- CLAUDE.md advertises
   `python tests/data/test_loader.py` and `tests/_fixtures.py` explains that this is
   why the shared store is a module and not a fixture. The common runner
   iterates `globals()` and calls everything named `test_*`, and at the moment
   the guard executes, only the functions defined ABOVE it exist. Four files
   had tests below theirs: 6 in test_combat, 15 in test_gaps, 5 in
   test_reinforcements, 12 in test_skill_mechanics. Thirty-eight tests that
   pytest ran and the documented script path silently skipped.

   A guard that calls `pytest.main([__file__])` is exempt: it re-imports the
   module, so it sees the whole file wherever it sits.
"""
import ast
import collections
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

# rglob, not glob: the suite lives in subdirectories since 2026-09-07, and a non-recursive glob here would keep PASSING while checking nothing.
TESTS = pathlib.Path(__file__).resolve().parents[1]


def _modules():
    for path in sorted(TESTS.rglob("test_*.py")):
        try:
            yield path, ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue


def test_no_test_module_defines_the_same_function_twice():
    """The shadowed-test bug. Named per file so the failure says where."""
    offenders = {}
    for path, tree in _modules():
        seen = collections.defaultdict(list)
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                seen[node.name].append(node.lineno)
        dupes = {name: lines for name, lines in seen.items() if len(lines) > 1}
        if dupes:
            offenders[path.name] = dupes
    assert not offenders, (
        f"these test modules define a function more than once: {offenders}. "
        "Python keeps the LAST one, so every earlier definition is dead code "
        "pytest never collects -- and if the copies have diverged, the live "
        "one is whichever was pasted second. Delete the duplicate.")
    print("  PASS no test module shadows one of its own functions")


def _guard(tree):
    for i, node in enumerate(tree.body):
        if isinstance(node, ast.If) and "__main__" in ast.dump(node.test):
            return i, node
    return None, None


def _runner_reads_globals(node) -> bool:
    """A runner that enumerates `globals()` can only see what is already
    defined. One that re-enters pytest cannot miss anything."""
    return any(isinstance(sub, ast.Name) and sub.id == "globals"
               for sub in ast.walk(node))


def test_a_globals_style_main_runner_is_the_last_thing_in_its_file():
    offenders = {}
    for path, tree in _modules():
        index, node = _guard(tree)
        if node is None or index == len(tree.body) - 1:
            continue
        if not _runner_reads_globals(node):
            continue                      # pytest.main re-imports; position is free
        hidden = [n.name for n in tree.body[index + 1:]
                  if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                  and n.name.startswith("test_")]
        if hidden:
            offenders[path.name] = hidden
    assert not offenders, (
        f"these files define tests BELOW a `globals()`-style __main__ runner, "
        f"so `python tests/<file>.py` silently skips them: {offenders}. Move "
        "the guard to the end of the file.")
    print("  PASS every globals() runner sits below every test it must run")


def test_the_script_path_and_pytest_agree_on_what_a_file_contains():
    """The invariant behind both checks above, stated once: for every module,
    the number of distinct `test_*` functions pytest would collect equals the
    number its own script runner can reach."""
    for path, tree in _modules():
        defined = [n.name for n in tree.body
                   if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                   and n.name.startswith("test_")]
        assert len(defined) == len(set(defined)), path.name
        index, node = _guard(tree)
        if node is None or not _runner_reads_globals(node):
            continue
        reachable = [n.name for n in tree.body[:index]
                     if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                     and n.name.startswith("test_")]
        assert set(reachable) == set(defined), (
            f"{path.name}: pytest sees {len(defined)} tests, the script runner "
            f"reaches {len(reachable)}")
    print("  PASS pytest and the script runner see the same tests")


def test_no_test_reads_a_BANKED_FAILURE_frame():
    """`captures/_*.png` is scratch, and a fixture must never be scratch.

    `ActRunner._bank_frame` writes one fixed filename per KIND of failure and
    overwrites it every time, and `.gitignore` line 44 excludes the lot. So a
    test pointed at one grades whatever the last live run happened to be
    looking at.

    That is not hypothetical: `test_curiopanel.py` used `_curio_approach.png`
    as its "no panel is open" control, an overnight run replaced it with a
    different corridor on 2026-09-05, and the file went red with no source
    change. The repair was to copy the frame to a tracked name.

    Only string literals are checked, which is the whole of how a test names a
    fixture; a prose mention in a docstring is fine and is how the banked shots
    get explained.
    """
    import ast

    offenders = []
    for path in sorted(TESTS.rglob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
                continue
            name = node.value.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
            if name.startswith("_") and name.endswith(".png"):
                offenders.append(f"{path.name}:{node.lineno} {node.value}")
    assert not offenders, (
        "these name a gitignored banked-failure frame, which any live run "
        "overwrites: " + "; ".join(offenders))
    print("  PASS no test depends on a banked failure frame")


if __name__ == "__main__":
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            _fn()
