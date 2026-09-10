"""`--prevention-reward`: credit prevention, not restoration (2026-08-27).

Fran's ask, and his own Plague Doctor guide states the thesis outright:

    *"Facetanking attacks and simply trying to outheal enemy offense is just a
    very bad idea in general. It's much safer and more efficient to be
    proactive... rather than being reactive as in just facetanking and spamming
    Heals."*

Two terms, both INSIDE the potential function, so by Ng/Harada/Russell neither
can change the optimal policy -- they fix credit TIMING, which is the whole
complaint. The same argument `W_ENEMY_STUNNED` and `W_BLOCKING_CORPSE` rest on.

- **Outstanding DoT counts against an enemy.** `_enemy_score` read `hp/max_hp`
  and nothing else, so an enemy on 20 HP carrying 15 points of pending blight
  looked untouched. That is a complete account of why the Plague Doctor is
  under-used here: her entire kit is deferred damage, earning zero immediate
  credit while the turn penalty charges the turn at once.
- **The threat forecast counts against the party.** Killing, stunning, raising
  DODGE and debuffing enemy ACC all lower next round's forecast; a heal
  restores HP and changes NOTHING about what is coming. That asymmetry IS the
  ask.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests._fixtures import get_store

from engine.combat_engine import CombatEngine
from engine.combatant import DotInstance
from engine.rng import SeededRNG
from env.expedition_sequence_env import ExpeditionSequenceEnv
from env.gym_env import DarkestDungeonEnv, _outstanding_dot

store = get_store()


def _env(prevention, seed=1):
    env = DarkestDungeonEnv(store=store, prevention_reward=prevention)
    env.reset(seed=seed)
    return env


def _trajectory(prevention, seed, steps=40):
    """A fixed episode. `reset(seed=)` does not reach the ACTION SPACE, so it
    is seeded too -- otherwise the test replays a different episode every run,
    which cost this project a full-suite failure once already."""
    env = ExpeditionSequenceEnv(sampler_seed=0, prevention_reward=prevention)
    env.reset(seed=seed)
    env.action_space.seed(seed)
    trail = []
    for _ in range(steps):
        mask = env.action_masks()
        legal = [i for i, m in enumerate(mask) if m]
        if not legal:
            break
        action = legal[env.action_space.sample() % len(legal)]
        _obs, reward, term, trunc, _info = env.step(action)
        trail.append((round(float(reward), 9), term, trunc,
                      tuple(round(h.hp, 6) for h in env.engine.heroes),
                      tuple(round(e.hp, 6) for e in env.engine.enemies)))
        if term or trunc:
            break
    return trail


# ----------------------------------------------------------- the OFF proof --

def test_the_flag_off_is_byte_identical_to_before_it_existed():
    """Every knob in this project defaults off and is proven identical, so an
    archived checkpoint's distribution stays reproducible and the retrain gets
    a clean control arm."""
    for seed in (1, 7, 42):
        env = ExpeditionSequenceEnv(sampler_seed=0, prevention_reward=False)
        env.reset(seed=seed)
        assert env._incoming_threat_penalty() == 0.0
        for enemy in env.engine.enemies:
            enemy.burn_stacks = 9        # a DoT the ON arm would price
        with_dot = env._compute_potential()
        for enemy in env.engine.enemies:
            enemy.burn_stacks = 0
        assert env._compute_potential() == with_dot, (
            "the OFF arm priced a DoT")


def test_the_two_arms_diverge_only_in_REWARD_never_in_dynamics():
    """A reward term must not move the world. If the trajectories differ, the
    forecast call is drawing from the RNG and every trace comparison after it
    is invalid."""
    for seed in (3, 11):
        off = _trajectory(False, seed)
        on = _trajectory(True, seed)
        assert len(off) == len(on)
        for a, b in zip(off, on):
            assert a[1:] == b[1:], f"seed {seed}: the world diverged"


# ------------------------------------------------------------ deferred DoT --

def test_outstanding_dot_counts_burn_by_OUTSTANDING_not_by_STACKS():
    """A stack of 7 is worth 11 -- burn decays rather than expiring, so the
    displayed number is not the damage owed."""
    eng = CombatEngine(store, SeededRNG(1))
    eng.reset(hero_defs=[store.heroes["crusader"]],
              enemy_defs=[store.enemies["swine_wretch_A"]],
              auto_resolve_to_hero=False)
    target = eng.enemies[0]
    target.max_hp = target.hp = 999
    target.burn_stacks = 7
    assert _outstanding_dot(target) > 7


def test_applying_a_dot_moves_the_potential_immediately():
    """The defect: a blight earned NOTHING until it ticked, several turns
    later, while the turn penalty charged the turn at once."""
    env = _env(True)
    before = env._compute_potential()
    target = env.engine.enemies[0]
    target.blights.append(DotInstance(effect_name="test blight", damage_per_tick=3, remaining_ticks=3))
    assert env._compute_potential() > before


def test_the_OFF_arm_is_blind_to_that_same_dot():
    env = _env(False)
    before = env._compute_potential()
    env.engine.enemies[0].blights.append(
        DotInstance(effect_name="test blight", damage_per_tick=3, remaining_ticks=3))
    assert env._compute_potential() == before


def test_overkill_dot_cannot_out_score_an_actual_kill():
    """Capped at the target's own HP. Uncapped, a huge DoT on a full-health
    enemy would score better than killing it, which is nonsense -- and the cap
    is also what stops the term being double counted as the DoT ticks."""
    env = _env(True)
    target = env.engine.enemies[0]
    target.blights.append(DotInstance(effect_name="test blight", damage_per_tick=999, remaining_ticks=9))
    doomed = env._compute_potential()
    target.blights.clear()
    target.hp = 0
    target.is_dead = True
    killed = env._compute_potential()
    assert killed > doomed


# ------------------------------------------------------------- prevention ---

def test_stunning_the_board_beats_healing_the_same_hp_back():
    """The asymmetry the whole flag exists for. Both arms are given one turn's
    worth of value; only prevention changes what is COMING."""
    def potential_after(stun):
        env = _env(True, seed=5)
        hero = env.engine.heroes[0]
        hero.hp = max(1, hero.max_hp // 2)
        if stun:
            for enemy in env.engine.enemies:
                enemy.is_stunned = True
        else:
            hero.hp = hero.max_hp          # healed to full instead
        return env._compute_potential()

    assert potential_after(stun=True) > potential_after(stun=False)


def test_the_penalty_is_zero_when_nothing_can_act():
    env = _env(True)
    for enemy in env.engine.enemies:
        enemy.is_stunned = True
    assert env._incoming_threat_penalty() == 0.0


def test_the_penalty_is_bounded():
    """Both halves are `_clamp01`ed, so no board can make Φ diverge."""
    from env.gym_env import W_INCOMING_DAMAGE, W_INCOMING_STRESS
    env = _env(True)
    for hero in env.engine.heroes:
        hero.hp = 1
    assert 0.0 <= env._incoming_threat_penalty() <= (W_INCOMING_DAMAGE
                                                     + W_INCOMING_STRESS)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  PASS {name}")
