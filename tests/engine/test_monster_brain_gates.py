"""The gates in `base.monster_brains.json` that the engine never read.

Sixth audit sweep. The brain file has two halves and both had holes:

**Skill selection.** A `skill_selection_desires` entry can carry a gate --
"only while 2-4 monsters are up", "only while fewer than 3 heroes are
marked", "only when nobody is guarded". The engine checked gates *inside the
branch of whichever desire type it first met them on*: `non_stunned_heroes_
min` in `stun_skill`, `monsters_size_*`/`hp_ratio_threshold`/
`first_initiative_only` in `heal_skill`. The shipped data puts the same keys
on `specific_skill` (20 distinct kinds, 130+ instances) and
`performing_turn_skill` (44), where nothing read them.

    21.8% of the hall/room monster pool carries a gate the engine ignored,
    and 5.2% of real enemy skill selections were drawn from a different
    weighting because of it (40 random-action expeditions, 2183 selections).

**Target selection.** `is_enemy_target_desire` was not read at all, and the
side filter tested `is_friendly_target_desire` alone. The two are NOT
complements: 25 desires set both, every one of them a brain's baseline
`random_target`. Dropping that baseline from enemy targeting turned "prefer
a marked hero 3:1" into "hit the marked hero, always" for the Swinetaur,
the Octotank and the Fishman Shaman.
"""

import collections
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests._fixtures import get_store

from data.types import TargetDesire
from engine import CombatEngine, SeededRNG


@pytest.fixture(scope="module")
def store():
    return get_store()


def _engine(store, enemy_ids, hero_ids=("crusader", "vestal", "arbalest", "hellion"),
            seed=1):
    engine = CombatEngine(store, SeededRNG(seed))
    engine.reset(hero_defs=[store.heroes[h] for h in hero_ids],
                 enemy_defs=[store.enemies[e] for e in enemy_ids],
                 surprise=(False, False), auto_resolve_to_hero=False)
    return engine


def _brain_of(engine, enemy):
    return engine._get_monster_brain(engine._get_enemy_def(enemy.id))


def _skill_freq(engine, enemy, trials=300):
    ed = engine._get_enemy_def(enemy.id)
    brain = engine._get_monster_brain(ed)
    counts = collections.Counter()
    for seed in range(trials):
        engine.rng = SeededRNG(seed)
        sk, _ = engine._select_enemy_skill(enemy, ed, brain)
        counts[sk] += 1
    return counts


# ── the data actually loads ──

def test_the_gates_reach_the_typed_records(store):
    """Spot-check one carrier of each family, so a loader regression fails
    here rather than silently reverting the behaviour below."""
    witch = store.monster_brains["cultist_witch_B"]
    inc = next(d for d in witch.skill_desires
               if d.combat_skill_id == "cultist_incantation"
               and d.type == "specific_skill")
    assert (inc.marked_heroes_min, inc.marked_heroes_max) == (0, 2)
    assert inc.virtued_heroes_max == 3

    bearer = store.monster_brains["skeleton_bearer"]
    rally = next(d for d in bearer.skill_desires
                 if d.combat_skill_id == "unholy_rally")
    assert rally.is_valid_when_corpse_enabled is False

    octo = store.monster_brains["octotank_A"]
    guard = next(d for d in octo.skill_desires
                 if d.combat_skill_id == "octo_guard" and d.type == "specific_skill")
    assert (guard.guarded_monsters_min, guard.guarded_monsters_max) == (0, 0)

    swine = store.monster_brains["swinetaur_A"]
    trot = next(d for d in swine.skill_desires if d.combat_skill_id == "trot_retreat")
    assert trot.monsters_min == 2


def test_the_two_side_flags_are_not_complements(store):
    """The premise of the target-side fix: 25 desires set BOTH, so reading
    `is_friendly_target_desire` alone drops them from enemy targeting."""
    both = [(b.id, d.type) for b in store.monster_brains.values()
            for d in b.target_desires
            if d.is_enemy_target_desire and d.is_friendly_target_desire]
    # 25 -> 71 on 2026-09-02, when the DLC monster brains started loading
    # (31 Crimson Court + 3 Shieldbreaker + 24 Color of Madness). The PROPERTY
    # is what this test is about and it is unchanged -- every one of them is
    # still a brain's baseline flat `random_target` -- so this is a deliberate
    # bump of a count, not a weakened assertion.
    assert len(both) == 71, len(both)
    assert {t for _, t in both} == {"random_target"}, set(t for _, t in both)


# ── skill-side gates ──

def test_a_gate_on_a_specific_skill_desire_is_honoured(store):
    """The Cultist Witch stops marking a party that is already marked --
    `marked_heroes_max: 2` on both her `cultist_incantation` desires. Before
    the fix she re-marked forever."""
    engine = _engine(store, ["cultist_witch_B"])
    witch = engine.enemies[0]

    clean = _skill_freq(engine, witch)
    for h in engine.heroes:
        h.is_marked = True
    saturated = _skill_freq(engine, witch)

    assert clean["cultist_incantation"] > 0
    assert saturated["cultist_incantation"] == 0, saturated
    # ...and she still acts: the push/pull desires are ungated.
    assert sum(saturated.values()) == 300
    assert set(saturated) <= {"cultist_push", "cultist_pull"}


def test_a_no_corpse_skill_is_never_selected(store):
    """`is_valid_when_corpse_enabled: false` marks a skill that belongs to
    the game's no-corpse ruleset. Corpses are always on here."""
    engine = _engine(store, ["skeleton_bearer_C"])
    counts = _skill_freq(engine, engine.enemies[0])
    assert counts["unholy_rally"] == 0, counts
    assert sum(counts.values()) == 300


def test_monsters_size_is_occupancy_not_a_head_count(store):
    """DECLARED READING. The Siren's conch_summon carries `monsters_min/max`
    AND `monsters_size_min/max` on one desire, so they cannot be the same
    quantity, and only one is named for size.

    Two size-2 monsters are a head count of 2 and an occupancy of 4."""
    big = [e for e in store.enemies.values()
           if e.size == 2 and e.can_be_damaged_directly][:2]
    engine = CombatEngine(store, SeededRNG(1))
    engine.reset(hero_defs=[store.heroes["crusader"]],
                 enemy_defs=big, surprise=(False, False),
                 auto_resolve_to_hero=False)
    enemy = engine.enemies[0]
    heroes = [h for h in engine.heroes if h.is_alive()]
    living = [e for e in engine.enemies if e.is_alive()]

    class D:
        type = "specific_skill"
        base_chance = 1.0
        combat_skill_id = ""
        is_valid_when_corpse_enabled = True
        first_initiative_only = False
        performing_turn_min = None
        monsters_min = monsters_max = None
        guarded_monsters_min = guarded_monsters_max = None
        hp_ratio_threshold = None
        heroes_min = non_stunned_heroes_min = None
        non_deaths_door_heroes_min = None
        marked_heroes_min = marked_heroes_max = non_marked_heroes_min = None
        virtued_heroes_max = None
        monsters_size_min = 3
        monsters_size_max = None

    d = D()
    assert engine._skill_desire_gates_pass(d, enemy, 0, heroes, living), \
        "occupancy 4 must clear a min of 3; a head count of 2 would not"
    d.monsters_size_min = None
    d.monsters_size_max = 3
    assert not engine._skill_desire_gates_pass(d, enemy, 0, heroes, living)
    # the head-count key still reads a head count
    d.monsters_size_max = None
    d.monsters_max = 2
    assert engine._skill_desire_gates_pass(d, enemy, 0, heroes, living)


def test_a_guard_is_not_stacked_on_a_guard(store):
    """`guarded_monsters_max: 0` -- the Octotank guards while nobody is
    guarded, and stops once somebody is."""
    engine = _engine(store, ["octotank_A", "cultist_brawler_A"])
    octo = engine.enemies[0]
    assert _skill_freq(engine, octo)["octo_guard"] > 0

    engine.enemies[1].guard_source_id = octo.id
    octo.guard_target_id = engine.enemies[1].id
    assert _skill_freq(engine, octo)["octo_guard"] == 0


def test_a_heal_desire_with_no_threshold_still_needs_a_wounded_ally(store):
    """Regression pin for the one behaviour the hoist could have dropped:
    `heal_skill` with no `hp_ratio_threshold` used to default to 1.0, i.e.
    "somebody is hurt". The shared gate only fires when the key is present,
    so the heal branch keeps that default itself."""
    engine = _engine(store, ["fishman_shaman_A", "cultist_brawler_A"])
    shaman = engine.enemies[0]
    healer_desires = [d for d in _brain_of(engine, shaman).skill_desires
                      if d.type == "heal_skill"]
    assert healer_desires and healer_desires[0].hp_ratio_threshold is not None

    # its threshold IS present (0.7), so drive the general case directly
    heroes = [h for h in engine.heroes if h.is_alive()]
    living = [e for e in engine.enemies if e.is_alive()]
    d = healer_desires[0]
    assert not engine._skill_desire_gates_pass(d, shaman, 0, heroes, living)
    engine.enemies[1].hp = 1
    assert engine._skill_desire_gates_pass(d, shaman, 0, heroes, living)


# ── target-side ──

def test_the_baseline_random_desire_reaches_enemy_targeting(store):
    """The Swinetaur's only flat desire sets BOTH side flags. Filtering on
    the friendly flag alone dropped it, and the marked hero then absorbed
    100% of `pig_spear` instead of 3 in 4 (random 1.0 + marked 2.0)."""
    engine = _engine(store, ["swinetaur_A"])
    brain = _brain_of(engine, engine.enemies[0])
    engine.heroes[1].is_marked = True

    counts = collections.Counter()
    for seed in range(1200):
        engine.rng = SeededRNG(seed)
        counts[engine._select_enemy_target("pig_spear", brain, engine.heroes).id] += 1

    marked = counts[engine.heroes[1].id]
    assert marked < 1200, "the marked hero must not absorb every attack"
    # 3 : 1 : 1 : 1 => 50%
    assert 0.42 < marked / 1200 < 0.58, counts


def test_a_stun_is_not_aimed_at_an_already_stunned_hero(store):
    """`can_target_stunned: false` -- the desire abstains for a stunned
    target while the brain's flat desire keeps that hero reachable, so the
    gate is a preference and not an immunity.

    Driven through a two-desire stub rather than a shipped brain, because
    every real carrier ALSO ships the second mechanism tested below."""
    engine = _engine(store, ["swinetaur_A"])
    heroes = [h for h in engine.heroes if h.is_alive()]
    victim = heroes[2]

    class Stub:
        target_desires = [
            TargetDesire(type="random_target", base_chance=1.0),
            TargetDesire(type="resistance_target", base_chance=9.0,
                         resistance_type_id="stun", can_target_stunned=False),
        ]

    def share():
        counts = collections.Counter()
        for seed in range(800):
            engine.rng = SeededRNG(seed)
            counts[engine._select_enemy_target("bull_rush", Stub(), heroes).id] += 1
        return counts

    free = share()
    victim.is_stunned = True
    stunned = share()
    assert stunned[victim.id] < free[victim.id], (free, stunned)
    assert stunned[victim.id] > 0, "the flat desire still keeps them reachable"


def test_the_two_avoid_stunning_mechanisms_stack(store):
    """The Swinetaur carries both: `can_target_stunned: false` on the
    resistance desire AND a `stunned_target` desire at **-1000**. A negative
    weight is the data's own way of writing "never", and `weighted_choice`
    skips any weight <= 0 -- so with both present a stunned hero really is
    unreachable by `bull_rush`. That is the shipped intent, not a clamp of
    ours."""
    engine = _engine(store, ["swinetaur_A"])
    brain = _brain_of(engine, engine.enemies[0])
    res = next(d for d in brain.target_desires if d.type == "resistance_target")
    veto = next(d for d in brain.target_desires if d.type == "stunned_target")
    assert res.can_target_stunned is False and veto.base_chance == -1000.0

    heroes = [h for h in engine.heroes if h.is_alive()]
    heroes[2].is_stunned = True
    picks = set()
    for seed in range(400):
        engine.rng = SeededRNG(seed)
        picks.add(engine._select_enemy_target("bull_rush", brain, heroes).id)
    assert heroes[2].id not in picks


def test_an_exclusive_desire_suppresses_the_others(store):
    """The Fishman Shaman's heal is `is_exclusive_desire: true` on
    `health_target`, so it picks the most wounded ally outright instead of
    competing with the flat friendly random weight."""
    engine = _engine(store, ["fishman_shaman_A", "cultist_brawler_A",
                             "cultist_brawler_A"])
    brain = _brain_of(engine, engine.enemies[0])
    hurt = engine.enemies[2]
    hurt.hp = 1

    counts = collections.Counter()
    for seed in range(400):
        engine.rng = SeededRNG(seed)
        t = engine._select_enemy_target("shaman_heal", brain,
                                        [e for e in engine.enemies if e.is_alive()],
                                        is_friendly=True)
        counts[t.id] += 1
    assert counts[hurt.id] / 400 > 0.75, counts


def test_the_last_hero_gate_loads_and_cannot_bite_here(store):
    """`can_target_last_hero` means the last LIVING hero -- the Hag's
    `hag_grab` carries it, and putting the last hero in the cauldron would
    leave nobody able to act.

    It is read and applied, but in a weighted selection over the party it can
    never change an outcome: when one hero is left there is exactly one
    candidate. Recorded so nobody "fixes" it into something that does bite."""
    engine = _engine(store, ["swinetaur_A"])
    brain = _brain_of(engine, engine.enemies[0])
    res = next(d for d in brain.target_desires if d.type == "resistance_target")
    assert res.can_target_last_hero is False

    for h in engine.heroes[1:]:
        h.hp = 0
        h.is_dead = True
    alive = [h for h in engine.heroes if h.is_alive()]
    assert len(alive) == 1
    engine.rng = SeededRNG(3)
    assert engine._select_enemy_target("bull_rush", brain, alive) is alive[0]


def test_a_guardian_does_not_guard_a_guarded_ally(store):
    """`is_guarded: false` / `is_guarding: false` on the Skeleton Defender's
    friendly desire for `shield_defend`."""
    engine = _engine(store, ["skeleton_defender_B", "cultist_brawler_A",
                             "cultist_brawler_A"])
    defender = engine.enemies[0]
    brain = _brain_of(engine, defender)
    already, free = engine.enemies[1], engine.enemies[2]
    already.guard_source_id = 999

    counts = collections.Counter()
    for seed in range(300):
        engine.rng = SeededRNG(seed)
        t = engine._select_enemy_target("shield_defend", brain, [already, free],
                                        is_friendly=True)
        counts[t.id] += 1
    assert counts[already.id] == 0, counts
    assert counts[free.id] == 300


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
