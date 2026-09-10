"""Tests for CompLoadoutLoader (data/comp_loadout_loader.py) — parses
COMPS/curated_comps/*.json, the only data source in this project that
records which 4 of a hero's ~7 skills a given real comp actually equips
(.info.darkest lists the full movepool; COMPS/<hash>.csv only has hero
class + zone, no skills). Found while investigating why a trained model's
Highwayman had access to skills (take_aim) that "Dark Ritual: Heist" never
actually equips in the real game.
"""

import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests._fixtures import get_store, comp_with_classes

from data.comp_loadout_loader import CompLoadoutLoader, HERO_DISPLAY_NAME_TO_ID
from env.expedition_env import UNRESTRICTED_HEROES
from engine import SeededRNG, CombatEngine


from data.paths import GAME_DIR
from data.paths import CSV_DIR
store = get_store()


COMPOSED_MARKER = "composed from curated hero blocks; no fitness involved"


COMP_DIRS = ("curated_comps", "composed_comps")


def _comp_docs(*folders):
    for name in folders:
        for path in sorted((CSV_DIR / "metadata/COMPS" / name).glob("*.json")):
            yield json.loads(path.read_text(encoding="utf-8"))


def _composed_comps():
    """The training-coverage comps. They live in their OWN folder, because
    "curated" in this project means Fran played the comp and vouches for it,
    and these are machine recombinations nobody has played. The marker is
    checked too, so a file landing in the wrong folder fails rather than
    silently counting as human-tested."""
    out = {}
    for doc in _comp_docs("composed_comps"):
        assert (doc.get("provenance") or {}).get("authoredBy") == COMPOSED_MARKER,             f"{doc['teamName']} is in composed_comps/ without the composed marker"
        out[doc["teamName"]] = doc
    for doc in _comp_docs("curated_comps"):
        assert (doc.get("provenance") or {}).get("authoredBy") != COMPOSED_MARKER,             f"{doc['teamName']} is a composed comp sitting in curated_comps/"
    return out


def test_the_composed_comps_are_legal_parties():
    """These exist to give the two starved classes training coverage WITHOUT
    the GA's fitness touching them -- a comp selected by the policy's own score
    and then trained on is the loop Fran named. They are recombined from
    human-authored hero blocks, and recombination is exactly what breaks the
    two invariants below, so both are pinned here rather than trusted.
    """
    composed = _composed_comps()
    assert composed, "no composed comps found; the provenance marker moved"
    limited = {tid for tid, t in store.trinkets.items() if t.limit}
    checked = 0
    for name, doc in composed.items():
        loadout = store.comp_loadouts.get(name)
        assert loadout is not None, f"{name} does not load"
        # A COMP NAMING A CLASS THIS STORE CANNOT LOAD IS EXCLUDED, NOT BROKEN.
        # Since 2026-08-27 `composed_comps/` can hold coverage for a modded
        # class, and this suite runs WITHOUT `DDSIM_MOD_DIRS` -- so the Sibyl's
        # slot does not resolve and the comp arrives short. That is the
        # structural fan-made-class filter working exactly as designed:
        # `build_comp_pool` drops any comp with
        # `len(heroes) < declared_heroes`, which is what replaced the old
        # `.md`-name list. Asserting four heroes here would be asserting that
        # no composed comp may ever field a third-party class.
        #
        # What still must hold is the legality of every comp that DOES fully
        # resolve, which is the invariant recombination breaks.
        if len(loadout.heroes) < loadout.declared_heroes:
            continue
        assert len(loadout.heroes) == 4, f"{name}: {len(loadout.heroes)} heroes"
        checked += 1
        one_offs = 0
        for hero in loadout.heroes:
            # Two trinkets, both RESOLVED. A curated slot whose display name
            # collides with a localization-only id equips nothing, and
            # composing from one of those silently yields a hero with one
            # trinket or none.
            assert len(hero.trinket_ids) == 2, f"{name}/{hero.hero_id}: {hero.trinket_ids}"
            assert len(set(hero.trinket_ids)) == 2, f"{name}/{hero.hero_id}: duplicate"
            one_offs += sum(1 for t in hero.trinket_ids if t in limited)
            skills = {s.id for s in store.heroes[hero.hero_id].skills}
            assert not set(hero.skill_ids) - skills, f"{name}/{hero.hero_id}: bad skills"
        # Fran's rule, and the one crossover always breaks: three comps each
        # legally holding one Ancestral trinket compose into a party holding
        # three, which nobody can field.
        assert one_offs <= 1, f"{name}: {one_offs} limit-bearing trinkets"
    assert checked, ("every composed comp was skipped as unresolvable -- either "
                     "they all field a modded class, or the loader stopped "
                     "resolving vanilla ones")


def test_every_hero_class_has_at_least_five_curated_comps():
    """Fran's target, and the reason the composed comps exist. Duelist had 2
    and Runaway 1 while every other class had 9 or more, so those two were
    nearly absent from the training distribution."""
    # COUNT RESOLVED IDS, NOT THE RAW STRING. One class can legitimately be
    # written two ways -- `sibyl_ms` by `compose_class_comps.py`, "Sibyl" by
    # Fran, both resolving to the same hero -- and counting strings splits her
    # into two classes with too few comps each. Anything that does not resolve
    # is a class this store cannot load, which is the fan-made-class case and
    # not a shortage.
    from data.comp_loadout_loader import CompLoadoutLoader
    from env.gym_env import GAME_DIR
    class_map = CompLoadoutLoader._hero_class_map(store, GAME_DIR)
    counts = {}
    for doc in _comp_docs(*COMP_DIRS):
        # An EMPTY heroClass is padding, not a missing class: the export pads
        # every comp to four slots, so the 2-hero tutorial party arrives with
        # two blank ones. Counting them gives a phantom class named "".
        ids = {class_map.get(h["heroClass"]) for h in doc.get("heroes", [])
               if h.get("heroClass")}
        for hero_id in ids - {None}:
            counts[hero_id] = counts.get(hero_id, 0) + 1
    short = {c: n for c, n in counts.items()
             if n < 5 and c in store.heroes}
    assert not short, f"classes with fewer than 5 comps: {short}"


def test_loads_real_comp_loadouts():
    assert len(store.comp_loadouts) > 100, len(store.comp_loadouts)
    print(f"  PASS test_loads_real_comp_loadouts ({len(store.comp_loadouts)} comps)")


def test_dark_ritual_heist_resolves_real_skill_ids():
    """Resolved by SHAPE, not by name -- a re-export deletes comp names, and
    this one was "Dark Ritual: Heist" until 2026-08-26.

    Regression test for the exact combo under investigation: Plague
    Doctor's blinding_gas + Occultist's hands_from_abyss stun opener,
    Highwayman/Crusader burst. Confirms the loader resolves real display
    names ("Hands from the Abyss", "Sacrificial Stab") to the real internal
    skill ids used by CombatEngine — including Occultist's bloodlet, whose
    display name ("Sacrificial Stab") no longer matches its internal id at
    all (a real, independently-verified in-game renaming)."""
    entry = comp_with_classes("occultist", "plague_doctor", "crusader", "highwayman", store=store)
    by_hero = {h.hero_id: set(h.skill_ids) for h in entry.heroes}
    assert by_hero["occultist"] == {"bloodlet", "weakening_curse", "wyrd_reconstruction", "hands_from_abyss"}
    assert by_hero["plague_doctor"] == {"noxious_blast", "blinding_gas", "battlefield_medicine", "disorienting_blast"}
    assert by_hero["crusader"] == {"smite", "stunning_blow", "holy_lance", "inspiring_cry"}
    # Highwayman's real loadout excludes take_aim and wicked_slice/grape_shot_blast —
    # the skills a full-movepool simulation would wrongly make available.
    assert by_hero["highwayman"] == {"pistol_shot", "point_blank_shot", "duelist_advance", "opened_vein"}
    assert "take_aim" not in by_hero["highwayman"]
    print("  PASS test_dark_ritual_heist_resolves_real_skill_ids")


def test_resolved_skill_ids_are_real_skills_of_their_hero():
    """Every resolved skill id, across every loaded comp, must be a real
    skill of that hero's HeroDefinition — catches any hero-id-prefix
    mismatch in the name resolver (e.g. multi-word hero ids like
    man_at_arms/bounty_hunter/grave_robber/plague_doctor being split
    incorrectly on the first underscore)."""
    bad = []
    for entry in store.comp_loadouts.values():
        for h in entry.heroes:
            hd = store.heroes.get(h.hero_id)
            real_ids = {s.id for s in hd.skills} if hd else set()
            for sid in h.skill_ids:
                if sid not in real_ids:
                    bad.append((entry.name, h.hero_id, sid))
    assert not bad, bad
    print("  PASS test_resolved_skill_ids_are_real_skills_of_their_hero")


def test_most_heroes_resolve_all_4_skills():
    """Real-data coverage check, not a hard requirement (a handful of
    entries have skill-name variants that don't match localization
    exactly) — but the overwhelming majority should resolve cleanly."""
    total = 0
    full = 0
    for entry in store.comp_loadouts.values():
        for h in entry.heroes:
            total += 1
            if len(h.skill_ids) == 4:
                full += 1
    rate = full / total
    assert rate > 0.9, f"only {full}/{total} ({rate:.1%}) heroes resolved all 4 skills"
    print(f"  PASS test_most_heroes_resolve_all_4_skills ({full}/{total} = {rate:.1%})")


def test_trinket_and_quirk_names_resolve_to_real_ids():
    entry = comp_with_classes("occultist", "plague_doctor", "crusader", "highwayman", store=store)
    occultist = next(h for h in entry.heroes if h.hero_id == "occultist")
    assert "demons_cauldron" in occultist.trinket_ids
    assert all(tid in store.trinkets for tid in occultist.trinket_ids)
    print("  PASS test_trinket_and_quirk_names_resolve_to_real_ids")


def test_engine_restricts_valid_actions_to_the_real_loadout():
    """CombatEngine.reset(hero_skill_ids=...) should make take_aim
    unavailable to a Highwayman restricted to Dark Ritual: Heist's real
    loadout, while all 4 real skills remain available."""
    hwm = store.heroes["highwayman"]
    brawler = store.enemies["cultist_brawler_A"]
    loadout = comp_with_classes("occultist", "plague_doctor", "crusader", "highwayman", store=store)
    hwm_skills = next(h.skill_ids for h in loadout.heroes if h.hero_id == "highwayman")

    engine = CombatEngine(store, SeededRNG(1))
    engine.reset(hero_defs=[hwm], enemy_defs=[brawler, brawler], hero_skill_ids=[hwm_skills])
    available = set(engine.get_hero_skill_ids(0))
    assert available == set(hwm_skills)
    assert "take_aim" not in available
    print("  PASS test_engine_restricts_valid_actions_to_the_real_loadout")


def test_engine_default_behavior_unchanged_without_hero_skill_ids():
    """hero_skill_ids=None (default) must stay behaviorally identical to
    before this parameter existed — full movepool, same convention as
    hero_levels=None."""
    hwm = store.heroes["highwayman"]
    brawler = store.enemies["cultist_brawler_A"]
    engine = CombatEngine(store, SeededRNG(1))
    engine.reset(hero_defs=[hwm], enemy_defs=[brawler, brawler])
    assert set(engine.get_hero_skill_ids(0)) == {s.id for s in hwm.skills}
    print("  PASS test_engine_default_behavior_unchanged_without_hero_skill_ids")



def test_the_fuzzy_skill_name_fallback_now_fires_on_nothing():
    """It was added on 2026-08-22 for two hand-written misspellings -- the
    Vestal's "Judgment" for the game's "Judgement" (15 hero slots) and the
    Grave Robber's "Poison Dart" for "Poison Darts" (8), which between them
    left 23 heroes holding THREE skills.

    Fran corrected both files the same day, so the corpus no longer needs it.
    The fallback stays as the net for the next hand-written comp, and this
    test pins that it currently catches nothing: an entry appearing here means
    a comp was written with a name the game does not use, which is worth
    knowing about rather than silently repairing.

    It is deliberately narrow -- exact match first, scoped to one hero's ~7
    skills, difflib ratio 0.9, refusing whenever the runner-up is within 0.05.
    """
    from data.comp_loadout_loader import _resolve_skill_name
    maps = CompLoadoutLoader._build_skill_name_maps(GAME_DIR, set(store.heroes))
    fuzzy = set()
    for path in sorted((CSV_DIR / "metadata" / "COMPS" / "curated_comps").glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        for hero in data.get("heroes", []):
            hero_id = HERO_DISPLAY_NAME_TO_ID.get(hero.get("heroClass", ""))
            names = maps.get(hero_id, {})
            for name in hero.get("activeSkills", []):
                if name in names:
                    continue
                resolved = _resolve_skill_name(name, names)
                if resolved is not None:
                    fuzzy.add((hero_id, name, resolved))
    assert fuzzy == set(), sorted(fuzzy)


def test_the_fallback_still_resolves_the_names_it_was_built_for():
    """Pinned separately from the corpus, so correcting a file cannot quietly
    delete the safety net along with the evidence for it."""
    from data.comp_loadout_loader import _resolve_skill_name
    maps = CompLoadoutLoader._build_skill_name_maps(GAME_DIR, set(store.heroes))
    assert _resolve_skill_name("Judgment", maps["vestal"]) == "judgement"
    assert _resolve_skill_name("Poison Dart", maps["grave_robber"]) == "poison_dart"
    # And refuses a name that is close to nothing in particular.
    assert _resolve_skill_name("Fireball", maps["vestal"]) is None


def test_every_restricted_hero_in_every_curated_comp_has_four_skills():
    """The consequence of the above, stated as the thing that actually matters:
    a comp evaluated with three of its four skills is not that comp."""
    short = [(name, h.hero_id, len(h.skill_ids))
             for name, entry in store.comp_loadouts.items()
             for h in entry.heroes
             if h.hero_id not in UNRESTRICTED_HEROES and len(h.skill_ids) != 4]
    assert not short, short[:5]

if __name__ == "__main__":
    print("=== Comp Loadout Tests ===\n")
    for name, func in sorted(globals().items()):
        if name.startswith("test_"):
            try:
                func()
            except Exception as e:
                import traceback
                print(f"  FAIL {name}: {e}")
                traceback.print_exc()
    print("\nDone.")
