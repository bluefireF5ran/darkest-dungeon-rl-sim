"""`overstressed_modifier:` -- the Flagellant always becomes Rapturous.

A contradiction was filed against this on 2026-08-23 and it was not one.
`is_generated: False` says berserk is never produced by the RANDOM DRAW;
`overstressed_modifier:` says this hero does not use the random draw at all.
Both are true, `_draw_trait`'s `is_rollable` filter stays exactly as it is, and
the standing "no Flagellant-only berserk branch" rule is satisfied -- nothing
here tests a hero class, it reads a record off whatever hero carries one.

What it fixes: the Flagellant drew one of the seven ordinary afflictions.
Rapturous is strictly better for him -- `DMGL25 DMGH25 DEF-20 SPD3`, and the
list carries NEITHER the -15 resists nor the MAXHP-10 that every ordinary
affliction includes in its own -- so the simulator was making him worse when
overstressed than the real game does, and his six act-outs never fired.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests._fixtures import get_store

from engine.combat_engine import CombatEngine
from engine.rng import SeededRNG
from engine.stress import resolve_check

store = get_store()


def _engine(seed=0):
    return CombatEngine(store, SeededRNG(seed))


def _one(eng, hero_class):
    hd = store.heroes[hero_class]
    ed = next(iter(store.enemies.values()))
    eng.reset([hd], [ed])
    return eng.heroes[0]


def test_the_record_is_parsed_and_is_flagellant_only():
    """One carrier in the whole install. If a second appears, the reading has
    to be re-checked rather than extended -- this is the only hero whose
    override was ever validated against the wiki."""
    carriers = sorted(h.id for h in store.heroes.values()
                      if getattr(h, "overstress_override_ids", None))
    assert carriers == ["flagellant"], carriers
    hd = store.heroes["flagellant"]
    assert hd.overstress_override_ids == ["berserk"]
    assert hd.overstress_override_chances == [1.0]
    print("  PASS test_the_record_is_parsed_and_is_flagellant_only")


def test_berserk_is_an_affliction_that_is_never_rollable():
    """The two halves of the non-contradiction, asserted side by side so a
    later reader does not have to re-derive it."""
    b = store.traits["berserk"]
    assert b.is_virtue is False
    assert b.is_rollable is False, "berserk must stay out of the random pool"
    assert set(b.buff_ids) == {"DMGL25", "DMGH25", "DEF-20", "SPD3"}
    print("  PASS test_berserk_is_an_affliction_that_is_never_rollable")


def test_the_flagellant_always_becomes_berserk():
    """Twenty seeds, no draw from the pool. Before this each of these was one
    of the seven ordinary afflictions."""
    for seed in range(20):
        eng = _engine(seed)
        hero = _one(eng, "flagellant")
        hero.stress = 99
        eng._apply_stress(hero, 5)
        assert hero.is_afflicted and not hero.is_virtuous
        assert hero.affliction_id == "berserk", (seed, hero.affliction_id)
    print("  PASS test_the_flagellant_always_becomes_berserk")


def test_the_override_carries_no_shared_affliction_penalty():
    """`TraitDefinition.buff_ids` is the COMPLETE effect list, and berserk's
    omits the -15 resists and MAXHP-10 the other seven include in theirs. So
    Rapturous must not cost max HP -- adding a baseline here would be the same
    double-count the trait work already removed once."""
    eng = _engine(3)
    hero = _one(eng, "flagellant")
    before = hero.max_hp
    hero.stress = 99
    eng._apply_stress(hero, 5)
    assert hero.affliction_id == "berserk"
    assert hero.max_hp == before
    print("  PASS test_the_override_carries_no_shared_affliction_penalty")


def test_every_other_class_still_draws_from_the_pool():
    """The override must not leak. A Crusader has no `overstressed_modifier:`
    record and has to reach one of the twelve rollable traits."""
    seen = set()
    for seed in range(24):
        eng = _engine(seed)
        hero = _one(eng, "crusader")
        hero.stress = 99
        eng._apply_stress(hero, 5)
        tid = hero.affliction_id or hero.virtue_id
        assert tid != "berserk"
        assert store.traits[tid].is_rollable
        seen.add(tid)
    assert len(seen) > 1, "the pool collapsed to one trait"
    print(f"  PASS test_every_other_class_still_draws_from_the_pool "
          f"({len(seen)} distinct)")


def test_a_single_entry_override_takes_no_draw():
    """The `dot_irresistible` precedent: rolling against a foregone conclusion
    gives the same outcome and a DIFFERENT RNG stream, which invalidates every
    seeded comparison after it. The shipped data has exactly one entry, so the
    weighted branch is unreachable in vanilla and this is the path that runs."""
    eng = _engine(11)
    hero = _one(eng, "flagellant")
    hero.stress = 100
    # `call_count`, and read directly rather than through a defaulting getattr:
    # a misspelled attribute behind `getattr(..., None)` turns this assertion
    # into a silent no-op, which is how two dead observation features shipped.
    before = eng.rng.call_count
    resolve_check(hero, eng.rules, eng.rng)
    assert eng.rng.call_count == before, "a draw was taken for a foregone outcome"
    assert hero.forced_trait_id == "berserk"
    assert hero.is_afflicted and not hero.is_virtuous
    # `affliction_id` is set later, by `_apply_affliction_effects`.
    assert hero.affliction_id is None
    print("  PASS test_a_single_entry_override_takes_no_draw")


def test_an_empty_override_falls_back_to_the_resolve_check():
    """An override id naming no loaded trait resolves to an EMPTY list, and
    that must leave the ordinary Resolve Check in place rather than leave the
    hero with no outcome at all. A mod is the realistic way this happens."""
    eng = _engine(5)
    hero = _one(eng, "crusader")
    assert hero.overstress_override == []
    hero.stress = 99
    eng._apply_stress(hero, 5)
    assert hero.is_afflicted or hero.is_virtuous
    print("  PASS test_an_empty_override_falls_back_to_the_resolve_check")


def test_the_weighted_branch_picks_by_weight():
    """Unreachable in shipped data -- one id at 1.0 -- but a mod can ship two,
    and a weighted pick that silently always took entry 0 would look exactly
    like the single-entry path working."""
    eng = _engine(2)
    hero = _one(eng, "crusader")
    hero.overstress_override = [("courageous", 0.0, True), ("abusive", 1.0, False)]
    hero.stress = 99
    eng._apply_stress(hero, 5)
    assert hero.affliction_id == "abusive", hero.affliction_id
    assert hero.is_afflicted and not hero.is_virtuous
    print("  PASS test_the_weighted_branch_picks_by_weight")


def test_an_override_onto_a_virtue_sets_the_virtue_side():
    """`is_virtue` comes from the named trait, not from an assumption that an
    override is always an affliction. Berserk happens to be one; nothing in the
    key says it must be."""
    eng = _engine(8)
    hero = _one(eng, "crusader")
    hero.overstress_override = [("courageous", 1.0, True)]
    hero.stress = 99
    eng._apply_stress(hero, 5)
    assert hero.is_virtuous and not hero.is_afflicted
    assert hero.virtue_id == "courageous"
    print("  PASS test_an_override_onto_a_virtue_sets_the_virtue_side")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
