"""`tools/forecast_vs_game.py` -- the forecast against real raids, per fight."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.forecast_vs_game import raids  # noqa: E402

LOG = """[01:00:00] === rooA ===
  4 combats left in cove (tier 1): P(finish intact) 50% +-14, expected deaths 2.0
[01:01:00] [R1] Leper (rank 1)
[01:01:10] [R2] Leper (rank 1)
[01:02:00]   DEATH  vestal died
[01:03:00] [R1] Leper (rank 1)
[01:04:00] -- raid over --
"""


def test_a_raid_is_scored_per_fight_REACHED():
    """Two fights reached of four forecast: the forecast's 2.0 deaths over
    four fights is 1.0 over the two that happened."""
    (zone, n, rate, fights, deaths), = list(raids(LOG))
    assert (zone, n, fights, deaths) == ("cove", 4, 2, 1)
    assert rate * fights == 1.0


def test_a_raid_without_a_forecast_is_skipped():
    assert list(raids("[R1] x\n-- raid over --\n")) == []
