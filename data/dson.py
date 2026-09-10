"""Decoder for Darkest Dungeon's binary save format (DSON).

Every `persist.*.json` in a save profile is **not JSON** — it is a binary
container with magic `01 B1 00 00`. Decoding it turns the player's actual
campaign into a data source: the real roster (class, resolve level, equipped
skills, trinkets, weapon/armour tier) and, mid-raid, `persist.raid.json`.
That is what makes a game-trace capture checkable — the trace records damage
ranges but never says which hero rolled them at what gear tier.

Layout (all little-endian; offsets verified against real saves by internal
consistency: meta1_off + meta1_size == meta2_off, meta2_off + 12*n == data_off):

    0x00  magic 01 B1 00 00
    0x04  revision
    0x08  header length (0x40)
    0x10  meta1 size, meta1 count, meta1 offset
    0x2C  meta2 count, meta2 offset
    0x38  data length, data offset

`meta1` (16 bytes) carries the tree: (parent index, meta2 index,
direct children, total children). `meta2` (12 bytes) carries one field each:
(name hash, offset into data, field info bitfield). The bitfield packs
`isObject` in bit 0, the name length in bits 2-10, and the owning meta1 index
in bits 11+ (masked to 20 bits — the high bit is a flag, not part of the
index). In the data block a field is its NUL-terminated name followed by the
value, 4-byte aligned; a field's value runs until the next field's name.

**Values are untyped on disk.** The container records no type, so this decodes
by shape: a length-prefixed NUL-terminated run is a string, 1 byte is a bool,
4 is an int, a multiple of 4 is an int list. `raw_value` is always kept so a
caller that knows better can reinterpret (notably floats, which are
indistinguishable from ints at this layer).

Hero records are stored as a **whole DSON file nested inside a `raw_data`
byte array**, so decoding recurses.
"""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass, field as dc_field
from pathlib import Path
from typing import Any, Optional

MAGIC = b"\x01\xb1\x00\x00"


@dataclass
class Field:
    name: str
    is_object: bool
    raw_value: bytes = b""
    value: Any = None
    children: list["Field"] = dc_field(default_factory=list)

    def get(self, name: str) -> Optional["Field"]:
        for c in self.children:
            if c.name == name:
                return c
        return None

    def as_float(self) -> Optional[float]:
        """Reinterpret a 4-byte value as a float32.

        DSON does not tag scalar types, so `_decode_value` returns every
        4-byte field as an int — correct for guids, ranks and the game's
        name hashes, wrong for the ones the game stores as float. `m_Stress`
        is the one that matters: it decodes as 1115422720, which is
        0x42800000, i.e. 64.0f.

        Deliberately NOT auto-detected. A magnitude heuristic would have to
        choose between "huge int" and "plausible float" and the game's own
        class-name hashes reinterpret as plausible floats too (1181166609 ->
        14925.27), so guessing would silently corrupt the fields that are
        genuinely ints. The caller knows which fields are floats; this makes
        reading them explicit."""
        if len(self.raw_value) != 4:
            return None
        return struct.unpack_from("<f", self.raw_value, 0)[0]

    def find_all(self, name: str) -> list["Field"]:
        out = [self] if self.name == name else []
        for c in self.children:
            out.extend(c.find_all(name))
        return out

    def to_dict(self) -> Any:
        if not self.is_object:
            return self.value
        # Numeric keys mean the game wrote a list; keep it as a dict anyway so
        # the original (sparse, non-contiguous) keys survive — roster hero ids
        # are guids like 1, 2, 7, 19, 20, not 0..n.
        return {c.name: c.to_dict() for c in self.children}


def _decode_value(raw: bytes) -> Any:
    if not raw:
        return None
    if len(raw) >= 5:
        (n,) = struct.unpack_from("<i", raw, 0)
        if 0 < n == len(raw) - 4:
            body = raw[4:]
            # A nested save (hero records live inside `raw_data`) carries the
            # magic and is NOT NUL-terminated; a string is.
            if is_dson(body):
                return parse_bytes(body)
            if body[-1] == 0:
                try:
                    return body[:-1].decode("utf-8")
                except UnicodeDecodeError:
                    return raw
    if len(raw) == 1:
        return bool(raw[0])
    if len(raw) == 4:
        return struct.unpack_from("<i", raw, 0)[0]
    if len(raw) % 4 == 0:
        return list(struct.unpack(f"<{len(raw) // 4}i", raw))
    return raw


def dd_name_hash(name: str) -> int:
    """The game's own string hash: `h = 0; for c in s: h = h*53 + c`, mod 2^32.

    Recovered by brute force from a real save, which is self-describing here:
    meta2 stores each field's hash *and* the field's name sits in the data
    block, so the (name, hash) pairs are free. 200/200 pairs match this
    function on the first snapshot tried.

    Worth having because the format stores some ids as this hash rather than
    as text. Inverting one means hashing a candidate list and matching, since
    the hash itself is one-way.

    **Not every opaque int is one of these.** `raid.in_area` was the obvious
    suspect and is not: it matches none of the six real `dungeons/` ids, so it
    is an area *instance* id, not a name. Check before assuming."""
    h = 0
    for byte in name.encode("utf-8"):
        h = (h * 53 + byte) & 0xFFFFFFFF
    return h


def is_dson(buf: bytes) -> bool:
    return len(buf) >= 64 and buf[:4] == MAGIC


def is_complete(buf: bytes) -> bool:
    """Would `parse_bytes` have every byte its own header asks for?

    `is_dson` reads the magic and nothing else, and the magic is the FIRST four
    bytes written — so it is present throughout a torn write and says yes to a
    save the game is halfway through rewriting. That is not a rare window:
    `bridge/run.py` polls on `(mtime, size)`, so the watcher is triggered *by*
    the write starting, and the copy it takes is the likeliest possible moment
    to be short. A live run died on exactly this ("not a DSON buffer (bad
    magic)" — the magic was fine; the header pointed past the end).

    Cheap on purpose: check the three declared extents against the bytes in
    hand rather than parsing, so `_stage` can afford to call it on every poll.

    **A plain-text save counts as complete if it parses.** A freshly created
    campaign holds `scripts/starting_save/`'s templates verbatim, and those are
    JSON with no magic (see `parse_file`). "Complete" here means "not torn", so
    answering False for a perfectly whole file would make the reader retry six
    times and then fail on every poll until the game happened to rewrite it.
    """
    if not is_dson(buf):
        if buf[:1] in (b"{", b"\xef"):
            try:
                json.loads(buf.decode("utf-8-sig"))
            except (ValueError, UnicodeDecodeError):
                return False
            return True
        return False
    try:
        _meta1_size, num_meta1, meta1_off = struct.unpack_from("<III", buf, 0x10)
        num_meta2, meta2_off = struct.unpack_from("<II", buf, 0x2C)
        data_len, data_off = struct.unpack_from("<II", buf, 0x38)
    except struct.error:
        return False
    return (meta1_off + num_meta1 * 16 <= len(buf)
            and meta2_off + num_meta2 * 12 <= len(buf)
            and data_off + data_len <= len(buf))


def parse_bytes(buf: bytes) -> Field:
    if not is_dson(buf):
        raise ValueError("not a DSON buffer (bad magic)")
    meta1_size, num_meta1, meta1_off = struct.unpack_from("<III", buf, 0x10)
    num_meta2, meta2_off = struct.unpack_from("<II", buf, 0x2C)
    data_len, data_off = struct.unpack_from("<II", buf, 0x38)

    meta1 = [struct.unpack_from("<IIII", buf, meta1_off + i * 16) for i in range(num_meta1)]

    # Field name offsets are needed pairwise: a value ends where the next
    # field's name begins.
    entries = []
    for i in range(num_meta2):
        _hash, off, info = struct.unpack_from("<III", buf, meta2_off + i * 12)
        entries.append((off, info))

    fields: list[Field] = []
    for i, (off, info) in enumerate(entries):
        is_obj = bool(info & 1)
        name_len = (info & 0b11111111100) >> 2
        nstart = data_off + off
        name = buf[nstart:nstart + name_len - 1].decode("utf-8", "replace")
        vstart = (nstart + name_len + 3) & ~3
        vend = data_off + entries[i + 1][0] if i + 1 < num_meta2 else data_off + data_len
        raw = buf[vstart:vend] if vend > vstart else b""
        f = Field(name=name, is_object=is_obj, raw_value=raw)
        if not is_obj:
            f.value = _decode_value(raw)
        fields.append(f)

    # meta1[k] = (parent, meta2 index, direct children, total children).
    # Children of an object are the meta2 entries immediately following it.
    #
    # `owner` below used to be `next((m for m in meta1 if m[1] == cursor), None)`
    # -- a LINEAR SCAN of meta1 inside a loop over every child of every object,
    # i.e. quadratic in the save's field count. It was 34% of the parse: 77,265
    # generator steps for 45 real captures. A `persist.roster.json` is a big
    # save, and this decoder is on the bridge's poll loop as well as under half
    # the test suite's wall clock.
    #
    # `setdefault` and not plain assignment, so a duplicated meta2 index keeps
    # the FIRST entry -- which is what `next()` returned. Same discipline as
    # `DataStore.skill_index`, and for the same reason: swapping first-wins for
    # last-wins is a silent behaviour change, not an optimisation.
    owner_by_meta2: dict[int, tuple] = {}
    for m in meta1:
        owner_by_meta2.setdefault(m[1], m)
    n_fields = len(fields)
    for parent_idx, meta2_idx, direct, _total in meta1:
        parent = fields[meta2_idx]
        cursor = meta2_idx + 1
        for _ in range(direct):
            if cursor >= n_fields:
                break
            parent.children.append(fields[cursor])
            owner = owner_by_meta2.get(cursor)
            cursor += 1 + (owner[3] if owner else 0)
    return fields[0]


def _field_from_json(name: str, value: Any) -> Field:
    if isinstance(value, dict):
        f = Field(name=name, is_object=True)
        f.children = [_field_from_json(k, v) for k, v in value.items()]
        return f
    if isinstance(value, list):
        # DSON has no list type: the game writes a list as an object whose
        # children are named "0", "1", ... So a JSON list becomes the same
        # shape, and a reader cannot tell which source it came from.
        f = Field(name=name, is_object=True)
        f.children = [_field_from_json(str(i), v) for i, v in enumerate(value)]
        return f
    return Field(name=name, is_object=False, value=value)


def parse_json_bytes(buf: bytes, root_name: str = "base_root") -> Field:
    """Build the same `Field` tree from a plain-text JSON save."""
    return _field_from_json(root_name, json.loads(buf.decode("utf-8-sig")))


def parse_file(path: str | Path) -> Field:
    """Parse a save, whether it is DSON or plain text JSON.

    **A freshly created campaign is plain text.** Creating a save slot copies
    `GAME_DIR/scripts/starting_save/`'s seven files into `profile_N/`
    byte-for-byte, and those templates ship as ordinary JSON with no DSON
    magic. The game rewrites each one as DSON the first time it saves it, but
    until then every reader in `bridge/` was raising `ValueError: not a DSON
    buffer (bad magic)` on the very files it had just been told to watch.
    Proven by `remotecache.vdf`, which still records a `profile_1` created
    today whose untouched `persist.loading_screen.json` (279 B) and
    `persist.upgrades.json` (3318 B) match the template byte counts exactly.

    The two formats are distinguished by the magic and nothing else -- no
    extension, no heuristic -- so this cannot mis-read a real save.
    """
    buf = Path(path).read_bytes()
    return parse_bytes(buf) if is_dson(buf) else parse_json_bytes(buf)


def main() -> int:
    import argparse
    import json

    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("save", help="a persist.*.json from a save profile")
    ap.add_argument("--json", action="store_true", help="dump the whole tree as JSON")
    ap.add_argument("--find", help="print every field with this name")
    args = ap.parse_args()

    root = parse_file(args.save)
    if args.find:
        for f in root.find_all(args.find):
            print(json.dumps(f.to_dict(), indent=1, ensure_ascii=False, default=str))
        return 0
    print(json.dumps(root.to_dict(), indent=1, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
