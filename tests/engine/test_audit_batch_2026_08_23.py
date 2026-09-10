"""The 2026-08-23 mechanic audit: seven things the shipped data says and the
engine did not do.

Found by sweeping EVERY data family -- effects, hero and monster
`.info.darkest`, trinkets, quirks, monster brains, traits, buffs, provisions,
camping, `rules.json` -- for keys nothing in `data/`, `engine/` or `env/`
names, then keeping only the ones carried by content this simulator actually
fields. That filter is the whole method: the raw diff is hundreds of keys, most
of them curio, town, loot or Farmstead content, and the filtered list is short
enough to act on.

Ranked by live carriers, which is how the buff sweep found `stun_chance` and
how this found trinket set bonuses:

  * trinket SET BONUSES      23 of 564 hero slots in the comp pool
  * brain stress-state gates PARSED BUT INERT -- see the correction below
  * affliction reactions     5 of 7 afflictions, 33% each
  * trinket attack procs     28 equipped slots, 3 trinkets
  * refresh_after_each_wave  10 skills
  * mode battle-start effects 2 stance heroes
  * stall_round_reset_threshold  a rules.json scalar that was not even PARSED
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests._fixtures import get_store

from engine.action import Action
from engine.combat_engine import CombatEngine
from engine.rng import SeededRNG


@pytest.fixture(scope="module")
def store():
    return get_store()


def _engine(store, hero_ids, foe_ids, torch=50.0, seed=11):
    e = CombatEngine(store, SeededRNG(seed))
    e.reset(hero_defs=[store.heroes[h] for h in hero_ids],
            enemy_defs=[store.enemies[f] for f in foe_ids], torch_level=torch)
    return e


# ── trinket set bonuses ────────────────────────────────────────────────────

def test_the_set_files_are_read_at_all(store):
    """Four `*.sets.trinkets.json` files ship, all under `dlc/`. Nothing read
    them, so the 38 `SET_*` buffs already sitting in `store.buffs` could never
    be applied to anyone."""
    assert store.trinket_sets, "no trinket sets loaded"
    assert "cc_vest" in store.trinket_sets
    assert all(store.trinket_sets.values()), "a set was loaded with no buffs"
    for set_id, buff_ids in store.trinket_sets.items():
        for bid in buff_ids:
            assert bid in store.buffs, f"{set_id} names a buff that does not exist: {bid}"


def test_every_shipped_set_has_exactly_two_members(store):
    """`apply_trinkets_and_quirks` pays out at `worn >= 2`. That threshold is
    only correct because every shipped set is a pair -- pinned here so a mod
    with a three-piece set fails loudly instead of paying early."""
    members = {}
    for tid, td in store.trinkets.items():
        if td.set_id:
            members.setdefault(td.set_id, []).append(tid)
    assert members, "no trinket carries a set_id"
    for set_id, ids in members.items():
        assert len(ids) == 2, f"{set_id} has {len(ids)} members: {ids}"


def test_wearing_the_pair_grants_the_set_buffs(store):
    """The whole point. 23 pool hero slots equip a complete pair and got
    nothing for it."""
    pair = ["cc_set_vest_atonement_beads", "cc_set_vest_salacious_diary"]
    set_buffs = store.trinket_sets["cc_vest"]

    e = _engine(store, ["vestal"], ["cultist_brawler_A"])
    hero = e.heroes[0]
    before = len(hero.buffs) + len(hero.conditional_buffs)
    e.apply_trinkets_and_quirks(hero, pair, [])
    after = len(hero.buffs) + len(hero.conditional_buffs)
    assert after > before

    # one member alone must NOT pay out
    e2 = _engine(store, ["vestal"], ["cultist_brawler_A"])
    solo = e2.heroes[0]
    e2.apply_trinkets_and_quirks(solo, pair[:1], [])

    def stat_total(c):
        return (c.dodge, c.prot, c.speed, c.crit, c.max_hp,
                c.dmg_min, c.dmg_max, c.atk_bonus)

    assert set_buffs, "cc_vest has no buffs, so this test proves nothing"
    assert stat_total(hero) != stat_total(solo) or len(hero.conditional_buffs) > len(
        solo.conditional_buffs), "the pair produced the same hero as one member"


def test_the_wearer_remembers_what_it_wears(store):
    """`trinket_ids` on the combatant is what lets an attack find its
    trinkets' proc effects; the buffs alone are already flattened away."""
    e = _engine(store, ["vestal"], ["cultist_brawler_A"])
    hero = e.heroes[0]
    e.apply_trinkets_and_quirks(hero, ["cc_set_vest_atonement_beads"], [])
    assert hero.trinket_ids == ["cc_set_vest_atonement_beads"]


# ── monster-brain stress-state gates ───────────────────────────────────────

def test_the_three_stress_gates_are_INERT_in_shipped_data(store):
    """Parsed, and it changes nothing today -- the same status as
    `can_target_last_hero`.

    A correction worth recording. The audit first scored these at "14.5% of
    hall/room encounter slots", which counted monsters whose brain MENTIONS
    the key. All 40 shipped carriers set all three to `true`, which is the
    default, so not one desire is actually gated by them. Mentioning a key and
    being changed by it are different measurements, and only the second one is
    worth anything.

    Kept anyway, for the same reason `can_target_last_hero` is kept: the
    parsing is free, a mod can set it false, and the engine branch is three
    lines. This test exists so nobody re-discovers the keys and reports them
    as a live gap a second time.
    """
    gated = 0
    for brain in store.monster_brains.values():
        for desire in getattr(brain, "target_desires", []) or []:
            if not (desire.can_target_afflicted and desire.can_target_virtued
                    and desire.can_target_not_overstressed):
                gated += 1
    assert gated == 0, (
        f"{gated} desires now gate on stress state -- shipped data used to gate "
        "none, so the engine branch in _monster_target_weights is live now and "
        "this test should assert its BEHAVIOUR instead of its absence")


def test_the_stress_gates_still_parse(store):
    """Separate from the inertness check: if the loader stopped reading them,
    the test above would pass for the wrong reason."""
    from data.types import TargetDesire
    d = TargetDesire(type="random_target", base_chance=1.0)
    assert d.can_target_afflicted is True
    assert d.can_target_virtued is True
    assert d.can_target_not_overstressed is True
    assert any("can_target_afflicted" in str(f)
               for f in TargetDesire.__dataclass_fields__)


# ── affliction reaction act-outs ───────────────────────────────────────────

def test_reactions_are_parsed_with_the_shipped_chances(store):
    for tid in ("depressed", "irrational", "masochistic", "paranoid"):
        r = store.traits[tid].reactions
        assert r.get("block_heal") == pytest.approx(0.33), tid
        assert r.get("block_buff") == pytest.approx(0.33), tid
        assert r.get("block_item") == pytest.approx(0.33), tid
        assert r.get("block_combat_retreat") == pytest.approx(0.33), tid
    assert store.traits["abusive"].reactions.get("block_move") == pytest.approx(0.33)


def test_barks_are_not_behaviour(store):
    """Every `comment_*` row carries a `BarkStress` payload -- a voice line.
    Keeping them would make a Fearful hero 'refuse' things it does not."""
    for trait in store.traits.values():
        assert not any(k.startswith("comment_") for k in trait.reactions)


def test_a_virtue_blocks_nothing_yet(store):
    """All five virtues ship `block_effect` at chance 1.0 with a
    `BarkStressHeal` payload. 'Immune to every incoming effect' cannot be the
    reading and nothing in the data narrows it, so it is parsed and NOT wired.
    See ASSUMPTIONS.md. This test records the decision, not an endorsement."""
    e = _engine(store, ["crusader", "vestal"], ["cultist_brawler_A"])
    hero = e.heroes[0]
    hero.is_virtuous = True
    hero.virtue_id = "courageous"
    assert e._reaction_blocks(hero, "block_effect") is False


def test_an_unafflicted_hero_never_blocks_and_draws_no_rng(store):
    """The RNG guard matters: drawing for every heal on every healthy hero
    would desynchronise every existing seeded trace."""
    e = _engine(store, ["crusader", "vestal"], ["cultist_brawler_A"])
    hero = e.heroes[0]
    before = e.rng.random()
    e2 = _engine(store, ["crusader", "vestal"], ["cultist_brawler_A"])
    assert e2._reaction_blocks(e2.heroes[0], "block_heal") is False
    assert e2.rng.random() == before, "a draw was consumed for a healthy hero"


def test_an_affliction_without_that_reaction_draws_nothing(store):
    """Abusive carries `block_move` but not `block_heal`."""
    e = _engine(store, ["crusader", "vestal"], ["cultist_brawler_A"])
    hero = e.heroes[0]
    hero.is_afflicted = True
    hero.affliction_id = "abusive"
    before = e.rng.random()
    e2 = _engine(store, ["crusader", "vestal"], ["cultist_brawler_A"])
    h2 = e2.heroes[0]
    h2.is_afflicted = True
    h2.affliction_id = "abusive"
    assert e2._reaction_blocks(h2, "block_heal") is False
    assert e2.rng.random() == before


def test_the_block_rate_is_the_shipped_one(store):
    e = _engine(store, ["crusader", "vestal"], ["cultist_brawler_A"])
    hero = e.heroes[0]
    hero.is_afflicted = True
    hero.affliction_id = "depressed"
    blocks = sum(1 for _ in range(2000) if e._reaction_blocks(hero, "block_heal"))
    assert 0.28 < blocks / 2000 < 0.38, blocks / 2000


# ── stall_round_reset_threshold ────────────────────────────────────────────

def test_the_reset_threshold_is_parsed(store):
    """It was absent from BOTH the rules map and the dataclass, which is why
    nothing could read it -- 'unread' understated the case."""
    assert store.rules.stall_round_reset_threshold == 4


def test_the_stall_counter_cycles_instead_of_running_away(store):
    """Declared reading: the counter resets at 4, so a stalling party is
    charged stress on two rounds in four rather than every round from the
    third onward forever. See CombatEngine._maybe_reset_stall_counter."""
    e = _engine(store, ["crusader", "vestal", "highwayman"], ["cultist_brawler_A"])
    e.stall_counter = store.rules.stall_round_reset_threshold
    e._maybe_reset_stall_counter()
    assert e.stall_counter == 0

    e.stall_counter = store.rules.stall_round_reset_threshold - 1
    e._maybe_reset_stall_counter()
    assert e.stall_counter == store.rules.stall_round_reset_threshold - 1


# ── the three narrow ones ──────────────────────────────────────────────────

def test_trinket_attack_effects_are_parsed(store):
    """Three trinkets in the pool carry them, across 28 equipped slots."""
    carriers = [t for t in store.trinkets.values() if t.attack_effects]
    assert carriers, "no trinket parsed an attack proc"
    for t in carriers:
        for ename in t.attack_effects:
            assert ename in store.effects, f"{t.id} names a missing effect {ename}"


def test_refresh_after_each_wave_is_parsed(store):
    flagged = [(h, s.id) for h, hd in store.heroes.items() for s in hd.skills
               if any(lv.refresh_after_each_wave for lv in s.levels.values())]
    assert flagged, "no skill parsed the wave-refresh flag"


def test_a_wave_gives_back_a_wave_refreshed_skill(store):
    # Abomination's Transform and Runaway's Controlled Burn are the only two
    # skills in the game that are BOTH per-battle limited and wave-refreshed,
    # so the test has to field one of them or it proves nothing.
    e = _engine(store, ["abomination", "vestal"], ["cultist_brawler_A"])
    hero = e.heroes[0]
    target = None
    for s in store.heroes[hero.definition_id].skills:
        lv = s.levels[max(s.levels)]
        if lv.refresh_after_each_wave and lv.per_battle_limit:
            target = s.id
            break
    if target is None:
        pytest.skip("this class has no wave-refreshed limited skill")
    hero.skill_uses[target] = 99
    e._refresh_wave_limited_skills()
    assert hero.skill_uses[target] == 0


def test_mode_battle_start_effects_are_parsed(store):
    """Two carriers ship, both stance heroes -- so an empty result here means
    the key stopped parsing, not that the mechanic is rare."""
    carriers = [(hid, m.id) for hid, hd in store.heroes.items()
                for m in hd.modes if m.battle_start_effect_ids]
    assert carriers, "no mode parsed a battle-start effect"
    for hid, _mid in carriers:
        for m in store.heroes[hid].modes:
            for ename in m.battle_start_effect_ids:
                assert ename in store.effects, f"{hid} names a missing effect {ename}"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
