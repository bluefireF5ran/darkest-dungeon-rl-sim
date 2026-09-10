"""`_CONDITIONAL_RULE_TYPES` is documentation that no code consults.

`_is_conditional_rule_active` branches on rule-type names in an if/elif chain
and never looks at the frozenset sitting beside it, so the two are free to
drift apart -- and when they do, the set becomes a lie about the code one
screen below it. `tests/env/test_region_quirks.py` cites the set in prose as
the authority for which rule types are conditional, which is only safe while
they agree.

They agreed exactly, in both directions, when the buff machinery moved into
`engine/_buffs.py` on 2026-09-08. This file keeps that true.

**Why the drift would be dangerous rather than untidy.** An unread
`rule_type` is GRANTED, not skipped: anything the chain does not recognise is
treated as always-active, matching the behaviour from before conditional
buffs existed. So a name that appears in the chain but not in the set is
merely stale documentation, while the reverse -- a rule type nobody
branches on -- hands out a free stat for the rest of the fight. A comp search
will find that and take it.
"""
import inspect
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from engine.combat_engine import CombatEngine  # noqa: E402

QUOTES = '"' + "'"


def _branched_rule_types() -> set:
    """Every rule-type literal the chain actually compares against.

    Located through `inspect` rather than by file path, so moving these
    methods again cannot silently turn the check off -- which is exactly what
    happened to the non-recursive globs in `tests/suite/` when the suite moved
    into subdirectories.
    """
    src = inspect.getsource(CombatEngine._is_conditional_rule_active)
    found = set(re.findall(r'rt == "([A-Za-z_]+)"', src))
    for group in re.findall(r"rt in \(([^)]*)\)", src):
        for piece in group.split(","):
            piece = piece.strip().strip(QUOTES)
            if piece:
                found.add(piece)
    return found


def test_the_declared_rule_types_match_the_branches_that_read_them():
    declared = set(CombatEngine._CONDITIONAL_RULE_TYPES)
    branched = _branched_rule_types()
    assert declared == branched, (
        "_CONDITIONAL_RULE_TYPES and the branch chain in "
        "_is_conditional_rule_active disagree. "
        f"declared but never branched on: {sorted(declared - branched)}; "
        f"branched on but not declared: {sorted(branched - declared)}. "
        "The second list is the dangerous one: an undeclared rule_type is "
        "GRANTED rather than skipped, so it is a free stat.")
    print(f"  PASS {len(declared)} rule types declared and branched on")


def test_the_set_is_not_empty_and_the_scan_found_something():
    """The vacuity guard.

    Both halves of the comparison above are derived, so both can silently
    become empty -- a renamed attribute, or a chain rewritten as a dict lookup
    that the regex no longer matches. Two empty sets compare equal and the
    test passes having checked nothing.
    """
    assert len(CombatEngine._CONDITIONAL_RULE_TYPES) >= 10
    assert len(_branched_rule_types()) >= 10
    print("  PASS neither side of the comparison is empty")


if __name__ == "__main__":
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            _fn()
    print("ok")
