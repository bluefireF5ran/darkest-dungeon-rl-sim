"""A panel met on the way to a curio can be the OBSTACLE's (2026-09-24).

Walking to a sconce in the Warrens, the party met the rubble first; the curio
path clicked the hand and cleared it bare-handed with shovels in the pack.
"""
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from bridge import act  # noqa: E402


def _runner(monkeypatch, *, obstacle: bool, takes_items: bool):
    r = object.__new__(act.ActRunner)
    r.resolved = types.SimpleNamespace(win=types.SimpleNamespace(
        to_screen=lambda x, y: (x, y)))
    r._say = lambda _s: None
    r.stats = {"acted": 0}
    calls = {"obstacle": 0, "hand": 0}
    found = types.SimpleNamespace(readable=True, hand=(0.1, 0.1),
                                  sole_item=lambda: (0.5, 0.5))
    import bridge.curiopanel as cp
    import bridge.curios as cu
    import bridge.screen as sc
    monkeypatch.setattr(sc, "grab", lambda _w: object())
    monkeypatch.setattr(cp, "find", lambda _img: found)
    monkeypatch.setattr(cu, "definition_for", lambda _n: types.SimpleNamespace(
        items={"torch": 1} if takes_items else {}))
    monkeypatch.setattr(act.ActRunner, "_corridor_has_obstacle",
                        lambda self, area: obstacle)

    def _obstacle(self, area):
        calls["obstacle"] += 1
        return True
    monkeypatch.setattr(act.ActRunner, "_work_obstacle_panel", _obstacle)

    class _Act:
        def execute(self, plan):
            calls["hand"] += 1
            return True
    r.actuator = _Act()
    monkeypatch.setattr(act.time, "sleep", lambda _s: None)
    return r, calls


def test_a_lit_slot_on_the_way_to_an_itemless_curio_is_the_obstacle(monkeypatch):
    r, calls = _runner(monkeypatch, obstacle=True, takes_items=False)
    assert r._work_curio_panel("sconce", None, area="coDG", curio_id="sconce")
    assert calls == {"obstacle": 1, "hand": 0}


def test_no_obstacle_left_means_the_curio_panel_as_before(monkeypatch):
    r, calls = _runner(monkeypatch, obstacle=False, takes_items=False)
    r._work_curio_panel("sconce", None, area="coDG", curio_id="sconce")
    assert calls["obstacle"] == 0 and calls["hand"] == 1


def test_a_curio_that_takes_items_is_not_second_guessed(monkeypatch):
    r, calls = _runner(monkeypatch, obstacle=True, takes_items=True)
    r._work_curio_panel("pile_of_scrolls", None, area="coDG",
                        curio_id="pile_of_scrolls")
    assert calls["obstacle"] == 0
