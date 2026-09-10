# Findings: driving the real game

What live play found that no simulator run did. Moved verbatim from
the root `CLAUDE.md` on 2026-09-07.


### The deaths were the STAY/LEAVE layer, not the combat policy (2026-09-03)

Fran: *"using the model in game I had several deaths which I didn't really
understand why it went for that kind of play"*. `bat/play.log` records the
model's own forecast beside every decision, and the answer was unambiguous:

```
10 combats left in crypts: P(finish intact) 0% +-0, expected deaths 3.8
leaving costs ~6,278 gold, staying ~-375 - worth pushing on
```

**"Worth pushing on" at every survival probability from 0% to 100%** — 130 of
135 decisions at `P(intact) <= 25%`, RETREAT advised 5 times in 514. Three
causes, all in `bridge/retreat.py` and its one live caller, all fixed:

- **A death was priced at ~1/30.** `advisor.py` passed no `profile`, so
  `party_investment` took the fallback its own docstring calls wrong by a factor
  of thirty (750 gold against a measured 23,750). The accurate path existed and
  was never wired to the only live caller. **The fallback is now an actual
  floor** — `max(profile, equipped)` per hero — because a profile parse that
  returns *less* than the equipment means the parse lost something, and
  `persist.upgrades.json` is the file that invites exactly that.
- **The stress ahead was priced at ZERO — a units bug.** `mean_final_stress` is
  a per-hero MEAN; `carried` was a party SUM; `max(0, mean - sum)` clamps for
  any stressed party. The same function scaled the retreat side by
  `len(living)` and the continue side not at all. **Every fixture in
  `test_retreat.py` defaults to `stress=0.0`**, where a sum and a mean coincide
  — which is why eighteen tests never saw it, and why one of them was passing
  *because* the term was dead.
- **The reward is credited on `completion_rate`**, so a run that finishes having
  lost heroes still pays. Faithful — and the reason the repair is a **survival
  floor** on top of the arithmetic (`FLOOR_DEATH_CHANCE = 0.5`, Fran's call)
  rather than a change to it. `RetreatAdvice.decided_by` names the floor **only
  when it changed the answer**.

```bash
python tools/retreat_audit.py                    # the verdict-by-survival table
python tools/retreat_audit.py --explain 13:23:03 # re-price one logged decision
```

A **RULER**, like `skill_tiers`/`team_probe`/`play_log_probe`, with the same
import guard. Two parsing traps are pinned in its fixture because each cost a
wrong answer on the first pass: a `+-0` error bar (dropping it loses both ends
of the distribution — all 110 confident and all 54 hopeless rows) and the
`after N enemy turn ->` hero prefix (missing it under-counted Death's Door turns
7 against 14 and the worst stress 152 against 180 — every miss a hero in
trouble, because that prefix appears exactly when the enemies got to act).


### `scale_floor_by_investment` — the floor is a constant too (2026-09-03)

Fran's Shambler raid, from his own play: a level-1 party, the Highwayman died,
he took `ancestors_scroll` (`limit=1`), verdict *"very much worth it"*. The
survival floor shipped that morning refuses it unconditionally, and
`advise_retreat` **cannot see the trinket** — trinkets are deliberately
unpriced.

Same defect `tools/exchange_rates.py` found one layer down: a level-1
Highwayman is **0 gold sunk**, a Week-22 veteran **23,750**, and the floor fires
identically for both. `scale_floor_by_investment` (default **False**) applies it
only when a hero is worth more than the quest risked for them. **The threshold
is DERIVED** — `mean_sunk > forfeited`, both already in gold from shipped files
— so the flag adds no number to tune. Turning it on is Fran's call.
