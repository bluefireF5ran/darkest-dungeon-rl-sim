# Darkest Dungeon Combat Simulator

Turn-based combat simulator for Darkest Dungeon, designed as a Gym environment for RL training.

## Setup

```bash
pip install -r requirements.txt
```

Requires the game installed at `D:\Program Files (x86)\Steam\steamapps\common\DarkestDungeon` and the CSV exports in this repo.

## Run tests

```bash
# All tests
python -m pytest tests/ -v

# Individual test files
python tests/data/test_loader.py
python tests/engine/test_position.py
python tests/engine/test_resolution.py
python tests/engine/test_combat.py
python tests/env/test_env.py
python tests/data/test_composition.py
python tests/engine/test_gaps.py
python tests/env/test_reward.py
python tests/engine/test_torchlight.py
python tests/engine/test_monster_ai.py
python tests/env/test_hero_level.py
python tests/engine/test_aoe.py
python tests/engine/test_transform.py
python tests/engine/test_monster_ai_friendly.py
python tests/engine/test_bonus_initiative.py
python tests/data/test_enemy_comps.py
python tests/env/test_expedition_env.py
python tests/data/test_comp_loadout.py
python tests/data/test_provision_loader.py
python tests/engine/test_hero_move_crit_deathsdoor.py
python tests/engine/test_buff_duration_fallback.py
python tests/engine/test_on_crit_passives.py
python tests/engine/test_move_action.py
python tests/engine/test_position_gap_and_pass_turn.py
python tests/env/test_provisions_combat.py
python tests/engine/test_reinforcements.py
python tests/engine/test_deaths_door_tiers.py
python tests/engine/test_heart_attack.py
python tests/engine/test_carry_over_reset.py
python tests/env/test_expedition_sequence_env.py
python tests/env/test_expedition_reward.py
```

## Data sources

Two sources, both required:

| Source | Path | Purpose |
|---|---|---|
| Game install (.darkest, .json) | `D:\Program Files (x86)\Steam\steamapps\common\DarkestDungeon` | Canonical combat math, buffs, effects, skill level scaling |
| CSV exports | `metadata/HEROES/`, `metadata/SKILLS/`, `metadata/ENEMIES/`, `metadata/TRINKETS/`, `metadata/QUIRKS/`, `metadata/CAMP_SKILLS/`, `metadata/COMPS/` | Metadata indexing (hero roster, skill-to-hero mapping). Only HEROES, COMPS and ENEMY_COMPS are read by code; the rest are reference. |

Loading everything:

```python
from data.data_loader import DataLoader
store = DataLoader.load_all("SIM", "D:/.../DarkestDungeon")
# store.heroes — 18 heroes from .info.darkest (15 vanilla + 3 DLC: shieldbreaker, musketeer, flagellant)
# store.enemies — 246 enemy variants
# store.buffs — 2529 buff definitions (base game + DLC packages)
# store.effects — 1809 effect templates (base game + DLC packages)
# store.rules — all combat constants from rules.json
```

CSVs only for metadata — all numeric truth is in the game install.

## Usage

```python
from engine import SeededRNG, CombatEngine
from data.data_loader import DataLoader

store = DataLoader.load_all("SIM", "D:/.../DarkestDungeon")
rng = SeededRNG(42)
engine = CombatEngine(store, rng)

crusader = store.heroes["crusader"]
brawler = store.enemies["cultist_brawler_A"]

engine.reset(hero_defs=[crusader, crusader], enemy_defs=[brawler, brawler])

while not engine.done:
    actions = engine.get_valid_actions(0)
    if actions:
        engine.submit_action(actions[0])
    result = engine.step()
    print(f"Round {result.info['round']}: {result.info.get('log', [])[-1:]}")
```

Gym environment:

```python
import numpy as np
from env import DarkestDungeonEnv

env = DarkestDungeonEnv()
obs, info = env.reset(seed=42, options={
    "hero_ids": ["crusader", "crusader"],
    "enemy_ids": ["cultist_brawler_A", "cultist_brawler_A"],
    "torch_level": 100.0,       # optional, 0-100, defaults to brightest (100)
    "hero_levels": [4, 4],      # optional Resolve Level 1-6 per hero; omit for max tier
})
done = False
while not done:
    action = env.action_space.sample()
    obs, reward, terminated, truncated, info = env.step(action)
    done = terminated or truncated
```

Full expedition (one episode = an 8-18 combat trip with the same 4 heroes, HP/stress/Death's-Door/mortality-tier carried between combats, provisions, reinforcements):

```python
from env import ExpeditionSequenceEnv

env = ExpeditionSequenceEnv(sampler_seed=42)
obs, info = env.reset(seed=42)  # samples a real comp, zone, difficulty tier, and expedition length
done = False
while not done:
    action = env.action_space.sample()
    obs, reward, terminated, truncated, info = env.step(action)
    done = terminated or truncated
# episode ends on a full party wipe or the last sampled combat finishing —
# a single hero death does not end it, the expedition continues with survivors
```

## Project structure

```
├── data/           # Data loaders + typed definitions
├── engine/         # Combat engine + state + resolution
├── env/            # Gym environment wrapper
├── tests/          # tests across all layers
├── tools/          # Diagnostics: analyze_behaviour, battle_report, analyze_coverage, summarize_eval
├── models/         # The 3 live checkpoints
├── logs/           # SB3 EvalCallback output, written by train.py
├── metadata/       # The 8 metadata CSV dirs: HEROES, SKILLS, ENEMIES, TRINKETS,
│                   # QUIRKS, CAMP_SKILLS, COMPS, and the generated ENEMY_COMPS
├── docs/           # ASSUMPTIONS, CHANGELOG, BRIDGE, DECOMP, PLAYBOOK, NEXT,
│                   # WIKI_MECHANICS, COMMANDS, findings/, changelog/, assumptions/
├── captures/       # Reference screenshots — not code, not read by anything
├── AGENTS.md       # OpenCode instructions
└── archive/        # Superseded checkpoints, past train/eval logs, prompts,
                    # and the stale ARCHITECTURE.md / PHASE0_REPORT.md
```

The metadata CSV directories moved into `metadata/` on 2026-08-20. **`CSV_DIR` is still
the repo root** — it is hardcoded in `env/gym_env.py` and re-declared across the tests,
all of which pass it to a loader rather than naming a subdirectory. The extra level is
known in one place, `data/csv_loader.py::csv_root`, which falls back to `csv_dir` when
there is no `metadata/`.

## Design principles

- **CSV only for metadata.** All combat math comes from game install files.
- **Buffs are the universal modifier.** Damage, stats, resistances — everything modifies through buffs.
- **Seeded RNG everywhere.** Every random draw goes through `SeededRNG` for deterministic reproduction.
- **Reward is potential-based shaping + terminal bonus.** Per step,
`reward = SHAPING_GAMMA·Φ(s') - Φ(s) + turn_penalty - STRESS_TICK_PENALTY·stress_gained`, where `Φ` is a mean per-side score built from HP%, stress%, affliction/virtue, and death state (see `_hero_score`/`_enemy_score`/`_compute_potential` in `env/gym_env.py`). Plus a terminal win/lose bonus. **Every one of those constants is actively tuned — read
them from `env/gym_env.py`, not from this sentence**, which said `-0.02` and `-3.0`
for a long time after they had become `-0.10`/`-0.01` and `-50.0`. This follows Ng/Harada/Russell (1999) potential-based shaping, which guarantees the shaping doesn't change which policy is optimal — see `docs/ASSUMPTIONS.md` for the full design rationale and `tests/env/test_reward.py` for the tests backing it. `ExpeditionSequenceEnv` layers a second, independent potential-based term on top (aggregate living-party stress) plus a flat one-time `REWARD_HERO_DEATH` penalty on an actual hero death — see `env/expedition_sequence_env.py` and `tests/env/test_expedition_reward.py`.

### Assumptions and data gaps

See `docs/ASSUMPTIONS.md` for the full, current list. Key items:
- CSV shows only max-level skill values; game files have 5 levels per skill
- Enemy encounter tables **are** parsed (`data/enemy_comp_loader.py`, `store.enemy_comps`) — 1105 real hall/room/boss/stall/named comps across the 5 core zones, straight from `dungeons/<zone>/<zone>.<tier>.mash.darkest`; a browsable snapshot lives in `metadata/ENEMY_COMPS/<zone>.json`. `.conditional.` (rare special-event) files and DLC zones aren't loaded — see docs/ASSUMPTIONS.md
- No Crimson Curse (Guard, Riposte, Stealth, Torchlight, and Monster AI **are** implemented)
- Monster AI uses real `base.monster_brains.json` weights and now models essentially every skill/target desire type, including monsters healing/buffing their own side — see docs/ASSUMPTIONS.md for exactly which two mechanics are still declared out of scope (The Hag's hero-capture mechanic, the Ancestor's phase-transition trigger)
- Provisions (bandage/antivenom/holy_water/laudanum as free in-combat actions, food as a between-combat heal), reinforcements, the Move action, per-class on-crit passives, and the 3-tier Death's Door/heart-attack system **are** implemented (`env/expedition_sequence_env.py`'s `ExpeditionSequenceEnv`, one Gym episode = a full multi-combat expedition). Camping/provisioning-phase, dungeon exploration/traversal, curios, and torch decay over travel remain out of scope
