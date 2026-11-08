"""The trinket tier lists, read out of five images into ids.

Fran, 2026-10-01: bosses are worth running for a very rare that gives *"una
ventaja real"*, and these lists are how that is judged.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from data import trinket_tiers as TT

DATA = json.loads(TT.TIERS_FILE.read_text(encoding="utf-8"))


def test_every_tile_was_placed_and_none_was_guessed():
    assert DATA["rejected"] == []
    assert len(DATA["entries"]) == 187
    assert {e["sheet"] for e in DATA["entries"].values()} == {
        "very_common", "common", "uncommon", "rare", "very_rare"}


def test_a_base_trinket_sits_on_the_sheet_of_its_own_rarity():
    """The matcher never sees the rarity, so agreement with the game's entries
    files is an independent check on every match it made."""
    for tid, e in DATA["entries"].items():
        if e["rarity"] in TT_SHEET_RARITIES:
            assert e["rarity"] == e["sheet"], tid


TT_SHEET_RARITIES = {"very_common", "common", "uncommon", "rare", "very_rare"}


def test_the_boss_rewards_fran_passed_on_are_not_worth_a_boss():
    """His first recorded session: six boss rewards offered, none taken."""
    skipped = ["focus_ring", "cc_pagan_talisman", "dark_tambourine",
               "antiq_5", "sacred_scroll", "berserk_mask"]
    assert not any(TT.worth_a_boss(t) for t in skipped)


def test_the_top_of_the_very_rare_sheet_is_worth_a_boss():
    for tid in ("blasphemous_vial", "demons_cauldron", "poisoning_buckle",
                "tough_ring", "fortifying_garlic"):
        assert TT.worth_a_boss(tid), tid


def test_torchless_only_and_unlisted_are_not_an_advantage():
    assert TT.tier("hells_hairpin")["torchless"]
    assert not TT.worth_a_boss("hells_hairpin")
    assert not TT.worth_a_boss("no_such_trinket")
