"""Provisions are NOT usable in battle, and this file used to assert they were.

The game says so itself. `localization/miscellaneous.string_table.xml` ships
`cant_use_items_in_battle` -- "Can't use items during battle" -- twice, once
plainly and once as `str_user_information_cant_use_items_in_battle`, the prefix
DD uses for a refusal shown to the player, and translated. Provisions are used
in a hallway or a cleared room, between fights.

So the four free turn-preserving in-combat actions this file was written around
were a simulator invention. They are gone (`IN_COMBAT_PROVISIONS_ALLOWED`), the
action INDICES stay where they are so every archived checkpoint still loads, and
the provisions moved to the between-rooms moment that already models eating --
`ExpeditionSequenceEnv._use_provisions_between_combats`.

What is kept from the old file is every CONDITION GATE, because the reason for
them survives the move: a provision is finite and has to last 8-18 combats, and
82% of the trained policy's uses were wasted when nothing stopped it.
"""

import sys
from pathlib import Path

import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests._fixtures import get_store

from engine import SeededRNG, CombatEngine, Action
from engine.combatant import DotInstance


from data.paths import GAME_DIR
from data.paths import CSV_DIR
store = get_store()


def _engine(provisions):
    crusader = store.heroes["crusader"]
    brawler = store.enemies["cultist_brawler_A"]
    engine = CombatEngine(store, SeededRNG(1))
    engine.reset(hero_defs=[crusader], enemy_defs=[brawler], provisions=provisions)
    return engine, engine.heroes[0]


def test_no_provision_is_ever_offered_in_battle():
    """The whole point: `cant_use_items_in_battle`. A bleeding hero holding a
    bandage still cannot use it mid-fight."""
    engine = CombatEngine(store, SeededRNG(1))
    engine.reset(hero_defs=[store.heroes["crusader"]],
                 enemy_defs=[store.enemies["cultist_brawler_A"]],
                 hero_positions=[0],
                 provisions={"bandage": 3, "antivenom": 3,
                             "holy_water": 3, "laudanum": 3})
    hero = engine.heroes[0]
    hero.bleeds.append(DotInstance(effect_name='Bleed 1', damage_per_tick=3, remaining_ticks=3))
    hero.blights.append(DotInstance(effect_name='Bleed 1', damage_per_tick=3, remaining_ticks=3))
    actions = engine.get_valid_actions(hero.id)
    assert not [a for a in actions if a.skill_id.startswith("__provision")]


def test_the_flag_is_what_turns_them_off_and_it_is_off():
    """Kept as a switch rather than deleted code, so the old behaviour is one
    edit away if the reading is ever falsified -- and so the reason lives next
    to it."""
    from engine.combat_engine import (IN_COMBAT_PROVISION_IDS,
                                      IN_COMBAT_PROVISIONS_ALLOWED)

    assert IN_COMBAT_PROVISIONS_ALLOWED is False
    assert IN_COMBAT_PROVISION_IDS == {"bandage", "antivenom",
                                       "holy_water", "laudanum"}


def test_the_action_indices_did_not_move():
    """Removing an action must not renumber the space -- `PROVISION_ACTION_ORDER`
    is `[32..35]` and `ACTION_SPACE_SIZE` is unchanged, so every archived
    checkpoint still loads. The actions are simply never legal, exactly as
    retreat is never legal on a surprised opening round."""
    from env.gym_env import ACTION_SPACE_SIZE, PROVISION_ACTION_ORDER

    assert ACTION_SPACE_SIZE == 39
    assert PROVISION_ACTION_ORDER == ("bandage", "antivenom",
                                      "holy_water", "laudanum")






def test_the_game_string_is_the_evidence():
    """Recorded in a test rather than only in a comment, because this reverses
    a mechanic the simulator carried for months and the next person to wonder
    why should find the citation, not the argument."""
    path = GAME_DIR / "localization" / "miscellaneous.string_table.xml"
    if not path.is_file():
        pytest.skip("needs the game install")
    text = path.read_text(encoding="utf-8", errors="replace")
    assert "cant_use_items_in_battle" in text
    assert "Can't use items during battle" in text


def test_no_provisions_by_default_unchanged_behavior():
    """reset() without a provisions arg must be identical to before this
    param existed."""
    crusader = store.heroes["crusader"]
    brawler = store.enemies["cultist_brawler_A"]
    engine = CombatEngine(store, SeededRNG(1))
    engine.reset(hero_defs=[crusader], enemy_defs=[brawler])
    assert engine.provision_inventory == {}
    acts = engine.get_valid_actions(engine.heroes[0].id)
    assert not any(a.skill_id.startswith("__provision_") for a in acts)
    print("  PASS test_no_provisions_by_default_unchanged_behavior")




# -- the cure, at the moment the game allows it ---------------------------

def _sequence_env():
    from env.expedition_sequence_env import ExpeditionSequenceEnv

    env = ExpeditionSequenceEnv(store=store, sampler_seed=1)
    env.reset(seed=0)
    return env


def test_a_bandage_is_used_between_combats_and_decrements():
    env = _sequence_env()
    env._provision_inventory.update({"bandage": 2, "antivenom": 2, "laudanum": 2})
    hero = env.engine.heroes[0]
    hero.bleeds.append(DotInstance(effect_name='Bleed 1', damage_per_tick=3, remaining_ticks=3))
    used = env._use_provisions_between_combats()
    assert used == 1
    assert not hero.bleeds
    assert env._provision_inventory["bandage"] == 1


def test_each_provision_is_gated_on_the_condition_it_treats():
    """The reason survives the move: a provision is finite and has to last
    8-18 combats, and 82% of the trained policy's uses were wasted when
    nothing stopped it."""
    env = _sequence_env()
    env._provision_inventory.update({"bandage": 2, "antivenom": 2, "laudanum": 2})
    before = dict(env._provision_inventory)
    assert env._use_provisions_between_combats() == 0
    assert env._provision_inventory == before


def test_an_empty_pack_treats_nothing_and_does_not_go_negative():
    env = _sequence_env()
    env._provision_inventory.update({"bandage": 0})
    hero = env.engine.heroes[0]
    hero.bleeds.append(DotInstance(effect_name='Bleed 1', damage_per_tick=3, remaining_ticks=3))
    assert env._use_provisions_between_combats() == 0
    assert hero.bleeds
    assert env._provision_inventory["bandage"] == 0


def test_holy_water_is_deliberately_NOT_used_here():
    """It is a proactive buff, not a cure -- there is no condition to gate it
    on, so "use it when it would help" has no honest reading, and applying it
    after every fight would spend the stock on corridors rather than on the
    fight it is meant to open."""
    env = _sequence_env()
    env._provision_inventory.update({"holy_water": 3})
    hero = env.engine.heroes[0]
    hero.bleeds.append(DotInstance(effect_name='Bleed 1', damage_per_tick=3, remaining_ticks=3))
    env._use_provisions_between_combats()
    assert env._provision_inventory["holy_water"] == 3

def test_the_cure_is_only_worth_anything_because_the_bleed_would_have_CARRIED():
    """**This is the test that makes the whole method non-trivial.** When the
    cure was first moved out of combat it was a pure no-op: `CarryOverHeroState`
    dropped DoTs, so the bandage prevented a bleed the next `reset()` was about
    to clear for free -- it spent a finite provision to buy nothing.

    `rules.json`'s `dotHallwayStepRatio` says a DoT walks the corridor with the
    party, so the DoTs carry now, and this pins BOTH halves: untreated the bleed
    costs the hero HP on the walk, treated it costs nothing.

    THE COST IS THE OBSERVABLE, not the list. A corridor spends three ticks
    (`hallway_dot_ticks`), so a three-tick bleed is exactly used up by the walk
    and the carried list is empty either way -- which is what an untreated
    bleed running its course looks like, not the cure working. Asserting on
    emptiness here passed for the wrong reason before the corridor existed."""
    hurt = {}
    for bandages in (0, 1):
        env = _sequence_env()
        env._provision_inventory.update({"bandage": bandages})
        hero = env.engine.heroes[0]
        hero.hp = hero.max_hp
        hero.bleeds.append(
            DotInstance(effect_name='Bleed 1', damage_per_tick=3, remaining_ticks=3))
        idx = env._alive_indices[0]
        env._snapshot_and_advance_roster(completed=True)
        hurt[bandages] = hero.max_hp - env._carry_over_by_index[idx].hp
        assert env._provision_inventory["bandage"] == 0

    assert hurt[0] == 9, f"no bandage: three ticks of a 3-damage bleed, got {hurt}"
    assert hurt[1] == 0, f"bandaged: the corridor has nothing to tick, got {hurt}"
    print("  PASS test_the_cure_is_only_worth_anything_because_the_bleed_would_have_CARRIED")


def test_a_fled_party_may_still_bandage():
    """The meal and the campfire are recoveries the game grants for CLEARING a
    room, so they are gated on `completed`. Using an item is a player action and
    a fled party is standing in a corridor with the same pack and the same
    bleed -- and it is about to re-take the encounter at full strength, which is
    the worst moment to still be bleeding."""
    env = _sequence_env()
    env._provision_inventory.update({"bandage": 1})
    env.engine.heroes[0].bleeds.append(
        DotInstance(effect_name='Bleed 1', damage_per_tick=3, remaining_ticks=3))
    env._snapshot_and_advance_roster(completed=False)
    assert env._provision_inventory["bandage"] == 0
    print("  PASS test_a_fled_party_may_still_bandage")


def test_medicinal_herbs_is_deliberately_NOT_used_here():
    """It is `.clear_debuff 1` -- one of the six `.item 1` effects the game
    ships -- and a combat debuff does not reach the corridor here: buffs are
    the one class `CarryOverHeroState` still drops, because their durations are
    counted in ROUNDS and a corridor has none. So there is nothing to clear,
    and using it anyway would repeat the in-combat version's mistake."""
    env = _sequence_env()
    env._provision_inventory.update({"medicinal_herbs": 3})
    hero = env.engine.heroes[0]
    hero.bleeds.append(
        DotInstance(effect_name='Bleed 1', damage_per_tick=3, remaining_ticks=3))
    env._use_provisions_between_combats()
    assert env._provision_inventory["medicinal_herbs"] == 3
    print("  PASS test_medicinal_herbs_is_deliberately_NOT_used_here")


if __name__ == "__main__":
    print("=== Provisions Combat Tests ===\n")
    for name, func in sorted(globals().items()):
        if name.startswith("test_"):
            try:
                func()
            except Exception as e:
                import traceback
                print(f"  FAIL {name}: {e}")
                traceback.print_exc()
    print("\nDone.")
