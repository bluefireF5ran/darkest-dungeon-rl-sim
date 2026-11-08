"""The play recorder's pure parts: the privacy gate, the input names, the
event log and the save watcher. The Windows hook itself needs a desktop and
is exercised by a live session, not here.

Fran, 2026-09-28: *"a logger of the inputs to try to teach the bridge how I
play"*.
"""
from __future__ import annotations

import gzip
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from bridge import recorder as R


def test_nothing_is_recorded_unless_the_game_is_focused():
    """The hook sees the whole desktop. A password typed in a browser must
    never reach the log, and neither may input while the game is unknown."""
    assert R.should_record(1234, 1234, injected=False)
    assert not R.should_record(999, 1234, injected=False)       # other window
    assert not R.should_record(1234, None, injected=False)      # game unknown
    assert not R.should_record(0, None, injected=False)


def test_input_the_bridge_sent_is_not_the_human_s():
    assert not R.should_record(1234, 1234, injected=True)


def test_key_names_cover_the_game_s_hotkeys():
    # 1-5 skills, A/D move, T torch, space loot, W interact, esc.
    assert [R.key_name(v) for v in (0x31, 0x35, 0x41, 0x44, 0x54, 0x57)] == \
        ["1", "5", "a", "d", "t", "w"]
    assert R.key_name(0x20) == "space"
    assert R.key_name(0x1B) == "esc"
    assert R.key_name(0x78) == "f9"
    assert R.key_name(0xFF).startswith("vk")


def test_mouse_messages_keep_presses_releases_and_wheel_not_moves():
    assert R.mouse_kind(0x0201) == ("left", "down")
    assert R.mouse_kind(0x0205) == ("right", "up")
    assert R.mouse_kind(0x020A) == ("wheel", "wheel")
    assert R.mouse_kind(0x0200) is None                        # WM_MOUSEMOVE


def test_the_event_log_appends_one_json_line_per_event(tmp_path):
    log = R.EventLog(tmp_path / "s" / "events.jsonl", start=100.0)
    log.write("mouse", t=101.5, button="left", action="down")
    log.write("key", t=102.0, key="w", action="down")
    log.close()
    lines = (tmp_path / "s" / "events.jsonl").read_text(encoding="utf-8").splitlines()
    recs = [json.loads(line) for line in lines]
    assert [r["kind"] for r in recs] == ["mouse", "key"]
    assert recs[0]["dt"] == 1.5
    assert log.counts == {"mouse": 1, "key": 1}


class _Win:
    client = (100, 50, 1380, 770)          # a window NOT at the screen origin
    client_size = (1280, 720)
    pid = 4242


def test_a_click_is_stored_in_client_coordinates_with_its_screen(
        tmp_path, monkeypatch):
    """Screen pixels are useless once the window moves; the layout, the
    bridge and the report all speak client fractions."""
    import numpy as np
    from bridge import screen as screen_mod
    monkeypatch.setattr(screen_mod, "grab",
                        lambda win: np.full((720, 1280, 3), 40, dtype=np.uint8))
    monkeypatch.setattr(screen_mod, "recognise", lambda img: "raid")
    gate = R.Gate()
    gate.win, gate.pid = _Win(), 4242
    log = R.EventLog(tmp_path / "events.jsonl", start=1000.0)
    ann = R.Annotator(gate, log, tmp_path / "frames", say=lambda *_: None)
    ann.handle((1001.0, "mouse", "left", "down", 740, 410, 0, []))
    ann.handle((1001.1, "mouse", "left", "up", 745, 412, 0, []))
    ann.handle((1002.0, "key", 0x31, "down", 0, 0, 0, ["shift"]))
    log.close()
    recs = [json.loads(line) for line in
            (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    down, up, key = recs
    assert (down["x"], down["y"], down["fx"], down["fy"]) == (640, 360, 0.5, 0.5)
    assert down["screen"] == "raid"
    assert up["screen"] == "raid" and "frame" not in up    # no grab on release
    assert key["key"] == "1" and key["mods"] == ["shift"]
    try:
        import PIL  # noqa: F401
        assert (tmp_path / down["frame"]).exists()
    except ImportError:
        assert down["frame"] == ""              # no Pillow: no frame, no crash


def test_black_frames_warn_once_instead_of_recording_nothing_silently(
        tmp_path, monkeypatch):
    """A borderless game covering its monitor grabs black. The session would
    still record inputs and saves, so the failure is a quiet one -- it has to
    be said on screen, once, while there is still time to relaunch."""
    import numpy as np
    from bridge import screen as screen_mod
    monkeypatch.setattr(screen_mod, "grab",
                        lambda win: np.zeros((720, 1280, 3), dtype=np.uint8))
    monkeypatch.setattr(screen_mod, "recognise", lambda img: "")
    said = []
    gate = R.Gate()
    gate.win, gate.pid = _Win(), 4242
    log = R.EventLog(tmp_path / "events.jsonl", start=1000.0)
    ann = R.Annotator(gate, log, None, say=said.append)
    for i in range(R.BLACK_STREAK + 2):
        ann.handle((1001.0 + i, "mouse", "left", "down", 740, 410, 0, []))
    log.close()
    assert len(said) == 1 and "black" in said[0]
    assert log.counts["warning"] == 1


def _profile(root: Path, name: str) -> Path:
    p = root / "7" / "262060" / "remote" / name
    p.mkdir(parents=True)
    return p


def _touch(f: Path, text: str, bump: float) -> None:
    f.write_text(text, encoding="utf-8")
    st = f.stat()
    os.utime(f, (st.st_atime, st.st_mtime + bump))


def _watcher(tmp_path):
    return R.SaveWatcher(out=tmp_path / "saves", root=tmp_path / "ud",
                         glob="*/262060/remote/profile_*")


def test_a_save_is_banked_once_it_stops_moving(tmp_path):
    prof = _profile(tmp_path / "ud", "profile_3")
    raid = prof / "persist.raid.json"
    _touch(raid, "a", 0)
    w = _watcher(tmp_path)
    w.prime()
    assert w.poll() == []                          # nothing moved
    _touch(raid, "b", 5)
    assert w.poll() == []                          # moved: wait one poll
    got = w.poll()
    assert [g["file"] for g in got] == ["persist.raid.json"]
    snap = tmp_path / got[0]["snap"]
    assert gzip.decompress(snap.read_bytes()) == b"b"


def test_the_whole_profile_is_banked_the_first_time_it_moves(tmp_path):
    """Replay needs the roster beside the raid; a profile that only wrote
    its raid file would otherwise have no roster in the session at all."""
    prof = _profile(tmp_path / "ud", "profile_3")
    _touch(prof / "persist.roster.json", "heroes", 0)
    _touch(prof / "persist.raid.json", "r0", 0)
    other = _profile(tmp_path / "ud", "profile_8")
    _touch(other / "persist.roster.json", "someone else", 0)
    w = _watcher(tmp_path)
    w.prime()
    _touch(prof / "persist.raid.json", "r1", 5)
    w.poll()
    got = w.poll()
    files = sorted((g["profile"], g["file"], g["baseline"]) for g in got)
    assert files == [("profile_3", "persist.raid.json", False),
                     ("profile_3", "persist.roster.json", True)]


def test_an_unchanged_rewrite_is_not_banked_twice(tmp_path):
    prof = _profile(tmp_path / "ud", "profile_3")
    raid = prof / "persist.raid.json"
    _touch(raid, "same", 0)
    w = _watcher(tmp_path)
    w.prime()
    _touch(raid, "same2", 5)
    w.poll(); assert len(w.poll()) == 1
    _touch(raid, "same2", 10)                      # rewritten, same bytes
    w.poll(); assert w.poll() == []


def test_a_new_campaign_made_mid_session_is_found(tmp_path):
    (tmp_path / "ud" / "7" / "262060" / "remote").mkdir(parents=True)
    w = _watcher(tmp_path)
    w.prime()
    prof = _profile(tmp_path / "ud", "profile_4")
    _touch(prof / "persist.game.json", "new", 0)
    w.poll()
    got = w.poll()
    assert [(g["profile"], g["file"]) for g in got] == \
        [("profile_4", "persist.game.json")]


def test_a_hook_windows_dropped_is_noticed_by_the_system_s_own_input_tick():
    """Windows removes a slow low-level hook and says nothing; the session
    would bank saves all evening and no input at all."""
    assert not R.hook_is_deaf(10_000, 10_000)
    assert not R.hook_is_deaf(10_000, 12_000)            # hook ahead: normal
    assert not R.hook_is_deaf(10_000 + R.HOOK_SLACK_MS, 10_000)
    assert R.hook_is_deaf(10_001 + R.HOOK_SLACK_MS, 10_000)
    # GetTickCount wraps every 49.7 days, and that is not a deaf hook.
    assert not R.hook_is_deaf(5, 0xFFFFFFF0)
    assert R.hook_is_deaf(R.HOOK_SLACK_MS + 100, 0xFFFFFFF0)


def test_a_mark_is_acknowledged_once_per_press(tmp_path, monkeypatch):
    """F8 shows nothing in the game, so the recorder answers by ear."""
    import numpy as np
    from bridge import screen as screen_mod
    monkeypatch.setattr(screen_mod, "grab",
                        lambda win: np.full((720, 1280, 3), 40, dtype=np.uint8))
    monkeypatch.setattr(screen_mod, "recognise", lambda img: "raid")
    beeps = []
    gate = R.Gate()
    gate.win, gate.pid = _Win(), 4242
    log = R.EventLog(tmp_path / "events.jsonl", start=1000.0)
    ann = R.Annotator(gate, log, None, say=lambda *_: None,
                      on_mark=lambda: beeps.append(1))
    ann.handle((1060.0, "key", R.MARK_VK, "down", 0, 0, 0, []))
    ann.handle((1060.1, "key", R.MARK_VK, "up", 0, 0, 0, []))
    ann.handle((1061.0, "key", 0x31, "down", 0, 0, 0, []))
    log.close()
    assert beeps == [1] and ann.marks == 1
    assert R.key_name(R.MARK_VK) == "f8"


def test_a_loading_fade_is_not_a_black_capture(tmp_path, monkeypatch):
    """The first real session warned at 43 s on two black loading frames,
    then banked 2,489 good ones. Only a streak means the capture is blind."""
    import numpy as np
    from bridge import screen as screen_mod
    frames = iter([0, 0, 40] * 4)
    monkeypatch.setattr(screen_mod, "grab", lambda win: np.full(
        (720, 1280, 3), next(frames), dtype=np.uint8))
    monkeypatch.setattr(screen_mod, "recognise", lambda img: "")
    said = []
    gate = R.Gate()
    gate.win, gate.pid = _Win(), 4242
    log = R.EventLog(tmp_path / "events.jsonl", start=1000.0)
    ann = R.Annotator(gate, log, None, say=said.append)
    for i in range(12):
        ann.handle((1001.0 + i, "mouse", "left", "down", 740, 410, 0, []))
    log.close()
    assert said == [] and "warning" not in log.counts
