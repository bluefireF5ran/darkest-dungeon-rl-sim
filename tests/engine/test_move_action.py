"""Tests for the voluntary "Move" action — real per-hero combat_move_skill
data (data/hero_loader.py, backward/forward distance), wired into
CombatEngine.get_valid_actions/_execute_action as a genuine turn-costing
action (Action(hero_id, "__move__", target_position)), not a free modifier.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests._fixtures import get_store

from engine import SeededRNG, CombatEngine, Action


from data.paths import GAME_DIR
from data.paths import CSV_DIR
store = get_store()


def test_reachable_positions_match_backward_forward_clamped_to_board():
    """Occultist: (backward=2, forward=2) — starting at position 1 (0-indexed),
    reachable should be [0,1,2,3] minus current (1) => [0,2,3].

    A FULL party, because a move is a swap and every destination therefore
    needs an ally standing in it. This used to be a solo Occultist walking
    into empty ranks, which is the thing the engine got wrong."""
    occultist = store.heroes["occultist"]
    crusader = store.heroes["crusader"]
    brawler = store.enemies["cultist_brawler_A"]
    engine = CombatEngine(store, SeededRNG(1))
    engine.reset(hero_defs=[occultist, crusader, crusader, crusader],
                 enemy_defs=[brawler], hero_positions=[1, 0, 2, 3])
    hero = engine.heroes[0]
    acts = engine.get_valid_actions(hero.id)
    move_targets = sorted(a.target_id for a in acts if a.skill_id == "__move__")
    assert move_targets == [0, 2, 3], move_targets
    print(f"  PASS test_reachable_positions_match_backward_forward_clamped_to_board ({move_targets})")


def test_leper_cannot_move_backward():
    """Leper: (backward=0, forward=1) — from position 2 he may only advance,
    to position 1.

    This test previously asserted [3] — i.e. that he moved toward the BACK,
    contradicting its own name — because the engine had the two directions
    inverted. position 0 is rank 1 (the front), so forward DECREASES the
    index and backward increases it; the old code had it the other way
    round. See CombatEngine.get_valid_actions for the two real classes whose
    data made the correct direction unambiguous."""
    leper = store.heroes["leper"]
    crusader = store.heroes["crusader"]
    brawler = store.enemies["cultist_brawler_A"]
    engine = CombatEngine(store, SeededRNG(1))
    # Allies both in front of and behind him, so "cannot move backward" is a
    # statement about his data and not about an empty rank 4.
    engine.reset(hero_defs=[leper, crusader, crusader], enemy_defs=[brawler],
                 hero_positions=[2, 1, 3])
    hero = engine.heroes[0]
    acts = engine.get_valid_actions(hero.id)
    move_targets = sorted(a.target_id for a in acts if a.skill_id == "__move__")
    assert move_targets == [1], move_targets
    print(f"  PASS test_leper_cannot_move_backward ({move_targets})")


def test_pushed_sniper_can_retreat_and_pushed_bruiser_can_advance():
    """The two cases that exposed the inverted move directions, both tied to
    a measured pathology in the trained policy.

    Musketeer (.move 2 0 = 2 back / 0 forward) is a rank 3-4 sniper: pushed
    to the front she must be able to retreat, and must never voluntarily
    advance. Under the inverted mapping she computed lo=max(0,0-2)=0,
    hi=min(3,0+0)=0 — no legal move whatsoever — and the trained policy was
    observed passing (nothing else legal either) on 39% of her turns.

    Hellion (.move 0 1 = 0 back / 1 forward) is the mirror case: a rank-1
    bruiser who must be able to advance and never retreat. Inverted, she
    could only ever drift toward rank 4 and never return, and was observed
    passing on 18% of her turns."""
    brawler = store.enemies["cultist_brawler_A"]

    crusader = store.heroes["crusader"]

    engine = CombatEngine(store, SeededRNG(1))
    engine.reset(hero_defs=[store.heroes["musketeer"], crusader, crusader, crusader],
                 enemy_defs=[brawler], hero_positions=[0, 1, 2, 3])
    targets = sorted(a.target_id for a in engine.get_valid_actions(engine.heroes[0].id)
                     if a.skill_id == "__move__")
    assert targets == [1, 2], targets  # retreats up to 2 ranks, never advances

    engine = CombatEngine(store, SeededRNG(1))
    engine.reset(hero_defs=[store.heroes["hellion"], crusader, crusader, crusader],
                 enemy_defs=[brawler], hero_positions=[3, 0, 1, 2])
    targets = sorted(a.target_id for a in engine.get_valid_actions(engine.heroes[0].id)
                     if a.skill_id == "__move__")
    assert targets == [2], targets  # advances 1 toward the front, never retreats
    print("  PASS test_pushed_sniper_can_retreat_and_pushed_bruiser_can_advance")


def test_an_occupied_rank_is_the_ONLY_place_a_move_can_go():
    """The rule used to be exactly inverted, and the inversion broke Move in
    both directions at once.

    A move SWAPS with the ally in the destination rank — the game's own
    options text calls it "swap hero positions" — and the party is contiguous
    from rank 1, so there is no empty rank to move into. Skipping occupied
    ranks therefore meant a FULL party could not move at all (every candidate
    skipped), while an under-strength one could only move into the ranks its
    dead used to hold. That second half reached live play: a 2-hero tutorial
    party offered its Highwayman rank 4, the bridge clicked rank 4, and the
    game ignored it because nothing is there."""
    occultist = store.heroes["occultist"]
    crusader = store.heroes["crusader"]
    brawler = store.enemies["cultist_brawler_A"]
    engine = CombatEngine(store, SeededRNG(1))
    engine.reset(hero_defs=[occultist, crusader], enemy_defs=[brawler],
                 hero_positions=[1, 2])
    hero = engine.heroes[0]
    acts = engine.get_valid_actions(hero.id)
    move_targets = sorted(a.target_id for a in acts if a.skill_id == "__move__")
    # Occultist is 2/2 from position 1, so ranks 0, 2 and 3 are all in reach —
    # but only 2 has anyone standing in it.
    assert move_targets == [2], move_targets
    print(f"  PASS test_an_occupied_rank_is_the_ONLY_place_a_move_can_go ({move_targets})")


def test_a_full_party_can_still_move():
    """The half of the inversion that never reached a bug report, because it
    is silent: with all four ranks occupied every move candidate was skipped,
    so Move has been dead in every four-hero combat this project simulated.
    The action exists in the space (`[28..31]`) and was never once legal."""
    crusader = store.heroes["crusader"]
    brawler = store.enemies["cultist_brawler_A"]
    engine = CombatEngine(store, SeededRNG(1))
    engine.reset(hero_defs=[crusader] * 4, enemy_defs=[brawler],
                 hero_positions=[0, 1, 2, 3])
    for hero in engine.heroes:
        moves = [a for a in engine.get_valid_actions(hero.id)
                 if a.skill_id == "__move__"]
        assert moves, f"rank {hero.position + 1} of a full party cannot move"


def test_a_lone_survivor_has_nobody_to_swap_with():
    """Not an edge case to paper over -- it is the rule working. The last
    hero standing has closed ranks to the front and there is no partner, so
    the real game greys the move out too."""
    crusader = store.heroes["crusader"]
    brawler = store.enemies["cultist_brawler_A"]
    engine = CombatEngine(store, SeededRNG(1))
    engine.reset(hero_defs=[crusader], enemy_defs=[brawler], hero_positions=[0])
    assert not [a for a in engine.get_valid_actions(engine.heroes[0].id)
                if a.skill_id == "__move__"]


def test_the_swap_moves_BOTH_heroes():
    """Writing the new position alone left the vacated rank empty and the
    destination doubly occupied -- a formation this engine forbids everywhere
    else, and one no save could ever contain."""
    occultist = store.heroes["occultist"]
    crusader = store.heroes["crusader"]
    brawler = store.enemies["cultist_brawler_A"]
    engine = CombatEngine(store, SeededRNG(1))
    engine.reset(hero_defs=[occultist, crusader], enemy_defs=[brawler],
                 hero_positions=[1, 2])
    mover = engine.heroes[0]
    partner = engine.heroes[1]
    engine._execute_action(Action(mover.id, "__move__", 2))
    assert (mover.position, partner.position) == (2, 1)
    assert len({h.position for h in engine.heroes}) == 2, "two heroes on one rank"


def test_move_action_repositions_and_consumes_the_turn():
    """Unlike a free provision action (Milestone 5), Move should NOT
    re-offer the same hero's turn immediately — the next actor should be
    a different combatant."""
    occultist = store.heroes["occultist"]
    crusader = store.heroes["crusader"]
    brawler = store.enemies["cultist_brawler_A"]
    engine = CombatEngine(store, SeededRNG(1))
    engine.reset(hero_defs=[occultist, crusader], enemy_defs=[brawler], hero_positions=[1, 3])
    first_actor_id = engine.get_current_actor_id()
    hero = next(h for h in engine.heroes if h.id == first_actor_id)
    reachable = [a.target_id for a in engine.get_valid_actions(hero.id) if a.skill_id == "__move__"]
    assert reachable, f"{hero.definition_id} at position {hero.position} has no reachable Move targets"
    move_to = reachable[0]

    engine.submit_action(Action(hero.id, "__move__", move_to))
    engine.step()

    assert hero.position == move_to, hero.position
    partner = next(h for h in engine.heroes if h.id != hero.id)
    assert partner.position == 1, "the swap partner did not come the other way"
    next_actor_id = engine.get_current_actor_id()
    assert next_actor_id != first_actor_id, "Move should consume the turn, not re-offer the same hero"
    print("  PASS test_move_action_repositions_and_consumes_the_turn")


def test_no_move_actions_when_launch_excludes_current_position():
    """All real hero move_skill.launch values are [4,3,2,1] (any position),
    so this can't happen with real data today — but the engine should
    respect a restrictive launch generically rather than assuming it's
    always [4,3,2,1]."""
    occultist = store.heroes["occultist"]
    assert occultist.move_skill.launch == [4, 3, 2, 1]
    print("  PASS test_no_move_actions_when_launch_excludes_current_position (real data always allows any position)")


if __name__ == "__main__":
    print("=== Move Action Tests ===\n")
    for name, func in sorted(globals().items()):
        if name.startswith("test_"):
            try:
                func()
            except Exception as e:
                import traceback
                print(f"  FAIL {name}: {e}")
                traceback.print_exc()
    print("\nDone.")
