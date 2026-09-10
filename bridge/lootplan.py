"""Route, torch and pack, planned together -- PLAYBOOK 2.1/2.2/2.3, decided.

Fran, 2026-09-06: *"minimizing low torch battles + maximizing loot at the same
time"*, and *"improve the layout planner in dungeons to really optimize loot in
the end using all the available info to us, even info we only get via code
rather than seeing it ingame"*.

`bridge/route.py` answers "where next" in moves and fights and says so in its
own docstring: *"optional detours are priced, not decided ... how a player
trades moves and fights against loot is a judgement no shipped file contains"*.
That was true when it was written and it is not true any more --
`data/loot_loader.py` prices a draw from the game's own tables. So this module
is the half `route.py` deliberately left out, and `route.py` keeps doing what
it does well: the exact shortest cover of what the quest requires.

## The three quantities, and why they have to be planned together

**A loot event is worth more in the dark.** A battle at torch 0 draws its bonus
table TWICE at 75%; a chest draws at 95% instead of never; and gold is +30% on
top. Measured through the shipped tables at Weald/Apprentice that is **+454g on
a fight** and **+165g and a third of a crest on a chest**, against zero at
radiant.

**A battle is worse in the dark**, and that is Fran's floor: no planned fight
below `TORCH_FLOOR` while the party can afford the light. The snuff is exempt
because it happens on the turn that ends the fight -- PLAYBOOK 2.3's rule, and
the reason the exemption is safe.

**Light is a budget, not a switch.** `tile_light_loss` burns 1 per tile while
bright and **6 once dim**, and a torch restores 25, so a pack with two torches
cannot hold the floor across a long map. `--torch-floor` is a target, not a
guarantee, and a plan that cannot meet it says which fights it could not light.

## Why "defer the curio and take it on the way home" is free, exactly

The party has to walk back to the entrance to finish a completed quest -- so
those corridors are re-entered whether or not anything was deferred. Deferring
a curio that sits on the homeward path therefore adds **no** re-entry, and
`corridor_return_content` is the reason that distinction matters: re-entering a
cleared corridor repopulates it, 5% battle / 7.5% hunger / 5% trap, and **each
climbs by half again at torch 0**. A dark backtrack MAKES low-torch battles.

Hence the rule, which needs no invented weight and reproduces the route Fran
described for the Old Road unaided:

> **Defer what is on the way home. Take the rest on the way out.**

A detour that exists only to revisit something is not deferred; it is taken lit
on the outbound pass instead, because deferring it would buy loot with exactly
the thing the objective says to minimise.

## What it does NOT decide

Whether to keep going at all. That is `Layers._raid_end` (affliction and the
Resolve Check) and `bridge/retreat.py` (what a death costs in town gold), both
unchanged. This module answers "given that we are continuing, in what order and
at what light" -- and reports the expected value so those two can be read
against it.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Mapping, Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bridge.curios import (CurioHero, SUPPLY_ITEMS, bare_hand_value,
                           definition_for, item_value)
from bridge.dungeon_map import (BATTLE_CONTENTS, CONTENT_BATTLE,
                                CURIO_CONTENTS, DungeonMap)
from bridge.route import Quest, _all_pairs, _cover_order, _stitch, mandatory_targets
from data.loot_loader import (GAME_DIR, LootValue, darkness_value,
                              gold_multiplier, load_loot_tables, walk_costs)

#: PLAYBOOK 2.3's working level, and Fran's hard floor for a planned fight.
#: 76 rather than 75 because `darkness.lowest_excluded` puts 75 in the dim
#: bracket -- the correction `torchlight.py` already carries.
TORCH_FLOOR = 76.0

#: What the snuff aims at, and what a torch restores. Both from
#: `bridge/torchlight.py` so there is one statement of each in the bridge.
from bridge.torchlight import SHUTDOWN_LEVEL, TORCH_RESTORE  # noqa: E402

#: The darkness-bonus kind a loot-bearing curio draws on. **A declared
#: reading**: `darkness_overrides.loot.json` names only `battle` and `chest`,
#: and a curio is neither word. Reading it as a chest is what makes the
#: shutdown worth anything on a curio at all; reading it as nothing would make
#: the darkness bonus apply to fights alone.
#:
#: Falsifier, and it is cheap: open one crate at torch 100 and one at torch 0
#: and compare the drops. If they match, curios take no bonus and only the
#: `gold_multiplier` half applies -- which changes the SIZE of the deferral
#: gain, not its sign, so the route would survive.
CURIO_BONUS_KIND = "chest"


# -- what is on the map --

@dataclass(frozen=True)
class LootEvent:
    """One thing on one tile that is worth something, or costs a fight."""
    area: str
    tile: int
    kind: str                      # "battle" | "curio"
    curio_id: str = ""
    guarded: bool = False          # its guard is a battle on the same tile
    in_room: bool = False

    @property
    def key(self) -> tuple:
        return (self.area, self.tile, self.kind)


def events_on(dungeon: DungeonMap) -> tuple[LootEvent, ...]:
    """Every unresolved battle and curio still on the map, tile by tile.

    **Tile granularity is the structural change.** `route.optional_targets`
    works in rooms and excludes corridor curios by design; the Old Road's whole
    decision is a corridor curio, so a room-level target list cannot express
    it.

    Reads `content`, which is the live state -- a spent curio drops to 0 and
    keeps its `curio_prop`, so this lists what is LEFT rather than what was
    generated.
    """
    out: list[LootEvent] = []
    for area_id in sorted(dungeon.areas):
        area = dungeon.areas[area_id]
        for tile in sorted(area.tiles, key=lambda t: t.index):
            guarded = tile.content in BATTLE_CONTENTS and tile.content != CONTENT_BATTLE
            if tile.content == CONTENT_BATTLE or guarded:
                out.append(LootEvent(area=area_id, tile=tile.index, kind="battle",
                                     guarded=guarded, in_room=area.is_room))
            if tile.content in CURIO_CONTENTS and tile.curio_id:
                out.append(LootEvent(area=area_id, tile=tile.index, kind="curio",
                                     curio_id=tile.curio_id, guarded=guarded,
                                     in_room=area.is_room))
    return tuple(out)


# -- what one curio is worth, with each thing the party could use on it --

@dataclass(frozen=True)
class Option:
    """One way to resolve a curio, and what it yields before the torch."""
    item: Optional[str]
    base: LootValue
    polarity: float                # `bridge/curios.py`'s +1/-1 judgement
    why: str
    #: How often this interaction draws loot AT ALL. The darkness bonus is an
    #: extra draw on a draw that happens, so a curio answering the bare hand
    #: with `Blight 1` earns none of it. Crediting it anyway made the Old
    #: Road's trapped chest look like +165g when its bare-handed outcome is
    #: pure poison -- and the planner took it.
    loot_chance: float = 0.0

    @property
    def harmful(self) -> bool:
        return self.polarity < 0


def _loot_of(results, difficulty: int, dungeon: str, game_dir: str) -> LootValue:
    """Price one outcome's concrete results, weighted as the table weights them."""
    tables = load_loot_tables(game_dir)
    total = sum(r.weight for r in results) or float(len(results) or 1)
    out = LootValue()
    for result in results:
        share = (result.weight / total) if total else 0.0
        out = out + share * tables.expected(result.name, difficulty, dungeon,
                                            game_dir)
    return out


def curio_options(curio_id: str, difficulty: int = 0, dungeon: str = "",
                  game_dir: str = str(GAME_DIR)) -> tuple[Option, ...]:
    """Bare-handed, and one per supply item the curio has an interaction for.

    The loot is priced here; the POLARITY still comes from `bridge/curios.py`,
    which already resolves quirks, diseases and stress effects against the real
    definitions. The two answer different questions and both are needed: a
    curio can pay well and still be a trap (`stack_of_books` answers its own
    item with `Stress 5`), and one can be harmless and pay nothing.
    """
    curio = definition_for(curio_id)
    if curio is None:
        return ()
    out: list[Option] = []

    bare = LootValue()
    total = curio.total_weight
    loot_share = 0.0
    if total:
        for kind, outcome in curio.outcomes.items():
            if kind != "Loot":
                continue
            loot_share += outcome.weight / total
            bare = bare + (outcome.weight / total) * _loot_of(
                outcome.results, difficulty, dungeon, game_dir)
    out.append(Option(item=None, base=bare, polarity=bare_hand_value(curio),
                      why="bare-handed", loot_chance=loot_share))

    for item, interaction in sorted(curio.items.items()):
        if item not in SUPPLY_ITEMS:
            continue
        value = LootValue()
        draws = 1.0 if interaction.result_type == "Loot" else 0.0
        if draws:
            value = _loot_of(interaction.results, difficulty, dungeon, game_dir)
        out.append(Option(item=item, base=value, polarity=item_value(interaction),
                          why=interaction.text or
                              f"{item} -> {interaction.result_type}",
                          loot_chance=draws))
    return tuple(out)


#: Of the loot an interaction draws, how much may be provisions before the
#: curio counts as a provision curio. Fran's own word is "mostly", so it is a
#: half. The OTHER half of the rule needs no number: an interaction only pays
#: for the dark at all if every one of its outcomes draws loot, which is a
#: structural fact about the curio rather than a threshold.
#:
#: Falsifier: a curio this refuses to defer, opened twice at torch 100 and at
#: torch 0, whose extra yield is plainly worth more than the heal or the
#: provisions it would have handed over earlier in the run.
DEFER_MAX_SUPPLY_SHARE = 0.5


def pays_for_the_dark(curio_id: str, difficulty: int = 0, dungeon: str = "",
                      game_dir: str = str(GAME_DIR)) -> bool:
    """Is holding this curio back for torch 0 worth anything?

    Fran, after watching a raid, 2026-09-06: *"healing or boosting curios like
    the fountain should be used during the dungeon, as they aren't loot
    curios, same for curios that mostly produces provisions like torches, food
    or blood"*.

    He is describing what the darkness table can and cannot multiply, and both
    halves are already in the data:

    * **`loot_chance` is the share of a curio's outcomes that draw loot AT
      ALL**, and the bonus is an extra draw on a draw. `holy_fountain` reads
      0.50 bare-handed and 0.00 for its holy water -- half of it is a heal or
      a buff, which is worth most while the party is hurt and worth nothing
      banked at the entrance.
    * **`LootValue.supplies` is how much of the gold is really provisions.**
      `sconce` prices at 5 gold and *all five* are a torch. A torch taken on
      the way home is never lit; its whole value is the light it would have
      given for the rest of the run.

    So a deferral is only free -- which is the argument the whole module rests
    on -- when the thing deferred is loot and nothing else. Anything with a
    non-loot outcome, or whose loot is mostly provisions, is taken as the
    party passes it.

    True when ANY interaction qualifies, because the pack assignment later
    picks among them; the point here is only whether the dark can ever be
    worth waiting for.
    """
    for option in curio_options(curio_id, difficulty, dungeon, game_dir):
        if option.harmful or option.loot_chance < 1.0:
            continue
        value = option.base
        if value.gold > 0 and value.supplies > DEFER_MAX_SUPPLY_SHARE * value.gold:
            continue
        return True
    return False


def option_worth(option: Option, torch: float, difficulty: int, dungeon: str,
                 crest_gold: Optional[float] = None,
                 game_dir: str = str(GAME_DIR)) -> tuple:
    """`(gold-equivalent, unpriced upside)` for this curio, item and light.

    A PAIR, compared lexicographically, and the second half is why. Trinkets
    are deliberately never given a gold price -- a trinket's worth to a
    campaign is not its sale value -- so a curio whose only yield is a trinket
    scores exactly 0, the same as one that yields nothing. That is right as a
    price and wrong as a decision: `bandits_trapped_chest` is `Blight 1` bare
    and loot table `T` with a skeleton key, and Fran's own instruction is to
    open it *if a key is spare*.

    So the tie-break carries the unpriced upside instead of pricing it. It can
    never outrank gold, and it decides exactly the cases the gold column calls
    a draw.
    """
    value = value_at(option, torch, difficulty, dungeon, game_dir)
    worth = value.worth() if crest_gold is None else value.worth(crest_gold)
    return (worth, value.trinkets)


def value_at(option: Option, torch: float, difficulty: int, dungeon: str,
             game_dir: str = str(GAME_DIR)) -> LootValue:
    """This interaction at this light: the gold multiplier, then the extra draw.

    The bonus is scaled by `loot_chance` because it is an extra draw on a draw
    -- a curio that answers with a status effect rolls no loot table and gets
    nothing from the dark.
    """
    if option.harmful:
        return LootValue()
    scaled = option.base.with_gold_multiplier(gold_multiplier(torch, game_dir))
    if option.loot_chance <= 0:
        return scaled
    return scaled + option.loot_chance * darkness_value(
        CURIO_BONUS_KIND, torch, difficulty, dungeon, game_dir)


def battle_value_at(torch: float, difficulty: int, dungeon: str,
                    game_dir: str = str(GAME_DIR)) -> LootValue:
    """What a fight's loot roll gains from the dark.

    The fight's OWN drop is not on the map -- it belongs to the encounter -- so
    only the bonus draw is counted. That understates every fight by the same
    unknown constant, which is exactly the quantity a comparison between two
    torch levels cancels.
    """
    return darkness_value("battle", torch, difficulty, dungeon, game_dir)


# -- the pack is shared, so the items are assigned, not chosen per curio --

@dataclass(frozen=True)
class Assignment:
    """Which item each curio gets, and what the whole set is then worth."""
    items: Mapping[tuple, Optional[str]]        # event key -> item or None
    worth: tuple
    exact: bool = True


def _plus(a: tuple, b: tuple) -> tuple:
    return tuple(x + y for x, y in zip(a, b))


ZERO_SCORE = (0.0, 0.0)


def assign_items(choices: Sequence[tuple], inventory: Mapping[str, int],
                 budget: int = 200_000) -> Assignment:
    """Spread a limited pack over the curios on the route, exactly.

    `choices` is one `(event_key, [(item, worth), ...])` per curio, already
    priced at the torch level that curio will actually see.

    **`bridge/curios.py::advise` cannot do this and is not wrong to.** It
    answers one curio at a time against the whole pack, so with a single key
    and two locked chests it says "use the key" twice. Fran, 2026-09-06: *"if
    we know the dungeon still have 3 extra chests, doesnt make sense to drop
    the keys"* -- which is the same fact seen from the inventory side.

    Exact by DP over the remaining counts of the item types anything actually
    wants. That is usually two or three types with two or three of each, so the
    state space is tiny; when it is not, this falls back to greedy by marginal
    gain and says so, because a plan that takes a minute to compute is not a
    plan a poll loop can use.
    """
    wanted = sorted({item for _key, opts in choices for item, _w in opts if item})
    counts = tuple(max(0, int(inventory.get(item, 0) or 0)) for item in wanted)

    space = 1
    for count in counts:
        space *= count + 1
    if not wanted or space * max(1, len(choices)) > budget:
        return _assign_greedy(choices, dict(zip(wanted, counts)), wanted)

    index = {item: i for i, item in enumerate(wanted)}
    best: dict[tuple, tuple] = {counts: (ZERO_SCORE, ())}
    for key, opts in choices:
        nxt: dict[tuple, tuple] = {}
        for state, (score, chosen) in best.items():
            for item, worth in opts:
                if item is None:
                    new_state = state
                elif state[index[item]] <= 0:
                    continue
                else:
                    as_list = list(state)
                    as_list[index[item]] -= 1
                    new_state = tuple(as_list)
                total = _plus(score, worth)
                have = nxt.get(new_state)
                if have is None or total > have[0]:
                    nxt[new_state] = (total, chosen + ((key, item),))
        if not nxt:                      # every curio had no legal option
            nxt = best
        best = nxt
    score, chosen = max(best.values(), key=lambda pair: pair[0])
    return Assignment(items=dict(chosen), worth=score, exact=True)


def _assign_greedy(choices, pool: dict, wanted) -> Assignment:
    """The fallback. Marginal gain over the bare hand, biggest first."""
    picked: dict[tuple, Optional[str]] = {}
    bare = {key: max((w for i, w in opts if i is None), default=ZERO_SCORE)
            for key, opts in choices}
    ranked = []
    for key, opts in choices:
        for item, worth in opts:
            if item:
                gain = tuple(x - y for x, y in zip(worth, bare[key]))
                ranked.append((gain, key, item))
    ranked.sort(reverse=True)
    total = ZERO_SCORE
    for value in bare.values():
        total = _plus(total, value)
    for gain, key, item in ranked:
        if gain <= ZERO_SCORE or key in picked or pool.get(item, 0) <= 0:
            continue
        pool[item] -= 1
        picked[key] = item
        total = _plus(total, gain)
    for key, _opts in choices:
        picked.setdefault(key, None)
    return Assignment(items=picked, worth=total, exact=not wanted)


# -- the plan --

@dataclass(frozen=True)
class Stop:
    """One thing to do, in order."""
    area: str
    action: str          # walk | relight | fight | snuff | take | defer | skip | finish
    tile: int = -1
    torch: float = 0.0
    item: str = ""
    curio_id: str = ""
    value: LootValue = field(default_factory=LootValue)
    why: str = ""

    def line(self) -> str:
        what = {"walk": "walk to", "fight": "FIGHT in", "take": "TAKE in",
                "defer": "DEFER in", "skip": "skip in", "snuff": "SNUFF at",
                "relight": "RELIGHT at", "finish": "FINISH at"}.get(
                    self.action, self.action)
        head = f"{what} {self.area}"
        if self.tile >= 0 and self.action not in ("walk", "finish", "snuff",
                                                  "relight"):
            head += f" tile{self.tile}"
        if self.curio_id:
            head += f" ({self.curio_id}{', ' + self.item if self.item else ''})"
        gain = f"  +{self.value.line()}" if not self.value.empty else ""
        return f"  torch {self.torch:>3.0f}  {head:<46}{gain}"


@dataclass(frozen=True)
class LootPlan:
    stops: tuple[Stop, ...] = ()
    walk: tuple[str, ...] = ()
    value: LootValue = field(default_factory=LootValue)
    torches_spent: int = 0
    torches_left: int = 0
    dark_battles: tuple[str, ...] = ()
    #: Corridors walked a second time, split by the light they were walked in.
    #: Both are counted, and the whole walk is counted -- an outbound cover of
    #: a branching map doubles back too, and the first version of this only
    #: looked at the leg home, which reported a nine-room raid as one re-entry
    #: when it made seven.
    relit_reentries: int = 0
    dark_reentries: int = 0
    note: str = ""

    @property
    def reentered(self) -> int:
        return self.relit_reentries + self.dark_reentries

    @property
    def next_stop(self) -> Optional[Stop]:
        return self.stops[0] if self.stops else None

    def expected_ambushes(self, game_dir: str = str(GAME_DIR)) -> float:
        """What re-entering those corridors is expected to spawn.

        `corridor_return_content` repopulates a cleared corridor and darkness
        raises every one of its three chances by half again, so the two counts
        cannot share a rate.
        """
        lit = walk_costs(100.0, game_dir).return_any
        dark = walk_costs(SHUTDOWN_LEVEL, game_dir).return_any
        return self.relit_reentries * lit + self.dark_reentries * dark

    def expected_spawned_battles(self, game_dir: str = str(GAME_DIR)) -> float:
        """Fights the re-entries alone are expected to create."""
        lit = walk_costs(100.0, game_dir).return_battle
        dark = walk_costs(SHUTDOWN_LEVEL, game_dir).return_battle
        return self.relit_reentries * lit + self.dark_reentries * dark

    def expected_dark_battles(self, game_dir: str = str(GAME_DIR)) -> float:
        """Planned fights below the floor, plus what the dark walk will spawn.

        The quantity Fran's objective names. The two halves are different in
        kind and both belong in it: a floor violation is a fight already on the
        map that the pack could not light, and a dark re-entry is one the walk
        home creates.
        """
        return (len(self.dark_battles)
                + self.dark_reentries
                * walk_costs(SHUTDOWN_LEVEL, game_dir).return_battle)

    def report(self, crest_gold: Optional[float] = None) -> str:
        out = [f"LOOT PLAN  {self.value.line()}"
               f"   (worth {self.value.worth() if crest_gold is None else self.value.worth(crest_gold):.0f})"]
        if self.note:
            out.append(f"  {self.note}")
        out.extend(stop.line() for stop in self.stops)
        out.append(f"  torches: spent {self.torches_spent}, "
                   f"{self.torches_left} left")
        if self.dark_battles:
            out.append(f"  !! {len(self.dark_battles)} planned fight(s) below "
                       f"the floor, no torch to fix it: "
                       f"{', '.join(self.dark_battles)}")
        if self.reentered:
            out.append(f"  {self.reentered} corridor(s) walked twice "
                       f"({self.relit_reentries} lit, {self.dark_reentries} "
                       f"dark): expected {self.expected_ambushes():.2f} new "
                       f"encounters, of which "
                       f"{self.expected_spawned_battles():.2f} fights")
        out.append(f"  expected fights in the dark: "
                   f"{self.expected_dark_battles():.2f}")
        return "\n".join(out)


def _tiles(dungeon: DungeonMap, area_id: str) -> int:
    area = dungeon.areas.get(area_id)
    return len(area.tiles) if area is not None else 1


def _cover_to(start: str, targets: Sequence[str], finish: str,
              routes) -> list[str]:
    """Shortest walk covering `targets` and ENDING at `finish`.

    `route._cover_order` is free to end anywhere, which is right for an
    objective route and wrong for the way home -- a completed quest is handed
    in at the entrance, so the last area is fixed and the order has to be
    solved against it.
    """
    if not targets:
        leg = routes.get(start, {}).get(finish)
        return list(leg or [start])
    order = _cover_order(start, list(targets) + [finish], routes)
    # `_cover_order` is free to end anywhere. Pull the finish out and put it
    # back last: the cover of the rest is unchanged and the endpoint is fixed.
    order = [a for a in order if a != finish]
    return list(_stitch(start, order + [finish], routes) or [start])


def plan_loot(dungeon: DungeonMap, quest: Optional[Quest] = None,
              here: Optional[str] = None, torch: float = 100.0,
              inventory: Optional[Mapping[str, int]] = None,
              party: Sequence[CurioHero] = (),
              torch_floor: float = TORCH_FLOOR,
              crest_gold: Optional[float] = None,
              game_dir: str = str(GAME_DIR)) -> LootPlan:
    """Order the map, schedule the torch, and spend the pack. See the module docstring."""
    inventory = dict(inventory or {})
    difficulty = int(getattr(quest, "difficulty", 0) or 0)
    zone = str(getattr(quest, "dungeon", "") or "")
    start = here or dungeon.entrance_id or next(iter(dungeon.rooms), "")
    home = dungeon.entrance_id or start
    routes = _all_pairs(dungeon)
    events = events_on(dungeon)

    # 1. Where we must go, and where the loot is.
    must = list(mandatory_targets(dungeon, quest))
    loot_areas = sorted({e.area for e in events if e.kind == "curio"})
    battle_areas = sorted({e.area for e in events if e.kind == "battle"})
    targets = [a for a in dict.fromkeys(list(must) + loot_areas + battle_areas)
               if a in dungeon.rooms]

    # **A corridor is not a node in `_all_pairs`**, which BFSs room to room --
    # so planning from one returned an empty cover and the block reported "0g
    # still on the map" for the whole hallway. Caught by replaying a capture;
    # it is the common case, because the party spends most of a raid walking.
    # Same repair `route.plan_route` makes: leave by whichever end is cheaper.
    # The end comes from `corridor_rooms`, never the name split.
    corridor_prefix: list[str] = []
    if start in dungeon.corridors:
        best: Optional[tuple] = None
        for end in dungeon.corridor_rooms(start):
            if end not in routes:
                continue
            candidate = _stitch(end, _cover_order(end, targets, routes), routes)
            cost = sum(1 for a in candidate[1:] if a in dungeon.corridors)
            if best is None or cost < best[0]:
                best = (cost, end, candidate)
        if best is not None:
            corridor_prefix = [start]
            start = best[1]

    outbound = corridor_prefix + _stitch(
        start, _cover_order(start, targets, routes), routes)
    if not outbound:
        outbound = [start]
    homeward = _cover_to(outbound[-1], [], home, routes)
    # The area the walk home STARTS in is not revisited, so anything standing
    # there has to be resolved in place. Deferring it drops it silently, which
    # is what the first run of this did to the Old Road's trapped chest.
    revisited = set(homeward[1:])

    outbound_areas = list(dict.fromkeys(outbound))
    battle_at = {e.area for e in events if e.kind == "battle"}
    snuff_at = ""
    for area in outbound_areas:
        if area in battle_at:
            snuff_at = area
    # Everything from the snuff onwards is ALREADY dark, so deferring it buys
    # nothing and only risks losing it. The deferral is for what would
    # otherwise be opened in the light.
    before_snuff = outbound_areas
    if snuff_at in outbound_areas:
        before_snuff = outbound_areas[:outbound_areas.index(snuff_at)]
    # With no battle anywhere in the plan the dark is free immediately -- see
    # the snuff emitted just before the walk below -- so there is no "later,
    # in the dark" left to defer TO. Every curio is already going to be opened
    # dark, and a deferral here would only manufacture the revisit that
    # justifies it.
    if not battle_at:
        before_snuff = []

    # 2. Which curios can be deferred for FREE -- see the module docstring.
    #    A corridor the party must re-enter anyway costs nothing extra; one it
    #    would only re-enter to fetch something costs a `corridor_return_content`
    #    roll, which is the thing the objective says to minimise.
    deferred: set[tuple] = set()
    for event in events:
        if event.kind != "curio":
            continue
        if event.area in revisited and event.area in before_snuff                 and pays_for_the_dark(event.curio_id, difficulty, zone,
                                      game_dir):
            deferred.add(event.key)

    # 3. Price every curio at the light it will actually see, then spend the
    #    pack across all of them at once.
    def torch_when(event: LootEvent) -> float:
        return SHUTDOWN_LEVEL if event.key in deferred else torch

    choices, options_by_key = [], {}
    for event in events:
        if event.kind != "curio":
            continue
        opts = curio_options(event.curio_id, difficulty, zone, game_dir)
        usable = [o for o in opts
                  if not o.harmful and (o.item is None or inventory.get(o.item, 0) > 0)]
        # No safe way to open it is a SKIP, never a bare-handed shrug. The
        # Old Road's trapped chest is `Blight 1` by hand and loot table `T`
        # with a key, so "open it anyway" is the one answer that is wrong at
        # both ends.
        options_by_key[event.key] = {o.item: o for o in usable}
        if not usable:
            continue
        light = torch_when(event)
        choices.append((event.key, [
            (o.item, option_worth(o, light, difficulty, zone, crest_gold, game_dir))
            for o in usable]))

    assignment = assign_items(choices, inventory)

    # 4. Walk it, holding the floor where the pack allows and snuffing at the
    #    last planned fight.
    last_fight_area = snuff_at

    stops: list[Stop] = []
    total = LootValue()
    torches = int(inventory.get("torch", 0) or 0)
    spent, dark = 0, []
    level = float(torch)
    snuffed = False

    def burn(area_id: str) -> None:
        nonlocal level
        loss = walk_costs(level, game_dir).light_loss
        level = max(SHUTDOWN_LEVEL, level - loss * _tiles(dungeon, area_id))

    seen_areas = set(outbound[:1 + len(corridor_prefix)])
    relit_again = dark_again = 0
    # **An event is resolved the FIRST time the party stands on it.** An exact
    # cover of a branching map doubles back through areas it has already
    # cleared, and without this the plan opened one crate twice and counted
    # the loot twice with it.
    done: set[tuple] = set()

    def note_entry(area_id: str) -> None:
        nonlocal relit_again, dark_again
        if area_id in seen_areas and area_id in dungeon.corridors:
            if level > SHUTDOWN_LEVEL:
                relit_again += 1
            else:
                dark_again += 1
        seen_areas.add(area_id)

    # **Index 0 is the area the party is STANDING IN, and its events are the
    # ones due now.** Starting the walk at `[1:]` skipped them entirely, so a
    # party in a corridor was never told to fight the battle beside it or to
    # defer the curio under its feet -- and the deferral is the whole point of
    # the layer. Live on the Old Road it opened the tent at torch 94 for a
    # third of its value, because no `defer` stop existed for `run.py` to see.
    #
    # It hid behind the entrance being empty: from `rooA` there was nothing to
    # lose, and every offline test planned from a room.
    # **WITH NOTHING LEFT TO FIGHT, THE DARK IS FREE WHERE THE PARTY STANDS.**
    # The only reason to hold a torch is the risk it buys off on the fights
    # still ahead -- that is exactly what `torchlight` prices, and it is what
    # makes the pre-killing-blow snuff safe rather than reckless. A plan
    # holding no battle at all has nothing to buy off, so waiting to go dark
    # costs the entire walk it waits through and buys nothing.
    #
    # Live 2026-09-06 that was an infinite loop rather than an inefficiency.
    # Both Old Road fights done, objective met, the tent underfoot in `coAB`:
    # the plan DEFERRED the tent -- which mutes the curio layer by design,
    # since a deferral must not arm `curio_action` -- and put the snuff at the
    # far end of a walk to `rooB` that existed for no other reason than to be
    # somewhere else while going dark. The route layer said `rooA`, so the
    # party walked there instead, re-planned, deferred again, and crossed the
    # same corridor until the run was killed.
    #
    # Snuffing here collapses all of it: every curio is then priced dark
    # already, so nothing is deferred, so no revisit is manufactured -- and
    # the tent is taken where the party is standing, at torch 0, for the very
    # value the deferral was chasing.
    if (not battle_at and level > SHUTDOWN_LEVEL
            and any(e.kind == "curio" and e.key not in done for e in events)):
        stops.append(Stop(area=outbound[0], action="snuff",
                          torch=SHUTDOWN_LEVEL,
                          why="nothing left to fight, so the dark costs "
                              "nothing here and pays on everything still "
                              "standing"))
        snuffed = True
        level = SHUTDOWN_LEVEL

    for index, area in enumerate(outbound):
        if index:
            burn(area)
            note_entry(area)
            stops.append(Stop(area=area, action="walk", torch=level))
        here_events = [e for e in events
                       if e.area == area and e.key not in done]
        # **Top up on ARRIVAL while any fight is still ahead, not on the tile
        # the fight is on.** `tile_light_loss` burns 1 per tile above 75 and
        # SIX below it, so a torch bought before the collapse buys 25 tiles of
        # walking and the same torch bought after it buys four. Waiting until
        # the fight also means the corridor before it was walked dark, which
        # is a `corridor_return_content` roll at the raised rate for nothing.
        ahead = [e for e in events
                 if e.kind == "battle" and e.key not in done]
        if ahead and not snuffed and level < torch_floor:
            need = 0
            while level + need * TORCH_RESTORE < torch_floor and need < torches:
                need += 1
            # Only when it actually reaches the floor. Half a relight leaves
            # the party in the same bracket, still burning at six a tile, and
            # spends the torches that a later fight could have used properly.
            if level + need * TORCH_RESTORE >= torch_floor and need:
                torches -= need
                spent += need
                level = min(100.0, level + need * TORCH_RESTORE)
                stops.append(Stop(area=area, action="relight", torch=level,
                                  why=f"{need} torch(es): "
                                      f"{len(ahead)} fight(s) still ahead and "
                                      f"the light was about to cost 6 a tile"))
        for event in sorted(here_events, key=lambda e: (e.tile, e.kind)):
            # A DEFER is not a resolution -- marking it done here is what made
            # the walk home skip everything it had gone back for.
            if event.key not in deferred:
                done.add(event.key)
            if event.kind == "battle":
                kill_light = level
                if area == last_fight_area and not snuffed:
                    if level > SHUTDOWN_LEVEL:
                        # Only worth an instruction when there is light to put
                        # out. A party already walked into the dark has
                        # nothing to snuff, and printing SNUFF there reads as
                        # if the plan chose it.
                        stops.append(Stop(
                            area=area, action="snuff", tile=event.tile,
                            torch=SHUTDOWN_LEVEL,
                            why="the last planned fight -- snuff before the "
                                "killing blow, when the fight is already "
                                "decided"))
                    kill_light = SHUTDOWN_LEVEL
                    snuffed = True
                    level = SHUTDOWN_LEVEL
                elif level < torch_floor:
                    dark.append(f"{area} tile{event.tile}")
                gain = battle_value_at(kill_light, difficulty, zone, game_dir)
                total = total + gain
                stops.append(Stop(area=area, action="fight", tile=event.tile,
                                  torch=kill_light, value=gain,
                                  why="guarding a curio" if event.guarded else ""))
                continue
            if event.key in deferred:
                stops.append(Stop(area=area, action="defer", tile=event.tile,
                                  torch=level, curio_id=event.curio_id,
                                  why="worth more in the dark, and the way "
                                      "home passes here anyway"))
                continue
            item = assignment.items.get(event.key)
            option = options_by_key.get(event.key, {}).get(item)
            if option is None:
                stops.append(Stop(area=area, action="skip", tile=event.tile,
                                  torch=level, curio_id=event.curio_id,
                                  why="every outcome is net negative"))
                continue
            gain = value_at(option, level, difficulty, zone, game_dir)
            total = total + gain
            stops.append(Stop(area=area, action="take", tile=event.tile,
                              torch=level, item=item or "", value=gain,
                              curio_id=event.curio_id, why=option.why))

    # 5. Home, in the dark, collecting what was deferred.
    if not snuffed and deferred:
        stops.append(Stop(area=outbound[-1], action="snuff",
                          torch=SHUTDOWN_LEVEL,
                          why="nothing left to fight -- the dark is free from "
                              "here and it pays on everything still standing"))
        snuffed = True
        level = SHUTDOWN_LEVEL
    for area in homeward[1:] if len(homeward) > 1 else []:
        note_entry(area)
        stops.append(Stop(area=area, action="walk", torch=level))
        # **Anything still standing here, not just what was deferred.** An
        # area the outbound pass never reached has nothing to defer -- there
        # was no "now" to weigh against -- but the walk home goes through it
        # all the same, and leaving the loot there is simply losing it. Live
        # case: a party in `rooB` with the tent still unclaimed in `coAB`
        # planned a run worth 454 instead of 1186.
        for event in sorted((e for e in events if e.area == area
                             and e.kind == "curio" and e.key not in done),
                            key=lambda e: e.tile):
            done.add(event.key)
            item = assignment.items.get(event.key)
            option = options_by_key.get(event.key, {}).get(item)
            if option is None:
                continue
            gain = value_at(option, level, difficulty, zone, game_dir)
            total = total + gain
            stops.append(Stop(area=area, action="take", tile=event.tile,
                              torch=level, item=item or "",
                              curio_id=event.curio_id, value=gain,
                              why=option.why))
    # **A DEFERRAL IS A PROMISE, AND A PLAN MUST NOT MAKE ONE IT CANNOT KEEP.**
    # `defer` means "not at this light" and is only honest while a later stop
    # comes back for it -- that is the whole of what separates it from `skip`,
    # which means never. The collector above walks `homeward[1:]`, so when the
    # party is ALREADY STANDING in the last corridor there is no later stop:
    # `outbound` ends where it stands and the walk home starts past it.
    #
    # Live 2026-09-06. Objective met, the party in `coAB` with the tutorial
    # tent under its feet and `rooA` one step away; the plan deferred, the
    # actuator dutifully walked past "COMING BACK FOR travellers_tent_tutorial"
    # and the raid ended 558 gold poorer. What makes it hard to see is that
    # the deferral was CORRECT when it was first made, one area back -- what
    # expired is the return leg that justified it, and nothing re-examined the
    # promise once the route under it changed.
    #
    # So this is a self-consistency pass and not a scheduling rule: every
    # `defer` either has a `take` for the same tile later in the plan, or it
    # was never a deferral and is re-priced at the light it will really see.
    collected = {(s.area, s.tile) for s in stops if s.action == "take"}
    keyed = {(e.area, e.tile): e.key for e in events if e.kind == "curio"}
    repaired: list[Stop] = []
    for stop in stops:
        if stop.action != "defer" or (stop.area, stop.tile) in collected:
            repaired.append(stop)
            continue
        key = keyed.get((stop.area, stop.tile))
        item = assignment.items.get(key) if key is not None else None
        option = (options_by_key.get(key, {}) or {}).get(item)
        if option is None:
            repaired.append(replace(
                stop, action="skip",
                why="deferred, but nothing comes back this way and there is "
                    "no safe way to open it here"))
            continue
        gain = value_at(option, stop.torch, difficulty, zone, game_dir)
        total = total + gain
        repaired.append(replace(
            stop, action="take", item=item or "", value=gain,
            why=f"{option.why} -- deferred, but the walk home does not pass "
                f"here again, so it is now or never"))
    stops = repaired

    stops.append(Stop(area=home, action="finish", torch=level,
                      why="hand the quest in at the entrance"))

    note = ""
    if not assignment.exact:
        note = ("the pack was spread greedily, not exactly -- too many item "
                "types to search")
    return LootPlan(stops=tuple(stops), walk=tuple(outbound + homeward[1:]),
                    value=total, torches_spent=spent, torches_left=torches,
                    dark_battles=tuple(dark), relit_reentries=relit_again,
                    dark_reentries=dark_again, note=note)


def main() -> None:                                          # pragma: no cover
    import argparse

    from bridge.dungeon_map import read_dungeon_map
    from bridge.route import read_position, read_quest

    ap = argparse.ArgumentParser(description="route, torch and pack together")
    ap.add_argument("map_file", help="a persist.map.json (a COPY, never live)")
    ap.add_argument("--raid", default=None)
    ap.add_argument("--from", dest="start", default=None)
    ap.add_argument("--torch", type=float, default=None)
    ap.add_argument("--have", default="", metavar="ITEMS",
                    help="provisions carried, e.g. torch=4,skeleton_key=2")
    ap.add_argument("--torch-floor", type=float, default=TORCH_FLOOR)
    ap.add_argument("--crest-gold", type=float, default=None)
    ap.add_argument("--game-dir", default=str(GAME_DIR))
    args = ap.parse_args()

    dungeon = read_dungeon_map(args.map_file)
    if dungeon is None:
        print("no dungeon map in that file (in town?)")
        return

    raid = args.raid or str(args.map_file).replace("map.json", "raid.json")
    quest = position = None
    if Path(raid).exists():
        quest = read_quest(raid)
        position = read_position(raid)

    inventory: dict[str, int] = {}
    for part in args.have.split(","):
        if "=" in part:
            name, _, count = part.partition("=")
            inventory[name.strip()] = int(count)

    if quest is not None:
        print(f"quest: {quest.quest_type} in {quest.dungeon} "
              f"(difficulty {quest.difficulty})  "
              f"goals={'/'.join(quest.goals) or 'unstated'}")
    plan = plan_loot(dungeon, quest, args.start or position,
                     torch=100.0 if args.torch is None else args.torch,
                     inventory=inventory, torch_floor=args.torch_floor,
                     crest_gold=args.crest_gold, game_dir=args.game_dir)
    print(plan.report(args.crest_gold))


if __name__ == "__main__":                                   # pragma: no cover
    main()
