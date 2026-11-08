"""Tests for campaign/week.py -- the weekly loop, end to end.

Invariants a campaign must keep whatever the decisions are: the vanilla
roster cap (never the Workshop's), heroes either alive or dead and never
both, money never spent twice, and the log one row per week.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest

import bridge.townplan as townplan
from campaign.expedition import ExpeditionRunner
from campaign.recruits import roster_size
from campaign.week import Campaign
from data.paths import CSV_DIR

SHIPPED = CSV_DIR / "models" / "dd_combat_ppo_full_expedition.zip"


def test_a_campaign_keeps_its_invariants():
    if not SHIPPED.exists():
        pytest.skip("no shipped checkpoint")
    saved = townplan.INCLUDE_WORKSHOP
    try:
        c = Campaign(ExpeditionRunner(str(SHIPPED)), seed=0)
        assert townplan.INCLUDE_WORKSHOP is False
        for _ in range(8):
            w = c.week()
            s = c.state
            cap = roster_size(s.ranks.get((0, "stage_coach.rostersize"), 0)) \
                + townplan.workshop_roster_bonus()
            assert len(s.heroes) <= cap, (w.week, len(s.heroes), cap)
            alive = {h.guid for h in s.heroes}
            assert not alive & {h.guid for h in s.dead}
            assert s.wallet.get("gold", 0) >= 0, w
        assert [w.week for w in c.log] == list(range(1, 9))
        assert any(w.success for w in c.log), "eight weeks and not one quest won"
        assert c.state.quests_finished >= 2           # the tutorial + at least one
    finally:
        townplan.INCLUDE_WORKSHOP = saved


def test_a_hero_equips_only_what_they_have_unlocked():
    from campaign.state import new_campaign
    from campaign.week import equip
    s = new_campaign()
    crusader = s.heroes[0]          # smite, zealous_accusation, stunning_blow, bulwark_of_faith
    comp = ("holy_lance", "smite", "inspiring_cry", "stunning_blow")
    assert equip(s, crusader, comp) == ["smite", "stunning_blow",
                                        "zealous_accusation", "bulwark_of_faith"]
    s.ranks[(crusader.guid, "crusader.holy_lance")] = 1      # the Guild bought it
    assert equip(s, crusader, comp)[:3] == ["holy_lance", "smite", "stunning_blow"]


def test_a_plot_offer_names_its_prize_and_pays_it():
    """The story bosses pay a very rare drawn per visit. Before 2026-10-10 the
    offer did not carry it, `boss_worth_it` said no to every plot quest, and
    no simulated campaign ever fought a boss."""
    from bridge.embarkplan import boss_worth_it
    from campaign.week import _plot_quest
    from data.paths import GAME_DIR
    from data.quest_gen_loader import load_plot_quests
    from data.trinket_tiers import worth_a_boss
    necro = next(p for p in load_plot_quests(GAME_DIR) if p.plot_id == "plot_kill_necromancer_1")
    assert necro.extra_trinket_rarities == ("very_rare",)
    assert boss_worth_it(_plot_quest(necro)) is False          # the old offer
    q = _plot_quest(necro, ("tough_ring",))
    assert q.reward_trinkets == ("tough_ring",)
    assert worth_a_boss("tough_ring") and boss_worth_it(q)


def test_gear_buying_always_keeps_the_provisions_reserve():
    """Fran, 2026-10-10: "siempre hay que guardar algo para provisiones" --
    even a budget of 1.0 leaves the dearest Long basket in the wallet."""
    from types import SimpleNamespace
    from tests._fixtures import get_store
    saved = townplan.INCLUDE_WORKSHOP
    try:
        c = Campaign(SimpleNamespace(store=get_store()), seed=0, gear_budget=1.0)
        reserve = c.provisions_reserve()
        c.state.ranks[(0, "blacksmith.weapon")] = 2      # the Blacksmith sells up
        c.state.ranks[(0, "blacksmith.armour")] = 2
        for h in c.state.heroes:
            h.resolve_level = 2                         # gear ranks are resolve-gated
        c.state.wallet["gold"] = 60000
        assert c._buy_gear(c.state.heroes) == 0         # the tutorial only: the Blacksmith is shut
        c.state.quests_finished = 3                     # ...and opens after 3 quests
        assert 7000 < reserve < 9000
        for gold in (reserve - 500, reserve + 3000, 60000):
            for h in c.state.heroes:
                h.weapon_rank = h.armour_rank = 0
            c.state.wallet["gold"] = gold
            spent = c._buy_gear(c.state.heroes)
            assert c.state.wallet["gold"] >= min(gold, reserve), (gold, spent)
        assert spent > 0                        # 60k does buy something
    finally:
        townplan.INCLUDE_WORKSHOP = saved


if __name__ == "__main__":
    for name, fn in sorted(list(globals().items())):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  ok  {name}")
