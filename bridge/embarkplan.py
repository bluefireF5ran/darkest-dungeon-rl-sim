"""Which quest, which comp, which formation, which skills, which provisions.

Decides; never clicks. Same split as `bridge/townplan.py` and for the same
reason -- a decision that replays offline is a decision that can be tested, and
`bridge/act.py` remains the only thing that turns an answer into input.

`bridge/embark.py` answers a different question and stays: it RANKS the
fieldable parties by running the policy on each. This one answers Fran's,
2026-09-01:

    rather than just choose "the best option" I would like just an option that
    works, so choosing a random available comp

So there is no rollout here, no policy and no checkpoint. The score is
**affordability**, the choice among the affordable ones is a coin flip, and
that is the whole design: variety beats a ranking when every option is already
one a human vouched for.

## The order money is spent in

1. **Provisions first, at the RECOMMENDED end** (`data/provisions.py`, Fran's
   answer of 2026-09-01). A Ruins short quest reserves 3550 gold before
   anything else is considered, because a party that arrives without torches
   and shovels loses more than a party that arrives with three skills at rank
   1.
2. **Skill unlocks, for the four heroes actually going** -- PLAYBOOK 4.4:
   *"Primero se decide quien va a la mision, y se maximiza a ese cuarteto si el
   dinero llega."* Never the whole roster.
3. **Nothing else this week.** Weapon and armour rank 1 is 750 gold, but it
   needs `blacksmith.weapon a` (8 deed + 8 crest against a wallet holding 0
   deed) and resolve 1, which two of the twelve live heroes have. Skills need
   no building rank and `prerequisite_resolve_level: 0`.

## Only CURATED comps

`store.comp_loadouts` holds both folders and `CompLoadoutEntry.source` says
which. `composed_comps/` exists for training coverage of a starved class --
nobody has played those. Sending a live party on one is exactly the claim the
two-folder split was made to keep separable.

## Why the quest filter is `simulable` and not `is_generated`

The only Ruins quest on the live map is `plot_tutorial_crypts`. It is a plot
quest and it is a perfectly ordinary Ruins expedition; `is_generated` refuses
it and reports an empty map. What genuinely has no encounter table here is the
courtyard and the Darkest Dungeon, which is what `simulable` excludes.
`bridge/embark.py` carried the same bug and got the same fix.
"""
from __future__ import annotations

import argparse
import random
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bridge import hero_roles
from bridge.embark import candidate_shapes
from bridge.town_state import Quest, RosterHero, TownState, read_town_state
from data import provisions
from data.hero_skills import missing as missing_skills
from data.hero_skills import unknown as unknown_skills
from data.hero_skills import unlock_cost

from data.paths import GAME_DIR

# A comp's own folder under `metadata/COMPS/`. Fran's answer, 2026-09-01:
# "This but only for curated comps".
CURATED = "curated_comps"

PARTY_SIZE = 4

#: **Count a seat's kit, not only its bill** (2026-10-10, the campaign
#: simulator). A fodder hero is never bought skills (Fran, 2026-09-25), so
#: its seat has no `unlocks` and read as a free, perfect fit even when the
#: hero owns none of the comp's four -- it then fought with whatever the
#: coach rolled. With this on, the comp skills a seat will NOT have
#: (`Seat.lacks`) weigh in the draw like an unlock does, and a class's
#: candidates are tried owning-the-kit first. Spends nothing.
KIT_AWARE = True

#: Gold the top-up may not spend: one skill unlock (1,000) plus one stress
#: treatment (up to 1,250). See `_spend_the_remainder`.
TOP_UP_RESERVE = 2250

#: Stress at which the game runs a hero's Resolve Check. `engine/stress.py`
#: carries the same 100 for combat; here it is the line between a hero who is
#: tired and one who is **already afflicted**, since an affliction lasts the
#: rest of the raid and a Resolve Check cannot be passed twice.
RESOLVE_CHECK = 100.0

#: Stress at which a hero is STRAINED and should sit the week out.
#
# **The gate used to be `RESOLVE_CHECK` alone, and that is the wrong question
# for choosing a party.** At 100 a hero is already afflicted; below it the
# game lets you send them, and this module deliberately refused to be
# cleverer than the game about that half. What it missed is that "legal to
# send" and "worth sending" are different, and nothing else re-picked: the
# comp is chosen deterministically, so the same tired four went out again.
#
# Measured 2026-09-21 on Fran's campaign: an Occultist at 60 stress was
# fielded at rank 1 on a 19 HP body, the party embarked a second time on week
# 2 without a single change, and came home as one hero at 3 HP and 157
# stress. Fran: *"after the first expedition we immediately went onto the 2nd
# one with the same comp without changing heroes even if they were stressed
# out ... each week lets redo the comp"*.
#
# 50 is the Hamlet's own line -- the point at which DD starts showing the
# stress bar as a concern -- and it is the same threshold the team-builder
# repo filters on, reached independently there from a roster study.
STRAINED = 50.0


@dataclass(frozen=True)
class Seat:
    """One hero, where they stand, and what it costs to give them their kit."""
    rank: int                       # 1 = front = the RIGHTMOST party slot
    hero: RosterHero
    skills: tuple[str, ...] = ()    # the comp's four, in the comp's order
    unlocks: tuple[str, ...] = ()   # of those, the ones not yet bought
    cost: int = 0
    #: Of the comp's four, the ones this hero will go WITHOUT: not owned and
    #: not being bought (a fodder seat, or an unlock trimmed off the budget).
    lacks: tuple[str, ...] = ()

    def line(self) -> str:
        buy = f"  buy {', '.join(self.unlocks)} ({self.cost}g)" if self.unlocks else ""
        return f"rank {self.rank}  {self.hero.label():28s} stress {self.hero.stress:5.1f}{buy}"


@dataclass(frozen=True)
class EmbarkPlan:
    """Everything the embark chain needs, decided before a pixel is touched."""
    quest: Quest
    comp: str
    party: tuple[Seat, ...] = ()
    basket: dict = field(default_factory=dict)
    basket_cost: int = 0
    unlocks_cost: int = 0
    gold: int = 0
    notes: tuple[str, ...] = ()

    @property
    def gold_after(self) -> int:
        return self.gold - self.basket_cost - self.unlocks_cost

    @property
    def under_strength(self) -> bool:
        """Fewer than four heroes. Fran: *"we should make sure we are sending
        4 people comps during embark"*.

        A short party is not a slightly worse party. Every curated comp is
        written for four, the formation rules that decide which skills are
        legal are about four ranks, and the two-hero raids this loop actually
        sent came home as wipes. It is also the one thing about a plan that a
        caller can act on without reading any of the rest of it, which is why
        it is a property here rather than a check at each of the three places
        that embark.
        """
        return len(self.party) < PARTY_SIZE

    @property
    def unlocks(self) -> tuple[tuple[RosterHero, str], ...]:
        return tuple((seat.hero, skill)
                     for seat in self.party for skill in seat.unlocks)

    def report(self) -> str:
        out = [f"quest   {self.quest.label()}  reward {self.quest.gold_reward}g",
               f"comp    {self.comp}",
               f"gold    {self.gold}  -> {self.gold_after} "
               f"(provisions {self.basket_cost}, unlocks {self.unlocks_cost})"]
        for seat in self.party:
            out.append("  " + seat.line())
        items = ", ".join(f"{k} x{v}" for k, v in sorted(self.basket.items()))
        out.append(f"basket  {items or '(none)'}")
        out.extend(f"note    {n}" for n in self.notes)
        return "\n".join(out)


#: A quest is in reach when four rested, eligible heroes can go and at most
#: this many of them are a level short of it (Fran: "optionally 1 underleveled
#: hero").
MAX_UNDERLEVELED = 1
#: A boss is a harder fight than its tier: nobody under the quest level goes.
BOSS_MAX_UNDERLEVELED = 0
#: EXPERIMENT knob, 0 = Fran's rule as it stands: a boss party is at the
#: quest level. N > 0 asks for N levels ABOVE it (an Apprentice boss at 1+N).
#: Priced by the campaign simulator (2026-10-10), not a ruling.
BOSS_LEVEL_MARGIN = 0
#: EXPERIMENT knob, None = Fran's rule as coded: the one underlevelled hero
#: may be any number of levels short (a level-0 in a Champion party). N
#: allows at most N levels short. Priced by the campaign simulator, not a ruling.
UNDERLEVEL_MAX_GAP = None


def _shortfall(levels, need: int) -> int:
    """Heroes under `need`. A hero further short than `UNDERLEVEL_MAX_GAP`
    puts the party over every limit, but still ranked by its count: a
    flat sentinel tied a four-level-0 seat with a one-level-0 seat and undid
    the fewest-underlevelled fallback (first gap-1 run, 2026-10-11)."""
    gaps = [need - lv for lv in levels if lv < need]
    if UNDERLEVEL_MAX_GAP is not None and any(g > UNDERLEVEL_MAX_GAP for g in gaps):
        return PARTY_SIZE + 1 + len(gaps)
    return len(gaps)


def _boss_level(q: Quest) -> int:
    return _quest_level(q) + BOSS_LEVEL_MARGIN


def _quest_level(q: Quest) -> int:
    """The resolve level a quest is pitched at, which IS its difficulty:
    Apprentice 1, Veteran 3, Champion 5. A hero below it carries the game's
    underlevelled penalty (`effectiveDifficultyAfflictionOnsetModifiers`,
    engine `level_shortfall`) -- a level-0 hero is one short on Apprentice."""
    return int(q.difficulty)


def _party_for(town: TownState, q: Quest) -> list:
    pool, _benched = _rested(town.eligible(q))
    return sorted(pool, key=lambda h: (-h.resolve_level, h.stress))[:PARTY_SIZE]


def quest_in_reach(town: TownState, q: Quest) -> bool:
    party = _party_for(town, q)
    if len(party) < PARTY_SIZE:
        return False
    boss = q.quest_type == "kill_boss"
    need = _boss_level(q) if boss else _quest_level(q)
    short = (sum(1 for h in party if h.resolve_level < need) if boss
             else _shortfall([h.resolve_level for h in party], need))
    limit = BOSS_MAX_UNDERLEVELED if boss else MAX_UNDERLEVELED
    return short <= limit


def boss_worth_it(q: Quest) -> bool:
    """A boss quest whose trinket is a real advantage (Fran, 2026-10-01:
    "prefiero buscar una que me de una ventaja real"), judged by HIS tier
    list (`data/trinket_tiers`). Every boss pays a very rare; most are not."""
    if q.quest_type != "kill_boss":
        return False
    from data.trinket_tiers import worth_a_boss                    # noqa: PLC0415
    return any(worth_a_boss(t) for t in q.reward_trinkets)


def pick_quest(town: TownState) -> Optional[Quest]:
    """The quest to take.

    1. **The highest difficulty the roster can field.** A hero refuses a quest
       below their band (`town.eligible` is the game's own resolve cap), so a
       campaign that never climbs is a campaign whose levelled heroes sit in
       the Hamlet forever -- the 2026-09-28 audit found the bridge at week 77
       with a mean hero level of 1.0 because this used to be shortest-first
       over everything. In reach = four rested eligible heroes, at most
       `MAX_UNDERLEVELED` of them a level short.
    2. **A boss only for a trinket worth it**, and never underlevelled. Fran
       passed on 12 of 12 boss offers whose rewards sat in the bottom tiers of
       his list; this reproduces every one of those decisions.
    3. Within that: shortest first, then the biggest reward -- a short run is
       what a thin roster survives.

    When nothing is in reach (a roster under four, or a fresh one all at
    level 0), the LOWEST difficulty is taken, then shortest, then gold -- the
    old rule ordered by gold before difficulty and would send a green roster
    to a Veteran quest for a bigger purse. **Never a boss** there: an
    Apprentice boss tied with the Apprentice quests, and the campaign
    simulator sent green rosters to the Necromancer 129 times out of 159
    lost (2026-10-10).
    """
    options = [q for q in town.quests if q.simulable
               and (q.quest_type != "kill_boss" or boss_worth_it(q))]
    if not options:
        return None
    reach = [q for q in options if quest_in_reach(town, q)]
    if reach:
        return min(reach, key=lambda q: (-q.difficulty, not boss_worth_it(q),
                                         q.length, -q.gold_reward, q.key))
    options = [q for q in options if q.quest_type != "kill_boss"]
    if not options:
        return None
    return min(options, key=lambda q: (q.difficulty, q.length, -q.gold_reward, q.key))


def _seat_cost(profile, hero: RosterHero, skills: Sequence[str],
               game_dir: str) -> tuple[tuple[str, ...], int]:
    # **Never for FODDER** (Fran, 2026-09-25 for level 0, widened 2026-09-26
    # to the fodder role): they are expendable and the coach replaces them
    # for free, so a skill bought for one is gold the next death throws away.
    # They go with the skills they have.
    if _role(hero) == "fodder":
        return (), 0
    want = missing_skills(profile, hero, skills, game_dir)
    gold = unlock_cost(profile, hero, skills, game_dir)
    return want, (gold if gold is not None else 0)


def _role(hero) -> str:
    """`hero_roles.role_of` off the roster record alone (no upgrades file):
    level, the save's gear ranks, and the quirk score for the class."""
    h = getattr(hero, "hero", hero)
    gear = (int(getattr(h, "weapon_rank", 0) or 0)
            + int(getattr(h, "armour_rank", 0) or 0))
    score = hero_roles.nurture_score(getattr(h, "hero_class", ""),
                                     getattr(h, "quirks", None) or ())
    return hero_roles.role_of(int(getattr(h, "resolve_level", 0) or 0), 0,
                              score, gear)


#: The comp draw's weight per seated hero, by role (Fran, 2026-09-26:
#: "priorize the most invested non stressed heroes"). A declared convention.
ROLE_DRAW_WEIGHT = {"invested": 2.0, "nurture": 1.5, "fodder": 1.0}
#: A party spanning more than one level band is drawn this much less
#: ("all heroes on the same lvl threshold optimal for the mission").
MIXED_BAND_WEIGHT = 0.25
#: A second nurture hero in one party ("optionally 1 underleved hero").
EXTRA_NURTURE_WEIGHT = 0.25


def party_weight(seats) -> float:
    """How much the draw favours this party: invested and rested heroes,
    one level band, at most one nurture hero. Multiplies the unlock weight."""
    heroes = [getattr(st, "hero", st) for st in seats]
    if not heroes:
        return 1.0
    w = 1.0
    roles = [_role(h) for h in heroes]
    for h, r in zip(heroes, roles):
        stress = float(getattr(h, "stress", 0.0) or 0.0)
        w *= ROLE_DRAW_WEIGHT.get(r, 1.0) * max(0.1, 1.0 - stress / 200.0)
    if len({hero_roles.band_of(getattr(h, "resolve_level", 0)) for h in heroes}) > 1:
        w *= MIXED_BAND_WEIGHT
    if roles.count("nurture") > 1:
        w *= EXTRA_NURTURE_WEIGHT ** (roles.count("nurture") - 1)
    return w


def fill_cheapest(comp, pool: list[RosterHero], profile,
                  game_dir: str) -> Optional[list[tuple[RosterHero, tuple[str, ...]]]]:
    """Seat this comp from the free roster as cheaply as it can be seated.

    **Not `embark.fill_shape`, and the difference is load-bearing.** That one
    picks lowest-stress-first within a class, which is right when a rollout is
    about to rank the parties; here the question is whether the comp is
    affordable at all, and two heroes of one class can differ by three unlocks
    -- the live roster's two Antiquarians share three skills and differ in the
    fourth. Choosing by stress would report a comp unaffordable that is
    affordable with the other one of the pair.

    Ordered by `(unlocks needed, stress, -resolve)`: gold first because it is
    what decides fieldable-or-not, then stress, which is the constraint the
    Hamlet imposes and does not heal in a dungeon.

    Greedy per slot. With a repeated class the greedy assignment is not
    provably minimal, but a class appears twice in a curated comp rarely and
    the two orders differ by at most one unlock.
    """
    remaining = list(pool)
    out: list[tuple[RosterHero, tuple[str, ...]]] = []
    for slot in comp.heroes:
        options = [h for h in remaining if h.hero_class == slot.hero_id]
        if not options:
            return None
        skills = tuple(slot.skill_ids)

        def key(h: RosterHero):
            want, gold = _seat_cost(profile, h, skills, game_dir)
            lacks = (len(_lacks(profile, h, skills, want, game_dir))
                     if KIT_AWARE else 0)
            return (gold, lacks, _role(h) != "invested", h.stress, -h.resolve_level)

        pick = min(options, key=key)
        remaining.remove(pick)
        out.append((pick, skills))
    return out


def _build_party(filled, profile, game_dir: str) -> tuple[tuple[Seat, ...], int]:
    """Seats in comp order -- index 0 is rank 1, the front.

    `CombatEngine.reset` assigns `position = index`, so the comp's own hero
    order IS the formation, and `townlayout.party_slot_for_rank` turns a rank
    into the slot on screen (rank 1 rightmost, rank 4 leftmost).
    """
    seats: list[Seat] = []
    total = 0
    for index, (hero, skills) in enumerate(filled):
        want, gold = _seat_cost(profile, hero, skills, game_dir)
        total += gold
        seats.append(Seat(rank=index + 1, hero=hero, skills=skills,
                          unlocks=want, cost=gold,
                          lacks=_lacks(profile, hero, skills, want, game_dir)))
    return tuple(seats), total


def _lacks(profile, hero: RosterHero, skills: Sequence[str],
           buying: Sequence[str], game_dir: str) -> tuple[str, ...]:
    """The comp skills this hero neither owns nor is being bought."""
    missing = missing_skills(profile, hero, skills, game_dir)
    return tuple(k for k in missing if k not in buying)


def _trim_to_budget(seats: Sequence[Seat], budget: int) -> tuple[tuple[Seat, ...], int]:
    """Drop unlocks, cheapest hero last, until the bill fits.

    A hero whose unlocks are dropped keeps the four skills they already have --
    the comp's formation and classes still stand, which is most of what a comp
    is, and the alternative is not embarking. Heroes are trimmed from the back
    rank forward so the front rank, which takes the hits, is equipped first.
    """
    kept = list(seats)
    total = sum(s.cost for s in kept)
    for i in range(len(kept) - 1, -1, -1):
        if total <= budget:
            break
        seat = kept[i]
        if not seat.unlocks:
            continue
        total -= seat.cost
        kept[i] = Seat(rank=seat.rank, hero=seat.hero, skills=seat.skills,
                       lacks=tuple(seat.lacks) + tuple(seat.unlocks))
    return tuple(kept), total


def _reseat_unbought(seats: Sequence[Seat],
                     pool: Sequence[RosterHero]) -> tuple[Seat, ...]:
    """Give an unequipped slot to the FRESHEST hero of that class, not the
    cheapest.

    `fill_cheapest` ranks by gold first, and it is right to: whether a comp can
    be seated at all is a question about money, and two heroes of one class can
    differ by three unlocks. But once `_trim_to_budget` has decided a slot's
    skills are not being bought, **that hero's price has stopped mattering**
    and the only thing separating the candidates is how close they are to
    breaking.

    Seen live 2026-09-05 at 15 gold, where nothing at all was affordable: the
    plan seated Highwayman #2 at 87 stress while #28, #64 and #65 sat at zero,
    to save an unlock bill it then did not pay.

    Only touches slots with no unlocks left, so an equipped hero is never
    swapped out from under the skills that were bought for them.
    """
    taken = {seat.hero.guid for seat in seats}
    out = []
    for seat in seats:
        if seat.unlocks:
            out.append(seat)
            continue
        fresher = [h for h in pool
                   if h.hero_class == seat.hero.hero_class
                   and h.guid not in taken
                   and h.stress < seat.hero.stress]
        if not fresher:
            out.append(seat)
            continue
        pick = min(fresher, key=lambda h: (h.stress, -h.resolve_level))
        taken.discard(seat.hero.guid)
        taken.add(pick.guid)
        out.append(Seat(rank=seat.rank, hero=pick, skills=seat.skills))
    return tuple(out)


def fieldable_comps(town: TownState, store, profile, quest: Quest,
                    game_dir: str = str(GAME_DIR),
                    pool: Optional[Sequence[RosterHero]] = None,
                    ) -> list[tuple[str, tuple[Seat, ...], int]]:
    """`(comp name, seats, unlock bill)` for every curated comp this roster can
    put four heroes into, cheapest fill per comp.

    One function rather than one per caller: the plan and the `--all` listing
    drifting apart is how `pick_representative_comps` and `build_comp_pool`
    drifted, and the two-hero tutorial party is exactly the row that shows up
    in a listing that skipped a filter.
    """
    # `pool` lets the caller hand in a roster it has already filtered --
    # `plan_embark` benches the afflicted before asking what is fieldable, so
    # a comp is only "fieldable" out of heroes it would actually send. The
    # default keeps `--all` and every other caller reading the whole roster.
    pool = list(town.eligible(quest) if pool is None else pool)
    out: list[tuple[str, tuple[Seat, ...], int]] = []
    for name, _shape in candidate_shapes(store, quest):
        comp = store.comp_loadout_by_name(name)
        if comp is None or comp.source != CURATED:
            continue
        filled = fill_cheapest(comp, pool, profile, game_dir)
        if filled is None:
            continue
        seats, cost = _build_party(filled, profile, game_dir)
        # A comp that declares fewer than four heroes is not a party to embark
        # with -- `The Old Road` is the tutorial pair and would otherwise lead
        # the list at 0 gold, being both fieldable and free.
        if len(seats) != PARTY_SIZE:
            continue
        out.append((name, seats, cost))
    return out


def _basket_for(quest, level, game_dir, seats, gold, notes) -> tuple[dict, int]:
    """The basket NET of what the party is handed free, and its cost.

    `campaign/provision/provision.json` grants eight classes a provision each
    on entering a raid -- a Crusader brings holy water, a Leper and a Jester
    herbs, an Arbalest a bandage, a Houndmaster two dog treats. PLAYBOOK 1.5's
    table says what to CARRY, so what the party already carries is not bought.
    On a four-hero party that is routinely 150-500 gold.

    It has to run AFTER the comp is chosen, because the grant is a property of
    the party. The reservation above therefore uses the party-less basket,
    which is the conservative direction: it holds back at least what any party
    could need, so the skill budget can only turn out larger than promised.
    """
    party = [s.hero.hero_class for s in seats]
    # PLAYBOOK 7.7: the game's own averages, not the community table, for
    # every item they cover -- rounded UP, "go over than under".
    from bridge.provision_needs import derived_basket      # noqa: PLC0415
    band = provisions.LENGTH_TO_BAND.get(quest.length, "short")
    try:
        derived = derived_basket(quest.dungeon, band,
                                 getattr(quest, "quest_type", "") or "explore",
                                 str(game_dir))
    except Exception:                                      # noqa: BLE001
        derived = {}
    want = provisions.basket(quest.dungeon, quest.length, level, game_dir,
                             party=party, derived=derived)
    cost = provisions.cost(want, game_dir)
    if cost > gold:
        want = provisions.afford(want, gold, game_dir)
        cost = provisions.cost(want, game_dir)
        notes.append(f"basket cut to fit {gold} gold -- see provisions.CUT_ORDER")
    free = provisions.granted(party, quest.length, game_dir)
    if free:
        notes.append("the party is handed "
                     + ", ".join(f"{n} {i}" for i, n in sorted(free.items()))
                     + " -- not bought")
    return want, cost


def _rested(pool: Sequence[RosterHero]) -> tuple[list[RosterHero],
                                                  list[RosterHero]]:
    """Drop the already-afflicted, but only while four heroes remain.

    **`town.eligible` is a statement of fact and this is a decision**, which is
    why the gate lives here: the game will happily send a hero at 200 stress,
    and three raids on 2026-09-05 died proving it. The last one fielded a
    Plague Doctor at 179 and a Shieldbreaker at 148 out of a roster holding
    eleven heroes at zero, wiped in four rounds, and left the campaign on 15
    gold -- which then bought no stress treatment, which put the same heroes
    out again.

    At or past the Resolve Check a hero **is** afflicted: the check is already
    made, the trait lasts the rest of the raid, and no amount of good play in
    the dungeon takes it off. Below it they are merely tired, and the real game
    lets a player send them -- so this refuses to be cleverer than the game
    about that half and leaves the ordering to `fill_cheapest`.

    Never starves. With fewer than four rested heroes the whole pool comes back
    untouched, because a party of three is worse than a party of four with a
    tired hero in it, and because a campaign that cannot field anybody is one
    the Hamlet has to fix rather than one this function should refuse.
    """
    # **Bench the STRAINED first, and fall back to the afflicted line.** Two
    # gates, tried in order, so a roster that cannot field four rested heroes
    # still sends the four it has rather than refusing the week.
    for bar in (STRAINED, RESOLVE_CHECK):
        fit = [h for h in pool if h.stress < bar]
        if len(fit) >= PARTY_SIZE:
            return fit, [h for h in pool if h.stress >= bar]
    return list(pool), []


def launchable_from(store, hero_class: str, rank: int) -> int:
    """How many of the class's skills can be performed FROM `rank`, standing.

    Read off the shipped `.launch` list at each skill's top level; an empty
    launch list means "anywhere" and counts everywhere. This is the same
    derivation `tools/comp_ga.py::CompSpace.usable_from` makes for the search,
    and `tests/test_embark_formation.py` pins the two against each other on
    every vanilla class rather than trusting that they stay in step -- a
    second copy of a rule is how this repo has twice ended up with two answers.
    """
    hero = store.heroes.get(hero_class)
    if hero is None:
        return 0
    seen = 0
    for skill in hero.skills:
        if not skill.levels:
            continue
        launch = skill.levels[max(skill.levels)].launch or ()
        if not launch or rank in launch:
            seen += 1
    return seen


def launchable_equipped(store, hero, rank: int) -> Optional[int]:
    """How many of THIS hero's four equipped skills launch from `rank`.

    **`launchable_from` reads the CLASS, and a hero is not their class.** A
    level-0 hero carries four of the seven, chosen at the Guild, and two
    heroes of one class can carry different four -- Fran's own roster holds
    two Bounty Hunters, one with `uppercut` and one with `finish_him`, which
    the class-level count scores identically. Seating on the class list can
    therefore seat a hero for skills they do not have.

    Fran, 2026-09-21: *"early game (weeks under week 20) we should try to use
    heroes that already can use most of their skills in their rank"*. That is
    a question about the equipped four, so this answers it directly.

    Returns None when the save records no equipped skills, which is the
    honest answer rather than 0 -- a hero whose loadout cannot be read must
    fall back to the class count, not be scored unusable.
    """
    equipped = list(getattr(getattr(hero, "hero", hero), "selected_skills",
                            []) or [])
    if not equipped:
        return None
    cls = getattr(hero, "hero_class", "")
    record = store.heroes.get(cls)
    if record is None:
        return None
    launches = {}
    for skill in record.skills:
        if skill.levels:
            launches[skill.id] = skill.levels[max(skill.levels)].launch or ()
    seen = 0
    for sid in equipped:
        launch = launches.get(sid)
        if launch is None:            # camp skill, mod skill, unknown id
            continue
        if not launch or rank in launch:
            seen += 1
    return seen


def best_formation(store, heroes: Sequence[RosterHero]) -> list:
    """Order heroes so the party can actually act, ranks 1..n front to back.

    **The fallback used to seat by stress and call the sort order a
    formation.** Measured live 2026-09-06 on the first post-tutorial roster:
    Plague Doctor rank 1, Vestal rank 2, Crusader rank 3, Highwayman rank 4 --
    the healers in front of the front-liner, which is the arrangement
    `viable_ranks` exists to say is illegal (Vestal and Plague Doctor 2-4,
    Crusader 1-2). No curated comp is fieldable from a four-hero roster, so
    that fallback is the ONLY path the whole early campaign takes.

    Maximise the total skills launchable in place over all `n!` assignments,
    which is 24 for a party of four. Ties keep the order handed in, so the
    stress sort still decides between two equally good formations and the
    result is deterministic.

    Pushes and pulls are deliberately not counted, the same call `comp_ga`
    makes: off-rank coverage matters in proportion to the party's own
    self-movement, and the rest is a contingency rather than a plan.
    """
    import itertools

    picked = list(heroes)
    n = len(picked)
    if n < 2:
        return picked
    # **The hero's OWN four, when the save records them.** See
    # `launchable_equipped`: the class list scores two heroes of one class
    # identically even when they carry different skills, and it counts
    # skills a level-0 hero has not bought.
    def _fit(hero, rank: int) -> int:
        mine = launchable_equipped(store, hero, rank)
        if mine is None:
            return launchable_from(store, hero.hero_class, rank)
        return mine

    fit = {(i, r): _fit(picked[i], r + 1)
           for i in range(n) for r in range(n)}
    best, order = -1, tuple(range(n))
    for cand in itertools.permutations(range(n)):
        total = sum(fit[(cand[r], r)] for r in range(n))
        if total > best:
            best, order = total, cand
    return [picked[i] for i in order]


def _spend_the_remainder(plan, quest, gold: int, game_dir: str):
    """Put leftover gold into torches and food, after everything else.

    **Order matters and it is already fixed.** `docs/BRIDGE.md`: provisions
    are reserved FIRST, at PLAYBOOK 1.5's recommended end, and the skill
    budget is what is left. Topping the basket up inside `_basket_for` would
    invert that -- a maxed basket eats the whole unlock budget -- so this runs
    last, on gold nothing else claimed.

    Fran, 2026-09-21: *"we should buy extra food and torches if we have money
    on the provisions (max if possible)"*. The measurement behind it: a live
    raid bought the guide's eight torches, ran out before `rooF`, and then
    took a fight at torch 6 -- the hard floor only binds *while the pack can
    afford the light*, so an empty pack turns the torch policy off silently.
    The shop's own stock is the ceiling, so "max" is a real bound.
    """
    # **A RESERVE, because "max if possible" is not "all of it".** Measured
    # live 2026-09-22: this took the wallet 5,070 -> 20, which is 99.6% of
    # the estate's gold into one expedition's food and torches. The next week
    # could then buy no skill unlock (1,000), no stress treatment (1,000 to
    # 1,250) and no recruit, and a retreat -- which the survival layer calls
    # for often at Resolve 0 -- threw the lot away. Fran: *"never overwaste
    # money on heroes until you have some extra money outside of the money on
    # provisions ... we should use a threshold to not overspend the money
    # there"*.
    #
    # The floor is one skill unlock plus one treatment, which is the cheapest
    # week that still improves the roster. The GUIDE basket is never cut by
    # this -- only the top-up is -- so a reserve can never make a party go
    # out under-provisioned; it only stops the surplus being spent.
    left = gold - plan.basket_cost - plan.unlocks_cost - TOP_UP_RESERVE
    if left <= 0:
        return plan
    topped = provisions.top_up(plan.basket, plan.basket_cost + left,
                               quest.length, game_dir)
    topped = fill_open_stacks(plan.basket, topped)
    if topped == plan.basket:
        return plan
    extra = {i: topped[i] - plan.basket.get(i, 0) for i in topped
             if topped[i] > plan.basket.get(i, 0)}
    cost = provisions.cost(topped, game_dir)
    note = ("spare gold spent on "
            + ", ".join(f"+{n} {i}" for i, n in sorted(extra.items()))
            + " -- up to what the shop stocks")
    return replace(plan, basket=topped, basket_cost=cost,
                   notes=tuple(plan.notes) + (note,))


#: The pack's stack size per top-up item (`inventory.item_defs`: food 12,
#: torch 8). Restated so the embark plan does not parse item files.
TOP_UP_STACKS = {"food": 12, "torch": 8}


def fill_open_stacks(basket: dict, topped: dict,
                     stacks: dict = TOP_UP_STACKS) -> dict:
    """The top-up, limited to the stacks the basket already opens.

    **A surplus stack costs a pack slot for the whole raid.** Measured
    2026-09-27: the loop went out with "+6 food, +9 torch" on top of the
    guide basket -- 18 food in two slots and 18 torches in three -- and then
    met the game's "no room" modal about 480 times across 40 raid logs,
    walking away from curio and battle loot. Filling the partial stack keeps
    the extra light and food and never opens a slot for it.
    """
    out = dict(topped)
    for item, size in stacks.items():
        have = int(basket.get(item, 0) or 0)
        want = int(topped.get(item, have) or 0)
        if want <= have:
            continue
        ceiling = -(-have // size) * size if have else 0
        out[item] = max(have, min(want, ceiling))
    return out


#: The most of the gold on hand an embark may spend on skill unlocks.
UNLOCK_GOLD_FRACTION = 0.10

#: How many further draws `veto` may reject before the first draw stands.
VETO_REDRAWS = 5


def plan_embark(town: TownState, store, profile, rng=None,
                game_dir: str = str(GAME_DIR),
                level: str = provisions.RESERVE,
                veto=None) -> Optional[EmbarkPlan]:
    """Decide a whole embark, then spend what is left on light and food.

    The decision itself is `_decide_embark`; this is the one seam where the
    REMAINDER is known -- after the basket is reserved and the skill unlocks
    are chosen -- which is the only point at which "spare gold" means
    anything. See `_spend_the_remainder`.
    """
    plan = _decide_embark(town, store, profile, rng, game_dir, level, veto)
    if plan is None or plan.quest is None:
        return plan
    return _spend_the_remainder(plan, plan.quest, plan.gold, game_dir)


def _decide_embark(town: TownState, store, profile, rng=None,
                   game_dir: str = str(GAME_DIR),
                   level: str = provisions.RESERVE,
                   veto=None) -> Optional[EmbarkPlan]:
    """Decide a whole embark. `None` only when no quest here is playable."""
    rng = rng or random.Random()
    notes: list[str] = []

    quest = pick_quest(town)
    if quest is None:
        return None

    gold = int(town.wallet.get("gold", 0))
    # Reserved FIRST, and deliberately WITHOUT the party: the free per-class
    # provisions depend on which comp is picked, and the comp is picked out of
    # what this reservation leaves. Reserving the un-discounted amount is the
    # safe direction -- `_basket_for` applies the grant once the party exists,
    # so the real spend can only come in under this.
    basket = provisions.basket(quest.dungeon, quest.length, level, game_dir)
    basket_cost = provisions.cost(basket, game_dir)
    if basket_cost > gold:
        basket = provisions.afford(basket, gold, game_dir)
        basket_cost = provisions.cost(basket, game_dir)
        notes.append(f"basket cut to fit {gold} gold -- see provisions.CUT_ORDER")
    budget = gold - basket_cost
    # **At most UNLOCK_GOLD_FRACTION of the gold on hand goes on skills**
    # (Fran, 2026-09-25). A random comp per raid bought 4-5k of Guild skills
    # every embark against raid income that averaged less, and the wallet
    # went 17,265 -> 1,185 in one night.
    cap = int(UNLOCK_GOLD_FRACTION * gold)
    if budget > cap:
        notes.append(f"skill budget capped at {cap}g "
                     f"({UNLOCK_GOLD_FRACTION:.0%} of {gold}g)")
        budget = cap
    # Skills are unlocked at the Guild, which opens after 3 quests
    # (`townplan.BUILDING_GATES`): before then the party goes with what it wears.
    from bridge.townplan import closed                       # noqa: PLC0415
    if "guild" in closed(town, game_dir) and budget > 0:
        notes.append("the Guild is not open yet: no skill unlocks")
        budget = 0

    pool = town.eligible(quest)
    if quest.quest_type == "kill_boss" and BOSS_MAX_UNDERLEVELED == 0:
        # The boss rule binds the party SEATED, not only `quest_in_reach`'s
        # top four: the comp draw seats the cheapest heroes, and fielded
        # level-0 fodder at the Necromancer (campaign sim, 2026-10-10).
        pool = [h for h in pool if h.resolve_level >= _boss_level(quest)]
    if len(pool) < PARTY_SIZE:
        notes.append(f"only {len(pool)} heroes are free and under the resolve cap")
    pool, benched = _rested(pool)
    if benched:
        notes.append(f"benched {len(benched)} hero(es) at or past the Resolve "
                     f"Check: " + ", ".join(f"{h.hero_class} {h.stress:.0f}"
                                            for h in benched))

    # Every curated comp this roster can seat, with what it would cost to give
    # each hero the comp's own four skills.
    fieldable = fieldable_comps(town, store, profile, quest, game_dir,
                                pool=pool)
    if quest.quest_type != "kill_boss" and quest_in_reach(town, quest):
        # The same rule binds the party SEATED as `quest_in_reach`'s top
        # four ("optionally 1 underleveled hero"): the draw seated level-0
        # fodder on Veteran quests, and those raids were 46% of the
        # campaign's deaths (campaign sim, 2026-10-11). Only when the
        # roster CAN meet it; a green roster's fallback is untouched. When no
        # comp meets it, the comps seating the FEWEST under-level heroes are
        # kept (stepping down a difficulty instead was worse: E13).
        need = _quest_level(quest)
        shorts = [_shortfall([st.hero.resolve_level for st in c[1]], need)
                  for c in fieldable]
        limit = max(MAX_UNDERLEVELED, min(shorts, default=0))
        at_level = [c for c, k in zip(fieldable, shorts) if k <= limit]
        if at_level and len(at_level) < len(fieldable):
            notes.append(f"{len(fieldable) - len(at_level)} comp(s) dropped: "
                         f"more than {limit} hero(es) under level {need}")
            fieldable = at_level
    if quest.quest_type == "kill_boss" and fieldable:
        # **A boss party is the highest level the quest allows** (Fran,
        # 2026-10-12: *"Boss parties should try to have the max available
        # level of heroes if possible, for example for apprentice missions go
        # with fully equipped level 2 heroes if possible"*). The quest's
        # resolve cap is the most a seat may be; the comps whose seats sum
        # highest under it are kept, so a level-2 four beats a level-1 four
        # whenever the roster has one. "If possible": a roster without them
        # still goes at the quest level, as before.
        cap = town.resolve_cap(quest.difficulty)
        totals = [sum(min(st.hero.resolve_level, cap) for st in c[1]) for c in fieldable]
        top = [c for c, t in zip(fieldable, totals) if t == max(totals)]
        if len(top) < len(fieldable):
            notes.append(f"{len(fieldable) - len(top)} comp(s) dropped: a boss "
                         f"goes at the highest levels the roster has (cap {cap})")
            fieldable = top

    if not fieldable:
        # Nothing curated fits the roster at all. Send the four healthiest and
        # buy nothing -- a plan is always produced, and this one says why.
        healthiest = sorted(
            pool, key=lambda h: (h.stress, -h.resolve_level))[:PARTY_SIZE]
        seats = tuple(Seat(rank=i + 1, hero=h) for i, h in
                      enumerate(best_formation(store, healthiest)))
        notes.append("no curated comp is fieldable from the free roster; "
                     "took the four lowest-stress heroes, stood them where "
                     "their skills launch from, and bought nothing")
        basket, basket_cost = _basket_for(quest, level, game_dir, seats, gold, notes)
        return EmbarkPlan(quest=quest, comp="(no comp)", party=seats,
                          basket=basket, basket_cost=basket_cost,
                          gold=gold, notes=tuple(notes))

    affordable = [c for c in fieldable if c[2] <= budget]
    if affordable:
        # THE CHOICE IS A COIN FLIP among the ones that work. Fran's ask, and
        # the reason there is no rollout in this module.
        # **Weighted toward FEWER unlocks** (Fran, 2026-09-25): still a flip,
        # but a comp this roster can field as it stands is drawn far more
        # often than one that needs the Guild. Weight 1 / (1 + unlocks)^2.
        # Times `party_weight`: invested, rested, one band (2026-09-26).
        weights = [party_weight(c[1])
                   / (1 + sum(len(st.unlocks) + (len(st.lacks) if KIT_AWARE else 0)
                              for st in c[1])) ** 2
                   for c in affordable]
        name, seats, cost = rng.choices(affordable, weights=weights)[0]
        notes.append(f"{len(affordable)} of {len(fieldable)} fieldable curated "
                     f"comps fit the {budget}g skill budget; picked at random, "
                     f"weighted toward fewer unlocks and invested, rested, "
                     f"one-band parties")
        # **The flip stays; a draw the raid layer would abandon is re-drawn.**
        # `veto(quest, seats)` is `bridge/embark_veto.py` -- it asks the same
        # simulator the survival layer asks mid-raid, and returns a reason
        # when the drawn party is expected to lose half its heroes. Not a
        # ranking: the first draw that passes is taken, in flip order.
        if veto is not None:
            order = [c for c in affordable if c[0] != name]
            rng.shuffle(order)
            for cand in [(name, seats, cost)] + order[:VETO_REDRAWS]:
                why = veto(quest, cand[1])
                if not why:
                    name, seats, cost = cand
                    break
                notes.append(f"VETO {cand[0]}: {why}")
            else:
                notes.append("every re-draw was vetoed too; keeping the first "
                             "draw, and the raid layer will price it")
        basket, basket_cost = _basket_for(quest, level, game_dir, seats, gold, notes)
        return EmbarkPlan(quest=quest, comp=name, party=seats, basket=basket,
                          basket_cost=basket_cost, unlocks_cost=cost,
                          gold=gold, notes=tuple(notes))

    # Fran: "or other if i dont have any combination". Take the cheapest comp
    # and equip as much of it as the budget reaches.
    name, seats, cost = min(fieldable, key=lambda c: (c[2], c[0]))
    trimmed, spend = _trim_to_budget(seats, budget)
    trimmed = _reseat_unbought(trimmed, pool)
    short = [s.hero.hero_class for s, t in zip(seats, trimmed) if s.unlocks and not t.unlocks]
    notes.append(f"no curated comp fits the {budget}g skill budget "
                 f"(cheapest of {len(fieldable)} is {cost}g); took it part-equipped")
    if short:
        notes.append("keeping their own skills: " + ", ".join(short))
    basket, basket_cost = _basket_for(quest, level, game_dir, trimmed, gold, notes)
    return EmbarkPlan(quest=quest, comp=name, party=trimmed, basket=basket,
                      basket_cost=basket_cost, unlocks_cost=spend,
                      gold=gold, notes=tuple(notes))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--profile", default=None)
    ap.add_argument("--game-dir", default=str(GAME_DIR))
    ap.add_argument("--seed", type=int, default=None,
                    help="pin the comp coin flip")
    ap.add_argument("--level", default=provisions.RESERVE,
                    choices=("minimum", "recommended"))
    ap.add_argument("--all", action="store_true",
                    help="also list every fieldable curated comp and its bill")
    args = ap.parse_args()

    from data.store_cache import load_store_cached

    town = read_town_state(args.profile)
    profile = _profile_dir(args.profile)
    store = load_store_cached(str(Path(__file__).resolve().parent.parent), args.game_dir)

    plan = plan_embark(town, store, profile, random.Random(args.seed),
                       args.game_dir, args.level)
    if plan is None:
        print("No simulable quest on the map "
              "(the courtyard and the Darkest Dungeon have no encounter tables here).")
        return 1
    print(plan.report())

    if args.all:
        print()
        print("every fieldable curated comp:")
        rows = fieldable_comps(town, store, profile, plan.quest, args.game_dir)
        for cost, name, seats in sorted(
                (c, n, s) for n, s, c in rows):
            classes = "/".join(s.hero.hero_class for s in seats)
            bad = [b for s in seats
                   for b in unknown_skills(s.hero, s.skills, args.game_dir)]
            flag = f"  !! not in the class tree: {bad}" if bad else ""
            print(f"  {cost:6d}g  {name[:34]:34s} {classes}{flag}")
    return 0


def _profile_dir(profile) -> Path:
    from bridge.live_state import find_profile
    return find_profile(str(profile) if profile else None)


if __name__ == "__main__":
    raise SystemExit(main())
