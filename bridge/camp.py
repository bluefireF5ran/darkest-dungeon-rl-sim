"""Camping policy — PLAYBOOK 2.5: camp now, or push on and keep the firewood?

Two questions, and only the first is new. **What** to spend the 12 points on is
already solved by `engine/camping.py`, which greedily buys the best recovery per
point out of the party's own equipped skills; this module reuses it verbatim
rather than growing a second copy that can drift. **When** to burn a campfire is
the decision the simulator never had to make, because it camps on a fixed
schedule.

## The timing rule, and why it needs no invented threshold

PLAYBOOK 2.5: camp mid-run and mostly to shed the stress already carried; if
stress is fine, push further and camp at the *end* as a pseudo-abbey, so the
party is ready for a back-to-back expedition.

**Two gates, both borrowed rather than invented.**

1. *Is there anything to recover?* The campfire must deliver actual HP or stress,
   not just buffs. Zero is zero, so this gate needs no threshold.
2. *Is this the scheduled fire?* `ExpeditionSequenceEnv._camp_schedule` already
   spaces `F` fires evenly over `N` combats — the simulator's own answer to "mid
   run" — so the bridge reuses it instead of growing a second schedule. Combined
   with `FIREWOOD_PER_BAND` (0 short / 1 medium / 2 long, real data) and the
   save's own combats-fought count, "camp at the halfway point" falls out for a
   medium quest with nothing tuned.

**Two earlier rules were wrong and the tests caught both**, which is worth
recording so neither comes back:

- *Carried stress vs the stress a campfire could shed.* The ceiling was measured
  with HP full, forcing the whole budget onto stress, while the real spend also
  buys HP and buffs. On a live capture they disagreed by a factor of four — it
  declared "none of it is wasted" over a camp that shed 15 stress of a possible
  70. The ceiling is still reported as context; it no longer decides.
- *Whether the spender exhausts the 12-point budget.* A **fully healthy party
  still spends all 12**, on buffs, because `W_BUFF` is positive. Budget
  exhaustion measures nothing.

The pseudo-abbey clause is the exception to both: on the **last** firewood with
the expedition nearly over, camp anyway. The stress it sheds is not for this run.

## What is real data here and what is not

Real: the 12-point budget and the 33% ambush chance (`shared/rules.json`), every
camp skill's cost, effects and amounts (`raid/camping/default.camping_skills.json`),
the party's equipped skills and their live HP/stress (the save), and the firewood
count — which is simply `provisions["firewood"]`, and is 0 on a short quest, so a
short run has no camp decision to make at all.

Convention, inherited and flagged as such: the spend heuristic's weights
(`W_STRESS`/`W_HP`/`URGENCY_MAX` in `engine/camping.py`). They order candidate
skills; they are not game data. This module adds no new ones.

**The ambush is a real cost and is reported, not priced.** Camping carries a 33%
ambush chance, and how a player trades that against recovery is a judgement no
shipped file states — the same reason `bridge/route.py` prices detours without
valuing them.
"""

from __future__ import annotations

import copy
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data.camping_loader import FIREWOOD_PER_BAND
from data.types import DataStore
from engine.camping import resolve_camp
from engine.combatant import CombatantState
from engine.rng import SeededRNG
from env.expedition_sequence_env import ExpeditionSequenceEnv

# The stress a hero is assumed to be carrying when measuring how much relief a
# campfire could deliver. Just under the affliction threshold, so no skill that
# gates on `afflicted` changes the measurement.
PROBE_STRESS = 99.0


@dataclass(frozen=True)
class CampAction:
    hero: str
    hero_index: int
    skill_id: str
    cost: int

    def line(self) -> str:
        # The rank is not decoration. A real party fields duplicate classes —
        # this capture has two Highwaymen, each with their own first_aid — and
        # a class-only label reads as one hero using a once-per-camp skill
        # twice. Collapsing duplicate classes has been a real bug here twice.
        return f"rank {self.hero_index + 1} {self.hero:16s} {self.skill_id:24s} {self.cost} pts"


@dataclass(frozen=True)
class CampAdvice:
    should_camp: bool
    reason: str
    firewood: int
    party_stress: float
    relief_available: float
    plan: tuple[CampAction, ...] = ()
    points_spent: int = 0
    points_budget: int = 0
    ambush_chance: float = 0.0
    stress_healed: float = 0.0
    hp_healed: float = 0.0

    def line(self) -> str:
        head = "CAMP" if self.should_camp else "push on"
        return (f"{head}: {self.reason}\n"
                f"  firewood={self.firewood}  party stress={self.party_stress:.0f}  "
                f"(a campfire's stress ceiling for this party: {self.relief_available:.0f})")

    def report(self) -> str:
        out = [self.line()]
        if self.plan:
            out.append(f"  spend {self.points_spent}/{self.points_budget} points"
                       f"  (+{self.stress_healed:.0f} stress, +{self.hp_healed:.0f} HP)")
            out.extend(f"    {action.line()}" for action in self.plan)
            out.append("  (the rank is the PERFORMER; a skill's target is chosen by the "
                       "engine and not recorded)")
            out.append(f"  ambush risk {self.ambush_chance:.0%} — a real cost, not priced here")
        return "\n".join(out)


def _clone(heroes: Sequence[CombatantState]) -> list[CombatantState]:
    return [copy.deepcopy(h) for h in heroes]


def _spend(store: DataStore, heroes: Sequence[CombatantState],
           camp_skill_ids: Sequence[Sequence[str]], seed: int = 0):
    """Run the engine's own spender on a COPY and report what it did.

    A copy because `resolve_camp` applies the effects as it picks them; advice
    must not mutate the board it is advising on — the same rule that keeps
    `Rollout` off the advisor's env.
    """
    party = _clone(heroes)
    stress_before = sum(h.stress for h in party)
    hp_before = sum(h.hp for h in party)
    result = resolve_camp(SeededRNG(seed), store, party, camp_skill_ids)
    return (result, party,
            max(0.0, stress_before - sum(h.stress for h in party)),
            max(0.0, sum(h.hp for h in party) - hp_before))


def relief_available(store: DataStore, heroes: Sequence[CombatantState],
                     camp_skill_ids: Sequence[Sequence[str]]) -> float:
    """How much stress THIS party's campfire could shed if it were needed.

    Measured, not assumed: the party is copied, its stress dialled to just under
    the affliction line and its HP filled (so HP heals earn nothing and the
    budget flows to stress), and the real spender is asked what it buys.
    """
    probe = _clone(heroes)
    for hero in probe:
        hero.stress = PROBE_STRESS
        hero.hp = hero.max_hp
    _result, _party, stress_healed, _hp = _spend(store, probe, camp_skill_ids)
    return stress_healed


def next_scheduled_fire(total_combats: int, firewood_now: int,
                        length_band: Optional[str]) -> Optional[int]:
    """After how many fights the next campfire is due.

    Reuses `ExpeditionSequenceEnv._camp_schedule`, which spreads F fires evenly
    over N combats. The initial firewood comes from the quest length band
    (`FIREWOOD_PER_BAND`, real data), so how many have already been burned is
    just the difference — a mid-run save does not have to record it.
    """
    initial = FIREWOOD_PER_BAND.get(length_band or "", firewood_now)
    initial = max(initial, firewood_now)
    slots = sorted(ExpeditionSequenceEnv._camp_schedule(total_combats, initial))
    burned = max(0, initial - firewood_now)
    return slots[burned] if burned < len(slots) else None


def advise_camp(store: DataStore, heroes: Sequence[CombatantState],
                camp_skill_ids: Sequence[Sequence[str]],
                firewood: int,
                combats_left: Optional[int] = None,
                combats_done: int = 0,
                length_band: Optional[str] = None,
                seed: int = 0) -> CampAdvice:
    """Camp here, or carry the firewood further?"""
    living = [h for h in heroes if h.is_alive()]
    party_stress = sum(h.stress for h in living)
    budget = int(store.rules.camp_start_camping_points)
    ambush = float(store.rules.ambush_camping_base_chance)

    if firewood <= 0:
        return CampAdvice(
            should_camp=False, reason="no firewood — short quests carry none",
            firewood=0, party_stress=party_stress, relief_available=0.0,
            points_budget=budget, ambush_chance=ambush)

    relief = relief_available(store, heroes, camp_skill_ids)
    result, _after, stress_healed, hp_healed = _spend(store, heroes, camp_skill_ids, seed)
    plan = tuple(CampAction(hero=heroes[i].definition_id, hero_index=i,
                            skill_id=sid,
                            cost=int(store.camping_skills[sid].cost))
                 for i, sid in result.skills_used if sid in store.camping_skills)

    last_fire = firewood <= 1
    nearly_done = combats_left is not None and combats_left <= 1
    recovers = stress_healed + hp_healed > 0
    total = combats_done + (combats_left or 0)
    due = next_scheduled_fire(total, firewood, length_band) if total else None

    if last_fire and nearly_done:
        reason = ("last firewood and the run is nearly over — camp as a pseudo-abbey; "
                  "the stress it sheds is for the NEXT expedition")
        should = True
    elif not recovers:
        reason = (f"the fire would buy only buffs (+0 stress, +0 HP) — the party has "
                  f"nothing to recover yet, push on")
        should = False
    elif due is not None and combats_done < due:
        reason = (f"the next campfire is due after fight {due} and only {combats_done} "
                  f"are done — push on and camp fuller "
                  f"(it would shed {stress_healed:.0f} stress, {hp_healed:.0f} HP now)")
        should = False
    else:
        when = f"due after fight {due}, {combats_done} done" if due is not None             else "no schedule available"
        reason = (f"{when}; the fire delivers +{stress_healed:.0f} stress and "
                  f"+{hp_healed:.0f} HP")
        should = True

    return CampAdvice(
        should_camp=should, reason=reason, firewood=firewood,
        party_stress=party_stress, relief_available=relief,
        plan=plan, points_spent=result.points_spent, points_budget=budget,
        ambush_chance=ambush, stress_healed=stress_healed, hp_healed=hp_healed)


def advise_from_live(state, store: DataStore, combats_left: Optional[int] = None,
                     length_band: Optional[str] = None) -> CampAdvice:
    """Camp advice straight off a `LiveState`."""
    from bridge.seed import build_party_from_live

    engine = build_party_from_live(state, store)
    camp_skill_ids = [list(h.camping_skills) for h in state.heroes]
    firewood = int((state.provisions or {}).get("firewood", 0) or 0)
    left = combats_left if combats_left is not None else state.remaining_battles
    return advise_camp(store, engine.heroes, camp_skill_ids, firewood, left,
                       combats_done=state.battles_completed or 0,
                       length_band=length_band)


def main() -> None:
    import argparse
    from bridge.live_state import read_live_state
    from data.data_loader import DataLoader

    ap = argparse.ArgumentParser(description="camp here, or carry the firewood further?")
    ap.add_argument("--raid", required=True, help="a persist.raid.json (a COPY)")
    ap.add_argument("--roster", default=None, help="the paired persist.roster.json")
    ap.add_argument("--firewood", type=int, default=None,
                    help="override the firewood count (a short quest carries none)")
    ap.add_argument("--combats-left", type=int, default=None)
    args = ap.parse_args()

    roster = args.roster or args.raid.replace("raid.json", "roster.json")
    state = read_live_state(raid_file=args.raid, roster_file=roster)
    from data.paths import CSV_DIR, GAME_DIR
    store = DataLoader.load_all(str(CSV_DIR), str(GAME_DIR))

    if args.firewood is None:
        advice = advise_from_live(state, store, args.combats_left)
    else:
        from bridge.seed import build_party_from_live
        engine = build_party_from_live(state, store)
        advice = advise_camp(store, engine.heroes,
                             [list(h.camping_skills) for h in state.heroes],
                             args.firewood, args.combats_left)
    print(advice.report())


if __name__ == "__main__":
    main()
