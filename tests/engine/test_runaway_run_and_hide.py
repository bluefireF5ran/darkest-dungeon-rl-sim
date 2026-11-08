"""The Runaway's Run and Hide: stealth and a heal over time, both via buffs.

Fran, 2026-09-28: the combat policy never used Run and Hide -- "is
restoration properly modeled?". It was not: "RW Stealth" and "RW Run HoT N"
carry their payload as buffs of stat_type `stealth` and `hp_dot_heal`, and
the engine read neither, so the skill was only "move back 2".
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests._fixtures import get_store  # noqa: E402

from engine import SeededRNG, CombatEngine  # noqa: E402

store = get_store()


def _runaway():
    engine = CombatEngine(store, SeededRNG(1))
    engine.reset(hero_defs=[store.heroes["runaway"]],
                 enemy_defs=[store.enemies["cultist_brawler_A"]])
    return engine, engine.heroes[0]


def test_rw_stealth_stealths_the_runaway_for_the_effect_duration():
    engine, hero = _runaway()
    engine._resolve_effect(hero, hero, store.effects["RW Stealth"], None)
    assert hero.is_stealthed
    assert hero.stealth_rounds_remaining == store.effects["RW Stealth"].duration


def test_rw_run_hot_heals_over_time():
    engine, hero = _runaway()
    eff = store.effects["RW Run HoT 5"]
    engine._resolve_effect(hero, hero, eff, None)
    assert hero.heal_dots, "the heal-over-time buff was not applied"
    dot = hero.heal_dots[-1]
    assert dot.damage_per_tick == store.buffs["rw_run_hot_5"].amount
    assert dot.remaining_ticks == eff.duration


def _party_vs_two():
    engine = CombatEngine(store, SeededRNG(1))
    engine.reset(hero_defs=[store.heroes["crusader"]],
                 enemy_defs=[store.enemies["cultist_brawler_A"],
                             store.enemies["cultist_brawler_A"]])
    return engine, engine.heroes[0]


def test_a_stealthed_enemy_is_not_a_single_target_but_aoe_still_hits_it():
    """Fran, 2026-09-28: stealthed units cannot be targeted by single-target
    attacks; an AoE still hits them. The monster side had the rule for heroes;
    the hero side did not have it for enemies."""
    engine, hero = _party_vs_two()
    stealthy = engine.enemies[0]
    stealthy.is_stealthed = True
    stealthy.stealth_rounds_remaining = 2
    spots = set(stealthy.occupied_positions)
    single = [a for a in engine.get_valid_actions(hero.id)
              if not engine._is_ally_skill(a.skill_id)
              and a.target_id in spots
              and not engine.get_skill_data(a.skill_id, level=hero.skill_tier).is_aoe]
    assert not single, single
    # every enemy stealthed: the rule lifts
    engine.enemies[1].is_stealthed = True
    lifted = [a for a in engine.get_valid_actions(hero.id)
              if not engine._is_ally_skill(a.skill_id) and a.target_id in spots]
    assert lifted



if __name__ == "__main__":
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            _fn()
            print(f"ok {_name}")
