"""Tests for two real position/turn-economy gaps found while auditing Move:

1. Heroes never leave a corpse (unlike enemies, whose is_corpse flag blocks
   their rank until cleared) — real rule: everyone behind a dead hero
   immediately shifts one rank toward the front. This was entirely
   unimplemented (only CombatantState.position was ever set at reset()) —
   a dead hero at rank 2 left a permanent hole, or duelist_advance/move
   actions were the only ways to reorganize a reduced roster.
2. A hero with truly zero legal actions (boxed in, no reachable skill or
   Move target) had no real "pass turn" fallback at the CombatEngine level
   — the raw engine would hang forever waiting for submit_action(); only
   env/gym_env.py's Gym-layer clamping trick silently synthesized a no-op,
   with no stress cost and no log entry. Now a real __pass__ action exists,
   offered ONLY when get_valid_actions would otherwise be empty, and it
   costs stress (matching the real "doing nothing is bad" expectation).
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests._fixtures import get_store

from engine import SeededRNG, CombatEngine, Action


from data.paths import GAME_DIR
from data.paths import CSV_DIR
store = get_store()


def test_heroes_behind_a_dead_hero_shift_forward():
    crusader = store.heroes["crusader"]
    vestal = store.heroes["vestal"]
    plague_doctor = store.heroes["plague_doctor"]
    brawler = store.enemies["cultist_brawler_A"]
    engine = CombatEngine(store, SeededRNG(1))
    engine.reset(hero_defs=[crusader, vestal, plague_doctor], enemy_defs=[brawler],
                 hero_positions=[0, 1, 2])
    dying, behind1, behind2 = engine.heroes

    dying.hp = 0
    dying.death_blow_resist = 0.0
    engine.rng = SeededRNG(999)  # any roll < 1.0 - 0 triggers the deathblow
    engine._handle_deaths_door(dying, engine.enemies[0])  # first hit: enters Death's Door
    engine._handle_deaths_door(dying, engine.enemies[0])  # second hit while already there: deathblow roll

    assert dying.is_dead, "expected the deathblow roll to kill the hero for this test"
    assert behind1.position == 0, behind1.position
    assert behind2.position == 1, behind2.position
    print(f"  PASS test_heroes_behind_a_dead_hero_shift_forward (positions now {[h.position for h in engine.heroes]})")


def test_enemies_do_not_shift_on_death_corpse_blocks_instead():
    """Regression guard: the collapse behavior is hero-only — enemies keep
    their existing corpse-blocks-the-rank behavior unchanged."""
    crusader = store.heroes["crusader"]
    brawler = store.enemies["cultist_brawler_A"]
    engine = CombatEngine(store, SeededRNG(1))
    engine.reset(hero_defs=[crusader], enemy_defs=[brawler, brawler, brawler])
    front, mid, back = engine.enemies
    front.hp = 0
    engine._handle_deaths_door(front, engine.heroes[0])
    assert front.is_dead and front.is_corpse
    assert mid.position == 1, mid.position
    assert back.position == 2, back.position
    print("  PASS test_enemies_do_not_shift_on_death_corpse_blocks_instead")


def test_pass_turn_offered_only_when_no_other_action_exists():
    crusader = store.heroes["crusader"]
    brawler = store.enemies["cultist_brawler_A"]
    engine = CombatEngine(store, SeededRNG(1))
    engine.reset(hero_defs=[crusader], enemy_defs=[brawler, brawler])
    hero = engine.heroes[0]
    acts = engine.get_valid_actions(hero.id)
    assert not any(a.skill_id == "__pass__" for a in acts), \
        "crusader with real skills/move available should never see __pass__"
    print("  PASS test_pass_turn_offered_only_when_no_other_action_exists")


def test_pass_turn_fallback_when_boxed_in_with_no_actions():
    """A genuinely boxed-in state: a hero whose only equipped skill can't
    launch from their current rank, with every rank they could move to
    already occupied by a living ally.

    This has been rebuilt twice, and both times by a Move fix.

    It began as a Musketeer alone at position 0, relying on her (2 back /
    0 forward) data yielding no legal target — true only because the engine
    had the move DIRECTIONS inverted. Fixing that let her retreat, so it grew
    a full party to block the board. Then the OCCUPANCY rule turned out to be
    inverted too: a move is a swap, so a full party is the one board on which
    everybody can move. Alone is right after all, for the opposite reason —
    a lone survivor has no partner to swap with."""
    musketeer = store.heroes["musketeer"]
    brawler = store.enemies["cultist_brawler_A"]
    engine = CombatEngine(store, SeededRNG(1))
    engine.reset(
        hero_defs=[musketeer], enemy_defs=[brawler], hero_positions=[0],
        # aimed_shot launches from ranks 3-4 only, so it's unusable at rank 1.
        hero_skill_ids=[["aimed_shot"]],
    )
    hero = engine.heroes[0]
    assert hero.position == 0
    acts = engine.get_valid_actions(hero.id)
    # Retreat is always offered (see `attempt_retreat`) — the real game never
    # greys the button out — so the claim under test is about PASS: it appears
    # exactly when no skill and no Move is legal, and never alongside one.
    # Asserting the whole list verbatim was pinning the absence of an action
    # that had not been written yet.
    assert [a.skill_id for a in acts] == ["__retreat__", "__pass__"], acts
    print("  PASS test_pass_turn_fallback_when_boxed_in_with_no_actions")


def test_executing_pass_turn_applies_stress_and_advances_turn():
    musketeer = store.heroes["musketeer"]
    brawler = store.enemies["cultist_brawler_A"]
    engine = CombatEngine(store, SeededRNG(1))
    engine.reset(hero_defs=[musketeer], enemy_defs=[brawler], hero_positions=[0],
                 hero_skill_ids=[["aimed_shot"]])
    hero = engine.heroes[0]
    stress_before = hero.stress
    engine.submit_action(Action(hero.id, "__pass__", -1))
    engine._execute_action(engine._action_buffer)
    assert hero.stress > stress_before, (hero.stress, stress_before)
    print("  PASS test_executing_pass_turn_applies_stress_and_advances_turn")


if __name__ == "__main__":
    print("=== Position Gap Collapse / Pass Turn Tests ===\n")
    for name, func in sorted(globals().items()):
        if name.startswith("test_"):
            try:
                func()
            except Exception as e:
                import traceback
                print(f"  FAIL {name}: {e}")
                traceback.print_exc()
    print("\nDone.")
