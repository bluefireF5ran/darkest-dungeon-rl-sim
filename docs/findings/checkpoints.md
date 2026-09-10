# Findings: the checkpoints, and what each one scored

Which checkpoint is shipped, what it was trained with, and what it
measured. Moved verbatim from the root `CLAUDE.md` on 2026-09-07.


## The shipped checkpoint (2026-09-03)

`models/dd_combat_ppo_full_expedition.zip` **is** `postfidelity_12M_s0` — 12M,
seed 0, 12 envs, `--hard-focus --no-abandon-train --batch-size 256
--lr-schedule linear --resolve-sampling`. 2h27m at ~1363 fps; the LR decayed
out to 1.7e-07 with `clip_fraction` 0. The 1636-wide predecessor is
`archive/models/dd_combat_ppo_full_expedition_1636_12M_lrdecay_s1_pre20260903.zip`.

All-comps, **176** comps x 3 bands x 4 episodes:

| band | SUCCESS | QUALITY | stress | hp | deaths |
|---|---|---|---|---|---|
| short | 96.4% | 90.0 | 12.9 | 87.1% | 0.04 |
| medium | 94.7% | 85.9 | 12.2 | 78.8% | 0.06 |
| long | **81.7%** | **75.1** | 25.3 | 65.4% | 0.21 |

**THE PREVIOUS TABLE READ 97.6 / 96.6 / 90.1 AND IS NOT A CONTROL FOR THIS
ONE.** Three things moved at once and none of them can be held still:

- the 2026-09-02 fidelity batch, which makes the world harder on purpose;
- the comp pool, 169 → 176;
- and the observation, 1636 → 1652, **which is why the old checkpoint cannot
  simply be re-run**. It does not load. There is no control and there cannot
  be one, so the drop is uninterpretable and must not be recorded as a
  regression. This is the price of the batch, and it was worth paying — but it
  is a price, and the honest record is "new baseline".


### The eval scores an EASIER world than training (2026-09-03)

`--underleveled` closes that gap, and it needs **two** gates, either of which
alone is a silent no-op:

- **`resolve_sampling`** — off, `TIER_TO_HERO_LEVEL` pins every hero at the TOP
  of its bracket, so the shortfall against `TIER_TO_QUEST_DIFFICULTY` is zero
  in every bracket and there is no penalty to charge.
- **`quest_difficulty_level` in the reset options** — the SAMPLED path
  (training) fills it from `TIER_TO_QUEST_DIFFICULTY`; the EXPLICIT path
  (`eval_expedition`, `bridge/rollout`, `tools/comp_ga`) leaves it `None`, and
  `CombatEngine.reset` reads `None` as zero shortfall.

Passing only the second is measurably nothing: an `--all-comps` run with it
came back **byte-identical** to the run without. Paired on identical scenarios,
both open:

| band | as shipped | in the TRAINING world | delta |
|---|---|---|---|
| short | 96.4 / 90.0 | 95.0 / 87.7 | −1.4 / −2.3 |
| medium | 94.7 / 85.9 | 91.8 / 83.3 | **−2.9** / −2.6 |
| long | 81.7 / 75.1 | **76.0 / 71.8** | **−5.7** / −3.3 |

Long-band stress p90 goes **51.5 → 69.0**. That is the first measurement of the
2026-09-02 headline fidelity fix and it is among the largest effects this
project has recorded. **The policy trained in the harder world and is scored in
the easier one**, so the headline flatters it; `--underleveled` is the number to
quote when the question is "how good is it at the game it learned".

It does **not** explain the drop against the archived 90.1, because that number
was taken with the penalty off too. Both sides are penalty-free.



### The modded checkpoint, and what the corpus bought (2026-09-05)

`models/dd_combat_ppo_full_expedition_mods.zip` (and `_s1`) is the shipped
recipe -- 12M, 12 envs, hard-focus, batch 256, linear decay, resolve sampling --
with **exactly one thing changed: the corpus**, 288 comps over 54 classes
instead of 176 over 20. The Phase 3 knobs stayed off deliberately; turning them
on here would have moved three things at once, which is the mistake the
2026-09-03 baseline records.

**The shipped vanilla checkpoint is untouched and is still the comparable one.**

**DO NOT COMPARE 95.7 / 92.0 / 78.5 AGAINST 96.4 / 94.7 / 81.7.** Those are
different pools. Run the VANILLA checkpoint on the 288-comp pool and it scores
95.7 / 92.4 / 77.2, so the apparent drop decomposes as:

| | short | medium | long |
|---|---|---|---|
| pool effect (the eval got harder) | −0.7 | −2.3 | −4.5 |
| **checkpoint effect** | **+0.0** | **−0.4** | **+1.3** |

Almost all of it is eval difficulty, and against its proper control the modded
checkpoint is slightly AHEAD on the long band.

**Split by comp type, paired, two seeds** -- same 268 comps, same scenarios,
the control being the vanilla checkpoint on the same pool:

| | seed 0 | seed 1 | mean | between-seed spread |
|---|---|---|---|---|
| vanilla-only (n=156) | −1.2 ± 0.8 | −1.1 ± 0.7 | **−1.15pp** | **0.1** |
| fields a modded class (n=112) | +2.5 ± 1.1 | +4.0 ± 0.9 | **+3.25pp** | 1.5 |

Both effects are long-band driven: vanilla −2.4 / −3.7, modded **+6.2 / +7.6**.
The vanilla cost replicates almost exactly; the modded gain replicates in
direction with more spread and stays well clear of zero on both seeds.

**The trade, stated plainly: about a point of vanilla performance for three on
modded comps -- nearly seven on the long band -- plus 34 classes that are
playable at all rather than not.** Whether that is worth it is Fran's call, and
both checkpoints exist so it does not have to be made blind.


### It survives longer and plays the SAME way (2026-09-05)

The modded checkpoint lasts longer and retreats half as often, so the obvious
next question is whether the corpus changed HOW it plays. Measured on
`tools/team_probe.py`, both modded seeds against the vanilla checkpoint, all
three on the same mods-on pool:

| | mods s0 | mods s1 | vanilla | between-seed spread |
|---|---|---|---|---|
| FOCUS | +9.8 | +11.4 | +12.2 | 1.6 |
| FINISH | −20.7 | −17.5 | −22.9 | 3.2 |
| CONTROL | −1.9 | −3.0 | −2.9 | 1.1 |
| between-comp SD | 10.1 | 8.1 | 8.0 | 2.0 |

**Every difference is inside the between-seed spread. The corpus bought
survival, not style.**

Two things this kills, both of which one seed would have reported as findings:

- **"It became more comp-adaptive."** Seed 0's between-comp SD of 10.1 against
  vanilla's 8.0 is exactly the signal `style_probe`'s "one plan" question asks
  for. Seed 1 reads 8.1. Noise.
- **"Control got worse."** Against the RECORDED +2.0 from 2026-09-03 the
  modded checkpoint's −1.9 looks like a regression. Against the vanilla
  checkpoint measured on the SAME pool (−2.9) it is fractionally better. The
  recorded number was pool-dependent, which is the same trap the eval headline
  set.

So the survival gain is real and unexplained by anything these probes see, and
the standing question -- why the policy under-uses control, buffs and stress
heals -- is untouched by the corpus.
