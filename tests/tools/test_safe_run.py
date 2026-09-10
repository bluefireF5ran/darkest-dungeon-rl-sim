"""The guards that stop a long job taking the machine down.

`tools/safe_run.py` exists because on 2026-09-04 three concurrent test suites,
each running a leftover `and` -> `or` mutation that made combats never end,
exhausted 32 GB. Windows paged rather than killing anything, so nothing died
and nothing was printed -- Fran lost four hours to a machine that never
crashed.

A safety net nobody tests is a safety net that rots, and this one is only ever
exercised on the day it is needed. What is pinned here is the part that has to
keep working: **the detector for the shape of edit that caused it**, and the
bound on what counts as a competitor.

The kill paths (RSS cap, free-memory floor, wall clock) are deliberately NOT
tested here -- they need a real child process burning real memory, which is
exactly the thing this suite must not do. They were verified by hand against a
deliberate allocator and a deliberate hang; see `CLAUDE.md`.
"""

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import tools.safe_run as safe_run                          # noqa: E402
from tools.safe_run import (COMPETITOR_RSS_MB, CORE_DIRS,  # noqa: E402
                            competing_python, is_suspicious)

REPO = Path(__file__).resolve().parents[2]


def test_it_flags_the_exact_edit_that_cost_four_hours():
    """`is_alive()` losing its `and`. This is the regression test for the
    incident, expressed as the diff line the tool actually sees."""
    line = "+        return not self.is_dead or not self.is_corpse"
    assert is_suspicious(line), "the mutation detector would miss it again"


def test_it_flags_a_flipped_comparison_too():
    """The other half of a mutation harness's operator set. A boundary that
    silently moves is the same class of bug as a boolean that flips."""
    for line in ("+    if hp >= threshold:", "+    if hp < cap:",
                 "+    return a != b", "+        while turns <= limit:"):
        assert is_suspicious(line), line


def test_it_flags_a_word_operator_only_next_to_code():
    """`and`/`or` need an expression around them.

    Without this the tool reported its OWN comments -- a line reading
    "...and was wrong" came back as a possible mutation on the first real
    run. A guard that cries wolf is one people learn to scroll past, which is
    the same as not having a guard.
    """
    assert is_suspicious("+    result = a and b")
    assert is_suspicious("+        if x or y:")
    assert not is_suspicious(
        "+    the entries are multi-line and the parser could not match them")


def test_it_ignores_a_comment_a_removed_line_and_a_file_header():
    """Only ADDED lines of CODE are candidates. A `+++` header starts with the
    same character as a hunk line, and a comment is prose however it reads."""
    assert not is_suspicious("+        # a comment mentioning and or whatever")
    assert not is_suspicious("-        return not self.is_dead and x")
    assert not is_suspicious("+++ b/engine/combatant.py")


def test_the_watched_directories_are_the_ones_that_decide_behaviour():
    """A mutation in `tools/` wastes a diagnostic; one in `engine/` corrupts
    every measurement taken after it. All four must exist."""
    assert set(CORE_DIRS) == {"engine", "env", "data", "bridge"}
    for name in CORE_DIRS:
        assert (REPO / name).is_dir(), name


def test_a_competitor_threshold_that_would_catch_a_real_suite():
    """The scan must be able to see a pytest worker.

    `pytest.ini`'s own note puts a worker at 63 MB with a DataStore and 534 MB
    once torch is loaded, and the runs that took the machine down were several
    GB each. A threshold above a worker's floor would let exactly the thing
    this guards against slip past.
    """
    assert 100 <= COMPETITOR_RSS_MB <= 1000, COMPETITOR_RSS_MB


def test_this_very_process_is_not_reported_as_a_competitor():
    """A guard that counts itself refuses every run, including the first."""
    assert all(pid != os.getpid()
               for pid, _rss in competing_python(exclude_pid=-1))


def test_a_lock_naming_a_dead_pid_is_reclaimable(tmp_path, monkeypatch):
    """Otherwise a killed run locks the repo until someone deletes a file by
    hand -- which, on the day this matters, is the moment people start
    deleting things they do not understand.

    **The real `.safe_run.lock` is redirected to `tmp_path` for this.** A test
    that edited the live lock would be racing whatever job is holding it, and
    restoring the file after that job had cleaned it up would leave a stale
    lock behind. This suite must never be the reason a training run cannot
    start.
    """
    lock = tmp_path / ".safe_run.lock"
    monkeypatch.setattr(safe_run, "LOCK", lock)

    # A pid that cannot still be running: the child has exited by the time its
    # own id is read back here.
    dead = subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"],
                          capture_output=True, text=True, timeout=60)
    lock.write_text(dead.stdout.strip() + " gone", encoding="utf-8")
    assert safe_run.stale_lock(), "a lock held by a dead pid must be reclaimable"

    lock.write_text("not-a-pid", encoding="utf-8")
    assert safe_run.stale_lock(), "an unreadable lock must not wedge the repo"

    lock.write_text(f"{os.getpid()} this very process", encoding="utf-8")
    assert not safe_run.stale_lock(), (
        "a lock held by a LIVE pid must be honoured, or the guard is decorative")


if __name__ == "__main__":
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            _fn()
            print(f"ok {_name}")
