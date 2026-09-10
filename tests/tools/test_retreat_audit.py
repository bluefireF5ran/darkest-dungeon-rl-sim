"""The stay/leave ruler (`tools/retreat_audit.py`), and the two parsing traps
that each cost a wrong answer on the first pass over the real log.

The tool exists because `bat/play.log` recorded the bridge advising "worth
pushing on" at `P(finish intact) 0%` with 3.8 expected deaths — the concrete
answer to Fran's *"several deaths which I didn't really understand"*. It reads
a log rather than a simulator, so its correctness IS its parsing, and both
regexes here were wrong in a way that quietly discarded exactly the rows worth
reading.

`bat/play.log` is a run artifact and is not committed, so every test drives an
inline fixture instead. The fixture carries the shapes that broke the first
version, not a tidied sample.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.retreat_audit import Decision, explain, parse, verdict_table

# Real shapes, copied from `bat/play.log`:
#  - a `+-0` forecast (the confident end AND the hopeless end both use it)
#  - a two-digit `+-14`
#  - a NEGATIVE staying cost with a thousands separator
#  - a block resumed with `after 1 enemy turn ->` before the hero's name
#  - a RETREAT verdict, so the table has both columns to count
FIXTURE = """\
[20:03:01] [R1] Highwayman (rank 2, 23/23 hp, 0 stress)
  2 combats left in weald (tier 1): P(finish intact) 67% ±14, expected deaths 0.4
  leaving costs ~5,889 gold, staying ~-2,552 — worth pushing on
  [act] sent - confirmed by the save after 3.8s

[13:23:03] [R1] after 1 enemy turn -> Shieldbreaker (rank 3, 0/22 hp, 180 stress)
  10 combats left in crypts (tier 1): P(finish intact) 0% ±0, expected deaths 3.8
  leaving costs ~6,278 gold, staying ~-375 — worth pushing on

[13:30:00] [R2] Crusader (rank 1, 30/33 hp, 12 stress)
  1 combat left in cove (tier 3): P(finish intact) 100% ±0, expected deaths 0.0
  leaving costs ~1,776 gold, staying ~-3,000 — worth pushing on

[13:40:00] [R3] Vestal (rank 4, 8/24 hp, 95 stress)
  4 combats left in warrens (tier 5): P(finish intact) 8% ±3, expected deaths 2.1
  leaving costs ~4,000 gold, staying ~9,000 — RETREAT
""".splitlines()


def _parsed():
    return parse(FIXTURE)


# --- the two parsing traps ---

def test_a_single_digit_error_bar_is_not_dropped():
    """THE TRAP THAT HID THE WHOLE FINDING. `+-0` is what a fully confident and
    a fully hopeless forecast both print, so a pattern demanding two digits
    after the separator discards both ends of the distribution — 110 confident
    and 54 hopeless decisions out of 514, i.e. every row the audit is about.
    The first ad-hoc pass over this log did exactly that and reported a
    forecast histogram that stopped at 83%."""
    got = {d.intact: d.se for d in _parsed()}
    assert got == {67: 14, 0: 0, 100: 0, 8: 3}


def test_the_hero_name_survives_a_resumed_block():
    """`after 1 enemy turn -> Shieldbreaker` must yield "Shieldbreaker".

    Not cosmetic: that prefix appears precisely when the enemies got to act
    between a hero's turns, which is when a hero is in trouble. A regex that
    misses the shape under-counted Death's Door turns 7 against 14 and reported
    the corpus's worst stress as 152 when it was 180."""
    shieldbreaker = [d for d in _parsed() if d.intact == 0][0]
    assert shieldbreaker.hero == "Shieldbreaker"
    assert shieldbreaker.hp == 0 and shieldbreaker.stress == 180


def test_a_negative_staying_cost_parses():
    """`staying ~-375` is the shape that makes the finding legible — a bill
    that says continuing PAYS while forecasting 3.8 deaths."""
    doomed = [d for d in _parsed() if d.intact == 0][0]
    assert doomed.staying == -375.0
    assert doomed.leaving == 6278.0
    assert doomed.deaths == 3.8


def test_a_forecast_with_no_verdict_under_it_is_skipped():
    """A block truncated mid-write (the log is read while the bridge runs)
    must be dropped, not recorded with half its fields."""
    truncated = FIXTURE[:2]          # hero header + forecast, no verdict line
    assert parse(truncated) == []


# --- what it reports ---

def test_the_verdict_table_separates_push_from_retreat():
    table = verdict_table(_parsed())
    assert "0%" in table and "100%" in table
    rows = {line.split("%")[0].strip(): line for line in table.splitlines()
            if "%" in line}
    assert rows["0"].split()[-2:] == ["1", "0"]      # pushed on at 0% intact
    assert rows["8"].split()[-2:] == ["0", "1"]      # retreated at 8%


def test_retreated_reads_the_verdict_text():
    by_pct = {d.intact: d for d in _parsed()}
    assert by_pct[8].retreated
    assert not by_pct[0].retreated


def test_explain_fires_the_floor_on_the_scene_that_prompted_all_this():
    """The 13:23:03 decision: 3.8 expected deaths, gold saying push on. The
    re-pricing has to come back RETREAT, and has to say the FLOOR did it rather
    than implying the gold changed its mind."""
    doomed = [d for d in _parsed() if d.intact == 0][0]
    text = explain(doomed)
    assert "VERDICT NOW: RETREAT" in text
    assert "FIRES" in text
    assert "90,250" in text        # 3.8 deaths at a real hero's ~23,750


def test_explain_leaves_a_safe_decision_to_the_gold():
    safe = [d for d in _parsed() if d.intact == 100][0]
    text = explain(safe)
    assert "does not fire" in text
    assert "VERDICT NOW: RETREAT" not in text


# --- the standing contract ---


if __name__ == "__main__":
    test_a_single_digit_error_bar_is_not_dropped()
    test_the_hero_name_survives_a_resumed_block()
    test_a_negative_staying_cost_parses()
    test_a_forecast_with_no_verdict_under_it_is_skipped()
    test_the_verdict_table_separates_push_from_retreat()
    test_retreated_reads_the_verdict_text()
    test_explain_fires_the_floor_on_the_scene_that_prompted_all_this()
    test_explain_leaves_a_safe_decision_to_the_gold()
    print("All retreat-audit tests passed")
