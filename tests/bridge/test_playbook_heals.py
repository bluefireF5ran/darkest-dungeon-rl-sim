"""PLAYBOOK 3.2 applied to the advisor's shortlist.

> *"3.2 Curar -- Sólo en situaciones extremas o en stall. En el resto, jugar
> proactivo."*

Fran ruled on 2026-09-20, having been shown `tools/check_playbook.py` on the
shipped checkpoint: **35.5% of 299 heals** are neither Death's Door, nor under
34% HP, nor stall -- 106 turns, 4.3% of every hero turn -- scoring **-0.0820**
against **+0.1021** for a justified one. The reward separates them and the
policy plays the negative one anyway, so no reward knob reaches it.

**The criteria are read from `PLAYBOOK.md`, not imported from the ruler.**
`tests/suite/test_rulers.py` forbids `bridge/` importing anything under
`tools/`, and it would be the wrong shape anyway: a ruler that the thing it
grades is built from has stopped being independent. The 34% is in the spec's
own text.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


class _Hero:
    def __init__(self, position, hp, max_hp=40, deaths_door=False,
                 name="Highwayman"):
        self.position = position
        self.hp = hp
        self.max_hp = max_hp
        self.is_deaths_door = deaths_door
        self.name = name

    def is_alive(self):
        return True


class _Skill:
    def __init__(self, heal_min=None):
        self.heal_min = heal_min


class _Engine:
    """Only the four things the 3.2 predicates touch."""

    def __init__(self, heroes, ally_skills=(), heals=()):
        self.heroes = list(heroes)
        self._ally = set(ally_skills)
        self._heals = set(heals)

    def _is_ally_skill(self, sid):
        return sid in self._ally

    def get_skill_data(self, sid, level=4):
        if sid not in self._ally:
            return _Skill()
        return _Skill(heal_min=3 if sid in self._heals else None)


class _Action:
    def __init__(self, skill_id, target_id=None):
        self.skill_id = skill_id
        self.target_id = target_id


def _engine():
    return _Engine([_Hero(0, 40), _Hero(1, 10), _Hero(2, 40, deaths_door=True)],
                   ally_skills={"divine_comfort", "battle_ballad"},
                   heals={"divine_comfort"})


# -- what counts as a heal ---------------------------------------------------

def test_a_STRESS_heal_is_not_a_heal_for_3_2():
    """3.2 is about HP. 3.3 names stress relief as a thing to spend a secured
    turn ON, so filtering it here would have the two rules contradicting."""
    from bridge.advisor import is_a_heal

    eng = _engine()
    assert is_a_heal(eng, _Action("divine_comfort", 0)) is True
    assert is_a_heal(eng, _Action("battle_ballad", 0)) is False, "a buff"
    assert is_a_heal(eng, _Action("wicked_slice", 1)) is False, "an attack"
    assert is_a_heal(eng, _Action("__pass__")) is False
    assert is_a_heal(eng, _Action("__move__")) is False


# -- the three justifications ------------------------------------------------

def test_DEATHS_DOOR_and_critical_HP_and_stall_each_justify_a_heal():
    from bridge.advisor import CRITICAL_HP_FRACTION, heal_is_justified

    eng = _engine()
    assert CRITICAL_HP_FRACTION == pytest.approx(0.34), (
        "the threshold is PLAYBOOK 3.2's own: 'HP crítico (<34%)'")

    ok, why = heal_is_justified(eng, _Action("divine_comfort", 2), False)
    assert ok and "Death's Door" in why

    ok, why = heal_is_justified(eng, _Action("divine_comfort", 1), False)
    assert ok and "25% HP" in why, why

    ok, why = heal_is_justified(eng, _Action("divine_comfort", 0), True)
    assert ok and "stall" in why


def test_a_healthy_target_outside_a_stall_is_the_antipattern():
    """The case the PLAYBOOK calls INCUMPLIDA, in its own words: *"la mitad de
    las curaciones caen sobre un héroe por encima del 34% de vida, fuera de
    stall y sin estar en la puerta de la muerte"*."""
    from bridge.advisor import heal_is_justified

    ok, why = heal_is_justified(_engine(), _Action("divine_comfort", 0), False)
    assert ok is False
    assert "100% HP" in why and "not a stall" in why


def test_a_target_it_cannot_SEE_is_left_alone():
    """Refusing what you cannot identify turns a seeding gap into a worse
    turn. The filter may only ever fire on a case it can name."""
    from bridge.advisor import heal_is_justified

    eng = _engine()
    assert heal_is_justified(eng, _Action("divine_comfort", None), False)[0]
    assert heal_is_justified(eng, _Action("divine_comfort", 9), False)[0]


# -- the shortlist rewrite ---------------------------------------------------

class _Fake:
    """Enough of an `Advisor` to run `_apply_playbook_32` unbound."""

    def __init__(self, action_map, stall=False, on=True):
        self.playbook_heals = on

        class _Env:
            _action_map = action_map

            @staticmethod
            def is_recovery_phase():
                return stall

        self.env = _Env()

    @staticmethod
    def describe(engine, action):
        return f"{action.skill_id} on {action.target_id}"


def _rewrite(action_map, order, stall=False, on=True):
    from bridge.advisor import Advisor

    return Advisor._apply_playbook_32(_Fake(action_map, stall, on),
                                      _engine(), order)


def test_an_unjustified_heal_is_DEMOTED_and_the_note_names_it():
    amap = {0: _Action("divine_comfort", 0),     # 100% HP, no stall
            1: _Action("wicked_slice", 1),
            2: _Action("battle_ballad", 0)}
    order, note = _rewrite(amap, [0, 1, 2])
    assert order == [1, 2, 0], "the heal must go last, not be dropped"
    assert "divine_comfort" in note and "demoted" in note


def test_it_is_NEVER_dropped_only_moved():
    """A hero can be in a position where every legal action is an unjustified
    heal -- a back-rank healer whose attack cannot reach, a skill list with
    nothing else. Dropping them leaves the actuator with nothing to send,
    which is a worse failure than a wasted heal."""
    amap = {0: _Action("divine_comfort", 0), 1: _Action("divine_comfort", 0)}
    order, note = _rewrite(amap, [0, 1])
    assert order == [0, 1], "the policy's own order has to survive"
    assert "every legal move is an unjustified heal" in note


def test_the_NOTE_only_speaks_when_the_move_actually_changed():
    """Demoting an ALTERNATIVE changes nothing that gets sent, and saying
    "demoted" for it reads as an override that did not happen. On the first
    replay 11 fires printed a line and only 3 moved the head; a log that
    overstates what the bridge did stops being evidence."""
    amap = {0: _Action("wicked_slice", 1),       # the top pick, unaffected
            1: _Action("divine_comfort", 0)}     # an alt, and unjustified
    order, note = _rewrite(amap, [0, 1])
    assert order == [0, 1]
    assert note == "", f"it claimed an override that did not happen: {note!r}"


def test_a_JUSTIFIED_heal_at_the_top_is_left_where_it_is():
    amap = {0: _Action("divine_comfort", 2),     # Death's Door
            1: _Action("wicked_slice", 1)}
    order, note = _rewrite(amap, [0, 1])
    assert order == [0, 1] and note == ""


def test_the_flag_turns_the_whole_thing_OFF():
    """`--no-playbook-heals` is there so the next person to measure this can
    get the unfiltered arm back without editing code -- which is how the
    3-of-56 A/B on the capture corpus was taken."""
    amap = {0: _Action("divine_comfort", 0), 1: _Action("wicked_slice", 1)}
    order, note = _rewrite(amap, [0, 1], on=False)
    assert order == [0, 1] and note == ""


def test_a_stall_suspends_the_whole_filter():
    amap = {0: _Action("divine_comfort", 0), 1: _Action("wicked_slice", 1)}
    order, note = _rewrite(amap, [0, 1], stall=True)
    assert order == [0, 1] and note == ""


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
