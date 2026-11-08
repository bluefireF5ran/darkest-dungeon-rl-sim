"""The raid pack: reading it, and what may be thrown out of it.

A discard is the only input in the bridge that DESTROYS something, and a raid
cannot be undone, so nearly everything here pins what must NOT be proposed.

"The game prices it at zero to buy and zero to sell" was tried as the rule
**twice**, and was wrong both times. The tests that pin each failure are the
load-bearing ones here:

* `test_the_things_a_raid_exists_for_are_never_proposed` -- run against the very
  first capture, the price test proposed discarding an heirloom portrait and
  625 gold. Both are 0/0, because heirlooms are a town currency and gold is
  gold.
* `test_the_same_item_is_kept_when_its_class_is_present` -- that left dog treats
  as the one "free" item, and Fran caught it mid-raid. Dog treats are +15%
  accuracy and +50% damage for three rounds
  (`effects/base.effects.darkest`), handed out because the party brought a
  Houndmaster (`provision.json`). The bridge was about to bin them.

A gold price says what a shop will pay and nothing about what an item is worth
in a dungeon. The rule now consults the effects file and the class-item table,
which leaves exactly one discardable case: a class item whose class is absent.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from bridge.inventory import (                                    # noqa: E402
    DISCARDABLE_TYPES, NEVER_DISCARD_IDS, RAID_SLOTS, Slot, describe,
    item_defs, pack_from_raid, plan_discards, spare_stack_slots,
)

TRACES = Path(__file__).resolve().parents[2] / "archive" / "traces"


def slots(*specs):
    return [Slot(slot=i, type=t, id=n, amount=a)
            for i, (t, n, a) in enumerate(specs)]


def fill(n, *, head=()):
    out = list(head)
    pad = [("supply", "torch", 8), ("gem", "ruby", 1), ("gold", "", 400),
           ("heirloom", "crest", 3), ("supply", "shovel", 4)]
    while len(out) < n:
        out.append(pad[len(out) % len(pad)])
    return slots(*out[:n])


# --- the game's own numbers ---

def test_every_item_file_loads():
    d = item_defs()
    assert ("supply", "torch") in d
    assert ("provision", "") in d, "food's id is genuinely the empty string"
    assert ("gold", "") in d
    assert ("gem", "ruby") in d
    assert ("quest_item", "holy_relic") in d


def test_stack_limits_come_from_the_game_not_from_here():
    d = item_defs()
    assert d[("supply", "torch")].stack_limit == 8
    assert d[("supply", "dog_treats")].stack_limit == 2
    assert d[("provision", "")].stack_limit == 12


def test_six_supplies_carry_a_combat_effect():
    """`effects/base.effects.darkest` is what stops a useful item being called
    chaff. Dog treats' entry is +15% ACC and +50% damage for three rounds --
    the fact the first two versions of the discard rule did not consult."""
    d = item_defs()
    withfx = {k[1] for k, v in d.items() if k[0] == "supply" and v.has_effect}
    assert withfx == {"bandage", "antivenom", "medicinal_herbs",
                      "holy_water", "laudanum", "dog_treats"}


def test_class_items_come_from_provision_json():
    d = item_defs()
    assert d[("supply", "dog_treats")].class_item == "houndmaster"
    assert d[("supply", "antivenom")].class_item == "plague_doctor"
    assert d[("supply", "shovel")].class_item == "grave_robber"
    assert d[("supply", "torch")].class_item == ""


def test_nothing_at_all_is_chaff_for_a_full_party():
    """The honest conclusion: every supply either sells, does something, or
    opens something. With every class present there is no free discard."""
    d = item_defs()
    everyone = frozenset({"houndmaster", "plague_doctor", "grave_robber"})
    dead = {k[1] for k, v in d.items() if v.is_dead_weight(everyone)}
    assert dead == set()


# --- what may be discarded ---

def test_a_class_item_is_dead_weight_without_its_class():
    """The one surviving case. Dog treats with no Houndmaster cannot be eaten
    by anybody, at any price."""
    pack = fill(RAID_SLOTS, head=[("supply", "dog_treats", 2)])
    plan = plan_discards(pack, want=1, party_classes=frozenset({"crusader"}))
    assert [x.slot for x in plan.discards] == [0]
    assert "houndmaster" in plan.discards[0].why


def test_the_same_item_is_kept_when_its_class_is_present():
    """Reported live by Fran, and the reason this rule was rewritten: the
    party HAD a Houndmaster and the bridge was about to bin its buff."""
    pack = fill(RAID_SLOTS, head=[("supply", "dog_treats", 2)])
    plan = plan_discards(pack, want=1,
                         party_classes=frozenset({"houndmaster", "vestal"}))
    assert plan.discards == []
    assert plan.shortfall == 1


def test_an_unknown_party_proposes_nothing():
    """None is not an empty set. 'I could not read the roster' must never read
    as 'there is no Houndmaster' -- the failure would be destructive."""
    pack = fill(RAID_SLOTS, head=[("supply", "dog_treats", 2)])
    plan = plan_discards(pack, want=1, party_classes=None)
    assert plan.discards == []
    assert any("unknown" in n for n in plan.notes)


def test_the_things_a_raid_exists_for_are_never_proposed():
    """Gold, heirlooms, gems and journal pages are all 0/0 or unpriced. None of
    them is a discard candidate; only `supply` is."""
    pack = slots(("gold", "", 625), ("heirloom", "portrait", 1),
                 ("heirloom", "crest", 12), ("gem", "ruby", 1),
                 ("gem", "trapezohedron", 1), ("journal_page", "9", 1),
                 ("trinket", "stun_stone", 1), ("estate", "the_blood", 1),
                 ("quest_item", "holy_relic", 1))
    plan = plan_discards(pack, want=4, slots_total=len(pack),
                         party_classes=frozenset())
    assert plan.discards == []
    assert plan.shortfall == 4


def test_firewood_is_never_proposed():
    """0/0 and no combat effect, so only the explicit exclusion saves it -- and
    it is what pays for a campfire."""
    pack = fill(RAID_SLOTS, head=[("supply", "firewood", 1)])
    assert plan_discards(pack, want=1, party_classes=frozenset()).discards == []


def test_the_curio_keys_and_the_torch_are_never_proposed():
    """A shovel is a Grave Robber's class item AND clears obstacles; a skeleton
    key opens locked curios; a torch is the light. None has an effect entry, so
    each is held by name."""
    for item in ("shovel", "skeleton_key", "torch"):
        pack = fill(RAID_SLOTS, head=[("supply", item, 1)])
        plan = plan_discards(pack, want=1, party_classes=frozenset())
        assert plan.discards == [], f"proposed discarding {item}"


def test_an_unknown_type_is_left_alone():
    """A positive gate, so something the game adds later defaults to safe."""
    pack = fill(RAID_SLOTS, head=[("mystery", "whatever", 1)])
    assert plan_discards(pack, want=1,
                         party_classes=frozenset()).discards == []
    assert "mystery" not in DISCARDABLE_TYPES


def test_it_stops_once_it_has_enough():
    pack = fill(RAID_SLOTS, head=[("supply", "dog_treats", 2)] * 2)
    none = frozenset({"crusader"})
    assert len(plan_discards(pack, want=1, party_classes=none).discards) == 1
    assert len(plan_discards(pack, want=2, party_classes=none).discards) == 2


def test_free_space_counts_before_any_discard():
    pack = fill(10)
    plan = plan_discards(pack, want=3, party_classes=frozenset())
    assert plan.free_before == RAID_SLOTS - 10
    assert plan.discards == [], "discarded when there was already room"
    assert plan.enough


def test_a_shortfall_is_reported_not_papered_over():
    pack = fill(RAID_SLOTS, head=[("supply", "dog_treats", 2)])
    plan = plan_discards(pack, want=3, party_classes=frozenset({"crusader"}))
    assert len(plan.discards) == 1 and plan.shortfall == 2
    assert not plan.enough
    assert "short" in str(plan)


# --- merging, which is free and is only reported ---

def test_split_stacks_are_reported():
    pack = slots(("supply", "torch", 6), ("supply", "torch", 8),
                 ("supply", "torch", 2))
    notes = spare_stack_slots(pack)
    assert len(notes) == 1
    assert "frees 1 slot" in notes[0]


def test_stacks_that_cannot_merge_are_not_reported():
    """6+1 bandages do not fit one slot: the limit is 6."""
    assert spare_stack_slots(slots(("supply", "bandage", 6),
                                   ("supply", "bandage", 1))) == []


def test_merging_is_never_planned_as_an_action():
    """It needs a click-drag the actuator cannot do. Reported, not executed."""
    pack = fill(RAID_SLOTS, head=[("supply", "torch", 1)] * 3)
    plan = plan_discards(pack, want=1)
    assert plan.notes and plan.discards == []


# --- against the real corpus ---

@pytest.mark.skipif(not TRACES.exists(), reason="no capture corpus")
def test_no_capture_ever_yields_a_risky_discard():
    """Swept over every raid save in `archive/traces/`, with the WORST-CASE
    party for the rule -- no class present, so every class item counts as dead
    weight. Anything it proposes must still be genuinely unusable.

    It proposes nothing, because none of these raids carried a class item its
    class had left behind. Safe, and on this corpus inert; that is why
    `--full-pack` exists rather than a cleverer discard rule.
    """
    from data.dson import parse_file
    defs = item_defs()
    nobody = frozenset()
    seen = proposed = 0
    for f in sorted(TRACES.glob("raidsnaps*/*raid*.json")):
        try:
            pack = pack_from_raid(parse_file(f))
        except Exception:                            # noqa: BLE001
            continue
        if not pack:
            continue
        seen += 1
        for d in plan_discards(pack, want=1, defs=defs,
                               party_classes=nobody).discards:
            proposed += 1
            slot = next(s for s in pack if s.slot == d.slot)
            assert slot.type in DISCARDABLE_TYPES
            assert defs[slot.key].is_dead_weight(nobody)
    assert seen > 300, f"corpus did not load ({seen} packs)"
    assert proposed == 0


@pytest.mark.skipif(not TRACES.exists(), reason="no capture corpus")
def test_a_real_save_decodes_into_contiguous_slots():
    """`party.inventory.items` children are named by slot, and the pack packs
    from 0 with no gaps -- which is what lets slot n map to grid cell n."""
    from data.dson import parse_file
    f = sorted(TRACES.glob("raidsnaps/*raid*.json"))[-1]
    pack = pack_from_raid(parse_file(f))
    assert pack
    assert [s.slot for s in pack] == list(range(len(pack)))
    assert len(pack) <= RAID_SLOTS
    assert describe(pack).startswith(f"pack: {len(pack)}/{RAID_SLOTS}")


# --- merging split stacks: the free slots nobody was taking -----------------

def _slot(n, item, amount, kind="supply"):
    from bridge.inventory import Slot
    return Slot(slot=n, type=kind, id=item, amount=amount)


def _defs(limit=8, item="torch", kind="supply"):
    from bridge.inventory import ItemDef
    try:
        return {(kind, item): ItemDef(type=kind, id=item, stack_limit=limit)}
    except TypeError:                       # a wider ItemDef; build from real
        from bridge.inventory import item_defs
        return item_defs()


def test_the_live_pack_that_stopped_a_raid_merges_in_one_drag():
    """2026-09-05, first live raid to reach a full pack. The layer PRINTED
    "2 stacks of torch hold 3 of a 8 limit - merging them frees 1 slot(s), for
    free" and then, on the next line, "nothing in the pack is free to discard"
    and paused. This is that pack."""
    from bridge.inventory import plan_stack_merges, item_defs
    pack = [_slot(8, "torch", 2), _slot(9, "torch", 1)]
    got = plan_stack_merges(pack, item_defs())
    assert len(got) == 1, [m.label() for m in got]
    assert (got[0].src, got[0].dst, got[0].total) == (9, 8, 3)


def test_only_a_pour_that_EMPTIES_its_source_is_emitted():
    """THE ACKNOWLEDGEMENT IS THE PACK GETTING SHORTER, so a partial pour is
    unverifiable: it tops the destination up, leaves the source holding the
    remainder, and the pack comes back the same length -- indistinguishable
    from an input that never landed."""
    from bridge.inventory import plan_stack_merges, item_defs
    defs = item_defs()
    for pack in ([_slot(0, "torch", 8), _slot(1, "torch", 8)],
                 [_slot(0, "torch", 7), _slot(1, "torch", 6)]):
        for merge in plan_stack_merges(pack, defs):
            src = next(s for s in pack if s.slot == merge.src)
            dst = next(s for s in pack if s.slot == merge.dst)
            limit = defs[src.key].stack_limit
            assert src.amount + dst.amount <= limit, merge.label()


def test_every_emitted_drag_frees_exactly_one_slot():
    """Which is what makes the count a sound test. Three stacks of 6, 5 and 3
    against a limit of 8 need two slots, so exactly one drag is available."""
    from bridge.inventory import plan_stack_merges, item_defs, spare_stack_slots
    defs = item_defs()
    pack = [_slot(0, "torch", 6), _slot(1, "torch", 5), _slot(2, "torch", 3)]
    got = plan_stack_merges(pack, defs)
    total = sum(s.amount for s in pack)
    limit = defs[pack[0].key].stack_limit
    needed = -(-total // limit)
    assert len(got) == len(pack) - needed == 1, [m.label() for m in got]
    # ...and it agrees with the description the layer has always printed.
    assert spare_stack_slots(pack, defs), "the note and the plan disagree"


def test_a_full_pair_of_stacks_is_left_alone():
    from bridge.inventory import plan_stack_merges, item_defs
    assert plan_stack_merges([_slot(0, "torch", 8), _slot(1, "torch", 8)],
                             item_defs()) == []
    assert plan_stack_merges([_slot(0, "torch", 3)], item_defs()) == []
    assert plan_stack_merges([], item_defs()) == []


def test_different_items_are_never_merged():
    """The key is (type, id). Pouring a bandage into a torch would destroy
    one of them, and there is no way back."""
    from bridge.inventory import plan_stack_merges, item_defs
    pack = [_slot(0, "torch", 1), _slot(1, "bandage", 1),
            _slot(2, "holy_water", 1)]
    assert plan_stack_merges(pack, item_defs()) == []


def test_the_note_and_the_plan_agree_on_every_real_pack():
    """`spare_stack_slots` has described these since the pack layer existed
    and `plan_stack_merges` now executes them. If the two ever disagree, the
    log says a slot is free and the actuator does not take it -- which is the
    state this whole change was fixing."""
    from bridge.inventory import (item_defs, plan_stack_merges,
                                  spare_stack_slots, read_pack)
    from pathlib import Path
    defs = item_defs()
    traces = Path(__file__).resolve().parents[2] / "archive" / "traces"
    seen = merged = 0
    for snap in sorted(traces.glob("raidsnaps*/*raid*.json")):
        try:
            pack = read_pack(str(snap))
        except Exception:                                    # noqa: BLE001
            continue
        if not pack:
            continue
        seen += 1
        described = bool(spare_stack_slots(pack, defs))
        planned = bool(plan_stack_merges(pack, defs))
        assert described == planned, (snap.name, described, planned)
        merged += planned
    # 399 packs, 34 of them mergeable. Both bounds matter: no packs read means
    # the corpus moved, and no MERGEABLE packs read means the two functions
    # agreed only by both saying nothing.
    assert seen > 300, seen
    assert merged > 20, merged


def test_a_surplus_provision_stack_is_offered_for_loot_room():
    """2026-09-27: 33 of 37 curios given up came after "no room". Only a stack
    wholly beyond the raid's reserve, and cheap, is offered."""
    from bridge.inventory import Slot, plan_surplus_discard
    pack = [Slot(0, "supply", "torch", 8), Slot(1, "supply", "bandage", 1),
            Slot(2, "supply", "antivenom", 2), Slot(3, "provision", "", 12),
            Slot(4, "supply", "medicinal_herbs", 1), Slot(5, "gold", "", 1750)]
    got = plan_surplus_discard(pack, {"food": 12, "medicinal_herbs": 1})
    # antivenom: 2 held, keep 1 (trigger) -> a stack of 2 is not all surplus;
    # bandage: keep 1 -> nothing; food and herbs reserved; torch never.
    assert got is None
    pack.append(Slot(6, "supply", "antivenom", 1))
    got = plan_surplus_discard(pack, {"food": 12, "medicinal_herbs": 1})
    assert got is not None and got.id == "antivenom"
    assert plan_surplus_discard(pack, {"food": 12, "medicinal_herbs": 1,
                                       "antivenom": 3}) is None


def test_a_full_pack_trades_away_its_cheapest_stack_of_value():
    """`--full-pack swap` (2026-10-05): the live bridge sat paused on a full
    pack all morning. A player drops the least valuable stack for the loot;
    the bridge does the same, but only below a cap, since it cannot see what
    the loot window holds."""
    from bridge.inventory import Slot, plan_value_discard
    pack = [Slot(0, "gold", "", 1750), Slot(1, "gold", "", 120),
            Slot(2, "heirloom", "crest", 2), Slot(3, "gem", "citrine", 1),
            Slot(4, "supply", "torch", 3)]
    d = plan_value_discard(pack)
    assert d is not None and d.slot == 1           # 120 gold, not the crests or the torch


def test_nothing_cheap_enough_means_no_swap():
    from bridge.inventory import Slot, plan_value_discard
    pack = [Slot(0, "gold", "", 1750), Slot(1, "gem", "ruby", 2),
            Slot(2, "supply", "torch", 8), Slot(3, "heirloom", "portrait", 1)]
    assert plan_value_discard(pack) is None         # portrait is 300g-equivalent
