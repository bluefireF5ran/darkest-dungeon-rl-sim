"""The exchange-rate audit (`tools/exchange_rates.py`).

The tool answers "what should the weights be" by refusing to guess: it puts the
training reward's implied rates and the game's own prices in one table. That
makes it a piece of EVIDENCE, and a stale piece of evidence is worse than none
— it would be quoted in an argument about a retrain.

Two things in it can go stale silently, and both are pinned here:

1. **`REWARD_WIN_SEQUENCE` is hand-copied.** `ExpeditionSequenceEnv` sets
   `self._reward_win` in `__init__` rather than at module scope, so it cannot
   be imported. If the env's value moves and the copy does not, the table keeps
   printing the old ratio with no error anywhere.
2. **The terminal-stress derivative uses /200, and there is a /100 in the same
   file.** `END_STRESS_SCALE` is 100 (the Resolve Check, used by
   `combat_end_condition`); `_mean_roster_stress_frac` divides by 200 (the
   ceiling). Using the wrong one overstates the stress price by exactly 2x and
   halves every ratio in the headline table.
"""

import re
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest

from tools.exchange_rates import (PARTY, REWARD_WIN_SEQUENCE, ROSTER_STRESS_CEILING,
                                  game_side, reward_side)

REPO = Path(__file__).resolve().parents[2]
from data.paths import GAME_DIR


def test_the_hand_copied_win_reward_still_matches_the_env():
    """`self._reward_win = 8.0` is assigned in `__init__`, so the only way to
    check the copy is to read the source. A drift here is invisible: the table
    still prints, with a wrong number in it."""
    src = (REPO / "env" / "expedition_sequence_env.py").read_text(
        encoding="utf-8", errors="replace")
    found = re.search(r"self\._reward_win\s*=\s*([\d.]+)", src)
    assert found, "the env stopped assigning _reward_win the way the tool assumes"
    assert float(found.group(1)) == REWARD_WIN_SEQUENCE


def test_the_terminal_stress_scale_is_the_CEILING_not_the_resolve_check():
    """`_mean_roster_stress_frac` is what the terminal penalty multiplies, and
    it divides by 200. `END_STRESS_SCALE` (100) is a different quantity in the
    same file. Read the source rather than trusting the constant here, because
    the point is that the two agree."""
    src = (REPO / "env" / "expedition_sequence_env.py").read_text(
        encoding="utf-8", errors="replace")
    body = src.split("def _mean_roster_stress_frac")[1].split("\n    def ")[0]
    assert "min(h.stress, 200) / 200.0" in body, (
        "the roster stress fraction changed scale -- the exchange-rate table's "
        "stress price is derived from it and is now wrong")
    assert ROSTER_STRESS_CEILING == 200.0


def test_a_stress_point_costs_the_tick_plus_its_share_of_the_terminal():
    """The derivative, spelled out: the terminal term is
    `W * mean over the roster of stress/200`, so one point on one hero moves it
    by `W / (party * 200)`."""
    rew = reward_side(party=4)
    assert rew["stress point (healed later)"] == pytest.approx(0.01)
    assert rew["stress point (carried home)"] == pytest.approx(0.01 + 4.0 / (4 * 200))


def test_the_reward_prices_a_hero_ABOVE_the_games_whole_range():
    """The finding itself, as an assertion. If a future retrain moves
    `REWARD_HERO_DEATH` into the game's range this test fails — which is the
    point: that would be a deliberate change and should not pass silently."""
    rew = reward_side(PARTY)
    game = game_side(GAME_DIR, roster_value=23750.0)
    reward_ratio = rew["death"] / rew["stress point (carried home)"]
    dearest = max(game["a rank-4 crusader"], game["a measured campaign hero"])
    game_ratio = dearest / game["stress point (Abbey, un-upgraded)"]
    assert reward_ratio > game_ratio, (
        f"reward prices a hero at {reward_ratio:,.0f} stress points, the game's "
        f"dearest reading is {game_ratio:,.0f} -- the audit's headline has changed")


def test_a_fresh_recruit_is_free_and_that_is_the_whole_spread():
    """PLAYBOOK 0.1: the stage coach gives them away. It is why the game's
    ratio spans three orders of magnitude and a single constant cannot."""
    game = game_side(GAME_DIR)
    assert game["a fresh recruit"] == 0.0
    assert game["a rank-4 crusader"] > 10_000


if __name__ == "__main__":
    test_the_hand_copied_win_reward_still_matches_the_env()
    test_the_terminal_stress_scale_is_the_CEILING_not_the_resolve_check()
    test_a_stress_point_costs_the_tick_plus_its_share_of_the_terminal()
    test_the_reward_prices_a_hero_ABOVE_the_games_whole_range()
    test_a_fresh_recruit_is_free_and_that_is_the_whole_spread()
    print("All exchange-rate tests passed")
