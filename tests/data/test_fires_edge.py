"""The Fire's Edge classes: the Runaway and the stance-based Duelist.

Two things are pinned here, and the second matters more than the first.

**The Duelist is a stance hero**, and the engine had exactly one of those --
the Abomination -- with `human`/`beast` written into the source at two sites.
That was never a two-mode rule, it was the Abomination's instance of a general
one: `.<mode>_effects`, one list per `.valid_modes` entry. The Duelist uses the
same convention with `.stance_defensive_effects` /
`.stance_aggressive_effects`, and under the old lookup every stance change she
made fell down the `beast` branch and found nothing.

So `test_the_abomination_still_transforms` is the important test in this file.
Generalising a special case is only safe if the special case still works.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests._fixtures import get_store

from engine.combat_engine import CombatEngine
from engine.rng import SeededRNG
from env.expedition_env import UNRESTRICTED_HEROES


@pytest.fixture(scope="module")
def store():
    return get_store()


def _combat(store, hero_ids):
    engine = CombatEngine(store, SeededRNG(7))
    enemy = next(k for k in store.enemies if "cultist_brawler" in k)
    # Neither side is surprised: these tests pin the Duelist as the FIRST
    # actor and read her stance-gated skill list off the opening turn, and a
    # surprised party both acts last and gets its ranks scrambled.
    engine.reset(hero_defs=[store.heroes[h] for h in hero_ids],
                 enemy_defs=[store.enemies[enemy], store.enemies[enemy]],
                 surprise=(False, False))
    # ...and pin her EXPLICITLY rather than trusting the seed to. The comment
    # above has always said these tests need her first; it was true only by
    # accident of the initiative roll, and the 2026-08-23 mode battle-start
    # effects consume two `.chance` draws before initiative is computed, which
    # silently reordered it. A fixture that states an invariant should enforce
    # it.
    first = engine.heroes[0]
    engine.turn_order = [first] + [c for c in engine.turn_order if c is not first]
    engine.current_turn_index = 0
    return engine


def _skill_ids(engine, hero):
    return sorted({a.skill_id for a in engine.get_valid_actions(hero.id)
                   if a.skill_id and not a.skill_id.startswith("__")})


def test_both_classes_load_with_their_real_movepools(store):
    """They live one level deeper than the older DLC heroes
    (features/<name>/heroes/<name>/), which the recursive glob already reached
    -- so this is really checking the two HERO_DIR_MAP entries, nothing more."""
    for hero_id, expected in (
            ("duelist", {"anticipation", "touche", "feint", "disengage",
                         "fleche", "coup_de_grace", "the_boot"}),
            ("runaway", {"searing_strike", "firefly", "run_and_hide", "ransack",
                         "hearthlight", "controlled_burn", "backdraft"})):
        hero = store.heroes.get(hero_id)
        assert hero is not None, f"{hero_id} did not load"
        assert {s.id for s in hero.skills} == expected, hero_id


def test_the_duelist_starts_in_her_defensive_stance(store):
    """She has no `human` mode at all, and `CombatantState.mode` defaults to
    `"human"` -- so a hero built without consulting the definition would have
    ZERO legal skills, every one of hers being stance-gated. `reset` takes
    `hd.modes[0]`, and the raid default in her data is the defensive stance."""
    engine = _combat(store, ["duelist", "crusader", "vestal", "highwayman"])
    assert engine.heroes[0].mode == "stance_defensive"
    assert engine.heroes[1].mode == "human", "a normal hero must be unaffected"


def test_each_stance_offers_its_own_four(store):
    """Fran's confirmed loadouts: defensive is Anticipation/Touche/Feint/
    Disengage, aggressive is Anticipation/Fleche/Coup de Grace/The Boot.

    Two absences here are correct rather than missing. Anticipation is
    `.per_turn_limit 1` AND `.is_continue_turn`, so having just spent it to
    change stance it is gone for the rest of the same turn. Fleche is
    `.launch 432` and she is in rank 1."""
    engine = _combat(store, ["duelist", "crusader", "vestal", "highwayman"])
    duelist = engine.heroes[0]
    assert engine.get_current_actor_id() == duelist.id

    assert _skill_ids(engine, duelist) == [
        "anticipation", "disengage", "feint", "touche"]

    flip = next(a for a in engine.get_valid_actions(duelist.id)
                if a.skill_id == "anticipation")
    engine.submit_action(flip)
    engine.step()

    assert duelist.mode == "stance_aggressive", "the stance did not change"
    assert _skill_ids(engine, duelist) == ["coup_de_grace", "the_boot"]


def test_the_stance_change_is_free(store):
    """`.is_continue_turn true` -- she changes stance and still acts. If this
    ever reads False she has effectively lost a turn every time she pivots."""
    engine = _combat(store, ["duelist", "crusader", "vestal", "highwayman"])
    duelist = engine.heroes[0]
    before = engine.get_current_actor_id()
    flip = next(a for a in engine.get_valid_actions(duelist.id)
                if a.skill_id == "anticipation")
    engine.submit_action(flip)
    engine.step()
    assert engine.get_current_actor_id() == before == duelist.id


def test_the_abomination_still_transforms(store):
    """The regression guard for generalising `human_effects`/`beast_effects`
    into `mode_effects`. He is the reason the special case existed."""
    engine = _combat(store, ["abomination", "crusader", "vestal", "highwayman"])
    abom = engine.heroes[0]
    assert abom.mode == "human"
    transform = next((a for a in engine.get_valid_actions(abom.id)
                      if a.skill_id == "transform"), None)
    assert transform is not None, "transform was not offered in human form"
    engine.submit_action(transform)
    engine.step()
    assert abom.mode == "beast", "transform no longer flips the mode"


def test_mode_effects_is_keyed_by_every_declared_mode(store):
    """The parse rule: one `.<mode>_effects` per `.valid_modes` entry. Gated on
    valid_modes rather than on any key ending in `_effects`, which would
    swallow `.self_effects` and invent a mode called "self"."""
    for hero_id, skill_id in (("duelist", "anticipation"),
                              ("abomination", "transform")):
        skill = next(s for s in store.heroes[hero_id].skills if s.id == skill_id)
        level = skill.levels[0]
        assert set(level.mode_effects) == set(level.valid_modes), hero_id
        assert "self" not in level.mode_effects
        for mode, effects in level.mode_effects.items():
            assert any(e.startswith("switch_mode_") for e in effects), (hero_id, mode)


def test_switching_out_of_a_mode_names_a_real_effect(store):
    """A stance change works by an effect with `.set_mode`. If the DLC effects
    file were not merged into the store these would resolve to None and the
    stance would silently never change -- which is a no-op, not a crash."""
    for effect_id, expected in (
            ("switch_mode_stance_aggressive_self", "stance_aggressive"),
            ("switch_mode_stance_defensive_self", "stance_defensive"),
            ("switch_mode_beast_self", "beast")):
        effect = store.effects.get(effect_id)
        assert effect is not None, f"{effect_id} is not in the store"
        assert effect.set_mode == expected


def test_the_duelist_is_never_restricted_to_four(store):
    """Same rule as the Abomination and for the same reason: all seven are
    always equipped, and a MODE decides which are usable. Cutting her to four
    would empty a whole stance."""
    assert "duelist" in UNRESTRICTED_HEROES
    assert "abomination" in UNRESTRICTED_HEROES
    assert "runaway" not in UNRESTRICTED_HEROES, "she is a normal 4-of-7 hero"


# ── comps (2026-08-20) ──

def test_a_fires_edge_comp_resolves_every_one_of_its_heroes(store):
    """Fran's "Iron Shackles: Burn" is the existing Stun Control comp with one
    slot swapped for a Runaway. Before `CompositionLoader.HERO_CLASS_MAP` and
    `HERO_DISPLAY_NAME_TO_ID` knew the two classes, that slot was dropped
    SILENTLY and the comp evaluated as a 3-hero party -- the exact failure
    `modded_hero_comp_names` exists to catch for fan-made classes, except
    these two really do load."""
    # BY SHAPE, and it RAISES rather than skipping. This looked the comp up
    # as "Iron Shackles: Burn" and skipped when it was missing, so the
    # 2026-09-19 re-export renamed it to "Creeping Rot: Mercy" and this
    # test simply stopped running -- green, silent, and checking nothing.
    # That is the failure `tests/suite/` exists for, so a missing shape is
    # now an error: a Crusader/Abomination/Runaway/Plague Doctor comp is
    # the premise, and if the corpus loses it the test must say so.
    from tests._fixtures import comp_with_classes
    loadout = comp_with_classes("crusader", "abomination", "runaway",
                                "plague_doctor", store=store)
    assert [h.hero_id for h in loadout.heroes] == [
        "crusader", "abomination", "runaway", "plague_doctor"]
    runaway = next(h for h in loadout.heroes if h.hero_id == "runaway")
    assert runaway.skill_ids == ["firefly", "run_and_hide", "hearthlight",
                                 "backdraft"]


def test_a_fires_edge_comp_reaches_training_like_any_other(store):
    """It used to be the point of this test that a Fire's Edge comp was
    UNREACHABLE from `pick_representative_comps`: those comps had no CSV row,
    precisely so adding them could not change which twelve comps an archived
    `--comp-seed` selects.

    The CSV was retired on 2026-08-23 and `pick_representative_comps` now
    delegates to `build_comp_pool`, the same single source `_build_hero_pool`
    uses -- so a Fire's Edge comp is drawn like any other, and the archived
    `--comp-seed` comparability it was protecting was already gone (the
    re-export resolved 1 of seed 1's 12 names). What is still worth pinning is
    that the eval and training see ONE population, not two.

    Resolved by SHAPE, not by name: the comp was "Stun Control: Burn", then
    "Iron Shackles: Burn", and the 2026-08-26 re-export renamed it again.
    """
    from env.expedition_env import ExpeditionEnv
    from eval_expedition import pick_representative_comps

    env = ExpeditionEnv(store=store, sampler_seed=0)
    pool = {name: heroes for name, _zone, heroes in env._hero_pool}
    fires_edge = {n for n, h in pool.items()
                  if "runaway" in h or "duelist" in h}
    assert fires_edge, "no Fire's Edge comp reaches training at all"

    for seed in (0, 1):
        picked = {c[0] for c in pick_representative_comps(store, 12, seed)}
        assert len(picked) == 12
        assert picked <= set(pool), (
            "the eval sampled a comp training cannot see -- two populations")


def test_the_tutorial_party_is_still_not_sampled(store):
    """The rule that lets Fire's Edge comps in is "fields a Fire's Edge
    class", not "has no CSV row" -- precisely so it cannot sweep up The Old
    Road, which must stay pinned rather than sampled, or Feral Vapors, which
    has been outside the pool since long before any of this."""
    from env.expedition_env import ExpeditionEnv
    env = ExpeditionEnv(store=store, sampler_seed=0)
    names = {name for name, _zone, _heroes in env._hero_pool}
    assert "The Old Road" not in names
    assert "Feral Vapors" not in names


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
