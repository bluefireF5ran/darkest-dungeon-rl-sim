"""Tests for the potential-based reward shaping in DarkestDungeonEnv.

Layer 1 (unit): mutate CombatantState directly after one env.reset() to
exercise _hero_score/_enemy_score/_compute_potential against exact, chosen
states — no RNG involved, so these are fully deterministic.

Layer 2 (integration): drive the real engine through the Gym wrapper with
fixed seeds to sanity-check the end-to-end reward signal.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from env import DarkestDungeonEnv
from env.gym_env import (
    SHAPING_GAMMA, REWARD_WIN, REWARD_LOSE,
    TURN_PENALTY_THREAT, TURN_PENALTY_STALL, STALL_PHASE_MAX_ENEMIES,
    STRESS_TICK_PENALTY,
)


def _turn_penalty(env):
    """The rate this env's next turn will be charged at. The time cost is
    phase-dependent (threat vs recovery), so a test asserting on reward has
    to ask which phase it's in rather than assume a single constant."""
    return TURN_PENALTY_STALL if env.is_recovery_phase() else TURN_PENALTY_THREAT


def _make_env(hero_ids=("crusader",), enemy_ids=("cultist_brawler_A",), seed=1):
    env = DarkestDungeonEnv()
    env.reset(seed=seed, options={"hero_ids": list(hero_ids), "enemy_ids": list(enemy_ids)})
    # `reset(seed=)` seeds the ENGINE's RNG, not the action space: gymnasium
    # leaves `action_space._np_random` unseeded until asked, and then draws it
    # from OS entropy. Every test here that plays `action_space.sample()` was
    # therefore running a different episode on every invocation — one of them
    # passed five runs alone and failed once inside the full suite, which reads
    # exactly like cross-test pollution and is not.
    env.action_space.seed(seed)
    return env


# ── Layer 1: potential function unit tests ──


def test_hero_score_ordering():
    env = _make_env()
    h = env.engine.heroes[0]

    h.hp, h.stress, h.is_dead = h.max_hp, 0, False
    full_hp = env._hero_score(h)

    h.hp = h.max_hp // 2
    half_hp = env._hero_score(h)

    h.hp = 0
    deaths_door = env._hero_score(h)

    h.is_dead = True
    dead = env._hero_score(h)

    assert full_hp > half_hp > deaths_door > dead, (full_hp, half_hp, deaths_door, dead)
    print("  PASS test_hero_score_ordering")


def test_enemy_score_ordering():
    env = _make_env()
    e = env.engine.enemies[0]

    e.hp, e.is_dead = e.max_hp, False
    full_hp = env._enemy_score(e)

    e.hp = 1
    low_hp = env._enemy_score(e)

    e.is_dead = True
    dead = env._enemy_score(e)

    assert full_hp > low_hp > dead, (full_hp, low_hp, dead)
    print("  PASS test_enemy_score_ordering")


def test_afflicted_penalizes_beyond_stress_cap():
    env = _make_env()
    h = env.engine.heroes[0]
    h.hp, h.stress, h.is_dead = h.max_hp, 100, False

    h.is_afflicted, h.is_virtuous = False, False
    plain = env._hero_score(h)

    h.is_afflicted = True
    afflicted = env._hero_score(h)

    assert afflicted < plain, (afflicted, plain)
    print("  PASS test_afflicted_penalizes_beyond_stress_cap")


def test_virtuous_bonus():
    env = _make_env()
    h = env.engine.heroes[0]
    h.hp, h.stress, h.is_dead, h.is_afflicted = h.max_hp, 0, False, False

    h.is_virtuous = False
    plain = env._hero_score(h)

    h.is_virtuous = True
    virtuous = env._hero_score(h)

    assert virtuous > plain, (virtuous, plain)
    print("  PASS test_virtuous_bonus")


def test_attack_and_heal_symmetric_shaping():
    """Equal party sizes: an enemy losing X% max_hp and a hero gaining X%
    max_hp move Phi by the same magnitude — no independent weight makes
    attacking worth more than healing (or vice versa)."""
    env = _make_env(hero_ids=("crusader", "crusader"), enemy_ids=("cultist_brawler_A", "cultist_brawler_A"))
    # Normalize max_hp to a round number for both sides so a "30%" HP move is
    # exactly representable as an int — real heroes/enemies have different
    # max_hp values, and int() truncation of 70% of two different totals
    # lands on slightly different fractions, which would confound this test
    # with a rounding artifact instead of a real symmetry check.
    for h in env.engine.heroes:
        h.max_hp = 100
        h.hp, h.stress, h.is_dead, h.is_afflicted, h.is_virtuous = h.max_hp, 0, False, False, False
    for e in env.engine.enemies:
        e.max_hp = 100
        e.hp, e.is_dead = e.max_hp, False

    baseline = env._compute_potential()

    # Branch A: an enemy loses 30% max_hp (simulated damage)
    env.engine.enemies[0].hp = int(env.engine.enemies[0].max_hp * 0.7)
    phi_after_damage = env._compute_potential()
    delta_damage = phi_after_damage - baseline
    env.engine.enemies[0].hp = env.engine.enemies[0].max_hp  # reset

    # Branch B: a hero gains back 30% max_hp from a damaged baseline
    env.engine.heroes[0].hp = int(env.engine.heroes[0].max_hp * 0.7)
    phi_before_heal = env._compute_potential()
    env.engine.heroes[0].hp = env.engine.heroes[0].max_hp
    phi_after_heal = env._compute_potential()
    delta_heal = phi_after_heal - phi_before_heal

    assert abs(delta_damage - delta_heal) < 1e-9, (delta_damage, delta_heal)
    print(f"  PASS test_attack_and_heal_symmetric_shaping (delta={delta_damage:.4f})")


def test_heal_prevents_death_beats_letting_ally_die():
    env = _make_env(hero_ids=("crusader", "crusader"), enemy_ids=("cultist_brawler_A",))
    a, b = env.engine.heroes
    for h in (a, b):
        h.stress, h.is_dead, h.is_afflicted, h.is_virtuous = 0, False, False, False
    b.hp = b.max_hp  # ally B unaffected in both branches
    a.hp = max(1, int(a.max_hp * 0.03))  # A at ~3% HP

    phi_before = env._compute_potential()

    # Branch A: A dies
    a.is_dead, a.hp = True, 0
    phi_after_death = env._compute_potential()
    shaping_death = SHAPING_GAMMA * phi_after_death - phi_before
    a.is_dead = False

    # Branch B: A gets healed back up instead
    a.hp = a.max_hp
    phi_after_heal = env._compute_potential()
    shaping_heal = SHAPING_GAMMA * phi_after_heal - phi_before

    assert shaping_heal > shaping_death, (shaping_heal, shaping_death)
    print(f"  PASS test_heal_prevents_death_beats_letting_ally_die "
          f"(heal={shaping_heal:.4f} > death={shaping_death:.4f})")


def test_no_free_reward_for_standing_still():
    env = _make_env()
    phi = env._compute_potential()
    shaping = SHAPING_GAMMA * phi - phi  # state unchanged
    reward = shaping + _turn_penalty(env)
    assert reward < 0, reward
    print(f"  PASS test_no_free_reward_for_standing_still (reward={reward:.4f})")


def test_terminal_potential_forced_to_zero():
    """When the fight ends this step, phi_after must be 0.0, not the real
    terminal potential — otherwise a lopsided final state could swamp the
    win/lose bonus."""
    env = _make_env()
    env.engine.enemies[0].is_dead = True  # guarantees a win once engine.step() runs
    phi_before = env._compute_potential()
    penalty = _turn_penalty(env)  # read before stepping, same as step() does

    obs, reward, terminated, truncated, info = env.step(0)

    assert terminated is True
    assert info["winner"] == "heroes"
    expected = penalty + REWARD_WIN - phi_before  # phi_after forced to 0.0
    assert abs(reward - expected) < 1e-6, (reward, expected)
    print(f"  PASS test_terminal_potential_forced_to_zero (reward={reward:.4f}, expected={expected:.4f})")


def test_forced_loss_gives_negative_reward():
    env = _make_env()
    env.engine.heroes[0].is_dead = True  # guarantees a loss once engine.step() runs
    obs, reward, terminated, truncated, info = env.step(0)
    assert terminated is True
    assert info["winner"] == "enemies"
    assert reward < 0, reward
    print(f"  PASS test_forced_loss_gives_negative_reward (reward={reward:.4f})")


# ── Layer 2: integration tests through the real engine ──


def test_terminal_reward_sign_matches_winner():
    for seed in (1, 2, 3, 4, 5):
        env = _make_env(hero_ids=("crusader", "crusader"), enemy_ids=("cultist_brawler_A", "cultist_brawler_A"), seed=seed)
        reward = 0.0
        done = False
        steps = 0
        while not done and steps < 150:
            obs, reward, terminated, truncated, info = env.step(env.action_space.sample())
            done = terminated or truncated
            steps += 1
        if not done:
            print(f"  SKIP seed={seed}: did not finish in {steps} steps")
            continue
        if info["winner"] == "heroes":
            assert reward > 0, (seed, reward, info)
        elif info["winner"] == "enemies":
            assert reward < 0, (seed, reward, info)
    print("  PASS test_terminal_reward_sign_matches_winner")


def test_episode_return_bounded_by_terminal_dominance():
    for seed in (1, 2, 3):
        env = _make_env(hero_ids=("crusader", "crusader"), enemy_ids=("cultist_brawler_A", "cultist_brawler_A"), seed=seed)
        total_reward = 0.0
        done = False
        steps = 0
        info = {}
        while not done and steps < 150:
            obs, reward, terminated, truncated, info = env.step(env.action_space.sample())
            total_reward += reward
            done = terminated or truncated
            steps += 1
        if not done:
            print(f"  SKIP seed={seed}: did not finish in {steps} steps")
            continue
        terminal_bonus = REWARD_WIN if info["winner"] == "heroes" else REWARD_LOSE
        distance = abs(total_reward - terminal_bonus)
        # Intermediate cost is now deliberately meaningful — a per-turn time
        # charge (TURN_PENALTY_THREAT) plus a dense per-point stress charge —
        # so a flat tolerance no longer expresses the invariant. What still
        # has to hold is that accumulated cost stays within what those
        # per-step charges can account for, which is what catches a runaway
        # shaping term. This episode plays RANDOM actions, so it is close to
        # the worst case for accumulated time cost.
        budget = 4.0 + steps * abs(TURN_PENALTY_THREAT)
        assert distance < budget, (seed, total_reward, terminal_bonus, distance, budget)
    print("  PASS test_episode_return_bounded_by_terminal_dominance")


def test_stalling_agent_does_not_profit():
    """A hero who does nothing must never accumulate positive reward, even
    against a harmless enemy.

    Emptying _valid_actions alone no longer forces a no-op: step() resolves
    the chosen index through _action_map (fixed action-space semantics), and
    an index that isn't mapped falls back to the engine's own __pass__ rather
    than to whatever happened to be last in a list. Clearing _action_map is
    the equivalent "the hero does nothing this turn" setup, and it exercises
    that fallback path directly."""
    env = _make_env()
    env.engine.enemies[0].dmg_min = 0
    env.engine.enemies[0].dmg_max = 0
    total_reward = 0.0
    for _ in range(10):
        if env.engine.done:
            break
        env._valid_actions = []
        env._action_map = {}  # nothing the policy picks maps to a real action
        obs, reward, terminated, truncated, info = env.step(0)
        total_reward += reward
        if terminated:
            break
    assert total_reward < 0, f"stalling should never accumulate positive reward, got {total_reward}"
    print(f"  PASS test_stalling_agent_does_not_profit (total_reward={total_reward:.4f})")


# ── Two-phase tempo model ──


def test_recovery_phase_starts_at_the_last_enemy():
    env = _make_env(enemy_ids=("cultist_brawler_A", "cultist_brawler_A", "cultist_brawler_A"))
    hero = env.engine.heroes[0]
    hero.hp = hero.max_hp // 2  # something worth recovering
    assert not env.is_recovery_phase(), "3 living enemies is the threat phase"
    env.engine.enemies[0].is_dead = True
    assert not env.is_recovery_phase(), "2 living enemies is still the threat phase"
    env.engine.enemies[1].is_dead = True
    assert env.is_recovery_phase(), "down to the last enemy — the real stall window"
    print("  PASS test_recovery_phase_starts_at_the_last_enemy")


def test_recovery_discount_only_while_there_is_something_to_recover():
    """Real flaw found by training on the first version of this reward: with
    the recovery discount granted purely on enemy count, the policy killed
    down to the last enemy and then dawdled indefinitely — a recovery turn
    costs -0.01 immediately while the -20 truncation penalty, discounted at
    gamma=0.99 over ~200 remaining steps, is worth only about -2.6 at the
    moment of choosing. Measured: "Ballad Spam" medium reached 60% stall-outs
    with a healthy party and 14% success. A topped-up party must be charged
    the threat rate so the fight actually finishes."""
    env = _make_env(enemy_ids=("cultist_brawler_A",))
    for h in env.engine.heroes:
        h.hp = h.max_hp
        h.stress = 0
    assert not env.is_recovery_phase(), \
        "nothing left to recover — stalling here is just dawdling"
    assert _turn_penalty(env) == TURN_PENALTY_THREAT

    env.engine.heroes[0].hp = env.engine.heroes[0].max_hp // 2
    assert env.is_recovery_phase(), "a hurt hero makes stalling worth it again"
    assert _turn_penalty(env) == TURN_PENALTY_STALL

    env.engine.heroes[0].hp = env.engine.heroes[0].max_hp
    env.engine.heroes[0].stress = 60
    assert env.is_recovery_phase(), "stress is also worth recovering, not just HP"
    print("  PASS test_recovery_discount_only_while_there_is_something_to_recover")


def test_the_stall_gate_is_the_games_own_not_an_invented_budget():
    """The need-gate alone does not bind: stress persists across combats and
    averages ~99 over an expedition, so "any hero above 30 stress" is true
    almost permanently. Measured on the gate-only version — stall-outs went
    20.4% -> 28.2%, i.e. it changed nothing. The budget is what actually ends
    the fight, and it must be per-combat so each internal combat of an
    expedition gets its own allowance."""
    env = _make_env(enemy_ids=("cultist_brawler_A",))
    env.engine.heroes[0].hp = 1  # permanently "needs recovery"
    env.engine.stall_counter = 0
    env.engine._reinforcements_spawned = False
    assert env.is_recovery_phase()

    # The GAME's own threshold closes it, not an invented budget.
    env.engine.stall_counter = int(
        env.engine.rules.stall_round_stress_effect_threshold)
    assert not env.is_recovery_phase(), \
        "the game is charging stress for the delay and time is still cheap"
    assert _turn_penalty(env) == TURN_PENALTY_THREAT

    # Two invalidating skills in a round reset the counter -- that IS the
    # technique -- and cheap turns come back with it. No total bound.
    env.engine.stall_counter = 0
    assert env.is_recovery_phase(), "resetting the stall counter did not reopen it"

    # And once the reinforcement wave has landed there is nothing further to
    # charge, so stalling is free again however long it has gone on.
    env.engine.stall_counter = 99
    assert not env.is_recovery_phase()
    env.engine._reinforcements_spawned = True
    assert env.is_recovery_phase(), \
        "the wave arrives ONCE; after it, standing around costs nothing more"

    # The counter survives only as instrumentation. Spending it must no
    # longer close the window -- that is the whole change.
    env._recovery_turns_used = 10_000
    assert env.is_recovery_phase(), \
        "_recovery_turns_used is gating again; it is diagnostic only"
    print("  PASS test_the_stall_gate_is_the_games_own_not_an_invented_budget")


def test_stress_is_charged_densely_when_it_lands():
    """Abomination's Transform is a FREE action (is_continue_turn), so no
    turn penalty applies to it, while it inflicts +8 stress on every other
    party member and 6/turn in beast form. Paying for that only at the
    expedition's end meant it was discounted to ~13% of face value at the
    moment of choosing, and the trained policy used it 1.77 times per combat
    — essentially its full 2-per-battle allowance every fight, for ~160
    stress per ally per expedition against a 100 affliction / 200 heart
    attack threshold. Stress now costs reward as it lands."""
    env = _make_env(hero_ids=("crusader", "crusader"))
    before = env._total_party_stress()
    env.engine.heroes[0].stress += 20
    gained = env._total_party_stress() - before
    assert gained == 20
    assert STRESS_TICK_PENALTY > 0, "stress must carry an immediate cost"
    # A transform's party cost (8 stress x 3 allies) should outweigh several
    # turns of the threat-phase time cost, or it stays effectively free.
    transform_cost = STRESS_TICK_PENALTY * 8 * 3
    assert transform_cost > abs(TURN_PENALTY_THREAT), (transform_cost, TURN_PENALTY_THREAT)
    print(f"  PASS test_stress_is_charged_densely_when_it_lands "
          f"(transform party cost {transform_cost:.2f} vs turn {TURN_PENALTY_THREAT})")


def test_dead_heroes_do_not_register_as_a_stress_drop():
    """_total_party_stress counts living heroes only, so a death must not
    look like the party's stress falling — that would hand back reward for
    losing a hero, the same perverse gradient _compute_expedition_potential
    had to be fixed for."""
    env = _make_env(hero_ids=("crusader", "crusader"))
    env.engine.heroes[0].stress = 80
    env.engine.heroes[1].stress = 80
    before = env._total_party_stress()
    env.engine.heroes[0].is_dead = True
    after = env._total_party_stress()
    assert after < before, "a death does reduce the living-hero sum..."
    # ...which is exactly why only INCREASES are charged: a drop must never
    # produce positive reward.
    assert max(0.0, after - before) == 0.0
    print("  PASS test_dead_heroes_do_not_register_as_a_stress_drop")


def test_the_stall_state_resets_between_internal_combats():
    from env import ExpeditionSequenceEnv
    env = ExpeditionSequenceEnv(sampler_seed=1)
    env.reset(seed=1, options={
        "hero_ids": ["crusader", "crusader"],
        "zone_id": "crypts", "tier": 1, "length_band": "short", "num_combats": 2,
    })
    env._recovery_turns_used = 10_000
    env.engine.stall_counter = 99
    # Same order step() uses when rolling into the next internal combat.
    env._snapshot_and_advance_roster()
    env._combat_idx += 1
    env._start_next_combat()
    # The diagnostic counter resets...
    assert env._recovery_turns_used == 0
    # ...and so does the thing that now actually gates, because the next
    # combat builds a fresh engine. A stall carried across a combat boundary
    # would price the opening turns of a fresh fight as expensive.
    assert env.engine.stall_counter == 0
    assert not env.engine._reinforcements_spawned
    print("  PASS test_the_stall_state_resets_between_internal_combats")


def test_threat_phase_turns_cost_far_more_than_recovery_turns():
    """The whole point of the two-phase model: dawdling while several enemies
    are still acting has to be expensive, while slowing down to heal once
    only one is left must be nearly free. The old single flat penalty made
    both equally (and negligibly) cheap, which is what produced the turtling
    policy — Crusader healing on 73% of turns, Abomination self-healing on
    74% (see ASSUMPTIONS.md)."""
    threat = _make_env(enemy_ids=("cultist_brawler_A", "cultist_brawler_A", "cultist_brawler_A"))
    recovery = _make_env(enemy_ids=("cultist_brawler_A",))
    for env in (threat, recovery):
        env.engine.heroes[0].hp = env.engine.heroes[0].max_hp // 2  # worth recovering
    assert not threat.is_recovery_phase()
    assert recovery.is_recovery_phase()
    assert _turn_penalty(threat) == TURN_PENALTY_THREAT
    assert _turn_penalty(recovery) == TURN_PENALTY_STALL
    assert TURN_PENALTY_THREAT < TURN_PENALTY_STALL < 0, \
        "both phases must still charge for time, threat much more so"
    assert abs(TURN_PENALTY_THREAT) >= 5 * abs(TURN_PENALTY_STALL), \
        "the gap has to be wide enough to actually change which action wins"
    print(f"  PASS test_threat_phase_turns_cost_far_more_than_recovery_turns "
          f"(threat={TURN_PENALTY_THREAT} vs recovery={TURN_PENALTY_STALL})")


def test_reinforcements_revert_the_fight_to_the_threat_phase():
    """Phase is derived from the live enemy count, so a reinforcement wave
    arriving during a stall automatically puts the fight back into
    proactive-play mode rather than leaving the policy in cheap-time mode."""
    env = _make_env(enemy_ids=("cultist_brawler_A",))
    env.engine.heroes[0].hp = env.engine.heroes[0].max_hp // 2  # worth recovering
    assert env.is_recovery_phase()
    env.engine._spawn_reinforcements()
    assert not env.is_recovery_phase(), \
        "a reinforcement wave must end the recovery window"
    print("  PASS test_reinforcements_revert_the_fight_to_the_threat_phase")


def test_stunning_an_enemy_is_worth_potential():
    """Action economy: an enemy that can't act is nearly as good as a dead
    one, and the potential function has to say so or the policy has to
    rediscover it through many turns of downstream consequences."""
    env = _make_env(enemy_ids=("cultist_brawler_A", "cultist_brawler_A"))
    before = env._compute_potential()
    env.engine.enemies[0].is_stunned = True
    after = env._compute_potential()
    assert after > before, (before, after)
    print(f"  PASS test_stunning_an_enemy_is_worth_potential ({before:.4f} -> {after:.4f})")


def test_prevention_states_are_worth_potential():
    """Guard and riposte stop damage arriving instead of restoring it after
    the fact, which is the threat-phase alternative to healing."""
    env = _make_env(hero_ids=("man_at_arms", "crusader"))
    before = env._compute_potential()
    env.engine.heroes[1].guard_source_id = env.engine.heroes[0].id
    guarded = env._compute_potential()
    assert guarded > before, (before, guarded)

    from engine.combatant import RiposteState
    env.engine.heroes[0].active_riposte = RiposteState(remaining_rounds=3)
    riposting = env._compute_potential()
    assert riposting > guarded, (guarded, riposting)
    print("  PASS test_prevention_states_are_worth_potential")


def test_observation_exposes_the_stall_timer():
    """The advanced 1-enemy technique is to keep landing the required number
    of stall-invalidating actions each round so reinforcements never arrive,
    while the rest of the party heals. That is unlearnable unless the policy
    can see how close a wave is — these two floats are what make it visible."""
    env = _make_env(enemy_ids=("cultist_brawler_A",))
    obs_before = env._get_obs().copy()
    env.engine.stall_counter = env.engine.rules.stall_round_summon_effect_threshold
    obs_after = env._get_obs()
    assert not (obs_before == obs_after).all(), \
        "stall progress must be visible in the observation"

    env.engine.stall_counter = 0
    base = env._get_obs().copy()
    env.engine._stall_invalidating_actions_this_round = 2
    bumped = env._get_obs()
    assert not (base == bumped).all(), \
        "real actions landed this round must be visible in the observation"
    print("  PASS test_observation_exposes_the_stall_timer")


if __name__ == "__main__":
    print("=== Reward Shaping Tests ===\n")
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
