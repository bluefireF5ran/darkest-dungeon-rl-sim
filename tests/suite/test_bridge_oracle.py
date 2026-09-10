"""The bridge's refactor oracle has to be able to fail, like the engine's.

`tools/bridge_oracle.py` replays captured save snapshots through every
decision layer and hashes what the bridge says to do. It is the only
behavioural gate on `bridge/` -- `tools/trajectory_oracle.py` grades the
engine and the envs and does not reach the bridge at all -- so the same two
questions apply: is it deterministic, and can it tell.

Kept deliberately small. The full sweep is 8 cells and 14 seconds on the
command line; this file checks the instrument on one trace, because a suite
that pays 14 seconds for something the command line already does is a suite
people start skipping.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))

REPO = Path(__file__).resolve().parents[2]
RECORD = REPO / "archive" / "traces" / "bridge_oracle.json"
PROBE = "raidsnaps2"          # the richest capture: 733 lines of decisions


def _oracle():
    import bridge_oracle
    return bridge_oracle


def test_the_recording_exists_and_covers_the_traces():
    assert RECORD.exists(), (
        "no bridge recording. Run `python tools/bridge_oracle.py --emit` "
        "BEFORE refactoring anything under bridge/.")
    cells = json.loads(RECORD.read_text(encoding="utf-8"))
    assert len(cells) >= 4, cells.keys()
    traces = {k.split("/")[0] for k in cells}
    assert PROBE in traces, traces
    for key, cell in cells.items():
        assert len(cell["digest"]) == 32, key
    print(f"  PASS {len(cells)} cells over {len(traces)} traces")


def test_the_replay_is_deterministic_and_matches_the_recording():
    """One trace, twice, and against what was recorded.

    Without a model, so it does not depend on `models/` -- which is
    gitignored, and absent on a fresh clone.
    """
    T = _oracle()
    if not (REPO / "archive" / "traces" / PROBE).is_dir():
        pytest.skip(f"{PROBE} capture is not present")
    a = T.digest_trace(PROBE, None)
    b = T.digest_trace(PROBE, None)
    assert a["digest"] == b["digest"], "the bridge replay is not deterministic"
    assert a["lines"] > 100, f"only {a['lines']} lines -- is the capture empty?"

    recorded = json.loads(RECORD.read_text(encoding="utf-8"))
    key = f"{PROBE}/plan"
    if key in recorded:
        assert recorded[key]["digest"] == a["digest"], (
            "the bridge decides something different than when the recording "
            "was taken. If that was intended, re-emit; if not, this is the "
            "regression the oracle exists to catch.")
    print(f"  PASS {PROBE} reproduces ({a['lines']} lines of decisions)")


def test_the_oracle_notices_a_changed_decision():
    """Nudge a torch threshold and the digest must move.

    `RADIANT_FLOOR` is the light level the route planner treats as radiant, so
    moving it changes what the bridge says about nearly every corridor. Chosen
    by measuring, the same way the engine oracle's constants were: a knob the
    replay never reaches would make this test pass while proving nothing.
    """
    T = _oracle()
    if not (REPO / "archive" / "traces" / PROBE).is_dir():
        pytest.skip(f"{PROBE} capture is not present")
    import bridge.torchlight as torchlight

    clean = T.digest_trace(PROBE, None)
    old = torchlight.RADIANT_FLOOR
    try:
        torchlight.RADIANT_FLOOR = old * 2
        dirty = T.digest_trace(PROBE, None)
    finally:
        torchlight.RADIANT_FLOOR = old
    assert torchlight.RADIANT_FLOOR == old, "the mutation leaked out"
    assert clean["digest"] != dirty["digest"], (
        "the bridge oracle did not notice RADIANT_FLOOR doubling. An "
        "instrument that misses a threshold will miss a moved planner.")
    print("  PASS a changed threshold moves the digest")


if __name__ == "__main__":
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            _fn()
    print("ok")
