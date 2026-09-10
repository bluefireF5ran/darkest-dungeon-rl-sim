"""Test JSON composition loader."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest

from tests._fixtures import get_store

from data.composition_loader import CompositionLoader
from engine import SeededRNG, CombatEngine


from data.paths import GAME_DIR
from data.paths import CSV_DIR
FIXTURES_DIR = Path(__file__).parents[1] / "fixtures"

store = get_store()
loader = CompositionLoader(store)


def test_load_example_json():
    path = FIXTURES_DIR / "african_vapors.json"
    assert path.exists()
    comp = loader.load_composition(path)
    assert comp.team_name == "African Vapors"
    assert comp.location == "The Ruins"
    assert len(comp.heroes) == 2
    assert comp.heroes[0].hero_class == "Crusader"
    assert comp.heroes[1].hero_class == "Vestal"
    print(f"  PASS test_load_example_json: {len(comp.heroes)} heroes")


def test_hero_position_order():
    comp = loader.load_composition(FIXTURES_DIR / "african_vapors.json")
    setup = loader.to_combat_setup(comp)
    loader.add_enemies(setup, ["cultist_brawler_A"])
    engine = CombatEngine(store, SeededRNG(42))
    engine.reset(hero_defs=setup.hero_defs, enemy_defs=setup.enemy_defs)
    assert engine.heroes[0].position == 0, f"Crusader at pos {engine.heroes[0].position}"
    assert engine.heroes[1].position == 1, f"Vestal at pos {engine.heroes[1].position}"
    assert engine.heroes[0].definition_id == "crusader"
    assert engine.heroes[1].definition_id == "vestal"
    print("  PASS test_hero_position_order")


def test_skill_validation():
    bad_data = {
        "teamName": "Bad", "location": "",
        "heroes": [{
            "heroClass": "Crusader", "activeSkills": ["FakeSkill"],
            "activeCampSkills": [], "trinket1": "", "trinket2": "",
            "quirks": {"positive": [], "negative": []},
            "lockedQuirks": {"positive": [], "negative": []}
        }]
    }
    with pytest.raises(ValueError):
        loader.parse_dict(bad_data)


def test_trinket_application():
    """The fixture's kit reaches the combatant's stats.

    This called `apply_trinkets_and_quirks` and printed the arguments until
    2026-08-27, so it passed just as happily when the call was a no-op -- which
    is the failure mode that actually happens here: a trinket display name that
    resolves to no id equips NOTHING and says nothing about it.
    """
    comp = loader.load_composition(FIXTURES_DIR / "african_vapors.json")
    setup = loader.to_combat_setup(comp)
    loader.add_enemies(setup, ["cultist_brawler_A"])
    engine = CombatEngine(store, SeededRNG(42))
    engine.reset(hero_defs=setup.hero_defs, enemy_defs=setup.enemy_defs)

    hero = engine.heroes[0]
    assert setup.hero_trinkets[0] and setup.hero_quirks[0], "nothing to apply"

    def scalars(c):
        return {k: v for k, v in vars(c).items() if isinstance(v, (int, float))}

    before = scalars(hero)
    engine.apply_trinkets_and_quirks(hero, setup.hero_trinkets[0], setup.hero_quirks[0])
    after = scalars(hero)
    changed = {k for k in before if before[k] != after[k]}
    assert changed, "the whole kit resolved to nothing"

    # The carriers, named so a silent resolution failure reads as a failure here
    # rather than as an unchanged number nobody looks at.
    assert after["max_hp"] > before["max_hp"], "quirk 'tough' (+10% max HP)"
    assert after["dodge"] < before["dodge"], "quirk 'clumsy' (-5 DODGE)"
    assert after["stress_dmg_received_mult"] > before["stress_dmg_received_mult"],         "trinket 'ancestors_pen' (+10% stress received)"


def test_combat_with_composition():
    comp = loader.load_composition(FIXTURES_DIR / "african_vapors.json")
    setup = loader.to_combat_setup(comp)
    loader.add_enemies(setup, ["cultist_brawler_A", "cultist_brawler_A"])
    engine = CombatEngine(store, SeededRNG(7))
    engine.reset(hero_defs=setup.hero_defs, enemy_defs=setup.enemy_defs)
    for i, hero in enumerate(engine.heroes):
        engine.apply_trinkets_and_quirks(hero, setup.hero_trinkets[i], setup.hero_quirks[i])

    steps = 0
    while not engine.done and steps < 300:
        steps += 1
        actor = engine.get_current_actor_id()
        if actor is not None:
            acts = engine.get_valid_actions(actor)
            ta = next((a for a in acts if a.target_id >= 0), None)
            if ta:
                engine.submit_action(ta)
        engine.step()

    assert engine.done, f"Did not finish in {steps} steps"
    print(f"  PASS test_combat_with_composition: {steps} steps ({engine.round} rounds), winner={engine.winner}")


if __name__ == "__main__":
    print("=== Composition Loader Tests ===\n")
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
