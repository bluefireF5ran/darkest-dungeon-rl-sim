"""Capture the Win32 debug channel (OutputDebugString) — the live trace out of
the real Darkest Dungeon.

Why this exists
---------------
`Darkest.exe` (win64, release) still contains its debug format strings for
exactly the mechanics this simulator has to match:

    Initiative order! %d %d %s %d %d %d %d
    RollInitiative %d.
    Desire: %s
    Queue %d: %s: %.2f
    '%s' landed a crit %d.
    Actor %d stress: %.5f %.5f
    %s: Affliction Check: Resisted!

and it imports `OutputDebugStringA` with no `AllocConsole`/`WriteConsole`, so
that is where the output goes. The Win32 debug channel is a shared-memory
mailslot any process can read: create the `DBWIN_BUFFER` section plus the two
handshake events and you receive every OutputDebugString on the machine. No
debugger, no injection, no modification of the game.

    Darkest.exe -randomseed 12345 -enabledebugmonsterbrainoutput -enabledebugqueueoutput

Easiest route is Steam -> game Properties -> General -> Launch Options, which
appends the flags to the command line and keeps overlay/achievements/cloud
saves intact. Note `HKCU\\SOFTWARE\\Red Hook Studios\\Darkest Dungeon\\commandline`
is NOT read by the game — it is only where the shipped `Darkest_command_line.vbs`
remembers what you last typed into its prompt before passing it as argv.

`-randomseed` is not needed for the probe; it matters for the differential
tests later, where the sim has to replay the same rolls.

Go/no-go
--------
The format strings being present does NOT prove the calls survived the release
build. `--probe` answers that mechanically: it watches for the known combat
patterns and reports which ones actually arrived.

    python tools/dbwin_capture.py --probe --duration 300
    python tools/dbwin_capture.py --out trace.log --filter "Initiative|Desire|Queue"

Only one process on the machine can own the debug channel. If DebugView, Visual
Studio, or any debugger is running, this exits with a clear error instead of
silently capturing nothing.
"""

from __future__ import annotations

import argparse
import ctypes
import subprocess
import ctypes.wintypes as wt
import re
import sys
import time

# --- Win32 ------------------------------------------------------------------

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
adv = ctypes.WinDLL("advapi32", use_last_error=True)

INVALID_HANDLE_VALUE = wt.HANDLE(-1)
PAGE_READWRITE = 0x04
FILE_MAP_READ = 0x0004
WAIT_OBJECT_0 = 0x0
WAIT_TIMEOUT = 0x102
ERROR_ALREADY_EXISTS = 183
BUFFER_SIZE = 4096
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

SECURITY_DESCRIPTOR_REVISION = 1


class SECURITY_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("nLength", wt.DWORD),
                ("lpSecurityDescriptor", wt.LPVOID),
                ("bInheritHandle", wt.BOOL)]


k32.CreateFileMappingW.restype = wt.HANDLE
k32.CreateFileMappingW.argtypes = [wt.HANDLE, wt.LPVOID, wt.DWORD, wt.DWORD, wt.DWORD, wt.LPCWSTR]
k32.MapViewOfFile.restype = wt.LPVOID
k32.MapViewOfFile.argtypes = [wt.HANDLE, wt.DWORD, wt.DWORD, wt.DWORD, ctypes.c_size_t]
# Without explicit argtypes ctypes marshals a 64-bit pointer as a C int and
# raises "int too long to convert" on teardown.
k32.UnmapViewOfFile.restype = wt.BOOL
k32.UnmapViewOfFile.argtypes = [wt.LPCVOID]
k32.CloseHandle.restype = wt.BOOL
k32.CloseHandle.argtypes = [wt.HANDLE]
k32.CreateEventW.restype = wt.HANDLE
k32.CreateEventW.argtypes = [wt.LPVOID, wt.BOOL, wt.BOOL, wt.LPCWSTR]
k32.WaitForSingleObject.restype = wt.DWORD
k32.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]
k32.SetEvent.restype = wt.BOOL
k32.SetEvent.argtypes = [wt.HANDLE]
k32.OpenProcess.restype = wt.HANDLE
k32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
k32.QueryFullProcessImageNameW.argtypes = [wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD)]

adv.InitializeSecurityDescriptor.restype = wt.BOOL
adv.InitializeSecurityDescriptor.argtypes = [wt.LPVOID, wt.DWORD]
adv.SetSecurityDescriptorDacl.restype = wt.BOOL
adv.SetSecurityDescriptorDacl.argtypes = [wt.LPVOID, wt.BOOL, wt.LPVOID, wt.BOOL]

class SECURITY_DESCRIPTOR(ctypes.Structure):
    """Declared in full so ctypes computes the size with correct alignment.

    The 4-byte header is followed by four *pointers*, which on x64 are aligned
    to 8 — so the real size is 40 (4 header + 4 padding + 4x8), not the 36 a
    naive `4 + 4 * sizeof(void*)` gives, and not the 20 of x86. Under-allocating
    makes InitializeSecurityDescriptor write past the buffer; the resulting heap
    corruption surfaces later and *nondeterministically* (it presented here as
    ERROR_NOACCESS (998) out of CreateEventW, while a smaller run happened to
    pass). Letting ctypes do the layout removes the arithmetic from the picture.
    """
    _fields_ = [("Revision", ctypes.c_ubyte),
                ("Sbz1", ctypes.c_ubyte),
                ("Control", wt.WORD),
                ("Owner", wt.LPVOID),
                ("Group", wt.LPVOID),
                ("Sacl", wt.LPVOID),
                ("Dacl", wt.LPVOID)]


SECURITY_DESCRIPTOR_MIN_LENGTH = ctypes.sizeof(SECURITY_DESCRIPTOR)


def _null_dacl_sa() -> tuple[SECURITY_ATTRIBUTES, ctypes.Array]:
    """A NULL DACL, i.e. "anyone may access".

    The game may run at a different integrity level than this script (Steam
    overlay, elevation). Without a permissive descriptor the writer silently
    fails to open our objects and we capture nothing at all — which is
    indistinguishable from "the debug calls were compiled out", the exact
    question --probe exists to answer. So don't tighten this without also
    re-validating the probe.

    Returns the descriptor buffer alongside the SECURITY_ATTRIBUTES: the
    struct only holds a raw pointer to it, so the caller must keep the buffer
    alive for as long as the SA is in use.
    """
    sd = ctypes.create_string_buffer(SECURITY_DESCRIPTOR_MIN_LENGTH)
    if not adv.InitializeSecurityDescriptor(sd, SECURITY_DESCRIPTOR_REVISION):
        raise ctypes.WinError(ctypes.get_last_error())
    if not adv.SetSecurityDescriptorDacl(sd, True, None, False):
        raise ctypes.WinError(ctypes.get_last_error())
    sa = SECURITY_ATTRIBUTES()
    sa.nLength = ctypes.sizeof(SECURITY_ATTRIBUTES)
    sa.lpSecurityDescriptor = ctypes.cast(sd, wt.LPVOID)
    sa.bInheritHandle = False
    return sa, sd


def process_name(pid: int) -> str:
    h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return "?"
    try:
        size = wt.DWORD(1024)
        buf = ctypes.create_unicode_buffer(size.value)
        if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return buf.value.rsplit("\\", 1)[-1]
        return "?"
    finally:
        k32.CloseHandle(h)


class DebugChannel:
    """Owns the DBWIN objects. Only one owner per machine."""

    def __init__(self):
        self._h_map = self._h_ready = self._h_data = None
        self._view = None
        self._sd = None  # descriptor buffer; SECURITY_ATTRIBUTES only points at it

    def __enter__(self):
        sa, self._sd = _null_dacl_sa()

        self._h_ready = k32.CreateEventW(ctypes.byref(sa), False, True, "DBWIN_BUFFER_READY")
        if not self._h_ready:
            raise ctypes.WinError(ctypes.get_last_error())
        if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
            raise RuntimeError(
                "another process already owns the Win32 debug channel "
                "(DebugView, Visual Studio, or another debugger). Close it and retry — "
                "otherwise this would capture nothing and look like a negative result."
            )

        self._h_data = k32.CreateEventW(ctypes.byref(sa), False, False, "DBWIN_DATA_READY")
        if not self._h_data:
            raise ctypes.WinError(ctypes.get_last_error())

        self._h_map = k32.CreateFileMappingW(
            INVALID_HANDLE_VALUE, ctypes.byref(sa), PAGE_READWRITE, 0, BUFFER_SIZE, "DBWIN_BUFFER")
        if not self._h_map:
            raise ctypes.WinError(ctypes.get_last_error())

        self._view = k32.MapViewOfFile(self._h_map, FILE_MAP_READ, 0, 0, 0)
        if not self._view:
            raise ctypes.WinError(ctypes.get_last_error())
        return self

    def __exit__(self, *exc):
        if self._view:
            k32.UnmapViewOfFile(self._view)
        for h in (self._h_map, self._h_data, self._h_ready):
            if h:
                k32.CloseHandle(h)

    def messages(self, timeout_ms: int = 250):
        """Yield (pid, text). Yields nothing on idle; caller controls duration."""
        while True:
            k32.SetEvent(self._h_ready)
            rc = k32.WaitForSingleObject(self._h_data, timeout_ms)
            if rc == WAIT_TIMEOUT:
                yield None
                continue
            if rc != WAIT_OBJECT_0:
                raise ctypes.WinError(ctypes.get_last_error())
            pid = ctypes.cast(self._view, ctypes.POINTER(wt.DWORD))[0]
            raw = ctypes.string_at(self._view + 4, BUFFER_SIZE - 4)
            text = raw.split(b"\x00", 1)[0].decode("latin1", errors="replace")
            yield (pid, text.rstrip("\r\n"))


# --- Go/no-go probe ---------------------------------------------------------

# Calibrated against a real captured single-player combat (see CHANGELOG).
#
# An earlier version of this table was mined straight out of Darkest.exe's
# string pool and scored 4/11 — badly under-reporting, because several of those
# strings ("Initiative order!", "Queue %d: %s: %.2f", "Actor %d stress: %.5f",
# "Actor %d buff %d turns duration") live in the **multiplayer sync** path.
# Their neighbours in the binary give it away: `MonsterBrain::ReceiveData`,
# `Reporting match`, `Local actor %d buff %d missing remote actor buff`. They
# need an actual MP session, so their absence says nothing about single-player
# fidelity. Only patterns confirmed to fire in a real SP combat belong here.
PROBE_PATTERNS = {
    # The RNG stream: every draw, with its raw 31-bit integer state.
    "rng_uniform":       re.compile(r"Random uniform: [\d.]+ Seed: \d+"),
    "rng_int_range":     re.compile(r"Random int range(?: A,B)?: "),
    "rng_get_seed":      re.compile(r"Get seed \d+"),
    # Attack resolution.
    "hit_chance":        re.compile(r"Chance to hit: [\d.]+"),
    "hit_roll":          re.compile(r"Roll to hit: [\d.]+"),
    # Turn structure: the battle state machine and the acting combatant.
    "battle_state":      re.compile(r"Battle: state change to bs_"),
    "current_actor":     re.compile(r"check pCurrentBattleActor #\d+:"),
    "roll_initiative":   re.compile(r"RollInitiative"),
    # Monster AI.
    "brain_select":      re.compile(r"MonsterBrain: Select (Skill|Target)"),
    "monster_desire":    re.compile(r"^\s*Desire: \w+"),
    "desire_chances":    re.compile(r"(Skill|Target) chances:"),
    "brain_skill_pick":  re.compile(r"MonsterBrain: selected skill at index"),
}


# One real sample per PROBE_PATTERNS row, copied from an actual capture.
SELFTEST_MESSAGES = [
    "PID: 1 Name: Battle Random uniform: 0.6740028262 Seed: 1447410098",
    "PID: 1 Name: Battle Random int range A,B: 3 , 5 Value: 3 Seed: 243990890",
    "PID: 1 Name: Battle Get seed 23",
    "Chance to hit: 0.925000",
    "Roll to hit: 0.674003",
    "Battle: state change to bs_turn_do_skill, (from bs_turn_wait_to_do_skill). 1",
    "check pCurrentBattleActor #0: Crusader 1",
    "RollInitiative 7.",
    "MonsterBrain: Select Skill: ",
    "Desire: specific_skill",
    " Skill chances: ",
    "MonsterBrain: selected skill at index: 0",
]


def _selftest_emitter(delay: float = 1.5) -> "subprocess.Popen":
    """Emit one sample of every probe pattern from a *separate* process.

    Separate because OutputDebugString from the process that owns the buffer
    is not a meaningful test of the handshake. This validates the capture path
    end-to-end without needing the game, so a later negative --probe result
    can be attributed to the game rather than to this tool.
    """
    import subprocess
    code = (
        "import ctypes,time\n"
        f"time.sleep({delay})\n"
        "k=ctypes.WinDLL('kernel32')\n"
        f"msgs={SELFTEST_MESSAGES!r}\n"
        "for m in msgs:\n"
        "    k.OutputDebugStringA(m.encode('latin1'))\n"
        "    time.sleep(0.05)\n"
    )
    return subprocess.Popen([sys.executable, "-c", code],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--selftest", action="store_true",
                    help="validate the capture path by emitting the probe patterns "
                         "from a separate process (no game needed)")
    ap.add_argument("--duration", type=float, default=0,
                    help="stop after N seconds (0 = until Ctrl-C)")
    ap.add_argument("--pid", type=int, default=None, help="only show this PID")
    ap.add_argument("--process", default=None,
                    help="only show processes whose image name matches (e.g. Darkest.exe)")
    ap.add_argument("--filter", default=None, help="regex the message must match")
    ap.add_argument("--out", default=None, help="also append messages to this file")
    ap.add_argument("--probe", action="store_true",
                    help="go/no-go: report which known combat-debug patterns actually fired")
    ap.add_argument("--quiet", action="store_true", help="suppress per-line echo")
    args = ap.parse_args()

    if sys.platform != "win32":
        print("This tool is Windows-only (Win32 debug channel).", file=sys.stderr)
        return 2

    if args.selftest:
        args.probe = True
        if not args.duration:
            args.duration = 8

    msg_re = re.compile(args.filter) if args.filter else None
    seen = {k: 0 for k in PROBE_PATTERNS}
    name_cache: dict[int, str] = {}
    unresolved_warned = False
    total = 0
    deadline = time.monotonic() + args.duration if args.duration else None
    out = open(args.out, "a", encoding="utf-8") if args.out else None

    if args.selftest:
        print("SELFTEST: emitting the probe patterns from a separate process.")
        print("This validates the capture path only — it says nothing about the game.\n")
    else:
        print("Listening on the Win32 debug channel. Start a combat in-game.")
        print("Launch the game with:  Darkest.exe -randomseed 12345 "
              "-enabledebugmonsterbrainoutput -enabledebugqueueoutput")
        print("Ctrl-C to stop.\n")

    emitter = None
    try:
        with DebugChannel() as ch:
            if args.selftest:
                emitter = _selftest_emitter()
            for item in ch.messages():
                if deadline and time.monotonic() > deadline:
                    break
                if item is None:
                    continue
                pid, text = item

                if args.pid is not None and pid != args.pid:
                    continue
                if args.process:
                    nm = name_cache.get(pid) or name_cache.setdefault(pid, process_name(pid))
                    # An unresolvable name ("?") means OpenProcess was denied,
                    # not that this is the wrong process. Dropping those would
                    # silently filter away the very messages we are testing for
                    # and report a false NO-GO, so keep them and say so once.
                    if nm == "?":
                        if not unresolved_warned:
                            print(f"  (note: cannot read the image name of pid {pid}; "
                                  f"keeping its messages rather than risk a false negative)",
                                  file=sys.stderr)
                            unresolved_warned = True
                    elif args.process.lower() not in nm.lower():
                        continue
                if msg_re and not msg_re.search(text):
                    continue

                total += 1
                for key, pat in PROBE_PATTERNS.items():
                    if pat.search(text):
                        seen[key] += 1
                line = f"[{pid}] {text}"
                if not args.quiet:
                    print(line, flush=True)
                if out:
                    out.write(line + "\n")
                    out.flush()
    except KeyboardInterrupt:
        pass
    except RuntimeError as e:
        print(f"\nERROR: {e}", file=sys.stderr)
        return 3
    finally:
        if out:
            out.close()
        if emitter is not None:
            emitter.terminate()

    print(f"\n{total} messages captured.")
    if args.probe:
        hit = sum(1 for v in seen.values() if v)
        label = "SELFTEST" if args.selftest else "PROBE"
        print(f"\n=== {label}: {hit}/{len(seen)} combat-debug patterns fired ===")
        for k, v in seen.items():
            print(f"  {'YES' if v else ' no'}  {k:20s} {v}")
        print()
        if args.selftest:
            if hit == len(seen):
                print("SELFTEST PASSED: the capture path works. A negative --probe run")
                print("against the game can now be trusted as a fact about the game.")
                return 0
            print("SELFTEST FAILED: the capture path itself is broken — fix this before")
            print("drawing any conclusion from --probe.")
            return 1
        if hit == 0:
            print("NO-GO: nothing fired. Either no combat happened while listening, the")
            print("debug flags were not applied, or the calls are compiled out of the")
            print("release build. Fall back to `-textsave` + a DSON decoder for")
            print("persist.raid.json (per-combat granularity instead of per-turn).")
            return 1
        print("GO: the release build does emit combat traces. Proceed to the trace")
        print("parser and differential tests (Phase 3b).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
