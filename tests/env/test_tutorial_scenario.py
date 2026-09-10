"""THE OLD ROAD as a training scenario, and the two files that must agree.

The tutorial is the only expedition Darkest Dungeon does not generate:
`scripts/starting_save/` is hand-authored and identical on every new campaign.
`data/tutorial_loader.py` reads it; `COMPS/curated_comps/The_Old_Road.json` registers the same
party as a comp for the tooling that works by name. Two sources for one party is a
drift risk, so the first test here is that they say the same thing.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests._fixtures import get_store

from data.tutorial_loader import (TUTORIAL_COMP_NAME, load_tutorial_scenario)
from env.expedition_env import (ENEMY_CATEGORIES_FOR_TRAINING,
                                build_comp_pool)
from env.expedition_sequence_env import ExpeditionSequenceEnv

from data.paths import GAME_DIR
from data.paths import CSV_DIR


@pytest.fixture(scope="module")
def store():
    return get_store()


@pytest.fixture(scope="module")
def scenario(store):
    return load_tutorial_scenario(GAME_DIR, store)


def test_the_party_is_the_one_the_game_ships(scenario):
    """Reynauld the Crusader in front, Dismas the Highwayman behind, at
    resolve 0 with rank-0 gear and 10 stress each. Order comes from
    `actor.ranks`, not from the roster dict (which is keyed by guid and has no
    formation in it) and not from `last_party_guids` (which reads
    back-to-front)."""
    assert scenario.hero_ids == ("crusader", "highwayman")
    assert scenario.hero_levels == (0, 0)
    assert scenario.gear_ranks == ((0, 0), (0, 0))
    assert scenario.start_hp == (33, 23)
    assert scenario.start_stress == (10, 10)


def test_the_two_fights_are_the_ones_the_map_points_at(scenario, store):
    """`coAB` tile 2 carries `mash_index` 0 and `rooB` carries 1; the weald
    ships exactly two `named:` entries called `tutorial_*`. The second is the
    fight seen in live play in `rooB` — Brigand Bloodletter + Fusilier."""
    assert scenario.enemy_comp_names == ("tutorial_1", "tutorial_2")
    by_name = {e.name: e for e in store.enemy_comps}
    assert by_name["tutorial_1"].enemy_ids == ["brigand_cutthroat_A"]
    assert by_name["tutorial_2"].enemy_ids == ["brigand_blood_A", "brigand_fusilier_A"]
    assert scenario.num_combats == 2


def test_the_comp_json_and_the_shipped_save_agree(scenario, store):
    """The anti-drift test. `COMPS/curated_comps/The_Old_Road.json` is written in DISPLAY
    names ("Tracking Shot") and resolved back through the game's localization;
    the save carries internal ids ("take_aim") directly. Two paths, one party —
    if they ever disagree, the comp is lying about what the tutorial equips."""
    # By ALIAS: the 2026-08-23 re-export renamed this comp to "Ragged Band:
    # Heist" and kept "The Old Road" only in the new `alias` field. Indexing
    # `comp_loadouts` directly returns None and takes `tools/eval_tutorial.py`
    # -- the one exactly reproducible ruler here -- with it.
    loadout = store.comp_loadout_by_name(TUTORIAL_COMP_NAME)
    assert loadout is not None, "COMPS/curated_comps/The_Old_Road.json did not load"
    assert [h.hero_id for h in loadout.heroes] == list(scenario.hero_ids)
    for hero, skills in zip(loadout.heroes, scenario.hero_skill_ids):
        assert hero.skill_ids == list(skills), hero.hero_id


def test_the_tutorial_comp_is_not_in_the_sampled_pool(store):
    """Deliberate: the tutorial party is reached by PINNING it, never by
    sampling it. Sampling it would change which comps every archived
    `--comp-seed` selects and silently invalidate the baselines in CLAUDE.md,
    and it would corrupt `tools/eval_tutorial.py`, whose whole value is that
    its scenario is fixed.

    This asserted `not in store.comps` until 2026-08-27, which had become
    VACUOUS: the comps CSV was retired on 2026-08-23 and `store.comps` is now
    empty, so the assertion could not fail however badly the exclusion broke.
    The live mechanism is `PINNED_COMP_NAMES` inside `build_comp_pool`, and
    that is what this now reads -- the same claim, actually tested.
    `test_comp_pool_exclusions.py` covers the mechanism itself; this covers the
    pairing THIS file cares about, that the pinned comp is reachable by name
    and absent from the pool."""
    pool_names = {name for name, _zone, _heroes in build_comp_pool(store)}
    assert pool_names, "the pool is empty, so this test would prove nothing"
    assert TUTORIAL_COMP_NAME not in pool_names
    # ...and it is still reachable the pinned way, which is the other half.
    assert store.comp_loadout_by_name(TUTORIAL_COMP_NAME) is not None


def test_a_scripted_encounter_cannot_leak_into_a_sampled_expedition():
    """`named` is excluded from the random pool, which is what keeps the two
    kinds of expedition apart in both directions."""
    assert "named" not in ENEMY_CATEGORIES_FOR_TRAINING


def _env(store, scenario, **kw):
    return ExpeditionSequenceEnv(store=store, sampler_seed=0,
                                 fixed_scenario=scenario.reset_options(), **kw)


def test_the_scenario_is_replayed_on_every_reset(store, scenario):
    """SB3 calls `reset()` with no options, so a scenario that only applied
    when passed explicitly would train on sampled dungeons and quietly report
    itself as a tutorial run."""
    env = _env(store, scenario)
    for seed in (0, 1, 2):
        env.reset(seed=seed)
        assert [h.definition_id for h in env.engine.heroes] == ["crusader", "highwayman"]
        assert [e.definition_id for e in env.engine.enemies] == ["brigand_cutthroat_A"]
        assert env.engine.torch_level == 100.0
        assert env._num_combats == 2


def test_the_opening_hp_and_stress_are_the_saves_own(store, scenario):
    """Carry-over is the only way into the env with a hero above zero stress,
    and 10 of 100 is not nothing on a two-hero party with no healer."""
    env = _env(store, scenario)
    env.reset(seed=0)
    assert [(h.hp, h.stress) for h in env.engine.heroes] == [(33, 10), (23, 10)]


def test_each_hero_has_only_the_four_skills_the_tutorial_equips(store, scenario):
    """The whole point of the comp: slot 4 is Take Aim for this Highwayman,
    and a policy trained on the full movepool is choosing from a bar the
    tutorial does not show."""
    env = _env(store, scenario)
    env.reset(seed=0)
    for hero, expected in zip(env.engine.heroes, scenario.hero_skill_ids):
        offered = {a.skill_id for a in env.engine.get_valid_actions(hero.id)}
        assert not (offered - set(expected) - {"__move__", "__pass__", "__retreat__"}
                    - {a for a in offered if a.startswith("__provision_")})


def test_the_second_combat_is_the_second_scripted_fight(store, scenario):
    """Not just the first. `_start_next_combat` samples unless a script is
    present, and getting that half wrong would train fight 1 correctly and
    fight 2 against a random weald encounter."""
    env = _env(store, scenario)
    env.reset(seed=3)
    for _ in range(400):
        if env._combat_idx > 0:
            break
        masks = env.action_masks()
        legal = [i for i, ok in enumerate(masks) if ok]
        if not legal:
            break
        _obs, _r, terminated, truncated, _i = env.step(legal[0])
        if terminated or truncated:
            break
    if env._combat_idx == 0:
        pytest.skip("the party never finished the first fight with this policy")
    assert [e.definition_id for e in env.engine.enemies] == [
        "brigand_blood_A", "brigand_fusilier_A"]
    assert env.engine.torch_level == 100.0, "a scripted dungeon pins its torch too"


def test_an_unknown_encounter_name_raises_instead_of_sampling(store, scenario):
    """A typo that silently became "some random weald fight" would be a
    scenario nobody could reproduce, reported as the policy having got worse."""
    opts = scenario.reset_options()
    opts["enemy_comp_names"] = ["tutorial_1", "no_such_encounter"]
    env = ExpeditionSequenceEnv(store=store, sampler_seed=0, fixed_scenario=opts)
    env.reset(seed=0)
    with pytest.raises(ValueError, match="no_such_encounter"):
        env._scripted_enemy(1)


def test_sampling_still_works_when_nothing_is_scripted(store):
    """The default path must be untouched: no scenario, no script, no pinned
    torch — every existing checkpoint was trained through it."""
    env = ExpeditionSequenceEnv(store=store, sampler_seed=0)
    env.reset(seed=0)
    assert env._enemy_script == []
    assert env._scripted_torch is None
    assert env._num_combats >= 3


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
