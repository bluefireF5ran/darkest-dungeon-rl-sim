"""Tests for ExpeditionEnv (env/expedition_env.py) — randomizes (real hero
comp from COMPS/ CSV, zone-matched real enemy group from store.enemy_comps,
paired Resolve Level) every reset(), instead of DarkestDungeonEnv's fixed
2xCrusader-vs-2xCultist-Brawler default. Named after the game's own term
for embarking a chosen party into a chosen zone.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests._fixtures import comp_with_classes
from env import ExpeditionEnv
from env.expedition_env import TIER_TO_HERO_LEVEL, CSV_ZONE_TO_ZONE_ID


def test_hero_pool_built_from_real_comps():
    """132/145 COMPS/ CSV comps are usable (zone maps to a trainable zone,
    >=2 real heroes, no fan-made modded hero class in any real slot) —
    DD2/Courtyard-tagged, 1-hero, and modded-hero comps excluded. Hero
    count/identity comes from store.comp_loadouts (the per-comp JSON)
    whenever a loadout exists, not the CSV's own (alphabetical,
    duplicate-collapsing) `heroes` field — see CompsCsvLoader.
    load_modded_hero_comp_names and ExpeditionEnv._build_hero_pool's
    docstring for the full rationale."""
    env = ExpeditionEnv(sampler_seed=1)
    # A plain tripwire, and it is EXPECTED to move whenever the comp folder
    # changes -- bump it deliberately, never reflexively. The Fire's Edge
    # carve-out this used to subtract is gone with the CSV: those comps were
    # counted separately only because they could not own a CSV row without
    # shifting every archived --comp-seed, and there is no CSV now.
    # 141 -> 152 on 2026-08-23: Fran added 12 curated comps. Bumped
    # deliberately, which is what this tripwire asks for.
    # 152 -> 158 on 2026-08-26: the re-export replaced most of the folder
    # (100 added, 94 deleted). Bumped deliberately, same rule.
    # 158 -> 163 across 2026-08-26/27: Fran promoted five hand-upgrades of GA
    # output out of GA_comps/ into curated_comps/, which is what promotion IS
    # -- a `mv`. They now train and appear in every leaderboard. The first two
    # beat the originals they were built from by +5.3 and +17.5 paired; the
    # third did not (-4.2), so promotion is his judgement, not a score gate.
    # 169 -> 172 on 2026-08-30: the four hand-optimised proposals closing the
    # Warrens batch. Three land here; the fourth ('Grand Finale: Sibyl') fields
    # a modded class and needs DDSIM_MOD_DIRS. Measured on TWO checkpoints
    # each, three replicate (+11.0, +7.1, +3.1 mean) and the Sibyl one comes
    # back -2.9 / +5.0 -- opposite signs, so it is not distinguishable from its
    # original. Promotion stays his judgement, not a score gate.
    # 172 -> 175 on 2026-08-30: Fran reviewed the CLOSING COVE BATCH and
    # promoted three of it -- 'Grand Finale: Twin Warlock', 'Open Season:
    # Twin Cross' and 'Waiting Blade: Beast', all tagged The Cove. That is
    # the review loop closing rather than a search result: the batch was
    # emitted into GA_comps/ and only his judgement moved these three, which
    # is why promotion is still a `mv` and never a score gate. It also bites
    # into the pool's crypts skew, the one thing a Cove batch was for.
    # 175 -> 176 on 2026-09-02: Fran's own edit, net +1 across three files --
    # 'Ember Trail: Cross' and 'Torchless: Bulwark' added, 'Torchless: Lunge'
    # deleted. The Cross one arrived with only TWO skills on its Plague Doctor
    # and `test_every_restricted_hero_in_every_curated_comp_has_four_skills`
    # caught it before this tripwire did; he supplied the missing two. Both
    # nets firing on one file, one on its CONTENT and one on the COUNT, is the
    # pair working as designed.
    # 176 -> 446 on 2026-09-19 with the team-builder comp sync (178 -> 477
    # files). Deliberate; see test_comp_pool_exclusions for the same bump.
    assert len(env._hero_pool) == 446, len(env._hero_pool)
    # The tutorial party must never be sampled. It is pinned by ALIAS here:
    # the 2026-08-23 re-export renamed it to "Ragged Band: Heist" and kept
    # "The Old Road" only in the new `alias` field, so a pin on the current
    # name alone would silently leak it into training.
    names_and_aliases = {n for n, _z, _h in env._hero_pool}
    assert "The Old Road" not in names_and_aliases
    assert "Ragged Band: Heist" not in names_and_aliases
    names = {name for name, zone_id, hero_ids in env._hero_pool}
    assert "Holy Hymn: Monk" not in names
    for name, zone_id, hero_ids in env._hero_pool:
        assert zone_id in CSV_ZONE_TO_ZONE_ID.values()
        assert len(hero_ids) >= 2
        assert name not in env.store.modded_hero_comp_names
    print(f"  PASS test_hero_pool_built_from_real_comps ({len(env._hero_pool)} comps)")


def test_hero_pool_uses_real_json_order_and_count_when_available():
    """Real bugs found and fixed: store.comps' CSV `heroes` field is (1)
    alphabetically sorted (an artifact of that CSV's own generation), NOT
    the real front-to-back formation -- CombatEngine.reset() assigns
    position=index when hero_positions isn't given, so using CSV order
    directly silently scrambled every trained/evaluated comp's formation;
    and (2) DEDUPLICATES repeated hero classes to their unique names --
    user-reported: "Cross Pack: Blood" is really 3 different Crusaders + 1
    Flagellant (each Crusader with a distinct real loadout), but the CSV's
    `heroes` field has only 2 unique class names, silently evaluating it
    as a 2-hero party. store.comp_loadouts (the per-comp JSON) has the
    real order AND count, and is now used directly (not just as a
    same-multiset reordering) whenever a loadout exists for the comp.
    Verified directly against "Pigpen Cleanup" (real formation order,
    no duplicates) and "Cross Pack: Blood"/"Prophet's Last Sight V2.0"
    (real duplicate-class rosters, 4 heroes each, not 2 or 3)."""
    env = ExpeditionEnv(sampler_seed=1)
    pool_by_name = {name: hero_ids for name, zone_id, hero_ids in env._hero_pool}
    # Named comps drift -- the 2026-08-23 re-export renamed 66 of 138 -- so
    # the shapes are asserted by PROPERTY and one current example is pinned
    # per property as a readable anchor.
    #
    # (1) real formation, not alphabetical. Alphabetising this one would put
    # the Abomination in front of the Crusader.
    # BY SHAPE. This pinned "Blood Money: Ballad" as "a readable anchor"
    # and the 2026-09-19 re-export renamed it -- the third time a named
    # anchor here has drifted, which is the argument for not having one.
    _want = sorted(["crusader", "abomination", "jester", "bounty_hunter"])
    _match = [ids for ids in pool_by_name.values() if sorted(ids) == _want]
    assert _match, "no crusader/abomination/jester/bounty_hunter comp"
    assert any(ids != sorted(ids) for ids in _match), (
        "that comp is alphabetised -- the real formation was lost")
    assert any(ids != sorted(ids) for ids in pool_by_name.values()),         "no comp has a non-alphabetical formation -- the CSV order is back"

    # (2) duplicate-class rosters survive as 4 heroes, not collapsed to the
    # unique class names. "Cross Pack: Blood" is the old "Cross Pack: Blood":
    # three DIFFERENT Crusaders, each with its own loadout, plus a Flagellant.
    # BY SHAPE, like the twin-Arbalest check just below -- this file already
    # had the right pattern and this line was the leftover named one.
    triple_cross = comp_with_classes("crusader", "crusader", "crusader",
                                     "flagellant", store=env.store).name
    assert pool_by_name[triple_cross] == [
        "crusader", "crusader", "crusader", "flagellant"]
    # Resolved by SHAPE, not by name -- this comp was "Hex Battery: Volley"
    # until the 2026-08-26 re-export deleted the name. The property is that
    # the two Arbalests survive as two heroes, not one.
    twin_volley = comp_with_classes("leper", "occultist", "arbalest", "arbalest",
                                    store=env.store).name
    assert pool_by_name[twin_volley] == [
        "leper", "occultist", "arbalest", "arbalest"]
    dupes = [ids for ids in pool_by_name.values() if len(set(ids)) < len(ids)]
    assert len(dupes) >= 30, f"only {len(dupes)} duplicate-class rosters survived"

    # General check: every comp with a matching loadout must use the
    # JSON's order+count exactly, not just when it happens to agree with
    # the CSV's (deduplicated, alphabetical) version.
    checked_a_reordered_comp = False
    for name, hero_ids in pool_by_name.items():
        loadout = env.store.comp_loadouts.get(name)
        if not loadout:
            continue
        json_ids = [h.hero_id for h in loadout.heroes if h.hero_id in env.store.heroes]
        assert hero_ids == json_ids, (name, hero_ids, json_ids)
        if hero_ids != sorted(hero_ids):
            checked_a_reordered_comp = True
    assert checked_a_reordered_comp
    print("  PASS test_hero_pool_uses_real_json_order_and_count_when_available")


def test_reset_applies_real_trinkets_and_quirks_when_comp_has_one():
    """Sampled comps with a matching COMPS/curated_comps/*.json entry
    should have their real 2 trinkets + quirks per hero applied via
    CombatEngine.apply_trinkets_and_quirks -- previously resolved by
    CompLoadoutLoader but never actually wired into any reset() call.
    Verified by finding an auto-sampled reset that lands on a comp with a
    Hellion, then re-resetting the exact same scenario explicitly (hero_ids/
    enemy_ids/hero_levels, no comp_name -- bypasses trinket application,
    same contract as test_explicit_options_bypass_sampling) as a same-
    scenario baseline to diff against. 20/23 real Hellion comps grant
    "Thirsting Blade" (com_hel_thirsting_blade, +15% ACC unconditionally --
    checked here instead of its own +8% crit-vs-bleeding-target buff, which
    is real but conditional (rule_type="actorStatus", only active while the
    Hellion's *target* is currently bleeding -- correctly inactive at a
    fresh reset, before any attack; see CombatEngine._is_conditional_rule_
    active), so this should trigger well within 60 random seeds."""
    env = ExpeditionEnv(sampler_seed=1)
    found = False
    for seed in range(60):
        env.reset(seed=seed)
        ids = [h.definition_id for h in env.engine.heroes]
        if "hellion" not in ids:
            continue
        hellion_idx = ids.index("hellion")
        sampled_acc = env.engine.heroes[hellion_idx].atk_bonus
        enemy_ids = [e.definition_id for e in env.engine.enemies]
        hero_levels = [h.level for h in env.engine.heroes]

        env.reset(seed=seed, options={"hero_ids": ids, "enemy_ids": enemy_ids, "hero_levels": hero_levels})
        baseline_acc = env.engine.heroes[hellion_idx].atk_bonus

        if sampled_acc > baseline_acc:
            found = True
            break
    assert found, "no reset over 60 seeds sampled a comp granting a Hellion an ACC-boosting trinket"
    print(f"  PASS test_reset_applies_real_trinkets_and_quirks_when_comp_has_one "
          f"(seed={seed}, sampled_acc={sampled_acc} > baseline={baseline_acc})")


def test_enemy_pool_covers_all_trainable_zones():
    env = ExpeditionEnv(sampler_seed=1)
    for zone_id in CSV_ZONE_TO_ZONE_ID.values():
        assert zone_id in env._enemy_pool_by_zone
        assert len(env._enemy_pool_by_zone[zone_id]) > 0
    print("  PASS test_enemy_pool_covers_all_trainable_zones")


def test_reset_pairs_hero_level_to_enemy_tier_band():
    """Apprentice (tier 1-2) -> resolve 2, Veteran (tier 3-4) -> resolve 4,
    Champion (tier 5) -> resolve 6, per the user-specified pairing. Verified
    over many resets that every sampled scenario's hero levels match the
    band its sampled enemy tier belongs to."""
    env = ExpeditionEnv(sampler_seed=3)
    seen_levels = set()
    for i in range(60):
        env.reset(seed=i)
        levels = {h.level for h in env.engine.heroes}
        assert len(levels) == 1, f"expected uniform party level, got {levels}"
        level = levels.pop()
        assert level in (2, 4, 6), level
        seen_levels.add(level)
    assert seen_levels == {2, 4, 6}, f"expected to sample all 3 bands over 60 resets, got {seen_levels}"
    print(f"  PASS test_reset_pairs_hero_level_to_enemy_tier_band (levels seen: {seen_levels})")


def test_tier_to_hero_level_mapping():
    assert TIER_TO_HERO_LEVEL == {1: 2, 2: 2, 3: 4, 4: 4, 5: 6}
    print("  PASS test_tier_to_hero_level_mapping")


def test_explicit_options_bypass_sampling():
    """Passing hero_ids/enemy_ids explicitly should behave exactly like the
    base DarkestDungeonEnv — no randomization, no forced hero_levels."""
    env = ExpeditionEnv(sampler_seed=1)
    env.reset(seed=1, options={
        "hero_ids": ["crusader", "crusader"],
        "enemy_ids": ["cultist_brawler_A", "cultist_brawler_A"],
    })
    assert [h.definition_id for h in env.engine.heroes] == ["crusader", "crusader"]
    assert env.engine.heroes[0].skill_tier == 4  # default max tier, no hero_levels forced
    print("  PASS test_explicit_options_bypass_sampling")


def test_reset_applies_real_skill_loadout_when_comp_has_one():
    """Sampled comps with a matching COMPS/curated_comps/*.json entry
    (store.comp_loadouts) should restrict each hero to their real 4-skill
    loadout, not the full movepool — verified by finding a reset that lands
    on 'Dark Ritual: Heist' and checking its Highwayman never gets take_aim."""
    env = ExpeditionEnv(sampler_seed=7)
    found = False
    for seed in range(60):
        env.reset(seed=seed)
        ids = [h.definition_id for h in env.engine.heroes]
        if "highwayman" not in ids:
            continue
        hwm_engine_id = next(h.id for h in env.engine.heroes if h.definition_id == "highwayman")
        available = set(env.engine.get_hero_skill_ids(hwm_engine_id))
        if not available:
            continue
        found = True
        # Any real Highwayman loadout is a subset of his full 7-skill
        # movepool; the point of this test is that it's restricted at all.
        assert available.issubset({s.id for s in env.store.heroes["highwayman"].skills})
        if hwm_engine_id in env.engine._hero_skill_id_restrictions:
            assert len(available) <= 4
    assert found, "no reset over 60 seeds sampled a comp with a Highwayman"
    print("  PASS test_reset_applies_real_skill_loadout_when_comp_has_one")


def test_duplicate_class_comp_gives_each_hero_its_own_distinct_loadout():
    """Skills, trinkets and quirks used to be looked up in a dict keyed by
    hero_id, so a comp fielding two heroes of one class collapsed onto
    whichever was inserted last and applied the SAME (wrong) kit to both.
    Fixed by resolving POSITIONALLY.

    This used to name "Cross Pack: Blood" and assert its exact skill lists.
    The 2026-09-19 re-export renamed it and changed one Crusader's kit, so
    the test died on a comp that no longer existed while the behaviour it
    guards was fine. It now FINDS a comp that can still show the bug -- a
    duplicated class whose members carry genuinely different skills -- which
    is the condition it needs and is immune to the next rename.
    """
    import collections

    from tests._fixtures import get_store
    store = get_store()
    env = ExpeditionEnv(store=store, sampler_seed=1)
    for name in sorted(store.comp_loadouts):
        loadout = store.comp_loadouts[name]
        hero_ids = [h.hero_id for h in loadout.heroes if h.hero_id]
        for cls, n in collections.Counter(hero_ids).items():
            if n < 2:
                continue
            want = [tuple(h.skill_ids) for h in loadout.heroes if h.hero_id == cls]
            if len(set(want)) < 2:
                continue
            got = env._resolve_skill_ids(name, hero_ids)
            assert got is not None, name
            seen = {tuple(sk) for sk, hid in zip(got, hero_ids) if hid == cls}
            assert len(seen) == len(set(want)), (
                f"{name}: {n} {cls}s collapsed to {len(seen)} distinct kit(s)")
            trinkets = env._resolve_trinkets_and_quirks(name, hero_ids)
            assert trinkets is not None, name
            print(f"  PASS test_duplicate_class_comp_gives_each_hero_its_own_"
                  f"distinct_loadout ({name!r}, {cls} x{n})")
            return
    raise AssertionError(
        "no comp fields two heroes of one class with differing skills -- the "
        "positional-resolution bug can no longer be observed here")


def test_abomination_is_never_skill_restricted():
    """Real bug found and fixed: unlike every other hero, Abomination
    always has all 7 combat skills equipped in the real game (HEROES/
    Heroes' own doc: "Unlike other heroes who can only equip 4 combat
    skills, Abomination always has all 7 combat skills equipped") -- there
    is no 4-skill loadout choice for him at all. The scraped comp JSON
    still lists 4 "activeSkills" for him like any other hero (no
    Abomination carve-out in the source tool) -- confirmed directly on
    "Stun Control: Beast"'s 2nd Abomination, whose JSON list
    (transform/manacles/vomit/absolution) is missing ALL THREE of his
    beast-form attacks (rake/rage/slam), which would leave him unable to
    deal any damage at all while transformed if applied as a real
    restriction. _resolve_skill_ids must leave both Abominations'
    entries unrestricted ([]) while still restricting the Crusader and
    Plague Doctor normally."""
    env = ExpeditionEnv(sampler_seed=1)
    # Resolved by SHAPE: this roster was "Stun Control: Beast", then
    # "Iron Shackles: Beast", and is now "Iron Shackles: Twin Beast". Asserted by property rather than by the
    # literal skill lists, because which four skills a comp equips is Fran's
    # to change and is not what this test is about.
    hero_ids = ["crusader", "abomination", "abomination", "plague_doctor"]
    comp = comp_with_classes(*hero_ids, store=env.store).name
    skill_ids = env._resolve_skill_ids(comp, hero_ids)
    assert skill_ids is not None, "the comp did not resolve -- renamed again?"
    assert skill_ids[1] == [] and skill_ids[2] == [], skill_ids
    for slot in (0, 3):
        assert len(skill_ids[slot]) == 4, skill_ids
    print("  PASS test_abomination_is_never_skill_restricted")


def test_full_combat_resolves_with_expedition_scenario():
    env = ExpeditionEnv(sampler_seed=7)
    obs, _ = env.reset(seed=7)
    env.action_space.seed(7)
    # Seeded for the same reason as tests/env/test_reward.py::_make_env:
    # `reset(seed=)` does not reach the action space, so an unseeded
    # `sample()` replays a different episode on every run.
    steps = 0
    terminated = truncated = False
    while not (terminated or truncated) and steps < 300:
        steps += 1
        action = env.action_space.sample()
        obs, reward, terminated, truncated, info = env.step(action)
    assert terminated or truncated
    print(f"  PASS test_full_combat_resolves_with_expedition_scenario (steps={steps}, winner={info.get('winner')})")


if __name__ == "__main__":
    print("=== Expedition Env Tests ===\n")
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
