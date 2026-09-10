r"""The two absolute paths this project is built on, defined once.

They were defined in `env/gym_env.py` and then re-declared, verbatim, in about
a hundred other files: 73 test files repeat `GAME_DIR` and 43 repeat
`CSV_DIR`, of which **31 hardcode the absolute string
`F:\Personal_Fran\...\SIM`** -- so the repo was pinned to one drive letter
by its own test suite. `CLAUDE.md` conceded the consequence for years: "if you
move either, grep `tests/` for both names -- there is no single place to
change."

This is that place.

`data/` is the right home because it is the bottom of the dependency graph
(`engine/` imports `data.types` and nothing else; `env/` imports both) and
because `data/store_cache.py` already reasons about the two roots together in
`fingerprint(csv_dir, game_dir)` and already owns this project's env-var
escape hatches.

**`CSV_DIR` is the repo ROOT, not `metadata/`.** The extra level is known in
exactly one place, `data/csv_loader.py::csv_root`, and this module does not
duplicate that logic -- it supplies the root that function takes as an
argument.

Both are overridable, which is what unpins the drive:

    DDSIM_GAME_DIR   where Darkest Dungeon is installed
    DDSIM_CSV_DIR    the repo root, if you are running from somewhere else

A `DDSIM_*` variable pointing at a directory that does not exist is a silent
failure rather than a loud one -- `CsvLoader.find_csv` returns `None` for a
missing directory instead of raising, so a wrong root yields an EMPTY store.
`describe()` exists to make that visible from a CLI.
"""
from __future__ import annotations

import os
from pathlib import Path

#: The repo root, derived rather than hardcoded. This is `CSV_DIR`'s default,
#: and on the machine this project was written on it is byte-identical to the
#: literal that used to sit in `env/gym_env.py`.
REPO_ROOT = Path(__file__).resolve().parent.parent

#: Where the game is installed. There is no deriving this one: the combat math
#: is parsed out of the install, so this is a genuine hard dependency and the
#: default is the machine it was developed on.
DEFAULT_GAME_DIR = Path(
    r"D:\Program Files (x86)\Steam\steamapps\common\DarkestDungeon")

GAME_DIR = Path(os.environ.get("DDSIM_GAME_DIR") or DEFAULT_GAME_DIR)
CSV_DIR = Path(os.environ.get("DDSIM_CSV_DIR") or REPO_ROOT)


def describe() -> str:
    """One line per root, saying whether it is actually there.

    A missing root does not raise anywhere in the loader chain, so this is how
    a CLI reports "your store is empty because your path is wrong".
    """
    rows = []
    for name, value, var in (("GAME_DIR", GAME_DIR, "DDSIM_GAME_DIR"),
                             ("CSV_DIR", CSV_DIR, "DDSIM_CSV_DIR")):
        mark = "ok     " if value.is_dir() else "MISSING"
        src = f" (from {var})" if os.environ.get(var) else ""
        rows.append(f"  {name:<9} {mark}  {value}{src}")
    return "\n".join(rows)


if __name__ == "__main__":
    print(describe())
