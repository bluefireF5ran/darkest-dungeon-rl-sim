"""The agreement scorer's statistics, and its refusal to compare misaligned arms.

The tool's whole value is that it can be trusted to say "no difference" when
there is none. `eval_expedition.py --full-expedition` could not -- it scored the
same checkpoint at 41.7% and 33.3% back to back -- and 8.4pp of harness noise
was read as signal for months. So the pins here are mostly about the ruler and
not about the numbers it happens to produce.

The replay itself is covered end-to-end by `--self-check`, which is a runtime
gate rather than a test: it needs a real checkpoint and the capture corpus.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.agreement import (                                     # noqa: E402
    Arm, bootstrap_ci, mcnemar_exact, rate, report_paired,
)


def arm(label, rows):
    """rows: (layer, key, verdict[, played, advised])"""
    a = Arm(label=label)
    for row in rows:
        layer, key, verdict = row[:3]
        a.keys.append(key)
        a.verdicts.append(verdict)
        a.layers.append(layer)
        a.infos.append({"played": row[3], "advised": row[4]}
                       if len(row) > 4 else {})
    return a


# --- McNemar ---

def test_no_discordant_pairs_is_no_evidence():
    assert mcnemar_exact(0, 0) == 1.0


def test_a_symmetric_split_is_no_evidence():
    assert mcnemar_exact(10, 10) == 1.0


def test_a_lopsided_split_is_significant():
    assert mcnemar_exact(20, 2) < 0.001


def test_it_is_two_sided():
    assert mcnemar_exact(2, 20) == mcnemar_exact(20, 2)


def test_the_measured_split_reproduces():
    """27/15 over the real corpus came out at p = 0.088 -- a large point
    estimate that does NOT clear 0.05, which is the whole reason the tool
    prints b/c next to the delta."""
    assert 0.08 < mcnemar_exact(27, 15) < 0.09


# --- the bootstrap ---

def test_an_all_zero_difference_has_a_zero_interval():
    lo, hi = bootstrap_ci([0] * 50)
    assert lo == 0.0 and hi == 0.0


def test_the_interval_brackets_the_mean():
    diffs = [1] * 30 + [0] * 60 + [-1] * 10
    lo, hi = bootstrap_ci(diffs)
    assert lo < sum(diffs) / len(diffs) < hi


def test_an_empty_sample_does_not_raise():
    assert bootstrap_ci([]) == (0.0, 0.0)


def test_the_bootstrap_is_seeded():
    d = [1, 0, -1, 1, 0, 0, 1]
    assert bootstrap_ci(d) == bootstrap_ci(d)


# --- rates ---

def test_the_rate_ignores_everything_but_match():
    pairs = [((), "MATCH"), ((), "DIFFER"), ((), "MATCH"), ((), "DIFFER")]
    assert rate(pairs) == 0.5
    assert rate([]) == 0.0


def test_unknowns_are_dropped_not_counted_as_disagreement():
    """A missed attack leaves no trace in the save. Counting `?` as a DIFFER
    would charge the policy for the instrument's blind spot."""
    a = arm("x", [("combat", (1,), "MATCH"), ("combat", (2,), "?"),
                  ("combat", (3,), "DIFFER")])
    assert len(a.scored("combat")) == 2
    assert a.unknown("combat") == 1
    assert rate(a.scored("combat")) == 0.5


def test_layers_are_never_pooled():
    a = arm("x", [("combat", (1,), "DIFFER"), ("curio", (2,), "MATCH")])
    assert rate(a.scored("combat")) == 0.0
    assert rate(a.scored("curio")) == 1.0


# --- refusing to compare misaligned arms ---

def test_misaligned_arms_are_refused(capsys):
    """If the two arms did not face the same decisions, no delta they produce
    means anything -- so none is printed."""
    a = arm("A", [("combat", (1,), "MATCH"), ("combat", (2,), "MATCH")])
    b = arm("B", [("combat", (1,), "MATCH"), ("combat", (99,), "DIFFER")])
    assert report_paired(a, b) == 1
    out = capsys.readouterr().out
    assert "REFUSING" in out
    assert "first divergence at 1" in out
    assert "%" not in out.split("REFUSING")[1], "printed a rate anyway"


def test_differing_lengths_are_refused(capsys):
    a = arm("A", [("combat", (1,), "MATCH")])
    b = arm("B", [("combat", (1,), "MATCH"), ("combat", (2,), "MATCH")])
    assert report_paired(a, b) == 1
    assert "REFUSING" in capsys.readouterr().out


def test_identical_arms_pass_the_self_check(capsys):
    rows = [("combat", (i,), "MATCH" if i % 3 else "DIFFER") for i in range(30)]
    assert report_paired(arm("A", rows), arm("A2", rows), self_check=True) == 0
    assert "PASSED" in capsys.readouterr().out


def test_the_self_check_fails_on_any_difference(capsys):
    rows_a = [("combat", (i,), "MATCH") for i in range(10)]
    rows_b = [("combat", (i,), "MATCH" if i else "DIFFER") for i in range(10)]
    assert report_paired(arm("A", rows_a), arm("B", rows_b),
                         self_check=True) == 1
    assert "SELF-CHECK FAILED" in capsys.readouterr().out


# --- the disagreement breakdown ---

def test_only_combat_differs_are_broken_down():
    a = arm("x", [
        ("combat", (1,), "DIFFER", "attack", "attack"),
        ("combat", (2,), "DIFFER", "attack", "heal"),
        ("combat", (3,), "MATCH", "attack", "attack"),
        ("curio", (4,), "DIFFER", "n/a", "n/a"),
    ])
    d = a.disagreements()
    assert d == {("attack", "attack"): 1, ("attack", "heal"): 1}


def test_a_same_kind_disagreement_is_distinguishable_from_a_different_play():
    """The distinction the breakdown exists for. Measured on the real corpus:
    ~70% of both checkpoints' DIFFERs are attack->attack, so the gap between
    them is aiming, not choosing."""
    a = arm("x", [("combat", (i,), "DIFFER", "attack", "attack")
                  for i in range(7)]
                 + [("combat", (99,), "DIFFER", "attack", "heal")])
    d = a.disagreements()
    same = sum(n for (p, adv), n in d.items() if p == adv)
    other = sum(n for (p, adv), n in d.items() if p != adv)
    assert (same, other) == (7, 1)


def test_a_missing_info_dict_does_not_crash_the_breakdown():
    a = arm("x", [("combat", (1,), "DIFFER")])
    assert a.disagreements() == {("?", "?"): 1}
