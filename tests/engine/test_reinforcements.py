"""Tests for reinforcements — real, fixed, zone-independent
stall_summon_A/B/C effects (2x cultist_brawler + cultist_witch at a
matching A/B/C tier), triggered once when a fight stalls too long with too
few enemies (rules.stall_round_summon_effect_threshold, already loaded but
previously never consulted anywhere in engine/).
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests._fixtures import get_store

from engine import SeededRNG, CombatEngine


from data.paths import GAME_DIR
from data.paths import CSV_DIR
store = get_store()


def test_cultist_brawler_prefers_front_cultist_witch_does_not():
    assert store.enemies["cultist_brawler_A"].prefers_front is True
    assert store.enemies["cultist_witch_A"].prefers_front is False
    print("  PASS test_cultist_brawler_prefers_front_cultist_witch_does_not")


def test_tier_suffix_matches_current_enemies():
    crusader = store.heroes["crusader"]
    engine = CombatEngine(store, SeededRNG(1))
    engine.reset(hero_defs=[crusader], enemy_defs=[store.enemies["cultist_brawler_B"]])
    assert engine._current_enemy_tier_suffix() == "B"
    print("  PASS test_tier_suffix_matches_current_enemies")


def test_reinforcements_spawn_once_after_sustained_stall():
    """Drive stall_counter to the summon threshold directly (deterministic,
    doesn't depend on real combat RNG to get there) and confirm exactly one
    wave of 2 enemies appears, on open ranks, matching the fight's own tier."""
    crusader = store.heroes["crusader"]
    brawler_b = store.enemies["cultist_brawler_B"]
    engine = CombatEngine(store, SeededRNG(5))
    engine.reset(hero_defs=[crusader, crusader, crusader, crusader], enemy_defs=[brawler_b])
    assert len(engine.enemies) == 1

    for _ in range(int(engine.rules.stall_round_summon_effect_threshold)):
        engine._check_stall()

    assert engine._reinforcements_spawned is True
    assert len(engine.enemies) == 3, [e.definition_id for e in engine.enemies]
    new_ids = sorted(e.definition_id for e in engine.enemies[1:])
    assert new_ids == ["cultist_brawler_B", "cultist_witch_B"], new_ids

    positions = [e.position for e in engine.enemies]
    assert len(set(positions)) == len(positions), "reinforcements must not overlap the original enemy or each other"

    # Idempotent — further stall checks must not spawn a second wave.
    enemy_count_after_first_wave = len(engine.enemies)
    for _ in range(5):
        engine._check_stall()
    assert len(engine.enemies) == enemy_count_after_first_wave
    print(f"  PASS test_reinforcements_spawn_once_after_sustained_stall ({new_ids}, positions={positions})")


def test_reinforced_enemies_join_next_rounds_turn_order():
    crusader = store.heroes["crusader"]
    brawler_b = store.enemies["cultist_brawler_B"]
    engine = CombatEngine(store, SeededRNG(5))
    engine.reset(hero_defs=[crusader, crusader, crusader, crusader], enemy_defs=[brawler_b])
    for _ in range(int(engine.rules.stall_round_summon_effect_threshold)):
        engine._check_stall()
    engine._compute_turn_order()
    turn_order_ids = {c.id for c in engine.turn_order}
    assert all(e.id in turn_order_ids for e in engine.enemies), \
        "every enemy, including reinforcements, should be in the next round's turn order"
    print("  PASS test_reinforced_enemies_join_next_rounds_turn_order")


def test_no_reinforcements_without_sustained_stall():
    crusader = store.heroes["crusader"]
    brawler_b = store.enemies["cultist_brawler_B"]
    engine = CombatEngine(store, SeededRNG(1))
    engine.reset(hero_defs=[crusader, crusader, crusader, crusader], enemy_defs=[brawler_b])
    engine._check_stall()  # 1 round of stall, well below the summon threshold
    assert len(engine.enemies) == 1
    assert engine._reinforcements_spawned is False
    print("  PASS test_no_reinforcements_without_sustained_stall")


def test_two_real_actions_per_round_prevent_stall_accumulation():
    """Real bug found and fixed: _check_stall used to increment stall_
    counter purely from a low alive-enemy count, ignoring the actual real-
    game condition entirely -- "if you use 2 or more damage dealing moves
    (stall-moves) per round, reinforcements don't come" (HEROES/Heroes/
    Plague Doctor's own "How Stalling Works" section). A slow-grind/stun-
    control comp that naturally keeps the enemy count low for several
    rounds while actually attacking normally should NOT accrue stall just
    because few enemies remain."""
    crusader = store.heroes["crusader"]
    brawler_b = store.enemies["cultist_brawler_B"]
    engine = CombatEngine(store, SeededRNG(5))
    engine.reset(hero_defs=[crusader, crusader, crusader, crusader], enemy_defs=[brawler_b])
    assert len(engine.enemies) == 1  # alive_enemies well under the threshold

    smite = next(s for s in crusader.skills if s.id == "smite")
    for _ in range(int(engine.rules.stall_round_summon_effect_threshold) + 2):
        # 2 "real" (is_stall_invalidating) actions this round -- should
        # invalidate the stall check regardless of the low enemy count.
        engine._track_skill_use(engine.heroes[0], "smite", smite.levels[4])
        engine._track_skill_use(engine.heroes[1], "smite", smite.levels[4])
        engine._check_stall()

    assert engine.stall_counter == 0, engine.stall_counter
    assert engine._reinforcements_spawned is False
    assert len(engine.enemies) == 1
    print("  PASS test_two_real_actions_per_round_prevent_stall_accumulation")


def test_nearly_wiped_party_does_not_accrue_stall():
    """Real fix: alive_heroes below stall_party_size_threshold (3) exempts
    the round entirely -- "Having only 1-2 heroes alive prevents
    reinforcements from coming" per the same doc."""
    crusader = store.heroes["crusader"]
    brawler_b = store.enemies["cultist_brawler_B"]
    engine = CombatEngine(store, SeededRNG(5))
    engine.reset(hero_defs=[crusader, crusader, crusader, crusader], enemy_defs=[brawler_b])
    engine.heroes[2].is_dead = True
    engine.heroes[3].is_dead = True  # only 2 heroes alive now

    for _ in range(int(engine.rules.stall_round_summon_effect_threshold) + 2):
        engine._check_stall()  # zero real actions, but party too small to "stall"

    assert engine.stall_counter == 0, engine.stall_counter
    assert engine._reinforcements_spawned is False
    print("  PASS test_nearly_wiped_party_does_not_accrue_stall")


def test_accelerated_stall_enemy_summons_reinforcements_twice_as_fast():
    """Real gap found and fixed: EnemyDefinition never parsed the real
    .accelerate_stall_penalty flag (battle_modifier: record) at all, even
    though the darkest parser's own comments already mention it from an
    earlier boolean-capitalization fix. Wiki-confirmed (darkestdungeon.
    wiki.gg/wiki/Stalling), and independently corroborated by HEROES/
    Heroes/Occultist's own "enemies that accelerate reinforcements" list
    (Madman is on it): when every living enemy has this flag, reinforce-
    ments arrive after 2 stall rounds instead of the normal 4."""
    crusader = store.heroes["crusader"]
    madman = store.enemies["madman_A"]
    assert madman.accelerate_stall_penalty is True
    engine = CombatEngine(store, SeededRNG(5))
    engine.reset(hero_defs=[crusader, crusader, crusader, crusader], enemy_defs=[madman])

    engine._check_stall()
    assert engine._reinforcements_spawned is False
    engine._check_stall()
    assert engine._reinforcements_spawned is True
    print("  PASS test_accelerated_stall_enemy_summons_reinforcements_twice_as_fast")


def test_accelerated_stall_skips_the_stress_penalty():
    """Wiki-confirmed: "the party does not suffer extra stress with
    accelerated stall penalties" -- the round-3 stress effect should never
    fire for an all-accelerated enemy side."""
    crusader = store.heroes["crusader"]
    madman = store.enemies["madman_A"]
    engine = CombatEngine(store, SeededRNG(5))
    engine.reset(hero_defs=[crusader, crusader, crusader, crusader], enemy_defs=[madman])
    stress_before = [h.stress for h in engine.heroes]

    engine._check_stall()  # reaches the accelerated summon threshold already

    assert [h.stress for h in engine.heroes] == stress_before, \
        "accelerated stalling should not apply the normal stress penalty"
    print("  PASS test_accelerated_stall_skips_the_stress_penalty")


def test_disable_stall_penalty_enemy_exempts_the_fight_entirely():
    """Real gap found and fixed: EnemyDefinition never parsed the real
    .disable_stall_penalty flag either (~119 monsters have it, including
    most boss/Ancestor forms and brigand_blood at every tier) -- any
    living enemy with it should exempt the whole fight from stalling,
    however long the party goes without a "real" action."""
    crusader = store.heroes["crusader"]
    brigand = store.enemies["brigand_blood_A"]
    assert brigand.disable_stall_penalty is True
    engine = CombatEngine(store, SeededRNG(5))
    engine.reset(hero_defs=[crusader, crusader, crusader, crusader], enemy_defs=[brigand])

    for _ in range(int(engine.rules.stall_round_summon_effect_threshold) + 5):
        engine._check_stall()

    assert engine.stall_counter == 0
    assert engine._reinforcements_spawned is False
    print("  PASS test_disable_stall_penalty_enemy_exempts_the_fight_entirely")


def test_zero_real_actions_still_accrues_stall_as_before():
    """Confirms the fix didn't just disable reinforcements outright --
    a full, healthy party that lands fewer than 2 real actions per round
    against a nearly-cleared enemy side should still eventually summon
    reinforcements, exactly like test_reinforcements_spawn_once_after_
    sustained_stall already covers (this test just makes the "zero real
    actions" condition explicit via _track_skill_use instead of relying on
    it being the default)."""
    crusader = store.heroes["crusader"]
    brawler_b = store.enemies["cultist_brawler_B"]
    engine = CombatEngine(store, SeededRNG(5))
    engine.reset(hero_defs=[crusader, crusader, crusader, crusader], enemy_defs=[brawler_b])
    battle_heal = next(s for s in crusader.skills if s.id == "battle_heal")

    for _ in range(int(engine.rules.stall_round_summon_effect_threshold)):
        # A support skill (is_stall_invalidating False) shouldn't help avoid stall.
        engine._track_skill_use(engine.heroes[0], "battle_heal", battle_heal.levels[4])
        engine._check_stall()

    assert engine._reinforcements_spawned is True
    print("  PASS test_zero_real_actions_still_accrues_stall_as_before")




# ---------------------------------------------------------------------------
# The four stall keys that went unread until 2026-08-23, and what wiring them
# up actually changed -- which, honestly, is nothing yet. Both are fidelity
# fixes: the guessed constant is gone and the shipped numbers drive the
# mechanic. These tests exist to pin WHY there is no behaviour change, so the
# next person does not read the code as dead and delete it.
# ---------------------------------------------------------------------------

def test_the_four_stall_keys_come_from_rules_json_not_from_defaults():
    r = store.rules
    assert r.stall_extra_round_size_threshold == 2
    assert r.stall_extra_round_size_amount == 1
    assert r.stall_round_accelerated_only_increment == 1
    assert r.stall_round_accelerated_only_consecutive_increment == 5
    print("  PASS test_the_four_stall_keys_come_from_rules_json_not_from_defaults")


def test_the_enemy_side_of_the_stall_test_is_size_not_heads():
    """One size-2 monster is worth 3 (occupancy 2 + surcharge 1), which is
    over `stall_enemy_size_threshold`, while two size-1 monsters are worth 2
    and are not. That is the wiki's rule, and a head count gets it backwards.
    """
    crusader = store.heroes["crusader"]
    engine = CombatEngine(store, SeededRNG(1))
    small = store.enemies["cultist_brawler_A"]
    assert small.size == 1
    engine.reset(hero_defs=[crusader], enemy_defs=[small, small])
    assert engine.effective_enemy_stall_size() == 2
    assert engine.effective_enemy_stall_size() <= store.rules.stall_enemy_size_threshold

    big = next(d for d in store.enemies.values() if d.size == 2)
    engine.reset(hero_defs=[crusader], enemy_defs=[big])
    # occupancy 2 + surcharge 1 = 3, over the threshold of 2
    assert engine.effective_enemy_stall_size() == 3
    assert engine.effective_enemy_stall_size() > store.rules.stall_enemy_size_threshold
    print("  PASS test_the_enemy_side_of_the_stall_test_is_size_not_heads")


def test_the_size_rule_is_inert_in_shipped_content_and_here_is_why():
    """EVERY size-2-or-larger monster the game ships also carries
    `disable_stall_penalty`, and that flag short-circuits `_check_stall`
    before the size test is reached. So wiring the size rule up changed no
    behaviour at all -- it is right rather than useful.

    This is a tripwire, not a celebration: if content ever ships a large
    monster that does NOT disable stalling, this fails and the size rule
    starts mattering. Do not delete the rule because it looks dead.
    """
    big = [(k, d) for k, d in store.enemies.items() if d.size >= 2]
    assert big, "no large monsters at all -- the store is wrong"
    live = [k for k, d in big if not d.disable_stall_penalty]
    assert not live, f"large monsters that do NOT disable stalling: {live[:5]}"
    print(f"  PASS test_the_size_rule_is_inert_in_shipped_content ({len(big)} large monsters)")


def test_an_accelerated_stall_counts_one_then_five():
    """`stall_round_accelerated_only_increment` (1) for the first stalled
    round, `_consecutive_increment` (5) for each one after -- so the counter
    reads 1, then 6, crossing the ordinary summon threshold of 4 on the
    SECOND round. That reproduces the wiki's "2 rounds instead of 4" out of
    the shipped numbers, where this file previously hardcoded a 2 and said in
    a comment that it was a guess.
    """
    crusader, vestal = store.heroes["crusader"], store.heroes["vestal"]
    hellion = store.heroes["hellion"]
    accel = next(d for k, d in store.enemies.items()
                 if d.accelerate_stall_penalty and not d.disable_stall_penalty
                 and d.size == 1)
    engine = CombatEngine(store, SeededRNG(3))
    engine.reset(hero_defs=[crusader, vestal, hellion], enemy_defs=[accel])

    engine._stall_invalidating_actions_this_round = 0
    engine._check_stall()
    assert engine.stall_counter == 1, engine.stall_counter
    assert not engine._reinforcements_spawned

    engine._stall_invalidating_actions_this_round = 0
    engine._check_stall()
    # The counter reached 1 + 5 = 6, crossed the summon threshold of 4, and was
    # then cleared by `stall_round_reset_threshold` (4) inside the SAME call --
    # so 6 is no longer observable afterwards and the spawn is what proves the
    # arithmetic ran. See CombatEngine._maybe_reset_stall_counter.
    assert engine._reinforcements_spawned, "the second accelerated round did not summon"
    assert engine.stall_counter == 0, engine.stall_counter
    print("  PASS test_an_accelerated_stall_counts_one_then_five")


def test_two_real_actions_reset_the_accelerated_streak_too():
    """The streak is what makes the second round cost 5, so it has to clear
    with the counter -- otherwise a party that breaks its stall and later
    stalls again is charged 5 on its FIRST round back.
    """
    heroes = [store.heroes[h] for h in ("crusader", "vestal", "hellion")]
    accel = next(d for k, d in store.enemies.items()
                 if d.accelerate_stall_penalty and not d.disable_stall_penalty
                 and d.size == 1)
    engine = CombatEngine(store, SeededRNG(3))
    engine.reset(hero_defs=heroes, enemy_defs=[accel])

    engine._stall_invalidating_actions_this_round = 0
    engine._check_stall()
    assert engine._accelerated_stall_streak is True

    engine._stall_invalidating_actions_this_round = 2   # a real round of play
    engine._check_stall()
    assert engine.stall_counter == 0
    assert engine._accelerated_stall_streak is False

    engine._stall_invalidating_actions_this_round = 0
    engine._check_stall()
    assert engine.stall_counter == 1, "the streak did not clear"
    print("  PASS test_two_real_actions_reset_the_accelerated_streak_too")


if __name__ == "__main__":
    print("=== Reinforcements Tests ===\n")
    for name, func in sorted(globals().items()):
        if name.startswith("test_"):
            try:
                func()
            except Exception as e:
                import traceback
                print(f"  FAIL {name}: {e}")
                traceback.print_exc()
    print("\nDone.")
