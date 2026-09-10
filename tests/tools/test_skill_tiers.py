"""The expert tier list resolves onto real skills, and stays a RULER.

Fran supplied it on 2026-08-28 as a judge of whether the model plays well, with
the same standing condition `tools/check_class_play.py` carries: *"this is not
to bias the model, rather as a judge to evaluate if a model is playing good"*.

Two things worth a test:

**It resolves completely, or it silently measures less than it claims.** The
list is keyed by DISPLAY name and the game's display names have bitten this repo
repeatedly -- "First Aid" against "Wound Care" for one `first_aid`, a trinket
name colliding with a localization-only id, `Sibyl` against `sibyl_ms`. A tier
entry that fails to resolve does not error; it just quietly drops out of every
rate computed from it. All 119 must land.

**Its coverage is a known 17 of 20 classes.** Musketeer, Duelist and Runaway are
absent from the source by the author's choice, and Musketeer is deliberately not
folded into Arbalest despite the shared icons. A caller that treated missing as
"average" would flatter or punish exactly those three, so the index must return
nothing for them rather than a guess.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from env.gym_env import GAME_DIR
from tests._fixtures import get_store
from tools.skill_tiers import (SKILL_TIERS_JS, TIER_ORDER, TIER_RANK, coverage,
                               load_tiers, tier_index)

store = get_store()

needs_list = pytest.mark.skipif(
    not SKILL_TIERS_JS.exists(),
    reason="the team-builder tier list is not on this machine")

# The source states its own size in its header: 17 classes x 7 skills.
EXPECTED_ENTRIES = 119
EXPECTED_UNTIERED = ["duelist", "musketeer", "runaway"]


@needs_list
def test_the_list_parses_into_seventeen_classes_of_seven():
    tiers = load_tiers()
    assert tiers, "the tier list parsed to nothing"
    assert len(tiers) == 17, sorted(tiers)
    total = sum(len(v) for v in tiers.values())
    assert total == EXPECTED_ENTRIES, total
    for cls, entries in tiers.items():
        assert len(entries) == 7, (cls, len(entries))
        assert set(entries.values()) <= set(TIER_ORDER), (cls, entries)


@needs_list
def test_every_tiered_skill_resolves_to_a_real_skill_id():
    """The silent one. An unresolved display name drops out of every rate
    computed from this list without any error."""
    index = tier_index(store, GAME_DIR)
    assert len(index) == EXPECTED_ENTRIES, (
        f"{len(index)} of {EXPECTED_ENTRIES} tier entries resolved -- a display "
        "name stopped matching and the ruler is now measuring less than it says")
    for (hero_id, skill_id), tier in index.items():
        hero = store.heroes.get(hero_id)
        assert hero is not None, hero_id
        assert any(s.id == skill_id for s in hero.skills), (hero_id, skill_id)
        assert tier in TIER_RANK


@needs_list
def test_the_three_uncovered_classes_return_nothing_rather_than_a_guess():
    index = tier_index(store, GAME_DIR)
    _tiered, _total, untiered = coverage(store, index)
    for hero_id in EXPECTED_UNTIERED:
        if hero_id in store.heroes:
            assert hero_id in untiered, (
                f"{hero_id} acquired tiers -- the source says it has none, so "
                "something is guessing")
    for hero_id, _skill in index:
        assert hero_id not in EXPECTED_UNTIERED, hero_id


@needs_list
def test_coverage_is_85_percent_of_the_roster_and_says_so():
    """A rate over 85% of the roster means something different from a rate over
    all of it, so the number is asserted rather than assumed."""
    index = tier_index(store, GAME_DIR)
    tiered, total, _untiered = coverage(store, index)
    assert tiered == EXPECTED_ENTRIES
    # 20 vanilla classes x 7 skills; the three uncovered ones are 21 of them.
    assert total >= 140, total
    assert 0.80 <= tiered / total <= 0.90, tiered / total


@needs_list
def test_the_transcript_quotes_land_on_the_right_skill():
    """The expert's REASONING, indexed. Speech-to-text mangles the names, so
    this is a best-effort locator over 42 KB of prose -- and it is built to
    refuse rather than guess, because a quote attached to the wrong skill would
    appear next to a claim about the model.

    Coverage is asserted as a band, not a number: the matcher is fuzzy and the
    exact count will move if the transcript is re-transcribed.
    """
    from tools.skill_tiers import TIER_TRANSCRIPT, explanations
    if not TIER_TRANSCRIPT.exists():
        pytest.skip("the transcript is not on this machine")
    notes = explanations(store, GAME_DIR)
    index = tier_index(store, GAME_DIR)
    assert notes, "no passage located at all"
    assert 0.6 <= len(notes) / len(index) <= 1.0, len(notes) / len(index)
    for key in notes:
        assert key in index, key
        assert len(notes[key]) > 40

    # The one that was wrong before the occurrence scoring went in: Open Vein's
    # first "vein" lands inside a Suppressing Fire explanation.
    vein = notes.get(("highwayman", "opened_vein"))
    if vein:
        assert "open vein" in vein.lower(), vein[:120]


def test_a_missing_file_degrades_instead_of_raising():
    """Callers must keep working on a machine without Fran's other project."""
    assert load_tiers(str(Path(__file__).with_name("does_not_exist.js"))) == {}


def test_nothing_in_engine_env_or_train_imports_the_tier_list():
    """THE RULER RULE, enforced statically.

    `check_class_play.py` carries the same condition and states it in prose;
    this is the version a future edit trips over. A tier list reaching the
    reward, the action mask or the observation would be fitting the policy to
    one person's opinion of seventeen classes -- and it covers no modded class
    at all, so it would transfer to the Sibyl as nothing.
    """
    root = Path(__file__).resolve().parents[2]
    offenders = []
    for sub in ("engine", "env", "data", "bridge"):
        for path in (root / sub).glob("**/*.py"):
            if "skill_tiers" in path.read_text(encoding="utf-8"):
                offenders.append(str(path.relative_to(root)))
    for name in ("train.py",):
        if "skill_tiers" in (root / name).read_text(encoding="utf-8"):
            offenders.append(name)
    assert not offenders, (
        f"the expert tier list is imported by {offenders}. It is a RULER: it may "
        "be read by tools/ to judge play, and must never reach training, the "
        "reward, the action mask or the observation.")


if __name__ == "__main__":
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            print(_name)
            _fn()
