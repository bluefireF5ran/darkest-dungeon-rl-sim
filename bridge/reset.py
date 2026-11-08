"""Tear a campaign down, so the next one can be started unattended.

    python bridge/reset.py --status          # what is on disk, and what is up
    python bridge/reset.py --to-menu         # ESC -> Exit to Main Menu
    python bridge/reset.py --to-menu --act   # ...and actually send it

`bridge/menus.py` is the forward flow -- front end into a new campaign. This is
the reverse, and it exists because an overnight loop cannot ask a human to
delete a save between iterations.

## Why the delete is by PATH and not by clicking the red X

The save list draws campaigns in slot order with the empty letter at the first
free index, and only TWO rows are visible. On this machine that puts the
target on row 2 and a real campaign on row 1, 90 px away. `BRIDGE.md` has
refused to click a delete X since the module was written, for that reason, and
`tests/bridge/test_menus.py` proves by exhaustion that nothing does.

Naming the directory removes the whole class of failure: it cannot be one row
off. What it does NOT remove is deleting the wrong directory, so that is what
the gates below are about.

## The gate is the TYPE, not a check

`delete_owned` takes an `OwnedSlot`, and the only way to get one is
`claim_slot`, which **refuses if the directory already exists**. So a save that
was on disk before this process started cannot be represented, and therefore
cannot be passed to the delete -- the invariant is enforced by what the
function will accept rather than by a condition inside it that a later edit
could weaken.

Three further refusals, each for a distinct way to be wrong:

* the path must sit directly under `SAVE_ROOT` and be named `profile_<slot>`,
  so a crafted or relative path cannot escape;
* the game must be showing `title`, i.e. the campaign is unloaded -- deleting
  a profile the game still has open is how a half-written save survives;
* the directory must be GONE afterwards, and still gone a moment later.
  **Steam Cloud is live for this app** (`remotecache.vdf` is present), so a
  restore is a real possibility and is reported rather than fought.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bridge.live_state import PROFILE_GLOB, SAVE_ROOT      # noqa: E402
from bridge.act import (INPUT_MOUSE, Step, InputPlan,      # noqa: E402,F401
                        SendInputBackend, VK_ESCAPE)

# How long to wait for a screen to become what we expect, and how often to look.
SCREEN_TIMEOUT = 12.0
SCREEN_POLL = 0.4
# After a delete, look again: a cloud sync that restores the directory does not
# do it instantly.
CLOUD_RECHECK = 3.0

# Where a claim is written down. Deliberately OUTSIDE the game's save
# directory: nothing this tool does should put a foreign file where DD
# enumerates profiles.
CLAIM_JOURNAL = Path(__file__).resolve().parent.parent / "archive" / "reset_claims.json"
# How long an unreleased claim can authorise adopting an existing directory.
# Long enough to cover a night, short enough that a claim from last week
# cannot speak for whatever is in that slot now.
ADOPT_MAX_AGE = 18 * 3600.0
JOURNAL_KEEP = 50


class ResetRefused(Exception):
    """A gate said no. Never caught inside this module."""


@dataclass(frozen=True)
class OwnedSlot:
    """A save slot THIS process created, and the only thing `delete_owned` takes.

    Frozen, and constructed only by `claim_slot`. That is the whole safety
    argument: a directory that existed before we started can never be wrapped
    in one of these, so it can never be handed to the delete.
    """
    slot: int
    path: Path
    claimed_at: float



# Screens where ESC closes an overlay rather than opening the pause menu.
ESC_STEPS_BACK = ("hamlet", "estate_map")

def journal_for(save_root: Path) -> Path:
    """The claim journal that describes `save_root`.

    **A journal belongs to the save root it is about, and that is a structural
    guarantee rather than a convention.** `CLAIM_JOURNAL` describes the real
    Steam save directory; a claim on some other root -- a pytest `tmp_path`,
    a copied backup -- describes a different world and must not be written
    into it.

    This exists because the alternative failed in exactly the way a
    convention does. `tests/bridge/test_menus.py` overrides `save_root` but not
    `journal`, so **every suite run wrote ~20 claims about pytest temp
    directories into the production journal**, and `JOURNAL_KEEP` (50) then
    evicted the real ones. Measured 2026-08-31: after four suite runs the
    production journal held nothing but `pytest-of-Francisco` paths, and
    `tools/overnight.py` could no longer adopt the very slot it had created
    that morning -- so the loop refused to start, correctly, on evidence the
    test suite had destroyed.

    Passing `journal=` explicitly still wins; this only decides the default.
    """
    root = Path(save_root).resolve()
    try:
        if root == Path(SAVE_ROOT).resolve():
            return CLAIM_JOURNAL
    except OSError:
        pass
    # Inside the given root, NOT beside it. The journal must outlive the
    # profile directory it describes, and it does -- a claim is about
    # `<root>/<id>/262060/remote/profile_N`, several levels down. Putting it in
    # `root.parent` instead shares one file between every caller under a common
    # parent, which for pytest is the shared `tmp_path` base: two tests, or two
    # xdist workers, would then write the same journal and the isolation this
    # function exists for would be gone again one level up.
    return root / "reset_claims.json"


def claim_slot(slot: int, save_root: Path = SAVE_ROOT,
               journal: Optional[Path] = None,
               max_age: float = ADOPT_MAX_AGE) -> OwnedSlot:
    """Record that we are about to create `profile_<slot>`.

    Refuses if it already exists -- that is the point. Call this BEFORE the
    campaign is created, never after, or the refusal cannot fire.

    **The one exception is our own crash.** An iteration that dies between
    creating a campaign and deleting it leaves the directory behind, and a
    strict refusal then blocks every later run: the loop stops at the first
    crash and the night is over. So a claim is also WRITTEN DOWN, outside the
    game's save directory, and an existing slot may be adopted when the
    journal says this tool created it and never released it.

    That keeps the invariant rather than weakening it -- a campaign a human
    made has no journal entry and stays unreachable -- and the entry expires,
    so a claim left over from days ago cannot authorise deleting whatever
    happens to be in that slot today.
    """
    root = Path(save_root)
    journal = Path(journal) if journal is not None else journal_for(root)
    target = _profile_path(slot, root)
    if target is None:
        raise ResetRefused(
            f"cannot locate profile_{slot}: no sibling profile exists to "
            "derive the Steam user id from")
    if target.exists():
        held = _open_claim(target, journal, max_age)
        if held is None:
            raise ResetRefused(
                f"{target.name} already exists and this tool has no open "
                "claim on it -- it was not created here, so it will never be "
                "deletable through this module")
        return held                              # adopt: ours, from a crash
    owned = OwnedSlot(slot=slot, path=target, claimed_at=time.time())
    _write_claim(owned, journal)
    return owned


def _read_journal(journal: Path) -> list[dict]:
    try:
        return json.loads(Path(journal).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def _write_journal(entries: list[dict], journal: Path) -> None:
    path = Path(journal)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(entries, indent=1), encoding="utf-8")
    except OSError:
        pass                                     # best effort; never fatal


def _write_claim(owned: OwnedSlot, journal: Path) -> None:
    entries = [e for e in _read_journal(journal)
               if e.get("path") != str(owned.path)]
    entries.append({"slot": owned.slot, "path": str(owned.path),
                    "claimed_at": owned.claimed_at, "released": False})
    _write_journal(entries[-JOURNAL_KEEP:], journal)


def _release_claim(owned: OwnedSlot, journal: Path) -> None:
    entries = _read_journal(journal)
    for e in entries:
        if e.get("path") == str(owned.path):
            e["released"] = True
    _write_journal(entries, journal)


def _open_claim(target: Path, journal: Path,
                max_age: float) -> Optional[OwnedSlot]:
    """An unreleased, unexpired claim on `target`, or None.

    Expiry is what stops a claim from a previous night authorising the
    deletion of whatever is in that slot today.
    """
    now = time.time()
    for e in reversed(_read_journal(journal)):
        if e.get("path") != str(target) or e.get("released"):
            continue
        claimed = float(e.get("claimed_at") or 0.0)
        if now - claimed > max_age:
            return None
        return OwnedSlot(slot=int(e["slot"]), path=target,
                         claimed_at=claimed)
    return None


def slots_on_disk(save_root: Path = SAVE_ROOT) -> frozenset[int]:
    """Which `profile_<n>` directories exist right now."""
    out = set()
    for d in Path(save_root).glob(PROFILE_GLOB):
        tail = d.name.rsplit("_", 1)[-1]
        if d.is_dir() and tail.isdigit():
            out.add(int(tail))
    return frozenset(out)


def orphans(save_root: Path = SAVE_ROOT, journal: Optional[Path] = None,
            max_age: float = ADOPT_MAX_AGE) -> list[OwnedSlot]:
    """Slots this tool created, still on disk, and never released.

    **An interrupted iteration leaves a campaign nobody can clean up.**
    `claim_created` owns what a boot makes, and releases it at teardown -- but
    a run that is killed mid-raid never reaches the teardown, so the directory
    survives with an unreleased claim against it. The next run snapshots
    before booting, sees that directory as pre-existing, and correctly refuses
    to touch it. Two of those accumulated on this machine in a day and had to
    be removed by hand.

    The journal already holds the proof of ownership and `_open_claim`
    already reads it, expiry and all -- this is only the sweep nobody had
    written. Same guarantee as everywhere else here: a campaign a human made
    has no journal entry and can never come back from this.

    Newest first, so a caller that deletes them in order frees the most
    recently abandoned slot first.
    """
    root = Path(save_root)
    book = Path(journal) if journal is not None else journal_for(root)
    found, seen = [], set()
    for e in reversed(_read_journal(book)):
        path = Path(str(e.get("path") or ""))
        if path in seen or not path.is_dir():
            continue
        seen.add(path)
        try:
            _require_save_shape(path, root)
        except Exception:                                  # noqa: BLE001
            continue                                       # a different world
        owned = _open_claim(path, book, max_age)
        if owned is not None:
            found.append(owned)
    return found


def claim_created(before: frozenset[int], save_root: Path = SAVE_ROOT,
                  journal: Optional[Path] = None) -> OwnedSlot:
    """Own the slot the game ACTUALLY created, given what existed before.

    **`claim_slot` predicts the slot and Darkest Dungeon does not agree.**
    The save list is COMPACT -- one row per campaign with the holes closed up
    -- so "the row for slot N" is not a thing, and clicking the empty letter
    makes a campaign wherever the game feels like. Measured 2026-09-21: the
    boot asked for slot 2, on a disk holding profiles 0, 1, 8 and 9, and DD
    created **profile_7**. The loop then owned a slot that was never made, its
    teardown deleted nothing, and `profile_7` joined `profile_1` as an orphan
    no later run can claim or remove.

    Observing beats predicting here, and it is not a weaker safety argument
    but the same one made directly. `OwnedSlot`'s guarantee is *"a directory
    that existed before we started can never be wrapped in one of these"* --
    `claim_slot` enforces it by refusing an existing path, and this enforces
    it by construction, against a snapshot taken before the campaign was
    created. `before` must come from `slots_on_disk` BEFORE the boot; passing
    a stale or invented set is the one way to misuse this.

    Refuses on nothing new (no campaign was made) and on more than one new
    (the diff cannot say which is ours, and guessing is how the wrong save
    gets deleted). The claim is journalled like any other, so a crash between
    creating and deleting can still be adopted.
    """
    now = slots_on_disk(save_root)
    fresh = sorted(now - set(before))
    if not fresh:
        raise ResetRefused(
            "no new profile appeared, so no campaign was created and there "
            "is nothing to own")
    if len(fresh) > 1:
        raise ResetRefused(
            f"{len(fresh)} new profiles appeared ({fresh}) - the diff cannot "
            f"say which one is ours, and guessing is how the wrong save gets "
            f"deleted")
    slot = fresh[0]
    path = _profile_path(slot, save_root)
    if path is None:
        raise ResetRefused(f"cannot locate profile_{slot}")
    owned = OwnedSlot(slot=slot, path=path, claimed_at=time.time())
    _write_claim(owned, Path(journal) if journal is not None
                 else journal_for(Path(save_root)))
    return owned


def _profile_path(slot: int, save_root: Path) -> Optional[Path]:
    """`profile_<slot>` beside an existing one. None if none exists.

    The Steam user id is only discoverable from a directory that is already
    there, so a machine with no saves cannot be predicted and this says so
    rather than inventing a path.
    """
    for existing in sorted(Path(save_root).glob(PROFILE_GLOB)):
        return existing.parent / f"profile_{slot}"
    return None


def _require_save_shape(path: Path, root: Path) -> None:
    """The path must be one `SAVE_ROOT.glob(PROFILE_GLOB)` could have yielded.

    NOT "directly under the save root", which was the first version of this
    check and is wrong: `PROFILE_GLOB` is `*/262060/remote/profile_*`, so a
    profile's parent is the app's `remote` directory and its grandparent is the
    app id. That check would have refused every legitimate call while still
    passing anything shaped like `<root>/profile_1`, which is the opposite of
    what it is for.
    """
    try:
        path.resolve().relative_to(Path(root).resolve())
    except ValueError:
        raise ResetRefused(f"{path} is outside the save root") from None
    if path.parent.name != "remote" or path.parent.parent.name != "262060":
        raise ResetRefused(
            f"{path} is not a Darkest Dungeon profile directory "
            "(expected .../262060/remote/profile_N)")


def delete_owned(owned: OwnedSlot, *, screen: str = "",
                 game_running: bool = True,
                 save_root: Path = SAVE_ROOT,
                 recheck: float = CLOUD_RECHECK,
                 journal: Optional[Path] = None) -> bool:
    """Remove the directory `owned` names. True when it is gone and stays gone.

    The campaign must not be loaded. Two states satisfy that, and the caller
    says which:

    * `game_running=False` -- the game is CLOSED, which is the strongest form
      and the one the overnight loop uses;
    * `screen == "title"` -- running, but back at the front end.

    **Closing the game is not merely tidier, it is required if the slot is
    going to be re-used.** Fran: deleting a save without restarting leaves the
    game bugged and it "doesn't load the new save properly". Observed exactly
    once that was known to look for: recreating a just-deleted slot in the same
    process skipped the difficulty screen and produced a campaign whose estate
    was named "Click the letter to begin..." with 0 gold -- the save-select
    hint text, captured as the name. DD caches the slot list, and a directory
    that reappears under it is not re-read.
    """
    journal = Path(journal) if journal is not None else journal_for(save_root)
    root = Path(save_root)
    path = Path(owned.path)

    if game_running and screen != "title":
        raise ResetRefused(
            f"the game is showing {screen or 'an unrecognised screen'!r}, not "
            "the title screen -- the campaign may still be loaded")
    if path.name != f"profile_{owned.slot}":
        raise ResetRefused(f"{path.name} is not profile_{owned.slot}")
    _require_save_shape(path, root)
    if not path.exists():
        _release_claim(owned, journal)
        return True                             # already gone; nothing to do

    shutil.rmtree(path)
    if path.exists():
        raise ResetRefused(f"{path.name} survived the delete")
    if recheck:
        time.sleep(recheck)
        if path.exists():
            raise ResetRefused(
                f"{path.name} came BACK after {recheck:.0f}s -- Steam Cloud "
                "restored it. Delete with the game closed, or turn cloud sync "
                "off for this app.")
    _release_claim(owned, journal)
    return True


# ── getting to the title screen ──

def exit_to_main_menu(win, backend, *, say=print, act: bool = False,
                      timeout: float = SCREEN_TIMEOUT) -> bool:
    """ESC, then "Exit to Main Menu", then wait for the title screen.

    **Every step is gated on the screen being what it should be**, and the
    reason is one row: `Exit to Desktop` sits 37.5 px under the row we want at
    720p. A blind click on a screen that merely resembles the pause menu quits
    the game, which for an overnight run costs the whole night rather than one
    iteration.

    The confirm is `MODAL_OK`, which DD draws at one fixed position for every
    modal -- measured twice independently, at the DLC dialog and at the
    underprepared-embark dialog, agreeing to (0.0000, 0.0007).
    """
    from bridge import screen as screen_mod          # noqa: PLC0415
    from bridge.layout import load as load_layout    # noqa: PLC0415

    fractional = load_layout()
    if fractional is None:
        say("  [reset] no overlay_layout.json - cannot resolve coordinates")
        return False
    resolved = fractional.resolve(win)

    def look() -> str:
        img = screen_mod.grab(win)
        return "" if img is None else screen_mod.recognise(img)

    here = look()
    if here == "title":
        say("  [reset] already at the title screen")
        return True

    if not act:
        say(f"  [reset] WOULD press ESC (screen: {here or 'UNKNOWN'}), then "
            f"click Exit to Main Menu at {resolved.pause_exit_to_main_menu}")
        return False

    if here != "pause_menu":
        say(f"  [reset] ESC (from {here or 'UNKNOWN'})")
        backend.key(VK_ESCAPE)
        # **ESC does two different things and both are fine.** In a campaign
        # it opens the pause menu; in the FRONT END there is no pause menu and
        # it simply steps back, which can land on the title directly. The
        # first version demanded the pause menu, so an iteration that happened
        # to be on the save list reported "the pause menu did not come up" and
        # the whole iteration was written off -- on a game that was one step
        # from where it needed to be.
        here = _await_any(look, ("pause_menu", "title"), timeout)
        # **ESC from an OVERLAY only closes the overlay.** Measured
        # 2026-09-23: from the estate map it landed on the Hamlet and the
        # iteration was written off as stuck. Those screens are one more ESC
        # from the pause menu, so press again -- a bounded number of times.
        for _ in range(3):
            if here in ("pause_menu", "title") or here not in ESC_STEPS_BACK:
                break
            say(f"  [reset] ESC again (from {here})")
            backend.key(VK_ESCAPE)
            here = _await_any(look, ("pause_menu", "title"), timeout)
        if here == "title":
            say("  [reset] ESC went straight back to the title")
            return True
        if here != "pause_menu":
            say(f"  [reset] REFUSE: neither the pause menu nor the title "
                f"came up (screen: {here or 'UNKNOWN'})")
            return False

    say(f"  [reset] Exit to Main Menu {resolved.pause_exit_to_main_menu}")
    backend.click(resolved.pause_exit_to_main_menu)
    time.sleep(0.6)

    # DD asks for confirmation. `modal` is a CLASS of screen here, not one
    # screen -- every centred dialog hashes alike -- which is exactly why the
    # click is the one fixed position they all share rather than something
    # measured per dialog.
    if look() == "modal":
        say(f"  [reset] confirm {resolved.menu_point('modal_ok')}")
        backend.click(resolved.menu_point("modal_ok"))

    here = _await(look, "title", timeout)
    if here != "title":
        say(f"  [reset] REFUSE: never reached the title screen "
            f"(screen: {here or 'UNKNOWN'})")
        return False
    say("  [reset] at the title screen")
    return True


def _await_any(look, wanted, timeout: float) -> str:
    """Poll until `look` returns one of `wanted`. Returns what it saw last."""
    deadline = time.time() + timeout
    seen = ""
    while time.time() < deadline:
        seen = look()
        if seen in wanted:
            return seen
        time.sleep(SCREEN_POLL)
    return seen


def _await(look, want: str, timeout: float) -> str:
    """Poll `look` until it returns `want`, or give up. Returns what it saw."""
    deadline = time.time() + timeout
    seen = ""
    while time.time() < deadline:
        seen = look()
        if seen == want:
            return seen
        time.sleep(SCREEN_POLL)
    return seen


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--status", action="store_true",
                    help="what is on disk and which screen is up")
    ap.add_argument("--to-menu", action="store_true",
                    help="ESC -> Exit to Main Menu")
    ap.add_argument("--act", action="store_true",
                    help="with --to-menu, actually send the input")
    ap.add_argument("--process", default="Darkest.exe")
    args = ap.parse_args(argv)

    from bridge.window import find_window                # noqa: PLC0415
    win = find_window(args.process)

    if args.status or not args.to_menu:
        profiles = sorted(Path(SAVE_ROOT).glob(PROFILE_GLOB))
        print(f"save root : {SAVE_ROOT}")
        print(f"profiles  : {', '.join(p.name for p in profiles) or 'none'}")
        if win is None:
            print("game      : not running")
        else:
            from bridge import screen as screen_mod      # noqa: PLC0415
            img = screen_mod.grab(win)
            name = screen_mod.recognise(img) if img is not None else ""
            print(f"game      : running, showing {name or 'UNKNOWN'}")
        return 0

    if win is None:
        print("Darkest.exe is not running")
        return 1
    backend = SendInputBackend(win)
    return 0 if exit_to_main_menu(win, backend, act=args.act) else 1


if __name__ == "__main__":
    sys.exit(main())
