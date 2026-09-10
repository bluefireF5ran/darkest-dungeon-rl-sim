"""The action head that scores a skill by its payload, not by its index.

Two observation redesigns failed the held-out-class falsifier by +46.9pp and
+46.6pp, and giving the policy its allies' whole movesets moved it 0.3pp.
`env/equivariant_policy.py` attacks the remaining identity-coupled surface: the
action index itself, where slot *k* names a different skill for every class.

What has to hold for that to be true, and what these tests pin:

* **permuting the slots permutes the logits, exactly.** That is the definition
  of the equivariance, and it is checkable without training anything;
* **the eleven non-skill actions do NOT move**, because move / provision /
  pass / retreat / abandon mean the same thing for every class and keep their
  own parameters on purpose;
* **the head actually receives gradient.** `_build` creates the optimizer over
  the ORIGINAL `action_net`, so replacing the head without rebuilding the
  optimizer trains everything except the head -- and nothing would report it.
  Training would run, the loss would fall, and the one new component would be
  frozen.

Torch is imported inside the test bodies, never at module level: every xdist
worker collects the whole suite, so a module-level sb3 import is 454 MB paid
sixteen times over. See `tests/suite/test_suite_memory.py`.
"""
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from env.expedition_sequence_env import ExpeditionSequenceEnv
from env.gym_env import (ACTION_SPACE_SIZE, MAX_SKILL_SLOTS, RANKS,
                         SKILL_SLOT_FEATURES)
from tests._fixtures import get_store

store = get_store()


def _env():
    env = ExpeditionSequenceEnv(store=store, sampler_seed=0)
    env.seed_scenario(1)
    return env


def test_the_skill_block_slice_is_the_acting_heros_own_slots():
    """DERIVED, and checked against the live vector rather than a constant.

    Three pinned observation offsets broke on 2026-08-25 alone. The
    enemy-type one-hot width is a property of the loaded store, so this cannot
    be a module constant even in principle.
    """
    env = _env()
    obs, _ = env.reset(seed=1)
    sl = env.skill_block_slice
    assert sl.stop - sl.start == MAX_SKILL_SLOTS * SKILL_SLOT_FEATURES
    actor = env.engine.get_current_actor_id()
    want = np.array(env._skill_slot_vecs(actor), dtype=np.float32)
    assert np.array_equal(obs[sl], want), (
        "the slice no longer holds the acting hero's skill block -- the head "
        "would be scoring actions from whatever else lives at that offset")


def test_the_slice_and_the_action_table_share_one_slot_order():
    """The premise the whole head rests on: slot k of the block IS action
    indices `k*RANKS .. k*RANKS+3`.

    `_skill_slot_vecs` and `_rebuild_action_table` were two independent readers
    of the slot order until 2026-08-24, and a mismatch is invisible -- the mask
    is built from `_action_map` alone, so it stays self-consistent while
    describing the wrong skill.
    """
    env = _env()
    env.reset(seed=1)
    for _ in range(12):
        actor = env.engine.get_current_actor_id()
        if actor is None:
            break
        order = env._slot_order(actor)
        for idx, action in env._action_map.items():
            if idx >= MAX_SKILL_SLOTS * RANKS or action.skill_id.startswith("__"):
                continue
            slot = idx // RANKS
            assert order[slot] == action.skill_id, (
                f"action {idx} fires {action.skill_id} but slot {slot} of the "
                f"observation describes {order[slot]}")
        legal = np.flatnonzero(env.action_masks())
        if not len(legal):
            break
        _o, _r, term, trunc, _i = env.step(int(legal[0]))
        if term or trunc:
            break


@pytest.mark.xdist_group("torch")
def test_permuting_the_slots_permutes_the_logits_and_nothing_else():
    """The equivariance itself, which is the entire claim."""
    import torch as th

    from env.equivariant_policy import SKILL_ACTIONS, SkillEquivariantHead

    env = _env()
    obs, _ = env.reset(seed=1)
    sl = env.skill_block_slice
    head = SkillEquivariantHead(latent_dim=256, skill_block=(sl.start, sl.stop))

    o = th.tensor(obs).unsqueeze(0)
    latent = th.randn(1, 256)
    before = head(latent, o)
    assert before.shape == (1, ACTION_SPACE_SIZE)

    perm = [3, 1, 0, 6, 2, 5, 4]
    block = o[:, sl.start:sl.stop].reshape(1, MAX_SKILL_SLOTS, SKILL_SLOT_FEATURES)
    shuffled = o.clone()
    shuffled[:, sl.start:sl.stop] = block[:, perm, :].reshape(1, -1)
    after = head(latent, shuffled)

    a = before[0, :SKILL_ACTIONS].reshape(MAX_SKILL_SLOTS, RANKS)
    b = after[0, :SKILL_ACTIONS].reshape(MAX_SKILL_SLOTS, RANKS)
    assert th.allclose(a[perm], b, atol=1e-6), (
        "the logits did not follow the slots -- the head is reading an index "
        "somewhere, which is the thing it exists to remove")
    assert th.allclose(before[0, SKILL_ACTIONS:], after[0, SKILL_ACTIONS:],
                       atol=1e-6), "the non-skill actions moved"


@pytest.mark.xdist_group("torch")
def test_the_head_holds_no_per_slot_parameters():
    """A structural check, so the equivariance cannot be lost by a later edit
    that happens to keep the numeric test passing on one permutation."""
    from env.equivariant_policy import (DESC_FEATURES, OTHER_ACTIONS,
                                        SkillEquivariantHead)

    head = SkillEquivariantHead(latent_dim=256, skill_block=(0, MAX_SKILL_SLOTS
                                                             * SKILL_SLOT_FEATURES))
    for name, param in head.named_parameters():
        if name == "other_desc":
            assert param.shape == (OTHER_ACTIONS, DESC_FEATURES)
            continue
        assert MAX_SKILL_SLOTS not in param.shape, (
            f"{name} has a per-slot dimension {tuple(param.shape)} -- shared "
            "parameters are what make the head equivariant")
        assert ACTION_SPACE_SIZE not in param.shape, name


@pytest.mark.xdist_group("torch")
def test_the_optimizer_actually_reaches_the_new_head():
    """`_build` creates the optimizer over the ORIGINAL action_net. Replacing
    the head without rebuilding it trains everything except the head, and
    nothing reports it: the run completes, the loss falls, and the one new
    component never moves."""
    import torch as th
    from sb3_contrib import MaskablePPO
    from sb3_contrib.common.wrappers import ActionMasker

    from env.equivariant_policy import make_policy_class, policy_kwargs_for
    from env.gym_env import mask_fn

    base = _env()
    env = ActionMasker(base, mask_fn)
    model = MaskablePPO(make_policy_class(), env, device="cpu", n_steps=64,
                        batch_size=32, n_epochs=1, seed=0,
                        policy_kwargs=policy_kwargs_for(env))

    head_params = {id(p) for p in model.policy.action_net.parameters()}
    optim_params = {id(p) for group in model.policy.optimizer.param_groups
                    for p in group["params"]}
    assert head_params <= optim_params, (
        "the head's parameters are not in the optimizer -- it would train "
        "frozen and every metric would look normal")

    before = [p.detach().clone() for p in model.policy.action_net.parameters()]
    model.learn(total_timesteps=128)
    after = list(model.policy.action_net.parameters())
    assert any(not th.allclose(b, a) for b, a in zip(before, after)), (
        "no head parameter moved after a learn() call")


@pytest.mark.xdist_group("torch")
def test_it_still_behaves_like_an_ordinary_maskable_policy():
    """Every tool here drives a MaskablePPO. The head is the only change, so
    predict, masking and save/load must all keep working."""
    import tempfile

    from sb3_contrib import MaskablePPO
    from sb3_contrib.common.maskable.utils import get_action_masks
    from sb3_contrib.common.wrappers import ActionMasker

    from env.equivariant_policy import make_policy_class, policy_kwargs_for
    from env.gym_env import mask_fn

    base = _env()
    env = ActionMasker(base, mask_fn)
    model = MaskablePPO(make_policy_class(), env, device="cpu", n_steps=64,
                        batch_size=32, n_epochs=1, seed=0,
                        policy_kwargs=policy_kwargs_for(env))
    obs, _ = env.reset(seed=1)
    masks = get_action_masks(env)
    action, _state = model.predict(obs, deterministic=True, action_masks=masks)
    assert masks[int(action)], "the policy chose a masked-out action"

    with tempfile.TemporaryDirectory() as td:
        path = str(Path(td) / "m.zip")
        model.save(path)
        again = MaskablePPO.load(path, env=env, device="cpu")
    action2, _s = again.predict(obs, deterministic=True, action_masks=masks)
    assert int(action) == int(action2), "save/load changed the chosen action"


@pytest.mark.xdist_group("torch")
def test_the_fast_actor_handles_the_new_head():
    """`eval_expedition.deterministic_actor` is the 3.4x path every eval and
    every comp search runs on, and it calls `policy.action_net(latent_pi)`
    directly. The equivariant head needs the observation too, so without the
    `NEEDS_OBS` marker this raises TypeError inside every rollout -- loudly,
    but only once a real evaluation is already running.
    """
    import tempfile

    from sb3_contrib import MaskablePPO
    from sb3_contrib.common.maskable.utils import get_action_masks
    from sb3_contrib.common.wrappers import ActionMasker

    from env.equivariant_policy import make_policy_class, policy_kwargs_for
    from env.gym_env import mask_fn
    from eval_expedition import deterministic_actor

    base = _env()
    env = ActionMasker(base, mask_fn)
    model = MaskablePPO(make_policy_class(), env, device="cpu", n_steps=64,
                        batch_size=32, n_epochs=1, seed=0,
                        policy_kwargs=policy_kwargs_for(env))
    assert getattr(model.policy.action_net, "NEEDS_OBS", False)

    act = deterministic_actor(model)
    assert act.__name__ == "fast_actor", "the fast path refused to engage"
    obs, _ = env.reset(seed=1)
    for _ in range(25):
        masks = get_action_masks(env)
        fast = act(obs, masks)
        ref = int(model.predict(obs, deterministic=True, action_masks=masks)[0])
        assert fast == ref, (fast, ref)
        obs, _r, term, trunc, _i = env.step(fast)
        if term or trunc:
            break


def test_the_policy_module_is_not_imported_by_the_env_package():
    """It imports torch at module level, so `from env import ...` reaching it
    would drag 454 MB into every xdist worker's collection."""
    src = (Path(__file__).resolve().parents[2] / "env" / "__init__.py"
           ).read_text(encoding="utf-8")
    assert "equivariant_policy" not in src, (
        "env/__init__.py imports the equivariant policy, which pulls torch "
        "into every env import. train.py imports it lazily inside main.")


if __name__ == "__main__":
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            print(_name)
            _fn()
