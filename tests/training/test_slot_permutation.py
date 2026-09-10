"""Slot permutation — the one-run probe of why cross-class transfer fails.

The held-out-class falsifier came back +46.6pp negative twice: once on mechanic
payloads alone, and again after `_hero_capabilities` gave the policy its allies'
whole movesets. Neither was an observability fix, because observability was
never the blocker.

The blocker is the ACTION SPACE. An action index is `slot * RANKS + rank`, and
slot *k* names a different skill for every class — an ally heal sits at slot 3
for Vestal and Occultist, 4 for Crusader and Plague Doctor, 5 for Arbalest. So
"press the slot that heals" is a per-class fact the policy has to memorise, and
for a class it never trained on there is nothing to look up.

`slot_permute=True` shuffles which skill occupies which index, fixed per
EPISODE. Across training the policy then sees every skill at every index, so an
index cannot be memorised and the choice has to come from the slot's own payload
features. If transfer does not move under it, the action-space theory is wrong
too and the whole line closes for the price of one run.

**The trap these tests exist for.** The slot order used to be read at two
independent sites — the action map and the observation's skill block — coupled
only by both calling the same engine method. Permute one and not the other and
the policy reads slot k's features while index `k*RANKS + r` fires slot j's
skill. **The action mask cannot catch that**: the mask is built from
`_action_map` alone, so it stays perfectly self-consistent while describing the
wrong skill. Both now go through `_slot_order`.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests._fixtures import get_store

from env.gym_env import (ACTION_MOVE_BASE, ACTION_PASS, ACTION_RETREAT,
                         DarkestDungeonEnv, MAX_SKILL_SLOTS, RANKS)
import pytest

store = get_store()


def _env(permute, seed=0):
    e = DarkestDungeonEnv(store=store, slot_permute=permute)
    e.reset(seed=seed)
    return e


def test_off_by_default_and_byte_identical():
    """Every archived checkpoint and every existing test depends on the
    unpermuted order, so the flag must change nothing when unset."""
    a = _env(False)
    b = DarkestDungeonEnv(store=store)
    b.reset(seed=0)
    for hid in range(len(a.engine.heroes)):
        assert a._slot_order(hid) == a.engine.get_hero_full_skill_ids(hid)
        assert a._slot_order(hid) == b._slot_order(hid)
    print("  PASS test_off_by_default_and_byte_identical")


def test_a_permutation_is_a_permutation():
    """Same skills, different order — never a drop, never a duplicate. A
    permutation that lost a skill would silently shrink the movepool."""
    e = _env(True)
    for hid in range(len(e.engine.heroes)):
        full = e.engine.get_hero_full_skill_ids(hid)
        got = e._slot_order(hid)
        assert sorted(got) == sorted(full), (got, full)
        assert len(set(got)) == len(got)
    print("  PASS test_a_permutation_is_a_permutation")


def test_it_is_stable_within_an_episode():
    """Per-step permutation would make the action space non-stationary inside
    one decision sequence, which is a different and incoherent experiment."""
    e = _env(True)
    first = {h: e._slot_order(h) for h in range(len(e.engine.heroes))}
    for _ in range(20):
        for h in range(len(e.engine.heroes)):
            assert e._slot_order(h) == first[h]
    print("  PASS test_it_is_stable_within_an_episode")


def test_it_is_redrawn_between_episodes():
    """The augmentation itself. Over many episodes a skill must appear at many
    indices, or the policy can still memorise one."""
    e = _env(True)
    seen = set()
    for ep in range(40):
        e.reset(seed=ep)
        order = e._slot_order(0)
        if order:
            seen.add(order[0])
    assert len(seen) > 1, f"slot 0 always held {seen}; the order is not redrawn"
    print(f"  PASS test_it_is_redrawn_between_episodes ({len(seen)} skills seen at slot 0)")


def test_the_action_map_and_the_observation_agree():
    """THE test. This is the failure the action mask cannot report.

    For every legal skill action, the index's slot number must equal that
    skill's position in `_slot_order` — the same list `_skill_slot_vecs` walks
    to build the observation. If the two ever diverge the policy is reading one
    skill's features and firing another's, with a perfectly consistent mask.
    """
    for permute in (False, True):
        for ep in range(12):
            e = DarkestDungeonEnv(store=store, slot_permute=permute)
            e.reset(seed=ep)
            for _ in range(12):
                actor = e.engine.get_current_actor_id()
                if actor is None:
                    break
                order = e._slot_order(actor)
                pos = {sid: i for i, sid in enumerate(order)}
                for idx, action in e._action_map.items():
                    if idx >= ACTION_MOVE_BASE:
                        continue          # move / provision / pass / retreat
                    slot, _rank = divmod(idx, RANKS)
                    assert action.skill_id in pos, (
                        f"action {idx} fires {action.skill_id}, not in the slot order")
                    assert pos[action.skill_id] == slot, (
                        f"permute={permute} ep={ep}: index {idx} is slot {slot} "
                        f"but {action.skill_id} sits at slot {pos[action.skill_id]}")
                mask = e.action_masks()
                legal = [i for i, ok in enumerate(mask) if ok]
                if not legal:
                    break
                _o, _r, term, trunc, _i = e.step(legal[0])
                if term or trunc:
                    break
    print("  PASS test_the_action_map_and_the_observation_agree")


def test_the_reserved_indices_are_untouched():
    """Permuting the seven skill slots must not disturb move / provision /
    pass / retreat, which are absolute indices above the skill block and are
    what `tools/playbook_policy.py` and `bridge/run.py` dispatch on."""
    e = _env(True)
    assert ACTION_MOVE_BASE == MAX_SKILL_SLOTS * RANKS
    for _ in range(30):
        actor = e.engine.get_current_actor_id()
        if actor is None:
            break
        for idx, action in e._action_map.items():
            if idx == ACTION_PASS:
                assert action.skill_id == "__pass__"
            elif idx == ACTION_RETREAT:
                assert action.skill_id == "__retreat__"
            elif ACTION_MOVE_BASE <= idx < ACTION_MOVE_BASE + RANKS:
                assert action.skill_id.startswith("__move")
        mask = e.action_masks()
        legal = [i for i, ok in enumerate(mask) if ok]
        if not legal:
            break
        _o, _r, term, trunc, _i = e.step(legal[0])
        if term or trunc:
            break
    print("  PASS test_the_reserved_indices_are_untouched")


def test_train_py_actually_passes_the_flag_to_every_env():
    """A flag that is ECHOED but not WIRED is the failure this session kept
    finding, and this one shipped that way for a minute: `train.py` printed
    `slot_permute=True` in its distribution line while no env ever received it.

    All three construction sites matter -- the vec env the policy learns from,
    the eval env `MaskableEvalCallback` scores on (a mismatch there would grade
    a permuted policy on an unpermuted board), and the `--play`/`--check` env.
    """
    import train

    on = train.make_env(mode="full_expedition", slot_permute=True)
    assert on.unwrapped._slot_perm_rng is not None, "make_env dropped the flag"
    off = train.make_env(mode="full_expedition")
    assert off.unwrapped._slot_perm_rng is None

    src = Path(train.__file__).read_text(encoding="utf-8")
    # Every make_env/make_vec_env call in main() must forward it.
    assert src.count("slot_permute=args.slot_permute") >= 3, (
        "a construction site in train.py is not forwarding --slot-permute; "
        "the distribution echo will lie about it")
    print("  PASS test_train_py_actually_passes_the_flag_to_every_env")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
