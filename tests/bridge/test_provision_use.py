"""`bridge/provision_use.py` -- every provision use is a trigger (Fran, 2026-09-25)."""
import sys
from pathlib import Path
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from bridge.provision_use import (HeroNeed, between_fights, dog_treat_now,  # noqa: E402
                                  dot_threat, holy_water_now, HOLY_WATER_THREAT)

PACK = {"food": 12, "bandage": 2, "antivenom": 1, "laudanum": 1,
        "holy_water": 2, "dog_treats": 2}


def h(**k):
    base = dict(guid=1, hero_class="crusader", hp=30, max_hp=33)
    base.update(k)
    return HeroNeed(**base)


def test_a_healthy_hero_with_no_status_uses_nothing():
    assert between_fights([h()], PACK) == []


def test_each_status_triggers_only_its_own_cure():
    uses = between_fights([h(bleeding=True), h(guid=2, blighted=True),
                           h(guid=3, horror=True)], PACK)
    assert [(u.guid, u.item) for u in uses] == [(1, "bandage"), (2, "antivenom"),
                                                (3, "laudanum")]


def test_under_30_percent_eats_until_back_over_it_or_full():
    # 20 HP max: 30% is 6; at 2 HP that is 4 HP short, 1 HP a portion -> 4.
    uses = between_fights([h(hp=2, max_hp=20)], PACK)
    assert [u.item for u in uses] == ["food"] * 4
    # ...but never past full: two portions already eaten leaves two.
    assert len(between_fights([h(hp=2, max_hp=20, eaten=2)], PACK)) == 2
    # at 29% one portion is enough
    assert len(between_fights([h(hp=5.8, max_hp=20)], PACK)) == 1


def test_the_reserve_is_never_spent():
    """The curio middle path: what lootplan earmarked stays in the pack."""
    assert between_fights([h(bleeding=True)], PACK, reserve={"bandage": 2}) == []
    assert holy_water_now(h(), 20.0, PACK, reserve={"holy_water": 2}) is None


def test_holy_water_needs_a_real_threat_and_is_not_stacked():
    assert holy_water_now(h(), HOLY_WATER_THREAT - 0.1, PACK) is None
    assert holy_water_now(h(), HOLY_WATER_THREAT, PACK).item == "holy_water"
    assert holy_water_now(h(holy_water_up=True), 30.0, PACK) is None


def test_a_dog_treat_only_when_it_makes_the_kill_and_only_for_the_houndmaster():
    hm = h(hero_class="houndmaster")
    # 6 average against 8 HP: short now, 9 with the treat -> use it
    assert dog_treat_now(hm, 4, 8, 8, PACK).item == "dog_treats"
    assert dog_treat_now(hm, 4, 8, 5, PACK) is None      # kills anyway
    assert dog_treat_now(hm, 4, 8, 20, PACK) is None     # still no kill
    assert dog_treat_now(h(), 4, 8, 8, PACK) is None     # not the Houndmaster


def test_the_threat_reads_shipped_enemy_skills():
    from tests._fixtures import get_store
    st = get_store()
    crab = [NS(definition_id="fishman_crabby_A", position=0, is_alive=lambda: True)]
    assert dot_threat(1, crab, st) >= HOLY_WATER_THREAT
    dead = [NS(definition_id="fishman_crabby_A", position=0, is_alive=lambda: False)]
    assert dot_threat(1, dead, st) == 0.0


def _fight(actor_class, enemy_id, enemy_hp=8.0, dmg=(4, 8), buffs=()):
    actor = NS(id=0, definition_id=actor_class, hp=20, max_hp=25, position=1,
               dmg_min=dmg[0], dmg_max=dmg[1],
               buffs=[NS(buff_id=b) for b in buffs])
    enemy = NS(definition_id=enemy_id, position=0, hp=enemy_hp, is_alive=lambda: True)
    engine = NS(heroes=[actor], enemies=[enemy],
                get_skill_data=lambda sid: NS(dmg_mod=0.0, heal_min=None))
    state = NS(heroes=[NS(guid=11, hero_class=actor_class)],
               provisions={"holy_water": 2, "dog_treats": 2})
    action = NS(hero_id=0, skill_id="hounds_rush", target_id=0)
    return state, engine, action


def test_a_fight_item_is_holy_water_against_a_dot_heavy_line():
    from bridge.provision_use import battle_item
    from tests._fixtures import get_store
    st = get_store()
    state, engine, action = _fight("vestal", "fishman_crabby_A")
    use = battle_item(state, engine, action, st)
    assert use is not None and use.item == "holy_water" and use.guid == 11
    # already flasked: nothing
    state, engine, action = _fight("vestal", "fishman_crabby_A",
                                   buffs=("holy_water_bleed_resist",))
    assert battle_item(state, engine, action, st) is None


def test_a_fight_item_is_a_treat_only_when_it_makes_the_kill():
    from bridge.provision_use import battle_item
    from tests._fixtures import get_store
    st = get_store()
    quiet = next(e for e in st.enemies if e.startswith("skeleton_common"))
    state, engine, action = _fight("houndmaster", quiet, enemy_hp=8.0)
    use = battle_item(state, engine, action, st)
    assert use is not None and use.item == "dog_treats"
    state, engine, action = _fight("houndmaster", quiet, enemy_hp=3.0)
    assert battle_item(state, engine, action, st) is None
    state, engine, action = _fight("crusader", quiet, enemy_hp=8.0)
    assert battle_item(state, engine, action, st) is None
