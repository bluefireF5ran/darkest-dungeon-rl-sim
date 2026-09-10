"""What to do in the Hamlet, as structure. Decides; never clicks.

`bridge/town.py` is the WALKER -- a table from a recognised screen to the one
button that advances it. This is the layer beside `route.py`, `curios.py`,
`torchlight.py` and `camp.py`: it answers *what should happen in town*, and
`bridge/act.py` is the only thing that turns an answer into input. Same split,
same reason -- a decision that can be replayed offline is a decision that can be
tested, and `--replay` is what has caught most of the bridge's real bugs.

Three decisions, in the order a town visit has to make them:

1. **Trade heirlooms, if an upgrade needs it.** `data/heirlooms.py` solves it.
2. **Buy building upgrades**, cheapest-path-first down a declared priority.
3. **Recruit**, toward a curated comp SHAPE rather than toward a wish list.

## Why recruiting is a SHAPE question

The tutorial leaves a two-hero party, so the first decision every campaign
faces is which two classes complete something real. "Take the highest resolve"
and "take one of each" are both plausible and both wrong: what makes a party
work here is the class multiset and the formation, which is the same thing
`tools/comp_ga.py`'s stage 1 ranks and the same reason `archetype` is a comp's
identity. So the score is **how many curated comps the roster could field**,
and `embark.candidate_shapes` already enumerates exactly those -- it is what
`bridge/embark.py` builds its parties from, so recruiting and embarking cannot
drift apart.

**The SET is chosen, not the hires one at a time.** The coach shows a handful,
so every combination of the size the roster has room for is enumerable --
`C(6,2)` is fifteen -- and the exact answer is simply cheaper than arguing
about a heuristic. It also matters: greedy is wrong here in a way that is easy
to miss, because taking the single best hire and then the best remaining one
can leave a pair that completes nothing, while a different pair completes a
comp outright. A comp is a class MULTISET, so hires interact; scoring them
independently is the same mistake as ranking comps by their best hero.

Greedy remains as the fallback for an implausibly long offer list, and the
report says which was used.

**A tie breaks toward the better hero, and "better" is resolve then gear.** The
coach's offers carry full records in the save (`town_state.read_recruits`), so
this is a data decision and needs no screen at all.
"""
from __future__ import annotations

import sys
from collections import Counter
from dataclasses import dataclass, field
from itertools import combinations
from math import comb
from pathlib import Path
from typing import Iterable, Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bridge.town_state import Recruit, RosterHero, TownState
from data.town_costs import load_stress_treatments
from data.heirlooms import (UpgradeNode, crest_values, load_building_upgrades,
                            load_rates, next_nodes, plan_purchases,
                            read_purchased, solve_exchange)

from data.paths import GAME_DIR

# What to buy, in order. A POLICY rather than a fact about the game, so it is
# a constant a caller can replace and not a rule buried in a branch.
#
# Fran's instruction is "a few levels on the top path" of the stage coach, and
# the top path is `numrecruits` -- more heroes offered per week, which is what
# a campaign holding a two-hero party is short of.
#
# **`rostersize` was off this path until 2026-09-01, and the evidence that put
# it back is the WEEK SKIP.** The old reason was real -- it costs `2 bust -> 3
# crest` and `2 portrait -> 3 deed`, nine crest-equivalent of trades on week
# one -- and it rested on "a bigger roster is worth nothing while the roster
# is nearly empty". The post-tutorial procedure makes that false, and
# `stage_coach.building.json` states both halves outright:
#
#     number_of_recruits_upgrades   2 -> a:3 -> b:4 -> ... -> e:7
#     roster_size_upgrades          9 -> a:12 -> b:16 -> ... -> e:28
#
# So the two top-path ranks are what make the coach offer **four** heroes,
# which is the "recruit all 4" the procedure is written around; and the mid
# path is what raises the roster from **9 to 12**, which is what holds the two
# tutorial heroes plus two skips' worth of hires -- **ten**. Bought in the
# other order, or not at all, the last hire of the second week has nowhere to
# go. Both are therefore derived from shipped data rather than chosen.
#
# The wallet still runs out exactly where it did: after these three the
# shipped starting heirlooms are down to (10, 8, 0, 2) and `rostersize.b`
# is unreachable, so `MAX_RANKS_PER_VISIT` is not what bounds this.
#
# `upgraded_recruits` is absent for a harder reason: it requires guild and
# blacksmith ranks the first weeks cannot pay for, so `next_nodes` refuses it
# and listing it would put an unbuyable line in every town report.
UPGRADE_PRIORITY = (
    ("stage_coach", "stage_coach.numrecruits"),
    ("stage_coach", "stage_coach.rostersize"),
)

# The pre-2026-09-01 path, kept named so an arm can be run against it and so
# the paragraph above stays attached to the thing it is about.
UPGRADE_PRIORITY_TOP_ONLY = UPGRADE_PRIORITY[:1]
# The old name for what is now the default. Kept so callers and tests that
# named it explicitly still mean the same list.
UPGRADE_PRIORITY_WITH_ROSTER = UPGRADE_PRIORITY

# How many ranks of one tree to take in a single visit. Two is Fran's "a few
# levels", and it is also where the shipped starting wallet runs out: rank `c`
# costs 16 deed against the 2 left after `b`.
MAX_RANKS_PER_VISIT = 2


@dataclass(frozen=True)
class Purchase:
    """One upgrade to buy, with the trades that pay for it."""
    node: UpgradeNode
    trades: tuple = ()

    def label(self) -> str:
        via = ("  via " + "; ".join(t.label() for t in self.trades)
               if self.trades else "")
        return f"{self.node.label()}{via}"


#: Treat a hero above this much stress and not below it.
#:
#: THE ONE CONVENTION IN THIS FILE'S STRESS HALF, and it is argued rather than
#: picked. 50 is half the Resolve Check at 100. The cheapest slot removes 45
#: stress for 1000 gold, so treating a hero at 20 throws away more than half of
#: what it buys, and that hero is nowhere near an affliction. Above 50 the slot
#: is nearly all used and the hero is within one bad raid of breaking.
#:
#: Falsifier: if a treated hero's stress is routinely seen dropping to 0 with
#: heal left over, this is too low.
TREAT_ABOVE = 50.0


@dataclass(frozen=True)
class Treatment:
    """One hero into one stress activity."""
    hero_name: str
    hero_class: str
    building: str
    activity: str
    cost: int
    stress_before: float
    heals: float

    def label(self) -> str:
        after = max(0.0, self.stress_before - self.heals)
        return (f"{self.hero_name} ({self.hero_class}) -> {self.building}/"
                f"{self.activity}  {self.cost:,}g  "
                f"stress {self.stress_before:.0f} -> {after:.0f}")


@dataclass(frozen=True)
class Hire:
    """One recruit to take, with the reason, and where it is on the panel.

    `index` is the recruit's PANEL ROW (`Recruit.row`), so
    `townlayout.recruit_slot(index)` is the point to drag from. It is
    deliberately not the position in `TownState.recruits`: that list pools
    both of the coach's stores, and the column draws only one of them.
    """
    recruit: Recruit
    index: int
    opens: int = 0                 # curated comps this hire makes fieldable
    why: str = ""

    def label(self) -> str:
        return f"{self.recruit.label()}  [{self.why}]"


@dataclass(frozen=True)
class TownAdvice:
    """Structure, never text. Same rule as `RoomAdvice`.

    An actuator keyed on a printed line breaks silently the first time a label
    changes -- and in town that means clicking a building it did not mean to
    open.
    """
    purchases: tuple[Purchase, ...] = ()
    hires: tuple[Hire, ...] = ()
    treatments: tuple[Treatment, ...] = ()
    wallet_after: dict = field(default_factory=dict)
    notes: tuple[str, ...] = ()

    @property
    def empty(self) -> bool:
        return not (self.purchases or self.hires or self.treatments)

    def lines(self) -> list[str]:
        out = []
        for p in self.purchases:
            out.append(f"  BUY  {p.label()}")
        for h in self.hires:
            out.append(f"  HIRE {h.label()}")
        for t in self.treatments:
            out.append(f"  TREAT {t.label()}")
        out.extend(f"  ({n})" for n in self.notes)
        return out or ["  nothing to do in town"]


# -- recruiting -----------------------------------------------------------

def _roster_classes(town: TownState) -> Counter:
    return Counter(h.hero_class for h in town.heroes)


def missing_for(shape: Sequence[str], have: Counter) -> int:
    """How many hires the roster still needs before it can field this shape.

    **Multiset arithmetic, not set arithmetic.** "Ascetic Movement" is three
    distinct Crusaders and a Flagellant, so one Crusader leaves it two short,
    not zero. A set test would call it fieldable -- the same bug the CSV's
    deduplication caused, and the reason `comp_loadouts` exists.
    """
    want = Counter(shape)
    return sum(max(0, n - have.get(cls, 0)) for cls, n in want.items())


def fieldable(shapes: Iterable[Sequence[str]], have: Counter) -> int:
    """How many of `shapes` the roster could field RIGHT NOW."""
    return sum(1 for shape in shapes if missing_for(shape, have) == 0)


def reachable(shapes: Iterable[Sequence[str]], have: Counter, budget: int) -> int:
    """How many shapes are within `budget` more hires.

    **This, and not `fieldable`, is what a hire is scored against**, because
    `fieldable` has no gradient where the decision actually happens. The
    tutorial leaves a TWO-hero roster; no four-hero comp is fieldable from it,
    so every possible hire scores exactly zero on `fieldable` and the choice
    collapses onto the tie-break. Measured: all 179 curated shapes score 0.

    With a two-hire budget the same roster has **9 shapes within reach**, and
    the classes that close the distance are exactly the ones a
    Crusader/Highwayman core wants -- Vestal, Plague Doctor, Occultist,
    Man-at-Arms, Houndmaster and Grave Robber all tie at the top, and the
    tie-break then picks among them on merit. A metric that only turns on at
    the finish line cannot steer the two steps that reach it.
    """
    return sum(1 for shape in shapes if missing_for(shape, have) <= budget)


def distance(shapes: Iterable[Sequence[str]], have: Counter) -> int:
    """Total hires between this roster and EVERY shape, summed.

    The tie-break under `reachable`, and it is needed because `reachable`
    flattens too: the last hire of a visit is scored at a budget of zero,
    where `reachable` collapses back into `fieldable` and every candidate
    scores nothing again. Measured on the tutorial roster -- after taking the
    Vestal, no single fourth hire completes a curated comp, so the second pick
    fell straight through to hero merit and took a Leper.

    Reducing this is "gets closer", and a hire reduces it by one for every
    shape that still wants that class. So the tie-break is effectively **the
    class the most curated comps are still short of**, which is a rule that
    can be read off the log and argued with. It is deliberately only a
    tie-break: on its own it would prefer whatever class is commonest in the
    corpus (Houndmaster fills 96 hero slots against Duelist's 6), which is the
    convergence `--rarity-bias` exists to fight in the comp search.
    """
    return sum(missing_for(shape, have) for shape in shapes)


def _recruit_quality(recruit: Recruit) -> tuple:
    """Tie-break only. Resolve first, then gear, then a stable key.

    Never the primary criterion: a resolve-3 Jester the roster cannot build a
    comp around is worth less than a resolve-0 Vestal that completes seven,
    and ranking on quality is exactly how a roster ends up as six strong
    classes that do not play together.
    """
    return (recruit.resolve_xp,
            recruit.weapon_rank + recruit.armour_rank,
            -recruit.slot)


# Above this many combinations the exact search is abandoned for the greedy
# one. The coach shows four to six, so `C(6,2)` = 15 and this is never reached
# in practice -- it exists so a modded coach with thirty offers degrades
# instead of hanging.
MAX_COMBINATIONS = 20000


def _score_roster(shapes, have: Counter) -> tuple[int, int]:
    """How good a finished roster is: comps it can field, then how close it is
    to the rest. Higher is better on both, so `distance` is negated."""
    if not shapes:
        return (0, 0)
    return (fieldable(shapes, have), -distance(shapes, have))


# The coach's two stores, and the one a drag can pay for.
GOLD_RECRUIT_LISTING = "hero_recruit"


def choose_recruits(town: TownState, shapes, limit: int = 2) -> list[Hire]:
    """Which of the coach's offers to take, best first.

    Greedy and re-scored after each pick, so the second hire is chosen knowing
    the first was made -- two Houndmasters can each open comps alone and open
    nothing extra together, and scoring them independently would take both.

    `limit` is how many the roster has room for; the caller knows that and
    this does not. It is also the LOOKAHEAD BUDGET -- see `reachable` -- so a
    two-slot visit scores each hire by what two hires together can reach,
    which is the only way the choice has any gradient at all from a two-hero
    roster.

    A hire that brings nothing closer is still returned when there is room,
    with `opens=0` and a reason saying so: an empty roster slot is worth less
    than a body in it, and the log should say which case it was.
    """
    # **Only the gold store is hireable, and that is a currency fact rather
    # than a preference.** `read_recruits` pools `hero_recruit` with Color of
    # Madness's `shard_hero_recruit`, which is bought with SHARDS -- a
    # currency nothing here spends and a fresh campaign has none of. On
    # 2026-09-01 a live save held one of the first and four of the second, the
    # panel drew one row, and two hires dragged from empty space below it. The
    # index carried through to the drag is therefore `r.row`, the position
    # within the drawn listing.
    offers = [(r.row, r) for r in town.recruits
              if r.hero_class and r.listing == GOLD_RECRUIT_LISTING]
    have = _roster_classes(town)
    shapes = list(shapes)
    take = min(limit, len(offers))
    if take <= 0:
        return []

    if shapes and comb(len(offers), take) <= MAX_COMBINATIONS:
        return _best_combination(offers, have, shapes, take)
    return _greedy(offers, have, shapes, limit)


def _best_combination(offers, have: Counter, shapes, take: int) -> list["Hire"]:
    """Exact: the set of `take` offers that leaves the best roster.

    Ties break on the hires' own merit, summed, so two sets that leave the
    roster in the same shape are separated by who the heroes actually are --
    the same ordering `_recruit_quality` gives, and never the primary
    criterion.
    """
    best = None
    for picks in combinations(offers, take):
        after = Counter(have)
        for _i, recruit in picks:
            after[recruit.hero_class] += 1
        merit = tuple(sorted((_recruit_quality(r) for _i, r in picks),
                             reverse=True))
        key = (_score_roster(shapes, after), merit)
        if best is None or key > best[0]:
            best = (key, picks, after)

    (score, _merit), picks, after = best
    fielded, neg_dist = score
    base_fielded, base_neg_dist = _score_roster(shapes, have)

    out = []
    running = Counter(have)
    for index, recruit in picks:
        step = Counter(running)
        step[recruit.hero_class] += 1
        opened = fieldable(shapes, step) - fieldable(shapes, running)
        wanted = distance(shapes, running) - distance(shapes, step)
        if opened:
            plural = "s" if opened != 1 else ""
            why = f"completes {opened} curated comp{plural}"
        elif wanted:
            why = (f"part of the best pair; {wanted} curated comps "
                   f"still want one")
        else:
            why = f"no comp wants one; on merit (resolve xp {recruit.resolve_xp})"
        out.append(Hire(recruit=recruit, index=index, opens=opened, why=why))
        running = step

    gained = fielded - base_fielded
    if out and gained:
        plural = "s" if gained != 1 else ""
        out[0] = Hire(recruit=out[0].recruit, index=out[0].index,
                      opens=out[0].opens,
                      why=f"{out[0].why} (the set opens {gained} comp{plural})")
    return out


def _greedy(offers, have: Counter, shapes, limit: int) -> list["Hire"]:
    """Fallback for an offer list too long to enumerate. See `MAX_COMBINATIONS`."""
    remaining = list(offers)
    have = Counter(have)
    out: list[Hire] = []

    while remaining and len(out) < limit:
        # The budget AFTER this hire. Every candidate is scored at the same
        # remaining budget, which is what makes their numbers comparable:
        # `reachable` shrinks as the budget does, so mixing budgets would rank
        # the order of evaluation rather than the hires.
        left = limit - len(out) - 1
        before = reachable(shapes, have, left) if shapes else 0
        before_dist = distance(shapes, have) if shapes else 0

        scored = []
        for index, recruit in remaining:
            after = Counter(have)
            after[recruit.hero_class] += 1
            if shapes:
                gain = reachable(shapes, after, left) - before
                closer = before_dist - distance(shapes, after)
            else:
                gain = closer = 0
            scored.append((gain, closer, _recruit_quality(recruit),
                           index, recruit))
        scored.sort(key=lambda s: (s[0], s[1], s[2]), reverse=True)
        gain, closer, _quality, index, recruit = scored[0]

        if not shapes:
            why = f"no comp shapes loaded; on merit (resolve xp {recruit.resolve_xp})"
        elif gain:
            plural = "s" if gain != 1 else ""
            more = f"{left} more hire{'s' if left != 1 else ''}"
            why = f"brings {gain} curated comp{plural} within {more}"
        elif closer:
            why = f"completes no comp yet; {closer} curated comps still want one"
        else:
            why = f"no comp wants one; on merit (resolve xp {recruit.resolve_xp})"
        out.append(Hire(recruit=recruit, index=index, opens=gain, why=why))
        have[recruit.hero_class] += 1
        remaining = [(i, r) for i, r in remaining if i != index]

    return out


# -- upgrades -------------------------------------------------------------

def choose_treatments(town: TownState, game_dir: Path = GAME_DIR,
                      tier: int = 0, budget: int | None = None,
                      threshold: float = TREAT_ABOVE) -> list[Treatment]:
    """Who to put in the Abbey and the Tavern this week.

    Ordered by stress DESCENDING: the hero nearest the Resolve Check is the one
    an affliction is about to cost, and `--affliction-penalty` measured
    afflictions as largely unpreventable once a raid is under way -- so the
    town is where they are actually prevented.

    Two exclusions, both read off the save rather than assumed:

    * a hero already in a building is being treated; `RosterHero.building` is
      non-empty exactly then;
    * a hero marked for the party is embarking, and a hero in a building
      cannot embark. Treating one would silently cancel the raid it was
      selected for.

    Cheapest activity first, so the gold buys the most heroes rather than the
    most stress. That is the right way round while slots are scarce: an
    untreated hero at 90 is a coming affliction, and a treated one at 45 is
    not, so covering two heroes beats over-healing one.
    """
    treatments = load_stress_treatments(str(game_dir))
    if not treatments:
        return []
    slots = [(a, i) for a in treatments for i in range(a.slots_at(tier))]
    slots.sort(key=lambda pair: pair[0].cost_at(tier))

    gold = budget if budget is not None else int(town.wallet.get("gold", 0))
    candidates = [h for h in town.heroes
                  if not h.building and not h.in_party
                  and float(getattr(h.hero, "stress", 0.0)) > threshold]
    candidates.sort(key=lambda h: -float(getattr(h.hero, "stress", 0.0)))

    out: list[Treatment] = []
    for hero, (activity, _slot) in zip(candidates, slots):
        cost = activity.cost_at(tier)
        if cost > gold:
            break
        gold -= cost
        out.append(Treatment(
            hero_name=getattr(hero.hero, "name", "") or getattr(hero.hero, "hero_class", ""),
            hero_class=getattr(hero.hero, "hero_class", ""),
            building=activity.building, activity=activity.activity,
            cost=cost, stress_before=float(getattr(hero.hero, "stress", 0.0)),
            heals=activity.heal_at(tier)))
    return out


def choose_upgrades(wallet: dict, purchased: set, game_dir: Path = GAME_DIR,
                    priority=UPGRADE_PRIORITY,
                    max_ranks: int = MAX_RANKS_PER_VISIT):
    """Walk `priority` and buy what the wallet reaches, trading when short.

    Returns `([Purchase], wallet_after)`.

    **Ranks are taken in file order and prerequisites are re-checked as they
    are bought**, because `b` is only reachable once `a` is owned -- which is
    also why this cannot be a set-difference against `next_nodes`: that answers
    "what is open now", and buying `a` opens `b` within the same visit.
    """
    nodes_by_building = {}
    chosen: list[UpgradeNode] = []
    owned = set(purchased)

    for building, tree_id in priority:
        if building not in nodes_by_building:
            nodes_by_building[building] = load_building_upgrades(str(game_dir),
                                                                 building)
        all_nodes = nodes_by_building[building]
        taken = 0
        while taken < max_ranks:
            openable = [n for n in next_nodes(all_nodes, owned)
                        if n.tree_id == tree_id]
            if not openable:
                break
            node = openable[0]
            chosen.append(node)
            owned.add(node.key)
            taken += 1

    rates = load_rates(str(game_dir))
    bought, left = plan_purchases(wallet, chosen, rates)
    return [Purchase(node=n, trades=tuple(t)) for n, t in bought], left


# -- how many heroes the coach is ALLOWED to add --------------------------

def roster_size_tiers(game_dir: Path = GAME_DIR) -> tuple[int, ...]:
    """The roster cap per `stage_coach.rostersize` rank, from shipped data.

    `roster_size_upgrades` in the coach's building file: 9 with nothing
    bought, then 12 / 16 / 20 / 24 / 28. Derived rather than listed, for the
    same reason every other number here is -- a DLC or a patch that adds a
    rank should move this on its own.
    """
    import json

    path = (Path(game_dir) / "campaign" / "town" / "buildings" /
            "stage_coach" / "stage_coach.building.json")
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:                                      # noqa: BLE001
        return ()

    def _find(node):
        if isinstance(node, dict):
            if "roster_size_upgrades" in node:
                return node["roster_size_upgrades"]
            for value in node.values():
                got = _find(value)
                if got:
                    return got
        elif isinstance(node, list):
            for value in node:
                got = _find(value)
                if got:
                    return got
        return None

    rows = _find(data) or []
    out = []
    for row in rows:
        try:
            out.append(int(row["amount"]))
        except Exception:                                  # noqa: BLE001
            continue
    return tuple(out)


def roster_cap(upgrades_file=None, game_dir: Path = GAME_DIR):
    """How many heroes this campaign may hold, or None when it cannot be read.

    None is a real answer and it does NOT block hiring: not knowing the cap
    must not starve a roster that has room. What blocks is a cap that is known
    and already met.
    """
    tiers = roster_size_tiers(game_dir)
    if not tiers or upgrades_file is None:
        return None
    try:
        from data.dson import dd_name_hash
        from data.heirlooms import read_rank_counts
        counts = read_rank_counts(upgrades_file)
    except Exception:                                      # noqa: BLE001
        return None
    wanted = dd_name_hash("stage_coach.rostersize") & 0xFFFFFFFF
    rank = 0
    for (tree_hash, _instance), n in counts.items():
        if tree_hash == wanted:
            rank = max(rank, int(n))
    return tiers[min(rank, len(tiers) - 1)]


# -- the whole visit ------------------------------------------------------

def advise(town: TownState, store=None, upgrades_file=None,
           game_dir: Path = GAME_DIR, roster_room: int = 2) -> TownAdvice:
    """One town visit's plan.

    `store` is optional and only buys the recruiting half: without it there are
    no comp shapes to recruit toward, so hiring falls back to merit order
    rather than guessing. Upgrades need no store at all, which is why a
    store-less caller still gets a useful plan instead of an exception.
    """
    notes: list[str] = []

    all_nodes: list[UpgradeNode] = []
    for building, _tree in UPGRADE_PRIORITY:
        all_nodes.extend(load_building_upgrades(str(game_dir), building))
    purchased = read_purchased(upgrades_file, all_nodes) if upgrades_file else set()

    wallet = dict(town.wallet or {})
    purchases, left = choose_upgrades(wallet, purchased, game_dir)
    if not purchases and wallet:
        notes.append("no affordable upgrade on the priority path")

    shapes = []
    if store is not None:
        from bridge.embark import candidate_shapes
        # Every curated comp's class list. `candidate_shapes` wants a quest to
        # filter by resolve cap; recruiting is about the roster's long-run
        # shape rather than one quest, so the shapes are taken unfiltered.
        shapes = [classes for _name, classes in _all_shapes(store)]
    else:
        notes.append("no store: recruiting by merit, not by comp shape")

    # **THE COACH CANNOT HIRE INTO A FULL ROSTER**, and `roster_room` used to
    # be the constant 2 -- so a campaign 9 heroes OVER its cap planned two
    # hires a week, dragged twice, and got "the roster did not grow" twice.
    # Seen live 2026-09-05 at 25 heroes against a cap of 16. The drags are
    # harmless and the report was honest; what was wrong is planning a thing
    # the game will refuse, every single visit, while `rostersize` upgrades
    # get bought on the theory that they are what is missing.
    cap = roster_cap(upgrades_file, game_dir)
    room = roster_room
    if cap is not None:
        free = cap - len(town.heroes or ())
        room = max(0, min(roster_room, free))
        if room < roster_room:
            notes.append(f"the roster holds {len(town.heroes or ())} of a cap "
                         f"of {cap}, so there is room for {room}")
    else:
        notes.append("could not read the roster cap; hiring as if there is room")
    hires = choose_recruits(town, shapes, limit=room) if room else ()
    # Stress last, and out of what the other two LEAVE. A treated hero is
    # worth having and a hire is worth more: the roster is what makes a comp
    # fieldable at all, and an untreated hero is still playable while a missing
    # class is not. Spending the remainder is also the safe direction -- it can
    # only ever plan fewer treatments than the wallet could afford, never more.
    treatments = choose_treatments(
        town, game_dir, budget=int(left.get("gold", 0)))
    for t in treatments:
        left["gold"] = int(left.get("gold", 0)) - t.cost

    return TownAdvice(purchases=tuple(purchases), hires=tuple(hires),
                      treatments=tuple(treatments),
                      wallet_after=left, notes=tuple(notes))


def _all_shapes(store) -> list[tuple[str, list[str]]]:
    """Every curated comp as `(name, class list)`, unfiltered by quest.

    Mirrors `embark.candidate_shapes`'s resolution rules deliberately rather
    than calling it: that function filters by a quest's resolve cap, and
    recruiting has no quest. What must NOT differ is how a comp's classes are
    read -- positionally off `comp_loadouts`, never the CSV `heroes` field,
    which is alphabetised and collapses duplicate classes.
    """
    out = []
    for name, loadout in (store.comp_loadouts or {}).items():
        classes = [h.hero_id for h in loadout.heroes if h.hero_id in store.heroes]
        if len(classes) == getattr(loadout, "declared_heroes", len(classes)):
            out.append((name, classes))
    return out


def main() -> int:
    import argparse

    from bridge.town_state import read_town_state

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--profile", default=None)
    ap.add_argument("--no-store", action="store_true",
                    help="skip the comp shapes (fast; hires by merit)")
    ap.add_argument("--room", type=int, default=2,
                    help="how many roster slots are free")
    args = ap.parse_args()

    town = read_town_state(args.profile)
    store = None
    if not args.no_store:
        from data.store_cache import load_store_cached
        store = load_store_cached(str(Path(__file__).resolve().parent.parent),
                                  str(GAME_DIR))

    upgrades_file = None
    if args.profile:
        candidate = Path(args.profile) / "persist.upgrades.json"
        if candidate.exists():
            upgrades_file = candidate

    print(f"wallet: {town.wallet}")
    print(f"roster: {len(town.heroes)} heroes, coach offers {len(town.recruits)}")
    advice = advise(town, store=store, upgrades_file=upgrades_file,
                    roster_room=args.room)
    for line in advice.lines():
        print(line)
    if advice.wallet_after:
        print(f"  wallet after: {advice.wallet_after}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
