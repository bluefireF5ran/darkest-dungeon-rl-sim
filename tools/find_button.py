"""Locate a HUD button on a live frame from the game's OWN shipped icon.

    python tools/find_button.py --icon panels/quest_return_to_hamlet.png
    python tools/find_button.py --icon panels/party_order_button.png --frame f.png
    python tools/find_button.py --all-quest          # the raid-end choice pair

`bridge/layout.py` stores every HUD point as a fraction of the client rect, and
most of them were hand-clicked through `overlay.py --calibrate`. Two were not:
`PARTY_ORDER` and the pack grid were MEASURED, because there is nothing a human
can usefully click on for them and a hand-clicked value would be worse.

That measurement was done once, by hand, and the number written down -- which is
why `BRIDGE.md` can quote "0.9767 at scale 1.35" for the party-order button and
nothing can reproduce it. This turns that one-off into a command, so the next
button costs a minute instead of a session. It is what the curio panel needs
too: acting on a curio needs the panel FOUND on screen, and its button row's y
flows with the description length, so no constant can stand in for it.

**Diagnostics only, and deliberately so.** It imports OpenCV, which nothing
under `bridge/` or `engine/` may depend on: the output is a fraction to paste
into `layout.py`, so the runtime keeps needing nothing but numpy. Same split as
Pillow, which is dev-only for the same reason.

The match is alpha-weighted normalised cross-correlation over a scale sweep. DD
scales its 16:9 layout to fill whatever rect it is given, so the icon is drawn
at some multiple of its shipped size that depends on the window -- the scale is
therefore searched, never assumed, and it is REPORTED so a wildly wrong one is
visible rather than silently accepted.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from data.paths import GAME_DIR

# A match below this is reported but never trusted. The party-order button --
# the one case with a known-good answer -- scored 0.9767 against 0.4778 for the
# best match anywhere else, so the two populations are far apart and the
# threshold does not need to be delicate.
GOOD = 0.90


def load_icon(path: Path):
    """RGB and alpha, both float32. Alpha is the mask: DD's icons are cut out,
    so scoring their transparent margin would match any dark rectangle."""
    from PIL import Image                       # noqa: PLC0415  (dev-only)
    im = Image.open(path).convert("RGBA")
    a = np.asarray(im, dtype=np.float32)
    return a[..., :3], a[..., 3:4] / 255.0


def match(frame: np.ndarray, icon_rgb, icon_a, scales) -> dict:
    import cv2                                  # noqa: PLC0415  (dev-only)
    best = {"score": -1.0}
    fr = np.asarray(frame, dtype=np.float32)
    for s in scales:
        h = max(1, int(round(icon_rgb.shape[0] * s)))
        w = max(1, int(round(icon_rgb.shape[1] * s)))
        if h >= fr.shape[0] or w >= fr.shape[1]:
            continue
        tpl = cv2.resize(icon_rgb, (w, h), interpolation=cv2.INTER_AREA)
        msk = cv2.resize(icon_a, (w, h), interpolation=cv2.INTER_AREA)
        if msk.max() <= 0:
            continue
        msk3 = np.repeat(msk[..., None] if msk.ndim == 2 else msk, 3, axis=2)
        res = cv2.matchTemplate(fr, tpl, cv2.TM_CCORR_NORMED, mask=msk3)
        res = np.nan_to_num(res, nan=-1.0, posinf=-1.0, neginf=-1.0)
        _, mx, _, loc = cv2.minMaxLoc(res)
        if mx > best["score"]:
            best = {"score": float(mx), "scale": float(s),
                    "x": int(loc[0]), "y": int(loc[1]), "w": w, "h": h}
    return best


def report(name: str, frame: np.ndarray, icon: Path, scales) -> dict:
    rgb, a = load_icon(icon)
    b = match(frame, rgb, a, scales)
    if b["score"] < 0:
        print(f"  {name:28s} NO MATCH")
        return b
    cx, cy = b["x"] + b["w"] / 2.0, b["y"] + b["h"] / 2.0
    fh, fw = frame.shape[:2]
    b["centre"] = (cx, cy)
    b["fraction"] = (cx / fw, cy / fh)
    flag = "" if b["score"] >= GOOD else "   <-- WEAK, do not bank this"
    print(f"  {name:28s} score {b['score']:.4f}  scale {b['scale']:.2f}  "
          f"centre ({cx:.0f},{cy:.0f})  fraction "
          f"({b['fraction'][0]:.4f}, {b['fraction'][1]:.4f}){flag}")
    return b


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--icon", action="append", default=[],
                    help="asset path, relative to the game dir or absolute")
    ap.add_argument("--all-quest", action="store_true",
                    help="the raid-end choice pair (return / continue)")
    ap.add_argument("--frame", help="a PNG; omit to grab the running game")
    ap.add_argument("--scale-min", type=float, default=0.25)
    ap.add_argument("--scale-max", type=float, default=2.5)
    ap.add_argument("--steps", type=int, default=60)
    args = ap.parse_args(argv)

    if args.frame:
        from PIL import Image                   # noqa: PLC0415  (dev-only)
        frame = np.asarray(Image.open(args.frame).convert("RGB"),
                           dtype=np.uint8)
        where = args.frame
    else:
        from bridge import screen, window       # noqa: PLC0415
        win = window.find_window()
        if win is None:
            print("Darkest.exe is not running (and no --frame given)")
            return 1
        frame = screen.grab(win)
        if frame is None:
            print("capture failed")
            return 1
        where = f"live client {frame.shape[1]}x{frame.shape[0]}"

    icons = list(args.icon)
    if args.all_quest:
        icons += ["panels/quest_return_to_hamlet.png",
                  "panels/quest_continue_raid.png"]
    if not icons:
        ap.error("give --icon or --all-quest")

    print(f"frame: {where}")
    scales = np.linspace(args.scale_min, args.scale_max, args.steps)
    worst = 1.0
    for spec in icons:
        p = Path(spec)
        if not p.is_absolute():
            p = GAME_DIR / spec
        if not p.exists():
            print(f"  {spec:28s} MISSING {p}")
            worst = 0.0
            continue
        worst = min(worst, report(p.stem, frame, p, scales)["score"])
    return 0 if worst >= GOOD else 1


if __name__ == "__main__":
    sys.exit(main())
