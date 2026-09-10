"""Boss quests: the boss is the last room, or there is no boss at all.

`boss` used to sit in `ENEMY_CATEGORIES_FOR_TRAINING`, so a Siren could turn up
in a corridor at 3-6% of every draw. Removing it fixed that and created the
opposite problem: the policy then never saw a boss in its life, which matters
the moment the bridge walks into a real boss room.

This is the structural version. An expedition is EITHER a boss quest, whose
FINAL combat is that region's boss, or it is an ordinary one. The boss goes
last because that is where the game puts it — a static boss spawns in the room
furthest from the entrance, so the party arrives having already spent the
dungeon's attrition.

The rosters are not curated here; they are whatever the mash files' own `boss:`
rows say, which turns out to be exactly the two static bosses per region that
the game ships.
"""

import re
import sys
from collections import Counter
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests._fixtures import get_store

from env.expedition_env import (BOSS_QUEST_TIER_FOR, CORE_ZONE_IDS,
                                ExpeditionEnv)
from env.expedition_sequence_env import ExpeditionSequenceEnv

# One identifying member of each region's real boss encounters.
EXPECTED_BOSSES = {
    "crypts": {"necromancer", "prophet"},
    "cove": {"siren", "drowned_captain"},
    "warrens": {"swine_prince", "formless_guard"},
    "weald": {"hag", "brigand_cannon", "nest"},
}


@pytest.fixture(scope="module")
def store():
    return get_store()


def _bases(entry):
    return {re.sub(r"_[A-D]$", "", e) for e in entry.enemy_ids}


def test_every_region_offers_its_own_real_bosses(store):
    """Straight from the mash `boss:` rows — two static bosses per core
    region, which is what the game ships. If this drifts, the data moved."""
    env = ExpeditionEnv(store=store, sampler_seed=0)
    for zone in CORE_ZONE_IDS:
        seen = set()
        for _ in range(400):
            seen |= _bases(env._sample_boss(zone, 1))
        assert seen & EXPECTED_BOSSES[zone], (zone, sorted(seen))
        # and nothing from another region's list
        for other, names in EXPECTED_BOSSES.items():
            if other == zone:
                continue
            stray = seen & (names - EXPECTED_BOSSES[zone])
            assert not stray, (zone, stray)


def test_an_even_tier_snaps_to_its_bracket(store):
    """Tiers 2 and 4 carry only a stray Necromancer row, so a boss quest there
    would field one boss half the time across every region. They snap to the
    bracket's canonical odd tier — the real Apprentice/Veteran/Champion quest
    difficulties, and the tiers with full rosters."""
    assert BOSS_QUEST_TIER_FOR == {1: 1, 2: 1, 3: 3, 4: 3, 5: 5}
    env = ExpeditionEnv(store=store, sampler_seed=0)
    for tier in (2, 4):
        seen = set()
        for _ in range(400):
            seen |= _bases(env._sample_boss("cove", tier))
        assert {"siren", "drowned_captain"} <= seen, (tier, sorted(seen))


def test_a_boss_is_still_never_a_corridor_encounter(store):
    """The boss pool is SEPARATE from the corridor pool. That separation is the
    property that was missing when `boss` lived in
    ENEMY_CATEGORIES_FOR_TRAINING."""
    env = ExpeditionEnv(store=store, sampler_seed=0)
    for zone in CORE_ZONE_IDS:
        cats = Counter(env._sample_enemy(zone, 1).category for _ in range(1500))
        assert set(cats) <= {"hall", "room"}, (zone, dict(cats))


# ── the expedition ──

def test_the_default_never_fields_a_boss(store):
    """p=0.0 is what every checkpoint so far trained in, and must stay exact."""
    env = ExpeditionSequenceEnv(store=store, sampler_seed=0)
    for _ in range(12):
        env.reset()
        assert env._is_boss_quest is False


def test_a_boss_quest_puts_the_boss_last_and_nowhere_else(store):
    """Both halves matter. Last, because a static boss spawns in the furthest
    room; nowhere else, because a boss met mid-corridor is the bug this
    replaced."""
    env = ExpeditionSequenceEnv(store=store, sampler_seed=0, boss_quest_p=1.0)
    for _ in range(6):
        env.reset()
        assert env._is_boss_quest
        n = env._num_combats
        names = EXPECTED_BOSSES[env._zone_id]
        for i in range(n):
            got = _bases(env._draw_encounter(i))
            if i == n - 1:
                assert got & names, (env._zone_id, i, sorted(got))
            else:
                assert not (got & names), f"boss at combat {i} of {n}"


def test_whether_it_is_a_boss_quest_reproduces_with_the_scenario(store):
    """It is drawn from `_sampler_rng`, like the monsters and the torch —
    WHICH boss and WHETHER there is one are both part of the scenario, not of
    the party. So a reseeded scenario reproduces them."""
    env = ExpeditionSequenceEnv(store=store, sampler_seed=0, boss_quest_p=0.5)
    env.seed_scenario(31337)
    first = [_bases(env._sample_boss(z, 3)) for z in CORE_ZONE_IDS]
    env.seed_scenario(31337)
    assert [_bases(env._sample_boss(z, 3)) for z in CORE_ZONE_IDS] == first


def test_an_explicit_option_overrides_the_probability(store):
    """A replayed capture knows whether it was a boss quest; it must not be
    re-rolled underneath the caller."""
    env = ExpeditionSequenceEnv(store=store, sampler_seed=0, boss_quest_p=1.0)
    env.reset(options={"hero_ids": ["crusader", "vestal"],
                       "zone_id": "cove", "tier": 1, "length_band": "short",
                       "num_combats": 2, "boss_quest": False})
    assert env._is_boss_quest is False


def test_p_is_clamped_rather_than_trusted(store):
    assert ExpeditionEnv(store=store, sampler_seed=0,
                         boss_quest_p=4.0)._boss_quest_p == 1.0
    assert ExpeditionEnv(store=store, sampler_seed=0,
                         boss_quest_p=-2.0)._boss_quest_p == 0.0


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
