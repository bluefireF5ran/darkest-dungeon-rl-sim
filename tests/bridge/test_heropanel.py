"""`bridge/heropanel.py` -- who the GAME is asking, off the combat HUD.

The save is wrong about whose turn it is on about 2% of raid turns, and the
failure is silent and total. These pin the reader against frames whose answer
is known independently: two banked misses where the log names a different hero
than the panel does, and two action-bar fixtures named after their own hero.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

CAPTURES = Path(__file__).resolve().parents[2] / "captures"
from data.paths import GAME_DIR

#: The party in the raid the `battle_panel_duelist_asked` frame came from.
DUELIST_PARTY = ("bounty_hunter", "highwayman", "houndmaster", "duelist")


def _frame(name):
    pytest.importorskip("numpy")
    pytest.importorskip("PIL")
    import numpy as np
    from PIL import Image

    path = CAPTURES / name
    if not path.exists():
        pytest.skip(f"{name} is not banked")
    return np.asarray(Image.open(path).convert("RGB"))


def _refs():
    from bridge import heropanel

    if not GAME_DIR.exists():
        pytest.skip("the game is not installed at the hardcoded path")
    refs = heropanel.portrait_refs(str(GAME_DIR))
    if not refs:
        pytest.skip("no roster portrait art found")
    return refs


def test_it_names_the_hero_the_GAME_is_asking():
    """The frame that found the bug.

    The log said "Antiquarian (rank 3)"; the panel said "Verrall / Duelist",
    with the Duelist's slot 3 drawn selected because the keystroke had landed
    on HER bar. Six misses in one raid at 15 s each.
    """
    from bridge import heropanel

    _refs()
    got = heropanel.read(_frame("battle_panel_duelist_asked.png"),
                         DUELIST_PARTY, GAME_DIR)
    assert got == "duelist", got


def test_it_names_a_SECOND_hero_out_of_sample():
    """Banked after the constant was measured, from a different raid.

    The log said "Bounty Hunter (rank 2, 1/22 hp)"; the panel says
    `highwayman`, against the party pool AND against all twenty classes. The
    first frame is what the box was measured on, so it can only ever confirm
    the arithmetic -- this one is the reader being right about a hero it was
    not tuned to.
    """
    from bridge import heropanel

    refs = _refs()
    frame = _frame("battle_panel_highwayman_asked.png")
    assert heropanel.read(frame, DUELIST_PARTY, GAME_DIR) == "highwayman"
    assert heropanel.read(frame, tuple(refs), GAME_DIR) == "highwayman"


def test_it_works_at_another_RESOLUTION_and_against_every_class():
    """The two action-bar fixtures are 683x384 and named after their hero, so
    they are ground truth nobody chose for this test -- and they are matched
    against all twenty classes rather than a party of four.

    Worth having both: the box is a fraction of the client rect, so a fixed
    pixel box would pass the 720p frame and silently fail everywhere else.
    """
    from bridge import heropanel

    refs = _refs()
    every = tuple(refs)
    for name, want in (("battle_bar_shieldbreaker_rank1.png", "shieldbreaker"),
                       ("battle_bar_plague_doctor_rank1.png", "plague_doctor")):
        assert heropanel.read(_frame(name), every, GAME_DIR) == want, name


def test_the_GAP_is_the_statistic_and_it_does_not_overlap():
    """A low-resolution frame costs 25 where a 720p one costs 6, so an
    absolute cut would have to be set for the worse case and would then admit
    anything at the better one. The gap holds above 11 in both, while a crop
    that has slipped off the art scores about 5.

    This is the measurement `MATCH_MARGIN` is set from, re-run.
    """
    import numpy as np
    from PIL import Image

    from bridge import heropanel

    refs = _refs()

    def gap(frame, pool, dx=0, dy=0):
        height, width = frame.shape[:2]
        half = heropanel.PORTRAIT_HALF * width
        cx = heropanel.PORTRAIT_CENTRE[0] * width + dx
        cy = heropanel.PORTRAIT_CENTRE[1] * height + dy
        box = (int(cx - half), int(cy - half), int(cx + half), int(cy + half))
        patch = np.asarray(
            Image.fromarray(frame[box[1]:box[3], box[0]:box[2]])
            .resize((heropanel.PATCH, heropanel.PATCH)), dtype=float)
        scored = sorted((min(float(np.abs(patch - a).mean()) for a in refs[c]), c)
                        for c in pool)
        return scored[1][0] - scored[0][0]

    on_art = gap(_frame("battle_panel_duelist_asked.png"), DUELIST_PARTY)
    assert on_art > heropanel.MATCH_MARGIN, on_art

    # ...and slid a third of a box off it, which is what a mis-measured
    # constant looks like, the same comparison collapses.
    slipped = gap(_frame("battle_panel_duelist_asked.png"), DUELIST_PARTY,
                  dx=40, dy=40)
    assert slipped < heropanel.MATCH_MARGIN, slipped


def test_it_REFUSES_rather_than_guessing():
    """One-sided, like `actionbar.read` and `_panel_is_open`: it names a hero
    or it says nothing. The caller's fallback is the save, which is right 98%
    of the time, so a refusal costs nothing that was not already being paid --
    while a wrong name sends a keystroke to the wrong bar."""
    import numpy as np

    from bridge import heropanel

    _refs()
    frame = _frame("battle_panel_duelist_asked.png")
    assert heropanel.read(None, DUELIST_PARTY, GAME_DIR) is None
    assert heropanel.read(frame, (), GAME_DIR) is None
    # A pool of one cannot be CONFIRMED by a comparison.
    assert heropanel.read(frame, ("duelist",), GAME_DIR) is None
    # Nothing to match against.
    assert heropanel.read(frame, ("nosuchclass", "another"), GAME_DIR) is None
    # A frame of the wrong shape.
    assert heropanel.read(np.zeros((10, 10)), DUELIST_PARTY, GAME_DIR) is None


def test_it_is_a_READER_and_nothing_decides_with_it():
    """It may not reach the simulator. Same rule the rulers follow: a pixel
    reader that leaked into `engine/` or `env/` would make the training
    distribution depend on a screenshot."""
    root = Path(__file__).resolve().parents[2]
    for where in ("engine", "env", "data"):
        for path in (root / where).rglob("*.py"):
            assert "heropanel" not in path.read_text(encoding="utf-8",
                                                     errors="replace"), path


if __name__ == "__main__":                                  # pragma: no cover
    pytest.main([__file__, "-v"])
