"""Does the policy play the COMP, or does it always play the same plan?

Fran, 2026-08-28, rejecting the idea that the comp search's healing bias comes
from its scenario:

    "The hallway fights should still show what i talk about with great and
    better play... taking bosses to the mix wont really solve the issue, this
    have more deep roots, but to be fair, our model IS good, but only with high
    sustain comps, aka, knows how to minimize damage or outheal enemies."

That is a claim about the POLICY, not the scenario, and it gates everything
else: if the model has one plan, then the search's fitness is
`P(this one-plan policy finishes)` and no change to the search SPACE fixes the
comps it prefers.

## What this measures

Comps are bucketed by how much sustain their EQUIPPED kit carries -- not by
which classes they field, because a Vestal who did not bring a heal is not a
healer, the same rule `_hero_capabilities` follows. Then each comp is played and
every decision the policy makes is recorded, by hooking the env's own
`_action_map` exactly as `comp_ga --explain` does, so this records the CHOICE
rather than re-deriving it.

## Reading the result

    action mix roughly CONSTANT across buckets
        -> one plan. Low-sustain comps lose while being played wrong, and the
           work is in training, not in the search space.

    action mix ADAPTS, low-sustain still scores worse
        -> the policy plays to the comp and those comps are genuinely weaker on
           this ruler. The scenario question reopens, on evidence.

The prior evidence is split, which is why this is measured rather than argued:
`CLAUDE.md`'s Vestal finding has sustain lifting the pool MEAN (74.2 vs 60.6)
but not the CEILING (86.6 vs 85.3), and a one-plan policy predicts a lifted
ceiling too.

**This is a RULER, like `tools/check_class_play.py`.** Nothing it measures may
become a reward term, an action mask or a training target -- a policy fitted to
an action-mix target is precisely the scripted player the mechanics-not-identity
work exists to avoid.

    python tools/style_probe.py --model models/dd_combat_ppo_full_expedition.zip
    python tools/style_probe.py --model <ckpt> --episodes 8 --limit 60
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
from tools.skill_tiers import (TIER_LABEL, TIER_ORDER, TIER_RANK, coverage,
                               explanations, tier_index)

# The decision taxonomy. Deliberately about INTENT rather than about the skill's
# name: "did the policy attack, sustain, control or reposition" is the question
# "one plan or a player" turns on.
CATEGORIES = ("attack", "control", "heal", "support", "move", "pass", "other")


def classify(skill_data, skill_id: str, store) -> str:
    """One chosen action -> one category.

    `skill_data` is the `SkillLevelData` the engine resolved for the performer,
    so a skill is judged at the tier it was actually used at.
    """
    if skill_id in ("__pass__",):
        return "pass"
    if skill_id in ("__move__",):
        return "move"
    if skill_id in ("__retreat__", "__abandon__"):
        return "other"
    if skill_data is None:
        return "other"

    # Ally- or self-directed. `targets_allies` is the `@` prefix; a SELF-only
    # skill carries an empty `.target` and no prefix, so both tests are needed
    # -- keying on the flag alone files Solemnity and Lick Wounds under FOE.
    friendly = skill_data.targets_allies or not skill_data.target
    heals = skill_data.heal_min is not None
    stress_heals = False
    stuns = False
    for name in list(skill_data.effects or []) + list(skill_data.self_effects or []):
        eff = store.effects.get(name)
        if eff is None:
            continue
        if eff.heal_min is not None or eff.heal_percent or eff.heal_dot_amount:
            heals = True
        if eff.stress_heal:
            stress_heals = True
        if eff.stun_chance is not None:
            stuns = True

    if friendly:
        return "heal" if (heals or stress_heals) else "support"
    # Aimed at the enemy. A skill that cannot deal damage is control, however
    # it is spelled -- Blinding Gas is `.dmg -100%`, and a stun is control even
    # when it also hits.
    if skill_data.dmg_mod <= -1.0 or (stuns and skill_data.dmg_mod < 0):
        return "control"
    return "attack"


def sustain_score(loadout, store, tier: int = 4) -> float:
    """How much healing the party's EQUIPPED kit can deliver, per combat.

    Summed over heroes rather than max-pooled: two heroes who can each heal is
    the thing that makes a comp a sustain comp, and pooling would call that the
    same as one. Stress healing counts -- it is what stops a Resolve Check, and
    Fran's objective names zero stress explicitly.
    """
    total = 0.0
    for hero in loadout.heroes:
        if not hero.hero_id:
            continue
        hero_def = store.heroes.get(hero.hero_id)
        if hero_def is None:
            continue
        ids = list(hero.skill_ids) or [s.id for s in hero_def.skills if s.id != "move"]
        for sid in ids:
            skill = next((s for s in hero_def.skills if s.id == sid), None)
            if skill is None:
                continue
            data = skill.levels.get(tier) or next(iter(skill.levels.values()), None)
            if data is None:
                continue
            if not (data.targets_allies or not data.target):
                continue          # a lifesteal heals the user, not the party
            if data.heal_min is not None:
                total += (data.heal_min + (data.heal_max or data.heal_min)) / 2.0
            for name in list(data.effects or []) + list(data.self_effects or []):
                eff = store.effects.get(name)
                if eff is None:
                    continue
                if eff.heal_min is not None:
                    total += (eff.heal_min + (eff.heal_max or eff.heal_min)) / 2.0
                if eff.heal_percent:
                    total += 10.0 * float(eff.heal_percent)
                if eff.heal_dot_amount:
                    total += float(eff.heal_dot_amount) * (eff.heal_dot_rounds or 1)
                if eff.stress_heal:
                    total += float(eff.stress_heal)
    return total


def party_of(loadout):
    return [PartyMember(hero_class=h.hero_id,
                        skill_ids=tuple(h.skill_ids),
                        trinkets=tuple(h.trinket_ids),
                        quirks=tuple(h.quirk_ids["positive"])
                        + tuple(h.quirk_ids["negative"]))
            for h in loadout.heroes if h.hero_id]


def play_and_count(rollout, party, zone, args, tiers) -> tuple[Counter, float]:
    """(action counts, run_score) for one comp.

    Hooks `type(env).step`, the same device `comp_ga._record_skill_use` uses,
    because `_action_map` is the table `step` itself reads -- so what lands in
    the counter is the decision, not a reconstruction of it.
    """
    env = rollout.env
    base = env.unwrapped
    store = base.store
    counts: Counter = Counter()
    original = type(base).step

    def counting_step(self, action):
        actor = self.engine.get_current_actor_id()
        mapped = self._action_map.get(action)
        if actor is not None and mapped is not None:
            hero = next((h for h in self.engine.heroes if h.id == actor), None)
            data = None
            if hero is not None and not mapped.skill_id.startswith("__"):
                data = self.engine.get_skill_data(mapped.skill_id,
                                                  level=hero.skill_tier)
            chosen = classify(data, mapped.skill_id, store)
            counts[chosen] += 1

            # THE AVAILABILITY CONTROL, and the reason the raw mix cannot
            # answer the question. A low-sustain comp has fewer heal actions on
            # the board, so "heal share rises with sustain" is close to
            # mechanical. What is NOT mechanical is how often the policy takes
            # a heal WHEN ONE IS OFFERED, and whether it does so only when
            # somebody is hurt. A policy running one plan takes the offered
            # heal at the same rate whatever the comp.
            if hero is not None:
                offered = False
                for idx in self._action_map:
                    other = self._action_map.get(idx)
                    if other is None or other.skill_id.startswith("__"):
                        continue
                    od = self.engine.get_skill_data(other.skill_id,
                                                    level=hero.skill_tier)
                    if classify(od, other.skill_id, store) == "heal":
                        offered = True
                        break
                if offered:
                    counts["heal_offered"] += 1
                    if chosen == "heal":
                        counts["heal_taken"] += 1
                    living = [h for h in self.engine.heroes if h.is_alive()]
                    if any(h.hp < 0.7 * h.max_hp for h in living):
                        counts["heal_offered_hurt"] += 1
                        if chosen == "heal":
                            counts["heal_taken_hurt"] += 1

            # THE EXPERT'S RULER. Of the skills legal for this hero right now,
            # which tiers were on offer and which did the policy take? Graded
            # only when the choice was real -- at least two DIFFERENT tiers
            # available -- so "it took an A" does not count as a good decision
            # when an A was the only thing it could do.
            #
            # Untiered skills are EXCLUDED, never counted as average: three
            # classes are absent from the list and scoring them as middling
            # would flatter or punish exactly those.
            if hero is not None and tiers:
                offered_tiers = []
                cat_tiers = []
                cat_skills = []
                for idx in self._action_map:
                    other = self._action_map.get(idx)
                    if other is None or other.skill_id.startswith("__"):
                        continue
                    t = tiers.get((hero.definition_id, other.skill_id))
                    if t:
                        offered_tiers.append(t)
                        od2 = self.engine.get_skill_data(other.skill_id,
                                                        level=hero.skill_tier)
                        cat2 = classify(od2, other.skill_id, store)
                        cat_tiers.append((t, cat2))
                        cat_skills.append((other.skill_id, t, cat2))
                offer = set(offered_tiers)
                mine = tiers.get((hero.definition_id, mapped.skill_id))
                if mine and len(offer) >= 2:
                    counts["tier_graded"] += 1
                    counts[f"tier_played_{mine}"] += 1
                    best = min(offer, key=lambda t: TIER_RANK[t])
                    if mine == best:
                        counts["tier_took_best"] += 1
                    # How far below the best available, in tiers. 0 is the best
                    # on offer; 4 would be taking a D over an S.
                    counts["tier_regret_sum"] += TIER_RANK[mine] - TIER_RANK[best]
                    # THE BASELINE, without which neither number means
                    # anything: what a UNIFORM pick among the same legal,
                    # tiered options would have scored on this very decision.
                    # Accumulated per decision rather than assumed, because how
                    # many options are on offer varies by hero, rank and round.
                    n_off = len(offered_tiers)
                    counts["tier_rand_best_sum"] += (
                        sum(1 for t in offered_tiers if t == best) / n_off)
                    counts["tier_rand_regret_sum"] += (
                        sum(TIER_RANK[t] - TIER_RANK[best] for t in offered_tiers)
                        / n_off)

                # WITHIN-CATEGORY, which is the only version of this ruler I
                # would defend. A tier rates a skill IN GENERAL, not in this
                # board state, so a C-tier heal on a dying hero is correct play
                # that the table above scores as a mistake -- and a policy that
                # heals a lot is therefore punished for its strategy rather
                # than judged on its execution.
                #
                # Grading only against skills of the SAME category the policy
                # chose removes that entirely: given that it decided to attack,
                # did it pick the best attack it had? The strategic call is
                # taken as given and only the tactical one is scored.
                if mine:
                    same = [t for t, cat in cat_tiers if cat == chosen]
                    if len(set(same)) >= 2:
                        counts["cat_graded"] += 1
                        cbest = min(same, key=lambda t: TIER_RANK[t])
                        if mine == cbest:
                            counts["cat_took_best"] += 1
                        else:
                            # Name the skill that WAS the best on offer and did
                            # not get taken. A rate says the policy chooses
                            # badly; this says what it is choosing badly about,
                            # which is the only form of the finding anyone can
                            # act on.
                            for sid2, t2, cat2 in cat_skills:
                                if t2 == cbest and cat2 == chosen:
                                    counts[("passed", hero.definition_id, sid2)] += 1
                                    break
                        counts["cat_regret_sum"] += TIER_RANK[mine] - TIER_RANK[cbest]
                        counts["cat_rand_best_sum"] += (
                            sum(1 for t in same if t == cbest) / len(same))
                        counts["cat_rand_regret_sum"] += (
                            sum(TIER_RANK[t] - TIER_RANK[cbest] for t in same)
                            / len(same))
        return original(self, action)

    type(base).step = counting_step
    try:
        result = rollout.evaluate(party, zone, args.tier, args.combats, args.band,
                                  episodes=args.episodes, seed=args.seed)
    finally:
        type(base).step = original
    return counts, result.mean_run_score * 100


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", default="models/dd_combat_ppo_full_expedition.zip")
    ap.add_argument("--episodes", type=int, default=6)
    ap.add_argument("--tier", type=int, default=3)
    ap.add_argument("--band", default="long", choices=("short", "medium", "long"))
    ap.add_argument("--combats", type=int, default=15)
    ap.add_argument("--zone", default=None,
                    help="pin every comp to one zone; default is the comp's own")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--misses", type=int, default=6, metavar="N",
                    help="how many passed-over skills to name, with the "
                         "expert's own words about each")
    ap.add_argument("--limit", type=int, default=60,
                    help="how many comps to play, taken evenly across the "
                         "sustain range so all three buckets fill")
    args = ap.parse_args()

    store = load_store_cached(CSV_DIR, GAME_DIR)
    tiers = tier_index(store, GAME_DIR)
    if tiers:
        n_t, n_all, missing = coverage(store, tiers)
        print(f"expert tier list: {n_t} of {n_all} hero skills tiered "
              f"({100 * n_t / max(n_all, 1):.0f}%); untiered classes {missing}")
    else:
        print("expert tier list not found -- the tier columns will be empty")
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

    print(f"style probe | {len(rows)} comps x {args.episodes} episodes | "
          f"tier {args.tier} {args.band} x{args.combats} | seed {args.seed}")
    print(f"  sustain from the EQUIPPED kit, buckets are thirds of this sample")

    rollout = Rollout(args.model)
    played = []
    for i, (sustain, name, zone, loadout) in enumerate(rows):
        zone_id = args.zone or zone_id_for_location(loadout.location) or zone or "warrens"
        counts, score = play_and_count(rollout, party_of(loadout), zone_id,
                                       args, tiers)
        played.append((sustain, name, score, counts))
        total = sum(counts[c] for c in CATEGORIES) or 1
        graded = counts["tier_graded"] or 1
        print(f"  [{i + 1:3d}/{len(rows)}] {name[:32]:34s} sustain {sustain:6.1f} "
              f"score {score:5.1f}  attack {100 * counts['attack'] / total:4.1f}% "
              f"heal {100 * counts['heal'] / total:4.1f}%  "
              f"best-tier {100 * counts['tier_took_best'] / graded:4.1f}%",
              flush=True)

    if len(played) < 6:
        print("\ntoo few comps played to bucket")
        return 1

    played.sort(key=lambda r: r[0])
    third = len(played) // 3
    buckets = (("LOW  sustain", played[:third]),
               ("MID  sustain", played[third:2 * third]),
               ("HIGH sustain", played[2 * third:]))

    print()
    header = "  bucket        n   sustain   score  " + "  ".join(
        f"{c[:6]:>6s}" for c in CATEGORIES)
    print(header)
    print("  " + "-" * (len(header) - 2))
    mixes = {}
    for label, group in buckets:
        pooled = Counter()
        for _s, _n, _sc, c in group:
            pooled.update(c)
        # Over the CATEGORIES only. `pooled` also carries the availability
        # bookkeeping (`heal_offered` and friends), and summing everything
        # silently diluted every share -- the first run of this probe printed
        # an action mix adding to ~60%.
        total = sum(pooled[c] for c in CATEGORIES) or 1
        mix = {c: 100 * pooled[c] / total for c in CATEGORIES}
        mixes[label] = mix
        print(f"  {label}  {len(group):3d}  {statistics.fmean(r[0] for r in group):7.1f}  "
              f"{statistics.fmean(r[2] for r in group):6.1f}  "
              + "  ".join(f"{mix[c]:5.1f}%" for c in CATEGORIES))

    print()
    print("  THE AVAILABILITY CONTROL -- is an OFFERED heal taken at the same rate?")
    print("  bucket          offered    taken    rate |  someone hurt: offered  taken    rate")
    rates = {}
    for label, group in buckets:
        pooled = Counter()
        for _s, _n, _sc, c in group:
            pooled.update(c)
        off, took = pooled["heal_offered"], pooled["heal_taken"]
        offh, tookh = pooled["heal_offered_hurt"], pooled["heal_taken_hurt"]
        rate = 100 * took / off if off else 0.0
        rateh = 100 * tookh / offh if offh else 0.0
        rates[label] = (rate, rateh)
        print(f"  {label} {off:9d}{took:9d}  {rate:6.1f}% |"
              f"{offh:17d}{tookh:7d}  {rateh:6.1f}%")

    print()
    print("  THE EXPERT'S RULER -- of the tiers on offer, which did it take?")
    print("  bucket         graded   took best   mean regret |  " +
          "  ".join(f"{t:>5s}" for t in TIER_ORDER))
    for label, group in buckets:
        pooled = Counter()
        for _s, _n, _sc, c in group:
            pooled.update(c)
        graded = pooled["tier_graded"]
        if not graded:
            print(f"  {label}       0   (no tiered choice arose)")
            continue
        # NOT `played`: that name holds the outer list of (sustain, name,
        # score, counts) and shadowing it made the report below iterate a dict.
        by_tier = {t: pooled[f"tier_played_{t}"] for t in TIER_ORDER}
        tot = sum(by_tier.values()) or 1
        print(f"  {label} {graded:8d}   {100 * pooled['tier_took_best'] / graded:6.1f}%   "
              f"{pooled['tier_regret_sum'] / graded:11.2f} |  "
              + "  ".join(f"{100 * by_tier[t] / tot:4.0f}%" for t in TIER_ORDER))
        print(f"  {'':12s} {'random:':>8s}   "
              f"{100 * pooled['tier_rand_best_sum'] / graded:6.1f}%   "
              f"{pooled['tier_rand_regret_sum'] / graded:11.2f} |")
        cg = pooled["cat_graded"]
        if cg:
            print(f"  {'':12s} {'within-category:':>8s} {cg:6d} graded, "
                  f"took best {100 * pooled['cat_took_best'] / cg:5.1f}% "
                  f"vs random {100 * pooled['cat_rand_best_sum'] / cg:5.1f}%, "
                  f"regret {pooled['cat_regret_sum'] / cg:4.2f} "
                  f"vs {pooled['cat_rand_regret_sum'] / cg:4.2f}")

    passed: Counter = Counter()
    for _s, _n, _sc, c in played:
        for k, v in c.items():
            if isinstance(k, tuple) and k[0] == "passed":
                passed[(k[1], k[2])] += v
    if passed:
        notes = explanations(store, GAME_DIR)
        print()
        print("  MOST PASSED OVER -- the best-tier option in its own category,")
        print("  legal, and not taken. The expert's words are his, verbatim.")
        for (hero_id, sid), n in passed.most_common(args.misses):
            tier = tiers.get((hero_id, sid), "?")
            print()
            print(f"  {n:5d}x  {hero_id}/{sid}  [{tier} -- {TIER_LABEL.get(tier, chr(63))}]")
            note = notes.get((hero_id, sid))
            if note:
                print(f"         \"{note[:300]}\"")
            else:
                print("         (the transcript does not name this skill)")

    low, high = mixes["LOW  sustain"], mixes["HIGH sustain"]
    spread = {c: high[c] - low[c] for c in CATEGORIES}
    biggest = max(spread, key=lambda c: abs(spread[c]))
    score_gap = (statistics.fmean(r[2] for r in buckets[2][1])
                 - statistics.fmean(r[2] for r in buckets[0][1]))

    print()
    print(f"  HIGH minus LOW, by category: "
          + ", ".join(f"{c} {spread[c]:+.1f}pp" for c in CATEGORIES
                      if abs(spread[c]) >= 1.0))
    print(f"  largest shift: {biggest} {spread[biggest]:+.1f}pp")
    print(f"  score gap HIGH - LOW: {score_gap:+.1f}")
    print()
    # A threshold has to be declared rather than discovered, or the verdict is
    # just the sign of a noisy number. 5pp on the largest category is the
    # smallest shift that would show up as a visibly different game plan.
    # The verdict rests on the CONTROLLED rate, not the raw mix. The raw heal
    # share is confounded by how many heals the comp owns; the rate at which an
    # OFFERED heal is taken is not.
    take_spread = rates["HIGH sustain"][0] - rates["LOW  sustain"][0]
    print(f"  take-rate HIGH - LOW: {take_spread:+.1f}pp   "
          f"(raw heal share moved {spread['heal']:+.1f}pp, which is confounded "
          f"by availability)")
    print()
    if abs(take_spread) < 5.0:
        print("  READING: with availability controlled for, the policy takes an "
              "offered heal at")
        print("           the SAME rate whatever the comp. That is ONE PLAN -- "
              "the raw mix only")
        print("           moved because high-sustain comps put more heals on "
              "the board. Low-")
        print("           sustain comps are played the same way and lose, which "
              "is a TRAINING")
        print("           problem the search space cannot fix.")
    else:
        print("  READING: the take-rate itself moves with the comp, so the "
              "policy is not")
        print("           running one fixed plan. A score gap between buckets "
              "is then a")
        print("           statement about the comps or the scenario rather than "
              "about the")
        print("           policy's repertoire.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
