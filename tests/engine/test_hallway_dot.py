"""The corridor between two fights, and what a DoT costs to walk it.

`rules.json` ships `dotHallwayStepRatio = 0.5` -- a rate for advancing a
damage-over-time effect per HALLWAY TILE -- and nothing read it. DoTs began
crossing the combat boundary on 2026-09-02; until this landed they then sat
there costing nothing until the next fight started, which made the key's whole
premise unreachable.

The measurement that sizes it is in `CORRIDOR_TILES`: 48 corridors across
seven distinct generated dungeons in `archive/traces/raidsnaps*`, mean 5.625
tiles, cross-checked against the walk `bat/play.log` records the actuator
actually performing.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests._fixtures import get_store

from engine.combatant import DotInstance
from env.expedition_sequence_env import (CORRIDOR_TILES, ExpeditionSequenceEnv,
                                         _spend_dot_ticks, hallway_dot_ticks)

from data.paths import GAME_DIR
from data.paths import CSV_DIR
store = get_store()


def _env(seed=0):
    env = ExpeditionSequenceEnv(store=store, sampler_seed=seed)
    env.reset(seed=seed, options={"length_band": "long"})
    return env


def _empty_the_pack(env):
    """The tail of a long run. A provisioned party CURES the DoT one block
    earlier, which is the whole point of the item -- so a test that wants to
    see the corridor bite has to spend the stock first."""
    for pid in ("bandage", "antivenom", "laudanum"):
        env._provision_inventory[pid] = 0


# -- the two pure helpers -------------------------------------------------

def test_the_shipped_ratio_makes_a_corridor_three_ticks():
    """0.5 per tile against a measured 5.625-tile corridor. The ratio is read
    off `rules.json` rather than typed here: if the key stops parsing the
    default keeps the mechanic alive, but a CHANGED shipped value has to move
    this number rather than being silently ignored."""
    assert store.rules.dot_hallway_step_ratio == 0.5
    assert CORRIDOR_TILES == 5.625
    assert hallway_dot_ticks(CORRIDOR_TILES, store.rules.dot_hallway_step_ratio) == 3
    print("  PASS test_the_shipped_ratio_makes_a_corridor_three_ticks")


def test_a_dot_is_truncated_to_what_it_has_left():
    """A 2-tick bleed in a 3-tick corridor deals two ticks, not three -- and
    leaves the list empty rather than carrying a negative count into the next
    fight."""
    short = [DotInstance("Bleed 3", 3, 2)]
    assert _spend_dot_ticks(short, 3) == 6
    assert short == []

    long_ = [DotInstance("Bleed 3", 3, 5)]
    assert _spend_dot_ticks(long_, 3) == 9
    assert [d.remaining_ticks for d in long_] == [2]
    print("  PASS test_a_dot_is_truncated_to_what_it_has_left")


# -- the boundary ---------------------------------------------------------

def test_the_corridor_spends_the_bleed_the_party_walked_through():
    env = _env()
    _empty_the_pack(env)
    hero = env.engine.heroes[0]
    hero.hp = hero.max_hp
    hero.bleeds.append(DotInstance("Bleed 3", 3, 5))

    env._snapshot_and_advance_roster(completed=True)
    carried = env._carry_over_by_index[env._alive_indices[0]]
    assert carried.hp == hero.max_hp - 9, carried.hp
    assert [d.remaining_ticks for d in carried.bleeds] == [2]
    assert env._corridor_dot_damage == 9
    print("  PASS test_the_corridor_spends_the_bleed_the_party_walked_through")


def test_a_RETREAT_walks_no_corridor():
    """`completed=False` means the party broke off and is standing in the
    corridor it came FROM. It has not walked a new one -- the same gate that
    denies it the meal and the campfire, and for the same reason: fleeing must
    never become a way to BUY anything."""
    env = _env()
    _empty_the_pack(env)
    hero = env.engine.heroes[0]
    hero.hp = hero.max_hp
    hero.bleeds.append(DotInstance("Bleed 3", 3, 5))

    env._snapshot_and_advance_roster(completed=False)
    carried = env._carry_over_by_index[env._alive_indices[0]]
    assert carried.hp == hero.max_hp
    assert [d.remaining_ticks for d in carried.bleeds] == [5]
    assert env._corridor_dot_damage == 0
    print("  PASS test_a_RETREAT_walks_no_corridor")


def test_a_hero_emptied_in_the_corridor_arrives_on_deaths_door_ALIVE():
    """NOBODY DIES IN A CORRIDOR. `_handle_deaths_door` puts a hero who is not
    already on the door at 0 HP and on it; the deathblow roll a hero already on
    it would face is deliberately NOT taken here, because a draw between
    combats moves the RNG stream and a death arriving where nothing can respond
    to it is what the starvation clamp already refuses. The consequence is
    carried into the next fight instead, where the engine resolves it."""
    env = _env()
    _empty_the_pack(env)
    hero = env.engine.heroes[0]
    hero.hp = 4
    hero.bleeds.append(DotInstance("Bleed 5", 5, 5))

    env._snapshot_and_advance_roster(completed=True)
    idx = env._alive_indices[0]
    carried = env._carry_over_by_index[idx]
    assert carried.hp == 0
    assert carried.is_deaths_door is True
    assert env._corridor_downed == 1
    # ...and it is still on the roster. A corridor tick removes nobody.
    assert idx in env._carry_over_by_index
    print("  PASS test_a_hero_emptied_in_the_corridor_arrives_on_deaths_door_ALIVE")


def test_the_corridor_DRAWS_NOTHING():
    """Deterministic by construction. Rolling a tick per tile would give the
    same expectation and shift the RNG stream for everything after it -- which
    is what stops two arms of an A/B facing the same dungeon. Same discipline
    `.dot_irresistible` follows by SKIPPING its roll rather than rolling
    against a zero resist."""
    env = _env()
    _empty_the_pack(env)
    env._snapshot_and_advance_roster(completed=True)
    for state in env._carry_over_by_index.values():
        state.bleeds.append(DotInstance("Bleed 3", 3, 5))
        state.blights.append(DotInstance("Poison 2", 2, 5))

    before = env.engine.rng.call_count
    env._walk_the_corridor(env._carry_over_by_index)
    assert env.engine.rng.call_count == before
    assert env._corridor_dot_damage > 0, "the walk has to have DONE something"
    print("  PASS test_the_corridor_DRAWS_NOTHING")


def test_the_bandage_finally_prevents_something():
    """The coherence check on the whole mechanic. `_use_provisions_between_
    combats` runs one block ABOVE the walk, so a bandage spent in the corridor
    is a bleed the corridor never gets to tick. Before DoTs crossed the
    boundary that method was a pure no-op which burned finite stock to prevent
    nothing -- strictly worse than doing nothing at all."""
    costs = {}
    for stocked in (True, False):
        env = _env()
        if stocked:
            env._provision_inventory["bandage"] = 1
        else:
            _empty_the_pack(env)
        hero = env.engine.heroes[0]
        hero.hp = hero.max_hp
        hero.bleeds.append(DotInstance("Bleed 3", 3, 5))
        env._snapshot_and_advance_roster(completed=True)
        costs[stocked] = env._corridor_dot_damage

    assert costs[False] == 9, costs
    assert costs[True] == 0, costs
    print("  PASS test_the_bandage_finally_prevents_something")


if __name__ == "__main__":
    test_the_shipped_ratio_makes_a_corridor_three_ticks()
    test_a_dot_is_truncated_to_what_it_has_left()
    test_the_corridor_spends_the_bleed_the_party_walked_through()
    test_a_RETREAT_walks_no_corridor()
    test_a_hero_emptied_in_the_corridor_arrives_on_deaths_door_ALIVE()
    test_the_corridor_DRAWS_NOTHING()
    test_the_bandage_finally_prevents_something()
    print("All hallway-DoT tests passed")
