"""Tests for the retreat policy (`bridge/retreat.py`) and its price tables
(`data/town_costs.py`).

The point of the module is that PLAYBOOK 2.6's decision is an arithmetic
comparison of two gold bills, not a probability against a threshold. These tests
pin the exchange rates against the shipped files — if the Abbey's price or a
hero's upgrade tree ever reads differently, the decision silently changes sign
and nothing else would notice.
"""

import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest

from bridge.retreat import QuestReward, advise_retreat, party_investment, read_quest_reward
from data.rules import RulesLoader
from data.town_costs import gold_per_stress, hero_investment, load_hero_costs

REPO = Path(__file__).resolve().parents[2]
from data.paths import GAME_DIR
CAPTURE = REPO / "archive" / "traces" / "raidsnaps2" / "20260811-220332_00400_persist.raid.json"


@dataclass
class _Hero:
    hero_class: str
    weapon_rank: int = 0
    armour_rank: int = 0
    selected_skills: tuple = ()
    hp: float = 20.0
    stress: float = 0.0


@dataclass
class _Result:
    mean_deaths: float = 0.0
    mean_final_stress: float = 0.0
    completion_rate: float = 1.0
    success_rate: float = 1.0
    #: P(nobody dies). `None` exercises `advise_retreat`'s `mean_deaths`
    #: fallback, which is what a caller with an older result object gets —
    #: several tests below rely on that branch, so it stays the default.
    zero_death_rate: float = None


@lru_cache(maxsize=1)
def rules():
    return RulesLoader.load(GAME_DIR / "shared" / "rules.json")


# --- the exchange rates, straight from shipped files ---

def test_stress_is_priced_by_the_abbey():
    """1000 gold for 45 stress un-upgraded, 700 for 70 fully upgraded."""
    assert gold_per_stress(GAME_DIR, 0) == pytest.approx(1000 / 45, rel=1e-3)
    assert gold_per_stress(GAME_DIR, 2) == pytest.approx(10.0, rel=1e-3)


def test_the_un_upgraded_rate_is_the_conservative_default():
    """A dearer stress point argues for pulling out, so tier 0 is the safe
    default for a decision that costs a hero if it is wrong."""
    assert gold_per_stress(GAME_DIR, 0) > gold_per_stress(GAME_DIR, 2)


def test_retreating_costs_twenty_stress_a_head():
    assert rules().combat_retreat_stress == pytest.approx(20.0)


def test_retreating_from_a_fight_can_fail():
    """70% base, +5% per further attempt — a retreat is not a free action, and
    the advice should never read as though it were."""
    assert rules().combat_retreat_chance == pytest.approx(0.7)
    assert rules().combat_retreat_bonus_chance_per_attempt == pytest.approx(0.05)


def test_a_fresh_recruit_costs_nothing_to_replace():
    """PLAYBOOK 0.1: hiring is free, so a rank-0 hero carries no sunk gold. A
    real capture has exactly this — a rank 0/0 Man-at-Arms."""
    assert hero_investment(GAME_DIR, "man_at_arms", 0, 0, ()) == 0


def test_a_levelled_hero_carries_tens_of_thousands():
    costs = load_hero_costs(str(GAME_DIR), "crusader")
    assert costs.weapon == (750, 1750, 3000, 6000)
    sunk = hero_investment(GAME_DIR, "crusader", 4, 4, list(costs.skills)[:4])
    assert sunk == 36000


def test_a_death_dwarfs_a_full_party_retreat():
    """The quantitative core of PLAYBOOK 2.6: ~1,800 gold to pull four heroes
    out against ~36,000 to lose one levelled hero. Retreat is rare because
    stress is cheap, and decisive because heroes are not."""
    retreat = 20.0 * 4 * gold_per_stress(GAME_DIR, 0)
    death = hero_investment(GAME_DIR, "crusader", 4, 4,
                            list(load_hero_costs(str(GAME_DIR), "crusader").skills)[:4])
    assert death > 15 * retreat


# --- the quest reward ---

def test_the_reward_reads_off_the_raid_save():
    if not CAPTURE.exists():
        pytest.skip("capture missing")
    reward = read_quest_reward(CAPTURE)
    assert reward.gold == 3000
    assert ("deed", 3) in reward.heirlooms
    assert "evasion_incense" in reward.trinkets


def test_reward_items_are_keyed_by_slot_with_the_payload_inside():
    """Same shape trap as `trinkets.items`: reading the keys gives ['0','1'].
    A reader that trusts the keys reports a reward of nothing."""
    if not CAPTURE.exists():
        pytest.skip("capture missing")
    assert read_quest_reward(CAPTURE).gold > 0


def test_a_missing_reward_is_empty_not_an_error():
    assert read_quest_reward(REPO / "nope.json").gold == 0


# --- the decision ---

def test_certain_death_says_retreat():
    heroes = [_Hero("crusader", 4, 4, ("smite", "stunning_blow")) for _ in range(4)]
    result = _Result(mean_deaths=1.0, mean_final_stress=200.0, completion_rate=0.5)
    advice = advise_retreat(heroes, result, QuestReward(gold=3000))
    assert advice.retreat
    assert advice.expected_death_gold > advice.cost_of_retreating


def test_a_safe_run_with_a_reward_says_push_on():
    heroes = [_Hero("crusader", 2, 2) for _ in range(4)]
    result = _Result(mean_deaths=0.0, mean_final_stress=0.0, completion_rate=1.0)
    advice = advise_retreat(heroes, result, QuestReward(gold=3000))
    assert not advice.retreat
    assert advice.cost_of_continuing < 0  # it pays


def test_losing_a_rank_zero_party_is_not_worth_retreating_over():
    """The dual of PLAYBOOK 0.1. Same death rate, disposable heroes: the bill
    changes sign, which a probability threshold could never express.

    **Two things are held neutral so this tests the DEATH term and only it.**
    `mean_final_stress` is 0 against a party carrying 0, because stress is a
    party bill and 100 points a head across four heroes is 8,880 gold — which
    swamps a fresh party's death term and would flip this test for a reason
    that has nothing to do with what it is named after. And `mean_deaths` is
    0.3, under `FLOOR_MEAN_DEATHS`, because at 1.0 the survival floor retreats
    both arms and there is no sign to observe.

    Before 2026-09-03 this passed at `mean_final_stress=100.0` — because a
    units bug priced the stress ahead at exactly zero. It was testing less than
    it claimed to.
    """
    levelled = [_Hero("crusader", 4, 4, ("smite", "stunning_blow")) for _ in range(4)]
    fresh = [_Hero("crusader", 0, 0) for _ in range(4)]
    result = _Result(mean_deaths=0.3, mean_final_stress=0.0, completion_rate=0.5)
    assert advise_retreat(levelled, result, QuestReward(gold=3000)).retreat
    assert not advise_retreat(fresh, result, QuestReward(gold=3000)).retreat


# --- the stress units, and the bug they hid ---



def test_current_stress_is_a_per_hero_MEAN():
    """The explicit parameter has to agree with the fallback, or a caller that
    passes it gets a different answer from one that does not."""
    heroes = [_Hero("crusader", 0, 0, stress=75.0) for _ in range(4)]
    result = _Result(mean_deaths=0.0, mean_final_stress=150.0, completion_rate=1.0)
    implicit = advise_retreat(heroes, result, QuestReward(gold=3000))
    explicit = advise_retreat(heroes, result, QuestReward(gold=3000), current_stress=75.0)
    assert implicit.expected_stress_gold == pytest.approx(explicit.expected_stress_gold)


# --- the survival floor ---

def test_the_survival_floor_overrides_the_gold():
    """The scene from `bat/play.log` at 13:23:03: ten combats left, the rollout
    forecasting 3.8 deaths, and the gold arithmetic calling it a net GAIN of
    375 gold because a fresh party is cheap. Fran's rule — never continue when
    the run is more likely than not to cost somebody — has to win here.

    UNSCALED, explicitly: since 2026-09-23 the default scales the floor by
    investment, and this party of free recruits is exactly who it spares.
    This test is about the floor's mechanism, not that default.
    """
    fresh = [_Hero("crusader", 0, 0) for _ in range(4)]
    result = _Result(mean_deaths=3.8, mean_final_stress=0.0, completion_rate=1.0,
                     zero_death_rate=0.0)
    advice = advise_retreat(fresh, result, QuestReward(gold=3000),
                            scale_floor_by_investment=False)
    assert advice.retreat
    assert advice.decided_by == "floor"
    # ...and the gold genuinely did favour staying, or this proves nothing.
    assert advice.cost_of_continuing < advice.cost_of_retreating
    assert "floor" in advice.reason


def test_the_floor_reads_P_A_DEATH_when_the_rollout_supplies_it():
    """`zero_death_rate` is the exact quantity; `mean_deaths` is an expected
    COUNT and only a proxy. A run that kills the whole party a third of the
    time and nobody otherwise has mean_deaths 1.33 but P(a death) 33% — under
    the floor, and the two readings disagree."""
    fresh = [_Hero("crusader", 0, 0) for _ in range(4)]
    result = _Result(mean_deaths=1.33, mean_final_stress=0.0, completion_rate=1.0,
                     zero_death_rate=0.67)
    advice = advise_retreat(fresh, result, QuestReward(gold=3000))
    assert advice.death_chance == pytest.approx(0.33)
    assert advice.decided_by == "gold"


def test_the_floor_stays_out_of_a_safe_run():
    fresh = [_Hero("crusader", 0, 0) for _ in range(4)]
    result = _Result(mean_deaths=0.0, mean_final_stress=0.0, completion_rate=1.0,
                     zero_death_rate=1.0)
    advice = advise_retreat(fresh, result, QuestReward(gold=3000))
    assert not advice.retreat
    assert advice.decided_by == "gold"
    assert advice.death_chance == pytest.approx(0.0)


def test_the_floor_is_named_only_when_it_CHANGED_the_answer():
    """A run the gold already refuses is decided by the gold. Reporting "floor"
    there would credit the override with a call it did not make."""
    levelled = [_Hero("crusader", 4, 4, ("smite", "stunning_blow")) for _ in range(4)]
    result = _Result(mean_deaths=2.0, mean_final_stress=0.0, completion_rate=0.5,
                     zero_death_rate=0.0)
    advice = advise_retreat(levelled, result, QuestReward(gold=3000))
    assert advice.retreat
    assert advice.decided_by == "gold"




def test_heirlooms_and_trinkets_are_reported_not_priced():
    """Heirlooms have no gold price and PLAYBOOK 1.2 values a trinket by whether
    a comp equips it. Folding either into the total would need an invented
    exchange rate."""
    heroes = [_Hero("crusader", 2, 2) for _ in range(4)]
    reward = QuestReward(gold=3000, heirlooms=(("deed", 3),), trinkets=("evasion_incense",))
    advice = advise_retreat(heroes, _Result(), reward)
    assert advice.forfeited_gold == 3000
    assert any("deed" in u for u in advice.unpriced)
    assert any("evasion_incense" in u for u in advice.unpriced)


def test_the_party_investment_is_reported_per_hero():
    heroes = [_Hero("crusader", 4, 4, ("smite",)), _Hero("vestal", 0, 0)]
    sunk = dict(party_investment(heroes))
    assert sunk["vestal"] == 0
    assert sunk["crusader"] > 0


def test_every_advice_renders():
    heroes = [_Hero("crusader", 2, 2) for _ in range(4)]
    for deaths in (0.0, 0.5, 2.0):
        advice = advise_retreat(heroes, _Result(mean_deaths=deaths, mean_final_stress=120.0),
                                QuestReward(gold=3000))
        assert advice.line() and advice.report()


# --- the module-shadowing guard ---

def test_the_bridge_has_no_module_that_shadows_a_real_package():
    """`bridge/torch.py` shadowed the actual `torch` package — running any
    script inside `bridge/` puts that directory first on `sys.path`, and
    `retreat.py` died with "'torch' is not a package" the moment it loaded a
    checkpoint. Renamed to `torchlight.py`; this stops it coming back, and
    catches the same mistake for other names."""
    shadowed = {"torch", "types", "json", "random", "copy", "queue", "select",
                "signal", "socket", "string", "time", "abc", "io", "os", "sys",
                "math", "csv", "glob", "struct", "logging", "operator", "enum"}
    for path in (REPO / "bridge").glob("*.py"):
        assert path.stem not in shadowed, path


if __name__ == "__main__":
    import pytest as _p
    raise SystemExit(_p.main([__file__, "-v"]))


# --- the floor, scaled by what is actually at risk ---

def test_the_floor_is_SCALED_by_default_on_Frans_ruling():
    """Shipped off in 2026-09-03 as contested; Fran ruled 2026-09-23 that
    level-0 heroes are expendable and a broke campaign prefers deaths to
    retreats. So by default a free party is not floored, and the gold decides."""
    fresh = [_Hero("crusader", 0, 0) for _ in range(4)]
    result = _Result(mean_deaths=3.8, mean_final_stress=0.0, completion_rate=1.0,
                     zero_death_rate=0.0)
    default = advise_retreat(fresh, result, QuestReward(gold=3000))
    explicit = advise_retreat(fresh, result, QuestReward(gold=3000),
                              scale_floor_by_investment=True)
    assert default == explicit
    assert default.decided_by == "gold"


def test_the_loot_still_on_the_map_counts_for_staying():
    """Leaving forfeits the map's loot as well as the reward (2026-09-23)."""
    fresh = [_Hero("crusader", 0, 0) for _ in range(4)]
    result = _Result(mean_deaths=0.5, mean_final_stress=0.0, completion_rate=0.5,
                     zero_death_rate=0.6)
    without = advise_retreat(fresh, result, QuestReward(gold=3000))
    with_loot = advise_retreat(fresh, result, QuestReward(gold=3000),
                               loot_ahead_gold=1000)
    assert with_loot.cost_of_continuing == pytest.approx(
        without.cost_of_continuing - 500)


def test_scaled_the_floor_STANDS_ASIDE_for_a_party_nobody_paid_for():
    """Fran's Shambler raid, 2026-09-03. A level-1 party fought a Shambler,
    the Highwayman died, and he took `ancestors_scroll` -- `limit=1`, one of a
    kind -- off the fight. His verdict: "very much worth it".

    The unscaled floor refuses that unconditionally, and `advise_retreat`
    cannot see the trinket at all because trinkets are deliberately unpriced.
    Scaled, the floor stands aside for heroes worth less than the quest, and
    the gold arithmetic -- which is RIGHT about a free recruit -- decides."""
    fresh = [_Hero("crusader", 0, 0) for _ in range(4)]
    result = _Result(mean_deaths=3.8, mean_final_stress=0.0, completion_rate=1.0,
                     zero_death_rate=0.0)
    assert dict(party_investment(fresh))["crusader"] == 0, "the premise: free heroes"

    scaled = advise_retreat(fresh, result, QuestReward(gold=3000),
                            scale_floor_by_investment=True)
    assert not scaled.retreat
    assert scaled.decided_by == "gold"


def test_scaled_the_floor_STILL_FIRES_for_a_party_that_cost_something():
    """The other half, or the flag would just be "delete the floor". Same
    forecast, same quest, heroes with real ranks behind them."""
    levelled = [_Hero("crusader", 4, 4, ("smite", "stunning_blow")) for _ in range(4)]
    result = _Result(mean_deaths=3.8, mean_final_stress=0.0, completion_rate=1.0,
                     zero_death_rate=0.0)
    sunk = dict(party_investment(levelled))["crusader"]
    assert sunk > 3000, "the premise: worth more than the quest"

    scaled = advise_retreat(levelled, result, QuestReward(gold=3000),
                            scale_floor_by_investment=True)
    assert scaled.retreat


def test_the_scaled_threshold_is_DERIVED_not_a_new_constant():
    """`mean_sunk > forfeited` -- a hero against the quest being risked for
    them. Both sides are already in gold from shipped files, so the flag adds
    no number to tune. Crossing the quest reward is what flips it, and nothing
    else changes between these two calls."""
    party = [_Hero("crusader", 2, 2) for _ in range(4)]
    sunk = dict(party_investment(party))["crusader"]
    result = _Result(mean_deaths=3.8, mean_final_stress=0.0, completion_rate=1.0,
                     zero_death_rate=0.0)

    cheap_quest = advise_retreat(party, result, QuestReward(gold=sunk - 1),
                                 scale_floor_by_investment=True)
    rich_quest = advise_retreat(party, result, QuestReward(gold=sunk + 1),
                                scale_floor_by_investment=True)
    # Worth more than the quest -> protected. Worth less -> the gold decides.
    assert cheap_quest.retreat
    assert rich_quest.decided_by == "gold"

def test_a_FREE_partys_stress_costs_nothing_so_it_pushes_on():
    """2026-09-23, Fran: "the model has been retreating all the time". A
    level-0 party (0 gold sunk) at ~60 stress, forecast to end near 150: the
    flat 22 gold/stress priced staying at ~8,000 against leaving at 4,778, and
    it retreated from every fight. Their stress is not worth treating, so it
    costs nothing, and the quest decides."""
    party = [_Hero("crusader", 0, 0, stress=60.0) for _ in range(4)]
    result = _Result(mean_deaths=1.0, mean_final_stress=150.0,
                     completion_rate=0.6, zero_death_rate=0.3)
    advice = advise_retreat(party, result, QuestReward(gold=3000),
                            loot_ahead_gold=1000)
    assert advice.expected_stress_gold == 0.0
    assert advice.retreat_stress_gold == 0.0
    assert not advice.retreat


def test_stress_is_NOT_billed_in_gold_on_either_side():
    """Fran, 2026-09-23: only survival matters for stress, and the rollout
    already carries it in `mean_deaths`. A heavily stressed party heading
    for more stress pays nothing for it on either bill."""
    heroes = [_Hero("crusader", 2, 2, stress=150.0) for _ in range(4)]
    result = _Result(mean_deaths=0.0, mean_final_stress=199.0,
                     completion_rate=1.0, zero_death_rate=1.0)
    advice = advise_retreat(heroes, result, QuestReward(gold=3000))
    assert advice.retreat_stress_gold == 0.0
    assert advice.expected_stress_gold == 0.0


def test_a_death_costs_at_least_the_heros_share_of_the_quest():
    """Never steer toward losing a hero: a free recruit's death is priced at
    their share of the reward, and the living alone share it."""
    heroes = [_Hero("crusader", 0, 0, hp=0.0)] + [_Hero("crusader", 0, 0)
                                                  for _ in range(3)]
    result = _Result(mean_deaths=1.0, mean_final_stress=0.0,
                     completion_rate=1.0, zero_death_rate=0.0)
    advice = advise_retreat(heroes, result, QuestReward(gold=3000))
    assert advice.expected_death_gold == pytest.approx(1000.0)

def test_the_reward_is_not_counted_twice_so_a_lone_survivor_leaves():
    """Live 2026-09-23: down to one Vestal, the bill read "leaving ~3,000,
    staying ~3,000 -- push on", because leaving was charged the whole reward
    while staying already credited it at its odds. Leaving costs nothing
    extra; a lone hero with poor odds leaves, a sound party stays."""
    lone = [_Hero("vestal", 0, 0, stress=90.0)]
    grim = _Result(mean_deaths=1.0, mean_final_stress=0.0, completion_rate=0.1,
                   zero_death_rate=0.0)
    advice = advise_retreat(lone, grim, QuestReward(gold=3000))
    assert advice.cost_of_retreating == 0.0
    assert advice.retreat

    party = [_Hero("crusader", 0, 0) for _ in range(4)]
    sound = _Result(mean_deaths=1.4, mean_final_stress=0.0, completion_rate=0.6,
                    zero_death_rate=0.3)
    assert not advise_retreat(party, sound, QuestReward(gold=3000),
                              loot_ahead_gold=1300).retreat

