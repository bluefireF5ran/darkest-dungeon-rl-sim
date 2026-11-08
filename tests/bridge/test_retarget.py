"""`ActRunner._retarget_to_a_legal_hero` -- ask the screen who a skill can reach.

2026-09-23: a Musketeer's heal went at a Bounty Hunter the game did not
bracket as a target; the click did nothing and ten minutes went on heroes the
game was not asking, while her self-heal was legal the whole time.
"""
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from bridge import act                                   # noqa: E402
from bridge.actuation.plan import InputPlan, Step        # noqa: E402


@dataclass
class _Alt:
    skill_slot: int
    side: str
    rank: int
    headline: str = ""
    continues_turn: bool = False


@dataclass
class _Update:
    skill_slot: int = 4
    alternatives: tuple = ()


class _Res:
    @staticmethod
    def point(side, i):
        return (100 * (i + 1), 373)


@dataclass
class _Act:
    sent: list = field(default_factory=list)

    def execute(self, plan):
        self.sent.append(tuple(s.label for s in plan.steps))
        return True


def _runner(monkeypatch, legal):
    r = object.__new__(act.ActRunner)
    r.armed = True
    r.resolved = _Res()
    r.actuator = _Act()
    r._say = lambda _s: None
    monkeypatch.setattr(act.time, "sleep", lambda _s: None)
    monkeypatch.setattr(act.ActRunner, "_legal_hero_ranks", lambda self: legal)
    return r


def _heal(rank):
    return InputPlan((Step("key", vk=0x34, label="select slot 4"),
                      Step("click", screen=(100 * rank, 373),
                           label=f"hero rank {rank}")), "battle", "PATCH UP")


def test_an_illegal_target_is_swapped_for_a_legal_alternative(monkeypatch):
    r = _runner(monkeypatch, {1, 3, 4})
    up = _Update(alternatives=(_Alt(4, "hero", 0), _Alt(4, "hero", 3)))
    got = r._retarget_to_a_legal_hero(up, _heal(2), bar=None)
    assert r.actuator.sent == [("select slot 4",)]
    assert [s.label for s in got.steps] == ["hero rank 1"]


def test_a_legal_target_is_left_alone(monkeypatch):
    r = _runner(monkeypatch, {1, 2})
    got = r._retarget_to_a_legal_hero(_Update(), _heal(2), bar=None)
    assert [s.label for s in got.steps] == ["hero rank 2"]


def test_an_unreadable_screen_changes_nothing_but_the_split(monkeypatch):
    r = _runner(monkeypatch, None)
    got = r._retarget_to_a_legal_hero(_Update(), _heal(2), bar=None)
    assert [s.label for s in got.steps] == ["hero rank 2"]
