"""Read a `bridge/recorder.py` session: what was played, how fast, and where.

    python tools/recording_report.py                      # the latest session
    python tools/recording_report.py archive/recordings/20260929-201500
    python tools/recording_report.py --clicks             # every labelled click
    python tools/recording_report.py --marks              # every F8 press, with its frame
    python tools/recording_report.py --unpack DIR         # replay-ready saves
    python bridge/run.py --replay DIR                     # ...then mark them

The recorder keeps raw input and a frame; everything that INTERPRETS lives
here, so a better labeller re-reads every old session for free.

**Click labels are the bridge's own geometry.** A click is named after the
nearest point the bridge itself would click -- hero/enemy ranks, skill slots
and pack cells from `bridge/layout.py`'s calibrated layout, plus every named
client-fraction constant in `layout.py` and `townlayout.py` -- within
`LABEL_RADIUS` of the client width. So "the human clicked X" and "the bridge
clicks X" are the same statement, which is what an agreement measure needs.
Anything farther than that is `?`, never a guess.

**`--unpack` rebuilds the triples `run.py --replay` reads.** The recorder
banks a file only when its contents change, so a raid snapshot usually has
no roster beside it. Unpacking pairs each raid snapshot with the latest
roster and map banked at or before it, under one shared prefix -- the pairing
`tools/save_watch.py --capture` produced by copying the roster every time.
"""
from __future__ import annotations

import argparse
import collections
import gzip
import json
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

DEFAULT_ROOT = REPO / "archive" / "recordings"
#: A click farther than this from every known point (fraction of client
#: width) is unlabelled. ~26 px at 1280 wide: a skill icon is ~70.
LABEL_RADIUS = 0.02
#: The key Fran presses to flag "look at this moment". F8 because F9 is the
#: bridge's kill key (`actuation/keys.VK_KILL`) and F12 is Steam's screenshot.
MARK_KEY = "f8"


def load(session: Path) -> list[dict]:
    out = []
    path = Path(session) / "events.jsonl"
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            continue                  # a line torn by a crash: skip, keep going
    return annotate_context(out)


def latest(root: Path = DEFAULT_ROOT) -> Optional[Path]:
    sessions = sorted(p for p in Path(root).glob("*") if (p / "events.jsonl").exists())
    return sessions[-1] if sessions else None


# ---------------------------------------------------------------------------
# Click labels
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Target:
    name: str
    fx: float
    fy: float


class _Win:
    """A stand-in `GameWindow` at the client origin, for `ResolvedLayout`."""

    def __init__(self, w: int, h: int):
        self.client_size = (w, h)
        self.client_origin = (0, 0)


def _is_frac_pair(v) -> bool:
    return (isinstance(v, tuple) and len(v) == 2
            and all(isinstance(x, (int, float)) for x in v)
            and 0.0 < v[0] < 1.0 and 0.0 < v[1] < 1.0)


def named_constants() -> list[Target]:
    """Every module-level client-fraction point in the two layout modules."""
    out: list[Target] = []
    from bridge import layout as L, townlayout as T     # noqa: PLC0415
    for mod, prefix in ((L, ""), (T, "town.")):
        for name, val in vars(mod).items():
            if not name.isupper() or name.startswith("_"):
                continue
            if _is_frac_pair(val):
                out.append(Target(prefix + name.lower(), float(val[0]),
                                  float(val[1])))
            elif isinstance(val, dict):
                for k, v in val.items():
                    if _is_frac_pair(v):
                        out.append(Target(f"{prefix}{name.lower()}.{k}",
                                          float(v[0]), float(v[1])))
    return out


def board_targets(w: int, h: int) -> list[Target]:
    """Ranks, skill slots and pack cells, through the calibrated layout."""
    out: list[Target] = []
    try:
        from bridge import layout as L                    # noqa: PLC0415
        frac = L.load()
    except Exception:                                     # noqa: BLE001
        frac = None
    if frac is None or not w or not h:
        return out
    res = L.ResolvedLayout(frac, _Win(w, h))
    for side in ("hero", "enemy"):
        for r in range(4):
            p = res.point(side, r)
            if p:
                out.append(Target(f"{side}_rank{r + 1}", p[0] / w, p[1] / h))
    for s in range(1, 7):
        p = res.skill_point(s)
        if p:
            label = {5: "move", 6: "pass"}.get(s, f"skill{s}")
            out.append(Target(f"bar.{label}", p[0] / w, p[1] / h))
    for i in range(getattr(L, "INVENTORY_SLOTS", 16)):
        p = res.inventory_point(i)
        if p:
            out.append(Target(f"pack{i}", p[0] / w, p[1] / h))
    return out


class Labeller:
    def __init__(self):
        self._consts = named_constants()
        self._board: dict[tuple[int, int], list[Target]] = {}

    def targets(self, w: int, h: int) -> list[Target]:
        if (w, h) not in self._board:
            self._board[(w, h)] = board_targets(w, h)
        return self._board[(w, h)] + self._consts

    def label(self, ev: dict) -> str:
        fx, fy, w, h = ev.get("fx"), ev.get("fy"), ev.get("w"), ev.get("h")
        if fx is None or fy is None or not w or not h:
            return "?"
        aspect = h / w
        best, dist = "?", LABEL_RADIUS + 1e-9
        for t in self.targets(w, h):
            # Distance in units of client WIDTH on both axes, so a radius is
            # round on a 16:9 client rather than squashed. Strict `<`: two
            # names for one pixel (`MODAL_OK` and `MENU_POINTS["modal_ok"]`)
            # go to the first, which is the plain constant.
            d = ((fx - t.fx) ** 2 + ((fy - t.fy) * aspect) ** 2) ** 0.5
            if d < dist:
                best, dist = t.name, d
        return best


# ---------------------------------------------------------------------------
# The summary
# ---------------------------------------------------------------------------

def screen_of(ev: dict) -> str:
    """The recogniser's name, else `raid?`/`town?` from the saves, else `?`.

    The recogniser names menus and panels; it has no reference for the raid
    itself, so in the first real session 74% of presses read `?`. The saves
    know which side of the gate the party is on, and the `?` suffix keeps
    "inferred" distinguishable from "seen".
    """
    if ev.get("screen"):
        return ev["screen"]
    return f"{ev['ctx']}?" if ev.get("ctx") else "?"


#: Writes that only happen on one side of the gate. `persist.raid.json` is
#: written per turn in a raid and not at all in the Hamlet; these two are the
#: Hamlet's. Measured on the first session: every press the recogniser named
#: `hunger` or `quest_complete` fell on the raid side, every `hamlet`,
#: `stage_coach_panel` and `estate_map` press on the town side.
RAID_FILES = {"persist.raid.json"}
TOWN_FILES = {"persist.town.json", "persist.estate.json"}


def annotate_context(events: list[dict]) -> list[dict]:
    """Set `ctx` ("raid"/"town") on every input, from the latest save write
    before it. Baselines are skipped: they are copies, not writes."""
    last_raid = last_town = None
    for e in events:
        if e.get("kind") == "save" and not e.get("baseline"):
            if e.get("file") in RAID_FILES:
                last_raid = e["dt"]
            elif e.get("file") in TOWN_FILES:
                last_town = e["dt"]
        elif e.get("kind") in ("mouse", "key"):
            if last_raid is None and last_town is None:
                continue
            e["ctx"] = ("raid" if last_town is None
                        or (last_raid is not None and last_raid > last_town)
                        else "town")
    return events


def summary(events: list[dict], labeller: Optional[Labeller] = None) -> str:
    labeller = labeller or Labeller()
    start = next((e for e in events if e["kind"] == "session_start"), None)
    end = next((e for e in reversed(events) if e["kind"] == "session_end"), None)
    span = (events[-1]["dt"] if events else 0.0)
    lines = [f"session: {start.get('out') if start else '?'}",
             f"  length {span / 60:.1f} min"
             + (f", ended cleanly" if end else ", NO session_end (killed?)")]

    focus_in = _focused_seconds(events)
    lines.append(f"  game focused {focus_in / 60:.1f} min")

    inputs = [e for e in events if e["kind"] in ("mouse", "key")]
    presses = [e for e in inputs if e.get("action") in ("down", "wheel")]
    kinds = collections.Counter(
        f"{e['kind']}:{e.get('button') or e.get('key')}" for e in presses)
    lines.append(f"  {len(presses)} presses: " + ", ".join(
        f"{k} {n}" for k, n in kinds.most_common(12)))

    by_screen = collections.Counter(screen_of(e) for e in presses)
    lines.append("  presses by screen: " + ", ".join(
        f"{s} {n}" for s, n in by_screen.most_common(12)))

    # Pace: the gap between consecutive presses, by the screen of the second.
    gaps: dict[str, list[float]] = collections.defaultdict(list)
    for a, b in zip(presses, presses[1:]):
        gap = b["dt"] - a["dt"]
        if 0 < gap < 30:                  # longer is thinking or away
            gaps[screen_of(b)].append(gap)
    if gaps:
        lines.append("  pace (median s between presses): " + ", ".join(
            f"{s} {statistics.median(g):.2f}"
            for s, g in sorted(gaps.items(), key=lambda kv: -len(kv[1]))[:10]))

    clicks = [e for e in presses if e["kind"] == "mouse"
              and e.get("button") in ("left", "right")]
    labels = collections.Counter(labeller.label(e) for e in clicks)
    lines.append(f"  {len(clicks)} clicks, "
                 f"{sum(n for k, n in labels.items() if k != '?')} labelled; top: "
                 + ", ".join(f"{k} {n}" for k, n in labels.most_common(15)))

    saves = [e for e in events if e["kind"] == "save"]
    by_file = collections.Counter(e["file"] for e in saves)
    profiles = sorted({e["profile"] for e in saves})
    lines.append(f"  {len(saves)} save snapshots from {', '.join(profiles) or '-'}: "
                 + ", ".join(f"{f.replace('persist.', '')} {n}"
                             for f, n in by_file.most_common()))
    marks = [e for e in events if e["kind"] == "key"
             and e.get("key") == MARK_KEY and e.get("action") == "down"]
    if marks:
        lines.append(f"  {len(marks)} marks ({MARK_KEY.upper()}): "
                     + ", ".join(f"{m['dt'] / 60:.1f}min" for m in marks))
    if end and end.get("injected_ignored"):
        lines.append(f"  ({end['injected_ignored']} injected inputs ignored)")
    return "\n".join(lines)


def _focused_seconds(events: list[dict]) -> float:
    total, since = 0.0, None
    for e in events:
        if e["kind"] == "focus":
            if e.get("state") == "in" and since is None:
                since = e["dt"]
            elif e.get("state") == "out" and since is not None:
                total += e["dt"] - since
                since = None
    if since is not None and events:
        total += events[-1]["dt"] - since
    return total


def clicks_table(events: list[dict], labeller: Optional[Labeller] = None) -> str:
    labeller = labeller or Labeller()
    rows = []
    for e in events:
        if e["kind"] == "mouse" and e.get("action") == "down":
            rows.append(f"{e['dt']:9.2f}  {e.get('button', ''):6s} "
                        f"{screen_of(e):28s} {labeller.label(e):30s} "
                        f"({e.get('fx')}, {e.get('fy')})  {e.get('frame', '')}")
        elif e["kind"] == "key" and e.get("action") == "down":
            rows.append(f"{e['dt']:9.2f}  key    {screen_of(e):28s} "
                        f"{e.get('key', ''):30s} {'+'.join(e.get('mods') or [])}")
    return "\n".join(rows)


# ---------------------------------------------------------------------------
# Unpacking for run.py --replay
# ---------------------------------------------------------------------------

def unpack(session: Path, dest: Path, profile: Optional[str] = None) -> int:
    """Write `NNNNNN_persist.{raid,roster,map}.json` triples into `dest`.

    Returns the number of raid snapshots written. `profile` picks one when a
    session touched several; by default the one with the most raid writes.
    """
    events = load(session)
    saves = [e for e in events if e["kind"] == "save"]
    if profile is None:
        raids = collections.Counter(e["profile"] for e in saves
                                    if e["file"] == "persist.raid.json")
        if not raids:
            return 0
        profile = raids.most_common(1)[0][0]
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    have: dict[str, bytes] = {}
    written = 0
    for e in saves:
        if e["profile"] != profile:
            continue
        data = gzip.decompress((Path(session) / e["snap"]).read_bytes())
        have[e["file"]] = data
        if e["file"] != "persist.raid.json" or "persist.roster.json" not in have:
            continue
        written += 1
        prefix = f"{written:06d}_"
        for name in ("persist.raid.json", "persist.roster.json",
                     "persist.map.json"):
            if name in have:
                (dest / (prefix + name)).write_bytes(have[name])
    return written


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("session", nargs="?", default=None,
                    help="a session directory (default: the latest)")
    ap.add_argument("--clicks", action="store_true",
                    help="every press, labelled, with its frame")
    ap.add_argument("--marks", action="store_true",
                    help=f"only the {MARK_KEY.upper()} presses and their frames")
    ap.add_argument("--unpack", metavar="DIR", default=None,
                    help="write replay-ready raid/roster/map triples to DIR")
    ap.add_argument("--profile", default=None,
                    help="with --unpack: which profile (default: most raid writes)")
    args = ap.parse_args(argv)

    session = Path(args.session) if args.session else latest()
    if session is None or not (session / "events.jsonl").exists():
        print("no recording found under archive/recordings/")
        return 1
    events = load(session)
    if args.unpack:
        n = unpack(session, Path(args.unpack), args.profile)
        print(f"{n} raid snapshot(s) -> {args.unpack}")
        print(f"  python bridge/run.py --replay {args.unpack}")
        return 0
    print(summary(events))
    if args.marks:
        for e in events:
            if (e["kind"] == "key" and e.get("key") == MARK_KEY
                    and e.get("action") == "down"):
                print(f"  mark at {e['dt'] / 60:.1f} min on "
                      f"{screen_of(e)}  {session / e.get('frame', '')}")
    if args.clicks:
        print()
        print(clicks_table(events))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
