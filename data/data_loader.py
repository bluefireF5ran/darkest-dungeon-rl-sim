from __future__ import annotations
from pathlib import Path
from .types import (
    DataStore, HeroDefinition, HeroClass, HeroStats, SkillDefinition, SkillLevelData,
    SkillType, WeaponTier, ArmourTier, EnemyDefinition, HeroMode,
)
from .darkest_parser import DarkestParser
from .csv_loader import CsvLoader, csv_root
from .rules import RulesLoader
from .buff_loader import BuffLoader
from .effect_loader import EffectLoader
from .hero_loader import HeroLoader
from .monster_brain_loader import MonsterBrainLoader
from .enemy_comp_loader import EnemyCompLoader
from .comps_csv_loader import CompsCsvLoader
from .comp_loadout_loader import CompLoadoutLoader
from .provision_loader import ProvisionLoader
from .map_gen_loader import MapGenLoader
from .camping_loader import CampingLoader
from .district_loader import DistrictLoader
from .trait_loader import TraitLoader
from .mod_roots import merge_new_only, mod_files


class DataLoader:
    """Central loader binding CSVs and game files into a DataStore."""

    @staticmethod
    def load_all(csv_dir: str | Path, game_dir: str | Path) -> DataStore:
        csv_dir = Path(csv_dir)
        game_dir = Path(game_dir)
        store = DataStore()

        # 1. Rules
        rules_path = game_dir / "shared" / "rules.json"
        if rules_path.exists():
            store.rules = RulesLoader.load(rules_path)

        # 2. Buffs
        buffs_path = game_dir / "shared" / "buffs" / "base.buffs.json"
        if buffs_path.exists():
            store.buffs = BuffLoader.load_buffs(buffs_path)

        # 3. Effects
        effects_path = game_dir / "effects" / "base.effects.darkest"
        if effects_path.exists():
            store.effects = EffectLoader.load(effects_path)

        # 3b/2b. DLC heroes (shieldbreaker, musketeer, flagellant) reference
        # effect/buff names that only exist in their own DLC packages, not in
        # the base shared/ tables. Merge those in too, base tables win on
        # id collisions (base loaded first, DLC .update() only adds/overrides
        # DLC-specific ids).
        dlc_path = game_dir / "dlc"
        if dlc_path.exists():
            for effects_file in dlc_path.glob("**/*.effects.darkest"):
                store.effects.update(EffectLoader.load(effects_file))
            for buffs_file in dlc_path.glob("**/*.buffs.json"):
                store.buffs.update(BuffLoader.load_buffs(buffs_file))

        # 4. Trinkets
        trinkets_path = game_dir / "trinkets" / "base.entries.trinkets.json"
        if trinkets_path.exists():
            store.trinkets = BuffLoader.load_trinkets(trinkets_path)

        # 5. Quirks
        quirks_path = game_dir / "shared" / "quirk" / "quirk_library.json"
        if quirks_path.exists():
            store.quirks = BuffLoader.load_quirks(quirks_path)

        # 5b/4b. DLC-exclusive trinkets/quirks (Color of Madness, Crimson
        # Court, Musketeer/Shieldbreaker/Flagellant DLC content) — same gap
        # as 3b/2b above but for trinkets/entries.trinkets.json and
        # shared/quirk/*quirk_library.json, found while wiring real per-comp
        # trinkets/quirks (data/comp_loadout_loader.py) into CombatEngine:
        # any comp using a DLC-exclusive trinket (e.g. Color of Madness's
        # "Thirsting Blade", com_hel_thirsting_blade) resolved a real id via
        # localization but silently no-op'd in apply_trinkets_and_quirks
        # since store.trinkets never had it. Base tables win on id
        # collisions, matching the effects/buffs merge above. Arena PvP's
        # (dlc/1117860_arena_mp) trinkets file is genuinely encrypted/binary
        # (not valid UTF-8, unlike every other plain-text game data file in
        # the install) — skip unreadable files rather than crash; consistent
        # with this project's existing PvP-exclusion convention (Daze, see
        # ASSUMPTIONS.md/CLAUDE.md), so nothing of relevance is lost.
        # Trinket SETS. Base ships none; all four files are under dlc/.
        # Loaded before the entries merge below only for tidiness -- set ids
        # are independent of trinket ids, so there is no collision to resolve.
        for sets_file in list(game_dir.glob("trinkets/*.sets.trinkets.json")) + (
                list(dlc_path.glob("**/*.sets.trinkets.json")) if dlc_path.exists() else []):
            try:
                store.trinket_sets.update(BuffLoader.load_trinket_sets(sets_file))
            except (UnicodeDecodeError, ValueError):
                continue

        if dlc_path.exists():
            for trinkets_file in dlc_path.glob("**/*.entries.trinkets.json"):
                try:
                    store.trinkets.update(BuffLoader.load_trinkets(trinkets_file))
                except UnicodeDecodeError:
                    continue
            for quirks_file in dlc_path.glob("**/*quirk_library.json"):
                try:
                    store.quirks.update(BuffLoader.load_quirks(quirks_file))
                except UnicodeDecodeError:
                    continue

        # 6. Heroes (from game install .info.darkest)
        store.heroes = HeroLoader.load_all(game_dir)

        # 7. Enemies (from game install monsters/)
        enemies = HeroLoader._load_enemies(game_dir)
        for e in enemies:
            store.enemies[e.id] = e
        # Corpse classes, deliberately in their own dict — never sampleable as
        # an encounter, but real stat blocks the engine needs to make a corpse
        # attackable. See HeroLoader._load_corpses.
        for c in HeroLoader._load_corpses(game_dir):
            store.corpses[c.id] = c

        # 8. Monster AI brains (skill/target selection weights)
        #
        # **The DLC files are read too, and a monster without a brain is a
        # monster that cannot choose.** `_monster_roots` started loading the
        # DLC monsters on 2026-09-02; their brains live in their own DLC's
        # `raid/ai/`, so loading one without the other gives the Shieldbreaker's
        # snakes stats, skills, and no way to pick between them.
        #
        # Base first and merged first-wins, like every other DLC merge here: a
        # DLC may ADD a brain and must never silently redefine one.
        brains_path = game_dir / "raid" / "ai" / "base.monster_brains.json"
        if brains_path.exists():
            store.monster_brains = MonsterBrainLoader.load(brains_path)
        dlc_root = game_dir / "dlc"
        if dlc_root.is_dir():
            for extra in sorted(dlc_root.glob("**/raid/ai/*.monster_brains.json")):
                try:
                    loaded = MonsterBrainLoader.load(extra)
                except (OSError, ValueError, UnicodeDecodeError):
                    # `arena.monster_brains.json` is not UTF-8 -- the Arena PvP
                    # content ships encrypted, the same way its trinket file
                    # does, and that one is already skipped rather than
                    # crashing the load. Four of the five DLC brain files read
                    # fine (31 + 3 + 22 + 2 brains); this is the fifth.
                    continue
                for brain_id, brain in loaded.items():
                    store.monster_brains.setdefault(brain_id, brain)

        # 9. Enemy encounter comps (real hall/room/boss/stall/named groups
        # per zone/tier, from dungeons/<zone>/<zone>.<tier>.mash.darkest)
        store.enemy_comps = EnemyCompLoader.load_all(game_dir)

        # 10. Hero comps (community-curated, from COMPS/ CSV — hero class
        # list + designated zone; was declared on DataStore but never
        # actually loaded until now)
        store.comps = CompsCsvLoader.load(csv_dir)
        store.modded_hero_comp_names = CompsCsvLoader.load_modded_hero_comp_names(csv_dir)

        # 11. Real per-hero skill/trinket/camp-skill loadouts per comp
        # (COMPS/curated_comps/*.json) — the only source that narrows a
        # hero's full movepool down to the 4 skills actually equipped.
        loadout_entries = CompLoadoutLoader.load_all(csv_dir, game_dir, store)
        store.comp_loadouts = {entry.name: entry for entry in loadout_entries}

        # 12. Provisions (bandage/antivenom/holy_water/laudanum/food) —
        # inventory/base.supply.inventory.items.darkest, mechanical effects
        # resolved against store.effects (already loaded above) at combat time.
        store.provisions = ProvisionLoader.load_all(game_dir)
        store.hero_class_starting_provisions = ProvisionLoader.load_hero_class_starting_items(game_dir)

        # 13. Dungeon layout generation parameters (scripts/map_generator.darkest)
        # — real per-size/quest/zone hallway+room battle counts. Supersedes the
        # invented EXPEDITION_LENGTH_BANDS convention; see CHANGELOG.md.
        store.map_gen_settings = MapGenLoader.load_all(game_dir)

        # 14. Camping skills (raid/camping/default.camping_skills.json). The
        # CampingSkillDefinition type and CompLoadoutLoader's camp_skill_ids
        # both predate this — the definitions were simply never loaded, so
        # camping was inert. See CHANGELOG.md for why it came into scope.
        store.camping_skills = CampingLoader.load_all(game_dir)

        # 14b. Districts. Every buff id they name was already read in step 2,
        # so this is a binding from a hero tag to existing buffs -- the same
        # shape the trinket table has -- not a new source of stats.
        store.districts = DistrictLoader.load_all(game_dir)

        # 14c. THIRD-PARTY CONTENT (`DDSIM_MOD_DIRS`), and only ever ADDITIVE.
        #
        # `HeroLoader` has discovered modded hero CLASSES since 2026-08-24, and
        # that was half a class: the hero file names effects, buffs, trinkets
        # and camping skills that live in the mod's own subtrees, and every one
        # of those loaders read `game_dir` alone. A modded hero therefore
        # arrived with correct stats and damage and NO PAYLOADS -- 93.8% of the
        # Sibyl's 160 effect references resolved to nothing, the three
        # `switch_mode_*_self` effects among them, so the class whose whole
        # design is three stances could not change stance and five of its seven
        # skills were unreachable.
        #
        # `merge_new_only`, not `.update()`: a mod may add an id, never shadow
        # one. See data/mod_roots.py for why that differs from the DLC rule
        # directly above.
        for effects_file in mod_files("**/effects/*.effects.darkest"):
            try:
                merge_new_only(store.effects, EffectLoader.load(effects_file))
            except (UnicodeDecodeError, ValueError, OSError):
                continue
        for buffs_file in mod_files("**/shared/buffs/*.buffs.json"):
            try:
                merge_new_only(store.buffs, BuffLoader.load_buffs(buffs_file))
            except (UnicodeDecodeError, ValueError, OSError):
                continue
        for sets_file in mod_files("**/trinkets/*.sets.trinkets.json"):
            try:
                merge_new_only(store.trinket_sets,
                               BuffLoader.load_trinket_sets(sets_file))
            except (UnicodeDecodeError, ValueError, OSError):
                continue
        for trinkets_file in mod_files("**/trinkets/*.entries.trinkets.json"):
            try:
                merge_new_only(store.trinkets, BuffLoader.load_trinkets(trinkets_file))
            except (UnicodeDecodeError, ValueError, OSError):
                continue
        for quirks_file in mod_files("**/shared/quirk/*quirk_library.json"):
            try:
                merge_new_only(store.quirks, BuffLoader.load_quirks(quirks_file))
            except (UnicodeDecodeError, ValueError, OSError):
                continue

        # 15. Overstress personalities (shared/trait/trait_library.json + DLC).
        # Never loaded before — the engine hardcoded a flat act-out convention
        # on the belief that these tables shipped only inside the binary. They
        # do not. See data/trait_loader.py for the four divergences this fixes.
        store.traits = TraitLoader.load_all(game_dir)

        return store

    @staticmethod
    def load_csv_roster(csv_dir: str | Path) -> list[dict]:
        """Quick-load just the hero roster from CSVs (metadata only)."""
        csv_dir = Path(csv_dir)
        heroes_dir = csv_root(csv_dir) / "HEROES"
        if not heroes_dir.exists():
            return []
        csvs = CsvLoader.find_all_csvs(heroes_dir, "Heroes")
        for csv_path in csvs:
            if not csv_path.name.endswith("_all.csv"):
                return CsvLoader.load_table(csv_path)
        return []
