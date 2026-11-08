"""The two fidelity fixes made before the retrain of 2026-09-02.

Both were mechanics the engine already implemented and the envs could not
reach, which is the most expensive kind of gap: the code looks right, the
tests pass, and the training distribution quietly excludes a thing the real
game does constantly.
"""
import collections
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest

from tests._fixtures import get_store
from engine.combat_engine import (UNDERLEVELED_STRESS_DMG_MULT_PER_LEVEL,
                                  UNDERLEVELED_STRESS_PER_LEVEL)
from env.expedition_env import (ExpeditionEnv, TIER_TO_QUEST_DIFFICULTY,
                                TIER_TO_RESOLVE_WEIGHTS, TORCH_SNUFFED_P)
from env.expedition_sequence_env import ExpeditionSequenceEnv

store = get_store()


def _band(torch):
    for lower, name in ((75, "radiant"), (50, "dim"), (25, "dark"), (0, "black")):
        if torch > lower:
            return name
    return "pitch black"


# -- the underleveled-hero penalty ---------------------------------------

def test_the_quest_difficulty_map_is_the_games_own_three():
    """Mash tiers 1-2 / 3-4 / 5 are Apprentice / Veteran / Champion, and DD's
    quest difficulties for those are 1 / 3 / 5 -- the same three values
    `town_state.Quest.difficulty` carries."""
    assert TIER_TO_QUEST_DIFFICULTY == {1: 1, 2: 1, 3: 3, 4: 3, 5: 5}


def test_only_the_APPRENTICE_bracket_can_be_underleveled():
    """Veteran fields resolve 3-4 against difficulty 3 and Champion 5-6 against
    5, so both are always at or above the bar. If a resolve weight ever drops
    below its bracket's difficulty this test says so before a training run
    silently gets harder."""
    for tier, weights in TIER_TO_RESOLVE_WEIGHTS.items():
        difficulty = TIER_TO_QUEST_DIFFICULTY[tier]
        short = [lvl for lvl, _w in weights if lvl < difficulty]
        if tier in (1, 2):
            assert short == [0], (tier, short)
        else:
            assert short == [], (tier, short)


def test_a_resolve_zero_hero_on_an_apprentice_quest_is_charged(monkeypatch):
    """The real game's rule, from the wiki: "Resolve level 0 heroes will always
    have this stress penalty on their first expedition". 20 per level of
    difference, and Fran's own play log shows 13 of 15 heroes entering at
    exactly 20."""
    env = ExpeditionEnv(store=store, resolve_sampling=True, sampler_seed=3)
    charged = []
    for seed in range(40):
        env.reset(seed=seed)
        difficulty = env._quest_difficulty_level
        for hero in env.engine.heroes:
            if hero.level == 0 and difficulty == 1:
                charged.append(hero.stress)
    assert charged, "no resolve-0 Apprentice hero was ever sampled"
    assert all(s == UNDERLEVELED_STRESS_PER_LEVEL for s in charged), charged


def test_the_stress_DAMAGE_multiplier_rides_along():
    """The compounding half, and the one that matters: a flat +20 would leave
    the eval understating by a constant, while +25% widens the gap with every
    stress event for the rest of the raid.

    Driven through the ENGINE with no kit, not through a sampled env. In an
    env the sampled trinkets move the same multiplier in both directions --
    `boss_prophet` reduces stress taken, so a resolve-0 Arbalest wearing it
    reads 1.10 against the penalty's own 1.25 -- and that is the engine working
    rather than the penalty missing. Isolating it is the only way to assert the
    number instead of a bound.
    """
    from engine import CombatEngine, SeededRNG

    for shortfall, difficulty in ((0, 0), (1, 1), (2, 2)):
        engine = CombatEngine(store, SeededRNG(1))
        engine.reset(hero_defs=[store.heroes["crusader"]],
                     enemy_defs=[store.enemies["cultist_brawler_A"]],
                     hero_positions=[1], hero_levels=[0],
                     quest_difficulty_level=difficulty or None,
                     surprise=(False, False))
        hero = engine.heroes[0]
        assert hero.stress == UNDERLEVELED_STRESS_PER_LEVEL * shortfall
        assert hero.stress_dmg_received_mult == pytest.approx(
            1.0 + UNDERLEVELED_STRESS_DMG_MULT_PER_LEVEL * shortfall)


def test_a_hero_AT_or_ABOVE_the_quest_level_is_charged_nothing():
    """The bar is `max(0, difficulty - resolve)`, so being over-levelled is
    free rather than negative."""
    from engine import CombatEngine, SeededRNG

    for level in (1, 3, 6):
        engine = CombatEngine(store, SeededRNG(1))
        engine.reset(hero_defs=[store.heroes["crusader"]],
                     enemy_defs=[store.enemies["cultist_brawler_A"]],
                     hero_positions=[1], hero_levels=[level],
                     quest_difficulty_level=1, surprise=(False, False))
        hero = engine.heroes[0]
        assert hero.stress == 0
        assert hero.stress_dmg_received_mult == pytest.approx(1.0)


def test_the_sequence_envs_OPENING_combat_is_charged_too():
    """The opening fight goes through the base env's own `reset`, on the
    explicit-scenario path, so the difficulty has to travel in the OPTIONS --
    an attribute alone was wiped before the engine saw it, and the first fight
    of every expedition was the one place the penalty did not apply."""
    env = ExpeditionSequenceEnv(store=store, resolve_sampling=True,
                                sampler_seed=5)
    charged = []
    for seed in range(30):
        env.reset(seed=seed)
        if env._quest_difficulty_level != 1:
            continue
        charged += [h.stress for h in env.engine.heroes if h.level == 0]
    assert charged, "no resolve-0 Apprentice hero in the sequence env"
    # `>=`, not `==`: `reset()` auto-resolves the enemy turns ahead of the first
    # hero, so a monster can land stress before the party is readable -- the
    # exact contamination the sibling test's header warns about, and what the
    # enemy `.launch` fix of 2026-09-19 surfaced by changing which skills those
    # monsters pick. The regression this guards is the penalty NOT being
    # applied at all (stress 0 in the opening fight), which `>=` still catches.
    assert all(s >= UNDERLEVELED_STRESS_PER_LEVEL for s in charged), charged


def test_an_explicit_scenario_is_unchanged_unless_it_asks():
    """Every pinned-comp path -- `eval_expedition`, `bridge/rollout`,
    `tools/comp_ga` -- must be byte-identical unless it opts in. Same rule the
    kit and loadout knobs follow."""
    env = ExpeditionEnv(store=store, sampler_seed=1)
    env.reset(seed=0, options={"hero_ids": ["crusader", "vestal"],
                               "enemy_ids": ["cultist_brawler_A"],
                               "hero_levels": [0, 0]})
    assert env._quest_difficulty_level is None
    assert all(h.stress == 0 for h in env.engine.heroes)


# -- the torch, and the band that could not be drawn ----------------------

def test_the_pitch_black_band_is_reachable_at_all():
    """`rules.json` gives torch <= 0 its own band -- +35 stress chance, +40%
    stress damage, +5 monster crit -- and `TORCH_LEVEL_DECILES` starts at 3.0,
    so the sampler could not produce it in any training run or eval this
    project has ever taken."""
    env = ExpeditionEnv(store=store, sampler_seed=1)
    draws = [env._sample_torch_level() for _ in range(4000)]
    assert any(t <= 0.0 for t in draws)


def test_the_snuffed_share_matches_what_real_play_measured():
    """4 of 30 fights in `bat/play.log` opened at pitch black. n is thin, which
    is why it is a named constant with the sample in it rather than a re-fitted
    decile table."""
    env = ExpeditionEnv(store=store, sampler_seed=1)
    draws = [env._sample_torch_level() for _ in range(20000)]
    share = sum(1 for t in draws if t <= 0.0) / len(draws)
    assert share == pytest.approx(TORCH_SNUFFED_P, abs=0.02)
    assert TORCH_SNUFFED_P == pytest.approx(4.0 / 30.0)


def test_a_snuffed_fight_is_a_POINT_MASS_not_an_interpolated_bin():
    """Interpolating a decile down to 0.0 would not reach the band: a
    continuous draw over [0, 25] lands ON zero with probability zero. A snuff
    is a discrete event -- somebody clicked the meter out."""
    env = ExpeditionEnv(store=store, sampler_seed=2)
    zeros = [t for t in (env._sample_torch_level() for _ in range(4000))
             if t <= 0.0]
    assert zeros and all(t == 0.0 for t in zeros)


def test_the_snuffed_mass_REPLACES_the_bottom_decile():
    """It does not stack. The 206-capture corpus had a minimum of 3, so its
    lowest bin is where a snuffed-and-partly-relit fight already landed --
    counting both would double the darkness and drag the mean down ten points.
    Real play agrees the bin should go: of 30 logged fights, NONE opened in
    `dark` or `black`."""
    env = ExpeditionEnv(store=store, sampler_seed=4)
    draws = [env._sample_torch_level() for _ in range(20000)]
    bands = collections.Counter(_band(t) for t in draws)
    n = len(draws)
    assert bands["black"] == 0, "the 3..25 bin is still being drawn"
    assert 0.55 < bands["radiant"] / n < 0.70          # real play: 63.3%
    assert sum(draws) / n > 60.0                        # real play mean: 74.3


def test_the_torch_still_comes_from_the_SCENARIO_rng():
    """A reseeded scenario has to reproduce its darkness along with its
    monsters, or two arms of a comparison face different dungeons."""
    a = ExpeditionEnv(store=store, sampler_seed=11)
    b = ExpeditionEnv(store=store, sampler_seed=11)
    assert ([a._sample_torch_level() for _ in range(50)]
            == [b._sample_torch_level() for _ in range(50)])


# -- the Virtue Chance penalty, and the act-out chaining rule -------------

def test_the_onset_table_decodes_to_the_stated_virtue_penalty():
    """`effectiveDifficultyAfflictionOnsetModifiers` indexed by LEVEL
    SHORTFALL -- not by quest difficulty, which is the reading that made this
    look unimplementable because it puts virtue at zero for every Champion
    party. By shortfall it is exactly "-5% Virtue Chance per level of
    difference, except at 6 where it is -33%"."""
    from data.rules import RulesLoader
    from env.gym_env import GAME_DIR

    rules = RulesLoader.load(GAME_DIR / "shared" / "rules.json")
    table = rules.difficulty_affliction_onset_modifiers
    assert table == (0.0, 0.05, 0.1, 0.15, 0.2, 0.25, 0.33)
    assert len(table) == 7                       # shortfalls 0..6
    for i in range(1, 6):
        assert table[i] - table[i - 1] == pytest.approx(0.05)
    assert table[6] == pytest.approx(0.33)


def test_the_virtue_clamp_IS_the_affliction_bounds_inverted():
    """1% and 95%, stated. `affliction_max_chance` 0.99 and
    `affliction_min_chance` 0.05 give exactly those -- two sides of one roll
    sharing one pair of bounds. An earlier comment claimed they were "stated
    for the affliction chance, not this one" and the numbers say otherwise."""
    from data.rules import RulesLoader
    from env.gym_env import GAME_DIR

    rules = RulesLoader.load(GAME_DIR / "shared" / "rules.json")
    assert 1.0 - rules.affliction_max_chance == pytest.approx(0.01)
    assert 1.0 - rules.affliction_min_chance == pytest.approx(0.95)


@pytest.mark.parametrize("shortfall,expected", [
    (0, 0.25), (1, 0.20), (2, 0.15), (3, 0.10), (4, 0.05),
    (5, 0.01), (6, 0.01),          # 0.25 - 0.25 = 0 and 0.25 - 0.33 < 0, both
])                                  # clamped up to the 1% floor
def test_an_underleveled_hero_is_likelier_to_break(shortfall, expected):
    from data.rules import RulesLoader
    from engine import CombatEngine, SeededRNG
    from engine.stress import resolve_check
    from env.gym_env import GAME_DIR

    rules = RulesLoader.load(GAME_DIR / "shared" / "rules.json")
    virtues = trials = 0
    for i in range(3000):
        engine = CombatEngine(store, SeededRNG(i))
        engine.reset(hero_defs=[store.heroes["crusader"]],
                     enemy_defs=[store.enemies["cultist_brawler_A"]],
                     hero_positions=[1], hero_levels=[0],
                     quest_difficulty_level=shortfall or None,
                     surprise=(False, False))
        hero = engine.heroes[0]
        assert hero.level_shortfall == shortfall
        hero.stress = 100
        resolve_check(hero, rules, SeededRNG(i))
        virtues += 1 if hero.is_virtuous else 0
        trials += 1
    assert virtues / trials == pytest.approx(expected, abs=0.025)


def test_act_outs_cannot_fire_on_consecutive_turns():
    """"two different heroes cannot have act-outs occur back-to-back". Without
    it an afflicted party chains act-outs, which is the difference between an
    affliction being a problem and being a spiral."""
    from engine import CombatEngine, SeededRNG

    engine = CombatEngine(store, SeededRNG(1))
    engine.reset(hero_defs=[store.heroes["crusader"]],
                 enemy_defs=[store.enemies["cultist_brawler_A"]],
                 hero_positions=[1], surprise=(False, False))
    hero = engine.heroes[0]
    hero.is_afflicted = True
    hero.affliction_id = "fearful"
    engine.round = 3
    engine.current_turn_index = 2
    engine._last_act_out_at = (3, 1)             # the turn immediately before
    engine._act_out_rolled_for_index = -1
    assert engine._maybe_act_out(hero) is False


def test_a_hero_cannot_act_out_two_of_its_own_turns_running():
    """"cannot happen two times in a row for the same hero" -- and a hero's own
    consecutive turns are a ROUND apart, not adjacent indices, which is why
    this needs its own record rather than falling out of the one above."""
    from engine import CombatEngine, SeededRNG

    engine = CombatEngine(store, SeededRNG(1))
    engine.reset(hero_defs=[store.heroes["crusader"]],
                 enemy_defs=[store.enemies["cultist_brawler_A"]],
                 hero_positions=[1], surprise=(False, False))
    hero = engine.heroes[0]
    hero.is_afflicted = True
    hero.affliction_id = "fearful"
    engine.round = 4
    engine.current_turn_index = 0
    engine._last_act_out_at = (2, 0)             # not the previous turn
    engine._last_act_out_round_by_hero[hero.id] = 3      # but their own last
    engine._act_out_rolled_for_index = -1
    assert engine._maybe_act_out(hero) is False


def test_the_no_chaining_rule_survives_a_round_boundary():
    """Clearing the record per round would let the first hero of a round act
    out immediately after the last hero of the previous one -- which is
    back-to-back by any reading."""
    from engine import CombatEngine, SeededRNG

    engine = CombatEngine(store, SeededRNG(1))
    engine.reset(hero_defs=[store.heroes["crusader"], store.heroes["vestal"]],
                 enemy_defs=[store.enemies["cultist_brawler_A"]],
                 hero_positions=[1, 2], surprise=(False, False))
    hero = engine.heroes[0]
    hero.is_afflicted = True
    hero.affliction_id = "fearful"
    engine.round = 5
    engine.current_turn_index = 0
    engine._last_act_out_at = (4, len(engine.turn_order) - 1)
    engine._act_out_rolled_for_index = -1
    assert engine._maybe_act_out(hero) is False


# -- an affliction lasts the raid, and ENDS at 0 stress -------------------
#
# Fran, 2026-09-02: "if a hero is afflicted, it'll remain afflicted for the
# whole dungeon unless his stress value reaches 0 again ... Afflictions can
# also be cured within a dungeon, by reducing the Afflicted hero's stress to 0
# during an expedition."
#
# Persistence was already modelled. The CURE was not: seven places reduce hero
# stress and none looked at the flags, so the simulator was HARSHER than the
# game -- the rare direction, and the reason it went unnoticed.


def _break(engine, hero, seed=2):
    from engine import SeededRNG
    from engine.stress import resolve_check

    hero.stress = 100
    resolve_check(hero, engine.rules, SeededRNG(seed))
    engine._apply_affliction_effects(hero)
    return hero.affliction_id or hero.virtue_id


def _engine(hero_id="crusader"):
    from engine import CombatEngine, SeededRNG

    engine = CombatEngine(store, SeededRNG(1))
    engine.reset(hero_defs=[store.heroes[hero_id]],
                 enemy_defs=[store.enemies["cultist_brawler_A"]],
                 hero_positions=[1], surprise=(False, False))
    return engine, engine.heroes[0]


def test_reaching_zero_stress_clears_the_affliction():
    engine, hero = _engine()
    assert _break(engine, hero)
    assert hero.is_afflicted or hero.is_virtuous
    hero.stress = 0
    assert engine._maybe_clear_overstress(hero) is True
    assert not hero.is_afflicted and not hero.is_virtuous
    assert hero.affliction_id is None and hero.virtue_id is None


def test_stress_above_zero_does_NOT_clear_it():
    """"unless his stress value reaches 0 again" -- 45 is where a Resolve Check
    leaves a hero, and it is not a cure."""
    engine, hero = _engine()
    _break(engine, hero)
    hero.stress = 1
    assert engine._maybe_clear_overstress(hero) is False
    assert hero.is_afflicted or hero.is_virtuous


@pytest.mark.parametrize("hero_id", ["crusader", "vestal", "leper", "jester"])
def test_the_cure_restores_every_stat_the_trait_touched(hero_id):
    """`TraitDefinition.buff_ids` is the complete effect list -- the -15
    resists and MAXHP-10 are entries inside it -- so a cure that left them
    behind would keep the hero permanently weakened, which is the same defect
    one layer down."""
    engine, hero = _engine(hero_id)
    before = (hero.max_hp, hero.dmg_min, hero.dmg_max, hero.speed)
    floats = (hero.stun_resist, hero.dodge)
    _break(engine, hero)
    hero.stress = 0
    engine._maybe_clear_overstress(hero)
    assert (hero.max_hp, hero.dmg_min, hero.dmg_max, hero.speed) == before
    assert hero.stun_resist == pytest.approx(floats[0])
    assert hero.dodge == pytest.approx(floats[1])


def test_an_integer_stat_does_not_round_trip_through_the_buff_system():
    """Why `overstress_stats` exists rather than reversing. MAXHP-10 on 61 is
    `int(61*0.9)` = 54, and reversing is `int(54/0.9)` = 60 -- a point lost per
    cycle, and a hero can break and recover several times in a long raid."""
    assert int(int(61 * 0.9) / 0.9) == 60
    assert int(int(10 * 0.75) / 0.75) == 9


def test_a_cured_hero_does_not_get_the_trait_back_next_combat():
    """The combat boundary is where camping sits, and camping is where most
    stress healing actually happens -- so restoring the trait from carry-over
    would undo the recovery every time the next room started."""
    from engine.combatant import CarryOverHeroState

    engine, hero = _engine()
    trait = _break(engine, hero)
    assert trait
    engine2, hero2 = _engine()
    engine2._apply_carry_over(hero2, store.heroes["crusader"],
                              CarryOverHeroState(hp=30, stress=0, is_deaths_door=False,
                                                 mortality_tier="none",
                                                 affliction_id=trait))
    assert not hero2.is_afflicted and not hero2.is_virtuous


def test_a_still_stressed_hero_DOES_get_it_back():
    """The other half: persistence across combats was already right and must
    stay right."""
    from engine.combatant import CarryOverHeroState

    engine, hero = _engine()
    trait = _break(engine, hero)
    engine2, hero2 = _engine()
    engine2._apply_carry_over(hero2, store.heroes["crusader"],
                              CarryOverHeroState(hp=30, stress=45, is_deaths_door=False,
                                                 mortality_tier="none",
                                                 affliction_id=trait))
    assert hero2.is_afflicted or hero2.is_virtuous


def test_several_heroes_can_be_broken_at_once():
    """"or even multiple afflictions on several heroes" -- nothing gates this,
    and a test says so rather than leaving it to luck."""
    from engine import CombatEngine, SeededRNG

    engine = CombatEngine(store, SeededRNG(1))
    engine.reset(hero_defs=[store.heroes[h] for h in
                            ("crusader", "vestal", "jester", "leper")],
                 enemy_defs=[store.enemies["cultist_brawler_A"]],
                 hero_positions=[1, 2, 3, 4], surprise=(False, False))
    for i, hero in enumerate(engine.heroes):
        _break(engine, hero, seed=i + 2)
    assert sum(1 for h in engine.heroes if h.is_afflicted or h.is_virtuous) == 4


def test_camping_stress_relief_clears_it_too():
    """The site that matters most for the cure in a real raid."""
    engine, hero = _engine()
    _break(engine, hero)
    hero.stress = 3
    src = (Path(__file__).resolve().parents[2]
           / "engine" / "camping.py").read_text(encoding="utf-8")
    assert "is_afflicted = False" in src, "camping does not clear the flags"


# -- the DLC monster trees were never read --------------------------------
#
# Found by scanning `bat/play.log` for what the bridge saw and could not
# account for: `cannot seed: unknown monster id(s)` THIRTY times in one
# session, on `snake_cobra_A` and `snake_rattler_A` -- the Shieldbreaker's
# nightmare snakes, in ordinary Cove and Crypts runs. Thirty combat turns with
# no advice at all.


def test_the_shieldbreakers_snakes_load():
    """They are first-party shipped content in ordinary dungeons, not a
    special mode: a nightmare fires on a 50% camp roll with a Shieldbreaker in
    the party, and once all seven are done snakes replace a normal corridor
    party at random -- "even if there is no Shieldbreaker in the party"."""
    for monster in ("snake_cobra_A", "snake_rattler_A", "snake_big_adder_A"):
        assert monster in store.enemies, monster
    assert store.enemies["snake_big_adder_A"].size == 2


def test_every_loaded_monster_has_a_brain_it_can_find():
    """Loading a monster without its AI gives it stats, skills, and no way to
    choose between them. The DLC brains live in their own DLC's `raid/ai/`, so
    the two loaders had to move together."""
    missing = sorted({e.monster_brain_id for e in store.enemies.values()
                      if e.monster_brain_id not in store.monster_brains})
    assert missing == [], missing


def test_the_arena_brain_file_is_skipped_rather_than_fatal():
    """`arena.monster_brains.json` is not UTF-8 -- the PvP content ships
    encrypted, exactly as its trinket file does, and that one was already
    skipped rather than crashing the load."""
    import json
    from env.gym_env import GAME_DIR

    path = (GAME_DIR / "dlc" / "1117860_arena_mp" / "raid" / "ai"
            / "arena.monster_brains.json")
    if not path.is_file():
        pytest.skip("no arena DLC")
    with pytest.raises(UnicodeDecodeError):
        json.load(open(path, encoding="utf-8"))
    assert len(store.monster_brains) > 200      # the other four still loaded


def test_a_CORPSE_never_reaches_the_enemy_pool():
    """`store.corpses` is a separate dict precisely so nothing can sample a
    corpse as an encounter. The exact-name list `("corpse", "corpse_large")`
    was complete while only the base tree was read -- the moment the DLC trees
    were added, Color of Madness's `corpse_crystal` walked into the enemy pool,
    where a table could have drawn four of them as a fight."""
    leaked = [k for k in store.enemies if k.startswith("corpse")]
    assert leaked == [], leaked
    assert "corpse_crystal_A" in store.corpses


def test_the_dlc_merge_is_first_wins_like_every_other():
    """A DLC may ADD a monster and must never silently redefine one -- the
    same rule `DataLoader`'s other merges follow. The Crimson Court re-ships
    several vanilla monsters, which is why this is not theoretical."""
    from data.hero_loader import HeroLoader
    from env.gym_env import GAME_DIR

    roots = HeroLoader._monster_roots(GAME_DIR)
    assert roots and roots[0] == GAME_DIR / "monsters", roots
    assert len(roots) > 1, "no DLC monster trees found"


# -- the camp FOOD phase, which did not exist ----------------------------
#
# Fran: "Lets model the camping for training properly ... this just makes
# sense for medium and long dungeons". Camping resolved the 12-point skill
# phase and the 33% ambush and skipped the meal entirely -- so a medium or
# long run camped without eating, which no player has ever done.


def test_the_meals_table_is_shipped_data_not_a_convention():
    """`rules.json meals_table`, and `rations_per` is PER LIVING HERO -- which
    is what makes the wiki's "food costs reduce by one-quarter per dead party
    member" fall out rather than needing a rule of its own."""
    from data.rules import RulesLoader
    from env.gym_env import GAME_DIR

    rules = RulesLoader.load(GAME_DIR / "shared" / "rules.json")
    table = {row[0]: row[1:] for row in rules.meals_table}
    assert table["none"] == (0.0, -0.2, 15.0)
    assert table["half"] == (0.5, 0.0, 0.0)
    assert table["full"] == (1.0, 0.1, 0.0)
    assert table["feast"] == (2.0, 0.25, -10.0)
    # Richest first, so a caller takes the best it can afford by walking it.
    assert [row[1] for row in rules.meals_table] == sorted(
        [row[1] for row in rules.meals_table], reverse=True)


class _Hero:
    def __init__(self, hp, max_hp, stress=0, alive=True):
        self.hp, self.max_hp, self.stress, self._alive = hp, max_hp, stress, alive

    def is_alive(self):
        return self._alive


def _meal(party, food):
    from engine.camping import choose_meal
    return choose_meal(store.rules, party, food)


def test_a_hurt_party_feasts_when_it_can_afford_to():
    assert _meal([_Hero(20, 40, 30)] * 4, 20)[:2] == ("feast", 8)


def test_the_cost_falls_by_a_quarter_per_dead_party_member():
    """2 rations per hero, so a feast is 8 for four and 6 for three."""
    assert _meal([_Hero(20, 40, 30)] * 3, 6)[:2] == ("feast", 6)
    assert _meal([_Hero(20, 40, 30)] * 2, 4)[:2] == ("feast", 4)


def test_it_takes_the_best_it_can_AFFORD():
    assert _meal([_Hero(20, 40, 30)] * 4, 5)[:2] == ("full", 4)
    assert _meal([_Hero(20, 40, 30)] * 4, 3)[:2] == ("half", 2)


def test_starving_only_happens_when_even_a_HALF_is_unaffordable():
    """`none` is -20% max HP and +15 stress on every hero -- four times the
    damage a `full` heals, and more stress than a campfire usually removes. Two
    food is never the worse trade."""
    assert _meal([_Hero(20, 40, 30)] * 4, 1)[:2] == ("none", 0)
    assert _meal([_Hero(20, 40, 30)] * 4, 2)[:2] == ("half", 2)


def test_a_healthy_CALM_party_does_not_gorge():
    """Six food spent on nothing, and food is what the hunger check comes back
    for."""
    assert _meal([_Hero(40, 40, 0)] * 4, 20)[:2] == ("half", 2)


def test_a_healthy_but_STRESSED_party_still_feasts():
    """The whole reason the meal is a decision: a feast is the only stress
    relief in the game that costs no respite points, so the 12-point skill
    budget stays free for everything else."""
    assert _meal([_Hero(40, 40, 25)] * 4, 20)[:2] == ("feast", 8)


def test_the_meal_actually_lands_on_the_heroes():
    from engine.camping import CampResult, eat_at_camp

    party = [_Hero(20, 40, 30) for _ in range(4)]
    result = CampResult()
    spent = eat_at_camp(store.rules, party, 20, result)
    assert spent == 8 and result.meal == "feast"
    for hero in party:
        assert hero.hp == 30            # 20 + 25% of 40
        assert hero.stress == 20        # 30 - 10
    assert result.food_spent == 8


def test_a_dead_hero_is_neither_fed_nor_counted():
    from engine.camping import CampResult, eat_at_camp

    party = [_Hero(20, 40, 30) for _ in range(3)] + [_Hero(0, 40, 0, alive=False)]
    result = CampResult()
    assert eat_at_camp(store.rules, party, 20, result) == 6    # three alive
    assert party[3].hp == 0


def test_NO_food_argument_means_NO_food_phase_not_starvation():
    """`None` and `0` are different outcomes and the difference is 20% of every
    hero's max HP plus 15 stress. Every caller that predates the phase --
    `bridge/camp.py`, and every test that camps a party directly -- passes
    nothing and must get exactly the behaviour it had."""
    import inspect

    from engine.camping import resolve_camp

    assert inspect.signature(resolve_camp).parameters["food"].default is None
    party = [_Hero(20, 40, 30) for _ in range(4)]
    before = [(h.hp, h.stress) for h in party]
    from engine import SeededRNG

    result = resolve_camp(SeededRNG(1), store, party, [[] for _ in party])
    assert [(h.hp, h.stress) for h in party] == before
    assert result.meal is None and result.food_spent == 0


def test_an_EMPTY_pack_really_does_starve():
    from engine.camping import CampResult, eat_at_camp

    party = [_Hero(40, 40, 0) for _ in range(4)]
    result = CampResult()
    assert eat_at_camp(store.rules, party, 0, result) == 0
    assert result.meal == "none"
    for hero in party:
        assert hero.hp == 32            # 40 - 20% of 40
        assert hero.stress == 15


def test_the_EVAL_path_does_not_fire_the_penalty_that_TRAINING_does():
    """A TRAIN/EVAL MISMATCH, found 2026-09-03 while trying to explain a
    long-band drop after the post-fidelity retrain.

    `ExpeditionSequenceEnv.reset` sets `_quest_difficulty_level` two different
    ways. The SAMPLED path -- what training uses -- takes it from
    `TIER_TO_QUEST_DIFFICULTY`. The EXPLICIT path -- what `eval_expedition`,
    `bridge/rollout` and `tools/comp_ga` all use -- takes
    `options.get("quest_difficulty_level")`, which is **None** unless the
    caller passes it, and `CombatEngine.reset` reads None as zero shortfall.

    So the 2026-09-02 headline fidelity fix applies while a policy learns and
    not while it is scored. Measured on a tier-1 cell: hero levels came out
    [1, 0, 2, 0] against difficulty 1, and every hero still entered on 0 stress
    instead of +20 for the two at resolve 0.

    This test does not assert which behaviour is RIGHT -- an explicit scenario
    keeping the old behaviour unless asked is a deliberate convention here. It
    asserts that the two paths DISAGREE, so the disagreement cannot go quiet
    again, and that `quest_difficulty_level` in the options is what closes it.
    """

    heroes = ["crusader", "vestal", "plague_doctor", "highwayman"]

    # `level_shortfall` and `stress_dmg_received_mult` are the discriminators,
    # NOT `hero.stress`. `reset()` auto-resolves every enemy turn ahead of the
    # first hero, so by the time the party is readable the board has already
    # dealt stress -- a first attempt asserted "the off arm has 0 stress" and
    # failed on seeds where a monster simply got there first. These two are
    # written once at reset and combat cannot touch them.
    #
    # SEEDS ARE SEARCHED, not hardcoded: `TIER_TO_RESOLVE_WEIGHTS[1]` draws
    # resolve 0 only 20% of the time per hero, so a fixed seed is a coin flip.
    # Same loop-and-assert shape as
    # `test_a_resolve_zero_hero_on_an_apprentice_quest_is_charged` above.
    checked = 0
    for seed in range(40):
        opts = dict(hero_ids=heroes, comp_name=None, zone_id="crypts", tier=1,
                    length_band="short", num_combats=4, sampler_seed=seed)

        off = ExpeditionSequenceEnv(store=store, resolve_sampling=True,
                                    sampler_seed=seed)
        off.reset(seed=seed, options=dict(opts))
        assert off._quest_difficulty_level is None, "the premise: eval passes nothing"

        shortfalls = [max(0, 1 - lv) for lv in off._original_hero_levels]
        if not any(shortfalls):
            continue

        on = ExpeditionSequenceEnv(store=store, resolve_sampling=True,
                                   sampler_seed=seed)
        on.reset(seed=seed, options=dict(opts, quest_difficulty_level=1))
        assert on._quest_difficulty_level == 1
        # Same seed, same scenario, so the drawn levels match and the ONLY
        # difference is whether the shortfall was charged.
        assert off._original_hero_levels == on._original_hero_levels

        # The eval path: nobody is underleveled, however low their resolve.
        assert [h.level_shortfall for h in off.engine.heroes] == [0, 0, 0, 0]
        # The training path: charged, and compounding.
        assert [h.level_shortfall for h in on.engine.heroes] == shortfalls

        # `level_shortfall` is the SOUND discriminator: `combat_engine.py:418`
        # is its only writer, so no fight can move it. The mult is not --
        # `_buffs.py` both adds to and subtracts from it, and `reset()`
        # auto-resolves every enemy turn ahead of the first hero, so a monster
        # debuff lands before the party is readable. The header comment above
        # claims "combat cannot touch them"; that was already false when it was
        # written. Measured 2026-09-19: 3 seeds in 120 come back with
        # shortfall 0 and a moved mult, and enforcing enemy `.launch` the same
        # day shifted the search onto a 4th (seed 4) and turned this red.
        #
        # So the mult is checked only on seeds combat left alone. That costs no
        # coverage: line 417 derives the mult FROM level_shortfall, so a charge
        # leaking into the eval path cannot happen without the shortfall
        # assertions above firing first.
        off_mult = [round(h.stress_dmg_received_mult, 2) for h in off.engine.heroes]
        on_mult = [round(h.stress_dmg_received_mult, 2) for h in on.engine.heroes]
        want_on = [round(1.0 + UNDERLEVELED_STRESS_DMG_MULT_PER_LEVEL * s, 2)
                   for s in shortfalls]
        if off_mult != [1.0] * len(off_mult) or on_mult != want_on:
            continue                      # combat contaminated it -- see above
        checked += 1
        if checked == 3:
            break

    assert checked, "no resolve-0 hero drawn in 40 seeds -- the premise is gone"


def test_the_OTHER_explicit_scenario_fields_still_fall_back_to_SAMPLING():
    """The audit that followed the `quest_difficulty_level` gap, as a guard.

    `reset()` has two branches, and the failure mode is a field the SAMPLED
    branch derives while the EXPLICIT branch reads `options.get(...)` and gets
    `None` with no fallback -- which is silent, because `None` is a plausible
    value everywhere downstream.

    Comparing the branches field by field, `quest_difficulty_level` was the
    only one with that shape. `boss_quest` and `torch_level` are the two near
    misses: both are read from options, and both have an explicit `else` that
    samples when the key is absent. This pins that, so neither can quietly
    acquire the bug the other one had.
    """
    heroes = ["crusader", "vestal", "plague_doctor", "highwayman"]
    opts = dict(hero_ids=heroes, comp_name=None, zone_id="crypts", tier=1,
                length_band="short", num_combats=4, sampler_seed=5)

    # A torch is always drawn, never left at a falsy default -- the sampler's
    # own distribution has a point mass at 0, so "did it sample" is asserted by
    # the value being a real reading rather than by it being non-zero.
    env = ExpeditionSequenceEnv(store=store, sampler_seed=5)
    env.reset(seed=5, options=dict(opts))
    assert env._current_torch is not None
    assert 0.0 <= float(env._current_torch) <= 100.0

    # ...and an explicit torch still wins, or `bridge/seed.py` could not copy a
    # real save's board.
    pinned = ExpeditionSequenceEnv(store=store, sampler_seed=5)
    pinned.reset(seed=5, options=dict(opts, torch_level=37.0))
    assert pinned._current_torch == 37.0

    # boss_quest: absent means sample (p defaults to 0, so False), present wins.
    assert env._is_boss_quest is False
    forced = ExpeditionSequenceEnv(store=store, sampler_seed=5)
    forced.reset(seed=5, options=dict(opts, boss_quest=True))
    assert forced._is_boss_quest is True
