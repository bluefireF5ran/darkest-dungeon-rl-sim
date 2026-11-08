# Objectives: from "plays a raid" to "beats the game better than Fran"

Written 2026-10-06, the second unattended night, at Fran's request: *"plantea
una sesión de análisis de objetivos a futuro del juego, y tras eso ejecuta el
plan"*. Every number below was measured that night or is cited with its
source; nothing is estimated where a measurement was possible.

## The end state

**An AI that finishes a Darkest Dungeon campaign -- kills the Heart of
Darkness -- with fewer deaths and fewer weeks than Fran.** Fran's own
campaign (`profile_8`, audited 2026-09-28) is the yardstick: Veteran from week
20, Champion from week 35, 16 plot/boss quests by week 54, 8 dead.

That decomposes into five capabilities, each of which has to work in the
RUNNING game, not only in the simulator:

| # | capability | what "done" means |
|---|---|---|
| 1 | **Combat** | wins corridor, room AND boss fights at every tier, few deaths |
| 2 | **Dungeon** | routes, torch, curios, camp, pack, retreat -- unattended |
| 3 | **Progression** | climbs Apprentice -> Veteran -> Champion -> Darkest |
| 4 | **Hamlet** | money, heirlooms, treatment, gear, skills, roster |
| 5 | **Robustness** | plays a night without a human, on any screen the game shows |

## Where each stands (2026-10-06)

**1. Combat.** In the simulator (`tools/fate_eval.py`, 1200 paired
`--all-comps` scenarios):

| | short | medium | long |
|---|---|---|---|
| corridor quests, SUCCESS | 96.2% | 95.3% | 83.8% |
| **boss quests, SUCCESS** (`eval_expedition --boss-quests`, 446 comps) | **75.3%** | **71.7%** | **61.4%** |
| **boss quests, deaths per expedition** | **0.72** | **0.83** | **0.97** |

**The policy has never trained on a boss** (`--boss-quest-p` defaults to 0,
the 2026-09-28 audit). Corridor failures are mostly ABANDONS (58 of 88), not
deaths; boss failures are deaths. Knowing the dice (DECOMP.md F15) is worth
**+1.15pp** pooled over two seeds (p~0.055) -- real, small.

**2. Dungeon.** Unattended raids complete (7,090 gold of treasure on
2026-10-05). Open: the full pack (now `--full-pack swap`), the
skeleton-key verb on curios, the torch snuff landing about half the time
against Fran's 22/22, a party drag that occasionally fails to seat.

**3. Progression.** `pick_quest` climbs difficulty and takes worthy bosses
since 2026-10-05; not yet seen climbing live (profile_0's roster is level 1).

**4. Hamlet.** Recruiting, treatment, guild/blacksmith nurture, trinkets,
building upgrades incl. the Nomad Wagon all fly live. Open: Sanitarium
quirks, the Darkest-Dungeon-run roster rules.

**5. Robustness.** One unattended night ran end to end (2026-09-21); every
live session still finds one new modal or UI state.

## The bottleneck, ranked

1. **Bosses (capability 1).** The progression layer now walks into them and
   the end game is nothing else; a boss expedition costs a hero 72-97% of the
   time. Training exposure is a one-flag change with a clean falsifier.
2. **Live robustness (5)** -- each failure is small, but the campaign needs
   hundreds of raids without one stuck screen.
3. **Long expeditions (1)** -- 83.8% against 96.2%, mostly abandons; the
   abandon trade (+1.2pp SUCCESS, +0.044 deaths/run if masked) is Fran's to
   rule.
4. **The machine's edges** -- dice (F15, small), the threat forecast (shipped,
   +6.2pp), and the next one: predicting MONSTER turns from the same RNG
   stream, which needs the draw-consumption model of the monster brain.
5. **Fidelity leftovers** -- F9 PROT (needs one Ruins/Warrens session), F13
   fractional level, `meals_table`.

## Tonight's plan (2026-10-06), in order

1. Pending from last night: the full suite (**3162 passed**), the fate seed-1
   replicate (**+1.2pp**, consistent with seed 0).
2. **Boss exposure**: `train.py ... --boss-quest-p 0.25 --seed 0` and the same
   on seed 1, the shipped recipe otherwise. Falsifier: boss-quest SUCCESS up
   more than 2 SE on `eval_expedition --boss-quests`, and corridor SUCCESS not
   down more than 2 SE on the 1200 paired scenarios. Both seeds or it is
   unreplicated.
3. If it holds: the same with `--fate-obs`, to see whether the two stack.
4. Offline, while the GPU-free CPU trains: per-boss breakdown of where the
   deaths happen, so the next step after exposure is aimed rather than
   guessed.

Results land in `docs/changelog/2026-10.md` and the top of `docs/NEXT.md`.
