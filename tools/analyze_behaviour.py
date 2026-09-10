"""What the trained model actually DOES, per hero class.

Rolls out full expeditions with a trained policy and records the concrete
action chosen on every hero turn (resolved through the env's own
index->Action map, so it's exactly what the engine executed), then reports
per-class skill usage, action-category mix, targeting, and a set of
tactical-quality checks a human player would care about:

  * does each class use its signature skills, or spam one?
  * are attacks spread across ranks or dumped on the front-most enemy?
  * are heals used before a hero is critical, or only at Death's Door?
  * are stress heals ever used at all?
  * are provisions used when the hero actually has the condition?
  * how many turns are "productive" (attack/heal/support) vs move/pass?
  * does it re-stun an already-stunned enemy (a wasted turn)?

    python tools/analyze_behaviour.py --episodes 30 [--dump-log run.log] [--comps 6]
"""

import argparse
import collections
import random
import statistics
import sys
import zlib
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sb3_contrib import MaskablePPO
from sb3_contrib.common.wrappers import ActionMasker
from sb3_contrib.common.maskable.utils import get_action_masks

from env import ExpeditionSequenceEnv, mask_fn
from env.expedition_sequence_env import EXPEDITION_LENGTH_BANDS
from eval_expedition import pick_representative_comps, resolve_skill_ids


def categorize(engine, action):
    sid = action.skill_id
    if sid == "__move__":
        return "move", None
    if sid.startswith("__provision_"):
        return "provision", sid[len("__provision_"):-2]
    if sid == "__pass__":
        return "pass", None
    sd = engine.get_skill_data(sid, level=4)
    if engine._is_ally_skill(sid):
        is_heal = sd is not None and sd.heal_min is not None
        return ("heal" if is_heal else "support"), sid
    if sd is not None and not sd.target:
        return "self", sid
    return "attack", sid


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="models/dd_combat_ppo_full_expedition.zip")
    ap.add_argument("--episodes", type=int, default=30)
    ap.add_argument("--comps", type=int, default=6)
    ap.add_argument("--comp-seed", type=int, default=1)
    ap.add_argument("--dump-log", default=None,
                    help="write the full engine combat log of the first episode here")
    args = ap.parse_args()

    base = ExpeditionSequenceEnv(sampler_seed=None)
    env = ActionMasker(base, mask_fn)
    model = MaskablePPO.load(args.model, env=env)
    store = base.store

    comps = pick_representative_comps(store, args.comps, args.comp_seed)
    rng = random.Random(999)

    skills = collections.defaultdict(collections.Counter)
    cats = collections.defaultdict(collections.Counter)
    attack_rank = collections.Counter()
    attack_rank_by_class = collections.defaultdict(collections.Counter)
    heal_hp_fracs = []
    stress_heal_uses = 0
    provision_uses = collections.Counter()
    provision_wasted = 0
    restun_wasted = 0
    attacks_total = 0
    deaths = []
    dumped = False

    for ep in range(args.episodes):
        comp_name, zone_id, hero_ids = comps[ep % len(comps)]
        band = list(EXPEDITION_LENGTH_BANDS)[ep % 3]
        lo, hi = EXPEDITION_LENGTH_BANDS[band]
        n_combats = rng.randint(lo, hi)
        seed = zlib.crc32(f"beh|{comp_name}|{ep}".encode()) % (2**31)
        opts = {
            "hero_ids": hero_ids, "comp_name": comp_name, "zone_id": zone_id,
            "tier": rng.randint(1, 5), "length_band": band, "num_combats": n_combats,
        }
        sk = resolve_skill_ids(store, comp_name, hero_ids)
        if sk is not None:
            opts["hero_skill_ids"] = sk
        obs, _ = env.reset(seed=seed, options=opts)
        eng = base.engine
        alive_before = {h.id: True for h in eng.heroes}

        term = trunc = False
        while not (term or trunc):
            a = int(model.predict(obs, deterministic=True,
                                  action_masks=get_action_masks(env))[0])
            eng = base.engine
            actor_id = eng.get_current_actor_id()
            chosen = base._action_map.get(a)
            if actor_id is not None and chosen is not None:
                actor = eng.heroes[actor_id]
                cls = actor.definition_id
                cat, sid = categorize(eng, chosen)
                cats[cls][cat] += 1
                if sid and cat not in ("provision",):
                    skills[cls][sid] += 1
                if cat == "attack":
                    attacks_total += 1
                    attack_rank[chosen.target_id] += 1
                    attack_rank_by_class[cls][chosen.target_id] += 1
                    tgt = next((e for e in eng.enemies
                                if e.is_alive() and e.position == chosen.target_id), None)
                    sd = eng.get_skill_data(chosen.skill_id, level=actor.skill_tier)
                    if tgt is not None and tgt.is_stunned and sd is not None:
                        # Landing a fresh stun on an already-stunned target is
                        # a wasted turn a human would not spend.
                        for en in sd.effects:
                            eff = store.effects.get(en)
                            if eff is not None and eff.stun_chance:
                                restun_wasted += 1
                                break
                elif cat == "heal":
                    t = next((h for h in eng.heroes
                              if h.is_alive() and h.position == chosen.target_id), None)
                    if t is not None:
                        heal_hp_fracs.append(t.hp / max(t.max_hp, 1))
                elif cat == "support":
                    sd = eng.get_skill_data(chosen.skill_id, level=actor.skill_tier)
                    if sd is not None and any(
                        (store.effects.get(n) is not None
                         and store.effects[n].stress_heal) for n in sd.effects):
                        stress_heal_uses += 1
                elif cat == "provision":
                    provision_uses[sid] += 1
                    if sid == "bandage" and not actor.bleeds:
                        provision_wasted += 1
                    if sid == "antivenom" and not actor.blights:
                        provision_wasted += 1

            obs, _r, term, trunc, _info = env.step(a)

        eng = base.engine
        for h in eng.heroes:
            if alive_before.get(h.id) and not h.is_alive():
                deaths.append(h.stress)
        if args.dump_log and not dumped:
            with open(args.dump_log, "w", encoding="utf-8") as f:
                f.write(f"# {comp_name} | {zone_id} | {band} | {n_combats} combats\n")
                f.write("\n".join(eng.log))
            dumped = True

    print(f"Model: {args.model} | episodes={args.episodes} | comps={args.comps}\n")

    print("Action-category mix per class (% of that class's turns):")
    print(f"{'class':16s} {'turns':>6s}  " +
          "".join(f"{c:>10s}" for c in ("attack", "heal", "support", "self", "move", "provision", "pass")))
    for cls in sorted(cats):
        tot = sum(cats[cls].values())
        row = "".join(f"{100*cats[cls].get(c,0)/tot:9.1f}%"
                      for c in ("attack", "heal", "support", "self", "move", "provision", "pass"))
        print(f"{cls:16s} {tot:6d}  {row}")

    print("\nSkill usage per class (share of that class's skill uses):")
    for cls in sorted(skills):
        tot = sum(skills[cls].values())
        top = ", ".join(f"{s}:{100*n/tot:.0f}%" for s, n in skills[cls].most_common(5))
        n_distinct = len(skills[cls])
        print(f"  {cls:16s} ({n_distinct} distinct) {top}")

    print("\nTargeting — which enemy rank attacks are aimed at:")
    tot_a = max(sum(attack_rank.values()), 1)
    print("  overall: " + " ".join(f"rank{r+1}={100*attack_rank.get(r,0)/tot_a:.0f}%" for r in range(4)))

    print("\nTactical quality checks:")
    if heal_hp_fracs:
        print(f"  heals cast on a target at mean {100*statistics.mean(heal_hp_fracs):.0f}% HP "
              f"(median {100*statistics.median(heal_hp_fracs):.0f}%); "
              f"{100*sum(1 for f in heal_hp_fracs if f <= 0.0)/len(heal_hp_fracs):.0f}% "
              f"were at 0 HP / Death's Door")
    else:
        print("  NO HEALS CAST AT ALL")
    print(f"  stress-heal skill uses: {stress_heal_uses}")
    print(f"  provisions used: {dict(provision_uses) or 'none'} (wasted={provision_wasted})")
    print(f"  attacks landing a stun on an already-stunned target: {restun_wasted} "
          f"of {attacks_total} attacks")
    if deaths:
        print(f"  hero deaths: {len(deaths)}, stress at death "
              f"mean={statistics.mean(deaths):.0f} median={statistics.median(deaths):.0f}")
    else:
        print("  hero deaths: 0")


if __name__ == "__main__":
    main()
