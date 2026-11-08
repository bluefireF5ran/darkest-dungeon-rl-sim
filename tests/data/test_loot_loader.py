"""`data/loot_loader.py` -- the darkness economy, both directions.

These pin the numbers the planner spends, and each one guards a specific way
the model went wrong while it was being built.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from data.paths import GAME_DIR


def _need_game():
    if not GAME_DIR.exists():
        pytest.skip("the game is not installed at the hardcoded path")
    return str(GAME_DIR)


def test_the_main_loot_file_is_actually_read():
    """`loot/loot.json` does NOT match `loot/*.loot.json`.

    The first glob used the suffix and silently lost every base table, which
    showed up two layers away as the whole model reporting "unpriced". A
    missing file here is not an error anywhere -- it is a zero -- so the only
    defence is asserting the tables arrived.
    """
    from data.loot_loader import load_loot_tables

    tables = load_loot_tables(_need_game())
    codes = tables.codes
    # From `loot.json`...
    assert {"B", "C", "G", "H", "T", "P"} <= codes
    # ...and from `common_overrides.loot.json`, which is where the two the
    # darkness bonus actually draws on live.
    assert {"A", "S"} <= codes


def test_a_draw_resolves_through_nested_tables_to_real_gold():
    """B is 3 parts C and 1 part G, and C is gold from 25 to 1000."""
    from data.loot_loader import load_loot_tables

    tables = load_loot_tables(_need_game())
    value = tables.expected("C", 1, "weald", _need_game())
    assert 25.0 <= value.gold <= 1000.0, value.gold
    assert not value.unpriced, value.unpriced

    nested = tables.expected("B", 1, "weald", _need_game())
    assert nested.gold > 0
    assert not nested.unpriced, nested.unpriced


def test_the_bottom_bracket_draws_TWICE_on_a_battle():
    """The fact the `rules.json` summary hides.

    `+30% gold and +12 treasure draws` reads as one bonus getting bigger. What
    the loot file says is that the chest CHANCE climbs 75 -> 95 while the
    battle chance does not move at all -- it draws two codes instead of one.
    That doubling is most of why the snuff is worth taking.
    """
    from data.loot_loader import bonus_for

    game = _need_game()
    assert bonus_for("battle", 100.0, game).chance == 0.0
    dark = bonus_for("battle", 0.0, game)
    assert dark.chance == 0.75
    assert len(dark.codes) == 2, dark.codes
    assert bonus_for("chest", 0.0, game).chance == 0.95
    assert len(bonus_for("chest", 0.0, game).codes) == 1


def test_darkness_is_worth_much_more_on_a_fight_than_at_radiant():
    """The number the whole plan turns on. Direction and rough size, not a
    decimal -- the tables are difficulty- and dungeon-keyed and a patch may
    move them."""
    from data.loot_loader import darkness_value

    game = _need_game()
    lit = darkness_value("battle", 100.0, 1, "weald", game)
    dark = darkness_value("battle", 0.0, 1, "weald", game)
    assert lit.worth() == 0.0
    assert dark.worth() > 100.0, dark.line()
    assert darkness_value("chest", 0.0, 1, "weald", game).worth() > 0.0


def test_gold_is_the_only_thing_the_gold_multiplier_scales():
    """`loot_increase_gold` and `loot_increase_treasure_draws` are two columns.

    Scaling the whole value by the first would inflate heirloom and trinket
    counts that the darkness table says nothing about.
    """
    from data.loot_loader import LootValue

    base = LootValue(gold=100.0, heirlooms={"crest": 2.0}, trinkets=1.0)
    scaled = base.with_gold_multiplier(1.30)
    assert scaled.gold == pytest.approx(130.0)
    assert scaled.heirlooms == {"crest": 2.0}
    assert scaled.trinkets == 1.0


def test_an_unknown_code_is_reported_and_never_priced_as_zero():
    """The `_effect_value` precedent: 0.0 has to mean "worth nothing", so "I
    cannot resolve this" needs somewhere else to go."""
    from data.loot_loader import load_loot_tables

    value = load_loot_tables(_need_game()).expected("NO_SUCH_TABLE", 1, "weald")
    assert value.gold == 0.0
    assert value.unpriced == {"NO_SUCH_TABLE": 1.0}


def test_heirlooms_are_counted_rather_than_sold():
    """Every heirloom ships `sell_gold_value 0`, which is the game saying they
    do not convert. Pricing them at 0 would make an heirloom draw worthless
    instead of unpriced, so they get their own column."""
    from data.loot_loader import item_sell_values

    prices = item_sell_values(_need_game())
    assert prices[("heirloom", "bust")] == 0.0
    # ...while gems really do have a price, and it is exact.
    assert prices[("gem", "ancient_idol")] == 4500.0
    assert prices[("supply", "shovel")] == 25.0


def test_a_new_tile_burns_six_and_a_cleared_one_burns_one():
    """`tile_light_loss` keys are TILE states, not light levels (corrected
    2026-09-23 from the live logs: a new corridor tile at torch 100 costs 6,
    and a re-walked one about 1). The light level does not change either."""
    from data.loot_loader import walk_costs

    game = _need_game()
    for torch in (100.0, 76.0, 75.0, 0.0):
        costs = walk_costs(torch, game)
        assert costs.light_loss == 6.0, torch
        assert costs.revisit_light_loss == 1.0, torch


def test_walking_back_through_a_cleared_corridor_repopulates_it():
    """`corridor_return_content` -- the cost of a dark homeward sweep, and the
    reason a detour to fetch something is not free."""
    from data.loot_loader import walk_costs

    game = _need_game()
    lit, dark = walk_costs(100.0, game), walk_costs(0.0, game)
    assert lit.return_battle == pytest.approx(0.05)
    assert dark.return_battle == pytest.approx(0.10)
    assert dark.return_any > lit.return_any
    # Backing up is the expensive direction for stress too.
    assert dark.stress_backing > dark.stress_forward * 4


def test_the_crest_convention_is_isolated_to_one_method():
    """`CREST_GOLD` is the module's only invented number, so nothing may READ
    it except `worth()`. A second reader is how a convention becomes a fact.

    Statically, over the AST, because a text scan counts the docstring that
    explains the constant and would pass or fail on prose.
    """
    import ast

    source = (Path(__file__).resolve().parents[2]
              / "data" / "loot_loader.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    readers = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for inner in ast.walk(node):
            if isinstance(inner, ast.Name) and inner.id == "CREST_GOLD"                     and isinstance(inner.ctx, ast.Load):
                readers.add(node.name)
    assert readers <= {"worth", "main"}, readers


if __name__ == "__main__":                                  # pragma: no cover
    pytest.main([__file__, "-v"])
