"""How many battles can the party hold before it loses someone?

Fran's idea: *"what if another metric is how many battles can the model hold
without someone dying, what if instead of dungeons of X battles, we go endless
essentially until the model loses someone?"*

## Why it is worth having

**SUCCESS has run out of resolution.** It is a gate -- finished every combat,
nobody died -- and the shipped checkpoint reads 96.4% short and 94.7% medium,
so it cannot resolve an improvement there at all. Battles-to-first-death is a
COUNT with a long tail and no ceiling, and it is the objective stated directly
rather than through a proxy.

**And it should be the ruler on which the expert's advice finally pays.** A
stress heal, a buff, a control skill and an HP-conserving line all cost a turn
now for value later; over eight combats that value barely arrives, and over
forty it is the whole game. `tools/style_probe.py` and the tier list have said
for weeks that the policy under-uses exactly those, and every attempt to
explain WHY has come back null -- most recently the fight-length reading, which
was retracted once it was controlled for party size and tier. **This mode makes
the prediction testable**: if the tier list is right, a policy that spends turns
on sustain should go measurably further here than one that does not, on the
same checkpoint family where SUCCESS cannot tell them apart.

## What it is NOT

**It is a comparative ruler, not a statement about skill.** The policy trains on
8-18 combat expeditions and its value function assumes an end; past that it is
extrapolating, so a low number can mean "never trained for this" rather than
"plays badly". Compare checkpoints on it. Do not read an absolute.

**And it is not the game.** Darkest Dungeon has no endless dungeon: a real
expedition ends and the heroes heal in town. So unlike fight length, this can
never be validated against real play -- there is nothing to validate against.

**RULER ONLY**, like `tools/skill_tiers.py` and `tools/check_class_play.py`:
nothing under `engine/`, `env/`, `data/`, `bridge/` or `train.py` may import it,
and a test enforces that. Training a policy to survive an endless dungeon would
be training it for a mode the game does not have.

## The one exploit that has to be closed

**`allow_abandon=False` is not optional.** `ExpeditionSequenceEnv`'s own notes
record a probe where the policy abandoned 62% of runs "at a mean 61% party HP
and 0.05 deaths -- i.e. no deaths, no lose-reward". A metric that counts
battles survived without a death is trivially maximised by quitting at combat
one, and abandon is the button that does it. It is disabled here, and the
run reports abandons so a future change that re-enables it cannot pass quietly.

Retreat is left available. It is a legal play, it does NOT advance the combat
count (a fled room is re-taken), so retreating forever ends in truncation --
a censored observation, not a survival. The retreat count is reported.

    python tools/endless.py --model models/dd_combat_ppo_full_expedition.zip
    python tools/endless.py --episodes 40 --cap 60
    python tools/endless.py --a <ckpt-A> --b <ckpt-B>      # two checkpoints
"""
from __future__ import annotations

import argparse
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

#: How far to let a run go. Not a measurement -- a budget. Runs that reach it
#: are CENSORED, never counted as "died at the cap", and the survival curve is
#: what keeps that honest.
DEFAULT_CAP = 60
DEFAULT_EPISODES = 20


@dataclass
class Run:
    """One endless expedition."""
    #: Combats fully cleared before the party first lost someone.
    died_after: Optional[int]
    #: Combats cleared when the run ended for any reason.
    reached: int
    why: str
    retreats: int
    abandoned: bool

    @property
    def censored(self) -> bool:
        """No death was observed, so the true horizon is beyond this run."""
        return self.died_after is None


def kaplan_meier(runs) -> list:
    """`[(k, S(k))]` -- the share of parties still whole after `k` combats.

    Censoring is the whole reason this is not just a mean. A run that reaches
    the cap intact, or truncates on the step budget, tells us the party lasted
    AT LEAST that long and nothing more; averaging it in as if it had died
    there biases the answer down, and dropping it biases the answer up.
    """
    events = sorted({r.died_after for r in runs if r.died_after is not None})
    surv, out = 1.0, []
    for k in events:
        at_risk = sum(1 for r in runs
                      if (r.died_after is None and r.reached >= k)
                      or (r.died_after is not None and r.died_after >= k))
        died = sum(1 for r in runs if r.died_after == k)
        if at_risk:
            surv *= (1.0 - died / at_risk)
        out.append((k, surv))
    return out


def median_survival(curve) -> Optional[int]:
    """First `k` where the curve drops to 0.5 or below, or None if it never
    does -- which is the honest answer when most runs are censored."""
    for k, s in curve:
        if s <= 0.5:
            return k
    return None


def play(model_path: str, episodes: int, cap: int, seed: int = 0) -> list:
    """Run `episodes` endless expeditions and record where each one broke.

    The scenario is the SAMPLED one -- comp, zone, tier, gear, torch and
    provisions all drawn the way training draws them -- and only the LENGTH is
    overridden. That is deliberate: the question is how long a normal party
    holds, so everything except the horizon should be the ordinary world.

    Extending the run after `reset` means reaching into three attributes the
    env sets from `_num_combats`. It is done here rather than by adding a knob
    to `env/` because this is a ruler and the env must not learn about it. Each
    one is set for a stated reason:

    * `_num_combats` -- the horizon itself;
    * `_max_steps` -- the step budget is `STEP_POOL_PER_COMBAT` per combat, so
      leaving it would truncate every run at the ORIGINAL length and censor
      the entire sample at once;
    * `_camp_after` -- the camp schedule is computed at reset from the old
      length, so without recomputing it every camp lands in the first few
      combats and the rest of the run has none. That is not "endless with
      camping", it is a different experiment.

    **Provisions are NOT topped up**, and that is the design: a fixed pack that
    has to last is what makes this a test of attrition rather than of combat
    alone. It also means the absolute number depends on
    `LENGTH_BAND_PROVISION_MULT`, which is a simulator convention -- another
    reason to read this comparatively.
    """
    from sb3_contrib import MaskablePPO
    from sb3_contrib.common.maskable.utils import get_action_masks

    from env.expedition_sequence_env import (FIREWOOD_PER_BAND,  # noqa: PLC0415
                                             STEP_POOL_PER_COMBAT,
                                             ExpeditionSequenceEnv)

    env = ExpeditionSequenceEnv(sampler_seed=seed, resolve_sampling=True,
                                allow_abandon=False)
    model = MaskablePPO.load(model_path, device="cpu")
    runs: list = []
    for ep in range(episodes):
        env.seed_scenario(seed + ep)
        obs, _ = env.reset(seed=seed + ep)
        env._num_combats = cap
        env._max_steps = STEP_POOL_PER_COMBAT * cap
        env._camp_after = env._camp_schedule(
            cap, FIREWOOD_PER_BAND.get(env._length_band, 0))

        started = sum(1 for h in env.engine.heroes if h.is_alive())
        died_after: Optional[int] = None
        term = trunc = False
        info: dict = {}
        steps = 0
        while not (term or trunc) and steps < STEP_POOL_PER_COMBAT * cap + 50:
            action = int(model.predict(obs, deterministic=True,
                                       action_masks=get_action_masks(env))[0])
            obs, _r, term, trunc, info = env.step(action)
            steps += 1
            if died_after is None:
                alive = sum(1 for h in env.engine.heroes if h.is_alive())
                if alive < started:
                    # `_combat_idx` is the fight in progress, so the party
                    # CLEARED that many before losing anybody.
                    died_after = int(getattr(env, "_combat_idx", 0))
        reached = int(getattr(env, "_combat_idx", 0))
        why = ("death" if died_after is not None else
               "wipe" if not any(h.is_alive() for h in env.engine.heroes) else
               "cap" if reached >= cap - 1 else
               "truncated" if trunc else str(info.get("winner", "ended")))
        runs.append(Run(died_after=died_after, reached=reached, why=why,
                        retreats=int(getattr(env, "_combats_retreated", 0)),
                        abandoned=bool(info.get("abandoned"))))
    return runs


def report(label: str, runs, cap: int = DEFAULT_CAP) -> None:
    if not runs:
        print(f"  {label}: no runs\n")
        return
    curve = kaplan_meier(runs)
    med = median_survival(curve)
    deaths = [r for r in runs if not r.censored]
    print(f"  {label}")
    print(f"    runs {len(runs):4d}   lost someone {len(deaths):4d}   "
          f"censored {len(runs) - len(deaths):4d}")
    print(f"    median battles before a death   "
          f"{med if med is not None else 'not reached'}")
    if deaths:
        got = sorted(r.died_after for r in deaths)
        print(f"    of the runs that lost someone: median {statistics.median(got):.0f}"
              f"   range {got[0]}-{got[-1]}")
    # Never past the cap: beyond it the curve is flat only because nothing
    # could have happened there, and printing it reads as evidence.
    for k in (1, 3, 5, 8, 12, 20, 30, 45):
        if k > cap:
            break
        surv = [s for j, s in curve if j <= k]
        if surv:
            print(f"    still whole after {k:2d} combats   {surv[-1]:5.0%}")
    abandoned = sum(1 for r in runs if r.abandoned)
    if abandoned:
        print(f"    !! {abandoned} run(s) ABANDONED -- the metric is being "
              f"gamed; allow_abandon must stay False")
    print(f"    retreats per run {statistics.mean(r.retreats for r in runs):.2f}")
    print()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", default="models/dd_combat_ppo_full_expedition.zip")
    ap.add_argument("--b", default=None, help="a second checkpoint to compare")
    ap.add_argument("--episodes", type=int, default=DEFAULT_EPISODES)
    ap.add_argument("--cap", type=int, default=DEFAULT_CAP)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args(argv)

    print(f"How many battles before someone dies? "
          f"(cap {args.cap}, {args.episodes} runs, seed {args.seed})\n")
    report(Path(args.model).name,
           play(args.model, args.episodes, args.cap, args.seed), args.cap)
    if args.b:
        report(Path(args.b).name,
               play(args.b, args.episodes, args.cap, args.seed), args.cap)
        print("  Both arms face the SAME sampled scenarios -- `seed_scenario`")
        print("  is reseeded per episode from the same seed -- so the two are")
        print("  paired on the dungeon, not on two independent draws.")
        print()
    print("  A run that reaches the cap or truncates is CENSORED: it says the")
    print("  party lasted AT LEAST that long. Averaging those in biases the")
    print("  answer down and dropping them biases it up, which is why the")
    print("  headline is a survival curve and not a mean.")
    print()
    print("  Read it COMPARATIVELY. The policy trains on 8-18 combat runs and")
    print("  its value function assumes an end, so past that it extrapolates:")
    print("  a low number can mean 'never trained for this'.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
