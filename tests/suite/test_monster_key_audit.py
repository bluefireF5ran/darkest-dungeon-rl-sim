"""The unread-key list is code, because as prose it went stale and nobody knew.

`ASSUMPTIONS.md` has carried an audit of monster keys nobody reads for a long
time, and it was accurate about every key it listed. `shared_health` was never
on it -- twelve shipped records, The Flesh's single HP pool against the
simulator's four bars, and no doc or line of code mentioned the field at all.
It was found on 2026-09-19 by reading a community boss guide, not by the
audit, and it had been missing for the life of the project.

Prose does not re-run. This does.

The same session found `.launch` and `.move` the same way, so the shape is
worth naming: **a field the game ships, the hero side reads, and the monster
side does not.** A new one arriving unnoticed is the failure this file exists
to prevent.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))

import pytest

import monster_key_audit as audit


def _monsters():
    d = audit.game_dir() / "monsters"
    if not d.is_dir():
        pytest.skip("the game install is not present")
    return d


def test_every_monster_key_is_either_read_or_classified():
    """Fails when the game ships a key nobody has looked at.

    When it fails: find out who carries it (`--carriers FIELD`), decide
    whether the simulator needs it, and then EITHER fix the gap OR add a line
    to `DECLARED` saying why not. Adding the line is not a way of silencing
    the test -- it is the classification, and it has to say something true.
    """
    monsters = _monsters()
    keys = audit.scan_keys(monsters)
    assert len(keys) > 100, f"only {len(keys)} keys -- did the parse break?"
    source = audit.read_by_code()

    undeclared = []
    for (record, field), n in keys.items():
        if field in source:
            continue
        if audit.DECLARED.get((record, field)) or audit.DECLARED.get(record):
            continue
        undeclared.append(f"{record}.{field} ({n} records, carried by "
                          f"{', '.join(audit.carriers(monsters, field)[:5])})")
    assert not undeclared, (
        "unclassified monster keys -- run `python tools/monster_key_audit.py`:\n  "
        + "\n  ".join(sorted(undeclared)))
    print(f"  PASS {len(keys)} keys, all read or classified")


def test_the_audit_can_actually_fail():
    """The `--self-check` question, asked of this instrument too.

    A guard that cannot go red is worse than no guard, because it reads as
    reassurance. Drop a real declaration and the audit must notice.
    """
    monsters = _monsters()
    victim = ("personality", "prefskill")
    assert victim in audit.DECLARED, "the probe's own premise moved"
    saved = audit.DECLARED.pop(victim)
    try:
        keys = audit.scan_keys(monsters)
        source = audit.read_by_code()
        missed = [(r, f) for (r, f) in keys
                  if f not in source
                  and not (audit.DECLARED.get((r, f)) or audit.DECLARED.get(r))]
        assert victim in missed, (
            "removing a declaration did not make the audit complain -- it "
            "cannot tell, so its silence means nothing")
    finally:
        audit.DECLARED[victim] = saved
    print("  PASS the audit notices an unclassified key")


def test_every_declaration_still_matches_something_shipped():
    """A declaration for a key the game no longer ships is dead weight, and a
    stale allowlist is how the prose version rotted in the first place."""
    monsters = _monsters()
    keys = audit.scan_keys(monsters)
    records = {r for r, _ in keys}
    stale = []
    for entry in audit.DECLARED:
        if isinstance(entry, tuple):
            if entry not in keys:
                stale.append(f"{entry[0]}.{entry[1]}")
        elif entry not in records:
            stale.append(entry)
    assert not stale, f"DECLARED names keys the game does not ship: {sorted(stale)}"
    print(f"  PASS {len(audit.DECLARED)} declarations, none stale")


if __name__ == "__main__":
    print("=== Monster Key Audit ===\n")
    for name, func in sorted(globals().items()):
        if name.startswith("test_"):
            try:
                func()
            except Exception as e:
                import traceback
                print(f"  FAIL {name}: {e}")
                traceback.print_exc()
    print("Done.")
