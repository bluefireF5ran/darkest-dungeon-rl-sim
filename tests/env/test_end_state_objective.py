"""Ending a combat WELL is the objective, and abandoning is a real option.

Fran, 2026-08-24: "right now we need to finish combats without losing heroes,
and finish combats with high hp and low stress values to not be ambushed by the
next combat so a surprise doesn't mean losing it... If the model is just
focusing on progressing, it wont reach far."

The old constants said the opposite out loud. A 15-combat run paid
`15 x 8 + 10 = 130` and a hero death cost `-20`, so losing a hero cost 15% of a
run and marching on with three heroes strictly beat turning back. The measured
consequence was the entire long-band failure mode: the first death lands 48% of
the way through, 2.07 more follow it, and 81% of wipes happen two to three
combats LATER rather than in the fight that took the hero.

Three changes, pinned here:
  * `win_reward()` scales with the state the party finishes in;
  * `REWARD_HERO_DEATH` is -50, so a death is worth most of a run;
  * `ACTION_ABANDON` exists, so a losing run can be walked away from.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests._fixtures import get_store

from env.expedition_env import build_comp_pool
from env.expedition_sequence_env import (ABANDON_HP_FRACTION,
                                         ABANDON_STRESS,
                                         COMBAT_END_CONDITION_FLOOR,
                                         END_STRESS_SCALE, REWARD_HERO_DEATH,
                                         REWARD_EXPEDITION_COMPLETE,
                                         W_END_HP, W_END_STRESS,
                                         ExpeditionSequenceEnv)
from env.gym_env import (ACTION_ABANDON, ACTION_PASS, ACTION_RETREAT,
                         ACTION_SPACE_SIZE, DarkestDungeonEnv)

store = get_store()
_POOL = sorted(build_comp_pool(store), key=lambda c: c[0])
_COMP_NAME, _ZONE, _HERO_IDS = _POOL[0]


def _env(tier=5, band="long", combats=15, seed=0):
    e = ExpeditionSequenceEnv(store=store, sampler_seed=seed)
    e.reset(seed=seed, options={
        "hero_ids": _HERO_IDS, "comp_name": _COMP_NAME, "zone_id": _ZONE,
        "tier": tier, "length_band": band, "num_combats": combats})
    return e


# -- The end-state objective --

def test_the_weights_are_a_convex_combination():
    """Otherwise `combat_end_condition` is not in [0, 1] and the floor stops
    meaning what its name says."""
    assert abs(W_END_HP + W_END_STRESS - 1.0) < 1e-9
    assert 0.0 < COMBAT_END_CONDITION_FLOOR < 1.0
    print("  PASS test_the_weights_are_a_convex_combination")


def test_a_pristine_party_scores_one_and_a_wrecked_one_scores_low():
    e = _env()
    for h in e.engine.heroes:
        h.hp, h.stress, h.is_deaths_door = h.max_hp, 0.0, False
    assert e.combat_end_condition() == 1.0
    assert e.win_reward() == e._reward_win
    for h in e.engine.heroes:
        h.hp, h.stress = 1, END_STRESS_SCALE
    assert e.combat_end_condition() < 0.1
    print("  PASS test_a_pristine_party_scores_one_and_a_wrecked_one_scores_low")


def test_winning_ragged_still_beats_losing():
    """The floor exists for this. A win the party barely survived is still a
    win, and must never be worth less than the loss reward."""
    e = _env()
    for h in e.engine.heroes:
        h.hp, h.stress = 1, 200.0
    assert e.win_reward() >= COMBAT_END_CONDITION_FLOOR * e._reward_win
    assert e.win_reward() > e._reward_lose
    print(f"  PASS test_winning_ragged_still_beats_losing "
          f"({e.win_reward():.2f} vs lose {e._reward_lose})")


def test_stress_and_hp_BOTH_move_the_payout():
    """Fran named both. A model that only watched HP would happily finish every
    fight at 100% HP and 99 stress, which is one Resolve Check from an
    affliction and exactly the state an ambush punishes."""
    e = _env()
    for h in e.engine.heroes:
        h.hp, h.stress, h.is_deaths_door = h.max_hp, 0.0, False
    full = e.win_reward()
    for h in e.engine.heroes:
        h.stress = END_STRESS_SCALE
    stressed = e.win_reward()
    for h in e.engine.heroes:
        h.stress = 0.0
        h.hp = max(1, h.max_hp // 2)
    hurt = e.win_reward()
    assert stressed < full, "stress did not lower the payout"
    assert hurt < full, "HP did not lower the payout"
    print(f"  PASS test_stress_and_hp_BOTH_move_the_payout "
          f"(full {full:.2f}, stressed {stressed:.2f}, hurt {hurt:.2f})")


def test_deaths_door_counts_as_zero_hp():
    """A hero one hit from dying is not a hero the party can take into an
    ambush, whatever the HP bar says."""
    e = _env()
    for h in e.engine.heroes:
        h.hp, h.stress, h.is_deaths_door = h.max_hp, 0.0, False
    before = e.combat_end_condition()
    e.engine.heroes[0].is_deaths_door = True
    assert e.combat_end_condition() < before
    print("  PASS test_deaths_door_counts_as_zero_hp")


def test_a_death_costs_more_than_completing_the_expedition():
    """The whole point of the reweighting. While a death cost -20 against a
    +10 completion and 15 x 8 of wins, marching on with a corpse was correct
    play and the policy learned it."""
    assert REWARD_HERO_DEATH <= -50.0
    assert abs(REWARD_HERO_DEATH) > REWARD_EXPEDITION_COMPLETE
    e = _env()
    clean_run = 15 * e._reward_win + REWARD_EXPEDITION_COMPLETE
    assert abs(REWARD_HERO_DEATH) > 0.25 * clean_run, (
        "a hero death is still cheap enough that pushing on dominates")
    print(f"  PASS test_a_death_costs_more_than_completing_the_expedition "
          f"(death {REWARD_HERO_DEATH} vs clean run {clean_run:.0f})")


# -- Abandoning --

def test_abandon_is_appended_and_nothing_below_it_moved():
    assert ACTION_PASS == 36
    assert ACTION_RETREAT == 37
    assert ACTION_ABANDON == 38
    assert ACTION_SPACE_SIZE == 39
    print("  PASS test_abandon_is_appended_and_nothing_below_it_moved")


def test_a_single_combat_env_never_offers_abandon():
    """There is no expedition to abandon, so the base env masks it off. If this
    ever flips, `DarkestDungeonEnv.step` would silently turn it into a pass."""
    e = DarkestDungeonEnv(store=store)
    e.reset(seed=0)
    assert not e._can_abandon()
    assert not bool(e.action_masks()[ACTION_ABANDON])
    print("  PASS test_a_single_combat_env_never_offers_abandon")


def _in_trouble(e):
    """Abandon is gated on the run having gone wrong -- see `_can_abandon`."""
    for h in e.engine.heroes:
        h.hp = max(1, int(h.max_hp * (ABANDON_HP_FRACTION - 0.1)))
    return e


def test_abandon_is_NOT_offered_to_a_healthy_party():
    """The gate that stopped the 400k probe's collapse: offered on every turn,
    the policy abandoned 75% of runs a median 12% of the way in, at 74% HP with
    zero deaths and not one wipe. A safe terminal worth ~0 beats playing on
    whenever the value estimate is negative, which it is early in training."""
    e = _env(combats=15)
    for h in e.engine.heroes:
        h.hp, h.stress = h.max_hp, 0.0
    assert not e._can_abandon()
    assert not bool(e.action_masks()[ACTION_ABANDON])
    print("  PASS test_abandon_is_NOT_offered_to_a_healthy_party")


def test_each_trouble_condition_opens_the_exit_on_its_own():
    e = _env(combats=15)
    for h in e.engine.heroes:
        h.hp, h.stress = h.max_hp, 0.0
    assert not e._can_abandon()
    # MEAN party stress, not any one hero: one hero at 120 while the rest are
    # fresh is a hero in trouble, not a run that has gone wrong.
    e.engine.heroes[0].stress = ABANDON_STRESS * 2
    assert not e._can_abandon(), "one stressed hero should not open the exit"
    for h in e.engine.heroes:
        h.stress = ABANDON_STRESS
    assert e._can_abandon(), "high party stress did not open the exit"
    for h in e.engine.heroes:
        h.stress = 0.0
        h.hp = max(1, int(h.max_hp * (ABANDON_HP_FRACTION - 0.1)))
    assert e._can_abandon(), "low HP did not open the exit"
    for h in e.engine.heroes:
        h.hp = h.max_hp
    assert not e._can_abandon()
    # `is_alive()` reads the `is_dead` FLAG, not hp -- setting hp to 0 leaves a
    # hero alive at 0 HP, which is a different state entirely.
    e.engine.heroes[0].is_dead = True
    assert e._can_abandon(), "a dead hero did not open the exit"
    print("  PASS test_each_trouble_condition_opens_the_exit_on_its_own")


def test_abandon_ends_the_episode_with_the_party_alive():
    e = _in_trouble(_env(combats=15))
    assert bool(e.action_masks()[ACTION_ABANDON])
    obs, reward, terminated, truncated, info = e.step(ACTION_ABANDON)
    assert terminated and not truncated
    assert info["winner"] == "abandoned"
    assert any(h.is_alive() for h in e.engine.heroes), (
        "abandoning killed the party")
    assert e._abandoned
    print("  PASS test_abandon_ends_the_episode_with_the_party_alive")


def test_abandoning_forfeits_the_completion_bonus():
    """Its price is entirely emergent, the same design retreat uses: what it
    costs is every remaining win plus the completion bonus, and what it buys is
    not paying REWARD_HERO_DEATH for the heroes that would have died."""
    e = _in_trouble(_env(combats=15))
    _, reward, _, _, _ = e.step(ACTION_ABANDON)
    assert reward < REWARD_EXPEDITION_COMPLETE, (
        "abandoning paid as much as completing the expedition")
    print(f"  PASS test_abandoning_forfeits_the_completion_bonus "
          f"(reward {reward:+.2f})")


def test_abandoning_is_better_than_wiping_and_worse_than_completing():
    """The ordering that makes it a real decision rather than a trap or a
    free exit. Compared on the SAME party, so only the ending differs."""
    e = _env(combats=15)
    for h in e.engine.heroes:
        h.hp, h.stress = max(1, h.max_hp // 3), 60.0
    assert e._can_abandon()
    _, abandon_r, _, _, _ = e.step(ACTION_ABANDON)
    # A wipe costs a death per hero on top of everything abandoning pays.
    wipe_r = abandon_r + REWARD_HERO_DEATH * len(e.engine.heroes)
    # Completing pays the bonus plus at least the floor on the last win.
    complete_r = abandon_r + REWARD_EXPEDITION_COMPLETE
    assert wipe_r < abandon_r < complete_r
    print(f"  PASS test_abandoning_is_better_than_wiping_and_worse_than_completing "
          f"(wipe {wipe_r:.1f} < abandon {abandon_r:.1f} < complete {complete_r:.1f})")


def test_abandoning_a_healthy_party_is_unreachable_AND_unpaid():
    """Belt and braces. The gate is what actually prevents the collapse, but
    the reward must not pay for it either -- if the mask ever loosens, a free
    exit would be found immediately.

    Phi is what enforces the second half without an invented constant: a
    terminal zeroes the potential, so a healthy party is charged for the health
    it walks away from, on top of forfeiting every remaining win.
    """
    e = _env(combats=15)
    for h in e.engine.heroes:
        h.hp, h.stress, h.is_deaths_door = h.max_hp, 0.0, False
    assert not e._can_abandon()
    _, reward, _, _, _ = e.step(ACTION_ABANDON)   # falls through to a pass
    assert reward <= 0.0, (
        f"abandoning a full-strength party PAID {reward:+.2f}; it is a free "
        "exit and the policy will find it")
    print(f"  PASS test_abandoning_a_healthy_party_is_unreachable_AND_unpaid "
          f"({reward:+.2f})")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
