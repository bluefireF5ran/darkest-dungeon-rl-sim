"""The save format decodes, and it is the only source for a capture's loadout.

A game-trace capture records the damage ranges the game rolled but never says
which hero rolled them at what gear tier, which quirks, or which trinkets. All
of that lives in `persist.roster.json` — which is not JSON at all but DSON, a
binary container (magic `01 B1 00 00`) whose hero records are themselves whole
DSON files nested inside a `raw_data` byte array.

Structural assertions only: this reads the player's live campaign, so every
value moves as they play. Skipped when no save profile is present.

See CHANGELOG.md.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest

from data.dson import parse_file, parse_bytes, is_complete, is_dson, MAGIC

SAVE_ROOT = Path(r"D:\Program Files (x86)\Steam\userdata")
_profiles = sorted(SAVE_ROOT.glob("*/262060/remote/profile_*/persist.roster.json")) \
    if SAVE_ROOT.exists() else []
ROSTER = _profiles[0] if _profiles else None

pytestmark = pytest.mark.skipif(ROSTER is None, reason="no Darkest Dungeon save profile found")


def test_magic_is_required():
    with pytest.raises(ValueError):
        parse_bytes(b"\x00" * 64)
    assert not is_dson(b"{}")
    assert is_dson(MAGIC + b"\x00" * 60)


def test_a_truncated_save_still_passes_the_magic_check():
    """The exact shape of the live crash: the magic is the first four bytes
    written, so it survives a torn write and `is_dson` says yes to a file the
    game is halfway through replacing. `parse_bytes` then indexes past the end.
    This is what `is_complete` exists to catch — assert the gap it closes."""
    whole = ROSTER.read_bytes()
    torn = whole[:len(whole) // 2]
    assert is_dson(torn), "the premise: a half-written save keeps its magic"
    assert not is_complete(torn)
    assert is_complete(whole)


def test_is_complete_rejects_a_header_that_points_past_the_end():
    """Cheap by design — it checks the three declared extents rather than
    parsing, so `_stage` can afford it on every poll. A header alone, with no
    data block behind it, must fail."""
    header_only = ROSTER.read_bytes()[:64]
    assert is_dson(header_only)
    assert not is_complete(header_only)
    assert not is_complete(b"")
    assert not is_complete(MAGIC + b"\x00" * 8)


def test_roster_decodes_to_the_expected_shape():
    root = parse_file(ROSTER)
    assert root.name == "base_root"
    assert root.get("heroes") is not None
    assert root.get("last_party") is not None


def test_hero_records_are_nested_dson_and_carry_a_class():
    """The recursion is the part that is easy to get wrong: `raw_data` is a
    length-prefixed blob that is NOT NUL-terminated, unlike a string."""
    root = parse_file(ROSTER)
    heroes = root.get("heroes").children
    assert heroes, "roster has no heroes"
    for h in heroes:
        nested = h.get("hero_file_data").get("raw_data").value
        assert hasattr(nested, "children"), f"{h.name} raw_data did not decode as DSON"
        cls = nested.get("heroClass")
        assert cls is not None and isinstance(cls.value, str) and cls.value


def test_every_hero_exposes_the_fields_a_differential_test_needs():
    root = parse_file(ROSTER)
    needed = {"heroClass", "resolveXp", "weapon_rank", "armour_rank", "skills"}
    for h in root.get("heroes").children:
        nested = h.get("hero_file_data").get("raw_data").value
        present = {c.name for c in nested.children}
        assert needed <= present, f"{h.name} missing {needed - present}"
        skills = nested.get("skills").get("selected_combat_skills")
        assert skills is not None and len(skills.children) > 0


def test_last_party_is_a_slot_count_followed_by_guids_or_empty_markers():
    """The leading number is the party's SLOT COUNT, not how many heroes are
    in it, and `-1` marks an empty slot.

    Originally asserted that all `n` following entries resolve to roster
    heroes. A later read of the same live save returned `[4, -1, -1, -1, -1]`
    — four slots, none filled — which falsified that. Only the non-empty
    entries are guids."""
    root = parse_file(ROSTER)
    party = root.get("last_party").get("last_party_guids").value
    assert isinstance(party, list) and party
    n = party[0]
    assert 1 <= n <= 4
    assert len(party) >= 1 + n
    known = {h.name for h in root.get("heroes").children}
    for guid in party[1:1 + n]:
        if guid == -1:
            continue
        assert str(guid) in known, f"party guid {guid} is not in the roster"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  ok  {name}")
