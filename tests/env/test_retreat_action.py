"""Breaking off a fight — the decision the simulator could not express.

The full-expedition SUCCESS number was always measured with the party unable to
leave. That overstates how dangerous a dungeon is, because a large part of real
play is not fighting the fights you lose: a competent player walks out of a bad
board and keeps four living heroes. Until now the only two endings a combat had
were "everything dead on one side or the other".

Everything numeric here is `shared/rules.json`, not convention:

    combat_retreat_chance                     0.70
    combat_retreat_bonus_chance_per_attempt   0.05
    combat_retreat_stress                     20.0   (25.0 in New Game Plus)

The NG+ contrast is what settles the reading of the third: a harder mode ships
the same odds and a *higher* stress, so the stress is the price of leaving
rather than an unrelated constant that happens to sit nearby.

`min_hero_count_for_escape` (3) is loaded and deliberately NOT implemented — no
shipped file states what it gates and every mode ships the same value, so there
is no contrast to recover its meaning from. See ASSUMPTIONS.md.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests._fixtures import get_store

import pytest

from engine import Action, CombatEngine, SeededRNG
from env import ExpeditionSequenceEnv
import env.expedition_sequence_env as ese_mod
from env.expedition_sequence_env import EXPEDITION_CONTEXT_FEATURES
from env.gym_env import RETREAT_CHANCE_FROM_END
from env.gym_env import (ACTION_PASS, ACTION_ABANDON, ACTION_RETREAT, ACTION_SPACE_SIZE,
                          ACTION_MOVE_BASE, ACTION_PROVISION_BASE,
                          GLOBAL_FEATURES, MAX_SKILL_SLOTS, RANKS)

from data.paths import GAME_DIR
from data.paths import CSV_DIR


@pytest.fixture(scope="module")
def store():
    return get_store()


def engine_with(store, n_heroes=4, seed=1):
    eng = CombatEngine(store, SeededRNG(seed))
    eng.reset(hero_defs=[store.heroes["crusader"]] * n_heroes,
              enemy_defs=[store.enemies["cultist_brawler_A"]],
              hero_positions=list(range(n_heroes)))
    return eng


# ── the numbers are the game's ──

def test_the_odds_are_the_shipped_constants(store):
    eng = engine_with(store)
    assert eng.rules.combat_retreat_chance == pytest.approx(0.70)
    assert eng.rules.combat_retreat_bonus_chance_per_attempt == pytest.approx(0.05)
    assert eng.rules.combat_retreat_stress == pytest.approx(20.0)
    assert eng.retreat_chance() == pytest.approx(0.70)


def test_each_failed_attempt_buys_five_points(store):
    """The bonus is per ATTEMPT, so a party that keeps trying gets out
    eventually. Without it "try again" would be a strictly repeated 70% and the
    observation feature below would carry no information."""
    eng = engine_with(store)
    eng.retreat_attempts = 3
    assert eng.retreat_chance() == pytest.approx(0.85)
    eng.retreat_attempts = 20
    assert eng.retreat_chance() == 1.0, "must clamp, not exceed certainty"


# ── what a retreat does ──

class _Rigged(SeededRNG):
    """A stream whose next `random()` is forced, so the branch under test is
    the one that runs. Everything else still goes through SeededRNG — engine
    code must never call `random` directly."""

    def __init__(self, value, seed=1):
        super().__init__(seed)
        self._forced = value

    def random(self):
        if self._forced is not None:
            v, self._forced = self._forced, None
            return v
        return super().random()


def test_a_successful_retreat_ends_the_combat_without_a_defeat(store):
    """`winner` gains a THIRD value rather than folding into "enemies". A
    retreat is not a defeat — nobody died, the party keeps its HP and its
    expedition — and every caller that has to price the difference
    (ExpeditionSequenceEnv, eval_expedition) needs to see it."""
    eng = engine_with(store)
    eng.rng = _Rigged(0.1)               # below 0.70 -> out
    assert eng.attempt_retreat(eng.heroes[0]) is True
    assert eng.done is True
    assert eng.winner == "retreat"
    assert all(h.is_alive() for h in eng.heroes), "a retreat killed somebody"


def test_win_and_loss_checks_are_untouched_by_the_new_value(store):
    """Anything asking `winner == "heroes"` must be unaffected, which is the
    whole reason a third value is safer than overloading an existing one."""
    eng = engine_with(store)
    eng.rng = _Rigged(0.1)
    eng.attempt_retreat(eng.heroes[0])
    assert eng.winner != "heroes" and eng.winner != "enemies"


def test_every_living_hero_pays_the_stress_and_the_dead_do_not(store):
    eng = engine_with(store)
    eng.heroes[2].hp = 0
    eng.heroes[2].is_dead = True
    before = [h.stress for h in eng.heroes]
    eng.rng = _Rigged(0.1)
    eng.attempt_retreat(eng.heroes[0])
    for i, h in enumerate(eng.heroes):
        if i == 2:
            assert h.stress == before[i], "a dead hero was billed for leaving"
        else:
            assert h.stress > before[i], f"hero {i} left for free"


def test_a_failed_retreat_costs_the_turn_and_improves_the_next_try(store):
    eng = engine_with(store)
    eng.rng = _Rigged(0.99)              # above 0.70 -> stays
    assert eng.attempt_retreat(eng.heroes[0]) is False
    assert eng.done is False
    assert eng.retreat_attempts == 1
    assert eng.retreat_chance() == pytest.approx(0.75)
    assert all(h.stress == 0 for h in eng.heroes), "a failed retreat charged stress"


def test_the_attempt_counter_is_per_combat(store):
    """It resets in `reset()` alongside every other per-combat counter. Carried
    over, a party that failed twice in fight one would open fight two at 80%."""
    eng = engine_with(store)
    eng.retreat_attempts = 4
    eng.reset(hero_defs=[store.heroes["crusader"]],
              enemy_defs=[store.enemies["cultist_brawler_A"]])
    assert eng.retreat_attempts == 0


# ── the action ──

def test_retreat_is_offered_alongside_real_options_not_as_a_fallback(store):
    """Unlike Pass it is a decision, not a last resort. Offering it only when
    nothing else is legal would model a party that can leave a fight it has
    already lost and not one that can leave a fight it is losing."""
    eng = engine_with(store)
    acts = eng.get_valid_actions(eng.heroes[0].id)
    assert any(a.skill_id == "__retreat__" for a in acts)
    assert any(a.skill_id not in ("__retreat__", "__pass__") for a in acts), \
        "this board was supposed to have real options too"
    assert not any(a.skill_id == "__pass__" for a in acts), \
        "Pass must still be a last resort"


def test_an_afflicted_hero_never_flees(store):
    """Data, not taste: the real game has its own `retreat_from_combat` act-out
    and ships it at weight 0 in every trait. Letting the random-command act-out
    reach `__retreat__` would smuggle in the behaviour those tables switch
    off."""
    eng = engine_with(store)
    assert any(a.skill_id == "__retreat__"
               for a in eng.get_valid_actions(eng.heroes[0].id)), "premise"
    # Drive the act-out itself, many times, on the outcome that matters: an
    # afflicted hero must never end the fight by walking the party out. Reading
    # the filter back would only assert that the line I wrote is the line I
    # wrote.
    for _ in range(200):
        eng._act_out_random_command(eng.heroes[0], None)
        assert eng.winner != "retreat", "an afflicted hero fled the fight"
        assert eng.retreat_attempts == 0, "an afflicted hero tried to flee"


def test_the_index_is_appended_and_nothing_below_it_moved():
    """Every index 0..36 still means what it meant, so the layout comment and
    every reading of an old trace stay true. The space widening is the real and
    deliberate break: no old checkpoint loads into it."""
    assert ACTION_MOVE_BASE == MAX_SKILL_SLOTS * RANKS == 28
    assert ACTION_PROVISION_BASE == 32
    assert ACTION_PASS == 36
    assert ACTION_RETREAT == 37
    # ACTION_ABANDON was appended after it on 2026-08-24 under the same rule,
    # so retreat's own index is unmoved and the space widened again.
    assert ACTION_ABANDON == 38
    assert ACTION_SPACE_SIZE == 39


# FUNCTION scope, deliberately. A module-scoped env is SHARED, so its
# `_sampler_rng` carries whatever state the tests before it left -- and under
# xdist those tests are a different subset in a different order in every worker,
# which is how `test_fleeing_buys_no_recovery` came to fail about one run in
# three on 2026-08-27 and pass every time the file was run alone. Construction
# is 0.3 ms against a cached store, so module scope was buying nothing.
@pytest.fixture
def env():
    return ExpeditionSequenceEnv(sampler_seed=3)


def test_the_mask_offers_retreat_on_a_normal_turn(env):
    env.reset(options={"hero_ids": ["crusader", "vestal"],
                       "zone_id": "crypts", "tier": 1,
                       "length_band": "short", "num_combats": 3})
    # "Normal" now has to be said out loud: a surprised party cannot retreat
    # during the opening round, and surprise is rolled per combat, so leaving
    # this implicit made the test pass or fail on the draw.
    env.engine.party_surprised = False
    # ...and rebuild the table, because the mask is served from a cache built
    # when the flag was still set. `_rebuild_action_table` is what reset() and
    # step() call; nothing else re-reads legality.
    env._rebuild_action_table()
    assert env.action_masks()[ACTION_RETREAT], "retreat was never selectable"


def test_the_observation_carries_the_LIVE_retreat_chance(env):
    """The base constant would be a constant, and a constant feature carries no
    information — the same mistake torchlight made before it was sampled. What
    the policy has to see is that the second attempt is the better bet."""
    env.reset(options={"hero_ids": ["crusader", "vestal"],
                       "zone_id": "crypts", "tier": 1,
                       "length_band": "short", "num_combats": 3})
    # Counted back from the end of the globals block. This offset has been
    # bumped three times by later appends (-2, then -8, now -11), so it lives
    # in gym_env as RETREAT_CHANCE_FROM_END and anything appended to the
    # globals must bump it there rather than here.
    idx = (env.observation_space.shape[0] - EXPEDITION_CONTEXT_FEATURES
           - RETREAT_CHANCE_FROM_END)
    first = env._get_obs()[idx]
    env.engine.retreat_attempts = 2
    assert env._get_obs()[idx] > first
    assert env._get_obs()[idx] == pytest.approx(0.80)


def _flee(env, num_combats=4, seed=4, food=None):
    """Set the board up so the next `step(ACTION_RETREAT)` is a guaranteed,
    legal, successful retreat — and nothing else about the scenario matters.

    Three separate traps live here, all found the hard way:
    `reset(seed=)` does not seed `_sampler_rng`, so which dungeon this lands on
    depends on how many tests ran before it; a surprised party may not retreat
    at all in the opening round; and the mask is served from a cache, so
    mutating engine state after `reset()` needs `_rebuild_action_table`.
    Rigging the retreat roll is avoided entirely -- the per-attempt bonus takes
    the chance to a capped 1.0, which no stream can fail.
    """
    env.reset(seed=seed, options={"hero_ids": ["crusader", "vestal"],
                                  "zone_id": "crypts", "tier": 1,
                                  "length_band": "short",
                                  "num_combats": num_combats})
    if food is not None:
        env._provision_inventory["food"] = food
    env.engine.retreat_attempts = 6
    assert env.engine.retreat_chance() == 1.0
    env.engine.party_surprised = False
    env._rebuild_action_table()
    return env


def test_a_retreat_does_not_consume_the_encounter(env):
    """Fran, 2026-08-21: *"a retreat doesn't really mean that you skip the
    combat, as the combat is still on the map"*. The wiki's Retreating page
    agrees — the party is left in the hallway it entered from, and a wandering
    boss "will reappear if heroes re-enter the same hallway where they
    retreated".

    This used to advance `_combat_idx`, which made a retreat a PAID SKIP: 20
    stress and a forgone +8 bought the party out of the fight entirely."""
    _flee(env)
    before = list(env._current_entry.enemy_ids)
    _o, _r, term, trunc, _i = env.step(ACTION_RETREAT)
    assert env._combats_retreated == 1
    assert env._combat_idx == 0, "the fled room is still on the map"
    assert not term and not trunc
    # ...and it is the SAME room. Re-drawing would quietly reroll the monsters
    # the party just retreated from.
    assert list(env._current_entry.enemy_ids) == before
    assert [e.definition_id for e in env.engine.enemies] == before


def test_fleeing_the_last_fight_does_not_pay_the_completion_bonus(env):
    """The expensive half of the old behaviour, and a pure reward bug: a
    retreat sets `terminated`, so retreating from the FINAL combat fell through
    to `terminated and not party_wiped` and collected
    REWARD_EXPEDITION_COMPLETE — the full bonus for walking out of the last
    fight."""
    _flee(env, num_combats=1)
    _o, reward, term, _tr, _i = env.step(ACTION_RETREAT)
    assert not term, "the expedition cannot end by fleeing its last room"
    assert reward < ese_mod.REWARD_EXPEDITION_COMPLETE, reward


def test_fleeing_buys_no_recovery(env):
    """A retreat is not a cleared room, so neither between-rooms recovery has
    happened: the party has not eaten and it has not camped. Either one would
    make fleeing a way to BUY recovery, which is the opposite of its price."""
    _flee(env, food=3)
    hp_before = [h.hp for h in env.engine.heroes]
    env.step(ACTION_RETREAT)
    assert env._provision_inventory["food"] == 3, "the party ate on the way out"
    assert [h.hp for h in env.engine.heroes] == hp_before


def test_the_stress_is_paid_and_the_fight_starts_over(env):
    """What a re-take actually buys, and why Fran calls it the reason to flee
    "specially after being surprised": the party pays `combat_retreat_stress`
    per living hero and gets a clean opening round back — formation restored,
    monsters at full HP, round 0 again."""
    _flee(env)
    stress_before = [h.stress for h in env.engine.heroes]
    env.step(ACTION_RETREAT)
    assert env.engine.round == 0
    assert all(e.hp == e.max_hp for e in env.engine.enemies)
    paid = [h.stress - s for h, s in zip(env.engine.heroes, stress_before)]
    assert all(p >= env.engine.rules.combat_retreat_stress for p in paid), paid


def test_retreating_forfeits_the_per_combat_win_bonus(env):
    """The whole price, and none of it invented: +8 forgone plus 20 stress per
    living hero. Against REWARD_HERO_DEATH (-20) that makes fleeing correct at
    roughly half an expected death — the trade a real player is making."""
    env.reset(seed=5, options={"hero_ids": ["crusader", "vestal"],
                               "zone_id": "crypts", "tier": 1,
                               "length_band": "short", "num_combats": 4})
    env.engine.rng = _Rigged(0.1)
    _o, reward, _t, _tr, _i = env.step(ACTION_RETREAT)
    assert env._reward_win == 8.0
    assert reward < env._reward_win, (
        f"a retreat paid {reward}, at least as much as clearing the room")


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
