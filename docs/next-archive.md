# NEXT: closed and superseded items

Moved verbatim out of `docs/NEXT.md` on 2026-09-07. Every section here was
marked closed by the queue itself -- SUPERSEDED, RETRACTED, RESOLVED,
ANSWERED, PHASE CLOSED, CLOSED or SETTLED. Several are kept explicitly for
their DIAGNOSIS rather than their status, which is why they are archived
and not deleted.

## Superseded: THE LIVE BRIDGE IS BLOCKED ON HARDWARE, NOT ON CODE (2026-09-03, evening)

**Read this before planning any bridge work.** The pixel layers were unblocked
this session and then blocked again by the machine, not by the repo.

The black GDI capture that stopped two previous sessions was **never a missing
display**: a borderless window covering a whole monitor takes DWM's
independent-flip path and never reaches the surface GDI reads. `-fullscreen 0`
fixes it, `-size` is ignored, and **Steam must be running or the game writes no
saves at all**. With that understood the bridge played a full tutorial raid end
to end — 82 actions, and the first 24 combat `decision` events ever recorded.

Then the display configuration collapsed mid-session: the virtual desktop went
from **3840x1080 across two monitors to 1280x720 across one** and did not come
back. Everything downstream degrades SILENTLY — the window size wanders, the
game renders cropped, nothing recognises, and the map finds 8 cells where a fit
needs a dozen. `tools/overnight.py` now refuses up front when the window fills
the desktop, so a night is no longer spent discovering it one lost raid at a
time.

**Unblocking it needs a physical display, a dummy plug, or a virtual display
driver — a call for Fran, not something to install unasked.** Until then:
everything save-driven still runs (`live_state`, `town_state`, `embarkplan`,
`retreat`, `rollout`, `run.py --replay`), as do training, evals and the comp
search. The full account is in `CHANGELOG.md`; the operational version is in
memory under `dd-no-desktop-in-agent-session`.

**What the bridge gained while it was up**, all committed and tested: save-list
scrolling (so a slot below the fold is reachable), `--resume` to play an
existing campaign, the roster forced to a known position when duplicate classes
make it unreadable, a corridor door that judges where it landed, `town_event`
handled, the zone intro plate banked, and the map click re-measured on the frame
it will actually land on.

**One thing is banked and still open**: `bridge/screen.panel()` is an
inventory-vs-map discriminator with no "neither", so it answers `"map"` for a
title screen — which is how the router came to report a map-fit failure for a
dungeon that was not on screen. The cheap gates do not work (a title fade's
strip spread measures 4.99 against a real map's 1.78). A positive map test is
the real fix and it can be developed **offline against the 71 banked captures**,
which makes it the best bridge work available with no display.

---

## RETRACTED THE SAME DAY: simulator fights are NOT half as long (2026-09-03)

**Do not re-derive this.** The claim was real play 3.9 rounds against the
simulator's 2.0, read as the structural explanation for the policy skipping
buffs, control and stress heals. Controlled, it dissolves:

| arm | combats | mean rounds |
|---|---|---|
| sampled tier and band (the 2.0) | 357 | 1.92 [1.8, 2.0] |
| tier 1 / short / **4 heroes** at rank 0 | 119 | **2.68 [2.3, 3.1]** |
| the same comps cut to **2 heroes** | 100 | **4.19 [3.4, 5.2]** |
| real play, **4 heroes, nobody fell** | 7 | **3.00 [2.3, 3.9]** |

The 3.9 pooled 4-hero combats with a 2-hero one and two where a hero fell
mid-fight, against an arm pooling every tier that never lost anybody — **both
confounds push the same way**. Party size is the effect that was hiding in it.
`python tools/stress_ratchet.py --arms all --episodes 40`.

**So why the policy under-uses buffs, control and stress heals is open again**,
and now has no structural excuse.

---

## WHY IT DIED — ANSWERED 2026-09-03, and it was not the combat policy

Fran: *"using the model in game I had several deaths which I didn't really
understand why it went for that kind of play"*.

**The stay/leave layer said "worth pushing on" at every survival probability
from 0% to 100%** — 130 of 135 decisions at `P(finish intact) <= 25%`, and
RETREAT advised 5 times in 514. Three pricing defects, all fixed; the evidence
and the reasoning are in `CHANGELOG.md`. The ruler that found it is new:

```bash
python tools/retreat_audit.py                    # the verdict-by-survival table
python tools/retreat_audit.py --explain 13:23:03 # re-price one decision
```

**This is verified by unit tests and by re-pricing the logged scenes, NOT by a
live run** — the bridge is still down (below), so the end-to-end replay gate is
blocked on the retrain. Re-run `tools/retreat_audit.py` on a fresh log
afterwards: the RETREAT column must stop being empty at low `P(intact)`.

### The three axes Fran asked about, and where they stand

He asked whether the missing piece is fidelity, a deeper model, or something
else. Two are already settled by measurements in this repo:

| axis | status |
|---|---|
| a deeper model | **falsified.** `--net-arch 512,512` measured −4.1 SUCCESS / −3.0 QUALITY with `clip_fraction` and `approx_kl` both UP. Four cross-class transfer mechanisms also null. |
| game fidelity | the constraint moved off it 2026-08-24; a large batch landed 2026-09-02. Re-measure after the retrain, do not sweep again. |
| **the objective** | **open, and it is where the deaths came from.** |

### The weights question — measured, not guessed

`python tools/exchange_rates.py`. How many stress points is a hero worth?

| | ratio |
|---|---|
| **training reward** | **3,333 : 1 to 5,000 : 1**, one constant for every hero |
| game, fresh recruit | **0 : 1** |
| game, rank-4 crusader | 1,474 : 1 (3,275 at an upgraded Abbey) |

**The game varies this by three orders of magnitude; the reward has one number,
at the top of the range.** So the policy is trained to treat every hero as a
max-investment veteran — including the resolve-0 Apprentice parties the sampler
draws 20% of the time. Over-protecting a cheap hero buys caution with turns and
turns cost stress, which is exactly Fran's *"plays too slow and gets
stress-defeated"* worry.

**Nothing was changed.** The next step, if it is taken, is to scale the death
term by the hero's own investment — the env already knows the gear ranks. It is
a from-scratch retrain (`--resume` is invalid across a reward change) and it
must be a paired arm on **two seeds**; `--stress-weight` and
`--prevention-reward` both came back null on exactly this bar. Do not sweep
another single weight before reading the table.

---

## RESOLVED 2026-09-03 — the block below is kept for the DIAGNOSIS, not the status

`bridge/advisor.py` refuses to run the shipped checkpoint:

    ValueError: Unexpected observation shape (1652,) for Box environment,
    please use (1636,) or (n_env, 1636) for the observation shape.

So `bat/combat_advise.bat`, `bat/combat_play.bat`, `bridge/run.py --model`,
`tools/overnight.py` and every eval against
`models/dd_combat_ppo_full_expedition.zip` are all down. **Nothing is wrong
with the bridge** — the observation got 16 floats wider and no checkpoint
exists at the new width.

**The cause is a DATA fix.** The enemy slot carries a one-hot over the monster
types the STORE holds, one per enemy rank, so "THE DLC MONSTER TREES WERE NEVER
READ" reaching `carpentry`/`ironwork`/`thatchery`/`cauldron` cost 4 x 4 = 16
floats with no edit in `env/` at all. `tests/suite/test_obs_width_tripwire.py` now
pins it; a green suite could not see it before.

**THE ONLY FIX IS THE RETRAIN.** A checkpoint at 1652 restores the live bridge
and every eval at once. Until then the town/embark layers still work (they run
off the save and do not load a policy) — combat advice does not.

*First command after the retrain:*
`python bridge/advisor.py --once --raid archive/traces/raidsnaps2/00066_persist.raid.json --roster archive/traces/raidsnaps2/00066_persist.roster.json`
— it must print advice instead of a shape error.

### THE RETRAIN, with the recipe decided

```bash
python train.py --full-expedition --timesteps 12000000 --n-envs 12 --seed 0     --hard-focus --no-abandon-train --batch-size 256 --lr-schedule linear     --resolve-sampling     --out models/postfidelity_12M_s0.zip
```

`--resolve-sampling` is Fran's call and it is load-bearing: without it
`TIER_TO_HERO_LEVEL` fields every Apprentice hero at resolve 2 against
difficulty 1, so the shortfall is **zero in every bracket** and the
underleveled-hero penalty — the headline fidelity fix — never fires at all.

**Budget ~3.2 h at this recipe** (measured 2026-09-02, not the ~2.2 h the
shipped checkpoint's note claims). Nothing else on the machine while it runs.

`--batch-size 512` is worth **15%** of that (2.69 h) and is deliberately NOT in
the line above: batch size changes the optimisation and not just its speed, so
it wants a paired quality arm the way 64 → 256 got one. `--n-envs 16` is worth
3.5% and is nearly free.

### Then re-baseline, in this order

1. `eval_expedition.py --all-comps --episodes 4` on the new checkpoint. **The
   old one cannot be an arm** — it does not load. Every comp score, leaderboard
   and checkpoint table in the repo predates the fidelity batch AND the width
   change, so they are re-measured, never re-quoted.
2. `tools/comp_ga.py --scenario-probe` — the discriminating corner is a
   property of the POLICY and must be re-derived after any retrain.
3. `tools/comp_leaderboard.py` validity check (curated vs random legal). If the
   gap collapses, the fidelity batch changed what the fitness measures.
4. The expert rulers as corroboration — `style_probe`, `team_probe`,
   `play_log_probe`, `check_playbook`. **Rulers, never targets.**
5. Only then the `--affliction-penalty` paired 3M arm against a matched
   control, on **two seeds** — `--stress-weight` came back null twice on one.

---

## PHASE CLOSED 2026-08-29 — the optimiser, and a new shipped checkpoint

`models/dd_combat_ppo_full_expedition.zip` **is** `hpB12M_lrdecay_s1` — 12M,
seed 1, `--batch-size 256 --lr-schedule linear`. All-comps, 169 comps x 4
episodes:

| band | SUCCESS | QUALITY | stress | hp | deaths |
|---|---|---|---|---|---|
| short | 97.6% | 89.5 | 10.8 | 85.0% | 0.03 |
| medium | 96.6% | 88.5 | 10.1 | 82.8% | 0.04 |
| long | **90.1%** | **81.9** | **16.8** | 74.5% | **0.14** |

Against the two criteria this file has been tracking since 2026-08-25:

- **Deaths: comfortably clear.** 0.14 over a 14-16 combat long run is ~1 death
  per 107 combats, against Fran's "you won't lose a hero in 25-30 combats" bar.
  It was 0.38 when that bar was set.
- **Stress: still the open one, but it moved a lot.** Long runs come home at
  **16.8** mean against 33.6 in August. The target is 0 and that is not
  reachable; see N3 and the stress-weight result below for why this is now
  believed to be *correctly priced* rather than under-priced.
- **Failure rate: 9.9% of long expeditions**, from 21% — under ten percent for
  the first time.

### Two standing claims below are now WRONG — do not act on them

- **"Training longer buys noise" is retired.** It was true only because no eval
  the project ran was large enough to see the effect. On the reproducible ruler
  3M -> 6M is worth **+3.8 long QUALITY**.
- **"6M is the knee" is superseded, and the reason generalises.** That was
  measured at a constant learning rate. Under `--lr-schedule linear`, 6M -> 12M
  is worth **+1.6 SUCCESS / +1.9 QUALITY** where it was worth +0.4/+0.3 before,
  replicated on both seeds. **A plateau measured with a badly-sized step is a
  fact about the optimiser, not about the data.** The recipe is now **12M**.
- **24M was then probed and IS the plateau, so the recipe stops there.** Long
  SUCCESS 91.1 but QUALITY 81.1 and hp 72.4% — both inside one SE and
  **pointing opposite ways**, which is what a real plateau looks like. Do not
  re-run it.

### What was tried and did NOT pay, so it is not queued

Recorded so none of it is re-proposed as a fresh idea:

| tried | result |
|---|---|
| `--net-arch 512,512` | **-4.1 long SUCCESS.** The input-ratio argument in `train.py` is falsified; clip_fraction went UP. |
| `--n-epochs 5` | -1.0 SUCCESS / -1.9 QUALITY. |
| `--stress-weight 5` | Stress falls reliably (6/6, mean -3.9) and QUALITY does not follow. Diagnosed: damage taken per combat is identical, so there is no HP trade — the effect is simply worth ~1 quality point and is correctly sized. |
| `--prevention-reward` | Inside the noise on two seeds; both terms are inside Phi so they cannot change the optimum. |
| equivariant pointer head | +20.6 vs +21.5 control on held-out-class transfer. Null. |
| slot permutation | Worse (+29.6 excess gap). |

### The Warrens batch — CLOSED at 20/20 classes, 3 comps awaiting review (2026-08-29)

Seven comps, each searched with the classes of every previous one banned, so the
batch covers **all 20 vanilla classes**. Scored on ONE ruler (`comp_leaderboard`,
shipped checkpoint, warrens tier 3 long, 16 ep, seed 0) against the 169-comp
curated pool:

| comp | classes (rank order) | score | pct | status |
|---|---|---|---|---|
| warrens_01 | BountyHunter / Highwayman / BountyHunter / Vestal | 93.8 ±0.9 | 90.5 | hand-optimised |
| warrens2_01 | Crusader / Crusader / Occultist / Jester | 93.6 ±0.9 | 89.9 | hand-optimised |
| warrens3_01 | Leper / Shieldbreaker / Houndmaster / Flagellant | 91.6 ±1.0 | 75.1 | hand-optimised |
| warrens5_01 | Duelist / Duelist / Arbalest / GraveRobber | 87.0 ±2.3 | 32.5 | **raw, awaiting review** |
| warrens6_01 | Shieldbreaker x2 / Antiquarian / PlagueDoctor | 86.8 ±3.4 | 32.0 | **raw, awaiting review** |
| warrens4s_01 | Hellion / ManAtArms / Abomination / Musketeer | 86.7 ±1.9 | 29.0 | hand-optimised |
| warrens7_01 | Runaway / Runaway / Arbalest / GraveRobber | 79.6 ±4.8 | 4.7 | **raw, awaiting review** |

**The ordering tracks how CONSTRAINED the pool was, not comp quality.** Comps 5-7
drew from a ban-limited pool with **no healer class in it at all**, and 7 had
Runaway pinned with `--require-class`. Their standard errors are 2-5x wider, so
those three are genuinely inconsistent comps rather than merely lower-scoring
ones.

**The GA's emit score is a SELECTION score and reads high.** warrens6 emitted at
90.6 and re-measures at 86.8; warrens7 emitted at 86.0 and re-measures at 79.6.
The search reports the best of its population, so an independent re-measurement
always comes down — the same winner's-curse effect the 40-vs-160-episode note
records. **Quote the leaderboard number, never the emit number.**

**Two findings from the batch worth keeping:**

- **warrens7 is a real BURN comp** — Controlled Burn + Backdraft on rank 1,
  Hearthlight on rank 2, Infernal Coalstone and Pile of Ash, Kindle and Play
  with Fire in camp. The dormant vanilla subsystem is being built around on
  purpose, and Backdraft is the skill that read as inert until its
  `.requires_burning` / `.dmg_per_burn_stack` keys were parsed.
- **A comp with NO heal skill anywhere can still hold stress at 4.7** (warrens7,
  the batch's lowest) while carrying the batch's worst HP (65%). Sustain and
  stress are not the same axis.

**The corner did NOT move**, so all seven are comparable: `--scenario-probe` on
the shipped checkpoint still names `--tier 3 --band long`. What DID move is that
**tier 3 medium is now saturated** (curated bar 92.9, 3.8 headroom) — a stronger
policy eats a corner that used to discriminate, which is exactly why the probe
is re-run after every retrain.

**The fitness still PASSes but its gap narrowed, and the reason is informative:**
curated 88.1 vs random-legal 72.5, gap **+15.6 ±3.7** (164/169 curated above the
random mean), against +27.9 ±4.8 on the previous checkpoint. Random comps gained
**+15.7** and curated comps only **+3.4** — the new checkpoint improved weak
rosters four times as much as good ones, because the good ones were already near
ceiling. Comp quality matters less than it did, because the policy now rescues
bad rosters.

### The Sibyl has a checkpoint that can play her (2026-08-29)

`models/sibyl_12M_20260829.zip` — 12M, `--batch-size 256 --lr-schedule linear`,
**mods on** (21 classes, 181 comps, 12 of them fielding her). Its predecessor
`models/sibyl_20260827.zip` was 3M at batch 64 with a constant LR, i.e. the
recipe from before both of the optimiser findings.

Paired on one ruler (warrens tier 3 long, 16 ep, seed 0):

| | 3M old | 12M new |
|---|---|---|
| worst Sibyl comp | 64.8 | **87.3** |
| Sibyl comps, mean change | — | **+8.3** (n=13) |
| whole pool, mean change | — | +6.3 (n=182) |
| pool min | 30.7 | **66.8** |

Sibyl comps gained MORE than the pool average, so she is not lagging the general
improvement.

**A CONFOUND that cannot be resolved, and the fix for it.** `train.py` did not
echo `DDSIM_MOD_DIRS` until 2026-08-29, so **there is no record of whether
`sibyl_20260827` ever saw a Sibyl comp**. The +2.0 excess over the pool average
is therefore recipe and first-time-exposure mixed together. That is exactly the
unrecoverable-arm failure the `Distribution:` line was added to prevent, one
environment variable to the left — `train.py` now prints `Mod roots:` on every
run, verified in both states.

**`--model-b` is NOT usable for a Sibyl search.** The cross-check needs a second
*Sibyl-competent* checkpoint; the shipped one is mods-off and would score her
comps low for a reason that is not comp quality, manufacturing a misleading
second opinion. A second mods-on run at another seed is the honest way to get
one.

**The corner is a property of the POLICY, and this is the second demonstration
in one day.** `--scenario-probe` names `--tier 3 --band long` for the shipped
checkpoint and **`--tier 5 --band long`** for this one (SD/SE 6.3, headroom
20.4). At tier 5 long the validity gap is **+36.1 ±3.8** against +15.9 at tier 3
long — at Champion the comps genuinely differentiate.

`GA_sibyl2_01` (occultist / **sibyl** / jester / vestal) scores **87.9 ±2.1 at
Champion long, the 98.3rd percentile**, beating 178 of 181 curated comps, at
96.7% success and 90% HP. **It is also the Vestal convergence again** —
Occultist and Vestal and Jester's Inspiring Tune around her — which is the
documented hill-climb artefact, not a claim about the Sibyl.

**`GA_sibyl1_01` reads 88.3 at tier 3 long and 56.1 ±7.0 at tier 5 long.** The
two Sibyl comps are on different rulers and must not be tabled together.

### 2026-08-30 — the corroboration pass, and two leads CLOSED

**Did the new recipe teach it to PLAY or to SCORE? Some of both.** Four rulers
that never compute `run_score`, every arm re-measured rather than quoted. The
declared falsifier (flat-or-worse on 3 of 4) was **not** met — but `run_score`
moved **+6.5 long QUALITY** while the expert rulers moved a point or two. The
recipe win is real and **partly instrument-specific**; quote both halves.

Two documented findings died on re-measurement, and both had been quoted here:

- **"Skill choice degrades to 10.3pp BELOW random as sustain rises" — RETIRED**,
  on both decay seeds (-1.9, +0.8). `forecastv2_20260825` still reproduces the
  old table exactly, so it was a real property of THAT checkpoint. What replaces
  it is not a win: within-category skill choice is about as good as chance at
  every sustain level.
- **"The Crusader spends 51.9% of its turns healing" — belongs to no checkpoint
  measurable here.** `forecastv2` reads 46.4%, shipped 46.0%. Crusader healing
  has been FLAT. Arbalest is the one that moved (39.8 -> 23.5%).

**The abandon lead is CLOSED, not dropped.** It read 83% winnable at 6 events,
56% at 18, and **0.14pp of recoverable SUCCESS** once scored on the metric that
matters — a seventh of the long band's own error bar. Two thirds of abandons
happen after a hero is already dead, so SUCCESS was gone before the exit was
taken. `tools/abandon_audit.py` had a real bug feeding this (it counted the
CURRENT roster, which re-packs to drop the dead); corrected, deaths from playing
on go 0.89 -> **2.38 per run**, which argues MORE strongly that abandoning is
right.

**The Sibyl comp survived its second opinion.** `models/sibyl_12M_s1.zip` (seed
1, mods on) is the independent checkpoint that did not exist yesterday.
`GA_sibyl2_01` reads **87.9 ±2.1 (98.3rd pct)** on the policy that found it and
**85.2 ±3.5 (96.7th pct)** on the one that did not — -2.7 ±4.1, inside noise.
The comp is good, not merely easy for its finder.

- Both mods-on seeds independently name **`--tier 5 --band long`**, so that
  corner is a property of the mods-on family rather than of one run.
- **`GA_sibyl3_01` is a byte-identical duplicate of `GA_sibyl2_01`** and can be
  deleted. That is `--model-b` behaving correctly — reported, never selected on,
  so it cannot bend the search. Its internal cross-check (84.9 ±3.8) reproduces
  the independent leaderboard figure (85.2 ±3.5) by a second route.

**All four were reviewed and promoted the same day** (pool 168 -> **172**),
and three of the four hand-optimisations replicate on two checkpoints:

| comp | mean delta over its GA original |
|---|---|
| Ember Trail: Twin Burn | **+11.0** |
| Curious Coin: Twin Sand | **+7.1** |
| Waiting Blade: Twin Duel | +3.1 |
| Grand Finale: Sibyl | **-2.9 / +5.0 -- the two policies disagree in SIGN** |

See CHANGELOG. The Sibyl one is not distinguishable from its original and
should not be quoted as an improvement in either direction.

### The COVE batch — 20/20 classes in 7 comps, awaiting review (2026-08-30)

Cove was the thinnest zone (27 comps against crypts' 85). Same sequential
method as the Warrens: emit, ban the classes used, search again. Driven to
completion unsupervised. Scored on **two** checkpoints against the 172-comp
pool, warrens-style ruler (`--zones cove --tier 3 --band long`, 16 ep, seed 0);
the Cove bar is **mean 89.0, max 97.5**, validity PASS (+15.8 ±4.3).

| comp | roster | shipped | independent |
|---|---|---|---|
| **Waiting Blade: Sand & Lunge** (Fran's rework of cove1) | SB / HWM / GR / Vestal | **97.1** | **98.0** (100th pct) |
| GA cove1 01 | SB / HWM / GR / Vestal | 96.0 | 95.6 |
| GA cove5 01 | Leper x2 / Arbalest x2 | 93.8 | 92.3 |
| GA cove2 01 | Occultist x2 / Jester / Houndmaster | 93.4 | 93.4 |
| GA cove3 01 | Crusader x2 / Musketeer / Bounty Hunter | 92.6 | 91.2 |
| GA cove4 01 | Hellion / Abomination / Flagellant / Duelist | 90.9 | 92.2 |
| GA cove7 01 | Runaway / GraveRobber x2 / Highwayman | 82.1 | 82.7 |
| GA cove6 01 | Antiquarian / ManAtArms x2 / PlagueDoctor | 81.0 | **70.9** |

**Five of seven clear the pool mean on both checkpoints.** The ordering tracks
how CONSTRAINED each round's pool was, not comp quality -- the same shape the
Warrens batch produced. cove6 is the one the two policies disagree about
(81.0 vs 70.9, ±2.4 / ±5.6): treat it as unresolved rather than as the worst
comp.

**`Waiting Blade: Sand & Lunge` beats every comp in the pool on the independent
checkpoint** and is 2nd on the shipped one. Its gain over the GA original is
only +1.1 / +2.4 because the original already scored 96.0 against a 97.5
ceiling -- **a ceiling effect, not a weaker optimisation.** Compare Ember Trail,
which started at 79.6 and gained +11.0.

**One observation worth testing later:** the search keeps equipping region
quirks (Cove Explorer / Tactician / Scrounger / Adventurer) and Fran's rework
replaced all of them with the general dodge sheet (Corvid's Eye/Grace, Evasive,
Luminous) -- and scored higher. That is one comparison, not a finding, but it
is cheap to check across the batch.

**Nothing here is promoted.** All 7 sit in `GA_comps/`, which is read by
nothing; promotion is Fran's call alone.

**The seven were emitted under the OLD sparse quirk draw**, fixed the same day
(`SEARCH_QUIRKS_PER_POLARITY = (5, 5)`). Each would gain roughly **+2.3** with
its quirk slots filled -- which is exactly what Fran's hand-optimisation pass
does anyway, and is a large part of why his reworks gain. The batch was NOT
re-run for it: ~4.7 h of search to recover something his review step supplies
for free. **The next batch gets it from the start.**

### The WEALD batch — 20/20 classes, and the first batch with filled quirks (2026-08-30)

Run unsupervised straight after the Cove. Weald bar: **mean 91.0, max 98.0**,
validity PASS but a NARROWER gap than the Cove's (+8.7 ±2.2 against +15.8
±4.3) — random legal comps score **82.3** in the Weald against 73.3 in the
Cove, so this region separates good comps from bad ones less sharply. Read a
Weald delta with that in mind.

| comp | roster | shipped | independent |
|---|---|---|---|
| GA weald7 01 | Occultist x2 / Abomination / BountyHunter | **97.2** | **97.2** |
| GA weald1 01 | Houndmaster / Occultist / Abomination / Vestal | **97.0** | 96.4 |
| GA weald2 01 | Crusader x2 / GraveRobber / Jester | 95.5 | **97.2** |
| GA weald3 01 | Hellion / Highwayman / Flagellant / PlagueDoctor | 93.1 | 93.5 |
| GA weald6 01 | Occultist / Houndmaster x2 / Antiquarian | 91.4 ±4.9 | 94.4 |
| GA weald5 01 | Runaway / ManAtArms / Shieldbreaker / Musketeer | 90.5 | 93.7 |
| GA weald4 01 | Leper x2 / Arbalest / Duelist | 90.0 | 90.8 |

**Batch mean 93.5 / 94.7 against a pool mean of 91.0** — five of seven above the
bar on the shipped checkpoint, six of seven on the independent one, and the two
policies agree on the ordering at the top and bottom.

**This is the quirk fix in production and it worked.** Every Weald hero emitted
with **5 positive quirks**; the Cove batch, searched two hours earlier, emitted
`[3,2,1,2]`, `[1,3,1,1]`, `[1,3,3,1]`. The batch has no comp in the Cove tail's
79-82 range — though the Weald being an easier region is a confound, so the
production evidence for the fix is the QUIRK COUNTS, not the scores.

**Nothing is promoted.** All 7 sit in `GA_comps/` alongside the 7 Cove comps and
`Waiting_Blade__Sand_Lunge`; promotion is Fran's call alone.

### What is worth doing next, in order  (rewritten 2026-08-30)

1. **Fran's review queue: 15 comps.** Seven Cove, seven Weald, plus
   `Waiting_Blade__Sand_Lunge`. Nothing is promoted and nothing should be
   without him. His hand-optimisation is worth **+11.0 / +7.1 / +3.1** on the
   Warrens comps (replicated on two checkpoints), so this step is where most of
   the remaining comp quality is, not in more searching.
2. **The pool is 49% crypts** (85 of 172). The Cove and Weald batches were run
   precisely to rebalance it, and they only do so if he promotes some. A crypts
   batch is the LEAST valuable one to run next.
3. **THE BRIDGE RUNS ITSELF (2026-08-31).** `tools/overnight.py` launches the
   game, boots a campaign, plays the Old Road, walks to the Hamlet,
   provisions, embarks on a generated dungeon, plays that, and repeats --
   then deletes the slot and starts over. Logs and a rolling `summary.json`
   under `archive/logs/overnight/`.

   **Read the summary first; that is the point of it.** Within three
   iterations it had found two bugs nobody was looking for: only one of the
   game's EIGHT corpse classes was recognised (so at Veteran and Champion
   every corpse silenced the advisor for the rest of the fight), and every
   self-buff in the game was unplayable (`advised_slot` gave no rank for a
   self-cast, so Bulwark of Faith, Take Aim and Bolster could be recommended
   and never played). Both are fixed and pinned.

   **What is left is DRAG-shaped**, and the primitive now exists and is
   verified live:

   - **The stage coach.** Recruits are in the save --
     `persist.town.json` -> `buildings.stage_coach.store.hero_recruit` and
     `shard_hero_recruit`, with `heroClass`, `resolveXp`, ranks, quirks and
     skills -- so WHO to recruit is a data decision and the screen is only
     needed for geometry. Recruit toward a curated comp SHAPE, reusing
     `embark.candidate_shapes`.
   - **Party assembly on the mission page.** Four heroes dragged into the
     party slots. Until then every embark goes through the game's own
     "without a full contingent?" warning, which is the tell that the party
     is short.
   - **The other buildings.** Abbey/Tavern stress relief, Sanitarium quirks,
     Blacksmith/Guild upgrades, Nomad Wagon trinkets. Each needs a screen
     reference, a geometry measurement and a decision rule, and each changes
     who is available to embark -- so each lands WITH its effect on the town
     advice, not before it.

   **A drag cannot be confirmed by the save.** Verified: a pack rearrangement
   moves the item on screen with the save's mtime unchanged and its slot
   index still reporting the old cell. The proof has to be pixels.

4. **The live bridge is UNBLOCKED as of 2026-08-30 and is the most
   productive thing an agent session can do.** With a Tailscale session
   attached there IS an interactive desktop. The whole campaign loop ran with
   no human input -- title screen -> new campaign -> the Old Road -> combat ->
   loot -> Return to Hamlet -> the Hamlet -> Estate Map -> Provision -> a real
   Ruins quest -> `rooG`, a room beyond the entrance of a GENERATED dungeon.

   It paid for itself four times over, and every one of these was invisible
   from offline replay:

   - `MOUSEEVENTF_VIRTUALDESK` missing, landing **every click at half its x**
     on a two-monitor machine.
   - the dungeon graph reading `corA` as a phantom room `roor`, so **every
     route on every real raid** was planned against a fabricated topology.
   - the map panel's zoom being stateful, and its reset an animation.
   - a corridor heading that could be measured wrong and never unlearned.

   **THE NEXT TWO ARE NAMED AND HAVE THEIR EVIDENCE BANKED:**

   - **Provisions read empty.** A relight was advised (`torchlight.advise`
     gates on `torches_held > 0`, so it saw torches), `T` was sent, and the
     bridge reported "the input did not land" three times -- while the state
     read back showed **no provisions at all** and torch **0**. Either the
     pack emptied between advice and send, or `LiveState.provisions` is not
     reporting what the party carries. **Distinguish those first**: the second
     would mean every dungeon-layer provision decision is made on an empty
     dict. First command: read `persist.raid.json`'s inventory directly
     against `LiveState.provisions` on the same save.
   - **The curio panel is still not found on screen**, and the banked frame
     says why it is not the detector's fault: the party stood at the far LEFT
     of the corridor with the sconce well to the right, so the APPROACH is
     what needs measuring. `tools/find_button.py` is the instrument now, and
     `-firstroomcurio` puts a curio in the first room so it costs one boot
     rather than a raid.

   **Iteration flags, probed on this build's argument table:**
   `-skipfeflowintro` works and saves ~60s of intro per boot -- use it always.
   `-skipfeflow -profile N` **hangs on a black screen even for a slot that
   exists**, which settles the "unprobed" note in BRIDGE.md: it cannot replace
   the menu clicking. `-firstroombattle`, `-firstroomcurio`, `-firstroomcamp`,
   `-alwayshit`/`-alwaysmiss`/`-alwayscrit` and `-randomseed` are all in the
   table and are the scenario control a capture session wants.

   The five unconfirmed readings in `ASSUMPTIONS.md` are a separate job and
   still want a real capture session with those determinism flags on.
5. **The model is at a genuine plateau.** 12M + linear decay, long band 90.1%
   SUCCESS / 81.9 QUALITY; 24M was measured and the metrics stop agreeing
   there. The one diagnosed weakness left -- within-category skill choice is
   about as good as chance -- has no proposed lever, and the tier list that
   measures it must never become a training target.

### The open question the session ends on

**Zero-shot transfer to an unseen class is CLOSED as a representation problem** —
four mechanisms, four nulls. Few-shot works (+12.7pp in 600k steps). So a modded
class needs *training data*, not a better encoding, and
`tools/compose_class_comps.py` is the tool for that.

**The live behavioural lead is `tools/style_probe.py`:** given that it decided
to attack, the policy picks a worse-tier attack than chance as comp sustain
rises, and it passes over stress-heal/debuff/control utility thousands of times
(Crusader's Inspiring Cry 1108, Leper's Intimidate — S-tier — 479). The
direction replicates across five checkpoints; the magnitude swings ~12pp between
seeds. **That is a ruler, never a target** — nothing in `tools/skill_tiers.py`
or `check_class_play.py` may become a reward term.

---


## PHASE CLOSED 2026-08-25 — the combat model is shipped (HISTORICAL)

**Superseded twice since. The canonical slot holds `hpB_lrdecay_s1` — see the
2026-08-29 section above.** `forecastv2_20260825` is in `archive/models/`, and
the table below is ITS eval on the ruler of the day; re-measured on the current
engine that same checkpoint reads short 95.3/86.5, medium 90.4/83.9, long
81.8/75.4, because several fidelity fixes landed after these numbers were taken.
Kept for the reasoning, not the figures.

All-comps, 1,824 episodes:

| band | SUCCESS | QUALITY | stress | hp | deaths |
|---|---|---|---|---|---|
| short | 94.2% | 86.4 | 15.7 | 82.3% | 0.08 |
| medium | 92.3% | 84.7 | 18.3 | 80.3% | 0.14 |
| long | 78.8% | 73.8 | 33.6 | 68.9% | 0.38 |

Against Fran's two stated criteria: **0.38 deaths over a 14-16 combat long run
is ~1 death per 39 combats, which clears his own "you won't lose a hero in
25-30 combats" bar.** Stress does not: long runs come home at ~34 mean against
a target of 0. And 21% of long expeditions still fail outright.

### The night's ledger — one win, one negative, three nulls

| experiment | verdict |
|---|---|
| **eval reproducibility** | the enabler; every prior `--all-comps` delta carried ~2pp of hidden error |
| **threat forecast** | **+6.2pp SUCCESS, +4.57 QUALITY, −29% deaths, p=0.0010** |
| five knobs, deployment | −8.6pp, p<0.0001 |
| five knobs, robustness | **+4.9pp, p=0.0224** — the sign flips |
| crit-stress calibration | null, p=0.51 |
| P(death) / P(Death's Door) | null → reverted from the observation |
| `max_provisions_before_full` | null → kept on fidelity grounds |

**The coarse signal is what paid.** Both refinements of the forecast were null.
Stop refining it.

### What is still open, in order of expected value

1. **Re-record the agreement corpus during serious play.** `tools/agreement.py`
   is the only ruler with no simulator in it, and its combat rate is retired
   because the corpus was recorded while testing the bridge. This is the only
   measurement that answers *"is the model better than Fran"*, which is the
   real question behind "good enough".
2. **Corpse attacks cost 59 kills** (`check_playbook`): the policy hits a corpse
   on 10.2% of the turns it could, and in 39.1% of those a living enemy in the
   same skill's reach would have died to that hit. Concrete and tactical.
3. **Recovery on a secured kill is 34.5%**, doubled from 17.2% but short of the
   expert "most of them".
4. **Unjustified heals** — realised reward is −0.0635 against +0.2183 for
   justified ones, so the reward already prices them correctly and this is
   training depth, not design.
5. **Fights are short** (mean 2.9 rounds, 76% end within three), which bounds
   how much any stall/buff work can ever be worth. Check this before investing
   in either.

### Rules this phase established, all in CLAUDE.md

- A disagreement between two rulers is a **bug signal**, never noise. It caught
  every measurement error this session; no test did.
- **Internally valid is not measuring the intended distribution** — the
  comparators ran full 7-skill movepools and diluted a real effect from +7.7pp
  to +3.0pp.
- **Changing what an observation feature CONTAINS is the silent break.** The
  width still matches and the checkpoint still loads.
- **A null feature that widens the observation is worse than neutral**: it
  strands every checkpoint trained before it.
- **To test a new feature, train a control at the SAME width** with only that
  feature zeroed (`--blind-forecast` is the pattern).
- **Never run the xdist suite or heavy tools alongside a 12-env training job.**
  It OOM'd a 3M run.

---

## CLOSED 2026-08-27 — the test suite (Fran's ask, done)

Two asks, both finished; nothing here is pending. Full evidence in
`CHANGELOG.md`, invariants in `CLAUDE.md` and `pytest.ini`.

- *"The test suit... clogs up all the memory, this crashes the pc"* and
  *"optimize... faster, but also better memory wise"* — **173 s → 63 s**, and
  **9.65 GB → 5.4-6.8 GB** peak with torch out of collection (same instrument;
  the pre-fix figure is the collection-time load reproduced, since the original
  7.4 GB reading came from a sampler too slow to catch the spike). 1820 tests,
  three consecutive clean runs, no flakes, no skips.
- *"lets also check if there are stale test thats now with all the changes on
  the project dont make sense"* — **ten found and fixed.** One iterated
  `store.comps`, emptied when the CSV was retired; nine asserted nothing at all.
  `tools/find_silent_tests.py` is the instrument and is re-runnable; it reports
  one remaining, declared exempt with its reason.

Three things worth carrying forward, because each cost a wrong first answer:

- **The expensive thing was COLLECTION, not the tests.** Every xdist worker
  collects the whole suite, so one module-level `import torch` is paid sixteen
  times before anything runs. `--dist loadfile`/`loadgroup` do nothing for that.
- **Pinning a file is the wrong instrument for torch and the right one for
  memory.** Six tests in 1820 load torch; the suite's peak is transient frame
  decoding, not models.
- **A module-scoped fixture holding an RNG is an order-dependent test**, and
  making the draw deterministic can turn a conditional `pytest.skip` into a
  permanent one — which is worse than the flake it replaced.

## SETTLED 2026-08-27 — the Runaway fix landed; the prevention reward did not pay

**Landed and kept** (suite green, `train.py --check` clean):

- **Backdraft and Controlled Burn work.** `backdraft` applied literally nothing
  — it names no `.effect`, and its payload is three unparsed keys. Controlled
  Burn's rank zones are new engine machinery (`CombatEngine.burn_zones`).
  `tests/engine/test_runaway_burn.py`; `ASSUMPTIONS.md`'s two Runaway gaps are closed.
  **This is a fidelity fix and does not depend on any training run.**
- **`tools/check_class_play.py`** — the per-class execution audit. A RULER only.
- **`--focus-mechanic {dodge,bleed,blight,burn}`** — specialisation by
  CAPABILITY, read off the equipped kit, never by comp name.
- **`--prevention-reward`** — kept, **default off**, byte-identical when off.

**Measured and RETRACTED.** Four 3M arms (two seeds x control/prevention).
Paired per comp, 19 shared comps x 4 zones x 6 episodes at Champion:

| band | seed 0 | seed 1 | mean of seeds |
|---|---|---|---|
| short | −0.44 ± 1.39 | −2.50 ± 1.20 | −1.47 ± 1.38 |
| medium | +0.38 ± 2.08 | +3.42 ± 1.65 | +1.90 ± 2.02 |
| long | +4.69 ± 2.15 | +1.58 ± 1.84 | +3.14 ± 2.10 |

Inside the noise on every band. The behavioural claim **inverted**: Blinding Gas
openings `prevention − control` were +49.3pp on seed 0 and −15.0pp on seed 1.
Both seeds agree only that Crusader healing went **up**. Full evidence and the
reasoning in `CHANGELOG.md`.

**The two open questions this leaves, in priority order:**

1. **Is the death penalty mispriced?** `REWARD_HERO_DEATH = -50.0` against
   `REWARD_WIN = 5.0` makes healing the direct way to avoid the largest term in
   the reward, so heavy healing may simply be correct here. A Φ term cannot
   test this — it would take a **non-potential** change and a re-check of the
   invariance argument. **Falsifier:** if healing share is insensitive to the
   death penalty across a sweep, the guides and the simulator disagree about
   the game, which is a fidelity question and not a reward one.
2. **How many seeds does this project actually need?** Between-seed spread
   (~3pp on the long band) is larger than the within-seed paired SE (~2pp), so
   **more episodes buy nothing and only more seeds do**. Every single-seed A/B
   in `CHANGELOG.md` — including the dodge and burn focus arms — inherits this.
   Deciding the standard seed count is worth more than any one more experiment.

**Do not compare any of these arms to the shipped checkpoint.** It is
off-distribution the moment the Runaway fix landed — the observation width is
unchanged so it still LOADS, which `CLAUDE.md` names as the silent break.

---
