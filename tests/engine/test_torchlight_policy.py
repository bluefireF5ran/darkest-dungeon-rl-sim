"""Tests for the torch policy (`bridge/torchlight.py`) and the darkness-bracket
boundary it corrected in `CombatEngine`.

Three things are pinned here:

- **The loot columns of `darkness.range_table` are parsed.** They used to be
  dropped by `DarknessBand` as "dungeon mechanics with no analog", which is what
  made the shutdown look like a decision needing an invented model. It is not:
  the file prices darkness in both directions.
- **The brackets exclude their lower bound.** `darkness.lowest_excluded` is
  `true`, so torch 75 is *dim*, not radiant. The engine implemented the opposite
  while its own docstring listed the correct tiers.
- **The shutdown's cost is getting back to where you were**, not back to 100 —
  charging the full relight talks the policy out of a play that is nearly free
  for a party already in the dark.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from functools import lru_cache

import pytest

from bridge.torchlight import (
    RADIANT_FLOOR, SHUTDOWN_LEVEL, TORCH_RESTORE, advise_from_live, advise_torch,
    band_for, describe, torches_to_restore,
)
from data.rules import RulesLoader

from data.paths import GAME_DIR


@lru_cache(maxsize=1)
def rules():
    return RulesLoader.load(GAME_DIR / "shared" / "rules.json")


# --- the data that makes this a policy and not a guess ---

def test_the_darkness_table_prices_loot():
    """Five brackets, and the darkest pays +30% gold and +12 treasure draws.
    Without these columns the shutdown cannot be evaluated at all."""
    table = rules().darkness_table
    assert len(table) == 5
    darkest = table[-1]
    assert darkest.loot_increase_gold == pytest.approx(30.0)
    assert darkest.loot_increase_treasure_draws == pytest.approx(12.0)


def test_the_brightest_bracket_pays_no_loot_and_costs_nothing():
    brightest = band_for(rules(), 100.0)
    assert brightest.loot_increase_gold == 0
    assert brightest.monster_attack_increase == 0
    assert brightest.stress_damage_increase == 0
    assert brightest.player_scouting_increase > 0


def test_darkness_is_priced_in_both_directions():
    """Every bracket that pays loot also hands the monsters something — which
    is exactly why the shutdown is only correct on the turn that ends a fight."""
    for band in rules().darkness_table:
        if band.loot_increase_gold > 0:
            assert band.monster_attack_increase > 0
            assert band.monster_damage_increase > 0


def test_camping_restores_the_torch_outright():
    """`camp_restore_torch` is 100, which makes a shutdown on the fight before a
    campfire free. PLAYBOOK 2.3 does not mention this case."""
    assert rules().camp_restore_torch == pytest.approx(100.0)


# --- the boundary the engine had backwards ---

def test_seventy_five_is_dim_not_radiant():
    """`darkness.lowest_excluded` is true, so the tiers are 76-100 / 51-75 /
    26-50 / 0-25. The engine used to put 75 in the radiant band while its own
    docstring listed these tiers."""
    assert describe(rules(), 75.0) == "dim"
    assert describe(rules(), 76.0) == "radiant"


def test_the_engine_agrees_with_the_bridge_at_every_boundary():
    from engine.combat_engine import CombatEngine
    from engine.rng import SeededRNG

    class _Store:
        pass
    store = _Store()
    store.rules = rules()
    engine = CombatEngine.__new__(CombatEngine)
    engine.rules = rules()
    for torch in (100.0, 76.0, 75.0, 51.0, 50.0, 26.0, 25.0, 1.0, 0.0, -50.0):
        engine.torch_level = torch
        assert engine._get_darkness_band() is band_for(rules(), torch), torch


def test_zero_falls_in_the_darkest_bracket():
    """Shift-clicking the bar lands on 0, and that must be the +30% bracket —
    a lower-inclusive reading would put it in +15% instead."""
    assert band_for(rules(), 0.0).loot_increase_gold == pytest.approx(30.0)


# --- cost ---

def test_four_torches_take_a_dark_run_back_to_full():
    """The expert-measured constant, and the only number here that is not from
    a shipped file."""
    assert torches_to_restore(0.0, 100.0) == 4
    assert TORCH_RESTORE == 25.0


def test_the_cost_is_returning_to_where_you_were():
    """A party already at 25 pays ONE torch to undo a shutdown, not four.
    Charging the full relight talks it out of a nearly free play."""
    advice = advise_torch(rules(), torch=25.0, torches_held=2)
    # Priced at 1 torch -- and since PLAYBOOK 7.2 a PAID shutdown is held.
    assert advice.action == "hold"
    assert advice.torches_needed == 1


def test_a_shutdown_is_refused_when_the_torches_are_not_there():
    advice = advise_torch(rules(), torch=25.0, torches_held=0)
    assert advice.action == "hold"


# --- the policy ---

def test_the_shutdown_is_only_offered_on_the_turn_that_ends_the_fight():
    """Darkness hands the monsters +12.5 accuracy, +25% damage and +5 crit. It
    is free only once the fight is already won."""
    # On the run's last fight (free), so PLAYBOOK 7.2 does not hold it.
    mid = advise_torch(rules(), torch=100.0, torches_held=8, about_to_win=False,
                       last_combat_of_run=True)
    end = advise_torch(rules(), torch=100.0, torches_held=8, about_to_win=True,
                       last_combat_of_run=True)
    assert mid.action == "hold"
    assert end.action == "shutdown"


def test_the_runs_last_fight_is_always_worth_it():
    """PLAYBOOK 2.3: there is nothing left to walk through, so the light never
    has to be bought back — it works with zero torches in the pack."""
    advice = advise_torch(rules(), torch=100.0, torches_held=0, last_combat_of_run=True)
    assert advice.action == "shutdown"
    assert advice.torches_needed == 0
    assert "last fight" in advice.free_because


def test_a_campfire_next_makes_it_free_too():
    advice = advise_torch(rules(), torch=100.0, torches_held=0, camp_next=True)
    assert advice.action == "shutdown"
    assert advice.torches_needed == 0
    assert "campfire" in advice.free_because


def test_already_at_the_bottom_there_is_nothing_left_to_buy():
    advice = advise_torch(rules(), torch=0.0, torches_held=4)
    assert advice.action == "hold"
    assert "darkest bracket" in advice.reason


def test_dark_mid_fight_with_torches_in_hand_says_relight():
    """Sitting in the dark before the fight is won is a straight loss: the
    monster bonuses are being paid for loot not yet earned."""
    advice = advise_torch(rules(), torch=25.0, torches_held=3, about_to_win=False)
    assert advice.action == "relight"
    assert advice.torches_needed == torches_to_restore(25.0)


def test_the_gain_reported_is_marginal_not_absolute():
    """From 25 the shutdown adds +15% gold, not +30% — the party is already
    collecting the first half."""
    from_full = advise_torch(rules(), torch=100.0, torches_held=8)
    from_dark = advise_torch(rules(), torch=25.0, torches_held=8)
    assert from_full.gold_bonus == pytest.approx(30.0)
    assert from_dark.gold_bonus == pytest.approx(15.0)


def test_radiant_is_the_documented_default():
    assert RADIANT_FLOOR == 76.0
    assert SHUTDOWN_LEVEL == 0.0
    assert describe(rules(), RADIANT_FLOOR) == "radiant"


def test_every_advice_renders():
    for torch in (100.0, 75.0, 40.0, 10.0, 0.0):
        for held in (0, 4):
            for won in (True, False):
                advice = advise_torch(rules(), torch=torch, torches_held=held,
                                      about_to_win=won)
                assert advice.line() and advice.report()


# --- "the last fight" is not "the run is over" ---
#
# Found in the first live run with the whole loop attached: one fight left but
# five corridors and eight unexplored areas still ahead, and the advice read
# "the run's last fight — there is nothing left to walk through", pricing the
# relight at 0 torches. Walking five corridors pitch black is not free.

class _State:
    """Just the fields `advise_from_live` reads."""
    def __init__(self, torch=100.0, torches=14, remaining=1):
        self.torchlight = torch
        self.provisions = {"torch": torches}
        self.remaining_battles = remaining


def test_one_fight_left_is_not_a_free_shutdown_while_there_is_dungeon_to_walk():
    advice = advise_from_live(_State(), rules(), moves_left=5)
    # Not free -- and since PLAYBOOK 7.2 (max light while torches allow) a
    # paid shutdown is held rather than bought.
    assert advice.action == "hold"
    assert not advice.free_because
    assert advice.torches_needed > 0
    assert "nothing left to walk through" not in advice.report()


def test_the_last_fight_with_nowhere_left_to_go_IS_free():
    advice = advise_from_live(_State(), rules(), moves_left=0)
    assert advice.action == "shutdown"
    assert advice.free_because and advice.torches_needed == 0


def test_an_unknown_route_never_claims_the_shutdown_is_free():
    """Without the route the two cases are indistinguishable, so it charges the
    relight — conservative in the direction that cannot mislead."""
    assert not advise_from_live(_State(), rules()).free_because


def test_more_fights_left_is_never_free_however_short_the_walk():
    assert not advise_from_live(_State(remaining=4), rules(),
                                moves_left=0).free_because


def test_the_shutdown_is_free_when_no_FIGHT_follows_it():
    """Priced in torches, the tutorial can never snuff: the Old Road hands out
    none at all. Priced in fights, it can -- a relight buys light for a
    battle, and with no battle left there is nothing to buy.

    PLAYBOOK 2.3: *"en el ultimo combate de la mazmorra siempre compensa"*.
    """
    table = rules()
    assert advise_torch(table, 94.0, 0, battles_left=0).action == "shutdown"
    assert advise_torch(table, 94.0, 0, battles_left=1).action == "hold"
    # Unknown keeps the old torch-budget reasoning rather than assuming the
    # best case, so nothing that does not pass it changes behaviour.
    assert advise_torch(table, 94.0, 0).action == "hold"
    # Paid (a fight still follows): held since PLAYBOOK 7.2.
    assert advise_torch(table, 94.0, 4, battles_left=1).action == "hold"


def test_the_free_reason_says_which_rule_made_it_free():
    """Three different rules can make a shutdown free and they are not the
    same claim -- "nothing left to walk through" is a stronger statement than
    "no fight left", and the first was being printed for the second."""
    table = rules()
    last = advise_torch(table, 94.0, 0, battles_left=0)
    assert "no fight left after this one" in last.free_because
    run_end = advise_torch(table, 94.0, 0, last_combat_of_run=True)
    assert "nothing left to walk through" in run_end.free_because


if __name__ == "__main__":
    import pytest as _p
    raise SystemExit(_p.main([__file__, "-v"]))
