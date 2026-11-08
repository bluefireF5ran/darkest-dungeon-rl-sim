"""`bridge/embark_veto.py`: re-draw a comp the raid layer would abandon.

Live 2026-09-27: a Cove party at 38% intact and 1.2 expected deaths passed
the 2.0-deaths gate, and the raid layer's `advise_retreat` pulled it out
after one fight. The veto now asks that same function.
"""
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from bridge import embark_veto  # noqa: E402
from bridge.live_state import LiveHero  # noqa: E402


class _Rollout:
    def __init__(self, *_a, **_k):
        pass

    def evaluate(self, *_a, **_k):
        return SimpleNamespace(success_rate=0.38, mean_deaths=1.2)


def _veto(monkeypatch, retreat: bool):
    import bridge.rollout as ro
    import bridge.retreat as rt
    monkeypatch.setattr(ro, "Rollout", _Rollout)
    monkeypatch.setattr(ro.PartyMember, "from_live_hero",
                        classmethod(lambda cls, h: SimpleNamespace(hp=1)))
    monkeypatch.setattr(embark_veto, "replace", lambda obj, **k: obj)
    monkeypatch.setattr(embark_veto, "expected_battles", lambda *a, **k: 5)
    monkeypatch.setattr(rt, "advise_retreat",
                        lambda *a, **k: SimpleNamespace(retreat=retreat,
                                                        line=lambda: "x"))
    return embark_veto.make_veto("m", say=lambda *_a: None)


def _args():
    quest = SimpleNamespace(length=1, dungeon="cove", quest_type="explore",
                            difficulty=1, gold_reward=3000)
    hero = SimpleNamespace(hero=LiveHero(guid=1, hero_class="leper"))
    seats = [SimpleNamespace(rank=1, hero=hero, skills=())]
    return quest, seats


def test_a_draw_the_raid_layer_would_retreat_from_is_vetoed(monkeypatch):
    veto = _veto(monkeypatch, retreat=True)
    assert "RETREAT" in veto(*_args())


def test_a_draw_the_raid_layer_would_keep_passes(monkeypatch):
    veto = _veto(monkeypatch, retreat=False)
    assert veto(*_args()) == ""
