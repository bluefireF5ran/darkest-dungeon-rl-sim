# Darkest Dungeon Combat Simulator

## Status

**All phases complete.** ~2,830 tests passing across data layer, engine, Gym env,
the bridge and the tools. Guard/Riposte, Stealth, real healing resolution, all 20
hero classes (15 vanilla + 5 DLC, plus modded classes behind `DDSIM_MOD_DIRS`),
potential-based reward shaping, correct per-round turn
order, torchlight, monster AI driven by real `base.monster_brains.json`
weights (essentially every skill/target desire type, including monsters
healing/buffing their own side), `bonus_initiative_desires` for all 14
boss-tier brains, Resolve Level (Apprentice/Veteran/Champion party setups via
`hero_levels`), true multi-target AoE with correct ally-vs-enemy targeting,
Abomination mode switching, corpse clearing, guard auto-expiry, monster
skill cooldowns, the consecutive-miss ACC buff, excess-crit overflow damage,
all 4 real heal-modifier stat_types, exact (non-drifting) buff-reversal
math, and real per-zone enemy encounter comps (1105 hall/room/boss/stall/
named groups parsed from `dungeons/<zone>/<zone>.<tier>.mash.darkest`,
`store.enemy_comps` / `metadata/ENEMY_COMPS/<zone>.json`) are all implemented.
Full-expedition simulation (`env/expedition_sequence_env.py`) layers on top:
real per-comp skill loadouts, provisions (bandage/antivenom/holy_water/
laudanum as free in-combat actions, food as a between-combat heal),
reinforcements, the Move action, per-class on-crit passives, hero-death
position collapse, a pass-turn fallback, and the 3-tier Death's Door/
heart-attack system — see `docs/ASSUMPTIONS.md` for exact status per mechanic.
Two mechanics remain deliberately out of scope (The Hag's hero-capture
"captor" mechanic, the Ancestor's `death`-transition trigger) — both need a
new subsystem this simulator has no other support for.

## Project goal

Python turn-based combat simulator → Gym wrapper → RL training (SB3). See `prompt_simulador_darkest_dungeon.md`.

## Two data sources — CSV vs game install

CSVs are a useful **metadata index** (hero roster, skill→hero mapping, enemy list, trinket/comp reference) but **insufficient for simulation math**.

| What | Source |
|---|---|
| Combat constants (crit, stress, death's door, stun, dodge, stall, torchlight) | `shared/rules.json` |
| All buff definitions (~26K lines) | `shared/buffs/base.buffs.json` |
| Skill per-level atk/dmg/crit/effects (5 levels per skill) | `heroes/<hero>/<hero>.info.darkest` |
| Monster skills + exact damage ranges per variant | `monsters/<monster>/<monster>_<A\|B\|C>.info.darkest` |
| Effect templates (push, pull, bleed, blight, stun amounts) | `effects/base.effects.darkest` |
| Trinket→buff-id mapping | `trinkets/base.entries.trinkets.json` |
| Quirk→buff-id mapping | `shared/quirk/quirk_library.json` |
| Camping skill exact effect amounts | `raid/camping/default.camping_skills.json` |
| Monster AI | `raid/ai/base.monster_brains.json` |
| Enemy encounter comps per zone/tier | `dungeons/<zone>/<zone>.<tier>.mash.darkest` |
| Hero/Skill/Enemy metadata | CSVs |
| Real per-comp skill/trinket/camp-skill/quirk loadouts | `metadata/COMPS/Comps/<name>/<name>.json` (this repo, not `GAME_DIR`) + `localization/*.string_table.xml` for name→id resolution |

Game install path: `D:\Program Files (x86)\Steam\steamapps\common\DarkestDungeon`

## Data layout (CSVs)

| Directory | Contents |
|---|---|
| `metadata/HEROES/` | Hero roster + stats at max upgrade |
| `SKILLS/` | Skill defs — only max level, ~150 empty placeholder rows |
| `ENEMIES/` | Enemy stats per tier (Apprentice/Veteran/Champion) |
| `TRINKETS/` | 392 entries, pipe-delimited effects, set bonuses |
| `QUIRKS/` | 185 entries, Positive/Negative, human-readable effects |
| `CAMP_SKILLS/` | 71 entries, time cost 1-5 |
| `metadata/COMPS/` | 145 named party comps |

### CSV quirks

- Each entity has `<Name> <hash>.csv` (clean) and `<Name> <hash>_all.csv` (full export).
- CRIT_MOD% uses comma decimal (`11,5` not `11.5`).
- Effects are free-form strings with `%` signs (e.g. `Blight (140% base) 5 pts/rd for 3 rds`).
- Healing values use range notation (`4-5` = 4-5 HP, `0-22` = 0-22 HP).
- `SKILLS.csv` IDs 130+ are all-`No` placeholders — skip them.
- `Falconer - Marvin Seo` has an extraction typo (comma in name).
- Modded heroes exist (Black Mage, Red Mage, Pit Fighter).

## Key combat mechanics (from game files)

- **Initiative**: `SPD + d8`, `initiative_base = 8`.
- **Accuracy roll**: compare modified ACC vs target dodge. Always-hit ceiling = 95%. Always-miss floor = 0%.
- **Damage**: base range × (1 + DMG MOD). Crit multiplier = ×1.5 (player and monster).
- **Crit roll**: `crit_chance` check, overflow into damage via `excessCritMax = 0.20` (implemented; confirmed data-inert since `excessCritMult = 0.0` in every shipped `rules.json`).
- **Stress**: enemy crits, death's door, specific skills. Resolve check at 100 stress: `affliction_base_onset_chance = 0.75`, `affliction_critical_success_chance = 0.25`.
- **Death's door**: HP ≤ 0 → death's door. `deathblow_chance = 1.0` (next hit kills unless blocked by Death Blow Resist %).
- **Stun**: `stun_ends_turn = true`. Recovery buff: +50% stun resist for 2 rounds.
- **DoTs**: bleed/blight tick per round vs resist. Format: base_pts × (140% base) for N rounds.
- **Corpses**: size-based, block positions, can be cleared.
- **Stall**: after 3 rounds with ≤ 2 enemies, stress damage applied. After 4, reinforcements spawn (2 real, fixed `cultist_brawler`/`cultist_witch` at a matching A/B/C tier — implemented).

## Architecture direction

Expected code layout: `/engine`, `/data`, `/tests`, `/env`.

Key components:
- Typed data loaders (dataclasses/Pydantic) for all entity types
- `.darkest` file parser (custom key-value format, position notation: `launch 4321` = from positions 4/3/2/1, `target 123` = targets 1/2/3)
- `CombatEngine` — 4-position ranks per side, turn order, stress, death's door
- Seeded RNG wrapper
- Gym `reset()` / `step(action)` wrapper

## Rules

- Truth source for numerics: game install files. CSVs only for metadata.
- No premature optimization. Correctness and readability first.
- (Historical, Phase 1) Do not implement RL algorithms or reward functions in this phase — since superseded: reward shaping is implemented, see `docs/ASSUMPTIONS.md`.
