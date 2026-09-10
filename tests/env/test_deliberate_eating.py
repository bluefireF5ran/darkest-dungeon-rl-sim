"""Food that a party can actually SPEND, and the districts that change it.

Until 2026-08-24 the involuntary hunger check was food's only consumer. A long
run ended with 18.5 of 36 rations unspent; a Champion run, where the Mill
switches hunger off entirely, ended with all of them. Measured over 24 long
runs taking 345 HP of damage, camp gave back 4.7% and food 4.7% against 48.4%
from in-combat healing -- which is what made a dedicated healer structurally
mandatory in this simulator and is the real source of the comp search's healer
clustering.

These pin the env wiring. `tests/data/test_districts.py` pins the loader.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests._fixtures import get_store

from data.district_loader import districts_at_tier, food_bonus, hunger_disabled
from env.expedition_env import build_comp_pool
from env.expedition_sequence_env import (DISTRICTS_ENABLED, EAT_TOPUP_FRACTION,
                                         FOOD_AMOUNTS, HUNGER_CHECK_CHANCE,
                                         HUNGER_STARVE_FRACTION,
                                         ExpeditionSequenceEnv)

store = get_store()

# `tier` in options is ONLY honoured on the explicit-scenario branch, i.e. when
# `hero_ids` is present -- otherwise reset() draws it from `_sampler_rng` and
# silently ignores what the caller asked for. That is a real trap: a test that
# passes `tier` alone gets a RANDOM tier and its district assertions then pass
# or fail by luck. Every helper below therefore pins a real comp.
_POOL = sorted(build_comp_pool(store), key=lambda c: c[0])
assert _POOL, "no trainable comps"
_COMP_NAME, _ZONE, _HERO_IDS = _POOL[0]


def _env(tier=5, band="long", combats=15, seed=0):
    e = ExpeditionSequenceEnv(store=store, sampler_seed=seed)
    e.reset(seed=seed, options={
        "hero_ids": _HERO_IDS, "comp_name": _COMP_NAME, "zone_id": _ZONE,
        "tier": tier, "length_band": band, "num_combats": combats})
    assert e._tier == tier, "the scenario did not pin the tier"
    return e


# -- The Granary --

def test_the_granary_adds_food_at_veteran_and_not_below():
    """+10, and only once a campaign could have built it."""
    assert DISTRICTS_ENABLED, "these tests describe the districts-on behaviour"
    bonus = food_bonus(store.districts, districts_at_tier(store.districts, 5))
    assert bonus == 10
    lo = _env(tier=2, band="long")
    hi = _env(tier=5, band="long")
    assert lo._provision_inventory["food"] == FOOD_AMOUNTS["long"]
    assert hi._provision_inventory["food"] == FOOD_AMOUNTS["long"] + bonus
    print(f"  PASS test_the_granary_adds_food_at_veteran_and_not_below "
          f"(+{bonus} at Champion, +0 at Apprentice)")


def test_the_granary_eat_bonus_does_NOT_touch_ordinary_healing():
    """`districts_granary_1` is `hp_heal_received_percent` with sub type
    'eat'. Reading the stat type alone -- which `_apply_buff_to_stats` did --
    turns a food bonus into +15% on a Vestal's heals. The sub type IS the
    mechanic, the same trap `combat_stat_add` sets."""
    granary = store.buffs["districts_granary_1"]
    assert granary.stat_type == "hp_heal_received_percent"
    assert granary.stat_sub_type == "eat"
    e = _env(tier=5)
    for h in e.engine.heroes:
        assert h.eat_heal_received_mult > 1.0, "the eat bonus did not land"
        assert h.hp_heal_received_mult == 1.0, (
            "the Granary leaked into general healing -- the sub type is not "
            "being read")
    low = _env(tier=2)
    for h in low.engine.heroes:
        assert h.eat_heal_received_mult == 1.0
    print("  PASS test_the_granary_eat_bonus_does_NOT_touch_ordinary_healing")


# -- The Mill --

def test_the_mill_disables_hunger_at_champion_only():
    at3 = districts_at_tier(store.districts, 3)
    at5 = districts_at_tier(store.districts, 5)
    assert not hunger_disabled(store.districts, at3)
    assert hunger_disabled(store.districts, at5)
    assert not _env(tier=3)._built_districts() >= {"the_mill"}
    assert "the_mill" in _env(tier=5)._built_districts()
    print("  PASS test_the_mill_disables_hunger_at_champion_only")


def test_no_hunger_roll_is_DRAWN_when_hunger_is_off():
    """The `dot_irresistible` precedent: skip the draw, do not draw and
    discard. Rolling when the roll cannot matter desynchronises every trace
    comparison after it."""
    e = _env(tier=5, combats=4)
    before = e.engine.rng.calls if hasattr(e.engine.rng, "calls") else None
    e._snapshot_and_advance_roster(completed=True)
    assert e._hunger_checks == 0, "the Mill did not switch the check off"
    if before is not None:
        # Eating draws nothing either, so the stream must be untouched by the
        # hunger machinery specifically.
        assert isinstance(e.engine.rng.calls, int)
    print("  PASS test_no_hunger_roll_is_DRAWN_when_hunger_is_off")


# -- Eating --

def test_eating_spends_food_and_heals():
    e = _env(tier=5, combats=15)
    for h in e.engine.heroes:
        h.hp = max(1, h.max_hp // 3)
    food_before = e._provision_inventory["food"]
    hp_before = sum(h.hp for h in e.engine.heroes if h.is_alive())
    meals = e._deliberate_eat()
    assert meals > 0, "a party at a third HP with 46 food ate nothing"
    alive = sum(1 for h in e.engine.heroes if h.is_alive())
    assert e._provision_inventory["food"] == food_before - meals * alive, (
        "a meal must cost one food PER HERO")
    assert sum(h.hp for h in e.engine.heroes if h.is_alive()) > hp_before
    print(f"  PASS test_eating_spends_food_and_heals ({meals} meals, "
          f"{meals * alive} food)")


def test_a_full_party_does_not_eat():
    """Food is the scarcest recovery resource in the game; burning it on a
    hero who cannot benefit is the same waste `_is_wasted_heal` refuses."""
    e = _env(tier=5)
    for h in e.engine.heroes:
        h.hp = h.max_hp
    food_before = e._provision_inventory["food"]
    assert e._deliberate_eat() == 0
    assert e._provision_inventory["food"] == food_before
    print("  PASS test_a_full_party_does_not_eat")


def test_eating_stops_at_the_topup_threshold():
    e = _env(tier=5)
    for h in e.engine.heroes:
        h.hp = max(1, h.max_hp // 4)
    e._deliberate_eat()
    alive = [h for h in e.engine.heroes if h.is_alive()]
    assert alive
    # Three legal exits: every hero at or above the threshold, the ration
    # gone, or the stop's own cap (max_provisions_before_full) reached.
    stopped_full = all(h.hp >= EAT_TOPUP_FRACTION * h.max_hp for h in alive)
    stopped_broke = e._provision_inventory["food"] < len(alive)
    stopped_cap = e._meals_eaten >= int(e.store.rules.max_provisions_before_full)
    assert stopped_full or stopped_broke or stopped_cap
    print(f"  PASS test_eating_stops_at_the_topup_threshold "
          f"(topped_up={stopped_full}, out_of_food={stopped_broke}, "
          f"at_cap={stopped_cap})")


def test_eating_RESERVES_food_for_the_hunger_checks_it_can_still_face():
    """Starving costs 20% max HP against the 5% a meal restores -- four meals
    wasted to buy one. So eating must never be able to CAUSE a starvation.

    Veteran, where the Mill is not built and the checks are still live.
    """
    assert HUNGER_STARVE_FRACTION == 4 * 0.05
    e = _env(tier=3, band="long", combats=15)
    for h in e.engine.heroes:
        h.hp = 1
    e._deliberate_eat()
    alive = sum(1 for h in e.engine.heroes if h.is_alive())
    remaining = max(0, e._num_combats - e._combat_idx - 1)
    reserve = int(round(remaining * HUNGER_CHECK_CHANCE * alive))
    assert e._provision_inventory["food"] >= reserve, (
        f"ate into the reserve: {e._provision_inventory['food']} left, "
        f"{reserve} needed for {remaining} remaining combats")
    print(f"  PASS test_eating_RESERVES_food_for_the_hunger_checks_it_can_still_face "
          f"(kept {e._provision_inventory['food']}, reserve {reserve})")


def test_at_champion_there_is_no_reserve_because_there_are_no_checks():
    """The Mill's whole value: it converts a forced ration into a spendable
    one. Fran's point, and the measurement agrees -- full+eat 36.1% against
    buffs+eat 27.8%."""
    e = _env(tier=5, band="long", combats=15)
    alive = sum(1 for h in e.engine.heroes if h.is_alive())
    # Across stops, since one stop is capped at max_provisions_before_full.
    # Nothing may be held back: the Mill means no check can ever demand it.
    for _ in range(40):
        for h in e.engine.heroes:
            h.hp = 1
        if e._deliberate_eat() == 0:
            break
    assert e._provision_inventory["food"] < alive, (
        "food was reserved at Champion, where no hunger check can fire")
    print("  PASS test_at_champion_there_is_no_reserve_because_there_are_no_checks")


def test_one_stop_is_capped_by_the_GAMES_number_not_an_invented_rate():
    """`rules.json max_provisions_before_full` (4) is per hero, and a meal here
    feeds every living hero one food, so it bounds the meals at one stop.

    This replaced an invented `FOOD_ALLOWANCE_PER_COMBAT = 2.0` rate on
    2026-08-24. The rate blocked 82.8% of all refused meal-stops at Champion,
    46 of them with the party under 75% HP, while the run ended carrying 26.6
    of its 46 food -- and paired on one checkpoint, removing it moved the wipe
    rate 46.0% -> 24.0%. Do not reintroduce a pacing constant here without a
    shipped key to hang it on."""
    e = _env(tier=5, band="long", combats=15)
    cap = int(e.store.rules.max_provisions_before_full)
    assert cap == 4, "the shipped rules.json value moved; re-read the key"
    for h in e.engine.heroes:
        h.hp = 1                     # so EAT_TOPUP_FRACTION can never bind
    alive = sum(1 for h in e.engine.heroes if h.is_alive())
    e._provision_inventory["food"] = 400   # so the ration can never bind
    meals = e._deliberate_eat()
    assert meals == cap, f"one stop ate {meals} meals against a cap of {cap}"
    assert e._provision_inventory["food"] == 400 - cap * alive


def test_a_hurt_party_at_champion_is_never_refused_by_a_rate():
    """The failure the rate limit caused: a party below the 85% HP that the
    first-death probe says separates a survivable fight from a fatal one,
    holding food, and refused a meal. Champion has no hunger check, so nothing
    may be reserved and nothing may be paced."""
    e = _env(tier=5, band="long", combats=15)
    for h in e.engine.heroes:
        h.hp = max(1, int(0.60 * h.max_hp))
    assert e._provision_inventory["food"] > 0
    assert e._deliberate_eat() > 0, "a hurt party with food in the pack did not eat"
    alive = [h for h in e.engine.heroes if h.is_alive()]
    frac = sum(h.hp for h in alive) / sum(h.max_hp for h in alive)
    assert frac > 0.60, "eating did not raise the party's HP"


def test_a_retreat_does_not_feed_the_party():
    """`completed=False` is a retreat: the party is standing in the corridor it
    fled into. Neither between-rooms recovery has happened, and letting one
    happen would make fleeing a way to BUY recovery."""
    e = _env(tier=5, combats=15)
    for h in e.engine.heroes:
        h.hp = max(1, h.max_hp // 3)
    food_before = e._provision_inventory["food"]
    meals_before = e._meals_eaten
    e._snapshot_and_advance_roster(completed=False)
    assert e._provision_inventory["food"] == food_before
    assert e._meals_eaten == meals_before
    print("  PASS test_a_retreat_does_not_feed_the_party")


# -- District buffs as persistent equipment --

def test_district_buffs_reach_the_heroes_at_veteran_and_not_below():
    lo, hi = _env(tier=2), _env(tier=3)
    assert lo._built_districts() == set()
    assert hi._built_districts()
    # conservatory_dodge is +0.03 defense_rating on EVERY hero, so a party at
    # Veteran must dodge better than the same party at Apprentice.
    assert any(h.dodge > 0 for h in hi.engine.heroes)
    print("  PASS test_district_buffs_reach_the_heroes_at_veteran_and_not_below")


def test_the_max_hp_district_lands_BEFORE_hp_targets_are_clamped():
    """`districts_training_ring_2` is `combat_stat_multiply max_hp` and moves
    the ceiling an hp_target is clamped against -- exactly the ordering bug
    `health_stone` caused, where a party quietly bled HP between fights."""
    src = (Path(__file__).resolve().parents[2] / "env"
           / "expedition_sequence_env.py").read_text(encoding="utf-8")
    i_dist = src.index("self.engine._apply_buff_ids(hero, ids)")
    i_hp = src.index("hero.hp = max(0, min(int(hp_targets[k]), hero.max_hp))")
    assert i_dist < i_hp, (
        "district buffs are applied AFTER hp_targets are clamped; a max_hp "
        "district then raises a ceiling the HP can never catch up to")
    print("  PASS test_the_max_hp_district_lands_BEFORE_hp_targets_are_clamped")


def test_a_full_party_stays_full_when_a_district_raises_the_CEILING():
    """`districts_training_ring_2` is `combat_stat_multiply max_hp`, and
    `_apply_buff_to_stats` only ever clamps HP DOWN to a new ceiling -- right
    for a mid-combat buff, which must not heal.

    A district is not that. It is permanent roster equipment already in force
    when the raid begins, so applying it after `reset()` has filled the party
    opened a training_ring hero at 49/53 EVERY combat, and it compounds across
    an expedition. Exactly the `health_stone` trap `hp_targets` exists for, and
    `tests/bridge/test_rollout.py` is what caught it.
    """
    e = _env(tier=5)
    ring = [h for h in e.engine.heroes
            if "training_ring" in getattr(
                store.heroes.get(h.definition_id), "tags", ())]
    for h in e.engine.heroes:
        assert h.hp == h.max_hp, (
            f"{h.definition_id} opened at {h.hp}/{h.max_hp} -- a district "
            "raised the ceiling and the HP did not follow")
    if ring:
        low = _env(tier=2)
        base = {h.definition_id: h.max_hp for h in low.engine.heroes}
        for h in ring:
            assert h.max_hp > base.get(h.definition_id, h.max_hp), (
                "training_ring did not actually raise max_hp, so this test "
                "is no longer exercising the trap it was written for")
    print(f"  PASS test_a_full_party_stays_full_when_a_district_raises_the_CEILING "
          f"({len(ring)} training_ring heroes)")


def test_a_HURT_hero_keeps_absolute_hp_across_the_ceiling_change():
    """The other half of the rule: a bigger pool is not free healing. Only a
    hero who was AT full stays at full."""
    e = _env(tier=5)
    h = e.engine.heroes[0]
    h.hp = max(1, h.max_hp // 2)
    hurt = h.hp
    e._apply_persistent_equipment_for_current_roster()
    assert e.engine.heroes[0].hp == hurt, (
        "re-applying districts healed a hurt hero")
    print("  PASS test_a_HURT_hero_keeps_absolute_hp_across_the_ceiling_change")


def test_apprentice_is_a_control_group():
    """Nothing about tiers 1-2 may change, so a retrain can be read against
    them -- the role the short band plays for camping."""
    e = _env(tier=1, band="long", combats=15)
    assert e._built_districts() == set()
    assert e._provision_inventory["food"] == FOOD_AMOUNTS["long"]
    for h in e.engine.heroes:
        assert h.eat_heal_received_mult == 1.0
    e._snapshot_and_advance_roster(completed=True)
    assert e._hunger_checks in (0, 1)      # the ordinary roll, not the Mill
    print("  PASS test_apprentice_is_a_control_group")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
