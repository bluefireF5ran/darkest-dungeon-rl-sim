"""Tests for real Flagellant/Abomination mechanics found and fixed in the
same audit pass: heal skills silently dropping their own .effect list
(fixed generically in engine/combat_engine.py's is_heal branch — this file
covers the two data-layer fields that gate specific real heals), a
previously-unparsed .heal_percent/.dotHpHeal pair (Flagellant's Redeem/
Reclaim), a group-targeting bug that silently applied "performer_group_
other" effects to only ONE other ally instead of all of them (confirmed to
affect Abomination's Transform too), and two entirely unwired hero-specific
trigger systems (deaths_door's .enter_effects, and the standalone
death_reaction: record type) — both real, both Flagellant-only in practice.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests._fixtures import get_store

from engine import SeededRNG, CombatEngine, Action
from engine.combatant import DotInstance


from data.paths import GAME_DIR
from data.paths import CSV_DIR
store = get_store()


def test_transform_party_stress_applies_to_all_other_allies_not_just_one():
    """Real bug found and fixed: _get_effect_target's "performer_group_
    other" branch only ever returned ONE ally (the first found alive), so
    Abomination's Transform (human->beast: "beast_stress_party", +8 stress
    to every OTHER party member) silently only ever stressed 1 of the
    other 3 heroes in a full party, not all of them."""
    abom = store.heroes["abomination"]
    crusader = store.heroes["crusader"]
    vestal = store.heroes["vestal"]
    houndmaster = store.heroes["houndmaster"]
    brawler = store.enemies["cultist_brawler_A"]
    engine = CombatEngine(store, SeededRNG(3))
    engine.reset(hero_defs=[crusader, abom, vestal, houndmaster], enemy_defs=[brawler])
    c, a, v, h = engine.heroes
    stress_before = {x.id: x.stress for x in (c, v, h)}

    engine._execute_action(Action(a.id, "transform", -1))

    for other in (c, v, h):
        assert other.stress == stress_before[other.id] + 8, (
            other.name, stress_before[other.id], other.stress)
    print("  PASS test_transform_party_stress_applies_to_all_other_allies_not_just_one")


def test_redeem_heals_ally_and_self_via_heal_percent():
    """Real bug found and fixed: EffectDefinition never parsed .heal_percent
    at all (a percent-of-max-HP heal, distinct from the flat .heal_min/
    heal_max) -- Redeem's real heal ("Flag Super Heal N" targets the ally,
    "Flag HealSelf N" targets Flagellant himself) is entirely percent-
    based, so before this fix Redeem -- "arguably Flagellant's strongest
    skill" per HEROES/Heroes' own doc -- healed NOTHING at all, only
    applying its self-debuff."""
    flag = store.heroes["flagellant"]
    crusader = store.heroes["crusader"]
    brawler = store.enemies["cultist_brawler_A"]
    engine = CombatEngine(store, SeededRNG(1))
    engine.reset(hero_defs=[flag, crusader], enemy_defs=[brawler])
    f, c = engine.heroes
    f.hp = 10
    c.hp = 20
    engine._execute_action(Action(f.id, "redeem", c.position))
    assert c.hp > 20, ("Flag Super Heal (ally heal) never applied", c.hp)
    assert f.hp > 10, ("Flag HealSelf (self heal) never applied", f.hp)
    print("  PASS test_redeem_heals_ally_and_self_via_heal_percent")


def test_reclaim_heals_over_time_via_dot_hp_heal():
    """Real bug found and fixed: .dotHpHeal (Reclaim's real heal-over-time/
    regeneration mechanism, "Flag Healdot N") wasn't parsed at all --
    Reclaim only ever applied its self-Bleed recoil ("Flag Self Wound N"),
    never any actual healing."""
    flag = store.heroes["flagellant"]
    crusader = store.heroes["crusader"]
    brawler = store.enemies["cultist_brawler_A"]
    engine = CombatEngine(store, SeededRNG(1))
    engine.reset(hero_defs=[flag, crusader], enemy_defs=[brawler])
    f, c = engine.heroes
    c.hp = 20
    engine._execute_action(Action(f.id, "reclaim", c.position))
    assert c.heal_dots, "reclaim should inflict a heal-over-time on its target"
    hp_before = c.hp
    engine._tick_heal_dots(c)
    assert c.hp > hp_before, ("Flag Healdot tick never healed", hp_before, c.hp)
    print("  PASS test_reclaim_heals_over_time_via_dot_hp_heal")


def test_flagellant_death_door_enter_heals_the_rest_of_the_party():
    """Real gap found and fixed: deaths_door's real .enter_effects/
    .enter_effect_round_cooldown fields (Flagellant-only:
    "flagellant_death_door_enter", a 10%-max-HP heal for every OTHER party
    member) were never read by HeroLoader or consulted anywhere in the
    engine -- entering Death's Door triggered only the generic debuffs,
    never this hero-specific reaction."""
    flag = store.heroes["flagellant"]
    crusader = store.heroes["crusader"]
    vestal = store.heroes["vestal"]
    brawler = store.enemies["cultist_brawler_A"]
    engine = CombatEngine(store, SeededRNG(1))
    engine.reset(hero_defs=[flag, crusader, vestal], enemy_defs=[brawler])
    f, c, v = engine.heroes
    c.hp = 1
    v.hp = 1
    f.hp = 5
    engine._handle_deaths_door(f, f)
    assert f.is_deaths_door
    assert c.hp > 1, ("death's-door-enter party heal never reached the Crusader", c.hp)
    assert v.hp > 1, ("death's-door-enter party heal never reached the Vestal", v.hp)
    print("  PASS test_flagellant_death_door_enter_heals_the_rest_of_the_party")


def test_flagellant_death_door_enter_respects_round_cooldown():
    """The real .enter_effect_round_cooldown (1) should block an immediate
    same-round re-trigger."""
    flag = store.heroes["flagellant"]
    crusader = store.heroes["crusader"]
    brawler = store.enemies["cultist_brawler_A"]
    engine = CombatEngine(store, SeededRNG(1))
    engine.reset(hero_defs=[flag, crusader], enemy_defs=[brawler])
    f, c = engine.heroes
    f.hp = 5
    engine._handle_deaths_door(f, f)
    f.is_deaths_door = False  # simulate leaving and re-entering within the same round
    c.hp = 1
    engine._handle_deaths_door(f, f)
    assert c.hp == 1, "re-entering within the cooldown window should not re-trigger the heal"
    print("  PASS test_flagellant_death_door_enter_respects_round_cooldown")


def test_flagellant_death_reaction_stuns_enemies_and_heals_allies():
    """Real gap found and fixed: the standalone death_reaction: record type
    (Flagellant-only: "Flag Last Gift" heals the rest of the party 10 HP,
    "Flag Last Insult" stuns every enemy at 125% chance) was never parsed
    or consulted anywhere -- his real "On Death" reactions per HEROES/
    Heroes' own doc did nothing at all."""
    flag = store.heroes["flagellant"]
    crusader = store.heroes["crusader"]
    brawler = store.enemies["cultist_brawler_A"]
    engine = CombatEngine(store, SeededRNG(5))
    engine.reset(hero_defs=[flag, crusader], enemy_defs=[brawler, brawler])
    f, c = engine.heroes
    for e in engine.enemies:
        e.stun_resist = -10.0  # force the stun-resist roll to always fail
    c.hp = 1
    f.is_deaths_door = True
    f.death_blow_resist = -10.0  # force the deathblow roll to succeed deterministically
    engine._handle_deaths_door(f, f)
    assert f.is_dead
    assert c.hp > 1, "Flag Last Gift (party heal on death) never applied"
    assert all(e.is_stunned for e in engine.enemies), "Flag Last Insult (stun all enemies) never applied"
    print("  PASS test_flagellant_death_reaction_stuns_enemies_and_heals_allies")


if __name__ == "__main__":
    print("=== Flagellant / group-effect fidelity tests ===\n")
    for name, func in sorted(globals().items()):
        if name.startswith("test_"):
            try:
                func()
                print()
            except Exception as e:
                import traceback
                print(f"  FAIL {name}: {e}")
                traceback.print_exc()
                print()
    print("Done.")
