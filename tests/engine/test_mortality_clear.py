"""A mortality debuff was permanent for the whole expedition.

Found 2026-08-23, during the camping sweep -- the one in-scope data family the
engine audit had never covered.

`_apply_recovery_tier` gives a hero who survives Death's Door a sticky tier of
debuffs (`mortality`: -2 ACC, -5% DMG low/high, -1 SPD, -stress resist; or the
heavier `heart_attack` set). It is carried between combats in
`CarryOverHeroState`, and **every write to `mortality_tier` raises it** -- the
applier itself guards with `MORTALITY_TIER_RANK[new] <= [current]`. Nothing
anywhere lowered it.

The real game removes them at a campfire, and the data says so: `stand_tall`
and `dark_ritual` carry `remove_deaths_door_recovery_buffs`, 94 equipped slots
across the comp pool. `engine/camping.py` filtered that effect out on the
stated grounds that "the engine's own mortality tiers handle it". They handle
applying it; nothing handled removing it.

So one Death's Door visit cost a hero those stats for the remainder of the run,
and on a 14-16 fight expedition that is paid ten or more times over.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests._fixtures import get_store

from engine.camping import HEAL_EFFECTS, resolve_camp
from engine.combat_engine import CombatEngine
from engine.rng import SeededRNG

store = get_store()


def _party(n=4, hero="crusader", seed=0):
    eng = CombatEngine(store, SeededRNG(seed))
    eng.reset([store.heroes[hero]] * n, [next(iter(store.enemies.values()))])
    return eng


def test_the_effect_is_no_longer_filtered_out():
    assert "remove_deaths_door_recovery_buffs" in HEAL_EFFECTS
    print("  PASS test_the_effect_is_no_longer_filtered_out")


def test_the_tier_is_sticky_without_a_campfire():
    """The bug's premise. Confirm the engine really has no other way down, so
    this test fails loudly if someone adds one and forgets this path."""
    eng = _party()
    h = eng.heroes[0]
    eng._apply_recovery_tier(h)
    assert h.mortality_tier == "mortality"
    # A full heal does not clear it -- that is the deaths_door_buffs tier, a
    # different set entirely (`_reverse_deaths_door_buffs`).
    h.hp = h.max_hp
    assert h.mortality_tier == "mortality"
    print("  PASS test_the_tier_is_sticky_without_a_campfire")


def test_a_campfire_clears_the_tier_and_restores_the_stats_exactly():
    """Exactly, not approximately: `_reverse_buff_from_stats` is the inverse of
    the applier, so a clear must land back on the original numbers rather than
    drifting through a multiply/divide round trip."""
    eng = _party()
    h = eng.heroes[1]
    acc0, spd0, dmg0 = h.atk_bonus, h.speed, h.dmg_min
    eng._apply_recovery_tier(h)
    assert (h.atk_bonus, h.speed) != (acc0, spd0), "the debuff did not apply"

    resolve_camp(SeededRNG(1), store, eng.heroes, [["stand_tall"]] * 4,
                 apply_buff=eng._apply_buff_to_stats,
                 clear_mortality=eng.clear_mortality_tier)

    assert h.mortality_tier == "none"
    assert abs(h.atk_bonus - acc0) < 1e-9, (h.atk_bonus, acc0)
    assert h.speed == spd0
    assert h.dmg_min == dmg0
    print("  PASS test_a_campfire_clears_the_tier_and_restores_the_stats_exactly")


def test_clearing_a_hero_who_never_went_down_is_a_no_op():
    eng = _party()
    h = eng.heroes[0]
    before = (h.atk_bonus, h.speed, h.dmg_min)
    assert eng.clear_mortality_tier(h) is False
    assert (h.atk_bonus, h.speed, h.dmg_min) == before
    print("  PASS test_clearing_a_hero_who_never_went_down_is_a_no_op")


def test_the_heart_attack_tier_reverses_its_own_buff_list():
    """The two tiers have different buff lists, so a clear must read the tier
    it is undoing -- reversing the `mortality` list off a `heart_attack` hero
    would leave the stats wrong in both directions."""
    eng = _party()
    h = eng.heroes[2]
    acc0, spd0 = h.atk_bonus, h.speed
    h.next_recovery_is_heart_attack = True
    eng._apply_recovery_tier(h)
    assert h.mortality_tier == "heart_attack"
    assert eng.clear_mortality_tier(h) is True
    assert h.mortality_tier == "none"
    assert abs(h.atk_bonus - acc0) < 1e-9 and h.speed == spd0
    print("  PASS test_the_heart_attack_tier_reverses_its_own_buff_list")


def test_the_camp_targets_the_hero_who_actually_went_down():
    """`individual` targeting. Both carriers are individual-target, so aiming
    at the lowest-index living hero -- what the fall-through did -- would spend
    the skill on someone with no tier at all."""
    eng = _party()
    marked = eng.heroes[3]
    eng._apply_recovery_tier(marked)
    resolve_camp(SeededRNG(2), store, eng.heroes, [["stand_tall"]] * 4,
                 apply_buff=eng._apply_buff_to_stats,
                 clear_mortality=eng.clear_mortality_tier)
    assert marked.mortality_tier == "none", "the campfire cleared the wrong hero"
    print("  PASS test_the_camp_targets_the_hero_who_actually_went_down")


def test_a_party_with_no_marks_does_not_buy_the_skill():
    """It scores 0 on an unmarked party, so the greedy spend must prefer
    anything else -- otherwise a campfire burns points on nothing."""
    eng = _party()
    res = resolve_camp(SeededRNG(3), store, eng.heroes,
                       [["stand_tall", "encourage"]] * 4,
                       apply_buff=eng._apply_buff_to_stats,
                       clear_mortality=eng.clear_mortality_tier)
    picked = [s for _i, s in res.skills_used] if res.skills_used else []
    assert "stand_tall" not in picked, f"bought a useless clear: {picked}"
    print("  PASS test_a_party_with_no_marks_does_not_buy_the_skill")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
