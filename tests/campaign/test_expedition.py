"""Tests for campaign/expedition.py -- one campaign expedition.

The load-bearing check lives in the runner itself: every run compares the
deaths it read out of the env with the eval's own count and raises on a
mismatch. Its first run found one (a death in the LAST fight is not in
`_alive_indices`), so these tests drive enough episodes for that check to
mean something, on quests hard enough for level-0 heroes to die in.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest

from tests._fixtures import get_store

from campaign.expedition import ExpeditionRunner, boss_group
from campaign.state import new_campaign
from data.paths import CSV_DIR

SHIPPED = CSV_DIR / "models" / "dd_combat_ppo_full_expedition.zip"


def test_a_plot_boss_is_fought_in_its_own_mash_group():
    store = get_store()
    assert boss_group(store, "weald", 5, ("hag_C",)) == ["cauldron_empty_C", "hag_C"]
    assert boss_group(store, "darkestdungeon", 6, ("shuffler_D",)) == ["shuffler_D", "cultist_shrouded_D"]
    # The Heart has no mash group: fielded alone (an approximation, see the module).
    assert boss_group(store, "darkestdungeon", 6, ("ancestor_heart_D",)) == ["ancestor_heart_D"]
    assert boss_group(store, "crypts", 1, ()) is None


def _party():
    s = new_campaign()
    s.add_hero("plague_doctor", combat_skills=("noxious_blast", "plague_grenade",
                                               "blinding_gas", "battlefield_medicine"))
    s.add_hero("vestal", combat_skills=("mace_bash", "dazzling_light",
                                        "divine_grace", "gods_comfort"))
    return [s.heroes[i] for i in (0, 2, 1, 3)]


def test_hero_fates_agree_with_the_eval_on_deadly_quests():
    if not SHIPPED.exists():
        pytest.skip("no shipped checkpoint")
    runner = ExpeditionRunner(str(SHIPPED))
    party = _party()
    deaths = 0
    for seed in range(30):
        # Level-0 heroes on a Champion quest: deaths in the last fight happen.
        r = runner.run(party, dungeon="cove", difficulty=5, length=2, seed=seed)
        assert [f.guid for f in r.heroes] == [h.guid for h in party]
        assert r.wiped == all(f.died for f in r.heroes)
        deaths += r.deaths
    assert deaths > 0, "no death in 30 Champion runs: the check proved nothing"


def test_a_run_is_reproducible_from_its_seed():
    if not SHIPPED.exists():
        pytest.skip("no shipped checkpoint")
    runner = ExpeditionRunner(str(SHIPPED))
    party = _party()
    a = runner.run(party, dungeon="crypts", difficulty=1, length=1, seed=7)
    b = runner.run(party, dungeon="crypts", difficulty=1, length=1, seed=7)
    assert a == b


if __name__ == "__main__":
    for name, fn in sorted(list(globals().items())):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  ok  {name}")
