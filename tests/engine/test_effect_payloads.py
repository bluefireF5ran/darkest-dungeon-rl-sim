"""Five effect keys the shipped data carries and nothing read (2026-08-23).

Found by a mechanical audit rather than by play: every `.key` used by an
effect file, filtered to the effects this simulator actually FIELDS (named by
a hero skill or a loadable monster), then ranked by live carriers. That is the
same method the buff sweep used -- rank by what a real expedition equips, not
by how many definitions exist -- and it is the reason `torch_increase` came
out on top of a list that also contains 838 carriers of `.curio_result_type`.

The headline is torchlight. `CombatEngine.torch_level` was assigned once in
`reset()` and never moved again, while nine hero skills raise it, four
monsters drain it, and **104 of the 141 comps in the pool equip at least
one**. Everything DOWNSTREAM of the torch was already wired -- the 234
`lightbelow`/`lightabove` conditional buffs, the surprise draw, the loot
bands -- so a Crusader's +24 was resolving a whole party's buff set against a
number that could not change. This is a fix to the input of a subsystem that
was otherwise complete.

The other four are each one or two skills, but they are the DEFINING skill of
their class: Flagellant's Suffer without `.steal_buff_stat_type` is a
mark-swap that leaves the ally's bleed where it was, and Duelist's Coup de
Grace without `.bonus_action_next_turn` is a plain attack.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests._fixtures import get_store

from engine.combat_engine import CombatEngine
from engine.combatant import DotInstance
from engine.rng import SeededRNG


@pytest.fixture(scope="module")
def store():
    return get_store()


def _engine(store, hero_ids, foe_ids, torch=50.0, seed=11):
    e = CombatEngine(store, SeededRNG(seed))
    e.reset(hero_defs=[store.heroes[h] for h in hero_ids],
            enemy_defs=[store.enemies[f] for f in foe_ids], torch_level=torch)
    return e


# -- parsing ---------------------------------------------------------------

def test_torch_keys_parse_with_our_sign(store):
    """`.torch_increase` and `.torch_decrease` both ship as POSITIVE
    magnitudes under oppositely-signed key names, so the sign is ours."""
    assert store.effects["Crusader Bulwark Light"].torch_change == 24
    assert store.effects["Darkness 3"].torch_change == -25
    changers = [e for e in store.effects.values() if e.torch_change]
    assert len(changers) > 20, "the torch keys stopped parsing"
    assert any(e.torch_change > 0 for e in changers)
    assert any(e.torch_change < 0 for e in changers)


def test_valueless_flags_are_read_by_presence(store):
    """`.untag`, `.unstun`, `.bonus_action_next_turn` and
    `.requires_kill_target` are the same trap `.shuffleparty` was: the parser
    hands back `''` for a valueless flag and `bool('')` is False."""
    assert store.effects["FlareClear 1"].clears_mark
    assert store.effects["FlareClear 1"].clears_stun
    assert store.effects["Clear Marked Target"].clears_mark
    coup = store.effects["Duelist Kill Bonus Action"]
    assert coup.grants_bonus_action and coup.requires_kill_target


def test_steal_carries_the_dot_stat_type(store):
    assert store.effects["Flag Steal Bleed"].steal_dot_stat_type == "hp_dot_bleed"
    assert store.effects["Flag Steal Blight"].steal_dot_stat_type == "hp_dot_poison"


# -- torchlight ------------------------------------------------------------

def test_a_torch_skill_actually_moves_the_torch(store):
    e = _engine(store, ["crusader", "vestal"], ["cultist_brawler_A"], torch=40.0)
    cru = next(h for h in e.heroes if h.definition_id == "crusader")
    # `turn_order` holds CombatantState OBJECTS, not ids -- putting an id in
    # it makes _advance_to_next_hero_turn raise on `.is_dead`.
    e.turn_order = [cru] + [c for c in e.turn_order if c is not cru]
    e.current_turn_index = 0
    act = next(a for a in e.get_valid_actions(cru.id)
               if a.skill_id == "bulwark_of_faith")
    e.submit_action(act)
    e.step()
    assert e.torch_level == 64.0


@pytest.mark.parametrize("start,effect,expect", [
    (90.0, "Crusader Bulwark Light", 100.0),   # +24 clamps at the band ceiling
    (10.0, "Darkness 3", 0.0),                 # -25 clamps at pitch black
])
def test_the_torch_is_clamped_at_both_ends(store, start, effect, expect):
    e = _engine(store, ["crusader"], ["cultist_brawler_A"], torch=start)
    h = e.heroes[0]
    e._apply_effect_to_target(h, h, h, store.effects[effect], None)
    assert e.torch_level == expect


def test_a_global_effect_fires_once_per_action_not_once_per_target(store):
    """Arbalest's Flare is an AoE against the enemy side whose torch effect is
    `.target "global"`. Per-target it would pay +7 for every enemy hit. 23 of
    the 24 shipped global effects also carry `.apply_once`; the one that does
    not is why "global" is in the once list rather than relying on that flag.
    """
    flare = next(s for s in store.heroes["arbalest"].skills if s.id == "flare")
    lv = flare.levels[max(flare.levels)]
    e = _engine(store, ["arbalest", "crusader"], ["cultist_brawler_A"] * 3, torch=30.0)
    once, per_target = e._split_group_effect_names(lv)
    assert any(store.effects[n].torch_change for n in once)
    assert not any(store.effects[n].torch_change for n in per_target)

    arb = next(h for h in e.heroes if h.definition_id == "arbalest")
    # `turn_order` holds CombatantState OBJECTS, not ids -- putting an id in
    # it makes _advance_to_next_hero_turn raise on `.is_dead`.
    e.turn_order = [arb] + [c for c in e.turn_order if c is not arb]
    e.current_turn_index = 0
    act = next(a for a in e.get_valid_actions(arb.id) if a.skill_id == "flare")
    e.submit_action(act)
    e.step()
    assert e.torch_level == 37.0, "the torch was paid once per enemy hit"


# -- untag / unstun --------------------------------------------------------

def test_flare_clears_a_mark_and_a_stun_off_an_ally(store):
    e = _engine(store, ["arbalest", "crusader"], ["cultist_brawler_A"])
    ally = e.heroes[1]
    ally.is_stunned = True
    ally.is_marked = True
    ally.mark_rounds_remaining = 3
    e._apply_effect_to_target(ally, ally, e.heroes[0],
                              store.effects["FlareClear 1"], None)
    assert not ally.is_stunned
    assert not ally.is_marked and ally.mark_rounds_remaining is None


def test_a_forced_unstun_grants_the_recovery_buff(store):
    """DECLARED, not from a shipped key: no data says whether a FORCED unstun
    also grants the stun-resist buff that NATURAL recovery grants. Granted
    because `_apply_stun_recovery` is the only other stun-clearing path in the
    engine and a second convention is worse than one. See ASSUMPTIONS.md for
    the falsifier."""
    e = _engine(store, ["arbalest", "crusader"], ["cultist_brawler_A"])
    ally = e.heroes[1]
    ally.is_stunned = True
    e._apply_effect_to_target(ally, ally, e.heroes[0],
                              store.effects["FlareClear 1"], None)
    assert any(b.buff_id == e.rules.stun_recovery_buff_id for b in ally.buffs)


# -- the DoT steal ---------------------------------------------------------

def test_suffer_moves_the_dots_off_the_ally(store):
    e = _engine(store, ["flagellant", "crusader"], ["cultist_brawler_A"])
    flag, ally = e.heroes[0], e.heroes[1]
    ally.bleeds.append(DotInstance(effect_name="b", damage_per_tick=3, remaining_ticks=3))
    ally.blights.append(DotInstance(effect_name="p", damage_per_tick=2, remaining_ticks=2))
    for name in ("Flag Steal Bleed", "Flag Steal Blight"):
        e._apply_effect_to_target(ally, ally, flag, store.effects[name], None)
    assert not ally.bleeds and not ally.blights, "the DoT stayed on the ally"
    assert len(flag.bleeds) == 1 and len(flag.blights) == 1


def test_a_steal_onto_yourself_is_not_a_wipe(store):
    """The guard that matters: with eff_target IS performer, extending then
    clearing the same list would delete the DoT instead of moving it."""
    e = _engine(store, ["flagellant"], ["cultist_brawler_A"])
    flag = e.heroes[0]
    flag.bleeds.append(DotInstance(effect_name="b", damage_per_tick=3, remaining_ticks=3))
    e._apply_effect_to_target(flag, flag, flag, store.effects["Flag Steal Bleed"], None)
    assert len(flag.bleeds) == 1


# -- the kill-gated bonus action -------------------------------------------

def _coup(store, kill: bool):
    e = _engine(store, ["duelist", "crusader"],
                ["cultist_brawler_A", "cultist_brawler_A"])
    d = next(h for h in e.heroes if h.definition_id == "duelist")
    # She has no `human` mode and starts in her DEFENSIVE stance, where Coup de
    # Grace is not a legal action at all.
    d.mode = "stance_aggressive"
    # `turn_order` holds CombatantState OBJECTS, not ids -- putting an id in
    # it makes _advance_to_next_hero_turn raise on `.is_dead`.
    e.turn_order = [d] + [c for c in e.turn_order if c is not d]
    e.current_turn_index = 0
    act = next(a for a in e.get_valid_actions(d.id) if a.skill_id == "coup_de_grace")
    victim = next(x for x in e.enemies if act.target_id in x.occupied_positions)
    victim.hp = 1 if kill else victim.max_hp
    victim.dodge = -100.0
    before = e.current_turn_index
    e.submit_action(act)
    e.step()
    return victim, before, e.current_turn_index


def test_a_kill_grants_a_bonus_action(store):
    """`.requires_kill_target` + `.bonus_action_next_turn`. Implemented as the
    free-action path `.is_continue_turn` already uses -- the turn does not end
    -- rather than by inserting a second entry into the initiative queue, so
    `.initiative_change 1` on the same effect is subsumed."""
    victim, before, after = _coup(store, kill=True)
    assert victim.is_dead
    assert after == before, "the turn was consumed despite the kill"


def test_no_kill_means_no_bonus_action(store):
    """The control. Without it this file would pass on an engine that handed
    out a free action for every Coup de Grace."""
    victim, before, after = _coup(store, kill=False)
    assert not victim.is_dead
    # `> before`, not `== before + 1`: once the duelist's turn ends the engine
    # auto-resolves every enemy in the queue before handing back control, so
    # the index lands wherever the next living hero is. The invariant is that
    # the turn was CONSUMED.
    assert after > before, "a bonus action was granted with no kill"


def test_the_kill_gate_is_checked_after_the_chance_roll(store):
    """Deliberate: gating before the roll would shift the RNG stream of a
    skill that was already drawing, and Duelist's Coup de Grace already drew
    for this effect while the payload did nothing. No shipped effect pairs
    `requires_kill_target` with any other payload, so the position is not
    otherwise observable."""
    carriers = [e for e in store.effects.values() if e.requires_kill_target]
    assert len(carriers) == 1, "a second carrier appeared -- re-check placement"
    only = carriers[0]
    assert only.grants_bonus_action
    assert only.torch_change == 0 and not only.clears_mark and not only.clears_stun
    assert not only.steal_dot_stat_type


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
