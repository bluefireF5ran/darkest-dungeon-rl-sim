"""Monsters that summon — which, until 2026-08-21, none of them did.

`EffectDefinition.summon_count` was parsed and never read, so 54 monster
skills across 35 monsters resolved to nothing. The spawner already existed
(`_spawn_reinforcements`); it was simply never reachable from a skill.

Who that covers matters more than the count. **Four of the eight region bosses
we now field are in the set** — Necromancer, Drowned Captain, Brigand Cannon
and the Siren — and summoning is the shape of those fights, not a flourish.
Ectoplasm splitting is in it too, in ordinary encounters.

The semantics were not obvious and are pinned here because two shipped effects
contradict each other under any simpler reading:

    NecroSummon 2      count 1, three candidates, chances 1.0 3.0 1.0
    SummonTentacle A   count 2, ONE candidate
    stall_summon_A     count 2, two candidates, plus .summon_erase_data_on_roll

So: a weighted pick repeated `count` times, WITH replacement, except where
`.summon_erase_data_on_roll` says otherwise — a flag exactly three effects
carry, all of them the reinforcement waves.
"""

import sys
from collections import Counter
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests._fixtures import get_store

from engine.combat_engine import CombatEngine
from engine.rng import SeededRNG


@pytest.fixture(scope="module")
def store():
    return get_store()


def _engine(store, enemy_ids, seed=3):
    engine = CombatEngine(store, SeededRNG(seed))
    engine.reset(hero_defs=[store.heroes["crusader"]],
                 enemy_defs=[store.enemies[e] for e in enemy_ids],
                 auto_resolve_to_hero=False)
    return engine


def test_summon_count_is_how_many_not_how_many_candidates(store):
    """`NecroSummon 2` names three skeletons and asks for ONE. Reading the
    chances as independent probabilities spawns all three, because two of them
    are above 1.0 and can never fail a `random() < chance` test."""
    engine = _engine(store, ["necromancer_B"])
    engine._summon_from_effect(store.effects["NecroSummon 2"])
    assert len(engine.enemies) == 2, [e.definition_id for e in engine.enemies]


def test_the_chances_are_weights(store):
    """1.0 / 3.0 / 1.0 over 400 draws is roughly 20/60/20."""
    counts = Counter()
    for seed in range(400):
        engine = _engine(store, ["necromancer_B"], seed=seed)
        engine._summon_from_effect(store.effects["NecroSummon 2"])
        counts[engine.enemies[-1].definition_id] += 1
    total = sum(counts.values())
    assert 0.12 < counts["skeleton_common_B"] / total < 0.30, counts
    assert 0.48 < counts["skeleton_militia_B"] / total < 0.72, counts
    assert 0.12 < counts["skeleton_defender_B"] / total < 0.30, counts


def test_the_same_monster_can_be_drawn_twice(store):
    """`SummonTentacle A` is count 2 over a single candidate, which is what
    rules out drawing without replacement as the general rule."""
    eff = store.effects["SummonTentacle A"]
    assert eff.summon_count == 2 and len(eff.summon_monster_ids) == 1
    engine = _engine(store, ["shambler_A"])
    before = len(engine.enemies)
    engine._summon_from_effect(eff)
    assert len(engine.enemies) == before + 2


def test_erase_on_roll_draws_without_replacement(store):
    """The reinforcement waves, and the only three effects carrying the flag.
    Two candidates at equal weight and count 2 gives one of each every time —
    which is the behaviour the reinforcement test has always pinned."""
    eff = store.effects["stall_summon_B"]
    assert eff.summon_erase_on_roll
    for seed in range(25):
        engine = _engine(store, ["cultist_brawler_B"], seed=seed)
        engine._summon_from_effect(eff)
        new = sorted(e.definition_id for e in engine.enemies[1:])
        assert new == ["cultist_brawler_B", "cultist_witch_B"], new


def test_only_the_reinforcement_waves_erase(store):
    """If a patch ever puts the flag on a normal summon, the reasoning in this
    file's docstring needs redoing."""
    flagged = sorted(e.name for e in store.effects.values()
                     if e.summon_erase_on_roll)
    assert flagged == ["stall_summon_A", "stall_summon_B", "stall_summon_C"]


def test_a_full_board_absorbs_no_summons(store):
    """Board space is the hard cap; a summon with nowhere to stand does not
    appear rather than stacking onto an occupied rank."""
    engine = _engine(store, ["necromancer_B", "cultist_brawler_B",
                             "cultist_brawler_B", "cultist_brawler_B"])
    assert len(engine.enemies) == 4
    engine._summon_from_effect(store.effects["NecroSummon 2"])
    assert len(engine.enemies) == 4


def test_summon_limits_cap_how_many_are_alive(store):
    """`Summon Fuseman 1` is `.summon_limits 1` — the cannon crew gets one
    loader, not one per turn."""
    eff = store.effects["Summon Fuseman 1"]
    assert eff.summon_limits == [1]
    engine = _engine(store, ["brigand_cannon_A"])
    engine._summon_from_effect(eff)
    engine._summon_from_effect(eff)
    fusemen = [e for e in engine.enemies
               if e.definition_id == "brigand_fuseman_A" and e.is_alive()]
    assert len(fusemen) == 1, [e.definition_id for e in engine.enemies]


def test_a_summon_never_lands_on_an_occupied_rank(store):
    engine = _engine(store, ["necromancer_B", "cultist_brawler_B"])
    for _ in range(3):
        engine._summon_from_effect(store.effects["NecroSummon 2"])
    occupied = [p for e in engine.enemies if e.is_alive()
                for p in e.occupied_positions]
    assert len(set(occupied)) == len(occupied), occupied


def test_the_bosses_that_summon_are_the_ones_we_field(store):
    """The reason this was worth fixing now rather than later: boss quests
    landed the same week, and half the bosses in them summon."""
    summoners = set()
    names = {e.name for e in store.effects.values() if e.summon_count}
    for ed in store.enemies.values():
        for sk in ed.skills:
            for lv in sk.levels.values():
                if names & {x for x in (lv.effects or []) if x}:
                    summoners.add(ed.id)
                    break
    for boss in ("necromancer_B", "drowned_captain_A", "brigand_cannon_A"):
        assert boss in summoners, boss


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
