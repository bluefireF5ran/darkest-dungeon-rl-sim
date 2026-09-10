# Assumptions: session history

**Historical. Read for archaeology, not for current behaviour.**

Fifty reverse-chronological "Status after &lt;phase&gt;" reports, moved out of
`docs/ASSUMPTIONS.md` verbatim on 2026-09-07 so that file can do its actual
job -- telling you whether a mechanic works today.

Parts of this are now WRONG, and deliberately kept anyway because the
reasoning is why the code looks the way it does. In particular the
"Deliberate simplifications" list below still names camping, provisions and
modded heroes as out of scope; camping has been implemented since
2026-08-13, provisions and deliberate eating since 2026-08-24, and modded
heroes load with their kit since 2026-08-27. **`docs/ASSUMPTIONS.md` and
`docs/CHANGELOG.md` are authoritative over anything here.**

## Abomination's free Transform was a stress engine — and fixing it exposed that progress had stopped paying

Chasing the standing outlier ("Dark Ritual: Ballad", last in every sweep) produced a chain of three findings, the last two self-inflicted.

**Ruled out first**: the comp's loadout is fine (15/16 equipped skills usable from their starting ranks), and "it has no healer" is false — every comp in the sweep has at least one ally heal, and the count doesn't correlate with success at all (Ballad Spam has 4 → 67%; Stun Control: Blight has 4 → 45%).

**The actual cause is stress.** Against the best comp: Dark Ritual: Ballad ran 123.9/140.1/**169.2** mean final stress with 44/60/**78%** wipes, versus Riposte Style (Hag) at 49.5/44.7/**69.6** and 10/4/**12%**. Affliction begins at 100 and heart attack at 200.

**Source: Abomination's Transform.** Real data — +8 stress to *every other party member* on transforming to beast, 6 stress/turn while in beast form, and only 2 refunded on reverting. Measured on the 6M model: **1.77 transforms per combat** — essentially the full 2-per-battle allowance every fight — for ~153-166 stress per ally per expedition from transforms alone. The two worst comps in every sweep were precisely the Abomination ones.

Why the policy did it: Transform is `is_continue_turn`, a **free action**, so no turn penalty applies, while its only real cost (stress) was charged **once at the expedition's end**. At γ=0.99 across ~200 steps a terminal penalty is worth ~13% of face value at the moment of choosing. This is the identical structural mistake as the truncation leak and the recovery-discount leak: *a cheap immediate action whose true cost is sparse and terminal*.

**Fix 1 — dense stress cost.** `STRESS_TICK_PENALTY = 0.01` per point of party stress gained, charged as it lands; `TERMINAL_STRESS_PENALTY_WEIGHT` reduced 10 → 4 so this redistributes the existing cost rather than adding on top. Only increases are charged (refunding stress-healing would pay the policy to farm heal loops), and `_total_party_stress` counts living heroes only so a death can't read as a stress *drop*.

**This alone was a -13pp regression** (55.4% → 39.3%), and the failure mode diagnosed itself: "Stun Control: Beast" stalled **82%** of long-band episodes while holding stress at 53.5. It had learned to avoid stress by refusing to fight.

**Fix 2 — progress had to pay.** Measured per-combat reward rather than guessing again: mean reward per *completed* combat was **-0.03** for Riposte Style (Hag) (15 steps — breaks even, keeps fighting) and **-7.15** for Stun Control: Beast (31 steps). For that comp, eight remaining combats at -7 is far worse than a single -20 truncation discounted to ~-2.6, so stalling was genuinely optimal.

Root cause was stale calibration: `_reward_win = 1.0` was set when the only per-turn cost was `TURN_PENALTY = -0.02`. The threat-phase turn cost had since risen to -0.10 and a dense stress charge was added — roughly 10x the per-combat cost — without ever revisiting whether progress still paid. Raised to **8.0**.

**Result** (12-comp × 50-episode sweep):

| run | mean | median | max | wipe | stall | stress |
|---|---|---|---|---|---|---|
| move-fix 6M | 55.4% | 61.0% | 92.0% | 24.9% | 8.7% | 89.5 |
| stress-only 3M | 39.3% | 41.0% | 80.0% | 29.1% | 20.7% | 90.4 |
| progress 3M | 54.8% | 61.0% | 98.0% | 28.3% | 6.0% | 94.8 |
| **progress 6M** | **58.4%** | **64.0%** | 94.0% | **22.7%** | **5.6%** | **84.8** |

Best figures in the project on mean, median, wipe, stall and stress simultaneously. Transform spam dropped from 1.77 to **0.75-1.16 per combat**. Stalling fell to 5.6%. "Dark Ritual: Ballad", the outlier that started this, went 34/8/6 → 34/26/2 (short-band 48% at the 3M checkpoint) — no longer anomalous, just genuinely the hardest comp. Riposte Style (Hag) peaked at 98/96/92 at 3M, which is 5 deathless expeditions in a row ~82% of the time.

**Process note worth keeping.** Three of the reward magnitudes in this arc were set wrong by me on first attempt, each the same way: making one side of a trade-off dense while leaving its counterpart sparse or stale (unbounded cheap recovery turns; a stress gate that was permanently open; a dense stress charge against a stale progress reward). The reward's terms are *coupled*, and treating them as independent knobs is what caused each regression. Measuring per-combat reward directly is what resolved it, and should be the first step next time rather than the fourth.

## New plateau at ~55% — and the old "45% plateau" was an artefact of the broken Move action

Re-ran the multi-batch sweep on the fixed engine (3M → 6M → 9M, standard 12-comp × 50-episode eval each):

| checkpoint | succ mean | median | max | min | wipe | stall | stress |
|---|---|---|---|---|---|---|---|
| move-fix 3M | 52.1% | 53.0% | 84.0% | 2.0% | 24.0% | 12.9% | 87.1 |
| **move-fix 6M** | **55.4%** | **61.0%** | **92.0%** | **6.0%** | 24.9% | **8.7%** | 89.5 |
| move-fix 9M | 50.6% | 55.0% | 82.0% | 2.0% | 27.9% | 8.7% | 93.8 |

**The earlier plateau finding is superseded.** That sweep concluded the ceiling was ~45% mean and that more uniform budget was the wrong lever — but it was measured on an engine where every backline hero pushed forward was permanently trapped at the front and every frontline hero pushed back was trapped at the back. With Move working, the same training recipe reaches 55.4%. Stalling in particular fell to 8.7%, a third of the original 17.3%.

Peak is 6M; 9M regresses on mean/median/max/wipe/stress. So there is still a plateau, just a much higher one, and the same per-comp trading is visible across checkpoints (Dragon Blood 84/70/52 at 6M → 30/40/12 at 9M, while Stun Control: Beast went 42/24/20 → 58/58/44).

**Interference is genuinely smaller now.** Recomputing the best-per-comp envelope over the three fixed-engine checkpoints: envelope mean 60.9% / median 68.0% versus 55.4% for the best single checkpoint — a **+5.5pp** gap, down from the **+6.8pp** (52.2% vs 45.4%) measured before the fix. Checkpoint wins also concentrate rather than scatter: 6M takes 19/36 cells, versus the pre-fix spread where no checkpoint took more than 14. Specialisation is still worth something, but it is no longer the dominant story it looked like, and a chunk of what was diagnosed as "multi-task interference" was really the policy failing to reposition.

Live model set to the 6M checkpoint. Against the project's stated bar: best cell 92% (Riposte Style (Hag) medium) is 5-deathless-in-a-row ~66%; median comp 61% is ~8%. Still short of high-stakes-autonomous, but the distance closed substantially and mostly from correctness fixes rather than compute.

> **Superseded in part on 2026-08-19.** Move had a SECOND inverted rule underneath this
> one: it treated an occupied rank as the only *illegal* destination, when a move is a
> swap and an occupied rank is the only *legal* one. So a full party could not move at
> all, and every checkpoint below — including the "move-fix" sweep — was trained on an
> engine where repositioning only existed after a hero had died. The direction fix and
> its numbers stand; the claim that Move was then *working* does not. See
> `CHANGELOG.md`, "A move is a SWAP, and the rule was exactly inverted".

## The Move action moved heroes the wrong way — found by chasing a 39% pass rate

`CombatEngine.get_valid_actions`' Move branch had the two directions inverted. `position 0` is rank 1 (the front), so moving **forward** must DECREASE the index and **backward** must increase it. The code was:

```python
lo = max(0, hero.position - hd.move_skill.backward)   # backward moved toward the FRONT
hi = min(3, hero.position + hd.move_skill.forward)    # forward moved toward the BACK
```

Both reversed, for the entire history of the Move action. Consequence: **every backline hero pushed to the front was trapped there, and every frontline hero pushed to the back was trapped there.**

Found by investigating why the trained policy was spending so many turns on `__pass__`, which the engine only ever offers when *nothing else is legal* (`analyze_behaviour.py` measured Musketeer at 39% and Hellion at 18% of their turns). Two real classes make the correct direction unambiguous, and each matched one of those measurements:

- **Musketeer/Arbalest** (`.move 2 0` = 2 back / 0 forward) are rank 3-4 snipers: they must be able to retreat and must never voluntarily advance. Inverted, a Musketeer pushed to rank 1 computed `lo=max(0,0-2)=0, hi=min(3,0+0)=0` — **no legal move at all**, ever.
- **Hellion** (`.move 0 1` = 0 back / 1 forward) is the mirror: a rank-1 bruiser who must advance and never retreat. Inverted, she could only ever drift toward rank 4 and never return.

The field order itself (`.move <backward> <forward>`) was correct and is confirmed by both classes reading sensibly under it; only the direction mapping was wrong.

**Two existing tests had encoded the bug**, which is why it survived:
- `test_leper_cannot_move_backward` asserted Leper moved to position 3 — i.e. *backward* — directly contradicting its own name. Leper is 0-back/1-forward and should advance to position 1. The name was right; the assertion was wrong.
- `test_pass_turn_fallback_when_boxed_in_with_no_actions` constructed its boxed-in state *out of* the bug (a lone Musketeer at rank 1 with no legal move). Rebuilt as a genuine one: a full party with every rank occupied, so Move truly has nowhere to go.

New test `test_pushed_sniper_can_retreat_and_pushed_bruiser_can_advance` covers both directions against real data. Suite: 321 passed.

**Effect — the single largest gain of any change so far.** Retrained 3M from scratch: success mean **44.7% → 52.1%**, median 48% → **53%**, wipes 29.6% → **24.0%**, stalls 14.7% → **12.9%**, final stress 94.2 → **87.1**. This 3M model beats the previous best (a 6M model at 46.9%) by 5pp on half the budget. Pass rates collapsed exactly as predicted: Musketeer 39.1% → **0.0%**, Hellion 18.0% → **1.6%**. Houndmaster's attack share rose 50% → 79%.

That the policy had barely used Move at all (0.4-3% of turns) is retrospectively explained: it was an action that did the opposite of what the data said, so it was mostly a trap.

## Two-phase tempo model — user-specified, and it works once the discount is hard-capped

The user described how advanced players actually play: a **threat phase** while several enemies can act (kill/stun/riposte/prevent — "on Darkest Dungeon if you are 4v4 the heals wont ever be enough"), and a **recovery phase** at the last enemy where deliberately stalling to heal is correct. Implemented as a phase-dependent per-turn time cost, because that is the only term that distinguishes a fast combat from a slow one — the potential-based shaping telescopes, so two paths to the same end state earn identical shaping regardless of length. The old flat `TURN_PENALTY = -0.02` summed to about -5 across a ~250-turn expedition, a quarter of one death, which is why the policy turtled.

`TURN_PENALTY_THREAT = -0.10`, `TURN_PENALTY_STALL = -0.01`, phase read *before* the step so a turn is charged at the rate of the situation the decision was made in. Also added to Φ: `W_ENEMY_STUNNED = 0.30` (a stunned enemy can't act — action economy) and `W_HERO_GUARDED`/`W_HERO_RIPOSTE = 0.10` (prevention). Those are potential terms, so policy-invariant by Ng/Harada/Russell; they only speed up credit assignment. The stall timer (`stall_counter`, invalidating actions this round) was added to the observation — the advanced 1-enemy technique of poking with real attacks so reinforcements never arrive was strictly unlearnable without it.

**It took three attempts, and the first two failures were both the same mistake: trying to bound a cheap action with something soft.**
- **v1** (discount on enemy count alone): success 41.4% → 32.4%, stalls 17.3% → 20.4%. The policy killed down to one enemy then dawdled forever, because a recovery turn costs -0.01 *immediately* while the -20 truncation penalty, discounted at γ=0.99 over ~200 remaining steps, is worth about **-2.6** at the moment of choosing. A sparse terminal penalty cannot discipline a dense cheap action.
- **v2** (added a need-gate: any hero below 85% HP or above 30 stress): 33.2%, stalls **28.2%** — i.e. no effect. Stress persists across combats and averages ~99 over an expedition, so "any hero above 30 stress" is true almost permanently and the gate never closed.
- **v3** (`RECOVERY_TURN_BUDGET = 10` discounted turns per combat, reset per internal combat): **44.7% at 3M / 46.9% at 6M**, stalls back to 14.7%/13.7%, and the best wipe and stress figures to that point. A budget can't be argued with.

Behaviour moved as the user predicted: Plague Doctor 22% attack / 70% heal → **57% / 25%**; Abomination 11% attack / 74% self-heal → **44% / 38%**; Man-at-Arms 34% → **75%** attack. Stun Control: Blood went 32/22/12 → 76/64/48 and Stun Control: Beast reached 40/38/32 — the comp family the user originally flagged.

**Still unresolved**: the dedicated healers did not respond to tempo pressure and in some runs got worse (Crusader 83% heal, Vestal 78%, Occultist 66%, Arbalest 63%). Plague Doctor also oscillates between runs (25% heal in one, 65% in the next), so per-class behaviour is not yet stable across retrains.

## What the model actually learned: a turtle strategy, and it never trained on ad-hoc comps

User asked two things after the plateau: whether the model can handle *imperfect* comps ("Stun Control is really Plague Doctor + Crusader + Abomination + 1 — a strong core, but you don't always need a perfect comp"), and what it has actually learned — plus whether it plays like a human. Built two analysis tools for this: `analyze_coverage.py` (what training exposed it to) and `analyze_behaviour.py` (what it does, from rollouts, reading the concrete `Action` the env executed each turn).

### Training exposure — the ceiling on what it could have learned

`ExpeditionEnv._sample_scenario` → `_sample_comp()` draws only from `_hero_pool`, which is built exclusively from the 132 real comps. **An ad-hoc party is a distribution the policy has literally never seen.** Two consequences measured directly:

- **Class exposure is 7.4x imbalanced**: Houndmaster 81 slots (15.4% of all hero slots), Crusader 54, Plague Doctor 48, Arbalest/Occultist 45 … down to Shieldbreaker 11 (2.1%), Antiquarian/Musketeer 14, Grave Robber/Leper 15.
- **Positional exposure is extremely narrow.** Several classes were never trained in whole halves of the board: Plague Doctor appears at ranks 3-4 only (0/0/23/25 across ranks 1-4), Arbalest effectively rank 4 (0/1/4/40), Leper rank 1 only (14/1/0/0), Shieldbreaker rank 1 only (10/1/0/0), Vestal rank 4 (0/2/4/10), Abomination rank 2 (2/30/7/0). So a shuffled formation — or an ad-hoc comp that puts a Plague Doctor at rank 2 — is out of distribution.
- Some skills are never equipped by any real comp and so were never available to learn: Crusader's `zealous_accusation`, Flagellant's `exsanguinate`, Grave Robber's `poison_dart`, Highwayman's `take_aim`, Shieldbreaker's `single_out`, Vestal's `judgement`.

### Behaviour — it plays like a turtle, not like a human

24 expeditions, ~5.7k hero turns. What it does *right*: attacks are spread across ranks (39/19/24/17 for ranks 1-4, not front-dumping), it almost never wastes a stun on an already-stunned target (8 of 1823 attacks), bandage/antivenom are used only when the hero actually has the condition (0 wasted), stress-heal skills do get used (227 uses), and the old position-shuffling pathology is gone (Move is 0.4-3% of turns for most classes).

What it does *wrong* — a consistent, systemic over-valuation of sustain over offence:

| class | % turns healing | signature damage skill, share of its uses |
|---|---|---|
| Crusader | **73.1%** (`battle_heal` 66%) | `smite` 5%, `holy_lance` 4% |
| Arbalest | **62.7%** (`battlefield_bandage` 66%) | `sniper_shot` 26% |
| Musketeer | **61.8%** (`patch_up` 70%) | `aimed_shot` 25% |
| Vestal | 59.0% | attacks on **1.7%** of turns |
| Occultist | 48.4% (`wyrd_reconstruction` 61%) | — |
| Plague Doctor | 39.3% (`battlefield_medicine` 53%) | `blinding_gas` **7%** |
| Abomination | 74.4% **self** (`absolution` **78%**) | attacks on **11.5%** of turns |

Concrete non-human plays confirmed in a dumped log (`run_sample.log`): an **Arbalest at Death's Door spent her turn healing the Houndmaster for 5** instead of herself; `laudanum` and `holy_water` are fired prophylactically on turn 1 before the condition exists (741 uses across 24 episodes — they're free actions so there's no tempo cost, but it burns finite consumables). Heals land on targets at a median of **90% HP** — technically "proactive", actually wasteful.

**Abomination's `absolution` is the sharpest case, and it closes a loop back to the user's own example.** Real data: `.atk 0% .dmg 0%`, effects `"Absolution HealStress 5" "Absolution Heal 5"`, and — critically — **`.is_stall_invalidating false`**. So the policy's favourite Abomination action does no damage, heals him, *and does not count as a real action for the stall mechanic*. Abomination spends 74% of his turns on it, which means the stall counter climbs, reinforcements spawn, the fight drags, and the party either stalls out or is ground down. The Abomination-heavy Stun Control comps being the weakest in every sweep is the direct downstream consequence — and Plague Doctor, the other half of that core, uses his signature stun (`blinding_gas`) on only 7% of his turns.

Hellion's 13.8% `__pass__` rate is a genuine positional trap rather than a pure policy error: her real skills launch from ranks 1-2 (`wicked_hack` 21, `iron_swan` **1** only, `barbaric_yawp` 21) or 1-3 (`if_it_bleeds` 321), so once pushed to rank 4 with living allies filling 1-3 she has no legal action at all. A human would have repositioned earlier; the policy uses Move on 0.9% of her turns.

### Root cause: the reward pays for not dying, and barely charges for time

`REWARD_HERO_DEATH = -20`, `TERMINAL_STRESS_PENALTY_WEIGHT = 10` and `REWARD_EXPEDITION_COMPLETE = +10` are large; `TURN_PENALTY = -0.02` is not. Over a ~250-turn expedition the entire accumulated time cost is about **-5**, i.e. a quarter of a single death. Healing therefore dominates attacking on expected value, which is locally rational and globally fatal: turtling cannot finish 14-18 combats within the step budget, so the long bands stall or get worn down. This is the same failure the truncation penalty was meant to address, but the penalty is terminal and sparse while the incentive to heal is dense and immediate.

**Indicated fixes, in priority order** (none applied yet — this is analysis):
1. **Rebalance tempo vs. sustain** — raise the per-turn/per-round cost so killing is worth more than out-healing, and/or scale completion reward with how quickly combats resolve. This is the single change most likely to move behaviour.
2. **Train on ad-hoc comps** — sample a fraction of parties as "strong core + arbitrary filler" rather than only the 132 curated comps, which is what the user actually wants to deploy against.
3. **Randomize formation** — so classes are seen outside their one canonical rank (Plague Doctor has never acted from rank 1-2).
4. Consider whether `absolution` spam should additionally be discouraged directly; the stall mechanic already punishes it, but only indirectly and on a delay.

## Plateau found at ~45% mean success — and it's multi-task interference, not a learning limit

User asked for a big multi-batch training run to find the plateau. Trained 3M→6M→9M→12M in 3M-step `--resume` batches from the interface-redesign checkpoint, running the standard 12-comp × 50-episode sweep at each (`eval_{3,6,9,12}M.log`, summarised by the new `summarize_eval.py`).

| checkpoint | succ mean | succ median | succ max | wipe | stall | stress |
|---|---|---|---|---|---|---|
| 3M | 41.4% | 42.0% | 88.0% | 32.8% | 17.3% | 98.1 |
| **6M** | **45.4%** | 44.0% | 86.0% | 30.1% | 14.6% | 97.6 |
| 9M | 42.6% | 47.0% | 78.0% | 36.2% | 12.7% | 104.5 |
| 12M | 42.4% | 46.0% | 82.0% | 32.6% | 15.0% | 101.1 |

**Flat from 6M onward.** Quadrupling the budget (3M→12M) bought **+1.0pp mean**. The only real gain was 3M→6M.

**The interesting part is underneath the aggregate.** Per-comp results oscillate 20-30pp *between checkpoints* — far beyond the ±7pp sampling noise at 50 episodes/cell, i.e. real policy drift, not measurement error:
- Riposte Style (Hag) long: 74 → 84 → 54 → 70
- Stun Control: Beast short: 22 → 32 → 20 → **8** (near-total collapse by 12M)
- Ballad Spam short: 48 → 52 → 70 → **76** (steady climb)
- Dark Ritual: Ballad medium: 2 → 12 → 2 → 6

The single shared policy keeps **trading competence between comp families** rather than accumulating it. Classic multi-task interference: ~132 comps sampled uniformly into one set of weights, and the median tightens while the ceiling erodes (max 88 → 86 → 78 → 82).

**Quantified directly — the best-per-comp envelope:** taking, for each of the 36 comp×band cells, the best result across the four existing checkpoints (zero additional training) gives **mean 52.2% / median 54.0%**, versus **45.4%** for the best single checkpoint. **Interference is costing ~7-10pp**, and that gap is recoverable by specialisation alone. Checkpoint wins are spread: 6M takes 14/36 cells, 3M 9, 12M 7, 9M 6 — no single checkpoint dominates.

**Conclusion: uniform additional budget is the wrong lever from here.** This is not "the task is learned" — it's "one policy can't hold specialist play for 132 comps simultaneously." The natural fix also happens to match deployment reality: a real run takes *one* specific party into *one* dungeon, so what's needed is a policy excellent at the comp actually being played, not one averaged over all of them. Per-comp (or per-archetype) fine-tuning from a shared base is the indicated direction, and the envelope number is a lower bound on what it should recover.

**Live model set to the 6M checkpoint** (best single checkpoint; 12M is strictly worse on mean, median and wipe rate). All four checkpoints retained as `dd_combat_ppo_full_expedition_{3M,6M,9M,12M}*_backup.zip` — they are individually useful as per-comp specialists per the envelope analysis.

Also worth recording against the project's stated bar: at 6M the strong comps sit at 84-86% (Riposte Style (Hag) 86/86/84, Holy Hymn: Riposte 72/84/66), which is 5-expeditions-in-a-row at ~45%. The ~96% per-expedition needed for genuine high-stakes autonomy is still out of reach for any comp, and clearly out of reach for the median comp (44%).

## Agent interface redesign — the eval metric was measuring the wrong thing, and the agent could not see the game

User called a stop-and-assess: *"i want minimal death rate that can properly get 0 death in a batch of several expeditions in a row... safe to use in a real, high stakes environment."* Assessing against that bar surfaced two problems bigger than any individual mechanic fix, and both are upstream of everything this project had been doing.

### 1. The headline metric counted stalling as success

`eval_expedition.py` computed `zero_death = len(living) == len(hero_ids)` **at episode end regardless of why it ended**, and `timed_out = truncated and not living` only counted truncations where the party had ALSO died. Net effect: an expedition that ran out of the step budget with everyone alive scored as a zero-death success *with no timeout flagged*.

Caught it via `mean_combats_reached`: "Stun Control: Beast" short band reported 28% zero-death while reaching 3.0 of a sampled 8-11 combats. For those 28% to be real completions, the other 72% would have to wipe inside half a combat — implausible. Re-ran with the corrected metric and confirmed directly: **"Ballad Spam, BIS!!" medium band = 33.3% zero-death, 0.0% success, 46.7% stall.** Every bit of that comp's apparent survival was the clock running out.

Fixed: new `ExpeditionOutcome` dataclass splitting the genuinely distinct end states — `success` (completed every combat AND zero deaths, now the headline), `completed`, `zero_death` (kept as a secondary column for continuity, explicitly not a success rate), `wiped`, `truncated_alive`. Re-baselining the pre-redesign model on the honest metric: success is 0-47% depending on comp/band, wipes are the dominant failure mode (33-100%), and stalling is a real secondary failure (up to 47%). **Several eval comparisons reported earlier in this file — especially the "Stun Control family breaks out" result — were partly measuring dithering and should be read with that caveat.**

Also added `REWARD_TRUNCATION = -20.0` (`env/expedition_sequence_env.py`): stalling out the clock previously strictly dominated finishing a risky expedition (no deaths, no lose-reward, party alive), which is exactly the behavior the metric was rewarding. Now roughly a death's worth of penalty. Simulator convention, like the other reward magnitudes.

### 2. The agent could not observe the game it was playing

The observation was 50 floats (57 with expedition context): `[hp%, position, stress, is_dd, is_dead]` per hero and `[hp%, position, is_dead]` per enemy. Verified by direct inspection that it did **not** contain:
- **whose turn it is** — no current-actor signal anywhere, so the vector was identical whether the Crusader or the Vestal was choosing, and the policy could not condition on the hero it was controlling;
- **hero class identity** — rank 0 could hold a Leper or a Vestal, indistinguishable;
- **enemy identity** — a Cultist Witch and a Bone Rabble were the same three numbers;
- **any status at all** — no stun, mark, bleed, blight, guard, riposte, stealth, or buff state.

The action space compounded it: `Discrete(64)` indexing into whatever `get_valid_actions()` returned that turn, so index *k* meant a different skill and target every turn.

**This is the honest explanation for why ~10 real fidelity fixes this session produced ±10pp noise rather than clean gains: every mechanic being fixed was invisible to the policy.** It cannot see marks, so the Mark fix is unexploitable; cannot see stuns, so stun-lock comps cannot learn stun-locking; cannot see buffs, so conditional trinkets and Aegis blocks are unobservable. The simulator fidelity work was necessary and is a prerequisite — but it was no longer the binding constraint, and more of it (or more timesteps) would have kept producing noise.

**Redesign — both spaces are now RANK-INDEXED**, so the observation is positionally aligned with what an action means:
- Observation (328 base / 336 with expedition context): 4 hero rank slots × (21 scalars + 18-class one-hot) + 4 enemy rank slots × (15 scalars + 9-type one-hot) + 7 skill slots × 10 features + 4 globals. Hero slots carry the **is_current_actor** flag plus stun/mark/guarded/guarding/riposte/stealth/afflicted/virtuous/Death's-Door flags, DoT totals, Aegis blocks, and live dodge/prot/speed/crit. Encoding live stats is what makes buff and debuff *effects* observable without enumerating every buff id — it's how the conditional trinket/quirk work actually reaches the policy. Enemy slots carry corpse/stun/mark/stealth/doomed-by-DoT flags, DoTs, size, live stats, `actions_per_round`, and the real `enemy_types` one-hot. Skill slots describe the acting hero's full 7-skill movepool (exists / legal-now / ally-targeting / heal / AoE / atk / dmg_mod / crit / stall-invalidating / free-action), so slot semantics generalize across classes.
- Action space: fixed `Discrete(37)` — `[0..27]` skill_slot×4 + target_rank, `[28..31]` move-to-rank, `[32..35]` provision, `[36]` pass. Slot indices come from the hero's **full** 7-skill movepool (`CombatEngine.get_hero_full_skill_ids`, new) rather than the restricted 4-skill loadout, so slot *k* names the same skill for a class regardless of which 4 a comp equips — verified all 18 real hero classes have exactly 7 skills. `step()` no longer clamps: an unmapped index falls back to the engine's own `__pass__` rather than silently substituting a different real action.
- `PROVISION_ACTION_ORDER` is an explicit tuple: `engine.IN_COMBAT_PROVISION_IDS` is a **set**, whose iteration order is not part of its contract and must never assign stable action indices.
- `train.py`'s network widened from SB3's default `[64, 64]` to `[256, 256]` — the first layer was narrower than its own input at ~330 floats.

**Test fallout, all real contract changes rather than incidental breakage** (311 pass, from 309): shape assertions updated to derive from the layout instead of hardcoding 50/57/64; `test_action_masks_marks_exactly_the_valid_actions_true` rewritten from "True on 0..n-1" (a statement about list length) to a bidirectional faithfulness check on the index↔Action mapping; new `test_action_index_layout_is_stable_and_disjoint` and `test_observation_encodes_which_hero_is_acting` (two different actors on an otherwise identical board must produce different observations — the single property the old observation most conspicuously lacked); `_pick_attack_idx` returns an action-space index rather than a list offset; `test_stalling_agent_does_not_profit`'s "force a no-op" now clears `_action_map` (clearing `_valid_actions` alone no longer suffices, and this exercises the new unmapped-index fallback directly).

**All prior models are invalid under the new spaces** — the last one is archived as `dd_combat_ppo_full_expedition_OLD_OBS_ACTION_SPACE_3M_archive.zip` and cannot be loaded against the current env. Retraining from scratch.

**Bar-setting math, for reference**: getting N expeditions in a row deathless is `p^N`. At the old best cell (`p=0.50`) that's 12.5% for 3 and 3% for 5; the median cell was worse. Reaching 5-in-a-row at 80% reliability needs `p ≈ 0.96`. That is a different regime from where the project is, not a tuning gap — and it is the number to judge "safe for high-stakes autonomous use" against.

### Result: the largest single improvement in the project's history

From-scratch 3M-step retrain on the new interface, then the standard 12-comp × 50-episode sweep. `ep_rew_mean` went **-60.8 → -36.6** at the same budget *despite* the new -20 truncation penalty pushing it down.

Comparing new SUCCESS against the old model's ZERO-DEATH column (i.e. against a number that structurally *favours* the old model, since it counted stall-outs as wins — the honest old success rate is strictly lower than shown):

| Comp | old 0-death (S/M/L) | new success (S/M/L) |
|---|---|---|
| Riposte Style (Hag) | 50 / 40 / 20 | **82 / 88 / 74** |
| Dragon Blood | 32 / 26 / 18 | **76 / 56 / 58** |
| Marked Prey: Snipe | 10 / 10 / 6 | **70 / 64 / 66** |
| Holy Hymn: Riposte | 50 / 26 / 10 | **66 / 76 / 62** |
| Marked Prey: Royal | 18 / 14 / 6 | **64 / 46 / 36** |
| All Good Hellion | 38 / 34 / 18 | **54 / 46 / 44** |
| Ballad Spam, BIS!! | 26 / 18 / 14 | **48 / 24 / 20** |
| Stun Control: Blight | 10 / 4 / 4 | **40 / 32 / 6** |
| Stun Control: Blood | 14 / 6 / 4 | **32 / 22 / 12** |
| Shadowed Purse Strings ALT | 8 / 4 / 0 | **28 / 20 / 14** |
| Stun Control: Beast | 28 / 22 / 20 | 22 / 20 / 6 |
| Dark Ritual: Ballad | 16 / 12 / 0 | 14 / 2 / 0 |

Median cell 15% → **42%**; best cell 50% → **88%**. Mean final stress fell from 72-194 to 49-182 (the strong comps now sit at 49-105, well clear of the 200 heart-attack ceiling). Several comps now post *positive* mean reward for the first time in the project (Riposte Style (Hag): +6.5 / +10.0 / +1.6).

**"Marked Prey: Snipe" is the cleanest causal evidence available**: 0% success on the pre-redesign model at the same measurement (4-comp direct comparison run), now 64-70%. It's an Occultist/Houndmaster/Musketeer/Arbalest comp built entirely around Mark — and Mark was one of the statuses the old observation could not represent at all. Same story for the Riposte comps (guard/riposte state now visible) and the Stun Control family (stun state now visible), all of which had been stuck for the entire project.

**The two apparent regressions are both explainable and one is not real:**
- *Stun Control: Beast* (28→22 short) — the old 28% was the specific number proven to be almost entirely stall-out (3.0 of ~9.5 combats reached). The new model reaches 6.4 combats and its 22% is genuine completion. It got substantially better in reality while the headline number went down, which is exactly what fixing an inflated metric looks like.
- *Dark Ritual: Ballad* — genuinely the weakest comp (64-88% wipe, stress 142-182). Not a metric artifact; it is losing fights.

**Remaining failure modes, ranked by how much they cost:**
1. **Wipes on long bands** — every comp degrades from short to long, and the weak comps wipe outright (Dark Ritual: Ballad 88% on long).
2. **Stalling is reduced but not solved** — Stun Control: Beast 46-54%, Ballad Spam 30-48%, Stun Control: Blight 20-28%. Worth investigating whether `MAX_STEPS_PER_COMBAT = 40` is simply too tight for buff/CC-heavy comps before penalising harder: Ballad Spam reaches ~8 of 16 combats at roughly 75 steps/combat, which reads more like a genuine budget shortfall than dithering, and raising the penalty would then be punishing a comp for the cap rather than for stalling.
3. Camping is still unmodelled, which structurally caps the stress objective (see the Known gaps section in CLAUDE.md).

## A third, entirely unparsed inline-buff mechanism (`.buff_type`/`.buff_amount`) — found finishing the Shieldbreaker audit

While investigating Expose ("+Crits Received Chance Debuff... invaluable" per her doc), found its real effect record: `effect: .name "Expose Crit Debuff 1" .target "target" ... .buff_type crit_received_chance .buff_amount 8% ...` — a THIRD distinct inline-buff mechanism, alongside `.buff_ids "X" "Y"` (a reference to a named buff in `base.buffs.json`) and the `combat_stat_buff 1` + specific-field pattern (`INLINE_STAT_FIELD_MAP`, e.g. Abomination's `beast_buff_N`, already fixed earlier this session). `.buff_type` names the real stat_type directly; `.buff_amount` is its value. Grepped for `buff_type`/`buff_amount` anywhere in the codebase: zero hits (aside from an unrelated method name) — completely unparsed.

**Scope, verified directly**: 29 real occurrences across `base.effects.darkest` + DLC, two real stat_types: `hp_heal_received_percent` (base's "Stacking Heal N", Arbalest's "Arb Stacking Heal N" — the real mechanism behind `battlefield_bandage`'s stacking self-heal, "Crabby Pinch Debuff"/"Reaver Cut Debuff"/"Heal Recd Debuff"/"black seed heal debuff" — several monster heal-received debuffs) and `crit_received_chance` (Shieldbreaker's "Expose Crit Debuff N", Arbalest/Musketeer's "Grapeshot Vulnerability N" on Grapeshot). One test-only outlier (`"test_torchlight_burn"`, `buff_type torchlight_burn_percent`) is a leftover dev fixture, not real content, ignored. Directly connects to two already-"fixed" things this session that turned out to still be incomplete: Arbalest's `battlefield_bandage` (the heal-effect-list fix made `_apply_skill_effects` get CALLED for it, but the underlying "Arb Stacking Heal N" buff itself still did nothing until now) and `crit_received_chance` (wired into `_apply_buff_to_stats`/`check_crit` for the Man-at-Arms Bellow/conditional-buff work above, but this is a second, independent real source of the same stat that needed its own parsing).

**Fixed**: `EffectDefinition.inline_buff_type`/`inline_buff_amount` (parsed from `.buff_type`/`.buff_amount`, `data/effect_loader.py`). `_apply_effect_to_target` synthesizes a `BuffDefinition` (same pattern as the existing `INLINE_STAT_FIELD_MAP` branch — cached in `store.buffs` under a `__buff_type__{effect_name}` key) with `stat_type=eff.inline_buff_type` directly, applies it via the existing `_apply_buff_to_stats`, and tracks duration via `ActiveBuff` when `eff.duration` is set (respecting `buff_duration_type` the same way, per the fix above).

**Known real-data quirk, not chased further**: several of the real Shieldbreaker effects that use this field (`"Expose Crit Debuff N"`, `"SB Slow N"` — the latter using the pre-existing `INLINE_STAT_FIELD_MAP` path, unaffected by this specific fix) carry NO `.duration` field on the effect record at all, yet her own doc states finite durations ("+Crits Received lasts for 3 rounds. SPD lasts for 4 rounds"). Checked `rules.json` for any default-debuff-duration constant that might implicitly apply — none exists. Since neither of these inline-buff code paths has a "buff definition's own duration" fallback to check (unlike the `buff_ids` path, which falls back to `bd.duration` when the effect doesn't specify one — these are synthesized fresh, not looked up), both apply as permanent-for-combat when the effect itself has no `.duration`, which is what the real data supports today. Flagged rather than silently accepted or guessed at with a hardcoded number not backed by any parseable field — consistent with this project's "trace back to the game install source, never assume a doc/CSV value is combat-authoritative" principle. A real "Grapeshot Vulnerability N" (the same `crit_received_chance` mechanism, Arbalest/Musketeer's Grapeshot) DOES carry an explicit `.duration 3`, confirming the duration-tracking path itself works correctly when the data provides one.

New tests (`tests/engine/test_gaps.py`): `test_inline_buff_type_applies_hp_heal_received_percent` (Arb Stacking Heal 1 applies +20% `hp_heal_received_mult`), `test_inline_buff_type_crit_received_chance_with_real_duration_expires` (Grapeshot Vulnerability 1 applies +4% `crit_received_bonus`, still active after 2 of 3 rounds, expires on the 3rd).

## Shieldbreaker's signature survivability tool was 100% non-functional — Aegis Blocks, a genuinely new mechanic

Continuing the doc audit into Shieldbreaker (DLC), whose own doc calls Serpent's Sway "marvelous survivability": "Adds up to 2 Aegis Blocks... When hit by any non-zero direct damage, an Aegis Block is consumed, preventing damage/Deathblow check... Aegis does not protect against secondary effects (Stress, DoT, Debuff, KB/Pull, Stun, etc.)." Checked the real skill: `serpents_sway` carries `.effect "SB Aegis" "SB Serpent Speed N"` — "SB Aegis" is `effect: .name "SB Aegis" .target performer .chance 100% .health_damage_blocks 2 .on_hit true .on_miss false .queue true`. `.health_damage_blocks` had never been parsed anywhere in the codebase (confirmed via grep — zero hits for `health_damage_blocks`/`aegis` in any `.py` file) — meaning Shieldbreaker's signature defensive tool applied only its minor SPD buff and did nothing else, a genuinely new (not just unwired) mechanic this simulator had zero prior support for, unlike every other fix this session (which wired up an already-partially-modeled concept).

Confirmed the real stack cap directly in `shared/rules.json`: `"health_damage_blocks_max" : 2` — matching her doc's "Aegis Blocks stack up to 2" exactly, and previously unparsed too (0 references anywhere).

**Fixed**: `EffectDefinition.health_damage_blocks: Optional[int]` (parsed from `.health_damage_blocks`, `data/effect_loader.py`). `CombatRules.health_damage_blocks_max: int = 2` (parsed via `RulesLoader.KEY_MAP`, real default matching the shipped value in case a future mode override changes it). `CombatantState.damage_blocks: int = 0` (naturally resets to 0 every combat via `CombatEngine.reset()` rebuilding a fresh `CombatantState`, matching the real "removed at end of battle/quest" — no explicit clearing needed). `_apply_effect_to_target` grants blocks (capped at the rules max) when `eff.health_damage_blocks` is set. The actual negation lives in `engine/resolution.py`'s `resolve_attack` — scoped deliberately narrow, matching her doc's "doesn't protect against secondary effects" line: only the raw HP damage a landed attack roll deals is blocked (checked right after the `max(1, round(applied))` floor, so every landed hit qualifies as "non-zero"), consuming exactly one block and zeroing `applied_damage`/skipping the Death's Door check entirely — DoTs, Stress, debuffs, push/pull, and Stun all route through completely separate code paths untouched by this. `AttackResult.blocked_by_aegis: bool` lets `CombatEngine` log the block distinctly. Also wired into `_maybe_trigger_riposte`'s counter-attack damage (the same kind of direct-damage hit, just via a different code path than `resolve_attack`) for consistency, since a Shieldbreaker with Aegis up could in principle be riposted against by an enemy.

New test: `tests/engine/test_gaps.py::test_serpents_sway_grants_aegis_blocks_that_negate_damage` — a real Serpent's Sway use grants exactly 2 blocks (capped at the real rules value), the first landed hit fully negates damage and consumes one block, the second consumes the last, and the third (no blocks remaining) applies damage normally again.

## Bounty Hunter's signature "+DMG vs Human" was completely inert — `.monsterType` was never parsed

Continuing the doc audit into Bounty Hunter, whose own doc calls out Collect Bounty's "+DMG vs. Human starts from +15% at level 1 and scales with skill level" as a headline mechanic (alongside its already-correctly-implemented +90% DMG vs Marked). Checked the real skill record: `collect_bounty` carries `.effect "BH Dmg Marked" "Man Killer N"` — "BH Dmg Marked" is the already-fixed `key_status`-conditional mechanism, but "Man Killer N" uses a different, sibling field entirely: `.monsterType "man"` (`effect: .name "Man Killer 1" .target "performer" ... .monsterType "man" .combat_stat_buff 1 .damage_low_multiply 15% .damage_high_multiply 15% ...`). Grepped for `monsterType`/`monster_type` anywhere in the codebase: zero hits — this field had never been parsed at all, meaning Bounty Hunter's real "Man Killer" bonus (and 20 more real occurrences of the same shape — Beast/Unholy/Eldritch Killer effects on other classes, 25 total across `base.effects.darkest`) was completely inert regardless of what the target actually was.

Also confirmed `EnemyDefinition.enemy_types` (a real, already-parsed field — `enemy_type: .id "man"`/`"beast"`/etc. per-monster record, e.g. `cultist_brawler_A` → `["man"]`, `swine_prince_A` → `["beast"]`) was itself never consulted anywhere in `engine/combat_engine.py` before this — the data existed but had no consumer.

**Fixed**: `EffectDefinition.monster_type_condition: Optional[str]` (parsed from `.monsterType`, `data/effect_loader.py`). `_compute_mark_damage_mult` (already generalized this session to check any `.keyStatus` value, see below) now ALSO checks `eff.monster_type_condition` against the attack target's real `EnemyDefinition.enemy_types` (via `_get_enemy_def`, guarded to enemies only — heroes have no monster type) — the two conditions (`key_status`-vs-transient-status, `monster_type_condition`-vs-permanent-type-tag) are independent `or`-combined, since a single skill's effect list can carry either or both (Collect Bounty carries one of each).

New test: `tests/engine/test_gaps.py::test_monster_type_condition_applies_bounty_hunters_man_killer` — Collect Bounty's damage multiplier is `>1.0` against `cultist_brawler_A` (real `enemy_types == ["man"]`) and exactly `1.0` against `swine_prince_A` (real `enemy_types == ["beast"]`).

## Whole-battle self-buffs were expiring after 1 round — `buff_duration_type` was never parsed

Continuing the Grave Robber audit into her Toxin Trickery skill ("1 Use per Battle... Buffs last for the whole battle" per her own doc — grants "Shadow Blood N" +DEF and "GR Self Speed N" +2 SPD): both real effects carry `.duration 1` (a normal-looking 1-round value) but ALSO `.buff_duration_type combat_end` — a field this project had never parsed at all (confirmed via grep: 0 references anywhere before this fix). Real semantics, confirmed against her doc and cross-checked against how the field is used elsewhere: `buff_duration_type combat_end` overrides the numeric `.duration` entirely — the buff lasts for the rest of the fight, not a real N-round countdown. Without this, Toxin Trickery's signature whole-battle Dodge/SPD trade-up (the entire point of the skill, per her build-around identity) reversed after just 1 round like a normal short buff.

**Scope, verified directly**: 52 real occurrences in `base.effects.darkest` (0 in DLC files), always the value `"combat_end"` (never any other non-default value) — Crusader's `"Bulwark 1-5"`/`"Crusader Bulwark Mark"` (his post-Guard PROT buff), Grave Robber's `"Shadow Blood N"`/`"GR Self Speed N"` (Toxin Trickery), Highwayman's `"Tracking Buff N"` (Take Aim's stacking ACC buff), Leper's `"Leper Strength N"`/`"Leper Vulnerability"`/`"Leper Protect N"`/`"Leper Mark Self"` (several of his self-buffs/debuffs), Man-at-Arms' `"Bolster N"`/`"Bolster Stress Buff N"` (Bolster), Plague Doctor's `"PD Vapours Buff N"` (Blinding Gas's self-buff), and a few boss/monster effects (`"Mortal Weakness"`, `"Turbo Mark"`). Found both real occurrence shapes in the data: inline stat fields (`"GR Self Speed N"`'s `.speed_rating_add`, no `.buff_ids`) and real `.buff_ids` references (`"Leper Vulnerability"` → `LeperDEFVuln`/`LeperDMGVuln`; `"Bolster Stress Buff N"` → `STRESSDMG-10`) — both code paths needed the fix.

**Fixed**: `EffectDefinition.buff_duration_type: str = "rounds"` (parsed from `.buff_duration_type`, `data/effect_loader.py`), default preserving normal countdown behavior for every effect that doesn't set it (the vast majority). Both `_apply_effect_to_target` call sites that decide whether to track a buff for later reversal (the inline-stat-field branch, and the `buff_ids` branch's `effective_duration` fallback) now force `remaining_rounds`/`effective_duration` to `None` — i.e. never tracked for expiry, applied exactly like a trinket/quirk's permanent-for-combat buff — whenever `eff.buff_duration_type == "combat_end"`, regardless of what the numeric `.duration` says.

New test: `tests/engine/test_gaps.py::test_buff_duration_type_combat_end_overrides_numeric_duration` — Grave Robber's real "GR Self Speed 1" applies its +2 SPD once, then survives 5 rounds of `_tick_buff_durations()` unreversed (a real N-round buff would have expired after `duration=1`'s first tick).

## Mark/Vulnerable status was never actually applied to anything — a related bug found while generalizing the conditional-buff fix

While tracing Grave Robber's Thrown Dagger (`.effect "GR Acc Buff N" "GR Dagger Dmg Marked N" "Poison Killer N"`) through the conditional-buff investigation above, generalized `_compute_mark_damage_mult` to check any real `.keyStatus` value ("tagged"/"poisoned"/"bleeding"/"stunned" — 35/6/1/5 real occurrences respectively in `base.effects.darkest`, not just "tagged") via the same `_actor_has_status` helper the buff-level `actorStatus` rule uses — previously an early `if not target.is_marked: return 1.0` short-circuit meant a skill's OTHER `keyStatus` values (Grave Robber's own "Poison Killer N", bonus damage vs a currently-blighted target) were never evaluated at all whenever the target happened not to be marked.

That generalization surfaced a much bigger, adjacent bug while checking exactly how "marked" gets set in the first place: `_apply_effect_to_target`'s mark-setting line keyed off `eff.key_status == "tagged"` — but `key_status` is a **different, same-named-by-coincidence mechanism** from the real Mark-application field. Grepping the raw `.darkest` files: every real "apply Marked/Vulnerable to an enemy" effect (`"Mark Target"`, `"Vulnerability Curse 1-5"` — Occultist's Disruptive Curse/Vulnerability Hex — `"Arb Mark Target"`, `"BH Mark Target"`/`"BH Minor Mark"`, `"HM Mark Target"`, and boss/monster "X Mark" effects) uses a completely separate real field, `.tag 1`, paired with a genuine `.duration` (2-4 rounds) and `.target "target"`. Meanwhile every `key_status == "tagged"` effect (`"GR Dagger Dmg Marked N"`, `"BH Dmg Marked"`, `"HW Pistol Dmg Marked N"`, `"HM Dmg Marked N"`, etc.) is a **conditional damage-bonus-vs-marked-target check** targeting `"performer"` (the attacker, not the enemy) — confirmed 100% of real `.keyStatus` occurrences target "performer". So the pre-fix code was flagging the **attacking hero** as Marked every time they landed a Dmg-Marked-synergy skill (since `eff_target` resolves to "performer" for those effects), while the real Mark-applying effects — including Occultist's signature Vulnerability Hex — never set `is_marked` on anyone at all. `EffectDefinition.is_mark_effect` didn't even exist as a parseable field before this (`.tag` was completely unparsed, 0 references anywhere in the codebase).

`.tag 1` is also reused by several unrelated self-buffs (Bulwark 1-5, "Crusader Bulwark Mark", "Leper Mark Self", "Solo Mark Self", "Gibbering Buff", "Buff Guard") that all target `"performer"` and bundle a real stat buff (PROT, etc.) — almost certainly the real game engine's own "unique/refreshable buff instance" bookkeeping, unrelated to the Marked *status* this simulator tracks via `is_marked`. Confirmed the discriminator is clean: every genuine Mark-application effect uses `.target "target"`; every self-buff reuse uses `.target "performer"`. Fix only sets `is_marked` when both `eff.is_mark_effect` and `eff.target == "target"` hold, avoiding a second new bug (flagging Crusader/Leper/Jester's own self-buffs as "the enemy is Marked").

**Fixed**: `EffectDefinition.is_mark_effect` (parsed from `.tag`, `data/effect_loader.py`). `_apply_effect_to_target` now sets `is_marked`/`mark_rounds_remaining` (new `CombatantState` field, same shape as `stealth_rounds_remaining`/`guard_rounds_remaining`) off `is_mark_effect and target=="target"`, ticked down each round via new `_tick_mark_durations` (wired into `_end_of_round` alongside the other duration ticks) so Mark genuinely expires after its real 2-4 rounds instead of being permanent for the rest of combat once applied (the pre-fix code, even for the wrong entity, never expired it either — no duration tracking existed for `is_marked` at all before this). `eff.remove_status == "tagged"` (dead against real data — 0 real files use `.removeStatus` at all, confirmed by grep) is left as harmless pre-existing dead code, now also clearing `mark_rounds_remaining` for consistency should it ever fire.

New tests (`tests/engine/test_gaps.py`): `test_mark_target_effect_actually_marks_the_target_now` (Occultist's real "Mark Target" effect marks the enemy, not the performer, and expires after 3 rounds), `test_dmg_marked_synergy_effect_no_longer_self_marks_the_performer` (Bounty Hunter's "BH Dmg Marked" no longer flips `is_marked` on anyone), `test_compute_mark_damage_mult_generalizes_beyond_tagged` (Grave Robber's Poison Killer applies its bonus vs a blighted-but-unmarked target). Full suite: 301 → 305 passed (net, alongside the conditional-buffs work below, from the pre-session-segment 290).

**Real test fallout, both from Mark now genuinely working end-to-end**: `tests/engine/test_monster_ai_friendly.py::test_swine_prince_ally_dead_skill_gates_rage_trigger` and `test_stun_skill_non_stunned_gate_shifts_weight` both broke — not from a bug in the fix, but because `CombatEngine.reset()`'s pre-hero-turn auto-resolution (a faster enemy legitimately acting before the first hero decision) can now genuinely mark a Crusader (SPD 3, the slowest hero) for the first time: swine_piglet's own kit uses "Piggie Mark", spider_web itself uses "Mark Target". Both swine_prince's and spider_webber's real monster brains separately carry an `effect_key_status_skill` desire (`base_chance=10`, no `combat_skill_id` of its own — routes into the shared flat weight pool per-skill, per `_select_enemy_skill`) gated on `any(h.is_marked for h in heroes_alive)` — previously always false (Mark never actually applied to anyone), so this desire was silently 100% inert everywhere in the game. Now that it can fire, it adds a small constant background weight to every one of a brain's usable skills whenever a hero happens to be marked — which is real, correct behavior, but confounds both tests' narrowly-scoped assertions (obliterate_enraged reachable via a second, legitimate trigger even with the ally alive; the non_stunned_heroes_min gate's measured contrast diluted by the extra flat weight in both measured phases). Fixed by explicitly clearing `is_marked` on the test heroes right after `reset()`, isolating each test's actual mechanic from this now-also-correctly-firing desire — not a workaround for a real problem, just controlling for a second real effect the test wasn't written to account for.

## Conditional (rule_type-gated) trinket/quirk buffs were never actually conditional

Continuing the doc audit into Grave Robber, her Thrown Dagger's effect list ("GR Acc Buff N", "GR Dagger Dmg Marked N", "Poison Killer N") led to checking how the engine resolves `.keyStatus`-gated effects. That in turn surfaced a much bigger, previously invisible gap: `shared/buffs/base.buffs.json`'s `BuffDefinition` entries carry a `rule_type`/`rule_data`/`is_false_rule` field (and a `remove_if_not_active` flag, always `false` in real data except a single irrelevant outlier) that determines whether the buff is **currently** active — not a decoration. `data/buff_loader.py` parsed `rule_type`/`is_false_rule` into every `BuffDefinition` but never parsed `rule_data` at all, and `CombatEngine._apply_buff_to_stats`/`apply_trinkets_and_quirks` never consulted `rule_type` anywhere — every trinket/quirk buff was applied once at reset as a permanent stat delta for the whole fight, regardless of rank, HP, guard status, round number, or the target's status.

**Scope, verified directly against the real buff file**: 1060/2019 buff entries (52%) have a non-"always" `rule_type`. Breaking down by id prefix: 345 `TRINKET_*`, 100 `QUIRK_*`, 467 `TB` (per-tier trinket-bonus scaling, also trinket-sourced), plus ~36 affliction/virtue personality-type buffs (`afflictionFearfulBuff*`, `virtueStalwartBuff*`, etc.), ~40 town/camping-only buffs, and a handful of hero-skill-conditional ones (Jester's `build_to_finale`, Man-at-Arms' `Bellow`, Abomination's `rake`). This matters far more than it would have earlier in the session because trinkets/quirks are now auto-applied to every hero in a real comp loadout (see "Trinkets/quirks now auto-applied" below) — meaning a meaningful fraction of every trained comp has been getting permanent, unconditional bonuses (or occasionally penalties, from negative quirks/curse trinkets) that should only apply situationally.

**Rule types found, with real sample IDs and `rule_data` shape** (verified via direct JSON inspection, not guessed):
- `in_rank` (144) — `rule_data.float` is a specific 0-indexed position (0, 1, or 3 seen; never a bitmask/range). `TRINKET_FRONTRANK_ACC_D1` (float=0), `TRINKET_BACKRANK_ACC_B1` (float=3), `TRINKET_NOTBACKRANK_ACC_D1` (float=3, `is_false_rule=true` — "active whenever NOT in this rank").
- `meleeonly`/`rangedonly` (61+67) — depends on the skill type of the action currently being used, e.g. `DMGLMELEE10`/`TRINKET_meleeonly_CRIT_B1`.
- `hpbelow`/`hpabove` (66+29) — `rule_data.float` is a max_hp fraction threshold (e.g. `QUIRK_hpbelow_DMGL_B` at 0.5, `TRINKET_hpabove_DEF_B1` at 0.75).
- `at_deaths_door` (42) — e.g. `TRINKET_at_deaths_door_CRIT_B1` (+9% crit while on Death's Door).
- `is_guarded` (10) — e.g. `MAA_Command_GuardedDMGL_1` (+15% DMG multiplier while the bearer is actively guarding an ally).
- `firstroundonly` (80) — e.g. `SPDFIRSTROUND4` (+4 SPD, round 1 only).
- `stress_above` (3) — `rule_data.float` is a raw stress value, not a fraction (e.g. `QUIRK_stress_above_ACC_D` at 70).
- `actorStatus`/`is_actor_status` (28+5) — `rule_data.string` is a status name ("tagged", "bleeding", ...) checked against the buff's own bearer. `MAA_Bellow_CritReceived_1..5` (+5-10% `crit_received_chance` while the bearer — the target Bellow was used on — is marked/tagged); `com_hel_thirsting_blade`'s own `TRINKET_com_hel_thirsting_blade_crit_v_bleeder_B5` (+8% crit while the bearer's *target* is bleeding — this is the real, correct semantics behind "Thirsting Blade," previously believed unconditional).
- **[SUPERSEDED 2026-08-09 — personality types are implemented from `shared/trait/trait_library.json`; see the top of this file. The `afflictionXBuffN` ids this paragraph is about turned out to be legacy: no trait references them.]** Declared **out of scope**, same tier as Crimson Curse/camping (documented, not silently ignored): `afflicted`/`virtued` (~36 buffs) — these only ever gate the affliction/virtue **personality-type** buffs (Fearful/Paranoid/Masochistic/Abusive/Selfish/Depressed/Irrational; Stalwart/Courageous/Focused/Powerful/Vigorous) — a real DD mechanic where breaking/resolving assigns one specific personality with its own in-combat behavior, needing a whole separate "which personality did this roll into" subsystem this simulator doesn't have at all (today, `is_afflicted`/`is_virtuous` are just flags with generic stress consequences). Also out of scope: `monsterType`/`monsterSize`/`attacking_monster_type`/`monster_type_count_min` (97+4+17+22 — real but narrow "extra resist/damage vs a specific monster tag" trinkets/quirks) and `in_camp`/`in_dungeon`/`in_corridor`/`in_activity`/`skill` (town/camping/exploration-phase buffs and Jester's Build to Finale stacking counter — no camping/exploration modeled at all, consistent with existing scope).

**Fix — a genuinely new subsystem, not a one-shot field wire-up like every other fix this session** (architecture confirmed necessary by the zero-instances-of-`.duration`-on-a-conditional-buff finding below): `BuffDefinition` gained `rule_data_float`/`rule_data_string` (parsed by `data/buff_loader.py`, previously dropped entirely). `CombatantState` gained `conditional_buffs: list[BuffDefinition]` (raw, un-applied) and `active_conditional_buffs: dict[str, int|None]` (buff id → the exact delta `_apply_buff_to_stats` returned, for exact reversal, same convention as `ActiveBuff.applied_delta`). `apply_trinkets_and_quirks` now splits each buff via the new `_apply_buff_ids`: `rule_type == "always"` applies immediately exactly as before; anything else is deferred into `conditional_buffs` instead of baked in. `CombatEngine._is_conditional_rule_active(c, bd, skill_id)` evaluates the 10 in-scope rule types against current state (position, hp/max_hp, is_deaths_door, guard_target_id, self.round, stress, or `_actor_has_status` for actorStatus — "tagged"→`is_marked`, "bleeding"→`bleeds`, "poisoned"/"blighted"→`blights`, "stunned"→`is_stunned`), XORed with `is_false_rule`; any rule type not in the explicit list returns `True` (matches pre-fix always-on behavior rather than silently dropping the buff). `_sync_conditional_buffs(c, skill_id)` toggles each conditional buff on/off against `active_conditional_buffs` (apply via `_apply_buff_to_stats` when newly active, reverse via `_reverse_buff_from_stats` when newly inactive) — called once per action via `_sync_all_conditional_buffs(performer, skill_id)`, hooked into both `CombatEngine.step()` (hero actions) and `_execute_enemy_skill()` (enemy actions, so a hero's rank/HP/guard-based conditional buffs stay correct when an enemy is the one attacking). Only `performer` gets `skill_id`-dependent evaluation (meleeonly/rangedonly) since that's specifically about which skill THEY are about to use, not a standing field-wide state. Verified via direct JSON inspection that real conditional trinket/quirk buffs never combine a non-"always" `rule_type` with a `.duration` (0 matches across the whole file) — confirming the sync/toggle design is complete on its own, with no separate duration-expiry interaction to handle for this buff class.

**A connected, independent gap found and fixed alongside this**: `crit_received_chance` (the stat_type `MAA_Bellow_CritReceived_N` uses) had no handler at all in `_apply_buff_to_stats`/`_reverse_buff_from_stats`, and `engine/resolution.py`'s `check_crit` never took a `target` parameter to consult it — Man-at-Arms' Bellow debuff would have synced as "active" under the new rule-conditional system but still done nothing. Added `CombatantState.crit_received_bonus`, wired into both stat-apply paths, `check_crit(attacker, target, skill, rng)` (now includes `target.crit_received_bonus` in the crit roll), and the Riposte counter-attack's own inline crit roll (`_maybe_trigger_riposte`, now includes `attacker.crit_received_bonus` — `attacker` there being who the riposte lands on).

**Test fallout, both pre-existing tests that had baked in the old unconditional-buff assumption**: `tests/env/test_expedition_env.py::test_reset_applies_real_trinkets_and_quirks_when_comp_has_one` and `tests/env/test_expedition_sequence_env.py::test_trinkets_reapply_at_the_start_of_every_internal_combat` both asserted a sampled Hellion's `.crit` was higher than baseline because of "Thirsting Blade" — but that trinket's crit bonus is exactly the `actorStatus`/bleeding-target case above, correctly inactive at a fresh reset before any attack has landed. Switched both to check `.atk_bonus` instead (Thirsting Blade's `TRINKET_com_hel_thirsting_blade_att_rating_B15`, +15% ACC, `rule_type="always"` — genuinely unconditional), same pattern as several rule-formula test corrections earlier in this session (e.g. the hidden +5 ACC fix). New test file `tests/engine/test_conditional_buffs.py` (11 tests) covers each in-scope rule type directly via synthetic `BuffDefinition`s, the apply/defer split in `apply_trinkets_and_quirks`, an end-to-end HP-crossing-threshold apply/reverse round-trip, the performer-only skill_id scoping in `_sync_all_conditional_buffs`, and the `crit_received_chance` wiring. Full suite: 301 passed (290 → 301: +11 new, 2 corrected).

## A second, much broader instance of the Rallying Flare bug — Hellion's own signature skill was affected

Continuing the audit (user: "lets keep going") into Hellion, whose doc mentions a Color of Madness trinket detail almost in passing: *"The 'On Attack Miss: Self: -5 DMG' applies only once even when AoE attacks (Breakthrough, Barbaric YAWP!) fail to land against multiple targets."* That's the exact same bug shape as the already-fixed Rallying Flare issue — a self/reactive effect nested inside an AoE skill, needing to fire once per use, not once per target — stated as a documented quirk of the real game, meaning the *fix* for it is a real mechanic too, not just the bug.

Chasing this down a side path first: the specific trinket behind that quote (`com_hel_thirsting_blade`, Thirsting Blade) uses a real, previously entirely unseen trinket mechanism — `attack_skill_additional_effects` (distinct from a trinket's regular `buffs` list) — found in exactly 3 Color of Madness trinkets (Crusader's Non-Euclidean Hilt, Arbalest's Keening Bolts, Hellion's Thirsting Blade), none of which are parsed by this project at all. Confirmed narrow-but-real usage: Thirsting Blade alone is equipped on Hellion in 22 of the ~132 real comp loadouts this project trains against, including 3 of the standing 12-comp eval sample (All Good Hellion, Dragon Blood, Dark Ritual: Ballad). **Deliberately not implementing this specific trinket-reactive-effect subsystem this pass** — it needs a genuinely new hook (attacker gets an extra effect check after every attack use, keyed off trinket ownership rather than skill data) for a narrow set of 3 DLC trinkets, and its net effect is a self-damage/self-stress *downside* currently missing (making those Hellion builds slightly too generous, not too harsh) — lower priority than the broader thing the same doc quote actually pointed at.

**The broader thing**: the doc's phrase "applies only once even when AoE attacks... fail to land against multiple targets" describes a *general* rule, and checking the underlying data confirmed it: the real `.apply_once` field (`EffectDefinition.is_apply_once`) is the mechanism, and it's used far beyond just this one trinket. Wrote a script to check every real hero/monster AoE skill for a `target: "performer"` effect in its list (the same shape as the already-fixed Rallying Flare bug, just self-targeted instead of group-targeted) — **57 real cases**, and checked a representative sample directly against the raw `.darkest`/JSON data: every single one already has `.apply_once true`. This field was parsed (`data/effect_loader.py`, an earlier session) but never once consulted anywhere in `engine/` — confirmed via a direct grep.

**Concrete, high-impact cases this surfaced, independent of the trinket investigation**:
- Hellion's own signature `barbaric_yawp` (Barbaric YAWP, her double-frontline-stun, "one of the strongest tools in her arsenal" per her own doc) and `breakthru` (Breakthrough) would double/triple-stack her real "Hellion Exhaust" self-debuff (`-3` speed, `HellionExSPD`, plus matching `-DMG` terms) depending on how many of the AoE's 2-3 enemy targets were actually hit.
- Jester's `battle_ballad`, `harvest`, and `solo` would over-charge "Build to Finale"/"Build to Finale Song" — her stacking counter toward the Finale skill — once per AoE target instead of once per use.
- Occultist's `abyssal_artillery` would double-stack "Abyssal Killer" (a +DMG-vs-Eldritch self-buff).
- Several monster AoE skills — Shambler's `shamble_fwd`/`shamble_back` (tentacle summons), Necromancer's `unholy_curse`/`unholy_judgement`/`unholy_smite` (skeleton reinforcement summons) — would spawn extra, unintended summons per AoE hit landed, a real enemy-side balance bug alongside the hero-side ones.

**Fixed**: widened the existing `_split_group_effect_names` helper (added for the Rallying Flare fix) to pull an effect into the once-per-action bucket when *either* `eff.target in ("performer_group_other", "performer_group")` *or* `eff.is_apply_once` is true — the two signals are independent and not mutually exclusive (`FlareHealStress N` has both; `beast_stress_party`/`human_stress_heal_party_N`, Abomination's Transform party-stress effects fixed earlier this session, have only the group-target signal since they're never used inside an AoE loop anyway; `Hellion Exhaust` has only `apply_once`). No other code changes needed — the 3 call sites already applied `group_names` (now the wider "once-names") exactly once per action via `(performer, performer, ...)`.

New test: `tests/engine/test_gaps.py::test_apply_once_effect_on_aoe_attack_fires_once_not_per_target` — Barbaric YAWP against 2 living enemies applies `HellionExSPD`'s real `-3` speed delta exactly once (not `-6`, which the old per-AoE-target-hit bug would have produced). Full suite: 290 passed (from 289).

**Not yet retrained** — folded into whatever the next retrain covers.

## The broadest fix of the whole session: every attack roll was missing a universal hidden +5 ACC

> **SUPERSEDED 2026-08-19 — the effect is real, the mechanism recorded below is not.**
> Read from the game's own to-hit function (`DECOMP.md` F4): there is no +5 anywhere in
> it, and the chance is never clamped. The 5% floor comes from `roll >= cutoffAlwaysHit
> (0.95)` hitting regardless of the chance — the top 5% of the roll range. The two rules
> agree below a raw chance of 0.90 and diverge above it, where this one under-hits by up
> to 5pp; measured at 24 of 166 real captured attacks. The reasoning below is kept because
> it was right about the *observable* and is a fair record of how far two independent
> secondary sources can agree and still describe the wrong mechanism.

Continued the hero-doc audit into Houndmaster, whose doc explains a Guard Dog nuance: even with massive DODGE investment, "immunity against almost all enemy attacks" tops out at "95% of the time," never 100% — because *"A hidden +5 ACC modifier always applies to any hero/enemy attack so an attack always has a minimum 5% chance to hit, regardless of the target's DODGE."*

This is a much bigger claim than a single skill quirk — it's a universal modifier on every attack roll in the game. Before trusting it enough to change `compute_hit_chance` (the single most-called function in the whole combat resolution pipeline), cross-checked it with `WebSearch` rather than relying on one community doc alone. Independent confirmation: *"Everyone has a 5% chance to hit at bare minimum - even if targets have infinite dodge. This is achieved through a hidden +5 added to the calculation: ACC + 5 - DODGE = Hit Chance."* Two independent sources, same exact number, same exact mechanism.

Checked `engine/resolution.py`'s `compute_hit_chance`: `acc = skill.atk + attacker.atk_bonus + acc_bonus; hit_chance = acc - dodge` — no `+5` anywhere. Checked whether this might already be covered under a different name: `rules.json`'s `cutoffAlwaysMiss` is `0.00` (already loaded, already correctly used as the lower clamp bound) — genuinely a *different*, pre-existing mechanic (a raw safety floor preventing a mathematically negative hit chance in extreme cases), not the practical 5% floor players actually experience in normal play. No `rules.json` field holds a `5`/`0.05` value in any accuracy-related context either (checked directly) — this is a real, hardcoded-in-the-binary mechanic with no backing data file, the same treatment already given to `TIER_TO_HERO_LEVEL` and the heart-attack stress threshold.

**Fixed**: new `HIDDEN_ACC_BONUS = 0.05` module constant in `resolution.py`, added into `compute_hit_chance`'s formula ahead of the existing `cutoff_always_miss`/`cutoff_always_hit` clamp (so the clamp's own already-correct behavior is untouched). Also mirrored into `_maybe_trigger_riposte`'s separate hand-rolled hit-chance calculation (added a few sessions-worth of investigation ago in this same file) for consistency — Riposte doesn't go through `compute_hit_chance` since there's no full `SkillLevelData` to build for it, just a raw `riposte_acc` float.

**This is universal — it touches every hero attack, every monster attack, and every riposte in the whole simulator**, which is exactly why 4 pre-existing tests broke and needed *correcting*, not just extending:
- `test_resolution.py::test_hit_chance_50_acc_vs_25_dodge` — a direct unit test of the formula; corrected its expected value from 25% to 30% (50% ACC + 5% hidden − 25% dodge).
- `test_carry_over_reset.py::test_none_carry_over_is_identical_to_before_this_param_existed` and `test_carry_over_index_aligned_shorter_than_hero_defs_is_safe`, plus `test_heart_attack.py`'s shared `_fresh_hero()` helper — all three used Crusader (SPD 3, confirmed the slowest hero in the game per multiple docs read this session) against a faster enemy at a fixed seed, implicitly relying on `CombatEngine.reset()`'s pre-hero-turn auto-resolution (documented behavior: a faster enemy acts before the first hero decision) never landing a hit with that seed's RNG roll. With the more accurate (higher) hit chance, that roll now lands — a real, expected side effect of a correct fidelity fix, not a bug in the fix. These "freshness" tests never actually needed zero combat resolution, just a hero fast enough to never be attacked first — switched all three to Jester (SPD 9) instead, which is robust against any real enemy's speed, not just this specific brawler.

New test: `test_resolution.py::test_hidden_acc_bonus_gives_minimum_five_percent_hit_chance` (0% ACC vs 0% dodge → exactly 5%, not 0%). Full suite: 289 passed (from 288).

**Retrained again to include this alongside the still-in-flight Rallying Flare/Riposte-ACC/is_crit_valid/stunned-riposte retrain** (stopped and restarted a 3rd time in that stretch — see the training note above — all landing within the first ~15 minutes of the run each time, so nothing meaningful was lost by restarting).

## Stunned defenders could still Riposte — one more gap from the same Riposte-ACC investigation

Read `HEROES/Heroes/Highwayman *.md` right after the Man-at-Arms Riposte-ACC fix, since he's the other of the 2 real riposte classes. Two things confirmed independently: (1) his doc states the exact same `riposte_skill:` mechanic verbatim — "Highwayman's Riposte's ACC stays fixed at 85 and DOES NOT scale with Duelist's Advance's Skill Level" — matching the real data's `.atk 85% .crit 5%` for both Highwayman and Bounty Hunter, strong independent confirmation the fix modeled the right mechanic. (2) A new, separate claim: "Ripostes aren't activated when Stunned," repeated again under a trinket note ("20% Stun Resist is crippling for Highwayman, as Ripostes require avoiding Stuns").

Checked `_maybe_trigger_riposte`: it never referenced `defender.is_stunned` anywhere. Riposte is a purely reactive ability — it fires off *being attacked*, not off the defender's own turn — so the existing stun-skip logic in `_advance_to_next_hero_turn` (which correctly skips a stunned combatant's own turn) never incidentally covered this; a stunned hero with an active Riposte could still counter-attack when hit.

**Fixed**: one added condition (`defender.is_stunned`) to the existing early-return guard in `_maybe_trigger_riposte`. New test `tests/engine/test_gaps.py::test_stunned_defender_riposte_never_triggers` (a stunned Man-at-Arms with an active Riposte and a zero-dodge attacker — conditions that would otherwise guarantee a connecting counter-attack per the ACC fix above — deals zero damage). Full suite: 288 passed (from 287).

**Training note**: this and the `is_crit_valid` fix both landed while the Rallying Flare + Riposte-ACC retrain was still in its first ~10-15 minutes (170k-235k/3M steps) both times — restarted from scratch each time rather than let a partial run finish, so all 4 fixes from this stretch (Rallying Flare AoE-duplication, Riposte ACC, `is_crit_valid`, stunned-Riposte) train together as one unit instead of needing yet another retrain immediately after.

## is_crit_valid never consulted — crit-ineligible skills (Plague Doctor's blinding_gas/disorienting_blast, again) could still crit

Continued reading `HEROES/Heroes/Vestal *.md` (her comp — Vestal/Highwayman/Highwayman/Man-at-Arms vs. Hag — turned out to literally BE this session's "Riposte Style (Hag)" eval comp, video and all, confirming that comp's real intended strategy is exactly "Man-at-Arms and Vestal for survivability... then kills things quickly with Ripostes"). Her doc flags Illumination bluntly: "CANNOT be used from Rank 4. CANNOT CRIT."

Checked `engine/resolution.py`'s `check_crit` — it only ever computes `attacker.crit + skill.crit` and rolls against it, with zero reference to `SkillLevelData.is_crit_valid`. That field IS parsed (`data/hero_loader.py`, default `True`), and real data explicitly sets it `False` on 65 skill-level records across 10 hero files — not a rare edge case. Confirmed several are skills already central to this session's investigation: Plague Doctor's `blinding_gas` and `disorienting_blast` (her two AoE control skills, both used heavily by the "Stun Control" comp family), Arbalest/Musketeer's `flare` (Rallying Flare, already fixed once this session for the AoE-effect bug above), and Vestal's `illumination` itself.

**Consequence**: a crit on a crit-ineligible skill incorrectly triggers `_stress_relief_performer` (crit stress relief, both self and the party-wide companion-relief chance) and per-class `_trigger_on_crit_passive` — real mechanical benefits the real game never grants for these skills, since they can never actually crit there.

**Fixed**: `check_crit` returns `False` immediately when `not skill.is_crit_valid`, before ever rolling. New test `tests/engine/test_on_crit_passives.py::test_is_crit_valid_false_skill_never_crits` verifies both directions with the crit chance forced to 100%: `blinding_gas` (positioned correctly, rank 4, with 4 enemies so its real `~34` backline-AoE target actually resolves) never logs a crit and never relieves stress, while `manacles` (no override) under the identical forced-100%-crit setup still does. Full suite: 287 passed (from 286).

**Not yet retrained specifically for this fix** — folded into whatever the next retrain covers (the Rallying Flare + Riposte-ACC retrain was already in flight when this was found).

## Riposte counter-attacks were unmissable — directly affects this session's two best comps

Continued the same audit method ("yes, lets keep hunting for more gaps, as more fidelity is clearly making the model strlnger"), reading `HEROES/Heroes/Man-at-Arms *.md` next since "Riposte Style (Hag)" and "Holy Hymn: Riposte" — the #1 and #2 best-performing comps in every eval this session — are both built around Riposte (Man-at-Arms + Highwayman together in both).

The doc has an explicit, easy-to-verify technical claim under "Things to note": *"Man-at-Arms' Riposte's ACC stays fixed at 90 and DOES NOT scale with Retribution's Skill Level. Riposte's ACC is AFFECTED by ACC modifiers such as quirks, trinkets, Buffs/Debuffs."* This describes a real accuracy check specific to the counter-attack itself. Checked `_maybe_trigger_riposte`: it rolls `chance` (`on_hit_chance`/`on_miss_chance`, real data: always 100% once a riposte is armed) to decide whether a counter-attack is *attempted*, then applies damage completely unconditionally — no second roll for whether the counter-attack actually *connects*. Every riposte in this simulator's history has been a guaranteed hit.

Found the real source of the doc's "fixed at 90" claim: a data record type this project had never looked for at all, `riposte_skill:` — distinct from `combat_skill:`, syntactically generic enough that the parser already handled it fine, just nobody ever asked for `_type == "riposte_skill"`. Present in exactly 3 hero files (confirmed via `grep -rl "riposte_skill:" heroes/` — no others):
- `man_at_arms.info.darkest`: `.atk 90% .crit 0%`
- `bounty_hunter.info.darkest` / `highwayman.info.darkest`: `.atk 85% .crit 5%` each

All three are a single record with no level progression at all (unlike every `combat_skill:`, which has 5 levels) — directly confirming "does not scale with Retribution's Skill Level." (One monster file, `brigand_barrel_D`, also has a `riposte_skill:` record — out of scope, a rare boss-tier variant; the fix below falls back to the old guaranteed-hit behavior for any defender without hero-side `riposte_acc` data, which covers this case safely.)

**Fixed**: `HeroDefinition.riposte_acc`/`riposte_crit_mod: Optional[float]`, parsed from `riposte_skill:` in `HeroLoader._build_hero`. `_maybe_trigger_riposte` now computes a real hit chance (`riposte_acc + defender.atk_bonus - attacker.dodge`, clamped the same way every other attack's hit chance is) and rolls it before applying any damage; crit chance now adds `riposte_crit_mod` on top of the setup-effect's own `crit_mod` (two genuinely separate, additive real crit sources, mirroring how any normal skill's crit chance is `attacker.crit + skill.crit`).

New test: `tests/engine/test_gaps.py::test_riposte_counterattack_can_miss` (verifies both directions deterministically — an overwhelmingly-dodgy target makes the riposte miss with 0 damage dealt, a zero-dodge target still reliably connects). The pre-existing `test_riposte_counterattacks_on_hit` (fixed seed 7) still passes unchanged — Man-at-Arms' 90% base ACC against a low-dodge `cultist_brawler_A` was already likely to hit anyway, so that seed's outcome didn't change, it just stopped being *guaranteed*. Full suite: 286 passed (from 285).

**Expected direction of the next retrain's numbers, stated honestly in advance**: this fix should make Riposte-centric comps *harder*, not easier — their signature mechanic can now miss, same as everything else in the game. If "Riposte Style (Hag)"/"Holy Hymn: Riposte"'s zero-death rates drop in the next eval, that's the fidelity fix working as intended, not a regression to chase — more faithful ≠ always better numbers, it means the numbers finally mean what they claim to.

## Stall/reinforcement fix retrained — the "Stun Control" family breaks out of 0% across the board, in every band, for the first time all session

Retrained from scratch (3M steps, ~2h04m, restarted once to fold in the accelerated/disabled-stalling follow-up so it trained as one unit) and re-ran the standard 12-comp/seed-1 sweep. This is the strongest result of the entire session:

| Comp | Zero-death short (before→after) | medium | long |
|---|---|---|---|
| **Stun Control: Blight** | 0%→**8%** | 0%→**8%** | 0%→**4%** |
| **Stun Control: Beast** | 16%→**30%** | 10%→**18%** | 6%→**14%** |
| **Stun Control: Blood** | 0%→**8%** | 0%→2% | 0%→**4%** |
| Riposte Style (Hag) | 54%→**62%** | 46%→48% | 28%→**32%** |
| Holy Hymn: Riposte | 30%→**46%** | 20%→**44%** | 14%→30% |
| All Good Hellion | 16%→**40%** | 20%→18% | 8%→12% |

**Every single one of the three "Stun Control" comps — the ones the user's very first report in this investigation was about — is now positive across all three length bands, for the first time in this entire session.** Stun Control: Blight in particular: zero-death went from a flat 0% to 8/8/4%, `mean_final_stress` dropped further (was already improved to 90-137 after the heal-effect fix, now 90-109), and `mean_combats_reached` more than doubled (was stuck at 1.4-1.8 combats after the heal-fix retrain — the exact regression that triggered this whole investigation — now 4.0-4.8). Stun Control: Beast now has the *lowest* mean_final_stress of any comp in the entire 12-comp sweep (63-69, beating even Riposte Style (Hag)'s 64-124). Every other comp in the sweep also improved or stayed flat/roughly even — no regressions this time, unlike the previous two retrains in this file, which each traded gains on some comps for losses on others.

This is about as direct a causal chain as this project gets: comp family reported stuck → replayed it → found the real mechanic was implemented backwards → fixed it against the wiki → retrained → that exact comp family breaks out, with no collateral damage elsewhere. Strongly supports the user's working theory for this session ("the model doesnt encapsulates the game propperly") over a training-budget explanation — this was a simulator bug the whole time, not something more training would have fixed on its own (the earlier from-scratch 6M/3M extensions, done before this bug was found, moved individual comps up and down but never broke this family out).

**The subsequent Rallying Flare AoE-effect fix (see below) was found and fixed after this retrain's eval had already been launched, so it isn't reflected in these numbers.** Given the scale of improvement already achieved and that the Rallying Flare fix is narrower in scope (one skill, two hero classes), not immediately retraining again for it alone — folding it into whatever comes next.

## AoE attack skills firing a party-wide effect once per enemy hit instead of once per use (Rallying Flare)

While continuing the audit ("the tendency is that the model doesnt encapsulates the game propperly, so lets keep finding things that are like that"), read `HEROES/Heroes/Arbalest Musketeer *.md` and `HEROES/Heroes/Occultist *.md` to cross-check the just-fixed stall mechanic (both docs independently confirm the exact edge cases the fix handles: Occultist's Vulnerability Hex is a damaging move that does NOT count toward avoiding stall, `disruptive_curse`'s real `.is_stall_invalidating false` confirms this; Jester's Solo is a non-damaging move that DOES count, and `solo` has no `.is_stall_invalidating` field at all, defaulting `True` — both real data points match the fix exactly, no further changes needed there).

Arbalest's doc describes Rallying Flare (`flare`) as dealing "a small Stress heal" alongside its real purpose (AoE Destealth + clears Stun/Mark from allies). Checked the real skill: `flare`'s outer `.target` is `~1234` (AoE, all 4 enemy ranks, no `@` — an attack against the ENEMY side, matching "destealths every enemy on hit"), but its effect list includes `"FlareHealStress N"`, whose own `.target` is `"performer_group_other"` — i.e. one of the effects nested inside an enemy-targeting AoE attack redirects to heal the whole ALLY side.

**Bug**: `_execute_action`'s attack branch loops over every AoE target (each enemy independently hit-or-missed) and calls `_apply_skill_effects` once per target inside that loop — including whatever's in `skill.effects`, with no distinction for effects that redirect to a whole different side. Verified directly: with 3 living enemies, a single Rallying Flare use produced stress-heal amounts of 0, 3, 6, 9, or 12 across 60 seeds — clearly re-rolling and re-applying `FlareHealStress`'s own chance-gated heal once per enemy independently hit, rather than the flat, single "small Stress heal" the skill actually is.

**Fixed generally, not just for this one skill**: new `CombatEngine._split_group_effect_names(skill)` partitions a skill's `.effects` into group-targeted (`"performer_group_other"`/`"performer_group"`) vs. per-target effects. All 3 multi-target branches in `_execute_action` (`is_heal`, the ally-skill branch, and the attack branch) now apply per-target effects inside their loops as before, but apply group-targeted effects exactly **once** per action afterward (gated on `any_hit` for the attack branch, unconditional for the always-guaranteed heal/ally branches) — mirroring the pre-existing `_apply_self_effects` "once per action regardless of how many targets an AoE hit" convention that already existed for self-buffs. Re-verified: the same 60-seed sweep now produces only `{0, 3, 6}` — 0/3 from a single clean `FlareHealStress` roll, and the rare 6 fully explained by a separate, legitimate, pre-existing mechanic (`player_crit_companion_stress_relief` — party-wide crit stress relief, unrelated to Rallying Flare) coincidentally stacking, not a duplication bug.

New test: `tests/engine/test_gaps.py::test_group_targeted_effect_on_aoe_attack_fires_once_not_per_target`. Full suite: 285 passed (from 284).

**Not yet retrained specifically for this fix** — it's real but narrower in scope than the stall/reinforcement fix (only affects Arbalest/Musketeer's Rallying Flare in the base game, no other skill was found combining an AoE-vs-one-side attack with a group-redirect effect on the other side). Folded into whatever the next scheduled retrain covers rather than warranting a dedicated one on its own.

## The stall/reinforcement mechanic had its core condition backwards — a real, high-impact bug directly explaining the "Stun Control: Blight" regression

Triggered by the user, after the post-heal-fix retrain's eval showed "Stun Control: Beast" finally breaking out of its session-long 0% rut but "Stun Control: Blight" getting *worse* (mean_combats_reached dropping from 3.6-4.7 to 1.4-1.8): "yes, lets keep digging, the tendency is that the model doesnt encapsulates the game propperly, so lets keep finding things that are like that." Rather than accepting the Blight regression as from-scratch training noise, replayed "Stun Control: Blight" (crusader/abomination/2×plague_doctor, Crypts tier 1) 5 times with the retrained model and dumped the full combat log.

**Found: 1-4 reinforcement waves spawning within a single 8-combat episode**, correlating with 3 of 5 replays hitting `truncated=True` at the internal per-combat step cap instead of resolving normally (`combats_reached` as low as 1-2 out of a planned 8). Reinforcements are meant to be a rare punishment for deliberate, excessive stalling — this rate is wildly higher than intended.

**Root cause: `CombatEngine._check_stall`'s condition for what counts as "stalling" was completely wrong**, not just under-implemented. The code was:
```python
alive_enemies = sum(1 for e in self.enemies if e.is_alive())
if alive_enemies <= self.rules.stall_enemy_size_threshold:
    self.stall_counter += 1
else:
    self.stall_counter = max(0, self.stall_counter - 1)
```
i.e., purely "are there few enemies left" — with no reference at all to what the PARTY actually did that round. The real mechanic (wiki-confirmed, and independently spelled out in `HEROES/Heroes/Plague Doctor *.md`'s own "How Stalling Works" section, found while auditing that class for the heal-effect-list bug earlier this session) is about the party's *behavior*, not raw enemy count: "If you use 2 or more damage dealing moves (stall-moves) per round, reinforcements don't come," "Having only 1–2 heroes alive prevents reinforcements from coming," and "Having three Size 1 enemies or any Size 2 enemy alive will also prevent reinforcements." `rules.json` has the fields to prove this is real, not a guess: `stall_number_of_invalidating_skills_threshold: 2` and `stall_party_size_threshold: 3` sit right next to the 3 fields (`stall_enemy_size_threshold`, `stall_round_stress_effect_threshold`, `stall_round_summon_effect_threshold`) that WERE already loaded — both were simply never added to `RulesLoader.KEY_MAP`/`CombatRules`, so the code had no way to consult them even if someone had wanted to. `SkillLevelData.is_stall_invalidating` (default `True`; heals/buffs/utility skills like Blinding Gas, Battlefield Medicine, Absolution, Inspiring Cry explicitly set it `False`) was *also* already parsed and sitting unused for exactly this purpose the whole time.

**Why this disproportionately hurt "Stun Control"-style comps specifically, and explains the Blight regression directly**: a slow-grind Stun/Blight comp's *entire intended strategy* is low direct damage output per round, relying on stuns (mostly `is_stall_invalidating=False`) and passive Blight ticks (not an "action" at all) to whittle enemies down over many rounds — precisely the play pattern the old, wrong condition punished hardest, since it triggered purely from "few enemies left," which a slow grind achieves and holds for many consecutive rounds by design. A burst-damage comp that kills fast never lingers at "1-2 enemies left" for more than a round or two, so it rarely triggered the bug — matching the exact comp-family-specific pattern observed all session (Stun Control comps disproportionately stuck/regressing while burst comps like Dragon Blood/All Good Hellion improved from the same session's other fixes).

**Fixed**: `CombatRules` gained `stall_party_size_threshold: int = 3`/`stall_number_of_invalidating_skills_threshold: int = 2`; `RulesLoader.KEY_MAP` now loads both from the real `rules.json` keys. New `CombatEngine._stall_invalidating_actions_this_round` counter, incremented in `_track_skill_use` whenever `performer.is_hero and skill.is_stall_invalidating` (reset to 0 at the end of `_check_stall`, mirroring how every other per-round tracker in this engine resets). `_check_stall` now computes `is_stalling = alive_heroes >= stall_party_size_threshold and alive_enemies <= stall_enemy_size_threshold and stall_invalidating_actions_this_round < stall_number_of_invalidating_skills_threshold`, only incrementing `stall_counter` when all three hold.

**Follow-up, same investigation**: initially left `.accelerate_stall_penalty`/two more `rules.json` fields (`stall_round_accelerated_only_increment: 1`/`_consecutive_increment: 5`) unimplemented — checking `.accelerate_stall_penalty` against real monster files showed 56 flagged `True`, not cleanly matching Plague Doctor's doc's named list (e.g. `carrion_eater` is flagged `True` but isn't on that list), and the exact arithmetic those two rate fields encode wasn't obvious. Rather than guess, used `WebSearch`/`WebFetch` against `darkestdungeon.wiki.gg/wiki/Stalling` directly (this is a factual, mechanics-verification query about a game system, exactly the kind of thing worth checking against an authoritative source rather than reverse-engineering blind). Confirmed exactly:
- A round only counts as a stall if `alive_enemies <= 2` **and** the party landed 0-1 "active" (damage) skills that round — matches what was already implemented.
- Using 2+ real actions in a round **resets** the stall timer outright (not a one-step decay, which the code originally did) — fixed: `_check_stall` now does `self.stall_counter = self.stall_counter + 1 if is_stalling else 0`.
- **Accelerated stalling**: when every living enemy has `.accelerate_stall_penalty` (wiki's list: Bone Arbalist/Courtier/Rabble/Soldier, Carrion Eater, Collected Man-at-Arms, Collected Vestal, Crone, Fungal Artillery, Madman, Maggot, Pelagic Guardian/Shaman, Swine Drummer/Skiver, Webber — a superset of, and fully consistent with, Plague Doctor's shorter community-observed list, resolving the earlier "doesn't cleanly match" concern), reinforcements arrive after 2 stall rounds instead of 4, with **no** stress penalty at all. Verified directly against 12 real monster ids across both docs' lists (accounting for internal-id differences, e.g. "Bone Arbalist"→`skeleton_arbalist`, "Webber"→`spider_webber`) — 12/12 hits, `accelerate_stall_penalty=True` every time.
- **`.disable_stall_penalty`** (a sibling field on the same `battle_modifier:` record, found while implementing the above, not from the wiki page): ~119 monsters, mostly boss/Ancestor forms plus some regular enemies (`brigand_blood` at every tier) — any living enemy with this exempts the fight from stalling entirely. Not from the wiki's Stalling page specifically, but unambiguous from the field name and the pattern of which monsters have it (bosses where extra reinforcements make no narrative/mechanical sense).
- Still not implemented: exactly what `stall_round_accelerated_only_increment`/`_consecutive_increment` and `stall_round_reset_threshold`/`stall_extra_round_size_threshold`/`_amount` numerically do — the wiki confirms the *outcome* (threshold 2, no stress) but not the increment arithmetic those fields presumably drive, so a new `ACCELERATED_STALL_SUMMON_THRESHOLD = 2` simulator-convention constant is used directly instead of deriving 2 from the raw fields.

**Fixed (final)**: `EnemyDefinition` gained `accelerate_stall_penalty`/`disable_stall_penalty: bool`, parsed from the `battle_modifier:` record in `HeroLoader._load_enemies` (previously entirely unparsed — the darkest parser could already handle the field syntactically, from an earlier, unrelated boolean-capitalization fix, but nothing ever looked for it). `_check_stall` now checks `disable_stall_penalty` first (any living enemy → reset counter, return), then computes the normal `is_stalling` condition, then checks `accelerate_stall_penalty` (all living enemies → compare against `ACCELERATED_STALL_SUMMON_THRESHOLD` instead of `stall_round_summon_effect_threshold`, skip the stress block entirely).

New tests: `tests/engine/test_reinforcements.py::test_two_real_actions_per_round_prevent_stall_accumulation`, `test_nearly_wiped_party_does_not_accrue_stall`, `test_zero_real_actions_still_accrues_stall_as_before`, `test_accelerated_stall_enemy_summons_reinforcements_twice_as_fast`, `test_accelerated_stall_skips_the_stress_penalty`, `test_disable_stall_penalty_enemy_exempts_the_fight_entirely`. Full suite: 284 passed (from 278).

**Retrain relaunched to include this follow-up.** The first stall-fix retrain was stopped after ~290k/3M steps (cheap to redo) once this follow-up landed, so the full fix (base condition + accelerated + disabled) trains as one unit rather than needing yet another retrain immediately after.

## Continued bug hunt: group-targeting bug + Flagellant's Redeem/Reclaim/death mechanics all found inert

Direct continuation of the same session, per the user: "lets try to locate more possible bugs on the system, until now, most of those cases happened because of bugs in the model." Continued the `HEROES/Heroes/*.md`-doc-vs-engine audit method for Plague Doctor (which surfaced the heal-effect-list bug above), then Crusader (confirmed `inspiring_cry`'s real behavior matches the fix exactly: "Provides small HP heal + decent Stress Heal"), then Flagellant (the third class common to all 3 stuck "Stun Control" comps) — which surfaced three more real, confirmed, previously-invisible gaps.

**1. `.heal_percent`/`.dotHpHeal` were never parsed, so Redeem and Reclaim healed for nothing.** Flagellant's doc calls Redeem "arguably Flagellant's strongest skill, and a staple in most builds" (heals both an ally and himself for ~50% max HP) and describes Reclaim as "Regeneration Healing." Checked the real data: `redeem`'s effects are `"Flag Super Heal N"` (ally) + `"Flag HealSelf N"` (self), both using `.heal_percent 0.43` etc. — a field `EffectDefinition` never had at all. `reclaim`'s heal is `"Flag Healdot N"`, using `.dotHpHeal 2 .duration 2` — a heal-over-time field, also never parsed. Both skills' OTHER effect (a self-debuff/self-Bleed recoil) applied fine, so nothing looked broken — Redeem and Reclaim just silently healed for exactly 0 every single use, for the whole project's history. Confirmed narrow real-data scope: `grep -rl "heal_percent"`/`"dotHpHeal"` across the entire game install returns only Flagellant's own effects file and Color of Madness DLC content (2 files each) — no base-game hero uses either field.

Fixed: `EffectDefinition` gained `heal_percent: Optional[float]`, `heal_dot_amount`/`heal_dot_rounds: Optional[int]`; `data/effect_loader.py` parses `.heal_percent`/`.dotHpHeal` (mirroring the existing `.duration`-fallback convention `dotBleed`/`dotPoison`/`dotStress` already use). `engine/combat_engine.py`'s `_resolve_effect` (renamed `_apply_effect_to_target` internally, see below) now applies `eff.heal_min`/`heal_percent` via `_apply_heal` (this ALSO fixes "Flag Last Gift", Flagellant's on-death party heal — see item 3 — which uses a flat `.heal 10` that was equally unapplied, since `_resolve_effect` had NO heal-handling of any kind before this pass, not even for the pre-existing `heal_min`/`heal_max` fields), and inflicts `eff.heal_dot_amount` as a new `CombatantState.heal_dots` DoT-like list, ticked via new `_tick_heal_dots` (mirrors `_tick_dots`/`_tick_stress_dots` exactly, but calls `_apply_heal` instead of mutating `.hp` directly, so max-HP clamping and Death's-Door recovery fire correctly).

**2. `_get_effect_target("performer_group_other")` only ever returned ONE ally — a second, independently severe bug found while fixing #1.** While tracing why Flagellant's real `flagellant_death_door_enter` effect (`.target performer_group_other`, meant to heal every OTHER party member 10% max HP) wasn't reaching more than one hero in a test scenario, found that `_get_effect_target`'s `"performer_group_other"` branch was `return alive[0] if alive else performer` — the FIRST living ally, full stop, never all of them. This is not Flagellant-specific: the same target spec is used by **`beast_stress_party`** and **`human_stress_heal_party_1-5`** — Abomination's Transform's own party-wide stress cost (+8 human→beast) and stress relief (−2 beast→human) — meaning Transform's party-wide stress mechanic, one of the very mechanics this session's earlier Abomination-fidelity pass already worked on and retrained against, has *also* been silently under-applying to just 1 of typically 3 other party members this entire time, missed by that earlier pass. Also affects Arbalest's Rallying Flare (`"FlareHealStress N"`/`"FlareBuff N"`, explicitly named in Flagellant's own doc as an Endure-recoil counter: "Use Arbalest/Musketeer's Rallying Flare/Skeetshot to offset recoil").

Fixed via a structural change: `_resolve_effect` is now a thin dispatcher — for `eff.target in ("performer_group_other", "performer_group")` it loops over every living member of the resolved side (excluding the performer for the `_other` variant) and calls a new `_apply_effect_to_target` (the old `_resolve_effect` body, renamed and given an `eff_target` parameter instead of re-deriving it) once per member, each with its own independent `chance` roll — matching how every other multi-target case in this engine already resolves (AoE skills roll hit/effects once per target). Single-target specs still resolve via `_get_effect_target` exactly as before, called once. Also fixed a redundant, pre-existing re-resolution inside the `is_stat_buff` block (`apply_target = self._get_effect_target(...)` — always the same value as the already-resolved `eff_target`, harmless while `_get_effect_target` was itself single-target-only, but would have silently diverged from the new group loop) to just reuse `eff_target` directly. `_get_effect_target`'s now-dead, misleadingly-single-valued `"performer_group_other"` branch was removed rather than left as confusing dead code.

**3. Two entirely unwired Flagellant-specific trigger systems** (found reading the rest of his `.md` doc, which describes both in detail): a real `.enter_effects`/`.enter_effect_round_cooldown` pair on the `deaths_door:` record (fires once on *entering* Death's Door — Flagellant's doc: "On reaching 'Death's Door': All other heroes are Healed for 10% of MAX HP... Does not suffer Mortality Debuffs"), and an entirely separate `death_reaction:` record type (fires once on actually *dying* — his doc: "On 'Death': 1. All enemies are Stunned (125% Stun Chance) 2. All other heroes are Healed for 10 HP"). Neither was parsed by `HeroLoader` (the first is a sub-field on an already-partially-read record; the second is a whole record type nobody ever looked for) nor consulted anywhere in `CombatEngine`. Verified both are Flagellant-only in the real game via `grep -rl "enter_effects"`/`"death_reaction"` across every hero file.

Fixed: `HeroDefinition` gained `death_door_enter_effects: list[str]`, `death_door_enter_round_cooldown: int`, `death_reactions: list[DeathReaction]` (new type: `target_allies: bool`, `target_enemies: bool`, `effects: list[str]`); `HeroLoader._build_hero` parses both. `CombatEngine` gained `_maybe_trigger_death_door_enter` (called from both places a hero's `is_deaths_door` first flips to `True` — `_handle_deaths_door` and `_trigger_heart_attack` — gated by a new `CombatantState.last_death_door_enter_round` tracker checked against the real cooldown) and `_maybe_trigger_death_reactions` (called from both places a hero's `is_dead` gets set to `True` from a hero-death path — the deathblow branch of `_handle_deaths_door` and the "already at Death's Door" branch of `_trigger_heart_attack`). The death-reaction's group semantics live on the *record* (`target_allies`/`target_enemies`), not the effect (`"Flag Last Gift"`/`"Flag Last Insult"` both use the single-target `.target "target"` spec) — `_maybe_trigger_death_reactions` resolves the whole side itself and calls `_resolve_effect` once per living member, distinct from (and not reusing) the `performer_group_other` group-dispatch fixed in item 2.

**Related but explicitly NOT fixed this pass**: `flagellant_low_hp_reaction` (`.buff_ids flag_hpbelow_CRIT_buff flag_hpbelow_DMGL_buff flag_hpbelow_DMGH_buff`, Flagellant's real "below 40% HP: +20% DMG/+7% CRIT (3 rounds)" self-buff, per his doc's "'Below 40% HP' Buffs" section). Unlike the two triggers above, this effect record carries no HP-ratio condition of its own — whatever gates the 40% check must live in a different, not-yet-identified place (possibly a periodic per-round check the engine has no equivalent of yet, akin to a monster brain's `hp_ratio_threshold` bonus-initiative trigger, but there is no hero-side analog implemented). Flagged rather than guessed at; a real gap, needs its own follow-up investigation into exactly how/where the real game gates this.

Tests: `tests/engine/test_flagellant.py` (new file) — `test_transform_party_stress_applies_to_all_other_allies_not_just_one` (4-hero party, verifies all 3 non-Abomination heroes gain +8 stress, not just 1), `test_redeem_heals_ally_and_self_via_heal_percent`, `test_reclaim_heals_over_time_via_dot_hp_heal`, `test_flagellant_death_door_enter_heals_the_rest_of_the_party`, `test_flagellant_death_door_enter_respects_round_cooldown`, `test_flagellant_death_reaction_stuns_enemies_and_heals_allies`. Full suite: 278 passed (from 272 after the heal-effect-list fix, 267 before this session's Abomination work).

**Retrained from scratch (3M timesteps, ~2h02m — slower than the first 3M leg's ~56min, machine load not a mechanics issue), combining every fix from this pass and the heal-effect-list fix before it.** Training-time metrics hit the best of the whole session: `ep_len_mean` 227 (vs 210-211 post-Abomination-fix, 184-201 before that), `ep_rew_mean` -58 (vs -69 to -73). Re-ran the identical 12-comp/seed-1 sweep:

| Comp | Zero-death short (post-Abom-fix→post-heal-fix) | medium | long |
|---|---|---|---|
| **Stun Control: Beast** | **0-2%→16%** | **0%→10%** | **0%→6%** |
| Stun Control: Blood | 0%→0% | 0%→0% | 0%→0% (but mean stress 197-200→159-173, meaningfully healthier despite still not clearing) |
| Stun Control: Blight | 0-4%→0% | 0%→0% | 0%→0% (mean_combats_reached dropped to 1.4-1.8, from 3.6-4.7 — wiping *faster*, a genuine regression for this one specific comp) |
| Riposte Style (Hag) | 52%→54% | 50%→46% | 32%→28% (flat, still the strongest comp) |
| Holy Hymn: Riposte | 38%→30% | 32%→20% | 0%→14% |
| All Good Hellion / Dragon Blood / Shadowed Purse / Dark Ritual: Ballad / Marked Prey (both) | mixed, mostly down 10-20pp each | | |

**Read: real, direct validation that this pass's fixes mattered, alongside expected from-scratch training noise.** "Stun Control: Beast" — a double-Abomination stun-lock comp that was stuck at 0% (occasionally 2%) across every single eval in this entire session, through the reward realignment, the CSV→JSON redesign, action masking, and the full 4-part Abomination fidelity pass — broke out to a real, non-trivial 16/10/6% the very first retrain after fixing Transform's party-stress group-targeting bug and Crusader's Inspiring Cry. That's the most direct causal evidence available this session that a specific fix closed a specific gap, not just aggregate noise. "Stun Control: Blood" didn't clear zero-death but its stress dropped by ~30-40 points across all three bands, consistent with the same underlying improvement not yet being enough to fully close that harder comp. Everything else moving up or down by 10-20pp in both directions (several previously-strong comps dipped: All Good Hellion, Dragon Blood, Marked Prey) is the expected shape of a **from-scratch** retrain (not a `--resume`) re-discovering per-comp strategy from zero under a budget shared across ~132 comps — direct comp-to-comp comparisons across two independently-trained-from-scratch models carry more inherent noise than a resume/fine-tune would. The one comp that got concretely worse in a way that doesn't fit that noise story is "Stun Control: Blight" (mean_combats_reached dropping from 3.6-4.7 to 1.4-1.8, i.e. wiping faster) — flagged as worth a closer look if the user wants to keep chasing this specific comp family, but not chased further this pass. The pre-this-fix checkpoint (post-Abomination-fix, 6M steps) is preserved as `dd_combat_ppo_full_expedition_prehealfix_6M_backup.zip`; `dd_combat_ppo_full_expedition.zip` is now this new 3M from-scratch retrain.

## Heal skills silently dropping their own `.effect` list — likely the biggest bug found this session

Triggered by the user, after the 6M-extension eval showed uniform extra training was a wash on the "Stun Control" comps (and even regressed two others): "lets try to locate more possible bugs on the system, until now, most of those cases happened because of bugs in the model." Continued the same audit method that found the Abomination gaps — reading `HEROES/Heroes/*.md` docs against the engine/data — this time for Plague Doctor (present in all 3 stuck "Stun Control" comps) and, from there, the general heal-skill code path.

**The bug**: `_execute_action`'s `is_heal` branch (`skill_data.heal_min is not None`) calls `_resolve_heal`, which only ever applies the skill's bare `.heal N M` amount — it never calls `_apply_skill_effects`, unlike every other branch (`_resolve_self_action`, the ally-skill branch, the attack branch, and every monster-skill call site). Any hero skill that has **both** a bare `.heal` field **and** a named `.effect` list silently dropped the effects entirely, with no error or log gap to notice — the same invisible-failure shape as the historic `dmg_min`/`dmg_max` bug and the ally-buff-damage bug, just on the healing side this time.

**Confirmed via a project-wide grep** (`grep -rn '\.heal [0-9].*\.effect '` across every `heroes/*/*.info.darkest`) that exactly 5 skills project-wide have this shape — this is the complete list, not a sample:

| Skill (hero) | Bare `.heal` | Real value actually in the dropped `.effect` |
|---|---|---|
| `inspiring_cry` (Crusader) | 1-2 HP (token) | **`"Crusader HealStress N"`** — the real stress-heal (8 stress at level 5) — **and** `"Crusader Light N"` (torch gain, itself out of scope — see Torchlight section) |
| `battlefield_medicine` (Plague Doctor) | 1-3 HP (token, doc calls it "tiny direct Healing") | `"Cure"`/`"Cureself"` — clears Bleed/Blight on both the performer and the target, the skill's actual documented purpose |
| `fortifying_vapours` (Antiquarian) | 1-3 HP | `"Fortify Resists N"` — a real resist buff |
| `battlefield_bandage` (Arbalest) | 2-5 HP | `"Arb Stacking Heal N"` — a real stacking self-buff |
| `wyrd_reconstruction` (Occultist) | 0-22 HP (the real "risky heal," can heal for 0) | `"Wyrd Bleed N"` — the real chance to bleed the target as the risk side of the risky heal |

**Crusader's `inspiring_cry` is almost certainly the highest-impact instance.** It's the party's primary stress-relief tool in most comps that carry a Crusader (`HEROES/Heroes/Plague Doctor *.md` itself says, of an unrelated comp: "Crusader as main healer via Battle Heal + Inspiring Cry"; "Crusader's Inspiring Cry is sufficient Stress Healing" appears in another sample comp writeup) — and Crusader appears in all 3 "Stun Control" comps plus several others in the standing 12-comp eval sweep. Before this fix, every use of Inspiring Cry in training/eval gave a trivial 1-2 HP heal and **relieved no stress whatsoever** — directly explaining the earlier full-expedition replay's own observation (see the Abomination-fixes section above): `"Crusader heals Crusader for 4"` spamming for dozens of lines while stress climbed to repeated Affliction/heart-attacks anyway, because the actual stress-heal component never fired. This calls into question whether the "Stun Control" comps' stuck-at-0% result (and the training-coverage explanation given for it) was really about strategy-learning difficulty at all, versus this bug quietly capping how much stress ANY comp with a Crusader-as-healer could ever recover — a hypothesis the next retrain should help settle.

**Existing test only ever checked the data layer, not the engine wiring** — `test_stress_heal_effect_resolves` (added in an earlier session) already confirmed `store.effects["Crusader HealStress 5"].stress_heal == 8` was parsed correctly from the real `.healstress` key. That test passed the whole time; nothing ever asserted that using Inspiring Cry in an actual `CombatEngine` reduced a target's stress. Same blind spot pattern as the historic weapon-damage bug (`tests/engine/test_resolution.py` bypassed `HeroLoader` entirely) — a correct unit test at the data layer gave false confidence that the mechanic worked end-to-end.

**Fixed**: the `is_heal` branch now mirrors the ally-skill branch immediately below it — `_apply_skill_effects(target, performer, skill_data, hit=True)` and `_apply_movement(performer, target, skill_data)` per target, plus one `_apply_self_effects(performer, skill_data)` call. Verified safe: none of the 5 affected skills have a `.move` field (checked directly), so `_apply_movement` is a no-op for all of them; `_apply_skill_effects` is side-effect-free for effects a skill doesn't carry.

New test: `tests/engine/test_gaps.py::test_heal_skills_apply_their_own_effect_list` — exercises both `inspiring_cry` (asserts a stressed ally's stress actually drops) and `battlefield_medicine` (asserts an inflicted Bleed is actually cleared) through the real `CombatEngine._execute_action` path, not just data assertions. Full suite: 272 passed (from 271).

**Not yet retrained.** Given the scale of this bug (the primary stress-relief skill for the most commonly-used support hero silently doing almost nothing), a full retrain is warranted before drawing further conclusions about the "Stun Control" comps or any other eval result gathered before this fix — every prior eval number in this file involving a Crusader-as-healer comp should be treated as measured under a materially broken stress-recovery mechanic.

## Abomination fidelity audit — 4 real gaps found and fixed, all previously silent

Triggered directly by the user: after seeing the post-CSV→JSON-redesign retrain's eval results (Riposte Style (Hag) hit 58/38/18% zero-death, the best of the whole session, but the three "Stun Control" comps — all Abomination-heavy — stayed pinned near 0%), the user asked to read the `HEROES/Heroes/*.md` docs (community-written per-hero guides, Notion exports, same folder as `COMPS/Comps/`) to check whether Abomination specifically was being simulated faithfully, flagging directly: "he has all 7 skills active at all times ... this is a new bug that makes abom not work properly." Reading `HEROES/Heroes/Abomination *.md` against the current engine/data code surfaced four confirmed gaps, all Abomination-specific (verified via `grep -rl` across the whole game install — none of these fields/flags appear for any other hero):

1. **Skill-restriction bug, exactly matching the user's report.** The doc states plainly: "Unlike other heroes who can only equip 4 combat skills, Abomination always has all 7 combat skills equipped" — there's no in-game 4-skill loadout choice for him at all, unlike every other class. But `store.comp_loadouts` (the scraped `COMPS/Comps/<name>/<name>.json`, wired into `ExpeditionEnv`/`eval_expedition.py` skill-restriction resolution — see CLAUDE.md "Real per-comp skill loadouts") still lists exactly 4 `activeSkills` for him per comp, same shape as any other hero — the source scraping tool has no Abomination carve-out. Concretely checked "Stun Control: Beast"'s JSON (2 Abominations): the first lists `transform/manacles/absolution/rage` (missing `vomit`/`rake`/`slam`), the second lists `transform/manacles/vomit/absolution` (missing `rage`/`rake`/`slam` — **all three** of his beast-form attacks). Applied literally as a skill restriction, the second Abomination would have been completely unable to deal damage while transformed into Beast mode — a severe, concrete capability loss for exactly the comp family that stayed stuck at 0%. Fixed in `ExpeditionEnv._resolve_skill_ids`/`eval_expedition.py`'s `resolve_skill_ids`: any hero with `hero_id == "abomination"` always resolves to an unrestricted `[]` entry (no engine change needed — `CombatEngine.reset()`'s existing `if ids:` check already treats an empty per-hero list as "use the full movepool," the same convention used when a comp has no loadout at all).
2. **Transform's free-action flag was inert.** The real file (`heroes/abomination/abomination.info.darkest`) has `.is_continue_turn true .per_turn_limit 1 .per_battle_limit 2` on the `transform` skill — `HeroLoader` already parsed `is_continue_turn` into `SkillLevelData`, but nothing in `engine/combat_engine.py` ever read it. The doc independently confirms this is real, not a guess: "FREE ACTION - does not end your turn ... FREE ACTION means you can Transform AND attack in the same turn." Every Transform use was silently costing the hero's entire turn — a severe fidelity gap for a mechanic the game explicitly designs around, and a very plausible direct cause of the "Stun Control" family's poor performance (all 3 are Abomination-heavy comps built around transforming into Beast form for burst damage; without the free action, the policy pays a full wasted turn every time it wants to combo transform-then-attack, which a real player never would). Fixed: `CombatEngine.step()`'s `is_free_action` check (previously provision-only) now also covers `skill_data.is_continue_turn`, looked up via the same `get_skill_data` call `_execute_action` already makes. Verified scoped correctly: `grep -rl "is_continue_turn true" heroes/ monsters/` across the whole install returns exactly one file, Abomination's own.
3. **`get_valid_actions` didn't filter by `per_battle_limit`/`per_turn_limit` at all** — only execution-time `_check_skill_limits` did. A maxed-out Transform (already used twice this battle) was still offered as a legal action every turn thereafter, silently rejected on selection (wasting the whole turn if picked) — the identical bug class as the mode-filter gap fixed in an earlier session (`_is_valid_mode`/`_check_valid_mode`), just never extended to skill-use limits at the time. Fixed the same way: extracted a side-effect-free `_has_skill_uses_remaining`, shared by `get_valid_actions`' new filter check and `_check_skill_limits` (which keeps its log line for the execution-time case, though that path should now rarely trigger in practice since the action won't be offered).
4. **Affliction-triggered forced mode revert was never implemented.** Real data: Abomination's `beast` mode record (only his, confirmed via `grep -rl "affliction_combat_skill_id" heroes/`) sets `.affliction_combat_skill_id transform .stress_damage_per_turn 6` — becoming Afflicted while in Beast form auto-transforms him back to Human for free, NOT counted against his 2-per-battle Transform limit (a status-triggered event, not a player action). Without this, an Abomination who'd already spent both Transform charges and then got Afflicted while in Beast form would be stuck there — Rake/Rage/Slam only, no Manacles/Beast's Bile/Absolution — for the remainder of that fight, which the real game never allows. Fixed via new `_maybe_force_mode_revert(hero)`, called from `_apply_affliction_effects`: looks up the hero's `HeroDefinition.modes` entry matching their current mode, and if it has `affliction_combat_skill_id` set, resolves that skill's `human_effects`/`beast_effects` list (the exact same mechanism `_resolve_self_action` uses for a normal player-chosen Transform) without calling `_track_skill_use`. The mode record's other auto-revert trigger, `battle_complete_combat_skill_id` (also `"transform"`, fires when a combat ends while in Beast form), is deliberately left unimplemented — `CombatEngine.reset()` already starts every new combat at a fresh default mode regardless of how the previous one ended, so there is no observable behavior left for this field to change; implementing it would be dead code.

**One pre-existing test needed correcting, not just extending**: `tests/engine/test_transform.py::test_transform_switches_mode_both_directions` used to call `step()` twice in immediate succession and expect the second Transform to flip the mode back — this only worked because the *old, buggy* non-free-action behavior advanced `current_turn_index` through the rest of the turn order after the first Transform, incidentally landing on a new round (and therefore a fresh `per_turn_limit` key) before the second call. With Transform correctly *not* ending the turn anymore, a same-round second use is now properly blocked by the real `per_turn_limit=1` (exactly as `test_transform_respects_per_turn_limit` already documented) — the test now explicitly advances `engine.round` between the two calls, modeling the real constraint that a given hero only gets one turn per round.

New tests: `tests/env/test_expedition_env.py::test_abomination_is_never_skill_restricted`; `tests/engine/test_transform.py::test_transform_is_free_action_does_not_advance_turn`, `test_transform_not_offered_once_per_battle_limit_exhausted`, `test_affliction_in_beast_mode_forces_revert_to_human`. Full suite: 271 passed (from 267).

**Retrained from scratch (3M timesteps, ~56 min) and re-ran the identical 12-comp/seed-1 eval sweep** to see whether this closes the "Stun Control" gap:

| Comp | Zero-death short (before→after) | medium | long |
|---|---|---|---|
| All Good Hellion (1 Abomination) | 14%→**42%** | 14%→**30%** | 2%→**18%** |
| Dragon Blood (1 Abomination) | 28%→**46%** | 12%→**32%** | 6%→**24%** |
| Dark Ritual: Ballad (1 Abomination) | 2%→10% | 8%→8% | 0%→0% |
| Stun Control: Blight (1 Abomination) | 2%→4% | 2%→0% | 0%→0% |
| Stun Control: Beast (2 Abominations) | 0%→0% | 0%→0% | 0%→0% |
| Stun Control: Blood (1 Abomination) | 0%→0% | 0%→0% | 0%→0% |
| *(6 Abomination-free comps)* | mixed ±4-12pp | mixed | mixed | 

Comps with a single, conventionally-played Abomination (All Good Hellion, Dragon Blood) improved dramatically — zero-death rate roughly doubled to tripled across all three length bands, clearly attributable to the fixes since nothing else changed about these comps between runs. The 6 Abomination-free comps in the sweep moved within ±4-12 percentage points in both directions, consistent with ordinary run-to-run training variance (expected — these comps' heroes were untouched by the fix). But all three "Stun Control" comps (the ones the user's original report was about) stayed pinned at or near 0% regardless.

**Investigated directly rather than assuming a training-budget answer** (per the user's own stated preference to investigate before extending training): wrote a throwaway script replaying "Stun Control: Beast" (crusader/abomination/abomination/plague_doctor, Cove tier 1) 5 times with the retrained model and dumped the full per-step combat log. Findings:
- **The fixed mechanics are demonstrably in active use, not still broken**: `transforms into beast/human mode` appears 62 times across the 5 replays, enemy `stunned!` 71 times, `blight` 141 times — the policy is genuinely attempting the stun-lock/blight strategy this comp is built around, not failing to engage with Abomination at all.
- **Every one of the 5 replays ends in a full party wipe**, within the first 1-4 combats (`combats_reached` 1-4) — Plague Doctor dies first (or near-first) in 4 of 5 replays, then the rest of the party crumbles within a few more combats.
- Formation is correct (Crusader front, both Abominations mid, Plague Doctor safely backline — matches the real JSON order), so this isn't a repeat of the earlier formation-order bug.

**Conclusion: this reads as a genuine training-coverage gap, not a remaining simulator bug.** "Stun Control: Beast/Blood/Blight" all depend on a harder, more coordinated multi-turn tactic (double-Abomination or Abomination+2×Plague-Doctor stun-lock-and-blight, requiring the policy to consistently prioritize control skills over raw damage across several rounds) than the more generic "attack the softest target" style that carries All Good Hellion/Dragon Blood to their big gains. With training budget shared across ~132 comps × 5 tiers × 3 length bands sampled randomly each reset, a comp needing a qualitatively different, harder-to-discover strategy is expected to lag behind simpler comps at the same total timestep budget — this is the same "needs more budget, not a design flaw" pattern already seen once before in this file (see the original 3M/6M full-expedition plateau, before the reward realignment).

**User asked to extend training regardless — resumed 3M→6M (`--resume --timesteps 3000000`, ~2h19m for this leg, slower fps (360-486 vs 884-570 on the first leg, machine load not a training-mechanics issue) — and re-ran the identical 12-comp/seed-1 sweep:**

| Comp | Zero-death short (3M→6M) | medium | long |
|---|---|---|---|
| Riposte Style (Hag) | 50%→**52%** | 44%→**50%** | 20%→**32%** |
| Marked Prey: Snipe | 2%→**12%** | 0%→**6%** | 2%→2% |
| Dragon Blood | **46%→26%** | **32%→20%** | **24%→8%** |
| All Good Hellion | 42%→32% | 30%→22% | 18%→16% |
| Stun Control: Blight | 4%→**0%** | 0%→0% | 0%→0% |
| Stun Control: Beast | 0%→2% | 0%→0% | 0%→0% |
| Stun Control: Blood | 0%→0% | 0%→0% | 0%→0% |

**Honest read: uniformly extending training to 6M is a wash at best for the comps this was meant to help, and actively regressed two others.** Riposte Style (Hag) and Marked Prey: Snipe kept improving, but Dragon Blood dropped substantially (46%→26% short, 24%→8% long) and All Good Hellion slipped slightly too — both were the two comps that gained the most from the Abomination fixes one retrain ago. The three "Stun Control" comps this whole extension was meant to target are unchanged within noise (Blight arguably got worse, going from a marginal 4% to a flat 0%). This is consistent with what a single shared policy trained on ~132 randomly-sampled comps with no curriculum or per-task balancing looks like: it can keep improving on some subtasks while drifting on others, with no guarantee that raw additional uniform-random timesteps preferentially help the specific hard comps a user cares about. **Blindly extending training further is not recommended as the next move** — a targeted approach (oversampling the "Stun Control" family specifically, or a dedicated curriculum/fine-tuning pass) would be needed to make further progress on them, which is a design decision for the user rather than something to do unprompted. Current model (`dd_combat_ppo_full_expedition.zip`, 6M steps) backed up as `dd_combat_ppo_full_expedition_postabomfix_6M_backup.zip`; the pre-extension 3M version is `dd_combat_ppo_full_expedition_postabomfix_3M_backup.zip`.

## Full-expedition simulation implemented — provisions, reinforcements, Move, on-crit passives, 3-tier Death's Door, heart attack, multi-combat sequencing

Triggered by the user wanting to evaluate a trained model across full expeditions (not just isolated combats), with the real objectives being expedition-level: 1) never lose a party member, 2) minimize final aggregate party stress. Implemented in 11 staged milestones (data layer → engine mechanics → Gym sequencing → reward → retrain), each with its own test file, 244 tests total (from 171 at the start of this work). Full design doc: `C:\Users\Francisco\.claude\plans\golden-waddling-crown.md` (plan approved before implementation).

**New real data sources found and wired in** (none previously read by this project):
- `combat_move_skill:` record (per-hero `.move <backward> <forward>`) — genuinely varies by class, not universal: Arbalest/Musketeer (2,0) — can't move forward at all; Jester (3,3); Flagellant (1,3); most others (2,2). Verified against all 18 heroes, cross-checked with the wiki's "Other information → Movement" field.
- `crit: .effects "on_crit_X"` record — a per-class passive that fires on ANY crit with ANY skill, separate from a skill's own `.effect` list. All 18 catalogued (crusader/shieldbreaker→on_crit_prot +15% PROT/2rd, leper→on_crit_acc, hellion→on_crit_dmgbleeding, plague_doctor→on_crit_blightchance, occultist/vestal→on_crit_healdone, jester→on_crit_stresshealdone, arbalest/bounty_hunter/musketeer→on_crit_dmgmarked, houndmaster/flagellant→on_crit_bleedchance, grave_robber/antiquarian→on_crit_def, highwayman→on_crit_speed, man_at_arms→on_crit_stressresist, abomination→on_crit_dmg).
- `deaths_door:` record's `.recovery_buffs`/`.recovery_heart_attack_buffs` — 2 further real debuff tiers beyond the already-implemented active `.buffs` tier. Real buff amounts prove these are **alternate, sticky tiers, not additive**: mortality's stress term is +10%, heart_attack's is **-15%** (opposite sign rules out stacking). Modeled as `CombatantState.mortality_tier` ("none"→"mortality"→"heart_attack"), monotonic, never downgrades.
- `wave_spawning: .prefers_front True` — only on the ~89/254 monsters used as mid-combat reinforcements/summons (confirmed: `cultist_brawler` has it, `cultist_witch` doesn't) — used to place reinforcements on a front-most vs. any-open rank.
- `inventory/base.supply.inventory.items.darkest` + `campaign/provision/provision.json` — provision item defs, stack limits, and each hero class's real free starting item (`raid_starting_hero_class_item_lists`, e.g. crusader→+1 holy_water).
- `effects/base.effects.darkest`'s `stall_summon_A/B/C` — the real, fixed, zone-independent reinforcement (always 2x `cultist_brawler`+`cultist_witch`, tier-matched), gated by `rules.stall_round_summon_effect_threshold` (already loaded since an earlier session, never consulted anywhere until now).
- `rules.json`'s `monster_crit_companion_stress_chance/_damage` and `player_crit_companion_relief_chance/_stress_relief` — real party-wide crit stress/relief fields, loaded but unused before this (the wiki independently confirms this mechanic: "every party member will have a 25%/50% chance to also heal/gain stress").

**Heart attack mechanic** (`HEART_ATTACK_STRESS_THRESHOLD=200`, `HEART_ATTACK_RESULTING_STRESS=170`) — wiki-sourced (`darkestdungeon.wiki.gg`), no `rules.json` key exposes this at all (confirmed via exhaustive search — same "hardcoded in the binary" situation as `TIER_TO_HERO_LEVEL`/expedition-length bands). Triggers only when Afflicted and stress≥200 (a Virtuous hero at 200 loses Virtue and resets to 0 instead); HP→0/Death's Door, stress reset to 170; if already at Death's Door, instant death bypassing the deathblow-resist roll entirely.

**Two more bugs found and fixed while implementing this** (in addition to the on-crit-passive duration fallback and `buff_ids` bareword-list bugs, both documented inline in `CLAUDE.md`'s "Full-expedition simulation" section):
1. **Hero-death position gap never collapsed.** Enemies correctly leave a corpse blocking their rank (`is_corpse`), but heroes — who never leave a corpse in the real game — had no equivalent: a dead hero at rank 2 left a permanent hole, with nothing shifting the ranks behind them forward. Found while the user asked about position mirroring/gap-collapse mechanics directly. Fixed via `_collapse_hero_position_gap`, called from `_handle_deaths_door`'s hero-death branch.
2. **No pass-turn fallback existed at the engine level.** A hero with zero legal actions (boxed in, no reachable skill or Move target) would make the raw `CombatEngine` hang forever waiting for `submit_action()` — only `env/gym_env.py`'s `Discrete(64)` index-clamping trick silently synthesized a no-op action around this, with no stress cost and no log entry, meaning the raw engine (used directly by many tests) had no real handling at all. Fixed via a real `__pass__` action, offered only when `get_valid_actions` would otherwise be empty, costing `PASS_TURN_STRESS=5.0` (no `rules.json` source — matches `stall_stress`'s own +5/round convention).

**Position mirroring and enemy frontline/backline placement were already correct** — verified, not fixed. Hero rank 1 (position 0) and enemy rank 1 (position 0) already face each other symmetrically (no code asymmetry anywhere). Enemy frontline-vs-backline placement is encoded purely as "whichever order the mash file lists them in" (verified: real `hall:`/`room:` lines list melee frontliners before ranged backliners, e.g. `cultist_brawler_A cultist_brawler_A cultist_witch_A`), and `CombatEngine.reset()`'s existing sequential-by-list-order assignment is correct by construction — no separate ignored rank field exists.

**Provision starting quantities — explicit simulator convention, no real data anchor** (flagged prominently in the plan for user sign-off; proceeded with the plan's proposed numbers, no objection raised):
```
BASE_PROVISION_AMOUNTS = {"bandage": 2, "antivenom": 2, "holy_water": 1, "laudanum": 1}
LENGTH_BAND_PROVISION_MULT = {"short": 1.0, "medium": 1.5, "long": 2.0}
FOOD_AMOUNTS = {"short": 2, "medium": 3, "long": 4}
```
`campaign/provision/provision.json` only has store purchase *limits* per dungeon-length index (not recommendations) and each hero class's single free item — no "recommended loadout" data exists anywhere in the install. The formula above is capped at each item's real stack limit so it can never exceed what the real game would even allow buying. **Revisit these numbers if expedition training results look off in a way traceable to running out of (or never using) provisions.**

**Expedition length bands — also no data source, user-provided directly**: Short 8-11 combats, Medium 10-14, Long 14-18 (`EXPEDITION_LENGTH_BANDS`). Confirmed via exhaustive search that dungeon layout/room-count generation is hardcoded in the game binary, not exposed in any data file — same situation as `TIER_TO_HERO_LEVEL`.

> **Superseded by a later fix, kept for history (2026-08-08).** The claim above is
> **wrong**, and the "exhaustive search" missed the file: `scripts/map_generator.darkest`
> ships with the game in plain text and states the counts directly, as 44 `map:`
> records keyed by (size, quest_type, dungeon_type) carrying `.hallway_battle` and
> `.total_room_battles` ranges. The search plausibly missed it because the file uses
> the **multi-line block layout**, so this repo's own `DarkestParser.parse_file`
> returns 44 records with *every field silently dropped* — it looks like a file with
> no relevant content. `DarkestParser.parse_blocks` + `data/map_gen_loader.py` now
> parse it, and `EXPEDITION_LENGTH_BANDS` is derived rather than invented:
> **short (3, 8) / medium (6, 10) / long (14, 16)**. The invented bands ran roughly
> twice the real number of combats at short and medium — a simulated "short"
> expedition contained more fights than a real *medium* one, inflating attrition
> across all training and eval to date. Unlike `TIER_TO_HERO_LEVEL` (still genuinely
> binary-only), this one had a data source all along. Evidence and the measured
> before/after in `CHANGELOG.md`; pinned by `tests/data/test_map_gen.py`.

**Laudanum is currently mechanically inert** — its real effect (`.clearDotStress`) clears a "stress DoT" status that, verified, no real combat effect in the entire game ever inflicts (`EffectDefinition.stress_damage_per_round` is parsed but never populated by any real file — likely an exploration/curio-only mechanic). Implemented literally (offered as a free action, does nothing) rather than reinterpreted into a made-up substitute effect not backed by real numbers.

**Reward design**: the existing per-combat potential-based shaping and `REWARD_WIN`/`REWARD_LOSE` are reused unchanged, firing every combat within a sequence (not just the last) for dense signal across an 8-18 combat episode. Two new layers, both composed inside `ExpeditionSequenceEnv.step()`: `REWARD_HERO_DEATH=-3.0` (flat one-time terminal-style penalty on an actual death, same treatment as `REWARD_WIN`/`REWARD_LOSE`, deliberately *not* folded into any potential function — keeps Φ smooth/interpretable and avoids a large one-step Φ cliff exactly at the death step) and `Φ_expedition` (a second, independent potential-based term for aggregate living-party stress, same `SHAPING_GAMMA` — a sum of two valid potential-based terms is itself valid per Ng/Harada/Russell 1999's linearity, so the existing invariance guarantee still holds). `W_EXPEDITION_STRESS=1.0` is a starting guess, not derived — worth tuning empirically once training results are in.

**Trained `dd_combat_ppo_full_expedition.zip`, 3M timesteps** (~55 min wall clock, ~505-910 fps — faster than the v4 single-combat run's ~1388fps average would suggest per-step, but each episode now spans many combats so total combats simulated per second is comparable). Final training-eval reward 6.73±24.06 over ~179-step episodes (high variance expected — episode reward accumulates across an entire 8-18 combat sequence, and outcomes range from an early wipe to a full clean run). Old single-combat expedition model preserved as `dd_combat_ppo_expedition.zip` / backup `dd_combat_ppo_expedition_v3_backup.zip`; `ExpeditionEnv` (single combat) is unchanged and still trainable/evaluable independently of `ExpeditionSequenceEnv`.

`eval_expedition.py --full-expedition`, 50 episodes × 5 comps × 3 length bands (750 episodes):

| Comp | Short zero-death | Medium zero-death | Long zero-death | Mean combats reached (S/M/L) |
|---|---|---|---|---|
| Dark Ritual: Heist | 6% | 0% | 0% | 6.8 / 7.2 / 7.2 |
| Mark for Blood | 0% | 0% | 0% | 5.3 / 4.9 / 5.6 |
| Holy Hymn: Monk | 2% | 0% | 0% | 5.5 / 5.7 / 5.0 |
| Blight, Bite & Bleed V2.0 | 4% | 6% | 0% | 3.7 / 5.0 / 7.0 |
| Holy Hymn: Bulwark | 6% | 2% | 4% | 2.5 / 3.1 / 3.1 |

**Honest first-run read**: zero-death rate is low everywhere (0-6%) and mean final stress is high (170-200, near the heart-attack ceiling) across nearly every cell — at 3M timesteps, the policy has not learned to reliably complete a full expedition without losing at least one hero or ending badly stressed. This is a materially harder compound objective than the single-combat win-rate task the v4 model solved at the same budget (zero deaths across ~5-10 consecutive real fights is a strict, low-probability event even for a policy that's individually quite good per-combat — e.g. 95%-survival-per-combat only compounds to ~60% zero-death over 10 fights). Two things point at "needs more budget, not a design flaw": (1) mean reward is often clearly positive for the stronger comps (Dark Ritual: Heist +15 to +17, Holy Hymn: Monk +7 to +12) even with near-zero zero-death rate, meaning the shaped reward is correctly rewarding partial progress, not just the binary outcome; (2) `mean_combats_reached` cleanly tracks comp strength in the same ranking the single-combat v4 model already established (Dark Ritual: Heist strongest at ~7 combats reached, Holy Hymn: Bulwark weakest at ~2.5-3.1 — the same comp flagged as the v4 outlier for stalling/timing out). Not treating this as a bug — no timeouts occurred in any cell (0 across all 15), so episodes are ending cleanly (win-through or wipe), just not reaching the zero-death bar often yet. **Recommended next step: more training budget** (the reward trend was still climbing at the 3M mark in the training log, unlike v4's single-combat task which had comfortably plateaued near-ceiling win rates by then) — this is a genuinely harder objective, not evidence of a mechanic being broken.

**Update: resumed training to 6M timesteps total** (`train.py --full-expedition --resume --timesteps 3000000`, loads existing weights + optimizer state via `PPO.load`, `reset_num_timesteps=False` so the timestep counter and eval-callback continuity are preserved — verified working via a 5k-step smoke test first: eval reward picked up at 6.79, matching the 3M checkpoint's 6.73, no discontinuity). Second 3M-step leg took ~2h17m (slower fps than the first leg, 363-402 vs 505-910 — likely other load on the machine, not a training-mechanics issue). Re-ran the identical 750-episode eval:

| Comp | Short zero-death (3M→6M) | Medium (3M→6M) | Long (3M→6M) |
|---|---|---|---|
| Dark Ritual: Heist | 6%→2% | 0%→0% | 0%→0% |
| Mark for Blood | 0%→2% | 0%→0% | 0%→0% |
| Holy Hymn: Monk | 2%→0% | 0%→0% | 0%→0% |
| Blight, Bite & Bleed V2.0 | 4%→4% | 6%→0% | 0%→0% |
| Holy Hymn: Bulwark | 6%→8% | 2%→4% | 4%→6% |

**Honest read: doubling the budget to 6M did not produce a clean win on zero-death rate** — mostly flat-to-noisy (all 15 cells are within ±1-4 percentage points, well inside the sampling noise of 50 episodes per cell at a true rate near 0-8%), except Holy Hymn: Bulwark, which improved consistently across all 3 bands (6/2/4%→8/4/6%) and also its mean reward improved meaningfully (-9.96/-7.86/-7.58 → -4.59/-5.42/-5.86) and `mean_combats_reached` ticked up (2.5-3.1→3.4-3.6) — the one comp that was clearly still improving is the one that gained the most, consistent with "more budget helps where there's more room to learn." Mean final stress is essentially unchanged (still 170-200, near the heart-attack ceiling almost everywhere) — the stress-minimization half of the objective hasn't visibly improved either. Still zero timeouts across all 15 cells both times, confirming the mechanics themselves are sound and stable; this reads as "the zero-death compound objective needs either substantially more budget than 6M, a reward-shaping adjustment (`W_EXPEDITION_STRESS`/`REWARD_HERO_DEATH` were both starting guesses, flagged as such at design time), or both" rather than evidence of a bug. Not chasing this further without user direction — flagging as the concrete next decision point.

## Reward realignment — the 3M/6M zero-death plateau was a misaligned objective, not a training-budget problem

Diagnosis, confirmed directly in the reward code (not just inferred from the flat 3M→6M eval numbers above): the original reward rewarded "win every combat," not the user's actual stated objectives (1. never lose a hero, 2. minimize final aggregate stress). Summed over an expedition, the agent was maximizing `REWARD_WIN=+5.0` fired every internal combat — up to **+90** over 18 combats — against which `REWARD_HERO_DEATH=-3.0` is negligible (losing a hero cost ~5% of the reward from winning through) and `Φ_expedition` is purely potential-based, so it telescopes to ~0 over a full episode (Ng/Harada/Russell) and **never entered the objective function at all**, only reshaped when reward arrived. Doubling the timestep budget 3M→6M couldn't fix this because PPO had already converged to a real (if unwanted) optimum of that objective — more budget refines convergence to the same wrong optimum, it doesn't change what the optimum is. Worse than a magnitude problem: `_compute_expedition_potential` averaged stress over *living* roster heroes only, so when a stressed hero died their high-stress term simply dropped out of the mean — a genuine spurious positive-reward gradient at the exact step a hero dies, rewarding the policy for killing off its own liabilities.

Fix (all in `env/expedition_sequence_env.py` unless noted), keeping the existing potential-based shaping exactly as-is (it's policy-invariant and was never the misalignment) while making the real, optimum-changing terms encode the priority order survival ≫ stress ≫ individual combat wins:
- `env/gym_env.py`: `REWARD_WIN`/`REWARD_LOSE` became instance attrs (`self._reward_win`/`self._reward_lose`, set from the module constants in `DarkestDungeonEnv.__init__`) instead of module constants read directly in `step()` — zero behavior change for `DarkestDungeonEnv`/`ExpeditionEnv`, but lets a subclass override the per-combat win magnitude.
- `ExpeditionSequenceEnv.__init__` overrides `self._reward_win = 1.0` (down from 5.0) — a small per-combat progress/credit-assignment signal instead of a reward large enough to dominate on its own. `self._reward_lose` stays at the inherited default (-5.0); death penalties do that job now.
- `REWARD_HERO_DEATH`: `-3.0 → -20.0` — a single death now outweighs the maximum possible per-combat win reward for an entire 18-combat expedition (`18 × 1.0 = 18 < 20`).
- New `REWARD_EXPEDITION_COMPLETE = +10.0`: a real, one-time bonus applied only at the true expedition end without a party wipe (`terminated and not party_wiped` in `step()` — at that point in the method this can only mean the *last* sampled combat was won, since an internal mid-expedition win already got `terminated` reset to `False` a few lines earlier, and `CombatEngine._check_win_loss` sets `winner=="enemies"` iff no heroes are alive, so "terminated, not wiped" can't mean a loss either).
- New `TERMINAL_STRESS_PENALTY_WEIGHT = 10.0`: at any true end (`terminated or truncated`), `reward -= TERMINAL_STRESS_PENALTY_WEIGHT * self._mean_roster_stress_frac()` — a **real, non-potential** term, so unlike `Φ_expedition` it doesn't cancel out over the episode and genuinely enters the objective.
- `_compute_expedition_potential`/new `_mean_roster_stress_frac` (factored out since both need identical accounting) now average over the **original roster**, not just the living: `n_dead_prior = len(self._original_hero_ids) - len(self.engine.heroes)` heroes lost in earlier combats, plus any `is_dead` hero in the current combat, each count as max stress (frac `1.0`); living heroes count `min(stress,200)/200` as before. Since `1.0 >= min(stress,200)/200` always, a death can now only ever make the roster-mean stress fraction non-decreasing (never a spurious improvement) — this directly closes the perverse gradient above. One side effect worth knowing: `_compute_expedition_potential()` on a fully-wiped party now correctly returns `-1.0` (worst case) instead of the old special-cased `0.0` for "no living heroes" — that old special case was part of the bug, not a deliberate convention, and `step()` already forces the shaping term itself to `0` at any true terminal step regardless of what this method returns, so nothing downstream depended on the old value.

Design check — incentive ordering with the new values, 12-combat expedition: `all alive + low stress (~+22)` > `all alive + high stress (~+12)` > `1 death (~+2)` > `2 deaths (~-18)` > `party wipe (~-79)`. Matches survival ≫ stress ≫ individual wins. All magnitudes above are simulator convention, not derived from any game data (no source exposes "how much an RL reward should penalize a death") — same treatment as `TIER_TO_HERO_LEVEL`/expedition-length bands/provision quantities elsewhere in this doc; the incentive *ordering* is robust to reasonable perturbation of the exact numbers, tune further only if a real training run's direction demands it.

Verification: `tests/env/test_expedition_reward.py` extended with `test_all_alive_scores_higher_than_one_death_at_true_expedition_end`, `test_high_final_stress_scores_lower_than_low_final_stress`, `test_terminal_stress_penalty_is_a_real_non_telescoping_term` (proves the term isn't absorbed by the surrounding potential-based shaping — zeroing `TERMINAL_STRESS_PENALTY_WEIGHT` via monkeypatch changes the returned reward by exactly `TERMINAL_STRESS_PENALTY_WEIGHT * mean_roster_stress_frac`), `test_completion_bonus_only_on_true_expedition_end` (an internal mid-expedition win produces identical reward whether `REWARD_EXPEDITION_COMPLETE` is patched to 0 or 1000 — proves it never fires there), and `test_death_never_increases_expedition_potential`. Full suite green (249 tests, up from 244) after the change.

**The pre-realignment `dd_combat_ppo_full_expedition.zip` (the 6M-timestep model evaluated above) is obsolete under the new reward** — the two rewards aren't comparable and `--resume` can't cross a reward-function change; a from-scratch retrain is required, and its eval should be compared directly against the 0-8%/170-200 baseline tables above once run.

## Investigation phase after the reward realignment — formation-order bug, real trinkets/quirks wired in, DLC trinket/quirk gap, logging upgrade

Triggered by a qualitative replay of the realigned 3M model (see below) plus a direct user question: comp "Pigpen Cleanup"'s Hellion looked out of formation in the replay log. Investigated three things end to end: comp/formation correctness, real status-effect mechanics (cross-checked against `darkestdungeon.wiki.gg`), and real combat-math mechanics — found and fixed several real, previously-undiscovered bugs; found (but deliberately did not fix this session) two more.

**1. Formation-order bug — 100% of trained/evaluated comps had a scrambled front-to-back formation.** `store.comps` (`COMPS/` CSV, used by `ExpeditionEnv._build_hero_pool` to build `hero_ids`) lists heroes **alphabetically by class name** — an artifact of that CSV's own generation. `CombatEngine.reset()` assigns `position = index in hero_defs` whenever `hero_positions` isn't given, so `hero_ids` order **is** each hero's formation — using the CSV order directly silently replaced every comp's real formation with alphabetical order project-wide. Verified systematically: 0/129 checked comps had CSV order matching the real formation in `store.comp_loadouts` (the per-comp JSON, see "Real per-comp skill loadouts" in `CLAUDE.md`), all 129 were alphabetical. Concretely, "Pigpen Cleanup"'s real `Hellion(front), Houndmaster, Plague Doctor, Arbalest(back)` became `Arbalest(front!), Hellion, Houndmaster, Plague Doctor(back!)` — a backline ranged unit put in the exposed melee rank, a support unit pushed to the very back. Skills/trinkets were unaffected (matched by `hero_id`, not position) — this was purely a positioning bug. Fixed in `ExpeditionEnv._build_hero_pool` (mirrored in `eval_expedition.py`'s `pick_representative_comps`): use `store.comp_loadouts`' real order whenever it exists AND its hero multiset exactly equals the CSV-derived one (`Counter` equality, not just `set()` — distinguishes a genuine reorder from a different roster) — 92/134 pool comps. The other 42 (12 with no JSON match, 30 where the CSV deduplicates a comp's repeated hero class, e.g. "Hound Pack: Berserker" has 2 real Houndmasters but the CSV lists only 3 unique classes — a separate, smaller CSV data-quality gap, not fixed here) fall back to the pre-existing CSV order, unchanged. Pool size (134) is unaffected. Verified via `tests/env/test_expedition_env.py::test_hero_pool_uses_real_json_formation_order_when_available`/`test_hero_pool_size_unchanged_by_formation_order_fix`.

**2. Trinkets/quirks now auto-applied — plus a DLC trinket/quirk loader gap found while wiring it.** `HeroLoadout.trinket_ids`/`quirk_ids` (resolved by `CompLoadoutLoader`) were captured but nothing ever called the pre-existing `CombatEngine.apply_trinkets_and_quirks`. Wired into `ExpeditionEnv.reset()` (auto-sampled path only, matching the existing skill-loadout convention), `ExpeditionSequenceEnv` (both its explicit- and auto-sampled paths, reapplied at the start of *every* internal combat via `_apply_trinkets_and_quirks_for_current_roster`, since `CombatEngine.reset()` rebuilds a fresh `CombatantState` each combat but trinkets are permanent-for-the-expedition equipment), and `eval_expedition.py`. While verifying this end-to-end (Pigpen Cleanup's Hellion should get "Thirsting Blade", +8% crit), found `com_hel_thirsting_blade` wasn't in `store.trinkets` at all — real bug: `store.trinkets`/`store.quirks` only ever loaded their single base-game file each (`trinkets/base.entries.trinkets.json`, `shared/quirk/quirk_library.json`), unlike `store.buffs`/`store.effects`, which already merge in `dlc/**/*.buffs.json`/`*.effects.darkest` (same treatment `HeroLoader.load_all` already gets for DLC heroes). "Thirsting Blade" turned out to be a Color of Madness DLC trinket (`dlc/735730_color_of_madness/trinkets/special_com.entries.trinkets.json`) — silently missing from every comp's real loadout resolution project-wide until now. Fixed in `DataLoader.load_all`: merges `dlc/**/*.entries.trinkets.json`/`dlc/**/*quirk_library.json` too (base wins on id collision, matching the buffs/effects merge convention) — total went from 490→581 trinkets, 170→213 quirks. One wrinkle: the Arena PvP DLC's trinkets file (`dlc/1117860_arena_mp/trinkets/arena.entries.trinkets.json`) is genuinely **encrypted/binary**, not valid UTF-8 like every other plain-text game data file in the install (confirmed by inspecting raw bytes — no recognizable text structure at all) — skipped via try/except rather than crashing the whole load; consistent with this project's existing PvP-exclusion convention (Daze, see below). Verified via `tests/data/test_loader.py::test_dlc_trinkets_and_quirks_merged_into_store`, `tests/env/test_expedition_env.py::test_reset_applies_real_trinkets_and_quirks_when_comp_has_one`, `tests/env/test_expedition_sequence_env.py::test_trinkets_reapply_at_the_start_of_every_internal_combat`.

**3. Real train/eval mismatch found and fixed while auditing the trinket-wiring path.** `eval_expedition.py --full-expedition`'s `run_full_expedition_episode` never resolved or passed `hero_skill_ids` at all — unlike the single-combat eval loop (`main()`'s tier-band table), which already did this correctly. This means **every prior full-expedition eval run** (including the 1M/3M reward-realignment validation runs earlier this session) evaluated each hero with their full ~7-skill movepool instead of the real 4-skill loadout the model was actually *trained* with (training itself was always correct — `ExpeditionSequenceEnv`'s own internal auto-sampling path has always called `_resolve_skill_ids`). Fixed by threading `comp_name` and a freshly-resolved `hero_skill_ids` through `run_full_expedition_episode`'s options (mirroring the single-combat path's existing `resolve_skill_ids` helper, now factored out and shared). **This means the 1M/3M eval tables reported earlier this session are not fully trustworthy as a read on the realigned reward** — the policy was trained against restricted loadouts but evaluated against full ones, a real mismatch independent of the reward-magnitude question. Re-running the eval (not yet done) is needed before drawing further conclusions about the realigned reward's effect on zero-death rate/final stress/combats-reached.

**4. Logging upgraded for debugging** (`env/gym_env.py`'s `render()`): now prints the new `engine.log` lines (skill used, target, hit/miss/crit, buffs gained) since the last `render()` call, ahead of the HP/position/stress snapshot — previously only the snapshot was shown, requiring a one-off custom script (as used earlier this session) to see what a policy actually did each turn. `train.py --play`'s existing `print(env.render())` loop gets this for free now.

**Two more things found via the status-effect/combat-mechanics cross-check against the wiki — both fixed:**
- **Bleed/Blight were double-gated by resistance.** `_tick_dots` (`engine/combat_engine.py`) re-rolled `self.rng.random() < (1.0 - resist)` on **every tick** to decide whether that round's DoT damage applied, on top of the resistance check already gating whether the DoT gets inflicted in the first place (`_resolve_effect`'s `effective_resist` roll). The wiki (`darkestdungeon.wiki.gg/wiki/Bleed_(Darkest_Dungeon)`/`Blight_(Darkest_Dungeon)`) describes resistance as gating *infliction only* — once bleeding/blighted, damage applies every round of the duration, no further check. (One test, `test_monster_ai.py`, sets `bleed_resist = 0.0` specifically "to guarantee the resist roll doesn't block it," suggesting this was noticed informally but never resolved before now.) Every DoT in the simulator had dealt less expected damage than the real game, scaled by the target's own resistance stat, for the project's entire history until this fix. Fixed by removing the per-tick roll entirely — `_tick_dots` now applies `dot.damage_per_tick` unconditionally every tick, resistance only ever consulted at infliction time. Verified via `tests/engine/test_gaps.py::test_dot_damage_applies_every_tick_regardless_of_resist` (bleed/blight resist forced to 0.99, damage still applies in full both ticks).
- **Horror (a real stress-damage-over-time status effect) was unimplemented due to a parser key mismatch, the same bug class as the historic `dmg_min`/`dmg_max` and unquoted-trailing-decimal bugs.** `data/effect_loader.py` checked for a `"stress_damage_per_round"` key, but the real `.darkest` field is `.dotStress` (confirmed directly: `effects/base.effects.darkest`'s `"Madman Horror 1/2"`, `"Ghoul Stress DOT"`, `"Drum Stress DoT"`, `"Stress AOE DoT 1/2"` templates, referenced by real monster skills including Ghoul's `howl` — the exact enemy that finished off the party in this session's replayed episode). This directly contradicted the "Laudanum is currently mechanically inert... verified no real combat effect in the game ever inflicts this" claim elsewhere in this doc/`CLAUDE.md` — that verification was against a parser looking for the wrong key, a false negative. Fixed: `EffectDefinition.stress_damage_per_round`/new `stress_dot_rounds` now parse from `.dotStress`/`.duration`; new `CombatantState.stress_dots` (a `DotInstance` list, same shape as `bleeds`/`blights`) is inflicted in `_resolve_effect` (hero-only, gated only by the effect's own `.chance` — no dedicated "Horror resist" stat exists in the real game, unlike bleed/blight) and ticked via new `_tick_stress_dots`, which routes through the existing `_apply_stress` helper (not a direct `.stress` mutation) so `stress_dmg_received_mult`/affliction/virtue/heart-attack triggers all fire correctly from a Horror tick exactly as they would from any other stress source. Laudanum's real `.clearDotStress` cure (`_use_provision`) is no longer inert — it clears `hero.stress_dots`. Verified via `tests/engine/test_gaps.py::test_horror_stress_dot_inflicts_ticks_and_clears_with_laudanum`.
- (Checked and confirmed correct, no action needed: Riposte firing on a missed incoming attack — `_maybe_trigger_riposte`'s separate `on_hit_chance`/`on_miss_chance` already handles this exactly as the wiki describes.)

**Broader bug scout across the engine/data layer** (systematically diffing every `.key` actually used in real `effects/`/`heroes/`/`monsters/` `.darkest` files against every key each loader checks for, the same technique that found the Horror mismatch) turned up three more real, verified-in-data gaps — user chose to fix all three before retraining ("high confidence on the model... rather than fine tuning on an unrealistic model"):
- **`extra_targets_chance`/`extra_targets_count`** (30 real monster files, including boss/named monsters — Crone's `curse_vulnerability`/`embrace_the_dark`, Ancestor's `ancestor_fuse_two`, Brigand Sapper's `sapper_detonate`/`sapper_stress`) — a real chance-based "also hit N extra target(s)" mechanic (`extra_targets_count` is always exactly 1 in every real occurrence; `extra_targets_chance` ranges 0-1, scaling up by tier), completely unhandled anywhere in the codebase before this. Distinct from the already-implemented `~`-prefix deterministic AoE notation (hits every rank in the target list, no roll) — this is a probabilistic *additional* target beyond whichever one the skill's normal single-target selection already picked. Fixed in `_execute_enemy_skill` (`engine/combat_engine.py`): after the primary target is chosen, each of `extra_targets_count` independently rolls `extra_targets_chance`; on success, `_select_enemy_target` picks one more *distinct* target (excluded from the remaining pool as they're added, so the same hero is never double-counted) and receives the identical attack/effect resolution. Verified directly against Crone's real `curse_vulnerability` (`tests/engine/test_gaps.py::test_extra_targets_mechanic_hits_a_second_distinct_hero`) — confirmed two genuinely different heroes get hit in the same skill use across enough seeds, and confirmed a normal (`extra_targets_count=0`) skill never does.
- **`can_be_riposted false`** (7 files — `bloated_corpse`'s `explode`, `crow`'s `escape`, and similar self-destruct/flee skills, all paired with `kill_self_queued`) — real; Riposte used to fire against these specific narrow-scope monster skills when the real game wouldn't allow it. Fixed: `SkillLevelData.can_be_riposted` (default `True` — no known real hero-skill occurrence, so this changes nothing for the vast majority of skills) gates the existing `_maybe_trigger_riposte` call site. Verified via `tests/engine/test_gaps.py::test_can_be_riposted_false_blocks_riposte` (a hero with an active Riposte state takes no counter-riposte against `bloated_corpse_A`'s real `explode`).
- **`ignore_protection`/`ignore_stealth`** — resolved via the wiki rather than guessing from field names alone. Grave Robber's "Pick to the Face" (`ignore_protection`) is wiki-described as **"Armor Piercing"**; Highwayman's "Tracking Shot" (internal id `take_aim`, `ignore_stealth`) is wiki-described as **"Bypass/Remove Stealth"**. Both fixed: `resolve_attack` (`engine/resolution.py`) skips the `applied *= (1.0 - target.prot)` line entirely when `skill.ignore_protection` (verified: same seed/skill, only this flag differs, damage against a 50%-PROT target roughly doubles, `tests/engine/test_gaps.py::test_ignore_protection_bypasses_prot`). Critically, `ignore_stealth` turned out to also be a real, previously-unhandled **monster**-skill flag (Crow's `distract`, Cyst's `beholder_gaze`/`beholder_burn`, Prophet's `prophet_mark`/`prophet_attack`/`eye_on_you`, Brigand Sapper's `sapper_detonate`) — `_execute_enemy_skill`'s stealthed-hero exclusion (`targetable = [h for h in heroes_alive if not h.is_stealthed]`) applied unconditionally, with no bypass for skills specifically meant to reveal a hidden hero. Fixed by gating that exclusion on `sd.ignore_stealth` (verified via `tests/engine/test_gaps.py::test_ignore_stealth_allows_targeting_a_stealthed_hero`: with the flag off a stealthed hero is never targeted across 30 seeds, with it on they can be). The hero-side reading (bypassing an *enemy's* stealth) stays unexercised in practice — no monster in the install was found to self-stealth via a direct search — but the field is parsed and would work correctly if that ever changed.
(Checked and out of scope, not bugs: `removeStatus`/bare `clearguard` genuinely never appear in any real effects file, base or DLC — dead code paths, not silent failures. The remaining unmatched monster/hero keys are camping/quirk-generation/deck/curio/Crimson-Curse-related, all already explicitly out of scope per this doc/`CLAUDE.md`.)

## Post-faithfulness-fixes retrain, and the real root cause of the still-capped comps: the action space, not compute

Retrained `dd_combat_ppo_full_expedition.zip` from scratch, 3M timesteps, on the engine as fixed above (formation order, real trinkets/quirks, DLC trinket/quirk loading, Horror, bleed/blight, `extra_targets`, `can_be_riposted`, `ignore_protection`/`ignore_stealth` — everything through the previous section). Old model archived as `dd_combat_ppo_full_expedition_prefaithfulnessfix_3M_backup.zip`. Re-ran the same 50-episode × 5-comp eval:

| Comp | Zero-death before→after (S/M/L) | Combats reached before→after (S/M/L) |
|---|---|---|
| Dark Ritual: Heist | 6/4/0 → 10/8/4 | 5.2/5.4/5.4 → 6.5/7.0/7.8 |
| Mark for Blood | 0/0/0 → 2/0/0 | 3.5/3.9/4.1 → 5.5/5.9/6.1 |
| Holy Hymn: Monk | 0/0/0 → 0/0/0 | 2.1/2.2/2.2 → 2.4/2.1/2.1 |
| Blight, Bite & Bleed V2.0 | 0/0/0 → 0/0/0 | 2.0/2.2/2.3 → 1.9/2.0/2.1 |
| Holy Hymn: Bulwark | 6/2/2 → 4/6/0 | 1.5/1.4/1.6 → 1.6/1.4/1.5 |

**Real, meaningful improvement for 2/5 comps** (Dark Ritual: Heist's combats-reached now nearly matches the original pre-realignment 3M baseline of 6.8/7.2/7.2, while also gaining real zero-death the old reward never achieved) — **but 3/5 comps (Monk, Blight/Bite/Bleed, Bulwark) showed literally no movement.** User asked to scout for a structural cause before committing to more training budget, rather than assume it's compute-bound.

**Root cause found via replaying Holy Hymn: Monk with the new action-log render():** the party got stuck for ~66 consecutive steps in combat 0, with Jester and Highwayman just shuffling positions back and forth (`Jester moves from 2 to 4`, `4 to 3`, `3 to 4`, `4 to 1`, `1 to 2`, ...) instead of attacking a single, already-weakened `cultist_brawler_B`, while only Vestal made any progress. Instrumenting the raw model prediction directly exposed the mechanism precisely:

```
step 22: raw_action_idx=14, num_valid=5  -> clamped to __move__ (last in list)
step 23: raw_action_idx=26, num_valid=7  -> clamped to __move__
step 33: raw_action_idx=21, num_valid=13 -> __provision_antivenom__
step 38: raw_action_idx=21, num_valid=10 -> clamped to __move__
```

`env/gym_env.py`'s `Discrete(64)` action space was already documented as "an index into whatever `get_valid_actions()` currently returns... not a stable encoding across states" — but the real-world severity of that wasn't appreciated until this replay: `step()`'s `action_idx = min(action, len(valid_actions)-1)` means a policy that learned "index 21 is usually a good attack" (true when ~18+ actions are legal) gets **silently clamped to whatever's last in the list** — almost always `__move__` or a `__provision_*__` action — the instant fewer actions are legal (skills on cooldown, per-battle-limit skills exhausted, provisions depleted — exactly what a long grinding fight against one weakened enemy produces). This is not a data/mechanics bug and not a compute shortfall — no amount of additional training timesteps fixes a policy aiming at an index whose real-world meaning keeps shifting underneath it. This plausibly explains why the smaller/weaker comps (fewer heroes → fewer total legal actions on average, more likely to fall below whatever index the model is aiming for) were the ones capped, while 4-strong-hero comps with more skill diversity had more headroom before hitting the same wall.

**Fix: proper action masking**, the standard RL solution for variable-size legal-action sets (`pip install sb3-contrib`, not in `requirements.txt`, same "install separately" treatment as `stable-baselines3` itself):
- `DarkestDungeonEnv.action_masks()` (`env/gym_env.py`) returns a `(64,)` bool array, `True` for indices `0..len(self._valid_actions)-1`, `False` beyond — the exact same indices `step()` would otherwise clamp into.
- `train.py`/`eval_expedition.py` switched from `stable_baselines3.PPO`/`EvalCallback` to `sb3_contrib`'s `MaskablePPO`/`MaskableEvalCallback`, wrapping every env in `ActionMasker(env, lambda e: e.action_masks())` and threading `action_masks=get_action_masks(env)` through every `model.predict(...)` call (`--play`, both eval paths).
- **Real bug found in `sb3_contrib.common.wrappers.ActionMasker` itself** while wiring this up: its string-lookup branch (`ActionMasker(env, "action_masks")`) does `found_method = getattr(self.env, "action_masks")` (already a bound method) but its own `action_masks()` then calls `self._action_mask_fn(self.env)` — passing `self.env` a *second* time to an already-bound method, raising `TypeError: action_masks() takes 1 positional argument but 2 were given` for any normal instance method. Worked around by using the callable form instead (`ActionMasker(env, lambda e: e.action_masks())`), which the same code path handles correctly since a plain lambda genuinely does expect the env as an explicit argument.
- Verified via `tests/env/test_env.py::test_action_masks_shape_and_dtype`/`test_action_masks_marks_exactly_the_valid_actions_true`/`test_action_masks_tracks_shrinking_valid_action_count_across_steps`, plus an end-to-end smoke test (`train.py --full-expedition --timesteps 5000`, `train.py --play --full-expedition`) confirming masked training/eval/play all run cleanly before committing to a full retrain.

**A second, independent structural cause found for the same comp while investigating the above**: "Holy Hymn: Monk" (one of the 3 flat/capped comps) is user-flagged and confirmed as a comp our data can't fully represent — it's really a 4-hero comp (Highwayman, Jester, Vestal, and a modded "Monk" class), but `store.comps`' own `Heroes:` list only has the 3 real ones (the modded slot is simply omitted, not an unresolvable placeholder), so it was silently trained/evaluated as a 3-hero party the whole time — a structural disadvantage baked into the source data, unrelated to the model or the action-space bug. Checked systematically: `COMPS/Comps/<name> <hash>.md` (a Notion export per comp) has an explicit `Modded Heroes: X` line, and 11 comps have a non-empty one (Monk, Black Mage ×4, Spartan, Falconer, Sisters, Cataphract, Thrall) — distinct from the base-game class *mods* (shieldbreaker/musketeer/flagellant), which fully load and aren't affected. Fixed: new `CompsCsvLoader.load_modded_hero_comp_names(csv_dir)` parses this field (matched by the comp's `# <name>` header, not the hash-suffixed filename), wired into `store.modded_hero_comp_names`, and consumed by `ExpeditionEnv._build_hero_pool`/`eval_expedition.py`'s `pick_representative_comps` to exclude these comps entirely rather than silently under-strength them. Pool size: 134 → 125 (9 newly excluded; 2 of the 11 modded comps, e.g. "Holy Hymn: Spartan" with only 1 real-class hero, were already excluded by the pre-existing zone/`>=2 heroes` filters). Verified via `tests/data/test_loader.py::test_modded_hero_comp_names_detected_from_md_metadata` and updated `tests/env/test_expedition_env.py::test_hero_pool_built_from_real_comps`/`test_hero_pool_size_unchanged_by_formation_order_fix` (134→125). Applied before the masked retrain below, alongside the action-masking fix, so both structural causes are addressed together rather than needing a third retrain.

**Retrain results** (3M timesteps, ~54 min wall clock at ~900fps): re-ran the eval, first against the same 5-comp sample size (different actual comps now, since the pool itself changed), then a broader 12-comp × 3-band × 50-episode sweep (1800 episodes, 0 timeouts everywhere) for a fuller picture:

| Comp | Zero-death (S/M/L) | Combats reached (S/M/L) |
|---|---|---|
| Snipe & Slice | 14/18/6% | 6.6/9.1/8.1 |
| All Good Hellion | 12/8/2% | 6.4/6.9/7.2 |
| All Good Bounty Returns | 4/0/2% | 5.4/5.9/7.2 |
| Prophet's Last Sight V3.0 | 8/4/0% | 5.2/5.9/5.3 |
| Marked Prey: Stun | 0/2/0% | 5.8/6.5/6.3 |
| Blight, Bite & Bleed | 6/0/0% | 3.4/3.8/3.8 |
| Dark Ritual: Classic | (not in this sample) | — |
| Dark Ritual: Ballad | 0/0/0% | 2.6-2.8 |
| Dark Ritual: Hound | 0/0/0% | 2.6-3.4 |
| Battlefield | 0/0/0% | 1.6-1.7 |
| Prophet's Last Sight V2.0 (3 heroes) | 0/0/0% | 2.0-2.2 |
| Sacred Lunge (3 heroes) | 0/0/0% | 4.3-4.6 |
| Ascetic Movement (2 heroes) | 0/0/0% | **exactly 1.0, all 150 episodes** |

Best-case zero-death rate (14-22% for Snipe & Slice) is the highest seen all session — real progress. But several comps remain flatly stuck, and one number was too clean to be noise: **"Ascetic Movement" hit exactly 1.0 combats reached across all 150 episodes**, never varying.

**Investigated "Ascetic Movement" via replay — resolved, not a bug.** This is a genuine 2-hero comp (Crusader, Flagellant). The replay showed it facing a full 4-enemy encounter (cultist_witch, skeleton_arbalist, cultist_brawler, skeleton_militia) — real mash-file encounter tables have no party-size dimension, so a 2-hero party faces the exact same enemy groups a 4-hero party would. Both heroes died to sustained fire within the first combat, every time, across every sampled tier/zone/seed — a structurally brutal, faithfully-represented matchup, not a bug. Same underlying explanation extends to the two 3-hero comps (Prophet's Last Sight V2.0, Sacred Lunge) being among the weakest: fewer heroes means less redundancy against encounters that don't scale down.

**Investigated the remaining stuck 4-hero comps (Dark Ritual: Ballad) via replay — found the single most severe bug this investigation phase turned up.** The replay showed `"Jester hits Jester for 102"` — a hero landing a massive, self-inflicted attack from its own support skill. Traced to `_execute_action` (`engine/combat_engine.py`): it only ever branched on `is_heal` (`skill_data.heal_min is not None`) vs. falling through to the enemy-attack path (`resolve_attack`) for everything else. But plenty of real ally-targeting support skills heal/buff purely through a *named effect*, not the skill's own bare `.heal` field — Jester's `battle_ballad`/`inspiring_tune` (→ "Embolden Team"), Flagellant's `reclaim`/`redeem`/`endure`/`suffer` (→ "Flag Super Heal"/"Flag Grit", also directly observed in the earlier "Ascetic Movement" replay as `"Flagellant misses Flagellant"`) — so these fell through to the attack path and dealt the performer's own real weapon damage to their ally targets, including the performer itself for AoE/self-inclusive skills. Confirmed directly: `battle_ballad`'s real `.dmg` field is `0%` — which means *no damage reduction at all* (not "-100%"), the misreading that let a support skill deal full, unmitigated weapon damage to its own party as an unnoticed side effect of applying its buff. **Fixed**: added a third branch (`is_ally_skill and not is_heal`) that applies effects/movement only, with no attack roll, no Riposte trigger, and no Guard redirect (mirroring how `_resolve_self_action` already correctly handled self-only skills) — none of those concepts apply to buffing your own ally. Verified via `tests/engine/test_gaps.py::test_ally_buff_skills_never_deal_damage_to_their_targets` (`battle_ballad` never changes any target's HP across 30 seeds, while its real buff still lands) and confirmed the full suite (267 tests) stays green — no existing test had exercised (or depended on) the old behavior, since it was purely an accidental side effect nobody had written a test against.

**Found and fixed a second, related gap while investigating the above**: `get_valid_actions` never checked a skill's `valid_modes` against the performer's current mode — only `_check_valid_mode` did, at execution time. So Abomination's beast-only skills (`rage`/`rake`/`slam`) were offered as legal actions while in human mode (and vice versa for `absolution`), silently rejected on selection (logged as `"cannot use that skill in X mode"`), wasting the entire turn — the same *class* of "offered but not really usable" problem the action-masking fix addressed, just via a different mechanism (missing upfront filtering rather than index clamping). Fixed by factoring the mode check out into a shared `_is_valid_mode` helper and calling it from `get_valid_actions`'s skill-filtering loop, alongside the existing `launch` check. Verified via `tests/engine/test_gaps.py::test_get_valid_actions_excludes_mode_invalid_skills`.

Given how common ally-buff/support skills are across the roster (nearly every class has at least one), and that this bug meant *using your own support kit could kill your own party*, this is very plausibly a major contributor to the flat/capped comps above.

**User-reported real bug found while reviewing the "Ascetic Movement" investigation, exposing a systemic design flaw: `store.comps` (the CSV) is not a reliable source of a comp's real hero identity, and hadn't been treated with enough suspicion.** "Ascetic Movement" isn't a 2-hero comp (Crusader + Flagellant) as the CSV's `heroes` field suggests — it's really **3 different Crusaders (each with a genuinely distinct real skill loadout and trinkets) + 1 Flagellant**, confirmed directly against `COMPS/Comps/Ascetic Movement/Ascetic_Movement.json`. The CSV's `heroes` field **deduplicates repeated hero classes down to their unique names** — a real, previously-underestimated-in-scope data-quality issue: checked systematically, **37 of 131 comps with a loadout JSON have at least one duplicate-class roster** (e.g. "Prophet's Last Sight V2.0", one of the comps already flagged as capped above, is really 4 heroes with two Arbalests, not the 3-hero comp its CSV-derived hero list suggested during the earlier investigation — meaning that comp's low numbers were *also* partly a data-fidelity artifact, not purely policy/model weakness).

**Fixed as a systemic design change, not a one-off patch** — per explicit user direction ("stick to the JSON rather than the CSV... for future reference"): `ExpeditionEnv._build_hero_pool` (`env/expedition_env.py`) and `eval_expedition.py`'s `pick_representative_comps` now take hero identity/order/**count** directly from `store.comp_loadouts` (the per-comp JSON) whenever a loadout exists for the comp — not merely as a same-multiset reordering on top of a CSV-derived roster (the previous, insufficient fix). Only the ~13 comps with no JSON match at all still fall back to the CSV, on the same (alphabetical, deduplicated) footing as always. Pool size moved again as a result: 125 → 132 (previously-undercounted comps like "Antiques Roadshow" — really 4 Antiquarians, but the CSV's 1-unique-class listing failed the `>=2 heroes` gate entirely — are now correctly included).

This in turn broke `_resolve_skill_ids`/`_resolve_trinkets_and_quirks` (and their `eval_expedition.py` mirrors), which resolved by a **hero_id-keyed dict** — for a duplicate-class comp, a dict can only hold one skill-set per hero_id, so 3 Crusaders would silently collapse to whichever one was inserted last, applying that one Crusader's skills/trinkets to all three. Fixed by resolving **positionally** against the same filtered `loadout.heroes` list `_build_hero_pool` already builds `hero_ids` from (both are built from the identical filtered list, in the same order, so this is safe — a defensive equality check guards the invariant rather than assuming it silently). Verified via `tests/env/test_expedition_env.py::test_hero_pool_uses_real_json_order_and_count_when_available` (formation order *and* count, including the "Ascetic Movement"/"Prophet's Last Sight V2.0" duplicate-class cases) and `test_duplicate_class_comp_gives_each_hero_its_own_distinct_loadout` (each of the 3 Crusaders ends up with its own genuinely distinct skill set and trinkets, not one repeated three times). Full suite green (267 tests) after the change.

**Three real, independent structural fixes now stacked on top of the masked/modded-comp retrain that was stopped before completion** — action masking, the ally-buff self-damage fix, and this CSV→JSON redesign. A fresh retrain is warranted before drawing any further conclusions about the realigned reward or the model's real ceiling; not yet run as of this writing.

## Status after expedition training v4 (decimal-parsing fix + real skill loadout, combined) — the collapse is gone

Retrained `dd_combat_ppo_expedition.zip` from scratch, same 3M timesteps as
v3, but now with both the decimal-parsing ACC fix *and* the real per-comp
skill loadout restriction (previous section) applied together — the two
items "Recommended next steps" had called out to combine into one run.
Old model backed up to `dd_combat_ppo_expedition_v3_backup.zip` before
overwriting.

Training itself was ~2.5x faster than v3 (~1388 fps vs ~530-550 fps, 36 min
vs ~91 min wall clock for the same 3M steps) — expected, since restricting
each hero to 4 real skills instead of ~7 means fewer, more decisive actions
per episode, so more episodes complete per unit time. Final training-eval
reward 4.62±0.15 over episodes averaging 16.4 steps (vs the drawn-out,
often-timing-out episodes typical of earlier versions).

Re-ran `eval_expedition.py` with the *exact* same 5 comps/comp-seed/100
episodes×5 bands as v3, for a direct comparison:

| Comp | Tier1 (resolve2) | Tier2 (resolve2) | Tier3 (resolve4) | Tier4 (resolve4) | Tier5 (resolve6) |
|---|---|---|---|---|---|
| Blight, Bite & Bleed V2.0 | 96% | 91% | 96% | 96% | 80% |
| Dark Ritual: Heist | **99%** | **99%** | **100%** | **100%** | **99%** |
| Mark for Blood | 99% | 100% | 100% | 100% | 97% |
| Holy Hymn: Bulwark | 83%* | 81%* | 69%* | 82%* | 65%* |
| Holy Hymn: Monk | 99% | 100% | 100% | 100% | 96% |

(`*` = at least one episode per cell hit the 150-step timeout without a
winner.)

**The Veteran/Champion collapse is gone for 4/5 comps.** Compare Dark
Ritual: Heist's v3 numbers (74%/82%/8%/24%/8%) to v4 (99%/99%/100%/100%/99%)
— the exact comp/scenario at the center of this entire investigation (the
`--play` trace, the stun-combo verification, the take_aim loadout
question) now wins essentially every fight at every difficulty band,
Champion included. Same story for Mark for Blood (31→99% at tier1,
1→97% at tier5) and Holy Hymn: Monk. This confirms the "Case closed"
diagnosis was correct — the collapse really was a training-quality/
action-space problem, not a simulator bug — and quantifies just how much
the two fixes (correct ACC values, realistic 4-skill action space) mattered
for PPO's ability to actually learn a good policy within a fixed budget.

**New, narrower finding: Holy Hymn: Bulwark (Abomination/Crusader/
Man-at-Arms/Vestal, Ruins) is a real outlier**, not close to the other 4
comps — 65-83% win rate with 17-29/100 episodes timing out at every tier
(vs 0 timeouts for every other comp), and mean episode length 59-87 steps
vs 9-26 for everyone else. This isn't the old "loses every fight" collapse
pattern — it's winning most of the time, just very slowly, with a
meaningful chunk of fights stalling out past the 150-step cap. Plausible
candidates, not yet investigated: this comp is unusually defense/utility
-heavy (Man-at-Arms' Guard/Riposte, Vestal's heals, Abomination's mode-
switching all reduce net damage output relative to a burst comp like Dark
Ritual: Heist), so a policy that plays defensively without closing out
fights could plausibly stall against tankier Ruins enemies. Worth a
targeted investigation (a `--play` trace or scripted-policy check, same
methodology as the "Case closed" section) if this comp matters for
training — not yet done.

## Real per-comp skill loadouts implemented — a third data source found while chasing the user's Dark Ritual: Heist question

Follow-up to the "Case closed" investigation directly below. Two things
the user caught, in order:

1. **`take_aim`/`duelist_advance` do deal real damage.** An earlier pass
   through this investigation (further down) had claimed they were
   "utility only" — wrong, not checked. Verified directly: both go through
   the same `resolve_attack` path as any other non-heal skill (only
   `is_heal` branches differently in `_execute_action`); `dmg_mod=-0.8`/
   `-0.2` just scales the roll down, doesn't zero it. 10/10 seeds each:
   `take_aim` landed 2-3 damage alongside its Tracking Buff; `duelist_advance`
   landed 8-13 damage *and* set `active_riposte` (`on_hit_chance=1.0`),
   which then fired automatically the next time the enemy hit Highwayman —
   exactly the "gets damage in even off-turn" behavior described.

2. **`take_aim` isn't even in "Dark Ritual: Heist"'s real loadout — and
   the simulator had no way to know that.** `HeroDefinition.skills` is
   always a hero's full movepool (~7 skills); nothing in `GAME_DIR`
   narrows it to the 4 a real player would equip (that choice lives in a
   save file, not base install data), and `COMPS/<hash>.csv`
   (`store.comps`) only has hero class + zone, no skills. So every combat
   this simulator has ever run — including every expedition-training
   episode — has given each hero strictly more options than the real game
   would, one more mismatch on top of the already-documented action-space-
   instability issue.

   The user pointed at a fix: `COMPS/Comps/<name>/<name>.json` (repo root,
   493 folders, peer to the `COMPS/<hash>.csv` already loaded) is a second,
   richer per-comp source — `activeSkills` (exactly 4, verified for 504/528
   heroes across all 132 parseable comp JSONs), `activeCampSkills`,
   `trinket1`/`trinket2`, `quirks` — that had never been read by anything
   in this project. Confirmed "Dark Ritual: Heist" is real data here too,
   and its real Highwayman loadout is `pistol_shot`/`point_blank_shot`/
   `duelist_advance`/`opened_vein` — `take_aim` genuinely isn't part of it,
   so testing it wasn't representative of this comp specifically (it was
   still a valid standalone mechanic check, per point 1).

   Built `data/comp_loadout_loader.py` (`CompLoadoutLoader`) to parse this:
   resolves the JSON's display names ("Hands from the Abyss", "Demon's
   Cauldron", "Tough") to internal ids via the game's own English
   localization string tables (`localization/*.string_table.xml` +
   `dlc/**/localization/*.string_table.xml`), not guessed — necessary
   because several skills were renamed after their internal id was set
   (Occultist's `bloodlet` displays as "Sacrificial Stab"; `SKILLS.csv`
   even has this typo'd as "Sacrifical Stab", underscoring why a
   name-guess isn't safe). Hero-id parsing matches the *longest* known
   hero id as a prefix rather than splitting on the first underscore,
   since several hero ids contain underscores themselves (`man_at_arms`,
   `bounty_hunter`, `grave_robber`, `plague_doctor`). Verified: 0 bad
   (unresolvable-to-a-real-skill) ids across all 528 hero-loadout entries;
   95.5% resolve all 4 skills, the rest resolve 3/4 or (one outlier) 7 —
   acceptable, not chased further.

   Deliberately did NOT use `store.trinkets[id].name`/`store.quirks[id].name`
   for the trinket/quirk half of this — both are set to the id itself, not
   a real display name (`BuffLoader.load_trinkets`/`load_quirks` never
   populated one; same shape of bug as the pre-existing
   `SkillDefinition.name=sk_id`). Resolved those via their own separate
   localization keys instead (`str_inventory_title_trinket<id>` — no
   separator before the id — and `str_quirk_name_<id>`). Not fixing the
   `.name` fields themselves — out of scope, nothing before this loader
   depended on either being a real name.

   Wired `store.comp_loadouts` (dict keyed by comp name, 131/145 CSV comps
   have a match) into `CombatEngine.reset(hero_skill_ids: list[list[str]] =
   None)`, which restricts `get_hero_skill_ids`/`get_valid_actions` for the
   matching hero. `hero_skill_ids=None` (default) is exactly the
   pre-existing full-movepool behavior — same "None means unchanged"
   convention as `hero_levels`. `env/gym_env.py` exposes it via
   `options={"hero_skill_ids": [...]}`; `ExpeditionEnv._sample_scenario()`
   now looks up the sampled comp's real loadout automatically (matched to
   `hero_ids` by `hero_id`, since CSV/JSON hero-list order isn't guaranteed
   to agree) and passes it through; `eval_expedition.py` does the same
   lookup so evaluation matches whatever a comp's real loadout restricts it
   to. Trinket/quirk ids are captured and resolved (consumable via the
   pre-existing `CombatEngine.apply_trinkets_and_quirks(hero, trinket_ids,
   quirk_ids)`, which was already implemented but never auto-wired to
   anything) but not yet auto-applied by `ExpeditionEnv`/`eval_expedition.py`
   — declared out of scope for this pass, flagged as a natural next step if
   trinket/quirk realism matters for training later.

   163 pre-existing tests + 8 new (`tests/data/test_comp_loadout.py` ×7,
   `tests/env/test_expedition_env.py` +1) all pass — 171 total.

**Net effect on the "Case closed" conclusion below**: unchanged, and now
demonstrated with a *more* realistic setup than either scripted-policy test
used — the specific stun-opener combo the user described (Plague Doctor
`blinding_gas` → Occultist `hands_from_abyss` → Highwayman/Crusader finish)
still won 5/5 seeds by round 3 against the exact tier-3 Cove group the
trained model failed against, and none of the skills it uses were ever
outside "Dark Ritual: Heist"'s real loadout in the first place. What
changes going forward is that expedition training/eval now sees a
smaller, more realistic action space per hero — worth retraining against
once the user wants to revisit the collapse.

## Case closed: the Veteran/Champion win-rate collapse is a training problem, not a simulator bug

User's idea: instead of comparing different mash-file tiers (which mix
different monster rosters, confounding "harder difficulty" with "different
enemies"), hold the monster **identity** fixed and only vary its A/B/C
variant (Apprentice/Veteran/Champion — same monster line, e.g.
`skeleton_common_A/B/C`), paired with the matching hero resolve level. This
isolates whether tier *scaling itself* is broken, independent of which
specific monsters happen to populate a given mash tier.

**Raw stat comparison** (Crusader at resolve 2/4/6 vs `skeleton_common`
A/B/C, computed directly from loaded data, no engine involved): hit chance
stayed 84-91%, hits-to-kill stayed ≤1.1, at every tier — both sides scale
together sensibly (e.g. Crusader HP 40→54→61 alongside skeleton HP
8→11→16). No red flags in the numbers themselves.

**Scripted-policy combat test** (real `CombatEngine`, no RL — a simple
"attack the first non-heal, non-ally-targeting valid action" heuristic,
paired resolve level per band, 30 seeds each):
- 4×Crusader vs 4×`skeleton_common` at matched A/B/C: **30/30 wins at every
  tier**, resolving in under 2 rounds each.
- The real "Dark Ritual: Heist" comp (resolve 4) vs the **exact** tier-3
  Cove group the trained model previously lost 100% of episodes to
  (`bloated_corpse_B` + `snail_urchin_B` + `ghoul_B` — the fight watched
  turn-by-turn where all three enemies stayed at full HP for 8 rounds
  straight): **30/30 wins**.
- Same comp (resolve 6) vs 30 randomly-sampled real tier-5 (Champion) Cove
  groups: **30/30 wins**.

**Conclusion**: the simulator's combat math, tier scaling, and hero/enemy
data are all correct and well-balanced at every difficulty band — a policy
that does nothing more sophisticated than "attack something" wins
essentially every fight, at every tier, including the exact scenarios where
the trained PPO model scored 0-8%. The Veteran/Champion collapse documented
in the "expedition training v3" phase below is **entirely a reinforcement-
learning problem** (the policy hasn't learned to reliably act well across
this much combined variation — 134 comps × ~1000 enemy groups × 3
gear/skill-tier configurations, compounded by the pre-existing documented
issue that the action-space index has no stable meaning across different
heroes/states), not a data or engine bug. No further data auditing is
warranted from this angle — the next lever is training quality (budget,
hyperparameters, possibly a more stable action representation), not more
bug-hunting.

**Note on the earlier `--play` trace that started this whole investigation
chain**: that fight (which led to finding the weapon-damage and decimal-
parsing bugs) was run against the *trained model*, not a scripted policy —
the model's own poor play was what made 0-damage-in-8-rounds possible, not
the two bugs by themselves. Both bugs were real and worth fixing regardless
(they corrupted the numbers the model trained on), but this control
experiment shows they weren't the root cause of the collapse pattern —
the collapse would very likely have persisted even without them, and did
in fact persist across v1→v2→v3 despite each fix.

**Follow-up: the user directly challenged this conclusion** with a specific
tactical claim, not just "generic attacking should win" — that "Dark Ritual:
Heist" (Crusader/Highwayman/Occultist/Plague Doctor, the exact comp from the
`--play` trace) has a real double-stun opener (Plague Doctor's `blinding_gas`,
an AoE hitting ranks 3-4 with a `Stun 5` effect, + Occultist's
`hands_from_abyss`, a rank-1/2-launched single target hitting ranks 1-3 with
`Abyssal Stun 5`) that should let Highwayman/Crusader burst the enemy down
in essentially one round, making an 8-round 0-damage stalemate look
impossible — implying maybe the scripted "attack anything" policy above
papered over a real bug in stun mechanics or specific skill data that a
sharper policy would still trip on.

Verified directly against real game data first (not memory): confirmed
`blinding_gas` (launch `[4,3]`, target `[3,4]`, AoE, `Stun 5`) and
`hands_from_abyss` (launch `[1,2]`, target `[1,2,3]`, single-target,
`Abyssal Stun 5` + `Darkness 1`) are real, and that "Dark Ritual: Heist"
(Crusader/Highwayman/Occultist/Plague Doctor, Cove) is a real comp in
`store.comps`.

Initially guessed (wrongly, without checking) that `take_aim`/`duelist_advance`
were "utility only" despite their negative `dmg_mod` — user corrected this,
and a direct engine test (10 seeds each, `cultist_brawler_A`) confirms the
user was right and the simulator already handles it correctly: both skills
go through the normal `resolve_attack` path like any other non-heal skill
(`_execute_action`'s `is_heal` check is the only branch, everything else is
an attack), so `dmg_mod=-0.8`/`-0.2` just scales the weapon-damage roll down,
it doesn't zero it out. `take_aim` landed 2-3 damage in all 10/10 seeds
alongside applying `Tracking Buff 5`. `duelist_advance` landed 8-13 damage
on the initial hit in 10/10 seeds *and* set `active_riposte`
(`RiposteState(..., on_hit_chance=1.0)`), which then fired automatically
the next time the enemy attacked Highwayman (`Highwayman ripostes
cultist_brawler_A for N`) — exactly the "gets damage in even off-turn"
behavior the user described. So all 7 Highwayman skills deal real damage in
this simulator, matching the user's original claim more literally than my
first pass gave credit for.

Then simulated the *specific* opening the user described — PD `blinding_gas`
→ Occultist `hands_from_abyss` (preferring whichever enemy isn't already
stunned) → Highwayman/Crusader attacking with their strongest available
skill each turn — against the exact same tier-3 Cove group
(`bloated_corpse_B`/`snail_urchin_B`/`ghoul_B`, hero_levels `[4,4,4,4]`),
5 seeds: **5/5 wins, every one resolved by round 3** (not round 1 as the
user estimated — DD's miss/stun-resist rolls mean it's never literally
deterministic — but nowhere close to the observed 8-round 0-damage
stalemate either). Sample seed 1 log: `blinding_gas` stuns `ghoul_B`,
`hands_from_abyss` stuns `bloated_corpse_B`, Highwayman/Crusader kill
`bloated_corpse_B` and `snail_urchin_B` by round 2, `ghoul_B` dies round 3.
Stun resist (0.7 on all three of this group) behaved exactly as expected —
it *fails* to land some fraction of attempts (e.g. seed 2's `blinding_gas`
never procs the stun on `ghoul_B` at all, just chip damage each turn) rather
than being either guaranteed or dead code.

**This confirms, more specifically than the generic-attack test above, that
the user's tactical read of the comp was correct and the underlying stun/
damage mechanics for these exact skills are implemented and working.** The
"Case closed" conclusion stands and is now stronger: even a policy that
mimics real player doctrine for this exact comp — not just "hit whatever's
alive" — wins convincingly and quickly against the precise scenario the
trained model failed. The 8-round/0-damage trace was the trained model
picking bad actions (or getting stuck cycling through ineffective ones),
not a simulator limitation.

## Status after full project audit — a second parser-level bug found, affecting 779 fields across 240 real game files

User asked for a systematic audit after the weapon-damage bug (below) to
find other errors of the same shape. Found one more severe bug, this time
in the parser itself rather than a single loader's key names — plus a
clean audit of every other loader (`buff_loader.py`, `rules.py`,
`monster_brain_loader.py`, `comps_csv_loader.py`, `enemy_comp_loader.py`)
with no further issues found.

**Bug**: `DarkestParser._parse_line`'s unquoted-value regex has a trailing
lookahead, `(?=\s*\.|$)`, added in an earlier phase specifically to
tolerate zero whitespace before the next key (e.g.
`base.effects.darkest:655`'s `"Arb Mark Target".target`). The problem:
a bare `.` inside an **unquoted decimal number** (e.g. `.atk 72.5%`) looks
identical to "the next key starts here" — the lookahead can't tell them
apart. Since the value match is non-greedy, it stops as early as possible:
for `72.5%`, it matches just `72` and treats `.5%` as if it were an
attempted next key (which then fails to match anything, so `.5%` is
silently dropped). Verified directly: `DarkestParser._parse_line('skill:
.atk 72.5% .dmg 2 4 .crit 12%')` returned `{'atk': 72, ...}` (a bare
truncated int) instead of `{'atk': 0.725, ...}` (the correct percent-decoded
float) — `.dmg`/`.crit` on the same line were unaffected only because
their real values (`2 4`, `12%`) don't happen to contain a decimal point.

**Found while investigating a different result**: chasing why a `--play`
qualitative trace of a Veteran-band fight showed the party dealing zero net
damage over 8 rounds (the weapon-damage bug, documented below) led to
checking real numbers end-to-end; auditing further after that fix turned up
this second, independent bug in the parser layer itself, one level below
`hero_loader.py`.

**Scope, quantified directly against the game install**: 779 individual
`.key value%` instances across 240 real `.info.darkest` files (heroes +
monsters) match this exact shape. Most commonly hit: `.atk N.M%` — ACC
values are very often non-integer percentages (`82.5%`, `88.75%`, etc.),
unlike `.crit`/`.dmg` which are more often round numbers, so this
disproportionately corrupted **accuracy**, not damage — a systematically
*different* (and previously invisible) failure mode from the weapon-damage
bug, not a duplicate of it.

**Fixed**: tightened the lookahead to `(?=\s*\.[a-zA-Z_]|$)` — a real next
key always has a letter immediately after its dot; a decimal fraction
never does. Verified this doesn't regress the case it was added for
(`"Arb Mark Target".target` still parses both keys correctly), verified
multi-quoted-value lists / `@`/`~` position notation / bare-word
identifiers / negative decimals all still parse correctly, and swept the
entire loaded dataset afterward for any remaining suspicious values (any
hero/enemy skill `atk > 300%`, any weapon tier still stuck at `dmg=(1,1)`)
— zero found. A second sweep for suspiciously high resistances/dodge
(`> 300%`) found 14 hits, all verified against their raw source lines to
be genuine, intentional game values (e.g. `ancestor_nebula_D`'s `.hp 999
.stun_resist 1000%` — a scripted near-invincible background entity in the
Ancestor fight; Prophet/Pew puppets with `.move_resist 320%` — real
boss-tier stat blocks), not further parser artifacts. New regression test:
`test_darkest_parser_decimal_percent_value_not_truncated`
(`tests/data/test_loader.py`).

**One test broke from this fix** (not a new bug — a pre-existing test
fragility this fix exposed): `test_aoe.py::test_enemy_aoe_skill_hits_all_heroes_in_range`
relied on `ancestor_big_D`'s monster brain's weighted-random skill
selection landing on the AoE skill (`ancestor_fuse_all`) for one specific
hardcoded seed (`SeededRNG(5)`). Fixing decimal parsing shifted numeric
values elsewhere in the same file (e.g. SPD), which shifted turn-order/
initiative RNG draws, which changed what that seed rolls — a different,
non-AoE skill got selected instead, and the test failed not because AoE
broke but because the test's implicit assumption ("seed 5 always picks
this skill") was never actually guaranteed. Fixed by rewriting the test to
force the specific skill directly via `_execute_enemy_skill` instead of
depending on brain-driven randomness for one seed — tests the AoE
mechanic in isolation, which is what it was actually meant to verify, and
is no longer fragile to unrelated upstream numeric changes.

**Blast radius**: like the weapon-damage bug, this affects real combat
math (hit chance, specifically) across the whole project, meaning the v3
expedition training/eval numbers documented below (already flagged as
superseding v1/v2) are themselves now superseded too — ACC values for
every hero/monster skill with a non-integer `.atk` percentage were wrong
during that training run. Full suite: 163/163 passing (up from 162 — the
new regression test). Not yet retrained against this second fix as of this
writing — see "Recommended next steps."

## THE most significant bug this project has found: every hero has been dealing ~1 damage per hit, always, project-wide

Found while chasing the v2 tier3/5 win-rate collapse via qualitative
`--play`-style inspection (per user request, after v2's pairing fix and a
6x-longer retrain both failed to move tier3/5 off ~0%). Watched a real
tier-3 fight turn by turn: after 8 full rounds / 25 steps, **all three
enemies were still at exactly full HP** — a 4-hero party (Crusader,
Highwayman, Occultist, Plague Doctor) landed *zero* net damage before dying
one by one. That shouldn't be possible even for a bad policy against a
snail with 7 HP, which prompted checking raw damage output directly.

**Root cause**: `data/hero_loader.py`'s weapon-tier parsing
(`_build_hero`, `elif rtype == "weapon":`) read `rec.get("dmg_min", 1)` and
`rec.get("dmg_max", 1)` — but verified directly against a parsed record
(`{'name': 'crusader_weapon_4', 'atk': 0.0, 'dmg': '10 19', 'crit': 0.07,
'spd': 3}`), the real key is a single `dmg` field holding a two-part string
("10 19"), not separate `dmg_min`/`dmg_max` keys — those never exist in any
real weapon record. `dict.get(key, default)` silently returns the default
whenever the key is absent, so **every hero's weapon-derived `dmg_min`/
`dmg_max` has been `1`/`1` at every one of their 5 weapon tiers, for this
entire project's history** — verified: Crusader's real tiers are
`(6,12)/(7,14)/(8,16)/(9,17)/(10,19)` per `crusader.info.darkest`, all 5
were loading as `(1,1)`. Confirmed this is universal, not crusader-specific
(spot-checked Vestal, Highwayman, Abomination — same `dmg` key shape on
all). The sibling `armour` block was never affected — it correctly reads
`def`/`prot`/`hp`/`spd`, verified against real armour records, and that's
exactly why HP/dodge always looked reasonable throughout this project while
damage output silently didn't.

**Why this went undetected through dozens of "verify against real data"
phases**: `tests/engine/test_resolution.py`'s attack-resolution tests construct
`CombatantState` objects manually (`make_attacker(dmg_min=10, dmg_max=19,
...)`), bypassing `HeroLoader` entirely — so damage-resolution *math* was
thoroughly tested and correct, but no test exercised the *loaded* damage
values from real `HeroDefinition` data end-to-end with an explicit number
assertion. Every full-combat test that used `store.heroes[...]` (the vast
majority of `tests/`) only ever asserted relative outcomes (combat
resolves, someone wins, HP decreases) — a hero dealing 1 damage/hit instead
of 10-19 still produces a technically-valid, technically-terminating
combat, just an absurdly slow and lopsided one. This is exactly why it took
an actual turn-by-turn qualitative read (watching a snail survive 25 steps)
to surface it, not any aggregate statistic.

**Fixed**: `_build_hero`'s weapon block now splits the real `dmg` field the
same way `_build_enemy`'s `parse_monster_dmg` already did for monsters —
verified against the raw game file, `crusader.weapon_tiers` now reads
`6-12/7-14/8-16/9-17/10-19` exactly. **All 161 pre-existing tests passed
unchanged after the fix** — confirms nothing anywhere in the test suite was
asserting the broken behavior, a clean fix with zero collateral. New
regression test: `test_weapon_tier_damage_matches_real_game_file`
(`tests/data/test_loader.py`).

**Blast radius — this invalidates every prior training/evaluation
conclusion in this document that involved real hero combat damage**,
including: the original fixed-matchup PPO run (~93% win rate in 100k
steps — heroes were winning *despite* dealing ~1 damage/hit, purely by
attrition against very low enemy HP pools, e.g. 15-HP Cultist Brawlers),
and both expedition v1 and v2 phases below (the whole "win rate collapses
at Veteran/Champion bands" investigation, the hero-level-pairing fix, and
the 6x-training-budget test) — all of it ran against heroes that could
never meaningfully hurt anything. The tier3-vs-tier4 mystery (identical
hero level, wildly different win rate) is now trivially explained: with
real per-hit damage near zero, whichever enemies in a given tier's pool
happen to have marginally more total HP become effectively unkillable
within the 150-step cap — it was never about tier pairing, monster type, or
training budget. **Every training run in this document needs to be redone
against the fixed damage numbers before any of the prior win-rate/band
conclusions can be trusted.** Kept the old sections below as historical
record (correctly re-labeled as pre-fix), not deleted, since they document
real methodology and real (if differently-caused) findings about the
evaluation harness, the size-2 position bug, the missing episode cap, etc.
— none of which are invalidated by this, only the win-rate numbers are.

**Sanity check post-fix**: a scripted always-first-valid-action fight
(2 Crusaders vs 2 Cultist Brawlers) now resolves in 3 steps with real
numbers in the log ("Crusader hits cultist_brawler_A for 12", "for 14")
instead of dragging on with "for 1"/"for 2" hits — confirms the fix
produces game-realistic pacing, not just different numbers.

## Status after expedition training v3 (post weapon-damage-fix) — the real picture

Retrained `dd_combat_ppo_expedition.zip` from scratch, 3M timesteps again
(same budget as the v2 6x-budget test, ~1:31:09 wall clock, ~530-550 fps —
notably slower fps than pre-fix despite less total compute per episode,
because fights now resolve much faster, so far more resets/episodes happen
per unit time and per-reset overhead becomes relatively more significant).
Re-ran `eval_expedition.py` with the same 5 comps/seed, 100 episodes × 5
bands:

| Comp | Tier1 (resolve2) | Tier2 (resolve2) | Tier3 (resolve4) | Tier4 (resolve4) | Tier5 (resolve6) |
|---|---|---|---|---|---|
| Blight, Bite & Bleed V2.0 | 39% | 50% | 0% | 15% | 0% |
| Dark Ritual: Heist | **74%** | **82%** | 8% | 24% | 8% |
| Mark for Blood | 31% | 50% | 2% | 14% | 1% |
| Holy Hymn: Bulwark | 34% | 34% | 4% | 18% | 2% |
| Holy Hymn: Monk | 48% | **76%** | 4% | 17% | 2% |

This is a categorically different, far more plausible result than v1/v2:
- **Apprentice bands (1-2) are now strong**, not just "less broken" — two
  comps (Dark Ritual: Heist, Holy Hymn: Monk) hit 74-82% and 48-76%
  respectively, with **positive mean reward** (up to +3.00), meaning the
  policy is winning decisively, not just eking out attrition wins. This
  didn't happen even once in v1/v2 (every single (comp, band) cell had
  negative mean reward, including "easy" bands).
- **Veteran/Champion bands (3-5) are still weak (0-8%) but no longer a
  flat, suspicious 0% wall** — 8 of 10 (comp, tier3-or-5) cells now show
  nonzero win rate with real variation across comps (Dark Ritual: Heist
  leads at 8%/8%, "Blight, Bite & Bleed V2.0" is the one comp still at a
  hard 0% for both). This is a believable "harder content, comp quality
  still matters, more training/tactics needed" pattern — exactly what you'd
  expect from a genuinely difficult but *not structurally broken* matchup,
  unlike v1/v2's uniform collapse.
- **Comp choice matters even more clearly now**: "Dark Ritual: Heist"
  outperforms "Blight, Bite & Bleed V2.0" by roughly 2x at every single
  band, most dramatically at bands 1-2 (74%/82% vs 39%/50%). With real
  damage numbers actually differentiating skill/gear quality, comp
  selection has real, visible leverage on outcome — the original premise
  behind building this whole system.
- **The tier3-vs-tier4 divergence persists** (same resolve-4 hero
  configuration, 0-8% at tier3 vs 14-24% at tier4) — real, not an artifact
  of the damage bug (confirmed: the bug affected all bands equally, so it
  can't explain a tier3-specific gap that survives the fix). Still
  unexplained; likely genuine per-encounter-roster difficulty variance
  within the "Veteran" bracket rather than anything wrong with the
  simulator. Worth revisiting only if Veteran-band performance specifically
  becomes a priority.

**Recommended next step**: more training budget, now that it would
actually be well-spent (fights are real fights, not near-unwinnable
attrition races) — try 5-10M timesteps focused on lifting the Veteran/
Champion bands specifically, or consider per-band curriculum weighting
(currently all bands within a zone are sampled together, weighted only by
each entry's real in-game `.chance` — nothing biases training time toward
the harder bands the model is currently worst at).

## Status after expedition training v2 (paired hero levels) — the fix did NOT solve the collapse (PRE weapon-damage-fix, numbers superseded)

Following v1's finding ("heroes always max-gear" as the suspected root
cause), three things changed: (1) the whole "curriculum" system was renamed
to "expedition" throughout (code, files, docs — matches the game's own term
for embarking a chosen party into a chosen zone, not borrowed ML
terminology), (2) the `hero_levels` tier-index formula was corrected from
`level-2` to `level-1` per user-specified pairing (verified against
darkestdungeon.wiki.gg/wiki/Weapons' tier-unlock table: a level-2/4/6 hero
is equipped at weapon/armour/skill tier 2/4/5(max) — level6 caps at tier5
since only 5 tiers exist), (3) `ExpeditionEnv`'s sampler and
`eval_expedition.py` now field a Resolve-Level-2/4/6 party against
Apprentice/Veteran/Champion bands respectively (`TIER_TO_HERO_LEVEL` in
`env/expedition_env.py`) instead of always max-gear heroes.

**Retrained**: `dd_combat_ppo_expedition.zip`, 500k timesteps (~9:23 wall
clock, ~850-930 fps on CPU) — same budget as v1, for a clean comparison.

**Re-evaluated** (`eval_expedition.py`, same 5 comps/seed, 100 episodes ×
5 bands = 2500 episodes, deterministic policy):

| Band (resolve) | v1 win rate (unpaired, max-gear) | v2 win rate (paired) |
|---|---|---|
| 1 (resolve 2) | 15-47% | 20-34% |
| 2 (resolve 2) | 18-68% | 17-43% |
| 3 (resolve 4) | 0-1% | 0% |
| 4 (resolve 4) | 4-14% | 4-13% |
| 5 (resolve 6) | 0-1% | 0% |

**Honest finding: pairing hero level to the enemy band did NOT fix the
collapse.** Band 3 (Veteran) and band 5 (Champion) stay at ~0% win rate
either way; bands 1-2 (Apprentice) got *slightly worse* on average under
pairing, not better. This is a genuinely surprising result relative to the
v1 hypothesis, worth stating plainly rather than reframing as a partial
win. Plausible explanations, none confirmed:
- **Band 1-2 got harder, not easier, because it's now a fair fight.** v1's
  band-1/2 numbers were heroes at *max* gear against the *weakest* enemies
  in the game — an intentionally lopsided matchup that should have been
  close to trivial, yet the policy still only won 15-68% of the time
  (itself evidence the model hadn't mastered even the easy end). Once
  heroes are downgraded to match Apprentice-appropriate gear, the fight
  becomes genuinely contested, so a *lower* win rate at the same band is
  not a regression in the policy — it's a harder, more honest test.
- **Band 3+ collapsing regardless of pairing suggests the bottleneck isn't
  gear mismatch at all** — it may simply be that 500k timesteps isn't
  enough training budget for a single network to learn effective play
  across this much combined variation (134 comps × ~1000 enemy groups ×
  now 3 distinct gear/skill-tier configurations per comp instead of 1).
  Each individual (comp, band) cell gets a small fraction of total training
  signal.
- **Real hero comps (`COMPS/` CSV) aren't tagged with a resolve level** —
  they're built assuming *some* level of investment (trinkets, camping
  skills, specific skill choices), none of which this simulator applies.
  A comp that reads well at max level might be substantially weaker with
  the same skill loadout at tier 2/4 gear if its strategy depends on
  late-unlocked skill ranks or trinkets not modeled here.
- **Timeout patterns shifted unevenly across comps**, not uniformly
  better or worse — e.g. "Blight, Bite & Bleed V2.0" dropped from 69 to 6
  timeouts at band 1, while "Holy Hymn: Bulwark" stayed high (69→38) and
  "Dark Ritual: Heist" actually stayed elevated (30→20). Comps built
  around Abomination (mode-switching, stat-buff-dependent) show up in both
  of the worst-timeout cases — plausibly harder for the policy to pilot
  efficiently at reduced skill tier, but not confirmed.

**Not investigated further in this phase** — would need either a longer
training run to rule out "insufficient budget," or qualitative `--play`
inspection of specific (comp, band) pairs to see whether the policy is
making sensible-but-losing decisions or something is going wrong
structurally. Both are reasonable next steps, not done here since they're
open-ended enough to warrant checking in before spending more compute.

Full suite after this phase: 161/161 passing (up from 155 — 6 new
`tests/env/test_expedition_env.py` tests covering the pool-building, the
band→level pairing, and end-to-end resolution).

## Status after expedition training v1 / real-comp evaluation phase (PRE weapon-damage-fix, numbers superseded)

**Renamed from "curriculum" to "expedition"** after this phase — "curriculum
learning" was borrowed ML terminology that didn't fit the project's
vocabulary; "Expedition" is the game's own term for embarking a chosen
party into a chosen zone, which is exactly what this system randomizes
over. `CurriculumDarkestDungeonEnv`/`env/curriculum_env.py`/`--curriculum`/
`eval_curriculum.py`/`dd_combat_ppo_curriculum.zip` below are all historical
names from when this phase ran — see the "expedition training v2" phase
below for the current names and the hero-level-pairing fix this phase's
findings led to (item 6's "Root cause" below).

User asked to retrain using real hero comps (`COMPS/` CSV) paired with real
zone-matched enemy groups (`store.enemy_comps`, previous phase) instead of
the fixed 2xCrusader-vs-2xCultist-Brawler matchup, then evaluate across
difficulty bands over many episodes to see how the model actually behaves.

1. **`store.comps` wired up for real** — `data/comps_csv_loader.py` loads
   `COMPS/` CSV (145 comps: name, hero classes, `Location` zone tag) into
   the `DataStore.comps` field, which was declared in `data/types.py` from
   an earlier phase but never actually populated by `DataLoader.load_all`
   until now.
2. **`CurriculumDarkestDungeonEnv`** (`env/curriculum_env.py`) — randomizes
   (real hero comp, zone-matched real enemy group) every `reset()`. 134/145
   CSV comps are usable (zone maps to one of the 4 core trainable zones,
   ≥2 heroes, every hero class resolves); `DD2`/`Courtyard`-tagged comps
   (3 entries) have no matching zone in `store.enemy_comps` and are skipped,
   `named` (scripted) enemy encounters are excluded from the sampling pool.
3. **Real bug found and fixed**: `CombatEngine.reset()` assigned enemy
   positions via naive `enumerate()` — didn't account for a size-2 enemy
   occupying 2 ranks. Verified this affects **~25% of real multi-enemy
   `hall`/`room`/`boss`/`stall` comps** (e.g. any group containing
   `skeleton_captain_A`, `brigand_blood_A`, or another size-2 monster
   alongside others) — the following enemy would silently land on the
   size-2 enemy's second occupied position, an overlap that was invisible
   until this phase started sampling these very common comps at scale.
   Fixed via a running slot cursor advanced by each enemy's own `.size`
   (confirmed: no real comp's total size-units exceed the 4-slot board, so
   this never overflows). Test:
   `test_size2_enemy_combined_with_others_gets_non_overlapping_positions`.
4. **Real bug found and fixed**: `DarkestDungeonEnv`/`gymnasium.Env.step()`
   never set `truncated=True` — no episode length cap existed anywhere.
   Invisible with the old fixed easy matchup (episodes stayed short), but a
   randomly-sampled mismatched pairing (e.g. a low-damage comp vs a tanky
   enemy group) could run thousands of steps — confirmed directly, one
   early-training eval episode hit **3565 steps** (reward -74.5, almost
   exactly `3565 * TURN_PENALTY`). Fixed by wrapping the env in
   `gymnasium.wrappers.TimeLimit(max_episode_steps=150)` in both `train.py`
   and the eval script. **Not applied inside `DarkestDungeonEnv`/
   `ExpeditionEnv` themselves** — any other caller constructing these
   directly (a notebook, a future script) needs to apply the same wrapper
   itself; declared here rather than silently assumed.
5. **Trained**: `dd_combat_ppo_curriculum.zip` (historical name), 500k
   timesteps (~8:44 wall clock, ~950-1200 fps on CPU). Saved separately from
   the original `dd_combat_ppo.zip` (fixed-matchup model) — not overwritten,
   since they're not comparable (different training distributions). This
   checkpoint and its logs were deleted once superseded by the v2 retrain
   below (unpaired hero levels made it not directly useful going forward).
6. **Evaluation** (100 episodes × 5 sampled real comps × 5 difficulty bands
   = 2500 episodes, deterministic policy) — real, verified findings, not a
   success story to spin:
   - **Difficulty bands 1-5 are confirmed to be real Apprentice/Apprentice/
     Veteran/Veteran/Champion monster-tier progression** (verified directly:
     mean enemy HP in crypts climbs 11.4 → 13.3 → 17.6 → 18.6 → 26.1 across
     bands 1-5, and `EnemyDefinition.variant` confirms the tier boundary
     exactly). This is real fidelity, not an artifact.
   - **Win rate collapses hard at band 3** (Veteran) — 0-1% across all 5
     sampled comps, down from 15-68% at bands 1-2 (Apprentice) — and stays
     near 0% through band 5 (Champion), with a small, non-monotonic bump at
     band 4 (4-14%). The non-monotonic bump suggests this isn't a pure
     power-curve effect — specific enemy type matchups likely matter as
     much as raw stat scaling.
   - **Root cause, not a bug**: heroes are always loaded at max gear tier
     (no `hero_levels` passed anywhere in this phase's train/eval path) —
     there is no resolve-level-to-dungeon-tier gating in this simulator (a
     documented deliberate simplification, see "No resolve XP / level-up"
     below), so the trained policy faces the *entire* Apprentice→Champion
     monster power range with hero stats held constant, instead of the real
     game's paired progression (a Champion-geared party is meant to be sent
     into Champion-tier dungeons, not Apprentice ones and vice versa). **Now
     fixed** — see the expedition training v2 phase below.
   - **High timeout rates at the easier bands too** — e.g. "Holy Hymn:
     Bulwark" (a Crypts-designated comp) hit the 150-step cap on 69/100
     band-1 episodes and 44/100 band-2 episodes, dragging its win rate/mean
     reward down even where the matchup should be favorable. Suggests the
     policy sometimes fails to close out winnable fights within budget,
     not just that it loses decisively.
   - **Comp choice visibly matters, validating the original premise**:
     "Dark Ritual: Heist" (Cove) reached 47%/68% win rate at bands 1-2 vs.
     "Holy Hymn: Bulwark" (Ruins)'s 15%/18% — a >3x spread between comps at
     the same difficulty band, on the same training budget.
   - **500k timesteps is not enough to solve this**: generalizing across
     134 comps × ~1000 zone-matched enemy groups is a categorically harder
     problem than the original single fixed matchup, which converged to
     ~93% win rate in 100k timesteps (see reward-shaping phase notes).

Full suite after this phase: 155/155 passing (up from 154 — the one new
test is the size-2 position regression).

## Status after enemy comp system (real per-zone encounter tables)

After the training-readiness push, user asked for a data-mining pass: find
the real enemy compositions per dungeon zone in the game's own files and
generate a battery of enemy comps analogous to the hero comp system
(`COMPS/` CSV + `tests/fixtures/*.json`). Found `dungeons/<zone>/<zone>.<tier>.mash.darkest`
— the game's own weighted-random encounter tables (not something that
needed inventing or approximating): every hallway/room/boss/stall/named
battle the game can roll, for the 5 core zones, already parseable cleanly
by the existing `DarkestParser` with zero new parser work needed.

- **`data/enemy_comp_loader.py`** (`EnemyCompLoader.load_all(game_dir)`) parses
  `crypts`(Ruins)/`weald`/`warrens`/`cove` tiers 1-5 and `darkestdungeon`
  tier 6 (its only tier — the zone is entirely the 4 scripted story quests,
  confirmed by its mash file being 100% `named`/`boss`/fallback entries, no
  generic tiers 1-5 exist for it). New `EnemyCompEntry` dataclass
  (`data/types.py`): `zone_id`, `zone_name`, `tier`, `category`
  (`hall`/`room`/`boss`/`stall`/`named`), `chance` (the real relative
  selection weight), `enemy_ids` (file order = position 1..N), `name`
  (set only for `named` entries). Wired into `DataLoader.load_all` as
  `store.enemy_comps` — **1105 real entries** total (crypts 282, warrens
  287, weald 259, cove 234, darkestdungeon 43), verified by direct grep
  count against the source files. `EnemyCompLoader.to_enemy_defs(store, comp)`
  resolves a comp straight to `EnemyDefinition`s for `CombatEngine.reset()`.
- **One real data quirk found and fixed**: `warrens.2`/`warrens.4.mash.darkest`
  reference `carrion_eater_large_A` (8 occurrences) — but the monster's
  actual asset folder in this install is `monsters/carrion_eater_big/`, not
  `monsters/carrion_eater_large/` (which doesn't exist), and
  `carrion_eater_big_A`'s own `.info.darkest` has no alternate `.id`
  override to bridge the two names. A genuine legacy naming mismatch in the
  game's own shipped data (both clearly refer to the same monster line —
  "large" reads like an older internal name for what's now called "big"),
  not a parsing bug. Corrected via `ENEMY_ID_ALIASES` rather than left
  silently unresolvable. Verified: every one of the 1105 entries' enemy_ids
  now resolves against `store.enemies` with zero exceptions.
- **`ENEMY_COMPS/<zone>.json`** (repo root, peer to `COMPS/`) — a
  **generated snapshot** for browsing, produced by `python -m data.generate_enemy_comps`
  (must run as a module: running it as a script path shadows the stdlib
  `types` module with this repo's `data/types.py` and crashes on import).
  Same role CSVs play for hero data — reference index, not
  combat-authoritative; `store.enemy_comps` (parsed live from the game
  install on every `DataLoader.load_all()` call) is the real source of
  truth, and a test (`test_generated_json_battery_matches_live_data`)
  cross-checks the snapshot's comp counts against it so it can't silently
  go stale.
- **Deliberately excluded**: `.conditional.` mash files (rare,
  condition-gated special events — e.g. a 1-3% Shambler ambush, a Collector
  event gated on inventory value — would skew a "representative" battery)
  and DLC zones/encounters (Crimson Court's Courtyard, Color of Madness's
  Farmstead, DLC-added encounters layered onto the 4 base zones). Not a
  capability gap so much as a scope call matching what "per zone" means for
  the base game; revisit if DLC zone combat becomes a training target.

Tests: `tests/data/test_enemy_comps.py` (9 tests — full-id-resolution check
across all 1105 entries, spot-checks against literal source-file content,
the alias regression, an end-to-end `CombatEngine` run using a real
extracted comp, and the JSON-snapshot-vs-live-data consistency check). Full
suite: 154/154 passing (up from 145).

## Status after monster-AI completion phase (friendly targeting, remaining desire types, bonus_initiative_desires, rounding-drift fix)

User asked to close out essentially every remaining documented gap before a
real PPO training run. This phase is the largest single batch of fixes in
the project's history — full verification notes below; short version: two
real, previously-unknown bugs were found and fixed (monster `.heal` field
never parsed at all; monster heal skills executed as attacks against heroes
instead of healing allies), then every remaining monster-AI desire type
(skill-selection and target-selection) was implemented, then
`bonus_initiative_desires` (the item deferred two phases ago pending a scope
decision) was implemented for real, then a small buff-reversal precision bug
was fixed. Two mechanics remain deliberately out of scope (The Hag's
hero-capture "captor" mechanic, and the Ancestor's phase-transition `death`
trigger) — both require building a new game subsystem this simulator has no
other support for, not just wiring existing data, and were confirmed with
the user before being deferred. Full suite: 145/145 passing (up from 128).

### 1. Two real bugs found while scoping "monsters never target/heal their own side"

- **Monster `.heal N M` field was never read.** `_build_enemy` (`data/hero_loader.py`)
  only ever consulted `.dmg` — verified via Collector Shaman's `head_heal`
  (`.dmg 0 0 ... .heal 6 12`) and Cyst's `beholder_healself`
  (`.dmg   ... .heal 10 20`, blank `.dmg`): both parsed to `heal_min=heal_max=None`
  every time, because `heal_min`/`heal_max` were being *overloaded* with the
  `.dmg`-derived attack range (a pre-existing, documented data-gap: "Monster
  damage ranges... parsed as heal_min/heal_max"). Fixed by adding real
  `.heal` parsing (mirroring the hero path exactly) and moving the
  `.dmg`-derived range to new `SkillLevelData.atk_dmg_min`/`atk_dmg_max`
  fields instead, so a skill can now have a genuine heal range and an attack
  range as two independent, non-colliding fields.
- **Monster heal/buff skills always executed as attacks against a hero.**
  `_execute_enemy_turn` had no concept of "this skill targets an ally" at
  all — every monster action always picked a hero target and called
  `resolve_attack`. Even with `.heal` now parsed, a heal skill would have
  gone through `resolve_attack` against a random hero (harmlessly dealing
  the skill's near-zero `.dmg` "damage", since `.atk` is `0%` on every real
  heal/buff monster skill) instead of ever healing an ally. Both bugs had to
  be fixed together — fixing one without the other would have been inert or
  produced worse behavior (a "fixed" heal_min with no execution path for it
  would just leave `atk_dmg_min` at `None`, silently keeping the enemy's
  stale previous attack-damage range instead).

### 2. Friendly-targeting subsystem — monsters now genuinely heal/buff their own side

- `SkillLevelData.targets_allies` (the `@`-in-`.target` signal, previously
  only computed for hero skills) is now computed for monster skills too —
  verified real: Collector Shaman's `head_heal`/`head_buff`
  (`.target @1234`), Cultist Orgiastic's `orgiastic_heal`/`orgiastic_guard`,
  Totem Guard's `totem_bolster`, Fuseman's `light_fuse`.
- `TargetDesire.is_friendly_target_desire` (previously dropped at load
  time — the old code had a `continue` for any target desire where
  `is_enemy_target_desire` was `False`) now drives which candidate pool
  `_select_enemy_target` scores against: heroes (unchanged default) or the
  performer's own living side (new).
- A monster skill with a completely empty `.target` (not even `@` — e.g.
  Cyst's `beholder_healself`) is a self-only action, the monster-side
  equivalent of a hero's empty-`.target` self-action — resolved directly
  against the performer, no target selection at all.
- Verified end-to-end against real data: a wounded Collector (ally enemy)
  actually receives HP from a Collector Shaman's `head_heal`; a self-only
  Cyst heals itself via `beholder_healself`. Tests: `tests/engine/test_monster_ai_friendly.py`.

### 3. Every remaining skill-selection desire type implemented (or confirmed data-equivalent to an already-modeled type)

All verified against the real `data` payload for every occurrence in
`base.monster_brains.json`, not guessed:

- **`performing_turn_skill`** (53 real occurrences — the most common
  unmodeled type by far) — gates a skill by which 1-indexed action slot
  (`performing_turn`) this is within a multi-action monster's own turn;
  matches the existing `action_idx` loop in `_execute_enemy_turn` exactly.
- **`stun_skill`** (29) — gates by `non_stunned_heroes_min`/
  `marked_heroes_min`/`marked_heroes_max` computed from the live hero party
  (e.g. Spider Webber's `spider_web` needs ≥1 non-stunned hero to be worth
  casting). Verified the weight shift is real: `spider_web`'s selection
  frequency drops from ~87.5% to ~51.5% (falls back to the flat
  `random_skill` baseline only) once every hero is already stunned.
- **`ally_alive_skill` / `ally_dead_skill`** (17 / 7) — gates by whether a
  living ally enemy of a given base class (`ally_base_class_id`, matched via
  a new `_base_class_id()` helper that strips a definition id's trailing
  `_<single letter>` variant suffix — e.g. `swine_piglet_A/_B/_C` ->
  `swine_piglet`) is alive/dead. Verified with Swine Prince: `obliterate_enraged`
  (weight 1000, `ally_dead_skill` on `swine_piglet`) is **completely
  unreachable** while a piglet ally is alive, and **dominates** selection
  the moment one dies — the real rage-on-ally-death mechanic, previously not
  modeled at all.
- **`heal_skill`** (10) — gates by whether any living ally (including self)
  is below `hp_ratio_threshold`, plus optional `monsters_size_min`/`_max`
  (current enemy party size) and `first_initiative_only` (only the first
  enemy to act each round bothers checking — new `_first_enemy_this_round`,
  backed by `_first_enemy_acted_this_round`, reset each round). Boosts
  `combat_skill_id` if the desire names one; otherwise spreads across
  whichever of the monster's own usable skills are real heals
  (`heal_min is not None`) — matches how the "default" fallback brain (used
  by many basic monsters) is actually structured in real data (no
  `combat_skill_id`, relies on the monster's own skill list).
- **`effect_key_status_skill`** (6) — gates by whether any hero currently
  carries the given key status (only `"tagged"`/marked appears in real
  data, mapped to `CombatantState.is_marked`). When no `combat_skill_id` is
  given, spreads across all usable skills — verified this is exactly what's
  needed: Fungal Bloat's `kill_marked_target_fungal_bloat` brain has **no**
  skill-selection desire naming `fierce_swipe` at all (only this
  `effect_key_status_skill` entry, no id), yet `fierce_swipe` is clearly
  meant to be used once something is marked (its own effect is literally
  "Damage Marked Target Lesser", and a `marked_target` *target*-desire
  singles it out). Without this, `fierce_swipe` was **completely
  unreachable** regardless of mark state — confirmed by direct selection
  sampling before/after the fix.
- **`preferred_skill`** — treated identically to `random_skill`. Verified
  data-equivalent: every real brain that has `preferred_skill` (the
  `default`/`default_A/B/C` fallback brains) has it sitting right next to a
  `random_skill` entry with the exact same shape (flat `base_chance`, no
  `combat_skill_id`) — no per-skill "preferred" flag exists anywhere in any
  monster's `.info.darkest` file (checked directly), so there's nothing to
  distinguish it by.
- **Not modeled**: `fill_ally_captor_empty_skill` — see "Deliberate
  simplifications" below.

### 4. Every remaining target-selection desire type implemented (or confirmed data-equivalent)

- **`rank_target`** (4) — treated identically to `random_target`. Verified
  data-equivalent: all 4 real occurrences (Prophet ×3, Sapper) carry zero
  rank-specific fields — only `base_chance`/`specific_combat_skill_id`/
  exclusivity/friendly-or-enemy — so there's no data-derived "which rank" to
  weight toward.
- **`ally_class_target`** (13) — filters/weights the candidate pool (hero or
  friendly, per `is_friendly_target_desire`) to a specific base class via
  `ally_base_class_id`, same suffix-stripping convention as
  `ally_alive_skill`. 12 of 13 real occurrences are friendly (Collector
  Shaman prioritizing Collector/Collector Battle, Cultist Orgiastic
  prioritizing Cultist Shrouded, Totem Guard prioritizing Shuffler); 1 is
  enemy-side (Drowned Pirate prioritizing the Jester hero specifically).
  Verified: Collector Shaman's `head_buff` target weighting favors a
  same-class Collector ally over an unrelated Cultist Brawler ally in the
  same pool, ~500-seed sampled.
- **Not modeled**: `fill_ally_captor_empty_target` (see "Deliberate
  simplifications"), `next_initiative_target` — confirmed via `DataLoader`
  that **zero real enemies** reference the one brain (literally named
  `"test"`) that uses it; unreachable, not worth modeling.

### 5. `bonus_initiative_desires` implemented (supersedes the "deferred" note from the previous phase)

Corrected description confirmed real (not initiative-roll modifiers — see
below): a scripted bonus-action/skill-override system for 14/165 brains,
all named/boss-tier (`prophet_A/B/C`, `swine_prince`, `cannon_A/B/C`,
`drowned_captain`, `ancestor_small_D`/`ancestor_big_D`/`ancestor_heart_D`,
`brigand_sapper_D`, `hateful_virago`, `skeleton_bearer`). Every real entry
fires at exactly one of two round-timing moments —
`is_round_start`+`is_pre_turn` (before normal turn order for the round) or
`is_round_finish`+`is_post_turn` (after it; `is_round_in_progress` always
co-occurs with this pair in real data, so it's folded in rather than
tracked continuously) — wired into `reset()`/`_advance_to_next_hero_turn()`
(round-start) and `_end_of_round()` (round-finish). A shared
`_execute_enemy_skill()` helper was extracted from `_execute_enemy_turn`'s
per-action body so both the normal turn loop and bonus actions run the
exact same self/friendly/enemy-targeting execution path.

4 of 6 real trigger types modeled, covering 22 of 25 real entries:
- **`guaranteed`** (10) — always fires, subject only to party-size gates.
  Verified: Prophet's `prophet_mark` fires every round-start, `prophet_attack`
  every round-finish, entirely outside the normal turn order.
- **`ally_actor_class_count`** (7) — gates by counting living allies of a
  base class within `[ally_count_min, ally_count_max]` (e.g. Drowned
  Captain's `all_hands`, Sapper's `sapper_throw`/`sapper_detonate` gated on
  `brigand_barrel` count, Hateful Virago/Skeleton Bearer's revival skills
  gated on `corpse`/`corpse_large` count).
- **`hp_ratio_threshold`** (2) — gates by the performer's own HP fraction
  vs `health_ratio_threshold` (`is_under_threshold` for direction), plus
  `heroes_min`/`heroes_max` (current living hero party size). Verified:
  Ancestor Heart's `kill_pill` (desperate attack at ≤67%/≤33% HP) is
  ineligible at full HP and eligible once HP drops under threshold.
  Direction confirmed by test at both HP levels.
- **`last_skill`** (3) — gates by whether `last_combat_skill_id` was the
  most recently used skill by *anyone* this combat, fed by
  `CombatEngine._last_skill_used`, updated in the already-existing
  `_track_skill_use` (called for every hero and enemy skill use — no new
  instrumentation needed). Verified: Cannon's `last_skill` desire (no
  `combat_skill_id_override`, falls back to normal skill selection) is only
  eligible immediately after Fuseman's `light_fuse` was used.
- **Not modeled**: `death` and `ally_last_damaged` — see "Deliberate
  simplifications" below.

Tests: `tests/engine/test_bonus_initiative.py`.

### 6. `combat_stat_multiply` buff-reversal rounding drift fixed

Previously documented as a low-priority known imprecision: reversing a
duration-tracked `combat_stat_multiply` buff (e.g. a -25% damage debuff) on
an `int`-typed stat (`max_hp`/`damage_low`/`damage_high`) divided back
through the same multiplier, which isn't guaranteed to return the exact
original integer (confirmed: `int(3*0.75)=2`, then `int(2/0.75)=2`, not
`3` — a real, reproducible 1-point drift). Fixed by having
`_apply_buff_to_stats` return the exact integer delta it applied, storing it
on the new `ActiveBuff.applied_delta` field at the two call sites that track
duration-based buffs (`_resolve_effect`'s inline-stat-field block and
`.buff_ids` block), and having `_reverse_buff_from_stats` subtract that
exact recorded delta instead of re-deriving it — falls back to the old
division-based approach only where no delta was recorded (death's door
debuff reversal via `_reverse_deaths_door_buffs`, which isn't
`ActiveBuff`-tracked at all). Tests:
`test_combat_stat_multiply_reversal_is_exact_not_drifted` (reproduces the
old drift directly, then proves the new path is exact),
`test_buff_ids_with_duration_stores_and_uses_exact_delta_on_expiry`
(end-to-end through the real effect-resolution path).

## Status after heal-modifier / bonus_initiative_desires investigation phase

**Note on item 2 below**: `bonus_initiative_desires` was deferred here
pending a scope decision — it has since been implemented for real, see the
phase above (section 5). This section is kept as the historical record of
when that deferral decision was made and why.

1. **Real bug found: `heal_received_percent` never matched any real key.**
   `_apply_buff_to_stats` had a `pass` for a stat_type string
   (`heal_received_percent`) that appears in **zero** files across the
   entire game install (verified with a full-install grep) — dead code that
   could never have fired. The real key, verified in
   `shared/buffs/base.buffs.json`, is `hp_heal_received_percent` (54 buffs,
   e.g. `HPRECOVERY10` = +10%), and it has 3 siblings that were also
   entirely unimplemented: `hp_heal_percent` (51 buffs, performer/produced
   side, e.g. `on_crit_healbuff` = +25% healing done on crit),
   `stress_heal_received_percent` (32 buffs), and `stress_heal_percent` (3
   buffs, e.g. `on_crit_stresshealbuff` = +40%). All 4 are now real
   `CombatantState` fields (`hp_heal_received_mult`, `hp_heal_produced_mult`,
   `stress_heal_received_mult`, `stress_heal_produced_mult`), wired through
   `_apply_buff_to_stats`/`_reverse_buff_from_stats`, and consulted in
   `_apply_heal` (both multipliers stack multiplicatively on the final
   applied amount) and the `eff.stress_heal` branch of `_resolve_effect`.
   `_apply_heal` now returns the post-multiplier amount so log lines report
   what was actually applied, not the pre-buff roll. Tests:
   `test_hp_heal_received_and_produced_percent_scale_applied_amount`,
   `test_stress_heal_received_and_produced_percent_scale_relief`.
2. **`bonus_initiative_desires` investigated, description corrected, implementation deferred.**
   The existing doc claimed this was "initiative roll modifiers" — never
   actually verified against the real JSON. It's not: it's a scripted
   bonus-action/skill-override system for 14/165 monster brains, all
   named/boss-tier. See "Remaining gaps" below for the full corrected
   description. User decision after the real scope became clear: fix the
   documentation, leave implementation deferred (boss-only, comparable
   effort to the still-deferred "remaining monster AI desire types" gap).

Full suite: 128/128 passing after this phase (up from 126).

## Status after corpse-clearing / guard-duration / skill-cooldown / excess-crit / consecutive-miss / stress-received phase

Closed six smaller documented gaps left over from the AoE/ally-targeting and
Abomination phases above. All verified against real game data, all covered
by new tests in `tests/engine/test_gaps.py`, `tests/engine/test_monster_ai.py`, and
`tests/engine/test_resolution.py`.

1. **Corpse clearing.** `EffectDefinition.is_clear_corpses` (from
   `.kill_enemy_types corpse`) now actually removes corpses in the effect's
   target ranks via `_resolve_effect`. Size-2 corpses keep both occupied
   positions cleared together (distinct from size-2 *living* combatants,
   which already worked). Tests:
   `test_clear_corpses_removes_corpse_in_target_ranks`,
   `test_size2_corpse_keeps_occupied_positions`.
2. **Guard duration auto-expiry.** `CombatantState.guard_rounds_remaining`
   is set when a Guard effect (e.g. MAA's `MAA Guard 1`, `.duration 3`) is
   applied, and `_tick_guard_durations()` (hooked into `_end_of_round`)
   clears `guard_target_id`/`guard_source_id` on both sides once it reaches
   0 — previously Guard persisted forever unless explicitly cleared by a
   ClearGuard effect. Test: `test_guard_expires_after_real_duration`.
3. **Monster skill cooldowns.** `MonsterBrain.skill_cooldowns` (parsed from
   `base.monster_brains.json`'s per-brain `skill_cooldowns` list, e.g.
   `giant_B`: `confusion_spores`/`poison_spores` both on a 2-round cooldown)
   is now enforced — `_select_enemy_skill` filters out any skill with
   `enemy.skill_cooldowns.get(sk.id, 0) > 0`, cooldowns are set after use,
   and `_tick_enemy_skill_cooldowns()` counts them down each round. Test:
   `test_monster_skill_cooldown_enforced_and_ticks_down`.
4. **`stress_dmg_received_percent` is no longer a no-op.** New
   `CombatantState.stress_dmg_received_mult` (default `1.0`, running
   multiplier) is now written/reversed by `_apply_buff_to_stats`/
   `_reverse_buff_from_stats` and consulted in `_apply_stress`. This is one
   of the 5 real death's-door debuffs (`deathsdoorSRDebuff`, +33% stress
   received) and also backs monster effects like cultist_brawler_A's
   `Brawler Rend Debuff 1` → `BRAWLER_STRESSDMG_D1` (+10% stress received,
   3 rounds) — previously "applied" but silently inert either way. Found via
   this fix: `tests/engine/test_torchlight.py::test_stress_amount_and_chance_scale_with_torch`
   started failing because a fresh engine at `torch_level=-1` can let the
   enemy win initiative and land `Brawler Rend Debuff 1` on the hero before
   the test's own assertions run — the test now explicitly normalizes
   `stress_dmg_received_mult` back to `1.0` first, since it's isolating
   torch-only scaling, not monster-debuff interaction (the interaction
   itself is correct, newly-real behavior). The healing-side siblings
   (`hp_heal_received_percent` etc.) were resolved in a later phase — see
   "Status after heal-modifier" notes above.
   Test: `test_stress_dmg_received_percent_now_applies`.
5. **Consecutive-miss ACC buff.** `rules.consecutive_miss_reduction_acc_buff`
   (0.04, i.e. +4% ACC per prior consecutive miss by the same attacker) is
   now wired into `compute_hit_chance`/`resolve_attack` via a new
   `acc_bonus` parameter, fed from `CombatEngine.consecutive_misses` (which
   already existed and incremented on miss, but was never consulted and
   never reset on hit — both fixed). Applies symmetrically to heroes and
   enemies (`consecutive_misses` is keyed by `CombatantState.id`, and hero/
   enemy ids never collide — enemies are offset by `100 + i`). Tests:
   `test_consecutive_miss_acc_bonus_raises_hit_chance` (resolution.py unit
   test), `test_consecutive_miss_resets_on_hit_and_boosts_acc` (engine
   integration test).
6. **excessCritMax / excessCritMult.** New `compute_excess_crit_bonus()` in
   `engine/resolution.py`: crit chance stacked above 100% can't add more hit
   probability, so the excess converts to a flat bonus damage multiplier on
   every hit, scaled by `excessCritMult` and capped at `excessCritMax`
   (0.20). **Confirmed data-inert**: `excessCritMult` is `0.0` in every
   shipped `rules.json` this game ships (`shared/rules.json`,
   `modes/radiant`, `modes/new_game_plus`, and
   `dlc/580100_crimson_court/.../bloodmoon` all checked directly) — so
   against real game data this always evaluates to a `0.0` bonus regardless
   of how high crit chance stacks. Implemented for correctness/
   future-proofing (the formula itself isn't guessed — the field names and
   cap value come straight from `rules.json`), not because it changes any
   number today. Tests: `test_excess_crit_bonus_is_zero_against_real_game_rules`,
   `test_excess_crit_bonus_formula_capped_by_excess_crit_max` (exercises the
   formula with a hypothetical non-zero `excessCritMult` override, since no
   shipped data does).

Full suite: 126/126 passing after this phase (up from 121).

## Status after AoE / ally-targeting fix (most significant bug this project has found)

> **Later addendum, kept alongside rather than merged (2026-08-08).** Everything
> below remains accurate about *which* targets an AoE hits. A separate question —
> how the to-hit roll is drawn across those targets — went unexamined here and
> was wrong: the engine drew an independent `rng.random()` per target, while the
> real game rolls **once per skill use** and tests that single roll against each
> target's own hit chance. Confirmed from captured game debug output, 11/11
> multi-target attacks with zero counterexamples. Only the to-hit roll is shared;
> crit and damage stay per-target. This does not change any target's expected hit
> rate, only the correlation between them — a real AoE hits everything or misses
> everything, where independent rolls average out. Fixed via
> `CombatEngine._shared_hit_roll` + `resolve_attack(hit_roll=...)`, default `None`
> so single-target RNG is byte-identical. See `CHANGELOG.md` and
> `tests/engine/test_shared_hit_roll.py`. **This was the first gap found by the
> game-trace oracle rather than by reading docs or the wiki.**

Started as "fix enemy `is_aoe` hardcoded `False`" (a documented gap) and
uncovered two much bigger, previously-invisible bugs:

### 1. `is_aoe` was never consulted anywhere in the live engine

Fixing the hardcoded `False` in `hero_loader.py::_build_enemy` alone would
have done nothing — `is_aoe` was read only by vestigial free functions in
`engine/action.py` used solely by `tests/engine/test_position.py`, never by
`CombatEngine`. `get_valid_actions` generated one single-target `Action` per
candidate position regardless of `is_aoe`, and `_execute_action`/
`_execute_enemy_turn` only ever resolved against one target. **No AoE skill
— hero or monster — had ever hit more than one target.** Fixed:
- `get_valid_actions` now emits exactly one `Action` per AoE skill (it hits
  everyone in its target ranks regardless of which candidate you'd have
  picked) instead of one per position.
- New `_resolve_action_targets` (hero side) resolves against every living
  combatant in the skill's target ranks for AoE, re-checked at execution
  time (not the stale candidate list from when the action was generated).
  `_execute_action` loops per-target for accuracy/damage/effects/riposte;
  self-effects and stealth-break stay once-per-action, not multiplied per
  target.
- `_execute_enemy_turn` gained the equivalent branch. AoE enemy attacks now
  correctly hit stealthed heroes too (stealth protects against being
  singled out by single-target skills, not against an attack that covers
  the whole formation — previously declared as unmodeled, now correct
  behavior rather than a gap).
- Verified against real skills: Man-at-Arms' `bellow` hits all 3 enemies in
  one action; `ancestor_big_D`'s `ancestor_fuse_all` hits all 4 heroes.
  Tests: `tests/engine/test_aoe.py`.

### 2. Heal/support skills were resolving as if they targeted the enemy — likely the single most impactful bug found this session

`_is_ally_skill(skill_id)` decided ally-vs-enemy targeting from
`SkillType.value in ("ally/team", "self")`. **Those two enum values are
never actually assigned by the loader.** Verified: raw `.type` only ever
appears as `"melee"`/`"ranged"`/`"move"` across every hero file — heal
skills like `divine_grace` have **no `.type` field at all** and silently
default to `SkillType.MELEE`; support skills like Man-at-Arms' Guard
(`defender`) carry `.type "melee"` too despite targeting an ally. So
`_is_ally_skill` returned `False` for **every single heal and support skill
in the game**, meaning:
- `get_valid_actions` built the target pool from `enemy_positions` for heals
  — `divine_grace` either offered 0 actions (positions didn't line up) or
  offered actions whose `target_id` pointed at enemy positions.
  `_execute_action` would then have resolved the heal against whatever
  enemy occupied that position.
- This was invisible in every prior test because all healing tests called
  `_resolve_heal`/`_apply_heal` directly, bypassing `_is_ally_skill`/target
  resolution entirely — including the reward-shaping tests, the DLC/guard/
  stealth verification, all of it. The bug only surfaces when a heal is
  driven through the real `get_valid_actions` → `submit_action` → `step()`
  pipeline, which is exactly how the Gym env (and therefore any RL training
  so far) drives it.

Fixed by adding `SkillLevelData.targets_allies: bool`, derived from the real
signal — `'@' in the raw .target notation` (verified consistent across
heals, Man-at-Arms' Guard, and Antiquarian's Protect Me!, none of which use
a `SkillType` this engine can distinguish) — and rewriting `_is_ally_skill`
to read it instead of `skill_type`. Verified end-to-end: a Vestal in a
2-hero party now correctly offers `divine_grace` targeted at her wounded
ally (not the enemies), and a scripted "heal when possible" playthrough
brought a 5-HP Crusader back to full 61 HP mid-combat. Tests:
`tests/engine/test_gaps.py::test_heal_and_support_skills_target_allies_not_enemies`.

**Implication for anything already trained**: any PPO checkpoint trained
before this fix learned against an environment where healing/Guard either
did nothing selectable or (worse) healed the enemy — that experience doesn't
transfer. `dd_combat_ppo.zip` gets overwritten by smoke tests regularly
anyway (see earlier phases), but flagging explicitly: **retrain from scratch
after this fix**, don't resume from an older checkpoint.

## Status after Abomination transform / inline stat-buff fix

Fixing "mode switching not triggered by transform skill" (a documented data
gap) cascaded into fixing a foundational engine gap affecting far more than
Abomination:

### Inline `combat_stat_buff` effects were completely inert

`_resolve_effect`'s `is_stat_buff` handling only ever appended a decorative
`ActiveBuff` marker (`buff_id=f"EFFECT_{eff.name}"`) with no corresponding
`store.buffs` entry — `_apply_buff_to_stats` was **never actually called**
for any inline stat field (`damage_low_multiply`, `attack_rating_add`,
`crit_chance_add`, `protection_rating_add`, `defense_rating_add`,
`speed_add`, `damage_low_add`/`damage_high_add`). This affects every skill
in the game using inline stat modifiers instead of `.buff_ids` references,
not just Abomination's transform buffs. Fixed by synthesizing a real
`BuffDefinition` per populated field (cached in `store.buffs` under a
deterministic `__inline__<effect_name>__<stat_sub_type>` id so repeat
applications reuse it) and applying/reversing it through the existing
`_apply_buff_to_stats`/`_reverse_buff_from_stats`/`ActiveBuff` machinery —
no new reversal mechanism needed, it plugs into what already exists.
`INLINE_STAT_FIELD_MAP` in `combat_engine.py` documents the field→
(stat_type, stat_sub_type) mapping.

### `.speed_add` never appears in the real files

Same class of bug as `speed`/`speed_rating` in `_apply_buff_to_stats`
(found in the death's-door-debuff phase): the real key is
`.speed_rating_add` (97 occurrences; bare `.speed_add` appears **0** times).
Fixed in `EffectLoader`. (`.max_hp_percent` — checked too, and genuinely
**does not appear anywhere** in `base.effects.darkest`, unlike the other
mismatches; left parsed-but-unverified with a comment rather than removed,
since it might be real in a DLC/mode effects file not checked.)

### `.valid_modes human beast` (multi-value) collapsed to one unsplit string

Same shape as `.human_effects`/`.beast_effects` below — unquoted,
space-separated bare words. The old parsing only handled a *single* mode
name correctly; `transform`'s two-mode declaration became the literal
one-element list `["human beast"]`, so `"human" in skill.valid_modes` was
`False` even for transform itself, and `_check_valid_mode` silently
rejected every attempt to use it. Fixed by reusing the same
space-splitting helper written for `human_effects`/`beast_effects`.

### Transform mechanism itself

`transform`'s `.human_effects`/`.beast_effects` (space-separated bare-word
effect lists, gated by the performer's *current* mode — a different
mechanism from the regular `.effect` list, which transform doesn't use at
all) are now parsed into `SkillLevelData.human_effects`/`.beast_effects` and
applied by `_resolve_self_action` based on `performer.mode`. The actual
mode flip comes from a new `EffectDefinition.set_mode` field — found by
grepping the exact effect names referenced
(`switch_mode_beast_self`/`switch_mode_human_self` → `.set_mode
beast`/`.set_mode human`), applied in `_resolve_effect`.

Verified end-to-end across real rounds (not just one call — `transform` has
a real `per_turn_limit=1` that would mask a broken second call as "did
nothing" if tested naively): mode flips human→beast→human across separate
rounds, `beast_buff_5`'s `+5 speed_rating_add` actually raises `.speed`
(9→14) and correctly reverses when its real 4-round duration expires
(back to 9), `xform_damage_5`'s damage multipliers apply and expire on
their own separate duration. Tests: `tests/engine/test_transform.py`.

## Status after Resolve Level (hero_levels) phase

`CombatEngine.reset(..., hero_levels: Optional[list[int]] = None)` adds
per-hero Resolve Level (1-6), for building Apprentice/Veteran/Champion-
appropriate parties instead of everyone always at max gear/skill tier.
Exposed via `env.reset(options={"hero_levels": [...]})`.

**Important finding before implementing**: Resolve Level and weapon/armour/
skill tier are *decoupled* progression systems in the real game — verified
against `campaign/roster/roster.variables.json` (resolve level XP
thresholds: `[0,2,8,14,24,36,48]`) and `campaign/quest/quest.restriction.json`
(`resolve_level_threshold_table: [2,2,3,4,5,99,99]`, gates *dungeon access*
by quest length, not gear). Neither file ties resolve level to equipment
tier — in the real game tier is bought independently at the Blacksmith/
Guild. So the mapping used here is a **deliberate simulator convention**,
chosen with the user, not a literal extracted game rule:

```
tier_idx (0-4) = clamp(hero_level - 2, 0, 4)
# level 1,2 -> Tier I (idx 0)   level 4 -> Tier III (idx 2)
# level 3   -> Tier II (idx 1) level 5 -> Tier IV (idx 3)
#                                level 6 -> Tier V (idx 4)
```

`tier_idx` selects both weapon/armour tier (`hd.weapon_tiers[tier_idx]`,
`hd.armour_tiers[tier_idx]`) and skill data tier (`get_skill_data(id,
level=hero.skill_tier)` — previously **every** call site hardcoded
`level=4`, so low-tier heroes were structurally impossible before this).
Verified `smite`'s ACC actually differs by tier (0.85 at tier 0 vs 1.05 at
tier 4) and that `get_valid_actions`/`_execute_action` both resolve through
the acting hero's own tier now.

Also implements the wiki-verified mechanic: **each Resolve Level above 1
adds +10% resist to stun/blight/bleed/move/debuff**
(darkestdungeon.wiki.gg/wiki/Resolve_Level). Interpreted as relative to the
level-1 baseline (`base_stats` already represents an unleveled hero), so
level 6 = +50% resist on top of base. Not implemented: the wiki's trap
disarm chance bonus — no trap mechanic exists in this combat-only simulator.

**Superseded by a later fix, kept for history**: the 20-stress/25%-stress-
damage-per-level underleveled-hero penalty noted above as unimplemented
("no dungeon difficulty/quest concept exists here to compare against") was
subsequently added as an opt-in `CombatEngine.reset(quest_difficulty_level=
None)` param — passing it gives exactly that comparison point. Default
`None` still means zero penalty, so this doesn't change any existing
caller's behavior. Deliberately not wired into either expedition env's
auto-sampling: `TIER_TO_HERO_LEVEL` already pairs party level exactly to
zone tier everywhere those envs build a party, so the shortfall this
mechanic charges for is currently unreachable through them by design — see
CHANGELOG.md's "round 3" wiki-driven fidelity fixes entry.

**Regression found and fixed while implementing**: the first version
defaulted unspecified hero levels to an implicit `level=6`, which — because
the resist bonus formula is derived from `level` — silently added +50%
resist to *every* default-composition combat (i.e. changed existing/default
behavior, not just new opt-in behavior). Fixed by decoupling "no
`hero_levels` given" (exact old behavior: max tier, zero resist bonus) from
"explicit `level=6` given" (max tier, *and* +50% resist bonus) — these are
different things and must stay different. Regression-tested explicitly
(`test_default_no_hero_levels_matches_old_max_tier_behavior`).

Enemies are unaffected by this — Apprentice/Veteran/Champion enemy variants
already exist as entirely separate loaded stat blocks (`cultist_brawler_A`/
`_B`/`_C`, already selectable by id), not a level parameter needing new
plumbing. Tests: `tests/env/test_hero_level.py`.

## Status after torchlight / monster AI / buff_ids / death's-door-debuff phase

All four items from the prior phase's "Recommended next steps" are now done.

### Torchlight

`rules.json`'s `darkness.range_table` (5 bands, `[75,inf)`/`[50,75)`/`[25,50)`/
`[0,25)`/`[-inf,0)`) loads into `CombatRules.darkness_table` (`DarknessBand`
in `data/types.py`). `CombatEngine.reset(..., torch_level: float = 100.0)`
picks the matching band once (`_get_darkness_band`, lower-inclusive/
upper-exclusive) and applies it as permanent stat deltas at reset
(`_apply_torch_modifiers`) — torch is fixed for the whole encounter in this
simulator (no mid-combat camping/consumables), so there's no need to
recompute per-attack. `env/gym_env.py::reset(options={"torch_level": N})`
exposes it; default 100.0 (brightest band) matches pre-existing behavior
exactly (regression-tested).

Percentage values in `rules.json` (e.g. `4.0` = +4%) are divided by 100 to
match the 0-1 fraction scale used everywhere else in this engine for
crit/dodge/atk_bonus (confirmed by reading `resolution.py`: `hit_chance =
acc - dodge`, both fractions). `monster_damage_increase` is treated as a
multiplicative scale on damage (consistent with `skill.dmg_mod`); the rest
are additive. **Real bug found while wiring damage**: `monster_damage_increase`
can't be baked into `enemy.dmg_min`/`dmg_max` once at reset — `_execute_enemy_turn`
re-derives those from the selected skill's `heal_min`/`heal_max` every turn
(monster damage ranges are parsed into those fields — see Data gaps), which
would silently overwrite a one-time modifier. Fixed by storing the
multiplier (`self._monster_dmg_mult`) and applying it at that point of use
instead. Stress scaling (`stress_chance_increase`/`stress_damage_increase`)
is applied inside `_apply_stress`. Not modeled: `loot_increase_gold`,
`loot_increase_treasure_draws`, `player_scouting_increase`,
`monsters_surprised_increase`, `heroes_surprised_increase` — dungeon
exploration/loot mechanics with no analog in this single-encounter combat
simulator. Tests: `tests/engine/test_torchlight.py`.

### Monster AI

`raid/ai/base.monster_brains.json` (165 brains) loads into
`store.monster_brains: dict[str, MonsterBrain]` (`data/monster_brain_loader.py`).
`_execute_enemy_turn` now does a weighted lottery over the enemy's *own*
skills (previously **always `ed.skills[0]`** — e.g. cultist_brawler_A's
second skill, `brawler_claw_weak`, was structurally unreachable regardless
of brain data) and over candidate hero targets, using `SeededRNG.weighted_choice`
(new method).

Only a subset of the real desire types are modeled — the format has 10
skill-selection desire types and 10 target-selection desire types, and
implementing all of them (some need concepts this simulator doesn't have,
e.g. `fill_ally_captor_empty_*` for Crimson Court capture mechanics) was out
of scope for this pass:
- Skill selection: `specific_skill` (weighted preference for a named skill)
  and `random_skill` (fallback weight split evenly across all usable
  skills). Not modeled: `performing_turn_skill`, `stun_skill`,
  `ally_alive_skill`, `heal_skill`, `ally_dead_skill`,
  `effect_key_status_skill`, `preferred_skill`, `fill_ally_captor_empty_skill`
  — these are conditional/contextual desires needing per-skill semantic
  tagging (e.g. "which skill counts as a stun skill") this engine doesn't
  derive from data.
- Target selection: `random_target`, `marked_target`, `health_target`,
  `stunned_target`, `stress_target`, `resistance_target` — all enemy-target
  only (`is_friendly_target_desire` entries are dropped at load time; this
  engine doesn't model monsters healing/buffing their own side at all). Not
  modeled: `ally_class_target`, `rank_target`, `fill_ally_captor_empty_target`,
  `next_initiative_target`.
- Not modeled at all (at the time this section was written — `skill_cooldowns`
  is now modeled, see the corpse-clearing/guard-duration/skill-cooldown phase
  notes above): `skill_cooldowns` (per-skill N-round cooldown, separate from
  `per_battle_limit`/`per_turn_limit`, which *are* still respected) and
  `bonus_initiative_desires`. The latter's name is misleading — **it is not
  an initiative-roll modifier** (corrected description; the original note
  here guessed wrong without checking the real JSON). Verified against
  `base.monster_brains.json`: it's a scripted bonus-action system — 14/165
  brains (all named/boss-tier: `prophet_A/B/C`, `swine_prince`, `cannon_A/B/C`,
  `drowned_captain`, `ancestor_small_D`/`ancestor_big_D`/`ancestor_heart_D`,
  `brigand_sapper_D`, `hateful_virago`, `skeleton_bearer`) get a guaranteed
  extra action at specific round-timing hooks (`is_round_start`/`is_pre_turn`/
  `is_post_turn`/`is_round_finish`), often overriding which skill they use
  (`combat_skill_id_override`) — e.g. Prophet always opens a round with
  `prophet_mark` and closes it with `prophet_attack` regardless of normal
  turn order. 6 distinct trigger types found (`guaranteed` ×10,
  `ally_actor_class_count` ×7, `last_skill` ×3, `death` ×2,
  `hp_ratio_threshold` ×2, `ally_last_damaged` ×1), each needing its own
  condition-evaluation logic — comparable in scope to the still-deferred
  "remaining monster AI desire types" gap above, not a small fix. Left
  unimplemented (user decision after scope was corrected): only affects
  14/165 brains, all boss-tier.

**Real bug found and fixed as a prerequisite**: `monster_brain_id` resolution
depended on parsing `monster_brain: .id cultist_brawler_A` — an *unquoted
bare-word identifier*. The parser's unquoted character class only ever
allowed `[@~0-9.,%\-+eE\s]`, no letters at all, so any unquoted value
containing non-digit letters (not just this field — verified ~168 occurrences
of unquoted `.id <name>` alone across monster files, plus capitalized
`True`/`False` literals like `.accelerate_stall_penalty False`, which the
parser's `true`/`false`-only lowercase check also missed) was silently
dropped, same failure class as the `@`-prefix and malformed-line bugs from
the guard/stealth/DLC phase. Before this fix, **245/246 enemies silently
fell back to `monster_brain_id = "default"`** regardless of their real
brain. Fixing the character class introduced its own regression (empty
values like a self-only skill's blank `.target` immediately followed by
`.is_crit_valid` could now swallow the next key's name as if it were the
value) — fixed with a negative lookahead (`(?!\.[a-zA-Z_])`) that refuses to
start a value match at what's clearly the next key. All 246 enemies now
resolve to a real brain; regression-tested.

**Also wired as part of this**: monster skill effects (bleed, mark, debuffs)
were *never* applied before — `_execute_enemy_turn` only ever called
`resolve_attack` for raw damage, never `_apply_skill_effects`. Real monster
skills do carry populated `.effects` (e.g. cultist_brawler_A's `brawler_claw`:
`Minor Bleed 1`, `SmallDmg Marked Target`, `Brawler Rend Debuff 1`) — now
applied the same way hero skill effects already were. Tests:
`tests/engine/test_monster_ai.py`.

### Generic `.buff_ids` resolution

`EffectDefinition` gained `buff_ids: list[str]` and a generic `duration:
Optional[int]` (previously duration was only tracked per-mechanic —
`bleed_rounds`, `stealth_duration`, `riposte_duration` — with no generic
slot). `_resolve_effect` resolves each id against `store.buffs` and applies
it via the existing `_apply_buff_to_stats`; if `duration` is set, tracks it
as an `ActiveBuff` for later reversal (permanent for the rest of combat,
same as trinkets/quirks, if `duration` is absent). Verified against real
effects: `Blight Debuff 1`→`blight_debuff_1` (single bare id), `Fortify
Resists 1`→`BLEEDRESIST10`+`BLIGHTRESIST10` (multi-id with `duration=3`,
confirmed it reverses cleanly on expiry), `Command 1`(MAA)→4 ids,
`StealthedCrit`→`stealthed_crit_chance_buff` (still resolves to a no-op —
that specific buff id genuinely doesn't exist in `base.buffs.json`, a data
gap not a code gap, see prior phase's notes).

**Real bug found while generalizing reversal**: `_tick_buff_durations` only
ever reversed expiring buffs with `stat_type=="resistance", stat_sub_type=="stun"`
— a hardcoded special case. Any other duration-tracked buff (including the
new generic `.buff_ids` ones) would apply via `_apply_buff_to_stats` but
never get undone when its `ActiveBuff` expired. Generalized via new
`_reverse_buff_from_stats` (exact inverse of `_apply_buff_to_stats`) called
for any buff id that resolves in `store.buffs`. Note: `combat_stat_multiply`
reversal (divide back through the same multiplier) can drift by integer
rounding across repeated apply/reverse cycles on `int`-typed stats
(`dmg_min`/`dmg_max`/`max_hp`) — declared rather than assumed exact;
`combat_stat_add` and `resistance` reversals are exact.

### Death's door debuff reversal

`_apply_deaths_door_buffs` (5 debuffs: ACC, dmg low/high, speed, stress
received) used to be permanent for the rest of combat even after the hero
recovered. New `_reverse_deaths_door_buffs`, called from `_apply_heal` at
the same point `is_deaths_door` clears, re-looks-up the hero's
`deaths_door_buffs` and undoes each one via `_reverse_buff_from_stats`.
**Real bug found while wiring this**: the speed debuff
(`deathsdoorSPDDebuff`) uses `stat_sub_type == "speed_rating"` in the real
buff data, but `_apply_buff_to_stats`'s `combat_stat_add` branch only ever
matched `"speed"` — the speed penalty had **never actually applied**,
regardless of this phase's reversal work. Fixed both `_apply_buff_to_stats`
and its new inverse to accept either key. `stress_dmg_received_percent`
(the 5th debuff) is still a documented no-op — see Remaining gaps. Tests:
`tests/engine/test_gaps.py::test_deaths_door_debuffs_reverse_on_heal`.

## Status after turn-economy / death's-door / DoT fixes

The three items declared-but-deferred at the end of the reward-shaping phase
are now fixed:

- **Turn economy.** `CombatEngine` now tracks whose turn it actually is via
  `current_turn_index` into `self.turn_order` (the field existed before but
  was dead code — set on reset, never read or advanced). New method
  `_advance_to_next_hero_turn()` walks the initiative queue one entry at a
  time — resolving enemy actions, stun-recovery skips, and dead/corpse skips
  automatically — and pauses exactly when it reaches a living hero's turn.
  `get_current_actor_id()` exposes whose turn it is; callers (the Gym
  wrapper, tests) must query this instead of picking a hero on their own.
  `_end_of_round()` (buff/riposte/stealth duration ticks, mode stress, stall
  check, `self.round += 1`, win/loss check) now runs once per full initiative
  cycle instead of once per hero action — this was silently ticking down
  every duration-based mechanic ~2-4x too fast in multi-hero parties before.
  `reset()` now calls `_advance_to_next_hero_turn()` immediately, so a
  faster-than-the-party enemy can act before the first hero decision, same
  as the real game. `env/gym_env.py` was updated to call
  `engine.get_current_actor_id()` instead of its own independent
  `_current_hero_idx` round-robin (removed entirely, along with
  `_get_current_hero`/`_advance_hero`) — this was the other half of the bug:
  even after fixing the engine, the old Gym wrapper would still have
  requested actions for the wrong hero out of turn order. Verified via
  `tests/engine/test_combat.py::test_turn_order_is_engine_owned_and_alternates_heroes`
  and `test_round_does_not_increment_per_hero_action`. Side effect visible in
  training: mean episode length in a smoke run went from ~24-26 steps to
  ~44-45 steps after the fix — both heroes now actually get turns and
  enemies no longer act ~4x too often in a 2-hero party.
- **Sticky `is_deaths_door` flag.** New `_apply_heal()` helper (used by both
  `_resolve_heal` and `_resolve_self_action`'s heal branch) clears
  `is_deaths_door` once healed HP rises above 0. Does **not** reverse the
  stat debuffs from `_apply_deaths_door_buffs` — those are direct,
  permanent stat mutations with no tracking for reversal, a separate and
  deeper gap not addressed here. Verified in
  `tests/engine/test_gaps.py::test_heal_clears_sticky_deaths_door_flag` (and a
  companion test confirming a heal that doesn't restore HP above 0 correctly
  leaves the flag set).
- **Hero DoTs never ticking.** Fell out of the turn-economy fix directly:
  `step()` now calls `_tick_dots`/`_check_deaths_door_dot` on the acting
  hero right after their action resolves (previously only enemies, via the
  now-removed branch, ever got ticked). Verified in
  `tests/engine/test_gaps.py::test_hero_dots_tick_on_their_own_turn`.

None of this invalidates the reward-shaping design from the previous phase —
`Φ` is computed from whatever state exists after `engine.step()` regardless
of the turn-economy details, and all of `tests/env/test_reward.py` still passes
unmodified. Re-ran the `train.py --check` / short-training smoke test after
these fixes too; still completes cleanly end-to-end.

## Status after guard/stealth/healing/DLC phase

### Resolved gaps (14/14 original core mechanics, across multiple phases)

The original "14/14" count in an early revision of this document was
self-contradictory — rows 13 and 14 of its own table had `Status = "Not yet
integrated"` / `"Data loaded"`, not `"Implemented"`; a later revision
correctly demoted the count to 12/14 and deferred both to a future phase.
That later phase (see "Torchlight"/"Monster AI" sections above) has now
happened, so 14/14 is accurate again — this time because the table's own
per-row status says so, not because the header asserts it.

| # | Mechanic | Status | Notes |
|---|---|---|---|
| 1 | Mark synergy | Implemented | `_compute_mark_damage_mult` checks effect `keyStatus == "tagged"` and applies `damage_low_multiply` / `damage_high_multiply` to damage. |
| 2 | Buff application from effects | Implemented | `_apply_skill_effects` and `_apply_self_effects` resolve named effects from `base.effects.darkest`. Supports bleed, blight, stun, push, pull, mark, stress, kill, and stat-buff effects. |
| 3 | Healing resolution | **Implemented and test-verified**, was previously mismarked | Two real bugs found and fixed this phase (see "Bugs found and fixed" below): `hero_loader.py` hardcoded `heal_min=None, heal_max=None` for every hero skill regardless of the game file's `.heal N M`, and the parser dropped any `.effect "A" "B"` field with more than one quoted value outright. Verified against Crusader (Battle Heal, Inspiring Cry's stress-heal), Vestal (Divine Grace, God's Comfort), Occultist (Wyrd Reconstruction), Plague Doctor (Battlefield Medicine's HP heal + bleed/blight cure). |
| 4 | Push/Pull/Shuffle | Implemented | `_resolve_effect` handles push/pull with boundary clamping (0-3 for size-1, 0-2 for size-2). |
| 5 | Trinket/quirk wiring | Implemented | `apply_trinkets_and_quirks` resolves buff references and applies `combat_stat_multiply`, `combat_stat_add`, and `resistance` stat types to hero stats. |
| 6 | Per-battle/per-turn skill limits | Implemented | `_check_skill_limits` enforces `per_battle_limit` and `per_turn_limit`. Validated with Abomination transform (limit=2). Does **not** cover mode switching itself — see Remaining gaps. |
| 7 | Affliction/virtue stat effects | **Implemented from real data** (superseded twice — this row described the first version) | Now `data/trait_loader.py` → each trait's own `buff_ids` from `shared/trait/trait_library.json`. Previously generic `AFFLICTION_DEFAULT`/`VIRTUE_DEFAULT` ids (no effect at all), then `base.buffs.json`'s `afflictionXBuffN` ids, which no trait actually references. |
| 8 | Death's door buffs | Implemented | `_apply_deaths_door_buffs` resolves `deaths_door_buffs` from the hero definition. |
| 9 | Stun resist recovery fix | Implemented | `_apply_stun_recovery` adds +0.5 stun resist for 2 rounds. |
| 10 | Multi-action enemies | Implemented | `_execute_enemy_turn` loops `ed.actions_per_round` times. |
| 11 | Size-2 enemies | Implemented (live combatants only) | `occupied_positions` and push/pull clamping respect `size`. Corpse-specific size-2 handling still not implemented — see Remaining gaps. |
| 12 | Stall stress fix | Partial | Stall counter and stress application exist; `is_stall_invalidating` is parsed but still not checked in stall detection. |
| 13 | Torchlight | **Implemented** (later phase) | See "Torchlight" section above — darkness tables now applied to stress, ACC, dodge, monster damage. |
| 14 | Monster AI real | **Implemented (partial)** (later phase) | See "Monster AI" section above — real skill/target selection from `base.monster_brains.json`, subset of desire types. |

### Guard / Riposte / Stealth — new this phase

All three were previously unimplemented despite `CombatantState` already having
the necessary fields (`guard_target_id`, `guard_source_id`, `active_riposte`,
`is_stealthed`) — `EffectLoader` simply never parsed the relevant `.darkest`
keys, so nothing ever set them.

- **Guard**: `EffectDefinition` gained `is_guard`, `is_clear_guarding`,
  `is_clear_guarded`, `swap_source_and_target`. `_resolve_effect` sets
  `guard_target_id`/`guard_source_id` on the guardian/guarded pair;
  `swap_source_and_target` (used by Antiquarian's Protect Me!) inverts which
  side becomes the guardian — verified against both Man-at-Arms' Guard
  (`defender` skill → `MAA Guard 1`) and Protect Me! (`protect_me` →
  `Antiq ProtectMe Guard`), which point in opposite directions.
  `_resolve_guard_redirect` redirects an incoming attack from a guarded
  combatant to its guardian; wired into both hero attacks and enemy attacks.
  **Guard duration is not auto-expired** — real Guard effects carry a
  `.duration` (2-3 rounds) but this engine treats guard as persistent until
  explicitly cleared by another effect (Clear Guard/ClearGuards effects),
  since guard state isn't tracked through the existing `ActiveBuff`
  duration-tick system. Declared here rather than silently approximated.
  **Superseded by later fixes, kept for history**: Guard duration auto-
  expiry (`_tick_guard_durations`) was added subsequently — see "Torchlight,
  monster AI, and generic buff resolution" in `CHANGELOG.md`. Two more real
  gaps fixed in the wiki-driven pass: AoE attacks used to redirect onto a
  guardian same as single-target attacks (wiki: "AoE bypasses guard" — now
  gated via `_resolve_guard_redirect(target, is_aoe=...)`), and stunning a
  guardian never broke their guard (wiki: stunning the guardian "immediately"
  clears it from the protected unit — now `_break_guard_if_guarding`, called
  wherever a stun lands). See CHANGELOG.md's "round 3" entry.
- **Riposte**: `EffectDefinition` gained `is_riposte`,
  `riposte_on_hit_chance_add`, `riposte_on_miss_chance_add`,
  `riposte_effect`, `riposte_duration`, `is_clear_riposte`. Also fixed:
  `damage_low_multiply`/`damage_high_multiply`/`crit_chance_add` were
  previously only parsed when a `.combat_stat_buff` flag was present, but
  "MAA Riposte 1"–5 and "Hwy Riposte 1"–5 carry those fields without that
  flag — now parsed unconditionally. `RiposteState` (already existed on
  `CombatantState`) gained the chance/effect fields it was missing.
  `_maybe_trigger_riposte` counter-attacks using either the referenced
  `riposte_effect` (used by generic/enemy ripostes) or the riposte's own
  damage-multiplier/crit profile (used by MAA/Highwayman). Verified with
  Man-at-Arms' Retribution (`retribution` → `MAA Riposte 1`).
- **Stealth**: `EffectDefinition` gained `stealth_duration`, `is_unstealth`.
  Found and fixed a second, unrelated bug while wiring this in:
  **self-targeted skills (empty `.target`) never processed their `.effect`
  list at all** — `_resolve_self_action` only ever called
  `_apply_self_effects`, which reads `SkillLevelData.self_effects`, a field
  `hero_loader.py` always hardcodes to `[]` (the game format has no separate
  "self effect" key — target routing is entirely done via each effect's own
  `.target: "performer"/"target"` field). This meant Grave Robber's Shadow
  Fade (`stealth_self`) — and potentially other self-buff skills — silently
  did nothing. Fixed by also calling `_apply_skill_effects(performer,
  performer, skill, hit=True)` from `_resolve_self_action`. Stealth now:
  applies/expires via a round-tick (`_tick_stealth_durations`), excludes
  stealthed heroes from enemy random target selection (falls back to the
  full pool if every hero is stealthed, since AoE-vs-stealth reveal isn't
  modeled), and breaks on the stealthed combatant's own attack.
  **Crit-from-stealth bonus is a declared no-op**: the "StealthedCrit" effect
  references buff id `stealthed_crit_chance_buff` via `.buff_ids`, which does
  not exist under that id in `shared/buffs/base.buffs.json`, and
  `EffectLoader` doesn't parse `.buff_ids` generically yet (see Remaining
  gaps). The lookup mechanism (`_resolve_named_buff_amount`) is wired in and
  will apply the bonus automatically if the data is ever found — it currently
  resolves to 0 rather than a guessed percentage.

### DLC hero loading — new this phase

Was a path bug, not a scope decision: `HeroLoader.HERO_DIR_MAP` and
`HERO_CLASS_MAP` already listed `shieldbreaker`, `musketeer`, `flagellant`
correctly, but `load_all()` only searched `game_dir/heroes/`. Fixed by
falling back to a recursive `dlc/**/heroes/<subdir>/<subdir>.info.darkest`
search when a class isn't found under `heroes/`. Confirmed exact real paths:
- `dlc/702540_shieldbreaker/heroes/shieldbreaker/shieldbreaker.info.darkest`
- `dlc/445700_musketeer/heroes/musketeer/musketeer.info.darkest`
- `dlc/580100_crimson_court/features/flagellant/heroes/flagellant/flagellant.info.darkest` (nested one level deeper than the other two — the recursive glob handles this without a hardcoded depth)

All 18 known hero classes (15 vanilla + 3 DLC) now load; see
`tests/data/test_loader.py::test_all_18_hero_classes_load_by_name`, which asserts
each by name individually rather than checking `len(store.heroes)`, precisely
because a count check is what let the original DLC gap go unnoticed.

`DataLoader.load_all` also now merges effect and buff definitions from
`dlc/**/*.effects.darkest` and `dlc/**/*.buffs.json` into `store.effects` /
`store.buffs` (base-game tables load first and win on id collisions). Without
this, DLC heroes' skills referenced effect names (e.g. `SB Aegis`, `Flag Grit
5`) that only exist in their own DLC package. Verified zero unresolved effect
references across all 18 classes at all 5 skill levels after this fix.

### Bugs found and fixed this phase (parser-level)

These affected more than healing/guard/riposte/stealth/DLC — documenting them
here since they were silent, not surfaced by any prior test:

1. **`hero_loader.py` hardcoded `heal_min=None, heal_max=None`** for every
   hero `combat_skill`, ignoring `rec.get("heal")` entirely, even though
   e.g. `battle_heal .level 4 .heal 5 6` parses the value fine. Fixed to read
   and split it.
2. **`darkest_parser.py` dropped any key with more than one consecutive
   quoted value** (e.g. `.effect "A" "B"`, `.buff_ids "X" "Y" "Z"`) — the key
   didn't appear in the parsed record at all, not even partially. Fixed the
   regex to capture a list when multiple quoted values follow one key.
3. **`darkest_parser.py` couldn't parse `@`-prefixed position notation**
   (`.target @1234`, `.target ~@1234`, `.target @~1234`) — `@` marks a
   target list that includes the performer's own rank. The key was dropped
   entirely (same failure mode as #2, different cause: `@` wasn't in the
   unquoted character class). Fixed the parser and
   `decode_position_notation` to strip `@`/`~` in either order.
   `hero_loader.py`'s `is_aoe` detection (`target_raw.startswith("~")`) had
   the same order-dependent bug for `~@...` — fixed to
   `target_raw.replace("@", "").startswith("~")`.
4. **`.stress N` / `.healstress N` / `.cure 1` were never read** — the code
   checked for `.stress_damage` / `.stress_heal`, keys that appear **zero**
   times in the real game files (verified by grep). Every stress-damage,
   stress-heal, and bleed/blight-cure effect in the game was silently inert
   until this was fixed. (`.stress_damage_per_round`, used only inside the
   `combat_stat_buff` block, was checked the same way and is *also* confirmed
   to never appear in the real files — left as a documented dead field
   rather than guessing a real key name for it, since no per-round-stress
   effect exists in `base.effects.darkest` at all; it may be buff-level
   rather than effect-level.)
5. **A malformed source line** in `effects/base.effects.darkest:655` —
   `.name "Arb Mark Target".target "target"` — has no space between the
   closing quote and the next key. The parser's lookahead required `\s+`
   before the next `.key`, so it failed to close the quoted value and the
   entire `name` key (and therefore the whole effect) was dropped. This
   effect is referenced by both Arbalest's `sniper_mark` and Musketeer's
   `call_the_shot`. Fixed by loosening the lookahead to tolerate zero
   whitespace (`\s*\.` instead of `\s+\.`).

## Remaining gaps

Monster AI is now essentially complete: every skill-selection and
target-selection desire type is modeled (or confirmed data-equivalent to one
that already is), monsters heal/buff their own side for real, and
`bonus_initiative_desires` covers 22/25 real entries across all 14 boss
brains. What's left is narrow and specific:

| Gap | Detail | Priority |
|---|---|---|
| **The Hag's captor (hero-capture) mechanic** | `fill_ally_captor_empty_skill`/`fill_ally_captor_empty_target` (hag_A/B/C only) — a distinct hero-abduction subsystem (captured status, rescue) this combat-only simulator has no other support for at all. Declared out of scope like camping/Crimson Curse, not an AI gap — confirmed with the user after the real scope became clear. | Low (1 boss only) |
| **Ancestor's `death` phase-transition trigger** | `ancestor_small_D`/`ancestor_big_D`'s `bonus_initiative_desires` (`death` type, `kill_self` override) — ties into spawning the next Ancestor form, a boss-specific mechanic this simulator has no other support for. Implementing just the trigger without that payload would produce incorrect behavior, not approximate-but-reasonable behavior, so it's deferred rather than half-built. | Low (1 boss only) |
| **`ally_last_damaged` bonus-initiative trigger** | Swine Prince only, 1 occurrence — a redundant secondary trigger for the same rage mechanic already covered by its regular `ally_dead_skill` desire (verified working). Would need new "damaged this round" instrumentation across every damage codepath for one redundant occurrence. | Low |

### JSON Composition Loader

- `data/composition_loader.py` loads team compositions from JSON files
- Validates `heroClass` against loaded hero definitions
- Validates `activeSkills` against actual skill IDs for that class
- Maps JSON position order (index 0 = position 1) to `CombatEngine` initial positions
- Applies trinkets and quirks via `apply_trinkets_and_quirks`
- Stores `activeCampSkills` for future camping simulation
- Test fixture at `tests/fixtures/african_vapors.json`

## Data gaps

| Gap | Detail |
|---|---|
| **Skill level / weapon / armour tier selection** | **Resolved via `hero_levels`** (see "Status after Resolve Level" above) — but the level-to-tier mapping is a simulator convention agreed with the user, not extracted from a game rule (none exists; tier is bought independently of resolve level in the real game). Default (no `hero_levels`) still uses max tier, unchanged. |
| **Enemy damage ranges** | **Resolved** (see "Status after monster-AI completion phase" above) — monster attack damage now parses into its own `SkillLevelData.atk_dmg_min`/`atk_dmg_max` fields; `heal_min`/`heal_max` are the skill's real (previously-unparsed) `.heal` values, no longer overloaded with the attack-damage range. |
| **Mode switching** | **Resolved** (see "Status after Abomination transform" above) — `transform` now actually flips `hero.mode` via `EffectDefinition.set_mode`, with real inline stat buffs applying/reversing correctly. |

## Deliberate simplifications

- **No camping skills** — out of scope for single-combat simulation
- **No provisions or inventory** — out of scope
- **No dungeon traversal** — stress from hallway tiles, scouting, traps not modeled. (Distinct from encounter *composition* — which enemies show up in a fight — now resolved via `store.enemy_comps`/`ENEMY_COMPS/`, see "Status after enemy comp system" above; traversal is about everything *between* fights, still entirely out of scope.)
- **No resolve XP / level-up** — heroes always fight at max level
- **No Crimson Curse** — blood mechanics not modeled
- **No modded heroes** — Falconer, Black Mage, Red Mage, Pit Fighter lack `.info.darkest` files (unrelated to the DLC fix above — these are third-party mods, not official DLC, and genuinely have no game files to load)
- **No hero-capture ("captor") mechanic** — The Hag's cauldron (`fill_ally_captor_empty_skill`/`_target`) is a distinct hero-abduction subsystem (captured status, rescue) this simulator has no other support for. Confirmed with the user as an explicit out-of-scope call, same category as camping/Crimson Curse, not a monster-AI gap.
- **No Ancestor phase-transition mechanic** — the `death`-type `bonus_initiative_desires` trigger (Ancestor small/big forms) spawns the next Ancestor form on "death," a boss-specific mechanic with no other support in this simulator. Implementing the trigger alone without that payload would be actively incorrect, not a reasonable approximation.
- **Reward is potential-based shaping + terminal ±5** (see "Status after reward-shaping phase" above) — not the `0.0` stub older docs describe, and no longer the flat ±10 terminal-only signal either.

## Recommended next steps

1. ~~The Veteran/Champion collapse is confirmed a training problem, not a data/engine bug~~ — **done, and resolved**: see "Status after expedition training v4" at the top of this document. Retraining with both the decimal-parsing fix and the real skill-loadout restriction took Dark Ritual: Heist from 74/82/8/24/8% to 99/99/100/100/99% across tiers 1-5. The `Discrete(64)` action-index-instability issue documented below is apparently no longer the binding constraint at this budget (3M timesteps) once the action space itself was made smaller/more realistic — hadn't needed a stable-encoding fix to unblock this.
2. ~~Retrain against the decimal-parsing fix regardless~~ — **done**, combined with item 4 into the v4 run above.
3. **The three declared-out-of-scope items** (The Hag's captor mechanic, the Ancestor's `death`-transition trigger, `ally_last_damaged`) are all boss-specific and narrow — revisit only if a training/eval run specifically needs those exact bosses to behave with full fidelity.
4. ~~Retrain against the real per-comp skill loadout restriction too~~ — **done**, see v4 above.
5. **New: investigate "Holy Hymn: Bulwark" specifically** (v4's one real outlier — 65-83% win rate, 17-29/100 episodes timing out per tier, vs 0 timeouts and 96-100% for the other 4 v4 comps). Not the old "loses outright" pattern — wins slowly, sometimes too slowly. See "Status after expedition training v4" for candidate explanations (defense/utility-heavy comp against tankier Ruins enemies) — not yet verified with a `--play` trace or scripted-policy check.
