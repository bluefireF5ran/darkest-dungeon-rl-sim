r"""Photograph what the BRIDGE decides, so its modules can be moved safely.

`tools/trajectory_oracle.py` grades the engine and the envs. It does not
reach `bridge/` at all -- the bridge reads saves and screens, not a Gym env --
so splitting `bridge/act.py` (6,887 lines) had no behavioural gate beyond the
unit tests. This is that gate.

    python tools/bridge_oracle.py --emit        # before the first edit
    python tools/bridge_oracle.py               # after every edit

`bridge.act.replay_plans` walks a directory of captured save snapshots and
prints, for each one, what every decision layer would do: the route and the
reason for it, the curio verdicts, the loot valuation, the torch call, and --
when a checkpoint is supplied -- the combat plan. That output is the bridge's
answer, in full, and it is deterministic: the planners take no RNG and a
policy queried with `deterministic=True` does not either. Two runs over
`raidsnaps2` produce identical bytes.

**Two kinds of cell, and the difference matters.** A `plan` cell runs with no
model, which keeps it hermetic and independent of whatever checkpoints happen
to be on disk -- `models/` is gitignored, so a fresh clone has none. A `model`
cell adds the shipped checkpoint and is what reaches `plan_battle` and the
advisor. Model cells are skipped, loudly, when the checkpoint is absent; they
are not silently dropped, because a comparison that quietly checks half of
what it did last time is the failure this whole class of tool exists to avoid.

What it does NOT cover: anything that talks to a live window. Backends,
`SendInput`, the hardware probes and the screen recognisers need a running
game, and no recording substitutes for one. Those are covered by
`tests/bridge/`, and a refactor that touches them is graded there.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data.paths import REPO_ROOT                                # noqa: E402

TRACES = ("raidsnaps", "raidsnaps2", "raidsnaps3", "raidsnaps4")

#: The shipped checkpoint. Absent on a fresh clone -- `models/` is gitignored.
MODEL = REPO_ROOT / "models" / "dd_combat_ppo_full_expedition_mods.zip"

DEFAULT_RECORD = REPO_ROOT / "archive" / "traces" / "bridge_oracle.json"


def digest_trace(trace: str, model: str | None) -> dict:
    """Replay one capture directory and reduce its whole report to a hash."""
    from bridge.act import replay_plans

    buf = io.StringIO()
    t0 = time.time()
    with contextlib.redirect_stdout(buf):
        replay_plans(REPO_ROOT / "archive" / "traces" / trace, model, 0)
    text = buf.getvalue()
    return {
        "digest": hashlib.blake2b(text.encode("utf-8"),
                                  digest_size=16).hexdigest(),
        "chars": len(text),
        "lines": len(text.splitlines()),
        "seconds": round(time.time() - t0, 1),
    }


def record(with_model: bool) -> dict:
    out = {}
    for trace in TRACES:
        if not (REPO_ROOT / "archive" / "traces" / trace).is_dir():
            print(f"  {trace:<12} MISSING, skipped")
            continue
        cell = digest_trace(trace, None)
        out[f"{trace}/plan"] = cell
        print(f"  {trace:<12} plan   {cell['digest']}  "
              f"{cell['lines']:>5} lines  {cell['seconds']:>5.1f}s")
        if with_model:
            cell = digest_trace(trace, str(MODEL))
            out[f"{trace}/model"] = cell
            print(f"  {trace:<12} model  {cell['digest']}  "
                  f"{cell['lines']:>5} lines  {cell['seconds']:>5.1f}s")
    return out


def compare(before: dict, after: dict) -> list:
    bad = []
    for key in sorted(set(before) | set(after)):
        b, a = before.get(key), after.get(key)
        if b is None:
            bad.append(f"{key}: new cell, not in the recording")
        elif a is None:
            bad.append(f"{key}: MISSING now -- the recording had it, this run "
                       "did not produce it")
        elif b["digest"] != a["digest"]:
            bad.append(f"{key}: DECISIONS CHANGED  {b['digest']} -> "
                       f"{a['digest']}  ({b['lines']} -> {a['lines']} lines)")
    return bad


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--emit", action="store_true",
                    help="record what the bridge decides today and stop")
    ap.add_argument("--record", default=str(DEFAULT_RECORD))
    ap.add_argument("--no-model", action="store_true",
                    help="plan cells only; skips the combat planner")
    args = ap.parse_args(argv)

    with_model = not args.no_model and MODEL.is_file()
    if not args.no_model and not MODEL.is_file():
        print(f"  NOTE {MODEL.name} is absent, so the combat-plan cells are "
              "not produced.\n       models/ is gitignored; this is expected "
              "on a fresh clone.")

    now = record(with_model)
    path = Path(args.record)

    if args.emit:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(now, indent=1), encoding="utf-8")
        print()
        print(f"wrote {path.name}: {len(now)} cells")
        return 0

    if not path.exists():
        print(f"\nno recording at {path}; run --emit first")
        return 2

    before = json.loads(path.read_text(encoding="utf-8"))
    if not with_model:
        # Compare only what this run could produce, but say so.
        skipped = [k for k in before if k.endswith("/model")]
        if skipped:
            print(f"  (not comparing {len(skipped)} model cells)")
        before = {k: v for k, v in before.items() if not k.endswith("/model")}

    bad = compare(before, now)
    if bad:
        print(f"\n  {len(bad)} CELL(S) CHANGED:")
        for line in bad:
            print(f"    {line}")
        return 1
    print(f"\n  PASS {len(now)} cells byte-identical to the recording")
    return 0


if __name__ == "__main__":
    sys.exit(main())
