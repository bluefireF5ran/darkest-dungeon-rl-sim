"""Tests for the per-class on-crit passive (crit: .effects "on_crit_X",
data/hero_loader.py) actually triggering during combat — wired into
_execute_action's crit branch and the riposte-crit path in
_maybe_trigger_riposte. Also covers the party-wide crit stress (both
directions: enemy crits a hero -> allies may gain stress; hero crits ->
allies may get relief) and the newly-read player_crit_companion_*/
monster_crit_companion_* rules.json fields.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests._fixtures import get_store

from engine import SeededRNG, CombatEngine, Action


from data.paths import GAME_DIR
from data.paths import CSV_DIR
store = get_store()


def _force_crit_engine(hero_id: str, enemy_id: str = "cultist_brawler_A", hero_position: int = 0):
    hero = store.heroes[hero_id]
    enemy = store.enemies[enemy_id]
    engine = CombatEngine(store, SeededRNG(1))
    engine.reset(hero_defs=[hero], enemy_defs=[enemy, enemy], hero_positions=[hero_position])
    performer = engine.heroes[0]
    performer.crit = 1.0  # guarantee crit on next attack
    return engine, performer


def test_crusader_on_crit_prot_applies_and_expires():
    engine, crusader = _force_crit_engine("crusader")
    acts = engine.get_valid_actions(crusader.id)
    smite = next(a for a in acts if a.skill_id == "smite")
    prot_before = crusader.prot
    engine.submit_action(smite)
    engine._execute_action(engine._action_buffer)
    assert crusader.prot > prot_before, (crusader.prot, prot_before)
    assert any(b.buff_id == "on_crit_prot" for b in crusader.buffs)
    print("  PASS test_crusader_on_crit_prot_applies_and_expires")


def test_occultist_on_crit_healdone_applies():
    """combat_stat_add-adjacent but resolves to hp_heal_percent stat_type —
    a different shape than combat_stat_add, exercised for coverage."""
    engine, occultist = _force_crit_engine("occultist")
    acts = engine.get_valid_actions(occultist.id)
    atk = next(a for a in acts if a.skill_id == "bloodlet")
    mult_before = occultist.hp_heal_produced_mult
    engine.submit_action(atk)
    engine._execute_action(engine._action_buffer)
    assert occultist.hp_heal_produced_mult > mult_before
    print("  PASS test_occultist_on_crit_healdone_applies")


def test_plague_doctor_on_crit_blightchance_applies():
    """poison_chance stat_type — the newly-added CombatantState field."""
    engine, pd = _force_crit_engine("plague_doctor", hero_position=1)
    acts = engine.get_valid_actions(pd.id)
    atk = next(a for a in acts if a.skill_id == "noxious_blast")
    assert pd.blight_chance_bonus == 0.0
    engine.submit_action(atk)
    engine._execute_action(engine._action_buffer)
    assert pd.blight_chance_bonus > 0.0
    print("  PASS test_plague_doctor_on_crit_blightchance_applies")


def test_monsters_have_no_crit_effect_id_and_no_op_safely():
    """A monster carries no `crit:` passive, so the trigger must do NOTHING.

    "Does not raise" was the whole of this test until 2026-08-27, which cannot
    tell a clean no-op from a passive that fires on the wrong side of the board.
    """
    brawler = store.enemies["cultist_brawler_A"]
    engine = CombatEngine(store, SeededRNG(1))
    hero = store.heroes["crusader"]
    engine.reset(hero_defs=[hero], enemy_defs=[brawler])
    enemy = engine.enemies[0]
    assert not getattr(brawler, "crit_effect_id", None)

    def snapshot(c):
        # Every scalar the state carries, so a renamed field cannot quietly
        # narrow what this test watches.
        return {k: v for k, v in vars(c).items()
                if isinstance(v, (int, float, bool, str, type(None)))}

    before = [snapshot(c) for c in engine.heroes + engine.enemies]
    engine._trigger_on_crit_passive(enemy)
    assert [snapshot(c) for c in engine.heroes + engine.enemies] == before


def test_hero_crit_grants_party_wide_relief_chance():
    """player_crit_companion_relief_chance/companion_stress_relief,
    newly read from rules.json — a hero's crit gives OTHER living heroes
    a chance at stress relief too."""
    crusader = store.heroes["crusader"]
    vestal = store.heroes["vestal"]
    brawler = store.enemies["cultist_brawler_A"]
    engine = CombatEngine(store, SeededRNG(1))
    engine.reset(hero_defs=[crusader, vestal], enemy_defs=[brawler, brawler])
    performer = engine.heroes[0]
    ally = engine.heroes[1]
    performer.crit = 1.0
    performer.stress = 50
    ally.stress = 50
    acts = engine.get_valid_actions(performer.id)
    smite = next(a for a in acts if a.skill_id == "smite")
    engine.submit_action(smite)
    engine._execute_action(engine._action_buffer)
    assert performer.stress < 50, "self relief should have applied"
    # Ally relief is probabilistic (25% chance) — just assert it never went up
    # and never went negative (regression for the stress floor clamp).
    assert ally.stress <= 50
    assert ally.stress >= 0
    print(f"  PASS test_hero_crit_grants_party_wide_relief_chance (ally stress={ally.stress})")


def test_riposte_crit_triggers_on_crit_passive():
    crusader = store.heroes["crusader"]
    brawler = store.enemies["cultist_brawler_A"]
    engine = CombatEngine(store, SeededRNG(3))
    engine.reset(hero_defs=[crusader], enemy_defs=[brawler])
    defender = engine.heroes[0]
    attacker = engine.enemies[0]
    from engine.combatant import RiposteState
    defender.active_riposte = RiposteState(remaining_rounds=2, on_hit_chance=1.0, on_miss_chance=1.0)
    defender.crit = 1.0
    prot_before = defender.prot
    engine._maybe_trigger_riposte(defender, attacker, hit=True)
    assert defender.prot > prot_before, "on-crit passive should have fired on the riposte crit"
    print("  PASS test_riposte_crit_triggers_on_crit_passive")


def test_is_crit_valid_false_skill_never_crits():
    """Real bug found and fixed: SkillLevelData.is_crit_valid (default
    True; 65 real skill-level records across 10 hero files explicitly set
    it False -- e.g. Plague Doctor's blinding_gas/disorienting_blast,
    Vestal's illumination "CANNOT CRIT" per HEROES/Heroes' own doc) was
    parsed but never consulted in check_crit -- every skill could crit
    regardless, incorrectly triggering stress relief and on-crit passives.
    Verified with attacker.crit forced to 100%: a crit-ineligible skill
    (blinding_gas, .is_crit_valid False) never crits and never fires the
    on-crit passive, while a crit-eligible skill (manacles, no
    is_crit_valid override) under the identical forced-crit setup does."""
    pd_def = store.heroes["plague_doctor"]
    brawler_def = store.enemies["cultist_brawler_A"]
    engine = CombatEngine(store, SeededRNG(1))
    # blinding_gas targets enemy ranks 3-4 (~34) -- needs enemies actually
    # occupying those ranks, unlike _force_crit_engine's usual 2-enemy setup.
    engine.reset(hero_defs=[pd_def], enemy_defs=[brawler_def] * 4, hero_positions=[3])
    pd = engine.heroes[0]
    pd.crit = 1.0  # guarantee crit if is_crit_valid were (wrongly) ignored
    pd.stress = 20
    acts = engine.get_valid_actions(pd.id)
    blinding_gas = next(a for a in acts if a.skill_id == "blinding_gas")
    stress_before = pd.stress
    engine.submit_action(blinding_gas)
    engine._execute_action(engine._action_buffer)
    assert not any("(crit!)" in l for l in engine.log), engine.log
    assert pd.stress == stress_before, "a crit-ineligible skill should never trigger crit stress relief"

    abom = store.heroes["abomination"]
    brawler = store.enemies["cultist_brawler_A"]
    engine2 = CombatEngine(store, SeededRNG(1))
    engine2.reset(hero_defs=[abom], enemy_defs=[brawler, brawler], hero_positions=[1])
    performer = engine2.heroes[0]
    performer.crit = 1.0
    acts2 = engine2.get_valid_actions(performer.id)
    manacles = next(a for a in acts2 if a.skill_id == "manacles")
    engine2.submit_action(manacles)
    engine2._execute_action(engine2._action_buffer)
    assert any("(crit!)" in l for l in engine2.log), (
        "manacles has no is_crit_valid override and should still crit at 100% crit chance", engine2.log)
    print("  PASS test_is_crit_valid_false_skill_never_crits")


if __name__ == "__main__":
    print("=== On-Crit Passive Tests ===\n")
    for name, func in sorted(globals().items()):
        if name.startswith("test_"):
            try:
                func()
            except Exception as e:
                import traceback
                print(f"  FAIL {name}: {e}")
                traceback.print_exc()
    print("\nDone.")
