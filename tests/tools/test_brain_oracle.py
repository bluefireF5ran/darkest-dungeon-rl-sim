"""The monster AI's weighting, graded against the real game.

Sibling of `test_hit_oracle.py`. `Darkest.exe` logs how its own brain weighted
each decision, one block per desire, in the brain file's order, each printing
the running total -- so the DELTA between consecutive blocks is exactly what
one desire contributed:

    MonsterBrain: Select Skill:
    valid skills: cultist_pull, cultist_push, cultist_incantation
    Desire: specific_skill         Skill chances: 0, 1, 0
    Desire: specific_skill         Skill chances: 1, 1, 0
    Desire: specific_skill         Skill chances: 1, 1, 2
    Desire: performing_turn_skill  Skill chances: 1, 1, 7

That is the Cultist Witch, and it is `cultist_witch_B`'s brain read straight
back out of the running game: +1 push, +1 pull, +2 incantation, then +5
incantation because it is her first action of the turn.

29 skill blocks (17 of them trivial -- one usable skill, and the game prints
no desire at all), 12 gradeable, **46 of 46 desire contributions match**.
47 target blocks, of which the state-free `random_target` desires all match.

A desire that carries a gate is reported, not scored: the trace does not
record who was marked or stunned, so a zero contribution there is equally
consistent with the gate having been closed at the time.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests._fixtures import get_store

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
from brain_oracle import (FIXTURE, grade, grade_targets, identify,  # noqa: E402
                          parse_targets, parse_trace)

TRACES = Path(__file__).resolve().parents[2] / "archive" / "traces"


@pytest.fixture(scope="module")
def store():
    return get_store()


@pytest.fixture(scope="module")
def corpus():
    assert FIXTURE.exists(), "archive/traces/brain_blocks.json is missing"
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_the_engine_weights_a_skill_the_way_the_game_does(store, corpus):
    res = grade(corpus["skills"], store)
    assert res["identified"] >= 12, res["identified"]
    assert res["scored"] >= 40, res["scored"]
    assert not res["mismatch"], res["mismatch"]
    assert res["agree"] == res["scored"]


def test_every_logged_brain_is_one_this_repo_loads(store, corpus):
    """The identification is deliberately strict -- the monster owns every
    skill in the `valid skills` list AND its brain's desire types are the
    logged sequence in the logged order. If a shipped brain ever stops
    matching, the loader has drifted from the file."""
    for b in corpus["skills"]:
        if b.get("trivial"):
            continue
        brain_id, brain = identify(b, store)
        assert brain is not None, b["skills"]


def test_the_witch_block_is_read_back_exactly(store, corpus):
    """One block spelled out, so a refactor that changes the arithmetic
    fails against a number rather than against an aggregate."""
    witch = next(b for b in corpus["skills"]
                 if b["skills"] == ["cultist_pull", "cultist_push",
                                    "cultist_incantation"])
    assert [s["running"] for s in witch["steps"]] == [
        [0.0, 1.0, 0.0], [1.0, 1.0, 0.0], [1.0, 1.0, 2.0], [1.0, 1.0, 7.0]]
    brain_id, brain = identify(witch, store)
    assert brain_id.startswith("cultist_witch")
    assert [d.type for d in brain.skill_desires] == [
        "specific_skill", "specific_skill", "specific_skill",
        "performing_turn_skill"]
    inc = brain.skill_desires[3]
    assert inc.combat_skill_id == "cultist_incantation"
    assert inc.base_chance == 5.0 and inc.performing_turn == 1


def test_a_random_target_desire_weights_every_valid_target_alike(corpus):
    res = grade_targets(corpus["targets"])
    assert res["random_desires"] >= 40, res["random_desires"]
    assert res["agree"] == res["random_desires"]


def test_the_target_epsilon_is_one_ten_thousandth(corpus):
    """The game's target weights carry `base_chance + 1e-4` per desire and
    its skill weights carry nothing. Recorded rather than copied: the epsilon
    only decides anything when every weight is zero, and there
    `SeededRNG.weighted_choice` already falls back to a uniform pick."""
    res = grade_targets(corpus["targets"])
    assert res["epsilon"] == [0.0001], res["epsilon"]
    for b in corpus["skills"]:
        for step in b["steps"]:
            for v in step["running"]:
                assert v == round(v, 3), (b["source"], step)


@pytest.mark.skipif(not list(TRACES.glob("*.log")),
                    reason="raw captures are gitignored; fixture-only run")
def test_the_fixture_still_matches_the_raw_captures(corpus):
    skills, targets = [], []
    for f in sorted(TRACES.glob("*.log")):
        skills.extend(parse_trace(str(f)))
        targets.extend(parse_targets(str(f)))
    assert len(skills) == len(corpus["skills"])
    assert len(targets) == len(corpus["targets"])
    for a, b in zip(skills, corpus["skills"]):
        assert a == b, (a, b)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
