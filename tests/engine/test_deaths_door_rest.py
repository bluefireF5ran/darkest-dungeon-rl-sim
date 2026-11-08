"""A hero resting on Death's Door rolls no deathblow at its turn start
(2026-10-11).

The game rolls a deathblow only on damage taken (`Actor::vf50`: a hit at or
under 0 damage survives). The turn-start DoT bookkeeping called the Death's
Door check unconditionally, so a hero at 0 HP with nothing ticking rolled 33%
every turn it took -- half of all Death's Door entries in the campaign's
first Apprentice raids ended in a death, against none of Fran's nine.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests._fixtures import get_store

from engine import SeededRNG, CombatEngine
from engine.combatant import DotInstance

store = get_store()


def _rest_on_the_door(seed, bleed):
    """Four Crusaders at 0 HP on Death's Door, the lone enemy stunned, and
    one advance to the next hero turn: the only damage there can be is the
    bleed."""
    engine = CombatEngine(store, SeededRNG(seed))
    engine.reset(hero_defs=[store.heroes["crusader"]] * 4, enemy_defs=[store.enemies["madman_A"]])
    for h in engine.heroes:
        h.hp = 0
        h.is_deaths_door = True
        h.bleeds.clear()
        h.blights.clear()
        h.burn_stacks = 0
        if bleed:
            h.bleeds.append(DotInstance("Bleed 3", 3, 3))
    for e in engine.enemies:
        e.is_stunned = True
    engine.current_turn_index += 1
    engine._advance_to_next_hero_turn()
    return sum(not h.is_alive() for h in engine.heroes)


def test_a_hero_resting_on_deaths_door_takes_no_roll():
    """No damage, no roll; the control is the same hero bleeding, which still
    rolls (and dies to some of them) -- so the guard is not a blanket skip."""
    assert sum(_rest_on_the_door(seed, bleed=False) for seed in range(40)) == 0
    assert sum(_rest_on_the_door(seed, bleed=True) for seed in range(40)) > 0


if __name__ == "__main__":
    test_a_hero_resting_on_deaths_door_takes_no_roll()
    print("  ok  test_a_hero_resting_on_deaths_door_takes_no_roll")
