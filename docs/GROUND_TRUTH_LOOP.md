# The ground-truth loop

Fran, 2026-10-04: *"aprovecha todos los recursos posibles de nuestro
decompilado para mejorar nuestro proyecto, plantea un sistema complejo para
hacer mas interesante y eficiente todos los apartados"*.

This is that system. It is a design with its first pieces built, not a wish
list. Each part names what exists, what to build, its first command and what
would show it wrong.

## The problem it solves

The project has three sources of truth and has used them one at a time:

| source | what it knows | how we read it today |
|---|---|---|
| **shipped data** (`rules.json`, `.darkest`, CSVs) | numbers | `data/` loaders, thoroughly |
| **the binary** (`Darkest.exe`, now fully decompiled) | what the numbers *do*, and which ones are never read | by hand, one finding at a time |
| **the running game** (saves, debug channel, screen) | what actually happened | oracles built ad hoc: `hit_oracle`, `brain_oracle` |

Every fidelity bug found so far was a disagreement between two of them that
nobody had put side by side: a key the binary spells differently (F1), keys
it never reads (F7, F14), a rule the code has and the engine lacks (F4, F9),
and an env premise built on an unread key (F12). The loop puts all three side
by side continuously, and it makes every hour Fran plays count as fidelity data
as well as decision data.

## The loop

```
   binary ──(1) extract──▶ facts ──(2) compare──▶ engine/env/bridge
     ▲                       │                        │
     │                  (3) falsify                (4) promote
  (6) patch                  ▼                        ▼
   watch            running game (oracles)  ──▶  re-measure policies (5)
```

1. **Extract**, mechanically, not by hand: `tools/rules_audit.py` (every
   `rules.json` key to parser, global and readers, 2 s),
   `tools/decomp_lookup.py` (who reads a string, a function's C, callers and
   callees), and the named export (14,584 functions named, 360 by their real
   name). **Built.**
2. **Compare**: `data/decomp_facts.json` holds each code-derived fact with its
   address, finding and the repo constant it governs;
   `tests/engine/test_decomp_facts.py` holds the engine to every `matches`
   fact on any machine, with no export needed. A constant with a code address
   behind it can no longer be retuned by hand. **Built, 10 facts.**
3. **Falsify** against the running game, one oracle per mechanic. Two exist
   (`hit_oracle`, `brain_oracle`); F12's corridor check was run by hand on the
   recordings today. **To build:** one oracles report (`oracles --all`), folding them into
   one report:
   - `damage_oracle` (**built**, `tools/damage_oracle.py`) -- pairs each `Battle Random int range` draw from
     `dbwin_capture` with the target's HP drop in the next save (F9: PROT
     rule, integer roll);
   - `corridor_oracle` -- the F12 script, generalised: HP and stress change
     per tile against active DoTs, per tile type;
   - `resolve_oracle` -- every hero crossing 100 stress in the recordings,
     outcome against the F13 prediction (fractional level);
   - `retreat_oracle` -- attempts and outcomes from the bridge's own logs
     against F10;
   - `loot_oracle` -- gold per raid net of reward, Fran against the bridge
     (the 6,879 against 4,161 measured 2026-10-01).
4. **Promote**: a staged rule goes into `engine/` behind a `CombatRules`
   switch whose default is the current behaviour, is A/B'd on two seeds (the
   repo's standing rule), and is promoted by flipping the default, with a
   `CHANGELOG` entry and its fact moved from `staged` to `matches`. Never mid
   A/B.
5. **Re-measure** the policy only when promoted physics moves the eval
   by more than twice its SE. Physics changes leave the observation width
   untouched, so checkpoints still load; whether a retrain pays is a
   measurement, not an assumption (F4 precedent: +8pp with no retrain).
6. **Patch watch**: the 2026-09-15 patch went unnoticed for three weeks and
   split the tools across two builds. **To build:** a patch-watch tool,
   run at the start of `bridge/run.py` and `tools/overnight.py`: hash the
   installed exe, and if it is not a pinned build, say so and print the
   four-command recovery (pin, import, `ghidra_export.py --all`,
   `ghidra_diff.py`). `data/decomp_facts.json` carries the build hash it was
   read from, so the facts test can say "these facts are for another build".

## One session, two datasets

Fran's sessions already teach the decide layers (`bat\record_play.bat`). With
the debug channel captured alongside, the same hour also feeds every oracle.

**Built 2026-10-04:** `bat
ecord_play_full.bat` runs `bridge/recorder.py` and
`tools/dbwin_capture.py --probe --process Darkest.exe` together, into one
session directory. No input is sent, nothing changes for him, and every
session then answers fidelity questions too. The first such session settles
F9's PROT rule.

## The machine's edge: the dice are known (2026-10-05)

The loop's first payoff that no player can have. DECOMP.md F15: the battle
RNG is minstd and the save holds its turn-start state, so every hero turn's
hit roll, crit roll and damage roll are facts before the hero acts.

- **Read:** `bridge/fate.py` (`fate_for_state`), `engine/game_rng.py`.
- **Used:** `run.py --fate` / `bat\combat_play_fate.bat` print the dice every
  turn; `env/fate_wrapper.py` (`--fate-obs`, `--fate-outcomes`) trains a
  policy that sees them, and the advisor feeds such a checkpoint from the save.
- **Measured** (`tools/fate_eval.py`, `tools/pair_runs.py`): about one point
  of SUCCESS in the simulator so far -- the simulator is at 92-94% and its
  failures are mostly abandons, not lost coin flips. The live game, where
  levelled heroes and real bosses are at stake, is where it is meant to pay.

## What it buys each part of the project

**Engine (combat physics).** Three staged rules with measured stakes:
PROT's min-1 cut (11.6% mean expected-damage gap, up to +78% on the weakest
attacks), the integer damage roll, and the fractional-level resolve check.
Fated is a quirk the game implements and we do not. Each one is a switch plus
a falsifier.

**Env (expedition).** F12 replaces the corridor-DoT premise: ticks per
eligible tile, from the map's tile types, instead of a key the game never
reads. F14's `meals_table` lead decides whether camping is priced on real
numbers.

**Monster AI.** `brain_oracle` grades the weights; the decompiled
`Battle::GetMonsterAISelectSkill`, `ActivateMonsterBrain` and the four target
desires (`health/stress/resistance/uncontrolled_target`) are now named, so the
remaining gates (corpses option, F11) can be read instead of inferred.

**Bridge (the player).** `bridge/game_options.py` (built) warns when a campaign
runs rules the simulator does not. Retreat pricing gains the failure counter's
exact form (F10). The snuff gap -- Fran 22/22 snuffs landed, the bridge about
11 of 22 -- is an actuation problem the recordings now locate.

**Decide layers.** Bosses: `data/trinket_tiers.worth_a_boss` plus the quest
save's reward trinket (built in part). Torch: the bracket semantics are
code-confirmed. Camp: meals once F14 is settled.

**Training.** Physics is promoted only with a measured eval delta, so retrains
happen when they have a hypothesis (Fran's standing constraint).

**Efficiency.** Questions that took an evening by hand take seconds:
`rules_audit` re-found F1 and F7 in 2 s. `decomp_facts` makes fidelity a test,
not a memory. Patch watch makes a game update a four-command chore, not a
three-week silent split.

## Order of work

| step | what | first command | falsifier |
|---|---|---|---|
| 1 | play one session with `bat
ecord_play_full.bat`, then read `damage_oracle` | `python tools/damage_oracle.py --list` | a 1-3 hit into PROT dealing 3 refutes F9 rule 2 |
| 2 | promote or reject F9's PROT rule | `python tools/ab_compare.py ...` on two seeds | delta inside 2 SE: keep the switch off |
| 3 | corridor tile mask -> F12 fix | parse `persist.map.json` tile types in the recordings | a full tick on a masked type |
| 4 | patch watch | hash check on `run.py` start | a pinned build reported as unknown |
| 5 | the oracles report | -- | any oracle red on a `matches` fact |
| 6 | F13 fractional level in env | needs resolve XP on env heroes | resolve_oracle disagreement |
| 7 | meals (F14), curios (Open 7), burn modifier (F12) | `decomp_lookup.py --fn 0x140246e40` | -- |

The repo's rules stay in force throughout: no formula from the decompiler
alone; no engine edit mid A/B; one seed is not evidence.
