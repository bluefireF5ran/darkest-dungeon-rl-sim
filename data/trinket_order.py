"""The Trinket Inventory's "sort by rarity" order, from shipped data.

Measured live 2026-09-27 on a 20-trinket chest (20 of 20 cells): the sort is
the rarities file's order, then the ENGLISH DISPLAY NAME -- "Agility Talon,
Archer's Ring, Diseased Herb, Heartburst Hood, ..." -- not the id. Knowing it
lets the bridge place every cell right after an unequip-all, when the estate
save still holds the chest as it was BEFORE the heroes' trinkets came back
(the same night: a whole party equipped one cell off, witnessed by the
roster save).

DLC rarity files are ranked after the base file, in path order. That part is
an assumption no measurement has covered; `known_rarity` says whether a
trinket's rank rests on it.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from data.paths import GAME_DIR


def _load(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return {}


@lru_cache(maxsize=4)
def _tables(game_dir: str = str(GAME_DIR)):
    root = Path(game_dir)
    order: list = []
    base = root / "trinkets" / "base.rarities.trinkets.json"
    files = [base] + sorted(p for p in root.glob("dlc/**/*.rarities.trinkets.json")
                            if "modes" not in p.parts)
    base_ids = set()
    for i, f in enumerate(files):
        for r in _load(f).get("rarities", []) or []:
            rid = str(r.get("id") or "")
            if rid and rid not in order:
                order.append(rid)
                if i == 0:
                    base_ids.add(rid)
    rarity: dict = {}
    for f in [root / "trinkets" / "base.entries.trinkets.json"] + sorted(
            p for p in root.glob("dlc/**/*.entries.trinkets.json")
            if "modes" not in p.parts):
        for e in _load(f).get("entries", []) or []:
            if e.get("id") and e["id"] not in rarity:
                rarity[e["id"]] = str(e.get("rarity") or "")
    return {r: i for i, r in enumerate(order)}, base_ids, rarity


@lru_cache(maxsize=1)
def _names() -> dict:
    try:
        from data.comp_loadout_loader import CompLoadoutLoader  # noqa: PLC0415
        return dict(CompLoadoutLoader._load_english_entries(
            GAME_DIR, "str_inventory_title_trinket"))
    except Exception:                                            # noqa: BLE001
        return {}


def sort_key(trinket_id: str, game_dir: str = str(GAME_DIR)) -> tuple:
    rank, _base, rarity = _tables(game_dir)
    name = _names().get(trinket_id, trinket_id)
    return (rank.get(rarity.get(trinket_id, ""), 10**6), name.casefold(),
            trinket_id)


def known_rarity(trinket_id: str, game_dir: str = str(GAME_DIR)) -> bool:
    """True when the trinket's rarity is in the base file (measured order)."""
    _rank, base, rarity = _tables(game_dir)
    return rarity.get(trinket_id, "") in base


def rarity_sorted(ids, game_dir: str = str(GAME_DIR)) -> list:
    return sorted(ids, key=lambda t: sort_key(t, game_dir))
