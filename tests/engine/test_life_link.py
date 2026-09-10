"""A boss's adds die with the boss — `life_link: .base_class "X"`.

177 monsters carry it across 19 parents, and nothing read it until 2026-08-21.
So **killing the boss did not end the fight**: the party stood there clearing
summons that no longer had a summoner.

That went from theoretical to live two commits earlier. Every summoning boss in
the game summons monsters that are life-linked back to it — Necromancer
skeletons, Siren fishmen, the Drowned Captain's anchor, the Brigand Cannon's
crew, the Collector's collected heroes — and `summon_count` had just started
working. The static pairs are in the shipped comps too: the Prophet's pews, the
Hag's cauldron, the Crow's nest.

**The link is inert unless the parent is on the board**, and that guard is the
whole reading rather than a safety margin. `skeleton_arbalist_A` is linked to
`necromancer` and fills ordinary Ruins corridors with no Necromancer anywhere:
of the 1266 comp slots holding a linked add, **1239 have no parent present**,
and all 27 that do are boss or named comps.
"""

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests._fixtures import get_store

from engine.combat_engine import CombatEngine
from engine.rng import SeededRNG


@pytest.fixture(scope="module")
def store():
    return get_store()


def _engine(store, enemy_ids, heroes=("crusader", "vestal"), seed=1):
    engine = CombatEngine(store, SeededRNG(seed))
    engine.reset(hero_defs=[store.heroes[h] for h in heroes],
                 enemy_defs=[store.enemies[e] for e in enemy_ids],
                 surprise=(False, False), auto_resolve_to_hero=False)
    return engine


def _slay(engine, victim):
    victim.hp = 0
    engine._handle_deaths_door(victim, engine.heroes[0])


def _alive(engine):
    return [e.definition_id for e in engine.enemies if e.is_alive()]


# ── the data ──

def test_the_link_is_parsed_and_points_at_a_directory(store):
    """Matched on `base_class`, the monster's DIRECTORY, not on a `_[A-E]`
    suffix stripped off the id — the suffix convention is not universal
    (`brigand_barrel_D`, `com_crocodile`) and a regex that is wrong here
    silently mis-links a boss to its adds."""
    assert store.enemies["skeleton_militia_B"].life_link_base_class == "necromancer"
    assert store.enemies["skeleton_militia_B"].base_class == "skeleton_militia"
    assert store.enemies["necromancer_B"].base_class == "necromancer"
    assert store.enemies["necromancer_B"].life_link_base_class is None
    assert store.enemies["cultist_brawler_A"].life_link_base_class is None


def test_every_summoning_boss_summons_something_linked_back(store):
    """Why this had to land right after summoning did. If a patch ever adds a
    summoner whose adds are NOT linked, that is a fight which no longer ends
    when the boss dies, and this test is where it shows up."""
    checked = 0
    for boss_id in ("necromancer_B", "drowned_captain_A", "brigand_cannon_A",
                    "siren_A"):
        ed = store.enemies[boss_id]
        summoned = set()
        for sk in ed.skills:
            for lv in sk.levels.values():
                for name in (lv.effects or []):
                    eff = store.effects.get(name)
                    if eff and eff.summon_monster_ids:
                        summoned.update(eff.summon_monster_ids)
        assert summoned, boss_id
        linked = [s for s in summoned
                  if store.enemies[s].life_link_base_class == ed.base_class]
        assert linked, (boss_id, sorted(summoned))
        checked += 1
    assert checked == 4


# ── the behaviour ──

def test_killing_the_necromancer_clears_his_skeletons(store):
    """The headline, and the one the summoning fix created. Note the skeletons
    here are SUMMONED mid-fight rather than listed in the comp, which is
    exactly how the real fight goes."""
    engine = _engine(store, ["necromancer_B"])
    for _ in range(3):
        engine._summon_from_effect(store.effects["NecroSummon 2"])
    assert len(_alive(engine)) == 4, _alive(engine)
    _slay(engine, engine.enemies[0])
    assert _alive(engine) == [], _alive(engine)


def test_the_pews_go_with_the_prophet(store):
    """A static pair, straight out of a shipped boss comp, so this does not
    depend on summoning working."""
    engine = _engine(store, ["pew_small_A", "pew_medium_A", "prophet_A"])
    prophet = next(e for e in engine.enemies if e.definition_id == "prophet_A")
    _slay(engine, prophet)
    assert _alive(engine) == []


def test_a_skeleton_in_an_ordinary_crypt_is_unaffected(store):
    """The guard, and the case that rules out the naive reading. Without the
    parent-present check, the first skeleton kill in any Ruins corridor would
    wipe the rest of the group."""
    engine = _engine(store, ["skeleton_militia_B", "skeleton_militia_B",
                             "skeleton_common_B"])
    _slay(engine, engine.enemies[0])
    assert len(_alive(engine)) == 2, _alive(engine)


def test_killing_an_add_does_not_touch_the_boss(store):
    """The link is one-directional: adds die with the parent, never the
    reverse."""
    engine = _engine(store, ["skeleton_militia_B", "necromancer_B"])
    _slay(engine, engine.enemies[0])
    assert "necromancer_B" in _alive(engine)


def test_the_link_names_a_DIRECTORY_so_tiers_do_cross(store):
    """A Veteran Necromancer clears an Apprentice skeleton too, because
    `life_link` names a base class and both variants live in the same
    directory. That is the faithful reading -- the key carries no variant --
    and the reason it costs nothing is checked below rather than assumed.

    A first draft of this test asserted the opposite and was wrong; the code
    was right."""
    engine = _engine(store, ["necromancer_B", "skeleton_militia_A"])
    _slay(engine, engine.enemies[0])
    assert _alive(engine) == []


def test_no_shipped_comp_can_ever_expose_that(store):
    """The premise the test above rests on. 16 of 1105 comps mix variant tiers
    -- all of them Vvulf-adjacent brigand groups pairing `_D` with `_C` -- and
    **not one contains a parent and a linked add together**. So the shipped
    game never puts a boss on a board with an add of a different tier, and the
    directory match cannot misfire in play."""
    exposed = []
    for comp in store.enemy_comps:
        defs = [store.enemies[e] for e in comp.enemy_ids if e in store.enemies]
        if not defs:
            continue
        tiers = {re.search(r"_([A-E])$", d.id).group(1)
                 for d in defs if re.search(r"_([A-E])$", d.id)}
        if len(tiers) < 2:
            continue
        bases = {d.base_class for d in defs}
        if any(d.life_link_base_class in bases for d in defs):
            exposed.append(comp.enemy_ids)
    assert exposed == [], exposed


def test_a_linked_add_leaves_no_corpse(store):
    """It is removed, not slain — no killing blow, so `_leaves_corpse` has no
    say and nothing should be left blocking the rank."""
    engine = _engine(store, ["necromancer_B"])
    engine._summon_from_effect(store.effects["NecroSummon 2"])
    add = engine.enemies[1]
    _slay(engine, engine.enemies[0])
    assert add.is_dead and not add.is_corpse


def test_a_boss_bled_to_death_still_takes_its_adds(store):
    """The DoT path is a separate death site and had to be wired separately."""
    engine = _engine(store, ["necromancer_B"])
    engine._summon_from_effect(store.effects["NecroSummon 2"])
    necro = engine.enemies[0]
    necro.hp = 0
    engine._check_deaths_door_dot(necro)
    assert necro.is_dead
    assert _alive(engine) == []


def test_the_ranks_close_after_the_adds_vanish(store):
    """Same invariant every other formation change keeps: gaps only ever sit
    at the back."""
    engine = _engine(store, ["skeleton_militia_B", "necromancer_B",
                             "skeleton_militia_B"])
    necro = next(e for e in engine.enemies if e.definition_id == "necromancer_B")
    engine.enemies[0].hp = engine.enemies[0].max_hp
    _slay(engine, necro)
    alive = [e for e in engine.enemies if e.is_alive() or e.is_corpse]
    positions = sorted(p for e in alive for p in e.occupied_positions)
    assert positions == list(range(len(positions))), positions


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
