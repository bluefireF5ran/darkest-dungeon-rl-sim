"""Camping — the between-combat recovery the simulator was missing.

Camping was deliberately out of scope until the eval put the whole remaining
gap in the long band: 37.2% SUCCESS against short's 80.7%, failing by wipes
(45.5%) at mean stress 117.5 versus an affliction threshold of 100. Firewood by
raid length is **0 short, 1 medium, 2 long** (user-confirmed against the real
game, consistent with provision.json's length-indexed starting inventory), so
the simulator's short band already matched the real game while its long band
was missing every recovery the real game grants.

Short granting zero firewood is load-bearing for reading the retrain: the short
band CANNOT move because of camping, so if it does, the cause is elsewhere.

See CHANGELOG.md.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests._fixtures import get_store

from data.camping_loader import CampingLoader, FIREWOOD_PER_BAND
from engine.combat_engine import CombatEngine
from engine.rng import SeededRNG
from engine.camping import resolve_camp

from data.paths import GAME_DIR as _GAME_DIR

GAME_DIR = str(_GAME_DIR)
from data.paths import CSV_DIR as _CSV_DIR

CSV_DIR = str(_CSV_DIR)

store = get_store()
COMP = "Blood Money: Volley"


def _party(hp_stress):
    """A live party from a real comp, beaten down to a given state."""
    eng = CombatEngine(store, SeededRNG(7))
    loadout = store.comp_loadouts[COMP]
    hero_ids = [h.hero_id for h in loadout.heroes]
    eng.reset(hero_defs=[store.heroes[h] for h in hero_ids],
              enemy_defs=[store.enemies["fishman_harpoon_B"]])
    for hero, (hp, stress) in zip(eng.heroes, hp_stress):
        hero.hp = min(hp, hero.max_hp)
        hero.stress = stress
    return eng, [list(h.camp_skill_ids) for h in loadout.heroes]


def _camp(eng, skills):
    return resolve_camp(eng.rng, store, eng.heroes, skills,
                        apply_buff=eng._apply_buff_to_stats)


def test_all_80_camping_skills_load_with_costs_and_effects():
    """64 base + 16 from the four DLC files. It read only the base file until
    2026-08-22, which left four classes unable to camp at all."""
    assert len(store.camping_skills) == 80
    assert all(s.cost > 0 for s in store.camping_skills.values())
    assert all(s.effects for s in store.camping_skills.values())
    first_aid = store.camping_skills["first_aid"]
    assert first_aid.cost == 2
    assert any(e.stat_type == "health_heal_max_health_percent" for e in first_aid.effects)


def test_every_loadable_class_can_camp():
    """The bug this catches: a DLC class is granted the SHARED camping skills
    by its own DLC re-declaring them, never by the base file, which was written
    before that class existed. Reading `raid/camping/default.camping_skills.json`
    alone left shieldbreaker, flagellant, duelist and runaway with nothing but
    the generic `hobby`."""
    for hero_id in store.heroes:
        pool = [s.id for s in store.camping_skills.values()
                if not s.hero_classes or hero_id in s.hero_classes]
        assert len(pool) > 1, f"{hero_id} has no camping skills beyond the generic one"


def test_hero_classes_is_unioned_across_dlc_but_costs_are_base_wins():
    """The two DLC files disagree about what re-declaring a skill means, and
    only a union satisfies both:

    * the Shieldbreaker DLC ships a second copy of all 64 base skills whose
      only change is `shieldbreaker` prepended to every `hero_classes`;
    * Fire's Edge re-declares `encourage` with `hero_classes: ["duelist"]`
      alone — sixteen classes short, so it cannot be a replacement either.

    Costs stay base-wins so a DLC copy cannot silently reprice a base skill.
    """
    encourage = store.camping_skills["encourage"]
    for hero_id in ("crusader", "shieldbreaker", "duelist"):
        assert hero_id in encourage.hero_classes
    assert len(encourage.hero_classes) == len(set(encourage.hero_classes))
    # `way_of_adder` is Shieldbreaker-only and `meditation` Fire's Edge-only:
    # a DLC-only skill has to arrive at all, not just widen an existing one.
    assert store.camping_skills["way_of_adder"].hero_classes == ["shieldbreaker"]
    assert "duelist" in store.camping_skills["meditation"].hero_classes
    # Base-wins on the numbers: the Shieldbreaker copy of first_aid is
    # identical, so the only observable of a wrong merge order would be a
    # DLC-only field leaking in. Pin the cost the base file states.
    assert store.camping_skills["first_aid"].cost == 2


def test_firewood_is_zero_on_short_one_medium_two_long():
    """User-confirmed against the real game. Short's zero is what makes the
    short band a control group when reading a retrain."""
    assert FIREWOOD_PER_BAND == {"short": 0, "medium": 1, "long": 2}
    assert CampingLoader.firewood_for_band("short") == 0


def test_camp_never_overspends_its_point_budget():
    eng, skills = _party([(10, 80), (25, 40), (8, 95), (30, 10)])
    result = _camp(eng, skills)
    assert result.points_spent <= int(store.rules.camp_start_camping_points)
    assert result.skills_used


def test_a_use_limit_is_respected_per_hero():
    eng, skills = _party([(1, 99), (1, 99), (1, 99), (1, 99)])
    result = _camp(eng, skills)
    for (hero_idx, skill_id) in set(result.skills_used):
        limit = store.camping_skills[skill_id].use_limit or 1
        used = sum(1 for u in result.skills_used if u == (hero_idx, skill_id))
        assert used <= limit


def test_a_battered_party_gets_healed_not_just_de_stressed():
    """The regression that motivated pool-relative, urgency-weighted scoring:
    flat point-for-point scoring spent the whole campfire on stress and left a
    Bounty Hunter at 8/45 HP with an unused 2-point first_aid in his own kit."""
    eng, skills = _party([(10, 80), (25, 40), (8, 95), (30, 10)])
    before = [h.hp for h in eng.heroes]
    _camp(eng, skills)
    assert any(h.hp > b for h, b in zip(eng.heroes, before)), "nobody was healed"


def test_a_stressed_party_spends_on_stress():
    eng, skills = _party([(99, 90), (99, 85), (99, 70), (99, 95)])
    before = sum(h.stress for h in eng.heroes)
    _camp(eng, skills)
    assert sum(h.stress for h in eng.heroes) < before


def test_healing_is_not_wasted_on_a_full_party():
    """Value is measured as recovery that actually LANDS, so a rested party
    cannot burn its budget topping up full heroes. It may still buy the same
    skills for their other effects — Zealous Vigil is worth taking purely for
    its ambush reduction — which is why this asserts on the state, not on
    which skills were chosen."""
    eng, skills = _party([(999, 0), (999, 0), (999, 0), (999, 0)])
    before = [(h.hp, h.stress) for h in eng.heroes]
    _camp(eng, skills)
    assert [(h.hp, h.stress) for h in eng.heroes] == before


def test_class_restricted_skills_never_fire_on_the_wrong_hero():
    """zealous_vigil is crusader-only; the comp's Crusader is at index 0."""
    eng, skills = _party([(10, 90), (10, 90), (10, 90), (10, 90)])
    result = _camp(eng, skills)
    for hero_idx, skill_id in result.skills_used:
        classes = store.camping_skills[skill_id].hero_classes
        if classes:
            assert eng.heroes[hero_idx].definition_id in classes


def test_camp_skills_resolve_positionally_for_duplicate_class_comps():
    """"Curious Coin: Sand" fields two Antiquarians whose camp skills genuinely
    differ. A hero_id-keyed dict collapses them onto whichever was inserted
    last — a bug this repo has already hit twice for skills and trinkets."""
    loadout = store.comp_loadouts["Curious Coin: Sand"]
    classes = [h.hero_id for h in loadout.heroes]
    assert classes.count("antiquarian") == 2
    kits = [tuple(h.camp_skill_ids) for h in loadout.heroes if h.hero_id == "antiquarian"]
    assert kits[0] != kits[1], "the two Antiquarians should have different camp skills"


def test_requirements_gate_effects():
    """Zealous Vigil heals 25 stress unconditionally and a further 15 only
    when the hero is afflicted."""
    vigil = store.camping_skills["zealous_vigil"]
    gated = [e for e in vigil.effects if "afflicted" in e.requirements]
    assert gated and gated[0].amount == 15

    eng, _ = _party([(99, 60), (99, 0), (99, 0), (99, 0)])
    crusader = eng.heroes[0]
    crusader.is_afflicted = False
    _camp(eng, [["zealous_vigil"], [], [], []])
    assert crusader.stress == 60 - 25, "unafflicted hero got the afflicted-only heal"


def test_an_unknown_requirement_blocks_rather_than_fires():
    from data.types import CampingSkillEffect
    from engine.camping import _requirements_met
    eng, _ = _party([(99, 99), (99, 0), (99, 0), (99, 0)])
    effect = CampingSkillEffect(target="self", amount=10, stat_type="stress_heal_amount",
                                stat_sub_type="", duration=None,
                                requirements=["something_this_sim_never_heard_of"])
    assert not _requirements_met(effect, eng.heroes[0], store)


def test_ambush_comes_from_the_seeded_rng_at_the_real_rate():
    """33% in every shipped rules.json. Without an ambush risk, camping is
    free healing and the correction overshoots in the other direction.

    Measured on a party carrying NO ambush-reduction skill — see the next test
    for why that qualifier matters."""
    assert abs(store.rules.ambush_camping_base_chance - 0.33) < 1e-9
    hits = 0
    for seed in range(300):
        eng, _ = _party([(999, 0), (999, 0), (999, 0), (999, 0)])
        eng.rng = SeededRNG(seed)
        hits += _camp(eng, [[], [], [], []]).ambush
    assert 0.25 < hits / 300 < 0.42, f"ambush rate {hits / 300:.2f} is not near 33%"


def test_reduce_ambush_chance_really_does_cancel_the_ambush():
    """Zealous Vigil carries reduce_ambush_chance 1.0, and in the real game it
    states outright that it drops ambush chance to 0%. Any comp fielding a
    Crusader who brings it is simply never ambushed at camp — that is faithful,
    not a bug, and it is why the rate test above has to strip camp skills."""
    vigil = store.camping_skills["zealous_vigil"]
    assert any(e.stat_type == "reduce_ambush_chance" and e.amount >= 1.0 for e in vigil.effects)
    for seed in range(50):
        eng, _ = _party([(999, 0), (999, 0), (999, 0), (999, 0)])
        eng.rng = SeededRNG(seed)
        assert not _camp(eng, [["zealous_vigil"], [], [], []]).ambush


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  ok  {name}")
