# WIKI_MECHANICS.md — gameplay mechanics extracted from darkestdungeon.wiki.gg

Condensed mechanics reference pulled from the wiki's Gameplay category. Numbers only — lore, flavor, strategy and patch history deliberately dropped.

## Source precedence — read this first

**Game install files > wiki > anything else.** The wiki is a *third* source, and it earns its place in exactly two situations:

1. **Mechanics hardcoded in the game binary**, exposed by no data file at all. The wiki is the only source for these. Precedent: the hidden `+5` ACC, the 200-stress heart-attack threshold, `TIER_TO_HERO_LEVEL`.
2. **Interpreting a field whose data value is ambiguous.** Precedent: `is_crit_valid`, the stall conditions, crit damage being 150% of *maximum* skill damage rather than 1.5× the roll.

Where the wiki and a parsed game file disagree on a number, **the game file wins** — it's what the shipped game actually executes. The wiki is community-maintained, occasionally reflects a different patch, and rounds things. Two live examples of exactly that are footnoted below.

Sources: `Combat_Mechanics`, `Status_Effects`, `Damage_over_Time`, `Stalling`, `Critical_Hit`, `Death's_Door`, `Affliction`, `Virtue`, `Stress`, `Resolve_Level`, `Surprise`, `Corpse`, `Enemy_Type`, `Light_Meter` (all `(Darkest_Dungeon)` where disambiguated).

---

## Fidelity gaps this extract exposed

Ordered by expected impact. **Verified** = I checked the code path directly this pass. **Unverified** = flagged from the wiki text, still needs a look.

### Fixed (were confirmed gaps, verified against code)

1. **PROT had no 80% cap.** `resolution.py`'s `resolve_attack` now clamps via `PROT_DAMAGE_REDUCTION_CAP = 0.80` before applying `(1.0 - prot)`. The buff-application clamp on the *stat* itself stays at 0.95 (`combat_engine.py:376`) — that ceiling is real too, just on a different quantity (the stat, not its damage effect). Regression test: `tests/engine/test_resolution.py::test_prot_is_capped_at_80_percent`.
2. **Restoration (heal-over-time) ticked in the wrong order.** `_advance_to_next_hero_turn` now runs `_tick_heal_dots` → `_tick_dots` → `_tick_stress_dots`, matching the wiki's stated Restoration-before-Bleed/Blight order. Fixing the order alone opened a second-order bug: a heal_dot crossing above 0 HP could prematurely flip a hero off Death's Door before that same turn's bleed/blight landed, skipping the deathblow roll a hero already on Death's Door owes for a net-negative turn. Fixed by deferring the recovery check (`_apply_heal(..., check_recovery=False)` from `_tick_heal_dots`) to a single reconciliation after all three ticks. Regression tests: `test_heal_dot_ticks_before_bleed_blight_each_turn`, `test_heal_dot_does_not_cause_premature_deaths_door_recovery` (`tests/engine/test_gaps.py`).
3. **Initiative tie-breaks were correct only by accident.** `_compute_turn_order`'s sort key now explicitly tie-breaks on `(0 if is_hero else 1, position)` after `-(spd+roll)`, instead of relying on Python's stable sort plus both lists happening to already be rank-ordered. Regression test: `test_initiative_tie_break_is_explicit_heroes_first_then_lower_rank` (`tests/engine/test_gaps.py`).
8. **Crit-heal was entirely unmodeled.** No heal ever crit, regardless of `skill.crit`/`attacker.crit`, since heals never route through `resolve_attack`. Added a hero-only heal-crit roll inside `_apply_heal` — `HEAL_CRIT_CHANCE_SINGLE_TARGET=0.12`, `HEAL_CRIT_CHANCE_PER_TARGET=0.05` (wiki-confirmed rates), doubling the applied amount and cutting the target's stress by `HEAL_CRIT_STRESS_REDUCTION=4.0` on a crit. Wired into every hero-cast heal path: skill heals (`_resolve_heal`), self-heals (`_resolve_self_action`), named-effect heals (`_resolve_effect`'s `heal_min`/`heal_percent` branch), and heal-dot ticks (`_tick_heal_dots`, gated on the *receiving* hero since `DotInstance` has no caster reference — narrow real scope, Flagellant's Reclaim only). `crit_eligible` is opt-in per call site, and `_apply_heal` itself also re-checks `performer.is_hero` as a safety net so a monster heal can never crit even if a future caller passes the flag by mistake. Regression tests: `test_heal_crit_doubles_amount_and_reduces_stress`, `test_heal_crit_single_vs_multi_target_rate`, `test_heal_crit_not_eligible_by_default_or_for_monster_heals` (`tests/engine/test_gaps.py`).
9. **Underpowered-hero penalty was entirely unmodeled.** New opt-in `CombatEngine.reset(quest_difficulty_level=None)` param (default `None` → zero penalty, same backward-compatible-default convention as `hero_levels`). When set, each hero takes `UNDERLEVELED_STRESS_PER_LEVEL=20.0` starting stress and `1.0 + UNDERLEVELED_STRESS_DMG_MULT_PER_LEVEL=0.25` stress-damage multiplier per level their `hero_levels` entry falls short of it (wiki-confirmed: "20 stress for every level of difference" / "+25% stress for every level of difference"). **Currently unreachable through either expedition env by design** — `TIER_TO_HERO_LEVEL` always pairs party level exactly to zone tier, so the shortfall is always 0 there; this only fires for a caller that explicitly passes a mismatched `quest_difficulty_level`, e.g. a manual `CombatEngine.reset()` call. Same "implemented for correctness, currently inert against this project's own envs" category as `excessCritMult`. Regression tests: `test_underleveled_penalty_defaults_to_zero`, `test_underleveled_penalty_scales_with_level_shortfall`, `test_underleveled_penalty_never_applies_when_at_or_above_difficulty` (`tests/env/test_hero_level.py`).
10. **AoE bypassed Guard; Stun didn't break Guard — two separate real bugs, verified against a second wiki fetch for the exact direction.** (a) `_resolve_guard_redirect` was called unconditionally for every AoE target, so a guarded hero standing in an AoE's target ranks was incorrectly protected — now gated on `is_aoe`, matching "AoE bypasses guard." (b) Stun application never touched `guard_target_id`/`guard_source_id` at all. A second wiki fetch confirmed the exact direction: *"whenever a guarding unit is stunned, the guard effect will immediately disappear from the protected unit"* — stunning the **guardian** breaks the guard, not stunning the guarded unit. New `_break_guard_if_guarding` helper (also absorbs the pre-existing `is_clear_guarding` effect branch, same logic) is now called whenever a stun lands. Regression tests: `test_aoe_attack_bypasses_guard`, `test_stunning_the_guardian_breaks_guard` (`tests/engine/test_gaps.py`).

See `CHANGELOG.md` for the full before/after evidence on all five.

### Already correct or moot (verified while investigating the above)

4. **Crit *does* extend DoT duration and application chance.** Re-checked while fixing #2 above — `tests/engine/test_gaps.py::test_crit_extends_dot_duration_and_status_chance` already covers +50%/rounded-up duration on crit via `_resolve_effect`. My original extract flagged this as an unverified "likely gap"; that was wrong. Leaving this line so the correction is visible rather than silently deleting the wrong claim.
6. **Consecutive-stun resist stacking is already correct.** Verified empirically: `_apply_stun_recovery` adds +50% `stun_resist` with no dedup guard, so two stuns within the 2-round recovery window stack (0.9→1.4 for a base-0.4-resist hero) — the resist roll (`rr < stun_resist`, `rr ∈ [0,1)`) naturally saturates at guaranteed-resist once the stat crosses 1.0. No code change needed.
7. **Aegis + 0-damage attacks is moot, not a bug.** `resolve_attack` floors all damage to `max(1, round(applied))` *before* the Aegis check runs, so Aegis always sees `applied >= 1` — there's no code path where "does a 0-damage hit still consume a block" could ever differ from current behavior; the question the wiki raises simply can't arise here. Side-finding, unconfirmed either way: 15 real skill-levels (Hellion's Barbaric Yawp, Plague Doctor's Blinding Gas, Bounty Hunter's Flashbang, and 12 others) carry `dmg_mod <= -0.95` — apparent zero-damage status attacks — which this floor forces to deal 1 HP instead of a literal 0. No wiki/doc text confirms the real game intends exactly 0 rather than a token minimum, so left alone.
11. **Multi-rank enemies are already hit at most once per AoE — moot, not a bug.** Verified empirically against a real size-2 enemy (`brigand_blood_A`) and a real AoE skill (`zealous_accusation`): a size-2 enemy is one `CombatantState` with a single `.position`, and AoE target resolution filters `c.position in positions` — it structurally cannot match twice regardless of how many ranks it visually occupies.

### Known scope boundary, not a fresh gap

5. **Surprise is unimplemented, but this was already a documented, deliberate exclusion** (`ASSUMPTIONS.md`'s Torchlight section: `monsters_surprised_increase`/`heroes_surprised_increase` exist in the raw `rules.json` darkness table and are dropped as "dungeon exploration mechanics with no analog in this single-encounter combat simulator"). Not a new finding — just confirming the existing scope note is still accurate.

The affliction/virtue tables above also give exact per-personality stats for the subsystem `ASSUMPTIONS.md` lists as deliberately unimplemented — enough to build it if that gap is ever picked up.

---

## Combat core

**Rounds & turns**
- Turn order set per round by Speed. Most units 1 turn/round; some bosses multiple; corpses 0.
- **Free actions** (Abomination Transform, some boss abilities) don't consume a turn, don't trigger turn-start effects, and **don't decrement status durations**.

**Ranks & size**
- 4 ranks per side (1 = front). Size: 1 rank (small/average), 2 (large), 3 (gigantic), 4 (unimaginable).
- A multi-rank enemy is **hit at most once per attack** despite occupying several ranks.

**Initiative resolution order**
1. Round speed = `SPD + 1d8`
2. Sort descending
3. **Heroes win ties against enemies**
4. **Lower rank wins ties within a faction**

**Accuracy**
- `Hit chance = ACC + 5 − Dodge`. The hidden `+5` applies after all modifiers → a displayed 95%+ is a guaranteed hit, and there is a practical 5% minimum to hit regardless of Dodge.
- Hidden **+4 ACC per consecutive miss** after the first.

**Damage**
- Hero damage = weapon level × skill base multiplier × modifiers, reduced by PROT.
- **PROT is capped at 80%** — excess has no effect. PROT does **not** reduce Bleed/Blight.
- A few skills ignore PROT entirely (Shieldbreaker Pierce, Grave Robber Pick to the Face).

**Crit**
- Crit damage = **150% of the skill's MAXIMUM damage**, not 1.5× the rolled damage. Modifiers apply as usual afterward. Heroes and enemies alike.
- Hero crit chance = weapon CRIT + skill CRIT + modifiers (additive). Enemy crit is set by the skill alone.
- An enemy killed by a crit **leaves no corpse**.

---

## Status effects

| Status | Duration | Key numbers |
|---|---|---|
| **Bleed** | 3 turns; **5 on crit** | Ticks at start of the affected unit's turn, ignores PROT. 1–9 pts/round. Stacks; stacks expire separately. |
| **Blight** | 3 turns; **5 on crit** | Identical mechanics to Bleed. 0–7 pts/round. Stacks separately. |
| **Horror** | per application | Stress-over-time at turn start. Stacks. **Curable only by Laudanum** — no hero skill removes it. |
| **Stun** | 1 turn | Skips the turn entirely. Post-stun buff grants **50% stun resist**, stacking to **100%** if stunned consecutively. No stacking; overwrites. |
| **Debuff** | 3 turns default | Catch-all stat reduction. Stacks. Medicinal Herbs cures most, but **not self-inflicted** debuffs. |
| **Mark** | until removed | **Cannot be resisted by anyone.** Enables bonus-damage synergy skills. |
| **Move** | — | Forward/Back/Pull/Knockback/Shuffle. Resistible on opponents. Shuffle has **no removal**. |
| **Buff** | 3 turns default; **2 on crit-granted** | Stacks without limit until expiry. |
| **Guard** | 1–2 turns (3 for the defensive buff) | Redirects the guarded unit's incoming attacks. **AoE bypasses guard. Stun breaks guard.** No chains; can't guard multiple units. |
| **Guard Break** | 2 turns | Prevents being guarded. Applied at "500% chance" (i.e. guaranteed). |
| **Riposte** | buff-tied | Counters **any suffered attack, whether it hit or was evaded**. |
| **Restoration** | — | Heal-over-time. **Triggers before Bleed/Blight tick.** Scales with Heals-Received buffs but *not* Healing-Skill buffs. |
| **Aegis** | until spent | Blocks all damage from N direct attacks. Max 2 blocks. **A 0-damage attack still consumes a block.** No effect on non-damaging or status damage. |

### Damage over time
- Ticks at the **beginning of the affected unit's turn**.
- **A crit adds +2 turns of duration and +20% application chance.**
- Base resistance gates **application only**; it is not re-rolled per tick. Resistance rises **+10% per Resolve Level**.
- A DoT tick forces a Death's Door check at round start when it deals damage.
- **A unit killed by DoT leaves no corpse.**
- Per-turn DoT instances cannot crit independently.

---

## Critical hits — stress and healing

- **Hero lands a crit**: self heals **3 stress**; each ally has a **25% chance** to heal 3 stress. An AoE producing multiple crits gives a **single** stress heal, not one per target. No stress heal against corpses unless the AoE also hits ≥1 living target.
- **Hero takes a crit**: self gains **10 stress**; each ally has a **50% chance** to gain **5 stress**.
- **Critical heals**: fixed rate — **12%** for single-target heals, **5% per target** for multi-target. A crit heal doubles the healing and reduces target stress by **4**. Restoration HoT can crit on each tick.
- Crit-granted buffs last **2 turns** including the current one.

---

## Death's Door

- Entry: HP reaches 0. Any further damage triggers a Deathblow test.
- **Base Deathblow Resist 67%** (Flagellant **73%**), **hard cap 87%** even if trinkets/quirks exceed it. Quirks: Unyielding +10%, Weak Grip on Life −10%.
- **At Death's Door**: `−10 ACC, −25% DMG, −5 SPD, +33% Stress`.
- **Death's Door Recovery** (after being healed above 0, lasts the rest of the quest): `−2 ACC, −5% DMG, −1 SPD, +10% Stress`. Does not stack on repeat applications.
- **Heart Attack Recovery** (survived a 200-stress heart attack): `−3 ACC, −10% DMG, −2 SPD, +15% Stress`. ¹
- **Flagellant**: at Death's Door gains `+5 ACC, +20% DMG, +2 SPD, +20% Stun/Blight/Bleed/Debuff resist`; immune to Death's Door Recovery debuffs but **not** to Heart Attack Recovery.

---

## Stress, Affliction, Virtue

- Stress range **0–200**. At **100** → affliction, or virtue on a successful roll. At **200** → heart attack: HP to 0 and Death's Door, or **instant death if already at Death's Door**. A virtuous hero at 200 instead **loses virtue and resets to 0**.
- Virtuous heroes **ignore stress accumulation above 100**.
- Stress resistance **cannot reduce by more than 80%**.
- In-combat stress heals: killing an enemy **3 (50% chance)**; crit-related heals as above; trap disarm 8.

**Virtue chance**: **25% base**, modified −5% per level below dungeon level (−33% at 6+ difference), up to +45% from trinkets, clamped to **1%–95%**. On gaining virtue, stress drops to **45** and the party's stress reduces slightly.

**All afflictions**: `−15% Stun/Blight/Bleed/Disease/Debuff/Move/Trap Resist, −10% MAX HP`.

| Affliction | Stats | Act-out |
|---|---|---|
| Fearful | −25% DMG, +10 DODGE, +2 SPD | 31.5% |
| Paranoid | −25% DMG, +10 DODGE, +2 SPD | 31.25% |
| Selfish | −5 ACC, −10% DMG, +5 DODGE | 33.33% |
| Masochistic | −15 DODGE | 33.33% |
| Abusive | −5 ACC, +20% DMG, −15 DODGE | 29.4% |
| Hopeless | −5 ACC, −5 DODGE, −3 SPD | 35% |
| Irrational | −5 ACC, −10% DMG, −5 DODGE, +2 SPD | 33.33% |
| Rapturous | +25% DMG, −20 DODGE, +3 SPD | 41.67% (Flagellant only) |

**All virtues**: `+25% Stun/Blight/Bleed/Disease/Debuff/Move Resist`.

| Virtue | Stats | Act-out (25%) |
|---|---|---|
| Stalwart | +15% PROT, +8% Deathblow Resist | −15 own stress |
| Courageous | −33% Stress, +2 SPD | −4 stress each other hero |
| Focused | +10 ACC, +8% CRIT | buff an ally +10 ACC, +10% CRIT |
| Powerful | +25% DMG | buff allies +15% DMG |
| Vigorous | +4 SPD, +10% DODGE | heal self 10% MAX HP |

**Act-out rules** (both afflictions and virtues): cannot fire twice consecutively on the same hero, and two different heroes cannot act out back-to-back. One action is selected, not rolled per-option. Affliction act-outs include: stress the party, move, pass, random skill, attack an ally, attack self, mark self. **Self-attacks deal 10% MAX HP** with no other effects. Afflicted barks deal **6 stress to the whole party** (hero attacked, or bark from an act-out) or **6 to a single ally**.

---

## Stalling

A round counts as a stall when **both** hold:
- **two or fewer enemies alive** at end of round, **and**
- the party used **one or zero active skills** that round.

"Active" = deals damage to an enemy. Missing, or hitting a corpse, still counts as active. Exceptions: Occultist's Vulnerability Hex deals damage but is **not** active; Jester's Solo **is** active despite dealing none.

| Enemy kind | Stress + bark | Reinforcements |
|---|---|---|
| Normal | after 3 consecutive stall rounds | after 4 |
| Accelerated | bark only after 1, **no stress** | after 2 |

- The timer **resets** on using two active skills.
- **Stalling is fully disabled** against: any large enemy (2+ ranks), all bosses and most boss minions, Shieldbreaker DLC snakes, Necrotic Fungus, during Farmstead, the tutorial Ruins run, and **parties of 2 or fewer heroes**.
- Reinforcements can be any enemy that appears in the current dungeon and **drop no loot**.
- Accelerated enemies: Bone Arbalist, Bone Courtier, Bone Rabble, Bone Soldier, Carrion Eater, Collected Man-at-Arms, Collected Vestal, Crone, Fungal Artillery, Madman, Maggot, Pelagic Guardian, Pelagic Shaman, Swine Drummer, Swine Skiver, Webber.

---

## Light, Surprise, Corpses, Enemy types, Resolve

**Light bands** (start 100; −1 explored / −6 new area / −7 Stygian):

| Band | Range | Hero effects | Monster effects |
|---|---|---|---|
| Radiant | >75 | +7.5 DODGE, +1% CRIT, +15% scout, 25% surprise *them* | — |
| Dim | 75–51 | +4 DODGE, +1% CRIT, 15% surprised | +5 ACC, +10% stress, +25% loot |
| Shadowy | 50–26 | +2% CRIT, 25% surprised | +10 ACC, +10% DMG, +2% crit, +20% stress, +50% loot |
| Dark | 26–1 | +3% CRIT, 40% surprised | +12.5 ACC, +12.5–17.5% DMG, +3–3.5% crit, +30–35% stress, +75% loot |
| Black as Pitch | 0 | +3% CRIT, 40% surprised | +12.5 ACC, +12.5–30% DMG, +5–6% crit, +40–50% stress, 1–12% Shambler ambush |

Note the inversion: **darker light raises hero CRIT** as well as every monster stat.

**Surprise** — 10% either direction in unscouted rooms; scouted areas use the light table above; max 65%. Surprised party is **shuffled** and cannot escape on turn 1; surprised enemies **act last on turn 1**. Impossible in the Darkest Dungeon and Farmstead; unavoidable vs Shambler/Fanatic/camping ambush.

**Corpses** — not created by death from **bleed/blight or a crit**, nor by small/gigantic/unimaginable enemies. Persist until the end of the 4th round.² HP is a fraction of the monster's max, with **no PROT or DODGE**; **0% Bleed/Blight resist, 200% Move/Debuff/Stun resist**. Not selectable by random targeting unless nothing else remains. Corpse-clearing skills hit all corpses at once and need not target them.

**Enemy types** — Human, Beast, Unholy, Eldritch, Stonework, Bloodsucker, Husk. Enemies can hold several tags (Swine are Beast + Human; Rapturous Cultist is Human + Unholy; Drowned Thrall is Unholy + Eldritch). Tags gate bonus-damage skills and trinkets: Crusader/Vestal vs Unholy, Occultist vs Eldritch, Houndmaster's Hound's Rush vs Beast, Sculptor's Tools vs Stonework.

**Resolve Level** — 0 Seeker … 6 Legend. Each level grants **+10%** trap-disarm, stun, blight, bleed, move and debuff resist. Apprentice/Veteran/Champion admit resolve 1-2 / 3-4 / 5-6. A hero **below** quest difficulty takes **+20 stress and +25% stress damage per level of difference**.

---

¹ The project's parsed `deaths_door:` record stores the heart-attack stress term as `−15%` where the wiki states `+15% Stress`. These agree in meaning — one is a stress *resist* reduction, the other stress *damage taken*. Don't "fix" the sign.

² **Corrected 2026-08-20 — the wiki was right and this note was wrong.** It used to read *"Data wins — the wiki's 'end of the fourth round' is likely counting inclusively"*, which dismissed the one source that actually disambiguated the number. The file gives `life_time: .alive_round_limit 3` but **not the comparison**, and the two readings differ by a whole round; the wiki's worked example (*killed in round 1 → corpse until the end of round 4 → slot free in round 5*) supplies exactly the missing half. Fran confirmed it from play. The engine now excludes the round the corpse appeared in, so `CORPSE_ALIVE_ROUND_LIMIT` stays at the real data value of 3 and the corpse survives four round-ends. This is the precedent for the precedence rule itself: game files outrank the wiki **on a number**, not on the semantics of a number they never state.
