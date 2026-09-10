"""Tests for Resolve Level (hero_levels) — Apprentice/Veteran/Champion party
setups per the user's convention: quest difficulty levels 1/3/5 are
Apprentice/Veteran/Champion, and a party fielded at the top of its bracket
(resolve 2/4/6) is equipped at matching weapon/armour/skill tier index
(level-1, clamped 0-4), i.e. level2->TierII(idx1), level4->TierIV(idx3),
level6->TierV/max(idx4, since only 5 gear tiers exist for a level-6 hero).
Also the wiki-verified **+10% per resolve level** resist bonus to
stun/blight/bleed/move/debuff -- `level`, not `level - 1`, on the save's 0-6
scale. Corrected 2026-09-02 on Fran's worked example: "a level 0 leper has 60%
but a level 6 leper has 60 base + 10*6% extra so 120%".

Resolve level and equipment tier are NOT tied 1:1 in the real game (verified
against campaign/roster/roster.variables.json and
campaign/quest/quest.restriction.json — neither ties them together, and the
real weapon-tier-unlock table only gives a non-linear *minimum* resolve
level per tier — 0/1/2/3/5 for tiers 1-5 per
darkestdungeon.wiki.gg/wiki/Weapons — not a canonical "hero of level N owns
tier N" mapping); this mapping is a deliberate simulator convention, not a
literal game rule.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest

from tests._fixtures import get_store

from engine import SeededRNG, CombatEngine
from env import DarkestDungeonEnv


from data.paths import GAME_DIR
from data.paths import CSV_DIR
store = get_store()


def _engine_with_level(level, hero_id="crusader"):
    engine = CombatEngine(store, SeededRNG(1))
    hero = store.heroes[hero_id]
    brawler = store.enemies["cultist_brawler_A"]
    engine.reset(hero_defs=[hero], enemy_defs=[brawler], hero_positions=[1],
                 hero_levels=[level] if level is not None else None)
    return engine


def test_default_no_hero_levels_matches_old_max_tier_behavior():
    """Regression guard: not passing hero_levels must be identical to the
    pre-existing behavior — max gear/skill tier, NO resistance bonus. This
    was broken once already (defaulting to an implicit level=6 also silently
    added +50% resist to every default-composition combat) — this test
    exists specifically to catch that class of regression again.
    """
    default_engine = _engine_with_level(None)
    champion_engine = _engine_with_level(6)
    d, c = default_engine.heroes[0], champion_engine.heroes[0]
    assert d.skill_tier == c.skill_tier == 4
    assert d.max_hp == c.max_hp
    assert abs(d.dodge - c.dodge) < 1e-9
    # Champion (explicit level 6) gets +60% resist bonus; default must NOT.
    # 0.5 -> 0.6 on 2026-09-02: the formula was a level short, not this test.
    base_stun_resist = store.heroes["crusader"].base_stats.stun_resist
    assert abs(d.stun_resist - base_stun_resist) < 1e-9, d.stun_resist
    assert abs(c.stun_resist - (base_stun_resist + 0.6)) < 1e-9, c.stun_resist
    print("  PASS test_default_no_hero_levels_matches_old_max_tier_behavior")


def test_tier_index_mapping():
    # From the game's own `prerequisite_resolve_level` gating, not the old
    # `level - 1` convention: rank 1 is free and the thresholds are
    # (1, 2, 3, 5), so resolve 2 buys rank 3 and resolve 4 stops at rank 4.
    # See engine/combat_engine.py::GEAR_RANK_RESOLVE_THRESHOLDS.
    cases = {1: 1, 2: 2, 3: 3, 4: 3, 5: 4, 6: 4}
    for level, expected_tier in cases.items():
        engine = _engine_with_level(level)
        assert engine.heroes[0].skill_tier == expected_tier, (level, engine.heroes[0].skill_tier)
    print("  PASS test_tier_index_mapping")


def test_stats_increase_monotonically_with_level():
    levels = [2, 4, 6]
    stats = []
    for lvl in levels:
        h = _engine_with_level(lvl).heroes[0]
        stats.append((h.max_hp, h.dodge, h.crit, h.atk_bonus))
    for i in range(len(stats) - 1):
        hp_a, dodge_a, crit_a, atk_a = stats[i]
        hp_b, dodge_b, crit_b, atk_b = stats[i + 1]
        assert hp_b > hp_a, stats
        assert dodge_b > dodge_a, stats
    print(f"  PASS test_stats_increase_monotonically_with_level ({stats})")


def test_resist_bonus_formula():
    """+10% per resolve level, on the SAVE's 0-6 scale.

    **`level`, not `level - 1`** -- corrected 2026-09-02. `base_stats`
    describes a resolve-0 hero, so subtracting one gave resolve 0 and resolve 1
    identical resistances and left every hero above 0 a level short.
    """
    base = store.heroes["crusader"].base_stats
    for level in (0, 1, 2, 3, 4, 5, 6):
        h = _engine_with_level(level).heroes[0]
        expected_bonus = max(0, level) * 0.10
        assert abs(h.stun_resist - (base.stun_resist + expected_bonus)) < 1e-9, (level, h.stun_resist)
        assert abs(h.bleed_resist - (base.bleed_resist + expected_bonus)) < 1e-9, (level, h.bleed_resist)
    print("  PASS test_resist_bonus_formula")


def test_resolve_zero_is_the_base_stat_and_resolve_one_is_not():
    """The exact pair the old formula collapsed. Worth its own test because
    a `level - 1` regression passes every monotonic check above."""
    base = store.heroes["crusader"].base_stats
    at_zero = _engine_with_level(0).heroes[0]
    at_one = _engine_with_level(1).heroes[0]
    assert at_zero.stun_resist == pytest.approx(base.stun_resist)
    assert at_one.stun_resist == pytest.approx(base.stun_resist + 0.10)


def test_the_lepers_sixty_percent_becomes_a_hundred_and_twenty_uncapped():
    """Fran's worked example, 2026-09-02, and both ends are shipped numbers.

    The Leper ships `.stun 60% .move 60%`, and at resolve 6 that is 120%.
    **Nothing clamps a resist**: it is subtracted in `roll < chance - resist`,
    where above 1.0 simply means the payload cannot land. A clamp at 1.0 would
    silently discard the top four resolve levels for a hero like this one.
    """
    base = store.heroes["leper"].base_stats
    assert base.stun_resist == pytest.approx(0.60)
    assert base.move_resist == pytest.approx(0.60)
    hero = _engine_with_level(6, hero_id="leper").heroes[0]
    assert hero.stun_resist == pytest.approx(1.20)
    assert hero.move_resist == pytest.approx(1.20)


def _underleveled_engine(level, quest_difficulty_level):
    # Jester (SPD 9) — deliberately not Crusader: he's fast enough that no
    # enemy ever acts before reset() returns control to the first hero
    # decision, which would otherwise let a real enemy debuff (e.g.
    # cultist_brawler_A's "Brawler Rend Debuff 1", +10% stress_dmg_received_
    # mult) contaminate the exact multiplier this test asserts on. Same fix
    # CHANGELOG.md documents for test_carry_over_reset.py/test_heart_attack.py.
    engine = CombatEngine(store, SeededRNG(1))
    jester = store.heroes["jester"]
    brawler = store.enemies["cultist_brawler_A"]
    # ...and `surprise=(False, False)` for the same reason, one mechanic
    # later: a surprised party acts LAST, which hands the brawler the opening
    # turn that being fast was supposed to deny it.
    engine.reset(hero_defs=[jester], enemy_defs=[brawler], hero_positions=[1],
                 hero_levels=[level], quest_difficulty_level=quest_difficulty_level,
                 surprise=(False, False))
    return engine.heroes[0]


def test_underleveled_penalty_defaults_to_zero():
    """quest_difficulty_level=None (default) must be identical to before this
    param existed -- zero stress, zero stress_dmg_received_mult change --
    same backward-compatible-default convention as hero_levels/hero_skill_ids.
    """
    hero = _underleveled_engine(level=1, quest_difficulty_level=None)
    assert hero.stress == 0
    assert hero.stress_dmg_received_mult == 1.0
    print("  PASS test_underleveled_penalty_defaults_to_zero")


def test_underleveled_penalty_scales_with_level_shortfall():
    """Wiki-confirmed (darkestdungeon.wiki.gg): "20 stress for every level of
    difference" and "+25% stress for every level of difference" to incoming
    stress damage, when a hero's resolve level falls short of the
    expedition's own quest difficulty level (Apprentice/Veteran/Champion =
    1/3/5 on the same scale). Real gap found: this mechanic was entirely
    unmodeled -- no code path charged any penalty for fielding an
    underleveled party."""
    # Level 1 hero fielded into a Champion (difficulty 5) quest: 4 levels short.
    hero = _underleveled_engine(level=1, quest_difficulty_level=5)
    assert hero.stress == 80, f"expected 20*4=80 starting stress, got {hero.stress}"
    assert abs(hero.stress_dmg_received_mult - 2.0) < 1e-9, \
        f"expected 1 + 0.25*4 = 2.0 stress dmg mult, got {hero.stress_dmg_received_mult}"
    print("  PASS test_underleveled_penalty_scales_with_level_shortfall")


def test_underleveled_penalty_never_applies_when_at_or_above_difficulty():
    """A hero fielded AT or ABOVE the quest's difficulty must take zero
    penalty -- max(0, shortfall) must not go negative for an overleveled
    party."""
    hero = _underleveled_engine(level=6, quest_difficulty_level=1)
    assert hero.stress == 0
    assert hero.stress_dmg_received_mult == 1.0
    print("  PASS test_underleveled_penalty_never_applies_when_at_or_above_difficulty")


def test_skill_data_scales_with_tier():
    """Smite's ACC should be lower at low tier than at max tier — confirms
    get_valid_actions/get_skill_data actually resolve the hero's own
    skill_tier instead of always grabbing level 4."""
    low = _engine_with_level(2)
    high = _engine_with_level(6)
    low_sd = low.get_skill_data("smite", level=low.heroes[0].skill_tier)
    high_sd = high.get_skill_data("smite", level=high.heroes[0].skill_tier)
    assert low_sd is not None and high_sd is not None
    assert low_sd.atk < high_sd.atk, (low_sd.atk, high_sd.atk)
    print(f"  PASS test_skill_data_scales_with_tier (low.atk={low_sd.atk}, high.atk={high_sd.atk})")


def test_get_valid_actions_uses_hero_own_tier():
    """End-to-end: get_valid_actions (what the Gym action space is built
    from) must resolve skills at the acting hero's own tier, not a
    hardcoded default."""
    engine = _engine_with_level(2)
    hero = engine.heroes[0]
    acts = engine.get_valid_actions(hero.id)
    assert acts, "expected at least one valid action for a level-2 Crusader"
    print(f"  PASS test_get_valid_actions_uses_hero_own_tier ({len(acts)} actions)")


def test_full_combat_resolves_at_each_level():
    for level in (2, 4, 6):
        engine = CombatEngine(store, SeededRNG(5))
        crusader = store.heroes["crusader"]
        brawler = store.enemies["cultist_brawler_A"]
        engine.reset(hero_defs=[crusader, crusader], enemy_defs=[brawler, brawler],
                     hero_levels=[level, level])
        steps = 0
        while not engine.done and steps < 300:
            steps += 1
            actor = engine.get_current_actor_id()
            if actor is not None:
                acts = engine.get_valid_actions(actor)
                if acts:
                    engine.submit_action(acts[0])
            engine.step()
        assert engine.done, f"level={level} combat did not finish in {steps} steps"
    print("  PASS test_full_combat_resolves_at_each_level")


def test_gym_env_exposes_hero_levels():
    env = DarkestDungeonEnv()
    obs, info = env.reset(seed=1, options={
        "hero_ids": ["crusader"],
        "enemy_ids": ["cultist_brawler_A"],
        "hero_levels": [2],
    })
    # resolve 2 buys gear rank 3 => tier index 2 (real upgrade gating)
    assert env.engine.heroes[0].skill_tier == 2
    print("  PASS test_gym_env_exposes_hero_levels")


if __name__ == "__main__":
    print("=== Hero Level (Resolve Level) Tests ===\n")
    for name, func in sorted(globals().items()):
        if name.startswith("test_"):
            try:
                func()
                print()
            except Exception as e:
                import traceback
                print(f"  FAIL {name}: {e}")
                traceback.print_exc()
                print()
    print("Done.")
