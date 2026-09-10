"""Who steps on the trap, and who opens the curio.

Both are Fran's rules from 2026-09-02, and both turned out to be things the
game states rather than things to encode:

* every disarmable trap ships `success_effects: ["Heal Stress TrapD"]`, which
  is his "trap defuse relieves stress";
* the Antiquarian's own `.info.darkest` carries `extra_curio_loot`, which is
  his "choose antiquarian if available".

The load-bearing check here is
`test_the_derived_trap_resistances_reproduce_common_knowledge`: a derivation
that reproduces something nobody derived it from is measuring the right thing.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest

from bridge import curios, traps

from data.paths import GAME_DIR

pytestmark = pytest.mark.skipif(not GAME_DIR.exists(),
                                reason="needs the game install")

GD = str(GAME_DIR)


@pytest.fixture(scope="module")
def store():
    from tests._fixtures import get_store
    return get_store()


def hero(hero_class, guid="h", stress=0.0, hp_fraction=1.0, quirks=(), trinkets=()):
    return curios.CurioHero(id=str(guid), hero_class=hero_class, stress=stress,
                            hp=hp_fraction, max_hp=1.0, quirks=tuple(quirks))


# -- the trap stat --------------------------------------------------------

def test_the_derived_trap_resistances_reproduce_common_knowledge(store):
    """Nobody derived the "Grave Robber is the trap hero" folklore from
    `.info.darkest`, and the derivation lands on it: 50% top, the three
    scouting classes at 40, the Flagellant at 0."""
    got = {c: traps.trap_resist(store, c) for c in store.heroes}
    assert got["grave_robber"] == pytest.approx(0.50)
    assert max(got.values()) == pytest.approx(0.50)
    for cls in ("houndmaster", "highwayman", "bounty_hunter"):
        assert got[cls] == pytest.approx(0.40), cls
    assert got["flagellant"] == pytest.approx(0.0)


def test_NO_shipped_quirk_carries_trap_resistance(store):
    """`trap_resist`'s own docstring says so -- "0 of the 33 trap buffs sits
    on one... the absence is worth a test rather than an assumption" -- and
    this is that test. It used to be the opposite: search the quirks for a
    carrier and skip when none turned up, which is a test that has never once
    run and reports as a skip rather than as the finding it is.

    The quirk PATH is deliberately kept in `trap_resist` because a mod can add
    a carrier. What exercises it today is the trinket side, 35 carriers
    strong, tested below -- the two share `_buff_trap_amount` and the
    `stat_sub_type` axis check that is the point of both.
    """
    carriers = [q for q, d in store.quirks.items()
                if any(traps._buff_trap_amount(store, b)
                       for b in (getattr(d, "buffs", None) or ()))]
    assert carriers == [], (
        f"a quirk carries trap resistance now: {carriers}. That is not a "
        f"failure -- it means the quirk path is live and wants its own test.")


def test_a_buff_on_another_axis_is_not_counted(store):
    """SPD2 is `combat_stat_add`/speed. If the sub-type test were dropped it
    would read as +2 trap resistance."""
    base = traps.trap_resist(store, "crusader")
    speedy = [q for q, d in store.quirks.items()
              if "SPD2" in (getattr(d, "buffs", None) or ())]
    if not speedy:
        pytest.skip("no SPD2 quirk")
    assert traps.trap_resist(store, "crusader", quirks=(speedy[0],)) == base


# -- the chance -----------------------------------------------------------

def test_the_difficulty_and_scout_terms_are_the_shipped_ones():
    assert traps.difficulty_penalty(1, GD) == 0.0
    assert traps.difficulty_penalty(3, GD) == pytest.approx(0.2)
    assert traps.difficulty_penalty(5, GD) == pytest.approx(0.4)
    assert traps.scout_bonus(GD) == pytest.approx(0.4)
    # Out-of-range clamps to the ends rather than raising.
    assert traps.difficulty_penalty(99, GD) == traps.difficulty_penalty(6, GD)


def test_scouting_helps_and_difficulty_hurts(store):
    plain = traps.disarm_chance(store, "grave_robber", difficulty=1)
    hard = traps.disarm_chance(store, "grave_robber", difficulty=5)
    scouted = traps.disarm_chance(store, "grave_robber", difficulty=5, scouted=True)
    assert plain > hard and scouted > hard
    assert 0.0 <= hard <= 1.0


def test_the_chance_is_clamped_not_negative(store):
    assert traps.disarm_chance(store, "flagellant", difficulty=6) == 0.0


def test_the_formula_is_declared_unverified():
    """DD draws the disarm percentage on the trap tile's own hover text, so
    this is one live frame from being settled -- and the module says so in the
    line it prints rather than in a comment."""
    assert traps.DISARM_MEASURED is False


# -- who to send ----------------------------------------------------------

def test_an_ambush_trap_is_refused_rather_than_advised(store):
    """`spikes_ambush` ships no `success_effects`: there is no disarm to offer
    a hero for, so advising one would be advice about a choice DD never
    presents."""
    assert "spikes_ambush" not in traps.disarmable_traps(GD)
    assert "spikes" in traps.disarmable_traps(GD)
    got = traps.advise_trap(store, "spikes_ambush", [hero("grave_robber")])
    assert got.hero is None and "cannot be disarmed" in got.why


def test_above_the_threshold_the_MOST_STRESSED_qualified_hero_goes(store):
    """`Heal Stress TrapD` is the payout, so it should land where it is worth
    the most."""
    party = [hero("grave_robber", "gr", stress=5),
             hero("houndmaster", "hm", stress=90)]
    # Both need to clear it, so lower the bar to the houndmaster's 40%.
    got = traps.advise_trap(store, "spikes", party, difficulty=1,
                            scouted=True, threshold=0.40)
    assert got.hero == "hm", got.line()
    assert "most stressed" in got.criterion


def test_below_the_threshold_the_BEST_ODDS_hero_goes_instead(store):
    """Nobody can take the payout, so the failure is what to plan for -- and
    a 90-stress hero eating `Stress 2` plus a DoT is the wrong answer."""
    party = [hero("grave_robber", "gr", stress=5),
             hero("flagellant", "fl", stress=90)]
    got = traps.advise_trap(store, "spikes", party, difficulty=6, scouted=True)
    assert got.hero == "gr", got.line()
    assert "best odds" in got.criterion


def test_the_ranking_does_not_depend_on_the_unverified_difficulty_term(store):
    """The difficulty is the same for every hero on the tile, so it cancels
    out of the comparison. That is why the ranking is trustworthy while
    `DISARM_MEASURED` is False -- and it is worth a test, because it is the
    argument the whole module ships on."""
    party = [hero("crusader", "cr", stress=10), hero("grave_robber", "gr", stress=10),
             hero("houndmaster", "hm", stress=10)]
    orders = set()
    for difficulty in range(7):
        for scouted in (False, True):
            got = traps.advise_trap(store, "spikes", party, difficulty=difficulty,
                                    scouted=scouted, threshold=0.0)
            if got.candidates:
                orders.add(tuple(c.hero_id for c in got.candidates))
    assert len(orders) == 1, orders
    # ...and the reason it holds: the ranking key is the UNCLAMPED margin.
    # Asserted on the two functions rather than through `advise_trap`, because
    # the case where the clamp bites is an UNSCOUTED trap, which offers no
    # hero to rank at all.
    classes = ("crusader", "grave_robber", "houndmaster")
    chances = [traps.disarm_chance(store, c, difficulty=6, game_dir=GD)
               for c in classes]
    margins = [traps.disarm_margin(store, c, difficulty=6, game_dir=GD)
               for c in classes]
    assert chances == [0.0, 0.0, 0.0], "clamped, all three tie"
    assert len(set(margins)) == 3, "unclamped, all three separate"
    assert sorted(margins, reverse=True) == [
        traps.disarm_margin(store, c, difficulty=6, game_dir=GD)
        for c in ("grave_robber", "houndmaster", "crusader")]


def test_an_empty_party_refuses(store):
    assert traps.advise_trap(store, "spikes", []).hero is None


# -- the curio specialist -------------------------------------------------

def test_the_curio_bonus_class_is_derived_from_the_game_file():
    """`extra_curio_loot: .code "ANTIQ"` on the Antiquarian's own file. A
    class name written here instead would be right today and wrong for any
    modded class shipping the same key."""
    assert curios.extra_curio_loot_classes(GD) == frozenset({"antiquarian"})


def test_a_loot_curio_goes_to_the_specialist():
    """`Loot` is 41 of the library's outcomes and had NO hero rule at all --
    every one fell through to "no opinion"."""
    party = [hero("crusader", "cr"), hero("antiquarian", "an"), hero("vestal", "ve")]
    got = curios.advise("sconce", "rooA", {"torch": 4}, party)
    assert got.hero == "antiquarian"
    assert got.criterion == "extra curio loot"


def test_without_a_specialist_it_still_declines_to_guess():
    party = [hero("crusader", "cr"), hero("vestal", "ve")]
    got = curios.advise("sconce", "rooA", {"torch": 4}, party)
    assert got.hero is None


def test_the_stress_rule_still_wins_over_the_loot_rule():
    """The Antiquarian preference must not swallow the rule already there.

    Asserted on `_pick_hero` rather than through `advise`, because `advise`
    picks the curio's BEST-VALUED outcome first -- `holy_fountain` relieves
    stress on one roll and its best outcome is still Loot at 50%, so going in
    the front door tests the outcome ranking instead of the hero rule.
    """
    party = [hero("antiquarian", "an", stress=0), hero("crusader", "cr", stress=80)]
    stress_result = next(
        (o.results for d in curios._curios().values()
         for k, o in d.outcomes.items() if curios._relieves_stress(k, o.results)),
        None)
    if stress_result is None:
        pytest.skip("no stress-relief outcome in the library")
    picked, criterion = curios._pick_hero("Effect", stress_result, party)
    assert picked is not None and picked.hero_class == "crusader"
    assert criterion != "extra curio loot"


# -- the three sources, after Fran's correction ---------------------------
#
# "trinkets and resolve level also change the trap resistance value, so the
# class isnt the only thing" (2026-09-02). The class was the only one wired,
# and the trinket path was wired but dead.

def test_rule_type_always_is_UNCONDITIONAL_not_a_gate(store):
    """The bug this caught: 22 of the 33 trap buffs carry `rule_type
    "always"`, and skipping any buff with a truthy rule_type valued four of
    the game's best trap trinkets at exactly zero."""
    base = traps.trap_resist(store, "crusader")
    for trinket in ("ancestors_map", "raiders_talisman", "survival_guide",
                    "drifters_buckle"):
        if trinket not in store.trinkets:
            continue
        got = traps.trap_resist(store, "crusader", trinkets=(trinket,))
        assert got > base, trinket


def test_the_shipped_trap_trinkets_are_worth_what_the_buff_says(store):
    """Not just "more than base" -- the exact amount, so a mis-summed buff
    shows up rather than passing for being positive."""
    trap_buffs = {b for b, d in store.buffs.items()
                  if d.stat_type == traps.RESIST_STAT
                  and str(d.stat_sub_type) == traps.TRAP_SUB}
    base = traps.trap_resist(store, "crusader")
    checked = 0
    for trinket_id, trinket in store.trinkets.items():
        mine = [b for b in (trinket.buffs or []) if b in trap_buffs]
        if not mine:
            continue
        want = sum(store.buffs[b].amount for b in mine
                   if traps.rule_active(store.buffs[b]) is True)
        got = traps.trap_resist(store, "crusader", trinkets=(trinket_id,))
        assert got == pytest.approx(base + want), trinket_id
        checked += 1
    assert checked >= 20, f"only {checked} trap trinkets seen"


def test_resolve_level_adds_ten_percent_a_level_on_the_SAVE_scale(store):
    """Resolve runs 0-6 -- `resolve_level_thresholds` ships seven entries, the
    first being 0 -- and a resolve-0 hero is the baseline `base_stats`
    describes. So resolve 1 is already +10%."""
    base = traps.trap_resist(store, "crusader")
    for level in range(7):
        got = traps.trap_resist(store, "crusader", resolve_level=level)
        assert got == pytest.approx(base + level * traps.RESIST_PER_RESOLVE_LEVEL)
    assert traps.trap_resist(store, "crusader", resolve_level=1) > base


def test_the_engine_and_the_trap_layer_read_resolve_THE_SAME_WAY():
    """They disagreed by one level for a few hours on 2026-09-02, and the
    Resolve Level wiki page closed it: *"Each resolve level increases hero
    chance to disarm traps and resistances to stun, blight, bleed, move and
    debuff by 10%"*, over seven levels numbered 0-6.

    Two modules applying one rule is how a rule drifts, so this asserts they
    still agree rather than trusting them to.
    """
    source = (Path(__file__).resolve().parents[2]
              / "engine" / "combat_engine.py").read_text(encoding="utf-8")
    assert "max(0, level - 1) * 0.10" not in source, "the off-by-one is back"
    assert "max(0, level) * 0.10" in source
    assert traps.RESIST_PER_RESOLVE_LEVEL == 0.10

    from engine import CombatEngine, SeededRNG
    from tests._fixtures import get_store

    store_ = get_store()
    engine = CombatEngine(store_, SeededRNG(1))
    engine.reset(hero_defs=[store_.heroes["crusader"]],
                 enemy_defs=[store_.enemies["cultist_brawler_A"]],
                 hero_positions=[1], hero_levels=[4])
    hero = engine.heroes[0]
    base = store_.heroes["crusader"].base_stats
    engine_bonus = hero.stun_resist - base.stun_resist
    trap_bonus = (traps.trap_resist(store_, "crusader", resolve_level=4)
                  - traps.trap_resist(store_, "crusader"))
    assert engine_bonus == pytest.approx(trap_bonus)
    assert engine_bonus == pytest.approx(0.40)


def test_the_wiki_names_TRAPS_first_which_is_why_resolve_belongs_here():
    """*"increases hero chance to disarm traps AND resistances to ..."* -- so
    the trap bonus is not an extrapolation from the resistance rule, it is
    stated on its own. That matters because trap resistance is the one
    resistance the engine does not carry at all."""
    assert traps.RESIST_PER_RESOLVE_LEVEL == 0.10
    from tests._fixtures import get_store

    store_ = get_store()
    assert (traps.trap_resist(store_, "leper", resolve_level=6)
            == pytest.approx(traps.trap_resist(store_, "leper") + 0.60))


def test_no_shipped_quirk_carries_a_trap_resistance_buff(store):
    """Zero of the 33. The quirk path stays wired anyway -- a mod can add one
    -- but the absence is a fact worth failing on if it changes, because a
    quirk appearing would mean the ranking has a source it is ignoring."""
    trap_buffs = {b for b, d in store.buffs.items()
                  if d.stat_type == traps.RESIST_STAT
                  and str(d.stat_sub_type) == traps.TRAP_SUB}
    carriers = [q for q, d in store.quirks.items()
                if set(getattr(d, "buffs", None) or ()) & trap_buffs]
    assert carriers == []


# -- the three-valued gate ------------------------------------------------

def test_a_gate_that_cannot_be_decided_is_UNKNOWN_not_granted(store):
    """The engine grants an unread `rule_type` -- a choice made with a board
    in hand. Out here there is no board, so the third answer is the honest
    one, and `lightbelow` is why granting is the wrong default."""
    light = next((d for d in store.buffs.values()
                  if d.rule_type in traps.LIGHT_RULES
                  and d.stat_type == traps.RESIST_STAT
                  and str(d.stat_sub_type) == traps.TRAP_SUB), None)
    if light is None:
        pytest.skip("no light-gated trap buff")
    assert traps.rule_active(light, torch=None) is None
    assert traps.rule_active(light, torch=0.0) is not None
    assert traps.rule_active(light, torch=100.0) is not None


def test_a_torch_gated_buff_is_decided_once_the_torch_is_known(store):
    """The bridge reads the torch off the raid save, so this gate is knowable
    -- and both comparisons are STRICT, which is what puts the shipped
    thresholds on a darkness band edge."""
    below = next((d for d in store.buffs.values()
                  if d.rule_type == "lightbelow"
                  and d.stat_type == traps.RESIST_STAT
                  and str(d.stat_sub_type) == traps.TRAP_SUB), None)
    if below is None:
        pytest.skip("no lightbelow trap buff")
    threshold = float(below.rule_data_float or 0.0)
    assert traps.rule_active(below, torch=threshold - 1) is True
    assert traps.rule_active(below, torch=threshold) is False
    assert traps.rule_active(below, torch=threshold + 1) is False


def test_a_camp_or_combat_gate_is_decidably_false_on_a_trap_tile(store):
    """Knowledge, not caution: there is no camp, no combat round and no
    monster in front of a trap, so these are False rather than unknown."""
    for rule in ("in_camp", "firstroundonly", "monsterType"):
        buff = next((d for d in store.buffs.values() if d.rule_type == rule), None)
        if buff is None:
            continue
        assert traps.rule_active(buff) is False, rule


def test_an_undecidable_buff_is_reported_rather_than_silently_dropped(store):
    """Under-counting quietly is how a hero looks worse at traps than they
    are. The caller gets the ids and can say so."""
    light = next((b for b, d in store.buffs.items()
                  if d.rule_type in traps.LIGHT_RULES
                  and d.stat_type == traps.RESIST_STAT
                  and str(d.stat_sub_type) == traps.TRAP_SUB), None)
    if light is None:
        pytest.skip("no light-gated trap buff")
    carrier = next((t for t, d in store.trinkets.items()
                    if light in (d.buffs or [])), None)
    if carrier is None:
        pytest.skip("no trinket carries it")
    unknown = []
    traps.trap_resist(store, "crusader", trinkets=(carrier,), unknown=unknown)
    assert light in unknown


def test_the_full_stack_composes(store):
    """Class + resolve + trinket, all three at once, against the sum of the
    parts -- the check that adding a source did not replace another."""
    base = traps.trap_resist(store, "crusader")
    trinket = "ancestors_map"
    if trinket not in store.trinkets:
        pytest.skip("no ancestors_map")
    solo = traps.trap_resist(store, "crusader", trinkets=(trinket,)) - base
    full = traps.trap_resist(store, "crusader", trinkets=(trinket,), resolve_level=4)
    assert full == pytest.approx(base + solo + 4 * traps.RESIST_PER_RESOLVE_LEVEL)


def test_advice_reads_resolve_off_the_hero(store):
    """A resolve-6 Crusader beats a resolve-0 Grave Robber at traps, which is
    only true once resolve is wired -- the class table alone says 10% vs 50%."""
    class _H:
        def __init__(self, hero_class, guid, resolve):
            self.hero_class, self.id, self.resolve_level = hero_class, guid, resolve
            self.stress, self.quirks, self.trinkets = 0.0, (), ()
            self.hp_fraction = 1.0

    got = traps.advise_trap(store, "spikes",
                            [_H("crusader", "cr", 6), _H("grave_robber", "gr", 0)],
                            difficulty=1, scouted=True,
                            threshold=2.0)   # nobody clears: best odds
    assert got.hero == "cr", got.line()


# -- the wiki's four numbers, against shipped data ------------------------
#
# https://darkestdungeon.wiki.gg/wiki/Trap -- none of these was derived from
# the files this module reads, which is what makes them checks rather than
# restatements.

def test_the_best_trinket_pair_is_worth_the_fifty_five_percent_the_wiki_claims(store):
    """*"a theoretical maximum of +55% for Grave Robbers (Ancestor's Map +
    Raider Talisman)"*. The two buffs sum to exactly that out of
    `base.buffs.json`, and a Grave Robber lands on 105%.

    The strongest of the four cross-checks: nobody derived +55% from the buff
    table, and both halves have to be right for it to come out.
    """
    for trinket in ("ancestors_map", "raiders_talisman"):
        if trinket not in store.trinkets:
            pytest.skip(f"{trinket} not in this install")
    base = traps.trap_resist(store, "grave_robber")
    both = traps.trap_resist(store, "grave_robber",
                             trinkets=("ancestors_map", "raiders_talisman"))
    assert base == pytest.approx(0.50)
    assert both - base == pytest.approx(0.55)
    assert both == pytest.approx(1.05)


def test_the_difficulty_penalties_are_the_wikis_twenty_forty_fifty(store):
    """*"traps in Veteran level dungeons have +20% chance to resist disarm,
    while traps in Champion level dungeons have +40%. Darkest level dungeons
    have +50%"*, and DD's quest difficulties are Apprentice 1 / Veteran 3 /
    Champion 5 / Darkest 6. `difficulty_trap_base` indexes straight onto them,
    which is the check that the index IS the quest difficulty."""
    assert traps.difficulty_penalty(1, GD) == pytest.approx(0.0)
    assert traps.difficulty_penalty(3, GD) == pytest.approx(0.20)
    assert traps.difficulty_penalty(5, GD) == pytest.approx(0.40)
    assert traps.difficulty_penalty(6, GD) == pytest.approx(0.50)


def test_the_scouted_bonus_is_the_wikis_forty_percent():
    """*"If a trap has been scouted, the chance for a hero to disarm it is
    their Trap Resist + 40%"*."""
    assert traps.scout_bonus(GD) == pytest.approx(0.40)


def test_the_disarm_heals_the_eight_stress_the_wiki_states():
    """*"Disarming a trap renders it harmless and heals 8 stress for the
    hero"*, delivered by `Heal Stress TrapD` on every disarmable trap."""
    assert traps.DISARM_STRESS_HEAL == 8


# -- scouted or not is a different MECHANIC, not a modifier ---------------

def test_an_unscouted_trap_offers_no_hero_to_pick(store):
    """*"Trap Resist is the chance to dodge unscouted traps"* -- it fires on
    whoever walks into it, there is no disarm and nothing heals stress. Naming
    a hero would imply a choice the game is not offering."""
    party = [hero("grave_robber", "gr", stress=60), hero("crusader", "cr")]
    got = traps.advise_trap(store, "spikes", party, difficulty=1, scouted=False)
    assert got.hero is None
    assert "not scouted" in got.why
    # ...and the same party DOES get an answer once it is scouted.
    assert traps.advise_trap(store, "spikes", party, difficulty=1,
                             scouted=True).hero is not None


def test_a_disarm_chance_above_one_hundred_survives_the_champion_penalty(store):
    """*"Disarm Chance can go above 100%"* -- that headroom is exactly what
    pays for the Champion penalty, and it only works because the difficulty is
    subtracted BEFORE the clamp. Clamping the stat first would throw the
    surplus away and read 60% here instead of 100%."""
    kit = ("ancestors_map", "raiders_talisman")
    if any(t not in store.trinkets for t in kit):
        pytest.skip("trinkets missing")
    # 0.50 base + 0.55 trinkets + 0.40 scouted = 1.45, less 0.40 Champion.
    margin = traps.disarm_margin(store, "grave_robber", trinkets=kit,
                                 difficulty=5, scouted=True, game_dir=GD)
    assert margin == pytest.approx(1.05)
    assert traps.disarm_chance(store, "grave_robber", trinkets=kit,
                               difficulty=5, scouted=True, game_dir=GD) == 1.0
