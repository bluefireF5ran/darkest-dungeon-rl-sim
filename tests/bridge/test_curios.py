"""Tests for the curio policy (`bridge/curios.py`, `data/curio_loader.py`).

Two jobs. The first is pinning the format: `curio_type_library.csv` is a
spreadsheet dump with two unrelated tables sharing every row, and its `% CHANCE`
column is stale in places, so the weights are the only trustworthy numbers.

The second is keeping the policy **falsifiable**. PLAYBOOK 2.4 is an expert's
rules; this module scores every curio from the game's own tables instead of
encoding those rules, so the rules become predictions that can fail. Where the
data disagrees with the playbook the test records the disagreement rather than
forcing either side — see `test_only_one_of_the_book_curios_is_actually_negative`.
"""

import glob
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest

from bridge.curios import (
    SUPPLY_ITEMS, CurioHero, advise, advise_map, bare_hand_value,
    definition_for, item_value, result_value, _curios, _effects, _quirks,
)
from bridge.dungeon_map import read_dungeon_map
from data.curio_loader import Result

REPO = Path(__file__).resolve().parents[2]
SNAPSHOTS = sorted(glob.glob(str(REPO / "archive" / "traces" / "raidsnaps*" / "*map*.json")))

FULL_KIT = {i: 4 for i in SUPPLY_ITEMS}


# --- the format ---

def test_the_library_parses_into_curios_with_outcomes():
    curios = _curios()
    assert len(curios) > 50
    strongbox = curios["unlocked_strongbox"]
    assert strongbox.verdict == "Good"
    assert "Treasure" in strongbox.tags
    assert strongbox.chance("Loot") == pytest.approx(0.75)
    assert strongbox.chance("Effect") == pytest.approx(0.25)


def test_chances_come_from_weights_not_from_the_stale_percent_column():
    """Every curio's outcome chances must sum to 1 by construction. The sheet's
    own `% CHANCE` column does not always."""
    for curio in _curios().values():
        if not curio.outcomes:
            continue
        total = sum(curio.chance(k) for k in curio.outcomes)
        assert total == pytest.approx(1.0)


def test_sub_results_are_parsed_with_their_weights():
    """`Quirk: positive x2, negative x1` is the whole reason polarity can be
    resolved at all — dropping the sub-results loses it."""
    books = _curios()["stack_of_books"]
    quirk = books.outcomes["Quirk"]
    assert {r.name: r.weight for r in quirk.results} == {"positive": 2.0, "negative": 1.0}


def test_item_interactions_are_parsed():
    locked = _curios()["locked_strongbox"]
    assert set(locked.items) == {"skeleton_key", "shovel"}
    assert locked.items["skeleton_key"].result_type == "Loot"


def test_the_map_stores_a_prop_name_that_must_be_translated_to_a_type():
    """`persist.map.json` stores the prop; the library is keyed by type. They
    differ for some curios, so the lookup cannot skip the mapping."""
    assert definition_for("locked_strongbox") is not None
    assert definition_for("no_such_curio") is None


# --- polarity, resolved against real definitions ---

def test_a_journal_page_is_not_loot():
    """`stack_of_books`' only Loot result is JOURNALONLY — lore, not resources.
    Scoring it as loot makes books look worth opening."""
    assert result_value("Loot", Result("JOURNALONLY")) == 0.0
    assert result_value("Loot", Result("A")) == 1.0


def test_quirk_polarity_comes_from_the_quirk_library():
    assert result_value("Quirk", Result("positive")) == 1.0
    assert result_value("Quirk", Result("negative")) == -1.0
    # bloodthirsty is what pile_of_bones grants, and it is a negative compulsion.
    assert _quirks()["bloodthirsty"]["is_positive"] is False
    assert result_value("Quirk", Result("bloodthirsty")) == -1.0


def test_effect_polarity_comes_from_the_real_effect_definitions():
    assert result_value("Effect", Result("Blight 1")) == -1.0
    assert result_value("Effect", Result("Stress 3")) == -1.0


def test_every_effect_named_by_a_curio_exists_in_the_effects_file():
    """The polarity model returns 0.0 for an unknown effect — an honest
    'I don't know'. This asserts it never has to: all 30 resolve."""
    effects = _effects()
    named = set()
    for curio in _curios().values():
        for kind, outcome in curio.outcomes.items():
            if kind == "Effect":
                named.update(r.name for r in outcome.results)
        for item in curio.items.values():
            if item.result_type == "Effect":
                named.update(r.name for r in item.results)
    missing = sorted(n for n in named if n not in effects)
    assert not missing, f"unjudgeable effects: {missing}"


def test_purge_and_scouting_are_good_and_disease_is_bad():
    assert result_value("Purge", Result("negative")) == 1.0
    assert result_value("Scouting", Result("2 - all")) == 1.0
    assert result_value("Disease", Result("random")) == -1.0


# --- the policy ---

def test_a_matching_provision_is_not_automatically_recommended():
    """PLAYBOOK 2.4 says interact with a curio's provision when it rewards. Four
    interactions do the opposite, so a naive 'always use the matching item' rule
    walks into them. `bas_relief` answers a shovel with Stress 5, which also
    makes it a counterexample to a blanket 'dig everything you can'."""
    relief = _curios()["bas_relief"]
    assert item_value(relief.items["shovel"]) < 0
    advice = advise("bas_relief", "rooA", FULL_KIT, ())
    assert advice.item != "shovel"


def test_the_result_type_alone_does_not_settle_polarity():
    """`Effect` is hostile on `stack_of_books` (Stress 5) and helpful on
    `confession_booth` (a stress heal). Judging by the type — the obvious
    shortcut — gets one of them backwards whichever way it is chosen."""
    books = _curios()["stack_of_books"]
    booth = _curios()["confession_booth"]
    assert books.items["torch"].result_type == booth.items["holy_water"].result_type == "Effect"
    assert item_value(books.items["torch"]) < 0
    assert item_value(booth.items["holy_water"]) > 0


def test_a_shovel_can_hand_you_a_negative_quirk():
    """`decorative_urn` + shovel yields `guilty_conscience`, a compulsion. The
    quirk library is what makes this visible; the result type says only 'Quirk'."""
    urn = _curios()["decorative_urn"]
    assert [r.name for r in urn.items["shovel"].results] == ["guilty_conscience"]
    assert item_value(urn.items["shovel"]) < 0
    assert advise("decorative_urn", "rooA", FULL_KIT, ()).item != "shovel"


def test_a_rewarding_provision_is_recommended():
    advice = advise("locked_strongbox", "rooG", {"skeleton_key": 1}, ())
    assert advice.action == "use" and advice.item == "skeleton_key"


def test_without_the_key_a_trapped_chest_is_skipped():
    advice = advise("locked_strongbox", "rooG", {}, ())
    assert advice.action == "skip" and "skeleton_key" in advice.why


def test_the_shamblers_altar_is_never_a_routine_yes():
    """PLAYBOOK 2.4: only when you choose to, and reorder to the WORST formation
    first, because the shuffle is forced."""
    advice = advise("shamblers_altar", "rooA", FULL_KIT, ())
    assert advice.action == "optional"
    assert "worst" in advice.criterion.lower()


def test_an_item_id_no_inventory_file_defines_is_reported_as_unusable():
    """`sacrificial_stone`'s only interaction names `provision`, which is not a
    supply item. Advising the player to go buy one would be a fiction."""
    stone = _curios()["sacrificial_stone"]
    assert set(stone.items) == {"provision"}
    assert "provision" not in SUPPLY_ITEMS
    advice = advise("sacrificial_stone", "rooF", FULL_KIT, ())
    assert advice.action == "skip" and "no inventory file defines" in advice.why


# --- who does it (PLAYBOOK 2.4's hero rules) ---

def test_a_purge_goes_to_the_hero_with_the_worst_quirks():
    party = (
        CurioHero(id="1", hero_class="crusader", quirks=("kleptomaniac", "slow_reflexes")),
        CurioHero(id="2", hero_class="vestal", quirks=("quick_reflexes",)),
    )
    advice = advise("pile_of_scrolls", "coBC", {"torch": 4}, party)
    assert advice.item == "torch"
    assert advice.hero == "crusader"
    assert "quirk" in advice.criterion


def test_a_purge_names_nobody_when_the_party_has_no_negative_quirks():
    party = (CurioHero(id="1", hero_class="vestal", quirks=("quick_reflexes",)),)
    advice = advise("pile_of_scrolls", "coBC", {"torch": 4}, party)
    assert advice.hero is None


def test_stress_relief_goes_to_the_most_stressed_hero():
    party = (
        CurioHero(id="1", hero_class="crusader", stress=20.0, hp=10.0, max_hp=40.0),
        CurioHero(id="2", hero_class="vestal", stress=80.0, hp=30.0, max_hp=30.0),
    )
    stress_curios = [c for c in _curios().values()
                     if any(r.name for k, o in c.outcomes.items() if k == "Effect"
                            for r in o.results
                            if _effects().get(r.name) and _effects()[r.name].stress_heal)]
    if not stress_curios:
        pytest.skip("no bare-hand stress-relief curio in the library")

    # `advise` names a hero only when the outcome it RECOMMENDS relieves stress,
    # and `_pick_hero` is silent otherwise. holy_fountain's best outcome is loot,
    # so it names nobody -- which is correct, and is why testing
    # `stress_curios[0]` alone proved nothing: the old assertion sat behind
    # `if advice.hero is not None` and never ran.
    named = {c.id: advise(c.id, "rooA", {}, party).hero
             for c in stress_curios}
    assert any(h is not None for h in named.values()),         f"no stress curio named a hero at all: {named}"
    for curio_id, hero in named.items():
        if hero is not None:
            assert hero == "vestal", f"{curio_id} sent the relief to {hero}"


def test_the_damage_buff_rule_states_its_criterion_instead_of_guessing():
    """A save carries no damage number and the bridge builds no combatants, so
    naming a hero here would be invented."""
    from bridge.curios import _pick_hero
    buff_effects = [n for n, e in _effects().items() if e.is_stat_buff or e.buff_ids]
    if not buff_effects:
        pytest.skip("no buff effects loaded")
    hero, criterion = _pick_hero("Effect", (Result(buff_effects[0]),),
                                 (CurioHero(id="1", hero_class="crusader"),))
    assert hero is None and "damage" in criterion


# --- compulsions: skipping is advice, not a guarantee ---

def test_a_compulsive_hero_is_named_on_a_matching_curio():
    party = (CurioHero(id="1", hero_class="crusader", quirks=("kleptomaniac",)),)
    advice = advise("unlocked_strongbox", "rooA", {}, party)
    names = {c.quirk for c in advice.compulsions}
    assert "kleptomaniac" in names
    assert all(c.chance > 0 for c in advice.compulsions)


def test_every_curio_tag_quirk_in_the_game_is_negative():
    """The premise of the warning: there is no such thing as a *helpful*
    compulsion, so a named one is always a risk to the advice."""
    tagged = [q for q in _quirks().values() if q.get("curio_tag")]
    assert tagged
    assert all(q.get("is_positive") is False for q in tagged)


def test_a_hero_with_no_matching_tag_is_not_warned_about():
    party = (CurioHero(id="1", hero_class="vestal", quirks=("ablutomania",)),)
    advice = advise("unlocked_strongbox", "rooA", {}, party)
    assert "ablutomania" not in {c.quirk for c in advice.compulsions}


# --- the expert's rules as predictions ---

def test_only_one_of_the_book_curios_is_actually_negative():
    """PLAYBOOK 2.4 says books and libraries are net negative, don't touch.

    The tables only back that for `stack_of_books`. `bookshelf` and
    `pile_of_scrolls` score slightly POSITIVE bare-handed, mostly because they
    can roll Scouting. All three sit near zero, so 'not worth the turn' survives
    as a reading — but 'net negative' does not, and this test records the
    disagreement rather than hard-coding either side. Resolving it needs
    magnitudes (a Stress 3 hit against a loot draw), which no shipped file
    provides; inventing them would be a convention wearing game-data clothes.
    """
    values = {k: bare_hand_value(_curios()[k])
              for k in ("stack_of_books", "bookshelf", "pile_of_scrolls")
              if k in _curios()}
    assert values["stack_of_books"] < 0
    assert all(abs(v) < 0.25 for v in values.values()), values


def test_a_bare_trapped_container_is_maximally_bad():
    """The unambiguous end of the scale: several containers do nothing but hurt
    you without their key or shovel."""
    for name in ("locked_sarcophagus", "shallow_grave", "bandits_trapped_chest"):
        if name in _curios():
            assert bare_hand_value(_curios()[name]) == -1.0


# --- against the real captures ---

def test_every_curio_in_the_captured_maps_gets_advice():
    seen = 0
    for path in SNAPSHOTS[:40]:
        dm = read_dungeon_map(path)
        if dm is None:
            continue
        for advice in advise_map(dm, FULL_KIT, ()):
            assert advice.action in ("use", "interact", "skip", "optional")
            assert advice.line()
            seen += 1
    assert seen > 0


def test_no_captured_curio_falls_through_to_not_in_the_library():
    for path in SNAPSHOTS[:40]:
        dm = read_dungeon_map(path)
        if dm is None:
            continue
        for advice in advise_map(dm, FULL_KIT, ()):
            assert advice.why != "not in the curio library", advice.curio_id


if __name__ == "__main__":
    import pytest as _p
    raise SystemExit(_p.main([__file__, "-v"]))
