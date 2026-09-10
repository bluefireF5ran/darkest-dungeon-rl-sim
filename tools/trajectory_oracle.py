r"""Photograph what the simulator DOES, so a refactor can be proved harmless.

The 2026-09-08 refactor splits `engine/combat_engine.py` and `bridge/act.py`
into submodules. Every one of those edits is supposed to be behaviour-
preserving, and "supposed to be" is not a standard this project accepts
anywhere else: `ab_compare`, `agreement`, `comp_ga` and `eval_tutorial` all
carry a `--self-check` that has to print zeros, and `doc_migration_audit`
photographs the docs before they move. This is the same device pointed at the
engine.

    python tools/trajectory_oracle.py --emit        # before the first edit
    python tools/trajectory_oracle.py               # after every edit

A trajectory here is every observation, reward and terminal flag a
deterministic walk produces, reduced to one `blake2b` digest per cell. Byte-
identical or it did not happen.

**Both RNG streams are pinned.** `seed_scenario()` fixes the monsters, the
torch level, the tier and the loadouts; `reset(seed=)` fixes the combat rolls.
Pinning only one reads as a real difference when it is not -- that cost an
hour on the day `tests/suite/test_hot_paths.py` was written, and its
`_trajectory_digest` docstring says so. The digest recipe here is deliberately
the same one, because it is already proven: it is what showed
`_BASE_STEP_RETURNS_OBS` changed no trajectory.

**No model is loaded.** Actions are `legal[i % len(legal)]`, which makes the
walk hermetic, fast, and independent of which checkpoint happens to be on
disk -- and it still drives the whole engine, because the cycling index
reaches skills, moves, provisions and retreats rather than hammering one
action.

**The draw count is recorded next to the digest.** Two different code paths
can produce identical observations while consuming a different number of RNG
draws; the next scenario then diverges. `SeededRNG.get_state()` makes that
visible, and it is the signal most likely to catch a subtly reordered
extraction.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data.paths import REPO_ROOT                              # noqa: E402
from data.store_cache import load_store_cached                # noqa: E402
from data.paths import CSV_DIR, GAME_DIR                      # noqa: E402

DEFAULT_RECORD = REPO_ROOT / "archive" / "traces" / "trajectory_oracle.json"

#: env name -> (seeds, steps). The whole grid costs seconds, so it is sized
#: for COVERAGE rather than for speed: the cycling walk ends episodes often
#: (retreat and abandon are legal actions and the cycle reaches them), so the
#: step budget buys many short scenarios -- many comps, many zones, many enemy
#: rosters -- rather than a few long ones. The `rand` policy below is what
#: buys the long ones.
GRID = {
    "DarkestDungeonEnv": (tuple(range(8)), 800),
    "ExpeditionEnv": (tuple(range(8)), 800),
    "ExpeditionSequenceEnv": (tuple(range(6)), 800),
}
#: A genuine SUBSET of GRID -- same step budget, fewer seeds. It has to be a
#: subset: the digest depends on the step count, so a `--quick` run with its
#: own budget would mismatch every cell and report a refactor as broken when
#: it is not.
QUICK = {
    "DarkestDungeonEnv": ((0, 1), 800),
    "ExpeditionEnv": ((0, 1), 800),
    "ExpeditionSequenceEnv": ((0,), 800),
}

#: Two deterministic action rules, because they reach different code.
#: `cycle` walks the legal set in order, which hits retreat/abandon on a fixed
#: rhythm and so samples many short episodes. `rand` draws from the legal set
#: with its own seeded stream, which lets fights run long enough for buff
#: durations, DoT stacks and act-outs to matter.
POLICIES = ("cycle", "rand")


def _make(name, store):
    from env.gym_env import DarkestDungeonEnv
    from env.expedition_env import ExpeditionEnv
    from env.expedition_sequence_env import ExpeditionSequenceEnv
    if name == "DarkestDungeonEnv":
        return DarkestDungeonEnv(store=store)
    if name == "ExpeditionEnv":
        return ExpeditionEnv(store=store, sampler_seed=0)
    if name == "ExpeditionSequenceEnv":
        return ExpeditionSequenceEnv(store=store, sampler_seed=0)
    raise SystemExit(f"unknown env {name}")


def _draws(env):
    """`(seed, call_count)` of the combat RNG, or None if there is no engine.

    Recorded beside the digest because an extraction can preserve every
    observation and still consume a different number of draws -- which does
    not show up until the NEXT scenario, far from the change that caused it.
    """
    rng = getattr(getattr(env, "engine", None), "rng", None)
    if rng is None or not hasattr(rng, "get_state"):
        return None
    return list(rng.get_state())


def digest_cell(env, steps, seed, policy="cycle"):
    """One deterministic walk, reduced to a hash.

    The fields, their order and their formatting are verbatim from
    `tests/suite/test_hot_paths.py::_trajectory_digest`, so the two
    instruments cannot come to disagree about what "the same trajectory"
    means.
    """
    import random
    pick = random.Random(0xC0FFEE ^ seed)
    h = hashlib.blake2b(digest_size=16)
    if hasattr(env, "seed_scenario"):
        env.seed_scenario(seed)
    obs, _ = env.reset(seed=seed)
    h.update(obs.tobytes())
    resets = 0
    for i in range(steps):
        legal = np.flatnonzero(env.action_masks())
        if not len(legal):
            action = 36
        elif policy == "cycle":
            action = int(legal[i % len(legal)])
        else:
            action = int(legal[pick.randrange(len(legal))])
        obs, reward, term, trunc, _info = env.step(action)
        h.update(obs.tobytes())
        h.update(f"{reward:.12g}|{int(term)}{int(trunc)}".encode())
        if term or trunc:
            resets += 1
            if hasattr(env, "seed_scenario"):
                env.seed_scenario(seed + i)
            obs, _ = env.reset(seed=seed + i)
            h.update(obs.tobytes())
    return {"digest": h.hexdigest(), "resets": resets, "draws": _draws(env)}


def record(grid, store):
    out = {}
    for name, (seeds, steps) in grid.items():
        for policy in POLICIES:
            t0 = time.time()
            for seed in seeds:
                env = _make(name, store)
                cell = digest_cell(env, steps, seed, policy)
                cell["steps"] = steps
                out[f"{name}/{policy}/{seed}/{steps}"] = cell
            n = len(seeds)
            eps = sum(out[f"{name}/{policy}/{s}/{steps}"]["resets"]
                      for s in seeds)
            print(f"  {name:<24} {policy:<6} {n} seeds x {steps} steps  "
                  f"{eps:>4} episodes  {time.time() - t0:>5.1f}s")
    return out


def compare(before, after):
    keys = sorted(set(before) | set(after))
    bad = []
    for k in keys:
        b, a = before.get(k), after.get(k)
        if b is None:
            bad.append(f"{k}: not in the recording (new cell)")
        elif a is None:
            bad.append(f"{k}: missing now (cell disappeared)")
        elif b["digest"] != a["digest"]:
            bad.append(f"{k}: TRAJECTORY CHANGED  {b['digest']} -> {a['digest']}")
        elif b.get("draws") != a.get("draws"):
            bad.append(f"{k}: same observations, different RNG draw count "
                       f"{b.get('draws')} -> {a.get('draws')}")
    return bad


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--emit", action="store_true",
                    help="record the current behaviour and stop")
    ap.add_argument("--record", default=str(DEFAULT_RECORD),
                    help="where the recording lives")
    ap.add_argument("--quick", action="store_true",
                    help="one seed per env; for iterating, not for a verdict")
    args = ap.parse_args(argv)

    store = load_store_cached(str(CSV_DIR), str(GAME_DIR))
    grid = QUICK if args.quick else GRID
    path = Path(args.record)

    now = record(grid, store)

    if args.emit:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(now, indent=1), encoding="utf-8")
        try:
            shown = path.relative_to(REPO_ROOT).as_posix()
        except ValueError:                       # a scratch path is fine
            shown = str(path)
        print()
        print(f"wrote {shown}: {len(now)} cells")
        return 0

    if not path.exists():
        print(f"\nno recording at {path}; run --emit first")
        return 2
    before = json.loads(path.read_text(encoding="utf-8"))
    if args.quick:
        before = {k: v for k, v in before.items() if k in now}

    bad = compare(before, now)
    if bad:
        print(f"\n  {len(bad)} CELL(S) CHANGED:")
        for line in bad:
            print(f"    {line}")
        print("\n  A refactor that was supposed to preserve behaviour did not.")
        return 1
    print(f"\n  PASS {len(now)} cells byte-identical to the recording")
    return 0


if __name__ == "__main__":
    sys.exit(main())
