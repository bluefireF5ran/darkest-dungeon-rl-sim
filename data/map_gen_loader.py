"""Loader for `scripts/map_generator.darkest` — the real dungeon layout
generation parameters, and with them the real number of combats a raid of a
given size contains.

Why this file matters: `EXPEDITION_LENGTH_BANDS` in
`env/expedition_sequence_env.py` was originally an invented simulator
convention, because both CLAUDE.md and ASSUMPTIONS.md recorded that no game
data exposed per-length room counts ("layout generation is hardcoded in the
game binary"). That was wrong — this plain-text file ships with the game and
states the counts directly. See CHANGELOG.md for the measured gap.
"""

from __future__ import annotations
from pathlib import Path

from .types import MapGenSettings
from .darkest_parser import DarkestParser


class MapGenLoader:
    RELATIVE_PATH = Path("scripts") / "map_generator.darkest"

    @staticmethod
    def _pair(raw) -> tuple[int, int] | None:
        """`.hallway_battle 2 4` parses to the string "2 4" (the shared line
        parser keeps multi-value numerics as raw text). A single value is
        still valid and means a fixed count."""
        if raw is None:
            return None
        parts = str(raw).split()
        try:
            nums = [int(p) for p in parts]
        except ValueError:
            return None
        if not nums:
            return None
        return (nums[0], nums[-1])

    @staticmethod
    def load_all(game_dir: str | Path) -> list[MapGenSettings]:
        path = Path(game_dir) / MapGenLoader.RELATIVE_PATH
        if not path.exists():
            return []

        out: list[MapGenSettings] = []
        for rec in DarkestParser.parse_blocks(path):
            if rec.get("_type") != "map":
                continue
            hall = MapGenLoader._pair(rec.get("hallway_battle"))
            room = MapGenLoader._pair(rec.get("total_room_battles"))
            size, quest, dungeon = rec.get("size"), rec.get("quest_type"), rec.get("dungeon_type")
            if not (hall and room and size and quest and dungeon):
                continue
            out.append(MapGenSettings(
                size=str(size),
                quest_type=str(quest),
                dungeon_type=str(dungeon),
                base_room_number=int(rec.get("base_room_number", 0)),
                base_corridor_number=int(rec.get("base_corridor_number", 0)),
                hallway_battles=hall,
                room_battles=room,
            ))
        return out

    @staticmethod
    def combat_count_bands(settings: list[MapGenSettings]) -> dict[str, tuple[int, int]]:
        """Collapse the per-(size, quest_type, dungeon_type) records into one
        inclusive (min, max) combat-count band per size.

        The simulator has no concept of quest_type (verified: zero references),
        so a size's band is the envelope across every quest type and zone that
        the real game can generate at that size. Keeping the `short`/`medium`/
        `long` keys preserves the existing public API of
        `EXPEDITION_LENGTH_BANDS`.

        Insertion order is forced to short -> medium -> long rather than
        inherited from the file: callers index the bands positionally
        (`list(EXPEDITION_LENGTH_BANDS)[ep % 3]` in tools/analyze_behaviour.py,
        `list(...keys())` in eval_expedition.py), so a reordering in the game
        file would silently relabel every eval column.
        """
        acc: dict[str, tuple[int, int]] = {}
        for s in settings:
            lo, hi = s.total_battles
            if s.size in acc:
                cur_lo, cur_hi = acc[s.size]
                acc[s.size] = (min(cur_lo, lo), max(cur_hi, hi))
            else:
                acc[s.size] = (lo, hi)

        ordered = ("short", "medium", "long")
        bands = {k: acc[k] for k in ordered if k in acc}
        # Any size the game grows that this list doesn't know about still
        # surfaces rather than being silently dropped.
        bands.update({k: v for k, v in acc.items() if k not in bands})
        return bands
