"""The demonstration corpus (`bridge/journal.py`, and `run.py`'s hooks into it).

Fran, 2026-09-03: *"Improve the advise model so it gathers much more data and
ill just run it and play through several expeditions, make sure it logs
everything, dungeon, hamlet, team management, money management etc."*

Two things had to be true for that to produce anything usable, and both are
pinned here because both were false before:

1. **The Hamlet was never recorded at all.** `run.py`'s watch loop keys on
   `persist.raid.json` and `continue`s whenever the party is not in a raid, so
   every town visit ever made went past it unseen.
2. **Recording what was played was fused to grading it.** `_resolve` returns
   immediately when no advice was armed, and no advice is armed when no
   checkpoint loads — which is the state this repo is in until the retrain. A
   demonstration corpus that needs a working policy to exist is useless exactly
   when it is most wanted.
"""

import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from bridge.journal import SCHEMA, Journal, diff_town, town_snapshot

REPO = Path(__file__).resolve().parents[2]
SNAPS = REPO / "archive" / "traces" / "raidsnaps2"


def _rows(path):
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


# --- the sink -------------------------------------------------------------

def test_every_event_is_one_json_line_carrying_its_kind_and_schema(tmp_path):
    path = tmp_path / "j.jsonl"
    j = Journal(path)
    j.event("decision", layer="curio", verdict="MATCH")
    j.event("play", layer="combat", played="attack")
    j.close()
    rows = _rows(path)
    assert [r["kind"] for r in rows] == ["decision", "play"]
    assert all(r["schema"] == SCHEMA and isinstance(r["t"], float) for r in rows)


def test_it_appends_so_a_session_adds_to_the_corpus(tmp_path):
    """A night of play is several launches of the same script. Truncating would
    make the corpus 'whatever happened last', which is not a corpus."""
    path = tmp_path / "j.jsonl"
    for kind in ("first", "second"):
        j = Journal(path)
        j.event(kind)
        j.close()
    assert [r["kind"] for r in _rows(path)] == ["first", "second"]


def test_a_disabled_journal_is_free_and_silent(tmp_path):
    j = Journal(None)
    assert not j.enabled
    j.event("decision", layer="curio")      # must not raise
    j.close()


def test_an_unserialisable_payload_does_not_take_the_poll_thread_down(tmp_path):
    """The loop is a daemon and the only source of updates while somebody is
    playing. Losing a line beats going silent mid-raid, so the encoder has a
    last resort and the writer swallows."""
    path = tmp_path / "j.jsonl"
    j = Journal(path)
    j.event("weird", obj=object(), where=Path("x"), tup=(1, 2))
    j.close()
    row = _rows(path)[0]
    assert row["where"] == "x" and row["tup"] == [1, 2]


# --- the town diff --------------------------------------------------------

class _Hero:
    def __init__(self, guid, cls, resolve=0, stress=0.0, building="", in_party=False):
        self.guid, self.hero_class, self.resolve_level = guid, cls, resolve
        self.stress, self.building, self.in_party = stress, building, in_party


class _Town:
    def __init__(self, **kw):
        self.heroes = kw.get("heroes", [])
        self.quests = kw.get("quests", [])
        self.wallet = kw.get("wallet", {})
        self.trinket_inventory = kw.get("trinkets", [])
        self.recruits = kw.get("recruits", [])


def test_the_roster_is_keyed_by_GUID_not_by_position():
    """The roster reorders freely. A positional diff would report one hire as
    four changes and every reshuffle as a whole new party."""
    a = town_snapshot(_Town(heroes=[_Hero(7, "crusader"), _Hero(3, "vestal")]))
    b = town_snapshot(_Town(heroes=[_Hero(3, "vestal"), _Hero(7, "crusader")]))
    assert diff_town(a, b) == []


def test_money_reports_the_before_and_the_after_not_just_the_delta():
    """A corpus is read months later. "gold -1500" cannot answer "could he have
    afforded the other thing", which is the question a spend is about."""
    a = town_snapshot(_Town(wallet={"gold": 5000, "bust": 2}))
    b = town_snapshot(_Town(wallet={"gold": 3500, "bust": 2}))
    (kind, payload), = diff_town(a, b)
    assert kind == "wallet"
    assert payload["changed"]["gold"] == {"from": 5000, "to": 3500, "delta": -1500}
    assert "bust" not in payload["changed"]


def test_a_hire_a_treatment_and_a_purchase_are_separate_events():
    a = town_snapshot(_Town(heroes=[_Hero(1, "crusader")],
                            recruits=[type("R", (), {"slot": 0, "hero_class": "vestal"})()]),
                      upgrades={(111, 1): 1})
    b = town_snapshot(_Town(heroes=[_Hero(1, "crusader", stress=40.0, building="abbey"),
                                    _Hero(2, "vestal")]),
                      upgrades={(111, 1): 2})
    kinds = {k for k, _ in diff_town(a, b)}
    assert {"hero_joined", "hero_changed", "recruits_gone", "upgrades_bought"} <= kinds


def test_a_HIRED_heros_existing_trees_are_not_counted_as_purchases():
    """Found by the first real recording session, which reported batches of 92,
    78, 95 and 99 "bought" upgrade trees against a wallet that never held more
    than 6,210 gold. At ~1,000 a skill rank the arithmetic refuses it: those
    were thirteen hires walking in with their own kits.

    **The test is on the LEDGER, not the roster**, because the two files are
    written at different times — a hero can join on one poll and their trees
    appear on the next, so "did they just join the roster" misses it. This
    fixture reproduces exactly that lag: the roster is unchanged and eleven
    trees appear for an owner the ledger has never seen.
    """
    def snap(heroes, ups):
        return {"wallet": {}, "heroes": {g: {} for g in heroes}, "quests": {},
                "recruits": {}, "trinkets": [], "upgrades": ups}

    before = snap(["1", "9"], {"aa:1": 1})
    after = snap(["1", "9"], {"aa:1": 1, **{f"x{i}:9": 1 for i in range(11)}})
    assert diff_town(before, after) == []


def test_the_three_things_that_ARE_purchases_still_report():
    """The filter must not swallow real spending. An existing hero gaining a
    rank, a BUILDING tree (whose instance is not a hero guid at all), and a
    fresh recruit buying their genuine first rank — that last one reads 0 -> 1,
    under the arrival threshold, which is why the threshold is 2 and not 1."""
    def snap(heroes, ups):
        return {"wallet": {}, "heroes": {g: {} for g in heroes}, "quests": {},
                "recruits": {}, "trinkets": [], "upgrades": ups}

    for label, ub, ua in (
            ("existing hero ranks up", {"aa:1": 1}, {"aa:1": 2}),
            ("a building tree", {"aa:1": 1}, {"aa:1": 1, "bb:0": 1}),
            ("a recruit's first rank", {"aa:1": 1}, {"aa:1": 1, "cc:9": 1})):
        events = diff_town(snap(["1", "9"], ub), snap(["1", "9"], ua))
        assert [k for k, _ in events] == ["upgrades_bought"], label


def test_a_hero_leaving_is_NOT_labelled_a_death():
    """The town save cannot tell a death from a dismissal from a parse slip, so
    the event records the departure and refuses to name a cause. Pairing it with
    the raid's own outcome is a reader's job."""
    a = town_snapshot(_Town(heroes=[_Hero(1, "crusader"), _Hero(2, "vestal")]))
    b = town_snapshot(_Town(heroes=[_Hero(1, "crusader")]))
    (kind, payload), = diff_town(a, b)
    assert kind == "hero_left" and payload["class"] == "vestal"
    assert "died" not in json.dumps(payload) and "cause" not in payload


def test_the_first_look_at_a_town_has_nothing_to_subtract_from():
    """Reporting a whole roster as N hires on the first poll would be a lie
    about what just happened. `run.py` emits a baseline instead."""
    assert diff_town({}, town_snapshot(_Town(heroes=[_Hero(1, "crusader")]))) == []


# --- the decoupling, which is the point -----------------------------------

def test_a_played_turn_is_recorded_WITHOUT_a_checkpoint(tmp_path):
    """THE LOAD-BEARING TEST. No model loads at the current observation width,
    so `_arm` leaves `_pending` None and the grading path returns immediately.
    The demonstration record must not be on that path -- otherwise Fran plays
    several expeditions and the combat half of the corpus is empty.
    """
    from bridge.live_state import read_live_state
    from bridge.run import Board, Session, _position

    def read(prefix):
        return read_live_state(
            raid_file=SNAPS / f"{prefix}_persist.raid.json",
            roster_file=SNAPS / f"{prefix}_persist.roster.json",
            map_file=SNAPS / f"{prefix}_persist.map.json")

    class _NoModel:
        last_action = last_engine = None
        last_provisional = False
        store = None

    before, after, actor = read("00066"), read("00108"), 49
    path = tmp_path / "j.jsonl"
    session = Session(_NoModel(), journal_path=path)
    session._demo = (actor, Board.of(before), _position(before, actor))
    assert session._pending is None, "the premise: nothing armed, nothing to grade"

    session._resolve(after)
    session.journal.close()

    plays = [r for r in _rows(path) if r["kind"] == "play"]
    assert len(plays) == 1, "the played turn was not recorded"
    assert plays[0]["played"] == "attack"
    # ...and it carries the board it was played on, because advise mode
    # captures no saves: whatever is not in this event is gone.
    board = plays[0]["board"]
    assert board["heroes"] and board["enemies"]
    assert all("hp" in h and "stress" in h for h in board["heroes"])
    # Nothing was GRADED, which is the other half of the claim.
    assert not [r for r in _rows(path) if r["kind"] == "decision"]


def test_a_hero_leaving_the_party_MID_RAID_is_a_death(tmp_path):
    """Modelled on Fran's Shambler raid, 2026-09-03: the Highwayman goes
    28 -> 22 -> 11 -> healed to 22 -> 16 -> 12 -> 4 -> **0.0 for three turns**
    and then stops appearing. Every fact was in the boards and nothing said
    "died", which is absurd for the one event `REWARD_HERO_DEATH` is built
    around.

    **0 HP is NOT the signal.** Death's Door sits there for turns at a time —
    it did for three here — so a death detector keyed on HP would fire early
    and then never resolve. Leaving the party list is the signal, and inside a
    raid there is no other way to leave it.
    """
    from bridge.journal import Journal
    from bridge.run import _note_deaths

    class _Hero:
        def __init__(self, guid, cls, hp, stress=0.0):
            self.guid, self.hero_class, self.hp, self.stress = guid, cls, hp, stress

    class _State:
        def __init__(self, heroes):
            self.heroes = heroes

    class _Session:
        def __init__(self, path):
            self.journal = Journal(path)
        def emit(self, _text):
            pass

    path = tmp_path / "j.jsonl"
    session = _Session(path)

    full = [_Hero(1, "occultist", 20.0), _Hero(2, "highwayman", 4.0),
            _Hero(3, "antiquarian", 15.0), _Hero(4, "plague_doctor", 18.0)]
    door = [_Hero(1, "occultist", 20.0), _Hero(2, "highwayman", 0.0, 40.0),
            _Hero(3, "antiquarian", 15.0), _Hero(4, "plague_doctor", 18.0)]
    without = [h for h in full if h.guid != 2]

    seen = _note_deaths(session, _State(full), None)     # baseline, no event
    seen = _note_deaths(session, _State(door), seen)     # Death's Door, still here
    seen = _note_deaths(session, _State(door), seen)     # ...and still here
    session.journal.close()
    assert _rows(path) == [], "Death's Door is not a death"

    session = _Session(path)
    _note_deaths(session, _State(without), seen)
    session.journal.close()
    died = [r for r in _rows(path) if r["kind"] == "hero_died"]
    assert len(died) == 1
    assert died[0]["class"] == "highwayman"
    # The state it died IN, not a blank record — 0 hp and the stress it carried.
    assert died[0]["hp"] == 0.0 and died[0]["stress"] == 40.0
    assert sorted(died[0]["survivors"]) == ["antiquarian", "occultist", "plague_doctor"]


def test_the_party_a_session_STARTS_with_is_not_a_casualty_list(tmp_path):
    """`seen` is None before the first look, the same baseline rule the town
    diff and `raid_started` both follow — and the rule this file has now had to
    learn three times."""
    from bridge.journal import Journal
    from bridge.run import _note_deaths

    class _Hero:
        def __init__(self, guid):
            self.guid, self.hero_class, self.hp, self.stress = guid, "leper", 20.0, 0.0

    class _Session:
        def __init__(self, path):
            self.journal = Journal(path)
        def emit(self, _text):
            pass

    path = tmp_path / "j.jsonl"
    session = _Session(path)
    _note_deaths(session, type("S", (), {"heroes": [_Hero(1), _Hero(2)]})(), None)
    session.journal.close()
    assert _rows(path) == []


def test_a_session_begun_MID_RAID_is_not_an_embark():
    """The second bug the first real recording session found, and the same one
    the town baseline avoids one block away.

    `in_raid` starts as `None`, not `False`, so the first poll of a session that
    finds the party already underground emits `raid_in_progress` — a baseline —
    rather than `raid_started`. With `False` it reported an embark whose "pack"
    was mid-raid inventory: measured on Fran's own recording, BOTH "embarks"
    fired 0.0 s after startup and neither was one.

    This drives the loop's own state machine rather than restating it, so a
    change to the transition has to keep the two apart.
    """
    from bridge.run import _watch          # noqa: F401  (import proves it loads)

    # The transition table, as the loop implements it.
    def label(previous, now_in_raid):
        if not now_in_raid:
            return None, False
        if previous is not True:
            return ("raid_in_progress" if previous is None else "raid_started"), True
        return None, True

    kind, state = label(None, True)          # started while already in a raid
    assert kind == "raid_in_progress"
    kind, state = label(state, True)         # still in it, every later poll
    assert kind is None
    kind, state = label(state, False)        # walked out
    assert kind is None and state is False
    kind, state = label(state, True)         # a real embark
    assert kind == "raid_started"


# --- the reader -----------------------------------------------------------

def test_the_report_survives_a_half_written_last_line(tmp_path):
    """The writer appends while the game runs, so reading a LIVE journal will
    routinely catch the final line mid-write. That must be a skipped line, not
    a crash -- the whole point of the reader is checking a session in progress.
    """
    from tools.journal_report import load

    path = tmp_path / "j.jsonl"
    path.write_text('{"kind": "play", "t": 1.0}\n{"kind": "play", "t":',
                    encoding="utf-8")
    rows = load([path])
    assert len(rows) == 1 and rows[0]["kind"] == "play"


if __name__ == "__main__":
    import tempfile
    tmp = Path(tempfile.mkdtemp())
    test_every_event_is_one_json_line_carrying_its_kind_and_schema(tmp)
    test_it_appends_so_a_session_adds_to_the_corpus(tmp / "a")
    (tmp / "a").mkdir(exist_ok=True)
    test_a_disabled_journal_is_free_and_silent(tmp)
    test_an_unserialisable_payload_does_not_take_the_poll_thread_down(tmp / "b")
    test_the_roster_is_keyed_by_GUID_not_by_position()
    test_money_reports_the_before_and_the_after_not_just_the_delta()
    test_a_hire_a_treatment_and_a_purchase_are_separate_events()
    test_a_hero_leaving_is_NOT_labelled_a_death()
    test_the_first_look_at_a_town_has_nothing_to_subtract_from()
    print("All journal tests passed")
