# Contracts for `bridge/`

`bridge/` is the PLAYER: it reads the running game and says what to do.

**`docs/BRIDGE.md` is the full reference and is required reading before
changing anything here** -- the six per-layer decision scripts, the marking
rules, the overlay's constraints and the save-format traps. What follows is
the summary that used to live in the root `CLAUDE.md`, moved verbatim on
2026-09-07.


### The bridge (`bridge/`) — the actual player

**See `BRIDGE.md`.** `tools/` is diagnostics; `bridge/` is the player: it reads the RUNNING
game's saves and says what to do. Three layers — **read** (`live_state.py` decodes
`persist.raid.json`/`persist.roster.json` into a `LiveState` at turn granularity), **decide**
(`seed.py` builds a `CombatEngine` holding that exact position, `advisor.py` runs a checkpoint
on it, `run.py` puts all six dungeon layers behind one poll loop and marks each recommendation
against what was then played), and **act** — input injection, **started 2026-08-16**
(`act.py`, `window.py`, `layout.py`). `overlay.py` draws `run.py`'s output over the game and
decides nothing.

```bash
python bridge/run.py --model logs/full_expedition/best_model.zip --log run1.log --survival 12
python bridge/run.py --replay archive/traces/raidsnaps2    # validate the marking offline
python bridge/overlay.py --model logs/full_expedition/best_model.zip --log run1.log
python bridge/advisor.py --once --raid <snap> --roster <roster>   # replay a capture
python bridge/embark.py --model logs/full_expedition/best_model.zip   # which quest, with whom

# Layer 3. Nothing is sent unless a caller passes a live backend AND sets enabled=True.
python bridge/window.py --list                  # find the game window
python bridge/layout.py --resolve               # the board in this window's pixels
python bridge/act.py --replay archive/traces/raidsnaps2 --model <ckpt>   # dry run
python bridge/act.py --probe                    # game RUNNING: PostMessage vs SendInput
python bridge/inventory.py --want 2             # the raid pack, and what may be dropped

# Booting from the menus (2026-08-17). Creating a save slot IS the embark: the
# shipped template has `inraid: true, raiddungeon: "tutorial"`, so a new campaign
# starts inside the Old Road, whose layout is hand-authored and identical every
# run — which makes it the corridor test rig. See BRIDGE.md.
python bridge/screen.py --once                  # which screen is up (GDI grab + hash match)
python bridge/menus.py --slots                  # which save slot is free, and is it reachable
python bridge/run.py --boot --act               # front end -> new campaign -> the tutorial

# Leaving a room (2026-08-18). A room is exited by CLICKING THE DESTINATION ROOM
# on the map panel -- the game's own `str_help_raid_room_0` says so, and it walks
# the connecting hallway for you, so `RoutePlan.next_step` (that hallway) is NOT a
# click target. The map zooms and pans, so the transform is measured per frame
# against `Tile.mappos` and never stored. See BRIDGE.md.
python bridge/mappanel.py <frame.png> <persist.map.json> --area rooB
```

**THE TOWN'S GEOMETRY IS SHIPPED DATA, not a measurement job (2026-08-31).**
Darkest Dungeon ships its whole UI layout as `.darkest` **block-layout** files
in a **1920x1080 design space**, and `DarkestParser.parse_blocks` already reads
them — `building_navigation.layout.darkest` indexes every building's entry
button by name (`stage_coach 0 … statue 9`).

**BUT THAT STRIP IS NOT DRAWN ON THE HAMLET, and a live run is what proved
it** (2026-08-31). DD puts the strip INSIDE a building panel — it is how you
hop from one building to the next once one is open. From the town screen you
click the **building in the scene**. The first live town visit clicked the
derived strip button, nothing opened, and the visit then sent six more clicks
into an open Hamlet; every one was correctly reported UNCONFIRMED by the save
check, which is the only reason it was harmless.

**The scene x IS derivable and the y is NOT.** `town.layout.darkest` gives each
building a `pos`, and hue-detecting the Hamlet's red banners off a live frame
matches `pos.x / 1920` to within two pixels — stage_coach 313 measured against
315 shipped, statue 939 against 940. The y does not fit any linear model: the
scene is a perspective projection with a per-building z, and `alert_offset` is
in different units again. So `BUILDING_SCENE_POINTS` is MEASURED, holds only
the stage coach today, and `plan_open_building` **refuses** for anything not in
it rather than falling back to the derivable-but-wrong strip.

**THE COACH WORKS END TO END, verified live 2026-09-01.** It bought
`numrecruits.a` — the HUD went `deed 12 crest 24` to `deed 9 crest 20`, exactly
the shipped `3 deed + 4 crest`, bust and portrait untouched — and it hired
Boteler off the coach with a drag, roster 2/12 to 3/12. **The upgrade trees are
not drawn until the building CREST is clicked** (`plan_open_upgrades`), which
is why they were absent from the first banked panel; with that click all 13
nodes land within 1 px of their box centres.

**In town the SAVE CAN LAG A WHOLE SESSION, and the HUD is the truer channel.**
The live campaign had written nothing to disk, so the save check reported
UNCONFIRMED on a purchase that had plainly worked. Save-is-truth still holds in
a raid, where the save is written per turn; town acknowledgement should read
the drawn wallet and the drawn roster count too. Related footgun found the same
hour: `read_town_state` on a profile directory with missing files silently
falls back to a different profile.

**...AND IT DOES NOT HOLD FOR WHOSE TURN IT IS (2026-09-05).** The one thing a
raid save gets wrong is the turn order: measured over the capture corpus, the
rule that names the acting hero is right on **108 of 110** decisions, and the
two obvious alternatives (`currentturn + 1`, fewest-turns-first) miss the same
two. So there is no better ruler and the 2% is recovered from --
`LiveState.next_hero(skip)` plus a bounded retry in `run.py`. It matters
because the failure is silent and total: the slot number selects the acting
hero's skill while the click aims at the advised hero's target, nothing lands,
and a live Cove raid stopped there. Two shapes, and the second has no failure
in it at all -- a send is "confirmed" by any save write, including an enemy
acting -- so `STUCK_SECONDS` is a heartbeat for the case where nothing errors.
See `BRIDGE.md`.

**The measured scene click WORKS, and the panel frame settled the rest.** It
opened the coach; the guard banked the frame; that frame is now
`captures/screens/stage_coach_panel.png` (separation 0.700). Overlaid on it:
all ten navigation markers land on their icons — **the strip derivation was
right all along**, it just lives inside the panel — `building_close` lands on
the X a third time, the recruit slots were off by a consistent (+26,+26)
because `store_item_pos` anchors the row and a drag needs the portrait
(`RECRUIT_PORTRAIT_OFFSET`, design +39,+39), and **no upgrade tree is drawn on
that panel at all** until the building CREST is clicked, which is what
`plan_open_upgrades` exists for. **`UPGRADES_MEASURED` and
`EXCHANGE_MEASURED` are both True now** and nothing in a town visit is
gated; the two tests that pinned the closed side were retired on 2026-09-03
once `tools/find_silent_tests.py` showed they could only skip.
`bridge/townlayout.py` derives it; `layout.design_pt` and `ResolvedLayout.at`
are the only seam, so a derived point gets the same letterbox correction as a
hand-clicked rank.

**The cross-checks are the evidence and they are tests** (`test_townlayout.py`):
derived `MODAL_OK` lands 1.5 px in x and 4.8 in y from the edge-detected one;
the derived column pitch is **80.0 design px**, which the raid pack was
independently measured at and which two separate files state; and the
three-term composition for the activity-log X reproduces the hue-measured
`TOWN_CLOSE_PANEL` exactly. **Anchoring is per widget and must be established,
never assumed** — `confirm_dialog.base_pos` is centre-anchored in x (960 is
half of 1920), a close glyph is top-left anchored, and that is where
`CLOSE_GLYPH_CENTRE` comes from.

```bash
python bridge/townlayout.py                # every derived point + the cross-checks
python bridge/townplan.py --profile <dir>  # decide a town visit, print it
python bridge/town.py --coach              # plan it against the live game
python bridge/town.py --coach --act        # and do it
python -m data.heirlooms                   # the rates, the trees, Fran's case
```

**THE EMBARK IS DECIDED OFF THE SAVE, with no policy in it (2026-09-02).**
Fran asked for *"rather than just choose the best option I would like just an
option that works"*, so `bridge/embarkplan.py` scores comps by
**affordability** and picks among the affordable with `rng.choice`.
`bridge/embark.py` is the other one and still ranks by running the model; both
now filter quests on `simulable` alone — **`is_generated` refused the only
quest on the live map**, `plot_tutorial_crypts`, which is an ordinary Ruins
expedition.

```bash
python bridge/embarkplan.py --seed 0 --all   # the plan, and every fieldable comp
python bridge/town.py --embark-setup         # walk the chain, send nothing
python bridge/town.py --embark-setup --act   # and do it
python -m data.provisions --zone crypts --length 1
python -m data.hero_skills --profile <dir>
```

* **Provisions are reserved FIRST, at PLAYBOOK 1.5's recommended end** —
  Ruins/short is 3550 gold, and the skill budget is what is left. `GUIDE` is
  the one convention in `data/provisions.py`; prices, stock, the shop's draw
  order and `minimum_food` (0/8/8/12/12/12) are all parsed.
* **...and the party is HANDED some of them, so the plan must not buy those.**
  `raid_starting_hero_class_item_lists` grants eight classes a provision each
  (houndmaster 2 dog treats, plague doctor 1 antivenom, grave robber 1 shovel,
  arbalest 1 bandage, crusader 1 holy water, leper 1 medicinal herbs, jester 1
  medicinal herbs, antiquarian 1 skeleton key) — 150-500 gold a week on a real
  party. `provisions.granted` is **per hero, not per party** (two Houndmasters
  bring four treats), and `basket(..., party=())` defaults to no party and is
  byte-identical for every existing caller. The grant depends on the COMP and
  the comp is chosen out of what the reservation leaves, so `plan_embark`
  reserves un-discounted and applies the grant in `_basket_for` — the safe
  direction, since the skill budget can then only come out larger.
* **A hero's unlocked skills are in the save.** `persist.upgrades.json`'s
  `instance_number` IS the hero guid, and the leftover hashes are the camp
  trees — the count is the hero's own `camping_skills`, not a fixed three
  (`flagellant#60` carries two). `upgrades/heroes/` holds only the fifteen
  vanilla classes; the other five are under `dlc/**`, excluding `modes/`.
* **The estate map's quest tokens are SHIPPED geometry**, derived within 0.5
  design px of three measured tokens out of two layout files. A SECOND quest
  in one region refuses (`QUEST_ROW_MEASURED`).
* **Only `curated_comps/`** — `CompLoadoutEntry.source` is what says which
  folder a comp came from, and `composed_comps/` is training coverage nobody
  has played.
* **Three gates keep it from flying**, each one live visit away:
  `BUILDING_SCENE_POINTS["guild"]`, `GUILD_MEASURED`, and which element of a
  guild skill row is the selection toggle. `ROSTER_SCROLL_MEASURED` was a
  fourth and is **True** now; the reason it was False is worth keeping,
  because it is the shape of the remaining three — a mis-scrolled list
  re-indexes every row, so the drag would still land, on somebody else.

**"Crests for Scrolls" is `crest 3 -> deed 1`** — there is no scroll currency,
the deed icon is a scroll. The shipped starting wallet plus the shipped tree
costs leave the top path **exactly one deed short**, which is what makes Fran's
instruction a specification. **The trade is SOLVED and the cost function is
DERIVED**: counting raw units picks `2 portrait -> 3 deed` over `3 crest -> 1
deed` because 2 < 3, and that is twice as expensive — the three `X -> crest`
rows price bust 1.5 / portrait 3.0 / deed 1.5 / crest 1.0. Corrected, the
solver reproduces Fran's own instruction, which is the check that it is right.

**`persist.upgrades.json` is not shaped like its field names.**
`requirement_code` and `is_purchased` decode to **nothing** on all 228 entries
of a real save, so the obvious reading returns an empty set from a save full of
purchases. `tree_id` is `dd_name_hash(tree.id)` read UNSIGNED; an entry's
**presence** is the purchase and the **count** is the rank (0 of 85 trees
exceed their own rank count); `instance_number` is which hero. No building tree
appears in either of Fran's saves because none was ever bought — the Week 22
wallet is within two busts of the shipped starting heirlooms.

**Recruiting is a SHAPE question and the obvious metric has NO GRADIENT.**
"How many curated comps can the roster field afterwards" scores **zero for
every candidate** from the two-hero tutorial party — no four-hero comp is
fieldable from three heroes, so all 179 shapes score 0 and the choice collapses
onto the tie-break. `reachable(shapes, have, budget)` is what steers: at a
two-hire budget the same roster has 9 shapes within reach and the top classes
are exactly the ones a Crusader/Highwayman core wants. **And hires interact, so
the SET is searched** — `C(6,2)` is fifteen, and greedy can leave a pair that
completes nothing while another pair completes a comp outright.

**NOTHING INSIDE A BUILDING PANEL CHANGES THE SCREEN.** Buying, trading and
hiring all leave the recogniser reading the same panel, so a click that did
nothing and a click that worked are pixel-identical. Every action is
acknowledged against the **save** — wallet for a trade or a purchase, roster
for a hire — and `Did(sent=, confirmed=)` reports the two separately.
`tools/overnight.py` counts them separately for the same reason.

**That design paid for itself on its first live run**, which is the reason to
keep it: six clicks went into an open Hamlet and all six came back
`UNCONFIRMED (the wallet did not move)` / `(the roster did not grow)`. Nothing
was silently claimed as done, and the loop carried on and embarked — a town
visit is never fatal to the night.

**`_panel_is_open` gates everything after the open, and UNKNOWN means NO.**
The first version read `recognise(img) not in TOWN_SCREENS`, so an
unrecognised frame — a mid-transition, or an interior with no banked reference
— read as "it opened". A one-sided test has to refuse when it cannot see. It
therefore refuses until `BUILDING_SCREENS` is filled by banking an interior,
and on refusal it banks the frame and still clicks the close, because "did not
open" and "opened but unrecognised" are indistinguishable and leaving a panel
over the Hamlet swallows the embark walk.

**Layer 3 is small because the game ships hotkeys.** `localization/miscellaneous.string_table.xml`
documents `1`/`2`/`3`/`4`/`5` = quick-select a skill, `A`/`D` = move, `T` = torch,
`SPACE` = take all loot. The `1..5` numbering *is* `overlay_slots.py`'s and *is* what
`run.py::advised_button` computes, so **a combat turn is one keystroke plus one click** on
an already-calibrated rank, and movement/torch need no coordinates at all. Coordinates are
fractions of the game's **client** rect (not the window rect — an 8px titlebar offset reads
as "it clicked the wrong hero"), so a windowed game works and can be dragged mid-fight.
Dry run over the capture corpus: **145 plans, 118 actionable, 27 refused — every refusal a
provisional look-ahead — zero exceptions, zero degenerate clicks.**

**A LAYER THAT CANNOT ACT MUST NOT BLOCK ONE THAT CAN, and this bit twice.**
`plan_room` checks torch, then curio, then movement, and each branch used to
`return` its refusal. The curio was fixed; the TORCH was not, and on
2026-09-01 a relight the save would not acknowledge pinned the party in `rooH`
for 180 s and threw away a 40-action raid — every poll re-planned the same
relight and the route step was never reached. `_LayerDone(update, *fields)`
with `_curio_done` / `_torch_done` is the repair, and `_AdviceWithout.empty` is
recomputed over what REMAINS because reading it off the underlying advice says
"still busy" forever. If a third layer gains an actuator, it gets a
`_..._done` too.

**The one HUD geometry that is measured rather than clicked** is the raid pack: 2 rows of
8, `layout.py`'s `INVENTORY_X/Y`, plus `MODAL_OK` and `LOOT_CLOSE`. Edge-detected off
`captures/*.png` and verified by redrawing the grid over the capture, so it lives in code
and not in `overlay_layout.json`. Discarding is **SHIFT-CLICK**, per the game's own
`str_discard_item_instructions`, and it is the only input the bridge sends that destroys
something — hence `bridge/inventory.py`'s positive `type == "supply"` gate, and hence a
discard being verified by re-reading the slot rather than by the save's mtime. Details and
the corpus numbers are in `BRIDGE.md`.


### The route is decided on LOOT now (`bridge/lootplan.py`, 2026-09-06)

Fran: *"minimizing low torch battles + maximizing loot at the same time"*.
`route.py` said in its own docstring that it could not do this -- *"how a
player trades moves and fights against loot is a judgement no shipped file
contains"* -- and three shipped files nobody had read turn out to contain it.

**"+12 treasure draws" is not twelve draws.** It is a chance of an extra draw
from a named table, and at the bottom bracket the *chest* chance climbs 75% ->
95% while the *battle* chance does not move at all: it draws **twice**. Priced
through the real tables at Weald/Apprentice, a fight's bonus is **+454g** at
torch 0 against 0g radiant, and a chest's is **+165g + 0.39 crest**.

**The cost side is shipped too**, and it is what makes the rule non-arbitrary:
`tile_light_loss` burns 1 per tile above 75 and **6 below** (so topping up on
arrival, not at the fight, took a real plan from four torches to two), and
`corridor_return_content` **repopulates a re-entered corridor** at 5% battle /
7.5% hunger / 5% trap, each +5% at torch 0. A dark walk home MAKES fights.

Fran's rule, and it invents no weight: **hard torch floor (76) on every
planned fight the pack can afford, then maximise expected loot.** Plus the one
consequence that does the work --

> **Defer what is on the way home. Take the rest on the way out.**

because a completed quest is handed in at the entrance, so those corridors are
re-entered anyway and a deferral there is free.

```bash
python bridge/lootplan.py <persist.map.json> --have torch=4,skeleton_key=1
python -m data.loot_loader --darkness-table --walk-costs
```

**The acceptance test is that the optimiser reproduces the route Fran
described for the Old Road without being told it** -- fight lit, defer the
tent, snuff before the killing blow, skip the trapped chest, take the tent at
torch 0, finish at the entrance (`tests/bridge/test_lootplan.py`). And
`bandits_trapped_chest` is the inventory case in one curio: `Blight 1` bare,
loot table `T` with a skeleton key, so the same map flips SKIP -> TAKE on one
item. The pack is therefore **assigned across the whole route** by DP, not
chosen per curio -- `curios.py::advise` answers one curio against the whole
pack and would spend the last key twice.

**`DEFER` is not `SKIP`.** Skip means never; defer means not at this light,
and it must not arm `curio_action` (the marker would score a deliberate hold
as ignored advice) nor leave `RoomAdvice.empty` true (the actuator would print
"no room advice to act on" and stand still).

**The Old Road is an offline fixture now.**
`scripts/starting_save/persist.map.json` is the tutorial map shipped as plain
JSON, and `read_dungeon_map` reads it, along with the `maps/*.dm` floors. It
spells a curio `cur` and writes **0xCCCCCCCC** for "nothing", which is MSVC's
uninitialised fill rather than a hash -- without `EMPTY_HASHES` every tile in
it reports a trap called `#3435973836`.

`BRIDGE.md` holds the rest: the six per-layer decision scripts, the marking rules that keep
the agreement rate honest, the overlay's hard constraints, and the save-format traps
(`persist.map.json`'s two id conventions, `dd_name_hash`, corpses, `buff_group` stat types,
torn saves). **Read it before changing anything under `bridge/`** — most of those rules exist
because the first version got it wrong.


### The demonstration corpus: `bridge/journal.py` (2026-09-03)

Fran: *"Improve the advise model so it gathers much more data and ill just run
it and play through several expeditions, make sure it logs everything, dungeon,
hamlet, team management, money management etc."*

```bash
bat\combat_advise.bat                    # play; it watches and records
python tools/journal_report.py           # what the corpus caught, mid-session
python tools/journal_report.py --differs # every disagreement, to annotate
```

`--log FILE` now also writes `FILE.jsonl`: **one JSON object per event**, beside
the readable log rather than instead of it. `--journal none` opts out. The prose
log is for a person; every analysis built on it so far has had to regex it back
into structure and **two of those regexes were wrong in one session** — a prose
log is not a dataset.

**Three things were missing and all three are load-bearing.**

- **THE HAMLET WAS NEVER RECORDED AT ALL.** `_watch` keys on
  `persist.raid.json` and `continue`s whenever the save says the party is not
  in a raid, so every town visit ever made went past it. The town poll now runs
  **first, outside every raid-signature early return** — putting it after them
  is the same as not writing it, because that file does not move while the
  party is in the Hamlet. Five stat calls on `TOWN_FILES`.
- **The town is DIFFED, not read.** Two `TownState` snapshots plus the upgrade
  ledger, subtracted, is a complete account of a town visit with no screen
  reading and no actuator: wallet deltas, hires, departures, treatment,
  resolve, trinkets, upgrade trees, the quest map. Keyed on hero **guid**, never
  roster position — the roster reorders freely and a positional diff reports one
  hire as four changes. Every branch records the **before and the after**, not
  the delta: "gold −1500" cannot answer "could he have afforded the other
  thing", which is the question a spend is about.
- **RECORDING WHAT WAS PLAYED WAS FUSED TO GRADING IT.** `_resolve` returns
  immediately when `_pending` is None, and `_arm` leaves it None when the
  advisor has nothing to say — which is every turn while no checkpoint loads.
  So the combat corpus would have been **empty in exactly the state that makes
  it most valuable**. `_record_play` is the split: `play` events need no policy,
  `decision` events are the agreement half. Measured on the replay corpus:
  **58 demonstrations recorded model-less, up from 0.**

**A `play` carries the BOARD, because advise mode captures no saves.** HP,
stress, affliction and virtue per hero; enemy id, HP, slot and corpse flag;
torch. Whatever is not in that event is gone — `--replay` reads
`archive/traces/`, but a live watch writes nothing else.

**`hero_left` does not say why.** The town save cannot tell a death from a
dismissal, so the event records the departure and refuses to guess; pairing it
with the raid's own outcome is a reader's job.


### A checkpoint at the wrong width now REFUSES instead of raising per turn

`Advisor.__init__` compares the checkpoint's observation width against the
env's and drops the model with one clear line if they differ, setting
`model_refused`. SB3 does not notice until `predict` — which is the first combat
turn, mid-raid, once per turn, forever.

That is not a hypothetical: the width is a property of the **loaded data** as
well as of the code (the enemy-type one-hot), so a pure loader fix reached it on
2026-09-02 with no change in `env/` at all. Refusing costs the combat advice and
keeps everything else — route, torch, curio and camp decide without a policy,
and the journal still records every move played. That is the difference between
a usable recording session and an exception per turn.

`_Pending` is a `NamedTuple` now rather than a bare tuple, with `board`
defaulted: the shape is built in `_arm`, read in `_resolve`, and built a third
time by hand in `tests/bridge/test_run.py`, where adding a sixth element broke an
unpack a long way from the change.


## Splitting `act.py` further: attempted, reverted, and why (2026-09-08)

`bridge/act.py` was 6,888 lines. Two pieces came out cleanly and are still
out -- `actuation/keys.py` (the key codes, Win32 structs and timing tunables)
and `actuation/plan.py` (`Step`, `InputPlan`, `refuse`). The rest was
extracted too, in dependency order: the raid/town/movement planners, the four
backends, `choose_backend`, `ActRunner` and the hardware probes. `act.py`
reached **370 lines**, every import path still resolved, all eight
`bridge_oracle` cells were byte-identical, and every `bat/`-invoked entry
point still ran.

**Then 66 tests failed, and the split was reverted.**

**The reason is `monkeypatch`, and it is not fixable by re-exporting.**
`tests/bridge/test_act.py` patches about fifteen names on the `bridge.act`
module -- `FOCUS_WAIT`, `ACK_TIMEOUT`, `SEND_ATTEMPTS`, `MAX_WALK_STEPS`,
`plan_one_move`, `plan_battle`, `refresh_window`, `act.time`, `act.user32` --
across 26 call sites, and roughly a third of the file's tests reach those
through shared helpers.

That works only while the code that READS a name lives in the same module the
test patches. `from .keys import FOCUS_WAIT` copies the binding into the
importing module's namespace, so once `ActRunner` moved to
`actuation/runner.py` it read `runner.FOCUS_WAIT`, and patching either
`bridge.act` or `actuation.keys` changed nothing it could see.

So `bridge.act`'s module namespace is not merely an import surface. It is a
**patch surface**, and re-exporting preserves the first and cannot preserve
the second.

**Why the tests were not simply re-pointed.** The correct target is the module
whose code is under test -- mostly `runner` -- and it is about 26 mechanical
edits. It was not done because of how that fails when it is wrong: a
`monkeypatch.setattr` aimed at a module the code does not read **raises
nothing**. It silently patches an unused binding, and a test that was
asserting on a stubbed planner or a shortened timeout quietly starts asserting
on the real one. Some of those tests would still pass, for the wrong reason.
Trading 66 loud failures for an unknown number of silent ones is the wrong
direction in a repo whose characteristic bug is a test that stops testing.

**What would make it safe**, in the order it should be done:

1. Give `ActRunner` its tunables as instance state set in `__init__` -- reading
   `self.focus_wait` rather than a module global. Then a test configures the
   runner instead of patching a module, and the values can live anywhere.
2. Inject the planners the same way, as `ActRunner` collaborators, which is
   what the three `plan_*` patches are simulating by hand.
3. Only then move the code. At that point the split is mechanical and the
   scripts that did it are in this session's scratchpad.

That is a behavioural refactor of `ActRunner`'s construction, not a file move,
and it needs its own session and its own before/after recording. The split
scripts refuse on cycles and re-parse what they write, so re-running them is
cheap once the state question is settled.
