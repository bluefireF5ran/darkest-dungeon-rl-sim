"""What clears the thing blocking the corridor.

A live raid died to this on 2026-09-05: the party stood on `coNO` tile 2 in the
Cove with four shovels in the pack, `W` opened the Shipwreck panel, and nothing
knew how to click it.

The reader is small; what these tests pin is that it is a *derivation* and not
a memory. Nowhere in `bridge/obstacles.py` does the word "shovel" appear as an
answer -- it appears once, as the value `shovel_obstacle` maps to -- and the
four base zones reach it through their own `.props.darkest`.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from bridge import obstacles                              # noqa: E402
from env.gym_env import GAME_DIR                          # noqa: E402

needs_game = pytest.mark.skipif(not Path(GAME_DIR).is_dir(),
                                reason="no game install")

BASE_ZONES = ("cove", "crypts", "warrens", "weald")


@needs_game
def test_every_base_zone_declares_exactly_one_obstacle_type():
    """The premise the whole derivation rests on. A zone that drew two
    different tools would be unanswerable from the save, because an obstacle
    tile carries `content == 4` and nothing else -- no `curio_id`, no
    `trap_id`. That is measured against a live map in `test_act.py`.
    """
    for zone in BASE_ZONES:
        types = obstacles.zone_obstacle_types(zone)
        assert len(types) == 1, (zone, types)


@needs_game
def test_the_four_base_zones_all_want_a_shovel():
    """Derived, not listed: cove draws `shipwreck`, crypts and warrens
    `rubble`, weald `thorny_thicket`, and every one of those inherits the base
    prop's `shovel_obstacle`."""
    for zone in BASE_ZONES:
        assert obstacles.tool_for_zone(zone) == "shovel", zone


@needs_game
def test_a_zone_whose_obstacles_DISAGREE_names_no_tool():
    """THE CASE THAT MAKES THIS A DERIVATION.

    Crimson Court's Courtyard draws `mist` (a torch obstacle) and `pillar`
    (a shovel one). The save cannot say which is on the tile, so the answer is
    None and the caller uses its hands -- which always works. Defaulting to
    the common tool would spend a shovel and leave the mist standing.
    """
    types = obstacles.zone_obstacle_types("courtyard")
    assert set(types) == {"mist", "pillar"}
    assert obstacles.tool_for_prop("mist") == "torch"
    assert obstacles.tool_for_prop("pillar") == "shovel"
    assert obstacles.tool_for_zone("courtyard") is None


@needs_game
def test_inheritance_is_RESOLVED_and_not_read_as_absent():
    """Only two shipped props declare an `interaction_type` of their own.
    Reading a missing one as "no tool" would make every base obstacle
    bare-handed -- which is the bug this file exists to avoid, wearing a
    different hat."""
    props = obstacles.obstacle_props()
    declared = {n for n, kind in props.items() if kind}
    assert declared == {"mist", "farm_mist"}, sorted(declared)
    for name in ("shipwreck", "rubble", "thorny_thicket"):
        assert props[name] is None
        assert obstacles.tool_for_prop(name) == "shovel"


@needs_game
def test_the_bare_handed_cost_is_read_off_the_base_prop():
    """`clear_on_failure: true` is why there is no "skip" here. The cost is
    reported so a log can say what it paid rather than announcing a free
    action."""
    cost = obstacles.bare_handed_cost()
    assert cost.max_hp_fraction == pytest.approx(-0.05)
    assert cost.torchlight == pytest.approx(-20.0)
    assert cost.effects == ("Stress 2",)
    assert "max HP" in cost.line() and "torch" in cost.line()


@needs_game
def test_an_obstacle_is_never_skipped():
    """The difference from a curio, and it is structural rather than a policy
    choice: the obstacle is standing in the corridor the party has to walk
    down. Every path returns "use" or "interact"."""
    cases = [("cove", {"shovel": 4}), ("cove", {}), ("courtyard", {"torch": 9}),
             ("", {}), ("not_a_zone", {"shovel": 1})]
    for zone, pack in cases:
        advice = obstacles.advise(zone, pack)
        assert advice.action in ("use", "interact"), (zone, advice)


@needs_game
def test_the_tool_is_only_taken_when_the_pack_HAS_it():
    with_it = obstacles.advise("cove", {"shovel": 4})
    assert with_it.action == "use" and with_it.item == "shovel"
    assert "4" in with_it.why
    without = obstacles.advise("cove", {"shovel": 0})
    assert without.action == "interact" and without.item is None
    assert "shovel" in without.why


@needs_game
def test_an_unknown_zone_degrades_to_hands_rather_than_raising():
    """This runs mid-raid off a save read that can fail for a dozen reasons.
    The fallback has to be the answer that always works, not an exception."""
    advice = obstacles.advise("no_such_zone", {"shovel": 4})
    assert advice.action == "interact"
    assert obstacles.zone_obstacle_types("no_such_zone") == ()
    assert obstacles.tool_for_prop("no_such_prop") is None


def test_the_zone_comes_from_game_json_and_not_raid_json():
    """`persist.raid.json` carries the party, the pack and the position and
    says nothing about the zone -- `raiddungeon` is one file over. Passing a
    path that does not exist has to answer "" rather than raise."""
    assert obstacles.zone_from_save(Path("nowhere") / "persist.raid.json") == ""


@needs_game
def test_the_answer_is_not_hardcoded_anywhere():
    """The claim this module makes about itself, as a test.

    `shovel` may appear only as the value `shovel_obstacle` maps to. If a
    later fix "helpfully" defaults an unknown zone to a shovel, the Courtyard
    starts spending them on mist.
    """
    src = Path(obstacles.__file__).read_text(encoding="utf-8")
    body = src.split('"""', 2)[-1]          # skip the module docstring
    code = "\n".join(line for line in body.splitlines()
                     if not line.lstrip().startswith("#"))
    assert code.count('"shovel"') == 1, code.count('"shovel"')


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
