# Findings: where the time goes

The rollout loop is the cost. Measured, not guessed, and every number
below is pinned by a test. Moved verbatim from the root `CLAUDE.md` on
2026-09-07.


### The rollout loop is the cost, and four things were wasting it (2026-08-27)

Measured, not guessed. A rollout step was **1,100 us**: 736 us of env and 331 us
of `model.predict`. It is now **~560 us**, and every number below is an
equivalence, not an approximation — `tests/suite/test_hot_paths.py` pins each one.

- **`_get_obs` is 76% of an env step** (279 us of 736, the threat forecast 119
  of that), and `ExpeditionSequenceEnv` was **building it twice per step**. Its
  `step` calls `super().step()`, then mutates the world (combat transition,
  roster snapshot, a retreat re-entering the room) and returns its own
  `_get_obs()`. The base's copy was not merely redundant, it was **stale** in
  exactly those cases. `_BASE_STEP_RETURNS_OBS = False` says "I always rebuild";
  `step` then returns a shared zero vector. **1.58x**, byte-identical
  trajectories. A subclass may only set it if it overrides `step` — otherwise
  callers silently receive zeros of the right shape and dtype.
- **`tools/dson.py::parse_bytes` was quadratic.** The child-owner lookup was a
  linear scan of `meta1` inside a loop over every child of every object: 34% of
  a parse. A dict, built with **`setdefault` so first still wins** (`next()`
  returned the first match; last-wins would be a silent behaviour change),
  gives **1.8x** with identical trees over all 817 captures. This is on the
  bridge's poll loop, not only in tests.
- **`eval_expedition.deterministic_actor` replaces `model.predict`** on the
  rollout path: 331 us -> 92 us, **3.4x**, exact because `MaskableCategorical`
  masks by `where(mask, logits, -1e8)` and `deterministic=True` is
  `probs.argmax(-1)`, and softmax is monotone. It **falls back** for anything
  that is not a plain `MaskableActorCriticPolicy` over a float32 `Box`, so
  `PlaybookPolicy` still works. `tools/comp_ga.py` and
  `tools/comp_leaderboard.py` are pure rollout, so this is ~1.9x on a search.
- **`--device` exists and defaults to `cpu`, because the GPU is 13% SLOWER**
  here — 63.4 s against 71.6 s over four `--n-envs 4 --timesteps 40960` runs,
  even though an isolated benchmark of the same net says the GPU should win
  2.6x. Real PPO ships every minibatch across PCIe and `.item()`s `approx_kl`
  and `clip_fraction` on each of 1,280 gradient steps per update; the syncs cost
  more than the matmuls. **Two arms of an A/B must match on `--device`**, like
  `--n-envs`. It also fixes a silent bug: `--resume` used SB3's `device="auto"`
  (CUDA here) while a fresh model got `"cpu"`.

**The suite is 45 s and `pytest.ini` now defaults to `-n 8`, not `-n auto`.**
Eight workers cost 2.5 s and save **2.8 GB** (7.84 -> 5.01 GB peak); sixteen no
longer have enough work to fill them. Two test-side fixes got it there:
`inert_trinkets` is memoised (it was called per curated trinket slot — 28 s, two
thirds of the old wall clock) and `test_suite_memory.py` caches the derived
import set rather than re-parsing the repo per test file.

**A test can stop running without anything going red, and two ways of that were
live here.** `tools/find_silent_tests.py` finds tests that assert nothing; it
does not find these. `tests/suite/test_suite_hygiene.py` does, statically:

- **A duplicate function name shadows the earlier `def`.** `test_comp_ga.py` had
  a 112-line block pasted twice and **eight tests had silently stopped running**.
- **A `globals()`-style `__main__` runner only sees what is defined above it.**
  Four files had tests below theirs, so `python tests/engine/test_gaps.py` ran 15 fewer
  tests than pytest did — 38 across four files. The script path is documented
  and is why `tests/_fixtures.py` is a module and not a `conftest.py`.

**This repo's line endings are MIXED, and a patch script must match the committed
style per file.** `pathlib.write_text` defaults to `os.linesep`, so on Windows it
silently rewrites a whole LF file to CRLF and the diff becomes unreadable; forcing
`newline="
"` everywhere does the same thing in reverse to the CRLF half. That
cost two clean-up passes in one session (17 files one way, then 10 the other).
Read the committed blob's style with `git show HEAD:<path>` and write to match.

**Never import torch or sb3 at a test module's top level, and never run the suite
alongside a training job.** Both cost a crashed machine on 2026-08-27. Every xdist
worker collects the ENTIRE suite, so distribution decides what a worker *runs* and
has no say in what it *imports*: one module-level `from sb3_contrib import ...` is
paid by all sixteen workers before a test executes. Torch is **454 MB per worker**
against the `DataStore`'s 26 MB — the obvious suspect is seventeen times too small
to be the problem. `--dist loadfile`/`loadgroup` do **nothing** for this on their
own. The four production lazy-import sites (`train.py`, `eval_expedition.py`,
`tools/ab_compare.py`, `tools/ablate_forecast.py`) are pinned by
`tests/suite/test_suite_memory.py`, which is a static import-graph walk and therefore
free to run.

**An `xdist_group` mark goes on the TEST for torch and on the MODULE for memory,
and never the other way round.** A module-level `pytestmark` serialises every
test in the file onto one worker: `test_live_bridge.py` is 68 s of capture
decoding with exactly ONE test that touches torch, and pinning the file made
those 68 s the critical path of a 97 s suite. Exactly **six tests in 1820**
import torch/sb3 in their bodies and each carries its own mark. The module-level
marks belong to the four files whose tests *spike* — `test_pe_anchors` +416 MB,
`test_screen` +172, `test_mappanel` +92, `test_curiopanel` +21 — because the
suite's peak is transient (a serial run ends at 648 MB, 466 of it the one torch
test), and scattered spikes are what land sixteen at a time.
`tests/suite/test_suite_memory.py` enumerates both sets with the measured spike.

**A module-scoped fixture holding a stateful RNG is an order-dependent test.**
Under `--dist load` each worker runs a different subset in a different order, so
a shared env's `_sampler_rng` is in a different state — that is one failure in
three for `test_fleeing_buys_no_recovery`, passing every time in isolation. All
four env fixtures are function-scoped; construction is **0.3 ms** against a
cached store, so module scope bought nothing. Watch the second-order effect too:
making the draw deterministic turned a *conditional* `pytest.skip` into a
permanent one, which is worse than a flake. Pin the scenario and assert the
premise instead.

```bash
python train.py --check              # gymnasium API compliance check

# Writing a test that drives an episode with `env.action_space.sample()`? Seed the
# SPACE as well as the env: `reset(seed=)` reaches the engine RNG only, and gymnasium
# draws an unseeded action space from OS entropy, so the test replays a DIFFERENT
# episode every run. That cost a full-suite failure that looked exactly like
# cross-test pollution. See CHANGELOG.md.

# Training. --resume continues an existing model; NOT valid across a reward change
# (a model trained under a different reward must be retrained from scratch).
python train.py --timesteps 200000                          # fixed 2xCrusader vs 2xCultist-Brawler
python train.py --expedition --timesteps 500000             # randomized real comps x zone-matched enemies
python train.py --full-expedition --timesteps 3000000       # one episode = a full 8-18 combat expedition
python train.py --play                                      # watch one episode (add --expedition/--full-expedition)
python train.py --expedition --timesteps 500000 --seed 0    # --seed fixes the run; --resume continues a checkpoint

# --n-envs is SubprocVecEnv width (default 8; 16 cores here). Rollout collection went
# 766 -> 5226 fps, but SGD is then ~73% of wall time, so end-to-end is only ~1.5x.
# Each worker gets sampler_seed+i — one seed everywhere would give N copies of ONE
# episode and a NARROWER distribution than the single env it replaced.
python train.py --full-expedition --timesteps 3000000 --n-envs 12 --seed 0

# THE RECIPE THE SHIPPED CHECKPOINT WAS TRAINED WITH. `--lr-schedule linear`
# is worth +1.5/+4.5 long SUCCESS over a matched constant-LR arm on two seeds,
# and it also RESTORES the budget: 6M -> 12M is worth +0.3 QUALITY at constant
# LR and +1.9 under the schedule, so the old "6M knee" was the optimiser, not
# the data. batch 256 is what makes 12M cost ~2.2 h at ~1500 fps.
python train.py --full-expedition --timesteps 12000000 --n-envs 12 --seed 0 \
    --hard-focus --no-abandon-train --batch-size 256 --lr-schedule linear \
    --out models/<name>.zip
# `--net-arch 512,512` and `--n-epochs 5` were both MEASURED AND REJECTED
# on 2026-08-29 -- see the shipped-checkpoint section. Do not retry either
# without a new argument; the input-ratio one has already been falsified.
# THE OLD ROAD only: the shipped 2-hero tutorial party (Crusader/Highwayman at
# resolve 0, rank-0 gear, their real 4 skills each, 10 stress) against its two
# scripted fights at torch 100. Nothing is sampled, so this can only get better
# at ONE fight pair -- which is why it writes its own checkpoint. `--resume`
# fine-tunes: copy a general checkpoint onto models/dd_combat_ppo_tutorial.zip
# first and it starts from that instead of from scratch (same 589-wide obs).
python train.py --tutorial --timesteps 500000 --n-envs 8 --seed 0

python train.py --expedition --timesteps 2000 --smoke        # throwaway: writes models/_smoke_*.zip
                                                             # and logs/_smoke_*/, never the live ones
# A short run WITHOUT --smoke overwrites the live checkpoint AND logs/<mode>/best_model.zip.
# models/ is gitignored, so git cannot restore it. This already cost one checkpoint.
# --out PATH is the other way to redirect.

# Eval. Fix --comp-seed to compare runs against each other.
python eval_expedition.py --model models/dd_combat_ppo_expedition.zip --episodes 100 --comps 5 --comp-seed 0
python eval_expedition.py --model models/dd_combat_ppo_full_expedition.zip --full-expedition --episodes 100
# THE eval to run. Same 1,824 episodes as `--comps 12 --episodes 50`, zero
# comp-sampling error instead of +-4.7pp on the long band, and REPRODUCIBLE
# since 2026-08-24 (see below -- it was not before). `--comps`/`--comp-seed`
# are ignored under it.
python eval_expedition.py --model <ckpt> --full-expedition --all-comps --episodes 4
```

**Every `--all-comps` delta measured before 2026-08-24 carries ~2pp of invisible
run-to-run error, and must not be re-quoted.** `reset(seed=)` reaches the ENGINE rng
only; the torch level, which monsters are drawn and whether a quest is a boss quest all
come from `_sampler_rng`, which was built once in `__init__` and never reseeded — and
the eval constructs its env with `sampler_seed=None`. Two runs of one command on one
checkpoint gave **short 91.8% then 93.8%**. `--all-comps` removed comp *sampling* error
and left scenario *seeding* untouched, so it read as the trustworthy ruler while being
no more reproducible than the flag it replaced. Fixed by a `sampler_seed` reset option
filled from each cell's `_cell_scenario` hash; two runs are now bit-for-bit identical.
The option is **opt-in, never derived from `seed`** — each `SubprocVecEnv` worker gets
`sampler_seed + i` precisely so the workers face different scenarios, and deriving it
would collapse them into N copies of one episode. `tests/training/test_eval_reproducible.py`.

**Read QUALITY, not just SUCCESS.** SUCCESS (finished every combat, nobody died) is a
*gate*: binary, silent about the state the party is in, and saturated — the shipped
checkpoint reads 94.4% short and 90.1% medium, so it cannot resolve an improvement
there. `_run_quality` scores each run 0-100 over the **roster** (per hero: 0 if dead,
else half HP and half stress), and the band line prints the three components beside it
because the weights are a declared convention. Two rules: it averages over the roster
rather than the survivors, so a policy cannot raise its score by letting the most
stressed hero die; and it scores stress against the **200 ceiling, not the 100 Resolve
Check**, because the reward's own `combat_end_condition` clamps at 100 and a ruler must
not go blind where the runs it grades end up.

```
  short: succ 94.4%  qual 86.6   (stress 13.9  hp 81.9%  deaths 0.06)
 medium: succ 90.1%  qual 83.0   (stress 17.6  hp 77.4%  deaths 0.19)
   long: succ 76.0%  qual 71.2   (stress 35.2  hp 65.1%  deaths 0.51)
```

**Measure BOTH saves — `logs/full_expedition/best_model.zip` and the final-step save in
`models/` — and pick on the eval, never on which one SB3 called "best".** The eval-reward
curve is noise with an envelope, not a curve with a plateau (a 3M run peaked at 103.97 and
ended at 30.91; the 6M bundle run peaked at 98.12 at **1.07M steps** and ended at 48.92), so
`best_model` is pinned wherever the noise spiked and the final save lands wherever it landed.
The spread between the two is ~3pp, larger than any fidelity fix ever measured here, and it
does **not** favour one side consistently: a 3M run gave 64.3% (final) vs 67.4% (best), while
the 6M bundle run gave **66.7% (final) vs 64.2% (best)** on the same radiant-pinned eval, and
the 6M retreat run gave **67.1% (final) vs 65.4% (best)** on the control column. Two of three
went to the final save. See `CHANGELOG.md`.

**Those historical best-vs-final numbers were read off a broken ruler, and should not be
re-quoted as evidence.** `eval_expedition.py --full-expedition` builds its env with
`sampler_seed=None` and never reseeds `_sampler_rng` per episode, so **two runs of one
command on one checkpoint face different monsters** — measured at 41.7% vs 33.3% back to
back. The ~3pp "spread between the saves" is inside that. Use `tools/ab_compare.py`, which
reseeds per scenario and pairs the two arms on the same dungeon list; its `--self-check`
proves the harness is deterministic before you believe any delta it prints.

**SETTLED on 2026-08-24, and the answer is that it does not matter.** Measured on
the shipped checkpoint with `--all-comps --episodes 4` — **456 paired cells**, the
same scenarios in both runs because `_cell_scenario` hashes `comp|band|episode`:

| band | final (shipped) | best_model | paired delta | 95% CI |
|---|---|---|---|---|
| short | 81.9% | 82.1% | +0.2pp | [−3.5, +3.8] |
| medium | 75.3% | 77.8% | +2.5pp | [−1.3, +6.6] |
| long | 54.4% | 53.3% | −1.2pp | [−5.1, +3.0] |
| **overall** | **70.6%** | **71.1%** | **+0.5pp** | **[−1.8, +2.8]** |

Every interval spans zero and the bands disagree in direction. **The two saves
are indistinguishable**, so pick either and spend the effort elsewhere. The
historical ±9pp swings below were the unpaired eval's own noise, exactly as the
2026-08-20 retraction suspected — a properly paired comparison makes the whole
question evaporate rather than answering it. Everything from here to the end of
this section is kept for the METHOD, not for its numbers.

**That claim was retracted on 2026-08-20 — the sign flipped on a rerun.** The same 216-scenario comparison on the burn/loadout retrain gives `best_model` **+9.3pp on SUCCESS** (p=0.0078), the exact opposite of the table below, while the final save wins every survival metric. Read both, and read the retraction under "The retrain" in `CHANGELOG.md` before treating either as settled. The table is kept because the *method* is right and the numbers were real for that pair of saves.

**Paired, the question appeared to resolve — and it resolved against `best_model`.** 216
scenarios (`--episodes 6 --comps 12 --comp-seed 1`), final save vs `best_model.zip`:

| metric | best_model | final | delta | 95% CI | p |
|---|---|---|---|---|---|
| SUCCESS | 29.2% | **37.5%** | **+8.3pp** | [+1.9, +14.8] | 0.020 |
| completed | 31.0% | **40.3%** | +9.3pp | [+2.3, +15.7] | 0.011 |
| retreats/exp | 2.31 | **1.84** | -0.46 | [-0.76, -0.17] | — |
| final stress | 77.8 | **68.7** | -9.1 | [-15.7, -2.5] | — |
| reward | 37.5 | **45.1** | +7.6 | [+2.2, +13.2] | — |

162 of 216 scenarios tied; the signal is in 54 discordant pairs, which is exactly the
sensitivity an unpaired comparison throws away.

**But that is one ruler, and the second one points the other way.** `tools/agreement.py`
grades a checkpoint on **real captured positions** instead of simulated ones — how often
it advises the move that was actually played — and over 101 combat decisions
`best_model` agrees **+11.9pp more often** than the final save (47.5% vs 35.6%,
CI [-23.8, +1.0], McNemar 27/15, p=0.088). Not significant, and **agreement is not
correctness** — a DIFFER can be the policy outplaying the human. But it means the
"prefer the final save" call above rests on the simulator alone and is **not settled**.
Run both tools before picking a checkpoint, and say which ruler you used. See
`CHANGELOG.md`.

Note how the interval behaved: at 72 scenarios the same comparison gave +9.7pp
CI [-2.8, +22.2] (not distinguishable); at 216 it gave +8.3pp CI [+1.9, +14.8]. Same point
estimate, tighter interval — the instrument converging rather than changing its mind.
±2pp would still need ~2300 paired scenarios, which is the real reason "training longer"
never resolved anything: **no eval this project ever ran was large enough to see the effect
it was looking for.**

**RESOLVED 2026-08-28, and the diagnosis above was exactly right.** `--all-comps`
became reproducible on 2026-08-24 and carries an SE near 1.0 on QUALITY over 169
comps. On that instrument, 3M -> 6M is worth **+2.4 SUCCESS and +3.8 QUALITY on
the long band** -- an effect that was always there and was invisible to the
+-3pp ruler that preceded it. "Training longer buys noise" is retired as a
standing claim; it buys about four points of long-band quality, and
`--batch-size 256` is what makes it affordable (an update costs 5.3 s instead of
12.9 s, so 6M runs in the wall clock of the old 3M).

**Since retreat exists, `SUCCESS` is not comparable to an archived eval** — it requires
`retreats == 0`, so it fell 66.7% -> 36.0% on a model that plays at parity. Diff the
`old-succ` control column instead (end reached alive, fights skipped or not), and read the
`fled` and `deaths` columns for what actually changed. `old-succ` is a control, never a
headline: fleeing every fight would score 100% on it.

**Comp sampling, not episode count, is what the error bar is made of.** Measured
2026-08-23 over 12 comps x 3 bands x 50 episodes: comp-to-comp SD is 7.3pp short,
12.1 medium and **16.2 long**, so a 12-comp eval buys +-2.1 / +-3.5 / **+-4.7pp**
and individual comps run 30% to 88% on the long band. Adding episodes does not
touch that; only adding comps does, and +-2pp would need ~252 of ~152 that exist.
**Use `--all-comps --episodes 4`** — same total episodes, no sampling error —
and quote the per-band SE the summary prints.

**The historical baselines were measured with `--episodes 50 --comps 12 --comp-seed 1`, not
with the line above.** `eval_traits_3M.log` — the 64.4% recent decisions are anchored to —
has 36 cells; `--comps 5` gives 15, and `--comp-seed 0` selects twelve *entirely different*
comps. Use those flags for anything meant to compare against an archived eval.

Full-expedition eval headline is **SUCCESS = completed every combat AND zero deaths**. It also breaks out completed/zero_death/wipe/stall separately: a zero-death episode that *stalled out* on the step budget is not a success, and conflating the two badly inflated this table historically.
