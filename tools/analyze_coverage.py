"""What the model was EXPOSED to during training, per hero class.

Training samples scenarios only from the real-comp pool
(ExpeditionEnv._build_hero_pool -> _sample_comp), so a class the real comps
rarely use is a class the policy has had little chance to learn — and an
ad-hoc party ("a strong core + whatever") is a distribution the policy has
never seen at all. This script measures that exposure directly, which is the
ceiling on "does the model know how to play class X".

    python tools/analyze_coverage.py
"""

import collections
import statistics
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from env.expedition_env import ExpeditionEnv


def main() -> None:
    env = ExpeditionEnv(sampler_seed=1)
    pool = env._hero_pool  # [(comp_name, zone_id, [hero_id, ...]), ...]
    store = env.store

    comps_per_class = collections.Counter()
    slots_per_class = collections.Counter()
    rank_per_class = collections.defaultdict(collections.Counter)
    skills_per_class = collections.defaultdict(collections.Counter)
    party_sizes = collections.Counter()

    for comp_name, _zone, hero_ids in pool:
        party_sizes[len(hero_ids)] += 1
        for cls in set(hero_ids):
            comps_per_class[cls] += 1
        skill_ids = env._resolve_skill_ids(comp_name, hero_ids) or [None] * len(hero_ids)
        for rank, cls in enumerate(hero_ids):
            slots_per_class[cls] += 1
            rank_per_class[cls][rank] += 1
            for sid in (skill_ids[rank] or []):
                skills_per_class[cls][sid] += 1

    total_slots = sum(slots_per_class.values())
    print(f"Training pool: {len(pool)} comps, {total_slots} hero slots")
    print(f"Party sizes: {dict(sorted(party_sizes.items()))}")
    print()

    print(f"{'class':16s} {'comps':>5s} {'slots':>5s} {'%slots':>7s}  rank distribution (1/2/3/4)")
    print("-" * 74)
    missing = []
    for cls in sorted(store.heroes):
        n_slots = slots_per_class.get(cls, 0)
        if n_slots == 0:
            missing.append(cls)
            continue
        ranks = rank_per_class[cls]
        rank_str = " ".join(f"{ranks.get(r, 0):4d}" for r in range(4))
        print(f"{cls:16s} {comps_per_class[cls]:5d} {n_slots:5d} "
              f"{100*n_slots/total_slots:6.1f}%  {rank_str}")
    if missing:
        print(f"\nCLASSES NEVER SEEN IN TRAINING: {missing}")

    counts = [slots_per_class.get(c, 0) for c in store.heroes]
    print(f"\nslot-count spread across the 18 classes: min={min(counts)} "
          f"max={max(counts)} median={statistics.median(counts):.0f} "
          f"ratio max/min={max(counts)/max(min(counts),1):.1f}x")

    # Which of each class's 7 skills the real comps actually equip. A skill
    # equipped by almost no comp is one the policy has essentially never had
    # the option to use, regardless of how good it is.
    print("\nPer-class skill exposure (times equipped across the pool; a class's")
    print("full movepool is 7, so any skill absent here is one the policy never saw):")
    for cls in sorted(store.heroes):
        if cls not in skills_per_class:
            continue
        hd = store.heroes[cls]
        equipped = skills_per_class[cls]
        never = [s.id for s in hd.skills if s.id not in equipped]
        top = ", ".join(f"{sid}:{n}" for sid, n in equipped.most_common(4))
        print(f"  {cls:16s} top: {top}")
        if never:
            print(f"  {'':16s} NEVER equipped: {never}")


if __name__ == "__main__":
    main()
