"""The PE anchor tool, against the real `Darkest.exe`.

`tools/pe_anchors.py` decides where to point a decompiler. If its address
arithmetic is wrong it does not fail loudly — it sends the reader to the wrong
function and everything downstream is confidently wrong. So the arithmetic is
checked against known-answer round trips, and the two verdicts the tool is
allowed to state ("present, here" and "absent from the image") are pinned to
keys whose answer is already known from the shipped data files.

Skipped wholesale without the game install, like the rest of the suite.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.pe_anchors import EXE, PE

# xdist: this file's tests spike to +416 MB each while they decode frames.
# `--dist load` scatters them, so up to 16 spikes land at once and the suite's
# PEAK -- not its steady state -- is what crashes the machine. Grouped, at most
# one runs at a time. The four grouped files cost 12.9 s serially against a 74 s
# suite, so this buys memory without touching the critical path.
pytestmark = pytest.mark.xdist_group("memory_heavy")

pytestmark = pytest.mark.skipif(not EXE.exists(), reason="game not installed")


@pytest.fixture(scope="module")
def pe():
    return PE(EXE)


def test_the_image_parses_as_the_binary_we_pinned(pe):
    """A 64-bit PE at the standard image base, with the sections the
    reconnaissance recorded. `.bind` would mean a Steam DRM wrapper, and its
    absence is why this loads into a decompiler as a plain PE."""
    assert pe.image_base == 0x140000000
    names = [s.name for s in pe.sections]
    assert names == [".text", ".rdata", ".data", ".pdata", ".rsrc", ".reloc"]
    assert ".bind" not in names
    text = pe.section(".text")
    assert text.rsize > 12_000_000


def test_file_offsets_and_virtual_addresses_round_trip(pe):
    """The one piece of arithmetic everything else rests on. A wrong section
    delta still produces plausible-looking addresses, which is the dangerous
    kind of wrong."""
    for s in pe.sections:
        if s.rsize == 0:
            continue
        for off in (s.raddr, s.raddr + s.rsize // 2, s.raddr + s.rsize - 1):
            va = pe.file_to_va(off)
            assert va is not None, s.name
            assert pe.va_to_file(va) == off, (s.name, hex(off))


def test_a_located_string_really_is_at_that_address(pe):
    """`find_string` returns virtual addresses; this reads the bytes back
    through the reverse mapping and checks they are the string."""
    (va,) = pe.find_string("cutoffAlwaysMiss")
    off = pe.va_to_file(va)
    assert pe.data[off:off + 16] == b"cutoffAlwaysMiss"


def test_rtti_gives_readable_class_names(pe):
    """596 of them, and the qualifiers are stored inside-out — a name that
    still reads `Foo@Bar@@` means the reversal was dropped and every class in
    a namespace is mislabelled."""
    names = pe.rtti_classes()
    assert len(names) > 500
    assert "Battle::CombatActionProcessor" in names
    assert not any("@" in n for n in names)


def test_a_key_is_found_in_whatever_form_its_own_file_uses(pe):
    """The trap that made the first run report a present key as absent. JSON
    keys are bare, `.darkest` block headers keep the colon, `.darkest` fields
    keep the dot."""
    assert [f for f, _ in pe.locate_key("cutoffAlwaysMiss")] == ["json"]
    assert "block" in {f for f, _ in pe.locate_key("combat_move_skill")}
    assert "field" in {f for f, _ in pe.locate_key("per_battle_limit")}


def test_the_excess_crit_multiplier_is_read_under_a_misspelled_key(pe):
    """DECOMP.md F1, and the reason an ABSENT verdict is a lead rather than a
    conclusion.

    `shared/rules.json` ships `excessCritMult`. The binary compares against
    `exessCritMult` — one `s` — so the two never match and the data file's value
    never reaches the game. Both are 0.0 today, so nothing behaves differently;
    what it means is that a modded `rules.json` setting that key moves the
    simulator and not the game.

    The first version of this test asserted only the absence and concluded the
    mechanic was unread. It is read. Absence of a key is a reason to go looking
    for a typo."""
    assert not pe.present_anywhere("excessCritMult"), "the correct spelling"
    assert pe.locate_key("exessCritMult"), "the spelling the game actually uses"
    assert pe.locate_key("excessCritMax"), "the neighbouring key is read normally"


def test_absent_means_absent_and_not_merely_unterminated(pe):
    """`present_anywhere` is the honest test behind an ABSENT verdict: a key
    that exists only inside a longer literal is present, and must not be
    reported as evidence that the game ignores it."""
    assert pe.present_anywhere("combat_move_skill")      # stored as "combat_move_skill:"
    assert not pe.find_string("combat_move_skill")       # ...so the bare form misses
    assert not pe.present_anywhere("this_key_does_not_exist_anywhere")


def test_a_format_string_ending_in_a_newline_is_not_dropped(pe):
    """The regression that produced a confident, wrong zero.

    The first version of the scan required printable characters all the way to
    the NUL, so every literal ending in `\\n` — which is most log lines,
    including this one — was invisible, and it reported that nothing in the
    image reached the debug channel. `Chance to hit` was known to be in both
    the image and the captures at the time."""
    strings = pe.format_strings()
    assert "Chance to hit: %f\n" in strings
    assert any(t.endswith("\n") for t in strings), "the trailing class is wrong again"


def test_the_telemetry_control_holds(pe):
    """`--telemetry` refuses to print a count unless this passes, because a
    broken scan here does not raise — it reports zero. Two measurements in one
    session returned a clean wrong zero (a missing `bc`, then the newline
    above), each caught only because something else disagreed."""
    from tools.pe_anchors import TELEMETRY_CONTROL, capture_text
    assert any(t.startswith(TELEMETRY_CONTROL) for t in pe.format_strings())
    assert TELEMETRY_CONTROL in capture_text()


def test_telemetry_separates_what_is_proven_from_what_is_merely_present(pe):
    """Being in the image is not evidence a line ever fires. The split is the
    whole value of the mode: `Chance to hit` is proven (it is in the corpus),
    while hundreds of error and audio-event paths are only candidates."""
    from tools.pe_anchors import capture_text, telemetry
    live, candidates = telemetry(pe, capture_text())
    assert "Chance to hit: %f\n" in live
    assert "Chance to hit: %f\n" not in candidates
    assert len(candidates) > len(live), "everything cannot be proven live"
    # The three that made the mode worth writing: position, loot, encounter.
    for wanted in ("Moved onto areatile", "in LootTable:",
                   "FillOutBattle with mash entry index:"):
        assert any(wanted in t for t in live), wanted


def test_the_xref_scan_finds_the_accuracy_anchor(pe):
    """The scan over-reports by design (it cannot see instruction boundaries),
    so this asserts it finds *something* in code for a key the game certainly
    parses — a zero here would mean the displacement arithmetic is broken."""
    (va,) = pe.find_string("cutoffAlwaysMiss")
    refs = pe.xrefs(va)
    assert refs, "no reference at all to a key the game demonstrably reads"
    assert any(s in refs for s in (".text", ".rdata"))


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
