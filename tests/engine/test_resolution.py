"""Test basic attack resolution: accuracy, damage, crit, dodge."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from engine.rng import SeededRNG
from engine.combatant import CombatantState
from engine.resolution import (
    resolve_attack, compute_hit_chance, resolve_to_hit, check_crit,
    compute_excess_crit_bonus,
)
from data.types import CombatRules, SkillLevelData


def make_rules() -> CombatRules:
    return CombatRules(
        cutoff_always_miss=0.0,
        cutoff_always_hit=0.95,
        player_crit_multiplier=1.5,
        monster_crit_multiplier=1.5,
        excess_crit_max=0.20,
    )


def make_attacker(**kwargs) -> CombatantState:
    defaults = dict(
        id=0, definition_id="crusader", is_hero=True, name="Crusader",
        position=1, hp=50, max_hp=50, dodge=25, prot=0.0,
        speed=3, crit=0.07, atk_bonus=0.0, dmg_min=10, dmg_max=19,
        stun_resist=0.4, blight_resist=0.3, bleed_resist=0.3,
        debuff_resist=0.3, move_resist=0.4, death_blow_resist=0.67,
    )
    defaults.update(kwargs)
    return CombatantState(**defaults)


def make_defender(**kwargs) -> CombatantState:
    defaults = dict(
        id=1, definition_id="cultist_brawler_A", is_hero=False, name="Brawler",
        position=0, hp=15, max_hp=15, dodge=0, prot=0.0,
        speed=5, crit=0.12, atk_bonus=0.0, dmg_min=2, dmg_max=4,
        stun_resist=0.25, blight_resist=0.2, bleed_resist=0.2,
        debuff_resist=0.15, move_resist=0.25, death_blow_resist=0.0,
    )
    defaults.update(kwargs)
    return CombatantState(**defaults)


def make_skill(**kwargs) -> SkillLevelData:
    defaults = dict(
        level=4, atk=1.05, dmg_mod=0.0, crit=0.04,
        launch=[3, 2], target=[1, 2], is_aoe=False,
        is_crit_valid=True, is_continue_turn=False,
        effects=[], self_effects=[], heal_min=None, heal_max=None,
        move_self=0, move_target=0, push=0, pull=0,
        valid_modes=["human"], per_battle_limit=None,
        per_turn_limit=None, is_stall_invalidating=True,
    )
    defaults.update(kwargs)
    return SkillLevelData(**defaults)


# ── the to-hit rule (rewritten 2026-08-19, DECOMP.md F4) ──
#
# These four tests used to pin the opposite rule: a hidden +5 ACC folded into
# the chance, and the chance clamped to [0.00, 0.95]. The binary says the 5%
# floor is a ROLL cutoff and the chance is never clamped, and the captured
# corpus agrees three ways -- chances of 1.05 that a clamp forbids, 6/6 hits
# where `roll >= 0.95` beat the chance, 17/17 misses just below. The *claim*
# the old tests were defending is still true; it simply lives in
# `resolve_to_hit` rather than in `compute_hit_chance`.


def test_the_chance_is_bare_acc_minus_dodge():
    """No hidden bonus. 50% ACC vs 25 DODGE is 25%, not the 30% this asserted
    for most of the project's life."""
    rules = make_rules()
    chance = compute_hit_chance(make_attacker(), make_defender(dodge=0.25),
                                make_skill(atk=0.5), rules)
    assert abs(chance - 0.25) < 0.001, f"Got {chance}"


def test_the_chance_is_not_clamped_at_either_end():
    """The real game's chance genuinely exceeds 1.0 -- 1.05 is in
    `archive/traces/` -- and genuinely goes negative. Clamping it here is what
    made the sim miss 5% of attacks the game never misses."""
    rules = make_rules()
    high = compute_hit_chance(make_attacker(), make_defender(dodge=0.0),
                              make_skill(atk=1.2), rules)
    low = compute_hit_chance(make_attacker(), make_defender(dodge=2.0),
                             make_skill(atk=0.0), rules)
    assert abs(high - 1.2) < 0.001, f"Got {high}"
    assert low < 0.0, f"Got {low}"


def test_a_roll_at_or_above_the_always_hit_cutoff_lands_whatever_the_chance():
    """The 5% floor, and where it actually comes from. An attack with NO chance
    at all still lands on the top 5% of the roll range -- which is what the
    Houndmaster doc's "minimum 5% chance to hit regardless of DODGE" describes.

    Evidence: 6 captured attacks with `roll >= 0.95` and `roll > chance` hit,
    6 out of 6, each followed by a damage roll."""
    rules = make_rules()
    assert resolve_to_hit(0.95, -1.0, rules) is True
    assert resolve_to_hit(0.99, 0.0, rules) is True
    assert resolve_to_hit(0.9499, 0.0, rules) is False


def test_a_roll_below_the_chance_lands_and_one_above_it_does_not():
    """The ordinary case, which is most attacks. 17 captured misses with
    `roll > chance` and `roll < 0.95` missed, 17 out of 17."""
    rules = make_rules()
    assert resolve_to_hit(0.40, 0.75, rules) is True
    assert resolve_to_hit(0.80, 0.75, rules) is False


def test_a_roll_at_or_below_the_always_miss_cutoff_never_lands():
    """The other end. `cutoff_always_miss` is 0.00 in every shipped
    rules.json, so this is unreachable from a uniform (0,1) draw in practice --
    pinned because the branch exists in the game and a modded rules.json could
    reach it."""
    rules = make_rules()
    assert resolve_to_hit(0.0, 5.0, rules) is False


def test_the_ceiling_is_where_the_old_rule_was_wrong():
    """The whole measured cost, in one test. At a raw chance of 0.95 or more
    the game always hits; the old rule clamped to 0.95 and so missed one attack
    in twenty. 24 of 166 real samples sat in this region."""
    rules = make_rules()
    for roll in (0.951, 0.97, 0.999):
        assert resolve_to_hit(roll, 1.05, rules) is True
    # ...and every roll below the cutoff is under a chance of 0.95+ anyway,
    # so P(hit) is 1.0 across the whole range rather than 0.95.
    assert all(resolve_to_hit(r / 1000.0, 0.95, rules) for r in range(1, 950))


def test_attack_hits():
    """Attack with high ACC should hit with predictable damage."""
    rng = SeededRNG(42)
    att = make_attacker(dmg_min=10, dmg_max=10)  # fixed damage
    dfn = make_defender(hp=20, dodge=0, prot=0.0)
    skill = make_skill(atk=1.0, dmg_mod=0.0)
    rules = make_rules()

    # Reroll until we hit
    result = None
    for _ in range(20):
        rng = SeededRNG(42 + _)
        result = resolve_attack(att, dfn, make_skill(atk=1.0, dmg_mod=0.0), rules, rng)
        if result.hit:
            break

    assert result is not None and result.hit, "Should hit with 95% chance"
    assert result.applied_damage >= 10, f"Damage >= dmg_min: {result.applied_damage}"
    print(f"  PASS attack_hits (dmg={result.applied_damage}, crit={result.is_crit})")


def test_attack_misses():
    """Attack with very low ACC should miss."""
    rng = SeededRNG(1)
    att = make_attacker(atk_bonus=0.0)
    dfn = make_defender(dodge=10.0)
    skill = make_skill(atk=0.0, dmg_mod=0.0)
    rules = make_rules()
    result = resolve_attack(att, dfn, skill, rules, rng)
    assert not result.hit, "Should miss with terrible ACC"
    print("  PASS attack_misses")


def test_crit_applies_multiplier():
    """Crit should apply 1.5x damage multiplier."""
    rng = SeededRNG(42)
    att = make_attacker(dmg_min=10, dmg_max=10, crit=0.99)  # guaranteed crit
    dfn = make_defender(hp=100, dodge=0, prot=0.0)
    skill = make_skill(atk=1.0, dmg_mod=0.0, crit=0.99)
    rules = make_rules()
    result = resolve_attack(att, dfn, skill, rules, rng)
    assert result.hit, "Should hit"
    assert result.is_crit, f"Should crit (crit={att.crit + skill.crit})"
    # 10 base * 1.0 dmg_mod * 1.5 crit = 15
    assert result.applied_damage in (14, 15, 16), f"Crit damage={result.applied_damage}"
    print(f"  PASS crit_applies_multiplier (dmg={result.applied_damage})")


def test_prot_reduces_damage():
    """50% PROT should halve incoming damage."""
    rng = SeededRNG(77)
    att = make_attacker(dmg_min=20, dmg_max=20, crit=0.0)
    dfn = make_defender(hp=100, dodge=0, prot=0.5)
    skill = make_skill(atk=1.0, dmg_mod=0.0, crit=0.0)
    rules = make_rules()
    result = resolve_attack(att, dfn, skill, rules, rng)
    assert result.hit
    # 20 * 0.5 = 10
    assert result.applied_damage == 10, f"Expected 10, got {result.applied_damage}"
    print("  PASS prot_reduces_damage")


def test_prot_is_capped_at_80_percent():
    """Wiki-confirmed (darkestdungeon.wiki.gg/wiki/Combat_Mechanics): PROT
    cannot reduce damage past 80%, no matter how high the stat is stacked.
    The stat itself can reach 0.95 (combat_engine.py's buff-application
    clamp), so a target sitting at 0.95 PROT must still take 20% of base
    damage, not 5%."""
    rng = SeededRNG(77)
    att = make_attacker(dmg_min=100, dmg_max=100, crit=0.0)
    dfn = make_defender(hp=1000, dodge=0, prot=0.95)
    skill = make_skill(atk=1.0, dmg_mod=0.0, crit=0.0)
    rules = make_rules()
    result = resolve_attack(att, dfn, skill, rules, rng)
    assert result.hit
    # 100 * (1 - 0.80) = 20, not 100 * (1 - 0.95) = 5
    assert result.applied_damage == 20, f"Expected 20, got {result.applied_damage}"
    assert result.prot_applied == 0.80, result.prot_applied
    print("  PASS prot_is_capped_at_80_percent")


def test_dmg_mod_negative_reduces_damage():
    """-60% DMG MOD should reduce damage."""
    rng = SeededRNG(99)
    att = make_attacker(dmg_min=20, dmg_max=20, crit=0.0)
    dfn = make_defender(hp=100, dodge=0, prot=0.0)
    skill = make_skill(atk=1.0, dmg_mod=-0.6, crit=0.0)
    rules = make_rules()
    result = resolve_attack(att, dfn, skill, rules, rng)
    assert result.hit
    # 20 * (1 + -0.6) = 15 * 0.4 = 8, round = 8
    assert result.applied_damage == 8, f"Expected 8, got {result.applied_damage}"
    print("  PASS dmg_mod_negative_reduces_damage")


def test_monster_crit():
    """Monster crits also use 1.5x multiplier."""
    rng = SeededRNG(42)
    att = make_attacker(is_hero=False, dmg_min=4, dmg_max=4, crit=0.99)
    dfn = make_defender(is_hero=True, hp=100, dodge=0, prot=0.0)
    skill = make_skill(atk=1.0, dmg_mod=0.0, crit=0.99)
    rules = make_rules()
    result = resolve_attack(att, dfn, skill, rules, rng)
    assert result.hit
    assert result.is_crit
    # monster_crit_multiplier = 1.5, so 4 * 1.5 = 6
    assert result.applied_damage == 6, f"Expected 6, got {result.applied_damage}"
    print("  PASS monster_crit")


def test_consecutive_miss_acc_bonus_raises_hit_chance():
    """rules.json's consecutive_miss_reduction_acc_buff (0.04) should raise
    the attacker's effective ACC by 4% per prior consecutive miss."""
    att = make_attacker()
    dfn = make_defender(dodge=0.30)
    skill = make_skill(atk=0.30)
    rules = make_rules()
    base = compute_hit_chance(att, dfn, skill, rules)
    with_bonus = compute_hit_chance(att, dfn, skill, rules,
                                      acc_bonus=3 * rules.consecutive_miss_reduction_acc_buff)
    assert abs((with_bonus - base) - 0.12) < 1e-9, (base, with_bonus)
    print(f"  PASS test_consecutive_miss_acc_bonus_raises_hit_chance ({base:.2f} -> {with_bonus:.2f})")


def test_excess_crit_bonus_is_zero_against_real_game_rules():
    """rules.json ships excessCritMult=0.0 in every mode (base, radiant,
    new_game_plus, bloodmoon) — confirmed via direct inspection of each
    file. So even with crit chance stacked above 100%, the excess-crit
    damage bonus must be exactly 0.0 against real game data."""
    att = make_attacker(crit=0.90)
    skill = make_skill(crit=0.50)  # combined crit_chance = 1.40, well above 100%
    rules = make_rules()  # excess_crit_mult defaults to 0.0, matching every shipped rules.json
    bonus = compute_excess_crit_bonus(att, skill, rules)
    assert bonus == 0.0, bonus
    print("  PASS test_excess_crit_bonus_is_zero_against_real_game_rules")


def test_excess_crit_bonus_formula_capped_by_excess_crit_max():
    """Hypothetical non-zero excessCritMult (not present in shipped data,
    but exercises the formula rules.json declares): excess crit chance
    beyond 100% converts to bonus damage, capped at excessCritMax."""
    att = make_attacker(crit=0.90)
    skill = make_skill(crit=0.50)  # crit_chance = 1.40 -> excess = 0.40
    rules = make_rules()
    rules.excess_crit_mult = 0.5  # excess * 0.5 = 0.20, right at the cap
    bonus = compute_excess_crit_bonus(att, skill, rules)
    assert abs(bonus - 0.20) < 1e-9, bonus

    rules.excess_crit_mult = 1.0  # excess * 1.0 = 0.40, must clip to excess_crit_max=0.20
    bonus_capped = compute_excess_crit_bonus(att, skill, rules)
    assert abs(bonus_capped - rules.excess_crit_max) < 1e-9, bonus_capped
    print("  PASS test_excess_crit_bonus_formula_capped_by_excess_crit_max")


if __name__ == "__main__":
    print("=== Attack Resolution Tests ===\n")
    for name, func in sorted(globals().items()):
        if name.startswith("test_"):
            try:
                func()
                print(f"  PASS {name}")
            except Exception as e:
                import traceback
                print(f"  FAIL {name}: {e}")
                traceback.print_exc()
    print("\nDone.")
