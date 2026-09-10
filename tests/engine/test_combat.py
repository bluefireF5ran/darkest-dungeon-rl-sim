"""Integration test: full multi-round combat with real game data."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests._fixtures import get_store

from engine import SeededRNG, CombatEngine
from engine.action import Action


from data.paths import GAME_DIR
from data.paths import CSV_DIR


def test_2v2_combat():
    """2 Crusaders vs 2 Cultist Brawlers — runs a full combat to completion.

    One engine.step() = one turn (the current actor's, per
    engine.get_current_actor_id() — the engine owns turn order; callers
    don't pick a hero on their own). Loop bound is in *steps*, not rounds,
    since each step is a single actor's turn now, not a full round.
    """
    store = get_store()
    rng = SeededRNG(42)
    engine = CombatEngine(store, rng)

    crusader = store.heroes.get("crusader")
    brawler_a = store.enemies.get("cultist_brawler_A")
    assert crusader is not None, "Crusader not found"
    assert brawler_a is not None, "Cultist Brawler A not found"

    engine.reset(
        hero_defs=[crusader, crusader],
        enemy_defs=[brawler_a, brawler_a],
    )

    steps = 0
    while not engine.done and steps < 300:
        steps += 1
        actor = engine.get_current_actor_id()
        if actor is not None:
            acts = engine.get_valid_actions(actor)
            if acts:
                engine.submit_action(acts[0])
        engine.step()

    assert engine.done, f"Combat did not finish after {steps} steps"
    assert engine.winner in ("heroes", "enemies"), f"Unexpected winner: {engine.winner}"
    print(f"  Combat finished in {steps} steps ({engine.round} rounds), winner={engine.winner}")
    print(f"  Heroes alive: {sum(1 for h in engine.heroes if h.is_alive())}")
    print(f"  Enemies alive: {sum(1 for e in engine.enemies if e.is_alive())}")


def test_2v2_heroes_always_win():
    """2 Crusaders vs 2 Brawlers with fixed seed — heroes should win."""
    store = get_store()
    rng = SeededRNG(7)
    engine = CombatEngine(store, rng)
    crusader = store.heroes.get("crusader")
    brawler_a = store.enemies.get("cultist_brawler_A")

    engine.reset(
        hero_defs=[crusader, crusader],
        enemy_defs=[brawler_a, brawler_a],
    )

    steps = 0
    while not engine.done and steps < 300:
        steps += 1
        actor = engine.get_current_actor_id()
        if actor is not None:
            acts = engine.get_valid_actions(actor)
            target_action = next((a for a in acts if a.target_id >= 0), acts[0] if acts else None)
            if target_action:
                engine.submit_action(target_action)
        engine.step()

    assert engine.winner == "heroes", f"Expected heroes to win, got {engine.winner}"
    print(f"  Heroes won in {steps} steps ({engine.round} rounds)")


def test_turn_order_is_engine_owned_and_alternates_heroes():
    """Regression test for the turn-economy bug: get_current_actor_id()
    should alternate between different heroes over time (not perpetually
    favor one), and NOT every enemy should act within a single step() —
    only those between the current hero's turn and the next hero's turn in
    the same initiative queue.
    """
    store = get_store()
    engine = CombatEngine(store, SeededRNG(5))
    crusader = store.heroes["crusader"]
    brawler = store.enemies["cultist_brawler_A"]
    engine.reset(hero_defs=[crusader, crusader], enemy_defs=[brawler, brawler, brawler])

    actors_seen = set()
    any_step_with_partial_enemy_action = False
    for _ in range(12):
        if engine.done:
            break
        actor = engine.get_current_actor_id()
        assert actor is not None, "actor should be a living hero while combat is ongoing"
        actors_seen.add(actor)
        acts = engine.get_valid_actions(actor)
        if acts:
            engine.submit_action(acts[0])
        hp_before = [e.hp for e in engine.enemies]
        engine.step()
        hp_after = [e.hp for e in engine.enemies]
        changed = sum(1 for b, a in zip(hp_before, hp_after) if b != a)
        if 0 < changed < len(engine.enemies):
            any_step_with_partial_enemy_action = True

    assert actors_seen == {0, 1}, f"Expected both heroes to act, saw only {actors_seen}"
    assert any_step_with_partial_enemy_action, (
        "Expected at least one step where some but not all enemies acted — "
        "if every enemy always changes together, step() is still running the "
        "whole enemy phase instead of stopping at the next hero's turn"
    )
    print(f"  PASS: both heroes acted ({actors_seen}), enemy turns interleave correctly")


def test_round_does_not_increment_per_hero_action():
    """self.round should advance once per full initiative cycle, not once
    per engine.step() call (regression test for the unreachable `break` bug
    that made every hero action count as a full round)."""
    store = get_store()
    engine = CombatEngine(store, SeededRNG(3))
    crusader = store.heroes["crusader"]
    brawler = store.enemies["cultist_brawler_A"]
    engine.reset(hero_defs=[crusader, crusader], enemy_defs=[brawler, brawler])

    steps_taken = 0
    for _ in range(6):
        if engine.done:
            break
        actor = engine.get_current_actor_id()
        if actor is not None:
            acts = engine.get_valid_actions(actor)
            if acts:
                engine.submit_action(acts[0])
        engine.step()
        steps_taken += 1

    # 2 heroes + 2 enemies = 4 actors/round; 6 hero-turn steps can't possibly
    # need more than 3 rounds even in the worst case (every actor stunned-skip
    # aside), and should typically need fewer since only hero turns consume a
    # step() call while enemy turns resolve silently inside one.
    assert engine.round <= 3, f"round={engine.round} after only {steps_taken} steps — advancing too fast"
    print(f"  PASS: round={engine.round} after {steps_taken} hero-turn steps")


def test_stress_accumulation():
    """Stress should increase when heroes take crits."""
    store = get_store()
    rng = SeededRNG(42)
    engine = CombatEngine(store, rng)

    crusader = store.heroes.get("crusader")
    brawler_c = store.enemies.get("cultist_brawler_C")  # Champion tier, higher crit
    assert brawler_c is not None, "Cultist Brawler C not found"

    engine.reset(
        hero_defs=[crusader],
        enemy_defs=[brawler_c],
        hero_positions=[1],
    )

    rounds = 0
    while not engine.done and rounds < 30:
        rounds += 1
        acts = engine.get_valid_actions(0)
        if acts:
            target_action = next((a for a in acts if a.target_id >= 0), acts[0])
            engine.submit_action(target_action)
        engine.step()

    total_stress = sum(h.stress for h in engine.heroes if h.is_alive())
    print(f"  Combat ended round {rounds}, total hero stress={total_stress}")
    if engine.winner == "enemies":
        print("  (Heroes were defeated)")
    else:
        print(f"  Heroes alive: {sum(1 for h in engine.heroes if h.is_alive())}")


def test_deaths_door():
    """A hero driven to 0 HP by the real step loop enters Death's Door first.

    `tests/engine/test_deaths_door_tiers.py` pokes `_handle_deaths_door` directly; what
    is checked here is that the ordinary combat path reaches it at all. Until
    2026-08-27 this test set a 1 HP hero against a SINGLE brawler, printed the
    outcome and asserted nothing -- and the crusader won in two rounds without
    ever being hit, so the state named in the docstring was never reached.
    """
    store = get_store()
    engine = CombatEngine(store, SeededRNG(1))
    engine.reset(
        hero_defs=[store.heroes.get("crusader")],
        enemy_defs=[store.enemies.get("cultist_brawler_A")] * 4,
        hero_positions=[1],
    )

    hero = engine.heroes[0]
    hero.hp = 1
    hero.max_hp = 1

    saw_deaths_door = False
    rounds = 0
    while not engine.done and rounds < 60:
        rounds += 1
        actor = engine.get_current_actor_id()
        acts = engine.get_valid_actions(actor)
        if acts:
            engine.submit_action(acts[0])
        engine.step()
        if hero.is_deaths_door:
            saw_deaths_door = True

    assert engine.done, f"combat did not conclude in {rounds} rounds"
    assert saw_deaths_door, "the hero went from 1 HP to dead without Death's Door"
    assert hero.is_dead or hero.is_deaths_door
    assert hero.hp <= 0




def test_a_heal_that_cannot_restore_anything_is_not_offered():
    """A pure heal aimed only at full-HP heroes is strictly dominated: it
    restores nothing, carries no other effect, and costs the turn.

    Found in real play before it was found here — a Vestal healing herself at
    34/34 while the Man-at-Arms stood at 16/27. In the simulator the trained
    policy spent 21.2% of its skill turns healing, and 17.7% of those landed on
    a target already at full HP. The real game permits it; this is a deliberate
    narrowing of the legal set, not a fidelity fix.
    """
    store = get_store()
    engine = CombatEngine(store, SeededRNG(1))
    vestal = store.heroes["vestal"]
    crusader = store.heroes["crusader"]
    brawler = store.enemies["cultist_brawler_A"]
    engine.reset(hero_defs=[crusader, vestal], enemy_defs=[brawler],
                 hero_positions=[0, 3])
    # reset() auto-resolves enemy turns up to the first hero, so the party is
    # NOT necessarily untouched by the time it returns — top them up so the
    # premise of this test is explicit rather than incidental.
    for h in engine.heroes:
        h.hp = h.max_hp
    vestal_id = engine.heroes[1].id

    def heal_targets():
        return {a.target_id for a in engine.get_valid_actions(vestal_id)
                if a.skill_id == "divine_grace"}

    assert heal_targets() == set(), "full party: a pure heal restores nothing"

    engine.heroes[0].hp -= 1
    assert engine.heroes[0].position in heal_targets(), "the hurt hero must be healable"
    assert engine.heroes[1].position not in heal_targets(), "the full healer must not"


def test_a_heal_carrying_another_effect_stays_legal_at_full_hp():
    """The narrowing is deliberately tight: only skills whose SOLE payload is
    healing are withheld. Plague Doctor's Battlefield Medicine carries
    Cure/Cureself and Occultist's Wyrd Reconstruction carries a self-bleed, so
    both remain legal whatever the target's HP.

    `.effect ""` parses to `['']`, not to an empty list, so a plain truthiness
    test would read every heal as "has effects" and disable the rule entirely —
    which is exactly what happened on the first attempt."""
    assert CombatEngine._named_effects(['']) == []
    assert CombatEngine._named_effects('Wyrd Bleed 5') == ['Wyrd Bleed 5']

    store = get_store()
    engine = CombatEngine(store, SeededRNG(1))
    pd = store.heroes["plague_doctor"]
    crusader = store.heroes["crusader"]
    brawler = store.enemies["cultist_brawler_A"]
    engine.reset(hero_defs=[crusader, pd], enemy_defs=[brawler], hero_positions=[0, 3])
    for h in engine.heroes:
        h.hp = h.max_hp

    pd_id = engine.heroes[1].id
    medicine = {a.target_id for a in engine.get_valid_actions(pd_id)
                if a.skill_id == "battlefield_medicine"}
    assert medicine, "a heal that also cures must stay available at full HP"


def test_a_large_monster_answers_to_every_rank_it_stands_on():
    """A size-2+ monster covers several ranks and is reachable from any of
    them — offering only its FRONT rank made a skill whose `.target` is exactly
    its back rank illegal against it, up to "no legal target at all" for a
    rank-3-only skill facing a large enemy on ranks 2-3.

    61 of the 246 loaded monsters are size 2 or bigger, so this is not an edge
    case. Found from live play: with a size-2 Brigand Bloodletter holding ranks
    1-2, every rank behind it was being counted one short."""
    store = get_store()
    engine = CombatEngine(store, SeededRNG(1))
    heroes = [store.heroes[h] for h in ("highwayman", "man_at_arms", "vestal", "plague_doctor")]
    enemies = [store.enemies["brigand_blood_A"],      # size 2 -> ranks 1-2
               store.enemies["brigand_cutthroat_A"],  # rank 3
               store.enemies["brigand_fusilier_A"]]   # rank 4
    engine.reset(heroes, enemies, auto_resolve_to_hero=False)

    big, mid, back = engine.enemies
    assert big.size == 2
    assert big.occupied_positions == [0, 1]
    assert (mid.position, back.position) == (2, 3)

    # Both of the big monster's ranks resolve to it, and to nobody else.
    assert engine._find_combatant_by_position(0) is big
    assert engine._find_combatant_by_position(1) is big
    assert engine._find_combatant_by_position(2) is mid

    offered = {a.target_id for a in engine.get_valid_actions(engine.heroes[0].id)
               if not a.skill_id.startswith("__") and a.target_id >= 0}
    assert offered == {0, 1, 2, 3}, "every occupied rank must be targetable"


def test_an_aoe_covering_a_large_monsters_back_rank_hits_it():
    """Same rule on the resolution side: an AoE over ranks 3-4 hits a monster
    standing on ranks 2-3. Matching on the front rank alone let it slip out of
    the blast entirely. It is still hit at most once — one CombatantState
    cannot appear twice in the resolved list."""
    store = get_store()
    engine = CombatEngine(store, SeededRNG(1))
    engine.reset([store.heroes["hellion"]],
                 [store.enemies["brigand_cutthroat_A"],   # rank 1
                  store.enemies["brigand_blood_A"]],      # size 2 -> ranks 2-3
                 auto_resolve_to_hero=False)
    big = engine.enemies[1]
    assert big.occupied_positions == [1, 2]

    class _AoE:
        is_aoe = True
        target = [3, 4]           # ranks 3-4 -> positions 2-3
        heal_min = None           # a plain damaging AoE, so corpses are in scope
        dmg_mod = 0.0

    hit = engine._resolve_action_targets(Action(engine.heroes[0].id, "x", 2),
                                         _AoE(), is_ally_skill=False)
    assert hit == [big]


def test_an_aoe_damages_a_size_2_enemy_ONCE_not_twice():
    """The wiki's rule, asserted on damage rather than on the target list.

    *"an attack will affect a unit once at most, regardless of how many ranks
    are hit. For example, a size 2 enemy will not take double damage from an
    attack that hits both of its ranks."*

    The test above proves a large monster is REACHED by an AoE covering only
    one of its ranks. This proves the other half -- that covering BOTH of them
    does not charge it twice -- because the two are separate failure modes and
    only the first was pinned. `_resolve_action_targets` iterates `pool`, so
    one `CombatantState` cannot appear twice; this is the property that makes
    that implementation detail a rule.
    """
    store = get_store()
    engine = CombatEngine(store, SeededRNG(1))
    engine.reset([store.heroes["hellion"]],
                 [store.enemies["brigand_blood_A"]],      # size 2 -> ranks 1-2
                 auto_resolve_to_hero=False)
    big = engine.enemies[0]
    assert big.occupied_positions == [0, 1], "the premise: it covers two ranks"

    class _AoE:
        is_aoe = True
        target = [1, 2]           # BOTH of the ranks it stands on
        heal_min = None
        dmg_mod = 0.0

    hit = engine._resolve_action_targets(Action(engine.heroes[0].id, "x", 0),
                                         _AoE(), is_ally_skill=False)
    assert hit == [big], "hit once, not once per covered rank"
    assert len(hit) == len(set(id(c) for c in hit))


def test_every_hero_is_size_1_which_is_what_the_monster_AoE_path_assumes():
    """A premise, pinned because one code path depends on it silently.

    The hero->enemy AoE at `combat_engine.py:2332` matches against
    `occupied_positions`; the monster->hero AoE at `:4145` matches against
    `h.position` alone. Both are correct **only because every hero occupies
    exactly one rank** -- a size-2 hero would be missed by an AoE covering
    only its back rank, which is the same bug that was fixed on the enemy side.

    No shipped hero is size 2, so this is not a live defect and is deliberately
    not "fixed" ahead of a case that exists. If a mod or a DLC ever ships one,
    this fails and names the site to change rather than letting the AoE quietly
    under-hit.
    """
    store = get_store()
    oversized = [hid for hid, hd in store.heroes.items()
                 if getattr(hd, "size", 1) not in (1, None)]
    assert not oversized, (
        f"{oversized} occupy more than one rank -- combat_engine.py:4145 "
        f"matches heroes on `.position`, not `.occupied_positions`")


def test_occupied_positions_covers_size_three_and_four():
    """The property returned at most two ranks regardless of size, silently
    truncating the 8 size-3 and 2 size-4 monsters in the loaded data."""
    from engine.combatant import CombatantState

    def at(position, size):
        c = CombatantState(id=0, definition_id="x", is_hero=False, name="x",
                           position=position, hp=1, max_hp=1, dodge=0.0, prot=0.0,
                           speed=0, crit=0.0, atk_bonus=0.0)
        c.size = size
        return c.occupied_positions

    assert at(0, 1) == [0]
    assert at(0, 2) == [0, 1]
    assert at(0, 3) == [0, 1, 2]
    assert at(0, 4) == [0, 1, 2, 3]


def test_a_large_monster_cannot_be_moved_off_the_back_of_the_board():
    """The rear-most rank a combatant can hold is `BOARD_RANKS - size`, and
    all three movement paths were writing that bound by hand.

    `_apply_movement` and the push branch spelled out `size == 2` with
    everything else falling through to rank 4, so a size-3 monster could park
    its front rank at 4 and hang two ranks off the board. The pull branch had
    no size awareness at all. Same assumption that made large monsters answer
    to only their front rank."""
    from engine.combat_engine import BOARD_RANKS

    store = get_store()
    engine = CombatEngine(store, SeededRNG(1))
    engine.reset([store.heroes["crusader"]], [store.enemies["drowned_captain_A"]],
                 auto_resolve_to_hero=False)
    mon = engine.enemies[0]
    assert mon.size == 3

    class _MoveBack:
        move_self = 3

    # Since 2026-08-21 a displacement is a ROTATION through the occupied ranks
    # (see CombatEngine._shift_to_position), so the assertion has to be the
    # invariant rather than a landing spot: a lone monster has nobody to rotate
    # with and does not move at all, and a rotation that would hang this one
    # off the back is refused outright. Either way it never overhangs, which is
    # what this test exists to guarantee.
    engine._apply_movement(mon, mon, _MoveBack())
    assert mon.position <= BOARD_RANKS - mon.size
    assert max(mon.occupied_positions) <= BOARD_RANKS - 1

    mon.move_resist = 0.0
    engine._resolve_effect(mon, engine.heroes[0], store.effects["Pull 1A"], None)
    assert mon.position <= BOARD_RANKS - mon.size
    assert max(mon.occupied_positions) <= BOARD_RANKS - 1

    # And with a small monster behind it, where a naive swap WOULD overhang.
    engine2 = CombatEngine(store, SeededRNG(1))
    engine2.reset([store.heroes["crusader"]],
                  [store.enemies["drowned_captain_A"],
                   store.enemies["cultist_brawler_A"]],
                  auto_resolve_to_hero=False)
    big = engine2.enemies[0]
    big.move_resist = 0.0
    engine2._resolve_effect(big, engine2.heroes[0], store.effects["Push 2A"], None)
    assert max(big.occupied_positions) <= BOARD_RANKS - 1, big.occupied_positions


if __name__ == "__main__":
    print("=== Combat Engine Integration Tests ===\n")
    for name, func in sorted(globals().items()):
        if name.startswith("test_"):
            try:
                func()
                print(f"  PASS {name}\n")
            except Exception as e:
                import traceback
                print(f"  FAIL {name}: {e}")
                traceback.print_exc()
                print()
    print("Done.")
