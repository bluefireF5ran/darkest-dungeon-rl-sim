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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

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

#: Stress at which the game runs a hero's Resolve Check. `engine/stress.py`
#: carries the same 100 for combat; here it is the line between a hero who is
#: tired and one who is **already afflicted**, since an affliction lasts the
#: rest of the raid and a Resolve Check cannot be passed twice.
RESOLVE_CHECK = 100.0


@dataclass(frozen=True)
class Seat:
    """One hero, where they stand, and what it costs to give them their kit."""
    rank: int                       # 1 = front = the RIGHTMOST party slot
    hero: RosterHero
    skills: tuple[str, ...] = ()    # the comp's four, in the comp's order
    unlocks: tuple[str, ...] = ()   # of those, the ones not yet bought
    cost: int = 0

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


def pick_quest(town: TownState) -> Optional[Quest]:
    """The quest to take: shortest first, then the biggest reward.

    Shortest first is deliberate and is not a tie-break -- a fresh roster at
    resolve 0 with rank-0 gear survives a short run and does not survive a long
    one, and `EXPEDITION_LENGTH_BANDS` puts 14-16 combats in the long band
    against 3-8 in the short.
    """
    options = [q for q in town.quests if q.simulable]
    if not options:
        return None
    return min(options, key=lambda q: (q.length, -q.gold_reward, q.key))


def _seat_cost(profile, hero: RosterHero, skills: Sequence[str],
               game_dir: str) -> tuple[tuple[str, ...], int]:
    want = missing_skills(profile, hero, skills, game_dir)
    gold = unlock_cost(profile, hero, skills, game_dir)
    return want, (gold if gold is not None else 0)


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
            _, gold = _seat_cost(profile, h, skills, game_dir)
            return (gold, h.stress, -h.resolve_level)

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
                          unlocks=want, cost=gold))
    return tuple(seats), total


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
        kept[i] = Seat(rank=seat.rank, hero=seat.hero, skills=seat.skills)
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
    want = provisions.basket(quest.dungeon, quest.length, level, game_dir,
                             party=party)
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
    fit = [h for h in pool if h.stress < RESOLVE_CHECK]
    if len(fit) < PARTY_SIZE:
        return list(pool), []
    return fit, [h for h in pool if h.stress >= RESOLVE_CHECK]


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
    fit = {(i, r): launchable_from(store, picked[i].hero_class, r + 1)
           for i in range(n) for r in range(n)}
    best, order = -1, tuple(range(n))
    for cand in itertools.permutations(range(n)):
        total = sum(fit[(cand[r], r)] for r in range(n))
        if total > best:
            best, order = total, cand
    return [picked[i] for i in order]


def plan_embark(town: TownState, store, profile, rng=None,
                game_dir: str = str(GAME_DIR),
                level: str = provisions.RESERVE) -> Optional[EmbarkPlan]:
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

    pool = town.eligible(quest)
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
        name, seats, cost = rng.choice(affordable)
        notes.append(f"{len(affordable)} of {len(fieldable)} fieldable curated "
                     f"comps fit the {budget}g skill budget; picked at random")
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
