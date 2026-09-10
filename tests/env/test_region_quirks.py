"""Region quirks are gated by `in_dungeon`, and they were not (2026-08-27).

`QUIRK_in_dungeon_<region>_*` carries `rule_type: in_dungeon` with the region
in `rule_data_string`. The engine's conditional-rule handler left it out of
`_CONDITIONAL_RULE_TYPES`, and anything not listed there is treated as
always-active -- so **Cove Scrounger paid out in full in the Weald**.

The old justification is quoted in the source and was true when written: "0
equipped" carried it. That stopped being true the moment `tools/comp_ga.py`
started choosing quirks freely, and a search will always take free stats. Fran
found it in the emitted comps: *"recommends quirks for other regions ... they
shouldn't work on other regions"*.

Only 15 exist, and only for three regions -- ruins/crypts, cove and weald. The
Warrens has none at all.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests._fixtures import get_store

from engine.combat_engine import CombatEngine
from engine.rng import SeededRNG

store = get_store()

COVE = "cove_tactician"        # +15% damage, in_dungeon cove
WEALD = "weald_adventurer"     # -20% stress damage received, in_dungeon weald


def _hero(zone, quirks):
    eng = CombatEngine(store, SeededRNG(1))
    eng.reset(hero_defs=[store.heroes["leper"]],
              enemy_defs=[store.enemies["swine_wretch_A"]],
              zone_id=zone,
              on_board_ready=lambda: eng.apply_trinkets_and_quirks(
                  eng.heroes[0], [], list(quirks)))
    return eng.heroes[0]


def test_a_region_quirk_fires_only_in_its_own_region():
    cove = _hero("cove", [COVE, WEALD])
    weald = _hero("weald", [COVE, WEALD])
    bare = _hero("cove", [])
    # Cove Tactician is a damage buff: live in the cove, dead in the weald.
    assert cove.dmg_max > bare.dmg_max
    assert weald.dmg_max == bare.dmg_max, "a Cove quirk paid out in the Weald"
    # Weald Adventurer cuts stress taken: live in the weald, dead in the cove.
    assert weald.stress_dmg_received_mult < 1.0
    assert cove.stress_dmg_received_mult == 1.0, "a Weald quirk paid out in the Cove"


def test_an_UNSTATED_zone_grants_them_exactly_as_before():
    """`zone_id` is optional on `reset`, so every existing call site keeps its
    behaviour. A buff must not be silently withdrawn because the caller simply
    never said where the fight was -- that is a different bug from the one
    being fixed, and it would be invisible."""
    anywhere = _hero("", [COVE, WEALD])
    bare = _hero("", [])
    assert anywhere.dmg_max > bare.dmg_max
    assert anywhere.stress_dmg_received_mult < 1.0


def test_the_expedition_envs_tell_the_engine_where_the_fight_is():
    """Both reset paths matter: the opening combat is built by
    `DarkestDungeonEnv.reset`, which makes a FRESH engine, and every later
    combat by `_start_next_combat`. The zone has to travel in both, or the
    first fight of every expedition is the one place the gate stays open."""
    from env.expedition_sequence_env import ExpeditionSequenceEnv
    env = ExpeditionSequenceEnv(sampler_seed=0)
    env.reset(seed=1)
    assert env._zone_id
    assert env.engine.zone_id == env._zone_id

    from env.expedition_env import ExpeditionEnv
    single = ExpeditionEnv(sampler_seed=0)
    single.reset(seed=1)
    assert single.engine.zone_id


def test_only_three_regions_have_quirks_and_the_warrens_has_none():
    """A Warrens comp cannot carry a region quirk at all, so the search-side
    rewrite has nothing to rewrite TO there -- it drops them instead."""
    families = ("adventurer", "explorer", "scrounger", "tactician", "phobe")
    for fam in families:
        assert f"cove_{fam}" in store.quirks
        assert f"ruins_{fam}" in store.quirks
        assert f"weald_{fam}" in store.quirks
        assert f"warrens_{fam}" not in store.quirks


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  PASS {name}")
