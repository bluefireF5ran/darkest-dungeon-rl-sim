"""The refactor oracle has to be able to fail.

`tools/trajectory_oracle.py` is the instrument the 2026-09-08 refactor is
graded against: it photographs every observation, reward and terminal flag a
deterministic walk produces, and a split of `combat_engine.py` may only land
if the photograph is unchanged.

An instrument like that has one dangerous failure mode: **passing because it
cannot tell**. A digest over the wrong fields, a walk that never reaches the
code being moved, or a comparison that skips cells would all report PASS
forever, and the refactor would sail through on a green light that means
nothing. That is this repo's characteristic bug -- `tests/suite/` exists
because a test can stop running without anything going red -- so the oracle
gets the same treatment as the suite guards.

Three properties, and the last two are the ones worth having:

1. **Determinism.** The same walk twice is the same digest.
2. **Coverage.** The walk takes every action the environment ever makes legal.
3. **Discrimination.** One constant nudged, on each side of the engine/env
   boundary, and it must notice.

**Choosing the mutation is not arbitrary, and getting it wrong hides the
bug.** The first draft of this file nudged `PASS_TURN_STRESS` and watched the
oracle report nothing -- not because the oracle was blind, but because the
walk essentially never spends `ACTION_PASS`: it was legal once in sixteen
hundred steps.

That is a fact about the GAME, not about the instrument. Passing costs the
party stress and is only ever the right move when there is no other -- a lone
surviving back-rank hero with nothing that reaches, and in that spot a player
retreats instead if retreat is available (Fran, 2026-09-08). So the constant
is genuinely near-unreachable, and a mutation test built on it is vacuous.
`DEATHBLOW_RESIST_CAP` and `HEART_ATTACK_STRESS_THRESHOLD` are the same trap
for the same reason: they price rare events.

The two constants used below were chosen by measuring which ones the walk
actually reaches. Both flag every cell.
"""
import json
import random
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))

REPO = Path(__file__).resolve().parents[2]
RECORD = REPO / "archive" / "traces" / "trajectory_oracle.json"

#: Small probes: this file is about the instrument; the full sweep belongs on
#: the command line. The sizes are empirical -- smaller than these and the
#: discrimination checks start passing vacuously.
PROBE_ENV = {"DarkestDungeonEnv": ((0, 1), 300)}
PROBE_SEQ = {"ExpeditionSequenceEnv": ((0, 1), 400)}


def _oracle():
    import trajectory_oracle
    return trajectory_oracle


def _store():
    from tests._fixtures import get_store
    return get_store()


def test_the_recording_exists_and_covers_all_three_envs():
    assert RECORD.exists(), (
        f"no recording at {RECORD.relative_to(REPO).as_posix()}. Run "
        "`python tools/trajectory_oracle.py --emit` -- and note it has to be "
        "emitted BEFORE a refactor, not after, or it photographs the very "
        "thing it was supposed to catch.")
    cells = json.loads(RECORD.read_text(encoding="utf-8"))
    envs = {k.split("/")[0] for k in cells}
    assert envs == {"DarkestDungeonEnv", "ExpeditionEnv",
                    "ExpeditionSequenceEnv"}, envs
    assert len(cells) >= 20, f"only {len(cells)} cells recorded"
    for key, cell in cells.items():
        assert len(cell["digest"]) == 32, key
        assert cell["steps"] > 0, key
    print(f"  PASS recording has {len(cells)} cells over {len(envs)} envs")


def test_the_walk_is_deterministic():
    """Same seed, same policy, same digest -- twice in one process.

    If this fails, every verdict the oracle gives is noise and the refactor
    has no instrument at all.
    """
    T, store = _oracle(), _store()
    a = T.record(PROBE_ENV, store)
    b = T.record(PROBE_ENV, store)
    assert a.keys() == b.keys()
    for k in a:
        assert a[k]["digest"] == b[k]["digest"], f"{k} is not deterministic"
        assert a[k]["draws"] == b[k]["draws"], f"{k} draw count is not stable"
    print(f"  PASS {len(a)} cells reproduce exactly")


def test_the_walk_takes_every_action_the_env_allows():
    """The anti-vacuity check: an oracle only covers what its walk visits.

    Measured rather than assumed, because the numbers surprise in both
    directions. `DarkestDungeonEnv` is a fixed 2v2 board, so only 13 of its 39
    action slots are EVER legal -- the walk taking 13 is full coverage, not
    poor coverage. `ExpeditionSequenceEnv` makes 35 legal and the walk reaches
    them. What this forbids is the walk leaving a legal action unspent, which
    is what would let a moved method go unexercised.
    """
    from env.gym_env import ACTION_SPACE_SIZE
    T, store = _oracle(), _store()
    for probe in (PROBE_ENV, PROBE_SEQ):
        (name, (seeds, steps)), = probe.items()
        legal_ever = np.zeros(ACTION_SPACE_SIZE, dtype=int)
        taken = np.zeros(ACTION_SPACE_SIZE, dtype=int)
        for policy in T.POLICIES:
            pick = random.Random(0xC0FFEE ^ seeds[0])
            env = T._make(name, store)
            if hasattr(env, "seed_scenario"):
                env.seed_scenario(seeds[0])
            env.reset(seed=seeds[0])
            for i in range(steps):
                mask = np.asarray(env.action_masks())
                legal_ever += mask.astype(int)
                legal = np.flatnonzero(mask)
                if not len(legal):
                    action = 36
                elif policy == "cycle":
                    action = int(legal[i % len(legal)])
                else:
                    action = int(legal[pick.randrange(len(legal))])
                taken[action] += 1
                _o, _r, term, trunc, _i = env.step(action)
                if term or trunc:
                    if hasattr(env, "seed_scenario"):
                        env.seed_scenario(seeds[0] + i)
                    env.reset(seed=seeds[0] + i)
        unspent = sorted(
            np.flatnonzero((legal_ever > 0) & (taken == 0)).tolist())
        n_legal = int((legal_ever > 0).sum())
        assert not unspent, (
            f"{name}: these actions are legal but the walk never takes them, "
            f"so the oracle is blind to code they reach: {unspent}")
        print(f"  PASS {name}: walk takes all {n_legal} ever-legal actions")


def _flagged(probe, module_name, attr, new_value):
    """Nudge one constant; return (cells flagged, cells total)."""
    import importlib
    T, store = _oracle(), _store()
    mod = importlib.import_module(module_name)
    clean = T.record(probe, store)
    old = getattr(mod, attr)
    try:
        setattr(mod, attr, new_value)
        dirty = T.record(probe, store)
    finally:
        setattr(mod, attr, old)
    assert getattr(mod, attr) == old, "the mutation leaked out of the test"
    assert not T.compare(clean, clean), "the oracle flags an unchanged run"
    return len(T.compare(clean, dirty)), len(clean)


def test_the_oracle_notices_an_engine_change():
    """`CORPSE_ALIVE_ROUND_LIMIT` -- corpses appear in nearly every fight."""
    flagged, total = _flagged(
        PROBE_SEQ, "engine.combat_engine", "CORPSE_ALIVE_ROUND_LIMIT", 2)
    assert flagged == total, (
        f"the oracle noticed {flagged} of {total} cells when the corpse round "
        "limit moved 3 -> 2. An instrument that can miss a constant will miss "
        "a mis-extracted method.")
    print(f"  PASS an engine constant flags all {total} cells")


def test_the_oracle_notices_an_env_change():
    """`W_STRESS` sits in the potential, so it moves every reward."""
    flagged, total = _flagged(PROBE_ENV, "env.gym_env", "W_STRESS", 0.31)
    assert flagged == total, (
        f"the oracle noticed {flagged} of {total} cells when W_STRESS moved.")
    print(f"  PASS an env constant flags all {total} cells")


def test_quick_is_a_subset_of_the_full_grid():
    """`--quick` shares the full grid's step budget on purpose.

    The digest depends on the step count, so a quick mode with its own budget
    would mismatch every cell and report a clean refactor as broken. That bug
    was live for one run of this tool.
    """
    T = _oracle()
    for name, (seeds, steps) in T.QUICK.items():
        assert name in T.GRID, name
        full_seeds, full_steps = T.GRID[name]
        assert steps == full_steps, (
            f"{name}: QUICK uses {steps} steps, GRID uses {full_steps}; "
            "quick cells would never match the recording")
        assert set(seeds) <= set(full_seeds), name
    print("  PASS QUICK is a strict subset of GRID")


if __name__ == "__main__":
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            _fn()
    print("ok")
