"""A policy head that scores an action by WHAT IT DOES, not by where it sits.

Fran, 2026-08-28: *"the model needs to understand patterns rather than specific
classes or skills, to extrapolate them into other modded classes"*.

That claim has been tested twice and failed twice, both times on the wrong axis.
The mechanics-not-identity observation came back at **+46.9pp** excess gap on a
held-out class; adding the three-pool hero capability profile moved it **0.3pp**.
Observability was never the blocker.

**The action space is.** An index is `slot * RANKS + rank`, and slot *k* names a
different skill for every class -- an ally heal sits at slot 3 for Vestal and
Occultist, 4 for Crusader and Plague Doctor, 5 for Arbalest. "Press the slot
that heals" is a per-class fact a policy can only memorise, and for a class it
never trained on there is nothing to look up, however well the observation
describes the skill sitting there.

## What this replaces

`MaskableActorCriticPolicy` ends in `Linear(latent -> 39)`, so output *k* means
"slot k of whichever class happens to be acting" and the weights that produce it
are shared across every class that ever stood there. This head instead scores
each action through ONE function of that action's own description:

    logit(slot k, rank r) = <q(latent), key(payload_k, rank_r)> + bias(...)

`q` and `key` are shared across all seven slots, so permuting the slots permutes
the logits and nothing else -- the head is equivariant by construction. A skill's
logit follows its 45-float mechanic payload, which is the same payload a modded
class produces, so an unseen class arrives described in a language the head
already reads.

## Three design decisions worth stating

* **The payload is SLICED OUT OF THE OBSERVATION.** The 2026-08-23 rewrite
  already put `SKILL_SLOT_FEATURES` per slot in there, and `_skill_slot_vecs`
  and `_rebuild_action_table` both read `_slot_order`, so slot *k* of the block
  IS action indices `k*RANKS .. k*RANKS+3`. That shared order is what
  `--slot-permute` was refactored to guarantee. No new plumbing, and no second
  copy of the layout to drift.

* **The eleven non-skill actions KEEP a learned embedding, and that is not a
  contradiction.** Move-to-rank-2, "eat", pass, retreat and abandon mean the
  same thing for every class in the game. There is no cross-class generalisation
  to be had there, so giving them per-index parameters costs nothing and asking
  them to be described by a zero payload would only add noise. The equivariance
  that matters is over the 28 skill actions.

* **A pointer head, not an MLP over the concatenation.** Scoring
  `MLP(concat(latent, desc))` for all 39 actions is ~160x the arithmetic of the
  linear layer it replaces, and SGD is already ~73% of a training run's wall
  clock. A projected dot product is ~350k MACs against the trunk's 419k, so the
  head roughly holds the step cost while buying the equivariance.

## What it does NOT do

It does not read a class id, a class one-hot, or a skill id -- there is nowhere
for one to enter. That is the point, and `tests/training/test_equivariant_policy.py`
pins it by permuting a hero's slots and requiring the logits to follow.
"""
from __future__ import annotations

import numpy as np
import torch as th
from torch import nn

from .gym_env import (ACTION_SPACE_SIZE, MAX_SKILL_SLOTS, RANKS,
                      SKILL_SLOT_FEATURES)

# Action indices `[0 .. SKILL_ACTIONS-1]` are `slot * RANKS + rank`; everything
# above is the fixed set (move x4, provision x4, pass, retreat, abandon).
# Derived, because `ACTION_SPACE_SIZE` is `ACTION_ABANDON + 1` and this file
# must not be the second place that knows the layout.
SKILL_ACTIONS = MAX_SKILL_SLOTS * RANKS
OTHER_ACTIONS = ACTION_SPACE_SIZE - SKILL_ACTIONS

# One skill action's description: the slot's own feature vector, plus which
# rank it is aimed at, plus a flag separating it from the embedded eleven.
DESC_FEATURES = SKILL_SLOT_FEATURES + RANKS + 1


class SkillEquivariantHead(nn.Module):
    """`latent_pi` + the observation's skill block -> one logit per action."""

    # A standard head is `action_net(latent_pi)`. This one needs the
    # observation as well, and `eval_expedition.deterministic_actor` -- the
    # 3.4x fast path every eval and search runs on -- calls the head directly.
    # A marker attribute rather than a signature check or an isinstance test:
    # the caller must not have to import this module (it pulls torch) to find
    # out how to call the head it is holding.
    NEEDS_OBS = True

    def __init__(self, latent_dim: int, key_dim: int = 64,
                 skill_block: tuple[int, int] | None = None):
        super().__init__()
        if skill_block is None:
            raise ValueError("skill_block=(start, stop) is required; take it "
                             "from `env.skill_block_slice`, never a literal")
        self.start, self.stop = skill_block
        expected = MAX_SKILL_SLOTS * SKILL_SLOT_FEATURES
        if self.stop - self.start != expected:
            raise ValueError(f"skill block is {self.stop - self.start} wide, "
                             f"expected {expected}")
        self.key_dim = key_dim

        # Shared across all seven slots -- this is the equivariance.
        self.key = nn.Sequential(
            nn.Linear(DESC_FEATURES, key_dim), nn.Tanh(),
            nn.Linear(key_dim, key_dim))
        self.query = nn.Linear(latent_dim, key_dim)
        # A per-action scalar offset derived from the description, so a skill
        # can be generally good or bad independently of the state.
        self.desc_bias = nn.Linear(DESC_FEATURES, 1)
        # The eleven state-independent actions. See the module docstring for
        # why these keep per-index parameters.
        self.other_desc = nn.Parameter(th.zeros(OTHER_ACTIONS, DESC_FEATURES))
        nn.init.normal_(self.other_desc, std=0.1)

        # `_build`'s ortho init does not reach a replaced head, so the small
        # final gain the policy net normally gets is applied here instead: a
        # near-uniform initial policy is what keeps early PPO from collapsing
        # onto one action.
        for module in (self.key[-1], self.query, self.desc_bias):
            nn.init.orthogonal_(module.weight, gain=0.01)
            if module.bias is not None:
                nn.init.zeros_(module.bias)

        # The rank one-hot block, built once: rows are the RANKS targets a
        # skill action can name, repeated across slots.
        rank_onehot = th.eye(RANKS)
        self.register_buffer("_rank_onehot", rank_onehot, persistent=False)

    def descriptors(self, obs: th.Tensor) -> th.Tensor:
        """(B, ACTION_SPACE_SIZE, DESC_FEATURES) -- what each action IS."""
        batch = obs.shape[0]
        block = obs[:, self.start:self.stop].reshape(
            batch, MAX_SKILL_SLOTS, SKILL_SLOT_FEATURES)
        # slot k repeated for each of its RANKS targets, matching
        # `slot * RANKS + rank`.
        per_slot = block.repeat_interleave(RANKS, dim=1)          # (B, 28, F)
        ranks = self._rank_onehot.repeat(MAX_SKILL_SLOTS, 1)      # (28, RANKS)
        ranks = ranks.unsqueeze(0).expand(batch, -1, -1)
        flag = th.ones(batch, SKILL_ACTIONS, 1, device=obs.device, dtype=obs.dtype)
        skill_desc = th.cat([per_slot, ranks.to(obs.dtype), flag], dim=2)

        other = self.other_desc.unsqueeze(0).expand(batch, -1, -1).to(obs.dtype)
        return th.cat([skill_desc, other], dim=1)

    def forward(self, latent_pi: th.Tensor, obs: th.Tensor) -> th.Tensor:
        desc = self.descriptors(obs)                              # (B, A, D)
        keys = self.key(desc)                                     # (B, A, K)
        query = self.query(latent_pi).unsqueeze(2)                # (B, K, 1)
        scores = th.bmm(keys, query).squeeze(2) / (self.key_dim ** 0.5)
        return scores + self.desc_bias(desc).squeeze(2)


def make_policy_class():
    """Build the policy subclass lazily.

    A module-level `class X(MaskableActorCriticPolicy)` would import sb3 at
    import time, and `tests/suite/test_suite_memory.py` measures what that costs:
    every xdist worker collects the whole suite, so one such import is 454 MB
    paid sixteen times over. `env/__init__.py` deliberately does not import
    this module either.
    """
    from sb3_contrib.common.maskable.policies import MaskableActorCriticPolicy

    class SkillEquivariantPolicy(MaskableActorCriticPolicy):
        """`MaskableActorCriticPolicy` with the pointer head above.

        The only structural change is `action_net`. The features extractor, the
        shared trunk, the value head, the masking and the distribution are all
        untouched, so this remains an ordinary MaskablePPO policy and every
        tool that drives one keeps working.
        """

        def __init__(self, *args, skill_block=None, key_dim: int = 64, **kwargs):
            self._skill_block = skill_block
            self._key_dim = key_dim
            self._obs_for_head: th.Tensor | None = None
            super().__init__(*args, **kwargs)

        def _build(self, lr_schedule) -> None:
            super()._build(lr_schedule)
            self.action_net = SkillEquivariantHead(
                latent_dim=self.mlp_extractor.latent_dim_pi,
                key_dim=self._key_dim, skill_block=self._skill_block)
            # The optimizer was created over the OLD action_net's parameters,
            # so it has to be rebuilt or the head never receives a gradient
            # step -- and nothing would report it, because training would run
            # perfectly happily with a frozen head.
            self.optimizer = self.optimizer_class(
                self.parameters(), lr=lr_schedule(1), **self.optimizer_kwargs)

        # `_get_action_dist_from_latent` receives only `latent_pi`, and the head
        # needs the observation. The three entry points that call it all have
        # `obs` in hand, so each stashes it first. Keeping the stash to exactly
        # these three is what makes it safe: there is no other path into the
        # distribution.
        def forward(self, obs, deterministic: bool = False, action_masks=None):
            self._obs_for_head = obs
            return super().forward(obs, deterministic=deterministic,
                                   action_masks=action_masks)

        def evaluate_actions(self, obs, actions, action_masks=None):
            self._obs_for_head = obs
            return super().evaluate_actions(obs, actions,
                                            action_masks=action_masks)

        def get_distribution(self, obs, action_masks=None):
            self._obs_for_head = obs
            return super().get_distribution(obs, action_masks=action_masks)

        def _get_action_dist_from_latent(self, latent_pi):
            obs = self._obs_for_head
            if obs is None:
                raise RuntimeError(
                    "the equivariant head was reached without an observation. "
                    "Every path into the distribution must go through forward, "
                    "evaluate_actions or get_distribution.")
            logits = self.action_net(latent_pi, obs)
            return self.action_dist.proba_distribution(action_logits=logits)

    return SkillEquivariantPolicy


def policy_kwargs_for(env, net_arch=None, key_dim: int = 64) -> dict:
    """`policy_kwargs` for a MaskablePPO built on `env`.

    Takes the skill block from the env itself rather than a constant: the
    enemy-type one-hot width is a property of the loaded store, so the offset
    is not knowable at import time. See `DarkestDungeonEnv.skill_block_slice`.
    """
    # A `SubprocVecEnv` has no local env to unwrap -- its workers live in other
    # processes -- so the VecEnv `get_attr` API is the only way to reach the
    # property. `DummyVecEnv` and a bare env are handled by the fallbacks.
    sl = None
    if hasattr(env, "get_attr"):
        try:
            sl = env.get_attr("skill_block_slice")[0]
        except (AttributeError, IndexError, NotImplementedError):
            sl = None
    if sl is None:
        base = env.unwrapped if hasattr(env, "unwrapped") else env
        while sl is None:
            sl = getattr(base, "skill_block_slice", None)
            if sl is not None:
                break
            inner = getattr(base, "envs", None)
            if inner:
                base = inner[0].unwrapped if hasattr(inner[0], "unwrapped") else inner[0]
            elif hasattr(base, "venv"):
                base = base.venv
            elif hasattr(base, "env"):
                base = base.env
            else:
                raise AttributeError(
                    "could not reach skill_block_slice through the env "
                    f"wrappers around {type(env).__name__}")
    return {
        "skill_block": (sl.start, sl.stop),
        "key_dim": key_dim,
        "net_arch": net_arch or dict(pi=[256, 256], vf=[256, 256]),
    }
