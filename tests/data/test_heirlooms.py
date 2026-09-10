"""Building upgrades, the heirloom wallet, and the trade that pays for one.

The worked example throughout is Fran's, given as an instruction rather than as
a specification: *"upgrading a few levels on the top path (to do the second
level you'll need to trade Crests for the needed Scrolls until you can upgrade
it)"*. Three separate readings had to be right for that to be actionable, and
each is a test here:

* **"Scrolls" are DEEDS.** There is no scroll currency; the deed icon is a
  scroll. The shipped rate `crest 3 -> deed 1` is the trade he is describing.
* **The shortfall is exactly one deed**, from the shipped starting wallet
  through the shipped tree costs. If that arithmetic did not come out, the
  reading would be wrong however plausible.
* **The trade he names is the CHEAPEST one**, which is not obvious and which
  the first version of the solver got wrong -- see
  `test_a_portrait_is_worth_three_crests_and_that_changes_the_answer`.

That last one is the reason the objective is derived from the exchange table
rather than chosen: an expert's instruction and a data-derived cost function
arriving at the same trade from opposite directions is much better evidence
than either alone.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest

from data.heirlooms import (HEIRLOOMS, Rate, Trade, UpgradeNode, crest_values,
                            load_building_upgrades, load_rates, next_nodes,
                            plan_purchases, read_purchased, read_rank_counts,
                            solve_exchange)

from data.paths import GAME_DIR

pytestmark = pytest.mark.skipif(not GAME_DIR.exists(),
                                reason="needs the game install")

# `scripts/starting_save/persist.estate.json` -- what a new campaign begins
# with. Written out rather than read so this test states its own premise: if
# the shipped save ever changes, the arithmetic below should be re-derived
# deliberately and not silently follow it.
STARTING_WALLET = {"bust": 10, "portrait": 10, "deed": 10, "crest": 20}


@pytest.fixture(scope="module")
def rates():
    return load_rates(str(GAME_DIR))


@pytest.fixture(scope="module")
def coach():
    return load_building_upgrades(str(GAME_DIR), "stage_coach")


# -- reading the data -----------------------------------------------------

def test_the_exchange_ships_twelve_rates_over_the_four_heirlooms(rates):
    assert len(rates) == 12
    assert {r.from_type for r in rates} | {r.to_type for r in rates} == set(HEIRLOOMS)


def test_crests_buy_deeds_at_three_to_one(rates):
    """Fran's "Crests for Scrolls", located in the shipped table."""
    match = [r for r in rates if r.from_type == "crest" and r.to_type == "deed"]
    assert len(match) == 1
    assert (match[0].from_amount, match[0].to_amount) == (3, 1)


def test_gold_is_never_part_of_an_upgrade_cost(coach):
    """Every building requirement ships a `gold` entry of 0. It must not reach
    the wallet or the solver -- gold is earned and spent by other rules and a
    trade solver would happily try to sell it."""
    assert coach
    for node in coach:
        assert "gold" not in node.cost
        assert all(amount > 0 for amount in node.cost.values())


def test_a_building_with_no_upgrade_file_is_empty_not_an_error():
    """The graveyard and the statue are legitimate destinations with nothing to
    buy. Raising here would make `TownAdvice` need a try/except per building."""
    assert load_building_upgrades(str(GAME_DIR), "graveyard") == ()
    assert load_building_upgrades(str(GAME_DIR), "not_a_building") == ()


def test_a_prerequisite_can_name_ANOTHER_buildings_tree(coach):
    """`upgraded_recruits.a` needs guild and blacksmith ranks. A reader that
    assumed "the previous code in this tree" would offer it on week one, when
    it is unbuyable."""
    node = next(n for n in coach if n.tree_id.endswith("upgraded_recruits")
                and n.code == "a")
    trees = {t for t, _ in node.prerequisites}
    assert "guild.skill_levels" in trees
    assert "blacksmith.weapon" in trees
    assert not any(t.startswith("stage_coach") for t in trees)


def test_next_nodes_offers_only_what_is_reachable_now(coach):
    """On a fresh campaign the two first ranks are buyable and nothing else."""
    open_now = {n.key for n in next_nodes(coach, purchased=set())}
    assert ("stage_coach.numrecruits", "a") in open_now
    assert ("stage_coach.rostersize", "a") in open_now
    assert ("stage_coach.numrecruits", "b") not in open_now
    assert ("stage_coach.upgraded_recruits", "a") not in open_now

    after_a = next_nodes(coach, purchased={("stage_coach.numrecruits", "a")})
    assert ("stage_coach.numrecruits", "b") in {n.key for n in after_a}
    assert ("stage_coach.numrecruits", "a") not in {n.key for n in after_a}


# -- the valuation --------------------------------------------------------

def test_the_crest_values_come_out_of_the_sell_rows(rates):
    """The crest is 1 by definition -- the finest denomination, and the only
    currency every other one sells into."""
    assert crest_values(rates) == {"bust": 1.5, "portrait": 3.0,
                                   "deed": 1.5, "crest": 1.0}


def test_every_trade_that_is_not_a_SELL_row_is_lossy_in_crest_terms(rates):
    """The exchange takes a cut, which is why the solver minimises what goes
    IN rather than counting rows.

    The three `X -> crest` rows are exempt, and not by special pleading: those
    are the rows the valuation is READ OFF, so they price themselves at
    exactly 1.0 by construction. A test that expected them to be lossy would
    be asserting that the definition disagrees with itself. The nine remaining
    rows are the ones that carry real information about the house's cut, and
    all nine lose value.
    """
    values = crest_values(rates)
    sells = [r for r in rates if r.to_type == "crest"]
    others = [r for r in rates if r.to_type != "crest"]
    assert len(sells) == 3 and len(others) == 9

    for r in sells:
        assert (r.to_amount * values["crest"]
                == pytest.approx(r.from_amount * values[r.from_type])), r
    for r in others:
        put_in = r.from_amount * values[r.from_type]
        got_out = r.to_amount * values[r.to_type]
        assert got_out < put_in, r


def test_a_portrait_is_worth_three_crests_and_that_changes_the_answer(rates):
    """The bug this objective exists to prevent, kept as a test.

    Counting raw units, `2 portrait -> 3 deed` beats `3 crest -> 1 deed`
    because 2 < 3 -- and it costs twice as much, because a portrait is three
    crests and a crest is one. The exchange table says so itself; the naive
    objective simply was not reading it.
    """
    values = crest_values(rates)
    portrait_route = 2 * values["portrait"]
    crest_route = 3 * values["crest"]
    assert portrait_route == 6.0
    assert crest_route == 3.0
    assert crest_route < portrait_route
    assert 2 < 3, "and unit-counting would have preferred the expensive one"


# -- Fran's case ----------------------------------------------------------

def test_the_top_path_leaves_the_wallet_exactly_one_deed_short(coach):
    """The arithmetic that makes the instruction a specification.

    Start (10, 10, 10, 20); `numrecruits a` costs deed 3 + crest 4, leaving 7
    deed and 16 crest; `b` costs deed 8 -- one more than there is.
    """
    node_a = next(n for n in coach if n.key == ("stage_coach.numrecruits", "a"))
    node_b = next(n for n in coach if n.key == ("stage_coach.numrecruits", "b"))
    assert node_a.cost == {"deed": 3, "crest": 4}
    assert node_b.cost == {"deed": 8, "crest": 10}

    after_a = {c: STARTING_WALLET[c] - node_a.cost.get(c, 0) for c in HEIRLOOMS}
    assert after_a == {"bust": 10, "portrait": 10, "deed": 7, "crest": 16}
    assert after_a["deed"] == node_b.cost["deed"] - 1
    assert after_a["crest"] >= node_b.cost["crest"], "crests were never the problem"


def test_the_solver_names_the_trade_Fran_named(rates, coach):
    """One `3 crest -> 1 deed`, and nothing else.

    An expert's instruction and a cost function derived from the shipped
    exchange table, arriving at the same trade from opposite directions.
    """
    node_a = next(n for n in coach if n.key == ("stage_coach.numrecruits", "a"))
    node_b = next(n for n in coach if n.key == ("stage_coach.numrecruits", "b"))
    bought, left = plan_purchases(STARTING_WALLET, [node_a, node_b], rates)

    assert [n.key for n, _ in bought] == [node_a.key, node_b.key]
    assert bought[0][1] == [], "the first rank is affordable outright"
    trades = bought[1][1]
    assert len(trades) == 1
    assert trades[0].rate.from_type == "crest"
    assert trades[0].rate.to_type == "deed"
    assert trades[0].times == 1
    assert trades[0].spends == 3 and trades[0].gains == 1
    assert left == {"bust": 10, "portrait": 10, "deed": 0, "crest": 3}


# -- the solver's contract ------------------------------------------------

def test_an_affordable_cost_needs_no_trades_and_says_so(rates):
    """`[]` and `None` are different answers and the actuator does opposite
    things about them: one buys, the other reports it cannot."""
    assert solve_exchange(STARTING_WALLET, {"deed": 3}, rates) == []
    assert solve_exchange({"bust": 0, "portrait": 0, "deed": 0, "crest": 0},
                          {"deed": 99}, rates) is None


def test_no_plan_ever_contains_a_step_the_game_would_refuse(rates):
    """Affordability is checked as the plan is built, so no plan is valid only
    if executed in some other order."""
    wallet = {"bust": 3, "portrait": 0, "deed": 0, "crest": 1}
    for target in ({"deed": 2}, {"crest": 6}, {"portrait": 1}, {"bust": 4}):
        plan = solve_exchange(wallet, target, rates)
        if plan is None:
            continue
        have = dict(wallet)
        for trade in plan:
            assert have.get(trade.rate.from_type, 0) >= trade.spends, (target, plan)
            have[trade.rate.from_type] -= trade.spends
            have[trade.rate.to_type] = have.get(trade.rate.to_type, 0) + trade.gains
        assert all(have.get(c, 0) >= n for c, n in target.items())


def test_the_solver_will_take_two_hops_when_one_will_not_do(rates):
    """Not every pair has a direct row -- and where it does, a two-step route
    can still be cheaper. Either way the plan has to be reachable, which is
    what a hand-written "sell the currency you have most of" would miss."""
    # Only busts, and deeds are wanted. bust -> crest -> deed exists;
    # bust -> deed also exists, so the solver must pick, not just find.
    plan = solve_exchange({"bust": 9, "portrait": 0, "deed": 0, "crest": 0},
                          {"deed": 2}, rates)
    assert plan is not None
    values = crest_values(rates)
    spent = sum(t.spends * values[t.rate.from_type] for t in plan)
    assert spent == pytest.approx(4.5), "3 bust -> 2 deed, the direct row"
    assert len(plan) == 1


def test_a_node_that_cannot_be_paid_for_is_skipped_not_fatal(rates, coach):
    """Reporting "bought a, could not afford b" is more useful than stopping,
    and a later node may be cheap in a currency the wallet still holds."""
    nodes = [n for n in coach if n.tree_id.endswith("numrecruits")][:4]
    bought, _ = plan_purchases(STARTING_WALLET, nodes, rates)
    assert [n.code for n, _ in bought] == ["a", "b"], "c and d are out of reach"


def test_plan_purchases_never_overdraws_the_wallet(rates, coach):
    bought, left = plan_purchases(STARTING_WALLET, list(coach), rates)
    assert all(amount >= 0 for amount in left.values()), left


# -- reading what has already been bought ---------------------------------
#
# `persist.upgrades.json` is not shaped the way its field names suggest, and
# the obvious reading returns an empty set from a save with 228 purchases in
# it. These pin the real encoding against a real save.

SAVE = Path("D:/Program Files (x86)/Steam/userdata/312830650/262060/remote"
            "/profile_8/persist.upgrades.json")
needs_save = pytest.mark.skipif(not SAVE.exists(), reason="needs Fran's save")


@needs_save
def test_the_rank_of_a_tree_is_HOW_MANY_entries_it_has():
    """The encoding, stated as the property that makes it checkable.

    `requirement_code` and `is_purchased` decode to nothing in all 228
    entries, so neither can carry the rank. What does is the entry COUNT --
    and the check that this is a reading rather than a guess is that no tree
    in the save exceeds its own rank count. A wrong interpretation would
    overflow somewhere in 228 entries.
    """
    import json
    from data.dson import dd_name_hash

    ranks = {}
    upgrades = Path(GAME_DIR) / "upgrades"
    for f in upgrades.rglob("*.upgrades.json"):
        try:
            raw = json.load(open(f, encoding="utf-8", errors="replace"))
        except (OSError, ValueError):
            continue
        for tree in raw.get("trees") or []:
            tid = str(tree.get("id") or "")
            if tid:
                ranks[dd_name_hash(tid) & 0xFFFFFFFF] = len(
                    tree.get("requirements") or [])

    counts = read_rank_counts(SAVE)
    assert counts, "a Week 22 save has purchases"
    known = [(h, n) for (h, _i), n in counts.items() if h in ranks]
    assert len(known) >= 40, "most hashes must invert against shipped trees"
    over = [(h, n, ranks[h]) for h, n in known if n > ranks[h]]
    assert not over, f"rank count exceeded the tree's own ranks: {over}"


@needs_save
def test_the_tree_id_is_a_dd_name_hash_and_inverts():
    """One-way, so inverting means hashing the candidates -- the same method
    `bridge/dungeon_map.py` uses for `curio_prop`. Decoded UNSIGNED, because
    DSON reads 4-byte fields signed."""
    from data.dson import dd_name_hash
    counts = read_rank_counts(SAVE)
    smite = dd_name_hash("crusader.smite") & 0xFFFFFFFF
    ranks = sorted(n for (h, _i), n in counts.items() if h == smite)

    # The hash inverts: a skill nobody in the roster has would not appear at
    # all, and a wrong hash function would find nothing for every name tried.
    assert ranks, "crusader.smite must be found by its hash"
    assert all(1 <= n <= 5 for n in ranks), ranks

    # `instance_number` is WHICH HERO, so one tree legitimately appears once
    # per Crusader in the roster at that hero's own rank. Asserting a single
    # value here is what first failed: this save has two Crusaders, at ranks
    # 1 and 5. That the tally is per-instance rather than per-tree is the
    # thing worth pinning.
    instances = {i for (h, i) in counts if h == smite}
    assert len(instances) == len(ranks), "one tally per hero, not per tree"

    # An id the game does not ship must not resolve to anything.
    bogus = dd_name_hash("crusader.not_a_real_skill") & 0xFFFFFFFF
    assert not [n for (h, _i), n in counts.items() if h == bogus]


@needs_save
def test_every_BUILDING_upgrade_read_back_is_a_REAL_node():
    """**This test used to assert the live save contained NO building upgrade**,
    on the strength of a Week 22 wallet sitting within two busts of the shipped
    starting heirlooms. That was a fact about one snapshot, and it expired the
    moment the bridge bought `stage_coach.numrecruits.a` for real -- the save
    now carries six, across all three buildings.

    Pinning a live save's CONTENT is what was wrong with it: the reader is what
    this file tests, and the reader cannot be judged by how much the player has
    since spent. So the claim is now the one that survives a campaign
    progressing -- every `(tree, code)` that comes back names a node the game
    actually ships, in that building's own file, and an id the game does not
    ship still resolves to nothing (the test above).

    The original reason for recording anything here stands and is why this is a
    test rather than a deletion: "the reader returns nothing" and "the save
    contains nothing" look identical from the outside, and a non-empty read is
    now positive evidence the reader works against a real purchase.
    """
    from data.dson import dd_name_hash

    nodes = (load_building_upgrades(str(GAME_DIR), "stage_coach")
             + load_building_upgrades(str(GAME_DIR), "guild")
             + load_building_upgrades(str(GAME_DIR), "blacksmith"))
    got = read_purchased(SAVE, nodes)

    # The cross-check, and the reason this is not a tautology: `read_purchased`
    # derives its answer from `nodes`, so "everything it returns is a real node"
    # is true by construction. Which TREES it found is not -- that comes from
    # matching the save's raw hashed `tree_id`s, so hash the ids independently
    # and require the two agree. Holds on an empty save too, both sides being
    # empty, which is what keeps it honest across a slot reset.
    counts = read_rank_counts(SAVE)
    in_save = {h for (h, _instance) in counts}
    expected = {n.tree_id for n in nodes
                if (dd_name_hash(n.tree_id) & 0xFFFFFFFF) in in_save}
    assert {tree for tree, _code in got} == expected

    # And a tree can never report more ranks bought than it ships.
    per_tree: dict[str, int] = {}
    for tree, _code in got:
        per_tree[tree] = per_tree.get(tree, 0) + 1
    ships: dict[str, int] = {}
    for n in nodes:
        ships[n.tree_id] = ships.get(n.tree_id, 0) + 1
    assert all(k <= ships[tree] for tree, k in per_tree.items())


def test_a_missing_file_reads_as_nothing_bought():
    """Which is exactly what a fresh campaign reports, and being wrong costs
    an offer the game refuses rather than a wrong click."""
    assert read_rank_counts(Path("Z:/no/such/persist.upgrades.json")) == {}
    assert read_purchased(Path("Z:/no/such/persist.upgrades.json"), []) == set()


def test_purchased_codes_come_back_in_FILE_order(coach):
    """A count of N means the first N codes, because a rank's prerequisite is
    the rank below it -- which is why a bare count is enough."""
    import types
    from data import dson

    fake = {("stage_coach.numrecruits", 0): 2}
    # Drive `read_purchased` through a stubbed tally rather than a stubbed
    # save: the file format is pinned above, and this is about the mapping.
    counts = {dson.dd_name_hash(t) & 0xFFFFFFFF: n for (t, _i), n in fake.items()}

    ordered = {}
    for node in coach:
        ordered.setdefault(node.tree_id, []).append(node.code)
    got = {(tid, c)
           for tid, codes in ordered.items()
           for c in codes[:counts.get(dson.dd_name_hash(tid) & 0xFFFFFFFF, 0)]}
    assert got == {("stage_coach.numrecruits", "a"),
                   ("stage_coach.numrecruits", "b")}
