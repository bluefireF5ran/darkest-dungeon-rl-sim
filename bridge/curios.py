"""Curio policy — PLAYBOOK 2.4, driven by the game's own tables.

Which curios to touch, with which provision, and which hero should do it. Reads
`bridge/dungeon_map.py`'s decoded tiles and answers per curio.

**The rules are the expert's; the judgements are the game's.** PLAYBOOK 2.4 says
"books are net negative, skip them" and "interact with anything whose provision
gives a reward" — but *which* curios those are is not hard-coded here. Every
outcome is scored from `curios/curio_type_library.csv`, resolving each concrete
result against the real effect and quirk definitions. That way the expert rule is
a **prediction this module can be wrong about**, not an assumption baked in.
`tests/bridge/test_curios.py` checks the prediction: the books do come out negative.

Three things the tables settle that a plain reading of the rule would get wrong:

- **`Loot: JOURNALONLY` is not loot.** It is a journal page — lore, no resources.
  `stack_of_books` has *only* that, which is most of why books are a trap.
- **A matching provision is not automatically good.** Four of the 46 item
  interactions hurt you: `stack_of_books` and `bas_relief` answer their item
  with `Stress 5`, `decorative_urn` answers a shovel with the *negative* quirk
  `guilty_conscience`, and the Shambler's altar answers a torch with a boss.
  "Interact with its provision" has to mean *when the result is a reward*, and
  only the tables know which those are — the result **type** does not settle it,
  since `confession_booth` also answers holy water with an `Effect` and that one
  is a stress heal.
- **`Quirk` has a polarity and it is per-result.** `stack_of_books` rolls
  positive:2 / negative:1; `pile_of_bones` gives `bloodthirsty`, which
  `quirk_library.json` marks negative — and which is itself a curio compulsion.

**Skipping a curio is advice, not a guarantee.** Every quirk carrying a
`curio_tag` in `quirk_library.json` is `is_positive: False` — 20 compulsions,
0.2 to 0.6 chance — so a hero can interact without being asked. The advice names
them rather than pretending the choice is always the player's.

Deliberately **not** covered, because the data does not support it yet: trap
disarm (PLAYBOOK 2.4's last row). Disarm chance is the `resistance`/`trap` stat,
built from class base plus quirks plus trinkets, and neither `CombatantState` nor
`live_state.py` carries it — inventing a ranking would be a convention wearing
game-data clothes.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data.curio_loader import CurioDefinition, ItemInteraction, Result, load_curios, load_prop_types
from data.effect_loader import EffectLoader

from data.paths import GAME_DIR

# Loot codes that are not resources. Everything else in that column is a real
# loot table letter.
LOOT_NOTHING = {"JOURNALONLY"}

# Outcome types whose polarity never depends on the concrete result.
ALWAYS_GOOD = {"Purge", "Scouting"}
ALWAYS_BAD = {"Disease", "Teleport", "Summon"}

# PLAYBOOK 2.4: interact with the altar only when you choose to, and reorder the
# party first — the shuffle is forced, so any starting order can only improve.
SUMMON_CURIOS = {"shamblers_altar"}

# The real supply items, from `inventory/base.supply.inventory.items.darkest`.
# The curio sheet names one interaction item that is NOT among them —
# `sacrificial_stone` takes `"provision"`, which no inventory file defines — so
# an unrecognised item id is reported as unusable rather than as something the
# party forgot to buy.
SUPPLY_ITEMS = frozenset({
    "antivenom", "bandage", "dog_treats", "firewood", "holy_water",
    "laudanum", "medicinal_herbs", "shovel", "skeleton_key", "torch",
})


@dataclass(frozen=True)
class CurioHero:
    """The party facts the policy needs. `max_hp` is optional because a save
    does not carry it — without it, HP only breaks ties by raw value."""
    id: str
    hero_class: str
    stress: float = 0.0
    hp: float = 0.0
    max_hp: Optional[float] = None
    quirks: tuple[str, ...] = ()

    @property
    def hp_fraction(self) -> float:
        return (self.hp / self.max_hp) if self.max_hp else self.hp

    @classmethod
    def from_live_hero(cls, hero, max_hp: Optional[float] = None) -> "CurioHero":
        return cls(
            id=str(getattr(hero, "guid", hero.hero_class)),
            hero_class=hero.hero_class,
            stress=float(getattr(hero, "stress", 0.0) or 0.0),
            hp=float(getattr(hero, "hp", 0.0) or 0.0),
            max_hp=max_hp,
            quirks=tuple(getattr(hero, "quirks", ()) or ()),
        )


@dataclass(frozen=True)
class Compulsion:
    hero: str
    quirk: str
    chance: float


@dataclass(frozen=True)
class CurioAdvice:
    curio_id: str
    area_id: str
    action: str                  # "use" | "interact" | "skip" | "optional"
    item: Optional[str]
    hero: Optional[str]
    criterion: str               # how the hero was (or should be) picked
    why: str
    value: float                 # expected polarity of the recommended action
    compulsions: tuple[Compulsion, ...] = ()

    def line(self) -> str:
        head = {
            "use": f"{self.item} -> {self.curio_id}",
            "interact": f"interact with {self.curio_id}",
            "skip": f"SKIP {self.curio_id}",
            "optional": f"{self.curio_id}: your call",
        }[self.action]
        who = f"  [{self.hero or self.criterion}]" if (self.hero or self.criterion) else ""
        risk = ""
        if self.compulsions:
            risk = "  !! " + ", ".join(
                f"{c.hero} is {c.quirk} ({c.chance:.0%})" for c in self.compulsions)
        return f"{self.area_id:6s} {head}{who}  - {self.why}{risk}"


# --- game data, loaded once ---

@lru_cache(maxsize=1)
def _curios() -> dict[str, CurioDefinition]:
    return load_curios(GAME_DIR)


@lru_cache(maxsize=1)
def _prop_types() -> dict[str, str]:
    return load_prop_types(GAME_DIR)


@lru_cache(maxsize=1)
def _effects():
    return EffectLoader.load(GAME_DIR / "effects" / "base.effects.darkest")


@lru_cache(maxsize=1)
def _quirks() -> dict[str, dict]:
    path = GAME_DIR / "shared" / "quirk" / "quirk_library.json"
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return {q["id"]: q for q in json.load(fh).get("quirks", []) if q.get("id")}
    except (OSError, ValueError, KeyError):
        return {}


def definition_for(curio_prop: str) -> Optional[CurioDefinition]:
    """Look up by the name the MAP stores, translating prop -> type."""
    return _curios().get(_prop_types().get(curio_prop, curio_prop))


# --- polarity, resolved against the real definitions ---

def _effect_value(name: str) -> float:
    """Judge an effect by what it actually does, not by its label.

    Returns 0.0 for an effect that is not in the effects file — an honest "I
    don't know", never a guess.
    """
    eff = _effects().get(name)
    if eff is None:
        return 0.0
    good = bool(eff.stress_heal or eff.heal_min or eff.heal_percent
                or eff.buff_ids or eff.is_stat_buff or eff.cures_dots
                or eff.cures_bleed or eff.cures_blight)
    bad = bool(eff.stress_damage or eff.bleed_amount or eff.blight_amount
               or eff.stress_damage_per_round)
    if good and not bad:
        return 1.0
    if bad and not good:
        return -1.0
    return 0.0


def _quirk_value(name: str) -> float:
    if name.lower() == "positive":
        return 1.0
    if name.lower() == "negative":
        return -1.0
    quirk = _quirks().get(name)
    if quirk is None:
        return 0.0
    return 1.0 if quirk.get("is_positive") else -1.0


def result_value(result_type: str, result: Result) -> float:
    """+1 good, -1 bad, 0 neutral or unknown."""
    if result_type in ALWAYS_GOOD:
        return 1.0
    if result_type in ALWAYS_BAD:
        return -1.0
    if result_type == "Nothing":
        return 0.0
    if result_type == "Loot":
        return 0.0 if result.name in LOOT_NOTHING else 1.0
    if result_type == "Quirk":
        return _quirk_value(result.name)
    if result_type == "Effect":
        return _effect_value(result.name)
    return 0.0


def _results_value(result_type: str, results: Sequence[Result]) -> float:
    """Weighted mean over the concrete results of one outcome type.

    An outcome with no listed results still counts as its type (`Purge` with no
    row detail is still a purge), which is why the empty case falls back to
    scoring the type alone.
    """
    if not results:
        return result_value(result_type, Result(name=""))
    total = sum(r.weight for r in results) or float(len(results))
    return sum(result_value(result_type, r) * r.weight for r in results) / total


def bare_hand_value(curio: CurioDefinition) -> float:
    """Expected polarity of touching it with no provision, in [-1, 1]."""
    total = curio.total_weight
    if not total:
        return 0.0
    return sum(o.weight * _results_value(kind, o.results)
               for kind, o in curio.outcomes.items()) / total


def item_value(interaction: ItemInteraction) -> float:
    return _results_value(interaction.result_type, interaction.results)


# --- who should do it ---

@lru_cache(maxsize=4)
def extra_curio_loot_classes(game_dir: str = str(GAME_DIR)) -> frozenset:
    """Classes the game itself says get MORE out of a curio.

    Fran, 2026-09-02: *"for curio we should choose antiquarian if available"*.
    That is right and the game states it rather than leaving it to lore --
    `heroes/antiquarian/antiquarian.info.darkest` carries
    `extra_curio_loot: .code "ANTIQ" .count 1`, beside an `extra_battle_loot`
    with the same code.

    **Derived rather than a class name**, for the same reason
    `unrestricted_heroes` and `skill_slots` are: a modded class shipping the
    same key is handled with no entry anywhere, and if a DLC ever grants it to
    a second class the rule follows the data instead of needing an edit.
    """
    from data.darkest_parser import DarkestParser

    root = Path(game_dir)
    paths = list(root.glob("heroes/*/*.info.darkest"))
    paths += [p for p in root.glob("dlc/**/heroes/*/*.info.darkest")
              if "modes" not in p.parts]
    out = set()
    for path in paths:
        try:
            records = DarkestParser.parse_file(path)
        except (OSError, ValueError):
            continue
        if any(r.get("_type") == "extra_curio_loot" for r in records):
            out.add(path.stem.split(".")[0])
    return frozenset(out)


def _extra_loot_hero(party: Sequence[CurioHero],
                     game_dir: str = str(GAME_DIR)) -> Optional[CurioHero]:
    """The party's curio specialist, if it has one. Lowest stress among them,
    because opening a curio can cost stress and this is not a stress rule."""
    special = extra_curio_loot_classes(game_dir)
    have = [h for h in party if h.hero_class in special]
    if not have:
        return None
    return min(have, key=lambda h: (h.stress, -h.hp_fraction))


def _worst_quirk_hero(party: Sequence[CurioHero]) -> Optional[CurioHero]:
    """Most negative quirks, since a purge draws from them (PLAYBOOK 2.4)."""
    def negatives(hero: CurioHero) -> int:
        return sum(1 for q in hero.quirks if _quirk_value(q) < 0)
    ranked = sorted(party, key=negatives, reverse=True)
    return ranked[0] if ranked and negatives(ranked[0]) else None


def _most_stressed_hero(party: Sequence[CurioHero]) -> Optional[CurioHero]:
    """Most stress, tie-broken by lowest HP — PLAYBOOK 2.4's stress-relief rule."""
    if not party:
        return None
    return max(party, key=lambda h: (h.stress, -h.hp_fraction))


def _relieves_stress(result_type: str, results: Sequence[Result]) -> bool:
    if result_type != "Effect":
        return False
    return any((_effects().get(r.name) is not None and _effects()[r.name].stress_heal)
               for r in results)


def _compulsions(curio: CurioDefinition, party: Sequence[CurioHero]) -> tuple[Compulsion, ...]:
    """Heroes who may interact whether or not you advised it.

    A quirk's `curio_tag` matches when the curio carries that tag, and the tag
    `All` matches anything.
    """
    tags = {t for t in curio.tags}
    out = []
    for hero in party:
        for quirk_id in hero.quirks:
            quirk = _quirks().get(quirk_id)
            tag = quirk.get("curio_tag") if quirk else None
            if not tag:
                continue
            if tag == "All" or tag in tags:
                out.append(Compulsion(hero=hero.hero_class, quirk=quirk_id,
                                      chance=float(quirk.get("curio_tag_chance") or 0.0)))
    return tuple(sorted(out, key=lambda c: -c.chance))


# --- the policy ---

def advise(curio_prop: str, area_id: str = "",
           inventory: Optional[dict[str, int]] = None,
           party: Sequence[CurioHero] = ()) -> CurioAdvice:
    """What to do with one curio, given what the party is carrying."""
    inventory = inventory or {}
    curio = definition_for(curio_prop)
    if curio is None:
        return CurioAdvice(curio_id=curio_prop, area_id=area_id, action="skip",
                           item=None, hero=None, criterion="",
                           why="not in the curio library", value=0.0)

    compulsions = _compulsions(curio, party)

    # PLAYBOOK 2.4: the altar is never a routine yes.
    if curio.id in SUMMON_CURIOS:
        return CurioAdvice(
            curio_id=curio.id, area_id=area_id, action="optional", item="torch",
            hero=None, criterion="reorder to your WORST formation first",
            why="summons a roaming boss; the shuffle is forced, so any order can only improve",
            value=-1.0, compulsions=compulsions)

    # Best provision we actually carry whose result is a reward.
    best: Optional[tuple[float, ItemInteraction]] = None
    for item, interaction in curio.items.items():
        if item not in SUPPLY_ITEMS or inventory.get(item, 0) <= 0:
            continue
        value = item_value(interaction)
        if value > 0 and (best is None or value > best[0]):
            best = (value, interaction)

    if best is not None:
        value, interaction = best
        hero, criterion = _pick_hero(interaction.result_type, interaction.results, party)
        return CurioAdvice(
            curio_id=curio.id, area_id=area_id, action="use", item=interaction.item,
            hero=hero.hero_class if hero else None, criterion=criterion,
            why=interaction.text or f"{interaction.item} -> {interaction.result_type}",
            value=value, compulsions=compulsions)

    bare = bare_hand_value(curio)
    if bare > 0:
        kind, outcome = max(curio.outcomes.items(),
                            key=lambda kv: kv[1].weight * max(0.0, _results_value(kv[0], kv[1].results)))
        hero, criterion = _pick_hero(kind, outcome.results, party)
        return CurioAdvice(
            curio_id=curio.id, area_id=area_id, action="interact", item=None,
            hero=hero.hero_class if hero else None, criterion=criterion,
            why=f"{curio.chance(kind):.0%} {kind} and net positive bare-handed",
            value=bare, compulsions=compulsions)

    missing = [i for i in curio.items if i in SUPPLY_ITEMS and inventory.get(i, 0) <= 0]
    unusable = [i for i in curio.items if i not in SUPPLY_ITEMS]
    why = "net negative bare-handed"
    if missing:
        why += f"; needs {'/'.join(missing)}"
    elif unusable:
        why += f"; its only interaction names '{'/'.join(unusable)}', which no inventory file defines"
    return CurioAdvice(curio_id=curio.id, area_id=area_id, action="skip", item=None,
                       hero=None, criterion="", why=why, value=bare,
                       compulsions=compulsions)


def _pick_hero(result_type: str, results: Sequence[Result],
               party: Sequence[CurioHero]) -> tuple[Optional[CurioHero], str]:
    """PLAYBOOK 2.4's hero rules, and silence where it has none.

    The damage-buff rule ("the hero you expect to hit hardest") is stated as a
    criterion rather than resolved: a save carries no damage number, and the
    bridge does not build combatants.
    """
    if result_type == "Purge":
        hero = _worst_quirk_hero(party)
        return hero, "worst negative quirk"
    if _relieves_stress(result_type, results):
        return _most_stressed_hero(party), "most stressed, then lowest HP"
    # **The largest outcome family had no rule at all.** `Loot` is 41 of the
    # curio library's outcomes and 27 of its item interactions, and every one
    # of them fell through to "no opinion" -- so the one hero the game gives a
    # documented curio bonus to was never named. See
    # `extra_curio_loot_classes`.
    if result_type == "Loot":
        hero = _extra_loot_hero(party)
        if hero is not None:
            return hero, "extra curio loot"
        return None, ""
    if result_type == "Effect" and any(
            (_effects().get(r.name) is not None
             and (_effects()[r.name].is_stat_buff or _effects()[r.name].buff_ids))
            for r in results):
        return None, "your highest expected damage"
    return None, ""


def advise_map(dungeon_map, inventory: Optional[dict[str, int]] = None,
               party: Sequence[CurioHero] = ()) -> list[CurioAdvice]:
    """Advice for every curio still standing in a decoded dungeon map."""
    return [advise(tile.curio_id, area_id, inventory, party)
            for area_id, tile in dungeon_map.curios() if tile.curio_id]


def main() -> None:
    import argparse
    from bridge.dungeon_map import read_dungeon_map

    ap = argparse.ArgumentParser(description="what to do with the curios in a dungeon")
    ap.add_argument("map_file", help="a persist.map.json (a COPY, never the live one)")
    ap.add_argument("--have", default="", metavar="ITEMS",
                    help="provisions carried, e.g. shovel=3,holy_water=2,skeleton_key=1")
    args = ap.parse_args()

    inventory: dict[str, int] = {}
    for part in args.have.split(","):
        if "=" in part:
            name, _, count = part.partition("=")
            inventory[name.strip()] = int(count)

    dm = read_dungeon_map(args.map_file)
    if dm is None:
        print("no dungeon map in that file (in town?)")
        return
    for advice in advise_map(dm, inventory):
        print(advice.line())


if __name__ == "__main__":
    main()
