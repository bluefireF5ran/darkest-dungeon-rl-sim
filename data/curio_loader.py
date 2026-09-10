"""Parse `curios/curio_type_library.csv` — what every curio does, and which
provision changes the outcome.

**Deliberately NOT wired into `DataLoader.load_all`.** The engine does not
simulate curios (they are a dungeon-traversal mechanic, explicitly out of scope
in `ASSUMPTIONS.md`), so loading this on every env construction would be pure
cost. `bridge/curios.py` loads it lazily instead.

## The format

A human-authored spreadsheet dump, not a data file: one **block** per curio, with
two independent tables laid out side by side in the same rows. Column 2 carries a
vertical key/value list (`ID STRING` then the id, `REGION FOUND` then the region,
`TAGS` then the tags), while column 4 simultaneously runs down the fixed result-
type ladder (`Nothing`, `Loot`, `Quirk`, `Effect`, `Purge`, `Scouting`,
`Teleport`, `Disease`) with that block's weights in columns 5-6.

So the same physical row holds two unrelated facts, and neither table can be read
without ignoring the other. A block ends with an `Item Interactions` header, under
which each row names a provision in column 4 and its result type in column 5.

The **weights are what matter** and the percentages beside them are the author's
own arithmetic — several are stale. Normalise the weights; never trust the `%`.

The header row of each block also carries a one-word verdict in column 4 —
`Good`, `Mixed`, `Bad` — which is the game's own summary of whether touching the
thing bare-handed is worth it.
"""

from __future__ import annotations

import csv
import glob
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# Column 4's fixed ladder. Order matters only for recognising the rows.
RESULT_TYPES = ("Nothing", "Loot", "Quirk", "Effect", "Purge",
                "Scouting", "Teleport", "Disease")

# Column 2's vertical key/value list.
_KEYS = ("ID STRING", "REGION FOUND", "FULL CURIO?", "TAGS")

_COL_INDEX = 1
_COL_KEY = 2
_COL_TAG_EXTRA = 3
_COL_TYPE = 4
_COL_WEIGHT = 5
_COL_RESULT1 = 7


@dataclass(frozen=True)
class Result:
    """One concrete result under an outcome type, with its own draw weight.

    The name is domain-specific per type: a loot-table letter for `Loot`
    (plus the special `JOURNALONLY` and `<ITEM>ONLY` codes), an effect
    template name for `Effect`, a quirk id or the words `positive`/`negative`
    for `Quirk`, a disease id for `Disease`, `<level> - <what>` for `Scouting`.
    Judging any of them is the caller's job — see `bridge/curios.py`.
    """
    name: str
    weight: float = 1.0


@dataclass(frozen=True)
class Outcome:
    result_type: str
    weight: float
    results: tuple[Result, ...] = ()


@dataclass(frozen=True)
class ItemInteraction:
    """What happens when a specific provision is used on this curio."""
    item: str
    result_type: str
    results: tuple[Result, ...]
    text: str = ""


@dataclass(frozen=True)
class CurioDefinition:
    id: str
    name: str
    verdict: str                      # the sheet's own Good / Mixed / Bad
    region: str = ""
    tags: tuple[str, ...] = ()
    outcomes: dict[str, Outcome] = field(default_factory=dict)
    items: dict[str, ItemInteraction] = field(default_factory=dict)

    @property
    def total_weight(self) -> float:
        return sum(o.weight for o in self.outcomes.values())

    def chance(self, result_type: str) -> float:
        """Probability of a result type with **no item used**, from the weights.

        Computed, never read from the sheet's `% CHANCE` column — several of
        those are stale arithmetic left over from an edit.
        """
        total = self.total_weight
        outcome = self.outcomes.get(result_type)
        return (outcome.weight / total) if (outcome and total) else 0.0


def _clean(row: list[str], idx: int) -> str:
    return row[idx].strip() if len(row) > idx else ""


def _weight(row: list[str]) -> Optional[float]:
    raw = _clean(row, _COL_WEIGHT)
    try:
        return float(raw)
    except ValueError:
        return None


def _results(row: list[str]) -> tuple[Result, ...]:
    """RESULT 1/2/3 with their weights, at columns 7/10/13 and 8/11/14.

    The `%` columns beside them are ignored on purpose (stale arithmetic), and
    a weight that reads `<- # Draws` is the sheet reusing the column for a loot
    draw count rather than a weight, so it falls back to 1.
    """
    out = []
    for col in (_COL_RESULT1, _COL_RESULT1 + 3, _COL_RESULT1 + 6):
        name = _clean(row, col)
        if not name or name == "N/A":
            continue
        try:
            weight = float(_clean(row, col + 1))
        except ValueError:
            weight = 1.0
        out.append(Result(name=name, weight=weight))
    return tuple(out)


def _parse_block(rows: list[list[str]], start: int, end: int) -> Optional[CurioDefinition]:
    header = rows[start]
    name = _clean(header, _COL_KEY)
    verdict = _clean(header, _COL_TYPE)

    curio_id = ""
    region = ""
    tags: list[str] = []
    outcomes: dict[str, Outcome] = {}
    items: dict[str, ItemInteraction] = {}

    pending_key: Optional[str] = None
    in_items = False

    for row in rows[start + 1:end]:
        key = _clean(row, _COL_KEY)
        if key == "Item Interactions":
            in_items = True
            pending_key = None
            continue

        if not in_items:
            if key in _KEYS:
                pending_key = key
            elif key and pending_key:
                if pending_key == "ID STRING":
                    curio_id = key
                elif pending_key == "REGION FOUND":
                    region = key
                elif pending_key == "TAGS":
                    tags.append(key)
                    extra = _clean(row, _COL_TAG_EXTRA)
                    if extra:
                        tags.append(extra)
                # FULL CURIO? is a Yes/No the policy has no use for.
                if pending_key != "TAGS":
                    pending_key = None

            kind = _clean(row, _COL_TYPE)
            if kind in RESULT_TYPES:
                w = _weight(row)
                if w:
                    outcomes[kind] = Outcome(result_type=kind, weight=w,
                                             results=_results(row))
        else:
            item = _clean(row, _COL_TYPE)
            if not item:
                continue
            items[item] = ItemInteraction(
                item=item,
                result_type=_clean(row, _COL_WEIGHT),
                results=_results(row),
                text=_clean(row, 17),
            )

    if not curio_id:
        return None
    return CurioDefinition(id=curio_id, name=name, verdict=verdict, region=region,
                           tags=tuple(tags), outcomes=outcomes, items=items)


def load_prop_types(game_dir: str | Path) -> dict[str, str]:
    """Curio **prop** name -> curio **type** id, from `curios/curio_props.csv`.

    `persist.map.json` stores the prop; `curio_type_library.csv` is keyed by
    type. They are equal for most curios and NOT for all, so the mapping is not
    optional.
    """
    out: dict[str, str] = {}
    for path in glob.glob(str(Path(game_dir) / "**" / "curio_props.csv"), recursive=True):
        try:
            with open(path, newline="", encoding="utf-8", errors="replace") as fh:
                for row in csv.reader(fh):
                    prop = _clean(row, 0)
                    if prop and prop != "Curio Prop Name" and len(row) > 2:
                        out.setdefault(prop, _clean(row, 2) or prop)
        except OSError:
            continue
    return out


def load_curios(game_dir: str | Path) -> dict[str, CurioDefinition]:
    """id -> definition, base game plus DLC. Base wins on id collision, the same
    merge rule the rest of the loaders use."""
    out: dict[str, CurioDefinition] = {}
    paths = sorted(glob.glob(str(Path(game_dir) / "**" / "curio_type_library.csv"),
                             recursive=True),
                   key=lambda p: ("dlc" in p.lower(), p))
    for path in paths:
        try:
            with open(path, newline="", encoding="utf-8", errors="replace") as fh:
                rows = list(csv.reader(fh))
        except OSError:
            continue

        # Block starts are the only rows carrying an index in column 1.
        starts = [i for i, r in enumerate(rows)
                  if _clean(r, _COL_INDEX).isdigit() and _clean(r, _COL_KEY)]
        for n, start in enumerate(starts):
            end = starts[n + 1] if n + 1 < len(starts) else len(rows)
            curio = _parse_block(rows, start, end)
            if curio is not None and curio.id not in out:
                out[curio.id] = curio
    return out


if __name__ == "__main__":
    import sys
    from data.paths import GAME_DIR
    curios = load_curios(GAME_DIR)
    print(f"{len(curios)} curios\n")
    verdicts: dict[str, int] = {}
    items_seen: dict[str, int] = {}
    for c in curios.values():
        verdicts[c.verdict] = verdicts.get(c.verdict, 0) + 1
        for it in c.items:
            items_seen[it] = items_seen.get(it, 0) + 1
    print("verdicts:", verdicts)
    print("provisions used on curios:", items_seen)
    want = sys.argv[1] if len(sys.argv) > 1 else None
    for c in curios.values():
        if want and want not in c.id:
            continue
        print(f"\n{c.id}  [{c.verdict}]  tags={c.tags}")
        for kind, outcome in c.outcomes.items():
            detail = ", ".join(f"{r.name}x{r.weight:g}" for r in outcome.results)
            print(f"    {c.chance(kind):5.0%} {kind:9s} {detail}")
        for it in c.items.values():
            detail = ", ".join(r.name for r in it.results)
            print(f"  + {it.item:16s} -> {it.result_type:10s} {detail}")
