"""Every slot a curated comp declares must actually reach the engine.

A comp JSON names its skills, trinkets, camp skills and quirks by DISPLAY name,
and `CompLoadoutLoader` resolves each against the game's own string table. Until
2026-08-27 every one of those lookups was exact with a **silent drop**, and a
hero with three quirks instead of five is perfectly playable -- nothing raises,
nothing logs, and the comp simply trains and scores weaker than it was written
to be.

Measured over `curated_comps/` the day it was found:

    quirks      138 of  703 slots equipped NOTHING   (19.6%)
    trinkets     42 of 1304
    camp skills  27 of 2578

`Corvids Eye` (79 slots) and `Corvids Grace` (57) were the whole of the quirk
loss: the string table spells them `Corvid's Eye` and `Corvid's Grace`, and the
export drops the apostrophe. Same shape as the trinket-display-name collision
already in CHANGELOG.md.

This file is the tripwire, and it is written so that the ONLY way to keep it
green is to keep the data resolvable. Two halves:

1. Nothing regresses -- quirks and camp skills resolve completely, trinkets
   resolve except for a declared list.
2. The declared list does not GROW, and every name on it is a genuine name
   difference rather than punctuation. The loader's net is deliberately
   orthographic only; inventing `Tempting Goblet -> The Tempting Goblet` in
   code would be a convention wearing game-data clothes.
"""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

import pytest

from tests._fixtures import get_store

REPO = pathlib.Path(__file__).resolve().parents[2]
CURATED = REPO / "metadata" / "COMPS" / "curated_comps"

# Display names in the comp files that match NOTHING in this install, with the
# closest shipped name where there is one. Each is a real difference -- a
# plural, a leading article, or a rename -- so it is Fran's to correct at the
# source. Counted 2026-08-27.
UNRESOLVED_TRINKETS = {
    "Hunter's Talons": "Hunter's Talon (singular)",
    "Tempting Goblet": "The Tempting Goblet (leading article)",
    "Cloak and Dagger": "no candidate in this install",
    "Eerie Eye": "no candidate in this install",
}
# `First Aid` is the game's own inconsistency, already recorded in CLAUDE.md:
# heroes.js says "First Aid" where the string table says "Wound Care", and both
# are `first_aid`.
UNRESOLVED_CAMP = {"First Aid": 'the string table calls it "Wound Care"'}


def _declared_and_resolved():
    store = get_store()
    want = {"quirk": [], "trinket": [], "camp": []}
    got = {"quirk": 0, "trinket": 0, "camp": 0}
    for path in sorted(CURATED.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        entry = store.comp_loadout_by_name(data.get("teamName", ""))
        if entry is None:
            continue
        raw = [h for h in data.get("heroes", []) if h.get("heroClass")]
        for rh, lh in zip(raw, entry.heroes):
            for pol in ("positive", "negative"):
                want["quirk"] += (rh.get("quirks") or {}).get(pol, [])
            got["quirk"] += len(lh.quirk_ids["positive"]) + len(lh.quirk_ids["negative"])
            want["trinket"] += [rh[k] for k in ("trinket1", "trinket2") if rh.get(k)]
            got["trinket"] += len(lh.trinket_ids)
            want["camp"] += rh.get("activeCampSkills", [])
            got["camp"] += len(lh.camp_skill_ids)
    return want, got


@pytest.fixture(scope="module")
def counts():
    return _declared_and_resolved()


def test_every_curated_quirk_slot_resolves(counts):
    """138 of 703 did not, and the comps read as two quirks lighter than written."""
    want, got = counts
    assert want["quirk"], "no curated comp declared a quirk -- the sweep found nothing"
    assert got["quirk"] == len(want["quirk"]), (
        f"{len(want['quirk']) - got['quirk']} quirk slots equip nothing. A dropped "
        "quirk is silent: the hero just plays weaker. Check the apostrophe first "
        "-- the string table says \"Corvid's Eye\", not \"Corvids Eye\".")


def test_every_curated_camp_skill_resolves_except_the_declared_one(counts):
    want, got = counts
    dead = len(want["camp"]) - got["camp"]
    allowed = sum(1 for n in want["camp"] if n in UNRESOLVED_CAMP)
    assert dead == allowed, (
        f"{dead} camp slots equip nothing against {allowed} declared. "
        f"Declared: {UNRESOLVED_CAMP}")


def test_unresolved_trinkets_do_not_grow(counts):
    want, got = counts
    dead = len(want["trinket"]) - got["trinket"]
    allowed = sum(1 for n in want["trinket"] if n in UNRESOLVED_TRINKETS)
    assert dead == allowed, (
        f"{dead} trinket slots equip nothing against {allowed} declared. A NEW "
        "unresolved name means either a typo in the comp or a trinket this "
        f"install does not ship. Declared: {sorted(UNRESOLVED_TRINKETS)}")


def test_the_net_is_ORTHOGRAPHIC_and_never_maps_one_name_onto_another():
    """The fallback may fix punctuation and spacing. It may NOT decide that
    `Tempting Goblet` means `The Tempting Goblet` -- that is a guess, and this
    project does not ship guessed mappings (see DEBUFF RES in ASSUMPTIONS.md).
    """
    from data.comp_loadout_loader import CompLoadoutLoader as L
    for name in UNRESOLVED_TRINKETS:
        assert L._punct_key(name) != L._punct_key("The " + name) or name.startswith("The"), name
    # Punctuation-only, so stripping letters must never happen.
    assert L._punct_key("Corvid's Eye") == L._punct_key("Corvids Eye") == "corvids eye"
    assert L._squash_key("Snake Skin") == L._squash_key("SNAKESKIN") == "snakeskin"
    assert L._punct_key("Self-Medicate") == L._punct_key("Self Medicate")
    # ...and a genuinely different name must still miss.
    assert L._punct_key("Hunter's Talons") != L._punct_key("Hunter's Talon")


def test_an_ambiguous_bucket_is_dropped_rather_than_guessed():
    """Two real names that differ ONLY by the punctuation the net erases are
    indistinguishable to it, and it must then answer nothing rather than pick.

    `edge_comb` / `edgecomb` is the one real collision in 620 trinkets, and it
    is NOT this case: each has its own exact display name, and the punctuation
    pass maps each from its own spelling. What must fail is a lookup that only
    the ambiguous key could satisfy.
    """
    from data.comp_loadout_loader import CompLoadoutLoader as L
    idx = L._fuzzy_index({"A-B": "x", "A B": "y"})   # both -> "a b" and "ab"
    assert L._resolve_display("AB", {}, idx) is None
    assert L._resolve_display("A--B", {}, idx) is None

    # ...while an unambiguous pair still resolves each from its own spelling.
    idx2 = L._fuzzy_index({"Edge Comb": "edge_comb", "Edgecomb": "edgecomb"})
    assert L._resolve_display("Edgecomb", {}, idx2) == "edgecomb"
    assert L._resolve_display("edge-comb", {}, idx2) == "edge_comb"


def test_the_exact_display_name_always_wins_over_the_net():
    """The net is a fallback. If the string table has the name, that is the
    answer, and no amount of squashing may override it."""
    from data.comp_loadout_loader import CompLoadoutLoader as L
    exact = {"Edgecomb": "edgecomb"}
    idx = L._fuzzy_index({"Edge Comb": "edge_comb"})
    assert L._resolve_display("Edgecomb", exact, idx) == "edgecomb"
