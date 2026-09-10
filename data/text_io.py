"""Reading a content file whose encoding is not ours to choose.

Every shipped Darkest Dungeon file is UTF-8, so for the base game this module
is a no-op with an extra function call. **Workshop mods are a different
matter**: a mod author writes their `.darkest` and `.json` files in whatever
their editor defaults to, and on a Chinese or Korean Windows that is GBK or
CP949, not UTF-8.

Two of the fifty subscribed classes died on exactly that, and they died HARD --
`UnicodeDecodeError` out of `DarkestParser.parse_file` aborts
`DataLoader.load_all` entirely, so **one badly-encoded mod takes the whole
store down**, not just its own class:

    lyzm  'utf-8' codec can't decode byte 0xd4 in position 124
    mtgh  'utf-8' codec can't decode byte 0xb4 in position 992

## The order is the whole design

`utf-8` strict comes first and is tried on its own, so **a valid UTF-8 file
decodes exactly as it did before this module existed** -- that is what keeps
the vanilla store, and every measurement anchored to it, byte-identical.

Only when that fails do the others run, and they are ordered by how much they
can be trusted to be RIGHT rather than merely to succeed:

* `utf-8-sig` -- a BOM'd UTF-8 file, which is still UTF-8.
* `gb18030` -- the current Chinese national standard and a superset of GBK.
* `cp949` -- Korean.
* `cp1252` -- Western European, the other common editor default.
* `latin-1` -- **cannot fail on any byte sequence**, so it is the terminator
  that guarantees this function returns rather than raises. Reaching it means
  the text is probably mojibake, and the caller is told.

`gb18030` also decodes most byte sequences without complaint, so a genuinely
CP949 file may be silently mis-decoded into Chinese characters. That is
accepted deliberately: the alternative on offer is not "decode it correctly",
it is "lose the class and every other class in the store with it". The names
come out wrong; the numbers, which are ASCII, come out right, and the numbers
are what the simulator runs on.

**Detection is not attempted.** `chardet` is not a dependency here and a
guessed encoding is the same class of thing as a guessed game formula -- see
the DEBUFF RES precedent in `ASSUMPTIONS.md`. A fixed, ordered, documented
chain is reproducible; a heuristic is not.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Tried in order. `latin-1` is last because it never raises, which is what
# makes this chain total.
ENCODINGS = ("utf-8", "utf-8-sig", "gb18030", "cp949", "cp1252", "latin-1")

# Paths already reported, so a 50-mod load does not print the same warning for
# every file in one badly-encoded mod.
_warned: set[str] = set()


def read_text(path: str | Path, *, quiet: bool = False) -> str:
    """The file's text, decoded by the first encoding in `ENCODINGS` that works.

    A valid UTF-8 file takes the first branch and is unaffected. Anything else
    warns ONCE per file, naming the encoding that was used, because a silent
    fallback is how mojibake becomes permanent.
    """
    raw = Path(path).read_bytes()
    for encoding in ENCODINGS:
        try:
            text = raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
        if encoding != "utf-8" and not quiet:
            key = str(path)
            if key not in _warned:
                _warned.add(key)
                print(f"  [encoding] {Path(path).name}: not UTF-8, decoded as "
                      f"{encoding}", file=sys.stderr)
        return text
    # Unreachable: latin-1 decodes every byte sequence. Kept so a future edit
    # to ENCODINGS that drops the total codec fails loudly instead of
    # returning None.
    raise UnicodeDecodeError(  # pragma: no cover
        "none", raw, 0, len(raw),
        f"no encoding in {ENCODINGS} could decode {path}")


def read_lines(path: str | Path, *, quiet: bool = False) -> list[str]:
    """`read_text` split into lines, keeping no line endings.

    `open(...)` iteration yields lines with their terminators and callers strip
    them; `splitlines()` handles CR, LF and CRLF alike, which matters because
    mod files arrive with all three.
    """
    return read_text(path, quiet=quiet).splitlines()
