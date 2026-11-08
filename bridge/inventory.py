"""The raid pack: what is in it, and what may be thrown away to make room.

    python bridge/inventory.py                     # read the live save
    python bridge/inventory.py --raid <snap>       # read a capture
    python bridge/inventory.py --want 2            # ...and plan two free slots

## Why this module exists

A fight ends, the loot window opens, `act.py` presses SPACE, and DD answers
with a modal: *"You don't have enough room in inventory to take all. Make room
or choose only what you can carry."* Until now the bridge paused there and
handed the pack back to the human. That is the last routine interruption in the
dungeon loop.

## What the game tells us, and what it does not

Four files under `inventory/` define every carryable thing --
`base.supply.*`, `mode.supply.*`, `base.currency.*`, `base.gem.*`,
`base.quest_item.*` -- each with `.base_stack_limit`, `.purchase_gold_value`
and `.sell_gold_value`. Those are real game data and this module reads them
rather than restating them.

`sell_gold_value` is **not** a ranking of worth, and using it as one would be a
serious mistake. Gold sells for 0 because it *is* gold; every heirloom sells for
0 because heirlooms are the town-upgrade currency rather than merchandise. A
rule that discarded by sale price would throw out a crest to keep a shovel.

Nor is "priced at zero in both directions" enough on its own. **That test was
written twice and was wrong both times**, and the two failures are the reason
the rule now looks the way it does:

1. Run against the first capture, it proposed discarding an **heirloom portrait
   and 625 gold**. Both are 0/0, because heirlooms are the town-upgrade currency
   and gold is gold. Fixed by gating on `type == "supply"`.
2. That left `firewood` and `dog_treats` as the only 0/0 supplies, and dog
   treats were called chaff -- "a Houndmaster-only self-buff, bought for
   nothing, sold for nothing". Reported wrong by Fran mid-raid, and the game
   data is unambiguous. `effects/base.effects.darkest`:

       effect: .name "dog_treats" .target "performer" .item 1 .duration 3
               .attack_rating_add 15% .damage_low_multiply 50%
               .damage_high_multiply 50%

   **+15% accuracy and +50% damage for three rounds** is one of the strongest
   consumables in the game, and `campaign/provision/provision.json` hands them
   out under `raid_starting_hero_class_item_lists` precisely *because* the party
   brought a Houndmaster -- the same mechanism that gives a Plague Doctor an
   antivenom and a Grave Robber a shovel.

The lesson, twice over: **a gold price describes what a shop will do, and says
nothing about what an item is worth in a dungeon.** So the rule no longer asks
only about price.

**The honest conclusion is that there is almost never a free discard.** Of the
ten supplies the game defines, six carry a `.item 1` combat effect (bandage,
antivenom, medicinal herbs, holy water, laudanum, dog treats) and the other
four have obvious non-combat uses -- shovel and skeleton key open curios,
firewood pays for a campfire, torch is the light. Nothing in a pack is chaff.

The one case that survives is an item bound to a hero class that **is not in
the party**: dog treats with no Houndmaster cannot be used by anyone, so they
are dead weight and nothing else. That is read from `provision.json` rather
than hardcoded, so it covers any class item the game adds.

Everything else -- a gem, an heirloom, gold, a spare stack of holy water -- is a
judgement call with a real cost, so the plan reports it as a *shortfall* and
`act.py` pauses or abandons the loot. Discarding is irreversible in a live
raid, and "stop and say what the options were" is the right failure mode for a
decision this module was never given the criteria to make. `PLAYBOOK.md` does
not cover the pack.

## Consolidation is free, and is not done here

`use_stack_limits true` means a pack can hold `torch 6`, `torch 8` and
`torch 2` in three slots when 16 torches fit in two. Merging them frees a slot
at zero cost -- strictly better than discarding anything. It is reported in
`DiscardPlan.notes` and NOT executed, because merging is a click-drag between
two slots and the actuator only does discrete clicks. Worth building next; the
measured case (`captures/full_inventory.png`) frees exactly one slot.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

if __package__ in (None, ""):                    # runnable as a plain script
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from data.darkest_parser import DarkestParser     # noqa: E402

from data.paths import GAME_DIR

# `.type "raid" .max_slots 16` in base.inventory.system_configs.darkest.
RAID_SLOTS = 16

ITEM_FILES = (
    "inventory/base.supply.inventory.items.darkest",
    "inventory/mode.supply.inventory.items.darkest",
    "inventory/base.currency.inventory.items.darkest",
    "inventory/base.gem.inventory.items.darkest",
    "inventory/base.quest_item.inventory.items.darkest",
)

# The ONLY type a discard may come from. Everything else in a pack is either
# the reason the party is down here (`gold`, `gem`, `heirloom`, `journal_page`),
# something the game refuses to discard anyway ("You cannot discard quest
# items."), or something irreplaceable (`trinket`, `estate` = a Crimson Court
# dose). A positive gate rather than a deny-list: a type this module has never
# seen defaults to "leave it alone".
DISCARDABLE_TYPES = frozenset({"supply"})
# Firewood is priced at zero both ways and carries no combat effect, so both
# of the other tests clear it -- and it is the one thing that pays for a
# campfire (0 short / 1 medium / 2 long, per `engine/camping.py`). Losing it
# costs a rest the party may be counting on. Shovel, skeleton key and torch are
# excluded by the same reasoning: no effect entry, but a use the effects file
# does not know about.
NEVER_DISCARD_IDS = frozenset({"firewood", "shovel", "skeleton_key", "torch"})

# Items a hero class starts a raid with, from `provision.json`'s
# `raid_starting_hero_class_item_lists`. The party bringing that class is the
# whole reason the item is in the pack, so the item is worth carrying exactly
# while the class is. Read from the file rather than hardcoded.
PROVISION_JSON = "campaign/provision/provision.json"
# The file that says an item DOES something in combat: an entry named after the
# item with `.item 1`. Six of the ten supplies have one.
EFFECTS_FILE = "effects/base.effects.darkest"


@dataclass(frozen=True)
class ItemDef:
    type: str
    id: str
    stack_limit: int
    purchase: int
    sell: int
    # True when `effects/base.effects.darkest` has an entry named after this
    # item with `.item 1` -- the game's own statement that using it does
    # something. Six supplies have one; dog treats' is +15% ACC / +50% damage.
    has_effect: bool = False
    # The hero class this item ships with, per `provision.json`, or "".
    class_item: str = ""

    @property
    def priceless(self) -> bool:
        """Zero to buy and zero to sell.

        Necessary for a discard, nowhere near sufficient -- gold and every
        heirloom are 0/0 too, and so are dog treats. Kept as a named property
        because "the shop pays nothing" is a real fact about an item; it is
        just not the fact a discard decision turns on. See `is_dead_weight`.
        """
        return self.purchase == 0 and self.sell == 0

    def is_dead_weight(self, party_classes: frozenset[str]) -> bool:
        """Nothing in the party can get any value out of this, at any price.

        The only surviving discard case. An item bound to a hero class is
        useless when that class is absent -- dog treats with no Houndmaster
        cannot be eaten by anyone. Everything else either sells, or does
        something, or opens something.
        """
        if self.type not in DISCARDABLE_TYPES or self.id in NEVER_DISCARD_IDS:
            return False
        if not self.priceless:
            return False
        if not self.class_item:
            return False              # not class-bound: someone can still use it
        return self.class_item not in party_classes


@dataclass(frozen=True)
class Slot:
    """One occupied cell of the raid pack, in the save's own numbering."""
    slot: int
    type: str
    id: str
    amount: int

    @property
    def key(self) -> tuple[str, str]:
        return (self.type, self.id)

    @property
    def label(self) -> str:
        return f"{self.id or self.type} x{self.amount}"


@dataclass(frozen=True)
class Discard:
    """One slot to shift-click empty.

    `amount` is carried because a shift-click may take a single unit rather
    than the whole stack -- the game documents `[SHIFT+CLICK] to reduce torch`
    for the torch METER, so a per-unit reading of the same gesture is entirely
    plausible for a pack stack, and nothing shipped says which it is. It
    bounds how many times `act.py` may click before giving up.
    """
    slot: int
    label: str
    why: str
    type: str = ""
    id: str = ""
    amount: int = 1


@dataclass
class DiscardPlan:
    """Which slots to shift-click, and what could not be freed for free."""
    discards: list[Discard] = field(default_factory=list)
    want: int = 1
    free_before: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def freed(self) -> int:
        return self.free_before + len(self.discards)

    @property
    def shortfall(self) -> int:
        return max(0, self.want - self.freed)

    @property
    def enough(self) -> bool:
        return self.shortfall == 0

    def __str__(self) -> str:
        if not self.discards:
            head = f"nothing free to discard ({self.free_before} slot(s) already open)"
        else:
            head = "discard " + ", ".join(
                f"slot {d.slot} ({d.label})" for d in self.discards)
        if self.shortfall:
            head += f" - still {self.shortfall} slot(s) short"
        return head + "".join(f"\n    note: {n}" for n in self.notes)


# ── game data ──

_ITEMS: Optional[dict[tuple[str, str], ItemDef]] = None


def item_defs(game_dir: Path = GAME_DIR) -> dict[tuple[str, str], ItemDef]:
    """Every carryable item, keyed (type, id). Parsed once, then memoised.

    Later files do NOT win: `mode.supply` redeclares the provision with the
    same numbers as `base.supply`, and the project's rule everywhere else is
    that base wins on collision. `setdefault` keeps that consistent.
    """
    global _ITEMS
    if _ITEMS is not None:
        return _ITEMS
    effects = _items_with_effects(game_dir)
    class_items = _class_items(game_dir)
    out: dict[tuple[str, str], ItemDef] = {}
    for rel in ITEM_FILES:
        path = Path(game_dir) / rel
        if not path.exists():
            continue
        for rec in DarkestParser.parse_file(path):
            if rec.get("_type") != "inventory_item":
                continue
            t = str(rec.get("type", ""))
            # `.id ""` parses to the empty string, which is the real id for
            # gold and for food -- do not treat it as missing.
            i = str(rec.get("id", "") or "")
            out.setdefault((t, i), ItemDef(
                type=t, id=i,
                stack_limit=int(rec.get("base_stack_limit", 1) or 1),
                purchase=int(rec.get("purchase_gold_value", 0) or 0),
                sell=int(rec.get("sell_gold_value", 0) or 0),
                has_effect=i in effects,
                class_item=class_items.get(i, "")))
    _ITEMS = out
    return out


def _items_with_effects(game_dir: Path) -> set[str]:
    """Names in `base.effects.darkest` that are item effects (`.item 1`).

    An item with one of these DOES something when used, whatever a shop would
    pay for it. This is what stops the discard rule from ever again calling
    a +50%-damage consumable chaff.
    """
    path = Path(game_dir) / EFFECTS_FILE
    if not path.exists():
        return set()
    out = set()
    for rec in DarkestParser.parse_file(path):
        name = rec.get("name")
        if rec.get("_type") == "effect" and name and rec.get("item"):
            out.add(str(name))
    return out


def _class_items(game_dir: Path) -> dict[str, str]:
    """{item id: hero class} from `raid_starting_hero_class_item_lists`.

    The party bringing that class is why the item is in the pack, so the item
    is worth carrying exactly as long as the class is. Houndmaster -> dog
    treats, Plague Doctor -> antivenom, Grave Robber -> shovel.
    """
    import json
    path = Path(game_dir) / PROVISION_JSON
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out: dict[str, str] = {}
    for entry in raw.get("raid_starting_hero_class_item_lists", []) or []:
        cls = str(entry.get("hero_class", "") or "")
        for item in entry.get("item_lists", []) or []:
            item_id = str(item.get("id", "") or "")
            if cls and item_id:
                out.setdefault(item_id, cls)
    return out


# ── reading the pack ──

def read_pack(raid_path: Path) -> list[Slot]:
    """The occupied slots of `persist.raid.json`, in save order.

    Copies before parsing, for the same reason `live_state._stage` does: never
    hold a read handle on a file the game may be mid-write on.
    """
    from data.dson import parse_file
    tmp_dir = Path(tempfile.mkdtemp())
    try:
        tmp = tmp_dir / "raid.json"
        shutil.copy2(raid_path, tmp)
        raid = parse_file(tmp)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
    return pack_from_raid(raid)


def pack_from_raid(raid) -> list[Slot]:
    """Split out so a caller that already parsed the save need not parse twice."""
    party = raid.get("party") if raid is not None else None
    inv = party.get("inventory") if party is not None else None
    items = inv.get("items") if inv is not None else None
    out: list[Slot] = []
    for child in (items.children or []) if items is not None else []:
        kv = {k.name: k.value for k in (child.children or [])}
        try:
            n = int(child.name)
        except (TypeError, ValueError):
            continue
        out.append(Slot(slot=n, type=str(kv.get("type", "") or ""),
                        id=str(kv.get("id", "") or ""),
                        amount=int(kv.get("amount", 0) or 0)))
    return sorted(out, key=lambda s: s.slot)


# ── deciding ──

def spare_stack_slots(pack: list[Slot],
                      defs: Optional[dict] = None) -> list[str]:
    """Slots recoverable by MERGING split stacks, described not planned.

    `use_stack_limits true`, so k stacks of one item totalling n units need
    only ceil(n / limit) slots. The difference is free space nobody has to
    decide about -- strictly better than any discard, and the reason this is
    reported even though it is not executed.
    """
    defs = defs if defs is not None else item_defs()
    by_key: dict[tuple[str, str], list[Slot]] = {}
    for s in pack:
        by_key.setdefault(s.key, []).append(s)
    notes = []
    for key, slots in sorted(by_key.items()):
        if len(slots) < 2:
            continue
        limit = defs[key].stack_limit if key in defs else 0
        if limit <= 0:
            continue
        total = sum(s.amount for s in slots)
        needed = -(-total // limit)              # ceil
        if needed < len(slots):
            name = key[1] or key[0]
            notes.append(
                f"{len(slots)} stacks of {name} hold {total} of a {limit} limit "
                f"- merging them frees {len(slots) - needed} slot(s), for free")
    return notes


@dataclass(frozen=True)
class Merge:
    """Pour slot `src` into slot `dst`. Both hold the same item."""
    src: int
    dst: int
    item: str
    moved: int
    total: int

    def label(self) -> str:
        return (f"{self.item}: slot {self.src} ({self.moved}) into slot "
                f"{self.dst} -> {self.total}")


def plan_stack_merges(pack: list[Slot],
                      defs: Optional[dict] = None) -> list[Merge]:
    """Drags that each free EXACTLY ONE slot, and cost nothing.

    `use_stack_limits true`, so k stacks of one item totalling n units need
    only `ceil(n / limit)` slots. Combining them throws nothing away, needs no
    judgement about what is chaff, and is strictly better than any discard --
    which is why it is tried before the pack layer ever considers one.

    **Only a drag that EMPTIES its source is emitted.** A partial pour tops up
    the destination and leaves the source holding the remainder, so it frees
    no slot and cannot be acknowledged: the pack comes back the same length
    and the bridge cannot tell it from an input that never landed. Requiring
    `src + dst <= limit` makes every drag verifiable by one number.

    **Smallest into largest**, and greedily, so the fewest drags are sent:
    each one is a real mouse gesture over a live inventory and the cheapest
    way to drop an item on the floor is to send more of them than the answer
    needs.
    """
    defs = defs if defs is not None else item_defs()
    by_key: dict[tuple[str, str], list[Slot]] = {}
    for s in pack:
        by_key.setdefault(s.key, []).append(s)

    out: list[Merge] = []
    for key, slots in sorted(by_key.items()):
        if len(slots) < 2:
            continue
        limit = defs[key].stack_limit if key in defs else 0
        if limit <= 0:
            continue
        name = key[1] or key[0]
        # A mutable running count per slot: a slot poured into can be poured
        # into again, and a slot emptied is out of the pool.
        held = {s.slot: s.amount for s in slots}
        while True:
            live = sorted(((n, v) for n, v in held.items() if v > 0),
                          key=lambda pair: pair[1])
            if len(live) < 2:
                break
            src_slot, src_amt = live[0]
            # The fullest destination this source fits into completely.
            dst = next(((n, v) for n, v in reversed(live[1:])
                        if v + src_amt <= limit), None)
            if dst is None:
                break
            dst_slot, dst_amt = dst
            held[src_slot] = 0
            held[dst_slot] = dst_amt + src_amt
            out.append(Merge(src=src_slot, dst=dst_slot, item=name,
                             moved=src_amt, total=dst_amt + src_amt))
    return out


def plan_discards(pack: list[Slot], want: int = 1,
                  defs: Optional[dict] = None,
                  slots_total: int = RAID_SLOTS,
                  party_classes: Optional[frozenset[str]] = None) -> DiscardPlan:
    """Which slots may be shift-clicked away to open `want` cells.

    Almost always none: see the module docstring for why nothing in a
    Darkest Dungeon pack is chaff. The one surviving case needs
    `party_classes` -- a class item whose class is absent. **Passing None means
    "the party is unknown", and an unknown party is treated as containing
    every class**, so the plan comes back empty rather than discarding a
    Houndmaster's dog treats because nobody said there was a Houndmaster.
    """
    defs = defs if defs is not None else item_defs()
    plan = DiscardPlan(want=want, free_before=max(0, slots_total - len(pack)))
    plan.notes.extend(spare_stack_slots(pack, defs))
    if plan.enough:
        return plan
    if party_classes is None:
        plan.notes.append("the party's classes are unknown, so no class item "
                          "can be called dead weight - nothing proposed")
        return plan

    for s in sorted(pack, key=lambda s: (s.amount, s.slot)):
        if plan.enough:
            break
        d = defs.get(s.key)
        if d is None or not d.is_dead_weight(party_classes):
            continue
        plan.discards.append(Discard(
            s.slot, s.label,
            f"a {d.class_item} item and there is no {d.class_item} in the "
            f"party, so nobody can use it",
            type=s.type, id=s.id, amount=s.amount))
    return plan


#: Provisions the fight layer spends on a trigger (bleed, blight, horror,
#: disease): one of each stays in the pack whatever the reserve says.
TRIGGER_ITEMS = frozenset({"bandage", "antivenom", "holy_water", "laudanum",
                           "medicinal_herbs"})
#: A stack worth more than this (at shop price) is not traded for unseen
#: loot. A declared convention: under a single curio's usual gold draw.
SURPLUS_VALUE_CAP = 300


def plan_surplus_discard(pack: list[Slot], reserve: dict,
                         defs: Optional[dict] = None) -> Optional[Discard]:
    """A provision stack the rest of the raid does not need, or None.

    The loot in a window is not in the save, so it cannot be priced; what can
    be priced is what the pack will never use. `reserve` is the raid layer's
    own ledger (`provision_use.room_reserve`: every item earmarked for a curio
    still ahead, and food for the hunger checks left). A stack is offered only
    when ALL of it is beyond that reserve (plus one of each trigger item), it
    is never a torch, shovel, key or firewood, and it is cheap at shop price.
    The cheapest such stack goes. Measured 2026-09-27: 33 of 37 curios given
    up on in one night came straight after the game's "no room" modal.
    """
    defs = defs if defs is not None else item_defs()
    held: dict = {}
    for s in pack:
        name = s.id or ("food" if s.type == "provision" else "")
        if name:
            held[name] = held.get(name, 0) + int(s.amount)
    best = None
    for s in pack:
        # SUPPLY only -- the same positive gate `ItemDef.is_dead_weight`
        # keeps. Food is reserved by the hunger budget and never offered.
        if s.type not in DISCARDABLE_TYPES:
            continue
        name = s.id or ("food" if s.type == "provision" else "")
        if not name or name in NEVER_DISCARD_IDS:
            continue
        keep = int((reserve or {}).get(name, 0)) + (1 if name in TRIGGER_ITEMS else 0)
        surplus = held.get(name, 0) - keep
        if s.amount > surplus:
            continue
        d = defs.get(s.key)
        value = int(s.amount) * int(getattr(d, "purchase", 0) or 0)
        if value > SURPLUS_VALUE_CAP:
            continue
        if best is None or value < best[0]:
            best = (value, s)
    if best is None:
        return None
    value, s = best
    return Discard(s.slot, s.label,
                   f"beyond what the rest of the raid reserves ({value}g at "
                   f"shop price), to make room for the loot",
                   type=s.type, id=s.id, amount=s.amount)


#: `--full-pack swap`: the most a slot may be worth (gold, gems at their sale
#: value, heirlooms at `CREST_GOLD` per crest-equivalent) for it to be thrown
#: away to make room for loot nobody can see yet. Under a single curio's usual
#: gold draw, so a swap trades a small stack for an ordinary pickup and never a
#: real one for the unknown. A declared convention.
SWAP_VALUE_CAP = 250.0


def slot_value(s: Slot, defs: dict) -> Optional[float]:
    """One comparable number for a pack slot, or None for what is never
    traded away (supplies -- see `plan_surplus_discard` -- trinkets, quest
    items, anything unpriced)."""
    if s.type == "gold":
        return float(s.amount)
    if s.type == "gem":
        d = defs.get(s.key)
        return float(s.amount) * float(getattr(d, "sell", 0) or 0) if d else None
    if s.type == "heirloom":
        from data.heirlooms import crest_values, load_rates    # noqa: PLC0415
        from data.loot_loader import CREST_GOLD                # noqa: PLC0415
        try:
            per = crest_values(load_rates(str(GAME_DIR))).get(s.id)
        except Exception:                                      # noqa: BLE001
            per = None
        return None if per is None else float(s.amount) * per * CREST_GOLD
    return None


def plan_value_discard(pack: list[Slot], defs: Optional[dict] = None,
                       cap: float = SWAP_VALUE_CAP) -> Optional[Discard]:
    """The cheapest slot of value, if it is worth at most `cap`.

    What a player does with a full pack: drop the least valuable stack and
    take the new loot. The bridge cannot see the loot window (it is not in
    the save until picked up), so it only ever trades away what is cheaper
    than an ordinary pickup -- a part-stack of gold, one crest, a citrine.
    """
    defs = defs if defs is not None else item_defs()
    best = None
    for s in pack:
        v = slot_value(s, defs)
        if v is None or v > cap:
            continue
        if best is None or v < best[0]:
            best = (v, s)
    if best is None:
        return None
    v, s = best
    return Discard(slot=s.slot, label=s.label,
                   why=f"the cheapest stack in a full pack ({v:.0f}g-equivalent, "
                       f"under the {cap:.0f}g swap cap) - traded for the loot",
                   type=s.type, id=s.id, amount=int(s.amount))


def describe(pack: list[Slot], defs: Optional[dict] = None,
             slots_total: int = RAID_SLOTS) -> str:
    defs = defs if defs is not None else item_defs()
    lines = [f"pack: {len(pack)}/{slots_total} slots"]
    for s in pack:
        d = defs.get(s.key)
        cap = f"/{d.stack_limit}" if d else ""
        val = f"  sell {d.sell}" if d and d.sell else ""
        lines.append(f"  {s.slot:2d}  {s.type:<12s} {s.id or '(none)':<18s}"
                     f" {s.amount}{cap}{val}")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Read and plan the raid pack.")
    ap.add_argument("--raid", help="persist.raid.json (default: the live save)")
    ap.add_argument("--want", type=int, default=1, help="slots to free")
    args = ap.parse_args(argv)

    raid = Path(args.raid) if args.raid else None
    if raid is not None and raid.is_dir():
        found = sorted(raid.glob("*raid*.json"))
        if not found:
            print(f"no raid save under {raid}")
            return 1
        raid = found[-1]
    if raid is None:
        from bridge.live_state import find_profile
        raid = find_profile() / "persist.raid.json"
    if not raid.exists():
        print(f"no raid save at {raid}")
        return 1

    pack = read_pack(raid)
    print(describe(pack))
    classes = None
    try:
        from bridge.live_state import read_live_state
        roster = Path(str(raid).replace("raid.json", "roster.json"))
        if roster.exists():
            state = read_live_state(raid_file=raid, roster_file=roster)
            found = {h.hero_class for h in (state.heroes or [])
                     if getattr(h, "hero_class", "")}
            classes = frozenset(found) if found else None
    except Exception as exc:                      # noqa: BLE001
        print(f"(could not read the party: {exc})")
    print(f"party: {', '.join(sorted(classes)) if classes else 'unknown'}")
    print()
    print(plan_discards(pack, want=args.want, party_classes=classes))
    return 0


if __name__ == "__main__":
    sys.exit(main())
