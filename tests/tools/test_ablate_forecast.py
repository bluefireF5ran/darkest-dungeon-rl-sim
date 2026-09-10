"""The ablation harness must blind exactly the forecast, and nothing else.

`tools/ablate_forecast.py` is the pre-committed falsifier for the threat
forecast: evaluate one checkpoint twice, once with those features zeroed. If
it blinds the wrong indices the answer is meaningless in a way no eval would
reveal -- a wrong-index ablation still produces a plausible-looking delta.

Sizing an observation block by hand is exactly how the enemy slot once landed
6 short, so the indices are derived from the layout constants and this checks
the derivation against the observation the env actually emits.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import numpy as np

from env.gym_env import HERO_SLOT_SCALARS, HERO_THREAT_FORECAST, RANKS
from env.expedition_sequence_env import (EXPEDITION_CONTEXT_FEATURES,
                                         ExpeditionSequenceEnv)
from tools.ablate_forecast import PARTY_FORECAST_GLOBALS, forecast_indices
import pytest



def _env(seed=1):
    e = ExpeditionSequenceEnv(sampler_seed=0)
    e.reset(seed=seed, options={"sampler_seed": seed})
    return e


def test_it_blinds_exactly_the_expected_count():
    e = _env()
    idx = forecast_indices(e.observation_space.shape[0])
    assert len(idx) == RANKS * HERO_THREAT_FORECAST + PARTY_FORECAST_GLOBALS
    assert len(set(idx.tolist())) == len(idx), "duplicate indices"
    assert idx.min() >= 0 and idx.max() < e.observation_space.shape[0]


def test_the_hero_indices_are_the_TAIL_of_each_hero_slot():
    """They were appended after the heal headroom, so they are the last
    HERO_THREAT_FORECAST floats of each slot -- and the headroom must sit
    immediately before them, not inside them."""
    e = _env()
    idx = set(forecast_indices(e.observation_space.shape[0]).tolist())
    for rank in range(RANKS):
        end = (rank + 1) * HERO_SLOT_SCALARS
        assert set(range(end - HERO_THREAT_FORECAST, end)) <= idx
        assert (end - HERO_THREAT_FORECAST - 1) not in idx, (
            "the heal headroom is being blinded too")


def test_the_globals_sit_before_the_expedition_context_block():
    """The trap that caught the first draft of this: `ExpeditionSequenceEnv`
    appends EXPEDITION_CONTEXT_FEATURES AFTER the base globals, so the three
    party forecast floats are not at the tail of the vector."""
    e = _env()
    w = e.observation_space.shape[0]
    idx = set(forecast_indices(w).tolist())
    g_end = w - EXPEDITION_CONTEXT_FEATURES
    assert set(range(g_end - PARTY_FORECAST_GLOBALS, g_end)) <= idx
    for i in range(g_end, w):
        assert i not in idx, "an expedition-context feature is being blinded"


def test_every_blinded_index_really_moves_when_the_forecast_does():
    """Derivation checked against the live vector: stunning the whole board
    zeroes the forecast, so each blinded index must respond.

    Run in TWO regimes, because `p_deaths_door` and `p_death` are mutually
    exclusive per hero -- a hero is either at Death's Door or is not -- so no
    single party state can make all six features non-zero at once.

    The converse is NOT asserted and must not be: stunning also flips each
    enemy slot's own stun flag and moves `_threat_ratio`, which excludes
    stunned enemies. Those are legitimately changed by the intervention.
    """
    for at_deaths_door in (False, True):
        e = _env()
        live = [h for h in e.engine.heroes if h.is_alive()]
        assert live
        for h in live:
            h.hp = 1
            if at_deaths_door:
                h.is_deaths_door = True
                h.hp = 0
        before = e._get_obs().copy()
        for x in e.engine.enemies:
            x.is_stunned = True
        after = e._get_obs()
        moved = set(np.nonzero(~np.isclose(before, after))[0].tolist())

        # In each regime, every index that was NON-ZERO must have moved. The
        # zero ones are the other half of the exclusive pair.
        #
        # A CLAMPED feature already sitting at its ceiling is the third case,
        # and it is saturation rather than an inert index: SECURED is
        # `_clamp01(heal throughput / incoming damage)`, so any party whose
        # healer out-paces the board reads 1.0 before AND after. That became
        # the common case with the 2026-08-26 comp re-export. Exempt it, but
        # prove the exemption is really the clamp by checking it did not move
        # off 1.0 either.
        all_idx = forecast_indices(len(before))
        # SECURED is the FIRST of the party globals, which follow the four
        # hero blocks -- derived, never written down, same rule as the rest of
        # this file.
        secured = int(all_idx[RANKS * HERO_THREAT_FORECAST])
        exempt = set()
        if before[secured] == 1.0:
            assert after[secured] == 1.0, (
                "SECURED left the clamp ceiling, so it was not saturated")
            exempt.add(secured)
        live_idx = [i for i in all_idx if before[i] != 0.0 and i not in exempt]
        assert live_idx, f"nothing was forecast at all (deaths_door={at_deaths_door})"
        assert set(live_idx) <= moved, (
            f"blinded but inert at deaths_door={at_deaths_door}: "
            f"{sorted(set(live_idx) - moved)}")


def test_p_deaths_door_and_p_death_are_mutually_exclusive():
    """The mechanic, and the reason there are two features rather than one.

    A hero brought to 0 HP ENTERS Death's Door and survives it. Only a hero
    ALREADY there dies, on any landed damaging hit at `deathblow_chance` minus
    resist. So for any one hero exactly one of the pair can be live, and
    folding them into a single "danger" number would conflate two very
    different severities.
    """
    e = _env()
    live = [h for h in e.engine.heroes if h.is_alive()]
    assert len(live) >= 2
    for h in live:
        h.hp = 1
    live[0].is_deaths_door = True
    live[0].hp = 0

    f = e.engine.threat_forecast()
    dd = f[live[0].id]
    assert dd["p_deaths_door"] == 0.0, "a hero already at Death's Door cannot enter it"
    assert dd["p_death"] > 0.0, "a hero at Death's Door faces no deathblow"

    for h in live[1:]:
        v = f[h.id]
        assert v["p_death"] == 0.0, (
            "a healthy hero was forecast to DIE; entering Death's Door is not "
            "dying, and the same-round second-hit path is deliberately unmodelled")
        assert v["p_deaths_door"] > 0.0, "a hero at 1 HP is in no danger?"


def test_blinding_actually_zeroes_them_and_leaves_the_rest_alone():
    e = _env()
    idx = forecast_indices(e.observation_space.shape[0])
    obs = e._get_obs().copy()
    blinded = obs.copy()
    blinded[idx] = 0.0
    assert np.all(blinded[idx] == 0.0)
    mask = np.ones(len(obs), dtype=bool)
    mask[idx] = False
    assert np.array_equal(obs[mask], blinded[mask])




def test_blind_forecast_zeroes_exactly_the_ablated_block_and_nothing_else():
    """The CONTROL ARM. `--blind-forecast` must differ from a normal env in
    exactly the indices `tools/ablate_forecast.py` zeroes -- otherwise the two
    training arms differ in something other than the thing under test, and the
    attribution it exists to produce is worthless.

    Zeroed at the SOURCE, not by post-hoc masking, so the observation width and
    the action space are untouched.
    """
    a = ExpeditionSequenceEnv(sampler_seed=0, blind_forecast=False)
    b = ExpeditionSequenceEnv(sampler_seed=0, blind_forecast=True)
    oa, _ = a.reset(seed=1, options={"sampler_seed": 1})
    ob, _ = b.reset(seed=1, options={"sampler_seed": 1})
    assert oa.shape == ob.shape

    idx = forecast_indices(oa.shape[0])
    assert np.all(ob[idx] == 0.0), "the blinded arm still emits a forecast"
    assert np.abs(oa[idx]).sum() > 0.0, "the sighted arm emitted nothing to blind"

    mask = np.ones(len(oa), dtype=bool)
    mask[idx] = False
    assert np.array_equal(oa[mask], ob[mask]), (
        "the arms differ outside the forecast block")


def test_blind_forecast_defaults_off():
    """Every existing construction must be byte-identical."""
    e = ExpeditionSequenceEnv(sampler_seed=0)
    assert e._blind_forecast is False


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  PASS {name}")
