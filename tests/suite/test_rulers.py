"""A ruler grades the policy; nothing the policy is built from may read one.

There were **seven copies of this guard**, one per ruler, each an AST walk
over `engine/`, `env/`, `data/` and `bridge/` plus a substring check on
`train.py`, differing only in the module name. Copying it is how the list got
holes: `tools/skill_tiers.py` had NO guard, though `CLAUDE.md` says "a test
enforces that", and `tools/check_class_play.py` and `tools/style_probe.py`
had none either -- while `tests/tools/test_endless.py`'s own docstring cited
"the same guard as `skill_tiers` and `check_class_play`", neither of which
existed. Three rulers unguarded, and the prose said otherwise in two places.

One list, one guard. Adding a ruler is one line here, and the line carries
its own reason so the list cannot decay into a bare tuple of names.

**Why the rule exists at all.** Fran, on `check_class_play`: *"only use this
to verify and iterate"*. Each of these grades the policy against something
with no stake in it -- an expert's tier list, a per-class guide, his own
recorded play, a reward-to-gold exchange table. Fit the policy to its own
ruler and the measurement stops being independent, and in the tier-list and
class-guide cases you get precisely the un-scalable player that
`--hold-out-class` measured at +46.9pp: one that has learned seventeen
scripts rather than the game.
"""

import ast
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

REPO = Path(__file__).resolve().parents[2]

# Where a policy's behaviour comes from. A ruler in any of these is a ruler
# that can reach training, whether or not it does today.
WATCHED = ("engine", "env", "data", "bridge")

# module name -> why fitting the policy to it would be wrong.
RULERS = {
    "skill_tiers":
        "one 9,000-hour player's opinion of seventeen classes, covering no "
        "modded class at all -- it would transfer to the Sibyl as nothing",
    "check_class_play":
        "the 21 per-class guides; a policy following a per-class script is "
        "the un-scalable player the mechanics-not-identity observation exists "
        "to avoid",
    "style_probe":
        "grades WHICH SKILL against the tier list, so it inherits the tier "
        "list's rule",
    "team_probe":
        "grades WHICH TARGET; a policy fitted to a focus-fire target is a "
        "scripted player, and targeting is where a scripted player stops "
        "transferring to an unseen class",
    "play_log_probe":
        "the same three metrics on Fran's own recorded play -- training on it "
        "is behavioural cloning wearing a ruler's name",
    "stress_ratchet":
        "compares the simulator's stress recovery against his recordings; an "
        "engine that reads it would be tuning itself to its own oracle",
    "retreat_audit":
        "re-prices logged STAY/LEAVE decisions, and imports `bridge.retreat` "
        "to read the floor -- so the dependency runs tools -> bridge and must "
        "never come back",
    "exchange_rates":
        "reads the reward constants; anything consuming them must not read "
        "the table back, or it becomes an input to what it measures",
    "endless":
        "battles survived before a death. Darkest Dungeon has no endless "
        "dungeon, so training for it is training for a mode the game does "
        "not have",
    "journal_report":
        "reads the demonstration corpus; the corpus is evidence, not a "
        "training target",
    "model_preference":
        "the policy's own pick rates. A policy trained to match a usage "
        "profile is a policy imitating its own past play, and the tier column "
        "would fit it to `skill_tiers`' seventeen classes on top of that -- so "
        "it inherits that list's rule as well as its own",
    "class_visibility":
        "grades whether a class's skills reach the observation at all. It "
        "reads `_mechanics_from_data`, so an `env/` module importing it back "
        "would let the gate that measures visibility shape the definition of "
        "visible -- and the gate decides which classes enter the training "
        "corpus, so that loop would close on itself",
}


def _imported_modules(path):
    """Every module name this file IMPORTS. Comments and strings excluded.

    The old per-file copies walked the AST for the four folders and then fell
    back to a plain substring search for `train.py`, which is a different and
    weaker test: it fails on a COMMENT naming the ruler. Three of these are
    named in production comments today (`play_log_probe` in
    `env/expedition_env.py`, `retreat_audit` in `bridge/journal.py`,
    `exchange_rates` in `bridge/retreat.py`) and every one of those is a
    cross-reference that should be encouraged, not broken.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:                      # not ours to police
        return set()
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            out.add(node.module)
        elif isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
    return out


#: Root-level modules that are as training-adjacent as anything in WATCHED.
#: `eval_expedition.py` was missing here until 2026-09-07 while `train.py` was
#: present -- and it is imported by eight tools, so a ruler could have reached
#: the training path through it with nothing to say so.
WATCHED_FILES = ("train.py", "eval_expedition.py")


def _production_files():
    files = [p for d in WATCHED for p in (REPO / d).rglob("*.py")]
    return files + [REPO / f for f in WATCHED_FILES]


@pytest.mark.parametrize("ruler", sorted(RULERS))
def test_a_ruler_is_not_imported_by_anything_that_trains(ruler):
    for path in _production_files():
        for mod in _imported_modules(path):
            assert ruler not in mod.split("."), (
                f"{path.relative_to(REPO).as_posix()} imports {ruler}: "
                f"{RULERS[ruler]}")


def test_every_named_ruler_actually_exists():
    """The guard's own failure mode, and the reason the old copies could rot
    unnoticed: a ruler that has been renamed or deleted still passes its
    import check, because nothing imports a module that is not there. Then the
    list quietly guards nine things while claiming ten."""
    missing = [r for r in RULERS if not (REPO / "tools" / f"{r}.py").exists()]
    assert not missing, f"RULERS names tools that do not exist: {missing}"


def test_every_ruler_carries_its_reason():
    """A bare list of names decays. The reason is what a future reader needs
    to decide whether a new tool belongs here."""
    for ruler, why in RULERS.items():
        assert len(why) > 40, f"{ruler} has no real reason attached"


if __name__ == "__main__":
    for _r in sorted(RULERS):
        test_a_ruler_is_not_imported_by_anything_that_trains(_r)
    test_every_named_ruler_actually_exists()
    test_every_ruler_carries_its_reason()
    print("ok")
