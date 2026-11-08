"""Building upgrades, the heirloom wallet, and the trades that close a shortfall.

Three things the bridge could not do, and each was a different kind of missing:

1. **What a building upgrade costs** -- `upgrades/building/*.upgrades.json`.
   `data/town_costs.py` reads the HERO trees out of `upgrades/heroes/` and
   prices them in gold, because that is what a retreat decision needs. A
   building is priced in **heirlooms**, and nothing read those at all.
2. **What has already been bought** -- `persist.upgrades.json`, ~228
   `purchases.<n>` entries of `tree_id` / `requirement_code` / `is_purchased`,
   unread. Without it the bridge cannot tell an available upgrade from one it
   already owns, which makes "buy the top path" unanswerable rather than
   merely unactuated.
3. **How to pay for one you cannot afford** -- the exchange.

## Fran's stage coach, which is the worked example and the test

*"upgrading a few levels on the top path (to do the second level you'll need to
trade Crests for the needed Scrolls until you can upgrade it)"*.

There is no *scroll* currency. The four heirlooms are bust, portrait, deed and
crest, and the **deed** icon is a scroll -- so "Scrolls" is deeds, and the
shipped rate `crest 3 -> deed 1` is the trade being described. The arithmetic
is exact, which is the check that this reading is right rather than plausible:

* a new campaign starts with `bust 10, portrait 10, deed 10, crest 20`
  (`scripts/starting_save/persist.estate.json`);
* `stage_coach.numrecruits` costs `deed 3 + crest 4` for `a`, leaving 7 and 16;
* `b` costs `deed 8 + crest 10` -- **one deed short**;
* one 3:1 crest->deed trade closes it, and nothing else needs to happen.

## Why the trade is SOLVED and not scripted

Twelve rates over four currencies is a small graph, and the cheap answer for
the coach is one hop. But the rates are not symmetric (`crest 3 -> deed 1`
against `deed 3 -> portrait 1`), some pairs are only reachable through a third
currency, and which shortfall appears depends on what the run has spent. A
scripted `if deeds_short: trade crests` is right for exactly one upgrade on
exactly one turn, and silently wrong everywhere else.

**Cost is measured in CREST-EQUIVALENT, and that is derived rather than
chosen.** The first version of this counted raw units, and it picked
`2 portrait -> 3 deed` over Fran's `3 crest -> 1 deed` because 2 is less than
3. That is wrong, and the game says so: the three `X -> crest` rows price every
heirloom in the finest denomination --

    2 bust = 3 crest      1 portrait = 3 crest      2 deed = 3 crest

-- so a portrait is worth **three** crests and the "cheaper" plan spent 6.0
against Fran's 3.0. Counting units treats a portrait as interchangeable with a
crest, which the exchange table flatly denies.

**That the corrected objective reproduces the expert's own instruction is the
check that it is right**, in the same way `viable_ranks` reproducing common
knowledge is the check on that derivation. Ties break toward fewer hops: fewer
clicks, and fewer chances for a modal to swallow one.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Optional

# The four heirlooms, in the order `persist.estate.json` and the exchange
# widget list them. Gold is deliberately NOT one: it is a currency in the same
# `currency_cost` array, and a building upgrade's gold amount is always 0, but
# it is earned and spent by completely different rules and must not end up in a
# trade solver that would happily sell it.
HEIRLOOMS = ("bust", "portrait", "deed", "crest")


@dataclass(frozen=True)
class UpgradeNode:
    """One purchasable rank of one tree, with its heirloom price.

    `code` is the game's own (`a`, `b`, `c`), and `prerequisites` are
    `(tree_id, code)` pairs -- a node can require a rank of ANOTHER building's
    tree, which is why they are not just "the previous code".
    `stage_coach.upgraded_recruits` requires guild and blacksmith ranks, and a
    reader that assumed a linear chain would offer it on week one.
    """
    tree_id: str
    code: str
    cost: dict[str, int]
    prerequisites: tuple[tuple[str, str], ...] = ()

    @property
    def key(self) -> tuple[str, str]:
        return (self.tree_id, self.code)

    def label(self) -> str:
        price = " + ".join(f"{n} {c}" for c, n in sorted(self.cost.items()) if n)
        return f"{self.tree_id}.{self.code} ({price or 'free'})"


def _cost_map(entry: dict) -> dict[str, int]:
    """`currency_cost` -> `{heirloom: amount}`, dropping the zeros.

    Every building requirement ships a `gold` entry of 0. Keeping it would put
    a currency in the map that the exchange cannot trade and the wallet does
    not carry as a heirloom, so the filter is by VALUE as well as by name --
    a future upgrade that genuinely costs gold keeps it.
    """
    out: dict[str, int] = {}
    for currency in entry.get("currency_cost") or []:
        name = str(currency.get("type") or "")
        amount = int(currency.get("amount") or 0)
        if name and amount:
            out[name] = out.get(name, 0) + amount
    return out


@lru_cache(maxsize=8)
def load_building_upgrades(game_dir: str, building: str) -> tuple[UpgradeNode, ...]:
    """Every node of every tree for one building, in file order.

    File order is the order the panel draws the trees down the page, which is
    what makes `townlayout.upgrade_node(tree, node)` indexable against this.
    Returns `()` for a building with no upgrade file rather than raising: the
    graveyard and the statue have none, and they are legitimate destinations.
    """
    path = Path(game_dir) / "upgrades" / "building" / f"{building}.upgrades.json"
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            raw = json.load(fh)
    except (OSError, ValueError):
        return ()
    out: list[UpgradeNode] = []
    for tree in raw.get("trees") or []:
        tree_id = str(tree.get("id") or "")
        for req in tree.get("requirements") or []:
            out.append(UpgradeNode(
                tree_id=tree_id,
                code=str(req.get("code") or ""),
                cost=_cost_map(req),
                prerequisites=tuple(
                    (str(p.get("tree_id") or ""), str(p.get("requirement_code") or ""))
                    for p in req.get("prerequisite_requirements") or []),
            ))
    return tuple(out)


def read_rank_counts(upgrades_file: str | Path) -> dict[tuple[int, int], int]:
    """`{(tree_id hash, instance_number): ranks bought}` from
    `persist.upgrades.json`.

    ## What that file actually contains, because it is not what it looks like

    `purchases.<n>` entries carry four fields and **two of them decode to
    nothing**: `requirement_code` and `is_purchased` are zero-length in every
    one of the 228 entries of a real Week 22 save, so the obvious reading --
    filter on `is_purchased`, key on `requirement_code` -- silently returns an
    EMPTY SET on a save with 228 purchases in it. That is the failure this
    docstring exists to prevent someone re-introducing.

    What the file really encodes:

    * **`tree_id` is `dd_name_hash(tree.id)`**, not text. 48 of the 85 distinct
      hashes in that save invert against the shipped `upgrades/**` trees; the
      other 37 belong to DLC and Workshop classes whose files are not under
      `upgrades/`. The hash is one-way, so inverting means hashing the
      candidates -- the same method `bridge/dungeon_map.py` uses for
      `curio_prop`. Decode it **unsigned**: DSON reads 4-byte fields signed.
    * **An entry's PRESENCE is the purchase, and the COUNT is the rank.** Five
      entries for `(crusader.smite, hero 11)` is `smite` at rank 5. Verified
      against every tree in the save: **0 of 85 exceed their own rank count**,
      which is what makes this a reading rather than a guess -- a wrong one
      would overflow somewhere in 228 entries.
    * **`instance_number` is which hero**, for the per-hero trees. Building
      trees are singletons and carry one instance.

    ## What is NOT in there

    **No building tree appears in either of Fran's saves.** That is not a
    format gap: the wallet in the Week 22 save is still within two busts of
    the shipped starting heirlooms, so no building upgrade was ever bought.
    A caller must therefore treat an empty result as "nothing bought", which
    is also what a fresh campaign reports -- and that is the case the
    overnight loop actually plays.

    Returns `{}` for a missing or unreadable file. A caller cannot distinguish
    that from "nothing bought", and does not need to: being wrong costs an
    offer the game refuses, which is a wasted click and not a wrong one.

    **A fresh campaign's file is plain JSON, not DSON**, and nests the
    purchases one level deeper (`{"version", "data": {"purchases"}}`):
    `scripts/starting_save/persist.upgrades.json` is copied in byte-for-byte
    and only becomes DSON at the game's first save. Reading it with
    `parse_bytes` raised on the magic, the `except` turned that into `{}`, and
    a new campaign's 28 starting skills read as none bought (2026-10-10).
    """
    try:
        from data.dson import parse_file
        raw = parse_file(upgrades_file)
    except Exception:                                     # noqa: BLE001
        return {}
    tree = raw.to_dict() if hasattr(raw, "to_dict") else raw
    tree = tree or {}
    entries = tree.get("purchases") or (tree.get("data") or {}).get("purchases") or {}
    counts: dict[tuple[int, int], int] = {}
    for entry in (entries.values() if isinstance(entries, dict) else entries):
        if not isinstance(entry, dict) or "tree_id" not in entry:
            continue
        try:
            key = (int(entry["tree_id"]) & 0xFFFFFFFF,
                   int(entry.get("instance_number") or 0))
        except (TypeError, ValueError):
            continue
        counts[key] = counts.get(key, 0) + 1
    return counts


def read_purchased(upgrades_file: str | Path, nodes) -> set[tuple[str, str]]:
    """`{(tree_id, code)}` already bought, for the trees in `nodes`.

    Turns `read_rank_counts`'s hashed, instance-keyed tallies back into the
    `(tree_id, code)` pairs `next_nodes` checks prerequisites against, by
    taking the **first N codes in file order** for a count of N. File order is
    the purchase order because a rank's prerequisite is the rank below it --
    which is exactly why a bare count is enough and no `requirement_code`
    field is needed.

    `nodes` is passed in rather than loaded here so a caller can cover several
    buildings at once: a `stage_coach` prerequisite can name a `guild` tree,
    and checking it against a set built from the coach alone would refuse an
    upgrade that is in fact available.
    """
    from data.dson import dd_name_hash

    counts = read_rank_counts(upgrades_file)
    by_hash: dict[int, int] = {}
    for (tree_hash, _instance), n in counts.items():
        # Buildings are singletons, so an instance dimension cannot appear on
        # one; taking the max is right for a hero tree too, where "has anyone
        # bought rank 3" is the question a shared tree can answer.
        by_hash[tree_hash] = max(by_hash.get(tree_hash, 0), n)

    ordered: dict[str, list[str]] = {}
    for node in nodes:
        ordered.setdefault(node.tree_id, []).append(node.code)

    out: set[tuple[str, str]] = set()
    for tree_id, codes in ordered.items():
        bought = by_hash.get(dd_name_hash(tree_id) & 0xFFFFFFFF, 0)
        for code in codes[:bought]:
            out.add((tree_id, code))
    return out


def next_nodes(nodes, purchased: set[tuple[str, str]]) -> list[UpgradeNode]:
    """The nodes that are buyable RIGHT NOW: not owned, every prerequisite met.

    A prerequisite naming a tree this building does not own is checked against
    `purchased` like any other -- the caller passes the whole campaign's
    purchase set, not one building's, precisely so a cross-building
    prerequisite resolves.
    """
    return [n for n in nodes
            if n.key not in purchased
            and all(p in purchased for p in n.prerequisites)]


# -- the exchange ---------------------------------------------------------

@dataclass(frozen=True)
class Rate:
    from_type: str
    from_amount: int
    to_type: str
    to_amount: int


@dataclass(frozen=True)
class Trade:
    """One row of the exchange widget, executed `times` times."""
    rate: Rate
    times: int

    @property
    def spends(self) -> int:
        return self.rate.from_amount * self.times

    @property
    def gains(self) -> int:
        return self.rate.to_amount * self.times

    def label(self) -> str:
        return (f"{self.spends} {self.rate.from_type} -> "
                f"{self.gains} {self.rate.to_type}")


@lru_cache(maxsize=4)
def load_rates(game_dir: str) -> tuple[Rate, ...]:
    path = (Path(game_dir) / "campaign" / "heirloom_exchange"
            / "heirloom_exchange.json")
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            raw = json.load(fh)
    except (OSError, ValueError):
        return ()
    return tuple(Rate(from_type=str(r.get("exchange_from_type") or ""),
                      from_amount=int(r.get("exchange_from_amount") or 0),
                      to_type=str(r.get("exchange_to_type") or ""),
                      to_amount=int(r.get("exchange_to_amount") or 0))
                 for r in raw.get("exchange_rates") or []
                 if r.get("exchange_from_amount") and r.get("exchange_to_amount"))


def crest_values(rates) -> dict[str, float]:
    """What one of each heirloom is worth, in crests, per the shipped rates.

    Read off the three `X -> crest` rows, which are the game's own statement of
    relative value; the crest is 1 by definition because it is the finest
    denomination and the only currency every other one sells into. An heirloom
    with no sell row keeps a value of 1.0 -- neutral, so an unknown currency
    can neither be preferred nor avoided by a cost function that cannot price
    it.

    Every trade in the table is LOSSY in these units (`3 crest -> 1 deed` is
    3.0 in for 1.5 out), which is the exchange taking its cut and is exactly
    why the solver should minimise what it puts in rather than count rows.
    """
    values = {name: 1.0 for name in HEIRLOOMS}
    for rate in rates or ():
        if rate.to_type == "crest" and rate.from_amount:
            values[rate.from_type] = rate.to_amount / rate.from_amount
    return values


@lru_cache(maxsize=4)
def heirloom_bias(game_dir: str) -> dict[str, dict[str, int]]:
    """Which heirloom each region actually drops, from `loot/loot.json`.

    `solve_exchange` answers "I am short three deeds, what do I trade?"; this
    answers the question before it -- **where do I go to earn them?** The two
    together turn an upgrade shortfall into a region to raid.

    Returns `{dungeon: {heirloom: weight}}` from the shipped drop tables,
    which carry a `dungeon` field and the per-entry `chances` the game rolls
    against. Identical across all three difficulty tiers (1/3/5), so the tier
    is collapsed; the weights below are the whole table.

        dungeon    bust  deed  portrait  crest
        cove          1     1         1      3
        crypts        3     1         1      3
        warrens       1     1         3      3
        weald         1     3         1      3

    **The community claim is right for three regions and misleading for the
    fourth.** Ruins->busts, Weald->deeds and Warrens->portraits are real: each
    elevates exactly one heirloom to 3 against a baseline of 1. But *crest is
    3 in every region*, so "Cove -> crests" names the universal baseline
    rather than a Cove speciality -- **the Cove is the one region that
    elevates nothing**, and if crests are what is short then the region does
    not matter. A router that believed the guide would send a party to the
    Cove for crests it would have got anywhere.

    An empty dict when the tables cannot be read, so a caller degrades to
    "region does not matter" rather than raising.
    """
    path = Path(game_dir) / "loot" / "loot.json"
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            raw = json.load(fh)
    except (OSError, ValueError):
        return {}
    out: dict[str, dict[str, int]] = {}
    for table in raw.get("loot_tables") or ():
        dungeon = str(table.get("dungeon") or "")
        if not dungeon:
            continue
        for entry in table.get("entries") or ():
            data = entry.get("data") or {}
            if str(data.get("type") or "") != "heirloom":
                continue
            item = str(data.get("id") or "")
            if item not in HEIRLOOMS:
                continue
            chances = int(entry.get("chances") or 0)
            # max, not sum: the same (dungeon, heirloom) row repeats once per
            # difficulty tier with an IDENTICAL `chances`, and summing them
            # would invent a tier effect the weights do not have. The `amount`
            # on those rows is what scales with the tier -- bust 2/3/4 and
            # crest 4/6/8 at tiers 1/3/5 -- so this function answers WHICH
            # region, and the tier answers how much per drop.
            bucket = out.setdefault(dungeon, {})
            bucket[item] = max(bucket.get(item, 0), chances)
    return out


def regions_for(heirloom: str, game_dir: str) -> list[str]:
    """The regions that drop `heirloom` most, best first, then alphabetical.

    Every region when nothing is elevated -- which is the honest answer for
    crests, and the reason this returns a list rather than one region.
    """
    bias = heirloom_bias(game_dir)
    if not bias:
        return []
    ranked = sorted(bias.items(),
                    key=lambda kv: (-kv[1].get(heirloom, 0), kv[0]))
    best = ranked[0][1].get(heirloom, 0)
    return [d for d, w in ranked if w.get(heirloom, 0) == best]


def _spend_value(trade: Trade, values: dict[str, float]) -> float:
    return trade.spends * values.get(trade.rate.from_type, 1.0)


def _apply(wallet: dict[str, int], trade: Trade) -> Optional[dict[str, int]]:
    """The wallet after a trade, or None if it cannot be afforded.

    Affordability is checked here rather than by the search, so no plan can
    contain a step the game would refuse -- a plan that is only valid if
    executed in some other order is not a plan.
    """
    have = wallet.get(trade.rate.from_type, 0)
    if trade.spends > have:
        return None
    out = dict(wallet)
    out[trade.rate.from_type] = have - trade.spends
    out[trade.rate.to_type] = out.get(trade.rate.to_type, 0) + trade.gains
    return out


def _affords(wallet: dict[str, int], cost: dict[str, int]) -> bool:
    return all(wallet.get(currency, 0) >= amount
               for currency, amount in cost.items())


def solve_exchange(wallet: dict[str, int], cost: dict[str, int],
                   rates, max_hops: int = 3) -> Optional[list[Trade]]:
    """The cheapest sequence of trades that makes `cost` affordable.

    `[]` when the wallet already covers it -- a distinct answer from `None`,
    which is "no sequence within `max_hops` gets there". Collapsing the two
    would make "you can already buy this" indistinguishable from "you never
    can", and the actuator does opposite things about them.

    **Search, not a rule.** Four currencies and twelve asymmetric rates, with
    some pairs only reachable through a third currency; a hand-written "short
    on deeds -> sell crests" is right for one upgrade and silently wrong for
    the next. Small enough to enumerate: the branching factor is the number of
    rates whose amount the wallet can currently cover, and `max_hops` of 3 is
    already more than any shipped shortfall needs.

    **Ordering is by CREST-EQUIVALENT spent, then by hop count.** Counting raw
    units instead picks `2 portrait -> 3 deed` over `3 crest -> 1 deed`,
    which costs twice as much: see `crest_values` and the module docstring.
    Among equally-priced plans the shorter one is fewer clicks and fewer
    chances for a modal to eat one.
    """
    if _affords(wallet, cost):
        return []
    if not rates:
        return None

    values = crest_values(rates)
    best: Optional[tuple[float, int, list[Trade]]] = None
    seen: dict[tuple, float] = {}

    def key(w):
        return tuple(sorted(w.items()))

    def walk(w: dict[str, int], plan: list[Trade], spent: float) -> None:
        nonlocal best
        if best is not None and (spent, len(plan)) >= (best[0], best[1]):
            return
        if len(plan) >= max_hops:
            return
        for rate in rates:
            have = w.get(rate.from_type, 0)
            if rate.from_amount <= 0 or have < rate.from_amount:
                continue
            # How many times this row could be pulled. Bounded by the wallet,
            # and every count is tried because the cheapest plan is very often
            # "this row, twice" rather than two different rows.
            for times in range(1, have // rate.from_amount + 1):
                trade = Trade(rate, times)
                after = _apply(w, trade)
                if after is None:
                    break
                cost_here = spent + _spend_value(trade, values)
                # Pulling the same row again only ever costs more, so once
                # this branch is past the incumbent every larger `times` is
                # too. That is what keeps the enumeration small.
                if best is not None and cost_here > best[0]:
                    break
                plan.append(trade)
                if _affords(after, cost):
                    if best is None or (cost_here, len(plan)) < (best[0], best[1]):
                        best = (cost_here, len(plan), list(plan))
                else:
                    state = key(after)
                    if seen.get(state, float("inf")) > cost_here:
                        seen[state] = cost_here
                        walk(after, plan, cost_here)
                plan.pop()

    walk(dict(wallet), [], 0)
    return best[2] if best else None


def plan_purchases(wallet: dict[str, int], nodes, rates,
                   max_hops: int = 3) -> tuple[list, dict[str, int]]:
    """Buy `nodes` in the order given, trading when short. Returns
    `([(node, trades)], wallet_after)`.

    **In the order given, and that is the caller's decision to make.** Trees
    have prerequisites, so "the top path" is a sequence and not a set; and a
    solver free to reorder could spend the crests that the second node needs
    on the first one's shortfall. `next_nodes` is what tells the caller which
    orders are legal.

    A node that cannot be afforded even after trading is SKIPPED, not
    abandoned: a later node may be cheaper in a currency the wallet still has,
    and reporting "bought a, could not afford b" is more useful than stopping.
    """
    out = []
    have = dict(wallet)
    for node in nodes:
        trades = solve_exchange(have, node.cost, rates, max_hops)
        if trades is None:
            continue
        for trade in trades:
            nxt = _apply(have, trade)
            if nxt is None:                    # unreachable; solve_exchange checks
                break
            have = nxt
        if not _affords(have, node.cost):
            continue
        for currency, amount in node.cost.items():
            have[currency] = have.get(currency, 0) - amount
        out.append((node, trades))
    return out, have


def main() -> int:
    from data.paths import GAME_DIR as game_dir
    rates = load_rates(str(game_dir))
    print(f"{len(rates)} exchange rates")
    for r in rates:
        print(f"  {r.from_amount} {r.from_type:9s} -> {r.to_amount} {r.to_type}")

    print("\nstage coach trees:")
    nodes = load_building_upgrades(str(game_dir), "stage_coach")
    for n in nodes:
        prereq = ", ".join(f"{t}.{c}" for t, c in n.prerequisites)
        print(f"  {n.label():52s} {'needs ' + prereq if prereq else ''}")

    # Fran's case, from the shipped starting wallet.
    wallet = {"bust": 10, "portrait": 10, "deed": 10, "crest": 20}
    top = [n for n in nodes if n.tree_id.endswith("numrecruits")][:2]
    print(f"\nFran's case -- wallet {wallet}, buying the top path:")
    bought, left = plan_purchases(wallet, top, rates)
    values = crest_values(rates)
    print(f"  crest values: {values}")
    for node, trades in bought:
        price = sum(_spend_value(t, values) for t in trades)
        why = ("  via " + "; ".join(t.label() for t in trades)
               + f"  [{price:g} crest-equivalent]") if trades else ""
        print(f"  BUY {node.label()}{why}")
    print(f"  wallet after: {left}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
