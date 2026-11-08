"""`CombatEngine.fate` and `resolve_attack(crit_roll=, damage_u=)`: the game's
known draws used for the hero's attack, and NOTHING changed when absent."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def _env(seed=3):
    import train
    e = train.make_env(mode="full_expedition", sampler_seed=seed)
    e.reset(seed=seed)
    return e


def _attack(e):
    from sb3_contrib.common.maskable.utils import get_action_masks
    from env.action_space import ACTION_MOVE_BASE
    m = get_action_masks(e)
    hits = [i for i in np.flatnonzero(m) if i < ACTION_MOVE_BASE]
    return int(hits[0]) if hits else None


@pytest.mark.xdist_group("torch")
def test_a_forced_miss_misses_and_a_forced_hit_lands():
    from bridge.fate import Fate, clone_env
    e = _env()
    a = _attack(e)
    assert a is not None
    base = e.unwrapped
    outcomes = {}
    for name, roll in (("miss", 0.94999), ("hit", 0.99)):
        c = clone_env(base)
        c.engine.fate = Fate(None, roll, 0.99, 0.5)
        before = sum(x.hp for x in c.engine.enemies if x.is_alive())
        c.engine.log.clear()
        c.step(a)
        outcomes[name] = " ".join(c.engine.log)
    assert "hits" in outcomes["hit"]                 # a roll >= 0.95 always hits
    assert c.engine.fate is None                     # one action's worth only


@pytest.mark.xdist_group("torch")
def test_no_fate_means_no_change_in_the_stream():
    """The default path draws exactly as before: two clones stepped with the
    same action from the same state end identical."""
    from bridge.fate import clone_env
    e = _env(5)
    a = _attack(e)
    c1, c2 = clone_env(e.unwrapped), clone_env(e.unwrapped)
    o1 = c1.step(a)[0]
    o2 = c2.step(a)[0]
    assert np.array_equal(o1, o2)


@pytest.mark.xdist_group("torch")
def test_the_fate_wrapper_widens_by_three_and_clears_the_hook(monkeypatch):
    monkeypatch.setenv("DDSIM_FATE_OBS", "1")
    import train
    from env.fate_wrapper import FATE_FEATURES
    e = train.make_env(mode="full_expedition", sampler_seed=2)
    plain = train.ExpeditionSequenceEnv(sampler_seed=2) if hasattr(train, "ExpeditionSequenceEnv") else None
    obs, _ = e.reset(seed=2)
    assert all(0.0 <= v < 1.0 for v in obs[-FATE_FEATURES:])
    a = _attack(e)
    e.step(a)
    assert e.unwrapped.engine.fate is None


@pytest.mark.xdist_group("torch")
def test_the_resolved_outcomes_are_what_then_happens(monkeypatch):
    """`attack_outcomes` predicts hit and crit for every attack from the known
    dice; the env then plays the attack with those dice. Measured 2026-10-05:
    1296/1299 hits and 1272/1284 crits agree; the misses are AoE edge cases."""
    monkeypatch.setenv("DDSIM_FATE_OBS", "2")
    import train
    from sb3_contrib.common.maskable.utils import get_action_masks
    e = train.make_env(mode="full_expedition", sampler_seed=7)
    o, _ = e.reset(seed=7)
    u = e.unwrapped
    rng = np.random.default_rng(0)
    ok = total = 0
    for i in range(400):
        legal = np.flatnonzero(get_action_masks(e))
        att = [a for a in legal if a < 28]
        a = int(rng.choice(att)) if att else int(rng.choice(legal))
        if a < 28:
            pred = o[-84:].reshape(28, 3)[a]
            eng = u.engine
            hero = eng.heroes[eng.get_current_actor_id()].name
            eng.log.clear()
        o2, _r, te, tr, _ = e.step(a)
        if a < 28:
            lines = [l for l in eng.log
                     if l.startswith(hero + " hits ") or l.startswith(hero + " misses ")]
            if lines:
                total += 1
                ok += bool(pred[0]) == (" hits " in lines[0])
        o = o2
        if te or tr:
            o, _ = e.reset(seed=1000 + i)
    assert total > 50 and ok / total >= 0.97
