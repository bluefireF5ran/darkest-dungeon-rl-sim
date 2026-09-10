"""What a hero is WORTH, in gold — the number a retreat is weighed against.

Fran, 2026-09-02, on the retreat valuation being wrong:

    each hero starts with 4 skills (outside of 7 skill heroes) so each one that
    has other skills unlocked is +1000 per other skill unlocked +X per level of
    each skill +Y per level of armor +Z per level of weapon, +N per positive
    quirk locked, +All the value of the inventory

`data/town_costs.py::hero_investment` had three of those wrong at once, and all
three erred in the same direction — they priced the hero by what is EQUIPPED
rather than by what was BOUGHT:

* **It charged for the four skills the recruit arrived with.** Rank 1 of a
  skill tree is 1000 gold, and a fresh hero already has four trees at rank 1
  that nobody paid for. Charging them inflates every hero by up to 4000.
* **It read `selected_skills`, the four EQUIPPED**, so a hero who unlocked six
  and equipped four was valued as if the other two had never been bought —
  which is exactly the "+1000 per other skill unlocked" Fran is describing, and
  it is the half that grows as a campaign goes on.
* **It assumed every skill sits at the weapon's rank.** That was a documented
  convention, made because nothing could read the real ranks. `data/hero_
  skills.py` reads them per skill out of `persist.upgrades.json` now, so the
  convention is not needed and is simply wrong for any hero upgraded unevenly.

## Everything here is a shipped number

| what | gold | source |
|---|---|---|
| skill rank 1 (the unlock) | 1000 | `upgrades/heroes/<class>.upgrades.json` |
| skill ranks 2-5 | 250 / 750 / 1250 / 2500 | same |
| weapon / armour ranks | 750 / 1750 / 3000 / 6000 | same |
| free skill slots | **4**, or **7** | that class's `.info.darkest` |
| locking a positive quirk | 7500 / 6750 / 6000 | `sanitarium.building.json` |

**The free-slot count is DERIVED, not the constant 4.**
`skill_selection: .number_of_selected_combat_skills_max` is 4 on eighteen
classes and 7 on the Abomination and the Duelist — the same key
`unrestricted_heroes` reads `can_select_combat_skills` from, so the two agree by
construction and a modded stance class is priced correctly with no entry
anywhere.

## `is_locked` is a real bool, and the POSITIVE filter is load-bearing

`persist.roster.json` stores `quirks.<id>.is_locked` as a 1-byte DSON bool —
unlike `persist.upgrades.json`'s `is_purchased`, which decodes to nothing on
every entry. Over both live campaigns: 25 `False`, **1 `True`**, and 115
zero-length (absent, i.e. not locked).

That single `True` is why the filter exists rather than being tidiness: it is
`torn_rotator_cuff` on a Vestal — a **negative** quirk. The game locks diseases
and permanent negatives on its own, and nobody paid 7500 gold for one. Counting
every locked quirk would have valued a diseased hero as though they carried a
bought positive.

## What is deliberately NOT priced

**Trinkets have no gold value in shipped data** — DD does not sell them, and
`base.entries.trinkets.json` carries no price field. `InventoryValue` reports
the count and prices only what the game itself prices (carried gold and
provisions at their purchase price). Inventing a trinket price would be a
convention wearing game data's clothes, and the retreat decision would inherit
it silently.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Optional

from data.darkest_parser import DarkestParser
from data.hero_skills import gear_ranks, unlocked
from data.town_costs import load_hero_costs

from data.paths import GAME_DIR

# What a hero can equip, and therefore what a recruit arrives owning, when the
# class file cannot be read. Eighteen of twenty classes; the fallback, never
# the answer -- `skill_slots` reads the real number.
DEFAULT_SKILL_SLOTS = 4

_SANITARIUM = ("campaign", "town", "buildings", "sanitarium",
               "sanitarium.building.json")


@lru_cache(maxsize=64)
def _info_path(game_dir: str, hero_class: str) -> Optional[Path]:
    root = Path(game_dir)
    direct = root / "heroes" / hero_class / f"{hero_class}.info.darkest"
    if direct.is_file():
        return direct
    for path in sorted(root.glob(f"dlc/**/heroes/{hero_class}/{hero_class}.info.darkest")):
        if "modes" not in path.parts:
            return path
    return None


@lru_cache(maxsize=64)
def skill_slots(hero_class: str, game_dir: str = str(GAME_DIR)) -> int:
    """How many combat skills this class arrives owning, and can equip.

    `skill_selection: .number_of_selected_combat_skills_max` — 4 on eighteen
    classes, 7 on the Abomination and the Duelist, which are exactly the two
    with `can_select_combat_skills false`. Deriving it rather than listing them
    is what makes a modded stance class priced right with no entry here.
    """
    path = _info_path(game_dir, hero_class)
    if path is None:
        return DEFAULT_SKILL_SLOTS
    try:
        records = DarkestParser.parse_file(path)
    except (OSError, ValueError):
        return DEFAULT_SKILL_SLOTS
    for rec in records:
        if rec.get("_type") != "skill_selection":
            continue
        try:
            return int(rec.get("number_of_selected_combat_skills_max")
                       or DEFAULT_SKILL_SLOTS)
        except (TypeError, ValueError):
            return DEFAULT_SKILL_SLOTS
    return DEFAULT_SKILL_SLOTS


@lru_cache(maxsize=8)
def quirk_lock_prices(game_dir: str = str(GAME_DIR)) -> tuple[int, ...]:
    """Gold to lock ONE positive quirk, per Sanitarium upgrade tier.

    7500 / 6750 / 6000 in shipped data. The negative-removal and
    disease-removal ladders live beside it and are deliberately not read here:
    removing a quirk is money spent on a problem, not value stored in a hero,
    so it is not what dies with them.
    """
    import json

    path = Path(game_dir).joinpath(*_SANITARIUM)
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            raw = json.load(fh)
    except (OSError, ValueError):
        return ()
    for activity in raw.get("data", {}).get("activities", []):
        rows = (activity.get("data") or {}).get("positive_quirk_cost_upgrades")
        if rows:
            return tuple(int((r.get("cost_currency") or {}).get("amount") or 0)
                         for r in rows)
    return ()


def quirk_lock_gold(count: int, tier: int = 0,
                    game_dir: str = str(GAME_DIR)) -> int:
    """What `count` locked positive quirks cost at a given Sanitarium tier.

    `tier` defaults to 0, the un-upgraded building and the DEAREST rate, which
    is the conservative direction for a retreat: it makes the party look
    expensive, which argues for pulling out.
    """
    prices = quirk_lock_prices(game_dir)
    if not prices or count <= 0:
        return 0
    return int(count) * prices[min(max(0, tier), len(prices) - 1)]


def locked_quirks(roster_file: str | Path) -> dict:
    """`{hero guid: (quirk id, ...)}` for every quirk flagged locked.

    **Unfiltered by side on purpose** — the caller decides. `is_locked` is a
    genuine DSON bool here (25 False, 1 True, 115 absent over both live
    campaigns), and the one True is a disease. See the module docstring.

    Returns `{}` for a missing or unreadable file, which a caller cannot tell
    from "nothing locked" and does not need to: being wrong costs an
    under-valued hero, which is the safe direction.
    """
    try:
        from data.dson import parse_file
        root = parse_file(Path(roster_file))
    except Exception:                                     # noqa: BLE001
        return {}
    tree = root.to_dict() if hasattr(root, "to_dict") else root
    out: dict = {}
    for guid, entry in ((tree or {}).get("heroes") or {}).items():
        try:
            raw = entry["hero_file_data"]["raw_data"]
        except (KeyError, TypeError):
            continue
        found = []
        for block in getattr(raw, "children", ()):
            if block.name != "quirks":
                continue
            for quirk in block.children:
                flag = next((f.value for f in quirk.children
                             if f.name == "is_locked"), None)
                if flag is True:
                    found.append(quirk.name)
        if found:
            try:
                out[int(guid)] = tuple(found)
            except (TypeError, ValueError):
                continue
    return out


def positive_locked(quirk_ids: Iterable[str], store) -> tuple[str, ...]:
    """Of those locked quirks, the ones somebody PAID to lock.

    The game locks diseases and permanent negatives by itself, so the side test
    is what separates 7500 gold of investment from a hero who is merely ill.
    An unknown quirk id is excluded rather than assumed positive — the same
    direction every other unknown here takes.
    """
    quirks = getattr(store, "quirks", {}) if store is not None else {}
    return tuple(q for q in quirk_ids
                 if getattr(quirks.get(q), "is_positive", False))


@dataclass(frozen=True)
class HeroValue:
    """Gold sunk into one hero, itemised so a report can say where it went."""
    hero_class: str
    guid: int
    unlocks: int = 0          # skills bought beyond the free slots
    unlock_gold: int = 0
    skill_rank_gold: int = 0  # ranks 2+ on any skill, free or bought
    weapon_gold: int = 0
    armour_gold: int = 0
    quirk_gold: int = 0
    locked_quirks: tuple[str, ...] = ()
    priceable: bool = True    # False when the class has no upgrade file

    @property
    def total(self) -> int:
        return (self.unlock_gold + self.skill_rank_gold
                + self.weapon_gold + self.armour_gold + self.quirk_gold)

    def line(self) -> str:
        bits = []
        if self.unlock_gold:
            bits.append(f"{self.unlocks} unlock(s) {self.unlock_gold:,}")
        if self.skill_rank_gold:
            bits.append(f"skill ranks {self.skill_rank_gold:,}")
        if self.weapon_gold:
            bits.append(f"weapon {self.weapon_gold:,}")
        if self.armour_gold:
            bits.append(f"armour {self.armour_gold:,}")
        if self.quirk_gold:
            bits.append(f"{len(self.locked_quirks)} locked quirk(s) "
                        f"{self.quirk_gold:,}")
        note = "" if self.priceable else "  (class not priceable here)"
        return (f"{self.hero_class}#{self.guid}: {self.total:>7,} gold"
                + (f"  = {' + '.join(bits)}" if bits else "  (a fresh recruit)")
                + note)


def hero_value(profile, hero, store=None, game_dir: str = str(GAME_DIR),
               sanitarium_tier: int = 0,
               locked: Optional[dict] = None) -> HeroValue:
    """What this hero cost to build, from the save.

    `locked` is the whole campaign's `locked_quirks` map, passed in so a party
    valuation parses `persist.roster.json` once instead of per hero.
    """
    hero_class = str(getattr(hero, "hero_class", ""))
    guid = int(getattr(hero, "guid", 0) or 0)
    costs = load_hero_costs(game_dir, hero_class)
    if costs is None:
        return HeroValue(hero_class=hero_class, guid=guid, priceable=False)

    have = unlocked(profile, hero, game_dir)
    free = skill_slots(hero_class, game_dir)
    # **The unlock price is the tree's own rank 1**, read rather than typed:
    # it is 1000 in all twenty shipped trees and in all four campaign modes,
    # and reading it is what keeps that a fact instead of a memory.
    prices = [ranks[0] for ranks in costs.skills.values() if ranks]
    unlock_price = prices[0] if prices else 0
    bought = max(0, len(have) - free)

    # Ranks ABOVE the unlock, charged for every skill the hero owns -- the
    # free four are free to OWN, not free to upgrade.
    rank_gold = 0
    for skill, rank in have.items():
        ladder = costs.skills.get(skill, ())
        rank_gold += sum(ladder[1:max(1, int(rank))])

    gear = gear_ranks(profile, hero, game_dir)
    weapon_gold = sum(costs.weapon[:gear.get("weapon", 0)])
    armour_gold = sum(costs.armour[:gear.get("armour", 0)])

    mine = (locked or {}).get(guid, ())
    positives = positive_locked(mine, store)
    return HeroValue(
        hero_class=hero_class, guid=guid,
        unlocks=bought, unlock_gold=bought * unlock_price,
        skill_rank_gold=rank_gold,
        weapon_gold=weapon_gold, armour_gold=armour_gold,
        quirk_gold=quirk_lock_gold(len(positives), sanitarium_tier, game_dir),
        locked_quirks=positives)


@dataclass(frozen=True)
class InventoryValue:
    """What the party is CARRYING, priced only where the game prices it."""
    gold: int = 0
    provisions_gold: int = 0
    trinkets: int = 0         # counted, never priced -- see the module note
    heirlooms: int = 0        # counted; heirlooms have no gold rate at all

    @property
    def total(self) -> int:
        return self.gold + self.provisions_gold

    def line(self) -> str:
        extra = []
        if self.trinkets:
            extra.append(f"{self.trinkets} trinket(s), unpriced")
        if self.heirlooms:
            extra.append(f"{self.heirlooms} heirloom(s), unpriced")
        return (f"{self.total:,} gold carried "
                f"({self.gold:,} coin + {self.provisions_gold:,} supplies)"
                + (f"  [{', '.join(extra)}]" if extra else ""))


def inventory_value(items: Iterable, game_dir: str = str(GAME_DIR)) -> InventoryValue:
    """Price a raid pack. `items` is anything with `.type`/`.id`/`.amount`.

    Provisions are priced at what the provisioner charged for them, which is
    the only gold figure the game states for them. Loot gold counts itself.
    """
    from data import provisions as prov

    unit = prov.prices(game_dir)
    gold = supplies = trinkets = heirlooms = 0
    for item in items or ():
        kind = str(getattr(item, "type", "") or "")
        item_id = str(getattr(item, "id", "") or "")
        amount = int(getattr(item, "amount", 0) or 0)
        if kind == "gold":
            gold += amount
        elif kind == "trinket":
            trinkets += max(1, amount)
        elif kind == "heirloom":
            heirlooms += max(1, amount)
        elif kind in ("supply", "provision"):
            supplies += unit.get(item_id or "food", 0) * max(1, amount)
    return InventoryValue(gold=gold, provisions_gold=supplies,
                          trinkets=trinkets, heirlooms=heirlooms)


def party_value(profile, heroes, store=None, game_dir: str = str(GAME_DIR),
                sanitarium_tier: int = 0) -> list:
    """`HeroValue` per hero, parsing the roster's lock flags once."""
    roster = Path(profile)
    if roster.is_dir():
        roster = roster / "persist.roster.json"
    locked = locked_quirks(roster)
    return [hero_value(profile, h, store, game_dir, sanitarium_tier, locked)
            for h in heroes]


def main() -> int:
    import argparse
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--profile", default=None)
    ap.add_argument("--game-dir", default=str(GAME_DIR))
    ap.add_argument("--sanitarium-tier", type=int, default=0)
    args = ap.parse_args()

    from bridge.live_state import find_profile
    from bridge.town_state import read_town_state
    from data.store_cache import load_store_cached

    profile = find_profile(args.profile)
    store = load_store_cached(str(Path(__file__).resolve().parent.parent),
                              args.game_dir)
    town = read_town_state(profile)
    print(f"profile: {profile}")
    print(f"free skill slots: "
          + ", ".join(f"{c}={skill_slots(c, args.game_dir)}"
                      for c in sorted({h.hero_class for h in town.heroes})))
    print(f"locking one positive quirk: {quirk_lock_prices(args.game_dir)} "
          f"by sanitarium tier")
    print()
    values = party_value(profile, town.heroes, store, args.game_dir,
                         args.sanitarium_tier)
    for value in sorted(values, key=lambda v: -v.total):
        print("  " + value.line())
    print(f"\n  roster total: {sum(v.total for v in values):,} gold "
          f"over {len(values)} heroes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
