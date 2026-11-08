# Commands

Every runnable command in one place. The reasoning behind each lives in the
`CLAUDE.md` for the directory it belongs to, or in `docs/findings/`.

**Before any long job:** confirm no other Python is alive
(`Get-Process python -ErrorAction SilentlyContinue`), and never run two at
once. `python tools/safe_run.py -- <cmd>` wraps a job in five guards.

## Setup

```bash
pip install -r requirements.txt      # declares sb3/sb3-contrib/torch/pytest/xdist, pins gymnasium<2
```

The game must be installed at the path in `data/paths.py`
(`DDSIM_GAME_DIR` overrides it). Nothing in `env/` runs without it.

## Tests

```bash
python -m pytest tests/ -v                       # ~2,830 tests, ~55 s, ~9.7 GB peak
python -m pytest tests/engine/test_combat.py -v
python -m pytest tests/engine/test_combat.py::test_2v2_combat -v
python tests/data/test_loader.py                 # every test file is also a plain script
python -m pytest tests/bridge/test_run.py -n0    # -n0 to debug: a crashed xdist worker reports poorly
DDSIM_NO_STORE_CACHE=1 python -m pytest tests/   # bypass the store cache (~2x slower)

python tools/find_silent_tests.py                # which tests assert NOTHING
python tools/find_silent_tests.py tests/bridge/test_route.py
```

## Training

```bash
python train.py --check                                     # gymnasium API compliance
python train.py --timesteps 200000                          # fixed 2xCrusader vs 2xCultist-Brawler
python train.py --expedition --timesteps 500000             # randomized comps x zone-matched enemies
python train.py --full-expedition --timesteps 3000000       # one episode = a full 8-18 combat expedition
python train.py --play                                      # watch one episode
python train.py --tutorial --timesteps 500000 --n-envs 8 --seed 0   # the Old Road only

# THE RECIPE THE SHIPPED CHECKPOINT WAS TRAINED WITH.
python train.py --full-expedition --timesteps 12000000 --n-envs 12 --seed 0 \
    --hard-focus --no-abandon-train --batch-size 256 --lr-schedule linear \
    --out models/<name>.zip

python train.py --expedition --timesteps 2000 --smoke        # throwaway checkpoint + logs
```

**A short run WITHOUT `--smoke` overwrites the live checkpoint AND
`logs/<mode>/best_model.zip`.** `models/` is gitignored, so git cannot restore
it. This already cost one checkpoint. Use `--smoke` or `--out PATH`.

`--net-arch 512,512` and `--n-epochs 5` were **measured and rejected**; the
distribution bundle was measured and is **harmful**. See
`docs/findings/training-recipe.md` before adding a flag.

## Evaluation

```bash
# THE eval to run: zero comp-sampling error, reproducible since 2026-08-24.
python eval_expedition.py --model <ckpt> --full-expedition --all-comps --episodes 4

python eval_expedition.py --model <ckpt> --episodes 100 --comps 5 --comp-seed 0
python tools/summarize_eval.py archive/logs/eval_6M.log archive/logs/eval_wikifix_3M.log

# Which bosses kill, and whether in the boss fight or before it (--all-comps scenarios).
python tools/boss_report.py --model <ckpt> --episodes 600 --workers 4

# Paired A/B with a bootstrap CI and exact McNemar.
python tools/ab_compare.py --a <ckptA> --b <ckptB> --episodes 6 --comps 12 --comp-seed 1
python tools/ab_compare.py --a <ckpt> --self-check      # must print all zeros

python tools/eval_tutorial.py --a <ckpt> --b models/dd_combat_ppo_tutorial.zip
python tools/endless.py --episodes 24 --cap 40           # battles before someone dies
python tools/transfer_eval.py --model <ckpt> --hold-out-class duelist --baseline <ckpt>
```

Read **QUALITY**, not just SUCCESS — SUCCESS is a saturated gate. Quote the
per-band standard error with any rate.

## Diagnostics

```bash
python tools/analyze_behaviour.py --model <ckpt> --episodes 30 --dump-log out.log
python tools/battle_report.py --model <ckpt> --comp "Riposte Style (Hag)" --combats 1
python tools/analyze_coverage.py
python tools/check_playbook.py --model <ckpt> --episodes 36 --comps 12 --comp-seed 1
python tools/ablate_forecast.py --model <ckpt> --episodes 2
python tools/forecast_calibration.py --model <ckpt> --episodes 10
python tools/agreement.py --a <ckpt> --b <ckptB>          # --self-check must be all zeros
```

## The rulers

Independent graders. **None of these may become a training target** —
`tests/suite/test_rulers.py` enforces it.

```bash
python tools/skill_tiers.py                       # coverage + the tier histogram
python tools/style_probe.py --model <ckpt> --episodes 6 --limit 60
python tools/team_probe.py --model <ckpt> --episodes 4 --limit 30

# Which classes and skills the policy prefers, as picks over the decisions the
# skill was LEGAL. Emits the shape Fran's team-builder scorer reads, so the
# policy and the comp library rank side by side in the app's Generalist view.
# DDSIM_MOD_DIRS must name the roots the checkpoint trained with -- it refuses
# rather than measure a vanilla-only store. `grep '^Mod roots: ' the train log`.
python tools/model_preference.py --model models/mods54_knobs_s0.zip --episodes 4     --json archive/logs/model_pref_s0.json
python tools/model_preference.py --model <ckpt> --self-check   # two sweeps, identical
python tools/play_log_probe.py                    # the same metrics on REAL play
python tools/play_log_probe.py --distribution
python tools/check_class_play.py
python tools/stress_ratchet.py --no-sim           # real play only, free
python tools/retreat_audit.py
python tools/retreat_audit.py --explain 13:23:03
python tools/exchange_rates.py
python tools/class_visibility.py --mods "<root>;<root>" --json out.json
python tools/journal_report.py --differs

# Recording a human session (bat\record_play.bat): inputs only while the game
# is focused, a frame per press, every changed save. See bat/README.md.
python bridge/recorder.py --selftest              # the input hook delivers
python bridge/recorder.py                         # record until the game closes
python tools/recording_report.py [SESSION] [--clicks] [--marks]
python tools/recording_report.py --unpack DIR     # then run.py --replay DIR
```

## The comp search

```bash
python tools/comp_ga.py --self-check --model <ckpt>          # must print all zeros
python tools/comp_ga.py --model <ckpt> --zones crypts --workers 12
python tools/comp_ga.py --model <ckpt> --require-class duelist
python tools/comp_ga.py --scenario-probe --model <ckpt>      # RE-RUN AFTER ANY RETRAIN
python tools/comp_ga.py --explain metadata/COMPS/GA_comps/GA_duelist_01.json --model <ckpt>
python tools/comp_leaderboard.py --model <ckpt> --tier 5 --band medium --episodes 16 --workers 12 --seed 0
python tools/compose_class_comps.py --from-gate out.json --count 4
```

## The campaign simulator

```bash
# The ruler: 40 campaigns x 60 weeks, paired by seed; run both blocks (0, 40).
python -m campaign.run --campaigns 40 --weeks 60 --workers 6 --seed 0 \r
    --gear-budget 0.5 --jsonl archive/logs/campaign/SW_<tag>_s0.jsonl
# One campaign told as a story (F3); --best picks it from a ruler run.
python -m campaign.chronicle --best archive/logs/campaign/SW_v8_g0.5_s0.jsonl \r
    --out archive/logs/campaign/chronicle_v8_s0.md

# The F2 learning curve: every shipped version, world vs decision steps
python -m campaign.curve --png docs/figures/campaign_curve.png
```

Experiment knobs (defaults are the shipped rules): `--boss-margin`,
`--max-underleveled`, `--max-gap`, `--coach-first` (the v8 building order;
smith first is shipped), `--no-sanitarium`, `--discount-cap`,
`--no-quest-quirks`. See `docs/CAMPAIGN_SIM.md`.

```bash
# Death's Door per checkpoint (E22): entries, rolls, deaths per entry, paired
python tools/deaths_door_eval.py --models models/a.zip,models/b.zip --episodes 1200
# The training knob it judges: charge P per deathblow roll (0 = every archived checkpoint)
python train.py --full-expedition ... --dd-exposure-penalty 10 --out models/<name>.zip
```

## The bridge — against the running game

Launch the game **windowed and smaller than the monitor**
(`-fullscreen 0 -size 1280 720`), with **Steam running**.

```bash
python bridge/run.py --survival 12   # models/dd_combat_ppo_full_expedition.zip
python bridge/run.py --replay archive/traces/raidsnaps2       # validate the marking offline
python bridge/run.py --boot --act                             # front end -> new campaign -> tutorial
python bridge/overlay.py --model <ckpt> --log run1.log
python bridge/advisor.py --once --raid <snap> --roster <roster>

python bridge/screen.py --once                # which screen is up
python bridge/menus.py --slots
python bridge/window.py --list
python bridge/layout.py --resolve
python bridge/act.py --probe                  # PostMessage vs SendInput
python bridge/act.py --replay archive/traces/raidsnaps2 --model <ckpt>
python bridge/inventory.py --want 2
python bridge/mappanel.py <frame.png> <persist.map.json> --area rooB
python bridge/lootplan.py <persist.map.json> --have torch=4,skeleton_key=1

python bridge/town.py --coach                 # plan it
python bridge/town.py --coach --act           # and do it
python bridge/townlayout.py                   # every derived point + the cross-checks
python bridge/townplan.py --profile <dir>
python bridge/embarkplan.py --seed 0 --all
python bridge/town.py --embark-setup --act
```

`bat/*.bat` are the double-click wrappers Fran uses; `bat/README.md` says which
ones send input.

## Game data and the binary

```bash
python -m data.provisions --zone crypts --length 1
python -m data.hero_skills --profile <dir>
python -m data.heirlooms
python -m data.loot_loader --darkness-table --walk-costs
python -m data.generate_enemy_comps          # MUST run as a module, not a script path

python tools/pe_anchors.py --sections
python tools/pe_anchors.py --rtti --grep Battle
python tools/pe_anchors.py --key cutoffAlwaysMiss
python tools/pe_anchors.py --telemetry
python tools/ghidra_export.py --key cutoffAlwaysMiss   # refuses if exe and program differ
python tools/ghidra_export.py --all               # decompile every function (~10 min)
python tools/ghidra_diff.py D:/ghidra/out/e95ff9f0 D:/ghidra/out/d83a6d57
python tools/decomp_lookup.py --fn Battle::ApplyHit --callers --callees
python tools/rules_audit.py                   # every rules.json key against the parser
python tools/damage_oracle.py --list          # F9: paired damage draws vs HP lost (needs record_play_full)

python tools/dbwin_capture.py --selftest      # always, before trusting a negative result
python tools/dbwin_capture.py --probe --process Darkest.exe --out trace.log --duration 600
python tools/game_trace.py trace.log --summary
python tools/hit_oracle.py                    # resolve_to_hit vs 186 real (chance, roll) pairs
python tools/brain_oracle.py                  # _select_enemy_skill vs the game's weight vectors
python tools/dson.py "<save>/persist.roster.json" --find heroes
```

## Proving a refactor changed nothing

```bash
python tools/trajectory_oracle.py --emit      # BEFORE the first edit
python tools/trajectory_oracle.py             # after every edit
python tools/trajectory_oracle.py --quick     # a subset, for iterating
```

Plays ~3,700 deterministic episodes across all three env classes in about 14
seconds and reduces each to one `blake2b` digest of every observation, reward
and terminal flag, with the RNG draw count beside it. Byte-identical or the
refactor changed behaviour. Both RNG streams are pinned (`seed_scenario` and
`reset(seed=)`); pinning one reads as a real difference.

`tests/suite/test_trajectory_oracle.py` guards the instrument itself -- that
it is deterministic, that its walk takes every action the envs ever make
legal, and that it actually notices a nudged constant on both the engine and
the env side.

## Housekeeping

```bash
python tools/doc_migration_audit.py           # did the docs lose anything?
python tools/safe_run.py -- <any long command>
```

### The transcription oracle (`tools/pizzarat_oracle.py`)

Grades the engine's parsed monster data against an independent community
transcription of the same game files. Found the missing enemy `.launch`
filter on 2026-09-19. Exits non-zero on a disagreement that is not already
adjudicated in `KNOWN_SITE_ERRORS`; adjudicate a new one against the
monster's own `.info.darkest`, never against the site.

```bash
python tools/pizzarat_oracle.py                 # grade every field
python tools/pizzarat_oracle.py --field launch  # one field only
python tools/pizzarat_oracle.py --verbose       # every disagreement
python tools/pizzarat_oracle.py --refresh DIR   # re-extract from the .js
```

See `docs/COMMUNITY_GUIDES.md`.

### The unread-key audit (`tools/monster_key_audit.py`)

Every key the monster files ship against every key the code mentions. Fails
when the game carries a field nobody has classified -- the guard that would
have caught `shared_health`.

```bash
python tools/monster_key_audit.py
python tools/monster_key_audit.py --all
python tools/monster_key_audit.py --carriers does_count_towards_stall_penalty
```

See `docs/COMMUNITY_GUIDES.md` and `ASSUMPTIONS.md`.
