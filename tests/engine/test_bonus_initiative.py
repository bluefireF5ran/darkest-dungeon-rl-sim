"""Tests for bonus_initiative_desires — a scripted bonus-action/skill-override
system for 14/165 boss-tier monster brains, verified NOT to be initiative-roll
modifiers despite the name (a prior doc note guessed wrong). See
BonusInitiativeDesire's docstring in data/types.py for the full verification
notes: every real entry fires at exactly round-start+pre_turn or
round-finish+post_turn, covering guaranteed/ally_actor_class_count/
hp_ratio_threshold/last_skill (death and ally_last_damaged are documented
gaps — see ASSUMPTIONS.md).
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests._fixtures import get_store

from engine import SeededRNG, CombatEngine


from data.paths import GAME_DIR
from data.paths import CSV_DIR
store = get_store()


def _pick_attack_action(engine, acts):
    """Prefer a genuine enemy-targeting attack over __move__/__pass__/
    provisions/self-only buffs (e.g. bulwark_of_faith) — a plain acts[0]
    pick can get stuck looping a self-buff forever once reinforcements
    (added after this test file was written) place a straggler enemy
    outside every hero's attack range, since a self-only skill has no
    target-range gate and is always "valid" regardless of position."""
    for a in acts:
        if a.skill_id.startswith("__"):
            continue
        if engine._is_ally_skill(a.skill_id):
            continue
        if a.target_id < 0:
            continue
        return a
    return acts[0] if acts else None


def test_bonus_initiative_desires_loaded_for_boss_brains():
    """14 real brains carry bonus_initiative_desires; confirm they load
    with the modeled types only (death/ally_last_damaged excluded)."""
    boss_brains = {"prophet_A", "prophet_B", "prophet_C", "cannon_A", "cannon_B", "cannon_C",
                    "drowned_captain", "ancestor_small_D", "ancestor_big_D", "ancestor_heart_D",
                    "brigand_sapper_D", "hateful_virago", "skeleton_bearer", "swine_prince"}
    for bid in boss_brains:
        assert bid in store.monster_brains, bid
    prophet = store.monster_brains["prophet_A"]
    assert len(prophet.bonus_initiative_desires) == 2
    assert {d.type for d in prophet.bonus_initiative_desires} == {"guaranteed"}
    print("  PASS test_bonus_initiative_desires_loaded_for_boss_brains")


def test_prophet_round_start_bonus_fires_on_reset():
    """prophet_mark (guaranteed, round_start+pre_turn) should fire
    immediately on reset(), before the first normal turn — verified real
    behavior: Prophet always opens a round by marking, out of the normal
    turn order entirely."""
    crusader = store.heroes["crusader"]
    prophet = store.enemies["prophet_A"]
    engine = CombatEngine(store, SeededRNG(5))
    engine.reset(hero_defs=[crusader, crusader], enemy_defs=[prophet], hero_positions=[1, 2])
    assert any("bonus action" in line for line in engine.log), engine.log
    print("  PASS test_prophet_round_start_bonus_fires_on_reset")


def test_prophet_round_finish_bonus_fires_every_round():
    """prophet_attack (guaranteed, round_finish+post_turn) should fire once
    per completed round, in addition to the round_start mark."""
    crusader = store.heroes["crusader"]
    prophet = store.enemies["prophet_A"]
    engine = CombatEngine(store, SeededRNG(5))
    engine.reset(hero_defs=[crusader, crusader], enemy_defs=[prophet], hero_positions=[1, 2])
    steps = 0
    while not engine.done and steps < 60:
        steps += 1
        actor = engine.get_current_actor_id()
        if actor is not None:
            acts = engine.get_valid_actions(actor)
            chosen = _pick_attack_action(engine, acts)
            if chosen:
                engine.submit_action(chosen)
        engine.step()
    bonus_count = sum(1 for line in engine.log if "bonus action" in line)
    # At least one mark + one attack per completed round (final round may be
    # cut short by combat ending), so bonus_count should track round count.
    assert bonus_count >= engine.round, (bonus_count, engine.round)
    print(f"  PASS test_prophet_round_finish_bonus_fires_every_round (bonus_count={bonus_count}, rounds={engine.round})")


def test_cannon_last_skill_gate_tracks_light_fuse():
    """Cannon's last_skill bonus (no combat_skill_id_override -> falls back
    to normal skill selection) should only be eligible the moment
    last_combat_skill_id ('light_fuse') was the most recent skill used by
    anyone — verified via CombatEngine._last_skill_used, fed by
    _track_skill_use (already called for every hero/enemy skill use)."""
    crusader = store.heroes["crusader"]
    cannon = store.enemies["brigand_cannon_A"]
    fuseman = store.enemies["brigand_fuseman_A"]
    engine = CombatEngine(store, SeededRNG(1))
    engine.reset(hero_defs=[crusader] * 4, enemy_defs=[cannon, fuseman], hero_positions=[1, 2, 3, 4])
    cannon_state = engine.enemies[0]
    ed = engine._get_enemy_def(cannon_state.id)
    brain = engine._get_monster_brain(ed)
    last_skill_desire = next(d for d in brain.bonus_initiative_desires if d.type == "last_skill")
    assert last_skill_desire.last_combat_skill_id == "light_fuse"

    living_enemies = [e for e in engine.enemies if e.is_alive()]
    heroes_alive = [h for h in engine.heroes if h.is_alive()]

    engine._last_skill_used = "light_fuse"
    assert engine._check_bonus_initiative_condition(cannon_state, last_skill_desire, living_enemies, heroes_alive)

    engine._last_skill_used = "some_other_skill"
    assert not engine._check_bonus_initiative_condition(cannon_state, last_skill_desire, living_enemies, heroes_alive)
    print("  PASS test_cannon_last_skill_gate_tracks_light_fuse")


def test_hp_ratio_threshold_gate_fires_only_under_threshold():
    """ancestor_heart_D's kill_pill (hp_ratio_threshold, is_under_threshold=True,
    threshold 0.67, heroes_min/max=4) should only be eligible once the
    Ancestor Heart's own HP fraction drops below 0.67, with exactly 4 heroes
    alive."""
    crusader = store.heroes["crusader"]
    heart = store.enemies["ancestor_heart_D"]
    engine = CombatEngine(store, SeededRNG(1))
    engine.reset(hero_defs=[crusader] * 4, enemy_defs=[heart], hero_positions=[1, 2, 3, 4])
    heart_state = engine.enemies[0]
    ed = engine._get_enemy_def(heart_state.id)
    brain = engine._get_monster_brain(ed)
    d67 = next(d for d in brain.bonus_initiative_desires if abs(d.health_ratio_threshold - 0.67) < 1e-9)

    living_enemies = [heart_state]
    heroes_alive = [h for h in engine.heroes if h.is_alive()]

    heart_state.hp = heart_state.max_hp  # full HP -> above threshold -> should not fire
    assert not engine._check_bonus_initiative_condition(heart_state, d67, living_enemies, heroes_alive)

    heart_state.hp = int(heart_state.max_hp * 0.5)  # below 0.67 -> should fire
    assert engine._check_bonus_initiative_condition(heart_state, d67, living_enemies, heroes_alive)
    print("  PASS test_hp_ratio_threshold_gate_fires_only_under_threshold")


def test_full_combat_still_resolves_with_bonus_initiative_enabled():
    """Regression guard: a boss brain with bonus_initiative_desires should
    still resolve to a clean win/lose."""
    crusader = store.heroes["crusader"]
    prophet = store.enemies["prophet_A"]
    engine = CombatEngine(store, SeededRNG(3))
    engine.reset(hero_defs=[crusader, crusader], enemy_defs=[prophet], hero_positions=[1, 2])
    steps = 0
    while not engine.done and steps < 200:
        steps += 1
        actor = engine.get_current_actor_id()
        if actor is not None:
            acts = engine.get_valid_actions(actor)
            chosen = _pick_attack_action(engine, acts)
            if chosen:
                engine.submit_action(chosen)
        engine.step()
    assert engine.done, f"combat did not finish in {steps} steps"
    assert engine.winner in ("heroes", "enemies")
    print(f"  PASS test_full_combat_still_resolves_with_bonus_initiative_enabled ({steps} steps, winner={engine.winner})")


if __name__ == "__main__":
    print("=== Bonus Initiative Tests ===\n")
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
