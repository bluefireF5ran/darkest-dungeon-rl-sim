"""The to-hit rule, graded against the real game.

Every other check in this suite compares the simulator to itself or to a data
file. This one compares it to `Darkest.exe` actually running: the release
build logs `Chance to hit` and `Roll to hit` for every attack over
`OutputDebugStringA`, unconditionally (`DECOMP.md` F3). `archive/traces/`
holds 186 such pairs, captured for other purposes and distilled into
`archive/traces/hit_pairs.json` because the raw `*.log` files are gitignored.

The outcome is read from the damage band -- a landed attack draws
`Battle Random int range A,B` in the same `bs_turn_presentation` block. That
marker is sound in one direction only: a stress attack or a debuff lands and
draws nothing, so a turn in which NOTHING drew damage is unclassifiable and
is not scored. 155 of 186 pairs are classifiable.

F4 in `DECOMP.md` did this analysis once by hand; this file is the same
evidence as a standing guard. What makes it more than a tautology is
`test_the_corpus_can_tell_the_two_
rules_apart`: 7 classifiable pairs are decided differently by the pre-F4 rule,
and the game sided with F4 on every one of them -- in both directions, so the
old rule is refuted rather than merely unsupported.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests._fixtures import get_store

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
from hit_oracle import FIXTURE, grade, load_corpus, parse_trace  # noqa: E402

from engine import CombatEngine, SeededRNG  # noqa: E402
from engine.resolution import resolve_to_hit  # noqa: E402

TRACES = Path(__file__).resolve().parents[2] / "archive" / "traces"


@pytest.fixture(scope="module")
def rules():
    return get_store().rules


@pytest.fixture(scope="module")
def corpus():
    rows, _ = load_corpus(None)          # the tracked fixture, not the logs
    assert rows, "archive/traces/hit_pairs.json is missing"
    return rows


def _old_rule(roll: float, chance: float) -> bool:
    """The pre-F4 rule, kept verbatim as the thing to discriminate against:
    a hidden +5% accuracy bonus, the chance clamped at 95%, and the roll
    compared straight against it."""
    return roll < min(0.95, chance + 0.05)


def test_the_engine_agrees_with_the_game(corpus, rules):
    res = grade(corpus, rules)
    assert res["classifiable"] >= 150, res["classifiable"]
    assert not res["disagree"], res["disagree"]


def test_the_corpus_contains_a_real_miss(corpus, rules):
    """Agreement on a corpus of nothing but hits would prove very little."""
    res = grade(corpus, rules)
    assert res["misses"], "no classifiable miss in the corpus"
    for r in res["misses"]:
        assert not resolve_to_hit(r["roll"], r["chance"], rules)


def test_the_corpus_can_tell_the_two_rules_apart(corpus, rules):
    """The point of the whole exercise, and the reason this is not a
    tautology. 13 pairs are decided differently by F4 and by the rule this
    project used until 2026-08-19; 7 of them are classifiable, and the game
    sided with F4 on every one -- in BOTH directions:

      * 6 pairs where the roll cleared `cutoffAlwaysHit` while beating the
        chance. F4 hits, the old rule misses. Damage was drawn.
      * 1 pair (chance 0.625, roll 0.673) that only lands if you add the old
        hidden +5% to the chance. F4 misses, the old rule hits. No damage.

    So the old rule is refuted from both sides, not merely unsupported."""
    split = [r for r in corpus
             if resolve_to_hit(r["roll"], r["chance"], rules)
             != _old_rule(r["roll"], r["chance"])]
    scored = [r for r in split if r["turn_damaged"]]
    assert len(scored) >= 7, (len(scored), len(split))

    for r in scored:
        assert resolve_to_hit(r["roll"], r["chance"], rules) == r["damage"], r
        assert _old_rule(r["roll"], r["chance"]) != r["damage"], r

    assert any(r["damage"] for r in scored), "no F4-hit / old-miss pair"
    assert any(not r["damage"] for r in scored), "no F4-miss / old-hit pair"


def test_the_chance_is_never_clamped(corpus):
    """`compute_hit_chance` returns a bare ACC - DODGE. The game logging a
    chance of 1.05 is only possible if it does the same."""
    over = sorted({r["chance"] for r in corpus if r["chance"] > 1.0})
    assert over, "no chance above 1.0 in the corpus"
    assert max(over) >= 1.05, over


def test_a_shared_roll_covers_every_target_of_one_attack(corpus):
    """The other thing the corpus shows: a multi-target attack prints one
    `Roll to hit` per target and they are IDENTICAL, while the chances
    differ per target. One roll, per-target accuracy."""
    by_turn = {}
    for r in corpus:
        by_turn.setdefault((r["source"], r["turn"]), []).append(r)
    multi = [v for v in by_turn.values() if len(v) > 1]
    assert multi, "no multi-target attack in the corpus"
    shared = [v for v in multi if len({round(x["roll"], 6) for x in v}) == 1]
    assert len(shared) == len(multi), \
        "%d of %d multi-target attacks did not share one roll" % (
            len(multi) - len(shared), len(multi))
    assert any(len({x["chance"] for x in v}) > 1 for v in multi), \
        "no multi-target attack in the corpus has per-target chances"


@pytest.mark.skipif(not list(TRACES.glob("*.log")),
                    reason="raw captures are gitignored; fixture-only run")
def test_the_fixture_still_matches_the_raw_captures(corpus):
    """The logs are `*.log` and therefore untracked, so the fixture is the
    tracked evidence. When the logs ARE present they must still produce it,
    or the fixture has drifted from what was captured."""
    rebuilt = []
    for f in sorted(TRACES.glob("*.log")):
        rebuilt.extend(parse_trace(str(f)))
    assert len(rebuilt) == len(corpus), (len(rebuilt), len(corpus))
    for a, b in zip(rebuilt, corpus):
        assert a == b, (a, b)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
