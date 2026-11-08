"""Tests for campaign/state.py -- the campaign state and its two views.

The point is the SEAM: a simulated state must read back through the bridge's
own readers (`data.hero_skills`, `data.heirlooms`) and drive the bridge's own
decide layers (`townplan.advise`, `embarkplan.plan_embark`) unchanged. If a
reader's expectations move, these go red instead of the simulator quietly
playing a Hamlet with no skills unlocked.
"""

import random
import sys
import tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests._fixtures import get_store

from bridge.embarkplan import plan_embark
from bridge.townplan import advise
from campaign.board import generate_board
from campaign.state import new_campaign
from data.heirlooms import read_rank_counts
from data.hero_skills import unlocked
from data.quest_gen_loader import load

PD = ("noxious_blast", "plague_grenade", "blinding_gas", "battlefield_medicine")
VESTAL = ("mace_bash", "dazzling_light", "divine_grace", "gods_comfort")


def _four():
    s = new_campaign()
    s.visits = 1
    s.add_hero("plague_doctor", combat_skills=PD)
    s.add_hero("vestal", combat_skills=VESTAL)
    return s


def test_a_campaign_starts_from_the_games_own_starting_save():
    s = new_campaign()
    assert [h.hero_class for h in s.heroes] == ["crusader", "highwayman"]
    assert [h.guid for h in s.heroes] == [1, 2]
    assert s.heroes[0].quirks == ["warrior_of_light", "kleptomaniac", "god_fearing"]
    # starting heirlooms + the tutorial's 3000 gold and 4 crests
    assert s.wallet == {"gold": 3000, "bust": 10, "portrait": 10, "deed": 10, "crest": 24}
    assert s.completed_plots == {"plot_tutorial_crypts"} and s.quests_finished == 1
    assert s.next_guid == 5


def test_the_written_profile_reads_back_through_the_bridge_readers():
    s = _four()
    prof = s.write_profile(tempfile.mkdtemp())
    assert len(read_rank_counts(prof / "persist.upgrades.json")) == 14 + 8
    by_class = {rh.hero_class: rh for rh in s.roster()}
    assert set(unlocked(prof, by_class["crusader"])) == {
        "smite", "zealous_accusation", "stunning_blow", "bulwark_of_faith"}
    assert set(unlocked(prof, by_class["vestal"])) == set(VESTAL)


def test_the_bridge_decide_layers_run_on_a_simulated_hamlet():
    s = _four()
    board = generate_board(random.Random(0), load(), visit_index=s.visits,
                           quests_finished=s.quests_finished, dungeon_xp=s.dungeon_xp,
                           roster_levels=[h.resolve_level for h in s.heroes],
                           completed_plots=s.completed_plots)
    town = s.to_town_state([q.quest for q in board])
    prof = s.write_profile(tempfile.mkdtemp())
    store = get_store()
    plan = advise(town, store=store, upgrades_file=prof / "persist.upgrades.json")
    assert plan.wallet_after["gold"] <= s.wallet["gold"]
    embark = plan_embark(town, store, prof, random.Random(0))
    assert embark is not None and embark.quest is not None
    assert embark.quest.dungeon == "crypts"
    assert len(embark.party) == 4
    assert 0 < embark.basket_cost <= s.wallet["gold"]


def test_kill_moves_a_hero_to_the_dead():
    s = _four()
    s.buildings[1] = "abbey"
    s.kill(1)
    assert [h.guid for h in s.dead] == [1] and 1 not in s.buildings
    assert all(h.guid != 1 for h in s.heroes)


def test_a_skill_the_class_does_not_have_is_refused():
    s = new_campaign()
    try:
        s.add_hero("vestal", combat_skills=("mace_bash", "divine_comfort"))
    except ValueError as e:
        assert "divine_comfort" in str(e)
    else:
        raise AssertionError("an unknown skill was accepted")


if __name__ == "__main__":
    for name, fn in sorted(list(globals().items())):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  ok  {name}")
