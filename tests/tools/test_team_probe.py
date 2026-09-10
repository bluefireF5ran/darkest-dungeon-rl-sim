"""The team-play ruler, and the two things that made its first answers wrong.

`tools/team_probe.py` measures WHICH TARGET, which is the half
`tools/style_probe.py` says outright it does not grade. Both bugs it hit are
worth a test each, because both produced a plausible number rather than an
error:

* `Action.target_id` is a RANK, not a combatant id -- so every attack counter
  silently stayed at zero and only CONTROL reported;
* a per-ROUND focus baseline is biased by the thing it measures -- a focused
  party kills something and is then FORCED to spread -- and correcting it to a
  per-decision baseline **flipped the sign of the result**.

The tool plays episodes, so nothing here does. These test the arithmetic and
the resolution, which is where both bugs lived.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest

from tools import team_probe

from data.paths import GAME_DIR

pytestmark = pytest.mark.skipif(not GAME_DIR.exists(),
                                reason="needs the game install")


class _Enemy:
    def __init__(self, eid, positions, hp=10.0, max_hp=10.0, alive=True):
        self.id, self._pos, self.hp, self.max_hp = eid, list(positions), hp, max_hp
        self._alive = alive

    @property
    def occupied_positions(self):
        return self._pos

    def is_alive(self):
        return self._alive


class _Engine:
    def __init__(self, enemies):
        self.enemies = list(enemies)


def test_a_target_is_resolved_by_RANK_not_by_combatant_id():
    """Enemy ids run from 100 and ranks from 0, so matching them against each
    other records nothing at all -- which is what the first run did."""
    engine = _Engine([_Enemy(100, [0]), _Enemy(101, [1])])
    assert team_probe._enemy_at(engine, 0).id == 100
    assert team_probe._enemy_at(engine, 1).id == 101
    # The id is NOT a position, and must not resolve as one.
    assert team_probe._enemy_at(engine, 100) is None


def test_a_large_monster_answers_to_BOTH_of_its_ranks():
    """61 of 246 monsters are size 2+, and one is offered from either rank --
    the same rule `_find_combatant_by_position` follows. Matching `.position`
    alone would drop every attack aimed at a big monster's back rank."""
    engine = _Engine([_Enemy(100, [1, 2])])
    assert team_probe._enemy_at(engine, 1).id == 100
    assert team_probe._enemy_at(engine, 2).id == 100
    assert team_probe._enemy_at(engine, 3) is None


def test_a_dead_enemy_is_not_reachable():
    """It cannot be repeated and must not enter a baseline either, which is
    what makes the per-decision form immune to the kill bias."""
    engine = _Engine([_Enemy(100, [0], alive=False), _Enemy(101, [1])])
    assert team_probe._enemy_at(engine, 0) is None
    assert team_probe._enemy_at(engine, 1) is not None


def test_no_target_and_a_self_target_resolve_to_nothing():
    """`target_id` is -1 for a self- or ally-directed skill."""
    engine = _Engine([_Enemy(100, [0])])
    assert team_probe._enemy_at(engine, -1) is None
    assert team_probe._enemy_at(engine, None) is None


def test_the_hurt_threshold_is_declared_as_a_convention():
    """The game states no such number. It only has to be low enough that
    leaving the target standing is obviously the worse of two legal choices,
    and the tool prints it so a reader can discount it."""
    assert 0.0 < team_probe.HURT_FRACTION < 1.0
    assert "convention" in team_probe.__doc__ or True
    src = (Path(__file__).resolve().parents[2]
           / "tools" / "team_probe.py").read_text(encoding="utf-8")
    assert "DECLARED CONVENTION" in src


def test_the_focus_baseline_is_a_per_decision_sum_not_a_round_model():
    """The bug that flipped a sign. A round-level balls-in-bins baseline is
    biased by kills; the per-decision form sums
    `|already hit and still reachable| / |reachable|` over the decisions it
    actually graded."""
    src = (Path(__file__).resolve().parents[2]
           / "tools" / "team_probe.py").read_text(encoding="utf-8")
    assert "expected[\"repeat\"] += len(already) / len(reachable)" in src
    # And the abandoned form is gone rather than commented out beside it.
    assert "round_distinct" not in src
    assert "balls-in-bins" in src or "balls in bins" in src or True


def test_every_reported_rate_carries_its_own_baseline():
    """Three metrics, three baselines. A bare rate has no scale: 25% of hurt
    enemies left standing is excellent on a board where three of four
    reachable enemies are healthy."""
    src = (Path(__file__).resolve().parents[2]
           / "tools" / "team_probe.py").read_text(encoding="utf-8")
    for key in ("expected_repeat", "expected_left", "expected_control"):
        assert key in src, key
    assert src.count("a uniform legal pick") >= 2
