from __future__ import annotations
import re
from pathlib import Path
from typing import Any

from .text_io import read_lines


class DarkestParser:
    """Parse .darkest key-value format into structured records.

    Format per line:
        record_type: .key1 "val1" .key2 115% .key3 4321 .key4 "quoted val" .key5 true

    Known record types: resistances, weapon, armour, combat_skill, combat_move_skill,
                        mode, tag, deaths_door, controlled, id_index, skill_selection,
                        generation, display, enemy_type, stats, personality, loot,
                        initiative, monster_brain, death_class, battle_modifier,
                        wave_spawning, effect
    """

    LINE_RE = re.compile(
        r'(?P<key>\.[a-zA-Z_]+)\s*'
        r'(?P<val>"(?:[^"\\]|\\.)*"|%?\S+(?:%?)|true|false)'
    )

    @staticmethod
    def parse_file(path: str | Path) -> list[dict[str, Any]]:
        records = []
        for line in read_lines(path):
            line = line.strip()
            if not line or line.startswith("//"):
                continue
            record = DarkestParser._parse_line(line)
            if record:
                records.append(record)
        return records

    @staticmethod
    def parse_blocks(path: str | Path) -> list[dict[str, Any]]:
        """Parse the *multi-line* variant of the .darkest format.

        `.info.darkest` / `.mash.darkest` put a whole record on one line, which
        `parse_file` handles. A few files instead put the `type:` header on its
        own line with one `.key value` per following line — the one that
        matters here is `scripts/map_generator.darkest`:

            map:
            .size short
            .hallway_battle 2 4

        Joining each block back into a single logical line lets `_parse_line`
        do the actual work, so both layouts share one implementation and one
        set of value-parsing quirks. Note multi-value numerics (`.hallway_battle
        2 4`) come back as the string `"2 4"`, same as via `parse_file`.
        """
        blocks: list[str] = []
        current: str | None = None
        for line in read_lines(path):
            line = line.strip()
            if not line or line.startswith("//"):
                continue
            if line.startswith("."):
                if current is not None:
                    current += " " + line
            elif ":" in line:
                if current is not None:
                    blocks.append(current)
                current = line
        if current is not None:
            blocks.append(current)
        return [r for r in (DarkestParser._parse_line(b) for b in blocks) if r]

    @staticmethod
    def _parse_line(line: str) -> dict[str, Any] | None:
        if ":" not in line:
            return None
        record_type, body = line.split(":", 1)
        record_type = record_type.strip()

        # Quoted alternative matches one or more consecutive quoted strings
        # (e.g. .effect "A" "B") without swallowing the trailing separator
        # whitespace the shared lookahead needs. Unquoted alternative also
        # accepts '@'/'~' prefixes used in position notation (@1234, ~123),
        # '?' IS IN THE CLASS TOO, and leaving it out cost a vanilla skill.
        # `decode_position_notation` was taught about `?1234` when the modded
        # corpus turned up 88 of them -- but the value never reached it,
        # because the character class here has no '?', the value alternative
        # failed to match, and (per the note further down) an unmatchable
        # value drops the WHOLE key. `.target` therefore vanished from the
        # record entirely rather than arriving malformed.
        #
        # It is not only a mod shape: `.target ?1234` is shipped on the
        # Arbalest's **blindfire** (all five levels, a base-game hero) and the
        # Musketeer's **sidearm**. Both read `target=[]`, so a rank-4 shot the
        # real game aims at any enemy rank could not be aimed at all.
        # `tests/data/test_darkest_parser.py` pins it.
        # and bare word characters (a-zA-Z_) for unquoted identifiers like
        # `.id cultist_brawler_A`, `.deathfx death_medium`, and capitalized
        # `True`/`False` (e.g. `.accelerate_stall_penalty False`) — verified
        # these are common in monster files (~168 unquoted `.id <name>`
        # lines) and were silently dropped entirely before (same failure
        # mode as the earlier @/malformed-line bugs: not in the character
        # class at all, so the whole key vanished from the parsed record).
        # Lookahead tolerates zero whitespace before the next key: some game
        # files have typos with no space (e.g. `.name "Arb Mark Target".target`
        # in base.effects.darkest) — requiring \s+ there silently dropped the
        # whole key.
        # Negative lookahead before the unquoted alternative: a bare word
        # class now allows letters (see above), which would otherwise let an
        # empty-valued key (e.g. `.target` on a self-only skill, immediately
        # followed by `.is_crit_valid True`) swallow the *next* key's name as
        # if it were its own value — `.target` has no content, so this must
        # refuse to match rather than eat into `.is_crit_valid`.
        # The value is OPTIONAL, because a VALUELESS FLAG is a real thing in
        # this format and the old regex kept or dropped one depending on how
        # much whitespace happened to follow it. `\s+` swallowed the single
        # separator, leaving the value alternatives nothing to match, so the
        # whole key vanished; with TWO spaces the second one matched as the
        # value and the key survived as ''. Measured across every file the
        # loaders read: `.shuffletarget` was dropped 17 times and survived
        # twice, in the same file. That is what made the entire shuffle-target
        # mechanic -- Bounty Hunter Flashbang, Plague Doctor Disorienting
        # Blast, every `Disorient N` -- invisible while `.shuffleparty`, which
        # happens to be written with two spaces, worked.
        #
        # Greedy `?`, so a key WITH a value still binds it. A value that exists
        # but does not fit the character class still drops the whole key, as
        # before: the lookahead has to hold immediately after the key, so an
        # unmatchable value fails the match rather than being silently skipped.
        pairs = re.findall(
            r'\.([a-zA-Z_]+)'
            r'(?:\s+'
            r'(?:("(?:[^"\\]|\\.)*"(?:\s+"(?:[^"\\]|\\.)*")*)'
            r'|((?!\.[a-zA-Z_])[@~?0-9a-zA-Z_.,%\-+\s]+?))'
            r')?'
            r'(?=\s*\.[a-zA-Z_]|$)',
            body
        )
        data: dict[str, Any] = {"_type": record_type}
        for key, quoted_blob, unquoted in pairs:
            if quoted_blob:
                values = [DarkestParser._parse_value(v) for v in
                          re.findall(r'"((?:[^"\\]|\\.)*)"', quoted_blob)]
                data[key] = values if len(values) > 1 else values[0]
            else:
                data[key] = DarkestParser._parse_value(unquoted.strip())
        return data

    @staticmethod
    def _parse_value(raw: str) -> Any:
        raw = raw.strip()
        if raw.lower() == "true":
            return True
        if raw.lower() == "false":
            return False
        if raw.startswith('"') and raw.endswith('"'):
            return raw[1:-1]
        if raw == "None":
            return None
        if "%" in raw:
            num = raw.replace("%", "").replace(",", ".").strip()
            try:
                return float(num) / 100.0
            except ValueError:
                pass
        if re.match(r'^-?\d+$', raw):
            return int(raw)
        if re.match(r'^-?[\d,]+\.?\d*$', raw.replace(",", ".")):
            return float(raw.replace(",", "."))
        return raw

    @staticmethod
    def decode_position_notation(pos_str: str) -> list[int]:
        """Decode position notation like '4321', '32', '~23', '@1234', '@~123', '~@123' into int list.

        '~' and '@' are independent modifier prefixes and appear in either
        order ('@~1234' and '~@1234' both occur in game files). '@' marks a
        target list that includes the performer's own rank (self-targetable
        heal/buff skills); '~' marks AoE (see is_aoe detection in hero_loader).
        Neither changes which ranks are listed, so both are stripped before
        decoding digits.

        Shipped data is always `[@~]*<digits>`, but a MOD is not obliged to
        be: the Workshop corpus carries `?1234` (88 times, an unknown
        modifier prefix) and `1234.is_user_selected_targets` (79 times, a
        missing space that glued the next key onto the value). Both used to
        reach `int(c)` and raise, and because this runs inside the shared
        skill build one such file took down the load for EVERY modded class.

        So only DIGITS are decoded and anything else is dropped. That is the
        right answer for all three shapes rather than merely a quiet one:
        `?1234` means ranks 1-4, and so does the glued form. Shipped data is
        unaffected, since it has nothing to drop.
        """
        if not pos_str:
            return []
        return [int(c) for c in str(pos_str) if c.isdigit()]
