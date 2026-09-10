"""Shuffle — and the parser bug that hid half of it.

Two `.darkest` flags, 24 effects, neither parsed:

    .shuffletarget    move the effect's target to a random rank
    .shuffleparty     scramble that target's whole side

So **Bounty Hunter Flashbang and Plague Doctor Disorienting Blast — two skills
whose entire payload is the shuffle — resolved to a plain attack**, and the
monster side lost Gargoyle `claw_flurry`, Unclean Giant `confusion_spores` and
the Shambler's howl.

The half that is a parser bug rather than a missing feature: `.shuffleparty` was
*parseable* and `.shuffletarget` mostly was not, for no reason but whitespace.
See `test_a_valueless_flag_survives_however_it_is_spaced`.

No resist. None of the 24 effects carries anything to roll against — push and
pull carry the distance the MOVE RES roll reads, and these carry nothing — so
the `.chance` roll is the only gate. Which is why `Shuffle Party` ships at
80-150%: an over-100% chance is a certainty, not something to resist.
"""

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests._fixtures import get_store

from data.darkest_parser import DarkestParser
from engine.combat_engine import CombatEngine
from engine.rng import SeededRNG

FOUR = ("crusader", "vestal", "arbalest", "hellion")


@pytest.fixture(scope="module")
def store():
    return get_store()


def _engine(store, seed=1, n_enemies=3):
    engine = CombatEngine(store, SeededRNG(seed))
    engine.reset(hero_defs=[store.heroes[h] for h in FOUR],
                 enemy_defs=[store.enemies["cultist_brawler_A"]] * n_enemies,
                 surprise=(False, False), auto_resolve_to_hero=False)
    return engine


def _order(cs):
    return [c.name for c in sorted(cs, key=lambda c: c.position)]


# ── the parser bug ──

def test_a_valueless_flag_survives_however_it_is_spaced(store):
    """The bug, reduced to two lines that differ only in whitespace.

    The old regex required `\\s+` and then a value. With TWO spaces the second
    one matched as the value and the key survived as ''; with ONE space or a
    TAB there was nothing left to match, the whole key failed, and it vanished
    from the record silently.

    Measured across every file the loaders read, `.shuffletarget` was dropped
    17 times and survived twice — in the same file."""
    one = DarkestParser._parse_line(
        'effect: .name "A" .shuffletarget .chance 100%')
    tab = DarkestParser._parse_line(
        'effect: .name "A" .shuffletarget\t.chance 100%')
    two = DarkestParser._parse_line(
        'effect: .name "A" .shuffletarget  .chance 100%')
    for label, rec in (("one space", one), ("tab", tab), ("two spaces", two)):
        assert "shuffletarget" in rec, label
        assert rec["chance"] == 1.0, label


def test_a_key_with_a_real_value_still_binds_it(store):
    """The optional-value group is greedy, so nothing that already worked
    starts parsing as an empty flag."""
    rec = DarkestParser._parse_line(
        'combat_skill: .id "x" .atk 72.5% .launch 4321 .target ~123 .dmg 2 4')
    assert rec["atk"] == 0.725
    # `_parse_line` returns the raw token; `decode_position_notation` turns
    # 4321 into [4, 3, 2, 1] later, at the loader.
    assert rec["launch"] == 4321
    assert rec["target"] == "~123"
    assert rec["dmg"] == "2 4"


def test_all_24_shuffle_effects_load(store):
    """11 of 24 before the parser fix."""
    got = sorted(e.name for e in store.effects.values()
                 if e.shuffle_party or e.shuffle_target)
    assert len(got) == 24, got
    assert "Disorient 3" in got and "Shuffle Party 3" in got


def test_the_flags_are_read_by_presence_not_truthiness(store):
    """`.shuffleparty` has no value, so it parses to the empty string and
    `bool('')` is False — the same trap as `.effect ""` parsing to `['']`. In
    this format an empty value means the flag is SET."""
    assert store.effects["Shuffle Party 3"].shuffle_party is True
    assert store.effects["Disorient 3"].shuffle_target is True
    assert store.effects["Disorient 3"].shuffle_party is False


# ── who uses it ──

def test_the_two_hero_skills_whose_whole_point_this_is(store):
    """Flashbang and Disorienting Blast are staples, and both were resolving
    to a plain attack."""
    for hero_id, skill_id in (("bounty_hunter", "flashbang"),
                              ("plague_doctor", "disorienting_blast")):
        sk = next(s for s in store.heroes[hero_id].skills if s.id == skill_id)
        lv = sk.levels[max(sk.levels)]
        assert any(store.effects[e].shuffle_target or store.effects[e].shuffle_party
                   for e in (lv.effects or []) if e in store.effects), skill_id


def test_no_shuffle_effect_carries_a_resist(store):
    """The evidence for "shuffle is unresistable": push and pull carry the
    distance the MOVE RES roll reads, and these carry no distance at all."""
    for e in store.effects.values():
        if e.shuffle_party or e.shuffle_target:
            assert not e.push_distance and not e.pull_distance, e.name


# ── the behaviour ──

def test_shuffle_party_scrambles_the_whole_side(store):
    moved = 0
    for seed in range(40):
        engine = _engine(store, seed=seed)
        before = _order(engine.heroes)
        engine._resolve_effect(engine.heroes[0], engine.enemies[0],
                               store.effects["Shuffle Party 3"], None)
        if _order(engine.heroes) != before:
            moved += 1
    # 1/24 of permutations are the identity, so a couple of misses is correct.
    assert moved >= 34, moved


def test_shuffle_target_moves_only_the_one(store):
    engine = _engine(store, seed=3)
    victim = engine.enemies[2]
    was = victim.position
    engine._resolve_effect(victim, engine.heroes[0],
                           store.effects["Disorient 3"], None)
    assert victim.position != was


def test_it_hits_whichever_side_the_target_is_on(store):
    """`.target "target"` on a monster skill means the party; the same effect
    on a hero skill means the monsters. The side comes from the effect target,
    never from who performed it."""
    moved = 0
    for seed in range(40):
        engine = _engine(store, seed=seed)
        # By ID, not by name: the three brawlers share a name, so an order
        # read off names cannot see a permutation at all.
        before = [c.id for c in sorted(engine.enemies, key=lambda c: c.position)]
        hero_before = [h.id for h in sorted(engine.heroes, key=lambda h: h.position)]
        engine._resolve_effect(engine.enemies[0], engine.heroes[0],
                               store.effects["Shuffle Party 3"], None)
        after = [c.id for c in sorted(engine.enemies, key=lambda c: c.position)]
        hero_after = [h.id for h in sorted(engine.heroes, key=lambda h: h.position)]
        assert hero_after == hero_before,             "the party must not move when the monsters are the target"
        if after != before:
            moved += 1
    assert moved >= 20, moved


def test_a_shuffle_is_a_permutation(store):
    """Same invariant as a push, a pull and the surprise scramble: the SET of
    occupied ranks cannot change, so a shuffle can neither stack two
    combatants nor open a hole."""
    for seed in range(60):
        engine = _engine(store, seed=seed)
        for eff in ("Shuffle Party 3", "Disorient 3"):
            engine._resolve_effect(engine.heroes[0], engine.enemies[0],
                                   store.effects[eff], None)
            pos = [h.position for h in engine.heroes]
            assert sorted(pos) == list(range(len(pos))), (seed, eff, pos)


def test_a_large_monster_is_not_shuffled_into_an_overlap(store):
    """A size-2 monster consumes two ranks, so a permutation is laid out with
    a cursor rather than by swapping positions. One that cannot fit is
    abandoned rather than half-applied."""
    big = next(e for e in store.enemies.values()
               if e.size == 2 and e.can_be_damaged_directly and "cauldron" not in e.id)
    engine = CombatEngine(store, SeededRNG(2))
    engine.reset(hero_defs=[store.heroes[h] for h in FOUR],
                 enemy_defs=[big, store.enemies["cultist_brawler_A"]],
                 surprise=(False, False), auto_resolve_to_hero=False)
    for seed in range(40):
        engine.rng = SeededRNG(seed)
        engine._scramble_side(is_hero=False)
        seen = [p for e in engine.enemies if e.is_alive()
                for p in e.occupied_positions]
        assert len(seen) == len(set(seen)), (seed, seen)
        assert max(seen) < 4, (seed, seen)


def test_a_lone_combatant_is_not_shuffled(store):
    engine = _engine(store, n_enemies=1)
    engine._scramble_side(is_hero=False)
    assert engine.enemies[0].position == 0


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
