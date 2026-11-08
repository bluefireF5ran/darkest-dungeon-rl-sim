"""Let the policy see the dice it is about to roll, as the live game allows.

In the real game the battle RNG's state is in the save (DECOMP.md F15), so at
every hero decision three draws are FACTS before anything is chosen: the hit
roll every target is tested against, and the first target's crit roll and
damage roll (`engine/game_rng.py`). A human cannot read them; a program can.

This wrapper gives a policy the same knowledge in training:

- at every decision it draws those three values from the engine's own RNG and
  appends them to the observation (`FATE_FEATURES`, in [0, 1]);
- on `step` it installs them as `engine.fate`, so the hero's attack USES them
  -- the hit, the first target's crit and its damage are exactly what the
  observation said, as they will be in the game;
- the bridge fills the same three numbers from the save's seed
  (`bridge/fate.py`), so train and play read one quantity.

Opt-in (`train.py --fate-obs`), because it widens the observation by three: a
checkpoint trained with it loads only behind this wrapper, and one trained
without it never sees it. The env itself is untouched.
"""
from __future__ import annotations

from typing import Optional

import gymnasium as gym
import numpy as np

FATE_FEATURES = 3          # hit roll, crit roll, damage roll
#: v2 (`--fate-outcomes`): the dice already RESOLVED against every attack the
#: hero can make -- will it hit, will it crit, will it kill the first target --
#: one triple per attack action index (skill slot x target rank).
OUTCOME_ACTIONS = 28
OUTCOME_FEATURES = 3 * OUTCOME_ACTIONS


def attack_outcomes(engine, action_map: dict, fate) -> np.ndarray:
    """`(28, 3)`: will_hit, will_crit, will_kill for each attack action.

    Resolved with the engine's OWN rules against the first target, the one the
    known draws cover (DECOMP.md F15): guard redirect, the hit chance with the
    consecutive-miss bonus, the F4 roll rule, the crit check, and the damage
    path of `resolve_attack` with the known damage roll. Read-only; draws
    nothing. Non-attack and illegal indices stay 0. Conditional buffs toggled
    at execution time are not re-evaluated -- a small, documented gap.
    """
    from engine.resolution import (PROT_DAMAGE_REDUCTION_CAP,      # noqa: PLC0415
                                   compute_excess_crit_bonus,
                                   compute_hit_chance, resolve_to_hit)
    out = np.zeros((OUTCOME_ACTIONS, 3), dtype=np.float32)
    if fate is None:
        return out
    actor_id = engine.get_current_actor_id()
    if actor_id is None:
        return out
    performer = engine.heroes[actor_id]
    rules = engine.rules
    for idx in range(OUTCOME_ACTIONS):
        action = action_map.get(idx)
        if action is None or action.target_id is None or action.target_id < 0:
            continue
        try:
            sd = engine.get_skill_data(action.skill_id, level=performer.skill_tier)
            if sd is None or engine._is_ally_skill(action.skill_id):
                continue
            if sd.heal_min is not None and sd.heal_max is not None:
                continue
            targets = engine._resolve_action_targets(action, sd, False)
            if not targets:
                continue
            target = targets[0]
            if not target.is_corpse and not sd.ignore_guard:
                target = engine._resolve_guard_redirect(target, is_aoe=sd.is_aoe)
            acc_bonus = (engine.consecutive_misses.get(performer.id, 0)
                         * rules.consecutive_miss_reduction_acc_buff)
            chance = compute_hit_chance(performer, target, sd, rules, acc_bonus)
            hit = True if target.is_corpse else resolve_to_hit(fate.hit_roll, chance, rules)
            crit_chance = performer.crit + sd.crit + target.crit_received_bonus
            crit = bool(sd.is_crit_valid and fate.crit_roll < crit_chance
                        and not target.is_corpse)
            m = 1.0 + sd.dmg_mod
            lo = int(np.ceil(performer.dmg_min * m - 1e-9))
            hi = int(np.ceil(performer.dmg_max * m - 1e-9))
            base = float(performer.dmg_max) if crit else \
                (lo + int(fate.damage_u * (hi - lo + 1))) / (m if m > 0 else 1.0)
            dmg = (base * m * engine._compute_mark_damage_mult(performer, target, sd)
                   * (1.0 + sd.dmg_per_burn_stack * float(target.burn_stacks or 0))
                   * (1.0 + compute_excess_crit_bonus(performer, sd, rules)))
            if crit:
                dmg *= rules.player_crit_multiplier
            if not sd.ignore_protection:
                dmg *= 1.0 - min(target.prot, PROT_DAMAGE_REDUCTION_CAP)
            dmg = max(1, round(dmg))
            out[idx] = (float(hit), float(hit and crit),
                        float(hit and dmg >= target.hp and target.hp > 0))
        except Exception:                                   # noqa: BLE001
            continue
    return out


class _Fate:
    __slots__ = ("hit_roll", "crit_roll", "damage_u")

    def __init__(self, hit_roll: float, crit_roll: float, damage_u: float):
        self.hit_roll, self.crit_roll, self.damage_u = hit_roll, crit_roll, damage_u


def fate_vector(fate) -> np.ndarray:
    if fate is None:
        return np.full(FATE_FEATURES, 0.5, dtype=np.float32)
    return np.array([fate.hit_roll, fate.crit_roll, fate.damage_u], dtype=np.float32)


class FateObsWrapper(gym.Wrapper):
    def __init__(self, env, outcomes: bool = False):
        super().__init__(env)
        self.outcomes = bool(outcomes)
        base = env.observation_space
        extra = FATE_FEATURES + (OUTCOME_FEATURES if self.outcomes else 0)
        self.observation_space = gym.spaces.Box(
            low=-np.inf, high=np.inf, shape=(base.shape[0] + extra,),
            dtype=np.float32)
        self._fate: Optional[_Fate] = None

    def _draw(self) -> None:
        rng = self.env.unwrapped.engine.rng
        self._fate = _Fate(rng.random(), rng.random(), rng.random())

    def _obs(self, obs) -> np.ndarray:
        parts = [np.asarray(obs, dtype=np.float32), fate_vector(self._fate)]
        if self.outcomes:
            u = self.env.unwrapped
            parts.append(attack_outcomes(u.engine, u._action_map, self._fate).reshape(-1))
        return np.concatenate(parts)

    def reset(self, **kwargs):
        obs, info = self.env.reset(**kwargs)
        self._draw()
        return self._obs(obs), info

    def step(self, action):
        engine = self.env.unwrapped.engine
        engine.fate = self._fate
        try:
            obs, reward, terminated, truncated, info = self.env.step(action)
        finally:
            # The env may have built a NEW engine for the next combat; clear
            # whichever one holds a stale fate.
            engine.fate = None
            self.env.unwrapped.engine.fate = None
        self._draw()
        return self._obs(obs), reward, terminated, truncated, info

    def action_masks(self):
        return self.env.unwrapped.action_masks()


def fate_mode_for_width(model_width: int, env_width: int) -> Optional[str]:
    """"obs" (+3), "outcomes" (+87), or None for a checkpoint of the plain width."""
    extra = int(model_width) - int(env_width)
    if extra == FATE_FEATURES:
        return "obs"
    if extra == FATE_FEATURES + OUTCOME_FEATURES:
        return "outcomes"
    return None


def wrap_for(base_env, model_width: int):
    """`base_env` behind the wrapper a checkpoint of `model_width` needs."""
    mode = fate_mode_for_width(model_width, base_env.observation_space.shape[0])
    if mode is None:
        return base_env
    return FateObsWrapper(base_env, outcomes=(mode == "outcomes"))


def live_features(engine, action_map, fate, outcomes: bool) -> np.ndarray:
    """What the wrapper would append, computed from a SEEDED engine and the
    dice read off the save -- the bridge's side of the same quantity."""
    parts = [fate_vector(fate)]
    if outcomes:
        parts.append(attack_outcomes(engine, action_map, fate).reshape(-1))
    return np.concatenate(parts)
