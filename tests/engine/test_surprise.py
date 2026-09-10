"""Surprise — who moves first, and whether the party is still in formation.

Unimplemented until 2026-08-21, and the last part of the dark that was not
simulated. `ASSUMPTIONS.md` excluded it for years as a "dungeon exploration
mechanic with no analog in a single-encounter combat simulator", which was
true when the torch was pinned at 100 and stopped being true the day
`TORCH_LEVEL_DECILES` started drawing a real torch per combat: the darkness
table's whole point is that the dark surprises YOU, and without surprise a
low torch read to the policy as a mild stat swing.

Every number here is shipped. The bases and the two 0.65 caps are `rules.json`
scalars, the darkness column is the band's own `heroes_surprised_increase` /
`monsters_surprised_increase`, and the behaviour (act last, party scrambled,
no retreat) is the wiki's Surprise page.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests._fixtures import get_store

from engine.combat_engine import CombatEngine
from engine.rng import SeededRNG

FOUR = ("crusader", "vestal", "arbalest", "hellion")


@pytest.fixture(scope="module")
def store():
    return get_store()


def _engine(store, torch=100.0, seed=1, heroes=FOUR, n_enemies=3,
            surprise=None, auto=False):
    engine = CombatEngine(store, SeededRNG(seed))
    engine.reset(hero_defs=[store.heroes[h] for h in heroes],
                 enemy_defs=[store.enemies["cultist_brawler_A"]] * n_enemies,
                 torch_level=torch, surprise=surprise,
                 auto_resolve_to_hero=auto)
    return engine


# ── the chance ──

def test_the_dark_surprises_the_party_and_the_light_surprises_the_monsters(store):
    """The sign is the whole mechanic, and it is why torchlight is frightening
    rather than merely worse. Radiant: the party is at its floor and the
    monsters at their ceiling. Black as pitch: exactly inverted."""
    radiant_party, radiant_monsters = _engine(store, torch=100.0)._surprise_chances()
    pitch_party, pitch_monsters = _engine(store, torch=0.0)._surprise_chances()
    assert radiant_party == pytest.approx(0.10)
    assert radiant_monsters == pytest.approx(0.35)
    assert pitch_party == pytest.approx(0.50)
    assert pitch_monsters == pytest.approx(0.10)


def test_the_chance_is_base_plus_the_band(store):
    """Percentage points over a fraction, /100 like every other band column —
    the unit mismatch that would otherwise read 25.0 as 2500%."""
    rules = get_store().rules
    for torch in (100.0, 74.0, 40.0, 10.0, 0.0):
        engine = _engine(store, torch=torch)
        band = engine._get_darkness_band()
        party, monsters = engine._surprise_chances()
        assert party == pytest.approx(min(
            rules.surprise_max_party_surprised_chance,
            rules.surprise_room_party_base_chance
            + band.heroes_surprised_increase / 100.0))
        assert monsters == pytest.approx(min(
            rules.surprise_max_monsters_surprised_chance,
            rules.surprise_room_monsters_base_chance
            + band.monsters_surprised_increase / 100.0))


def test_a_scouted_room_is_a_hard_never_not_a_zero(store):
    """`-1.0`, and the sign is load-bearing: the darkness table ADDS up to
    +0.40 on top, so a base of 0.0 would let the dark surprise a party in a
    room it had already scouted. Scouting is not modelled here — this pins the
    datum so the reasoning survives whoever adds a map layer."""
    rules = store.rules
    assert rules.surprise_known_room_party_base_chance == -1.0
    assert rules.surprise_known_corridor_party_base_chance == -1.0
    worst_dark = max(b.heroes_surprised_increase for b in rules.darkness_table)
    assert rules.surprise_known_room_party_base_chance + worst_dark / 100.0 < 0.0
    # ...and it goes the other way for the monsters: scouting is what makes
    # THEM easier to catch, from 0.1 to 0.25.
    assert rules.surprise_known_room_monsters_base_chance == 0.25


def test_corridor_and_room_are_identical_while_unscouted(store):
    """Why this simulator not knowing which one an encounter is costs nothing.
    If a patch ever splits them, this fails and `_surprise_chances` needs a
    real corridor/room input."""
    rules = store.rules
    assert (rules.surprise_corridor_party_base_chance
            == rules.surprise_room_party_base_chance)
    assert (rules.surprise_corridor_monsters_base_chance
            == rules.surprise_room_monsters_base_chance)


def test_the_ambush_keys_are_dead_data(store):
    """`rules.json` ships `surprise_ambush_party_base_chance 1.0` and
    `surprise_ambush_monsters_base_chance 0.0`, and NEITHER string exists in
    `Darkest.exe` — while all ten other surprise keys appear exactly once, in
    one contiguous parse run. So the game does not roll for an ambush; it sets
    it, which is what "unavoidable vs Shambler/Fanatic" means. Not parsed here
    for that reason, and this pins the decision."""
    assert not hasattr(store.rules, "surprise_ambush_party_base_chance")
    assert not hasattr(store.rules, "surprise_ambush_monsters_base_chance")


def test_the_caps_bind(store):
    """0.65 both ways. Nothing shipped reaches it on the darkness table alone
    (the worst is 0.1 + 0.40), so the cap only matters once camping and
    trinket modifiers pile on — which is exactly when a runaway chance would
    otherwise go unnoticed."""
    engine = _engine(store, torch=0.0)
    for h in engine.heroes:
        h.party_surprise_chance_mod += 0.5
        h.monsters_surprise_chance_mod += 0.5
    party, monsters = engine._surprise_chances()
    assert party == pytest.approx(0.65)
    assert monsters == pytest.approx(0.65)


def test_a_modifier_can_take_the_chance_to_zero_but_not_below(store):
    """Book of Intuition is a real Common trinket carrying a real buff
    (`TRINKET_PARTYSURPRISE_B2`, `party_surprise_chance -0.2`), so one hero's
    slot protects the whole party. The clamp at 0 is what keeps a negative
    chance from reading as a certainty anywhere downstream."""
    buff = store.buffs["TRINKET_PARTYSURPRISE_B2"]
    assert buff.stat_type == "party_surprise_chance" and buff.amount == -0.2
    engine = _engine(store, torch=100.0)
    engine.heroes[0].party_surprise_chance_mod = -0.2
    assert engine._surprise_chances()[0] == 0.0


def test_the_frequency_matches_the_chance(store):
    """The roll itself, over 500 seeds at black as pitch (party 50%)."""
    hits = sum(_engine(store, torch=0.0, seed=s).party_surprised
               for s in range(500))
    assert 0.42 < hits / 500 < 0.58, hits


def test_both_draws_are_always_taken(store):
    """Independence, and a stream that does not fork on the first outcome. If
    the monster draw were skipped when the party was surprised, every combat
    after a surprised opening would face a different RNG future."""
    counts = {(False, False): 0, (False, True): 0,
              (True, False): 0, (True, True): 0}
    for s in range(400):
        e = _engine(store, torch=0.0, seed=s)
        counts[(e.party_surprised, e.monsters_surprised)] += 1
    assert all(v > 0 for v in counts.values()), counts


# ── what it costs ──

def test_a_surprised_party_acts_last(store):
    engine = _engine(store, surprise=(True, False))
    order = [c.is_hero for c in engine.turn_order]
    assert order == sorted(order), order
    assert not order[0], "a monster must open the round"


def test_surprised_monsters_act_last(store):
    engine = _engine(store, surprise=(False, True))
    order = [c.is_hero for c in engine.turn_order]
    assert order == sorted(order, reverse=True), order


def test_both_surprised_cancels_out(store):
    """Representable, because the save carries two independent booleans and
    rules.json gives each side its own base and its own cap. With both sides
    flat-footed the tempo term is a wash and initiative decides, which is the
    sensible thing for it to do."""
    both = _engine(store, surprise=(True, True), seed=5)
    neither = _engine(store, surprise=(False, False), seed=5)
    assert ([c.name for c in both.turn_order]
            == [c.name for c in neither.turn_order])


def test_surprise_is_the_OPENING_round_only(store):
    """Tempo, not a standing debuff. The flags stay set — the bridge reads
    them off a save — so the round gate is what expires the effect."""
    surprised = _engine(store, surprise=(True, False))
    control = _engine(store, surprise=(False, False))
    for e in (surprised, control):
        e.round = 1
        e.rng = SeededRNG(99)
        e._compute_turn_order()
    assert ([c.name for c in surprised.turn_order]
            == [c.name for c in control.turn_order])
    assert surprised.party_surprised, "the flag itself does not get cleared"


def test_a_surprised_party_is_scrambled(store):
    """The expensive half: a Vestal shoved into rank 1 has no legal heal. Over
    enough seeds the formation must actually change."""
    scrambled = 0
    for s in range(60):
        engine = _engine(store, seed=s, surprise=None, torch=0.0)
        if not engine.party_surprised:
            continue
        if [h.name for h in sorted(engine.heroes, key=lambda h: h.position)] != list(
                store.heroes[h].name for h in FOUR):
            scrambled += 1
    assert scrambled > 5, scrambled


def test_the_scramble_is_a_permutation(store):
    """Same invariant a push or a pull keeps: the SET of occupied ranks cannot
    change, so a scramble can neither stack two heroes nor open a hole."""
    for s in range(120):
        engine = _engine(store, seed=s, torch=0.0)
        pos = [h.position for h in engine.heroes]
        assert sorted(pos) == list(range(len(pos))), (s, pos)


def test_a_lone_hero_is_not_scrambled(store):
    """Nothing to permute, and `rng.shuffle` on a 1-element list must not be
    allowed to look like a no-op that quietly did something."""
    engine = _engine(store, heroes=("crusader",), surprise=(True, False))
    assert engine.heroes[0].position == 0


def test_stating_the_outcome_does_not_scramble(store):
    """`surprise=` is the bridge's path: a live `persist.raid.json` already
    knows both flags AND already holds the scrambled formation, so re-rolling
    would shuffle the very board being copied. Same class of rule as
    `auto_resolve_to_hero=False`."""
    engine = _engine(store, surprise=(True, True))
    assert [h.position for h in engine.heroes] == [0, 1, 2, 3]
    assert engine.party_surprised and engine.monsters_surprised


def test_a_surprised_party_cannot_retreat_that_round(store):
    engine = _engine(store, surprise=(True, False), auto=True)
    hero = engine.get_current_actor_id()
    assert hero is not None
    ids = {a.skill_id for a in engine.get_valid_actions(hero)}
    assert "__retreat__" not in ids
    # ...and the door reopens the moment the round does.
    engine.round = 1
    ids = {a.skill_id for a in engine.get_valid_actions(hero)}
    assert "__retreat__" in ids


def test_surprised_monsters_do_not_lock_the_door(store):
    """The no-escape rule is the PARTY's, not a property of surprise in
    general — catching the monsters flat-footed must not also trap you."""
    engine = _engine(store, surprise=(False, True), auto=True)
    hero = engine.get_current_actor_id()
    ids = {a.skill_id for a in engine.get_valid_actions(hero)}
    assert "__retreat__" in ids


# ── the monsters get a say (2026-08-21) ──

def _flag_engine(store, monster, torch=100.0, seed=0):
    engine = CombatEngine(store, SeededRNG(seed))
    engine.reset(hero_defs=[store.heroes[h] for h in FOUR],
                 enemy_defs=[store.enemies[monster]],
                 torch_level=torch, auto_resolve_to_hero=False)
    return engine


def test_the_always_surprise_list_is_exactly_the_wikis(store):
    """The evidence that these four flags mean what they say. `always_surprise`
    is carried by the Shambler line and the Fanatic and by nothing else — which
    is the wiki's "unavoidable vs Shambler/Fanatic/camping ambush" list, arrived
    at from the other direction."""
    always = sorted(e.id for e in store.enemies.values() if e.always_surprise)
    assert always, "premise: the flag is parsed"
    assert all(a.startswith(("shambler", "fanatic")) for a in always), always
    assert any(a.startswith("shambler") for a in always)


def test_a_shambler_always_surprises_and_is_never_surprised(store):
    for seed in range(40):
        engine = _flag_engine(store, "shambler_A", seed=seed)
        assert engine.party_surprised, seed
        assert not engine.monsters_surprised, seed


def test_the_ancestor_is_outside_surprise_entirely(store):
    """162 monsters ship both `can_` flags False — the Ancestor forms, the
    Crimson Court bodies, the Baron. That is how the game encodes the wiki's
    "impossible in the Darkest Dungeon and the Farmstead": per monster, not per
    region. Checked at black as pitch, where the roll would otherwise be 50%."""
    ancestor = next(e for e in store.enemies.values()
                    if not e.can_surprise and not e.can_be_surprised)
    for seed in range(40):
        engine = _flag_engine(store, ancestor.id, torch=0.0, seed=seed)
        assert not engine.party_surprised and not engine.monsters_surprised, seed


def test_an_ordinary_monster_still_rolls(store):
    """The control: without it, "surprise never happens" would pass all three
    tests above."""
    seen = set()
    for seed in range(120):
        engine = _flag_engine(store, "cultist_brawler_A", torch=0.0, seed=seed)
        seen.add((engine.party_surprised, engine.monsters_surprised))
    assert len(seen) > 1, seen


def test_a_monster_that_cannot_be_surprised_still_costs_the_draws(store):
    """Both draws are taken BEFORE the flags are consulted, so which monsters
    turned up cannot shift every subsequent roll of the combat. If this ever
    fails, two arms of an A/B comparison stop facing the same fight the moment
    one of them meets an Ancestor form.

    Measured against a monster that genuinely rolled and happened not to be
    surprised, so the two engines differ in nothing but the flags. The
    scramble is deliberately excluded: it is a real consequence of the
    OUTCOME, and it does draw."""
    ancestor = next(e for e in store.enemies.values()
                    if not e.can_surprise and not e.can_be_surprised)
    seed = next(s for s in range(50)
                if not _flag_engine(store, "cultist_brawler_A", seed=s).party_surprised
                and not _flag_engine(store, "cultist_brawler_A", seed=s).monsters_surprised)
    a = _flag_engine(store, ancestor.id, seed=seed)
    b = _flag_engine(store, "cultist_brawler_A", seed=seed)
    assert not (a.party_surprised or b.party_surprised)
    assert a.rng.call_count == b.rng.call_count


def test_the_mixed_case_is_rare_enough_to_declare(store):
    """ANY, not ALL, in both directions — one ambusher is enough and one
    Shambler forces it. The choice is nearly unobservable and this says how
    nearly: across every real encounter comp, only a handful are mixed."""
    mixed = 0
    for comp in store.enemy_comps:
        defs = [store.enemies[e] for e in comp.enemy_ids if e in store.enemies]
        if not defs:
            continue
        if len({d.can_surprise for d in defs}) > 1 or            len({d.can_be_surprised for d in defs}) > 1:
            mixed += 1
    assert mixed < 0.02 * len(store.enemy_comps), mixed


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
