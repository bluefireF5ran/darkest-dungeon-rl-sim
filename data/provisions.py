"""What to buy at the provisioner, and what it costs.

`env/expedition_sequence_env.py` carries `BASE_PROVISION_AMOUNTS` -- a declared
simulator convention, because `provision.json` exposes purchase *limits* and
not recommended loadouts, and no shipped file says what a party should carry.
That is still true of the game. It is not true of this project: PLAYBOOK 1.5 is
Fran's own table, **per zone and per length**, transcribed from the provisions
guide he plays by, and the variation across zones is large -- Weald wants 4-5
antidotes where the Ruins wants none, Warrens wants twice the torches.

So this module is two halves that must not be confused:

* **The quantities are a DECLARED CONVENTION with a named source** (PLAYBOOK
  1.5). They are one expert's table, not game data, and `GUIDE` says so.
* **Everything else is game data and is parsed**: unit prices from
  `inventory/base.supply.inventory.items.darkest`, what the shop stocks per
  quest length and *in which order it draws them* from
  `campaign/provision/provision.json`, and the food count below which DD
  raises its own "you haven't purchased much food" dialog.

## The shop's item ORDER is shipped, not measured

`default_store_inventory_item_lists[length]` lists the store's stock, and the
order it lists them in **is the order the panel draws them**: food, shovel,
antivenom, bandage, medicinal_herbs, skeleton_key, holy_water, laudanum,
torch. Nine items into a 7-column grid puts laudanum at index 7 and **torch at
index 8** -- which is exactly where `bridge/layout.py::PROVISION_TORCH` was
independently hand-measured, and food at index 0 is where `PROVISION_FOOD` is.
Two constants clicked by a human, reproduced by a file nobody derived them
from, is the check that this reading is right.

## Overbuying is nearly a total loss, so the reserve is a RESERVE

darkestdungeon.wiki.gg's interface note says leftover provisions sell back "at
purchase price". The shipped file says otherwise and the shipped file wins:
`sell_gold_value` is about a tenth of `purchase_gold_value` on every row --
food 75 buys and 5 sells, shovel 250 and 25, the 150-gold cures 15. So a
provision bought and not used returns **10%**, and there is no cheap way to
correct an over-estimate.

That is the argument for taking PLAYBOOK 1.5's *recommended* end rather than the
shop's stock, and for `granted` existing at all: the failure mode is not "we
ran out", which costs some HP, but "we bought four spare shovels", which costs
900 gold and returns 90. Nothing in this module prices a refund -- it is a town
transaction and the planner never sells -- but the ratio is why the numbers
here are a table and not a maximum.

## The food floor is the game's own number

`confirm_datas[length].minimum_food` is 0 / 8 / 8 / 12 / 12 / 12. The bridge
bought a flat 8 for exactly that reason and it is only right for the two short
lengths; a medium quest wants 12 or the dialog appears. `minimum_food` is now
read rather than hardcoded, and `basket` never returns less than it.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from data.darkest_parser import DarkestParser

from data.paths import GAME_DIR

# `bridge/rollout.py`'s mapping, restated rather than imported: `bridge/`
# imports `data/`, never the other way round, and one dict is cheaper than the
# cycle. `tests/data/test_provisions.py` asserts the two agree.
LENGTH_TO_BAND = {1: "short", 2: "medium", 3: "long", 4: "long"}

# The zone ids the save uses. "crypts" IS the Ruins -- PLAYBOOK 1.5 writes the
# display name and every save, mash file and encounter table writes `crypts`.
ZONES = ("crypts", "warrens", "weald", "cove")

# PLAYBOOK 1.5, `docs/PLAYBOOK.md`. `(minimum, recommended)` per item, per
# zone, per band -- the two ends of each range in his table.
#
# **This is the one part of this module that is not game data.** It is one
# expert's guide, and it is used because the alternative is the invented
# convention `BASE_PROVISION_AMOUNTS` already documents itself as. Anything
# reading it should say where the number came from.
#
# `firewood` is absent deliberately: it is not sold, the game grants it by
# length (0 short / 1 medium / 2 long) and `engine/camping.py` already knows.
GUIDE: dict[str, dict[str, dict[str, tuple[int, int]]]] = {
    "crypts": {
        "short":  {"food": (8, 12), "shovel": (2, 2), "antivenom": (0, 1),
                   "bandage": (0, 1), "medicinal_herbs": (0, 1),
                   "skeleton_key": (2, 3), "holy_water": (2, 3), "torch": (8, 8)},
        "medium": {"food": (16, 20), "shovel": (3, 3), "antivenom": (0, 2),
                   "bandage": (0, 2), "medicinal_herbs": (0, 2),
                   "skeleton_key": (2, 4), "holy_water": (3, 4), "torch": (10, 13)},
        "long":   {"food": (20, 24), "shovel": (3, 4), "antivenom": (0, 3),
                   "bandage": (0, 3), "medicinal_herbs": (0, 3),
                   "skeleton_key": (3, 5), "holy_water": (3, 5), "torch": (12, 16)},
    },
    "warrens": {
        "short":  {"food": (8, 12), "shovel": (1, 2), "antivenom": (0, 1),
                   "bandage": (1, 3), "medicinal_herbs": (2, 3),
                   "skeleton_key": (1, 2), "holy_water": (2, 3), "torch": (8, 10)},
        "medium": {"food": (12, 18), "shovel": (2, 3), "antivenom": (0, 2),
                   "bandage": (2, 3), "medicinal_herbs": (3, 4),
                   "skeleton_key": (2, 3), "holy_water": (3, 3), "torch": (10, 16)},
        "long":   {"food": (16, 20), "shovel": (2, 4), "antivenom": (0, 3),
                   "bandage": (3, 4), "medicinal_herbs": (3, 5),
                   "skeleton_key": (2, 4), "holy_water": (3, 4), "torch": (16, 20)},
    },
    "weald": {
        "short":  {"food": (8, 12), "shovel": (3, 4), "antivenom": (2, 3),
                   "bandage": (2, 2), "medicinal_herbs": (1, 1),
                   "skeleton_key": (1, 2), "holy_water": (1, 1), "torch": (8, 8)},
        "medium": {"food": (16, 20), "shovel": (4, 5), "antivenom": (3, 4),
                   "bandage": (3, 3), "medicinal_herbs": (2, 2),
                   "skeleton_key": (2, 3), "holy_water": (1, 2), "torch": (12, 12)},
        "long":   {"food": (20, 24), "shovel": (4, 6), "antivenom": (4, 5),
                   "bandage": (4, 4), "medicinal_herbs": (2, 2),
                   "skeleton_key": (2, 4), "holy_water": (2, 2), "torch": (16, 16)},
    },
    "cove": {
        "short":  {"food": (8, 12), "shovel": (3, 4), "antivenom": (0, 1),
                   "bandage": (2, 2), "medicinal_herbs": (2, 2),
                   "skeleton_key": (1, 2), "holy_water": (0, 1), "torch": (8, 8)},
        "medium": {"food": (16, 20), "shovel": (4, 5), "antivenom": (0, 2),
                   "bandage": (3, 4), "medicinal_herbs": (3, 4),
                   "skeleton_key": (2, 3), "holy_water": (0, 2), "torch": (12, 12)},
        "long":   {"food": (20, 24), "shovel": (4, 6), "antivenom": (0, 3),
                   "bandage": (4, 6), "medicinal_herbs": (2, 4),
                   "skeleton_key": (2, 4), "holy_water": (0, 2), "torch": (16, 16)},
    },
}

# Fran's answer, 2026-09-01: reserve the RECOMMENDED end before anything else
# is bought. `"minimum"` stays reachable so an arm can be run against it.
RESERVE = "recommended"


def _item_id(rec: dict) -> str:
    """A store row's id, with the food special case.

    The food item ships with an EMPTY id and `type "provision"` where every
    other row is `type "supply"` -- `data/provision_loader.py` documents the
    same thing and this repeats it rather than importing, because that loader
    filters to the five ids the simulator models and the shop sells nine.
    """
    item_id = str(rec.get("id") or "")
    if not item_id and rec.get("type") == "provision":
        return "food"
    return item_id


@lru_cache(maxsize=4)
def prices(game_dir: str = str(GAME_DIR)) -> dict[str, int]:
    """`{item id: gold}` for everything the provisioner sells."""
    path = Path(game_dir) / "inventory" / "base.supply.inventory.items.darkest"
    try:
        records = DarkestParser.parse_file(path)
    except (OSError, ValueError):
        return {}
    out: dict[str, int] = {}
    for rec in records:
        if rec.get("_type") != "inventory_item":
            continue
        item_id = _item_id(rec)
        gold = int(rec.get("purchase_gold_value") or 0)
        # A zero price is firewood and dog treats -- granted, never sold.
        if item_id and gold > 0:
            out[item_id] = gold
    return out


@lru_cache(maxsize=4)
def _provision_json(game_dir: str) -> dict:
    path = Path(game_dir) / "campaign" / "provision" / "provision.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def store_order(length: int, game_dir: str = str(GAME_DIR)) -> tuple[str, ...]:
    """The shop's items, in the order the panel draws them. See the module note.

    Indexed by quest length, because that is how the file is indexed -- the
    order is the same for every length in shipped data and
    `tests/data/test_provisions.py` pins that, so a caller may use any length's.
    """
    lists = _provision_json(game_dir).get("default_store_inventory_item_lists") or []
    if not 0 <= length < len(lists):
        return ()
    return tuple(_item_id(row) for row in lists[length] if _item_id(row))


def store_stock(length: int, game_dir: str = str(GAME_DIR)) -> dict[str, int]:
    """How many of each item the shop offers for a quest of this length."""
    lists = _provision_json(game_dir).get("default_store_inventory_item_lists") or []
    if not 0 <= length < len(lists):
        return {}
    return {_item_id(row): int(row.get("amount") or 0)
            for row in lists[length] if _item_id(row)}


def minimum_food(length: int, game_dir: str = str(GAME_DIR)) -> int:
    """DD's own floor -- below this it raises the underprepared dialog.

    0 / 8 / 8 / 12 / 12 / 12 in shipped data, so the bridge's flat 8 was right
    for a short quest and short by four for a medium one.
    """
    rows = _provision_json(game_dir).get("confirm_datas") or []
    if not 0 <= length < len(rows):
        return 0
    return int((rows[length] or {}).get("minimum_food") or 0)


def granted(hero_classes=(), length: int = 1,
            game_dir: str = str(GAME_DIR)) -> dict[str, int]:
    """What the party is HANDED, free, before a coin is spent.

    Two shipped lists, and they are indexed differently on purpose:

    * `raid_starting_length_inventory_item_lists[length]` is the firewood --
      0 / 1 / 2 for the three lengths this project embarks on, which is the
      same table `data/camping_loader.FIREWOOD_PER_BAND` carries and the wiki
      states. (Length 4 grants 4 and length 5 grants none; neither is embarked
      on here.)
    * `raid_starting_hero_class_item_lists` is PER HERO, not per party -- two
      Houndmasters bring four Dog Treats. Eight classes carry one: houndmaster
      2 dog_treats, plague_doctor 1 antivenom, grave_robber 1 shovel, arbalest
      1 bandage, crusader 1 holy_water, leper 1 medicinal_herbs, jester 1
      medicinal_herbs, antiquarian 1 skeleton_key.

    This exists so the plan does not BUY what the party is given. A Crusader's
    free holy water against a Ruins/short reserve of three is 150 gold, and a
    four-hero party can easily carry two or three such grants.

    `hero_classes` is a sequence of class ids, WITH duplicates -- passing a set
    would silently undercount the party that most benefits.
    """
    data = _provision_json(game_dir)
    out: dict[str, int] = {}

    lists = data.get("raid_starting_length_inventory_item_lists") or []
    if 0 <= length < len(lists):
        for row in lists[length]:
            item_id, n = _item_id(row), int(row.get("amount") or 0)
            if item_id and n:
                out[item_id] = out.get(item_id, 0) + n

    by_class = {row.get("hero_class"): row.get("item_lists") or []
                for row in data.get("raid_starting_hero_class_item_lists") or []}
    for cls in hero_classes:
        for row in by_class.get(cls, ()):
            item_id, n = _item_id(row), int(row.get("amount") or 0)
            if item_id and n:
                out[item_id] = out.get(item_id, 0) + n
    return out


def basket(zone: str, length: int, level: str = RESERVE,
           game_dir: str = str(GAME_DIR), party=()) -> dict[str, int]:
    """What to BUY for one expedition: `{item id: quantity}`.

    Bounded four ways, and each bound is a different kind of fact: the GUIDE is
    Fran's table (what to CARRY), `granted` is what the game hands the party for
    free, `store_stock` is what the shop will sell, and `minimum_food` is the
    dialog DD raises on its own. An unknown zone returns `{}` rather than a
    guess -- the courtyard and the Darkest Dungeon have no row in the table and
    are not embarked on here anyway.

    `party` is the party's class ids, with duplicates. It defaults to empty, so
    every existing caller is byte-identical: no party means no grant, and the
    basket is the guide's own numbers. Passing one turns the guide from "buy
    this much" into "carry this much", which is what it always meant --
    `minimum_food` is applied to the CARRIED total for the same reason, since
    the dialog counts the pack and not the receipt.
    """
    band = LENGTH_TO_BAND.get(length)
    table = (GUIDE.get(zone) or {}).get(band or "")
    if not table:
        return {}
    pick = 1 if level == "recommended" else 0
    stock = store_stock(length, game_dir)
    free = granted(party, length, game_dir)
    out: dict[str, int] = {}
    for item, pair in table.items():
        want = pair[pick]
        if item == "food":
            want = max(want, minimum_food(length, game_dir))
        want -= free.get(item, 0)
        want = min(want, stock.get(item, want))
        if want > 0:
            out[item] = want
    return out


def cost(want: dict[str, int], game_dir: str = str(GAME_DIR)) -> int:
    """Gold for a basket. An item with no price contributes nothing, which can
    only happen for firewood -- granted, never sold."""
    unit = prices(game_dir)
    return sum(unit.get(item, 0) * n for item, n in want.items())


# What to give up first when the wallet will not stretch. Torches and food are
# LAST because they are what a run cannot start without: the dark is a damage
# modifier on every fight and the food floor is a dialog. Shovels go first --
# a blocked corridor costs a little HP and stress, the cheapest thing here to
# pay in.
CUT_ORDER = ("shovel", "medicinal_herbs", "skeleton_key", "holy_water",
             "bandage", "antivenom", "laudanum", "torch", "food")


def afford(want: dict[str, int], gold: int,
           game_dir: str = str(GAME_DIR)) -> dict[str, int]:
    """Trim a basket down to what `gold` buys. See `CUT_ORDER`."""
    out = {k: v for k, v in want.items() if v > 0}
    for item in CUT_ORDER:
        if cost(out, game_dir) <= gold:
            break
        while out.get(item, 0) > 0 and cost(out, game_dir) > gold:
            out[item] -= 1
            if out[item] == 0:
                del out[item]
    unit = prices(game_dir)
    return {k: v for k, v in out.items() if v > 0 and unit.get(k)}


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--zone", default="crypts", choices=list(ZONES))
    ap.add_argument("--length", type=int, default=1)
    ap.add_argument("--gold", type=int, default=None)
    args = ap.parse_args()

    print(f"prices: {prices()}")
    print(f"store order (length {args.length}): {store_order(args.length)}")
    print(f"stock: {store_stock(args.length)}")
    print(f"minimum food: {minimum_food(args.length)}")
    for level in ("minimum", "recommended"):
        want = basket(args.zone, args.length, level)
        print(f"  {level:12s} {cost(want):5d} gold  {want}")
    if args.gold is not None:
        want = afford(basket(args.zone, args.length), args.gold)
        print(f"  within {args.gold:5d} {cost(want):5d} gold  {want}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
