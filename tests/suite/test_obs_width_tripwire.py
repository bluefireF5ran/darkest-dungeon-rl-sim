"""THE OBSERVATION WIDTH IS THE CHECKPOINT COMPATIBILITY CONTRACT.

A checkpoint only loads against the width it trained at. Every other test in
this suite DERIVES the width from the env -- which is right for indices, and is
exactly why none of them noticed when the width itself moved. The suite stayed
green, `models/dd_combat_ppo_full_expedition.zip` stopped loading, and the live
bridge stopped being able to play.

**And the width is a property of the LOADED DATA, not just of the code.** The
enemy slot carries a one-hot over the monster types the store contains, and
`_skill_slot_indices`'s own docstring says so: "The enemy-type one-hot width is
a property of the loaded store, so this cannot be a module constant either."
So a loader fix that reaches more content silently rewidens the observation and
kills every checkpoint -- no code in `env/` need change at all.

That is not hypothetical. On 2026-09-02 "THE DLC MONSTER TREES WERE NEVER READ"
added four monster types (`carpentry`, `ironwork`, `thatchery`, `cauldron`),
which is 4 types x 4 enemy ranks = **16 floats**, taking the sequence env from
1636 to 1652. The CHANGELOG entry for that fix does not mention the width at
all, and the first thing to notice was `bridge/advisor.py` refusing to run:

    ValueError: Unexpected observation shape (1652,) for Box environment,
    please use (1636,) or (n_env, 1636) for the observation shape.

WHEN THIS TEST FAILS, that is the tripwire working. Do not just bump the
numbers. Decide, deliberately, that every checkpoint is being invalidated --
then bump them, retrain, and say so in `CHANGELOG.md` where the width is
recorded.

Run the suite WITHOUT `DDSIM_MOD_DIRS`: a mods-on store loads more monsters and
would legitimately produce a different width, which is the same mechanism this
test exists to make visible.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests._fixtures import get_store

from env.gym_env import DarkestDungeonEnv
from env.expedition_env import ExpeditionEnv
from env.expedition_sequence_env import (EXPEDITION_CONTEXT_FEATURES,
                                         ExpeditionSequenceEnv)

from data.paths import GAME_DIR
from data.paths import CSV_DIR
store = get_store()

#: What the CURRENT engine produces. Moving either number invalidates every
#: checkpoint in `models/` and `archive/models/`.
BASE_OBS_WIDTH = 1644
SEQUENCE_OBS_WIDTH = 1652

#: The monster types the shipped (mods-off) content defines. The last four
#: arrived with the DLC monster-tree fix and are what widened the observation.
ENEMY_TYPES = (
    "beast", "carpentry", "cauldron", "cosmic", "eldritch", "husk",
    "ironwork", "man", "thatchery", "unfortunate_soul", "unholy",
    "vampire", "vegetation",
)


def test_the_enemy_type_one_hot_is_the_width_that_moves():
    """The one-hot is `len(store monster types)` wide and sits in EVERY enemy
    rank, so one new type costs `RANKS` floats. This is the only part of the
    observation whose size a pure DATA change can move."""
    env = DarkestDungeonEnv(store=store)
    assert tuple(sorted(env._enemy_type_index)) == ENEMY_TYPES, (
        "the monster type set moved -- the observation just changed width and "
        "every checkpoint trained at the old one is now unloadable")
    print("  PASS test_the_enemy_type_one_hot_is_the_width_that_moves")


def test_the_observation_widths_are_what_the_checkpoints_expect():
    """Absolute, deliberately. Every other test derives the width from the env,
    which is why the 1636 -> 1652 break passed a green suite."""
    assert DarkestDungeonEnv(store=store).observation_space.shape == (BASE_OBS_WIDTH,)
    assert ExpeditionEnv(store=store).observation_space.shape == (BASE_OBS_WIDTH,)
    assert (ExpeditionSequenceEnv(store=store).observation_space.shape
            == (SEQUENCE_OBS_WIDTH,))
    print("  PASS test_the_observation_widths_are_what_the_checkpoints_expect")


def test_the_sequence_env_is_the_base_plus_its_own_context_block():
    """The relationship, not just the two numbers -- so a change to one of them
    that forgets the other is caught as an inconsistency rather than passing as
    a deliberate bump."""
    assert SEQUENCE_OBS_WIDTH == BASE_OBS_WIDTH + EXPEDITION_CONTEXT_FEATURES
    env = ExpeditionSequenceEnv(store=store)
    assert env.observation_space.shape[0] == env.base_obs_size + EXPEDITION_CONTEXT_FEATURES
    print("  PASS test_the_sequence_env_is_the_base_plus_its_own_context_block")


def test_an_observation_is_actually_that_wide():
    """The declared space and what `_get_obs` returns are two different things,
    and the space is the one a checkpoint is checked against. The 2026-08-23
    note records the enemy slot landing six short when its payload grew
    underneath the constant."""
    env = ExpeditionSequenceEnv(store=store, sampler_seed=0)
    obs, _info = env.reset(seed=0)
    assert obs.shape == (SEQUENCE_OBS_WIDTH,), obs.shape
    assert obs.dtype.name == "float32"
    for _ in range(5):
        legal = [i for i, ok in enumerate(env.action_masks()) if ok]
        obs, _r, term, trunc, _i = env.step(legal[0])
        assert obs.shape == (SEQUENCE_OBS_WIDTH,), obs.shape
        if term or trunc:
            break
    print("  PASS test_an_observation_is_actually_that_wide")


if __name__ == "__main__":
    test_the_enemy_type_one_hot_is_the_width_that_moves()
    test_the_observation_widths_are_what_the_checkpoints_expect()
    test_the_sequence_env_is_the_base_plus_its_own_context_block()
    test_an_observation_is_actually_that_wide()
    print("All observation-width tripwire tests passed")
