"""Read the trinket tier-list images into ids, by matching the game's own icons.

    python tools/trinket_tierlist.py                 # rebuild data/trinket_tiers.json
    python tools/trinket_tierlist.py --check         # print every tile and its score

Fran, 2026-10-01, on bosses: *"si me puedo asegurar un muy raro, como siempre
dan los bosses prefiero buscar una que me de una ventaja real"*, and he
pointed at five tier-list images, one per rarity
(`captures/trinket_tiers/tier_list_<n>.png`). They are icons only, so this
names each tile by template-matching it against every
`inv_trinket+<id>.png` the game ships, base and DLC.

**The tiles are centre crops of the 72x144 icon, scaled about 1.25x**, so the
first pass searches each tile's centre inside every icon at that scale. Eight
of the 180 tiles score below `ACCEPT` there, all Crimson Court art drawn at a
different scale, and a multi-scale pass settles them (every one above 0.77,
with the runner-up a different id far below). A tile still under `ACCEPT`
after both passes is reported and left out, never guessed.

**Two ids share one icon file**: `music_box` and `madman_2` are byte-identical.
`music_box` has no rarity in any entries file, so the shipped one wins.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from data.paths import GAME_DIR                          # noqa: E402
from data.trinket_order import _tables                   # noqa: E402

SHEETS = REPO / "captures" / "trinket_tiers"
OUT = REPO / "data" / "trinket_tiers.json"
BG = (26, 26, 23)                 # the tier list's empty-cell colour
LABEL_W, PITCH, CELL = 126, 91, 90
ACCEPT = 0.75
SCALES = (0.9, 1.0, 1.1, 1.25, 1.4, 1.6, 1.8, 2.0, 2.3)

#: The row labels, best first, as written on each sheet. A `torchless` row is
#: a mode-specific pick, not a rank, so it is kept apart.
SHEETS_META = {
    1: ("very_common", ["actually decent", "has actual use cases",
                        "niche but still useful sometimes",
                        "better than an empty slot", "nah"]),
    2: ("common", ["actually really good, might get value later on in game even",
                   "yippie", "wouldn't be crazy to run it a couple times",
                   "use it once, toss away later", "just sell"]),
    3: ("uncommon", ["useful through a big chunk of the game at the very least",
                     "very solid", "decent", "mid", "yucky"]),
    4: ("rare", ["torchless exclusive", "peak", "pretty good",
                 "p mid but still usable", "niche", "just sell"]),
    5: ("very_rare", ["torchless", "insane", "pretty good", "usable", "niche",
                      "sell / keep just to remove from pool"]),
}


def _icons() -> dict[str, Image.Image]:
    _, _, rarity = _tables()
    out: dict[str, Image.Image] = {}
    for f in sorted(Path(GAME_DIR).rglob("inv_trinket+*.png")):
        tid = f.name[len("inv_trinket+"):-4]
        if tid in out or (tid == "music_box" and "madman_2" in rarity):
            continue
        ic = Image.open(f).convert("RGBA")
        bg = Image.new("RGBA", ic.size, BG + (255,))
        bg.alpha_composite(ic)
        out[tid] = bg.convert("RGB")
    return out


def _bgr(img: Image.Image) -> np.ndarray:
    return cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2BGR)


def _bands(a: np.ndarray) -> list[tuple[int, int]]:
    """Tier rows, from colour changes down the label column."""
    col = a[:, 3].astype(int)
    edges = [0]
    for y in range(1, len(col)):
        if np.abs(col[y] - col[y - 1]).sum() > 30 and y - edges[-1] > 5:
            edges.append(y)
    edges.append(len(col))
    return [(edges[k], edges[k + 1]) for k in range(len(edges) - 1)
            if edges[k + 1] - edges[k] > 60]


def tiles(sheet: Path):
    """(row, line, col, 90x90 tile) for every non-empty cell, in reading order.

    A line is kept only when it fits inside its row: a tall label makes a row
    taller than its tiles, and the first version read the next row's tiles a
    second time through that gap."""
    a = np.asarray(Image.open(sheet).convert("RGB"))
    for row, (y0, y1) in enumerate(_bands(a)):
        line = 0
        while y0 + 2 + PITCH * line + CELL <= y1 + 1:
            y = y0 + 2 + PITCH * line
            for col in range(11):
                x = LABEL_W + PITCH * col
                tile = a[y:y + CELL, x:x + CELL]
                if tile.shape[:2] != (CELL, CELL):
                    continue
                if (np.abs(tile.astype(int) - BG).sum(2) > 15).mean() < 0.08:
                    continue
                yield row, line, col, tile
            line += 1


def match(tile: np.ndarray, icons: dict[str, Image.Image],
          scales=(1.25,)) -> list[tuple[float, str]]:
    t = _bgr(Image.fromarray(tile))[15:75, 15:75]
    best: dict[str, float] = {}
    for sc in scales:
        size = (int(72 * sc), int(144 * sc))
        for tid, ic in icons.items():
            r = cv2.matchTemplate(_bgr(ic.resize(size)), t, cv2.TM_CCOEFF_NORMED)
            best[tid] = max(best.get(tid, -1.0), float(r.max()))
    return sorted(((s, t) for t, s in best.items()), reverse=True)


def build(verbose: bool = False) -> dict:
    icons = _icons()
    _, _, rarity = _tables()
    entries, rejected = {}, []
    for n, (sheet_rarity, labels) in SHEETS_META.items():
        for row, line, col, tile in tiles(SHEETS / f"tier_list_{n}.png"):
            ranked = match(tile, icons)
            if ranked[0][0] < ACCEPT:
                ranked = match(tile, icons, SCALES)
            score, tid = ranked[0]
            where = f"sheet {n} row {row} line {line} col {col}"
            if verbose:
                print(f"{where:28s} {tid:28s} {score:.3f}  next {ranked[1][1]} "
                      f"{ranked[1][0]:.3f}")
            if score < ACCEPT:
                rejected.append({"where": where, "best": tid,
                                 "score": round(score, 3)})
                continue
            label = labels[row]
            torchless = label.startswith("torchless")
            ranks = [lab for lab in labels if not lab.startswith("torchless")]
            entries[tid] = {
                "sheet": sheet_rarity,
                "rarity": rarity.get(tid, ""),
                "tier": label,
                "rank": None if torchless else ranks.index(label),
                "of": len(ranks),
                "torchless": torchless,
                "score": round(score, 3),
            }
    return {"source": "captures/trinket_tiers/tier_list_<n>.png, matched by "
                      "tools/trinket_tierlist.py",
            "entries": dict(sorted(entries.items())), "rejected": rejected}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--check", action="store_true",
                    help="print every tile and its match; write nothing")
    args = ap.parse_args(argv)
    data = build(verbose=args.check)
    print(f"{len(data['entries'])} trinkets placed, "
          f"{len(data['rejected'])} tiles rejected")
    for r in data["rejected"]:
        print("  REJECTED", r)
    if not args.check:
        OUT.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
        print(f"-> {OUT.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
