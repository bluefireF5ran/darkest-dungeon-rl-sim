"""Districts - the town buildings that buff a hero *in the raid*.

Added 2026-08-24 on Fran's ask. Districts are a Crimson Court town system, and
this project has always listed the town as out of scope - but a district's
payoff is not paid in town. Nine of the sixteen hand a hero an ordinary buff id
that `_apply_buff_to_stats` already knows how to cash, so the effect lands in
combat and the simulator was quietly fielding parties without it.

**Every buff id these files name already resolves in `store.buffs`.** Nothing
here parses a stat: the district files are a *binding* from a hero tag to buff
ids the buff loader already read, the same shape
`trinkets/base.entries.trinkets.json` has. That is why this loader is small.

Two readings that must not be regressed, both evidenced rather than declared:

**1. `hero_type_tags: []` means EVERY hero, not none.** This is the opposite of
the `hero_classes: []` rule on camping skills, where empty means no class - so
it cannot be inherited from there and needs its own evidence.
`conservatory_of_steel` settles it: that one building ships BOTH an empty-tag
entry (`hero_buff0`, three buffs) and a `conservatory_of_steel`-tagged entry
(`hero_buff1`, one buff). If empty meant "no hero", `hero_buff0` would be three
buffs of unreachable dead data sitting beside a working example of the
non-empty form. `granary` is the same shape: its `hp_heal_received_percent`
bonus for eating is tagged `[]`, and **no hero anywhere carries a `granary`
tag**, so the empty list is the only way it ever pays out.

**2. Every hero carries exactly one district tag** (plus `light`/`heavy` and
`religious`/`non-religious`, which no district reads). All 20 classes, DLC
included - so a district's hero buff reaches a predictable 1-5 classes and
`hero_type_tags` is a *class* gate wearing a tag's clothes.

**The Mill turns hunger off.** `the_mill` (Color of Madness) carries
`DistrictDisableBuffData: .disabled_type "hunger"`, so a full district loadout
deletes the hunger check outright. That is a real interaction with
`ExpeditionSequenceEnv`'s food model and not a rounding error, so it is loaded
and reported rather than quietly dropped - see `hunger_disabled`.

Deliberately not consumed here (recorded so the omission is a decision):
`DistrictPrestigeBuffData`, `DistrictEstateBuffData` (weekly gold),
`DistrictRosterStressReliefBuffData` (town visit), `DistrictRandomLootBuffData`,
`DistrictCurioInteractionBuffData` (curios are out of scope),
`DistrictStackingBuffData`, `DistrictReplacementInventoryEffectBuffData`, and
`DistrictLightBuffData` - the last is the Illuminators Guild replacing the
whole darkness range table, which IS a real combat effect and is wired
separately because it is a table swap rather than a buff grant.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DistrictHeroBuff:
    """One `DistrictHeroBuffData` entry: which heroes, and what they get.

    `tags` empty means every hero - see the module docstring for why.
    """
    tags: tuple[str, ...]
    buff_ids: tuple[str, ...]

    def applies_to(self, hero_tags) -> bool:
        return not self.tags or any(t in hero_tags for t in self.tags)


# Mash tiers 1-2 / 3-4 / 5 are Apprentice / Veteran / Champion (confirmed, see
# TIER_TO_HERO_LEVEL). A district is only in play once a real campaign could
# plausibly have built it -- Fran's call, 2026-08-24, and the reason is
# economic rather than mechanical: the gold and the blueprints do not exist
# early, so fielding a fully-districted Apprentice party is a scenario that
# never occurs.
MIN_TIER_VETERAN = 3
MIN_TIER_CHAMPION = 5

# Per-DLC availability. Color of Madness districts cost the most and arrive
# last, so they are Champion-only; the Crimson Court and Fires Edge sets are
# Veteran+. Keyed on the `dlc/<package>/` directory the file was read from, so
# a district cannot drift into the wrong bracket by being renamed.
SOURCE_MIN_TIER: dict[str, int] = {
    "735730_color_of_madness": MIN_TIER_CHAMPION,
}
DEFAULT_MIN_TIER = MIN_TIER_VETERAN


@dataclass(frozen=True)
class DistrictDefinition:
    name: str
    # The `dlc/<package>/` directory this district was read from, and the
    # lowest mash tier at which it is considered built. See SOURCE_MIN_TIER.
    source: str = ""
    min_tier: int = DEFAULT_MIN_TIER
    hero_buffs: tuple[DistrictHeroBuff, ...] = ()
    # DistrictCampingBuffData
    camping_points: int = 0
    camping_tags: tuple[str, ...] = ()
    # DistrictSupplyBuffData. `item_name` is "" for food, which is the empty-id
    # `provision` row in inventory/base.supply.inventory.items.darkest.
    supply_item_type: str = ""
    supply_item_name: str | None = None
    supply_min: int = 0
    supply_max: int = 0
    # DistrictDisableBuffData - "hunger" is the only shipped value.
    disables: tuple[str, ...] = ()
    # Every buff type present that this loader does not consume, kept so a test
    # can assert the ignore-list is a decision and not an oversight.
    unread_buff_types: tuple[str, ...] = ()


class DistrictLoader:
    """Reads every shipped `*districts*.json` outside `modes/`.

    `modes/{radiant,bloodmoon,new_game_plus}/` hold difficulty-mode overrides of
    the same buildings; the only `buff_list` that differs between the base file
    and the radiant one is `illuminators_guild`'s darkness table, which this
    loader does not consume anyway. Base wins, matching the DLC-merge rule used
    everywhere else in `data/`.
    """

    GLOB = "dlc/**/*districts*.json"
    SKIP_NAME_PARTS = ("town_events", "estate", "buffs")

    @staticmethod
    def _paths(game_dir: Path) -> list[Path]:
        out = []
        for p in sorted(game_dir.glob(DistrictLoader.GLOB)):
            if "modes" in {q.lower() for q in p.parts}:
                continue           # difficulty-mode overrides, not new content
            if any(s in p.name for s in DistrictLoader.SKIP_NAME_PARTS):
                continue
            out.append(p)
        return out

    @staticmethod
    def load_all(game_dir: str | Path) -> dict[str, DistrictDefinition]:
        game_dir = Path(game_dir)
        districts: dict[str, DistrictDefinition] = {}
        for path in DistrictLoader._paths(game_dir):
            try:
                raw = json.loads(path.read_text(encoding="utf-8-sig"))
            except (OSError, ValueError):
                continue
            if raw.get("enabled") is False:
                continue
            source = DistrictLoader._source(path)
            for b in raw.get("buildings", []):
                name = b.get("name")
                if not name or name in districts:
                    continue        # base wins on collision
                districts[name] = DistrictLoader._building(name, b, source)
        return districts

    @staticmethod
    def _source(path: Path) -> str:
        """The `dlc/<package>/` directory name, which is what gates the tier."""
        parts = list(path.parts)
        if "dlc" in parts:
            i = parts.index("dlc")
            if i + 1 < len(parts):
                return parts[i + 1]
        return ""

    @staticmethod
    def _building(name: str, b: dict, source: str = "") -> DistrictDefinition:
        hero_buffs: list[DistrictHeroBuff] = []
        camping_points, camping_tags = 0, ()
        s_type, s_name, s_min, s_max = "", None, 0, 0
        disables: list[str] = []
        unread: list[str] = []
        for entry in b.get("buff_list", []):
            kind = entry.get("type")
            if kind == "DistrictHeroBuffData":
                ids = tuple(entry.get("buff_list", []))
                if ids:
                    hero_buffs.append(DistrictHeroBuff(
                        tuple(entry.get("hero_type_tags", [])), ids))
            elif kind == "DistrictCampingBuffData":
                camping_points = int(entry.get("camping_points", 0))
                camping_tags = tuple(entry.get("hero_type_tags", []))
            elif kind == "DistrictSupplyBuffData":
                s_type = entry.get("item_type", "")
                s_name = entry.get("item_name")
                s_min = int(entry.get("range_min", 0))
                s_max = int(entry.get("range_max", 0))
            elif kind == "DistrictDisableBuffData":
                d = entry.get("disabled_type")
                if d:
                    disables.append(d)
            elif kind:
                unread.append(kind)
        return DistrictDefinition(
            name=name, source=source,
            min_tier=SOURCE_MIN_TIER.get(source, DEFAULT_MIN_TIER),
            hero_buffs=tuple(hero_buffs),
            camping_points=camping_points, camping_tags=camping_tags,
            supply_item_type=s_type, supply_item_name=s_name,
            supply_min=s_min, supply_max=s_max,
            disables=tuple(disables),
            unread_buff_types=tuple(sorted(set(unread))))


# -- What a party actually gets, given a set of built districts --

def districts_at_tier(districts, tier: int) -> set[str]:
    """Which districts a party at this mash tier is treated as having built.

    Below Veteran nothing is built at all, so an Apprentice expedition is
    exactly what it was before districts existed -- which also makes tiers 1-2
    the control group when reading a retrain, the same role the short band
    plays for camping.
    """
    return {n for n, d in districts.items() if tier >= d.min_tier}

def hero_district_buff_ids(districts, hero_tags, built=None) -> list[str]:
    """Buff ids one hero receives from the built districts, in district order.

    `built` None means the FULL loadout - Fran's simplification for now.
    """
    tags = set(hero_tags or ())
    out: list[str] = []
    for name in sorted(districts):
        if built is not None and name not in built:
            continue
        for hb in districts[name].hero_buffs:
            if hb.applies_to(tags):
                out.extend(hb.buff_ids)
    return out


def camping_point_bonus(districts, hero_tags, built=None) -> int:
    """Extra camping points. Outsiders Bonfire, +2, tagged."""
    tags = set(hero_tags or ())
    total = 0
    for name in sorted(districts):
        if built is not None and name not in built:
            continue
        d = districts[name]
        if d.camping_points and (not d.camping_tags
                                 or any(t in tags for t in d.camping_tags)):
            total += d.camping_points
    return total


def party_camping_point_bonus(districts, party_tags, built=None) -> int:
    """Extra camping points for the PARTY, counted once per district.

    DECLARED READING, and the smaller of the two. Camping points are a party
    budget, but `DistrictCampingBuffData` carries `hero_type_tags` - so the
    shipped data is equally consistent with "+2 once if the party contains an
    Outsider" and with "+2 for each Outsider in the party", which on a party of
    four Outsiders would be +8 against a base budget of 12. Nothing in the file
    disambiguates, so this takes the conservative one: **once, if any hero
    qualifies.**

    Falsifier: camp in a real Champion raid with the Outsiders Bonfire built,
    once with a single Outsider in the party and once with two, and read the
    camping-point total off the camp screen. Two different totals means the
    per-hero reading is right and this is undercounting.
    """
    tag_union = set()
    for tags in party_tags:
        tag_union.update(tags or ())
    total = 0
    for name in sorted(districts):
        if built is not None and name not in built:
            continue
        d = districts[name]
        if d.camping_points and (not d.camping_tags
                                 or any(t in tag_union for t in d.camping_tags)):
            total += d.camping_points
    return total


def food_bonus(districts, built=None) -> int:
    """Extra food from the Granary.

    The shipped range is 4-10 and the game rolls it, but the roll is rerollable
    by leaving and re-entering the provision screen - so a player who wants the
    maximum simply takes it. **Fran's call, 2026-08-24: always the maximum.**
    Modelling the roll would add variance no real player is exposed to.
    """
    total = 0
    for name in sorted(districts):
        if built is not None and name not in built:
            continue
        d = districts[name]
        if d.supply_item_type == "provision" and not d.supply_item_name:
            total += d.supply_max
    return total


def hunger_disabled(districts, built=None) -> bool:
    """The Mill (Color of Madness) switches the hunger check off entirely."""
    for name in sorted(districts):
        if built is not None and name not in built:
            continue
        if "hunger" in districts[name].disables:
            return True
    return False
