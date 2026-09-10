# NEXT — the pending queue

**How to use this in a new session: say "read docs/NEXT.md" (or just "pick up
NEXT").** Everything below is agreed work that is *not* finished. Each item says
what it is, what the evidence is, and the first concrete command. Delete an item
when it lands and put the evidence in `CHANGELOG.md` where it belongs.

Header rewritten **2026-08-29**; the top block is **2026-09-04**, then
**2026-09-03** and **2026-09-02**. Everything under the dated PHASE/N headings
below is older; read the top section first, because two of its standing claims
have since been overturned.

---

## What is in this queue

Derived from the h2 headings, which carry their own verdicts. Sections
this file marked SUPERSEDED / RETRACTED / RESOLVED / CLOSED / SETTLED /
PHASE CLOSED moved to `docs/next-archive.md` on 2026-09-07; nothing else
was reclassified, because mistaking an open item for a closed one is the
expensive direction.

- **THE MODDED CORPUS IS BUILT, AND THE TRANSFER EXPERIMENT IS RUNNING**  _2026-09-04_  <sub>77 lines</sub>
- **UNBLOCKED 2026-09-05 — the pixel layers see the game again**  <sub>42 lines</sub>
- **THE NEW METRIC, and the region no eval visits**  _2026-09-03_  <sub>51 lines</sub>
- **THE BRIDGE IS UP AGAIN**  _2026-09-03_  <sub>60 lines</sub>
- **RESUME HERE (2026-09-02) — the embark chain DECIDES; three gates keep it from flying**  <sub>286 lines</sub>
- **RESUME HERE (2026-09-01, later) — the post-tutorial phase is DONE**  <sub>31 lines</sub>
- **RESUME HERE (2026-09-01) — the night the bridge was flown**  <sub>68 lines</sub>
- **RESUME HERE**  _2026-08-31_  <sub>142 lines</sub>
- **PHASE OPENED 2026-08-26 — use the shipped model to propose comps**  <sub>71 lines</sub>
- **N1 — the capture session, and it is blocked on UI automation**  <sub>85 lines</sub>
- **N2 — bosses are built, trained against never, and measured never**  <sub>36 lines</sub>
- **N3 — the heal/recovery behaviour, now that it is a policy problem**  <sub>35 lines</sub>
- **N4 — the comp layer**  <sub>15 lines</sub>
- **N5 — ~~the slot-permutation probe~~ — RUN 2026-08-24, INCONCLUSIVE**  <sub>21 lines</sub>
- **N6 — the boss arm, running as of 2026-08-24 ~07:00**  <sub>15 lines</sub>
- **~~N5 old text~~ — superseded**  <sub>28 lines</sub>
- **Still open, and NOT queued**  <sub>22 lines</sub>
- **Sources the audit has swept, so they are not worth re-sweeping**  <sub>15 lines</sub>

Archived: 8 closed sections, 741 lines.

## THE MODDED CORPUS IS BUILT, AND THE TRANSFER EXPERIMENT IS RUNNING (2026-09-04)

Fran subscribed 50 Workshop classes and asked for a policy that plays them.
**The corpus exists: 267 comps over 47 classes** (20 vanilla + 27 modded,
median 3 hero slots each). The observation stays **1652 wide**, so the shipped
checkpoint still loads and this was a corpus job throughout.

`tools/class_visibility.py` is the new gate and `CLAUDE.md` has its rules. The
headline it produced: **36% of modded skills are invisible to the policy**
(all-zero payload) against 9% for vanilla, and five classes are 100% invisible.
Training on those is worse than omitting them.

### The experiment, and why it is not a fifth doomed attempt

Four mechanisms have failed at cross-class transfer. **All four changed the
model; this changes the data** — hold a class out of **47** instead of out of
19, so memorising the pool stops being the cheap solution.

And the premise none of the four ever checked is now measured: **a skill's
nearest neighbour in payload space is four times closer in a DIFFERENT class
(0.255) than in its own (1.032)**, 28% have a near-exact twin elsewhere, 55% a
close cousin. The shared structure transfer needs is real and rich. What may
have been missing all along is only the pressure to use it.

Arms, both 3M at the shipped recipe, `DDSIM_MOD_DIRS` set:

    models/mods47_control_s0.zip      the full 47-class pool
    models/mods47_heldout_sibyl_s0.zip  the same, minus sibyl_ms

    python tools/transfer_eval.py --model models/mods47_heldout_sibyl_s0.zip \
        --hold-out-class sibyl_ms --baseline models/mods47_control_s0.zip

`sibyl_ms` is the hold-out because it is MODDED (which is the goal), carries
**13 of the 267 comps** so the held-out half has some power, and has a prior:
the mods-on retrain took her from a -28% swap cost to -1%, so we know what
learned looks like for her.

**RAN 2026-09-04, BOTH SEEDS, AND IT IS CLOSED.** Excess gap **+22.5pp**
[+13.0, +32.7] on seed 0 and **+18.1pp** [+7.6, +30.3] on seed 1 — mean
**+20.3**, between-seed SD **3.1**, so the effect is 6.5x the spread and this
is a replication rather than one lucky arm. The policy that never saw the class
is decisively worse at it.

Both evals ran on the SAME 267-comp pool: `dis_trapper` and `spas` were
composed after the seed-0 arms trained, so their six comps were moved aside for
seed 1 and its eval, or a pool difference would have sat inside a seed
comparison.

**Few-shot per class is the permanent answer. Do not open a sixth attempt** —
the rule required a mechanism nobody had tried plus a reason it differed, both
were met, and it came back negative twice.

### Cheaper diagnostic that should come FIRST if this is picked up cold

Split a held-out class's skills into *twinned* (a cross-class match within
0.05) and *novel*, and ask which it fails on. Fails on both -> skill-level
transfer was never the issue and it is kit- or party-level. Fails only on novel
-> skill transfer already works. One eval, no training.

### Traps this session added

- **NEVER run two suites at once, and guard every long job.**
  `tools/safe_run.py` is not advisory: lock, competitor scan, RSS cap,
  free-memory floor, wall clock, and a working-tree scan for one-line boolean
  flips. A leftover mutation is a MEMORY bug before it is a red test -- an
  `and` -> `or` in `is_alive()` made combats never end, and three concurrent
  suites of that cost Fran four hours.
- **A mod's string table may lie about its own encoding.** Three declare
  UTF-8 and are not; the reader dropped the whole file silently and the
  symptom appeared two layers away as "its trinkets do not resolve".
- **`dis_trapper` and `spas` still compose nothing** -- 5 of 7 skills resolve
  to a display name. That is the last thing between 27 classes and 29.
- **The zone skew got WORSE**: crypts 55% of the pool, because a composed comp
  inherits its source's region. `--cross-region-p` is still 0.0.

---

## UNBLOCKED 2026-09-05 — the pixel layers see the game again

Fran attached a display. Verified live, with nothing clicked:

    window        Darkest Dungeon, client 1366x768, borderless
    display_is_live   True | the picture is changing
    grab          (768,1366,3)  min 0  max 254  mean 10.9
    recognise     title
    layout        resolves; every rank, slot, pack cell and menu point lands

Mean brightness 10.9 against the 11.2 this file records for a working session,
and three consecutive frames differ by 0.04 rather than 0.00 — so this is the
live compositor, not a frozen frame.

**The full-desktop borderless window is FINE, and the old worry about it was
wrong.** `overnight.py::_window_is_big_enough` already stopped inferring
blindness from window size and measures it with `display_is_live`; the RDP
precedent recorded there (borderless at the full 1366x768, capturing perfectly)
is exactly this setup. Client width 1366 clears `MIN_CLIENT_W` 1024.

**Steam must be running**, and is. Without it the game writes no saves and the
save-driven half of the bridge is blind whatever the screen does.

### What was NOT done, deliberately, and what it needs

Nothing was clicked and no campaign was loaded. Four profiles exist —
`profile_0` is Fran's own, last touched 2026-09-02 and mid-raid — and **Steam
being up is exactly the condition under which the delete/claim cycle is unsafe**
(Cloud restores a deleted profile). The standing rule binds here.

So the next session, with Fran awake or with his say-so:

1. load an EXISTING campaign rather than claiming a slot — `profile_9` is
   bridge-made, 8 files, not mid-raid, and is the safe one to drive;
2. get a dungeon on screen and re-check `mappanel.locate`, which is the layer
   that failed silently when the display collapsed ("8 cells where a fit needs
   a dozen");
3. then `bridge/run.py` in advise mode before anything acts.

The blocker was never the code. It is now a supervision question, not a
hardware one.

## THE NEW METRIC, and the region no eval visits (2026-09-03)

Fran's idea: run endless until the party loses someone.
`python tools/endless.py --episodes 24 --cap 40`.

| still whole after | 8 | 12 | 20 | 30 |
|---|---|---|---|---|
| shipped checkpoint | **96%** | 92% | **54%** | 33% |

Median **22 battles** before a death. **SUCCESS measures the flat top of that
curve**: a real expedition is 3-18 combats, so every eval sits at 96-92% where
there is no gradient, and the discriminating region is 12-30 where nothing
looks.

**The first thing to do with it is a paired A/B against the archived
checkpoints** — `--model A --b B`, same scenarios — because it is a comparative
ruler and has never compared anything. It is also where the expert tier list's
advice should finally show, since sustain pays over forty combats and not over
eight.

Two caveats that travel with every number it prints: `allow_abandon=False` is
load-bearing (abandoning gives "no deaths" and the env's own notes record a
probe that did exactly that), and the policy trains on 8-18 combat runs, so past
that it extrapolates.

**RUN 2026-09-05, and it is the first thing this ruler has ever compared.**
Modded checkpoint against the shipped vanilla one, paired on the same
scenarios, four seeds of 24 runs to a 40-combat cap:

| seed | median battles (mods/van) | whole @20 | whole @30 | retreats/run |
|---|---|---|---|---|
| 0 | 23 / 20 | 61 / 50 | 44 / 29 | 1.75 / 3.08 |
| 1 | 26 / 20 | 61 / 50 | 44 / 29 | 1.67 / 3.33 |
| 2 | 26 / 22 | 65 / 54 | 44 / 29 | 1.62 / 2.83 |
| 3 | 23 / 20 | 61 / 50 | 39 / 29 | 1.62 / 2.88 |

**Sixteen of sixteen comparisons favour the modded checkpoint**, and the
margin is largest exactly where the standard eval has no gradient: +11pp at 20
combats and +10 to +15pp at 30, against +0.0 / −0.4 / +1.3 on the three
standard bands. That is the case for this ruler existing.

Two caveats that travel with it: n=24 per arm, and endless is off-distribution
for BOTH policies, which trains on 8-18 combat runs.

**And it answers the retreat question below.** The vanilla checkpoint
reproduces the 2.83 (2.83-3.33 across seeds); the modded one nearly HALVES it
to 1.62-1.75, on every seed. Whatever a bigger, more varied corpus buys, part
of it is not needing to bail out.

---

## THE BRIDGE IS UP AGAIN (2026-09-03)

`models/dd_combat_ppo_full_expedition.zip` is `postfidelity_12M_s0`, 1652-wide.
`bridge/advisor.py --once` advises with **no `--model` argument**, so every tool
that defaults to the named slot works again. The 1636 predecessor is archived
with its width in the filename. Nothing below is blocked on a checkpoint any
more.

**The new baseline is 96.4 / 94.7 / 81.7 SUCCESS** (176 comps). The archived
97.6 / 96.6 / 90.1 **is not a control for it** — the fidelity batch, the comp
pool and the observation width all moved, and a 1636 checkpoint cannot be
re-run at 1652 to settle it. Record "new baseline", not "regression".

### DONE — the affliction arm came back HARMFUL

Four 3M runs, two seeds: SUCCESS **−7.05** mean of seeds against a between-seed
spread of **0.90**, QUALITY −2.07, deaths UP six of six, and **stress did not
move** — so the policy never learned to avoid the thing being charged for. It
prices an event the ratchet says is largely unpreventable, which makes it
variance rather than signal. Default stays 0.0. See `CHANGELOG.md`; do not
re-run it hoping.

**The follow-up is NOT a reward knob.** Make afflictions preventable first —
that is the stress-recovery question below.

### DECIDED by Fran 2026-09-03 — both stay OFF, stop asking

- **The survival floor.** `scale_floor_by_investment` stays **off**. His
  Shambler raid says a level-1 hero's death can be correct play and the floor
  refuses it unconditionally; the flag is implemented with a derived threshold
  (`mean_sunk > forfeited`) and he chose to leave today's behaviour in place.
- **Which world the eval should score.** `--underleveled` stays **off**, worth
  −1.4 / −2.9 / −5.7 SUCCESS. Training uses the harder world and the headline
  uses the easier one; the mismatch is visible and every archived number stays
  comparable. **Quote the un-penalised number as the headline and say so.**

Both were put to him directly and answered "leave both as they are". Re-opening
either needs a new reason, not a re-ask.

### Open, and needing DATA

- **The stress ratchet.** Real play recovers from above 50 on 14% of heroes,
  the simulator on 48%; falls are −4.4 real against −6.9 sim while the rises
  agree. Directional only — 8 heroes, and the arms sample at different rates.
  **A bigger, rate-matched recording settles it; an engine edit on this
  evidence would not.** `python tools/stress_ratchet.py`.
- **Combat agreement on real play — STARTED, 24 decisions.** Fran's four
  sessions produced 130 demonstrations and zero graded combat decisions because
  no checkpoint could load; the agent session added the first **24** once one
  did. Still far too few to grade a checkpoint, and more needs a display.
- **The fight-length arms are the template for the rest of this.** Two samples
  that differ in three ways cannot be subtracted, and both confounds there
  pointed the same way. Build the arm in the world the real data came from —
  `bat/advise.jsonl`'s `raid_started` events name it exactly — and put a
  **bootstrap interval on every arm including the real one**. Note the
  intervals are over combats and combats CLUSTER by comp, so they are too
  narrow: direction and magnitude, never a decimal.

---

## RESUME HERE (2026-09-02) — the embark chain DECIDES; three gates keep it from flying

`bridge/embarkplan.py` picks the quest, the curated comp, the formation, the
skill unlocks and the provision basket, entirely off the save, and sends
nothing. `python bridge/embarkplan.py --seed 0` prints it;
`bat/embark_plan.bat` double-clicks it. `bridge/town.py --embark-setup` walks
the whole chain in Fran's order and `--act` flies it.
`CHANGELOG.md`'s "THE EMBARK CHAIN IS DECIDED OFFLINE" has the four findings
and the evidence.

**What can already be flown end to end:** the estate map, the quest token (all
six dungeons, shipped geometry), clearing the party bar, seating four heroes by
combat rank, and the provision basket — priced from the shipped item file and
acknowledged per item against the wallet.

### Two things 2026-09-02 opened, neither blocking

- **"Skills only this week" has EXPIRED.** The plan's finding 3 rested on
  `blacksmith.weapon` code 0 requiring the building upgrade `blacksmith.weapon
  a`, against a wallet holding 0 deed. That upgrade is now BOUGHT — the live
  save carries six building purchases (`stage_coach.numrecruits` a and b,
  `rostersize` a, `blacksmith.weapon` a, `blacksmith.armour` a,
  `guild.skill_levels` a), which is what made
  `test_heirlooms.py::test_no_BUILDING_upgrade_appears...` go red and then get
  rewritten. The **resolve** gate still stands for ten of the twelve heroes, so
  gear is reachable for two of them at 750g a rank. Whether that beats a skill
  unlock at 1000g is Fran's call, and `embarkplan.py` deliberately does not
  guess.
  *First command:* `python bridge/embarkplan.py --seed 0 --all`, then read the
  two resolve-1 heroes' rows.

- **The hallway DoT tick is not modelled, and it is the one thing that makes
  the simulator gentler than the game here.** DoTs now cross the combat
  boundary (`rules.json`'s `dotHallwayStepRatio = 0.5` is the evidence), but
  the tick needs a tile count and `ExpeditionSequenceEnv` is a list of combats
  with no map between them. `scripts/map_generator.darkest` has the hallway
  lengths per size/quest/zone, so the input exists; wiring it means giving the
  sequence env a notion of distance it has never had. Measured today: **18.4%
  of combat boundaries have a hero holding a DoT** under random play, so the
  missing ticks are not rare.
  *Falsifier:* apply `ceil(tiles * 0.5)` ticks per boundary and compare a long
  run's total DoT damage against a real expedition's. Far above means the ratio
  is not per-tile-per-DoT the way this reads it.
  See `ASSUMPTIONS.md`, "Provisions: what a corridor can do".

### The three gates, each ONE live visit from opening

Every one of these is a coordinate that refuses rather than guesses. They are
listed in the order a single town visit would clear them.

1. **`BUILDING_SCENE_POINTS["guild"]`** — a building is entered by clicking it
   in the Hamlet scene, and only the stage coach has ever been measured. The
   scene x is derivable (hue-detect the red banner, match `pos.x / 1920`); the
   **y is not**, because the scene is a perspective projection with a
   per-building z. Same job that was done for the coach on 2026-08-31, one
   frame of a live Hamlet.
   *First command:* `python bridge/town.py --where` with the Hamlet up, then
   the hue measurement in `townlayout.BUILDING_SCENE_POINTS`'s note.
2. **`townlayout.GUILD_MEASURED`** — with the guild open, bank the panel and
   overlay `guild_skill_node(i, r)`. Expect a residual: the coach's derived
   nodes sat in the left margin until a frame moved them by (133.5, 35.3), and
   **no tree was drawn at all until the building crest was clicked**.
   `_visit_guild` already opens, banks and stops, so the frame arrives for
   free on the first `--embark-setup --act` once gate 1 is open.
   *Falsifier:* the derived rows do not land on the drawn skill rows, in which
   case the residual is measured off that frame the way `UPGRADE_NODE_OFFSET`
   was.
3. **Which element of a guild skill row is the SELECTION toggle** rather than
   the first purchase. `plan_buy_skill` and `plan_select_skill` compose the
   same row and differ only in the rank index, which is a guess until the
   banked frame says otherwise. This is Fran's *"set attacks for the comp"*
   and it is the only part of his sentence that is not yet decided.

### And one gate that is NOT one visit away

**`ROSTER_SCROLL_MEASURED`.** The town roster list shows `element_max` = 8 of a
12-hero roster, so a comp naming a hero on row 8+ cannot be seated at all.
`roster_scroll` composes the arrows off `top_frame_offset` /
`bottom_frame_offset` plus the row band (44 and 886 design px, bracketing rows
that run 180 to 859) and `act.plan_scroll_roster` refuses.

Refusing is right and the reason is worth keeping: a mis-scrolled list silently
**re-indexes every row**, so the drag still lands — on somebody else. Opening
this needs a frame *and* a way to confirm the scroll happened, and the honest
confirmation is the same one seating uses: the party bar's own pixels after the
drag. Until then `embark_setup` reports `SKIP` naming the row and the gate.

### The torch POLICY, which is not the torch actuator

The actuator is fixed and verified (`CHANGELOG.md`, "THE SNUFF WAS A QUARTER OF
A SNUFF"). The policy is not built. Fran, 2026-09-02:

    we should also stay at +25 torch most of the time outside of curio/rewards
    interaction, for example, we should never start a combat at 0 torch, but we
    should try to chain most curio possible at 0 torch ... in my opinion, we
    should buy extra torches and food to manipulate more the torch without
    reaching that extremes

`bridge/torchlight.py` answers exactly one question -- shut down for THIS
fight's loot roll, or hold -- and every branch is about a combat. What is
missing is the travel decision:

1. **a floor of 25 while walking**, relighting to it rather than to 100;
2. **snuff before a curio or a reward**, and stay dark while they chain;
3. **never enter a fight at 0** -- which is the relight trigger, and needs the
   route layer to say a battle tile is next;
4. **buy the torches that pay for it.** `data/provisions.py`'s GUIDE is
   PLAYBOOK 1.5 verbatim and Ruins/short is 8 torches at both ends of the
   range, which funds no manipulation at all. A "manipulation reserve" belongs
   on top of the table as its own named policy, with his sentence as the
   source -- NOT by editing his numbers.

**The extreme version is out of scope until scouting is read.** "clear all the
main combats, then backtrack at 0 torch to gather max resources" needs to know
which tiles hold battles before walking them, and the bridge does not use
scouting data yet. He does not recommend it either -- "in my opinion, we should
buy extra torches and food to manipulate more the torch without reaching that
extremes" -- so the moderate version is the target.

### Why real-play stress is higher than the eval says

Answered as far as the log can answer it (`CHANGELOG.md`, "WHY REAL-PLAY STRESS
IS HIGHER"), and the two things left are both cheap:

1. **Start the eval's party at a non-zero stress.** `ExpeditionSequenceEnv`
   starts every hero at 0; Fran's roster starts at **20** (13 of 15 heroes, and
   every big drop lands back there). Stress is a campaign quantity and the eval
   measures accrual from nothing, so its `16.8` and real play's `36` are not
   the same measurement. `PartyMember.stress` already exists and
   `bridge/embark.py` fills it from the live save -- what is missing is the
   eval drawing one.
2. **Report the per-hero stress DISTRIBUTION, not the roster mean.** A mean of
   16.8 is silent about whether one hero hit 100, and the log has a
   Shieldbreaker at 180. The eval already keeps per-episode `qualities`; the
   stress half needs the same treatment.

**A smaller, real torch gap survives the retraction**: 4 of 30 logged fights
(13.3%) OPENED at pitch black, and `TORCH_LEVEL_DECILES` starts at 3.0 so the
sampler reaches that band 0.0% of the time. Worth closing when the deciles are
next revisited -- it is a training-distribution change and therefore a
deliberate one -- but it is not the stress explanation and must not be sold as
one.

### The underleveled-hero penalty is implemented and unreachable

**No env passes `quest_difficulty_level`, so a resolve-0 hero on an Apprentice
quest is charged nothing** -- while the real game charges +20 stress on entry
and +25% stress damage for the whole raid. `TIER_TO_RESOLVE_WEIGHTS` draws 20%
of Apprentice-bracket heroes at resolve 0 under `--resolve-sampling`, so the
distribution already contains the party the penalty exists for. Full evidence
in `CHANGELOG.md`, "THE 20 IS THE UNDERLEVELED PENALTY".

*First command:* pass the sampled tier's difficulty into `reset()` from
`ExpeditionEnv`/`ExpeditionSequenceEnv` and re-run one eval arm. **It is a
training-distribution change** -- every Apprentice run with a resolve-0 hero
gets harder, so scores either side are different rulers. Do it deliberately,
like the resolve correction.

*Falsifier:* if stress on the Apprentice band does not move, the penalty is not
reaching the heroes and the wiring is wrong rather than the distribution.

### The ruler cannot see an affliction

`eval_expedition._run_quality` is half HP and half stress per hero, and counts
afflictions **not at all**. Fran rates an afflicted hero *"almost the same as a
death"*. So the number every comp score, leaderboard and checkpoint comparison
here is selected on is blind to an outcome just below the one it weights
heaviest.

Two decisions, and they are separable:

1. **The ruler.** Adding an affliction term to `_run_quality` re-scores every
   archived measurement -- so it is a new ruler, not a fix, and everything
   quoted across it needs re-measuring. Worth it only if the term is defensible;
   `W_AFFLICTED = 0.15` in the reward is not evidence for a weight, it is the
   same guess in another file.
2. **The reward.** `W_AFFLICTED` sits inside Phi, so by Ng/Harada/Russell it
   **cannot change the optimal policy** whatever its size -- only credit timing.
   Making avoiding afflictions actually worth something means a NON-potential
   term and a re-check of the invariance argument in `ASSUMPTIONS.md`, the same
   bar `--stress-weight` had to clear.

Also unmodelled and cheap to state:
`effectiveDifficultyAfflictionOnsetModifiers` `[0, .05, .1, .15, .2, .25, .33]`
is read by nothing, so a Champion run afflicts at the Apprentice rate.

### THE PRE-RETRAIN CHECKLIST (2026-09-02)

Fran: *"Are there other things to add to the combat model that happen in the
real game that we've not modeled before a retrain?"*

**The DATA sweep is done and must not be redone.** Engine audit rounds 7 and 8
(2026-08-23) walked every effect key, every buff `stat_type` and every monster
key in the shipped content this simulator fields; the standing note is *"the
constraint moved off fidelity -- stop proposing data sweeps"*. What is left is
not unparsed keys. It is three other kinds of gap, and they are ordered here by
what would actually move a retrain.

#### 1. Implemented, and UNREACHABLE (do these first -- the code exists)

* **The underleveled-hero penalty.** `quest_difficulty_level` is never passed
  by any env, so `UNDERLEVELED_STRESS_PER_LEVEL` (20) and
  `UNDERLEVELED_STRESS_DMG_MULT_PER_LEVEL` (0.25) never fire -- while
  `TIER_TO_RESOLVE_WEIGHTS` draws 20% of Apprentice heroes at resolve 0, which
  is exactly the party the penalty exists for. **This is the biggest one**: it
  hits stress, which PLAYBOOK 0.1 ranks second only to deaths, and the +25%
  multiplier compounds over a whole raid rather than being a constant offset.
* **Torch 0.** `TORCH_LEVEL_DECILES` starts at 3.0, so the pitch-black band is
  unreachable -- +35 stress chance, +40% stress damage, +5 monster crit. 4 of
  30 logged real fights (13.3%) opened there, because the bridge's own torch
  layer snuffs for loot. Smaller than it first looked (see the retraction), but
  structurally zero against a real 13%.

#### 2. Genuinely unmodelled, ranked by whether a retrain would notice

* **Affliction onset does not scale with difficulty.**
  `effectiveDifficultyAfflictionOnsetModifiers` `[0, .05, .1, .15, .2, .25,
  .33]` is read by nothing, so a Champion run afflicts at the Apprentice rate.
  Two readings of what it modifies and neither is obvious from the file, so it
  wants a decision, not a guess.
* **`stall_round_reset_threshold = 4` is unread**, and the two readings are far
  apart -- the stall counter resets after 4 clean rounds, or the stall penalty
  stops after 4 rounds of it. Today's behaviour is a third thing.
* **The virtue `block_effect`.** All five virtues ship it at chance 1.0.
  Parsed, deliberately not wired, because "a virtuous hero blocks every
  incoming effect" cannot be the reading and nothing narrows it.
* **`deaths_door_*_repeat`** -- every subsequent hit at Death's Door, or the
  second entry to it. Implementing the wrong one is a regression.
* **Scouting.** Every encounter is treated as unscouted; the `known_` surprise
  bases are parsed and unused. Worth noting that the bridge now HAS scouting
  data it does not use either.
* **Summons never roll initiative the round they arrive**
  (`.summon_does_roll_initiatives`, boss-only).
* **`DistrictLightBuffData`** -- the Illuminators Guild replaces the whole
  darkness range table rather than granting a buff. A table swap, so it is not
  a buff-wiring job.

#### 3. The two that would make a retrain pointless if left alone

Neither is a missing mechanic. Both are about what the retrain would be
optimising, and they matter more than anything in (2):

* **The ruler cannot see an affliction.** `_run_quality` is HP and stress. A
  retrain that improves it can improve nothing about the outcome Fran rates
  *"almost the same as a death"*, because the number does not contain it.
* **`W_AFFLICTED` sits inside Phi**, so by Ng/Harada/Russell it **cannot change
  the optimal policy at all** -- only credit timing. Whatever value it is given.
  Making afflictions worth avoiding is a NON-potential change and needs the
  invariance argument in `ASSUMPTIONS.md` re-checked, the same bar
  `--stress-weight` had to clear (and that came back null twice).

**So the honest ordering is: fix (1), decide (3), and treat (2) as optional.**
A retrain that lands (1) and neither of (3) will produce a policy that takes
more stress seriously and still cannot tell an affliction from a bad day.

### Then, in order

1. **Fly `--embark-setup --act` once, with Fran at the machine.** It clears
   gates 1-3 and produces the first real raid the bridge chose the party for.
   The tell that it all worked: the raid starts with the chosen comp in the
   chosen formation and **no underprepared dialog** — which is the party and
   the basket both being right.
2. **Fran's own comp file needs a look.**
   `metadata/COMPS/curated_comps/Ember_Trail__Cross.json` (untracked) lists
   **two** skills for its Plague Doctor where every other slot lists four, and
   the pool tripwires are firing on it: `test_comp_ga`,
   `test_comp_loadout`, `test_comp_pool_exclusions` and `test_expedition_env`
   all fail, three of them naming `('Ember Trail: Cross', 'plague_doctor', 2)`
   and the fourth reading pool 176 against a pinned 175. That is the tripwires
   doing their job; the fix is either two more skill names in the file or a
   deliberate bump of the three pinned counts.
3. **Phase 0.2 — the guild and the blacksmith BUILDING upgrades.** Still
   blocked on Fran (*"i'll talk about it later"*). Distinct from the skill
   unlocks above, which are per-hero and already decided.
4. **Dismiss the burnt hero.** Fran's step 8, phrased permissively (*"you may
   dismiss her"*), and the roster is FULL at 12 — the next batch of hires needs
   the slot. Not done: a dismissal is irreversible and he did not ask outright.
5. **`tools/overnight.py::_profile` has the wrong-path bug** that
   `bridge/town.py --coach` had — it assembles a profile under `SAVE_ROOT`
   instead of resolving one, so `read_town_state` silently falls back to
   whichever campaign was touched last. One line, same fix (`find_profile`).
6. **`plan_hallway_sweep` and `plan_snuff_torch` are still unflown.**

**`bat/` is how Fran drives it** — `bat/README.md` says which scripts send
input. `embark_plan.bat` prints and sends nothing; `embark_act.bat` flies it.

## RESUME HERE (2026-09-01, later) — the post-tutorial phase is DONE

Boot → tutorial → Hamlet 0.1 → two week skips all ran live. Roster **12**, the
Vestal home at 100 stress. `CHANGELOG.md`'s "THE POST-TUTORIAL PHASE RAN END TO
END" has the five bugs it caught and the evidence for each.

**`bat/` exists now** — double-click scripts for Fran to drive it himself.
`bat/README.md` says which ones send input and which do not; the two combat
ones (`combat_advise.bat`, `combat_play.bat`) are the same decision stack with
and without `--act`, so a disagreement between them is a bug.

### Next, in order

1. **Phase 0.2 — the guild and the blacksmith.** Both unlock after the week
   skips and Fran said he would spell out what to buy (*"i'll talk about it
   later"*). Nothing to build until he does; `townplan.UPGRADE_PRIORITY` is
   where it lands and `_tree_index` already derives the panel column.
2. **Dismiss the burnt hero.** Fran's step 8, phrased permissively (*"you may
   dismiss her"*), and the roster is now FULL at 12 — so the next batch of
   hires needs the slot. Not done: a dismissal is irreversible and he did not
   ask for it outright.
3. **`tools/overnight.py::_profile` has the wrong-path bug** that
   `bridge/town.py --coach` had — it assembles a profile under `SAVE_ROOT`
   instead of resolving one, so `read_town_state` silently falls back to
   whichever campaign was touched last. One line, same fix (`find_profile`).
4. **Tests for the new town geometry are still thin.** `party_slot_for_rank`,
   `region_point`, `RAID_ABANDON_QUEST` and the exchange icon probe against
   `captures/exchange/*.png` have no coverage; the week skip is currently
   pinned only by having been flown.
5. **`plan_hallway_sweep` and `plan_snuff_torch` are still unflown.**

## RESUME HERE (2026-09-01) — the night the bridge was flown

**Close Steam before starting.** Steam Cloud restores a save slot the loop has
just deleted (observed: deleted 00:10, back at 00:11:01), and once the claim is
marked released the slot is unownable forever. With Steam closed the delete
sticks first try. `tools/overnight.py --slot N` is the escape hatch if it
happens anyway; **never** delete by clicking the save list's red X.

```bash
python tools/overnight.py --max-iterations 20 --raids-per-campaign 2 --until 07:00
```

`--raids-per-campaign` must be **2 or more**: the town visit happens BETWEEN
raids, so 1 never reaches the Hamlet at all.

### What is verified LIVE, and what is not

| | |
|---|---|
| combat, route, loot, curio-on-a-sconce | working, MATCH-rate high |
| the stage coach **opens** | verified — `BUILDING_SCENE_POINTS` |
| the coach panel is **recognised** | verified — `stage_coach_panel`, separation 0.700 |
| recruit drag | geometry measured, **drag itself unflown** |
| heirloom trade | ungated — `EXCHANGE_MEASURED` True since the panel was banked |
| building upgrades | ungated — `UPGRADES_MEASURED` True; the crest opens the trees |
| the tutorial tent curio | still not interacting |

### The three open threads, in order

1. **The upgrade trees are not drawn on the coach panel.** Derived nodes land
   in its left margin. The crest beside the panel title is the suspect --
   `building_base_layout.upgrade_base_pos` agrees with the crest's measured y
   to **4 design px** -- and `townlayout.building_crest` is measured and
   deliberately NOT wired. The experiment is one click and one frame.
2. **The exchange panel's contents are unseen.** Its toggle IS measured
   (`HEIRLOOM_EXCHANGE_BUTTON`, the two-arrow glyph on the currency bar); what
   the panel then draws is not. Open it, bank a frame, compare against the
   derived `exchange_from/to/confirm` points.
3. **The tent.** `W` is right, the panel detector works (a sconce opened one
   in the same run), the battle guard works, and the position satisfies Fran's
   rule. `CURIO_NUDGE_HOLD` is now 0.25 s so the search resolves the ~40 px
   window instead of stepping over it -- unflown. If it still fails, capture
   what is on screen at the moment of the press rather than asking the sconce
   detector about it.

### Traps this night added

- **The party's map token MERGES two cells** if it straddles the gap, and the
  route then refuses to leave the room. Masked by hue in `find_boxes`; frame
  banked as `captures/map_party_token_merge.png`. This blocked the tutorial's
  first hallway, which every run must pass.
- **`dungeon_intro` is a LOADING GATE**, not an unresponsive screen. It is in
  `PATIENT_SCREENS` now; before that the night deleted campaigns that had
  booted fine.
- **The torch layer blocked the route.** `plan_room` checks torch first, so a
  relight the save would not acknowledge pinned the party forever.
  `_LayerDone` / `_torch_done` is the repair, and the rule is general: a layer
  that cannot act must hand over to one that can.
- **A widget derived from a layout file may not be ON the screen you are
  looking at.** The building navigation strip lives inside a panel, not on the
  Hamlet; the exchange panel does not exist until its toggle is clicked. Both
  cost a run before being caught. **Check a derived point against a banked
  frame before clicking it.**
- **The test suite used to destroy `archive/reset_claims.json`.** Fixed
  structurally by `journal_for(save_root)`.

---

## RESUME HERE (2026-08-31)

**The bridge plays campaigns unattended.** One command, and it needs nothing
from you:

```bash
python tools/overnight.py --max-iterations 200 --raids-per-campaign 3 --until 20:00
tail -f archive/logs/overnight/console.log
touch archive/logs/overnight/STOP        # ask it to finish the current one
```

It launches the game, boots a campaign into slot 1, plays the Old Road, walks
back to the Hamlet, provisions, embarks on a generated dungeon, plays that,
repeats, then deletes the slot and starts again. One runner at a time (PID
lock). It refuses to start beside pytest or a training run.

**Read `archive/logs/overnight/summary_*.json` first.** That is the point of
it: within three iterations it found two bugs nobody was looking for, and a
handful more after that. Each one is in `CHANGELOG.md` under the 2026-08-31
entry.

**State it was left in:** nothing running, no `profile_1`, `profile_0` and
`profile_8` (Week 22) byte-identical to `archive/save_backup_20260830/`.
Suite green at 1946.

### What to do next, in order

1. **Start the loop before doing anything else.** It accumulates evidence
   while you work, and everything below is cheaper once it has run for a
   while. Anything it reports as `stalled` banks a `*_stall.png` next to its
   log -- read the frame, not just the line.

2. **VERIFY THE STAGE COACH LIVE. It is built and it has never been run
   against the game.** Everything below it in this list is cheaper once this
   is confirmed, and everything above it in the file is already true.

   ```bash
   python bridge/townlayout.py     # the derived points + the three cross-checks
   python bridge/town.py --coach   # plan it against the running game, send NOTHING
   python bridge/town.py --coach --act
   ```

   The decision half and the geometry are both done and covered by 61 offline
   tests: `bridge/townlayout.py` derives every point from the game's own
   layout files, `data/heirlooms.py` solves the crest->deed trade,
   `bridge/townplan.py` picks the upgrades and the recruits, and
   `bridge/town.py::visit_stage_coach` sequences them and checks each against
   the save. `tools/overnight.py` calls it between `to_town` and `to_raid`.

   **What is unverified is the pixels.** The derivation agrees with three
   independently measured coordinates to within ~5 px, which is strong
   evidence it reads the files correctly -- but no derived point has been
   clicked yet. Run `--coach` without `--act` first and read the plan; the
   likely failure is a panel whose `.pos` is relative to something other than
   what `townlayout` composes it against, and it will show as a whole family
   of points shifted together rather than one wrong point.

   **The exchange widget's COLUMN ORDER is the one declared reading.** The
   layout file gives the geometry but not which heirloom is which column;
   `HEIRLOOMS` order is taken from the wallet and the rate file agreeing.
   It is checked against the save on the first trade, so a wrong column
   reports "the wallet did not move" rather than draining the wrong currency.

3. **Party assembly (DRAG).** Four heroes into the party slots on the mission
   page. `townlayout.party_slot(i)` is derived and tested; `embark.py` already
   ranks the party by simulation and returns it in formation order. What is
   missing is only the sequencing, and it is the same shape as
   `visit_stage_coach`. Until it lands, every embark goes through the game's
   own "without a full contingent?" dialog -- which is the tell that the party
   is short, and is currently just confirmed.

4. **The shop (2/10).** `town.py` buys a blind 8 food + 8 torches.
   `townlayout.shop_slot(i)` and `party_pack_slot(i)` are derived and tested
   (7 columns of stock, 8 of pack -- different strides, do not mix them), gold
   is already parsed, and `embark.py` can choose the basket by simulation.
   Keep the eight-food floor: DD warns below it in its own words, and avoiding
   that dialog is why the constant existed.

5. **The other buildings.** Every entry button is already derived
   (`townlayout.building_button`), so what each one still needs is a SCREEN
   REFERENCE for recognition -- `captures/screens/` holds eleven and not one
   building interior -- plus a decision rule. The references can be captured
   unattended: boot to the Hamlet, click navigation index 0-9, grab a frame,
   click `building_close()`. Each building changes who is available to embark,
   so each lands WITH its effect on the town advice.

6. **The tutorial's three gaps, all located, none needing new decisions.**
   - **The torch before the LAST combat only.** `torchlight.py:186` already
     knows the run's last fight makes a shutdown free and `run.py` already
     calls it with `about_to_win=state.in_battle`. `layout.TORCH_METER` is
     measured, banked and deliberately unwired, and the comment beside it
     names the change: it belongs in the BATTLE plan ("snuff, then swing"),
     not the room plan. `Step.mods` already carries modifiers, so SHIFT-CLICK
     and SHIFT+CTRL-CLICK need no new primitive. **Fran's caveat is the gate**:
     doing it on the first tutorial fight makes the second worse, and "last
     fight of the run" is exactly that condition.
   - **The tent.** `act.py` refuses with "camping needs the campfire button
     and skill picks". `bridge/camp.py` already decides with a real point
     budget; the campfire button and the skill grid come from the camping
     layout file, the same mechanism `townlayout.py` uses.
   - **The chest.** `act.py` refuses `curio == "use"`, which is **29 of 71
     captured curio decisions (41%)**. `W` is the BARE-HANDED interaction only,
     and pressing it on a curio that wants a key is actively wrong rather than
     merely useless. Using an item on a curio is a DRAG from the pack, and the
     primitive exists. Trap DISARM stays out of scope (`curios.py:35`).

### Traps that cost time today, so they do not cost it twice

- **The game MUST be restarted between iterations.** Deleting a save without
  restarting leaves DD's slot list cached and the next campaign comes out with
  0 gold and an estate named "Click the letter to begin..." -- the save-select
  hint text, captured as the name.
- **`-resetcampaigntutorials` or new campaigns skip the Old Road** and land in
  the Hamlet, while `run.py` reads the raid template and advises on `rooA` in
  a dungeon that is not loaded.
- **A drag cannot be confirmed by the save.** A pack rearrangement moves the
  item on screen with the mtime unchanged. The proof has to be pixels.
- **`bridge/act.py --geometry`** before trusting any click on a new machine or
  after moving a monitor. It exits non-zero above one pixel of error.
- **Nothing inside a building panel changes the SCREEN**, so `_await_change`
  is useless there and every town action is acknowledged against the SAVE
  instead. A click that did nothing and a click that worked are pixel
  identical on that screen.
- **A default argument binds ONCE, at definition.** `_await(reader, before,
  timeout=SAVE_ACK_TIMEOUT)` froze the timeout at 6 s, so a test that lowered
  the module constant still waited the full six seconds per action and hung
  the suite past two minutes. Resolve a tunable from the module global inside
  the function.
- **THE WORKSHOP SUBSCRIPTION LIST GREW FROM 12 TO 50 MODS on 2026-08-31**
  (every directory stamped 20:14), and `tests/data/test_mod_heroes.py` is gated on
  the workshop directory EXISTING rather than on `DDSIM_MOD_DIRS` -- so the
  suite's greenness now depends on what Steam has downloaded. 17 classes are
  discovered, 16 have the vanilla shape, and **`deovolente` ships 9 skills
  against the engine's 7 slots**. That is a real limit, not a loader bug: the
  extra two would be dropped in silence. The test was made a CENSUS (it names
  every offender instead of dying on the first) and is left RED on purpose --
  the fix is a policy call, either excluding an oversized class at load the
  way `build_comp_pool` excludes an unloadable comp, or accepting that this
  one mod is unsupported.

---

## PHASE OPENED 2026-08-26 — use the shipped model to propose comps

Fran's ask, in his words: *"now that our model can play several different
scenarios with comps, now i want to use it to generate new comps"*. Three
decisions he took at the start of it: rank on **QUALITY**, target **strongest
overall** (all 20 classes, all four zones, 8 comps), and **baseline every
curated comp first**.

What landed (evidence in `CHANGELOG.md`, invariants in `CLAUDE.md`):

- The search's fitness is `run_score = (quality/100) * progress`, not SUCCESS.
  Quality alone would have selected comps for stalling out safely.
- `tools/comp_leaderboard.py` ranks all 158 pool comps and checks the fitness
  against a random-legal control: **curated 84.1 vs random 55.3, +28.8 ± 4.1**.
- `--scenario-probe` now scores a curated arm as well as a random one and ranks
  a corner by the worse of the two. The random arm alone was pointing the
  search at a corner with 4 points of headroom left.
- The 2026-08-26 comp re-export broke 28 tests, all of them pinning a comp by
  name. `tests/_fixtures.py::comp_with_classes` resolves by class multiset now.

### Round 1 ran, Fran reviewed it, and round 2 rebuilt the search

`GA_strongest_0{1..8}.json`, all four zones, tier 5 medium. Best proposal
**87.9 ± 1.0** against a best curated comp of **89.2 ± 1.3** — level with the
top of the pool, not above it. Fran's review found the reason and it was
structural, not statistical:

- **The build order was inverted.** Skills were picked by keeping the best of
  six whole-party random draws out of ~1.5M and never refined, while trinkets
  got dedicated hill-climbing. Now **ranks → skills → trinkets**, with
  `polish_skills` as a real stage.
- **23% of equipped skills could not be used from their hero's rank.** Now a
  static constraint, with `viable_ranks` (derived, and it reproduces the game's
  own rank rules) stopping a Leper reaching rank 4 in the first place.
- **Trinkets that cannot fire are no longer worn** (`in_rank`, `meleeonly`,
  `rangedonly`).
- **Stress weighs 2:1 over HP in the search only** — his call, after the first
  batch came home at 38.8 stress against 19.8 for his own top eight.

`GA_weald_0{1..4}.json` is the first region-pinned run under the new pipeline
(before the stress reweighting): 0 unperformable skills, 0 dead trinkets, 0
misranked heroes, and every Vestal on the four skills he says every Vestal build
uses. Best **85.6 ± 1.7** against a Weald-best curated **86.0 ± 3.6** — level
again. `GA_weald2_*` is the same search with the stress weighting applied.

**Open, in order:**

1. **Fran rules on the emitted comps.** `GA_comps/` is read by nothing;
   promotion into `curated_comps/` is a `mv` and his call alone. Show them as
   comps, never lead with the score.
2. **He produces a human comp ranking.** The team builder's ranker now has a
   **comps** category (`#/ranker`), one region at a time, both parties drawn in
   full, exporting JSON that carries the loadouts. That is the only ruler with
   no model in it — and the reason it is needed is item 3.
3. **The leaderboard's validity check is confounded and needs redoing.**
   "Curated beats random" mixes comp quality with policy familiarity, because
   the shipped checkpoint trained on those comps with the knobs off. Re-measure
   the same gap on `knobs_20260825`, which trained with randomised loadouts and
   kits. Note the constraints already narrowed it: the control rose from 24.2 to
   31.3 once random comps stopped being malformed, so part of the original gap
   was measuring *broken*, not *unfamiliar*.
4. **The skill search cannot resolve what it is searching.** A swap is worth
   1-3pp against ~1.3pp of error at 64 episodes, and region-pinning does not
   change that (tested: sigma 1.3 vs 1.4). The constraints are doing the real
   work. Either buy ~7x the episodes for the skill stage, or accept that skills
   are chosen by rule and only ranked among already-sensible options.
5. **Fran's own domain rules are not encoded yet**, only the two that were
   statically checkable. Guard Dog's dodge-tank role is the clearest example:
   the instrument independently ranks it the top swap in this comp, so it is a
   real synergy the search has no way to reach on purpose.

## N1 — the capture session, and it is blocked on UI automation

**Five declared readings are unconfirmed against the running game**, each with a
written falsifier in `ASSUMPTIONS.md`: the DEBUFF RES *shape* (subtractive vs
two rolls), `deaths_door_*_repeat`, `monsters_size_*`, `BURN_BASE_DECAY`, and
whether the light comparisons are strict.

**What was established 2026-08-24** (full detail in `ASSUMPTIONS.md`):

- **Launch with `cwd` set to the game's install root.** From anywhere else it
  dies on a "could not open pin file" dialog that only a human can see, while
  `app.log` shows a normal boot that just stops. This cost an hour.
- Every determinism flag is accepted: `alwayshit`, `randomseed`,
  `enabledebugmonsterbrainoutput`, `enabledebugqueueoutput`.
- **`-skipfeflow` renders a black screen and hangs**, reproducibly, even on a
  save the game created through its own front end. `NEXT.md` used to blame a
  nonexistent slot; that is not it.
- A hand-copied `scripts/starting_save/` is not usable — but *not* because it
  has 7 files instead of 16. A fresh campaign the game makes itself also has 7.
- `profile_0` now exists as a real tutorial save (Fran made it).

**The blocker:** without `-skipfeflow` the game sits in the front end, and
`bridge/screen.py` returns `UNKNOWN` on the animated front-end frames.

**RESOLVED THE SAME DAY -- it was the SESSION, not the machine.** With Fran's
Tailscale session attached, `OpenInputDesktop()` returns a real handle,
`ImageGrab.grab()` returns a 1920x1080 frame, the game launches and holds a
window, and the entire boot-to-Hamlet loop ran unattended. The corpus is 14
references now, six of them captured live from that run. Everything below is
kept because the SYMPTOMS are exact and will recur in any session without an
attached desktop -- which is what a plain background/CI session is.

**The original finding, measured earlier on 2026-08-30 with no desktop
attached:** an automated Claude Code session has NO INTERACTIVE DESKTOP, so it
cannot do ANY of this. Confirmed three ways in one attempt:

- `Darkest.exe` launches and dies at **"Video subsystem has not been
  initialized SDL Error"**, showing a 201x92 `Error` dialog reading *"That
  operation is not supported"*. It does this whether launched from bash with
  the correct `cwd` or via PowerShell `Start-Process`, so it is NOT the
  pin-file/`cwd` trap recorded below.
- `OpenInputDesktop()` returns **NULL with error 5 (ACCESS_DENIED)**.
- `ImageGrab.grab()` raises **"screen grab failed"**, so `bridge/screen.py`
  cannot capture a frame at all -- and neither can `bridge/act.py` send input.

So the note below that extending the hash corpus "is not blocked on anything"
is **wrong for an automated session**. The corpus is fine: `--calibrate`
re-derives all 8 references from `captures/` with **worst separation 0.484** and
every capture classifying to itself at ~0.000. What is missing is live frames,
and live frames need a desktop.

**Everything in `bridge/` that reads the screen or sends input -- screen.py,
act.py, window.py, layout.py, run.py --boot, the capture session -- needs a
session ATTACHED TO THE DESKTOP.** That is the real condition, and Tailscale
satisfies it; "must be run by Fran interactively" was the wrong reading of the
same evidence. Offline paths (`--replay`, save decoding, `dson.py`, the
oracles) are unaffected either way.

**The tell, if it recurs:** the game dies at "Video subsystem has not been
initialized" before writing any `as_*` line to `app.log`. Check
`OpenInputDesktop()` first -- it is one line and it separates "no desktop"
from every other launch problem, including the pin-file/`cwd` trap below.

**The automation-shaped fix, and the actual next step:** extend
`bridge/screen.py`'s hash corpus to cover the current front end (title, save
select, difficulty, the campaign-name modal). That is not blocked on anything,
and it unlocks every future capture as well as `bridge/run.py --boot`.

```bash
cd "D:/Program Files (x86)/Steam/steamapps/common/DarkestDungeon" && \
  ./_windows/win64/Darkest.exe -profile 0 -skipvalidation -full_as_borderless 1 \
  -enabledebugmonsterbrainoutput -enabledebugqueueoutput -alwayshit -randomseed N
python bridge/screen.py --once        # currently UNKNOWN; that is the gap
python tools/dbwin_capture.py --selftest   # ALWAYS, before trusting a negative
```

**Question 1 also needs a ranked-up hero**, which the tutorial cannot give: at
`.chance` 1.0 the two readings are *mathematically identical* (both give
`1 - resist`), and every tutorial monster debuff is 100%. Use
`-autolevelupheroes -everythingunlocked` and a Houndmaster's Whistle (chance
1.7) or Occultist's Weakening Curse (1.4). Subtractive predicts it lands every
time; two rolls predict ~20-30% failures.

---

## N2 — bosses are built, trained against never, and measured never

**The largest content gap.** 37 boss encounter rows and **60 monsters reachable
only through them** — the Collector, Brigand Cannon, the Hag's cauldron,
Necromancer forms. `life_link` (a boss's adds die with it), the untargetable
cauldron and the summon machinery were all implemented and none has ever been
exercised by a policy or appeared in an eval.

The shipped checkpoint trained with `boss_quest_p=0.0` and every eval runs
corridors only. So roughly a quarter of the monster roster is unseen.

```bash
python train.py --full-expedition --timesteps 3000000 --seed 0 --n-envs 12 \
  --boss-quest-p 0.25 --out models/boss_20260825.zip
```

**Measured 2026-08-24, before any boss training: -28.9pp overall**
(corridor 70.6% -> boss 41.7%), the largest effect this project has recorded.
Deaths per expedition go 0.78 -> **1.87** and `old-succ` tracks SUCCESS in both
columns, so the failure is dying, not stalling or fleeing to safety. The content
itself is intact — 0 of the 66 boss-row monsters missing, 0 without brains — so
that number is the training gap at full size.

**The ruler, decided:** report corridor and boss as two numbers, never merged.
A boss-trained arm ships if it **gains on `--boss-quests` without losing on the
corridor eval beyond noise (±2pp)**. Grading it on the corridor eval alone would
punish exactly what it is buying; grading it on bosses alone would hide a
regression in the 90% of play that is corridors.

```bash
python eval_expedition.py --model <ckpt> --full-expedition --all-comps --episodes 4
python eval_expedition.py --model <ckpt> --full-expedition --all-comps --episodes 4 --boss-quests
```

---

## N3 — the heal/recovery behaviour, now that it is a policy problem

`tools/check_playbook.py` on the shipped checkpoint:

| | value |
|---|---|
| heals justified by NOTHING | **51.9%** (was 58.6%) |
| secured-kill turns spent recovering | **23.4%** (was 17.2%) |
| forfeited kills from attacking corpses | 40 |

The reward fix moved all of these the right way without targeting them, which
is why they are now a *policy* item rather than a reward item. But see the open
question below before touching the reward again.

**The corpse forfeit is not a reward mispricing — checked 2026-08-24.** In Φ,
breaking one blocking corpse is worth `W_BLOCKING_CORPSE / RANKS` = **+0.025**,
while killing a full-HP enemy out of four is `(1 - ENEMY_DEAD_SCORE) / RANKS` =
**+0.2875**. A kill is **11.5x** the corpse. The corpse break is also smaller
than the `TURN_PENALTY_THREAT` (-0.10) it costs, so it is net negative on its
own — exactly as designed, since `W_BLOCKING_CORPSE` is potential-based and
cannot change the optimum. The policy still spends 9.6% of its corpse-legal
turns on one and forfeits a certain kill in a third of them. Nothing to fix in
the reward; this is the policy leaving 11x on the table.

**Open, and first to look at if unjustified healing stops falling:** the 3.2
discrimination line flipped sign. Unjustified heals now score **-0.1921** and
justified ones **-0.2592**, where before justified scored better. By the tool's
own reading the reward can no longer tell them apart. The confound is large and
named in `CHANGELOG.md` — a justified heal happens in a worse position, and Φ
is no longer pinned at the combat boundary, so those surrounding losses now
reach the step reward. May be the measurement getting more faithful rather than
the reward getting worse.

---

## N4 — the comp layer

- ~~**Re-run the four-way scenario probe.**~~ **DONE 2026-08-24.** It is now a
  command, `tools/comp_ga.py --scenario-probe`, instead of folklore. Result: the
  polarity has inverted since 2026-08-21 — tier 5 medium is a floor at 3.1%,
  tier 5 long ranks nothing (SD 0.0), and **tier 3 long has the widest spread
  (SD 32.6pp)**. Use `--tier 3 --band long`.
- **Then the first real two-stage run.** The structure-then-kit search shipped
  2026-08-23 and has never been run against a loadable model. `--self-check`
  passes (all four deltas exactly 0.000000, both legs).
- Fran added 12 curated comps (pool 141 -> 152) and they are **still
  untracked** in git.

---

## N5 — ~~the slot-permutation probe~~ — RUN 2026-08-24, INCONCLUSIVE

Excess gap **-3.8pp** [-13.7, +4.2] against the standing +46.6pp, so the old gap
is gone — **but both arms score ~3% on seen classes**, because
`tools/transfer_eval.py` runs at tier 5 medium and the scenario probe found that
is a floor for this generation. No gap is measurable between two policies that
can both do nothing.

**Permutation costs 16 points of general competence** at 3M (456 paired cells:
-12.2 short, -16.4 medium, -19.7 long). Expected in direction, far larger than
expected in size, and enough that a fair test would need several times the
budget. The action-space theory is therefore **still untested**.

**Do not spend another long session on cross-class transfer.** The `mods/`
directory is empty and the fan-made-class comps are gone from the pool, so there
is currently nothing to transfer *to*. The line has consumed two observation
redesigns and four 3M arms for no usable result. If it is ever revived, the
best remaining idea is **sorting slots by payload** — a mapping that is both
stable and class-independent, where permutation removed the stability without
replacing it. Full write-up in `CHANGELOG.md`.

## N6 — the boss arm, running as of 2026-08-24 ~07:00

`--boss-quest-p 0.25`, 3M, seed 0, 12 envs, everything else matching
`control_20260824` so it is a one-variable comparison.

**What a boss fight actually is**, for reading the result: **29 distinct boss
parties** (not 35 — that was per-zone counts summed, and six parties appear in
more than one zone). Mean party size **2.3** against a corridor's ~4, **16 of 29**
contain a size-2+ monster, and **12 of 29** contain a `life_link`ed add — so the
"killing the boss removes its adds" rule, built and never exercised, decides
four boss fights in ten.

Grade per the ruler in N2: corridor and boss reported separately, ships only if
it gains on `--boss-quests` without losing more than ±2pp on corridors.

## ~~N5 old text~~ — superseded

Grade with `tools/transfer_eval.py` against the standing **+46.6pp**. Near zero
means the action-space theory is right and a slot-equivariant policy head is
worth building. Still large means that theory is wrong too and cross-class
transfer closes for good — which is a real result, not a failure.

**Grade them on the UNPERMUTED board, and that is deliberate.**
`bridge/rollout.py` builds its env without `slot_permute`, so `transfer_eval`
tests a permutation-trained policy against the canonical slot order. That looked
like the eval-env mismatch already found in `train.py` this session, and it is
not the same thing:

- Permutation is a **training-time augmentation**. Its purpose is to stop the
  policy memorising an index; at deployment the order is always canonical,
  because that is what the real game and `bridge/` present.
- The canonical order is one of the orderings the policy trained on, so it is
  in-distribution, not a shift.
- And the comparison target — the +46.6pp pair — was trained *and* graded
  unpermuted. Grading the new arms permuted would change two things at once.

So the question being asked is precisely the right one: *does permuting during
training buy transfer at deployment?* If a future session wants the other
number too (permuted policy on a permuted board), that needs a `slot_permute`
kwarg threaded through `bridge/rollout.py` — read `BRIDGE.md` first.

---

## Still open, and NOT queued

- **`docs/NEXT.md`'s own history**: the 2026-08-21 version's N2 recon note said
  question 1 was reachable from the tutorial monster-side. It is not — see N1.
- **Two camping conventions**, measured and deliberately left alone
  (`ASSUMPTIONS.md`): an `individual`-target camp buff lands on the frontmost
  living hero (261 equipped slots, defensible), and stress urgency saturates at
  100 though the heart attack is at 200.
- **The camp schedule.** Three quarters of the second campfire is never spent
  (33 of 40 long runs reach combat 5, only 10 reach combat 10). A need-gated
  window was implemented, measured **worse** (stress removed per camp 31.1 ->
  10.6) and reverted. The low reach is the policy dying, not a scheduling bug.
- **`tools/agreement.py`'s combat rate is void** — the corpus is bridge-testing,
  not real play. Its route/torch/curio layers still stand. Re-recording during a
  serious run is what would restore it.
- **The two-save question is settled for this checkpoint**: `best_model` vs the
  final save is +0.5pp [-1.8, +2.8] over 456 paired cells. `CLAUDE.md` still
  carries a long section about them disagreeing by ~9pp; that was the unpaired
  eval's noise.
- Out of scope by decision: curios, scouting, dungeon traversal, the town, the
  Crimson Curse, the Hag's hero-capture.

## Sources the audit has swept, so they are not worth re-sweeping

dataclass fields; `rules.json` scalars; hero and monster `.darkest` keys; the
effects file; buff `stat_type`s and `rule_type`s; monster-brain desires and
gates; traits; **and camping, as of 2026-08-24** — all 7 skill keys and 6 effect
keys across 6 files and 150 records, with the one unparsed key
(`class_specific_number_of_classes_threshold`) shown to be inert.

**Not swept:** `.mash.darkest` columns and curio tables. Both sit outside the
combat loop, and curios are out of scope entirely.

**The method note worth keeping:** rank a candidate by what a real expedition
*equips* or *fields*, never by how many definitions exist. And one axis of a
data file is not the file — `stat_type` and `rule_type` sit on the same
dataclass and sweeping one said nothing about the other.
