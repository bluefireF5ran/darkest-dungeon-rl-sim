"""Were the abandoned runs actually lost, or are they points left on the table?

    python tools/abandon_audit.py <checkpoint> [episodes]


The trained checkpoint abandons 16.3% of expeditions, at 62% of the way through
with 0.36 deaths already taken. That LOOKS like cutting losses, but "looks like"
is not evidence -- the same numbers are consistent with a policy that has
learned to bail on runs it could still have won.

The experiment is a counterfactual, not a description:

  arm A   abandon allowed          (what the checkpoint actually does)
  arm B   abandon masked OFF       (same policy, same seeds, forced to play on)

With `deterministic=True` the policy is a function of state, so both arms
produce IDENTICAL trajectories up to the moment A abandons -- B then continues
from exactly that position. So for every run A abandoned, B tells us what
playing on would have got.

  If B mostly wipes            -> the abandons were correct, and cheap.
  If B mostly completes        -> the policy is bailing on winnable runs.

COMPLETED IS NOT SUCCESS, and the difference decides whether this is a
lever. `eval_expedition`'s SUCCESS is completed AND zero deaths, and two
thirds of abandons happen AFTER a hero has already died -- those runs had
forfeited SUCCESS before the exit was taken, so abandoning them costs
nothing. Measured 2026-08-30 on the shipped checkpoint over 700 episodes:
18 abandons, 12 of them already down a hero, and of the 6 clean ones only
ONE completed with zero deaths when forced on -- 0.14pp of SUCCESS, an
order of magnitude under the long band's own +-1.38pp error bar. Read the
zero-death column below, not `completed`.
"""
import sys
import statistics as st
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sb3_contrib import MaskablePPO
from sb3_contrib.common.maskable.utils import get_action_masks

from env.expedition_sequence_env import ExpeditionSequenceEnv
from env.gym_env import ACTION_ABANDON

MODEL = sys.argv[1] if len(sys.argv) > 1 else "models/endstate_20260824.zip"
EPISODES = int(sys.argv[2]) if len(sys.argv) > 2 else 300

model = MaskablePPO.load(MODEL, device="cpu")

_real_can_abandon = ExpeditionSequenceEnv._can_abandon
ALLOW = {"on": True}


def _gated(self):
    return _real_can_abandon(self) if ALLOW["on"] else False


ExpeditionSequenceEnv._can_abandon = _gated


# One env per arm, reused. Building a fresh one per episode restarts
# `_sampler_rng` from the same seed every time, so EVERY episode draws the
# identical comp/band/tier -- the first version of this script did exactly that
# and reported "100% of runs abandoned", all of them the same long/Champion
# scenario. Reseeding the sampler per episode is the documented pattern
# (tools/ab_compare.py) and is what makes the two arms face the same dungeon.
ENVS = {True: ExpeditionSequenceEnv(sampler_seed=0),
        False: ExpeditionSequenceEnv(sampler_seed=0)}


def run(seed, allow):
    ALLOW["on"] = allow
    env = ENVS[allow]
    env._sampler_rng.seed(seed)
    obs, _ = env.reset(seed=seed)
    # DEATHS ARE CUMULATIVE AND THE ROSTER RE-PACKS: a dead hero is dropped
    # from the next combat's `engine.heroes`, so counting !is_alive() there
    # undercounts. It reported runs that had already lost a hero as later
    # "completing with zero deaths", which is impossible. eval_expedition
    # uses `len(hero_ids) - len(living)`; this is that quantity.
    orig = len(env.engine.heroes)
    snap = None
    term = trunc = False
    steps = 0
    while not (term or trunc) and steps < 900:
        steps += 1
        masks = get_action_masks(env)
        a = int(model.predict(obs, deterministic=True, action_masks=masks)[0])
        if allow and a == ACTION_ABANDON:
            alive = [h for h in env.engine.heroes if h.is_alive()]
            cap = sum(h.max_hp for h in alive)
            snap = {
                "frac": env._combat_idx / max(1, env._num_combats),
                "combats_left": env._num_combats - env._combat_idx,
                "hp": (sum(h.hp for h in alive) / cap) if cap else 0.0,
                "stress": st.mean([h.stress for h in alive]) if alive else 0.0,
                "deaths": orig - sum(1 for h in env.engine.heroes if h.is_alive()),
                "food": env._provision_inventory.get("food", 0),
                "tier": env._tier, "band": env._length_band,
            }
        obs, r, term, trunc, info = env.step(a)
    alive = [h for h in env.engine.heroes if h.is_alive()]
    deaths = orig - len(alive)
    abandoned = bool(getattr(env, "_abandoned", False))
    if abandoned:
        outcome = "abandoned"
    elif not alive:
        outcome = "wipe"
    elif trunc:
        outcome = "truncated"
    elif env._combat_idx >= env._num_combats - 1:
        outcome = "completed"
    else:
        outcome = "other"
    return outcome, deaths, snap


rows = []
for ep in range(EPISODES):
    a_out, a_deaths, snap = run(20_000 + ep, True)
    if a_out != "abandoned":
        continue
    b_out, b_deaths, _ = run(20_000 + ep, False)
    rows.append((snap, b_out, b_deaths))

print(f"model={MODEL}  episodes={EPISODES}  abandoned={len(rows)} "
      f"({len(rows)/EPISODES*100:.1f}%)")
if not rows:
    sys.exit(0)

print()
print("  STATE AT THE MOMENT OF ABANDONING")
s = [r[0] for r in rows]
print(f"    through the run   {st.mean(x['frac'] for x in s)*100:5.1f}%   "
      f"combats left {st.mean(x['combats_left'] for x in s):.1f}")
print(f"    party HP          {st.mean(x['hp'] for x in s)*100:5.1f}%")
print(f"    mean stress       {st.mean(x['stress'] for x in s):5.1f}")
print(f"    heroes dead       {st.mean(x['deaths'] for x in s):5.2f}")
print(f"    food left         {st.mean(x['food'] for x in s):5.1f}")
bands = Counter(x["band"] for x in s)
tiers = Counter(x["tier"] for x in s)
print(f"    bands {dict(bands)}   tiers {dict(sorted(tiers.items()))}")

print()
print("  COUNTERFACTUAL -- what the SAME policy did when forced to play on")
c = Counter(r[1] for r in rows)
for k in ("completed", "wipe", "truncated", "other"):
    if c[k]:
        print(f"    {k:10s} {c[k]:4d}  {c[k]/len(rows)*100:5.1f}%")
print(f"    deaths taken by playing on: {st.mean(r[2] for r in rows):.2f} per run")
# The SUCCESS-relevant split. `completed` above counts runs that finish WITH
# deaths, which still fail the eval's SUCCESS gate -- so the headline below
# overstates the lever unless this is read with it.
_clean = [r for r in rows if r[0]["deaths"] == 0]
_recovered = sum(1 for r in _clean if r[1] == "completed" and r[2] == 0)
print(f"    of those, abandoned with ZERO deaths so far: {len(_clean)}"
      f" of {len(rows)} -- the rest had already forfeited SUCCESS")
print(f"    -> SUCCESS actually recoverable by never abandoning: {_recovered}"
      f" of {EPISODES} episodes = {_recovered/EPISODES*100:.2f}pp")
print()
# THE VERDICT IS SCORED ON SUCCESS, NOT ON `completed`. Scoring it on
# `completed` printed "the policy is bailing out of runs it could have
# finished" directly beneath a line showing 0.00pp of SUCCESS recoverable --
# because most of those completions arrive with a dead hero, and a dead hero
# fails the gate however the run ends.
recover_pp = _recovered / EPISODES * 100
won = c["completed"] / len(rows)
if recover_pp >= 1.0:
    print(f"    -> {recover_pp:.2f}pp of SUCCESS is being left on the table."
          " The policy is bailing out of runs it could have WON CLEAN.")
else:
    print(f"    -> only {recover_pp:.2f}pp of SUCCESS is recoverable, so the exit"
          " is correctly priced: {} of {} abandons had already lost a hero."
          .format(len(rows) - len(_clean), len(rows)))
    print(f"       ({won*100:.0f}% of them do reach the end when forced on, but at"
          f" {st.mean(r[2] for r in rows):.2f} deaths per run, which is not a SUCCESS.)")
