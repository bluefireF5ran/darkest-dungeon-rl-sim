"""`tools/overnight.py` -- the unattended loop's own reading of the save.

Only the profile resolution, which is the part that decided a whole town
visit against the wrong campaign. Everything else in that file needs a
running game.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def _night(monkeypatch, *, resume, slot=1, live="profile_8", exists=True):
    """An `Overnight` with nothing constructed and a fake live profile.

    `__init__` makes directories and stamps a run id, and `_profile` reads
    none of that -- so build it the way `tests/bridge/test_act.py` builds a bare
    runner rather than paying for a real one.
    """
    import tools.overnight as overnight

    said = []
    n = object.__new__(overnight.Overnight)
    n.slot = slot
    n.resume = resume
    n.say = said.append
    n._resumed_profile = None

    root = Path("D:/steam/userdata/1/262060/remote")
    monkeypatch.setattr("bridge.live_state.find_profile",
                        lambda *_a, **_k: root / live)
    monkeypatch.setattr(Path, "exists", lambda self: exists)
    return n, said


def test_a_claimed_night_reads_the_slot_it_owns(monkeypatch):
    """The ordinary path is unchanged: a night that CREATED the campaign
    reads the slot `reset.claim_slot` handed it, whatever else is on disk."""
    n, _said = _night(monkeypatch, resume=False, slot=1, live="profile_8")
    assert n._profile().name == "profile_1"


def test_a_RESUMED_night_reads_the_campaign_ON_SCREEN(monkeypatch):
    """The bug, in one line.

    Resume plays whatever is loaded and claims nothing, so pinning the read
    to `--slot` answered questions about a save nobody was looking at.
    Measured 2026-09-06: raid played in `profile_8`, town read out of
    `profile_1`, and the embark refused because that other campaign had two
    heroes while the one on screen had nine.
    """
    n, said = _night(monkeypatch, resume=True, slot=1, live="profile_8")
    assert n._profile().name == "profile_8"
    assert any("profile_8" in s for s in said), said


def test_the_resumed_profile_is_announced_ONCE(monkeypatch):
    """It is polled per town visit and the log is meant to be tailed at 3am."""
    n, said = _night(monkeypatch, resume=True, live="profile_8")
    for _ in range(5):
        n._profile()
    assert sum("resuming the campaign" in s for s in said) == 1, said


def test_a_resumed_night_REFUSES_profile_0(monkeypatch):
    """profile_0 is Fran's own campaign and the bridge never drives it.

    Resume takes the live profile rather than a slot it owns, so this is the
    one place that guarantee has to be spelled out instead of falling out of
    `claim_slot`.
    """
    n, said = _night(monkeypatch, resume=True, live="profile_0")
    assert n._profile() is None
    assert any("profile_0" in s and "never drives it" in s for s in said), said


def test_a_CLAIMED_night_pointed_at_slot_0_is_not_this_guard(monkeypatch):
    """Deliberately narrow: the refusal is about what resume INFERS, not
    about what an operator asked for. `--slot 0` is refused far upstream by
    `reset.claim_slot`, which will not adopt a campaign it cannot prove it
    created, and duplicating that here would give two places to keep in
    step."""
    n, _said = _night(monkeypatch, resume=False, slot=0, live="profile_8")
    assert n._profile().name == "profile_0"


def test_no_campaign_on_disk_is_a_refusal_not_a_crash(monkeypatch):
    """`find_profile` raises SystemExit when there is nothing to read, and a
    night that cannot find a save still has to write its summary."""
    import tools.overnight as overnight

    def _boom(*_a, **_k):
        raise SystemExit("no save profile")

    n = object.__new__(overnight.Overnight)
    n.slot, n.resume, n.say = 1, True, lambda *_a: None
    n._resumed_profile = None
    monkeypatch.setattr("bridge.live_state.find_profile", _boom)
    assert n._profile() is None


def test_a_slot_directory_that_does_not_exist_is_None(monkeypatch):
    """A claimed night whose slot was deleted underneath it reads nothing
    rather than handing a missing path to `read_town_state` -- which,
    given None, silently resolves the most recently touched campaign
    instead."""
    n, _said = _night(monkeypatch, resume=False, slot=3, exists=False)
    assert n._profile() is None


if __name__ == "__main__":                                  # pragma: no cover
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            print(name)
            pytest.main([__file__, "-k", name, "-q"])
