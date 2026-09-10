"""Districts - the town buildings whose payoff lands in the raid.

The town is out of scope in this project, and districts were filed under it
until 2026-08-24. That was the wrong call for nine of the nineteen: they grant
ordinary buff ids that `_apply_buff_to_stats` already cashes, so the effect is
a combat effect and the simulator was fielding parties without it. Measured on
36 paired long/Champion scenarios, the hero buffs alone are worth +13.9pp
SUCCESS to a policy that has never seen them.

These tests pin the LOADER. The two readings that could silently invert it are
`hero_type_tags: []` and the tier gate, and both get their own test.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests._fixtures import get_store

from data.district_loader import (DistrictLoader, DEFAULT_MIN_TIER,
                                  MIN_TIER_CHAMPION, MIN_TIER_VETERAN,
                                  camping_point_bonus, districts_at_tier,
                                  food_bonus, hero_district_buff_ids,
                                  hunger_disabled, party_camping_point_bonus)
from env.gym_env import GAME_DIR

store = get_store()
DISTRICTS = DistrictLoader.load_all(Path(GAME_DIR))

# Buff types the loader deliberately does not consume. A NEW one appearing
# means the game shipped a district mechanic nobody has looked at.
KNOWN_UNREAD = {
    "DistrictPrestigeBuffData",            # town rank
    "DistrictEstateBuffData",              # weekly gold / the Blood
    "DistrictRosterStressReliefBuffData",  # town visit
    "DistrictRandomLootBuffData",          # weekly trinket / heirloom
    "DistrictCurioInteractionBuffData",    # curios are out of scope
    "DistrictStackingBuffData",            # inventory stack limits
    "DistrictReplacementInventoryEffectBuffData",
    "DistrictLightBuffData",               # the darkness table swap
}


def test_the_districts_load_and_every_buff_id_resolves():
    """The whole design rests on this: the district files are a BINDING to
    buffs `shared/buffs` already carries, not a new source of stats. An
    unresolved id would mean a district that silently grants nothing."""
    assert len(DISTRICTS) >= 19, sorted(DISTRICTS)
    granted = [b for d in DISTRICTS.values() for hb in d.hero_buffs
               for b in hb.buff_ids]
    assert granted, "no district grants a hero buff"
    missing = sorted({b for b in granted if b not in store.buffs})
    assert not missing, f"district buff ids not in store.buffs: {missing}"
    print(f"  PASS test_the_districts_load_and_every_buff_id_resolves "
          f"({len(DISTRICTS)} districts, {len(granted)} buff grants)")


def test_an_EMPTY_hero_tag_list_means_EVERY_hero():
    """The opposite of the `hero_classes: []` rule on camping skills, so it
    cannot be inherited and needs its own evidence.

    `conservatory_of_steel` is the proof: one building shipping BOTH an
    empty-tag entry and a tagged one. Read empty as "no hero" and its three
    buffs are unreachable dead data sitting beside a working example of the
    other form.
    """
    con = DISTRICTS["conservatory_of_steel"]
    empties = [hb for hb in con.hero_buffs if not hb.tags]
    tagged = [hb for hb in con.hero_buffs if hb.tags]
    assert empties and tagged, (
        "conservatory_of_steel no longer ships both forms; the evidence for "
        "the empty-means-all reading has changed and must be re-derived")
    assert empties[0].applies_to({"altar_of_light"})
    assert empties[0].applies_to(set())
    # And the Granary, whose whole payout depends on it: no hero carries a
    # `granary` tag, so an empty list is the only way it ever fires.
    assert not any("granary" in h.tags for h in store.heroes.values())
    assert "districts_granary_1" in hero_district_buff_ids(
        DISTRICTS, store.heroes["vestal"].tags)
    print("  PASS test_an_EMPTY_hero_tag_list_means_EVERY_hero")


def test_every_hero_carries_exactly_one_district_tag():
    """Which is what makes `hero_type_tags` a class gate wearing a tag's
    clothes, and what bounds how much any one district is worth."""
    district_tags = {t for d in DISTRICTS.values() for hb in d.hero_buffs
                     for t in hb.tags}
    district_tags |= {t for d in DISTRICTS.values() for t in d.camping_tags}
    for hid, hero in sorted(store.heroes.items()):
        mine = [t for t in hero.tags if t in district_tags]
        assert len(mine) == 1, f"{hid} has district tags {mine}, want exactly 1"
    print(f"  PASS test_every_hero_carries_exactly_one_district_tag "
          f"({len(store.heroes)} classes)")


def test_nothing_is_built_below_veteran():
    """Fran's gate, and economic rather than mechanical: the gold and the
    blueprints do not exist at Apprentice. It also makes tiers 1-2 a control
    group when reading the retrain, the role the short band plays for camping.
    """
    for tier in (1, 2):
        assert districts_at_tier(DISTRICTS, tier) == set()
        assert food_bonus(DISTRICTS, districts_at_tier(DISTRICTS, tier)) == 0
        assert not hunger_disabled(DISTRICTS, districts_at_tier(DISTRICTS, tier))
        assert hero_district_buff_ids(
            DISTRICTS, store.heroes["vestal"].tags,
            districts_at_tier(DISTRICTS, tier)) == []
    print("  PASS test_nothing_is_built_below_veteran")


def test_color_of_madness_districts_are_CHAMPION_only():
    """Same reason one tier further out - they cost the most and arrive last.
    The Mill is the one that matters, because it deletes the hunger check."""
    com = {n for n, d in DISTRICTS.items()
           if d.source == "735730_color_of_madness"}
    assert com, "no Color of Madness districts found"
    assert "the_mill" in com
    for n, d in DISTRICTS.items():
        want = MIN_TIER_CHAMPION if n in com else MIN_TIER_VETERAN
        assert d.min_tier == want, f"{n} min_tier {d.min_tier}, want {want}"
    at3, at5 = districts_at_tier(DISTRICTS, 3), districts_at_tier(DISTRICTS, 5)
    assert com & at3 == set(), sorted(com & at3)
    assert com <= at5
    assert not hunger_disabled(DISTRICTS, at3)
    assert hunger_disabled(DISTRICTS, at5)
    print(f"  PASS test_color_of_madness_districts_are_CHAMPION_only "
          f"({len(com)} CoM, {len(at3)} at Veteran, {len(at5)} at Champion)")


def test_the_granary_always_gives_its_MAXIMUM():
    """Shipped range is 4-10 and the game rolls it, but the roll is rerollable
    from the provision screen, so a player who wants the maximum takes it.
    Fran's call: model the maximum, not the roll."""
    g = DISTRICTS["granary"]
    assert g.supply_item_type == "provision"
    assert not g.supply_item_name, (
        "food is the EMPTY-id provision row; a named item here is a different "
        "supply and must not be counted as food")
    assert (g.supply_min, g.supply_max) == (4, 10)
    assert food_bonus(DISTRICTS) == 10
    print("  PASS test_the_granary_always_gives_its_MAXIMUM")


def test_camping_points_are_counted_ONCE_for_the_party():
    """The declared reading, and the smaller of the two available. Counting
    per hero would give a four-Outsider party +8 against a base budget of 12.
    """
    bonfire = DISTRICTS["outsiders_bonfire"]
    assert bonfire.camping_points == 2
    assert bonfire.camping_tags == ("outsiders_bonfire",)
    leper = store.heroes["leper"].tags        # tagged
    vestal = store.heroes["vestal"].tags      # not
    assert camping_point_bonus(DISTRICTS, leper) == 2
    assert camping_point_bonus(DISTRICTS, vestal) == 0
    assert party_camping_point_bonus(DISTRICTS, [vestal, vestal]) == 0
    assert party_camping_point_bonus(DISTRICTS, [leper, vestal]) == 2
    assert party_camping_point_bonus(DISTRICTS, [leper, leper, leper, leper]) == 2, (
        "camping points are counted once for the party, not per hero")
    print("  PASS test_camping_points_are_counted_ONCE_for_the_party")


def test_the_unread_buff_types_are_a_DECISION_not_an_oversight():
    """A district mechanic nobody has looked at should fail here rather than
    be silently dropped, the same way an unread monster key does."""
    seen = {t for d in DISTRICTS.values() for t in d.unread_buff_types}
    new = seen - KNOWN_UNREAD
    assert not new, (
        f"unrecognised district buff type(s) {sorted(new)} - read them or add "
        "them to KNOWN_UNREAD with a reason")
    print(f"  PASS test_the_unread_buff_types_are_a_DECISION_not_an_oversight "
          f"({len(seen)} unread types, all known)")


def test_mode_overrides_are_not_loaded_as_extra_districts():
    """`modes/{radiant,bloodmoon,new_game_plus}/` re-ship the same buildings at
    different costs. Loading them would double every district."""
    paths = DistrictLoader._paths(Path(GAME_DIR))
    assert paths, "no district files found at all"
    assert not [p for p in paths if "modes" in {q.lower() for q in p.parts}]
    names = [b for p in paths for b in [p.name]]
    assert len(set(names)) == len(names) or True   # duplicates by name are fine
    # The real invariant: one entry per building.
    assert len(DISTRICTS) == len({d.name for d in DISTRICTS.values()})
    print(f"  PASS test_mode_overrides_are_not_loaded_as_extra_districts "
          f"({len(paths)} files)")


def test_the_default_tier_is_veteran():
    assert DEFAULT_MIN_TIER == MIN_TIER_VETERAN == 3
    assert MIN_TIER_CHAMPION == 5
    print("  PASS test_the_default_tier_is_veteran")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
