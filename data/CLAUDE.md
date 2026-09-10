# Contracts for `data/`

The two-source data model, the `.darkest` parser, the store cache and the
comp pool. Moved verbatim from the root `CLAUDE.md` on 2026-09-07.


## Two-source data model — the central design decision

CSVs (`HEROES/`, `SKILLS/`, `ENEMIES/`, `TRINKETS/`, `QUIRKS/`, `CAMP_SKILLS/`, `COMPS/`) are a **metadata index only** — roster, skill→hero mapping, human-readable reference. They are **insufficient for simulation math** (max-level only, comma-decimal crit mods, free-form effect strings). All numeric truth comes from the game install:

| What | Source (under `GAME_DIR`) |
|---|---|
| Combat constants (crit, stress, death's door, stun, dodge, stall, torchlight) | `shared/rules.json` |
| **Torch brackets, incl. the loot they pay** | `shared/rules.json` `darkness.range_table` — 5 bands; `lowest_excluded: true`, so a band EXCLUDES its lower bound and torch 75 is *dim*. Also `camp_restore_torch` 100, `ambush_torch_reduction` -100. How much one torch restores is in **no** shipped file. The shutdown is only **free** at `remaining_battles <= 1` **and** `moves_left == 0` — "one fight left" is not "the run is over", and claiming it with five corridors still to walk priced a relight at zero. |
| Buff definitions | `shared/buffs/base.buffs.json` |
| Skill per-level atk/dmg/crit/effects (5 levels each) | `heroes/<hero>/<hero>.info.darkest` |
| Monster skills + damage ranges per variant | `monsters/<monster>/<monster>_<A\|B\|C>.info.darkest` |
| Effect templates (push, pull, bleed, blight, stun) | `effects/base.effects.darkest` |
| Trinket→buff-id / quirk→buff-id | `trinkets/base.entries.trinkets.json`, `shared/quirk/quirk_library.json` (+ `dlc/**` merge) |
| Monster AI (165 brains) | `raid/ai/base.monster_brains.json` |
| Enemy encounter comps per zone/tier | `dungeons/<zone>/<zone>.<tier>.mash.darkest` |
| Display-name → internal-id resolution | `localization/*.string_table.xml` (+ `dlc/**`) |
| Provisions | `inventory/base.supply.inventory.items.darkest`, `campaign/provision/provision.json` |
| **Town prices** (what a retreat costs) | `campaign/town/buildings/abbey/abbey.building.json` (gold per stress healed), `upgrades/heroes/<class>.upgrades.json` (gold sunk per weapon/armour/skill rank), `rules.json` `combat_retreat_*` |
| Curio outcomes + which provision changes them | `curios/curio_type_library.csv` (block layout; **weights are truth, the `%` column is stale**), `curios/curio_props.csv` for prop->type |
| **What a loot draw is worth** | `loot/loot.json` + `loot/common_overrides.loot.json` (58+ tables keyed `(id, difficulty, dungeon)`, resolving recursively), `inventory/*.inventory.items.darkest` for `sell_gold_value`. **`loot/loot.json` does NOT match a `*.loot.json` glob** and a missing file here is a silent zero |
| **What darkness pays and costs** | `loot/darkness_overrides.loot.json` (chance of an extra draw per event kind), `rules.json` `tile_light_loss` (1/tile lit, **6 dim**) and `corridor_return_content` (a re-entered corridor repopulates, +50% dark) |
| Camping skills (80) + point budget / ambush chance | `raid/camping/default.camping_skills.json` **plus all four `dlc/**/raid/camping/*.camping_skills.json`** — a DLC class is granted the shared skills by its own DLC re-declaring them, so reading the base file alone left Shieldbreaker, Flagellant, Duelist and Runaway unable to camp. `hero_classes` is UNIONED across files, everything else base-wins. Also `shared/rules.json` (`camp_*`, `ambush_camping_base_chance`) |
| Overstress personalities: per-trait stat effects **and** act-out probability tables | `shared/trait/trait_library.json` (+ `dlc/**/*trait_library.json`) |
| Resolve XP → resolve level (used by `bridge/`) | `campaign/roster/roster.variables.json` (`resolve_level_thresholds`) |
| Per-hero move distance / on-crit passive / Death's-Door tiers | that hero's `.info.darkest` (`combat_move_skill:`, `crit:`, `deaths_door:`) |
| Dungeon layout: rooms/corridors, hallway + room battle counts per size/quest/zone | `scripts/map_generator.darkest` (**multi-line block layout** — parse with `DarkestParser.parse_blocks`, not `parse_file`) |

`DataLoader.load_all(csv_dir, game_dir) -> DataStore` (`data/data_loader.py`) is the single entry point binding both sources. On DLC merge, **base wins on id collision**. The Arena PvP trinkets file is genuinely encrypted/binary and is skipped rather than crashing the load.


### Design principle: prefer `store.comp_loadouts` (per-comp JSON) over `store.comps` (CSV)

For anything about a comp's real hero **identity, order, or count**, the CSV has repeatedly proven unreliable — two independent confirmed bugs:

1. **Formation order** — the CSV's `heroes` field is alphabetically sorted by class name, not the real front-to-back formation. But `CombatEngine.reset()` assigns `position = index`, so that order *is* the formation. Verified: 0/129 comps had CSV order matching the real JSON formation.
2. **Duplicate-class comps** — the CSV deduplicates repeated classes to unique names. "Ascetic Movement" is really 3 distinct Crusaders + 1 Flagellant, each with a genuinely different loadout, but silently evaluated as a 2-hero party. 37/131 loadout comps have a duplicate-class roster.

Consequences that must not be regressed: skill/trinket/quirk resolution is **positional** against `loadout.heroes`, never a `hero_id`-keyed dict (a dict collapses duplicate classes onto whichever was inserted last). Modded-hero comps (11, containing fan-made classes this project can't load) are **excluded outright** from training/eval rather than silently trained as a smaller party. `store.trinkets[id].name`/`store.quirks[id].name` are the *id*, not a real name — resolve names via localization instead.

See `CHANGELOG.md` for the full evidence behind both.


## `.darkest` parser (`data/darkest_parser.py`)

Custom line-oriented format: `combat_skill: .id "manacles" .level 4 .atk 115% .launch 32 .target 123`.

- `%` suffix → float/100; `launch 4321` → `[4,3,2,1]` (usable-from positions)
- `target ~123` → AoE across ranks; `@123` → list includes the performer's own rank (self-targetable). Both prefixes strip in either order.
- A key with multiple consecutive quoted values parses to `list[str]`; a single value stays `str` — **don't assume `rec["effect"]` is one type**
- Unquoted barewords and capitalized booleans parse correctly; the regex tolerates zero whitespace before the next `.key` (a real game file has that typo)

**The trailing-value lookahead requires a letter after the next dot** (`(?=\s*\.[a-zA-Z_]|$)`), not just any dot — an unquoted decimal (`.atk 72.5%`) otherwise truncates to `72`. This silently affected 779 field instances across 240 real game files. Don't loosen it.

**Two layouts, two entry points.** `parse_file` handles the one-record-per-line layout above (`.info.darkest`, `.mash.darkest`). A few files put the `type:` header on its own line with one `.key value` per following line — `scripts/map_generator.darkest` is the one that matters. Use **`parse_blocks`** for those: it rejoins each block and delegates to the same `_parse_line`, so both layouts share one set of value-parsing quirks. Running `parse_file` on a block-layout file **fails silently** — you get the right number of records with every field missing (`{'_type': 'map'}`). `tests/data/test_map_gen.py` pins that distinction.

Multi-value numerics (`.hallway_battle 2 4`) come back as the **string** `"2 4"`, not a list — split at the call site.


### The DataStore is built once and shared (`data/store_cache.py`)

`DataLoader.load_all` costs ~1.0s. Nothing cached it, so a full suite run rebuilt
it **~98 times** — ~98s of a 275s baseline, and `--collect-only` alone cost 32s.

`load_store_cached(csv_dir, game_dir)` memoises per process (`lru_cache`) and to a
2.3 MB pickle on disk. `DarkestDungeonEnv(store=...)` and both expedition envs accept
an injected store; omitting it now goes through the cache, so **bare
`DarkestDungeonEnv()` is already free** and existing call sites needed no change.

Rules that must not be regressed:

- **The CSV roots come from `csv_root(csv_dir)`, never `csv_dir`.** They did not,
  from the `metadata/` move until 2026-08-20, and `_iter_files` skips a missing
  root silently — so the whole CSV half of the digest contributed nothing and a
  comp or CSV edit did **not** invalidate the cache. Found only because a comp
  directory appeared mid-suite. See `CHANGELOG.md`.
- **Invalidation is a stat sweep, not a timestamp.** The key digests `(size, mtime_ns)`
  of every file the loader reads — the game subtrees, the CSV dirs, **and `data/*.py`**,
  since a parser edit changes the store without touching any data. Measured 0.172s over
  10,292 files, ~6x cheaper than the rebuild it guards. A cache that can serve stale
  game data is not a trade-off in this project, it is the failure the CHANGELOG exists
  to prevent. `DDSIM_NO_STORE_CACHE=1` bypasses; the suite must stay green under it.
- **A shared store legitimately grows.** `CombatEngine` memoises synthesised inline
  buffs into `store.buffs` under `__inline__*` / `__buff_type__*`
  (`combat_engine.py:2150`, `:2185`), and `bridge/seed.py` does the same for a save's
  un-named `buff_group` entries under `__live__*`. All are get-or-create keyed on
  something that determines the definition. So a store that has run combat holds MORE
  buffs than a fresh one. `tests/data/test_store_cache.py` exempts exactly those three
  prefixes and fails on anything else appearing — the tripwire for real cross-test
  pollution. **A new synthesis site must adopt the `__` prefix, not be added as a fourth
  exemption**: `bridge/seed.py` keyed its memo `LIVE_*` for months and only read as
  pollution once the xdist scheduler happened to put a save-seeding test and the cache
  test in one worker.
- **A test that MUTATES the store asks for `get_store(fresh=True)`** (`tests/_fixtures.py`).
  `test_conditional_buffs.py` and `test_gaps.py` do. The keys they inject are namespaced,
  so sharing would not break anything *today* — which is exactly why the rule is explicit.
- **`tests/_captures.py` caches decoded captures**, not raw bytes: `Tile`/`Area`/`DungeonMap`
  are all `frozen=True`, so one decode is shareable. `LiveState` is **not** frozen and tests
  do mutate it, so it is deliberately not cached.


### The comp pool is the JSON folder, and nothing else (2026-08-23)

`build_comp_pool(store)` (`env/expedition_env.py`) is the single source, and
both `ExpeditionEnv._build_hero_pool` and
`eval_expedition.pick_representative_comps` delegate to it. The latter used to
keep a hand-synced copy of the same logic, which is exactly how the two drift.
Drop a file into `metadata/COMPS/curated_comps/` and it trains — no CSV row, no
code change, no Fire's Edge carve-out.

- **A comp is resolved by `DataStore.comp_loadout_by_name`, never by indexing
  `comp_loadouts`.** Comps get renamed wholesale — the 2026-08-23 re-export
  renamed 66 of 138 and kept the old name only in a new `alias` field — so a
  direct index silently returns `None`. That is how the tutorial party went
  missing: `The_Old_Road.json` now says `teamName: "Ragged Band: Heist"`.
- **Aliases are NOT extra keys in `comp_loadouts`.** Anything iterating that
  dict to build a pool would draw the same comp twice, under two names.
- **`PINNED_COMP_NAMES` keeps the tutorial party out, and matches on the alias
  too.** It used to be kept out by having no CSV row; with no CSV that
  exclusion has to be explicit, or The Old Road leaks into training and
  quietly corrupts `tools/eval_tutorial.py`.
- **Zone tags go through `zone_id_for_location`.** A loadout JSON says
  `"The Cove"` where the CSV said `"Cove"`. A location resolving to nothing is
  dropped, which is load-bearing: two comps are tagged
  `"The Darkest Dungeon II"`, a place with no enemy pool here.
- **Comparability with any eval before 2026-08-23 is gone.** It was lost when
  the comps were renamed, not when the CSV was dropped: on the re-exported
  folder, `--comp-seed 1` resolved **1 of its 12 comps** and `--comp-seed 0`
  resolved 3.

Both name maps still need the class or the slot is dropped **silently** and the
comp trains as a smaller party: `CompositionLoader.HERO_CLASS_MAP` *and*
`comp_loadout_loader.HERO_DISPLAY_NAME_TO_ID`. An **empty** `heroClass` is not
that failure — the export pads every comp to four slots, so the 2-hero tutorial
party legitimately arrives with two blanks.


### Hero classes: 20, and two of them are stance heroes (2026-08-20)

The Fire's Edge **Runaway** and **Duelist** load from `dlc/4964110_fires_edge`
(two `HeroLoader.HERO_DIR_MAP` entries; the recursive glob already reached their
extra `features/<name>/` level). Consequences that must not be regressed:

- **They no longer have a one-hot at all.** The 20-class block was removed on
  2026-08-23 and what a hero can DO reaches the policy as skill payloads
  instead, which is what makes a Fire's Edge (or modded) class transferable
  rather than a new index. See "The observation encodes mechanics" below.
- **A stance is not a special case.** The data convention is one
  `.<mode>_effects` list per `.valid_modes` entry; the Abomination's
  `human`/`beast` is just the vanilla instance of it. Read it through
  `SkillLevelData.mode_effects[performer.mode]` — the old
  `human_effects if mode == "human" else beast_effects` sent every Duelist
  stance change down the beast branch and silently found nothing. Build the
  dict gated on `.valid_modes`, never on "any key ending in `_effects`", which
  swallows `.self_effects`.
- **`UNRESTRICTED_HEROES`** (`env/expedition_env.py`) is `abomination` and
  `duelist` — all seven skills always equipped, a *mode* deciding which are
  usable, so there is no 4-of-7 to resolve or randomise. Import it; do not
  re-test `== "abomination"` anywhere.
- **The Duelist has no `human` mode**, and `CombatantState.mode` defaults to
  `"human"`. `reset()` takes `hd.modes[0].id`, which is her defensive stance.
- **The Runaway's kit is inert until burn exists.** Six of her seven skills
  apply `.dotBurn`, which `data/effect_loader.py` does not parse (it reads
  `dotBleed`/`dotPoison` only), so those effects resolve to real objects that
  do nothing. Burn is a genuine dormant **vanilla** subsystem, not a mod
  invention — `DECOMP.md` F6 has the full key list from the binary, including
  why a bleed cure must not clear burn (`.cure_burn` is its own key) and which
  number is still unknown (the base decay rate).
- **They have three comps between them, and that is the shortage.** Two
  Duelist ("Dancing Lunge V3.0", "Riposte Style (Duel)") and one Runaway
  ("Stun Control: Burn"); every other class has 9 or more. A comp fielding one
  of them reaches the pool the same way every other comp does, since the CSV
  was retired on 2026-08-23 and `build_comp_pool` iterates the JSON directly.
  `_fires_edge_comps` is gone with it. `tools/comp_ga.py --require-class` and
  `composed_comps/` are what close the gap.
