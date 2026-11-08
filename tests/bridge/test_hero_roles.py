"""The hero model the Hamlet reads: level bands, roles, nurture score.

Fran, 2026-09-26 (docs/NEXT.md, "the Hamlet, nuanced").
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from bridge import hero_roles as R
from bridge.live_state import LiveHero
from bridge.town_state import RosterHero, TownState


def rh(guid, cls="leper", level=0, quirks=(), weapon=0, stress=0.0):
    return RosterHero(hero=LiveHero(guid=guid, hero_class=cls, resolve_level=level,
                                    quirks=list(quirks), weapon_rank=weapon,
                                    stress=stress))


def test_bands_are_fran_s():
    assert [R.band_of(n) for n in range(7)] == \
        ["0-2", "0-2", "0-2", "3-4", "3-4", "5+", "5+"]


def test_the_low_band_cap_tightens_as_the_roster_levels():
    assert R.band_caps(1.0) == {"0-2": 4, "3-4": 4, "5+": 5}
    assert R.band_caps(3.0)["0-2"] == 3
    assert R.band_caps(4.5)["0-2"] == 2
    assert R.band_caps(9.0)["0-2"] == R.MIN_LOW_CAP


def test_a_quirk_counts_more_for_a_class_whose_comps_want_it():
    share = {"arbalest": {"corvids_eye": 0.9}, "leper": {}}
    assert R.nurture_score("arbalest", ["corvids_eye"], share) > \
        R.nurture_score("leper", ["corvids_eye"], share) > 0


def test_negatives_count_only_when_the_store_says_so():
    share = {}
    assert R.nurture_score("leper", ["unknown_thing"], share) == 0
    assert R.nurture_score("leper", ["unknown_thing"], share,
                           negative=lambda q: True) < 0


def test_the_curated_comps_are_read_by_quirk_id():
    share = R.class_quirk_share()
    assert share, "no curated comp carried a quirk"
    assert share.get("arbalest", {}).get("corvids_eye", 0) > 0.3


def test_roles_level_one_on_xp_alone_is_fodder_mortmain():
    """Fran's worked case: level 1, nothing bought, no great quirk for her
    class -- she has "already done her role"."""
    assert R.role_of(1, 0, 2.0) == "fodder"
    assert R.role_of(1, 0, 2.0, gear=2) == "invested"     # Experienced Recruit
    assert R.role_of(0, 6250, 0.0) == "invested"
    assert R.role_of(3, 0, 0.0) == "invested"
    assert R.role_of(0, 0, R.NURTURE_MIN) == "nurture"


def test_roles_read_the_roster_without_a_profile():
    town = TownState(heroes=[rh(1, level=0), rh(2, level=1, weapon=1),
                             rh(3, level=4)])
    got = {g: r.role for g, r in R.roles(town).items()}
    assert got == {1: "fodder", 2: "invested", 3: "invested"}
    assert R.mean_level(town) == 5 / 3
