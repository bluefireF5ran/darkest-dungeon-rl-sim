"""Regression test for a real bug found while implementing on-crit passives:
_resolve_effect's .buff_ids loop only created a duration-tracked ActiveBuff
(so it can later expire via _tick_buff_durations) when the EFFECT record
itself had a .duration field. Several real effects (on_crit_prot/
on_crit_acc/on_crit_def/on_crit_speed) have no .duration on the effect line
even though the underlying BuffDefinition does (e.g. on_crit_prot's buff has
duration=2) — so the stat change applied permanently instead of expiring.
Fixed by falling back to the buff definition's own duration.

Also covers the poison_chance/bleed_chance stat_type additions
(on_crit_blightchance/on_crit_bleedchance), which had no case in
_apply_buff_to_stats/_reverse_buff_from_stats before this.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests._fixtures import get_store

from engine import SeededRNG, CombatEngine


from data.paths import GAME_DIR
from data.paths import CSV_DIR
store = get_store()


def _build_engine():
    crusader = store.heroes["crusader"]
    brawler = store.enemies["cultist_brawler_A"]
    engine = CombatEngine(store, SeededRNG(1))
    engine.reset(hero_defs=[crusader], enemy_defs=[brawler])
    return engine


def test_on_crit_prot_effect_has_no_own_duration_but_its_buff_does():
    """Confirms the real data shape that caused the bug — the effect
    record itself has no .duration, only the underlying buff does."""
    eff = store.effects["on_crit_prot"]
    assert eff.duration is None, "expected on_crit_prot effect to have no own duration (real data shape)"
    bd = store.buffs["on_crit_prot"]
    assert bd.duration == 2, bd.duration
    print("  PASS test_on_crit_prot_effect_has_no_own_duration_but_its_buff_does")


def test_buff_falls_back_to_buff_definition_duration_and_expires_on_schedule():
    engine = _build_engine()
    hero = engine.heroes[0]
    eff = store.effects["on_crit_prot"]

    prot_before = hero.prot
    engine._resolve_effect(hero, hero, eff, None)
    assert abs(hero.prot - (prot_before + 0.15)) < 0.001, hero.prot

    tracked = [b for b in hero.buffs if b.buff_id == "on_crit_prot"]
    assert len(tracked) == 1, "expected the buff to be tracked for reversal via the bd.duration fallback"
    assert tracked[0].remaining_rounds == 2, tracked[0].remaining_rounds

    engine._tick_buff_durations()
    assert any(b.buff_id == "on_crit_prot" for b in hero.buffs), "should still be active after 1 tick (duration 2)"
    assert abs(hero.prot - (prot_before + 0.15)) < 0.001, "still applied mid-duration"

    engine._tick_buff_durations()
    assert not any(b.buff_id == "on_crit_prot" for b in hero.buffs), "should have expired after 2 ticks"
    assert abs(hero.prot - prot_before) < 0.001, f"expected prot reversed back to {prot_before}, got {hero.prot}"
    print("  PASS test_buff_falls_back_to_buff_definition_duration_and_expires_on_schedule")


def test_poison_chance_and_bleed_chance_stat_types_apply_and_reverse():
    engine = _build_engine()
    hero = engine.heroes[0]
    assert hero.blight_chance_bonus == 0.0
    assert hero.bleed_chance_bonus == 0.0

    blight_eff = store.effects["on_crit_blightchance"]
    engine._resolve_effect(hero, hero, blight_eff, None)
    assert abs(hero.blight_chance_bonus - 0.2) < 0.001, hero.blight_chance_bonus

    bleed_eff = store.effects["on_crit_bleedchance"]
    engine._resolve_effect(hero, hero, bleed_eff, None)
    assert abs(hero.bleed_chance_bonus - 0.2) < 0.001, hero.bleed_chance_bonus

    for _ in range(2):
        engine._tick_buff_durations()
    assert hero.blight_chance_bonus == 0.0, hero.blight_chance_bonus
    assert hero.bleed_chance_bonus == 0.0, hero.bleed_chance_bonus
    print("  PASS test_poison_chance_and_bleed_chance_stat_types_apply_and_reverse")


def test_bleed_chance_bonus_reduces_effective_resist_floor():
    """A high bleed_chance_bonus should make a bleed effect land even
    against a target whose raw bleed_resist alone would have resisted it."""
    engine = _build_engine()
    performer = engine.heroes[0]
    target = engine.enemies[0]
    target.bleed_resist = 0.9  # would resist almost always unresisted
    performer.bleed_chance_bonus = 0.9  # cancels it out

    from data.types import EffectDefinition
    eff = EffectDefinition(
        name="test_bleed", target="target", chance=1.0, on_hit=True, on_miss=False,
        is_apply_once=False, is_queued=False, can_apply_on_death=False,
        bleed_amount=3, bleed_rounds=3,
    )
    landed = 0
    trials = 30
    for i in range(trials):
        engine.rng = SeededRNG(i)
        target.bleeds = []
        engine._resolve_effect(target, performer, eff, None)
        if target.bleeds:
            landed += 1
    assert landed == trials, f"expected the bonus to cancel resist entirely, landed {landed}/{trials}"
    print(f"  PASS test_bleed_chance_bonus_reduces_effective_resist_floor ({landed}/{trials})")


if __name__ == "__main__":
    print("=== Buff Duration Fallback / Chance-Bonus Tests ===\n")
    for name, func in sorted(globals().items()):
        if name.startswith("test_"):
            try:
                func()
            except Exception as e:
                import traceback
                print(f"  FAIL {name}: {e}")
                traceback.print_exc()
    print("\nDone.")
