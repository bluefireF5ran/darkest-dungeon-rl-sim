"""Measure the trained policy against PLAYBOOK.md's combat rules.

`analyze_behaviour.py` answers "what does it do"; this answers "does it do what
an expert player says is correct". Each check below names a PLAYBOOK.md rule and
reports the number that would falsify it. Read-only: no training, no writes.

  3.2  heal only in extreme situations or in stall
  3.3  with a kill already secured, the hero acting first spends its turn on
       heal / stress-heal instead of piling on
  3.4  stalling is conditional on the comp actually having a healer or
       stress-healer to stall FOR
  3.5  buffs pay off in long fights; how long are our fights?

Plus the two falsifiers the 2026-08-15 pre-retrain bundle was built to move, which
are not PLAYBOOK rules but engineering claims about the observation and the
action set (see CHANGELOG.md):

  DOT  attacks aimed at an enemy already doomed by its own bleed/blight — the
       thing `_is_doomed` was added to the observation to make visible. Distinct
       from 3.3, which measures the *stunned*-secured case.
  CRP  attacks aimed at a corpse, against how often one was legally targetable
       at all. Zero for the life of the project before the bundle, since no hero
       was ever offered the action.

    python tools/check_playbook.py --model models/dd_combat_ppo_full_expedition.zip --episodes 30
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
from env.gym_env import _is_doomed
from eval_expedition import pick_representative_comps, resolve_skill_ids
from tools.analyze_behaviour import categorize

# "Extreme" for PLAYBOOK 3.2. Death's Door is its own bucket; this is the band
# where a human would agree the hero is genuinely in trouble.
CRITICAL_HP_FRACTION = 0.34


def skill_kinds(engine, hero_id):
    """(can_heal, can_stress_heal) for one hero's EQUIPPED skills."""
    heal = stress = False
    hero = engine.heroes[hero_id]
    for sid in engine.get_hero_skill_ids(hero_id):
        sd = engine.get_skill_data(sid, level=hero.skill_tier)
        if sd is None:
            continue
        if engine._is_ally_skill(sid) and sd.heal_min is not None:
            heal = True
        for name in sd.effects:
            eff = engine.store.effects.get(name)
            if eff is not None and eff.stress_heal:
                stress = True
    return heal, stress


def pct(n, d):
    return f"{100.0 * n / d:5.1f}%" if d else "    - "


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="models/dd_combat_ppo_full_expedition.zip")
    ap.add_argument("--episodes", type=int, default=30)
    ap.add_argument("--comps", type=int, default=6)
    ap.add_argument("--comp-seed", type=int, default=1)
    args = ap.parse_args()

    base = ExpeditionSequenceEnv(sampler_seed=None)
    env = ActionMasker(base, mask_fn)
    model = MaskablePPO.load(args.model, env=env)
    store = base.store

    comps = pick_representative_comps(store, args.comps, args.comp_seed)
    rng = random.Random(999)

    # 3.2
    heal_buckets = collections.Counter()
    # 3.3 / 3.4
    recovery_actions = collections.Counter()
    recovery_by_healer = {True: collections.Counter(), False: collections.Counter()}
    secured_actions = collections.Counter()
    recovery_turns_in_healerless_comps = 0
    # 3.5
    combat_rounds = []
    # bundle falsifiers
    doomed_available = doomed_attacks = doomed_attacks_elsewhere = 0
    doomed_chance = 0.0   # what an indifferent policy would score — see the report
    corpse_available = corpse_attacks = 0
    blocking_corpse_turns = 0
    # The sharp version of CRP, asked after two live boards showed the policy
    # picking a 7 hp corpse over a reachable 8 hp Swine Slasher: when it hits a
    # corpse, was a LIVING enemy in reach of that same skill that the same hit
    # would have killed outright? Conservative on purpose — `dmg_min` before
    # modifiers, so a forgone kill counted here is one that was never in doubt.
    corpse_over_secured_kill = 0
    # The sharp version of 3.2. "Unjustified" is a criterion; these are the
    # facts underneath it, and they decide whether this is a reward bug (the
    # policy is being paid for the heal) or a policy artifact (it is not).
    unjustified_heal_hp_frac = []
    unjustified_heal_overheal = []
    unjustified_heal_reward = []
    justified_heal_reward = []
    other_turn_reward = []
    unjustified_heal_restored = []

    turns_total = 0
    _prev_unjustified = 0
    _prev_justified = 0

    for ep in range(args.episodes):
        comp_name, zone_id, hero_ids = comps[ep % len(comps)]
        band = list(EXPEDITION_LENGTH_BANDS)[ep % 3]
        lo, hi = EXPEDITION_LENGTH_BANDS[band]
        n_combats = rng.randint(lo, hi)
        seed = zlib.crc32(f"playbook|{comp_name}|{ep}".encode()) % (2 ** 31)
        opts = {
            "hero_ids": hero_ids, "comp_name": comp_name, "zone_id": zone_id,
            "tier": rng.randint(1, 5), "length_band": band, "num_combats": n_combats,
        }
        sk = resolve_skill_ids(store, comp_name, hero_ids)
        if sk is not None:
            opts["hero_skill_ids"] = sk
        obs, _ = env.reset(seed=seed, options=opts)

        eng = base.engine
        kinds = [skill_kinds(eng, i) for i in range(len(eng.heroes))]
        comp_can_recover = any(h or s for h, s in kinds)

        last_round = eng.round
        term = trunc = False
        while not (term or trunc):
            a = int(model.predict(obs, deterministic=True,
                                  action_masks=get_action_masks(env))[0])
            eng = base.engine

            # New engine round counter went backwards -> a new combat started.
            if eng.round < last_round:
                combat_rounds.append(last_round)
            last_round = eng.round

            actor_id = eng.get_current_actor_id()
            chosen = base._action_map.get(a)
            if actor_id is not None and chosen is not None:
                turns_total += 1
                cat, sid = categorize(eng, chosen)

                # A stress-heal is a "support" skill; separate it out, since
                # PLAYBOOK 3.2/3.3 treat it as recovery, not as a buff.
                if cat == "support":
                    sd = eng.get_skill_data(chosen.skill_id, level=eng.heroes[actor_id].skill_tier)
                    if sd is not None and any(
                            (eng.store.effects.get(n) is not None
                             and eng.store.effects[n].stress_heal) for n in sd.effects):
                        cat = "stress_heal"

                in_recovery = base.is_recovery_phase()
                living = [e for e in eng.enemies if e.is_alive()]
                all_stunned = bool(living) and all(e.is_stunned for e in living)

                # --- 3.2: was this heal justified? ---
                if cat == "heal":
                    tgt = next((h for h in eng.heroes
                                if h.is_alive() and h.position == chosen.target_id), None)
                    if tgt is None:
                        heal_buckets["unknown target"] += 1
                    elif tgt.is_deaths_door:
                        heal_buckets["Death's Door"] += 1
                    elif tgt.hp < CRITICAL_HP_FRACTION * max(tgt.max_hp, 1):
                        heal_buckets["critical HP"] += 1
                    elif in_recovery:
                        heal_buckets["stall/recovery"] += 1
                    else:
                        heal_buckets["NEITHER (violates 3.2)"] += 1
                        # How healthy was the hero being topped up, and how much
                        # of the heal could not land? "Tres a tope y uno tocado"
                        # is a claim about exactly these two numbers.
                        unjustified_heal_hp_frac.append(tgt.hp / max(tgt.max_hp, 1))
                        sd_h = eng.get_skill_data(
                            chosen.skill_id, level=eng.heroes[actor_id].skill_tier)
                        if sd_h is not None and sd_h.heal_max:
                            room = max(tgt.max_hp - tgt.hp, 0)
                            unjustified_heal_overheal.append(
                                max(sd_h.heal_max - room, 0) / max(sd_h.heal_max, 1))
                            unjustified_heal_restored.append(min(sd_h.heal_max, room))

                # --- 3.4: what gets done with the cheap turns, and by whom ---
                if in_recovery:
                    recovery_actions[cat] += 1
                    recovery_by_healer[comp_can_recover][cat] += 1
                    if not comp_can_recover:
                        recovery_turns_in_healerless_comps += 1

                # --- 3.3: the kill is secured; is the free turn spent well? ---
                if all_stunned and base._party_needs_recovery():
                    secured_actions[cat] += 1

                # --- DOT / CRP: what the bundle claims to have made possible ---
                # Both keyed on what the CHOSEN action actually points at, which
                # is why they read chosen.target_id rather than the board alone.
                aimed_at = next((e for e in eng.enemies
                                 if chosen.target_id in e.occupied_positions), None)
                if any(_is_doomed(e) for e in eng.enemies):
                    doomed_available += 1
                    if cat == "attack" and aimed_at is not None:
                        if _is_doomed(aimed_at):
                            doomed_attacks += 1
                        else:
                            doomed_attacks_elsewhere += 1
                        # The null this rate has to be read against: an attacker
                        # that did not care would hit the doomed one at the rate
                        # doomed targets occur on the board. Without it, "39.5%"
                        # is a number rather than a finding.
                        if living:
                            doomed_chance += sum(1 for e in living if _is_doomed(e)) / len(living)
                if any(e.is_corpse for e in eng.enemies):
                    corpse_available += 1
                    if base._blocking_corpses():
                        blocking_corpse_turns += 1
                    if cat == "attack" and aimed_at is not None and aimed_at.is_corpse:
                        corpse_attacks += 1
                        # Was a living enemy in reach of THIS skill that this
                        # hit would have killed outright? Reach is the skill's
                        # own `target` ranks, so the comparison is against what
                        # the hero could actually have clicked instead.
                        hero = eng.heroes[actor_id]
                        sd_c = eng.get_skill_data(chosen.skill_id,
                                                  level=hero.skill_tier)
                        reach = set(sd_c.target) if sd_c is not None else set()
                        if any(e.hp <= hero.dmg_min
                               and reach & set(p + 1 for p in e.occupied_positions)
                               for e in living):
                            corpse_over_secured_kill += 1

            # Which bucket this turn landed in, read off the counters rather
            # than recomputed — so the reward split cannot drift from the split
            # the 3.2 table reports.
            n_unjust = heal_buckets["NEITHER (violates 3.2)"]
            n_just = (heal_buckets["Death's Door"] + heal_buckets["critical HP"]
                      + heal_buckets["stall/recovery"])
            was_unjustified_heal = n_unjust > _prev_unjustified
            was_justified_heal = n_just > _prev_justified
            _prev_unjustified, _prev_justified = n_unjust, n_just

            obs, _r, term, trunc, _info = env.step(a)
            # The realized step reward. Both buckets carry the same confound —
            # the enemy turns auto-resolved before step() returns — so the
            # DIFFERENCE is the readable part, not either level. If unjustified
            # heals pay MORE than an average turn, the reward is buying them and
            # no amount of extra training will stop it.
            if was_unjustified_heal:
                unjustified_heal_reward.append(float(_r))
            elif was_justified_heal:
                justified_heal_reward.append(float(_r))
            else:
                other_turn_reward.append(float(_r))

        combat_rounds.append(last_round)

    print(f"Model: {args.model} | episodes={args.episodes} | comps={args.comps} "
          f"| hero turns={turns_total}\n")

    print("PLAYBOOK 3.2 — heal only in extreme situations or in stall")
    tot = sum(heal_buckets.values())
    if not tot:
        print("  NO HEALS CAST AT ALL\n")
    else:
        for k in ("Death's Door", "critical HP", "stall/recovery",
                  "NEITHER (violates 3.2)", "unknown target"):
            if heal_buckets.get(k):
                print(f"  {k:26s} {heal_buckets[k]:5d}  {pct(heal_buckets[k], tot)}")
        print(f"  {'total heals':26s} {tot:5d}  ({pct(tot, turns_total)} of all hero turns)\n")

    print("PLAYBOOK 3.4 — stalling is for comps that can actually recover")
    for has, label in ((True, "comp CAN recover"), (False, "comp CANNOT recover")):
        c = recovery_by_healer[has]
        t = sum(c.values())
        if not t:
            print(f"  {label:22s}  no recovery-phase turns")
            continue
        mix = "  ".join(f"{k}={pct(v, t)}" for k, v in c.most_common(5))
        print(f"  {label:22s} {t:5d} turns   {mix}")
    print(f"  cheap turns granted to comps with no healer AND no stress-healer: "
          f"{recovery_turns_in_healerless_comps}\n")

    print("PLAYBOOK 3.3 — kill secured (every living enemy stunned) + party needs recovery")
    t = sum(secured_actions.values())
    if not t:
        print("  situation never arose\n")
    else:
        for k, v in secured_actions.most_common():
            print(f"  {k:26s} {v:5d}  {pct(v, t)}")
        good = secured_actions["heal"] + secured_actions["stress_heal"]
        print(f"  -> spent on recovery: {pct(good, t)}  (expert play: most of them)\n")

    print("DOT — piling onto a target already dying to its own bleed/blight")
    if not doomed_available:
        print("  situation never arose\n")
    else:
        aimed = doomed_attacks + doomed_attacks_elsewhere
        print(f"  turns with a doomed enemy on the board: {doomed_available:5d}  "
              f"({pct(doomed_available, turns_total)} of all hero turns)")
        print(f"  attacks aimed AT the doomed enemy:      {doomed_attacks:5d}  "
              f"{pct(doomed_attacks, aimed)} of attacks made in that situation")
        if aimed:
            chance = doomed_chance / aimed
            print(f"  an INDIFFERENT attacker would score:           {100.0 * chance:5.1f}%")
            print(f"  -> the policy is "
                  f"{'AVOIDING' if doomed_attacks / aimed < chance - 0.03 else ('PILING ON' if doomed_attacks / aimed > chance + 0.03 else 'INDIFFERENT')}"
                  f" doomed targets")
        print("  -> this is what the _is_doomed observation flag exists to lower\n")

    print("CRP — attacking a corpse (legal only since 2026-08-15)")
    if not corpse_available:
        print("  no corpse ever appeared\n")
    else:
        print(f"  turns with a corpse on the board:  {corpse_available:5d}  "
              f"({pct(corpse_available, turns_total)} of all hero turns)")
        print(f"  of those, a corpse BLOCKED a living enemy: {blocking_corpse_turns:5d}  "
              f"{pct(blocking_corpse_turns, corpse_available)}")
        print(f"  attacks aimed at a corpse:         {corpse_attacks:5d}  "
              f"{pct(corpse_attacks, corpse_available)} of the turns it was possible")
        print("  -> 0.0% means the new action was learned away, not that it is rare")
        print(f"  of those, a LIVING enemy in the same skill's reach would have "
              f"died to that hit: {corpse_over_secured_kill:5d}  "
              f"{pct(corpse_over_secured_kill, corpse_attacks)}")
        print("  -> conservative: dmg_min before modifiers, so these kills were "
              "never in doubt\n")

    print("3.2 sharpened — what the unjustified heals actually were")
    if not unjustified_heal_hp_frac:
        print("  none\n")
    else:
        f = sorted(unjustified_heal_hp_frac)
        print(f"  n={len(f)} | target's HP fraction: median={statistics.median(f):.0%} "
              f"mean={statistics.mean(f):.0%} p90={f[int(0.9 * (len(f) - 1))]:.0%}")
        print(f"    above 80% HP: {pct(sum(1 for x in f if x > 0.8), len(f))}"
              f"   above 95%: {pct(sum(1 for x in f if x > 0.95), len(f))}")
        if unjustified_heal_overheal:
            o = unjustified_heal_overheal
            print(f"  heal wasted on a full bar: mean={statistics.mean(o):.0%} "
                  f"of the skill's own maximum")
        if unjustified_heal_restored:
            r = unjustified_heal_restored
            print(f"  HP the heal could actually restore: "
                  f"mean={statistics.mean(r):.1f} median={statistics.median(r):.1f}")
        if unjustified_heal_reward and justified_heal_reward:
            u = statistics.mean(unjustified_heal_reward)
            j = statistics.mean(justified_heal_reward)
            t = statistics.mean(other_turn_reward) if other_turn_reward else float("nan")
            print(f"  realized step reward   unjustified heal {u:+.4f}  |  "
                  f"justified heal {j:+.4f}  |  any other turn {t:+.4f}")
            print(f"    read heal vs HEAL (delta {u - j:+.4f}). The third column is "
                  "not a control:")
            print("    an attack turn kills things, and scores higher for reasons "
                  "that have")
            print("    nothing to do with 3.2.")
            print("  -> unjustified >= justified means the reward CANNOT TELL THEM "
                  "APART and no")
            print("     amount of further training will; clearly below means the "
                  "policy is")
            print("     leaving reward on the table, and the fault is not in the "
                  "reward.\n")

    print("PLAYBOOK 3.5 — buffs need long fights to pay off")
    if combat_rounds:
        cr = sorted(combat_rounds)
        print(f"  combats={len(cr)}  rounds: mean={statistics.mean(cr):.1f} "
              f"median={statistics.median(cr)} p90={cr[int(0.9 * (len(cr) - 1))]} max={max(cr)}")
        for n in (3, 5, 8):
            print(f"    lasting >{n} rounds: {pct(sum(1 for r in cr if r > n), len(cr))}")


if __name__ == "__main__":
    main()
