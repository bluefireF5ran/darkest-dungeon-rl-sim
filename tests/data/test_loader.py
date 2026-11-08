"""Test data layer: loaders parse real game files without error."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests._fixtures import get_store

from data.data_loader import DataLoader
from data.darkest_parser import DarkestParser
from data.rules import RulesLoader
from data.buff_loader import BuffLoader
from data.effect_loader import EffectLoader
from data.csv_loader import CsvLoader
from data.hero_loader import HeroLoader


from data.paths import GAME_DIR
from data.paths import CSV_DIR


def test_darkest_parser_hero():
    path = GAME_DIR / "heroes" / "abomination" / "abomination.info.darkest"
    records = DarkestParser.parse_file(path)
    assert len(records) > 0
    types = [r["_type"] for r in records]
    assert "resistances" in types
    assert "weapon" in types
    assert "armour" in types
    assert "combat_skill" in types
    print(f"  Abomination: {len(records)} records, skills={sum(1 for r in records if r['_type'] == 'combat_skill')}")


def test_darkest_parser_monster():
    path = GAME_DIR / "monsters" / "cultist_brawler" / "cultist_brawler_A" / "cultist_brawler_A.info.darkest"
    records = DarkestParser.parse_file(path)
    assert len(records) > 0
    types = [r["_type"] for r in records]
    assert "stats" in types
    assert "skill" in types  # monsters use 'skill:', heroes use 'combat_skill:'
    for r in records:
        if r["_type"] == "skill":
            assert "id" in r
            assert "atk" in r
            assert "dmg" in r
            break
    print(f"  Cultist Brawler A: {len(records)} records")


def test_darkest_parser_decimal_percent_value_not_truncated():
    """Real bug found and fixed: the unquoted-value regex's trailing
    lookahead (?=\\s*\\.|$) — added to tolerate zero whitespace before the
    next key, e.g. base.effects.darkest:655's '"Arb Mark Target".target' —
    also matched on the literal '.' inside an unquoted decimal number
    (e.g. '72.5%'), since a bare '.' looks identical to 'the next key
    starts here' with no way to tell them apart. The non-greedy value match
    would stop at '72', treating '.5%' as if it were an attempted next key,
    silently dropping it — so any unquoted decimal percent value (verified:
    779 field instances across 240 real game files, mostly .atk fields like
    fungal_bloat_A's 'fierce_swipe' skill '.atk 82.5%') parsed as a bare
    truncated integer (72), not a percent-decoded float (0.725). Fixed by
    tightening the lookahead to require a letter after the dot
    ((?=\\s*\\.[a-zA-Z_]|$)) — a real next-key start always has one, a
    decimal fraction never does."""
    r = DarkestParser._parse_line('skill: .atk 72.5% .dmg 2 4 .crit 12%')
    assert r["atk"] == 0.725, r["atk"]
    assert r["dmg"] == "2 4", r["dmg"]
    assert r["crit"] == 0.12, r["crit"]
    # the zero-whitespace-before-next-key case this lookahead exists for
    # must still work after tightening it
    r2 = DarkestParser._parse_line('effect: .name "Arb Mark Target".target "target"')
    assert r2["name"] == "Arb Mark Target"
    assert r2["target"] == "target"
    print("  PASS test_darkest_parser_decimal_percent_value_not_truncated")


def test_darkest_parser_effect():
    path = GAME_DIR / "effects" / "base.effects.darkest"
    records = DarkestParser.parse_file(path)
    effects = [r for r in records if r["_type"] == "effect"]
    assert len(effects) > 50
    push_effects = [e for e in effects if "push" in e]
    pull_effects = [e for e in effects if "pull" in e]
    print(f"  Effects: {len(effects)} total, {len(push_effects)} push, {len(pull_effects)} pull")


def test_rules_loader():
    path = GAME_DIR / "shared" / "rules.json"
    rules = RulesLoader.load(path)
    assert abs(rules.cutoff_always_hit - 0.95) < 0.001
    assert abs(rules.initiative_base - 8) < 0.001
    assert abs(rules.player_crit_multiplier - 1.5) < 0.001
    assert abs(rules.deathblow_chance - 1.0) < 0.001
    assert rules.stun_recovery_buff_id == "STUNRECOVERYBUFF"
    print(f"  Rules: init_base={rules.initiative_base}, crit_mult={rules.player_crit_multiplier}, deathblow={rules.deathblow_chance}")


def test_buff_loader():
    path = GAME_DIR / "shared" / "buffs" / "base.buffs.json"
    buffs = BuffLoader.load_buffs(path)
    assert len(buffs) > 100
    assert "MAXHP10" in buffs
    assert "STUNRECOVERYBUFF" in buffs
    stun_buff = buffs["STUNRECOVERYBUFF"]
    assert stun_buff.stat_type == "resistance"
    assert stun_buff.stat_sub_type == "stun"
    assert abs(stun_buff.amount - 0.5) < 0.001
    print(f"  Buffs: {len(buffs)} total")


def test_trinket_loader():
    path = GAME_DIR / "trinkets" / "base.entries.trinkets.json"
    trinkets = BuffLoader.load_trinkets(path)
    assert len(trinkets) > 50
    assert "ancestors_coat" in trinkets
    t = trinkets["ancestors_coat"]
    assert len(t.buffs) == 2
    assert t.rarity == "ancestral"
    print(f"  Trinkets: {len(trinkets)} total")


def test_quirk_loader():
    path = GAME_DIR / "shared" / "quirk" / "quirk_library.json"
    quirks = BuffLoader.load_quirks(path)
    assert len(quirks) > 50
    assert "tough" in quirks
    q = quirks["tough"]
    assert q.is_positive is True
    assert "MAXHP10" in q.buffs
    print(f"  Quirks: {len(quirks)} total")


def test_dlc_trinkets_and_quirks_merged_into_store():
    """Real bug found while wiring real per-comp trinkets/quirks
    (data/comp_loadout_loader.py) into CombatEngine.apply_trinkets_and_
    quirks: store.trinkets/store.quirks only ever loaded the base game's
    single file each, unlike store.buffs/store.effects (which already merge
    in dlc/**/*.buffs.json and *.effects.darkest). Any comp using a
    DLC-exclusive trinket (e.g. Color of Madness's "Thirsting Blade",
    com_hel_thirsting_blade — a real Hellion comp trinket) resolved a real
    id via localization but silently no-op'd in apply_trinkets_and_quirks
    since the id was never in store.trinkets. Also verifies the Arena PvP
    DLC's genuinely encrypted/binary trinkets file (not valid UTF-8, unlike
    every other plain-text game data file) is skipped rather than crashing
    the whole load."""
    store = get_store()
    assert "com_hel_thirsting_blade" in store.trinkets
    assert "hellion" in store.trinkets["com_hel_thirsting_blade"].class_requirements

    base_trinkets = BuffLoader.load_trinkets(GAME_DIR / "trinkets" / "base.entries.trinkets.json")
    base_quirks = BuffLoader.load_quirks(GAME_DIR / "shared" / "quirk" / "quirk_library.json")
    # Strictly more than the base-only counts -- a loose absolute threshold
    # wouldn't actually catch a regression here, since the base file alone
    # already clears a few hundred of each.
    assert len(store.trinkets) > len(base_trinkets), (len(store.trinkets), len(base_trinkets))
    assert len(store.quirks) > len(base_quirks), (len(store.quirks), len(base_quirks))
    print(f"  PASS test_dlc_trinkets_and_quirks_merged_into_store "
          f"({len(store.trinkets)} trinkets vs {len(base_trinkets)} base, "
          f"{len(store.quirks)} quirks vs {len(base_quirks)} base)")


def test_effect_loader():
    path = GAME_DIR / "effects" / "base.effects.darkest"
    effects = EffectLoader.load(path)
    assert len(effects) > 50
    assert "Push 2A" in effects
    assert "Pull 1A" in effects
    print(f"  Effects: {len(effects)} loaded")


def test_full_load():
    store = get_store()
    assert len(store.heroes) > 0
    assert len(store.buffs) > 100
    assert len(store.trinkets) > 50
    assert len(store.quirks) > 50
    assert len(store.effects) > 50
    assert store.rules is not None
    print(f"  Full load: {len(store.heroes)} heroes, {len(store.enemies)} enemies, "
          f"{len(store.buffs)} buffs, {len(store.trinkets)} trinkets, "
          f"{len(store.quirks)} quirks, {len(store.effects)} effects")


def test_every_hero_class_in_the_map_loads_by_name():
    """Every class in HeroLoader.HERO_DIR_MAP must load individually — including
    the 3 DLC classes (shieldbreaker, musketeer, flagellant), whose
    .info.darkest files live under dlc/<pkg>/... not heroes/.

    Regression test for the original bug: HERO_DIR_MAP/HERO_CLASS_MAP already
    listed these 3 as DLC, but load_all() only searched heroes/, so they were
    silently absent from store.heroes with no error — a len(store.heroes) > 0
    style check would never have caught it.
    """
    store = get_store()
    expected_ids = list(HeroLoader.HERO_DIR_MAP.keys())
    # Not a hardcoded count: the map IS the expectation, and every entry in it
    # must load. It said 18 until The Fire's Edge added two, at which point the
    # count was the only thing that failed -- which tested the number, not the
    # loader. Vanilla is the invariant worth pinning; DLC is whatever is installed.
    vanilla = [hid for hid in expected_ids
               if store.heroes.get(hid) and store.heroes[hid].hero_class.name != "DLC"]
    assert len(vanilla) == 15, f"Expected 15 vanilla classes, got {sorted(vanilla)}"
    failures = [hid for hid in expected_ids if hid not in store.heroes]
    assert not failures, f"Failed to load: {failures}"
    for hid in ("shieldbreaker", "musketeer", "flagellant", "duelist", "runaway"):
        assert store.heroes[hid].hero_class.name == "DLC", f"{hid} should be classified as DLC"
    print(f"  All {len(expected_ids)} hero classes loaded individually "
          f"({len(vanilla)} vanilla + {len(expected_ids) - len(vanilla)} DLC)")


def test_weapon_tier_damage_matches_real_game_file():
    """Real bug found and fixed: hero_loader.py's weapon-tier parsing
    checked for 'dmg_min'/'dmg_max' keys that never exist in any real
    parsed record — the actual key is a single 'dmg' field with a two-part
    value ('6 12'), same convention as monster damage (parse_monster_dmg).
    Because rec.get(key, 1) silently defaults when the key is absent, every
    hero's weapon-derived dmg_min/dmg_max was 1/1 at every tier, for the
    entire project's history — undetected because tests/engine/test_resolution.py
    constructs CombatantState manually (bypassing this loader) instead of
    going through real HeroDefinition data, so no existing test exercised
    the real numbers. Verified directly against
    heroes/crusader/crusader.info.darkest's 5 real .dmg lines."""
    store = get_store()
    crusader = store.heroes["crusader"]
    expected = [(6, 12), (7, 14), (8, 16), (9, 17), (10, 19)]
    actual = [(wt.dmg_min, wt.dmg_max) for wt in crusader.weapon_tiers]
    assert actual == expected, actual
    print(f"  PASS test_weapon_tier_damage_matches_real_game_file ({actual})")


def test_a_comp_losing_a_slot_to_an_unloadable_class_is_detectable():
    """User-reported real problem, now caught STRUCTURALLY (2026-08-23).

    Some comps are missing a real slot to a fan-made class this project cannot
    resolve -- "Holy Hymn: Monk" was really 4 heroes (Highwayman, Jester,
    Vestal and a modded "Monk") and trained as a 3-hero party, which is not a
    fair test of a comp balanced around 4.

    This used to be detected by a name list parsed from the "Modded Heroes: X"
    line of `COMPS/Comps/<name> <hash>.md`. Those files were deleted on
    2026-08-23 along with the retired CSV, and the check they backed is
    replaced by `CompLoadoutEntry.declared_heroes`: the count of hero slots the
    source JSON asked for with a NON-EMPTY heroClass, against the count that
    actually resolved. Strictly better -- it catches ANY unloadable class, not
    just the eleven someone had listed, and there is no external file to keep
    in sync.

    An EMPTY heroClass is padding, not a loss: the export pads every comp to
    four slots, so the 2-hero tutorial party legitimately carries two blanks.
    """
    import json
    import tempfile
    from data.comp_loadout_loader import CompLoadoutLoader

    store = get_store()
    # A LOST SLOT IS ONLY ACCEPTABLE WHEN THE CLASS IS GENUINELY UNLOADABLE.
    #
    # This used to assert no shipped comp loses a slot at all, which was true
    # while every modded-hero comp had been dropped from the export. Since
    # 2026-08-27 `composed_comps/` can hold coverage for a third-party class,
    # and this suite runs WITHOUT `DDSIM_MOD_DIRS` -- so those comps lose the
    # modded slot here, on purpose, and `build_comp_pool` excludes them.
    #
    # The invariant that still means something is that a loss is EXPLAINED: it
    # must be a class this store does not have. A slot lost to a name-map bug
    # on a class we DO load is the silent failure this test exists for, and
    # that is what the check below still catches.
    class_map = CompLoadoutLoader._hero_class_map(store)
    for entry in store.comp_loadouts.values():
        if len(entry.heroes) >= entry.declared_heroes:
            continue
        declared = [h for h in entry.declared_classes or ()
                    if class_map.get(h) in store.heroes]
        assert len(declared) == len(entry.heroes), (
            f"{entry.name} lost a slot to something OTHER than an unloadable "
            f"class: {entry.declared_heroes} declared, {len(entry.heroes)} "
            f"resolved, {len(declared)} of the declared classes are loadable")

    # Built in a TEMP tree, never in `metadata/COMPS/`: the suite runs under
    # xdist, so a probe file dropped into the shared folder is visible to every
    # other worker for as long as it exists.
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "metadata" / "COMPS" / "curated_comps"
        root.mkdir(parents=True)
        (root / "_modded_probe.json").write_text(json.dumps({
            "teamName": "Modded Probe",
            "location": "The Ruins",
            "heroes": [{"heroClass": c, "activeSkills": [], "activeCampSkills": [],
                        "trinket1": None, "trinket2": None,
                        "quirks": {"positive": [], "negative": []}}
                       for c in ("Crusader", "Vestal", "Monk", "")],
        }), encoding="utf-8")
        entries = {e.name: e for e in
                   CompLoadoutLoader.load_all(Path(td), GAME_DIR, store)}
        probe = entries["Modded Probe"]

    # "Monk" does not resolve and IS counted as declared; the trailing blank is
    # padding for a short party and is not.
    assert probe.declared_heroes == 3, probe.declared_heroes
    assert len(probe.heroes) == 2, [h.hero_id for h in probe.heroes]
    assert len(probe.heroes) < probe.declared_heroes, "the loss must be visible"

    # ...and the pool must actually act on it. A fresh store, because this
    # mutates `comp_loadouts` -- see tests/_fixtures.py.
    from env.expedition_env import build_comp_pool
    scratch = get_store(fresh=True)
    scratch.comp_loadouts["Modded Probe"] = probe
    pool = {n for n, _z, _h in build_comp_pool(scratch)}
    assert "Modded Probe" not in pool, "a comp missing a real slot reached the pool"
    print("  PASS test_a_comp_losing_a_slot_to_an_unloadable_class_is_detectable")


def test_csv_hero_roster():
    rows = DataLoader.load_csv_roster(CSV_DIR)
    assert len(rows) > 0
    names = [r.get("Nombre", "") for r in rows]
    assert "Abomination" in names
    assert "Crusader" in names
    print(f"  CSV heroes: {len(rows)} entries")


def test_a_QUESTION_MARK_target_prefix_survives_the_parse():
    """`.target ?1234` dropped the WHOLE key, and it is shipped on a vanilla
    hero.

    `decode_position_notation` was taught about `?1234` when the modded corpus
    turned up 88 of them -- but the value never reached it. The unquoted-value
    character class carried `@` and `~` and not `?`, so the value alternative
    failed to match, and an unmatchable value drops the whole key rather than
    arriving malformed. `target` was therefore ABSENT from the record,
    decoded as `[]`, and the skill could not be aimed at anything.

    Not a mod-only shape: the **Arbalest's blindfire** (all five levels, a
    base-game hero) and the **Musketeer's sidearm** both carry it, and both
    are rank-4 shots the real game aims at any enemy rank.
    """
    from data.darkest_parser import DarkestParser

    line = ('combat_skill: .id "blindfire" .level 4 .type "ranged" .atk 95% '
            '.dmg -10% .crit 4% .launch 4321 .target ?1234 .is_crit_valid '
            'True  .effect "Arb Self Speed 5"')
    rec = DarkestParser._parse_line(line)
    assert rec is not None
    assert "target" in rec, "the whole key vanished again"
    assert rec["target"] == "?1234"
    assert DarkestParser.decode_position_notation(rec["target"]) == [1, 2, 3, 4]
    # The keys either side must be unharmed: a greedy value would eat the
    # next key's name, which is what the negative lookahead prevents.
    assert rec["launch"] == 4321
    assert rec["is_crit_valid"] is True
    assert rec["effect"] == "Arb Self Speed 5"


def test_the_two_SHIPPED_skills_with_that_prefix_can_be_aimed():
    """The end-to-end half: the store, not the regex. A parse fix that does
    not reach `SkillLevelData.target` has fixed nothing."""
    store = get_store()
    for hero, sid in (("arbalest", "blindfire"), ("musketeer", "sidearm")):
        h = store.heroes.get(hero)
        if h is None:
            continue                     # DLC not installed; not a failure
        sk = next((s for s in h.skills if s.id == sid), None)
        assert sk is not None, f"{hero} has no {sid}"
        for lv, data in sk.levels.items():
            assert data.target == [1, 2, 3, 4], (
                f"{hero}/{sid} level {lv} targets {data.target}")
            assert not data.is_aoe, "'?' is not the AoE marker; '~' is"


if __name__ == "__main__":
    print("=== Data Layer Tests ===\n")
    for name, func in sorted(globals().items()):
        if name.startswith("test_"):
            try:
                func()
                print(f"  PASS {name}")
            except Exception as e:
                print(f"  FAIL {name}: {e}")
    print("\nDone.")
