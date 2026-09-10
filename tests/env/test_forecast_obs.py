"""The threat forecast, as observation features.

The observation carried the board but never what the board was ABOUT to do.
"Kill it before it swings", "guard the Vestal, she is the one being aimed at",
and "this hero cannot survive the round" are all judgements about the next
round, and none was recoverable from the vector.

Every input is exact: the enemy's own decision distributions (graded 46/46
against the running game by `tools/brain_oracle.py`) through the hit rule
(155/155 by `tools/hit_oracle.py`). That is the whole answer to "can the model
have an edge a real player does not" -- a human estimates these; we compute
them.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from env.gym_env import (GLOBAL_FEATURES, HERO_HEAL_HEADROOM_INDEX,
                         HERO_SLOT_SCALARS, HERO_THREAT_FORECAST,
                         RETREAT_CHANCE_FROM_END)
from env.expedition_sequence_env import (EXPEDITION_CONTEXT_FEATURES,
                                         ExpeditionSequenceEnv)

# The four forecast floats are the last of the hero slot.
DMG_OVER_HP = -HERO_THREAT_FORECAST
DMG_OVER_MAXHP = -HERO_THREAT_FORECAST + 1
STRESS = -HERO_THREAT_FORECAST + 2
P_TARGETED = -HERO_THREAT_FORECAST + 3


def _env(seed=1):
    e = ExpeditionSequenceEnv(sampler_seed=0)
    e.reset(seed=seed, options={"sampler_seed": seed})
    return e


def test_the_block_is_appended_and_the_headroom_did_not_move():
    """Append-only. `HERO_HEAL_HEADROOM_INDEX` exists precisely because a test
    had pinned the headroom as `[-1]`, which this block silently moved."""
    e = _env()
    assert HERO_HEAL_HEADROOM_INDEX == -(HERO_THREAT_FORECAST + 1)
    for h in e.engine.heroes:
        vec = e._hero_slot_vec(h, False, e.engine.threat_forecast())
        assert len(vec) == HERO_SLOT_SCALARS
    assert e._hero_slot_vec(None, False, {}) == [0.0] * HERO_SLOT_SCALARS


def test_an_empty_rank_reads_all_zeros_even_with_a_forecast():
    e = _env()
    assert not any(e._hero_slot_vec(None, False, e.engine.threat_forecast()))


def test_a_hero_who_cannot_survive_the_round_reads_1_0():
    """Feature 0 is incoming / CURRENT hp, so it saturates exactly when the
    expected damage would finish the hero. This is the read the policy needs
    to guard, heal or kill first, and it had no way to make it."""
    e = _env()
    f = e.engine.threat_forecast()
    victim = max((h for h in e.engine.heroes if h.is_alive()),
                 key=lambda h: f.get(h.id, {}).get("damage", 0.0))
    assert f[victim.id]["damage"] > 0.0, "nothing on the board threatens anyone"
    victim.hp = 1
    vec = e._hero_slot_vec(victim, False, e.engine.threat_forecast())
    assert vec[DMG_OVER_HP] == 1.0

    victim.hp = victim.max_hp
    vec = e._hero_slot_vec(victim, False, e.engine.threat_forecast())
    assert vec[DMG_OVER_HP] < 1.0


def test_the_absolute_scale_is_not_redundant_with_the_fraction():
    """Two heroes taking the same expected damage at the same HP FRACTION but
    different max HP must differ. Same argument HERO_HEAL_HEADROOM rests on:
    a fraction alone cannot say how big the incoming hit actually is."""
    e = _env()
    f = e.engine.threat_forecast()
    h = max((x for x in e.engine.heroes if x.is_alive()),
            key=lambda x: f.get(x.id, {}).get("damage", 0.0))
    assert f[h.id]["damage"] > 0.0

    h.max_hp, h.hp = 40, 20
    small = e._hero_slot_vec(h, False, e.engine.threat_forecast())
    h.max_hp, h.hp = 100, 50
    big = e._hero_slot_vec(h, False, e.engine.threat_forecast())
    assert small[DMG_OVER_HP] > big[DMG_OVER_HP]
    assert small[DMG_OVER_MAXHP] > big[DMG_OVER_MAXHP]


def test_stunning_the_board_zeroes_every_forecast_feature():
    """A stunned enemy does not act, which is the entire reason stunning buys
    a stall turn. The policy could see the stun flag but not its consequence."""
    e = _env()
    for x in e.engine.enemies:
        x.is_stunned = True
    f = e.engine.threat_forecast()
    for h in e.engine.heroes:
        if not h.is_alive():
            continue
        vec = e._hero_slot_vec(h, False, f)
        assert vec[DMG_OVER_HP] == 0.0
        assert vec[STRESS] == 0.0
        assert vec[P_TARGETED] == 0.0


def test_the_SECURED_global_saturates_when_the_party_can_heal_through_it():
    """Fran's sense of "secured": not that a kill is available, but that what
    is still coming can be healed through."""
    e = _env()
    # NOT -3. `ExpeditionSequenceEnv` appends EXPEDITION_CONTEXT_FEATURES
    # after the base globals, so the tail of the full vector is expedition
    # context -- the same counting-from-the-end trap that has now moved the
    # retreat-chance offset three times.
    secured_idx = (e.observation_space.shape[0]
                   - EXPEDITION_CONTEXT_FEATURES - 3)
    for x in e.engine.enemies:
        x.is_stunned = True   # nothing incoming at all
    assert e._get_obs()[secured_idx] == 1.0

    # ...and it drops below 1.0 once the board out-damages the party's
    # healing, or the assertion above would pass on a constant. Forced rather
    # than hoped for: scale the live board's damage past the throughput.
    e2 = _env(seed=4)
    thr = e2._heal_throughput()
    for x in e2.engine.enemies:
        if x.is_alive():
            x.dmg_min = x.dmg_max = max(1, int(thr) + 50)
    for h in e2.engine.heroes:
        h.dodge = -5.0                      # so the forecast's hit term is ~1
    assert e2._get_obs()[secured_idx] < 1.0


def test_the_globals_grew_by_exactly_three_and_the_retreat_offset_moved_with_them():
    """The retreat-chance offset counts back from the tail and has been bumped
    three times by later appends. It is a named constant now so the next
    append is one edit, not a hunt."""
    assert GLOBAL_FEATURES == 17
    assert RETREAT_CHANCE_FROM_END == 11


def test_get_obs_computes_the_forecast_exactly_once():
    """It is the single most expensive thing in a step -- 124 us of 533,
    re-measured 2026-09-02 -- and there are four hero slots plus three globals
    reading it. Computing it per slot would be seven times that for no new
    information."""
    e = _env()
    calls = {"n": 0, "kw": []}
    real = e.engine.threat_forecast

    def counted(*a, **kw):
        calls["n"] += 1
        calls["kw"].append(kw)
        return real(*a, **kw)

    e.engine.threat_forecast = counted
    e._get_obs()
    assert calls["n"] == 1, f"_get_obs called threat_forecast {calls['n']} times"


def test_get_obs_does_NOT_pay_for_the_death_odds_it_never_reads():
    """`p_deaths_door`/`p_death` were appended to the observation on
    2026-08-25 and reverted the same day. Computing them survived the revert,
    which left `_forecast_kill_chance` and two survival products running on
    the hottest function of the hottest path to fill two keys nothing reads --
    10% of an env step, discarded, for over a week.

    The keys are OMITTED rather than zeroed so a caller that wants them and
    forgets to ask gets a KeyError instead of a plausible 0.0."""
    e = _env()
    seen = {}
    real = e.engine.threat_forecast

    def spy(*a, **kw):
        seen.update(kw)
        return real(*a, **kw)

    e.engine.threat_forecast = spy
    e._get_obs()
    assert seen.get("death_odds") is False, (
        "the observation must ask for the cheap forecast: it reads damage, "
        "stress and p_targeted and nothing else")

    cheap = real(death_odds=False)
    full = real()
    assert cheap and full
    for hid, slot in cheap.items():
        assert set(slot) == {"damage", "stress", "p_targeted"}, set(slot)
        # ...and the three shared keys are bit-identical either way.
        for key in ("damage", "stress", "p_targeted"):
            assert slot[key] == full[hid][key], (key, slot[key], full[hid][key])
    for slot in full.values():
        assert "p_death" in slot and "p_deaths_door" in slot


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  PASS {name}")
