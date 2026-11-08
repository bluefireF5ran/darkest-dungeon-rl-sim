"""`bridge/trinketplan.py` -- Fran's team-builder picks the trinkets.

The builder's valuation is called, not ported (`tools/teambuilder_reequip.js`).
These pin the Python half: ids round-trip, one trinket goes to one hero, and
any failure means no picks rather than an exception.
"""
import json
import shutil
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from bridge import trinketplan as TP  # noqa: E402


def _fake_run(stdout):
    def run(*_a, **_k):
        return types.SimpleNamespace(stdout=stdout)
    return run


def test_picks_come_back_as_the_OWNED_ids(monkeypatch):
    monkeypatch.setattr(TP.shutil, "which", lambda _n: "node")
    monkeypatch.setattr(TP.subprocess, "run", _fake_run(json.dumps({"heroes": [
        {"trinket1": "Snake Oil", "trinket2": "Speed Stone"},
        {"trinket1": "Ancestor's Pistol", "trinket2": ""}]})))
    got = TP.plan_trinkets([("leper", []), ("highwayman", [])],
                           ["snake_oil", "speed_stone", "ancestors_pistol"])
    assert got == [["snake_oil", "speed_stone"], ["ancestors_pistol"]]


def test_one_trinket_is_handed_out_once(monkeypatch):
    monkeypatch.setattr(TP.shutil, "which", lambda _n: "node")
    monkeypatch.setattr(TP.subprocess, "run", _fake_run(json.dumps({"heroes": [
        {"trinket1": "Snake Oil", "trinket2": ""},
        {"trinket1": "Snake Oil", "trinket2": ""}]})))
    assert TP.plan_trinkets([("leper", []), ("vestal", [])],
                            ["snake_oil"]) == [["snake_oil"], []]


def test_no_node_means_no_picks_not_an_error(monkeypatch):
    monkeypatch.setattr(TP.shutil, "which", lambda _n: None)
    assert TP.plan_trinkets([("leper", [])], ["snake_oil"]) == []


def test_garbage_from_the_builder_means_no_picks(monkeypatch):
    monkeypatch.setattr(TP.shutil, "which", lambda _n: "node")
    monkeypatch.setattr(TP.subprocess, "run", _fake_run("not json"))
    assert TP.plan_trinkets([("leper", [])], ["snake_oil"]) == []


@pytest.mark.skipif(shutil.which("node") is None or not Path(
    "F:/Personal_Fran/Programas/Softwares/nextjs_projects/dd-team-builder").exists(),
    reason="no node or no team-builder checkout")
def test_the_real_builder_equips_something_from_a_real_inventory():
    got = TP.plan_trinkets([("leper", []), ("crusader", []), ("plague_doctor", []),
                            ("vestal", [])],
                           ["agile_talon", "move_stone", "protection_stone",
                            "recovery_chalice", "snake_oil", "speed_stone"])
    flat = [t for picks in got for t in picks]
    assert flat and len(flat) == len(set(flat))
