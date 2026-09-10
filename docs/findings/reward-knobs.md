# Findings: the reward, and every knob turned on it

Three deliberate experiments on the reward and one measurement of its
constants. Two came back null, one came back HARMFUL, and the fourth
says why. Moved verbatim from the root `CLAUDE.md` on 2026-09-07.


### The Runaway's kit, and PREVENTION in the reward (2026-08-27)

Fran's round-4 review. Three of his four asks were the same defect: **neither
the engine nor the reward credited deferred or preventive value**, so the policy
converged on immediate healing. Measured before the fix
(`tools/check_class_play.py`, 30 episodes at Champion): the **Crusader spends
51.9% of its turns healing**, the Occultist 48.2%, the Arbalest 45.0%.

**DO NOT QUOTE 51.9% AGAINST A LATER CHECKPOINT.** Re-measured 2026-08-30 with
the same command, `forecastv2_20260825` reads **46.4%** and the shipped
checkpoint **46.0%** — so Crusader healing has been FLAT, not falling, and the
51.9% belongs to some earlier arm. Arbalest is the one that moved (39.8 ->
**23.5%**). What changed overall is heal VOLUME, not heal JUDGEMENT: 13.1% ->
9.4% of hero turns, with the unjustified FRACTION unmoved (44.3 -> 44.9%).

- **`backdraft` applied NOTHING and that was not obvious.** It names no
  `.effect`, so its parsed effect list is `['']` and it read as an inert skill
  for months. Its whole payload is `.requires_burning`,
  `.dmg_per_burn_stack 0.25` and `.copy_burn_behind true`. Parse them at
  **both** `_build_hero` sites; gate `requires_burning` in **both**
  `get_valid_actions` and `_resolve_action_targets`, including the
  **single-target** branch — Backdraft is single-target, so gating only the AoE
  path left it unenforced exactly where it lives. Scaling reads `burn_stacks`,
  the COUNT, never `burn_total_outstanding`.
- **`CombatEngine.burn_zones` is a persistent effect attached to a RANK**,
  which nothing else in the engine had. Ticked at `_end_of_round` against the
  board **as it stands then** — that ordering IS the mechanic, and it is why
  Controlled Burn pairs with pull and push. Expiry after the tick, so a
  one-round zone still burns once; re-painting refreshes; cleared on `reset`.
- **A low usage rate still is not evidence against a skill** (2026-08-26 rule),
  but an EMPTY effect list now is: `tests/engine/test_runaway_burn.py` pins that
  Backdraft is the only carrier of those three keys, so if they stop parsing the
  skill goes back to inert silently.

**IT WAS MEASURED OVER TWO SEEDS AND IT DID NOT PAY — leave it off.** Two
seeds x two 3M arms, paired per comp: long band **+4.69 ± 2.15** on seed 0 and
**+1.58 ± 1.84** on seed 1, mean of seeds **+3.14 ± 2.10** once between-seed
variance is admitted — inside the noise on every band. The behavioural claim
inverted outright: Blinding Gas openings ran `prevention − control` **+49.3pp
on seed 0 and −15.0pp on seed 1**. The only thing both seeds agree on is that
**Crusader healing went UP** (+7.5pp, +20.3pp), the opposite of the ask.

**And that was predictable from the theory.** Both terms are inside Φ, so they
cannot change the optimal policy — only credit timing. **"Heal less" is not
buyable with a potential-based term.** Healing is near-optimal on the merits
while `REWARD_HERO_DEATH = -50.0` stands against `REWARD_WIN = 5.0`; moving it
means a NON-potential change and a re-check of the invariance argument in
`ASSUMPTIONS.md`. See the retraction in `CHANGELOG.md`.

**`--prevention-reward` (default off, byte-identical).** Two terms inside Φ, so
by Ng/Harada/Russell neither can change the optimal policy — they fix credit
TIMING, the same argument `W_ENEMY_STUNNED` rests on.

- **Outstanding DoT enters `_enemy_score`**, capped at the target's own HP. It
  read `hp/max_hp` and nothing else, so applying a blight earned zero immediate
  credit while the turn penalty charged the turn at once. **That is the complete
  account of why the Plague Doctor is under-used** — her kit is deferred damage.
  The cap is what stops overkill out-scoring a kill AND what stops double
  counting when the DoT ticks.
- **The threat forecast enters Φ.** Killing, stunning, raising DODGE and
  debuffing ACC all lower next round's forecast; a heal changes **nothing**
  about what is coming. That asymmetry is the whole point, and it prices dodge
  without a dodge term. It must keep drawing nothing —
  `tests/engine/test_threat_forecast.py` is the guard.

**`--focus-mechanic {dodge,bleed,blight,burn}` specialises on CAPABILITY.**
Fran: *"we want to fine tune this model so it learns 'The mechanics of Darkest
Dungeon and the importance of certain numbers' rather than making a player that
follows the same plan always, as this wont enable the use of modded classes"*.
Membership is read off the **equipped** kit's effects, so a modded class joins a
family with no entry anywhere. **`FOCUS_MIN_SOURCES = 2`, because a family means
BUILT AROUND it**: at a threshold of 1, 144 of 163 comps are "dodge comps" and
130 are "bleed comps", which is the pool rather than a focus. `FOCUS_MIN_POOL`
relaxes a thin family (burn: 7 can do it, 2 can stack it) rather than shipping a
two-comp focus.

**Its error bar between TRAINING RUNS is very wide.** Its control arm moved
**2.4% -> 15.0%** on the Blinding Gas check between two seeds of one recipe,
and Crusader healing **47.9% -> 32.5%**. A single-seed behavioural delta from
it is not evidence: two arms of one seed control for a fidelity fix and for
scoring noise, never for the training run. Quote it with a second seed or not
at all.

**`tools/check_class_play.py` is a RULER and must stay one.** It grades the
policy against the 21 per-class guides in `metadata/HEROES/Heroes/`. Fran:
*"only use this to verify and iterate"* — **nothing in it may become a reward
term, an action mask or a training target**, because a policy following a
per-class script is exactly the un-scalable player the mechanics-not-identity
observation exists to avoid.


### `--stress-weight`: the training reward and the ruler disagree about stress

Measured, not assumed. `eval_expedition._run_quality` — what `run_score` is
built from, and what Fran's objective names ("finish, nobody dead, zero
stress") — gives stress **half** of a hero's score. The training return gives it
about **9%**: over a 15-combat run, combat wins and the completion bonus are
worth ~85 while `STRESS_TICK_PENALTY` and `TERMINAL_STRESS_PENALTY_WEIGHT`
together reach ~8.

The same gap shows up per-turn. Inspiring Cry stress-heals 8, which through
`W_STRESS = 0.50` on a `stress/100` scale is **+0.010 of Φ** averaged over four
heroes, against `TURN_PENALTY_THREAT = −0.100` for spending the turn. An attack
taking 30% off one of four enemies is roughly **+0.075**. The shaping prices a
damage turn at about seven times a stress-relief turn — and Crusader's Inspiring
Cry is the single most passed-over skill in `tools/style_probe.py`, 1108 times.

`--stress-weight W` scales BOTH stress terms together; 1.0 is byte-identical to
every archived checkpoint. **This is a NON-potential change and does move the
optimum** — unlike `--prevention-reward`, whose terms sat inside Φ and therefore
could only ever change credit timing, which is why it came back null twice.

Scaling both together is deliberate: the tick charges stress when it lands and
the terminal charges what is left, so moving one alone changes the shape of the
incentive rather than its size. `tests/training/test_train_wiring.py` asks the WORKERS
what they received, because `--slot-permute` was once echoed as `True` in the
distribution line while no env ever got it.

**MEASURED OVER TWO SEEDS, and the verdict is split.** `--all-comps
--episodes 4` on four 3M arms:

| | seed 0 | seed 1 |
|---|---|---|
| QUALITY, mean of three bands | **+2.33** | **+0.27** |
| end-of-run stress, mean of three bands | **-6.3** | **-1.5** |

**The stress reduction replicates** -- six of six band-seed comparisons
negative, mean **-3.9 points**. The knob does what it says. **The quality gain
does not**: six of six positive, but the honest unit is the seed, and
+2.33 / +0.27 is a mean of +1.30 with a between-seed SD of 1.46 -- an interval
of about +-2.9 that spans zero. Quoting the six bands as independent gives
+1.30 +- 0.95 and excludes zero; that would be wrong, because the three bands
of a seed share one checkpoint.

Stress falls and quality barely moves, so something offsets it -- and
`_run_quality` is half HP, half stress. The policy appears to **trade HP for
stress relief at roughly par**. Default stays **1.0**; the flag is a real lever
on a real quantity, and it does not buy score. If anyone returns to it, ask
first why stress relief costs HP.


### `--affliction-penalty` is HARMFUL — measured, two seeds (2026-09-03)

Four 3M runs at the shipped recipe, control against `--affliction-penalty 20`,
seeds 0 and 1, each scored `--all-comps --episodes 4`. The prior was null.
**It is worse than null:**

| | s0 | s1 | mean of seeds | between-seed spread |
|---|---|---|---|---|
| SUCCESS | −7.50 | −6.60 | **−7.05** | 0.90 |
| QUALITY | −1.47 | −2.67 | **−2.07** | 1.20 |

Eight times the between-seed spread — the bar `--stress-weight` and
`--prevention-reward` both failed. Long-band SUCCESS **−11.8 / −10.4**. Six of
six agree on SUCCESS, QUALITY and HP; deaths go **up** six of six (+0.15 long).

**Stress did not move** (3/6 negative). An affliction IS 100 stress, so a term
that charges for afflictions should show as less stress if the policy learned to
avoid them. It did not; it just got worse.

**Why: it prices something the policy cannot prevent.** 20 is 40% of
`REWARD_HERO_DEATH`, and the same day's ratchet measurement says a hero
climbing toward 100 mostly keeps climbing — even in the simulator, falls are
**−6.9** against rises of **+12.6**. That makes the term variance rather than
signal, and PPO's advantages get noisier for nothing. The rise in deaths is the
policy trying anyway: haste is its only lever against accumulating stress.

**The transferable point.** `--prevention-reward` and every `W_*` sit inside Φ,
so by Ng/Harada/Russell they cannot change the optimal policy — which is why
they came back null twice. This one is non-potential, so it genuinely can move
the optimum, and it did. **Moving the optimum is not improving it.** A term that
prices a real thing correctly still hurts when no available policy avoids it.

Default stays 0.0 and the flag stays, with this attached, so nobody re-runs it
hoping. **What would change the answer is making afflictions preventable** —
the stress-recovery question the ratchet opened, which is a fidelity
investigation and not a knob.


### The weights are a CONSTANT where the game's ratio spans 1000x (2026-09-03)

`python tools/exchange_rates.py` — Fran: *"I don't really know what should be
the optimal scoring parameters for the model as well as weights"*. How many
stress points is a hero worth?

| | ratio |
|---|---|
| **training reward** | **3,333 : 1 to 5,000 : 1**, one constant for every hero |
| game, fresh recruit | **0 : 1** (free from the coach) |
| game, rank-2 crusader | 394 : 1 |
| game, rank-4 crusader | 1,474 : 1 (3,275 at an upgraded Abbey) |

**The reward's single number sits at the top of a range the game varies by three
orders of magnitude**, so the policy is trained to treat every hero as a
max-investment veteran — including the resolve-0 Apprentice parties
`TIER_TO_RESOLVE_WEIGHTS` draws 20% of the time. Over-protecting a cheap hero
buys caution with turns and turns cost stress, which is precisely the "plays too
slow and gets stress-defeated" failure Fran named, and is consistent with
`--stress-weight` reading as a straight HP-for-stress trade at par.

Separately: a completed expedition is 0.20 of a hero in the reward and ~0.10 in
the game, so the reward makes finishing about twice as attractive relative to
losing somebody as the economy does.

**NO CONSTANT WAS CHANGED, deliberately.** Gold is the town's currency and the
reward orders in-combat decisions; they need not share a scale for the ordering
to be right. What the table establishes is that one constant cannot express this
and that the one chosen is at an extreme. The cheap testable version is to scale
the death term by the hero's own investment, which the env already knows. It is
a from-scratch retrain and needs a paired arm on **two seeds** — do not sweep
another single weight before reading the table, which is the move that produced
three nulls.

Two numbers in the tool rot silently and are pinned by tests: `_reward_win` is
assigned in `__init__` and cannot be imported, and the terminal-stress
derivative divides by **200** (the ceiling) while `END_STRESS_SCALE` in the same
file is **100** (the Resolve Check).

**The real stress gap was the UNDERLEVELED-HERO PENALTY, implemented and
unreachable.** `UNDERLEVELED_STRESS_PER_LEVEL = 20.0` and
`UNDERLEVELED_STRESS_DMG_MULT_PER_LEVEL = 0.25` fire on `quest_difficulty_level`
-- and **no env has ever passed it**, so a resolve-0 hero on an Apprentice quest
is charged nothing while the real game charges +20 stress on entry and **+25%
stress damage for the whole raid**. `expedition_env.py`'s own comment predicted
exactly this case as a hypothetical. `TIER_TO_RESOLVE_WEIGHTS` draws 20% of
Apprentice heroes at resolve 0, so the distribution already holds the party the
penalty is for. The compounding half is the one that matters: a flat +20 would
leave the eval understating by a constant, while +25% widens the gap with every
stress event. (70.5% of logged stress rise happens inside fights, so it is not
travel stress; in-combat torch matches training, so it is not the dark.)

**`tools/skill_tiers.py` is the only ruler here that judges a CHOICE rather than
an outcome.** `run_score` and SUCCESS measure what happened;
`tools/agreement.py` measures agreement with recorded play, and its combat rate
was disqualified because that play was bridge testing. This measures the quality
of a decision against someone with no stake in the model.

- **17 classes x 7 = 119, and all 119 must resolve.** Musketeer, Duelist and
  Runaway are absent from the source by the author's choice, and an untiered
  skill is EXCLUDED from every rate rather than counted as average — scoring it
  as middling would flatter or punish exactly the three classes the list omits.
  `tests/tools/test_skill_tiers.py` fails if one display name stops resolving, which
  is the silent failure: the ruler would quietly measure less than it claims.
- **`explanations()` locates the expert's REASONING, and refuses rather than
  guesses.** The transcript is speech-to-text — "Calrops", "minles", "lens" for
  Holy Lance, "a bomb" throughout — so matching the full name finds 62%, the
  rarest token with a fuzzy fallback 92%, and **scoring every occurrence instead
  of taking the first brings it to 82% and makes those 82% right.** Open Vein's
  first "vein" sat inside a Suppressing Fire explanation. A misattributed quote
  appears next to a claim about the model, so precision beats recall here.
- **Nothing in `engine/`, `env/`, `data/`, `bridge/` or `train.py` may import
  it**, and a test enforces that. Fitting a policy to a tier list is fitting it
  to one person's opinion of seventeen classes, and it covers no modded class at
  all — it would transfer to the Sibyl as exactly nothing.

**`tools/style_probe.py` answers "does it play the comp, or one plan?"** It
buckets comps by the sustain their EQUIPPED kit carries and records every
decision by hooking the env's own `_action_map`, the way `comp_ga --explain`
does. Three findings, and the third is the one that matters:

| | LOW | MID | HIGH |
|---|---|---|---|
| run_score | 79.0 | **87.5** | 83.6 |
| heal taken when OFFERED | 20.4% | 21.5% | 23.8% |
| took best tier, within category | **57.3%** | 54.8% | **48.9%** |
| ...random baseline | 53.7% | 58.6% | **59.2%** |

- **Score is an inverted U, not a ramp.** "More sustain is better" was never the
  shape.
- **The heal take-rate is flat**, so the raw heal share rising with sustain is
  availability, not adaptation. One plan.
- **~~Skill choice degrades to 10.3pp BELOW random as sustain rises~~ —
  RETIRED 2026-08-30, on both seeds.** It holds on `forecastv2_20260825`,
  which still reproduces the table above exactly, and does NOT hold on the
  shipped checkpoint: the HIGH-sustain within-category gap is **-1.9 and
  +0.8** on the two decay seeds. The gradient flattened partly by getting
  WORSE at low sustain (+3.6 -> -2.1 / -6.9), so what replaces it is not a
  win: **within-category skill choice is about as good as chance at every
  sustain level.** Still a ruler, still never a target.

**Read the passed-over list, not just the rate.** The probe names the skills
that were the best-tier option in their own category, legal, and not taken:
Crusader's Inspiring Cry 1108 times, Highwayman's Open Vein 849, Hellion's If It
Bleeds 830, Leper's Intimidate — an **S-tier** skill — 479. On the shipped
checkpoint those read 568 / 754 / 832 / 465, but RAW COUNTS ARE NOT COMPARABLE
across checkpoints — a stronger policy plays shorter games, so there are fewer
decisions in which to pass anything over. They are almost all
stress-heal, torch, debuff and control: the policy under-uses exactly the
non-damage utility the expert rates highest, which is what "one plan" looks like
from the inside.

Three caveats that belong with any quote of these numbers: the tiers are one
person's opinion and unordered within themselves; the random baseline is uniform
over legal ACTIONS, so a skill legal against four ranks carries four times the
weight of an AoE; and the ruler grades WHICH SKILL and never WHICH TARGET.
