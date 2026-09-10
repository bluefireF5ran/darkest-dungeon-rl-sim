"""A modded class arrives with its KIT now, not just its stat block.

`tests/data/test_mod_heroes.py` proved the class loads. This file proves the class
*works*: the effects, buffs, trinkets and camping skills its hero file names all
resolve, because `DataLoader` reads the mod's own `effects/`, `shared/buffs/`,
`trinkets/` and `raid/camping/` subtrees the way it already read the DLC's.

**The suite as a whole is a MODS-OFF suite.** Eleven tests assert vanilla
counts — 80 camping skills, 24 shuffle effects, one district tag per hero, the
comp pool size — and every one of them is *supposed* to fail with
`DDSIM_MOD_DIRS` set, because the flag changes the data those numbers describe.
That is what makes the default store reproducible and every archived
measurement re-readable. This file is the exception and builds both stores
explicitly, so it is correct whichever way the environment is pointed.

Before 2026-08-27 it did not, and the consequence was not subtle. Of the Sibyl's
160 effect references, **150 (93.8%) resolved to nothing** — including all three
`switch_mode_*_self` effects. A class whose entire design is three stances could
not change stance, so she started in `moon`, stayed there for the whole raid,
and five of her seven skills were permanently illegal. That is the same silent
inertness the Runaway's burn had, and the same shape as Backdraft's empty effect
list: real objects that do nothing.

## Why the Sibyl is the case worth a file

Three stances (moon / eclipse / sun), `align` cycling between them at
`.per_turn_limit 1` and `.is_continue_turn true`, and five of seven skills gated
on `.valid_modes`. Nothing in the engine needed changing for her — the stance
machinery has been there since the Duelist and `.set_mode` was already parsed
and already applied. What was missing was the data.
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from data.data_loader import DataLoader
from data.mod_roots import MOD_DIRS_ENV, merge_new_only
from engine import CombatEngine, SeededRNG
from env.expedition_env import UNRESTRICTED_HEROES, unrestricted_heroes
from env.gym_env import CSV_DIR, GAME_DIR

SIBYL_ROOT = Path(r"D:/Program Files (x86)/Steam/steamapps/workshop/content"
                  r"/262060/3490076588")
SIBYL = "sibyl_ms"

needs_sibyl = pytest.mark.skipif(
    not SIBYL_ROOT.is_dir(),
    reason="the Sibyl workshop mod is not installed on this machine")

_CACHE = {}


def _with_mod_dirs(value):
    """Build a store with `DDSIM_MOD_DIRS` set to exactly `value` (or cleared
    when None), restoring the environment afterwards.

    Both halves matter. `DataLoader.load_all` reads the variable now, so a
    "plain" store built while the suite happens to be running under
    `DDSIM_MOD_DIRS` is not plain -- which made this file's own derivation test
    fail under mods, comparing a modded store against a modded store.
    """
    old = os.environ.get(MOD_DIRS_ENV)
    if value is None:
        os.environ.pop(MOD_DIRS_ENV, None)
    else:
        os.environ[MOD_DIRS_ENV] = str(value)
    try:
        return DataLoader.load_all(CSV_DIR, GAME_DIR)
    finally:
        if old is None:
            os.environ.pop(MOD_DIRS_ENV, None)
        else:
            os.environ[MOD_DIRS_ENV] = old


def _plain_store():
    """A store with NO mod roots, whatever the environment says."""
    if "plain" not in _CACHE:
        _CACHE["plain"] = _with_mod_dirs(None)
    return _CACHE["plain"]


def _mod_store():
    """A store built WITH the Sibyl root. Deliberately `DataLoader.load_all`
    and a module-local memo rather than `load_store_cached`: the disk cache now
    keys on `DDSIM_MOD_DIRS` (that was itself the fix), but a test that reaches
    for the shared cache would still hand its 20-hero copy to whichever worker
    warmed it first."""
    if "store" not in _CACHE:
        _CACHE["store"] = _with_mod_dirs(SIBYL_ROOT)
    return _CACHE["store"]


def _sibyl_effect_refs(store):
    hero = store.heroes[SIBYL]
    for skill in hero.skills:
        for level in skill.levels.values():
            names = [e for e in (level.effects or []) if e]
            names += [e for e in (level.self_effects or []) if e]
            for mode_list in (level.mode_effects or {}).values():
                names += [e for e in (mode_list or []) if e]
            for name in names:
                yield skill.id, name


@needs_sibyl
def test_every_effect_the_sibyl_names_resolves():
    """160 references, 150 unresolved before this. The number that matters is
    zero, not "fewer": an unresolved effect is not a weaker effect, it is a
    skill that silently does nothing."""
    store = _mod_store()
    refs = list(_sibyl_effect_refs(store))
    missing = sorted({name for _skill, name in refs if name not in store.effects})
    assert refs, "no effect references at all -- did the hero stop loading?"
    assert not missing, (
        f"{len(missing)} of {len(refs)} effect references do not resolve: "
        f"{missing[:8]}")
    print(f"  PASS all {len(refs)} Sibyl effect references resolve")


@needs_sibyl
def test_the_three_stance_switches_exist_and_carry_set_mode():
    """`align` is the whole class. Its three payloads are the only thing that
    moves her between moon, eclipse and sun, and `.set_mode` is the key the
    engine reads (combat_engine.py, the Abomination's mechanism)."""
    store = _mod_store()
    for mode in ("moon", "eclipse", "sun"):
        eff = store.effects.get(f"switch_mode_{mode}_self")
        assert eff is not None, f"switch_mode_{mode}_self is missing"
        assert eff.set_mode == mode, (mode, eff.set_mode)
    print("  PASS moon/eclipse/sun switches all carry .set_mode")


@needs_sibyl
def test_she_can_reach_all_three_stances_in_a_real_combat():
    """The end-to-end claim, and the one that would have failed before.

    `align` carries `.per_turn_limit 1` and `.is_continue_turn true`, so she
    aligns once per turn and keeps the turn — which is why this drives a real
    fight rather than calling the skill three times in a row.
    """
    store = _mod_store()
    engine = CombatEngine(store, SeededRNG(3))
    heroes = [store.heroes[SIBYL], store.heroes["crusader"],
              store.heroes["vestal"], store.heroes["highwayman"]]
    enemies = [store.enemies["swinetaur_A"], store.enemies["swine_slasher_A"],
               store.enemies["swine_slasher_A"]]
    engine.reset(hero_defs=heroes, enemy_defs=enemies)
    sibyl = engine.heroes[0]
    assert sibyl.mode == "moon", "she starts in her file's first mode"

    seen = {}
    for _ in range(80):
        if engine.done:
            break
        actor = engine.get_current_actor_id()
        actions = engine.get_valid_actions(actor)
        if not actions:
            break
        if actor != sibyl.id:
            engine.submit_action(actions[0])
            engine.step()
            continue
        seen.setdefault(sibyl.mode,
                        sorted({a.skill_id for a in actions
                                if getattr(a, "skill_id", None)}))
        align = [a for a in actions if getattr(a, "skill_id", None) == "align"]
        engine.submit_action(align[0] if align else actions[0])
        engine.step()

    assert set(seen) >= {"moon", "eclipse", "sun"}, (
        f"only reached {sorted(seen)} -- the stance cycle is broken")
    # And the gate is real: each stance offers a different set.
    assert "banishment" in seen["moon"], seen["moon"]
    assert "faerie_fire" in seen["eclipse"], seen["eclipse"]
    assert "faerie_fire" in seen["sun"] or "solar_flare" in seen["sun"], seen["sun"]
    assert "solar_ray" not in seen["moon"], "a sun-only skill was legal in moon"
    print(f"  PASS reached {sorted(seen)}, each with its own legal set")


@needs_sibyl
def test_her_camping_skills_and_trinkets_load():
    """`hero_classes` on a camping skill is STRICT membership, so a class no
    shipped file names cannot camp at all unless its own file is read. Seven is
    the vanilla per-class count."""
    store = _mod_store()
    camp = [c for c in store.camping_skills.values()
            if SIBYL in (c.hero_classes or [])]
    assert len(camp) >= 4, f"only {len(camp)} camping skills reach her"
    trinkets = [t for t in store.trinkets if "sibyl" in t.lower()]
    assert trinkets, "no Sibyl trinkets loaded"
    print(f"  PASS {len(camp)} camping skills, {len(trinkets)} trinkets")


@needs_sibyl
def test_a_mod_may_add_an_id_but_never_shadow_one():
    """The rule that separates this merge from the DLC merge above it. A mod
    redefining `Stun 1` or a base buff would silently change what every vanilla
    comp does, with no error anywhere."""
    plain = _plain_store()
    modded = _mod_store()
    for table in ("effects", "buffs", "trinkets", "quirks"):
        base = getattr(plain, table)
        after = getattr(modded, table)
        assert set(base) <= set(after), f"{table}: mods REMOVED ids"
        changed = [k for k in base if after.get(k) is not base.get(k)
                   and after.get(k) != base.get(k)]
        assert not changed, (
            f"{table}: a mod redefined shipped ids {changed[:5]} -- "
            "merge_new_only exists to make that impossible")
    print("  PASS mods add ids and shadow none")


def test_merge_new_only_is_the_opposite_of_update():
    """A unit check, so the rule above does not rest on the shipped mods
    happening not to collide."""
    target = {"a": 1, "b": 2}
    added = merge_new_only(target, {"a": 99, "c": 3})
    assert target == {"a": 1, "b": 2, "c": 3}
    assert added == 1
    print("  PASS merge_new_only keeps the incumbent")


@needs_sibyl
def test_unrestricted_is_derived_from_the_data_not_from_a_list():
    """`UNRESTRICTED_HEROES` is a hand-kept pair of ids, so no modded class
    could ever join it — and identity is precisely what this project decided
    not to encode. The game states the fact itself:
    `skill_selection: .can_select_combat_skills false`, shipped on exactly the
    Abomination and the Duelist.

    The derivation reproducing that pair on the vanilla roster IS the check
    that it reads the right key.
    """
    plain = _plain_store()
    assert unrestricted_heroes(plain) == UNRESTRICTED_HEROES, (
        "the derived set no longer matches the shipped pair -- either the key "
        "moved or the constant is now wrong")

    modded = _mod_store()
    assert SIBYL in unrestricted_heroes(modded), (
        "the Sibyl declares can_select_combat_skills false and must not be cut "
        "to a 4-of-7: she can lose `align` and be stranded in one stance")
    assert SIBYL not in UNRESTRICTED_HEROES, "the constant stays vanilla-only"
    assert unrestricted_heroes(None) == UNRESTRICTED_HEROES
    print("  PASS derived on vanilla == the constant, and admits the Sibyl")


if __name__ == "__main__":
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            print(_name)
            _fn()
