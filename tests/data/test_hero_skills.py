"""Reading a hero's unlocked skills out of `persist.upgrades.json`.

The reading is: hashed tree id, `instance_number` is the hero's guid, presence
is the purchase, count is the rank. Three of those four are established by
`data/heirlooms.py` and pinned by `tests/data/test_heirlooms.py`; the guid is this
module's claim, and it is the one that makes a comp's affordability a save
question instead of a screen question.

Most of these drive a **synthetic tally** rather than a save, because there is
no upgrades file to bank: the game's own `scripts/starting_save/` copy holds
zero purchases, and Fran's live profile changes every time the bridge buys
something. What the live profile is used for is the one invariant that cannot
move -- three camp trees per hero, whatever else is bought.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest

from data import hero_skills as HS
from data.town_costs import hero_upgrades_path, load_hero_costs

from data.paths import GAME_DIR

pytestmark = pytest.mark.skipif(not GAME_DIR.exists(),
                                reason="needs the game install")

GD = str(GAME_DIR)

# Every class the shipped install can price, DLC included.
ALL_CLASSES = ("crusader", "highwayman", "vestal", "plague_doctor", "occultist",
               "man_at_arms", "leper", "jester", "antiquarian", "hellion",
               "arbalest", "bounty_hunter", "grave_robber", "houndmaster",
               "abomination", "musketeer", "flagellant", "shieldbreaker",
               "duelist", "runaway")


def _tally(pairs):
    """`{(tree hash, guid): ranks}` from `[(class, skill, guid, ranks)]`."""
    from data.dson import dd_name_hash

    return {(dd_name_hash(f"{cls}.{tree}") & 0xFFFFFFFF, guid): ranks
            for cls, tree, guid, ranks in pairs}


@pytest.fixture
def fake_counts(monkeypatch):
    def install(pairs):
        monkeypatch.setattr(HS, "rank_counts", lambda _profile: _tally(pairs))
    return install


def test_every_shipped_class_has_a_priceable_upgrade_tree():
    """The five DLC classes live under `dlc/**`, not `upgrades/heroes/`.

    Reading only the base path returned `None` for them, which made a Duelist
    read as a hero with no skills and nothing to buy -- silently, because
    `unlocked` returns `{}` for a class it cannot price.
    """
    for cls in ALL_CLASSES:
        costs = load_hero_costs(GD, cls)
        assert costs is not None, cls
        assert len(costs.skills) == 7, cls
        path = hero_upgrades_path(GD, cls)
        assert "modes" not in path.parts, cls


def test_a_skill_unlock_is_1000_gold_in_every_shipped_tree():
    """Rank 1 is the unlock, and it is what the embark budget is spent on.

    Flat across all twenty classes and across the campaign modes -- only
    weapon and armour prices differ per mode -- which is why
    `hero_upgrades_path` may pick one file and ignore the three variants.
    """
    for cls in ALL_CLASSES:
        for skill, ranks in load_hero_costs(GD, cls).skills.items():
            assert ranks[0] == 1000, (cls, skill)


def test_the_instance_is_the_guid_so_two_heroes_do_not_share_a_tally(fake_counts):
    fake_counts([("crusader", "smite", 11, 1),
                 ("crusader", "stunning_blow", 12, 1)])
    assert HS.unlocked(None, ("crusader", 11), GD) == {"smite": 1}
    assert HS.unlocked(None, ("crusader", 12), GD) == {"stunning_blow": 1}


def test_the_count_is_the_rank_and_gear_is_kept_apart(fake_counts):
    fake_counts([("crusader", "smite", 11, 5),
                 ("crusader", "weapon", 11, 2),
                 ("crusader", "armour", 11, 1)])
    assert HS.unlocked(None, ("crusader", 11), GD) == {"smite": 5}
    assert HS.gear_ranks(None, ("crusader", 11), GD) == {"weapon": 2, "armour": 1}


def test_an_unrecognised_hash_is_reported_rather_than_discarded(fake_counts):
    """A camp tree cannot be named here -- it lives outside `upgrades/`. If it
    were dropped instead, a tree this module fails to name would look exactly
    like a hero who owns nothing, and `unlocked` would understate silently."""
    fake_counts([("crusader", "smite", 11, 1),
                 ("crusader", "zealous_speech", 11, 1)])
    assert HS.unlocked(None, ("crusader", 11), GD) == {"smite": 1}
    assert len(HS.unaccounted(None, ("crusader", 11), GD)) == 1


def test_missing_names_only_what_the_guild_can_actually_sell(fake_counts):
    fake_counts([("crusader", "smite", 11, 1)])
    hero = ("crusader", 11)
    wanted = ("smite", "stunning_blow", "battle_heal")
    assert HS.missing(None, hero, wanted, GD) == ("stunning_blow", "battle_heal")
    assert HS.unlock_cost(None, hero, wanted, GD) == 2000
    # A skill this class cannot learn is a data problem, not a purchase.
    assert HS.missing(None, hero, ("harvest",), GD) == ()
    assert HS.unknown(hero, ("harvest", "smite"), GD) == ("harvest",)


def test_an_unrestricted_class_needs_no_special_case(fake_counts):
    """The Abomination cannot select a 4-of-7, so the save already carries all
    seven trees and `missing` comes back empty on its own. Nothing in
    `data/hero_skills.py` tests for identity, which is what makes this hold for
    a modded stance class too."""
    seven = tuple(load_hero_costs(GD, "abomination").skills)
    fake_counts([("abomination", s, 31, 1) for s in seven])
    assert HS.missing(None, ("abomination", 31), seven, GD) == ()
    assert HS.unlock_cost(None, ("abomination", 31), seven, GD) == 0


def test_a_class_this_install_cannot_price_returns_None_not_zero(fake_counts):
    """`unlocked` returning `{}` for a modded hero must not read as "free"."""
    fake_counts([])
    assert HS.unlocked(None, ("sibyl_ms", 40), GD) == {}
    assert HS.unlock_cost(None, ("sibyl_ms", 40), ("moonlight",), GD) is None


def test_a_missing_upgrades_file_reads_as_nothing_bought(tmp_path):
    assert HS.rank_counts(tmp_path) == {}
    assert HS.unlocked(tmp_path, ("crusader", 1), GD) == {}


def test_the_tally_is_reread_when_the_save_changes(tmp_path):
    """The guild acknowledges a purchase by re-reading this file, so a plain
    `lru_cache` would serve the pre-purchase answer for the rest of the run."""
    path = tmp_path / "persist.upgrades.json"
    path.write_bytes(b"not dson")
    assert HS.rank_counts(tmp_path) == {}
    path.write_bytes(b"still not dson, but a different size")
    assert HS.rank_counts(tmp_path) == {}       # different key, same answer


def test_the_live_save_leaves_exactly_the_camp_skills_unnamed():
    """The invariant that says the reading still holds, on a save that moves.

    The unnamed hashes are the hero's camp trees, which live outside
    `upgrades/`, so the count is the hero's own `selected_camping_skills`.
    Three for nearly every hero -- and `flagellant#60` in Fran's Week 22 save
    carries two and leaves two, which is what turned a hardcoded three into
    the rule that is actually true.

    How many COMBAT skills a hero owns changes every time the Guild is
    visited; this does not.
    """
    from bridge.live_state import find_profile
    from bridge.town_state import read_town_state

    try:
        profile = find_profile()
    except Exception:                                      # noqa: BLE001
        pytest.skip("no live profile on this machine")
    if not (profile / "persist.upgrades.json").is_file():
        pytest.skip("the live profile has no upgrades file yet")

    town = read_town_state(profile)
    if not town.heroes:
        pytest.skip("empty roster")
    for hero in town.heroes:
        if load_hero_costs(GD, hero.hero_class) is None:
            continue                                       # a modded class
        # **A FRESH save NAMES its camp trees; an aged one does not.** The
        # invariant this test was written against came from Fran's Week-22
        # profile, where the camp trees are the leftover unnamed hashes. A
        # campaign created minutes ago spells them out -- `crusader.encourage`
        # sits in `purchases` beside `crusader.smite` -- so nothing is left
        # over and the count is 0 rather than 3.
        #
        # Found the moment the bridge started making its own saves, which is
        # now the common case rather than an oddity. Both readings are the
        # game's, so the assertion is the weaker true one: the leftovers are
        # camp trees or there are none, never some third thing.
        leftover = len(HS.unaccounted(profile, hero, GD))
        assert leftover in (0, len(hero.hero.camping_skills)), (
            f"{hero.label()}: {leftover} unnamed tree(s) against "
            f"{len(hero.hero.camping_skills)} camp skills")
        # Every named tree belongs to this hero's own class, which is the
        # other half of "the instance is the guid".
        owned = set(load_hero_costs(GD, hero.hero_class).skills)
        assert set(HS.unlocked(profile, hero, GD)) <= owned, hero.label()
        # Whatever they own already costs nothing to own.
        have = tuple(HS.unlocked(profile, hero, GD))
        assert HS.unlock_cost(profile, hero, have, GD) == 0, hero.label()
