# Findings — the lab notebook, by subject

Everything here was measured. This index exists so the **verdict** is one hop
away instead of buried in a 3,878-line file, because the expensive mistake in
this project is re-running an experiment that has already been settled in the
negative.

**Read the verdict before proposing the work.** Three of the entries below are
settled *against*, two are retracted, and one is closed after five separate
attempts.

| verdict | meaning |
|---|---|
| **shipped** | measured, it paid, it is in the default path |
| **null** | measured, no effect beyond noise; the flag usually still exists, defaulted off |
| **harmful** | measured, it made things *worse*, replicated on two seeds |
| **unreplicated** | the effect moved one seed and not the other — not evidence |
| **retracted** | it was claimed here and then refuted, usually by controlling something |
| **closed** | asked and answered; do not reopen without a new mechanism |
| **open** | real question, no answer yet |

## Training

[`training-recipe.md`](training-recipe.md)

| finding | date | verdict |
|---|---|---|
| `--lr-schedule linear` — the biggest single-flag win here | 2026-08-29 | **shipped** |
| `--batch-size 256` — the enabler; an update costs 5.3 s not 12.9 s | 2026-08-28 | **shipped** |
| 12M is the budget; the "6M knee" was the untuned optimiser, not the data | 2026-08-29 | **shipped** |
| 24M — SUCCESS +1.0, QUALITY −0.8, pointing opposite ways | 2026-08-29 | **closed** (do not pay) |
| `--net-arch 512,512` — this file's own argument predicted a win | 2026-08-29 | **harmful** (−4.1 / −3.0) |
| `--n-epochs 5` | 2026-08-29 | **harmful** |
| The distribution bundle (loadout + kit + class-balance at 0.5/0.5/1.0) | 2026-09-05 | **harmful**, two seeds |
| The five distribution knobs, wired and defaulted off | 2026-09-04 | **open** — no arm run |

## The reward

[`reward-knobs.md`](reward-knobs.md)

| finding | date | verdict |
|---|---|---|
| `--prevention-reward` — both terms sit inside Φ, so they can only move credit timing | 2026-08-27 | **null**, two seeds |
| `--stress-weight` — stress falls ~3.9 points and quality does not move | 2026-09-?? | **unreplicated** |
| `--affliction-penalty 20` — it prices something the policy cannot prevent | 2026-09-03 | **harmful**, two seeds |
| The death:stress ratio is one constant where the game's spans 1000× | 2026-09-03 | **open** — no constant changed |
| Backdraft applied nothing; its whole payload was unparsed keys | 2026-08-27 | **shipped** (fidelity) |

## Transfer to unseen classes

[`transfer.md`](transfer.md) · [`modded-classes.md`](modded-classes.md)

| finding | date | verdict |
|---|---|---|
| Zero-shot cross-class transfer, after **five** mechanisms | 2026-09-04 | **closed** — do not open a sixth |
| Few-shot: 600k steps recovered +12.7pp on an unseen class | 2026-08-28 | **shipped** — this is the answer |
| A modded class loads with its kit (`DDSIM_MOD_DIRS`) | 2026-08-27 | **shipped** |
| 50 Workshop classes: 48 load, 29 pass the visibility gate, 27 have a corpus | 2026-09-04 | **shipped** |

## Checkpoints

[`checkpoints.md`](checkpoints.md)

| finding | date | verdict |
|---|---|---|
| The shipped vanilla checkpoint, and why its table is a new baseline not a regression | 2026-09-03 | **shipped** |
| The modded checkpoint: ~1pp of vanilla for ~3pp on modded comps | 2026-09-05 | **shipped** |
| The eval scores an EASIER world than training; `--underleveled` closes it | 2026-09-03 | **open** |
| The corpus bought survival, not style | 2026-09-05 | **null** (style unchanged) |

## The comp layer

[`comp-search.md`](comp-search.md)

| finding | date | verdict |
|---|---|---|
| The build order — ranks, then skills, then trinkets — is a dependency | 2026-08-26 | **shipped** |
| Sustain has two routes, and the search knew one | 2026-08-26 | **shipped** |
| The Vestal is a **convergence artefact**, not a fidelity bug; `--ban-class` is the answer | 2026-08-26 | **closed** |
| Region-pinning does not make skill choice measurable | 2026-08-26 | **retracted** |
| `comp_leaderboard`'s curated-vs-random gap is confounded by familiarity | 2026-08-26 | **open** |

## The rulers

[`rulers.md`](rulers.md) — and the rule that **none of them may become a training target** (`tools/CLAUDE.md`).

| finding | date | verdict |
|---|---|---|
| The threat forecast is the first observability change the policy demonstrably uses | 2026-08-25 | **shipped** (McNemar p=0.0132) |
| "Simulator fights are half as long as real ones" | 2026-09-03 | **retracted** same day |
| `style_probe`: skill choice degrades below random as sustain rises | 2026-08-30 | **retracted** |
| Stress is a ratchet, and the simulator disagrees about the way down | 2026-09-03 | **open**, directional only (n=8) |
| Endless mode: median 22 battles before a death; SUCCESS measures the flat top | 2026-09-03 | **open** |
| 13 skills are never taken by EITHER seed, with 300-1,100 offers each | 2026-09-07 | **shipped** (replicated) |
| Skill pick-rate-when-offered replicates across seeds, rho=0.84 over 235 skills | 2026-09-07 | **shipped** |
| The class leaderboard replicates only where a class has comps: rho 0.65 all 54, 0.89 at n>=10 | 2026-09-07 | **shipped** |
| The expert tier list barely predicts what the policy plays | 2026-09-07 | **open**, C-tier is the only clear signal |
| `agreement.py`'s combat rate is disqualified — the corpus is bridge testing | 2026-08-23 | **closed** |

## Driving the real game

[`bridge-live.md`](bridge-live.md) — see also `docs/BRIDGE.md` and `bridge/CLAUDE.md`.

| finding | date | verdict |
|---|---|---|
| The deaths were the STAY/LEAVE layer, not the combat policy | 2026-09-03 | **shipped** (three bugs fixed) |
| `scale_floor_by_investment` — the floor is a constant too | 2026-09-03 | **open**, default off, Fran's call |

## Performance

[`performance.md`](performance.md)

| finding | date | verdict |
|---|---|---|
| `ExpeditionSequenceEnv` built the observation twice per step, and the base copy was stale | 2026-08-27 | **shipped** (1.58×) |
| `tools/dson.py::parse_bytes` was quadratic | 2026-08-27 | **shipped** (1.8×) |
| `deterministic_actor` replaces `model.predict` on the rollout path | 2026-08-27 | **shipped** (3.4×) |
| `--device cpu` by default — the GPU is 13% **slower** here | 2026-08-27 | **shipped** |
