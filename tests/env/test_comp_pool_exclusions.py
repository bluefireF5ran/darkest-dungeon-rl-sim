"""Which comps `build_comp_pool` drops, and why — because it drops them SILENTLY.

Every exclusion in `build_comp_pool` is a bare `continue`. That is the right
shape for the code and a bad shape for the author of a comp: a file can sit in
`metadata/COMPS/curated_comps/` looking perfectly correct, never train, and
never say so.

It happened on 2026-08-24. Fran added 12 curated comps; **11 reached the pool**
and `Curious Coin: Plague` did not, because its `location` is `"The Farmstead"`
and `zone_id_for_location` returns `None` for it — Color of Madness content with
no enemy pool here. All four of its heroes load fine. It is one zone tag away
from being trainable and nothing anywhere said so.

This file is the missing report. It re-derives each exclusion reason against the
same conditions `build_comp_pool` applies and asserts the totals, so a NEW
silent drop fails a test instead of quietly shrinking the training set — which
is the same failure mode as an eval whose comp count moved underneath it.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests._fixtures import get_store

from env.expedition_env import (PINNED_COMP_NAMES, build_comp_pool,
                                zone_id_for_location)

store = get_store()


def _exclusions():
    """`{reason: [comp names]}`, mirroring `build_comp_pool`'s order."""
    out = {"pinned": [], "unloadable_class": [], "no_zone": [], "under_two_heroes": []}
    for name, loadout in sorted(store.comp_loadouts.items()):
        if name in PINNED_COMP_NAMES or loadout.alias in PINNED_COMP_NAMES:
            out["pinned"].append(name)
            continue
        if len(loadout.heroes) < loadout.declared_heroes:
            out["unloadable_class"].append(name)
            continue
        if zone_id_for_location(loadout.location) is None:
            out["no_zone"].append(f"{name} ({loadout.location!r})")
            continue
        if len([h for h in loadout.heroes if h.hero_id in store.heroes]) < 2:
            out["under_two_heroes"].append(name)
            continue
    return out


def test_the_exclusions_account_for_every_comp():
    """The arithmetic has to close, or this report is describing a different
    filter from the one that runs."""
    excluded = _exclusions()
    n_excluded = sum(len(v) for v in excluded.values())
    pool = build_comp_pool(store)
    assert len(pool) + n_excluded == len(store.comp_loadouts), (
        f"{len(pool)} pooled + {n_excluded} excluded != "
        f"{len(store.comp_loadouts)} on disk — this mirror has drifted from "
        "build_comp_pool")
    print(f"  PASS test_the_exclusions_account_for_every_comp "
          f"({len(pool)} pooled, {n_excluded} excluded)")


def test_the_tutorial_party_is_the_only_pinned_comp():
    """`PINNED_COMP_NAMES` keeps The Old Road out of the sampled pool. With the
    CSV retired that exclusion has to be explicit, or it leaks into training and
    quietly corrupts `tools/eval_tutorial.py`."""
    pinned = _exclusions()["pinned"]
    assert len(pinned) == 1, pinned
    print(f"  PASS test_the_tutorial_party_is_the_only_pinned_comp ({pinned[0]})")


def test_every_zone_drop_is_a_region_with_no_enemy_pool():
    """A comp dropped for its zone tag is the silent case that actually bit.
    Each one must be a place this simulator genuinely has no encounters for —
    if a real region ever shows up here, `zone_id_for_location` has a gap and
    real comps are being thrown away."""
    dropped = _exclusions()["no_zone"]
    # + Hamlet and Darkest Dungeon III/IV on 2026-09-19: the team-builder
    # export added one comp in each. None is a raidable region with an
    # enemy pool, so being dropped is correct -- but it has to be a
    # DECLARED drop, which is what this test is for.
    known_unsupported = ("Farmstead", "Darkest Dungeon II", "Courtyard",
                         "Hamlet", "Darkest Dungeon III",
                         "Darkest Dungeon IV")
    for entry in dropped:
        assert any(k in entry for k in known_unsupported), (
            f"{entry} was dropped for its zone, but that is not a region known "
            "to lack an enemy pool — either zone_id_for_location has a gap or "
            "this list needs updating")
    print(f"  PASS test_every_zone_drop_is_a_region_with_no_enemy_pool "
          f"({len(dropped)} dropped: {dropped})")


def test_no_comp_is_dropped_for_being_under_strength():
    """A comp that loses a slot to an unloadable class trains as a smaller
    party, which is why it is excluded rather than fielded. If this list grows,
    a hero class stopped loading."""
    ex = _exclusions()
    assert not ex["under_two_heroes"], ex["under_two_heroes"]
    print(f"  PASS test_no_comp_is_dropped_for_being_under_strength "
          f"({len(ex['unloadable_class'])} dropped for an unloadable class)")


def test_the_pool_size_is_what_tonight_s_evals_measured():
    """Every number recorded on 2026-08-24 — the all-comps eval, the boss eval,
    the paired ladders — ran over this many comps. If it moves, those numbers
    are describing a different population and must not be diffed against new
    ones without saying so."""
    pool = build_comp_pool(store)
    # 152 -> 158 on 2026-08-26: Fran re-exported the folder (100 comps added,
    # 94 deleted). Bumped deliberately, which is what this tripwire asks for --
    # and it means no eval taken before that date is comparable to one after.
    # 158 -> 163: five hand-upgrades of GA output promoted out of GA_comps/
    # across 2026-08-26/27, including a four-Runaway comp whose two signature
    # skills this engine does not implement -- see ASSUMPTIONS.md.
    # 163 -> 165 on 2026-08-27: Fran added four Curious Coin / Money comps
    # (Money Quartet, Curious Coin: Sand, : Sand & Holy Lance, : Twin Money &
    # Sand), two of which replaced existing files. Bumped deliberately -- the
    # prevention A/B measured at 163 is NOT comparable across this.
    # 166 -> 169 on 2026-08-28: Fran promoted three hand-upgrades of this
    # batch's GA output into curated_comps -- 'Waiting Blade: Twin Contract',
    # 'Grand Finale: Twin Cross' and 'Rabid Devotion: Sand'. The fourth,
    # 'Torchless: Sibyl', fields a modded class and is therefore in the pool
    # only under DDSIM_MOD_DIRS, which this suite does not set.
    # 169 -> 172 on 2026-08-30: Fran hand-optimised the four proposals of the
    # closing Warrens batch and promoted them -- 'Waiting Blade: Twin Duel',
    # 'Curious Coin: Twin Sand', 'Ember Trail: Twin Burn' and 'Grand Finale:
    # Sibyl'. Only THREE reach this pool: the Sibyl one fields a modded class
    # and needs DDSIM_MOD_DIRS, which this suite deliberately does not set.
    # Three of the four replicate as improvements on two checkpoints (+11.0,
    # +7.1, +3.1 mean); the Sibyl one does not -- see CHANGELOG.md.
    # 172 -> 175 on 2026-08-30: Fran reviewed the CLOSING COVE BATCH and
    # promoted three of it -- 'Grand Finale: Twin Warlock', 'Open Season:
    # Twin Cross' and 'Waiting Blade: Beast', all tagged The Cove. That is
    # the review loop closing rather than a search result: the batch was
    # emitted into GA_comps/ and only his judgement moved these three, which
    # is why promotion is still a `mv` and never a score gate. It also bites
    # into the pool's crypts skew, the one thing a Cove batch was for.
    # 176 -> 446 on 2026-09-19: `tools/sync_preset_comps.py` replaced
    # `curated_comps/` with the team-builder export, 178 files -> 477.
    # A DELIBERATE bump -- no measurement taken before it compares with
    # one taken after, and no checkpoint trained on the old pool is
    # comparable on the new one.
    # 175 -> 176 on 2026-09-02: Fran's own edit, net +1 across three files --
    # 'Ember Trail: Cross' and 'Torchless: Bulwark' added, 'Torchless: Lunge'
    # deleted. The Cross one arrived with only TWO skills on its Plague Doctor
    # and `test_every_restricted_hero_in_every_curated_comp_has_four_skills`
    # caught it before this tripwire did; he supplied the missing two. Both
    # nets firing on one file, one on its CONTENT and one on the COUNT, is the
    # pair working as designed.
    assert len(pool) == 446, (
        f"pool is {len(pool)}, was 175; bump this "
        "deliberately and re-measure rather than comparing across it")
    print("  PASS test_the_pool_size_is_what_tonight_s_evals_measured (446)")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
