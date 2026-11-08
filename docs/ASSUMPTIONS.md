# Assumptions and Data Gaps

## What is declared, and what is open

Derived from the headings below, not written by hand -- this document
already labels its own sub-sections `Declared:` / `Open:` / `Recorded:`,
and the index states what the document states. Regenerate it if you add
a section.

- **The retreat decision: two declared conventions**  _2026-09-03_
- **Provisions: what a corridor can do, and the one number that proved it**  _2026-09-02_
  - Open: is the Dog Treats exception real, and it does not reach here anyway
  - Declared: DoTs carry, and the corridor is 5.625 tiles long
  - Declared: `--affliction-penalty` is the THIRD non-potential term
  - Open: hallway stress is shipped, unread, and the reading is 4x wide
  - Open: `medicinal_herbs` has nothing to cure here
  - Recorded: `has_item_id` is an unread buff `rule_type`
- **Districts and deliberate eating: what is declared, and what is still open**  _2026-08-24_
  - Declared: the eating threshold, `EAT_TOPUP_FRACTION = 0.90`
  - Declared: camping points are counted ONCE for the party
  - Declared: the tier gate is economic
  - Declared: the Granary always rolls its maximum
  - Out of scope, with the reason
  - Still open, and not a district problem
- **"Modded heroes don't load" is a PATH problem, not a format problem**  _2026-08-24_
- **Launching the game unattended: what works, and the two things that killed it**  _2026-08-24_
- **Camping: two items the sweep measured but did not change**  _2026-08-23_
  - Open: three quarters of the second campfire is never spent
  - Declared: an `individual`-target camp BUFF lands on the frontmost hero
  - Declared: stress urgency saturates at 100
- **Where the project stands against its actual goal: one decision of seventeen**  _2026-08-13_
- **The engine audit, round 8: the full data sweep**  _2026-08-23_
  - Declared: `stall_round_reset_threshold` makes the counter cycle
  - Open: the virtue `block_effect`
  - Resolved, and it was never a contradiction: `override_trait_type_ids`
  - Inert, and recorded so it is not re-reported
  - Out of scope, with the reason
  - Shipped-data bug, not ours
- **The engine audit, round 7: effect keys nobody read**  _2026-08-23_
  - One declared reading: does a FORCED unstun grant the recovery buff?
  - Deliberately not implemented, with the reason
- **The comp layer: what the search may propose, and what it cannot know**  _2026-08-22_
- **The engine audit, round 6: monster-brain gates**  _2026-08-21_
- **The engine audit, round 5: buff stat_types**  _2026-08-21_
- **The engine audit, round 4: the effects file, and a parser bug**  _2026-08-21_
- **The engine audit, round 3: `.darkest` keys nobody reads**  _2026-08-21_
- **The engine audit, round 2: `rules.json` scalars nobody reads**  _2026-08-21_
- **The engine audit: what is still unread from real data**  _2026-08-21_
- **The training distribution, and what is still wrong with it**  _2026-08-20_
- **Corpses: two gaps Fran's wiki reading exposed**  _2026-08-20_
- **Burn is implemented, and its decay rate is a CONVENTION**  _2026-08-20_
- **The bridge to the real game reads at turn granularity; what it does NOT carry**  _2026-08-11_
- **Overstress personalities are real data now — and the fix is correct but did NOT move the number**  _2026-08-09_
- **Camping implemented — and the retrain shows a clean dose-response to firewood**  _2026-08-09_
- **Monster formations never closed ranks — and it was the Abomination stall engine**  _2026-08-08_
- **Wiki audit: five real mechanics wrong, including the crit damage formula**
- **Stun granted immunity to DoT — stun and bleed/blight anti-synergized**
- **Stalling: two keys wired, two readings left open**  _2026-08-23_

Older **"Status after &lt;phase&gt;" session reports moved to `docs/assumptions/session-history.md`** on 2026-09-07. Read them as history: parts are superseded, and the list of "deliberate simplifications" there still names camping, provisions and modded heroes as out of scope when all three are implemented.

## The retreat decision: two declared conventions (2026-09-03)

**Implemented**: the stay/leave call is a comparison of two gold bills
(`bridge/retreat.py::advise_retreat`), with a **survival floor** over the top.
Both bills are now party totals, and a death is priced off
`persist.upgrades.json` when a profile is available. See `CHANGELOG.md` for the
three defects this replaced and the real-play evidence for each.

Two things here are conventions rather than shipped numbers.

### 1. `FLOOR_DEATH_CHANCE = 0.5` — the survival floor

**The convention**: never continue when the rollout says the run is more likely
than not to cost a hero, whatever the gold arithmetic says.

**Why it is not arithmetic.** The gold comparison is an expected-value rule, and
expected value is the wrong instrument for a decision made once with a hero who
does not come back. It is also correct on its own terms — a fresh recruit really
is free from the coach — which is why the floor is layered *over* it rather than
replacing it.

**Source**: Fran, 2026-09-03, choosing "gold economics plus a survival floor"
over both pure economics and a pure survival gate; and 2026-09-02, that an
afflicted hero is "almost the same as a death".

**Falsifier**: play a batch of expeditions with the floor on and off, paired on
the same scenarios. If the floor arm retreats out of runs the control arm
finishes **without losing anybody**, the threshold is too low — the rollout is a
lower bound on how a human plays, so it under-reads survival and the floor
inherits that bias. Retreating from a run that was going to be fine is the
failure mode to watch, and it is the one this constant can produce.

**Known and deliberate**: `FLOOR_MEAN_DEATHS = 0.5` is a *separate* constant for
callers whose result object has no `zero_death_rate`. An expected count is not a
probability and the two disagree in real cases (`mean_deaths 1.33` with
`zero_death_rate 0.67` is under the floor on the exact reading and over it on
the proxy). They are not to be collapsed into one number.

### 2. `abbey_tier = 0` — the stress price the bridge assumes

**The convention**: stress is priced at the **un-upgraded** Abbey's rate, 22.2
gold a point, rather than reading the estate's real building tier.

**Why**: it is the most expensive rate the game offers (a fully upgraded Abbey
is 10.0), so it makes the *continue* bill larger and leans the decision toward
retreating. That is the safe direction for a mistake, and the alternative is
guessing at a tier the bridge does not currently parse.

**Cost when wrong**: a campaign with an upgraded Abbey has cheaper stress than
this assumes, so the bridge over-values avoiding it and will retreat slightly
too readily. Bounded — it is a factor of 2.2 on one of the four terms, never a
sign flip on its own.

**Falsifier**: parse the Abbey's real tier out of the estate save and compare
the two bills on the logged decisions in `bat/play.log`. If any verdict flips on
the tier alone, this stops being a safe default and becomes a thing to read.

### What is NOT priced, and why

The heirlooms and the trinket a forfeited quest would have paid. Heirlooms buy
building upgrades and have no gold price; PLAYBOOK 1.2 values a trinket by
whether a comp equips it. Both are reported beside the total rather than folded
in with an invented rate.

**And the reward is credited on `completion_rate`, not `success_rate`** — a run
that reaches the end having lost heroes still pays, because the game pays it.
That is faithful, not a gap. It is also why the floor exists: without it, a
quest worth finishing plus a cheap party is a coherent argument for continuing
into a wipe.


## Provisions: what a corridor can do, and the one number that proved it (2026-09-02)

**OVERTURNED 2026-09-26 by a live test:** an item right-clicked on a hero's
own turn is used and does NOT end the turn (holy water 2 -> 1, the Plague
Doctor still to act). `cant_use_items_in_battle` is only a UI string. The
simulator keeps the flag off until a retrain can take the change: enabling
it must also make item use a FREE action, not a turn. See NEXT.

**Implemented**: items are no longer offered in battle at all
(`IN_COMBAT_PROVISIONS_ALLOWED = False`), the cure moved to the corridor
(`ExpeditionSequenceEnv._use_provisions_between_combats`), and **DoTs now cross
the combat boundary** (`CarryOverHeroState.bleeds/blights/stress_dots/
burn_stacks`) — without which the cure was a pure no-op. See `CHANGELOG.md`.

### The evidence, and what it does and does not settle

Six shipped effects carry `.item 1 .target "performer"` — `bandage`
(`.cure_bleed`), `antivenom` (`.cure_poison`), `medicinal_herbs`
(`.clear_debuff`), `laudanum` (`.clearDotStress`), `holy_water` (four resist
buffs) and `dog_treats` (+15% ACC, +50% damage, 3 rounds). `.item 1` is the
marker for "delivered by consuming an inventory item"; it says nothing about
*when* the item may be consumed.

`cant_use_items_in_battle` is what says when, and it sits directly beside
`cant_unequip_trinket_in_battle` in `miscellaneous.string_table.xml` — an
indisputably real restriction, in every shipped language. **`tools/pe_anchors.py`
reports both strings absent from `Darkest.exe`, and that is not evidence
either way**: every localization id tested came back absent, including the
trinket one, so the anchor tool does not reach this class of string. Recorded
so the negative is not re-quoted as a finding.

### Open: is the Dog Treats exception real, and it does not reach here anyway

darkestdungeon.wiki.gg states Dog Treats *"can be consumed in battle … Only
usable by a Houndmaster"*, and it states that of **no other provision** — so
the wiki corroborates the four the simulator used to offer being unusable, and
names exactly one exception. No shipped file expresses that exception: the
supply row carries no in-battle flag, no Houndmaster skill references the item,
and the only data-side consumption path is the `consume_item` act-out, which
ships at **chance 0 in all thirteen base traits** and chance 3 in Color of
Madness's `color_madness` alone.

It is moot for this simulator: `dog_treats` costs 0 gold, is absent from every
`default_store_inventory_item_lists` row, is granted only to a Houndmaster by
`raid_starting_hero_class_item_lists`, and has never been in
`PROVISION_ACTION_ORDER`. **Nothing to undo.**

*Falsifier*: a live capture of a Houndmaster consuming Dog Treats mid-fight
would establish a hardcoded exception, and the item would then need modelling
as a real in-battle action rather than as a restored offer for the other five.

### Declared: DoTs carry, and the corridor is 5.625 tiles long

`rules.json` ships `dotHallwayStepRatio = 0.5` — a DoT keeps ticking as the
party walks, at half rate per hallway tile. A rate for ticking a DoT in a
corridor is only meaningful if the DoT reaches the corridor, so carrying
bleeds, blights, stress-DoTs and burn is the reading, and dropping them was a
gap rather than the faithful simplification the docstring claimed.

**The tick is modelled since 2026-09-02** (`ExpeditionSequenceEnv._walk_the_
corridor`), and the tile count is MEASURED rather than derived.
`scripts/map_generator.darkest` gives `.base_corridor_number` and `.spacing 4`
but no corridor length — spacing is a grid stride between room centres, and
reading it as a tile count would be a convention wearing game data's clothes.
`bridge/dungeon_map.py`'s `Area.tiles` is the real thing, off real
`persist.map.json` captures: **48 corridors across seven distinct generated
dungeons in `archive/traces/raidsnaps*`, mean 5.625 tiles, median 5, range
3–9.** `bat/play.log` agrees from another direction — its `a fight started
after N step(s)` lines run 1–11, mean 4.3 (n=23), lower because a fight can
start part-way down a corridor, which brackets the map figure from below.

Three declared choices inside it:

- **A constant, not a draw.** The env has no map, so there is no corridor to
  measure per boundary, and drawing one would consume `_sampler_rng` and put a
  different dungeon in front of the two arms of every A/B. The expected tick
  count is what matters and a constant delivers it exactly. `5.625 * 0.5`
  rounds to **3 ticks per boundary**.
- **Bleed and blight only.** Burn is a decaying stack rather than a tick
  budget and ticks at the end of a TURN, which a corridor has none of — the
  same argument that expires buffs by rounds and not by tiles. Horror
  (`stress_dots`) is left because stress crossing 100 owes a Resolve Check and
  `_apply_stress` is what fires one; ticking it here would apply the stress and
  skip the check, which is a worse error than not ticking it.
- **Nobody dies in a corridor.** A hero emptied on the walk arrives at the next
  fight on Death's Door at 0 HP — what `_handle_deaths_door` does for a hero
  not already on it — but the deathblow roll a hero already on the door would
  face is not taken. A draw between combats moves the RNG stream, and a death
  arriving where nothing can respond to it is what the starvation clamp beside
  it already refuses.

**What it is actually worth is smaller than the raw rate suggests, and that is
the interesting part.** A provisioned party pays nothing: the bandage and the
antivenom are used one block earlier, and three of each covers the DoTs a
typical run brings to a boundary. Measured over 12 long runs of random play
with the pack emptied — the tail of a long expedition — **27% of boundaries
carry a DoT and each costs about 5 HP**. So the mechanic's real effect is that
it **gives the bandage a job it never had**, and it bites once stock is gone.

*Falsifier*: if a long run's total DoT damage lands far above a real
expedition's, the ratio is not per-tile-per-DoT the way this reads it.

### Declared: `--affliction-penalty` is the THIRD non-potential term

The invariance argument covers the Phi terms only, and this file's rule is to
re-check it whenever another term goes outside them. Three now do: the
two-phase tempo (`TURN_PENALTY_THREAT`/`TURN_PENALTY_STALL`),
`STRESS_TICK_PENALTY` with its terminal partner, and — since 2026-09-02 —
`affliction_penalty`. Each is deliberately outside Phi because each is meant to
change the optimum, and the potential-based terms provably cannot.

The argument is unchanged and still sound: `Phi_combat` and `Phi_expedition`
remain valid potentials, their sum is a valid potential by linearity
(Ng/Harada/Russell 1999), and every non-potential term is a bounded,
state-dependent cost charged at most once per hero per expedition. Nothing
about the shaping guarantee is weakened by adding a third; what changes is that
the optimum being shaped toward is a different one, which is the stated intent.

**Default 0.0, and that is load-bearing rather than cautious.** `W_AFFLICTED =
0.15` sits INSIDE Phi, so the price the reward has actually put on an
affliction has been exactly zero however the constant is read — a fact worth
stating plainly, because the constant's existence reads as a price. A nonzero
default here would silently change what every run after it optimises, and no
comparison across that boundary would mean anything.

*Falsifier*: it is a paired training arm, on **two seeds**, sized against
`REWARD_HERO_DEATH` (-50.0). `--stress-weight` is the precedent and the warning:
it moved the quantity it names, reliably and on both seeds, and bought no
score. An affliction term that lowers afflictions and does not move
`run_score` is the same result, and the honest response is the same — report
both halves.

### Open: hallway stress is shipped, unread, and the reading is 4x wide

**Update 2026-09-25: the live logs pick ONE HERO PER TILE.** The bridge prints
`hero_changed ... stress a -> b` per hero whenever the save moves, and the save
is written on room entry, so each such event after a corridor walk carries
exactly what that corridor did. Filtered to corridors with no fight, curio,
trap or camp in them, 97 events across every `archive/logs/overnight` log:
**61 changed one hero, 21 changed two**, and 4-hero changes are as rare as the
town events mixed into the same window. The typical delta is +2 / +3 (the 2.0
`_fwd` value through quirk multipliers), and the hero varies (Highwayman,
Crusader, Vestal, Plague Doctor), so it is not always rank 1. Party-wide
would change all four about a third of the time on a ~5-tile corridor, and it
essentially never does. The single-hero reading (0.15 per hero per tile) is the
one to implement; the script is reproducible from the logs
(`hero_changed` grouped by timestamp, corridor areas only). Still unread by the
simulator: implementing it changes the training world, so it waits for the next
retrain rather than being slipped under a live checkpoint.

`rules.json` also ships, in the same object:

    hallway_per_tile_stress_damage_fwd          2.0
    hallway_per_tile_stress_damage_chance_fwd   0.3
    hallway_per_tile_stress_damage_backing_up   5.0
    hallway_per_tile_stress_damage_chance_backing_up  0.55

Nothing in Python reads any of the four. `DECOMP.md`'s open-questions list
already carries "how `hallway_per_tile_stress_damage_fwd` / `_backing_up` are
applied", and now that the corridor exists it is implementable — **except that
the reading differs by 4x depending on WHO takes the hit**, and no shipped key
says.

- **Party-wide**: every hero rolls 0.3 for 2.0 → **0.6 stress per hero per
  tile**, ~3.4 per corridor, ~50 over a long run.
- **One hero per tile**: the same 0.6 spread over four → **0.15 per hero per
  tile**, ~13 over a long run.

**Fran's own `bat/play.log` bounds it, and the bound argues against
party-wide.** Tagging every hero stress reading with the walk steps between it
and the previous one gives **367 points of stress rise across 604 hero-steps =
0.608 per hero-step** — which matches the party-wide figure almost exactly, and
that is the problem: that bucket also contains the fight that followed, and
70.5% of all observed stress rise happens inside fights. Party-wide would
consume the entire boundary bucket and leave nothing for the combat that is
demonstrably in it. So 0.6 is an **upper bound the data cannot exceed**, not a
measurement, and the single-hero reading sits comfortably inside it.

It composes cleanly with machinery that already exists, which is why it is
worth deciding rather than dropping: the darkness table's
`stress_chance_increase` (0/5/10/25/35) is an additive percentage-point
modifier and `stress_damage_increase` (0/10/20/30/40) a multiplier, both
already parsed and applied to combat stress as `_stress_chance_bonus` /
`_stress_damage_mult`. At pitch black a tile would be 65% for 2.8 — three times
the lit rate — which is the mechanic players describe.

*Falsifier*: a live capture with `-startquest` walking a known corridor at a
known torch level, reading party stress before and after. That distinguishes
the two readings in one run.

### Open: `medicinal_herbs` has nothing to cure here

Its whole payload is `.clear_debuff 1`, and a combat stat debuff does not reach
the corridor in this simulator: buff durations are counted in ROUNDS and a
corridor has none, so `CarryOverHeroState` drops them — faithfully, as far as
any shipped key states. The item is therefore never used between combats, and
`.clear_debuff` is unparsed in `data/effect_loader.py` rather than parsed and
dead.

*Falsifier*: a live capture showing a combat debuff still on a hero in the next
room. That would make combat buffs partially cross the boundary, which is a
larger change than this item — the two would have to land together.

### Recorded: `has_item_id` is an unread buff `rule_type`

Ten Color of Madness trinket buffs are gated on **carrying** an item —
`TRINKET_com_hnd_huskfang_whistle_bleed_chance_B50_w_biscuit` wants
`dog_treats`, others want holy water, laudanum, herbs, a bandage.
`_is_conditional_rule_active` grants any `rule_type` it has no branch for, so
all ten fire unconditionally. It is the same shape as the `lightbelow` finding
and is left unwired for now because every carrier is a CoM trinket and no
curated comp equips one; unlike the light rules, the exposure is currently
zero. Wire it when a comp does.


## Districts and deliberate eating: what is declared, and what is still open (2026-08-24)

**Implemented**: the nineteen town districts, as a binding from a hero tag to
buff ids `store.buffs` already carried (`data/district_loader.py`); the
Granary's food; the Outsiders Bonfire's camping points; the Mill's hunger
shutdown; and **deliberate eating**, which had never existed — the involuntary
hunger check was food's only consumer, so a long run ended with half its ration
unspent and a Champion run with all of it. Worth +13.9pp (buffs) to +19.4pp
(full package) on paired long/Champion scenarios. See `CHANGELOG.md`.

### Declared: the eating threshold, `EAT_TOPUP_FRACTION = 0.90`

The real trigger is a player clicking food in a corridor, and **no shipped key
states when they do it**. 0.90 reads as "a player tops the party up between
rooms rather than hoarding". A reserve is held back for the expected remaining
hunger checks, so eating can never *cause* a starvation — which costs 20% max
HP against the 5% a meal restores, four meals wasted to buy one.

*Falsifier*: a real long run that ends with a large unspent ration means
players eat less often than this and the threshold is too high. Count the food
left at the Quest Complete screen.

### Declared: camping points are counted ONCE for the party

`DistrictCampingBuffData` on the Outsiders Bonfire carries `camping_points: 2`
**and** `hero_type_tags`. Camping points are a party budget, so the shipped data
is equally consistent with "+2 once if the party contains an Outsider" and "+2
per Outsider" — which on four Outsiders would be +8 against a base budget of
12. Nothing in the file disambiguates. This takes the conservative reading.

*Falsifier*: camp in a Champion raid with the Bonfire built, once with one
Outsider and once with two, and read the point total off the camp screen. Two
different totals means the per-hero reading is right.

### Declared: the tier gate is economic

Crimson Court and Fires Edge districts are treated as built from **Veteran**,
Color of Madness from **Champion**. Fran's call, and the reason is that the
gold and blueprints do not exist earlier — a fully-districted Apprentice party
is a scenario that never occurs. This is a *simulator* convention: the game
itself applies a built district at every difficulty.

*Consequence worth keeping*: tiers 1–2 are byte-identical to the pre-districts
behaviour, so they are a control group when reading a retrain.

### Declared: the Granary always rolls its maximum

Shipped range 4–10; the roll is rerollable from the provision screen, so a
player who wants 10 takes 10. Modelling the roll would add variance no real
player is exposed to.

### Out of scope, with the reason

`DistrictLightBuffData` — the Illuminators Guild **replaces the whole darkness
range table**, and that is a real combat effect, not a buff grant: at radiant
it pays +7.5 DODGE against the base table's +4.0, and −5 to heroes-surprised.
It is loaded and reported by the loader but not wired, because a table swap
touches every torch-dependent path at once and deserves its own measurement.
This is the one district with a combat effect that is deliberately unmodelled.

The rest are town or economy and stay out of scope: `DistrictPrestigeBuffData`,
`DistrictEstateBuffData`, `DistrictRosterStressReliefBuffData`,
`DistrictRandomLootBuffData`, `DistrictCurioInteractionBuffData` (curios are
out of scope), `DistrictStackingBuffData`,
`DistrictReplacementInventoryEffectBuffData`.
`tests/data/test_districts.py` fails if a district ships a type not on that list, so
a new mechanic cannot be dropped silently.

### Still open, and not a district problem

Long runs make **0.88 of their 2 campfires** — parties die before the second.
That is `_camp_schedule` placing them at combats 5 and 10 of ~15, and it is why
camping restores only 4.7% of a long run's incoming damage. Recorded at
"Open: three quarters of the second campfire is never spent" below; districts
did not change it.

## "Modded heroes don't load" is a PATH problem, not a format problem (2026-08-24)

`CLAUDE.md` has said since early on that modded heroes cannot load because "no
`.info.darkest` exists anywhere in the install". That is true and it is the
wrong diagnosis. Fran has **20 Workshop mods subscribed**, five of them class
mods — Exanimate, Trophy Hunter, Navigator, Tempest and Sibyl — and their hero
files exist. They live in `steamapps/workshop/content/262060/<id>/`, which is
**outside `GAME_DIR`**, so no loader has ever looked at them.

**Sibyl parses cleanly with the existing parser, unmodified.** Reading
`heroes/sibyl_ms/sibyl_ms.info.darkest` straight out of the Workshop directory
yields exactly the shape a vanilla hero does:

| record | count |
|---|---|
| `combat_skill` | 35 — **7 distinct skills x 5 levels**, the standard |
| `weapon` / `armour` | 5 each |
| `mode` | **3 — she is a STANCE hero**, like the Abomination and Duelist |
| `resistances`, `crit`, `deaths_door`, `combat_move_skill`, `controlled`, `skill_selection`, `generation`, `tag`, `id_index` | all present |

And she ships the whole standard layout beside it: `effects/sibyl_ms*.effects.darkest`,
`shared/buffs/sibyl_ms*.buffs.json`, `trinkets/sibyl_ms*.entries.trinkets.json`
(including a `.sets.trinkets.json`), `raid/camping/sibyl_ms.camping_skills.json`,
`upgrades/heroes/sibyl_ms.upgrades.json`.

### The route in already exists, and it is called Fire's Edge

`dlc/4964110_fires_edge` is **itself a Workshop package**, and `HeroLoader`'s own
comment says so: "it installs under `dlc/` and loads exactly like one". Several
loaders already glob `dlc/**` — camping, traits, trinkets, localization, comps.
So a Workshop class becomes visible by being **placed under `dlc/`**, not by
teaching every loader a second root.

Two things are then needed, and only two:

1. the mod directory copied to `GAME_DIR/dlc/<id>_<name>/`;
2. its hero id added to `HeroLoader.HERO_DIR_MAP`, which is an explicit list of
   20 and therefore skips anything it has not been told about.

**Deliberately not done yet.** Copying into `GAME_DIR` changes the hero roster
and invalidates the store cache, which would move the ground truth underneath a
training arm that is mid-flight and a grading that has to compare against
`control_20260824`. It waits until that comparison is closed.

**And the honest caveat about why it matters.** If a modded class loads, the
right response is to *train on it*, not to build cross-class transfer
machinery — transfer only earns its keep for a class you specifically do not
want to retrain for. This finding therefore argues **against** reviving the
transfer line, not for it. What it does buy is a larger legitimate roster and
the first real test of whether third-party content works at all.

One known gap even after the two steps above: Sibyl's localization ships as
`localization/*.loc2`, the compiled format, where the loader reads
`*.string_table.xml`. Display names would not resolve, which matters for the
comp layer's name matching and for nothing in combat.

## Launching the game unattended: what works, and the two things that killed it (2026-08-24)

The capture session needs `Darkest.exe` running under determinism flags with no
human at the keyboard. Four launch attempts, and the useful part is what failed.

**1. The working directory is load-bearing, and nothing recorded it.** Launched
from the repo, the game dies immediately with *"could not open pin file, wrong
working directory"* -- a dialog only a human at the screen can see, since
`app.log` shows a normal-looking boot that just stops. **Launch with `cwd` set
to the install root.** This cost an hour and is written down nowhere else.

**2. `option:` in `app.log` is NOT a reliable test for every flag.** `CLAUDE.md`
says "every accepted option is echoed as `option: <name>`... one the binary does
not know is simply absent", and on the strength of that this session concluded
that `-profile`, `-skipfeflow` and `-enable_autobattle` were unknown to the
binary and that `NEXT.md`'s recon note was wrong. **Both conclusions were
wrong.** All three strings are in the argument table (dumped from
`Darkest::ParseArguments`), and once the working directory was fixed `profile`
and `skipfeflow` *do* appear in the echo. The dead boot had simply not reached
the parse. Grade a flag from a boot that actually started.

**3. `-skipfeflow` renders a black screen and hangs, on a valid save.** This is
the real blocker and it is reproducible:

| launch | result |
|---|---|
| no profile flags | renders normally, reaches the title screen |
| `-profile 0` (save made by the game itself) | renders, cycles the front end |
| `-profile 0 -skipfeflow` | **pure black, `mean 0`, process alive, hangs** |

`NEXT.md` attributed this to the slot not existing. It is not that: slot 0 was
created by the game through its own front end, and `-skipfeflow` still hangs on
it. Whatever `-skipfeflow` skips, the game then waits for something that never
arrives.

**4. A hand-copied `scripts/starting_save/` is not a usable slot** -- but not
for the reason first guessed. The guess was "a real profile has 16 files and the
template has 7". Wrong: a campaign the game creates fresh **also has 7**; the
other nine accumulate during play. The template copy failed for the same reason
a real save fails, which is `-skipfeflow` (3).

### Where that leaves the capture session

Reachable unattended: launching with `-profile N` and every determinism flag
accepted (`alwayshit`, `randomseed`, `enabledebugmonsterbrainoutput`,
`enabledebugqueueoutput`). Not reachable unattended: getting from the front end
into a raid, because `-skipfeflow` hangs and `bridge/screen.py` cannot classify
the animated front-end frames (`UNKNOWN`, mean drifting 3 -> 31 as it animates).

So the five open readings still need a human to walk the party into a corridor,
**or** `bridge/screen.py`'s hash corpus extended to cover the current front end.
The latter is the automation-shaped fix and is not blocked on anything.

Recorded rather than solved, because the next thing in the queue is worth more
than the fifth attempt at this.

## Camping: two items the sweep measured but did not change (2026-08-23)

The sweep itself is in `CHANGELOG.md` -- two real bugs fixed (the permanent
mortality debuff, and `.chance` never being rolled). These two are recorded
instead, because changing either is a convention call rather than a fidelity
fix.

### Open: three quarters of the second campfire is never spent

`_camp_schedule` spreads firewood evenly -- on a 15-fight run the party camps
after combats **5 and 10** -- documented as "the neutral choice that neither
hoards recovery for a final push nor wastes it on a fresh party".

Measured on the live checkpoint, 40 long expeditions: **33 runs reach combat 5,
10 reach combat 10.** So the first campfire fires for 83% of runs and the
second for 25%, giving 0.93 camps per run against a firewood budget of 2. Each
camp is worth ~32 party stress and ~17 party HP.

The neutral choice turns out not to be neutral: it does not waste the second
fire on a fresh party, it strands it behind a wall the party usually does not
reach. Two readings, and they point opposite ways.

* **The schedule is right and the party is just dying.** Then front-loading
  would be tuning the model to flatter a weak policy, which is exactly what
  this project refuses to do elsewhere.
* **The schedule is wrong because the real game does not have one.** A player
  camps when the party needs it, so fixed spacing is itself the convention, and
  a need-gated camp ("camp at the first opportunity after the party drops below
  X") would be closer to real play than even spacing is.

Not decided. It is Fran's call, and worth revisiting after the reward fix,
since the per-combat potential reset was penalising exactly the recovery this
schedule delivers.

### Declared: an `individual`-target camp BUFF lands on the frontmost hero

`_pick_individual` has targeting branches for both heal types, both damage
types and both DoT cures, and none for `buff` -- so it falls through to
`candidates[0]`, the frontmost LIVING hero.

First written up as a 2,018-slot bug. **That count was wrong**: only 23 of 107
buff rows are `individual`-target, which is **261 equipped slots of 2,435**
(10.7%). And `candidates[0]` is a defensible target -- the front rank takes the
hits, so a +DODGE or resist buff belongs there, and offensive buffs are not
obviously misplaced on a front-line attacker.

There is no non-invented rule for which hero most wants +ACC. Recording it as a
declared convention rather than replacing it with a guessed heuristic.
**Falsifier:** if a real player's camp buffs land systematically on a rank the
frontmost hero does not occupy, the rule is wrong.

### Declared: stress urgency saturates at 100

`_urgency(1 - min(1, stress/100))` prices a hero at 190 stress identically to
one at 100, though the heart attack lands at 200. `STRESS_POOL = 100` is
deliberate for the *value* of a heal -- relief above the affliction threshold
matters less -- but the same clamp flattens *urgency* across the whole top half
of the bar. A convention weight, not game data; left alone rather than tuned
without a measurement to tune against.

## Where the project stands against its actual goal: one decision of seventeen (2026-08-13)

An honest inventory, because "the combat is good" had started to read as "the player is nearly done". A Darkest Dungeon player makes roughly **17 kinds of decision** per cycle. Before today exactly **one** was implemented.

| Layer | Decisions | State |
|---|---|---|
| Town | treat stress, sanitarium, guild/blacksmith, stage coach, nomad wagon, heirloom upgrades, dismiss | **stress, coach and heirlooms decided** (`bridge/townplan.py`). Stress treatment landed 2026-09-05 and needed no screen at all — every price, heal and slot count is shipped data, and its two exclusions are correctness rather than preference: a hero already in a building is being treated, and a hero marked for the party **cannot embark**, so treating one silently cancels the raid it was selected for. Sanitarium, guild/blacksmith rank choice, the nomad wagon and dismissals still decide nothing. **The Sanitarium is blocked on a QUIRK VALUE MODEL**, which is a judgement call and not a parse: nothing shipped says whether −10 DODGE is worse than −10% damage, so ranking negative quirks means inventing a weighting — a convention wearing game data's clothes. |
| Embark | which quest, which four heroes, trinkets, provisions | **quest + party** now measured (`bridge/embark.py`); trinkets and provisions still not chosen |
| Dungeon | route, torch, curios, when/how to camp, **when to retreat** | **route and curios decided** (`bridge/route.py`, `bridge/curios.py`) off the decoded map (`bridge/dungeon_map.py`) and the raid save's own quest record; retreat **priced in town gold** (`bridge/retreat.py`), which is the currency PLAYBOOK 2.6 actually uses — a death costs ~20x a full-party retreat, so the verdict changes sign with party quality rather than with a probability threshold; **camping decided** (`bridge/camp.py`: when to burn a fire, and the engine's own spend); **torch decided** (`bridge/torchlight.py`) — the loot model turned out to be shipped after all, in `rules.json`'s `darkness.range_table`, so the shutdown-before-the-killing-blow play is priced rather than merely described |
| Combat | the turn | policy done, and **validated against an expert's criteria** (`PLAYBOOK.md`) — two measured violations left: half of all heals are justified by nothing, and it piles on kills that are already secured |

The simulator matches: `ExpeditionSequenceEnv` chains combats and camps, but models no traversal, no torch decay, no curios, no hunger, no loot, and **no retreat** (it is not in the action space). The bridge now decides route, curios, camping and torch off the real save without any of that — those are algorithmic layers, and none of them needed the simulator to grow.

`bridge/run.py` puts all six dungeon layers plus the combat move behind one poll loop, and **marks each recommendation against what was then played** by diffing consecutive saves. That closes a gap every acceptance run had left open: five live runs found five real defects, all reconstructed from memory afterwards. It recovers the *kind and target* of a turn, never the skill, and **a miss leaves no trace at all** — so `?` is a real verdict, currently 5.4% of 56 captured turns, printed as a rate rather than folded into agreement. See `CHANGELOG.md` for why the signals are ranked rather than treated as ambiguity.

`bridge/overlay.py` draws that loop's output on top of the game (click-through, borderless windowed only). It is presentation, not a decision layer — **still no input injection**, for the standing reason that automating clicks fixes nothing if the recommendation is wrong.

**The 67.4% full-expedition SUCCESS is not the bottleneck for "plays the game".** It is measured with convention provisions, no route choice, and no ability to turn back — and in the real game a large share of success is not fighting the fights you lose. Adding a retreat decision raises the effective rate without touching the policy.

### Built 2026-08-16: the five dungeon layers get an agreement rate, and it is small

The inventory above says which layers *decide*. It did not say which ones had ever been **checked**, and the answer was one. Combat: 84% MATCH over 32 recommendations, three live runs. Route, torch, curio, camp, retreat: nothing, ever — `Session.room` printed and published but never armed, and `replay()` did not dispatch to it.

`bridge/run.py` now marks all of them, and `replay()` runs them offline over `raidsnaps2`/`raidsnaps4` (328 captures with a map save beside each raid save). Full evidence in `CHANGELOG.md`; what matters for this file is **how little it currently proves**:

| layer | scored | MATCH | not scored |
|---|---|---|---|
| route | 4 | 3 | **36** steps had one way forward |
| torch | 6 | 6 | — |
| curio | 13 | 12 | — |

**Read these as an instrument that works, not as layers that are validated.** The route sample is 4 because 36 of 40 captured steps were forced by the shape of the dungeon; the torch column is 6 relights at torch 0, which is close to forced in the same way, and the shutdown call — the one with a real trade-off in it — has **no sample at all**. Only curio has a usable n, and its single real disagreement is `advised antivenom -> old_tree, spent holy_water, shovel`.

Three rules exist purely to stop the number flattering itself and must not be relaxed: a forced route step is not scored, a curio walked past is parked rather than failed (the party comes back for it — 4 of 5 DIFFERs), and a snuff is told from decay by crossing `rules.json`'s own darkest-lit-band boundary rather than by reaching zero.

### Built 2026-08-15: retreat, the eighteenth decision (and the one that changes the number)

**A party that cannot leave a fight it is losing overstates how dangerous the dungeon is**, and that overstatement is most of what the full-expedition SUCCESS number measures. Every eval this project has ever run was taken with retreat unavailable. In real play a large part of success is simply not fighting the fights you lose.

All three numbers are `shared/rules.json`, none is convention:

| | value | note |
|---|---|---|
| `combat_retreat_chance` | 0.70 | base odds |
| `combat_retreat_bonus_chance_per_attempt` | 0.05 | per **failed** attempt, this combat |
| `combat_retreat_stress` | 20.0 | per living hero, on success (**25.0** in New Game Plus) |

The NG+ contrast is what settles the third: a harder mode ships the same odds and a *higher* stress, so the stress is the price of leaving and not an unrelated constant sitting nearby.

What it cost, and what is deliberately NOT invented:

- **`ACTION_RETREAT` is appended at index 37**, so every index 0..36 still means what it always meant. The space widens to `Discrete(38)` and the observation to 337/329 (a 7th global: the *live* retreat chance, which climbs per failed attempt — a constant feature would carry no information, the same mistake torchlight made before it was sampled). **No pre-2026-08-15 checkpoint loads into this**, which is a real break and blocks `bridge/` until a new one exists.
- **`winner` gains a third value, `"retreat"`.** Not folded into `"enemies"`: nobody died and the party keeps its expedition. Everything asking `winner == "heroes"` is unaffected.
- **The price is entirely emergent.** `REWARD_RETREAT` is `0.0`, so a retreat simply forfeits `_reward_win` (+8) and pays 20 stress per living hero through the existing `STRESS_TICK_PENALTY`. Against `REWARD_HERO_DEATH` (−20) that makes fleeing correct at roughly half an expected death. No new reward term, so the potential-invariance argument is untouched.
- **A retreated combat is not a completion.** `eval_expedition` requires `retreats == 0` for `completed`, so the option cannot inflate SUCCESS by definition instead of by playing better; `ExpeditionOutcome.retreats` reports it separately.
- **An afflicted hero never flees.** The real game has its own `retreat_from_combat` act-out and ships it at **weight 0 in every trait**, so `_act_out_random_command` excludes `__retreat__` rather than reaching it through the back door.
- **`min_hero_count_for_escape` (3) is loaded and NOT implemented.** No shipped file states what it gates and all three modes ship the same 3, so there is no contrast to recover its meaning from. Guessing would be a convention wearing game data's clothes.

### Measured 2026-08-15: neither open PLAYBOOK defect is a reward bug

Both were quantified before deciding anything, on the 6M final save, 36 episodes / 12 comps / `--comp-seed 1` (`archive/logs/playbook_sharpened_final6M.log`), because "is this the reward or the policy" has different fixes and the answer was not guessable.

**3.2 (over-healing).** 413 unjustified heals. The target's HP fraction is a **median of 92%**, 41.9% of them are above 95%, the heal wastes **49%** of the skill's own maximum, and it restores a **median of 2 HP**. That is the live report ("Divine Comfort with three at full and one hurt") confirmed with numbers. But the realized step reward is **−0.061 for an unjustified heal against −0.023 for a justified one** — read heal-vs-heal, since an attack turn (+0.36) scores higher for reasons that have nothing to do with 3.2. The reward already prices these as a loss; it is *not* buying them. So a reward term is the wrong fix.

Nor is a legality narrowing available: `_is_wasted_heal` blocks a heal that cannot restore **a single point**, which is a fact and not a threshold, and it is already applied on both the AoE and single-target branches. A hero at 92% can receive 2 HP, so these heals are legal by a rule that is working as designed. Narrowing further needs an invented HP threshold — a convention masquerading as game data, which is what this project refuses everywhere else. **Left open deliberately**; the retrain may reduce it as optimization slack, and if it does not, the honest options are a convention or nothing.

**Corpses over living targets.** 99 corpse attacks; in **38.4%** of them a living enemy stood in the same skill's reach and would have died to that hit (conservative — `dmg_min` before modifiers, so those kills were never in doubt). Real, but the same diagnosis: `W_BLOCKING_CORPSE` is 0.10/4 per blocking corpse against an entire enemy leaving Φ on a kill, so the reward already prefers the kill by a wide margin. Optimization slack, not a mis-specified reward.

The conclusion is what makes the retrain honest: **it carries retreat and nothing else.** Neither measured defect justified touching the reward, and both would have been tempting to "fix" without measuring.

### Built 2026-08-15: the pre-retrain bundle (three gaps, one retrain)

All three below **invalidate every checkpoint saved before 2026-08-15** — two change the
observation width (base 324 -> 328, `ExpeditionSequenceEnv` 332 -> 336) and one changes the
legal action set. They were done together for exactly that reason. Full evidence in
`CHANGELOG.md`; each carries its own falsifier, to be read off
`tools/check_playbook.py` and `tools/analyze_behaviour.py` after the retrain.

**1. Attacking a corpse — built.** `get_valid_actions` filtered its target pool on
`e.is_alive()`, which excludes corpses, so no hero had ever been offered an attack on one.
The game allows it plainly: `corpse_A.info.darkest` gives `.hp 7 .def -20%` and
**`.can_be_missed False`**. The corpse now carries its own stat block from `store.corpses`
(the variant named by the slain monster's `death_class:`), cannot be missed, takes damage
and nothing else, and is destroyed at 0 HP with the line collapsing over it. `W_BLOCKING_CORPSE`
in the potential function gives the new action a gradient — without it, breaking a corpse
reads as a turn spent on a target whose HP is not in `Phi` at all.
*Falsifier*: attacks-on-corpses must be non-zero and concentrated in the position that
motivated it (a survivor out of reach behind a corpse). A corpse is legally targetable on
**15.2%** of hero turns and blocks a living enemy on **13.9%** (measured over 2730
random-policy turns), so a rate that stays at 0.0% means the term failed, not that the
situation is rare.

**2. The "doomed target" observation feature — built.** Reported from live play: the policy
finished an enemy already dying to a bleed. The observation genuinely could not express it —
HP is a fraction and DoT damage an absolute, and comparing them needs the target's absolute
max HP, which the vector never carried. Now an explicit per-enemy-slot flag (`_is_doomed`,
`ENEMY_SLOT_SCALARS` 14 -> 15): outstanding bleed + blight >= current HP.
*Falsifier*: a new `DOT` block in `tools/check_playbook.py` — of the attacks made while a
doomed enemy is on the board, how many are aimed at that enemy. It had to be added, because
PLAYBOOK 3.3 as the tool measures it is the *stunned*-secured case, not this one (that one's
baseline is 60.4% attack / 18.7% recovery over 4083 hero turns, 334 combats,
`--episodes 36 --comps 12 --comp-seed 1`). If the DoT rate does not fall, the constraint was
never observability.

**3. Torchlight in training — built.** `ExpeditionSequenceEnv._start_next_combat` never
passed `torch_level`, so every combat ever trained ran at the engine default of 100.0. Torch
is in the observation, so the feature was constant and carried nothing, and the policy was
off-distribution for the ~32% of real captured combats below radiant, where `rules.json`
changes crit, ACC, dodge, stress and surprise on both sides. Now drawn per combat from
`TORCH_LEVEL_DECILES`, the deciles of 206 real in-battle captures, via the scenario RNG.
*Falsifier*: the eval spread between radiant and dark combats should narrow. There is no
prior number for it because the case never occurred.

## The engine audit, round 8: the full data sweep (2026-08-23)

Every remaining family, plus a directory-level check. Seven mechanics wired (see
`CHANGELOG.md`); what follows is what is deliberately still open.

### Declared: `stall_round_reset_threshold` makes the counter cycle

Read as "at counter 4, reset", matching its two siblings
(`stall_round_stress_effect_threshold` 3, `stall_round_summon_effect_threshold`
4), which both mean "at this value, do this". Stalling therefore charges +5 party
stress on two rounds in four instead of every round from the third onward.

**The other reading**, and it points the opposite way: 4 could be the number of
consecutive NON-stalling rounds needed before the counter clears, where this
engine clears it instantly. That would make stalling strictly harsher. It is the
weaker reading -- the counter already has a documented instant reset (>=2
stall-invalidating actions in a round) and a second, slower reset on the same
counter would need a name that says so.

**Falsifier:** stall a fight past the reinforcement wave in the real game with a
party at known stress and count the +5 ticks over eight rounds. Six means the old
behaviour, four means this one.

### Open: the virtue `block_effect`

All five virtues ship `block_effect` at **chance 1.0** with a `BarkStressHeal`
payload. A virtuous hero blocking every incoming effect outright cannot be the
reading, and nothing in the data narrows it to a subset. Parsed into
`TraitDefinition.reactions` and deliberately **not** wired; a test asserts it
blocks nothing, recording the decision rather than endorsing it.

### Resolved, and it was never a contradiction: `override_trait_type_ids`

Filed as "both cannot be right" and investigated 2026-08-23. Both are right,
about different things:

* `is_generated: False` says berserk is never produced by the RANDOM DRAW.
  `_draw_trait`'s `is_rollable` filter was correct and is unchanged.
* `overstressed_modifier:` says this hero does not use the random draw at all.
  It is a per-hero OVERRIDE of the outcome, and reading it satisfies the
  standing rule rather than breaking it -- nothing in the wiring tests a hero
  class.

Wired. See `CHANGELOG.md`. What remains open is the READING, not the mechanism:

**Declared: the override replaces the WHOLE outcome, so a Flagellant can never
be virtuous.** Two pieces of evidence. The record is named
`overstressed_modifier`, for the whole overstress event rather than the
affliction half of it; and its `_ids`/`_chances` pair has the same shape as
`.random_monster_class_ids`/`_chances`, four entries away in the same `.darkest`
field table, where the chances are WEIGHTS over a draw that always happens
rather than a gate on whether it happens.

**The other reading:** the override could replace only the AFFLICTION side,
leaving the 25% virtue roll intact. Weaker -- it makes `chances 1.0` redundant
with the pool weights it replaces, and it does not explain the key sitting on a
record named for overstress rather than for afflictions.

**Falsifier, and it is cheap:** run a Flagellant to 100 stress repeatedly under
`-randomseed` and count virtues. **Any** virtue at all falsifies the declared
reading; ~25% of them confirms the other one.

### Inert, and recorded so it is not re-reported

- **`can_target_afflicted` / `can_target_virtued` /
  `can_target_not_overstressed`** -- all 40 shipped carriers set them to `true`.
  Parsed and branched, changes nothing, same status as `can_target_last_hero`.
- **`performer_health_ratio_min` / `_max`** -- 67 definitions, **zero** fielded
  carriers. Boss-only.
- **`does_count_towards_stall_penalty`** -- 12 carriers, zero loadable here.
- The fourteen trinket proc hooks other than `attack_skill_additional_effects` --
  zero pool carriers.

### Out of scope, with the reason

`.curio_result_type` (838 carriers, and curios are excluded by design),
`.summon_can_spawn_loot`, the eleven disease flags, `.source_heal_type`
(quirk-routing metadata), `.summon_does_roll_initiatives` (boss-only), and
Backdraft's `.requires_burning` / `.copy_burn_behind` / `.dmg_per_burn_stack` --
all three sit on that one skill, which is already recorded as deliberately
unimplemented because the mod's own file says it needs engine support.

### Shipped-data bug, not ours

`carrion_eater_large_A` is named by 8 Warrens hall/room rows and appears nowhere
else in the install -- no definition, no localization. Those rows field one
monster fewer than they read. Left alone.

## The engine audit, round 7: effect keys nobody read (2026-08-23)

Method: every `.key` any shipped effect file uses, filtered to effects this
simulator FIELDS (named by a hero skill or a loadable monster), ranked by live
carriers. 25 unread keys survived that filter; five are now implemented (see
`CHANGELOG.md`). What follows is what is still open.

### One declared reading: does a FORCED unstun grant the recovery buff?

`.unstun` (Arbalest's Flare, Musketeer's Skeet Shot) clears a stun off an ally.
Natural stun recovery grants `STUNRECOVERYBUFF` -- +50% stun resist for 2
rounds -- via `_apply_stun_recovery`. **No shipped key says whether a forced
unstun does the same.**

Implemented as: it does. Not because the data says so, but because
`_apply_stun_recovery` is the only other stun-clearing path in the engine and
one convention beats two. It matters: it decides whether Flare can be used to
free an ally and then have that ally re-stunned the same round.

**Falsifier, and it is cheap:** in the real game, Flare a stunned ally and
re-stun them the same round with a known stun chance. If it lands at the base
rate, delete the `_apply_stun_recovery` call in `_apply_effect_to_target`.
`rules.json`'s `stun_recovery_buff_consecutive_applies: true` is suggestive but
does not settle it -- it says stacking is allowed, not what grants a stack.

### `stall_round_reset_threshold = 4` is still unread

Missed when the other four stall keys were wired on 2026-08-23. Two readings,
and they are not close: the stall counter resets after 4 rounds without a
stall-invalidating action, or the stall *penalty* stops after 4 rounds of it.
Implementing the wrong one changes how long a stalling party is punished.
Nothing here reads it today, which is the third possible behaviour.

### Deliberately not implemented, with the reason

- **`.curio_result_type`** (838 fielded carriers, by far the largest) --
  curios are out of scope; the key tags an effect as reusable as a curio
  outcome and says nothing about combat.
- **`.summon_can_spawn_loot`** (43, monster) -- loot is out of scope.
- **`.crit_doesnt_apply_to_roll`** (11, monster) -- all eleven are disease
  applications, and diseases are a town mechanic this simulator does not carry.
- **`.summon_does_roll_initiatives`** (9) -- whether a summoned add acts the
  round it arrives. Real, boss-only (Drowned Captain, Brigand Cannon). Summons
  currently never roll in.
- **`.controlled_burn_amount` / `_duration`** (5 each) -- already recorded as
  deliberately unimplemented under the burn subsystem; the mod's own file says
  they need engine support.
- **`.source_heal_type`** (23) -- routing metadata for quirk interactions, not
  a combat payload.
- The remaining singletons (`.clear_rank_target`, `.performer_rank_target`,
  `.apply_with_result`, `.skill_instant`, `.critical_rating`,
  `.virtue_blockable_chance`, `.capture_remove_from_party`, `.has_description`,
  `.buff_is_clear_debuff_valid`) each have one or two carriers and no
  behaviour this engine models.

### What the same audit confirmed was already correct

Recorded so it is not re-run: every buff `stat_type` equipped by a trinket in
the comp pool is named in `combat_engine.py`; all six camping-effect keys
across the base file and all four DLC files are read; the in-combat provision
set is right; and the stun-recovery buff was already implemented.

## The comp layer: what the search may propose, and what it cannot know (2026-08-22)

`tools/comp_ga.py` searches for party compositions. Three of its inputs are
declared readings rather than data, and one whole class of question is outside
what it can answer at all.

### One one-off trinket per comp — Fran's rule, stricter than the game's

`limit` is the game's own marker for a trinket you can only ever own one of, and
it is data: it lands on exactly the four families Fran named (Ancestral,
Shambler ancestral, Crimson Court, Color of Madness comets) plus boss trophies
and eleven `very_rare` ones, 56 of a class's 154 wearable trinkets.

What is **not** data is the cap. The game's rule is per id — you own one
Ancestor's Candle, so one hero wears it, but nothing stops another wearing
Ancestor's Map. `UNIQUE_TRINKETS_PER_COMP = 1` caps the whole comp at one,
because the first search produced comps wearing four to six at once, which is
most of the boss-drop collection on a single party. Fran's instruction, and his
own note that "realistically it's just 1 on the whole collection, but for our
task this simplification works".

*Falsifier*: a comp Fran plays that fields two one-offs he owns. That would mean
the cap is costing real options and the per-id rule is the right one.

### The class-locked trinket bias is a prior, not a restriction

`CLASS_TRINKET_BIAS = 0.5`. A class has ~6 unlimited class-locked trinkets out
of ~98 wearable, so a uniform draw finds a designed synergy 6% of the time and a
twelve-generation search never explores them. The bias says where to look first;
every generic trinket stays reachable and selection drops anything that does not
earn its slot.

*Falsifier*: run with `--class-trinket-bias 0` and compare the emitted comps
paired. If the biased run is not better, the prior is buying nothing.

### The wearable-trinket pool is read off the corpus, not chosen

`UNIVERSAL_TRINKET_RARITIES` is the pool `--kit-random-p` draws a *sampled*
party's kit from, and it is right for that. It is the wrong pool for designing a
comp: 455 of the 1039 trinket slots across the 136 curated comps — 44% — sit
outside it (Shambler, Crimson Court, comets, boss trophies, the Collector's, the
Madman's; Ancestor's Candle alone is worn in 28). So `wearable_rarities` takes
every rarity a curated comp equips, plus the universal ones.

*Why this is not just a wider guess*: the rule names its own evidence and updates
itself — a new curated comp wearing a new rarity widens the pool with no code
change — and it still excludes the 294 `kickstarter` backer trinkets, which no
comp wears. *Falsifier*: a GA comp Fran rejects because he does not own the
trinket. That would mean the corpus is not a proxy for his inventory and the pool
needs an explicit ownership list instead.

### The shaping weights are a convention, and are bounded so they cannot matter much

Fitness is `success_rate + W * shaping`. `shaping` is a weighted mean of
completion (0.35), zero-death (0.25), fraction of combats reached (0.25) and
1 - stress/200 (0.15). Those four weights are a choice. What is *not* a choice is
`W = min(0.04, 0.8 / episodes)`, strictly below `1 / episodes` — so the entire
shaping term is worth less than one extra successful episode, and can only order
candidates the headline could not separate. Early generations are all zeros and
need a gradient; the bound is what keeps that gradient from deciding anything it
should not.

*Falsifier*: rank the same hall of fame with the shaping term deleted. Any
reordering among candidates whose success rates differ is a bug in the bound.

### `STRESS_SCALE = 200` is invented

Stress is a level, not a rate, and the shaping mean needs it in [0, 1]. 200 is two
full afflictions' worth. Nothing in `rules.json` states a scale for "how bad is
this much stress"; a different divisor changes only the 0.15-weighted term inside
a term already bounded below `1/episodes`, which is why it was not worth deriving.

### What the search cannot see, and must not be read as ranking

* **The policy is the measuring instrument.** `P(success)` is the probability
  *this checkpoint* completes the run, and it plays worse than a good human. A
  comp that needs play the policy has not learned scores low for a reason that is
  not about the comp. The three-way ruler disagreement recorded in `CLAUDE.md`
  applies here in full.
* **No town layer.** Gold, heirlooms, hero availability, stress healing between
  raids, and whether a roster can even field four of a class are invisible. A
  comp of four resolve-6 heroes in rank-5 gear is what gets scored; assembling
  one is a campaign's worth of work the search does not price.
* **One zone and one length per run**, unless `--zones` is given several. Cross-
  region play is now the training default, so a comp tuned on crypts alone is
  tuned on the region whose enemies are undead — which is exactly why the Duelist
  run's Crusader stacks are plausible rather than suspicious, and equally why
  they should not be promoted as general-purpose comps without a second run.
* **No quest objective and no curios.** Every scenario is "walk the corridors and
  win the fights".

### The scenario has to be re-chosen after every retrain

A fitness everything scores 100% on ranks nothing. On the 2026-08-21 retrain,
tier 3 was a ceiling at both medium and long, tier 5 long a floor, and tier 5
medium the only one of the four that discriminated. That is a property of the
*policy*, not of the game, so it moves. The probe is four one-generation runs and
is part of using the tool.

### `metadata/COMPS/GA_comps/` is not data

Nothing in `data/` reads it, deliberately. A search result that silently joined
the training pool would change which comps every archived `--comp-seed` selects,
and would make an unreviewed machine guess indistinguishable from a comp Fran has
played. Promotion is a `mv` into `curated_comps/` — plus, for a comp of ordinary
classes, a CSV row, which is the part that shifts archived selections and is
therefore a decision.

## The engine audit, round 6: monster-brain gates (2026-08-21)

`base.monster_brains.json`, 165 brains. Gates on `skill_selection_desires`
were read only inside the branch of whichever desire type they were first met
on, so every gate on `specific_skill` (130+ instances) and
`performing_turn_skill` (44) went unread — **21.8% of hall/room monster slots**,
and **5.2% of real enemy skill selections** drawn from a different weighting.
On the target side `is_enemy_target_desire` was not read at all, which dropped
the baseline flat desire from enemy targeting for 9 pool monsters.

Fixed: one shared `_skill_desire_gates_pass`; the corrected side filter;
`can_target_stunned` / `can_target_deaths_door` / `can_target_last_hero` /
`is_guarded` / `is_guarding` / `is_exclusive_desire`.

### Both light comparisons are strict — read off the thresholds, not chosen

`lightbelow X` is `torch < X` and `lightabove X` is `torch > X`, so the
threshold itself belongs to neither side. The shipped constants are the
argument. The darkness bands exclude their lower bound (`lowest_excluded:
true`), so their tops are 25 / 50 / 75 — and the two thresholds that dominate
the data, `lightbelow 26` (105 trinkets) and `lightabove 75` (56), land exactly
on a band edge only under a strict reading:

| written | strict | means |
|---|---|---|
| `lightbelow 26` | `torch <= 25` | precisely "black as pitch, or black" |
| `lightabove 75` | `torch > 75` | precisely "radiant" |

Read inclusively each spills one point into the neighbouring band and names
nothing. The light quirks agree independently, and nobody tuned them for us:
`lygophobia` (fear of the dark) is +20% stress taken at `lightbelow 26`,
`phengophobia` (fear of daylight) +20% at `lightabove 75`, `nocturnal` -2 SPD
in the light and `diurnal` -2 SPD in the dark.

Kept as a *declared reading* rather than a fact because the shipped data is not
perfectly tidy: `dark_bracer` pairs `lightbelow 51` with `lightabove 51`, so at
torch exactly 51 neither fires. That one-point dead zone is in the game's own
numbers; the alternative reading replaces it with a point where a buff and a
debuff both fire, which is worse.

*Falsifier, and it is cheap*: equip `dark_bracer` and enter a fight at torch
exactly 51, then at 50 and 52, reading the applied buffs out of
`persist.raid.json`. Strict predicts nothing at 51, the DEF buff at 50 and the
damage debuff at 52. Fold it into the N2 capture session.

### `in_camp` is False inside a combat, and that is the whole rule

`CombatEngine` is combat; `engine/camping.py` never touches the buff
machinery. So no `in_camp` buff can be active in anything
`_is_conditional_rule_active` sees. Not a gap — the resolution of one. It had
been reaching the always-active fallback, which handed the party
`cc_set_vest_salacious_diary`'s +33% stress healing and `campers_helmet`'s +20%
stress healing received in the middle of a fight.

### `monsters_size_*` is rank occupancy — a declared reading

The Siren's `conch_summon` desire carries `monsters_min`/`monsters_max` **and**
`monsters_size_min`/`monsters_size_max`. Two keys on one record cannot be the
same quantity, and only one is named for size, so `monsters_*` is a head count
and `monsters_size_*` is the sum of `size` over living monsters. The previous
code read the size keys as a head count inside `heal_skill`; that was an
inherited convention with no argument behind it.

*Falsifier*: `-firstroombattle` a comp of two size-2 monsters whose brain gates
a skill at `monsters_size_max: 3`, with `-alwayshit` for determinism. Under the
occupancy reading the skill is never used (4 > 3); under a head count it is
(2 <= 3). One capture settles it.

### `can_target_last_hero` is inert here, on purpose

It means the last **living** hero — the Hag's `hag_grab` carries it, and
grabbing your last hero would leave nobody able to act. In a weighted selection
over the party it can never change an outcome, because when one hero is left
there is exactly one candidate. Implemented and pinned by a test that records
why it does nothing, so it is not later "fixed" into something that does.

### Still unread, and why

- **`control_count_min`/`_max`** (4) — needs the Siren's charm/control
  subsystem, which this simulator does not model. Siren only.
- **`per_round_chance`** (2) — no stated semantics; a probability, a cap, or a
  budget are all consistent with the two carriers (`cell_white`,
  `ancestor_small_D`), neither of which is in the hall/room pool.
- **`is_greater_comparison` on `resistance_target`** — read, but its reading is
  worth a note: 9 of 17 stun-typed carriers set it true, which under the
  current formula means *prefer the hero with the HIGHEST stun resist* for a
  stun skill. That is either a designer choice or evidence the weight is an
  argmax selector rather than a proportional one. Not changed without a
  capture; changing it on suspicion would silently reshape 51 desires.

## The engine audit, round 5: buff stat_types (2026-08-21)

`_apply_buff_to_stats` is where trinkets, quirks, traits and skill effects all
cash out, so a `stat_type` it has no branch for is a **silent no-op**. Sweeping
the 1869 buff ids reachable from real content found 25 unhandled types; ranking
them by what a real expedition actually EQUIPS (150 sampled) rather than by how
many exist is what made the list actionable.

Fixed: `stun_chance` (1.85 per expedition, the largest in the game),
`resolve_check_percent` (0.47), `move_chance` (0.19).

### DEBUFF RES is rolled now, and the whole effect shape changed with it

**Fixed 2026-08-22.** What follows is kept because it is the story of how the
predicate was settled, and because the *falsifier* below is still worth running
— but the gap itself is closed. `CombatEngine._effect_resist` maps a payload to
its resist type, and `_apply_effect_to_target` makes ONE draw and two
comparisons: `roll < chance` for what cannot be resisted, `roll < chance -
resist` for what can.

**The declared reading, and the falsifier for it.** The reading is that the
game subtracts the resist from the chance rather than rolling twice. The
evidence is the data's own: 572 of 1973 effects ship `.chance` above 1.0, in a
per-rank ladder (1.0 / 1.2 / 1.4 / 1.6) that reaches 5.0, and every one of those
values is meaningless unless something is subtracted from it;
`effect_crit_roll_mod` is shipped as a modifier on the roll; and
`disease_hero_disease_resist_weight` weights a resist inside a formula that must
subtract it. *Falsifier*: `-alwayshit` plus a rank-1 and a rank-5 version of one
debuff skill against the same monster, counting how often each lands. The
subtractive reading predicts the two differ by the ladder's step; two
independent rolls predict they are identical.

**Mark is the clause that changed.** This file used to record the predicate as
blocked because "Mark IS resisted by DEBUFF RES in the real game". The wiki says
the opposite and always did — *"Mark: cannot be resisted by anyone"*
(`WIKI_MECHANICS.md`) — and the data agrees, since Mark carries `.tag` and no
stat field. With Mark out, the predicate is the complement of the other resist
types: **a buff crossing to the other side is a debuff.** The side test is the
whole rule, because nothing in an effect record distinguishes Bolster from a
Weakening Curse.

Two things this does NOT do, deliberately: it does not gate Mark, and it does
not gate a self-inflicted debuff (Leper's Vulnerability, Hellion's Exhaust) —
both fall out of the same rule.

### The history: DEBUFF RES was never rolled — the largest known gap in the engine

**Measured for the first time on 2026-08-22, and it is worth 20 points.**
Fran read a search-proposed comp and asked why its Vestal held Illumination,
"probably the worst skill in the game". Gating stat debuffs on the target's
DEBUFF RES (55% then resisted) drops that comp from 85.0% to 65.0% success and
turns the skill's advantage from -12.5pp-to-swap-out into +2.5pp. Champion
monsters average 0.802 debuff resist against a `chance 1.4` debuff, so the real
game lands it ~60% of the time and this simulator lands it 100%.

The blocker below is unchanged and the gate was NOT shipped — `is_stat_buff` is
a guess at the predicate, and Mark is resisted in the real game while carrying
only `.tag 1`. What changed is that the gap now has a number, a reproducible
experiment, and the distinction of being the first one caught by a human
looking at output rather than at data. See `CHANGELOG.md`.

**A related hazard that is NOT a bug**: `_apply_buff_to_stats` does
`c.dodge += amt` with no floor and `compute_hit_chance` is unclamped, so a
dodge debuff routinely takes a target below zero (measured -0.637, on 7.4% of
attacks). It changes nothing today — all 205 such attacks already had a hit
chance of at least 1.04 against `cutoffAlwaysHit = 0.95` — and re-scoring with
a floor is byte-identical. It would start mattering if anything clamped the
chance, or against a low-accuracy attacker facing a high-dodge target.

`debuff_resist` is parsed onto every hero and monster, raised +10% per resolve
level, and modified by trinkets and quirks. **Nothing rolled against it** until
2026-08-22: its only readers were the monster AI's target weighting and
`_get_resistance`, so every stat debuff landed unconditionally and
`debuff_chance` (0.65 buffs equipped per expedition) had no roll to modify.
Both are wired now.

Not implemented because the shipped data does not say **which** effects it
resists, and the plausible rules disagree at the edges:

- "a stat modifier applied to the opposing side" excludes Mark, and Mark IS
  resisted by DEBUFF RES in the real game — it carries `.tag 1`, no stat field.
- there is no per-effect marker. `.buff_source_type` is `bsrc_crit` on all 14
  carriers; `.debuffenemy` exists on exactly one effect in the game ("Slow").

Getting the set wrong silently changes how a large fraction of skills resolve,
in both directions, which is why it stayed open for as long as it did. It was
the second bullet that broke the deadlock: the wiki says Mark cannot be resisted
by anyone, so the "excludes Mark" objection to the obvious predicate was never
an objection at all.

*Falsifier, and it is cheap*: `-alwayshit` plus a hero skill that applies a
known debuff, aimed repeatedly at a monster with a high `debuff_resist`, then
read the applied buffs out of `persist.raid.json`. A resist rate near the
monster's DEBUFF RES confirms the mechanic; zero resists refutes it. The same
capture answers *which* payloads are gated if it is run once against a Mark and
once against a stat debuff.

### Unhandled and left alone

`scouting_chance` (1.06 per expedition) needs a map layer; `food_consumption_
percent`, `resolve_xp_bonus_percent`, `starving_damage_percent`,
`activity_side_effect_chance`, `upgrade_discount` and the `shard_*` family are
town, roster or hunger. `resistance/trap` (21 buffs) is the dungeon layer.
Everything else is under 0.07 equipped per expedition — the bar to beat is
`move_chance`'s 0.19.

## The engine audit, round 4: the effects file, and a parser bug (2026-08-21)

Same sweep pointed at `*.effects.darkest`: 145 distinct keys, **75 unread**.
Most are correctly out of scope — `.curio_result_type` alone is 1333 of them,
and `.set_monster_class_*` is the Ancestor/Farmstead transformation chain.

Fixed: `.shuffletarget` / `.shuffleparty` (24 effects), and the **parser bug**
that hid two thirds of them — a valueless flag vanished or survived depending on
how much whitespace followed it. Verified surgical by diffing the whole parsed
store: 13 effects changed, all shuffle.

### Effect keys still unread, and why

- **`.health_damage` (17 effects)** — direct HP damage bypassing the attack
  roll. Shieldbreaker-only among heroes, **zero monsters**, so it is one class's
  skill and not a general mechanic.
- **`.initiative_change` (6)** — Duelist-only, and she is in no comp.
- **`.crit_doesnt_apply_to_roll` (16)** — moot rather than missing. 15 of the 16
  are **disease** effects and disease is not tracked in combat; the odd one out
  is `Keening Bolt Stress On Attack`. Its only job is to stop the crit bonus
  applying to a roll whose outcome is already inert here.
- **`.torch_increase` (30)** — the torch is fixed for the encounter in this
  simulator (`_apply_torch_modifiers` runs once at reset), so a mid-combat
  change has nowhere to land. Real, and a bigger change than it looks.
- **`.clear_rank_target` (6), `.skill_instant` (13), `.is_trigger_effect` (10),
  `.can_crit_heal` (9), `.critical_rating` (5)** — small, and none reaches the
  hall/room pool.
- **`.summon_can_spawn_loot` (85), `.summon_does_roll_initiatives` (22)** — loot
  is out of scope; the initiative one is worth a look if summoning ever needs to
  be exact about when an add first acts.

## The engine audit, round 3: `.darkest` keys nobody reads (2026-08-21)

Same method again, pointed at the third data source: every `.key` in every
`.info.darkest`, hero and monster, diffed against the source. **Include `dlc/`**
— a first pass scanned only `heroes/` and under-reported `.ignore_guard` by two
of its three carriers, both DLC classes.

Fixed from it: `.self_target_valid`, `.required_performer_hp_range`,
`.ignore_guard` (all legality — see `tests/engine/test_skill_legality.py`), and the
four `battle_modifier:` surprise flags that every monster file carries.

### Monster keys still unread, roughly worst first

- ~~**`life_link: .base_class` — 177 monsters.**~~ **BUILT.** An add dies when
  its parent does, and the link is inert with no parent on the board — the
  guard the naive reading is missing, settled by the counts (1239 of 1266
  linked-add comp slots have no parent; all 27 that do are boss or named).
  Matched on the monster's directory. `tests/engine/test_life_link.py`.
- ~~**`battle_modifier: .can_be_damaged_directly` (12)**~~ **BUILT** — the
  Hag's empty cauldron, a 100 HP size-2 prop that was the fattest and
  front-most target in her boss fight. `tests/engine/test_undamageable.py`.
- **Unreachable, and therefore deliberately not built.** Every remaining unread
  monster key has **zero carriers in the hall/room training pool**:
  `.can_be_random_target` (8), `.is_valid_friendly_target` (12),
  `.does_count_towards_stall_penalty` (12),
  `.can_relieve_stress_from_killing_blow` (12),
  `.does_count_as_monster_size_for_monster_brain` (12),
  `skill: .starting_cooldown` (4), `skill: .rank_damage_modifiers` (17).
  Every carrier of the last two is Crimson Court or Farmstead content
  (Countess, Baron, Esquire, Miller, Plowhorse) or a roaming boss this project
  does not field. Semantics captured so a future integration need not
  re-derive them: `starting_cooldown N` makes a monster skill unavailable for
  the first N rounds, and `rank_damage_modifiers -0.8 -0.65 -0.3 0.0` on an
  AoE is a per-target-rank damage falloff.
- **Reachable but not resolvable from shipped data.** The Drowned Captain's
  `companion: .monster_class drowned_anchored .heal_per_turn_percent 0.025`
  names a companion class that is NOT the monster his summon skill spawns
  (`drowned_anchor`) — two distinct monsters with identical HP. Which one the
  2.5%-per-turn regeneration attaches to decides whether the correct play is
  "kill the anchor first", so it is worth getting right and not worth
  guessing. The Siren's `controller: .stress_per_controlled_turn` belongs to
  the charm mechanic, already an out-of-scope exclusion.
- **`death_class: .is_valid_on_blight_dot` / `.is_valid_on_burn_dot` (10 each)
  — rechecked, and the docstring's conclusion survives with a wrong number.**
  There are **183** `death_class` records naming a class, not 125, and 10 do
  carry the DoT flags. But all 10 are **phase transitions**, not corpses:
  `ancestor_small_D -> ancestor_big_D`, `spire_D -> galaxy_D`,
  `curtain_E -> sycophant_B`. A `death_class` whose `monster_class_id` is
  another LIVING monster is the Ancestor/Farmstead transformation chain, which
  is a separate documented exclusion. No actual corpse record carries them, so
  the behaviour is right and only the count in the docstring is stale.
- **`shape_shifter:` (12)**, **`companion: .heal_per_turn_percent` (6)**,
  **`controller: .stress_per_controlled_turn` (3)** — genuinely new subsystems.
- **`shared_health: .id formless` (12)** -- **UNMODELLED, and it was not on
  this list until 2026-09-19.** Every other unread monster key here had been
  counted; this one appeared in no doc and no code. The twelve records are the
  four Flesh parts (`formless_melee` / `_ranged` / `_guard` / `_weak`) across
  the three difficulty tiers, and they all name ONE pool. In the game, damage
  to any part depletes the single pool they share, so "killing" a part does
  not reduce the boss's remaining health.

  **The simulator gives each part its own bar**, so The Flesh is roughly **4x
  too durable**: 4 x 51 = 204 effective HP at Champion against a real 51. It
  is reachable -- all twelve parts sit in the `boss` comp pool, so any run
  with `boss_quest_p > 0` fights the inflated version. The default is 0.0,
  which is why nothing has noticed.

  The per-part PROT spread is correct and is what makes the pool matter:
  `formless_weak` (the Heart) is PROT 0 and `formless_guard` reaches 0.9 on
  Champion, so WHICH part is struck decides how much of the shared pool the
  blow removes. Modelling the pool without that spread would miss the fight.

  Not implemented yet: damage lands in five places (`resolution.py:238`,
  `_dots.py:119` and `:143`, `_afflictions.py:403`, `combat_engine.py:2019`)
  and `resolution.py` is a pure function with no engine handle, so there is no
  single post-damage hook to hold the propagation; death routing is split
  between `_handle_deaths_door` and `_check_deaths_door_dot` too. Plan and
  falsifier in `NEXT.md`.

- **`captor_empty:` / `captor_full:`** — the Hag/Siren capture mechanic, already
  a declared out-of-scope exclusion.

### Hero keys still unread

`.requires_burning`, `.dmg_per_burn_stack` and `.copy_burn_behind` (5 each) are
the Runaway's Backdraft, already documented as deliberately unimplemented.
`.refresh_after_each_wave` (10) and `.generation_guaranteed` (18) are wave
bookkeeping and roster generation. The `generation:` / `skill_selection:` /
`weapon:`/`armour:` blocks are town and roster.

## The engine audit, round 2: `rules.json` scalars nobody reads (2026-08-21)

Same method as the dataclass sweep below, pointed at the config instead: diff
every scalar in `shared/rules.json` against every attribute access and string
literal in `data/`, `engine/`, `env/`, `bridge/`. **83 are referenced nowhere.**
Most are correctly out of scope (town, disease, quirk slots, barks, New Game+,
the camera constants) and one was a false positive — `provision_hp_heal` is
already sourced as `FOOD_HEAL_FRACTION`, with the key named in its comment.

Fixed: `death_party_stress_chance`/`_damage` (a death charged the survivors
nothing), and `effect_crit_roll_mod` (a hardcoded wiki constant that turned out
to be shipped data of exactly the same value).

### Death's Door "repeat" stress — open, with a cheap falsifier

`deaths_door_self_stress_dmg_repeat` (4.0) and
`deaths_door_companion_stress_dmg_repeat` (2.0) are parsed, read by the game,
and read by nothing here. Both are exactly half their non-repeat counterparts,
and there are two incompatible readings:

**A** — each subsequent hit taken while already at Death's Door. The engine's
`else:` branch currently applies no stress at all, so A means we undercharge
the common case.
**B** — the second *entry* to Death's Door. Then the `else:` branch is right
and the first branch should switch on `mortality_tier != "none"`.

Implementing either when the other is true is a regression. No shipped file
says, the wiki does not address it, and the capture corpus has **no Death's
Door in it** (360 decodable raid snapshots, zero heroes at 0 HP, zero with
`visited_deaths_door`).

*Falsifier*: `-startatbeforedeathsdoor -alwayshit`, then read hero stress out of
`persist.raid.json` across two consecutive hits taken at Death's Door. +4 on the
second is A; no change is B.

### Still unread, and judged out of scope rather than missed

`scouting_*` and `trap_scout_disarm_bonus` (no map layer), `curioTriggerChance_*`
(curios out of scope), `hallway_hunger_*` and `dotHallwayStepRatio` (traversal),
`disease_*`, `quirks_max_*`, `new_game_plus_*`, `the_blood_item_*` (town, roster,
Crimson Curse), and the `m_*`/`*BarkChance` presentation constants. The four
`stall_round_accelerated_only_*` / `stall_extra_round_size_*` keys are a real
combat refinement to the stall system and are the next candidate here.

## The engine audit: what is still unread from real data (2026-08-21)

Method, so it can be repeated: diff every dataclass field in `data/types.py`
against every attribute access **and string literal** in `engine/`, `env/`,
`bridge/`, `tools/`; keep only fields the shipped data actually populates. The
string-literal half matters — `INLINE_STAT_FIELD_MAP` reads a dozen fields by
name in a loop, and without it the scan reports them all as dead.

That found the summoning gap (fixed) and left these, worst first.

### ~~Surprise is not modelled at all~~ — BUILT 2026-08-21

Was the largest remaining hole, and it compounded two things already in place:
torchlight is **sampled per combat** rather than pinned at 100, and the
darkness bands carry exactly the surprise modifiers — so the mechanic that
makes the dark frightening was the one part of the dark not simulated. A
policy trained without it read low torch as "slightly worse stats" instead of
"you may lose the first round".

Now rolled at `reset()`, both sides independently, from
`base + darkness band`, clamped to the shipped 0.65 cap. Measured over 400
expeditions on the real training torch distribution: **the party is surprised
in 16.8% of combats, the monsters in 27.8%, and 40.5% of combats now open with
someone flat-footed.** Three effects, all from the wiki's Surprise page — the
surprised side acts **last in the opening round**; a surprised **party is
scrambled**, which is the expensive half; and a surprised party **cannot
retreat** that round. `tests/engine/test_surprise.py`.

*Falsifier*: dark combats should now cost measurably more than the stat deltas
alone predict, and a party that opens surprised should retreat later and win
less. Nothing anchors this yet — no eval predates the mechanic.

**Two things it still does not do, both stated rather than hidden:**

- **Scouting.** The `known_` bases are parsed and unused. A scouted room sets
  the party's base to `-1.0` (a hard never, and the sign is deliberate: it has
  to survive the darkness table adding +0.40) and *raises* the monsters' to
  0.25. Modelling it needs a map layer the combat sim does not have, so every
  encounter is treated as unscouted — the pessimistic side for the party.
  Corridor vs room costs nothing at the shipped values, which are identical
  (0.1) for all four unscouted combinations.
- **The two surprise-chance buff terms are wired but currently unreachable**,
  for the ordering reason immediately below.

### A retreat leaves the encounter on the map — two of three options unmodelled

Fixed 2026-08-21: a retreat no longer consumes the combat. The party pays the
stress, forfeits the per-combat win bonus, and **faces the same room again**.

Of the three things a real fled party can do, this env models one:

| option | modelled | why not |
|---|---|---|
| re-take the fight | **yes** | — |
| abandon the quest (+20 stress, quest failed) | no | needs a new action; the expedition has no "leave" that is not a combat action |
| re-route to a different room | no | needs a map. `bridge/route.py` is the layer that owns this |

So a party that cannot win a room and cannot afford to keep re-taking it has no
way out except stalling into truncation, which is a real distortion in the
pessimistic direction. Both missing options are dungeon-layer decisions, the
same category as traversal and curios.

**One declared convention**: the re-taken encounter is the same monsters at
**full HP**. The wiki says a wandering boss "will reappear", which implies the
encounter regenerates rather than resuming damaged, but no shipped file states
it and no capture in the corpus contains a retreat. *Falsifier*: retreat from a
room battle in a live raid, re-enter, and read the monsters' HP out of
`persist.raid.json` with `bridge/live_state.py`.

### ~~Equipment is applied AFTER the opening enemy turns~~ — FIXED 2026-08-21

Both expedition envs applied persistent equipment after `reset()` returned, and
`reset()` at its default `auto_resolve_to_hero=True` has already let faster
enemies act. Two opposite bugs in the same two lines: the opening blows landed
on heroes with no gear dodge, no trinket PROT and the base max-HP ceiling; and
then, on the real-party path where the HP target is "full", writing HP last
**healed the party back and erased the round**. Measured at **2.79 HP per
combat refunded in 34% of combats** — the free healing accrued to exactly the
parties that lose initiative.

Now `CombatEngine.reset(on_board_ready=...)`, a window after the board is built
and before the torch, the surprise roll, initiative or any turn. It switched on
round-1 gear defence, made a +SPD trinket affect the round it is bought for,
and made the two surprise-chance buff types reachable at all.

The buff terms are consequently **no longer a gap** — `book_of_intuition` takes
a party's radiant surprise chance from 10% to 0%.

### Effect flags parsed and unread

- **`can_apply_on_death`** — 321 effects, 167 monster skills. Whether an effect
  still lands on a target that died to the same hit.
- **`is_queued`** — 969 effects. Effect ordering within a resolution.

Both are correctness-at-the-margins rather than missing mechanics, and neither
has been measured.

### Rules values parsed and unread

`stun_ends_turn`, `initiative_base`, `monster_crit_stress_chance`,
`player_crit_relief_chance`, `deaths_door_self_stress_chance_repeat`,
`deaths_door_self_stress_dmg_repeat`, `stress_affliction_resisted`,
`camp_rest_point_threshold` (deliberate — see the camping note),
`camp_rest_number_of_hero_threshold`, `camp_relieve_stress`.

`affliction_base_onset_chance` / `_min` / `_max` are also unread but that is
**correct**: `stress.py` rolls `affliction_critical_success_chance`, the
complement, and documents why.

### Confirmed harmless

`SkillLevelData.extra_damage_vs_*` (8 fields) is zero across all 1252 skill
levels — bonus-damage-vs-type goes through buffs instead. `human_effects` /
`beast_effects` are superseded by `mode_effects`. `HeroStats.disease_resist`
and `trap_resist`, `EnemyDefinition.loot_code`, `MapGenSettings.*` are all out
of scope for a combat simulator.

## The training distribution, and what is still wrong with it (2026-08-20)

Five knobs now describe what a training run sees, all defaulting off. What is
NOT yet faithful, in the order it is likely to matter:

- **Roaming bosses are absent entirely.** The Shambler, Collector, Fanatic and
  Thing from the Stars spawn on their own conditions mid-expedition (torch
  level, a curio, a stress threshold), not in a fixed room. `boss_quest_p`
  models only the four regions' **static** bosses, which sit in the last room.
  A roaming boss needs a spawn trigger, which nothing in the expedition loop
  has.
- **Bosses have per-difficulty variants and one shared policy.** The mash rows
  give `_A`/`_B`/`_C` per tier and those load correctly, but nothing represents
  that each boss wants a *different* approach — the Prophet's pews, the Hag's
  cauldron, the Swine Prince's Wilbur. The policy sees them as ordinary
  monsters with unusual stats. Fran flagged this directly: *"you shouldn't
  approach each boss equally"*.
- **`RANDOM_QUIRKS_PER_POLARITY = (1, 3)` is invented.** No shipped file states
  how often a roster hero carries a given number of quirks. The real cap is 5
  per polarity and recruits arrive with one or two.
- **Resolve weights are Fran's judgement, not data** (Apprentice 20/40/40,
  Veteran and Champion 40/60). Deliberately leaning high — *"lets favour the
  heroes a bit here"*.
- **Boss quests do not change expedition length or rewards.** In the real game
  a boss quest has its own map size and payout; here it is an ordinary
  expedition whose last fight happens to be a boss.

### What the knobs are worth, measured

`tools/distribution_sensitivity.py`, 48 paired scenarios each, against a policy
trained on none of them. **Only `boss` is significant** (-18.75pp SUCCESS,
p=0.0117). `kit` -8.3pp, `loadout` -6.3pp, `resolve` -6.3pp are all
indistinguishable from zero at that size, and `region` is *positive* (+12.5pp),
most likely because the Ruins are harder than average and 78 of 145 comps are
tagged there. See `CHANGELOG.md`.

**Training against the knobs has not yet been shown to help.** The 6M retrain
that turned three of them on beats its predecessor by +13.0pp on the curated
eval (p=0.0001) but only +1.9pp — not distinguishable — on its own randomised
training distribution. The curated gain is more plausibly the *engine* fixes
(encounter categories, gear gating, corpse duration) than the randomisation.

## Corpses: two gaps Fran's wiki reading exposed (2026-08-20)

Both from darkestdungeon.wiki.gg/wiki/Corpse, neither closed:

- **Corpses take no damage-over-time here.** The page says they are vulnerable
  to bleed and blight at 0% resist even when the living monster resisted them
  heavily. `_strike_corpse` applies no effects at all, and a corpse never takes
  a turn, so nothing would tick even if it did. Newly relevant: Fire's Edge
  ships burn effects flagged `.can_apply_on_corpse true`, so the mod expects
  this to work. **Not fixed because the tick point is unknown** — bleed and
  blight tick at the start of the affected unit's turn, and a corpse has none,
  so implementing it means inventing an end-of-round tick. That is a convention
  masquerading as game data unless it is checked first.
- **AoE crits off a corpse should relieve stress.** The page carves out AoE
  from the no-stress-relief rule. `_strike_corpse` skips crit stress relief
  unconditionally, justified by the corpse's own
  `.can_relieve_stress_from_crit False`. The data and the wiki disagree, and
  the data currently wins.

Also unverified: the page says random-target abilities (Blindfire) avoid
corpses unless nothing else is left.

## Burn is implemented, and its decay rate is a CONVENTION (2026-08-20)

Burn is now a real DoT — stack-based, decaying, ticking at the **end** of the
burning actor's turn, irresistible, with its own `.cure_burn`. All of that is
read off shipped data (`DECOMP.md` F6, `CHANGELOG.md`).

**One number in it is invented and must be treated as such: `BURN_BASE_DECAY`
= 0.5** (`engine/combatant.py`). No shipped file states a base decay, only a
buff (`hp_dot_burn_decay_percent`) that modifies one. It rests on two pieces of
evidence, neither of them the binary:

* Fran's play observation for the **magnitude** ("the numbers go halving each
  turn");
* the game's own localisation for the **sign**, which was the ambiguous half —
  `%+d%% Burn Decay` / *"a la disminución de Quemadura"* means decay is the
  fraction **lost**, so a positive amount makes burn expire sooner.

**The falsifier has not been run.** Capture one burn tick in a live raid with no
Firefly debuff on the target; `tools/dbwin_capture.py` logs the damage numbers,
so `7 → 3` confirms and `7 → 7` refutes. Until then this sits beside
`VIRTUE_SELF_HEAL_MAX_HP_FRACTION` as a declared convention, not a recovered
formula.

**CLOSED 2026-08-27 — both are implemented.** This entry read "deliberately
unimplemented, and not a gap in the fix" for Controlled Burn's rank *zones*
(`controlled_burn_amount`/`_duration`) and Backdraft's `.dmg_per_burn_stack`
scaling, on the grounds that the mod's own file says they "require engine
support". The engine support is what was missing; the data is fully specified.

Backdraft names **no `.effect` at all** — its parsed effect list is literally
`['']`, and its whole payload is `.requires_burning`, `.dmg_per_burn_stack 0.25`
and `.copy_burn_behind true`, none of which was parsed. So the skill really did
apply nothing, and the Runaway was being judged with two of her three signature
skills deleted. `CombatEngine.burn_zones` now holds the painted ranks and ticks
them at `_end_of_round` against the board as it stands then. See `CHANGELOG.md`
and `tests/engine/test_runaway_burn.py`.

## The bridge to the real game reads at turn granularity; what it does NOT carry (2026-08-11)

`bridge/` seeds a `CombatEngine` from a running game's save files and runs a checkpoint on it (`bridge/advisor.py`). Verified working against real captured combats — see `CHANGELOG.md` for the field table and the evidence.

**Implemented and real**: torchlight; party in formation order and embark size; round and current turn; the full merged initiative queue; enemy line in rank order with corpses holding their rank; per-enemy HP/stun; per-hero HP/stress/affliction/virtue/Death's-Door history; equipped combat skills; trinkets and quirks; resolve level from `resolveXp` against the game's own thresholds; party provisions; combats already fought.

**Declared gaps in the seeded state** — a recommendation is right about position, HP, stress and the enemy line, and can be wrong on a turn that hinges on one of these:

- **Two buff `stat_type` values (1 and 4) are unmapped.** Everything else in `actor.buff_group` transfers — buffs, debuffs, DoTs, guard and riposte (see `bridge/seed.py`'s `SAVE_STAT_TYPE` and the CHANGELOG entry for how the enum was recovered). These two were not identified: 1 demonstrably deals neither HP nor stress damage, and 4's duration never ticked inside a capture. They are dropped and counted into `engine.seed_unmapped_buffs`, which the advisor prints — 49 entries across the captures, against 354 buffs / 42 DoTs / 17 guards / 135 ripostes applied.
- **Bleed vs blight comes from `stat_type` (5 bleed, 4 blight), never from `damageSourceType`.** That field is the source side — 5 monster-applied, 6 hero-applied — proven by a run whose only DoT source was `opened_vein`: the same kind of bleed reads `damageSourceType` 6 on an enemy and 5 on a hero. `stat_type 5 = bleed` is confirmed twice; **`stat_type 4 = blight` is by elimination** (it appears only in encounters holding a `dotPoison` monster) and no capture yet shows a blight landing from a known single source.
- **Monster AI intent and skill cooldown history do not transfer** — the engine re-derives them, and the real fight's history is not persisted.
- **Total combats in the dungeon is read from `persist.map.json`** — tiles with an unspent `mash_index` are the fights left, so `battles_completed + remaining` is the length. It is a **floor**: a camping ambush is a real fight that never had a tile (observed once, +1 on a long run). `--combats-total` still overrides, and the fixed default only applies when no map file is readable.
- **Whose turn it is comes from `actor.performing_turn`, never from `currentturn`** — resolved by live play, see below.
- **Layer 3 (input injection) does not exist.** Deliberate: automating clicks fixes nothing if the recommendation is wrong, so it waits on the acceptance test.

**Not a gap, and left alone on purpose**: the engine's initiative rolls `SPD + randint(1,8)` where the real game lands in `[spd, spd+7]`. A uniform `+1` to every combatant cannot change any ordering, so it is behaviourally inert; changing it would shift every existing seeded trace for nothing.

### The acceptance test ran, and found the one thing it was built to find (2026-08-11)

Fran played a real Weald run with the **Riposte Style (Hag)** comp — Man-at-Arms / Highwayman / Highwayman / Vestal, the project's best-evaluated comp and the one whose damage bands were already differentially verified — while `bridge/advisor.py` ran against the live save.

**The recommendations were legal and the identities were right. The timing was one turn late.** Reported as "when it was the Man-at-Arms's turn it was still showing the Vestal's".

Root cause: `battle.currentturn` is written to disk **after** the turn it names has resolved. Keying on it therefore always advises for a turn already played. Confirmed offline against the capture: the save labelled `t1` already recorded the Vestal's `performing_turn` at 1.

Fixed by deriving the next actor from `actor.performing_turn` — turns TAKEN this battle, present on heroes and monsters alike — as the first queue entry whose counter is still below the current round, skipping the stunned. That names the next hero in the very write that records the previous one finishing, a full save-write earlier. `LiveState.current_actor`, `bridge/seed.py::_sync_turn_pointer` and the advisor's poll key all use it; pinned by three tests in `tests/bridge/test_live_bridge.py` against `archive/traces/raidsnaps2/`.

**A dataset caveat this exposed**: the *first* capture (`archive/traces/raidsnaps/`) has no contemporaneous roster — `--capture` did not snapshot one until this session — so its hero turn counters are from a later moment and it cannot answer "whose turn was it". Turn-identity tests run against `raidsnaps2/` only.

Two things the same run also confirmed, both previously untested: **duplicate classes stay distinct** (the two Highwaymen were tracked separately, at different ranks and stress), and **live formation changes are tracked** (Duelist's Advance reordered the party mid-fight and the ranks followed).

A **fourth run** reported the advisor working well, with one behavioural fault: several heals on targets already at full HP. Two independent causes, only one of them the policy's:

- **The bridge ignored the real `weapon_rank`/`armour_rank`**, deriving gear from resolve level instead. That lowered max HP, and `_apply_carry_over` clamps HP to it, so **a damaged hero read as full** — two of the four reported heals were on a Highwayman at 28/33 and 20/28 and were correct all along. Also fixed: `trinkets.items` is keyed by slot with the id inside, so every trinket was equipping as a no-op.
- **The rest were a genuine dominated action.** A pure heal that cannot restore a point of HP is now withheld (`_is_wasted_heal`). Measured at fixed policy over 12 expeditions: pure wasted heals → 0, deaths 7 → 7, steps 1306 → 1285. The current checkpoint was trained *without* this narrowing, so it only avoids the move at inference; learning from it needs a retrain.

**Still open on over-healing**: heals that carry another effect (Plague Doctor's `Cure`/`Cureself`, Occultist's self-bleed) stay legal at full HP by design, and the policy still spends ~21% of skill turns healing with a median target at 0.72 HP fraction. Whether that rate is too high is untested.

A **third run** found the actual cause of "it doesn't move on to whoever's turn it is", and it was not the turn order at all: **a corpse broke seeding outright**. `corpse_A` is not a loadable monster, so `build_engine_from_live` raised for the whole board and the advisor stopped advising from the first kill onward. Resolved through `previous_monster_class` (the format's name hash). Swept clean afterwards: 37 recommendation blocks across two full raids, zero failures. Separately, the save is written only when an action *resolves*, so chained enemy turns produce up to ~17 s of silence — the advisor now advises for `next_hero()` and states how many enemy turns come first.

A **second run** found the same rule still wrong at the end of a round — "the text does not update properly after two monster attacks in a row". Two causes, both fixed: a round ending on a **stunned** straggler emptied the pending list and fell back to the stale `currentturn`, and a combatant stunned through a whole round **trails its counter permanently**, so a straggler near the front of the queue would be reported as next forever. Resolved by scanning forward from the last combatant that acted (the acted set is a prefix of the queue) and returning **None** when only stunned combatants remain, so the advisor holds instead of re-announcing. See `CHANGELOG.md`.

## Overstress personalities are real data now — and the fix is correct but did NOT move the number (2026-08-09)

Three fidelity fixes, all evidence-backed, all kept. **The headline hypothesis behind them was wrong about magnitude, and the falsification condition written into the plan beforehand was met.**

### What was fixed

**1. `shared/trait/trait_library.json` had never been opened.** `_maybe_act_out` asserted in its own docstring that the per-personality act-out tables "live in the game binary, not in any data file", and `CLAUDE.md`/`ASSUMPTIONS.md` listed personality types as out of scope. The file ships 14 traits with complete `buff_ids` and `combat_start_turn_act_outs` weights. Four divergences, all now closed — see `CHANGELOG.md` for the tables. The largest: the engine charged **every** act-out as a party-stress bark at a flat 33%, where the real bark share is ~10% (Fearful 4/38), i.e. roughly **3.3x the real affliction stress**.

**2. `combat_stat_add` / `defense_rating` was silently dropped.** `_apply_buff_to_stats` matched only `"defense"`/`"dodge"`. 119 buffs in the merged store use `defense_rating` — the game's own name for dodge in a buff record — so all of them did nothing, from trinkets, quirks, skills and traits alike. **10 of the 48 hero slots across the 12 eval comps carry one**, permanently equipped and permanently inert. Same bug class as the historic `dmg_min`/`dmg_max` and `.dotStress` key mismatches.

**3. Affliction was cleared at every combat boundary**, documented as "matches the real game: affliction is a per-battle mental state". The game's own data says otherwise: `zealous_vigil` and `turn_back_time` each grant an extra 15 stress heal gated on `requirements: ["afflicted"]`, and both are **camping** skills. Camping happens between combats, so an afflicted hero at a campfire can only exist if the affliction outlived the fight. Clearing it also handed a stressed hero a fresh 25% virtue roll in every room. Now carried via `CarryOverHeroState.affliction_id`/`virtue_id`, restored by id rather than re-rolled.

### The measurement, at fixed policy

Run before retraining precisely so the engine change could be read without retrain noise — the technique that put the frozen-formation fix beyond doubt (228.4 → 35.4 steps/combat on the *old* checkpoint). Same `camping_3M` checkpoint, same `--comps 12 --comp-seed 1 --episodes 50`:

| band | SUCCESS base → +traits → +persist | mean stress base → +traits → +persist |
|---|---|---|
| short | 76.5 → 77.8 → 77.8 | 41.7 → 38.4 → 42.1 |
| medium | 73.7 → 73.2 → 72.5 | 52.4 → 53.5 → 51.8 |
| long | 44.5 → 45.3 → 44.8 | 101.1 → 99.3 → **103.6** |
| **mean** | **64.9 → 65.4 → 65.1** | **65.1 → 63.7 → 65.9** |

**Flat.** Every movement is inside eval noise, and long-band stress ended *higher* than it started.

The plan committed in advance to: *"if long-band stress does not fall from 101.1, the 3.3x bark divergence was not load-bearing and the rest of the plan is in doubt."* It did not fall. **The divergence was not load-bearing.**

### Why — the number that explains it

The affliction subsystem barely runs. Measured on the trained policy over 30 expeditions:

- act-outs fire on **0.6% of env steps** (18 in 3100);
- only **4 of 30 episodes** produce a single afflicted or virtuous hero, averaging **0.33 resolved heroes per expedition**;
- **median peak stress across a whole expedition is 50**, and only 5/30 episodes ever cross 100.

Verified this is not a wiring bug: of the 9 heroes finishing an episode at ≥100 stress, **9 of 9** had a resolved personality. The mechanism works; it is simply rare. Correcting 3.3x of a thing that touches 0.6% of steps cannot move a headline.

**The mean-stress figures in the eval mislead on this point, and that is worth remembering.** "Long band mean stress 101" is the mean of *final* stress over long-band episodes, dominated by a few comps (Stun Control: Beast 155) — not evidence that most heroes spend the raid afflicted. The correct reading was available all along from the same eval and was not made.

### What this leaves

- **The fixes stay.** They are correct, data-backed and independently verified; this file and `CHANGELOG.md` exist to stop correct-but-unrewarding fixes being reverted later. Fidelity is the project's goal, not the eval number.
- **The dodge fix is the one with real reach** — 21% of eval hero slots, always on, every step — and it is the more likely source of any retrain movement. The trait work is not.
- **A retrain was still required** (the engine changed; no checkpoint is comparable), run at 3M with `--seed 0`, and **predicted small in advance** rather than after the fact. It came in at **64.4% vs camping_3M's 64.9%** — flat, as predicted, with `ep_rew_mean` 45.1 → 36.1 at the same `ep_len_mean` (96.9 → 96.4), i.e. the same tempo for less return, which is what a harder engine looks like.

### The retrain, and the one thing in it that is not noise

| band | SUCCESS | wipe | mean stress |
|---|---|---|---|
| short | 76.5 → **80.2** (+3.7) | 9.7 → 9.0 | 41.7 → **34.7** |
| medium | 73.7 → 70.7 (−3.0) | 14.2 → 14.0 | 52.4 → **43.9** |
| long | 44.5 → 42.5 (−2.0) | 39.3 → 40.8 | 101.1 → 97.4 |
| **mean** | **64.9 → 64.4 (−0.4)** | 21.1 → 21.3 | **65.1 → 58.6** |

The headline is flat and every band sits inside the ±4pp noise floor. **Mean stress fell 6.5 points across the board**, which is the one aggregate that moved further than noise and is consistent with the bark-rate correction.

**The per-comp long-band split is violent and bidirectional**, and it is the interesting result:

| comp | SUCCESS | mean stress |
|---|---|---|
| Riposte Style (Hag) | 82 → **52** | 42.7 → **97.8** |
| Holy Hymn: Riposte | 68 → **38** | 89.1 → 115.0 |
| Stun Control: Blight | 34 → **62** | 134.8 → **72.4** |
| Stun Control: Beast | 14 → **40** | 155.2 → **118.3** |
| Shadowed Purse Strings ALT | 4 → 18 | 153.2 → 142.4 |

The comps that were worst improved most, and **both riposte comps collapsed by exactly 30pp with their stress roughly doubling** — Riposte Style (Hag), the project's best comp for many sweeps, more than doubled its long-band stress.

**Untested hypothesis, recorded as such**: the `defense_rating` fix gives heroes dodge that was previously inert, and riposte comps *want* to be hit — every dodge is a riposte that never fires, so their damage engine slows, fights run longer, and stress accumulates. The stress doubling fits. This has NOT been verified (no riposte-trigger-rate measurement was taken before and after), and a −30pp move in a single 50-episode cell is not by itself outside what 36 cells can produce by chance. Do not treat it as established.

**Also lost, and worth stating**: `camping_3M`'s checkpoint was overwritten by this retrain rather than archived first. Its 64.9% remains in the eval logs, but the weights are gone. It was trained against an engine with per-battle afflictions and inert dodge, so it was already outside the comparable set — but the archiving step should have happened anyway. `traits_3M` is archived at `archive/models/dd_combat_ppo_full_expedition_traits_3M.zip`.
- **Newly surfaced gap**: the observation encodes live dodge/prot/speed/crit but **not damage**, so no damage buff or debuff is directly observable — including the traits' largest effects (Fearful −25% DMG, Abusive +20%, Powerful virtue +25%). Deliberately left out of this retrain to avoid a fourth confounded variable.
- **`train.py --seed` added** so future A/Bs share an initialization; the ±4pp unseeded noise floor is what made the camping retrain's medium-band result unclaimable.

## Camping implemented — and the retrain shows a clean dose-response to firewood (2026-08-09)

Camping was deliberately out of scope until the eval localised the entire remaining gap to the long band. Implemented auto-resolved (not a policy decision — see `CHANGELOG.md` for the full rationale and the two heuristic calibration bugs found before shipping).

**Result: 64.9% mean SUCCESS, the best the project has produced** (`rankfix_3M` 62.5% → `camping_3M` 64.9%), wipes 24.0% → 21.1%, mean stress 72.3 → 65.1.

The per-band result is the actual evidence, because **firewood is 0/1/2 by band** and therefore predicts a dose-response:

| band | firewood | SUCCESS | wipe | mean stress |
|---|---|---|---|---|
| short | **0** | 80.7 → 76.5 (**−4.2pp**) | 9.2 → 9.7 | 38.1 → 41.7 (**+3.6**) |
| medium | **1** | 69.7 → 73.7 (+4.0pp) | 17.3 → 14.2 | 61.2 → 52.4 (−8.8) |
| long | **2** | 37.2 → 44.5 (**+7.3pp**) | 45.5 → 39.3 | 117.5 → 101.1 (**−16.5**) |

Stress moves +3.6 / −8.8 / −16.5 across 0 / 1 / 2 campfires. That monotone response across three bands is what makes this a mechanism rather than noise; no single band would carry the claim on its own.

**A stated prediction was wrong, and it calibrates the rest.** I predicted short would not move, since it gets no campfire at all (verified: `_camp_schedule` returns empty at zero firewood, and an end-to-end episode makes 0 camps on short). It moved **−4.2pp**. Camping cannot be the cause, so that figure is a direct read of from-scratch retrain variance — consistent with the ±3-5pp recorded elsewhere in this file. Which means **medium's +4.0pp is only marginally above the noise floor**; long's +7.3pp and the stress gradient are the load-bearing results.

Biggest movers on long: Stun Control: Blood +38pp (wipe 46→26, stress 134.8→93.0), All Good Hellion +22, Ballad Spam +18 (recovering the −12 it lost in the previous retrain), and **Dark Ritual: Ballad off zero for the first time**, 0% → 14% with wipes 94% → 66% and stress 192.4 → 142.3. The one large regression is Shadowed Purse Strings ALT, 24% → 4% (wipe 56→72, stress 128→153).

**Honest framing**: camping makes the game easier, so part of this gain is not the policy playing better. The point was that 37.2% measured a game the player never plays. `max` also fell 98% → 94% and `stall` ticked 0.3% → 1.0%, the latter plausibly ambush combats lengthening expeditions.

## Monster formations never closed ranks — and it was the Abomination stall engine (2026-08-08)

Enemy `.position` was never written on death anywhere in the engine (only Move, push, pull, `move_self`, and the hero-only `_collapse_hero_position_gap`), so a monster formation was **frozen for the whole fight**: killing the rank-1 monster with no corpse left ranks 2/3/4 occupied and rank 1 permanently empty.

Real rule, user-confirmed: monsters advance into an unblocked empty rank, so the **only gaps are at the back**; a corpse is an occupant of the formation — which is exactly what makes it block — and slides forward with everything else.

Fixed in `_collapse_enemy_position_gaps`, called from all five formation-changing sites (slain by attack, slain by DoT, `is_kill_effect`, corpse rot, corpse cleared by skill). Tests: `tests/engine/test_enemy_ranks.py` (7). Suite 365 → 372.

**This is the mechanism behind the long-standing "Stun Control" underperformance** noted in the stun/DoT entry below. The Abomination cannot reach enemy rank 4 in *any* form, so a surviving back-rank monster was permanently untargetable by him, leaving Absolution (free, unlimited, `is_stall_invalidating False`) as his only legal action — 57% of every env step in "Stun Control: Beast", 228 env steps per combat, 70% stall-outs on the long band. Measured with the same checkpoint before/after: 228.4 → 35.4 steps per combat, truncation 30% → 0%. See `CHANGELOG.md` for the full evidence table.

**Refuted along the way** (recorded so it isn't re-proposed): the hypothesis that free actions — `is_continue_turn` Transform and provisions — were burning env steps without consuming turns. They are 3-17% of steps everywhere and were *lowest* in the comps that stalled.

**Still open after this fix**: the policy over-heals across the board — heals land on targets at **median 83% HP**, Crusader heals on 53% of its turns, Occultist 80%, Vestal 97%. `Dragon Blood` and `Stun Control: Blood` still spend 26%/12% of steps on Absolution. The rank fix removed the *forced* no-op; the discretionary one remains, and `W_ENEMY_STUNNED = 0.30` telescoping inside `Φ` (see the stun/DoT entry) is still untested.

**Requires a retrain**: every existing checkpoint was trained against a frozen formation.

**Retrain result (3M from scratch, `--comps 12 --comp-seed 1 --episodes 50`): the mechanism is confirmed and the score went up.**

| eval | mean | median | wipe | **stall** | stress |
|---|---|---|---|---|---|
| `mapgen_aoe_3M` | 55.8% | 61.0 | 25.6% | 7.0% | 78.4 |
| `lenobs_3M` | 59.7% | 69.0 | 23.8% | 6.2% | 73.6 |
| **`rankfix_3M`** | **62.5%** | 69.0 | 24.0% | **0.3%** | 72.3 |

**Stalling is gone** — 6.2% → 0.3%, and 12.7% → 0.8% on the long band. That is the fix doing exactly and only what it was predicted to do; nothing else in this retrain targeted tempo.

Gains land on the band the fix targeted: long **+6.3pp** (30.8 → 37.2), medium +4.2pp, short **−2.2pp**. The Stun Control family that produced the diagnosis all recovered, each with its stall driven to zero: Beast 2% → 16% (stall 70% → 0%, combats reached 6.1 → 11.0 of 15), Blight 18% → 32% (stall 30% → 0%), Blood 16% → 26%. Marked Prey: Snipe +26pp.

Training-side corroboration: `ep_rew_mean` flat (24.2 → 23.9) with `ep_len_mean` **125 → 99**, i.e. the same return in 21% fewer steps.

**Not a clean sweep.** Ballad Spam −12pp on long, Dark Ritual: Ballad 6% → **0%**, Holy Hymn: Riposte −4pp, and long-band mean stress rose 112.0 → 117.5. The stress rise is coherent rather than alarming: parties now reach 12.1 of 15 combats instead of 11.0, so they accumulate more stress instead of being cut off early. Dark Ritual: Ballad's stall went 14% → 0% while its success went to zero — it no longer dithers, it just loses. Same "more fidelity, harder and more honest" pattern as the Riposte-ACC and stun/DoT fixes.

**The 61.8% from `wikifix_3M` still is not comparable** to any of these — it was measured on the pre-`map_generator` bands (8-11/10-14/14-18). 62.5% is the best number the project has produced, but the honest claim is +2.8pp over its own directly comparable predecessor, not +0.7 over `wikifix`.

## Wiki audit: five real mechanics wrong, including the crit damage formula

User asked for a systematic check against `darkestdungeon.wiki.gg`'s gameplay pages (Combat Mechanics, Status Effects, Damage over Time, Critical Hit, Death's Door). Five confirmed discrepancies, all fixed.

**1. Critical damage was computed from the ROLL, not the maximum.** Wiki, corroborated by a second independent source: *"A critical hit's damage is equal to 150% of the MAXIMUM damage of a skill, both for heroes and enemies... Unlike normal hits, whose base damage varies within a fixed range, the base damage of a critical hit is always equal to 150% of the maximum damage of the skill. Additional modifiers are then applied as usual."* We drew a uniform roll from `dmg_min..dmg_max` and multiplied that by 1.5, which averages 1.5x the *midpoint* — on a typical 6-14 range that is 15 instead of 21, about **30% too little crit damage** on every crit in the game, for both sides. `resolve_attack` now replaces the roll with `dmg_max` when the hit crits (the roll is still drawn so the RNG stream stays aligned with non-crit resolution).

**2. Crits did not extend DoT or boost status chance.** Wiki: *"All status effects applied on a critical hit have a 20% higher chance of applying, and Bleed and Blight will last 50% longer, rounded up, or 2 additional turns for most skills."* Neither was modelled — a crit applied exactly the same status, for the same duration, as a normal hit. `is_crit` is now threaded from `resolve_attack` through `_apply_skill_effects` → `_resolve_effect` → `_apply_effect_to_target`; `CRIT_STATUS_CHANCE_BONUS = 0.20` is added to the effect's chance and `_crit_dot_rounds` extends bleed/blight by 50% rounded up (the standard 3-round DoT becomes 5).

**3. DoT kills left a corpse.** Wiki: *"A unit that dies from a damage over time effect will not leave a Corpse."* We set `is_corpse = True` on every enemy death including DoT ticks. Tactically load-bearing rather than cosmetic — a corpse keeps blocking its rank until cleared, so finishing something with bleed/blight is supposed to open the lane that a normal kill does not.

**4. Death Blow resist had no cap.** Wiki: *"The maximum resistance cap is 87%, even if modifications would theoretically reach higher values."* Trinkets and quirks could push `death_blow_resist` arbitrarily high, making a stacked hero effectively unkillable at Death's Door. `DEATHBLOW_RESIST_CAP = 0.87` now clamps it.

**5. Push/pull ignored Move Resist entirely** (found while adding resist-roll logging, not from the wiki). `move_resist` was parsed, buffed and debuffed correctly, but its only consumer anywhere in the engine was monster-brain target weighting — nothing ever rolled against it. Every knockback and pull landed regardless: Man-at-Arms' Rampart, Bounty Hunter's Come Hither, Shieldbreaker's Puncture, and every monster shuffle were unresistable. Now rolled, with the result logged.

**Correction to an earlier audit answer in this file**: I previously reported "Push/pull gated by move_resist? ✓ 7 sites" — that check counted string occurrences rather than verifying they were *consumers*, and the answer was wrong. Occurrence counts are not evidence of use.

**Also confirmed correct against the wiki** (no change needed): DoT ticks at the start of the affected unit's turn; DoT can force a Death's Door check; per-turn DoT instances cannot themselves crit; the Death's Door debuff set (-10 ACC, -25% DMG, -5 SPD, +33% stress) and Mortality recovery set (-2 ACC, -5% DMG, -1 SPD, +10% stress); heart attack at 200 stress with harsher recovery penalties; crit stress numbers (dealer -3 and 25% party chance, victim +10 and 50% party chance for +5).

**Superseded by a later fix, kept for history**: the critical-heals gap noted
above was subsequently closed — `_apply_heal` gained its own crit roll
(`HEAL_CRIT_CHANCE_SINGLE_TARGET=0.12`/`HEAL_CRIT_CHANCE_PER_TARGET=0.05`,
double the applied amount, `-4` stress), independent of the skill's attack
crit stat exactly as anticipated here, wired into every hero-cast heal path
(skill heals, self-heals, named-effect heals, heal-dot ticks). See
CHANGELOG.md's "round 3" wiki-driven fidelity fixes entry.

Suite: 331 → 335 passing.

## Stun granted immunity to DoT — stun and bleed/blight anti-synergized

User asked a set of verification questions about whether the game is modelled properly ("does DoT stack with proper pacing", "are turns evaluated per rotation or per opportunity", "check AoE stuns like Plague Doctor's"). Auditing those found one real bug and confirmed the rest.

**The bug**: `_advance_to_next_hero_turn`'s stun branch ended in `continue`, jumping past the DoT ticks that every other path runs:

```python
if combatant.is_stunned:
    combatant.is_stunned = False
    self._apply_stun_recovery(combatant)
    self.current_turn_index += 1
    continue          # skipped _tick_dots / _tick_stress_dots / _tick_heal_dots
```

So a stunned combatant took **no bleed, blight or horror damage** — and received no heal-over-time — for that round. Stun prevents *acting*, not bleeding: in the real game a stunned target still takes its DoT on its turn, which is exactly why stun+DoT is a standard pairing (Plague Doctor stunning ranks 3-4 with Blinding Gas while Blight ticks). As written, applying both to the same target made each *worse*.

Plausibly a real contributor to two long-standing symptoms: the trained policy used blight on only **2.5%** of skill uses and stun on **3.5%**, and the Abomination/Plague-Doctor "Stun Control" family — built on precisely this combo — underperformed in every sweep since the project began. Verified before and after: a 10/tick blight now deals 10 to a stunned target, identical to an unstunned one.

Also applies `_check_win_loss()` after the ticks, since a DoT can now kill a stunned combatant.

**Everything else in the audit checked out**, and is worth recording so it isn't re-litigated:
- **DoT stacking**: re-applying a DoT appends an independent instance rather than refreshing, so three consecutive applications of a 6-damage/3-round blight tick for **6, then 12, then 18** while the older stacks run down. Matches the user's stated model exactly.
- **Turn system**: one unified initiative queue (heroes and enemies together, SPD + d8), and a *round* is every combatant acting once — confirmed by 2 hero actions advancing a 2-hero party from round 0 to 1. Env steps count hero decisions only (enemy turns auto-resolve), so `MAX_STEPS_PER_COMBAT = 40` is ~10 full rounds for a 4-hero party — generous against the 2-3 rounds a real fight takes, so the step cap is not what limits normal combats.
- **AoE stun**: `blinding_gas` is `is_aoe` with `target=[3,4]`, offered as ONE action, and stuns both back-rank enemies.
- Weapon/armour tier scaling, trinket application, HP/stress carry-over, initiative order, multi-action enemies, stun-recovery buff, move-resist gating on push/pull, corpse blocking, enemy skill cooldowns, torchlight stat effects, affliction/heart-attack thresholds, and Death's-Door recovery tiers all verified working.
- `armour: .spd` is not parsed, but **zero** armour records in the entire install have a non-zero value, so it is inert.
- Crusader's weapons showing `atk 0%` at every tier is faithful to the game data (weapon upgrades give damage/crit/speed; ACC comes from the skill), not a scaling bug.

New tests: `test_stunned_combatant_still_takes_dot_damage`, `test_dot_applications_stack_independently`, `test_aoe_stun_is_one_action_hitting_every_target_rank`. Suite 323 → 326.

**Retrain result: the behavioural prediction held, and the score legitimately went DOWN.**

Every control mechanic rose once stun and DoT stopped cancelling each other (share of hero skill uses, before → after): riposte 4.1% → **6.8%**, stun 3.5% → **5.7%**, blight 2.5% → **3.8%**, mark 1.9% → **2.8%**, guard 0.6% → **1.4%**, bleed flat at ~18%.

But success fell and stayed down across both checkpoints — 54.8% → 51.1% at 3M, and 58.4% → **49.5%** at 6M. Two independent runs both below the pre-fix baseline is outside the ±3-5pp from-scratch variance seen elsewhere, so this is real rather than noise.

**The correct interpretation is that the old numbers were inflated.** The bug skipped DoT ticks for *whichever* side was stunned, and empirically it was shielding the party more than the enemy: monsters stun heroes routinely and monster bleed on heroes is common, whereas the policy stunned on only 3.5% of its own skill uses. So heroes had been dodging bleed damage for free, and removing that made the game genuinely harder. Same category as the Riposte-ACC fix earlier in this file — more fidelity meaning a *harder* fight and a lower, more honest number.

Live model set to the `dotfix_3M` checkpoint (51.1% mean, wins 22/36 cells against `dotfix_6M`'s 49.5%). **The 58.4% figure is retired**: it was measured on an engine where stunned heroes were immune to bleed, and is not comparable to anything measured afterwards.

**Open question this leaves**: stun is priced at `W_ENEMY_STUNNED = 0.30` inside the potential function, which telescopes — the value is handed back when the stun expires, so it shapes learning but cannot make stunning genuinely preferable to damage. The DoT fix moved stun usage from 3.5% to 5.7% without closing the gap, so a *real* (non-potential) stun term remains the untested candidate, following the same "make the true benefit dense rather than telescoping" pattern that fixed tempo and stress.

## Stalling: two keys wired, two readings left open (2026-08-23)

Prompted by Fran's own stalling notes (darkestdungeon.wiki.gg/wiki/Stalling plus
a linked write-up). Most of what they describe was **already right**, from real
`rules.json` values: 2+ real actions per round resets the counter,
`stall_party_size_threshold 3` means a 1-2 hero party is never stalling,
Vulnerability Hex (`disruptive_curse`) is the one attack with
`is_stall_invalidating False`, Jester's Solo is the one non-attack with it
`True`, and the accelerating-monster list is read off `.accelerate_stall_penalty`
rather than transcribed. Post-stun resist (+50%, `stun_recovery_buffs` with
`consecutive_applies true`) was already modelled too, which is what makes
"stun all four, then eat four attacks" a real consequence here.

**Wired on 2026-08-23, and neither changed behaviour.** Said plainly rather than
counted as fixes:

- **`stall_extra_round_size_threshold` (2) / `_amount` (1)** — the enemy side of
  the test is SIZE, not heads: occupancy plus 1 for each monster of size >= 2.
  That is the only reading fitting all three wiki statements at once (1-2 small
  enemies stall, three small do not, any size-2 does not).
  **Inert in shipped content:** all 61 size-2-or-larger monsters also carry
  `disable_stall_penalty`, which short-circuits first.
  `tests/engine/test_reinforcements.py` pins that as a tripwire — if content ever ships
  a large monster without the flag, the rule starts mattering.
  *Falsifier:* a capture where a party stalls freely with a single size-2,
  non-`disable_stall_penalty` monster on the board.
- **`stall_round_accelerated_only_increment` (1) / `_consecutive_increment` (5)**
  — the counter climbs 1 then 5, crossing the ordinary summon threshold of 4 on
  the second accelerated round. Replaces a hardcoded
  `ACCELERATED_STALL_SUMMON_THRESHOLD = 2` whose own comment admitted it was a
  guess. It lands on the same round, so nothing observable moved; what changed
  is that shipped numbers drive it.

### The shipped flag alone gets three skills wrong (fixed 2026-09-20)

`.is_stall_invalidating` **defaults to TRUE** and is written out only where it
is false, so a non-damaging skill that carries no tag inherits "invalidating"
by omission. Three shipped skills do exactly that — Houndmaster's **Guard
Dog**, Leper's **Revenge**, Plague Doctor's **Emboldening Vapours** — and the
wiki's `Talk:Stalling` records that the game charges a stall for them anyway,
proposing the missing condition: **the skill must target an enemy.**

Checked against the shipped files, that single test reproduces every
documented row, including both "well-known exceptions" untouched:

| skill | flag | ally? | target | invalidates |
|---|---|---|---|---|
| ordinary attack | True (default) | no | enemies | **yes** |
| Jester `solo`, −100% dmg | True (default) | no | enemies | **yes** |
| MaA `bellow`, −100% dmg | **False** | no | enemies | no |
| Occultist `disruptive_curse`, −90% | **False** | no | enemies | no |
| HM `guard_dog` | True (default) | **yes** | allies | no |
| PD `emboldening_vapours` | True (default) | **yes** | allies | no |
| Leper `revenge` | True (default) | no | **empty (self)** | no |

`CombatEngine._invalidates_stall` is the gate, and it is two conditions
because self-targeting and ally-targeting are different in this loader:
`_is_ally_skill` reads the `@` in the raw `.target` notation, while a pure
self-buff like Revenge has an EMPTY target list.

**The bug ran in the dangerous direction.** Those three read as invalidating,
so a simulated party could dodge a stall penalty with moves the real game
does not accept — and two of the three are pure support, which is precisely
what a stalling party is casting. Pinned by
`tests/engine/test_reinforcements.py`.

**ONE reading left open (the second was closed 2026-09-20, below) —
implementing the wrong one is a regression:**

- **"Enemies that will die from Bleed/Blight next round will not call for
  reinforcements."** Two readings with *opposite* effects. (a) A doomed enemy is
  excluded from the living count — the party then looks like it has FEWER
  enemies, so it stalls sooner and the wave comes EARLIER, the opposite of what
  the sentence promises. (b) The summon is performed by an enemy and a doomed
  one does not perform it — which delays the wave. `_is_doomed` already exists,
  so either is cheap; neither is evidenced. *Falsifier:* a capture of a stalled
  fight whose last enemy is dying to a DoT, showing whether the wave arrives.
- **~~`accelerated` requires ALL living enemies to carry the flag, not ANY.~~
  CLOSED 2026-09-20 — ALL, and the wiki says so in as many words.** It was
  kept as ALL on the strength of the key's own name,
  `stall_round_accelerated_only_*` ("only accelerating enemies remain"),
  against the wiki's looser "some enemies accelerate" — which describes which
  monsters carry the flag, not the gate. The Stalling page's own Accelerated
  Penalties section resolves it: *"**If all remaining enemies belong to the
  list below**, then stalling penalties are incurred faster."* The reading
  the key name implied is the documented one, and no code changed.

  The same page also confirms the accelerated schedule already implemented:
  a bark after one stalled round with **no stress**, reinforcements on the
  second.
