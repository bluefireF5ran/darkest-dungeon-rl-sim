"""`tools/damage_oracle.py`: a damage draw from the game's trace, paired with
the HP its target lost, graded under the game's PROT rule (DECOMP.md F9) and
the engine's."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools import damage_oracle as DO


def test_the_two_rules_where_they_part():
    # A 1-3 hit into 10% PROT: the game cuts at least one point.
    assert DO.game_damage(1, 0.10) == 0
    assert DO.engine_damage(1, 0.10) == 1
    assert DO.game_damage(3, 0.10) == 2
    # No PROT: both are the draw itself.
    assert DO.game_damage(6, 0.0) == DO.engine_damage(6, 0.0) == 6
    # roundf goes away from zero; Python's round() would give 2 for 2.5.
    assert DO.roundf(2.5) == 3 and DO.game_damage(10, 0.25) == 7


def _trace(*rows):
    return [f"@{t:.3f} [1] {text}" for t, text in rows]


def test_an_attack_window_carries_its_draw():
    lines = _trace(
        (10.0, "Battle: state change to bs_turn_presentation, (from bs_turn_do_skill). 1"),
        (10.1, "Chance to hit: 0.900000"),
        (10.2, "PID: 1 Name: Battle Random int range A,B: 1 , 3 Value: 1 Seed: 5"),
        (10.3, "Battle: state change to bs_after_turn, (from bs_turn_presentation). 1"),
    ) + ["[1] an unstamped line is ignored"]
    attacks = DO.parse_trace(lines)
    assert len(attacks) == 1 and attacks[0].draws == [(10.2, 1, 3, 1)]


def test_a_hit_is_graded_only_when_one_surviving_enemy_lost_hp():
    attacks = DO.parse_trace(_trace(
        (10.0, "Battle: state change to bs_turn_presentation, (x). 1"),
        (10.2, "PID: 1 Name: Battle Random int range A,B: 1 , 3 Value: 1 Seed: 5"),
        (10.3, "Battle: state change to bs_after_turn, (x). 1"),
    ))
    before = DO.Snapshot(9.0, {7: (12.0, 0.10, True), 8: (9.0, 0.0, True)})
    after_zero = DO.Snapshot(11.0, {7: (12.0, 0.10, True), 8: (9.0, 0.0, True)})
    assert DO.score(attacks, [before, after_zero]) == []      # nothing lost: unpairable
    after = DO.Snapshot(11.0, {7: (11.0, 0.10, True), 8: (9.0, 0.0, True)})
    (row,) = DO.score(attacks, [before, after])
    assert (row["observed"], row["game"], row["engine"]) == (1, 0, 1)
    assert "engine right 1" in DO.summarise([row])


def test_a_monster_s_turn_is_not_a_hero_hit():
    """2026-10-04: the draw of a monster's attack paired with that monster's
    own DoT tick read as five "unexplained" hits. Who is acting is in the
    trace; a name the save lists among the enemies is a monster's turn."""
    attacks = DO.parse_trace(_trace(
        (9.5, "check pCurrentBattleActor #2: Pelagic Grouper 1"),
        (10.0, "Battle: state change to bs_turn_presentation, (x). 1"),
        (10.2, "PID: 1 Name: Battle Random int range A,B: 5 , 9 Value: 7 Seed: 5"),
        (10.3, "Battle: state change to bs_after_turn, (x). 1"),
    ))
    assert attacks[0].actor == "Pelagic Grouper"
    before = DO.Snapshot(9.0, {7: (14.0, 0.0, True)}, {1: 20.0}, {"Pelagic Grouper"})
    after = DO.Snapshot(11.0, {7: (11.0, 0.0, True)}, {1: 20.0}, {"Pelagic Grouper"})
    assert DO.score(attacks, [before, after]) == []
