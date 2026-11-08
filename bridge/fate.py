"""Know the dice before choosing: the game's next rolls, and a look-ahead that uses them.

**What is known.** The save's battle `seed` is the state of the game's
`prng_minstd` at the start of the turn being decided (67/67 hero turns, DECOMP.md
F15), and a hero's attack consumes the stream in a fixed order
(`engine/game_rng.py`). So before the hero acts, the hit roll every target will
be tested against, and the first target's crit roll and damage, are FACTS:

    fate = fate_from_seed(state.battle_seed)
    fate.hit_roll    # hit iff >= 0.95 or < the target's chance
    fate.crit_roll   # crit iff < the hero's crit chance on that skill
    fate.damage_u    # damage = lo + int(u * (hi - lo + 1))

**How it is used.** A policy picks by what usually happens; this picks by what
WILL happen. `q_rank` scores every legal action as `r + gamma * V(s')`: the
env is cloned, the known draws are installed for the hero's action
(`CombatEngine.fate`), the action is stepped, and the policy's OWN value
network judges the position that results -- averaged over the parts of the
future that are not known (later targets, enemy turns). It is one ply of
search with the RNG revealed, the advantage a machine has and a player does
not. The value network is the same one PPO trained, so the look-ahead and the
policy agree on what a good position is; only the dice differ.

`tools/fate_eval.py` measures what it is worth in the simulator, against the
plain policy on identical expeditions.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Optional

import numpy as np

from engine.game_rng import (FIRST_CRIT_DRAW, FIRST_DAMAGE_DRAW, HIT_ROLL_DRAW,
                             uniforms)
from engine.rng import SeededRNG

GAMMA = 0.99                       # train.py's PPO gamma; SHAPING_GAMMA matches it


def _never_searched() -> frozenset:
    """Retreat, abandon and pass are never PROPOSED by the look-ahead.

    The first simulator run scored the plain argmax over every legal action at
    43.3% SUCCESS against the policy's 93.3% (n=60 paired, 32 lost, 2 won) --
    with FEWER deaths. The critic values a retreat or an abandon as a safe end
    worth about zero, which beats playing on whenever its estimate is
    negative: the exact collapse `_can_abandon`'s gate exists for. The search
    may keep one of these only when the policy itself picked it.
    """
    from env.action_space import ACTION_ABANDON, ACTION_PASS, ACTION_RETREAT  # noqa: PLC0415
    return frozenset({ACTION_PASS, ACTION_RETREAT, ACTION_ABANDON})


def searchable(legal: list[int], keep: Optional[int] = None) -> list[int]:
    banned = _never_searched()
    return [a for a in legal if a not in banned or a == keep]


@dataclass(frozen=True)
class Fate:
    seed: Optional[int]
    hit_roll: float
    crit_roll: float
    damage_u: float


def fate_from_seed(seed: int) -> Fate:
    u = uniforms(int(seed), FIRST_CRIT_DRAW)
    return Fate(int(seed), u[HIT_ROLL_DRAW - 1], u[FIRST_CRIT_DRAW - 1],
                u[FIRST_DAMAGE_DRAW - 1])


def fate_for_state(state) -> Optional[Fate]:
    """The current hero turn's known draws, or None when the save has no seed
    (out of combat, or a save written mid-transition)."""
    seed = getattr(state, "battle_seed", None)
    return fate_from_seed(seed) if seed else None


def clone_env(env):
    """A deep copy that SHARES the read-only data store (hundreds of MB)."""
    store = getattr(env, "store", None)
    memo = {id(store): store} if store is not None else {}
    return copy.deepcopy(env, memo)


def make_value_fn(model):
    """`obs -> V(obs)` through the policy's own critic, batched."""
    import torch                                                   # noqa: PLC0415
    policy = model.policy

    def value(obs_batch: np.ndarray) -> np.ndarray:
        with torch.no_grad():
            t, _ = policy.obs_to_tensor(np.asarray(obs_batch, dtype=np.float32))
            return policy.predict_values(t).cpu().numpy().reshape(-1)
    return value


def q_values(env, value_fn, legal: list[int], fate: Optional[Fate] = None,
             continuations: int = 1, reseed: bool = False,
             gamma: float = GAMMA) -> dict[int, float]:
    """`{action: r + gamma * V(s')}` for each legal action.

    `fate` installs the known draws for the hero's action. `reseed` replaces
    the clone's RNG for everything AFTER them with a fresh stream per
    continuation -- the honest setting when only the fate is known (the live
    game). With `reseed=False` and one continuation the clone keeps the env's
    own RNG, which in the simulator is the exact future: the upper bound.
    """
    pending = []                                   # (action, reward, obs or None)
    for a in legal:
        for k in range(continuations):
            e = clone_env(env)
            eng = e.engine
            if reseed:
                # COMMON random numbers: continuation k gets the same stream
                # whichever action is being scored, so the comparison between
                # actions is paired and the noise of the unknown future cancels.
                eng.rng = SeededRNG(7919 * (k + 1))
            if fate is not None:
                eng.fate = fate
            obs, r, term, trunc, _ = e.step(int(a))
            pending.append((a, float(r), None if (term or trunc) else obs))
    vals = value_fn(np.stack([o for _, _, o in pending if o is not None])) \
        if any(o is not None for _, _, o in pending) else []
    q: dict[int, list[float]] = {}
    it = iter(vals)
    for a, r, o in pending:
        v = float(next(it)) if o is not None else 0.0
        q.setdefault(a, []).append(r + gamma * v)
    return {a: float(np.mean(v)) for a, v in q.items()}


def policy_probs(model, obs: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """The checkpoint's masked action distribution at `obs`."""
    import torch                                                   # noqa: PLC0415
    policy = model.policy
    with torch.no_grad():
        t, _ = policy.obs_to_tensor(np.asarray(obs, dtype=np.float32))
        dist = policy.get_distribution(t, action_masks=np.asarray(mask, dtype=bool)[None])
        return dist.distribution.probs.cpu().numpy().reshape(-1)


def peek_fate(rng: SeededRNG) -> Fate:
    """The simulator's own next three draws, in the order a single-target
    attack consumes them (hit, crit, damage) -- the sim's equivalent of
    reading the save's seed. Draws from a COPY; the live stream is untouched."""
    probe = copy.deepcopy(rng)
    return Fate(None, probe.random(), probe.random(), probe.random())


def gated_choice(model, env, value_fn, obs, mask, fate: Optional[Fate],
                 continuations: int = 4, margin: float = 2.0, topk: int = 4,
                 explain: Optional[list] = None) -> int:
    """The policy's pick, unless the known dice say one of its other top-k
    candidates is better by more than `margin` (in value units).

    A veto, not a replacement: the plain argmax over every legal action trusts
    the critic far more than PPO trained it to be trusted, and in the first
    simulator run it lost to the policy. Here the critic only arbitrates among
    moves the policy already rates, and only when the dice make the gap large
    -- the attack that WILL miss, the crit that WILL land.
    """
    probs = policy_probs(model, obs, mask)
    legal = [i for i in range(len(mask)) if mask[i]]
    a0 = int(max(legal, key=lambda i: probs[i]))
    cands = sorted(searchable(legal, keep=a0), key=lambda i: -probs[i])[:max(1, topk)]
    if len(cands) <= 1:
        return a0
    q = q_values(env, value_fn, cands, fate=fate, continuations=continuations,
                 reseed=fate is not None)
    best = max(q, key=q.get)
    if best != a0 and q[best] - q[a0] > margin:
        if explain is not None:
            explain.append((a0, best, q[a0], q[best]))
        return int(best)
    return a0


#: The dice veto only reaches into moves the policy itself rates at least this.
DICE_MIN_PROB = 0.05


def dice_choice(model, env, obs, mask, fate, topk: int = 4,
                explain: Optional[list] = None) -> int:
    """The policy's pick, corrected by FACTS rather than by a critic.

    With the turn's draws known, every attack's outcome is known
    (`env/fate_wrapper.attack_outcomes`, 99.8% / 99.1% against what then
    happens). Two corrections, both among the policy's own top-k candidates
    rated at least `DICE_MIN_PROB`:

    - the pick is an attack that WILL MISS -> the best-rated candidate that
      will hit (a kill first);
    - another candidate WILL KILL and the pick will not -> that kill.

    Everything else -- heals, moves, buffs, a pick that lands -- stands.
    """
    from env.fate_wrapper import OUTCOME_ACTIONS, attack_outcomes      # noqa: PLC0415
    probs = policy_probs(model, obs, mask)
    legal = [i for i in range(len(mask)) if mask[i]]
    a0 = int(max(legal, key=lambda i: probs[i]))
    if fate is None:
        return a0
    out = attack_outcomes(env.engine, env._action_map, fate)
    cands = [i for i in sorted(legal, key=lambda i: -probs[i])[:max(1, topk)]
             if probs[i] >= DICE_MIN_PROB or i == a0]

    kills = [i for i in cands if i < OUTCOME_ACTIONS and out[i][2] > 0]
    pick = a0
    if a0 < OUTCOME_ACTIONS and _is_enemy_attack(env, a0) and out[a0][0] == 0:
        hitters = [i for i in cands if i < OUTCOME_ACTIONS and out[i][0] > 0]
        pick = (kills or hitters or [a0])[0]
    elif kills and not (a0 < OUTCOME_ACTIONS and out[a0][2] > 0):
        pick = kills[0]
    if pick != a0 and explain is not None:
        explain.append((a0, pick))
    return int(pick)


def _is_enemy_attack(env, idx: int) -> bool:
    action = env._action_map.get(idx)
    if action is None or action.target_id is None or action.target_id < 0:
        return False
    eng = env.engine
    try:
        actor = eng.heroes[eng.get_current_actor_id()]
        sd = eng.get_skill_data(action.skill_id, level=actor.skill_tier)
        return bool(sd) and not eng._is_ally_skill(action.skill_id) and             not (sd.heal_min is not None and sd.heal_max is not None)
    except Exception:                                                  # noqa: BLE001
        return False
