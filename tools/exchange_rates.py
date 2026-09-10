"""What does the training reward think a hero is WORTH? — and what does the
game charge?

Fran, 2026-09-03: *"I don't really know what should be the optimal scoring
parameters for the model as well as weights"*, with the worry attached: *"if I
say hp is important, maybe the model will play too slow and get
stress-defeated"*.

That worry is exactly right, and this project has already measured it once:
`--stress-weight` **lowered end-of-run stress and traded HP for it at par,
buying no score**. Turning one knob moves the policy ALONG a tradeoff curve
rather than up it, which is why three separate single-knob sweeps
(`--stress-weight`, `--prevention-reward` twice) have come back null.

So do not guess the next weight. **The game already prices these things**, and
`bridge/retreat.py` already uses that fact to decide a retreat: the Abbey sells
stress relief for gold, and `upgrades/heroes/<class>.upgrades.json` says what
was sunk into a hero. `data/town_costs.py` reads both. This tool puts the
training reward's implied rates and the game's real ones in one table.

## What it does NOT do

It changes no constant and recommends no specific number. A reward change is a
from-scratch retrain (`--resume` is invalid across one) and has to clear the
same bar the last three did: a paired arm on **two seeds**, because between-seed
variance here beats the paired standard error. The output is evidence for that
decision, not the decision.

    python tools/exchange_rates.py
    python tools/exchange_rates.py --roster-value 23750   # a real campaign's
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data.town_costs import gold_per_stress, hero_investment
from env.expedition_sequence_env import (END_STRESS_SCALE, REWARD_EXPEDITION_COMPLETE,
                                         REWARD_HERO_DEATH, REWARD_TRUNCATION,
                                         TERMINAL_STRESS_PENALTY_WEIGHT)
from env.gym_env import STRESS_TICK_PENALTY, TURN_PENALTY_THREAT

from data.paths import GAME_DIR

#: `ExpeditionSequenceEnv.__init__` sets this on the instance rather than at
#: module scope, so it cannot be imported. Kept here with its source named so a
#: change there is a visible mismatch rather than a silently stale table.
REWARD_WIN_SEQUENCE = 8.0            # env/expedition_sequence_env.py, `self._reward_win`

#: `_mean_roster_stress_frac` divides by 200, not by `END_STRESS_SCALE` (100).
#: The two scales live in the same file and mean different things -- 100 is the
#: Resolve Check, used by `combat_end_condition`; 200 is the ceiling, used by
#: the terminal penalty. Getting this wrong overstates the stress price by 2x.
ROSTER_STRESS_CEILING = 200.0
PARTY = 4


def reward_side(party: int = PARTY) -> dict:
    """What one unit of each thing costs in TRAINING REWARD.

    Stress is a range, not a number, and the range is the honest answer: a
    point charged by `STRESS_TICK_PENALTY` when it lands is paid whether or not
    it survives to the end, while the terminal term only charges what the party
    still carries. So a point healed before the run ends costs the tick alone.
    """
    tick = STRESS_TICK_PENALTY
    # d(terminal penalty)/d(one hero's stress point), with the penalty being
    # `W * mean over the roster of min(stress,200)/200`.
    terminal = TERMINAL_STRESS_PENALTY_WEIGHT / (party * ROSTER_STRESS_CEILING)
    return {
        "death": abs(REWARD_HERO_DEATH),
        "combat win": REWARD_WIN_SEQUENCE,
        "expedition complete": REWARD_EXPEDITION_COMPLETE,
        "truncation": abs(REWARD_TRUNCATION),
        "stress point (healed later)": tick,
        "stress point (carried home)": tick + terminal,
        "one turn": abs(TURN_PENALTY_THREAT),
    }


def game_side(game_dir=GAME_DIR, roster_value: float | None = None) -> dict:
    """What one unit of each thing costs in GOLD, from shipped files."""
    out = {
        "stress point (Abbey, un-upgraded)": gold_per_stress(game_dir, 0),
        "stress point (Abbey, fully upgraded)": gold_per_stress(game_dir, 3),
        "a fresh recruit": 0.0,          # PLAYBOOK 0.1: free from the coach
    }
    for rank in (2, 4):
        out[f"a rank-{rank} crusader"] = float(hero_investment(
            game_dir, "crusader", weapon_rank=rank, armour_rank=rank,
            skill_ids=["smite", "stunning_blow", "holy_lance"]))
    if roster_value:
        out["a measured campaign hero"] = float(roster_value)
    return out


def _fmt(value: float) -> str:
    return f"{value:,.3f}" if value < 1 else f"{value:,.0f}"


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--game-dir", default=str(GAME_DIR))
    ap.add_argument("--party", type=int, default=PARTY)
    ap.add_argument("--roster-value", type=float, default=23750.0,
                    help="gold sunk in a real campaign hero; the default is the "
                         "figure measured over Fran's Week 22 roster")
    args = ap.parse_args()

    rew = reward_side(args.party)
    game = game_side(Path(args.game_dir), args.roster_value)

    print("=" * 72)
    print("WHAT THE TRAINING REWARD CHARGES  (env/gym_env.py, "
          "env/expedition_sequence_env.py)")
    print("=" * 72)
    for k, v in rew.items():
        print(f"  {k:34s} {_fmt(v):>12s}")
    print()
    print(f"  stress: {STRESS_TICK_PENALTY} when it lands, plus "
          f"{TERMINAL_STRESS_PENALTY_WEIGHT}/({args.party} x "
          f"{ROSTER_STRESS_CEILING:.0f}) if it is still there at the end.")
    print(f"  (END_STRESS_SCALE is {END_STRESS_SCALE:.0f} and is a DIFFERENT "
          f"scale -- it feeds combat_end_condition, not this.)")
    print()

    print("=" * 72)
    print("WHAT THE GAME CHARGES  (data/town_costs.py, off shipped files)")
    print("=" * 72)
    for k, v in game.items():
        print(f"  {k:38s} {v:>10,.1f} gold")
    print()

    print("=" * 72)
    print("THE SAME QUESTION IN BOTH CURRENCIES:  how many points of stress is")
    print("one hero's life worth?")
    print("=" * 72)
    death = rew["death"]
    lo = death / rew["stress point (carried home)"]
    hi = death / rew["stress point (healed later)"]
    print(f"  training reward{'':22s}{lo:>9,.0f} : 1   to {hi:,.0f} : 1")
    print("    (the range is whether the stress is healed before the run ends;")
    print("     one CONSTANT either way, whoever the hero is)")
    print()
    for tier_name, rate in (("un-upgraded Abbey", game["stress point (Abbey, un-upgraded)"]),
                            ("upgraded Abbey", game["stress point (Abbey, fully upgraded)"])):
        print(f"  game, {tier_name}:")
        for who in ("a fresh recruit", "a rank-2 crusader", "a rank-4 crusader",
                    "a measured campaign hero"):
            if who not in game:
                continue
            print(f"    {who:34s}{game[who] / rate:>9,.0f} : 1")
    print()

    print("=" * 72)
    print("WHAT THIS SAYS")
    print("=" * 72)
    print("""\
  THE GAME'S RATIO IS NOT A CONSTANT. It runs from 0:1 for a fresh recruit --
  who is free from the stage coach, PLAYBOOK 0.1 -- to a few thousand to one
  for a kitted veteran, and it moves again with the Abbey's own upgrades. The
  training reward has ONE number for all of them.

  And that number sits at the TOP of the game's range. So the policy is trained
  to treat every hero as a max-investment veteran, including the resolve-0
  Apprentice parties `TIER_TO_RESOLVE_WEIGHTS` actually draws 20% of the time.
  Over-protecting a cheap hero is not a free error: it buys caution with turns
  and turns cost stress, which is precisely the "plays too slow and gets
  stress-defeated" failure mode -- and it is consistent with `--stress-weight`
  coming back as a straight HP-for-stress trade at par.

  SECOND, AND SEPARATELY: a completed expedition is worth 0.20 of a hero in the
  reward and about 0.10 of one in the game, so the reward makes FINISHING
  roughly twice as attractive, relative to losing somebody, as the economy
  does. That compounds with the retreat layer's own bias -- see
  `tools/retreat_audit.py`.

  WHAT NOT TO CONCLUDE. This is not "set REWARD_HERO_DEATH to X". Gold is the
  currency the TOWN trades in; the reward's job is to order in-combat decisions,
  and the two need not share a scale for the ordering to be right. What the
  table does establish is that a single constant cannot express a quantity the
  game varies by three orders of magnitude, and that the constant chosen is at
  one extreme of it. The cheap, testable version of the change is to scale the
  death term by the hero's OWN investment -- which the simulator already knows,
  since it sets gear ranks per tier.

  Whatever is tried: two seeds, paired, or it is not evidence.""")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
