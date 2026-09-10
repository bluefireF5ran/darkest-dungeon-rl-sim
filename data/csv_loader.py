from __future__ import annotations
import csv
from pathlib import Path
from typing import Any

# Where the metadata CSV directories live under `csv_dir` (2026-08-20).
#
# `CSV_DIR` is the repo root — hardcoded at the top of `env/gym_env.py` and
# re-declared in ~35 test files — and `HEROES/`, `COMPS/` and the other six sat
# directly in it, which is eight of the root's directories spent on one thing.
# They moved under `metadata/`; callers still pass the repo root, and this is
# the only place that knows about the extra level.
CSV_SUBDIR = "metadata"


def csv_root(csv_dir: str | Path) -> Path:
    """The directory the metadata CSV folders sit in.

    Falls back to `csv_dir` itself when there is no `metadata/`, so an archived
    copy or an older checkout still resolves. That fallback is not decoration:
    `CsvLoader.find_csv` returns `None` for a missing directory rather than
    raising, so a path this function got wrong would produce an empty store
    instead of an error — the failure mode this project has been bitten by
    before.
    """
    base = Path(csv_dir)
    nested = base / CSV_SUBDIR
    return nested if nested.is_dir() else base


class CsvLoader:
    """Load CSV exports into lists of dicts."""

    @staticmethod
    def load_table(path: str | Path) -> list[dict[str, str]]:
        rows: list[dict[str, str]] = []
        with open(path, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f, delimiter=",")
            for row in reader:
                cleaned = {k.strip(): v.strip() for k, v in row.items() if k}
                rows.append(cleaned)
        return rows

    @staticmethod
    def find_csv(data_dir: str | Path, prefix: str, all_variant: bool = False) -> Path | None:
        """Find a CSV by prefix match, preferring the _all variant if requested."""
        base = Path(data_dir)
        if not base.exists():
            return None
        suffix = "_all.csv" if all_variant else ".csv"
        for f in base.iterdir():
            if f.name.startswith(prefix) and f.name.endswith(suffix):
                return f
        return None

    @staticmethod
    def find_all_csvs(data_dir: str | Path, prefix: str) -> list[Path]:
        """Find both clean and _all CSV files for an entity type."""
        base = Path(data_dir)
        if not base.exists():
            return []
        results = []
        for f in base.iterdir():
            if f.name.startswith(prefix) and f.name.endswith(".csv"):
                results.append(f)
        return results
