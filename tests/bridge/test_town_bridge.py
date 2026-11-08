"""Tests for the Hamlet reader and the embark advisor (`bridge/town_state.py`,
`bridge/embark.py`).

These pin the save-format facts that are easy to get wrong and impossible to
notice when they are — a hero silently counted as available while he is in the
Abbey, a resolve cap read off by one so the tool suggests a party the game will
refuse, or HP read from a town save where it is stale.

Roster fixtures come from the captured snapshots in `archive/traces/`, which are
real save files. The quest and estate files are not captured (they only matter
outside a raid, which is when nothing is capturing), so those paths are covered
through their pure functions and against the game's own data instead.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest

from bridge.embark import combats_for, fill_shape, _to_member
from bridge.rollout import infer_zone_and_tier
from bridge.town_state import (FALLBACK_RESOLVE_CAPS, Quest, RosterHero,
                               TownState, _read_roster, load_resolve_caps)
from bridge.live_state import load_resolve_thresholds
from env import ExpeditionSequenceEnv
from env.expedition_sequence_env import EXPEDITION_LENGTH_BANDS

REPO = Path(__file__).resolve().parents[2]
ROSTERS = sorted((REPO / "archive" / "traces").glob("raidsnaps*/*persist.roster.json"))


@pytest.fixture(scope="module")
def store():
    return ExpeditionSequenceEnv().store


@pytest.fixture(scope="module")
def roster():
    if not ROSTERS:
        pytest.skip("no captured roster snapshot")
    return _read_roster(ROSTERS[-1], load_resolve_thresholds())


def _hero(hero_class, level=2, stress=0.0, building="", guid=1):
    """A RosterHero without a save behind it, for the pure selection logic."""
    from bridge.live_state import LiveHero
    return RosterHero(hero=LiveHero(guid=guid, hero_class=hero_class,
                                    resolve_level=level, stress=stress),
                      building=building)


# ── the resolve cap the game itself enforces ──

def test_resolve_caps_come_from_the_games_own_restriction_table():
    """`campaign/quest/quest.restriction.json` — Apprentice caps at 2, Veteran
    at 4, Champion is unrestricted. Getting this wrong means suggesting a party
    the game will simply refuse to send."""
    caps = load_resolve_caps()
    assert caps[1] == 2
    assert caps[3] == 4
    assert caps[5] >= 99


def test_eligible_excludes_over_cap_and_busy_heroes():
    town = TownState(
        heroes=[_hero("crusader", level=2, guid=1),
                _hero("vestal", level=5, guid=2),           # over the cap
                _hero("hellion", level=1, building="abbey", guid=3)],
        resolve_caps=list(FALLBACK_RESOLVE_CAPS))
    quest = Quest(key="0", quest_id="generated_0", dungeon="weald",
                  quest_type="explore", difficulty=1, length=1)
    assert [h.guid for h in town.eligible(quest)] == [1]


def test_a_veteran_quest_admits_the_heroes_an_apprentice_one_bars():
    town = TownState(heroes=[_hero("vestal", level=4, guid=2)],
                     resolve_caps=list(FALLBACK_RESOLVE_CAPS))
    apprentice = Quest(key="0", quest_id="g", dungeon="weald",
                       quest_type="explore", difficulty=1, length=1)
    veteran = Quest(key="1", quest_id="g", dungeon="weald",
                    quest_type="explore", difficulty=3, length=1)
    assert town.eligible(apprentice) == []
    assert len(town.eligible(veteran)) == 1


# ── availability is a field on the hero, not a search of the buildings ──

def test_a_hero_in_a_building_is_not_available():
    assert not _hero("crusader", building="abbey").available
    assert _hero("crusader").available


def test_real_roster_parses_with_classes_and_levels(roster):
    assert len(roster) >= 4
    assert all(h.hero_class for h in roster)
    assert all(0 <= h.resolve_level <= 6 for h in roster)


# ── quest identity ──

def test_only_the_four_core_zones_are_simulable():
    def q(dungeon, difficulty=1):
        return Quest(key="0", quest_id="generated_0", dungeon=dungeon,
                     quest_type="explore", difficulty=difficulty, length=1)
    assert q("weald").simulable
    assert q("crypts").simulable
    # darkestdungeon has no tiers 1-5, only scripted tier-6 content, so it must
    # not be simulated against a bracket that does not exist for it.
    assert not q("darkestdungeon", difficulty=6).simulable
    assert not q("courtyard").simulable


def test_generated_quests_are_told_apart_from_plot_ones():
    gen = Quest(key="0", quest_id="generated_2", dungeon="cove",
                quest_type="cleanse", difficulty=1, length=1)
    plot = Quest(key="1", quest_id="plot_darkest_dungeon_1", dungeon="darkestdungeon",
                 quest_type="kill_boss", difficulty=6, length=2)
    assert gen.is_generated and not plot.is_generated


def test_quest_length_picks_a_real_band_and_a_count_inside_it():
    for length in (1, 2, 3, 4):
        q = Quest(key="0", quest_id="generated_0", dungeon="weald",
                  quest_type="explore", difficulty=1, length=length)
        band, combats = combats_for(q)
        lo, hi = EXPEDITION_LENGTH_BANDS[band]
        assert lo <= combats <= hi


# ── filling a comp's shape from a real roster ──

def test_a_duplicate_class_shape_takes_two_different_heroes():
    pool = [_hero("highwayman", stress=10, guid=1),
            _hero("highwayman", stress=40, guid=2),
            _hero("vestal", guid=3)]
    filled = fill_shape(["highwayman", "highwayman", "vestal"], pool)
    assert [h.guid for h in filled] == [1, 2, 3]


def test_lowest_stress_is_picked_first():
    pool = [_hero("crusader", stress=80, guid=1), _hero("crusader", stress=5, guid=2)]
    assert fill_shape(["crusader"], pool)[0].guid == 2


def test_an_unfieldable_shape_returns_none_rather_than_a_short_party():
    pool = [_hero("crusader", guid=1)]
    assert fill_shape(["crusader", "vestal"], pool) is None


def test_town_heroes_embark_at_full_hp():
    """The game restores HP on returning to the Hamlet, and the save's
    `current_hp` in town is stale from the last raid (1.0 for anyone never
    deployed). Stress is the opposite and must survive."""
    member = _to_member(_hero("crusader", stress=37.0))
    assert member.hp is None
    assert member.stress == 37


# ── which bracket a live fight came from ──

def test_zone_and_tier_are_recovered_from_the_enemy_line(store):
    entry = next(e for e in store.enemy_comps
                 if e.zone_id == "weald" and e.tier == 1 and len(e.enemy_ids) >= 2)
    assert infer_zone_and_tier(store, entry.enemy_ids) == ("weald", 1)


def test_a_partial_enemy_line_still_identifies_the_bracket(store):
    """Real fights hold corpses, reinforcements and the already-dead, so an
    exact match on the encounter table would almost never fire."""
    entry = next(e for e in store.enemy_comps
                 if e.zone_id == "cove" and len(e.enemy_ids) >= 3)
    got = infer_zone_and_tier(store, entry.enemy_ids[:2])
    assert got is not None and got[0] == "cove"


def test_no_enemies_means_no_inference(store):
    assert infer_zone_and_tier(store, []) is None
    assert infer_zone_and_tier(store, ["not_a_monster"]) is None

def test_a_dead_hero_is_not_on_the_roster():
    """`roster.status` 3 is a hero who died; the file keeps them. Captured
    2026-09-23 from profile_0 with eight dead: 43 records, 35 alive -- and 35
    is exactly what the estate-map list drew."""
    snap = REPO / "archive" / "traces" / "roster_dead_20260923" / "persist.roster.json"
    if not snap.exists():
        pytest.skip("no captured roster with dead heroes")
    heroes = _read_roster(snap, load_resolve_thresholds())
    guids = {h.guid for h in heroes}
    assert len(heroes) == 35
    assert not guids & {254, 255, 256, 260}

