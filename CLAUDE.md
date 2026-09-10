# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with
code in this repository.

**It is deliberately short.** It used to be 3,878 lines and was loaded in full
on every session, most of it a dated lab notebook. On 2026-09-07 that notebook
moved to `docs/findings/` and the per-directory contracts moved next to the
code they constrain, where Claude Code loads them on demand. Nothing was
deleted — `python tools/doc_migration_audit.py` is the check.

What stays here is what applies to *every* task.

## Doc precedence

| File | Role |
|---|---|
| `docs/ASSUMPTIONS.md` | **Source of truth** for what's implemented vs stubbed. Opens with a status table: mechanic → status → where. Check before assuming a mechanic works. |
| `<dir>/CLAUDE.md` | **The contracts for that directory** — `engine/`, `env/`, `data/`, `bridge/`, `tools/`, `tests/`. Loaded automatically when you touch that subtree. Read the one for the code you are changing; most of its rules exist because the first version got it wrong. |
| `CLAUDE.md` (this) | Orientation, the cross-cutting rules, and where everything lives. |
| `docs/findings/` | **The lab notebook**, by subject, with `INDEX.md` carrying one line per finding and its verdict (`shipped` / `null` / `harmful` / `retracted` / `open`). Read the verdict before re-running an experiment — three of them are already settled in the negative. |
| `docs/NEXT.md` | **The pending queue.** Agreed work not started, each item with its evidence, its first command and its falsifier. Read it when a session opens with "what's next". |
| `docs/CHANGELOG.md` | Why the code is the way it is — real bugs found, game-data evidence, rationale per convention. Split by month under `docs/changelog/`. **Read before changing engine behaviour.** |
| `docs/BRIDGE.md` | The full `bridge/` reference. **Required reading before changing anything under `bridge/`**; `bridge/CLAUDE.md` is the summary. |
| `docs/DECOMP.md` | What the game's **code** says, and how we know: the pinned binary and its hash, the anchor table, one entry per finding recovered from `Darkest.exe`. A formula here outranks the wiki and matches the data files. |
| `docs/PLAYBOOK.md` | **The decision spec.** An expert player's criteria, interviewed 2026-08-14. Game files outrank it on anything numeric; it outranks everything on *what to do* with those numbers. |
| `docs/WIKI_MECHANICS.md` | Condensed wiki mechanics. **Game files still outrank it.** |
| `docs/COMMANDS.md` | Every runnable command in one place. |
| `archive/docs/*` | **Historical and stale.** Never "fix" behaviour to match them. |
| `README.md`, `AGENTS.md` | Orientation only. |

For anything numeric and actively tuned — reward constants, observation width,
provision quantities — **read the constant in the source file**. Prose
describing those drifts faster than the code.

## What this is

A Python turn-based combat simulator for Darkest Dungeon, exposed as a
Gymnasium environment for RL training (`sb3_contrib`'s `MaskablePPO`). Not a
game — a faithful numeric reimplementation of the game's combat math, driven by
two data sources.

Beyond the simulator, `bridge/` is an actual **player**: it reads the running
game's saves and screen and decides what to do, end to end, from the front-end
menus through the Hamlet to a completed raid.

## Hard dependency: local Darkest Dungeon install

Combat math is NOT re-derived from CSVs — it's parsed from the actual game's
data files at a hardcoded absolute path:

```
D:\Program Files (x86)\Steam\steamapps\common\DarkestDungeon
```

**`data/paths.py` is the one place that defines this**, along with `CSV_DIR`
(the repo root) and `REPO_ROOT`; both are overridable with the `DDSIM_GAME_DIR`
and `DDSIM_CSV_DIR` environment variables. `env/gym_env.py` re-exports them, so
the ~33 `from env.gym_env import GAME_DIR` sites still work. Nothing in `env/`
runs without the game installed.

## Repo layout

```
train.py  eval_expedition.py   entry points — stay in root
engine/ env/ data/            the simulator
tests/                        mirrors the source areas — engine/ env/ data/
                              bridge/ tools/ training/, plus suite/ for the
                              meta-tests that police the suite itself
                              — see tests/CLAUDE.md
bridge/                        the player: reads the RUNNING game and says what
                               to do (run, overlay, live_state, town_state,
                               dungeon_map, route, lootplan, curios, camp,
                               torchlight, retreat, inventory, seed, rollout,
                               advisor, embark, embarkplan, applog, screen,
                               menus, mappanel, mapicons, curiopanel, obstacles,
                               actionbar, heropanel, townlayout, townplan)
                               — see bridge/CLAUDE.md and docs/BRIDGE.md
tools/                         diagnostics, oracles, rulers, and one search
                               — see tools/CLAUDE.md
models/                        the live checkpoints
logs/                          SB3 EvalCallback output — written by train.py
archive/{models,logs,prompts,docs,traces}/   superseded checkpoints, past
                               train/eval runs, stale docs, game debug-channel
                               captures (traces/raidsnaps..raidsnaps4 are the
                               save captures --replay reads)
captures/                      reference screenshots — not code
metadata/                      the 8 metadata CSV dirs — HEROES/ SKILLS/
                               ENEMIES/ TRINKETS/ QUIRKS/ CAMP_SKILLS/ COMPS/
                               ENEMY_COMPS/. COMPS/ splits by PROVENANCE;
                               see data/CLAUDE.md
docs/                          ASSUMPTIONS, CHANGELOG, BRIDGE, DECOMP,
                               PLAYBOOK, WIKI_MECHANICS, NEXT, COMMANDS,
                               findings/, changelog/, assumptions/
bat/                           double-click scripts Fran runs the bridge with
```

**Run artifacts do not live in the root.** `train.py`/`eval_expedition.py` are
usually invoked with stdout redirected to a `train_*.log`/`eval_*.log`; move
those to `archive/logs/` once read, and `dbwin_capture.py` output to
`archive/traces/` (a capture that motivated a `CHANGELOG.md` entry is evidence,
not scratch — keep it). The root drifted back to 115 MB of loose `.zip`/`.log`
once already.

**The CSV directories live in `metadata/`, and `CSV_DIR` is still the repo
root.** The extra level is known in exactly one place,
`data/csv_loader.py::csv_root`, which appends `metadata/` when it exists and
falls back when it does not — so an archived copy or an older checkout still
resolves. That fallback is load-bearing rather than decorative:
`CsvLoader.find_csv` returns `None` for a missing directory instead of raising,
so a wrong path would produce a silently empty store rather than an error.

## The rules that apply to every task

These are the ones that have cost real time. Everything else lives in the
`CLAUDE.md` for the directory you are touching.

**Machine safety — this took the machine down for four hours once.**

- **One suite at a time, always.** Before starting a run, confirm none is
  alive: `Get-Process python -ErrorAction SilentlyContinue`. An empty result is
  the only green light.
- **Never restart a suite because it "seems stuck".** `pytest -q` under xdist
  buffers almost everything until the end, so a 0-byte output file at 60 s is
  normal. `[gwN] node down: Not properly terminated` means you already did this.
- **Never run the suite, or heavy tools, alongside a training job.** A 12-env
  run is ~6 GB on its own and the pair already OOM'd a 3M run.
- **The failure mode is the PAGE FILE, not an OOM kill.** Windows swaps rather
  than killing anything, so nothing dies, nothing is printed, and the only
  symptom is the whole machine going treacle. Watch free physical memory:
  `(Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory/1KB`.
- **`python tools/safe_run.py -- <cmd>`** wraps a long job in five guards, each
  a refusal or a kill and none a warning.
- **A leftover mutation is a MEMORY bug before it is a red test.** An `and`
  flipped to `or` in `is_alive` means a combat never ends: the symptom is not a
  red X, it is a hang that grows. `git status` after any mutation run, and
  `git diff --ignore-all-space` to see a one-line edit hiding inside a
  whole-file line-ending flip.

**Measurement discipline.**

- **One seed is not evidence.** Between-seed variance beats the paired standard
  error here. Replicate a checkpoint A/B on two seeds or label it
  unreplicated — `--stress-weight` read +2.33 and +0.27 and had to be reported
  as unreplicated, while `--affliction-penalty` read −7.50 and −6.60 and is
  settled.
- **No engine edits mid-A/B.** Arm 2 re-imports, so a mid-run edit voids the
  comparison. Stage the patch instead.
- **A checkpoint is only comparable in the world it trained in.** Widening the
  observation is the loud break — nothing loads. **Changing what an existing
  feature CONTAINS is the silent one**: the width still matches and the model
  is fed numbers it never saw.
- **A checkpoint is only loadable at its own observation width**, and that width
  is a property of the loaded DATA as well as of the code. See
  `tests/suite/test_obs_width_tripwire.py`.
- **Two instruments disagreeing when they should not is a bug signal, never
  noise.** That is what exposed both of one night's measurement bugs.
- **A delta smaller than twice the standard error is the instrument saying it
  cannot tell.** Quote the standard error with the rate, always.
- **Check the verdict in `docs/findings/INDEX.md` before re-running an
  experiment.** Cross-class transfer is closed after five mechanisms and five
  nulls; do not open a sixth without a mechanism nobody has tried.

**Editing this repo.**

- **This repo's line endings are MIXED, and a patch script must match the
  committed style per file.** `pathlib.write_text` defaults to `os.linesep`, so
  on Windows it silently rewrites a whole LF file to CRLF and the diff becomes
  unreadable; forcing `newline="\n"` everywhere does the same in reverse. Read
  the committed style with `git show HEAD:<path>` and write to match.
- **When you fix a fidelity bug, record the evidence in `docs/CHANGELOG.md`.**
  Most entries there exist to stop a correct fix being reverted by someone who
  didn't know why it was made.
- **A test can stop running without anything going red.** That is this repo's
  characteristic failure, and `tests/suite/` exists to catch it. Do not weaken
  those guards to make a change land.

## Commands

The full list is `docs/COMMANDS.md`. The ones needed constantly:

```bash
pip install -r requirements.txt

# Tests. `pytest.ini` adds `-n 8 --dist loadgroup`: ~2,830 tests, ~55 s,
# ~9.7 GB peak. Test files are also runnable as plain scripts.
python -m pytest tests/ -v
python -m pytest tests/engine/test_combat.py -v
python -m pytest tests/bridge/test_run.py -n0        # -n0 to debug
python tests/data/test_loader.py                     # the script path
DDSIM_NO_STORE_CACHE=1 python -m pytest tests/       # bypass the store cache

# Training. THE RECIPE THE SHIPPED CHECKPOINT WAS TRAINED WITH.
python train.py --full-expedition --timesteps 12000000 --n-envs 12 --seed 0 \
    --hard-focus --no-abandon-train --batch-size 256 --lr-schedule linear \
    --out models/<name>.zip
python train.py --check                              # gymnasium API compliance

# THE eval to run: zero comp-sampling error, and reproducible since 2026-08-24.
python eval_expedition.py --model <ckpt> --full-expedition --all-comps --episodes 4

# The bridge, against the running game.
python bridge/run.py --model models/dd_combat_ppo_full_expedition_mods.zip --survival 12
python bridge/run.py --replay archive/traces/raidsnaps2   # validate offline

# Did the docs lose anything?
python tools/doc_migration_audit.py
```

**A short run WITHOUT `--smoke` overwrites the live checkpoint AND
`logs/<mode>/best_model.zip`.** `models/` is gitignored, so git cannot restore
it. This already cost one checkpoint. Use `--smoke` or `--out PATH`.

## Design rules

- CSVs are metadata-only. Never treat a CSV value as combat-authoritative —
  trace back to the game install source (`data/CLAUDE.md` has the table).
- No premature optimization; correctness and readability first.
- Keep `SHAPING_GAMMA` in sync with PPO's `gamma`, and don't add ad-hoc reward
  components outside `Φ` without re-checking the invariance argument in
  `docs/ASSUMPTIONS.md`.
- When you fix a fidelity bug, record the evidence in `docs/CHANGELOG.md`.
