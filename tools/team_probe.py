"""Do the four heroes play as a PARTY, or as four heroes who happen to share a
screen?

Fran, 2026-09-02, after watching the actuator play real dungeons:

    Each hero plays its own game rather than thinking like a team it seems,
    sometimes it feels that each hero targets its own individual objective
    rather than playing with the whole build

    Combat plays really risky lines in my opinion, it true that sustain is less
    relevant, but sometimes it prefers go all out on damage rather than stunning
    for example, in my opinion it outvalues just raw numbers (high damage/heal
    actions) and infravalues the more support skills

Nothing in this repo measured either claim. `tools/style_probe.py` grades WHICH
SKILL against an expert tier list and says so itself: *"the ruler grades WHICH
SKILL and never WHICH TARGET"*. Team play is almost entirely about the target.

## The three things this measures, and why each is the honest form

**1. FOCUS -- do the heroes converge on one enemy?** Killing an enemy removes
its turns; wounding two removes none. So a party that spreads its damage is
paying full price for half the benefit, and "each hero targets its own
objective" is exactly what spreading looks like from the outside.

Measured as a **repeat rate**: of the attacks that were not the first in their
round, how many landed on an enemy a hero had ALREADY hit this round? Against a
uniform baseline accumulated **per decision** -- at each one, the chance a
random legal pick would have repeated is `|already hit and still reachable| /
|reachable|`, and those are summed rather than modelled.

**The per-decision form is not a refinement, it is the only honest one.** The
first version counted distinct enemies per round against a balls-in-bins
baseline, and that is biased by the very thing being measured: a focused party
KILLS something, and every attack after that is forced onto a different target.
Counting per decision against the set that was actually reachable at that
moment removes it -- a dead enemy is not in `reachable`, so it can neither be
repeated nor expected.

**2. FINISHING -- is a hurt enemy left standing while a healthy one is hit?**
The crispest single event behind Fran's sentence. When a hero attacks enemy X
while enemy Y was legal, alive and on strictly less HP, that is a kill deferred
in favour of a fresh target. Counted only when both were legal for the SAME
decision, so it is a choice rather than a reachability accident.

**3. CONTROL TAKEN WHEN OFFERED -- the availability control.** "It prefers
damage over stunning" cannot be read off the raw action mix, because a comp
with no stuns cannot stun. What is meaningful is how often a control skill is
taken WHEN ONE IS ON THE BAR, which is the same device `style_probe` uses for
heals and the same reason.

## This is a RULER, and it must stay one

Same standing rule as `tools/check_class_play.py` and `tools/style_probe.py`:
**nothing measured here may become a reward term, an action mask or a training
target.** A policy fitted to a focus-fire target is a scripted player, and
target selection is precisely where a scripted player stops transferring to a
class it has never seen. Use it to verify and to iterate.

    python tools/team_probe.py --model models/dd_combat_ppo_full_expedition.zip
    python tools/team_probe.py --model <ckpt> --episodes 4 --limit 24
"""
from __future__ import annotations

import argparse
import statistics
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bridge.rollout import PartyMember, Rollout
from data.store_cache import load_store_cached
from env.expedition_env import build_comp_pool, zone_id_for_location
from env.gym_env import CSV_DIR, GAME_DIR
from tools.style_probe import classify, party_of, sustain_score

# An enemy at or under this fraction of its max HP is "hurt" for the finishing
# test. A DECLARED CONVENTION: the game states no such threshold, and the
# number only has to be low enough that leaving the target standing is
# obviously the worse of two legal choices. Reported so a reader can discount
# it, and `--hurt` moves it.
HURT_FRACTION = 0.40


def _enemy_at(engine, position):
    """The living enemy standing on `position`, or None.

    **`Action.target_id` is a RANK, not a combatant id.** Enemy ids run from
    100 and ranks from 0, so matching them against each other silently records
    nothing at all -- which is exactly what the first version of this probe
    did, and why every counter but CONTROL came back empty on its first run.

    Matched against `occupied_positions` rather than `.position`, because a
    size-2+ monster answers to both of its ranks and is offered from either --
    the same rule `_find_combatant_by_position` follows.
    """
    if position is None or position < 0:
        return None
    for e in engine.enemies:
        if e.is_alive() and position in e.occupied_positions:
            return e
    return None


def play_and_watch(rollout, party, zone, args) -> tuple[Counter, float]:
    """(counters, run_score) for one comp.

    Hooks `type(env).step` the way `style_probe` and `comp_ga --explain` do, so
    what lands in the counters is the DECISION the policy made against the
    board it actually faced -- not a reconstruction from a log.
    """
    env = rollout.env
    base = env.unwrapped
    store = base.store
    counts: Counter = Counter()
    # Enemies already hit this round, to measure convergence.
    round_state = {"round": None, "hit": set()}
    # Sum of P(a uniform legal pick repeats), one term per graded decision.
    expected = {"repeat": 0.0, "left": 0.0, "control": 0.0}
    original = type(base).step

    def watching_step(self, action):
        engine = self.engine
        actor = engine.get_current_actor_id()
        mapped = self._action_map.get(action)
        if actor is not None and mapped is not None:
            hero = next((h for h in engine.heroes if h.id == actor), None)
            if round_state["round"] != engine.round:
                round_state["round"] = engine.round
                round_state["hit"] = set()

            data = None
            if hero is not None and not mapped.skill_id.startswith("__"):
                data = engine.get_skill_data(mapped.skill_id,
                                             level=hero.skill_tier)
            chosen = classify(data, mapped.skill_id, store)

            # -- 3. control offered vs taken, availability-controlled --------
            if hero is not None:
                legal = 0
                control = 0
                for idx in self._action_map:
                    other = self._action_map.get(idx)
                    if other is None or other.skill_id.startswith("__"):
                        continue
                    legal += 1
                    od = engine.get_skill_data(other.skill_id,
                                               level=hero.skill_tier)
                    if classify(od, other.skill_id, store) == "control":
                        control += 1
                if control:
                    counts["control_offered"] += 1
                    # **The share is the baseline.** "It took control 17% of
                    # the time" is not a finding until you know what fraction
                    # of the bar WAS control -- a hero with one stun among
                    # twelve legal attacks taking it 17% of the time is
                    # over-using it, not under.
                    expected["control"] += control / legal
                    if chosen == "control":
                        counts["control_taken"] += 1

            # -- 1 and 2 are about ATTACKS on a living enemy -----------------
            target = _enemy_at(engine, mapped.target_id)
            if chosen == "attack" and target is not None:
                # Every OTHER living enemy this same decision could have hit.
                reachable = {}
                for idx in self._action_map:
                    other = self._action_map.get(idx)
                    if other is None or other.skill_id.startswith("__"):
                        continue
                    od = engine.get_skill_data(other.skill_id,
                                               level=hero.skill_tier)
                    if classify(od, other.skill_id, store) != "attack":
                        continue
                    cand = _enemy_at(engine, other.target_id)
                    if cand is not None:
                        reachable[cand.id] = cand

                # -- 1. repeat rate, against a per-decision baseline ---------
                # Graded only when somebody has already attacked this round
                # and more than one target is reachable: with one option there
                # is no choice to grade, and the first attack of a round cannot
                # repeat anything.
                already = round_state["hit"] & set(reachable)
                if round_state["hit"] and len(reachable) >= 2:
                    counts["graded"] += 1
                    expected["repeat"] += len(already) / len(reachable)
                    if target.id in round_state["hit"]:
                        counts["repeated"] += 1
                round_state["hit"].add(target.id)

                # -- 2. was a hurt enemy passed over for a healthier one? ----
                if len(reachable) >= 2:
                    counts["choices"] += 1
                    hurt = [e for e in reachable.values()
                            if e.max_hp and e.hp <= args.hurt * e.max_hp]
                    if hurt:
                        counts["choices_with_hurt"] += 1
                        if all(e.id != target.id for e in hurt) and any(
                                e.hp < target.hp for e in hurt):
                            counts["left_standing"] += 1
                        # The same baseline discipline as FOCUS: how often
                        # would a uniform legal pick have left a hurt enemy
                        # standing? Without it 25% is a number with no scale --
                        # on a board where three of four reachable enemies are
                        # healthy, picking at random leaves one standing 75% of
                        # the time and 25% is excellent play.
                        hurt_ids = {e.id for e in hurt}
                        passes = sum(
                            1 for cand in reachable.values()
                            if cand.id not in hurt_ids
                            and any(e.hp < cand.hp for e in hurt))
                        expected["left"] += passes / len(reachable)
        return original(self, action)

    type(base).step = watching_step
    try:
        result = rollout.evaluate(party, zone, args.tier, args.combats,
                                  length_band=args.band, episodes=args.episodes,
                                  seed=args.seed)
    finally:
        type(base).step = original
    counts["expected_repeat"] = expected["repeat"]
    counts["expected_left"] = expected["left"]
    counts["expected_control"] = expected["control"]
    # `RolloutResult` carries per-episode `qualities`, not a `run_score`.
    quality = getattr(result, "qualities", ()) or ()
    return counts, (sum(quality) / len(quality)) if quality else 0.0


def _rate(counts, num, den):
    total = counts.get(den, 0)
    return (100.0 * counts.get(num, 0) / total) if total else float("nan")


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", default="models/dd_combat_ppo_full_expedition.zip")
    ap.add_argument("--episodes", type=int, default=4)
    ap.add_argument("--tier", type=int, default=3)
    ap.add_argument("--band", default="long", choices=("short", "medium", "long"))
    ap.add_argument("--combats", type=int, default=15)
    ap.add_argument("--zone", default=None)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--limit", type=int, default=30,
                    help="how many comps to play, spread evenly over sustain")
    ap.add_argument("--hurt", type=float, default=HURT_FRACTION,
                    help="an enemy at or under this fraction of max HP counts "
                         "as hurt for the finishing test (a convention)")
    args = ap.parse_args()

    store = load_store_cached(CSV_DIR, GAME_DIR)
    rows = []
    for name, zone, _classes in build_comp_pool(store):
        loadout = store.comp_loadout_by_name(name)
        if loadout is None or len(loadout.heroes) < 4:
            continue
        rows.append((sustain_score(loadout, store), name, zone, loadout))
    rows.sort()
    if args.limit and len(rows) > args.limit:
        step = len(rows) / args.limit
        rows = [rows[int(i * step)] for i in range(args.limit)]

    print(f"team probe | {len(rows)} comps x {args.episodes} episodes | "
          f"tier {args.tier} {args.band} x{args.combats} | seed {args.seed}")
    print(f"  hurt = at or under {args.hurt:.0%} of max HP (a declared "
          f"convention, not a game number)")

    rollout = Rollout(args.model)
    totals: Counter = Counter()
    focus_rows = []
    for sustain, name, zone, loadout in rows:
        zone_id = (args.zone or zone_id_for_location(loadout.location)
                   or zone or "warrens")
        counts, score = play_and_watch(rollout, party_of(loadout), zone_id, args)
        totals.update(counts)
        if counts.get("graded"):
            gap = 100.0 * (counts["repeated"] - counts["expected_repeat"]) \
                / counts["graded"]
            focus_rows.append((gap, name, score))

    print()
    if totals.get("graded"):
        got = 100.0 * totals["repeated"] / totals["graded"]
        want = 100.0 * totals["expected_repeat"] / totals["graded"]
        print(f"FOCUS   {got:.1f}% of follow-up attacks hit an enemy already "
              f"struck this round  (n={totals['graded']})")
        print(f"        a uniform legal pick would repeat {want:.1f}%")
        gap = got - want
        verdict = ("MORE focused than chance" if gap > 1.0
                   else "MORE SPREAD than chance" if gap < -1.0
                   else "indistinguishable from chance")
        print(f"        -> {gap:+.1f}pp, {verdict}")
    if totals.get("choices_with_hurt"):
        got = _rate(totals, "left_standing", "choices_with_hurt")
        want = 100.0 * totals["expected_left"] / totals["choices_with_hurt"]
        print(f"FINISH  {got:.1f}% of decisions with a hurt enemy in reach hit "
              f"a HEALTHIER one instead  (n={totals['choices_with_hurt']})")
        print(f"        a uniform legal pick would do that {want:.1f}% of the time")
        gap = got - want
        verdict = ("WORSE than chance at finishing" if gap > 1.0
                   else "better than chance at finishing" if gap < -1.0
                   else "indistinguishable from chance")
        print(f"        -> {gap:+.1f}pp, {verdict}")
    if totals.get("control_offered"):
        got = _rate(totals, "control_taken", "control_offered")
        want = 100.0 * totals["expected_control"] / totals["control_offered"]
        print(f"CONTROL {got:.1f}% of turns took a control skill when one was "
              f"on the bar  (n={totals['control_offered']})")
        print(f"        control was {want:.1f}% of the legal actions on those "
              f"turns")
        gap = got - want
        verdict = ("takes control MORE than its share" if gap > 1.0
                   else "takes control LESS than its share" if gap < -1.0
                   else "takes control at its share")
        print(f"        -> {gap:+.1f}pp, {verdict}")

    if focus_rows:
        focus_rows.sort()
        print()
        print("  most focused comps (repeat rate minus its own baseline):")
        for gap, name, score in focus_rows[-3:][::-1]:
            print(f"    {name[:34]:34s} {gap:+6.1f}pp  quality {score:.1f}")
        print("  most scattered comps:")
        for gap, name, score in focus_rows[:3]:
            print(f"    {name[:34]:34s} {gap:+6.1f}pp  quality {score:.1f}")
        gaps = [g for g, _n, _s in focus_rows]
        if len(gaps) > 1:
            print(f"  between-comp SD {statistics.pstdev(gaps):.1f}pp -- a "
                  f"policy running ONE plan varies little here")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
