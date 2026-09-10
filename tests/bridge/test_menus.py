"""Booting the game: title -> a new campaign -> inside the Old Road.

Two rules here are load-bearing and neither is about clicking accurately.

**The save row comes from disk, not from pixels.** DD puts the empty letter at
the first FREE slot index and renders rows in slot order, so the row to click
is derived from which `profile_N` directories exist. The rendered list scrolls;
the directory does not.

**Nothing in this module may ever click a delete X.** It sits 185 px from
Fran's real campaign (`DD_Model_1.2`, Week 22) and a wrong row destroys 22
weeks with no undo. `test_no_plan_can_ever_target_a_delete_button` is the
tripwire, and it works by exhaustion rather than by inspection.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from bridge import menus                                         # noqa: E402
from bridge.act import Actuator, DryRunBackend, VK_RETURN        # noqa: E402
from bridge.layout import (MENU_POINTS, SAVE_ROW_PITCH,          # noqa: E402
                           SAVE_ROW_Y0, VISIBLE_SAVE_ROWS, load as load_layout)

CAPTURES = Path(__file__).resolve().parents[2] / "captures"

SCREENS = ("cinematic", "message", "title", "save_select", "dlc", "modal",
           "difficulty", "loading")


class FakeWindow:
    """A 2560x1440 client at the origin, so a fraction is a capture pixel."""
    hwnd = 1
    client_size = (2560, 1440)
    client_origin = (0, 0)
    is_foreground = True

    def to_screen(self, fx, fy):
        return int(round(fx * 2560)), int(round(fy * 1440))

    def to_client(self, sx, sy):
        return sx, sy


@pytest.fixture
def resolved():
    lay = load_layout()
    assert lay is not None, "no overlay_layout.json"
    return lay.resolve(FakeWindow())


# --- the slot rule ---

def _profiles(tmp_path, *slots):
    """Real CAMPAIGN directories -- each carries `persist.game.json`.

    The marker is what separates a campaign from a `profile_N` that is
    something else: `profile_9` on the live machine is the Butcher's Circus
    multiplayer profile, and DD draws no row for it. A bare directory still
    consumes the slot for `first_free_slot`, which is why that stays a
    directory test while the row functions ask for the marker.
    """
    root = tmp_path / "u" / "262060" / "remote"
    root.mkdir(parents=True)
    for s in slots:
        (root / f"profile_{s}").mkdir()
        (root / f"profile_{s}" / menus.CAMPAIGN_MARKER).write_text("{}")
    return tmp_path


def test_the_free_slot_is_the_first_gap_not_the_next_number(tmp_path):
    """DD fills the lowest free index, so with 0 used and 8/9 used the letter
    is at 1 -- which is exactly what both captures show, taken on a machine
    holding profile_0, profile_8 and profile_9."""
    root = _profiles(tmp_path, 0, 8, 9)
    assert menus.first_free_slot(root, "*/262060/remote/profile_*") == 1


def test_an_empty_machine_starts_at_zero(tmp_path):
    root = _profiles(tmp_path)
    assert menus.first_free_slot(root, "*/262060/remote/profile_*") == 0


def test_contiguous_slots_push_the_letter_along(tmp_path):
    root = _profiles(tmp_path, 0, 1, 2)
    assert menus.first_free_slot(root, "*/262060/remote/profile_*") == 3


def test_a_non_numeric_profile_directory_is_ignored(tmp_path):
    root = _profiles(tmp_path, 0)
    (root / "u" / "262060" / "remote" / "profile_options").mkdir()
    assert menus.first_free_slot(root, "*/262060/remote/profile_*") == 1


# --- the row geometry ---

def test_the_row_grid_lands_inside_the_rows_of_both_captures(resolved):
    """Redrawn over the captures the constants came from, which is the same
    check the pack grid got. Row separators were detected at y = 955 and 1137,
    so each row spans 182 px and the click must sit inside its own band."""
    for slot in range(VISIBLE_SAVE_ROWS):
        x, y = resolved.save_row(slot)
        top = 955 + 182 * slot
        assert top < y < top + 182, (slot, y)
        assert 800 < x < 1570, x


def test_a_row_past_the_visible_list_is_None_not_a_guess(resolved):
    """The viewport holds two rows and the screen is black below 1340. A third
    slot needs the scrollbar, which nothing here drives -- so it must refuse
    rather than click into the dark."""
    assert resolved.save_row(VISIBLE_SAVE_ROWS) is None
    assert resolved.save_row(-1) is None
    assert SAVE_ROW_Y0 + SAVE_ROW_PITCH * VISIBLE_SAVE_ROWS > 1319 / 1440


# --- the plan table ---

def test_every_recognised_screen_has_a_plan(resolved):
    for name in SCREENS:
        plan = menus.plan_for(name, resolved, slot=0)
        assert plan.actionable, f"{name} produced no plan"
        assert plan.steps


def test_an_unmodelled_screen_refuses_and_names_itself(resolved):
    plan = menus.plan_for("inventory_screen", resolved, slot=0)
    assert not plan.actionable
    assert "inventory_screen" in plan.refusal


def test_the_difficulty_screen_clicks_then_presses_enter(resolved):
    """DD has no OK button there: the estate-name field takes RETURN, and the
    default it holds is the campaign type -- which is why the created save is
    called "Darkest" in `00_7_After_creating_save.png`."""
    plan = menus.plan_for("difficulty", resolved, slot=0)
    kinds = [(s.kind, s.vk) for s in plan.steps]
    assert kinds == [("click", 0), ("key", VK_RETURN)]


def test_the_enter_waits_for_the_selection_to_register(resolved):
    """The two steps are NOT independent: the click selects and the RETURN
    confirms. At the default 80ms gap the RETURN arrived first -- the same
    plan went through on one live run and left the screen sitting on the next,
    which is what a race looks like rather than a wrong coordinate."""
    click, enter = menus.plan_for("difficulty", resolved, slot=0).steps
    assert click.after == menus.SELECT_SETTLE >= 0.5
    assert enter.after == 0.0, "nothing follows it, so nothing to wait for"


def test_no_other_plan_pays_that_pause(resolved):
    """A settle is a claim that one step depends on another. Every other menu
    plan is a single click, so any `after` there would be superstition."""
    for name in SCREENS:
        if name == "difficulty":
            continue
        for step in menus.plan_for(name, resolved, slot=0).steps:
            assert step.after == 0.0, name


def test_start_campaign_reuses_the_loot_modal_button(resolved):
    """1262,779 on the Crimson Court warning is `MODAL_OK` to the pixel -- DD
    centres modal buttons the same way whatever the modal says, so no new
    constant was invented for it."""
    from bridge.layout import MODAL_OK
    assert menus.plan_for("modal", resolved, slot=0).steps[0].screen == \
        resolved.modal_ok == FakeWindow().to_screen(*MODAL_OK)


def test_save_select_clicks_a_visible_row_directly(resolved):
    for slot in (0, 1):
        plan = menus.plan_for("save_select", resolved, slot=slot,
                              total_rows=9)
        assert plan.actionable
        assert plan.steps[0].label == f"save row {slot}"


def test_a_slot_past_the_fold_is_SCROLLED_TO_rather_than_refused(resolved):
    """`scroll_plan` and `MENU_SAVE_SCROLL_DOWN` were both written, tested and
    never reached from `plan_for`, so every campaign below the second row was
    unloadable -- which on this machine is the only campaign the bridge owns.

    `profile_8` is row 8 of a nine-row list two rows tall: four down-clicks
    take the offset to seven and land it on visible row 1.
    """
    seen = []
    for scrolled in range(5):
        plan = menus.plan_for("save_select", resolved, slot=8,
                              scrolled=scrolled, total_rows=9)
        assert plan.actionable, plan.refusal
        seen.append(plan.steps[0].label)
    assert seen == ["scroll the save list"] * 4 + ["save row 8"]


def test_the_scroll_is_ONE_click_per_plan(resolved):
    """The list animates, and a plan that sends four clicks blind cannot be
    stopped halfway by the kill switch or checked against what the rows then
    say."""
    plan = menus.plan_for("save_select", resolved, slot=8, total_rows=9)
    assert len(plan.steps) == 1
    assert "1 of 4" in plan.why


def test_a_slot_that_is_not_on_the_list_still_refuses(resolved):
    """Scrolling reaches a row that exists. A slot past the end of the list is
    not a scroll away, it is nowhere, and guessing a y below the viewport is
    how a click lands on whatever DD draws under it."""
    plan = menus.plan_for("save_select", resolved, slot=12, total_rows=9)
    assert not plan.actionable
    assert "not on a list" in plan.refusal


def test_the_same_click_serves_both_visits_to_the_save_list(resolved):
    """Row `slot` holds the empty letter before the campaign exists and the
    new campaign after, so the flow needs no 'have I created it yet' flag --
    the screen-change wait separates the two visits."""
    a = menus.plan_for("save_select", resolved, slot=1, total_rows=9)
    b = menus.plan_for("save_select", resolved, slot=1, total_rows=9)
    assert a.steps[0].screen == b.steps[0].screen


def test_the_intro_screens_click_only_the_dead_zone(resolved):
    """Verified black (max channel <= 9) on all nine captures, which is what
    makes it safe to poke an animation along without knowing what is up."""
    dead = resolved.menu_point("dead_zone")
    for name in ("cinematic", "message"):
        assert menus.plan_for(name, resolved, slot=0).steps[0].screen == dead


#: The save list's red X column, at 2560 wide, with room either side. Each
#: row's X is 182 px below the last, so the hazard is the COLUMN and not one
#: point -- a stray y anywhere in it deletes a campaign.
DELETE_COLUMN = (1800, 2050)


def test_no_plan_can_ever_target_a_delete_button(resolved):
    """By exhaustion, not by inspection.

    **The bound used to be a flat `x < 1800` and it is now the column itself**,
    because the scrollbar is legitimately to the RIGHT of the X at x 2156 --
    221 px clear of it -- and the flat bound would have to be loosened to let
    the list scroll at all. Naming the hazard is stricter than moving the
    fence: the old rule also permitted every x below 1800, and this one still
    does while refusing the band that actually deletes something.
    """
    for name in SCREENS:
        for slot in range(-2, 12):
            for scrolled in (0, 4):
                plan = menus.plan_for(name, resolved, slot=slot,
                                      scrolled=scrolled, total_rows=9)
                for step in plan.steps:
                    if step.kind == "click":
                        assert not (DELETE_COLUMN[0] <= step.screen[0]
                                    <= DELETE_COLUMN[1]), (name, slot, step)


def test_the_scroll_arrow_is_clear_of_the_delete_column(resolved):
    """The one point this module clicks to the right of the rows. Measured off
    `savelist.png` and confirmed by driving it; this is what says it has not
    drifted into the X."""
    pt = resolved.menu_point("save_scroll_down")
    assert pt is not None
    assert pt[0] > DELETE_COLUMN[1], pt


def test_every_named_point_is_inside_the_client_rect(resolved):
    for name in MENU_POINTS:
        x, y = resolved.menu_point(name)
        assert 0 < x < 2560 and 0 < y < 1440, name


# --- the state machine ---

@pytest.fixture(autouse=True)
def _pin_the_save_list(monkeypatch):
    """How many rows the save list draws is NOT a property of these tests.

    `menus.total_save_rows()` counts the campaigns in the real save directory,
    so `plan_for("save_select", slot=1)` with no `total_rows` silently asked
    the developer's own disk -- and three flow tests passed only because there
    happened to be two campaigns on it. Deleting the saves to start a fresh
    campaign, which is the ordinary thing to do here, turned them red with a
    refusal ("slot 1 is not on a list of 1 row(s)") that says nothing about
    the flow being tested.

    The tests that are genuinely ABOUT the row count build a profile tree
    under `tmp_path` and pass it explicitly, so ONLY the default root is
    pinned -- replacing the function outright would have hidden a regression
    in it, and immediately did.
    """
    real = menus.total_save_rows

    def pinned(save_root=menus.SAVE_ROOT, glob=menus.PROFILE_GLOB):
        if save_root == menus.SAVE_ROOT:
            return 9
        return real(save_root, glob)

    monkeypatch.setattr(menus, "total_save_rows", pinned)


def _runner(seq, *, armed=True, slot=1, then=None):
    """A BootRunner whose eyes return `seq` in order, then `then` forever.

    `then` defaults to the last entry, which is what a screen that stopped
    responding looks like; pass `""` to end a scripted flow instead.
    """
    lay = load_layout()
    win = FakeWindow()
    r = menus.BootRunner(window=win, resolved=lay.resolve(win),
                         actuator=Actuator(backend=DryRunBackend(), enabled=armed),
                         slot=slot, armed=armed)
    seen = list(seq)
    tail = seq[-1] if then is None else then
    r._look = lambda: ((seen.pop(0) if seen else tail), [])
    r._await_change = lambda was, **_kw: None
    r._focused = lambda: True
    r._nudge = lambda: None
    return r


def test_the_whole_flow_runs_in_order(monkeypatch):
    """The sequence Fran wrote down, minus the clicks he said to ignore.

    Note `save_select` twice with no state flag between them, and the estate
    name never typed -- both are properties of the flow, not simplifications.
    """
    monkeypatch.setattr(menus.applog, "count", lambda *_a, **_k: 0)
    monkeypatch.setattr(menus, "UNKNOWN_GRACE", 0.0)
    r = _runner(["title", "save_select", "dlc", "modal", "difficulty",
                 "save_select", "loading"], then="")
    r.run(timeout=5)
    assert r.seen == ["title", "save_select", "dlc", "modal", "difficulty",
                      "save_select", "loading"]


def test_it_stops_the_moment_app_log_says_the_raid_is_running(monkeypatch):
    """The handoff, and the only success condition. A screen recogniser can be
    fooled; `as_raid_start_done_loading` cannot."""
    calls = {"n": 0}

    def count(needle, *a, **k):
        calls["n"] += 1
        return 1 if calls["n"] > 3 else 0

    monkeypatch.setattr(menus.applog, "count", count)
    r = _runner(["title", "save_select", "dlc", "modal"])
    assert r.run(timeout=5) is True
    assert r.steps < 4


def test_an_unknown_screen_pauses_and_clicks_nothing(monkeypatch):
    monkeypatch.setattr(menus.applog, "count", lambda *_a, **_k: 0)
    monkeypatch.setattr(menus, "UNKNOWN_GRACE", 0.0)
    r = _runner([""])
    assert r.run(timeout=5) is False
    assert r.actuator.backend.sent == []


def test_a_screen_that_never_responds_pauses_instead_of_hammering(monkeypatch):
    """The failure the corridor loop kept hitting, in a new place: keep
    pressing and something eventually goes wrong somewhere unrelated."""
    monkeypatch.setattr(menus.applog, "count", lambda *_a, **_k: 0)
    r = _runner(["title"] * 20)
    assert r.run(timeout=5) is False
    assert r.steps <= menus.MAX_REPEAT


def test_a_refusal_stops_the_run_rather_than_skipping_it(monkeypatch):
    """A slot that is not on the list at all must end the run. Carrying on
    would leave the party staring at a list with no such row in it."""
    monkeypatch.setattr(menus.applog, "count", lambda *_a, **_k: 0)
    monkeypatch.setattr(menus, "total_save_rows", lambda *_a, **_k: 9)
    r = _runner(["save_select"], slot=40)
    assert r.run(timeout=5) is False
    assert r.actuator.backend.sent == []


def test_a_dry_run_sends_absolutely_nothing(monkeypatch):
    monkeypatch.setattr(menus.applog, "count", lambda *_a, **_k: 0)
    r = _runner(["title", "save_select", "dlc"], armed=False)
    r.run(timeout=1.5)
    assert r.actuator.backend.sent == []
    assert r.steps == 0


def test_a_dry_run_never_declares_itself_stuck(monkeypatch):
    """Reported live: `--dry` printed the title plan three times and paused
    with "did not respond to 3 attempts". Of course it did not respond -- a
    dry run sends nothing, so the screen cannot change on its own. The repeat
    guard was counting trips round the loop instead of clicks sent, which is
    the one thing it must not count."""
    monkeypatch.setattr(menus.applog, "count", lambda *_a, **_k: 0)
    monkeypatch.setattr(menus, "POLL", 0.01)
    r = _runner(["title"] * 200, armed=False)
    r.run(timeout=1.0)
    assert r.actuator.backend.sent == []
    assert "PAUSED" not in "".join(str(s) for s in r.seen)
    assert r.steps == 0


def test_one_screen_is_announced_once_not_once_per_poll(monkeypatch, capsys):
    """The same corollary: at POLL the dry run looks several times a second,
    and reprinting the plan each time buries the moment it actually changes."""
    monkeypatch.setattr(menus.applog, "count", lambda *_a, **_k: 0)
    monkeypatch.setattr(menus, "POLL", 0.01)
    r = _runner(["title"] * 100, armed=False)
    r.run(timeout=0.6)
    assert capsys.readouterr().out.count("[title]") == 1


def test_the_run_is_bounded_even_if_every_screen_keeps_changing(monkeypatch):
    monkeypatch.setattr(menus.applog, "count", lambda *_a, **_k: 0)
    # POLL is 0.6 s live and the Actuator paces each click on top of it. Left
    # real, this spent the full `timeout=10` asleep and the assertion could not
    # tell MAX_STEPS from the clock running out. Collapsing both is what makes
    # this test about the STEP bound, as its name claims.
    monkeypatch.setattr(menus, "POLL", 0.01)
    monkeypatch.setattr("time.sleep", lambda *_a: None)
    r = _runner(["title", "dlc"] * 40)
    r.run(timeout=10)
    assert r.steps <= menus.MAX_STEPS


def test_the_loading_screen_is_clicked_again_and_again(monkeypatch):
    """Reported live: "you need to wait till the game loads so needs to keep
    re-clicking". The Continue crest is inert until the raid has finished
    loading, so one click is a coin flip on load time -- and three is still a
    guess. Repeating is the mechanism there, not a symptom."""
    monkeypatch.setattr(menus.applog, "count", lambda *_a, **_k: 0)
    # Same as above: real POLL plus Actuator pacing made this a 10 s sleep
    # whose assertion could not distinguish the screen allowance from the
    # timeout. Now it can only pass by hitting the allowance.
    monkeypatch.setattr(menus, "POLL", 0.01)
    monkeypatch.setattr("time.sleep", lambda *_a: None)
    r = _runner(["loading"] * 60)
    r.run(timeout=10)
    clicks = len(r.actuator.backend.sent)
    assert clicks > menus.MAX_REPEAT, "gave up like an ordinary screen"
    assert clicks == menus.PATIENT_SCREENS["loading"], "and still bounded"


def test_the_global_budget_cannot_undercut_a_screen_allowance():
    """Two bounds that disagree about which is in force is one too many. A
    flat MAX_STEPS of 24 stopped the loading screen one click short of the 25
    it had just been granted, which reads as 'gave up' and is really 'the
    other limit fired'."""
    assert menus.MAX_STEPS > max(menus.PATIENT_SCREENS.values())


def test_no_other_screen_inherits_that_patience(monkeypatch):
    """A title screen that eats three clicks is broken and must say so; only
    the loading screen has a reason to be pressed twenty times."""
    monkeypatch.setattr(menus.applog, "count", lambda *_a, **_k: 0)
    r = _runner(["title"] * 60)
    r.run(timeout=10)
    assert len(r.actuator.backend.sent) == menus.MAX_REPEAT


def test_the_kill_switch_stops_it(monkeypatch):
    monkeypatch.setattr(menus.applog, "count", lambda *_a, **_k: 0)
    r = _runner(["title"] * 5)
    r.actuator.kill.tripped = True
    assert r.run(timeout=5) is False
    assert r.actuator.backend.sent == []


def test_the_nudge_never_fires_before_something_was_recognised():
    """An unknown screen at the very start is one this module has never seen,
    not a transition of its own making. Poking it would be exactly the blind
    click the rest of the bridge refuses to make."""
    r = _runner([""])
    r._nudge = menus.BootRunner._nudge.__get__(r)
    r.steps = 0
    r._nudge()
    assert r.actuator.backend.sent == []
    r.steps = 1
    r._nudge()
    assert len(r.actuator.backend.sent) == 1


# ── tearing a campaign DOWN (`bridge/reset.py`) ──

# The forward flow above never clicks a delete X. The reset does not either:
# it removes the directory by name. That trades a whole class of failure (one
# row off) for a narrower one (the wrong directory), and these are the proofs
# that the narrower one is closed.


def _fake_saves(tmp_path, slots):
    """A save root shaped like the real one: `<root>/<id>/262060/remote`."""
    remote = tmp_path / "312830650" / "262060" / "remote"
    remote.mkdir(parents=True)
    for n in slots:
        d = remote / f"profile_{n}"
        d.mkdir()
        (d / "persist.game.json").write_text("{}")
    return tmp_path, remote


def test_no_pre_existing_save_can_even_be_CLAIMED(tmp_path):
    """The safety argument is the TYPE, not a condition.

    `delete_owned` accepts only an `OwnedSlot`, and the only constructor is
    `claim_slot`, which refuses a directory that already exists. So a save
    that was on disk before this process started cannot be represented at all,
    and therefore cannot be handed to the delete -- rather than being caught
    by a check inside it that a later edit could weaken.

    By exhaustion over every occupied slot, which is how
    `test_no_plan_can_ever_target_a_delete_button` works and for the same
    reason: inspection is what missed this class of bug before.
    """
    from bridge import reset                                  # noqa: PLC0415
    root, _ = _fake_saves(tmp_path, [0, 1, 2, 8, 9])
    for slot in (0, 1, 2, 8, 9):
        with pytest.raises(reset.ResetRefused):
            reset.claim_slot(slot, save_root=root)


def test_a_free_slot_can_be_claimed_and_then_deleted(tmp_path):
    """The claim happens BEFORE the campaign exists, which is what makes the
    refusal above possible. The directory appearing afterwards is the normal
    case, not an exception."""
    from bridge import reset                                  # noqa: PLC0415
    root, remote = _fake_saves(tmp_path, [0, 8])
    owned = reset.claim_slot(1, save_root=root)
    assert owned.slot == 1 and owned.path.name == "profile_1"

    owned.path.mkdir()                                        # the game creates it
    (owned.path / "persist.game.json").write_text("{}")
    assert reset.delete_owned(owned, screen="title", save_root=root,
                              recheck=0) is True
    assert not owned.path.exists()
    # and nothing else was touched
    assert (remote / "profile_0").exists()
    assert (remote / "profile_8").exists()


def test_the_delete_refuses_anywhere_but_the_title_screen(tmp_path):
    """The campaign has to be UNLOADED. Deleting a profile the game still has
    open is how a half-written save survives the removal -- and every screen
    that is not the title is a screen where it might."""
    from bridge import reset                                  # noqa: PLC0415
    root, _ = _fake_saves(tmp_path, [0])
    owned = reset.claim_slot(1, save_root=root)
    owned.path.mkdir()
    for screen in ("hamlet", "estate_map", "provision", "raid_results",
                   "pause_menu", "modal", "", "UNKNOWN"):
        with pytest.raises(reset.ResetRefused):
            reset.delete_owned(owned, screen=screen, save_root=root,
                               recheck=0)
    assert owned.path.exists(), "a refused delete still removed the directory"


def test_the_delete_refuses_a_path_that_is_not_a_profile_directory(tmp_path):
    """`PROFILE_GLOB` is `*/262060/remote/profile_*`, so a profile's parent is
    the app's `remote` directory -- NOT the save root. The first version of
    this gate asserted "directly under the save root", which would have
    refused every real call while still passing anything shaped like
    `<root>/profile_1`: the exact opposite of its purpose.
    """
    from bridge import reset                                  # noqa: PLC0415
    root, remote = _fake_saves(tmp_path, [0])

    outside = tmp_path.parent / "profile_1"
    wrong_depth = root / "profile_1"
    wrong_app = root / "312830650" / "999999" / "remote" / "profile_1"
    for bad in (outside, wrong_depth, wrong_app):
        bad.mkdir(parents=True, exist_ok=True)
        owned = reset.OwnedSlot(slot=1, path=bad, claimed_at=0.0)
        with pytest.raises(reset.ResetRefused):
            reset.delete_owned(owned, screen="title", save_root=root,
                               recheck=0)
        assert bad.exists()

    good = remote / "profile_1"
    good.mkdir()
    owned = reset.OwnedSlot(slot=1, path=good, claimed_at=0.0)
    assert reset.delete_owned(owned, screen="title", save_root=root, recheck=0)


def test_a_mismatched_slot_number_is_refused(tmp_path):
    """The slot and the path have to agree. They are carried together in one
    frozen record so they cannot drift, and this is the tripwire for a caller
    that builds an `OwnedSlot` by hand."""
    from bridge import reset                                  # noqa: PLC0415
    root, remote = _fake_saves(tmp_path, [0])
    victim = remote / "profile_0"
    owned = reset.OwnedSlot(slot=1, path=victim, claimed_at=0.0)
    with pytest.raises(reset.ResetRefused):
        reset.delete_owned(owned, screen="title", save_root=root, recheck=0)
    assert victim.exists(), "a slot/path mismatch deleted a real save"


def test_a_closed_game_is_a_valid_state_to_delete_in(tmp_path):
    """Closing the game is the STRONGEST form of "the campaign is unloaded",
    and it is the one the overnight loop uses.

    It is also required rather than tidy: DD caches its save list, so a slot
    deleted under a running game is not re-read, and the next campaign created
    in it skips the difficulty screen and comes out named after the
    save-select hint text with no gold. Reproduced exactly once it was known
    to look for.
    """
    from bridge import reset                                  # noqa: PLC0415
    root, _ = _fake_saves(tmp_path, [0])
    owned = reset.claim_slot(1, save_root=root)
    owned.path.mkdir()
    assert reset.delete_owned(owned, game_running=False, save_root=root,
                              recheck=0) is True
    assert not owned.path.exists()


def test_a_running_game_still_has_to_be_at_the_title(tmp_path):
    """The relaxation above is for a CLOSED game only. With one running, the
    screen gate stands exactly as before."""
    from bridge import reset                                  # noqa: PLC0415
    root, _ = _fake_saves(tmp_path, [0])
    owned = reset.claim_slot(1, save_root=root)
    owned.path.mkdir()
    with pytest.raises(reset.ResetRefused):
        reset.delete_owned(owned, screen="hamlet", game_running=True,
                           save_root=root, recheck=0)
    assert owned.path.exists()


def test_a_crashed_iteration_can_adopt_its_own_leftover(tmp_path):
    """An iteration that dies between creating a campaign and deleting it
    leaves the directory behind. A strict refusal then blocks EVERY later
    run -- the loop stops at the first crash and the night is over.

    So the claim is written down outside the save directory, and an existing
    slot may be adopted when the journal says this tool created it and never
    released it.
    """
    from bridge import reset                                  # noqa: PLC0415
    root, _ = _fake_saves(tmp_path, [0])
    journal = tmp_path / "claims.json"

    owned = reset.claim_slot(1, save_root=root, journal=journal)
    owned.path.mkdir()                                        # "the game made it"
    # ... and now the process dies. A fresh claim adopts rather than refusing.
    again = reset.claim_slot(1, save_root=root, journal=journal)
    assert again.path == owned.path and again.slot == 1
    assert reset.delete_owned(again, game_running=False, save_root=root,
                              recheck=0, journal=journal)


def test_a_slot_with_no_claim_is_still_unreachable(tmp_path):
    """The invariant, restated for the recovery path: a campaign a HUMAN made
    has no journal entry, so adoption cannot reach it."""
    from bridge import reset                                  # noqa: PLC0415
    root, remote = _fake_saves(tmp_path, [0, 1])
    journal = tmp_path / "claims.json"
    with pytest.raises(reset.ResetRefused):
        reset.claim_slot(1, save_root=root, journal=journal)
    assert (remote / "profile_1").exists()


def test_a_released_claim_cannot_be_reused(tmp_path):
    """A successful delete releases the claim. Without that, the entry would
    keep authorising deletion of whatever later occupies the slot."""
    from bridge import reset                                  # noqa: PLC0415
    root, _ = _fake_saves(tmp_path, [0])
    journal = tmp_path / "claims.json"
    owned = reset.claim_slot(1, save_root=root, journal=journal)
    owned.path.mkdir()
    assert reset.delete_owned(owned, game_running=False, save_root=root,
                              recheck=0, journal=journal)
    owned.path.mkdir()                            # somebody else's campaign now
    with pytest.raises(reset.ResetRefused):
        reset.claim_slot(1, save_root=root, journal=journal)


def test_a_stale_claim_expires(tmp_path):
    """A claim from a previous night must not speak for what is in the slot
    today. Expiry is what bounds the recovery path in time."""
    from bridge import reset                                  # noqa: PLC0415
    root, _ = _fake_saves(tmp_path, [0])
    journal = tmp_path / "claims.json"
    owned = reset.claim_slot(1, save_root=root, journal=journal)
    owned.path.mkdir()
    with pytest.raises(reset.ResetRefused):
        reset.claim_slot(1, save_root=root, journal=journal, max_age=-1.0)


# ── the pause menu, where one row quits the game ──

def test_exit_to_main_menu_is_far_enough_from_exit_to_desktop():
    """37.5 px apart at 720p, and the lower one ends the night.

    The constant for the row we want is worth little without the distance to
    the row we must never hit, so the gap is asserted rather than trusted --
    the same argument `test_no_plan_can_ever_target_a_delete_button` makes
    about the save list.
    """
    from bridge import layout                                 # noqa: PLC0415
    menu_y = layout.PAUSE_EXIT_TO_MAIN_MENU[1]
    desktop_y = layout.PAUSE_EXIT_TO_DESKTOP[1]
    assert desktop_y > menu_y, "the two rows are the wrong way round"
    gap = (desktop_y - menu_y) * 720
    assert gap > 30, f"only {gap:.1f} px between them at 720p"
    assert layout.PAUSE_EXIT_TO_MAIN_MENU[0] == 0.5, "the menu is centred"


def test_nothing_ever_clicks_exit_to_desktop():
    """Banked and deliberately unreferenced, the way `TORCH_METER` is.

    A coordinate that exists so its hazard is documented must not acquire a
    caller by accident, so this greps the package rather than trusting review.
    """
    import re                                                 # noqa: PLC0415
    root = Path(__file__).resolve().parents[2] / "bridge"
    users = []
    for path in root.glob("*.py"):
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "PAUSE_EXIT_TO_DESKTOP" not in line:
                continue
            if path.name == "layout.py":
                continue                        # its definition
            if re.match(r"\s*#", line):
                continue                        # a comment naming it
            users.append(f"{path.name}:{i}")
    assert not users, f"something references Exit to Desktop: {users}"


def test_the_dungeon_title_plate_is_pressed_past(resolved):
    """DD shows a "Press [SPACE] or [CLICK] to Continue" plate over a loaded
    dungeon. The boot ends THERE, not in the raid, and without a plan for it
    the whole campaign was written off as a failed boot at the last step --
    then torn down and deleted.

    SPACE, because that is the game's own instruction on the plate, so it
    needs no coordinate and cannot drift.
    """
    from bridge.act import VK_SPACE                            # noqa: PLC0415
    from bridge.menus import plan_for                          # noqa: PLC0415
    plan = plan_for("dungeon_intro", resolved, slot=1)
    assert plan.actionable, plan.refusal
    assert [s.kind for s in plan.steps] == ["key"]
    assert plan.steps[0].vk == VK_SPACE


# ── the journal belongs to its save root ─────────────────────────────────

def test_a_claim_on_a_TEMP_root_never_reaches_the_production_journal(tmp_path):
    """The suite must not be able to damage the tool it is testing.

    Every `claim_slot` call in this file overrides `save_root` and most do not
    override `journal`, so with a fixed default they all wrote claims about
    pytest temp directories into `archive/reset_claims.json`. `JOURNAL_KEEP`
    is 50 and a suite run makes about twenty, so **after a few runs the real
    journal held nothing but `pytest-of-Francisco` paths** -- and
    `tools/overnight.py` could no longer adopt the slot it had itself created
    that morning. The loop then refused to start, correctly, on evidence the
    test suite had destroyed.

    Measured on 2026-08-31, which is how it was found: the production journal
    contained 20 entries and every single one was a pytest path.

    The fix is structural rather than a convention -- `journal_for(save_root)`
    means a claim about some other world cannot be written into this one's
    journal even by a caller that forgets.
    """
    from bridge import reset

    production = reset.CLAIM_JOURNAL
    before = production.read_bytes() if production.exists() else None

    root, _ = _fake_saves(tmp_path, [0, 8])
    owned = reset.claim_slot(1, save_root=root)
    assert owned.slot == 1

    after = production.read_bytes() if production.exists() else None
    assert after == before, "a temp-root claim reached the production journal"

    # ...and it went somewhere, so adoption still works for that root.
    assert reset.journal_for(root).exists()
    assert reset.journal_for(root) != production


def test_the_real_save_root_still_uses_the_real_journal():
    """The other half. If `journal_for` stopped resolving the production root
    to the production journal, the overnight loop would write its claims
    somewhere nothing reads and could never adopt its own leftover."""
    from bridge import reset
    from bridge.live_state import SAVE_ROOT

    assert reset.journal_for(SAVE_ROOT) == reset.CLAIM_JOURNAL


def test_an_explicit_journal_still_wins(tmp_path):
    """`journal_for` decides the DEFAULT only -- a caller that names one gets
    it, which is what the existing tests in this file rely on."""
    from bridge import reset

    root, _ = _fake_saves(tmp_path, [0, 8])
    named = tmp_path / "named.json"
    reset.claim_slot(1, save_root=root, journal=named)
    assert named.exists()
    assert not reset.journal_for(root).exists()


# ── screens that are waiting on a LOAD ───────────────────────────────────

def test_a_screen_that_gates_on_a_LOAD_is_patient():
    """`MAX_REPEAT` asks "is this screen responding", which is the wrong
    question for a gate waiting on a load.

    Both of these dismiss on a click or a key that does nothing until the
    dungeon has finished loading, and the screen AFTER them has no reference
    -- so `app.log` is what ends the wait, and `app.log` cannot report a raid
    that has not started loading yet.

    `dungeon_intro` was missing from this list and it cost a whole campaign on
    2026-08-31: three SPACE presses over 12 s, "PAUSED - did not respond to 3
    clicks", the night recorded `boot-failed` and deleted the slot -- and a
    minute later the party was standing in the Ruins. The boot had worked.
    """
    from bridge.menus import MAX_REPEAT, PATIENT_SCREENS

    # `dungeon_intro_darkest` is the same gate wearing the Darkest Dungeon's
    # own art -- every week skip goes through it, so it needs the same
    # patience for the same reason.
    assert set(PATIENT_SCREENS) == {"loading", "dungeon_intro",
                                    "dungeon_intro_darkest"}
    for name, allowed in PATIENT_SCREENS.items():
        assert allowed > MAX_REPEAT, f"{name} is no more patient than default"


def test_the_step_budget_cannot_undercut_a_patient_screen():
    """Two bounds that can disagree about which is in force are one bound too
    many -- a flat budget once silently overrode the loading screen's own
    allowance and stopped a run one click short of it."""
    from bridge.menus import MAX_STEPS, PATIENT_SCREENS

    assert MAX_STEPS > max(PATIENT_SCREENS.values())


# --- the save list is indexed by SLOT, not packed -------------------------
#
# Both readings fit the evidence for a long time, and picking the wrong one
# does not fail -- it clicks an empty letter and starts a campaign nobody
# asked for, which is what happened on 2026-09-03.
#
# Settled by scrolling the live list to the bottom with profile_0, profile_1
# and profile_8 on disk. DD draws "Click the letter to begin...", "Click the
# letter to begin...", then "Darkest - Hamlet, Week 6". Those are slots 6, 7
# and 8: EVERY free slot draws its own letter row, so the row is the slot and
# profile_8 sits eight rows down a list two rows tall.

def test_an_existing_campaigns_row_is_its_own_slot_number(tmp_path):
    root = _profiles(tmp_path, 0, 1, 8)
    g = "*/262060/remote/profile_*"
    assert menus.campaign_row(0, root, g) == 0
    assert menus.campaign_row(1, root, g) == 1
    assert menus.campaign_row(8, root, g) == 8, \
        "a hole does not pull later campaigns up the list"


def test_a_slot_with_no_campaign_has_no_row(tmp_path):
    """None, never a number. The row would name an empty letter, and clicking
    that starts a NEW campaign instead of loading the one asked for."""
    root = _profiles(tmp_path, 0, 8)
    assert menus.campaign_row(3, root, "*/262060/remote/profile_*") is None


def test_the_free_slot_and_its_row_are_the_same_number(tmp_path):
    """`first_free_slot` names both the directory DD will create and the row
    its letter is drawn on -- that identity is what `plan_for` relies on, and
    it holds precisely because the holes are drawn rather than closed up."""
    root = _profiles(tmp_path, 0, 8, 9)
    g = "*/262060/remote/profile_*"
    assert menus.first_free_slot(root, g) == 1
    assert menus.campaign_row(8, root, g) == 8


def test_the_plan_clicks_the_row_that_is_the_slot(resolved):
    plan = menus.plan_for("save_select", resolved, slot=1)
    assert plan.actionable
    assert plan.steps[0].screen == resolved.save_row(1)


def test_a_negative_slot_is_not_on_the_list(resolved):
    """`scroll_plan` bounds the slot on both sides; a negative one is a caller
    bug and must not resolve to row 0."""
    plan = menus.plan_for("save_select", resolved, slot=-1, total_rows=9)
    assert not plan.actionable


# --- the town event -------------------------------------------------------

def test_the_town_event_is_closed_rather_than_read(resolved):
    """"Hear Ye, Hear Ye!" -- the festival announcement DD raises on entering
    the Hamlet. It swallows every click aimed at the town underneath, and
    refusing it as unmodelled threw away a whole campaign on 2026-09-03."""
    plan = menus.plan_for("town_event", resolved, slot=0)
    assert plan.actionable
    assert plan.steps[0].screen == resolved.town_close_panel
    assert len(plan.steps) == 1, "one click, and nothing that reads the event"
# --- reaching a slot below the fold ---------------------------------------
#
# Measured on the live game 2026-09-03 with slots {0, 1, 8} on disk, reading
# each visible row's info column (only a campaign draws location/week/date):
#
#     clicks 0 -> CAMPAIGN CAMPAIGN   (slots 0, 1)
#     clicks 1 -> letter   letter     (slots 2, 3)
#     clicks 2 -> letter   letter     (slots 4, 5)
#     clicks 3 -> letter   CAMPAIGN   (slots 7, 8)  and unchanged through 12
#
# So the arrow PAGES by the viewport and CLAMPS at the bottom.

def test_a_visible_slot_needs_no_scrolling(tmp_path):
    total = 9
    assert menus.scroll_plan(0, total) == (0, 0)
    assert menus.scroll_plan(1, total) == (0, 1)


def test_the_arrow_pages_by_the_viewport(tmp_path):
    """One click per VISIBLE row, not one per row -- which is why slot 3 is
    one click away and not two."""
    total = 9
    assert menus.scroll_plan(2, total) == (1, 0)
    assert menus.scroll_plan(3, total) == (1, 1)
    assert menus.scroll_plan(4, total) == (2, 0)


def test_the_bottom_clamps_and_the_row_follows_it(tmp_path):
    """Slot 8 of 9 rows: paging would reach offset 8, the list stops at 7, so
    the slot lands on the LAST visible row rather than the first. Getting this
    backwards clicks row 0 -- a different campaign, silently."""
    assert menus.scroll_plan(8, 9) == (4, 1)
    assert menus.scroll_plan(7, 9) == (3, 1)


def test_over_clicking_is_safe_because_the_list_clamps(tmp_path):
    """The live probe clicked 12 times and rows 3..12 were identical. The plan
    may therefore ask for more clicks than strictly needed without landing
    somewhere else -- which is what makes a single arithmetic slip harmless."""
    clicks, row = menus.scroll_plan(8, 9)
    assert clicks * menus.SAVE_ROWS_PER_CLICK >= 9 - VISIBLE_SAVE_ROWS
    assert row == 8 - (9 - VISIBLE_SAVE_ROWS)


def test_a_slot_off_the_list_has_no_plan(tmp_path):
    assert menus.scroll_plan(9, 9) is None
    assert menus.scroll_plan(-1, 9) is None
    assert menus.scroll_plan(0, 0) is None


def test_the_row_count_stops_at_the_highest_CAMPAIGN(tmp_path):
    """`profile_9` on this machine is the Butcher's Circus MULTIPLAYER profile
    -- no `persist.game.json` -- and DD does not draw it. Counting it as a row
    added a tenth that does not exist and moved every scroll target by one."""
    root = tmp_path / "u" / "262060" / "remote"
    root.mkdir(parents=True)
    for n in (0, 1, 8):
        (root / f"profile_{n}").mkdir()
        (root / f"profile_{n}" / menus.CAMPAIGN_MARKER).write_text("{}")
    mp = root / "profile_9"
    mp.mkdir()
    (mp / "persist.circus_estate.json").write_text("{}")

    g = "*/262060/remote/profile_*"
    assert menus.campaign_slots(tmp_path, g) == {0, 1, 8}
    assert menus.total_save_rows(tmp_path, g) == 9
    assert menus.campaign_row(9, tmp_path, g) is None, \
        "a multiplayer profile is not a campaign to load"


def test_a_contiguous_run_still_leaves_room_for_the_letter(tmp_path):
    """With 0 and 1 used the letter is at slot 2, so the list is three rows --
    the highest campaign is not the end of it."""
    root = tmp_path / "u" / "262060" / "remote"
    root.mkdir(parents=True)
    for n in (0, 1):
        (root / f"profile_{n}").mkdir()
        (root / f"profile_{n}" / menus.CAMPAIGN_MARKER).write_text("{}")
    assert menus.total_save_rows(tmp_path, "*/262060/remote/profile_*") == 3


# --- the click is gated on what the row actually holds ---------------------

def _row_image(levels, h=720, w=1280):
    """A frame whose visible rows carry the given info-column brightness."""
    import numpy as np
    from bridge.layout import SAVE_ROW_PITCH, SAVE_ROW_Y0

    img = np.zeros((h, w, 3), dtype=np.uint8)
    x0, x1 = int(menus.SAVE_INFO_X[0] * w), int(menus.SAVE_INFO_X[1] * w)
    for row, level in enumerate(levels):
        centre = (SAVE_ROW_Y0 + SAVE_ROW_PITCH * row) * h
        half = menus.SAVE_INFO_BAND * SAVE_ROW_PITCH * h
        img[int(centre - half):int(centre + half), x0:x1] = level
    return img


def test_a_campaign_row_reads_bright_and_a_letter_row_dark():
    """Measured on two live frames: campaign rows 8.4 and 10.8, letter rows
    1.8. The threshold sits between them with room on both sides."""
    img = _row_image([12, 0])
    assert menus.row_holds_a_campaign(img, 0)
    assert not menus.row_holds_a_campaign(img, 1)


def test_an_unreadable_frame_is_not_a_campaign():
    """Failing to False, like `_panel_is_open`: not being able to see is not
    evidence that the row is safe to click. Clicking an empty letter STARTS a
    campaign, so the wrong default here creates saves nobody asked for."""
    assert not menus.row_holds_a_campaign(None, 0)
    assert menus.row_info_level(None, 0) == 0.0


# --- reaching a campaign under the fold, and not the wrong one -------------

def _list_frame(campaign_rows=(), width=2560, height=1440):
    """A save-list frame where the named visible rows draw campaign info."""
    import numpy as np
    from bridge.layout import SAVE_ROW_PITCH, SAVE_ROW_Y0
    img = np.zeros((height, width, 3), dtype=np.uint8)
    x0 = int(menus.SAVE_INFO_X[0] * width)
    x1 = int(menus.SAVE_INFO_X[1] * width)
    for row in campaign_rows:
        centre = (SAVE_ROW_Y0 + SAVE_ROW_PITCH * row) * height
        half = menus.SAVE_INFO_BAND * SAVE_ROW_PITCH * height
        img[int(centre - half):int(centre + half), x0:x1] = 200
    return img


def test_a_campaign_row_reads_as_one_and_an_empty_letter_does_not():
    """The gate's own eyes, before anything is built on them. Measured on two
    live frames: campaign rows 8.4 and 10.8, letter rows 1.8."""
    frame = _list_frame(campaign_rows=(1,))
    assert not menus.row_holds_a_campaign(frame, 0)
    assert menus.row_holds_a_campaign(frame, 1)


def _gated(monkeypatch, *, slot, on_disk, drawn_rows, total=9):
    monkeypatch.setattr(menus, "total_save_rows", lambda *_a, **_k: total)
    monkeypatch.setattr(menus, "campaign_row",
                        lambda s, *_a, **_k: s if on_disk else None)
    r = _runner(["save_select"], slot=slot)
    r._frame = _list_frame(campaign_rows=drawn_rows)
    plan = menus.plan_for("save_select", r.resolved, slot=slot,
                          scrolled=99, total_rows=total)
    return r._save_row_looks_wrong(plan)


def test_the_row_click_is_refused_when_the_slot_should_hold_a_campaign(
        monkeypatch):
    """CLICKING AN EMPTY LETTER STARTS A NEW CAMPAIGN, and that has already
    happened here once on a wrong belief about how the list is indexed. Slot 8
    lands on visible row 1; if row 1 draws nothing, the scroll went somewhere
    else and the click is not recoverable."""
    why = _gated(monkeypatch, slot=8, on_disk=True, drawn_rows=())
    assert "would START a campaign" in why, why


def test_the_row_click_is_refused_when_the_slot_should_be_EMPTY(monkeypatch):
    """The other side, and it matters just as much: loading somebody else's
    campaign is as irreversible as starting one nobody asked for."""
    why = _gated(monkeypatch, slot=8, on_disk=False, drawn_rows=(1,))
    assert "would LOAD somebody else" in why, why


def test_the_row_click_goes_ahead_when_disk_and_screen_agree(monkeypatch):
    assert _gated(monkeypatch, slot=8, on_disk=True, drawn_rows=(1,)) == ""
    assert _gated(monkeypatch, slot=8, on_disk=False, drawn_rows=()) == ""


def test_a_gate_that_cannot_SEE_does_not_also_decide(monkeypatch):
    """A frame that will not read is not evidence of anything. `plan_for` is
    what refuses an unreachable slot; this one only ever refuses a mismatch it
    actually measured."""
    monkeypatch.setattr(menus, "total_save_rows", lambda *_a, **_k: 9)
    r = _runner(["save_select"], slot=8)
    plan = menus.plan_for("save_select", r.resolved, slot=8, scrolled=99,
                          total_rows=9)
    r._frame = None
    assert r._save_row_looks_wrong(plan) == ""
    r._frame = object()
    assert r._save_row_looks_wrong(plan) == ""


def test_the_gate_ignores_a_plan_that_is_not_a_row_click(monkeypatch):
    """A scroll is not the irreversible click, so gating it would stall the
    list on its first arrow press."""
    monkeypatch.setattr(menus, "total_save_rows", lambda *_a, **_k: 9)
    monkeypatch.setattr(menus, "campaign_row", lambda *_a, **_k: 8)
    r = _runner(["save_select"], slot=8)
    r._frame = _list_frame(campaign_rows=())
    scroll = menus.plan_for("save_select", r.resolved, slot=8, scrolled=0,
                            total_rows=9)
    assert scroll.steps[0].label == "scroll the save list"
    assert r._save_row_looks_wrong(scroll) == ""


def test_the_runner_scrolls_four_times_and_then_clicks_the_row(monkeypatch):
    """End to end through the loop, which is where the offset is tracked.

    A scroll leaves the screen NAMED `save_select`, so without the counter the
    runner sends the same first arrow press forever and the repeat guard calls
    a working list a stuck one.
    """
    monkeypatch.setattr(menus.applog, "count", lambda *_a, **_k: 0)
    monkeypatch.setattr(menus, "total_save_rows", lambda *_a, **_k: 9)
    monkeypatch.setattr(menus, "campaign_row", lambda *_a, **_k: 8)
    r = _runner(["save_select"] * 6, slot=8, then="hamlet")
    r._look = (lambda seq=iter(["save_select"] * 5 + ["hamlet"]):
               (next(seq, "hamlet"), []))
    assert r.run(timeout=5) is True
    # The backend records the primitive, not the plan, so the WHERE is the
    # only thing it keeps -- which is the right thing to assert anyway.
    arrow = r.resolved.menu_point("save_scroll_down")
    row = r.resolved.save_row(1)                 # slot 8 lands on visible 1
    where = [st.screen for st in r.actuator.backend.sent]
    assert where == [arrow] * 4 + [row], where
    assert r.scrolled == 4


def test_the_scroll_count_resets_when_the_list_is_left(monkeypatch):
    """The list re-opens at the top, so a stale offset would click a row four
    places off on the next visit."""
    monkeypatch.setattr(menus.applog, "count", lambda *_a, **_k: 0)
    monkeypatch.setattr(menus, "total_save_rows", lambda *_a, **_k: 9)
    monkeypatch.setattr(menus, "campaign_row", lambda *_a, **_k: 8)
    r = _runner(["save_select"], slot=8, then="hamlet")
    r._look = (lambda seq=iter(["save_select", "save_select", "title",
                                "save_select", "hamlet"]):
               (next(seq, "hamlet"), []))
    r.run(timeout=5)
    arrow = r.resolved.menu_point("save_scroll_down")
    row = r.resolved.save_row(1)
    where = [st.screen for st in r.actuator.backend.sent]
    assert where.count(arrow) == 3, where
    assert row not in where, "a stale offset clicked a row"
