"""`app.log` — the game's own running commentary, and the only acknowledgement
the bridge has before a save file exists.

    python bridge/applog.py --options        # which launch flags this boot accepted
    python bridge/applog.py --tail           # follow the interesting lines live
    python bridge/applog.py --state          # where the app state machine got to

## Why this exists

Everything else in `bridge/` acknowledges an action by watching a save file
change. That model is inert in the front end: the title screen, the save-slot
list, the DLC page and the mode select write **nothing** to disk, and
`act.py::_send_and_confirm` already degrades to *"sent (no save to confirm
against)"* when `raid_path` is missing. So a menu click had no way to be
confirmed at all.

`GAME_DIR/app.log` closes part of that gap. It is plain text, timestamped,
append-only within a boot, and **truncated at each launch** (exactly one
`Game started...` line in a 46 KB session log), so there is no session
boundary to hunt for -- though `_last_boot` finds one anyway rather than trust
that forever.

What it actually gives, measured on a real session:

    [2026-08-17 17:56:13] [INFO] [General] option: skipvalidation
    [2026-08-17 18:01:50] [INFO] [General] as_load_campaign - LoadProfile() 0.071200
    [2026-08-17 18:02:27] [INFO] [General] Continue pressed in Loading Screen
    [2026-08-17 18:02:27] [INFO] [General] as_raid_start_done_loading - RaidStartDoneLoading()

Three things worth having:

1. **Which launch flags the binary accepted.** Every parsed option is echoed as
   `option: <name>` with its value on the following line. A flag the binary does
   not know is simply absent -- which is how a `-skipfeflow` probe gets graded
   without a screenshot and without guessing.
2. **When a campaign is loaded**, which is the moment a `profile_N/` directory
   becomes real and `find_profile` starts having a right answer.
3. **When a raid is actually running.** `as_raid_start_done_loading` fires after
   the loading screen's Continue, and is the handoff point to the poll loop.

## What it does NOT give

It does not name the screen that is currently up. `as_fe_flow_*` never appears
in a normal session log -- only `as_load_campaign` and
`as_raid_start_done_loading` do, three times each across three campaign loads.
So this is a source of **checkpoints, not per-step acks**; recognising the menu
screen itself is `bridge/screen.py`'s job.

Deliberately dependency-free and read-only: it opens the file, reads it, and
closes it. `live_state._stage` copies saves before parsing because the game may
be mid-write on them; a text log appended line-at-a-time needs no such care,
and a torn final line is dropped by the timestamp regex on its own.
"""

from __future__ import annotations

import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data.paths import GAME_DIR
APP_LOG = GAME_DIR / "app.log"

# [2026-08-17 18:02:27] [INFO] [General] Continue pressed in Loading Screen
LINE_RE = re.compile(
    r"^\[(?P<ts>\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\]\s+"
    r"\[(?P<level>\w+)\]\s+\[(?P<chan>[^\]]+)\]\s?(?P<msg>.*)$")

BOOT_MARK = "Game started"
OPTION_RE = re.compile(r"^option:\s*(?P<name>\S+)\s*$")

# The state-machine lines the game really emits. The binary defines ~30
# `as_*` states around `Darkest::ParseArguments`, but only these two reach the
# log in a normal session -- `as_fe_flow_start/update/finish` never do. Named
# here so a caller asks for a MEANING and not for a substring.
CAMPAIGN_LOADED = "as_load_campaign"
LOADING_CONTINUE = "Continue pressed in Loading Screen"
RAID_RUNNING = "as_raid_start_done_loading"


@dataclass(frozen=True)
class Entry:
    """One parsed line. `raw` is kept so a caller can print what it matched."""
    ts: str
    level: str
    channel: str
    msg: str
    raw: str

    def __str__(self) -> str:
        return f"{self.ts}  {self.msg}"


def _read(path: Path) -> list[str]:
    """Read the log, tolerating the game holding it open and writing to it.

    `errors="replace"` rather than a decode failure: the log is the game's own
    output in the machine's locale, and one odd byte in a hero name must not
    take out the only acknowledgement channel the boot phase has.
    """
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []


def _last_boot(lines: Iterable[str]) -> list[str]:
    """Everything since the most recent `Game started...`.

    Measured behaviour is that the file is TRUNCATED per launch (one such line
    in a full session), so this is usually the whole file. It is done anyway
    because "the log happens to be truncated" is an observation about one
    build, and reading a previous boot's `option:` lines would grade a flag
    probe against the wrong launch.
    """
    lines = list(lines)
    for i in range(len(lines) - 1, -1, -1):
        if BOOT_MARK in lines[i]:
            return lines[i:]
    return lines


def entries(path: Path = APP_LOG, *, this_boot: bool = True) -> list[Entry]:
    lines = _read(Path(path))
    if this_boot:
        lines = _last_boot(lines)
    out = []
    for raw in lines:
        m = LINE_RE.match(raw)
        if m:
            out.append(Entry(m["ts"], m["level"], m["chan"], m["msg"].strip(),
                             raw))
    return out


def options(path: Path = APP_LOG) -> dict[str, str]:
    """Every launch option the binary accepted this boot, name -> value.

    The value sits on the NEXT logged line, quoted, and a valueless flag has a
    blank line there instead:

        option: skipvalidation          -> {"skipvalidation": ""}
        option: vsync / "1"             -> {"vsync": "1"}

    **An unknown flag never appears at all**, which is the whole point: this is
    how `-skipfeflow` and `-profile` get graded from disk instead of from a
    screenshot of whatever screen came up.
    """
    got: dict[str, str] = {}
    ents = entries(path)
    for i, e in enumerate(ents):
        m = OPTION_RE.match(e.msg)
        if not m:
            continue
        value = ""
        if i + 1 < len(ents):
            nxt = ents[i + 1].msg.strip()
            if nxt.startswith('"') and nxt.endswith('"') and len(nxt) >= 2:
                value = nxt[1:-1]
        got[m["name"]] = value
    return got


def find(needle: str, path: Path = APP_LOG) -> list[Entry]:
    """Every entry this boot whose message contains `needle`."""
    return [e for e in entries(path) if needle in e.msg]


def last(needle: str, path: Path = APP_LOG) -> Optional[Entry]:
    hits = find(needle, path)
    return hits[-1] if hits else None


def count(needle: str, path: Path = APP_LOG) -> int:
    return len(find(needle, path))


def wait_for(needle: str, *, timeout: float = 60.0, path: Path = APP_LOG,
             since: int = 0, poll: float = 0.35) -> Optional[Entry]:
    """Block until a NEW line containing `needle` appears, or give up.

    `since` is a count taken before the action was sent -- pass
    `count(needle)`. Without it a caller that just re-read a line the previous
    campaign load wrote would return instantly: `as_load_campaign` appears
    three times in one real session, so "has it ever appeared" is not a usable
    acknowledgement and "has it appeared AGAIN" is.
    """
    end = time.perf_counter() + timeout
    while time.perf_counter() < end:
        hits = find(needle, path)
        if len(hits) > since:
            return hits[-1]
        time.sleep(poll)
    return None


def state(path: Path = APP_LOG) -> str:
    """The furthest checkpoint reached this boot, as a coarse phase name.

    Deliberately coarse -- `""`, `"campaign"`, `"raid"`. The log carries no
    finer detail (see the module docstring), and inventing a finer answer from
    it would be a convention masquerading as a measurement.
    """
    if count(RAID_RUNNING, path):
        raid = last(RAID_RUNNING, path)
        load = last(CAMPAIGN_LOADED, path)
        if load is None or raid is not None and raid.ts >= load.ts:
            return "raid"
    if count(CAMPAIGN_LOADED, path):
        return "campaign"
    return ""


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Read the game's app.log.")
    ap.add_argument("--path", default=str(APP_LOG))
    ap.add_argument("--options", action="store_true",
                    help="the launch flags this boot accepted")
    ap.add_argument("--state", action="store_true",
                    help="the furthest checkpoint reached")
    ap.add_argument("--find", default=None, metavar="TEXT")
    ap.add_argument("--tail", action="store_true",
                    help="follow the checkpoint lines live")
    ap.add_argument("--interval", type=float, default=0.5)
    args = ap.parse_args(argv)
    path = Path(args.path)

    if not path.exists():
        print(f"no app.log at {path}")
        return 1

    if args.options:
        opts = options(path)
        print(f"{len(opts)} option(s) accepted this boot:")
        for k, v in sorted(opts.items()):
            print(f"  {k:28s} {v!r}" if v else f"  {k}")
        return 0

    if args.find is not None:
        hits = find(args.find, path)
        print(f"{len(hits)} line(s) containing {args.find!r}")
        for e in hits:
            print(f"  {e}")
        return 0

    if args.state:
        for name, needle in (("campaign loaded", CAMPAIGN_LOADED),
                             ("loading continue", LOADING_CONTINUE),
                             ("raid running", RAID_RUNNING)):
            e = last(needle, path)
            n = count(needle, path)
            print(f"  {name:18s} {'x%d  %s' % (n, e.ts) if e else '-'}")
        print(f"  => state {state(path)!r}")
        return 0

    if args.tail:
        seen = {n: count(n, path) for n in
                (CAMPAIGN_LOADED, LOADING_CONTINUE, RAID_RUNNING)}
        print("following app.log (ctrl-c to stop)")
        try:
            while True:
                for needle in list(seen):
                    hits = find(needle, path)
                    if len(hits) > seen[needle]:
                        for e in hits[seen[needle]:]:
                            print(f"  {e}")
                        seen[needle] = len(hits)
                time.sleep(args.interval)
        except KeyboardInterrupt:
            return 0

    print(f"{len(entries(path))} entries this boot; state {state(path)!r}")
    print(f"{len(options(path))} launch options; "
          f"use --options / --state / --find / --tail")
    return 0


if __name__ == "__main__":
    sys.exit(main())
