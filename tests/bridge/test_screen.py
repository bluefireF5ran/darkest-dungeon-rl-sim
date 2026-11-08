"""Reading the screen — the sense the bridge never had.

Everything else in `bridge/` confirms an action by watching a save file change.
Three things carry no save at all: the front-end menus, which of the map /
inventory panels is showing, and where the curio panel's button row is. This
module is the primitive all three need, and these tests are what make it
trustworthy enough to click on.

The load-bearing test is `test_a_near_tie_is_reported_as_unknown`. Recognition
is allowed to say "I do not know", and it MUST say it rather than pick a
winner by a hair: the DLC list and the modal drawn on top of it are the
closest distinct pair in the corpus, and reading the list as the modal would
click `layout.MODAL_OK` into the DLC rows and switch a DLC off with nothing
to notice it.

`grab()` is deliberately untested here — it needs a live window, and a test
that mocks Win32 would only prove the mock.
"""

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from bridge import screen                                        # noqa: E402

# xdist: this file's tests spike to +172 MB each while they decode frames.
# `--dist load` scatters them, so up to 16 spikes land at once and the suite's
# PEAK -- not its steady state -- is what crashes the machine. Grouped, at most
# one runs at a time. The four grouped files cost 12.9 s serially against a 74 s
# suite, so this buys memory without touching the critical path.
pytestmark = pytest.mark.xdist_group("memory_heavy")

CAPTURES = Path(__file__).resolve().parents[2] / "captures"
PNGS = sorted(CAPTURES.glob("00_*.png"))
# The named family `screen.py --capture` writes. It is globbed SEPARATELY and
# then folded into the same checks, because a reference that goes into
# screen_refs.json without the suite ever recognising it is precisely the
# silent failure these tests exist to catch -- and for one commit that is
# exactly what the six live-captured screens were.
SCREEN_PNGS = sorted(screen.CAPTURES_SCREENS.glob("*.png"))
ALL_PNGS = PNGS + SCREEN_PNGS


def _expected(png):
    """The name a capture must resolve to, whichever family it is from.

    A `name@variant.png` capture is a SECOND LOOK at `name` -- the raid
    progression screen with an affliction glowing over it, say -- so it must
    resolve to the base name, not to its own stem.
    """
    return (screen.CAPTURE_NAMES.get(png.name[:4]) if png in PNGS
            else screen.base_name(png.stem))
pytestmark = pytest.mark.skipif(not PNGS, reason="no menu captures")


def _pil():
    return pytest.importorskip("PIL.Image", reason="Pillow is dev-only")


def _img(png):
    Image = _pil()
    return np.asarray(Image.open(png).convert("RGB"), dtype=np.uint8)


@pytest.fixture(scope="module")
def refs():
    r = screen.load_refs()
    if not r:
        pytest.skip("no screen_refs.json - run bridge/screen.py --calibrate")
    return r


# --- the signature ---

def test_the_downsample_averages_areas_not_samples_them():
    """A single bright pixel must survive as a raised block average, not be
    dropped by a nearest-neighbour pick. Otherwise a one-pixel difference
    between two screens decides which one we think is up."""
    a = np.zeros((100, 100))
    a[7, 7] = 255.0
    out = screen._boxdown(a, 10, 10)
    assert out.sum() == pytest.approx(255.0 / 100)
    assert out[0, 0] > 0 and out.max() == out[0, 0]


def test_a_signature_is_unit_length_and_mean_free():
    sig = screen.signature(_img(PNGS[2]))
    assert sig.shape == (screen.SIG_GRID[0] * screen.SIG_GRID[1],)
    assert abs(float(sig.mean())) < 1e-12
    assert float(np.linalg.norm(sig)) == pytest.approx(1.0)


def test_brightness_and_contrast_do_not_change_a_signature():
    """Mean-normalising is what survives the menus' flickering fire and a
    monitor set brighter than the one the captures came off: it compares the
    SHAPE of the light, not its level."""
    img = _img(PNGS[2]).astype(np.float64)
    base = screen.signature(img)
    dimmer = screen.signature(np.clip(img * 0.6 + 12, 0, 255))
    assert screen.distance(base, dimmer) < 0.01


def test_a_featureless_frame_matches_nothing(refs):
    """A screen still fading in has no shape. 'Both frames were flat' is not
    a match, so `distance` returns its maximum rather than zero."""
    black = np.zeros((1440, 2560, 3), dtype=np.uint8)
    assert not np.any(screen.signature(black))
    assert screen.distance(screen.signature(black), list(refs.values())[0]) == 2.0
    assert screen.recognise(black, refs) == ""


def test_the_region_is_a_fraction_so_a_windowed_game_still_works():
    """Every stored point in `layout.py` is a fraction of the client rect for
    this reason; the signature box has to be too, or the recogniser and the
    clicker disagree about where the screen is the moment the game is not
    fullscreen."""
    Image = _pil()
    full = _img(PNGS[4])
    small = np.asarray(Image.open(PNGS[4]).convert("RGB").resize((1280, 720)),
                       dtype=np.uint8)
    assert screen.distance(screen.signature(full),
                           screen.signature(small)) < 0.02


# --- recognition against the real corpus ---

def test_every_capture_is_recognised_as_itself(refs):
    for png in ALL_PNGS:
        want = _expected(png)
        assert want, f"{png.name} has no expected name"
        assert screen.recognise(_img(png), refs) == want, png.name


def test_every_screen_is_far_from_every_other_one(refs):
    """The number that justifies the thresholds.

    It was 0.484 -- the DLC list against the modal drawn over it -- when the
    corpus was the nine front-end captures. Eleven live screens from a real
    campaign take it to **0.305**, and the closest pair is `modal` against
    `embark_underprepared`.

    **That pair was removed once and then put back, and the reversal is the
    point.** It was dropped on the argument that it bought nothing: DD draws
    every dialog's first option at one fixed position, so both modals take the
    same click and telling them apart changes no action. Then the town walk
    stalled on it -- at 0.305 from `modal` and 0.413 from `difficulty` the
    live frame is a near-tie, `recognise` correctly answers "I do not know",
    and a walk that refuses unrecognised screens cannot pass. Being unable to
    NAME a screen is a cost even when the action would have been the same.

    **So THRESHOLD (0.35) is now above the worst separation, and that is a
    real weakening.** What still holds is the property that matters: a live
    frame near two references fails the MARGIN test and comes back UNKNOWN,
    which every caller treats as a pause. What no longer holds is the stronger
    one -- that THRESHOLD could never be the binding constraint. Modals are
    the crowded region because the signature is the centre block and a modal
    IS centred text over a darkened copy of whatever it covers.

    If a further modal pushes the MARGIN test into failing for a screen that
    is needed, the answer is a second discriminating region for modals
    specifically -- NOT a smaller MARGIN, which would start letting near-ties
    through everywhere else.
    """
    # Base names on both sides: two looks at one screen are not "two screens
    # close together", and counting them as such would make banking a variant
    # look like a corpus regression.
    worst = min(
        (min(d for n, d in screen.rank(_img(png), refs)[1]
             if screen.base_name(n) != _expected(png)), png.name)
        for png in ALL_PNGS)
    assert worst[0] > screen.MARGIN * 2, worst
    assert worst[0] == pytest.approx(0.305, abs=0.01)


def test_the_two_save_select_captures_are_one_screen(refs):
    """`00_3` (empty letter in row 2) and `00_7` (the new campaign in row 2)
    are the same screen. They share a name on purpose, and which row 2 holds
    is read from disk -- how many `profile_*` exist -- never from pixels."""
    a, b = CAPTURES / "00_3_Select_Save.png", CAPTURES / "00_7_After_creating_save.png"
    assert screen.distance(screen.signature(_img(a)),
                           screen.signature(_img(b))) < 0.01
    assert screen.recognise(_img(a), refs) == screen.recognise(_img(b), refs) \
        == "save_select"


# --- the refusal, which is the point ---

def test_a_near_tie_is_reported_as_unknown(refs):
    """Two screens a hair apart must resolve to "I do not know". Forced here
    by demanding an impossible margin: the answer has to become `""`, not
    whichever reference happened to win."""
    img = _img(CAPTURES / "00_4_Select_DLCs.png")
    assert screen.recognise(img, refs) == "dlc"
    assert screen.recognise(img, refs, margin=1.5) == ""


def test_a_screen_nothing_resembles_is_unknown(refs):
    rng = np.random.default_rng(0)
    noise = rng.integers(0, 256, (1440, 2560, 3), dtype=np.uint8)
    assert screen.recognise(noise, refs) == ""


def test_the_thresholds_bracket_what_the_live_game_actually_produces():
    """The numbers `THRESHOLD` was set from, pinned so a later tightening has
    to argue with them.

    Measured against the running game: the title screen matched itself at
    0.076 and then 0.176 on the next look -- its animated fire and cloud sit
    inside the signature region -- while every runner-up was 0.79 or worse and
    a black frame was 0.9+ from everything. So the threshold has to clear
    0.176 with room, and stay well under the 0.484 worst-case gap between two
    distinct screens, or it starts deciding matches that `MARGIN` should.
    """
    assert screen.THRESHOLD > 0.176 * 1.5, "an animated screen will read UNKNOWN"
    # THRESHOLD is deliberately NOT compared to the worst separation any
    # more: at 0.35 it now exceeds it (0.305, the two modals). See
    # `test_every_screen_is_far_from_every_other_one` for why that was
    # accepted. What must still hold is that MARGIN can separate the closest
    # real pair, because MARGIN is the only thing standing between a near-tie
    # and a wrong answer.
    assert screen.MARGIN < 0.305, "MARGIN cannot separate the closest pair"


def test_rank_reports_the_scores_even_when_it_refuses(refs):
    """A pause has to name what it could not decide between, or the operator
    is told 'unknown screen' and nothing else."""
    name, scored = screen.rank(_img(PNGS[0]), refs, thresh=0.0)
    assert name == "" and scored
    assert scored == sorted(scored, key=lambda t: t[1])


# --- which right-hand panel is showing ---
#
# The map and the inventory share one slot and the save records neither, which
# is why a room cannot be left: the exit is a click on the map, and the map has
# to be in front. Eight real dungeon captures, three of them map.

PANELS = {"combat.png": "map", "generic curio.png": "map",
          "calibration_full.png": "map", "trap.png": "inventory",
          "chest.png": "inventory", "shovel_curio.png": "inventory",
          "trap_disarm_chance.png": "inventory",
          "full_inventory.png": "inventory"}


@pytest.mark.parametrize("name,want", sorted(PANELS.items()))
def test_the_panel_is_named_correctly(name, want):
    png = CAPTURES / name
    if not png.exists():
        pytest.skip(f"{name} missing")
    assert screen.panel(_img(png)) == want


def test_a_dimmed_screen_is_still_read_correctly():
    """`full_inventory.png` is the one with DD's "no room" modal up, and the
    game darkens everything behind it. An absolute brightness test got the
    other four inventories and missed this one -- which is precisely the case
    `bridge/inventory.py` exists for. Normalising the profile by its own
    spread is what fixes it, so the check is that the two agree after a
    synthetic dimming too."""
    img = _img(CAPTURES / "trap.png")
    dim = (img.astype(np.float64) * 0.25).astype(np.uint8)
    assert screen.panel(dim) == screen.panel(img) == "inventory"


def test_one_reference_could_not_have_done_this_job():
    """Why `panel()` measures a grid instead of matching a hash: the three MAP
    captures are three different dungeons and sit up to 0.802 from each other,
    against 0.971 to the nearest inventory. A single "map" reference would be
    a reference to one dungeon's map."""
    maps = [_img(CAPTURES / n) for n, v in PANELS.items()
            if v == "map" and (CAPTURES / n).exists()]
    if len(maps) < 2:
        pytest.skip("need two map captures")
    worst = max(screen.distance(screen.signature(a, screen.PANEL_X + screen.PANEL_Y),
                                screen.signature(b, screen.PANEL_X + screen.PANEL_Y))
                for i, a in enumerate(maps) for b in maps[i + 1:])
    assert worst > 0.5, "maps resemble each other more than expected"


def test_a_blank_panel_answers_neither():
    """A frame mid-transition has no grid and no map. Guessing there would put
    a TAB into a screen that has no panel at all."""
    assert screen.panel(np.zeros((1440, 2560, 3), dtype=np.uint8)) == ""
    assert screen.panel(None) == ""


# --- banking a grab ---

def test_a_saved_grab_round_trips(tmp_path):
    """`bridge/act.py` banks the map panel it stopped in front of, and that
    PNG is the input to measuring where an area sits on it -- so the bytes
    have to come back unchanged, not merely be written."""
    _pil()
    rng = np.random.default_rng(1)
    img = rng.integers(0, 256, (40, 60, 3), dtype=np.uint8)
    out = tmp_path / "sub" / "shot.png"
    assert screen.save_png(img, out)
    assert np.array_equal(_img(out), img)


def test_saving_never_raises_on_a_machine_that_cannot(tmp_path):
    """Best-effort by contract: it is called from the stop path, and the stop
    MESSAGE is what the operator needs. A missing Pillow, an unwritable disk
    or a buffer that is not an image all have to come back as False."""
    assert screen.save_png(object(), tmp_path / "x.png") is False
    assert screen.save_png(np.zeros((2, 2, 3), dtype=np.uint8),
                           tmp_path) is False


# --- the references on disk ---

def test_the_committed_references_match_a_fresh_build(refs):
    """`screen_refs.json` is generated, so it can go stale against the
    captures it claims to come from. Rebuilt here and compared.

    The tolerance is the file's own 6-decimal rounding (~1.5e-6 on the worst
    reference), which is four orders of magnitude inside `MARGIN` and keeps
    the committed file small enough to read."""
    built = screen.build_refs()
    assert set(built) == set(refs)
    for name, sig in built.items():
        assert screen.distance(sig, refs[name]) < 1e-4, name


def test_the_reference_file_records_the_box_it_was_built_with():
    import json
    raw = json.loads(screen.REFS_PATH.read_text(encoding="utf-8"))
    assert tuple(raw["box"]) == pytest.approx(screen.SIG_BOX)
    assert tuple(raw["grid"]) == screen.SIG_GRID


# --- is there a display at all? ---

def test_a_frozen_picture_is_reported_as_NO_DISPLAY(monkeypatch):
    """Probed live on 2026-09-03. The session was unlocked, its id matched
    `WTSGetActiveConsoleSessionId`, `OpenInputDesktop` returned a handle, the
    game launched and took a 1920x1080 borderless window reporting itself
    foreground and not minimised -- and nothing was ever painted. Every capture
    came back a frozen copy of the desktop, with a taskbar clock reading 10:09
    in a frame grabbed at 12:15.

    Without this the symptom reaches the operator as "no recognisable screen
    after 75s", which sends them hunting for a hash mismatch that does not
    exist.
    """
    import bridge.screen as S

    frame = np.full((8, 8, 3), 69, dtype=np.uint8)
    monkeypatch.setattr(S, "grab", lambda _win: frame)
    monkeypatch.setattr(S.time, "sleep", lambda _s: None)

    live, why = S.display_is_live(object(), gap=1.2, samples=3)
    assert live is False
    assert "identical" in why and "69" in why
    # The brightness is in the message on purpose: a frozen DESKTOP reads
    # bright and a mid-load black screen reads near zero, and a reader has to
    # be able to tell those apart in a log.


def test_a_CHANGING_picture_is_live(monkeypatch):
    """A live Darkest Dungeon never holds a frame still -- flames on the title,
    banners in the Hamlet, idle animations in combat -- so any difference at
    all is enough."""
    import bridge.screen as S

    frames = [np.full((8, 8, 3), v, dtype=np.uint8) for v in (10, 10, 11)]
    seq = iter(frames)
    monkeypatch.setattr(S, "grab", lambda _win: next(seq))
    monkeypatch.setattr(S.time, "sleep", lambda _s: None)

    live, why = S.display_is_live(object(), samples=3)
    assert live is True and "changing" in why


def test_a_STILL_frame_the_recogniser_NAMES_is_a_live_game(monkeypatch):
    """THE PREMISE "a live game always animates" IS FALSE for three screens.

    `raid_results` and the two `raid_progression` pages are flat images with
    no animation at all. Measured live 2026-09-05: three identical frames
    1.2 s apart at mean brightness 9.8, recognised as `raid_results` every
    time, game perfectly fine and waiting for a click -- and
    `tools/overnight.py` refused the whole night twice over with "THE GAME IS
    NOT DRAWING".

    A name is the second opinion, and it is strong in the direction that
    matters: a frozen desktop copy matches no banked game screen, and pure
    black matches none either.
    """
    import bridge.screen as S

    frame = np.full((8, 8, 3), 10, dtype=np.uint8)
    monkeypatch.setattr(S, "grab", lambda _win: frame)
    monkeypatch.setattr(S.time, "sleep", lambda _s: None)
    monkeypatch.setattr(S, "recognise", lambda _img: "raid_results")

    live, why = S.display_is_live(object(), gap=1.2, samples=3)
    assert live is True
    assert "raid_results" in why and "still" in why


def test_an_UNNAMED_still_frame_is_still_a_dead_display(monkeypatch):
    """The recognition opinion may only ever RESCUE a frame, never condemn
    one -- so the no-name path has to keep reporting exactly what it did."""
    import bridge.screen as S

    frame = np.full((8, 8, 3), 69, dtype=np.uint8)
    monkeypatch.setattr(S, "grab", lambda _win: frame)
    monkeypatch.setattr(S.time, "sleep", lambda _s: None)
    monkeypatch.setattr(S, "recognise", lambda _img: "")

    live, why = S.display_is_live(object(), gap=1.2, samples=3)
    assert live is False and "identical" in why


def test_a_recogniser_that_RAISES_does_not_take_the_check_with_it(monkeypatch):
    """This runs before anything else in an unattended night, on a frame that
    may be any shape at all. A second opinion that can throw would turn a
    diagnosis into a crash."""
    import bridge.screen as S

    frame = np.zeros((8, 8, 3), dtype=np.uint8)
    monkeypatch.setattr(S, "grab", lambda _win: frame)
    monkeypatch.setattr(S.time, "sleep", lambda _s: None)

    def _boom(_img):
        raise RuntimeError("no references banked")

    monkeypatch.setattr(S, "recognise", _boom)
    live, why = S.display_is_live(object(), gap=1.2, samples=3)
    assert live is False and "PURE BLACK" in why


def test_no_window_is_not_a_frozen_display(monkeypatch):
    """A missing window is a different failure and must not be reported as a
    dead display -- the operator's next move is different for each."""
    import bridge.screen as S

    monkeypatch.setattr(S, "grab", lambda _win: None)
    monkeypatch.setattr(S.time, "sleep", lambda _s: None)
    live, why = S.display_is_live(object())
    assert live is False and "no window" in why


def test_two_samples_is_the_floor():
    """`samples=1` cannot compare anything. It is clamped rather than accepted,
    because a check that silently always returns "live" is worse than no check
    -- this whole function exists because a silent pass sent a reader after the
    wrong bug for an hour."""
    import bridge.screen as S

    calls = []
    orig = S.grab
    try:
        S.grab = lambda _w: (calls.append(1),
                             np.zeros((4, 4, 3), dtype=np.uint8))[1]
        S.display_is_live(object(), gap=0.0, samples=1)
    finally:
        S.grab = orig
    assert len(calls) >= 2
# --- the two frozen-frame causes need OPPOSITE fixes ----------------------
#
# Both look identical to the liveness check -- frames that do not change --
# and the operator's next move is the reverse of the other's. On 2026-09-03
# the message named only the "no display" one and the machine had the other,
# which is an hour spent on a display that was working the whole time.

def test_a_pure_black_frame_blames_the_WINDOW_not_the_display(monkeypatch):
    """`min == max == 0` with a window up is DWM's independent-flip path: a
    borderless window covering a whole monitor is scanned out directly, so
    nothing reaches the surface GDI reads. The display is fine."""
    import bridge.screen as S

    frame = np.zeros((8, 8, 3), dtype=np.uint8)
    monkeypatch.setattr(S, "grab", lambda _win: frame)
    monkeypatch.setattr(S.time, "sleep", lambda _s: None)

    live, why = S.display_is_live(object(), gap=1.2, samples=3)
    assert live is False
    assert "PURE BLACK" in why
    assert "-fullscreen 0" in why, "the fix has to be in the message"
    assert "frozen copy of the desktop" not in why


def test_a_bright_frozen_frame_blames_the_DISPLAY(monkeypatch):
    """A frozen copy of the desktop reads at ordinary desktop brightness --
    ~71 on this machine. That one really is a compositor with nowhere to
    draw, and the fix is the opposite: attach a display."""
    import bridge.screen as S

    frame = np.full((8, 8, 3), 71, dtype=np.uint8)
    monkeypatch.setattr(S, "grab", lambda _win: frame)
    monkeypatch.setattr(S.time, "sleep", lambda _s: None)

    live, why = S.display_is_live(object(), gap=1.2, samples=3)
    assert live is False
    assert "frozen copy of the DESKTOP" in why
    # ...and it must NOT claim that is what this is. A hung GAME looks
    # identical and measured 13 on a stuck title screen, so the message names
    # both and says how to tell them apart. The first version asserted
    # "desktop" and was wrong the first time it mattered.
    assert "the GAME hung on a real frame" in why
    assert "-fullscreen 0" not in why, "wrong remedy for this cause"


def test_one_bright_pixel_is_enough_to_rule_out_the_flip_case(monkeypatch):
    """The test is `max == 0`, not the mean: a nearly-black loading screen
    with any content at all is still being DRAWN, so it is not the bypass."""
    import bridge.screen as S

    frame = np.zeros((8, 8, 3), dtype=np.uint8)
    frame[0, 0, 0] = 1
    monkeypatch.setattr(S, "grab", lambda _win: frame)
    monkeypatch.setattr(S.time, "sleep", lambda _s: None)

    _live, why = S.display_is_live(object(), gap=1.2, samples=3)
    assert "PURE BLACK" not in why
# --- reading the game's answer off what it DREW --------------------------
#
# Fran: "if you don't get the tooltip the game won't let you go to that path".
# Hovering a legal map cell draws "Move to this room" and hovering anything
# else draws nothing, which is a positive permission -- where the map click
# used to send and then wait 15 s for a save change that never came.


def _frame(level=8, shape=(120, 160)):
    return np.full((shape[0], shape[1], 3), level, dtype=np.uint8)


def test_a_plate_drawn_over_the_panel_is_found():
    import bridge.screen as S

    before = _frame()
    after = before.copy()
    after[40:70, 20:100] = 180                      # a tooltip-sized plate
    got = S.what_appeared(before, after)
    assert got is not None
    x0, x1, y0, y1, count = got
    assert (x0, x1, y0, y1) == (20, 99, 40, 69)
    assert count == 30 * 80


def test_nothing_drawn_is_None():
    import bridge.screen as S

    before = _frame()
    assert S.what_appeared(before, before.copy()) is None


def test_only_BRIGHTER_counts():
    """The sign is information. A thing that merely moved -- the party token
    pulsing, a torch guttering -- darkens as much as it brightens, and a
    one-sided test does not survive it."""
    import bridge.screen as S

    before = _frame(level=200)
    after = before.copy()
    after[40:70, 20:100] = 10                       # got darker, not lighter
    assert S.what_appeared(before, after) is None


def test_a_few_flickering_pixels_are_not_a_tooltip():
    """The panel animates on its own, so the floor is what separates a plate
    from the map's own idle."""
    import bridge.screen as S

    before = _frame()
    after = before.copy()
    after[10:12, 10:20] = 200                       # 20 px
    assert S.what_appeared(before, after) is None


def test_the_region_is_respected_and_the_answer_is_in_FRAME_space():
    """A caller that passed a region must not have to add the offset back --
    every coordinate in `bridge/` is frame- or client-relative already."""
    import bridge.screen as S

    before = _frame()
    after = before.copy()
    after[40:70, 20:100] = 180
    assert S.what_appeared(before, after, box=(0, 15, 0, 120)) is None
    got = S.what_appeared(before, after, box=(15, 160, 30, 120))
    assert got is not None and got[0] == 20 and got[2] == 40


def test_mismatched_or_missing_frames_say_nothing():
    """Failing to None, like `_panel_is_open` and `_bar_moved`: not being able
    to see is not evidence that something appeared -- and here that would mean
    clicking a destination the game never offered."""
    import bridge.screen as S

    assert S.what_appeared(None, _frame()) is None
    assert S.what_appeared(_frame(), None) is None
    assert S.what_appeared(_frame(), _frame(shape=(60, 60))) is None


# --- two references that want one click must be ONE screen -----------------

def test_every_banked_screen_is_recognised_as_itself():
    """THE RUNNER-UP TEST CUTS BOTH WAYS, and the second way stranded a raid.

    `recognise` refuses when the nearest reference of a DIFFERENT screen is
    within `MARGIN`. That is right when confusing the two would click the
    wrong thing -- the DLC list and the modal drawn over it are the pair it
    exists for. It is exactly wrong when the two want the SAME click: the
    refusal costs everything and the confusion costs nothing.

    Live on 2026-09-05, the defeat results page scored `raid_results@defeat`
    0.159 and `raid_results` 0.308 -- a gap of 0.149 against a margin of
    0.150 -- and came back UNKNOWN. The walk home stopped after every lost
    raid, which is the state a bridge under development reaches most often.
    The repair was to make the defeat page a VARIANT of the results page
    rather than a sibling of it, which is what `VARIANT_SEP` is for.

    This is the guard that generalises: a banked frame the recogniser cannot
    name is a reference that has been added too close to another one, and it
    catches that when it is banked instead of live.
    """
    import numpy as np
    from bridge import screen as screen_mod
    Image = pytest.importorskip("PIL.Image", reason="Pillow is dev-only")
    folder = screen_mod.CAPTURES / "screens"
    if not folder.is_dir():
        pytest.skip("no banked screens")
    seen = 0
    for png in sorted(folder.glob("*.png")):
        want = screen_mod.base_name(png.stem)
        img = np.asarray(Image.open(png).convert("RGB"), dtype=np.uint8)
        got, scored = screen_mod.rank(img)
        assert got == want, (png.name, got, scored[:3])
        seen += 1
    assert seen >= 15, seen


def test_the_defeat_pages_are_variants_and_not_siblings():
    """The two specific pairs that caused it, pinned by name."""
    from bridge import screen as screen_mod
    names = set(screen_mod.load_refs())
    assert "raid_results@defeat" in names
    assert "raid_results_defeat" not in names
    assert "raid_progression_quirks@defeat" in names
    assert "raid_progression_quirks_defeat" not in names
    for a, b in (("raid_results@defeat", "raid_results"),
                 ("raid_progression_quirks@defeat",
                  "raid_progression_quirks")):
        assert screen_mod.base_name(a) == screen_mod.base_name(b), (a, b)


def test_every_distinct_reference_pair_is_further_apart_than_the_margin():
    """Two screens closer together than `2 * MARGIN` can never both be
    recognised: a frame between them fails the runner-up test whichever it is
    nearer. Measured, so a new capture that lands too close is caught when it
    is banked rather than live."""
    from bridge import screen as screen_mod
    refs = screen_mod.load_refs()
    names = sorted(refs)
    worst = None
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            if screen_mod.base_name(a) == screen_mod.base_name(b):
                continue
            gap = screen_mod.distance(refs[a], refs[b])
            if worst is None or gap < worst[0]:
                worst = (gap, a, b)
    assert worst is not None
    assert worst[0] >= 2 * screen_mod.MARGIN, worst


# --- a tooltip is enough to make a known screen unrecognisable ---

def _pair():
    """The same provision screen with and without the mouse on a cart item."""
    pytest.importorskip("PIL")
    import numpy as np
    from PIL import Image

    root = Path(__file__).resolve().parents[2] / "captures"
    out = []
    for name in ("provision_with_tooltip.png", "provision_no_tooltip.png"):
        path = root / name
        if not path.exists():
            pytest.skip(f"{name} is not banked")
        out.append(np.array(Image.open(path).convert("RGB")))
    return out


def test_a_tooltip_puts_a_known_screen_past_the_threshold():
    """The failure `recognise_live` exists for, as it was measured.

    Provision screen, pointer resting on a Holy Water in the supply cart: the
    right answer is nearest by a mile and still gets no name, because the cut
    is absolute and the tooltip sits inside the signature box.
    """
    dirty, _clean = _pair()
    name, scored = screen.rank(dirty)
    assert name == "", "the tooltip frame is supposed to be unrecognisable"
    assert scored[0][0] == "provision"
    assert scored[0][1] > screen.THRESHOLD
    # And the identification was never actually in doubt -- which is why the
    # repair is to clean the FRAME rather than to loosen the cut. The runner-up
    # is 0.29 further away, nearly twice MARGIN.
    assert scored[1][1] - scored[0][1] > 1.5 * screen.MARGIN


def test_moving_the_mouse_off_it_is_the_whole_repair():
    """Same screen, same everything, cursor parked: 0.45 becomes 0.25."""
    dirty, clean = _pair()
    assert screen.recognise(clean) == "provision"
    d_dirty = screen.rank(dirty)[1][0][1]
    d_clean = screen.rank(clean)[1][0][1]
    assert d_clean < screen.THRESHOLD < d_dirty
    assert d_dirty - d_clean > 0.15


def test_the_absolute_cut_is_load_bearing_and_cannot_just_be_raised():
    """Why `recognise_live` parks the mouse instead of relaxing THRESHOLD.

    Admitting the tooltip frame by raising the cut would need it near 0.45 --
    and two DIFFERENT banked screens already sit 0.305 apart
    (`embark_underprepared` against `modal`), with several more inside 0.45.
    At that cut the absolute test stops carrying any weight at all and every
    identification rests on MARGIN alone. Cleaning the frame keeps both.
    """
    import itertools

    refs = screen.load_refs()
    apart = sorted(screen.distance(refs[a], refs[b])
                   for a, b in itertools.combinations(sorted(refs), 2))
    assert apart[0] < 0.35, "two distinct screens are this close together"
    inside = [d for d in apart if d < 0.45]
    assert len(inside) >= 5, (
        f"only {len(inside)} distinct pairs sit inside the distance the "
        f"tooltip frame needed -- re-read the argument before trusting it")


def test_parking_is_a_move_and_never_a_click():
    """The property that makes it safe to do behind a caller's back.

    `park_cursor` may run in the middle of a town walk, so it must not be able
    to play anything. It touches `SetCursorPos` and nothing else -- no button
    events, no `SendInput`, no `PostMessage`.
    """
    import inspect

    src = inspect.getsource(screen.park_cursor)
    assert "SetCursorPos" in src
    for forbidden in ("mouse_event", "SendInput", "PostMessage", "SendMessage",
                      "keybd_event", "MOUSEEVENTF"):
        assert forbidden not in src, forbidden


def test_park_cursor_survives_a_window_it_cannot_read():
    """Best effort in the literal sense: no second chance, never an exception.

    `recognise_live` is called from the poll loop, which is the one place in
    the bridge that must not raise.
    """
    class _Broken:
        @property
        def client_origin(self):
            raise RuntimeError("no window")

    assert screen.park_cursor(_Broken()) is False

# -- a PURE BLACK frame, and the cause that cost a whole night ---------------
#
# 2026-09-21: the 09:15 run was scored `no-game` over four iterations while
# the game ran perfectly -- 16.9% of the GPU's 3D engine, a live `title`
# screen. The monitor had gone to sleep. The desktop still read fine from its
# cached composition, so every control said the machine was healthy.

class _Win:
    """Enough of a `GameWindow` for the geometry branch."""

    def __init__(self, borderless=False):
        self.borderless = borderless
        self.hwnd = 1
        self.client_size = (1280, 720)


def _black_why(monkeypatch, win, covers):
    import bridge.screen as S
    import bridge.window as W

    frame = np.zeros((8, 8, 3), dtype=np.uint8)
    monkeypatch.setattr(S, "grab", lambda _win: frame)
    monkeypatch.setattr(S.time, "sleep", lambda _s: None)
    monkeypatch.setattr(W, "covers_a_monitor", lambda _w: covers)
    return S.display_is_live(win, gap=1.2, samples=3)


def test_a_black_frame_names_the_SLEEPING_DISPLAY_first(monkeypatch):
    """The cause that was actually measured has to be the first one read,
    and it needs a remedy the loop can perform unattended."""
    live, why = _black_why(monkeypatch, _Win(borderless=False), covers=False)
    assert live is False
    assert "PURE BLACK" in why
    assert "powered down" in why, "the measured cause has to be named"
    assert "wake_display" in why and "keep_display_awake" in why


def test_it_does_not_tell_the_operator_to_do_what_is_already_DONE(monkeypatch):
    """The 09:15 message said "relaunch windowed and smaller" to a window
    that was already 1296x759 on a 1920x1080 monitor."""
    _live, why = _black_why(monkeypatch, _Win(borderless=False), covers=False)
    assert "-fullscreen 0" not in why


def test_it_does_not_OVERCORRECT_into_ruling_the_flip_path_out(monkeypatch):
    """The first fix claimed "not covering a monitor, therefore composited".
    The game in that state WAS drawing and GDI still read nothing, so a
    windowed swapchain can leave the composited path too."""
    _live, why = _black_why(monkeypatch, _Win(borderless=False), covers=False)
    assert "multiplane overlay" in why
    assert "GPU 3D time" in why, "name the instrument that needs no frame"


def test_a_window_COVERING_the_monitor_still_gets_the_flip_remedy(monkeypatch):
    """The 2026-09-03 case is real and must survive: that one IS the flip
    path, and the fix is to stop the window covering the monitor."""
    live, why = _black_why(monkeypatch, _Win(borderless=True), covers=True)
    assert live is False
    assert "-fullscreen 0 -size 1280 720" in why


def test_geometry_it_cannot_READ_names_both_causes(monkeypatch):
    """Refusing to guess is the point. A `win` that cannot answer leaves both
    standing rather than picking the one that reads better."""
    import bridge.screen as S
    import bridge.window as W

    def _boom(_w):
        raise AttributeError("no window rect")

    frame = np.zeros((8, 8, 3), dtype=np.uint8)
    monkeypatch.setattr(S, "grab", lambda _win: frame)
    monkeypatch.setattr(S.time, "sleep", lambda _s: None)
    monkeypatch.setattr(W, "covers_a_monitor", _boom)

    live, why = S.display_is_live(object(), gap=1.2, samples=3)
    assert live is False
    assert "-fullscreen 0" in why and "both causes stand" in why


def test_keep_display_awake_and_wake_display_report_what_windows_said(
        monkeypatch):
    """Both are one syscall, and the return value is the only evidence the
    night gets that the request was accepted -- so it may not be invented."""
    import bridge.window as W

    calls = []
    monkeypatch.setattr(
        W.kernel32, "SetThreadExecutionState",
        lambda flags: calls.append(int(getattr(flags, "value", flags))) or 1)
    monkeypatch.setattr(W.user32, "mouse_event",
                        lambda *a: calls.append("move"))

    assert W.keep_display_awake(True) is True
    assert calls[0] & W.ES_DISPLAY_REQUIRED, "the DISPLAY flag is the point"
    assert calls[0] & W.ES_CONTINUOUS, "without ES_CONTINUOUS it is one-shot"

    calls.clear()
    assert W.keep_display_awake(False) is True
    assert calls[0] == W.ES_CONTINUOUS, "releasing keeps only ES_CONTINUOUS"

    calls.clear()
    assert W.wake_display() is True
    assert "move" in calls, "a request alone does not wake a dark monitor"


def test_a_refused_request_is_reported_as_refused(monkeypatch):
    """`SetThreadExecutionState` returning 0 means the display may still
    sleep, and the night has to say so rather than claim it is safe."""
    import bridge.window as W

    monkeypatch.setattr(W.kernel32, "SetThreadExecutionState", lambda _f: 0)
    assert W.keep_display_awake(True) is False

# -- the hunger PANEL, matched without the room behind it -------------------

def test_the_hunger_panel_is_found_in_every_room_it_was_banked_in():
    """**A floating panel is the wrong thing to match whole-screen.** `hunger`
    was banked three times -- plain, dark, and over a corridor -- and the
    fourth room it appeared in read UNKNOWN, because a whole-frame reference
    carries the room art behind the panel. `_answer_hunger` then refuses to
    click and the modal swallows every other input, which deadlocked a raid
    on 2026-09-21 with a lone Grave Robber on 3 HP at 157 stress.
    """
    import bridge.screen as S
    from PIL import Image

    banked = sorted(S.CAPTURES_SCREENS.glob("hunger*.png"))
    assert banked, "no hunger captures to check against"
    for path in banked:
        img = np.asarray(Image.open(path).convert("RGB"))
        assert S.hunger_panel_is_up(img), path.name


def test_it_does_not_fire_on_screens_that_are_NOT_hunger():
    """The measured gap is three orders of magnitude -- 0.000-0.002 for the
    hunger frames against 0.792 for the nearest other screen."""
    import bridge.screen as S
    from PIL import Image

    others = [p for p in sorted(S.CAPTURES_SCREENS.glob("*.png"))
              if "hunger" not in p.name]
    assert others, "no other captures to check against"
    for path in others:
        img = np.asarray(Image.open(path).convert("RGB"))
        assert not S.hunger_panel_is_up(img), path.name


def test_the_title_card_is_found_whatever_the_dungeon():
    """The Cove and the Weald cards each stalled a raid as unbanked variants
    (2026-09-23). The fixed lower half is the same in every region, so match
    that -- every banked card must pass and no other banked screen may."""
    import bridge.screen as S
    from PIL import Image

    cards = sorted(S.CAPTURES_SCREENS.glob("dungeon_intro*.png"))
    assert len(cards) >= 3
    for path in sorted(S.CAPTURES_SCREENS.glob("*.png")):
        img = np.asarray(Image.open(path).convert("RGB"))
        assert S.title_card_is_up(img) is (path in cards), path.name
    assert S.title_card_is_up(None) is False


def test_enemy_bars_tell_a_finished_fight_from_a_live_one():
    """A live fight shows red under the enemy ranks -- even a last sliver of
    hp -- and exploration shows none. Held on the banked title and town
    screens, which have no enemies and must read exactly 0."""
    import bridge.screen as S
    from PIL import Image

    for name in ("hamlet.png", "dungeon_intro@cove.png"):
        img = np.asarray(Image.open(S.CAPTURES_SCREENS / name).convert("RGB"))
        assert S.enemy_bars_drawn(img) == 0.0, name
    fake = np.zeros((720, 1280, 3), dtype=np.uint8)
    fake[462:468, 740:760] = (200, 20, 20)          # a last sliver of hp
    assert S.enemy_bars_drawn(fake) > 0.0
    assert S.enemy_bars_drawn(None) is None


def test_the_progression_strip_is_found_on_every_progression_page():
    """The page's art is the dungeon's own backdrop, so it misses whole-frame
    refs region by region; its bottom strip does not (2026-09-23)."""
    import bridge.screen as S
    from PIL import Image

    for path in sorted(S.CAPTURES_SCREENS.glob("*.png")):
        img = np.asarray(Image.open(path).convert("RGB"))
        want = path.name.startswith("raid_progression")
        assert S.progression_strip_is_up(img) is want, path.name


def test_a_journal_page_is_found_and_nothing_else_is():
    """A curio's journal page sat unanswered over a Cove corridor for six
    minutes on 2026-09-23. Matched on its close X and wax seal."""
    import bridge.screen as S
    from PIL import Image

    assert S.journal_page_is_up(np.asarray(
        Image.open(S.JOURNAL_REF).convert("RGB")))
    for path in sorted(S.CAPTURES_SCREENS.glob("*.png")):
        img = np.asarray(Image.open(path).convert("RGB"))
        assert not S.journal_page_is_up(img), path.name
    assert S.journal_page_is_up(None) is False


def test_a_results_page_is_found_by_its_header_and_strip():
    """A completed Cove quest's results page, with a gem tooltip up, went
    unrecognised (2026-09-23). Header crest AND results strip: every banked
    results page passes, nothing else does -- the Hamlet's Embark button
    shares the strip, and the header is what rules it out."""
    import bridge.screen as S
    from PIL import Image

    live = np.asarray(Image.open(S.CAPTURES_SCREENS.parent
                                 / "results_complete_escape.png").convert("RGB"))
    assert S.recognise(live) == "raid_results"
    for path in sorted(S.CAPTURES_SCREENS.glob("*.png")):
        img = np.asarray(Image.open(path).convert("RGB"))
        assert S.results_page_is_up(img) is path.name.startswith(
            "raid_results"), path.name


def test_the_help_card_is_found_and_nothing_else_is():
    """Fran's pause switch (2026-09-23): the backend holds every input while
    the game's help card is open."""
    import bridge.screen as S
    from PIL import Image

    assert S.help_card_is_up(np.asarray(Image.open(S.HELP_CARD_REF).convert("RGB")))
    for path in sorted(S.CAPTURES_SCREENS.glob("*.png")):
        img = np.asarray(Image.open(path).convert("RGB"))
        assert not S.help_card_is_up(img), path.name
    assert S.help_card_is_up(None) is False


def test_a_building_panel_is_known_by_its_title_plate():
    """The coach panel with the coachman's speech bubble over it failed the
    whole-frame match on 2026-09-23 and the town visit was skipped. The title
    plate identifies it; no screen that is not a panel matches."""
    import bridge.screen as S
    from PIL import Image

    live = np.asarray(Image.open(S.CAPTURES_SCREENS.parent
                                 / "coach_panel_speech_bubble.png").convert("RGB"))
    assert S.panel_title_match(live) == "stage_coach_panel"
    for path in sorted(S.CAPTURES_SCREENS.glob("*.png")):
        img = np.asarray(Image.open(path).convert("RGB"))
        got = S.panel_title_match(img)
        assert (got != "") == ("_panel" in path.name), path.name


def test_an_unreadable_frame_is_not_a_hunger_panel():
    """It runs ahead of every layer on every update, so it must cost nothing
    and claim nothing when the capture fails."""
    import bridge.screen as S

    assert S.hunger_panel_is_up(None) is False

