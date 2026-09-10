"""Who is the game asking? Read it off the combat hero panel.

**The save is wrong about whose turn it is on about 2% of raid turns**, and
the failure is silent and total: the digit selects the OTHER hero's skill, the
click aims where the advised hero's target is, nothing lands, and 15 seconds
burn per attempt. `LiveState.next_hero(skip)` recovers by guessing; this knows.

Caught live 2026-09-06 and this is the frame that named it: the log said
"Antiquarian (rank 3)" and the panel said "Verrall / Duelist", with the
Duelist's slot 3 drawn SELECTED -- gold border, diamond marker -- because the
keystroke had landed on her bar. Six misses in one raid, 15 s each.

DD draws the acting hero's portrait at the left of the combat HUD, and it is
the same `*_portrait_roster.png` art the stage coach and the roster list use,
so the machinery to identify it already existed one module away.

Measured on four banked frames, two of them at a different resolution and
matched against all twenty classes rather than a party of four:

    miss2 (Duelist asked)   -> duelist        cost  6.28  gap 12.46
    miss1 (Duelist asked)   -> duelist        cost  6.28  gap 12.46
    shieldbreaker fixture   -> shieldbreaker  cost 22.77  gap 11.38
    plague doctor fixture   -> plague_doctor  cost 25.14  gap 18.22

Four for four. **The GAP is the statistic, not the cost** -- a low-resolution
frame costs 25 where a 720p one costs 6, while the gap stays above 11 in both.
A crop that has slipped off the art matches nothing in particular and scores a
gap around 5, which is where `MATCH_MARGIN` sits between.

Like `bridge/actionbar.py`, this is a ONE-SIDED reader: it names a hero or it
refuses. Refusing is the safe answer, because the caller's fallback is the
save and the save is right 98% of the time.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Iterable, Optional

#: Centre of the panel portrait as a fraction of the CLIENT rect, and its
#: half-size as a fraction of the WIDTH so the box stays square in pixels.
#: Measured off a banked 1280x720 frame: the framed art runs x 182-238,
#: y 502-558.
PORTRAIT_CENTRE = (210.0 / 1280, 530.0 / 720)
PORTRAIT_HALF = 28.0 / 1280

#: Resolution the patch and the reference art are compared at.
PATCH = 32

#: How far the best class must beat the runner-up before this will name it.
#: The four true readings scored 11.38, 12.46, 12.46 and 18.22; a crop that
#: has slipped off the art scores about 5. The point is not where exactly the
#: line sits, it is that the two populations do not overlap.
MATCH_MARGIN = 8.0


@lru_cache(maxsize=4)
def portrait_refs(game_dir: str, size: int = PATCH) -> dict:
    """`{class: (art, ...)}` from the game's own roster portraits.

    Every class ships one per variant (`<class>_A..D`) and all of them are
    kept: a hero's variant is not in the save, so a match takes the best of
    the four. DLC classes live under `dlc/**` and are globbed the same way,
    which is how the Duelist -- a Fire's Edge class -- is reachable at all.

    Memoised, because this is on the combat poll loop and it reads ~80 PNGs.
    """
    import numpy as np                                    # noqa: PLC0415
    from PIL import Image                                 # noqa: PLC0415

    root = Path(game_dir)
    out: dict = {}
    for pattern in ("heroes/*/*_?/*_portrait_roster.png",
                    "dlc/**/heroes/*/*_?/*_portrait_roster.png"):
        for path in root.glob(pattern):
            hero_class = path.parts[-2].rsplit("_", 1)[0]
            try:
                art = Image.open(path).convert("RGB").resize((size, size))
            except OSError:
                continue
            out.setdefault(hero_class, []).append(np.asarray(art, dtype=float))
    return {k: tuple(v) for k, v in out.items()}


def read(img, classes: Iterable[str], game_dir) -> Optional[str]:
    """Which class the game is asking, or None when it cannot tell.

    `classes` narrows the pool to the party, which is both cheaper and more
    discriminating -- but it is not required to be the party, and two of the
    fixtures above are matched against all twenty.

    None on every unreadable thing: no numpy, no Pillow, no art, a frame of
    the wrong shape, a box that falls off the edge, or two classes too close
    to separate. The caller falls back to the save, which is right 98% of the
    time, so a refusal costs nothing that was not already being paid.
    """
    try:
        import numpy as np                                # noqa: PLC0415
        from PIL import Image                             # noqa: PLC0415
    except Exception:                                     # noqa: BLE001
        return None
    if img is None:
        return None
    try:
        refs = portrait_refs(str(game_dir))
    except Exception:                                     # noqa: BLE001
        return None
    pool = [c for c in dict.fromkeys(classes) if c in refs]
    if len(pool) < 2:
        # One candidate cannot be CONFIRMED by a comparison and zero has
        # nothing to say. Both are refusals rather than a free "yes".
        return None
    arr = np.asarray(img)
    if arr.ndim != 3 or arr.shape[2] < 3:
        return None
    height, width = arr.shape[:2]
    half = PORTRAIT_HALF * width
    cx, cy = PORTRAIT_CENTRE[0] * width, PORTRAIT_CENTRE[1] * height
    x0, y0 = int(cx - half), int(cy - half)
    x1, y1 = int(cx + half), int(cy + half)
    if x0 < 0 or y0 < 0 or x1 > width or y1 > height or x1 - x0 < 8:
        return None
    patch = np.asarray(
        Image.fromarray(arr[y0:y1, x0:x1, :3].astype("uint8"))
        .resize((PATCH, PATCH)), dtype=float)
    scored = sorted(
        (min(float(np.abs(patch - a).mean()) for a in refs[c]), c)
        for c in pool)
    if scored[1][0] - scored[0][0] < MATCH_MARGIN:
        return None
    return scored[0][1]


if __name__ == "__main__":                                # pragma: no cover
    import sys

    import numpy as np
    from PIL import Image

    from bridge.townplan import GAME_DIR

    frame = sys.argv[1]
    party = sys.argv[2:] or list(portrait_refs(str(GAME_DIR)))
    got = read(np.asarray(Image.open(frame).convert("RGB")), party, GAME_DIR)
    print(f"{frame}: {got or 'cannot tell'}")
