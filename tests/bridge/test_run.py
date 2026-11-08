"""Tests for the unified run loop (`bridge/run.py`), and mostly for the one
part of it that is a measuring instrument rather than a printer: marking what
was advised against what the player then did.

Everything else in `bridge/` answers a question. This module makes a *claim
about the past* — "you did not take that advice" — and a claim like that is
worthless if it leans. The failure mode to guard against is not a crash, it is
an instrument that quietly agrees with itself: resolve every ambiguous diff in
favour of whatever the advice predicted and the DIFFER rate collapses to zero
while the tool still looks like it is working.

So the tests come in two halves. The synthetic ones pin the attribution rules
one signal at a time. The ones over `archive/traces/raidsnaps2/` pin the rules
against 92 real consecutive pairs of a real fight, including the measured 5.4%
of turns the save simply cannot explain — that number is the instrument's own
blind spot, and it is asserted so a change that hides it fails here.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests._fixtures import get_store

import pytest

from bridge.live_state import read_live_state
from bridge.run import (
    DEFER, LENGTH_BANDS, VERDICTS, Board, Observed, RoomAdvice, RoomBoard,
    RoomPending, Session, _Pending, advised_kind, judge, judge_room, latch, observe,
    same_raid, ways_forward, who,
)

REPO = Path(__file__).resolve().parents[2]
from data.paths import GAME_DIR
SNAPS = REPO / "archive" / "traces" / "raidsnaps2"
SNAPS4 = REPO / "archive" / "traces" / "raidsnaps4"

# Concrete anchors, one per kind the diff can name, found by walking every
# consecutive in-battle pair in the capture set. Pinned by filename so a change
# to the attribution rules has to explain itself against a real turn rather than
# against a board somebody made up.
ATTACK_PAIR = ("00066", "00108", 49)
SUPPORT_PAIR = ("00121", "00128", 67)
HEAL_PAIR = ("20260811-220332_00167", "20260811-220332_00173", 246)
# The Highwayman advances a rank AND takes 6 hp off the corpse at slot 2 —
# Duelist's Advance. This used to be MOVE_PAIR and was attributed `move`, which
# was the corpse blind spot being pinned as if it were the answer: `Board.of`
# dropped corpses, so the attack half of the turn was invisible and the move was
# all that was left. It is the only corpse attack in 189 captured in-battle
# pairs, so it is the whole real-world evidence for that rule.
CORPSE_PAIR = ("20260811-220332_00521", "20260811-220332_00575", 245)
NOTHING_PAIR = ("20260811-220332_00575", "20260811-220332_00586", 246)
# raidsnaps2 contains NO move-only turn — every rank change in it comes with an
# attack. The two that exist are in raidsnaps4, so this anchor lives there.
MOVE_PAIR = ("20260812-211842_00453", "20260812-211842_00459", 245)

HERO, OTHER = 1, 2
FOE, FOE2 = 10, 11


def board(round_=1, hero_turns=None, hero_hp=None, hero_stress=None,
          hero_rank=None, hero_buffs=None, enemy_hp=None, provisions=None):
    return Board(
        battle_round=round_,
        hero_turns=hero_turns or {HERO: 0, OTHER: 0},
        hero_hp=hero_hp or {HERO: 20.0, OTHER: 20.0},
        hero_stress=hero_stress or {HERO: 10.0, OTHER: 10.0},
        hero_rank=hero_rank or {HERO: 0, OTHER: 1},
        hero_buffs=hero_buffs or {HERO: 0, OTHER: 0},
        hero_names={HERO: "crusader", OTHER: "vestal"},
        enemy_hp=enemy_hp if enemy_hp is not None else {FOE: 12.0, FOE2: 12.0},
        enemy_names={FOE: "Carrion Eater", FOE2: "Carrion Eater"},
        enemy_slot={FOE: 0, FOE2: 1},
        enemy_turns={FOE: 0, FOE2: 0},
        provisions=provisions if provisions is not None else {"bandage": 2},
    )


def acted(**over):
    """The board after HERO took a turn and nothing else is specified."""
    over.setdefault("hero_turns", {HERO: 1, OTHER: 0})
    return board(**over)


def read(prefix, snaps=None):
    snaps = snaps or SNAPS
    raid = snaps / f"{prefix}_persist.raid.json"
    return read_live_state(raid_file=raid,
                           roster_file=snaps / f"{prefix}_persist.roster.json",
                           map_file=snaps / f"{prefix}_persist.map.json")


# --- one signal at a time ---

def test_an_untaken_turn_resolves_to_nothing_yet():
    """The commonest state by far: the save moved for some other reason and the
    advised hero has not played. Returning a verdict here would score every
    recommendation against the turn before it."""
    assert observe(board(), board(), HERO) is None


def test_a_damaged_enemy_is_an_attack_on_that_enemy():
    obs = observe(board(), acted(enemy_hp={FOE: 5.0, FOE2: 12.0}), HERO)
    assert (obs.kind, obs.guid) == ("attack", FOE)


def test_a_vanished_enemy_is_a_kill():
    """A killed monster leaves the live enemy list for the corpse list, so its
    guid disappearing IS the kill. Without this every killing blow would read as
    "an enemy whose HP did not change"."""
    obs = observe(board(), acted(enemy_hp={FOE2: 12.0}), HERO)
    assert (obs.kind, obs.guid) == ("attack", FOE)
    assert "killed" in obs.why


def test_a_healed_hero_is_a_heal_on_that_hero():
    obs = observe(board(), acted(hero_hp={HERO: 20.0, OTHER: 26.0}), HERO)
    assert (obs.kind, obs.guid) == ("heal", OTHER)


def test_stress_coming_off_is_support():
    obs = observe(board(), acted(hero_stress={HERO: 10.0, OTHER: 3.0}), HERO)
    assert (obs.kind, obs.guid) == ("support", OTHER)


def test_a_new_buff_is_support():
    """This is what catches Defender being played instead of the advised
    attack — guard leaves no HP trace at all, only a buff."""
    obs = observe(board(), acted(hero_buffs={HERO: 0, OTHER: 1}), HERO)
    assert obs.kind == "support"


def test_a_spent_provision_is_a_provision():
    obs = observe(board(), acted(provisions={"bandage": 1}), HERO)
    assert (obs.kind, obs.label) == ("provision", "bandage")


def test_only_in_combat_provisions_count():
    """Firewood and torches are spent outside a turn; reading them as the turn
    would file a campfire as somebody's combat action."""
    obs = observe(board(provisions={"firewood": 1}),
                  acted(provisions={"firewood": 0}), HERO)
    assert obs.kind == "?"


# --- signals that arrive together ---

def test_a_lunge_is_one_turn_not_an_ambiguity():
    """Duelist's Advance moves the performer AND attacks. Treating the two
    signals as competing evidence marked 23 of 28 unresolved turns on the first
    replay of the capture set — every one of them this skill. Nothing but an
    attack takes HP off an enemy on a hero's turn, so that signal wins."""
    obs = observe(board(), acted(enemy_hp={FOE: 5.0, FOE2: 12.0},
                                 hero_rank={HERO: 1, OTHER: 0}), HERO)
    assert (obs.kind, obs.guid) == ("attack", FOE)


def test_the_signals_not_chosen_are_still_reported():
    """Ranking one signal over another must not hide the others — a reader has
    to be able to see what the ranking threw away."""
    obs = observe(board(), acted(enemy_hp={FOE: 5.0, FOE2: 12.0},
                                 hero_rank={HERO: 1, OTHER: 0}), HERO)
    assert "also move" in obs.why


def test_judgement_damages_and_heals_and_is_still_one_attack():
    obs = observe(board(), acted(enemy_hp={FOE: 5.0, FOE2: 12.0},
                                 hero_hp={HERO: 26.0, OTHER: 20.0}), HERO)
    assert obs.kind == "attack"


def test_an_item_and_a_skill_in_one_window_is_undetermined():
    """An item is not a skill, so unlike the lunge these cannot be one turn.
    Two real turns landed in one write and neither can be attributed."""
    obs = observe(board(), acted(provisions={"bandage": 1},
                                 enemy_hp={FOE: 5.0, FOE2: 12.0}), HERO)
    assert obs.kind == "?"
    assert "item and a skill" in obs.why


def test_two_heroes_between_writes_is_undetermined():
    obs = observe(board(), acted(hero_turns={HERO: 1, OTHER: 1},
                                 enemy_hp={FOE: 5.0, FOE2: 12.0}), HERO)
    assert obs.kind == "?"
    assert "2 heroes" in obs.why


def test_a_pass_that_looks_like_a_move_is_undetermined_not_a_disagreement():
    """Reported from live play: advised pass, the player passed, and it was
    marked DIFFER because an enemy shoved them afterwards.

    A pass leaves no trace at all, so a bare rank change is exactly as
    consistent with "passed, then got pulled" as with "chose to move" — 46 of
    246 monsters can reposition and the diff cannot tell a pull from a step.
    Claiming a disagreement here manufactures one, which is the failure mode
    this whole instrument has to avoid."""
    verdict, reason = judge("pass", frozenset(), Observed("move", HERO, "rank 2", guids=frozenset({HERO})), board())
    assert verdict == "?"
    assert "shove" in reason


def test_a_move_is_judged_against_a_rank_not_against_an_enemy_slot():
    """Seen live, and it marked every followed move as a disagreement.

    `__move__` is not a skill id, so `advised_target`'s skill lookup returned
    None and its fallback resolved the DESTINATION RANK against the enemy
    list: "MOVE to rank 1" came out as "advice pointed at Brigand Bloodletter
    (slot 1)", and the actor -- the only guid that visibly changed -- was
    never in the advised set at all. `tools/agreement.py` shares this marking,
    so it was wrong there too."""
    verdict, reason = judge("move", frozenset({HERO}),
                            Observed("move", HERO, "rank 1",
                                     guids=frozenset({HERO})),
                            board(), "rank 1")
    assert verdict == "MATCH", reason
    assert "as advised" in reason


def test_a_move_to_a_rank_nobody_advised_is_still_a_disagreement():
    """The fix must not make every move a MATCH: the advice names a
    destination, and going somewhere else is a different play."""
    verdict, reason = judge("move", frozenset({HERO}),
                            Observed("move", HERO, "rank 3",
                                     guids=frozenset({HERO})),
                            board(), "rank 1")
    assert verdict == "DIFFER"
    assert "rank 1" in reason and "rank 3" in reason


def test_a_pass_played_as_an_attack_is_still_a_disagreement():
    """The narrow rule must not swallow real disagreements."""
    assert judge("pass", frozenset(), Observed("attack", FOE, "x", guids=frozenset({FOE})), board())[0] == "DIFFER"


def test_a_killing_blow_does_not_invent_an_enemy_turn():
    """`enemies_acted` counts turn-counter advances only. Treating every enemy
    that VANISHED as having acted looks like a tighter guard and is not: the
    overwhelmingly common reason one vanishes is that the hero just killed it,
    so that version annotated every killing blow with a phantom enemy turn."""
    obs = observe(board(), acted(enemy_hp={FOE2: 12.0}), HERO)
    assert obs.kind == "attack"
    assert "enemy turn" not in obs.why


def test_a_miss_leaves_no_trace_and_is_never_a_match():
    """The instrument's real blind spot, and the reason the summary prints the
    '?' rate instead of folding it into agreement."""
    obs = observe(board(), acted(), HERO)
    assert obs.kind == "?"
    verdict, _ = judge("attack", frozenset({FOE}), obs, board())
    assert verdict == "?"


def test_a_push_is_not_read_as_a_move():
    """46 of 246 monsters reposition. A rank change while enemies were acting is
    not evidence the hero chose to move, so it is not counted as a signal."""
    before = board()
    after = acted(hero_rank={HERO: 1, OTHER: 0})
    after = Board(**{**after.__dict__, "enemy_turns": {FOE: 1, FOE2: 0}})
    obs = observe(before, after, HERO)
    assert obs.kind == "?"
    assert "enemy turn" in obs.why


def test_a_move_with_no_enemy_turn_is_a_move():
    obs = observe(board(), acted(hero_rank={HERO: 1, OTHER: 0}), HERO)
    assert obs.kind == "move"


# --- the verdict ---

def test_same_kind_same_target_is_a_match():
    assert judge("attack", frozenset({FOE}), Observed("attack", FOE, "x", guids=frozenset({FOE})), board())[0] == "MATCH"


def test_same_kind_other_target_is_a_disagreement():
    verdict, reason = judge("attack", frozenset({FOE}), Observed("attack", FOE2, "x", guids=frozenset({FOE2})), board())
    assert verdict == "DIFFER"
    assert "Carrion Eater (slot 1)" in reason      # names the ADVISED one


def test_a_different_kind_is_a_disagreement():
    verdict, reason = judge("attack", frozenset({FOE}), Observed("heal", OTHER, "x", guids=frozenset({OTHER})), board())
    assert verdict == "DIFFER"
    assert "advised attack, played heal" in reason


def test_heal_and_support_are_one_family():
    """A heal on a full-HP ally and a pure buff are the same diff, so claiming
    to tell them apart would be claiming more than the evidence carries. The
    MATCH here is about WHO was helped, not about which skill did it."""
    assert judge("heal", frozenset({OTHER}), Observed("support", OTHER, "x", guids=frozenset({OTHER})), board())[0] == "MATCH"
    assert judge("support", frozenset({OTHER}), Observed("heal", OTHER, "x", guids=frozenset({OTHER})), board())[0] == "MATCH"


def test_an_attack_is_not_in_the_help_family():
    assert judge("heal", frozenset({OTHER}), Observed("attack", FOE, "x", guids=frozenset({FOE})), board())[0] == "DIFFER"


def test_a_targetless_advice_cannot_disagree_on_target():
    assert judge("support", frozenset(), Observed("support", OTHER, "x", guids=frozenset({OTHER})), board())[0] == "MATCH"


# --- guard redirects the hit, and the marker has to know ---

def guarded_board(**over):
    """`board()` with FOE2 guarded by FOE — the Pelagic Guardian in front of the
    Pelagic Grouper, which is the real encounter this came from."""
    b = board(**over)
    return Board(**{**b.__dict__, "guarded_by": {FOE2: FOE}})


def test_a_hit_that_a_guardian_ate_still_matches():
    """Three of three DIFFERs in the second live run were this, all one
    encounter: the advice said rank 2 (Pelagic Grouper), the player clicked rank
    2, and `octotank_A guards fishman_harpoon_A` sent every hit into the Pelagic
    Guardian instead. The diff sees the wrong combatant lose HP.

    The bridge already PRINTED the guard relation on the same screen — it just
    was not judged with. Nothing about the click was wrong, so nothing about the
    verdict should be."""
    verdict, reason = judge("attack", frozenset({FOE2}),
                            Observed("attack", FOE, "x", guids=frozenset({FOE})),
                            guarded_board())
    assert verdict == "MATCH"
    assert "guarded" in reason, "a redirected hit must say so, not pass silently"


def test_a_redirect_does_not_inflate_the_footprint_count():
    """The guardian widens what counts as reached, never the footprint the count
    is reported against: a redirected single-target hit is 1 of 1, not 1 of 2."""
    _v, reason = judge("attack", frozenset({FOE2}),
                       Observed("attack", FOE, "x", guids=frozenset({FOE})),
                       guarded_board())
    assert "footprint" not in reason


def test_an_unguarded_wrong_target_is_still_a_disagreement():
    """The guard rule must not become "any enemy will do". Without the marker on
    the board, the same play is the disagreement it always was."""
    verdict, _reason = judge("attack", frozenset({FOE2}),
                             Observed("attack", FOE, "x", guids=frozenset({FOE})),
                             board())
    assert verdict == "DIFFER"


def test_the_guard_relation_is_read_off_the_marker_on_the_guarded_one():
    """Direction matters and is easy to get backwards. `stat_type` 27 sits on
    the GUARDED combatant and its `guarded_guid` names the guardian — the same
    reading `bridge/seed.py` uses, settled by the game's own `defender` data.
    Reversed, this protects the wrong combatant and matches nothing."""
    from bridge.run import _guard_map
    from bridge.seed import SAVE_STAT_GUARD_MARKER

    class _Buff:
        def __init__(self, stat_type, guarded_guid):
            self.stat_type, self.guarded_guid = stat_type, guarded_guid

    class _Foe:
        def __init__(self, guid, buffs):
            self.battle_guid, self.buffs = guid, buffs

    class _State:
        heroes = ()

        def __init__(self, enemies):
            self.enemies = enemies

    state = _State([_Foe(FOE, []),
                    _Foe(FOE2, [_Buff(SAVE_STAT_GUARD_MARKER, FOE)])])
    assert _guard_map(state) == {FOE2: FOE}


@pytest.mark.skipif(not SNAPS.exists(), reason="captures missing")
def test_the_guard_relation_is_found_in_the_real_captures():
    """The stub above pins the direction; this pins that the field is being read
    at all. 17 of `raidsnaps2`'s snapshots carry a live guard — the Man-at-Arms
    (279) standing in front of the Highwayman (245) and later the Vestal (246),
    the same capture `bridge/seed.py` recovered the marker's semantics from. A
    rename or a changed `stat_type` would leave the stub passing and this
    failing, which is the point.

    The union is over the whole fight, so both guarded heroes appear; a guardian
    protects one target at a time, which is why 279 is the value on both entries
    and never a key."""
    from bridge.run import _guard_map
    found = {}
    for raid in sorted(SNAPS.glob("*_persist.raid.json")):
        stem = raid.name[: -len("_persist.raid.json")]
        roster = SNAPS / f"{stem}_persist.roster.json"
        if not roster.exists():
            continue
        found.update(_guard_map(read_live_state(raid_file=raid, roster_file=roster)))
    assert found == {245: 279, 246: 279}, found


# --- an AoE is judged on its footprint, not on one nominal rank ---

def test_an_aoe_heal_matches_whoever_inside_it_was_healed():
    """The bug this closes, in the shape it was reported in.

    Divine Comfort is `.target @~1234` — it heals the whole party. The advice
    printed ONE nominal rank, `observe` kept ONE arbitrary member of the healed
    set, and `judge` compared the two, so the same play read MATCH or DIFFER
    depending on dict order. Three of five DIFFERs in the first live run were
    this, every one of them on a turn the player had followed exactly."""
    party = frozenset({HERO, OTHER})
    verdict, reason = judge("heal", party,
                            Observed("heal", OTHER, "x", guids=party), board())
    assert verdict == "MATCH"
    assert "2 of 2 in the advised footprint" in reason


def test_an_aoe_that_only_helped_one_still_matches_but_says_so():
    """Deliberately permissive, and deliberately NOT silent about it: a
    single-target heal aimed inside a party-wide footprint is indistinguishable
    from the AoE by the diff alone. Being stricter would need to know who COULD
    have been healed — who was damaged — and the board carries current HP, not
    max. So the shortfall is printed on the line instead of vanishing into the
    tally, which is the same rule `?` follows."""
    verdict, reason = judge("heal", frozenset({HERO, OTHER}),
                            Observed("heal", OTHER, "x", guids=frozenset({OTHER})),
                            board())
    assert verdict == "MATCH"
    assert "1 of 2 in the advised footprint" in reason


def test_help_outside_the_advised_footprint_is_still_a_disagreement():
    """The guard that stops the footprint rule from swallowing everything. A
    heal that reached someone the advised skill could not touch is a different
    play, however many of the advised ranks it also covered."""
    verdict, reason = judge("heal", frozenset({HERO}),
                            Observed("heal", HERO, "x", guids=frozenset({HERO, OTHER})),
                            board())
    assert verdict == "DIFFER"
    assert "outside what was advised" in reason


def test_a_single_target_footprint_prints_no_count():
    """The count is only meaningful for an AoE; on a one-target skill it would
    be noise on every line."""
    _v, reason = judge("attack", frozenset({FOE}),
                       Observed("attack", FOE, "x", why="(killed)",
                                guids=frozenset({FOE})), board())
    assert "footprint" not in reason


def test_duplicate_enemies_get_labels_that_tell_them_apart():
    """Three identical Carrion Eaters on identical HP is a real encounter in the
    capture set. "advised Carrion Eater, played Carrion Eater" reads as a tooling
    bug when it is a genuine disagreement about which one to hit."""
    b = board()
    assert who(b, FOE) != who(b, FOE2)
    assert "slot 1" in who(b, FOE) and "slot 2" in who(b, FOE2)


def test_a_hero_label_carries_the_rank():
    assert "rank 2" in who(board(), OTHER)


# --- the session ---

def test_provisional_advice_is_never_marked():
    """`recommend` looks ahead past pending enemy turns so the screen is not
    blank while they play, and labels that block provisional. Scoring it would
    score a prediction about a board that had not happened yet."""
    class _Stub:
        last_action, last_engine, last_provisional = object(), object(), True
        store = None
    session = Session(_Stub())
    session._arm(read(ATTACK_PAIR[0]), HERO)
    assert session._pending is None


def test_a_new_fight_drops_an_unresolved_recommendation():
    """Turn counters restart every combat, so a recommendation left pending from
    the last fight would either hang forever or be closed out against a
    stranger's turn. The round going backwards is the boundary."""
    class _Stub:
        last_action = last_engine = None
        last_provisional = False
        store = None
    session = Session(_Stub())
    # `_Pending`, not a bare tuple: the shape is built in `_arm` and read in
    # `_resolve`, and a hand-built tuple here broke on an unpack when a sixth
    # field was added. The named type gives `board` a default, so the next
    # field added with one leaves this line alone.
    session._pending = _Pending(HERO, "attack", FOE, "x", board(round_=4))
    state = read(ATTACK_PAIR[1])
    assert state.battle_round < 4
    session._resolve(state)
    assert session._pending is None
    assert sum(session.counts.values()) == 0


def test_every_verdict_has_a_counter():
    class _Stub:
        last_action = last_engine = None
        last_provisional = False
        store = None
    assert set(Session(_Stub()).counts) == set(VERDICTS)


def test_the_turn_pointer_never_names_a_hero_outside_the_party():
    """The check that cleared the turn pointer of a live report ("the decision it
    recommends is not the one that's due").

    If the queue could name a combatant the party does not contain, the advisor
    would seed with an actor it cannot place and advise for whoever the engine
    picked instead — which is exactly what that report would look like. Across
    every usable in-battle capture it never happens, which is what moved the
    investigation to the overlay's look-ahead labelling, where the bug was.
    """
    if not SNAPS.exists():
        pytest.skip("captures missing")
    usable = 0
    for raid in sorted(SNAPS.glob("*_persist.raid.json")):
        state = read(raid.name[:-len("_persist.raid.json")])
        if not state.in_battle or not state.heroes:
            continue          # an unpaired roster is a capture problem, not a bug
        usable += 1
        party = {h.guid for h in state.heroes}
        assert not [t.guid for t in state.turn_order
                    if t.side == "hero" and t.guid not in party], raid.name
        hero, _ = state.next_hero()
        assert hero is None or hero.guid in party, raid.name
    assert usable >= 90, f"only {usable} usable captures — did they move?"


def test_a_look_ahead_carries_what_a_front_end_needs_to_label_it():
    """`Update` must ship the enemy-turn count, not just the move.

    The overlay published only the move and dropped the terminal's "after 2
    enemy turns ->" lead, so a preview rendered as an instruction. Building the
    lead here means every front-end inherits it instead of each having to
    remember."""
    from bridge.run import Update
    assert Update(phase="battle").enemies_first == 0
    assert Update(phase="battle").movers == ()
    early = Update(phase="battle", provisional=True, enemies_first=2,
                   movers=("Brigand Bloodletter",))
    assert early.enemies_first == 2 and early.movers


def test_walking_a_corridor_does_not_reprint_the_whole_dungeon_block():
    """The torch burns down continuously while the party walks.

    Keying the out-of-combat block on the raw torch level reprinted all fifteen
    lines for every single point lost — six identical blocks in one corridor in
    the first live run, which buries the combat log it is interleaved with. None
    of the six layers reads the raw number; what changes their answer is
    crossing a darkness band."""
    from bridge.torchlight import describe as describe_torch
    from data.rules import RulesLoader
    rules = RulesLoader.load(GAME_DIR / "shared" / "rules.json")
    brackets = {describe_torch(rules, t) for t in (100, 99, 98, 97, 96, 95)}
    assert brackets == {"radiant"}, "a walk inside one band must be one key"
    assert describe_torch(rules, 71) != describe_torch(rules, 77), \
        "crossing a band must still reprint"


def test_length_bands_cover_every_quest_length():
    """Quest `length` is 1-4 (Short/Medium/Long/Exhausting) and the camp
    scheduler speaks in three bands. A missing key silently drops the campfire
    schedule for that band."""
    assert set(LENGTH_BANDS) == {1, 2, 3, 4}
    assert set(LENGTH_BANDS.values()) <= {"short", "medium", "long"}


# --- against the real captures ---

@pytest.mark.parametrize("before,after,guid,kind,snaps", [
    (*ATTACK_PAIR, "attack", SNAPS),
    (*SUPPORT_PAIR, "support", SNAPS),
    (*HEAL_PAIR, "heal", SNAPS),
    (*CORPSE_PAIR, "attack", SNAPS),
    (*NOTHING_PAIR, "?", SNAPS),
    (*MOVE_PAIR, "move", SNAPS4),
])
def test_a_real_turn_is_attributed(before, after, guid, kind, snaps):
    if not (snaps / f"{before}_persist.raid.json").exists():
        pytest.skip("captures missing")
    obs = observe(Board.of(read(before, snaps)), Board.of(read(after, snaps)), guid)
    assert obs is not None and obs.kind == kind


def test_breaking_a_corpse_is_an_attack_on_that_corpse():
    """The corpse half of `CORPSE_PAIR`, asserted as identity rather than kind.

    Corpses became legal targets on 2026-08-15 and `Board.of` still dropped
    them, so a followed recommendation to break one was marked by whatever
    unrelated signal was left over — twice in the first live run, both times
    reported as "advised attack, played support" against a player who had done
    exactly what he was told."""
    before_stem, after_stem, guid = CORPSE_PAIR
    if not (SNAPS / f"{before_stem}_persist.raid.json").exists():
        pytest.skip("captures missing")
    before, after = Board.of(read(before_stem)), Board.of(read(after_stem))
    obs = observe(before, after, guid)
    assert obs.kind == "attack"
    assert obs.guid in before.corpse_hp, "attributed to a living enemy"
    assert before.corpse_hp[obs.guid] == 7.0 and after.corpse_hp[obs.guid] == 1.0
    # The move is still seen and still reported — it is just no longer the
    # headline, because "attacked that corpse" is the stronger claim.
    assert "also move" in obs.why


def test_the_blind_spot_stays_small_across_the_whole_capture_set():
    """The regression guard for the whole attribution scheme.

    Every consecutive in-battle pair in the capture set, every hero whose turn
    counter advanced: 56 real turns, of which 3 leave nothing in the save. If a
    change to the rules pushes that back up — conflating move with attack put it
    at 50% — the instrument stops being able to answer the question it exists
    for, and it does so silently, because '?' is not an error.
    """
    if not SNAPS.exists():
        pytest.skip("captures missing")
    counts = {"?": 0, "known": 0}
    previous = None
    for raid in sorted(SNAPS.glob("*_persist.raid.json")):
        prefix = raid.name[:-len("_persist.raid.json")]
        state = read(prefix)
        if not state.in_battle:
            continue
        current = Board.of(state)
        if previous is not None and current.battle_round >= previous.battle_round:
            for guid, turns in current.hero_turns.items():
                if turns <= previous.hero_turns.get(guid, turns):
                    continue
                obs = observe(previous, current, guid)
                if obs is not None:
                    counts["?" if obs.kind == "?" else "known"] += 1
        previous = current
    total = counts["?"] + counts["known"]
    assert total >= 50, f"only {total} real turns found — did the captures move?"
    assert counts["?"] / total < 0.20, f"{counts['?']}/{total} turns unattributable"


# --- the out-of-combat block (2026-08-15, from the live report) ---

class _NoAdvisor:
    """Layers needs `store` (the camp layer) and `survival_note`; the combat half
    never runs here, so this keeps the test off the checkpoint while still
    rendering every section of the block — a length assertion over a block whose
    camp section silently failed to render would not be measuring anything."""

    def __init__(self, store):
        self.store = store

    def survival_note(self, state):
        return "3 combats left: P(finish intact) 60% +-11, expected deaths 0.7"


@pytest.fixture(scope="module")
def store():
    return get_store()


def _dungeon_blocks(store, limit=None, dark_only=False):
    """Render `Layers.block` over the real out-of-combat captures."""
    from bridge.route import read_quest
    from bridge.run import Layers
    from data.rules import RulesLoader

    rules = RulesLoader.load(GAME_DIR / "shared" / "rules.json")
    out = []
    for map_file in sorted(SNAPS4.glob("*_persist.map.json")):
        stem = map_file.name[: -len("_persist.map.json")]
        raid = SNAPS4 / f"{stem}_persist.raid.json"
        roster = SNAPS4 / f"{stem}_persist.roster.json"
        if not (raid.exists() and roster.exists()):
            continue
        state = read_live_state(raid_file=raid, roster_file=roster, map_file=map_file)
        if state.in_battle or (dark_only and state.torchlight >= 75):
            continue
        layers = Layers(_NoAdvisor(store), rules)
        layers.quest = read_quest(raid)
        block, headline, _advice = layers.block(state, raid, map_file)
        # The camp section is suppressed when there is no firewood — that is not
        # a decision, it is a fact about the quest, and on a short one it never
        # changes. Wherever a campfire IS available it must render, which is what
        # keeps a length assertion from passing over a block whose camp layer
        # silently crashed.
        if int((state.provisions or {}).get("firewood", 0) or 0) > 0:
            assert "CAMP" in block, "the camp layer did not render; the block is not whole"
        out.append((state, block, headline))
        if limit and len(out) >= limit:
            break
    return out


@pytest.mark.skipif(not SNAPS4.exists(), reason="captures missing")
def test_out_of_combat_the_torch_layer_is_not_asked_the_in_battle_question(store):
    """`about_to_win` is "is the killing blow the next thing that happens", and
    the dungeon block passed a hard `True` — so between rooms the layer answered
    the in-battle question and told the player to douse the torch for a fight
    that was not happening.

    Not merely noisy: on the same dark board the two answers are opposites,
    `shutdown` against `relight`. The block was printing the reverse of its own
    layer's advice, in every corridor, for the whole run."""
    blocks = _dungeon_blocks(store, dark_only=True)
    assert blocks, "no dark out-of-combat capture to test against"
    for state, block, _headline in blocks:
        assert "SHUT DOWN" not in block, (
            f"torch {state.torchlight}: advised a shutdown out of combat\n{block}")
    assert any("RELIGHT" in b for _s, b, _h in blocks), (
        "no capture advised a relight — the layer is not being reached at all")


@pytest.mark.skipif(not SNAPS4.exists(), reason="captures missing")
def test_holding_the_light_prints_nothing(store):
    """Holding is the default and the usual answer, so printing it costs two
    lines per room to say "carry on"."""
    for state, block, _headline in _dungeon_blocks(store, limit=6):
        if state.torchlight >= 75:
            assert "TORCH" not in block, block


@pytest.mark.skipif(not SNAPS4.exists(), reason="captures missing")
def test_the_block_leads_with_the_immediate_next_area(store):
    """"No se entiende qué pasillo te recomienda" — the answer was in the middle
    of a 21-area path printed as one line of 4-character ids. It gets its own
    line, first, with what is waiting in it."""
    for _state, block, headline in _dungeon_blocks(store, limit=6):
        lines = block.splitlines()
        assert any(ln.startswith("NEXT -> ") for ln in lines), block
        nxt = next(ln for ln in lines if ln.startswith("NEXT -> "))
        assert lines.index(nxt) == 1, "the next step is not the first thing said"
        assert "(" in nxt, "the next area is named but not described"
        assert headline.startswith("go to ") or headline in ("RELIGHT", "CAMP HERE",
                                                             "RETREAT") \
            or nxt.split()[2] not in headline


@pytest.mark.skipif(not SNAPS4.exists(), reason="captures missing")
def test_the_block_is_short_enough_to_read_at_a_glance(store):
    """It reached fifteen lines, most of it the same eleven dungeon-wide curios
    reprinted every time the party moved a room. The cap is what makes it a
    glance rather than a document; if a layer needs more room, it has to earn it
    by cutting something else."""
    for _state, block, _headline in _dungeon_blocks(store, limit=12):
        lines = block.splitlines()
        assert len(lines) <= 10, f"{len(lines)} lines\n{block}"
        assert max(len(ln) for ln in lines) <= 160, block


@pytest.mark.skipif(not SNAPS4.exists(), reason="captures missing")
def test_curios_ahead_are_on_the_planned_route_and_bounded(store):
    """The ahead-list is inventory planning, so it is worth keeping — but for
    the route actually chosen, not for every tile left in the dungeon."""
    for _state, block, _headline in _dungeon_blocks(store, limit=8):
        for line in block.splitlines():
            if "on the way:" not in line:
                continue
            listed = line.split("on the way:")[1]
            assert listed.count(",") <= 2, line
            # The reasoning is kept for the curio underfoot and dropped for the
            # ones ahead: three rooms away the only question is what to carry.
            assert " - " not in listed, line


# --- the five dungeon layers, which had never been measured at all ---
#
# `Session.room` printed advice and armed nothing, and `replay` did not even
# dispatch to it, so route/torch/curio/camp shipped with no agreement rate while
# combat had one. These pin the rules that keep the new number from flattering
# itself — every one of them was written because the first replay got it wrong.


def rb(area="rooA", torch=50.0, provisions=None, battles=0, curio_left=-1,
       party=(1, 2)):
    return RoomBoard(area=area, torch=torch,
                     provisions=dict(provisions if provisions is not None
                                     else {"torch": 3, "firewood": 1}),
                     battles=battles, curio_left=curio_left,
                     party=frozenset(party))


def pending(advice, before=None, ways=2, **latches):
    p = RoomPending(advice=advice, board=before or rb(), ways=ways)
    for k, v in latches.items():
        setattr(p, k, v)
    return p


# `rules.json`'s own boundary between pitch black and the darkest lit band; a
# snuff has to cross it in one step, decay never does. Asserted against the real
# file below rather than trusted as a number written here.
DARK = 25.0


def only(verdicts, layer):
    return [(v, r) for ell, v, r in verdicts if ell == layer]


def test_the_shutdown_is_only_offered_on_the_turn_that_ends_the_fight():
    """`about_to_win` is the torch layer's whole safety argument — darkness hands
    the monsters +12.5 ACC, +25% damage and +5 crit — and it is why the shutdown
    had never once been recommended in the live loop. `Layers.block` asks it
    `state.in_battle`, and `Session.room` runs ONLY out of battle, so it was
    always False and the play was unreachable. Zero samples was the loop never
    asking, not the play being rare."""
    from bridge.run import about_to_win

    class _E:
        def __init__(self, hp, alive=True):
            self.hp, self._alive, self.is_corpse = hp, alive, False
            self.bleeds = self.blights = []
            # `_is_doomed` sums burn too since 2026-08-20. A stub standing in
            # for a CombatantState has to carry every field it reads.
            self.burn_stacks, self.burn_decay_bonus = 0, 0.0

        def is_alive(self):
            return self._alive

    class _H:
        # `dmg_max` too: the gate is the AVERAGE roll now, not the floor.
        id, dmg_min, dmg_max = 0, 8, 12

    class _Eng:
        def __init__(self, foes):
            self.enemies, self.heroes = foes, [_H()]

    class _A:
        hero_id = 0

    assert about_to_win(_Eng([_E(5)]), _A()), "a secured last kill is the moment"
    assert not about_to_win(_Eng([_E(30)]), _A()), "not secured, so it is a bet"
    assert not about_to_win(_Eng([_E(5), _E(5)]), _A()), \
        "two enemies left is not the killing blow whatever their HP"
    assert not about_to_win(_Eng([]), _A())

    # **THE LINE IS THE AVERAGE ROLL, NOT THE FLOOR.** Fran, 2026-09-06:
    # *"it's very likely to kill it's good enough to go dark on"*, and *"we
    # should assume that snuff it's used on advantaged positions that expect
    # last turn anyway"*. At 8-12 damage the midpoint is 10, so a foe on 10
    # qualifies and one on 11 does not.
    #
    # The old rule was `hp <= dmg_min`, and it is why the snuff never fired:
    # live on the Old Road the last blow was 4 hp against a floor of 3, in a
    # fight the party won on that very swing.
    assert about_to_win(_Eng([_E(10)]), _A()),         "the average roll ends it, which is what 'expects last turn' means"
    assert about_to_win(_Eng([_E(9)]), _A())
    assert not about_to_win(_Eng([_E(11)]), _A()),         "above the average roll it is a bet again, not an expectation"


def test_the_snuff_boundary_is_read_off_the_game_and_not_written_down():
    """`DARK` above is 25 because `rules.json` says so, and this is what keeps
    that true. `darkness.range_table` ships pitch black as `(-inf, 0]` and the
    darkest lit band as `(0, 25]`, so 25 is the boundary a snuff crosses in one
    step and decay never does. If the table ever moves, the instrument has to
    move with it rather than keep a number somebody once copied."""
    from data.rules import RulesLoader
    from bridge.run import dark_ceiling
    rules = RulesLoader.load(GAME_DIR / "shared" / "rules.json")
    assert dark_ceiling(rules) == DARK
    assert dark_ceiling(object()) == 0.0, "an unreadable table must not invent one"


def test_a_hold_is_never_a_recommendation_and_is_never_armed():
    """The rule the whole number rests on. "Carry on" costs nothing to be right
    about, and scoring it against a save where nothing changed would manufacture
    agreement for free — exactly the way fleeing every fight scores 100% on
    `old-succ`. Torch `hold`, curio `skip` and curio `optional` are all holds."""
    assert RoomAdvice().empty
    assert judge_room(pending(RoomAdvice()), rb(area="rooB")) == []


def test_a_relight_that_then_burned_back_down_is_still_a_relight():
    """The trap the latches exist for. The torch decays continuously while the
    party walks, so comparing the arm-time level against the level when they
    leave the area reads a followed relight as "never relit". The rise is caught
    when it happens, and no threshold is needed for it: decay only ever lowers
    the torch, so ANY rise is a relight."""
    p = pending(RoomAdvice(torch="relight"), before=rb(torch=20.0))
    latch(p, rb(torch=20.0), rb(torch=70.0), DARK)   # relit
    latch(p, rb(torch=70.0), rb(torch=61.0), DARK)   # then walked with
    assert p.torch_up
    assert only(judge_room(p, rb(area="rooB", torch=61.0)), "torch") == \
        [("MATCH", "the torch went up")]


def test_a_torch_left_to_burn_out_is_not_a_shutdown():
    """A snuff writes 0 in one step; decay reaches 0 too, but gradually. Testing
    the endpoint would score any party that let the torch die on a long walk as
    having followed a shutdown it ignored, which is the single most flattering
    mistake this layer could make — advising darkness is the rarest call it
    gives and the one that most needs an honest number."""
    p = pending(RoomAdvice(torch="shutdown"), before=rb(torch=40.0))
    for a, b in ((40.0, 30.0), (30.0, 18.0), (18.0, 7.0), (7.0, 0.0)):
        latch(p, rb(torch=a), rb(torch=b), DARK)
    assert not p.torch_out
    assert only(judge_room(p, rb(area="rooB", torch=0.0)), "torch")[0][0] == "DIFFER"


def test_snuffing_the_torch_in_one_step_is_a_shutdown():
    p = pending(RoomAdvice(torch="shutdown"), before=rb(torch=64.0))
    latch(p, rb(torch=64.0), rb(torch=0.0), DARK)
    assert p.torch_out
    assert only(judge_room(p, rb(area="rooB", torch=0.0)), "torch")[0][0] == "MATCH"


def test_a_route_step_with_one_way_forward_is_not_scored():
    """`ways_forward` and why it had to exist: the first replay of the real
    captures came out 18 for 18 on route, which said nothing about the layer and
    everything about the shape of a dungeon. From inside a corridor there is the
    far room and the way back; that is not a decision. 18 of the 20 steps in
    `raidsnaps4` are exactly that."""
    a = RoomAdvice(next_step="coAB")
    assert only(judge_room(pending(a, ways=1), rb(area="coAB")), "route") == []
    assert only(judge_room(pending(a, ways=0), rb(area="coXY")), "route") == []
    scored = only(judge_room(pending(a, ways=2), rb(area="coAB")), "route")
    assert scored and scored[0][0] == "MATCH"


def test_walking_somewhere_else_is_a_route_disagreement():
    v = only(judge_room(pending(RoomAdvice(next_step="rooG")), rb(area="rooB")),
             "route")
    assert v[0][0] == "DIFFER" and "rooG" in v[0][1] and "rooB" in v[0][1]


def test_ways_forward_counts_a_corridor_as_forced_and_a_junction_as_a_choice():
    """Adjacency off `DungeonMap.graph()`, never off the `coAB` name convention,
    so the two readings of an id cannot drift apart."""
    class _Map:
        @staticmethod
        def graph():
            return {"rooA": {"rooB": "coAB"},
                    "rooB": {"rooA": "coAB", "rooC": "coBC", "rooD": "coBD"},
                    "rooC": {"rooB": "coBC"}, "rooD": {"rooB": "coBD"}}
    dm = _Map()
    assert ways_forward(dm, "coAB", "rooA") == 1        # a corridor is forced
    assert ways_forward(dm, "rooB", "coAB") == 2        # a junction is a choice
    assert ways_forward(dm, "rooA", "coAB") == 0        # a dead end
    assert ways_forward(None, "rooB", "") == 0


def test_a_curio_walked_past_is_held_open_not_called_a_disagreement():
    """Four of the five curio DIFFERs in the first replay were this, and none of
    them was a disagreement: the party walked `coEF(sack) > rooE > coEI(crate) >
    rooI` into a dead end and used both curios on the way back out. Scoring the
    first pass read the layer at 72% when the advice had in fact been taken."""
    p = pending(RoomAdvice(curio_area="coEF", curio_id="sack",
                           curio_action="interact"),
                before=rb(area="coEF", curio_left=1))
    v = only(judge_room(p, rb(area="rooE", curio_left=1)), "curio")
    assert v[0][0] == DEFER, "a deferral was scored as a disagreement"
    assert DEFER not in VERDICTS, "DEFER must not be scoreable by accident"


def test_a_deferred_curio_that_is_never_touched_settles_as_a_disagreement():
    """Held open is not forgiven. `summary()` is where "did they ever come back"
    becomes answerable, so that is where it settles."""
    class _Stub:
        last_action = last_engine = None
        last_provisional = False
        store = None
    session = Session(_Stub())
    session._deferred[("coEF", "sack")] = "walked past it"
    session.summary()
    assert session.by_layer["curio"]["DIFFER"] == 1
    assert not session._deferred


def test_using_the_curio_with_another_item_is_a_disagreement_that_names_it():
    p = pending(RoomAdvice(curio_area="rooA", curio_id="old_tree",
                           curio_action="use", curio_item="antivenom"),
                before=rb(curio_left=1), spent={"holy_water": 1, "shovel": 1})
    v = only(judge_room(p, rb(area="rooB", curio_left=0)), "curio")
    assert v[0][0] == "DIFFER"
    assert "antivenom" in v[0][1] and "holy_water" in v[0][1]


def test_using_the_curio_with_the_advised_item_is_a_match():
    p = pending(RoomAdvice(curio_area="rooA", curio_id="heirloom_chest",
                           curio_action="use", curio_item="skeleton_key"),
                before=rb(curio_left=1), spent={"skeleton_key": 1})
    assert only(judge_room(p, rb(area="rooB", curio_left=0)), "curio")[0][0] == "MATCH"


def test_an_unreadable_map_is_a_blind_spot_not_a_verdict():
    p = pending(RoomAdvice(curio_area="rooA", curio_id="sack",
                           curio_action="interact"), before=rb(curio_left=-1))
    assert only(judge_room(p, rb(area="rooB", curio_left=-1)), "curio")[0][0] == "?"


def test_camping_is_marked_by_the_firewood_and_not_by_anything_else():
    p = pending(RoomAdvice(camp=True))
    latch(p, rb(provisions={"firewood": 1}), rb(provisions={"firewood": 0}), DARK)
    assert p.camped
    assert only(judge_room(p, rb(area="rooB")), "camp")[0][0] == "MATCH"
    assert only(judge_room(pending(RoomAdvice(camp=True)), rb(area="rooB")),
                "camp")[0][0] == "DIFFER"


def test_a_new_raid_drops_the_claim_instead_of_billing_it():
    """Between two captures in one replay directory the party walks home, and
    the whole previous inventory reads as spent in a single poll — which
    produced `advised skeleton_key -> lost_luggage, spent 19, 8, 9, antivenom,
    bandage, bust, citrine, crest, deed, ...`, a disagreement invented entirely
    by the raid ending. The combat marker has had the equivalent guard (the
    round going backwards) since it was written."""
    assert same_raid(rb(battles=2, party=(1, 2)), rb(battles=3, party=(1, 2)))
    assert same_raid(rb(battles=2, party=(1, 2)), rb(battles=2, party=(1,))), \
        "a hero dying is not a new raid"
    assert not same_raid(rb(battles=3, party=(1, 2)), rb(battles=0, party=(1, 2)))
    assert not same_raid(rb(battles=2, party=(1, 2)), rb(battles=2, party=(7, 8)))


def test_the_verdicts_are_kept_per_layer_and_never_pooled():
    """A route layer at 30% must not be able to hide inside combat at 84%."""
    class _Stub:
        last_action = last_engine = None
        last_provisional = False
        store = None
    session = Session(_Stub())
    session._score("combat", "MATCH")
    session._score("route", "DIFFER")
    assert session.by_layer["combat"] == {"MATCH": 1, "DIFFER": 0, "?": 0}
    assert session.by_layer["route"] == {"MATCH": 0, "DIFFER": 1, "?": 0}
    assert session.counts["MATCH"] == 1 and session.counts["DIFFER"] == 1
    assert "combat" in session.summary() and "route" in session.summary()


@pytest.mark.skipif(not SNAPS4.exists(), reason="captures missing")
def test_the_block_reports_what_it_advised_as_structure(store):
    """The overlay's rule, applied to the marker: never read the printed block
    back. A claim recovered by a regex over `NEXT -> coAB  (corridor, 1 fight)`
    breaks the first time a label changes, and silently — by scoring the wrong
    thing."""
    seen = {"route": 0, "curio": 0}
    for state, block, _headline in _dungeon_blocks(store, limit=25):
        pass
    from bridge.route import read_quest
    from bridge.run import Layers
    from data.rules import RulesLoader
    rules = RulesLoader.load(GAME_DIR / "shared" / "rules.json")
    for map_file in sorted(SNAPS4.glob("*_persist.map.json"))[:25]:
        stem = map_file.name[: -len("_persist.map.json")]
        raid = SNAPS4 / f"{stem}_persist.raid.json"
        roster = SNAPS4 / f"{stem}_persist.roster.json"
        if not (raid.exists() and roster.exists()):
            continue
        state = read_live_state(raid_file=raid, roster_file=roster, map_file=map_file)
        if state.in_battle:
            continue
        layers = Layers(_NoAdvisor(store), rules)
        layers.quest = read_quest(raid)
        block, _headline, advice = layers.block(state, raid, map_file)
        if advice.next_step:
            seen["route"] += 1
            assert f"NEXT -> {advice.next_step}" in block, \
                "the structure and the text disagree about where to walk"
        if advice.curio_action:
            seen["curio"] += 1
            assert advice.curio_id in block
            # Only a decision gets armed; "your call" and "skip" are holds.
            assert advice.curio_action in ("use", "interact")
    assert seen["route"], "no route advice in 25 captures — did the block break?"


@pytest.mark.skipif(not SNAPS4.exists(), reason="captures missing")
def test_the_real_captures_produce_dungeon_verdicts_offline(store):
    """The whole point of the phase: route, torch and curio get marked against
    captures that were already on disk, with nobody playing. `replay` used to
    dispatch only to `battle`, which is why five layers had no number.

    Pinned loosely — the counts move with the checkpoint — but the layers have
    to be *present*, which is what silently stopped being true before. The combat
    half is deliberately stubbed out: this asserts the dungeon dispatch, and
    building a real `Advisor` here would put a checkpoint load in its way.
    """
    from bridge.run import replay
    from bridge.seed import SeedError

    class _RoomOnly(_NoAdvisor):
        def load_quest(self, _raid_file):
            return None

        def recommend(self, _state, _skip=0):
            raise SeedError("combat is stubbed out in this test")

    session = Session(_RoomOnly(store))
    replay(session, SNAPS4)
    session.summary()
    for layer in ("route", "torch", "curio"):
        assert sum(session.by_layer[layer].values()) > 0, \
            f"the {layer} layer produced no verdict at all"
    assert session.by_layer["combat"] == {v: 0 for v in VERDICTS}
    assert session.forced_route >= 5, \
        "no forced route steps found — ways_forward is not being applied"


def test_act_alone_plays_the_fight_it_does_not_ask_about_it():
    """The gate was inconsistent, and the inconsistency was the bug.

    `confirm` was written when combat was the ONLY thing the actuator did, and
    it is still the only thing it gates -- the five out-of-combat layers walk
    the party, press `W` into curios, SHIFT-CLICK discards and click the map
    without ever consulting it. So `--act` alone meant "do all of that
    unattended, then stop dead at the first fight to ask about the single most
    reversible action in the game", which in live play read as the bridge
    simply not playing: "it got until the combat but didnt really play, i
    needed to click the attacks myself".
    """
    from bridge.run import build_parser
    args = build_parser().parse_args(["--act"])
    assert args.act and not args.confirm, "--act asks again"


def test_asking_is_opt_in_and_the_old_flag_still_parses():
    """`--confirm` is the supervised mode now. `--auto` was the way to turn
    asking OFF, so it is kept and accepted -- a stale command line must not
    die at the argument parser, and asking for no prompts and getting none is
    the right answer whatever the flag is called."""
    from bridge.run import build_parser
    assert build_parser().parse_args(["--act", "--confirm"]).confirm
    assert not build_parser().parse_args(["--act", "--auto"]).confirm



# --- the wrong-hero recovery (2026-09-05) -------------------------------

def _two_hero_state(round_no=2, runaway_turns=1, crusader_turns=0):
    """The live Cove position, as `LiveState` read it.

    The save said the Runaway was next. The screen said the Crusader. Both
    were written at 13:25:43 and neither is corrupt -- the counters simply do
    not resolve it, which is the whole finding.
    """
    from bridge.live_state import LiveState, TurnEntry
    return LiveState(
        in_raid=True, in_battle=True, battle_round=round_no, battle_turn=2,
        turn_order=[
            TurnEntry(side="monster", guid=99, initiative=10.007, turns_taken=2),
            TurnEntry(side="monster", guid=100, initiative=9.007, turns_taken=2),
            TurnEntry(side="hero", guid=61, initiative=6.005,
                      turns_taken=runaway_turns),
            TurnEntry(side="hero", guid=1, initiative=6.001,
                      turns_taken=crusader_turns),
            TurnEntry(side="monster", guid=98, initiative=-2.005, turns_taken=1),
            TurnEntry(side="monster", guid=97, initiative=-3.005, turns_taken=1),
        ])


def test_skip_walks_to_the_other_hero():
    st = _two_hero_state()
    assert st.next_hero()[0].guid == 61            # the Runaway, as shipped
    assert st.next_hero(1)[0].guid == 1            # the Crusader, the recovery
    assert st.next_hero(2)[0] is None              # and no further


def test_skip_counts_only_HEROES_and_keeps_the_enemy_lead():
    """`enemies_first` is what makes a block provisional, so a skip that lost
    it would present look-ahead advice as the live position."""
    st = _two_hero_state()
    first, lead_a = st.next_hero()
    second, lead_b = st.next_hero(1)
    assert first.side == second.side == "hero"
    assert lead_a == lead_b == 0                   # both monsters ahead acted


def test_other_heroes_reaches_a_hero_the_prefix_scan_EXCLUDED():
    """Both corpus misses name a hero `pending_queue` has already ruled out,
    so the last resort cannot be a walk down that list."""
    st = _two_hero_state(round_no=1, runaway_turns=1, crusader_turns=0)
    # Every monster reads as having acted this round, so the prefix scan runs
    # off the end and offers NO hero -- while the game is plainly waiting for
    # one. That is the shape of both corpus misses.
    assert [t.guid for t in st.pending_queue() if t.side == "hero"] == []
    assert st.next_hero()[0] is None
    assert [t.guid for t in st.other_heroes(1)] == [61]
    assert [t.guid for t in st.other_heroes(61)] == [1]


def test_a_stunned_hero_is_never_offered_as_the_alternative():
    """Their slot is consumed without them acting, so they are not who the
    game is waiting for -- and a click for them would go nowhere twice."""
    from bridge.live_state import TurnEntry
    st = _two_hero_state()
    st.turn_order[3] = TurnEntry(side="hero", guid=1, initiative=6.001,
                                 turns_taken=0, stunned=True)
    assert st.next_hero(1)[0] is None
    assert st.other_heroes(61) == []


def test_the_skip_retires_the_moment_the_board_MOVES():
    """The skip is a hypothesis about a stuck position. A counter moving is
    the evidence it is unstuck, and holding it past that would advise the
    wrong hero for the rest of the fight."""
    from bridge.run import Session
    stuck = Session._turn_signature(_two_hero_state())
    same = Session._turn_signature(_two_hero_state())
    moved = Session._turn_signature(_two_hero_state(crusader_turns=1))
    assert stuck == same
    assert stuck != moved


def test_the_watcher_is_told_to_re_dispatch_an_UNCHANGED_save():
    """THE THIRD GATE, and the one that is invisible from `battle()`.

    `_watch` skips any poll whose raid signature has not moved -- and the
    wrong-hero situation is by definition one where nobody acted, so the file
    is byte-identical poll after poll. Clearing the dedupe key alone was not
    enough and the live re-run behaved exactly as before.

    Read-and-clear, like `_take_snuff`: a flag left set re-dispatches forever.
    """
    import ast
    import inspect
    import bridge.run as run

    session = object.__new__(run.Session)
    session._battle_skip = 0
    session._battle_key = object()
    session._replan = False
    assert session.take_replan() is False
    run.Session.note_turn_missed(session)
    assert session._battle_skip == 1
    assert session._battle_key is None
    assert session.take_replan() is True
    assert session.take_replan() is False, "a set flag would re-dispatch forever"

    # ...and `_watch` actually consults it, which no unit test can reach.
    src = inspect.getsource(run._watch)
    assert "take_replan" in src, "_watch never asks whether to re-dispatch"
    tree = ast.parse(inspect.getsource(run))
    assert any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
               and n.func.attr == "take_replan" for n in ast.walk(tree))


def test_the_skip_stops_at_the_bound_and_takes_no_more_polls():
    """Past `MAX_TURN_SKIPS` the answer is not "a different hero", and a
    re-plan that keeps being granted would send inputs on every tick."""
    import bridge.run as run
    session = object.__new__(run.Session)
    session._battle_skip = run.MAX_TURN_SKIPS
    session._battle_key = object()
    session._replan = False
    run.Session.note_turn_missed(session)
    assert session._battle_skip == run.MAX_TURN_SKIPS
    assert session.take_replan() is False


def test_a_battle_whose_save_STOPS_MOVING_asks_for_another_hero():
    """THE HALF THE FAILED-SEND PATH CANNOT REACH.

    `_send_and_confirm` calls a turn landed when the save changes at all -- and
    an enemy acting is a change. So a send can be FALSELY CONFIRMED: nothing
    failed, no counter moved, the dedupe key is identical, and the bridge goes
    quiet with the game waiting. Seen live: three real turns played, then
    silence, with the Runaway on Death's Door and the Crusader's bar up.

    The heartbeat is the only thing that catches it, so this pins that the
    watcher has one, that it is gated on a fight being on, and that it is
    bounded rather than pulsing.
    """
    import ast
    import inspect
    import bridge.run as run

    assert run.STUCK_SECONDS >= 20, "shorter than a measured run of enemy turns"
    src = inspect.getsource(run._watch)
    assert "STUCK_SECONDS" in src, "the watcher has no stuck check"
    assert "note_turn_missed" in src, "the stuck check asks for nothing"
    assert "in_battle" in src, "the stuck check is not gated on a fight"
    assert "MAX_TURN_SKIPS" in src, "the stuck check would pulse forever"

    # `stuck_since` has to be INITIALISED, not merely used -- it is read on a
    # poll that never parses anything, so a missing init is a NameError that
    # only fires once the game goes quiet.
    tree = ast.parse(inspect.getsource(run._watch))
    assigned = {t.id for n in ast.walk(tree) if isinstance(n, ast.Assign)
                for t in n.targets if isinstance(t, ast.Name)}
    assert {"stuck_since", "in_battle"} <= assigned


def test_a_LONE_hero_is_not_left_waiting_on_a_provisional_block():
    """THE THIRD SHAPE OF THE SAME LAG, and the skip cannot touch it.

    With one hero left there is nobody else to try. The queue then says a
    monster acts first, so the block is provisional and `plan_battle` refuses
    -- while that monster has already acted and its counter has not caught up.
    The bridge waits for an enemy turn that is never coming. Seen live: three
    heartbeats at 25 s apart and then nothing, one hero on Death's Door and
    the game waiting for him.

    "Provisional" means enemy turns are still AHEAD. Nothing having moved for
    `STUCK_SECONDS` is evidence that they are not.
    """
    from bridge.live_state import LiveState, TurnEntry
    lone = LiveState(
        in_raid=True, in_battle=True, battle_round=7, battle_turn=2,
        turn_order=[
            TurnEntry(side="monster", guid=98, initiative=3.0, turns_taken=6),
            TurnEntry(side="hero", guid=1, initiative=1.0, turns_taken=5),
        ])
    # Ordinarily an enemy is ahead, so the advice is a look-ahead and refused.
    hero, ahead = lone.hero_for_turn(0)
    assert hero.guid == 1 and ahead == 1

    # And a skip finds nobody else at all -- which is what says the hero was
    # never the problem.
    assert lone.hero_for_turn(1)[0] is None
    assert lone.other_heroes(1) == []


def test_the_forced_block_asks_the_advisor_for_the_SAME_hero():
    """`battle()` resolves a useless skip back to the first hero. Handing the
    advisor the raw count then makes it walk past that hero AGAIN and answer
    "no hero left to act this round" -- which is exactly what the first live
    run printed, one line after naming the hero it was about to advise, and
    the actuator then refused with `slot=-1`."""
    import ast
    import inspect
    import bridge.run as run

    src = inspect.getsource(run.Session.battle)
    tree = ast.parse(src.lstrip())
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and n.func.attr == "recommend"]
    assert len(calls) == 1
    arg = calls[0].args[1]
    assert isinstance(arg, ast.IfExp), "the skip is passed through unguarded"
    assert isinstance(arg.body, ast.Constant) and arg.body.value == 0


def test_a_frozen_DUNGEON_replans_the_room_rather_than_ending_the_night():
    """The other half of the same stall, and it needed a different answer.

    A map click for `rooK` went nowhere on 2026-09-05: the save sat unwritten
    for 180 s, the poll skipped every tick because the signature had not
    moved, and the iteration ended with the party in `rooJ` at 100 and 94
    stress. Out of a fight there is no other hero to try -- the room simply
    has to be planned again, which re-opens the map, re-fits it and re-clicks.

    Unbounded on purpose, unlike the hero skip: that walks a finite list, this
    re-runs a plan free to reach a different answer each time, and the route
    layer already refuses rather than repeating a click it cannot justify.
    """
    import ast
    import inspect
    import bridge.run as run

    session = object.__new__(run.Session)
    session._room_key = ("rooJ", "something")
    session._replan = False
    run.Session.note_room_stuck(session)
    assert session._room_key is None
    assert session.take_replan() is True

    src = inspect.getsource(run._watch)
    assert "note_room_stuck" in src, "the watcher never replans a stuck room"
    # ...and it is gated on being IN a raid, or a quiet Hamlet would replan a
    # room every 25 s forever.
    tree = ast.parse(src.lstrip())
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and n.func.attr == "note_room_stuck"]
    assert len(calls) == 1
    assert "in_raid" in src


def test_the_room_stall_and_the_battle_stall_share_ONE_timer():
    """Two timers would need two resets, and the one that was forgotten would
    fire on a raid that is moving perfectly well. Both recoveries have to sit
    under the same `sig == last_sig` branch, which is the only place the timer
    is armed and the only place it is cleared."""
    import ast
    import inspect
    import bridge.run as run

    tree = ast.parse(inspect.getsource(run._watch).lstrip())
    blocks = [n for n in ast.walk(tree)
              if isinstance(n, ast.If)
              and "last_sig" in ast.dump(n.test)
              and "stuck_since" in ast.dump(n)]
    assert len(blocks) == 1, "the stall timer lives in more than one place"
    inside = {c.func.attr for c in ast.walk(blocks[0])
              if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)}
    assert {"note_turn_missed", "note_room_stuck"} <= inside


def test_the_recovery_is_actually_WIRED_UP():
    """A CALLBACK NOBODY ASSIGNS IS A FEATURE THAT DOES NOT EXIST.

    Every part of this worked in isolation and the first live re-run behaved
    exactly as before, because `main()` never set the attribute -- one line,
    below everything a test can construct. `tests/training/test_train_wiring.py` exists
    for the same class of bug, and asks the workers what they received; here
    the seam is inside `main`, so the check is static.
    """
    import ast
    import inspect
    import bridge.run as run

    tree = ast.parse(inspect.getsource(run))
    wired = [n for n in ast.walk(tree)
             if isinstance(n, ast.Assign)
             and any(isinstance(t, ast.Attribute) and t.attr == "on_turn_missed"
                     for t in n.targets)]
    assert wired, "run.py never assigns runner.on_turn_missed"
    assert any(isinstance(n.value, ast.Attribute)
               and n.value.attr == "note_turn_missed" for n in wired),         "on_turn_missed is assigned something other than note_turn_missed"


def test_the_skip_is_bounded_by_the_party():
    """Every extra try is a real input sent into the running game."""
    from bridge.run import MAX_TURN_SKIPS
    assert 1 <= MAX_TURN_SKIPS <= 4

def test_a_deferred_curio_is_advice_and_arms_no_curio_action():
    """`RoomAdvice.empty` gates whether the room layer speaks at all, so a
    deferral has to count as something to say -- otherwise the actuator gets
    "no room advice to act on" and the party stands still, which is the exact
    failure `_LayerDone` was written for.

    And it must NOT arm `curio_action`: the marker scores that against the
    save, and a curio deliberately left standing would be recorded as advice
    the player ignored.
    """
    from bridge.run import RoomAdvice

    deferred = RoomAdvice(curio_id="crate", curio_defer=True)
    assert not deferred.empty
    assert deferred.curio_action == ""
    assert RoomAdvice().empty


def test_the_block_reports_what_is_still_on_the_map():
    """The loot layer's whole output in the block is one summary line plus the
    next decision. It is priced from the game's own tables, so it is the first
    time the block has said what a detour is WORTH rather than what it costs
    in moves."""
    import inspect

    from bridge import run

    src = inspect.getsource(run.Layers.block)
    assert "plan_loot" in src
    assert "still on the map" in src
    # One decision, not the whole plan: the block has a ten-line budget.
    assert 'if s.action not in ("walk", "finish")' in src


# -- leaving with loot still on the map (2026-09-06) -----------------------
#
# Live, two adjacent lines of ONE block, from one state:
#
#     LOOT    346g + ... still on the map
#     RAID    RETURN  - objective met and nothing left is priced positive
#
# and the party walked home leaving a DEFERRED curio behind. Fran: "it went
# back to the hamlet". `route.optional_targets` excludes corridor curios by
# design, so `plan.detours` and `mine` are both blind to exactly the thing
# `lootplan` exists to price.


class _Hero:
    def __init__(self, stress=0.0, affliction_id=""):
        self.stress = stress
        self.affliction_id = affliction_id
        self.hero_class = "crusader"


class _State:
    def __init__(self, heroes=()):
        self.heroes = list(heroes)


class _Plan:
    targets = ()
    detours = ()


def _loot_with(*stops):
    from bridge.lootplan import LootPlan
    return LootPlan(stops=tuple(stops))


def _stop(action, gold, curio_id="travellers_tent_tutorial", area="coAB",
          torch=0.0):
    from data.loot_loader import LootValue
    from bridge.lootplan import Stop
    return Stop(area=area, action=action, tile=4, torch=torch,
                curio_id=curio_id, value=LootValue(gold=gold))





def test_the_battle_quest_targets_MATCH_the_games_own_counter():
    """A negative result worth keeping, so nobody re-opens it.

    A raid save carries `raid_instance.battle_all_rooms` --
    `{current_battle_count, target_battle_count}` -- which is the game's own
    statement of how far along a `battle_all_rooms` objective is.
    `route.mandatory_targets` does not read it: it re-derives the objective
    from the map as "every room with an unspent battle". Those could easily
    disagree, and if the map demanded more fights than the quest the bridge
    would spend real risk on fights nothing required.

    Measured over the whole capture corpus, 2026-09-20: **263 paired
    (raid, map) snapshots, 263 exact agreements, 0 either way.** The map
    derivation is right. This test re-runs that check so a change to
    `_rooms_needing_battle` cannot quietly break it.
    """
    import json
    from pathlib import Path as _P

    from data.dson import parse_file
    from bridge.dungeon_map import read_dungeon_map
    from bridge.route import _rooms_needing_battle

    traces = _P(__file__).resolve().parents[2] / "archive" / "traces"
    if not traces.is_dir():
        pytest.skip("no capture corpus")
    checked = 0
    for trace in ("raidsnaps", "raidsnaps2", "raidsnaps3", "raidsnaps4"):
        d = traces / trace
        if not d.is_dir():
            continue
        for raid in sorted(d.glob("*_persist.raid.json")):
            mp = raid.with_name(raid.name.replace("persist.raid", "persist.map"))
            if not mp.exists():
                continue
            try:
                blob = parse_file(raid).to_dict()
                dungeon = read_dungeon_map(mp)
            except Exception:                              # noqa: BLE001
                continue
            if dungeon is None:
                continue
            root = blob.get("base_root", blob)
            ri = root.get("data", root).get("raid_instance", {})
            bar = ri.get("battle_all_rooms") or {}
            if not bar:
                continue
            need_quest = max(0, int(bar.get("target_battle_count") or 0)
                             - int(bar.get("current_battle_count") or 0))
            assert len(_rooms_needing_battle(dungeon)) == need_quest, (
                f"{raid.name}: the quest needs {need_quest} more fights and "
                f"the map derivation says "
                f"{len(_rooms_needing_battle(dungeon))}")
            checked += 1
    if checked < 100:
        pytest.skip(f"only {checked} paired snapshots - corpus is thin")


def test_the_light_is_HELD_where_the_loot_plan_wants_it_dark():
    """A relight in the corridor the snuff was paid for undoes the play.

    The torch layer is asked before the loot plan exists, so out of combat it
    sees only "below radiant, torches in the pack" and answers RELIGHT -- in
    the very area `lootplan` had just snuffed for. Fran, 2026-09-20: *"when
    you beat a battle and immediately after there is a loot curio ... you
    should keep the torch low for that part of the dungeon"*.

    The plan's own intended light is the signal, so no weight is invented.
    """
    from bridge.run import _dark_pays_here

    dark = _loot_with(_stop("defer", 646.0, curio_id="tent", area="coAB",
                            torch=0.0))
    assert _dark_pays_here(dark, "coAB", None) is True
    assert _dark_pays_here(dark, "rooA", "coAB") is True, "one step away is here"
    assert _dark_pays_here(dark, "rooZ", "rooY") is False, "not here at all"
    assert _dark_pays_here(None, "coAB", None) is False


def test_a_FIGHT_between_here_and_the_dark_loot_ends_the_scan():
    """**The bridge oracle caught this one before any live run did.**

    The first version asked only "is there a dark pickup nearby". In `coHM`
    the next dark stop was the heirloom chest in `rooH`, on the far side of a
    FIGHT in `rooH` -- so the rule held the light and would have walked the
    party into a dim combat, against the hard 76 floor that `lootplan` and
    Fran's own rule are both built on: *"try to never combat on under 75
    torch"*.

    A `relight` the plan has queued ends the scan for the same reason: it is
    the plan saying the light is wanted before anything else it has lined up.
    """
    from bridge.run import _dark_pays_here

    chest = _stop("take", 900.0, curio_id="heirloom_chest", area="rooH",
                  torch=0.0)
    fight = _stop("fight", 0.0, curio_id="", area="rooH", torch=95.0)
    assert _dark_pays_here(_loot_with(chest), "coHM", "rooH") is True
    assert _dark_pays_here(_loot_with(fight, chest), "coHM", "rooH") is False

    relight = _stop("relight", 0.0, curio_id="", area="coHM", torch=95.0)
    assert _dark_pays_here(_loot_with(relight, chest), "coHM", "rooH") is False


def test_a_SKIP_and_a_LIT_stop_cannot_hold_the_light():
    """`skip` is loot nobody is collecting, so it justifies nothing, and a
    stop the plan wants opened in the light is the opposite of a reason to
    stay dark. Darkness outside the killing blow is a straight loss."""
    from bridge.run import _dark_pays_here

    skipped = _loot_with(_stop("skip", 100.0, curio_id="chest", area="coAB",
                               torch=0.0))
    assert _dark_pays_here(skipped, "coAB", None) is False

    lit = _loot_with(_stop("take", 100.0, curio_id="chest", area="coAB",
                           torch=100.0))
    assert _dark_pays_here(lit, "coAB", None) is False


def test_a_DEFERRED_curio_keeps_the_party_in_the_dungeon():
    """A deferral is a PROMISE. `lootplan` only defers because the walk home
    comes back through here, so banking the quest early converts "worth three
    times as much later" into "lost" -- which is strictly worse than never
    having deferred it."""
    from bridge.run import Layers

    choice, why = Layers._raid_end(_State([_Hero()]), _Plan(), [],
                                   _loot_with(_stop("defer", 558.0)))
    assert choice == "continue"
    assert "travellers_tent_tutorial" in why
    assert "DEFERRED" in why


def test_a_SKIP_is_not_a_reason_to_stay():
    """`skip` means never, at any light -- the whole reason it is a separate
    word from `defer`. A trapped chest the party cannot open must not hold it
    in the dungeon forever."""
    from bridge.run import Layers

    choice, _why = Layers._raid_end(
        _State([_Hero()]), _Plan(), [],
        _loot_with(_stop("skip", 400.0, curio_id="bandits_trapped_chest")))
    assert choice == "return"


def test_worthless_loot_is_not_a_reason_to_stay():
    from bridge.run import Layers

    choice, _why = Layers._raid_end(_State([_Hero()]), _Plan(), [],
                                    _loot_with(_stop("take", 0.0)))
    assert choice == "return"


def test_a_curio_the_ACTUATOR_gave_up_on_stops_pinning_the_party():
    """`ActRunner._give_up_on_curio` stops trying one for the rest of the raid
    -- a corridor sack once ate a whole iteration -- but the decision layer
    could not see that, so the plan kept pricing it and `_raid_end` kept
    answering CONTINUE.

    Live 2026-09-06: ten presses at the tutorial tent with no panel, "giving
    up for this raid", and then the party walked `rooA` to `coAB` and back for
    as long as the run lasted. Same shape as a deferral nothing comes back
    for, one layer along.
    """
    from bridge.run import Layers

    loot = _loot_with(_stop("take", 558.0))
    stay, _why = Layers._raid_end(_State([_Hero()]), _Plan(), [], loot)
    assert stay == "continue", "the premise: it holds the party while live"

    choice, _why = Layers._raid_end(
        _State([_Hero()]), _Plan(), [], loot,
        {("coAB", "travellers_tent_tutorial")})
    assert choice == "return"


class _Mine:
    """What the curio layer says is underfoot."""
    def __init__(self, area_id, curio_id, action="interact"):
        self.area_id, self.curio_id, self.action = area_id, curio_id, action
        self.item = ""


class _Detours:
    targets = ()

    def __init__(self, *detours):
        self.detours = detours


class _D:
    def __init__(self, area_id, what, moves=1, fights=0):
        self.area_id, self.what = area_id, what
        self.extra_moves, self.extra_fights = moves, fights


def test_a_detour_the_LOOT_layer_priced_out_is_not_a_reason_to_stay():
    """`plan.detours` is the ROUTE layer's list of optional targets and it
    predates any pricing -- it offers whatever is reachable. `lootplan` is the
    layer that decides whether a curio is worth opening, and it refuses the
    Old Road's trapped chest outright: `Blight 1` bare-handed, no skeleton
    key.

    Live 2026-09-06, once the tent stopped holding the party, this branch took
    over and held it in the same corridor for a chest nothing would ever open.
    Third reader of "is there something here worth staying for" in one method.
    """
    from bridge.run import Layers

    plan = _Detours(_D("rooB", "curio(bandits_trapped_chest)"))
    loot = _loot_with(_stop("skip", 400.0, curio_id="bandits_trapped_chest",
                            area="rooB"))
    choice, _why = Layers._raid_end(_State([_Hero()]), plan, [], loot)
    assert choice == "return"


def test_a_detour_the_loot_layer_has_NOT_refused_still_counts():
    """The filter must not swallow the whole list. A detour to something the
    plan means to take -- or has no opinion on at all -- is still a reason to
    stay, which is what `detours` was for before any of this."""
    from bridge.run import Layers

    plan = _Detours(_D("rooC", "curio(iron_maiden)"))
    loot = _loot_with(_stop("skip", 400.0, curio_id="bandits_trapped_chest",
                            area="rooB"))
    choice, why = Layers._raid_end(_State([_Hero()]), plan, [], loot)
    assert choice == "continue"
    assert "iron_maiden" in why, why


def test_BOTH_readers_of_the_give_up_honour_it():
    """`_raid_end` asks two things whether there is something here worth
    staying for: the curio layer's own verdict on what is underfoot, and the
    loot plan. The curio branch runs FIRST, so filtering only the loot plan
    left the loop exactly where it was -- the party held station in `coAB`
    printing "take it first" every thirty seconds until the run was killed,
    with the actuator having already given up on that very curio.

    A filter applied to one of two readers is not a filter.
    """
    from bridge.run import Layers

    mine = [_Mine("coAB", "travellers_tent_tutorial")]
    loot = _loot_with(_stop("take", 558.0))
    gone = {("coAB", "travellers_tent_tutorial")}

    stay, _ = Layers._raid_end(_State([_Hero()]), _Plan(), mine, loot)
    assert stay == "continue", "the premise: it holds while the curio is live"

    choice, _why = Layers._raid_end(_State([_Hero()]), _Plan(), mine, loot,
                                    gone)
    assert choice == "return"


def test_giving_up_on_ONE_curio_does_not_release_the_others():
    """The skip is keyed on `(area, curio_id)`, the same key the actuator
    banks, so abandoning a sack in one corridor must not abandon an identical
    sack in another -- nor everything else on the map."""
    from bridge.run import Layers

    loot = _loot_with(_stop("take", 100.0, curio_id="sack", area="coAB"),
                      _stop("defer", 900.0, curio_id="sack", area="coCD"))
    choice, why = Layers._raid_end(_State([_Hero()]), _Plan(), [], loot,
                                   {("coAB", "sack")})
    assert choice == "continue"
    assert "coCD" in why, why


def test_the_risk_gate_still_outranks_the_loot():
    """An afflicted hero is the game's own signal that a run has gone wrong.
    Loot must never be able to keep that party in the dungeon -- so the
    ordering inside `_raid_end` is load-bearing, not incidental."""
    from bridge.run import Layers

    rich = _loot_with(_stop("defer", 5000.0))
    afflicted = _State([_Hero(affliction_id="paranoid")])
    assert Layers._raid_end(afflicted, _Plan(), [], rich)[0] == "return"
    checked = _State([_Hero(stress=100.0)])
    assert Layers._raid_end(checked, _Plan(), [], rich)[0] == "return"


def test_no_loot_plan_at_all_behaves_exactly_as_before():
    """`plan_loot` goes through `_safe`, so None is a real input -- and the
    old two-clause answer has to survive it unchanged."""
    from bridge.run import Layers

    assert Layers._raid_end(_State([_Hero()]), _Plan(), [], None)[0] == "return"


def test_the_loot_plan_can_supply_the_walk_the_route_no_longer_offers():
    """Half of "continue" is knowing WHERE, and `plan_route` stops planning the
    moment the objective is met -- `targets` empty, `next_step` empty. A
    "continue" with no step is a party standing still agreeing it ought to go
    and fetch something.

    On the Old Road, from `rooB` with the tent still standing in the hallway,
    the loot plan's own walk is what points back at it.
    """
    import pytest

    from bridge.dungeon_map import read_dungeon_map
    from bridge.lootplan import plan_loot
    from bridge.route import Quest
    from bridge.run import _loot_outstanding

    path = GAME_DIR / "scripts" / "starting_save" / "persist.map.json"
    if not path.exists():
        pytest.skip("the game is not installed at the hardcoded path")
    dungeon = read_dungeon_map(path)
    loot = plan_loot(dungeon, Quest(quest_type="explore", dungeon="weald",
                                    difficulty=1), "rooB", 70.0, {})
    outstanding = _loot_outstanding(loot)
    assert outstanding is not None, "the tent is still on the map"
    stop, gold = outstanding
    assert stop.curio_id == "travellers_tent_tutorial"
    assert gold > 400, "priced through the game's own tables at torch 0"

    ahead = [a for a in loot.walk if a != "rooB"]
    assert ahead and ahead[0] == "coAB", "the hallway the tent is in"


# -- the heartbeat must not flatline (2026-09-06) --------------------------
#
# Live, a tutorial fight stopped dead for six minutes and the log's last line
# was the bridge naming the hero it should have been asking:
#
#     the game is asking the crusader and the save says highwayman
#         - not sending; asking about the other hero on the next poll
#
# There was no next poll. `_watch` skips any poll whose raid signature has
# not moved, `take_replan` is the only thing that dispatches anyway, and past
# `MAX_TURN_SKIPS` nothing was left to set it.


class _NoAdvice:
    last_action = last_engine = None
    last_provisional = False
    store = None


def test_the_retry_budget_bounds_GUESSING_not_TRYING():
    """Spending the budget must not be the same as giving up.

    `note_turn_missed` is rightly bounded -- walking the queue blind past a
    few skips is hopeless. What made that fatal is that the bound also
    silenced the only gate that can dispatch on a save which is not moving,
    which is precisely the situation the bound is reached in.
    """
    from bridge.run import MAX_TURN_SKIPS, Session

    s = Session(_NoAdvice())
    for _ in range(MAX_TURN_SKIPS + 2):
        s.note_turn_missed("crusader")
    assert s._battle_skip == MAX_TURN_SKIPS
    # the fatal state: nothing pending, and the save will never move
    s.take_replan()
    assert s.take_replan() is False

    s.note_turn_stuck()
    assert s.take_replan() is True, "the heartbeat cannot re-open the gate"
    assert s._battle_skip == 0, "the blind walk has to start over"


def test_the_panel_s_ANSWER_survives_the_budget():
    """`battle()` solves the skip from `_battle_asked` in one lookup, so the
    class the screen named is the most valuable thing the bridge holds when
    it is stuck. Clearing it here would throw away the answer and go back to
    guessing -- which is what the six minutes were spent doing."""
    from bridge.run import MAX_TURN_SKIPS, Session

    s = Session(_NoAdvice())
    for _ in range(MAX_TURN_SKIPS + 1):
        s.note_turn_missed("crusader")
    s.note_turn_stuck()
    assert s._battle_asked == "crusader"


def test_a_turn_that_lands_still_retires_the_recovery():
    """The reset must not outlive the stall it was for. `battle()` clears both
    the skip and the remembered class as soon as any counter moves, and
    `note_turn_stuck` must not interfere with that."""
    from bridge.run import Session

    s = Session(_NoAdvice())
    s.note_turn_missed("crusader")
    s.note_turn_stuck()
    s._battle_sig = ("stale",)
    assert s._battle_asked == "crusader"


def test_the_watch_loop_actually_calls_the_recovery():
    """The wiring is the whole fix -- a `Session` method nothing calls is the
    same silence with more code. Three gates stand between a missed turn and
    a new block and this is the one furthest from `battle()`."""
    import inspect

    import bridge.run as run

    src = inspect.getsource(run._watch)
    assert "note_turn_stuck()" in src
    # ...and it must be reachable: the guarded branch above it returns early
    # for the budget it has already spent, so a bare `if` there would leave
    # the same hole.
    assert "elif in_battle:" in src


def test_the_panel_s_answer_is_never_published_as_PROVISIONAL():
    """Solving the skip is not enough on its own, and the second live run
    proved it: the bridge found the crusader, published him as "after 1 enemy
    turn -> Crusader", and the actuator refused -- correctly, because a timing
    refusal must never fall through to another candidate. The deadlock had
    moved, not gone.

    `enemies_first` comes from the save's turn queue, the one quantity here
    that is known to lag; the panel is the game's live prompt. A hero is not
    asked for input while enemy turns are pending, so the two cannot both be
    right and the screen wins.
    """
    import inspect

    import bridge.run as run

    src = inspect.getsource(run.Session.battle)
    # the correction exists, and it is gated on the panel having named this
    # very hero -- zeroing it unconditionally would publish a genuine
    # look-ahead as live and act a turn early, which is the bug the
    # provisional label exists to prevent.
    assert "enemies_first = 0" in src
    assert "who.hero_class == want" in src


def test_a_look_ahead_with_no_panel_answer_stays_provisional():
    """The guard is `want`, so an ordinary look-ahead -- nobody named, the
    queue simply reporting enemy turns ahead -- must keep its label. Acting on
    one is acting a turn early."""
    import inspect

    import bridge.run as run

    src = inspect.getsource(run.Session.battle)
    head = src[:src.index("enemies_first = 0")]
    assert "if want and hero is not None and enemies_first:" in head


# -- the survival layer's verdict has to leave the prose (2026-09-06) ------


def _battle_block(*lines):
    head = ("Highwayman: OPEN VEIN on rank 2",)
    return chr(10).join(head + lines)


def test_the_RETREAT_verdict_comes_out_as_structure():
    """`advise_retreat` prices the whole rest of the dungeon and applies the
    death floor, and its answer was printed and nowhere else. `Layers.block`
    turned it into a headline for the ROOM path; the battle path never
    looked, so the actuator could not act on it and sent the policy's pick
    instead. Live, that killed a party."""
    from bridge.run import Session

    adv = _NoAdvice()
    adv.last_wipe_rate = 0.9            # over FIGHT_RETREAT_WIPE: see below
    s = Session(adv)
    out = s._battle_update(None, _battle_block(
        "  leaving costs ~5,889 gold, staying ~4,972 - RETREAT"), 0)
    assert out.retreat_advised is True


def test_inside_a_fight_only_a_near_certain_WIPE_overrules_the_model():
    """Fran, 2026-09-23: keep the combat model's decisions its own, and let
    the dungeon layer pull a party out of a fight only when a wipe is really
    likely. Below the threshold the verdict waits for the fight to end."""
    from bridge.run import FIGHT_RETREAT_WIPE, Session

    adv = _NoAdvice()
    adv.last_wipe_rate = FIGHT_RETREAT_WIPE - 0.05
    out = Session(adv)._battle_update(None, _battle_block(
        "  leaving costs ~5,889 gold, staying ~4,972 - RETREAT"), 0)
    assert out.retreat_advised is False


def test_the_POLICYS_OWN_retreat_option_is_not_mistaken_for_the_verdict():
    """The model's shortlist prints "alt: RETREAT on self  7%" on most turns
    of a losing fight. A bare `"RETREAT" in block` reads that as the survival
    layer saying leave -- which would hand the actuator a retreat on the
    strength of the policy ranking it FOURTH."""
    from bridge.run import Session

    s = Session(_NoAdvice())
    out = s._battle_update(None, _battle_block(
        "     alt: RETREAT on self  7%",
        "  leaving costs ~5,889 gold, staying ~4,972 - worth pushing on"), 0)
    assert out.retreat_advised is False



def test_a_SELF_cast_heal_is_not_classified_as_an_attack():
    """The Abomination's Absolution targets the caster. The ledger called it
    "attack" and logged seven false DIFFERs in two raids (2026-09-24)."""
    import types

    eng = types.SimpleNamespace(
        heroes=[types.SimpleNamespace(id=1, skill_tier=0)],
        _is_ally_skill=lambda sid: False,
        get_skill_data=lambda sid, level: types.SimpleNamespace(heal_min=3))
    self_cast = types.SimpleNamespace(skill_id="absolution", hero_id=1,
                                      target_id=-1)
    strike = types.SimpleNamespace(skill_id="manacles", hero_id=1, target_id=5)
    assert advised_kind(eng, self_cast) == "heal"
    assert advised_kind(eng, strike) == "attack"


def test_a_stuck_loot_hold_is_given_up():
    """Live 2026-09-27: the raid held the party 27 minutes for a deferred
    knife rack nothing could reach. The held stop joins the given-up set."""
    from types import SimpleNamespace
    from bridge.run import Session, _loot_outstanding
    said = []
    fake = SimpleNamespace(layers=SimpleNamespace(loot_goal=("coAD", "knife_rack"),
                                                  abandoned=None),
                           emit=said.append)
    Session.abandon_loot_goal(fake)
    assert fake.layers.abandoned == {("coAD", "knife_rack")}
    assert said and "knife_rack" in said[0]
    stop = SimpleNamespace(action="defer", area="coAD", curio_id="knife_rack",
                           value=SimpleNamespace(worth=lambda: 185.0))
    assert _loot_outstanding(SimpleNamespace(stops=[stop]),
                             fake.layers.abandoned) is None


def test_the_step_back_to_loot_behind_the_party():
    """Live 2026-09-27: the walk was over and the heirloom chest sat in the
    adjacent rooA; nothing aimed the party back at it."""
    from types import SimpleNamespace
    from bridge.run import _step_toward
    graph = {"rooA": {"rooD": "coAD", "rooB": "coAB"},
             "rooD": {"rooA": "coAD"}, "rooB": {"rooA": "coAB"}}
    d = SimpleNamespace(graph=lambda: graph)
    assert _step_toward(d, "rooD", "rooA") == ("coAD", "rooA")
    assert _step_toward(d, "rooD", "coAD") == ("coAD", "rooA")
    assert _step_toward(d, "rooD", "coAB") == ("coAD", "rooA")
    assert _step_toward(d, "coAD", "rooA") == ("rooA", "rooA")
    assert _step_toward(d, "coAD", "coAB") == ("rooA", "rooA")


def test_a_detour_to_a_GIVEN_UP_curio_does_not_hold_the_raid():
    """Live 2026-09-27: an heirloom chest that did not answer three presses
    held the raid as "+0 moves away" after the actuator gave up on it."""
    from bridge.run import Layers
    plan = _Detours(_D("rooA", "treasure(heirloom_chest)"))
    choice, _why = Layers._raid_end(_State([_Hero()]), plan, [], None,
                                    abandoned={("rooA", "heirloom_chest")})
    assert choice == "return"



if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))


# ── a self-cast is clickable ──

class _FakeActor:
    def __init__(self, hero_id, position):
        self.id = hero_id
        self.position = position
        self.skill_tier = 0


class _FakeEngine:
    def __init__(self, heroes):
        self.heroes = heroes

    def get_skill_data(self, skill_id, level=0):
        return None


class _FakeAction:
    def __init__(self, hero_id, skill_id, target_id):
        self.hero_id = hero_id
        self.skill_id = skill_id
        self.target_id = target_id


def test_a_self_cast_names_the_CASTER_s_rank():
    """**Every self-buff in the game was unplayable.**

    The engine encodes "no target" as a negative id, and `advised_slot`
    returned `("", -1)` for it -- so `plan_battle` refused with "advice names
    no target rank to click" and the actuator never played Bulwark of Faith,
    Take Aim, Bolster or Solemnity, however strongly the policy wanted them.

    The refusal was right about the DATA and wrong about the SCREEN: DD casts
    a self-targeted skill by pressing the digit and clicking the caster, the
    same as any other target. Found by the overnight loop stalling on a
    Crusader at 7/33 HP whose best move was Bulwark of Faith.
    """
    from bridge.run import advised_slot                        # noqa: PLC0415
    engine = _FakeEngine([_FakeActor("h1", 0), _FakeActor("h2", 2)])
    side, rank = advised_slot(engine, _FakeAction("h2", "bulwark_of_faith", -1))
    assert side == "hero"
    assert rank == 2, "the click must land on the caster, not rank 0"


def test_a_self_cast_by_an_unknown_actor_still_refuses():
    """The fallback has to stay a refusal. An actor the engine does not hold
    has no rank, and guessing one would click a hero at random."""
    from bridge.run import advised_slot                        # noqa: PLC0415
    engine = _FakeEngine([_FakeActor("h1", 0)])
    assert advised_slot(engine, _FakeAction("ghost", "take_aim", -1)) == ("", -1)


def test_pass_and_retreat_are_still_targetless():
    """These are genuinely rankless and must NOT be redirected onto the
    caster -- the ordering of the checks in `advised_slot` is what keeps that
    true, since they also carry a negative target id."""
    from bridge.run import advised_slot                        # noqa: PLC0415
    engine = _FakeEngine([_FakeActor("h1", 1)])
    for skill in ("__pass__", "__retreat__", "__provision_food__"):
        assert advised_slot(engine, _FakeAction("h1", skill, -1)) == ("", -1), skill


# --- steer the wrong-hero recovery by what the SCREEN named (2026-09-06) ---


def test_the_asked_class_is_remembered_and_retires_with_the_skip():
    """It is a hypothesis about ONE stuck position, exactly like the skip.

    A stale class would steer a later turn to a hero the game is no longer
    asking -- which is the failure this whole path exists to fix, inverted.
    So it is cleared wherever the skip is cleared, and a test that only
    checked it was REMEMBERED would miss that entirely.
    """
    import inspect

    from bridge import run

    src = inspect.getsource(run.Session)
    assert 'self._battle_asked = ""' in src
    # set in the callback...
    missed = inspect.getsource(run.Session.note_turn_missed)
    assert "_battle_asked = asked" in missed
    # ...and retired beside the skip, in battle().
    battle = inspect.getsource(run.Session.battle)
    lines = [ln.strip() for ln in battle.splitlines()]
    i = lines.index("self._battle_skip = 0")
    assert lines[i + 1] == 'self._battle_asked = ""', lines[i:i + 3]


def test_the_callback_still_works_with_NO_argument():
    """`note_turn_missed()` took none for a long time, and `bridge/act.py`
    passes a class only when the panel could read one. A caller with its own
    listener must not break because a keyword appeared."""
    import inspect

    from bridge import run

    sig = inspect.signature(run.Session.note_turn_missed)
    assert sig.parameters["asked"].default == ""


def test_the_steer_FALLS_BACK_when_no_skip_reaches_that_class():
    """The panel can be right about a hero this queue cannot produce -- a
    stunned hero is not in `other_heroes` at all -- and a wrong steer is
    worse than a slow one, so the blind walk stays as the fallback."""
    import inspect

    from bridge import run

    src = inspect.getsource(run.Session.battle)
    body = src[src.index("want = self._battle_asked"):]
    # the walk's own result is only replaced INSIDE the match branch
    assert "for probe in range(MAX_TURN_SKIPS + 1):" in body
    assert "if who is not None and who.hero_class == want:" in body


# -- the walk must not change its mind (2026-09-06) -----------------------
#
# Fran, watching the party shuttle between two hallways with the quest done
# and loot in two directions: *"wants to go left side room, model detects
# downside room on the map first, and goes back like it was wrong the first
# time, both ways are good in a sense as both have loot, but it needs to
# maintain the pathing consistent to not loop"*.


def test_a_goal_already_CHOSEN_beats_a_marginally_better_one():
    """The plan is rebuilt every poll from wherever the party now stands, so
    two comparably priced stops swap places as it walks. A tie-break does not
    fix that -- it still flips the moment the numbers cross."""
    from bridge.run import _loot_outstanding

    loot = _loot_with(_stop("take", 500.0, curio_id="chest", area="rooA"),
                      _stop("take", 520.0, curio_id="sack", area="rooF"))
    best, _g = _loot_outstanding(loot)
    assert best.curio_id == "sack", "the unheld answer is still the richest"

    held, gold = _loot_outstanding(loot, None, ("rooA", "chest"))
    assert held.curio_id == "chest", "it turned the party round for 20 gold"
    assert gold == 500.0


def test_a_goal_that_is_GONE_releases_the_hold():
    """Collected, or priced out, or given up on: the commitment is to a stop
    that still exists, never to an area."""
    from bridge.run import _loot_outstanding

    loot = _loot_with(_stop("take", 520.0, curio_id="sack", area="rooF"))
    stop, _g = _loot_outstanding(loot, None, ("rooA", "chest"))
    assert stop.curio_id == "sack"

    gone = _loot_with(_stop("skip", 900.0, curio_id="chest", area="rooA"))
    assert _loot_outstanding(gone, None, ("rooA", "chest")) is None


def test_the_hold_never_revives_something_ABANDONED():
    """The actuator's give-up outranks the walk's commitment -- otherwise a
    curio nothing can open would pin the party for the rest of the raid,
    which is the exact failure `abandoned` was added for."""
    from bridge.run import _loot_outstanding

    loot = _loot_with(_stop("take", 500.0, curio_id="chest", area="rooA"),
                      _stop("take", 200.0, curio_id="sack", area="rooF"))
    stop, _g = _loot_outstanding(loot, {("rooA", "chest")}, ("rooA", "chest"))
    assert stop.curio_id == "sack"


def test_the_loot_walk_aims_at_the_step_AFTER_the_party():
    """`[a for a in walk if a != here]` is a filter, not a position: a party
    halfway along its own plan was aimed at the plan's first entry, which is
    behind it. rooC -> corE -> rooC -> corE, twenty seconds a lap."""
    walk = ["rooF", "corE", "rooC", "corB", "rooA"]
    here = "rooC"
    at = len(walk) - 1 - walk[::-1].index(here) if here in walk else -1
    ahead = ([a for a in walk[at + 1:] if a != here] if at >= 0
             else [a for a in walk if a != here])
    assert ahead[0] == "corB", "it aimed backwards"
    assert [a for a in walk if a != here][0] == "rooF", "which is what it did"


def test_a_REVISIT_in_the_walk_uses_the_LAST_time_the_party_was_there():
    """A deferred curio's revisit is the walk naming one area twice, so the
    first occurrence is not where the party is."""
    walk = ["rooA", "coAB", "rooB", "coAB", "rooA"]
    here = "coAB"
    at = len(walk) - 1 - walk[::-1].index(here)
    ahead = [a for a in walk[at + 1:] if a != here]
    assert ahead == ["rooA"], ahead
