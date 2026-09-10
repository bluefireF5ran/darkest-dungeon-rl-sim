"""Overstress personalities come from real data now, not from a convention.

`shared/trait/trait_library.json` ships the per-personality stat effects and
act-out probability tables that `engine/combat_engine.py` used to hardcode,
documenting them as unavailable ("the real per-personality act-out tables live
in the game binary, not in any data file"). They disagreed on four counts, and
each one gets a test here:

1. the engine charged EVERY act-out as a party-stress bark; really ~10% are;
2. virtues had no per-turn effect at all; really 25% of their turns carry one;
3. virtues were missing the six +25 resists their trait grants;
4. afflictions applied `base.buffs.json`'s `afflictionXBuffN` ids, which no
   trait actually references.

See CHANGELOG.md.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests._fixtures import get_store, comp_with_classes

import collections

from data.trait_loader import TraitLoader
from engine.combat_engine import CombatEngine
from engine.rng import SeededRNG

from data.paths import GAME_DIR as _GAME_DIR

GAME_DIR = str(_GAME_DIR)
from data.paths import CSV_DIR as _CSV_DIR

CSV_DIR = str(_CSV_DIR)

store = get_store()
# Resolved by SHAPE, not by name: a comp re-export deletes names.
COMP_CLASSES = ("man_at_arms", "highwayman", "highwayman", "vestal")


def _party():
    eng = CombatEngine(store, SeededRNG(5))
    loadout = comp_with_classes(*COMP_CLASSES, store=store)
    hero_ids = [h.hero_id for h in loadout.heroes]
    eng.reset(hero_defs=[store.heroes[h] for h in hero_ids],
              enemy_defs=[store.enemies["fishman_harpoon_B"]])
    return eng


def _become(hero, trait_id):
    trait = store.traits[trait_id]
    hero.is_virtuous = trait.is_virtue
    hero.is_afflicted = not trait.is_virtue
    hero.virtue_id = trait_id if trait.is_virtue else None
    hero.affliction_id = trait_id if not trait.is_virtue else None
    return trait


def _roll_distribution(eng, hero, n=20000):
    counts = collections.Counter()
    for _ in range(n):
        row = eng._roll_act_out(hero)
        counts["nothing" if row is None else row.kind] += 1
    return {k: v / n for k, v in counts.items()}


def test_the_trait_library_loads_including_the_dlc_personalities():
    ids = set(store.traits)
    assert {"fearful", "paranoid", "selfish", "masochistic", "abusive",
            "depressed", "irrational"} <= ids
    assert {"stalwart", "courageous", "focused", "powerful", "vigorous"} <= ids
    # Crimson Court gives the Flagellant his own affliction; Color of Madness
    # adds one more. Both live in DLC trait libraries, which the engine's old
    # comment claimed did not exist ("a Flagellant draws from the same 7").
    assert {"berserk", "color_madness"} <= ids
    # `test` is a shipped placeholder whose only act-out is `nothing`.
    assert "test" not in ids
    assert all(t.buff_ids for t in store.traits.values())


def test_only_the_seven_afflictions_and_five_virtues_are_rollable():
    """The game gates this itself and the flags contradicted a first attempt
    that handed the Flagellant `berserk` exclusively: `berserk` is
    `is_generated: False`, i.e. never produced by a Resolve Check at all — it
    comes from a specific Crimson Court trigger. `color_madness` carries
    `generate_chance_modifier: -1.0` and is Farmstead content in the real
    game. Both are loaded (other code may reference them) but not drawn."""
    rollable = {t.id for t in store.traits.values() if t.is_rollable}
    assert rollable == {"fearful", "paranoid", "selfish", "masochistic", "abusive",
                        "depressed", "irrational",
                        "stalwart", "courageous", "focused", "powerful", "vigorous"}
    assert store.traits["berserk"].is_generated is False
    assert store.traits["color_madness"].generate_chance_modifier == -1.0

    eng = _party()
    hero = eng.heroes[0]
    hero.is_afflicted, hero.is_virtuous = True, False
    drawn = {eng._draw_trait(hero).id for _ in range(400)}
    assert "berserk" not in drawn and "color_madness" not in drawn
    assert len(drawn) == 7, drawn

    flagellant = store.heroes.get("flagellant")
    if flagellant is not None:
        eng2 = CombatEngine(store, SeededRNG(9))
        eng2.reset(hero_defs=[flagellant], enemy_defs=[store.enemies["fishman_harpoon_B"]])
        f = eng2.heroes[0]
        f.is_afflicted, f.is_virtuous = True, False
        assert {eng2._draw_trait(f).id for _ in range(200)}.isdisjoint({"berserk"})


def test_the_binary_arena_trait_libraries_are_skipped_not_fatal():
    """dlc/1117860_arena_mp/**/…trait_library.json is binary, exactly like the
    Arena trinkets file. It must not take the whole load down with it."""
    arena = sorted(Path(GAME_DIR).glob("dlc/1117860_arena_mp/**/*trait_library.json"))
    assert arena, "expected the Arena trait libraries to be present"
    assert all(TraitLoader._read_json(p) is None for p in arena)
    assert TraitLoader.load_all(GAME_DIR) == store.traits


def test_every_trait_buff_id_resolves_in_base_buffs():
    missing = [b for t in store.traits.values() for b in t.buff_ids if b not in store.buffs]
    assert not missing, f"unresolved trait buff ids: {missing}"


def test_act_out_weights_normalise_per_hero_class_not_over_the_raw_table():
    """`attack_friendly` ships as one row per hero class — Abusive has 19 of
    them at weight 1. Normalising over the raw table would make Abusive act out
    64.7% of turns instead of its real 29.4%."""
    abusive = store.traits["abusive"]
    friendly = [a for a in abusive.act_outs if a.kind == "attack_friendly"]
    assert len(friendly) > 15 and all(a.valid_hero_class_ids for a in friendly)
    for hero_class in ("crusader", "leper", "vestal"):
        rows = abusive.act_outs_for(hero_class)
        assert sum(1 for r in rows if r.kind == "attack_friendly") == 1
        total = sum(r.chance for r in rows)
        nothing = sum(r.chance for r in rows if r.kind == "nothing")
        assert abs((total - nothing) / total - 0.294) < 0.01


def test_only_a_tenth_of_fearful_act_outs_is_a_party_stress_bark():
    """Divergence #1, the one that mattered most: the engine barked on EVERY
    act-out at a flat 33%, which is 3.3x the real rate on top of the exact
    mechanism that decides whether a long expedition survives."""
    fearful = store.traits["fearful"]
    rows = fearful.act_outs_for("vestal")
    total = sum(r.chance for r in rows)
    bark = sum(r.chance for r in rows if r.kind == "bark_stress")
    assert bark / total == 4 / 38

    eng = _party()
    hero = eng.heroes[3]
    _become(hero, "fearful")
    dist = _roll_distribution(eng, hero)
    assert abs(dist["bark_stress"] - 4 / 38) < 0.02
    assert abs((1 - dist["nothing"]) - 12 / 38) < 0.02
    # The outcomes the flat model could not express at all.
    assert dist["change_pos"] > 0.05 and dist["ignore_command"] > 0.05


def test_an_affliction_applies_its_own_trait_buffs_not_the_afflictionxbuffn_ids():
    """Divergence #4. Fearful really takes -25% damage; the old code read
    `afflictionFearfulBuff*` from base.buffs.json and gave -10 ACC / +10 PROT
    instead — ids the trait does not reference."""
    fearful = store.traits["fearful"]
    assert not any(b.startswith("afflictionFearful") for b in fearful.buff_ids)
    assert {"DMGL-25", "DMGH-25", "MAXHP-10", "STUNRESIST-15"} <= set(fearful.buff_ids)

    eng = _party()
    hero = eng.heroes[0]
    before = (hero.dmg_min, hero.dmg_max, hero.max_hp, hero.stun_resist)
    hero.is_afflicted = True
    hero.affliction_id = None
    eng._draw_trait = lambda h: fearful          # pin the draw
    eng._apply_affliction_effects(hero)
    assert hero.affliction_id == "fearful"
    assert hero.dmg_min < before[0] and hero.dmg_max < before[1], "no -25% damage"
    assert hero.max_hp < before[2], "no -10% MAX HP"
    assert abs(hero.stun_resist - (before[3] - 0.15)) < 1e-6


def test_the_shared_affliction_baseline_is_applied_exactly_once():
    """It used to be a hardcoded loop ON TOP of the personality buffs. The
    -15 resists and MAXHP-10 are entries in each affliction's own buff_ids, so
    adding a baseline now would double them."""
    eng = _party()
    hero = eng.heroes[1]
    before = hero.bleed_resist
    eng._draw_trait = lambda h: store.traits["selfish"]
    hero.is_afflicted = True
    eng._apply_affliction_effects(hero)
    assert abs(hero.bleed_resist - (before - 0.15)) < 1e-6


def test_a_virtue_grants_the_six_plus_25_resists_it_used_to_miss():
    """Divergence #3: the old code applied only `virtueXBuffN` (2 per virtue)
    and dropped the resist block the trait grants alongside them."""
    courageous = store.traits["courageous"]
    assert {"STUNRESIST25", "BLIGHTRESIST25", "BLEEDRESIST25",
            "DISEASERESIST25", "DEBUFFRESIST25", "MOVERESIST25"} <= set(courageous.buff_ids)
    assert "virtueCourageousBuff1" in courageous.buff_ids

    eng = _party()
    hero = eng.heroes[2]
    before = (hero.stun_resist, hero.bleed_resist, hero.move_resist)
    hero.is_virtuous = True
    eng._draw_trait = lambda h: courageous
    eng._apply_affliction_effects(hero)
    assert abs(hero.stun_resist - (before[0] + 0.25)) < 1e-6
    assert abs(hero.bleed_resist - (before[1] + 0.25)) < 1e-6
    assert abs(hero.move_resist - (before[2] + 0.25)) < 1e-6


def test_courageous_heals_the_whole_party_stress_and_keeps_its_turn():
    """Divergence #2: virtues had NO per-turn effect. Really one turn in four
    fires `HealStressVirtuedParty` (.healstress 4). And it must NOT burn the
    turn — the virtue act-outs are `.queue true` bonuses, not replacements."""
    eng = _party()
    hero = eng.heroes[0]
    _become(hero, "courageous")
    for h in eng.heroes:
        h.stress = 50
    fired = False
    for _ in range(200):
        eng._act_out_rolled_for_index = -1
        burned = eng._maybe_act_out(hero)
        assert burned is False, "a virtue act-out must not cost the turn"
        if sum(h.stress for h in eng.heroes) < 200:
            fired = True
            break
    assert fired, "stress_heal_party never landed in 200 virtuous turns"
    assert all(h.stress < 50 for h in eng.heroes if h.is_alive())


def test_attack_friendly_uses_the_row_matching_the_attacker_class():
    """A real per-class table: an Abusive Crusader's ally-hit carries `Stun 3`
    at 0.1 damage, a Leper's is a bare 0.33. Picking the wrong row would hand
    a Vestal the Leper's damage."""
    abusive = store.traits["abusive"]
    by_class = {c: a for a in abusive.act_outs if a.kind == "attack_friendly"
                for c in a.valid_hero_class_ids}
    assert by_class["crusader"].string_value == "Stun 3"
    assert abs(by_class["crusader"].number_value - 0.1) < 1e-9
    assert abs(by_class["leper"].number_value - 0.33) < 1e-9
    assert by_class["leper"].string_value == ""

    eng = _party()
    attacker, victim = eng.heroes[0], eng.heroes[1]
    _become(attacker, "abusive")
    row = by_class["man_at_arms"]
    before = victim.hp
    eng._act_out_strike(attacker, victim, row)
    assert victim.hp < before, "the ally took no damage"


def test_change_pos_keeps_the_hero_formation_contiguous():
    """The invariant every formation change in this engine has to preserve:
    ranks are contiguous from the front, gaps only ever sit at the back."""
    eng = _party()
    hero = eng.heroes[3]
    _become(hero, "fearful")
    row = next(a for a in store.traits["fearful"].act_outs if a.kind == "change_pos")
    for _ in range(50):
        eng._act_out_change_pos(hero, row)
        positions = sorted(h.position for h in eng.heroes if h.is_alive())
        assert positions == list(range(len(positions))), positions


def test_mark_self_actually_marks():
    """`Mark Self` is `.tag 1 .target "performer"`, and _apply_effect_to_target
    only marks on `.target "target"` — a guard that exists because 11 real
    `.tag 1` effects target "performer" while being conditional self-buffs, and
    marking their performer was a real bug. So this one needs its own path, and
    the mark has to genuinely land: monster brains weight Marked heroes up."""
    eng = _party()
    hero = eng.heroes[1]
    _become(hero, "masochistic")
    row = next(a for a in store.traits["masochistic"].act_outs if a.kind == "mark_self")
    assert store.effects[row.string_value].is_mark_effect
    assert hero.is_marked is False
    eng._act_out_mark_self(hero, row)
    assert hero.is_marked is True
    assert hero.mark_rounds_remaining == store.effects[row.string_value].duration


def test_random_command_only_ever_plays_a_legal_action():
    eng = _party()
    hero = eng.heroes[1]
    _become(hero, "irrational")
    row = next(a for a in store.traits["irrational"].act_outs if a.kind == "random_command")
    legal = {a.skill_id for a in eng.get_valid_actions(hero.id)}
    for _ in range(30):
        before = len(eng.log)
        assert eng._act_out_random_command(hero, row) is True
        used = [l for l in eng.log[before:] if "ignores the order and uses" in l]
        for line in used:
            assert line.rsplit(" ", 1)[-1] in legal


def test_an_unknown_act_out_kind_blocks_rather_than_fires():
    """Same rule as engine/camping.py::_requirements_met — an outcome this
    simulator does not understand must not be silently reinterpreted as a
    wasted turn, which would be inventing a mechanic and calling it data."""
    from data.types import TraitActOut
    eng = _party()
    hero = eng.heroes[0]
    _become(hero, "fearful")
    assert "consume_item" not in eng._ACT_OUT_HANDLERS
    assert "retreat_from_combat" not in eng._ACT_OUT_HANDLERS
    eng._roll_act_out = lambda h: TraitActOut(kind="something_new", chance=1)
    eng._act_out_rolled_for_index = -1
    assert eng._maybe_act_out(hero) is False


def test_retreat_from_combat_carries_zero_weight_in_every_shipped_trait():
    """Which is why it needs no implementation — it can never be rolled."""
    raw_paths = [Path(GAME_DIR) / TraitLoader.RELATIVE_PATH]
    raw_paths += sorted(Path(GAME_DIR).glob(TraitLoader.DLC_GLOB))
    seen = False
    for path in raw_paths:
        data = TraitLoader._read_json(path)
        if not data:
            continue
        for trait in data.get("traits", []):
            for row in trait.get("combat_start_turn_act_outs", []):
                if row.get("id") == "retreat_from_combat":
                    seen = True
                    assert (row.get("chance") or 0) == 0
    assert seen, "expected to find retreat_from_combat rows at all"


def test_the_act_out_is_rolled_once_per_turn_not_once_per_free_action():
    """A hero re-enters step() on every provision and on Abomination's
    Transform. Without the guard a Courageous hero could farm party stress
    heals by chaining provisions."""
    eng = _party()
    hero = eng.heroes[0]
    _become(hero, "courageous")
    eng._act_out_rolled_for_index = -1
    eng._maybe_act_out(hero)
    rolled = eng._act_out_rolled_for_index
    assert rolled == eng.current_turn_index
    before = dict(eng._act_out_counts)
    for _ in range(10):
        assert eng._maybe_act_out(hero) is False
    assert eng._act_out_counts == before, "act-out re-rolled inside the same turn"


def test_defense_rating_buffs_are_no_longer_silently_dropped():
    """Found while wiring the traits: `combat_stat_add`/`defense_rating` is 95
    entries in base.buffs.json — the 7th most common buff shape — and
    _apply_buff_to_stats only matched "defense"/"dodge", so every one of them
    did nothing. Trait, trinket and quirk sourced alike."""
    count = sum(1 for b in store.buffs.values()
                if b.stat_type == "combat_stat_add" and b.stat_sub_type == "defense_rating")
    assert count > 50, f"expected many defense_rating buffs, found {count}"

    eng = _party()
    hero = eng.heroes[0]
    before = hero.dodge
    bd = store.buffs["DEF10"]
    assert bd.stat_sub_type == "defense_rating"
    eng._apply_buff_to_stats(hero, bd)
    assert abs(hero.dodge - (before + bd.amount)) < 1e-9
    eng._reverse_buff_from_stats(hero, bd)
    assert abs(hero.dodge - before) < 1e-9


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  ok  {name}")
