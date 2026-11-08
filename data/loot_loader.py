"""What a loot draw is worth, and what darkness pays for it.

The last shipped table nothing in this repo read. `bridge/torchlight.py` has
known since 2026-08-14 that darkness pays "+30% gold and +12 treasure draws"
because `rules.json` says so -- but "+12 treasure draws" is not a quantity you
can compare against anything, so the torch layer could state the bonus and
never price it, and `bridge/route.py` priced a detour in *moves and fights*
and left the reward to the reader.

Three files close that, and all three were on disk the whole time.

## `loot/darkness_overrides.loot.json` -- the actual mechanism

"+12 treasure draws" is not twelve draws. It is a **chance of one extra draw
from a named table**, per event kind, and the game states it directly:

| torch | `battle` | `chest` |
|---|---|---|
| 76+ | 0% | 0% |
| 51-75 | 25% x [B] | 25% x [A] |
| 26-50 | 50% x [B] | 50% x [A] |
| 1-25 | 75% x [B] | 75% x [A] |
| **0 and below** | **75% x [B, B]** | **95% x [A]** |

Note what the bottom bracket actually does: the *chest* chance climbs 75 -> 95,
and the *battle* chance does not move at all -- it draws **twice**. That is why
snuffing to zero is worth so much more on a fight than the 75 -> 95 on a chest
suggests, and it is invisible in the `rules.json` summary both numbers are
folded into.

**The thresholds are the band FLOORS and they disagree with `rules.json` by one
point at three places.** `darkness.lowest_excluded` makes the rules bands
76-100 / 51-75 / 26-50 / 1-25 / <=0, so torch exactly 25 sits in the 1-25 band
and pays +15% gold -- while this table's keys 0 / 1 / 25 / 51 / 76 put 25 in
the "25" row at 50%. Both readings are taken from the file that states them;
they price different quantities and are deliberately not reconciled, because
reconciling them means overriding one shipped file with another.

## `loot/*.loot.json` -- 58+ tables, and they resolve

Keyed `(id, difficulty, dungeon)` with no duplicate key in the base game.
Entries are weighted by `chances`, and one entry type is `table`, so a draw is
a recursive walk: B is 3 parts C and 1 part G, and C is gold from 25 to 1000.

**A and S are not in `loot.json`.** They live in `common_overrides.loot.json`,
which is why a first pass reading only the main file could not price the
`chest` bonus at all. Every `**/*.loot.json` is merged, DLC included, base
winning on a key collision -- the same rule `DataLoader` follows.

## `inventory/*.inventory.items.darkest` -- the price list

`sell_gold_value` per item, and it covers more than expected: gems price
exactly (ancient_idol 4500, ruby 1250, sapphire 1000), and so do supplies and
provisions. So a draw's gold value is **parsed, not modelled**, for four of the
six item types the tables can yield.

**Heirlooms sell for 0 and that is deliberate** -- the game makes them
non-fungible on purpose. They are therefore counted, converted to crests
through the shipped exchange rates in `data/heirlooms.py`, and turned into a
comparable number by exactly one constant, `CREST_GOLD`, labelled as a
convention rather than as data. Trinkets carry only a rarity and are counted,
never priced: a trinket's worth to a campaign is not a sale price.

`LootValue.worth()` is the only place the convention is applied, so a caller
that wants the honest breakdown never has to touch it.

## ...and the COST side, which is in `rules.json` and was unread too

Darkness is not a free multiplier on loot, and two shipped rules say what it
charges. Both live here rather than in `bridge/`, because they are parsed game
data and the planner that spends them is policy.

* **`tile_light_loss`** -- clear 1.0, dim 6.0, dark 6.0 per tile. The torch
  burns **six times faster** once it drops out of the bright band, which is
  why light is nearly self-sustaining while you hold it and collapses once you
  do not. It is also why a torch budget is a real constraint: holding 76+
  across a long map costs a torch every twenty-five tiles.

* **`corridor_return_content`** -- walking back through a corridor you have
  already cleared **repopulates it**: `ac_battle` 5%, `ac_hunger` 7.5%,
  `ac_trap` 5%, each with a darkness modifier that reaches **+5% at torch 0**.
  So a dark homeward sweep does not merely risk the fights already on the map,
  it MAKES new ones, at double the lit rate. That is the price of deferring a
  curio to the way home, and it is the exact quantity "minimise low-torch
  battles" is about.

**One declared reading:** the chance is applied **per corridor re-entered**,
not per tile. The key is `corridor_return_content` and its siblings are
per-corridor concepts, and a per-tile reading would make a six-tile hallway a
near-certain ambush, which is not how the game plays. Falsifier: walk one
cleared six-tile corridor back at torch 0 ten times and count the battles --
per-corridor predicts ~1, per-tile predicts ~5.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Mapping, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data.darkest_parser import DarkestParser

from data.paths import GAME_DIR

#: Gold for one crest, the finest heirloom denomination. **NOT from a shipped
#: file** -- every heirloom is `sell_gold_value 0`, which is the game stating
#: that heirlooms do not convert to gold at all. This exists only so two routes
#: offering different currencies can be ordered, and it is the single
#: convention in this module.
#:
#: Falsifier, and the reason it is safe: a decision that turns on this number
#: is a decision the data cannot support. `tests/data/test_loot_loader.py` sweeps it
#: over three orders of magnitude and requires the Old Road plan not to move;
#: if some future map's plan *does* flip, that map's answer has to be reported
#: as undecided rather than resolved by this constant.
CREST_GOLD = 100.0

#: Item types whose `sell_gold_value` is a real price. `heirloom` is excluded
#: because its shipped value is 0 -- pricing it at 0 gold would silently make
#: every heirloom draw worthless rather than unpriced.
PRICED_ITEM_TYPES = frozenset({"gold", "gem", "supply", "provision", "quest_item"})

#: Of those, the ones the party CARRIES and spends inside the dungeon. A torch
#: sells for a few gold and is worth a whole fight's loot bracket while it is
#: lit; that difference is invisible to `sell_gold_value`, so the split is
#: recorded and the decision left to whoever is planning.
SUPPLY_ITEM_TYPES = frozenset({"supply", "provision"})

#: Loot codes that yield nothing of value. JOURNALONLY is `bridge/curios.py`'s
#: rule, restated because this module has to price it too.
WORTHLESS_CODES = frozenset({"JOURNALONLY", "NONE", "Nothing", "test"})


# -- the value of a draw --

@dataclass(frozen=True)
class LootValue:
    """Expected yield of one draw. Additive and scalable, so a plan is a sum.

    Everything priceable from shipped data is in `gold`. Everything else is
    kept as a count under its own name, so a caller can print the honest
    breakdown and only `worth()` ever collapses it to one number.
    """
    gold: float = 0.0
    heirlooms: Mapping[str, float] = field(default_factory=dict)
    trinkets: float = 0.0
    journal_pages: float = 0.0
    #: How much of `gold` is really `supply`/`provision` items -- torches,
    #: food, bandages, blood -- priced at their sell value. A SUBSET of
    #: `gold`, never added to it: `worth()` is unchanged and every existing
    #: caller sees the same number.
    #:
    #: It exists because a provision is worth more in the party's hands than
    #: in the wagon. Fran, 2026-09-06: *"curios that mostly produces
    #: provisions like torches, food or blood"* belong in the same class as
    #: the fountain -- used during the dungeon, not held back for the dark.
    #: Sell value cannot express that, so the planner needs to see the split.
    supplies: float = 0.0
    #: code -> expected number of draws that could not be resolved at all. An
    #: honest "I do not know", never folded into `gold` as a zero.
    unpriced: Mapping[str, float] = field(default_factory=dict)

    def __add__(self, other: "LootValue") -> "LootValue":
        return LootValue(
            gold=self.gold + other.gold,
            heirlooms=_merge(self.heirlooms, other.heirlooms),
            trinkets=self.trinkets + other.trinkets,
            journal_pages=self.journal_pages + other.journal_pages,
            supplies=self.supplies + other.supplies,
            unpriced=_merge(self.unpriced, other.unpriced))

    def __mul__(self, k: float) -> "LootValue":
        return LootValue(
            gold=self.gold * k,
            heirlooms={n: v * k for n, v in self.heirlooms.items()},
            trinkets=self.trinkets * k,
            journal_pages=self.journal_pages * k,
            supplies=self.supplies * k,
            unpriced={n: v * k for n, v in self.unpriced.items()})

    __rmul__ = __mul__

    @property
    def empty(self) -> bool:
        return not (self.gold or self.heirlooms or self.trinkets
                    or self.journal_pages or self.unpriced)

    def with_gold_multiplier(self, mult: float) -> "LootValue":
        """`loot_increase_gold` scales GOLD and nothing else.

        Applying it to the whole value would silently inflate heirloom and
        trinket counts, which the darkness table says nothing about -- it names
        `loot_increase_gold` and `loot_increase_treasure_draws` as two separate
        columns, and the draws are `darkness_value`'s job.
        """
        return LootValue(gold=self.gold * mult, heirlooms=dict(self.heirlooms),
                         trinkets=self.trinkets,
                         journal_pages=self.journal_pages,
                         unpriced=dict(self.unpriced))

    def crests(self, values: Optional[Mapping[str, float]] = None) -> float:
        """Heirlooms in crests, per the shipped exchange rates."""
        values = values if values is not None else heirloom_crest_values()
        return sum(n * float(values.get(name, 1.0))
                   for name, n in self.heirlooms.items())

    def worth(self, crest_gold: float = CREST_GOLD,
              values: Optional[Mapping[str, float]] = None) -> float:
        """One comparable number. The ONLY place `CREST_GOLD` is applied."""
        return self.gold + crest_gold * self.crests(values)

    def line(self) -> str:
        bits = [f"{self.gold:.0f}g"]
        for name, n in sorted(self.heirlooms.items()):
            if n >= 0.005:
                bits.append(f"{n:.2f} {name}")
        if self.trinkets >= 0.005:
            bits.append(f"{self.trinkets:.2f} trinket")
        if self.unpriced:
            bits.append("unpriced " + ",".join(sorted(self.unpriced)))
        return " + ".join(bits)


def _merge(a: Mapping[str, float], b: Mapping[str, float]) -> dict:
    out = dict(a)
    for key, value in b.items():
        out[key] = out.get(key, 0.0) + value
    return out


# -- the shipped price list --

@lru_cache(maxsize=4)
def item_sell_values(game_dir: str = str(GAME_DIR)) -> Mapping[tuple, float]:
    """`(type, id) -> sell_gold_value`, over every inventory item file.

    Zero-valued entries are kept: `PRICED_ITEM_TYPES` decides what may be
    priced, and a present-but-zero entry is a real price (firewood does sell
    for nothing) while an absent one is a gap.
    """
    out: dict[tuple, float] = {}
    root = Path(game_dir)
    paths = sorted(root.glob("inventory/*.inventory.items.darkest"))
    paths += sorted(root.glob("dlc/**/inventory/*.inventory.items.darkest"))
    for path in paths:
        try:
            records = DarkestParser.parse_file(path)
        except (OSError, ValueError):
            continue
        for rec in records:
            if rec.get("_type") != "inventory_item":
                continue
            key = (str(rec.get("type") or ""), str(rec.get("id") or ""))
            out.setdefault(key, float(rec.get("sell_gold_value") or 0.0))
    return out


@lru_cache(maxsize=4)
def heirloom_crest_values(game_dir: str = str(GAME_DIR)) -> Mapping[str, float]:
    """Each heirloom in crests, off `campaign/heirloom_exchange`."""
    from data.heirlooms import crest_values, load_rates      # noqa: PLC0415

    try:
        return dict(crest_values(load_rates(game_dir)))
    except Exception:                                        # noqa: BLE001
        return {}


# -- the darkness bonus --

@dataclass(frozen=True)
class DarknessBonus:
    """One row of `darkness_overrides.loot.json`."""
    kind: str
    threshold: float
    chance: float
    codes: tuple[str, ...]


@lru_cache(maxsize=4)
def load_darkness_bonuses(game_dir: str = str(GAME_DIR)) -> Mapping[str, tuple]:
    """`{kind: (DarknessBonus, ...)}`, each tuple sorted by threshold."""
    path = Path(game_dir) / "loot" / "darkness_overrides.loot.json"
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            raw = json.load(fh)
    except (OSError, ValueError):
        return {}
    out: dict[str, list] = {}
    for block in raw.get("darkness_bonuses") or []:
        kind = str(block.get("key") or "")
        rows = []
        for row in block.get("data") or []:
            rows.append(DarknessBonus(
                kind=kind,
                threshold=float(row.get("darkness") or 0.0),
                chance=float(row.get("chance") or 0.0),
                codes=tuple(str(c) for c in (row.get("codes") or ()))))
        out[kind] = sorted(rows, key=lambda b: b.threshold)
    return {k: tuple(v) for k, v in out.items()}


def bonus_for(kind: str, torch: float,
              game_dir: str = str(GAME_DIR)) -> Optional[DarknessBonus]:
    """The row applying at this torch level: the highest threshold <= torch.

    Below every threshold there is still a row, because the table's own bottom
    key is 0 and nothing clamps the torch above it -- so a negative torch takes
    the 0 row, exactly as `darkness.range_table`'s -inf..0 bracket does.
    """
    rows = load_darkness_bonuses(game_dir).get(kind) or ()
    chosen = None
    for row in rows:
        if torch >= row.threshold:
            chosen = row
    return chosen or (rows[0] if rows else None)


# -- the tables --

@dataclass(frozen=True)
class LootEntry:
    kind: str                      # nothing | table | item | trinket | journal_page
    chances: float
    data: Mapping = field(default_factory=dict)


class LootTables:
    """Every `*.loot.json` in the install, merged and resolvable.

    Lookup falls back in two steps, both of which the shipped data needs:
    `dungeon` to "" (most tables are dungeon-agnostic) and `difficulty` down to
    the highest defined value at or below the one asked for (the files define
    0, 1, 3, 5 and 6 -- the mash tiers, not every integer).
    """

    def __init__(self, tables: Mapping[tuple, tuple]):
        self._tables = dict(tables)
        self._memo: dict[tuple, LootValue] = {}

    def lookup(self, code: str, difficulty: int = 0,
               dungeon: str = "") -> Optional[tuple]:
        for dun in (dungeon, ""):
            best = None
            for (cid, diff, dun2), entries in self._tables.items():
                if cid != code or dun2 != dun:
                    continue
                if diff <= difficulty and (best is None or diff > best[0]):
                    best = (diff, entries)
            if best is not None:
                return best[1]
            # Nothing at or below the difficulty asked for: take the lowest
            # defined one rather than calling the code missing, since a table
            # defined only at difficulty 3 is still that code's table.
            above = sorted(((diff, entries) for (cid, diff, dun2), entries
                            in self._tables.items()
                            if cid == code and dun2 == dun),
                           key=lambda pair: pair[0])
            if above:
                return above[0][1]
        return None

    @property
    def codes(self) -> frozenset:
        return frozenset(cid for cid, _diff, _dun in self._tables)

    def expected(self, code: str, difficulty: int = 0, dungeon: str = "",
                 game_dir: str = str(GAME_DIR)) -> LootValue:
        """Expected yield of one draw from `code`, resolving nested tables."""
        key = (code, difficulty, dungeon, game_dir)
        cached = self._memo.get(key)
        if cached is None:
            cached = self._expected(code, difficulty, dungeon, game_dir, ())
            self._memo[key] = cached
        return cached

    def _expected(self, code: str, difficulty: int, dungeon: str,
                  game_dir: str, seen: tuple) -> LootValue:
        if code in WORTHLESS_CODES:
            return LootValue()
        if code in seen:
            # A cycle. Nothing shipped has one, and returning empty rather
            # than recursing keeps a modded table from hanging the planner.
            return LootValue()
        entries = self.lookup(code, difficulty, dungeon)
        if entries is None:
            return LootValue(unpriced={code: 1.0})
        total = sum(max(0.0, e.chances) for e in entries)
        if total <= 0:
            return LootValue()
        prices = item_sell_values(game_dir)
        out = LootValue()
        for entry in entries:
            weight = max(0.0, entry.chances) / total
            if weight <= 0:
                continue
            out = out + weight * self._entry_value(
                entry, difficulty, dungeon, game_dir, seen + (code,), prices)
        return out

    def _entry_value(self, entry: LootEntry, difficulty: int, dungeon: str,
                     game_dir: str, seen: tuple, prices) -> LootValue:
        if entry.kind == "nothing":
            return LootValue()
        if entry.kind == "journal_page":
            return LootValue(journal_pages=1.0)
        if entry.kind == "trinket":
            return LootValue(trinkets=1.0)
        if entry.kind == "table":
            return self._expected(str(entry.data.get("table") or ""),
                                  difficulty, dungeon, game_dir, seen)
        if entry.kind == "item":
            kind = str(entry.data.get("type") or "")
            item_id = str(entry.data.get("id") or "")
            amount = float(entry.data.get("amount") or 0.0)
            if kind == "gold":
                return LootValue(gold=amount)
            if kind == "heirloom":
                return LootValue(heirlooms={item_id or "crest": amount})
            if kind in PRICED_ITEM_TYPES:
                price = prices.get((kind, item_id))
                if price is None:
                    return LootValue(unpriced={f"{kind}:{item_id}": 1.0})
                worth = amount * float(price)
                # `supplies` shadows `gold` rather than replacing it -- see the
                # field. `gem` and `quest_item` are priced loot like any other.
                return LootValue(gold=worth,
                                 supplies=worth if kind in SUPPLY_ITEM_TYPES
                                 else 0.0)
            return LootValue(unpriced={f"{kind}:{item_id}": 1.0})
        return LootValue(unpriced={entry.kind: 1.0})


@lru_cache(maxsize=4)
def load_loot_tables(game_dir: str = str(GAME_DIR)) -> LootTables:
    """Every `**/*.loot.json`, base first so base wins on a key collision.

    `darkness_overrides` is excluded: it is not a table file but the bonus
    schedule, and its `codes` name tables defined elsewhere.
    """
    root = Path(game_dir)
    # `loot/loot.json` is the main file and does NOT match `*.loot.json`, so a
    # glob on the suffix alone silently loses every base table and leaves the
    # whole model reporting "unpriced". Found on the first run.
    paths = sorted(root.glob("loot/*.json"))
    paths += sorted(root.glob("dlc/**/loot/*.json"))
    paths += sorted(root.glob("dlc/**/*.loot.json"))
    tables: dict[tuple, tuple] = {}
    for path in paths:
        if path.name == "darkness_overrides.loot.json":
            continue
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                raw = json.load(fh)
        except (OSError, ValueError):
            continue
        for table in raw.get("loot_tables") or []:
            key = (str(table.get("id") or ""),
                   int(table.get("difficulty") or 0),
                   str(table.get("dungeon") or ""))
            if key in tables:
                continue
            tables[key] = tuple(
                LootEntry(kind=str(entry.get("type") or ""),
                          chances=float(entry.get("chances") or 0.0),
                          data=dict(entry.get("data") or {}))
                for entry in table.get("entries") or [])
    return LootTables(tables)


# -- what darkness is worth on one event --

def darkness_value(kind: str, torch: float, difficulty: int = 0,
                   dungeon: str = "",
                   game_dir: str = str(GAME_DIR)) -> LootValue:
    """Expected EXTRA loot this event pays at `torch`, over its own drop.

    `kind` is "battle" or "chest". This is the bonus alone -- the event's base
    drop is the same at every light level -- so the gain from going darker is a
    plain subtraction of two of these.
    """
    bonus = bonus_for(kind, torch, game_dir)
    if bonus is None or bonus.chance <= 0 or not bonus.codes:
        return LootValue()
    tables = load_loot_tables(game_dir)
    out = LootValue()
    for code in bonus.codes:
        out = out + bonus.chance * tables.expected(code, difficulty, dungeon,
                                                   game_dir)
    return out


def gold_multiplier(torch: float, game_dir: str = str(GAME_DIR)) -> float:
    """`1 + loot_increase_gold/100` at this torch, from `rules.json`.

    The second half of what darkness pays, and it multiplies the base drop as
    well as the bonus draw. Read through `data.rules` so there is one darkness
    table in the process rather than two.
    """
    from data.rules import RulesLoader                       # noqa: PLC0415

    try:
        rules = RulesLoader.load(Path(game_dir) / "shared" / "rules.json")
    except (OSError, ValueError):
        return 1.0
    for band in rules.darkness_table:
        if band.lower < torch <= band.upper:
            return 1.0 + float(band.loot_increase_gold) / 100.0
    return 1.0


# -- what walking costs --

@dataclass(frozen=True)
class WalkCosts:
    """What one tile and one corridor re-entry cost at a given torch level."""
    light_loss: float = 1.0
    #: A tile already walked. See `walk_costs`.
    revisit_light_loss: float = 1.0
    return_battle: float = 0.0
    return_hunger: float = 0.0
    return_trap: float = 0.0
    stress_forward: float = 0.0
    stress_backing: float = 0.0

    @property
    def return_any(self) -> float:
        """Chance a re-entered corridor hands back SOMETHING unwanted."""
        return self.return_battle + self.return_hunger + self.return_trap


@lru_cache(maxsize=8)
def _raw_rules(game_dir: str = str(GAME_DIR)) -> Mapping:
    path = Path(game_dir) / "shared" / "rules.json"
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _band_value(table: Mapping, torch: float, default: float = 0.0) -> float:
    """Read a `lowest_excluded` range table at one torch level."""
    for row in (table or {}).get("range_table") or []:
        rng = row.get("range") or {}
        lower = float(rng.get("lower", 0.0))
        upper = float(rng.get("upper", 0.0))
        if lower < torch <= upper:
            value = row.get("value")
            return float(value) if isinstance(value, (int, float)) else default
    return default


def walk_costs(torch: float, game_dir: str = str(GAME_DIR)) -> WalkCosts:
    """`tile_light_loss`, `corridor_return_content` and `hallway_stress`.

    `tile_light_loss` is keyed by a light NAME rather than a range, and the
    three keys are `clear` / `dim` / `dark` against the darkness table's five
    brackets. Mapped by what the brackets mean rather than by position: the
    bright bracket is clear, and everything below it burns at the dim rate --
    which the file makes easy by giving dim and dark the same 6.0.
    """
    raw = _raw_rules(game_dir)
    loss = {str(row.get("key")): float(row.get("data") or 0.0)
            for row in raw.get("tile_light_loss") or []}
    clear = loss.get("clear", 1.0)
    dim = loss.get("dim", loss.get("dark", clear))

    chances: dict[str, float] = {}
    for row in raw.get("corridor_return_content") or []:
        data = row.get("data") or {}
        base = float(data.get("base_chance") or 0.0)
        chances[str(row.get("key"))] = base + _band_value(
            data.get("base_dark_mod_table") or {}, torch)

    stress = raw.get("hallway_stress") or {}
    # **`clear` / `dim` / `dark` are TILE states, not light levels.**
    # Corrected 2026-09-23 from the live logs: walking a NEW corridor tile at
    # torch 100 costs 6 (100 -> 94 recurs across a dozen clean moves), and a
    # re-walked corridor costs about 1-2. The old reading (1 per tile while
    # radiant, 6 once dim) under-priced every planned walk in the light
    # six-fold. A new tile burns the `dim` rate at any light; a cleared one
    # burns `clear`.
    del torch  # the light level does not change the per-tile cost
    return WalkCosts(
        light_loss=dim,
        revisit_light_loss=clear,
        return_battle=chances.get("ac_battle", 0.0),
        return_hunger=chances.get("ac_hunger", 0.0),
        return_trap=chances.get("ac_trap", 0.0),
        stress_forward=(float(stress.get("hallway_per_tile_stress_damage_fwd") or 0.0)
                        * float(stress.get(
                            "hallway_per_tile_stress_damage_chance_fwd") or 0.0)),
        stress_backing=(float(stress.get(
                            "hallway_per_tile_stress_damage_backing_up") or 0.0)
                        * float(stress.get(
                            "hallway_per_tile_stress_damage_chance_backing_up") or 0.0)),
    )


def main() -> None:                                          # pragma: no cover
    import argparse

    ap = argparse.ArgumentParser(description="what a loot draw is worth")
    ap.add_argument("--code", default=None, help="a loot table id, e.g. A")
    ap.add_argument("--difficulty", type=int, default=0)
    ap.add_argument("--dungeon", default="")
    ap.add_argument("--darkness-table", action="store_true")
    ap.add_argument("--walk-costs", action="store_true")
    ap.add_argument("--codes", action="store_true", help="list every table id")
    ap.add_argument("--game-dir", default=str(GAME_DIR))
    args = ap.parse_args()

    tables = load_loot_tables(args.game_dir)
    if args.codes:
        print(" ".join(sorted(tables.codes)))
    if args.darkness_table:
        print(f"{'torch':>7}  {'kind':<7} {'chance':>7}  {'codes':<8}"
              f"{'expected extra':>26}   gold")
        for torch in (100.0, 75.0, 50.0, 25.0, 0.0):
            for kind in ("battle", "chest"):
                bonus = bonus_for(kind, torch, args.game_dir)
                value = darkness_value(kind, torch, args.difficulty,
                                       args.dungeon, args.game_dir)
                codes = ",".join(bonus.codes) if bonus else "-"
                chance = f"{bonus.chance:.0%}" if bonus else "-"
                print(f"{torch:>7.0f}  {kind:<7} {chance:>7}  {codes:<8}"
                      f"{value.line():>26}   "
                      f"x{gold_multiplier(torch, args.game_dir):.2f}")
    if args.walk_costs:
        print(f"{'torch':>7}  {'light/tile':>10}  {'re-battle':>9} "
              f"{'re-hunger':>9} {'re-trap':>7}  {'stress fwd':>10} "
              f"{'stress back':>11}")
        for torch in (100.0, 75.0, 50.0, 25.0, 0.0):
            cost = walk_costs(torch, args.game_dir)
            print(f"{torch:>7.0f}  {cost.light_loss:>10.1f}  "
                  f"{cost.return_battle:>8.1%} {cost.return_hunger:>9.1%} "
                  f"{cost.return_trap:>7.1%}  {cost.stress_forward:>10.2f} "
                  f"{cost.stress_backing:>11.2f}")

    if args.code:
        value = tables.expected(args.code, args.difficulty, args.dungeon,
                                args.game_dir)
        print(f"{args.code} @ difficulty {args.difficulty} "
              f"{args.dungeon or '(any dungeon)'}: {value.line()}")
        print(f"  worth {value.worth():.0f} "
              f"(gold {value.gold:.0f} + {value.crests():.2f} crests "
              f"@ {CREST_GOLD:.0f})")


if __name__ == "__main__":                                   # pragma: no cover
    main()
