"""Tests for Abomination's transform (mode switching) — and the inline
combat_stat_buff fix it required.

Chased through three real bugs to get here, all verified against
D:\\...\\DarkestDungeon\\effects\\base.effects.darkest and
heroes\\abomination\\abomination.info.darkest:

1. Inline stat-buff effects (damage_low_multiply, speed_rating_add, etc. —
   as opposed to .buff_ids references) never actually applied to any stat;
   _resolve_effect only ever appended a decorative ActiveBuff marker whose
   buff_id had no store.buffs entry, so _apply_buff_to_stats was never
   called for it. Affects every inline combat_stat_buff effect in the game,
   not just Abomination's.
2. .speed_add never appears as a literal key in the real file — the real
   key is .speed_rating_add (97 occurrences, 0 for bare .speed_add).
3. .valid_modes human beast (multi-value, unquoted, space-separated) was
   kept as one unsplit string "human beast" instead of being split, so
   `"human" in skill.valid_modes` was False even for transform itself.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests._fixtures import get_store

from engine import SeededRNG, CombatEngine, Action


from data.paths import GAME_DIR
from data.paths import CSV_DIR
store = get_store()


def _fresh_engine(seed=2):
    engine = CombatEngine(store, SeededRNG(seed))
    abom = store.heroes["abomination"]
    brawler = store.enemies["cultist_brawler_A"]
    engine.reset(hero_defs=[abom], enemy_defs=[brawler, brawler], hero_positions=[1])
    return engine


def test_transform_valid_modes_parsed_as_two_separate_entries():
    """Regression test for .valid_modes human beast collapsing to one
    unsplit string ["human beast"] instead of ["human", "beast"]."""
    abom = store.heroes["abomination"]
    sk = next(s for s in abom.skills if s.id == "transform")
    assert sk.levels[4].valid_modes == ["human", "beast"], sk.levels[4].valid_modes
    print("  PASS test_transform_valid_modes_parsed_as_two_separate_entries")


def test_transform_switches_mode_both_directions():
    engine = _fresh_engine()
    performer = engine.heroes[0]
    assert performer.mode == "human"

    engine.submit_action(Action(performer.id, "transform", -1))
    engine.step()
    assert performer.mode == "beast", "transform should flip human -> beast"

    # transform is a free action (is_continue_turn) now that step() honors
    # it -- it no longer auto-advances the round the way a normal action
    # would have (see test_transform_is_free_action_does_not_advance_turn).
    # Its real per_turn_limit=1 correctly blocks a second use within the
    # same round (test_transform_respects_per_turn_limit), matching how a
    # hero only gets one turn per round in-game -- so the second (reverse)
    # transform needs a new round, same as it would for a real player.
    engine.round += 1
    engine.submit_action(Action(performer.id, "transform", -1))
    engine.step()
    assert performer.mode == "human", "transform should flip beast -> human"
    print("  PASS test_transform_switches_mode_both_directions")


def test_transform_applies_real_inline_stat_buff():
    """Regression test for inline combat_stat_buff effects being fully
    inert (decorative ActiveBuff marker only, never applied to any stat).
    beast_buff_5's speed_rating_add=5 should actually raise .speed.
    """
    engine = _fresh_engine()
    performer = engine.heroes[0]
    speed_before = performer.speed

    engine.submit_action(Action(performer.id, "transform", -1))
    engine.step()

    assert performer.speed == speed_before + 5, (speed_before, performer.speed)
    tracked_ids = {b.buff_id for b in performer.buffs}
    assert "__inline__beast_buff_5__speed" in tracked_ids, tracked_ids
    print(f"  PASS test_transform_applies_real_inline_stat_buff (speed {speed_before} -> {performer.speed})")


def test_inline_stat_buff_reverses_on_duration_expiry():
    """beast_buff_5 has a real 4-round duration — the speed bonus should
    disappear again once it expires, via the generic _tick_buff_durations
    reversal (not just apply-and-forget)."""
    engine = _fresh_engine()
    performer = engine.heroes[0]
    speed_before = performer.speed

    engine.submit_action(Action(performer.id, "transform", -1))
    engine.step()
    assert performer.speed == speed_before + 5

    for _ in range(4):
        engine._tick_buff_durations()

    assert performer.speed == speed_before, (speed_before, performer.speed)
    print("  PASS test_inline_stat_buff_reverses_on_duration_expiry")


def test_transform_respects_per_turn_limit():
    """transform has a real per_turn_limit=1 — calling it twice without
    advancing to a new round should only switch mode once."""
    engine = _fresh_engine()
    performer = engine.heroes[0]
    action = Action(performer.id, "transform", -1)
    engine._execute_action(action)
    assert performer.mode == "beast"
    engine._execute_action(action)  # same turn — should be blocked
    assert performer.mode == "beast", "second transform in the same turn should be blocked by per_turn_limit"
    print("  PASS test_transform_respects_per_turn_limit")


def test_transform_is_free_action_does_not_advance_turn():
    """Real data: transform's .is_continue_turn true / "FREE ACTION, does
    not end your turn" per the wiki -- lets Abomination switch Human/Beast
    and still attack the same turn. Previously is_continue_turn was parsed
    but never consulted in step(), so using transform silently cost the
    whole turn like any other skill. Verified via the full step() path
    (not _execute_action directly, since the turn-advance logic lives in
    step())."""
    engine = _fresh_engine()
    performer = engine.heroes[0]
    assert engine.get_current_actor_id() == performer.id

    engine.submit_action(Action(performer.id, "transform", -1))
    engine.step()

    assert performer.mode == "beast"
    assert engine.get_current_actor_id() == performer.id, (
        "transform should not have ended the hero's turn")
    print("  PASS test_transform_is_free_action_does_not_advance_turn")


def test_transform_not_offered_once_per_battle_limit_exhausted():
    """Real gap found alongside the free-action fix: get_valid_actions()
    never checked per_battle_limit/per_turn_limit, only _check_skill_limits
    did at execution time -- so a maxed-out transform (2/battle) was still
    offered as a legal action, silently rejected on selection (wasting a
    turn) exactly like the mode-filter bug this mirrors."""
    engine = _fresh_engine()
    performer = engine.heroes[0]

    engine.submit_action(Action(performer.id, "transform", -1))
    engine.step()
    assert performer.mode == "beast"
    # per_turn_limit=1 already blocks a same-turn second use; advance a
    # round so the second (battle-legal) use is only gated by
    # per_battle_limit.
    engine.round += 1
    engine.submit_action(Action(performer.id, "transform", -1))
    engine.step()
    assert performer.mode == "human"

    engine.round += 1
    skills_after_two_uses = {a.skill_id for a in engine.get_valid_actions(performer.id)}
    assert "transform" not in skills_after_two_uses, skills_after_two_uses
    print("  PASS test_transform_not_offered_once_per_battle_limit_exhausted")


def test_affliction_in_beast_mode_forces_revert_to_human():
    """Real data: only abomination's "beast" mode record sets
    .affliction_combat_skill_id "transform" -- becoming Afflicted while in
    Beast form force-transforms back to Human for free, NOT counted
    against the 2-per-battle transform limit. Without this an Abomination
    who already spent both charges and then got Afflicted in Beast form
    would be stuck there (Rake/Rage/Slam only) for the rest of the fight."""
    engine = _fresh_engine()
    performer = engine.heroes[0]
    performer.mode = "beast"

    performer.is_afflicted = True
    engine._apply_affliction_effects(performer)

    assert performer.mode == "human", "affliction in beast mode should force a revert"
    assert performer.skill_uses.get("transform", 0) == 0, (
        "the forced revert must not consume a per-battle transform charge")
    print("  PASS test_affliction_in_beast_mode_forces_revert_to_human")


def test_full_combat_resolves_with_abomination():
    engine = _fresh_engine(seed=7)
    steps = 0
    while not engine.done and steps < 300:
        steps += 1
        actor = engine.get_current_actor_id()
        if actor is not None:
            acts = engine.get_valid_actions(actor)
            if acts:
                engine.submit_action(acts[0])
        engine.step()
    assert engine.done, f"combat did not finish in {steps} steps"
    print(f"  PASS test_full_combat_resolves_with_abomination ({steps} steps, winner={engine.winner})")


if __name__ == "__main__":
    print("=== Abomination Transform Tests ===\n")
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
