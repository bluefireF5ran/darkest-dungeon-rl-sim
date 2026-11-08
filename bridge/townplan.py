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
from dataclasses import dataclass, field, replace
from functools import lru_cache
from itertools import combinations
from math import comb
from pathlib import Path
from typing import Iterable, Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bridge import hero_roles
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
# `upgraded_recruits` requires guild and blacksmith ranks the first weeks
# cannot pay for, so `next_nodes` refuses it until they are owned -- and it
# is therefore listed AFTER those trees, not beside its coach siblings.
# **Fran's building order, PLAYBOOK 7.4 (2026-09-23):** the coach first ("fresh
# blood for cheap expeditions, and level 1 onwards come with all skills"),
# then guild and blacksmith ("important for stats when resolve level
# increases"), the sanitarium ("long term value on quirks and removing
# diseases"), the survivalist ("less useful as base version is already
# fine"), and the abbey last. **The tavern never**: it can cost a hero trinkets.
COACH_FIRST_PRIORITY = (
    ("stage_coach", "stage_coach.numrecruits"),
    ("stage_coach", "stage_coach.rostersize"),
    # **Experienced Recruits comes right AFTER its prerequisites** (Fran,
    # 2026-09-25). Shipped data: `upgraded_recruits.a` needs rank b of
    # `guild.skill_levels`, `blacksmith.weapon` AND `blacksmith.armour`, each
    # later rank one more of each. Listed before them it was checked while
    # they were still unowned and skipped every visit; listed after, one
    # visit can buy the chain and then the coach rank it opens.
    ("guild", "guild.skill_levels"),
    ("blacksmith", "blacksmith.weapon"),
    ("blacksmith", "blacksmith.armour"),
    ("stage_coach", "stage_coach.upgraded_recruits"),
    ("guild", "guild.cost"),
    ("blacksmith", "blacksmith.cost"),
    # **The Nomad Wagon's discount, from Fran's own play** (recorded
    # 2026-10-01/04, `archive/recordings/`): every crest he had after the
    # coach went to `nomad_wagon.cost`, rank 3 -> 5 inside two sessions, and
    # the 2026-09-28 audit counted 9 Nomad Wagon ranks in his campaign against
    # 0 in the bridge's. PLAYBOOK 7.4's list does not name the wagon; his
    # hands do. Trinkets are the gear the bridge equips every embark.
    ("nomad_wagon", "nomad_wagon.cost"),
    ("sanitarium", "sanitarium.slots"),
    ("sanitarium", "sanitarium.cost"),
    ("sanitarium", "sanitarium.disease_quirk_cost"),
    ("camping_trainer", "camping_trainer.cost"),
    ("abbey", "abbey.meditation"),
    ("abbey", "abbey.prayer"),
    ("abbey", "abbey.flagellation"),
)
#: **Guild and Blacksmith before the Coach** (Fran, 2026-10-12, on
#: docs/CAMPAIGN_SIM.md E16: 80 simulated campaigns per arm, deaths null, a
#: Champion 2-7 weeks earlier for about one boss fewer -- *"Lets go with smith
#: first, but we need to manage the vanilla roster size, not modded"*). The
#: second half of that is `priority_for`: a FULL vanilla roster puts
#: `rostersize` back in front. Everything else keeps PLAYBOOK 7.4's place.
_SMITH_TREES = ("guild.skill_levels", "blacksmith.weapon", "blacksmith.armour")
UPGRADE_PRIORITY = (tuple(e for e in COACH_FIRST_PRIORITY if e[1] in _SMITH_TREES)
                    + tuple(e for e in COACH_FIRST_PRIORITY if e[1] not in _SMITH_TREES))
ROSTER_SIZE_TREE = ("stage_coach", "stage_coach.rostersize")
#: **A building spends nothing until it opens** (Fran, 2026-10-12: *"On the
#: first weeks not all buildings are available"*). Each `.building.json`
#: ships its gate -- the Guild and the Blacksmith after 3 quests, the Abbey
#: after 2 -- so no upgrade, nurture rank, treatment or skill unlock is
#: planned at a closed one. False is the world before, for an A/B.
#: It also settles PLAYBOOK line 110 against smith first: after the tutorial
#: (1 quest) the Guild and the Blacksmith are shut, so the smith-first walk
#: spends the starting heirlooms at the coach, as the coach-first one did.
BUILDING_GATES = True


def closed(town: TownState, game_dir: Path = GAME_DIR) -> frozenset:
    """The buildings this town cannot use yet (`data.town_costs.closed_buildings`)."""
    if not BUILDING_GATES:
        return frozenset()
    from data.town_costs import closed_buildings                    # noqa: PLC0415
    return closed_buildings(str(game_dir), getattr(town, "quests_finished", None),
                            getattr(town, "highest_dungeon_level", None))


def priority_for(heroes: int, offers: int, cap, priority=None) -> tuple:
    """The walk for this visit: `rostersize` first when the roster cannot
    take every recruit the coach offers (Fran's "hire all", 2026-09-23).

    `cap` None (unreadable) or a modded cap that is never met leaves the
    order alone, which is the "not modded" half of Fran's ruling: on his
    Expanded Roster install the cap is ~1,000 and this never fires.
    """
    priority = UPGRADE_PRIORITY if priority is None else priority
    if cap is None or heroes + offers <= cap or ROSTER_SIZE_TREE not in priority:
        return tuple(priority)
    return (ROSTER_SIZE_TREE,) + tuple(e for e in priority if e != ROSTER_SIZE_TREE)
#: **A service discount stops at its first rank** (PLAYBOOK 4.1, Fran: *"La
#: reduccion de coste de servicios esta bien a nivel 1, pero no merece
#: maximizarla"*; the rest is saved for weapon, armour and skill ranks). Before
#: 2026-10-10 the walk took every discount rank it could reach: in a 40-week
#: simulated campaign `blacksmith.cost` b and c ate the 23 deeds that
#: `blacksmith.weapon.b` (20) needed, and no weapon or armour rank past `a` was
#: ever bought. `nomad_wagon.cost` is NOT here: Fran's own play buys it to rank
#: 5 (recordings 2026-10-01/04), and it costs crests only.
#:
#: **OFF by default: measured null.** 80 simulated campaigns per arm, paired
#: (docs/CAMPAIGN_SIM.md, "heirlooms"): dead +0.3 +- 2.2 / +3.7 +- 1.9, gear
#: gold unchanged -- the Stage Coach, above the Blacksmith in the priority,
#: takes the deeds instead, and gear is short of GOLD, not of building ranks.
#: `1` is the PLAYBOOK reading, for Fran to rule on; `None` is the shipped walk.
SERVICE_DISCOUNT_MAX_RANKS = None
SERVICE_DISCOUNT_TREES = frozenset({
    "guild.cost", "blacksmith.cost", "sanitarium.cost",
    "sanitarium.disease_quirk_cost", "camping_trainer.cost",
})

#: Never upgraded and never used for treatment -- PLAYBOOK 7.4.
NEVER_BUILDINGS = frozenset({"tavern"})

# The pre-2026-09-01 path, kept named so an arm can be run against it and so
# the paragraph above stays attached to the thing it is about.
#: The order as shipped, for an experiment that reorders `UPGRADE_PRIORITY`
#: (`campaign.run --coach-first` restores `COACH_FIRST_PRIORITY`) to build from.
SHIPPED_UPGRADE_PRIORITY = UPGRADE_PRIORITY
UPGRADE_PRIORITY_TOP_ONLY = COACH_FIRST_PRIORITY[:1]
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
#
# **Raised to 70 by Fran, PLAYBOOK 7.3 (2026-09-23):** heroes recover stress in
# the Hamlet passively, so treating for 20 stress is waste; treat only above
# about 70, only a hero worth keeping, and only with money to spare.
TREAT_ABOVE = 70.0


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
    #: Roster guid, so the town layer can find the hero's row.
    guid: int = 0
    #: How many of the activity's slots are unlocked (leftmost first).
    slots: int = 1

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
    #: A roster hero this hire REPLACES: the class band was full, and this
    #: recruit beats that band's worst fodder (`_band_room`). Dismissed on the
    #: same visit.
    replaces: Optional[RosterHero] = None

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
    # Heroes worth letting go to make room -- planned, not yet actuated.
    dismissals: tuple[RosterHero, ...] = ()
    #: Ranks for the ONE hero being nurtured this visit (`plan_nurture`).
    nurture: tuple = ()
    #: Sanitarium treatments (`choose_sanitarium`). Planned, not actuated live:
    #: the building's panel is not measured.
    sanitarium: tuple = ()

    @property
    def empty(self) -> bool:
        """Nothing the live town would DO. `sanitarium` is left out: it is
        reported, never actuated, so a visit with only that is a visit with
        nothing to click."""
        return not (self.purchases or self.hires or self.treatments
                    or self.dismissals or self.nurture)

    def lines(self) -> list[str]:
        out = []
        for p in self.purchases:
            out.append(f"  BUY  {p.label()}")
        for h in self.hires:
            out.append(f"  HIRE {h.label()}")
        for t in self.treatments:
            out.append(f"  TREAT {t.label()}")
        for v in self.sanitarium:
            out.append(f"  SANITARIUM {v.label()} (not actuated live)")
        for n in self.nurture:
            out.append(f"  NURTURE {n.label()}")
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
    return recruit_value(recruit) + (recruit.resolve_xp,
                                     recruit.weapon_rank + recruit.armour_rank,
                                     -recruit.slot)


#: Role order for hiring and for the band swap: what a hero is worth keeping.
ROLE_RANK = {"fodder": 0, "nurture": 1, "invested": 2}
#: A fodder recruit replaces a fodder resident only when its quirk score is
#: this much higher -- a dismissal plus a hire is two actions and a churn, so
#: "slightly better" is not worth it. A declared convention.
SWAP_MARGIN = 2.0


def recruit_value(recruit: Recruit, negative=None) -> tuple:
    """`(role rank, nurture score)` for a coach offer -- the same model the
    roster is judged by (`bridge/hero_roles.py`). An Experienced Recruit is
    invested; a level-0 one with a great quirk for its class is nurture."""
    score = hero_roles.nurture_score(recruit.hero_class, recruit.quirks or (),
                                     negative=negative)
    gear = int(recruit.weapon_rank or 0) + int(recruit.armour_rank or 0)
    role = hero_roles.role_of(recruit_level(recruit), 0, score, gear)
    return (ROLE_RANK[role], score)


def resident_value(hr) -> tuple:
    return (ROLE_RANK.get(getattr(hr, "role", "fodder"), 0),
            float(getattr(hr, "nurture", 0.0)))


def beats(recruit_v: tuple, resident_v: tuple) -> bool:
    """Is a recruit worth dismissing this resident for?"""
    if recruit_v[0] != resident_v[0]:
        return recruit_v[0] > resident_v[0]
    return recruit_v[1] >= resident_v[1] + SWAP_MARGIN


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


#: Recruit rows the Stage Coach panel draws without scrolling.
COACH_ROWS_VISIBLE = 7

# The coach's two stores, and the one a drag can pay for.
GOLD_RECRUIT_LISTING = "hero_recruit"


#: Fran, 2026-09-26: *"maximum 4 heroes with the same level and class as a
#: limit, to not overload the hamlet with the same classes"*. A 102-hero
#: roster made every roster lookup a two-minute scroll. Refined the same
#: night into BANDS (0-2 / 3-4 / 5+, caps 4/4/5) whose low cap tightens as
#: the roster levels: `bridge/hero_roles.band_caps`. This is the top cap.
MAX_PER_CLASS_LEVEL = 4


@lru_cache(maxsize=1)
def _thresholds() -> list:
    try:
        from bridge.live_state import load_resolve_thresholds  # noqa: PLC0415
        return load_resolve_thresholds(GAME_DIR)
    except Exception:                                          # noqa: BLE001
        return []


def recruit_level(recruit: Recruit, thresholds=None) -> int:
    """The resolve level a recruit arrives at (Experienced Recruits sends
    some in above 0), from its resolve XP and the shipped thresholds."""
    from bridge.live_state import resolve_level_for_xp        # noqa: PLC0415
    return resolve_level_for_xp(int(recruit.resolve_xp or 0),
                                _thresholds() if thresholds is None else thresholds)


def class_level_counts(town: TownState) -> Counter:
    """Heroes per (class, level BAND)."""
    return Counter((h.hero_class, hero_roles.band_of(h.resolve_level))
                   for h in town.heroes or ())


def band_caps(town: TownState, cap: Optional[int] = None) -> dict:
    """`{band: cap}` for this roster; `cap` overrides every band (tests)."""
    if cap is not None:
        return {b: cap for b in hero_roles.BAND_CAPS}
    return hero_roles.band_caps(hero_roles.mean_level(town))


def _within_class_level_cap(town: TownState, offers, cap: Optional[int] = None):
    """Drop offers that would put a (class, band) over its cap. Where several
    offers compete for the room left, the better recruit is kept."""
    kept, _swaps = _band_room(town, offers, cap)
    return kept


def _band_room(town: TownState, offers, cap: Optional[int] = None, roles=None):
    """Which offers fit the class band caps, and whom each one replaces.

    Fran, 2026-09-27: *"take into account the new hero quality to choose who
    to add to the roster and who to not, specially with the limited 4 class
    threshold slots"*. Best recruit first (`recruit_value`). With room in its
    band it is kept; with the band FULL it is kept only if it `beats` the
    band's worst free fodder, which is then dismissed for it. Returns
    `(kept offers in row order, {row: resident it replaces})`."""
    have = class_level_counts(town)
    caps = band_caps(town, cap)
    thresholds = _thresholds()
    roles = hero_roles.roles(town) if roles is None else roles
    pool: dict = {}
    for h in town.heroes or ():
        hr = roles.get(h.guid)
        if (getattr(hr, "role", "fodder") == "fodder" and h.available
                and not h.in_party):
            key = (h.hero_class, hero_roles.band_of(h.resolve_level))
            pool.setdefault(key, []).append(h)
    for heroes in pool.values():
        heroes.sort(key=lambda h: (resident_value(roles.get(h.guid)), -h.stress))
    keyed = sorted(offers, key=lambda o: _recruit_quality(o[1]), reverse=True)
    kept, taken, swaps = [], Counter(), {}
    for row, r in keyed:
        key = (r.hero_class, hero_roles.band_of(recruit_level(r, thresholds)))
        if have[key] + taken[key] < caps[key[1]]:
            kept.append((row, r))
            taken[key] += 1
            continue
        worst = (pool.get(key) or [None])[0]
        rv = recruit_value(r)
        wv = resident_value(roles.get(worst.guid)) if worst is not None else None
        if worst is not None and beats(rv, wv):
            pool[key].pop(0)
            kept.append((row, r))
            swaps[row] = (worst, rv, wv)
    return sorted(kept, key=lambda o: o[0]), swaps


def cap_dismissals(town: TownState, keep=(), cap: Optional[int] = None,
                   roles: Optional[dict] = None) -> list[RosterHero]:
    """Who goes to bring every (class, band) down to its cap, worst first.

    Only FODDER is dismissed (Fran, 2026-09-26: fodder is "better to dismiss
    and recruit anew"; nurture and invested heroes are what the roster is
    FOR). Within an over-full group the worst fodder goes first: lowest
    nurture score, no trinket before a worn one (nothing to strip), then the
    most stressed. Never a hero in the party, in a building, or in `keep` --
    so a group can stay over the cap rather than lose someone who matters."""
    keep = set(keep)
    caps = band_caps(town, cap)
    roles = hero_roles.roles(town) if roles is None else roles
    groups: dict = {}
    for h in town.heroes or ():
        groups.setdefault((h.hero_class, hero_roles.band_of(h.resolve_level)),
                          []).append(h)
    out = []
    for (_cls, band), heroes in groups.items():
        surplus = len(heroes) - caps[band]
        if surplus <= 0:
            continue
        free = [h for h in heroes
                if h.guid not in keep and h.available and not h.in_party
                and getattr(roles.get(h.guid), "role", "fodder") == "fodder"]
        free.sort(key=lambda h: (getattr(roles.get(h.guid), "nurture", 0.0),
                                 bool(getattr(h.hero, "trinkets", None)),
                                 -h.stress))
        out.extend(free[:surplus])
    return sorted(out, key=lambda h: (h.hero_class, h.resolve_level, -h.stress))


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
    # **Only rows the coach DRAWS.** Seven rows fit the panel (67 px apart
    # from y 147; the list ends near y 600) and the list's scroll is not
    # measured. With 24 offers, a hire from row 7 dragged from below the list
    # and "the roster did not grow" (live 2026-09-27 and -28). Hires only
    # move later rows UP, so a row visible at planning stays reachable.
    offers = [(r.row, r) for r in town.recruits
              if r.hero_class and r.listing == GOLD_RECRUIT_LISTING
              and r.row < COACH_ROWS_VISIBLE]
    offers, swaps = _band_room(town, offers)
    have = _roster_classes(town)
    shapes = list(shapes)
    take = min(limit, len(offers))
    if take <= 0:
        return []

    # **Experienced Recruits first** (Fran, 2026-09-26: "priorize this
    # heroes"). A level-1+ recruit arrives with every skill, skill and gear
    # rank 2 and four camp skills -- about 6,250 gold of Guild and Blacksmith
    # work for free -- so they take the room before any level-0 offer, and
    # the comp-shape search then ranks WITHIN each group.
    thresholds = _thresholds()
    seasoned = [o for o in offers if is_experienced(o[1], thresholds)]
    if seasoned and len(seasoned) < len(offers):
        first = _pick_hires(seasoned, have, shapes, min(take, len(seasoned)), limit)
        after = Counter(have)
        for h in first:
            after[h.recruit.hero_class] += 1
        rest = [o for o in offers if o not in seasoned]
        room = take - len(first)
        picked = first + (_pick_hires(rest, after, shapes, min(room, len(rest)), room)
                          if room > 0 and rest else [])
    else:
        picked = _pick_hires(offers, have, shapes, take, limit)
    names = ("fodder", "nurture", "invested")

    def swap_why(h):
        worst, rv, wv = swaps[h.index]
        return (f"{h.why}; replaces {worst.label()} "
                f"({names[rv[0]]} {rv[1]:.1f} over {names[wv[0]]} {wv[1]:.1f})")
    return [replace(h, replaces=swaps[h.index][0], why=swap_why(h))
            if h.index in swaps else h for h in picked]


def is_experienced(recruit: Recruit, thresholds=None) -> bool:
    """An Experienced Recruit: arrives above level 0, or with gear bought."""
    return (recruit_level(recruit, thresholds) >= 1
            or int(recruit.weapon_rank or 0) + int(recruit.armour_rank or 0) > 0)


def _pick_hires(offers, have: Counter, shapes, take: int, limit: int) -> list:
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
                      threshold: float = TREAT_ABOVE,
                      owned: dict | None = None,
                      roles: dict | None = None) -> list[Treatment]:
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
    # `owned` maps a tree id ("abbey.meditation") to the rank codes bought.
    # Given it, slots, price and heal each follow their OWN ladder; without
    # it, the old single `tier` stands for all three.
    def slots_of(a):
        if owned is None:
            return a.slots_at(tier)
        return a.owned_slots(owned.get(f"{a.building}.{a.activity}", ()))

    def cost_of(a):
        if owned is None:
            return a.cost_at(tier)
        return a.owned_cost(owned.get(f"{a.building}.{a.activity}", ()))

    def heal_of(a):
        if owned is None:
            return a.heal_at(tier)
        return a.owned_heal(owned.get(f"{a.building}.{a.activity}", ()))

    # Occupied slots come off the unlocked ones, and anyone already sitting in
    # a slot is being treated, whatever their roster record says yet.
    act_slots = getattr(town, "activity_slots", None) or {}
    busy = {g for guids in act_slots.values() for g in guids if g}

    def free_of(a):
        taken = sum(1 for g in act_slots.get((a.building, a.activity), [])[:slots_of(a)] if g)
        return max(0, slots_of(a) - taken)

    slots = [(a, i) for a in treatments for i in range(free_of(a))
             if a.building not in NEVER_BUILDINGS]
    slots.sort(key=lambda pair: cost_of(pair[0]))

    bank = int(town.wallet.get("gold", 0))
    gold = budget if budget is not None else bank
    # The Guild's cap: a tenth of the gold on hand, whatever the budget says.
    gold = min(gold, int(TREAT_GOLD_FRACTION * bank))
    # Only a hero WORTH treating, by ROLE (Fran, 2026-09-26): fodder "should
    # not use the facilities of the Hamlet at all" -- it is dismissed and
    # replaced from the coach. Invested heroes are kept usable; nurture
    # heroes are a luxury, ranked by their quirk score. An AFFLICTED hero of
    # either role is treated whatever the stress, since the town is the only
    # place an affliction comes off.
    roles = hero_roles.roles(town) if roles is None else roles

    def role(h):
        return getattr(roles.get(h.guid), "role", "fodder")

    def afflicted(h):
        return bool(getattr(h.hero, "affliction_id", None))

    candidates = [h for h in town.heroes
                  if not h.building and not h.in_party
                  and getattr(h.hero, "guid", None) not in busy
                  and role(h) in ("invested", "nurture")
                  and (float(getattr(h.hero, "stress", 0.0)) > threshold
                       or afflicted(h))]
    candidates.sort(key=lambda h: (
        role(h) != "invested", not afflicted(h),
        -float(getattr(h.hero, "stress", 0.0)) if role(h) == "invested"
        else -getattr(roles.get(h.guid), "nurture", 0.0)))

    # **Each hero to a slot the game will let them use** (live 2026-09-27: a
    # Flagellant refused by prayer and by meditation). A class may override
    # the valid activities outright, and an activity bars some quirks.
    from data.town_costs import class_valid_activities   # noqa: PLC0415

    def allowed(hero, activity) -> bool:
        valid = class_valid_activities(str(game_dir), hero.hero_class)
        if valid is not None and activity.activity not in valid:
            return False
        quirks = set(getattr(hero.hero, "quirks", None) or ())
        return not quirks & set(getattr(activity, "barred_quirks", ()) or ())

    pairs = []
    free_slots = list(slots)
    for hero in candidates:
        pick = next((sl for sl in free_slots if allowed(hero, sl[0])), None)
        if pick is not None:
            free_slots.remove(pick)
            pairs.append((hero, pick))

    out: list[Treatment] = []
    for hero, (activity, _slot) in pairs:
        cost = cost_of(activity)
        if cost > gold:
            break
        gold -= cost
        out.append(Treatment(
            hero_name=getattr(hero.hero, "name", "") or getattr(hero.hero, "hero_class", ""),
            hero_class=getattr(hero.hero, "hero_class", ""),
            building=activity.building, activity=activity.activity,
            cost=cost, stress_before=float(getattr(hero.hero, "stress", 0.0)),
            heals=heal_of(activity),
            guid=int(getattr(hero.hero, "guid", 0) or 0),
            slots=slots_of(activity)))
    return out


# -- the Sanitarium (Fran, 2026-10-12: docs/NEXT.md item 5, option 4) -------

#: On by default: Fran's ruling. False is the pre-2026-10-12 town, for an A/B.
SANITARIUM = True
#: A negative quirk is removed only when it ranks this bad or worse in
#: `data.quirk_tiers.NEGATIVE_PRIORITY` (1 = the worst of the worst).
SANITARIUM_NEGATIVE_MAX_PRIORITY = 5
#: The positive tier worth locking.
SANITARIUM_LOCK_TIERS = ("S",)


@dataclass(frozen=True)
class SanitariumVisit:
    """One hero into one Sanitarium slot for the week."""
    guid: int
    hero_class: str
    activity: str        # "disease_treatment" or "treatment"
    action: str          # "cure", "remove" or "lock"
    quirk: str
    cost: int
    #: The disease slot's chance of curing EVERY disease, not only `quirk`.
    cure_all_chance: float = 0.0

    def label(self) -> str:
        extra = f", {self.cure_all_chance:.0%} all" if self.action == "cure" else ""
        return (f"{self.hero_class}#{self.guid} {self.action} {self.quirk} "
                f"{self.cost:,}g{extra}")


@lru_cache(maxsize=2)
def quirk_kinds(game_dir: str = str(GAME_DIR)) -> dict:
    """`{quirk id: "positive" | "negative" | "disease"}`, from the base and
    DLC quirk libraries (never `modes/`)."""
    import json                                           # noqa: PLC0415
    g = Path(game_dir)
    files = [g / "shared" / "quirk" / "quirk_library.json"]
    files += sorted(f for f in (g / "dlc").rglob("*quirk_library.json") if "modes" not in f.parts)
    out = {}
    for f in files:
        try:
            for q in json.loads(f.read_text(encoding="utf-8", errors="replace"))["quirks"]:
                out[q["id"]] = ("disease" if q.get("is_disease")
                                else "positive" if q.get("is_positive") else "negative")
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return out


def choose_sanitarium(town: TownState, game_dir: Path = GAME_DIR,
                      budget: int | None = None, cap: int | None = None,
                      owned: dict | None = None, roles: dict | None = None,
                      exclude=()) -> list[SanitariumVisit]:
    """Who goes to the Sanitarium this week, and for what -- Fran's option 4.

    Diseases first, in the disease slots: the price cures the one chosen and
    rolls the shipped chance of curing them all. Then the quirk slots: the
    worst negative ranked 1-5 removed, else an S-tier positive locked. The
    same exclusions as the Abbey -- invested and nurture heroes only, never
    one in a building or marked for the party -- and the Abbey's tenth of
    the bank (`cap`). Shut until the building opens (4 quests).
    """
    from data.quirk_tiers import NEGATIVE_PRIORITY, POSITIVE_TIERS   # noqa: PLC0415
    from data.town_costs import load_sanitarium                     # noqa: PLC0415
    if not SANITARIUM:
        return []
    san = load_sanitarium(str(game_dir))
    if san is None:
        return []
    done = getattr(town, "quests_finished", None)
    if done is not None and done < san.quests_required:
        return []
    bank = int(town.wallet.get("gold", 0))
    gold = budget if budget is not None else bank
    gold = min(gold, cap if cap is not None else int(TREAT_GOLD_FRACTION * bank))
    kinds = quirk_kinds(str(game_dir))
    rank = {q: p for p, ids in NEGATIVE_PRIORITY.items() for q in ids}
    lockable = {q for t in SANITARIUM_LOCK_TIERS for q in POSITIVE_TIERS.get(t, ())}
    locked_by = getattr(town, "locked_quirks", None) or {}
    roles = hero_roles.roles(town) if roles is None else roles
    skip = set(exclude)

    def role(h):
        return getattr(roles.get(h.guid), "role", "fodder")

    act_slots = getattr(town, "activity_slots", None) or {}
    busy = {g for guids in act_slots.values() for g in guids if g}

    def free(activity, ladder):
        n = int(ladder.at(owned))
        taken = sum(1 for g in act_slots.get(("sanitarium", activity), [])[:n] if g)
        return max(0, n - taken)

    heroes = [h for h in town.heroes
              if not h.building and not h.in_party and h.guid not in busy
              and h.guid not in skip and role(h) in ("invested", "nurture")]
    heroes.sort(key=lambda h: (role(h) != "invested",
                               -getattr(roles.get(h.guid), "nurture", 0.0)))
    out: list[SanitariumVisit] = []
    taken: set = set()

    def take(h, activity, action, quirk, cost, chance=0.0):
        nonlocal gold
        if cost > gold:
            return False
        gold -= cost
        taken.add(h.guid)
        out.append(SanitariumVisit(guid=h.guid, hero_class=h.hero_class, activity=activity,
                                   action=action, quirk=quirk, cost=int(cost),
                                   cure_all_chance=float(chance)))
        return True

    slots = free("disease_treatment", san.disease_slots)
    for h in heroes:
        if slots <= 0:
            break
        diseases = [q for q in (getattr(h.hero, "quirks", None) or ()) if kinds.get(q) == "disease"]
        if diseases and take(h, "disease_treatment", "cure", diseases[0],
                             san.cure_disease.at(owned), san.cure_all_chance.at(owned)):
            slots -= 1
    slots = free("treatment", san.quirk_slots)
    for h in heroes:
        if slots <= 0:
            break
        if h.guid in taken:
            continue
        quirks = list(getattr(h.hero, "quirks", None) or ())
        locked = set(locked_by.get(h.guid, ()))
        bad = sorted((rank[q], q) for q in quirks
                     if kinds.get(q) == "negative"
                     and rank.get(q, 99) <= SANITARIUM_NEGATIVE_MAX_PRIORITY)
        if bad:
            q = bad[0][1]
            price = (san.remove_locked_negative if q in locked else san.remove_negative).at(owned)
            if take(h, "treatment", "remove", q, price):
                slots -= 1
            continue
        good = [q for q in quirks if q in lockable and q not in locked]
        if good and take(h, "treatment", "lock", good[0], san.lock_positive.at(owned)):
            slots -= 1
    return out


#: The Crimson Court's Granary, from `districts_districts.json`.
GRANARY_COST = {"gold": 2500, "bust": 60, "blueprint": 1}


def granary_reserve(town: TownState) -> dict:
    """What the Granary costs, while a blueprint is held and it is not built."""
    built = (getattr(town, "districts", None) or {}).get("granary")
    blueprints = int((town.wallet or {}).get("blueprint", 0) or 0)
    if built is None or built or blueprints < 1:
        return {}
    return {k: v for k, v in GRANARY_COST.items() if k != "blueprint"}


#: Fran, 2026-09-27: nurture heroes are levelled "without passing the cap to
#: have a safe money spend" -- the same tenth of the gold on hand the Guild
#: and the Abbey keep to.
NURTURE_GOLD_FRACTION = 0.10


@dataclass(frozen=True)
class NurtureBuy:
    """One rank for the hero being nurtured, and the building that sells it."""
    guid: int
    hero_class: str
    tree: str            # "weapon", "armour" or a skill id
    index: int           # the rank code bought (0 = a skill's unlock)
    gold: int
    building: str        # "blacksmith" or "guild"

    def label(self) -> str:
        gear = self.building == "blacksmith"
        what = ("unlock" if not gear and self.index == 0
                else f"level {self.index + (2 if gear else 1)}")
        return f"{self.hero_class}#{self.guid} {self.tree} {what} ({self.gold}g)"


def _owned_ranks(profile, hero, game_dir) -> dict:
    """`{tree: ranks bought}` for one hero, from the upgrades save."""
    try:
        from data.hero_skills import gear_ranks, unlocked  # noqa: PLC0415
        out = dict(unlocked(profile, hero, str(game_dir)))
        out.update(gear_ranks(profile, hero, str(game_dir)))
        return out
    except Exception:                                      # noqa: BLE001
        return {}


def nurture_buys(hero, owned: dict, purchased: set, game_dir: Path = GAME_DIR,
                 budget: Optional[int] = None, closed: frozenset = frozenset()) -> list:
    """Every rank this hero can buy NOW, cheapest first, within `budget`.

    "Now" is the shipped tree's own gate: the previous rank, the building
    rank (`guild.skill_levels`, `blacksmith.weapon`...) in `purchased`, and
    `prerequisite_resolve_level` against the hero's resolve. The skills the
    hero WEARS and the gear come before the other skills. Buying one rank can
    open the next, so this walks until nothing is left or the gold is."""
    from data.town_costs import load_hero_ranks            # noqa: PLC0415
    lh = getattr(hero, "hero", hero)
    ranks = load_hero_ranks(str(game_dir), lh.hero_class)
    if not ranks:
        return []
    level = int(getattr(lh, "resolve_level", 0) or 0)
    worn = set(getattr(lh, "selected_skills", None) or ())
    have = {t: int(owned.get(t, 0) or 0) for t in ranks}
    shut = {t for t in ranks
            if ("blacksmith" if t in ("weapon", "armour") else "guild") in closed}
    left = budget
    out = []
    while True:
        options = []
        for tree, ladder in ranks.items():
            i = have[tree]
            if i >= len(ladder) or tree in shut:
                continue
            r = ladder[i]
            if r.resolve > level or not all(n in purchased for n in r.needs):
                continue
            if left is not None and r.gold > left:
                continue
            gear = tree in ("weapon", "armour")
            options.append((not (gear or tree in worn), r.gold, tree, r))
        if not options:
            return out
        _worn, gold, tree, r = min(options)
        out.append(NurtureBuy(guid=int(lh.guid), hero_class=lh.hero_class,
                              tree=tree, index=r.index, gold=r.gold,
                              building="blacksmith" if tree in ("weapon", "armour")
                              else "guild"))
        have[tree] += 1
        if left is not None:
            left -= gold


def plan_nurture(town: TownState, profile, purchased: set,
                 game_dir: Path = GAME_DIR, budget: Optional[int] = None,
                 roles: Optional[dict] = None, closed: frozenset = frozenset()) -> list:
    """ONE nurture hero's buys this visit (Fran, 2026-09-27: *"max out what
    you can do in a hero before start nurturing another"*).

    The candidates are heroes whose quirks make them worth nurturing
    (`hero_roles.NURTURE_MIN`), whatever their role now -- gold sunk into a
    nurture hero makes them "invested", and that must not hand the Hamlet's
    attention to the next one. The one already carrying the most gold goes
    first, so a hero is finished before another is started; a hero with
    nothing to buy at their level is passed over until a level-up re-opens
    them."""
    roles = hero_roles.roles(town, profile) if roles is None else roles
    bank = int((town.wallet or {}).get("gold", 0))
    cap = int(NURTURE_GOLD_FRACTION * bank)
    budget = cap if budget is None else min(budget, cap)
    pool = [h for h in town.heroes or ()
            if getattr(roles.get(h.guid), "nurture", 0.0) >= hero_roles.NURTURE_MIN
            and not h.building]
    pool.sort(key=lambda h: (-getattr(roles.get(h.guid), "sunk", 0),
                             -getattr(roles.get(h.guid), "nurture", 0.0)))
    for h in pool:
        buys = nurture_buys(h, _owned_ranks(profile, h.hero, game_dir),
                            purchased, game_dir, budget, closed)
        if buys:
            return buys
    return []


def choose_upgrades(wallet: dict, purchased: set, game_dir: Path = GAME_DIR,
                    priority=None,
                    max_ranks: int = MAX_RANKS_PER_VISIT,
                    closed: frozenset = frozenset()):
    """Walk `priority` and buy what the wallet reaches, trading when short.

    Returns `([Purchase], wallet_after)`.

    **Ranks are taken in file order and prerequisites are re-checked as they
    are bought**, because `b` is only reachable once `a` is owned -- which is
    also why this cannot be a set-difference against `next_nodes`: that answers
    "what is open now", and buying `a` opens `b` within the same visit.
    """
    if priority is None:                   # read now, so an experiment can reorder it
        priority = UPGRADE_PRIORITY
    nodes_by_building = {}
    chosen: list[UpgradeNode] = []
    owned = set(purchased)

    for building, tree_id in priority:
        if building in closed:             # not open yet: nothing to buy there
            continue
        if building not in nodes_by_building:
            nodes_by_building[building] = load_building_upgrades(str(game_dir),
                                                                 building)
        all_nodes = nodes_by_building[building]
        taken = 0
        cap = max_ranks
        if SERVICE_DISCOUNT_MAX_RANKS is not None and tree_id in SERVICE_DISCOUNT_TREES:
            cap = min(cap, SERVICE_DISCOUNT_MAX_RANKS
                      - sum(1 for k in owned if k[0] == tree_id))
        while taken < cap:
            openable = [n for n in next_nodes(all_nodes, owned)
                        if n.tree_id == tree_id]
            if not openable:
                break
            node = openable[0]
            chosen.append(node)
            owned.add(node.key)
            taken += 1

    rates = load_rates(str(game_dir))
    if exchange_is_modded(game_dir):
        # **A modded exchange has rows nobody measured**, so no trade is
        # planned and only what the wallet covers outright is bought. Fran,
        # 2026-09-23: *"the model is trying to change heirlooms to unlock
        # something it isn't able to"* -- Garphyield's Exchange adds
        # heirloom -> gold rows, `exchange_to_row` (vanilla order) aims at
        # the wrong row, the name check refuses the confirm, and the whole
        # step spins for nothing every visit.
        rates = ()
    # **What the wallet cannot reach may be the prerequisite of what it can.**
    # `chosen` treats every openable rank as bought, so a rank behind an
    # unaffordable one looked open -- live 2026-09-27, twice: Experienced
    # Recruits b (heirlooms only) planned while its guild.skill_levels c and
    # blacksmith c (gold) were dropped, and the game refused it. Re-plan
    # without any rank whose prerequisites the kept set does not cover.
    while True:
        bought, left = plan_purchases(wallet, chosen, rates)
        have = set(purchased)
        orphan = None
        for n, _t in bought:
            if not all(p in have for p in n.prerequisites):
                orphan = n
                break
            have.add(n.key)
        if orphan is None:
            break
        chosen = [n for n in chosen if n.key != orphan.key]
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
    return tiers[min(rank, len(tiers) - 1)] + workshop_roster_bonus(game_dir)


#: Read Steam Workshop mods at all. True for the live player, whose install
#: is modded (Expanded Roster, a replaced heirloom exchange). The campaign
#: simulator plays VANILLA + DLC (docs/CAMPAIGN_SIM.md) and sets it False,
#: or Fran's `Expanded Roster: 1000` makes the simulated cap 1012 and the
#: roster grows to 70 heroes. The DLC slots live in `GAME_DIR` and are
#: counted either way.
INCLUDE_WORKSHOP = True


def exchange_is_modded(game_dir: Path = GAME_DIR) -> bool:
    """Does a Workshop mod replace `heirloom_exchange.json`?"""
    if not INCLUDE_WORKSHOP:
        return False
    try:
        root = Path(game_dir).parents[1] / "workshop" / "content" / "262060"
        return any(root.glob("*/campaign/heirloom_exchange/"
                             "heirloom_exchange.json"))
    except (OSError, IndexError):
        return False

def workshop_roster_bonus(game_dir: Path = GAME_DIR) -> int:
    """Extra roster slots the WORKSHOP mods add, which the game dir cannot see.

    **A cap read from the game files alone is wrong on a modded install, and
    a wrong cap does not merely misreport -- it starves the roster.** Fran,
    2026-09-22: *"there is no cap here, i enhanced the cap with a mod, the cap
    should be checked by the ingame info"*. Measured: `roster_cap` returned 9
    against 11 heroes, so `choose_recruits` correctly found "room for 0" and
    hired nobody for the whole campaign, while twelve recruits -- a Plague
    Doctor, a Crusader, a Highwayman among them -- sat in the coach and the
    fieldable roster stayed stressed and shrinking.

    DD composes the cap as the upgrade tier PLUS every mod's
    `*.roster.additional_variables.json`. `Expanded Roster: 1000` ships
    `{"additional": {"roster_size": 1000}}`, which is why the game's own
    counter reads 1017 where this file computed 9.

    **The DLC add slots too** -- Fran, same day: *"dlc files also increase
    roster slots"*. Shieldbreaker, Duelist and Runaway each ship one, so a
    normal campaign gains 3 from `GAME_DIR` before any mod is counted.

    Summed rather than maxed, because each source adds its own, and that is
    how the game reads them. Unreadable sources contribute 0 rather than
    breaking the cap. Measured on Fran's install: 1000 from the workshop plus
    3 from the DLC, against a game counter of 1017 -- so the remaining
    difference is in the vanilla TIER this file computes, and a cap that is a
    little low is safe where one that is high is not.
    """
    import json                                           # noqa: PLC0415

    total = 0
    roots = []
    try:
        game = Path(game_dir)
        roots.append(game)                                # the DLC live here
        if INCLUDE_WORKSHOP:
            roots.append(game.parents[1] / "workshop" / "content" / "262060")
    except Exception:                                     # noqa: BLE001
        return 0
    seen = set()
    for root in roots:
        try:
            if not root.is_dir():
                continue
            for path in root.rglob("*.roster.additional_variables.json"):
                # **`modes/` is a DIFFERENT CAMPAIGN.** Bloodmoon and New Game
                # Plus ship their own copy of each feature's slot, and counting
                # those would inflate an ordinary campaign's cap by one per
                # mode per class.
                if "modes" in path.parts or path in seen:
                    continue
                seen.add(path)
                try:
                    data = json.loads(path.read_text(encoding="utf-8",
                                                     errors="replace"))
                    total += int(data.get("additional", {})
                                 .get("roster_size", 0))
                except Exception:                         # noqa: BLE001
                    continue
        except Exception:                                 # noqa: BLE001
            continue
    return total


# -- the whole visit ------------------------------------------------------

# **PizzaRat's positive-quirk tiers**, keyed by the GAME's quirk id (the site
# uses its own ids: `corvid_grace` is `corvids_grace`, `man_slayer` is
# `slayer_of_man`). Fetched 2026-09-23 from
# https://pizzarat105.github.io/PizzaRatReview/dd_map_test.html -- Fran's
# ruler for "a quirk worth investing in". S/A/B are GOOD ENOUGH.
# The tables live in `data/quirk_tiers.py` (one source for dismissal and
# curio scoring alike): every positive quirk -> its tier letter.
from data.quirk_tiers import POSITIVE_TIERS as _POSITIVE_TIERS  # noqa: E402
QUIRK_TIER = {q: tier for tier, ids in _POSITIVE_TIERS.items() for q in ids}
GOOD_QUIRK_TIERS = ("S", "A", "B")


#: Fran, 2026-09-26, on the Abbey: *"lets make the same use as the guild, a
#: lvl 0 hero is not worth recovering unless extremely good trait or heavy
#: gold on bank ... otherwise its just a money waste"*. The Guild's rules:
#: never for level 0, and never more than a tenth of the gold on hand.
TREAT_GOLD_FRACTION = 0.10
#: "Heavy gold on bank": at or above this a level-0 hero may be treated too.
#: A declared convention -- about five treatments' worth at the dearest price.
HEAVY_GOLD = 30_000


def has_excellent_quirk(hero: RosterHero) -> bool:
    """An S-tier positive quirk: Fran's "extremely good trait"."""
    return any(QUIRK_TIER.get(q) == "S" for q in (hero.hero.quirks or ()))


def has_good_quirk(hero: RosterHero) -> bool:
    return any(QUIRK_TIER.get(q) in GOOD_QUIRK_TIERS
               for q in (hero.hero.quirks or ()))


def choose_dismissals(town: TownState, need: int, keep=()) -> list[RosterHero]:
    """Who to let go when the REAL cap is hit, worst first.

    Fran, 2026-09-22: *"a quirkless (good enough quirks) ... and low level
    hero can be dismissed freely"*, and *"low level depends on the average
    roster level, but 0 can be always dismissed"*. So a candidate has no
    S/A/B quirk AND is level 0 or below the roster's mean level; among those
    the most stressed goes first -- a stressed hero costs gold before they
    are worth anything. A hero in a building or in `keep` is never offered.
    """
    heroes = list(town.heroes or ())
    if need <= 0 or not heroes:
        return []
    mean = sum(h.resolve_level for h in heroes) / len(heroes)
    keep = set(keep)
    pool = [h for h in heroes
            if h.guid not in keep and h.available and not h.in_party
            and not has_good_quirk(h)
            and (h.resolve_level == 0 or h.resolve_level < mean)]
    pool.sort(key=lambda h: (h.resolve_level, -h.stress))
    return pool[:need]


#: Fran's reset rule, 2026-09-23: *"dismissing all heroes on lvl 0 and over
#: 50 stress"*, to restart a stuck campaign from a clean roster.
RESET_STRESS = 50.0


def reset_dismissals(town: TownState, keep=()) -> list[RosterHero]:
    """Every level-0 hero over `RESET_STRESS`, most stressed first.

    Not wired into `advise`: this is the rebuild Fran described for a save
    past the point of no return, and whether to run it is his call. The
    hero he keeps back for a Darkest Dungeon week is passed in `keep`.
    `town.dismiss_heroes` executes the list.
    """
    keep = set(keep)
    out = [h for h in (town.heroes or ())
           if h.guid not in keep and h.resolve_level == 0
           and h.stress > RESET_STRESS and not h.in_party]
    return sorted(out, key=lambda h: -h.stress)

def advise(town: TownState, store=None, upgrades_file=None,
           game_dir: Path = GAME_DIR,
           roster_room: Optional[int] = None,
           sanitarium_spends: bool = True) -> TownAdvice:
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
    # **The Granary first, once there is a blueprint** (Fran, 2026-09-27:
    # extra food and healing for the party). Its price is held back from
    # every other upgrade until it can be bought.
    held = granary_reserve(town)
    if held:
        held = {cur: min(n, int(wallet.get(cur, 0))) for cur, n in held.items()}
        for cur, n in held.items():
            wallet[cur] = int(wallet.get(cur, 0)) - n
        have = int((town.wallet or {}).get("bust", 0))
        notes.append(f"saving for the Granary (blueprint held): "
                     f"{have}/{GRANARY_COST['bust']} bust, "
                     f"{int((town.wallet or {}).get('gold', 0))}/"
                     f"{GRANARY_COST['gold']} gold")
    offers = sum(1 for r in town.recruits or ()
                 if r.hero_class and r.listing == GOLD_RECRUIT_LISTING)
    cap = roster_cap(upgrades_file, game_dir)
    shut = closed(town, game_dir)
    if shut & {b for b, _t in UPGRADE_PRIORITY}:
        notes.append("not open yet: " + ", ".join(sorted(shut)))
    order = priority_for(len(town.heroes or ()), offers, cap)
    if order != tuple(UPGRADE_PRIORITY):
        notes.append(f"roster {len(town.heroes or ())} + {offers} offered over a "
                     f"cap of {cap}: roster size first")
    purchases, left = choose_upgrades(wallet, purchased, game_dir, priority=order,
                                      closed=shut)
    for cur, n in held.items():
        left[cur] = int(left.get(cur, 0)) + n
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
    # **Hire EVERY offer by default.** Fran, 2026-09-23: *"You are recruiting
    # 2 people rather than all and that limits your options to 2 stressed out
    # and 2 clean"*. A recruit costs nothing at the coach; the choice of who
    # to field is made at embark, and it needs the whole pool to choose from.
    if roster_room is None:
        roster_room = sum(1 for r in town.recruits or ()
                          if r.hero_class and r.listing == GOLD_RECRUIT_LISTING)
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
    # **At the real cap, plan who makes room -- but do not hire into it yet.**
    # Dismissal is not actuated, so counting the freed slots as room would
    # re-create the "plan a hire the game will refuse" loop above.
    dismissals = tuple(cap_dismissals(town))
    if dismissals:
        notes.append(f"{len(dismissals)} fodder hero(es) over the per-class "
                     f"band caps {band_caps(town)}: " + ", ".join(
                         h.label() for h in dismissals[:6])
                     + (" ..." if len(dismissals) > 6 else ""))
    if cap is not None and room < roster_room:
        extra = [h for h in choose_dismissals(town, roster_room - room)
                 if h not in dismissals]
        dismissals = dismissals + tuple(extra)
        if dismissals:
            notes.append("at the cap: would dismiss " + ", ".join(
                h.label() for h in dismissals))
    hires = choose_recruits(town, shapes, limit=room) if room else ()
    # A hire into a FULL class band replaces that band's worst fodder, and the
    # dismissal is planned with it (`_band_room`).
    swapped = tuple(h.replaces for h in hires
                    if h.replaces is not None and h.replaces not in dismissals)
    if swapped:
        notes.append("better recruits replace: " + ", ".join(
            h.label() for h in swapped))
        dismissals = dismissals + swapped
    # Stress last, and out of what the other two LEAVE. A treated hero is
    # worth having and a hire is worth more: the roster is what makes a comp
    # fieldable at all, and an untreated hero is still playable while a missing
    # class is not. Spending the remainder is also the safe direction -- it can
    # only ever plan fewer treatments than the wallet could afford, never more.
    owned: dict = {}
    for tree_id, code in purchased:
        owned.setdefault(tree_id, set()).add(code)
    treatments = [] if "abbey" in shut else choose_treatments(
        town, game_dir, budget=int(left.get("gold", 0)), owned=owned)
    for t in treatments:
        left["gold"] = int(left.get("gold", 0)) - t.cost
    # The Sanitarium under the SAME tenth of the bank the Abbey keeps to,
    # out of what the Abbey left of it (Fran's option 4, 2026-10-12).
    bank = int(town.wallet.get("gold", 0))
    sanitarium = choose_sanitarium(
        town, game_dir, budget=int(left.get("gold", 0)), owned=owned,
        cap=max(0, int(TREAT_GOLD_FRACTION * bank) - sum(t.cost for t in treatments)),
        exclude={t.guid for t in treatments})
    # The simulator applies the plan, so it pays for it. The live town only
    # reports it (the panel is not measured) and passes False: gold held for
    # a treatment nobody clicks would starve the nurture spend below.
    if sanitarium_spends:
        for v in sanitarium:
            left["gold"] = int(left.get("gold", 0)) - v.cost

    # Nurture last, out of what the rest left, under its own tenth of the bank.
    nurture: list = []
    if upgrades_file is not None:
        owned_now = set(purchased) | {p.node.key for p in purchases}
        nurture = plan_nurture(town, Path(upgrades_file).parent, owned_now,
                               game_dir, budget=int(left.get("gold", 0)), closed=shut)
        for n in nurture:
            left["gold"] = int(left.get("gold", 0)) - n.gold

    return TownAdvice(sanitarium=tuple(sanitarium),
                      purchases=tuple(purchases), hires=tuple(hires),
                      treatments=tuple(treatments),
                      wallet_after=left, notes=tuple(notes),
                      dismissals=dismissals, nurture=tuple(nurture))


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
