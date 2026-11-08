"""Who a hero IS to the Hamlet: their level band and their role.

Fran, 2026-09-26, the spec every town decision reads (docs/NEXT.md, "the
Hamlet, nuanced"):

* **Bands**: resolve levels 0-2, 3-4 and 5+. The roster keeps at most 4 of a
  class in each of the two low bands and 5 at the top, and the low-band cap
  TIGHTENS as the roster's average level rises -- "we want to keep
  advancing, not the other way around".
* **Roles**:
    - ``invested`` -- real gold sunk (an Experienced Recruit arrives with
      every skill, skill and gear rank 2 and four camp skills: 6,250 gold),
      or a level-3+ band earned in raids. Kept usable; the Hamlet spends on
      them as needed.
    - ``nurture`` -- a lower hero with a great quirk *for their class*. A
      luxury: levelled, quirk-locked and geared out of spare money.
    - ``fodder`` -- everyone else. Provisions only, "using them as they are";
      dismissed when worn and replaced from the coach.
* **Nurture rank is almost all QUIRKS**, and not the global tier list alone:
  "not every quirk works the same for every hero". The curated comps name the
  quirks each class is built around (1,364 of 1,908 curated hero slots list
  positive quirks), so a quirk counts in proportion to how much THAT class's
  comps want it.

Everything here is a pure function of the save and shipped data, so it is
tested without a game. Thresholds are declared conventions, named below.
"""
from __future__ import annotations

import glob
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Callable, Iterable, Optional

from data.quirk_tiers import POSITIVE_TIERS

REPO = Path(__file__).resolve().parent.parent

#: Roster caps per class, per band (Fran).
BAND_CAPS = {"0-2": 4, "3-4": 4, "5+": 5}
#: The 0-2 cap drops by one per full level the roster's mean resolve level
#: sits above this, never below `MIN_LOW_CAP`. A declared convention.
LOW_CAP_PIVOT = 2.0
MIN_LOW_CAP = 1
#: Gold sunk (data/hero_value.py) that makes a hero "invested".
INVESTED_GOLD = 2000
#: A resolve level earned in raids that makes a hero invested by itself.
INVESTED_LEVEL = 3
#: Nurture score a hero needs to be worth nurturing. With the weights below,
#: one S quirk the class's comps use a lot scores ~7; a lone B quirk nobody
#: uses scores 1. A declared convention.
NURTURE_MIN = 4.0
TIER_WEIGHT = {"S": 3.0, "A": 2.0, "B": 1.0, "C": 0.5}
NEGATIVE_WEIGHT = 0.5

QUIRK_TIER = {q: t for t, ids in POSITIVE_TIERS.items() for q in ids}


def band_of(level: int) -> str:
    level = int(level or 0)
    return "0-2" if level <= 2 else ("3-4" if level <= 4 else "5+")


def band_caps(mean_level: float) -> dict:
    """The per-class caps for a roster whose mean resolve level is this."""
    caps = dict(BAND_CAPS)
    drop = max(0, int(float(mean_level) - LOW_CAP_PIVOT))
    caps["0-2"] = max(MIN_LOW_CAP, caps["0-2"] - drop)
    return caps


def _key(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(name).lower()).strip("_")


@lru_cache(maxsize=1)
def class_quirk_share(root: str = str(REPO)) -> dict:
    """`{class_id: {quirk_id: share of that class's curated slots listing it}}`."""
    counts: dict = defaultdict(Counter)
    slots: Counter = Counter()
    for f in glob.glob(str(Path(root) / "metadata/COMPS/curated_comps/*.json")):
        try:
            data = json.loads(Path(f).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for h in data.get("heroes", []) or []:
            cls = _key(h.get("heroClass", ""))
            if not cls:
                continue
            slots[cls] += 1
            wanted = set()
            for block in ("quirks", "lockedQuirks"):
                for q in ((h.get(block) or {}).get("positive") or []):
                    wanted.add(_key(q))
            for q in wanted:
                counts[cls][q] += 1
    return {cls: {q: n / slots[cls] for q, n in c.items()}
            for cls, c in counts.items()}


def nurture_score(hero_class: str, quirks: Iterable[str],
                  share: Optional[dict] = None,
                  negative: Optional[Callable[[str], bool]] = None) -> float:
    """Tier weight of each positive quirk x (1 + 2 x how much this class's
    curated comps want it), minus `NEGATIVE_WEIGHT` per negative quirk.

    `negative(quirk_id)` says which untiered quirks are negative (the store's
    `is_positive`); without it an untiered quirk counts for nothing, which is
    the direction that never invents a penalty."""
    share = class_quirk_share() if share is None else share
    wanted = share.get(_key(hero_class), {})
    score = 0.0
    for q in quirks or ():
        q = _key(q)
        tier = QUIRK_TIER.get(q)
        if tier is not None:
            score += TIER_WEIGHT.get(tier, 0.0) * (1.0 + 2.0 * wanted.get(q, 0.0))
        elif q and negative is not None and negative(q):
            score -= NEGATIVE_WEIGHT
    return round(score, 2)


def store_negative(store) -> Optional[Callable[[str], bool]]:
    """`negative(quirk_id)` off the data store, or None without one."""
    quirks = getattr(store, "quirks", None) if store is not None else None
    if not quirks:
        return None
    return lambda q: q in quirks and not getattr(quirks[q], "is_positive", False)


@dataclass(frozen=True)
class HeroRole:
    guid: int
    hero_class: str
    level: int
    band: str
    role: str            # invested | nurture | fodder
    sunk: int            # gold already invested (data/hero_value.py)
    nurture: float       # quirk score for this class
    stress: float

    def label(self) -> str:
        return (f"{self.hero_class}#{self.guid} L{self.level} {self.role} "
                f"sunk {self.sunk}g nurture {self.nurture:.1f} "
                f"stress {self.stress:.0f}")


def role_of(level: int, sunk: int, nurture: float, gear: int = 0) -> str:
    """Invested is GOLD or a band earned in raids, never level 1 alone: a
    hero who reached level 1 on XP with nothing bought is Fran's Mortmain
    case, and the answer for her is "already done her role" unless a quirk
    says otherwise. An Experienced Recruit arrives with 6,250 sunk.

    `gear` is the save's own weapon+armour rank, the witness that works
    without the upgrades file: on the live roster (2026-09-27) all ten heroes
    with rank 1 gear are the Experienced Recruits and the 114 at rank 0 are
    not."""
    if (int(level or 0) >= INVESTED_LEVEL or int(sunk or 0) >= INVESTED_GOLD
            or int(gear or 0) > 0):
        return "invested"
    if nurture >= NURTURE_MIN:
        return "nurture"
    return "fodder"


def roles(town, profile=None, store=None) -> dict:
    """`{guid: HeroRole}` for the whole roster. `sunk` comes from the save's
    purchases when `profile` is given, else 0 (level still decides)."""
    heroes = list(getattr(town, "heroes", []) or [])
    sunk: dict = {}
    if profile is not None:
        try:
            from data.hero_value import party_value      # noqa: PLC0415
            for rh, v in zip(heroes, party_value(profile, [h.hero for h in heroes], store)):
                sunk[rh.guid] = int(getattr(v, "total", 0) or 0)
        except Exception:                                # noqa: BLE001
            sunk = {}
    negative = store_negative(store)
    out = {}
    for rh in heroes:
        quirks = list(getattr(rh.hero, "quirks", None) or [])
        score = nurture_score(rh.hero_class, quirks, negative=negative)
        s = sunk.get(rh.guid, 0)
        out[rh.guid] = HeroRole(
            guid=rh.guid, hero_class=rh.hero_class, level=int(rh.resolve_level),
            band=band_of(rh.resolve_level),
            role=role_of(rh.resolve_level, s, score,
                         int(getattr(rh.hero, "weapon_rank", 0) or 0)
                         + int(getattr(rh.hero, "armour_rank", 0) or 0)),
            sunk=s, nurture=score, stress=float(rh.stress))
    return out


def mean_level(town) -> float:
    heroes = list(getattr(town, "heroes", []) or [])
    return (sum(int(h.resolve_level) for h in heroes) / len(heroes)) if heroes else 0.0

@dataclass(frozen=True)
class Bill:
    """Fran's "is it worth it", per hero: what is sunk, what is still to buy,
    and the verdict the role gives it (docs/NEXT.md step 2)."""
    role: HeroRole
    sunk: int
    to_unlock: int        # skills not yet owned, at the Guild's unlock price
    to_rank: int          # owned skills and gear up to rank level+1
    to_destress: int      # stress (and with it any affliction) at the Abbey
    to_lock: int          # S/A quirks not yet locked, at the Sanitarium
    afflicted: bool
    verdict: str

    @property
    def to_buy(self) -> int:
        return self.to_unlock + self.to_rank + self.to_destress + self.to_lock

    def text(self) -> str:
        head = self.role.label() + (" AFFLICTED" if self.afflicted else "")
        return (f"{head}\n"
                f"  sunk {self.sunk:,}g; to buy {self.to_buy:,}g = unlocks "
                f"{self.to_unlock:,} + ranks {self.to_rank:,} + stress "
                f"{self.to_destress:,} + quirk locks {self.to_lock:,}\n"
                f"  verdict: {self.verdict}")


def verdict_for(role: str, afflicted: bool) -> str:
    if role == "fodder":
        return ("has done its role: provisions only, no Hamlet spend; "
                "dismiss when worn" + (" (the affliction is the wear)" if afflicted else ""))
    if role == "nurture":
        return "nurture: a luxury -- de-stress, lock, gear and skills from spare gold, by score"
    return "invested: keep usable -- de-stress under the 10% cap; skills when the comp needs them"


def bill(profile, rh, hr: HeroRole, store=None, game_dir=None,
         gold_per_stress: float = 0.0) -> Bill:
    from data.hero_skills import gear_ranks, unlocked          # noqa: PLC0415
    from data.hero_value import locked_quirks, quirk_lock_prices  # noqa: PLC0415
    from data.paths import GAME_DIR                             # noqa: PLC0415
    from data.town_costs import load_hero_costs                 # noqa: PLC0415
    game_dir = str(game_dir or GAME_DIR)
    hero = rh.hero
    costs = load_hero_costs(game_dir, hero.hero_class)
    to_unlock = to_rank = to_lock = 0
    target = min(5, hr.level + 1)
    if costs is not None:
        have = unlocked(profile, hero, game_dir)
        prices = [r[0] for r in costs.skills.values() if r]
        to_unlock = max(0, len(costs.skills) - len(have)) * (prices[0] if prices else 0)
        for skill, rank in have.items():
            ladder = costs.skills.get(skill, ())
            to_rank += sum(ladder[max(1, int(rank)):target])
        gear = gear_ranks(profile, hero, game_dir)
        to_rank += sum(costs.weapon[gear.get("weapon", 0):target - 1])
        to_rank += sum(costs.armour[gear.get("armour", 0):target - 1])
    roster = Path(profile) / "persist.roster.json"
    locked = set(locked_quirks(roster).get(hr.guid, ())) if roster.exists() else set()
    good = [q for q in (hero.quirks or ())
            if QUIRK_TIER.get(_key(q)) in ("S", "A") and q not in locked]
    prices = quirk_lock_prices(game_dir)
    to_lock = len(good) * (prices[0] if prices else 0)
    afflicted = bool(getattr(hero, "affliction_id", None))
    return Bill(role=hr, sunk=hr.sunk, to_unlock=to_unlock, to_rank=to_rank,
                to_destress=int(round(float(hr.stress) * gold_per_stress)),
                to_lock=to_lock, afflicted=afflicted,
                verdict=verdict_for(hr.role, afflicted))
