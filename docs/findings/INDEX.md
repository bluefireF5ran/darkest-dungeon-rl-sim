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

## The fact audit

[`fact-audit.md`](fact-audit.md)

| finding | date | verdict |
|---|---|---|
| The provisions table is a community guide image, not Fran's own answers | 2026-09-23 | **open**: Fran to rule |
| Stress was priced in gold; only survival should count it | 2026-09-23 | **shipped** (fixed) |
| The survival forecast runs on 20 episodes and was never checked against the game | 2026-09-23 | **open** |

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
| `--boss-quest-p 0.25` (boss exposure, 12M) — wins the N2 rulers, loses the campaign | 2026-10-10 | **not promoted**; one training seed |

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

| PLAYBOOK 3.2 on the shortlist | **shipped** | the bridge demotes an unjustified heal; 3 of 56 combat decisions on the corpus, `--no-playbook-heals` to compare |
| PLAYBOOK 3.3 and CRP | **open** | the ruler was missing 3.3's STALL gate and scored correct attacks as violations; re-measured **44.6%** recover (was 37.3). CRP is 12 turns of hitting a corpse with a kill in reach |

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

## The campaign simulator

[`../CAMPAIGN_SIM.md`](../CAMPAIGN_SIM.md) (F2 log; 80 campaigns x 60 weeks per arm, paired, two seed blocks)

| finding | date | verdict |
|---|---|---|
| Gear is the lever (ablation), then trinkets; level, kit, stress, quirks are not | 2026-10-10 | **settled** |
| Gold sweep: gear 0.5 of the gold above a Long basket; flat above it | 2026-10-10 | **experiment knob**, off by default (breaks the 10% rule) |
| The 10% skill-unlock cap beats 25 / 50 / 100% | 2026-10-10 | **Fran's rule confirmed** |
| `BOSS_LEVEL_MARGIN 1`: -13 dead, +8 pp success, -2.2 plots | 2026-10-10 | **open**, default 0, Fran's call |
| `KIT_AWARE` (a seat's missing kit counts) | 2026-10-10 | **null**, kept on |
| Service discounts capped at rank a (PLAYBOOK 4.1); Guild/Blacksmith before the Coach | 2026-10-11 | **null**, both off: gear is short of gold, not of building ranks |
| Income calibrated on Fran's recorded raids: curio draws were read as weights (live bug) and the sim's curios were bare-handed; pack 0.58x -> 0.97x his | 2026-10-11 | **shipped** (e96ad44, 10544a2); draw fix alone -5.5 dead, +0.8 plots |
| Calibrated economy (v3): the basket used is -10.7 dead more; gear 0.5 -> 1.0 now null; 107k unspent at week 60 | 2026-10-11 | **shipped**; gold is no longer the constraint |
| `BOSS_LEVEL_MARGIN 1` on v3: -5.2 dead, +4.5 pp success, -3.2 plots | 2026-10-11 | **open**, default 0, Fran's call |
| Early raids vs Fran's: a hero resting on Death's Door rolled a deathblow every turn (engine bug); campaign -4.9 dead; what remains is hits taken while down | 2026-10-11 | **shipped** (7fe6ddb); the play gap is **open** |
| Quest-end quirks and diseases (v5): combat null on both blocks; gold -26k at week 60, replicated, nearly all of it nurture spend | 2026-10-11 | **shipped** (bce058e); Sanitarium rules **open** (Fran) |
| The seated party under the level rule (v6): Veteran raids with 2+ under-level heroes were 46% of deaths; success +5 / +4 pp replicated, dead -4.3 / -3.5 | 2026-10-11 | **shipped** (386d461) |
| Step down a difficulty when no curated comp seats it at level (v7): dead +3.3 / +3.1, success -2 / -4 pp | 2026-10-11 | **harmful**, reverted (490bdee) |
| With no comp at level, keep the comps seating the fewest under-level heroes (v8): dead -8.4 / -7.3, success +5 / +4 pp, wipes -2.1 / -1.7 | 2026-10-11 | **shipped** (aba4c78) |
| `--max-underleveled 0` (no under-level hero): dead -0.7 / -2.9, success +1 / +2 pp, all under 2 SE | 2026-10-11 | **null**; Fran's 1 stays |
| `--smith-first` on v8 (rich world): dead null, gear gold +11-12k, plots -1.2 / -0.6, first Champion -2 / -7 weeks | 2026-10-11 | **null** on deaths; a trade for Fran |
| Boss margin 1 on v8: dead -2.8 / -5.5, success +4 / +3 pp, plots 5.4 -> 1.5 | 2026-10-11 | **open** (Fran's item 1); default 0 |
| `--max-gap 1` (the one under-level hero at most a level short): dead -0.5 / +0.4; a first run's +12.8 was a sort-key bug (a8b3dc6) | 2026-10-11 | **null** |
| E19 smith first + the vanilla roster managed (`v9`, Fran's ruling): dead +2.0 / +0.7, bosses -0.8 / +0.2, roster_end unchanged | 2026-10-12 | **shipped** (ruling); measured null |
| E20 boss parties at the highest level the quest allows, fully equipped (`v10`): dead -0.8 / -2.9 (2 SE on one block), wipes -0.0 / -0.7 | 2026-10-12 | **shipped** (ruling); unreplicated, favourable |
| E21 Sanitarium option 4 (`v11`): diseases 8.4 -> 0.5, negatives -12, 81k gold; dead +1.2 / +2.0 (null); first Veteran +4.9 / +2.5 weeks | 2026-10-12 | **shipped** (ruling); null on deaths, a replicated delay |
| E22 Death's Door trained (`--dd-exposure-penalty 10`), s0, 1,200 paired runs: deaths per entry 0.140 -> 0.164 (wrong way, under 2 SE); entries -2.0 SE; SUCCESS +0.7 pp (null). s1's penalty arm died at 11.2M of 12M steps (the machine ran out of processes) | 2026-10-14 | **null** (pre-registered falsifier failed on s0); not promoted. The penalty taught avoiding the Door, not surviving on it |
| E23 building gates (`v12`, a world step): dead -4.5 / +0.3 (unreplicated), success +2 / +1 pp, first Veteran -2.2 / -2.0 weeks | 2026-10-14 | **shipped** (fidelity, 0efbaa9 / c926245); costs nothing measurable |

## Performance

[`performance.md`](performance.md)

| finding | date | verdict |
|---|---|---|
| `ExpeditionSequenceEnv` built the observation twice per step, and the base copy was stale | 2026-08-27 | **shipped** (1.58×) |
| `tools/dson.py::parse_bytes` was quadratic | 2026-08-27 | **shipped** (1.8×) |
| `deterministic_actor` replaces `model.predict` on the rollout path | 2026-08-27 | **shipped** (3.4×) |
| `--device cpu` by default — the GPU is 13% **slower** here | 2026-08-27 | **shipped** |
