"""A party that is not four heroes must not read as a party that lost some.

`GLOBAL_ALIVE_FRAC` answers "how much of the party is still standing", and it
divided by a hardcoded `RANKS = 4`. So a perfectly healthy two-hero party
reported **0.5** -- the same number a four-hero party reports the moment it
loses half of itself, which is the single thing the feature exists to
distinguish. Nothing pinned either denominator.

The situation is real and reachable: an expedition continues with survivors
after a death (`ExpeditionSequenceEnv` re-packs them into contiguous ranks),
and `bridge/seed.py` builds an engine from whatever the live save holds --
including the two-hero tutorial party, which is what every overnight iteration
plays first.

The index is DERIVED, never written down. Three pinned offsets broke in one
day on 2026-08-25 by being literals; see `CLAUDE.md`.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest

from env.gym_env import (ENEMY_SLOT_SCALARS, GLOBAL_FEATURES, HERO_SLOT_SCALARS,
                         MAX_SKILL_SLOTS, RANKS, SKILL_SLOT_FEATURES,
                         DarkestDungeonEnv)
from tests._fixtures import get_store


# The globals are the last block of the BASE observation, and the alive
# fraction is its third entry (round, torch, alive, enemies, ...).
GLOBAL_ALIVE_FRAC = -GLOBAL_FEATURES + 2
GLOBAL_ENEMY_FRAC = -GLOBAL_FEATURES + 3


def _env():
    return DarkestDungeonEnv(store=get_store())


def _obs_for(env, hero_ids, enemy_ids):
    env.reset(seed=7, options={"hero_ids": hero_ids, "enemy_ids": enemy_ids})
    return env._get_obs()


@pytest.mark.parametrize("n", [1, 2, 3, 4])
def test_an_intact_party_reads_1_0_at_every_size(n):
    """The bug, stated as the property it broke: an intact party is intact."""
    env = _env()
    heroes = ["crusader", "highwayman", "vestal", "plague_doctor"][:n]
    obs = _obs_for(env, heroes, ["cultist_brawler_A"])
    assert obs[GLOBAL_ALIVE_FRAC] == pytest.approx(1.0), (
        f"a healthy {n}-hero party read {obs[GLOBAL_ALIVE_FRAC]:.3f}")


def test_losing_half_a_two_hero_party_is_not_the_same_as_having_two():
    """The two states the old encoding collapsed onto 0.5."""
    env = _env()
    intact = _obs_for(env, ["crusader", "highwayman"], ["cultist_brawler_A"])
    env.engine.heroes[1].hp = 0
    env.engine.heroes[1].is_dead = True
    halved = env._get_obs()
    assert intact[GLOBAL_ALIVE_FRAC] == pytest.approx(1.0)
    assert halved[GLOBAL_ALIVE_FRAC] == pytest.approx(0.5)


def test_the_denominator_is_the_party_and_not_the_rank_count():
    """`engine.heroes` is appended to only at `reset()` and never removed
    from, so its length IS the party that started this combat -- which is what
    makes it the right denominator and a dead hero still a member of it."""
    env = _env()
    _obs_for(env, ["crusader", "highwayman"], ["cultist_brawler_A"])
    assert len(env.engine.heroes) == 2
    env.engine.heroes[0].hp = 0
    env.engine.heroes[0].is_dead = True
    assert len(env.engine.heroes) == 2, "a death must not shorten the party"
    assert env._get_obs()[GLOBAL_ALIVE_FRAC] == pytest.approx(0.5)


def test_the_enemy_fraction_still_divides_by_RANKS():
    """Deliberately NOT changed. A board holds up to four enemy ranks whatever
    arrived, so "how full is the enemy line" is the question there -- not
    "what fraction of the wave survives". Pinned so the two are never made
    consistent by mistake."""
    env = _env()
    obs = _obs_for(env, ["crusader", "highwayman"], ["cultist_brawler_A"])
    living = sum(1 for e in env.engine.enemies if e.is_alive())
    assert obs[GLOBAL_ENEMY_FRAC] == pytest.approx(living / RANKS)


def test_the_global_block_is_where_this_file_thinks_it_is():
    """The derivation guard. If the layout moves, these two indices point at
    some other feature and every assertion above becomes a lie that passes."""
    env = _env()
    obs = _obs_for(env, ["crusader"], ["cultist_brawler_A"])
    assert len(obs) == (RANKS * HERO_SLOT_SCALARS
                        + RANKS * (ENEMY_SLOT_SCALARS + len(env._enemy_type_index))
                        + MAX_SKILL_SLOTS * SKILL_SLOT_FEATURES
                        + GLOBAL_FEATURES)
    # The torch sits immediately before the alive fraction and is the one
    # global with a value we can state independently of the board.
    assert obs[GLOBAL_ALIVE_FRAC - 1] == pytest.approx(env.engine.torch_level / 100.0)
