"""Is the policy EXECUTING each class, or just holding it?

A ruler, and only a ruler. Fran, 2026-08-27, on the per-class guides in
`metadata/HEROES/Heroes/*.md`:

    *"you have a detailed md file explaining how should the class be played at
    a high level and even a priority of use of the skills, only use this to
    verify and iterate, as we want to fine tune this model so it learns 'The
    mechanics of Darkest Dungeon and the importance of certain numbers' rather
    than making a player that follows the same plan always, as this wont
    enable the use of modded classes and will hurt our scalability on the
    future"*

So nothing here may become a reward term, an action mask, or a training
target. A policy that follows a per-class script is exactly the un-scalable
player he does not want. These checks say where it misplays a class; what to
do about that is a separate decision every time.

Same shape as `tools/check_playbook.py`: each block names the claim it is
testing, from a specific guide, and prints the number that would falsify it.

    python tools/check_class_play.py --model models/dd_combat_ppo_full_expedition.zip

Four classes to start, chosen because each has a sharp, checkable claim:

- **Plague Doctor** -- *"Blinding Gas is an insanely overpowered double
  backline Stun... one can usually open a battle with it"*. She is the class
  Fran singled out: *"doesnt make sense to me that doesnt focus when its
  arguable top 3 most overpowered non mod classes of the game"*.
- **The dodge trio** (Jester, Antiquarian, Man-at-Arms) -- Solo, Protect Me,
  Invigorating Vapours and Bolster. The known baseline is near zero: Crusader's
  Bulwark of Faith measured 0.0% of turns, so this is the direct read on
  whether `--prevention-reward` changed anything.
- **Abomination** -- his transform costs the party stress; does a comp
  fielding him come home afflicted? Fran's own ask.
- **Anyone with a DoT** -- is a DoT applied to something already dying to one?
"""
import argparse
import collections
import random
import sys
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sb3_contrib import MaskablePPO
from sb3_contrib.common.maskable.utils import get_action_masks
from sb3_contrib.common.wrappers import ActionMasker

from env.expedition_env import build_comp_pool
from env.expedition_sequence_env import EXPEDITION_LENGTH_BANDS, ExpeditionSequenceEnv
from env.gym_env import _outstanding_dot

# The claims, each traced to the guide it came from. Skill ids, because a
# display name is not stable -- "First Aid" is `first_aid` and the string table
# calls it "Wound Care".
OPENER_SKILLS = {"blinding_gas"}          # PD: "open a battle with it"
DODGE_SKILLS = {"solo", "protect_me", "invigorating_vapours", "bolster",
                "guard_dog"}
STRESS_HEAL_HINT = ("inspiring_cry", "battle_ballad", "harvest", "encourage")


def mask_fn(env):
    return env.action_masks()


def _skill_of(engine, hero, skill_id):
    return engine.get_skill_data(skill_id, level=hero.skill_tier)


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", default="models/dd_combat_ppo_full_expedition.zip")
    ap.add_argument("--episodes", type=int, default=40)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--prevention-reward", action="store_true",
                    help="match the arm the checkpoint was TRAINED in; the "
                         "reward does not change what is measured here, but a "
                         "mismatched env is off-distribution")
    args = ap.parse_args()

    base = ExpeditionSequenceEnv(sampler_seed=None,
                                 prevention_reward=args.prevention_reward)
    env = ActionMasker(base, mask_fn)
    model = MaskablePPO.load(args.model, env=env)
    store = base.store

    # Comps that actually FIELD the classes under test -- measuring a Plague
    # Doctor claim over comps with no Plague Doctor measures nothing.
    pool = build_comp_pool(store)
    wanted = {"plague_doctor", "jester", "antiquarian", "man_at_arms",
              "abomination"}
    # Round-robin ACROSS the classes under test rather than walking the pool
    # in order: `ep % len(relevant)` over 40 episodes touches 40 comps out of
    # 100+, and whether a Plague Doctor appears at all becomes luck. Each class
    # gets its own queue and they are drawn in turn, so every claim is measured
    # on comps that can actually exercise it.
    by_class = {cls: [r for r in pool if cls in r[2]] for cls in sorted(wanted)}
    by_class = {c: rows for c, rows in by_class.items() if rows}
    relevant = []
    if by_class:
        for i in range(max(len(rows) for rows in by_class.values())):
            for cls in by_class:
                if i < len(by_class[cls]):
                    relevant.append(by_class[cls][i])
    relevant = relevant or pool
    rng = random.Random(args.seed)

    turns_by_class = collections.Counter()
    heal_turns = collections.Counter()
    plays = collections.defaultdict(collections.Counter)   # class -> skill
    combats_seen = 0
    combats_with_pd = 0
    opener_used = 0
    opener_available_round1 = 0
    uses_spent = collections.Counter()
    uses_available = collections.Counter()
    dodge_offered = dodge_taken = 0
    dot_on_doomed = dot_total = 0
    abom_party_stress = []
    other_party_stress = []

    for ep in range(args.episodes):
        comp_name, zone_id, hero_ids = relevant[ep % len(relevant)]
        band = list(EXPEDITION_LENGTH_BANDS)[ep % 3]
        lo, hi = EXPEDITION_LENGTH_BANDS[band]
        seed = zlib.crc32(f"classplay|{comp_name}|{ep}".encode()) % (2 ** 31)
        opts = {"hero_ids": hero_ids, "comp_name": comp_name,
                "zone_id": zone_id, "tier": 5, "length_band": band,
                "num_combats": rng.randint(lo, hi)}
        obs, _ = env.reset(seed=seed, options=opts)

        eng = base.engine
        last_round = eng.round
        seen_this_combat = set()
        term = trunc = False
        while not (term or trunc):
            action = int(model.predict(obs, deterministic=True,
                                       action_masks=get_action_masks(env))[0])
            eng = base.engine
            if eng.round < last_round:                 # a new combat began
                combats_seen += 1
                seen_this_combat = set()
            last_round = eng.round

            actor_id = eng.get_current_actor_id()
            chosen = base._action_map.get(action)
            if actor_id is not None and chosen is not None:
                hero = eng.heroes[actor_id]
                hero_class = hero.definition_id
                turns_by_class[hero_class] += 1
                sid = chosen.skill_id
                if not sid.startswith("__"):
                    plays[hero_class][sid] += 1

                legal = {a.skill_id for a in eng.get_valid_actions(actor_id)}

                # Healing share, per class. THE number behind Fran's ask, and
                # his own guide's thesis: "simply trying to outheal enemy
                # offense is just a very bad idea". Any skill whose level data
                # restores HP counts, so it catches Battle Heal and Divine
                # Grace without naming either.
                sd_now = (_skill_of(eng, hero, sid)
                          if not sid.startswith("__") else None)
                if sd_now is not None and sd_now.heal_min is not None:
                    heal_turns[hero_class] += 1

                # -- PD: is Blinding Gas OPENED with, and is it spent at all?
                if hero_class == "plague_doctor":
                    for opener in OPENER_SKILLS & legal:
                        if eng.round == 0 and opener not in seen_this_combat:
                            opener_available_round1 += 1
                            seen_this_combat.add(opener)
                            if sid == opener:
                                opener_used += 1
                    sd = _skill_of(eng, hero, "blinding_gas")
                    if sd is not None and sd.per_battle_limit:
                        uses_available["blinding_gas"] += 1
                        if sid == "blinding_gas":
                            uses_spent["blinding_gas"] += 1

                # -- the dodge trio: offered vs taken
                offered = DODGE_SKILLS & legal
                if offered:
                    dodge_offered += 1
                    if sid in offered:
                        dodge_taken += 1

                # -- DoT into a target already dying to one
                sd = _skill_of(eng, hero, sid) if not sid.startswith("__") else None
                applies_dot = bool(sd and any(
                    (store.effects.get(n) is not None
                     and (store.effects[n].bleed_amount
                          or store.effects[n].blight_amount
                          or store.effects[n].burn_amount))
                    for n in (sd.effects or [])))
                if applies_dot and chosen.target_id is not None and chosen.target_id >= 0:
                    dot_total += 1
                    victim = eng._find_combatant_by_position(chosen.target_id,
                                                             enemy=True)
                    if victim is not None and victim.is_alive() and \
                            _outstanding_dot(victim) >= victim.hp:
                        dot_on_doomed += 1

            obs, _r, term, trunc, _info = env.step(action)

        final_stress = [h.stress for h in base.engine.heroes if h.is_alive()]
        mean_stress = sum(final_stress) / max(len(final_stress), 1)
        if "abomination" in hero_ids:
            abom_party_stress.append(mean_stress)
        else:
            other_party_stress.append(mean_stress)

    # ---------------------------------------------------------------- report
    def pct(n, d):
        return f"{100.0 * n / d:5.1f}%" if d else "    --"

    print(f"\nclass execution audit | {args.model}")
    print(f"  {args.episodes} episodes over {len(relevant)} comps fielding the "
          f"classes under test, Champion\n")

    print("PD-1  Blinding Gas is opened with")
    print("      guide: \"one can usually open a battle with it ... expect to get "
          "the most\n             value out of the Plague Doctor's first action\"")
    print(f"      opened with it {opener_used}/{opener_available_round1} of the "
          f"combats where it was legal on her first turn "
          f"({pct(opener_used, opener_available_round1)})")

    print("\nPD-2  Blinding Gas is spent at all")
    print("      guide: \"3 uses are more than enough for 99% of normal battles\" "
          "-- so\n             unspent uses are value left on the table")
    print(f"      chosen on {uses_spent['blinding_gas']}/"
          f"{uses_available['blinding_gas']} of her turns where it was legal "
          f"({pct(uses_spent['blinding_gas'], uses_available['blinding_gas'])})")

    print("\nDODGE  the party buffs are cast at all")
    print("      Fran: a Solo + Protect Me opening reaches \"ridiculous levels of "
          "immunity\".\n             Known baseline: Bulwark of Faith at 0.0% of turns")
    print(f"      taken on {dodge_taken}/{dodge_offered} of the turns one was "
          f"legal ({pct(dodge_taken, dodge_offered)})")

    print("\nDOT   a DoT aimed at something already dying to one")
    print("      wasted by construction -- the target dies before the new DoT "
          "can tick")
    print(f"      {dot_on_doomed}/{dot_total} DoT applications "
          f"({pct(dot_on_doomed, dot_total)})")

    print("\nABOM  does a comp fielding him come home stressed?")
    print("      his transform charges the party stress every round he holds it")
    if abom_party_stress:
        a = sum(abom_party_stress) / len(abom_party_stress)
        o = (sum(other_party_stress) / len(other_party_stress)
             if other_party_stress else float("nan"))
        print(f"      abomination comps {a:5.1f} mean final stress "
              f"({len(abom_party_stress)} runs) vs {o:5.1f} without "
              f"({len(other_party_stress)} runs)")
    else:
        print("      no comp fielding an Abomination was sampled")

    print("")
    print("HEAL  share of each class's turns spent restoring HP")
    print("      guide: Facetanking attacks and simply trying to outheal enemy")
    print("             offense is just a very bad idea in general")
    for hero_class in sorted(heal_turns,
                             key=lambda c: -heal_turns[c] / max(turns_by_class[c], 1)):
        total = turns_by_class[hero_class]
        if total < 20:
            continue
        print(f"      {hero_class:16s} {pct(heal_turns[hero_class], total)} of {total} turns")
    if not heal_turns:
        print("      no healing at all was played")

    print("\nPER-CLASS SKILL USE (share of that class's turns)")
    for hero_class in sorted(plays, key=lambda c: -turns_by_class[c]):
        total = turns_by_class[hero_class]
        if total < 20:
            continue
        top = plays[hero_class].most_common(6)
        line = "  ".join(f"{sid} {100.0 * n / total:.1f}%" for sid, n in top)
        print(f"  {hero_class:16s} ({total:4d} turns)  {line}")

    print("\nThis is a RULER. Nothing here is a reward term, an action mask or a")
    print("training target -- a policy that follows a per-class script is exactly")
    print("the un-scalable player these numbers exist to avoid building.")


if __name__ == "__main__":
    main()
