# Contracts for `env/`

The Gymnasium layer: action space, observation layout, reward, and the
expedition envs. Moved verbatim from the root `CLAUDE.md` on 2026-09-07.

**The observation width is a property of the loaded DATA as well as of
this code**, so a loader fix can unload every checkpoint with no edit here.


## Gym env (`env/gym_env.py`)

`DarkestDungeonEnv(render_mode=None)` loads the full `DataStore` once at construction (slow — real game-file parsing).

- **Action space `Discrete(39)`, FIXED semantics**: `[0..27]` = `skill_slot * 4 + target_rank`, `[28..31]` = move-to-rank, `[32..35]` = provision (**never legal since 2026-09-02** -- the game ships `cant_use_items_in_battle` and the cure moved to the corridor; `IN_COMBAT_PROVISIONS_ALLOWED` is a flag rather than deleted code so the indices, and therefore every archived checkpoint, are untouched), `[36]` = pass, `[37]` = **retreat**, `[38]` = **abandon** (gated by `_can_abandon`; a single combat cannot be abandoned, so it is legal only in `ExpeditionSequenceEnv` and only when the run has gone wrong — read the gate, it is load-bearing). **Read `ACTION_SPACE_SIZE`, never a literal**: it is `ACTION_ABANDON + 1` and this line said 38 for as long as abandon existed. Retreat was APPENDED on 2026-08-15 so every index below it still means what it meant; the widening itself is a hard break, and **no checkpoint trained before that date loads** (obs also went 336 -> 337 / 328 -> 329 for the live retreat chance). Retreat is offered alongside real options, never as a fallback like pass; `engine.winner` gains a third value `"retreat"`; the price is entirely emergent (forfeited `_reward_win` + `combat_retreat_stress` through the existing dense term), so `REWARD_RETREAT` is `0.0` and no new reward term exists. An afflicted hero never flees — the real game ships `retreat_from_combat` at weight 0 in every trait, so `_act_out_random_command` excludes it. `skill_slot` indexes the hero's **full 7-skill movepool**, not the restricted 4-skill comp loadout — so slot *k* names the same skill for a class regardless of what a comp equips. `PROVISION_ACTION_ORDER` is an explicit **tuple**; `engine.IN_COMBAT_PROVISION_IDS` is a *set* whose iteration order must never assign indices.
  - **An action the policy ranks first can have NO CONTROL ON SCREEN, and that
    used to DEADLOCK the bridge (2026-08-31).** Retreat is its own button and
    the tutorial draws none; a provision comes from the inventory; the
    Abomination can be offered a beast-form skill that is not on his bar. In
    all three `advised_button` returns `-1`, `plan_battle` refused, and
    `ActRunner` returned **without marking the turn played** — so the dedupe
    guard never tripped and the identical refusal republished every poll
    forever. It never misclicked; it stopped. The advisor's whole ranked
    shortlist now reaches the actuator (`Advisor.last_actions`,
    `Update.alternatives`) and `plan_battle` falls through to the next-best
    legal move. **The TIMING refusals never fall through** — provisional
    advice, pending enemy turns and a pending repositioner are statements that
    the turn is not due, and every candidate shares them, so walking the
    shortlist would only find a different way to act too early. See
    `CHANGELOG.md`.
  - `step()` falls back to `__pass__` for an unmapped index. It must **not** clamp — the old `min(action, len-1)` rewrote out-of-range choices into whatever was last in the list, the confirmed source of a "shuffles position forever instead of attacking" pathology.
- **Action masking is required, not optional.** `env.action_masks()` returns a `(ACTION_SPACE_SIZE,)` = `(38,)` bool array. Use `ActionMasker(env, lambda e: e.action_masks())` — **the callable form, not the string form**; the string branch calls an already-bound method with an extra arg and raises `TypeError`.
- **Observation `Box(1644,)`** (**1652** for `ExpeditionSequenceEnv`) — rank-indexed so it lines up with what an action means: 4 hero slots × 196 (56 state + 3 × 45 capability + heal headroom + 4 forecast) + 4 enemy slots × (84 + **13-type** one-hot) + 7 skill slots × 65 (20 descriptive + 45 payload) + `GLOBAL_FEATURES` (17). **Derive from the layout constants; never hardcode the width** — that is how the enemy slot once landed 6 short when the payload grew underneath it.

  **AND THE WIDTH IS A PROPERTY OF THE LOADED DATA, NOT ONLY OF THE CODE.** The enemy one-hot is `len(store monster types)` wide and sits in every enemy rank, so **a loader fix that reaches more content rewidens the observation and unloads every checkpoint, with no change in `env/` at all**. That is not hypothetical: on 2026-09-02 the DLC-monster-tree fix added `carpentry`, `ironwork`, `thatchery` and `cauldron` — 4 types × 4 ranks = **16 floats**, 1636 → 1652 — the whole suite stayed green, and the first thing to notice was `bridge/advisor.py` refusing to run the shipped checkpoint. `tests/suite/test_obs_width_tripwire.py` is the guard; a mods-on store legitimately differs, which is the same mechanism. Encoding *live* dodge/prot/speed/crit is what makes buff/debuff effects observable without enumerating buff ids. It was 345 before 2026-08-23; the two sections below are why it is not.

**A party that is not four must not read as a party that lost some
(2026-08-31).** The alive fraction was `sum(alive) / RANKS` with `RANKS`
hardcoded at 4, so a **healthy two-hero party reported 0.5** — the same number
a four-hero party reports the moment it loses half of itself.
`ExpeditionSequenceEnv` computed a second, correct one against
`_original_hero_ids`, so a sequence observation carried **both and they
disagreed**. Now `len(self.engine.heroes)`, which IS the party that started
this combat (the list is appended to only at `reset()` and never removed from).
The two are deliberately different quantities and both read 1.0 for an intact
party of any size. **The enemy side keeps `/RANKS`** — a board holds four enemy
ranks whatever arrived — and `tests/env/test_small_party_obs.py` pins both so they
cannot be made consistent by mistake. This is an observation-CONTENT change, so
pre-fix small-party numbers are not comparable.

**The step budget is enforced twice, and both halves matter.** `_max_steps =
MAX_STEPS_PER_COMBAT * num_combats` is an expedition total; `_combat_step_count` bounds the
CURRENT combat. For a long time only the first existed, so one runaway fight could legally eat
the whole pool — a 220-round combat was measured, and 600 pooled steps / ~2.7 steps-per-round
identifies it exactly. The per-combat counter must be read **before** the combat-transition
block in `step()`, or a fight that ran over silently starts the next combat with a fresh
budget. 40 is measured, not assumed: p99 of real per-combat hero steps is 38.

- **No built-in episode cap** — `step()` never sets `truncated`. `train.py`/`eval_expedition.py` wrap in `TimeLimit(max_episode_steps=150)`; do the same if you construct `DarkestDungeonEnv`/`ExpeditionEnv` directly. Without it a bad matchup ran 3565 steps. (`ExpeditionSequenceEnv` self-truncates instead — its cap depends on the sampled length.)


### The observation encodes MECHANICS, not identity (2026-08-23)

Fran's call, and the measurement that justified it: the skill slot used to be
10 floats — `exists, legal_now, targets_allies, is_heal, is_aoe, atk, dmg_mod,
crit, is_stall_invalidating, is_continue_turn` — and **125 of 140 hero skills
(89.3%) carry at least one payload none of them describes.** Stat buff/debuff
on 60.7%, self-reposition 17.1%, use-limit 12.9%, mark 10.7%, bleed 10.0%,
stun and push/pull 8.6%, heal *amount* 6.4%, stress heal 5.7%,
guard/riposte/cure/burn ~2% each.

So two skills of one class were separable **only by the 20-class one-hot plus
the slot index**: the policy memorised `(class, slot)` pairs rather than
learning that +DODGE is worth having, and a modded class — which has no one-hot
slot at all — transferred nothing. `EffectDefinition` already parsed every one
of these; the observation was discarding them.

Three changes, each with a rule attached:

- **`SKILL_MECHANIC_FEATURES` (45) is APPENDED after the original 10**, so every
  index below it still means what it meant — the same discipline burn and the
  retreat action were added under. Memoised per `(skill_id, level)`: the payload
  is static, and it is rebuilt for 7 slots on every step without the memo.
- **The 20-class one-hot is GONE, and the cost was real.** It was stated up
  front — the policy can no longer tell which class the other three ranks are,
  so it cannot reason "my Vestal can heal me next turn" — and then MEASURED:
  the held-out-class falsifier came back **+46.9pp excess gap, CI [+37.2,
  +56.9], 17 of 17 held-out comps worse**, with the two arms level on seen
  comps. The repair is the **hero capability profile** below, never putting
  identity back.
- **`_threat_ratio()` is the 8th global** — enemy damage per round over party HP
  remaining, with stunned enemies excluded. This is what decides RUSH vs STALL,
  and without it the stall timer two features up is unreadable: the policy could
  see how close reinforcements were and still had no basis for judging whether
  standing there was survivable.


### The hero capability profile — what fixed the one-hot removal (2026-08-23)

`_hero_capabilities` mirrors the `_enemy_capabilities` the enemy slot already
had: the hero's whole equipped moveset, max-pooled by magnitude into the same
45-float payload vector a skill slot carries. The hero slot goes 56 → 191 and
the observation 1065 → 1605.

- **THREE pools — ally / self / foe — not one.** Pooled together a Vestal's
  Divine Grace and a Leper's Solemnity both read as "heal ~0.3", and "the rank
  behind me can heal ME" is not recoverable. Same argument that doubles
  `STAT_BUCKETS` into target-side and performer-side: never make the policy
  infer an XOR against another feature.
- **The split needs BOTH `targets_allies` and `.target`.** `targets_allies` is
  the `@` prefix, so it is False on a SELF-only skill — which also carries an
  **empty** `.target`. Keying on the flag alone files Solemnity and Lick Wounds
  under FOE, i.e. claims the hero can heal the monsters.
- **A heal in the FOE pool is a LIFESTEAL, not a bug.** Judgement is aimed at an
  enemy and heals the Vestal. Heal indexes carry no side of their own, so which
  pool it lands in is the only thing saying who gets the HP.
- **Read the RESTRICTED 4-of-7, never the full movepool.** A Vestal who did not
  bring a heal must not read as a healer, and `--loadout-random-p` varies
  exactly that. Memoised on `(equipped ids, skill_tier)` — the tier has to be in
  the key or a rank-0 party reads with rank-5 numbers.
- **Arbalest and Musketeer collide, by design.** Their seven skills are renamed
  copies with identical numbers, so one profile covers both — which is the
  transfer this encoding exists for, and `tests/env/test_hero_capabilities.py`
  asserts it is the *only* collision in the roster.

**It was run, and it changed nothing: +46.6pp against +46.9pp without it**, CI
[+37.2, +56.3], 17 of 17 comps still negative. Two follow-ups killed the obvious
explanations — the held-out pool still fields an ally-healer in 92% of comps
(93% in the full pool), and the held-out policy *does* heal on Vestal turns,
just 20.3% of the time against the baseline's 58.6%. It reaches the action and
does not value it.

**So zero-shot transfer to an unseen class is not an observability problem, and
putting the class one-hot back cannot fix it either** — `--hold-out-class`
removes the class from the pool entirely, so its one-hot bit is `0` for the
whole training run and fires untrained at eval. Identity carries strictly less
about an unseen class than mechanics do. Do not propose either repair again;
`tests/env/test_skill_mechanics.py::test_a_held_out_class_is_unreachable_by_ANY_identity_encoding`
pins the premise and `CHANGELOG.md` has the retraction.

The rewrite is still worth keeping: on seen classes — the entire real game — the
two arms are level, and it closed 89.3% of hero skills carrying invisible
payloads. What it does not buy is the modded-class transfer it was argued for.

Rules that must not be regressed:

- **Every stat bucket reads "better for whoever RECEIVES it", and target and
  self are separate blocks.** That is why there are 12 buckets twice rather than
  12 once: a Weakening Curse reads negative-to-target and Bolster
  positive-to-self, so the policy is never asked to infer an XOR against
  `targets_allies`. `_RECEIVED_IS_BAD` flips `stress_taken` and `dmg_taken`,
  because +20% stress damage *received* is a debuff.
- **`combat_stat_add`/`combat_stat_multiply` carry the real axis in the SUB
  type.** Keying `_stat_bucket` on `stat_type` alone collapses 272+209+177+164+145+118
  buffs into one meaningless bucket — most of the 60.7%.
- **A payload feature no shipped hero skill can set is a WIRING BUG**, and
  `tests/env/test_skill_mechanics.py` fails on one. That is how the missing heal
  amount was caught: Vestal's Divine Grace carries no healing *effect* — the
  amount is on the skill level — so her signature skill read as an empty
  payload. Five exceptions are allowed and each is justified in the test.
- **`SkillLevelData.extra_damage_vs_*` is NOT encoded.** All eight fields are
  zero on every shipped skill, hero and monster alike; the real "bonus damage vs
  unholy" is delivered by buffs carrying `monster_type_condition`, which arrive
  through `buff_ids` like any other buff.

**No checkpoint trained before 2026-08-23 loads.** The falsifier for whether
this was worth it is **transfer to a held-out class** — not a win-rate delta,
whose seed-to-seed spread (~1.4pp) is larger than most effects this project
measures.

```bash
python train.py --full-expedition --timesteps 3000000 --seed 0     --hold-out-class duelist --out models/heldout_duelist.zip
python tools/transfer_eval.py --model models/heldout_duelist.zip     --hold-out-class duelist --baseline models/dd_combat_ppo_full_expedition.zip
```

- **`--hold-out-class` removes the class from the comp pool entirely**, so the
  policy never sees it. It is NOT a sixth randomisation knob: those vary the
  party within a fixed pool, this changes which pool exists. The name is
  validated against the real class list, because a typo would hold out nothing
  and the run would look like a successful experiment.
- **Pick a class with a small share.** Duelist and Runaway cost 5 comps of 141;
  Houndmaster would cost 66 and confound the result with a halved training set.
- **`--baseline` is not optional in practice.** The two comp halves are not
  equally hard, so a single arm's gap conflates transfer with difficulty. The
  number that means something is the DIFFERENCE of the two gaps; near zero is
  the claim landing.


### The threat forecast — what the board is ABOUT to do (2026-08-25)

`CombatEngine.threat_forecast()` returns, per hero, expected incoming damage,
expected **stress** and P(targeted) for the coming round. It is the answer to
"can the model have an edge a real player does not": a human estimates these
from experience, and every input here is exact — the enemy's own decision
distributions (`tools/brain_oracle.py`, 46/46 against the running game) through
the validated hit rule (`tools/hit_oracle.py`, 155/155).

Observation went 1613 → 1636: four floats per hero slot (incoming/current HP,
incoming/max HP, incoming stress, P(targeted)) plus three globals (SECURED =
can the party heal through what is coming, board-wide damage, board-wide
stress). **No checkpoint trained before 2026-08-25 loads.**

Rules that must not be regressed:

- **IT DRAWS NOTHING.** `_enemy_skill_weights` / `_enemy_target_weights` are
  the weighting half of the brain selectors, split out from the drawing half.
  A redundant roll would move the RNG stream and invalidate every trace
  comparison after it — the same discipline `.dot_irresistible` follows by
  *skipping* its roll rather than rolling against a zero resist.
  `tests/engine/test_threat_forecast.py` replays seeded episodes with and without
  calling it and requires identical trajectories.
- **`_get_obs` calls it exactly ONCE** for four hero slots and three globals.
  It is 59µs against a 464µs step — 12.7% of rollout, ~3% end-to-end once
  SGD's ~73% share is counted. Per-slot computation would be seven times that
  for no new information. A test pins the single call.
- **Crit stress is included, and it is NON-LOCAL.** A monster crit charges 10
  stress to whoever it hit and 0.5 × 5 to every *other* living hero — 17.5
  party stress per crit with four alive, the largest stress source in a normal
  round. So an attack aimed at the front rank raises the forecast stress of the
  hero at the back. That is faithful and it is most of what makes a crit
  frightening.

**Validate the whole, not just the parts.** `tools/forecast_calibration.py`
compares predicted against realised, per round, and it is the check that caught
the crit-stress omission: the two *inputs* were each validated (46/46, 155/155)
while the *assembled* prediction under-read stress by 2×.

```bash
python tools/forecast_calibration.py --model <ckpt> --episodes 10
```

| tier | damage | stress before | stress after |
|---|---|---|---|
| 1 | 0.57 | 1.16 | 0.85 |
| 3 | 0.73 | 1.97 | **1.07** |
| 5 | 0.68 | 2.11 | **1.37** |

Ratios are realised/predicted. **Damage over-predicting (0.57–0.73) is the
expected direction and is left alone** — heroes kill or stun enemies mid-round,
removing damage the forecast already counted, and a consistent bias is
something a policy can learn to discount. **Under-predicting stress was the
defect**, because the feature exists so a hero can be *seen* to be in trouble.
The residual 1.37 at Champion is Death's Door and hero-death party stress,
which need P(a hero reaches 0 HP) rather than a mean — recorded, not guessed.


### Changing an observation-FEEDING function silently invalidates old checkpoints (2026-08-25)

A checkpoint is only comparable in the world it trained in. Widening the
observation is the obvious break and the loader catches it — the width changes
and nothing loads. **Changing what an existing feature CONTAINS is the silent
one**: the width still matches, the checkpoint still loads, and it is being fed
numbers it never saw.

This happened the same night the feature was introduced. `threat_forecast`
gained crit stress at 03:19, which roughly doubled its stress outputs.
`models/forecast_20260825.zip` had finished at 01:40. Every evaluation of it
after 03:19 fed it a shifted distribution.

Consequences, and both directions must be stated:

- The +7.7pp the forecast measured against its control is **confounded**.
  Off-distribution input usually hurts, so the number is probably understated
  — but "probably" is not a sign, and it should not be quoted as clean.
- Comparing a checkpoint trained before the change against one trained after it
  is **unfair to the older one**, independently of which is better.

**The rule:** when a function that fills an observation slot changes, every
checkpoint trained before it is off-distribution. Either retrain the arm, or
run the old checkpoint in a worktree at the pre-change commit
(`git worktree add --detach <path> <sha>`), which is cheap and keeps the
comparison honest.

**The tell** is two instruments disagreeing when they should not. That is what
exposed both of this night's measurement bugs: the loadout omission (paired
+3.0pp against standalone +6.4pp on nominally identical scenarios) and this
one. Treat a disagreement between rulers as a bug signal, never as noise.


### Reward

`reward = SHAPING_GAMMA * Φ(s') - Φ(s) + turn_penalty - STRESS_TICK_PENALTY * stress_gained`, plus win/lose on the terminal step. **`SHAPING_GAMMA` must match PPO's `gamma`** in `train.py` — that's what makes the *shaping* policy-invariant (Ng/Harada/Russell 1999).

Two terms are deliberately **non-potential**, and therefore *do* change the optimal policy:

- **Two-phase tempo** (`is_recovery_phase()`): per-turn cost is `TURN_PENALTY_THREAT` normally, dropping to `TURN_PENALTY_STALL` only when down to one living enemy **and** the party genuinely needs recovery **and** it's under a per-combat `RECOVERY_TURN_BUDGET`. All three gates matter — dropping the need-gate produced a policy that dawdled forever at one enemy left.
- **`STRESS_TICK_PENALTY`** per point of stress *gained*, charged when it lands. Only increases are charged; refunding stress healing would pay the policy to farm heal-stress loops.

`ExpeditionSequenceEnv` overrides `_reward_win` and adds `REWARD_HERO_DEATH`, `REWARD_EXPEDITION_COMPLETE`, `REWARD_TRUNCATION`, and `TERMINAL_STRESS_PENALTY_WEIGHT`. **All of these are actively tuned — read them from the source, not from prose.** `TERMINAL_STRESS_PENALTY_WEIGHT` and `STRESS_TICK_PENALTY` are meant to move together.

**Φ is zeroed only at the TRUE episode end, and that is `_is_episode_terminal()`, never `engine.done`.** In an expedition `engine.done` fires once per combat, 8-18 times an episode, and `ExpeditionSequenceEnv.step` un-terminates all but the last. Zeroing there pinned each combat's shaping to `-Φ_start`, which charged the party for entering a fight healthy (-0.33 a boundary, -4.9 a long run) **and cancelled every in-combat Φ term** — HP, stress, guard, riposte, `W_ENEMY_STUNNED` — since a pinned endpoint erases whatever happened in between. The subclass override mirrors `step`'s transition block, including that a **retreat from the last combat is not an episode end** (the room stays on the map and is re-taken). Fixed 2026-08-23; `tests/env/test_potential_reset.py`.

The invariance argument covers only the `Φ` terms. Re-check `ASSUMPTIONS.md` before adding another non-potential term. Tests: `tests/env/test_reward.py`, `tests/env/test_expedition_reward.py`.


## Expedition envs

- **`ExpeditionEnv`** (`env/expedition_env.py`, `--expedition`) — randomizes (real comp, zone-matched real enemy encounter, paired Resolve Level) every `reset()`. Mash tiers 1-2/3-4/5 are confirmed Apprentice/Veteran/Champion, so `TIER_TO_HERO_LEVEL` fields the party at the top of the matching bracket instead of always-max-gear heroes facing the full range — or, with `resolve_sampling`, spreads it across the bracket (see the five knobs below).
- **`ExpeditionSequenceEnv`** (`env/expedition_sequence_env.py`, `--full-expedition`) — one episode = a full 8-18 combat expedition. HP/stress/Death's-Door/mortality-tier, the overstress personality and **the DoTs** carry across combats via `carry_over=`; everything else (buffs, guard, riposte, stun, cooldowns) resets each combat, matching the real game. **The DoTs used to be in that second list and it was a gap**: `rules.json` ships `dotHallwayStepRatio = 0.5`, a rate for ticking a DoT per HALLWAY TILE, which is only a meaningful number if the DoT reaches the hallway — and it is the whole reason a bandage is worth carrying. The tick itself is not modelled (no tiles here), so the simulator is *gentler* than the game by half a hallway per room; see `ASSUMPTIONS.md`. Buffs still expire because their durations are counted in ROUNDS and a corridor has none. **A hero death does not end the episode** — the party continues with survivors, re-packed into contiguous ranks. Only a full wipe or the last combat ends it.


### The training distribution is five knobs, and all of them default OFF

Added 2026-08-20 after Fran audited what the model was actually trained on.
Every one is `0.0`/`False` by default and proven byte-identical to the previous
behaviour, so an archived checkpoint's distribution is reproducible; a retrain
turns them on explicitly. `tests/env/test_training_distribution.py`,
`tests/env/test_random_kit.py`, `tests/env/test_boss_quests.py`.

| flag | what it varies | default |
|---|---|---|
| `--loadout-random-p` | a random legal 4-of-7 instead of the comp's four | 0.0 |
| `--cross-region-p` | route the comp to a region other than its tag | 0.0 |
| `--resolve-sampling` | each hero's resolve drawn within its bracket | off |
| `--kit-random-p` | random legal trinket pair + quirk sheet | 0.0 |
| `--boss-quest-p` | the expedition's LAST combat is the region's boss | 0.0 |

**THE KNOBS ARE UNREACHABLE FROM AN EXPLICIT SCENARIO, and that is a trap
rather than a bug.** `ExpeditionSequenceEnv.reset`'s `"hero_ids" in options`
branch takes `hero_skill_ids` and `trinkets_and_quirks` straight from the
options and never calls `_resolve_skill_ids`, which is where
`--loadout-random-p` and `--kit-random-p` live. So every pinned-comp path --
`eval_expedition`, `tools/compare_arms.py`, `bridge/rollout.py` -- gets the
comp's own loadout regardless of what the knob says. Verified 2026-08-25: at
`loadout_random_p=1.0`, two *different* scenario seeds produced *identical*
loadouts.

That is defensible (an explicit scenario is explicit) and nothing depends on it
wrongly: `comp_ga`'s stage-1 kit redraw builds the party itself and passes it
through `bridge/rollout.py`'s explicit `trinkets_and_quirks`, so its claim to
integrate kit luck out is intact. **But anyone adding a randomisation flag to
an eval will get silence, not variation** -- draw the loadout at the call site
and pass it in, the way `tools/compare_arms.py --rand-loadout` does.

**Also: pinning a scenario means pinning TWO streams.** `seed_scenario(seed)`
reseeds `_sampler_rng` *and* `_loadout_rng`; `_sampler_rng.seed()` alone leaves
loadouts unpinned. The `sampler_seed` reset option calls `seed_scenario` for
that reason.

**Four of the five draw from `_loadout_rng`; the boss draws from
`_sampler_rng`.** That split is load-bearing, not an inconsistency. Anything
describing the *party* must not consume from the generator that also picks the
monsters and the torch, or two arms of a comparison face different dungeons —
the exact failure `tools/ab_compare.py` exists to prevent. Whether a quest is a
boss quest, and which boss, are properties of the *scenario*, so they belong in
`_sampler_rng` and a reseeded scenario reproduces them.

Three errors these were built on top of, all found by auditing the
distribution rather than by a crash — see `CHANGELOG.md` for the evidence:

- **`boss` and `stall` were sampled as corridor encounters**, 3-6% and ~9% of
  every draw. A `boss:` row is a real boss; a `stall:` row is the reinforcement
  wave the engine summons at a stalling party. About an eighth of all training
  combats could not happen.
- **A comp only ever fought in its tagged region**, and 78 of 145 comps are
  tagged Ruins — so 55% of all expeditions were in the Ruins and a Cove comp
  never met a skeleton.
- **Apprentice parties were a full gear rank under-equipped.**
  `tier_idx = level - 1` was a convention; the game's own
  `prerequisite_resolve_level` gating is `(1, 2, 3, 5)` across all 15 vanilla
  classes, so resolve 2 buys rank **3**. See `GEAR_RANK_RESOLVE_THRESHOLDS`.
  `apply_gear_ranks` must stay in sync with `reset()` — it applies a DELTA
  against the tier `reset()` chose, and keeping its own copy of the formula
  inflated every real-gear hero.

Two things the audit confirmed were already right, so don't re-derive them: the
enemy pool differs per zone **and** per difficulty (crypts has 14 distinct
monsters at tier 1, 18 at tier 3, 21 at tier 5), and variants track it — tier 1
draws are 100% `_A`, tier 3 100% `_B`, tier 5 `_C`/`_D`.

**The tutorial party is a comp, but NOT in the sampled pool.**
`COMPS/curated_comps/The_Old_Road.json` registers it for everything that works by name;
`PINNED_COMP_NAMES` (`env/expedition_env.py`) is what keeps it out of the
sampled pool, matched on its `alias` as well as its name — the 2026-08-23
re-export renamed it to "Ragged Band: Heist" and kept "The Old Road" only in
the alias. Reach it by pinning (`train.py --tutorial`,
`data/tutorial_loader.py`), never by sampling.

`EXPEDITION_LENGTH_BANDS` is **derived from real game data** — `scripts/map_generator.darkest` (`hallway_battle` + `total_room_battles` per size/quest/zone), loaded via `data/map_gen_loader.py`. It yields short `(3, 8)` / medium `(6, 10)` / long `(14, 16)`. It used to be an invented convention (short `(8, 11)` / medium `(10, 14)` / long `(14, 18)`) on the mistaken belief that layout generation was binary-only; that ran ~2x the real combats at short and medium. See `CHANGELOG.md`.

The provision quantities remain an explicit **simulator convention** (`provision.json` exposes only per-length purchase limits, not recommended loadouts).

Regenerate the browsable enemy-comp snapshot with `python -m data.generate_enemy_comps` — **must run as a module**; as a script path it shadows the stdlib `types` module with this repo's `data/types.py` and crashes on import.


### Districts are combat content, and food finally has a consumer (2026-08-24)

The town is out of scope; **a district's payoff is not paid in town.** Nine of
the nineteen grant ordinary buff ids `_apply_buff_to_stats` already cashes, so
`data/district_loader.py` is a *binding* from a hero tag to existing buffs —
the same shape the trinket table has — and nothing in it parses a stat. Worth
**+13.9pp** (hero buffs) to **+19.4pp** (full package) SUCCESS on 36 paired
long/Champion scenarios, measured on a checkpoint that had never seen them.
Largest fidelity effect this project has measured.

- **`hero_type_tags: []` means EVERY hero** — the OPPOSITE of the
  `hero_classes: []` rule on camping skills, so do not inherit that one.
  `conservatory_of_steel` is the evidence: it ships both an empty-tag entry and
  a tagged one, so empty-means-none would make three buffs dead data.
- **Every hero carries exactly one district tag.** `hero_type_tags` is a class
  gate wearing a tag's clothes.
- **Tier-gated economically, not mechanically** (`districts_at_tier`): Crimson
  Court / Fires Edge from **Veteran**, Color of Madness from **Champion**. A
  real campaign cannot afford them earlier. Tiers 1-2 are byte-identical to the
  pre-districts behaviour and are therefore a **control group**.
- **The Granary always gives its maximum** (10 of a shipped 4-10): the roll is
  rerollable from the provision screen, so a player takes the max.
- **`hp_heal_received_percent` carries the real axis in its SUB TYPE.**
  `districts_granary_1` is `'eat'` — food only. Ungated it silently becomes
  +15% to all healing including a Vestal's. Same class of bug as
  `combat_stat_add`. `CombatantState.eat_heal_received_mult` is its own field.
- **`DistrictLightBuffData` is the one combat district NOT wired**: the
  Illuminators Guild replaces the whole darkness range table (+7.5 DODGE at
  radiant against the base +4.0). A table swap, not a buff grant — see
  `ASSUMPTIONS.md`.

**Deliberate eating is what makes food real.** Until now the involuntary hunger
check was food's ONLY consumer, so a long run ended with 18.5 of 36 unspent and
a Champion run — where the Mill disables hunger — with all 46. Measured over 24
long runs taking 345 HP of damage, camp restored 4.7% and food 4.7%, against
**48.4% from in-combat healing**. That is what made a dedicated healer
structurally mandatory here, and it is the real source of the comp search's
healer clustering. `EAT_TOPUP_FRACTION` (0.90) is the one declared convention,
with a reserve held back so eating can never cause a starvation (20% max HP
against the 5% a meal restores).

**The Mill is a GAIN, not a tradeoff** — `full+eat` 36.1% vs `buffs+eat` 27.8%.
Read literally without eating it looked like a loss; that was the missing
mechanic, not the district. Fran's correction, and the measurement agrees.

**Comp scores from before 2026-08-24 are a different ruler** at Veteran and
Champion. Checkpoints still load (the observation is unchanged);
`DISTRICTS_ENABLED = False` reproduces the old distribution for an A/B arm.


### A retreat does NOT consume the encounter (2026-08-21)

`ExpeditionSequenceEnv` used to advance `_combat_idx` on a retreat, which made
fleeing a **paid skip** — and, because a retreat sets `terminated`, made
fleeing the LAST combat collect `REWARD_EXPEDITION_COMPLETE`. A fled room stays
on the map (wiki: the party is left in the hallway it entered from), so the
party now faces the same room again.

- **The same `entry` and `torch` are reused**, via
  `_start_next_combat(entry=, torch=)`. `_draw_encounter` consumes
  `_sampler_rng`, so merely not advancing the index rerolls the monsters the
  party retreated from.
- **`_snapshot_and_advance_roster(completed=False)`** — no food, no camp. Both
  are between-*cleared*-rooms recoveries; either would make fleeing a way to
  buy recovery.
- **Abandon is implemented now** (`ACTION_ABANDON`, index 38) and this line
  used to say it was not. It is deliberately gated: offered on every turn, a
  400k probe had the policy collapse onto it — 75% of runs abandoned, a median
  12% of the way in, at 74% party HP with zero deaths — because a safe terminal
  worth ~0 beats playing on while the value estimate is negative. Read
  `ExpeditionSequenceEnv._can_abandon`. **Re-route** is still a dungeon-layer
  decision this env has no map for, and is still a gap in `ASSUMPTIONS.md`.
- The re-taken encounter is the same monsters at **full HP**. That is the one
  declared convention in the mechanic.
