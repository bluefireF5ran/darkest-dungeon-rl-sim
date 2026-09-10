"""`battle_modifier: .can_be_damaged_directly false` — the Hag's empty pot.

12 monsters carry it. The one that matters is `cauldron_empty`: a **100 HP,
size-2** prop that fills ranks 1-2 of the Hag's boss fight and cannot be
attacked at all. Unread until 2026-08-21, so the engine saw a fat enemy in the
front rank and offered it — and it is the FIRST enemy on the board, so it is the
first thing any front-rank melee hero is shown.

`cauldron_full` — the one holding a captured hero — does **not** carry the flag.
That is the whole design: you break the pot your friend is in, never the empty
one. (The capture mechanic itself is a documented out-of-scope exclusion, so in
this simulator the pot stays empty and stays untargetable.)
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests._fixtures import get_store

from engine.combat_engine import CombatEngine
from engine.rng import SeededRNG

PARTY = ("crusader", "hellion", "arbalest", "vestal")


@pytest.fixture(scope="module")
def store():
    return get_store()


def _hag(store, seed=1):
    engine = CombatEngine(store, SeededRNG(seed))
    engine.reset(hero_defs=[store.heroes[h] for h in PARTY],
                 enemy_defs=[store.enemies["cauldron_empty_A"],
                             store.enemies["hag_A"]],
                 surprise=(False, False), auto_resolve_to_hero=False)
    return engine


def _attack_ranks(engine, hero):
    return sorted({a.target_id for a in engine.get_valid_actions(hero.id)
                   if a.skill_id and not a.skill_id.startswith("__")
                   and not engine._is_ally_skill(a.skill_id) and a.target_id >= 0})


def test_the_flag_is_parsed_and_only_the_EMPTY_pot_has_it(store):
    assert store.enemies["cauldron_empty_A"].can_be_damaged_directly is False
    assert store.enemies["cauldron_full_A"].can_be_damaged_directly is True
    assert store.enemies["hag_A"].can_be_damaged_directly is True
    assert store.enemies["cultist_brawler_A"].can_be_damaged_directly is True


def test_the_pot_is_worth_attacking_on_paper(store):
    """Why this was not a rounding error: the thing being offered was the
    fattest target on the board and stood in the front rank."""
    pot = store.enemies["cauldron_empty_A"]
    assert pot.stats.hp == 100 and pot.size == 2
    assert pot.stats.hp > store.enemies["hag_A"].stats.hp


def test_nobody_can_aim_at_the_pot(store):
    engine = _hag(store)
    pot = engine.enemies[0]
    assert pot.occupied_positions == [0, 1], "premise: it fills ranks 1-2"
    for hero in engine.heroes:
        assert not set(_attack_ranks(engine, hero)) & {0, 1}, hero.name


def test_the_front_rank_melee_is_left_with_no_attack(store):
    """The consequence, and it is the real fight rather than a bug: the Hag
    sits on ranks 3-4 behind two ranks of pot, so a Crusader cannot reach her
    at all and the party needs reach. Stated here because it looks alarming in
    a log and someone will otherwise "fix" it."""
    engine = _hag(store)
    crusader = engine.heroes[0]
    assert crusader.position == 0
    assert _attack_ranks(engine, crusader) == []
    # ...and the fight is still winnable: the back three all reach her.
    assert all(_attack_ranks(engine, h) for h in engine.heroes[1:])


def test_the_hag_herself_is_reachable(store):
    """The control. If the filter were catching the whole enemy side, every
    assertion above would pass and the fight would be unwinnable."""
    engine = _hag(store)
    hag = engine.enemies[1]
    reachable = set()
    for hero in engine.heroes:
        reachable |= set(_attack_ranks(engine, hero))
    assert reachable & set(hag.occupied_positions)


def test_an_aoe_over_the_pots_ranks_does_not_touch_it(store):
    """`get_valid_actions` and `_resolve_action_targets` must move together —
    the rule corpses already follow. An AoE that swept ranks 1-2 and re-resolved
    to nothing would silently burn the turn."""
    engine = _hag(store)
    pot = engine.enemies[0]
    aoe = next((lv for h in PARTY for s in store.heroes[h].skills
                for lv in [s.levels[max(s.levels)]]
                if lv.is_aoe and lv.heal_min is None
                and not set(lv.target).isdisjoint({1, 2})), None)
    assert aoe is not None, "premise: some hero AoE covers ranks 1-2"
    from engine.action import Action
    targets = engine._resolve_action_targets(
        Action(engine.heroes[0].id, "x", 0), aoe, is_ally_skill=False)
    assert pot not in targets, [t.name for t in targets]


def test_an_ordinary_front_rank_enemy_is_still_a_target(store):
    """The regression guard for the whole change: this must not have made the
    front rank untargetable in general."""
    engine = CombatEngine(store, SeededRNG(1))
    engine.reset(hero_defs=[store.heroes[h] for h in PARTY],
                 enemy_defs=[store.enemies["cultist_brawler_A"]] * 2,
                 surprise=(False, False), auto_resolve_to_hero=False)
    assert 0 in _attack_ranks(engine, engine.heroes[0])


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
