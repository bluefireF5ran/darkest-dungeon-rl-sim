"""Everything the human did, as STRUCTURED events — the demonstration corpus.

`bridge/run.py` already prints a readable block per decision and marks it
MATCH/DIFFER. That log is for a person, and every analysis built on it so far
has had to parse the prose back into structure with regexes. Two of those
regexes were wrong in one session (`tools/retreat_audit.py`'s `+-0` error bar,
which discarded both ends of the distribution, and its hero-name prefix, which
under-counted Death's Door turns 7 against 14). **A prose log is not a dataset.**

So this writes a second, machine-readable stream beside it: one JSON object per
line, one line per thing that happened. Nothing here interprets — it records
what changed and what was advised, and leaves the reading to a tool.

## What it covers that `run.py`'s log did not

The watch loop keys entirely on `persist.raid.json` and `continue`s whenever
the save says the party is not in a raid. So **the Hamlet has never been
recorded at all**: recruiting, treatment, upgrades, trinkets, the wallet, which
quest was taken, who was put in the party. Fran, 2026-09-03: *"make sure it logs
everything, dungeon, hamlet, team management, money management etc."*

The town has no poll of its own here either — it is diffed. Two snapshots of
`TownState` plus the upgrade ledger, subtracted, is a complete account of what a
town visit did, and it needs no screen reading and no actuator.

## Three rules

- **It never raises into its caller.** The poll thread is a daemon and is the
  overlay's only source of updates; taking it down mid-raid to report a logging
  failure is strictly worse than losing a line. Every public method swallows.
- **It appends.** A session is one more chunk of corpus, not a replacement, and
  `run.py --log` already opens in append mode for the same reason.
- **It records, it does not judge.** A `DIFFER` here is "advised X, observed Y",
  never "the human was wrong". Which of A (bridge bug) / B (worse) / C (better)
  it was is a separate, human call — see `bridge/run.py`'s marking note.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

#: Bumped when an event's SHAPE changes in a way a reader must know about, so a
#: pooled corpus spanning a change is still readable. Adding a new field or a
#: new `kind` does not need a bump; renaming or removing one does.
#: 2 — `upgrades_bought` stopped counting a hired hero's existing trees as
#:     purchases. A schema-1 recording reports batches of ~11 trees per hire
#:     that never happened; the rest of a schema-1 file is unaffected, which is
#:     what makes the bump worth having rather than discarding the corpus.
SCHEMA = 2


class Journal:
    """Append-only JSONL sink. Construct with `None` to disable it entirely."""

    def __init__(self, path: Optional[str | Path]) -> None:
        self.path = Path(path) if path else None
        self._fh = None
        self._failed = False
        if self.path is not None:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self._fh = open(self.path, "a", encoding="utf-8", buffering=1)
            except OSError as exc:
                print(f"  (journal: could not open {self.path}: {exc})")
                self._failed = True

    @property
    def enabled(self) -> bool:
        return self._fh is not None

    def event(self, kind: str, **fields: Any) -> None:
        """One line. `kind` is the discriminator every reader switches on."""
        if self._fh is None:
            return
        rec = {"t": time.time(), "schema": SCHEMA, "kind": kind}
        rec.update(fields)
        try:
            self._fh.write(json.dumps(rec, default=_plain, ensure_ascii=False) + "\n")
        except Exception as exc:                      # noqa: BLE001
            # Once, not per line: a broken sink would otherwise print per poll
            # for the rest of the session and bury the advice it sits beside.
            if not self._failed:
                self._failed = True
                print(f"  (journal: write failed, continuing without it: {exc})")

    def close(self) -> None:
        if self._fh is not None:
            try:
                self._fh.close()
            except OSError:
                pass
            self._fh = None


def _plain(obj: Any) -> Any:
    """Last-resort encoder. A dataclass or a Path in a payload is a mistake
    rather than a crash — record its text and keep the line."""
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, (set, frozenset, tuple)):
        return list(obj)
    return repr(obj)


# ── the town, as a diff ────────────────────────────────────────────────────

def town_snapshot(state, upgrades: Optional[dict] = None) -> dict:
    """A plain-data picture of the Hamlet, suitable for subtracting.

    Keyed by hero **guid** rather than by roster position, because the roster
    reorders freely and a positional diff would report every hire as four
    changes. `RosterHero` exposes guid directly.
    """
    heroes = {}
    for h in getattr(state, "heroes", ()) or ():
        try:
            heroes[str(h.guid)] = {
                "class": h.hero_class, "resolve": h.resolve_level,
                "stress": round(float(getattr(h, "stress", 0.0) or 0.0), 1),
                "building": getattr(h, "building", "") or "",
                "in_party": bool(getattr(h, "in_party", False)),
            }
        except Exception:                             # noqa: BLE001, PERF203
            continue
    quests = {}
    for q in getattr(state, "quests", ()) or ():
        try:
            quests[str(q.key)] = {"id": q.quest_id, "dungeon": q.dungeon,
                                  "type": q.quest_type, "difficulty": q.difficulty,
                                  "length": q.length, "gold": q.gold_reward}
        except Exception:                             # noqa: BLE001, PERF203
            continue
    recruits = {}
    for r in getattr(state, "recruits", ()) or ():
        try:
            recruits[str(getattr(r, "slot", len(recruits)))] = getattr(
                r, "hero_class", "") or ""
        except Exception:                             # noqa: BLE001, PERF203
            continue
    return {
        "wallet": dict(getattr(state, "wallet", {}) or {}),
        "heroes": heroes,
        "quests": quests,
        "recruits": recruits,
        "trinkets": sorted(getattr(state, "trinket_inventory", []) or []),
        # `{(tree hash, hero guid): ranks}` from `heirlooms.read_rank_counts`.
        # JSON has no tuple keys, so it is stored joined and split back on read.
        "upgrades": {f"{a}:{b}": n for (a, b), n in (upgrades or {}).items()},
    }


def diff_town(before: dict, after: dict) -> list[tuple[str, dict]]:
    """`(kind, payload)` for everything that changed between two snapshots.

    **Every branch reports the BEFORE and the AFTER**, not just the delta. A
    corpus is read months later by someone reconstructing a decision, and
    "gold -1500" cannot answer "could he have afforded the other thing".
    """
    out: list[tuple[str, dict]] = []
    if not before or not after:
        return out

    # -- money -------------------------------------------------------------
    wb, wa = before.get("wallet", {}), after.get("wallet", {})
    moved = {k: (wb.get(k, 0), wa.get(k, 0))
             for k in set(wb) | set(wa) if wb.get(k, 0) != wa.get(k, 0)}
    if moved:
        out.append(("wallet", {
            "changed": {k: {"from": f, "to": t, "delta": t - f}
                        for k, (f, t) in moved.items()}}))

    # -- the roster --------------------------------------------------------
    hb, ha = before.get("heroes", {}), after.get("heroes", {})
    for guid in sorted(set(ha) - set(hb)):
        out.append(("hero_joined", {"guid": guid, **ha[guid]}))
    for guid in sorted(set(hb) - set(ha)):
        # Gone from the roster. WHY is not knowable from the town save alone —
        # died on the last raid, dismissed, or a name-plate the parser lost —
        # so this deliberately does not guess. Pair it with the raid's own
        # outcome event to resolve it.
        out.append(("hero_left", {"guid": guid, **hb[guid]}))
    for guid in sorted(set(hb) & set(ha)):
        b, a = hb[guid], ha[guid]
        fields = {k: {"from": b[k], "to": a[k]} for k in a if b.get(k) != a[k]}
        if fields:
            out.append(("hero_changed",
                        {"guid": guid, "class": a.get("class", ""), **fields}))

    # -- the coach ---------------------------------------------------------
    rb, ra = before.get("recruits", {}), after.get("recruits", {})
    taken = {s: rb[s] for s in set(rb) - set(ra)}
    if taken:
        out.append(("recruits_gone", {"slots": taken, "offered_now": ra}))
    if set(ra) - set(rb):
        out.append(("recruits_offered", {"slots": ra}))

    # -- trinkets and upgrades --------------------------------------------
    tb, ta = set(before.get("trinkets", [])), set(after.get("trinkets", []))
    if tb != ta:
        out.append(("trinkets", {"gained": sorted(ta - tb),
                                 "lost": sorted(tb - ta), "held": len(ta)}))
    ub, ua = before.get("upgrades", {}), after.get("upgrades", {})
    # A HIRED HERO ARRIVES CARRYING TREES, AND THAT IS NOT A PURCHASE.
    #
    # `persist.upgrades.json` keys every purchase by `(tree hash, hero guid)`,
    # so a recruit walking in with seven unlocked skills and three camp skills
    # adds ~11 entries that were not there before. The first real recording
    # session reported batches of 92, 78, 95 and 99 "bought" trees against a
    # wallet that never held more than 6,210 gold — at ~1,000 a skill rank the
    # arithmetic refuses it outright.
    #
    # The test is on the LEDGER, not on the roster, because the two files are
    # written at different times: a hero can join on one poll and their trees
    # show up on the next, so "did they just join the roster" misses it. An
    # owner appearing in the ledger for the FIRST time with a whole kit is an
    # arrival; an owner who already had entries gaining more is a purchase.
    #
    # A genuinely fresh recruit who then buys their first rank reads 0 -> 1,
    # which is under the threshold and correctly counts as a purchase. Building
    # trees are never filtered: their instance is not a hero guid, so they are
    # judged on the same first-appearance rule and arrive one at a time.
    owners_before = {k.rsplit(":", 1)[-1] for k in ub}
    gained: dict[str, int] = {}
    for k in ua:
        if ua[k] != ub.get(k, 0):
            gained[k.rsplit(":", 1)[-1]] = gained.get(k.rsplit(":", 1)[-1], 0) + 1
    arrived = {owner for owner, n in gained.items()
               if owner not in owners_before and n >= 2}
    bought = {k: {"from": ub.get(k, 0), "to": ua[k]}
              for k in ua
              if ua[k] != ub.get(k, 0) and k.rsplit(":", 1)[-1] not in arrived}
    if bought:
        # Naming the tree needs the hash inverted against `upgrades/**` — a
        # reader's job, not a poll thread's.
        out.append(("upgrades_bought", {"trees": bought}))

    # -- the quest map -----------------------------------------------------
    qb, qa = before.get("quests", {}), after.get("quests", {})
    if set(qb) != set(qa):
        out.append(("quests", {"offered": qa, "gone": sorted(set(qb) - set(qa))}))
    return out
