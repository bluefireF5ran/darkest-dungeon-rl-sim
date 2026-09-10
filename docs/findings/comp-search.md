# Findings: the comp layer and the search

The three provenance folders, `tools/comp_ga.py`, the rules Fran set on
what a comp may contain, and the bar a proposal has to clear. Moved
verbatim from the root `CLAUDE.md` on 2026-09-07.


### The comp layer: three folders by provenance, and the search (2026-08-22)

`metadata/COMPS/` is three things now, and only one of them is read as data:

| directory | what it is |
|---|---|
| `curated_comps/*.json` | **137 comps Fran played in the real game and vouches for.** That is what "curated" means here — not "scraped". |
| `composed_comps/*.json` | **6 machine recombinations of human-authored hero blocks**, for training coverage of a starved class. Nobody has played these and no fitness chose them. Read by `CompLoadoutLoader` alongside `curated_comps/`; kept apart so the word "curated" keeps meaning something. |
| `Comps/` | the Notion `.md` files — `CompsCsvLoader.load_modded_hero_comp_names` globs `*.md` here and it is what keeps the 11 fan-made-class comps out of the pool — plus 1242 PNGs nothing reads. |
| `GA_comps/` | `tools/comp_ga.py` output. **Read by nothing, deliberately.** |
| `Comps <hash>.csv` | **RETIRED 2026-08-23.** Read by nothing. It named 113 comps that no longer exist and its two structural bugs (alphabetised formations, deduplicated duplicate-class rosters) had been documented for months. Not deleted yet — that is Fran's call. |

`CompLoadoutLoader._json_paths` falls back to the old `Comps/*/*.json` layout
when BOTH `curated_comps/` and `composed_comps/` are absent, the same shape of
fallback as `csv_root`: a missing directory here yields a silently EMPTY store,
not an error.

**GA output is for Fran to PLAY; it is deliberately not training material.**
The fitness is `P(this policy completes the run)`, so promoting a comp the
policy scored well and then training on it closes the loop — the search would
be selecting comps this policy likes and then teaching the policy to like them
more. Training does not need optimised comps anyway: with `--kit-random-p` and
`--loadout-random-p` on, what survives as signal is the **class multiset and
the formation**. So the two starved classes were covered by comps *composed
from human-authored hero blocks* into `composed_comps/`, recombined into new class
multisets with no fitness anywhere — Duelist and Runaway now have 5 curated
comps each and **no class is under 5**. Recombination breaks two things, both
pinned by `tests/data/test_comp_loadout.py`: three comps each legally holding one
Ancestral trinket compose into a party holding **three** (the same rule
`enforce_trinket_budget` exists for), and a source slot whose display name
resolves to nothing composes into a hero wearing **one trinket or none**.

**Promoting a GA comp is a `mv` into `curated_comps/`** — the formats are
identical, and a test proves a GA file reads back through the real loader into
the same heroes, skills, trinkets and quirks. For a comp of ordinary classes it
and that is now the whole of it: since the CSV was retired on 2026-08-23,
`build_comp_pool` iterates `store.comp_loadouts`, so a file dropped into
`curated_comps/` trains with no other step. There is no longer a Fire's Edge
special case, and no archived `--comp-seed` left to disturb.

**The census that made this a two-class problem is CLOSED** (2026-08-26). Fran's
re-export took `curated_comps/` to 156 files and the trainable pool to **158**;
the smallest class is now **runaway at 6**, duelist at 7, against a max of 74
(houndmaster). No class is under 5 and `--require-class` is no longer the point
of a search. The zone skew survived: **crypts 84 of 158 (53%)**, cove 27,
warrens 24, weald 23.

The old census, for the record: over the 135-comp pool, 18 of 20 classes had
between 9 and 65 comps, Duelist had 2 and Runaway 1.

```bash
python tools/comp_ga.py --self-check --model <ckpt>          # must print all zeros
python tools/comp_ga.py --model <ckpt> --require-class duelist --workers 12
python tools/comp_ga.py --model <ckpt> --zones crypts          # one zone, 4x cheaper
python tools/comp_ga.py --model <ckpt> --no-structure-first    # the pre-2026-08-23 single-stage search
```

**The search is TWO-STAGE since 2026-08-23, and stage 1 does not search the
kit.** Trinkets are the widest axis (~98 unlimited choices per hero) and the
noisiest (24pp of spread), so a strong class multiset drawn with a lucky
trinket set outranked a better one drawn with a poor set. Stage 1 now ranks the
**class multiset and the formation** with every hero's kit **redrawn per
episode**, so kit luck integrates out; stage 2 is the existing order-then-
trinket polish on the winners, unchanged. Four rules that must not be
regressed:

- **The hall of fame keys on `structural_key` (ordered), not `archetype`
  (unordered)** while stage 1 runs. 45% of its mutations are `position`, so
  keying on the multiset discards every reordering it finds. Emission still
  dedups on `archetype`.
- **`STRUCTURAL_MUTATIONS` is `position` and `class` only.** A kit mutation the
  fitness is about to redraw costs a generation and changes nothing.
- **Kit pairing is weaker than dungeon pairing, and that is inherent.** Every
  candidate's episode *k* draws from `_kit_rng(seed, k)`, but different classes
  draw from different pools, so the stream matches the *decisions*, not the
  items. Stage 1 buys less ranking power per episode than the old fitness did.
- **`_episode_seed` strides by 1024** so a tuning seed's episode range can never
  reach the held-out seed's, and the stage-1 → stage-2 kit pick happens on the
  TUNING seed. Picking a kit on `validate_seed` and then reporting it is the
  overfit the held-out validation exists to prevent, moved one step earlier.

**The scenario is all four zones at Champion with everything maxed** (resolve 6
→ weapon/armour rank 5 → the top skill tier), Fran's call on 2026-08-22: a comp
scored in one zone is a comp *for* one zone, and the enemy pool differs per zone
as well as per tier. Measured on the round-3 winner, which read 90% in the
Ruins: **crypts 78% / warrens 22% / weald 62% / cove 45%.** That spread is the
bias the single-zone scenario could not see. Per-zone rates survive into
`measured.byZone` and set the comp's `location` field (the zone it scored best
in — a label on a noisy number, and the field `ExpeditionEnv` routes by, so it
is one edit to override). It costs exactly 4x.

Five rules that must not be regressed:

- **Fitness is `Rollout.evaluate`, and the pairing is what makes it usable.**
  Its scenario key is `zone|tier|combats|band|seed` and does not mention the
  party, so every candidate in a generation faces the identical dungeon list.
  Absolute rates stay noisy; the ranking does not. Same trick as
  `tools/ab_compare.py`.
- **The ranked quantity is `run_score`, and it is the OBJECTIVE** (changed
  2026-08-26; it was SUCCESS). `run_score = (quality/100) * progress`, per
  episode, where `quality` is `eval_expedition._run_quality` — 0 for a dead
  hero, else half HP and half stress over the roster — and `progress` is `1.0`
  if the expedition completed, else `combats_reached / num_combats`.
  **Quality alone is exploitable and would have been worse than SUCCESS**:
  `_run_quality` scores the party's state at episode end unconditionally, so a
  run that stalls out at combat 2 of 14 with everyone healthy scores ~90,
  above a party that cleared fourteen and came home at half HP. The progress
  gate is what stops the search selecting comps for doing nothing safely. A
  wipe is 0 either way; a retreat is not a completion.
  `run_score_se` is a **sample SD over √n**, not the binomial form —
  `standard_error` is still the binomial SE and is still SUCCESS's.
- **`by_zone` carries the RANKED quantity at index 1.** `emit._best_zone` picks
  the comp's `location` tag off it, and that field is what `ExpeditionEnv`
  routes by, so tagging the zone by one metric while ordering by another makes
  the label disagree with the ranking. Success rides at index 3 for printing.
- **Shaping can never outrank ONE EPISODE'S WORTH of headline.** `run_score +
  W * shaping`, `shaping` a weighted mean of four rates so it is in [0, 1], and
  `W = min(0.04, 0.8/episodes)`. The old guarantee was absolute — `success` is
  a rate, so `1/episodes` was the smallest gap two scores could have. A
  continuous headline admits no absolute bound, and the term is kept anyway
  because `run_score` is 0 for any total wipe however far the party got, so
  `combats_frac` inside the shaping is what orders two total failures.
- **Three seeds, not two.** Generations rotate through `seed + g`; the hall of
  fame is validated on `validate_seed`; the trinket polish tunes on
  `validate_seed + 1` and is then **re-measured on `validate_seed` and kept
  only if that agrees**. Polishing selects, so tuning and reporting on one seed
  reintroduces exactly the overfit the held-out validation exists to prevent.
- **`--model-b` cross-checks the emitted comps against a SECOND policy.** The
  fitness is `P(this policy completes the run)`, so a comp can score well by
  being easy for one policy rather than by being good, and a single-model
  search cannot tell those apart. The comps that get written are re-scored
  with the second checkpoint on the **same held-out seed and scenario** — a
  different seed would put the gap between two dungeon lists into the column
  too. **Reported, never selected on**: emission order stays the headline
  model's, because ranking on both makes the second model a second fitness and
  moves the problem up one layer.
- **The scenario has to discriminate, and which one does is a property of the
  POLICY.** `python tools/comp_ga.py --scenario-probe --model <ckpt>` is the
  four-way probe, and it must be re-run after **any** retrain. On 2026-08-21
  only tier 5 medium separated candidates, which is where the defaults came
  from. On the 2026-08-24 checkpoint that inverted: tier 5 medium a floor at
  3.1%, tier 5 long ranking nothing at all, tier 3 long the widest at SD
  32.6pp. A search left on the old default would have ordered its comps by
  noise.
- **The probe scores TWO populations and ranks a corner by the WORSE of them**
  (2026-08-26). It probed random candidates only — which is what generation 0
  looks like, and is exactly half the run. A search *converges* into the
  curated region, so a corner that ranks random comps beautifully and then
  saturates on good ones ranks noise for the generations that decide what gets
  emitted. On the shipped checkpoint the two arms **invert**: the random arm
  names tier 3 long (SD 19.0 against 4.6 at tier 5 long) and on the top 24
  curated comps tier 3 long collapses to SD 1.7 with **4.0 points of headroom**
  left above the best comp in the pool, while tier 5 long holds SD 12.9 with
  19.1. Both arms are normalised by **their own** mean standard error — a
  spread smaller than the error bar is not a spread, and the arms carry very
  different error bars at one episode count.
- **The answer for the shipped checkpoint is `--tier 5 --band medium`**
  (worse-arm SD/SE 4.2, headroom 20.1) — the tool's own default since
  2026-08-21, and the corner the 2026-08-24 note declared dead. It was dead
  **on SUCCESS**: 11.6% there, a floor. On `run_score` it reads 26.0 random /
  60.0 curated and separates both arms. The corner did not change; the ruler
  did.
- **Empty `hero_classes` on a camping skill means NO class.** `hobby` is the only
  one, and reading it as universal gives every class an eighth camping skill.
  Strict membership gives exactly 7 per class and 4 for the Flagellant, which is
  the real game, and no curated comp equips Hobby in 136 files.


### Sustain has TWO routes and the search knew one (2026-08-26)

Fran hand-upgraded two GA comps, changing **only the kit** -- same classes, same
ranks -- and both beat their originals paired at 64 episodes: **+17.5 ± 3.5**
(stress 39.1 → 11.9, deaths 0.42 → 0.06) and **+5.3 ± 1.4**. The first is the
largest effect this project has ever measured, fidelity fixes included.

So the Vestal finding sharpens: **sustain** lifts both the mean and the ceiling,
and sustain comes either from a healer CLASS or from a KIT. The search knew one
route, which is why every lineage converged on the same answer.

- **`trinket_set` is a mutation operator, and it has to be.** All 20 classes can
  complete exactly one set; `CombatEngine` has applied set buffs all along
  (`combat_engine.py:334`). A random pair reaches a set about **1 in 11,800**
  times, and a hill-climb cannot walk to one because half a set is strictly
  worse than two good unrelated trinkets — every path goes downhill first. Both
  halves at once or nothing, plus an explicit offer once per hero per round in
  `polish_trinkets`.
- **A GA comp with 0 one-off trinkets is a search failure, not a preference.**
  Both upgraded comps went 0 → 7 and 0 → 5.
- **`Hex Battery: Faith` is the pool's best comp at 92.6** — a hand-upgrade of
  GA output, above every proposal the search has produced. Treat that as the
  standing bar.


### The Vestal is a CONVERGENCE artefact, and `--ban-class` is the answer (2026-08-26)

Every emitted comp fielded a Vestal, and that was read as either a true property
of the game or a fidelity bug. Measured over the curated pool in the Weald at
Champion/medium, it is neither:

| | comps | mean | best |
|---|---|---|---|
| with a Vestal | 22 (14%) | 74.2 | **86.6** |
| without | 136 (86%) | 60.6 | **85.3** |

45% of the top 20 field her; **0% of the bottom 20** do; and the best
Vestal-free comp is 1.3 behind the best Vestal comp, inside the noise.
**She lifts the MEAN by 13.6 and the CEILING by 1.3.** A hill-climb converges on
whatever lifts the mean, so every lineage finds her in the first generations and
cannot leave — the other basin is just as high and much narrower to reach. That
is a property of the search, not the simulator, so do not open a fidelity
investigation into healing on this evidence.

**`--ban-class a,b,c` is the answer, and it is a strategy rather than a filter.**
Fran's method is sequential: emit a comp, ban the four classes it used, search
again. Each round is forced into unexplored roster, so a BATCH covers the roster
instead of recombining the same six strong classes.

- Enforced in `CompSpace.classes`; `enforce_ban` runs **first** in `finish`,
  because a banned hero is not one whose rank or kit is worth repairing.
- **A curated seed fielding a banned class is DROPPED, never repaired** —
  repairing keeps three quarters of a comp the search was told to avoid.
- Validated against the real class list, like `--hold-out-class`: a typo would
  ban nothing and the run would look like a successful experiment.

**Promotion is a `mv`, and it moves the training pool.** Fran promoted
`Hex Battery: Faith` out of `GA_comps/` into `curated_comps/` on 2026-08-26; the
pool went 158 → 159 and both size tripwires fired, which is exactly their job.
His own note on it: *"the upgrade is not always optimal"* — a promoted comp is
his current best guess, not a gold standard to fit the search to.


### The build ORDER, and the two constraints it made possible (2026-08-26)

Fran's round-2 review. He described a pipeline bug from the output alone —
*"it feels the model chose the trinkets first before having the skill loadout
and base comp ready"* — and he was right: skills were picked by keeping the
best of `--restage-kits` (6) whole-party random draws out of ~35⁴ ≈ 1.5 million
and never refined, while trinkets got dedicated hill-climbing.

**The order is now RANKS → SKILLS → TRINKETS**, in `CompSpace.finish` and in the
polish loop, and it is a **dependency, not a preference**: which rank a hero
stands in decides which skills it can perform, and which skills it performs
decides which trinkets can fire. `polish_skills` is the stage that did not
exist; `--polish-skills 0` restores the old behaviour.

- **A skill must be performable from a rank its hero can REACH**
  (`dead_skills` / `repair_dead_skills`). 30 of 128 equipped skills in the
  2026-08-26 output could not be — Hand of Light on a rank-4 Vestal in four of
  eight comps, one comp with 7 of 16 dead. Invisible to the fitness because the
  policy plays whatever is legal, so a dead slot forfeits only the better skill
  that could have been there. **Reachability is a FIXPOINT over the hero's own
  `move_self`** — Duelist's Advance from rank 4 opens 3, then 2, then 1, which
  is what makes Point Blank Shot legal on a back-rank Highwayman. Enemy pushes
  are deliberately not counted: Fran's rule is that off-rank coverage matters in
  proportion to the comp's own self-move, and the rest is a surprise contingency
  camping and scouting mostly prevent.
- **`viable_ranks` is the game's rank rules, DERIVED** — a rank is viable when
  the class has ≥ `SKILLS_PER_HERO` skills launching from it. Read off shipped
  data that gives Arbalest/Musketeer 3-4, Leper/Hellion/Crusader 1-2,
  Vestal/Plague Doctor 2-4, Shieldbreaker 1-3. **That agreement with common
  knowledge is the check that the derivation is measuring the right thing.**
  Enforced structurally, and `repair_ranks` prefers a **swap** to redrawing a
  class, because a swap preserves the class multiset the hall of fame keys on.
- **A trinket whose every buff is switched off is not worn**
  (`trinket_is_dead`). Fran: *"using ranged trinkets with a full melee
  highwayman doesnt make sense"*. Three `rule_type`s are decided before the
  trinket is chosen — `in_rank` (92 trinkets), `rangedonly` (36), `meleeonly`
  (31). NOT judged: `lightbelow`/`lightabove` (the torch belongs to the run),
  `monsterType` (the region decides it — filed, not guessed), unconditional
  buffs. **An unknown buff id is never called dead**: that is the loader
  missing something, not an inert trinket.
- **`polish_skills` needs a MARGIN (`SKILL_STEP_SIGMAS`, 1.0), not a bare
  improvement.** A skill swap is worth 1-3pp against a ~1.3pp standard error at
  64 episodes, so taking the max of ~48 candidates that are all inside the noise
  picks ~2σ of upward noise every round. The winner's curse is what would make
  the stage harmful rather than merely weak.

**FAILED HYPOTHESIS, recorded so it is not re-argued: region-pinning does not
make skill choice measurable.** The reasoning was sound — a skill strong in one
region and weak in three averages to nothing — and the measurement refuses it:
same comp, same 64 episodes, pinned to the Weald gives **sigma 1.3 against 1.4
for all four zones**, with the swap deltas still inside 2σ. Pin the region on
Fran's authority (*"the region would precede all"*, and a comp for one region is
a different object from a compromise), never on this argument.

**What replicated instead:** *Guard Dog* is the top-ranked swap in both runs
(+2.9pp all-zones, +2.6pp Weald) and the only one to clear 2σ in either — and
Fran had independently named it the comp's biggest miss, with the mechanism (a
dodge-built Houndmaster guarding the front rank dodges nearly everything). The
instrument and the expert agreeing on one specific call from different
directions is worth more than either alone.

**A low usage rate is NOT evidence against a skill.** `--explain` showing 0.8%
for Illumination was read here as an indictment and that was wrong: niche skills
are rare by design — Battlefield Medicine against bleed and blight and in the
stall phase after Blinding Gas, Disorienting Blast against size-2 enemies and
heavy backline dealers. Judge a skill by its **paired swap delta** and its
**rank legality**, never by how often it is played.

**`tools/comp_leaderboard.py`'s validity check is CONFOUNDED and must not be
quoted as proof.** "Curated 63.0 vs random legal 24.2" mixes comp quality with
policy familiarity: the shipped checkpoint trained on those 158 comps with the
randomisation knobs **off**, so it has seen no other loadout or trinket, while
the control is unfamiliar in every dimension. Fran: *"we're just evaluating
which of the comps the model knows to use best"*. The clean test is the same gap
measured on `knobs_20260825`, which trained with randomised loadouts and kits.


### What a comp may contain — Fran's rules, after reviewing the first output

He judged the first twelve comps and the verdict was that the *space* was wrong,
not the search. Each of these is enforced in code and pinned by a test:

- **Two trinkets per hero, always** (`TRINKETS_PER_HERO`). Never `min(2, pool)`:
  the first run emitted seven trinkets across four heroes. `CompSpace.top_up` is
  the single place that refills, and every path that can leave a hero short goes
  through it.
- **No two heroes wear the same one-off — the rule is PER ID** (corrected
  2026-08-26; it was a per-comp cap of one, which was a misreading of Fran's
  round-1 review). His words: *"the problem is not 1 ancestral trinket per comp,
  rather several copies of the same ancestral trinket"*. You own one Ancestor's
  Candle, so one hero wears it, and another may still wear Ancestor's Map. His
  own comps field up to five different one-offs. `limit` is the game's own
  field. Enforced comp-wide after **every** operator, because crossover is what
  breaks it — and `draw_trinket` can now OFFER a one-off
  (`ONE_OFF_DRAW_RATE` 0.20), which it could not while the cap existed, so the
  search could never build a party around several of them.
- **The class multiset is the comp's identity** (`archetype`). Two emitted comps
  never share one; the hall of fame keeps the best of each; the population caps
  duplicates (`--max-per-archetype`). Reordering is deliberately the SAME
  archetype, so the search explores ranks freely while reordered clones cannot
  reach a file.
- **Curated comps are a quarter of the seed population, not half**
  (`--curated-fraction`). They are a starting point, not the search space —
  five different funky comps teach more than five near-clones of the sample.
- **The search does not pick negative quirks** (`--negative-quirks` restores
  it). A player removes those at the Sanitarium rather than choosing them, so
  optimising them flatters every result.
- **Trinkets get a class-locked draw bias, a pair operator and a polish phase.**
  ~98 unlimited choices per hero against 35 skill loadouts is why one-swap
  search never found a combination; ~6 of those are built around the class's
  kit and a uniform draw reaches them 6% of the time.
- **And a synergy bias** (`SYNERGY_TRINKET_BIAS`, 0.25): a trinket whose
  chance modifier matches a payload the hero's **equipped** kit delivers. Only
  positive ones, keyed on the four skills actually chosen, so a Vestal with
  Dazzling Light is offered stun trinkets and the same Vestal with Illumination
  is offered none. A prior, never a restriction — measured, one such swap is
  worth ~1 sigma at 160 episodes, which is real and under what a generation can
  resolve, so the search had to be pointed at it.
- **Output is ordered by `metadata/HEROES/heroes.js`**, on ids before names:
  that file says "First Aid" where the string table says "Wound Care", both
  `first_aid`, and name-matching dumped the skill at the end of the list.

Two data facts the search depends on, both fixed the same day and both silent
before that: a comp's trinket display name can collide with a
localization-only id (63 curated slots equipped **nothing**), and two comp skill
names were spelled differently from the game's (23 hero slots held **three**
skills). Fran corrected the source files; the loader keeps both nets, and their
tests now assert they catch nothing. See `CHANGELOG.md`.


### The bar, and the check that the fitness is real (2026-08-26)

```bash
python tools/comp_leaderboard.py --model models/dd_combat_ppo_full_expedition.zip \
    --tier 5 --band medium --episodes 16 --workers 12 --seed 0
```

Scores **every comp in the trainable pool** on one scenario and writes a ranked
table plus JSON. It changes no file under `metadata/`. Two jobs:

1. **The bar** a GA proposal has to clear. Deliberately paired with the search —
   same `Scenario`, same `--seed`, so a curated comp and a candidate face the
   identical dungeon list rather than two independent estimates.
2. **A validity check on the fitness**, and this is the reason to run it
   *before* a search rather than after. These are comps Fran has played and
   vouches for. If they do not beat comps drawn at random from the same legal
   space (`--random-control`, default 24), the fitness is not measuring comp
   quality and no search fixes that.

Measured 2026-08-26, shipped checkpoint, tier 3 long, four zones, 16 episodes
per zone: **curated 84.1 (n=158, median 85.8, min 9.0, max 94.9) against random
legal 55.3 (n=24) — a gap of +28.8 ± 4.1, with 155 of 158 curated comps above
the random mean.** Between-comp SD 10.2 against a mean within-comp SE of 1.4,
so the ruler resolves real differences between them. PASS.

**Every comp score taken before 2026-08-26 is a different ruler**, twice over:
the fitness changed from SUCCESS to `run_score`, and Fran re-exported
`curated_comps/` the same day (100 added, 94 deleted, pool 152 → 158).


### Diagnosing a comp: `--explain` (2026-08-22)

```bash
python tools/comp_ga.py --explain metadata/COMPS/GA_comps/GA_duelist_01.json --model <ckpt>
```

Answers "why does this comp hold these skills and these trinkets", in the order
that makes the answers mean anything: what the policy **actually played** (it
hooks the env's own `_action_map`, so it records the decision rather than
re-deriving it), then **every legal skill swap** scored paired, then a sample of
alternative trinket pairs. It changes no file.

**The first thing it found was that Fran was right and the search was not
wrong.** He asked why a Vestal held Illumination; the policy plays it on 9.7% of
her turns, more than any of her other three, and every swap out of it costs
10-12.5pp. The cause turned out to be **DEBUFF RES never being rolled** — see
`ASSUMPTIONS.md`, now with a measured cost of 20 points and a two-minute
reproduction. A guessed fix was NOT shipped.

Two habits this established, both worth keeping:

- **"The policy never uses it" is a hypothesis, not an explanation.** Test it
  before building on it — here it was flatly false.
- **A delta smaller than twice the standard error is the fitness saying it
  cannot tell.** `--explain` prints one sigma at the bottom for exactly that
  reason.


### Two numbers to know before quoting anything this tool prints

- **The fitness cache is keyed on `(genome, seed, scenario)`.** It was keyed on
  the first two and silently returned a 20-episode score for a 40-episode
  request, which broke the polish phase's own re-measurement. A test pins it.
- **40 episodes ranks candidates; it does not measure a rate.** The top round-3
  comp reads **90.0% at 40 episodes and 80.6% at 160**. Trinkets alone are worth
  ~24pp of spread, and the search does find good ones — the polished kit beat
  the best of nine random re-kits of the same comp by 7.5pp at 160 episodes,
  over 2σ. Quote the standard error with the rate, always.


### Diversity has two levels, and the archetype is only the first

`archetype` (the class multiset) keeps emitted comps apart, and on its own it
was not enough: six strong classes recombined into thirty distinct foursomes and
one run of six comps used **six of the twenty classes**. Two more levers, both
Fran's ask:

- **`--max-class-share`** (default 0.30) caps how much of the POPULATION may
  contain any one class. The pinned `--require-class` is exempt, since capping
  it would fight the flag.
- **`--class-coverage`** (on by default) prefers, at emit time, a comp
  introducing a class not yet emitted — **but only from among those within one
  standard error of the best remaining**. Coverage is worth having and is not
  worth buying with real quality; the bound is the same inside-the-noise test
  used everywhere else here, and `--no-class-coverage` emits strictly by score.


### Three corrections to the comp search's SPACE (2026-08-28)

Fran's review of the Warrens batch. Each is a PRIOR bought from inside the
noise, never a cap, and each has an off switch reproducing the old search
exactly — the discipline the five training knobs follow, for the same reason.

- **`--no-prefer-unique`.** At emit, prefer a party of four different classes,
  applied after the coverage preference and bought only from inside one
  `run_score_se`. A duplicated hero "should be more like an exception rather
  than a rule" — but NOT a cap: his own pool is 27% duplicates including 4
  triples and 8 quads, so `--max-copies 2` would forbid comps he wrote.
- **`CompSpace.rank_fit(classes)` — the party-level formation measure that did
  not exist.** Every other rank rule is per-hero: `viable_ranks` asks whether
  one class can fight from one rank, `repair_ranks` swaps until nobody stands
  anywhere illegal, and **four front-rank classes pass both**. `rank_fit` takes
  the best assignment over all 24 permutations and averages how many skills
  each hero can launch from where it stands. Four front-rankers **5.00**, four
  back-rankers 6.00, a spread party **6.75** — it reproduces common knowledge
  unaided, which is the check that it measures the right thing.
- **`--rarity-bias` (default 0.35), GATED ON `rank_fit`.** Weights the class
  draw by inverse frequency in `curated_comps/` — Houndmaster fills 96 hero
  slots against Duelist's 6 — so rare classes surface without hand-banning each
  round. Fran predicted its failure mode before it was written: *"Hellion,
  Leper, flagellant and shieldbreaker all want pos 1, even if this comp is
  'possible' doesnt mean it will be good."* So a biased substitution is
  **refused** when it lowers the party's `rank_fit`. Measured: rare-class share
  34.3% -> 42.0% with mean `rank_fit` slightly UP.
- **`--kit-prior` (default 0.0, OFF).** Weights the quirk and trinket draws
  toward what the curated corpus equips — quirks exploit (if a few genuinely
  are better, learning them is correct), trinkets bias without narrowing (~98
  choices per hero is the widest axis and a prior strong enough to freeze his
  habits there would remove the only part of the search that finds him
  anything new). Off by default because of his own caveat: *"i havent innovated
  too much, nor change too much the quirks... its difficult to gauge this."*
  Run it as a paired arm against 0 and read the interval before trusting it.
