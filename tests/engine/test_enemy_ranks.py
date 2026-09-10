"""Monsters advance into unblocked ranks; only the BACK of the formation gaps.

The engine used to never move an enemy on death at all — the only writes to a
non-hero `.position` anywhere were the push/pull effects. A formation was
therefore frozen for the whole fight, and a rank-4 monster stayed at rank 4 with
ranks 1-3 empty and unblocked.

That is not a cosmetic gap. No hero skill in the game reaches enemy rank 4 from
every position, and the Abomination cannot reach rank 4 in ANY form (human
manacles/vomit target ranks 1-3, beast rake/rage/slam target 1-3). A surviving
back-rank monster was permanently untargetable by him, leaving Absolution — free,
unlimited, and is_stall_invalidating False — as his only legal action. Measured
on the trained policy: 57% of every env step in "Stun Control: Beast" was
Absolution, 228 env steps per combat against 18.6 for the best comp, and a 70%
stall-out rate on the long band. The game's own stall penalty cannot catch this
because it requires alive_enemies <= 2 and the stalemate holds at 3-4 alive.

Corpses are occupants of the formation, not holes — that is exactly what makes
them block — so they slide forward too rather than leaving a permanent front gap.

See CHANGELOG.md.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests._fixtures import get_store

from engine.combat_engine import CombatEngine, CORPSE_ALIVE_ROUND_LIMIT
from engine.rng import SeededRNG

from data.paths import GAME_DIR as _GAME_DIR

GAME_DIR = str(_GAME_DIR)
from data.paths import CSV_DIR as _CSV_DIR

CSV_DIR = str(_CSV_DIR)

store = get_store()


def _engine(n_enemies=4, enemy_id="fishman_harpoon_B"):
    eng = CombatEngine(store, SeededRNG(1))
    eng.reset(hero_defs=[store.heroes["crusader"], store.heroes["vestal"]],
              enemy_defs=[store.enemies[enemy_id]] * n_enemies)
    return eng


def _ranks(eng):
    return sorted(e.position for e in eng.enemies if e.is_alive())


def _kill(enemy, corpse: bool):
    enemy.hp = 0
    enemy.is_dead = True
    enemy.is_corpse = corpse
    if corpse:
        enemy.corpse_rounds_remaining = CORPSE_ALIVE_ROUND_LIMIT


def test_survivors_advance_when_the_front_rank_dies_without_a_corpse():
    """The regression itself: this used to leave [1, 2, 3]."""
    eng = _engine()
    _kill(eng.enemies[0], corpse=False)
    eng._collapse_enemy_position_gaps()
    assert _ranks(eng) == [0, 1, 2]


def test_a_corpse_blocks_the_units_behind_it():
    """A corpse holds its rank — that is the whole point of corpses."""
    eng = _engine()
    _kill(eng.enemies[0], corpse=True)
    eng._collapse_enemy_position_gaps()
    assert _ranks(eng) == [1, 2, 3]
    assert eng.enemies[0].position == 0


def test_gaps_only_ever_form_at_the_back():
    """User-stated invariant: the only holes a monster formation can hold
    are in the rear positions. Checked over every death subset."""
    from itertools import combinations
    for k in range(1, 4):
        for dead in combinations(range(4), k):
            eng = _engine()
            for i in dead:
                _kill(eng.enemies[i], corpse=False)
            eng._collapse_enemy_position_gaps()
            occupied = _ranks(eng)
            assert occupied == list(range(len(occupied))), (
                f"killing {dead} left a front gap: {occupied}")


def test_a_rotting_corpse_releases_the_rank_it_held():
    eng = _engine()
    _kill(eng.enemies[0], corpse=True)
    eng._collapse_enemy_position_gaps()
    assert _ranks(eng) == [1, 2, 3]
    for _ in range(CORPSE_ALIVE_ROUND_LIMIT):
        eng._tick_corpse_durations()
    assert not eng.enemies[0].is_corpse
    assert _ranks(eng) == [0, 1, 2]


def test_a_size_two_monster_consumes_two_slots_when_advancing():
    """reset() lays the board out with a size-aware cursor; compaction
    must use the same accounting or a size-2 monster overlaps its neighbour."""
    big = next((e for e in store.enemies.values() if e.size == 2), None)
    assert big is not None, "no size-2 monster in the loaded data"
    eng = CombatEngine(store, SeededRNG(1))
    eng.reset(hero_defs=[store.heroes["crusader"]],
              enemy_defs=[store.enemies["fishman_harpoon_B"], big])
    assert eng.enemies[1].position == 1
    _kill(eng.enemies[0], corpse=False)
    eng._collapse_enemy_position_gaps()
    assert eng.enemies[1].position == 0
    assert eng.enemies[1].size == 2


def test_advancing_makes_a_back_rank_monster_reachable_by_the_abomination():
    """The concrete failure this fixes, stated in hero terms: the Abomination
    tops out at enemy rank 3 in every form, so a lone rank-4 survivor was
    unattackable by him for the rest of the fight."""
    eng = _engine()
    for i in range(3):
        _kill(eng.enemies[i], corpse=False)
    eng._collapse_enemy_position_gaps()
    survivor = next(e for e in eng.enemies if e.is_alive())
    abom_reach = {0, 1, 2}   # manacles/vomit 123, rake/rage 12/123, slam 12
    assert survivor.position in abom_reach


def test_death_during_normal_resolution_triggers_the_advance():
    """Not just the helper in isolation — the real kill path must call it."""
    eng = _engine()
    back = eng.enemies[3]
    front = eng.enemies[0]
    front.hp = 0
    eng._handle_deaths_door(front, eng.heroes[0], is_crit=True)  # crit => no corpse
    assert not front.is_corpse
    assert back.position < 3


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  ok  {name}")
