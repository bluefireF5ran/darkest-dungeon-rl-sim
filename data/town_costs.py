"""What things cost in the Hamlet, in gold — the currency PLAYBOOK 2.6 prices a
retreat in.

**Deliberately NOT wired into `DataLoader.load_all`.** Nothing in the combat
simulator spends gold; only `bridge/retreat.py` needs these, and it loads them
lazily.

Two tables, both real game data:

- `campaign/town/buildings/abbey/abbey.building.json` — each activity carries
  `cost_upgrades` (1000 / 850 / 700 gold as the building is upgraded) beside
  `stress_upgrades` (45 / 56 / 70 stress healed). Dividing gives **gold per point
  of stress**, which is the exchange rate the whole retreat decision turns on.
- `upgrades/heroes/<class>.upgrades.json` — every weapon rank, armour rank and
  skill rank with its gold price. A dead hero is not worth "a hero"; a fresh
  recruit is free from the stage coach (PLAYBOOK 0.1). What dies with them is
  the **gold already sunk into their ranks**, and that is exactly what these
  trees add up to.

The Sanitarium is read by `load_sanitarium` (2026-10-12, Fran's option 4).
The Tavern is not read for this purpose. The Abbey is the one PLAYBOOK 0.3 uses —
the Tavern is refused outright there, because its activities gamble your
trinkets.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Optional, Sequence

# Tree tags in upgrades/heroes/<class>.upgrades.json.
TAG_WEAPON = "weapon"
TAG_ARMOUR = "armour"
TAG_SKILL = "combat_skill"


@dataclass(frozen=True)
class StressTreatment:
    """One stress activity's price, effect and CAPACITY, per upgrade tier.

    `building` is "abbey" or "tavern" -- both heal stress, both ship the same
    file shape, and a planner that read only the Abbey would ignore half the
    capacity a real player uses.
    """
    activity: str
    gold: tuple[int, ...]
    heal: tuple[float, ...]
    slots: tuple[int, ...] = ()
    building: str = "abbey"
    #: The upgrade RANK each step needs ("" for the base step), from the
    #: building file's `upgrade_requirement_code`. Price, heal and slots are
    #: separate ladders on one tree -- the Abbey's meditation: heal a/d, price
    #: b/e, slots c/f -- so one `tier` for all three was wrong the moment only
    #: rank `a` was owned (Fran, 2026-09-26: "right now we only can use the
    #: leftmost box").
    gold_codes: tuple[str, ...] = ()
    heal_codes: tuple[str, ...] = ()
    slot_codes: tuple[str, ...] = ()
    #: Quirks that bar a hero from this activity (the activity's
    #: `not_have_quirks` requirement: an Alcoholic is not sent to pray).
    barred_quirks: tuple[str, ...] = ()

    @staticmethod
    def _step(codes, owned) -> int:
        """The highest step whose requirement is owned (the base always is)."""
        best = 0
        for i, code in enumerate(codes or ()):
            if not code or code in owned:
                best = i
        return best

    def owned_cost(self, owned=()) -> int:
        return self.cost_at(self._step(self.gold_codes, set(owned)))

    def owned_heal(self, owned=()) -> float:
        return self.heal_at(self._step(self.heal_codes, set(owned)))

    def owned_slots(self, owned=()) -> int:
        return self.slots_at(self._step(self.slot_codes, set(owned)))

    def gold_per_stress(self, tier: int = 0) -> float:
        tier_gold = self.gold[min(tier, len(self.gold) - 1)] if self.gold else 0
        tier_heal = self.heal[min(tier, len(self.heal) - 1)] if self.heal else 0.0
        return (tier_gold / tier_heal) if tier_heal else 0.0

    def heal_at(self, tier: int = 0) -> float:
        """Stress points this activity removes at `tier`."""
        return self.heal[min(tier, len(self.heal) - 1)] if self.heal else 0.0

    def cost_at(self, tier: int = 0) -> int:
        return self.gold[min(tier, len(self.gold) - 1)] if self.gold else 0

    def slots_at(self, tier: int = 0) -> int:
        """How many heroes this activity takes at once. 1 un-upgraded, 3 maxed.

        Defaults to 1 rather than 0 when the field is missing: an activity that
        exists can always take somebody, and a 0 would silently remove the
        building from the plan.
        """
        if not self.slots:
            return 1
        return self.slots[min(tier, len(self.slots) - 1)]


@dataclass(frozen=True)
class HeroUpgradeCosts:
    """Gold per rank, cumulative from a fresh recruit."""
    hero_class: str
    weapon: tuple[int, ...] = ()
    armour: tuple[int, ...] = ()
    skills: dict[str, tuple[int, ...]] = field(default_factory=dict)

    @staticmethod
    def _sunk(ranks: Sequence[int], rank: int) -> int:
        """Gold to reach `rank`. Rank 0 is what the recruit arrives with."""
        return sum(ranks[:max(0, rank)])

    def investment(self, weapon_rank: int = 0, armour_rank: int = 0,
                   skill_ranks: Optional[dict[str, int]] = None) -> int:
        total = self._sunk(self.weapon, weapon_rank) + self._sunk(self.armour, armour_rank)
        for skill_id, rank in (skill_ranks or {}).items():
            total += self._sunk(self.skills.get(skill_id, ()), rank)
        return total


def _costs(requirements) -> tuple[int, ...]:
    out = []
    for req in requirements or []:
        for currency in req.get("currency_cost") or []:
            if currency.get("type") == "gold":
                out.append(int(currency.get("amount") or 0))
                break
        else:
            out.append(0)
    return tuple(out)


#: Both buildings that remove stress. Same file shape, same resource.
STRESS_BUILDINGS = ("abbey", "tavern")


@lru_cache(maxsize=4)
def load_stress_treatments(game_dir: str,
                           buildings: Sequence[str] = STRESS_BUILDINGS
                           ) -> tuple[StressTreatment, ...]:
    """Every stress activity the town ships, with price, heal and capacity.

    `buildings` defaults to both. Passing `("abbey",)` reproduces what
    `bridge/retreat.py` has always seen, and that caller takes the CHEAPEST
    rate, so widening the default cannot make a retreat look dearer than it
    did -- only cheaper, if the Tavern undercuts the Abbey.
    """
    out = []
    for building in buildings:
        path = (Path(game_dir) / "campaign" / "town" / "buildings" / building
                / f"{building}.building.json")
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                raw = json.load(fh)
        except (OSError, ValueError):
            continue
        for activity in raw.get("data", {}).get("activities", []):
            data = activity.get("data") or {}
            gold = tuple(int((u.get("cost_currency") or {}).get("amount") or 0)
                         for u in data.get("cost_upgrades") or [])
            heal = tuple(float(u.get("heal_low") or 0)
                         for u in data.get("stress_upgrades") or [])
            slots = tuple(int(u.get("number_of_slots") or 0)
                          for u in data.get("slot_upgrades") or [])
            def codes(key):
                return tuple(str(u.get("upgrade_requirement_code") or "")
                             for u in data.get(key) or [])
            barred = tuple(q for req in data.get("requirements") or []
                           if req.get("type") == "not_have_quirks"
                           for q in (req.get("data") or {})
                           .get("quirk_library_names") or [])
            if gold and heal:
                out.append(StressTreatment(
                    barred_quirks=barred,
                    activity=str(activity.get("id") or ""),
                    gold=gold, heal=heal, slots=slots, building=building,
                    gold_codes=codes("cost_upgrades"),
                    heal_codes=codes("stress_upgrades"),
                    slot_codes=codes("slot_upgrades")))
    return tuple(out)


@dataclass(frozen=True)
class Ladder:
    """One upgradeable value of a building: a step per rank, each step
    behind a rank `code` of `tree` ("" for the base step)."""
    values: tuple
    codes: tuple[str, ...]
    tree: str

    def at(self, owned: dict | None = None):
        """The value at the highest step whose rank is owned. `owned` maps a
        tree id to the rank codes bought, as `choose_treatments` takes it."""
        have = set((owned or {}).get(self.tree, ()))
        return self.values[StressTreatment._step(self.codes, have)]


@dataclass(frozen=True)
class Sanitarium:
    """`sanitarium.building.json`, both activities. Each price, slot count
    and chance is its own ladder, on its own tree, exactly as shipped:
    quirk prices on `sanitarium.cost`, slots on `sanitarium.slots`, disease
    prices AND the cure-all chance on `sanitarium.disease_quirk_cost`."""
    quests_required: int
    lock_positive: Ladder
    remove_negative: Ladder
    remove_locked_negative: Ladder
    quirk_slots: Ladder
    cure_disease: Ladder
    cure_all_chance: Ladder
    disease_slots: Ladder


def _ladder(rows, key) -> Ladder:
    values, codes, trees = [], [], set()
    for row in rows or ():
        if "cost_currency" in row:
            values.append(int((row.get("cost_currency") or {}).get("amount") or 0))
        else:
            values.append(row.get(key))
        codes.append(str(row.get("upgrade_requirement_code") or ""))
        if row.get("upgrade_tree_id"):
            trees.add(str(row["upgrade_tree_id"]))
    return Ladder(values=tuple(values), codes=tuple(codes),
                  tree=trees.pop() if len(trees) == 1 else "")


@lru_cache(maxsize=4)
def load_sanitarium(game_dir: str) -> Optional[Sanitarium]:
    """The Sanitarium's prices, slots and chances, or None when unreadable."""
    path = (Path(game_dir) / "campaign" / "town" / "buildings" / "sanitarium"
            / "sanitarium.building.json")
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            raw = json.load(fh)
        acts = {a["id"]: a.get("data") or {} for a in raw["data"]["activities"]}
        quirk, disease = acts["treatment"], acts["disease_treatment"]
        return Sanitarium(
            quests_required=int((raw.get("requirements") or {})
                                .get("number_of_quests_finished") or 0),
            lock_positive=_ladder(quirk["positive_quirk_cost_upgrades"], ""),
            remove_negative=_ladder(quirk["negative_quirk_cost_upgrades"], ""),
            remove_locked_negative=_ladder(quirk["permanent_negative_quirk_cost_upgrades"], ""),
            quirk_slots=_ladder(quirk["slot_upgrades"], "number_of_slots"),
            cure_disease=_ladder(disease["disease_quirk_cost_upgrades"], ""),
            cure_all_chance=_ladder(disease["disease_quirk_cure_all_chance_upgrades"], "chance"),
            disease_slots=_ladder(disease["slot_upgrades"], "number_of_slots"))
    except (OSError, ValueError, KeyError, TypeError):
        return None


_VALID_RE = re.compile(r"activity_modifier:.*?\.override_valid_activity_ids\s+"
                       r"([a-z_ ]+?)(?:\s+\.|$)", re.M)


#: Every building directory under `campaign/town/buildings/`.
BUILDINGS_DIR = ("campaign", "town", "buildings")


@dataclass(frozen=True)
class BuildingGate:
    """When a building opens: its `.building.json` `requirements` block --
    quests finished, and the highest dungeon level reached."""
    quests: int = 0
    dungeon_level: int = 0


@lru_cache(maxsize=4)
def load_building_gates(game_dir: str) -> dict:
    """`{building: BuildingGate}` for every shipped building. The vanilla
    files open the Stage Coach, Nomad Wagon, Graveyard and Statue from the
    start, the Abbey and Tavern after 2 quests, the Guild and the Blacksmith
    after 3, the Sanitarium after 4, and the Survivalist at a dungeon level
    of 2 -- so the post-tutorial visit (1 quest) can buy at the coach and
    the wagon and nowhere else."""
    gates = {}
    for f in sorted(Path(game_dir).joinpath(*BUILDINGS_DIR).glob("*/*.building.json")):
        try:
            with open(f, encoding="utf-8", errors="replace") as fh:
                req = json.load(fh).get("requirements") or {}
        except (OSError, ValueError):
            continue
        gates[f.parent.name] = BuildingGate(
            quests=int(req.get("number_of_quests_finished") or 0),
            dungeon_level=int(req.get("highest_dungeon_level") or 0))
    return gates


def closed_buildings(game_dir: str, quests_finished: Optional[int],
                     highest_dungeon_level: Optional[int] = None) -> frozenset:
    """The buildings not yet open. A count that was not read (None) is taken
    as met, the direction that never blocks a campaign the save does not
    describe."""
    return frozenset(
        b for b, g in load_building_gates(str(game_dir)).items()
        if (quests_finished is not None and quests_finished < g.quests)
        or (highest_dungeon_level is not None and highest_dungeon_level < g.dungeon_level))


@lru_cache(maxsize=128)
def class_valid_activities(game_dir: str, hero_class: str) -> Optional[frozenset]:
    """The only town activities this class may use, or None for "any".

    Measured live 2026-09-27: a Flagellant dragged onto the Abbey's prayer and
    meditation rows was refused twice. His info file says why --
    `activity_modifier: .override_valid_activity_ids flagellation treatment
    disease_treatment` -- the Penance Hall and the Sanitarium, nothing else."""
    from data.hero_value import _info_path             # noqa: PLC0415
    path = _info_path(str(game_dir), hero_class)
    if path is None:
        return None
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    m = _VALID_RE.search(text)
    return frozenset(m.group(1).split()) if m else None


@dataclass(frozen=True)
class HeroRank:
    """One purchasable rank of a hero's own tree (weapon, armour or a skill)."""
    tree: str                      # "weapon", "armour" or the skill id
    index: int                     # 0-based rank code in the tree
    gold: int
    resolve: int                   # prerequisite_resolve_level
    needs: tuple = ()              # ((tree_id, code), ...) outside the hero


@lru_cache(maxsize=64)
def load_hero_ranks(game_dir: str, hero_class: str) -> dict:
    """`{tree: (HeroRank, ...)}` from `<class>.upgrades.json`, in rank order.

    The requirements are what make "max out at their level" derivable: each
    rank names its previous rank, the BUILDING rank it needs (e.g.
    `blacksmith.weapon` b) and a `prerequisite_resolve_level`."""
    path = hero_upgrades_path(str(game_dir), hero_class)
    if path is None:
        return {}
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return {}
    out: dict = {}
    prefix = f"{hero_class}."
    for tree in raw.get("trees", []) or []:
        tid = str(tree.get("id") or "")
        if not tid.startswith(prefix):
            continue
        name = tid[len(prefix):]
        ranks = []
        for i, req in enumerate(tree.get("requirements", []) or []):
            gold = sum(int(c.get("amount") or 0)
                       for c in req.get("currency_cost") or []
                       if c.get("type") == "gold")
            needs = tuple((str(r.get("tree_id")), str(r.get("requirement_code")))
                          for r in req.get("prerequisite_requirements") or []
                          if not str(r.get("tree_id", "")).startswith(prefix))
            ranks.append(HeroRank(tree=name, index=i, gold=gold,
                                  resolve=int(req.get("prerequisite_resolve_level") or 0),
                                  needs=needs))
        out[name] = tuple(ranks)
    return out


def gold_per_stress(game_dir: str | Path, tier: int = 0) -> float:
    """The cheapest Abbey rate at a given building tier.

    Cheapest because a player picks the best available slot; `tier` defaults to
    **0**, the un-upgraded Abbey, which is the most expensive rate and therefore
    the most conservative input to a retreat decision — it makes stress look
    dear, which argues for pulling out. Pass the real tier to sharpen it.
    """
    rates = [t.gold_per_stress(tier) for t in load_stress_treatments(str(game_dir))]
    rates = [r for r in rates if r > 0]
    return min(rates) if rates else 0.0


@lru_cache(maxsize=64)
def hero_upgrades_path(game_dir: str, hero_class: str) -> Optional[Path]:
    """That class's upgrade tree file, DLC classes included.

    `upgrades/heroes/` holds only the **fifteen vanilla classes**. The other
    five — Musketeer, Flagellant, Shieldbreaker, Duelist, Runaway — ship their
    own copy under `dlc/**/upgrades/heroes/`, so reading the base path alone
    returns `None` for them and a Duelist silently reads as a hero with no
    skills, no ranks and nothing to buy.

    Every class also carries three `modes/{radiant,bloodmoon,new_game_plus}`
    variants. **Those are excluded**, which leaves exactly one file per class,
    twenty of twenty — the check that this glob is a rule and not a heuristic.
    The modes differ only in weapon and armour prices (Duelist weapon rank 2 is
    1500 radiant / 1750 normal / 2000 bloodmoon); **every skill rank is 1000 /
    250 / 750 / 1250 / 2500 in all four**, so the campaign mode cannot change
    what an unlock costs, which is the number `data/hero_skills.py` needs.
    """
    base = Path(game_dir) / "upgrades" / "heroes" / f"{hero_class}.upgrades.json"
    if base.is_file():
        return base
    dlc = Path(game_dir) / "dlc"
    for path in sorted(dlc.rglob(f"upgrades/heroes/{hero_class}.upgrades.json")):
        if "modes" not in path.parts:
            return path
    return None


@lru_cache(maxsize=64)
def load_hero_costs(game_dir: str, hero_class: str) -> Optional[HeroUpgradeCosts]:
    path = hero_upgrades_path(game_dir, hero_class)
    if path is None:
        return None
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            raw = json.load(fh)
    except (OSError, ValueError):
        return None
    weapon: tuple[int, ...] = ()
    armour: tuple[int, ...] = ()
    skills: dict[str, tuple[int, ...]] = {}
    for tree in raw.get("trees", []):
        tags = set(tree.get("tags") or [])
        costs = _costs(tree.get("requirements"))
        tree_id = str(tree.get("id") or "")
        if TAG_WEAPON in tags:
            weapon = costs
        elif TAG_ARMOUR in tags:
            armour = costs
        elif TAG_SKILL in tags:
            skills[tree_id.split(".", 1)[-1]] = costs
    return HeroUpgradeCosts(hero_class=hero_class, weapon=weapon, armour=armour,
                            skills=skills)


def hero_investment(game_dir: str | Path, hero_class: str, weapon_rank: int,
                    armour_rank: int, skill_ids: Sequence[str] = (),
                    skill_rank: Optional[int] = None) -> int:
    """Gold sunk into one hero, and therefore what dies with them.

    `skill_rank` defaults to the weapon rank, which is the engine's own
    convention (`CombatantState.skill_tier` comes from weapon rank) and matches
    how a player actually upgrades — the Guild and the Blacksmith are levelled
    together.
    """
    costs = load_hero_costs(str(game_dir), hero_class)
    if costs is None:
        return 0
    rank = weapon_rank if skill_rank is None else skill_rank
    return costs.investment(weapon_rank=weapon_rank, armour_rank=armour_rank,
                            skill_ranks={s: rank for s in skill_ids})


if __name__ == "__main__":
    from data.paths import GAME_DIR
    print("Abbey stress treatment (gold / stress healed, per building tier):")
    for treatment in load_stress_treatments(str(GAME_DIR)):
        rates = ", ".join(f"tier {i}: {treatment.gold_per_stress(i):.1f}"
                          for i in range(len(treatment.gold)))
        print(f"  {treatment.activity:14s} gold={treatment.gold} heal={treatment.heal}"
              f"  -> {rates}")
    print(f"\ncheapest gold per stress point, un-upgraded: "
          f"{gold_per_stress(GAME_DIR, 0):.1f}")
    print(f"cheapest gold per stress point, fully upgraded: "
          f"{gold_per_stress(GAME_DIR, 2):.1f}\n")
    for hero_class in ("crusader", "vestal", "highwayman"):
        costs = load_hero_costs(str(GAME_DIR), hero_class)
        if costs is None:
            continue
        print(f"{hero_class}: weapon={costs.weapon} armour={costs.armour} "
              f"skills={len(costs.skills)} trees")
        for rank in (0, 2, 4, 5):
            print(f"    at rank {rank}: "
                  f"{hero_investment(GAME_DIR, hero_class, rank, rank, list(costs.skills)[:4]):,} gold sunk")
