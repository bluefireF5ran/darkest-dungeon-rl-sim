"""Tests for the rollout valuation — running a REAL party through a dungeon in
simulation to answer "does this survive?" (`bridge/rollout.py`).

The point of these is that a rollout number has to mean the same thing as an
eval-table number. So the central test is an EQUIVALENCE: a scraped comp
expressed the new way (explicit per-hero levels, gear ranks, kit) must produce
a bit-identical episode to the old comp_name path when the two describe the
same party. If that ever diverges, every rollout silently stops being
comparable to the archived evals the project's decisions are anchored to.

The rest pin the specific things that made a real party different from a
sampled one, each of which was a live bug or a near miss:

* gear ranks are three independent numbers, not one derived tier;
* HP must be written AFTER the ceiling moves, or a hero bleeds HP across
  combat boundaries they never fought;
* camp skills have no comp_name to be looked up by.

No model is loaded here — `Rollout` needs sb3-contrib and a checkpoint, while
everything these assert lives in the option plumbing and the env.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest

from bridge.rollout import (QUEST_LENGTH_TO_BAND, PartyMember, RolloutResult,
                            band_for, build_options)
from engine.combatant import CarryOverHeroState
from env import ExpeditionSequenceEnv
from env.expedition_env import TIER_TO_HERO_LEVEL
from env.expedition_sequence_env import EXPEDITION_LENGTH_BANDS


# FUNCTION scope, deliberately. A module-scoped env is SHARED, so its
# `_sampler_rng` carries whatever state the tests before it left -- and under
# xdist those tests are a different subset in a different order in every worker,
# which is how `test_fleeing_buys_no_recovery` came to fail about one run in
# three on 2026-08-27 and pass every time the file was run alone. Construction
# is 0.3 ms against a cached store, so module scope was buying nothing.
@pytest.fixture
def env():
    return ExpeditionSequenceEnv()


def _scenario(**over):
    base = dict(zone_id="weald", tier=3, length_band="medium", num_combats=5, comp_name=None)
    base.update(over)
    return base


# ── the equivalence that keeps rollouts comparable to the eval table ──

def test_explicit_party_reproduces_the_comp_path_exactly(env):
    """Same party, described two ways, must build the same board.

    The convention the comp path uses is `tier_idx = resolve_level - 1` for both
    gear tracks, so a PartyMember carrying exactly that has to be a no-op
    against it. This is what licenses reading a rollout's P(success) on the same
    scale as `eval_traits_3M.log`'s SUCCESS.

    **`_sampler_rng` has to be reseeded between the two resets.** `reset(seed=)`
    reseeds the combat RNG only; which enemy comp gets drawn comes from a
    separate generator that keeps advancing, so the same seed twice faces
    different monsters — and `reset()` auto-resolves any enemy turns ahead of
    the first hero, so a different enemy line means the party opens on different
    HP. That is not a difference between the two paths, and this test flaked
    until it was pinned."""
    hero_ids = ["man_at_arms", "highwayman", "highwayman", "vestal"]
    tier = 3
    level = TIER_TO_HERO_LEVEL[tier]

    env.unwrapped._sampler_rng.seed(99)
    env.reset(seed=7, options=_scenario(tier=tier, hero_ids=hero_ids))
    conventional = [(h.definition_id, h.hp, h.max_hp, h.speed, h.dodge, h.prot,
                     h.crit, h.dmg_min, h.dmg_max, h.skill_tier) for h in env.engine.heroes]

    party = [PartyMember(hid, resolve_level=level,
                         weapon_rank=level - 1, armour_rank=level - 1)
             for hid in hero_ids]
    opts = build_options(party)
    opts.update(_scenario(tier=tier))
    env.unwrapped._sampler_rng.seed(99)
    env.reset(seed=7, options=opts)
    explicit = [(h.definition_id, h.hp, h.max_hp, h.speed, h.dodge, h.prot,
                 h.crit, h.dmg_min, h.dmg_max, h.skill_tier) for h in env.engine.heroes]

    assert explicit == conventional


# ── gear: three independent numbers ──

def test_real_gear_ranks_beat_the_resolve_derived_tier(env):
    """A save's ACTUAL ranks must win over anything derived from resolve.

    The original case here was a resolve-2 Vestal with rank-2 armour, whose
    real 34 max HP was derived as 29 — then `_apply_carry_over` clamped her
    real HP down to it and a damaged hero read as full. That specific pair no
    longer diverges: since 2026-08-20 the derived tier comes from the game's
    own upgrade gating, and resolve 2 genuinely buys that rank, so derivation
    now agrees with the save. Agreement is the fix working, not the test
    passing vacuously.

    So the case is restated with gear resolve does NOT imply. A hero can be
    carrying better armour than their resolve would suggest — inherited from
    another roster slot, or bought before a resolve reset — and the save is
    still the authority. Pick a rank the derivation cannot produce and check
    the save wins.
    """
    env.reset(seed=3, options=_scenario(hero_ids=["vestal"], tier=1))
    derived = env.engine.heroes[0].max_hp

    over_geared = 4          # resolve 2 derives index 2; the save says 4
    party = [PartyMember("vestal", resolve_level=2, weapon_rank=over_geared,
                         armour_rank=over_geared)]
    opts = build_options(party)
    opts.update(_scenario(tier=1))
    env.reset(seed=3, options=opts)
    real = env.engine.heroes[0].max_hp

    assert derived < real, (derived, real)
    assert real == env.store.heroes["vestal"].armour_tiers[over_geared].hp


def test_skill_tier_follows_weapon_rank_not_resolve(env):
    party = [PartyMember("crusader", resolve_level=6, weapon_rank=1, armour_rank=5)]
    opts = build_options(party)
    opts.update(_scenario())
    env.reset(seed=3, options=opts)
    assert env.engine.heroes[0].skill_tier == 1


# ── HP after the ceiling moves ──

def _board_ready_snapshot(env, opts, seed=3):
    """Reset, and capture the party the instant the board is built.

    `_on_board_ready` is the window after every hero is fully equipped and
    before ANYTHING has acted -- which is the only place "the party opens at
    full" is a statement about equipment rather than about how the opening
    round went.
    """
    inner = env.unwrapped
    original = inner._on_board_ready
    snap = {}

    def spy():
        original()
        snap["heroes"] = [(h.hp, h.max_hp) for h in inner.engine.heroes]

    inner._on_board_ready = spy
    try:
        env.reset(seed=seed, options=opts)
    finally:
        del inner._on_board_ready
    return snap["heroes"]


def test_full_party_opens_full_even_when_gear_raises_max_hp(env):
    """Rank-5 armour at resolve 4 raises max HP after `reset()` already clamped
    HP against the lower resolve-derived ceiling. Before HP was written last,
    this Crusader opened at 54/67 -- a full hero reading as damaged."""
    party = [PartyMember("crusader", resolve_level=4, weapon_rank=0, armour_rank=5,
                         trinkets=("health_stone",))]
    opts = build_options(party)
    opts.update(_scenario())
    (hp, max_hp), = _board_ready_snapshot(env, opts)
    assert hp == max_hp
    # The ceiling the gear bought, not the one resolve alone derives.
    assert env.engine.heroes[0].max_hp == max_hp


def test_the_opening_enemy_turns_are_not_undone(env):
    """The opposite bug, in the same two lines, found by fixing the first
    (2026-08-21).

    Equipment used to be applied AFTER `reset()` returned -- and `reset()` has
    already let every faster enemy act. So on the fresh-party path, where the
    HP target is "full", writing HP last **healed the party back to full** and
    erased the entire opening round. A monster that won initiative did nothing
    at all.

    Now the party is equipped before the queue is walked, so an opening blow
    stays on the board. It must NOT be clamped back up, and it must not be
    clamped down to the pre-gear ceiling either."""
    party = [PartyMember("crusader", resolve_level=4, weapon_rank=0, armour_rank=5,
                         trinkets=("health_stone",))]
    opts = build_options(party)
    opts.update(_scenario())

    hurt = 0
    for seed in range(30):
        (opening_hp, opening_max), = _board_ready_snapshot(env, opts, seed=seed)
        h = env.engine.heroes[0]
        assert h.max_hp == opening_max, "the ceiling moved after the board was built"
        assert h.hp <= opening_hp, "HP was restored after the opening round"
        if h.hp < opening_hp:
            hurt += 1
    assert hurt > 0, (
        "no opening blow landed in 30 seeds -- either the scenario stopped "
        "having a faster enemy, or the damage is being erased again")


def test_hp_does_not_bleed_across_a_combat_boundary(env):
    """The compounding half of the same bug: `_start_next_combat` re-clamps
    against the low ceiling every combat, so a real party would lose HP between
    fights it never took damage in."""
    party = [PartyMember("crusader", resolve_level=4, weapon_rank=0, armour_rank=5),
             PartyMember("vestal", resolve_level=4, weapon_rank=0, armour_rank=5)]
    opts = build_options(party)
    opts.update(_scenario(num_combats=4))
    env.unwrapped._sampler_rng.seed(21)
    env.reset(seed=11, options=opts)

    ceilings = {}
    for hero in env.engine.heroes:
        hero.hp = hero.max_hp
        ceilings[hero.definition_id] = hero.max_hp
    env._snapshot_and_advance_roster()
    env._combat_idx += 1
    env._start_next_combat()

    for hero in env.engine.heroes:
        # The ceiling must survive the rebuild — if it drops back to the
        # resolve-derived tier, HP was re-clamped against the wrong number.
        assert hero.max_hp == ceilings[hero.definition_id]
        # Food heals on the boundary and enemy turns can hurt; the failure mode
        # was a hero arriving at the LOW ceiling, far below their real one.
        assert hero.hp > ceilings[hero.definition_id] * 0.8


def test_carry_over_opens_a_damaged_party(env):
    """Asserted as bounds, not equality: `reset()` auto-resolves any enemy turns
    that come before the first hero, so the party can already have been hit and
    stressed by the time it is observable. Those turns can only push HP down and
    stress up, never restore either — which is exactly what distinguishes a
    carry_over that arrived from one that was dropped."""
    party = [PartyMember("vestal", resolve_level=2, weapon_rank=2, armour_rank=2,
                         hp=12, stress=40)]
    opts = build_options(party)
    assert "carry_over" in opts
    opts.update(_scenario())
    # Pinned, because this test is about gear ranks reaching the engine and
    # nothing else. A sampled torch put this lone Vestal in the black, where the
    # darkness stress multipliers carried her from 40 stress to afflicted during
    # reset()'s auto-resolved enemy turns — and every affliction carries
    # MAXHP-10, so the ceiling assertion below read 31 instead of 34.
    opts["torch_level"] = 100.0
    env.unwrapped._sampler_rng.seed(4)
    env.reset(seed=3, options=opts)
    h = env.engine.heroes[0]
    assert h.max_hp == 34, "real gear ranks did not reach the engine"
    assert h.hp <= 12
    assert h.stress >= 40


def test_untouched_party_needs_no_carry_over():
    """A fresh party must go through the plain reset path — a carry_over would
    pin HP to a number computed before gear and trinkets moved the ceiling."""
    party = [PartyMember("crusader"), PartyMember("vestal")]
    assert "carry_over" not in build_options(party)


def test_partly_hurt_party_still_carries_an_entry_per_hero():
    party = [PartyMember("crusader", hp=5), PartyMember("vestal")]
    opts = build_options(party)
    assert len(opts["carry_over"]) == len(party)
    assert all(isinstance(c, CarryOverHeroState) for c in opts["carry_over"])


# ── kit that no comp_name can resolve ──

def test_camp_skills_reach_the_env_for_a_real_party(env):
    """Without these a real party gets no campfire at all, which removes the
    medium and long bands' whole recovery and biases any comparison towards
    short quests."""
    party = [PartyMember("crusader", camp_skills=("encourage", "pep_talk")),
             PartyMember("vestal", camp_skills=("unshakable_faith",))]
    opts = build_options(party)
    opts.update(_scenario())
    env.reset(seed=3, options=opts)
    assert env._original_camp_skill_ids == [["encourage", "pep_talk"], ["unshakable_faith"]]


def test_explicit_kit_is_positional_across_duplicate_classes(env):
    """Two Highwaymen with different loadouts must stay two different heroes —
    a hero_id-keyed dict collapses them onto whichever was inserted last."""
    party = [
        PartyMember("highwayman", skill_ids=("wicked_slice",), trinkets=("health_stone",)),
        PartyMember("highwayman", skill_ids=("open_vein",), trinkets=()),
    ]
    opts = build_options(party)
    assert opts["hero_skill_ids"] == [["wicked_slice"], ["open_vein"]]
    assert opts["trinkets_and_quirks"] == [(["health_stone"], []), ([], [])]


def test_explicit_kit_wins_over_a_comp_lookup(env):
    """A comp_name that half-matches a real party would apply the wrong hero's
    gear, which is worse than applying none."""
    party = [PartyMember("crusader", trinkets=("health_stone",))]
    opts = build_options(party)
    opts.update(_scenario(comp_name="Holy Hymn: Twin Heist"))
    env.reset(seed=3, options=opts)
    assert env._original_trinkets_and_quirks == [(["health_stone"], [])]


# ── the sampled path must be untouched ──

def test_sampled_reset_still_fields_a_uniform_resolve_level(env):
    env.reset(seed=5)
    assert env._original_gear_ranks is None
    assert set(env._original_hero_levels) == {TIER_TO_HERO_LEVEL.get(env._tier, 6)}
    assert len(env._original_hero_levels) == len(env._original_hero_ids)


def test_per_hero_levels_survive_a_death(env):
    """Levels are indexed against the ORIGINAL roster, so losing the first hero
    must not shift everybody else's level up one slot."""
    party = [PartyMember("crusader", resolve_level=1), PartyMember("vestal", resolve_level=5)]
    opts = build_options(party)
    opts.update(_scenario(num_combats=3))
    env.reset(seed=13, options=opts)
    env.engine.heroes[0].is_dead = True
    env._snapshot_and_advance_roster()
    env._combat_idx += 1
    env._start_next_combat()
    assert [h.definition_id for h in env.engine.heroes] == ["vestal"]
    assert env._alive_indices == [1]
    assert env._original_hero_levels == [1, 5]


# ── band selection ──

def test_band_for_always_returns_a_band_containing_the_count():
    """The real ranges OVERLAP (short 3-8, medium 6-10), so there is no single
    right answer for 6 — but whatever comes back must at least contain the
    count, since the band sets firewood and provisions."""
    for n in range(3, 17):
        band = band_for(n)
        lo, hi = EXPEDITION_LENGTH_BANDS[band]
        if any(l <= n <= h for l, h in EXPEDITION_LENGTH_BANDS.values()):
            assert lo <= n <= hi, f"{n} -> {band} ({lo}-{hi})"


def test_band_for_picks_the_nearest_band_by_midpoint_when_they_overlap():
    assert band_for(3) == "short"
    assert band_for(10) == "medium"
    assert band_for(16) == "long"


def test_band_for_covers_counts_outside_every_band():
    """The real bands leave a gap at 11-13, and a camping ambush can push a run
    past the top of its band. Both still have to price out somewhere."""
    assert band_for(12) in EXPEDITION_LENGTH_BANDS
    assert band_for(1) in EXPEDITION_LENGTH_BANDS
    assert band_for(40) == "long"


def test_quest_length_maps_to_the_games_own_length_names():
    """`quest_length_1..4` = Short / Medium / Long / Exhausting, from
    localization/miscellaneous.string_table.xml. Exhausting has no map-generator
    size of its own."""
    assert QUEST_LENGTH_TO_BAND[1] == "short"
    assert QUEST_LENGTH_TO_BAND[2] == "medium"
    assert QUEST_LENGTH_TO_BAND[3] == "long"
    assert QUEST_LENGTH_TO_BAND[4] == "long"
    assert set(QUEST_LENGTH_TO_BAND.values()) <= set(EXPEDITION_LENGTH_BANDS)


# -------------------------------------------------- the run score ----------
# `quality` is measured at episode end unconditionally, which is right for a
# metric reported NEXT TO success and wrong for one that REPLACES it as the
# thing a search maximises. These pin the gate that closes the hole.


def _result(qualities, progresses, **kw):
    base = dict(episodes=len(qualities), success_rate=0.0, completion_rate=0.0,
                zero_death_rate=0.0, wipe_rate=0.0, stall_rate=0.0,
                mean_deaths=0.0, mean_final_stress=0.0,
                mean_combats_reached=0.0, num_combats=14)
    base.update(kw)
    return RolloutResult(qualities=tuple(qualities),
                         progresses=tuple(progresses), **base)


def test_a_wipe_scores_zero():
    """Every hero dead means quality 0, so no amount of progress rescues it."""
    assert _result((0.0, 0.0), (1.0, 1.0)).mean_run_score == 0.0


def test_a_clean_full_run_scores_its_quality():
    assert _result((1.0,), (1.0,)).mean_run_score == 1.0
    assert _result((0.82,), (1.0,)).mean_run_score == pytest.approx(0.82)


def test_a_healthy_stall_out_must_score_below_a_bloodied_completion():
    """THE defect this gate exists for, stated as the comparison that used to
    come out backwards.

    A party that stalls out at combat 2 of 14 is in great shape -- it barely
    fought -- so raw quality ranks it ABOVE a party that cleared all fourteen
    and came home at half HP. A fitness that believes that would select comps
    for their ability to do nothing safely.
    """
    stalled = _result((0.90,), (2 / 14,), stall_rate=1.0, mean_combats_reached=2.0)
    finished = _result((0.55,), (1.0,), completion_rate=1.0, success_rate=1.0,
                       mean_combats_reached=14.0)
    assert stalled.mean_quality > finished.mean_quality      # the trap
    assert stalled.mean_run_score < finished.mean_run_score  # the gate


def test_progress_is_partial_credit_not_a_cliff():
    """Two total failures still have to be ordered -- the comp that reached
    combat 12 of 14 is not the comp that reached combat 2."""
    far = _result((0.5,), (12 / 14,))
    near = _result((0.5,), (2 / 14,))
    assert far.mean_run_score > near.mean_run_score


def test_the_run_score_error_bar_is_the_sample_sd_not_the_binomial_one():
    """`run_score` is continuous on [0, 1], so `p(1-p)/n` is the wrong form and
    would report a wide error for a comp whose runs all land in one place."""
    tight = _result((0.5,) * 20, (1.0,) * 20, success_rate=0.5)
    assert tight.run_score_se == 0.0
    assert tight.standard_error > 0.0          # the binomial one still fires
    spread = _result((0.0, 1.0) * 10, (1.0,) * 20)
    assert spread.run_score_se > 0.0


def test_the_search_can_reweight_stress_without_moving_the_eval():
    """Fran chose 2:1 stress-over-HP for the SEARCH on 2026-08-26, after the
    first run came home at 38.8 mean stress against 19.8 for his own top eight
    while the ranking could barely see it.

    The eval headline keeps the declared 50/50 -- every archived QUALITY number
    was measured on it -- so the weighting has to be a property of the CONSUMER.
    That is only possible because the two halves are carried unmixed.
    """
    # One episode, full HP, badly stressed: hp 1.0, calm 0.25.
    r = _result((0.625,), (1.0,), quality_parts=((1.0, 0.25),))
    assert r.weighted_run_scores(0.5) == pytest.approx((0.625,))
    assert r.mean_run_score == pytest.approx(0.625), "the default must not move"
    # 2:1 toward stress: 1/3 * 1.0 + 2/3 * 0.25 = 0.5
    assert r.weighted_run_scores(2 / 3) == pytest.approx((0.5,))
    assert r.weighted_qualities(2 / 3) == pytest.approx((0.5,))


def test_reweighting_still_multiplies_out_against_progress():
    """`score` is the mean of `quality x progress`, so a caller printing both
    must weight them the same way or the printed line shows a product that does
    not multiply out."""
    r = _result((0.9,), (0.5,), quality_parts=((1.0, 0.4),))
    q = r.weighted_qualities(2 / 3)[0]
    assert r.weighted_run_scores(2 / 3)[0] == pytest.approx(q * 0.5)


def test_an_outcome_with_no_components_falls_back_to_the_mixed_quality():
    """An older result -- or a pickled one from before the split -- carries no
    `quality_parts`. It must still read, at the quality it was recorded with."""
    r = _result((0.8,), (1.0,))
    assert r.weighted_run_scores(2 / 3) == pytest.approx((0.8,))
