"""`tools/endless.py` — battles held before the party loses someone.

The arithmetic is pinned rather than the finding, the way
`tests/tools/test_stress_ratchet.py` is: the ways to get a survival statistic wrong
are quiet ones, and every test here corresponds to a specific wrong answer.
"""
import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.endless import (DEFAULT_CAP, Run, kaplan_meier,  # noqa: E402
                           median_survival)


def _run(died_after=None, reached=0, why="cap", retreats=0, abandoned=False):
    return Run(died_after=died_after, reached=reached or (died_after or 0),
               why=why, retreats=retreats, abandoned=abandoned)


# --- censoring -------------------------------------------------------------

def test_a_run_with_no_death_is_censored_not_a_zero():
    """It says the party lasted AT LEAST that long. Counting it as a death at
    the cap biases the answer down; dropping it biases it up."""
    assert _run(died_after=None, reached=30).censored
    assert not _run(died_after=12).censored


def test_a_censored_run_still_counts_as_AT_RISK_while_it_lasted():
    """The point of Kaplan-Meier. A run censored at 30 was alive and at risk
    through combat 10, so it belongs in the denominator there -- leaving it
    out would make an early death look far more common than it is."""
    runs = [_run(died_after=10)] + [_run(reached=30) for _ in range(9)]
    (k, s), = kaplan_meier(runs)
    assert k == 10
    assert abs(s - 0.9) < 1e-9, "the nine survivors were not at risk at 10"


def test_a_run_censored_BEFORE_an_event_leaves_the_denominator():
    """A run that truncated at 5 cannot inform whether parties die at 20."""
    runs = [_run(died_after=20), _run(reached=5), _run(reached=30)]
    (_k, s), = kaplan_meier(runs)
    assert abs(s - 0.5) < 1e-9, "the run censored at 5 was still counted at 20"


def test_no_deaths_at_all_is_an_empty_curve_not_a_flat_one():
    assert kaplan_meier([_run(reached=30) for _ in range(5)]) == []
    assert median_survival([]) is None


# --- the curve and the median ---------------------------------------------

def test_the_curve_only_ever_falls():
    runs = [_run(died_after=k) for k in (2, 5, 5, 9, 14)] + [_run(reached=30)]
    surv = [s for _k, s in kaplan_meier(runs)]
    assert surv == sorted(surv, reverse=True)
    assert all(0.0 <= s <= 1.0 for s in surv)


def test_two_deaths_at_one_k_are_one_drop_not_two():
    """Ties are a single event time. Treating them as two sequential drops
    shrinks the denominator between them and overstates the fall."""
    runs = [_run(died_after=4), _run(died_after=4), _run(reached=30),
            _run(reached=30)]
    curve = kaplan_meier(runs)
    assert len(curve) == 1
    assert abs(curve[0][1] - 0.5) < 1e-9


def test_the_median_is_the_first_k_at_or_below_half():
    runs = [_run(died_after=k) for k in (1, 2, 3, 4)]
    assert median_survival(kaplan_meier(runs)) == 2


def test_a_median_that_is_never_reached_says_so():
    """With most runs censored the curve never crosses 0.5, and `None` is the
    honest answer -- the alternative is quoting the cap as if it were a
    measurement."""
    runs = [_run(died_after=9)] + [_run(reached=DEFAULT_CAP) for _ in range(9)]
    assert median_survival(kaplan_meier(runs)) is None


# --- the exploit that has to stay closed ----------------------------------

def test_abandon_is_disabled_where_the_runs_are_played():
    """`ExpeditionSequenceEnv`'s own notes record a probe that abandoned 62% of
    runs "at a mean 61% party HP and 0.05 deaths". A metric counting battles
    survived without a death is trivially maximised by quitting at combat one,
    so `allow_abandon=False` is load-bearing, not a default someone chose."""
    src = (Path(__file__).resolve().parents[2] / "tools" / "endless.py")
    tree = ast.parse(src.read_text(encoding="utf-8"))
    kwargs = [k for node in ast.walk(tree)
              if isinstance(node, ast.Call) for k in node.keywords
              if k.arg == "allow_abandon"]
    assert kwargs, "the env is constructed without pinning allow_abandon"
    assert all(isinstance(k.value, ast.Constant) and k.value.value is False
               for k in kwargs)


def test_an_abandoned_run_is_reported_rather_than_silently_counted(capsys):
    """If a future change re-enables abandon, the report has to say so instead
    of quietly returning a very good number."""
    from tools.endless import report
    report("x", [_run(reached=30, abandoned=True)], cap=30)
    assert "ABANDONED" in capsys.readouterr().out


def test_the_report_never_claims_beyond_the_cap(capsys):
    """Past the cap the curve is flat only because nothing COULD have happened
    there, and printing it reads as evidence that it did not."""
    from tools.endless import report
    report("x", [_run(died_after=3), _run(reached=8)], cap=8)
    out = capsys.readouterr().out
    assert "after  8 combats" in out
    assert "after 20 combats" not in out


# --- ruler only ------------------------------------------------------------