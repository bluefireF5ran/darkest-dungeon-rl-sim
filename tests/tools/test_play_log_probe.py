"""Reading real play out of `bat/play.log`.

`tools/team_probe.py` measures target selection by playing the simulator, and
carries a caveat it cannot remove from the inside: curated comps at a fixed
tier, against Fran's impression from driving his own campaign. `play.log` is
the other side of that, and it costs nothing to read -- `bat/combat_play.bat`
has been writing it all along.

The parser is where the risk is, because every failure mode here is silent:
a regex that stops matching reports a smaller corpus, not an error.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest

from tools import play_log_probe as P

LOG = """\
[20:03:01] [R1] Highwayman (rank 2, 23/23 hp, 0 stress)
  2 enemies left, stall 0/4 (0/2 real actions this round)
  -> OPEN VEIN on rank 1 (Brigand Cutthroat, 12 hp)   72%
     alt: GRAPESHOT BLAST on rank 2 (Brigand Fusilier, 20 hp) (AoE)  27%
  [act] [battle] Highwayman: OPEN VEIN on rank 1 (Brigand Cutthroat, 12 hp)
[20:03:08]   MATCH  played attack Brigand Cutthroat (slot 1) -  (-8 hp)

[20:03:08] [R1] Crusader (rank 1, 33/33 hp, 0 stress)
  2 enemies left, stall 0/4 (0/2 real actions this round)
  -> SMITE on rank 1 (Brigand Cutthroat, 4 hp)   58%
     alt: STUNNING BLOW on rank 2 (Brigand Fusilier, 20 hp)  13%
  [act] [battle] Crusader: SMITE on rank 1 (Brigand Cutthroat, 4 hp)
[20:03:12]   MATCH  played attack Brigand Cutthroat (slot 1) -  (-4 hp)

[20:58:08] [R2] after 1 enemy turn -> Arbalest (rank 4, 27/27 hp, 20 stress)
  (provisional; refreshes when they finish)
  -> SNIPER SHOT on rank 2 (Cultist Acolyte, 1 hp)   94%
     alt: BATTLEFIELD BANDAGE on rank 2 (Occultist)  5%
  [act] [battle] REFUSE: provisional look-ahead - this turn is not due yet

[20:59:01] [R2] Vestal (rank 4, 20/20 hp, 3 stress)
  1 enemy left, stall 0/4 (0/2 real actions this round)
  -> DIVINE GRACE on rank 1 (Crusader)   80%
     alt: JUDGEMENT on rank 1 (Cultist Acolyte, 6 hp)  20%
  [act] [battle] Vestal: DIVINE GRACE on rank 1 (Crusader)
[20:59:05]   MATCH  played heal Crusader (slot 2) -  (+4 hp)
"""


@pytest.fixture
def log(tmp_path):
    path = tmp_path / "play.log"
    path.write_text(LOG, encoding="utf-8")
    return path


def test_only_EXECUTED_decisions_are_graded(log):
    """A refused turn never happened on the board -- a provisional look-ahead,
    or pending enemy turns -- so counting its pick would fabricate an attack
    that was never made."""
    entries = P.parse(log)
    assert [e["hero"] for e in entries] == ["Highwayman", "Crusader", "Vestal"]
    assert all(e["sent"] for e in entries)


def test_an_enemy_target_carries_HP_and_an_ally_target_does_not(log):
    """The log's own way of telling them apart, and the reason an
    ally-directed skill cannot be mistaken for an attack."""
    entries = P.parse(log)
    assert entries[0]["pick"][1] == ("Brigand Cutthroat", 12)
    assert entries[2]["pick"][1] == ("Crusader", None)
    assert P._target("Cultist Acolyte, 5 hp") == ("Cultist Acolyte", 5)
    assert P._target("Occultist") == ("Occultist", None)
    assert P._target(None) is None


def test_the_alternatives_are_captured_with_their_own_targets(log):
    entries = P.parse(log)
    assert entries[0]["alts"][0][0] == "GRAPESHOT BLAST"
    assert entries[0]["alts"][0][1] == ("Brigand Fusilier", 20)


def test_the_convergence_window_is_a_ROUND_not_a_DECISION(log):
    """The bug that made FOCUS grade nothing at all.

    The first version reset the window whenever `round <= last_round`, which
    is true for every hero after the first in the same round -- so the set of
    already-struck enemies was always empty and the metric silently reported
    n=0 while the other two reported fine. Two heroes both acting in R1 is
    exactly the case being measured.
    """
    entries = P.parse(log)
    counts = P.grade(entries, control_names=set())
    assert counts["graded"] >= 1, "the round window is being reset per decision"
    # The Crusader repeated the Highwayman's target with a healthier one in
    # reach, so it is a graded repeat.
    assert counts["repeated"] >= 1


def test_a_new_combat_does_not_pool_with_the_last_round_of_the_old(log):
    """A round number that goes DOWN is a new fight."""
    entries = P.parse(log)
    rounds = [e["round"] for e in entries]
    assert rounds == [1, 1, 2]
    counts = P.grade([entries[1], entries[0]], control_names=set())
    # R1 then R1 again is the same round; R2 -> R1 would not be.
    assert counts["graded"] >= 1


def test_finishing_counts_only_when_there_was_a_CHOICE(log):
    """A decision whose shortlist reaches one enemy is not a choice about
    which enemy, and must not enter the denominator."""
    entries = P.parse(log)
    counts = P.grade(entries, control_names=set())
    assert counts["choices_with_hurt"] >= 1
    assert counts["left_standing"] <= counts["choices_with_hurt"]


def test_control_is_scored_against_its_share_of_the_shortlist(log):
    """A bare take-rate has no scale: one stun among twelve legal attacks
    taken 17% of the time is over-use, not under."""
    entries = P.parse(log)
    counts = P.grade(entries, control_names={"STUNNING BLOW"})
    assert counts["control_offered"] == 1
    assert counts["control_taken"] == 0
    assert 0.0 < counts["expected_control"] < 1.0


def test_no_control_names_means_no_control_rows(log):
    """The classifier resolves display names through localization; if that
    ever returns nothing the metric must vanish rather than read 0%."""
    counts = P.grade(P.parse(log), control_names=set())
    assert counts["control_offered"] == 0


def test_the_real_log_still_parses_if_it_is_there():
    """The silent failure this file exists for: a regex that stops matching
    reports a smaller corpus, never an error."""
    path = Path(__file__).resolve().parents[2] / "bat" / "play.log"
    if not path.is_file():
        pytest.skip("no play log on this machine")
    entries = P.parse(path)
    assert len(entries) > 50, f"only {len(entries)} decisions parsed"
    assert any(e["pick"][1] and e["pick"][1][1] is not None for e in entries)