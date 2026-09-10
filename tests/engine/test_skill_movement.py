"""Which way a hero moves, and which way a target gets shoved.

Two independent sign errors, both found on 2026-08-20 after Fran said *"lunge
from grave robber makes her go forward to the front"* and it did not.

**1. A combat skill's `.move <backward> <forward>` describes the PERFORMER**,
both numbers. The loader read `parts[1]` as a push on the *target* and stored
it in `SkillLevelData.push`, which the engine never reads — so every forward
self-move in the game was parsed into a field nothing consumes and silently
discarded. 55 of the 90 hero combat skills carrying a `.move` are
forward-moving (40x `0 1`, 10x `0 2`, 5x `0 3`). The 35 backward ones worked by
accident, because `parts[0]` was kept.

**2. Push and pull were inverted.** The engine moved a pushed target toward
rank 1 and a pulled target toward rank 4.

Neither is a matter of what the English words suggest; both are settled by
`.launch`/`.target` constraints in the shipped data, which is what this file
pins.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests._fixtures import get_store

from engine.combat_engine import CombatEngine
from engine.rng import SeededRNG


@pytest.fixture(scope="module")
def store():
    return get_store()


def _skill(store, hero_id, skill_id):
    sk = next(s for s in store.heroes[hero_id].skills if s.id == skill_id)
    return sk.levels[max(sk.levels)]


def _engine(store, hero_ids, n_enemies=3):
    engine = CombatEngine(store, SeededRNG(1))
    enemy = next(k for k in store.enemies if "cultist_brawler" in k)
    engine.reset(hero_defs=[store.heroes[h] for h in hero_ids],
                 enemy_defs=[store.enemies[enemy]] * n_enemies,
                 auto_resolve_to_hero=False)
    return engine


# ── the performer's own movement ──

def test_the_four_skills_that_settle_the_move_format(store):
    """`.move <backward> <forward>`, both about the performer. These four are
    unambiguous in the real game and disagree with each other in direction, so
    together they fix the format and the sign. Negative means forward."""
    assert _skill(store, "grave_robber", "lunge").move_self == -2
    assert _skill(store, "crusader", "holy_lance").move_self == -1
    assert _skill(store, "highwayman", "duelist_advance").move_self == -1
    assert _skill(store, "grave_robber", "shadow_fade").move_self == +2


def test_lunge_actually_moves_the_grave_robber_forward(store):
    """The report that started this: she launches from rank 3-4 and lands in
    the front half. Before the fix she did not move at all."""
    engine = _engine(store, ["crusader", "vestal", "arbalest", "grave_robber"])
    gr = engine.heroes[3]
    assert gr.position == 3, "premise: she starts at the back"
    engine._apply_movement(gr, engine.enemies[0],
                           _skill(store, "grave_robber", "lunge"))
    assert gr.position == 1, "Lunge is 2 forward: rank 4 -> rank 2"


def test_shadow_fade_still_retreats(store):
    """The backward half worked before and must keep working — the fix changed
    how both numbers are read, not just one."""
    engine = _engine(store, ["grave_robber", "vestal", "arbalest", "crusader"])
    gr = engine.heroes[0]
    engine._apply_movement(gr, engine.enemies[0],
                           _skill(store, "grave_robber", "shadow_fade"))
    assert gr.position == 2, "Shadow Fade is 2 back: rank 1 -> rank 3"


def test_a_forward_move_cannot_leave_the_board(store):
    engine = _engine(store, ["grave_robber", "vestal"])
    gr = engine.heroes[0]
    engine._apply_movement(gr, engine.enemies[0],
                           _skill(store, "grave_robber", "lunge"))
    assert gr.position == 0


def test_no_combat_skill_declares_both_directions(store):
    """The fix nets the two numbers, which is only safe because a combat skill
    never carries both. `combat_move_skill:` does -- there they are a RANGE --
    and it is parsed somewhere else entirely."""
    for hero in store.heroes.values():
        for sk in hero.skills:
            for lv in sk.levels.values():
                assert isinstance(lv.move_self, int)


# ── the target's movement ──

def test_push_goes_back_and_pull_comes_forward(store):
    """Settled by `.launch`/`.target`, not by the words:

        Daemon's Pull  .target 34  "Pull 2A"   -> must move forward
        Rampart        .target 12  "Push 1A"   -> must move back

    With the signs reversed each is a no-op on half its legal targets.
    """
    engine = _engine(store, ["crusader"])
    target = engine.enemies[0]
    target.move_resist = 0.0

    target.position = 0
    engine._resolve_effect(target, engine.heroes[0],
                           store.effects["Push 2A"], None)
    assert target.position == 2

    target.position = 3
    engine._resolve_effect(target, engine.heroes[0],
                           store.effects["Pull 2A"], None)
    assert target.position == 1


def test_the_two_skills_that_prove_it_are_still_shaped_that_way(store):
    """If a patch ever changes those skills' target ranks, the argument above
    stops holding and this file's reasoning needs redoing."""
    pull = _skill(store, "occultist", "daemons_pull")
    lance = _skill(store, "man_at_arms", "rampart")
    # `.target` keeps the game's own 1-indexed rank numbers.
    assert set(pull.target) <= {3, 4}, pull.target
    assert set(lance.target) <= {1, 2}, lance.target
    assert any("Pull" in e for e in pull.effects if e)
    assert any("Push" in e for e in lance.effects if e)


def test_a_push_cannot_shove_a_target_off_the_back(store):
    engine = _engine(store, ["crusader"])
    target = engine.enemies[0]
    target.move_resist = 0.0
    target.position = 3
    engine._resolve_effect(target, engine.heroes[0],
                           store.effects["Push 2A"], None)
    assert target.position == 3, "already at the rear; nowhere to go"


# ── the line moves with them (2026-08-21) ──

def _ranks(cs):
    return [c.position + 1 for c in cs]


def test_a_pull_slides_the_ranks_it_crosses(store):
    """Fran's statement of the rule, verbatim: "if you pull the position 4 to
    position 2, former-position 2 will go into 3 and former-position 3 will go
    into 4, it never leaves a gap nor has 2 enemies on the same position."."""
    engine = _engine(store, ["crusader"], n_enemies=4)
    for e in engine.enemies:
        e.move_resist = 0.0
    engine._resolve_effect(engine.enemies[3], engine.heroes[0],
                           store.effects["Pull 2A"], None)
    assert _ranks(engine.enemies) == [1, 3, 4, 2]


def test_a_push_slides_them_the_other_way(store):
    engine = _engine(store, ["crusader"], n_enemies=4)
    for e in engine.enemies:
        e.move_resist = 0.0
    engine._resolve_effect(engine.enemies[0], engine.heroes[0],
                           store.effects["Push 2A"], None)
    assert _ranks(engine.enemies) == [3, 1, 2, 4]


def test_lunge_pushes_the_party_back_as_she_passes(store):
    """Self-movement displaces allies for the same reason: she cannot land on
    top of the hero standing there. Before the fix she wrote an absolute
    position and stacked."""
    engine = _engine(store, ["crusader", "vestal", "arbalest", "grave_robber"])
    engine._apply_movement(engine.heroes[3], engine.enemies[0],
                           _skill(store, "grave_robber", "lunge"))
    assert _ranks(engine.heroes) == [1, 3, 4, 2]


def test_a_lone_combatant_cannot_be_pushed_at_all(store):
    """Fran, correcting a first attempt at this: *"a single enemy can't be
    pushed as there isn't enough positions to rotate, a displacement always
    keeps the gaps"*.

    A displacement is a rotation through the occupied ranks, so with one
    occupant there is nothing to rotate with and the push does nothing. This
    is the case that distinguishes a rotation from an arithmetic slide -- the
    slide would have parked it on rank 3."""
    engine = _engine(store, ["crusader"], n_enemies=1)
    engine.enemies[0].move_resist = 0.0
    engine._resolve_effect(engine.enemies[0], engine.heroes[0],
                           store.effects["Push 2A"], None)
    assert engine.enemies[0].position == 0


def test_the_rearmost_occupant_cannot_be_pushed_further_back(store):
    """Same rule from the other end: there is nobody behind to swap with."""
    engine = _engine(store, ["crusader"], n_enemies=3)
    for e in engine.enemies:
        e.move_resist = 0.0
    back = engine.enemies[2]
    engine._resolve_effect(back, engine.heroes[0],
                           store.effects["Push 2A"], None)
    assert back.position == 2
    assert _ranks(engine.enemies) == [1, 2, 3]


def test_the_occupied_ranks_are_the_same_ranks_afterwards(store):
    """The invariant that makes this a permutation: a displacement can neither
    open a hole nor close one. Gaps only ever sit at the BACK of a formation,
    so this is equivalent to saying the line stays contiguous from rank 1."""
    engine = _engine(store, ["crusader"], n_enemies=4)
    for e in engine.enemies:
        e.move_resist = 0.0
    before = sorted(e.position for e in engine.enemies)
    engine._resolve_effect(engine.enemies[3], engine.heroes[0],
                           store.effects["Pull 2A"], None)
    assert sorted(e.position for e in engine.enemies) == before


def test_no_stack_and_no_gap_under_random_shoving(store):
    """The invariant, hammered. Any sequence of pushes and pulls on a full
    board must leave four distinct ranks and no hole -- the state
    `[0, 0, 1, 3]` that motivated this must be unreachable."""
    import random
    rng = random.Random(7)
    for trial in range(60):
        engine = _engine(store, ["crusader", "vestal", "arbalest", "hellion"],
                         n_enemies=4)
        for e in engine.enemies:
            e.move_resist = 0.0
        for _ in range(8):
            eff = store.effects[rng.choice(["Push 2A", "Pull 2A",
                                            "Push 1A", "Pull 1A"])]
            engine._resolve_effect(rng.choice(engine.enemies),
                                   engine.heroes[0], eff, None)
        pos = [e.position for e in engine.enemies]
        assert len(set(pos)) == len(pos), (trial, pos)
        assert sorted(pos) == list(range(len(pos))), (trial, pos)



if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
