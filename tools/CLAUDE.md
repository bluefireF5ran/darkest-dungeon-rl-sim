# Contracts for `tools/`

Diagnostics, oracles and one search. Moved verbatim from the root
`CLAUDE.md` on 2026-09-07.

**The ruler rule.** A ruler grades the policy against something with no
stake in it, so nothing the policy is built from may import one --
`engine/`, `env/`, `data/`, `bridge/` and `train.py` are all guarded by
`tests/suite/test_rulers.py`, which names each ruler with the reason
fitting the policy to it would be wrong.


### Diagnostic scripts (how behaviour regressions actually get found)

Evals give aggregate rates; these answer *why*. Reach for them before guessing at a policy problem.

```bash
# Per-turn action-frequency breakdown per hero class — surfaced "Crusader heals on 73% of
# turns", which drove the tempo/stress reward redesign.
python tools/analyze_behaviour.py --model models/dd_combat_ppo_full_expedition.zip --episodes 30 --dump-log out.log

# Blow-by-blow combat log for one comp — the replay tool that confirmed the ally-damage
# and bogus-reinforcement bugs. --list prints selectable comp names.
python tools/battle_report.py --model models/dd_combat_ppo_full_expedition.zip --comp "Riposte Style (Hag)" --combats 1

python tools/analyze_coverage.py                 # which comps/zones/skills a sweep actually exercised

# Does the policy play the way an expert says is correct? Each check names a PLAYBOOK.md
# rule and prints the number that falsifies it. Found the reactive-healing violation
# (50.5% of heals justified by nothing) and the pile-on-a-secured-kill violation.
# Two extra blocks are NOT playbook rules but the 2026-08-15 bundle's own falsifiers:
# DOT (attacks aimed at an enemy already dying to its DoT — what `_is_doomed` exists to
# lower) and CRP (attacks aimed at a corpse — 0.0% means the newly-legal action was
# learned away, not that the situation is rare; a corpse is targetable on ~15% of turns).
# DOT is a DIFFERENT situation from 3.3, whose "secured" means every living enemy stunned.
python tools/check_playbook.py --model logs/full_expedition/best_model.zip --episodes 36 --comps 12 --comp-seed 1

# One line per eval log — compare checkpoints to spot a plateau without re-reading full tables.
# Reports SUCCESS, not the zero-death column (which counts stall-outs as survivals).
python tools/summarize_eval.py archive/logs/eval_6M.log archive/logs/eval_wikifix_3M.log

# The way to compare two checkpoints when you need a paired CI and McNemar.
# `--full-expedition` alone is still NOT reproducible (it builds its env with
# sampler_seed=None); `--all-comps` IS, since 2026-08-24, so for a plain
# before/after on one checkpoint that is now enough.
# Measured before the fix: the SAME checkpoint scored 41.7% and 33.3% on
# back-to-back identical `--full-expedition` runs.
# ab_compare reseeds per scenario, plays BOTH checkpoints on the SAME dungeon list,
# and reports a paired delta with a bootstrap CI + exact McNemar.
python tools/ab_compare.py --a logs/full_expedition/best_model.zip \
                           --b models/dd_combat_ppo_full_expedition.zip \
                           --episodes 6 --comps 12 --comp-seed 1
# Run this after touching the env or the tool. It plays one model as BOTH arms;
# every delta must be exactly 0.0, or no comparison the tool reports means anything.
python tools/ab_compare.py --a <ckpt> --self-check

# The OTHER ruler, and the only one with no simulator in it: how often does a
# checkpoint advise the move that was really played, over the captured corpus?
# Paired per decision, keyed on the SAVE (round, whose turn, what was played) so a
# change of checkpoint cannot shift the keys -- it refuses to print a delta if the
# two arms did not face identical decisions. ~101 combat decisions; free to re-run.
# MATCH is agreement, NOT correctness: a DIFFER can be the policy outplaying the human.
python tools/agreement.py --a <ckpt>                      # one checkpoint, per layer
python tools/agreement.py --a <ckpt-A> --b <ckpt-B>       # paired + McNemar
python tools/agreement.py --a <ckpt> --self-check         # must be all zeros

# THE OLD ROAD, and the only harness here that is exactly reproducible: the
# scenario is fixed (hand-authored map, fixed party, two named encounters), so
# the only thing varying is the combat RNG and that is seeded per episode. Two
# checkpoints therefore face identical fights. `--self-check` must print 0.00.
python tools/eval_tutorial.py --a <ckpt> --b models/dd_combat_ppo_tutorial.zip
```

**`tools/agreement.py` IS NOT A RULER FOR CHECKPOINT SELECTION — Fran, 2026-08-23.**
The captured corpus in `archive/traces/` was recorded while he was *testing the
bridge and the live model*, not playing to win. So a MATCH means the policy
agrees with a move made to exercise the plumbing, and the 39.6%/47.5%/35.6%
combat figures measure agreement with test play. The tool is still the right
instrument — paired per decision, keyed on the save, no simulator anywhere — and
its **route (80.0%), torch (66.7%) and curio (85.7%)** layers are still worth
reading, because those decisions are the same whoever is at the keyboard. But do
not use the combat rate to choose between checkpoints, and do not quote the
"two rulers disagree" table below as an open question: one of the two rulers was
measuring the wrong thing. Re-recording the corpus during a serious run is what
would restore it.

**The two rulers currently disagree about which checkpoint to ship** (see the table above
and `CHANGELOG.md`). They also answer different questions, so that is a finding rather
than a contradiction — but do not quote one as if it settled the other.


### Reading the binary itself (`DECOMP.md`, started 2026-08-19)

`Darkest.exe` is 19.3 MB, has **no PDB** and **no Steam DRM wrapper**, and MSVC left
**596 RTTI class names** in it (`Battle::CombatActionProcessor`, `Actor`, `MonsterClass`,
`AI::MonsterBrainClassLibrary`). The combat *maths* is non-virtual and therefore unnamed,
so the way in is the config keys: `data/*_loader.py` already enumerates every key the game
reads, and nearly every one appears in the image exactly **once**, at its parse site.

`tools/pe_anchors.py` needs nothing installed — pure Python over the PE — and turns a key
into the addresses that reference it, which is where to point a decompiler.

```bash
python tools/pe_anchors.py --sections
python tools/pe_anchors.py --rtti --grep Battle    # the C++ class names in the image
python tools/pe_anchors.py --key cutoffAlwaysMiss  # string address + code xrefs
python tools/pe_anchors.py --keys                  # the whole anchor table
python tools/pe_anchors.py --telemetry             # what the game LOGS for free
```

**The whole program is decompiled (2026-10-04)**, both pinned builds, under
`D:\ghidra\out\<sha8>\` with indexes; `DECOMP.md` says what is in it.

```bash
python tools/ghidra_export.py --all            # name RTTI methods + decompile everything
python tools/ghidra_diff.py <old export> <new export>   # map addresses across a patch
python tools/decomp_lookup.py --string combat_retreat_chance   # who reads it
python tools/decomp_lookup.py --fn Battle::ApplyHit --callers  # C, callers, callees
python tools/rules_audit.py                    # every rules.json key: parsed? read? by whom?
python tools/damage_oracle.py --list           # F9 against the running game (record_play_full.bat)
```

**`rules_audit.py` is the F1/F7 hunt automated, and it re-finds both on its
own.** "Parsed, unread" is a lead, not a verdict: a struct read through a
pointer is invisible to a text search. `data/decomp_facts.json` is where a
confirmed fact goes, with the repo constant it governs; its test runs without
the export.

**A game patch moves every address, and the tools were split across it for
three weeks.** `pe_anchors.py` reads the install; the Ghidra program is a
pinned copy. `ghidra_export.py --key` now refuses when the two are different
builds. After a patch: pin the new exe as `Darkest_<sha8>.exe`, import it,
`--all` it, and run `ghidra_diff.py` before trusting any cited address.

Three rules that cost a wrong answer each on the first run, all enforced in the tool now:
**a key is stored in the form its own file uses** (`cutoffAlwaysMiss`, but
`combat_move_skill:` with the colon and `.per_battle_limit` with the dot); **"absent" is
only evidence for a key the shipped data actually contains** (an invented key name is
absent too); and **a literal with no xref is not unreferenced** — the `.darkest` field
names sit in one table the parser walks from a single base pointer.

Ghidra 12.1.3 lives at `D:\ghidra`, with its project **outside the repo** (multi-GB).
Findings cite the pinned copy `D:\ghidra\bin\Darkest.exe`
(`sha256 e95ff9f0…52cfd`), never the install path, so a game patch cannot silently change
what an entry refers to.

**No formula is adopted from decompiler output alone.** It has to predict a number the
running game then produces under the determinism flags below. That loop is the whole point
of having both.


### The game-trace oracle — ground truth from the real game

`Darkest.exe` (release build) still emits combat debug traces over `OutputDebugStringA`. Capturing them turns the actual game into a fidelity oracle, which is how the shared-AoE-hit-roll gap was found. See `CHANGELOG.md` for what the stream contains.

Enable via **Steam → Properties → Launch Options** (the `HKCU\...\Darkest Dungeon\commandline` registry key is *not* read by the game):

```
-enabledebugmonsterbrainoutput -enabledebugqueueoutput
```

```bash
# Validate the capture path with no game running. Always do this before trusting
# a negative result — otherwise a tooling bug is indistinguishable from
# "the debug calls were compiled out of the release build".
python tools/dbwin_capture.py --selftest

python tools/dbwin_capture.py --probe --process Darkest.exe --out trace.log --duration 600

python tools/game_trace.py trace.log --summary   # event counts + RNG integrity check
python tools/game_trace.py trace.log --aoe       # do multi-target attacks share one to-hit roll?
python tools/game_trace.py trace.log --states    # observed battle state-machine ordering
python tools/game_trace.py trace.log --draws     # real PRNG advances + damage ranges per actor

# The save files decode too — `persist.*.json` is NOT JSON, it's binary DSON
# (magic 01 B1 00 00). This is the only way to recover the loadout a capture
# was made with: class, resolve XP, weapon/armour rank, equipped skills,
# trinkets, quirks. Hero records are nested DSON inside a `raw_data` blob.
python tools/dson.py "<steam>/userdata/<id>/262060/remote/profile_0/persist.roster.json" --find heroes
```

`--out` **appends**, so use a fresh filename per capture unless you mean to pool them. Only one process on the machine can own the debug channel — close DebugView/debuggers first (the tool detects this and aborts rather than silently capturing nothing). Actor names come out in the game's active language.

**The channel never names a hero skill — but it is NOT limited to monster AI and the state machine, and that half of this note was wrong (corrected 2026-08-19, see `DECOMP.md` F3).** It also logs **`Chance to hit: %f` and `Roll to hit: %f` for every attack**, with no flag: the game's `log_info` sink calls `OutputDebugStringA` unconditionally, before it even looks at its severity tag, so the `-enabledebug*` options are irrelevant to it. **`archive/traces/*.log` already holds 166 real (chance, roll) pairs**, captured for another purpose and never grepped for. The hero-skill half of the claim does stand — `MonsterBrain:` blocks name monster skills and target chances, the binary's `Actor_DoSkill` format string never fires in any capture, and a grep for every equipped hero skill across a 445 KB capture returns **0 hits**. There are exactly five `enabledebug*` flags in the whole binary (`monsterbrainoutput`, `queueoutput`, `input`, `audiooutput`, `hashoutput`) and none is hero-side, and `persist.campaign_log.json` is a town chronicle, not a combat log. So a human-played trace cannot report what the player chose: **no behavioural-cloning data is obtainable this way.** Don't record more traces hoping for it.

What partially recovers it: `Battle Random int range A,B` logs the skill's damage band, and several skills are unique in it (Man-at-Arms `0-0` = Bellow, `1-2` = Retribution, `2-4` = Rampart). The plain 100%-damage skills collapse together, so pair it with `-allow_smaller_party` to remove the "whose draw is this" ambiguity.

The full argument table is extractable from the binary — the strings sit contiguously around `Darkest::ParseArguments` at ~`0xab74c0`-`0xab7f00` in `_windows/win64/Darkest.exe`; dump printable runs near the anchor `enabledebugmonsterbrainoutput`. High-value flags for differential testing:

| purpose | flags |
|---|---|
| determinism | `-randomseed N`, `-alwayshit`, `-alwaysmiss`, `-alwayscrit`, `-nevercrit`, `-alwayshealcrit`, `-alwaysdeathblow`, `-alwaysapplyeffect` |
| scenario control | `-firstroombattle <monster>`, `-firstroomcurio`, `-firstroomcamp`, `-allow_smaller_party`, `-monsterbrainoverride`, `-battle_surprise_override`, `-alwaysambush` |
| **forcing state that is otherwise near-unobservable** | `-startafflicted`, `-startvirtued`, `-startatbeforedeathsdoor`, `-startquirk`, `-starttrinket`, `-alltrinkets`, `-starteffects`, `-startsupply` |
| setup | `-startquest`, `-autolevelupheroes`, `-everythingunlocked`, `-dungeonxpoverride`, `-skiptutorial`, `-skipfeflow` |
| **skipping the front end** | `-profile N` (the binary holds the format string `profile_%d`), `-skipfeflow`, `-skipfeflowintro`, `-forcetown`, `-startmap`, `-startbuilding`, `-skipraid`, `-startmode`, `-startradiant`, `-startnewgameplus`, `-resetcampaigntutorials`, `-allquests`, `-plotquests` |
| other | `-enable_autobattle` (the game plays itself), `-textsave`, `-enabledebughashoutput`, `-nosave` |

**This table is a subset: the binary carries ~140 flags and about 35 are missing here.**
The contiguous run is 0xab74c0-0xab7ef0 with `Darkest::ParseArguments` at 0xab7500. There
is a **second, separate table** at 0xaac2xx belonging to the plinth launcher —
`fullscreen`, `full_as_borderless`, `window_pos`, `vsync`, `opengl` — which is why
`full_as_borderless` is not in the list above.

**Grade a flag from `app.log`, never from what happens on screen** — every accepted
option is echoed as `option: <name>` (both tables), and one the binary does not know is
simply absent. `python bridge/applog.py --options` prints them.

**`-skipfeflow -profile N` crashes the game when slot N does not exist** (probed
2026-08-17: all four flags accepted, then dead 43 log lines in, before any `as_*` state).
`-profile` selects an existing slot, it does not create one — so it cannot stand in for
the front-end clicking that makes a new campaign. See `BRIDGE.md`.

**Never `-nosave`**: it suppresses the files the bridge reads.

**LAUNCH WINDOWED AND SMALLER THAN THE MONITOR — `-fullscreen 0 -size 1280 720`
(2026-09-03).** This replaces the old advice to keep `full_as_borderless`, which
was right about the symptom and wrong about the cause and cost two sessions to
the wrong diagnosis. Exclusive fullscreen does hand GDI a black frame — but so
does a BORDERLESS window covering a whole monitor, because DWM gives that the
**independent-flip** path: the game is scanned out directly and never lands in
the composited surface `bridge/screen.py` reads. `screen.grab` returns
`min=0 max=0` while the desktop clock one pixel outside the window ticks to the
second, which reads exactly like "no display attached".

Measured on one boot of one machine: `-full_as_borderless 1` pure black,
`-fullscreen 0 -size 1280 720` live and the recogniser reading `title` at a
separation of 0.15. The SIZE is the load-bearing half — it is what stops the
window covering the monitor. 16:9 keeps `overlay_layout.json` valid unchanged,
because every point in it is a fraction of the CLIENT rect rather than a screen
pixel. `tools/overnight.py::LAUNCH_FLAGS` carries it;
`bridge/screen.display_is_live` tells the two frozen-frame causes apart by
brightness and names the remedy for each.

**AND STEAM HAS TO BE RUNNING.** With Steam closed the game launches, loads a
campaign and plays perfectly — and writes **no saves at all**, so
`persist.raid.json` never appears and the entire save-driven half of the bridge
is blind. `app.log` carrying `STEAM: AddCommandHandler` is the discriminator.
The standing "close Steam before an unattended run" rule in `BRIDGE.md` is about
Cloud restoring a **deleted** profile, so it constrains the delete/claim cycle
and not `tools/overnight.py --resume`, which deletes nothing.

`-startafflicted`/`-startvirtued` matter most right now: overstress act-outs fire on only **0.6% of env steps** in normal play, so the affliction subsystem is almost impossible to validate without forcing it.


### Two oracles read the real game out of `archive/traces/` (2026-08-21)

Both were built from captures that already existed. Neither needs the game
running; both re-grade in under a second.

```bash
python tools/hit_oracle.py        # resolve_to_hit vs 186 real (chance, roll) pairs
python tools/brain_oracle.py      # _select_enemy_skill vs the game's own weight vectors
```

- **`hit_oracle`** — 155 of 155 classifiable pairs. The outcome is inferred
  from the damage band, which is sound in ONE direction only: a stress attack
  or a debuff lands and draws nothing, so a turn where nothing drew damage is
  **unclassifiable**, not a miss. Scoring all 186 reads as 92.6% with 14
  phantom failures. See `DECOMP.md` F4 and its inline correction.
- **`brain_oracle`** — the debug channel prints one block per desire, in the
  brain file's order, each with the running total, so the delta is what that
  desire contributed. 46 of 46 gradeable contributions match. **Target weights
  carry a `1e-4` epsilon per desire and skill weights carry none** — recorded,
  not implemented, since it only decides anything when every weight is zero
  and `weighted_choice` already goes uniform there.
- **A gated desire is reported, never scored.** The trace has no board state,
  so a zero contribution is equally consistent with a shut gate. The corpus
  therefore cannot *confirm* the gate work — only fail to contradict it.
- `archive/traces/*.json` are the tracked fixtures (the `*.log` captures are
  gitignored); `--emit` refreshes them and the tests re-derive them from the
  logs when present.


### The to-hit rule is validated against the running game (2026-08-21)

`tools/hit_oracle.py` grades `resolve_to_hit` against real `Chance to hit` /
`Roll to hit` pairs the release build logs over `OutputDebugStringA`. **155 of
155 classifiable pairs agree.** `DECOMP.md` F4 did this analysis once by hand
in 2026-08-19; this is the same evidence as a re-runnable tool, a test, and a
tracked fixture, over a corpus that has grown 166 -> 186 pairs.

- **The outcome comes from the damage band** (`Battle Random int range A,B`),
  which is sound in ONE direction: a stress attack or a debuff lands and draws
  nothing. A turn where nothing drew damage is **unclassifiable**, not a miss.
  Scoring all 186 reads as 92.6% with 14 phantom failures.
- **The corpus discriminates.** 7 classifiable pairs are decided differently by
  F4 and by the pre-2026-08-19 rule, and the game sided with F4 on all 7, in
  both directions. Chances of **1.01 and 1.05** appear, so the chance is never
  clamped.
- `archive/traces/hit_pairs.json` is the tracked fixture; the raw `*.log`
  captures are gitignored. Refresh it with `--emit`, extend the corpus with
  `--traces <dir>`.


### A third oracle: an independent TRANSCRIPTION (2026-09-19)

```bash
python tools/pizzarat_oracle.py                 # grade every field
python tools/pizzarat_oracle.py --field launch  # one field only
python tools/pizzarat_oracle.py --refresh DIR   # re-extract from the .js
```

`hit_oracle` and `brain_oracle` read the RUNNING game out of
`archive/traces/`. This one reads a third party's reading of the same game
files -- the community reference at `pizzarat105.github.io/PizzaRatReview`,
which lists every enemy and boss skill per difficulty tier with its accuracy,
damage band, crit, and the ranks it launches from and targets. 424 records
are vendored at `tools/pizzarat/enemy_skills.json`; the strategy notes for 11
bosses and 46 enemies are at `tools/pizzarat/strategy_notes.json`.

**It is NOT a ruler**, so it is not in `RULERS` and `engine/` may agree with
it freely: it grades the DATA LAYER against a second copy of the game's own
numbers, the same standing `hit_oracle` and `brain_oracle` have. A ruler
grades the POLICY.

**What it caught on its first run**: the enemy side was ignoring `.launch`
entirely -- 228 of 694 enemy skills usable from ranks the game forbids, which
made disruption a no-op. See `docs/changelog/2026-09.md`.

**Adjudicate every disagreement against the monster's own `.info.darkest`,
never against the site.** Its first run produced four, and the game files
sided with the ENGINE on all four -- about 4% of its gradeable numeric fields
are wrong. Those four are listed in `KNOWN_SITE_ERRORS` with the record that
settles each, and only a disagreement outside that list turns the run red.

**Coverage is 389 of 424 records** (267 of them resolved through the game's
own display-name map). The site derives its ids from display names, so
`graveyard_slash` is not a skill id -- the game calls it `sword_strike` and
only names it that in `localization/miscellaneous.string_table.xml`, whose
`str_monster_skill_<id>` entries are read English-block-only (the file ships
twelve languages, and pooling them makes "Bite" ambiguous across languages
as well as within English).

**It ABSTAINS rather than guess.** The site carries one record per (skill,
tier) but the game lets each monster file give the same skill id its own
numbers -- `spider_bite` is `.launch 12` on the Spitter and `.launch 1234`
on the Webber, both correct. Grading one record against every owner would
manufacture a disagreement against whichever one it was not describing, so
those are counted and skipped; the run says how many.

### The unread-key audit -- `tools/monster_key_audit.py` (2026-09-19)

```bash
python tools/monster_key_audit.py                    # undeclared keys, if any
python tools/monster_key_audit.py --all              # the whole table
python tools/monster_key_audit.py --carriers <field> # who ships it
```

Every key the monster files carry, against every key `data/`, `engine/` and
`env/` mention. 115 (record, field) pairs: 68 read, 47 declared-unread with a
reason each, 0 undeclared.

**Why it is code and not prose.** `ASSUMPTIONS.md` has carried this audit for
a long time and was accurate about every key it listed -- but `shared_health`
was never on it, and that is The Flesh's single HP pool against the
simulator's four bars. It was found by reading a community boss guide, not by
the audit, and had been missing for the life of the project. Prose does not
re-run; `tests/suite/test_monster_key_audit.py` does, and it also checks that
the audit can still go red and that no declaration has gone stale.

**Its one weakness, and it is the important one.** A key counts as read when
its field name appears anywhere in the source. That cannot raise a false
alarm, only miss one: a field that is PARSED and then never consulted reads
as handled. That is exactly how `.launch` hid for the life of the project --
`data/` decoded it faithfully and `_monster_ai.py` never looked at it. **This
tool finds keys nobody has looked at; it cannot find keys someone parsed and
then ignored.** For those the instrument is `tools/pizzarat_oracle.py`, which
grades behaviour rather than coverage.

### Never run a long job unguarded — `tools/safe_run.py` (2026-09-04)

```bash
python tools/safe_run.py -- python -m pytest tests/ -q
python tools/safe_run.py --max-minutes 120 -- python train.py --full-expedition ...
```

Five guards, every one a refusal or a kill and none a warning, because the
incident it exists to prevent produced no warning anybody could act on: a
single-instance lock; a pre-flight scan that refuses when heavy python is
already running; an RSS cap; a free-physical-memory floor (**the failure mode
is the PAGE FILE, not an OOM kill** — Windows swaps, nothing dies, the desktop
just stops); a wall-clock cap, because a hang is the expensive symptom; and a
working-tree report that flags **one-line boolean or comparison flips** under
`engine/ env/ data/ bridge/`. That last one catches a leftover mutation, which
is a MEMORY bug before it is a red test.
