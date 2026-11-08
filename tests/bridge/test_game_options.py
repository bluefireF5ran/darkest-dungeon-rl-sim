"""The campaign's gameplay options against what the simulator assumes.

DECOMP.md F11: the binary reads `retreats_can_fail`, `multiplied_enemy_crits`
and the rest at the point they matter, and `engine/` implements all of them
ON. A campaign with one off must be said out loud, not silently mispriced.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from bridge import game_options as GO


def test_every_option_on_is_no_divergence():
    assert GO.divergences({k: 1 for k in GO.SIMULATOR_ASSUMES}) == []


def test_an_option_switched_off_is_named():
    opts = {k: 1 for k in GO.SIMULATOR_ASSUMES}
    opts["multiplied_enemy_crits"] = 0
    lines = GO.divergences(opts)
    assert len(lines) == 1 and "multiplied_enemy_crits" in lines[0]


def test_an_unreadable_profile_warns_nothing_and_raises_nothing(tmp_path):
    assert GO.read_options(tmp_path) == {}
    assert GO.warn(tmp_path, say=lambda *_: None) == []

