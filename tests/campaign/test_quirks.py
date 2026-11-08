"""Tests for campaign/quirks.py -- a quest's quirk and disease rolls.

Pinned: the shipped rules parse, the two rolls land at their interpolated
rates, a full polarity replaces rather than grows, and the disease roll
waits for resolve 2.
"""

import json
import random
import sys
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests._fixtures import get_store

from campaign.quirks import quest_end
from data.paths import GAME_DIR

S = get_store()
N = 4000


def _hero(stress=0.0, level=0, quirks=()):
    return SimpleNamespace(hero_class="crusader", stress=stress, resolve_level=level,
                           quirks=list(quirks))


def _rates(stress, success, level=0):
    rng = random.Random(1)
    neg = pos = dis = 0
    for _ in range(N):
        h = _hero(stress, level)
        for q in quest_end(rng, S, h, success=success):
            k = S.quirks[q]
            dis += k.is_disease
            pos += k.is_positive
            neg += not k.is_positive and not k.is_disease
    return neg / N, pos / N, dis / N


def test_the_shipped_rules_parse():
    raw = json.loads((Path(GAME_DIR) / "shared" / "rules.json").read_text(encoding="utf-8"))
    r = S.rules
    assert (r.neg_stress0_success, r.pos_stress0_success) == (raw["negStress0Success"], raw["posStress0Success"])
    assert (r.neg_stress100_failure, r.pos_stress100_failure) == (raw["negStress100Failure"], raw["posStress100Failure"])
    assert r.disease_max_chance == raw["disease_max_chance"]
    assert r.quirks_max_positive == raw["quirks_max_positive"]


def test_the_rolls_land_at_their_interpolated_rates():
    """Won at 0 stress: 30% / 50%. Lost at 100: 70% / 25%. Lost at 50: the
    midpoint, 55% / 32.5%. A level-0 hero catches no disease."""
    for stress, success, want_neg, want_pos in ((0, True, 0.30, 0.50), (100, False, 0.70, 0.25),
                                                 (50, False, 0.55, 0.325)):
        neg, pos, dis = _rates(stress, success)
        assert abs(neg - want_neg) < 0.03 and abs(pos - want_pos) < 0.03, (stress, success, neg, pos)
        assert dis == 0


def test_the_disease_roll_waits_for_resolve_two():
    """A Crusader's 30% disease resist: 0.32 - 0.30 x 0.33 = 0.221."""
    assert _rates(0, True, level=1)[2] == 0
    assert abs(_rates(0, True, level=2)[2] - 0.221) < 0.03


def test_a_full_polarity_replaces_rather_than_grows():
    full = sorted(q.id for q in S.quirks.values() if q.is_positive and not q.is_disease)[:5]
    rng = random.Random(3)
    grew = 0
    for _ in range(200):
        h = _hero(quirks=full)
        gained = quest_end(rng, S, h, success=True)
        assert sum(S.quirks[q].is_positive for q in h.quirks) == 5
        assert len(set(h.quirks)) == len(h.quirks)
        grew += any(S.quirks[q].is_positive for q in gained)
    assert grew


def _full_positives():
    from bridge.townplan import quirk_kinds
    kinds = quirk_kinds(str(GAME_DIR))
    return [q for q in sorted(S.quirks) if kinds.get(q) == "positive"
            and not S.quirks[q].incompatible_quirks][:5]


def test_a_locked_quirk_is_never_the_one_replaced():
    """The Sanitarium's lock (Fran's option 4, 2026-10-12): at the cap the
    new positive replaces an UNLOCKED one."""
    five = _full_positives()
    replaced = 0
    for seed in range(300):
        h = _hero(quirks=five)
        quest_end(random.Random(seed), S, h, success=True, locked=frozenset(five[:4]))
        assert set(five[:4]) <= set(h.quirks)
        replaced += five[4] not in h.quirks
    assert replaced


def test_with_every_owned_one_locked_no_new_quirk_comes():
    five = _full_positives()
    for seed in range(300):
        h = _hero(quirks=five)
        quest_end(random.Random(seed), S, h, success=True, locked=frozenset(five))
        assert sorted(q for q in h.quirks if q in five) == sorted(five)
        assert sum(1 for q in h.quirks if not S.quirks[q].is_disease and S.quirks[q].is_positive) == 5


if __name__ == "__main__":
    for t in (test_the_shipped_rules_parse, test_the_rolls_land_at_their_interpolated_rates,
              test_the_disease_roll_waits_for_resolve_two, test_a_full_polarity_replaces_rather_than_grows,
              test_a_locked_quirk_is_never_the_one_replaced,
              test_with_every_owned_one_locked_no_new_quirk_comes):
        t()
        print("  ok ", t.__name__)
