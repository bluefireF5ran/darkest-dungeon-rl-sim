# Findings: the rulers, and what they say

Instruments that grade the policy against something with no stake in
it. **None of them may ever become a training target** -- see
`tools/CLAUDE.md`. Moved verbatim from the root `CLAUDE.md` on
2026-09-07.


### PLAYBOOK compliance, measured on the shipped 1836 checkpoint (2026-09-20)

```bash
python tools/check_playbook.py --model models/dd_combat_ppo_full_expedition.zip     --episodes 24 --comps 10 --comp-seed 1
```

2,469 hero turns, 229 combats. The first reading of this ruler against the
world the 1836 checkpoint trained in, so the numbers are a baseline rather
than a delta -- four world changes separate it from every earlier run.

| rule | reading | verdict |
|---|---|---|
| **3.2** heal only at Death's Door / critical HP / stall | **35.5%** of 299 heals are NONE of those -- 106 turns, **4.3% of all hero turns** | the biggest single gap |
| **3.3** IN A STALL, kill secured | 50.0% attack, **44.6%** recover; the expert says most of them | second, and smaller than first reported |
| **CRP** attack a corpse | 5.9% of the turns it was possible; of those **23.1%** had a living enemy in the same skill's reach that would have died to the hit | 12 turns, 0.5% |
| **DOT** pile onto a doomed enemy | 45.8% against an indifferent 56.3% | **working** -- the policy avoids them |
| **3.5** buffs need long fights | median combat **2 rounds**, only 8.7% last >3 | buffs rarely pay here |

**The 3.2 number is a POLICY gap, not a reward gap, and the tool says which.**
Realized step reward on those turns: unjustified heal **-0.0820**, justified
heal **+0.1021**. The reward tells them apart by 0.18 and the policy plays the
negative one 106 times, so "the reward cannot express it" is ruled out and no
reward knob is indicated. What is left is training, or the decision layer.

**Masking a full-HP heal target was considered and rejected**: 10.4% of the
unjustified heals land on a hero above 95% HP, which looks like pure waste
until you remember DD heals carry bleed and blight cures, stress relief and
buffs. A mask cannot see the secondary effect, so it would forbid correct
play -- and it would change the action distribution, which makes the shipped
checkpoint incomparable (`tests/suite/test_obs_width_tripwire.py`'s sibling
problem).

**3.3's number was wrong when first reported, and the ruler was the reason.**
The criterion opens *"**En stall**, si X está aturdido y va a morir a manos de
Y o Z"* -- the stall is part of it. `check_playbook` gated only on
`_party_needs_recovery()`, which is one of the three tests in
`is_recovery_phase()`; the enemy count and `_stall_is_still_cheap()` were
missing. So it counted turns where the party was NOT stalling, and outside a
stall attacking is correct play, because the point is to end the fight. Fran
caught it: *"the recover part only matters when you are stalling properly per
se, not whenever every enemy is stunned."*

Gated properly, the situation shrinks from 75 turns to 56 -- **19 correct
attacks had been scored as violations** -- and recovery reads **44.6%**
against the 37.3% first reported. **Figures from before 2026-09-20 are not
comparable**: what changed is the situation being counted, not the policy.

`all_stunned` is still a proxy for "X is stunned and will die to Y or Z", and
a reasonable one inside a stall, where `STALL_PHASE_MAX_ENEMIES` usually
leaves a single enemy. That half is unverified and the correction did not
touch it.

**3.2 is SHIPPED as of the same day**: Fran chose the bridge-side shortlist
filter, and `Advisor._apply_playbook_32` demotes an unjustified heal below
every compliant move. On the capture corpus it changes **3 of 56 combat
decisions**, and agreement with the recorded play moves 30 -> 29 MATCH --
which is not evidence either way, because that corpus is plumbing-test play
and **MATCH is agreement, not correctness**. See `docs/changelog/2026-09.md`.
`--no-playbook-heals` restores the unfiltered arm.

**3.3 and CRP stay open.** 3.3 is the same shape and would want the same
ruling; CRP is 12 turns (0.5%) and strictly worse play, so it is cheap and
uncontroversial but small.

### The expert's tier list is a RULER, and it says the policy chooses badly (2026-08-28)

Fran supplied a skill tier list transcribed from a 9,000-hour player, plus the
42 KB transcript of him talking through every placement
(`metadata/HEROES/tier_explanation.txt`), with the same condition
`tools/check_class_play.py` carries: *"this is not to bias the model, rather as
a judge to evaluate if a model is playing good"*.

```bash
python tools/skill_tiers.py                       # coverage + the tier histogram
python tools/style_probe.py --model <ckpt> --episodes 6 --limit 60
python tools/team_probe.py --model <ckpt> --episodes 4 --limit 30
```

**`tools/team_probe.py` grades WHICH TARGET, the half `style_probe` says it
does not** (2026-09-02). Three rates, each against a **per-decision uniform
baseline** -- a bare rate has no scale here, because what is reachable bounds
what is possible. On the shipped checkpoint it **refutes** Fran's "each hero
plays its own game": follow-up attacks repeat a struck enemy **+7.3pp** more
than chance, and a hurt enemy is left standing **-20.0pp** LESS than chance.
Control is taken 4.8pp below its share of the bar, which is the half of his
review that survives -- and it is about the SKILL, not the target.

**The per-decision baseline is the whole method.** A per-ROUND one is biased by
the behaviour it measures: a focused party kills something and every later
attack is forced onto a different target. That version read "more spread than
chance" -- the opposite conclusion, and the one that would have agreed with the
intuition. Also: `Action.target_id` is a **RANK**, not a combatant id, and
resolving it wants `occupied_positions` so a size-2+ monster answers to both.

Ruler only, like the two above: `tests/tools/test_team_probe.py` fails if `engine/`,
`env/`, `data/`, `bridge/` or `train.py` imports it.

**`tools/play_log_probe.py` runs the same three metrics on REAL PLAY**, out of
the `bat/play.log` that `bat/combat_play.bat` has been writing all along -- no
simulator, and a corpus recorded while playing to win rather than while testing
the bridge. On 263 executed decisions: **FOCUS +8.1pp** (against the
simulator's +7.3, so it replicates), **CONTROL -10.0pp** below its share
(against -4.8, so it is WORSE where Fran saw it), FINISH -2.9pp. Its baseline
is the policy's own printed SHORTLIST rather than the legal set, so it is the
conservative form of each claim and FINISH in particular is not comparable
across the two tools.

```bash
python tools/play_log_probe.py                 # bat/play.log
python tools/play_log_probe.py --distribution  # what real play RAN IN
```

**`--distribution` compares real play against what the sampler draws, and the
first use of it produced a WRONG answer worth remembering.** Pooling every
torch reading in the log said real play sits pitch black 22.6% of the time
against the sampler's 0.0%. Fran rejected it -- the sampler draws **per
combat**, and most of those readings are travel. Split properly, **in-combat
torch matches training almost exactly** (63.3% radiant against 63.9%, mean 74.3
against 73.0); the darkness is where the party walks between the snuff and the
relight. A distribution is only comparable to another distribution of the SAME
quantity.

**WIRED 2026-09-02, along with torch 0 -- see the CHANGELOG. Every checkpoint
and comp score from before that date was trained and measured on a different
distribution.**


### THE FIGHT-LENGTH GAP DID NOT SURVIVE BEING CONTROLLED (2026-09-03)

```bash
python tools/stress_ratchet.py --no-sim          # the real arms, free
python tools/stress_ratchet.py --arms all --episodes 40
```

**Retracted the same day it was written.** The claim was "simulator fights are
half as long as real ones" -- real play 3.9 rounds and 50% lasting past round
3, against 2.0 and 9.2% -- and it was read as the structural explanation for
the policy skipping buffs and stress heals. It compared two things that
differed in three ways at once.

| arm | combats | mean rounds | >3 rounds |
|---|---|---|---|
| **A** sampled tier and band (the 2.0) | 357 | 1.92 [1.8, 2.0] | 6.7% |
| **B** tier 1 / short / crypts+weald / **4 heroes** at rank 0 | 119 | **2.68 [2.3, 3.1]** | 19.3% |
| **C** the same comps cut to **2 heroes** | 100 | **4.19 [3.4, 5.2]** | 47.0% |
| real play, **4 heroes, nobody fell** | 7 | **3.00 [2.3, 3.9]** | 28.6% |
| real play, 2 heroes | 1 | 4.00 | — |
| real play, a hero fell mid-fight | 2 | 6 and 8 | — |

**Matched on tier and party size, the two agree**: real 3.00 [2.3, 3.9]
against arm B's 2.68 [2.3, 3.1], intervals overlapping across most of their
width. The original 3.9 pooled 4-hero combats with a 2-hero one and two where
a hero fell mid-fight, and compared the result against an arm that pooled
every tier and never lost anybody. **Both differences push the same way**,
which is what made the gap look like a fidelity defect.

Tier accounts for 1.92 -> 2.68; party size for 2.68 -> **4.19**, the largest
single effect here. And the "independent replication" from the agent session
-- 4.0 rounds -- was a **two-hero tutorial party**, which arm C says the
simulator itself puts at 4.19. It replicated party size, not a gap.

**So the reconciliation is withdrawn.** A tier-matched simulator fight is ~2.7
rounds and a real one ~3.0; the policy is NOT "being correct about a two-round
world", and why it passes over buffs, control and stress heals is once again
unexplained. `tools/style_probe.py` and the expert tier list still disagree
with it and now have no structural excuse.

**What is genuinely open** is much narrower: n=7 on the real side cannot
exclude a modest difference, and the two long combats are the ones where the
party was short-handed -- the same mechanism arm C isolates, operating inside
a single fight. A bigger recording would tighten it; nothing here justifies an
engine edit, and the DEBUFF RES precedent stands.

**THE INTERVALS ARE OVER COMBATS, AND COMBATS CLUSTER.** They are bootstrapped
as if each fight were independent, and they are not: fights share an episode,
and episodes share a comp, and comp identity moves fight length a lot at rank 0
(four Runaways is not four Crusaders). Measured: arm B at 40 episodes reads
2.68 [2.3, 3.1] and the same arm at 5 episodes on another seed reads **1.60
[1.3, 1.9]** — two intervals that do not overlap for one arm. So every interval
here is too NARROW, and the right reading of the headline is direction and
magnitude, never a decimal.

It does not change the conclusion, and it is worth being explicit about which
way: wider intervals make the matched arms agree MORE, not less. What it does
rule out is quoting 2.68 against 3.00 as though the difference were resolved.

**The method is the transferable part.** Every arm carries a bootstrap
interval, including the real one, because ten combats cannot support a bare
mean and this comparison was quoted as one for a day. Read the **>3 share**
first: it is what PLAYBOOK 3.5's rule turns on and it survives a small sample
far better than a mean over a long-tailed count.

Two counting traps, both pinned by `tests/tools/test_stress_ratchet.py`: the round
counter alone **merges two consecutive one-round fights** (`1 < 1` is false),
which drops a short combat and raises the mean -- the direction that flatters
the claim -- so a wholly different enemy set ends a combat too; and the
simulator side takes its boundaries from `_combat_idx`, which is exact, rather
than inferring them the way a journal has to.


### ENDLESS MODE: how many battles before someone dies (2026-09-03)

Fran's idea: *"what if another metric is how many battles can the model hold
without someone dying, what if instead of dungeons of X battles, we go endless
essentially until the model loses someone?"*

```bash
python tools/endless.py --episodes 24 --cap 40
python tools/endless.py --model <A> --b <B>      # paired on the same scenarios
```

**SUCCESS measures the flat top of this curve, which is why it cannot resolve
anything.** Shipped checkpoint, 24 runs, cap 40:

| still whole after | 8 | 12 | 20 | 30 |
|---|---|---|---|---|
| | **96%** | 92% | **54%** | 33% |

Median battles before a death **22**; 19 of 24 runs lost someone, range 7-38.
A real expedition is 3-18 combats, so every eval this project runs sits in the
96-92% region — the part of the curve with no gradient. **The discriminating
region is 12-30 combats and nothing currently visits it.**

**It should also be the ruler on which the expert's advice finally pays.** A
stress heal, a buff, a control skill and an HP-conserving line all cost a turn
now for value later; over eight combats that value barely arrives and over
forty it is the whole game. `tools/style_probe.py` and the tier list have said
for weeks that the policy under-uses exactly those, every explanation has come
back null, and the fight-length one was retracted the same day it was written.
This makes the prediction testable on a metric where the difference has room to
show.

Three things that are load-bearing rather than incidental:

- **`allow_abandon=False` is not a default, it is the exploit being closed.**
  `expedition_sequence_env.py`'s own notes record a probe that abandoned 62% of
  runs "at a mean 61% party HP and **0.05 deaths**". A metric counting battles
  survived without a death is trivially maximised by quitting at combat one.
  The report shouts if any run abandons anyway.
- **Censoring is why the headline is a survival curve and not a mean.** A run
  that reaches the cap says the party lasted AT LEAST that long; averaging it
  in biases down, dropping it biases up. Kaplan-Meier keeps censored runs in
  the denominator for as long as they were at risk.
- **Only the LENGTH is overridden** — comp, zone, tier, gear, torch and
  provisions are the ordinary sampled draw. Provisions are NOT topped up, so
  the absolute number depends on `LENGTH_BAND_PROVISION_MULT`; that is a
  simulator convention, and another reason to read this comparatively.

**A comparative ruler, not a statement about skill**, and unusually so: the
policy trains on 8-18 combat runs and its value function assumes an end, so
past that it is extrapolating and a low number can mean "never trained for
this". Unlike fight length it can also never be validated against real play —
Darkest Dungeon has no endless dungeon. **Ruler only**, with the usual import
guard: training for a mode the game does not have is exactly what
`tools/check_class_play.py`'s rule exists to prevent.

Worth a second look: **2.83 retreats per run.** A retreat does not advance the
combat count (a fled room is re-taken), so it is not a way to survive the
metric — but it is a lot of fleeing, and nothing here explains it.


### The rulers on the new checkpoint (2026-09-03)

All four had never run against a 1652-wide model. `agreement.py --self-check`
PASSES (every delta exactly zero), and its algorithmic layers come back
identical to the recorded values — route 80.0%, torch 66.7%, curio 85.7% —
which is the consistency check passing, since those layers never consult a
checkpoint. Combat reads 53.5% and **is still disqualified**: that corpus was
recorded while Fran tested the bridge, not while playing to win.

**`team_probe`: the control criticism flipped sign.** FOCUS +7.3 → **+9.6pp**,
FINISH −20.0 → **−26.2pp**, CONTROL **−4.8 → +2.0pp**. Control below its share
of the bar was "the half of Fran's review that survives"; it is now above it.
Not a clean A/B — engine and training distribution both moved.

**`check_class_play`: Crusader healing 46.0% → 52.6%, against Fran's own
73.3%.** "The policy over-heals" stays unsupported by real play. What the table
does show is one skill per class: `shieldbreaker/pierce` **96.3%**,
`houndmaster/hounds_rush` 83.0%, `abomination/manacles` 65.6%, `arbalest` two
skills only — and `crusader/inspiring_cry`, an A-tier stress heal, at **1.4%**
against `battle_heal`'s 51.2%.

**`style_probe`: within-category choice is now BELOW chance** — LOW −4.2, MID
−16.9, HIGH −2.2pp. No gradient, so the 2026-08-30 retraction stands; but its
replacement ("about as good as chance at every level") does not.


### Stress is a RATCHET, and the simulator disagrees about the way DOWN (2026-09-03)

```bash
python tools/stress_ratchet.py            # sim vs bat/advise.jsonl
python tools/stress_ratchet.py --no-sim   # real play only, free
```

Fran: *"when a hero starts panicking usually its like a house of cards"*.
Graded against his own recordings, **both obvious readings are wrong**. It does
not accelerate (rises **+13.1** under 50, **+10.6** over it), and it is not
irreversible (34 falls; 1 of 7 heroes who crossed 50 came back). What is
lopsided is the **size**: falls **−4.4** against rises **+12.4**.

| | real play (n=8) | simulator (n=368) |
|---|---|---|
| P(recover) above 50 | **14%** | **48%** |
| mean fall | **−4.4** | **−6.9** |
| mean rise under / over 50 | +13.1 / +10.6 | +12.7 / +12.6 |

**The rises agree and the recoveries do not.** DIRECTIONAL ONLY — 8 heroes, and
the two arms sample at different rates (per recorded decision against per env
step), which biases toward catching more dips in the simulator. It says where
to look. **A bigger, rate-matched recording settles it; an engine edit on this
evidence would not** — the DEBUFF RES precedent is that a guessed fix does not
ship.

It matters because stress is half of `_run_quality` and `--stress-weight` came
back as a clean HP-for-stress trade at par. If recovery really is three times
cheaper here than in the game, that trade is an artefact.

A **RULER**, with the usual import guard. Its tests pin the arithmetic rather
than the finding, because the ways to get it wrong are subtle: a recovery needs
the high reading FIRST (`min < threshold < max` would count a hero who merely
climbed), and a rise is attributed to where it STARTED (attributing by endpoint
pushes every crossing into the high bucket and manufactures the acceleration
being tested for).


### Is an observation feature actually USED? Ablate it (2026-08-25)

```bash
python tools/ablate_forecast.py --model <ckpt> --episodes 2
```

One checkpoint, two passes on identical paired scenarios, the second with the
19 forecast indices zeroed. On `models/forecast_20260825.zip`: pooled McNemar
**p=0.0132** (69 sighted-only wins against 42 blind-only). **The first
observability change in this project the policy demonstrably uses** — the
mechanics-not-identity rewrite and the hero capability profile both came back
at +46.9pp and +46.6pp, i.e. changed nothing.

Three rules, each of which cost something:

- **Success is a paired BINARY outcome and wants McNemar, not a mean.** The
  first version of this harness put a 95% CI on *quality* and left success as a
  bare point estimate — and printed "the policy learned to IGNORE the forecast"
  while the success column showed +2.0/+3.3/+3.6pp in one direction on all
  three bands. **Quality cannot resolve a 3pp success change at n=304**; a 3pp
  drop implies only ~1.6 quality against a ±1.3 interval.
- **Use is not value.** Zeroing is off-distribution: a policy can be hurt by
  nonsense input it would never have been *helped* by. The control arm
  (`train.py --blind-forecast`, features zeroed at their source during
  training) is the only thing that answers whether the feature paid.
- **Indices are DERIVED from the layout constants, never written down.** Three
  pinned offsets broke on 2026-08-25 alone — `[-1]` for the heal headroom, a
  literal `-8` for the retreat chance, and `[-3]` for the party forecast, which
  forgot that `ExpeditionSequenceEnv` appends 8 context features *after* the
  base globals. `HERO_HEAL_HEADROOM_INDEX` and `RETREAT_CHANCE_FROM_END` exist
  so the next append is one edit.


## What the policy prefers, and how much of it is the recipe (2026-09-07)

`tools/model_preference.py`, both `mods54_knobs` seeds, 288 comps x 4 episodes
each: **166,259 and 168,527 decisions**. Fran asked which classes and skills the
model prefers, to put beside his own team-builder ranking.

**The unit is picks over the decisions where the skill was LEGAL**, read off
`_action_map` and deduped per decision, because raw usage is confounded by
availability and this project has already read a rare skill as a bad one once.
The base rate is **30.0%** (161,052 picks over 536,589 offers on seed 0), so
that is the no-preference line a rate should be read against.

**Thirteen skills are never taken by either seed with a real denominator.**
Suppressing Fire (**A tier**, 0 of 547 and 0 of 600), Sniper's Mark (C, 0 of
559/537), Smokescreen (0 of 314/313), Call the Shot, Juggernaut, Baneful
Breath, Foxfire, Heckling Gunshot, Inferno, Wither and Prosper, Arietta, Decon
& Dressing, Tracking Shot. Almost every one is a mark, a debuff or a party
utility, which is the same shape as Fran's own complaint that it *"outvalues
raw numbers and infravalues support skills"* (`tools/team_probe.py`).

**Two instruments agree, which is what makes it a finding and not a reading.**
`tools/analyze_behaviour.py` on the same checkpoint reports Arbalest at 4
distinct skills (sniper_shot, battlefield_bandage, flare, bola) and Musketeer
at 4 (aimed_shot, patch_up, skeet_shot, buckshot) — exactly the complements of
the zeros above — and Crusader without Bulwark of Faith, whose 0.0% is already
recorded in `tools/CLAUDE.md` from a different tool and a different run.

**Skill preference is a property of the recipe: rho = 0.84** between the two
seeds over the 235 skills with >=100 offers, mean absolute difference 12.0pp.
It is not uniform, though — Anticipation is 68.4% on one seed and 2.4% on the
other, and eleven more move by >30pp — so an individual skill's rate is
unreplicated unless it is checked, and the page built from this marks which.

**The class leaderboard replicates only where a class has comps.** rho = 0.65
over all 54 classes, **rho = 0.89 over the 21 that appear in >=10 comps**. The
top six by run score share only two entries between seeds, because 33 classes
sit in exactly 3 comps and their standard error is larger than the spread. Do
not quote a modded class's rank from one seed.

**The expert tier list barely predicts what the policy plays**, pooled over
both seeds, against the 30.0% base: **S 40.2 +-11.3** (n=5), **A 28.7 +-3.7**
(34), **B 28.3 +-2.8** (43), **C 16.2 +-3.2** (21), **D 23.6 +-3.9** (8). C is
the only clear signal and D sits above it, so this is not a monotone
relationship. S's interval is too wide to carry the top of the ranking on its
own. **Open**: whether that gap is the policy misplaying good skills or the
tier list rating skills the policy is right to skip is not decided by these
numbers.

**Do not turn any of this into a target.** A policy fitted to a usage profile
imitates its own past play, and one fitted to the tier column memorises
seventeen classes out of fifty-four — `tests/suite/test_rulers.py` carries the
entry.
