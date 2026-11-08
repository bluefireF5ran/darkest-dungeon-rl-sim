"""`tools/overnight.py` -- the unattended loop's own reading of the save.

Only the profile resolution, which is the part that decided a whole town
visit against the wrong campaign. Everything else in that file needs a
running game.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def _night(monkeypatch, *, resume, slot=1, live="profile_8", exists=True,
           play_profile_0=False):
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
    n.play_profile_0 = play_profile_0
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


def test_the_profile_0_refusal_is_LIFTED_by_the_flag(monkeypatch):
    """**Fran handed profile_0 over on 2026-09-21** to be continued rather
    than restarted: *"I've provided a save on the first possible profile ...
    lets stop restarting the save as the tutorial and the first mission on
    each zone is scripted so we wont have the versatility on our model by
    repeating those"*.

    It stays an OPT-IN rather than a deleted rule, so the guarantee still
    holds for every run that was not told to do this.
    """
    n, said = _night(monkeypatch, resume=True, live="profile_0",
                     play_profile_0=True)
    assert n._profile().name == "profile_0"
    assert not any("never drives it" in s for s in said), said


def test_the_flag_does_nothing_for_any_OTHER_profile(monkeypatch):
    """It lifts one specific refusal; it is not a mode."""
    n, _said = _night(monkeypatch, resume=True, live="profile_8",
                      play_profile_0=True)
    assert n._profile().name == "profile_8"

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


# -- the full pack, and the escape that could not be reached ----------------

def test_the_full_pack_policy_REACHES_run_py(monkeypatch, tmp_path):
    """**`run.py` has had `--full-pack abandon` all along and this layer never
    forwarded it**, so the documented escape was unreachable from an
    unattended night. Measured 2026-09-21 on Fran's campaign: pack 16/16,
    "nothing in the pack is free to discard", six room plans in a row and not
    one input sent, because the default policy waits for a human to empty a
    slot by hand.
    """
    import tools.overnight as overnight

    n = object.__new__(overnight.Overnight)
    n.say = lambda *_a, **_k: None
    n.dry = True
    n.model = "m.zip"
    n.full_pack = "abandon"
    n.survival = 12
    seen = {}

    class _Proc:
        def poll(self):
            return 0

        def wait(self, timeout=None):
            return 0

    def _popen(cmd, **_kw):
        seen["cmd"] = list(cmd)
        return _Proc()

    monkeypatch.setattr(overnight.subprocess, "Popen", _popen)
    monkeypatch.setattr(overnight.time, "sleep", lambda *_a: None)
    log = tmp_path / "iter.log"
    log.write_text("", encoding="utf-8")
    it = overnight.Iteration(n=1, started="now")
    try:
        n._play(log, it)
    except Exception:                       # noqa: BLE001
        pass                                # the watcher loop is not the point
    cmd = seen.get("cmd", [])
    assert "--full-pack" in cmd, cmd
    assert cmd[cmd.index("--full-pack") + 1] == "abandon", cmd
    # **And the survival layer, for the same reason.** `run.py --survival`
    # defaults to 0, which leaves `Advisor._rollout` None and no retreat
    # verdict is ever computed -- so the bridge cannot decide to leave a raid
    # at all. Measured 2026-09-21: four heroes down to one, on 3 HP at 157
    # stress, three fights still routed ahead, not one retreat line logged.
    assert "--survival" in cmd, cmd
    assert cmd[cmd.index("--survival") + 1] == "12", cmd


def test_play_profile_0_can_only_mean_CONTINUING_it(tmp_path, monkeypatch):
    """2026-09-23: `--play-profile-0` alone booted the new-campaign flow on
    slot 1 and walked into the scripted Old Road. The flag implies
    `--resume --slot 0`."""
    import tools.overnight as overnight
    monkeypatch.setattr(overnight, "LOG_DIR", tmp_path, raising=False)
    n = overnight.Overnight(model="m", max_iterations=1, until=None,
                            dry=True, play_profile_0=True, slot=1,
                            resume=False)
    assert (n.resume, n.slot) == (True, 0)


def test_iterations_that_achieve_nothing_stop_the_night(monkeypatch, tmp_path):
    """Live 2026-09-25: a stuck raid ended thirteen iterations in a row at
    189 s, 9 actions, no raid, no embark -- slower than INSTANT_SECONDS, so the
    instant guard never fired. The same failure repeating must stop the night
    whatever each attempt cost."""
    import tools.overnight as overnight
    from bridge import window as window_mod

    n = object.__new__(overnight.Overnight)
    said = []
    n.say = said.append
    n.dry = False
    n.run_id = "t"
    n.max_iterations = 40
    n.until = None
    n.iterations = []
    calls = []

    def fake_iterate(i):
        calls.append(i)
        return overnight.Iteration(n=i, seconds=189.0, actions=9,
                                   outcome="no-raid-end")

    monkeypatch.setattr(overnight, "steam_is_running", lambda: True)
    monkeypatch.setattr(window_mod, "keep_display_awake", lambda on=True: True)
    monkeypatch.setattr(overnight, "STOP_FILE", tmp_path / "STOP")
    monkeypatch.setattr(n, "_sweep_orphans", lambda: None, raising=False)
    monkeypatch.setattr(n, "iterate", fake_iterate, raising=False)
    monkeypatch.setattr(n, "_write_summary", lambda: None, raising=False)
    n.summary_path = tmp_path / "s.json"
    n.run()
    assert len(calls) == overnight.IDLE_FAILURES, calls
    assert any("one failure repeating" in s for s in said)



if __name__ == "__main__":                                  # pragma: no cover
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            print(name)
            pytest.main([__file__, "-k", name, "-q"])

