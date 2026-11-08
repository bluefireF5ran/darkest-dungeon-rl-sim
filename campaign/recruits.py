"""The stage coach: who is on offer this week.

Data, all shipped:
- the class pool: every `campaign/roster/*roster.groups.json` (base: 15
  classes; DLC adds Musketeer, Runaway, Duelist). Shieldbreaker and the
  Flagellant arrive through town events instead, which F1 does not run;
- how many recruits and which levels: `stage_coach.building.json`
  (`number_of_recruits_upgrades`, `upgraded_recruits_upgrades`), keyed on the
  stage coach ranks the campaign has bought;
- what a recruit carries: the class's own `generation:` record in its
  `.info.darkest` (quirk counts, random combat skills, camping skills).

ASSUMPTIONS:
- classes are drawn uniformly from the pool;
- the experienced-recruit tiers are rolled highest first, each at its own
  `chance`, and a recruit is the first tier that hits (else level 0). The
  file gives one chance per tier and no combination rule;
- quirks are drawn uniformly from the non-disease quirks of the right
  polarity whose library `random_chance` is above 0, skipping one incompatible with a quirk already drawn;
- an experienced recruit's gear stays at rank 0 (no file says otherwise).
"""
from __future__ import annotations

import json
import random
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from bridge.town_state import Recruit
from data.darkest_parser import DarkestParser
from data.paths import GAME_DIR
from data.town_costs import load_hero_costs

COACH = ("campaign", "town", "buildings", "stage_coach", "stage_coach.building.json")
RESOLVE_XP_FOR_LEVEL = (0, 2, 8, 14, 24, 36, 48)  # roster.variables.json, read in tests


@dataclass(frozen=True)
class Generation:
    pos_quirks: tuple[int, int] = (1, 2)
    neg_quirks: tuple[int, int] = (1, 2)
    class_camping: int = 2
    shared_camping: int = 1
    combat_skills: int = 4


@lru_cache(maxsize=1)
def class_pool(game_dir: str = str(GAME_DIR)) -> tuple[str, ...]:
    g = Path(game_dir)
    files = [g / "campaign" / "roster" / "base.roster.groups.json"]
    files += sorted(p for p in (g / "dlc").rglob("*roster.groups.json") if "modes" not in p.parts)
    out: list[str] = []
    for f in files:
        if f.is_file():
            for grp in json.loads(f.read_text(encoding="utf-8")).get("groups", []):
                out.extend(c for c in grp.get("class_ids", []) if c not in out)
    return tuple(out)


@lru_cache(maxsize=1)
def coach_data(game_dir: str = str(GAME_DIR)) -> dict:
    d = json.loads(Path(game_dir).joinpath(*COACH).read_text(encoding="utf-8"))
    return d["data"]["stores"][0]["data"]


@lru_cache(maxsize=64)
def generation(hero_class: str, game_dir: str = str(GAME_DIR)) -> Generation:
    for path in Path(game_dir).rglob(f"heroes/{hero_class}/{hero_class}.info.darkest"):
        if "modes" in path.parts:
            continue
        for rec in DarkestParser.parse_file(str(path)):
            if rec.get("_type") == "generation":
                n = lambda k, d: int(rec.get(k, d))               # noqa: E731
                return Generation(
                    pos_quirks=(n("number_of_positive_quirks_min", 1), n("number_of_positive_quirks_max", 2)),
                    neg_quirks=(n("number_of_negative_quirks_min", 1), n("number_of_negative_quirks_max", 2)),
                    class_camping=n("number_of_class_specific_camping_skills", 2),
                    shared_camping=n("number_of_shared_camping_skills", 1),
                    combat_skills=n("number_of_random_combat_skills", 4))
    return Generation()


def recruit_count(coach_ranks: int, game_dir: str = str(GAME_DIR)) -> int:
    tiers = coach_data(game_dir)["number_of_recruits_upgrades"]
    return int(tiers[min(coach_ranks, len(tiers) - 1)]["amount"])


def roster_size(roster_ranks: int, game_dir: str = str(GAME_DIR)) -> int:
    tiers = coach_data(game_dir)["roster_size_upgrades"]
    return int(tiers[min(roster_ranks, len(tiers) - 1)]["amount"])


def _draw_level(rng: random.Random, upgraded_ranks: int, game_dir: str) -> tuple[int, dict]:
    tiers = coach_data(game_dir)["upgraded_recruits_upgrades"][:upgraded_ranks]
    for t in sorted(tiers, key=lambda t: -t["level"]):
        if rng.random() < float(t["chance"]):
            return int(t["level"]), t
    return 0, {}


@lru_cache(maxsize=1)
def unrandomisable_quirks(game_dir: str = str(GAME_DIR)) -> frozenset:
    """Quirks the library ships at `random_chance` 0 (the six `corvids_*`
    the crow hands out): never drawn for a recruit."""
    g = Path(game_dir)
    files = [g / "shared" / "quirk" / "quirk_library.json"]
    files += sorted(p for p in (g / "dlc").rglob("*quirk_library.json") if "modes" not in p.parts)
    out = set()
    for f in files:
        if f.is_file():
            for q in json.loads(f.read_text(encoding="utf-8")).get("quirks", []):
                if float(q.get("random_chance", 1)) <= 0:
                    out.add(q["id"])
    return frozenset(out)


def _quirks(rng: random.Random, store, n_pos: int, n_neg: int) -> tuple[str, ...]:
    never = unrandomisable_quirks()
    pool = [q for q in store.quirks.values() if not q.is_disease and q.id not in never]
    out: list[str] = []
    for positive, n in ((True, n_pos), (False, n_neg)):
        cands = [q for q in pool if q.is_positive == positive]
        rng.shuffle(cands)
        for q in cands:
            if len([x for x in out if store.quirks[x].is_positive == positive]) >= n:
                break
            if any(q.id in store.quirks[x].incompatible_quirks or x in q.incompatible_quirks
                   for x in out):
                continue
            out.append(q.id)
    return tuple(out)


def camping_skills(rng: random.Random, store, hero_class: str, n_class: int,
                   n_shared: int) -> tuple[str, ...]:
    """`n_class` from the skills only this class has, `n_shared` from the
    ones several classes share (ASSUMPTION: "shared" = more than one class)."""
    mine = [c for c in store.camping_skills.values() if hero_class in c.hero_classes]
    own = sorted(c.id for c in mine if len(c.hero_classes) == 1)
    shared = sorted(c.id for c in mine if len(c.hero_classes) > 1)
    return tuple(rng.sample(own, min(n_class, len(own))) +
                 rng.sample(shared, min(n_shared, len(shared))))


def draw_recruits(rng: random.Random, store, *, coach_ranks: int, upgraded_ranks: int,
                  first_visit: bool = False, game_dir: str = str(GAME_DIR)) -> list[Recruit]:
    pool = class_pool(game_dir)
    n = recruit_count(coach_ranks, game_dir)
    first = list(coach_data(game_dir).get("first_hero_classes", ())) if first_visit else []
    out: list[Recruit] = []
    for i in range(n):
        cls = first[i] if i < len(first) else rng.choice(pool)
        gen = generation(cls, game_dir)
        level, tier = _draw_level(rng, upgraded_ranks, game_dir)
        costs = load_hero_costs(game_dir, cls)
        tree = list(costs.skills) if costs is not None else []
        k = min(len(tree), gen.combat_skills + int(tier.get("number_of_extra_combat_skills", 0)))
        skills = tuple(rng.sample(tree, k))
        quirks = _quirks(rng, store,
                         rng.randint(*gen.pos_quirks) + int(tier.get("number_of_extra_positive_quirks", 0)),
                         rng.randint(*gen.neg_quirks) + int(tier.get("number_of_extra_negative_quirks", 0)))
        out.append(Recruit(slot=i, listing="hero_recruit", hero_class=cls,
                           resolve_xp=RESOLVE_XP_FOR_LEVEL[min(level, 6)],
                           quirks=quirks, skills=skills, row=i))
    return out
