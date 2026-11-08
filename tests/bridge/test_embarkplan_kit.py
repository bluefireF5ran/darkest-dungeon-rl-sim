"""`embarkplan.KIT_AWARE` -- a seat's missing kit counts (2026-10-10).

A fodder hero is never bought skills, so before this its seat read as free
and complete whatever it owned. Two level-0 Vestals, one owning the comp's
four and one owning none: the comp must take the one with the kit, and the
other's seat must say what it lacks.
"""

import sys
import tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests._fixtures import get_store

import bridge.embarkplan as embarkplan
from campaign.state import new_campaign
from data.hero_skills import load_hero_costs
from data.paths import GAME_DIR


def _vestal_comp(store):
    for name, comp in sorted(store.comp_loadouts.items()):
        if comp.source == embarkplan.CURATED and any(h.hero_id == "vestal" for h in comp.heroes):
            return comp
    raise AssertionError("no curated comp with a Vestal")


def test_the_vestal_who_owns_the_kit_is_seated():
    store = get_store()
    comp = _vestal_comp(store)
    kit = tuple(next(h for h in comp.heroes if h.hero_id == "vestal").skill_ids)
    tree = list(load_hero_costs(str(GAME_DIR), "vestal").skills)
    other = tuple(k for k in tree if k not in kit)[:4] or tuple(tree[:4])
    s = new_campaign()
    bare = s.add_hero("vestal", combat_skills=other)
    owner = s.add_hero("vestal", combat_skills=kit)
    prof = s.write_profile(tempfile.mkdtemp())
    pool = [h for h in s.roster() if h.hero_class == "vestal"]
    saved = embarkplan.KIT_AWARE
    try:
        embarkplan.KIT_AWARE = True
        vestal_only = type("C", (), {"heroes": [h for h in comp.heroes if h.hero_id == "vestal"][:1]})
        picked = embarkplan.fill_cheapest(vestal_only, pool, prof, str(GAME_DIR))
        assert picked[0][0].guid == owner.guid
        seats, _ = embarkplan._build_party([(pool[0], kit)], prof, str(GAME_DIR))
        assert pool[0].guid == bare.guid
        assert set(seats[0].lacks) == set(kit) - set(other)
    finally:
        embarkplan.KIT_AWARE = saved


if __name__ == "__main__":
    for name, fn in sorted(list(globals().items())):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  ok  {name}")
