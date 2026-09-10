# Contracts for `engine/`

The combat simulator's own rules. Every heading below was moved verbatim
out of the root `CLAUDE.md` on 2026-09-07; most of them exist because the
first version got it wrong, and `docs/CHANGELOG.md` carries the evidence.


## Engine (`engine/`)

- `CombatantState` (`combatant.py`) — one struct for heroes *and* enemies; `is_hero` plus separate definition refs handle divergence. No inheritance split.
- `CombatEngine` (`combat_engine.py` plus seven mixins, the core):

  **The class was split on 2026-09-08 and nothing about it moved layer
  or changed name.** 97 of its 168 methods are called from OUTSIDE the
  class -- mostly by `tests/engine/`, and six of them are
  underscore-private -- so mixins were the only split that keeps every
  call site working: attribute lookup still resolves on the instance.
  Delegation to a helper object would have broken all 97.

  | file | what |
  |---|---|
  | `_constants.py` | the tuning constants; re-exported from `combat_engine` |
  | `_forecast.py` | `threat_forecast` and its helpers; read-only, one consumer |
  | `_formation.py` | ranks, gaps and closing them |
  | `_dots.py` | bleed, blight, burn zones, and the end-of-turn ticks |
  | `_afflictions.py` | stress, the trait draw, and the twelve act-outs |
  | `_effects.py` | `_apply_effect_to_target` and the rest of the effect path |
  | `_buffs.py` | trinkets, quirks, gear ranks, conditional buffs |
  | `_monster_ai.py` | skill and target selection, the enemy turn |

  What is LEFT in `combat_engine.py` is the part that shares the most
  state and splits the worst: `reset`, the turn order, `step`,
  `get_valid_actions`, action resolution, deaths, and win/loss.

  **`_ACT_OUT_HANDLERS` is why the affliction block can only move as one
  piece** -- it binds twelve bare function objects in the class body at
  class-creation time, so the dict and its handlers must share a class
  body. **Eight attributes are created by `reset()` and not `__init__`**
  (`provision_inventory`, `party_surprised`, `monsters_surprised`,
  `retreat_attempts`, `burn_zones`, `zone_id`,
  `_hero_skill_id_restrictions`, `_reinforcements_spawned`), so an
  engine that has never been reset raises `AttributeError` rather than
  reading an empty value.

  **Prove any change here with `python tools/trajectory_oracle.py`** --
  every one of the seven extractions was graded byte-identical on 44
  cells before it was committed.

  - `reset(hero_defs, enemy_defs, hero_positions=None, torch_level=100.0, hero_levels=None, hero_skill_ids=None, provisions=None, carry_over=None)` — builds states, computes initiative, then **auto-resolves any enemy/stunned turns ahead of the first hero**, so a fast enemy can act before the first hero decision.
  - **Always call `get_current_actor_id()`** to learn whose turn it is — never pick a hero yourself. The engine owns a single unified initiative queue (SPD+d8, descending) mixing heroes and enemies. A previous version of the Gym env tracked its own hero index decoupled from this; that was a real bug.
  - `submit_action(action)` → `step()` executes exactly one turn, then walks the queue auto-resolving enemy/stun/dead-skip until the next living hero. `_end_of_round()` runs once per full initiative cycle, not once per hero action.
  - **Formations close ranks; gaps only ever sit at the back.** Heroes via `_collapse_hero_position_gap`, monsters via `_collapse_enemy_position_gaps` (called from all five formation-changing sites: slain by attack, slain by DoT, `is_kill_effect`, corpse rot, corpse cleared). A corpse is an *occupant* of the monster formation — that is what makes it block — so it slides forward too rather than leaving a front gap. Enemies had none of this until 2026-08-08: their formation was frozen all fight, which made a rank-4 survivor permanently untargetable by any hero capping at rank 3 (the Abomination in every form) and was the real cause of the "Stun Control" stall-outs. Don't reintroduce "enemies deliberately don't advance" — see `CHANGELOG.md`.
  - **A monster occupies `size` ranks, and answers to every one of them.** Targeting,
    AoE resolution and `_find_combatant_by_position` all match against
    `occupied_positions`, never against `.position` alone — 61 of 246 monsters are
    size 2+, and matching on the front rank made a skill aimed at their back rank
    illegal (up to "no legal target at all" for a rank-3-only skill facing a large
    enemy on ranks 2-3). Correspondingly, **a save's enemy list index is not a rank**:
    `LiveEnemy.slot` is named for that, and everything behind a large monster sits one
    rank further back than its slot. See `CHANGELOG.md`.
  - **A Move is a SWAP with the ally in the destination rank**, so an occupied rank is
    the only LEGAL destination, not the only illegal one. The game calls it "swap hero
    positions" (`menu_options_element_tooltip_hold_required_in_dungeon`) and the party is
    contiguous from rank 1, so there is no empty rank to move into. This was inverted
    until 2026-08-19 and broke the action both ways at once: a **full party could not
    move at all** (every candidate skipped, so `[28..31]` were never once legal), while an
    under-strength one could only move into the ranks its dead used to hold — which is not
    on the board, and a live 2-hero party clicking rank 4 is how it was found. The
    party-size bound falls out of requiring an occupant; do not re-add a `min(3, ...)`
    clamp. `_execute_action` swaps both heroes and re-looks-up the partner, because a DoT
    can kill it between legalisation and execution. `_act_out_change_pos` had this right
    all along and is the in-file precedent. **Current checkpoints were trained without
    it** — costs 2.9pp on `tools/agreement.py`, all of it newly-advised moves. See
    `CHANGELOG.md`.
  - **A displacement is a ROTATION through the occupied ranks** (fixed 2026-08-21;
    it used to write an absolute position and displace nobody, so a 2-rank push on
    rank 3 of a full party gave `[0, 0, 1, 3]` — two combatants stacked and a hole).
    Pull the rank-4 occupant to rank 2 and former-2 goes to 3, former-3 to 4.
    Three consequences, all load-bearing: the **set** of occupied ranks never
    changes, so a shove can neither open a hole nor close one; **a lone combatant
    cannot be pushed at all**, and neither can the rearmost one, because there is
    nothing to rotate with; and a rotation that would hang a large monster off the
    back is refused outright. Gaps from a DEATH are a different mechanic —
    `_collapse_enemy_position_gaps` closes those, front-first, and gaps only ever
    sit at the back. `CombatEngine._shift_to_position`, used by push, pull and skill
    self-movement alike.
  - **A pure heal that cannot restore a point of HP is not offered** (`_is_wasted_heal`),
    a deliberate narrowing of the legal set — the action is strictly dominated, though the
    real game permits it. Tight on purpose: only skills whose sole payload is healing, all
    ranks of an AoE must be full, Death's Door is never full. Note `.effect ""` parses to
    `['']`, so test emptiness with `_named_effects`, never with plain truthiness.
  - **Buffs are the universal modifier** — trinkets, quirks, and skill effects all resolve to buff ids applied via `_apply_buff_to_stats`. Three distinct inline mechanisms exist (`.buff_ids`, `combat_stat_buff`+field, `.buff_type`/`.buff_amount`); conditional trinket/quirk buffs (`rule_type`, 52% of real buff entries) are toggled per-action rather than baked in at equip time.
- **Skill lookup is an index, not a scan.** `get_skill_data`/`_is_ally_skill` delegate to
  `DataStore.skill_index()` / `ally_skill_index()` (`data/types.py`), built lazily and pickled
  with the store. They used to scan all ~678 skill definitions by string id, ~15.6x per env
  step. The indexes are built with **`setdefault` in the original scan order** — heroes then
  enemies — because the scan returned the FIRST match; plain assignment makes the LAST
  duplicate win and silently resolves a colliding id to a different skill's numbers.
  `tests/engine/test_skill_index.py` checks both against the original algorithm, kept verbatim as an
  oracle, for every `(skill_id, level)` the game ships.
- `SeededRNG` (`rng.py`) — **every** random draw goes through this. Never call `random` directly in engine code; that's what makes traces reproducible from a seed.
- `resolution.py` — accuracy and damage/crit. **The 5% hit floor is a ROLL CUTOFF, not an accuracy bonus** (corrected 2026-08-19 from the binary — `DECOMP.md` F4). `compute_hit_chance` returns a bare `ACC - DODGE`, **unclamped**, and `resolve_to_hit` decides: `roll <= cutoffAlwaysMiss` misses, `roll >= cutoffAlwaysHit` **hits whatever the chance is**, otherwise `roll < chance`. So `P(hit) = 0.05 + min(0.95, raw)`. The old `HIDDEN_ACC_BONUS = 0.05` added to the chance before a clamp gave `min(0.95, raw + 0.05)` — identical below `raw = 0.90` and under-hitting above it, by up to 5pp. Both cutoffs apply to the roll; neither is a clamp on the chance.
- `stress.py` — `apply_stress` / `resolve_check` at ≥100 stress.


### Backward-compatible defaults that must stay behaviorally identical

- **`hero_levels=None`** → max tier, **zero** resist bonus. Do NOT default per-hero level to 6 internally — that already caused a real regression (silently added +50% resist to every default combat).
- **`hero_skill_ids=None`** → full ~7-skill movepool.
- **Abomination is never skill-restricted** — the real game gives him all 7 skills always, but the scraped comp JSON still lists only 4 (omitting all three beast-form attacks). `_resolve_skill_ids` returns `[]` for him deliberately.


### Burn is a real DoT now, and it copies neither of the other two (2026-08-20)

Enabled by the Runaway, but a dormant **vanilla** subsystem — `DECOMP.md` F6.
Three properties that must not be regressed, each one a thing you get wrong by
copying the bleed code:

- **A decaying stack count, not an amount with a tick budget.** No shipped burn
  effect has a `.duration`. `CombatantState.burn_stacks` is a plain `int`, and a
  second application **adds** rather than queueing an independent instance.
  Outstanding damage is `burn_total_outstanding`, never `stacks` — a stack of 7
  is worth 11.
- **It ticks at the END of the actor's turn**, where bleed and blight tick at
  the start. So a burning enemy **always** acts, even on lethal burn, and the
  "it is already dead, spend the turn elsewhere" play that works against blight
  does not work against burn. Three call sites, because a turn ends in three
  places: `step()`, the enemy path in `_advance_to_next_hero_turn`, and the
  stunned `continue` branch (stun stops acting, not burning).
- **Irresistible by NOT DRAWING.** `.dot_irresistible` is honoured by skipping
  the roll. Rolling against a zero resist gives the same outcome and a different
  RNG stream, which invalidates every trace comparison after it.

`.cure_burn` is its own key: a bandage, an antivenom and the bare `.cure` all
leave burn alone. `BURN_BASE_DECAY = 0.5` is **the one declared convention** in
the subsystem — see its comment in `engine/combatant.py` for the evidence and
the cheap live capture that would falsify it. Controlled Burn's rank *zones* and
Backdraft's `.dmg_per_burn_stack` are deliberately unimplemented; the mod's own
file says they need engine support and ships Backdraft as a plain `.cure_burn`.

Burn was **appended** to both slot vectors, not inserted beside the other DoTs,
so every index below it still means what it meant.


### An unread buff `rule_type` is granted, not skipped (2026-08-21)

`_is_conditional_rule_active` returns `True` for any `rule_type` it has no
branch for. That fallback is deliberate, but it is a choice about **which way
to be wrong** — the buff fires unconditionally rather than never. Two of the
unread ones were live, and cost 0.63 withdrawn buffs per combat once fixed:

- **`lightbelow` / `lightabove`** (130 / 104 defs) — torchlight is modelled, so
  234 torch-gated buffs were paying out at any light level. 1.70 carried per
  combat, **28.7% of combats had at least one buff withdrawn**, and **190 of
  190 withdrawn buffs were ones the party wanted**. Ancestor's Candle is the
  biggest carrier (28 hero slots; +15%/+15% damage, +2 SPD, +5% dodge).
- **`in_camp`** (41) — `CombatEngine` is combat and `engine/camping.py` never
  touches the buff machinery, so this is a constant `False`, not a gap.

**Both light comparisons are STRICT**, and the shipped thresholds say so rather
than us declaring it: bands exclude their lower bound, so band tops are
25/50/75, and the dominant constants `lightbelow 26` (105 trinkets) and
`lightabove 75` (56) land on a band edge **only** under `<` / `>`. The light
quirks agree independently — `lygophobia` +20% stress at `lightbelow 26`,
`phengophobia` +20% at `lightabove 75`, `nocturnal`/`diurnal` -2 SPD in the
light/dark. Still a declared reading (`ASSUMPTIONS.md` has the falsifier):
`dark_bracer` pairs `lightbelow 51` with `lightabove 51`, so torch exactly 51
fires neither, and that dead point is the game's own.

**The method note is the transferable part.** The buff sweep that found
`stun_chance` ranked candidates by `stat_type` and never looked at `rule_type`
— same file, same dataclass, one axis examined. Rank by what a real expedition
**equips**; and remember that one axis of a data file is not the file.


### A monster-brain gate belongs to the DESIRE, not to its type (2026-08-21)

`_skill_desire_gates_pass` is the single place a `skill_selection_desires`
gate is spelled out, and it runs for **every** desire. Gates used to be
checked inside the branch of whichever type they were first met on, so every
gate on `specific_skill` and `performing_turn_skill` went unread — 21.8% of
hall/room monster slots, and 5.2% of real enemy skill selections drawn from
the wrong weighting. Do not re-add a per-type copy.

- **`monsters_size_*` is total rank occupancy; `monsters_*` is a head count.**
  Both sit on one Siren desire, so they cannot be one quantity. Declared
  reading, falsifier in `ASSUMPTIONS.md`.
- **`is_valid_when_corpse_enabled False` = the no-corpse ruleset**, so the
  desire never applies here.
- **A `heal_skill` with no `hp_ratio_threshold` still needs a wounded ally** —
  the old branch's `else 1.0` default, kept in the heal branch because the
  shared gate only fires when the key is present.

**The two target-side flags are NOT complements.** `is_enemy_target_desire`
and `is_friendly_target_desire` are both true on 25 desires — every one a
brain's baseline flat `random_target` — so filtering on the friendly flag
alone dropped the baseline from enemy targeting and turned a 3:1 preference
for a marked hero into an absolute one (Swinetaur, Octotank, Fishman Shaman).

- Per-desire exclusions (`can_target_stunned`, `can_target_deaths_door`,
  `can_target_last_hero`, `is_guarded`, `is_guarding`) suppress that desire's
  opinion, **not the target**: the flat desire still keeps it reachable. The
  data's own "never" is a `base_chance` of **-1000**, and the two stack.
- `is_exclusive_desire`: when one applies, only exclusive desires count.
- **`can_target_last_hero` is inert by construction** — the last *living*
  hero is the only candidate, so weighting cannot change the pick. A test
  records that; do not "fix" it.


### A buff stat_type with no branch is a SILENT no-op (2026-08-21)

`_apply_buff_to_stats` is where trinkets, quirks, traits and skill effects all
cash out. Rank candidates by what a real expedition **equips**, not by how many
buffs exist — that is what put `stun_chance` (1.85 per expedition, the largest
inert type in the game) ahead of types with more definitions.

Wired: `stun_chance` and `move_chance` lower the TARGET's effective resist, the
shape `bleed_chance`/`poison_chance` already set (one resist roll per payload,
no separate chance-to-apply roll). `resolve_check_percent` shifts the **virtue**
side of the Resolve Check — positive is better for the hero, per its carriers
(`mercurial` +, `ennui` -), not per the name.

**`debuff_resist` is parsed on every combatant and never rolled**, so every
stat debuff lands unconditionally and `debuff_chance` is deliberately left a
no-op with a test pinning it. The blocker is that no shipped key says which
effects DEBUFF RES gates — Mark is resisted in the real game and carries only
`.tag 1`. `ASSUMPTIONS.md` has the falsifier.


### A status effect is ONE roll, and the resist is subtracted (2026-08-22)

`_apply_effect_to_target` used to roll twice — `.chance`, then the payload's
resist — which made every `.chance` at or above 1.0 identical to 1.0. **572 of
1973 shipped effects carry one**, laddered per skill rank (1.0/1.2/1.4/1.6) and
reaching 5.0 on Guard Break, so 29% of the game's effects carried dead numbers
and skill rank never improved how often a status landed.

- **One draw, two comparisons.** `roll < chance` gates what cannot be resisted
  (stress, heals, guard, shuffle, summons, **Mark** — the wiki says Mark cannot
  be resisted by anyone); `roll < chance - resist` gates the resistable
  payload. Occultist's Vulnerability Curse therefore still marks when its stat
  debuff is resisted. Do NOT re-add a per-payload `self.rng.random()`.
- **`CombatEngine._effect_resist` is the only payload → resist mapping**, and
  it returns one type because **exactly one shipped effect carries two**
  ("Baron Blight", Crimson Court). A test pins that count.
- **DEBUFF RES is live, and the predicate is the side test**: a buff crossing to
  the other side is a debuff. Nothing in an effect record distinguishes Bolster
  from a Weakening Curse, so that test is the whole rule — and it is why a
  self-inflicted debuff is never resisted. `debuff_chance` joins the four other
  attacker-side bonuses that lower the target's effective resist.
- **This moved the training distribution** (a rank-5 bleed at `chance 1.4` vs
  30% resist now always lands; a Champion debuff lands 60% where it landed
  100%). Checkpoints still load — the observation is unchanged — but comp
  scores from before 2026-08-22 are a different ruler.


### Shuffle, and the parser rule it exposed (2026-08-21)

`.shuffletarget` / `.shuffleparty`, 24 effects, unparsed — so **Flashbang and
Disorienting Blast were plain attacks**, along with Gargoyle `claw_flurry` and
Unclean Giant `confusion_spores` (6 monsters in the hall/room pool).

- **A VALUELESS FLAG IS REAL, and the parser used to lose it to whitespace.**
  `.flag  .next` parsed to `''`; `.flag .next` and `.flag<TAB>.next` made the
  key vanish outright. The value is optional now. `.shuffletarget` was dropped
  17 times and survived twice **in one file**. Fourth bug of this class in
  `darkest_parser.py` — check a new key against BOTH spacings.
- **Read such a flag by PRESENCE, never truthiness**: it parses to `''` and
  `bool('')` is False. Same trap as `.effect ""` giving `['']`.
- **A shuffle is unresistable** — no shuffle effect carries a push/pull
  distance, which is what the MOVE RES roll reads. `.chance` is the only gate,
  hence Shuffle Party shipping at 80-150%.
- `_scramble_side` / `_scramble_one` share the rotation invariant with push,
  pull and the surprise scramble: the set of occupied ranks never changes.


### The Hag's empty cauldron is not a target (2026-08-21)

`battle_modifier: .can_be_damaged_directly false`, 12 monsters. `cauldron_empty`
is 100 HP and size 2 on ranks 1-2 of her boss fight — the fattest and
front-most thing on the board, and not attackable at all. `cauldron_full` (a
hero inside) does not carry the flag.

- Filtered in **both** `get_valid_actions` and `_resolve_action_targets`, the
  rule corpses already follow: an AoE offered over ranks 1-2 that re-resolves
  to nothing burns the turn silently.
- **A front-rank Crusader is left with no attack in that fight.** That is the
  real fight, not a bug — she is behind two ranks of pot and the party needs
  reach. The cauldron carries `disable_stall_penalty True`, so nothing punishes
  the idle hero.

**The monster-side audit is exhausted for content this simulator fields.**
Every other unread monster key has zero carriers in the hall/room pool;
`starting_cooldown` and `rank_damage_modifiers` are Crimson Court / Farmstead /
roaming-boss only. `ASSUMPTIONS.md` records their semantics rather than
shipping dead code.


### A boss's adds die with it — `life_link` (2026-08-21)

`life_link: .base_class "X"` on 177 monsters, unread until now, so **killing
the boss did not end the fight**. Live rather than theoretical because every
summoning boss summons adds linked back to itself (Necromancer, Siren, Drowned
Captain, Brigand Cannon, Collector) and summoning had just started working.

- **Inert unless the parent is on the board.** That guard is the reading, not a
  safety margin: `skeleton_arbalist_A` is linked to `necromancer` and fills
  ordinary Ruins corridors. 1239 of 1266 linked-add comp slots have no parent.
- **Matched on `EnemyDefinition.base_class`, the monster's DIRECTORY**, never a
  `_[A-E]` suffix stripped off the id — the convention is not universal
  (`brigand_barrel_D`, `com_crocodile`).
- The key carries no variant, so a Veteran parent clears an Apprentice add.
  Faithful, and unreachable in shipped data — pinned by a test.
- Adds are **removed, not slain**: no corpse, no killing-blow relief, no
  recursion. Wired at all three monster-death sites.


### A hero's death stresses the survivors (2026-08-21)

`death_party_stress_chance` (0.5) and `death_party_stress_damage` (12) are real
`rules.json` scalars that nothing read until now — **a death charged the rest
of the party no stress at all**, which is why the death spiral could not happen
here. ~18 points of party stress per death, landing when the party can least
afford it.

`CombatEngine._on_hero_died` is the one place it fires, called from all three
routes a hero can actually die by: the deathblow roll (the DoT path routes
through it too), the heart attack that bypasses it, and `is_kill_effect` —
which also never fired death reactions before, so a Flagellant killed that way
skipped Last Gift and Last Insult.

`CRIT_STATUS_CHANCE_BONUS` is **gone**: `rules.json`'s `effect_crit_roll_mod`
is the same 0.20, shipped as a modifier on the roll (hence negative). Identical
predicate, no clamps on either side, so no behaviour change.

**Open, deliberately**: `deaths_door_*_repeat`. Two readings — every subsequent
hit at Death's Door, or the second entry to it — and implementing one when the
other is true is a regression. `ASSUMPTIONS.md` has both and the falsifier.


### Persistent equipment goes in `_on_board_ready`, never after `reset()` (2026-08-21)

`CombatEngine.reset(on_board_ready=...)` fires once the board is built and
**before** the torch modifiers, the surprise roll, initiative, and any turn.
Anything a hero carries for longer than one fight — gear ranks, trinkets,
quirks, the HP ceiling — belongs there.

It used to be applied after `reset()` returned, which is after `reset()` had
already let every faster enemy act. Two opposite bugs at once: the opening
blows landed on an unequipped party, and then writing HP last **healed the
party back to full and erased the round** (2.79 HP per combat, 34% of combats,
measured on the real-party path — so `bridge/rollout.py` and every embark
valuation built on it).

- `DarkestDungeonEnv._on_board_ready` is a no-op; `ExpeditionEnv` applies the
  sampled comp's trinkets/quirks, `ExpeditionSequenceEnv` applies gear ranks,
  trinkets, quirks and `hp_targets`.
- **What to apply is resolved BEFORE `reset()` and stashed** (`_pending_kit`,
  `_pending_hp_targets`), cleared in a `finally`. A hook that fires with a
  stale party's kit is the failure mode to avoid.
- This is what makes `party_surprise_chance` / `monsters_surprise_chance` and a
  +SPD trinket's effect on opening initiative work at all.


### Surprise is rolled per combat, and the dark is what rolls it (2026-08-21)

`base + darkness band`, clamped to the shipped 0.65 cap, two independent draws
at `reset()`. Radiant is party 10% / monsters 35%; black as pitch is party 50%
/ monsters 10% — **the sign is the mechanic**. On the real training torch
distribution that is 16.8% / 27.8%, and **40.5% of combats open with someone
flat-footed**. Four rules that must not be regressed:

- **`surprise=(party, monsters)` states the outcome instead of rolling**, and
  then does NOT scramble. `bridge/seed.py` passes the flags a live
  `persist.raid.json` already carries; re-rolling would shuffle the very board
  being copied. Same class of rule as `auto_resolve_to_hero=False`. Tests that
  pin who acts first need it too.
- **Both draws are always taken**, so the RNG stream cannot fork on the first
  outcome.
- **The scramble is a permutation of the ranks already occupied** — the same
  invariant `_shift_to_position` keeps, so it can neither stack two heroes nor
  open a hole.
- **It is the opening round only** (`self.round == 0`). The flags stay set,
  because the bridge reads them off a save; the round gate is what expires the
  effect.
- **The monsters get a veto and a trump.** Every monster file carries
  `battle_modifier: .can_surprise .can_be_surprised .always_surprise
  .always_be_surprised`; `_apply_surprise_flags` applies them **ANY, not ALL**,
  and only AFTER both draws are taken. `always_surprise` is exactly the
  Shambler line and the Fanatic; 162 monsters (Ancestor forms, Crimson Court
  bodies, the Baron) can neither surprise nor be surprised, which is how the
  game encodes "impossible in the Darkest Dungeon and the Farmstead". Worth
  **nothing** in corridor training and everything on bosses: un-flagged, a
  third of boss fights opened with the boss flat-footed.

Two things it deliberately does not do, both in `ASSUMPTIONS.md`: **scouting**
(so every encounter is treated as unscouted — the `known_` bases are parsed and
unused, and `-1.0` is a hard *never* rather than a zero because the darkness
table adds up to +0.40 on top), and the **`party_surprise_chance` /
`monsters_surprise_chance` buffs**, which are wired but unreachable because
both expedition envs equip gear and trinkets *after* `reset()` — an ordering
bug wider than surprise, filed separately.

`data/rules.py` parses ten of rules.json's twelve surprise keys. The two
`surprise_ambush_*` are **absent from `Darkest.exe`** while the other ten sit in
one contiguous parse run: an ambush sets surprise rather than rolling for it.
`DECOMP.md` F7.


## Known gaps

`ASSUMPTIONS.md` is the live list — check it before assuming a mechanic works. Summary:

**Implemented**: Guard, Riposte (with real ACC from `riposte_skill:`), Stealth, Torchlight, Monster AI (incl. friendly targeting and `bonus_initiative_desires`), Resolve Level, provisions, reinforcements, Move action (**a SWAP** — see below), on-crit passives, 3-tier Death's Door + heart attack, conditional trinket/quirk buffs, corpse clearing and corpse targeting, rank advancement on both sides, monster skill cooldowns, camping, overstress personalities (per-trait stat effects and act-outs), **burn** (stack-based, end-of-turn, irresistible — one declared convention, `BURN_BASE_DECAY`), **surprise** (rolled per combat from the darkness band; see below).

**Camping is auto-resolved, not a policy decision** (`engine/camping.py`), and
it has a FOOD phase since 2026-09-02 — `rules.json meals_table`, shipped, with
`rations_per` **per living hero** so a feast is 8 food for four and 6 for
three. A feast is the only stress relief in the game costing no respite points
(+25% max HP and −10 stress, skill budget untouched), which is why it is a
decision and not a top-up. Runs BEFORE the skill phase, the real game's order,
so the skill phase does not spend points healing what the meal removes.
`food=None` (the default) means no food phase and keeps every old caller
identical; `food=0` means an empty pack and starves (−20% max HP, +15 stress). Firewood by length is **0 short / 1 medium / 2 long**, so the short band gets no campfire at all — that makes it a control group when reading any retrain. The agent does not choose camp skills: `Discrete(39)` has fixed semantics and the observation carries no camp state. The point budget (12), rest threshold and 33% ambush chance are real `rules.json` values; `camp_rest_point_threshold` is parsed but deliberately unused, since no shipped rule states a generic rest heal and inventing one would be a convention masquerading as game data. The spend heuristic's weights (`W_STRESS`/`W_HP`/`URGENCY_MAX`) *are* convention — recovery is scored as a fraction of the pool it protects, urgency-weighted, because raw point-for-point scoring bought stat buffs while leaving a hero at 8/45 HP.

**Overstress personalities are real data, not a convention** (`data/trait_loader.py`). `shared/trait/trait_library.json` carries all 14 traits — 7 afflictions, 5 virtues, the Flagellant's `berserk`, Color of Madness's `color_madness` — with each one's complete `buff_ids` and its `combat_start_turn_act_outs` weights. Two rules that must not be regressed:

- **Act-out weights normalise over the rows valid for the acting hero's class**, never the raw table. `attack_friendly` ships as one row per class (Abusive has 19), so normalising over everything makes Abusive act out 64.7% of turns instead of its real 29.4%. Use `TraitDefinition.act_outs_for(hero_class)`.
- **`buff_ids` is the complete effect list.** The −15 resists and `MAXHP-10` every affliction shares are entries *inside* each affliction's own list, and the six +25 resists are inside every virtue's. There is no separate baseline to add; adding one doubles it.
- **The draw pool is gated by `TraitDefinition.is_rollable`, never by hero class.** `berserk` and `test` are `is_generated: False` and `color_madness` carries `generate_chance_modifier: -1.0`, leaving the 7 afflictions + 5 virtues the wiki documents. Do not re-add a Flagellant-only `berserk` branch — the flag says a Resolve Check never produces it.
- **...and berserk reaches the Flagellant anyway, through `overstressed_modifier:`, which is not the same thing.** His own `.info.darkest` carries `.override_trait_type_ids berserk .override_trait_type_chances 1.0`: he does not draw from the pool at all, so both statements hold at once and `is_rollable` stays exactly as it is. This was filed as a contradiction on 2026-08-23 and is not one. The wiring reads the record off whatever hero carries one (today: exactly one, install-wide) and tests no class anywhere, which is what the rule above actually forbids. **Rapturous is berserk** — `DMGL25 DMGH25 DEF-20 SPD3`, the wiki's "+25% DMG, −20 DODGE, +3 SPD" — and its `buff_ids` carry **neither** the −15 resists nor the MAXHP−10 every ordinary affliction includes in its own list, so do not add a baseline. One declared reading (the override suppresses virtue too) with its falsifier in `ASSUMPTIONS.md`.
- **An affliction lasts the rest of the raid, not the rest of the fight** — carried in `CarryOverHeroState.affliction_id`/`virtue_id` and restored by id, not re-rolled. **And it ENDS at 0 stress** (wired 2026-09-02): every in-combat stress reduction and `engine/camping.py` call `_maybe_clear_overstress`, and `_apply_carry_over` refuses to hand the trait back at `carry.stress <= 0`. The cure REVERSES the trait's buffs, and restores `max_hp`/`dmg_min`/`dmg_max` from `overstress_stats` rather than reversing them — a multiplicative int buff loses a point per cycle (`int(61*0.9)=54`, `int(54/0.9)=60`). Before this the simulator was HARSHER than the game, which is why it went unnoticed. Evidence: `zealous_vigil`/`turn_back_time` grant an extra stress heal `requirements: ["afflicted"]`, and both are *camping* skills.

The one declared convention left is `VIRTUE_SELF_HEAL_MAX_HP_FRACTION` — Vigorous's `heal_self` row names no effect, so its amount is invented (0.10). `retreat_from_combat` carries weight 0 in every shipped trait and `consume_item` is Color-of-Madness-only; both are unimplemented, and an unrecognised act-out kind **blocks** rather than firing.

**A corpse IS a target** (closed 2026-08-15 — it was the long-standing gap; see
`CHANGELOG.md`). Rules that must not be regressed:

- **A corpse carries the corpse stat block, not the husk of what died.** `store.corpses`
  (`corpse_A..D` 7/10/14/14 HP, `corpse_large_A..D` 15/21/29/30, size 2) is a **separate
  dict from `store.enemies`** so nothing can sample a corpse as an encounter. The variant is
  named by the slain monster's own `death_class: .monster_class_id`
  (`EnemyDefinition.corpse_id`). Leaving it on the dead monster's HP makes the action a
  multi-turn job nobody would take — a fix that fixes nothing.
- **It cannot be missed** (`.can_be_missed False`), forced in `resolve_attack`. The roll is
  still drawn so the RNG stream stays aligned.
- **Damage and nothing else.** `_strike_corpse` skips guard, riposte, crit stress relief,
  on-crit passives, effects and movement — all of it justified by the corpse's own data
  (200% stun/debuff/move resist, never takes a turn so DoTs cannot tick, `crit:
  .is_valid_effects_target false`, `.can_relieve_stress_from_crit/_killing_blow False`,
  `.can_be_guarded False`). Breaking a corpse must **never** route through
  `_handle_deaths_door`: it is already dead, and that path would re-log the kill and spawn a
  fresh corpse.
- **`get_valid_actions` and `_resolve_action_targets` must move together.** An AoE offered
  because a corpse sat in its ranks, then re-resolved to zero targets at execution, silently
  burns the turn. `_can_damage_corpse` gates both (`heal_min is None and dmg_mod > -1.0` —
  Blinding Gas is `.dmg -100%` and can do nothing to a corpse).
- **`_find_combatant_by_position(include_corpses=...)` defaults to False.** Monster AI
  targeting and push/pull mean *living*; only a damaging hero skill that asked gets a corpse.
- **`W_BLOCKING_CORPSE` is what gives the action a gradient.** A corpse is `is_dead`, so its
  HP never enters Φ; without a term, breaking one reads as a pure turn-penalty loss and gets
  learned away. It scores only corpses standing *in front of a living enemy* — the ones that
  actually block reach. Potential-based, so it cannot change the optimum.

Everything else about corpses was already modelled and is unchanged: they spawn per
`corpse_id` (125 of 254 monsters), never on a crit or a DoT kill, they block and slide
forward, they rot after 3 rounds, and `is_clear_corpses` skills remove them.

**Torchlight is sampled in training, not pinned at 100.** `TORCH_LEVEL_DECILES`
(`env/expedition_env.py`) is the measured decile distribution of 206 real in-battle captures
(only 68% radiant), drawn **per combat** from `_sampler_rng` — the scenario RNG, so a
reseeded scenario reproduces its torch. Before this, `_start_next_combat` never passed
`torch_level` at all, so the torch observation feature was a constant and the policy had
never seen the dark. `options["torch_level"]` pins the opening combat, which is what a
rollout asked from mid-dungeon needs.

**The enemy slot carries a `_is_doomed` flag** (outstanding bleed + blight >= current HP).
Derived, but not redundant: the comparison needs the target's *absolute* max HP, which the
observation never carried, so "is this kill already secured?" was not recoverable from the
vector at all. It is the quantity PLAYBOOK 3.3 turns on.

**Deliberately out of scope**: dungeon traversal, curios, torch decay over travel, resolve XP, Crimson Curse; The Hag's hero-capture mechanic; the Ancestor's `death`-phase-transition trigger; monster-matchup and town/camping buff rule types. Modded heroes load **with their kit** behind `DDSIM_MOD_DIRS` since 2026-08-27 — see the section above; twelve Workshop classes are subscribed and ten resolve 100% of their effects. `tests/data/test_mod_heroes.py` pins the class shape and `tests/data/test_mod_kit.py` the kit.

**Known-inert but implemented for correctness**: excess-crit overflow (`excessCritMult` is `0.0` in every shipped `rules.json`); the stealth-attack crit bonus (its buff id isn't in the loaded data).
