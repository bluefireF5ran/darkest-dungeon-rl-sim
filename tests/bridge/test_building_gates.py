"""A building spends nothing until it opens (Fran, 2026-10-12: "On the first
weeks not all buildings are available"). The gates are each building's own
`.building.json` `requirements`."""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest

from bridge import townplan as TP
from bridge.town_state import TownState, read_quests_finished
from data.paths import GAME_DIR
from data.town_costs import closed_buildings, load_building_gates

pytestmark = pytest.mark.skipif(not GAME_DIR.exists(), reason="needs the game install")

STARTING_WALLET = {"bust": 10, "portrait": 10, "deed": 10, "crest": 20, "gold": 500}


@pytest.fixture(autouse=True)
def _vanilla(monkeypatch):
    monkeypatch.setattr(TP, "exchange_is_modded", lambda *_a: False)
    monkeypatch.setattr(TP, "roster_cap", lambda *a, **k: 12)


def test_the_shipped_gates():
    g = load_building_gates(str(GAME_DIR))
    assert (g["guild"].quests, g["blacksmith"].quests, g["abbey"].quests,
            g["sanitarium"].quests, g["stage_coach"].quests) == (3, 3, 2, 4, 0)
    assert g["camping_trainer"].dungeon_level == 2


def test_after_the_tutorial_only_the_coach_and_the_wagon_sell_upgrades():
    shut = closed_buildings(str(GAME_DIR), 1, 0)
    assert {"guild", "blacksmith", "abbey", "sanitarium"} <= shut
    assert not {"stage_coach", "nomad_wagon"} & shut
    assert closed_buildings(str(GAME_DIR), None, None) == frozenset()   # unread = open


def test_the_post_tutorial_visit_spends_its_heirlooms_where_it_can():
    """Smith first, but the Guild and the Blacksmith are shut at 1 quest, so
    the starting heirlooms go to the coach -- PLAYBOOK line 110 falls out of
    the gates without a special case."""
    town = TownState(wallet=dict(STARTING_WALLET), quests_finished=1)
    advice = TP.advise(town)
    bought = {p.node.tree_id.split(".")[0] for p in advice.purchases}
    assert bought and not bought & {"guild", "blacksmith", "abbey", "sanitarium"}
    assert any("not open yet" in n for n in advice.notes)
    opened = TP.advise(TownState(wallet=dict(STARTING_WALLET), quests_finished=3))
    assert opened.purchases[0].node.tree_id.split(".")[0] in ("guild", "blacksmith")


def test_the_switch_restores_the_world_before(monkeypatch):
    monkeypatch.setattr(TP, "BUILDING_GATES", False)
    assert TP.closed(TownState(quests_finished=0)) == frozenset()


def test_no_gear_or_skill_rank_at_a_shut_building():
    from bridge.live_state import LiveHero
    hero = LiveHero(hero_class="crusader", guid=1, resolve_level=1)
    purchased = {(t, c) for t in ("blacksmith.weapon", "blacksmith.armour",
                                  "guild.skill_levels") for c in "abcde"}
    every = TP.nurture_buys(hero, {}, purchased, GAME_DIR)
    assert {b.building for b in every} == {"blacksmith", "guild"}
    none_shut = TP.nurture_buys(hero, {}, purchased, GAME_DIR,
                                closed=frozenset({"blacksmith", "guild"}))
    assert none_shut == []
    guild_only = TP.nurture_buys(hero, {}, purchased, GAME_DIR, closed=frozenset({"blacksmith"}))
    assert guild_only and {b.building for b in guild_only} == {"guild"}


def test_an_unreadable_progression_file_is_none():
    with tempfile.TemporaryDirectory() as d:
        assert read_quests_finished(Path(d) / "persist.progression.json") is None
        bad = Path(d) / "persist.progression.json"
        bad.write_bytes(b"\x00\x01torn")
        assert read_quests_finished(bad) is None


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q", "-n0"]))
