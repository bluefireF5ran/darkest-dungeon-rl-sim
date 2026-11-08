"""A monster skill shipped at `.dmg 0 0` deals no damage (2026-10-06).

182 of the 892 monster skill lines carry it: stress attacks, marks, curses.
Before the fix the loader read the zero band as "absent", the executor kept
whatever band the monster's previous skill left behind, and the 1-damage
floor in `resolve_attack` turned a scream into a hit -- a deathblow roll on a
hero at Death's Door. The Madman's doomsay is the plainest case: pure stress.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests._fixtures import get_store

from engine import SeededRNG, CombatEngine

store = get_store()


def _doomsay(seed):
    crusader = store.heroes["crusader"]
    madman = store.enemies["madman_A"]
    engine = CombatEngine(store, SeededRNG(seed))
    engine.reset(hero_defs=[crusader] * 4, enemy_defs=[madman])
    enemy = engine.enemies[0]
    sk = next(s for s in madman.skills if s.id == "doomsay")
    sd = sk.levels[max(sk.levels.keys())]
    assert (sd.atk_dmg_min, sd.atk_dmg_max) == (0, 0)
    band = (enemy.dmg_min, enemy.dmg_max)
    hp = [h.hp for h in engine.heroes]
    stress = [h.stress for h in engine.heroes]
    engine._execute_enemy_skill(enemy, "doomsay", sd, engine._get_monster_brain(madman))
    return engine, enemy, band, hp, stress


def test_a_zero_band_skill_takes_no_hp_and_still_lands():
    landed = 0
    for seed in range(20):
        engine, enemy, band, hp, stress = _doomsay(seed)
        assert [h.hp for h in engine.heroes] == hp, seed
        assert (enemy.dmg_min, enemy.dmg_max) == band, seed
        landed += any(h.stress > s for h, s in zip(engine.heroes, stress))
    assert landed, "doomsay never applied its stress in 20 seeds"


if __name__ == "__main__":
    test_a_zero_band_skill_takes_no_hp_and_still_lands()
    print("  ok  test_a_zero_band_skill_takes_no_hp_and_still_lands")
