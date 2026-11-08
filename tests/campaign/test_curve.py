"""Tests for campaign/curve.py -- the learning curve pools both ruler blocks."""

import json
import sys
import tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from campaign import curve


def _write(root, stem, block, deads):
    rows = [{"dead": d, "success_rate": 0.5, "plots": 1} for d in deads]
    (root / f"{stem}_{block}.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")


def test_a_point_pools_both_blocks_with_its_standard_error():
    saved = curve.LOGS, curve.STEPS
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        _write(root, "SW_x", "s0", [10, 20])
        _write(root, "SW_x", "s40", [30, 40])
        curve.LOGS, curve.STEPS = root, [("v1", "SW_x", "start", "x")]
        try:
            (p,) = curve.curve()
        finally:
            curve.LOGS, curve.STEPS = saved
    assert p["n"] == 4
    mean, se = p["dead"]
    assert mean == 25
    assert abs(se - 12.909944 / 2) < 1e-5     # sd of 10..40 is 12.91, over sqrt(4)
    assert "| v1 | start | x | 4 | 25.00 +- 6.45" in curve.table([p])


def test_every_step_names_a_known_kind():
    assert {k for _l, _s, k, _w in curve.STEPS} <= {"start", "world", "decision", "ruling"}
    assert "v7" not in [l for l, *_ in curve.STEPS]       # harmful and reverted


if __name__ == "__main__":
    for t in (test_a_point_pools_both_blocks_with_its_standard_error,
              test_every_step_names_a_known_kind):
        t()
        print("  ok ", t.__name__)
