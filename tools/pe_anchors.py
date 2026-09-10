"""Anchors into `Darkest.exe`, with no decompiler in the loop.

Ghidra is the right tool for reading the code. This is the tool for deciding
*where to look*, and it needs nothing installed — pure Python plus numpy over
the PE file.

## Why this exists

`Darkest.exe` is 19.3 MB with a 12.97 MB `.text` and no PDB. What makes it
navigable is that **this project already knows the name of every configuration
key the game reads** — they are enumerated across `data/*_loader.py`, because the
loaders parse the same files the game does. Nearly every one of those keys
appears in the binary exactly **once**, as the string literal at its parse site.

So: string literal -> its virtual address -> the code that references that
address -> the function that parses it -> the struct field it writes. The
readers of that field are the formula. One string is one step from the maths.

`cutoffAlwaysMiss` (×1) is one string away from the whole accuracy pipeline.

## What it does

    python tools/pe_anchors.py --sections
    python tools/pe_anchors.py --rtti                     # 596 C++ class names
    python tools/pe_anchors.py --rtti --grep Battle
    python tools/pe_anchors.py --key cutoffAlwaysMiss     # string VA + code xrefs
    python tools/pe_anchors.py --keys                     # every key, with its xref count
    python tools/pe_anchors.py --string "MonsterBrain"
    python tools/pe_anchors.py --telemetry           # what the game LOGS
    python tools/pe_anchors.py --telemetry --all     # ...including unobserved

`--telemetry` is the one that pays without a decompiler. `log_info` reaches
`OutputDebugStringA` with no flag and no filter (DECOMP.md F3), so every printf
literal the game executes is free observation. The mode splits them by evidence:
**proven** (the text appears in `archive/traces/*.log`, so it demonstrably fired
in real play) against **candidate** (in the image, never seen — error paths and
audio-event paths are indistinguishable from log lines here). It refuses to
print a count unless a known-positive control passes first.

Addresses print as virtual addresses (`ImageBase + RVA`), which is what Ghidra's
"Go To" box takes, so a hit here is a jump there.

## How the cross-reference scan works, and what it is worth

x64 reaches a static string with RIP-relative addressing: `lea rcx, [rip+disp32]`,
where the effective address is `(address of the next instruction) + disp32`. For a
displacement field at virtual address `v`, that is `v + 4 + disp`. So finding every
reference to `target` means finding every offset where

    disp[i] + i == target - section_base - 4

which is one vectorised comparison over the section. No disassembler needed.

**It over-reports and never under-reports.** Any four bytes that happen to hold the
right value match, whether or not they are a displacement field — the scan does not
know where instructions begin. Treat a hit as "look here", not as proof. Absolute
64-bit pointers in `.rdata`/`.data` are scanned too, which is how vtables and
string tables reference their contents.

**A zero is still a result.** `excessCritMult` does not appear in the binary at all,
which says the game never reads a key `shared/rules.json` ships — exactly the kind
of thing this project otherwise implements "for correctness" and cannot confirm.

## Two traps, both hit on the first run

**A key is stored in the form its own file uses, and there are three.** JSON keys are
bare (`cutoffAlwaysMiss`), `.darkest` block headers keep their colon
(`combat_move_skill:`), and `.darkest` fields keep their dot (`.per_battle_limit`,
`.is_crit_valid`). Searching only the bare NUL-terminated form reported
`combat_move_skill` as absent when it is right there with a colon. All three forms are
searched now, and the matching one is printed -- which also says which of the game's two
parsers the key belongs to.

**"Absent" only means something for a key the shipped data actually contains.** Three
keys reported absent on the first run -- `torch_decrease_per_room_move`,
`combat_retreat_chance_increment`, `resolve_check_stress_level` -- were plausible-sounding
names that no game file ships. The tool was faithfully reporting that an invented string
is not in the binary. `ANCHOR_KEYS` now holds only names verified against the real files,
so an absence is evidence rather than a typo. (The real one is
`combat_retreat_bonus_chance_per_attempt`.)

**A literal with zero references is not unreferenced.** `.per_battle_limit`,
`.per_turn_limit` and `.is_crit_valid` sit within a few bytes of each other in one block
of `.darkest` field names, and none has a direct xref: the parser walks that table from a
single base pointer instead of naming each string. There the anchor is the table base --
cross-reference the *first* string of the run to find it.
"""

from __future__ import annotations

import argparse
import re
import struct
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data.paths import GAME_DIR
EXE = GAME_DIR / "_windows" / "win64" / "Darkest.exe"

REPO = Path(__file__).resolve().parent.parent
# The captures `--telemetry` grades against. Recorded for other purposes, which
# is the point: anything found in them reached the channel with no special
# flags and no intent.
CAPTURE_DIR = REPO / "archive" / "traces"

# MSVC RTTI type descriptors: ".?AV<name>@<outer>@...@@" for classes.
_RTTI_RE = re.compile(rb"\.\?A[VU][A-Za-z0-9_:@?$]{2,120}@@")

# A printf-style literal: some text, a conversion, then anything printable up
# to the NUL. **The trailing class must include `\r\n\t`.** Without them this
# regex silently drops every literal that ends in a newline -- which is most
# log lines, including `Chance to hit: %f\n` -- and the scan then reports a
# confident zero. That is exactly what it did the first time it was run.
_FORMAT_RE = re.compile(
    rb"[\x20-\x7e]{8,120}%[-+ 0-9.]*[dfsxup][\x20-\x7e\r\n\t]{0,80}\x00")

# The control. A literal verified by hand to be in BOTH the image and the
# captures, so the scan can prove itself before any count it prints is
# believed. Two measurements this session returned a clean, wrong zero -- one
# from a missing `bc`, one from the newline above -- and both were caught only
# because something else disagreed. A pipeline that has never printed a
# positive cannot be read as a negative.
TELEMETRY_CONTROL = "Chance to hit"


@dataclass(frozen=True)
class Section:
    name: str
    vaddr: int          # RVA
    vsize: int
    raddr: int          # file offset
    rsize: int

    def contains_file(self, off: int) -> bool:
        return self.raddr <= off < self.raddr + self.rsize

    def contains_rva(self, rva: int) -> bool:
        return self.vaddr <= rva < self.vaddr + max(self.vsize, self.rsize)


class PE:
    """Just enough PE to turn file offsets into virtual addresses and back."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.data = self.path.read_bytes()
        pe = struct.unpack_from("<I", self.data, 0x3C)[0]
        if self.data[pe:pe + 4] != b"PE\0\0":
            raise ValueError(f"{path} is not a PE file")
        n_sections = struct.unpack_from("<H", self.data, pe + 6)[0]
        opt_size = struct.unpack_from("<H", self.data, pe + 20)[0]
        magic = struct.unpack_from("<H", self.data, pe + 24)[0]
        if magic != 0x20B:
            raise ValueError("not a PE32+ (x64) image; the xref scan assumes x64")
        self.image_base = struct.unpack_from("<Q", self.data, pe + 24 + 24)[0]
        table = pe + 24 + opt_size
        self.sections = []
        for i in range(n_sections):
            off = table + i * 40
            name = self.data[off:off + 8].rstrip(b"\0").decode("latin1")
            vsize, vaddr, rsize, raddr = struct.unpack_from("<IIII", self.data, off + 8)
            self.sections.append(Section(name, vaddr, vsize, raddr, rsize))

    # -- address arithmetic --

    def file_to_va(self, off: int):
        for s in self.sections:
            if s.contains_file(off):
                return self.image_base + s.vaddr + (off - s.raddr)
        return None

    def va_to_file(self, va: int):
        rva = va - self.image_base
        for s in self.sections:
            if s.contains_rva(rva) and rva - s.vaddr < s.rsize:
                return s.raddr + (rva - s.vaddr)
        return None

    def section(self, name: str):
        return next((s for s in self.sections if s.name == name), None)

    def body(self, s: Section) -> bytes:
        return self.data[s.raddr:s.raddr + s.rsize]

    # -- strings --

    def find_string(self, text: str, nul: bool = True) -> list[int]:
        """Virtual addresses of a literal. NUL-terminated by default, so
        `crit` does not match every `crit_mod` in the file."""
        needle = text.encode("latin1") + (b"\0" if nul else b"")
        out, at = [], self.data.find(needle)
        while at != -1:
            va = self.file_to_va(at)
            if va is not None:
                out.append(va)
            at = self.data.find(needle, at + 1)
        return out

    def locate_key(self, key: str) -> list[tuple[str, int]]:
        """`[(form, va)]` for a config key, trying every form the game stores.

        A key lives in the binary shaped the way its own file writes it: bare
        for JSON, `key:` for a `.darkest` block header, `.key` for a `.darkest`
        field. Which form matches is itself information -- it says which of the
        game's two parsers reads the key.
        """
        hits: list[tuple[str, int]] = []
        for form, literal in (("json", key), ("block", key + ":"), ("field", "." + key)):
            hits.extend((form, va) for va in self.find_string(literal))
        return hits

    def present_anywhere(self, key: str) -> bool:
        """Is the key's text in the image at all, terminated or not?

        The honest test behind an ABSENT verdict. A key can be a substring of a
        longer literal and still be there; only this returning False supports
        the claim that the game never reads it.
        """
        return key.encode("latin1") in self.data

    def format_strings(self) -> dict[str, int]:
        """`{printf-style literal: virtual address}`.

        The game's whole potential telemetry surface. `log_info` reaches
        `OutputDebugStringA` unconditionally (DECOMP.md F3), so any of these
        that a running game actually executes is free observation — no launch
        flag, no capture setup beyond `tools/dbwin_capture.py`.

        Being in the image is NOT evidence that a line ever fires: plenty are
        error paths, and a good number are audio-event paths handed to the
        sound system rather than to the logger. `telemetry()` separates the two
        by checking the captures.
        """
        out: dict[str, int] = {}
        for m in _FORMAT_RE.finditer(self.data):
            text = m.group(0)[:-1].decode("latin1")
            if text.count("%") > 6:          # format tables, not log lines
                continue
            va = self.file_to_va(m.start())
            if va is not None:
                out.setdefault(text, va)
        return out

    def rtti_classes(self) -> list[str]:
        """The C++ class names MSVC left in the image, outermost-last.

        `.?AVActivityLock@SideEffect@Activity@Building@@` is
        `Building::Activity::SideEffect::ActivityLock` — the qualifiers are
        stored inside-out, so they are reversed here to read as C++.
        """
        names = set()
        for m in _RTTI_RE.findall(self.data):
            parts = m.decode("latin1")[4:-2].split("@")
            names.add("::".join(reversed([p for p in parts if p])))
        return sorted(names)

    # -- cross references --

    def xrefs(self, target_va: int) -> dict[str, list[int]]:
        """`{section: [virtual addresses that reference target_va]}`.

        RIP-relative displacements in every section, plus absolute 64-bit
        pointers. See the module docstring on why this over-reports.
        """
        found: dict[str, list[int]] = {}
        for s in self.sections:
            if s.rsize < 8:
                continue
            raw = self.body(s)
            base = self.image_base + s.vaddr
            b = np.frombuffer(raw, dtype=np.uint8).astype(np.int64)
            n = b.size

            # RIP-relative: disp32 at i, effective address = base + i + 4 + disp
            d = (b[0:n - 3] | (b[1:n - 2] << 8) | (b[2:n - 1] << 16)
                 | (b[3:n] << 24))
            d = np.where(d >= 0x80000000, d - 0x100000000, d)      # sign-extend
            idx = np.arange(d.size, dtype=np.int64)
            hits = np.nonzero(d + idx == target_va - base - 4)[0]

            # Absolute 8-byte pointers, for vtables and string tables.
            if n >= 8:
                q = np.frombuffer(raw[:n - (n % 8)], dtype="<u8")
                abs_hits = np.nonzero(q == np.uint64(target_va))[0] * 8
            else:
                abs_hits = np.empty(0, dtype=np.int64)

            all_hits = sorted({int(base + h) for h in hits}
                              | {int(base + h) for h in abs_hits})
            if all_hits:
                found[s.name] = all_hits
        return found


# ── the keys this project already knows the game reads ──
#
# Not a guess and not scraped: these are the literals `data/*_loader.py` looks
# for, which means the game's own parser must look for them too. Grouped by what
# answering them would settle, most valuable first — see the plan's Step 2.
ANCHOR_KEYS = {
    "accuracy / crit": ["cutoffAlwaysMiss", "cutoffAlwaysHit", "excessCritMult",
                        "excessCritMax", "player_crit_multiplier_table",
                        "monster_crit_multiplier_table", "can_be_missed",
                        "is_crit_valid"],
    "torch": ["range_table", "lowest_excluded", "camp_restore_torch",
              "ambush_torch_reduction"],
    "retreat": ["combat_retreat_chance", "combat_retreat_bonus_chance_per_attempt",
                "combat_retreat_stress"],
    "stress": ["hallway_per_tile_stress_damage_fwd",
               "hallway_per_tile_stress_damage_backing_up",
               "passed_turn_stress_dmg"],
    "skills": ["per_battle_limit", "per_turn_limit", "combat_move_skill",
               "riposte_skill", "buff_duration_type"],
    "progression": ["resolve_level_thresholds", "deaths_door"],
}


def capture_text(capture_dir: Path = CAPTURE_DIR) -> str:
    """Everything the debug channel has ever been recorded saying here."""
    return "\n".join(p.read_text(encoding="utf-8", errors="replace")
                     for p in sorted(Path(capture_dir).glob("*.log")))


def _prefix(literal: str) -> str:
    """The fixed text before the first conversion — what to grep a log for."""
    return literal.split("%")[0].strip()


# Load-time profiling, of which the image has a great deal: "Something - %f",
# "Something = %f". Real telemetry, useless to this project, and it would bury
# the eight lines that matter.
_TIMING_RE = re.compile(r"[-=] %f\s*$")


def _is_timing(literal: str) -> bool:
    return bool(_TIMING_RE.search(literal)) or literal.startswith("Darkest::Load")


def telemetry(pe: PE, captures: str, min_prefix: int = 10):
    """`(live, candidates)` — what the game is known to say, and what it might.

    **live**: the literal's fixed prefix occurs verbatim in the captured
    corpus, so that line demonstrably reached the channel in real play.

    **candidates**: in the image, never yet observed. Reachability unknown —
    error paths and audio-event paths look exactly like log lines from here.

    `min_prefix` guards against short prefixes matching by accident; a literal
    whose text before the first `%` is under ten characters is not searched.
    """
    live: dict[str, int] = {}
    candidates: dict[str, int] = {}
    for text, va in pe.format_strings().items():
        head = _prefix(text)
        target = live if (len(head) >= min_prefix and head in captures) else candidates
        target[text] = va
    return live, candidates


def _print_xrefs(pe: PE, label: str, va: int) -> int:
    refs = pe.xrefs(va)
    total = sum(len(v) for v in refs.values())
    print(f"  {label} @ 0x{va:012x}   {total} reference(s)")
    for name, addrs in refs.items():
        shown = ", ".join(f"0x{a:012x}" for a in addrs[:8])
        more = f" (+{len(addrs) - 8} more)" if len(addrs) > 8 else ""
        print(f"      {name:<8s} {shown}{more}")
    return total


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--exe", default=str(EXE))
    ap.add_argument("--sections", action="store_true")
    ap.add_argument("--rtti", action="store_true", help="the C++ class names in the image")
    ap.add_argument("--grep", default=None, help="filter --rtti, case-insensitive")
    ap.add_argument("--string", default=None, help="locate a literal and cross-reference it")
    ap.add_argument("--key", default=None, help="same, for one config key")
    ap.add_argument("--keys", action="store_true", help="every anchor key, grouped")
    ap.add_argument("--telemetry", action="store_true",
                    help="what the game LOGS: printf literals in the image, split "
                         "by whether the captured corpus proves they reach the "
                         "debug channel")
    ap.add_argument("--all", action="store_true",
                    help="with --telemetry, also list the unobserved candidates")
    ap.add_argument("--captures", default=str(CAPTURE_DIR),
                    help="directory of *.log captures to grade against")
    ap.add_argument("--no-nul", action="store_true",
                    help="match the literal without its terminator (substring search)")
    args = ap.parse_args()

    pe = PE(Path(args.exe))
    print(f"{pe.path.name}  {len(pe.data) / 1e6:.1f} MB  "
          f"image base 0x{pe.image_base:x}")

    if args.sections:
        print(f"\n  {'section':<10s} {'VA':>14s} {'vsize':>12s} {'raw':>12s}")
        for s in pe.sections:
            print(f"  {s.name:<10s} 0x{pe.image_base + s.vaddr:012x} "
                  f"{s.vsize:12,d} {s.rsize:12,d}")

    if args.rtti:
        names = pe.rtti_classes()
        if args.grep:
            names = [n for n in names if args.grep.lower() in n.lower()]
        print(f"\n  {len(names)} RTTI class name(s)"
              + (f" matching {args.grep!r}" if args.grep else ""))
        for n in names:
            print(f"      {n}")

    for target in (args.string, args.key):
        if not target:
            continue
        vas = pe.find_string(target, nul=not args.no_nul)
        print(f"\n  {target!r}: {len(vas)} occurrence(s)")
        if not vas:
            print("      absent from the image - the game does not read this "
                  "key, whatever the data files ship")
        for va in vas:
            _print_xrefs(pe, target, va)

    if args.telemetry:
        found = pe.format_strings()
        captures = capture_text(Path(args.captures))

        # The control, before any number below is worth reading. Both halves:
        # the scan must find the literal in the image, and the corpus must
        # contain it. Either failing means the pipeline is broken, and a broken
        # pipeline reports a confident zero rather than an error.
        in_image = any(t.startswith(TELEMETRY_CONTROL) for t in found)
        in_capture = TELEMETRY_CONTROL in captures
        print(f"\n  control {TELEMETRY_CONTROL!r}: in image {in_image}, "
              f"in captures {in_capture}")
        if not (in_image and in_capture):
            print("  CONTROL FAILED - the scan cannot see something known to be "
                  "in both. Every count below would be meaningless; fix the scan "
                  "or point --captures at the real corpus.")
            return 1

        live, cand = telemetry(pe, captures)
        game_live = sorted(t for t in live if not _is_timing(t))
        print(f"  {len(found)} printf literal(s); {len(live)} proven to reach the "
              f"channel, {len(game_live)} of those about the game rather than "
              f"load timing\n")
        for text in game_live:
            print(f"      {text.strip()[:88]}")

        if args.all:
            game_cand = sorted(t for t in cand if not _is_timing(t))
            print(f"\n  {len(game_cand)} candidate(s) never seen in a capture. In "
                  f"the image is NOT evidence a line fires -- error paths and "
                  f"audio-event paths look identical from here.\n")
            for text in game_cand:
                print(f"      {text.strip()[:88]}")
        return 0

    if args.keys:
        for group, keys in ANCHOR_KEYS.items():
            print(f"\n  == {group} ==")
            for k in keys:
                hits = pe.locate_key(k)
                if not hits:
                    verdict = ("ABSENT - not in the image at all"
                               if not pe.present_anywhere(k)
                               else "only as a SUBSTRING of a longer literal")
                    print(f"  {k:<44s} {verdict}")
                    continue
                n = sum(sum(len(v) for v in pe.xrefs(va).values()) for _f, va in hits)
                forms = ",".join(sorted({f for f, _ in hits}))
                print(f"  {k:<44s} {forms:<6s} {len(hits)} literal(s), "
                      f"{n} xref(s)   0x{hits[0][1]:012x}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
