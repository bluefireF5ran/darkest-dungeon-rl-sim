"""F2's learning curve: every shipped version of the campaign, one point each.

    python -m campaign.curve                      # table to stdout
    python -m campaign.curve --png docs/figures/campaign_curve.png

Each point is 80 campaigns x 60 weeks at gear 0.5 (the s0 and s40 ruler
blocks pooled), mean +- SE. The steps are of two kinds and the figure keeps
them apart (a RULING is the expert's own call, shipped and measured): a WORLD step makes the simulator more faithful to the game (it
may make the campaign harder -- v5's quirks did), a DECISION step changes
what the player chooses. Only decision steps are improvements; the world
steps are why an early number cannot be quoted against a late one.
v7 (stepping down a difficulty) was harmful and reverted, so it is not on
the curve.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

LOGS = Path(__file__).resolve().parent.parent / "archive" / "logs" / "campaign"

#: (label, file stem, kind, what changed) -- CAMPAIGN_SIM.md E7-E14.
STEPS = [
    ("v1", "SW_ship_g0.5", "start", "F1 heuristics + gear budget 0.5"),
    ("v2", "SW_v2_g0.5", "world", "curio loot pays its draws (E8)"),
    ("v3", "SW_v3_g0.5", "world", "provisions basket used (E8/E9)"),
    ("v4", "SW_v4_g0.5", "world", "deathblow only on damage (E10)"),
    ("v5", "SW_v5_g0.5", "world", "quest-end quirks + diseases (E11)"),
    ("v6", "SW_v6_g0.5", "decision", "seated party keeps the level rule (E12)"),
    ("v8", "SW_v8_g0.5", "decision", "fewest under-level heroes seated (E14)"),
    ("v9", "SW_v9_g0.5", "ruling", "Fran: smith first, vanilla roster (E19)"),
    ("v10", "SW_v10_g0.5", "ruling", "Fran: boss parties at max level (E20)"),
    ("v11", "SW_v11_g0.5", "ruling", "Fran: the Sanitarium, option 4 (E21)"),
    ("v12", "SW_v12_g0.5", "world", "building gates (E23)"),
]
METRICS = [("dead", "heroes dead / 60 weeks", 8, "Fran: 8 by week 54"),
           ("success_rate", "quest success", None, None),
           ("plots", "bosses killed / 60 weeks", 16, "Fran: 16 by week 54")]


def _rows(stem: str) -> list[dict]:
    rows = []
    for block in ("s0", "s40"):
        f = LOGS / f"{stem}_{block}.jsonl"
        rows += [json.loads(x) for x in f.read_text().splitlines() if x.strip()]
    return rows


def _mean_se(xs: list[float]) -> tuple[float, float]:
    n = len(xs)
    m = sum(xs) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1)) if n > 1 else 0.0
    return m, sd / math.sqrt(n)


def curve() -> list[dict]:
    out = []
    for label, stem, kind, what in STEPS:
        rows = _rows(stem)
        point = {"label": label, "kind": kind, "what": what, "n": len(rows)}
        for key, *_ in METRICS:
            point[key] = _mean_se([float(r[key]) for r in rows])
        out.append(point)
    return out


def table(points: list[dict]) -> str:
    head = "| version | kind | change | n | " + " | ".join(k for k, *_ in METRICS) + " |"
    lines = [head, "|" + "---|" * (4 + len(METRICS))]
    for p in points:
        cells = [f"{p[k][0]:.2f} +- {p[k][1]:.2f}" for k, *_ in METRICS]
        lines.append(f"| {p['label']} | {p['kind']} | {p['what']} | {p['n']} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def plot(points: list[dict], path: str) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    ink, muted, grid, surface = "#1f1f1e", "#6b6a64", "#e4e3de", "#fcfcfb"
    colour = {"start": muted, "world": "#eb6834", "decision": "#2a78d6", "ruling": "#1baf7a"}
    fig, axes = plt.subplots(1, len(METRICS), figsize=(13, 4.2), facecolor=surface)
    xs = range(len(points))
    for ax, (key, title, ref, ref_label) in zip(axes, METRICS):
        ax.set_facecolor(surface)
        ys = [p[key][0] for p in points]
        ax.plot(xs, ys, color=grid, linewidth=2, zorder=1)
        for x, p in zip(xs, points):
            m, se = p[key]
            ax.errorbar(x, m, yerr=2 * se, fmt="o", markersize=8, color=colour[p["kind"]],
                        markeredgecolor=surface, markeredgewidth=2, elinewidth=2, capsize=0, zorder=2)
        if ref is not None:
            ax.axhline(ref, color=muted, linewidth=1, linestyle=(0, (4, 3)), zorder=0)
            ax.annotate(ref_label, (len(points) - 1, ref), xytext=(0, 4), textcoords="offset points",
                        ha="right", va="bottom", fontsize=8, color=muted)
        last = points[-1][key][0]
        ax.annotate(f"{last:.2f}" if key == "success_rate" else f"{last:.1f}",
                    (len(points) - 1, last), xytext=(8, 0), textcoords="offset points",
                    va="center", fontsize=9, color=ink)
        ax.set_title(title, fontsize=10, color=ink, loc="left")
        ax.set_xticks(list(xs), [p["label"] for p in points], fontsize=9, color=ink)
        ax.tick_params(axis="y", labelsize=8, colors=muted, length=0)
        ax.tick_params(axis="x", length=0)
        ax.grid(axis="y", color=grid, linewidth=0.8)
        ax.set_axisbelow(True)
        for side in ("top", "right", "left"):
            ax.spines[side].set_visible(False)
        ax.spines["bottom"].set_color(grid)
        ax.set_xlim(-0.4, len(points) - 0.4)
    handles = [plt.Line2D([], [], marker="o", linestyle="", markersize=8, color=colour[k], label=t)
               for k, t in (("start", "start"), ("world", "world step: simulator made more faithful"),
                            ("decision", "decision step: the player chooses better"),
                            ("ruling", "the expert's ruling, measured"))]
    fig.legend(handles=handles, loc="lower center", ncol=4, frameon=False, fontsize=9,
               labelcolor=ink, bbox_to_anchor=(0.5, -0.02))
    fig.suptitle("The campaign simulator, version by version (80 campaigns x 60 weeks each, mean +- 2 SE)",
                 fontsize=11, color=ink, x=0.01, ha="left")
    fig.tight_layout(rect=(0, 0.07, 1, 0.95))
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=150, facecolor=surface)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--png", help="also draw the figure here")
    args = ap.parse_args(argv)
    points = curve()
    print(table(points))
    if args.png:
        plot(points, args.png)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
