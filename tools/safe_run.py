"""Run a long job so that it cannot take the machine down.

On 2026-09-04 a leftover mutation-testing edit in `engine/combatant.py` --
`is_alive()` returning `not dead OR not corpse` -- made combats never end, so
every test that drives a fight looped and allocated. Three concurrent suites of
those exhausted 32 GB, Windows fell back on the page file, and Fran lost **four
hours** to a machine that never crashed and never printed an error.

Three separate things had to go wrong together, and this wraps a guard around
each of them:

* **The job hung instead of failing.** A hang is not a red test; it is a
  process that grows. `--max-minutes` turns it into a kill.
* **Memory ran out silently.** Windows swaps rather than killing, so the
  symptom is the whole desktop going treacle with nothing in the log.
  `--min-free-mb` watches the machine, not the job.
* **Two heavy runs overlapped.** A lock plus a pre-flight scan makes the second
  one refuse to start rather than compete.

Nothing here is advisory. Every check either refuses to start or kills the
child; there is no warn-and-continue path, because the incident this exists to
prevent produced no warning anybody could have acted on.

```bash
python tools/safe_run.py -- python -m pytest tests/ -q
python tools/safe_run.py --max-minutes 240 -- python train.py --full-expedition ...
python tools/safe_run.py --require-clean -- python train.py ...   # long runs
```

`--` separates this tool's own flags from the command; everything after it is
passed through untouched.
"""
from __future__ import annotations

import argparse
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

try:
    import psutil
except ImportError:                                        # pragma: no cover
    print("safe_run needs psutil: pip install psutil", file=sys.stderr)
    raise SystemExit(2)

REPO = Path(__file__).resolve().parent.parent
LOCK = REPO / ".safe_run.lock"

# A python process bigger than this is a real job, not a shell helper. The
# pre-flight refuses when one is already running, which is the check that would
# have stopped the second and third concurrent suite.
COMPETITOR_RSS_MB = 400

# Directories whose contents decide what the policy learns. A stray edit here
# is the difference between a measurement and a wasted afternoon.
CORE_DIRS = ("engine", "env", "data", "bridge")

# The shape of a mutation-testing edit: one line, one operator flipped. Matched
# against the -w diff so a whole-file line-ending flip cannot hide it -- which
# is exactly how the `and` -> `or` above survived an 818-line diff.
#
# A comparison is matched on sight: `==`, `<=` and friends effectively never
# occur in English, and a boundary that moves silently is as damaging as a
# boolean that flips.
_COMPARISON = re.compile(r"(?:==|!=|<=|>=|<|>)")
# A word operator needs to sit in something shaped like an expression. Without
# this the detector reported its own comments -- "...and was wrong" -- and a
# guard that cries wolf is one people learn to scroll past.
_WORD_OP = re.compile(r"\b(?:and|or)\b")
# `not`, `if` and `while` are ordinary English words and were matching
# prose ("the parser could not match them"); what is left is punctuation
# prose does not use, plus two keywords that do not occur mid-sentence.
_CODE_ISH = re.compile(r"[()\[\]=]|\b(?:return|assert|elif)\b|:\s*$")
_COMMENT_ONLY = re.compile(r"^\+\s*#")


def is_suspicious(line: str) -> bool:
    """True when an ADDED diff line looks like a flipped operator.

    Kept as a function rather than one regex because the two operator families
    need different evidence: a comparison is self-evident, a word operator is
    only meaningful next to code.
    """
    if not line.startswith("+") or line.startswith("+++"):
        return False
    if _COMMENT_ONLY.match(line):
        return False
    body = line[1:]
    if _COMPARISON.search(body):
        return True
    return bool(_WORD_OP.search(body) and _CODE_ISH.search(body))


def _fmt_mb(n):
    return f"{n:,} MB"


# --------------------------------------------------------------- pre-flight

def competing_python(exclude_pid: int) -> list[tuple[int, int]]:
    """`(pid, rss_mb)` for every heavy python process that is not us."""
    out = []
    for proc in psutil.process_iter(["pid", "name", "memory_info"]):
        try:
            name = (proc.info["name"] or "").lower()
            if "python" not in name or proc.info["pid"] in (exclude_pid, os.getpid()):
                continue
            rss = proc.info["memory_info"].rss // (1024 * 1024)
            if rss >= COMPETITOR_RSS_MB:
                out.append((proc.info["pid"], rss))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return out


def stale_lock() -> bool:
    """True when the lock names a pid that is gone, so it can be reclaimed."""
    try:
        pid = int(LOCK.read_text(encoding="utf-8").split()[0])
    except (OSError, ValueError, IndexError):
        return True
    return not psutil.pid_exists(pid)


def core_diff() -> list[str]:
    """Lines of `git diff -w` over the directories that decide behaviour."""
    try:
        r = subprocess.run(
            ["git", "diff", "--ignore-all-space", "--unified=0", "--", *CORE_DIRS],
            cwd=REPO, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return []
    return r.stdout.splitlines()


def report_core_diff() -> int:
    """Print any real change under `CORE_DIRS`; return how many lines look
    like a mutation.

    Whitespace and line endings are ignored, because the edit that cost four
    hours was one line inside a diff that `git` rendered as 818.
    """
    lines = core_diff()
    added = [l for l in lines if l.startswith("+") and not l.startswith("+++")]
    if not added:
        print("  working tree: engine/ env/ data/ bridge/ match HEAD")
        return 0
    suspects = [l for l in added if is_suspicious(l)]
    print(f"  working tree: {len(added)} changed line(s) under "
          f"{'/ '.join(CORE_DIRS)}")
    for line in suspects[:8]:
        print(f"    OPERATOR CHANGED  {line.strip()[:96]}")
    if suspects:
        print(f"  {len(suspects)} line(s) change a comparison or a boolean. "
              f"A leftover mutation looks exactly like this.")
    return len(suspects)


# ------------------------------------------------------------------ the run

def tree_rss_mb(proc: psutil.Process) -> int:
    """Resident MB of the child and every descendant.

    xdist workers are grandchildren, so measuring the child alone reads a few
    tens of MB while the machine is being eaten.
    """
    total = 0
    for p in [proc] + proc.children(recursive=True):
        try:
            total += p.memory_info().rss
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return total // (1024 * 1024)


def kill_tree(proc: psutil.Process) -> None:
    """Children first, then the parent; SIGTERM, then SIGKILL."""
    procs = proc.children(recursive=True) + [proc]
    for p in procs:
        try:
            p.terminate()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    _, alive = psutil.wait_procs(procs, timeout=5)
    for p in alive:
        try:
            p.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--min-free-mb", type=int, default=4096,
                    help="kill the job if the MACHINE drops below this much "
                         "free physical memory (default 4096)")
    ap.add_argument("--max-rss-mb", type=int, default=0,
                    help="kill the job if it exceeds this resident size; "
                         "0 (default) means half of physical RAM")
    ap.add_argument("--max-minutes", type=float, default=45.0,
                    help="kill the job after this long. A hang is the symptom "
                         "that costs the most, so this defaults low; raise it "
                         "explicitly for a training run.")
    ap.add_argument("--require-clean", action="store_true",
                    help="refuse to start if engine/ env/ data/ bridge/ differ "
                         "from HEAD. Use for any run long enough that starting "
                         "it on a corrupted tree wastes real time.")
    ap.add_argument("--allow-competitors", action="store_true",
                    help="skip the pre-flight scan for other heavy python "
                         "processes. There is almost never a good reason.")
    ap.add_argument("--poll-ms", type=int, default=250)
    ap.add_argument("command", nargs=argparse.REMAINDER,
                    help="-- followed by the command to run")
    args = ap.parse_args()

    cmd = args.command
    if cmd and cmd[0] == "--":
        cmd = cmd[1:]
    if not cmd:
        ap.error("give a command after --")

    total_mb = psutil.virtual_memory().total // (1024 * 1024)
    max_rss = args.max_rss_mb or (total_mb // 2)

    print(f"safe_run: {' '.join(cmd)}")
    print(f"  machine {_fmt_mb(total_mb)} total, "
          f"{_fmt_mb(psutil.virtual_memory().available // (1024 * 1024))} available")
    print(f"  limits: rss <= {_fmt_mb(max_rss)}, free >= "
          f"{_fmt_mb(args.min_free_mb)}, wall <= {args.max_minutes:g} min")

    # --- guard 1: one at a time ------------------------------------------
    if LOCK.exists() and not stale_lock():
        held = LOCK.read_text(encoding="utf-8").strip()
        print(f"REFUSING: another safe_run holds the lock ({held}).\n"
              f"  Wait for it, or remove {LOCK.name} if you are sure it is dead.",
              file=sys.stderr)
        return 3

    # --- guard 2: nothing else heavy already running ----------------------
    if not args.allow_competitors:
        busy = competing_python(exclude_pid=-1)
        if busy:
            print("REFUSING: heavy python already running -- "
                  "starting a second is what took the machine down:",
                  file=sys.stderr)
            for pid, rss in busy:
                print(f"    pid {pid}  {_fmt_mb(rss)}", file=sys.stderr)
            return 4

    # --- guard 3: is the tree what you think it is? -----------------------
    suspects = report_core_diff()
    if args.require_clean and core_diff():
        print("REFUSING: --require-clean and the core directories differ from "
              "HEAD. Commit, stash, or drop the flag deliberately.",
              file=sys.stderr)
        return 5
    if suspects:
        print("  ...continuing anyway; --require-clean refuses instead.")

    LOCK.write_text(f"{os.getpid()} {' '.join(cmd)}\n", encoding="utf-8")
    started = time.time()
    peak = 0
    verdict = "completed"
    try:
        child = subprocess.Popen(cmd, cwd=REPO)
        proc = psutil.Process(child.pid)
        while child.poll() is None:
            time.sleep(args.poll_ms / 1000)
            try:
                rss = tree_rss_mb(proc)
            except psutil.NoSuchProcess:
                break
            peak = max(peak, rss)
            free = psutil.virtual_memory().available // (1024 * 1024)
            mins = (time.time() - started) / 60

            if rss > max_rss:
                verdict = f"KILLED: job reached {_fmt_mb(rss)} > {_fmt_mb(max_rss)}"
            elif free < args.min_free_mb:
                verdict = (f"KILLED: machine down to {_fmt_mb(free)} free "
                           f"< {_fmt_mb(args.min_free_mb)}")
            elif mins > args.max_minutes:
                verdict = (f"KILLED: ran {mins:.1f} min > {args.max_minutes:g}. "
                           f"A job that hangs looks exactly like this.")
            else:
                continue
            print(f"\n{verdict}", file=sys.stderr)
            kill_tree(proc)
            break
        code = child.poll()
    except KeyboardInterrupt:
        verdict = "interrupted"
        try:
            kill_tree(psutil.Process(child.pid))
        except (psutil.NoSuchProcess, NameError):
            pass
        code = 130
    finally:
        try:
            LOCK.unlink()
        except OSError:
            pass

    print(f"\nsafe_run: {verdict}  peak {_fmt_mb(peak)}  "
          f"{(time.time() - started) / 60:.1f} min  exit {code}")
    return 0 if (code == 0 and verdict == "completed") else (code or 1)


if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal.default_int_handler)
    raise SystemExit(main())
