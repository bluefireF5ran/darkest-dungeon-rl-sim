"""`bridge/lootplan.py` -- route, torch and pack, planned together.

The headline is `test_the_optimiser_reproduces_the_route_FRAN_described`. He
gave the Old Road's optimal line in prose on 2026-09-06 and asked explicitly
that it NOT be hard-coded -- so the test is that an optimiser told only the
objective emits his route anyway. Everything else here guards a way an earlier
version of it got that route right for the wrong reason.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from data.paths import GAME_DIR
OLD_ROAD = GAME_DIR / "scripts" / "starting_save" / "persist.map.json"
CAPTURES = (Path(__file__).resolve().parents[2] / "archive" / "traces")


def _old_road():
    from bridge.dungeon_map import read_dungeon_map

    if not OLD_ROAD.exists():
        pytest.skip("the game is not installed at the hardcoded path")
    dungeon = read_dungeon_map(OLD_ROAD)
    if dungeon is None:
        pytest.skip("the shipped starting save did not decode")
    return dungeon


def _quest():
    from bridge.route import read_quest

    raid = OLD_ROAD.with_name("persist.raid.json")
    return read_quest(raid) if raid.exists() else None


def _actions(plan, *kinds):
    return [s for s in plan.stops if s.action in kinds]


# -- the acceptance test --

def test_a_party_ALREADY_in_the_dark_defers_nothing():
    """Fran, 2026-09-21:

        if it's already a 0 torch there is also not a use on deferring that
        as well as you won't increase that value later, this covers cases
        like combat with chest rooms for example

    The module already cut deferral after a PLANNED snuff and when no battle
    exists at all -- both ask where the snuff falls, and neither asked what
    the torch reads NOW. Walking in at 0 is exactly where a fight leaves you,
    since the snuff lands before the killing blow, so a chest in that room
    was being deferred for a darkness it already had.

    The cut is narrow: it fires only when the light is out AND is not coming
    back. A relight ahead means the curio after it WOULD be opened lit, and
    then deferring buys a torch again -- which is the whole reason deferral
    exists.
    """
    from bridge.lootplan import plan_loot

    def verdicts(torch, inventory):
        plan = plan_loot(_old_road(), _quest(), here="rooA", torch=torch,
                         inventory=inventory)
        return [s.action for s in plan.stops
                if s.action in ("take", "defer", "skip")]

    assert "defer" in verdicts(100.0, {"torch": 4}), (
        "lit with torches in the pack is the case deferral is FOR")
    assert "defer" not in verdicts(0.0, {}), (
        "already dark with no torch to spend -- the curio is going to be "
        "opened in the dark either way, so deferring only risks losing it")
    assert "defer" in verdicts(0.0, {"torch": 3}), (
        "dark but able to relight: the light IS coming back, so the deferral "
        "is worth a torch again")


def test_a_deferral_BUYS_A_TORCH_and_the_backtrack_must_collect_it():
    """Why deferral exists, in Fran's words, 2026-09-21:

        deferring a loot curio can be useful to maintain torches to have a
        safe number of torches for the dungeon, but when you beat the dungeon
        or cases like one way exits you should snuff torch to max loot value
        when backtracking

    So the saving is a TORCH, not only the dark multiplier -- the curio is
    opened later in the dark instead of light being held for it. That makes
    the backtrack load-bearing: a deferral nothing returns for is a forfeit,
    and one cost a strongbox and 880 gold live the same night.

    `WALKS_HOME` was briefly False on the reasoning that the red-seal exit
    ends a raid from wherever the party stands. That is true of the EXIT and
    false of the plan -- the seal is pressed at the END of the backtrack, not
    instead of it.
    """
    from bridge import lootplan

    assert lootplan.WALKS_HOME is True, (
        "deferral is priced on the backtrack; turning it off forfeits every "
        "deferred curio rather than saving a torch")


def test_the_optimiser_reproduces_the_route_FRAN_described():
    """His words, 2026-09-06:

        hallway -> battle 1 -> skip curio 1 for best loot later -> enter room
        -> battle 2, quit torch completely before the last hit -> take the
        loot at torch 0, do not retorch -> ignore curio 2 (trapped) -> back to
        the hallway -> curio 1 at 0 torch -> the retreat location to finish.

    Nothing in `bridge/` names the Old Road, the tent or the chest. The plan
    below is what falls out of "hold a torch floor on planned fights, then
    maximise expected loot" over the shipped loot tables.

    **This route is pinned under the premise it was given.** Fran described
    it when the party walked back to the entrance to hand the quest in, and
    every deferral in `lootplan` is priced on that walk. Since 2026-09-21 the
    exit is the quest banner's red seal, which ends the raid from wherever
    the party stands, so `WALKS_HOME` is False and nothing is deferred --
    see `test_nothing_is_deferred_when_the_party_does_not_walk_home`.

    The optimiser is unchanged and still reproduces his route exactly when
    the premise holds, which is what this test exists to show. Forcing the
    flag here keeps his words on the record rather than editing them to match
    a behaviour change they predate.
    """
    from bridge import lootplan
    from bridge.lootplan import plan_loot

    was = lootplan.WALKS_HOME
    lootplan.WALKS_HOME = True
    try:
        plan = plan_loot(_old_road(), _quest(), here="rooA", torch=100.0,
                         inventory={})
    finally:
        lootplan.WALKS_HOME = was
    order = [(s.action, s.area, s.tile) for s in plan.stops
             if s.action != "walk"]

    assert order == [
        ("fight", "coAB", 2),      # battle 1, and it is LIT
        ("defer", "coAB", 4),      # skip curio 1 -- worth more later
        ("snuff", "rooB", 0),      # before the killing blow of battle 2
        ("fight", "rooB", 0),      # battle 2, its loot rolled at torch 0
        ("skip", "rooB", 0),       # the trapped chest, bare-handed
        ("take", "coAB", 4),       # curio 1, at torch 0, on the way home
        ("finish", "rooA", -1),    # hand it in at the entrance
    ], order

    lit = next(s for s in plan.stops if s.action == "fight")
    assert lit.torch >= 76.0, lit.torch
    assert plan.torches_spent == 0
    assert not plan.dark_battles


def test_it_names_the_two_curios_from_the_SAVE_and_not_from_a_list():
    """The trapped chest is `bandits_trapped_chest` and the game's own id says
    so. If either name stopped resolving the plan above would still pass, so
    it is asserted separately."""
    from bridge.lootplan import events_on

    curios = {e.area: e.curio_id for e in events_on(_old_road())
              if e.kind == "curio"}
    assert curios == {"coAB": "travellers_tent_tutorial",
                      "rooB": "bandits_trapped_chest"}


# -- the inventory half --

def test_a_key_turns_the_trapped_chest_from_a_skip_into_a_take():
    """Fran, 2026-09-06: *"that chest should be opened if we had a key during
    the tutorial for example"*.

    Bare-handed the chest is `Blight 1` and no loot at all; with a skeleton
    key it is loot table T. So the same map, the same route, one item -- and
    the verdict flips. This is the whole reason the router has to see the
    pack.
    """
    from bridge.lootplan import plan_loot

    dungeon, quest = _old_road(), _quest()
    without = plan_loot(dungeon, quest, here="rooA", inventory={})
    with_key = plan_loot(dungeon, quest, here="rooA",
                         inventory={"skeleton_key": 1})

    assert [s.action for s in without.stops if s.area == "rooB"
            and s.curio_id] == ["skip"]
    opened = [s for s in with_key.stops if s.curio_id == "bandits_trapped_chest"]
    assert [s.action for s in opened] == ["take"]
    assert opened[0].item == "skeleton_key"
    assert with_key.value.trinkets > without.value.trinkets


def test_one_key_is_not_spent_twice():
    """`bridge/curios.py::advise` answers one curio at a time against the whole
    pack, so with a single key and two locked chests it says "use the key"
    twice. Fran, 2026-09-06: *"if we know the dungeon still have 3 extra
    chests, doesnt make sense to drop the keys of the invetory"* -- the same
    fact from the inventory side, and the reason the pack is ASSIGNED.
    """
    from bridge.lootplan import assign_items

    choices = [(("a", 0, "curio"), [(None, (0.0, 0.0)), ("skeleton_key", (500.0, 0.0))]),
               (("b", 0, "curio"), [(None, (0.0, 0.0)), ("skeleton_key", (300.0, 0.0))])]
    one = assign_items(choices, {"skeleton_key": 1})
    assert list(one.items.values()).count("skeleton_key") == 1
    # ...and it goes to the better of the two, not the first.
    assert one.items[("a", 0, "curio")] == "skeleton_key"

    two = assign_items(choices, {"skeleton_key": 2})
    assert list(two.items.values()).count("skeleton_key") == 2


def test_the_assignment_beats_taking_the_best_each_time():
    """The case greedy-per-curio gets wrong, which is why this is a DP.

    One shovel and one key. Curio A pays a lot for either; curio B pays only
    for the key. Handing A the key -- its own best -- leaves B with nothing.
    """
    from bridge.lootplan import assign_items

    choices = [
        (("a", 0, "curio"), [(None, (0.0, 0.0)), ("skeleton_key", (100.0, 0.0)),
                             ("shovel", (90.0, 0.0))]),
        (("b", 0, "curio"), [(None, (0.0, 0.0)), ("skeleton_key", (80.0, 0.0))]),
    ]
    best = assign_items(choices, {"skeleton_key": 1, "shovel": 1})
    assert best.items[("a", 0, "curio")] == "shovel"
    assert best.items[("b", 0, "curio")] == "skeleton_key"
    assert best.worth[0] == pytest.approx(170.0)


# -- the torch half --

def test_a_loot_row_draws_each_table_its_count_of_times():
    """`curio_type_library.csv`: a locked strongbox is Loot `A` x2 bare-handed
    (half the time) and `A` x3 with the key -- `<- # Draws`, not weights. Read
    as weights, every curio paid ONE draw (2026-10-11)."""
    from bridge.lootplan import curio_options
    from data.loot_loader import load_loot_tables
    a = load_loot_tables(str(GAME_DIR)).expected("A", 1, "crypts").gold
    opts = {o.item: o.base.gold for o in curio_options("locked_strongbox", 1, "crypts")}
    assert a > 0
    assert abs(opts["skeleton_key"] - 3 * a) < 1e-6
    assert abs(opts[None] - 0.5 * 2 * a) < 1e-6


def test_a_curio_that_draws_no_loot_earns_nothing_from_the_dark():
    """The bonus is an extra draw on a draw. An early version credited the
    chest bonus to `bandits_trapped_chest` bare-handed -- whose only outcome
    is `Blight 1` -- and the plan took it for a fictional +165g."""
    from bridge.lootplan import curio_options, value_at

    bare = next(o for o in curio_options("bandits_trapped_chest", 1, "weald")
                if o.item is None)
    assert bare.loot_chance == 0.0
    assert value_at(bare, 0.0, 1, "weald").empty


def test_the_same_curio_is_worth_more_in_the_dark():
    """Both halves: the gold multiplier on its own drop, and the extra draw."""
    from bridge.lootplan import curio_options, value_at

    bare = next(o for o in curio_options("travellers_tent_tutorial", 1, "weald")
                if o.item is None)
    lit = value_at(bare, 100.0, 1, "weald")
    dark = value_at(bare, 0.0, 1, "weald")
    assert dark.worth() > lit.worth() * 1.5, (lit.line(), dark.line())


def test_light_is_a_budget_and_a_short_pack_says_so():
    """Fran, 2026-09-06: *"you cant re-torch to get better light level if you
    dont have enough torches"*. A relight is +25, so a floor of 76 is simply
    out of reach from the dark with one torch -- and the plan has to report
    the fight it could not light rather than pretending."""
    from bridge.lootplan import plan_loot

    dungeon = _old_road()
    broke = plan_loot(dungeon, _quest(), here="rooA", torch=10.0,
                      inventory={"torch": 1})
    assert broke.torches_spent == 0, "one torch cannot reach the floor"
    assert broke.dark_battles, "a fight below the floor has to be reported"

    rich = plan_loot(dungeon, _quest(), here="rooA", torch=10.0,
                     inventory={"torch": 4})
    assert rich.torches_spent > 0
    assert not rich.dark_battles


def test_the_torch_is_topped_up_BEFORE_the_light_collapses():
    """Since 2026-09-23 a new tile costs 6 at ANY light (measured live), so
    this long captured map cannot be lit on 8 torches -- the plan spends them
    and names the fights it could not light. With a big enough pack it lights
    every fight, relighting before each one that would otherwise start dark."""
    from bridge.dungeon_map import read_dungeon_map
    from bridge.lootplan import plan_loot
    from bridge.route import read_quest

    found = sorted(CAPTURES.glob("raidsnaps4/*_persist.map.json"))
    if not found:
        pytest.skip("no captured maps")
    dungeon = read_dungeon_map(found[0])
    if dungeon is None:
        pytest.skip("that capture did not decode")
    quest = read_quest(str(found[0]).replace("map.json", "raid.json"))
    short = plan_loot(dungeon, quest, torch=100.0, inventory={"torch": 8})
    assert short.dark_battles and short.torches_left == 0
    plan = plan_loot(dungeon, quest, torch=100.0, inventory={"torch": 24})
    assert not plan.dark_battles, plan.dark_battles


# -- properties that must hold on every map --

def test_nothing_is_resolved_twice():
    """An exact cover of a branching map doubles back through areas it has
    already cleared. Without a guard the plan opened one crate twice and
    counted the loot twice with it."""
    from bridge.dungeon_map import read_dungeon_map
    from bridge.lootplan import plan_loot
    from bridge.route import read_quest

    found = sorted(CAPTURES.glob("raidsnaps*/*_persist.map.json"))[:12]
    if not found:
        pytest.skip("no captured maps")
    for path in found:
        dungeon = read_dungeon_map(path)
        if dungeon is None:
            continue
        quest = read_quest(str(path).replace("map.json", "raid.json"))
        plan = plan_loot(dungeon, quest, torch=100.0,
                         inventory={"torch": 4, "shovel": 2, "skeleton_key": 2})
        resolved = [(s.area, s.tile, s.action) for s in plan.stops
                    if s.action in ("take", "fight", "skip")]
        assert len(resolved) == len(set(resolved)), (path.name, resolved)


def test_a_deferred_curio_is_always_collected():
    """A DEFER the walk home never reaches is loot thrown away, and it is
    silent -- the plan still reads as though it took everything. It happened
    twice while this was being written, both times for a different reason."""
    from bridge.dungeon_map import read_dungeon_map
    from bridge.lootplan import plan_loot
    from bridge.route import read_quest

    found = sorted(CAPTURES.glob("raidsnaps*/*_persist.map.json"))[:12]
    if not found:
        pytest.skip("no captured maps")
    for path in found:
        dungeon = read_dungeon_map(path)
        if dungeon is None:
            continue
        quest = read_quest(str(path).replace("map.json", "raid.json"))
        plan = plan_loot(dungeon, quest, torch=100.0, inventory={"torch": 4})
        deferred = {(s.area, s.tile) for s in plan.stops if s.action == "defer"}
        taken = {(s.area, s.tile) for s in plan.stops if s.action == "take"}
        assert deferred <= taken, (path.name, deferred - taken)


def test_a_plan_from_a_CORRIDOR_sees_the_same_map():
    """`_all_pairs` BFSs room to room, so a corridor is not a node in it and
    planning from one returned an empty cover -- the block reported "0g still
    on the map" for the whole length of a hallway. That is where the party
    spends most of a raid, so it was the common case rather than an edge one.

    Found by replaying a capture through `bridge/run.py`, not by a test, which
    is why the assertion is that every start on one walk agrees.
    """
    from bridge.dungeon_map import read_dungeon_map
    from bridge.lootplan import plan_loot
    from bridge.route import read_quest

    found = sorted(CAPTURES.glob("raidsnaps4/*_persist.map.json"))
    if not found:
        pytest.skip("no captured maps")
    dungeon = read_dungeon_map(found[0])
    if dungeon is None:
        pytest.skip("that capture did not decode")
    quest = read_quest(str(found[0]).replace("map.json", "raid.json"))

    corridors = sorted(dungeon.corridors)
    assert corridors, "the capture has no corridors to test with"
    for area in corridors[:4]:
        plan = plan_loot(dungeon, quest, here=area, torch=100.0,
                         inventory={"torch": 4})
        assert plan.value.worth() > 0.0, f"{area} reported nothing on the map"
        # ...and it starts where the party actually is.
        assert plan.walk[0] == area


def test_every_plan_ends_at_the_entrance():
    """A completed quest is handed in at the entrance, which is why the walk
    home is not optional and why deferring onto it is free."""
    from bridge.dungeon_map import read_dungeon_map
    from bridge.lootplan import plan_loot

    found = sorted(CAPTURES.glob("raidsnaps*/*_persist.map.json"))[:8]
    if not found:
        pytest.skip("no captured maps")
    for path in found:
        dungeon = read_dungeon_map(path)
        if dungeon is None:
            continue
        plan = plan_loot(dungeon, None, torch=100.0, inventory={"torch": 4})
        assert plan.stops[-1].action == "finish"
        assert plan.stops[-1].area == dungeon.entrance_id


def test_the_crest_convention_does_not_decide_the_route():
    """`CREST_GOLD` is the one invented number in the value model, and this is
    what makes it safe: sweep it over three orders of magnitude and the Old
    Road plan must not move. A map whose plan DOES flip on this is a map the
    data cannot decide, and has to be reported as undecided rather than
    resolved by a constant."""
    from bridge.lootplan import plan_loot

    dungeon, quest = _old_road(), _quest()
    shapes = set()
    for crest_gold in (0.0, 1.0, 10.0, 100.0, 1000.0):
        plan = plan_loot(dungeon, quest, here="rooA", torch=100.0,
                         inventory={"skeleton_key": 1}, crest_gold=crest_gold)
        shapes.add(tuple((s.action, s.area, s.tile, s.item)
                         for s in plan.stops))
    assert len(shapes) == 1, f"the plan moved with CREST_GOLD: {len(shapes)} shapes"


# -- the dark is free once there is nothing left to fight ------------------


def test_a_plan_with_NO_FIGHT_never_defers():
    """**A deferral trades light for a later revisit, and with no fight left
    there is nothing to trade.** The only reason to carry a torch is the risk
    it buys off on the battles ahead -- so a plan holding none can go dark
    where it stands, which makes every curio already-dark and leaves nothing
    to defer to.

    Live 2026-09-06 the old behaviour was an infinite loop, not a lost point
    of value: the plan deferred the tent (muting the curio layer, by design),
    scheduled the snuff at the end of a walk that existed only to be
    elsewhere while going dark, and the route layer walked the other way. The
    party re-planned and crossed the same corridor until it was killed.
    """
    from bridge.lootplan import plan_loot

    dungeon = _old_road()
    for here in ("rooA", "coAB", "rooB"):
        for torch in (100.0, 56.0, 0.0):
            plan = plan_loot(dungeon, _quest(), here=here, torch=torch,
                             inventory={})
            if any(s.action == "fight" for s in plan.stops):
                continue
            defers = [s.line() for s in plan.stops if s.action == "defer"]
            assert not defers, (here, torch, defers)


def test_the_snuff_still_WAITS_when_a_fight_is_ahead():
    """The other side of it, and the one that matters for survival: darkness
    hands the monsters +12.5 accuracy, +25% damage and +5 crit. Going dark
    early to open a curio better would buy loot with exactly the risk the
    torch floor exists to refuse."""
    from bridge.lootplan import plan_loot

    plan = plan_loot(_old_road(), _quest(), here="rooA", torch=100.0,
                     inventory={"torch": 4})
    order = [s.action for s in plan.stops]
    if "snuff" in order and "fight" in order:
        assert order.index("fight") < order.index("snuff") + 2, order
        # and the FIRST fight is lit -- Fran's rule, and the acceptance test
        # above pins the whole route
        first = next(s for s in plan.stops if s.action == "fight")
        assert first.torch >= 76.0, first.line()


# -- a deferral is a promise (2026-09-06, live) ----------------------------


@pytest.mark.parametrize("here", ["rooA", "coAB", "rooB"])
def test_every_DEFER_has_something_that_comes_back_for_it(here):
    """**The invariant that separates `defer` from `skip`.** Skip means never;
    defer means not at THIS light, and it is only honest while a later stop
    collects it. A plan that defers with no collector tells `run.py` to walk
    past the curio for a return trip that is not in the plan.

    Live: the party stood in `coAB` with the objective met and the tent under
    its feet, deferred it, and went home 558 gold poorer. The deferral had
    been correct one area earlier -- what expired was the return leg that
    justified it, and nothing re-examined the promise when the route changed.
    """
    from bridge.lootplan import plan_loot

    plan = plan_loot(_old_road(), _quest(), here=here, torch=100.0,
                     inventory={"torch": 4})
    takes = {(s.area, s.tile) for s in plan.stops if s.action == "take"}
    orphans = [s for s in plan.stops
               if s.action == "defer" and (s.area, s.tile) not in takes]
    assert not orphans, [s.line() for s in orphans]


def test_a_deferral_with_no_way_back_becomes_a_TAKE_not_a_SKIP():
    """Re-pricing must not quietly throw the loot away either. Standing on the
    tent with the quest done and only the entrance left, the answer is to take
    it here -- worth less than in the dark, worth far more than nothing."""
    from bridge.lootplan import plan_loot

    plan = plan_loot(_old_road(), _quest(), here="coAB", torch=70.0,
                     inventory={})
    tent = [s for s in plan.stops if s.curio_id == "travellers_tent_tutorial"]
    assert tent, "the tent vanished from the plan entirely"
    assert tent[-1].action in ("take", "defer"), tent[-1].line()
    if tent[-1].action == "take":
        assert not tent[-1].value.empty, "taken for nothing"


# -- what the dark can and cannot multiply (2026-09-06) -------------------
#
# Fran, after watching a crypts raid defer a wall sconce: *"healing or
# boosting curios like the fountain should be used during the dungeon, as
# they aren't loot curios, same for curios that mostly produces provisions
# like torches, food or blood"*. Both halves are already in the data, and the
# deferral rule was reading neither -- it deferred on POSITION alone.


def test_a_HEALING_curio_is_never_held_back_for_the_dark():
    """`holy_fountain` reads `loot_chance` 0.50 bare-handed and 0.00 for its
    holy water: half of it is a heal or a buff, worth most while the party is
    hurt and worth nothing banked at the entrance."""
    from bridge.lootplan import pays_for_the_dark

    assert pays_for_the_dark("holy_fountain", 1, "crypts") is False


def test_a_PROVISION_curio_is_never_held_back_either():
    """`sconce` prices at 5 gold and all five of them are a torch. A torch
    taken on the way home is never lit."""
    from bridge.lootplan import curio_options, pays_for_the_dark

    bare = next(o for o in curio_options("sconce", 1, "crypts") if o.item is None)
    assert bare.loot_chance == 1.0, "it does draw a loot table"
    assert bare.base.supplies == bare.base.gold > 0,         "and every gold of it is a provision"
    assert pays_for_the_dark("sconce", 1, "crypts") is False


def test_an_ORDINARY_loot_curio_still_is():
    """The rule has to leave the case the whole module was built on alone --
    the Old Road tent is a pure loot draw with no provisions in it."""
    from bridge.lootplan import pays_for_the_dark

    assert pays_for_the_dark("travellers_tent_tutorial", 1, "crypts") is True


def test_the_supply_share_is_a_SUBSET_of_gold_not_an_addition():
    """Every existing caller reads `worth()` and must see the same number:
    `supplies` shadows `gold`, it does not add to it."""
    from data.loot_loader import LootValue

    one = LootValue(gold=10.0, supplies=4.0)
    assert one.worth() == LootValue(gold=10.0).worth()
    assert (one + one).supplies == 8.0
    assert (one * 0.5).supplies == 2.0


def test_a_heal_curio_in_a_REVISITED_corridor_is_taken_on_the_way_out():
    """The end-to-end shape of it: position alone used to be the whole test,
    so a fountain standing in a corridor the walk home passes was deferred to
    torch 0 for a bonus the dark cannot pay on a heal."""
    from bridge import lootplan

    seen = {}
    real = lootplan.pays_for_the_dark

    def spy(curio_id, *a, **k):
        seen[curio_id] = real(curio_id, *a, **k)
        return seen[curio_id]

    lootplan.pays_for_the_dark = spy
    try:
        plan = lootplan.plan_loot(_old_road(), _quest(), here="rooA",
                                  torch=100.0, inventory={"torch": 4})
    finally:
        lootplan.pays_for_the_dark = real
    # **THE PREMISE MOVED, AND THE GUARD FOLLOWS IT.** Deferral is priced on
    # the party walking back to the entrance, and since 2026-09-21 it does
    # not: the exit is the quest banner's red seal, which ends the raid from
    # wherever the party stands. `lootplan.WALKS_HOME` is that fact, and with
    # it False nothing is deferred at all -- so the rule below has nothing to
    # consult, and what must hold instead is that nothing was deferred.
    if not lootplan.WALKS_HOME:
        assert not [s for s in plan.stops if s.action == "defer"], (
            "the party does not walk home, so a deferral is a forfeit -- "
            "live 2026-09-21 that lost a strongbox and 880 gold")
        return
    assert seen, "the deferral rule never asked what the curio pays"
    for stop in plan.stops:
        if stop.action == "defer":
            assert seen.get(stop.curio_id) is not False,                 f"{stop.curio_id} was deferred and the dark pays it nothing"


if __name__ == "__main__":                                  # pragma: no cover
    pytest.main([__file__, "-v"])
