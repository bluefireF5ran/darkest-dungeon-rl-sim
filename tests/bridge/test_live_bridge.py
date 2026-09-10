"""The bridge to the real game: save decoding -> LiveState -> CombatEngine.

Runs against `archive/traces/raidsnaps/`, 72 real `persist.raid.json` versions
captured second-by-second through one actual fight (a Shambler, in the Warrens).
That makes these tests hermetic in the part that matters: the enemy line, the
initiative queue, the rank collapse and the corpses are all frozen on disk.

The hero side needs a roster to pair with, which the original capture run did
not take (`tools/save_watch.py --capture` does now). `paired_persist.roster.json`
is one copy of the live roster: its party — Leper / Bounty Hunter / Musketeer /
Plague Doctor, guids 44 / 7 / 121 / 118 — is the party in those snapshots, so
the classes, equipped skills and formation order are the real ones. HP and
stress may have drifted since, so nothing here asserts a specific hero HP.
"""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests._fixtures import get_store

from bridge.live_state import (LiveState, load_resolve_thresholds,
                               read_live_state, resolve_level_for_xp)
from bridge.seed import (SAVE_DOT_STAT_TYPES, SAVE_STAT_BLEED, SAVE_STAT_GUARD_MARKER, SAVE_STAT_TYPE,
                         SeedError, build_engine_from_live)
from engine.action import Action

from data.paths import GAME_DIR
from data.paths import CSV_DIR

SNAP_DIR = CSV_DIR / "archive" / "traces" / "raidsnaps"
ROSTER = SNAP_DIR / "paired_persist.roster.json"

RAIDS = sorted(SNAP_DIR.glob("*persist.raid.json"))

# Captures taken with the fixed --capture that snapshots the roster alongside
# each raid write. Self-contained and contemporaneous, so these are the ones
# that can answer "whose turn was it, really".
#
# raidsnaps2 = the first live advisor run (Weald, Riposte Style comp).
# raidsnaps3 = the second, which is the one containing a stunned monster left
#              as the last combatant of a round and a monster whose turn
#              counter trails the party by a full round.
SNAP2_DIR = CSV_DIR / "archive" / "traces" / "raidsnaps2"
SNAP3_DIR = CSV_DIR / "archive" / "traces" / "raidsnaps3"
# raidsnaps4 = an acceptance run flown specifically to settle the bleed/blight
# question: Holy Hymn: Riposte in the Weald, whose ONLY DoT source is the
# Highwayman's `opened_vein`. That single-source property is what makes the
# enemy-side DoT in it unambiguous evidence.
SNAP4_DIR = CSV_DIR / "archive" / "traces" / "raidsnaps4"


def _capture_runs(directory=SNAP2_DIR):
    """[(raid, roster), ...] per capture RUN, each in time order.

    A directory can hold several runs: `--capture` now prefixes filenames with
    the run's start time, but before that it named them by elapsed seconds
    alone and two captures into one directory interleaved. Grouping by prefix
    keeps a later raid from being read as the continuation of an earlier one —
    which silently made a partial round look like a complete one."""
    runs: dict[str, list] = {}
    for raid in sorted(directory.glob("*persist.raid.json")):
        roster = directory / raid.name.replace("raid", "roster")
        if not roster.exists():
            continue
        stem = raid.name[: -len("_persist.raid.json")]
        run_id = stem.rsplit("_", 1)[0] if "_" in stem else "legacy"
        runs.setdefault(run_id, []).append((raid, roster))
    return [runs[k] for k in sorted(runs)]


def _paired_snapshots(directory=SNAP2_DIR):
    """Every (raid, roster) pair in a directory, ignoring run boundaries.

    Fine for per-snapshot invariants; use `_capture_runs` for anything that
    reads a sequence."""
    return [pair for run in _capture_runs(directory) for pair in run]


def _state_at(directory, stamp: str) -> LiveState:
    return read_live_state(raid_file=directory / f"{stamp}_persist.raid.json",
                           roster_file=directory / f"{stamp}_persist.roster.json")


def _state(name: str) -> LiveState:
    return read_live_state(raid_file=SNAP_DIR / name, roster_file=ROSTER)


@pytest.fixture(scope="module")
def store():
    return get_store()


# ── the reader ──

def test_every_captured_snapshot_parses():
    assert RAIDS, "no raid snapshots on disk"
    for path in RAIDS:
        st = read_live_state(raid_file=path, roster_file=ROSTER)
        assert st.in_raid
        assert st.party_guids, f"{path.name} has no party"


def test_battle_snapshots_expose_a_consistent_turn_queue():
    """`currentturn` has to index the merged initiative order, or the advisor
    is naming the wrong hero. This pins the invariant even though which
    combatant it lands on is only inferred (see live_state's docstring)."""
    seen = 0
    for path in RAIDS:
        st = read_live_state(raid_file=path, roster_file=ROSTER)
        if not st.in_battle or not st.turn_order:
            continue
        seen += 1
        assert 0 <= st.battle_turn < len(st.turn_order), path.name
        # One entry per living combatant on both sides.
        assert len(st.turn_order) == len(st.heroes) + sum(
            1 for e in st.enemies if not e.is_corpse), path.name
        # Strictly descending: initiative is the sort key, and the fractional
        # part exists precisely to break ties.
        vals = [t.initiative for t in st.turn_order]
        assert vals == sorted(vals, reverse=True), path.name
    assert seen >= 10, "expected many in-battle snapshots"


def test_enemy_list_order_is_rank_order_and_collapses():
    """The real game closes the monster line when one dies — the invariant
    `_collapse_enemy_position_gaps` maintains here. Snapshot 00095 has the
    Carrion Eater in front; by 00107 the Swine Chopper has taken that rank."""
    before = _state("00095_persist.raid.json")
    after = _state("00107_persist.raid.json")
    assert [e.enemy_id for e in before.enemies][:2] == ["carrion_eater_A", "swine_reaver_A"]
    assert [e.enemy_id for e in after.enemies][:2] == ["swine_reaver_A", "carrion_eater_A"]
    assert [e.slot for e in after.enemies] == list(range(len(after.enemies)))


def test_corpses_occupy_ranks():
    st = _state("00145_persist.raid.json")
    corpses = [e for e in st.enemies if e.is_corpse]
    assert len(corpses) == 2
    # A corpse holds a rank rather than vanishing — that is what makes it block.
    assert {c.slot for c in corpses} == {0, 2}


def test_party_reads_in_formation_order_with_real_loadouts():
    st = _state("00113_persist.raid.json")
    assert [h.hero_class for h in st.heroes] == [
        "leper", "bounty_hunter", "musketeer", "plague_doctor"]
    leper = st.heroes[0]
    # Equipped skills, not the full movepool — these feed hero_skill_ids.
    assert leper.selected_skills == ["chop", "hew", "solemnity", "intimidate"]
    assert st.start_party_size == 4


def test_provisions_sum_split_stacks():
    """The same supply occupies several inventory slots (bandage 6 + bandage 1);
    reading the first hit alone would under-report the party's stock."""
    st = _state("00113_persist.raid.json")
    assert st.provisions["bandage"] == 7
    assert st.provisions["antivenom"] == 7
    assert st.provisions["holy_water"] == 5
    # Food ships with an EMPTY id and is identified by its type instead.
    assert st.provisions["food"] > 0


# ── the resolve XP table ──

def test_resolve_level_thresholds_are_read_from_game_data():
    thresholds = load_resolve_thresholds(GAME_DIR)
    assert thresholds == [0, 2, 8, 14, 24, 36, 48]


@pytest.mark.parametrize("xp,level", [(0, 0), (1, 0), (2, 1), (7, 1), (8, 2),
                                      (47, 5), (48, 6), (999, 6)])
def test_resolve_level_for_xp(xp, level):
    assert resolve_level_for_xp(xp, [0, 2, 8, 14, 24, 36, 48]) == level


# ── the seeder ──

def test_seeded_engine_matches_the_real_board(store):
    st = _state("00113_persist.raid.json")
    engine = build_engine_from_live(st, store)

    assert [h.definition_id for h in engine.heroes] == [
        "leper", "bounty_hunter", "musketeer", "plague_doctor"]
    assert [h.position for h in engine.heroes] == [0, 1, 2, 3]
    assert [e.definition_id for e in engine.enemies] == [
        "swine_reaver_A", "carrion_eater_A", "swine_drummer_A"]
    assert [e.hp for e in engine.enemies] == [21, 11, 6]
    # Stun is a per-combat flag carry_over deliberately drops, so the sync
    # pass has to put it back or the policy plans around a threat that cannot act.
    assert engine.enemies[2].is_stunned
    assert engine.round == st.battle_round


def test_seeded_engine_hands_the_turn_to_the_derived_actor(store):
    """The pointer must land on whoever `current_actor` names.

    Note this uses the FIRST capture, whose roster is not contemporaneous — its
    hero turn counters are from a later moment, so which hero it picks is not
    meaningful there. What is meaningful, and all this asserts, is that the
    engine and LiveState agree. Turn *identity* against real play is pinned on
    the self-contained capture instead, in
    `test_seeded_engine_points_at_the_hero_who_has_not_acted`."""
    st = _state("00113_persist.raid.json")
    engine = build_engine_from_live(st, store)

    cur = st.current_actor
    assert cur is not None and cur.side == "hero"
    expected_index = [h.guid for h in st.heroes].index(cur.guid)
    assert engine.get_current_actor_id() == engine.heroes[expected_index].id


def test_seeding_does_not_let_the_engine_play_the_enemies(store):
    """With auto_resolve_to_hero left at its default the monsters ahead of the
    hero in the queue would act, so the seeded board would no longer be the
    board on screen."""
    st = _state("00113_persist.raid.json")
    engine = build_engine_from_live(st, store)
    # Every combatant still holds exactly the HP the save reported.
    assert [e.hp for e in engine.enemies] == [int(e.hp) for e in st.enemies]
    assert [h.hp for h in engine.heroes] == [int(h.hp) for h in st.heroes]
    # The pointer sits on the derived next actor, and nothing has been played.
    assert engine.current_turn_index == st.turn_order.index(st.current_actor)
    assert not any("attack" in line.lower() for line in engine.log)


def test_seeded_hero_keeps_only_the_skills_actually_equipped(store):
    st = _state("00113_persist.raid.json")
    engine = build_engine_from_live(st, store)
    leper_id = engine.heroes[0].id
    assert set(engine.get_hero_skill_ids(leper_id)) == {
        "chop", "hew", "solemnity", "intimidate"}
    # The action space still indexes the FULL movepool, so slot k names the
    # same skill regardless of what this comp equipped.
    assert len(engine.get_hero_full_skill_ids(leper_id)) == 7


def test_a_corpse_seeds_as_the_monster_it_came_from(store):
    """Corpses are stored as `monster_class: "corpse_A"`, which is not a
    monster this repo loads — so seeding raised the instant anything died and
    the advisor went silent for the rest of every fight. It only shows up
    after a kill, which is why earlier captures never triggered it.

    `previous_monster_class` keeps the original as the format's NAME HASH.
    Recovering it matters beyond labelling: a large monster's corpse occupies
    two ranks, and the engine takes that from the definition's `size`."""
    st = _state_at(SNAP3_DIR, "00219")
    assert [e.is_corpse for e in st.enemies] == [False, True, False, True]

    engine = build_engine_from_live(st, store)
    assert [e.definition_id for e in engine.enemies] == [
        "cultist_brawler_A", "fungal_artillery_A", "fungal_bloat_A", "fungal_artillery_A"]
    assert [e.is_corpse for e in engine.enemies] == [False, True, False, True]
    # A corpse occupies its rank; that is what makes it block.
    assert [e.position for e in engine.enemies] == [0, 1, 2, 3]


def test_seeding_never_fails_across_a_whole_captured_raid(store):
    """Sweep, because the corpse bug was a hard failure that no single
    mid-fight snapshot happened to contain."""
    seeded = 0
    for directory in (SNAP2_DIR, SNAP3_DIR):
        for raid, roster in _paired_snapshots(directory):
            st = read_live_state(raid_file=raid, roster_file=roster)
            if not st.in_battle or st.current_actor is None:
                continue
            build_engine_from_live(st, store)      # must not raise
            seeded += 1
    assert seeded >= 20


def test_next_hero_reads_past_enemy_turns():
    """The save is only written when an action resolves, so a run of enemy
    turns produces no update — 17 seconds of silence, measured. Advising only
    for the current actor left the previous hero's move on screen for that
    whole window."""
    found = None
    for directory in (SNAP2_DIR, SNAP3_DIR):
        for raid, roster in _paired_snapshots(directory):
            st = read_live_state(raid_file=raid, roster_file=roster)
            actor = st.current_actor
            if not st.in_battle or actor is None or actor.side != "monster":
                continue
            hero, enemies_first = st.next_hero()
            if hero is not None:
                found = (hero, enemies_first)
                assert enemies_first >= 1
                assert hero.side == "hero"
                assert hero.turns_taken < st.battle_round
                break
        if found:
            break
    assert found, "expected at least one snapshot mid enemy turns"


def test_repositioning_monsters_come_from_skill_data(store):
    """A look-ahead is only safe while ranks hold still: a skill's `launch`
    decides whether the advised hero can use it at all, and the target rank
    decides what gets hit. Which monsters can move somebody is real data —
    `move_self`/`move_target`/`push`/`pull` per skill level, plus effect
    `push_distance`/`pull_distance` — not a guess."""
    from bridge.advisor import _repositioning_monsters

    movers = _repositioning_monsters(store)
    assert "swine_reaver_A" in movers          # its charge shuffles the line
    assert "spider_webber_A" not in movers     # stun and web, no movement
    # A real minority, or the warning would be noise on every preview.
    assert 0 < len(movers) < len(store.enemies) // 2


def test_a_look_ahead_block_is_labelled_provisional():
    from bridge.advisor import Advisor

    advisor = Advisor(model_path=None)
    for directory in (SNAP2_DIR, SNAP3_DIR):
        for raid, roster in _paired_snapshots(directory):
            st = read_live_state(raid_file=raid, roster_file=roster)
            hero, enemies_first = st.next_hero()
            if not st.in_battle or hero is None or not enemies_first:
                continue
            text = advisor.recommend(st)
            assert "provisional" in text
            assert "enemy turn" in text
            return
    pytest.skip("no look-ahead situation in the captures")


def test_every_look_ahead_is_eventually_superseded():
    """The preview is a stopgap for the save's silent stretch, so it must be
    replaced by a block computed from the real post-enemy board.

    The one legitimate exception: the enemies being looked past can END the
    fight, in which case the previewed hero never gets a turn at all. That
    can only happen in the last round of a combat, which is what this asserts
    rather than demanding every preview be refreshed."""
    seen_any = False
    for directory in (SNAP2_DIR, SNAP3_DIR):
        # Per RUN: a later raid must not be read as a continuation of an
        # earlier one, or its rounds bleed into the same combat key.
        for run in _capture_runs(directory):
            previews, finals = set(), set()
            last_round: dict[int, int] = {}
            for raid, roster in run:
                st = read_live_state(raid_file=raid, roster_file=roster)
                hero, enemies_first = st.next_hero()
                if not st.in_battle or hero is None:
                    continue
                combat, rnd = st.battles_completed, st.battle_round
                last_round[combat] = max(last_round.get(combat, 0), rnd)
                sig = (combat, rnd, hero.guid, hero.turns_taken)
                (previews if enemies_first else finals).add(sig)

            seen_any = seen_any or bool(previews)
            for sig in previews - finals:
                combat, rnd = sig[0], sig[1]
                assert rnd == last_round[combat], (
                    f"{directory.name}: {sig} never refreshed mid-combat")
    assert seen_any, "expected at least one look-ahead across the captures"


def test_seeding_refuses_a_board_it_cannot_represent(store):
    st = _state("00113_persist.raid.json")
    st.enemies[0].enemy_id = "some_modded_monster_A"
    with pytest.raises(SeedError):
        build_engine_from_live(st, store)


def test_out_of_battle_state_is_not_seedable(store):
    st = _state("00113_persist.raid.json")
    st.in_battle = False
    with pytest.raises(SeedError):
        build_engine_from_live(st, store)


# ── the policy sees a legal board ──

def test_seeded_state_yields_a_legal_masked_action(store):
    """End-to-end: real save -> engine -> the env's own observation and mask.
    Reuses ExpeditionSequenceEnv rather than rebuilding the observation, which
    is what guarantees the width and layout the checkpoint was trained on."""
    from bridge.advisor import Advisor

    advisor = Advisor(model_path=None)
    st = _state("00113_persist.raid.json")
    engine = build_engine_from_live(st, advisor.store)
    advisor._install(engine, st)

    obs = advisor.env._get_obs()
    assert obs.shape == advisor.env.observation_space.shape

    mask = advisor.env.action_masks()
    assert mask.any(), "a live hero turn must offer at least one legal action"
    # ACTION_ABANDON is legal WITHOUT being in `_action_map`: it is a decision
    # about the EXPEDITION, not an engine Action on this board, so it has no
    # entry there by construction. Every OTHER index must still correspond
    # exactly. Consequence for the bridge: a live position can now be advised
    # to abandon the quest, which run.py and the overlay need a meaning for.
    from env.gym_env import ACTION_ABANDON
    for idx in range(len(mask)):
        if idx == ACTION_ABANDON:
            continue
        assert bool(mask[idx]) == (idx in advisor.env._action_map)


def test_next_actor_comes_from_turn_counters_not_currentturn():
    """The bug live play caught: `currentturn` names the turn that has already
    resolved, so following it puts every recommendation one turn behind.

    Ground truth here is `actor.performing_turn`. This walks the capture in
    time order and asserts the derived next-actor sequence reproduces the real
    play order of that fight — Highwayman(2), Vestal(49), Highwayman(43),
    Man-at-Arms(67) — and, crucially, that each hero is named in a snapshot
    taken BEFORE their counter increments."""
    pairs = _paired_snapshots(SNAP3_DIR)
    assert pairs, "raidsnaps3 capture missing"

    sequence = []
    last = None
    for raid, roster in pairs:
        st = read_live_state(raid_file=raid, roster_file=roster)
        actor = st.current_actor
        if not st.in_battle or actor is None:
            continue
        # Segment on the raid's own completed-combat counter so a later fight
        # in the same run cannot bleed into this one's round 1.
        key = (st.battles_completed, st.battle_round, actor.side, actor.guid,
               actor.turns_taken)
        if key == last:
            continue
        last = key
        sequence.append((st.battles_completed, st.battle_round, actor.side, actor.guid))

    first_combat = min(c for c, _, _, _ in sequence)
    round_one_heroes = [g for (c, r, side, g) in sequence
                        if c == first_combat and r == 1 and side == "hero"]
    # The real play order of that fight's first round: all four party members
    # named exactly once, in queue order rather than formation order.
    assert round_one_heroes == [2, 246, 245, 67], round_one_heroes


def test_a_round_ending_on_a_stunned_combatant_holds_instead_of_repeating():
    """Second defect from live play: "when there are two monster attacks in a
    row the text does not update properly".

    In snapshot 00174 everyone has acted except one Fungal Bloat, which is
    stunned — its slot is consumed without it acting and the round just ends.
    The first fix excluded stunned combatants and then fell back to
    `currentturn`, which re-announced the Man-at-Arms whose turn had already
    passed. None is the honest answer; the advisor holds its last output."""
    st = _state_at(SNAP3_DIR, "00174")
    pending = [t for t in st.turn_order if t.turns_taken < st.battle_round]
    assert len(pending) == 1 and pending[0].stunned, "expected one stunned straggler"
    assert st.current_actor is None


def test_a_trailing_turn_counter_does_not_block_the_queue():
    """A combatant stunned through a round comes back permanently one behind,
    so "has it acted this round" cannot be read from its counter alone.

    The queue is consumed in order, so whoever has acted is a prefix of it —
    scanning from the last known actor is what stops that straggler being
    reported as "next" forever. Snapshot 00219 is the case: the Fungal Bloat
    sits at 0 turns while the whole party is at 2, and it genuinely IS next."""
    st = _state_at(SNAP3_DIR, "00219")
    actor = st.current_actor
    assert actor is not None and actor.side == "monster"
    assert actor.turns_taken == 0 and st.battle_round == 2
    # And it is the last entry, i.e. reached by scanning forward, not by
    # picking the globally-lowest counter.
    assert st.turn_order[-1].guid == actor.guid


def test_no_snapshot_ever_names_an_actor_that_already_played():
    """The invariant, swept across both self-contained captures."""
    checked = 0
    for directory in (SNAP2_DIR, SNAP3_DIR):
        for raid, roster in _paired_snapshots(directory):
            st = read_live_state(raid_file=raid, roster_file=roster)
            actor = st.current_actor
            if not st.in_battle or actor is None:
                continue
            assert actor.turns_taken < st.battle_round, f"{raid.name}: {actor}"
            assert not actor.stunned, f"{raid.name}: named a stunned combatant"
            checked += 1
    assert checked >= 20


def test_currentturn_alone_would_have_been_late():
    """Pins the defect itself, so nobody "simplifies" current_actor back to
    indexing `currentturn`. In this capture the save labelled `t1` already
    records the Vestal as having taken her turn."""
    pairs = _paired_snapshots()
    late = 0
    for raid, roster in pairs:
        st = read_live_state(raid_file=raid, roster_file=roster)
        if not st.in_battle or st.battle_turn is None:
            continue
        if not (0 <= st.battle_turn < len(st.turn_order)):
            continue
        named = st.turn_order[st.battle_turn]
        if named.turns_taken >= (st.battle_round or 1):
            late += 1
    assert late > 0, "expected the currentturn reading to be demonstrably late"


def test_seeded_engine_points_at_the_hero_who_has_not_acted(store):
    """The seeder has to inherit the same rule, or the engine hands the
    observation to the wrong hero even when the printed name is right."""
    pairs = _paired_snapshots()
    checked = 0
    for raid, roster in pairs:
        st = read_live_state(raid_file=raid, roster_file=roster)
        actor = st.current_actor
        if not st.in_battle or actor is None or actor.side != "hero":
            continue
        engine = build_engine_from_live(st, store)
        expected = [h.guid for h in st.heroes].index(actor.guid)
        assert engine.get_current_actor_id() == engine.heroes[expected].id, raid.name
        checked += 1
    assert checked >= 3, "expected several hero turns in the capture"


def test_real_initiative_values_fit_the_engines_spd_plus_d8(store):
    """The captures are a free oracle for a mechanic never checked against the
    game: they record the actual initiative every combatant rolled.

    The engine models initiative as SPD + d8 and this confirms the SPD term and
    the 8-wide spread on both sides — every observed value across two rounds
    lands within 8 of that combatant's speed. Hero speed comes from the WEAPON
    TIER (`weapon_tiers[t].spd_bonus`), not `base_stats.speed`, which is what
    the engine itself uses; checking against base_stats makes 5 of 8 hero
    values look impossible when they are fine.

    **The offset is off by one and deliberately not changed.** Real rolls land
    in [spd, spd+7]; `SeededRNG.roll(8)` returns 1-8, so this engine sits one
    point high — on EVERY combatant equally, so no ordering and therefore no
    behaviour can differ. Changing it would shift every existing seeded trace
    for zero effect. See CHANGELOG.md."""
    thresholds = load_resolve_thresholds(GAME_DIR)
    observed: dict[tuple, int] = {}
    for path in RAIDS:
        st = read_live_state(raid_file=path, roster_file=ROSTER, thresholds=thresholds)
        if not st.in_battle or not st.turn_order:
            continue
        by_hero = {h.guid: h for h in st.heroes}
        by_monster = {e.battle_guid: e.enemy_id for e in st.enemies}
        for entry in st.turn_order:
            if entry.side == "hero":
                hero = by_hero.get(entry.guid)
                if hero is None:
                    continue
                tier = max(0, hero.resolve_level - 1)
                hd = store.heroes[hero.hero_class]
                spd = hd.weapon_tiers[min(tier, len(hd.weapon_tiers) - 1)].spd_bonus
            else:
                enemy_id = by_monster.get(entry.guid)
                if enemy_id is None:
                    continue
                spd = store.enemies[enemy_id].stats.speed
            observed.setdefault((st.battle_round, entry.side, entry.guid),
                                (int(entry.initiative), spd))

    assert len(observed) >= 12, "expected initiative from both rounds and both sides"
    for (rnd, side, guid), (init, spd) in observed.items():
        assert spd <= init <= spd + 7, (
            f"round {rnd} {side} {guid}: initiative {init} outside [{spd}, {spd + 7}]")


def test_seeding_uses_the_real_weapon_and_armour_ranks(store):
    """`reset()` derives one gear tier from resolve level, which is a fine
    convention for a sampled party and wrong for a real roster: resolve and the
    two upgrade tracks are independent, and the save carries all three.

    The cost was not cosmetic. A resolve-2 Vestal with rank-2 armour has 34 max
    HP; the convention gave 29, and `_apply_carry_over` clamps
    `hp = min(carry.hp, max_hp)`, so her real 34 became 29/29 — a damaged hero
    could read as full. Two of the four "heals on a full-HP target" seen in
    live play were this, not a policy fault: the Highwayman was at 28/33."""
    st = _state_at(SNAP2_DIR, "20260811-220332_00167")
    vestal = next(h for h in st.heroes if h.hero_class == "vestal")
    assert (vestal.resolve_level, vestal.armour_rank) == (2, 2)

    engine = build_engine_from_live(st, store)
    sim = engine.heroes[st.heroes.index(vestal)]
    assert sim.max_hp == 34, "armour rank 2, not resolve-derived tier 1"
    assert sim.hp == int(vestal.hp), "real HP must survive the max-HP clamp"
    # Skill damage rows are chosen by weapon rank.
    assert sim.skill_tier == vestal.weapon_rank


def test_no_hero_is_seeded_meaningfully_off_their_real_hp(store):
    """Swept, because the clamp is silent: a ceiling that is too low simply
    truncates and a damaged hero starts reading as full.

    One point of slack, and no more. The real game keeps HP as a float
    (a Man-at-Arms with `health_stone` reads 38.75) while the engine's max HP
    is an integer, so the ceiling can land one below. Every shortfall measured
    across both captures is exactly 1 and always on a fractional value — a
    rounding artifact, not a missing modifier."""
    for directory in (SNAP2_DIR, SNAP3_DIR):
        for raid, roster in _paired_snapshots(directory):
            st = read_live_state(raid_file=raid, roster_file=roster)
            if not st.in_battle or st.current_actor is None:
                continue
            engine = build_engine_from_live(st, store)
            for live, sim in zip(st.heroes, engine.heroes):
                assert sim.hp >= int(round(live.hp)) - 1, (
                    f"{raid.name}: {live.hero_class} seeded at {sim.hp}/{sim.max_hp}, "
                    f"save says {live.hp}")
                assert sim.hp <= sim.max_hp


def test_trinkets_are_read_by_id_not_by_slot_number(store):
    """`trinkets.items` is keyed by SLOT ("0", "1") with the id inside, unlike
    quirks and skills which are keyed by id. Reading the keys yielded
    `['0', '1']`, which matches no trinket, so every trinket in every real
    party was equipped as a silent no-op."""
    st = _state_at(SNAP2_DIR, "20260811-220332_00575")
    maa = next(h for h in st.heroes if h.hero_class == "man_at_arms")
    assert maa.trinkets == ["tough_ring", "health_stone"]
    assert all(t in store.trinkets for t in maa.trinkets), "ids must resolve"

    # And the max-HP trinket has to reach the ceiling, or the hero's real HP
    # gets truncated on the way in.
    engine = build_engine_from_live(st, store)
    sim = engine.heroes[st.heroes.index(maa)]
    assert sim.max_hp >= int(round(maa.hp)) - 1


def test_seeded_heroes_carry_their_real_quirks(store):
    """Quirks are real equipment with real stat buffs, and a live roster has
    several per hero — dropping them would hand the policy the wrong stats."""
    st = _state("00113_persist.raid.json")
    assert st.heroes[0].quirks, "the captured party should have quirks"
    engine = build_engine_from_live(st, store)
    assert len(engine.heroes) == 4


def test_advisor_names_the_move_in_english(store):
    from bridge.advisor import Advisor

    advisor = Advisor(model_path=None)
    st = _state("00113_persist.raid.json")
    text = advisor.recommend(st)
    hero = st.hero_by_guid(st.current_actor.guid)
    assert advisor.store.heroes[hero.hero_class].name in text
    # Display names, not internal ids — several skills were renamed after
    # their id was set, so an id would be the wrong label on screen.
    assert "_" not in text.split("legal:")[-1].split(";")[0]
    # Enemy names come from the SAVE (what is on screen), not store ids.
    assert "Swine Chopper" in text


# ── temporary state: buffs, DoTs, guard, riposte ──

def test_dots_live_in_buff_group_and_actor_dot_is_always_empty():
    """The field named `actor_dot` is a decoy, and this pins that.

    It exists on every combatant record in all 445 captured snapshots and is
    empty in every one of them — including fights where a hero was visibly
    bleeding. The DoT is a `buff_group` entry (`stat_type` 5). Anyone who
    later 'fixes' the reader to look at `actor_dot` will get nothing, so the
    negative result is worth a test of its own."""
    from data.dson import Field, parse_file

    non_empty_dot = dot_buffs = 0
    for raid, roster in _paired_snapshots():
        for path in (raid, roster):
            root = parse_file(path)
            records = [root] + [rd.value for rd in root.find_all("raw_data")
                                if isinstance(rd.value, Field)]
            for rec in records:
                non_empty_dot += sum(1 for d in rec.find_all("actor_dot") if d.children)
                for group in rec.find_all("buff_group"):
                    for slot in group.children:
                        stat = slot.get("stat_type")
                        if stat is not None and stat.value in SAVE_DOT_STAT_TYPES:
                            dot_buffs += 1
    assert non_empty_dot == 0
    assert dot_buffs > 0, "no DoT captured at all — this test proves nothing"


def test_a_dot_amount_is_damage_per_tick_with_stacks_already_summed():
    """`amount` 2 means 2 HP a round, not two stacks of something.

    Measured, not assumed: this Highwayman carries one `stat_type 5` entry of
    amount 2 and loses exactly 2 HP as its duration goes 3 -> 2. Getting this
    backwards would halve or double every DoT the advisor is told about."""
    before = _state_at(SNAP2_DIR, "20260811-214142_00031")
    after = _state_at(SNAP2_DIR, "20260811-214142_00042")

    def dot_of(state):
        for hero in state.heroes:
            for buff in hero.buffs:
                if buff.stat_type == SAVE_STAT_BLEED:
                    return hero, buff
        return None, None

    hero_before, dot_before = dot_of(before)
    assert dot_before is not None
    hero_after = next(h for h in after.heroes if h.guid == hero_before.guid)
    dot_after = next(b for b in hero_after.buffs if b.stat_type == SAVE_STAT_BLEED)

    assert dot_before.duration - dot_after.duration == 1
    assert hero_before.hp - hero_after.hp == dot_before.amount


def test_seeded_board_carries_dots_guard_and_riposte(store):
    """All three transfer, across the captures as a whole. Guards are rare
    (one comp, a handful of turns), so this asserts on the totals rather than
    on any single snapshot."""
    dots = guards = ripostes = 0
    for raid, roster in _paired_snapshots():
        state = read_live_state(raid_file=raid, roster_file=roster)
        if not state.in_battle:
            continue
        engine = build_engine_from_live(state, store)
        for c in engine.heroes + engine.enemies:
            dots += len(c.bleeds) + len(c.blights)
            guards += c.guard_target_id is not None
            ripostes += c.active_riposte is not None
    assert dots and guards and ripostes


def test_guard_is_seeded_onto_the_guardian_not_the_guarded(store):
    """The marker entry sits on the GUARDED hero and names its guardian —
    the reverse of what it looks like, and getting it wrong pointed the
    protection the wrong way round.

    The game's data is the arbiter: `defender` fires `MAA Guard 1` at the ally
    and `Defender N` (+15% PROT, `.target "performer"`) at itself, so whoever
    carries the PROT buff is the guardian. Here that is the Man-at-Arms."""
    state = _state_at(SNAP2_DIR, "20260811-220332_00213")
    engine = build_engine_from_live(state, store)

    guardians = [c for c in engine.heroes if c.guard_target_id is not None]
    assert len(guardians) == 1
    guardian = guardians[0]
    guarded = engine._find_combatant(guardian.guard_target_id)
    assert guardian.definition_id == "man_at_arms"
    assert guarded.definition_id == "vestal"
    assert guarded.guard_source_id == guardian.id
    # And the guardian is the one holding the skill's own PROT buff.
    assert guardian.prot > engine.store.heroes["man_at_arms"].base_stats.prot


def test_riposte_transfers_without_debuffing_the_hero(store):
    """Riposte's -33% damage and +2% crit belong to the counter-attack, so
    they must NOT land on the hero's own attack stats.

    Seeding the same snapshot with the riposte group removed has to produce
    identical damage, crit and accuracy — if the group leaked into
    `_apply_buff_to_stats`, every riposting Highwayman would be a third
    weaker."""
    import copy

    state = _state_at(SNAP2_DIR, "20260811-220332_00213")
    engine = build_engine_from_live(state, store)
    riposting = [c for c in engine.heroes if c.active_riposte]
    assert riposting, "snapshot has no active riposte"

    stripped = copy.deepcopy(state)
    riposte_sources = {b.source for h in stripped.heroes for b in h.buffs
                       if b.stat_sub_type.startswith("riposte_")}
    for hero in stripped.heroes:
        hero.buffs = [b for b in hero.buffs if b.source not in riposte_sources]
    plain = build_engine_from_live(stripped, store)

    for got, expected in zip(engine.heroes, plain.heroes):
        assert (got.dmg_min, got.dmg_max) == (expected.dmg_min, expected.dmg_max)
        assert got.crit == pytest.approx(expected.crit)
        assert got.atk_bonus == pytest.approx(expected.atk_bonus)
    assert not any(c.active_riposte for c in plain.heroes)


def test_permanent_buffs_are_left_to_the_equipment_pass(store):
    """A `duration -1` entry is a trinket/quirk/town buff that
    `_sync_equipment` already applied. Re-applying it here would double it,
    and the captures are full of them (224 inline plus 240 town ones)."""
    permanent_ids = set()
    seen = 0
    for raid, roster in _paired_snapshots():
        state = read_live_state(raid_file=raid, roster_file=roster)
        if not state.in_battle:
            continue
        for hero in state.heroes:
            permanent_ids |= {b.buff_id for b in hero.buffs
                              if (b.duration or 0) <= 0 and b.buff_id}
        engine = build_engine_from_live(state, store)
        applied = {b.buff_id for c in engine.heroes + engine.enemies for b in c.buffs}
        assert not (applied & permanent_ids)
        seen += 1
    assert permanent_ids, "no permanent buff in the captures — test proves nothing"
    assert seen


def test_an_unrecognised_buff_is_counted_never_guessed_at(store):
    """Two `stat_type` values in the captures (1 and 4) were never identified:
    one demonstrably deals no HP and no stress damage, the other never had its
    duration tick inside a capture. Applying them under a guessed meaning
    would be worse than dropping them, so they are dropped and counted."""
    total_unknown = total_reported = 0
    for raid, roster in _paired_snapshots():
        state = read_live_state(raid_file=raid, roster_file=roster)
        if not state.in_battle:
            continue
        known = set(SAVE_STAT_TYPE) | set(SAVE_DOT_STAT_TYPES) | {SAVE_STAT_GUARD_MARKER}
        for live in list(state.heroes) + list(state.enemies):
            riposte_sources = {b.source for b in live.buffs
                               if b.stat_sub_type.startswith("riposte_")}
            total_unknown += sum(1 for b in live.buffs
                                 if (b.duration or 0) > 0
                                 and b.source not in riposte_sources
                                 and b.stat_type not in known)
        total_reported += build_engine_from_live(state, store).seed_unmapped_buffs
    assert total_unknown > 0
    assert total_reported == total_unknown


def test_a_large_monster_does_not_shift_the_names_behind_it(store):
    """A size-2 monster covers two ranks, so the save's enemy LIST INDEX stops
    being a rank number for everything behind it.

    Reported from live play: the advisor said "rank 3 (Fusilier)" while the
    Fusilier was standing at rank 4, because a Brigand Bloodletter was holding
    ranks 1-2. This is that exact board — Bloodletter / Cutthroat / Fusilier as
    list entries 0/1/2, occupying ranks 1-2 / 3 / 4 — and the old code labelled
    rank 3 with entry 2's name."""
    from bridge.advisor import Advisor

    state = _state_at(SNAP2_DIR, "20260812-175803_00042")
    assert [e.enemy_id for e in state.enemies] == [
        "brigand_blood_A", "brigand_cutthroat_A", "brigand_fusilier_A"]

    engine = build_engine_from_live(state, store)
    assert [e.occupied_positions for e in engine.enemies] == [[0, 1], [2], [3]]

    advisor = Advisor(model_path=None)
    advisor._install(engine, state)
    hero = engine.heroes[0]

    def label(rank: int) -> str:
        return advisor._target_label(engine, hero, Action(hero.id, "pistol_shot", rank))

    assert "Brigand Bloodletter" in label(0)
    assert "Brigand Bloodletter" in label(1)   # its back rank names it too
    assert "rank 3 (Brigand Cutthroat" in label(2)
    assert "rank 4 (Brigand Fusilier" in label(3)


def test_expedition_length_comes_from_the_map_not_a_default():
    """`battles_completed + remaining_battles` is the dungeon's real length,
    and it has to stay put as the party fights through it.

    An encounter tile carries a `mash_index` into the zone's mash table and an
    empty one carries -1; winning the fight spends the entry. Checked across
    every captured run: the sum holds at 6 / 6 / 7 while combats are completed.

    The long run is the exception and an informative one — it goes 4 to 5,
    which is a **camping ambush**: a real fight that was never on the map. So
    the number is a floor on the total, and the test allows it to grow but
    never to shrink."""
    for run in _capture_runs():
        totals = []
        for raid, roster in run:
            state = read_live_state(raid_file=raid, roster_file=roster)
            if state.remaining_battles is None:
                continue
            totals.append(state.battles_completed + state.remaining_battles)
        if not totals:
            continue
        assert min(totals) > 0
        assert totals == sorted(totals), "the expedition cannot get shorter"
        assert max(totals) - min(totals) <= 1, "only an ambush should add a fight"


def test_content_is_not_the_battle_marker():
    """Guards the negative result, because `content` is the field anyone would
    reach for first.

    It is overloaded and it moves as tiles are merely explored: in one captured
    raid a completed fight left the `content == 1` count untouched, while the
    `mash_index` count dropped by exactly one. Same shape of trap as
    `actor_dot` looking like the home of DoTs."""
    from data.dson import parse_file

    raid_dir = SNAP2_DIR
    first = _state_at(raid_dir, "20260812-175803_00019")
    later = _state_at(raid_dir, "20260812-175803_00246")
    assert later.battles_completed - first.battles_completed == 1
    assert first.remaining_battles - later.remaining_battles == 1

    def content_ones(stamp):
        areas = (parse_file(raid_dir / f"{stamp}_persist.map.json")
                 .get("map").get("static_dynamic").get("areas"))
        return sum(1 for area in areas.children
                   for tile in (area.get("tiles").children if area.get("tiles") else [])
                   if (tile.get("content").value if tile.get("content") else None) == 1)

    assert content_ones("20260812-175803_00019") == content_ones("20260812-175803_00246")


# --- per-battle skill limits, which the save does not record ---
#
# Reported from live play: "Tracking shot can be used once per battle only,
# it's trying to use it several times". Take Aim is `.per_battle_limit 1` and
# the engine has always enforced it -- but a freshly seeded engine starts every
# counter at zero, so the advisor kept recommending a button DD had greyed out.
# The click then lands on nothing, which in the log is indistinguishable from a
# dropped input: three attempts, 45 seconds, no save change.

SPENT_SNAP = SNAP_DIR / "00074_persist.raid.json"


def _pd_actions(skill_uses=None):
    state = read_live_state(raid_file=SPENT_SNAP, roster_file=ROSTER)
    engine = build_engine_from_live(state, get_store(), skill_uses=skill_uses)
    pd = next(h for h in engine.heroes if h.name == "Plague Doctor")
    return {a.skill_id for a in engine.get_valid_actions(pd.id)}


@pytest.mark.skipif(not SPENT_SNAP.exists(), reason="capture missing")
def test_a_limited_skill_is_legal_when_nothing_says_it_was_spent():
    """The control. `blinding_gas` is `.per_battle_limit 3` and the Plague
    Doctor in this capture has it equipped and usable."""
    assert "blinding_gas" in _pd_actions()


@pytest.mark.skipif(not SPENT_SNAP.exists(), reason="capture missing")
def test_a_spent_per_battle_skill_is_not_offered_again():
    """Guid 118 is the Plague Doctor's roster guid: the ledger is keyed on
    that, not on a rank or a class, because duplicate classes are real."""
    assert "blinding_gas" not in _pd_actions({118: {"blinding_gas": 3}})


@pytest.mark.skipif(not SPENT_SNAP.exists(), reason="capture missing")
def test_a_partly_spent_skill_is_still_offered():
    """3 uses, 1 spent. Off-by-one here would take a skill away for the rest of
    a fight it was legal in."""
    assert "blinding_gas" in _pd_actions({118: {"blinding_gas": 1}})


@pytest.mark.skipif(not SPENT_SNAP.exists(), reason="capture missing")
def test_the_ledger_lands_on_the_hero_it_names_and_no_other():
    """Somebody else's guid must not spend this hero's skill."""
    assert "blinding_gas" in _pd_actions({999: {"blinding_gas": 3}})


@pytest.mark.xdist_group("torch")
def test_a_wrong_width_checkpoint_REFUSES_instead_of_raising():
    """THE SHIPPED CHECKPOINT DOES NOT FIT THE CURRENT ENGINE, and a recording
    session must survive that rather than die on startup.

    The width is a property of the loaded DATA as well as of the code — the
    enemy-type one-hot is as wide as the store's monster-type set — so a pure
    loader fix reached it on 2026-09-02 with no change in `env/` at all.

    **`survival_episodes` is not optional in this test, and that is the whole
    point.** The advice path calls `predict`, which fails per turn; `Rollout`
    passes `env=` and trips SB3's `check_for_correct_spaces` at LOAD. Guarding
    only the first left `bat/combat_advise.bat` — which passes `--survival 12`
    — crashing on startup with a traceback instead of the note. One guard, two
    call sites, and the second is the one that actually bit.
    """
    from bridge.advisor import Advisor

    shipped = CSV_DIR / "models" / "dd_combat_ppo_full_expedition.zip"
    if not shipped.exists():
        pytest.skip("no shipped checkpoint to test the mismatch against")

    advisor = Advisor(model_path=str(shipped), survival_episodes=12)
    if not advisor.model_refused:
        # The retrain landed and the widths agree again. Then the guard has
        # nothing to do and both halves must be live — asserting that keeps
        # this test meaningful instead of silently vacuous.
        assert advisor.model is not None and advisor._rollout is not None
        return
    assert advisor.model is None, "a refused checkpoint must not stay loaded"
    assert advisor._rollout is None, "the retreat valuation shares the checkpoint"
    assert "retrain" in advisor.model_refused


@pytest.mark.xdist_group("torch")
def test_the_bridge_runs_inference_on_ONE_thread():
    """Reported live as "the game is also lagging much with the command", and
    it is not the 1.5 ms of inference -- it is PyTorch's OpenMP pool
    spin-waiting ~200 ms afterwards on every one of its threads. Measured with
    `time.process_time()` around one `recommend()` plus a 1 s sleep:

        10 threads: 1968.8 ms CPU for 1.01 s wall (195% of one core)
         1 thread :    0.0 ms CPU for 1.00 s wall

    Two core-seconds of spin per piece of advice, on the machine rendering the
    game. Single-threaded is also faster here (0.7 ms vs 1.5 ms)."""
    import torch
    from bridge.rollout import quiet_torch

    quiet_torch()
    assert torch.get_num_threads() == 1


def test_the_advisor_remembers_only_what_it_played_and_forgets_per_fight():
    """A use is recorded from the bridge's own CONFIRMED send. Nothing else can
    know: `skill_log` and `party_skill_log` are empty in all 585 captures and
    in a live save taken while a Highwayman had already spent Take Aim, and no
    hero record carries a use count. DD keeps it in memory only."""
    from bridge.advisor import Advisor

    adv = Advisor(model_path=None)
    adv.note_skill_used(118, "blinding_gas")
    adv.note_skill_used(118, "blinding_gas")
    adv.note_skill_used(7, "take_aim")
    assert adv._skill_uses == {118: {"blinding_gas": 2}, 7: {"take_aim": 1}}
    # A move is not a skill with a limit, and `__move__` is not a skill id at
    # all -- recording it would put junk in the engine's counter dict.
    adv.note_skill_used(118, "__move__")
    assert "__move__" not in adv._skill_uses[118]
    adv.forget_skill_uses()
    assert adv._skill_uses == {}


def test_the_advisor_log_actually_receives_a_recommendation(tmp_path):
    """`--log` has to record the RECOMMENDATION, not just the run's bookends.

    It shipped once doing exactly that: a botched edit left the recommendation
    on `print` while only "-- raid over --" went through the logging path, and
    `--help` looked fine, so it was not noticed until a full acceptance run came
    back with a one-line log and the raid file already deleted. What the save
    captures cannot reconstruct is what was ADVISED, so an empty log means the
    run produced no evidence at all.

    Drives the real `_watch` loop against a profile built from a capture rather
    than testing `emit` directly — the bug was in the wiring, not the writer."""
    from bridge.advisor import Advisor, _watch

    profile = tmp_path / "profile_0"
    profile.mkdir()
    for kind in ("raid", "roster", "map"):
        src = SNAP2_DIR / f"20260812-175803_00042_persist.{kind}.json"
        if src.exists():
            (profile / f"persist.{kind}.json").write_bytes(src.read_bytes())

    log = tmp_path / "advisor.log"
    _watch(Advisor(model_path=None), profile, interval=0.2, duration=1.2, log_path=log)

    text = log.read_text(encoding="utf-8")
    assert "Highwayman" in text, "the log never saw a hero turn"
    assert "Brigand" in text, "the log never saw the actual recommendation body"


def test_a_dots_kind_is_its_stat_type_not_its_damage_source(store):
    """`damageSourceType` says WHO applied the DoT, not whether it bleeds.

    Shipped the other way round once, and a run flown to test it killed the
    idea outright. The party's only DoT source was `opened_vein`, a
    `dotBleed 3 / duration 3`:

      * that bleed on an ENEMY reads `stat_type 5, damageSourceType 6`;
      * a Rabid Dog's bleed on the CRUSADER reads `stat_type 5,
        damageSourceType 5`, in a fight whose only other monster
        (fungal_bloat) applies no DoT at all.

    Two confirmed bleeds, one stat_type, two damageSourceTypes. Classifying on
    the latter filed the hero's own Open Vein bleed as a blight, which offers
    `antivenom` against a bleed and withholds `bandage`."""
    enemy_dots = hero_dots = 0
    for raid, roster in _paired_snapshots(SNAP4_DIR):
        state = read_live_state(raid_file=raid, roster_file=roster)
        if not state.in_battle:
            continue
        engine = build_engine_from_live(state, store)
        for combatant in engine.enemies:
            assert not combatant.blights, "Open Vein is a bleed, not a blight"
            enemy_dots += len(combatant.bleeds)
        for combatant in engine.heroes:
            assert not combatant.blights, "the only DoT that landed was the dog's bleed"
            hero_dots += len(combatant.bleeds)
        assert engine.seed_unmapped_buffs == 0
    assert enemy_dots, "no enemy-side DoT in the capture — test proves nothing"
    assert hero_dots


def test_a_corpse_resolves_even_when_its_hash_reads_negative(store):
    """`previous_monster_class` is a 32-bit hash and DSON decodes 4-byte fields
    as SIGNED, so every monster whose hash has the top bit set arrived here
    negative and matched nothing.

    It failed silently — the fallback placed *some* monster — so it cost no
    crash and produced a wrong board instead: **72 of 160 captured corpses,
    45%**, were seeded as whatever id the store happened to yield first.
    `cultist_witch_A`, `fungal_bloat_A` and `cultist_brawler_A` are the three
    that were being lost. A corpse's only mechanical contribution is the
    `size` that decides how many ranks it blocks, so the wrong monster is the
    wrong board."""
    resolved = fallback = 0
    smallest = min(store.enemies, key=lambda k: store.enemies[k].size)
    for directory in (SNAP2_DIR, SNAP3_DIR, SNAP4_DIR):
        for raid, roster in _paired_snapshots(directory):
            state = read_live_state(raid_file=raid, roster_file=roster)
            if not state.in_battle or not any(e.is_corpse for e in state.enemies):
                continue
            engine = build_engine_from_live(state, store)
            for sim in engine.enemies:
                if not sim.is_corpse:
                    continue
                if sim.definition_id == smallest:
                    fallback += 1
                else:
                    resolved += 1
    assert resolved > 100
    assert fallback == 0, f"{fallback} corpses still fell through to the fallback"


def test_the_turn_order_rule_holds_over_a_whole_raid():
    """Ground truth for `next_hero()`, checked forward rather than asserted.

    For each in-battle snapshot, whoever the bridge names must be the first
    hero whose `performing_turn` actually increments afterwards. Over the
    acceptance run that is 50 for 50. The one apparent miss is a combat that
    ENDED on the predicted turn — the last enemy died, `battles_completed` went
    1 to 2, and the next increment belongs to a different fight six minutes
    later — so a combat boundary ends the check rather than failing it."""
    snaps = []
    for raid, roster in _paired_snapshots(SNAP4_DIR):
        state = read_live_state(raid_file=raid, roster_file=roster)
        if state.in_battle:
            snaps.append(state)
    assert len(snaps) > 30, "not enough in-battle snapshots to be worth checking"

    right = wrong = 0
    for i, state in enumerate(snaps):
        hero, _ = state.next_hero()
        if hero is None:
            continue
        taken = {h.guid: h.turns_taken for h in state.heroes}
        combat = state.battles_completed
        for later in snaps[i + 1:]:
            if later.battles_completed != combat:
                break            # different fight, different queue
            moved = [h.guid for h in later.heroes
                     if h.turns_taken > taken.get(h.guid, 0)]
            if moved:
                right, wrong = (right + 1, wrong) if hero.guid in moved else (right, wrong + 1)
                break
    assert right > 30
    assert wrong == 0, f"{wrong} of {right + wrong} turn predictions were wrong"


# ── staging a save out from under the running game (2026-08-15) ──

def test_stage_retries_until_the_copy_is_a_whole_save():
    """The live crash, pinned. `_stage` used to retry only on `OSError` — the
    case where Windows refuses the read. It never checked the case where Windows
    GRANTS the read and hands back half a file, which is the likelier one: the
    watcher polls on `(mtime, size)`, so it is woken by the write it then races.

    Simulated by making the first two copies short, as a torn write is. Before
    the fix this returned the truncated bytes and `read_live_state` died with
    "not a DSON buffer (bad magic)" — killing the daemon poll thread, and with it
    the overlay, while the player kept playing."""
    import shutil as _shutil
    from bridge import live_state as ls

    src = next(SNAP_DIR.glob("*persist.raid.json"))
    whole = src.read_bytes()
    calls = {"n": 0}

    def torn_copy(a, b):
        calls["n"] += 1
        Path(b).write_bytes(whole if calls["n"] > 2 else whole[:len(whole) // 3])

    real = _shutil.copy2
    ls.shutil.copy2 = torn_copy
    try:
        staged = ls._stage([src])
    finally:
        ls.shutil.copy2 = real

    assert calls["n"] == 3, f"gave up after {calls['n']} attempt(s)"
    assert (staged / src.name).read_bytes() == whole
    read_live_state(raid_file=staged / src.name,
                    roster_file=SNAP_DIR / "paired_persist.roster.json")


def test_the_staging_directory_is_per_process():
    """Two readers collide with each other far more reliably than either collides
    with the game: they wake on the same mtime change and `copy2` into the same
    destination within milliseconds of each other. `overlay.py` embeds `run.py`'s
    loop, so running both — which the docs invited — was two watchers sharing one
    staging path, and that is what crashed a live run."""
    from bridge import live_state as ls

    src = next(SNAP_DIR.glob("*persist.raid.json"))
    staged = ls._stage([src])
    assert str(os.getpid()) in staged.name, staged


def test_stage_gives_up_rather_than_spinning_forever():
    """The other half: a file that is genuinely not a save must not hang the
    poll loop. `_stage` bounds its attempts and returns; the caller raises, and
    `run.py`'s watch thread now survives that."""
    import shutil as _shutil
    from bridge import live_state as ls

    src = next(SNAP_DIR.glob("*persist.raid.json"))
    calls = {"n": 0}

    def always_torn(a, b):
        calls["n"] += 1
        Path(b).write_bytes(src.read_bytes()[:100])

    real = _shutil.copy2
    ls.shutil.copy2 = always_torn
    try:
        ls._stage([src])
    finally:
        ls.shutil.copy2 = real
    assert calls["n"] == ls._STAGE_ATTEMPTS


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))


# ── every corpse tier is a corpse ──

def test_all_eight_corpse_classes_are_recognised():
    """The game ships EIGHT corpse classes, one small and one large per
    difficulty tier, and the check used to be `== "corpse_A"`.

    So only the Apprentice-tier small corpse was seen as a corpse. Every other
    one fell through as an ordinary monster, was not found in
    `store.enemies`, and `bridge/seed.py` refused the whole board --
    `cannot seed: unknown monster id(s): ['corpse_large_A']` -- after which
    the advisor is silent for the rest of the fight. At Veteran and Champion
    that is EVERY corpse.

    It hid because the Old Road is Apprentice with small monsters, so the only
    corpse the tutorial can produce is the one id that worked. The overnight
    loop found it on its second iteration, the first time something large
    died.
    """
    from bridge.live_state import CORPSE_CLASS_PREFIX          # noqa: PLC0415
    for tier in "ABCD":
        assert f"corpse_{tier}".startswith(CORPSE_CLASS_PREFIX)
        assert f"corpse_large_{tier}".startswith(CORPSE_CLASS_PREFIX)


def test_a_monster_merely_NAMED_like_a_corpse_is_not_one():
    """`corpsegrinder` is a real Warrens monster. The trailing underscore in
    the prefix is what keeps it a fighter -- without it, a live enemy would be
    read as a rank-blocking corpse and never targeted."""
    from bridge.live_state import CORPSE_CLASS_PREFIX          # noqa: PLC0415
    for real in ("corpsegrinder_A", "corpsegrinder_B", "cultist_brawler_A"):
        assert not real.startswith(CORPSE_CLASS_PREFIX), real


def test_the_store_agrees_on_which_ids_are_corpses(store):
    """The premise the prefix rests on, checked against the shipped data
    rather than assumed: every id in `store.corpses` matches the prefix, and
    NO loadable monster does. If the game ever ships a monster whose id starts
    `corpse_`, this fails here instead of silently making it untargetable.
    """
    from bridge.live_state import CORPSE_CLASS_PREFIX          # noqa: PLC0415
    assert store.corpses, "no corpses loaded at all"
    for cid in store.corpses:
        assert cid.startswith(CORPSE_CLASS_PREFIX), cid
    clashes = [k for k in store.enemies if k.startswith(CORPSE_CLASS_PREFIX)]
    assert not clashes, f"loadable monsters look like corpses: {clashes}"


# ── the stage coach is in the save ──

def _a_real_town_save():
    """Any profile that actually has a town. Skips when none does."""
    import pytest                                              # noqa: PLC0415
    from bridge.live_state import PROFILE_GLOB, SAVE_ROOT      # noqa: PLC0415
    for prof in sorted(Path(SAVE_ROOT).glob(PROFILE_GLOB)):
        town = prof / "persist.town.json"
        if town.exists():
            return town
    pytest.skip("no profile with a persist.town.json")


def test_a_missing_town_save_is_no_recruits_not_an_exception():
    """A fresh campaign has no town file at all, and that is the ORDINARY
    case for the overnight loop's first raid. A reader that throws on it is
    one every caller has to wrap."""
    from bridge.town_state import read_recruits                # noqa: PLC0415
    assert read_recruits(Path("nowhere") / "persist.town.json") == []


def test_the_coach_roster_reads_out_of_the_save():
    """**Who is on offer is a DATA question, not a screen-reading one.**

    `persist.town.json` -> `buildings.stage_coach.store.<list>.generated.<n>`
    carries a full hero record: class, resolve XP, weapon and armour rank,
    quirks and equipped skills. That is what makes recruiting decidable
    offline, with the screen needed only for the geometry of the drag.

    Structural assertions rather than exact contents: the coach reshuffles
    every week, so pinning names would fail on the next save.
    """
    from bridge.town_state import (read_recruits,              # noqa: PLC0415
                                   RECRUIT_LISTINGS)
    recruits = read_recruits(_a_real_town_save())
    if not recruits:
        import pytest                                          # noqa: PLC0415
        pytest.skip("the coach happens to be empty in this save")
    for r in recruits:
        assert r.hero_class, "a recruit with no class is a parse failure"
        assert r.listing in RECRUIT_LISTINGS, r.listing
        assert r.slot >= 0, "the slot is the save's own key and is the drag index"


def test_the_two_coach_LISTS_are_kept_apart():
    """`shard_hero_recruit` is Color of Madness's shard-bought roster. It is
    read because the panel draws it, and named separately because nothing
    should silently treat the two as one pool -- they cost different
    currencies."""
    from bridge.town_state import read_recruits                # noqa: PLC0415
    recruits = read_recruits(_a_real_town_save())
    if not recruits:
        import pytest                                          # noqa: PLC0415
        pytest.skip("the coach happens to be empty in this save")
    listings = [r.listing for r in recruits]
    # ordering is stable: the ordinary coach first, then the shard list
    assert listings == sorted(listings, key=lambda n: n != "hero_recruit")
