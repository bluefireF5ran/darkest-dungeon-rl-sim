"""The three hot-path optimisations of 2026-08-27, each pinned by its own
equivalence check rather than by a timing assertion.

A speed test that asserts a wall clock is a flake generator on a shared
machine, so nothing here times anything. What these pin is the property that
made each optimisation *safe*, because that is what a future edit can silently
break:

    the observation is built ONCE per step, and it is the same vector the
    double build produced;

    `parse_bytes` builds the same field tree as the quadratic scan it replaced,
    over the real capture corpus;

    `inert_trinkets` returns the same frozenset from its memo as from a cold
    probe.

The measurements that motivated them, for the record:

    env step                743 us -> 470 us   (1.58x, observation built once)
    DSON parse of a save    1.8x               (the child walk was quadratic)
    full test suite         62.4 s -> 45.2 s
"""
import hashlib
import struct
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from env.gym_env import DarkestDungeonEnv
from env.expedition_env import ExpeditionEnv
from env.expedition_sequence_env import ExpeditionSequenceEnv
from tests._captures import map_paths, raid_paths
from tests._fixtures import get_store

store = get_store()


# ---------------------------------------------------------------- the obs --

def _trajectory_digest(env, n, seed=1):
    """A deterministic walk, reduced to one hash of every observation, reward
    and flag it produced.

    `seed_scenario` as well as `reset(seed=)`: the first pins the monsters, the
    torch and the loadouts, the second the combat RNG. Pinning only one is how
    a comparison here reads as a real difference when it is not -- that cost an
    hour on the day this file was written, and CLAUDE.md warns about it.
    """
    digest = hashlib.blake2b(digest_size=16)
    env.seed_scenario(seed)
    obs, _ = env.reset(seed=seed)
    digest.update(obs.tobytes())
    for i in range(n):
        legal = np.flatnonzero(env.action_masks())
        obs, reward, term, trunc, _info = env.step(
            int(legal[i % len(legal)]) if len(legal) else 36)
        digest.update(obs.tobytes())
        digest.update(f"{reward:.12g}|{int(term)}{int(trunc)}".encode())
        if term or trunc:
            env.seed_scenario(seed + i)
            obs, _ = env.reset(seed=seed + i)
            digest.update(obs.tobytes())
    return digest.hexdigest()


def test_the_sequence_env_builds_its_observation_exactly_once_per_step():
    """`ExpeditionSequenceEnv.step` calls `super().step()` and then returns its
    OWN `_get_obs()`, so the base's copy was pure waste -- and after a combat
    transition it was stale as well, which is why discarding it was already the
    correct behaviour. `_get_obs` is 279 us of a 736 us step.
    """
    env = ExpeditionSequenceEnv(store=store, sampler_seed=0)
    env.seed_scenario(3)
    env.reset(seed=3)

    calls = []
    real = env._get_obs
    env._get_obs = lambda: (calls.append(1), real())[1]
    steps = 0
    for i in range(40):
        legal = np.flatnonzero(env.action_masks())
        _obs, _r, term, trunc, _i = env.step(int(legal[i % len(legal)]))
        steps += 1
        if term or trunc:
            break
    assert calls, "the observation was never built at all"
    assert len(calls) <= steps, (
        f"{len(calls)} observation builds for {steps} steps -- something is "
        "building it more than once again. See _BASE_STEP_RETURNS_OBS.")


def test_skipping_the_base_build_changes_no_trajectory():
    """The equivalence that makes the skip legal, over ~600 real steps."""
    try:
        ExpeditionSequenceEnv._BASE_STEP_RETURNS_OBS = True
        before = _trajectory_digest(
            ExpeditionSequenceEnv(store=store, sampler_seed=0), 600)
    finally:
        ExpeditionSequenceEnv._BASE_STEP_RETURNS_OBS = False
    after = _trajectory_digest(
        ExpeditionSequenceEnv(store=store, sampler_seed=0), 600)
    assert before == after, (
        "the observation, reward or termination flag moved when the base's "
        "discarded build was skipped -- the two must be identical")


def test_only_a_class_that_overrides_step_may_skip_the_base_build():
    """The flag makes `step` hand back a shared ZERO vector. On a class that
    does not override `step`, that vector reaches the caller as if it were an
    observation -- silently, since it has the right shape and dtype.
    """
    for cls in (DarkestDungeonEnv, ExpeditionEnv, ExpeditionSequenceEnv):
        if cls.__dict__.get("_BASE_STEP_RETURNS_OBS") is False:
            assert "step" in cls.__dict__, (
                f"{cls.__name__} sets _BASE_STEP_RETURNS_OBS = False but does "
                "not override `step`, so nothing rebuilds the observation and "
                "its callers get zeros")
    assert ExpeditionSequenceEnv._BASE_STEP_RETURNS_OBS is False
    assert DarkestDungeonEnv._BASE_STEP_RETURNS_OBS is True
    assert ExpeditionEnv._BASE_STEP_RETURNS_OBS is True


# --------------------------------------------------------------- the save --

def _parse_bytes_quadratic(buf):
    """`data.dson.parse_bytes` as it stood before 2026-08-27, kept verbatim as
    an oracle -- the same device `tests/engine/test_skill_index.py` uses for the skill
    index. The only difference is the `owner` lookup at the bottom.
    """
    from data.dson import Field, _decode_value
    _meta1_size, num_meta1, meta1_off = struct.unpack_from("<III", buf, 0x10)
    num_meta2, meta2_off = struct.unpack_from("<II", buf, 0x2C)
    data_len, data_off = struct.unpack_from("<II", buf, 0x38)
    meta1 = [struct.unpack_from("<IIII", buf, meta1_off + i * 16)
             for i in range(num_meta1)]
    entries = []
    for i in range(num_meta2):
        _hash, off, info = struct.unpack_from("<III", buf, meta2_off + i * 12)
        entries.append((off, info))
    fields = []
    for i, (off, info) in enumerate(entries):
        is_obj = bool(info & 1)
        name_len = (info & 0b11111111100) >> 2
        nstart = data_off + off
        name = buf[nstart:nstart + name_len - 1].decode("utf-8", "replace")
        vstart = (nstart + name_len + 3) & ~3
        vend = (data_off + entries[i + 1][0] if i + 1 < num_meta2
                else data_off + data_len)
        raw = buf[vstart:vend] if vend > vstart else b""
        f = Field(name=name, is_object=is_obj, raw_value=raw)
        if not is_obj:
            f.value = _decode_value(raw)
        fields.append(f)
    for _parent_idx, meta2_idx, direct, _total in meta1:
        parent = fields[meta2_idx]
        cursor = meta2_idx + 1
        for _ in range(direct):
            if cursor >= len(fields):
                break
            parent.children.append(fields[cursor])
            owner = next((m for m in meta1 if m[1] == cursor), None)
            cursor += 1 + (owner[3] if owner else 0)
    return fields[0]


def _shape(field):
    """The whole tree, flattened: depth, name, kind, value and child count."""
    out = []
    stack = [(field, 0)]
    while stack:
        node, depth = stack.pop()
        out.append((depth, node.name, node.is_object, repr(node.value),
                    len(node.children)))
        stack.extend((c, depth + 1) for c in reversed(node.children))
    return out


def test_the_dson_child_walk_builds_the_same_tree_as_the_quadratic_scan():
    """The `owner` lookup was a linear scan of meta1 inside a loop over every
    child of every object -- quadratic in the field count, and 34% of a parse.
    A `persist.roster.json` is a big save and this decoder sits on the bridge's
    poll loop, so it is production speed and not only test speed.

    `setdefault` and not plain assignment in the replacement, because `next()`
    returned the FIRST match: last-wins would be a silent behaviour change of
    exactly the kind `DataStore.skill_index` documents.
    """
    from data.dson import is_dson, parse_bytes

    paths = list(raid_paths())[:60] + list(map_paths())[:60]
    checked = 0
    for path in paths:
        buf = Path(path).read_bytes()
        if not is_dson(buf):
            continue
        assert _shape(parse_bytes(buf)) == _shape(_parse_bytes_quadratic(buf)), path
        checked += 1
    assert checked >= 50, f"only {checked} DSON captures compared"


# ------------------------------------------------------------ the trinkets --

def test_inert_trinkets_memoises_without_changing_its_answer():
    """21 ms a call, and `tests/tools/test_comp_ga.py` called it once per curated
    trinket slot: 28 s, two thirds of the whole suite's wall clock at the time.
    """
    from tools import comp_ga

    cold = comp_ga.inert_trinkets(store)
    comp_ga._INERT_TRINKETS_MEMO.pop(id(store), None)
    recomputed = comp_ga.inert_trinkets(store)
    assert cold == recomputed
    assert comp_ga.inert_trinkets(store) is comp_ga.inert_trinkets(store)


def test_the_memo_holds_the_store_alive_so_an_id_cannot_be_reused():
    """Keyed on `id(store)`, which CPython reuses freely once an object is
    collected. The value keeps a reference to the store, so the id it is keyed
    by cannot come to name a different store while the entry exists.
    """
    from tools import comp_ga

    comp_ga.inert_trinkets(store)
    held, _value = comp_ga._INERT_TRINKETS_MEMO[id(store)]
    assert held is store


# ------------------------------------------------------------ the policy --

@pytest.mark.xdist_group("torch")
def test_the_fast_actor_picks_the_same_action_as_sb3_predict():
    """`eval_expedition.deterministic_actor` skips SB3's per-call `predict`
    scaffolding: 331 us -> 92 us for a forward pass worth ~30 us. That is a
    third of the whole rollout loop, and the rollout loop IS the cost of
    `tools/comp_ga.py` and `tools/comp_leaderboard.py`, neither of which has
    any SGD to hide behind.

    The equivalence argument is that `MaskableCategorical` masks by
    `where(mask, logits, -1e8)` and `deterministic=True` takes
    `probs.argmax(-1)`, and softmax is monotone -- so the shortcut is argmax
    over the masked logits with the same first-index tie-break. This checks it
    against the real thing on every decision of several real expeditions,
    because an argument is not a measurement.
    """
    import torch
    from sb3_contrib import MaskablePPO
    from sb3_contrib.common.maskable.utils import get_action_masks
    from sb3_contrib.common.wrappers import ActionMasker

    from env.gym_env import mask_fn
    from eval_expedition import deterministic_actor

    ckpt = Path(__file__).resolve().parents[2] / "models" / "dd_combat_ppo_full_expedition.zip"
    if not ckpt.exists():
        pytest.skip("the shipped checkpoint is not present")

    torch.set_num_threads(1)
    base = ExpeditionSequenceEnv(store=store, sampler_seed=0)
    env = ActionMasker(base, mask_fn)
    try:
        model = MaskablePPO.load(str(ckpt), env=env, device="cpu")
    except (ValueError, RuntimeError, AssertionError) as exc:
        pytest.skip(f"checkpoint does not load against this observation: {exc}")

    act = deterministic_actor(model)
    assert act.__name__ == "fast_actor", (
        "the fast path refused to engage -- deterministic_actor fell back to "
        "model.predict, so this test is checking nothing")

    # Episode COUNT is not the thing to fix -- a better checkpoint ends
    # episodes sooner, and pinning three of them took this below the floor
    # the moment the 12M linear-decay checkpoint shipped (98 decisions, every
    # one of them agreeing). Draw episodes until the floor is met instead, so
    # the guard measures what it claims and does not re-break on the next
    # improvement.
    decisions = 0
    episode = -1
    while decisions < 100 and episode < 20:
        episode += 1
        base.seed_scenario(episode)
        obs, _ = env.reset(seed=episode)
        term = trunc = False
        while not (term or trunc) and decisions < 400:
            masks = get_action_masks(env)
            fast = act(obs, masks)
            reference = int(model.predict(obs, deterministic=True,
                                          action_masks=masks)[0])
            assert fast == reference, (
                f"fast actor chose {fast}, model.predict chose {reference} "
                f"at decision {decisions}")
            obs, _r, term, trunc, _i = env.step(fast)
            decisions += 1
    assert decisions >= 100, f"only {decisions} decisions compared"


if __name__ == "__main__":
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            print(_name)
            _fn()
