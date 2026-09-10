"""The stress-ratchet ruler (`tools/stress_ratchet.py`).

Fran called high stress "like a house of cards". The corpus says something more
precise, and the tool exists because both of the obvious readings were wrong:
it does not accelerate (+13.1 under 50 against +10.6 over it) and it is not
strictly irreversible (34 downward moves, 1 of 7 heroes recovered). What is
lopsided is the SIZE -- falls average -4.4 against rises of +12.4.

These tests pin the arithmetic, not the finding: a ruler that miscounts a
recovery would quietly turn a ratchet into a cliff or vice versa.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.stress_ratchet import (ARMS, ARMS_BY_KEY, FULL_PARTY,  # noqa: E501
                                  LONG_ROUNDS, THRESHOLD, _long_share, _mean,
                                  _series_stats, bootstrap_ci, combat_lengths,
                                  combat_records, from_journal, parties_for)

REPO = Path(__file__).resolve().parents[2]


def test_a_hero_who_never_crosses_is_not_counted_either_way():
    """The denominator is heroes who got into trouble. Counting a calm hero as
    a non-recovery would drive P(recover) to zero with no evidence at all."""
    st = _series_stats([[0.0, 5.0, 12.0, 9.0]])
    assert st["crossed"] == 0 and st["p_recover"] is None


def test_recovery_needs_the_high_reading_FIRST():
    """Order matters: a hero who starts low, rises above the threshold and
    stays there has not recovered, even though low readings exist in the
    series. A naive `min() < threshold < max()` would call that a recovery."""
    climbed = _series_stats([[10.0, 40.0, 60.0, 80.0]])
    assert climbed["crossed"] == 1 and climbed["recovered"] == 0

    came_back = _series_stats([[10.0, 60.0, 80.0, 30.0]])
    assert came_back["crossed"] == 1 and came_back["recovered"] == 1


def test_rises_are_split_on_where_they_STARTED():
    """The cascade question is "does it go up faster once high", so a rise is
    attributed to the level it began at, not the one it reached. Attributing
    by the endpoint would push every threshold-crossing rise into the high
    bucket and manufacture the acceleration being tested for."""
    st = _series_stats([[40.0, 70.0, 90.0]])
    assert st["n_lo"] == 1 and st["rise_lo"] == 30.0    # 40 -> 70 began low
    assert st["n_hi"] == 1 and st["rise_hi"] == 20.0    # 70 -> 90 began high


def test_falls_are_counted_and_are_not_negative_rises():
    st = _series_stats([[80.0, 60.0, 65.0]])
    assert st["falls"] == 1 and st["fall_mean"] == 20.0
    assert st["n_hi"] == 1 and st["rise_hi"] == 5.0


def test_a_single_reading_carries_no_transitions():
    """One sample is not a trajectory. Included because `from_journal` filters
    on `len(s) > 1` and this is the invariant that filter protects."""
    st = _series_stats([[99.0]])
    assert st["n_lo"] == st["n_hi"] == st["falls"] == 0


def test_the_journal_arm_keys_heroes_across_RAIDS():
    """Stress carries out of the dungeon -- it is the whole reason Fran ranks
    it second -- so a hero's series must not restart per raid. Two raids, same
    guid, must read as one trajectory with the drop between them visible."""
    import json
    import tempfile

    rows = [
        {"kind": "play", "board": {"heroes": [{"guid": 1, "class": "vestal",
                                               "stress": 60.0}]}},
        {"kind": "play", "board": {"heroes": [{"guid": 1, "class": "vestal",
                                               "stress": 20.0}]}},
    ]
    tmp = Path(tempfile.mkdtemp()) / "j.jsonl"
    tmp.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    series = from_journal([tmp])
    assert series == [[60.0, 20.0]]
    assert _series_stats(series)["recovered"] == 1


def test_the_threshold_is_the_one_the_observation_was_made_at():
    """50 is half the Resolve Check and is where the real-play numbers quoted
    in the docstring were measured. Moving it silently would leave those
    numbers in place describing a different cut."""
    assert THRESHOLD == 50.0


# --- how LONG is a fight, and against WHAT ---------------------------------
#
# The comparison these support was quoted for a day as "simulator fights are
# half as long as real ones" (3.9 against 2.0) and that reading did not
# survive being controlled. Both differences that were folded into it push the
# same way: the real sample is 30% short-handed or two-hero combats, and the
# simulator arm pooled every tier with full parties.


def _p(round_, heroes=4, enemies=("a",)):
    return {"kind": "play", "round": round_,
            "board": {"heroes": [{"guid": i} for i in range(heroes)],
                      "enemies": [{"id": e} for e in enemies]}}


def test_the_round_going_backwards_ends_a_combat():
    assert combat_lengths("t", plays=[_p(1), _p(2), _p(3), _p(1), _p(2)]) \
        == [3, 2]


def test_two_ONE_ROUND_fights_are_not_one_fight():
    """`1 < 1` is false, so the round rule alone merges them -- and that drops
    a SHORT combat from the count, which raises the mean. That is the
    direction which would flatter the claim being tested, so a wholly
    different set of enemies ends a combat too."""
    plays = [_p(1, enemies=("a",)), _p(1, enemies=("b",))]
    assert combat_lengths("t", plays=plays) == [1, 1]


def test_an_overlapping_enemy_set_is_the_SAME_fight():
    """One enemy dying must not look like a new combat. Only a set with no
    survivor in common counts."""
    plays = [_p(1, enemies=("a", "b")), _p(2, enemies=("a",))]
    assert combat_lengths("t", plays=plays) == [2]


def test_a_journal_with_no_enemies_still_works():
    """The enemy rule is an addition, never a requirement: an older journal
    falls back to the round rule and is no worse than it was."""
    plays = [{"kind": "play", "round": r, "board": {"heroes": [{}] * 4}}
             for r in (1, 2, 1)]
    assert combat_lengths("t", plays=plays) == [2, 1]


def test_a_combat_records_the_party_it_was_fought_with():
    rec = combat_records([_p(1, heroes=4), _p(2, heroes=4)])
    assert len(rec) == 1
    assert (rec[0].rounds, rec[0].party_min, rec[0].party_max) == (2, 4, 4)
    assert not rec[0].lost_someone


def test_a_hero_falling_mid_fight_is_flagged_not_averaged_in():
    """The two longest combats in the real corpus are both ones where a hero
    fell. Averaging them into the four-hero number compares a short-handed
    fight against a simulator arm that never loses anybody."""
    rec = combat_records([_p(1, heroes=4), _p(2, heroes=3)])
    assert rec[0].lost_someone
    assert (rec[0].party_min, rec[0].party_max) == (3, 4)


# --- intervals -------------------------------------------------------------

def test_one_value_gets_no_interval():
    """A degenerate [3.9, 3.9] would claim one combat says something about the
    spread of combats."""
    assert bootstrap_ci([4], _mean) == (None, None)
    assert bootstrap_ci([], _mean) == (None, None)


def test_the_interval_brackets_the_statistic():
    vals = [2, 2, 3, 3, 2, 5, 4]
    lo, hi = bootstrap_ci(vals, _mean, seed=1)
    assert lo <= _mean(vals) <= hi
    lo, hi = bootstrap_ci(vals, _long_share, seed=1)
    assert lo <= _long_share(vals) <= hi


def test_the_interval_is_reproducible():
    vals = [1, 2, 3, 4, 5, 6]
    assert bootstrap_ci(vals, _mean, seed=7) == bootstrap_ci(vals, _mean, seed=7)


def test_a_wider_sample_gives_a_tighter_interval():
    """The whole reason the real arm now carries one: n=7 cannot separate 3
    from 2, and saying so is the finding."""
    lo_s, hi_s = bootstrap_ci([2, 3] * 4, _mean, seed=0)
    lo_w, hi_w = bootstrap_ci([2, 3] * 60, _mean, seed=0)
    assert (hi_w - lo_w) < (hi_s - lo_s)


def test_long_is_what_the_playbook_rule_turns_on():
    """PLAYBOOK 3.5 is about a fight lasting long enough for a buff to repay
    the turn it cost, so the share is the primary statistic and the threshold
    is not free to drift."""
    assert LONG_ROUNDS == 3
    assert _long_share([1, 2, 3, 4]) == 0.25


# --- the arms --------------------------------------------------------------

def test_arm_A_is_the_untouched_baseline():
    """It has to stay the sampled path, or the new arms have nothing to be
    compared against."""
    assert ARMS_BY_KEY["A"].tier is None
    assert ARMS_BY_KEY["A"].classes == ()


def test_B_and_C_differ_ONLY_in_party_size():
    """That is what makes B vs C a controlled contrast rather than a second
    experiment: same tier, same band, same zones, same comps."""
    b, c = ARMS_BY_KEY["B"], ARMS_BY_KEY["C"]
    assert (b.tier, b.zones, b.band, b.classes) == \
           (c.tier, c.zones, c.band, c.classes)
    assert (b.party_size, c.party_size) == (4, 2)


def test_the_matched_arms_are_the_world_the_real_combats_came_from():
    """`bat/advise.jsonl`'s `raid_started` events say `difficulty 1`,
    `length 1`, crypts and weald. The arms are not a guess at his world."""
    b = ARMS_BY_KEY["B"]
    assert b.tier == 1 and b.band == "short"
    assert set(b.zones) == {"crypts", "weald"}


class _Hero:
    def __init__(self, hero_id):
        self.hero_id = hero_id
        self.skill_ids = ["s1"]
        self.camp_skill_ids = ["c1"]
        self.trinket_ids = ["t1"]
        self.quirk_ids = {"positive": ["q1"], "negative": ["q2"]}


class _Entry:
    def __init__(self, ids):
        self.heroes = [_Hero(i) for i in ids]


class _Store:
    def __init__(self, comps):
        self.comp_loadouts = comps


def test_a_party_keeps_the_comp_ORDER_and_its_kit():
    """`CombatEngine.reset` assigns `position = index`, so the order IS the
    formation -- and the kit is kept because a bare class list understates
    damage, which lengthens fights, which is the direction that would flatter
    the claim under test."""
    store = _Store({"x": _Entry(["crusader", "highwayman", "occultist",
                                 "plague_doctor"])})
    (name, party), = parties_for(ARMS_BY_KEY["B"], store)
    assert [m.hero_class for m in party] == \
        ["crusader", "highwayman", "occultist", "plague_doctor"]
    assert party[0].trinkets == ("t1",)
    assert set(party[0].quirks) == {"q1", "q2"}


def test_resolve_and_gear_are_forced_to_the_bottom():
    """They have to be set explicitly: the five randomisation knobs --
    `resolve_sampling` among them -- are unreachable from an explicit
    scenario, so nothing else would apply them."""
    store = _Store({"x": _Entry(["crusader"] * 4)})
    (_name, party), = parties_for(ARMS_BY_KEY["B"], store)
    assert all(m.resolve_level == 0 for m in party)
    assert all((m.weapon_rank, m.armour_rank) == (0, 0) for m in party)


def test_a_comp_with_an_out_of_set_class_is_SKIPPED_not_trimmed():
    """Trimming and closing ranks moves every hero behind the dropped one
    into a rank their skills may not launch from -- and the comps that
    survived it were the degenerate ones, because a comp made entirely of
    in-set classes is rarer than a comp with four of them somewhere."""
    store = _Store({"x": _Entry(["crusader", "leper", "occultist",
                                 "plague_doctor"])})
    assert parties_for(ARMS_BY_KEY["B"], store) == []


def test_arm_C_cuts_the_SAME_comps_to_the_front_two():
    store = _Store({"x": _Entry(["crusader", "highwayman", "occultist",
                                 "plague_doctor"])})
    (_n, four), = parties_for(ARMS_BY_KEY["B"], store)
    (_m, two), = parties_for(ARMS_BY_KEY["C"], store)
    assert [m.hero_class for m in two] == [m.hero_class for m in four][:2]


def test_a_short_comp_is_not_a_party():
    """A comp is four heroes as written; anything else is a different object
    and padding it would invent a formation."""
    store = _Store({"x": _Entry(["crusader", "highwayman"])})
    assert parties_for(ARMS_BY_KEY["C"], store) == []
    assert FULL_PARTY == 4
