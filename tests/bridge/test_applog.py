"""`app.log`: the only acknowledgement the front end has.

Before a campaign exists there is no save file, so `_send_and_confirm`'s
"did the save change?" test degrades to *"sent (no save to confirm against)"*.
The game's own log fills part of that hole -- it reports which launch flags the
binary accepted, when a campaign loaded, and when a raid actually started.

The fixture below is a trimmed real session (2026-08-17), kept verbatim in
shape: the two-line `option:` pairs, the valueless flag, the `[INFO] [General]`
frame, and three campaign loads in one boot -- which is the detail that makes
`wait_for(..., since=)` necessary rather than nice.
"""

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from bridge import applog                                        # noqa: E402

REAL = applog.APP_LOG

SESSION = """\
[2026-08-17 17:56:13] [INFO] [General] Game started... Windows plinth_main
[2026-08-17 17:56:13] [INFO] [General] option: appname
[2026-08-17 17:56:13] [INFO] [General]  "Darkest Dungeon"
[2026-08-17 17:56:13] [INFO] [General]
[2026-08-17 17:56:13] [INFO] [General] option: skipvalidation
[2026-08-17 17:56:13] [INFO] [General]
[2026-08-17 17:56:13] [INFO] [General] option: vsync
[2026-08-17 17:56:13] [INFO] [General]  "1"
[2026-08-17 17:56:13] [INFO] [General] Setting random seed: 2134058804 (0x7f332734)
[2026-08-17 18:01:50] [INFO] [General] as_load_campaign - LoadProfile() 0.071200
[2026-08-17 18:02:27] [INFO] [General] Continue pressed in Loading Screen
[2026-08-17 18:02:27] [INFO] [General] as_raid_start_done_loading - RaidStartDoneLoading() 0.000035
[2026-08-17 18:02:53] [INFO] [General] as_load_campaign - LoadProfile() 0.045986
[2026-08-17 18:03:01] [INFO] [General] Continue pressed in Loading Screen
[2026-08-17 18:03:01] [INFO] [General] as_raid_start_done_loading - RaidStartDoneLoading() 0.000025
"""


@pytest.fixture
def log(tmp_path):
    p = tmp_path / "app.log"
    p.write_text(SESSION, encoding="utf-8")
    return p


def test_it_parses_the_frame_and_drops_everything_else(log):
    ents = applog.entries(log)
    assert len(ents) == 15
    assert all(e.level == "INFO" and e.channel == "General" for e in ents)
    assert ents[0].msg.startswith("Game started")


def test_a_torn_final_line_is_simply_not_an_entry(log, tmp_path):
    """The game appends while we read. A half-written last line fails the
    timestamp match and disappears -- no staging copy needed, unlike the
    binary saves `live_state._stage` guards."""
    p = tmp_path / "torn.log"
    p.write_text(SESSION + "[2026-08-17 18:03:0", encoding="utf-8")
    assert len(applog.entries(p)) == len(applog.entries(log))


def test_a_missing_log_is_empty_not_an_exception(tmp_path):
    assert applog.entries(tmp_path / "nope.log") == []
    assert applog.options(tmp_path / "nope.log") == {}
    assert applog.state(tmp_path / "nope.log") == ""


# --- launch flags: how a probe gets graded ---

def test_options_pair_a_name_with_the_value_on_the_next_line(log):
    opts = applog.options(log)
    assert opts["vsync"] == "1"
    assert opts["appname"] == "Darkest Dungeon"


def test_a_valueless_flag_is_present_with_an_empty_value(log):
    """`-skipvalidation` takes no argument, so its value line is blank. It has
    to read as PRESENT, because presence is the entire test: a flag the binary
    does not know is simply never echoed."""
    opts = applog.options(log)
    assert "skipvalidation" in opts and opts["skipvalidation"] == ""
    assert "skipfeflow" not in opts, "an unknown flag must not appear"


# --- checkpoints ---

def test_the_three_checkpoints_are_found(log):
    assert applog.count(applog.CAMPAIGN_LOADED, log) == 2
    assert applog.count(applog.LOADING_CONTINUE, log) == 2
    assert applog.count(applog.RAID_RUNNING, log) == 2
    assert applog.state(log) == "raid"


def test_state_is_campaign_before_any_raid(tmp_path):
    p = tmp_path / "a.log"
    p.write_text("\n".join(SESSION.splitlines()[:10]), encoding="utf-8")
    assert applog.state(p) == "campaign"


def test_only_the_current_boot_is_read(tmp_path):
    """The log is truncated per launch in practice, but a previous boot's
    `option:` lines would grade a flag probe against the wrong launch, so the
    boundary is found rather than assumed."""
    p = tmp_path / "two.log"
    p.write_text(SESSION + "[2026-08-17 19:00:00] [INFO] [General] Game started... again\n"
                 "[2026-08-17 19:00:01] [INFO] [General] option: profile\n"
                 "[2026-08-17 19:00:01] [INFO] [General]  \"2\"\n", encoding="utf-8")
    assert applog.options(p) == {"profile": "2"}
    assert applog.state(p) == ""


# --- waiting ---

def test_wait_for_counts_NEW_lines_not_any_line(log):
    """`as_load_campaign` appears three times in one real session, so "has it
    ever appeared" is not an acknowledgement. Passing the count taken before
    the action is what makes it one."""
    before = applog.count(applog.CAMPAIGN_LOADED, log)
    assert applog.wait_for(applog.CAMPAIGN_LOADED, timeout=0.2, path=log,
                           since=before) is None
    assert applog.wait_for(applog.CAMPAIGN_LOADED, timeout=0.2, path=log,
                           since=before - 1) is not None


def test_wait_for_returns_as_soon_as_the_line_lands(log):
    with open(log, "a", encoding="utf-8") as fh:
        fh.write("[2026-08-17 18:05:00] [INFO] [General] "
                 "as_raid_start_done_loading - RaidStartDoneLoading()\n")
    t0 = time.perf_counter()
    hit = applog.wait_for(applog.RAID_RUNNING, timeout=2.0, path=log, since=2)
    assert hit is not None and hit.ts == "2026-08-17 18:05:00"
    assert time.perf_counter() - t0 < 1.5


# --- against the real log ---

@pytest.mark.skipif(not REAL.exists(), reason="game not installed")
def test_the_real_log_parses_and_echoes_the_live_launch_options():
    """The shipped log is the fixture's source. If the format ever changes,
    this fails before a flag probe silently reports 'not accepted'."""
    ents = applog.entries(REAL)
    assert ents and ents[0].msg.startswith("Game started")
    opts = applog.options(REAL)
    assert opts, "no launch options echoed - the option: format changed"
    assert "appname" in opts
