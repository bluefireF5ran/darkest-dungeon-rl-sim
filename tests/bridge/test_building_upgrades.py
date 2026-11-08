"""`town.buy_building_upgrades` -- non-coach ranks, each in its own panel.

Live 2026-09-24 guild and sanitarium ranks were clicked at points inside the
COACH panel. Every building shares `building_base_layout`, so the fix is the
panel, not the geometry: open THAT building, draw its trees, buy, check the
wallet.
"""
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from bridge import town  # noqa: E402


def _pu(tree_id, code, trades=()):
    node = SimpleNamespace(tree_id=tree_id, code=code,
                           label=lambda: f"{tree_id}.{code}")
    return SimpleNamespace(node=node, trades=trades)


def _rig(monkeypatch, wallets):
    import bridge.act as act_mod
    sent = []
    monkeypatch.setattr(act_mod, "plan_open_building",
                        lambda name, r: ("open", name))
    monkeypatch.setattr(act_mod, "plan_open_upgrades", lambda r: ("crest",))
    monkeypatch.setattr(act_mod, "plan_buy_upgrade",
                        lambda pu, ti, r: ("buy", pu.node.tree_id, pu.node.code))
    monkeypatch.setattr(act_mod, "plan_close_building", lambda r: ("close",))
    monkeypatch.setattr(town, "_send",
                        lambda b, plan, **k: sent.append(plan) or True)
    monkeypatch.setattr(town, "_panel_is_open", lambda w: True)
    monkeypatch.setattr(town, "_on_town_screen", lambda w: True)
    monkeypatch.setattr(town, "_tree_index", lambda n: 0)
    monkeypatch.setattr(town.time, "sleep", lambda s: None)
    it = iter(wallets)
    monkeypatch.setattr(town, "_wallet", lambda p: next(it))
    monkeypatch.setattr(town, "_await", lambda f, before: f())
    return sent


def test_each_building_is_opened_before_its_ranks_are_clicked(monkeypatch):
    sent = _rig(monkeypatch, [{"gold": 9}, {"gold": 8}, {"gold": 8}, {"gold": 7}])
    done = town.buy_building_upgrades(
        None, None, None, "p",
        [_pu("guild.cost", "a"), _pu("sanitarium.cost", "a")], act=True)
    assert [d.confirmed for d in done] == [True, True], done
    assert sent == [("open", "guild"), ("crest",), ("buy", "guild.cost", "a"),
                    ("close",), ("open", "sanitarium"), ("crest",),
                    ("buy", "sanitarium.cost", "a"), ("close",)]


def test_a_rank_the_wallet_does_not_confirm_stops_that_building(monkeypatch):
    sent = _rig(monkeypatch, [{"gold": 9}, {"gold": 9}])
    done = town.buy_building_upgrades(
        None, None, None, "p",
        [_pu("guild.cost", "a"), _pu("guild.cost", "b")], act=True)
    assert len(done) == 1 and not done[0].confirmed
    assert ("buy", "guild.cost", "b") not in sent


def test_a_rank_that_needs_a_trade_is_reported_not_sent(monkeypatch):
    sent = _rig(monkeypatch, [])
    done = town.buy_building_upgrades(
        None, None, None, "p", [_pu("blacksmith.cost", "a", trades=("x",))],
        act=True)
    assert not done[0].sent and "trade" in done[0].why
    assert sent == []
