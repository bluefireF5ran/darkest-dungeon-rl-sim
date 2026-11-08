"""Which layer may import which, pinned.

The dependency direction here has been right almost by accident: `engine/`
imports `data.types` and nothing else, `data/` is the bottom, and `bridge/`
and `tools/` sit on top and may reach anywhere. Nothing enforced it, and it
had already started to slip -- `tools/dson.py` was a save-file PARSER, which
is a data-layer job in every way that matters, and four `data/` modules were
reaching up into `tools/` to use it. It moved to `data/dson.py` on 2026-09-08
and `tools/dson.py` is now just the documented CLI.

Why this matters beyond tidiness: the layering is what makes `engine/` cheap
to import and cheap to test. `env/` pulls in gymnasium, `bridge/` pulls in
ctypes and numpy and a Win32 window, `tools/` pulls in torch. An `engine/`
that imported upward would drag all of that into every test that touches a
combat, and the suite's memory budget is already the thing that took this
machine down once.

**The command line is exempt, deliberately.** `data/hero_skills.py` and
`data/hero_value.py` both read a live save in their CLI demo, which needs
`bridge.town_state`. That is a dependency of the *command*, not of the
*module*: nothing imports a `__main__` block, so it cannot drag anything into
anyone's import graph. Policing it would push those demos out of the file that
explains them, for no benefit.

The exemption covers the `__main__` block and the top-level functions it
CALLS, because both of those files use the ordinary idiom of putting the CLI
in `main()`. It does not extend further: a dependency parked in a helper that
`__main__` does not name is still a violation.
"""
import ast
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

REPO = Path(__file__).resolve().parents[2]

#: layer -> what its importable code may import from this repo.
#: `bridge` and `tools` are the top of the stack and are unconstrained; they
#: are listed so the test fails loudly if a new top-level package appears and
#: nobody decides where it sits.
ALLOWED = {
    "data": set(),
    "engine": {"data"},
    "env": {"data", "engine"},
    "bridge": {"data", "engine", "env", "tools", "train", "eval_expedition"},
    "tools": {"data", "engine", "env", "bridge", "campaign", "train", "eval_expedition"},
    #: The campaign simulator (docs/CAMPAIGN_SIM.md) sits beside `bridge`:
    #: it reuses the bridge's pure decide layers over `TownState`, so the
    #: simulated Hamlet decides exactly as the live player does.
    "campaign": {"data", "engine", "env", "bridge", "eval_expedition"},
}

REPO_MODULES = set(ALLOWED) | {"train", "eval_expedition"}

#: Edges that are wrong in principle and kept anyway, each with the reason.
#: A bare allow-list decays into "the rules that were inconvenient", so every
#: entry says what it would cost to remove.
DECLARED = {
    ("data/tutorial_loader.py", "engine"):
        "`reset_options()` builds the dict `ExpeditionSequenceEnv.reset()` "
        "takes, which means it has to name `CarryOverHeroState`. It is an "
        "ENV-layer adapter that happens to live on a data object. The clean "
        "fix would be moving `CarryOverHeroState` into `data/types.py`, but "
        "it holds `DotInstance` fields, so that would drag the whole "
        "combat-state vocabulary down a layer -- a bigger smell than the one "
        "it cures. The import is function-local, so it costs nothing at "
        "import time.",
}


def _imports_outside_main(path):
    """Repo modules this file imports, ignoring anything under `__main__`.

    Function-local imports DO count: a lazy import is still a dependency, and
    several of the ones this rule exists to catch were lazy.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return set()

    main_blocks = [n for n in tree.body
                   if isinstance(n, ast.If) and "__main__" in ast.dump(n.test)]
    skip = {id(sub) for block in main_blocks for sub in ast.walk(block)}

    # ...and whatever those blocks CALL. `data/hero_skills.py` and
    # `data/hero_value.py` both put their CLI in a top-level `main()` and call
    # it from the guard, which is the ordinary idiom -- the imports are still
    # command-line dependencies, they just sit one `def` away. Only functions
    # the guard actually names are exempt, so this cannot be used to park a
    # real dependency in a helper.
    called = set()
    for block in main_blocks:
        for sub in ast.walk(block):
            if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name):
                called.add(sub.func.id)
    for node in tree.body:
        if (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name in called):
            skip.update(id(sub) for sub in ast.walk(node))

    found = set()
    for node in ast.walk(tree):
        if id(node) in skip:
            continue
        names = []
        if isinstance(node, ast.ImportFrom) and node.module and not node.level:
            names = [node.module]
        elif isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        for name in names:
            head = name.split(".")[0]
            if head in REPO_MODULES:
                found.add(head)
    return found


@pytest.mark.parametrize("layer", sorted(ALLOWED))
def test_a_layer_imports_only_what_it_is_allowed_to(layer):
    allowed = ALLOWED[layer]
    offenders = []
    for path in sorted((REPO / layer).rglob("*.py")):
        rel = path.relative_to(REPO).as_posix()
        for head in sorted(_imports_outside_main(path)):
            if head == layer or head in allowed:
                continue
            if (rel, head) in DECLARED:
                continue
            offenders.append(f"{rel} -> {head}")
    assert not offenders, (
        f"`{layer}/` may import {sorted(allowed) or 'nothing in this repo'}, "
        f"but these reach further: {offenders}. Either move the code so the "
        f"dependency points downward, or add it to DECLARED with the reason "
        f"removing it would cost.")


def test_every_declared_exception_still_exists():
    """The failure mode of an allow-list: it outlives what it excused.

    An entry naming a file that has been deleted, or an edge that no longer
    exists, still passes silently -- and the list then claims to be guarding
    something it is not.
    """
    stale = []
    for (rel, head), _why in DECLARED.items():
        path = REPO / rel
        if not path.exists():
            stale.append(f"{rel} (file is gone)")
        elif head not in _imports_outside_main(path):
            stale.append(f"{rel} -> {head} (edge is gone; delete the entry)")
    assert not stale, f"DECLARED is out of date: {stale}"


def test_every_declared_exception_carries_its_reason():
    for key, why in DECLARED.items():
        assert len(why) > 80, f"{key} has no real reason attached"


def test_the_engine_does_not_need_the_heavy_layers():
    """`engine/` must stay importable without gymnasium, torch or Win32.

    This is the property the layering buys, stated as the thing that would
    actually hurt: every test that touches a combat imports `engine`, and the
    suite runs eight of those at once.
    """
    heavy = {"gymnasium", "gym", "torch", "stable_baselines3", "sb3_contrib",
             "ctypes", "win32api", "win32gui", "PIL"}
    offenders = []
    for path in sorted((REPO / "engine").rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            elif isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            for name in names:
                if name.split(".")[0] in heavy:
                    offenders.append(
                        f"{path.relative_to(REPO).as_posix()} -> {name}")
    assert not offenders, (
        f"engine/ imports a heavy dependency: {offenders}. Every test that "
        "touches a combat would pay for it, eight workers at a time.")


if __name__ == "__main__":
    for _layer in sorted(ALLOWED):
        test_a_layer_imports_only_what_it_is_allowed_to(_layer)
    test_every_declared_exception_still_exists()
    test_every_declared_exception_carries_its_reason()
    test_the_engine_does_not_need_the_heavy_layers()
    print("ok")
