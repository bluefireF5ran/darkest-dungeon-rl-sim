"""The Runaway's kit, which this engine did not implement (2026-08-27).

Fran, 2026-08-27: *"Runaway isn't a weak class at all, in fact its really really
strong in proper hands"*. The search had never once picked her, and the reason
was not that she is weak here -- it is that **two of her three signature skills
did nothing**:

- `backdraft` names no `.effect` at all. Its parsed effect list is literally
  `['']`, which read for months as an inert skill. Its whole payload is three
  keys nothing parsed: `.requires_burning`, `.dmg_per_burn_stack 0.25`,
  `.copy_burn_behind true`.
- `controlled_burn`'s `RW CB Rank Effect N` carries
  `.controlled_burn_amount/_duration`, a burn attached to a RANK rather than to
  a monster, which is machinery this engine had none of.

Both were recorded in `ASSUMPTIONS.md` as "deliberately unimplemented -- the
mod's own file says they need engine support". The data is fully specified and
the support is what was missing.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests._fixtures import get_store

from engine.combat_engine import Action, CombatEngine
from engine.rng import SeededRNG

store = get_store()
WRETCH = "swine_wretch_A"


def _engine(heroes=("runaway",), enemies=3, seed=1):
    eng = CombatEngine(store, SeededRNG(seed))
    eng.reset(hero_defs=[store.heroes[h] for h in heroes],
              enemy_defs=[store.enemies[WRETCH]] * enemies,
              auto_resolve_to_hero=False)
    return eng


def _backdraft_level():
    skill = next(s for s in store.heroes["runaway"].skills if s.id == "backdraft")
    return skill.levels[max(skill.levels)]


# --------------------------------------------------------------- the data ---

def test_backdrafts_whole_payload_is_three_keys_and_they_are_parsed():
    """The skill names no effect. If these three ever stop parsing it goes
    straight back to being an inert attack, silently."""
    lv = _backdraft_level()
    assert lv.requires_burning is True
    assert lv.dmg_per_burn_stack == 0.25
    assert lv.copy_burn_behind is True
    # And the base damage really is halved -- the scaling is what buys it back.
    assert lv.dmg_mod == -0.5


def test_no_other_shipped_skill_carries_them():
    """Every default is the pre-2026-08-27 behaviour for everything else, and
    this is what makes that claim checkable rather than asserted."""
    carriers = set()
    for hero in store.heroes.values():
        for skill in hero.skills:
            for lv in skill.levels.values():
                if lv.requires_burning or lv.dmg_per_burn_stack or lv.copy_burn_behind:
                    carriers.add(skill.id)
    assert carriers == {"backdraft"}, carriers


def test_the_rank_zone_effects_are_parsed():
    zones = {name: e for name, e in store.effects.items()
             if e.controlled_burn_amount}
    assert len(zones) == 5, sorted(zones)
    top = store.effects["RW CB Rank Effect 5"]
    assert top.controlled_burn_amount == 5
    assert top.controlled_burn_duration == 3


# ------------------------------------------------------------- requires_burning

def test_backdraft_is_illegal_against_a_target_that_is_not_burning():
    eng = _engine()
    assert not [a for a in eng.get_valid_actions(0) if a.skill_id == "backdraft"]


def test_backdraft_becomes_legal_the_moment_something_burns():
    eng = _engine()
    eng.enemies[0].burn_stacks = 3
    offered = [a for a in eng.get_valid_actions(0) if a.skill_id == "backdraft"]
    assert [a.target_id for a in offered] == [0]


def test_the_gate_is_applied_at_EXECUTION_too():
    """The corpse rule's precedent: an action offered because a target
    qualified, then re-resolved to nothing, silently burns the turn. A DoT can
    put the only burning target down between the two."""
    eng = _engine()
    eng.enemies[0].burn_stacks = 3
    action = next(a for a in eng.get_valid_actions(0) if a.skill_id == "backdraft")
    eng.enemies[0].burn_stacks = 0          # the DoT killed the fire meanwhile
    resolved = eng._resolve_action_targets(action, _backdraft_level(),
                                           is_ally_skill=False)
    assert resolved == [], resolved


# ------------------------------------------------------ dmg_per_burn_stack ---

def test_damage_scales_with_the_targets_burn_STACKS():
    """+25% per stack off a -50% base, so a target on 6 stacks takes 1.25x.

    Read off `burn_stacks`, the count, NOT `burn_total_outstanding`: the game's
    key is per stack and the two differ, since a stack of 7 is worth 11.
    """
    from engine.resolution import resolve_attack

    def damage(stacks):
        eng = _engine(seed=7)
        target = eng.enemies[0]
        target.burn_stacks = stacks
        target.max_hp = target.hp = 999          # never dies, never floors
        # 0.5, not 0.0: `resolve_to_hit` treats a roll at or under
        # `cutoffAlwaysMiss` as a FORCED MISS, so hit_roll=0.0 never lands.
        result = resolve_attack(eng.heroes[0], target, _backdraft_level(),
                                eng.rules, SeededRNG(3), hit_roll=0.5)
        assert result.hit, "the test's own roll missed"
        return result.applied_damage

    low, high = damage(1), damage(9)
    assert high > low
    # 1 + 0.25*9 = 3.25 against 1 + 0.25*1 = 1.25 -- a factor of 2.6, and the
    # bound is loose only because the damage roll and rounding sit in between.
    assert high >= low * 2.0, (low, high)


# --------------------------------------------------------- copy_burn_behind --

def test_the_fire_spreads_to_the_rank_BEHIND():
    """Fran's reading of what the skill buys: *"backdraft makes the burn damage
    from an enemy hit 2 enemies, essentially doubling burn damage"*."""
    eng = _engine(seed=4)
    eng.enemies[0].burn_stacks = 6
    before = eng.enemies[1].burn_stacks
    eng.submit_action(Action(0, "backdraft", 0))
    eng.step()
    assert eng.enemies[1].burn_stacks > before


def test_it_spreads_ADDITIVELY_onto_existing_burn():
    """Burn stacks additively, unlike a second bleed, which queues its own
    independent instance."""
    eng = _engine(seed=4)
    eng.enemies[0].burn_stacks = 4
    eng.enemies[1].burn_stacks = 2
    eng._copy_burn_behind(eng.enemies[0], _backdraft_level())
    assert eng.enemies[1].burn_stacks == 6


def test_a_target_with_nothing_behind_it_spreads_nothing():
    eng = _engine(enemies=1, seed=4)
    eng.enemies[0].burn_stacks = 5
    eng._copy_burn_behind(eng.enemies[0], _backdraft_level())
    assert eng.enemies[0].burn_stacks == 5


def test_a_skill_without_the_flag_spreads_nothing():
    eng = _engine(seed=4)
    eng.enemies[0].burn_stacks = 5
    plain = next(s for s in store.heroes["runaway"].skills if s.id == "searing_strike")
    eng._copy_burn_behind(eng.enemies[0], plain.levels[max(plain.levels)])
    assert eng.enemies[1].burn_stacks == 0


# ---------------------------------------------------------- the rank zones ---

def test_controlled_burn_paints_a_RANK():
    eng = _engine(heroes=("runaway", "crusader"), seed=2)
    eng.submit_action(Action(0, "controlled_burn", 1))
    eng.step()
    assert 1 in eng.burn_zones, eng.burn_zones
    amount, rounds_left = eng.burn_zones[1]
    assert amount > 0 and rounds_left > 0


def test_the_zone_burns_whoever_is_standing_there_AT_THE_TICK():
    """The entire mechanic, and why it pairs with pull and push: a monster
    dragged into the fire this round burns this round."""
    eng = _engine(seed=2)
    eng.paint_burn_zone(2, 5, 3)
    moved_in = eng.enemies[2]
    moved_in.position = 2
    eng.enemies[1].position = 3          # swap them, so rank 2 has a new tenant
    before = moved_in.burn_stacks
    eng._tick_burn_zones()
    assert moved_in.burn_stacks == before + 5


def test_an_empty_rank_burns_nobody_and_the_zone_still_ages():
    eng = _engine(enemies=1, seed=2)
    eng.paint_burn_zone(3, 5, 2)
    eng._tick_burn_zones()
    assert eng.burn_zones[3][1] == 1


def test_the_zone_expires_after_its_duration():
    eng = _engine(seed=2)
    eng.paint_burn_zone(0, 4, 2)
    eng._tick_burn_zones()
    assert 0 in eng.burn_zones
    eng._tick_burn_zones()
    assert eng.burn_zones == {}


def test_a_duration_of_one_still_burns_once():
    """Expiry happens AFTER the tick, so the shortest possible zone is not a
    zone that does nothing."""
    eng = _engine(seed=2)
    eng.paint_burn_zone(0, 4, 1)
    before = eng.enemies[0].burn_stacks
    eng._tick_burn_zones()
    assert eng.enemies[0].burn_stacks == before + 4
    assert eng.burn_zones == {}


def test_repainting_a_rank_REFRESHES_rather_than_stacking_a_second_zone():
    eng = _engine(seed=2)
    eng.paint_burn_zone(1, 3, 1)
    eng.paint_burn_zone(1, 5, 3)
    assert eng.burn_zones == {1: [5, 3]}


def test_zones_never_survive_into_the_next_combat():
    eng = _engine(seed=2)
    eng.paint_burn_zone(1, 5, 3)
    eng.reset(hero_defs=[store.heroes["runaway"]],
              enemy_defs=[store.enemies[WRETCH]],
              auto_resolve_to_hero=False)
    assert eng.burn_zones == {}


def test_a_dead_occupant_is_not_burned():
    eng = _engine(seed=2)
    eng.paint_burn_zone(0, 5, 3)
    eng.enemies[0].hp = 0
    eng.enemies[0].is_dead = True
    eng._tick_burn_zones()
    assert eng.enemies[0].burn_stacks == 0


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  PASS {name}")
