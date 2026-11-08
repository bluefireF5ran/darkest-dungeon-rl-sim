"""Tests for the Sanitarium decision (`bridge.townplan.choose_sanitarium`).

Fran, 2026-10-12, docs/NEXT.md item 5, option 4: diseases cured; the worst
negative removed when it ranks 1-5; else an S-tier positive locked. Invested
and nurture heroes only, under the Abbey's tenth of the bank.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from bridge import townplan
from bridge.live_state import LiveHero
from bridge.town_state import RosterHero, TownState
from bridge.townplan import choose_sanitarium, quirk_kinds
from data.paths import GAME_DIR
from data.town_costs import load_sanitarium

GD = str(GAME_DIR)
DISEASE = sorted(q for q, k in quirk_kinds(GD).items() if k == "disease")[0]
BAD = "fragile"          # NEGATIVE_PRIORITY 3
MILD = "calm"            # NEGATIVE_PRIORITY 6: never removed
GOOD = "eagle_eye"       # positive tier S


def invested(guid, quirks, **kw):
    """Weapon rank 1 makes a hero invested (`hero_roles.role_of`)."""
    return RosterHero(hero=LiveHero(guid=guid, hero_class="crusader", weapon_rank=1,
                                    quirks=list(quirks)), **kw)


def fodder(guid, quirks):
    return RosterHero(hero=LiveHero(guid=guid, hero_class="crusader", quirks=list(quirks)))


def town(heroes, gold=1_000_000, done=10, locked=None):
    return TownState(heroes=list(heroes), wallet={"gold": gold}, quests_finished=done,
                     locked_quirks=locked or {})


def test_the_prices_slots_and_chances_are_the_shipped_ladders():
    s = load_sanitarium(GD)
    assert s.quests_required == 4
    assert (s.lock_positive.at(), s.remove_negative.at(), s.cure_disease.at()) == (7500, 1500, 750)
    full = {t: set("abcdef") for t in ("sanitarium.cost", "sanitarium.slots",
                                       "sanitarium.disease_quirk_cost")}
    assert s.lock_positive.at(full) == 3750
    assert s.cure_all_chance.at() == 0.33 and s.cure_all_chance.at(full) == 1.0
    assert (s.quirk_slots.at(), s.quirk_slots.at(full)) == (1, 3)


def test_option_4_disease_first_then_the_worst_negative():
    plan = choose_sanitarium(town([invested(1, [DISEASE]), invested(2, [BAD, GOOD])]), GD)
    assert [(v.guid, v.action, v.quirk) for v in plan] == [(1, "cure", DISEASE), (2, "remove", BAD)]
    assert plan[0].cure_all_chance == 0.33


def test_with_no_bad_negative_an_S_tier_positive_is_locked():
    plan = choose_sanitarium(town([invested(1, [MILD, GOOD])]), GD)
    assert [(v.action, v.quirk, v.cost) for v in plan] == [("lock", GOOD, 7500)]


def test_fodder_a_hero_in_a_building_or_the_party_never_goes():
    heroes = [fodder(1, [DISEASE]), invested(2, [DISEASE], building="abbey"),
              invested(3, [DISEASE], in_party=True)]
    assert choose_sanitarium(town(heroes), GD) == []


def test_the_tenth_of_the_bank_caps_it():
    # 10,000 gold: a 1,000 cap pays the 750 cure, never the 7,500 lock.
    plan = choose_sanitarium(town([invested(1, [DISEASE]), invested(2, [GOOD])], gold=10_000), GD)
    assert [v.action for v in plan] == ["cure"]


def test_a_locked_negative_costs_the_permanent_price_and_a_locked_positive_is_not_relocked():
    plan = choose_sanitarium(town([invested(1, [BAD])], locked={1: {BAD}}), GD)
    assert plan[0].cost == load_sanitarium(GD).remove_locked_negative.at() == 5000
    assert choose_sanitarium(town([invested(1, [GOOD])], locked={1: {GOOD}}), GD) == []


def test_shut_until_four_quests_and_off_when_the_ruling_is_off():
    assert choose_sanitarium(town([invested(1, [DISEASE])], done=3), GD) == []
    assert choose_sanitarium(town([invested(1, [DISEASE])], done=None), GD)
    saved = townplan.SANITARIUM
    try:
        townplan.SANITARIUM = False
        assert choose_sanitarium(town([invested(1, [DISEASE])]), GD) == []
    finally:
        townplan.SANITARIUM = saved


def test_one_slot_each_until_upgraded():
    heroes = [invested(i, [DISEASE, BAD]) for i in range(1, 5)]
    plan = choose_sanitarium(town(heroes), GD)
    assert sorted(v.activity for v in plan) == ["disease_treatment", "treatment"]
    assert len({v.guid for v in plan}) == 2


def test_the_live_town_reports_the_plan_but_holds_no_gold_for_it():
    """The panel is not measured, so the live caller passes
    `sanitarium_spends=False`: the plan is printed, the nurture budget keeps
    its gold, and a visit with only Sanitarium lines has nothing to click."""
    t = town([invested(1, [DISEASE])], gold=100_000)
    sim = townplan.advise(t, game_dir=GAME_DIR)
    live = townplan.advise(t, game_dir=GAME_DIR, sanitarium_spends=False)
    assert sim.sanitarium and live.sanitarium
    spent = sum(v.cost for v in sim.sanitarium)
    assert live.wallet_after["gold"] - sim.wallet_after["gold"] == spent
    assert any("SANITARIUM" in line for line in live.lines())
    only = townplan.TownAdvice(sanitarium=live.sanitarium)
    assert only.empty


if __name__ == "__main__":
    for name, t in list(globals().items()):
        if name.startswith("test_"):
            t()
            print("  ok ", name)
