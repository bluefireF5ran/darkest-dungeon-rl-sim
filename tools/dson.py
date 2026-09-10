"""CLI over the save-file parser.

The parser itself moved to `data/dson.py` on 2026-09-08. It reads the game's
`persist.*.json` files, which are not JSON at all but a binary format (magic
`01 B1 00 00`), and that makes it a member of the data layer in every way that
matters: it is how `data/heirlooms.py`, `data/hero_skills.py` and
`data/hero_value.py` read shipped game state, and it imports nothing from this
repo at all.

Living in `tools/` made it the one thing `data/` reached UPWARD for, and
`tests/suite/test_architecture.py` now forbids exactly that. Moving the module
fixed the cause; this file stays so the documented command keeps working:

    python tools/dson.py "<profile>/persist.roster.json" --find heroes
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data.dson import main  # noqa: E402  (needs the path insert above)

if __name__ == "__main__":
    raise SystemExit(main())
