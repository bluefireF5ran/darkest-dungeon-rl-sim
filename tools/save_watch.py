"""Watch the live save profile and report when the game rewrites it, and what changed.

Answers the question the bridge depends on: **does Darkest Dungeon persist party
state during a combat, or only between rooms?** The debug channel
(`tools/dbwin_capture.py`) never emits HP, stress, torch or buffs, so the save
files are the only candidate observation channel for driving the real game — and
they are only usable if they refresh often enough to act on.

Run this with the game already running, then play. One line per detected change.

    python tools/save_watch.py
    python tools/save_watch.py --interval 0.5 --duration 900

What to look for in the output:
  * `inraid` flipping to True and `raid_save` gaining children -> the live raid
    state lives there, and that is where torch level should be.
  * hero `hp`/`stress` lines arriving DURING a fight -> turn-level observation is
    possible, and the bridge can act inside combat.
  * changes only at room transitions -> the bridge can only observe between
    combats, and driving a fight turn-by-turn needs a different channel.

See CHANGELOG.md.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data.dson import parse_file

SAVE_ROOT = Path(r"D:\Program Files (x86)\Steam\userdata")
PROFILE_GLOB = "*/262060/remote/profile_*"

# Fields the bridge would actually need to read. Kept short on purpose: the
# point is to see WHEN the file moves, not to dump the save.
WATCHED = ("hp", "stress", "afflicted", "mode")


def find_profile(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit)
    profiles = sorted(SAVE_ROOT.glob(PROFILE_GLOB))
    if not profiles:
        raise SystemExit(f"no save profile found under {SAVE_ROOT}")
    # The one the game touched most recently is the one being played.
    return max(profiles, key=lambda p: max((f.stat().st_mtime for f in p.glob("persist.*.json")), default=0))


def stat_all(profile: Path) -> dict[str, tuple[float, int]]:
    """(mtime, size) per save file, WITHOUT opening any of them.

    This is the safe default. It already answers the granularity question —
    which files the game rewrites and when — and it cannot interfere with the
    game's own writes."""
    out = {}
    for f in profile.glob("persist.*.json"):
        try:
            st = f.stat()
            out[f.name] = (st.st_mtime, st.st_size)
        except OSError:
            continue
    return out


def snapshot_copy(profile: Path) -> dict:
    """Copy the two saves we care about, then parse the COPIES.

    Never parse in place. The first version of this tool did, and Darkest
    Dungeon crashed on dungeon entry twice in a row — the heaviest write burst
    it produces. Holding a read handle on a file the game is writing can fail
    that write."""
    tmp = Path(tempfile.gettempdir()) / "dd_save_watch"
    tmp.mkdir(exist_ok=True)
    staged = tmp
    for name in ("persist.game.json", "persist.roster.json"):
        src = profile / name
        for _ in range(3):
            try:
                shutil.copy2(src, staged / name)
                break
            except OSError:
                time.sleep(0.15)
        else:
            return {}
    return snapshot(staged)


def snapshot(profile: Path) -> dict:
    """Small, comparable view of the state a bridge would consume."""
    out: dict = {}
    try:
        game = parse_file(profile / "persist.game.json")
        inraid = game.get("inraid")
        raid = game.get("raid_save")
        out["inraid"] = None if inraid is None else inraid.value
        out["raid_save_fields"] = len(raid.children) if raid is not None and raid.children else 0
    except Exception as exc:
        out["game_error"] = str(exc)

    try:
        roster = parse_file(profile / "persist.roster.json")
        for hero in roster.get("heroes").children:
            rec = hero.get("hero_file_data").get("raw_data").value
            actor = rec.get("actor")
            hp = actor.get("current_hp") if actor is not None else None
            stress = rec.get("m_Stress")
            aff = rec.get("affliction_type_id")
            mode = actor.get("current_mode_id") if actor is not None else None
            out[f"hero{hero.name}"] = {
                "hp": None if hp is None else hp.as_float(),
                "stress": None if stress is None else stress.as_float(),
                "afflicted": None if aff is None else aff.value,
                "mode": None if mode is None else mode.value,
            }
    except Exception as exc:
        out["roster_error"] = str(exc)
    return out


def describe(old: dict, new: dict) -> list[str]:
    lines = []
    for key in sorted(set(old) | set(new)):
        a, b = old.get(key), new.get(key)
        if a == b:
            continue
        if isinstance(b, dict) and isinstance(a, dict):
            deltas = [f"{f} {a.get(f)}->{b.get(f)}" for f in WATCHED if a.get(f) != b.get(f)]
            if deltas:
                lines.append(f"{key}: " + ", ".join(deltas))
        else:
            lines.append(f"{key}: {a} -> {b}")
    return lines


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default=None, help="path to a profile_N directory")
    ap.add_argument("--interval", type=float, default=1.0, help="poll seconds")
    ap.add_argument("--duration", type=float, default=900.0, help="stop after N seconds")
    ap.add_argument("--capture", default=None, metavar="DIR",
                    help="copy every new version of the raid-only saves into DIR. "
                         "persist.raid.json and persist.map.json exist ONLY while a "
                         "raid is in progress and are deleted on the way out, so "
                         "they cannot be inspected afterwards — this is the only "
                         "way to get them. Copies are cheap (6-30 KB) and the "
                         "sequence across one combat is what shows which fields "
                         "move per turn.")
    ap.add_argument("--decode", action="store_true",
                    help="ALSO decode the save and report field changes. OFF by "
                         "default: the first version of this tool did it "
                         "unconditionally and the game crashed twice on dungeon "
                         "entry, which is the heaviest write burst in the game. "
                         "Reading a file the game is mid-write on can fail its "
                         "write. Even with this flag the save is copied first "
                         "and the COPY is parsed, so the game's handle is held "
                         "for as short as possible.")
    args = ap.parse_args()

    profile = find_profile(args.profile)
    print(f"watching {profile}")
    mode = "decode (copies first)" if args.decode else "stat only — never opens the save"
    print(f"mode: {mode}")
    print("(play the game; one line per change. Ctrl-C to stop)\n")

    state = snapshot_copy(profile) if args.decode else {}
    stats = stat_all(profile)
    start = time.time()
    run_id = time.strftime("%Y%m%d-%H%M%S")
    if args.capture:
        # Created here rather than assumed. `shutil.copy2` into a missing
        # directory raises, the retry loop used to swallow it, and the result
        # was a silent no-op: an hour of real play captured nothing and nobody
        # found out until the raid file had already been deleted.
        Path(args.capture).mkdir(parents=True, exist_ok=True)
        print(f"capture run id: {run_id} -> {Path(args.capture).resolve()}")
    writes = 0
    try:
        while time.time() - start < args.duration:
            time.sleep(args.interval)
            now = stat_all(profile)
            touched = [n for n, m in now.items() if stats.get(n) != m]
            if not touched:
                continue
            stats = now
            writes += 1
            stamp = f"[{time.time() - start:7.1f}s]"
            sizes = ", ".join(f"{n} ({now[n][1]}B)" for n in sorted(touched))
            print(f"{stamp} WROTE {sizes}")
            if args.capture:
                # The roster rides along with every raid write even when its
                # own mtime didn't move: without it a captured raid snapshot
                # has an enemy line but no party (HP, stress, equipped skills
                # all live in the roster), which is exactly the pairing
                # bridge/live_state.py needs to be replayable offline. The
                # first 72 captures were taken without it and are hero-blind.
                names = ("persist.raid.json", "persist.map.json", "persist.roster.json")
                raid_moved = "persist.raid.json" in touched
                for name in names:
                    if name not in touched and not (raid_moved and name == "persist.roster.json"):
                        continue
                    # Prefixed with the run's start time: filenames used to be
                    # elapsed-seconds only, so a second capture into the same
                    # directory interleaved with the first and silently
                    # overwrote parts of it — which happened, and left one
                    # directory holding two different raids.
                    dest = Path(args.capture) / f"{run_id}_{int(time.time() - start):05d}_{name}"
                    last_err = None
                    for _ in range(3):
                        try:
                            shutil.copy2(profile / name, dest)
                            last_err = None
                            break
                        except OSError as exc:
                            last_err = exc
                            time.sleep(0.15)
                    if last_err is not None:
                        # Loud, and fatal. The retry loop exists for the game
                        # holding a write lock for a moment; anything that
                        # survives three tries is not that. Swallowing it cost
                        # a whole acceptance run: --capture into a directory
                        # that did not exist copied nothing, said nothing, and
                        # the run was only discovered to be evidence-free after
                        # it was over and the raid file had been deleted.
                        raise SystemExit(f"capture failed for {dest}: {last_err}")
            if args.decode:
                fresh = snapshot_copy(profile)
                for line in describe(state, fresh):
                    print(f"{'':10} {line}")
                state = fresh
    except KeyboardInterrupt:
        pass
    print(f"\n{writes} save writes in {time.time() - start:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
