"""Human-readable battle report — everything the game screen would show you,
followed by the trained model's actual play, turn by turn.

Intended for reviewing the policy's decisions against how a real player would
approach the same board: party and enemy rosters with species/resistances,
trinkets, equipped skills, live stats, and the initiative order.

    python tools/battle_report.py --comp "Iron Shackles: Blight" --combats 2
    python tools/battle_report.py --list          # show available comps
"""

import argparse
import sys
import zlib
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sb3_contrib import MaskablePPO
from sb3_contrib.common.wrappers import ActionMasker
from sb3_contrib.common.maskable.utils import get_action_masks

from env import ExpeditionSequenceEnv, mask_fn
from eval_expedition import pick_representative_comps, resolve_skill_ids


def pct(x: float) -> str:
    return f"{100*x:.0f}%"


def describe_hero(h, hd, skills, trinkets):
    print(f"  rank {h.position+1}  {h.name:14s} HP {h.hp:3d}/{h.max_hp:<3d} stress {h.stress:3d}"
          f"  SPD {h.speed:2d}  ACC +{pct(h.atk_bonus):>4s}  CRIT {pct(h.crit):>4s}"
          f"  DMG {h.dmg_min}-{h.dmg_max}  DODGE {pct(h.dodge):>4s}  PROT {pct(h.prot):>4s}")
    print(f"      resists  stun {pct(h.stun_resist):>4s} | blight {pct(h.blight_resist):>4s} | "
          f"bleed {pct(h.bleed_resist):>4s} | debuff {pct(h.debuff_resist):>4s} | "
          f"move {pct(h.move_resist):>4s} | deathblow {pct(h.death_blow_resist):>4s}")
    if trinkets:
        print(f"      trinkets: {', '.join(trinkets)}")
    print(f"      skills:   {', '.join(skills)}")


def describe_enemy(e, ed):
    species = "/".join(ed.enemy_types) if ed and ed.enemy_types else "?"
    size = f"size {e.size}" if e.size > 1 else ""
    print(f"  rank {e.position+1}  {e.name:22s} [{species}] {size:7s} HP {e.hp:3d}/{e.max_hp:<3d}"
          f"  SPD {e.speed:2d}  DODGE {pct(e.dodge):>4s}  PROT {pct(e.prot):>4s}"
          f"  DMG {e.dmg_min}-{e.dmg_max}  CRIT {pct(e.crit):>4s}")
    print(f"      resists  stun {pct(e.stun_resist):>4s} | blight {pct(e.blight_resist):>4s} | "
          f"bleed {pct(e.bleed_resist):>4s} | debuff {pct(e.debuff_resist):>4s} | "
          f"move {pct(e.move_resist):>4s}"
          + (f"  | {ed.actions_per_round} actions/round" if ed and ed.actions_per_round > 1 else ""))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="models/dd_combat_ppo_full_expedition.zip")
    ap.add_argument("--comp", default=None, help="comp name (default: first sampled)")
    ap.add_argument("--combats", type=int, default=1, help="how many combats to report")
    ap.add_argument("--tier", type=int, default=3)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()

    base = ExpeditionSequenceEnv(sampler_seed=None)
    store = base.store
    comps = pick_representative_comps(store, 12, 1)
    if args.list:
        for n, z, h in comps:
            print(f"  {n:32s} {z:8s} {h}")
        return

    chosen = next((c for c in comps if c[0] == args.comp), comps[0])
    comp_name, zone_id, hero_ids = chosen

    env = ActionMasker(base, mask_fn)
    model = MaskablePPO.load(args.model, env=env)

    loadout = store.comp_loadout_by_name(comp_name)
    skill_ids = resolve_skill_ids(store, comp_name, hero_ids)
    opts = {"hero_ids": hero_ids, "comp_name": comp_name, "zone_id": zone_id,
            "tier": args.tier, "length_band": "medium", "num_combats": args.combats}
    if skill_ids:
        opts["hero_skill_ids"] = skill_ids
    obs, _ = env.reset(seed=args.seed, options=opts)
    # Full calculation tracing — off during training, on here.
    base.engine.verbose = True

    print("=" * 100)
    print(f"COMP: {comp_name}   |   ZONE: {zone_id}   |   TIER {args.tier} "
          f"(heroes at resolve {base.engine.heroes[0].level})")
    print("=" * 100)

    reported = 0
    idx = base._combat_idx
    term = trunc = False
    while not (term or trunc) and reported < args.combats:
        eng = base.engine
        print(f"\n----- COMBAT {reported+1} -----")
        print("\nPARTY")
        for i, h in enumerate(eng.heroes):
            hero_skills = eng.get_hero_skill_ids(h.id)
            trk = loadout.heroes[i].trinket_ids if loadout and i < len(loadout.heroes) else []
            describe_hero(h, store.heroes.get(h.definition_id), hero_skills, trk)
        print("\nENEMIES")
        for e in eng.enemies:
            describe_enemy(e, store.enemies.get(e.definition_id))
        print("\nTURN ORDER (SPD + d8, this round)")
        print("  " + " -> ".join(f"{c.name}({'hero' if c.is_hero else 'foe'})"
                                  for c in eng.turn_order))
        print("\nPLAY")
        log_cursor = len(eng.log)
        while not (term or trunc) and base._combat_idx == idx:
            actor = eng.get_current_actor_id()
            a = int(model.predict(obs, deterministic=True,
                                  action_masks=get_action_masks(env))[0])
            chosen_action = base._action_map.get(a)
            if actor is not None and chosen_action is not None:
                h = eng.heroes[actor]
                tgt = ("self/none" if chosen_action.target_id < 0
                       else f"rank {chosen_action.target_id+1}")
                print(f"  > {h.name} (rank {h.position+1}, HP {h.hp}/{h.max_hp}, "
                      f"stress {h.stress}) uses {chosen_action.skill_id} -> {tgt}")
            obs, _r, term, trunc, _i = env.step(a)
            for line in eng.log[log_cursor:]:
                print(f"      {line}")
            log_cursor = len(eng.log)
            if base._combat_idx != idx:
                idx = base._combat_idx
                break
        alive = [h.name for h in eng.heroes if h.is_alive()]
        print(f"\n  RESULT: rounds={eng.round}  heroes alive={len(alive)}  "
              f"stress={[h.stress for h in eng.heroes if h.is_alive()]}")
        reported += 1


if __name__ == "__main__":
    main()
