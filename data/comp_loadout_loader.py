from __future__ import annotations
import difflib
import json
import re
from pathlib import Path

from .csv_loader import csv_root
from .types import HeroLoadout, CompLoadoutEntry, DataStore


# Real class display names, as they appear in COMPS/curated_comps/*.json
# ("heroClass") -> this project's internal hero id. Fixed, small, and
# hand-verified against every distinct heroClass string across all 493
# comp JSON files — not derived from HeroDefinition.name (which is just
# subdir.title(), e.g. "Man At Arms", not the real "Man at Arms").
HERO_DISPLAY_NAME_TO_ID = {
    "Abomination": "abomination",
    "Antiquarian": "antiquarian",
    "Arbalest": "arbalest",
    "Bounty Hunter": "bounty_hunter",
    "Crusader": "crusader",
    "Duelist": "duelist",
    "Flagellant": "flagellant",
    "Grave Robber": "grave_robber",
    "Hellion": "hellion",
    "Highwayman": "highwayman",
    "Houndmaster": "houndmaster",
    "Jester": "jester",
    "Leper": "leper",
    "Man at Arms": "man_at_arms",
    "Musketeer": "musketeer",
    "Occultist": "occultist",
    "Plague Doctor": "plague_doctor",
    "Runaway": "runaway",
    "Shieldbreaker": "shieldbreaker",
    "Vestal": "vestal",
}

# How close a comp's spelling has to be to the game's before it is accepted as
# the same skill, and how far clear of the runner-up.
#
# The comps are written by hand and two names are simply spelled differently
# from the game's own string table: "Judgment" for the Vestal's `judgement`
# (15 hero slots) and "Poison Dart" for the Grave Robber's `poison_dart`,
# which the game calls "Poison Darts" (8 slots). Those 23 slots silently
# trained with THREE skills instead of four.
#
# An exact match is always tried first, so this fallback only ever sees a name
# that failed. It is also scoped to ONE hero's ~7 skills, and refuses whenever
# two of them are close enough to argue about -- ambiguity is left unresolved
# rather than guessed. Across all 136 curated comps it fires exactly twice, on
# exactly those two names, which a test pins.
_SKILL_NAME_FUZZY_CUTOFF = 0.9
_SKILL_NAME_FUZZY_MARGIN = 0.05


def _resolve_skill_name(display_name: str, name_to_id: dict[str, str]) -> str | None:
    """A skill id for this display name, exactly or unambiguously nearly."""
    exact = name_to_id.get(display_name)
    if exact is not None:
        return exact
    ranked = sorted(
        ((difflib.SequenceMatcher(None, display_name.casefold(), known.casefold()).ratio(), known)
         for known in name_to_id),
        reverse=True)
    if not ranked or ranked[0][0] < _SKILL_NAME_FUZZY_CUTOFF:
        return None
    if len(ranked) > 1 and ranked[0][0] - ranked[1][0] < _SKILL_NAME_FUZZY_MARGIN:
        return None
    return name_to_id[ranked[0][1]]


# DOTALL because a CDATA block may span lines. Four skills across two
# Workshop classes were invisible without it -- the entry simply never
# matched, so they read as "this mod has no English name for that skill".
_ENTRY_RE = re.compile(r'<entry id="([^"]+)"><!\[CDATA\[(.*?)\]\]></entry>',
                       re.DOTALL)
# The game's own inline markup, which is decoration rather than name.
_COLOUR_RE = re.compile(r'\{colour_start\|[^}]*\}|\{colour_end\}', re.I)


def _display_name(raw: str) -> str:
    """The name inside a CDATA block that may carry a decoration line.

    A multi-line entry puts a colour-tagged row of `¤` icon placeholders
    first and the actual name after it, so the LAST non-empty line is the
    name. For an ordinary single-line entry that is the only line, which is
    why this can be applied everywhere without changing what already works.
    """
    cleaned = _COLOUR_RE.sub("", raw).replace("\u00a4", " ")
    lines = [ln.strip() for ln in cleaned.splitlines() if ln.strip()]
    return lines[-1] if lines else cleaned.strip()
_ENGLISH_SECTION_RE = re.compile(r'<language id="english">(.*?)</language>', re.DOTALL)


class CompLoadoutLoader:
    """Loads COMPS/curated_comps/*.json — per-comp, per-hero real
    loadouts (4 active skills, camp skills, 2 trinkets, quirks), a data
    source distinct from COMPS/<hash>.csv (which only has hero class +
    zone). This is the only source in the project that records which 4 of
    a hero's ~7 skills a given comp actually equips: the .info.darkest
    files list the full movepool and nothing else narrows it down (that
    choice lives in a player's save file in the real game, not any base
    install data).

    Skill/camp-skill/trinket/quirk display names ("Hands from the Abyss",
    "Demon's Cauldron", "Tough") are resolved to internal ids
    ("hands_from_abyss", "demons_cauldron", "tough") via the game's own
    English localization string tables rather than guessed from the name
    — several skills were renamed after their internal id was set (e.g.
    Occultist's `bloodlet` displays as "Sacrificial Stab"), so a
    name-derived guess would silently mismatch. Deliberately does NOT use
    store.trinkets[id].name / store.quirks[id].name for this — both are
    set to the id itself, not a real display name (BuffLoader.load_trinkets/
    load_quirks never populated a real name field; same shape of bug as
    the pre-existing SkillDefinition.name=sk_id, out of scope to fix here
    since nothing before this loader depended on either being a real name).
    Key patterns (all under localization/*.string_table.xml, base +
    dlc/**/localization/, English section only):
    - `combat_skill_name_<hero_id>_<skill_id>`
    - `camping_skill_name_<skill_id>` (values are ALL CAPS, matched
      case-insensitively against the JSON's title-case names)
    - `str_inventory_title_trinket<trinket_id>` (no separator before the id)
    - `str_quirk_name_<quirk_id>`
    """

    @staticmethod
    def _hero_class_map(store, game_dir: Path | None = None) -> dict[str, str]:
        """{whatever a comp JSON writes in `heroClass`: hero id}.

        `HERO_DISPLAY_NAME_TO_ID` is twenty hand-written rows, and an
        unrecognised value is dropped SILENTLY -- the comp trains as a smaller
        party, which is the failure mode `CLAUDE.md` warns about twice. That is
        survivable for a fixed roster of twenty. It is not survivable for
        third-party classes, which is the entire point of `DDSIM_MOD_DIRS`: a
        modded class could never appear in a comp at all, because no hand-kept
        map has a row for it.

        So the shipped map stays FIRST and authoritative -- it is the only
        thing that knows "Man At Arms" means `man_at_arms` -- and everything
        the store actually loaded is added underneath it, under both its id
        (`sibyl_ms`) and the title form `HeroLoader` derives for its display
        name (`Sibyl Ms`). Shipped rows always win, so this cannot change how a
        single existing comp resolves; it can only rescue a slot that was being
        dropped.
        """
        out = dict(HERO_DISPLAY_NAME_TO_ID)
        # The class's OWN NAME, as the game states it. `hero_class_name_<id>`
        # is the same shape of key the skill, trinket, camp-skill and quirk
        # maps already read, and reading it here is what lets a comp name a
        # modded class the way a human writes it. Fran wrote "Sibyl"; the id is
        # `sibyl_ms` and the derived title form is "Sibyl Ms", so without this
        # his comp resolved three heroes of four and was excluded from the
        # pool -- correctly, and uselessly.
        if game_dir is not None:
            for suffix, display in CompLoadoutLoader._load_english_entries(
                    Path(game_dir), "hero_class_name_").items():
                if suffix in getattr(store, "heroes", {}):
                    out.setdefault(display, suffix)
        for hero_id in getattr(store, "heroes", {}):
            out.setdefault(hero_id, hero_id)
            out.setdefault(hero_id.replace("_", " ").title(), hero_id)
            # `.title()` WITHOUT the underscore replaced ("Sibyl_Ms") is what
            # `comp_ga` used to emit. Kept as a net rather than relied on --
            # the emitter writes the localized name now -- because the failure
            # it prevents is silent: an unresolved class drops the slot and the
            # comp scores as a three-hero party.
            out.setdefault(hero_id.title(), hero_id)
        return out

    @staticmethod
    def _load_english_entries(game_dir: Path, prefix: str) -> dict[str, str]:
        """{entry_id_suffix_after_prefix: display_name}, scanning every
        *.string_table.xml (base game + DLC) for English-section entries
        starting with `prefix`."""
        result: dict[str, str] = {}
        search_roots = [game_dir / "localization"]
        dlc_dir = game_dir / "dlc"
        if dlc_dir.exists():
            search_roots.extend(dlc_dir.glob("**/localization"))
        # Third-party classes ship their own string tables, and a comp JSON
        # names skills and trinkets by DISPLAY name -- so without these a comp
        # fielding a modded hero resolves nothing and is silently dropped to a
        # smaller party, the exact failure mode the 2026-08-26 name-collision
        # fix chased.
        #
        # They are a SEPARATE pass, not more `search_roots`, because the loop
        # below is last-wins and this one must not be. Base and DLC keep the
        # precedence they have always had; a mod may only fill an id nobody
        # claimed. Otherwise a mod could rename a shipped skill and every comp
        # naming it would quietly resolve somewhere else.
        from .mod_roots import mod_roots
        from .text_io import read_text

        def scan(roots, *, overwrite: bool):
            for root in roots:
                if not root.exists():
                    continue
                for path in root.glob("*.string_table.xml"):
                    # A MOD'S STRING TABLE MAY LIE ABOUT ITS OWN ENCODING.
                    # Three of the subscribed Workshop files declare
                    # `encoding="UTF-8"` in the XML header and carry bytes
                    # that are not (seraph: 0xce at offset 227,850). A strict
                    # read dropped the WHOLE file silently, so ~600 entries
                    # disappeared and the class's trinkets then had no display
                    # name -- which surfaced two layers away as
                    # `build_hero_block` refusing the class for "its trinkets
                    # do not resolve". See `data/text_io.py`.
                    try:
                        text = read_text(path, quiet=True)
                    except OSError:
                        continue
                    m = _ENGLISH_SECTION_RE.search(text)
                    if not m:
                        continue
                    for entry_id, raw_name in _ENTRY_RE.findall(m.group(1)):
                        if not entry_id.startswith(prefix):
                            continue
                        display_name = _display_name(raw_name)
                        if not display_name:
                            continue
                        key = entry_id[len(prefix):]
                        if overwrite:
                            result[key] = display_name
                        else:
                            result.setdefault(key, display_name)

        scan(search_roots, overwrite=True)
        for root in mod_roots():
            scan(sorted(root.glob("**/localization")), overwrite=False)
        return result

    @staticmethod
    def _build_skill_name_maps(game_dir: Path, known_hero_ids: set[str]) -> dict[str, dict[str, str]]:
        """{hero_id: {display_name: skill_id}}. Hero ids are matched by
        longest known-id prefix, not a naive first-underscore split —
        several hero ids contain underscores themselves (man_at_arms,
        bounty_hunter, grave_robber, plague_doctor), which a first-'_'
        split would cut in the wrong place.

        **A skill with no display name is a class that cannot be composed**,
        so a parse gap here costs whole classes rather than one string.
        `dis_trapper` and `spas` were each missing two, which read as "the mod
        author never wrote them" and was wrong: the entries are multi-line
        CDATA with a colour-tagged decoration row above the name, and
        `_ENTRY_RE` could not match them at all. See `_display_name`.
        """
        raw = CompLoadoutLoader._load_english_entries(game_dir, "combat_skill_name_")
        sorted_hero_ids = sorted(known_hero_ids, key=len, reverse=True)
        maps: dict[str, dict[str, str]] = {}
        for suffix, display_name in raw.items():
            hero_id = next((h for h in sorted_hero_ids if suffix.startswith(h + "_")), None)
            if not hero_id:
                continue
            skill_id = suffix[len(hero_id) + 1:]
            maps.setdefault(hero_id, {})[display_name] = skill_id
        return maps

    @staticmethod
    def _build_camp_skill_name_map(game_dir: Path) -> dict[str, str]:
        """Keyed by UPPERCASE display name — localization values are ALL
        CAPS ("ENCOURAGE") but the comp JSON uses title case ("Encourage")."""
        raw = CompLoadoutLoader._load_english_entries(game_dir, "camping_skill_name_")
        return {display_name.upper(): skill_id for skill_id, display_name in raw.items()}

    @staticmethod
    def _build_trinket_name_map(game_dir: Path, known_ids: set[str] | None = None) -> dict[str, str]:
        """{display_name: trinket_id}, preferring an id the store actually has.

        Ten display names are claimed by two ids each, and on nine of them one
        id is a real trinket and the other is not: "Prophet's Eye" is both
        `boss_prophet` (shipped in `trinkets/base.entries.trinkets.json`) and
        `prophet_5` (localization only, in no shipped JSON at all). A plain
        dict comprehension keeps whichever the scan met last, and it met the
        dead one -- so **63 of the 1039 trinket slots across the curated comps
        resolved to an id nothing could look up and silently equipped
        nothing**, on exactly the boss trophies a real party earns and wears.

        With no `known_ids` the old first-wins behaviour is kept, so the map
        can still be built before a store exists.
        """
        raw = CompLoadoutLoader._load_english_entries(game_dir, "str_inventory_title_trinket")
        out: dict[str, str] = {}
        for trinket_id in sorted(raw):
            display_name = raw[trinket_id]
            if display_name not in out:
                out[display_name] = trinket_id
            elif known_ids is not None and trinket_id in known_ids and out[display_name] not in known_ids:
                out[display_name] = trinket_id
        return out

    # ── the punctuation net ────────────────────────────────────────────────
    #
    # Every display-name lookup below used to be exact, with a silent drop for
    # anything it missed. Measured 2026-08-27 over `curated_comps/`, that cost
    # **138 of 703 quirk slots, 42 of 1304 trinket slots and 27 of 2578 camp
    # slots** -- 19.6% of the pool's quirks equipping nothing at all, with
    # `Corvids Eye` (79 slots) and `Corvids Grace` (57) the whole of the quirk
    # loss. The game's string table spells them `Corvid's Eye` / `Corvid's
    # Grace`, and an export that drops the apostrophe reads as a hero with two
    # fewer quirks rather than as an error.
    #
    # Same shape of bug as the trinket-display-name collision already recorded
    # in CHANGELOG.md, and the reason it hid for so long is that a comp with
    # three quirks instead of five is perfectly playable -- nothing raises.
    #
    # TWO fallbacks, both PURELY orthographic. Neither may map one name onto a
    # DIFFERENT name:
    #
    #   1. punctuation -- apostrophes deleted, hyphens are spaces, whitespace
    #      collapsed. Zero collisions across all three maps (245 quirks, 620
    #      trinkets, 82 camp skills), so it is unambiguous by measurement.
    #   2. squashed -- every non-alphanumeric removed, which is what recovers
    #      `Snake Skin` -> `SNAKESKIN`. This one DOES collide, exactly once
    #      (`edge_comb` vs `edgecomb`), so an ambiguous bucket is dropped
    #      rather than guessed.
    #
    # What it deliberately does NOT do is guess a different name. Five names
    # are still unresolved and each is a real difference, not punctuation:
    # `Hunter's Talons` vs the shipped `Hunter's Talon`, `Tempting Goblet` vs
    # `The Tempting Goblet`, `First Aid` vs `Wound Care` (already recorded in
    # CLAUDE.md), and `Cloak and Dagger` / `Eerie Eye`, which match nothing in
    # this install at all. Those are for Fran to correct at the source; a
    # loader that invents them would be a convention wearing game-data
    # clothes. `tests/data/test_comp_name_resolution.py` pins the list.

    @staticmethod
    def _punct_key(name: str) -> str:
        s = name.replace("’", "'").replace("'", "")
        s = re.sub(r"-", " ", s)
        return re.sub(r"\s+", " ", s).strip().lower()

    @staticmethod
    def _squash_key(name: str) -> str:
        return re.sub(r"[^a-z0-9]", "", name.lower())

    @staticmethod
    def _fuzzy_index(name_to_id: dict) -> dict:
        """{punct key -> id} then {squash key -> id}, ambiguous buckets dropped."""
        out: dict = {}
        for keyfn in (CompLoadoutLoader._punct_key, CompLoadoutLoader._squash_key):
            buckets: dict = {}
            for name, ident in name_to_id.items():
                buckets.setdefault(keyfn(name), set()).add(ident)
            for key, idents in buckets.items():
                if len(idents) == 1:
                    out.setdefault(key, next(iter(idents)))
        return out

    @staticmethod
    def _resolve_display(name, exact: dict, fuzzy: dict):
        """Exact display name, then the orthographic net. None if neither."""
        if name in exact:
            return exact[name]
        for keyfn in (CompLoadoutLoader._punct_key, CompLoadoutLoader._squash_key):
            hit = fuzzy.get(keyfn(name))
            if hit is not None:
                return hit
        return None

    @staticmethod
    def _build_quirk_name_map(game_dir: Path) -> dict[str, str]:
        raw = CompLoadoutLoader._load_english_entries(game_dir, "str_quirk_name_")
        return {display_name: quirk_id for quirk_id, display_name in raw.items()}

    @staticmethod
    def _json_paths(csv_dir: Path) -> list[Path]:
        """Every comp JSON the trainable pool may draw on, newest layout first.

        **Two directories, and the split is a claim about PROVENANCE.**
        `curated_comps/` means Fran played the comp in the real game and
        vouches for it. `composed_comps/` means a machine recombined
        human-authored hero blocks into a new class multiset to give a starved
        class training coverage -- nobody has played those, and no fitness
        chose them either. Both are read, because both are legitimate training
        material; they are separate folders so that a year from now the word
        "curated" still means what it says and nothing has to infer provenance
        from a file's contents.

        The JSONs were lifted out of `COMPS/Comps/<name>/<name>.json` into a
        flat `COMPS/curated_comps/*.json` on 2026-08-21, because they are the
        only part of a comp directory any code reads -- the siblings were
        PNGs and a Notion `.md`. **`COMPS/Comps/` and the two `Comps <hash>`
        CSVs were DELETED on 2026-08-23**, once the pool stopped being built
        from the CSV and the fan-made-class filter became structural
        (`CompLoadoutEntry.declared_heroes` against the resolved count) rather
        than a name list parsed out of those `.md` files.

        The old layout is still read when the new folder is absent, the same
        shape of fallback as `csv_root` -- an archived copy or an older
        checkout has to keep resolving, and `CsvLoader.find_csv` returning
        `None` for a missing directory means the failure mode here is a
        silently EMPTY store rather than an error.

        `COMPS/GA_comps/` is the third kind and is deliberately NOT read: those
        are search output, chosen by `P(this policy completes the run)`, so
        training on them would close the loop between the comps and the policy
        that scored them. Promoting one is a decision and should look like one.
        """
        root = csv_root(csv_dir) / "COMPS"
        paths: list[Path] = []
        for name in ("curated_comps", "composed_comps"):
            folder = root / name
            if folder.is_dir():
                paths.extend(sorted(folder.glob("*.json")))
        if paths:
            return paths
        legacy = root / "Comps"
        if legacy.is_dir():
            return sorted(legacy.glob("*/*.json"))
        return []

    @staticmethod
    def load_all(csv_dir: str | Path, game_dir: str | Path, store: DataStore) -> list[CompLoadoutEntry]:
        csv_dir = Path(csv_dir)
        game_dir = Path(game_dir)
        json_paths = CompLoadoutLoader._json_paths(csv_dir)
        if not json_paths:
            return []

        skill_maps = CompLoadoutLoader._build_skill_name_maps(game_dir, set(store.heroes.keys()))
        camp_skill_map = CompLoadoutLoader._build_camp_skill_name_map(game_dir)
        trinket_name_to_id = CompLoadoutLoader._build_trinket_name_map(game_dir, set(store.trinkets))
        quirk_name_to_id = CompLoadoutLoader._build_quirk_name_map(game_dir)
        quirk_fuzzy = CompLoadoutLoader._fuzzy_index(quirk_name_to_id)
        trinket_fuzzy = CompLoadoutLoader._fuzzy_index(trinket_name_to_id)
        camp_fuzzy = CompLoadoutLoader._fuzzy_index(camp_skill_map)
        class_map = CompLoadoutLoader._hero_class_map(store, game_dir)

        entries: list[CompLoadoutEntry] = []
        for json_path in json_paths:
            try:
                data = json.loads(json_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            team_name = data.get("teamName", "")
            location = data.get("location", "")
            if not team_name:
                continue

            heroes: list[HeroLoadout] = []
            declared_classes = tuple(h["heroClass"] for h in data.get("heroes", [])
                                     if h.get("heroClass"))
            declared = len(declared_classes)
            for h in data.get("heroes", []):
                hero_id = class_map.get(h.get("heroClass", ""))
                if not hero_id:
                    continue
                name_to_skill_id = skill_maps.get(hero_id, {})
                skill_ids = [sid for sid in (_resolve_skill_name(n, name_to_skill_id)
                                             for n in h.get("activeSkills", []))
                             if sid is not None]
                camp_skill_ids = [
                    cid for cid in (
                        CompLoadoutLoader._resolve_display(n.upper(), camp_skill_map, camp_fuzzy)
                        for n in h.get("activeCampSkills", []))
                    if cid is not None
                ]
                trinket_ids = [
                    tid for tid in (
                        CompLoadoutLoader._resolve_display(n, trinket_name_to_id, trinket_fuzzy)
                        for n in (h.get("trinket1"), h.get("trinket2")) if n)
                    if tid is not None
                ]
                quirks = h.get("quirks") or {}
                quirk_ids = {
                    pol: [qid for qid in (
                            CompLoadoutLoader._resolve_display(n, quirk_name_to_id, quirk_fuzzy)
                            for n in quirks.get(pol, []))
                          if qid is not None]
                    for pol in ("positive", "negative")
                }
                heroes.append(HeroLoadout(
                    hero_id=hero_id,
                    skill_ids=skill_ids,
                    camp_skill_ids=camp_skill_ids,
                    trinket_ids=trinket_ids,
                    quirk_ids=quirk_ids,
                ))

            if heroes:
                entries.append(CompLoadoutEntry(name=team_name, location=location,
                                                heroes=heroes,
                                                alias=str(data.get("alias") or ""),
                                                declared_heroes=declared,
                                                declared_classes=declared_classes,
                                                source=json_path.parent.name))

        return entries
