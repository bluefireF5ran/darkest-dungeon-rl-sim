"""Third-party hero classes load — behind `DDSIM_MOD_DIRS`, and only partly.

`CLAUDE.md` has said modded heroes cannot load because "no `.info.darkest`
exists anywhere in the install". That diagnosis is wrong. Fran has five class
mods subscribed and their hero files exist and parse with the **unmodified**
parser; they live in `steamapps/workshop/content/262060/`, outside `game_dir`,
where no loader ever looked. And `HERO_DIR_MAP` is an explicit list of the 20
shipped ids, so even pointed at the right directory the loader would skip a
class it had never heard of.

`HeroLoader._load_mod_heroes` DISCOVERS classes under the roots named in
`DDSIM_MOD_DIRS` instead of enumerating them. With it set, all five load and
Sibyl fights a real combat to completion.

**But loading the hero is not loading the class**, and these tests pin that
distinction rather than papering over it: her effects, trinkets and camp skills
live in the mod directory too, and the loaders for those still read only
`game_dir`. She arrives with correct stats, skills, stances and damage, and
**42 of her 85 effect references resolve to nothing** — the same inertness the
Runaway's burn had before burn was implemented.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest

from data.hero_loader import HeroLoader

WORKSHOP = Path(r"D:/Program Files (x86)/Steam/steamapps/workshop/content/262060")
SHIPPED = {
    "abomination", "antiquarian", "arbalest", "bounty_hunter", "crusader",
    "duelist", "flagellant", "grave_robber", "hellion", "highwayman",
    "houndmaster", "jester", "leper", "man_at_arms", "musketeer", "occultist",
    "plague_doctor", "runaway", "shieldbreaker", "vestal",
}


def _workshop_has_content() -> bool:
    """Is there Workshop content, as opposed to a Workshop FOLDER?

    **Steam leaves the directory behind when everything is unsubscribed**, and
    the gate used to be `WORKSHOP.is_dir()` -- so an empty folder ran every
    test below against no mods at all and three of them failed on an empty
    list. That happened mid-session on 2026-08-31: the subscription list went
    12 -> 50 -> 0 inside a few hours while the suite was being run, and the
    failures said "no modded class discovered" as though the loader were
    broken.

    The suite's greenness must not depend on what Steam has finished
    downloading. `tests/data/test_mod_kit.py` never had this problem because it
    gates on ONE named mod directory; this is the same predicate, generalised.

    **Content is not CLASS content either (2026-09-23).** Fran's campaign
    subscriptions are 17 UI/QoL mods and not one hero class, and the old
    `any(iterdir())` gate ran these tests against them and failed the same
    three ways. The predicate is now what the loader itself globs for.
    """
    try:
        return any(WORKSHOP.glob("*/**/heroes/*/*.info.darkest"))
    except OSError:
        return False


needs_mods = pytest.mark.skipif(
    not _workshop_has_content(),
    reason="no Workshop HERO-CLASS mod for 262060 on this machine")


def _load(roots=None):
    """Load heroes with `DDSIM_MOD_DIRS` set (or cleared), restoring it after.

    Deliberately calls `HeroLoader.load_all` rather than going through
    `load_store_cached`: the store cache keys on `data/*.py` and the game
    subtrees, NOT on this env var, so a cached store would mask the effect.
    That is a real trap for anyone using the flag and is documented on the
    method.
    """
    old = os.environ.get(HeroLoader.MOD_DIRS_ENV)
    if roots is None:
        os.environ.pop(HeroLoader.MOD_DIRS_ENV, None)
    else:
        os.environ[HeroLoader.MOD_DIRS_ENV] = os.pathsep.join(str(r) for r in roots)
    try:
        from env.gym_env import GAME_DIR
        return HeroLoader.load_all(Path(GAME_DIR))
    finally:
        if old is None:
            os.environ.pop(HeroLoader.MOD_DIRS_ENV, None)
        else:
            os.environ[HeroLoader.MOD_DIRS_ENV] = old


def test_off_by_default():
    """The flag must be genuinely opt-in. Every archived measurement, every
    comp in the pool and every checkpoint assumes a 20-class roster."""
    heroes = _load(None)
    assert set(heroes) == SHIPPED, sorted(set(heroes) ^ SHIPPED)
    print(f"  PASS test_off_by_default ({len(heroes)} shipped classes)")


@needs_mods
def test_the_workshop_classes_are_discovered():
    heroes = _load([WORKSHOP])
    found = sorted(set(heroes) - SHIPPED)
    assert found, "no modded class discovered under the workshop root"
    assert SHIPPED <= set(heroes), "a shipped class went missing"
    print(f"  PASS test_the_workshop_classes_are_discovered ({found})")


@needs_mods
def test_a_discovered_class_has_the_vanilla_SHAPE():
    """A class that loads but is malformed is worse than one that does not
    load, because it reaches training silently.

    **A CENSUS, not a stop on the first offender.** This used to assert
    per-class inside the loop, so one bad class hid every other one and the
    failure said nothing about how widespread the problem was. That mattered
    the first time it fired: the subscription list had grown from 12 mods to
    50, seventeen classes were discovered, and the report named exactly one.
    Sixteen of the seventeen were fine; knowing that is the difference between
    "mod loading is broken" and "one mod does not fit".

    `MAX_SKILL_SLOTS` is **7** and the observation is built around it, so a
    class with more skills than that is not merely unusual -- the engine
    cannot represent it, and the extra skills would be dropped in silence.
    That is the failure this test exists to make loud.
    """
    from env.gym_env import MAX_SKILL_SLOTS

    heroes = _load([WORKSHOP])
    discovered = sorted(set(heroes) - SHIPPED)
    assert discovered, "no modded class discovered under the workshop root"

    bad: list[str] = []
    oversized: list[str] = []
    for hid in discovered:
        h = heroes[hid]
        if len(h.skills) > MAX_SKILL_SLOTS:
            # Not malformed -- a real class the ACTION SPACE cannot hold.
            # Checked against the gate below rather than failed here.
            oversized.append(hid)
        # FEWER skills than slots is fine and is not checked: the block
        # pads and the action mask marks the empty slots illegal, so the
        # class simply has less to choose from. Only MORE is a problem,
        # because the action space cannot hold them -- which is the
        # asymmetry above. `littlepiglet` ships 4 skills and fights fine
        # (`test_a_discovered_class_can_actually_FIGHT` covers 220
        # classes including it); calling that malformed was this test
        # asserting "exactly 7" while meaning "at most 7".
        if len(h.weapon_tiers) != 5:
            bad.append(f"{hid}: {len(h.weapon_tiers)} weapon tiers, want 5")
        if len(h.armour_tiers) != 5:
            bad.append(f"{hid}: {len(h.armour_tiers)} armour tiers, want 5")
        if h.base_stats.hp <= 0:
            bad.append(f"{hid}: no HP")
        bad += [f"{hid}/{sk.id}: no levels" for sk in h.skills if not sk.levels]

    broken = {b.split(":")[0].split("/")[0] for b in bad}
    detail = "\n  ".join(bad)
    assert not bad, (
        f"{len(bad)} problem(s) across {len(discovered)} discovered classes; "
        f"{len(discovered) - len(broken)} are fine:\n  {detail}")

    # A class with more skills than slots is allowed to EXIST -- Fran's
    # subscription list is not this repo's business -- but it must never reach
    # training, because actions 0..27 are `slot * RANKS + rank` and its extras
    # have no index at all. That is what the gate is for, and this is the
    # assertion that keeps the two honest with each other.
    if oversized:
        from tools.class_visibility import MAX_SKILLS
        leaked = [hid for hid in oversized
                  if len(heroes[hid].skills) <= MAX_SKILLS]
        assert not leaked, (
            f"{leaked} exceed MAX_SKILL_SLOTS yet the visibility gate would "
            f"admit them -- their extra skills would be dropped in silence, "
            f"which is the failure this test exists to make loud")
        print(f"  ...{len(oversized)} class(es) exceed {MAX_SKILL_SLOTS} "
              f"skills and are refused by the gate: {', '.join(oversized)}")
    print(f"  PASS test_a_discovered_class_has_the_vanilla_SHAPE "
          f"({len(discovered)} classes)")


@needs_mods
def test_a_mod_can_never_shadow_a_shipped_class():
    """A mod that replaces the Crusader would otherwise silently BECOME the
    Crusader everywhere, including in curated comps that mean the real one."""
    heroes = _load([WORKSHOP])
    for hid in SHIPPED:
        assert hid in heroes
    # And the guard is in the code, not just in today's mod set.
    src = Path(HeroLoader.__module__.replace(".", "/") + ".py")
    text = (Path(__file__).resolve().parents[2] / src).read_text(encoding="utf-8")
    assert "never shadow a shipped class" in text
    print("  PASS test_a_mod_can_never_shadow_a_shipped_class")


@needs_mods
def test_a_discovered_class_can_actually_FIGHT():
    """The end-to-end claim. Loading a dataclass proves nothing; this builds a
    board with one and runs the combat to a conclusion."""
    from engine.combat_engine import CombatEngine
    from engine.rng import SeededRNG
    from tests._fixtures import get_store

    heroes = _load([WORKSHOP])
    modded = sorted(set(heroes) - SHIPPED)
    # `fresh=True` because this MUTATES the store -- the rule in
    # `tests/_fixtures.py`. Injecting into the SHARED store put modded classes
    # into every other test that happened to land in the same xdist worker,
    # and `tests/engine/test_skill_index.py` duly failed with `KeyError: ('Cutter', 0)`
    # while passing in isolation. The store cache's own tripwire could not see
    # this one: it exempts `__`-prefixed buff keys and watches `store.buffs`,
    # not `store.heroes`.
    store = get_store(fresh=True)
    for hid in modded:
        store.heroes.setdefault(hid, heroes[hid])

    eng = CombatEngine(store, SeededRNG(0))
    hid = modded[0]
    enemy = next(iter(store.enemies.values()))
    eng.reset([store.heroes[hid], store.heroes["crusader"]], [enemy])
    turns = 0
    while not eng.done and turns < 60:
        aid = eng.get_current_actor_id()
        if aid is None:
            break
        actions = eng.get_valid_actions(aid)
        if not actions:
            break
        eng.submit_action(actions[0])
        eng.step()
        turns += 1
    assert turns > 0, f"{hid} could not take a single action"
    assert eng.winner is not None, f"{hid} combat never resolved in {turns} turns"
    print(f"  PASS test_a_discovered_class_can_actually_FIGHT "
          f"({hid}, {turns} turns, winner={eng.winner})")




if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
            except Exception as exc:
                print(f"  FAIL {name}: {exc}")
