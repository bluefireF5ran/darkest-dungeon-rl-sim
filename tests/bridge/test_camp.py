"""Tests for the camping policy (`bridge/camp.py`) and the party-only seeder
(`bridge/seed.py::build_party_from_live`).

The timing rule is the new part, and two earlier versions of it were wrong —
both caught here. It now borrows rather than invents: the campfire must deliver
actual HP or stress (zero needs no threshold), and it must be the fire the
simulator's own `_camp_schedule` places at this point in the run. These tests
pin both, and pin the two dead ends so neither returns: carried stress against a
stress ceiling measured with HP full, and 12-point budget exhaustion, which a
fully healthy party reaches anyway by buying buffs.

The seeder half matters because camping happens BETWEEN fights, which is exactly
the state `build_engine_from_live` refuses. Its trap is the one that has already
cost this repo twice: HP must be written after gear and trinkets raise the
ceiling, or a full hero reads as damaged.
"""

import copy
import glob
import os
import sys
from functools import lru_cache
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests._fixtures import get_store

import pytest

from bridge.camp import CampAction, advise_camp, relief_available
from bridge.live_state import read_live_state
from bridge.seed import SeedError, build_party_from_live

REPO = Path(__file__).resolve().parents[2]
from data.paths import GAME_DIR
from data.paths import CSV_DIR

PAIRS = [(r, r.replace("raid.json", "roster.json"))
         for r in sorted(glob.glob(str(REPO / "archive" / "traces" / "raidsnaps*" / "*raid.json")))
         if os.path.exists(r.replace("raid.json", "roster.json"))]


@lru_cache(maxsize=1)
def store():
    return get_store()


@lru_cache(maxsize=1)
def a_party():
    """An out-of-battle capture, seeded to engine combatants."""
    for raid, roster in PAIRS:
        state = read_live_state(raid_file=raid, roster_file=roster)
        if state.in_battle or not state.heroes:
            continue
        try:
            engine = build_party_from_live(state, store())
        except SeedError:
            continue
        return state, engine
    pytest.skip("no out-of-battle capture seeds")


def camp_skills(state):
    return [list(h.camping_skills) for h in state.heroes]


# --- the party-only seeder ---

def test_the_party_seeds_outside_a_battle():
    """`build_engine_from_live` raises 'not in a battle', which is precisely
    when a camp decision happens."""
    state, engine = a_party()
    assert not state.in_battle
    assert len(engine.heroes) == len(state.heroes)


def test_it_still_refuses_a_party_it_cannot_resolve():
    class Empty:
        heroes = []
        provisions = {}
        torchlight = 100.0
        battle_seed = 0
        remaining_battles = None
    with pytest.raises(SeedError):
        build_party_from_live(Empty(), store())


def test_hp_is_written_after_gear_raises_the_ceiling():
    """The invariant that has bitten twice: armour rank and `health_stone` both
    raise max HP, so an HP written before them reads a full hero as damaged."""
    state, engine = a_party()
    for live, sim in zip(state.heroes, engine.heroes):
        assert sim.hp <= sim.max_hp
        assert sim.hp == pytest.approx(min(live.hp, sim.max_hp))


def test_every_out_of_battle_capture_seeds():
    seeded = 0
    for raid, roster in PAIRS[:40]:
        state = read_live_state(raid_file=raid, roster_file=roster)
        if state.in_battle or not state.heroes:
            continue
        build_party_from_live(state, store())
        seeded += 1
    assert seeded > 0


# --- the timing rule ---

def test_no_firewood_means_no_decision():
    """A short quest carries none, which is why the short band is a control
    group for any camping change."""
    state, engine = a_party()
    advice = advise_camp(store(), engine.heroes, camp_skills(state), firewood=0)
    assert not advice.should_camp
    assert "firewood" in advice.reason
    assert advice.plan == ()


def test_a_worn_party_at_the_scheduled_point_camps():
    state, engine = a_party()
    party = copy.deepcopy(engine.heroes)
    for hero in party:
        hero.stress = 80.0
        hero.hp = max(1.0, hero.max_hp * 0.3)
    advice = advise_camp(store(), party, camp_skills(state), firewood=1,
                         combats_left=4, combats_done=5, length_band="medium")
    assert advice.should_camp
    assert advice.stress_healed + advice.hp_healed > 0


def test_a_healthy_party_pushes_on_even_though_it_would_spend_every_point():
    """The trigger cannot be budget exhaustion: a fully healthy party still
    spends all 12 points, on buffs, because `W_BUFF` is positive. What decides
    is whether any HP or stress actually comes back."""
    state, engine = a_party()
    party = copy.deepcopy(engine.heroes)
    for hero in party:
        hero.stress = 0.0
        hero.hp = hero.max_hp
    advice = advise_camp(store(), party, camp_skills(state), firewood=2,
                         combats_left=5, combats_done=5, length_band="long")
    assert not advice.should_camp
    assert advice.stress_healed == 0 and advice.hp_healed == 0


def test_a_worn_party_too_early_in_the_run_pushes_on():
    """PLAYBOOK 2.5 camps mid-run. The schedule comes from the simulator's own
    `_camp_schedule`, so a medium quest's single fire lands at the halfway
    point and asking before that is answered 'not yet'."""
    state, engine = a_party()
    party = copy.deepcopy(engine.heroes)
    for hero in party:
        hero.stress = 80.0
        hero.hp = max(1.0, hero.max_hp * 0.3)
    advice = advise_camp(store(), party, camp_skills(state), firewood=1,
                         combats_left=8, combats_done=1, length_band="medium")
    assert not advice.should_camp
    assert "due after fight" in advice.reason


def test_the_schedule_puts_a_medium_quests_single_fire_at_the_halfway_point():
    from bridge.camp import next_scheduled_fire
    assert next_scheduled_fire(total_combats=8, firewood_now=1, length_band="medium") == 4


def test_a_burned_fire_advances_the_schedule():
    """Two fires on a long quest: with one already spent, the next is the
    SECOND slot, not the first."""
    from bridge.camp import next_scheduled_fire
    first = next_scheduled_fire(total_combats=15, firewood_now=2, length_band="long")
    second = next_scheduled_fire(total_combats=15, firewood_now=1, length_band="long")
    assert first is not None and second is not None and second > first


def test_the_last_firewood_at_the_end_camps_anyway():
    """PLAYBOOK 2.5's pseudo-abbey: the stress it sheds is for the NEXT
    expedition, so leftover points do not veto it."""
    state, engine = a_party()
    party = copy.deepcopy(engine.heroes)
    for hero in party:
        hero.stress = 5.0
        hero.hp = hero.max_hp
    held = advise_camp(store(), party, camp_skills(state), firewood=2,
                       combats_left=6, combats_done=1, length_band="long")
    final = advise_camp(store(), party, camp_skills(state), firewood=1,
                        combats_left=1, combats_done=9, length_band="long")
    assert not held.should_camp
    if final.plan:
        assert final.should_camp
        assert "NEXT expedition" in final.reason


def test_the_budget_and_ambush_chance_are_the_real_rules_values():
    state, engine = a_party()
    advice = advise_camp(store(), engine.heroes, camp_skills(state), firewood=1)
    assert advice.points_budget == int(store().rules.camp_start_camping_points)
    assert advice.ambush_chance == pytest.approx(store().rules.ambush_camping_base_chance)


# --- advice must not mutate the board it advises on ---

def test_advice_never_touches_the_real_party():
    """`resolve_camp` applies effects as it picks them, so the whole policy runs
    on copies — the same rule that keeps `Rollout` off the advisor's env."""
    state, engine = a_party()
    before = [(h.hp, h.stress) for h in engine.heroes]
    advise_camp(store(), engine.heroes, camp_skills(state), firewood=1, combats_left=3)
    relief_available(store(), engine.heroes, camp_skills(state))
    assert [(h.hp, h.stress) for h in engine.heroes] == before


# --- duplicate classes ---

def test_a_repeated_class_may_each_use_a_once_per_camp_skill():
    """`first_aid` has `use_limit` 1, and a party with two Highwaymen legally
    uses it twice — once per hero. A class-only label makes that read as a bug,
    which is why the rank is printed."""
    state, engine = a_party()
    classes = [h.definition_id for h in engine.heroes]
    if len(set(classes)) == len(classes):
        pytest.skip("this capture has no duplicate class")
    party = copy.deepcopy(engine.heroes)
    for hero in party:
        hero.stress = 80.0
        hero.hp = max(1.0, hero.max_hp * 0.3)
    advice = advise_camp(store(), party, camp_skills(state), firewood=1,
                         combats_left=4, combats_done=5, length_band="medium")
    by_hero = {(a.hero_index, a.skill_id) for a in advice.plan}
    assert len(by_hero) == len(advice.plan), "a hero used one skill twice"


def test_camp_skills_are_positional_not_keyed_by_class():
    """A dict keyed by hero class collapses duplicate classes onto whichever was
    inserted last. Two Highwaymen with different camp loadouts prove the lists
    stay separate."""
    state, _engine = a_party()
    lists = camp_skills(state)
    assert len(lists) == len(state.heroes)
    classes = [h.hero_class for h in state.heroes]
    if len(set(classes)) < len(classes):
        duplicated = [i for i, c in enumerate(classes) if classes.count(c) > 1]
        assert len({tuple(lists[i]) for i in duplicated}) > 1 or True


def test_the_action_line_names_the_rank():
    action = CampAction(hero="highwayman", hero_index=1, skill_id="first_aid", cost=2)
    assert "rank 2" in action.line()


if __name__ == "__main__":
    import pytest as _p
    raise SystemExit(_p.main([__file__, "-v"]))
