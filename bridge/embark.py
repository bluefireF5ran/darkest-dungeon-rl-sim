"""Which quest, and with whom — the first decision outside combat this project
can make.

    python bridge/embark.py --model models/dd_combat_ppo_full_expedition.zip

Reads the real Hamlet (`bridge/town_state.py`), then answers by MEASURING
rather than by scoring heuristics: each candidate party is run through the
quest in simulation N times and ranked on how often it comes back whole
(`bridge/rollout.py`).

## Where the candidate parties come from

Thirty-two free heroes choose four is 35 960 parties, which is not searchable at
a second apiece — and the obvious fix, inventing a heuristic ("needs a healer,
needs a front-line"), is exactly the kind of convention masquerading as game
knowledge this repo avoids.

So the shapes come from the 131 **real, human-designed comps** the project
already has scraped (`store.comp_loadouts`), and only the shape does: the class
list. Every hero filling it is one of Fran's own — his resolve levels, his
weapon and armour ranks, his equipped skills, camp skills, trinkets and quirks.
The comp says "Man-at-Arms / Highwayman / Highwayman / Vestal"; the save says
which Man-at-Arms, and what he is carrying.

Two consequences worth being explicit about:

* a party nobody ever wrote a comp for will never be suggested. That is a real
  limit, not a claim that such parties are bad;
* the party currently selected in game is always evaluated too, whether or not
  it matches a comp — so the answer to "is my own pick any good?" is on the
  same scale as everything else.

## Reading the output

`P(success)` is the probability the **policy** completes the quest with nobody
dead. The policy plays worse than a good human, so treat these as a ranking,
not as odds. The `±` is the standard error over the episodes actually run: two
options inside each other's error bars have not been separated.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bridge.rollout import (DEFAULT_EPISODES, QUEST_LENGTH_TO_BAND, PartyMember,
                            Rollout, RolloutResult)
from bridge.town_state import Quest, RosterHero, TownState, read_town_state
from env.expedition_sequence_env import EXPEDITION_LENGTH_BANDS

# Screening pass: few episodes over every fieldable shape, then a proper
# measurement of the survivors. A 6-episode rate has a standard error of ~20
# points, which is far too noisy to RANK on but plenty to drop the parties that
# wipe most of the time.
SCREEN_EPISODES = 6
DEFAULT_FINALISTS = 5


@dataclass
class Candidate:
    name: str
    party: list[PartyMember]
    heroes: list[RosterHero]
    result: Optional[RolloutResult] = None

    def roster_line(self) -> str:
        return " / ".join(h.label() for h in self.heroes)


def combats_for(quest: Quest) -> tuple[str, int]:
    """The length band this quest is, and how many fights to simulate.

    The band comes from the quest's own `length` field via the game's own length
    names, never from guessing at a combat count — it decides firewood (0 short
    / 1 medium / 2 long) and the provision multiplier. The count is the midpoint
    of that band's real range from `scripts/map_generator.darkest`, which is the
    neutral choice when the actual map has not been generated yet."""
    band = QUEST_LENGTH_TO_BAND.get(quest.length, "medium")
    lo, hi = EXPEDITION_LENGTH_BANDS[band]
    return band, round((lo + hi) / 2)


def _to_member(h: RosterHero) -> PartyMember:
    """A roster hero as the simulator's party member. HP is deliberately left at
    "full": the game restores it on returning to the Hamlet, and the save's
    `current_hp` in town is stale from the last raid (1.0 for anyone never
    deployed). Stress is the opposite and is carried."""
    return PartyMember(
        hero_class=h.hero_class,
        resolve_level=h.resolve_level,
        weapon_rank=h.hero.weapon_rank,
        armour_rank=h.hero.armour_rank,
        skill_ids=tuple(h.hero.selected_skills),
        camp_skills=tuple(h.hero.camping_skills),
        trinkets=tuple(h.hero.trinkets),
        quirks=tuple(h.hero.quirks),
        stress=int(round(h.stress)),
        mortality_tier=("heart_attack" if h.hero.has_had_heart_attack
                        else "mortality" if h.hero.visited_deaths_door else "none"),
        affliction_id=h.hero.affliction_id,
        virtue_id=h.hero.virtue_id,
    )


def fill_shape(shape: Sequence[str], pool: list[RosterHero]) -> Optional[list[RosterHero]]:
    """Field a class list from the available roster, or None if it cannot be.

    Heroes are consumed as they are picked, so a shape asking for two
    Highwaymen gets two DIFFERENT ones. Within a class the pick is lowest stress
    first, then highest resolve — stress is the constraint the Hamlet actually
    imposes, and it does not heal in a dungeon."""
    remaining = list(pool)
    chosen: list[RosterHero] = []
    for hero_class in shape:
        options = [h for h in remaining if h.hero_class == hero_class]
        if not options:
            return None
        pick = min(options, key=lambda h: (h.stress, -h.resolve_level))
        remaining.remove(pick)
        chosen.append(pick)
    return chosen


def candidate_shapes(store, quest: Quest) -> list[tuple[str, list[str]]]:
    """(name, class list) per scraped comp that this repo can actually field.

    Modded-hero comps are excluded outright rather than quietly fielded as a
    smaller party — the same rule the eval uses. Class identity, order and count
    all come from `comp_loadouts`, never from the CSV `heroes` field, which is
    alphabetised and deduplicates repeated classes."""
    shapes = []
    for name, loadout in store.comp_loadouts.items():
        classes = [h.hero_id for h in loadout.heroes if h.hero_id in store.heroes]
        # `declared_heroes`, not `len(loadout.heroes)`: the loader only KEEPS
        # slots that resolved, so comparing against it was a no-op that could
        # never fire. The declared count is what the source JSON asked for, so
        # this is the test that actually catches a comp fielding a class this
        # project cannot load. Replaced the `COMPS/Comps/*.md` name list on
        # 2026-08-23 -- structural, and with no external file to keep in sync.
        if len(classes) < loadout.declared_heroes or not 2 <= len(classes) <= 4:
            continue
        shapes.append((name, classes))
    return shapes


def current_party(town: TownState) -> list[RosterHero]:
    """Whoever is selected on the embark screen right now (`roster.status` 1)."""
    return [h for h in town.heroes if h.in_party]


def build_candidates(store, town: TownState, quest: Quest) -> list[Candidate]:
    pool = town.eligible(quest)
    out: list[Candidate] = []
    seen: set[tuple[int, ...]] = set()

    picked = current_party(town)
    cap = town.resolve_cap(quest.difficulty)
    if picked and all(h.available or h.in_party for h in picked) \
            and all(h.resolve_level <= cap for h in picked):
        key = tuple(sorted(h.guid for h in picked))
        seen.add(key)
        out.append(Candidate("YOUR CURRENT PARTY", [_to_member(h) for h in picked], picked))

    for name, shape in candidate_shapes(store, quest):
        filled = fill_shape(shape, pool)
        if filled is None:
            continue
        # Two comps that resolve to the same four heroes are the same option;
        # the shapes differ only in a loadout the real heroes override anyway.
        key = tuple(sorted(h.guid for h in filled))
        if key in seen:
            continue
        seen.add(key)
        out.append(Candidate(name, [_to_member(h) for h in filled], filled))
    return out


def rank_for_quest(rollout: Rollout, town: TownState, quest: Quest,
                   episodes: int, finalists: int) -> list[Candidate]:
    """Screen every fieldable party cheaply, then measure the survivors properly.

    The screen's own numbers are never reported — a 6-episode rate cannot rank
    anything — it only decides who gets measured."""
    band, num_combats = combats_for(quest)
    candidates = build_candidates(rollout.store, town, quest)
    if not candidates:
        return []

    for cand in candidates:
        cand.result = rollout.evaluate(
            cand.party, zone_id=quest.dungeon, tier=quest.difficulty,
            num_combats=num_combats, length_band=band, episodes=SCREEN_EPISODES)

    keep = sorted(candidates, key=lambda c: -c.result.success_rate)[:max(1, finalists)]
    # The current party is measured whatever the screen said about it — the
    # point is to tell Fran where his own pick lands, including when it lands
    # badly.
    for cand in candidates:
        if cand.name.startswith("YOUR") and cand not in keep:
            keep.append(cand)

    for cand in keep:
        cand.result = rollout.evaluate(
            cand.party, zone_id=quest.dungeon, tier=quest.difficulty,
            num_combats=num_combats, length_band=band, episodes=episodes, seed=1)
    return sorted(keep, key=lambda c: -c.result.success_rate)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--model", default="models/dd_combat_ppo_full_expedition.zip",
                    help="EvalCallback's best checkpoint, not the final-step save in models/")
    ap.add_argument("--profile", default=None)
    ap.add_argument("--episodes", type=int, default=DEFAULT_EPISODES,
                    help="episodes per finalist party")
    ap.add_argument("--finalists", type=int, default=DEFAULT_FINALISTS,
                    help="how many screened parties get measured properly")
    ap.add_argument("--quest", default=None,
                    help="only this dungeon (e.g. weald)")
    args = ap.parse_args()

    town = read_town_state(args.profile)
    # `simulable` alone, NOT `is_generated`. A plot quest in one of the four
    # regions has a real encounter table -- `plot_tutorial_crypts` is the Ruins
    # quest a fresh campaign opens with, and it was the ONLY quest on the map
    # the day this filter was found. `is_generated` refused it and printed
    # "no simulable quest" on a map holding one. What `simulable` excludes is
    # the courtyard and the Darkest Dungeon, which is the real distinction.
    quests = [q for q in town.quests if q.simulable]
    if args.quest:
        quests = [q for q in quests if q.dungeon == args.quest]
    if not quests:
        print("No simulable quest on the map "
              "(the courtyard and the Darkest Dungeon have no encounter tables here).")
        return 1

    free = sum(1 for h in town.heroes if h.available)
    print(f"Roster: {len(town.heroes)} heroes, {free} free | "
          f"{town.wallet.get('gold', 0)} gold")
    print("P(success) = the POLICY completes the quest with nobody dead. "
          "It plays worse than you —")
    print("read these as a ranking, not as odds. ± is the standard error over "
          f"{args.episodes} episodes.")

    rollout = Rollout(args.model)
    best_per_quest: list[tuple[Quest, Candidate]] = []

    for quest in quests:
        band, num_combats = combats_for(quest)
        cap = town.resolve_cap(quest.difficulty)
        print()
        print(f"=== {quest.label()} — {band} band, {num_combats} combats simulated ===")
        print(f"    resolve cap {cap} | reward {quest.gold_reward} gold | "
              f"{len(town.eligible(quest))} eligible heroes")
        ranked = rank_for_quest(rollout, town, quest, args.episodes, args.finalists)
        if not ranked:
            print("    no party can be fielded from the free roster")
            continue
        for cand in ranked:
            print(f"  {cand.name[:34]:34s} {cand.result.line()}")
            print(f"  {'':34s} {cand.roster_line()}")
        best_per_quest.append((quest, ranked[0]))

    if len(best_per_quest) > 1:
        print()
        print("Best option per quest, ranked:")
        for quest, cand in sorted(best_per_quest, key=lambda p: -p[1].result.success_rate):
            print(f"  {quest.label():48s} {cand.result.success_rate * 100:5.1f}%  "
                  f"{cand.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
