"""Grade the damage rule against the running game: each damage draw the game
logged, paired with the HP its target lost in the next save.

    python tools/damage_oracle.py                         # latest recording
    python tools/damage_oracle.py archive/recordings/<session>
    python tools/damage_oracle.py --list                  # every paired hit
    python tools/damage_oracle.py --all                   # pooled over every session

Needs a session recorded with BOTH channels -- `bat\\record_play_full.bat`
(`bridge/recorder.py --trace`): the saves the recorder banks, and the game's
debug channel stamped on the same clock (`trace.log`).

**What it settles: DECOMP.md F9, rule 2.** The game cuts PROT as

    cut = roundf(p * dmg), at least 1 when p > 0;   final = max(0, dmg - cut)

while `engine/resolution.py` computes `max(1, round(dmg * (1 - p)))`. Over
typical ranges the two differ by 11.6% in expected damage, most on the weakest
attacks. Both are graded on every paired hit; the verdict is the count of
DISCRIMINATING hits -- the ones the two rules disagree on -- that each gets
right. A rule that loses one it should win is refuted on that hit alone.

**What is paired, and what is refused.** A hit is the `Random int range A,B`
draw inside one `bs_turn_presentation` .. `bs_after_turn` window. It is
scored only when the saves either side of it show exactly ONE enemy losing HP,
that enemy survives (a killing blow is capped by its HP), the window drew
exactly one damage roll (an AoE pairs ambiguously), no hero lost HP (then
the draw was a monster's attack and the enemy's loss is its own DoT tick), and
no other attack happened before the save. The target's PROT is read
from the engine seeded off the earlier save, so its buffs count. Crits are
not labelled in the trace; a drop that matches 150% of the maximum and not the
draw is counted as a crit and left out. Hits on HEROES are left out: a
monster's damage also carries the torch bracket's multiplier (F9), a second
unknown this oracle is not built to separate.
"""
from __future__ import annotations

import argparse
import gzip
import json
import math
import re
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

DEFAULT_ROOT = REPO / "archive" / "recordings"
#: The engine's own cap, which is also the game's PROT stat ceiling (F9).
PROT_CAP = 0.8
#: A save more than this long after the draw is not the one that recorded it.
PAIR_WINDOW = 15.0

_STAMP = re.compile(r"^@([\d.]+) \[\d+\]\s?(.*)$")
_ACTOR = re.compile(r"check pCurrentBattleActor #2: (.+?)\s+\d+\s*$")
_DRAW = re.compile(r"Battle Random int range A,B: (-?\d+) , (-?\d+) Value: (-?\d+)")


def roundf(x: float) -> int:
    """C's roundf: halves away from zero (Python's round() goes to even)."""
    return int(math.floor(x + 0.5)) if x >= 0 else -int(math.floor(-x + 0.5))


def game_damage(dmg: float, prot: float) -> int:
    p = max(0.0, min(prot, PROT_CAP))
    cut = roundf(p * dmg)
    if p > 0:
        cut = max(1, cut)
    return max(0, int(round(dmg - cut)))


def engine_damage(dmg: float, prot: float) -> int:
    p = max(0.0, min(prot, PROT_CAP))
    return max(1, round(dmg * (1.0 - p)))


@dataclass
class Attack:
    t_start: float
    t_end: float
    draws: list = field(default_factory=list)          # (t, lo, hi, value)
    actor: str = ""                    # display name of whoever is acting


def parse_trace(lines) -> list[Attack]:
    """Attack windows from a `--stamp`ed trace. Unstamped lines are skipped:
    without the clock there is nothing to pair them with."""
    out: list[Attack] = []
    cur: Optional[Attack] = None
    actor = ""
    for raw in lines:
        m = _STAMP.match(raw.rstrip("\n"))
        if not m:
            continue
        t, text = float(m.group(1)), m.group(2)
        am = _ACTOR.search(text)
        if am:
            actor = am.group(1)
        if "state change to bs_turn_presentation" in text:
            cur = Attack(t, t, actor=actor)
        elif cur is not None:
            d = _DRAW.search(text)
            if d:
                cur.draws.append((t, int(d.group(1)), int(d.group(2)), int(d.group(3))))
            if "state change to bs_after_turn" in text:
                cur.t_end = t
                out.append(cur)
                cur = None
    return out


@dataclass
class Snapshot:
    t: float
    enemies: dict                     # battle_guid -> (hp, prot, alive)
    heroes: dict = field(default_factory=dict)       # roster guid -> hp
    enemy_names: set = field(default_factory=set)


def score(attacks: list[Attack], snapshots: list[Snapshot]) -> list[dict]:
    """Pair each single-draw attack with the enemy HP change around it."""
    rows = []
    snaps = sorted(snapshots, key=lambda s: s.t)
    for a in attacks:
        if len(a.draws) != 1:
            continue
        t_draw, lo, hi, v = a.draws[0]
        if hi <= 0:
            # `A,B: 0 , 0` is a draw with nothing to choose -- not a damage
            # roll. The first live session paired three of them with unrelated
            # HP drops and they read as "discriminating".
            continue
        before = [s for s in snaps if s.t <= a.t_start]
        after = [s for s in snaps if a.t_end <= s.t <= a.t_end + PAIR_WINDOW]
        if not before or not after:
            continue
        s0, s1 = before[-1], after[0]
        # A MONSTER's attack draws too, and its target is a hero. The enemy HP
        # that falls in that window is the bleed or blight ticking at the start
        # of the monster's own turn -- four of the five "unexplained" hits of
        # the first live session (2026-10-04) were exactly that.
        if any(s1.heroes.get(g, hp) < hp - 1e-6 for g, hp in s0.heroes.items()):
            continue
        # ...and the save can be written before the hero's loss shows, so the
        # trace's own "who is acting" line is the direct test.
        if a.actor and a.actor in s0.enemy_names:
            continue
        # A second attack before the next save mixes two hits in one HP drop.
        if any(b is not a and b.draws and a.t_end < b.t_start < s1.t for b in attacks):
            continue
        lost = [(g, s0.enemies[g], s1.enemies[g]) for g in s0.enemies
                if g in s1.enemies and s1.enemies[g][0] < s0.enemies[g][0] - 1e-6]
        if len(lost) != 1:
            continue
        g, (hp0, prot, _), (hp1, _, alive1) = lost[0]
        if not alive1 or hp1 <= 0:
            continue                                  # a kill is capped by HP
        obs = int(round(hp0 - hp1))
        rows.append({
            "t": t_draw, "draw": v, "range": (lo, hi), "prot": round(prot, 3), "observed": obs,
            "game": game_damage(v, prot), "engine": engine_damage(v, prot),
            "crit_game": game_damage(hi * 1.5, prot),
        })
    for r in rows:
        r["crit"] = (r["observed"] == r["crit_game"]
                     and r["observed"] not in (r["game"], r["engine"]))
    return rows


def summarise(rows: list[dict]) -> str:
    scored = [r for r in rows if not r["crit"]]
    disc = [r for r in scored if r["game"] != r["engine"]]
    g_ok = sum(r["observed"] == r["game"] for r in scored)
    e_ok = sum(r["observed"] == r["engine"] for r in scored)
    dg = sum(r["observed"] == r["game"] for r in disc)
    de = sum(r["observed"] == r["engine"] for r in disc)
    lines = [
        f"{len(rows)} paired hits ({len(rows) - len(scored)} crit-shaped, left out)",
        f"  game rule (F9)   matches {g_ok}/{len(scored)}",
        f"  engine rule      matches {e_ok}/{len(scored)}",
        f"  DISCRIMINATING   {len(disc)} hits the rules disagree on: "
        f"game right {dg}, engine right {de}",
    ]
    if disc:
        if dg == len(disc):
            lines.append("  verdict: the game's rule, on every hit that could tell them apart")
        elif de == len(disc):
            lines.append("  verdict: the engine's rule -- F9 rule 2 is REFUTED")
        else:
            lines.append("  verdict: MIXED -- neither rule explains every hit; read --list")
    else:
        lines.append("  verdict: none yet -- no hit the two rules disagree on "
                     "(they differ most on low damage into PROT)")
    return "\n".join(lines)


def load_snapshots(session: Path, profile: Optional[str] = None,
                   prot_of: Optional[Callable] = None) -> list[Snapshot]:
    """Every in-battle raid snapshot, with each enemy's HP and seeded PROT."""
    from bridge.live_state import read_live_state                 # noqa: PLC0415
    if prot_of is None:
        prot_of = _seeded_prot()
    events = [json.loads(line) for line in
              (session / "events.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    saves = [e for e in events if e.get("kind") == "save"]
    if profile is None:
        counts: dict = {}
        for e in saves:
            if e["file"] == "persist.raid.json":
                counts[e["profile"]] = counts.get(e["profile"], 0) + 1
        profile = max(counts, key=counts.get) if counts else None
    cur: dict = {}
    tmp = Path(tempfile.mkdtemp())
    out = []
    for e in saves:
        if e["profile"] != profile:
            continue
        cur[e["file"]] = gzip.decompress((session / e["snap"]).read_bytes())
        if e["file"] != "persist.raid.json" or "persist.roster.json" not in cur:
            continue
        (tmp / "persist.raid.json").write_bytes(cur["persist.raid.json"])
        (tmp / "persist.roster.json").write_bytes(cur["persist.roster.json"])
        try:
            st = read_live_state(raid_file=tmp / "persist.raid.json",
                                 roster_file=tmp / "persist.roster.json",
                                 map_file=tmp / "absent.json")
        except Exception:                                         # noqa: BLE001
            continue
        if not st.in_battle:
            continue
        prots = prot_of(st)
        if prots is None:
            continue
        out.append(Snapshot(e["t"], {
            en.battle_guid: (float(en.hp), prots[i], not en.is_corpse and en.hp > 0)
            for i, en in enumerate(st.enemies)},
            {h.guid: float(h.hp) for h in st.heroes},
            {en.name for en in st.enemies if en.name}))
    return out


def _seeded_prot():
    from bridge.seed import build_engine_from_live, SeedError     # noqa: PLC0415
    from data.paths import CSV_DIR, GAME_DIR                      # noqa: PLC0415
    from data.store_cache import load_store_cached                # noqa: PLC0415
    store = load_store_cached(CSV_DIR, GAME_DIR)

    def prot_of(state):
        try:
            engine = build_engine_from_live(state, store)
        except (SeedError, Exception):                            # noqa: BLE001
            return None
        return [float(getattr(e, "prot", 0.0) or 0.0) for e in engine.enemies]
    return prot_of


def latest(root: Path = DEFAULT_ROOT) -> Optional[Path]:
    sessions = sorted(p for p in Path(root).glob("*") if (p / "trace.log").exists())
    return sessions[-1] if sessions else None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("session", nargs="?", type=Path)
    ap.add_argument("--profile")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--all", action="store_true",
                    help="pool every recorded session that has a trace.log: the "
                         "discriminating hits are rare, so the verdict accumulates")
    args = ap.parse_args(argv)
    if args.all:
        rows = []
        for sess in sorted(p for p in DEFAULT_ROOT.glob("*") if (p / "trace.log").exists()):
            attacks = parse_trace((sess / "trace.log").read_text(
                encoding="utf-8", errors="replace").splitlines())
            got = score(attacks, load_snapshots(sess, args.profile))
            print(f"  {sess.name}: {len(got)} paired hits")
            rows += got
        print(summarise(rows))
        return 0
    session = args.session or latest()
    if session is None or not (session / "trace.log").exists():
        print("no session with a trace.log -- record one with bat\\record_play_full.bat")
        return 1
    attacks = parse_trace((session / "trace.log").read_text(encoding="utf-8",
                                                           errors="replace").splitlines())
    snaps = load_snapshots(session, args.profile)
    rows = score(attacks, snaps)
    print(f"session {session}: {len(attacks)} attacks in the trace, "
          f"{len(snaps)} in-battle saves")
    print(summarise(rows))
    if args.list:
        for r in rows:
            mark = "crit" if r["crit"] else ("<- discriminating" if r["game"] != r["engine"] else "")
            print(f"  draw {r['draw']:>3} {r['range']} prot {r['prot']:.2f}: observed "
                  f"{r['observed']:>3}  game {r['game']:>3}  engine {r['engine']:>3}  {mark}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
