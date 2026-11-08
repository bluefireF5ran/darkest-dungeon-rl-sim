"""`tools/recording_report.py`: labels, the summary, and unpacking a session
into the triples `bridge/run.py --replay` reads."""
from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools import recording_report as RR


def _session(tmp_path, events, saves=()):
    s = tmp_path / "20260929-200000"
    (s / "saves").mkdir(parents=True)
    for rel, data in saves:
        (s / rel).write_bytes(gzip.compress(data))
    (s / "events.jsonl").write_text(
        "\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
    return s


def test_a_click_on_a_known_constant_is_named_after_it():
    """The label is the bridge's own geometry: clicking where the bridge
    would click MODAL_OK is a MODAL_OK click."""
    from bridge.layout import MODAL_OK
    lab = RR.Labeller()
    ev = {"fx": MODAL_OK[0], "fy": MODAL_OK[1], "w": 1280, "h": 720}
    assert lab.label(ev) == "modal_ok"


def test_a_click_far_from_everything_is_unlabelled_not_guessed():
    lab = RR.Labeller()
    lab._consts = [RR.Target("only", 0.9, 0.9)]
    lab._board = {(1280, 720): []}
    assert lab.label({"fx": 0.1, "fy": 0.1, "w": 1280, "h": 720}) == "?"


def test_the_label_radius_is_round_on_a_wide_client():
    lab = RR.Labeller()
    lab._consts = [RR.Target("dot", 0.5, 0.5)]
    lab._board = {(1280, 720): []}
    # 0.015 of the width sideways is inside; the same distance in pixels
    # downwards is 0.015 * 1280 / 720 of the height.
    assert lab.label({"fx": 0.515, "fy": 0.5, "w": 1280, "h": 720}) == "dot"
    assert lab.label({"fx": 0.5, "fy": 0.5 + 0.015 * 1280 / 720,
                      "w": 1280, "h": 720}) == "dot"
    assert lab.label({"fx": 0.5, "fy": 0.5 + 0.03 * 1280 / 720,
                      "w": 1280, "h": 720}) == "?"


def test_the_summary_counts_presses_focus_and_marks(tmp_path):
    ev = [
        {"t": 0, "dt": 0.0, "kind": "session_start", "out": "x"},
        {"t": 1, "dt": 1.0, "kind": "focus", "state": "in"},
        {"t": 2, "dt": 2.0, "kind": "key", "key": "1", "action": "down",
         "screen": "raid"},
        {"t": 2.5, "dt": 2.5, "kind": "mouse", "button": "left",
         "action": "down", "screen": "raid", "fx": 0.01, "fy": 0.99,
         "w": 1280, "h": 720},
        {"t": 3, "dt": 3.0, "kind": "key", "key": "f8", "action": "down",
         "screen": "raid"},
        {"t": 11, "dt": 11.0, "kind": "focus", "state": "out"},
        {"t": 12, "dt": 12.0, "kind": "session_end"},
    ]
    text = RR.summary(ev)
    assert "3 presses" in text
    assert "game focused 0.2 min" in text
    assert "1 marks (F8)" in text
    assert "ended cleanly" in text


def test_unpack_pairs_each_raid_write_with_the_latest_roster(tmp_path):
    saves = [("saves/000001_profile_4__persist.roster.json.gz", b"R1"),
             ("saves/000002_profile_4__persist.raid.json.gz", b"A1"),
             ("saves/000003_profile_4__persist.raid.json.gz", b"A2"),
             ("saves/000004_profile_4__persist.roster.json.gz", b"R2"),
             ("saves/000005_profile_4__persist.map.json.gz", b"M1"),
             ("saves/000006_profile_4__persist.raid.json.gz", b"A3")]
    ev = [{"t": i, "dt": float(i), "kind": "save", "profile": "profile_4",
           "file": rel.split("__")[1][:-3], "snap": rel}
          for i, (rel, _) in enumerate(saves)]
    s = _session(tmp_path, ev, saves)
    out = tmp_path / "replay"
    assert RR.unpack(s, out) == 3
    got = {p.name: p.read_bytes() for p in out.iterdir()}
    assert got["000001_persist.raid.json"] == b"A1"
    assert got["000001_persist.roster.json"] == b"R1"
    assert got["000002_persist.roster.json"] == b"R1"
    assert got["000003_persist.roster.json"] == b"R2"
    assert got["000003_persist.map.json"] == b"M1"
    assert "000001_persist.map.json" not in got
    # The pairing run.py --replay relies on: same prefix, sorted by name.
    raid = sorted(out.glob("*_persist.raid.json"))
    assert [p.name[:7] for p in raid] == ["000001_", "000002_", "000003_"]


def test_a_torn_last_line_does_not_lose_the_session(tmp_path):
    s = _session(tmp_path, [{"t": 0, "dt": 0.0, "kind": "session_start"}])
    with open(s / "events.jsonl", "a", encoding="utf-8") as fh:
        fh.write('{"t": 1, "dt": 1.0, "kind": "mo')
    assert [e["kind"] for e in RR.load(s)] == ["session_start"]


def test_an_unnamed_press_is_placed_in_the_raid_or_the_town_by_the_saves():
    """The recogniser has no raid reference, so most presses in a dungeon read
    `?`. The save stream says which side of the gate they were on."""
    ev = [
        {"kind": "save", "dt": 1.0, "file": "persist.town.json", "baseline": True},
        {"kind": "mouse", "dt": 2.0, "action": "down", "screen": ""},
        {"kind": "save", "dt": 3.0, "file": "persist.estate.json", "baseline": False},
        {"kind": "mouse", "dt": 4.0, "action": "down", "screen": ""},
        {"kind": "save", "dt": 5.0, "file": "persist.raid.json", "baseline": False},
        {"kind": "key", "dt": 6.0, "action": "down", "screen": ""},
        {"kind": "mouse", "dt": 7.0, "action": "down", "screen": "hunger"},
    ]
    RR.annotate_context(ev)
    assert [RR.screen_of(e) for e in ev if e["kind"] != "save"] ==         ["?", "town?", "raid?", "hunger"]
