# NEXT — the pending queue

## 2026-10-10: THE CLOSING ARC IS `docs/CAMPAIGN_SIM.md` -- read it, and only it

Fran's call: the live bridge is frozen (fix only what blocks a demo); all
night work goes to the campaign simulator. All DLC, Darkest difficulty, no
mods, nothing of Red Hook's published, hard stop mid-December. F0 is measured
(≤0.1 s per simulated expedition, no surrogate needed); **F1 is done (2026-10-10)**:
the simulator plays whole campaigns with the bridge's own decisions;
the baseline and the three F2 targets (loadouts, progression, the DD) are
in `docs/CAMPAIGN_SIM.md` under "F1 result". Everything
below this heading is the old queue, kept as reference only.

**Fran's rulings, 2026-10-12** (on the 2026-10-11 night's open calls; the
campaign was 31 dead / 60 weeks at gear 0.5, from 66). Each ships as the
default; the measurements are CAMPAIGN_SIM.md E19-E22:

1. **Boss parties at the highest level the quest allows**, fully equipped
   (replaces the margin question). E20.
2. `SERVICE_DISCOUNT_MAX_RANKS = 1`: measured null; off.
3. **Death's Door: train it.** `--dd-exposure-penalty 10` against a
   retrained control, two seeds, `tools/deaths_door_eval.py`. E22.
4. Unspent gold: the Sanitarium (5) is what spends it now.
5. **The Sanitarium: option 4** -- diseases, the worst negative ranked 1-5,
   else lock an S-tier. Planned, not actuated live: its panel needs a
   measuring session. E21.
6. **Smith first**, with the vanilla roster managed (`rostersize` first when
   the roster is full). E19.
   **Open, for Fran:** the live post-tutorial step (PLAYBOOK line 110:
   upgrade the coach's top path, then week-skip) now spends the starting
   heirlooms on the Guild and the Blacksmith instead, so that visit buys
   nothing at the coach and the coach keeps offering its base 2 recruits.
   1. Leave it: smith first from the very first visit.
   2. The post-tutorial visit alone keeps the coach-first order
      (`townplan.COACH_FIRST_PRIORITY`), smith first from then on.
   **Fran, 2026-10-12: "i dont mind, whatever works best for the model".**
   **Settled by the game's own gates** (Fran: *"On the first weeks not all
   buildings are available"*). The Guild and the Blacksmith open after 3
   quests (`townplan.closed`, 0efbaa9), so after the tutorial smith first
   spends the heirlooms at the coach. Option 2 is identical to option 1 and
   was dropped. The gates are a world step, measured as CAMPAIGN_SIM E23.

**How to use this in a new session: say "read docs/NEXT.md" (or just "pick up
NEXT").** Everything below is agreed work that is *not* finished. Each item says
what it is, what the evidence is, and the first concrete command. Delete an item
when it lands and put the evidence in `CHANGELOG.md` where it belongs.

Header rewritten **2026-08-29**; the top block is **2026-09-04**, then
**2026-09-03** and **2026-09-02**. Everything under the dated PHASE/N headings
below is older; read the top section first, because two of its standing claims
have since been overturned.

---

## 2026-10-05 overnight: the dice are known -- read this first

**Found:** the battle RNG is minstd and the save holds its turn-start state,
so every hero turn's hit roll, first-target crit roll and damage roll are
known before acting (DECOMP.md F15; 67/67 turns, 50/50 hit rolls, 62/62
damage values). Resolved per attack they predict the outcome 99.8% (hit) /
99.1% (crit) of the time.

**Measured in the simulator (n=1200 paired, `tools/fate_eval.py` +
`tools/pair_runs.py`):** worth about one point. `--fate-obs` seed 0 +1.1pp
(SE 0.8, p=0.21); the critic veto ~0; a facts-only dice veto +0.1pp. The
simulator is near its SUCCESS ceiling (92.7%), and **58 of its 88 failures
are ABANDONS**: masking abandon is +1.2pp SUCCESS (p=0.0001) but +0.044
deaths/run, so it is a trade for Fran to rule on, not a bug.

**Running / to read when back:**
1. `models/fate_obs_s1.zip` (seed 1 replicate; `archive/logs/train_fate_obs_s1.log`).
   Then `python tools/fate_eval.py --model models/fate_obs_s1.zip --episodes 1200
   --workers 3 --arms policy --json archive/logs/fate/p1200_fate_s1.json` and pair it
   against `p1200_base_s1.json`. Promote only if both seeds clear 2 SE.
2. Not yet run: `--fate-outcomes` (the dice resolved per action, +87 obs):
   `python train.py ... --fate-outcomes --seed 0 --out models/fate_out_s0.zip`.
3. **Live session, 2026-10-05 morning (done):** profile_0, `--fate`, trace
   recorder on. Fate forecasts came true **38/41** in the running game
   (`tools/fate_live_check.py`; the 3 are turns with no attack). First Nomad
   Wagon ranks ever, save-confirmed. One raid completed: **7,090 gold** of
   treasure plus the 3,000 reward. Damage oracle 49/49, still 0
   discriminating hits (Cove/crypts PROT values agree under both rules).

**Open from that session, in order:**
- **The full pack.** It paused on `--full-pack pause` (the default) -- that
  was the "stuck on inventory" Fran saw. `--full-pack swap` (built) trades the
  cheapest stack of value under 250g and otherwise walks on. FRAN TO RULE:
  should surplus FOOD (16 held against a reserve of 4) be traded first? It
  is excluded on purpose by the hunger budget and his "eat to Full" ruling.
- **Abandons** are 58 of the simulator's 88 failures; masking them is
  +1.2pp SUCCESS and +0.044 deaths/run. Fran to rule.
- A party drag that did not seat (3 of 4; bar trusted, embark refused) --
  retried fine the next iteration; `(1097, 226)` was the failing source.
- A locked sarcophagus given up (the skeleton-key verb), and the torch snuff
  failure rate (timing ruled out: Fran's CTRL+SHIFT click median 0.27 s).
- The run paused on the game's HELP CARD at the end -- Fran's own pause key.
- PROT (F9): play Ruins/Warrens with weak hitters into 15% PROT under
  `batecord_play_full.bat`, then `python tools/damage_oracle.py --all`.
- The full test suite was NOT re-run after the night's changes (a training
  job was alive the whole time). Run it first, alone.

---

## 2026-10-04: the ground-truth loop -- `docs/GROUND_TRUTH_LOOP.md`

The whole binary is decompiled and named (DECOMP.md F8-F14). Fran asked for a
system that uses it everywhere; the design, its order of work and each step's
falsifier are in `docs/GROUND_TRUTH_LOOP.md`. Built: `tools/decomp_lookup.py`,
`tools/rules_audit.py`, `data/decomp_facts.json` + its test,
`bridge/game_options.py`. **First step: one session with the recorder AND the
debug channel running, which settles F9's PROT rule** (11.6% mean
expected-damage gap, staged).

---

## NEXT SESSION: two things Fran saw live on 2026-09-28 -- check these first

1. **The combat policy never uses the Runaway's Run and Hide.** Is
   restoration (Run and Hide's heal/restore effect) modelled in the combat
   engine at all? Check the skill's payload resolves (see the memory "An
   empty effect list hid a dead skill": Backdraft carried its payload in
   unparsed keys), then whether the engine applies it, then whether the
   policy ever picks it (journal `play` events, `tools/journal_report.py`).
2. **(Investigated 2026-09-28: Run and Hide + stealth FIXED in the engine,
   awaiting a retrain. The comp ORDER is a post-fight problem: the Default
   Party order click does not restore the marching order -- 38 of 41 next
   fights out of order. Numbered frames of the first five cases per run are
   kept in `captures/_party_order_N_*.png`; read them first.)**
   **The comp ORDER looks wrong since the changes.** Something changed the
   formation of the embarked party. Suspects from 2026-09-27/28: the skill
   loadout step (`town.select_loadouts`), the quality-based hires/swaps, and
   the seating (`seat_party` with learned roster positions; one "page
   drifted" seat on run w). Compare the planned ranks (`[town] rank N ...`
   lines) with the party bar order in the raid log's "marching order is ..."
   line, on `archive/logs/overnight_profile0_[t-w].log`.

## NEXT SESSION: curios, nurture one at a time, food, granary, pace (Fran, 2026-09-27) -- read this first

Fran's agenda at the close of the 2026-09-27 night session.

**STATUS 2026-09-28** (changelog 2026-09-28):
- 1 curios: measured -- 33 of 37 give-ups were a FULL PACK, not a missed W.
  Built: top-up fills open stacks only; surplus supplies make room at "no
  room". Traps: the disarm is decided (`bridge/traps.py`) but
  `DISARM_MEASURED` is False -- needs a live frame of a scouted trap.
- 2 nurture: BUILT and flown (Antiquarian #749: skills, gear, an unlock, all
  wallet-witnessed). Blacksmith measured. Sanitarium quirk locks still open.
- 3 food: the "Full" state was already handled by the actuator (one refused
  portion marks the hero full for the raid); hunger reserve exists.
- 4 Granary: the save's `districts.granary.built` is read and the price
  (2,500 g + 60 bust) is held back while a blueprint waits (2/60 bust now).
  The district PURCHASE UI is not measured yet.
- 5 pace: measured (combat 4.1 h, out-of-combat 9.5 h of 15.2 h); the room
  heartbeat is 12 s now instead of 25 s. The Hamlet plan
below it (2026-09-26) is BUILT and flew all night; its status line says what
is left of it.

### 1. Missing curios and traps: use W more

Curios and traps get walked past or given up. Fran: *"it needs to use W more
in general"*.

- Evidence to collect first: grep tonight's raid logs for
  `giving up on`, `walked past it`, `no panel on screen`, `did not respond`
  (`archive/logs/overnight/iter_20260927_*.log`) and count by cause. Several
  "did not respond" were a FULL PACK (the "no room" modal), not a missed W.
- Traps: a disarm is also a W at the trap tile; check whether the route ever
  tries it or only walks through.
- Falsifier: curios used per raid, before and after, on the same logs.

### 2. Nurture heroes ONE AT A TIME, and max them out

Fran: finish everything that can be done for one nurture hero before starting
another. A hero's resolve level rises with raids, and each level opens new
caps: skill ranks, weapon and armour ranks. So a maxed hero becomes eligible
again after levelling.

- Pick the top nurture hero (`bridge/hero_roles.py`, nurture score).
- Spend on them, under the gold cap: Guild skill ranks and unlocks,
  Blacksmith weapon and armour, Sanitarium quirk locks (panel not yet banked),
  up to what their resolve level allows.
- Move to the next nurture hero only when the current one has nothing left
  to buy at their level.
- Re-check each visit: a level-up re-opens the same hero.
- Seams: `townplan.advise`, `_visit_guild`, `buy_building_upgrades`, a new
  Blacksmith hero-upgrade step, the Sanitarium.

### 3. Food: the "Full" state and a hunger budget

- Fran: right-click a hero and a grey **"Full"** text over them means they
  were fed recently and cannot eat again. That explains tonight's "the food
  was not eaten" on a 1/18-HP Occultist, planned every room. Read that state
  before planning food (screen, or find it in the save), and stop re-planning.
- Carry enough food: estimate hunger checks per remaining dungeon length
  (average), and keep that much food in reserve. `data/provisions.py` has
  `minimum_food`; the hunger-check cadence is in shipped data
  (`rules.json` / the raid settings); derive, don't invent.

### 4. Blueprints: the Granary first

Beating bosses pays blueprints. Fran: with a blueprint, the **Granary** is the
first building to buy, for the extra food and healing it gives the party.
Tonight's wallet showed `blueprint: 1`, so check whether one is already
spendable. Needs: where the district/building purchase is drawn, and the
Granary's effect in shipped data.

### 5. Pace: what can be made faster

Assess where the time goes: combat turns, room plans, town visits, the
out-of-combat save lag.

- Tonight's figures to start from: an iteration of 3 raids took 45-73 min.
  Long Cove clear-all maps hit the 1,800 s play allowance mid-raid.
  "no save change in 15s - the input did not land" after torch and map
  sends out of combat is the save answering late, and it costs 15 s each.
- Candidates: witness out-of-combat sends on the screen instead of the save,
  shorter settle waits in the town panels, fewer re-plans per room.
- Measure first: time per phase from the journal (`*.jsonl`) before
  changing anything.

### Carried open from 2026-09-27

- The trinket chest's scroll (only 21 cells reachable; the planner is limited
  to them).
- The pack reads full at 13-14 of 16 by the save while the game shows
  "no room".
- A Leper's meditation not confirmed on the first click (guarded by a second
  confirm, not explained).
- One unconfirmed Houndmaster hire after its swap dismissal.
- Fran to check: nurture top-3 (Runaway #835, Plague Doctor #884,
  Jester #1127) and Mortmain as fodder.
- Nothing from 2026-09-26 and 2026-09-27 is committed.

---


## The Hamlet, nuanced (Fran, 2026-09-26) -- BUILT 2026-09-27

Fran's spec, given after the 2026-09-26 session. It replaces the flat rules
from that day (4 per class+level; "never level 0"). Build it in the order
below: each step feeds the next.

**STATUS 2026-09-27: steps 0-6 are BUILT** (changelog 2026-09-27; flown in the
overnight run `archive/logs/overnight_profile0_m.log`). Left open: the
Sanitarium quirk lock (panel not banked), the nurture LUXURY spends beyond
treatment (Guild/Blacksmith for nurture heroes), and the trinket-fit term in
the comp draw. Fran to check: the nurture top-3 (Runaway#835, Plague
Doctor#884, Jester#1127) and the Mortmain verdict (fodder, 14,111g to buy
against 0 sunk).

### 0. The hero model everything else reads: `bridge/hero_roles.py`

Every decision below needs the same three facts about a hero, so compute them
once:

- **Level band**: `0-2`, `3-4` or `5+` (resolve level).
- **Role**:
  - `invested`: level 1+ with real investment (see below);
  - `nurture`: a lower-level hero with a great quirk *for their class*;
  - `fodder`: everyone else, the dismissable ones.
- **Sunk investment in gold**, counted from the save:
  - skills unlocked (1,000 each; stance heroes arrive with some free);
  - skill levels;
  - weapon and armour ranks;
  - camp skills (Experienced Recruits arrive with 4 instead of 3);
  - locked quirks;
  - trinkets worn, valued by the team-builder.

  `retreat.party_investment` already prices part of this; extend it, don't
  fork it.

- **Nurture rank is almost all QUIRKS**, and not just the global tier list.
  A quirk's worth depends on the class, and the curated comps say which
  quirks each class actually uses. Score = tier of each positive quirk,
  weighted by how often that quirk appears for this class in
  `metadata/COMPS/curated_comps`, minus the negatives.
  - Evidence to collect first: which quirk fields the curated comps carry.
  - Falsifier: the top-3 nurture picks on the live roster should be heroes
    Fran agrees with. Show him the list before anything acts on it.

### 1. Roster cap by level BAND, and it tightens as the roster levels up

Replaces `MAX_PER_CLASS_LEVEL = 4` (`bridge/townplan.py`):

- at most **4** per class in band 0-2, **4** in band 3-4, **5** in band 5+;
- the higher the roster's average level, the LOWER the cap for the low
  bands ("we want to keep advancing, not the other way around").
  Proposed: the 0-2 cap drops by 1 per full level of roster mean above 2,
  floor 1. That is a convention: confirm with Fran.
- Dismissal picks FODDER first, never a nurture or invested hero.
  `cap_dismissals` and `dismiss_surplus` stay; only the grouping and the
  ordering change.
- Test: a synthetic roster at mean level 3 keeps fewer 0-2 heroes than the
  same roster at mean level 1.

### 2. Who gets the Hamlet: fodder never, nurture as a luxury, invested as needed

- **Fodder** gets provisions and nothing else: no Guild, Blacksmith,
  Sanitarium or Abbey. When it wears out it is dismissed and replaced from
  the coach. This supersedes the "S-tier quirk or heavy gold" exception of
  2026-09-26 for level 0.
- **Nurture**: level up, lock the great quirks (Sanitarium), weapon and
  armour (Blacksmith), skills (Guild), but as a LUXURY. Only from money
  above a reserve (the 10% cap stays), and ranked by nurture score.
- **Invested**: keep usable (stress treatment) under the 10% cap.
- **Afflictions** are removed only by town treatment, and only worth it for
  a really good hero (nurture, or top-value invested).
- **The "is it worth it" bill**, per hero, before any spend:
  - the sunk investment plus what still needs buying (skills, levels, gear,
    stress, affliction);
  - weighed against the hero's remaining value (level band, quirks,
    whether their role is filled by someone better);
  - Fran's worked case: Musketeer Mortmain, level 1, afflicted, with an
    eagle-eye quirk, not all skills unlocked, skills at level 1. Does she
    get stress relief, skills, gear or a quirk lock, or has she "already
    done her role"? The model must print its answer and the numbers for
    this hero first, for Fran to check.
- Code seams: `choose_treatments`, the Guild unlock budget in
  `embarkplan._decide_embark`, `buy_building_upgrades`, and a new
  Sanitarium quirk-lock step (panel not yet banked or measured).

### 3. The stage coach: Experienced Recruits change who is worth hiring

A level-1 recruit arrives with:

- all skills unlocked (~3k of Guild spend saved; stance heroes excepted);
- every skill at level 2;
- weapon and armour at level 2;
- 4 camp skills instead of 3.

That is a large free investment:

- `_recruit_quality` and the hire choice rank level-1+ recruits well above
  level-0 ones.
- The recruit record already carries `resolve_xp`, `weapon_rank`,
  `armour_rank` and `skills`, so `recruit_level` can say which is which.

### 4. The comp draw: one level band, invested first, room for one nurture

Extends the weighted flip in `embarkplan._decide_embark`:

- all four heroes in the band that is optimal for the quest's difficulty
  (the resolve cap already gates the top; the floor is new);
- weight toward the most INVESTED, non-stressed heroes. Trinkets count: a
  comp that uses a strong trinket the roster owns for its class weighs more;
- optionally ONE under-levelled nurture hero, to keep investing in them;
- the embark veto (`bridge/embark_veto.py`) still gates the draw.

### 5. Loadouts before embarking

For invested and nurture heroes, pick the comp's optimal four skills (the
curated comp's skills, falling back to the team-builder) and SELECT them in
the Hamlet before embarking.

- The hero sheet shows the skill row. Measure how selecting and deselecting
  a skill is done: a lab item, with the sheet banked.
- Today the party embarks with whatever skills were last selected.

### 6. Trinkets: the chest's own buttons, and more trinkets in use

The Trinket Inventory header has buttons (the icons at client ~(893, 111),
(920, 111), (947, 111), (976, 111) in `captures`; identify each live):

- **Unequip all**: before every embark, so the planner sees the whole pool.
  This also makes the "strip benched heroes" pass unnecessary (delete it
  after).
- **Sort by rarity**: gives a known order (rarity, then id), so a cell can
  be predicted instead of assumed from the save key.
- **Sort by name**: the fallback order.

Then equip MORE: Fran's view (in the dd-team-builder) is that almost every
trinket helps if it suits the hero. Check `reequipParty`'s thresholds; it
equipped only 1-3 of ~20 owned on the live roster.

- Falsifier: after unequip-all plus a plan, the party should wear 6-8
  trinkets, not 1-3.

### Carried open from 2026-09-26 (still true)

- **The Abomination treatment was not flown.** The treatment path (drop +
  confirm + wallet witness) is built and was proven by hand once.
- **Two curios given up that the save later shows as used**: the save lags
  out of combat, so this needs a screen witness.
- **Horror's save `stat_type`**: needs a Cove fight against Drowned Pirates.
- **The out-of-combat party swap**: still unproven.
- **The simulator's in-combat items**: stage for the next retrain.

---

## THE NIGHT OF 2026-09-25 -- what it left open

The night's landed work is in `docs/changelog/2026-09.md` under 2026-09-25:
trinkets equipped (and stripped off benched heroes), non-coach building ranks
bought in their own panels, the known enemy groups in the forecast, the
corridor loot loop, the curio false negative, and stuck walking keys. Open:

1. **The out-of-combat marching swap has never been seen to work.** In 40
   logged attempts it logged 41 "the swap did not reach the save" and no
   success. Sometimes the next poll does show the marching order (a late
   save), and sometimes the same wrong order repeats. Live, the three inputs
   after a failed swap (a torch and two map clicks) did not land, so the `5`
   (MOVE) press may leave a targeting mode up. **Lab:** out of combat, press
   select + `5` + a rank and grab a frame; then try dragging one hero sprite
   onto another. First command: `python <scratch>/lab.py` as used on
   2026-09-25 (grab / move / click / drag / key with a frame after).
2. **Hallway stress is ONE HERO PER TILE** (ASSUMPTIONS, "hallway stress",
   update 2026-09-25: 61 of 97 walk-only events changed one hero). It is
   unimplemented and it changes the training world, so it goes in with the
   next retrain, not under a live checkpoint.
3. **`battle_seed` is in the raid save**, and the game logs a seed per random
   draw (DECOMP F3). If the RNG can be recovered from Darkest.exe, fights
   become predictable. This is a long Ghidra job; the falsifier is predicting
   one logged roll from the seed.
4. **Town visits: the roster scroll is fast and witnessed now** (2026-09-26).
   Time the next full visit. What remains slow is seating (its own
   drag-probe walk) -- measure before touching it.
5. **In-fight provisions are live in the BRIDGE** (2026-09-26: free action,
   measured). Left: (a) the SIMULATOR -- `IN_COMBAT_PROVISIONS_ALLOWED` must
   go True with item use as a free action (not a turn) at the next retrain,
   and food/dog treats need action slots; (b) bank `blacksmith_panel` (the
   Abbey is banked); (c) Horror's `stat_type` needs a Cove fight against
   Drowned Pirates captured.
6. **The retreat vs death price with a cheap roster.** With 33 heroes and a
   free coach, `hero_value` is the quest share (~750). A 2.2-expected-death
   push read "worth pushing on" until HP fell further; the raid then retreated
   cleanly at 2.9. Fran's ruling stands; this is data for his next review.
7. **Skill unlocks were draining the gold** -- ruled and done 2026-09-25
   (weighted flip, no level-0 unlocks, 10% cap; see the changelog).

---

## NEXT SESSION: FRAN'S CALLS FROM 2026-09-23 -- read this first

Fran reviewed the 2026-09-23 night and set the agenda. **Done offline the same
evening:** retreat floor scaled by investment (`SCALE_FLOOR_BY_INVESTMENT`
now True), a `loot_ahead_gold` term in `advise_retreat`, and the coach hires
every offer (`advise(roster_room=None)`). Tests pin all three. Everything below
is still open.

### A. The save is past the point of no return -- restore or rebuild (LIVE, with Fran)

Gold near zero, 35 living heroes, most level 0 and over 50 stress. Two ways
out, and **Fran chooses**:
1. Restore `archive/saves/profile_0_baseline_20260921/` (see its MANIFEST).
   Steam Cloud can put a replaced profile back, so do it with Fran and check
   `app.log` plus the save timestamps afterwards. Or he provides a new profile.
2. Rebuild in place: recruit EVERY coach offer; dismiss every level-0 hero over
   50 stress; keep ONE stressed hero to skip a week in the Darkest Dungeon
   region (ask Fran for the exact trick); recruit 4-6 more; spend nothing at
   the Guild, only on provisions, stretched as far as possible; **no
   retreating**.
First command: `python -c "from bridge.town_state import read_town_state; ..."`
to count level-0 heroes over 50 stress. That count is the dismissal bill.

### B. Dismissal actuation (LIVE discovery)

Fran: *"open their tab and click on the orange x on the top left corner, and
click yes to confirm"*. Needs: the hero panel's orange X point (measure live,
since mods move pixels), then the confirm on `MODAL_OK`, witnessed by the
guid leaving `persist.roster.json`. Planning exists: `townplan.choose_dismissals`
(no S/A/B quirk AND level 0 or below mean). Add Fran's reset rule: level 0 AND
stress > 50. Falsifier: roster count drops by exactly one per confirmed click.

### C. Seating: multi-click paging and the sort buttons (LIVE, then offline)

- `learn_roster_positions` scrolls ONE click per probe. Fran: 3-4 clicks at a
  time. Do it as a page jump: scroll 8 and learn all 8 rows, keeping the
  strip-moved check per page.
- The list has LEVEL and STRESS sort buttons above it, and a second click
  flips the order. Sorting by stress puts the clean heroes on page 0, which
  is where a comp wants to draw from. Measure both buttons' points live, and
  check that the sort survives a scroll and a re-open.

### D. Retreat pricing, the rest of Fran's ruling (OFFLINE)

- Wire `loot_ahead_gold` from `run.py` (the "LOOT ... still on the map"
  value) through to `advisor.py:735`. The parameter exists; nothing passes it.
- Poverty: *"retreating is worse the less money you have"*. Weight the
  forfeited reward by how short the wallet is of the next basket. Needs a
  rule from Fran, not a constant from me.
- Trinkets: price equipped trinkets into hero value, and treat a dead hero's
  trinkets as recovered only when the fight is won (Fran: *"those trinkets
  should almost always be looted"*). Verify the game rule in DECOMP/wiki first.
- Level-0 heroes are expendable, and the investment scaling now encodes that.
  Measure it on `bat/play.log`: fewer entrance retreats, deaths accepted.

### E. Still unverified live from 2026-09-23

PASS after two failed rotations (`_answer_phantom_fight`); the dim-map
`INK_DIM` retry during a real raid; the no-enemy-bars nudge.

### F. From the fact audit (2026-09-23, night) -- open

- **Forecast calibration on VALID raids.** Fran: the current raid (no
  provisions, several clean fights) was forecast at 50% intact and 1.4 deaths.
  Measure with `python tools/forecast_vs_game.py --since 20260923_2200` once valid raids exist. Log forecast against outcome per fight from now on, and treat the rollout as
  miscalibrated until measured. `DEFAULT_EPISODES = 20` is also too few for a
  verdict (+-11 points).
- ~~**Retreat fail chance in the decision.**~~ RESOLVED by design 2026-09-23 (see CHANGELOG). `combat_retreat_chance` 0.70
  (+0.05 per attempt) is in the engine; `advise_retreat` still assumes a
  retreat always succeeds. Fran agreed it should be fixed in both.
- **Provisions from game averages** -- DONE 2026-09-23, torches too since 2026-09-24 (`bridge/provision_needs.py`). Was: except TORCHES, which still use the table; derive the light drain per tile next. Was: (PLAYBOOK 7.7): derive food,
  torches and shovels from the dungeon's shipped data (obstacle and curio
  frequencies per zone and length), not the community image.
- **Retreat rate target: at most ~20% of dungeons** (7.1). Needs the
  scoreboard to measure.
- ~~**Don't burn overnight iterations while the help card pauses the bridge.**~~ DONE 2026-09-23.
- **Use bandages and antivenom BETWEEN fights** (Fran: *"the model is not using provisions"*). The game forbids items in battle; after a fight, a bleeding or blighted hero (the save's `actor_dot`) should get a bandage or an antivenom when the pack has one. Needs the inventory's use-on-hero click measured live.
- **The corridor curio approach barely lands** (2026-09-24, two raids: 37 attempts, 26 "no panel after W", sconces and packs "did not respond"). Needs a live session on where the party really stands in a corridor and when W opens a panel.
- **A room at the map panel's edge cannot be fitted** (`rooK`, 2026-09-24): pan the map before fitting when the target's predicted point is near the panel edge.
- **Upgrades outside the coach are unflown.** With PLAYBOOK 7.4's order the planner now buys Guild ranks, and live 2026-09-23 both came back UNCONFIRMED ("the wallet did not move"). The upgrade actuation was only ever built and measured for the stage coach; each building's upgrade panel needs its own points, measured live.
  Five iterations of run 12 were spent paused.
- **Flaky**: `tests/training/test_vec_env.py::test_every_mode_vectorises`
  intermittently hangs in `SubprocVecEnv` start-up under xdist on Windows
  (it passed alone; unrelated to 2026-09-23's changes).

---

## What is in this queue

Derived from the h2 headings, which carry their own verdicts. Sections
this file marked SUPERSEDED / RETRACTED / RESOLVED / CLOSED / SETTLED /
PHASE CLOSED moved to `docs/next-archive.md` on 2026-09-07; nothing else
was reclassified, because mistaking an open item for a closed one is the
expensive direction.

- **THE BRIDGE PLAYS A CAMPAIGN AGAIN, AND ONE EXIT IS STILL MISSING**  _2026-09-20_
- **THE COMMUNITY GUIDES ARE TRIAGED, AND FOUR ITEMS CAME OUT OF THEM**  _2026-09-19_
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

## THE LOOP RUNS UNATTENDED (2026-09-21)

`tools/overnight.py --max-iterations 3 --raids-per-campaign 2`, against the
real game, with Fran off the machine:

| iter | outcome | raids | towns | embarks | actions | refusals |
|---|---|---|---|---|---|---|
| 1 | played | 1 | 0 | 0 | 116 | 9 |
| 2 | played | 1 | 1 | 1 | 885 | 64 |
| 3 | played | **2** | 1 | 1 | 620 | 51 |

**4 raids, 2 embarks, 1,621 actions, and no stray saves left on disk** -- the
teardown removed every campaign it made. Iteration 3 finished BOTH raids,
including a generated crypts map.

Seven fixes got it there, all in `docs/changelog/2026-09.md`: the red seal as
the raid exit, the scrolled save list banked, `claim_created` owning what the
game made instead of what was predicted, the orphan sweep, the empty-letter
search, the stall `explain_again`, and the right-click verb for items on
curios.

### What is still costing the loop, in order of size

1. **A long generated raid can outrun the 1800 s allowance** -- iteration 2's
   second raid was cut off mid-dungeon at 885 actions. Either the allowance
   goes up again or the raid needs to be faster; the poll loop spends most of
   its time waiting for save acknowledgements.
2. **The results chain is occasionally sticky.** Iteration 1 clicked `Next`
   on `raid_results` four times with no movement and lost the iteration's
   town visit; iterations 2 and 3 walked the same chain without trouble. The
   page animates, `SAME_SCREEN_TRIES` is 4, and the retry may simply be
   landing inside the animation every time. **Falsifier**: a capture of the
   results page where `Next` is not yet live.
3. **The torch snuff still has no acknowledgement** and reports failure even
   when the clicks land -- see item 3 below, unchanged.

## THE BRIDGE PLAYS A CAMPAIGN AGAIN, AND ONE EXIT IS STILL MISSING (2026-09-20)

Unattended run, `tools/overnight.py`: boot -> the Old Road -> Return to
Hamlet -> a town visit -> the crypts quest -> four heroes seated -> embark ->
a generated dungeon. Three blockers were fixed to get there (all in
`docs/changelog/2026-09.md`): the launch was missing `-size 1280 720` so every
capture was frozen, both named checkpoint slots still held the 1652 file so
there was no combat policy, and `Continue Raid` stranded the party.

### 1. ~~Find a second way out of a raid~~ -- RESOLVED 2026-09-20

It was the abandon-quest banner, top left, measured since 2026-09-01 and
named by the game's own help text. `plan_leave_raid` sends it. See
`docs/changelog/2026-09.md`.

### 1b. Play in the BACKGROUND -- the gamepad route is MEASURED and CLOSED

Fran: *"I won't be able to provide the bridge unless you find a way to work
on the bridge on 1 screen and I work on the other"*. This is the blocker on
every live session, so it outranks everything else here.

**Two routes have now been measured and both are shut.**

*Mouse messages* (2026-08-17): `PostMessage`'s `WM_MOUSEMOVE` lands -- the
Campaign button highlights, which also proves the coordinates -- and the
`WM_LBUTTONDOWN`/`UP` after it do nothing. SDL takes mouse buttons partly
from raw input, which PostMessage cannot supply.

*Gamepad* (2026-09-20, `tools/gamepad_probe.py`): the game launched with
`-alwaysusecontroller` and `SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS=1`, a ViGEm
virtual X360 pad connected, measuring the save-row block of the front end,
five D-pad DOWN presses each way:

| game | parked | pressed |
|---|---|---|
| **background** | 0.0 x5 | **0.0 x5** |
| **focused** | 0.0 x3 | **2.7, 16.9, 16.1, 16.0, 16.0** |

Everything upstream checks out -- `XInputGetState` reports the pad on slot 0
with `A` reading `0x1000`, `app.log` echoes `option: alwaysusecontroller`,
and the game opens the joystick through SDL (`SDL_JoystickIsHaptic=0` on
every connect). It simply will not act on it unfocused. Whether SDL is
ignoring the env var or DD gates input on `SDL_WINDOWEVENT_FOCUS_LOST`
cannot be told apart from outside, and the outcome is the same.

**Do not re-run either of these hoping.** Re-run the probe only if the game
is patched or SDL2.dll changes; it answers in about a minute.

**What is left, and it needs Fran's call:**

1. **A second Windows user session.** Each session has its own desktop,
   cursor and foreground window, so `SendInput` in session 2 cannot touch
   session 1. It needs a second Windows account AND a second Steam account
   owning the game -- Steam will not run one account in two sessions.
   Nothing in the bridge changes.
2. **Blocks.** Keep today's arrangement: live runs when Fran can spare the
   machine, offline work (replay corpus, decide layers, tests) otherwise.
   Costs nothing and is what this session ran on.

**One finding worth keeping from the probe**, because it will bite any future
screen work: DD in controller mode draws button glyphs the banked references
have never seen, so `screen.recognise` mislabels those screens. The probe
read "screen still title" through six `A` presses that had in fact walked the
front end to the campaign list. A recogniser is a confirmation, not a primary
signal, for a screen nobody has banked.

### 2. ~~The map fit cannot place every room~~ -- MITIGATED 2026-09-20

"Not drawn on the map" now spends one view reset per destination instead of
being excluded from the reset gate. Live, that was costing four polls and a
wasted click before the reset happened anyway on the next poll. The fit
itself is unchanged; this only stops the common cause (a zoomed view) from
looking like the rare one (an unscouted room).

### 3. The torch snuff has no SCREEN acknowledgement -- still open

The snuff now sends enough SHIFT+clicks to reach 0 rather than stopping on a
save that has not been written yet, so the play lands. What is still missing
is a way to SEE the result: the save is written per turn and a snuff is sent
mid-turn, so every reduction is unwitnessed at the moment it happens.

The cursor does land on the meter -- a live frame shows the game's own
`DARK ( 20 )` tooltip open under it -- so the geometry is right and only the
reading is missing. The torch bar is the top-centre strip; measuring its lit
fraction would settle it, and OCR of the tooltip is the heavier fallback.

**Falsifier**: a measured lit fraction that does not track `torchlight` from
the save on the turns where the save DOES answer.

### 3b. PLAYBOOK 3.2 -- SHIPPED 2026-09-20. 3.3 and CRP still open

`tools/check_playbook.py` against the shipped 1836 checkpoint, 2,469 hero
turns (full table in `docs/findings/rulers.md`):

* **3.2** -- **35.5%** of 299 heals are neither Death's Door, nor critical
  HP, nor stall. That is 106 turns, **4.3% of every hero turn in the sample**,
  and they score **-0.0820** against **+0.1021** for a justified heal. The
  reward separates them by 0.18 and the policy plays the negative one anyway,
  so **no reward knob is indicated** -- this is the policy not having learned
  it.
* **3.3** -- with every living enemy stunned and the party needing recovery,
  50.0% of turns attack and **44.6%** recover. The expert says most of them
  should recover. **Corrected 2026-09-20** -- the ruler was measuring the
  situation without 3.3's stall gate, which scored 19 correct out-of-stall
  attacks as violations and read 37.3%.
* **CRP** -- 12 turns hit a corpse when a living enemy in the same skill's
  reach would have died to that hit. Small (0.5% of turns) but strictly
  worse, and not a judgement call.
* Working as intended: **DOT** (45.8% vs an indifferent 56.3% -- the policy
  avoids doomed targets, which is what the `_is_doomed` flag is for).

**3.2 is done.** Fran chose option 2 below.
`Advisor._apply_playbook_32` demotes an unjustified heal beneath every
compliant move -- demoted, never dropped -- and it changes **3 of 56 combat
decisions** on the capture corpus. `--no-playbook-heals` restores the
unfiltered arm. See `docs/changelog/2026-09.md`.

**What is left: 3.3 and CRP.** 3.3 is the same shape (with every living enemy
stunned, IN A STALL, 50.0% attack against **44.6%** recovering, where the
expert says most should recover) and would want the same
ruling. CRP is 12 turns -- 0.5% -- hitting a corpse when a living enemy in the
same skill's reach would have died to that hit; strictly worse play, cheap to
filter, and small.

**The three ways that were on the table for 3.2, kept because 3.3 faces the
same choice:**

1. **Train.** The reward already prices it; the policy has not learned it.
   Falsifiable and expensive, and it needs a stated hypothesis before a
   12M run (`dd-working-constraints`).
2. **A bridge-side shortlist filter.** `plan_battle` already walks the
   advisor's ranked shortlist; refusing an unjustified heal and taking the
   next candidate is a direct implementation of the decision spec, and
   `CLAUDE.md` says PLAYBOOK "outranks everything on what to DO". **But it is
   the bridge overriding the policy in combat**, which is the one place Fran
   has validated its play -- so it is his call, not a default to slip in.
3. **Nothing.** 4.3% of turns at -0.08 is real but small next to the things
   that end raids.

**Masking a full-HP heal target is NOT one of them.** 10.4% of the
unjustified heals land above 95% HP, which looks like pure waste until you
remember DD heals carry bleed and blight cures, stress relief and buffs; a
mask cannot see the secondary effect. It would also change the action
distribution and make the shipped checkpoint incomparable.

**First command**: `python tools/check_playbook.py --model
models/dd_combat_ppo_full_expedition.zip --episodes 24 --comps 10
--comp-seed 1`

### 3d. Two engine/data fixes are waiting for the NEXT retrain (2026-09-20)

Neither is worth a retrain on its own; both should be folded into the next
one, and **neither may be measured against the old numbers**.

* **`_invalidates_stall`** — three skills stopped counting toward the stall
  reset. Both `stall_counter` and `_stall_invalidating_actions_this_round`
  are observation features, so this is the silent kind of world change.
  Measured: **1.81% of hero turns** (41 of 2,267), all of them Houndmaster's
  Guard Dog.
* **`.target ?1234`** — the Arbalest's `blindfire` and the Musketeer's
  `sidearm` had NO target list and now target ranks 1-4. That changes which
  actions are legal for those classes, so any comp fielding them is a
  different problem than the checkpoint trained on.

**First command** when a retrain is next on the table:
`python tools/check_playbook.py --model <new> --episodes 24 --comps 10
--comp-seed 1` and the `--all-comps` eval, both quoted as a NEW BASELINE.

### 3c. Stalling: a TARGET gate, and a per-enemy timer (2026-09-20)

From two community stalling videos Fran supplied; full triage in
`docs/COMMUNITY_GUIDES.md`. The mechanics were all already implemented -- what
came out are two things nothing models.

* **A stall TARGET gate.** Both videos split a fight into a **rush phase**
  (kill what is dangerous or bad to stall against) and a **stall phase**, and
  agree on the bad targets: damage-over-time enemies, stress attackers,
  disease carriers. `PLAYBOOK.md` 3.4 has the COMP gate ("stalling is for
  comps that can actually recover"); the TARGET side is unmodelled, and the
  bridge has no notion of a good or bad stall target at all. Everything
  needed is in the monster data already parsed -- a skill's `stress` and
  DoT effects, `.disease` -- so this is a scoring function, not a data job.
  **It is a decision rule from strangers, so it wants Fran's ruling before it
  goes anywhere near the PLAYBOOK.**
* **"Each enemy has its own reinforcement timer"** (video 2, unexplained).
  The engine keeps one `stall_counter`. **Falsifier**: a capture where two
  enemies summon on different rounds.
* ~~**`accelerated` ALL vs ANY**~~ -- **CLOSED 2026-09-20.** The wiki's
  Stalling page settles it in as many words: *"If all remaining enemies
  belong to the list below"*. The repo already implemented ALL.
* **Farmstead and the first Ruins tutorial disable stall penalties
  outright** (wiki). The engine has no whole-expedition exemption. Neither
  is reachable from anything the bridge plays, so it is recorded rather
  than queued as work.

### 4. Knowledge gaps that are wired in nowhere -- open, and one needs Fran

* **Region -> heirloom bias** (item 2 of the guides triage): confirmed in
  `loot/loot.json` and exposed as `data.heirlooms.heirloom_bias`, consumed by
  nothing. `PLAYBOOK.md` 1.2 fixes three mission-choice criteria in a stated
  order, so adding "the next upgrade needs deeds" as a fourth is a change to
  the decision spec. **The question for Fran**: where does an heirloom
  shortfall rank against the existing three, and does it ever outrank them?
* **Hallway farming as a route-pricing input** (item 3): re-entering a
  hallway respawns hallway fights, the Antiquarian adds ~500 gold per fight,
  and `lootplan` prices none of it. Note `dd-rewalked-corridor-has-no-position`
  -- a second pass erases the dark run, so the position signal is lost even
  when the gold is real.

## THE COMMUNITY GUIDES ARE TRIAGED, AND FOUR ITEMS CAME OUT OF THEM (2026-09-19)

Fran supplied a Discord starter pack and `pizzarat105.github.io/PizzaRatReview`.
The triage is `docs/COMMUNITY_GUIDES.md`; the fidelity bug it found (enemies
ignored `.launch`) is fixed and in the changelog. Four items are left.

### 1. Boss data for N2 -- PARTLY SPENT 2026-09-19

`tools/pizzarat/strategy_notes.json` holds 11 boss write-ups. Reading them
for checkable claims is what found the `.move` bug: the Necromancer's
documented "starts at rank 1 and moves back with every action" was false in
the simulator, and 101 of 552 monster skill lines turned out to carry a
self-move nothing applied. Fixed; see `docs/changelog/2026-09.md`.

**What the notes claim that ASSUMPTIONS.md already says is unmodelled** -- do
not re-report these as bugs:

- the Siren's `Song of Desire` (flat 70% control) -- charm/control subsystem
  is not modelled at all
- the Hag's cauldron (8.75% max HP per action) -- `captor_empty`/`captor_full`
  is not modelled
- the Collector's "13+ filled inventory slots" trigger -- roaming bosses are
  absent entirely, so there is no spawn trigger to hold it

**Still unchecked**, and the best remaining candidates because they are
ordinary mechanics rather than missing subsystems:

- **The Flesh**: four parts sharing ONE HP pool, PROT 30%-90% except the
  Heart, and a per-round chance for each part to become a different body part
  (heads must be front, butts at back). If the shared pool is not modelled
  that is a large fidelity gap in a fight the policy can actually reach.
- **The Prophet**: starts at position 4 with three pews holding the front
  three ranks. Checkable against the boss comp.
- **`Maws of Life` hits the front 2 and `Undulating Invasion` only the back** --
  now that `.launch`/`.target` are enforced, these should hold; worth a
  rank-tactics test alongside the eight already there.

**First command**: `python -c "import json;d=json.load(open('tools/pizzarat/strategy_notes.json'));print(chr(10).join(d['bosses']['flesh']))"`

**Falsifier**: a claim the boss's own `.info.darkest` contradicts -- 4% of
this source's numeric fields are wrong, and the game files adjudicate.

**N2 proper is still open**: bosses remain trained against never and measured
never. This item only supplies the description of what correct looks like.

### 1c. ~~Sampled parties opened below full HP~~ -- FIXED 2026-09-19

`apply_trinkets_and_quirks` now keeps a full hero full, the same guard
the district branch beside it already had. `hp_targets` covered this on
the real-party path and is None on the sampled one, so training never
got the fix; no sampled comp had fielded a hero whose own kit moved the
ceiling until the team-builder sync. See `docs/changelog/2026-09.md`.

### 1b. The Flesh shares one HP pool, and the simulator gives it four

**Evidence**: `shared_health: .id formless` on all twelve Flesh part records
(4 parts x 3 tiers), naming one pool. Unmodelled, and undocumented until
2026-09-19 -- every other unread monster key had been counted, this one had
not. Now in `ASSUMPTIONS.md`.

**Impact**: ~**4x too durable** -- 4 x 51 = 204 effective HP at Champion
against a real 51. Reachable: all twelve parts are in the `boss` comp pool,
so any run with `boss_quest_p > 0` fights the inflated version.

**The plan**, and why it was not done on the spot: damage lands in five
places and `resolution.py` is a pure function with no engine handle, so there
is no post-damage hook to hang propagation on. Either

1. give `CombatEngine` one `_apply_damage(target, amount)` that all five
   sites call and propagate there -- the better shape, and a real refactor of
   death routing (`_handle_deaths_door` vs `_check_deaths_door_dot`); or
2. a `_sync_shared_health(combatant)` called from each of the five sites --
   cheaper, and exactly the kind of list that grows a hole later.

Mitigate against the STRUCK part's PROT first, then subtract from the pool:
the spread (Heart at 0, Guard at 0.9 on Champion) is what makes which part
you hit the decision in the fight.

**Falsifier, and the safety net**: a Flesh fight that never ends. Whichever
shape is taken, the test must drive a full fight to completion -- the failure
mode for a shared pool is a combat that cannot terminate, which is a hang
that grows rather than a red X. See the `is_alive` incident in
`tests/CLAUDE.md`.

**First command**: `python tools/pizzarat_oracle.py --field launch` then read
`tools/pizzarat/strategy_notes.json` for `bosses.flesh`.


### 2. ~~Region -> heirloom bias~~ -- DATA DONE 2026-09-19, WIRING OPEN

Confirmed from `loot/loot.json` and exposed as `data.heirlooms.heirloom_bias`
/ `regions_for`. Ruins->busts, Weald->deeds, Warrens->portraits are real;
**"Cove -> crests" is not** -- crest is weight 3 in all four regions, so the
Cove elevates nothing and `regions_for("crest")` returns all four. See
`docs/changelog/2026-09.md`.

**What is left**: nothing consumes it. `PLAYBOOK.md` 1.2 fixes three
mission-choice criteria in a stated order, so adding "the next upgrade needs
deeds" as a fourth is a change to the decision spec, not a data fix.

**The question for Fran**: where does an heirloom shortfall rank against the
existing three, and does it ever outrank them? Until that is answered the
data sits unused rather than being wired in on a guess.

### 3. Hallway farming as a route-pricing input

**Evidence**: re-entering a hallway respawns hallway fights; Antiquarian adds
~500 gold per fight; antiques are ~10k gold per inventory slot; the ceiling
is hunger checks, not food. `bridge/route.py` and `bridge/lootplan.py`
already price routes on gold and have none of this.

**Falsifier**: a raid where the modelled gold from a re-walk does not appear.
Note `dd-rewalked-corridor-has-no-position` -- a second pass erases the dark
run, so the position signal is lost even when the gold is real.

### 4. ~~Oracle coverage~~ -- DONE 2026-09-19

Was 122 of 424. The display-name map lives in
`localization/miscellaneous.string_table.xml` as `str_monster_skill_<id>`
entries, not in the compiled `english.loc2` as first guessed. **Now 389 of
424**, and it abstains on the 6 records where two monsters give the same
skill id different launch ranks. No new engine bug turned up in the
tripled coverage -- one more site error did (`shaman_heal`).

---

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
   `Ember_Trail__Cross.json` (untracked, and REPLACED by the 2026-09-19
   team-builder sync, so the path no longer exists) listed
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
