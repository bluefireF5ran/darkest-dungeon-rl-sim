"""Absolute HP headroom — the quantity "is this heal wasted" turns on.

51.9% of the shipped checkpoint's heals land on a target at a **median 94% HP**,
restoring 2.4 HP. That is not a reward bug, and the arithmetic says so: a
marginal heal of ~3.4 HP on a ~35 HP hero is worth `(3.4/35)/4 = +0.024` in Φ
against a `TURN_PENALTY_THREAT` of `-0.10`, so the reward prices it at about
**-0.076** — and the realised step reward for those heals measures **-0.0819**.
The reward is right. The policy does it anyway.

It does it anyway because the comparison was **not recoverable from the
observation**:

* the hero slot carried HP as a FRACTION (`hp / max_hp`);
* the skill slot carries a heal as an ABSOLUTE amount over 30.

To know whether a heal overheals you must compare an absolute heal against an
absolute deficit, and that needs the target's max HP, which appeared nowhere in
the vector. This is structurally the same gap `_is_doomed` was added to close —
that one also needed absolute max HP to answer "is this kill already secured?"

So the fix is a derived feature on the same `/30` scale as the heal, making the
comparison linear instead of requiring the policy to infer a hidden divisor.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests._fixtures import get_store

from env.gym_env import (DarkestDungeonEnv, HERO_HEAL_HEADROOM,
                         HERO_HEAL_HEADROOM_INDEX, HERO_SLOT_SCALARS,
                         HERO_STATE_SCALARS, HERO_THREAT_FORECAST,
                         SKILL_MECHANIC_FEATURES)

store = get_store()


def _env():
    e = DarkestDungeonEnv(store=store)
    e.reset(seed=0)
    return e


def test_the_width_is_derived():
    assert HERO_HEAL_HEADROOM == 1
    assert HERO_SLOT_SCALARS == (HERO_STATE_SCALARS + 3 * SKILL_MECHANIC_FEATURES
                                 + HERO_HEAL_HEADROOM + HERO_THREAT_FORECAST)
    e = _env()
    for h in e.engine.heroes:
        assert len(e._hero_slot_vec(h, False)) == HERO_SLOT_SCALARS
    assert len(e._hero_slot_vec(None, False)) == HERO_SLOT_SCALARS
    print("  PASS test_the_width_is_derived")


def test_it_is_appended_last_so_nothing_below_moved():
    """The project's standing discipline. The capability pools were appended in
    the same way the day before, and all three blocks must keep their indices.

    The heal headroom is no longer LAST -- the threat forecast was appended
    after it on 2026-08-24 -- which is exactly why it is now addressed through
    `HERO_HEAL_HEADROOM_INDEX` rather than `[-1]`."""
    e = _env()
    h = e.engine.heroes[0]
    vec = e._hero_slot_vec(h, False)
    caps = e._hero_capabilities(h)
    assert vec[HERO_STATE_SCALARS:HERO_STATE_SCALARS + len(caps)] == list(caps)
    assert len(vec) == (HERO_STATE_SCALARS + len(caps)
                        + HERO_HEAL_HEADROOM + HERO_THREAT_FORECAST)
    # The headroom sits between the pools and the forecast, and its named
    # index has to agree with that arithmetic.
    assert (len(vec) + HERO_HEAL_HEADROOM_INDEX
            == HERO_STATE_SCALARS + len(caps))
    print("  PASS test_it_is_appended_last_so_nothing_below_moved")


def test_full_HP_reads_zero_headroom():
    """The whole point: a heal aimed here restores nothing worth having, and
    the policy can now see that in one number rather than inferring it."""
    e = _env()
    h = e.engine.heroes[0]
    h.hp = h.max_hp
    h.is_deaths_door = False
    assert e._hero_slot_vec(h, False)[HERO_HEAL_HEADROOM_INDEX] == 0.0
    print("  PASS test_full_HP_reads_zero_headroom")


def test_headroom_grows_with_the_deficit_in_ABSOLUTE_hp():
    """Two heroes at the same HP FRACTION but different max HP must read
    differently — that difference is exactly what the old vector could not
    express, and exactly what decides whether a heal overheals."""
    e = _env()
    a, b = e.engine.heroes[0], e.engine.heroes[1]
    a.is_deaths_door = b.is_deaths_door = False
    a.max_hp, a.hp = 20, 10        # 50%, deficit 10
    b.max_hp, b.hp = 60, 30        # 50%, deficit 30
    ha = e._hero_slot_vec(a, False)[HERO_HEAL_HEADROOM_INDEX]
    hb = e._hero_slot_vec(b, False)[HERO_HEAL_HEADROOM_INDEX]
    assert abs(a.hp / a.max_hp - b.hp / b.max_hp) < 1e-9, "fractions must match"
    assert hb > ha, (
        f"same HP fraction, deficits 10 vs 30, headroom {ha} vs {hb} — the "
        "feature is not carrying absolute HP")
    print(f"  PASS test_headroom_grows_with_the_deficit_in_ABSOLUTE_hp "
          f"({ha:.3f} vs {hb:.3f})")


def test_deaths_door_always_reads_full_headroom():
    """`_is_wasted_heal` already makes this exception — a hero on Death's Door
    is never 'full', whatever the HP says, because healing them off the door is
    never wasted however few points it restores. The feature has to agree with
    the legality rule or it teaches the opposite of what the mask allows."""
    e = _env()
    h = e.engine.heroes[0]
    h.max_hp, h.hp = 40, 39        # one point down: almost no headroom
    h.is_deaths_door = False
    assert e._hero_slot_vec(h, False)[HERO_HEAL_HEADROOM_INDEX] < 0.1
    h.is_deaths_door = True
    assert e._hero_slot_vec(h, False)[HERO_HEAL_HEADROOM_INDEX] == 1.0
    print("  PASS test_deaths_door_always_reads_full_headroom")


def test_it_shares_the_heal_payload_scale():
    """Both are divided by 30, so heal amount and headroom are directly
    comparable. If one scale ever changes the other must follow, or the
    comparison silently stops being linear."""
    src = (Path(__file__).resolve().parents[2] / "env" / "gym_env.py").read_text(
        encoding="utf-8")
    assert "(h.max_hp - h.hp) / 30.0" in src
    assert "_clamp01(avg / 30.0)" in src, (
        "the skill heal payload no longer uses /30; the headroom feature must "
        "be rescaled to match or the two are not comparable")
    print("  PASS test_it_shares_the_heal_payload_scale")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
