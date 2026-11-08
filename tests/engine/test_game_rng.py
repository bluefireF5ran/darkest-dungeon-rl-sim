"""The game's own RNG (`prng_minstd`) and the known-dice hook (DECOMP.md F15).

The traced values below are copied from the 2026-10-04 paired session
(`archive/recordings/20261004-213937/trace.log`): a Houndmaster turn whose
start-of-turn state was 1415862132.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from engine import game_rng as G

TURN_START = 1415862132
#: The six draws the game logged for that turn, in order.
TRACED = [128560117, 341337537, 931163222, 1346936465, 1336044228, 806326964]


def test_the_stream_is_park_miller_minstd():
    assert G.stream(TURN_START, 6) == TRACED


def test_a_uniform_is_the_state_over_the_modulus():
    assert abs(G.uniforms(TURN_START, 2)[1] - 0.158948) < 1e-6      # "Roll to hit"


def test_the_damage_draw_is_lo_plus_int_u_times_width():
    u5 = G.uniforms(TURN_START, 5)[4]                              # 0.6221441031
    assert G.int_range(u5, 5, 8) == 7                              # traced "Value: 7"


def test_the_bridge_reads_the_same_three_numbers():
    from bridge.fate import fate_from_seed
    f = fate_from_seed(TURN_START)
    assert abs(f.hit_roll - TRACED[1] / G.MINSTD_M) < 1e-12
    assert abs(f.damage_u - TRACED[4] / G.MINSTD_M) < 1e-12
    assert abs(f.crit_roll - TRACED[5] / G.MINSTD_M) < 1e-12
